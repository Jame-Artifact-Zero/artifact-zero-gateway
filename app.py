import os
from flask import Flask, request, jsonify, render_template
from openai import OpenAI

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ------------------------------
# DEMO RUN (Raw OpenAI Response)
# ------------------------------
@app.route("/demo-run", methods=["POST"])
def demo_run():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload received"}), 400

    prompt = data.get("prompt")

    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )

        output = response.choices[0].message.content
        return jsonify({"output": output})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------
# NTI RUN (Framing Exposure)
# ------------------------------
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
                    "content": (
                        "You analyze AI responses and expose hidden framing in plain language.\n\n"
                        "Rules:\n"
                        "- No academic language.\n"
                        "- No jargon.\n"
                        "- Short.\n"
                        "- Maximum 6 bullets total.\n"
                        "- Format exactly as:\n\n"
                        "What It Did:\n"
                        "• bullet\n"
                        "• bullet\n\n"
                        "What That Means:\n"
                        "• bullet\n"
                        "• bullet\n\n"
                        "Make it clear. Make it readable. Make it screenshot-worthy."
                    )
                },
                {
                    "role": "user",
                    "content": user_input
                }
            ],
            temperature=0.2
        )

        output = response.choices[0].message.content
        return jsonify({"output": output})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
