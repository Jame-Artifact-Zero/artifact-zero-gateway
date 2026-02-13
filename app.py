import os
from flask import Flask, request, jsonify, render_template
from openai import OpenAI

app = Flask(__name__)

# ==========================
# ENVIRONMENT VARIABLES
# ==========================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload received"}), 400

    user_input = data.get("input")

    if not user_input:
        return jsonify({"error": "No input provided"}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are NTI. You perform structural clarity analysis only. Remove emotional framing. Identify distortions, leverage points, signal separation, execution vectors, and structural instability."
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
