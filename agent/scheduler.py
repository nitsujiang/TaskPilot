# scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from utils.slack import send_slack_message
from utils.gmail_utils import send_email


_sent_email_keys = set()

def send_reminders(db):
    """
    Checks for upcoming tasks and sends Slack + email reminders.
    """
    tasks = db.get_upcoming_tasks(within_hours=24)
    for task in tasks:
        for owner in task.get("owners", []):
            send_slack_message(
                channel=task["channel"],
                text=f"Reminder: '{task['title']}' is due soon! {owner}"
            )
        for recipient in task.get("owners_emails", []):
            # Keep one email reminder per process run per task+deadline+recipient.
            key = f"{task.get('id')}|{task.get('deadline')}|{recipient}"
            if key in _sent_email_keys:
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
                _sent_email_keys.add(key)
            except Exception as e:
                print(f"Failed to send reminder email to {recipient}: {e}")

def start_scheduler(db):
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_reminders, "interval", hours=1, args=[db])
    scheduler.start()
    return scheduler