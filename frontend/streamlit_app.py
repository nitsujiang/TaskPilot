import os
from datetime import datetime, timedelta, timezone

import psycopg2
import streamlit as st
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "agent-backend", ".env"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    st.error("DATABASE_URL is not set in .env or agent-backend/.env.")
    st.stop()


def _query(sql: str, params: tuple = ()) -> list[dict]:
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())


def _query_one(sql: str, params: tuple = ()) -> dict:
    rows = _query(sql, params)
    return rows[0] if rows else {}


def load_overview(now_utc: datetime) -> dict:
    week_start = now_utc.date() - timedelta(days=now_utc.weekday())
    week_end = week_start + timedelta(days=6)
    next_24h = now_utc + timedelta(hours=24)

    totals = _query_one(
        """
        SELECT
            COUNT(*) AS total_tasks,
            COUNT(*) FILTER (WHERE COALESCE(status, 'pending') != 'completed') AS open_tasks,
            COUNT(*) FILTER (WHERE task_type = 'meeting') AS meeting_tasks
        FROM tasks
        """
    )

    this_week = _query(
        """
        SELECT id, task_type, title, owners_json, deadline, status, urgency, created_at
        FROM tasks
        WHERE deadline IS NOT NULL
          AND deadline::date BETWEEN %s AND %s
        ORDER BY deadline ASC
        """,
        (week_start, week_end),
    )

    incomplete = _query(
        """
        SELECT id, task_type, title, owners_json, deadline, status, urgency, created_at
        FROM tasks
        WHERE COALESCE(status, 'pending') != 'completed'
        ORDER BY deadline NULLS LAST, created_at DESC
        LIMIT 200
        """
    )

    due_24h = _query(
        """
        SELECT id, title, owners_json, deadline, status, urgency
        FROM tasks
        WHERE deadline IS NOT NULL
          AND deadline >= %s
          AND deadline <= %s
          AND COALESCE(status, 'pending') != 'completed'
        ORDER BY deadline ASC
        """,
        (now_utc, next_24h),
    )

    profiles = _query_one("SELECT COUNT(*) AS connected_profiles FROM profiles")

    return {
        "totals": totals,
        "this_week": this_week,
        "incomplete": incomplete,
        "due_24h": due_24h,
        "connected_profiles": profiles.get("connected_profiles", 0),
        "week_start": week_start,
        "week_end": week_end,
    }


def main():
    st.set_page_config(page_title="TaskPilot Overview", page_icon="📋", layout="wide")
    st.title("TaskPilot Memory Overview")
    st.caption("Shows what the agent currently remembers from PostgreSQL.")

    now_utc = datetime.now(timezone.utc)
    data = load_overview(now_utc)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Tasks", int(data["totals"].get("total_tasks", 0)))
    c2.metric("Open Tasks", int(data["totals"].get("open_tasks", 0)))
    c3.metric("Meeting Tasks", int(data["totals"].get("meeting_tasks", 0)))
    c4.metric("Connected Google Profiles", int(data["connected_profiles"]))

    st.subheader("Tasks Saved This Week")
    st.caption(f"Week window: {data['week_start']} to {data['week_end']}")
    st.dataframe(data["this_week"], use_container_width=True, hide_index=True)

    st.subheader("Incomplete Tasks")
    st.dataframe(data["incomplete"], use_container_width=True, hide_index=True)

    st.subheader("Email Reminder Readiness (next 24h)")
    st.caption(
        "Current code has an email sender, but scheduled reminders are not fully wired in startup. "
        "This section shows tasks that should be reminder candidates."
    )
    st.dataframe(data["due_24h"], use_container_width=True, hide_index=True)

    st.info(
        "Reminder scheduler code exists in `agent/scheduler.py` (hourly job), "
        "but `start_scheduler(...)` is not started in the app entrypoint yet."
    )


if __name__ == "__main__":
    main()

