import os
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from config import DATABASE_URL

def _conn():
    return psycopg2.connect(DATABASE_URL)

# Expose a simple connection function for other modules to use. 
# Each call creates a new connection, so callers should use it in a context manager (with statement) to ensure proper cleanup
conn = _conn

def _parse_deadline(deadline_text: str | None):
    """
    Parse a deadline string in ISO 8601 format, e.g. "2024-06-01T15:00:00Z".
    Returns a timezone-aware datetime in UTC, or None if parsing fails.
    """
    if not deadline_text:
        return None
    try:
        # UTC in ISO 8601 format uses Z to indicate +00:00 or no offset
        dt = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
        # If no local timezone, assume UTC
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _init_from_sql_file(filename: str):
    sql_path = os.path.join(os.path.dirname(__file__), filename)
    with _conn() as conn:
        with conn.cursor() as cursor:
            with open(sql_path) as f:
                cursor.execute(f.read())

def init_db():
    """
    Create both the task and OAuth profile schemas if they do not exist.
    This is idempotent and can be called at app startup to ensure the database is ready.
    """
    _init_from_sql_file("tasks.sql")
    _init_from_sql_file("profiles.sql")
    # Backward-compatible migration for existing databases created before owners_emails_json.
    with _conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                ALTER TABLE tasks
                ADD COLUMN IF NOT EXISTS owners_emails_json JSONB NOT NULL DEFAULT '[]'::jsonb
                """
            )

def save_task(task_data: dict):
    """
    Save a task dict into PostgreSQL.

    Expected at save-time from the parser flow:
    - task, title, description, owners, deadline, urgency
    - channel, thread_ts (attached by the Slack handler before save)

    Optional input:
    - status (defaults to "pending" if missing)

    Notes:
    - owners should be a list of Slack mentions; it is stored in JSONB via Json(...).
    - deadline should be an ISO-8601 string; invalid/missing values are stored as NULL.
    """
    owners = task_data.get("owners") or []
    owners_emails = task_data.get("owners_emails") or []

    deadline_dt = _parse_deadline(task_data.get("deadline"))

    with _conn() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO tasks (
                    task_type, title, description, owners_json, owners_emails_json, channel, thread_ts, deadline, status, urgency, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_data.get("task"),
                    task_data.get("title"),
                    task_data.get("description"),
                    Json(owners),
                    Json(owners_emails),
                    task_data.get("channel"),
                    task_data.get("thread_ts"),
                    deadline_dt,
                    task_data.get("status") or "pending",
                    task_data.get("urgency"),
                    datetime.now(timezone.utc),
                ),
            )


def get_upcoming_tasks(within_hours: int = 24) -> list:
    """
    Return tasks with deadline in the next `within_hours` hours.
    """
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    task_type,
                    title,
                    description,
                    owners_json,
                    owners_emails_json,
                    channel,
                    thread_ts,
                    deadline,
                    status,
                    urgency,
                    created_at
                FROM tasks
                WHERE deadline IS NOT NULL
                  AND deadline >= NOW()
                  AND deadline <= NOW() + (%s || ' hours')::interval
                  AND COALESCE(status, 'pending') != 'completed'
                ORDER BY deadline ASC
                """,
                (str(int(within_hours)),),
            )
            rows = cursor.fetchall()

    out = []
    for row in rows:
        owners = row.get("owners_json") or []
        owners_emails = row.get("owners_emails_json") or []
        out.append(
            {
                "id": row["id"],
                "task": row.get("task_type"),
                "title": row.get("title"),
                "description": row.get("description"),
                "owners": owners,
                "owners_emails": owners_emails,
                "channel": row.get("channel"),
                "thread_ts": row.get("thread_ts"),
                "deadline": row.get("deadline").isoformat() if row.get("deadline") else None,
                "status": row.get("status"),
                "urgency": row.get("urgency"),
                "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            }
        )

    return out


def get_tasks_for_owner(owner_mention: str, limit: int = 10) -> list:
    """Return tasks assigned to a given Slack user mention."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    task_type,
                    title,
                    description,
                    owners_json,
                    owners_emails_json,
                    channel,
                    thread_ts,
                    deadline,
                    status,
                    urgency,
                    created_at
                FROM tasks
                WHERE owners_json @> %s::jsonb
                ORDER BY deadline NULLS LAST, created_at DESC
                LIMIT %s
                """,
                (Json([owner_mention]), int(limit)),
            )
            rows = cursor.fetchall()

    out = []
    for row in rows:
        owners = row.get("owners_json") or []
        owners_emails = row.get("owners_emails_json") or []
        out.append(
            {
                "id": row["id"],
                "task": row.get("task_type"),
                "title": row.get("title"),
                "description": row.get("description"),
                "owners": owners,
                "owners_emails": owners_emails,
                "channel": row.get("channel"),
                "thread_ts": row.get("thread_ts"),
                "deadline": row.get("deadline").isoformat() if row.get("deadline") else None,
                "status": row.get("status"),
                "urgency": row.get("urgency"),
                "created_at": row.get("created_at").isoformat() if row.get("created_at") else None,
            }
        )

    return out


def run_agent(task_data):
    if not task_data:
        return "ignore"

    if not task_data.get("task"):
        return "ignore"

    if len(task_data.get("missing_infos", [])) > 0:
        return "clarify"

    return "store"
