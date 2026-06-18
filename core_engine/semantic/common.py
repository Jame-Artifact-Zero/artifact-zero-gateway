"""Shared semantic-layer helpers."""

from __future__ import annotations

from typing import Any, Dict, List


def make_signal_envelope(
    tool: str,
    input_type: str,
    signal: str,
    strength: float,
    evidence: List[str],
    detail: Dict[str, Any],
    fired: bool,
) -> Dict[str, Any]:
    return {
        "tool": str(tool),
        "input_type": str(input_type or "unknown"),
        "signal": str(signal),
        "strength": float(max(0.0, min(1.0, strength))),
        "evidence": list(evidence or [])[:25],
        "detail": dict(detail or {}),
        "fired": bool(fired),
    }
