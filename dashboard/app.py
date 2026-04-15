"""
dashboard/app.py — Flask web dashboard
"""
from __future__ import annotations

import base64
import os
import threading
import yaml
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (Flask, render_template, redirect, url_for,
                   jsonify, request, flash, send_file, Response)

from monitor.storage import DB, get_recent_changes, get_site_stats, get_baseline_png_path
from monitor.capture import take_screenshot_sync

app = Flask(__name__, template_folder="templates")
app.secret_key = os.urandom(24)

_ROOT      = Path(__file__).parent.parent
CONFIG     = _ROOT / "config" / "sites.yaml"
LOG_PATH   = _ROOT / "data" / "activity.log"
DISPLAY_TZ = ZoneInfo(os.getenv("DISPLAY_TZ", "Europe/London"))

_scheduler = None
def set_scheduler(s): 
    global _scheduler
    _scheduler = s


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_ts(ts: str) -> str:
    if not ts: return "Never"
    try:
        return datetime.fromisoformat(ts).astimezone(DISPLAY_TZ).strftime("%d %b %Y %H:%M")
    except Exception:
        return ts[:16]


def load_sites() -> list:
    try:
        with open(CONFIG) as f:
            return yaml.safe_load(f).get("sites", []) or []
    except Exception:
        return []


def save_sites(sites: list):
    with open(CONFIG, "w") as f:
        yaml.dump({"sites": sites}, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)


def find_site(sites: list, name: str) -> int:
    return next((i for i, s in enumerate(sites) if s.get("name") == name), -1)


def schedule_label(site: dict) -> str:
    if site.get("schedule_type") == "time_window":
        windows = site.get("windows", [])
        if windows:
            w = windows[0]
            return f"{w['days']} {w['from']}-{w['to']} /{w.get('interval_minutes',1)}min"
        return "time window"
    m = site.get("interval_minutes", 60)
    if m >= 1440: return f"Every {m//1440}d"
    if m >= 60:   return f"Every {m//60}h"
    return f"Every {m}min"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sites   = load_sites()
    changes = get_recent_changes(100)
    rows = []
    for site in sites:
        stats  = get_site_stats(site["name"])
        clip   = site.get("clip")
        status = {"success": "OK", "error": "Error"}.get(stats["last_status"], "Pending")
        rows.append({
            "name":          site["name"],
            "url":           site["url"],
            "mode":          site.get("mode", "screenshot"),
            "schedule":      schedule_label(site),
            "clip":          f"{clip['width']}×{clip['height']} at ({clip['x']},{clip['y']})" if clip else "Full page",
            "last_check":    fmt_ts(stats["last_check"]),
            "total_changes": stats["total_changes"],
            "status":        status,
            "last_detail":   stats["last_detail"] or "",
            "has_baseline":  get_baseline_png_path(site["name"]) is not None,
        })

    parsed_changes = []
    for c in changes:
        parsed_changes.append({
            "site_name": c["site_name"],
            "timestamp": fmt_ts(c["timestamp"]),
            "summary":   c.get("summary", ""),
            "added":     [l for l in (c.get("added") or "").splitlines() if l.strip()][:5],
            "removed":   [l for l in (c.get("removed") or "").splitlines() if l.strip()][:3],
        })

    return render_template("index.html",
        sites=rows,
        changes=parsed_changes,
        total_sites=len(sites),
        total_changes=len(changes),
    )


@app.route("/baseline/<path:site_name>")
def baseline_image(site_name):
    path = get_baseline_png_path(site_name)
    if not path:
        return Response("No baseline yet", status=404)
    return send_file(path, mimetype="image/png")


@app.route("/logs")
def view_logs():
    lines = []
    if LOG_PATH.exists():
        raw = LOG_PATH.read_text().splitlines()[-2000:]
        for line in reversed(raw):
            line = line.strip()
            if not line: continue
            level = "INFO"
            if   "ERROR"   in line:                   level = "ERROR"
            elif "WARNING" in line:                    level = "WARNING"
            elif "change detected" in line.lower():    level = "CHANGE"
            elif "baseline saved"  in line.lower():    level = "BASELINE"
            elif "Checking"        in line:            level = "CHECKING"
            try:
                ts = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S") \
                       .replace(tzinfo=ZoneInfo("UTC")) \
                       .astimezone(DISPLAY_TZ) \
                       .strftime("%d %b %H:%M:%S")
                msg = line[20:].strip()
            except Exception:
                ts, msg = "", line
            lines.append({"ts": ts, "level": level, "message": msg, "count": 1})

    # Collapse consecutive identical messages
    collapsed = []
    for entry in lines:
        if collapsed and collapsed[-1]["message"] == entry["message"]:
            collapsed[-1]["count"] += 1
        else:
            collapsed.append(entry)

    return render_template("logs.html", lines=collapsed)


@app.route("/check-now", methods=["POST"])
def check_now_all():
    if _scheduler:
        from monitor.scheduler import trigger_immediate
        for site in load_sites():
            try: trigger_immediate(_scheduler, site)
            except Exception: pass
    return jsonify({"status": "dispatched"}), 202


@app.route("/check-now/<path:site_name>", methods=["POST"])
def check_now_one(site_name):
    if _scheduler:
        from monitor.scheduler import trigger_immediate
        for site in load_sites():
            if site["name"] == site_name:
                trigger_immediate(_scheduler, site)
                break
    return jsonify({"status": "dispatched"}), 202


@app.route("/reset-baseline/<path:site_name>", methods=["POST"])
def reset_baseline(site_name):
    """Delete stored baseline so next check saves a fresh one."""
    from monitor.storage import _site_dir
    d = _site_dir(site_name)
    for fname in ["baseline.png", "baseline_text.txt"]:
        p = d / fname
        if p.exists(): p.unlink()
    flash(f"Baseline cleared for \"{site_name}\". Next check will save a new baseline.")
    return redirect(url_for("index"))


@app.route("/api/screenshot")
def api_screenshot():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL"}), 400
    result = {}
    def _do():
        result["png"] = take_screenshot_sync(url, clip=None, js_wait=3.0)
    t = threading.Thread(target=_do)
    t.start()
    t.join(timeout=45)
    if not result.get("png"):
        return jsonify({"error": "Screenshot failed — check URL and try again"}), 500
    return jsonify({"image": base64.b64encode(result["png"]).decode()})


@app.route("/site/add")
def site_add():
    return render_template("site_form.html", page_title="Add Site", site=None, error=None)


@app.route("/site/edit/<path:site_name>")
def site_edit(site_name):
    sites = load_sites()
    idx   = find_site(sites, site_name)
    if idx == -1:
        flash("Site not found")
        return redirect(url_for("index"))
    return render_template("site_form.html", page_title=f"Edit — {site_name}",
                           site=sites[idx], error=None)


@app.route("/site/save", methods=["POST"])
def site_save():
    f     = request.form
    sites = load_sites()
    name  = f.get("name","").strip()
    url   = f.get("url","").strip()
    if not name or not url:
        return render_template("site_form.html", page_title="Add/Edit Site",
                               site=None, error="Name and URL are required")
    mode = f.get("mode", "screenshot")
    site: dict = {"name": name, "url": url, "mode": mode}

    if mode == "screenshot":
        try:
            cx, cy, cw, ch = int(f.get("clip_x",0)), int(f.get("clip_y",0)), \
                              int(f.get("clip_w",0)), int(f.get("clip_h",0))
            if cw > 0 and ch > 0:
                site["clip"] = {"x": cx, "y": cy, "width": cw, "height": ch}
        except (ValueError, TypeError):
            pass
        site["ocr_languages"] = [l.strip() for l in f.get("ocr_languages","en").split(",") if l.strip()]
        site["js_wait"] = float(f.get("js_wait", 3.0))

    if mode == "json_api":
        raw = f.get("json_fields_raw","").strip()
        if raw:
            site["json_fields"] = [x.strip() for x in raw.split(",") if x.strip()]

    sched = f.get("schedule_type","interval")
    site["schedule_type"] = sched
    if sched == "interval":
        site["interval_minutes"] = int(f.get("interval_minutes", 60) or 60)
    else:
        try:
            parsed = yaml.safe_load(f.get("windows_yaml",""))
            if isinstance(parsed, list):
                site["windows"] = parsed
        except Exception as e:
            return render_template("site_form.html", page_title="Add/Edit Site",
                                   site=None, error=f"Windows YAML error: {e}")
        site["timezone"] = f.get("timezone","UTC").strip()

    idx = find_site(sites, name)
    if idx >= 0: sites[idx] = site
    else: sites.append(site)
    save_sites(sites)
    flash(f"\"{name}\" saved.")
    return redirect(url_for("index"))


@app.route("/site/delete/<path:site_name>", methods=["POST"])
def site_delete(site_name):
    sites = load_sites()
    idx   = find_site(sites, site_name)
    if idx >= 0:
        sites.pop(idx)
        save_sites(sites)
        flash(f"\"{site_name}\" deleted.")
    return redirect(url_for("index"))


def start_dashboard(host="0.0.0.0", port=5000, debug=False):
    app.run(host=host, port=port, debug=debug, use_reloader=False)
