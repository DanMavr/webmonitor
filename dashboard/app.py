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
          {%
