"""
core.py — main check loop.

For each site:
  1. Take a clipped screenshot (Playwright)
  2. OCR the screenshot (EasyOCR)
  3. Diff against stored baseline text
  4. If changed → notify + save new screenshot as baseline
  5. If first run → save as baseline, no notification

mode: json_api — bypass screenshot/OCR, compare JSON fields directly (e.g. LSE RNS)
"""
from __future__ import annotations

import json
import logging
import aiohttp

from monitor.capture  import take_screenshot
from monitor.ocr      import extract_text
from monitor.diff     import compute_diff
from monitor.storage  import load_baseline, save_baseline, log_job, log_change
from monitor.notify   import send_change_alert, send_error_alert

logger = logging.getLogger("monitor")


# ── JSON API check ────────────────────────────────────────────────────────────

async def check_json_api(site: dict):
    name   = site["name"]
    url    = site["url"]
    fields = site.get("json_fields", [])

    logger.info(f"Checking JSON API: {name}")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.error(f"{name}: JSON fetch failed — {e}")
        log_job(name, "error", str(e))
        return

    def _flatten(obj, prefix=""):
        items = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                items.update(_flatten(v, f"{prefix}{k}."))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                items.update(_flatten(v, f"{prefix}{i}."))
        else:
            items[prefix.rstrip(".")] = obj
        return items

    flat = _flatten(data)
    if fields:
        flat = {k: v for k, v in flat.items()
                if any(k == f or k.endswith("." + f) for f in fields)}

    current_text = json.dumps(flat, sort_keys=True, ensure_ascii=False)
    _, baseline_text = load_baseline(name)

    if not baseline_text:
        save_baseline(name, b"", current_text)
        logger.info(f"{name}: JSON baseline saved")
        log_job(name, "success", "baseline saved")
        return

    if current_text == baseline_text:
        logger.info(f"{name}: no change")
        log_job(name, "success", "no change")
        return

    old = json.loads(baseline_text)
    new = json.loads(current_text)
    added   = [f"{k}: {new[k]}" for k in new if old.get(k) != new[k]]
    removed = [f"{k}: {old[k]}" for k in old if k not in new]
    diff = {
        "changed": True,
        "added":   added,
        "removed": removed,
        "summary": f"{len(added)} fields changed",
    }

    logger.info(f"{name}: JSON change — {diff['summary']}")
    await send_change_alert(name, diff, png_bytes=None)
    save_baseline(name, b"", current_text)
    log_change(name, diff)
    log_job(name, "success", diff["summary"])


# ── Screenshot + OCR check ────────────────────────────────────────────────────

async def check_screenshot_site(site: dict):
    name      = site["name"]
    url       = site["url"]
    clip      = site.get("clip")
    languages = site.get("ocr_languages", ["en"])
    js_wait   = float(site.get("js_wait", 3.0))
    min_conf  = float(site.get("ocr_min_confidence", 0.4))

    logger.info(f"Checking: {name}  url={url}  clip={clip}")

    png = await take_screenshot(url, clip, js_wait)
    if not png:
        msg = "Screenshot failed (browser error or timeout)"
        logger.error(f"{name}: {msg}")
        await send_error_alert(name, msg)
        log_job(name, "error", msg)
        return

    current_text = extract_text(png, languages, min_confidence=min_conf)
    if not current_text.strip():
        logger.warning(f"{name}: OCR returned empty text — skipping")
        log_job(name, "error", "OCR returned empty text")
        return

    _, baseline_text = load_baseline(name)

    if not baseline_text:
        save_baseline(name, png, current_text)
        logger.info(f"{name}: baseline saved ({len(current_text.splitlines())} OCR lines)")
        log_job(name, "success", "baseline saved")
        return

    diff = compute_diff(baseline_text, current_text)

    if not diff["changed"]:
        logger.info(f"{name}: no change")
        log_job(name, "success", "no change")
        return

    logger.info(f"{name}: change detected — {diff['summary']}")
    await send_change_alert(name, diff, png_bytes=png)
    save_baseline(name, png, current_text)
    log_change(name, diff)
    log_job(name, "success", diff["summary"])


# ── Dispatcher ────────────────────────────────────────────────────────────────

async def check_site(site: dict):
    if site.get("mode") == "json_api":
        await check_json_api(site)
    else:
        await check_screenshot_site(site)
