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
from apscheduler.events import EVENT_JOB_ERROR

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

# Shared scheduler reference — lets dashboard routes trigger immediate checks
_scheduler: "AsyncIOScheduler | None" = None


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

# ── Commands ──────────────────────────────────────────────────────────────────


async def config_watcher():
    """
    Runs every 30 seconds. Syncs APScheduler jobs with the current sites.yaml.

      - New site added via UI  → register job + run baseline immediately
      - Site deleted via UI    → remove job
      - Interval changed       → reschedule job
      - Site renamed           → old name disappears, new name appears automatically
    """
    global _scheduler
    if _scheduler is None:
        return

    try:
        current_sites = load_sites()
    except Exception as e:
        logger.warning(f"config_watcher: failed to read sites.yaml: {e}")
        return

    current_names   = {s["name"] for s in current_sites}
    scheduled_names = {
        j.id for j in _scheduler.get_jobs()
        if j.id != "config_watcher"
        and not j.id.startswith("__immediate_")
    }

    # ── Register newly added sites ──────────────────────────────────────────
    for site in current_sites:
        name = site["name"]
        if name not in scheduled_names:
            interval = _get_effective_interval(site)
            _scheduler.add_job(
                check_site,
                "interval",
                minutes=interval,
                args=[site],
                id=name,
                next_run_time=datetime.now(timezone.utc),   # run immediately
                max_instances=1,
                coalesce=True,
            )
            logger.info(
                f"config_watcher: registered new site '{name}' "
                f"(every {interval}min)"
            )

    # ── Remove jobs for deleted sites ───────────────────────────────────────
    for name in scheduled_names:
        if name not in current_names:
            try:
                _scheduler.remove_job(name)
                logger.info(f"config_watcher: removed deleted site '{name}'")
            except Exception:
                pass

    # ── Reschedule if interval changed ──────────────────────────────────────
    for site in current_sites:
        name = site["name"]
        if name in scheduled_names:
            new_interval = _get_effective_interval(site)
            job = _scheduler.get_job(name)
            if job:
                try:
                    current_secs = int(job.trigger.interval.total_seconds())
                    if current_secs != new_interval * 60:
                        _scheduler.reschedule_job(
                            name,
                            trigger="interval",
                            minutes=new_interval,
                        )
                        logger.info(
                            f"config_watcher: rescheduled '{name}' to "
                            f"every {new_interval}min"
                        )
                except Exception:
                    pass  # time_window sites use different trigger type



def _job_error_listener(event):
    """
    Fired by APScheduler whenever a scheduled job raises an unhandled exception.
    Writes to the monitor logger so it appears in activity.log,
    and updates job_log so the dashboard shows an error status immediately.
    """
    from monitor.storage import log_job
    job_id = event.job_id
    exc    = event.exception
    logger.error(f"APScheduler job '{job_id}' crashed: {exc}", exc_info=False)
    try:
        log_job(job_id, "error", f"Scheduler exception: {exc}")
    except Exception:
        pass  # never let logging failures cascade


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

    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_listener(_job_error_listener, EVENT_JOB_ERROR)

    for site in sites:
        interval = _get_effective_interval(site)
        _scheduler.add_job(
            check_site,
            "interval",
            minutes=interval,
            args=[site],
            id=site["name"],
            next_run_time=datetime.now(timezone.utc),
            max_instances=1,
            coalesce=True
        )

    # Config watcher — syncs jobs with sites.yaml every 30 seconds
    _scheduler.add_job(
        config_watcher,
        "interval",
        seconds=30,
        id="config_watcher",
        max_instances=1,
        coalesce=True
    )

    # Daily snapshot pruning — keeps DB small on Pi SD card
    from monitor.storage import prune_snapshots
    _scheduler.add_job(
        prune_snapshots,
        "interval",
        hours=24,
        id="prune_snapshots",
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    console.print("Running. Press Ctrl+C to stop.\n")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        _scheduler.shutdown()
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
