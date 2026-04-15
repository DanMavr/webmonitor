"""
dashboard/app.py — Flask dashboard

Routes:
  GET  /                       — main dashboard
  GET  /logs                   — activity log viewer
  GET  /site/add               — add new site form
  GET  /site/edit/<name>       — edit existing site
  POST /site/save              — save new or edited site to sites.yaml
  POST /site/delete/<name>     — delete site
  POST /check-now              — trigger immediate check for all sites
  POST /check-now/<name>       — trigger immediate check for one site
  GET  /api/screenshot         — fetch screenshot for clip region selector
  GET  /baseline/<name>        — serve baseline.png for a site (for inspect view)
"""

import base64
import re
import yaml
import sqlite3
import asyncio
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (
    Flask, render_template, redirect, url_for,
    jsonify, request, flash, send_file, Response
)

from monitor.storage  import DB, get_recent_changes, get_site_stats, get_baseline_png_path
from monitor.capture  import take_screenshot_sync

app = Flask(__name__, template_folder="templates")
app.secret_key = "webmonitor-secret-key-change-me"

CONFIG      = "config/sites.yaml"
DISPLAY_TZ  = ZoneInfo("Europe/London")

# Shared scheduler reference (set by run.py after scheduler is built)
_scheduler = None

def set_scheduler(s):
    global _scheduler
    _scheduler = s


# ── Helpers ───────────────────────────────────────────────────────────────────

def fmt_ts(ts: str) -> str:
    if not ts:
        return "Never"
    try:
        dt = datetime.fromisoformat(ts).astimezone(DISPLAY_TZ)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts[:16]


def load_sites() -> list[dict]:
    try:
        with open(CONFIG) as f:
            return yaml.safe_load(f).get("sites", [])
    except Exception:
        return []


def save_sites(sites: list[dict]):
    with open(CONFIG, "w") as f:
        yaml.dump({"sites": sites}, f, default_flow_style=False,
                  allow_unicode=True, sort_keys=False)


def find_site(sites: list, name: str) -> int:
    for i, s in enumerate(sites):
        if s.get("name") == name:
            return i
    return -1


# ── Main dashboard ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    sites    = load_sites()
    changes  = get_recent_changes(100)

    monitored = []
    for site in sites:
        stats = get_site_stats(site["name"])
        status = "Pending"
        if stats["last_status"] == "success":
            status = "OK"
        elif stats["last_status"] == "error":
            status = "Error"

        sched = site.get("schedule_type", "interval")
        if sched == "interval":
            m = site.get("interval_minutes", 60)
            if m >= 1440:   label = f"Every {m//1440}d"
            elif m >= 60:   label = f"Every {m//60}h"
            else:           label = f"Every {m}min"
        else:
            windows = site.get("windows", [])
            label   = " | ".join(
                f"{w['days']} {w['from']}-{w['to']} /{w['interval_minutes']}min"
                for w in windows
            ) if windows else "time window"

        clip = site.get("clip")
        clip_label = (
            f"{clip['width']}×{clip['height']} at ({clip['x']},{clip['y']})"
            if clip else "Full page"
        )

        monitored.append({
            "name":          site["name"],
            "url":           site.get("url",""),
            "mode":          site.get("mode","screenshot"),
            "schedule":      label,
            "clip":          clip_label,
            "last_check":    fmt_ts(stats["last_check"]),
            "total_changes": stats["total_changes"],
            "status":        status,
            "has_baseline":  get_baseline_png_path(site["name"]) is not None,
        })

    # Parse changes for display
    parsed_changes = []
    for c in changes:
        added   = [l for l in (c.get("added")   or "").splitlines() if l.strip()]
        removed = [l for l in (c.get("removed") or "").splitlines() if l.strip()]
        parsed_changes.append({
            "site_name": c["site_name"],
            "timestamp": fmt_ts(c["timestamp"]),
            "summary":   c.get("summary",""),
            "added":     added[:5],
            "removed":   removed[:3],
        })

    return render_template(
        "index.html",
        monitored_sites=monitored,
        changes=parsed_changes,
        total_sites=len(sites),
        total_changes=len(changes),
    )


# ── Baseline image viewer ─────────────────────────────────────────────────────

@app.route("/baseline/<path:site_name>")
def baseline_image(site_name):
    path = get_baseline_png_path(site_name)
    if not path:
        return Response("No baseline", status=404)
    return send_file(path, mimetype="image/png")


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.route("/logs")
def view_logs():
    log_path = Path("data/activity.log")
    lines = []
    if log_path.exists():
        raw = log_path.read_text().splitlines()[-1000:]
        for line in reversed(raw):
            line = line.strip()
            if not line:
                continue
            level = "INFO"
            if "ERROR"    in line: level = "ERROR"
            elif "WARNING" in line: level = "WARNING"
            elif "change detected" in line.lower(): level = "CHANGE"
            elif "baseline saved"  in line.lower(): level = "BASELINE"
            elif "Checking:"       in line:         level = "CHECKING"
            try:
                ts_display = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S") \
                               .replace(tzinfo=ZoneInfo("UTC")) \
                               .astimezone(DISPLAY_TZ) \
                               .strftime("%Y-%m-%d %H:%M:%S")
                message = line[20:].strip()
            except Exception:
                ts_display, message = "", line
            lines.append({"ts": ts_display, "level": level, "message": message})

    # Collapse consecutive identical messages
    parsed = []
    for entry in lines:
        if parsed and parsed[-1]["message"] == entry["message"] and parsed[-1]["level"] == entry["level"]:
            parsed[-1]["count"] = parsed[-1].get("count", 1) + 1
        else:
            entry["count"] = 1
            parsed.append(entry)

    return render_template("logs.html", lines=parsed)


# ── Immediate check ───────────────────────────────────────────────────────────

@app.route("/check-now", methods=["POST"])
def check_now_all():
    if _scheduler:
        from monitor.scheduler import trigger_immediate
        for site in load_sites():
            try:
                trigger_immediate(_scheduler, site)
            except Exception:
                pass
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


# ── Screenshot API (for clip region selector) ─────────────────────────────────

@app.route("/api/screenshot")
def api_screenshot():
    """
    Takes a full-page screenshot of the given URL and returns it as base64 PNG.
    Called by the Add/Edit site form to display the page for clip region selection.
    """
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Run Playwright in a thread (Flask is synchronous)
    result = {}

    def _capture():
        png = take_screenshot_sync(url, clip=None, js_wait=3.0)
        result["png"] = png

    t = threading.Thread(target=_capture)
    t.start()
    t.join(timeout=40)

    if not result.get("png"):
        return jsonify({"error": "Screenshot failed — check the URL and try again"}), 500

    b64 = base64.b64encode(result["png"]).decode()
    return jsonify({"image": b64})


# ── Site form (add / edit) ─────────────────────────────────────────────────────

@app.route("/site/add")
def site_add():
    return render_template("site_form.html", page_title="Add Site",
                           site=None, error=None)


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
    form = request.form
    sites = load_sites()

    name = form.get("name","").strip()
    url  = form.get("url","").strip()
    if not name or not url:
        return render_template("site_form.html", page_title="Add/Edit Site",
                               site=None, error="Name and URL are required")

    mode = form.get("mode", "screenshot")

    site: dict = {
        "name": name,
        "url":  url,
        "mode": mode,
        "notify": ["telegram"],
    }

    # --- Clip region (screenshot mode only) ---
    if mode == "screenshot":
        try:
            cx = int(form.get("clip_x", 0))
            cy = int(form.get("clip_y", 0))
            cw = int(form.get("clip_w", 0))
            ch = int(form.get("clip_h", 0))
            if cw > 0 and ch > 0:
                site["clip"] = {"x": cx, "y": cy, "width": cw, "height": ch}
        except (ValueError, TypeError):
            pass

        langs_raw = form.get("ocr_languages", "en").strip()
        site["ocr_languages"] = [l.strip() for l in langs_raw.split(",") if l.strip()]
        site["js_wait"] = float(form.get("js_wait", 3.0))

    # --- JSON API fields ---
    if mode == "json_api":
        fields_raw = form.get("json_fields_raw", "").strip()
        if fields_raw:
            site["json_fields"] = [f.strip() for f in fields_raw.split(",") if f.strip()]

    # --- Schedule ---
    sched_type = form.get("schedule_type", "interval")
    site["schedule_type"] = sched_type

    if sched_type == "interval":
        try:
            site["interval_minutes"] = int(form.get("interval_minutes", 60))
        except ValueError:
            site["interval_minutes"] = 60
    else:
        windows_yaml = form.get("windows_yaml","").strip()
        if windows_yaml:
            try:
                parsed = yaml.safe_load(windows_yaml)
                if isinstance(parsed, list):
                    site["windows"] = parsed
            except Exception as e:
                return render_template("site_form.html", page_title="Add/Edit Site",
                                       site=None, error=f"Windows YAML error: {e}")
        site["timezone"] = form.get("timezone","UTC").strip()

    # --- SSL ---
    if form.get("ssl_verify") == "false":
        site["ssl_verify"] = False

    # Save / replace
    idx = find_site(sites, name)
    if idx >= 0:
        sites[idx] = site
    else:
        sites.append(site)

    save_sites(sites)
    flash(f"Site \"{name}\" saved.")
    return redirect(url_for("index"))


@app.route("/site/delete/<path:site_name>", methods=["POST"])
def site_delete(site_name):
    sites = load_sites()
    idx   = find_site(sites, site_name)
    if idx >= 0:
        sites.pop(idx)
        save_sites(sites)
        flash(f"Site \"{site_name}\" deleted.")
    return redirect(url_for("index"))


# ── App factory ───────────────────────────────────────────────────────────────

def create_app():
    return app


def start_dashboard(host="0.0.0.0", port=5000, debug=False):
    app.run(host=host, port=port, debug=debug, use_reloader=False)
