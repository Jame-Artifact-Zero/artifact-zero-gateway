# core_engine/salience.py
# Salience Transform Set v1.0
# 6 closed operators. Detection only. No reweighting.
# Deterministic. No inference.

from __future__ import annotations
from typing import Any, Dict, List, Tuple

SALIENCE_VERSION = "salience-v1.0"

# Closed set of salience transforms
SALIENCE_TRANSFORMS = {
    "ELEVATE": {
        "description": "Increase priority weight of a variable, constraint, or dimension.",
        "effect": "Reweights gradient toward X.",
    },
    "SUPPRESS": {
        "description": "Decrease priority weight of a dimension without deleting it.",
        "effect": "Reduces gradient influence of Y.",
    },
    "RESTORE": {
        "description": "Return to previously dominant salience ordering.",
        "effect": "Reinstates last stable priority state.",
    },
    "FREEZE": {
        "description": "Temporarily prevent salience reweighting.",
        "effect": "Locks gradient ordering until explicit release.",
    },
    "ESCALATE": {
        "description": "Increase abstraction priority or altitude.",
        "effect": "Raises abstraction weighting.",
    },
    "COMPRESS": {
        "description": "Increase convergence pressure.",
        "effect": "Increases compression gradient.",
    },
}

ALL_SALIENCE_NAMES: List[str] = list(SALIENCE_TRANSFORMS.keys())

# Rule-based markers
SALIENCE_MARKERS: List[Tuple[str, List[str]]] = [
    ("ELEVATE", [
        "this is the important part", "focus on", "prioritize",
        "optimize for", "matters most", "key point", "critical",
        "most important", "highlight", "emphasize", "weight toward",
        "defensibility matters", "the priority is",
    ]),
    ("SUPPRESS", [
        "don't worry about", "ignore", "skip", "not important",
        "deprioritize", "less emphasis", "doesn't matter",
        "not developing", "idea thread only", "not relevant",
        "set aside", "low priority", "drop",
    ]),
    ("RESTORE", [
        "get back on track", "back to", "return to", "re-anchor",
        "original topic", "as we were", "main objective",
        "refocus", "where were we", "restore focus",
    ]),
    ("FREEZE", [
        "don't narrow", "don't optimize yet", "no picking one",
        "stay at this layer", "hold position", "don't converge",
        "keep exploring", "don't close", "leave it open",
        "maintain breadth", "no premature",
    ]),
    ("ESCALATE", [
        "push", "stretch", "go higher", "zoom out",
        "go deeper", "more abstract", "bigger picture",
        "step back", "meta level", "higher order",
    ]),
    ("COMPRESS", [
        "proceed", "summarize", "tighten", "less words",
        "shorter", "compress", "bottom line", "cut to",
        "just the answer", "no fluff", "brief",
        "concise", "net it out",
    ]),
]


def detect_salience_transforms(text: str) -> Dict[str, Any]:
    """
    Rule-based salience transform detection.
    Returns all detected transforms with markers.
    Detection only — does not execute reweighting.
    """
    t = (text or "").lower().strip()
    if not t:
        return {
            "version": SALIENCE_VERSION,
            "detected": [],
            "markers_matched": [],
        }

    hits: List[Dict[str, Any]] = []

    for transform_name, markers in SALIENCE_MARKERS:
        matched = [m for m in markers if m in t]
        if matched:
            hits.append({
                "transform": transform_name,
                "markers": matched,
                "marker_count": len(matched),
            })

    hits.sort(key=lambda x: x["marker_count"], reverse=True)

    return {
        "version": SALIENCE_VERSION,
        "detected": [h["transform"] for h in hits],
        "markers_matched": hits,
    }


def log_salience_event(
    transform_name: str,
    target: str = "",
    context: str = "",
) -> Dict[str, Any]:
    """
    Produce a deterministic salience event log entry.
    No execution. Log only.
    """
    tn = (transform_name or "").strip().upper()
    valid = tn in SALIENCE_TRANSFORMS

    return {
        "version": SALIENCE_VERSION,
        "transform": tn,
        "valid": valid,
        "target": (target or "").strip(),
        "context": (context or "").strip()[:200],
        "error": None if valid else f"Unknown salience transform '{tn}'. Must be one of: {ALL_SALIENCE_NAMES}",
    }
