"""Assemble the minimal existence coordinate for a pipeline task."""

from datetime import datetime, timezone

from patent_core.existence import LayeredExistenceCoordinate, ExistenceCoordinate


def assemble(S0: dict) -> dict:
    """Build a coordinate dictionary with who, where, when, and why."""
    S0 = S0 or {}
    context = S0.get("context", {})
    if not isinstance(context, dict):
        context = {}

    return LayeredExistenceCoordinate(
        where=S0.get("context", "unknown"),
        when=datetime.now(timezone.utc).isoformat(),
        for_=S0.get("user_id") or context.get("user_id", "unknown"),
        why=S0.get("action") or context.get("action", "unknown"),
    )
