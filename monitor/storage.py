"""
All SQLite storage operations for WebMonitor.
Includes baseline inspection (previously inspect.py).

All timestamps stored as UTC strings: "%Y-%m-%d %H:%M:%S"
"""

import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger("monitor")

DB = "data/monitor.db"

PRUNE_KEEP = 3   # snapshots to keep per URL


# ── Schema ────────────────────────────────────────────────────────────────────

def _col_names(conn, table):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _migrate_if_needed(conn):
    cols = _col_names(conn, "snapshots")
    if "site_name" not in cols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN site_name TEXT")
    if "word_count" not in cols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN word_count INTEGER DEFAULT 0")
    cols_changes = _col_names(conn, "changes")
    if "site_name" not in cols_changes:
        conn.execute("ALTER TABLE changes ADD COLUMN site_name TEXT")


def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                site_name TEXT,
                url       TEXT,
                content   TEXT,
                checksum  TEXT,
                timestamp TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS changes (
                site_name    TEXT,
                url          TEXT,
                old_checksum TEXT,
                new_checksum TEXT,
                change_pct   TEXT,
                diff_text    TEXT,
                timestamp    TEXT
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_log (
                site_name TEXT,
                status    TEXT,
                message   TEXT,
                timestamp TEXT
            )""")
        # Indexes for query performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_url_ts  ON snapshots (url, timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snap_site    ON snapshots (site_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_site ON changes   (site_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_joblog_site  ON job_log   (site_name)")
        _migrate_if_needed(conn)
        conn.commit()


# ── Snapshots ─────────────────────────────────────────────────────────────────

def get_last_snapshot(url: str) -> dict | None:
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM snapshots WHERE url = ? ORDER BY timestamp DESC LIMIT 1",
            (url,)
        ).fetchone()
    return dict(row) if row else None


def save_snapshot(site_name: str, url: str, content: str, checksum: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            (site_name, url, content, checksum, ts)
        )
        conn.commit()


# ── Changes ───────────────────────────────────────────────────────────────────

def save_change(site_name: str, url: str, old_checksum: str,
                new_checksum: str, change_pct: float, diff_text: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO changes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (site_name, url, old_checksum, new_checksum,
             str(change_pct), diff_text[:5000], ts)
        )
        conn.commit()


# ── Job log ───────────────────────────────────────────────────────────────────

def log_job(site_name: str, status: str, message: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO job_log VALUES (?, ?, ?, ?)",
            (site_name, status, message, ts)
        )
        conn.commit()


# ── Pruning ───────────────────────────────────────────────────────────────────

def prune_snapshots(keep: int = PRUNE_KEEP):
    """
    Keep only the N most recent snapshots per URL.
    Prevents unbounded growth on the Pi SD card.
    Run periodically (e.g. daily).
    """
    with sqlite3.connect(DB) as conn:
        urls = [r[0] for r in conn.execute(
            "SELECT DISTINCT url FROM snapshots"
        ).fetchall()]

        deleted_total = 0
        for url in urls:
            rows = conn.execute(
                "SELECT rowid FROM snapshots WHERE url = ? ORDER BY timestamp DESC",
                (url,)
            ).fetchall()
            to_delete = [r[0] for r in rows[keep:]]
            if to_delete:
                conn.execute(
                    f"DELETE FROM snapshots WHERE rowid IN ({','.join('?'*len(to_delete))})",
                    to_delete
                )
                deleted_total += len(to_delete)

        conn.commit()

    if deleted_total:
        logger.info(f"prune_snapshots: removed {deleted_total} old snapshots")


# ── Shared utilities ─────────────────────────────────────────────────────────

def flatten(obj, prefix=""):
    """
    Recursively flatten a nested dict/list into dot-notation keys.
    Used by both core.py (JSON diff) and app.py (JSON display).
    List expansion capped at 5 items to prevent huge outputs.
    """
    items = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                items.update(flatten(v, full_key))
            else:
                items[full_key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            full_key = f"{prefix}[{i}]"
            if isinstance(v, (dict, list)):
                items.update(flatten(v, full_key))
            else:
                items[full_key] = v
    return items


# ── Baseline inspection (merged from inspect.py) ──────────────────────────────

def get_baseline_summary(site_name: str) -> dict:
    """
    Returns snapshot health summary for one site.
    Used by dashboard /inspect route.
    """
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        snapshots = conn.execute("""
            SELECT url, content, checksum, timestamp
            FROM snapshots
            WHERE site_name = ?
            ORDER BY timestamp DESC
        """, (site_name,)).fetchall()

    if not snapshots:
        return {"error": f"No snapshots found for {site_name}"}

    # One entry per URL — latest only
    seen = {}
    for snap in snapshots:
        if snap["url"] not in seen:
            seen[snap["url"]] = snap

    pages = []
    for url, snap in seen.items():
        content    = snap["content"] or ""
        word_count = len(content.split())
        preview    = " ".join(content.split()[:30])

        if word_count >= 200:
            health = "OK"
        elif word_count >= 50:
            health = "Medium content"
        elif word_count >= 1:
            health = "Low content"
        else:
            health = "Empty"

        pages.append({
            "url":        url,
            "word_count": word_count,
            "preview":    preview,
            "timestamp":  snap["timestamp"][:16],
            "checksum":   snap["checksum"][:8] if snap["checksum"] else "",
            "health":     health,
            "healthy":    word_count >= 50,
        })

    return {
        "site_name":    site_name,
        "total_pages":  len(pages),
        "total_words":  sum(p["word_count"] for p in pages),
        "healthy_pages": sum(1 for p in pages if p["healthy"]),
        "pages":        pages,
    }


def get_all_baselines_summary() -> list:
    """Summary for all sites — used by dashboard main page."""
    with sqlite3.connect(DB) as conn:
        sites = [r[0] for r in conn.execute(
            "SELECT DISTINCT site_name FROM snapshots"
        ).fetchall()]

    return [
        s for s in (get_baseline_summary(name) for name in sites)
        if "error" not in s
    ]
