from flask import Flask, render_template_string, send_file
import sqlite3

app = Flask(__name__)
DB = "data/monitor.db"

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
    h2 { font-size: 16px; margin-bottom: 16px; color: #94a3b8 }
    .grid { display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 16px; margin-bottom: 40px }
    .card { background: #1e293b; border-radius: 12px;
            padding: 20px; border: 1px solid #334155 }
    .card .number { font-size: 36px; font-weight: 700; color: #38bdf8 }
    .card .label  { font-size: 13px; color: #94a3b8; margin-top: 4px }
    table { width: 100%; border-collapse: collapse;
            background: #1e293b; border-radius: 12px; overflow: hidden }
    thead tr { background: #0f172a }
    th { text-align: left; padding: 12px 16px; font-size: 12px;
         color: #64748b; text-transform: uppercase; letter-spacing: .05em }
    td { padding: 12px 16px; font-size: 14px;
         border-top: 1px solid #334155; color: #cbd5e1 }
    .badge { display: inline-block; padding: 2px 10px;
             border-radius: 999px; font-size: 12px; font-weight: 600 }
    .low    { background: #422006; color: #fbbf24 }
    .medium { background: #431407; color: #fb923c }
    .high   { background: #450a0a; color: #f87171 }
    .site   { color: #f1f5f9; font-weight: 500 }
    .url    { color: #38bdf8; font-size: 12px }
    .empty  { text-align: center; padding: 60px; color: #475569 }
    .ok     { color: #22c55e }
    .err    { color: #ef4444 }
  </style>
</head>
<body>
  <h1>🔍 Web<span>Monitor</span></h1>

  <div class="grid">
    <div class="card">
      <div class="number">{{ stats.total_sites }}</div>
      <div class="label">Sites monitored</div>
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

  <h2>Recent Changes</h2>

  {% if changes %}
  <table>
    <thead>
      <tr>
        <th>Site</th>
        <th>Change</th>
        <th>When</th>
      </tr>
    </thead>
    <tbody>
      {% for c in changes %}
      <tr>
        <td>
          <div class="site">{{ c['site_name'] }}</div>
          <div class="url">{{ c['url'][:60] }}</div>
        </td>
        <td>
          {% set pct = c['change_pct'] | float %}
          {% if pct < 5 %}
            <span class="badge low">{{ pct }}% minor</span>
          {% elif pct < 20 %}
            <span class="badge medium">{{ pct }}% moderate</span>
          {% else %}
            <span class="badge high">{{ pct }}% major</span>
          {% endif %}
        </td>
        <td>{{ c['timestamp'] }}</td>
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


def success_rate(db) -> int:
    total = db.execute("SELECT COUNT(*) FROM job_log").fetchone()[0]
    if total == 0:
        return 100
    success = db.execute(
        "SELECT COUNT(*) FROM job_log WHERE status='success'"
    ).fetchone()[0]
    return round((success / total) * 100)


@app.route("/")
def index():
    db = get_db()
    changes = db.execute("""
        SELECT site_name, url, change_pct, diff_screenshot, timestamp
        FROM changes ORDER BY timestamp DESC LIMIT 50
    """).fetchall()

    stats = {
        "total_sites": db.execute(
            "SELECT COUNT(DISTINCT site_name) FROM snapshots"
        ).fetchone()[0],
        "total_changes": db.execute(
            "SELECT COUNT(*) FROM changes"
        ).fetchone()[0],
        "checks_today": db.execute(
            "SELECT COUNT(*) FROM job_log WHERE DATE(timestamp)=DATE('now')"
        ).fetchone()[0],
        "success_rate": success_rate(db),
    }
    db.close()
    return render_template_string(TEMPLATE, changes=changes, stats=stats)


def start_dashboard():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
