"""Persist the post-response state update for the current pipeline event."""

from patent_core.psi import StateUpdateFunction
from memory import buckets


def run(S0: dict, R: dict) -> dict:
    """Write S1 through patent_core Psi and persist the event to memory buckets."""
    S0 = S0 or {}
    R = R or {}
    updater = StateUpdateFunction()
    S1 = updater.execute(S0, S0.get("Q", ""), R)

    user_id = S0.get("context", {}).get("user_id")
    bucket_key = S0.get("decision", {}).get("product") or "pipeline_events"
    if user_id:
        buckets.write(user_id, bucket_key, {"S0": S0, "R": R, "S1": S1})

    return S1
