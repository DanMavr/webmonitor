"""
storage.py — minimal two-file-per-site storage.

Layout:
  data/sites/<safe_name>/baseline.png        ← last known screenshot crop
  data/sites/<safe_name>/baseline_text.txt   ← OCR text of that screenshot
  data/changes.db                            ← SQLite change + job history
  data/activity.log
  data/monitor.log
"""
from __future__ import annotations

import sqlite3
import re
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("monitor")

DB        = "data/changes.db"
SITES_DIR = Path("data/sites")


# ── Initialisation ────────────────────────────────────────────────────────────

def init_db():
    Path("data").mkdir(exist_ok=True)
    SITES_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS changes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name   TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            added       TEXT,
            removed     TEXT,
            summary     TEXT
        );
        CREATE TABLE IF NOT EXISTS job_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name   TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            status      TEXT NOT NULL,
            detail      TEXT
        );
    """)
    conn.commit()
    conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower())


def _site_dir(name: str) -> Path:
    d = SITES_DIR / _safe_name(name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Baseline read/write ───────────────────────────────────────────────────────

def load_baseline(site_name: str) -> tuple:
    """Return (png_bytes, text) for the stored baseline, or (None, "")."""
    d         = _site_dir(site_name)
    png_path  = d / "baseline.png"
    text_path = d / "baseline_text.txt"
    png  = png_path.read_bytes()  if png_path.exists()  else None
    text = text_path.read_text()  if text_path.exists() else ""
    return png, text


def save_baseline(site_name: str, png_bytes: bytes, text: str):
    """Overwrite the baseline with the new screenshot and OCR text."""
    d = _site_dir(site_name)
    if png_bytes:
        (d / "baseline.png").write_bytes(png_bytes)
    (d / "baseline_text.txt").write_text(text)


def baseline_exists(site_name: str) -> bool:
    return (_site_dir(site_name) / "baseline.png").exists()


def get_baseline_png_path(site_name: str) -> Optional[Path]:
    p = _site_dir(site_name) / "baseline.png"
    return p if p.exists() else None


# ── Job log ───────────────────────────────────────────────────────────────────

def log_job(site_name: str, status: str, detail: str = ""):
    try:
        conn = sqlite3.connect(DB)
        conn.execute(
            "INSERT INTO job_log (site_name, timestamp, status, detail) VALUES (?,?,?,?)",
            (site_name, _now(), status, detail),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"log_job failed: {e}")


# ── Change history ────────────────────────────────────────────────────────────

def log_change(site_name: str, diff: dict):
    try:
        conn = sqlite3.connect(DB)
        conn.execute(
            "INSERT INTO changes (site_name, timestamp, added, removed, summary) VALUES (?,?,?,?,?)",
            (
                site_name,
                _now(),
                "\n".join(diff["added"]),
                "\n".join(diff["removed"]),
                diff["summary"],
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"log_change failed: {e}")


def get_recent_changes(limit: int = 100) -> list:
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM changes ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_site_stats(site_name: str) -> dict:
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row

        last_check = conn.execute(
            "SELECT timestamp, status FROM job_log WHERE site_name=? ORDER BY timestamp DESC LIMIT 1",
            (site_name,),
        ).fetchone()

        total_changes = conn.execute(
            "SELECT COUNT(*) FROM changes WHERE site_name=?", (site_name,)
        ).fetchone()[0]

        conn.close()
        return {
            "last_check":    last_check["timestamp"] if last_check else None,
            "last_status":   last_check["status"]    if last_check else None,
            "total_changes": total_changes,
        }
    except Exception:
        return {"last_check": None, "last_status": None, "total_changes": 0}
