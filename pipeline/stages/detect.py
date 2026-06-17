"""Run detection collapse over the current task state."""

from detection_engine import collapse


def run(S0: dict) -> dict:
    """Attach normalized signal output to the current state and return it."""
    S0 = S0 or {}
    signal = collapse(S0)
    S0["signal"] = signal
    return S0
