from flask import Flask, request, jsonify
from agent.agent import process_message
import re

processed_events = set()

app = Flask(__name__)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json()

    # Slack URL verification
    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})

    # Handle actual Slack events
    if "event" in data:
        event = data["event"]

        # Ignore bot messages
        if event.get("subtype") == "bot_message":
            return "", 200

        # Only handle mentions
        if event.get("type") == "app_mention":
            text = event.get("text", "")

            print("\nRaw Slack message:")
            print(text)

            # Remove the bot mention
            cleaned_text = re.sub(r"<@[^>]+>", "", text).strip()

            print("\nCleaned message:")
            print(cleaned_text)
            event_id = data.get("event_id")

            if event_id in processed_events:
                return "", 200  # Ignore duplicate

            processed_events.add(event_id)

            task_data = process_message(cleaned_text)

            print("\nReturned task data:")
            print(task_data)

    return "", 200


if __name__ == "__main__":
    app.run(port=3000)