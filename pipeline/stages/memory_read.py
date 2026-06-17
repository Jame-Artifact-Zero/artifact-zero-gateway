"""Read relevant memory buckets and merge them into the current pipeline state."""

from memory import assembler


def run(S0: dict) -> dict:
    """Assemble relevant memory state for the current context and return it."""
    return assembler.assemble(S0 or {})
