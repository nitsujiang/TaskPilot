# scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler
from utils.slack import send_slack_message

def send_reminders(db):
    """
    Checks for upcoming tasks and sends Slack reminders to owners.
    """
    # TODO: wire in db.get_upcoming_tasks once db.py is ready
    tasks = db.get_upcoming_tasks(within_hours=24)
    for task in tasks:
        for owner in task.get("owners", []):
            send_slack_message(
                channel=task["channel"],
                text=f"Reminder: '{task['title']}' is due soon! {owner}"
            )

def start_scheduler(db):
    scheduler = BackgroundScheduler()
    scheduler.add_job(send_reminders, "interval", hours=1, args=[db])
    scheduler.start()
    return scheduler