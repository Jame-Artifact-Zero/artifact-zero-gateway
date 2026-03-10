# nti_relay_routes.py
# NTI Customer AI Relay — Flask Blueprint
# Register in app.py:
#   from nti_relay_routes import relay_bp
#   app.register_blueprint(relay_bp)
#
# Endpoints:
#   POST /api/v1/relay          - single relay call
#   POST /api/v1/relay/batch    - up to 50 texts, shared governance
#   GET  /api/v1/relay/profile  - get stored governance profile for API key
#   PUT  /api/v1/relay/profile  - store governance profile for API key
#   GET  /api/v1/relay/health   - no-auth health check

import json
import time
import uuid
from flask import Blueprint, request, jsonify

from nti_relay import (
    process_relay,
    resolve_governance,
    dispatch_webhook,
    SUPPORTED_PROVIDERS,
    NTI_RELAY_VERSION,
)

relay_bp = Blueprint("relay_bp", __name__)

# ─── Import shared db module (repo uses: import db as database) ────────────
try:
    import db as database
    _USE_DB = True
except ImportError:
    _USE_DB = False

# ─── Import require_api_key from app (defined at module level in app.py) ───
try:
    from app import require_api_key
    _HAS_AUTH = True
except ImportError:
    # Fallback for test context
    import functools
    def require_api_key(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            request._api_key_id = "dev"
            request._api_tier = "dev"
            return f(*args, **kwargs)
        return wrapper
    _HAS_AUTH = False


# ─── Governance profile DB helpers ─────────────────────────────────────────

def _get_governance_profile(api_key_id: str) -> dict:
    """Load stored governance profile for this API key. Returns {} if none or column missing."""
    if not _USE_DB:
        return {}
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute(
                "SELECT governance_profile FROM api_keys WHERE id = %s",
                (api_key_id,)
            )
        else:
            cur.execute(
                "SELECT governance_profile FROM api_keys WHERE id = ?",
                (api_key_id,)
            )
        row = cur.fetchone()
        conn.close()
        if row:
            val = row[0] if database.USE_PG else row["governance_profile"]
            if val:
                return json.loads(val) if isinstance(val, str) else val
    except Exception as e:
        # Column may not exist yet if migration hasn't run — silent fail
        print(f"[relay] Profile load error (run migration?): {e}", flush=True)
    return {}


def _set_governance_profile(api_key_id: str, profile: dict) -> bool:
    """Store governance profile JSON for this API key."""
    if not _USE_DB:
        return False
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        profile_json = json.dumps(profile)
        if database.USE_PG:
            cur.execute(
                "UPDATE api_keys SET governance_profile = %s WHERE id = %s",
                (profile_json, api_key_id)
            )
        else:
            cur.execute(
                "UPDATE api_keys SET governance_profile = ? WHERE id = ?",
                (profile_json, api_key_id)
            )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[relay] Profile save error (run migration?): {e}", flush=True)
        return False


def _log_relay_usage(api_key_id: str, request_id: str, provider: str,
                     latency_ms: int, status: str) -> None:
    """Write relay call to api_usage via db.record_api_usage."""
    if not _USE_DB:
        return
    try:
        usage_id = str(uuid.uuid4())
        status_code = 200 if status == "ok" else (422 if status == "gated" else 502)
        database.record_api_usage(usage_id, api_key_id, "/api/v1/relay", latency_ms, status_code)
    except Exception as e:
        print(f"[relay] Usage log error: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/relay
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay", methods=["POST"])
@require_api_key
def relay_single():
    t0 = time.time()
    data = request.get_json(force=True, silent=True) or {}

    text = (data.get("text") or "").strip()
    ai_provider = (data.get("ai_provider") or "").strip().lower()
    ai_key = (data.get("ai_key") or "").strip()

    if not text:
        return jsonify({"error": "text is required"}), 400
    if not ai_provider:
        return jsonify({"error": "ai_provider is required",
                        "supported": sorted(SUPPORTED_PROVIDERS)}), 400
    if not ai_key:
        return jsonify({"error": "ai_key is required — pass your own provider API key"}), 400
    if len(text) > 50000:
        return jsonify({"error": "text exceeds 50,000 character limit"}), 400

    ai_model = (data.get("ai_model") or "").strip() or None
    system_prompt = (data.get("system_prompt") or "You are a helpful assistant.").strip()
    request_gov = data.get("governance") or {}
    webhook_url = (data.get("webhook_url") or "").strip() or None
    request_id = str(uuid.uuid4())
    # session_scope: call | session:{id} | account | shared:{topic}
    # Defaults to call (fully isolated) if not supplied.
    session_scope = (data.get("session_scope") or "call").strip()

    api_key_id = getattr(request, "_api_key_id", "unknown")
    stored_profile = _get_governance_profile(api_key_id)
    governance = resolve_governance(request_gov, stored_profile)

    result = process_relay(
        text=text,
        ai_provider=ai_provider,
        ai_key=ai_key,
        ai_model=ai_model,
        system_prompt=system_prompt,
        governance=governance,
        webhook_url=webhook_url,
        request_id=request_id,
        session_scope=session_scope,
        api_key_id=api_key_id,
    )

    _log_relay_usage(
        api_key_id=api_key_id,
        request_id=request_id,
        provider=ai_provider,
        latency_ms=result.get("latency_ms", int((time.time() - t0) * 1000)),
        status=result.get("status", "ok"),
    )

    status_code = 200
    if result.get("status") == "error":
        status_code = 400
    elif result.get("status") == "gated":
        status_code = 422
    elif result.get("status") == "llm_error":
        status_code = 502

    return jsonify(result), status_code


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/relay/batch
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/batch", methods=["POST"])
@require_api_key
def relay_batch():
    data = request.get_json(force=True, silent=True) or {}

    texts = data.get("texts") or []
    ai_provider = (data.get("ai_provider") or "").strip().lower()
    ai_key = (data.get("ai_key") or "").strip()

    if not texts or not isinstance(texts, list):
        return jsonify({"error": "texts array is required"}), 400
    if len(texts) > 50:
        return jsonify({"error": "batch limit is 50 texts"}), 400
    if not ai_provider:
        return jsonify({"error": "ai_provider is required"}), 400
    if not ai_key:
        return jsonify({"error": "ai_key is required"}), 400

    ai_model = (data.get("ai_model") or "").strip() or None
    system_prompt = (data.get("system_prompt") or "You are a helpful assistant.").strip()
    request_gov = data.get("governance") or {}
    webhook_url = (data.get("webhook_url") or "").strip() or None

    api_key_id = getattr(request, "_api_key_id", "unknown")
    stored_profile = _get_governance_profile(api_key_id)
    governance = resolve_governance(request_gov, stored_profile)
    session_scope = (data.get("session_scope") or "call").strip()

    results = []
    for i, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            results.append({"index": i, "status": "skipped", "error": "empty text"})
            continue

        res = process_relay(
            text=text,
            ai_provider=ai_provider,
            ai_key=ai_key,
            ai_model=ai_model,
            system_prompt=system_prompt,
            governance=governance,
            webhook_url=None,
            request_id=str(uuid.uuid4()),
            session_scope=session_scope,
            api_key_id=api_key_id,
        )
        res["index"] = i
        results.append(res)

    batch_result = {
        "status": "ok",
        "version": NTI_RELAY_VERSION,
        "count": len(results),
        "results": results,
    }

    if webhook_url:
        dispatch_webhook(webhook_url, batch_result)
        batch_result["webhook"] = {"dispatched": True, "url": webhook_url}

    return jsonify(batch_result)


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/relay/profile
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/profile", methods=["GET"])
@require_api_key
def relay_get_profile():
    api_key_id = getattr(request, "_api_key_id", "unknown")
    stored = _get_governance_profile(api_key_id)
    resolved = resolve_governance({}, stored)

    return jsonify({
        "api_key": api_key_id[:8] + "...",
        "stored_profile": stored,
        "resolved_defaults": resolved,
        "supported_providers": sorted(SUPPORTED_PROVIDERS),
        "governance_fields": {
            "audit_threshold": "float 0.0-1.0 (default 0.85)",
            "max_passes": "int 1-5 (default 2)",
            "token_ceiling": "int 100-8000 (default 1000)",
            "gate_mode": "standard | strict | permissive (default standard)",
        },
    })


# ═══════════════════════════════════════════════════════════════════════════
# PUT /api/v1/relay/profile
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/profile", methods=["PUT"])
@require_api_key
def relay_set_profile():
    data = request.get_json(force=True, silent=True) or {}
    profile = resolve_governance(data, {})

    api_key_id = getattr(request, "_api_key_id", "unknown")
    saved = _set_governance_profile(api_key_id, profile)

    if not saved:
        return jsonify({
            "status": "ok",
            "warning": "Profile resolved but not persisted — run nti_relay_migration.sql first.",
            "profile": profile,
        })

    return jsonify({
        "status": "ok",
        "message": "Governance profile saved. Applies to all future relay calls from this API key.",
        "profile": profile,
    })


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/relay/health  (no auth)
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/health", methods=["GET"])
def relay_health():
    return jsonify({
        "status": "ok",
        "version": NTI_RELAY_VERSION,
        "supported_providers": sorted(SUPPORTED_PROVIDERS),
        "endpoints": [
            "POST /api/v1/relay",
            "POST /api/v1/relay/batch",
            "GET  /api/v1/relay/profile",
            "PUT  /api/v1/relay/profile",
            "GET  /api/v1/relay/health",
        ],
    })
