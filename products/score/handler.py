"""Handle the score product context."""

from memory import buckets
from memory import schema


def handle(S0: dict) -> dict:
    """Handle a score result and write it to memory when a user is logged in."""
    S0 = S0 or {}
    result = S0.get("R") or S0.get("result") or {}
    user_id = S0.get("context", {}).get("user_id")

    if user_id and result:
        existing = buckets.read(user_id, schema.SCORED_ITEMS)
        items = existing.get("items", [])
        items.append({"Q": S0.get("Q"), "result": result})
        buckets.write(user_id, schema.SCORED_ITEMS, {"items": items})

    return {"product": "score", "result": result}
