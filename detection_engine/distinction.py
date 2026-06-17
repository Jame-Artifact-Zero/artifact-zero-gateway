"""Establish the baseline distinction for the current state."""


def run(S0: dict) -> dict:
    """Return a baseline dictionary derived from the current state."""
    S0 = S0 or {}
    return {
        "product": S0.get("context", {}).get("product"),
        "has_memory": bool(S0.get("memory")),
        "has_existence": bool(S0.get("existence")),
    }
