import secrets
import requests
from flask import Flask, request, jsonify
from config import SLACK_SIGNING_SECRET, SLACK_BOT_TOKEN, API_ENDPOINT, BACKEND_API_KEY
from agent.parser import process_message, process_clarification
import databases.db as db_module
from databases.db import (
    save_task,
    init_db,
    get_tasks_for_owner,
    get_all_tasks,
    mark_all_tasks_complete,
    clear_all_tasks,
    mark_initial_email_sent,
)
from agent.scheduler import start_scheduler
from utils.gmail_utils import send_email
from utils.email_templates import format_task_email_body
from utils.slack import (
    send_slack_message_with_fallback,
    FALLBACK_MESSAGE,
    get_user_timezone,
    BOT_USER_ID,
    resolve_owner_mentions_to_emails,
)
from utils.time import zoneinfo_or_utc
from utils.gemini import call_gemini, take_last_gemini_user_hint
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from collections import OrderedDict, defaultdict
import threading
import re
import os
import json
import time
from datetime import datetime, timedelta, time as dtime
from pydantic import BaseModel
from typing import Optional

app = Flask(__name__)
init_db()

_scheduler = None


def _should_start_scheduler() -> bool:
    # Flask dev reloader imports the module twice; start only in the reloader child.
    if os.environ.get("FLASK_RUN_FROM_CLI") == "true":
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    return True


if _should_start_scheduler():
    try:
        _scheduler = start_scheduler(db_module)
        print("Reminder scheduler started.")
    except Exception as e:
        print(f"Failed to start reminder scheduler: {e}")

verifier = SignatureVerifier(SLACK_SIGNING_SECRET)
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# --- Duplicate event prevention ---
# Used for in memory deduplication of Slack events to prevent double-processing on retries.
# For bigger scale, use a DB with a unique constraint on event_id.
# 1. Resets on restart — Slack may retry unprocessed events causing duplicates
# 2. Not thread safe — multiple threads could pass the duplicate check simultaneously
# 3. Fixed size is arbitrary
processed_events = OrderedDict()
MAX_EVENTS = 1000


def is_duplicate(event_id: str) -> bool:
    if event_id in processed_events:
        return True
    processed_events[event_id] = True
    if len(processed_events) > MAX_EVENTS:
        processed_events.popitem(last=False)
    return False


# --- Rate limiting ---
# Same caveats as processed_events — in-memory, resets on restart, not thread safe.
user_request_times = defaultdict(list)
MAX_REQUESTS = 5
WINDOW_SECONDS = 60


def is_rate_limited(user_id: str) -> bool:
    now = time.time()
    user_request_times[user_id] = [
        t for t in user_request_times[user_id]
        if now - t < WINDOW_SECONDS
    ]
    if len(user_request_times[user_id]) >= MAX_REQUESTS:
        return True
    user_request_times[user_id].append(now)
    return False


# --- Session management ---
# - Keyed by thread_ts since app_mention replies stay in the same thread.
# - In-memory — sessions are lost on restart, which is acceptable since abandoned tasks are never saved.
# - Sessions expire after 15 minutes of inactivity (so follow-ups are not lost between messages).
sessions = {}
MAX_SESSIONS = 500
SESSION_TIMEOUT = 15 * 60
meeting_followups = {}
materials_sessions = {}


class BookingWindow(BaseModel):
    can_book: bool = False
    start_iso: Optional[str] = None
    end_iso: Optional[str] = None


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"(https?://[^\s>]+)", text)
    # Strip trailing punctuation common in chat.
    cleaned = []
    for u in urls:
        cleaned.append(u.rstrip(").,;!?>\"'"))
    # de-dupe preserving order
    return list(dict.fromkeys(cleaned))


def _extract_initial_email_pref(text: str) -> bool | None:
    t = (text or "").lower()
    opt_out_markers = [
        "no initial email",
        "don't send initial email",
        "dont send initial email",
        "no email",
        "skip email",
        "without email",
    ]
    opt_in_markers = [
        "send email",
        "yes email",
        "with email",
    ]
    if any(m in t for m in opt_out_markers):
        return False
    if any(m in t for m in opt_in_markers):
        return True
    return None


def _send_initial_email_and_mark(task_id: int | None, recipients: list[str], subject: str, body: str) -> None:
    """Send initial email and mark DB only after successful send."""
    send_email(to=recipients, subject=subject, body=body)
    if task_id:
        mark_initial_email_sent(task_id)


def _fixed_offset_for_timezone(timezone: str) -> str:
    # Lightweight fallback for common local dev zones.
    return "-04:00" if timezone == "America/New_York" else "+00:00"


def _parse_hour_minute(text_lower: str) -> tuple[int, int] | None:
    """Parse '9am', '9:30pm', or '14:00' from lowercase text."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text_lower)
    if m:
        h, mn = int(m.group(1)), int(m.group(2) or 0)
        ap = m.group(3)
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if not (0 <= h <= 23 and 0 <= mn <= 59):
            return None
        return (h, mn)
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text_lower)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def _regex_weekday_and_time(text: str, timezone: str) -> dict | None:
    """
    Handles replies like 'Monday 9AM' or 'Tuesday 14:30' (no 'HH:MM-HH:MM' range).
    Picks the next occurrence of that weekday in the user's timezone (must be strictly in the future).
    """
    day_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    lower = text.lower()
    target_weekday = None
    for name, idx in day_map.items():
        if re.search(r"\b" + re.escape(name) + r"\b", lower):
            target_weekday = idx
            break
    if target_weekday is None:
        return None
    # Let the range-style regex handle "14:00-15:00"
    if re.search(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", text):
        return None
    hm = _parse_hour_minute(lower)
    if hm is None:
        return None
    h, minute = hm
    tz = zoneinfo_or_utc(timezone)
    now = datetime.now(tz)
    today = now.date()
    dow = today.weekday()
    days_ahead = (target_weekday - dow) % 7
    target_date = today + timedelta(days=days_ahead)
    start_dt = datetime.combine(target_date, dtime(h, minute), tzinfo=tz)
    if start_dt <= now:
        start_dt += timedelta(days=7)
    end_dt = start_dt + timedelta(hours=1)
    return {
        "can_book": True,
        "start_iso": start_dt.isoformat(timespec="seconds"),
        "end_iso": end_dt.isoformat(timespec="seconds"),
    }


def _regex_extract_booking_window(text: str, timezone: str) -> dict | None:
    # Example handled: "book Tuesday next week 14:00-15:00"
    m = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
    if not m:
        return None
    now = datetime.now(zoneinfo_or_utc(timezone))
    # Choose the next Tuesday when user says "Tuesday next week"; otherwise next day with that weekday if present.
    day_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    lower = text.lower()
    target_weekday = None
    for name, idx in day_map.items():
        if name in lower:
            target_weekday = idx
            break
    if target_weekday is None:
        return None
    days_ahead = (target_weekday - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    if "next week" in lower:
        days_ahead += 7
    target_date = now.date() + timedelta(days=days_ahead)
    sh, sm, eh, em = map(int, m.groups())
    offset = _fixed_offset_for_timezone(timezone)
    start_iso = f"{target_date.isoformat()}T{sh:02d}:{sm:02d}:00{offset}"
    end_iso = f"{target_date.isoformat()}T{eh:02d}:{em:02d}:00{offset}"
    return {"can_book": True, "start_iso": start_iso, "end_iso": end_iso}


def get_session(thread_ts: str) -> dict | None:
    session = sessions.get(thread_ts)
    if session is None:
        return None
    if time.time() - session.get("last_active", 0) > SESSION_TIMEOUT:
        sessions.pop(thread_ts)
        return None
    return session


def _backend_base() -> str:
    return (API_ENDPOINT or "").rstrip("/")


def _backend_headers() -> dict:
    return {"X-Backend-Key": BACKEND_API_KEY or ""}


def _connect_url(state_id: str) -> str:
    return f"{_backend_base()}/connect/google?state_id={state_id}"


def _poll_email_for_state(state_id: str, timeout_sec: int = 600, poll_every_sec: int = 2) -> str | None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        r = requests.get(
            f"{_backend_base()}/profiles/by_state",
            params={"state_id": state_id},
            headers=_backend_headers(),
            timeout=20,
        )
        if r.status_code == 200:
            email = (r.json().get("profile") or "").strip()
            if email:
                return email
        time.sleep(poll_every_sec)
    return None


def _calendar_list(profile_email: str, max_results: int = 80) -> list[dict]:
    r = requests.get(
        f"{_backend_base()}/calendar/list",
        params={"profile": profile_email, "max_results": max_results},
        headers=_backend_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _drive_search(profile_email: str, query: str, page_size: int = 10) -> list[dict]:
    # backend currently returns 10; this wrapper keeps signature future-proof
    r = requests.get(
        f"{_backend_base()}/drive/search",
        params={"profile": profile_email, "query": query},
        headers=_backend_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _calendar_update_description(profile_email: str, event_id: str, description: str) -> dict:
    r = requests.post(
        f"{_backend_base()}/calendar/update_description",
        params={"profile": profile_email, "event_id": event_id, "description": description},
        headers=_backend_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _calendar_create(
    profile_email: str,
    summary: str,
    description: str,
    start_iso: str,
    end_iso: str,
    timezone_name: str,
) -> dict:
    r = requests.post(
        f"{_backend_base()}/calendar/create",
        params={
            "profile": profile_email,
            "summary": summary,
            "description": description,
            "start_iso": start_iso,
            "end_iso": end_iso,
            "timezone_name": timezone_name,
        },
        headers=_backend_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _iso_to_dt(iso_text: str) -> datetime:
    # Support Google-style UTC timestamps ending with Z.
    dt = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    # Normalize naive datetimes (e.g., all-day events) to UTC so comparisons are valid.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt


def _format_conflicts(conflicts: list[dict]) -> str:
    if not conflicts:
        return ""
    lines = []
    for c in conflicts[:8]:
        lines.append(f"- {c['email']}: {c['summary']} ({c['start']} to {c['end']})")
    return "\n".join(lines)


def _find_conflicts(emails: list[str], start_iso: str, end_iso: str) -> list[dict]:
    start_dt = _iso_to_dt(start_iso)
    end_dt = _iso_to_dt(end_iso)
    conflicts = []
    for email in emails:
        events = _calendar_list(email)
        for ev in events:
            ev_start = ev.get("start")
            ev_end = ev.get("end")
            if not ev_start or not ev_end:
                continue
            ev_start_dt = _iso_to_dt(ev_start)
            ev_end_dt = _iso_to_dt(ev_end)
            # overlap iff start < other_end and other_start < end
            if start_dt < ev_end_dt and ev_start_dt < end_dt:
                conflicts.append(
                    {
                        "email": email,
                        "summary": ev.get("summary", "(busy)"),
                        "start": ev_start,
                        "end": ev_end,
                    }
                )
    return conflicts


def _extract_suggested_slots(raw: str, timezone: str) -> list[dict]:
    tz = zoneinfo_or_utc(timezone)
    now = datetime.now(tz)
    window_end = now + timedelta(days=7)
    slots = []
    seen = set()
    pattern = re.compile(
        r"(?:(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
        r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})",
        re.IGNORECASE,
    )
    for m in pattern.finditer(raw or ""):
        date_text = m.group(2)
        sh, sm = int(m.group(3)), int(m.group(4))
        eh, em = int(m.group(5)), int(m.group(6))
        try:
            y, mo, d = [int(x) for x in date_text.split("-")]
            start = datetime(y, mo, d, sh, sm, tzinfo=tz)
            end = datetime(y, mo, d, eh, em, tzinfo=tz)
            if end <= start:
                end = start + timedelta(hours=1)
        except Exception:
            continue
        # Enforce the same policy we asked for: next 7 days, Mon-Fri, 09:00-17:00.
        if start < now or start > window_end:
            continue
        if start.weekday() >= 5:  # 5=Sat, 6=Sun
            continue
        if start.hour < 9 or (end.hour > 17 or (end.hour == 17 and end.minute > 0)):
            continue
        key = (start.isoformat(), end.isoformat())
        if key in seen:
            continue
        seen.add(key)
        slots.append(
            {
                "start_iso": start.isoformat(timespec="seconds"),
                "end_iso": end.isoformat(timespec="seconds"),
                "weekday": start.strftime("%A").lower(),
                "label": f"{start.strftime('%A, %Y-%m-%d %H:%M')}-{end.strftime('%H:%M')} ({timezone})",
            }
        )
        if len(slots) >= 5:
            break
    return slots


def _format_slot_options(slots: list[dict], timezone: str) -> str:
    if not slots:
        return "I couldn't format time suggestions right now. Please share a specific date/time."
    lines = ["Common meeting time suggestions:"]
    for i, s in enumerate(slots, start=1):
        lines.append(f"{i}. {s['label']}")
    lines.append("")
    lines.append("Reply with `pick 1` ... `pick 5` (or just `1`-`5`).")
    return "\n".join(lines)


def _booking_from_suggested_choice(text: str, slots: list[dict]) -> dict | None:
    if not slots:
        return None
    lower = (text or "").lower()
    nums = [int(n) for n in re.findall(r"\b([1-9]\d*)\b", lower)]
    if nums:
        idx = nums[0] - 1
        if 0 <= idx < min(len(slots), 5):
            return {"can_book": True, "start_iso": slots[idx]["start_iso"], "end_iso": slots[idx]["end_iso"]}
    for day in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]:
        if day in lower:
            for s in slots:
                if s.get("weekday") == day:
                    return {"can_book": True, "start_iso": s["start_iso"], "end_iso": s["end_iso"]}
    return None


def _send_common_time_suggestions(channel: str, thread_ts: str, emails: list[str], timezone: str) -> list[dict]:
    events_by_email = {email: _calendar_list(email) for email in emails}
    prompt = f"""
    You are a scheduling assistant.
    Find common meeting times for the next 7 days within business hours:
    - Mon-Fri
    - 09:00-17:00
    Timezone: {timezone}
    Busy events per attendee:
    {json.dumps(events_by_email, indent=2)}
    Return only 5 lines in this exact format:
    Weekday, YYYY-MM-DD HH:MM-HH:MM ({timezone})
    Do not include explanations, availability dumps, or extra text.
    """
    suggestions = call_gemini(prompt)
    if not suggestions:
        hint = take_last_gemini_user_hint()
        send_slack_message_with_fallback(
            channel,
            hint
            or (
                "I couldn't generate time suggestions just now. "
                "Please try again in a few minutes, or say a specific day/time to book."
            ),
            thread_ts=thread_ts,
        )
        return []
    slots = _extract_suggested_slots(suggestions, timezone)
    if not slots:
        send_slack_message_with_fallback(
            channel,
            "I couldn't parse suggested slots cleanly. Please share a specific date/time (e.g. Friday 2pm).",
            thread_ts=thread_ts,
        )
        return []
    send_slack_message_with_fallback(
        channel,
        _format_slot_options(slots, timezone),
        thread_ts=thread_ts,
    )
    return slots


def _drive_query_from_task(title: str, description: str) -> str:
    bits = []
    if title:
        bits.append(title)
    if description:
        bits.append(description)
    q = " ".join(bits).strip()
    return q[:180] if len(q) > 180 else q


def _render_drive_results(items: list[dict]) -> str:
    if not items:
        return "No matching files found."
    lines = []
    for i, it in enumerate(items[:5], start=1):
        name = it.get("name") or "(unnamed)"
        link = it.get("webViewLink") or (f"https://drive.google.com/open?id={it.get('id')}" if it.get("id") else "")
        lines.append(f"{i}. {name}" + (f"\n   {link}" if link else ""))
    return "\n".join(lines)


def _build_materials_description(base_description: str, pasted_links: list[str], selected_drive_items: list[dict]) -> str:
    base = (base_description or "").strip()
    lines = [base] if base else []
    materials = []
    for u in pasted_links:
        materials.append(f"- {u}")
    for it in selected_drive_items:
        name = it.get("name") or "(file)"
        link = it.get("webViewLink") or (f"https://drive.google.com/open?id={it.get('id')}" if it.get("id") else "")
        if link:
            materials.append(f"- {name}: {link}")
        else:
            materials.append(f"- {name}")
    if materials:
        lines.append("")
        lines.append("Materials:")
        lines.extend(materials)
    return "\n".join(lines).strip()


def _append_materials_bullets(existing_description: str, bullets: list[str]) -> str:
    """
    Append bullets under an existing 'Materials:' section if present.
    Otherwise create a new Materials section at the end.
    """
    desc = (existing_description or "").rstrip()
    new_lines = [f"- {b}" if not b.strip().startswith("-") else b for b in bullets if b.strip()]
    if not new_lines:
        return desc

    marker = "\nMaterials:\n"
    if marker in f"\n{desc}\n":
        # Append at end (Materials section is already last in our formatting).
        return (desc + "\n" + "\n".join(new_lines)).strip()

    parts = [desc] if desc else []
    parts.append("")
    parts.append("Materials:")
    parts.extend(new_lines)
    return "\n".join(parts).strip()


def _snap_booking_if_llm_past(booking: dict, timezone: str) -> dict:
    """If Gemini used a past year, roll start/end forward until start is in the future."""
    tz = zoneinfo_or_utc(timezone)
    now = datetime.now(tz)
    try:
        start = _iso_to_dt(booking["start_iso"])
        end = _iso_to_dt(booking["end_iso"])
    except Exception:
        return booking
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)
    if start >= now - timedelta(minutes=1):
        return booking
    duration = end - start
    if duration.total_seconds() <= 0:
        duration = timedelta(hours=1)
    for _ in range(6):
        try:
            start = start.replace(year=start.year + 1)
        except ValueError:
            start = start.replace(year=start.year + 1, month=2, day=28)
        end = start + duration
        if start >= now - timedelta(minutes=1):
            break
    booking["start_iso"] = start.isoformat(timespec="seconds")
    booking["end_iso"] = end.isoformat(timespec="seconds")
    return booking


def _extract_booking_window(text: str, timezone: str) -> dict | None:
    wd = _regex_weekday_and_time(text, timezone)
    if wd:
        return wd
    regex_booking = _regex_extract_booking_window(text, timezone)
    if regex_booking:
        return regex_booking
    now = datetime.now(zoneinfo_or_utc(timezone)).replace(microsecond=0)
    prompt = f"""
You are extracting a meeting time from user text.
Timezone: {timezone}
Current date and time in that timezone (use this year/month — do not use 2024 unless the user explicitly says 2024): {now.isoformat()}
Text: {text}

Return ONLY compact JSON with this schema:
{{
  "can_book": true/false,
  "start_iso": "YYYY-MM-DDTHH:MM:SS±HH:MM",
  "end_iso": "YYYY-MM-DDTHH:MM:SS±HH:MM"
}}
start_iso and end_iso must be on or after {now.isoformat()}. If the user picks a weekday and time (e.g. Monday 9am), use the next occurrence of that weekday on or after today.
If no clear time range exists, return can_book=false and empty strings.
"""
    data = call_gemini(prompt, schema=BookingWindow)
    if not data or not isinstance(data, dict):
        return None
    if not data.get("can_book"):
        return None
    if not data.get("start_iso") or not data.get("end_iso"):
        return None
    return _snap_booking_if_llm_past(data, timezone)


def handle_event(text: str, user_id: str, channel: str, thread_ts: str, timezone: str) -> None:
    try:
        normalized_text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
        normalized_text = re.sub(r"\s+", " ", normalized_text).strip()
        if (
            "my tasks" in normalized_text
            or "what are my tasks" in normalized_text
            or "what am i assigned to" in normalized_text
        ):
            tasks = get_tasks_for_owner(f"<@{user_id}>", limit=10)
            if not tasks:
                send_slack_message_with_fallback(
                    channel,
                    "I couldn't find any tasks assigned to you yet.",
                    thread_ts=thread_ts,
                )
                return

            lines = [f"Here are your tasks ({min(len(tasks), 5)} of {len(tasks)}):"]
            for index, task in enumerate(tasks[:5], start=1):
                title = task.get("title") or "(no title)"
                deadline_text = task.get("deadline")
                deadline = "no deadline"
                if deadline_text:
                    try:
                        deadline = datetime.fromisoformat(deadline_text).strftime("%b %d, %Y %I:%M %p")
                    except Exception:
                        deadline = deadline_text
                status = task.get("status") or "pending"
                urgency = task.get("urgency") or "n/a"
                lines.append(f"{index}. {title} | due: {deadline} | status: {status} | urgency: {urgency}")
            if len(tasks) > 5:
                lines.append(f"...and {len(tasks) - 5} more.")

            send_slack_message_with_fallback(channel, "\n".join(lines), thread_ts=thread_ts)
            return

        if (
            "task log" in normalized_text
            or "overview" in normalized_text
            or "all tasks" in normalized_text
            or "show tasks" in normalized_text
        ):
            all_tasks = get_all_tasks()
            if not all_tasks:
                send_slack_message_with_fallback(
                    channel,
                    "No tasks found in the database.",
                    thread_ts=thread_ts,
                )
                return

            pending = [t for t in all_tasks if (t.get("status") or "pending") != "completed"]
            completed = [t for t in all_tasks if (t.get("status") or "pending") == "completed"]
            todos = [t for t in all_tasks if t.get("task") == "todo"]
            meetings = [t for t in all_tasks if t.get("task") == "meeting"]

            lines = [
                f"*Task Overview* — {len(all_tasks)} total | {len(pending)} open | {len(completed)} completed | {len(todos)} todos | {len(meetings)} meetings",
                "",
            ]

            if pending:
                lines.append("*Open Tasks:*")
                for index, task in enumerate(pending[:15], start=1):
                    title = task.get("title") or "(no title)"
                    task_type = task.get("task") or "?"
                    deadline_text = task.get("deadline")
                    deadline = "no deadline"
                    if deadline_text:
                        try:
                            deadline = datetime.fromisoformat(deadline_text).strftime("%b %d %I:%M %p")
                        except Exception:
                            deadline = deadline_text
                    urgency = task.get("urgency") or "n/a"
                    owners = ", ".join(task.get("owners") or []) or "unassigned"
                    lines.append(f"{index}. [{task_type}] {title} | due: {deadline} | urgency: {urgency} | owners: {owners}")
                if len(pending) > 15:
                    lines.append(f"...and {len(pending) - 15} more open tasks.")

            if completed:
                lines.append(f"\n_{len(completed)} completed task(s) not shown. Say \"clear tasks\" to wipe or \"mark all complete\" to close everything._")

            send_slack_message_with_fallback(channel, "\n".join(lines), thread_ts=thread_ts)
            return

        if (
            "clear tasks" in normalized_text
            or "wipe tasks" in normalized_text
            or "delete all tasks" in normalized_text
            or "clear the db" in normalized_text
            or "wipe the db" in normalized_text
        ):
            count = clear_all_tasks()
            send_slack_message_with_fallback(
                channel,
                f"Done. Permanently deleted {count} task(s) from the database.",
                thread_ts=thread_ts,
            )
            return

        if (
            "mark all complete" in normalized_text
            or "mark all tasks complete" in normalized_text
            or "complete all tasks" in normalized_text
            or "close all tasks" in normalized_text
        ):
            count = mark_all_tasks_complete()
            send_slack_message_with_fallback(
                channel,
                f"Done. Marked {count} task(s) as completed.",
                thread_ts=thread_ts,
            )
            return

        materials = materials_sessions.get(thread_ts)
        if materials:
            phase = materials.get("phase")
            lower = text.strip().lower()

            if phase == "awaiting_materials":
                urls = _extract_urls(text)
                if lower in {"suggest", "drive", "files", "file"}:
                    materials["pasted_links"] = []
                    materials["drive_suggest_attempted"] = True
                    q = _drive_query_from_task(materials.get("title") or "", materials.get("base_description") or "")
                    organizer = materials.get("organizer_email")
                    try:
                        items = _drive_search(organizer, q)
                    except Exception as e:
                        print(f"Drive search error: {e}")
                        items = []
                    materials["drive_items"] = items[:5]
                    if not materials["drive_items"]:
                        materials["phase"] = "awaiting_materials"
                        send_slack_message_with_fallback(
                            channel,
                            "I couldn’t find any relevant files in Drive. "
                            "Do you want to paste link(s) to include under Materials? (paste links or reply `none`)",
                            thread_ts=thread_ts,
                        )
                        return
                    materials["phase"] = "awaiting_drive_selection"
                    send_slack_message_with_fallback(
                        channel,
                        "Here are a few Drive files that might be relevant:\n"
                        f"{_render_drive_results(materials['drive_items'])}\n\n"
                        "Reply with numbers to include (e.g. 1,3) or 'none'.",
                        thread_ts=thread_ts,
                    )
                    return
                if lower in {"no", "none", "nope", "nah"}:
                    materials["pasted_links"] = []
                    materials["drive_items"] = []
                    # Skip optional Drive consent — user already declined materials.
                    materials["phase"] = "finalize"
                    materials["user_skipped_materials"] = True
                elif urls:
                    materials["pasted_links"] = urls
                else:
                    send_slack_message_with_fallback(
                        channel,
                        "Please paste link(s) to include under Materials, or reply 'none'.",
                        thread_ts=thread_ts,
                    )
                    return

                if materials.get("phase") != "finalize":
                    materials["phase"] = "awaiting_drive_consent"
                    send_slack_message_with_fallback(
                        channel,
                        "Want me to suggest a few relevant files from your Drive for this meeting? (yes/no)",
                        thread_ts=thread_ts,
                    )
                    return

            if phase == "awaiting_drive_consent":
                if lower in {"yes", "y", "yeah", "yep", "sure", "ok", "okay"}:
                    q = _drive_query_from_task(materials.get("title") or "", materials.get("base_description") or "")
                    organizer = materials.get("organizer_email")
                    try:
                        items = _drive_search(organizer, q)
                    except Exception as e:
                        print(f"Drive search error: {e}")
                        items = []
                    materials["drive_items"] = items[:5]
                    if not materials["drive_items"]:
                        materials["phase"] = "awaiting_materials"
                        send_slack_message_with_fallback(
                            channel,
                            "I couldn’t find any relevant files in Drive. "
                            "Do you want to paste link(s) to include under Materials? (paste links or reply `none`)",
                            thread_ts=thread_ts,
                        )
                        return
                    materials["phase"] = "awaiting_drive_selection"
                    send_slack_message_with_fallback(
                        channel,
                        "Here are a few Drive files that might be relevant:\n"
                        f"{_render_drive_results(materials['drive_items'])}\n\n"
                        "Reply with numbers to include (e.g. 1,3) or 'none'.",
                        thread_ts=thread_ts,
                    )
                    return
                if lower in {"no", "n", "nope", "nah"}:
                    materials["drive_items"] = []
                    materials["phase"] = "finalize"
                else:
                    send_slack_message_with_fallback(channel, "Please reply yes or no.", thread_ts=thread_ts)
                    return

            phase = materials.get("phase")

            if phase in {"awaiting_drive_selection", "finalize"}:
                selected = []
                if phase == "awaiting_drive_selection":
                    if lower in {"no", "none", "skip"}:
                        selected = []
                    else:
                        nums = [int(n) for n in re.findall(r"\d+", text)]
                        for n in nums:
                            idx = n - 1
                            if 0 <= idx < len(materials.get("drive_items") or []):
                                selected.append(materials["drive_items"][idx])

                full_desc = _build_materials_description(
                    materials.get("base_description") or "",
                    materials.get("pasted_links") or [],
                    selected,
                )

                try:
                    for email, ev_id in (materials.get("events") or {}).items():
                        _calendar_update_description(email, ev_id, full_desc)
                    send_slack_message_with_fallback(
                        channel,
                        "Updated the calendar invite with Materials.",
                        thread_ts=thread_ts,
                    )
                    # First reply was `none` — no Drive step, no extra "more materials" loop.
                    if materials.get("user_skipped_materials"):
                        send_slack_message_with_fallback(
                            channel,
                            "No links added under Materials. You're all set.",
                            thread_ts=thread_ts,
                        )
                        materials_sessions.pop(thread_ts, None)
                    else:
                        send_slack_message_with_fallback(
                            channel,
                            "Want to include more Materials in the invite?\n"
                            "- Paste link(s), or reply `none`",
                            thread_ts=thread_ts,
                        )
                        materials_sessions[thread_ts] = {
                            **materials,
                            "phase": "awaiting_more_materials",
                            "base_description": full_desc,
                            "drive_items": [],
                            "drive_suggest_attempted": False,
                        }
                except Exception as e:
                    print(f"Failed to update event description: {e}")
                    send_slack_message_with_fallback(channel, FALLBACK_MESSAGE, thread_ts=thread_ts)
                finally:
                    # Only clear session if we didn't transition to awaiting_more_materials.
                    if materials_sessions.get(thread_ts, {}).get("phase") != "awaiting_more_materials":
                        materials_sessions.pop(thread_ts, None)
                return

            if phase == "awaiting_more_materials":
                urls = _extract_urls(text)
                lower = text.strip().lower()
                if lower in {"no", "none", "nope", "nah"}:
                    materials_sessions.pop(thread_ts, None)
                    return
                if not urls:
                    send_slack_message_with_fallback(
                        channel,
                        "Please paste link(s) to add, or reply `none`.",
                        thread_ts=thread_ts,
                    )
                    return
                updated_desc = _append_materials_bullets(
                    materials.get("base_description") or "",
                    urls,
                )
                try:
                    for email, ev_id in (materials.get("events") or {}).items():
                        _calendar_update_description(email, ev_id, updated_desc)
                    send_slack_message_with_fallback(
                        channel,
                        "Added those links to Materials.",
                        thread_ts=thread_ts,
                    )
                except Exception as e:
                    print(f"Failed to append materials links: {e}")
                    send_slack_message_with_fallback(channel, FALLBACK_MESSAGE, thread_ts=thread_ts)
                # Keep looping until user says none.
                materials_sessions[thread_ts] = {**materials, "base_description": updated_desc, "phase": "awaiting_more_materials"}
                send_slack_message_with_fallback(
                    channel,
                    "Want to include more Materials in the invite?\n"
                    "- Paste link(s), or reply `none`",
                    thread_ts=thread_ts,
                )
                return

        followup = meeting_followups.get(thread_ts)
        if followup:
            booking = _extract_booking_window(text, timezone)
            if not booking:
                booking = _booking_from_suggested_choice(text, followup.get("suggested_slots") or [])
            if booking:
                conflicts = _find_conflicts(
                    followup["emails"],
                    booking["start_iso"],
                    booking["end_iso"],
                )
                if conflicts:
                    send_slack_message_with_fallback(
                        channel,
                        "That time conflicts with existing events:\n"
                        f"{_format_conflicts(conflicts)}\n\n"
                        "Pick 1-5 from the suggested options, or ask me to suggest again.",
                        thread_ts=thread_ts,
                    )
                    return
                created = []
                events = {}
                for email in followup["emails"]:
                    ev = _calendar_create(
                        profile_email=email,
                        summary=followup["title"],
                        description=followup["description"],
                        start_iso=booking["start_iso"],
                        end_iso=booking["end_iso"],
                        timezone_name=timezone,
                    )
                    created.append({"email": email, "event": ev})
                    if ev.get("id"):
                        events[email] = ev["id"]
                meeting_followups.pop(thread_ts, None)
                send_slack_message_with_fallback(
                    channel,
                    "No conflicts found. "
                    f"Booked the meeting for {booking['start_iso']} to {booking['end_iso']}.\n"
                    f"Created on {len(created)} calendar(s).",
                    thread_ts=thread_ts,
                )
                organizer = followup["emails"][0] if followup.get("emails") else None
                if organizer and events:
                    materials_sessions[thread_ts] = {
                        "phase": "awaiting_materials",
                        "organizer_email": organizer,
                        "events": events,  # email -> event_id
                        "title": followup.get("title") or "",
                        "base_description": followup.get("description") or "",
                        "pasted_links": [],
                        "drive_items": [],
                    }
                    send_slack_message_with_fallback(
                        channel,
                        "What links/files should I include under Materials in the invite?\n"
                        "- Paste link(s), or reply `none`\n"
                        "- Or reply `suggest` and I’ll suggest a few files from your Drive",
                        thread_ts=thread_ts,
                    )
                return
            lower_text = text.lower()
            if any(k in lower_text for k in ["suggest", "available", "free", "slot", "time"]):
                slots = _send_common_time_suggestions(channel, thread_ts, followup["emails"], timezone)
                if slots:
                    followup["suggested_slots"] = slots
                    meeting_followups[thread_ts] = followup
                return
            send_slack_message_with_fallback(
                channel,
                "Please choose a suggested slot by replying `pick 1` to `pick 5` (or just `1`-`5`). "
                "If you need a fresh list, say `suggest`.",
                thread_ts=thread_ts,
            )
            return

        session = get_session(thread_ts)
        if session:
            # Continuation — merge reply into existing session
            task_data = process_clarification(text, session["task_data"], channel, timezone, thread_ts)
            pref = _extract_initial_email_pref(text)
            if pref is not None:
                task_data["send_initial_email"] = pref
        else:
            # Fresh extraction
            task_data = process_message(text, channel, timezone, thread_ts)
            # Some error occurred during processing
            if not task_data: # process_message already sent the fallback
                sessions.pop(thread_ts, None)
                return
            pref = _extract_initial_email_pref(text)
            if pref is not None:
                task_data["send_initial_email"] = pref

        if task_data.get("missing_infos"):
            # Still needs clarification, keep session alive and update last_active
            sessions[thread_ts] = {
                "task_data": task_data,
                "timezone": timezone,
                "last_active": time.time()
            }
        else:
            # Complete — save and clear session
            sessions.pop(thread_ts, None)
            task_data["channel"] = channel
            task_data["thread_ts"] = thread_ts
            task_data["owners_emails"] = resolve_owner_mentions_to_emails(task_data.get("owners") or [])
            task_id = save_task(task_data)
            print(f"Task saved: {json.dumps(task_data, indent=2)}")
            send_slack_message_with_fallback(channel, "Got it! Task saved.", thread_ts=thread_ts)

            # --- Send email notification ---
            try:
               import threading
               recipients = task_data.get("owners_emails", [])
               if recipients and task_data.get("send_initial_email", True):
                    threading.Thread(
                        target=_send_initial_email_and_mark,
                        kwargs={
                            "task_id": task_id,
                            "recipients": recipients,
                            "subject": f"Task Saved: {task_data.get('title', '(no title)')}",
                            "body": format_task_email_body(task_data, kind="initial"),
                        }
                    ).start()
               elif not task_data.get("send_initial_email", True):
                    send_slack_message_with_fallback(
                        channel,
                        "Skipping initial email notification for this item (as requested).",
                        thread_ts=thread_ts,
                    )
            except Exception as e:
                print(f"Failed to send email: {e}")

            # Meeting flow: ask tagged owners to connect Google, then suggest common times.
            if task_data.get("task") == "meeting" and task_data.get("owners"):
                owners_mentions = task_data["owners"]
                requested_booking = _extract_booking_window(text, timezone)

                def oauth_and_suggest():
                    # 1) Ask tagged owners to connect Google
                    state_by_owner = {}  # mention -> state_id
                    for mention in owners_mentions:
                        state_id = secrets.token_urlsafe(16)
                        state_by_owner[mention] = state_id
                        send_slack_message_with_fallback(
                            channel,
                            f"{mention} please connect Google so I can suggest meeting times: {_connect_url(state_id)}",
                            thread_ts=thread_ts,
                        )

                    # 2) Poll backend until each owner’s email is available
                    emails = []
                    for _, state_id in state_by_owner.items():
                        email = _poll_email_for_state(state_id)
                        if email:
                            emails.append(email)

                    if len(emails) < 1: # minimum of 1 attendee to compute common times
                        send_slack_message_with_fallback(
                            channel,
                            "Not enough attendees connected Google to compute common times yet. Once everyone connects, mention me again in this thread.",
                            thread_ts=thread_ts,
                        )
                        return

                    meeting_followups[thread_ts] = {
                        "emails": emails,
                        "title": task_data.get("title") or "Meeting",
                        "description": task_data.get("description") or "",
                        "suggested_slots": [],
                    }

                    # If user already gave a concrete slot, perform direct conflict-check + booking.
                    if requested_booking:
                        try:
                            conflicts = _find_conflicts(
                                emails,
                                requested_booking["start_iso"],
                                requested_booking["end_iso"],
                            )
                            if conflicts:
                                send_slack_message_with_fallback(
                                    channel,
                                    "I checked that requested slot and found conflicts:\n"
                                    f"{_format_conflicts(conflicts)}\n\n"
                                    "Share another time and I will check it.",
                                    thread_ts=thread_ts,
                                )
                                return
                            created = []
                            events = {}
                            for email in emails:
                                ev = _calendar_create(
                                    profile_email=email,
                                    summary=task_data.get("title") or "Meeting",
                                    description=task_data.get("description") or "",
                                    start_iso=requested_booking["start_iso"],
                                    end_iso=requested_booking["end_iso"],
                                    timezone_name=timezone,
                                )
                                created.append({"email": email, "event": ev})
                                if ev.get("id"):
                                    events[email] = ev["id"]
                            send_slack_message_with_fallback(
                                channel,
                                "No conflicts found. "
                                f"Booked the requested meeting for {requested_booking['start_iso']} to {requested_booking['end_iso']}.\n"
                                f"Created on {len(created)} calendar(s).",
                                thread_ts=thread_ts,
                            )
                            # Booking completed for this thread; clear followup mode.
                            meeting_followups.pop(thread_ts, None)
                            organizer = emails[0] if emails else None
                            if organizer and events:
                                materials_sessions[thread_ts] = {
                                    "phase": "awaiting_materials",
                                    "organizer_email": organizer,
                                    "events": events,
                                    "title": task_data.get("title") or "",
                                    "base_description": task_data.get("description") or "",
                                    "pasted_links": [],
                                    "drive_items": [],
                                }
                                send_slack_message_with_fallback(
                                    channel,
                                    "What links/files should I include under Materials in the invite?\n"
                                    "- Paste link(s), or reply `none`\n"
                                    "- Or reply `suggest` and I’ll suggest a few files from your Drive",
                                    thread_ts=thread_ts,
                                )
                            return
                        except Exception as e:
                            print(f"Error during direct booking: {e}")
                            send_slack_message_with_fallback(channel, FALLBACK_MESSAGE, thread_ts=thread_ts)
                            return

                    send_slack_message_with_fallback(
                        channel,
                        "Got it. Do you have a specific time in mind, or should I suggest common free slots?",
                        thread_ts=thread_ts,
                    )
                    return
                threading.Thread(target=oauth_and_suggest, daemon=True).start()

    except Exception as e:
        print(f"Error processing message: {e}")
        sessions.pop(thread_ts, None)
        send_slack_message_with_fallback(channel, FALLBACK_MESSAGE, thread_ts=thread_ts)


@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Guard against oversized payloads before reading body
    if request.content_length and request.content_length > 1_000_000: # 1 MB limit
        return "", 413
    # Verify the request is from Slack and not a replay attack
    if not verifier.is_valid_request(request.get_data(), request.headers):
        return "", 403

    data = request.get_json()

    # Slack URL verification challenge
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})

    if "event" in data:
        event = data["event"]
        user_id = event.get("user")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        print(f"thread_ts: {event.get('thread_ts')}, ts: {event.get('ts')}, using: {thread_ts}")
        # Ignore bot messages and events without a user
        if event.get("subtype") == "bot_message" or not user_id:
            return "", 200

        if event.get("type") == "app_mention":
            event_id = data.get("event_id")
            # Acknowledge the request immediately, then process in the background to avoid timeouts.
            if is_duplicate(event_id):
                return "", 200

            if is_rate_limited(user_id):
                send_slack_message_with_fallback(
                    channel,
                    "You're sending too many requests. Please slow down.",
                    thread_ts=thread_ts
                )
                return "", 200

            if len(sessions) >= MAX_SESSIONS:
                send_slack_message_with_fallback(
                    channel,
                    "I'm currently at capacity. Please try again later.",
                    thread_ts=thread_ts
                )
                return "", 200

            # Check if session exists but expired
            if thread_ts in sessions and get_session(thread_ts) is None:
                send_slack_message_with_fallback(
                    channel,
                    "This conversation has timed out. Please start a new thread to create a task.",
                    thread_ts=thread_ts
                )
                return "", 200

            # Strip only the bot mention to preserve other mentions like owners
            text = event.get("text", "")
            cleaned_text = re.sub(rf"<@{BOT_USER_ID}>", "", text, count=1).strip()
            timezone = get_user_timezone(user_id)
            print(cleaned_text)
            # Acknowledge immediately and process in background
            # per https://docs.slack.dev/interactivity/handling-user-interaction/#acknowledgment_response
            threading.Thread(
                target=handle_event,
                args=(cleaned_text, user_id, channel, thread_ts, timezone)
            ).start()

    return "", 200


@app.route("/health", methods=["GET"])
def health():
    status = {"status": "ok", "version": os.getenv("APP_VERSION", "1.0")}
    try:
        auth = slack_client.auth_test()
        status["slack"] = {"ok": True, "user": auth.get("user")}
    except Exception as e:
        status["slack"] = {"ok": False, "error": str(e)}
        status["status"] = "degraded"

    code = 200 if status["status"] == "ok" else 503
    return jsonify(status), code


if __name__ == "__main__":
    # For auto reload, run: flask --app app run --port 3000 --reload
    app.run(port=3000)