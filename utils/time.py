from datetime import datetime
from zoneinfo import ZoneInfo


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