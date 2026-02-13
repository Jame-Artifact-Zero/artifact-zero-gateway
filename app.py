import os
import json
import time
import uuid
import datetime
from typing import Dict, Any, Optional

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not API_AUTH_TOKEN:
    raise ValueError("Missing API_AUTH_TOKEN env var")
if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY env var")

MODEL_DEFAULT = os.getenv("NTI_MODEL_DEFAULT", "gpt-4.1-mini")
LOG_FILE = os.getenv("NTI_LOG_FILE", "nti_log.jsonl")

client = OpenAI(api_key=OPENAI_API_KEY)
app = Flask(__name__)

PRICING_USD_PER_1M = {
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}

def _utc_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"

def _log(event: Dict[str, Any]) -> None:
    event["ts"] = _utc_iso()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

SYSTEM_CONTRACT = """You are operating inside NTI (No-Tilt Interface).
Rules:
- Deterministic.
- No emotional mirroring.
- No motivational language.
- Explicit assumptions only.
- Short structured output.
"""

def run_nti(input_text: str) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())

    try:
        resp = client.responses.create(
            model=MODEL_DEFAULT,
            temperature=0.0,
            input=[
                {"role": "system", "content": SYSTEM_CONTRACT},
                {"role": "user", "content": input_text},
            ],
            max_output_tokens=800,
        )

        output_text = getattr(resp, "output_text", "") or ""

        usage_obj = getattr(resp, "usage", None)
        usage = {}
        if usage_obj:
            usage = {
                "input_tokens": getattr(usage_obj, "input_tokens", None),
                "output_tokens": getattr(usage_obj, "output_tokens", None),
                "total_tokens": getattr(usage_obj, "total_tokens", None),
            }

        payload = {
            "request_id": request_id,
            "model": MODEL_DEFAULT,
            "input": input_text,
            "output": output_text,
            "usage": usage,
        }

        _log({"type": "success", **payload})
        return payload

    except Exception as e:
        _log({"type": "error", "error": str(e)})
        return {"error": str(e)}

# -----------------------
# ROUTES
# -----------------------

@app.route("/")
def home():
    return send_from_directory(".", "index.html")

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200

@app.route("/nti", methods=["POST"])
def nti_endpoint():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {API_AUTH_TOKEN}":
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    input_text = data.get("input", "")

    if not input_text.strip():
        return jsonify({"error": "No input provided"}), 400

    result = run_nti(input_text=input_text)
    return jsonify(result), 200

# -----------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
