
"""Artifact Zero — NTI Signal Detection (Deterministic)

Purpose:
- Emit span-based highlights + summary counts for UI rendering.
- Deterministic only. No inference. No LLM.
- Backend owns detection + span positions. Frontend is renderer.

Public API:
- detect_signals(text: str) -> dict
  returns:
    {
      "catalog_version": "nti-signals-v1",
      "signal_catalog": { SIGNAL: {label, css_class, color, axis, explanation, priority} },
      "signals_summary": { SIGNAL: count },
      "signals_detected": [SIGNAL...],
      "highlights": [ {start,end,signal,css_class,axis,label,pattern} ... ]  # overlap-resolved
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
import re

# -------------------------
# Catalog (backend truth)
# -------------------------

@dataclass(frozen=True)
class SignalSpec:
    label: str
    css_class: str
    color: str
    axis: int
    explanation: str
    priority: int  # higher wins overlaps

CATALOG_VERSION = "nti-signals-v1"

SIGNALS: Dict[str, SignalSpec] = {
    # Axis 1
    "CCA_COLLAPSE": SignalSpec("Constraint Collapse", "collapse", "purple", 1,
        "Different risks are bundled together and minimized into one vague conclusion.", 90),
    "DCE_DEFERRAL": SignalSpec("Deferred Decision", "deferral", "amber", 1,
        "A decision or responsibility is pushed to later without clear ownership.", 80),
    "UDDS_DRIFT": SignalSpec("Objective Drift", "drift", "teal", 1,
        "The objective shifts mid-message without acknowledgment.", 85),

    # Axis 2
    "BLAME_DIRECT": SignalSpec("Direct Blame", "blame", "red", 2,
        "Responsibility is assigned directly to you.", 80),
    "ACCUSATION_SUBJECT_FIRST": SignalSpec("Accusation Framing", "accusation", "red", 2,
        "A sentence begins with “You …” to frame fault.", 70),
    "FAULT_RETROACTIVE": SignalSpec("Retroactive Fault", "retro_fault", "red", 2,
        "Past actions are reframed as mistakes after the fact.", 75),
    "DOMINANCE_ASSERTION": SignalSpec("Dominance Posture", "dominance", "crimson", 2,
        "Authority is asserted through superiority cues.", 70),
    "ESCALATION_ABSOLUTE": SignalSpec("Absolutes", "absolute", "red", 2,
        "Extreme language removes nuance (always/never/everyone/no one).", 70),
    "EGO_CORRECTION": SignalSpec("Ego Correction", "ego", "pink", 2,
        "The speaker positions themselves as correcting you.", 65),

    # Axis 3
    "CONSTRAINT_EXPLICIT": SignalSpec("Explicit Constraint", "constraint", "green", 3,
        "Clear limits or boundaries are defined.", 70),
    "AMBIGUITY_HEDGE": SignalSpec("Hedge", "hedge", "amber", 3,
        "Language softens certainty or avoids commitment.", 60),
    "OWNERSHIP_DECLARED": SignalSpec("Ownership Declared", "ownership", "green", 3,
        "Responsibility is clearly assigned.", 60),
    "PASSIVE_ACTOR_MISSING": SignalSpec("Passive Voice", "passive", "gray", 3,
        "Action is described without naming who did it.", 55),

    # Noise
    "FILLER_VERBAL": SignalSpec("Filler", "filler", "lightgray", 3,
        "Extra words that add no meaning.", 30),
    "INTENSIFIER_REDUNDANT": SignalSpec("Intensifier", "intensifier", "orange", 3,
        "Unnecessary emphasis that inflates tone.", 35),
    "REPETITION_REDUNDANT": SignalSpec("Repetition", "repetition", "lightgray", 3,
        "The same idea is repeated without adding clarity.", 30),

    # Drift & scope
    "OBJECTIVE_SHIFT": SignalSpec("Objective Shift", "shift", "teal", 1,
        "A new issue is introduced mid-stream.", 45),
    "SCOPE_CREEP": SignalSpec("Scope Creep", "scope", "teal", 1,
        "Additional tasks are added without agreement.", 45),
    "TOPIC_DISCONTINUITY": SignalSpec("Topic Jump", "topic_jump", "teal", 1,
        "The topic changes without transition.", 40),

    # Commitment risk
    "COMMITMENT_IMPLIED": SignalSpec("Implied Commitment", "commitment", "amber", 3,
        "A promise is made without defined limits.", 45),
    "COMMITMENT_UNBOUNDED": SignalSpec("Unbounded Commitment", "unbounded", "amber", 3,
        "A commitment lacks timeline or scope.", 50),

    # Amplifiers
    "ABSOLUTE_LANGUAGE": SignalSpec("Absolute Language", "absolute", "red", 2,
        "Language suggests something is permanent or universal.", 60),
    "EMOTIONAL_ESCALATION": SignalSpec("Emotional Escalation", "emotion", "red", 2,
        "Emotionally charged words increase tension.", 55),

    # Logical integrity
    "CONTRADICTION_MARKER": SignalSpec("Contrast Marker", "contrast", "yellow", 3,
        "A contrast is introduced (but/however/although).", 35),
    "CONTRADICTION_UNRESOLVED": SignalSpec("Unresolved Contrast", "contradiction", "purple", 3,
        "A contradiction appears without resolution.", 40),
    "OBJECTIVE_MISSING": SignalSpec("Missing Objective", "missing_objective", "gray", 3,
        "The message lacks a clear goal.", 25),
    "ACTION_MISSING": SignalSpec("Missing Action", "missing_action", "gray", 3,
        "No clear next step is defined.", 25),
    "BOUNDARY_MISSING": SignalSpec("Missing Boundary", "missing_boundary", "gray", 3,
        "Limits are unclear.", 25),

    # Economic / operational
    "TOKEN_BLOAT": SignalSpec("Verbose", "bloat", "lightgray", 3,
        "The message is longer than necessary.", 25),
    "CLARIFICATION_TRIGGER": SignalSpec("Clarification Risk", "clarify", "yellow", 3,
        "Wording will likely require follow-up clarification.", 35),

    # Meta
    "AUTHORITY_ELEVATED": SignalSpec("Authority Signal", "authority", "crimson", 2,
        "Experience or tenure is used to reinforce hierarchy.", 70),
    "SOCIAL_PRESSURE": SignalSpec("Social Pressure", "social", "pink", 2,
        "Social consensus is invoked to influence perception.", 60),
    "URGENCY_PROJECTED": SignalSpec("Urgency", "urgency", "orange", 2,
        "Time pressure is implied.", 55),
    "ASK_INDIRECT": SignalSpec("Indirect Ask", "indirect", "amber", 3,
        "A request is implied but not clearly stated.", 45),
    "CONDITIONAL_AMBIGUITY": SignalSpec("Conditional Ambiguity", "conditional", "amber", 3,
        "An outcome depends on unclear conditions.", 45),

    # Added expansion layer
    "CAUSAL_JUSTIFICATION": SignalSpec("Justification", "causal", "blue", 3,
        "Justification language anchors a decision (because/based on/due to).", 50),
    "PREEMPTIVE_DEFENSE": SignalSpec("Preemptive Defense", "defense", "purple", 2,
        "Defense language appears before accusation is made.", 55),
    "REPUTATION_PROTECTION": SignalSpec("Reputation Protection", "reputation", "crimson", 2,
        "The speaker protects professional standing to legitimize an outcome.", 55),
    "SOCIAL_WITNESS_INVOCATION": SignalSpec("Social Witness", "witness", "pink", 2,
        "Other observers are invoked to reinforce claims.", 55),
    "ROLE_FRAMING": SignalSpec("Role Framing", "role", "blue", 2,
        "The speaker defines their role to legitimize the decision.", 50),
    "RESOLUTION_CLOSURE": SignalSpec("Final Decision", "closure", "purple", 2,
        "Language signals the outcome is final.", 60),
    "MORAL_POSITIONING": SignalSpec("Moral Positioning", "moral", "crimson", 2,
        "The speaker frames themselves as ethically superior.", 55),
    "EMOTION_DISMISSAL": SignalSpec("Emotion Dismissal", "emotion_dismiss", "red", 2,
        "Emotional responses are minimized or invalidated.", 55),
}

# -------------------------
# Pattern libraries
# NOTE: Keep patterns small + explicit. No inference.
# -------------------------

# Generic helper: compile with IGNORECASE | MULTILINE
def _c(rx: str) -> re.Pattern:
    return re.compile(rx, re.IGNORECASE | re.MULTILINE)

# Basic wordlists
HEDGE_PATTERNS = [
    r"\b(might|could|would|may|should)\b",
    r"\b(perhaps|possibly|likely|maybe|occasionally|somewhat)\b",
    r"\b(about|roughly|around|many|some|a few|various)\b",
    r"\b(i think|i believe|it seems|it appears)\b",
]

ABSOLUTE_PATTERNS = [
    r"\b(always|never|everyone|nobody|everything|nothing)\b",
    r"\b(forever|constantly|continuously)\b",
    r"\b(never again|no way|last chance|final|non[- ]negotiable)\b",
]

PASSIVE_PATTERNS = [
    # simple passive: was/were/is/are/been + past participle-ish
    r"\b(it|that|this)\s+(was|were|is|are|been)\s+\w+ed\b",
    r"\b(was|were|is|are|been)\s+\w+ed\b",
    r"\bmistakes were made\b",
    r"\bthe decision was made\b",
]

FILLER_PATTERNS = [
    r"\b(actually|basically|literally|just|really|very|totally|honestly|frankly)\b",
]

INTENSIFIER_PATTERNS = [
    r"\b(absolutely|completely|totally)\s+(required|necessary|done|finished)\b",
]

CONTRAST_PATTERNS = [
    r"\b(but|however|although|though|despite|whereas)\b",
]

COMMITMENT_PATTERNS = [
    r"\b(i'll take care of it|no problem|leave it with me|we've got this)\b",
]



# Meta / structure helpers
CONDITIONAL_PATTERNS = [
    r"\bif\b",
    r"\bprovided that\b",
    r"\bassuming\b",
    r"\bunless\b",
]

URGENCY_PATTERNS = [
    r"\b(asap|urgent|immediately|right away|today|now|by end of day|eod)\b",
    r"\b(last chance|final notice)\b",
]

SOCIAL_PRESSURE_PATTERNS = [
    r"\b(everyone|nobody|many people|the office|throughout the office)\b",
    r"\b(especially the women)\b",
]

AUTHORITY_PATTERNS = [
    r"\b(\d+\+?\s*years)\b",
    r"\b(in my entire (business|real estate)?\s*career)\b",
    r"\b(my experience)\b",
]

DOMINANCE_PATTERNS = [
    r"\b(obviously|clearly|as i said|i already told you)\b",
]

EGO_CORRECTION_PATTERNS = [
    r"\b(that's not the point)\b",
    r"\b(you're missing)\b",
    r"\b(i know you don't understand)\b",
]

RETRO_FAULT_PATTERNS = [
    r"\b(you should have)\b",
    r"\b(earlier you)\b",
    r"\b(last time you)\b",
]

INDIRECT_ASK_PATTERNS = [
    r"\b(my suggestion is)\b",
    r"\b(it would be great if)\b",
    r"\b(maybe you could)\b",
]

EXPLICIT_CONSTRAINT_PATTERNS = [
    r"\b(cannot|can't|must|required|need to|limited to|out of scope|within scope|non[- ]negotiable)\b",
]

OWNERSHIP_PATTERNS = [
    r"\b(i will|we will|assigned to|responsible party|by\s+\w+day|by\s+\d{1,2}/\d{1,2})\b",
]

DIRECTIVE_PATTERNS = [
    r"\b(ask for|relocate|get your things|stay in)\b",
]
# New expansion patterns (requested)
CAUSAL_JUSTIFICATION_PATTERNS = [
    r"\bbecause\b",
    r"\bbased on\b",
    r"\bdue to\b",
    r"\btherefore\b",
    r"\bgiven that\b",
    r"\bas a result\b",
]

PREEMPTIVE_DEFENSE_PATTERNS = [
    r"\bI['’]ve never\b",
    r"\bin my entire\b",
    r"\bregardless of\b",
    r"\bfavorites have nothing to do with\b",
    r"\bnot about\b.*\bbut\b.*",
]

REPUTATION_PROTECTION_PATTERNS = [
    r"\bmy job is\b",
    r"\bmy responsibility\b",
    r"\bmy experience\b",
    r"\bmy standards\b",
    r"\bin my (entire )?(business|real estate)?\s*career\b",
]

SOCIAL_WITNESS_INVOCATION_PATTERNS = [
    r"\bthroughout the office\b",
    r"\bmany people\b",
    r"\beveryone heard\b",
    r"\bespecially the women\b",
    r"\bthe office\b",
]

ROLE_FRAMING_PATTERNS = [
    r"\bas the owner\b",
    r"\bas your broker\b",
    r"\bin my role\b",
    r"\bmy job is\b",
]

RESOLUTION_CLOSURE_PATTERNS = [
    r"\bno way I can consider\b",
    r"\bthere is no way\b",
    r"\bthis is final\b",
    r"\bbest of luck\b",
    r"\bcannot consider\b",
]

MORAL_POSITIONING_PATTERNS = [
    r"\bprofessional\b",
    r"\bconduct\b",
    r"\bstandards\b",
    r"\binappropriate\b",
    r"\bbehavior\b",
]

EMOTION_DISMISSAL_PATTERNS = [
    r"\bemotional\b",
    r"\birrational\b",
    r"\bdramatic\b",
    r"\bsensitive\b",
    r"\boverreacting\b",
]

# Simple blame/accusation/directives patterns (backup if Axis2 friction not used)
BLAME_PATTERNS = [
    r"\byou (were|are|did|didn't|failed to|should have)\b",
    r"\byour fault\b",
]

ACCUSATION_SUBJECT_FIRST_PATTERNS = [
    r"(?m)^\s*you\s+\w+",
]

# Mapping signal -> compiled regex list
_SIGNAL_PATTERNS: Dict[str, List[re.Pattern]] = {
    "AMBIGUITY_HEDGE": [_c(rx) for rx in HEDGE_PATTERNS],
    "ABSOLUTE_LANGUAGE": [_c(rx) for rx in ABSOLUTE_PATTERNS],
    "ESCALATION_ABSOLUTE": [_c(rx) for rx in ABSOLUTE_PATTERNS],
    "PASSIVE_ACTOR_MISSING": [_c(rx) for rx in PASSIVE_PATTERNS],
    "FILLER_VERBAL": [_c(rx) for rx in FILLER_PATTERNS],
    "INTENSIFIER_REDUNDANT": [_c(rx) for rx in INTENSIFIER_PATTERNS],
    "CONTRADICTION_MARKER": [_c(rx) for rx in CONTRAST_PATTERNS],
    "COMMITMENT_IMPLIED": [_c(rx) for rx in COMMITMENT_PATTERNS],
    "BLAME_DIRECT": [_c(rx) for rx in BLAME_PATTERNS],
    "ACCUSATION_SUBJECT_FIRST": [_c(rx) for rx in ACCUSATION_SUBJECT_FIRST_PATTERNS],

    "CAUSAL_JUSTIFICATION": [_c(rx) for rx in CAUSAL_JUSTIFICATION_PATTERNS],
    "PREEMPTIVE_DEFENSE": [_c(rx) for rx in PREEMPTIVE_DEFENSE_PATTERNS],
    "REPUTATION_PROTECTION": [_c(rx) for rx in REPUTATION_PROTECTION_PATTERNS],
    "SOCIAL_WITNESS_INVOCATION": [_c(rx) for rx in SOCIAL_WITNESS_INVOCATION_PATTERNS],
    "ROLE_FRAMING": [_c(rx) for rx in ROLE_FRAMING_PATTERNS],
    "RESOLUTION_CLOSURE": [_c(rx) for rx in RESOLUTION_CLOSURE_PATTERNS],
    "MORAL_POSITIONING": [_c(rx) for rx in MORAL_POSITIONING_PATTERNS],
    "EMOTION_DISMISSAL": [_c(rx) for rx in EMOTION_DISMISSAL_PATTERNS],

    "CONDITIONAL_AMBIGUITY": [_c(rx) for rx in CONDITIONAL_PATTERNS],
    "URGENCY_PROJECTED": [_c(rx) for rx in URGENCY_PATTERNS],
    "SOCIAL_PRESSURE": [_c(rx) for rx in SOCIAL_PRESSURE_PATTERNS],
    "AUTHORITY_ELEVATED": [_c(rx) for rx in AUTHORITY_PATTERNS],
    "DOMINANCE_ASSERTION": [_c(rx) for rx in DOMINANCE_PATTERNS],
    "EGO_CORRECTION": [_c(rx) for rx in EGO_CORRECTION_PATTERNS],
    "FAULT_RETROACTIVE": [_c(rx) for rx in RETRO_FAULT_PATTERNS],
    "ASK_INDIRECT": [_c(rx) for rx in INDIRECT_ASK_PATTERNS],
    "CONSTRAINT_EXPLICIT": [_c(rx) for rx in EXPLICIT_CONSTRAINT_PATTERNS],
    "OWNERSHIP_DECLARED": [_c(rx) for rx in OWNERSHIP_PATTERNS],
}

# Some signals are composite / structural and are supplied by existing engines:
# - CCA_COLLAPSE, DCE_DEFERRAL, UDDS_DRIFT (from parent_failure_modes)
# - DOMINANCE_ASSERTION, EGO_CORRECTION, SOCIAL_PRESSURE, URGENCY_PROJECTED, etc. (from tilt taxonomy)
# We'll map those in integration code (not here).

# -------------------------
# Highlight merging
# -------------------------

def _overlap(a: Tuple[int,int], b: Tuple[int,int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])

def _resolve_overlaps(hls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Sort: earlier start, then higher priority, then longer span
    def key(h):
        spec = SIGNALS.get(h["signal"])
        pr = spec.priority if spec else 0
        ln = (h["end"] - h["start"])
        return (h["start"], -pr, -ln, h["signal"])
    hls_sorted = sorted(hls, key=key)

    kept: List[Dict[str, Any]] = []
    for h in hls_sorted:
        span=(h["start"], h["end"])
        if span[0] >= span[1]:
            continue
        if any(_overlap(span, (k["start"], k["end"])) for k in kept):
            # If overlap exists, only keep if it has higher priority than all overlapping kept
            spec = SIGNALS.get(h["signal"])
            pr = spec.priority if spec else 0
            overlaps=[k for k in kept if _overlap(span, (k["start"], k["end"]))]
            if not overlaps:
                kept.append(h); continue
            max_pr = max(SIGNALS.get(k["signal"], SignalSpec("","", "",0,"",0)).priority for k in overlaps)
            if pr > max_pr:
                kept = [k for k in kept if not _overlap(span, (k["start"], k["end"]))]
                kept.append(h)
        else:
            kept.append(h)

    # final sort by start then end
    kept = sorted(kept, key=lambda x: (x["start"], x["end"]))
    return kept

# -------------------------
# Public API
# -------------------------

def detect_signals(text: str) -> Dict[str, Any]:
    t = text or ""
    highlights: List[Dict[str, Any]] = []
    summary: Dict[str, int] = {}

    # Optional: use Axis2 friction engine if available (span-accurate)
    try:
        from axis2_friction import analyze_friction  # type: ignore
        fx = analyze_friction(t)
        for trig in fx.get("triggers", []) or []:
            cat = (trig.get("category") or "").lower()
            phrase = trig.get("phrase") or ""
            span = trig.get("span") or []
            if not isinstance(span, list) or len(span) != 2:
                continue
            start, end = int(span[0]), int(span[1])
            sig = None
            if "direct blame" in cat:
                sig = "BLAME_DIRECT"
            elif "retroactive fault" in cat:
                sig = "FAULT_RETROACTIVE"
            elif "dominance" in cat:
                sig = "DOMINANCE_ASSERTION"
            elif "escalation" in cat:
                sig = "ESCALATION_ABSOLUTE"
            elif "ego" in cat:
                sig = "EGO_CORRECTION"
            if sig and sig in SIGNALS:
                spec = SIGNALS[sig]
                highlights.append({
                    "start": start,
                    "end": end,
                    "signal": sig,
                    "css_class": spec.css_class,
                    "axis": spec.axis,
                    "label": spec.label,
                    "pattern": trig.get("pattern") or "axis2",
                })
                summary[sig] = summary.get(sig, 0) + 1
    except Exception:
        pass

    for signal, regexes in _SIGNAL_PATTERNS.items():
        spec = SIGNALS.get(signal)
        if not spec:
            continue
        for rx in regexes:
            for m in rx.finditer(t):
                highlights.append({
                    "start": m.start(),
                    "end": m.end(),
                    "signal": signal,
                    "css_class": spec.css_class,
                    "axis": spec.axis,
                    "label": spec.label,
                    "pattern": rx.pattern,
                })
                summary[signal] = summary.get(signal, 0) + 1

    highlights = _resolve_overlaps(highlights)

    # -------------------------
    # Heuristic (non-span) signals
    # -------------------------
    words = [w for w in re.findall(r"\b\w+\b", t)]
    word_count = len(words)
    sent_count = max(1, len(re.split(r"[.!?]+", t)) - 1)
    avg_sent_len = word_count / max(1, sent_count)

    # TOKEN_BLOAT: long messages are likely to create rework
    if word_count >= 180:
        summary["TOKEN_BLOAT"] = max(1, summary.get("TOKEN_BLOAT", 0))
    # CLARIFICATION_TRIGGER: hedge + conditional + missing constraints tends to trigger follow-ups
    if summary.get("AMBIGUITY_HEDGE", 0) >= 2 or summary.get("CONDITIONAL_AMBIGUITY", 0) >= 2:
        summary["CLARIFICATION_TRIGGER"] = max(1, summary.get("CLARIFICATION_TRIGGER", 0))

    # BOUNDARY_MISSING: no explicit constraint words present
    if summary.get("CONSTRAINT_EXPLICIT", 0) == 0:
        summary["BOUNDARY_MISSING"] = max(1, summary.get("BOUNDARY_MISSING", 0))

    # ACTION_MISSING: no action verbs/next-step cues detected
    if not re.search(r"\b(please|can you|next|by\b|i will|we will|schedule|send|share|review|confirm)\b", t, re.IGNORECASE):
        summary["ACTION_MISSING"] = max(1, summary.get("ACTION_MISSING", 0))

    # OBJECTIVE_MISSING: extremely short or no noun phrase; heuristic: no 'to <verb>' goal markers
    if not re.search(r"\b(to\s+\w+|goal|objective|need|want)\b", t, re.IGNORECASE):
        summary["OBJECTIVE_MISSING"] = max(1, summary.get("OBJECTIVE_MISSING", 0))

    # COMMITMENT_UNBOUNDED: commitment signal without any date/time boundary markers
    if summary.get("COMMITMENT_IMPLIED", 0) > 0 and not re.search(r"\b(by\s+\w+day|by\s+\d|tomorrow|today|eod|end of day|this week|next week)\b", t, re.IGNORECASE):
        summary["COMMITMENT_UNBOUNDED"] = max(1, summary.get("COMMITMENT_UNBOUNDED", 0))

    # CONTRADICTION_UNRESOLVED: contrast markers exist but no reconciliation cues
    if summary.get("CONTRADICTION_MARKER", 0) > 0 and not re.search(r"\b(so|therefore|which means|to resolve|the plan is)\b", t, re.IGNORECASE):
        summary["CONTRADICTION_UNRESOLVED"] = max(1, summary.get("CONTRADICTION_UNRESOLVED", 0))

    # REPETITION_REDUNDANT: crude heuristic: repeated bigrams
    tokens=[w.lower() for w in words]
    bigrams=[(tokens[i],tokens[i+1]) for i in range(len(tokens)-1)]
    rep=sum(1 for i in range(1,len(bigrams)) if bigrams[i]==bigrams[i-1])
    if rep >= 2:
        summary["REPETITION_REDUNDANT"] = max(1, summary.get("REPETITION_REDUNDANT", 0))

    detected = sorted(summary.keys())

    # Attach full catalog (backend truth) for UI.
    catalog = {
        k: {
            "label": v.label,
            "css_class": v.css_class,
            "color": v.color,
            "axis": v.axis,
            "explanation": v.explanation,
            "priority": v.priority,
        }
        for k, v in SIGNALS.items()
    }

    return {
        "catalog_version": CATALOG_VERSION,
        "signal_catalog": catalog,
        "signals_summary": summary,
        "signals_detected": detected,
        "highlights": highlights,
    }
