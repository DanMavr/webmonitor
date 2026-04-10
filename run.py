"""
Usage:
  python run.py start    ← Start monitoring + dashboard
  python run.py check    ← Run one immediate check
  python run.py status   ← Show current status table
"""
import os
import re
import sys
import asyncio
import logging
import threading
import warnings
import urllib3
from datetime import datetime, timezone

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich import box
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from monitor.storage import init_db, log_job
from monitor.core import check_site
from dashboard.app import start_dashboard

load_dotenv()
console = Console()

# ── Logging setup ────────────────────────────────────────────────────────────

class ActivityFileHandler(logging.Handler):
    """
    Writes INFO+ monitor activity to data/activity.log.
    Strips ANSI colour codes that Rich injects into log messages.
    """
    _ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            msg = self._ANSI_RE.sub("", msg)
            with open("data/activity.log", "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass   # never let a logging failure crash the app


# Root logger — WARNING+ errors to monitor.log
logging.basicConfig(
    level=logging.WARNING,
    handlers=[logging.FileHandler("data/monitor.log")]
)

# "monitor" logger — INFO+ activity to activity.log
_activity_handler = ActivityFileHandler()
_activity_handler.setLevel(logging.INFO)
_activity_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(message)s")
)

monitor_logger = logging.getLogger("monitor")
monitor_logger.setLevel(logging.INFO)
monitor_logger.addHandler(_activity_handler)
monitor_logger.propagate = False   # don't double-log to root WARNING handler

# Module-level logger for run.py itself
logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_sites() -> list:
    with open("config/sites.yaml") as f:
        return yaml.safe_load(f)["sites"]


def _get_effective_interval(site: dict) -> int:
    """
    Return the effective minimum check interval in minutes for a site.
    For time_window sites, reads the smallest window interval.
    For interval sites, reads interval_minutes directly.
    """
    schedule_type = site.get("schedule_type", "interval")

    if schedule_type == "time_window":
        windows = site.get("windows", [])
        if windows:
            return min(w.get("interval_minutes", 60) for w in windows)
        return 60

    return site.get("interval_minutes", 60)


# ── Watchdog ──────────────────────────────────────────────────────────────────

async def watchdog_check(sites: list):
    """
    Runs every hour. Alerts via Telegram if any site has not been checked
    within 3× its expected interval.
    """
    import sqlite3
    from monitor.scheduler import is_in_window

    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    with sqlite3.connect("data/monitor.db") as conn:
        conn.row_factory = sqlite3.Row

        for site in sites:
            name          = site["name"]
            schedule_type = site.get("schedule_type", "interval")

            # Don't alert for time-window sites that are outside their window
            if schedule_type == "time_window":
                in_window, _ = is_in_window(site)
                if not in_window:
                    continue

            expected_interval = _get_effective_interval(site)
            max_silence       = expected_interval * 3

            row = conn.execute(
                """
                SELECT timestamp FROM job_log
                WHERE site_name = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (name,)
            ).fetchone()

            if row is None:
                continue   # site hasn't run yet — not a watchdog concern

            # Parse stored UTC timestamp and keep it timezone-aware
            last_time = datetime.strptime(
                row["timestamp"][:19], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)

            silence_minutes = (
                datetime.now(timezone.utc) - last_time
            ).total_seconds() / 60

            if silence_minutes > max_silence:
                msg = (
                    f"WATCHDOG ALERT\n\n"
                    f"Site: {name}\n"
                    f"Silent for: {int(silence_minutes)} minutes\n"
                    f"Expected every: {expected_interval} minutes\n\n"
                    f"The monitor may have stopped working for this site."
                )
                console.log(f"[red]WATCHDOG: {name} silent {int(silence_minutes)}min[/red]")
                logger.warning(f"Watchdog: {name} silent {int(silence_minutes)}min")

                if token and chat_id:
                    import telegram
                    bot = telegram.Bot(token=token)
                    try:
                        await bot.send_message(chat_id=chat_id, text=msg)
                    except Exception as e:
                        logger.error(f"Watchdog Telegram alert failed: {e}")


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start():
    init_db()
    sites = load_sites()

    # Flask dashboard in background thread
    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()

    # Telegram bot in background thread
    try:
        from monitor.bot import start_bot
        bot_thread = threading.Thread(target=start_bot, daemon=True)
        bot_thread.start()
        console.print("Telegram bot started")
    except Exception as e:
        console.print(f"Bot not started: {e}")

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
    console.print("\nDashboard at http://localhost:5000\n")

    scheduler = AsyncIOScheduler()

    for site in sites:
        interval = _get_effective_interval(site)
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

    # Watchdog runs every hour
    scheduler.add_job(
        watchdog_check,
        "interval",
        hours=1,
        args=[sites],
        id="watchdog",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True
    )

    scheduler.start()
    console.print("Running. Press Ctrl+C to stop.\n")

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
        await check_site(site, force=True)
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
    table.add_column("Site",        style="cyan", min_width=25)
    table.add_column("Last Check",  justify="center")
    table.add_column("Changes",     justify="center")
    table.add_column("Status",      justify="center")

    for site in sites:
        name = site["name"]

        last_check = conn.execute(
            """
            SELECT timestamp FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (name,)
        ).fetchone()

        total = conn.execute(
            "SELECT COUNT(*) FROM changes WHERE site_name = ?",
            (name,)
        ).fetchone()[0]

        last_status = conn.execute(
            """
            SELECT status FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
            """,
            (name,)
        ).fetchone()

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


# ── Entry point ───────────────────────────────────────────────────────────────

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
        console.print("Usage: python run.py [start|check|status]")
