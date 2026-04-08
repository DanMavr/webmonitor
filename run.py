"""
Usage:
  python run.py start    ← Start monitoring + dashboard
  python run.py check    ← Run one immediate check
  python run.py status   ← Show current status table
"""

import sys
import asyncio
import threading
import yaml
import logging
from datetime import datetime

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from monitor.storage import init_db, log_job
from monitor.core import check_site
from dashboard.app import start_dashboard

console = Console()

logging.basicConfig(
    level=logging.WARNING,
    handlers=[logging.FileHandler("data/monitor.log")]
)


def load_sites() -> list:
    with open("config/sites.yaml") as f:
        return yaml.safe_load(f)["sites"]


async def cmd_start():
    init_db()
    sites = load_sites()

    # Start web dashboard in background
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()

    # Start Telegram bot commands in background
    from monitor.bot import start_bot
    bot_thread = threading.Thread(target=start_bot, daemon=True)
    bot_thread.start()

    # Show startup info using plain text only
    from monitor.scheduler import get_next_window_info

    console.print("\nWebMonitor Starting\n")
    console.print(f"{'Site':<35} {'Mode':<15} {'Schedule'}")
    console.print("-" * 80)

    for s in sites:
        console.print(
            f"{s['name']:<35} "
            f"{s.get('mode', 'single_page'):<15} "
            f"{get_next_window_info(s)}"
        )

    console.print(f"\nDashboard available at http://localhost:5000")

    # Set up scheduler with database persistence
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    jobstores = {
        "default": SQLAlchemyJobStore(url="sqlite:///data/scheduler.db")
    }
    scheduler = AsyncIOScheduler(jobstores=jobstores)
    scheduler.remove_all_jobs()

    for site in sites:
        schedule_type = site.get("schedule_type", "interval")

        if schedule_type == "interval":
            interval = site.get("interval_minutes", 60)
            scheduler.add_job(
                check_site,
                "interval",
                minutes=interval,
                args=[site],
                id=site["name"],
                next_run_time=datetime.now(),
                max_instances=1,
                coalesce=True
            )

        elif schedule_type == "time_window":
            windows = site.get("windows", [])
            if windows:
                min_interval = min(
                    w.get("interval_minutes", 60)
                    for w in windows
                )
            else:
                min_interval = 60

            scheduler.add_job(
                check_site,
                "interval",
                minutes=min_interval,
                args=[site],
                id=site["name"],
                next_run_time=datetime.now(),
                max_instances=1,
                coalesce=True
            )

    scheduler.start()
    console.print("\nRunning. Press Ctrl+C to stop.\n")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        console.print("\nStopped.")

async def cmd_check():
    init_db()
    sites = load_sites()
    console.print("[cyan]Running immediate check of all sites...[/cyan]\n")
    for site in sites:
        await check_site(site)
    console.print("\n[green]Done.[/green]")


def cmd_status():
    import sqlite3
    try:
        conn = sqlite3.connect("data/monitor.db")
    except Exception:
        console.print("[red]No database found. Run start first.[/red]")
        return

    sites = load_sites()

    table = Table(
        title="WebMonitor Status",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold"
    )
    table.add_column("Site", style="cyan", min_width=25)
    table.add_column("Last Check", justify="center")
    table.add_column("Changes", justify="center")
    table.add_column("Status", justify="center")

    for site in sites:
        name = site["name"]

        last_check = conn.execute("""
            SELECT timestamp FROM job_log
            WHERE site_name = ? ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        total = conn.execute(
            "SELECT COUNT(*) FROM changes WHERE site_name = ?", (name,)
        ).fetchone()[0]

        last_status = conn.execute("""
            SELECT status FROM job_log
            WHERE site_name = ? ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        if last_status and last_status[0] == "success":
            status_str = "[green]✓ OK[/green]"
        elif last_status:
            status_str = "[red]✗ Error[/red]"
        else:
            status_str = "[dim]Not run yet[/dim]"

        table.add_row(
            name,
            last_check[0] if last_check else "[dim]Never[/dim]",
            str(total),
            status_str
        )

    conn.close()
    console.print(table)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "start"

    if command == "start":
        asyncio.run(cmd_start())
    elif command == "check":
        asyncio.run(cmd_check())
    elif command == "status":
        cmd_status()
    else:
        console.print(f"[red]Unknown command:[/red] {command}")
        console.print(
            "Usage: python run.py [bold][start|check|status][/bold]"
        )
