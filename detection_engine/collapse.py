"""Collapse distinction, change, recurrence, and resolution into one signal."""

from detection_engine import distinction
from detection_engine import change
from detection_engine import recurrence
from detection_engine import resolution


def collapse(S0: dict) -> dict:
    """Run the detection sequence and return a normalized signal dictionary."""
    S0 = S0 or {}
    baseline = distinction.run(S0)
    delta = change.run(S0, baseline)
    recurrence_result = recurrence.run(S0, delta)
    return resolution.run(S0, recurrence_result)
