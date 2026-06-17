"""Assemble the minimal existence coordinate for a pipeline task."""

from datetime import datetime, timezone

from patent_core.existence import LayeredExistenceCoordinate, ExistenceCoordinate


def assemble(S0: dict) -> dict:
    """Build a coordinate dictionary with who, where, when, and why."""
    S0 = S0 or {}

    return LayeredExistenceCoordinate(
        where=S0.get("where", "unknown"),
        when=S0.get("when", "unknown"),
        for_=S0.get("who", "unknown"),
        why=S0.get("why", "unknown"),
    )
