# TaskPilot

TaskPilot is a Slack agent that extracts tasks/meetings from natural language, stores them in PostgreSQL, supports Google Calendar/Drive meeting workflows, and provides a Streamlit overview dashboard.

## Current Capabilities

- Extract task or meeting fields (`task`, `title`, `description`, `owners`, `deadline`, `urgency`) from Slack messages.
- Clarification loop for missing fields.
- Meeting flow:
  - book a user-provided time
  - or suggest common availability after Google OAuth connection.
- Conflict checking before meeting booking.
- Materials flow to append links/Drive files to event descriptions.
- Google backend service for OAuth + Calendar + Drive APIs.
- Reminder pipeline:
  - hourly scheduler checks upcoming tasks
  - Slack reminders
  - email reminders (requires Gmail API desktop credentials/token setup).
- Streamlit board for DB overview (this week, open tasks, reminder readiness).

## Architecture

- `app.py`  
  Flask Slack event handler, orchestration flow, meeting booking/materials, scheduler startup.
- `services/google_api.py`  
  FastAPI service for Google OAuth, Calendar, and Drive endpoints.
- `agent/parser.py`, `agent/prompts.py`  
  Gemini extraction + clarification behavior.
- `agent/scheduler.py`  
  Hourly reminder scheduler (Slack + email).
- `utils/slack.py`, `utils/gmail_utils.py`, `utils/gemini.py`, `utils/time.py`  
  Integrations and shared helpers.
- `databases/db.py`, `databases/tasks.sql`, `databases/profiles.sql`  
  PostgreSQL persistence.
- `frontend/streamlit_app.py`  
  Streamlit dashboard over DB state.

## Prerequisites

- Python 3.12+
- PostgreSQL database
- Slack app and bot token (at least `app_mentions:read`, `chat:write`, `users:read`)
- Gemini API key
- Google OAuth client credentials for Calendar/Drive backend
- ngrok for local Slack event tunneling

## Environment

Use root `.env` (and optionally `agent-backend/.env`) with values such as:

```env
DATABASE_URL=...
GEMINI_API_KEY=...
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
BACKEND_API_KEY=...
STATE_SIGNING_SECRET=...
APP_EXTERNAL_URL=http://localhost:8000
API_ENDPOINT=http://localhost:8000
```

## Install

```bash
pip install -r <your requirements export>  # optional style
pip install apscheduler streamlit tzdata
```

Or your existing project sync workflow (`uv sync` / lock-based flow).

## Local Run (All Services)

Open separate terminals.

1) Flask bot:

```bash
flask --app app run --port 3000 --reload
```

2) FastAPI Google backend:

```bash
uvicorn services.google_api:app --host 127.0.0.1 --port 8000
```

3) ngrok tunnel to Flask:

```bash
ngrok http 3000
```

If `ngrok` is not in PATH, run by full executable path.

4) Streamlit overview board:

```bash
streamlit run frontend/streamlit_app.py
```

Set Slack Event Subscriptions URL to:

```text
https://<ngrok-id>.ngrok-free.app/slack/events
```

## Email Reminder Setup Notes

Email reminders are enabled in code, but Gmail desktop OAuth must be prepared on the runtime machine:

- `utils/gmail_utils.py` expects:
  - `client_secrets_desktop.json` present locally.
  - generated `gmail_token.json` after first interactive auth.
- Scheduler runs hourly and sends reminder emails for tasks due within 24 hours to resolved owner emails.

## Quick Test Flow

1) Mention bot in Slack with a task/meeting message.
2) Confirm task saved message and DB insert.
3) For meetings, connect Google via link and ask for suggestions.
4) Book a suggested time and verify event creation.
5) Open Streamlit board to confirm task visibility.
6) Create a near-term task and verify reminder behavior in Flask logs/inbox.
