"""Semantic payload sequence analysis."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Sequence

from . import tags

IMPORTANT_TRANSITIONS = {
    (tags.EXPECTATION, tags.JUDGMENT): "EXPECTATION_TO_JUDGMENT",
    (tags.JUDGMENT, tags.AFFILIATION): "JUDGMENT_TO_AFFILIATION",
    (tags.JUDGMENT, tags.CONDITION_PROMPT): "JUDGMENT_TO_CONDITION_PROMPT",
    (tags.SELF_STATE, tags.JUDGMENT): "SELF_STATE_TO_JUDGMENT",
    (tags.DISSATISFACTION, tags.INVITATION): "DISSATISFACTION_TO_INVITATION",
}


def _statement_tags(statements: Sequence[Dict[str, Any]]) -> List[str]:
    return [str(s.get("tag", tags.UNCLASSIFIED)) for s in statements if s.get("tag")]


def analyze_semantic_patterns(statements: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    seq = _statement_tags(statements)
    transitions: Counter[str] = Counter()
    flagged: List[Dict[str, Any]] = []

    for idx in range(len(seq) - 1):
        pair = (seq[idx], seq[idx + 1])
        key = f"{pair[0]}->{pair[1]}"
        transitions[key] += 1
        if pair in IMPORTANT_TRANSITIONS:
            flagged.append({
                "index": idx,
                "transition": IMPORTANT_TRANSITIONS[pair],
                "from": pair[0],
                "to": pair[1],
            })

    runs: List[Dict[str, Any]] = []
    if seq:
        start = 0
        current = seq[0]
        for idx, tag in enumerate(seq[1:], start=1):
            if tag != current:
                runs.append({"tag": current, "start": start, "end": idx - 1, "length": idx - start})
                start = idx
                current = tag
        runs.append({"tag": current, "start": start, "end": len(seq) - 1, "length": len(seq) - start})

    dominant_sequence = None
    if transitions:
        dominant_sequence = transitions.most_common(1)[0][0]

    return {
        "transitions": dict(transitions),
        "flagged_transitions": flagged,
        "runs": runs,
        "dominant_sequence": dominant_sequence,
    }
