from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional


def zoneinfo_or_utc(tz_name: str | None) -> ZoneInfo:
    """Resolve a Slack/IANA timezone name. On Windows, install `tzdata` for names like America/New_York."""
    if not tz_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def is_valid_deadline(deadline_str: str) -> bool:
    """Return True if ``deadline_str`` is non-empty and parses as ISO 8601 via ``datetime.fromisoformat``."""
    if not deadline_str:
        return False

    try:
        datetime.fromisoformat(deadline_str)
        return True
    except Exception:
        return False


def localize_naive_deadline(deadline_str: str, timezone: str) -> str:
    """
    If deadline_str has no timezone offset (Gemini omitted it), attach the user's
    timezone so downstream code doesn't silently treat it as UTC.
    Returns the original string unchanged if it already has offset info or can't be parsed.
    """
    if not deadline_str:
        return deadline_str
    try:
        dt = datetime.fromisoformat(deadline_str)
        if dt.tzinfo is None:
            tz = zoneinfo_or_utc(timezone)
            dt = dt.replace(tzinfo=tz)
            return dt.isoformat(timespec="seconds")
    except Exception:
        pass
    return deadline_str