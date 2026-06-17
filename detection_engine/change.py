"""Measure change from the baseline state."""


def run(S0: dict, baseline: dict) -> dict:
    """Return a delta dictionary comparing S0 against the baseline."""
    S0 = S0 or {}
    baseline = baseline or {}
    return {
        "changed": False,
        "baseline": baseline,
        "current_product": S0.get("context", {}).get("product"),
    }
