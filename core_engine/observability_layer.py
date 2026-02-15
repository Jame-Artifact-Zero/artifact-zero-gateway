# observability_layer.py
# Axis 4 - Deterministic Observability Composite (obs-v1.0)
# Produces replay-safe composite + banding components.

from __future__ import annotations

from typing import Any, Dict

OBS_VERSION = "obs-v1.0"


def compute_observability(structural_score: float, edge_index: float) -> Dict[str, Any]:
    """
    composite_score here is "risk composite": 0=stable, 1=critical.
    structural contributes stability, relational contributes risk.
    """
    s = float(structural_score or 0.0)
    e = float(edge_index or 0.0)

    # convert structural stability into risk component
    structural_risk = 1.0 - max(0.0, min(1.0, s))
    relational_risk = max(0.0, min(1.0, e))

    composite = round((structural_risk * 0.60) + (relational_risk * 0.40), 4)

    return {
        "version": OBS_VERSION,
        "composite_score": composite,
        "components": {
            "structural_risk": round(structural_risk, 4),
            "relational_risk": round(relational_risk, 4),
        },
    }
