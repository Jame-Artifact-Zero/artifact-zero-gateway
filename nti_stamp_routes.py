"""
nti_stamp_routes.py

Flask blueprint for NTI Verified Stamp endpoints.

Register in app.py:
    from nti_stamp_routes import stamp_bp
    app.register_blueprint(stamp_bp)

Endpoints:
    POST /api/v1/stamp/generate   — return stamp variants, no log write
    POST /api/v1/stamp/append     — generate + write stamp event to The Log
"""

from flask import Blueprint, request, jsonify
from nti_stamp import generate_all, should_stamp, extract_recipient_domain

stamp_bp = Blueprint("stamp_bp", __name__)


# ── AUTH (reuse same pattern as log_bp) ──────────────────────────────────────

def _get_account(api_key: str):
    if not api_key:
        return None
    return {"account_id": api_key[:8], "user_id": None, "user_email": None}


def _auth():
    key = (
        request.headers.get("X-API-Key") or
        request.headers.get("Authorization", "").replace("Bearer ", "") or
        request.args.get("api_key", "")
    )
    return _get_account(key)


# ── POST /api/v1/stamp/generate ───────────────────────────────────────────────

@stamp_bp.route("/api/v1/stamp/generate", methods=["POST"])
def api_stamp_generate():
    """
    Generate stamp variants without writing to the log.
    Used when the surface wants to preview the stamp before appending.
    """
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    request_id = body.get("request_id", "")
    score = float(body.get("score", 0))

    if not request_id:
        return jsonify({"error": "request_id required"}), 400

    if not should_stamp(score):
        return jsonify({
            "ok": False,
            "reason": f"Score {int(score)} below threshold (80)",
            "stamps": None
        })

    result = generate_all(
        request_id=request_id,
        score=score,
        checked_at=body.get("checked_at"),
    )
    return jsonify({"ok": True, **result})


# ── POST /api/v1/stamp/append ─────────────────────────────────────────────────

@stamp_bp.route("/api/v1/stamp/append", methods=["POST"])
def api_stamp_append():
    """
    Generate stamp + write stamp event to The Log.
    Called when the stamp is actually appended to the email body.
    """
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    request_id = body.get("request_id", "")
    score = float(body.get("score", 0))
    stamp_variant = body.get("variant", "plain")
    to_address = body.get("to", "")

    if not request_id:
        return jsonify({"error": "request_id required"}), 400

    if not should_stamp(score):
        return jsonify({
            "ok": False,
            "reason": f"Score {int(score)} below threshold (80)",
            "stamps": None
        })

    result = generate_all(request_id=request_id, score=score)
    stamp_text = result["stamps"].get(stamp_variant, result["stamps"]["plain"])
    recipient_domain = extract_recipient_domain(to_address)

    # Write stamp event to The Log
    try:
        from nti_log import log_stamp
        log_stamp(
            request_id=request_id,
            score=score,
            stamp_text=stamp_text,
            account_id=acct["account_id"],
            user_id=body.get("user_id") or acct["user_id"],
            recipient_domain=recipient_domain,
        )
    except Exception as e:
        import logging
        logging.getLogger("nti_stamp_routes").warning(f"log_stamp failed: {e}")

    return jsonify({
        "ok": True,
        "stamp_text": stamp_text,
        "variant": stamp_variant,
        **result
    })
