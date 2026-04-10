import sqlite3
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
DB = "data/monitor.db"

# How many historical snapshots to keep per URL (older ones are pruned)
SNAPSHOT_KEEP = 5


def _col_names(conn, table: str) -> list[str]:
    """Return the list of column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _migrate_if_needed(conn):
    """
    Detect old schema (from early versions that used id + screenshot columns)
    and migrate to the current schema in-place, preserving all data.
    Called once inside init_db() after tables are created.
    """
    cols = _col_names(conn, "snapshots")

    # Old schema had 'id' and 'screenshot' columns — current schema has neither
    if "id" not in cols and "screenshot" not in cols:
        return  # already current, nothing to do

    logger.warning("Old DB schema detected — running migration to current schema")

    # ── snapshots ────────────────────────────────────────────────────────────
    if "id" in _col_names(conn, "snapshots") or "screenshot" in _col_names(conn, "snapshots"):
        conn.execute("ALTER TABLE snapshots RENAME TO snapshots_old")
        conn.execute("""
            CREATE TABLE snapshots (
                site_name TEXT,
                url       TEXT,
                content   TEXT,
                checksum  TEXT,
                timestamp TEXT
            )
        """)
        conn.execute("""
            INSERT INTO snapshots (site_name, url, content, checksum, timestamp)
            SELECT site_name, url, content, checksum, timestamp
            FROM snapshots_old
        """)
        conn.execute("DROP TABLE snapshots_old")
        logger.warning("Migrated snapshots table")

    # ── changes ──────────────────────────────────────────────────────────────
    if "id" in _col_names(conn, "changes") or "diff_screenshot" in _col_names(conn, "changes"):
        conn.execute("ALTER TABLE changes RENAME TO changes_old")
        conn.execute("""
            CREATE TABLE changes (
                site_name    TEXT,
                url          TEXT,
                old_checksum TEXT,
                new_checksum TEXT,
                change_pct   TEXT,
                diff_text    TEXT,
                timestamp    TEXT
            )
        """)
        conn.execute("""
            INSERT INTO changes (site_name, url, old_checksum, new_checksum,
                                 change_pct, diff_text, timestamp)
            SELECT site_name, url, old_checksum, new_checksum,
                   change_pct, diff_text, timestamp
            FROM changes_old
        """)
        conn.execute("DROP TABLE changes_old")
        logger.warning("Migrated changes table")

    # ── job_log ───────────────────────────────────────────────────────────────
    if "id" in _col_names(conn, "job_log"):
        conn.execute("ALTER TABLE job_log RENAME TO job_log_old")
        conn.execute("""
            CREATE TABLE job_log (
                site_name TEXT,
                status    TEXT,
                message   TEXT,
                timestamp TEXT
            )
        """)
        conn.execute("""
            INSERT INTO job_log (site_name, status, message, timestamp)
            SELECT site_name, status, message, timestamp
            FROM job_log_old
        """)
        conn.execute("DROP TABLE job_log_old")
        logger.warning("Migrated job_log table")

    conn.commit()
    logger.warning("DB migration complete")


def init_db():
    with sqlite3.connect(DB) as conn:
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                site_name TEXT,
                url       TEXT,
                content   TEXT,
                checksum  TEXT,
                timestamp TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS changes (
                site_name   TEXT,
                url         TEXT,
                old_checksum TEXT,
                new_checksum TEXT,
                change_pct  TEXT,
                diff_text   TEXT,
                timestamp   TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS job_log (
                site_name TEXT,
                status    TEXT,
                message   TEXT,
                timestamp TEXT
            )
        """)

        # Indexes — critical for performance as tables grow
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_url
            ON snapshots(url, timestamp)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_site
            ON snapshots(site_name, timestamp)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_changes_site
            ON changes(site_name, timestamp)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_job_log_site
            ON job_log(site_name, timestamp)
        """)

        conn.commit()

        # Migrate old schema if present (idempotent — safe to run every startup)
        _migrate_if_needed(conn)


def get_last_snapshot(url: str) -> dict | None:
    with sqlite3.connect(DB) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT * FROM snapshots
            WHERE url = ?
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (url,)
        ).fetchone()
    return dict(row) if row else None


def save_snapshot(site_name: str, url: str, content: str, checksum: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB) as conn:
        # Insert new snapshot
        conn.execute(
            "INSERT INTO snapshots VALUES (?, ?, ?, ?, ?)",
            (site_name, url, content, checksum, ts)
        )

        # Prune old snapshots — keep only the most recent SNAPSHOT_KEEP rows
        conn.execute(
            """
            DELETE FROM snapshots
            WHERE url = ?
              AND timestamp NOT IN (
                SELECT timestamp FROM snapshots
                WHERE url = ?
                ORDER BY timestamp DESC
                LIMIT ?
              )
            """,
            (url, url, SNAPSHOT_KEEP)
        )

        conn.commit()


def save_change(site_name: str, url: str, old_cs: str, new_cs: str,
                change_pct: float, diff_text: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO changes VALUES (?, ?, ?, ?, ?, ?, ?)",
            (site_name, url, old_cs, new_cs, str(change_pct), diff_text, ts)
        )
        conn.commit()


def log_job(site_name: str, status: str, message: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB) as conn:
        conn.execute(
            "INSERT INTO job_log VALUES (?, ?, ?, ?)",
            (site_name, status, message, ts)
        )
        conn.commit()
