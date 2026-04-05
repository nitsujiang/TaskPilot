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
    """
    Validates an ISO 8601 string returned by Gemini.
    Returns True if it's a valid ISO 8601 string, False if it's an invalid format or empty
    """

    if not deadline_str:
        return False

    try:
        datetime.fromisoformat(deadline_str)
        return True
    except Exception:
        return False