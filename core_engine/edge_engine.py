# edge_engine.py
# Relational Field Engine (edge-v0.2)
# Deterministic, rule-based detection of destabilizing interaction patterns.
# No motive inference. No psychology. No rewriting.
# Expanded: 16 categories, ~120 regex patterns.

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

EDGE_VERSION = "edge-v0.2"

EDGE_WEIGHTS: Dict[str, float] = {
    # Original 6
    "retroactive_attribution": 0.25,
    "vertical_claim": 0.20,
    "status_displacement": 0.15,
    "amplification_vector": 0.15,
    "escalation_syntax": 0.15,
    "dominance_posture": 0.10,
    # New 10
    "gaslighting_marker": 0.25,
    "false_consensus": 0.15,
    "emotional_leverage": 0.20,
    "authority_assertion": 0.15,
    "dismissal_pattern": 0.20,
    "guilt_induction": 0.20,
    "ultimatum_syntax": 0.20,
    "passive_aggression": 0.15,
    "credit_displacement": 0.15,
    "boundary_violation": 0.20,
}

PATTERNS: List[Tuple[str, List[re.Pattern]]] = [
    ("retroactive_attribution", [
        re.compile(r"\byou\s+were\s+wrong\b", re.IGNORECASE),
        re.compile(r"\byou\s+made\s+(a|the)\s+mistake\b", re.IGNORECASE),
        re.compile(r"\bthat\s+was\s+your\s+fault\b", re.IGNORECASE),
        re.compile(r"\byou\s+did\s+that\b", re.IGNORECASE),
        re.compile(r"\byou\s+caused\s+this\b", re.IGNORECASE),
        re.compile(r"\bthis\s+is\s+on\s+you\b", re.IGNORECASE),
        re.compile(r"\byou\s+dropped\s+the\s+ball\b", re.IGNORECASE),
        re.compile(r"\bif\s+you\s+had(n't)?\b", re.IGNORECASE),
        re.compile(r"\byou\s+should\s+have\b", re.IGNORECASE),
        re.compile(r"\byou\s+could\s+have\b", re.IGNORECASE),
    ]),
    ("vertical_claim", [
        re.compile(r"\byou\s+(misunderstood|missed|failed)\b", re.IGNORECASE),
        re.compile(r"\byou\s+(thought|assumed)\b", re.IGNORECASE),
        re.compile(r"\byou\s+don'?t\s+get\b", re.IGNORECASE),
        re.compile(r"\blet\s+me\s+be\s+clear\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+not\s+(seeing|understanding|getting)\b", re.IGNORECASE),
        re.compile(r"\bthat'?s\s+not\s+what\s+i\s+(said|meant)\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+missing\s+the\s+point\b", re.IGNORECASE),
        re.compile(r"\bi\s+never\s+said\s+that\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+confused\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+overthinking\b", re.IGNORECASE),
    ]),
    ("status_displacement", [
        re.compile(r"\bit\s+wasn'?t\s+[^.]{1,120}\b", re.IGNORECASE),
        re.compile(r"\bnot\s+about\s+[^.]{1,80}\s*[-\u2013\u2014]\s*but\s+about\b", re.IGNORECASE),
        re.compile(r"\bthe\s+real\s+question\s+is\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+matters\s+here\s+is\b", re.IGNORECASE),
        re.compile(r"\bthe\s+point\s+is\b", re.IGNORECASE),
    ]),
    ("amplification_vector", [
        re.compile(r"\bactually\b", re.IGNORECASE),
        re.compile(r"\bclearly\b", re.IGNORECASE),
        re.compile(r"\bobviously\b", re.IGNORECASE),
        re.compile(r"\bliterally\b", re.IGNORECASE),
        re.compile(r"\bfundamentally\b", re.IGNORECASE),
        re.compile(r"\babsolutely\b", re.IGNORECASE),
        re.compile(r"\bcompletely\b", re.IGNORECASE),
        re.compile(r"\btotally\b", re.IGNORECASE),
        re.compile(r"\bentirely\b", re.IGNORECASE),
        re.compile(r"\bextremely\b", re.IGNORECASE),
        re.compile(r"\bprofoundly\b", re.IGNORECASE),
        re.compile(r"\bindisputably\b", re.IGNORECASE),
        re.compile(r"\bunquestionably\b", re.IGNORECASE),
    ]),
    ("escalation_syntax", [
        re.compile(r"\bthe\s+(real\s+)?issue\s+is\b", re.IGNORECASE),
        re.compile(r"\bhere'?s\s+the\s+problem\b", re.IGNORECASE),
        re.compile(r"\blet'?s\s+be\s+honest\b", re.IGNORECASE),
        re.compile(r"\bthe\s+truth\s+is\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+really\s+happened\b", re.IGNORECASE),
        re.compile(r"\bhere'?s\s+what\s+you'?re\s+not\s+seeing\b", re.IGNORECASE),
        re.compile(r"\bthe\s+fact\s+of\s+the\s+matter\b", re.IGNORECASE),
        re.compile(r"\blet\s+me\s+tell\s+you\b", re.IGNORECASE),
        re.compile(r"\bi'?ll\s+tell\s+you\s+what\b", re.IGNORECASE),
        re.compile(r"\bwake\s+up\b", re.IGNORECASE),
    ]),
    ("dominance_posture", [
        re.compile(r"\byou\s+need\s+to\b", re.IGNORECASE),
        re.compile(r"\byou\s+have\s+to\b", re.IGNORECASE),
        re.compile(r"\byou\s+can'?t\b", re.IGNORECASE),
        re.compile(r"\byou\s+must\b", re.IGNORECASE),
        re.compile(r"\byou\s+will\b", re.IGNORECASE),
        re.compile(r"\byou\s+shall\b", re.IGNORECASE),
        re.compile(r"\byou\s+better\b", re.IGNORECASE),
        re.compile(r"\byou'?d\s+better\b", re.IGNORECASE),
        re.compile(r"\bdo\s+as\s+i\s+say\b", re.IGNORECASE),
        re.compile(r"\bthat'?s\s+final\b", re.IGNORECASE),
        re.compile(r"\bend\s+of\s+discussion\b", re.IGNORECASE),
        re.compile(r"\bi\s+don'?t\s+want\s+to\s+hear\b", re.IGNORECASE),
    ]),
    ("gaslighting_marker", [
        re.compile(r"\bthat\s+never\s+happened\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+(imagining|making)\s+(things|it)\s+up\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+being\s+(too\s+)?(sensitive|dramatic|emotional|paranoid)\b", re.IGNORECASE),
        re.compile(r"\bi\s+was\s+just\s+joking\b", re.IGNORECASE),
        re.compile(r"\byou\s+can'?t\s+take\s+a\s+joke\b", re.IGNORECASE),
        re.compile(r"\bno\s+one\s+else\s+(thinks|feels|sees)\s+that\b", re.IGNORECASE),
        re.compile(r"\byou'?re\s+the\s+only\s+one\b", re.IGNORECASE),
        re.compile(r"\beveryone\s+(agrees|thinks)\b", re.IGNORECASE),
        re.compile(r"\bthat'?s\s+not\s+how\s+it\s+happened\b", re.IGNORECASE),
        re.compile(r"\byou\s+always\s+do\s+this\b", re.IGNORECASE),
    ]),
    ("false_consensus", [
        re.compile(r"\beveryone\s+(knows|agrees|thinks|says|believes)\b", re.IGNORECASE),
        re.compile(r"\bnobody\s+(thinks|believes|agrees)\b", re.IGNORECASE),
        re.compile(r"\bit'?s\s+common\s+knowledge\b", re.IGNORECASE),
        re.compile(r"\bask\s+anyone\b", re.IGNORECASE),
        re.compile(r"\bthe\s+team\s+(agrees|feels|thinks)\b", re.IGNORECASE),
        re.compile(r"\bwe\s+all\s+(know|agree|think)\b", re.IGNORECASE),
        re.compile(r"\bmost\s+people\s+(would|think|agree)\b", re.IGNORECASE),
    ]),
    ("emotional_leverage", [
        re.compile(r"\bafter\s+everything\s+i'?ve\s+done\b", re.IGNORECASE),
        re.compile(r"\bi\s+sacrificed\b", re.IGNORECASE),
        re.compile(r"\bi\s+gave\s+up\b", re.IGNORECASE),
        re.compile(r"\byou\s+owe\s+me\b", re.IGNORECASE),
        re.compile(r"\bi\s+deserve\b", re.IGNORECASE),
        re.compile(r"\bhow\s+could\s+you\b", re.IGNORECASE),
        re.compile(r"\bi\s+can'?t\s+believe\s+you\b", re.IGNORECASE),
        re.compile(r"\byou\s+don'?t\s+(care|appreciate)\b", re.IGNORECASE),
        re.compile(r"\bi'?m\s+so\s+disappointed\b", re.IGNORECASE),
        re.compile(r"\byou\s+hurt\s+me\b", re.IGNORECASE),
    ]),
    ("authority_assertion", [
        re.compile(r"\bi'?m\s+the\s+(boss|manager|owner|ceo|director)\b", re.IGNORECASE),
        re.compile(r"\bi\s+have\s+more\s+experience\b", re.IGNORECASE),
        re.compile(r"\bi'?ve\s+been\s+doing\s+this\s+for\b", re.IGNORECASE),
        re.compile(r"\btrust\s+me\b", re.IGNORECASE),
        re.compile(r"\bbecause\s+i\s+said\s+so\b", re.IGNORECASE),
        re.compile(r"\bi\s+know\s+what\s+i'?m\s+(doing|talking\s+about)\b", re.IGNORECASE),
        re.compile(r"\bwith\s+all\s+due\s+respect\b", re.IGNORECASE),
        re.compile(r"\bno\s+offense\b", re.IGNORECASE),
        re.compile(r"\bnot\s+to\s+be\s+rude\b", re.IGNORECASE),
    ]),
    ("dismissal_pattern", [
        re.compile(r"\bwhatever\b", re.IGNORECASE),
        re.compile(r"\bit\s+doesn'?t\s+matter\b", re.IGNORECASE),
        re.compile(r"\bwho\s+cares\b", re.IGNORECASE),
        re.compile(r"\bthat'?s\s+(irrelevant|not\s+important)\b", re.IGNORECASE),
        re.compile(r"\bget\s+over\s+it\b", re.IGNORECASE),
        re.compile(r"\bmove\s+on\b", re.IGNORECASE),
        re.compile(r"\bstop\s+(complaining|whining|being)\b", re.IGNORECASE),
        re.compile(r"\bnot\s+my\s+problem\b", re.IGNORECASE),
        re.compile(r"\bnot\s+my\s+(fault|issue|concern)\b", re.IGNORECASE),
        re.compile(r"\byeah\s+yeah\b", re.IGNORECASE),
        re.compile(r"\bsure\s+sure\b", re.IGNORECASE),
    ]),
    ("guilt_induction", [
        re.compile(r"\bif\s+you\s+(really|truly)\s+(cared|loved|respected)\b", re.IGNORECASE),
        re.compile(r"\ba\s+(good|real|true)\s+(friend|partner|employee)\s+would\b", re.IGNORECASE),
        re.compile(r"\bi\s+guess\s+i'?ll\s+just\b", re.IGNORECASE),
        re.compile(r"\bfine\s*,?\s*i'?ll\s+do\s+it\s+myself\b", re.IGNORECASE),
        re.compile(r"\bdon'?t\s+worry\s+about\s+me\b", re.IGNORECASE),
        re.compile(r"\bi'?m\s+used\s+to\s+it\b", re.IGNORECASE),
        re.compile(r"\bstory\s+of\s+my\s+life\b", re.IGNORECASE),
    ]),
    ("ultimatum_syntax", [
        re.compile(r"\bor\s+else\b", re.IGNORECASE),
        re.compile(r"\blast\s+(chance|warning|time)\b", re.IGNORECASE),
        re.compile(r"\bfinal\s+(offer|warning|notice)\b", re.IGNORECASE),
        re.compile(r"\btake\s+it\s+or\s+leave\s+it\b", re.IGNORECASE),
        re.compile(r"\bif\s+you\s+don'?t\s*,?\s*(then\s+)?i\s+will\b", re.IGNORECASE),
        re.compile(r"\bdon'?t\s+make\s+me\b", re.IGNORECASE),
        re.compile(r"\byou\s+leave\s+me\s+no\s+choice\b", re.IGNORECASE),
        re.compile(r"\bthis\s+is\s+(your|the)\s+last\b", re.IGNORECASE),
    ]),
    ("passive_aggression", [
        re.compile(r"\bthat'?s\s+fine\b", re.IGNORECASE),
        re.compile(r"\bno\s+worries\b", re.IGNORECASE),
        re.compile(r"\bi'?m\s+not\s+(mad|upset|angry)\b", re.IGNORECASE),
        re.compile(r"\bdo\s+whatever\s+you\s+want\b", re.IGNORECASE),
        re.compile(r"\bi\s+just\s+think\s+it'?s\s+(funny|interesting)\s+that\b", re.IGNORECASE),
        re.compile(r"\bper\s+my\s+(last|previous)\s+email\b", re.IGNORECASE),
        re.compile(r"\bas\s+(previously|already)\s+(stated|mentioned|noted|discussed)\b", re.IGNORECASE),
        re.compile(r"\bi\s+thought\s+(we|you)\s+(agreed|said|decided)\b", re.IGNORECASE),
    ]),
    ("credit_displacement", [
        re.compile(r"\bthat\s+was\s+my\s+idea\b", re.IGNORECASE),
        re.compile(r"\bi\s+came\s+up\s+with\s+that\b", re.IGNORECASE),
        re.compile(r"\bi\s+told\s+you\s+so\b", re.IGNORECASE),
        re.compile(r"\bi\s+said\s+that\s+(first|before)\b", re.IGNORECASE),
        re.compile(r"\bif\s+it\s+wasn'?t\s+for\s+me\b", re.IGNORECASE),
        re.compile(r"\bwithout\s+me\b", re.IGNORECASE),
        re.compile(r"\byou\s+wouldn'?t\s+have\b", re.IGNORECASE),
    ]),
    ("boundary_violation", [
        re.compile(r"\bwhy\s+can'?t\s+you\s+just\b", re.IGNORECASE),
        re.compile(r"\byou\s+always\b", re.IGNORECASE),
        re.compile(r"\byou\s+never\b", re.IGNORECASE),
        re.compile(r"\bi\s+have\s+a\s+right\s+to\s+know\b", re.IGNORECASE),
        re.compile(r"\byou\s+should(n'?t)?\s+have\s+to\b", re.IGNORECASE),
        re.compile(r"\bthat'?s\s+not\s+(fair|right)\b", re.IGNORECASE),
        re.compile(r"\bi\s+thought\s+we\s+were\b", re.IGNORECASE),
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
