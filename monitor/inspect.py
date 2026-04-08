"""
Inspection tools for verifying what the monitor
has captured for each site.
"""

import sqlite3
from datetime import datetime

DB = "data/monitor.db"


def get_baseline_summary(site_name: str) -> dict:
    """
    Returns a summary of the saved baseline for a site.
    """
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    snapshots = conn.execute("""
        SELECT url, content, checksum, timestamp
        FROM snapshots
        WHERE site_name = ?
        ORDER BY timestamp DESC
    """, (site_name,)).fetchall()

    if not snapshots:
        conn.close()
        return {"error": f"No baseline found for {site_name}"}

    # Get unique URLs with their latest snapshot
    seen_urls = {}
    for snap in snapshots:
        url = snap["url"]
        if url not in seen_urls:
            seen_urls[url] = snap

    pages = []
    for url, snap in seen_urls.items():
        content = snap["content"] or ""
        word_count = len(content.split())
        preview = " ".join(content.split()[:30])

        pages.append({
            "url": url,
            "word_count": word_count,
            "preview": preview,
            "timestamp": snap["timestamp"][:16],
            "checksum": snap["checksum"][:8],
            "healthy": word_count > 50,
        })

    conn.close()

    total_words = sum(p["word_count"] for p in pages)
    healthy_pages = sum(1 for p in pages if p["healthy"])

    return {
        "site_name": site_name,
        "total_pages": len(pages),
        "total_words": total_words,
        "healthy_pages": healthy_pages,
        "pages": pages,
    }


def get_all_baselines_summary() -> list:
    """
    Returns a health summary for all monitored sites.
    """
    conn = sqlite3.connect(DB)
    sites = conn.execute(
        "SELECT DISTINCT site_name FROM snapshots"
    ).fetchall()
    conn.close()

    results = []
    for site in sites:
        summary = get_baseline_summary(site[0])
        if "error" not in summary:
            results.append(summary)

    return results
