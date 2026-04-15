"""
scheduler.py — APScheduler (BackgroundScheduler, thread-based).

schedule_type: interval    — check every N minutes
schedule_type: time_window — check every N minutes only within defined windows
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_JOB_ERROR

from monitor.core import check_site

logger = logging.getLogger("monitor")


def _parse_days(s: str) -> list:
    order = ["mon","tue","wed","thu","fri","sat","sun"]
    if "-" in s:
        a, b = s.lower().split("-")
        return order[order.index(a): order.index(b)+1]
    return [s.lower()]


def _in_window(windows: list, tz_name: str) -> bool:
    now = datetime.now(ZoneInfo(tz_name))
    day = now.strftime("%a").lower()
    t   = now.strftime("%H:%M")
    for w in windows:
        if day in _parse_days(w.get("days","mon-fri")):
            if w.get("from","00:00") <= t <= w.get("to","23:59"):
                return True
    return False


def _make_job(site: dict):
    if site.get("schedule_type") == "time_window":
        windows  = site.get("windows", [])
        tz_name  = site.get("timezone", "UTC")
        interval = min((w.get("interval_minutes", 1) for w in windows), default=1)

        def job():
            if _in_window(windows, tz_name):
                check_site(site)
            else:
                logger.info(f"Outside window: {site['name']} — skipping")
        return job, interval

    def job():
        check_site(site)
    return job, site.get("interval_minutes", 60)


def build_scheduler(sites: list) -> BackgroundScheduler:
    scheduler = BackgroundScheduler()
    scheduler.add_listener(
        lambda e: logger.error(f"Job error: {e.exception}"),
        EVENT_JOB_ERROR
    )
    for site in sites:
        job_fn, interval_min = _make_job(site)
        scheduler.add_job(
            job_fn, "interval",
            minutes=interval_min,
            id=site["name"],
            max_instances=1,
            coalesce=True,
        )
        logger.info(f"Scheduled [{site['name']}] every {interval_min} min")
    return scheduler


def trigger_immediate(scheduler: BackgroundScheduler, site: dict):
    """Run a site check immediately in the scheduler's thread pool."""
    job_fn, _ = _make_job(site)
    scheduler.add_job(job_fn, id=f"_immediate_{site['name']}", replace_existing=True)
