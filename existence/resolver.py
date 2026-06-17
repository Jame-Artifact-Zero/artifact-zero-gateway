"""Resolve unknown existence-coordinate values where possible."""


def resolve(coordinate: dict) -> dict:
    """Return the coordinate with unknown values filled when possible."""
    coordinate = coordinate or {}
    return {
        "who": coordinate.get("who") or "unknown",
        "where": coordinate.get("where") or "unknown",
        "when": coordinate.get("when") or "unknown",
        "why": coordinate.get("why") or "unknown",
    }
