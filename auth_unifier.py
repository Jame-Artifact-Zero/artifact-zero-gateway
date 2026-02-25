"""
auth_unifier.py
----------------
Unified auth adapter. Designed to sit on top of your existing auth/session system.
Does NOT assume a specific framework. Pure functions.

You will map these into your auth.py and remove az_users divergence.
"""

from typing import Optional, Dict, Any
from db import db_connection, param_placeholder


def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, email, stripe_id, credits, created_at FROM users WHERE id = {ph}", (user_id,))
        row = cur.fetchone()
    if not row:
        return None
    # Support both tuple and dict cursors
    if isinstance(row, dict):
        return row
    return {"id": row[0], "email": row[1], "stripe_id": row[2], "credits": row[3], "created_at": row[4]}


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, email, stripe_id, credits, created_at FROM users WHERE email = {ph}", (email,))
        row = cur.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row
    return {"id": row[0], "email": row[1], "stripe_id": row[2], "credits": row[3], "created_at": row[4]}


def ensure_user(email: str, stripe_id: Optional[str] = None) -> Dict[str, Any]:
    existing = get_user_by_email(email)
    if existing:
        # backfill stripe_id if missing
        if stripe_id and not existing.get("stripe_id"):
            set_user_stripe_id(existing["id"], stripe_id)
            existing["stripe_id"] = stripe_id
        return existing
    return create_user(email=email, stripe_id=stripe_id)


def create_user(email: str, stripe_id: Optional[str] = None) -> Dict[str, Any]:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO users (email, stripe_id, credits)
            VALUES ({ph}, {ph}, 0)
            RETURNING id, email, stripe_id, credits, created_at
            """,
            (email, stripe_id)
        )
        row = cur.fetchone()
        conn.commit()
    if isinstance(row, dict):
        return row
    return {"id": row[0], "email": row[1], "stripe_id": row[2], "credits": row[3], "created_at": row[4]}


def set_user_stripe_id(user_id: str, stripe_id: str) -> None:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET stripe_id = {ph} WHERE id = {ph}", (stripe_id, user_id))
        conn.commit()


def add_credits(user_id: str, delta: int, reason: str = "stripe") -> None:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET credits = credits + {ph} WHERE id = {ph}", (delta, user_id))
        # optional ledger
        try:
            cur.execute(
                f"INSERT INTO credit_ledger (user_id, delta, reason) VALUES ({ph}, {ph}, {ph})",
                (user_id, delta, reason)
            )
        except Exception:
            pass
        conn.commit()
