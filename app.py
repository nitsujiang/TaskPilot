from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.get_json()

    # Debug: print incoming payload
    print("Incoming request:", data)

    # Slack URL verification challenge
    if data and data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge")})

    return "", 200

if __name__ == "__main__":
    app.run(port=3000)