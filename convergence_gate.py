"""
convergence_gate.py
-------------------
I05 — Non-invocation routing enforcement.

This module enforces 'AI not invoked' when the input qualifies for deterministic routing.
Rule-based only. No LLM. Deterministic.

Integrate at your middleware / routing boundary BEFORE any AI call.
"""

from typing import Dict, Any, Tuple
import re


DEFAULT_RULES = [
    # Pure acknowledgements / closures
    ("CLOSE_ACK", re.compile(r"^(got it|done|ok|okay|thanks|thank you)\b", re.I), 1.0),
    ("CLOSE_PROCEED", re.compile(r"^(proceed|go|ship it|send it)\b", re.I), 1.0),
    # File operation directives (should route to deterministic action, not AI)
    ("FILE_ACTION", re.compile(r"\b(zip|bundle|attach|download|export|save as)\b", re.I), 0.9),
    # Simple status update (no need for AI generation)
    ("STATUS_UPDATE", re.compile(r"\b(status|scorecard|complete|usable|skeleton|not delivered)\b", re.I), 0.8),
]


def enforce(payload: Dict[str, Any], trace: Dict[str, Any], enabled: bool = True) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (ai_allowed, response_payload_if_blocked)
    """
    if not enabled:
        trace["convergence_gate"] = {"enabled": False}
        return True, {}

    text = (payload.get("text") or payload.get("input") or payload.get("message") or "").strip()
    if not text:
        trace["convergence_gate"] = {"enabled": True, "decision": "allow", "reason": "empty_text"}
        return True, {}

    hit = _evaluate(text)
    trace["convergence_gate"] = hit

    # If score >= 0.9, block AI invocation
    if hit["score"] >= 0.9:
        return False, {
            "ai_invoked": False,
            "reason": "non_invocation_routing",
            "hit": hit,
            "output": _deterministic_response(text, hit["rule_id"])
        }

    return True, {}


def _evaluate(text: str) -> Dict[str, Any]:
    best = {"rule_id": "NONE", "score": 0.0, "match": ""}
    for rule_id, rx, score in DEFAULT_RULES:
        m = rx.search(text)
        if m and score > best["score"]:
            best = {"rule_id": rule_id, "score": score, "match": m.group(0)}
    return best


def _deterministic_response(text: str, rule_id: str) -> str:
    if rule_id in ("CLOSE_ACK", "CLOSE_PROCEED"):
        return "Acknowledged."
    if rule_id == "FILE_ACTION":
        return "Action recorded: file operation requested."
    if rule_id == "STATUS_UPDATE":
        return "Status recorded."
    return "Routed without AI invocation."
