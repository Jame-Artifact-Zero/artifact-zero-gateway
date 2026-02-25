"""
stripe_webhook_unified.py
-------------------------
Canonical Stripe webhook handler with idempotency + event dispatch.

Repo-agnostic:
- does not import Stripe SDK; assumes you already validate signature elsewhere OR you pass validated event dict in.
- stores event_id in stripe_events table to prevent double-processing.

Required DB table:
- stripe_events(event_id text primary key, created_at timestamptz default now())

Optional:
- credit_ledger(user_id, delta, reason, created_at)
"""

from typing import Dict, Any, Tuple
from db import db_connection, param_placeholder
from auth_unifier import ensure_user, add_credits


# Expand this map in your repo (I03 hardening)
EVENT_DISPATCH = {
    "checkout.session.completed": "_on_checkout_completed",
    # "customer.subscription.updated": "_on_subscription_updated",
    # "customer.subscription.deleted": "_on_subscription_deleted",
    # "invoice.payment_failed": "_on_payment_failed",
}


def handle_stripe_webhook(event: Dict[str, Any]) -> Tuple[str, int]:
    event_id = event.get("id")
    event_type = event.get("type")

    if not event_id or not event_type:
        return ("invalid_event", 400)

    if _is_duplicate(event_id):
        return ("duplicate", 200)

    _mark_seen(event_id)

    handler_name = EVENT_DISPATCH.get(event_type)
    if not handler_name:
        # Unknown events are acknowledged but not acted upon.
        return ("ignored", 200)

    handler = globals().get(handler_name)
    if not handler:
        return ("misconfigured", 500)

    try:
        handler(event)
    except Exception:
        # In production, add trace logging here.
        return ("error", 500)

    return ("processed", 200)


def _is_duplicate(event_id: str) -> bool:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM stripe_events WHERE event_id = {ph}", (event_id,))
        return cur.fetchone() is not None


def _mark_seen(event_id: str) -> None:
    ph = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO stripe_events (event_id) VALUES ({ph})", (event_id,))
        conn.commit()


def _on_checkout_completed(event: Dict[str, Any]) -> None:
    obj = event.get("data", {}).get("object", {})

    # Stripe may provide customer_email OR customer_details.email depending on flow
    email = obj.get("customer_email") or (obj.get("customer_details") or {}).get("email")
    customer = obj.get("customer")  # stripe customer id

    if not email:
        return

    user = ensure_user(email=email, stripe_id=customer)

    # You will map credits to your product tier. This is placeholder logic.
    add_credits(user_id=user["id"], delta=100, reason="checkout.session.completed")
