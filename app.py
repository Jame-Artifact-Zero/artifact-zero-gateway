# =========================
# FILE: app.py
# Artifact0.com — Gateway v1.1 (Stripe + 2 runs + Email + Print + Share)
# =========================

import os
import json
import uuid
import time
import secrets
import datetime
from typing import Dict, Any, Optional, Tuple

import requests
import stripe
from flask import Flask, request, jsonify, redirect, url_for, render_template, make_response

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_AVAILABLE = True
except Exception:
    SENDGRID_AVAILABLE = False


# -------------------------
# Config
# -------------------------
app = Flask(__name__)

# Keep this stable so "credits" don't die on every restart.
# For production: set a real random value in Render env.
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-only-change-me")

APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")  # e.g. https://artifact0.com
ENV = os.getenv("ENV", "prod").lower()

# Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")  # MUST be secret key: sk_test_... or sk_live_...
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")      # MUST be price id: price_... (NOT prod_...)
STRIPE_MODE = os.getenv("STRIPE_MODE", "payment")   # payment
STRIPE_SUCCESS_PATH = os.getenv("STRIPE_SUCCESS_PATH", "/success")
STRIPE_CANCEL_PATH = os.getenv("STRIPE_CANCEL_PATH", "/cancel")

# Optional: webhook (not required for v1.1; you can add later)
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Gateway → NTI Runtime
NTI_API_URL = os.getenv("NTI_API_URL", "").rstrip("/")      # e.g. https://artifact-zero.onrender.com/nti
NTI_API_TOKEN = os.getenv("NTI_API_TOKEN")                 # token used to call NTI runtime

# Email (SendGrid)
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
FROM_EMAIL = os.getenv("FROM_EMAIL", "results@artifact0.com")

# Admin controls (for creating/revoking codes manually)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# Limits
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "50000"))
MAX_RUNS_PER_PURCHASE = int(os.getenv("MAX_RUNS_PER_PURCHASE", "2"))  # "give them a second one free"
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))

# Stripe init
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


# -------------------------
# In-memory stores (v1.1)
# NOTE: These reset if Render restarts.
# Next step later: persist to Redis / DB.
# -------------------------
CREDITS: Dict[str, Dict[str, Any]] = {}
REFERRALS: Dict[str, Dict[str, Any]] = {}  # ref_code -> {created, credits_earned}
EMAIL_LOG: Dict[str, Dict[str, Any]] = {}  # request_id -> {to, ts, status}


# -------------------------
# Helpers
# -------------------------
def _now_utc() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _iso(dt: Optional[datetime.datetime] = None) -> str:
    return (dt or _now_utc()).isoformat() + "Z"


def _base_url() -> str:
    # Prefer explicit APP_BASE_URL for clean artifact0.com links
    if APP_BASE_URL:
        return APP_BASE_URL
    # Fallback to request host
    return request.host_url.rstrip("/")


def _new_access_code() -> str:
    return secrets.token_urlsafe(8).upper().replace("-", "").replace("_", "")


def _create_credit(code: str, owner: str, runs: int, hours_valid: int, ref: Optional[str] = None) -> Dict[str, Any]:
    rec = {
        "code": code,
        "owner": owner,
        "created": _iso(),
        "expires": _iso(_now_utc() + datetime.timedelta(hours=hours_valid)),
        "runs_allowed": runs,
        "runs_used": 0,
        "active": True,
        "ref": ref,
    }
    CREDITS[code] = rec
    return rec


def _credit_status(code: str) -> Tuple[bool, str]:
    if not code or code not in CREDITS:
        return False, "CODE_NOT_FOUND"

    rec = CREDITS[code]
    if not rec.get("active", False):
        return False, "CODE_INACTIVE"

    expires = datetime.datetime.fromisoformat(rec["expires"].replace("Z", ""))
    if _now_utc() > expires:
        rec["active"] = False
        return False, "CODE_EXPIRED"

    if rec.get("runs_used", 0) >= rec.get("runs_allowed", 0):
        rec["active"] = False
        return False, "CODE_SPENT"

    return True, "OK"


def _use_credit(code: str) -> None:
    CREDITS[code]["runs_used"] += 1
    if CREDITS[code]["runs_used"] >= CREDITS[code]["runs_allowed"]:
        CREDITS[code]["active"] = False


def _safe_trim(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS]
    return text


def _nti_remote(content: str, mode: str = "default") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Calls your NTI runtime service (separate from gateway).
    Expects runtime contract:
      POST NTI_API_URL
      headers Authorization: Bearer <NTI_API_TOKEN>
      json { "input": "...", "mode": "default|fast|strong" }
    Returns:
      (ok, output_text_or_error, full_json_if_any)
    """
    if not NTI_API_URL or not NTI_API_TOKEN:
        return False, "NTI runtime not configured (missing NTI_API_URL or NTI_API_TOKEN).", None

    headers = {
        "Authorization": f"Bearer {NTI_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"input": content, "mode": mode}

    try:
        r = requests.post(NTI_API_URL, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        return False, f"NTI call failed: {str(e)}", None

    if r.status_code != 200:
        # Keep response body for debugging
        return False, f"NTI Error ({r.status_code}): {r.text}", None

    # Prefer JSON; fallback to raw
    try:
        j = r.json()
        # Your runtime returns {"output": "..."} typically
        out = j.get("output") or j.get("output_text") or json.dumps(j, ensure_ascii=False)
        return True, out, j
    except Exception:
        return True, r.text, None


def _send_email(to_email: str, subject: str, plain_text: str) -> Tuple[bool, str]:
    if not SENDGRID_AVAILABLE:
        return False, "SendGrid library not installed."
    if not SENDGRID_API_KEY:
        return False, "Missing SENDGRID_API_KEY."
    if not FROM_EMAIL:
        return False, "Missing FROM_EMAIL."

    msg = Mail(
        from_email=FROM_EMAIL,
        to_emails=to_email,
        subject=subject,
        plain_text_content=plain_text,
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        resp = sg.send(msg)
        return True, f"sent ({resp.status_code})"
    except Exception as e:
        return False, str(e)


def _require_admin(req) -> bool:
    if not ADMIN_TOKEN:
        return False
    auth = req.headers.get("Authorization", "")
    return auth == f"Bearer {ADMIN_TOKEN}"


def _mk_ref_code() -> str:
    return secrets.token_urlsafe(6).replace("-", "").replace("_", "")


# -------------------------
# Routes — UI
# -------------------------
@app.route("/", methods=["GET"])
def home():
    ref = request.args.get("ref", "").strip() or None
    return render_template(
        "index.html",
        ref=ref,
        base_url=_base_url(),
        max_runs=MAX_RUNS_PER_PURCHASE,
    )


@app.route("/create-checkout-session", methods=["POST"])
def create_checkout_session():
    # Must be POST (prevents the accidental GET 500 you saw)
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return make_response(
            "Stripe not configured. Missing STRIPE_SECRET_KEY or STRIPE_PRICE_ID (must be price_...).",
            500,
        )

    # Store referral in success return so we can credit it
    ref = (request.form.get("ref") or "").strip()
    success_url = f"{_base_url()}{STRIPE_SUCCESS_PATH}?session_id={{CHECKOUT_SESSION_ID}}"
    if ref:
        success_url += f"&ref={ref}"

    cancel_url = f"{_base_url()}{STRIPE_CANCEL_PATH}"

    try:
        session = stripe.checkout.Session.create(
            mode=STRIPE_MODE,
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
        )
        return redirect(session.url, code=303)
    except Exception as e:
        # Show actual Stripe error (fast triage)
        return make_response(f"Stripe error: {str(e)}", 500)


@app.route("/success", methods=["GET"])
def success():
    session_id = request.args.get("session_id", "").strip()
    ref = request.args.get("ref", "").strip() or None

    if not session_id:
        return make_response("Missing session_id.", 400)

    # Minimal verification: fetch session by ID (requires STRIPE_SECRET_KEY)
    if not STRIPE_SECRET_KEY:
        return make_response("Stripe secret key not configured.", 500)

    try:
        sess = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return make_response(f"Could not verify session: {str(e)}", 500)

    if sess.get("payment_status") != "paid":
        return make_response("Payment not confirmed as paid.", 402)

    # Create a code with 2 runs (purchase + 1 free)
    code = _new_access_code()
    owner = sess.get("customer_details", {}).get("email") or "paid-user"
    _create_credit(code=code, owner=owner, runs=MAX_RUNS_PER_PURCHASE, hours_valid=72, ref=ref)

    # Referral credit (lightweight v1.1)
    if ref:
        # If ref code exists, record credit earned (future: issue extra run)
        if ref not in REFERRALS:
            # allow first-seen refs to exist (so link sharing works even before DB)
            REFERRALS[ref] = {"created": _iso(), "credits_earned": 0}
        REFERRALS[ref]["credits_earned"] += 1

    share_ref = _mk_ref_code()
    if share_ref not in REFERRALS:
        REFERRALS[share_ref] = {"created": _iso(), "credits_earned": 0}

    return render_template(
        "success.html",
        code=code,
        share_ref=share_ref,
        base_url=_base_url(),
        max_runs=MAX_RUNS_PER_PURCHASE,
    )


@app.route("/cancel", methods=["GET"])
def cancel():
    return render_template("cancel.html", base_url=_base_url())


@app.route("/run", methods=["POST"])
def run_ui():
    code = (request.form.get("code") or "").strip()
    mode = (request.form.get("mode") or "default").strip().lower()
    email = (request.form.get("email") or "").strip()
    text = _safe_trim(request.form.get("text_input") or "")

    ok, reason = _credit_status(code)
    if not ok:
        return make_response(f"Access code invalid: {reason}", 401)

    if not text:
        return make_response("No text provided.", 400)

    if mode not in ("default", "fast", "strong"):
        mode = "default"

    request_id = str(uuid.uuid4())

    # Consume 1 run
    _use_credit(code)

    nti_ok, nti_out, nti_json = _nti_remote(text, mode=mode)

    # Compose display payload
    result = {
        "request_id": request_id,
        "code": code,
        "mode": mode,
        "input_len": len(text),
        "nti_ok": nti_ok,
        "nti_output": nti_out,
        "nti_raw": nti_json,
        "remaining_runs": max(0, CREDITS[code]["runs_allowed"] - CREDITS[code]["runs_used"]),
        "ts": _iso(),
    }

    # Optional email send (non-blocking would be better later; keep simple now)
    email_status = None
    if email:
        subject = "Artifact Zero — NTI Result"
        ok_send, msg = _send_email(email, subject, nti_out if nti_ok else f"NTI failed: {nti_out}")
        email_status = {"ok": ok_send, "message": msg}
        EMAIL_LOG[request_id] = {"to": email, "ts": _iso(), "status": email_status}

    # Share link: either ref in code record or generated
    share_link = f"{_base_url()}/?ref={(CREDITS[code].get('ref') or '')}".rstrip("=")

    return render_template(
        "result.html",
        base_url=_base_url(),
        result=result,
        email_status=email_status,
        share_link=share_link,
    )


# -------------------------
# Routes — API (optional)
# Keeps your existing /nti JSON API available
# -------------------------
@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok", "ts": _iso()}), 200


@app.route("/nti", methods=["POST"])
def nti_api_gateway():
    """
    Gateway API endpoint.
    Auth: API_AUTH_TOKEN
    Body: {"input": "...", "mode": "default|fast|strong"}
    Returns: JSON including runtime output.
    """
    api_auth = os.getenv("API_AUTH_TOKEN")
    if not api_auth:
        return jsonify({"error": "Missing API_AUTH_TOKEN env var in gateway."}), 500

    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {api_auth}":
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    input_text = _safe_trim(data.get("input") or "")
    mode = (data.get("mode") or "default").strip().lower()

    if not input_text:
        return jsonify({"error": "Missing input"}), 400

    nti_ok, nti_out, nti_json = _nti_remote(input_text, mode=mode)
    return jsonify({
        "ok": nti_ok,
        "mode": mode,
        "output": nti_out,
        "raw": nti_json,
        "ts": _iso(),
    }), (200 if nti_ok else 502)


# -------------------------
# Admin endpoints (optional)
# -------------------------
@app.route("/admin/generate", methods=["POST"])
def admin_generate():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    owner = data.get("owner", "admin")
    runs = int(data.get("runs", MAX_RUNS_PER_PURCHASE))
    hours = int(data.get("hours_valid", 72))

    code = _new_access_code()
    rec = _create_credit(code=code, owner=owner, runs=runs, hours_valid=hours)
    return jsonify(rec), 200


@app.route("/admin/revoke", methods=["POST"])
def admin_revoke():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if code in CREDITS:
        CREDITS[code]["active"] = False
        return jsonify({"status": "revoked", "code": code}), 200
    return jsonify({"error": "Not found"}), 404


@app.route("/admin/state", methods=["GET"])
def admin_state():
    if not _require_admin(request):
        return jsonify({"error": "Unauthorized"}), 401

    return jsonify({
        "credits_count": len(CREDITS),
        "referrals_count": len(REFERRALS),
        "email_log_count": len(EMAIL_LOG),
        "credits": CREDITS,
        "referrals": REFERRALS,
        "email_log": EMAIL_LOG,
    }), 200


# -------------------------
# Local run (Render uses gunicorn)
# -------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
