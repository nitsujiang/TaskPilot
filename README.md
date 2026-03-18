# Productivity-1-Agentic-AI
Agentic AI solution to prevent decision drift and action-item amnesia over time from Break Through Tech's Productivity Team 1.

## Team Members
Justin Jiang · Priya Sinha · Meron Oumer · Minh Trinh

---

## Setup

This project uses [uv](https://docs.astral.sh/uv/) for dependency management.

### 1. Install uv

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Install dependencies

```sh
uv sync
```

This creates a virtual environment and installs all dependencies from `uv.lock`.

### 3. Configure environment variables

**Root `.env`** — create a `.env` file in the project root:

```sh
GEMINI_API_KEY=your-gemini-api-key
```

**`agent-backend/.env`** — create a `.env` file inside `agent-backend/`:

```sh
# Postgres connection string (get one from neon.tech)
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require

# Google OAuth credentials (create an OAuth 2.0 Client ID in Google Cloud Console)
# Set redirect URI to: {APP_EXTERNAL_URL}/auth/google/callback
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret

# Public URL of the backend server
# Use http://localhost:8000 locally, your public URL in production
APP_EXTERNAL_URL=http://localhost:8000

# Secret key sent in X-Backend-Key header to protect API routes
BACKEND_API_KEY=your-random-backend-key

# Secret used to sign OAuth state (generate with: openssl rand -hex 32)
STATE_SIGNING_SECRET=your-random-state-secret

# URL the notebook uses to call the backend
API_ENDPOINT=http://localhost:8000

# Gemini API key for the notebook's LLM (get one at aistudio.google.com)
# Do not use quotes around the value
GOOGLE_API_KEY_AI=your-gemini-api-key

# Optional: Gmail address to use as the profile for Calendar/Drive
# PROFILE_EMAIL=your.email@gmail.com
```

> Never commit either `.env` file. They are already listed in `.gitignore`.

### 4. Run the Slack bot

```sh
uv run python app.py
```

The server starts on port 3000 and listens for Slack events.

### 5. Expose the server to Slack (local development)

Slack needs a public URL to send events to. Use [ngrok](https://ngrok.com/) to create a tunnel:

```sh
ngrok http 3000
```

Copy the ngrok URL and set it as your Slack app's event subscription URL:
```
https://<your-ngrok-id>.ngrok.io/slack/events
```

---

## Dependency Management

```sh
uv add <package>           # add a production dependency
uv add --dev <package>     # add a development dependency
uv sync                    # install all dependencies from uv.lock
uv sync --upgrade          # upgrade all dependencies
```