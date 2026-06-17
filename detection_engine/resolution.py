"""Normalize a recurrence result into a product-usable signal scale."""


def run(S0: dict, recurrence: dict) -> dict:
    """Return a normalized signal dictionary."""
    S0 = S0 or {}
    recurrence = recurrence or {}
    context = S0.get("context", {})
    product = context.get("product")
    action = context.get("action")
    priority = "normal"
    if recurrence.get("seen") and recurrence.get("count", 0) > 1:
        priority = "high"

    if product == "email":
        intent = "email"
    else:
        intent = action or product

    return {
        "signal": "recurrence" if recurrence.get("seen") else "new",
        "priority": priority,
        "intent": intent,
    }
