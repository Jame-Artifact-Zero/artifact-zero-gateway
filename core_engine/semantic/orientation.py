"""Ground/orientation and response-surface detection."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .common import make_signal_envelope


def _sentences(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s and s.strip()]


def _contains_any(text_lc: str, markers: List[str]) -> List[str]:
    return [m for m in markers if m in text_lc]


def detect_ground_fields(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    raw = text or ""
    t = raw.lower()
    fields = {
        "who": _contains_any(f" {t} ", [" i ", " me ", " my ", " we ", " us ", " our ", " you ", " your ", "they", "them", "he", "she", "buyer", "seller", "tenant", "landlord", "client", "customer", "title company", "owner", "manager"]),
        "what": _contains_any(t, ["issue", "question", "contract", "lease", "commission", "bylaws", "sale", "purchase", "approval", "decision", "unit", "building", "project", "email", "call", "meeting", "document"]),
        "when": _contains_any(t, ["today", "tomorrow", "yesterday", "soon", "now", "later", "next week", "this week", "month", "year", "deadline", "am", "pm", "morning", "afternoon", "evening"]),
        "where": _contains_any(t, ["office", "building", "suite", "unit", "address", "site", "location", "property", "room", "jackson oaks", "way", "road", "street", "avenue"]),
        "why": _contains_any(t, ["because", "due to", "reason", "why", "caused", "so that", "in order to", "as a result"]),
        "if": _contains_any(t, ["if", "unless", "provided that", "assuming", "condition", "contingent", "subject to", "depends"]),
        "for": _contains_any(t, ["for", "purpose", "goal", "objective", "in service of", "intended to", "so we can", "so that"]),
    }
    present = [k for k, v in fields.items() if v]
    missing = [k for k, v in fields.items() if not v]
    completeness = len(present) / 7.0
    fired = len(missing) > 0
    return make_signal_envelope("detect_ground_fields", input_type, "GROUND_FIELDS_INCOMPLETE" if fired else "GROUND_FIELDS_PRESENT", round(1.0 - completeness, 3) if fired else 1.0, present, {"fields": fields, "present": present, "missing": missing, "ground_completeness": round(completeness, 3)}, fired)


def detect_ground_shift(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    sents = _sentences(text)
    if not sents:
        return make_signal_envelope("detect_ground_shift", input_type, "NO_TEXT", 0.0, [], {"unit_count": 0}, False)
    first = sents[0].lower()
    later_text = " ".join(sents[1:]).lower()
    topic_groups = {
        "process": ["policy", "procedure", "approval", "review", "calculation", "bylaws"],
        "money": ["commission", "payment", "fee", "cost", "price", "rent", "sale"],
        "relationship": ["tone", "trust", "respect", "professional", "relationship"],
        "reaction": ["concerned", "upset", "rattled", "heard", "felt", "people", "office"],
        "authority": ["owner", "manager", "leadership", "company", "title company"],
        "timing": ["deadline", "soon", "today", "tomorrow", "month", "year"],
        "action": ["advise", "send", "provide", "confirm", "reply", "call"],
    }
    first_topics = [topic for topic, markers in topic_groups.items() if any(m in first for m in markers)]
    later_topics = [topic for topic, markers in topic_groups.items() if any(m in later_text for m in markers)]
    new_topics = [x for x in later_topics if x not in first_topics]
    fired = len(new_topics) >= 2
    return make_signal_envelope("detect_ground_shift", input_type, "GROUND_SHIFT_DETECTED" if fired else "GROUND_STABLE_OR_INSUFFICIENT_SHIFT", round(min(1.0, len(new_topics) / 4.0), 3), new_topics, {"initial_topics": first_topics, "later_topics": later_topics, "new_topics": new_topics, "unit_count": len(sents)}, fired)


def detect_response_surface(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    t = (text or "").lower()
    surfaces = {
        "FACTUAL_RESPONSE": ["what", "which", "where", "when", "how much", "confirm", "provide", "send"],
        "CLARIFICATION_REQUIRED": ["which", "clarify", "can you confirm", "what do you mean", "?"],
        "ACTION_REQUIRED": ["please advise", "send", "provide", "call", "reply", "review", "complete"],
        "DEFENSE_SURFACE": ["you were", "you did", "you failed", "your tone", "your behavior", "unprofessional", "inappropriate"],
        "APOLOGY_SURFACE": ["upset", "concerned", "rattled", "offended", "hurt", "tone"],
        "CONSTRAINT_SURFACE": ["right of first refusal", "policy", "bylaws", "contract", "lease", "subject to", "required", "must", "cannot"],
    }
    detected: Dict[str, List[str]] = {}
    evidence: List[str] = []
    for surface, markers in surfaces.items():
        hits = [m for m in markers if m in t]
        if hits:
            detected[surface] = hits
            evidence.extend(hits)
    if not detected:
        signal = "NO_CLEAR_RESPONSE_SURFACE"
    elif len(detected) == 1:
        signal = list(detected.keys())[0]
    else:
        signal = "MULTIPLE_RESPONSE_SURFACES"
    return make_signal_envelope("detect_response_surface", input_type, signal, round(min(1.0, len(detected) / 4.0), 3), evidence, {"surfaces": detected, "surface_count": len(detected)}, bool(detected))


def detect_orientation(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    ground = detect_ground_fields(text, input_type=input_type)
    shift = detect_ground_shift(text, input_type=input_type)
    surface = detect_response_surface(text, input_type=input_type)
    fired_tools = [r["tool"] for r in [ground, shift, surface] if r.get("fired")]
    evidence: List[str] = []
    for r in [ground, shift, surface]:
        evidence.extend(r.get("evidence", []))
    missing = ground.get("detail", {}).get("missing", [])
    response_surface_count = surface.get("detail", {}).get("surface_count", 0)
    ground_shift = shift.get("fired", False)
    if ground_shift and response_surface_count > 1:
        signal = "ORIENTATION_RISK"; strength = 0.85; fired = True
    elif missing and response_surface_count > 1:
        signal = "INSUFFICIENT_GROUND_MULTIPLE_RESPONSE_SURFACES"; strength = 0.75; fired = True
    elif missing:
        signal = "INSUFFICIENT_GROUND"; strength = 0.55; fired = True
    else:
        signal = "ORIENTATION_STABLE"; strength = 0.2; fired = False
    return make_signal_envelope("detect_orientation", input_type, signal, strength, list(dict.fromkeys(evidence))[:25], {"ground_fields": ground, "ground_shift": shift, "response_surface": surface, "fired_tools": fired_tools}, fired)


