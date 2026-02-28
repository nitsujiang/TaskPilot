from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json()

    if "challenge" in data:
        return jsonify({"challenge": data["challenge"]})

    # Only handle actual events
    if "event" in data:
        event = data["event"]

    # Ignore bot messages
    if event.get("subtype") == "bot_message":
        return "", 200

    # Only handle mentions
    if event.get("type") == "app_mention":
        text = event.get("text", "")
        print("Raw Slack message:")
        print(text)
        
    return "", 200

if __name__ == "__main__":
    app.run(port=3000)