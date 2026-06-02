"""
operator_prompt_builder.py

Builds an enriched system prompt for the operator room by pulling
recent context from operator_context and operator_sessions (Postgres).

Usage:
    from operator_prompt_builder import build_operator_prompt
    injected = build_operator_prompt(db_url=DATABASE_URL, push=current_push)
    # Prepend injected to your base system prompt
"""

import json
import psycopg2
from datetime import timezone


def build_operator_prompt(db_url: str, push: str = None, context_limit: int = 10, session_limit: int = 3) -> str:
    """
    Pulls recent operator_context blobs and operator_sessions summaries
    and returns a formatted string to prepend to the system prompt.

    Args:
        db_url:         Postgres connection string
        push:           Current push label (e.g. 'p0068'). If provided, filters
                        context rows matching this push first, then fills with recents.
        context_limit:  Max operator_context rows to inject (default 10)
        session_limit:  Max operator_sessions rows to inject (default 3)

    Returns:
        A formatted string block for system prompt injection.
    """
    blocks = []

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        # --- OPERATOR CONTEXT BLOBS ---
        # Pull rows matching current push first, then pad with most recent
        if push:
            cur.execute("""
                SELECT id, created_at, summary, blob_json
                FROM operator_context
                WHERE summary ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (f"%{push}%", context_limit))
            push_rows = cur.fetchall()

            remaining = context_limit - len(push_rows)
            push_ids = tuple(r[0] for r in push_rows) if push_rows else ('__none__',)

            if remaining > 0:
                cur.execute("""
                    SELECT id, created_at, summary, blob_json
                    FROM operator_context
                    WHERE id NOT IN %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (push_ids, remaining))
                other_rows = cur.fetchall()
            else:
                other_rows = []

            context_rows = push_rows + other_rows
        else:
            cur.execute("""
                SELECT id, created_at, summary, blob_json
                FROM operator_context
                ORDER BY created_at DESC
                LIMIT %s
            """, (context_limit,))
            context_rows = cur.fetchall()

        if context_rows:
            blocks.append("[OPERATOR CONTEXT — RECENT BLOBS]")
            for row in context_rows:
                row_id, created_at, summary, blob_json_str = row
                ts = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "unknown"

                # Extract key fields from blob_json
                try:
                    blob = json.loads(blob_json_str) if blob_json_str else {}
                except (json.JSONDecodeError, TypeError):
                    blob = {}

                push_label = blob.get("push", "?")
                objective = blob.get("objective", "")[:200]
                decisions = blob.get("decisions", [])
                key_facts = blob.get("key_facts", [])

                entry = f"  [{ts}] push={push_label} | {summary or ''}"
                if objective:
                    entry += f"\n    objective: {objective}"
                if decisions:
                    for d in decisions[:5]:
                        entry += f"\n    - {str(d)[:150]}"
                if key_facts and not decisions:
                    for kf in key_facts[:3]:
                        entry += f"\n    fact: {str(kf)[:150]}"

                blocks.append(entry)

        # --- OPERATOR SESSIONS ---
        cur.execute("""
            SELECT id, created_at, summary, jos_json
            FROM operator_sessions
            ORDER BY created_at DESC
            LIMIT %s
        """, (session_limit,))
        session_rows = cur.fetchall()

        if session_rows:
            blocks.append("\n[OPERATOR SESSIONS — RECENT]")
            for row in session_rows:
                row_id, created_at, summary, jos_json_str = row
                ts = created_at.strftime("%Y-%m-%d %H:%M") if created_at else "unknown"

                try:
                    jos = json.loads(jos_json_str) if jos_json_str else {}
                except (json.JSONDecodeError, TypeError):
                    jos = {}

                push_label = jos.get("push", "?")
                entry = f"  [{ts}] session={row_id} | push={push_label}"
                if summary:
                    entry += f"\n    {summary[:300]}"
                blocks.append(entry)

        cur.close()
        conn.close()

    except Exception as e:
        blocks.append(f"[OPERATOR CONTEXT LOAD ERROR: {e}]")

    if not blocks:
        return ""

    header = "=== INJECTED OPERATOR MEMORY ===\n"
    footer = "\n=== END INJECTED MEMORY ===\n"
    return header + "\n".join(blocks) + footer
