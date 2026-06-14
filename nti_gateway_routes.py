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

gateway_bp = Blueprint("nti_gateway_bp", __name__)


# ── AUTH ──────────────────────────────────────────────────────────────────────

def _auth():
    """
    Authenticate the gateway API key against PostgreSQL and return
    the real account and user identity.
    """
    api_key = (
        request.headers.get("X-API-Key")
        or request.args.get("api_key")
    )

    if not api_key:
        return None

    conn = database.db_connect()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                k.id,
                k.owner_email,
                k.active,
                u.id,
                u.email
            FROM api_keys AS k
            LEFT JOIN users AS u
                ON LOWER(u.email) = LOWER(k.owner_email)
            WHERE k.id = %s
            LIMIT 1
            """,
            (api_key,),
        )

        row = cur.fetchone()

        if not row:
            return None

        active = row[2]
        user_id = row[3]
        user_email = row[4] or row[1]

        if not active or not user_id or not user_email:
            return None

        cur.execute(
            """
            SELECT id
            FROM accounts
            WHERE owner_user_id = %s
            LIMIT 1
            """,
            (user_id,),
        )

        account_row = cur.fetchone()

        if account_row:
            account_id = account_row[0]
        else:
            from account import _get_or_create_account

            account_id = _get_or_create_account(
                user_id,
                user_email,
            )

        return {
            "account_id": account_id,
            "user_id": user_id,
            "user_email": user_email,
        }

    except Exception as error:
        print(
            f"[nti_gateway] Authentication lookup failed: {error}",
            flush=True,
        )

        return None

    finally:
        conn.close()

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
    acct = _auth()

    if not acct:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}

    url = (body.get("url") or "").strip()
    events = body.get(
        "events",
        ["check.complete"],
    )
    secret = (body.get("secret") or "").strip()

    if not url or not url.startswith("https://"):
        return jsonify({
            "error": "url must be a valid https:// URL"
        }), 400

    if not isinstance(events, list) or not events:
        return jsonify({
            "error": "events must be a non-empty list"
        }), 400

    if not secret:
        import secrets

        secret = (
            "whsec_"
            + secrets.token_hex(24)
        )

    try:
        wh_id = register_webhook(
            acct["account_id"],
            acct["user_id"],
            url,
            events,
            secret,
        )

    except RuntimeError as error:
        return jsonify({
            "error": str(error)
        }), 500

    except Exception as error:
        print(
            f"[nti_gateway] Webhook registration failed: {error}",
            flush=True,
        )

        return jsonify({
            "error": "Webhook registration failed"
        }), 500

    return jsonify({
        "webhook_id": wh_id,
        "url": url,
        "events": events,
        "secret": secret,
        "message": (
            "Store this secret securely. "
            "It will not be shown again."
        ),
    }), 201


# ── DELETE /api/v1/gateway/webhook ────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway/webhook", methods=["DELETE"])
def api_delete_webhook():
    acct = _auth()

    if not acct:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(silent=True) or {}

    webhook_id = (
        body.get("webhook_id")
        or body.get("id")
        or request.args.get("webhook_id")
    )

    if not webhook_id:
        return jsonify({
            "error": "webhook_id is required"
        }), 400

    deleted = delete_webhook(
        acct["account_id"],
        webhook_id,
    )

    if not deleted:
        return jsonify({
            "error": "Webhook not found"
        }), 404

    return jsonify({
        "deleted": True,
        "webhook_id": webhook_id,
    })


# ── GET /api/v1/gateway/health ────────────────────────────────────────────────

@gateway_bp.route("/api/v1/gateway/health", methods=["GET"])
def api_gateway_health():
    """No auth. Used by monitoring."""
    return jsonify({"ok": True, "service": "nti-gateway", "version": "1.0"})
