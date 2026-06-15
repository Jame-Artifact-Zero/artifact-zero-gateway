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
#   - relay_health() includes active_sessions (from RDS, accurate across ECS tasks)
#   - relay_health() includes session_store: "rds" or "memory"
#   - relay_health() includes window_calculation: "raw_chars" (diagnostic field)
#
# p0069 patent-core relay naming/wiring:
#   - relay_session_call() explicitly names existence, S0, R, and S1
#   - process_relay() remains Phi(Q, S0) and is not modified
#   - record_exchange() remains Psi(S0, Q, R) and is not modified
#   - S1 is returned in the relay JSON response

import json
import time
import uuid
from datetime import datetime, timezone
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
    database = None
    _USE_DB = False

# ─── Import require_api_key from shared auth module ───────────────────────
from api_auth import require_api_key
_HAS_AUTH = True

# ─── Patent-core imports ───────────────────────────────────────────────────
# These are naming/serialization additions around the existing relay chain.
# They do not add a second compute path and do not call the canonical patent
# runtime because process_relay() and record_exchange() are the existing Phi/Psi.
try:
    from patent_core import (
        LayeredExistenceCoordinate,
        PhysicalCoordinate,
        RelationalCoordinate,
        SensoryCoordinate,
        TemporalCoordinate,
        OperationalCoordinate,
        IdentityCoordinate,
    )
    _HAS_PATENT_CORE = True
except Exception as e:
    _HAS_PATENT_CORE = False
    _PATENT_CORE_IMPORT_ERROR = str(e)


# ─── Governance profile DB helpers ─────────────────────────────────────────

def _get_governance_profile(api_key_id: str) -> dict:
    """Load stored governance profile for this API key. Returns {} if none or column missing."""
    if not _USE_DB:
        return {}
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT governance_profile FROM api_keys WHERE id = %s",
            (api_key_id,)
        )
        row = cur.fetchone()
        conn.close()
        if row:
            val = row[0]
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
        cur.execute(
            "UPDATE api_keys SET governance_profile = %s WHERE id = %s",
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_as_dict(value):
    """Return a JSON-safe representation for patent-core objects."""
    if hasattr(value, "as_dict"):
        return value.as_dict()
    return value


def _build_relay_existence(session_id: str, text: str, when: str):
    """
    Declare exists(where, when, for, why) for the relay exchange.

    Important: this names the existing relay chain. It does not add a second
    computation path and does not call the canonical patent runtime.
    """
    if not _HAS_PATENT_CORE:
        return {
            "status": "unavailable",
            "error": _PATENT_CORE_IMPORT_ERROR,
            "where": "relay/session",
            "when": when,
            "for": "relay_exchange",
            "for_": "relay_exchange",
            "why": "stateful governed relay response",
        }

    existence = LayeredExistenceCoordinate(
        where="relay/session",
        when=when,
        for_="relay_exchange",
        why="stateful governed relay response",
        physical=PhysicalCoordinate(
            location="relay/session",
            environment="enclosed",
            constraints=["api_key_required", "stateful_session"],
        ),
        relational=RelationalCoordinate(
            working_with="human",
            trust_level="user",
            declared_by=session_id,
        ),
        sensory=SensoryCoordinate(
            inputs=["text"],
            missing_inputs=[],
            fidelity="high",
            latency="real_time",
        ),
        temporal=TemporalCoordinate(
            mode="real_time",
            when=when,
        ),
        operational=OperationalCoordinate(
            objective="relay_exchange",
            output_constraints=["governed_response", "session_state_update"],
            acceptable_failure_modes=["gated", "llm_error", "error"],
            cost_of_error="medium",
        ),
        identity=IdentityCoordinate(
            receiver=session_id,
            receiver_knows=None,
            receiver_needs="governed relay response",
            receiver_asked=text[:500],
        ),
    )
    return _safe_as_dict(existence)


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/relay  (single stateless relay call)
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay", methods=["POST"])
@require_api_key
def relay_call():
    data = request.get_json(force=True, silent=True) or {}

    text = data.get("text", "").strip()
    if not text:
        return jsonify({"status": "error", "error": "text required"}), 400

    ai_provider = data.get("ai_provider", "")
    ai_key = data.get("ai_key", "")
    if not ai_provider or not ai_key:
        return jsonify({"status": "error", "error": "ai_provider and ai_key required"}), 400

    api_key_id = getattr(request, "_api_key_id", "unknown")
    stored_profile = _get_governance_profile(api_key_id)
    governance = resolve_governance(data.get("governance"), stored_profile)

    result = process_relay(
        text=text,
        ai_provider=ai_provider,
        ai_key=ai_key,
        ai_model=data.get("ai_model"),
        system_prompt=data.get("system_prompt", "You are a helpful assistant."),
        governance=governance,
        webhook_url=data.get("webhook_url"),
        request_id=str(uuid.uuid4()),
    )

    _log_relay_usage(
        api_key_id=api_key_id,
        request_id=result.get("request_id", ""),
        provider=ai_provider,
        latency_ms=result.get("latency_ms", 0),
        status=result.get("status", "error"),
    )

    return jsonify(result)


# ═══════════════════════════════════════════════════════════════════════════
# POST /api/v1/relay/batch
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/batch", methods=["POST"])
@require_api_key
def relay_batch():
    data = request.get_json(force=True, silent=True) or {}

    texts = data.get("texts") or []
    if not isinstance(texts, list) or not texts:
        return jsonify({"status": "error", "error": "texts must be a non-empty list"}), 400
    if len(texts) > 50:
        return jsonify({"status": "error", "error": "batch limit is 50 texts"}), 400

    ai_provider = data.get("ai_provider", "")
    ai_key = data.get("ai_key", "")
    if not ai_provider or not ai_key:
        return jsonify({"status": "error", "error": "ai_provider and ai_key required"}), 400

    api_key_id = getattr(request, "_api_key_id", "unknown")
    stored_profile = _get_governance_profile(api_key_id)
    governance = resolve_governance(data.get("governance"), stored_profile)

    results = []
    for idx, item in enumerate(texts):
        text = str(item or "").strip()
        if not text:
            results.append({"index": idx, "status": "error", "error": "text required"})
            continue

        result = process_relay(
            text=text,
            ai_provider=ai_provider,
            ai_key=ai_key,
            ai_model=data.get("ai_model"),
            system_prompt=data.get("system_prompt", "You are a helpful assistant."),
            governance=governance,
            webhook_url=None,
            request_id=str(uuid.uuid4()),
        )
        result["index"] = idx
        results.append(result)

        _log_relay_usage(
            api_key_id=api_key_id,
            request_id=result.get("request_id", ""),
            provider=ai_provider,
            latency_ms=result.get("latency_ms", 0),
            status=result.get("status", "error"),
        )

    return jsonify({
        "status": "ok",
        "version": NTI_RELAY_VERSION,
        "count": len(results),
        "results": results,
    })


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/relay/profile
# PUT /api/v1/relay/profile
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/profile", methods=["GET"])
@require_api_key
def relay_profile_get():
    api_key_id = getattr(request, "_api_key_id", "unknown")
    return jsonify({
        "status": "ok",
        "profile": _get_governance_profile(api_key_id),
    })


@relay_bp.route("/api/v1/relay/profile", methods=["PUT"])
@require_api_key
def relay_profile_put():
    api_key_id = getattr(request, "_api_key_id", "unknown")
    data = request.get_json(force=True, silent=True) or {}
    profile = resolve_governance(data.get("governance") or data, {})
    ok = _set_governance_profile(api_key_id, profile)
    return jsonify({
        "status": "ok" if ok else "error",
        "saved": ok,
        "profile": profile,
    })


# ═══════════════════════════════════════════════════════════════════════════
# GET /api/v1/relay/health  (no auth)
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
# Formal patent naming around existing relay chain:
#   exists(where, when, for, why)
#   -> S0 = get_history(session_id)
#   -> Phi(Q, S0) = process_relay(...)
#   -> R = governed_response
#   -> Psi(S0, Q, R) = record_exchange(...)
#   -> S1 = session_meta["s1_state"]
# ═══════════════════════════════════════════════════════════════════════════

@relay_bp.route("/api/v1/relay/session", methods=["POST"])
@require_api_key
def relay_session_call():
    try:
        from relay_session import get_or_create_session, record_exchange, get_history
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

    # exists(where, when, for, why)
    when = _utc_now()
    existence = _build_relay_existence(session_id=session_id, text=text, when=when)

    # Build system prompt — prepend blob if window was just reset
    base_system_prompt = data.get("system_prompt", "You are a helpful assistant.")
    thread = get_or_create_session(session_id, label=label)
    pending_blob = thread.last_blob()
    if pending_blob and thread.relay_number > 1:
        system_prompt = pending_blob.to_prompt() + "\n\n" + base_system_prompt
    else:
        system_prompt = base_system_prompt

    # S0 — prior state before Phi executes
    s0 = get_history(session_id)

    # Phi(Q, S0) — existing standard relay pipeline: v2 gate -> LLM -> v3 governance
    relay_result = process_relay(
        text=text,
        ai_provider=ai_provider,
        ai_key=ai_key,
        ai_model=ai_model,
        system_prompt=system_prompt,
        governance=governance,
        webhook_url=webhook_url,
        request_id=str(uuid.uuid4()),
        history=s0,
    )

    # R — governed response returned by Phi
    R = relay_result.get("governed_response")

    # Psi(S0, Q, R) — record exchange in session thread (persists to RDS via p0040/p0068)
    ai_response = R or relay_result.get("error", "")
    session_meta = record_exchange(
        session_id=session_id,
        human_text=text,
        ai_response=ai_response,
        label=label,
    )

    # S1 — post-Psi full simulated-thread state
    s1_state = session_meta["s1_state"]

    relay_result["session"] = {
        "session_id": session_id,
        "window_pct": session_meta["window_pct"],
        "window_status": session_meta["window_status"],
        "relay_number": session_meta["relay_number"],
        "relay_triggered": session_meta["relay_triggered"],
        "total_messages": session_meta["total_messages"],
    }

    # Patent-core names exposed in the JSON response.
    relay_result["existence"] = existence
    relay_result["s0_count"] = len(s0)
    relay_result["R"] = R
    relay_result["s1_count"] = s1_state.get("history_count", 0)
    relay_result["s1"] = s1_state

    _log_relay_usage(
        api_key_id=api_key_id,
        request_id=relay_result.get("request_id", ""),
        provider=ai_provider,
        latency_ms=relay_result.get("latency_ms", 0),
        status=relay_result.get("status", "error"),
    )

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
