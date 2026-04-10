import logging
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DAY_MAP = {
    "mon": 0, "tue": 1, "wed": 2,
    "thu": 3, "fri": 4, "sat": 5, "sun": 6
}


def parse_day_range(day_str: str) -> list[int]:
    """
    Parse a day range string into a list of weekday integers (0=Mon, 6=Sun).

    Examples:
        "mon-fri"  → [0, 1, 2, 3, 4]
        "sat-sun"  → [5, 6]
        "mon"      → [0]

    Raises ValueError for unrecognised day names or wrap-around ranges
    (e.g. "fri-mon") which are ambiguous and likely a config mistake.
    """
    day_str = day_str.lower().strip()

    if "-" in day_str:
        parts = day_str.split("-", 1)
        start_str = parts[0].strip()
        end_str   = parts[1].strip()

        if start_str not in DAY_MAP:
            raise ValueError(f"Unrecognised day name: '{start_str}'")
        if end_str not in DAY_MAP:
            raise ValueError(f"Unrecognised day name: '{end_str}'")

        start_int = DAY_MAP[start_str]
        end_int   = DAY_MAP[end_str]

        if start_int > end_int:
            raise ValueError(
                f"Wrap-around day range '{day_str}' is not supported. "
                f"Split into two separate windows instead."
            )

        return list(range(start_int, end_int + 1))

    if day_str not in DAY_MAP:
        raise ValueError(f"Unrecognised day name: '{day_str}'")

    return [DAY_MAP[day_str]]


def parse_time(t: str) -> tuple[int, int]:
    """
    Parse 'HH:MM' into (hour, minute).
    Raises ValueError on bad format.
    """
    try:
        h, m = t.split(":")
        return int(h), int(m)
    except Exception:
        raise ValueError(f"Cannot parse time string: '{t}' — expected HH:MM")


def is_in_window(site: dict) -> tuple[bool, str]:
    """
    Check whether the current time falls inside any of the site's
    scheduled windows.

    Returns:
        (True,  reason_string)  — inside a window, should run
        (False, reason_string)  — outside all windows, should skip
    """
    windows  = site.get("windows", [])
    tz_name  = site.get("timezone", "UTC")

    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.warning(
            f"Unknown timezone '{tz_name}' for {site.get('name', '?')}, "
            f"falling back to UTC"
        )
        tz = ZoneInfo("UTC")

    now              = datetime.now(tz)
    current_weekday  = now.weekday()
    current_time     = now.time()

    for window in windows:
        days_str = window.get("days", "mon-fri")
        from_str = window.get("from", "00:00")
        to_str   = window.get("to",   "23:59")

        try:
            allowed_days       = parse_day_range(days_str)
            from_h, from_m     = parse_time(from_str)
            to_h,   to_m       = parse_time(to_str)
        except ValueError as e:
            logger.error(
                f"Bad window config for {site.get('name', '?')}: {e}"
            )
            continue

        from_time = dt_time(from_h, from_m)
        to_time   = dt_time(to_h,   to_m)

        if current_weekday in allowed_days and from_time <= current_time <= to_time:
            return True, f"in window: {days_str} {from_str}–{to_str}"

    return False, "outside all windows"


def get_next_window_info(site: dict) -> str:
    """
    Return a human-readable schedule summary string for the startup table.

    Examples:
        "every 30min"
        "mon-fri 07:10-17:00 every 5min | sat-sun 09:00-13:00 every 30min"
    """
    schedule_type = site.get("schedule_type", "interval")

    if schedule_type == "interval":
        interval = site.get("interval_minutes", 60)
        return f"every {interval}min"

    windows = site.get("windows", [])
    if not windows:
        return "time_window (no windows defined)"

    parts = []
    for w in windows:
        days = w.get("days", "?")
        frm  = w.get("from", "?")
        to   = w.get("to",   "?")
        mins = w.get("interval_minutes", "?")
        parts.append(f"{days} {frm}–{to} every {mins}min")

    return " | ".join(parts)
