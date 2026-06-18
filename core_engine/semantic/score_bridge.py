"""Bridge semantic payload signals to existing AZ score/delivery signals.

This module emits bridge findings only. It does not calculate final scores.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import tags


def bridge_semantic_to_score(
    payload_result: Dict[str, Any],
    contextual_result: Optional[Dict[str, Any]] = None,
    structural_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    counts = payload_result.get("detail", {}).get("counts", {}) if payload_result else {}
    statements = payload_result.get("detail", {}).get("statements", []) if payload_result else []
    contextual_items = contextual_result.get("detail", {}).get("results", []) if contextual_result else []

    statement_tags = [s.get("tag") for s in statements]
    contextual_signals = [r.get("signal") for r in contextual_items if r.get("fired")]

    bridges: List[Dict[str, Any]] = []

    if counts.get(tags.JUDGMENT, 0) and any(sig == "HEDGE_FUNCTION" for sig in contextual_signals):
        bridges.append({"signal": "HEDGED_JUDGMENT", "basis": [tags.JUDGMENT, "HEDGE_FUNCTION"]})

    if counts.get(tags.JUDGMENT, 0) and (counts.get(tags.AFFILIATION, 0) or counts.get(tags.CARE_FRAME, 0)):
        bridges.append({"signal": "AFFILIATION_FRAMED_JUDGMENT", "basis": [tags.JUDGMENT, tags.AFFILIATION, tags.CARE_FRAME]})

    for idx in range(len(statement_tags) - 1):
        pair = (statement_tags[idx], statement_tags[idx + 1])
        if pair == (tags.SELF_STATE, tags.JUDGMENT):
            bridges.append({"signal": "SELF_STATE_TO_JUDGMENT", "basis": list(pair), "index": idx})
        elif pair == (tags.JUDGMENT, tags.CONDITION_PROMPT):
            bridges.append({"signal": "CONDITION_PROMPT_AFTER_JUDGMENT", "basis": list(pair), "index": idx})

    return {
        "bridge_count": len(bridges),
        "bridges": bridges,
        "semantic_signal_count": len([x for x in statement_tags if x and x != tags.UNCLASSIFIED]),
        "contextual_signal_count": len(contextual_signals),
    }
