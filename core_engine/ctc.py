# core_engine/ctc.py
# Conversational Transform Calculus (CTC) v1.0
# 7 closed primitives. Deterministic classification. No inference.

from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple

CTC_VERSION = "ctc-v1.0"

# The 7 transform primitives (closed set)
TRANSFORMS = {
    "DECLARE": {
        "description": "Create/pin objective + constraints + variables.",
        "effect": "Stabilizes frame.",
    },
    "ADVANCE": {
        "description": "Reduce structured uncertainty inside current branch.",
        "effect": "Must reduce |V| or increase resolved state.",
    },
    "OPEN_BRANCH": {
        "description": "Create subordinate branch while pinning parent.",
        "effect": "Requires naming. Parent remains active or parked.",
    },
    "PARK": {
        "description": "Suspend branch without closing it.",
        "effect": "Keeps state, prevents accidental re-entry.",
    },
    "CLOSE": {
        "description": "Irreversibly terminate a branch or objective.",
        "effect": "Done / resolved / merged / discarded.",
    },
    "CONSOLIDATE": {
        "description": "Merge redundant paths or compress state.",
        "effect": "Reduces branch count without losing resolution.",
    },
    "REPAIR": {
        "description": "Fix structural violation or drift.",
        "effect": "Restores legal state without changing objective.",
    },
}

ALL_TRANSFORM_NAMES: List[str] = list(TRANSFORMS.keys())

# Rule-based markers for each transform type
TRANSFORM_MARKERS: List[Tuple[str, List[str]]] = [
    ("DECLARE", [
        "the objective is", "goal is", "we need to", "task is",
        "define", "scope is", "requirement is", "constraint is",
        "the question is", "problem statement",
    ]),
    ("ADVANCE", [
        "here is", "the answer is", "result:", "output:",
        "step 1", "step 2", "step 3", "next step",
        "therefore", "which means", "this gives us",
        "solution:", "resolved:", "decided:",
    ]),
    ("OPEN_BRANCH", [
        "side note", "tangent", "related topic", "by the way",
        "what about", "also consider", "another angle",
        "let me explore", "branching to", "separately",
    ]),
    ("PARK", [
        "park that", "hold that", "come back to that",
        "later", "set aside", "table that", "not now",
        "save for later", "revisit",
    ]),
    ("CLOSE", [
        "done", "resolved", "shipped", "closed", "finalized",
        "complete", "merged", "discarded", "decision made",
        "no further action", "locked",
    ]),
    ("CONSOLIDATE", [
        "summarize", "compress", "combine", "merge",
        "in summary", "net net", "boils down to",
        "consolidate", "tighten", "reduce to",
    ]),
    ("REPAIR", [
        "correction", "actually", "let me fix", "that was wrong",
        "clarification", "to be clear", "restate", "amend",
        "back on track", "re-anchor", "realign",
    ]),
]


def classify_transform(text: str) -> Dict[str, Any]:
    """
    Rule-based transform classification.
    Returns detected transform(s) with confidence markers.
    No inference. No LLM.
    """
    t = (text or "").lower().strip()
    if not t:
        return {
            "version": CTC_VERSION,
            "detected_transforms": [],
            "primary_transform": None,
            "markers_matched": [],
        }

    hits: List[Dict[str, Any]] = []

    for transform_name, markers in TRANSFORM_MARKERS:
        matched = [m for m in markers if m in t]
        if matched:
            hits.append({
                "transform": transform_name,
                "markers": matched,
                "marker_count": len(matched),
            })

    # Sort by marker count descending — highest match wins
    hits.sort(key=lambda x: x["marker_count"], reverse=True)

    primary = hits[0]["transform"] if hits else "ADVANCE"  # default: assume advance

    return {
        "version": CTC_VERSION,
        "detected_transforms": [h["transform"] for h in hits],
        "primary_transform": primary,
        "markers_matched": hits,
    }


def is_legal_transform(transform: str, allowed_transforms: List[str]) -> bool:
    """Check if a transform is legal under the active objective type."""
    return (transform or "").strip().upper() in (allowed_transforms or [])


def audit_transform(
    text: str,
    allowed_transforms: List[str],
) -> Dict[str, Any]:
    """
    Classify the transform in text and check legality against allowed set.
    Returns classification + legality verdict.
    """
    classification = classify_transform(text)
    primary = classification["primary_transform"]
    legal = is_legal_transform(primary, allowed_transforms)

    return {
        "version": CTC_VERSION,
        "classification": classification,
        "primary_transform": primary,
        "legal": legal,
        "allowed_transforms": allowed_transforms,
        "violation": None if legal else f"Transform '{primary}' is illegal under current objective. Allowed: {allowed_transforms}",
    }
