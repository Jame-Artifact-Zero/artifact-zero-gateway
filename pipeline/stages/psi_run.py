"""Persist the post-response state update for the current pipeline event."""

from patent_core.psi import StateUpdateFunction
from memory import buckets
from memory import schema


_PRODUCT_BUCKET_MAP = {
    "score": schema.SCORED_ITEMS,
    "email": schema.EMAIL_THREADS,
}


def run(S0: dict, R: dict) -> dict:
    """Write S1 through patent_core Psi and persist the event to memory buckets."""
    S0 = S0 or {}
    R = R or {}
    updater = StateUpdateFunction()
    S1 = updater.execute(S0, S0.get("Q", ""), R)

    user_id = S0.get("context", {}).get("user_id")
    product = S0.get("decision", {}).get("product")
    bucket_key = _PRODUCT_BUCKET_MAP.get(product)
    valid_bucket_keys = set(schema.BUCKET_SHAPES.keys())

    if user_id and bucket_key not in valid_bucket_keys:
        S1["memory_error"] = {
            "error": "invalid memory bucket key",
            "bucket_key": bucket_key,
            "product": product,
        }
        return S1

    if user_id:
        buckets.write(user_id, bucket_key, {"S0": S0, "R": R, "S1": S1})

    return S1
