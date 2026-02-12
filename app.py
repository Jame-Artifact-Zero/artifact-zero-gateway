import os
import uuid
import json
import time
import datetime
from typing import Dict, Any, Optional

import stripe
from flask import Flask, request, jsonify, redirect, render_template_string
from dotenv import load_dotenv
from openai import OpenAI

# =========================
# LOAD ENV
# =========================

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")

if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY")
if not STRIPE_SECRET_KEY:
    raise ValueError("Missing STRIPE_SECRET_KEY")
if not STRIPE_PRICE_ID:
    raise ValueError("Missing STRIPE_PRICE_ID")

stripe.api_key = STRIPE_SECRET_KEY
client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)

# =========================
# NTI CONFIG
# =========================

MODEL = "gpt-4.1-mini"

SYSTEM_CONTRACT = """You are operating inside NTI (No-Tilt Interface).
Rules:
- Deterministic
- No motivational language
- No emotional mirroring
- Explicit assumptions
- Structured output
"""

# =========================
# SIMPLE NTI RUN
# =========================

def run_nti(input_text: str) -> Dict[str, Any]:

    resp = client.responses.create(
        model=MODEL,
        temperature=0.0,
        input=[
            {"role": "system", "content": SYSTEM_CONTRACT},
            {"role": "user", "content": input_text},
        ],
        max_output_tokens=800,
    )

    output_text = getattr(resp, "output_text", "") or ""

    usage = {}
    if getattr(resp, "usage", None):
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "total_tokens": resp.usage.total_tokens,
        }

    return {
        "output": output_text,
        "usage": usage,
        "model": MODEL
    }

# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    return """
    <h2>Artifact Zero — NTI Live</h2>
    <p>$1 to run NTI.</p>
    <a href="/create-checkout-session">
        <button>Pay $1</button>
    </a>
    """

@app.route("/create-checkout-session")
def create_checkout_session():
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price": STRIPE_PRICE_ID,
            "quantity": 1,
        }],
        mode="payment",
        success_url=request.host_url + "nti-form",
        cancel_url=request.host_url,
    )
    return redirect(session.url, code=303)

@app.route("/nti-form")
def nti_form():
    return """
    <h3>Run NTI</h3>
    <form action="/run" method="post">
        <textarea name="input" rows="8" cols="60" placeholder="Paste text here"></textarea><br><br>
        <button type="submit">Run NTI</button>
    </form>
    """

@app.route("/run", methods=["POST"])
def run_nti_endpoint():
    input_text = request.form.get("input", "")

    if not input_text.strip():
        return "No input provided.", 400

    result = run_nti(input_text)

    return render_template_string("""
        <h3>NTI Result</h3>
        <pre>{{output}}</pre>
        <p><strong>Model:</strong> {{model}}</p>
        <p><strong>Usage:</strong> {{usage}}</p>
        <a href="/">Run Again</a>
    """,
    output=result["output"],
    model=result["model"],
    usage=result["usage"]
    )

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200

# =========================
# START
# =========================

if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
