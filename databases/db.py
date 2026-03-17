import sqlite3

def init_db():
    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT,
            owner TEXT,
            deadline TEXT,
            status TEXT,
            urgency TEXT,
            created_at TEXT
        )
        """)

    conn.commit()
    conn.close()

def save_task(task_data):
    import sqlite3
    from datetime import datetime

    conn = sqlite3.connect("tasks.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO tasks (task, owner, deadline, status, urgency, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        task_data.get("task"),
        task_data.get("owner"),
        task_data.get("deadline"),
        task_data.get("status"),
        task_data.get("urgency"),
        datetime.now().isoformat()
    ))

    conn.commit()
    conn.close()

def run_agent(task_data):
    if not task_data:
        return "ignore"

    if not task_data.get("task"):
        return "ignore"

    if len(task_data.get("missing_info", [])) > 0:
        return "clarify"

    return "store"