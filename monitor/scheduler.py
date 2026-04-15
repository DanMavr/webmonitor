"""
scheduler.py — APScheduler setup.

schedule_type: interval    — check every N minutes
schedule_type: time_window — check every N minutes only within defined windows
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR

from monitor.core import check_site

logger = logging.getLogger("monitor")


def _parse_days(days_str: str) -> list:
    day_order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    if "-" in days_str:
        start, end = days_str.lower().split("-")
        s, e = day_order.index(start), day_order.index(end)
        return day_order[s : e + 1]
    return [days_str.lower()]


def _in_window(windows: list, tz_name: str) -> bool:
    tz      = ZoneInfo(tz_name)
    now     = datetime.now(tz)
    day_abbr = now.strftime("%a").lower()
    now_time = now.strftime("%H:%M")
    for w in windows:
        if day_abbr in _parse_days(w.get("days", "mon-fri")):
            if w.get("from", "00:00") <= now_time <= w.get("to", "23:59"):
                return True
    return False


def _make_job(site: dict):
    async def job():
        try:
            await check_site(site)
        except Exception as e:
            logger.error(f"Unhandled error in job [{site['name']}]: {e}", exc_info=True)

    if site.get("schedule_type") == "time_window":
        windows  = site.get("windows", [])
        tz_name  = site.get("timezone", "UTC")
        min_interval = min(w.get("interval_minutes", 1) for w in windows) if windows else 1

        async def windowed_job():
            if _in_window(windows, tz_name):
                await job()
            else:
                logger.info(f"Outside window: {site['name']} — skipping")

        return windowed_job, min_interval

    return job, site.get("interval_minutes", 60)


def build_scheduler(sites: list) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    def on_error(event):
        logger.error(f"Scheduler job error: {event.exception}")

    scheduler.add_listener(on_error, EVENT_JOB_ERROR)

    for site in sites:
        job_fn, interval_min = _make_job(site)
        scheduler.add_job(
            job_fn,
            "interval",
            minutes=interval_min,
            id=site["name"],
            max_instances=1,
            coalesce=True,
        )
        logger.info(f"Scheduled [{site['name']}] every {interval_min} min")

    return scheduler


def trigger_immediate(scheduler: AsyncIOScheduler, site: dict):
    """Fire a site check immediately — called from Flask route (sync context)."""
    job_fn, _ = _make_job(site)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(job_fn())
    except RuntimeError:
        # No running loop in this thread — run synchronously
        asyncio.run(job_fn())
