from flask import Flask, request, jsonify
from agent.agent import process_message,generate_clarifying_question
import re
from databases.db import save_task,run_agent,init_db
import threading

processed_events = set()

app = Flask(__name__)

init_db()

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

            def handle_task(cleaned_text):
                try:
                    print("Processing message:", cleaned_text)

                    task_data = process_message(cleaned_text)

                    action = run_agent(task_data)

                    if action == "store":
                        save_task(task_data)
                        print("Task saved")

                    elif action == "clarify":
                        try:
                            question = generate_clarifying_question(task_data)
                            print("Clarification:", question)
                        except Exception as e:
                            print("LLM quota hit (clarification skipped):", e)

                    print("\nReturned task data:")
                    print(task_data)

                except Exception as e:
                    print("LLM ERROR:", e)


            threading.Thread(target=handle_task, args=(cleaned_text,)).start()
            return "", 200


if __name__ == "__main__":
    app.run(port=3000)