"""Contextual language-function detection."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .common import make_signal_envelope


WORD_RE = re.compile(r"[A-Za-z0-9']+")
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*", re.MULTILINE)

CONTEXTUAL_TRIGGER_SPECS = [
    {"signal": "SOCIAL_PRESSURE_FUNCTION", "trigger_category": "group_reference_candidate", "patterns": [r"\beveryone\b", r"\bnobody\b", r"\bno one\b", r"\bmany people\b", r"\bthe office\b", r"\bthe team\b", r"\bthe family\b", r"\bpeople\b"]},
    {"signal": "HEDGE_FUNCTION", "trigger_category": "uncertainty_candidate", "patterns": [r"\bmaybe\b", r"\bperhaps\b", r"\bpossibly\b", r"\bprobably\b", r"\blikely\b", r"\bi think\b", r"\bi thought\b", r"\bcould\b", r"\bwould\b", r"\bmight\b"]},
    {"signal": "DIRECT_BLAME_FUNCTION", "trigger_category": "direct_blame_candidate", "patterns": [r"\byou were\b", r"\byou are\b", r"\byou did\b", r"\byou failed\b", r"\byou missed\b", r"\byou forgot\b", r"\byou ignored\b", r"\byou misunderstood\b", r"\byou should have\b", r"\byou shouldn't have\b", r"\byou were supposed to\b", r"\byou caused\b", r"\byou created\b", r"\byou made\b", r"\byou broke\b"]},
    {"signal": "RETROACTIVE_FAULT_FUNCTION", "trigger_category": "prior_time_candidate", "patterns": [r"\bbefore\b", r"\bearlier\b", r"\bpreviously\b", r"\blast time\b", r"\bagain\b"]},
    {"signal": "DOMINANCE_POSTURE_FUNCTION", "trigger_category": "certainty_assertion_candidate", "patterns": [r"\bobviously\b", r"\bclearly\b", r"\beveryone knows\b", r"\banyone can see\b", r"\bas i said\b", r"\blike i told you\b", r"\bas mentioned\b"]},
    {"signal": "URGENCY_FUNCTION", "trigger_category": "time_pressure_candidate", "patterns": [r"\bnow\b", r"\btoday\b", r"\bimmediately\b", r"\basap\b", r"\bright away\b", r"\burgent\b"]},
    {"signal": "PASSIVE_VOICE_FUNCTION", "trigger_category": "passive_candidate", "patterns": [r"\b(was|were|is|are|been|being)\s+[A-Za-z0-9']+ed\b"]},
    {"signal": "ABSOLUTE_LANGUAGE_FUNCTION", "trigger_category": "absolute_scope_candidate", "patterns": [r"\balways\b", r"\bnever\b", r"\beveryone\b", r"\bnobody\b", r"\bno one\b", r"\beverything\b", r"\bnothing\b", r"\bcompletely\b", r"\bentirely\b"]},
    {"signal": "CONDITIONAL_AMBIGUITY_FUNCTION", "trigger_category": "conditional_candidate", "patterns": [r"\bif\b", r"\bunless\b", r"\bdepending\b"]},
    {"signal": "JUSTIFICATION_FUNCTION", "trigger_category": "causal_candidate", "patterns": [r"\bbecause\b", r"\bsince\b", r"\bdue to\b", r"\bso that\b"]},
]

FAULT_TERMS = {"wrong", "mistake", "failed", "missed", "forgot", "ignored", "misunderstood", "careless", "unacceptable", "broke", "caused", "problem", "fault"}
CONDUCT_TERMS = {"left", "said", "did", "called", "texted", "answered", "responded", "fixed", "sent", "showed", "missed", "forgot", "ignored", "handled", "talked"}
OBSERVATION_TERMS = {"noticed", "saw", "heard", "talked", "asked", "said", "thinks", "thought", "knows", "watching", "waiting"}
ACTION_DEMAND_TERMS = {"fix", "send", "call", "respond", "reply", "finish", "complete", "do", "handle", "review", "sign", "submit", "decide"}
STATE_ADJECTIVE_EXCLUSIONS = {"bothered", "tired", "concerned", "worried", "upset", "excited", "confused", "interested", "focused", "annoyed"}


def detect_contextual_language_functions(text: str, input_type: str = "unknown", window_size: int = 8) -> Dict[str, Any]:
    raw_text = text or ""
    units = collect_detection_units(raw_text)
    triggers = _find_contextual_triggers(raw_text)
    result_envelopes: List[Dict[str, Any]] = []

    for trigger in triggers:
        context = collect_trigger_context(raw_text, trigger, units, window_size)
        resolved = resolve_function_from_context(context)
        result_envelopes.append(make_signal_envelope(
            tool="resolve_function_from_context",
            input_type=input_type,
            signal=resolved["signal"],
            strength=resolved["strength"],
            evidence=resolved["evidence"],
            detail=resolved["detail"],
            fired=resolved["fired"],
        ))

    fired_results = [r for r in result_envelopes if r.get("fired")]
    evidence: List[str] = []
    for item in fired_results:
        evidence.extend(item.get("evidence", []))
    strength = len(fired_results) / max(len(result_envelopes), 1) if result_envelopes else 0.0

    return make_signal_envelope(
        tool="detect_contextual_language_functions",
        input_type=input_type,
        signal="contextual_language_functions",
        strength=round(strength, 3),
        evidence=evidence,
        detail={"unit_count": len(units), "trigger_count": len(triggers), "resolved_count": len(fired_results), "results": result_envelopes},
        fired=bool(fired_results),
    )


def collect_detection_units(text: str) -> List[Dict[str, Any]]:
    raw_text = text or ""
    units: List[Dict[str, Any]] = []
    for idx, match in enumerate(SENTENCE_RE.finditer(raw_text), start=1):
        unit_text = match.group(0).strip()
        if not unit_text:
            continue
        stripped_offset = len(match.group(0)) - len(match.group(0).lstrip())
        adjusted_start = match.start() + stripped_offset
        units.append({"unit_id": f"u{idx:04d}", "text": unit_text, "start": adjusted_start, "end": adjusted_start + len(unit_text), "tokens": _tokenize_with_offsets(unit_text, adjusted_start)})
    if not units and raw_text.strip():
        stripped = raw_text.strip()
        start = raw_text.find(stripped)
        units.append({"unit_id": "u0001", "text": stripped, "start": start, "end": start + len(stripped), "tokens": _tokenize_with_offsets(stripped, start)})
    return units


def collect_trigger_context(text: str, trigger: Dict[str, Any], units: List[Dict[str, Any]], window_size: int = 8) -> Dict[str, Any]:
    unit = _find_unit_for_span(units, trigger["span"])
    tokens = unit.get("tokens", []) if unit else _tokenize_with_offsets(text or "", 0)
    token_window = _get_token_window(tokens, trigger["span"], window_size)
    phrase = trigger.get("text", "")
    normalized_phrase = phrase.lower().strip()
    return {"trigger": trigger, "unit": unit, "token_window": token_window, "token_window_text": " ".join(t["text"] for t in token_window), "word_type": _basic_word_type(normalized_phrase), "semantic_role": _basic_semantic_role(normalized_phrase), "nearby_terms": [t["text"].lower() for t in token_window]}


def resolve_function_from_context(context: Dict[str, Any]) -> Dict[str, Any]:
    trigger = context.get("trigger", {})
    base_signal = trigger.get("signal", "UNKNOWN_CONTEXTUAL_FUNCTION")
    category = trigger.get("trigger_category", "")
    phrase = trigger.get("text", "")
    unit = context.get("unit") or {}
    unit_text = unit.get("text", "")
    nearby = set(context.get("nearby_terms", []))

    fired = False
    strength = 0.0
    resolved_function = "unresolved_candidate"

    if category == "group_reference_candidate":
        has_recipient = "you" in nearby or "your" in nearby
        has_observation = bool(nearby & OBSERVATION_TERMS)
        has_conduct = bool(nearby & CONDUCT_TERMS)
        if has_recipient and (has_observation or has_conduct):
            fired = True; strength = 1.0; resolved_function = "group_reference_applied_to_recipient_conduct"
        else:
            resolved_function = "group_or_place_reference"
    elif category == "uncertainty_candidate":
        is_request = _looks_like_request(unit_text)
        if phrase.lower() in {"maybe", "perhaps", "possibly", "probably", "likely", "i think", "i thought", "might"}:
            fired = True; strength = 1.0; resolved_function = "uncertainty_or_speaker_cognition_marker"
        elif phrase.lower() in {"could", "would"} and not is_request:
            fired = True; strength = 0.75; resolved_function = "modal_uncertainty_marker"
        else:
            resolved_function = "request_or_modal_grammar"
    elif category == "direct_blame_candidate":
        has_second_person = "you" in nearby or "your" in nearby
        has_fault = bool(nearby & FAULT_TERMS)
        if has_second_person and has_fault:
            fired = True; strength = 1.0; resolved_function = "second_person_fault_assignment"
        elif phrase.lower() in {"you failed", "you missed", "you forgot", "you ignored", "you misunderstood", "you should have", "you shouldn't have", "you caused", "you created", "you broke"}:
            fired = True; strength = 1.0; resolved_function = "second_person_conduct_assignment"
        else:
            resolved_function = "second_person_state_or_description"
    elif category == "prior_time_candidate":
        if bool(nearby & FAULT_TERMS) and ("you" in nearby or "your" in nearby):
            fired = True; strength = 1.0; resolved_function = "prior_time_fault_reference"
        else:
            resolved_function = "time_reference"
    elif category == "certainty_assertion_candidate":
        has_second_person = "you" in nearby or "your" in nearby
        has_fault = bool(nearby & FAULT_TERMS)
        has_correction = bool(nearby & {"actually", "wrong", "mistake", "missed", "failed"})
        if has_second_person and (has_fault or has_correction):
            fired = True; strength = 1.0; resolved_function = "certainty_assertion_applied_to_recipient"
        elif has_correction:
            fired = True; strength = 0.75; resolved_function = "certainty_assertion_with_correction"
        else:
            resolved_function = "certainty_marker"
    elif category == "time_pressure_candidate":
        if bool(nearby & ACTION_DEMAND_TERMS) or phrase.lower() in {"urgent", "asap", "immediately"}:
            fired = True; strength = 1.0; resolved_function = "time_pressure_or_action_demand"
        else:
            resolved_function = "present_time_reference"
    elif category == "passive_candidate":
        last_word = phrase.lower().split()[-1] if phrase else ""
        actor_missing = "by" not in nearby
        if last_word in STATE_ADJECTIVE_EXCLUSIONS:
            resolved_function = "state_adjective_construction"
        elif actor_missing:
            fired = True; strength = 1.0; resolved_function = "passive_actor_missing"
        else:
            fired = True; strength = 0.5; resolved_function = "passive_actor_present"
    elif category == "absolute_scope_candidate":
        phrase_lc = phrase.lower()
        has_second_person = "you" in nearby or "your" in nearby
        has_conduct = bool(nearby & CONDUCT_TERMS)
        if phrase_lc in {"always", "never"} and (has_second_person or has_conduct):
            fired = True; strength = 1.0; resolved_function = "absolute_conduct_scope"
        elif phrase_lc in {"everyone", "nobody", "no one", "everything", "nothing"}:
            fired = True; strength = 0.75; resolved_function = "absolute_quantifier_scope"
        else:
            resolved_function = "scope_marker"
    elif category == "conditional_candidate":
        has_main_clause = _has_text_after_trigger(unit_text, phrase)
        has_vague_modal = bool(nearby & {"maybe", "could", "would", "might", "want"})
        if has_main_clause and has_vague_modal:
            fired = True; strength = 1.0; resolved_function = "conditional_with_vague_resolution"
        elif has_main_clause:
            resolved_function = "clear_condition_structure"
        else:
            fired = True; strength = 0.75; resolved_function = "incomplete_condition_structure"
    elif category == "causal_candidate":
        has_before = _has_text_before_trigger(unit_text, phrase)
        has_after = _has_text_after_trigger(unit_text, phrase)
        fired = True
        strength = 0.75 if has_before and has_after else 0.5
        resolved_function = "causal_explanation_or_justification" if has_before and has_after else "incomplete_causal_marker"

    return {"signal": base_signal, "strength": strength, "evidence": [unit_text or phrase] if fired else [], "fired": fired, "detail": {"trigger": phrase, "trigger_category": category, "resolved_function": resolved_function, "unit_id": unit.get("unit_id"), "unit_text": unit_text, "span": trigger.get("span"), "token_window": context.get("nearby_terms", []), "word_type": context.get("word_type"), "semantic_role": context.get("semantic_role")}}


def _find_contextual_triggers(text: str) -> List[Dict[str, Any]]:
    raw_text = text or ""
    triggers: List[Dict[str, Any]] = []
    for spec in CONTEXTUAL_TRIGGER_SPECS:
        for pattern in spec["patterns"]:
            for match in re.compile(pattern, flags=re.IGNORECASE).finditer(raw_text):
                triggers.append({"signal": spec["signal"], "trigger_category": spec["trigger_category"], "pattern": pattern, "text": raw_text[match.start():match.end()], "span": [match.start(), match.end()]})
    triggers.sort(key=lambda item: (item["span"][0], item["span"][1], item["signal"]))
    return triggers


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _tokenize_with_offsets(text: str, offset: int = 0) -> List[Dict[str, Any]]:
    return [{"text": m.group(0), "lower": m.group(0).lower(), "start": offset + m.start(), "end": offset + m.end()} for m in WORD_RE.finditer(text or "")]


def _find_unit_for_span(units: List[Dict[str, Any]], span: List[int]) -> Optional[Dict[str, Any]]:
    if not span or len(span) != 2:
        return None
    start, end = span
    for unit in units:
        if unit["start"] <= start and end <= unit["end"]:
            return unit
    return None


def _get_token_window(tokens: List[Dict[str, Any]], span: List[int], window_size: int) -> List[Dict[str, Any]]:
    if not tokens or not span or len(span) != 2:
        return []
    start, end = span
    hit_indexes = [idx for idx, token in enumerate(tokens) if not (token["end"] <= start or token["start"] >= end)]
    if not hit_indexes:
        return []
    first = max(0, min(hit_indexes) - window_size)
    last = min(len(tokens), max(hit_indexes) + window_size + 1)
    return tokens[first:last]


def _basic_word_type(phrase: str) -> str:
    p = phrase.lower().strip()
    if p in {"you", "your"} or p.startswith("you "):
        return "second_person_pronoun_phrase"
    if p in {"everyone", "nobody", "no one", "everything", "nothing"}:
        return "indefinite_pronoun"
    if p == "many people":
        return "quantifier_plus_plural_noun"
    if p.startswith("the "):
        return "definite_noun_phrase"
    if p in {"maybe", "perhaps", "possibly", "probably", "likely"}:
        return "uncertainty_adverb"
    if p in {"could", "would", "might"}:
        return "modal_auxiliary"
    if p in {"before", "earlier", "previously", "again", "last time"}:
        return "time_reference_marker"
    if p in {"because", "since", "due to", "so that"}:
        return "causal_marker"
    if p in {"if", "unless", "depending"}:
        return "conditional_marker"
    return "unknown"


def _basic_semantic_role(phrase: str) -> str:
    p = phrase.lower().strip()
    if p in {"everyone", "nobody", "no one", "many people", "people", "the team", "the family"}:
        return "group_or_plural_referent"
    if p == "the office":
        return "place_or_group_reference"
    if p.startswith("you "):
        return "recipient_reference"
    if p in {"maybe", "perhaps", "possibly", "probably", "likely", "i think", "i thought", "could", "would", "might"}:
        return "uncertainty_or_modal_reference"
    if p in {"before", "earlier", "previously", "again", "last time"}:
        return "prior_time_reference"
    if p in {"now", "today", "immediately", "asap", "right away", "urgent"}:
        return "time_pressure_or_time_reference"
    if p in {"because", "since", "due to", "so that"}:
        return "cause_relation"
    if p in {"if", "unless", "depending"}:
        return "condition_relation"
    return "unknown"


def _looks_like_request(unit_text: str) -> bool:
    return bool(re.match(r"^(can|could|would|will|please|do|does|did|are|is)\b", _normalize_space(unit_text).lower()))


def _has_text_before_trigger(unit_text: str, trigger_phrase: str) -> bool:
    idx = (unit_text or "").lower().find((trigger_phrase or "").lower())
    return idx > 0 and bool(unit_text[:idx].strip(" ,;:-"))


def _has_text_after_trigger(unit_text: str, trigger_phrase: str) -> bool:
    idx = (unit_text or "").lower().find((trigger_phrase or "").lower())
    if idx < 0:
        return False
    return bool(unit_text[idx + len(trigger_phrase):].strip(" ,;:-"))


