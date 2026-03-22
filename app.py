from dotenv import load_dotenv
# Load environment variables from .env file in entry point of application
# DISCLAIMER: Must be done before the utils.slack and utils.gemini imports
load_dotenv()
from flask import Flask, request, jsonify
from agent.parser import process_message, process_clarification
from databases.db import save_task, init_db
from utils.slack import send_slack_message_with_fallback, FALLBACK_MESSAGE, get_user_timezone, BOT_USER_ID
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from collections import OrderedDict, defaultdict
import threading
import re
import os
import json
import time

app = Flask(__name__)
init_db()

verifier = SignatureVerifier(os.getenv("SLACK_SIGNING_SECRET"))
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# --- Duplicate event prevention ---
# For bigger scale, use a DB with a unique constraint on event_id.
# 1. Resets on restart — Slack may retry unprocessed events causing duplicates
# 2. Not thread safe — multiple threads could pass the duplicate check simultaneously
# 3. Fixed size is arbitrary
processed_events = OrderedDict()
MAX_EVENTS = 1000

def is_duplicate(event_id: str) -> bool:
    if event_id in processed_events:
        return True
    processed_events[event_id] = True
    if len(processed_events) > MAX_EVENTS:
        processed_events.popitem(last=False)
    return False

# --- Rate limiting ---
# Same caveats as processed_events — in-memory, resets on restart, not thread safe.
user_request_times = defaultdict(list)
MAX_REQUESTS = 5
WINDOW_SECONDS = 60

def is_rate_limited(user_id: str) -> bool:
    now = time.time()
    user_request_times[user_id] = [
        t for t in user_request_times[user_id]
        if now - t < WINDOW_SECONDS
    ]
    if len(user_request_times[user_id]) >= MAX_REQUESTS:
        return True
    user_request_times[user_id].append(now)
    return False

# --- Session management ---
# - Keyed by thread_ts since app_mention replies stay in the same thread.
# - In-memory — sessions are lost on restart, which is acceptable since abandoned tasks are never saved.
# - Sessions expire after 60 seconds of inactivity. Expired sessions cannot be resumed
#   -> user must start a new thread to discourage overly long threads.
sessions = {}
MAX_SESSIONS = 500
SESSION_TIMEOUT = 60

def get_session(thread_ts: str) -> dict | None:
    session = sessions.get(thread_ts)
    if session is None:
        return None
    if time.time() - session.get("last_active", 0) > SESSION_TIMEOUT:
        sessions.pop(thread_ts)
        return None
    return session

def handle_event(text: str, channel: str, thread_ts: str, timezone: str) -> None:
    try:
        session = get_session(thread_ts)
        if session:
            # Continuation — merge reply into existing session
            task_data = process_clarification(text, session["task_data"], channel, timezone, thread_ts)
        else:
            # Fresh extraction
            task_data = process_message(text, channel, timezone, thread_ts)
            # Some error occurred during processing
            if not task_data: # process_message already sent the fallback
                sessions.pop(thread_ts, None)
                return

        if task_data.get("missing_infos"):
            # Still needs clarification, keep session alive and update last_active
            sessions[thread_ts] = {
                "task_data": task_data,
                "timezone": timezone,
                "last_active": time.time()
            }
        else:
            # Complete — save and clear session
            sessions.pop(thread_ts, None)
            save_task(task_data)
            print(f"Task saved: {json.dumps(task_data, indent=2)}")
            send_slack_message_with_fallback(channel, "Got it! Task saved.", thread_ts=thread_ts)

    except Exception as e:
        print(f"Error processing message: {e}")
        sessions.pop(thread_ts, None)
        send_slack_message_with_fallback(channel, FALLBACK_MESSAGE, thread_ts=thread_ts)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    # Guard against oversized payloads before reading body
    if request.content_length and request.content_length > 1_000_000: # 1 MB limit
        return "", 413
    # Verify the request is from Slack and not a replay attack
    if not verifier.is_valid_request(request.get_data(), request.headers):
        return "", 403

    data = request.get_json()

    # Slack URL verification challenge
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})

    if "event" in data:
        event = data["event"]
        user_id = event.get("user")
        channel = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        print(f"thread_ts: {event.get('thread_ts')}, ts: {event.get('ts')}, using: {thread_ts}")
        # Ignore bot messages and events without a user
        if event.get("subtype") == "bot_message" or not user_id:
            return "", 200

        if event.get("type") == "app_mention":
            event_id = data.get("event_id")
            # Just ack the request and do processing in background to avoid timeouts
            if is_duplicate(event_id):
                return "", 200

            if is_rate_limited(user_id):
                send_slack_message_with_fallback(
                    channel,
                    "You're sending too many requests. Please slow down.",
                    thread_ts=thread_ts
                )
                return "", 200

            if len(sessions) >= MAX_SESSIONS:
                send_slack_message_with_fallback(
                    channel,
                    "I'm currently at capacity. Please try again later.",
                    thread_ts=thread_ts
                )
                return "", 200

            # Check if session exists but expired
            if thread_ts in sessions and get_session(thread_ts) is None:
                send_slack_message_with_fallback(
                    channel,
                    "This conversation has timed out. Please start a new thread to create a task.",
                    thread_ts=thread_ts
                )
                return "", 200

            # Strip only the bot mention to preserve other mentions like owners
            text = event.get("text", "")
            cleaned_text = re.sub(rf"<@{BOT_USER_ID}>", "", text, count=1).strip()
            timezone = get_user_timezone(user_id)
            print(cleaned_text)
            # Acknowledge immediately and process in background
            # per https://docs.slack.dev/interactivity/handling-user-interaction/#acknowledgment_response
            threading.Thread(
                target=handle_event,
                args=(cleaned_text, channel, thread_ts, timezone)
            ).start()

    return "", 200

if __name__ == "__main__":
    # For auto reload, run: flask --app app run --port 3000 --reload
    app.run(port=3000)