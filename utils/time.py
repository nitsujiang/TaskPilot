from datetime import datetime

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