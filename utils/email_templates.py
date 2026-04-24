def format_task_email_body(task: dict, *, kind: str) -> str:
    """Build a readable plain-text email body for task notifications."""
    title = task.get("title") or "(no title)"
    task_type = (task.get("task") or "todo").capitalize()
    description = task.get("description") or "(no description)"
    urgency = (task.get("urgency") or "medium").capitalize()
    deadline = task.get("deadline") or "Not specified"
    status = (task.get("status") or "pending").capitalize()
    channel = task.get("channel") or "n/a"
    owners = ", ".join(task.get("owners") or []) or "n/a"
    owner_emails = ", ".join(task.get("owners_emails") or []) or "n/a"

    if kind == "reminder":
        header = "This is a reminder that a task is due soon."
    else:
        header = "Your task was saved successfully."

    return (
        f"{header}\n\n"
        f"Title: {title}\n"
        f"Type: {task_type}\n"
        f"Description: {description}\n"
        f"Urgency: {urgency}\n"
        f"Deadline: {deadline}\n"
        f"Status: {status}\n"
        f"Channel: {channel}\n"
        f"Owners (Slack): {owners}\n"
        f"Owners (Email): {owner_emails}\n\n"
        "This is an automated TaskPilot notification."
    )
