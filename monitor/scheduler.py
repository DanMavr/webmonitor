"""
Handles both simple interval schedules and
time-window based schedules with timezone support.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DAYS = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def parse_day_range(day_str: str) -> list[int]:
    """
    Converts day string to list of weekday numbers.

    "mon-fri" -> [0, 1, 2, 3, 4]
    "mon"     -> [0]
    "sat-sun" -> [5, 6]
    """
    day_str = day_str.lower().strip()

    if "-" in day_str:
        start, end = day_str.split("-")
        start_num = DAYS[start.strip()]
        end_num = DAYS[end.strip()]
        return list(range(start_num, end_num + 1))
    else:
        return [DAYS[day_str]]


def parse_time(time_str: str) -> tuple[int, int]:
    """
    Converts "07:10" to (7, 10)
    """
    parts = time_str.strip().split(":")
    return int(parts[0]), int(parts[1])


def is_in_window(site: dict) -> tuple[bool, int]:
    """
    Checks if current time falls within any of the
    site's monitoring windows.

    Returns (should_check, interval_minutes)
    Returns (False, 0) if outside all windows.
    """
    schedule_type = site.get("schedule_type", "interval")

    # Simple interval sites always run
    if schedule_type == "interval":
        return True, site.get("interval_minutes", 60)

    # Time window sites
    windows = site.get("windows", [])
    if not windows:
        return False, 0

    tz_name = site.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.warning(f"Unknown timezone {tz_name}, using UTC")
        tz = ZoneInfo("UTC")

    now = datetime.now(tz)
    current_day = now.weekday()   # 0=Monday, 6=Sunday
    current_hour = now.hour
    current_minute = now.minute
    current_total_minutes = current_hour * 60 + current_minute

    for window in windows:
        # Check day
        allowed_days = parse_day_range(window.get("days", "mon-fri"))
        if current_day not in allowed_days:
            continue

        # Check time range
        from_h, from_m = parse_time(window.get("from", "00:00"))
        to_h, to_m = parse_time(window.get("to", "23:59"))

        from_total = from_h * 60 + from_m
        to_total = to_h * 60 + to_m

        if from_total <= current_total_minutes < to_total:
            return True, window.get("interval_minutes", 60)

    return False, 0


def get_next_window_info(site: dict) -> str:
    """
    Returns a human readable string of when the next
    monitoring window starts. Used in status display.
    """
    schedule_type = site.get("schedule_type", "interval")

    if schedule_type == "interval":
        return f"Every {site.get('interval_minutes', 60)} minutes"

    windows = site.get("windows", [])
    if not windows:
        return "No schedule configured"

    lines = []
    for w in windows:
        lines.append(
            f"{w.get('days', '?')} "
            f"{w.get('from', '?')}-{w.get('to', '?')} "
            f"every {w.get('interval_minutes', '?')}min"
        )

    return " | ".join(lines)
