# core_engine/relational_engine.py
# Relational Field Engine v1.0
# Deterministic. Rule-based. Non-invasive.

import re

RELATIONAL_VERSION = "relational-v1.0"

WEIGHTS = {
    "retroactive_attribution": 0.25,
    "vertical_claim": 0.20,
    "status_displacement": 0.15,
    "amplification_vector": 0.15,
    "escalation_syntax": 0.15,
    "dominance_posture": 0.10,
}

PATTERNS = [
    ("retroactive_attribution", [
        r"\byou\s+were\s+wrong\b",
        r"\byou\s+made\s+(a|the)\s+mistake\b",
        r"\bthat\s+was\s+your\s+fault\b",
    ]),
    ("vertical_claim", [
        r"\byou\s+(misunderstood|missed|failed)\b",
        r"\byou\s+(thought|assumed)\b",
    ]),
    ("status_displacement", [
        r"\bnot\s+about\s+.*?\s*[—-]{1,2}\s*but\s+about\b",
        r"\bnot\s+.*?\s*[—-]{1,2}\s*but\b",
    ]),
    ("amplification_vector", [
        r"\bactually\b",
        r"\bclearly\b",
        r"\bobviously\b",
    ]),
    ("escalation_syntax", [
        r"\bthe\s+(real\s+)?issue\s+is\b",
        r"\bit\s+wasn'?t\b",
    ]),
    ("dominance_posture", [
        r"\byou\s+need\s+to\b",
        r"\byou\s+have\s+to\b",
    ]),
]


def compute_relational_field(text: str):

    if text is None:
        text = ""

    triggered = set()
    markers = []

    for name, regex_list in PATTERNS:
        for pattern in regex_list:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                triggered.add(name)
                markers.append({
                    "pattern": name,
                    "phrase": match.group(0),
                    "weight": WEIGHTS[name]
                })

    total = sum(WEIGHTS[p] for p in triggered)
    edge_index = min(1.0, round(total, 4))

    if edge_index < 0.30:
        band = "LOW"
    elif edge_index < 0.60:
        band = "MEDIUM"
    else:
        band = "HIGH"

    route_hint = None
    if band == "HIGH":
        route_hint = "HUMAN_REQUIRED"

    return {
        "field": "relational",
        "version": RELATIONAL_VERSION,
        "edge_index": edge_index,
        "band": band,
        "route_hint": route_hint,
        "markers": markers,
        "triggered_patterns": list(triggered)
    }
