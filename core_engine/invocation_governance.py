# invocation_governance.py
# Axis 4 - Invocation Governance (invocation-v1.0)
# Advisory only. Deterministic. No enforcement.

from __future__ import annotations

from typing import Any, Dict

INVOCATION_VERSION = "invocation-v1.0"


def compute_invocation_governance(structural_score: float, edge_index: float) -> Dict[str, Any]:
    """
    Inputs:
      structural_score: 0..1 (higher = cleaner structure)
      edge_index: 0..1 (higher = more relational destabilization)

    Outputs:
      route_hint:
        AI_OPTIMAL
        CLARIFICATION_REQUIRED
        HUMAN_RECOMMENDED
        HUMAN_REQUIRED
    """
    structural_score = float(structural_score or 0.0)
    edge_index = float(edge_index or 0.0)

    if structural_score >= 0.75 and edge_index < 0.60:
        route_hint = "AI_OPTIMAL"
    elif structural_score < 0.75 and edge_index < 0.40:
        route_hint = "CLARIFICATION_REQUIRED"
    elif structural_score >= 0.75 and edge_index >= 0.60:
        route_hint = "HUMAN_RECOMMENDED"
    else:
        route_hint = "HUMAN_REQUIRED"

    return {
        "version": INVOCATION_VERSION,
        "route_hint": route_hint,
        "advisory_only": True,
    }
