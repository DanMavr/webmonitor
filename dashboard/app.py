from flask import Flask, render_template_string, send_file, redirect, url_for, jsonify, request, flash
import sqlite3
import yaml
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
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


TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>WebMonitor</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0 }
    body {
      background: #0f172a; color: #e2e8f0;
      font-family: system-ui, sans-serif; padding: 24px;
    }
    h1 { font-size: 22px; font-weight: 700;
         margin-bottom: 24px; color: #f8fafc }
    h1 span { color: #38bdf8 }
    h2 { font-size: 16px; margin-bottom: 16px;
         color: #94a3b8; margin-top: 40px }
    .grid { display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 16px; margin-bottom: 40px }
    .card { background: #1e293b; border-radius: 12px;
            padding: 20px; border: 1px solid #334155 }
    .card .number { font-size: 36px; font-weight: 700; color: #38bdf8 }
    .card .label  { font-size: 13px; color: #94a3b8; margin-top: 4px }
    table { width: 100%; border-collapse: collapse;
            background: #1e293b; border-radius: 12px;
            overflow: hidden; margin-bottom: 16px }
    thead tr { background: #0f172a }
    th { text-align: left; padding: 12px 16px; font-size: 12px;
         color: #64748b; text-transform: uppercase;
         letter-spacing: .05em }
    td { padding: 12px 16px; font-size: 14px;
         border-top: 1px solid #334155; color: #cbd5e1 }
    .badge { display: inline-block; padding: 2px 10px;
             border-radius: 999px; font-size: 12px;
             font-weight: 600 }
    .low    { background: #422006; color: #fbbf24 }
    .medium { background: #431407; color: #fb923c }
    .high   { background: #450a0a; color: #f87171 }
    .ok     { background: #052e16; color: #22c55e }
    .error  { background: #450a0a; color: #f87171 }
    .site   { color: #f1f5f9; font-weight: 500 }
    .url    { color: #38bdf8; font-size: 12px }
    .empty  { text-align: center; padding: 60px; color: #475569 }
    .schedule { color: #94a3b8; font-size: 12px; margin-top: 2px }
    .header-row { display: flex; align-items: center;
                  justify-content: space-between; margin-bottom: 24px }
    .btn-check {
      background: #0ea5e9; color: #fff; border: none;
      padding: 10px 20px; border-radius: 8px; font-size: 14px;
      font-weight: 600; cursor: pointer; text-decoration: none;
      display: inline-block;
    }
    .btn-check:hover { background: #38bdf8 }
    .btn-check:disabled { background: #334155; color: #64748b;
                          cursor: not-allowed }

    /* Diff expand/collapse */
    .diff-toggle {
      background: none; border: none; color: #38bdf8;
      font-size: 12px; cursor: pointer; padding: 0;
      margin-top: 4px; display: block;
    }
    .diff-toggle:hover { color: #7dd3fc }
    .diff-box {
      display: none; margin-top: 8px; background: #0f172a;
      border-radius: 6px; padding: 10px 12px;
      font-family: monospace; font-size: 12px;
      border: 1px solid #334155; white-space: pre-wrap;
      max-height: 300px; overflow-y: auto;
    }
    .diff-box .added   { color: #22c55e }
    .diff-box .removed { color: #f87171 }
    .diff-box .meta    { color: #64748b }

    /* Show more */
    .show-more-row td { text-align: center; padding: 12px }
    .btn-show-more {
      background: none; border: 1px solid #334155;
      color: #94a3b8; padding: 6px 18px; border-radius: 6px;
      font-size: 13px; cursor: pointer;
    }
    .btn-show-more:hover { border-color: #38bdf8; color: #38bdf8 }
    .hidden-row { display: none }

    .tz-note { font-size: 11px; color: #475569; margin-top: 4px }
  </style>
</head>
<body>

  <div class="header-row">
    <div>
      <h1>Web<span>Monitor</span></h1>
      <div class="tz-note">All times shown in Prague time (CEST)</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <a href="/logs" style="color:#94a3b8;font-size:13px;text-decoration:none;padding:10px 16px;border:1px solid #334155;border-radius:8px">View Logs</a>
      <button class="btn-check" id="checkBtn" onclick="runCheckNow()">
        Check All Now
      </button>
    </div>
  </div>

  <!-- Stats Row -->
  <div class="grid">
    <div class="card">
      <div class="number">{{ stats.total_sites }}</div>
      <div class="label">Sites monitored</div>
    </div>
    <div class="card">
      <div class="number">{{ stats.total_pages }}</div>
      <div class="label">Pages monitored</div>
    </div>
    <div class="card">
      <div class="number">{{ stats.total_changes }}</div>
      <div class="label">Total changes</div>
    </div>
    <div class="card">
      <div class="number">{{ stats.checks_today }}</div>
      <div class="label">Checks today</div>
    </div>
    <div class="card">
      <div class="number" style="color:#22c55e">
        {{ stats.success_rate }}%
      </div>
      <div class="label">Success rate</div>
    </div>
  </div>

  <!-- Monitored Sites Table -->
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <h2 style="margin:0">Monitored Sites</h2>
    <a href="/sites/add" class="btn-check" style="font-size:13px;padding:8px 16px">+ Add Site</a>
  </div>
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
      {% for category, message in messages %}
        <div style="background:{% if category=='error' %}#450a0a{% else %}#052e16{% endif %};
                    color:{% if category=='error' %}#f87171{% else %}#22c55e{% endif %};
                    padding:10px 16px;border-radius:8px;margin-bottom:16px;font-size:14px">
          {{ message }}
        </div>
      {% endfor %}
    {% endif %}
  {% endwith %}
  <table>
    <thead>
      <tr>
        <th>Site</th>
        <th>Mode</th>
        <th>Schedule</th>
        <th>Last Check</th>
        <th>Changes</th>
        <th>Status</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for site in monitored_sites %}
      <tr>
        <td>
          <div class="site">{{ site.name }}</div>
          <div class="url">{{ site.url }}</div>
        </td>
        <td style="color:#94a3b8;font-size:13px">{{ site.mode }}</td>
        <td>
          <div class="schedule">{{ site.schedule }}</div>
        </td>
        <td style="font-size:13px">{{ site.last_check }}</td>
        <td style="font-size:13px">{{ site.total_changes }}</td>
        <td>
          {% if site.status == "OK" %}
            <span class="badge ok">OK</span>
          {% elif site.status == "Error" %}
            <span class="badge error">Error</span>
          {% else %}
            <span style="font-size:12px;color:#64748b">Pending</span>
          {% endif %}
          <br>
          <a href="/inspect/{{ site.name }}"
             style="color:#38bdf8;font-size:11px">
            Inspect
          </a>
        </td>
        <td style="white-space:nowrap">
          <a href="/sites/edit/{{ site.name }}"
             style="color:#38bdf8;font-size:12px;margin-right:12px">Edit</a>
          <form method="POST" action="/sites/delete/{{ site.name }}"
                style="display:inline"
                onsubmit="return confirm('Delete {{ site.name }}? This cannot be undone.')">
            <button type="submit"
                    style="background:none;border:none;color:#f87171;
                           font-size:12px;cursor:pointer;padding:0">
              Delete
            </button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <!-- Recent Changes Table -->
  <h2>Recent Changes</h2>

  {% if changes %}
  <table>
    <thead>
      <tr>
        <th>Site</th>
        <th>Page</th>
        <th>Change</th>
        <th>When</th>
      </tr>
    </thead>
    <tbody>
      {% for c in changes %}
      <tr class="{% if loop.index > 10 %}hidden-row extra-row{% endif %}">
        <td class="site">{{ c.site_name }}</td>
        <td>
          <a href="{{ c.url }}" target="_blank"
             style="color:#38bdf8;font-size:13px">
            {{ c.short_url }}
          </a>
        </td>
        <td>
          {% set pct = c.change_pct | float %}
          {% if pct < 5 %}
            <span class="badge low">{{ c.change_pct }}% minor</span>
          {% elif pct < 20 %}
            <span class="badge medium">{{ c.change_pct }}% moderate</span>
          {% else %}
            <span class="badge high">{{ c.change_pct }}% major</span>
          {% endif %}

          {% if c.added or c.removed %}
          <button class="diff-toggle" onclick="toggleDiff(this)">
            ▶ Show what changed
          </button>
          <div class="diff-box">
            {% if c.added %}
            <div style="margin-bottom:6px;color:#64748b;font-size:11px">
              ADDED ({{ c.added | length }} line{{ 's' if c.added | length != 1 }})
            </div>
            {% for line in c.added[:20] %}
            <div class="added">+ {{ line }}</div>
            {% endfor %}
            {% if c.added | length > 20 %}
            <div class="meta">... {{ c.added | length - 20 }} more lines</div>
            {% endif %}
            {% endif %}

            {% if c.removed %}
            <div style="margin-top:8px;margin-bottom:6px;
                        color:#64748b;font-size:11px">
              REMOVED ({{ c.removed | length }} line{{ 's' if c.removed | length != 1 }})
            </div>
            {% for line in c.removed[:20] %}
            <div class="removed">- {{ line }}</div>
            {% endfor %}
            {% if c.removed | length > 20 %}
            <div class="meta">... {{ c.removed | length - 20 }} more lines</div>
            {% endif %}
            {% endif %}
          </div>
          {% endif %}
        </td>
        <td style="font-size:13px">{{ c.timestamp }}</td>
      </tr>
      {% endfor %}

      {% if changes | length > 10 %}
      <tr class="show-more-row" id="showMoreRow">
        <td colspan="4">
          <button class="btn-show-more" onclick="showAll()">
            Show {{ changes | length - 10 }} more
          </button>
        </td>
      </tr>
      {% endif %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">
    <p>No changes detected yet.</p>
    <p style="font-size:13px;margin-top:8px">
      Waiting for first monitoring cycle...
    </p>
  </div>
  {% endif %}

  <script>
    function runCheckNow() {
      const btn = document.getElementById("checkBtn");
      btn.disabled = true;
      btn.textContent = "Checking...";
      fetch("/check-now", { method: "POST" })
        .then(res => res.json())
        .then(() => { window.location.reload(); })
        .catch(() => {
          btn.disabled = false;
          btn.textContent = "Check All Now";
          alert("Request failed — check service logs.");
        });
    }

    function toggleDiff(btn) {
      const box = btn.nextElementSibling;
      const open = box.style.display === "block";
      box.style.display = open ? "none" : "block";
      btn.textContent = open ? "▶ Show what changed" : "▼ Hide";
    }

    function showAll() {
      document.querySelectorAll(".extra-row").forEach(r => {
        r.style.display = "";
      });
      document.getElementById("showMoreRow").style.display = "none";
    }
  </script>

</body>
</html>
"""


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
    return render_template_string(
        TEMPLATE,
        monitored_sites=monitored_sites,
        changes=changes,
        stats=stats,
    )


@app.route("/check-now", methods=["POST"])
def check_now():
    config_sites = load_config()

    async def run_all():
        from monitor.core import check_site
        for site in config_sites:
            await check_site(site, force=True)

    try:
        asyncio.run(run_all())
        return jsonify({"status": "ok", "checked": len(config_sites)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/inspect/<path:site_name>")
def inspect_site(site_name):
    from monitor.inspect import get_baseline_summary

    summary = get_baseline_summary(site_name)

    if "error" in summary:
        return render_template_string(
            "<h2 style='color:white;font-family:sans-serif;"
            "background:#0f172a;padding:20px'>"
            + summary["error"] + "</h2>"
        )

    INSPECT_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
      <title>Inspect - {{ summary.site_name }}</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0 }
        body { background: #0f172a; color: #e2e8f0;
               font-family: system-ui, sans-serif; padding: 24px }
        h1 { font-size: 20px; margin-bottom: 8px; color: #f8fafc }
        h1 span { color: #38bdf8 }
        h2 { font-size: 15px; color: #94a3b8; margin: 32px 0 16px }
        .back { color: #38bdf8; font-size: 14px;
                text-decoration: none; display: block;
                margin-bottom: 24px }
        .stats { display: grid;
                 grid-template-columns: repeat(auto-fill, minmax(160px,1fr));
                 gap: 12px; margin-bottom: 32px }
        .stat { background: #1e293b; border-radius: 10px;
                padding: 16px; border: 1px solid #334155 }
        .stat .n { font-size: 28px; font-weight: 700; color: #38bdf8 }
        .stat .l { font-size: 12px; color: #94a3b8; margin-top: 4px }
        table { width: 100%; border-collapse: collapse;
                background: #1e293b; border-radius: 12px;
                overflow: hidden }
        thead tr { background: #0f172a }
        th { text-align: left; padding: 12px 16px; font-size: 12px;
             color: #64748b; text-transform: uppercase }
        td { padding: 12px 16px; font-size: 13px;
             border-top: 1px solid #334155; color: #cbd5e1;
             vertical-align: top }
        .ok      { color: #22c55e; font-weight: 600 }
        .warning { color: #f59e0b; font-weight: 600 }
        .preview { color: #64748b; font-size: 12px;
                   margin-top: 4px; font-style: italic }
        .url { color: #38bdf8; font-size: 12px }
        .words { color: #94a3b8; font-size: 12px }
      </style>
    </head>
    <body>
      <a href="/" class="back">← Back to dashboard</a>
      <h1>Inspect: <span>{{ summary.site_name }}</span></h1>

      <div class="stats">
        <div class="stat">
          <div class="n">{{ summary.total_pages }}</div>
          <div class="l">Pages captured</div>
        </div>
        <div class="stat">
          <div class="n">{{ summary.total_words }}</div>
          <div class="l">Total words</div>
        </div>
        <div class="stat">
          <div class="n"
            {% if summary.healthy_pages == summary.total_pages %}
              style="color:#22c55e"
            {% else %}
              style="color:#f59e0b"
            {% endif %}>
            {{ summary.healthy_pages }}/{{ summary.total_pages }}
          </div>
          <div class="l">Healthy pages</div>
        </div>
      </div>

      <h2>Page Breakdown</h2>
      <table>
        <thead>
          <tr>
            <th>Page</th>
            <th>Words</th>
            <th>Status</th>
            <th>Last Captured</th>
          </tr>
        </thead>
        <tbody>
          {% for page in summary.pages %}
          <tr>
            <td>
              <div class="url">{{ page.url }}</div>
              <div class="preview">{{ page.preview[:120] }}...</div>
            </td>
            <td class="words">{{ page.word_count }}</td>
            <td>
              {% if page.healthy %}
                <span class="ok">OK</span>
              {% else %}
                <span class="warning">Low content</span>
              {% endif %}
            </td>
            <td style="font-size:12px">{{ page.timestamp }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </body>
    </html>
    """

    return render_template_string(INSPECT_TEMPLATE, summary=summary)

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

    parsed = []
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

        parsed.append({
            "ts": ts_display,
            "level": level,
            "message": message,
        })

    LOG_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
      <title>WebMonitor — Logs</title>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0 }
        body { background: #0f172a; color: #e2e8f0;
               font-family: system-ui, sans-serif; padding: 24px }
        .header-row { display: flex; align-items: center;
                      justify-content: space-between; margin-bottom: 20px }
        h1 { font-size: 22px; font-weight: 700; color: #f8fafc }
        h1 span { color: #38bdf8 }
        .tz-note { font-size: 11px; color: #475569; margin-top: 4px }
        .back { color: #38bdf8; font-size: 13px; text-decoration: none }
        .back:hover { color: #7dd3fc }
        .controls { display: flex; gap: 8px; flex-wrap: wrap;
                    margin-bottom: 16px; align-items: center }
        .filter-btn {
          border: 1px solid #334155; background: none;
          color: #94a3b8; padding: 5px 14px; border-radius: 6px;
          font-size: 12px; cursor: pointer;
        }
        .filter-btn:hover, .filter-btn.active {
          border-color: #38bdf8; color: #38bdf8;
        }
        .filter-btn.active { background: #0c2a3e }
        .search-box {
          background: #1e293b; border: 1px solid #334155;
          color: #e2e8f0; padding: 5px 12px; border-radius: 6px;
          font-size: 12px; width: 220px;
        }
        .search-box::placeholder { color: #475569 }
        .auto-refresh {
          margin-left: auto; font-size: 12px; color: #475569;
          display: flex; align-items: center; gap: 6px
        }
        .log-wrap {
          background: #0a0f1e; border: 1px solid #1e293b;
          border-radius: 10px; overflow: hidden;
        }
        .log-line {
          display: flex; gap: 12px; padding: 5px 14px;
          border-bottom: 1px solid #0f172a;
          font-family: monospace; font-size: 12px; line-height: 1.5;
        }
        .log-line:hover { background: #111827 }
        .ts { color: #475569; white-space: nowrap; min-width: 140px }
        .badge {
          font-size: 10px; font-weight: 700; padding: 1px 7px;
          border-radius: 4px; white-space: nowrap;
          align-self: center; min-width: 72px; text-align: center;
        }
        .msg { color: #cbd5e1; word-break: break-word }
        .badge-INFO     { background:#1e3a5f; color:#60a5fa }
        .badge-CHECKING { background:#1e3a5f; color:#38bdf8 }
        .badge-BASELINE { background:#052e16; color:#22c55e }
        .badge-CHANGE   { background:#3b1f00; color:#f59e0b }
        .badge-JS       { background:#2d1f00; color:#fbbf24 }
        .badge-WARNING  { background:#2d1f00; color:#fb923c }
        .badge-ERROR    { background:#450a0a; color:#f87171 }
        .badge-WATCHDOG { background:#450a0a; color:#f87171 }
        .msg-CHANGE     { color: #f59e0b }
        .msg-ERROR      { color: #f87171 }
        .msg-WATCHDOG   { color: #f87171 }
        .msg-BASELINE   { color: #22c55e }
        .empty  { text-align: center; padding: 60px; color: #475569 }
        .count  { font-size: 12px; color: #475569; margin-bottom: 10px }
        .hidden { display: none !important }
      </style>
    </head>
    <body>
      <div class="header-row">
        <div>
          <h1>Web<span>Monitor</span> — Logs</h1>
          <div class="tz-note">Times shown in Prague time (CEST) &nbsp;·&nbsp;
            Last {{ lines|length }} entries
          </div>
        </div>
        <a href="/" class="back">← Back to dashboard</a>
      </div>

      <div class="controls">
        <button class="filter-btn active" onclick="setFilter('ALL', this)">All</button>
        <button class="filter-btn" onclick="setFilter('CHANGE', this)">Changes</button>
        <button class="filter-btn" onclick="setFilter('ERROR', this)">Errors</button>
        <button class="filter-btn" onclick="setFilter('WATCHDOG', this)">Watchdog</button>
        <button class="filter-btn" onclick="setFilter('WARNING', this)">Warnings</button>
        <input class="search-box" type="text" id="searchBox"
               placeholder="Search logs..." oninput="applyFilters()">
        <div class="auto-refresh">
          <input type="checkbox" id="autoRefresh" onchange="toggleAutoRefresh()">
          <label for="autoRefresh" style="cursor:pointer">Auto-refresh 30s</label>
        </div>
      </div>

      <div class="count" id="lineCount">Showing {{ lines|length }} lines</div>

      {% if lines %}
      <div class="log-wrap" id="logWrap">
        {% for entry in lines %}
        <div class="log-line" data-level="{{ entry.level }}">
          <span class="ts">{{ entry.ts }}</span>
          <span class="badge badge-{{ entry.level }}">{{ entry.level }}</span>
          <span class="msg msg-{{ entry.level }}">{{ entry.message }}</span>
        </div>
        {% endfor %}
      </div>
      {% else %}
      <div class="empty">
        <p>No activity log yet.</p>
        <p style="font-size:13px;margin-top:8px">Logs will appear here after the monitor runs.</p>
      </div>
      {% endif %}

      <script>
        let currentFilter = 'ALL';
        let refreshTimer = null;

        function setFilter(level, btn) {
          currentFilter = level;
          document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          applyFilters();
        }

        function applyFilters() {
          const search = document.getElementById('searchBox').value.toLowerCase();
          const rows = document.querySelectorAll('.log-line');
          let visible = 0;
          rows.forEach(row => {
            const level = row.dataset.level;
            const text  = row.textContent.toLowerCase();
            const levelOk  = currentFilter === 'ALL' || level === currentFilter;
            const searchOk = !search || text.includes(search);
            if (levelOk && searchOk) {
              row.classList.remove('hidden');
              visible++;
            } else {
              row.classList.add('hidden');
            }
          });
          document.getElementById('lineCount').textContent = `Showing ${visible} lines`;
        }

        function toggleAutoRefresh() {
          const checked = document.getElementById('autoRefresh').checked;
          if (checked) {
            refreshTimer = setInterval(() => location.reload(), 30000);
          } else {
            clearInterval(refreshTimer);
            refreshTimer = null;
          }
        }
      </script>
    </body>
    </html>
    """

    return render_template_string(LOG_TEMPLATE, lines=parsed)


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

SITE_FORM_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <title>WebMonitor — {{ page_title }}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0 }
    body { background: #0f172a; color: #e2e8f0;
           font-family: system-ui, sans-serif; padding: 32px 24px }
    a.back { color: #38bdf8; font-size: 13px; text-decoration: none;
             display: inline-block; margin-bottom: 24px }
    a.back:hover { color: #7dd3fc }
    h1 { font-size: 20px; font-weight: 700; color: #f8fafc; margin-bottom: 28px }
    h1 span { color: #38bdf8 }
    .form-card { background: #1e293b; border: 1px solid #334155;
                 border-radius: 14px; padding: 28px; max-width: 700px }
    .section-title { font-size: 12px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: .08em; color: #64748b; margin: 24px 0 14px;
                     padding-bottom: 6px; border-bottom: 1px solid #334155 }
    .field { margin-bottom: 16px }
    label { display: block; font-size: 13px; color: #94a3b8; margin-bottom: 5px }
    label span.req { color: #f87171; margin-left: 2px }
    input[type=text], input[type=number], input[type=url], select, textarea {
      width: 100%; background: #0f172a; border: 1px solid #334155;
      color: #e2e8f0; padding: 9px 12px; border-radius: 8px;
      font-size: 14px; font-family: inherit;
    }
    input:focus, select:focus, textarea:focus {
      outline: none; border-color: #38bdf8;
    }
    textarea { resize: vertical; min-height: 90px; font-family: monospace; font-size: 12px }
    .hint { font-size: 11px; color: #475569; margin-top: 4px }
    .toggle-row { display: flex; gap: 24px; align-items: center; margin-bottom: 16px }
    .toggle-group { display: flex; align-items: center; gap: 8px }
    .toggle-group label { margin: 0; cursor: pointer; font-size: 13px; color: #94a3b8 }
    input[type=checkbox] { width: 16px; height: 16px; cursor: pointer;
                           accent-color: #38bdf8 }
    .collapsible { border: 1px solid #334155; border-radius: 10px;
                   overflow: hidden; margin-top: 8px }
    .collapsible-header { background: #0f172a; padding: 12px 16px;
                          cursor: pointer; font-size: 13px; color: #94a3b8;
                          display: flex; justify-content: space-between;
                          align-items: center; user-select: none }
    .collapsible-header:hover { color: #e2e8f0 }
    .collapsible-body { padding: 20px; display: none }
    .collapsible-body.open { display: block }
    .example-box { background: #0a1628; border: 1px solid #1e3a5f;
                   border-radius: 8px; padding: 14px 16px; margin-top: 10px }
    .example-box .ex-title { font-size: 11px; color: #38bdf8; font-weight: 700;
                              text-transform: uppercase; letter-spacing: .06em;
                              margin-bottom: 8px }
    .example-box pre { font-size: 11px; color: #7dd3fc; line-height: 1.6;
                       white-space: pre; font-family: monospace }
    .example-box .ex-note { font-size: 11px; color: #475569; margin-top: 8px }
    .error-box { background: #450a0a; color: #f87171; border-radius: 8px;
                 padding: 12px 16px; margin-bottom: 20px; font-size: 14px }
    .btn-row { display: flex; gap: 12px; margin-top: 24px; align-items: center }
    .btn-save {
      background: #0ea5e9; color: #fff; border: none;
      padding: 11px 28px; border-radius: 8px; font-size: 14px;
      font-weight: 600; cursor: pointer;
    }
    .btn-save:hover { background: #38bdf8 }
    .btn-cancel { color: #64748b; font-size: 13px; text-decoration: none }
    .btn-cancel:hover { color: #94a3b8 }
    #crawl-section { display: none }
    #interval-section { display: block }
    #timewindow-section { display: none }
    #js-fields { display: none }
  </style>
</head>
<body>
  <a href="/" class="back">← Back to dashboard</a>
  <h1>{{ page_title }}</h1>

  {% if error %}
    <div class="error-box">{{ error }}</div>
  {% endif %}

  <div class="form-card">
    <form method="POST">

      <!-- ── BASIC ── -->
      <div class="section-title">Basic</div>

      <div class="field">
        <label>Site name <span class="req">*</span></label>
        <input type="text" name="name" value="{{ site.name or '' }}"
               placeholder="e.g. LSE - RNS" required>
        <div class="hint">Display name shown on the dashboard. Must be unique.</div>
      </div>

      <div class="field">
        <label>URL <span class="req">*</span></label>
        <input type="url" name="url" value="{{ site.url or '' }}"
               placeholder="https://example.com/page" required>
      </div>

      <div class="field">
        <label>Monitoring mode</label>
        <select name="mode" id="mode-select" onchange="onModeChange(this.value)">
          <option value="single_page" {% if site.mode not in ('whole_site','json_api') %}selected{% endif %}>
            single_page — monitor one specific page only
          </option>
          <option value="whole_site" {% if site.mode == 'whole_site' %}selected{% endif %}>
            whole_site — crawl and monitor multiple pages
          </option>
          <option value="json_api" {% if site.mode == 'json_api' %}selected{% endif %}>
            json_api — watch specific fields from a JSON API
          </option>
        </select>
      </div>

      <!-- ── SCHEDULE ── -->
      <div class="section-title">Schedule</div>

      <div class="field">
        <label>Schedule type</label>
        <select name="schedule_type" id="sched-select"
                onchange="onSchedChange(this.value)">
          <option value="interval"
            {% if site.schedule_type != 'time_window' %}selected{% endif %}>
            interval — check every N minutes, always
          </option>
          <option value="time_window"
            {% if site.schedule_type == 'time_window' %}selected{% endif %}>
            time_window — check only during defined time windows
          </option>
        </select>
      </div>

      <div id="interval-section">
        <div class="field">
          <label>Check interval (minutes)</label>
          <input type="number" name="interval_minutes" min="1" max="10080"
                 value="{{ site.interval_minutes or 360 }}">
          <div class="hint">Common values: 60 (1h), 360 (6h), 720 (12h)</div>
        </div>
      </div>

      <div id="timewindow-section">
        <div class="field">
          <label>Timezone</label>
          <input type="text" name="timezone"
                 value="{{ site.timezone or 'Europe/London' }}"
                 placeholder="Europe/London">
          <div class="hint">IANA timezone name. Used to interpret window times.</div>
        </div>
        <div class="field">
          <label>Windows (YAML)</label>
          <textarea name="windows_yaml" id="windows-yaml"
                    placeholder="Paste your windows YAML here..."
                    rows="8">{{ windows_yaml or '' }}</textarea>
          <div class="hint">Define one or more time windows as a YAML list.</div>

          <!-- Example box -->
          <div class="example-box" style="margin-top:12px">
            <div class="ex-title">Example — LSE RNS style (two windows)</div>
            <pre>- days: mon-fri
  from: "06:55"
  to: "07:10"
  interval_minutes: 1

- days: mon-fri
  from: "07:10"
  to: "17:00"
  interval_minutes: 5</pre>
            <div class="ex-note">
              Window 1: checks every <strong>1 min</strong> from 06:55–07:10 Mon–Fri
              (catches the early morning RNS drop).<br>
              Window 2: checks every <strong>5 min</strong> from 07:10–17:00 Mon–Fri
              (full market hours coverage).<br><br>
              Valid day values: <code>mon tue wed thu fri sat sun</code>
              or ranges like <code>mon-fri</code> · <code>sat-sun</code><br>
              Times in <code>HH:MM</code> 24h format.
            </div>
          </div>
        </div>
      </div>

      <!-- ── SENSITIVITY ── -->
      <div class="collapsible">
        <div class="collapsible-header" onclick="toggleSection('sensitivity-body','sensitivity-arrow')">
          Sensitivity &amp; Filters
          <span id="sensitivity-arrow">▸</span>
        </div>
        <div class="collapsible-body" id="sensitivity-body">
          <div class="field">
            <label>Min content words</label>
            <input type="number" name="min_content_words" min="0"
                   value="{{ site.min_content_words or 50 }}">
            <div class="hint">Pages with fewer words are skipped (likely empty/error pages).</div>
          </div>
          <div class="field">
            <label>Min change % to trigger alert</label>
            <input type="number" name="min_change_percent" min="0" max="100" step="0.5"
                   value="{{ site.min_change_percent or 3 }}">
            <div class="hint">Changes smaller than this percentage are ignored.</div>
          </div>

          <!-- ── JSON API Fields ─────────────────────────────────────── -->
          <div id="json-api-section" style="display:none">
            <div class="field">
              <label>JSON Fields to monitor</label>
              <div class="hint" style="margin-bottom:8px">
                Paste the API URL above and click <strong>Detect Fields</strong>
                to see all available fields. Tick the ones you want to monitor —
                an alert fires when any ticked field changes value.
              </div>
              <button type="button" id="detect-fields-btn"
                      onclick="detectJsonFields()"
                      style="margin-bottom:12px;padding:6px 16px;background:#38bdf8;color:#000;border:none;border-radius:4px;cursor:pointer;font-weight:600">
                🔍 Detect Fields
              </button>
              <div id="json-fields-loading" style="display:none;color:#94a3b8;font-size:13px">
                Fetching fields…
              </div>
              <div id="json-fields-error" style="display:none;color:#f87171;font-size:13px"></div>
              <div id="json-fields-list" style="margin-top:8px"></div>
              <!-- Hidden textarea stores the final comma-separated field list -->
              <textarea name="json_fields_raw" id="json-fields-raw"
                        style="display:none">{{ site.json_fields|join(',') if site.json_fields else '' }}</textarea>
            </div>
          </div>

          <script>
          // onModeChange is defined globally at bottom of page (merged)
          // Pre-render saved JSON fields as checkboxes on page load
          document.addEventListener('DOMContentLoaded', function() {
            const raw = document.getElementById('json-fields-raw');
            if (raw && raw.value) {
              const saved = raw.value.split(',').map(s => s.trim()).filter(Boolean);
              renderFieldCheckboxes(saved.map(f => ({key: f, value: '…', checked: true})), []);
            }
          });

          function detectJsonFields() {
            const urlInput = document.querySelector('input[name="url"]');
            if (!urlInput || !urlInput.value.trim()) {
              alert('Please enter a URL first.');
              return;
            }
            document.getElementById('json-fields-loading').style.display = 'block';
            document.getElementById('json-fields-error').style.display   = 'none';
            document.getElementById('json-fields-list').innerHTML         = '';

            fetch('/api/json-fields?url=' + encodeURIComponent(urlInput.value.trim()))
              .then(r => r.json())
              .then(data => {
                document.getElementById('json-fields-loading').style.display = 'none';
                if (data.error) {
                  document.getElementById('json-fields-error').textContent = data.error;
                  document.getElementById('json-fields-error').style.display = 'block';
                  return;
                }
                // Get currently saved fields so we pre-check them
                const raw = document.getElementById('json-fields-raw');
                const saved = raw && raw.value
                  ? raw.value.split(',').map(s => s.trim()).filter(Boolean)
                  : [];
                renderFieldCheckboxes(data.fields, saved);
              })
              .catch(e => {
                document.getElementById('json-fields-loading').style.display = 'none';
                document.getElementById('json-fields-error').textContent = 'Request failed: ' + e;
                document.getElementById('json-fields-error').style.display = 'block';
              });
          }

          function renderFieldCheckboxes(fields, saved) {
            const container = document.getElementById('json-fields-list');
            container.innerHTML = '';
            fields.forEach(f => {
              const isChecked = saved.length === 0
                ? false
                : saved.includes(f.key);
              const row = document.createElement('div');
              row.style.cssText = 'display:flex;align-items:center;gap:10px;padding:5px 0;border-bottom:1px solid #1e293b';
              row.innerHTML = \`
                <input type="checkbox" id="jf_\${f.key}" value="\${f.key}"
                       \${isChecked ? 'checked' : ''}
                       onchange="updateJsonFieldsRaw()"
                       style="width:16px;height:16px;cursor:pointer">
                <label for="jf_\${f.key}" style="flex:0 0 220px;font-family:monospace;font-size:13px;color:#38bdf8;cursor:pointer">\${f.key}</label>
                <span style="font-size:13px;color:#94a3b8;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">\${f.value}</span>
              \`;
              container.appendChild(row);
            });
            updateJsonFieldsRaw();
          }

          function updateJsonFieldsRaw() {
            const checkboxes = document.querySelectorAll('#json-fields-list input[type=checkbox]:checked');
            const fields = Array.from(checkboxes).map(c => c.value);
            document.getElementById('json-fields-raw').value = fields.join(',');
          }
          </script>

          <div id="target-selector-field" class="field">
            <label>Target selector <span style="color:#38bdf8;font-size:11px;font-weight:400;margin-left:6px">Focus monitoring on one section only</span></label>
            <input type="text" name="target_selector"
                   value="{{ site.target_selector or '' }}"
                   placeholder=".news-table-results-component">
            <div class="hint">
              CSS selector for the <strong>only</strong> part of the page to monitor.
              Everything outside this element is completely ignored.<br>
              Leave blank to monitor the whole page.<br>
              Example — LSE RNS table only: <code>.news-table-results-component</code>
            </div>
          </div>

          <div class="field">
            <label>Ignore selectors (one per line)</label>
            <textarea name="ignore_selectors"
                      placeholder=".timestamp&#10;.price-ticker&#10;[data-testid='time']"
                      >{{ ignore_selectors_text or '' }}</textarea>
            <div class="hint">CSS selectors for elements to exclude from change detection
              (clocks, live prices, etc.). Used together with target selector if set.</div>
          </div>
        </div>
      </div>

      <!-- ── JAVASCRIPT ── -->
      <div class="collapsible" style="margin-top:10px">
        <div class="collapsible-header" onclick="toggleSection('js-body','js-arrow')">
          JavaScript Rendering
          <span id="js-arrow">▸</span>
        </div>
        <div class="collapsible-body" id="js-body">
          <div class="toggle-row">
            <div class="toggle-group">
              <input type="checkbox" id="js-toggle" name="javascript" value="true"
                     onchange="document.getElementById('js-wait-field').style.display=this.checked?'block':'none'"
                     {% if site.javascript %}checked{% endif %}>
              <label for="js-toggle">Enable Selenium JS rendering (slower, for JS-heavy sites)</label>
            </div>
          </div>
          <div id="js-wait-field" style="display:{% if site.javascript %}block{% else %}none{% endif %}">
            <div class="field">
              <label>JS wait seconds</label>
              <input type="number" name="js_wait_seconds" min="1" max="30"
                     value="{{ site.js_wait_seconds or 5 }}">
              <div class="hint">Seconds to wait for page JS to finish rendering.</div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── ADVANCED ── -->
      <div class="collapsible" style="margin-top:10px">
        <div class="collapsible-header" onclick="toggleSection('adv-body','adv-arrow')">
          Advanced
          <span id="adv-arrow">▸</span>
        </div>
        <div class="collapsible-body" id="adv-body">
          <div class="toggle-row">
            <div class="toggle-group">
              <input type="checkbox" id="ssl-toggle" name="ssl_verify" value="false"
                     {% if site.ssl_verify == False %}checked{% endif %}>
              <label for="ssl-toggle">Disable SSL certificate verification
                (only for sites with self-signed certs)</label>
            </div>
          </div>
        </div>
      </div>

      <!-- ── CRAWL ── -->
      <div id="crawl-section">
        <div class="section-title" style="margin-top:24px">Crawl Settings (whole_site)</div>
        <div class="toggle-row">
          <div class="toggle-group">
            <input type="checkbox" id="sitemap-toggle" name="use_sitemap" value="true"
                   {% if crawl_use_sitemap %}checked{% endif %}>
            <label for="sitemap-toggle">Use sitemap.xml for page discovery</label>
          </div>
          <div class="toggle-group">
            <input type="checkbox" id="domain-toggle" name="stay_on_domain" value="true"
                   {% if crawl_stay_on_domain %}checked{% endif %}>
            <label for="domain-toggle">Stay on domain</label>
          </div>
        </div>
        <div class="field">
          <label>Max pages to monitor</label>
          <input type="number" name="max_pages" min="1" max="500"
                 value="{{ crawl_max_pages }}">
        </div>
        <div class="field">
          <label>Exclude URL patterns (one per line)</label>
          <textarea name="exclude_patterns"
                    placeholder="/login&#10;/search&#10;.pdf&#10;.jpg"
                    >{{ exclude_patterns_text or '' }}</textarea>
          <div class="hint">Any URL containing these strings will be skipped.</div>
        </div>
      </div>

      <div class="btn-row">
        <button type="submit" class="btn-save">Save Site</button>
        <a href="/" class="btn-cancel">Cancel</a>
      </div>
    </form>
  </div>

  <script>
    function onModeChange(v) {
      // whole_site crawl options
      var crawl = document.getElementById('crawl-section');
      if (crawl) crawl.style.display = (v === 'whole_site') ? 'block' : 'none';

      // json_api fields section
      var japi = document.getElementById('json-api-section');
      if (japi) japi.style.display = (v === 'json_api') ? 'block' : 'none';

      // target selector (hide for json_api — not applicable)
      var tgt = document.getElementById('target-selector-field');
      if (tgt) tgt.style.display = (v === 'json_api') ? 'none' : 'block';

      // auto-open Sensitivity & Filters when json_api selected
      if (v === 'json_api') {
        var sensBody = document.getElementById('sensitivity-body');
        var sensArr  = document.getElementById('sensitivity-arrow');
        if (sensBody && !sensBody.classList.contains('open')) {
          sensBody.classList.add('open');
          if (sensArr) sensArr.textContent = '\u25be';
        }
      }
    }
    function onSchedChange(v) {
      document.getElementById('interval-section').style.display =
        v === 'interval' ? 'block' : 'none';
      document.getElementById('timewindow-section').style.display =
        v === 'time_window' ? 'block' : 'none';
    }
    function toggleSection(bodyId, arrowId) {
      var body  = document.getElementById(bodyId);
      var arrow = document.getElementById(arrowId);
      var open  = body.classList.toggle('open');
      arrow.textContent = open ? '\u25be' : '\u25b8';
    }
    // Init on load
    onModeChange(document.getElementById('mode-select').value);
    onSchedChange(document.getElementById('sched-select').value);
  </script>
</body>
</html>
"""


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

    return render_template_string(
        SITE_FORM_TEMPLATE,
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

    return render_template_string(
        SITE_FORM_TEMPLATE,
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
