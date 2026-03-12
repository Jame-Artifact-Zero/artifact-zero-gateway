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
#   POST /api/v1/relay/session  - stateful session call
#   GET  /api/v1/relay/session/status - session window + core sequences
#   DELETE /api/v1/relay/session - destroy session
#
# p0040 changes:
#   - relay_health() now includes active_sessions (from RDS, accurate across ECS tasks)
#   - relay_health() now includes session_store: "rds" or "memory"
#   - relay_health() now includes window_calculation: "raw_chars" (diagnostic field)

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

# ─── Import shared db module ───────────────────────────────────────────────
try:
    import db as database
    _USE_DB = True
except ImportError:
    _USE_DB = False

# ─── Import require_api_key from app ──────────────────────────────────────
try:
    from app import require_api_key
    _HAS_AUTH = True
except ImportError:
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
# GET /api/v1/relay/health  (no auth)
# p0040: added active_sessions, session_store, window_calculation fields
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/health", methods=["GET"])
def relay_health():
    # Get active session count — from RDS (accurate across ECS tasks)
    active_sessions = 0
    session_store = "memory"
    try:
        from relay_session import active_session_count, _USE_DB as relay_uses_db
        active_sessions = active_session_count()
        session_store = "rds" if relay_uses_db else "memory"
    except Exception as e:
        print(f"[relay_health] session count error: {e}", flush=True)

    return jsonify({
        "status": "ok",
        "version": NTI_RELAY_VERSION,
        "supported_providers": sorted(SUPPORTED_PROVIDERS),
        "active_sessions": active_sessions,
        "session_store": session_store,
        "window_calculation": "raw_chars",
        "endpoints": [
            "POST /api/v1/relay",
            "POST /api/v1/relay/batch",
            "GET  /api/v1/relay/profile",
            "PUT  /api/v1/relay/profile",
            "GET  /api/v1/relay/health",
            "POST /api/v1/relay/session",
            "GET  /api/v1/relay/session/status",
            "DELETE /api/v1/relay/session",
        ],
    })


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/relay/session
# Stateful relay call — maintains SimulatedThread per session_id.
# Same governance pipeline as /api/v1/relay. Adds window tracking + blob injection.
#
# Required body fields (same as /api/v1/relay, plus session_id):
#   session_id      str   — caller-managed, persists across calls
#   text            str   — human message
#   ai_provider     str
#   ai_key          str
#   ai_model        str   (optional)
#   system_prompt   str   (optional)
#   governance      obj   (optional)
#   webhook_url     str   (optional)
#   label           str   (optional, human-readable session name)
#
# Response additions vs /api/v1/relay:
#   session.window_pct      float  — % of context window used
#   session.window_status   str    — NOMINAL/WATCH/PREPARE/INJECT/CRITICAL
#   session.relay_number    int    — how many times window has been reset
#   session.relay_triggered bool   — blob built and window reset this call
#   session.total_messages  int    — total messages across all relays
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/session", methods=["POST"])
@require_api_key
def relay_session_call():
    try:
        from relay_session import get_or_create_session, record_exchange
    except ImportError as e:
        return jsonify({"status": "error", "error": f"relay_session unavailable: {e}"}), 500

    data = request.get_json(force=True, silent=True) or {}

    session_id = data.get("session_id", "")
    if not session_id or not isinstance(session_id, str):
        return jsonify({"status": "error", "error": "session_id required"}), 400

    text = data.get("text", "").strip()
    if not text:
        return jsonify({"status": "error", "error": "text required"}), 400

    ai_provider = data.get("ai_provider", "")
    ai_key = data.get("ai_key", "")
    if not ai_provider or not ai_key:
        return jsonify({"status": "error", "error": "ai_provider and ai_key required"}), 400

    ai_model = data.get("ai_model")
    webhook_url = data.get("webhook_url")
    label = data.get("label", session_id)

    api_key_id = getattr(request, "_api_key_id", "unknown")
    stored_profile = _get_governance_profile(api_key_id)
    governance = resolve_governance(data.get("governance"), stored_profile)

    # Build system prompt — prepend blob if window was just reset
    base_system_prompt = data.get("system_prompt", "You are a helpful assistant.")
    thread = get_or_create_session(session_id, label=label)
    pending_blob = thread.last_blob()
    if pending_blob and thread.relay_number > 1:
        system_prompt = pending_blob.to_prompt() + "\n\n" + base_system_prompt
    else:
        system_prompt = base_system_prompt

    # Standard relay pipeline: v2 gate -> LLM -> v3 governance
    relay_result = process_relay(
        text=text,
        ai_provider=ai_provider,
        ai_key=ai_key,
        ai_model=ai_model,
        system_prompt=system_prompt,
        governance=governance,
        webhook_url=webhook_url,
        request_id=str(uuid.uuid4()),
    )

    # Record exchange in session thread (persists to RDS via p0040)
    ai_response = relay_result.get("governed_response") or relay_result.get("error", "")
    session_meta = record_exchange(
        session_id=session_id,
        human_text=text,
        ai_response=ai_response,
        label=label,
    )

    relay_result["session"] = {
        "session_id": session_id,
        "window_pct": session_meta["window_pct"],
        "window_status": session_meta["window_status"],
        "relay_number": session_meta["relay_number"],
        "relay_triggered": session_meta["relay_triggered"],
        "total_messages": session_meta["total_messages"],
    }

    return jsonify(relay_result)


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/relay/session/status?session_id=...
# Returns window state and current core sequences for a session.
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/session/status", methods=["GET"])
@require_api_key
def relay_session_status():
    try:
        from relay_session import session_status
    except ImportError as e:
        return jsonify({"status": "error", "error": f"relay_session unavailable: {e}"}), 500

    session_id = request.args.get("session_id", "").strip()
    if not session_id:
        return jsonify({"status": "error", "error": "session_id required"}), 400

    status = session_status(session_id)
    return jsonify({"status": "ok", **status})


# ═══════════════════════════════════════════════════════════════════════════
# DELETE /api/v1/relay/session
# Destroy a session and free its memory.
# Body: { "session_id": "..." }
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/session", methods=["DELETE"])
@require_api_key
def relay_session_destroy():
    try:
        from relay_session import destroy_session
    except ImportError as e:
        return jsonify({"status": "error", "error": f"relay_session unavailable: {e}"}), 500

    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id", "").strip()
    if not session_id:
        return jsonify({"status": "error", "error": "session_id required"}), 400

    destroyed = destroy_session(session_id)
    return jsonify({
        "status": "ok",
        "session_id": session_id,
        "destroyed": destroyed,
    })
