from typing import Iterable
import requests
from config import API_ENDPOINT, BACKEND_API_KEY


def _backend_base() -> str:
    return (API_ENDPOINT or "").rstrip("/")


def _backend_headers() -> dict:
    return {"X-Backend-Key": BACKEND_API_KEY or ""}


def _is_connected_profile(email: str) -> bool:
    r = requests.get(
        f"{_backend_base()}/profiles/check",
        params={"profile": email},
        headers=_backend_headers(),
        timeout=15,
    )
    if r.status_code != 200:
        return False
    return bool((r.json() or {}).get("connected"))


def send_email(to, subject, body, profile: str | None = None):
    """Send email through the backend using the profile's stored OAuth creds.

    This avoids local desktop OAuth files (client_secrets_desktop.json).
    """
    recipients: list[str]
    if isinstance(to, str):
        recipients = [to]
    elif isinstance(to, Iterable):
        recipients = [str(x).strip() for x in to if str(x).strip()]
    else:
        raise ValueError("to must be a string or iterable of email strings")

    if not recipients:
        raise ValueError("No recipients provided")

    sender_profile = (profile or "").strip()
    if sender_profile:
        if not _is_connected_profile(sender_profile):
            raise RuntimeError(
                f"Profile '{sender_profile}' is not connected via Google OAuth. Reconnect and retry."
            )
    else:
        # Auto-pick a sender from recipients only if that address has completed OAuth.
        sender_profile = next((r for r in recipients if _is_connected_profile(r)), "")
        if not sender_profile:
            raise RuntimeError(
                "No connected Google profile found for email send. "
                "Have the user connect Google first via /connect/google."
            )

    for recipient in recipients:
        r = requests.post(
            f"{_backend_base()}/gmail/send",
            params={
                "profile": sender_profile,
                "to": recipient,
                "subject": subject or "",
                "body": body or "",
            },
            headers=_backend_headers(),
            timeout=30,
        )
        r.raise_for_status()
