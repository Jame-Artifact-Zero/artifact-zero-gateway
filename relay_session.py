# relay_session.py
# Stateful session layer — wires SimulatedThread into the NTI relay pipeline.
#
# p0040 changes:
#   - Session state persisted to RDS (relay_sessions table)
#   - In-memory cache retained as L1 cache — hot sessions served from RAM,
#     cold sessions rehydrated from RDS on first access after container restart
#   - Window counter now persists across ECS task routing changes
#   - active_session_count() now queries RDS, not local dict
#   - Thread state serialized as JSON blob (gateway dict + monitor records)
#
# Responsibilities:
#   - Hold per-session SimulatedThread instances (L1 RAM cache + RDS persistence)
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

import json
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from simulated_thread import SimulatedThread
from blob_builder import InjectionBlob

# ─── DB import ───────────────────────────────────────────────────────────────
# Graceful fallback: if RDS unavailable, log warning and fall back to
# in-memory only (same behavior as pre-p0040). No silent failure.

try:
    import db as _db
    _USE_DB = _db.USE_PG
    if not _USE_DB:
        print("[relay_session] WARNING: DATABASE_URL not postgres — sessions in-memory only", flush=True)
except ImportError:
    _db = None
    _USE_DB = False
    print("[relay_session] WARNING: db module unavailable — sessions in-memory only", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── L1 RAM cache ────────────────────────────────────────────────────────────
# Serves hot sessions without a DB round-trip.
# Populated on get_or_create_session. Evicted on destroy_session.
# Cold starts rehydrate from RDS.

_cache: Dict[str, SimulatedThread] = {}
_lock = threading.Lock()


# ─── RDS helpers ─────────────────────────────────────────────────────────────

def _db_load(session_id: str) -> Optional[SimulatedThread]:
    """Load a session from RDS. Returns None if not found or DB unavailable."""
    if not _USE_DB:
        return None
    try:
        conn = _db.db_connect()
        cur = conn.cursor()
        if _db.USE_PG:
            cur.execute(
                "SELECT label, state_json FROM relay_sessions WHERE session_id = %s",
                (session_id,)
            )
        else:
            cur.execute(
                "SELECT label, state_json FROM relay_sessions WHERE session_id = ?",
                (session_id,)
            )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        label = row[0] if _db.USE_PG else row["label"]
        state_json = row[1] if _db.USE_PG else row["state_json"]
        if not state_json:
            return None
        state = json.loads(state_json) if isinstance(state_json, str) else state_json
        return _thread_from_state(session_id, label, state)
    except Exception as e:
        print(f"[relay_session] RDS load error for {session_id}: {e}", flush=True)
        return None


def _db_save(session_id: str, thread: SimulatedThread) -> None:
    """Persist session state to RDS. Silent on failure — RAM cache still valid."""
    if not _USE_DB:
        return
    try:
        state = _thread_to_state(thread)
        state_json = json.dumps(state)
        conn = _db.db_connect()
        cur = conn.cursor()
        if _db.USE_PG:
            cur.execute(
                """
                INSERT INTO relay_sessions (session_id, label, state_json, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE
                  SET state_json = EXCLUDED.state_json,
                      updated_at = EXCLUDED.updated_at
                """,
                (session_id, thread.label, state_json, _now_iso())
            )
        else:
            # SQLite fallback (dev only)
            cur.execute(
                """
                INSERT OR REPLACE INTO relay_sessions (session_id, label, state_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, thread.label, state_json, _now_iso())
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[relay_session] RDS save error for {session_id}: {e}", flush=True)


def _db_delete(session_id: str) -> None:
    if not _USE_DB:
        return
    try:
        conn = _db.db_connect()
        cur = conn.cursor()
        if _db.USE_PG:
            cur.execute("DELETE FROM relay_sessions WHERE session_id = %s", (session_id,))
        else:
            cur.execute("DELETE FROM relay_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[relay_session] RDS delete error for {session_id}: {e}", flush=True)


def _db_active_count() -> int:
    """Count active sessions from RDS."""
    if not _USE_DB:
        with _lock:
            return len(_cache)
    try:
        conn = _db.db_connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM relay_sessions")
        row = cur.fetchone()
        conn.close()
        count = row[0] if _db.USE_PG else row[0]
        return count or 0
    except Exception as e:
        print(f"[relay_session] RDS count error: {e}", flush=True)
        with _lock:
            return len(_cache)


# ─── Thread serialization ─────────────────────────────────────────────────────
# Persist enough state to reconstruct cumulative window counts after rehydration.
# We do NOT serialize the full gateway stream (too large).
# We serialize: relay_number, total_messages, total_chars, cumulative_chars,
#               cumulative_tokens (the window counter), and last 8 verbatim records.

def _thread_to_state(thread: SimulatedThread) -> Dict:
    """Serialize the parts of a SimulatedThread needed for window continuity."""
    monitor = thread._monitor
    # Last 8 records for blob reconstruction continuity
    last_records = [
        {
            "source": r.source,
            "content": r.content,
            "char_count": r.char_count,
            "token_estimate": r.token_estimate,
        }
        for r in monitor.records[-8:]
    ]
    return {
        "thread_id": thread.thread_id,
        "label": thread.label,
        "relay_number": thread.relay_number,
        "total_messages": thread.total_messages,
        "total_chars": thread.total_chars,
        "cumulative_chars": monitor.cumulative_chars,
        "cumulative_tokens": monitor.cumulative_tokens,
        "injection_count": monitor.injection_count,
        "last_records": last_records,
        "saved_at": _now_iso(),
    }


def _thread_from_state(session_id: str, label: str, state: Dict) -> SimulatedThread:
    """Reconstruct a SimulatedThread from persisted state."""
    thread = SimulatedThread(
        label=label or state.get("label", session_id),
        thread_id=session_id,
    )
    thread.relay_number = state.get("relay_number", 1)
    thread.total_messages = state.get("total_messages", 0)
    thread.total_chars = state.get("total_chars", 0)

    # Restore monitor cumulative counters — this is the critical fix.
    # Pre-p0040: these reset to 0 on every container. Now they survive.
    monitor = thread._monitor
    monitor.cumulative_chars = state.get("cumulative_chars", 0)
    monitor.cumulative_tokens = state.get("cumulative_tokens", 0)
    monitor.injection_count = state.get("injection_count", 0)

    # Restore last records for blob verbatim context
    from thread_monitor import MessageRecord
    import uuid
    for r in state.get("last_records", []):
        rec = MessageRecord(
            record_id=str(uuid.uuid4())[:8],
            source=r.get("source", ""),
            content=r.get("content", ""),
            char_count=r.get("char_count", 0),
            token_estimate=r.get("token_estimate", 0),
            cumulative_chars=monitor.cumulative_chars,
            cumulative_tokens=monitor.cumulative_tokens,
            window_pct=round(monitor.cumulative_chars / 800_000, 4),
            window_status="NOMINAL",
            timestamp=_now_iso(),
        )
        monitor.records.append(rec)

    return thread


# ─── Public interface ─────────────────────────────────────────────────────────

def get_or_create_session(session_id: str, label: str = "") -> SimulatedThread:
    """
    Return SimulatedThread for session_id.
    Order: L1 RAM cache → RDS → new thread.
    """
    # L1 hit
    with _lock:
        if session_id in _cache:
            return _cache[session_id]

    # RDS lookup (outside lock to avoid blocking)
    thread = _db_load(session_id)

    with _lock:
        # Double-check after RDS lookup (another request may have populated cache)
        if session_id in _cache:
            return _cache[session_id]

        if thread is None:
            # New session
            thread = SimulatedThread(
                label=label or session_id,
                thread_id=session_id,
            )

        _cache[session_id] = thread
        return thread


def destroy_session(session_id: str) -> bool:
    """Remove session from RAM cache and RDS. Returns True if it existed."""
    existed = False
    with _lock:
        if session_id in _cache:
            del _cache[session_id]
            existed = True
    _db_delete(session_id)
    return existed


def active_session_count() -> int:
    """Session count from RDS (accurate across all ECS tasks)."""
    return _db_active_count()


# ─── Core exchange function ───────────────────────────────────────────────────

def record_exchange(
    session_id: str,
    human_text: str,
    ai_response: str,
    label: str = "",
) -> Dict[str, Any]:
    """
    Record one full exchange (human + AI) into the session thread.
    Persists updated state to RDS after each exchange.

    Returns:
        {
            "relay_triggered": bool,
            "blob_prompt": str | None,
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

    # Persist after every exchange — this is the core of p0040
    _db_save(session_id, thread)

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
    """
    thread = get_or_create_session(session_id)
    blob = thread.last_blob()
    if blob:
        return blob.to_prompt()
    return None


def session_status(session_id: str) -> Dict[str, Any]:
    """Return window status and thread stats for a session."""
    thread = None
    with _lock:
        thread = _cache.get(session_id)

    if thread is None:
        thread = _db_load(session_id)

    if thread is None:
        return {"error": "session not found", "session_id": session_id}

    status = thread.status()
    core = thread.core()
    return {
        "session_id": session_id,
        **status,
        "core_sequences": core,
        "core_count": len(core),
    }
