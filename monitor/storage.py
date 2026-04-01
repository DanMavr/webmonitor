import sqlite3
from pathlib import Path

DB_PATH = "data/monitor.db"


def init_db():
    Path("data/snapshots").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name     TEXT NOT NULL,
            url           TEXT NOT NULL,
            content       TEXT,
            screenshot    TEXT,
            checksum      TEXT NOT NULL,
            timestamp     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS changes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name       TEXT NOT NULL,
            url             TEXT NOT NULL,
            old_checksum    TEXT,
            new_checksum    TEXT,
            change_pct      TEXT,
            diff_text       TEXT,
            diff_screenshot TEXT,
            timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS job_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name   TEXT NOT NULL,
            status      TEXT,
            message     TEXT,
            timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


def get_last_snapshot(site_name: str, url: str) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT * FROM snapshots
        WHERE site_name = ? AND url = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (site_name, url))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def save_snapshot(site_name: str, url: str, content: str,
                  checksum: str, screenshot: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO snapshots (site_name, url, content, screenshot, checksum)
        VALUES (?, ?, ?, ?, ?)
    """, (site_name, url, content, screenshot, checksum))
    conn.commit()
    conn.close()


def save_change(site_name: str, url: str, old_checksum: str,
                new_checksum: str, change_pct: str,
                diff_text: str = "", diff_screenshot: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO changes
        (site_name, url, old_checksum, new_checksum, change_pct,
         diff_text, diff_screenshot)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (site_name, url, old_checksum, new_checksum, change_pct,
          diff_text, diff_screenshot))
    conn.commit()
    conn.close()


def log_job(site_name: str, status: str, message: str = ""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO job_log (site_name, status, message) VALUES (?, ?, ?)",
        (site_name, status, message)
    )
    conn.commit()
    conn.close()
