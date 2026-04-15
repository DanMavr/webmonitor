"""
storage.py — file + SQLite storage.

Layout:
  data/sites/<safe_name>/baseline.png       — last known screenshot
  data/sites/<safe_name>/baseline_text.txt  — OCR text of baseline
  data/changes.db                           — SQLite change + job history
"""
from __future__ import annotations

import re
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("monitor")

_ROOT     = Path(__file__).parent.parent
DB        = str(_ROOT / "data" / "changes.db")
SITES_DIR = _ROOT / "data" / "sites"


def init_db():
    (_ROOT / "data").mkdir(exist_ok=True)
    SITES_DIR.mkdir(exist_ok=True)
    with sqlite3.connect(DB) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS changes (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                added     TEXT,
                removed   TEXT,
                summary   TEXT
            );
            CREATE TABLE IF NOT EXISTS job_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                status    TEXT NOT NULL,
                detail    TEXT
            );
        """)


def _safe(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower())


def _site_dir(name: str) -> Path:
    d = SITES_DIR / _safe(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Baseline ──────────────────────────────────────────────────────────────────

def load_baseline(name: str) -> tuple[Optional[bytes], str]:
    d = _site_dir(name)
    png  = (d / "baseline.png").read_bytes()  if (d / "baseline.png").exists()  else None
    text = (d / "baseline_text.txt").read_text() if (d / "baseline_text.txt").exists() else ""
    return png, text


def save_baseline(name: str, png: bytes, text: str):
    d = _site_dir(name)
    if png:
        (d / "baseline.png").write_bytes(png)
    (d / "baseline_text.txt").write_text(text)


def get_baseline_png_path(name: str) -> Optional[Path]:
    p = _site_dir(name) / "baseline.png"
    return p if p.exists() else None


# ── Logging ───────────────────────────────────────────────────────────────────

def log_job(name: str, status: str, detail: str = ""):
    try:
        with sqlite3.connect(DB) as conn:
            conn.execute(
                "INSERT INTO job_log (site_name,timestamp,status,detail) VALUES (?,?,?,?)",
                (name, _now(), status, detail)
            )
    except Exception as e:
        logger.error(f"log_job: {e}")


def log_change(name: str, diff: dict):
    try:
        with sqlite3.connect(DB) as conn:
            conn.execute(
                "INSERT INTO changes (site_name,timestamp,added,removed,summary) VALUES (?,?,?,?,?)",
                (name, _now(),
                 "\n".join(diff.get("added",[])),
                 "\n".join(diff.get("removed",[])),
                 diff.get("summary",""))
            )
    except Exception as e:
        logger.error(f"log_change: {e}")


# ── Queries ───────────────────────────────────────────────────────────────────

def get_recent_changes(limit: int = 100) -> list[dict]:
    try:
        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM changes ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_site_stats(name: str) -> dict:
    try:
        with sqlite3.connect(DB) as conn:
            conn.row_factory = sqlite3.Row
            last = conn.execute(
                "SELECT status, timestamp, detail FROM job_log "
                "WHERE site_name=? ORDER BY timestamp DESC LIMIT 1", (name,)
            ).fetchone()
            total = conn.execute(
                "SELECT COUNT(*) FROM changes WHERE site_name=?", (name,)
            ).fetchone()[0]
        return {
            "last_status":    last["status"]    if last else None,
            "last_check":     last["timestamp"] if last else None,
            "last_detail":    last["detail"]    if last else "",
            "total_changes":  total,
        }
    except Exception:
        return {"last_status": None, "last_check": None,
                "last_detail": "", "total_changes": 0}
