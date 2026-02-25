"""
relay_pg.py
-----------
PostgreSQL-compatible Relay adapter for az_relay.py.

Replace direct sqlite3 usage with these patterns.
You may need to map your existing Relay tables (relay_messages, relay_events, az_orgs, etc.).

This file provides canonical patterns:
- db_connection usage
- placeholder normalization
- basic CRUD functions you can expand in your repo
"""

from typing import Optional, Dict, Any, List
from db import db_connection, param_placeholder


def get_org_by_api_key(api_key: str) -> Optional[Dict[str, Any]]:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, name, api_key, created_at FROM orgs WHERE api_key = {ph}", (api_key,))
        row = cur.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row
    return {"id": row[0], "name": row[1], "api_key": row[2], "created_at": row[3]}


def log_relay_event(org_id: str, kind: str, payload_json: str, trace_id: str) -> None:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO relay_events (org_id, kind, payload_json, trace_id) VALUES ({ph}, {ph}, {ph}, {ph})",
            (org_id, kind, payload_json, trace_id)
        )
        conn.commit()


def create_relay_message(org_id: str, source: str, destination: str, raw_text: str, normalized_text: str, trace_id: str) -> str:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO relay_messages (org_id, source, destination, raw_text, normalized_text, trace_id)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
            RETURNING id
            """,
            (org_id, source, destination, raw_text, normalized_text, trace_id)
        )
        row = cur.fetchone()
        conn.commit()
    if isinstance(row, dict):
        return row["id"]
    return row[0]
