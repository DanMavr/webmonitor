from flask import Flask, render_template, send_file, redirect, url_for, jsonify, request, flash
import sqlite3
import yaml
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__, template_folder="../dashboard/templates")
app.secret_key = "webmonitor-secret-key-change-me"
DB = "data/monitor.db"
CONFIG = "config/sites.yaml"
DISPLAY_TZ = ZoneInfo("Europe/Prague")


def format_timestamp(ts: str) -> str:
    """Convert a UTC timestamp string to Prague local time for display."""
    if not ts:
        return "Not yet"
    try:
        dt = datetime.fromisoformat(ts).replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts[:16]


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def load_config():
    try:
        with open(CONFIG) as f:
            return yaml.safe_load(f)["sites"]
    except Exception:
        return []


def success_rate(db) -> int:
    total = db.execute(
        "SELECT COUNT(*) FROM job_log"
    ).fetchone()[0]
    if total == 0:
        return 100
    success = db.execute(
        "SELECT COUNT(*) FROM job_log WHERE status='success'"
    ).fetchone()[0]
    return round((success / total) * 100)


def get_schedule_label(site: dict) -> str:
    schedule_type = site.get("schedule_type", "interval")
    if schedule_type == "interval":
        mins = site.get("interval_minutes", 60)
        if mins >= 1440:
            return f"Every {mins // 1440}d"
        elif mins >= 60:
            return f"Every {mins // 60}h"
        else:
            return f"Every {mins}min"
    elif schedule_type == "time_window":
        windows = site.get("windows", [])
        parts = []
        for w in windows:
            parts.append(
                f"{w.get('days','?')} "
                f"{w.get('from','?')}-{w.get('to','?')} "
                f"/{w.get('interval_minutes','?')}min"
            )
        return " | ".join(parts)
    return "Unknown"


@app.route("/")
def index():
    db = get_db()
    config_sites = load_config()

    monitored_sites = []
    for site in config_sites:
        name = site["name"]

        last_check = db.execute("""
            SELECT timestamp FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        total_changes = db.execute(
            "SELECT COUNT(*) FROM changes WHERE site_name = ?",
            (name,)
        ).fetchone()[0]

        last_status = db.execute("""
            SELECT status FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        if last_status and last_status[0] == "success":
            status = "OK"
        elif last_status and last_status[0] == "error":
            status = "Error"
        else:
            status = "Pending"

        monitored_sites.append({
            "name": name,
            "url": site.get("url", ""),
            "mode": site.get("mode", "single_page"),
            "schedule": get_schedule_label(site),
            "last_check": format_timestamp(last_check[0]) if last_check else "Not yet",
            "total_changes": total_changes,
            "status": status,
        })

    raw_changes = db.execute("""
        SELECT site_name, url, change_pct, diff_text, timestamp
        FROM changes
        ORDER BY timestamp DESC LIMIT 100
    """).fetchall()

    changes = []
    for c in raw_changes:
        from urllib.parse import urlparse
        path = urlparse(c["url"]).path or "/"
        added = []
        removed = []
        if c["diff_text"]:
            for line in c["diff_text"].splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    added.append(line[1:].strip())
                elif line.startswith("-") and not line.startswith("---"):
                    removed.append(line[1:].strip())
        changes.append({
            "site_name": c["site_name"],
            "url": c["url"],
            "short_url": path,
            "change_pct": c["change_pct"],
            "timestamp": format_timestamp(c["timestamp"]),
            "added": added,
            "removed": removed,
        })

    total_pages = db.execute(
        "SELECT COUNT(DISTINCT url) FROM snapshots"
    ).fetchone()[0]

    stats = {
        "total_sites": len(config_sites),
        "total_pages": total_pages,
        "total_changes": db.execute(
            "SELECT COUNT(*) FROM changes"
        ).fetchone()[0],
        "checks_today": db.execute(
            "SELECT COUNT(*) FROM job_log "
            "WHERE DATE(timestamp)=DATE('now')"
        ).fetchone()[0],
        "success_rate": success_rate(db),
    }

    db.close()
    return render_template(
        "index.html",
        monitored_sites=monitored_sites,
        changes=changes,
        stats=stats,
    )


@app.route("/check-now", methods=["POST"])
def check_now():
    """
    Dispatch an immediate check for every site via the running APScheduler.
    Returns immediately (202) — checks run in the background on the scheduler's
    event loop, same as normal scheduled runs. Job_log and snapshots update as
    each site finishes. The dashboard auto-reloads to show fresh timestamps.
    """
    config_sites = load_config()
    dispatched = []
    failed = []

    for site in config_sites:
        try:
            _trigger_immediate_check(site)
            dispatched.append(site["name"])
        except Exception as e:
            failed.append({"name": site["name"], "error": str(e)})

    return jsonify({
        "status": "dispatched",
        "dispatched": dispatched,
        "failed": failed,
    }), 202


@app.route("/inspect/<path:site_name>")
def inspect_site(site_name):
    from monitor.storage import get_baseline_summary

    summary = get_baseline_summary(site_name)

    if "error" in summary:
        return render_template_string(
            "<h2 style='color:white;font-family:sans-serif;"
            "background:#0f172a;padding:20px'>"
            + summary["error"] + "</h2>"
        )

    

    return render_template("inspect.html", summary=summary)

@app.route("/api/json-fields")
def api_json_fields():
    """
    Probe a JSON API URL and return its fields with current values.
    Used by the Add/Edit form to auto-detect monitorable fields.
    Flattens nested JSON using dot notation.
    """
    import requests as _requests

    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"})

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return jsonify({"error": f"URL returned HTTP {resp.status_code}"})

        data = resp.json()
    except ValueError:
        return jsonify({"error": "URL did not return valid JSON"})
    except Exception as e:
        return jsonify({"error": f"Request failed: {e}"})

    # Flatten nested structures
    def flatten(obj, prefix=""):
        items = {}
        if isinstance(obj, dict):
            for k, v in obj.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    items.update(flatten(v, full_key))
                else:
                    items[full_key] = v
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:5]):   # cap list expansion at 5 items
                full_key = f"{prefix}[{i}]"
                if isinstance(v, (dict, list)):
                    items.update(flatten(v, full_key))
                else:
                    items[full_key] = v
        return items

    flat = flatten(data)
    fields = [{"key": k, "value": str(v)} for k, v in sorted(flat.items())]

    return jsonify({"fields": fields})


@app.route("/logs")
def view_logs():
    import os
    from zoneinfo import ZoneInfo
    from datetime import datetime

    log_path = "data/activity.log"
    lines = []

    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            all_lines = f.readlines()
        lines = all_lines[-1000:]

    parsed_raw = []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue

        level = "INFO"
        if "[ERROR]" in raw:
            level = "ERROR"
        elif "[WARNING]" in raw:
            level = "WARNING"
        elif "WATCHDOG" in raw:
            level = "WATCHDOG"
        elif "Change detected" in raw:
            level = "CHANGE"
        elif "Checking:" in raw:
            level = "CHECKING"
        elif "Baseline" in raw:
            level = "BASELINE"
        elif "JS fallback" in raw:
            level = "JS"

        try:
            ts_str = raw[:19]
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            dt_utc = dt.replace(tzinfo=ZoneInfo("UTC"))
            dt_local = dt_utc.astimezone(ZoneInfo("Europe/Prague"))
            ts_display = dt_local.strftime("%Y-%m-%d %H:%M:%S")
            message = raw[20:].strip()
        except Exception:
            ts_display = ""
            message = raw

        parsed_raw.append({
            "ts": ts_display,
            "level": level,
            "message": message,
        })

    # Collapse consecutive identical messages into one entry with a repeat count.
    # Eliminates walls of "Outside window: LSE - RNS" and similar repeated lines.
    parsed = []
    for entry in parsed_raw:
        if (parsed
                and parsed[-1]["message"] == entry["message"]
                and parsed[-1]["level"] == entry["level"]):
            parsed[-1]["count"] = parsed[-1].get("count", 1) + 1
            parsed[-1]["ts_first"] = entry["ts"]   # earliest occurrence
        else:
            entry["count"] = 1
            parsed.append(entry)

    

    return render_template("logs.html", lines=parsed)


# ── YAML helpers ──────────────────────────────────────────────────────────────

def load_yaml_raw() -> dict:
    """Load the full sites.yaml as a dict, preserving structure."""
    with open(CONFIG) as f:
        return yaml.safe_load(f) or {"sites": []}


def save_yaml(data: dict):
    """Write the sites dict back to sites.yaml with clean formatting."""
    with open(CONFIG, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def find_site_index(sites: list, name: str) -> int:
    """Return the index of a site by name, or -1 if not found."""
    for i, s in enumerate(sites):
        if s.get("name") == name:
            return i
    return -1


def form_to_site(form) -> tuple[dict | None, str]:
    """
    Convert a Flask form submission into a site config dict.
    Returns (site_dict, error_message).  error_message is "" on success.
    """
    name = form.get("name", "").strip()
    url  = form.get("url", "").strip()
    if not name:
        return None, "Site name is required."
    if not url:
        return None, "URL is required."

    mode          = form.get("mode", "single_page")
    schedule_type = form.get("schedule_type", "interval")

    site = {
        "name": name,
        "url":  url,
        "mode": mode,
        "schedule_type": schedule_type,
        "notify": ["telegram"],
    }

    # Target selector
    target_sel = form.get("target_selector", "").strip()
    if target_sel:
        site["target_selector"] = target_sel

    # JSON fields
    json_fields_raw = form.get("json_fields_raw", "").strip()
    if json_fields_raw:
        fields = [f.strip() for f in json_fields_raw.split(",") if f.strip()]
        if fields:
            site["json_fields"] = fields

    # SSL
    if form.get("ssl_verify") == "false":
        site["ssl_verify"] = False

    # JavaScript
    if form.get("javascript") == "true":
        site["javascript"] = True
        js_wait = form.get("js_wait_seconds", "").strip()
        if js_wait:
            try:
                site["js_wait_seconds"] = int(js_wait)
            except ValueError:
                pass

    # Timezone (only relevant for time_window but harmless otherwise)
    tz = form.get("timezone", "").strip()
    if tz:
        site["timezone"] = tz

    # Schedule
    if schedule_type == "interval":
        try:
            site["interval_minutes"] = int(form.get("interval_minutes", 60))
        except ValueError:
            site["interval_minutes"] = 60
    else:  # time_window — raw YAML block
        windows_yaml = form.get("windows_yaml", "").strip()
        if windows_yaml:
            try:
                parsed = yaml.safe_load(windows_yaml)
                if isinstance(parsed, list):
                    site["windows"] = parsed
                else:
                    return None, "Windows YAML must be a list (starts with -). See the example."
            except yaml.YAMLError as e:
                return None, f"Windows YAML parse error: {e}"
        else:
            return None, "Time-window schedule requires at least one window. See the example."

    # Sensitivity
    try:
        min_words = int(form.get("min_content_words", 50))
        site["min_content_words"] = min_words
    except ValueError:
        pass

    try:
        min_pct = float(form.get("min_change_percent", 3))
        site["min_change_percent"] = min_pct
    except ValueError:
        pass

    # Ignore selectors
    selectors_raw = form.get("ignore_selectors", "").strip()
    if selectors_raw:
        site["ignore_selectors"] = [s.strip() for s in selectors_raw.splitlines() if s.strip()]

    # Crawl settings (whole_site only)
    if mode == "whole_site":
        crawl = {}
        crawl["use_sitemap"]    = form.get("use_sitemap", "true") == "true"
        crawl["stay_on_domain"] = form.get("stay_on_domain", "true") == "true"
        try:
            crawl["max_pages"] = int(form.get("max_pages", 50))
        except ValueError:
            crawl["max_pages"] = 50
        excl_raw = form.get("exclude_patterns", "").strip()
        if excl_raw:
            crawl["exclude_patterns"] = [p.strip() for p in excl_raw.splitlines() if p.strip()]
        site["crawl"] = crawl

    return site, ""


# ── Site form template ─────────────────────────────────────────────────────────


# ── Site management routes ────────────────────────────────────────────────────

def _trigger_immediate_check(site: dict):
    """
    Fire an immediate one-shot check for a site using the running scheduler.
    Falls back to a background thread if the scheduler is not available
    (e.g. during testing or when dashboard is run standalone).
    Called after add/edit so the site gets a baseline or refreshed snapshot
    without waiting for its next scheduled interval.
    """
    # Path 1 — running inside the normal run.py process: use APScheduler
    try:
        import run as _run_module
        sched = getattr(_run_module, "_scheduler", None)
        if sched is not None and sched.running:
            from monitor.core import check_site as _cs
            sched.add_job(
                _cs,
                "date",
                args=[site],
                id=f"__immediate_{site['name']}",
                misfire_grace_time=120,
                replace_existing=True,
            )
            return
    except Exception:
        pass

    # Path 2 — fallback: fire in a daemon thread
    import threading, asyncio as _asyncio

    def _run():
        from monitor.core import check_site as _cs
        _asyncio.run(_cs(site, force=True))

    threading.Thread(target=_run, daemon=True).start()


@app.route("/sites/add", methods=["GET", "POST"])
def site_add():
    error = ""
    site  = {}
    windows_yaml      = ""
    ignore_selectors_text = ""
    exclude_patterns_text = ""

    if request.method == "POST":
        site, error = form_to_site(request.form)
        if not error:
            data = load_yaml_raw()
            # Check for duplicate name
            if find_site_index(data["sites"], site["name"]) >= 0:
                error = f"A site named \"{site['name']}\" already exists. Choose a different name."
                site = dict(request.form)  # re-populate form
            else:
                data["sites"].append(site)
                save_yaml(data)
                # Trigger an immediate baseline check via the running scheduler
                _trigger_immediate_check(site)
                flash(f"Site \"{site['name']}\" added. Baseline check starting…", "success")
                return redirect(url_for("index"))
        else:
            # Re-populate form fields from raw form data on error
            site = {k: v for k, v in request.form.items()}
            windows_yaml = request.form.get("windows_yaml", "")
            ignore_selectors_text = request.form.get("ignore_selectors", "")
            exclude_patterns_text = request.form.get("exclude_patterns", "")

    return render_template(
        "site_form.html",
        page_title="Add Site",
        site=site,
        error=error,
        windows_yaml=windows_yaml,
        ignore_selectors_text=ignore_selectors_text,
        exclude_patterns_text=exclude_patterns_text,
        crawl_use_sitemap=True,
        crawl_stay_on_domain=True,
        crawl_max_pages=50,
    )


@app.route("/sites/edit/<path:site_name>", methods=["GET", "POST"])
def site_edit(site_name):
    data  = load_yaml_raw()
    idx   = find_site_index(data["sites"], site_name)
    error = ""

    if idx < 0:
        flash(f"Site \"{site_name}\" not found.", "error")
        return redirect(url_for("index"))

    existing = data["sites"][idx]

    if request.method == "POST":
        new_site, error = form_to_site(request.form)
        if not error:
            # If name changed, check it is not a duplicate of another site
            if new_site["name"] != site_name:
                other_idx = find_site_index(data["sites"], new_site["name"])
                if other_idx >= 0 and other_idx != idx:
                    error = f"A site named \"{new_site['name']}\" already exists."

            if not error:
                # Rename in DB if name changed
                if new_site["name"] != site_name:
                    import sqlite3 as _sq
                    with _sq.connect(DB) as conn:
                        for tbl in ("snapshots", "changes", "job_log"):
                            conn.execute(
                                f"UPDATE {tbl} SET site_name=? WHERE site_name=?",
                                (new_site["name"], site_name)
                            )
                        conn.commit()
                data["sites"][idx] = new_site
                save_yaml(data)
                # Re-check immediately so any URL/settings change takes effect now
                _trigger_immediate_check(new_site)
                flash(f"Site \"{new_site['name']}\" updated. Re-checking now…", "success")
                return redirect(url_for("index"))

        # Re-populate on error
        existing = {k: v for k, v in request.form.items()}

    # Build pre-fill values for special fields
    windows_yaml = ""
    if existing.get("schedule_type") == "time_window":
        wins = existing.get("windows", [])
        if wins:
            windows_yaml = yaml.dump(wins, default_flow_style=False).strip()

    ignore_selectors_text = "\n".join(existing.get("ignore_selectors", []))
    crawl = existing.get("crawl", {})
    exclude_patterns_text = "\n".join(crawl.get("exclude_patterns", []))

    return render_template(
        "site_form.html",
        page_title=f"Edit Site — {site_name}",
        site=existing,
        error=error,
        windows_yaml=windows_yaml,
        ignore_selectors_text=ignore_selectors_text,
        exclude_patterns_text=exclude_patterns_text,
        crawl_use_sitemap=crawl.get("use_sitemap", True),
        crawl_stay_on_domain=crawl.get("stay_on_domain", True),
        crawl_max_pages=crawl.get("max_pages", 50),
    )


@app.route("/sites/delete/<path:site_name>", methods=["POST"])
def site_delete(site_name):
    data = load_yaml_raw()
    idx  = find_site_index(data["sites"], site_name)
    if idx < 0:
        flash(f"Site \"{site_name}\" not found.", "error")
        return redirect(url_for("index"))
    data["sites"].pop(idx)
    save_yaml(data)
    flash(f"Site \"{site_name}\" deleted.", "success")
    return redirect(url_for("index"))


def start_dashboard():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
