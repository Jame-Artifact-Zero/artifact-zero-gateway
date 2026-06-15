# relay_session.py
# Stateful session layer — wires SimulatedThread into the NTI relay pipeline.
#
# p0097 fix:
#   - Serialize full active Gateway state, not just window counters.
#   - Serialize full active ThreadMonitor records, not just last 8 records.
#   - Restore Gateway + ThreadMonitor completely on cold RDS reload.
#   - Preserve the existing public interface used by nti_relay_routes.py.
#   - Preserve get_history() shape expected by nti_relay.py:
#       [{"source": "human"|"ai", "content": "..."}]

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from simulated_thread import SimulatedThread
from blob_builder import InjectionBlob

try:
    import db as _db
    _USE_DB = True
except ImportError:
    _db = None
    _USE_DB = False
    print("[relay_session] WARNING: db module unavailable — sessions in-memory only", flush=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_cache: Dict[str, SimulatedThread] = {}
_lock = threading.Lock()


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse ISO/datetime values used by saved_at and updated_at."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _state_blob_count(state: Optional[Dict[str, Any]]) -> int:
    if not isinstance(state, dict):
        return 0
    blobs = state.get("blobs", [])
    return len(blobs) if isinstance(blobs, list) else 0


def _state_history_count(state: Optional[Dict[str, Any]]) -> int:
    if not isinstance(state, dict):
        return 0
    if state.get("history_count") is not None:
        return _coerce_int(state.get("history_count"), 0)
    monitor = state.get("monitor", {}) if isinstance(state.get("monitor"), dict) else {}
    records = monitor.get("records") or state.get("all_records") or state.get("last_records") or []
    return len(records) if isinstance(records, list) else 0


def _state_metrics(state: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(state, dict):
        return {"total_messages": 0, "relay_number": 0, "blob_count": 0, "history_count": 0}
    return {
        "total_messages": _coerce_int(state.get("total_messages"), 0),
        "relay_number": _coerce_int(state.get("relay_number"), 0),
        "blob_count": _state_blob_count(state),
        "history_count": _state_history_count(state),
    }


def _thread_metrics(thread: Optional[SimulatedThread]) -> Dict[str, int]:
    if thread is None:
        return {"total_messages": 0, "relay_number": 0, "blob_count": 0, "history_count": 0}
    return {
        "total_messages": _coerce_int(getattr(thread, "total_messages", 0), 0),
        "relay_number": _coerce_int(getattr(thread, "relay_number", 0), 0),
        "blob_count": len(getattr(thread, "blobs", []) or []),
        "history_count": len(getattr(getattr(thread, "_monitor", None), "records", []) or []),
    }


def _thread_updated_at(thread: Optional[SimulatedThread]) -> Optional[datetime]:
    if thread is None:
        return None
    for attr in ("_az_updated_at", "_az_saved_at"):
        dt = _parse_dt(getattr(thread, attr, None))
        if dt is not None:
            return dt
    return _parse_dt(getattr(thread, "created_at", None))


def _state_datetime(state: Optional[Dict[str, Any]], updated_at: Any = None) -> Optional[datetime]:
    if isinstance(state, dict):
        for key in ("saved_at", "updated_at", "created_at"):
            dt = _parse_dt(state.get(key))
            if dt is not None:
                return dt
    return _parse_dt(updated_at)


def _db_row_to_thread(session_id: str, row: Dict[str, Any]) -> Optional[SimulatedThread]:
    state = row.get("state")
    if not isinstance(state, dict):
        return None
    label = row.get("label") or state.get("label") or session_id
    thread = _thread_from_state(session_id, label, state)
    updated_at = _state_datetime(state, row.get("updated_at"))
    if updated_at is not None:
        thread._az_updated_at = updated_at.isoformat()
        thread._az_saved_at = updated_at.isoformat()
    return thread


def _db_fetch_state(session_id: str) -> Optional[Dict[str, Any]]:
    """Fetch raw persisted state plus updated_at. Returns None if absent/unavailable."""
    if not _USE_DB or _db is None:
        return None
    try:
        conn = _db.db_connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT label, state_json, updated_at FROM relay_sessions WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        label = row[0]
        state_json = row[1]
        updated_at = row[2] if len(row) > 2 else None
        if not state_json:
            return None
        state = json.loads(state_json) if isinstance(state_json, str) else state_json
        if not isinstance(state, dict):
            return None
        return {"label": label, "state": state, "updated_at": updated_at}
    except Exception as e:
        print(f"[relay_session] RDS fetch error for {session_id}: {e}", flush=True)
        return None


def _db_load(session_id: str) -> Optional[SimulatedThread]:
    """Load a session from RDS. Returns None if not found or DB unavailable."""
    row = _db_fetch_state(session_id)
    if row is None:
        return None
    try:
        return _db_row_to_thread(session_id, row)
    except Exception as e:
        print(f"[relay_session] RDS load error for {session_id}: {e}", flush=True)
        return None


def _db_state_is_newer_than_thread(row: Dict[str, Any], thread: Optional[SimulatedThread]) -> bool:
    """True when persisted RDS state is ahead of RAM cache."""
    if row is None or thread is None:
        return False
    db_state = row.get("state") or {}
    dbm = _state_metrics(db_state)
    mem = _thread_metrics(thread)

    if dbm["total_messages"] > mem["total_messages"]:
        return True
    if dbm["relay_number"] > mem["relay_number"]:
        return True
    if dbm["blob_count"] > mem["blob_count"]:
        return True

    # If RAM is ahead on any monotonic metric, do not prefer RDS solely by timestamp.
    if dbm["total_messages"] < mem["total_messages"]:
        return False
    if dbm["relay_number"] < mem["relay_number"]:
        return False
    if dbm["blob_count"] < mem["blob_count"]:
        return False

    db_dt = _state_datetime(db_state, row.get("updated_at"))
    mem_dt = _thread_updated_at(thread)
    return bool(db_dt and mem_dt and db_dt > mem_dt)


def _thread_is_newer_than_db_state(thread: SimulatedThread, row: Optional[Dict[str, Any]]) -> bool:
    """True when RAM state is ahead of persisted RDS state."""
    if row is None:
        return True
    db_state = row.get("state") or {}
    dbm = _state_metrics(db_state)
    mem = _thread_metrics(thread)

    if mem["total_messages"] > dbm["total_messages"]:
        return True
    if mem["relay_number"] > dbm["relay_number"]:
        return True
    if mem["blob_count"] > dbm["blob_count"]:
        return True

    if mem["total_messages"] < dbm["total_messages"]:
        return False
    if mem["relay_number"] < dbm["relay_number"]:
        return False
    if mem["blob_count"] < dbm["blob_count"]:
        return False

    mem_dt = _thread_updated_at(thread)
    db_dt = _state_datetime(db_state, row.get("updated_at"))
    return bool(mem_dt and db_dt and mem_dt > db_dt)


def _state_write_would_reduce(candidate_state: Dict[str, Any], current_state: Dict[str, Any]) -> bool:
    """
    Return True if candidate would move persisted state backward.

    history_count may drop only when relay_number and blob_count both increase,
    because monitor reset is valid only immediately after relay fired.
    """
    cand = _state_metrics(candidate_state)
    cur = _state_metrics(current_state)

    if cand["total_messages"] < cur["total_messages"]:
        return True
    if cand["relay_number"] < cur["relay_number"]:
        return True
    if cand["blob_count"] < cur["blob_count"]:
        return True

    history_dropped = cand["history_count"] < cur["history_count"]
    valid_relay_reset = (
        cand["relay_number"] > cur["relay_number"]
        and cand["blob_count"] > cur["blob_count"]
    )
    if history_dropped and not valid_relay_reset:
        return True

    return False


def _set_cache(session_id: str, thread: SimulatedThread) -> None:
    with _lock:
        _cache[session_id] = thread


def _db_save(session_id: str, thread: SimulatedThread) -> SimulatedThread:
    """
    Persist session state to RDS with monotonic protection.

    Returns the authoritative thread after the save attempt. If RDS is ahead,
    RAM is hydrated from RDS and stale RAM is not written backward.
    """
    if not _USE_DB or _db is None:
        return thread

    current_row = _db_fetch_state(session_id)
    if current_row is not None:
        if _db_state_is_newer_than_thread(current_row, thread):
            rds_thread = _thread_from_state(
                session_id,
                current_row.get("label", thread.label),
                current_row.get("state") or {},
            )
            with _lock:
                _cache[session_id] = rds_thread
            return rds_thread

        current_state = current_row.get("state") or {}
        candidate_state = _thread_to_state(thread)

        if _state_write_would_reduce(candidate_state, current_state):
            authoritative = _db_row_to_thread(session_id, current_row)
            if authoritative is not None:
                _set_cache(session_id, authoritative)
                print(
                    f"[relay_session] monotonic save skipped for {session_id}: RDS ahead of RAM",
                    flush=True,
                )
                return authoritative

    try:
        state = _thread_to_state(thread)
        saved_at = state.get("saved_at") or _now_iso()
        state_json = json.dumps(state)
        conn = _db.db_connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO relay_sessions (session_id, label, state_json, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE
              SET state_json = EXCLUDED.state_json,
                  updated_at = EXCLUDED.updated_at
            """,
            (session_id, thread.label, state_json, saved_at),
        )
        conn.commit()
        conn.close()
        thread._az_updated_at = _parse_dt(saved_at).isoformat() if _parse_dt(saved_at) else saved_at
        thread._az_saved_at = thread._az_updated_at
        _set_cache(session_id, thread)
        return thread
    except Exception as e:
        print(f"[relay_session] RDS save error for {session_id}: {e}", flush=True)
        return thread


def _db_delete(session_id: str) -> None:
    if not _USE_DB or _db is None:
        return
    try:
        conn = _db.db_connect()
        cur = conn.cursor()
        cur.execute("DELETE FROM relay_sessions WHERE session_id = %s", (session_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[relay_session] RDS delete error for {session_id}: {e}", flush=True)


def _db_active_count() -> int:
    """Count active sessions from RDS, falling back to RAM cache on error."""
    if not _USE_DB or _db is None:
        with _lock:
            return len(_cache)
    try:
        conn = _db.db_connect()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM relay_sessions")
        row = cur.fetchone()
        conn.close()
        return row[0] or 0
    except Exception as e:
        print(f"[relay_session] RDS count error: {e}", flush=True)
        with _lock:
            return len(_cache)


def _message_record_to_dict(record: Any) -> Dict[str, Any]:
    return {
        "record_id": record.record_id,
        "source": record.source,
        "content": record.content,
        "char_count": record.char_count,
        "token_estimate": record.token_estimate,
        "cumulative_chars": record.cumulative_chars,
        "cumulative_tokens": record.cumulative_tokens,
        "window_pct": record.window_pct,
        "window_status": record.window_status,
        "timestamp": record.timestamp,
        "injection_triggered": getattr(record, "injection_triggered", False),
    }


def _gateway_to_state(gateway: Any) -> Dict[str, Any]:
    """Serialize the full active Gateway state needed by BlobBuilder."""
    synonym_map = {}
    for decl_id, decl in getattr(gateway, "synonym_map", {}).items():
        synonym_map[decl_id] = {
            "declaration_id": getattr(decl, "declaration_id", decl_id),
            "pointer_a": getattr(decl, "pointer_a", ""),
            "pointer_b": getattr(decl, "pointer_b", ""),
            "canonical": getattr(decl, "canonical", ""),
            "declared_by": getattr(decl, "declared_by", ""),
            "declared_at": getattr(decl, "declared_at", ""),
            "active": getattr(decl, "active", True),
            "revoked_at": getattr(decl, "revoked_at", ""),
            "revoke_reason": getattr(decl, "revoke_reason", ""),
            "note": getattr(decl, "note", ""),
        }

    return {
        "gateway_id": gateway.gateway_id,
        "label": gateway.label,
        "created_at": gateway.created_at,
        "dictionary": {
            ptr: {
                "pointer": entry.pointer,
                "original": entry.original,
                "normalized": entry.normalized,
                "frequency": entry.frequency,
                "first_seen": entry.first_seen,
            }
            for ptr, entry in gateway.dictionary.items()
        },
        "_key_index": dict(gateway._key_index),
        "stream": [
            {
                "message_id": msg.message_id,
                "source": msg.source,
                "raw": msg.raw,
                "stripped": msg.stripped,
                "compressed": list(msg.compressed),
                "timestamp": msg.timestamp,
            }
            for msg in gateway.stream
        ],
        "synonym_map": synonym_map,
        "_synonym_index": dict(getattr(gateway, "_synonym_index", {})),
    }


def _blob_to_state(blob: InjectionBlob) -> Dict[str, Any]:
    return blob.to_dict()


def _blob_from_state(blob_state: Dict[str, Any]) -> InjectionBlob:
    return InjectionBlob(
        blob_id=blob_state.get("blob_id", ""),
        thread_id=blob_state.get("thread_id", ""),
        created_at=blob_state.get("created_at", _now_iso()),
        message_count_at_injection=blob_state.get("message_count_at_injection", 0),
        window_pct_at_injection=blob_state.get("window_pct_at_injection", 0.0),
        core_sequences=blob_state.get("core_sequences", []),
        core_dictionary=blob_state.get("core_dictionary", {}),
        recent_messages=blob_state.get("recent_messages", []),
        thread_label=blob_state.get("thread_label", ""),
        ai_char_count=blob_state.get("ai_char_count", 0),
        human_char_count=blob_state.get("human_char_count", 0),
        relay_number=blob_state.get("relay_number", 1),
    )


def _thread_to_state(thread: SimulatedThread) -> Dict[str, Any]:
    """
    Serialize enough state for a cold RDS reload to behave like a hot session.
    """
    monitor = thread._monitor
    gateway = thread._gateway
    all_records = [_message_record_to_dict(r) for r in monitor.records]
    last_records = all_records[-8:]

    return {
        "schema_version": "relay_session_state_v2",
        "thread_id": thread.thread_id,
        "label": thread.label,
        "relay_number": thread.relay_number,
        "total_messages": thread.total_messages,
        "total_chars": thread.total_chars,
        "created_at": thread.created_at,
        "monitor": {
            "thread_id": monitor.thread_id,
            "created_at": monitor.created_at,
            "cumulative_chars": monitor.cumulative_chars,
            "cumulative_tokens": monitor.cumulative_tokens,
            "injection_count": monitor.injection_count,
            "records": all_records,
        },
        # Backward-compatible top-level fields used by old state readers.
        "cumulative_chars": monitor.cumulative_chars,
        "cumulative_tokens": monitor.cumulative_tokens,
        "injection_count": monitor.injection_count,
        "all_records": all_records,
        "last_records": last_records,
        "gateway": _gateway_to_state(gateway),
        "blobs": [_blob_to_state(blob) for blob in getattr(thread, "blobs", [])],
        "saved_at": _now_iso(),
        # Diagnostic fields only; useful for confirming cold reload state.
        "history_count": len(all_records),
        "history_preview_sources": [r["source"] for r in all_records[-5:]],
    }


def _thread_from_state(session_id: str, label: str, state: Dict[str, Any]) -> SimulatedThread:
    """
    Reconstruct a SimulatedThread from persisted state.

    Supports both:
      - New full-state format: monitor.records + gateway + blobs
      - Old format: top-level counters + last_records only
    """
    from gateway import Gateway, DictionaryEntry, StreamMessage, SynonymDeclaration
    from thread_monitor import MessageRecord

    thread = SimulatedThread(
        label=label or state.get("label", session_id),
        thread_id=session_id,
    )
    thread.thread_id = state.get("thread_id", session_id)
    thread.label = label or state.get("label", thread.label)
    thread.relay_number = state.get("relay_number", 1)
    thread.total_messages = state.get("total_messages", 0)
    thread.total_chars = state.get("total_chars", 0)
    thread.created_at = state.get("created_at", getattr(thread, "created_at", _now_iso()))

    # Restore blob history before route checks thread.last_blob().
    thread.blobs = [
        _blob_from_state(blob_state)
        for blob_state in state.get("blobs", [])
        if isinstance(blob_state, dict)
    ]

    # Restore monitor.
    monitor_state = state.get("monitor", {})
    monitor = thread._monitor
    monitor.thread_id = monitor_state.get("thread_id", state.get("thread_id", session_id))
    monitor.created_at = monitor_state.get("created_at", getattr(monitor, "created_at", _now_iso()))
    monitor.cumulative_chars = monitor_state.get("cumulative_chars", state.get("cumulative_chars", 0))
    monitor.cumulative_tokens = monitor_state.get("cumulative_tokens", state.get("cumulative_tokens", 0))
    monitor.injection_count = monitor_state.get("injection_count", state.get("injection_count", 0))

    records_data = (
        monitor_state.get("records")
        or state.get("all_records")
        or state.get("last_records")
        or []
    )
    monitor.records.clear()
    for record_state in records_data:
        rec = MessageRecord(
            record_id=record_state.get("record_id", ""),
            source=record_state.get("source", ""),
            content=record_state.get("content", ""),
            char_count=record_state.get("char_count", 0),
            token_estimate=record_state.get("token_estimate", 0),
            cumulative_chars=record_state.get("cumulative_chars", monitor.cumulative_chars),
            cumulative_tokens=record_state.get("cumulative_tokens", monitor.cumulative_tokens),
            window_pct=record_state.get("window_pct", 0.0),
            window_status=record_state.get("window_status", "NOMINAL"),
            timestamp=record_state.get("timestamp", _now_iso()),
            injection_triggered=record_state.get("injection_triggered", False),
        )
        monitor.records.append(rec)

    # Restore gateway.
    gateway_state = state.get("gateway")
    if gateway_state:
        gateway = Gateway(
            gateway_id=gateway_state.get("gateway_id", session_id),
            label=gateway_state.get("label", thread.label),
        )
        gateway.created_at = gateway_state.get("created_at", getattr(gateway, "created_at", _now_iso()))

        for ptr, entry_state in gateway_state.get("dictionary", {}).items():
            entry = DictionaryEntry(
                pointer=entry_state.get("pointer", ptr),
                original=entry_state.get("original", ""),
                normalized=entry_state.get("normalized", ""),
                frequency=entry_state.get("frequency", 1),
                first_seen=entry_state.get("first_seen", ""),
            )
            gateway.dictionary[ptr] = entry

        gateway._key_index = dict(gateway_state.get("_key_index", {}))
        if not gateway._key_index:
            gateway._key_index = {
                entry.normalized: ptr
                for ptr, entry in gateway.dictionary.items()
                if entry.normalized
            }

        gateway.stream.clear()
        for msg_state in gateway_state.get("stream", []):
            msg = StreamMessage(
                message_id=msg_state.get("message_id", ""),
                source=msg_state.get("source", ""),
                raw=msg_state.get("raw", ""),
                stripped=msg_state.get("stripped", ""),
                compressed=list(msg_state.get("compressed", [])),
                timestamp=msg_state.get("timestamp", _now_iso()),
            )
            gateway.stream.append(msg)

        gateway.synonym_map.clear()
        for decl_id, decl_state in gateway_state.get("synonym_map", {}).items():
            decl = SynonymDeclaration(
                declaration_id=decl_state.get("declaration_id", decl_id),
                pointer_a=decl_state.get("pointer_a", ""),
                pointer_b=decl_state.get("pointer_b", ""),
                canonical=decl_state.get("canonical", ""),
                declared_by=decl_state.get("declared_by", ""),
                declared_at=decl_state.get("declared_at", ""),
                active=decl_state.get("active", True),
                revoked_at=decl_state.get("revoked_at", ""),
                revoke_reason=decl_state.get("revoke_reason", ""),
                note=decl_state.get("note", ""),
            )
            gateway.synonym_map[decl_id] = decl

        gateway._synonym_index = dict(gateway_state.get("_synonym_index", {}))
        thread._gateway = gateway

    return thread


def _get_existing_authoritative_session(session_id: str) -> Optional[SimulatedThread]:
    """Return existing session from RAM/RDS without creating a new one."""
    row = _db_fetch_state(session_id)
    db_thread = None
    if row is not None:
        try:
            db_thread = _db_row_to_thread(session_id, row)
        except Exception as e:
            print(f"[relay_session] RDS hydrate error for {session_id}: {e}", flush=True)
            db_thread = None

    with _lock:
        ram_thread = _cache.get(session_id)

        if ram_thread is not None and db_thread is not None:
            if _db_state_is_newer_than_thread(row, ram_thread):
                _cache[session_id] = db_thread
                return db_thread
            return ram_thread

        if ram_thread is not None:
            return ram_thread

        if db_thread is not None:
            _cache[session_id] = db_thread
            return db_thread

    return None


def get_or_create_session(session_id: str, label: str = "") -> SimulatedThread:
    """Return authoritative SimulatedThread for session_id. Order: compare RDS ↔ RAM → new."""
    thread = _get_existing_authoritative_session(session_id)
    if thread is not None:
        return thread

    thread = SimulatedThread(label=label or session_id, thread_id=session_id)
    thread = _db_save(session_id, thread)
    _set_cache(session_id, thread)
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
    """Session count from RDS if available, else RAM cache."""
    return _db_active_count()


def record_exchange(
    session_id: str,
    human_text: str,
    ai_response: str,
    label: str = "",
) -> Dict[str, Any]:
    """Record one full exchange (human + AI) into the session thread."""
    thread = get_or_create_session(session_id, label=label)
    thread.add("human", human_text)
    ai_result = thread.add("ai", ai_response)

    relay_triggered = ai_result.inject_now
    blob_prompt = None
    if relay_triggered:
        blob: InjectionBlob = thread.relay()
        blob_prompt = blob.to_prompt()

    thread = _db_save(session_id, thread)
    s1_state = _thread_to_state(thread)

    return {
        "relay_triggered": relay_triggered,
        "blob_prompt": blob_prompt,
        "window_pct": round(ai_result.window_pct * 100, 2),
        "window_status": ai_result.window_status,
        "relay_number": thread.relay_number,
        "total_messages": thread.total_messages,
        "s1_state": s1_state,
    }


def get_history(session_id: str) -> List[Dict[str, str]]:
    """
    Return full active-window conversation history for a session as:
      [{"source": "human"|"ai", "content": str}, ...]

    This shape is required by nti_relay._build_messages().
    """
    thread = _get_existing_authoritative_session(session_id)
    if thread is None:
        return []
    return [
        {"source": r.source, "content": r.content}
        for r in thread._monitor.records
        if r.content
    ]


def get_s1_state(session_id: str) -> Dict[str, Any]:
    thread = get_or_create_session(session_id)
    return _thread_to_state(thread)


def get_blob_for_next_call(session_id: str) -> Optional[str]:
    """If the session has a prior blob, return it as a prompt string."""
    thread = get_or_create_session(session_id)
    blob = thread.last_blob()
    return blob.to_prompt() if blob else None


def session_status(session_id: str) -> Dict[str, Any]:
    """Return window status and thread stats for a session."""
    thread = _get_existing_authoritative_session(session_id)
    if thread is None:
        return {"error": "session not found", "session_id": session_id}

    status = thread.status()
    status["session_id"] = session_id
    status["history_count"] = len(thread._monitor.records)
    status["history_preview_sources"] = [r.source for r in thread._monitor.records[-5:]]
    return status