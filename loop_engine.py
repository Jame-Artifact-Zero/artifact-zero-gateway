"""
loop_engine.py
--------------
E06 Silent Loop Detection (rule-based, no LLM).

Detects when a conversation/request appears to be looping without reducing open variables.
Designed for single-text inputs, but can also operate on multi-message concatenations.

Outputs:
  {
    loop_score: 0..1,
    triggers: [{rule_id, phrase, evidence}],
    axis: "loop"
  }
"""

from typing import Dict, Any, List
import re


RULES = [
    ("REPEAT_PROCEED", re.compile(r"\b(proceed|continue)\b", re.I), 0.25),
    ("META_COMPLAINT_LOOP", re.compile(r"\b(loop|stuck|again|we already did this)\b", re.I), 0.35),
    ("QUESTION_SPAM", re.compile(r"\?\s*(\?\s*){2,}$", re.M), 0.25),
    ("ABSTRACT_NO_ACTION", re.compile(r"\b(concept|framework|theory)\b", re.I), 0.10),
    ("NO_DELIVERABLE", re.compile(r"\b(soon|later|eventually|we can)\b", re.I), 0.15),
]


def detect_silent_loop(text: str) -> Dict[str, Any]:
    score = 0.0
    triggers: List[Dict[str, Any]] = []

    for rule_id, rx, weight in RULES:
        m = rx.search(text or "")
        if m:
            score += weight
            triggers.append({"rule_id": rule_id, "phrase": m.group(0), "evidence": "pattern_match"})

    score = min(1.0, score)
    return {"loop_score": score, "triggers": triggers, "axis": "loop"}
