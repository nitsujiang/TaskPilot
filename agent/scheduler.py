from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from utils.slack import send_slack_message
from utils.gmail_utils import send_email


def _parse_iso(dt_text: str | None):
    if not dt_text:
        return None
    try:
        dt = datetime.fromisoformat(dt_text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _reminder_interval(urgency: str | None, days_left: float) -> timedelta:
    u = (urgency or "medium").lower()
    if u == "high":
        if days_left > 7:
            return timedelta(hours=24)
        if days_left > 2:
            return timedelta(hours=8)
        if days_left > 0.5:
            return timedelta(hours=2)
        return timedelta(hours=1)
    if u == "low":
        if days_left > 14:
            return timedelta(hours=72)
        if days_left > 3:
            return timedelta(hours=48)
        if days_left > 1:
            return timedelta(hours=24)
        return timedelta(hours=12)
    # medium urgency
    if days_left > 14:
        return timedelta(hours=48)
    if days_left > 3:
        return timedelta(hours=24)
    if days_left > 1:
        return timedelta(hours=8)
    return timedelta(hours=4)


def send_reminders(db):
    """
    Checks open tasks/meetings and sends urgency-aware reminders.
    """
    tasks = db.get_upcoming_tasks(within_hours=24 * 14)
    now = datetime.now(timezone.utc)
    for task in tasks:
        deadline = _parse_iso(task.get("deadline"))
        if not deadline:
            continue
        days_left = max((deadline - now).total_seconds() / 86400.0, 0.0)
        cadence = _reminder_interval(task.get("urgency"), days_left)

        for owner in task.get("owners", []):
            send_slack_message(
                channel=task["channel"],
                text=f"Reminder: '{task['title']}' is due soon! {owner}",
            )
        for recipient in task.get("owners_emails", []):
            recipient_last_map = task.get("last_email_reminder_by_recipient") or {}
            recipient_last_sent = _parse_iso(recipient_last_map.get(recipient))
            if recipient_last_sent and (now - recipient_last_sent) < cadence:
                continue
            try:
                send_email(
                    to=recipient,
                    subject=f"Reminder: {task.get('title', '(no title)')} is due soon",
                    body=(
                        f"Task: {task.get('title', '(no title)')}\n"
                        f"Type: {task.get('task', 'todo')}\n"
                        f"Deadline: {task.get('deadline') or 'none'}\n"
                        f"Urgency: {task.get('urgency') or 'n/a'}\n"
                        f"Status: {task.get('status') or 'pending'}\n"
                        f"Channel: {task.get('channel') or 'n/a'}"
                    ),
                )
                db.mark_email_reminder_sent(task.get("id"), recipient)
            except Exception as e:
                print(f"Failed to send reminder email to {recipient}: {e}")

def start_scheduler(db):
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_reminders, "interval", hours=1, args=[db])
    scheduler.start()
    return scheduler