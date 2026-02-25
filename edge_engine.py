# edge_engine.py
# Relational Field Engine (edge-v0.1)
# Deterministic, rule-based detection of destabilizing interaction patterns.
# No motive inference. No psychology. No rewriting.

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

EDGE_VERSION = "edge-v0.1"

EDGE_WEIGHTS: Dict[str, float] = {
    "retroactive_attribution": 0.25,
    "vertical_claim": 0.20,
    "status_displacement": 0.15,
    "amplification_vector": 0.15,
    "escalation_syntax": 0.15,
    "dominance_posture": 0.10,
}

PATTERNS: List[Tuple[str, List[re.Pattern]]] = [
    ("retroactive_attribution", [
        re.compile(r"\byou\s+were\s+wrong\b", re.IGNORECASE),
        re.compile(r"\byou\s+made\s+(a|the)\s+mistake\b", re.IGNORECASE),
        re.compile(r"\bthat\s+was\s+your\s+fault\b", re.IGNORECASE),
        re.compile(r"\byou\s+did\s+that\b", re.IGNORECASE),
    ]),
    ("vertical_claim", [
        re.compile(r"\byou\s+(misunderstood|missed|failed)\b", re.IGNORECASE),
        re.compile(r"\byou\s+(thought|assumed)\b", re.IGNORECASE),
        re.compile(r"\byou\s+don'?t\s+get\b", re.IGNORECASE),
        re.compile(r"\blet\s+me\s+be\s+clear\b", re.IGNORECASE),
    ]),
    ("status_displacement", [
        re.compile(r"\bnot\s+about\s+[^â€”-]{1,120}\s*[â€”-]{1,2}\s*but\s+about\b", re.IGNORECASE),
        re.compile(r"\bnot\s+[^â€”-]{1,120}\s*[â€”-]{1,2}\s*but\s+\b", re.IGNORECASE),
        re.compile(r"\bit\s+wasn'?t\s+[^.]{1,120}\b", re.IGNORECASE),
    ]),
    ("amplification_vector", [
        re.compile(r"\bactually\b", re.IGNORECASE),
        re.compile(r"\bclearly\b", re.IGNORECASE),
        re.compile(r"\bobviously\b", re.IGNORECASE),
        re.compile(r"\bliterally\b", re.IGNORECASE),
    ]),
    ("escalation_syntax", [
        re.compile(r"\bthe\s+(real\s+)?issue\s+is\b", re.IGNORECASE),
        re.compile(r"\bhere'?s\s+the\s+problem\b", re.IGNORECASE),
        re.compile(r"\blet'?s\s+be\s+honest\b", re.IGNORECASE),
    ]),
    ("dominance_posture", [
        re.compile(r"\byou\s+need\s+to\b", re.IGNORECASE),
        re.compile(r"\byou\s+have\s+to\b", re.IGNORECASE),
        re.compile(r"\byou\s+can'?t\b", re.IGNORECASE),
    ]),
]


def compute_relational_field(text: str) -> Dict[str, Any]:
    """
    Returns:
      edge_index: 0..1 (higher = more destabilizing)
      edge_markers: matched phrases + weights (deterministic)
      triggered_patterns: unique triggered pattern keys
    """
    if text is None:
        text = ""

    markers: List[Dict[str, Any]] = []
    triggered = set()

    seen = set()
    for pattern_name, regex_list in PATTERNS:
        weight = float(EDGE_WEIGHTS.get(pattern_name, 0.0))
        for rgx in regex_list:
            for m in rgx.finditer(text):
                phrase = (m.group(0) or "").strip()
                key = (pattern_name, phrase.lower())
                if key in seen:
                    continue
                seen.add(key)
                triggered.add(pattern_name)
                markers.append({
                    "pattern": pattern_name,
                    "phrase": phrase,
                    "weight": round(weight, 4),
                })

    total_weight = sum(EDGE_WEIGHTS[p] for p in triggered) if triggered else 0.0
    edge_index = min(1.0, round(total_weight, 4))

    return {
        "field": "relational",
        "version": EDGE_VERSION,
        "edge_index": edge_index,
        "edge_markers": markers,
        "triggered_patterns": sorted(list(triggered)),
    }
