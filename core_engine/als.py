# core_engine/als.py
# Abstraction Layer Stack (ALS) v1.0
# L0-L5 tracking. Delta detection. ±1 guard.
# Deterministic. No inference.

from __future__ import annotations
import re
from typing import Any, Dict, List, Optional, Tuple

ALS_VERSION = "als-v1.0"

# Abstraction levels (closed set)
ABSTRACTION_LEVELS = {
    0: {"name": "L0_CONCRETE", "description": "Specific facts, data, artifacts, literal references."},
    1: {"name": "L1_EXPLANATION", "description": "Causation, reasoning, how/why something works."},
    2: {"name": "L2_PATTERN", "description": "Recurring structures, templates, generalizations."},
    3: {"name": "L3_SYSTEM", "description": "Interconnected components, architecture, feedback loops."},
    4: {"name": "L4_INVARIANT", "description": "Principles, laws, constraints that do not change."},
    5: {"name": "L5_META_PHYSICS", "description": "Theory of theories, epistemology, foundational axioms."},
}

MAX_LEVEL = 5
MIN_LEVEL = 0
DEFAULT_ALLOWED_DELTA = 1  # ±1 default guard

# Rule-based markers for each level
LEVEL_MARKERS: List[Tuple[int, List[str]]] = [
    (0, [
        "specifically", "the file", "the code", "line ", "error message",
        "the value", "the number", "exactly", "literally", "data point",
        "this endpoint", "this function", "this variable", "the output",
    ]),
    (1, [
        "because", "the reason", "this means", "which causes", "as a result",
        "explains why", "due to", "how it works", "mechanism",
    ]),
    (2, [
        "pattern", "template", "recurring", "common approach", "typical",
        "generalize", "across cases", "in most scenarios", "framework",
        "model", "heuristic",
    ]),
    (3, [
        "architecture", "system", "feedback loop", "interconnected",
        "component", "dependency graph", "topology", "infrastructure",
        "end to end", "pipeline", "ecosystem",
    ]),
    (4, [
        "invariant", "principle", "law", "constraint", "must always",
        "cannot violate", "fundamental", "axiom", "non-negotiable",
        "core rule", "governing",
    ]),
    (5, [
        "theory of", "meta", "epistemology", "foundational assumption",
        "philosophy of", "nature of", "what is knowledge",
        "first principles beyond", "physics of physics",
    ]),
]


def detect_abstraction_level(text: str) -> Dict[str, Any]:
    """
    Rule-based abstraction level detection.
    Returns best-match level with markers.
    """
    t = (text or "").lower().strip()
    if not t:
        return {
            "version": ALS_VERSION,
            "detected_level": 0,
            "level_name": ABSTRACTION_LEVELS[0]["name"],
            "markers_matched": [],
            "scores": {},
        }

    scores: Dict[int, int] = {}
    all_markers: Dict[int, List[str]] = {}

    for level, markers in LEVEL_MARKERS:
        matched = [m for m in markers if m in t]
        if matched:
            scores[level] = len(matched)
            all_markers[level] = matched

    if not scores:
        # Default to L0 if no markers detected
        return {
            "version": ALS_VERSION,
            "detected_level": 0,
            "level_name": ABSTRACTION_LEVELS[0]["name"],
            "markers_matched": [],
            "scores": {},
        }

    # Highest scoring level wins
    best_level = max(scores, key=scores.get)

    return {
        "version": ALS_VERSION,
        "detected_level": best_level,
        "level_name": ABSTRACTION_LEVELS[best_level]["name"],
        "markers_matched": all_markers.get(best_level, []),
        "scores": scores,
    }


def compute_abstraction_delta(
    previous_level: int,
    current_level: int,
) -> Dict[str, Any]:
    """
    Compute delta between two abstraction levels.
    """
    prev = max(MIN_LEVEL, min(MAX_LEVEL, int(previous_level or 0)))
    curr = max(MIN_LEVEL, min(MAX_LEVEL, int(current_level or 0)))
    delta = curr - prev

    return {
        "previous_level": prev,
        "current_level": curr,
        "delta": delta,
        "abs_delta": abs(delta),
        "direction": "UP" if delta > 0 else ("DOWN" if delta < 0 else "STABLE"),
    }


def check_abstraction_guard(
    previous_level: int,
    current_level: int,
    allowed_delta: int = DEFAULT_ALLOWED_DELTA,
    allowed_range: Optional[Tuple[int, int]] = None,
) -> Dict[str, Any]:
    """
    Abstraction Delta Guard.
    Checks:
    1. Delta within ±allowed_delta
    2. Current level within allowed_range (if provided by objective type)
    """
    delta_info = compute_abstraction_delta(previous_level, current_level)
    violations: List[str] = []

    # Check ±delta guard
    if delta_info["abs_delta"] > allowed_delta:
        violations.append(
            f"Abstraction jump of {delta_info['delta']} exceeds allowed ±{allowed_delta}. "
            f"Previous: L{delta_info['previous_level']}, Current: L{delta_info['current_level']}."
        )

    # Check range guard (from objective type)
    if allowed_range is not None:
        lo, hi = allowed_range
        if delta_info["current_level"] < lo or delta_info["current_level"] > hi:
            violations.append(
                f"Abstraction level L{delta_info['current_level']} outside allowed range "
                f"L{lo}-L{hi} for active objective."
            )

    return {
        "version": ALS_VERSION,
        "delta": delta_info,
        "allowed_delta": allowed_delta,
        "allowed_range": allowed_range,
        "violations": violations,
        "passed": len(violations) == 0,
    }
