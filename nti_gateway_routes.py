"""
nti_gateway_routes.py

Flask blueprint for The Gateway — single API entry point for all integrations.

Register in app.py:
    from nti_gateway_routes import gateway_bp
    app.register_blueprint(gateway_bp)

Endpoints:
    POST /api/v1/gateway            — single entry for all 19 integrations
    POST /api/v1/gateway/batch      — batch scoring up to 500 texts
    POST /api/v1/gateway/webhook    — register webhook
    DELETE /api/v1/gateway/webhook  — delete webhook
    GET  /api/v1/gateway/health     — no auth, monitoring ping
"""

from flask import Blueprint, request, jsonify
from nti_gateway import process, score_batch, register_webhook, delete_webhook

gateway_bp = Blueprint("gateway_bp", __name__)


# ── AUTH ──────────────────────────────────────────────────────────────────────

def _auth():
    key = (
        request.headers.get("X-API-Key") or
        request.headers.get("Authorization", "").replace("Bearer ", "") or
        request.args.get("api_key", "")
    )
    if not key:
        return None
    return {"account_id": key[:8], "user_id": None, "user_email": None}


# ── POST /api/v1/gateway ──────────────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway", methods=["POST"])
def api_gateway():
    """
    Single entry point. Every integration posts here.

    Body:
      text     (str)   — required
      surface  (str)   — "outlook_compose" | "gmail_compose" | "salesforce" | etc.
      rewrite  (bool)  — optional governed rewrite
      draft_reply (bool) — optional governed draft reply (read surfaces)
      stamp    (bool)  — generate stamp if score >= 80
      user_id  (str)   — optional
      metadata (dict)  — passthrough
    """
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    if len(text) > 50000:
        return jsonify({"error": "text exceeds 50,000 character limit"}), 400

    result = process(
        text=text,
        surface=body.get("surface", "api"),
        account_id=acct["account_id"],
        user_id=body.get("user_id") or acct["user_id"],
        user_email=body.get("user_email") or acct["user_email"],
        request_id=body.get("request_id"),
        options={
            "rewrite":     bool(body.get("rewrite")),
            "draft_reply": bool(body.get("draft_reply")),
            "stamp":       bool(body.get("stamp")),
            "metadata":    body.get("metadata"),
        }
    )

    status = 200 if result.get("ok") else 400
    return jsonify(result), status


# ── POST /api/v1/gateway/batch ────────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway/batch", methods=["POST"])
def api_gateway_batch():
    """Batch scoring. Up to 500 texts per request."""
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    texts = body.get("texts", [])
    if not isinstance(texts, list) or len(texts) == 0:
        return jsonify({"error": "texts array required"}), 400

    result = score_batch(
        texts=texts,
        surface=body.get("surface", "batch"),
        account_id=acct["account_id"],
        user_id=body.get("user_id"),
    )

    return jsonify(result), 200 if result.get("ok") else 400


# ── POST /api/v1/gateway/webhook ──────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway/webhook", methods=["POST"])
def api_register_webhook():
    """Register a webhook URL for check.complete events."""
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    events = body.get("events", ["check.complete"])
    secret = body.get("secret", "")

    if not url or not url.startswith("https://"):
        return jsonify({"error": "url required and must be https"}), 400

    wid = register_webhook(
        account_id=acct["account_id"],
        url=url,
        events=events,
        secret=secret,
    )

    return jsonify({"ok": True, "webhook_id": wid, "url": url, "events": events}), 201


# ── DELETE /api/v1/gateway/webhook ────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway/webhook", methods=["DELETE"])
def api_delete_webhook():
    """Delete a registered webhook."""
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    wid = body.get("webhook_id", "")
    if not wid:
        return jsonify({"error": "webhook_id required"}), 400

    deleted = delete_webhook(acct["account_id"], wid)
    return jsonify({"ok": deleted})


# ── GET /api/v1/gateway/health ────────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway/health", methods=["GET"])
def api_gateway_health():
    """No auth. Used by monitoring."""
    return jsonify({"ok": True, "service": "nti-gateway", "version": "1.0"})
