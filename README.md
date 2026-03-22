# TaskPilot

An AI-powered Slack bot that helps teams track tasks and deadlines by extracting structured task data from natural language messages.

## Features
- Extracts task details (title, description, owners, deadline, urgency) from Slack messages
- Clarification loop — asks follow-up questions for missing or ambiguous fields
- Automatically resolves plain name mentions to Slack user IDs via workspace search
- Timezone-aware deadline parsing
- Session management per thread with 60 second inactivity timeout

## Project Structure
```
agent/
    parser.py       — core agent logic, extraction and clarification loop
    prompts.py      — Gemini prompt templates
utils/
    gemini.py       — Gemini API client and extraction schema
    slack.py        — Slack client, messaging helpers, workspace member search
    time.py         — deadline validation
databases/
    db.py           — database connection and task persistence
app.py              — Flask server, Slack event listener, session management
```

## Setup

### Prerequisites
- Python 3.12+
- A Slack app with the following bot token scopes:
  - `app_mentions:read`
  - `chat:write`
  - `users:read`
- A Gemini API key

### Installation
```bash
uv sync
```

### Environment Variables
Create a `.env` file in the root directory:
```
GEMINI_API_KEY=your_gemini_api_key
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret
```

### Running Locally
```bash
# Start the Flask server
flask --app app run --port 3000 --reload

# In a separate terminal, expose the server to the internet
ngrok http 3000
```

Set the ngrok URL as your Slack app's event subscription URL:
```
https://<ngrok-id>.ngrok-free.app/slack/events
```

## Usage
Invite the bot to a channel and tag it with a task:
```
@TaskPilot remind @john to fix the login bug by Friday, high urgency
```

The bot will extract the task details and ask clarifying questions if anything is missing. All replies must tag the bot:
```
@TaskPilot next Friday
@TaskPilot high urgency
```

Sessions should expire after 60 seconds of inactivity. If a session times out, start a new thread.