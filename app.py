import os
from flask import Flask, request, jsonify, render_template
from openai import OpenAI

app = Flask(__name__)

# ==========================
# ENVIRONMENT VARIABLES
# ==========================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
NTI_AUTH_TOKEN = os.getenv("NTI_AUTH_TOKEN")

if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)


# ==========================
# ROUTES
# ==========================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/nti", methods=["POST"])
def nti():
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        return jsonify({"error": "Unauthorized"}), 401

    token = auth_header.replace("Bearer ", "")

    if token != NTI_AUTH_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    user_input = data.get("input")

    if not user_input:
        return jsonify({"error": "No input provided"}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are NTI. You do structural clarity analysis only. Remove emotional guidance. Identify structural signals, leverage points, distortions, and execution vectors."
                },
                {
                    "role": "user",
                    "content": user_input
                }
            ],
            temperature=0.3
        )

        output = response.choices[0].message.content

        return jsonify({"output": output})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
