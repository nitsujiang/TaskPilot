import base64
import json
from email.mime.text import MIMEText
from typing import Iterable

from config import PROFILE_EMAIL
from databases.db import conn as db_conn


def _is_connected_profile(email: str) -> bool:
    c = db_conn()
    try:
        with c, c.cursor() as cur:
            cur.execute("SELECT 1 FROM profiles WHERE profile = %s", (email.strip(),))
            return cur.fetchone() is not None
    finally:
        c.close()


def _list_connected_profiles(limit: int = 20) -> list[str]:
    c = db_conn()
    try:
        with c, c.cursor() as cur:
            cur.execute(
                "SELECT profile FROM profiles WHERE profile NOT LIKE '__pending__%' ORDER BY created_at DESC NULLS LAST LIMIT %s",
                (max(1, min(int(limit), 200)),),
            )
            rows = cur.fetchall() or []
        return [r[0] for r in rows if r and r[0]]
    finally:
        c.close()


def _send_via_gmail_api(sender_profile: str, to: str, subject: str, body: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]

    c = db_conn()
    try:
        with c, c.cursor() as cur:
            cur.execute("SELECT creds_json FROM profiles WHERE profile = %s", (sender_profile,))
            row = cur.fetchone()
        if not row:
            raise RuntimeError(f"Profile '{sender_profile}' not found in DB.")
        creds = Credentials.from_authorized_user_info(json.loads(row[0]), scopes=SCOPES)
    finally:
        c.close()

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())

    service = build("gmail", "v1", credentials=creds)
    msg = MIMEText(body or "")
    msg["to"] = to.strip()
    msg["subject"] = (subject or "").strip()
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def send_email(to, subject, body, profile: str | None = None):
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
            raise RuntimeError(f"Profile '{sender_profile}' is not connected. Reconnect via /connect/google.")
    else:
        configured = (PROFILE_EMAIL or "").strip()
        if configured and _is_connected_profile(configured):
            sender_profile = configured
        else:
            sender_profile = next((r for r in recipients if _is_connected_profile(r)), "")
            if not sender_profile:
                connected = _list_connected_profiles(limit=20)
                sender_profile = connected[0] if connected else ""

        if not sender_profile:
            raise RuntimeError(
                "No connected Google profile found for email send. "
                "Connect Google via /connect/google, or set PROFILE_EMAIL to a connected address."
            )

    for recipient in recipients:
        _send_via_gmail_api(sender_profile, recipient, subject, body)
