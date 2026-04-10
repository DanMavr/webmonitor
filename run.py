"""
Usage:
  python run.py start    ← Start monitoring + dashboard
  python run.py check    ← Run one immediate check
  python run.py status   ← Show current status table
"""
import os
from dotenv import load_dotenv
import sys
import asyncio
import threading
import yaml
import logging
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from monitor.storage import init_db, log_job
from monitor.core import check_site
from dashboard.app import start_dashboard

load_dotenv()
console = Console()


class ActivityFileHandler(logging.FileHandler):
    """
    Writes INFO+ log records to data/activity.log with timestamps.
    Each line: 2026-04-10 09:14:42 [LEVEL] message
    """
    def emit(self, record):
        record.msg = self._strip_ansi(str(record.msg))
        super().emit(record)

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re
        text = re.sub(r'\x1b\[[0-9;]*m', '', text)
        text = re.sub(r'\[[a-z/ _]+\]', '', text)
        return text.strip()


# Error/warning log (existing)
logging.basicConfig(
    level=logging.WARNING,
    handlers=[logging.FileHandler("data/monitor.log")]
)

# Activity log — captures INFO and above from the monitor logger
activity_logger = logging.getLogger("monitor")
activity_logger.setLevel(logging.INFO)

activity_handler = ActivityFileHandler("data/activity.log")
activity_handler.setLevel(logging.INFO)
activity_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                      datefmt="%Y-%m-%d %H:%M:%S")
)
activity_logger.addHandler(activity_handler)


def load_sites() -> list:
    with open("config/sites.yaml") as f:
        return yaml.safe_load(f)["sites"]


async def watchdog_check(sites: list):
    """
    Runs every hour. Checks if any site has gone silent
    and alerts via Telegram if a site has not been checked
    within 3x its expected interval.
    """
    import sqlite3
    from datetime import datetime, timezone, timedelta
    from monitor.scheduler import is_in_window

    conn = sqlite3.connect("data/monitor.db")
    conn.row_factory = sqlite3.Row

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    for site in sites:
        name = site["name"]
        schedule_type = site.get("schedule_type", "interval")

        if schedule_type == "time_window":
            in_window, _ = is_in_window(site)
            if not in_window:
                continue

        expected_interval = site.get("interval_minutes", 60)
        max_silence = expected_interval * 3

        last_check = conn.execute("""
            SELECT timestamp FROM job_log
            WHERE site_name = ?
            ORDER BY timestamp DESC LIMIT 1
        """, (name,)).fetchone()

        if last_check is None:
            continue

        last_time = datetime.strptime(
            last_check[0][:19], "%Y-%m-%d %H:%M:%S"
        )
        silence_minutes = (
            datetime.now(timezone.utc).replace(tzinfo=None) - last_time
        ).total_seconds() / 60

        if silence_minutes > max_silence:
            msg = (
                f"WATCHDOG: {name} has not been checked "
                f"for {int(silence_minutes)} minutes "
                f"(expected every {expected_interval} min)"
            )
            console.log(f"[red]{msg}[/red]")
            activity_logger.warning(msg)

            if token and chat_id:
                import telegram
                bot = telegram.Bot(token=token)
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"WATCHDOG ALERT\n\n"
                            f"Site: {name}\n"
                            f"Has not been checked for "
                            f"{int(silence_minutes)} minutes\n"
                            f"Expected every {expected_interval} minutes\n\n"
                            f"The monitor may have stopped working for this site."
                        )
                    )
                except Exception as e:
                    console.log(f"[red]Watchdog alert failed: {e}[/red]")

    conn.close()


async def cmd_start():
    init_db()
    sites = load_sites()

    t = threading.Thread(target=start_dashboard, daemon=True)
    t.start()

    try:
        from monitor.bot import start_bot
        bot_thread = threading.Thread(target=start_bot, daemon=True)
        bot_thread.start()
        console.print("Telegram bot started")
        activity_logger.info("Telegram bot started")
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

    activity_logger.info(f"WebMonitor started — {len(sites)} sites loaded")
    console.print("\nDashboard at http://localhost:5000\n")

    scheduler = AsyncIOScheduler()

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
        activity_logger.info("WebMonitor stopped")
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

        status = "–"
        if last_status:
            status = "✓ OK" if last_status[0] == "success" else "✗ Error"

        table.add_row(
            name,
            last_check[0][:16] if last_check else "Never",
            str(total),
            status
        )

    console.print(table)
    conn.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"

    if cmd == "start":
        asyncio.run(cmd_start())
    elif cmd == "check":
        asyncio.run(cmd_check())
    elif cmd == "status":
        cmd_status()
    else:
        console.print(f"[red]Unknown command: {cmd}[/red]")
        sys.exit(1)
