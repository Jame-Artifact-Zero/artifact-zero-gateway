"""
az_relay_memory.py
Flask blueprint — Relay Memory System v3 routes

Register in app.py:
    from az_relay_memory import relay_memory_bp
    app.register_blueprint(relay_memory_bp)

Endpoints:
    POST /relay/inject                    ← main entry: classify + inject + return enriched prompt
    POST /relay/artifact                  ← store canonical artifact (priority required)
    GET  /relay/artifact/<key>            ← retrieve specific artifact
    GET  /relay/artifacts/<topic>         ← all artifacts for topic
    GET  /relay/staged                    ← list staged artifacts awaiting promotion
    POST /relay/staged/<id>/promote       ← manually promote staged artifact to P0
    POST /relay/store                     ← store a message manually
    GET  /relay/history                   ← recent messages
    GET  /relay/search                    ← search message history
    POST /relay/classify                  ← classify without storing
"""

from flask import Blueprint, request, jsonify
from relay_memory import (
    build_injected_prompt,
    store_message,
    store_artifact,
    get_artifact,
    get_artifacts_by_topic,
    get_staged_artifacts,
    promote_staged_artifact,
    get_recent_messages,
    search_messages,
    classify_topic,
    detect_mode,
    P0, P1, P2, P3,
)

relay_memory_bp = Blueprint("relay_memory", __name__, url_prefix="/relay")


# ─────────────────────────────────────────────
# MAIN: Inject + classify
# ─────────────────────────────────────────────
@relay_memory_bp.route("/inject", methods=["POST"])
def inject():
    """
    POST /relay/inject
    Body: { "message": str, "session_id": str (optional) }
    Returns enriched prompt. Send returned "prompt" field to LLM.
    """
    body = request.get_json() or {}
    message = (body.get("message") or "").strip()
    session_id = body.get("session_id", "default")
    if not message:
        return jsonify({"error": "message required"}), 400
    return jsonify(build_injected_prompt(message, session_id))


# ─────────────────────────────────────────────
# ARTIFACT STORAGE
# ─────────────────────────────────────────────
@relay_memory_bp.route("/artifact", methods=["POST"])
def post_artifact():
    """
    POST /relay/artifact
    Body: { "key": str, "topic": str, "content": str, "priority": int (0-3) }

    Priority:
        0 = P0 — deterministic procedure (locked after creation)
        1 = P1 — canonical definition
        2 = P2 — reference (default)
        3 = P3 — history

    P0 artifacts are locked. Subsequent writes are staged for manual promotion.
    """
    body = request.get_json() or {}
    key = (body.get("key") or "").strip()
    topic = (body.get("topic") or "").strip()
    content = (body.get("content") or "").strip()
    priority = body.get("priority", P2)

    if not key or not content:
        return jsonify({"error": "key and content required"}), 400
    if not topic:
        topic = classify_topic(content)

    result = store_artifact(key, topic, content, priority=int(priority))
    return jsonify({"ok": True, **result})


@relay_memory_bp.route("/artifact/<key>", methods=["GET"])
def get_artifact_route(key):
    """GET /relay/artifact/<key> — retrieve specific canonical artifact"""
    artifact = get_artifact(key)
    if not artifact:
        return jsonify({"error": f"artifact '{key}' not found"}), 404
    return jsonify(artifact)


@relay_memory_bp.route("/artifacts/<topic>", methods=["GET"])
def get_artifacts_route(topic):
    """GET /relay/artifacts/<topic> — all artifacts for topic, priority-ordered"""
    artifacts = get_artifacts_by_topic(topic)
    return jsonify({"topic": topic, "count": len(artifacts), "artifacts": artifacts})


# ─────────────────────────────────────────────
# STAGED ARTIFACT MANAGEMENT (P0 lock flow)
# ─────────────────────────────────────────────
@relay_memory_bp.route("/staged", methods=["GET"])
def list_staged():
    """
    GET /relay/staged?key=<optional>
    List staged artifacts awaiting manual promotion.
    These are queued writes that were blocked by P0 lock.
    """
    key = request.args.get("key", None)
    staged = get_staged_artifacts(key)
    return jsonify({"count": len(staged), "staged": staged})


@relay_memory_bp.route("/staged/<staged_id>/promote", methods=["POST"])
def promote_staged(staged_id):
    """
    POST /relay/staged/<staged_id>/promote
    Manually promote a staged artifact to replace the locked P0.
    This is the only authorized path to update a P0 procedure.
    """
    result = promote_staged_artifact(staged_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify({"ok": True, **result})


# ─────────────────────────────────────────────
# MESSAGE STORAGE
# ─────────────────────────────────────────────
@relay_memory_bp.route("/store", methods=["POST"])
def store_msg():
    """
    POST /relay/store
    Body: { "role": "user"|"assistant", "content": str, "session_id": str }
    Store AI response after receiving it.
    """
    body = request.get_json() or {}
    role = body.get("role", "assistant")
    content = (body.get("content") or "").strip()
    session_id = body.get("session_id", "default")
    if not content:
        return jsonify({"error": "content required"}), 400
    result = store_message(role, content, session_id)
    return jsonify({"ok": True, **result})


# ─────────────────────────────────────────────
# HISTORY + SEARCH
# ─────────────────────────────────────────────
@relay_memory_bp.route("/history", methods=["GET"])
def history():
    """GET /relay/history?session_id=default&limit=20"""
    session_id = request.args.get("session_id", "default")
    limit = min(int(request.args.get("limit", 20)), 100)
    messages = get_recent_messages(session_id, limit)
    return jsonify({"session_id": session_id, "count": len(messages), "messages": messages})


@relay_memory_bp.route("/search", methods=["GET"])
def search():
    """GET /relay/search?q=deploy&topic=deploy&limit=10"""
    q = request.args.get("q", "").strip()
    topic = request.args.get("topic", None)
    limit = min(int(request.args.get("limit", 10)), 50)
    if not q:
        return jsonify({"error": "q (query) required"}), 400
    results = search_messages(q, topic, limit)
    return jsonify({"query": q, "topic": topic, "count": len(results), "results": results})


# ─────────────────────────────────────────────
# CLASSIFY UTILITY
# ─────────────────────────────────────────────
@relay_memory_bp.route("/classify", methods=["POST"])
def classify():
    """POST /relay/classify — classify without storing"""
    body = request.get_json() or {}
    text = (body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    return jsonify({"topic": classify_topic(text), "mode": detect_mode(text)})
