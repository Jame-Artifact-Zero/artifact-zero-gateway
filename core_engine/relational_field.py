# core_engine/relational_field.py
# Relational Field Engine v0.1
# Deterministic. Rule-based. Additive only.

import re

RELATIONAL_VERSION = "relational-v0.1"

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

    if not text:
        text = ""

    triggered = set()
    markers = []

    for name, patterns in PATTERNS:
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                markers.append({
                    "pattern": name,
                    "phrase": match.group(0),
                    "weight": WEIGHTS[name]
                })
                triggered.add(name)

    total = sum(WEIGHTS[p] for p in triggered)
    index = min(1.0, round(total, 4))

    if index < 0.30:
        band = "LOW"
    elif index < 0.60:
        band = "MEDIUM"
    else:
        band = "HIGH"

    return {
        "field": "relational",
        "version": RELATIONAL_VERSION,
        "relational_index": index,
        "relational_band": band,
        "relational_markers": markers,
        "triggered_patterns": list(triggered)
    }
