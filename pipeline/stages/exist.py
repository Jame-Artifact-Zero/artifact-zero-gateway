"""Assemble existence coordinates for the current pipeline state."""

from existence import assembler


def run(S0: dict) -> dict:
    """Add existence coordinates to the current state and return the updated state."""
    S0 = S0 or {}
    coordinate = assembler.assemble(S0)
    S0["existence"] = coordinate.as_dict()
    return S0
