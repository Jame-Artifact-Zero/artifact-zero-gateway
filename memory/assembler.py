"""Assemble relevant memory buckets into one task-level state."""

from memory import buckets
from memory import schema


def assemble(S0: dict) -> dict:
    """Read relevant buckets based on context and collapse them into the current S0."""
    S0 = S0 or {}
    context = S0.get("context", {})
    user_id = context.get("user_id")
    product = context.get("product")
    memory_state = {}

    if user_id:
        memory_state[schema.USER_PROFILE] = buckets.read(user_id, schema.USER_PROFILE)
        if product == "email":
            memory_state[schema.EMAIL_SENDERS] = buckets.read(user_id, schema.EMAIL_SENDERS)
            memory_state[schema.EMAIL_THREADS] = buckets.read(user_id, schema.EMAIL_THREADS)
        if product == "score":
            memory_state[schema.SCORED_ITEMS] = buckets.read(user_id, schema.SCORED_ITEMS)

    S0["memory"] = memory_state
    return S0
