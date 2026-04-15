"""
core.py — site check logic.

Two modes:
  screenshot  — Selenium screenshot → OCR → diff → notify
  json_api    — HTTP GET JSON → field extraction → diff → notify
"""
from __future__ import annotations

import json
import logging
import requests

from monitor.capture  import take_screenshot
from monitor.ocr      import extract_text
from monitor.diff     import compute_diff
from monitor.storage  import load_baseline, save_baseline, log_job, log_change
from monitor.notify   import send_change_alert, send_error_alert

logger = logging.getLogger("monitor")


def check_json_api(site: dict):
    name   = site["name"]
    url    = site["url"]
    fields = site.get("json_fields", [])

    logger.info(f"Checking JSON API: {name}")
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"{name}: fetch failed — {e}")
        log_job(name, "error", str(e))
        return

    def _flatten(obj, prefix=""):
        out = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.update(_flatten(v, f"{prefix}{k}."))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                out.update(_flatten(v, f"{prefix}{i}."))
        else:
            out[prefix.rstrip(".")] = obj
        return out

    flat = _flatten(data)
    if fields:
        flat = {k: v for k, v in flat.items()
                if any(k == f or k.endswith("." + f) for f in fields)}

    current_text = json.dumps(flat, sort_keys=True, ensure_ascii=False)
    _, baseline_text = load_baseline(name)

    if not baseline_text:
        save_baseline(name, b"", current_text)
        logger.info(f"{name}: baseline saved")
        log_job(name, "success", "baseline saved")
        return

    if current_text == baseline_text:
        logger.info(f"{name}: no change")
        log_job(name, "success", "no change")
        return

    old  = json.loads(baseline_text)
    new  = json.loads(current_text)
    diff = {
        "changed": True,
        "added":   [f"{k}: {new[k]}" for k in new if old.get(k) != new[k]],
        "removed": [f"{k}: {old[k]}" for k in old if k not in new],
        "summary": "",
    }
    diff["summary"] = f"{len(diff['added'])} fields changed"
    logger.info(f"{name}: change — {diff['summary']}")
    send_change_alert(name, diff, png_bytes=None)
    save_baseline(name, b"", current_text)
    log_change(name, diff)
    log_job(name, "success", diff["summary"])


def check_screenshot_site(site: dict):
    name      = site["name"]
    url       = site["url"]
    clip      = site.get("clip")
    languages = site.get("ocr_languages", ["en"])
    js_wait   = float(site.get("js_wait", 3.0))
    min_conf  = float(site.get("ocr_min_confidence", 0.4))

    logger.info(f"Checking: {name}")

    png = take_screenshot(url, clip, js_wait)
    if not png:
        msg = "Screenshot failed"
        logger.error(f"{name}: {msg}")
        send_error_alert(name, msg)
        log_job(name, "error", msg)
        return

    current_text = extract_text(png, languages, min_conf)
    _, baseline_text = load_baseline(name)

    if not baseline_text:
        save_baseline(name, png, current_text)
        logger.info(f"{name}: baseline saved")
        log_job(name, "success", "baseline saved")
        return

    diff = compute_diff(baseline_text, current_text)
    if not diff["changed"]:
        logger.info(f"{name}: no change")
        log_job(name, "success", "no change")
        return

    logger.info(f"{name}: change detected — {diff['summary']}")
    send_change_alert(name, diff, png_bytes=png)
    save_baseline(name, png, current_text)
    log_change(name, diff)
    log_job(name, "success", diff["summary"])


def check_site(site: dict):
    """Main entry point called by scheduler and cmd_check."""
    try:
        if site.get("mode") == "json_api":
            check_json_api(site)
        else:
            check_screenshot_site(site)
    except Exception as e:
        logger.error(f"Unhandled error checking [{site.get('name')}]: {e}", exc_info=True)
