from flask import Flask, render_template_string, send_file, redirect, url_for, jsonify
import sqlite3
import yaml
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

app = Flask(__name__)
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
  <h2>Monitored Sites</h2>
  <table>
    <thead>
      <tr>
        <th>Site</th>
        <th>Mode</th>
        <th>Schedule</th>
        <th>Last Check</th>
        <th>Changes</th>
        <th>Status</th>
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

def start_dashboard():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
