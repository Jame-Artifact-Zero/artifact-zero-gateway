import os
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

app = Flask(__name__)

# Initialize OpenAI client (Render provides env variable)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Simple health route
@app.route("/", methods=["GET"])
def home():
    return {
        "status": "Artifact Zero Gateway Live",
        "service": "NTI",
        "version": "production"
    }, 200


# NTI endpoint (POST only)
@app.route("/nti", methods=["POST"])
def nti():
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return jsonify({"error": "Unauthorized"}), 401

    token = auth_header.split(" ")[1]
    expected_token = os.getenv("NTI_AUTH_TOKEN")

    if token != expected_token:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()

    if not data or "input" not in data:
        return jsonify({"error": "Missing input"}), 400

    user_input = data["input"]

    try:
        response = client.responses.create(
            model="gpt-4.1",
            input=user_input
        )

        return jsonify({
            "output": response.output_text
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
