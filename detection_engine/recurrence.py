"""Detect recurrence of a signal pattern."""


def run(S0: dict, delta: dict) -> dict:
    """Return whether the detected pattern has occurred before."""
    S0 = S0 or {}
    delta = delta or {}
    prior_inputs = S0.get("prior_inputs", [])
    Q = S0.get("Q")
    count = prior_inputs.count(Q) if Q is not None else 0

    return {
        "seen": count > 0,
        "count": count,
        "last_seen": prior_inputs[-1] if prior_inputs else None,
        "delta": delta,
    }
