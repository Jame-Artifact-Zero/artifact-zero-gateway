"""Parse a raw HTTP event into the initial pipeline state."""


def run(event: dict) -> dict:
    """Parse the raw event, identify product context, and return Q, S0, and context."""
    event = event or {}
    payload = event.get("json") or event.get("payload") or event
    product = payload.get("product") or payload.get("context") or "score"
    Q = payload.get("Q") or payload.get("text") or payload.get("message") or ""

    return {
        "Q": Q,
        "S0": payload.get("S0", {}),
        "context": {
            "product": product,
            "event": payload,
            "user_id": payload.get("user_id"),
            "action": payload.get("action"),
        },
    }
