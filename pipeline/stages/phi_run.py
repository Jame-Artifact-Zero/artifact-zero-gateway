"""Run the core engine scoring step for the current task state."""

from core_engine.service import dispatch


def run(S0: dict) -> dict:
    """Run NTI scoring through core_engine and return the response R."""
    S0 = S0 or {}
    result = dispatch(S0)

    return {
        "type": "response",
        "content": result
    }
