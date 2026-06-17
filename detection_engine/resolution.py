"""Normalize a recurrence result into a product-usable signal scale."""


def run(S0: dict, recurrence: dict) -> dict:
    """Return a normalized signal dictionary."""
    S0 = S0 or {}
    recurrence = recurrence or {}
    priority = "normal"
    if recurrence.get("seen") and recurrence.get("count", 0) > 1:
        priority = "high"

    return {
        "signal": "recurrence" if recurrence.get("seen") else "new",
        "priority": priority,
        "intent": S0.get("context", {}).get("action") or S0.get("context", {}).get("product"),
    }
