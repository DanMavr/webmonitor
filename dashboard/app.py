from flask import Flask, render_template_string, send_file
import sqlite3
import yaml
from datetime import datetime

app = Flask(__name__)
DB = "data/monitor.db"
CONFIG = "config/sites.yaml"

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
            overflow: hidden; margin-bottom: 40px }
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
  </style>
</head>
<body>
  <h1>Web<span>Monitor</span></h1>

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
        <td>{{ site.mode }}</td>
        <td>
          <div style="color:#e2e8f0">{{ site.schedule }}</div>
        </td>
        <td style="font-size:13px">{{ site.last_check }}</td>
        <td>{{ site.total_changes }}</td>
        <td>
          {% if site.status == "OK" %}
            <span class="badge ok">OK</span>
          {% elif site.status == "Error" %}
            <span class="badge error">Error</span>
          {% else %}
            <span class="badge" style="background:#1e293b;
              color:#64748b">Pending</span>
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
      <tr>
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
        </td>
        <td style="font-size:13px">{{ c.timestamp }}</td>
      </tr>
      {% endfor %}
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

    # Build monitored sites list combining config + db data
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

        check_time = last_check[0][:16] if last_check else "Not yet"

        monitored_sites.append({
            "name": name,
            "url": site.get("url", ""),
            "mode": site.get("mode", "single_page"),
            "schedule": get_schedule_label(site),
            "last_check": check_time,
            "total_changes": total_changes,
            "status": status,
        })

    # Get recent changes
    raw_changes = db.execute("""
        SELECT site_name, url, change_pct, timestamp
        FROM changes
        ORDER BY timestamp DESC LIMIT 50
    """).fetchall()

    changes = []
    for c in raw_changes:
        from urllib.parse import urlparse
        path = urlparse(c["url"]).path or "/"
        changes.append({
            "site_name": c["site_name"],
            "url": c["url"],
            "short_url": path,
            "change_pct": c["change_pct"],
            "timestamp": c["timestamp"][:16],
        })

    # Count total unique pages monitored
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
        stats=stats
    )

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
        h2 { font-size: 15px; color: #94a3b8;
             margin: 32px 0 16px }
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
      <a href="/" class="back">Back to dashboard</a>
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

def start_dashboard():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
