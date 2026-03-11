# relay_session.py
# Stateful session layer — wires SimulatedThread into the NTI relay pipeline.
#
# Responsibilities:
#   - Hold per-session SimulatedThread instances (in-memory, keyed by session_id)
#   - Feed each human message and AI response through the thread
#   - Signal when to prepend blob context to the next LLM call
#   - Reset the active window after relay
#
# What this file does NOT do:
#   - LLM routing (nti_relay.py owns that)
#   - V2/V3 governance (nti_relay.py owns that)
#   - Artifact injection (relay_artifacts.py owns that)
#   - Flask routing (nti_relay_routes.py owns that)
#
# Usage (from nti_relay_routes.py):
#   from relay_session import get_or_create_session, record_exchange, session_status

import threading
from typing import Optional, Dict, Any

from simulated_thread import SimulatedThread
from blob_builder import InjectionBlob

# ─── Session store (in-memory) ────────────────────────────────────────────
# ECS Fargate: sessions are per-container, per-process lifetime.
# Future: persist to RDS for cross-container continuity.

_sessions: Dict[str, SimulatedThread] = {}
_lock = threading.Lock()


def get_or_create_session(session_id: str, label: str = "") -> SimulatedThread:
    """Return existing SimulatedThread or create a new one."""
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = SimulatedThread(
                label=label or session_id,
                thread_id=session_id,
            )
        return _sessions[session_id]


def destroy_session(session_id: str) -> bool:
    """Remove a session. Returns True if it existed."""
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return True
        return False


def active_session_count() -> int:
    with _lock:
        return len(_sessions)


# ─── Core exchange function ───────────────────────────────────────────────

def record_exchange(
    session_id: str,
    human_text: str,
    ai_response: str,
    label: str = "",
) -> Dict[str, Any]:
    """
    Record one full exchange (human + AI) into the session thread.

    Returns:
        {
            "relay_triggered": bool,
            "blob_prompt": str | None,   # prepend to NEXT call's system prompt if relay_triggered
            "window_pct": float,
            "window_status": str,
            "relay_number": int,
            "total_messages": int,
        }
    """
    thread = get_or_create_session(session_id, label=label)

    # Feed human message
    human_result = thread.add("human", human_text)

    # Feed AI response
    ai_result = thread.add("ai", ai_response)

    relay_triggered = ai_result.inject_now
    blob_prompt = None

    if relay_triggered:
        blob: InjectionBlob = thread.relay()
        blob_prompt = blob.to_prompt()

    return {
        "relay_triggered": relay_triggered,
        "blob_prompt": blob_prompt,
        "window_pct": round(ai_result.window_pct * 100, 2),
        "window_status": ai_result.window_status,
        "relay_number": thread.relay_number,
        "total_messages": thread.total_messages,
    }


def get_blob_for_next_call(session_id: str) -> Optional[str]:
    """
    If the session has a pending blob from the last relay,
    return it as a prompt string. Otherwise None.

    Used to prepend to the system prompt of the next LLM call
    when the caller manages the relay trigger externally.
    """
    thread = get_or_create_session(session_id)
    blob = thread.last_blob()
    if blob:
        return blob.to_prompt()
    return None


def session_status(session_id: str) -> Dict[str, Any]:
    """Return window status and thread stats for a session."""
    with _lock:
        if session_id not in _sessions:
            return {"error": "session not found", "session_id": session_id}
    thread = _sessions[session_id]
    status = thread.status()
    core = thread.core()
    return {
        "session_id": session_id,
        **status,
        "core_sequences": core,
        "core_count": len(core),
    }
