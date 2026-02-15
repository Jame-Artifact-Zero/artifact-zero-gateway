# core_engine/otc.py
# Objective Type Calculus (OTC) v1.0
# Closed set. Deterministic. No inference.

from __future__ import annotations
from typing import Any, Dict, List, Optional

OTC_VERSION = "otc-v1.0"

# Closed set of objective types
OBJECTIVE_TYPES = {
    "RESOLVE": {
        "description": "Eliminate ambiguity or reach a decision.",
        "allowed_transforms": ["DECLARE", "ADVANCE", "CLOSE", "CONSOLIDATE", "REPAIR"],
        "abstraction_range": (0, 2),  # L0-L2 allowed
    },
    "EXPLORE": {
        "description": "Expand the space without immediate convergence.",
        "allowed_transforms": ["DECLARE", "ADVANCE", "OPEN_BRANCH", "PARK", "CONSOLIDATE", "REPAIR"],
        "abstraction_range": (0, 4),  # L0-L4 allowed
    },
    "STRUCTURE": {
        "description": "Organize or compress state.",
        "allowed_transforms": ["DECLARE", "ADVANCE", "CLOSE", "CONSOLIDATE", "REPAIR"],
        "abstraction_range": (1, 3),  # L1-L3 allowed
    },
    "EXECUTE": {
        "description": "Produce artifact or take action.",
        "allowed_transforms": ["DECLARE", "ADVANCE", "CLOSE", "REPAIR"],
        "abstraction_range": (0, 1),  # L0-L1 only
    },
    "DIAGNOSE": {
        "description": "Identify root cause or failure.",
        "allowed_transforms": ["DECLARE", "ADVANCE", "OPEN_BRANCH", "CLOSE", "CONSOLIDATE", "REPAIR"],
        "abstraction_range": (0, 3),  # L0-L3 allowed
    },
    "ALIGN": {
        "description": "Synchronize understanding between parties.",
        "allowed_transforms": ["DECLARE", "ADVANCE", "OPEN_BRANCH", "PARK", "CLOSE", "CONSOLIDATE", "REPAIR"],
        "abstraction_range": (1, 4),  # L1-L4 allowed
    },
}

ALL_OBJECTIVE_NAMES: List[str] = list(OBJECTIVE_TYPES.keys())


def validate_objective(objective_type: str) -> Dict[str, Any]:
    """Validate that an objective type is in the closed set."""
    ot = (objective_type or "").strip().upper()
    if ot in OBJECTIVE_TYPES:
        return {
            "valid": True,
            "objective_type": ot,
            "spec": OBJECTIVE_TYPES[ot],
        }
    return {
        "valid": False,
        "objective_type": ot,
        "error": f"Unknown objective type '{ot}'. Must be one of: {ALL_OBJECTIVE_NAMES}",
    }


def get_allowed_transforms(objective_type: str) -> List[str]:
    """Return allowed transforms for the given objective type."""
    ot = (objective_type or "").strip().upper()
    spec = OBJECTIVE_TYPES.get(ot)
    if spec:
        return list(spec["allowed_transforms"])
    return []


def get_abstraction_range(objective_type: str) -> Optional[tuple]:
    """Return (min_level, max_level) allowed for the given objective type."""
    ot = (objective_type or "").strip().upper()
    spec = OBJECTIVE_TYPES.get(ot)
    if spec:
        return spec["abstraction_range"]
    return None


def declare_objective(objective_type: str, description: str = "") -> Dict[str, Any]:
    """
    Freeze an objective declaration. Returns session-ready objective state.
    """
    validation = validate_objective(objective_type)
    if not validation["valid"]:
        return {"status": "INVALID", "error": validation["error"]}

    ot = validation["objective_type"]
    return {
        "status": "DECLARED",
        "version": OTC_VERSION,
        "objective_type": ot,
        "description": (description or "").strip() or OBJECTIVE_TYPES[ot]["description"],
        "allowed_transforms": OBJECTIVE_TYPES[ot]["allowed_transforms"],
        "abstraction_range": OBJECTIVE_TYPES[ot]["abstraction_range"],
        "frozen": True,
    }
