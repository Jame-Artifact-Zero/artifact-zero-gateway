"""
Artifact Zero Labs — Axis 2 (Conversational Friction) v0.1
Deterministic, rule-based detection + directional enforcement (V2/V3) with progressive modes.

Drop-in module. No external dependencies beyond Python stdlib + Flask (optional for route registration).

Public API:
- analyze_friction(text: str) -> dict
- apply_axis2_v2(text: str, axis2_mode: str) -> dict  # returns {action, template, axis2}
- apply_axis2_v3(ai_text: str, axis2_mode: str) -> dict  # returns {text, transforms, axis2}
- register_axis2_routes(app)  # optional Flask endpoints

Modes:
OFF | OBSERVE | SUGGEST | ASSIST | ENFORCE

Doctrine:
- No motive inference
- No psychological labeling
- No generative rewriting in V3 (delete/replace/compress only)
- Deterministic replay
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Tuple

# -----------------------------
# Configuration (LOCKED v0.1)
# -----------------------------

AXIS2_MODES = {"OFF", "OBSERVE", "SUGGEST", "ASSIST", "ENFORCE"}

BAND_THRESHOLDS = (
    (0.15, "B0"),  # < 0.15
    (0.35, "B1"),  # 0.15–0.34
    (0.65, "B2"),  # 0.35–0.64
    (1.01, "B3"),  # >=0.65
)

HOLD_THRESHOLD = 0.55
STOP_THRESHOLD = 0.75

# V2 deterministic templates (LOCKED)
V2_TEMPLATE_B2 = "State the objective directly."
V2_TEMPLATE_B3 = "I can proceed once the objective is stated directly."
V2_TEMPLATE_ESCALATION_PAUSED = "Execution paused. State objective in one sentence."

# V3 deterministic fallbacks (LOCKED)
V3_FALLBACK_CORRECTED_INFO = "Here is the corrected information."
V3_FALLBACK_CLARIFICATION_REQUIRED = "Clarification is required before proceeding."

# Pattern library (LOCKED v0.1)
# Each entry: (pattern_id, category, weight, compiled_regex)
_PATTERN_SPECS: List[Tuple[str, str, float, str]] = [
    # F1 — Direct Blame Construction
    ("F1-A", "Direct Blame Construction", 0.30, r"\b(you were|you are|you did|you failed|you missed|you forgot|you ignored|you misunderstood)\b"),
    ("F1-B", "Direct Blame Construction", 0.28, r"\b(you should have|you shouldn't have|you were supposed to)\b"),
    ("F1-C", "Direct Blame Construction", 0.27, r"\byou (caused|created|made|broke)\b"),

    # F2 — Retroactive Fault Framing
    ("F2-A", "Retroactive Fault Framing", 0.20, r"\b(earlier|before|previously|last time|again)\b"),

    # F3 — Dominance Posture
    ("F3-A", "Dominance Posture", 0.22, r"\b(obviously|clearly|everyone knows|anyone can see)\b"),
    ("F3-B", "Dominance Posture", 0.24, r"\b(as i said|like i told you|as mentioned)\b"),

    # F4 — Escalation Triggers
    ("F4-A", "Escalation Trigger", 0.25, r"\b(wrong|mistake|ridiculous|unacceptable|careless)\b"),
    ("F4-B", "Escalation Trigger", 0.18, r"\b(always|never)\b"),

    # F5 — Correction Intensifiers
    ("F5-A", "Correction Intensifier", 0.15, r"\b(not about|actually|technically|specifically)\b"),
]

PATTERNS: List[Tuple[str, str, float, re.Pattern]] = [
    (pid, cat, w, re.compile(rx, flags=re.IGNORECASE))
    for pid, cat, w, rx in _PATTERN_SPECS
]

# Interaction modifiers (LOCKED)
CATEGORY_KEYS = {
    "Direct Blame Construction": "F1",
    "Retroactive Fault Framing": "F2",
    "Dominance Posture": "F3",
    "Escalation Trigger": "F4",
    "Correction Intensifier": "F5",
}

INTERACTION_MODIFIERS: List[Tuple[frozenset, float, str]] = [
    (frozenset({"F1", "F4"}), 0.10, "F1+F4"),
    (frozenset({"F1", "F2"}), 0.08, "F1+F2"),
    (frozenset({"F1", "F5"}), 0.07, "F1+F5"),
    (frozenset({"F3", "F4"}), 0.05, "F3+F4"),
    (frozenset({"F1", "F3"}), 0.09, "F1+F3"),
]

# V3 strip phrase lists (deterministic)
V3_STRIP_PHRASES = [
    "obviously", "clearly", "everyone knows", "anyone can see",
    "as i said", "like i told you", "as mentioned",
]

V3_STRIP_EXCLAMATIONS = True


# -----------------------------
# Utilities
# -----------------------------

def _normalize_text(text: str) -> str:
    """Unicode normalize + whitespace normalize. Deterministic."""
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("\u200b", "")  # zero-width space
    t = t.replace("\u00a0", " ")  # NBSP
    t = t.replace("—", "-").replace("–", "-")
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _extract_matches(text: str, rx: re.Pattern) -> List[Tuple[int, int, str]]:
    out: List[Tuple[int, int, str]] = []
    for m in rx.finditer(text):
        out.append((m.start(), m.end(), text[m.start():m.end()]))
    return out


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def _band(score: float) -> str:
    for threshold, band_name in BAND_THRESHOLDS:
        if score < threshold:
            return band_name
    return "B3"


# -----------------------------
# Axis 2 Analyzer
# -----------------------------

def analyze_friction(text: str) -> Dict[str, Any]:
    raw_text = _normalize_text(text)
    if not raw_text:
        return {
            "axis": 2,
            "friction_score": 0.0,
            "band": "B0",
            "hold": False,
            "stop": False,
            "trigger_count": 0,
            "categories_detected": [],
            "interaction_modifiers": [],
            "triggers": [],
            "word_count": 0,
        }

    triggers: List[Dict[str, Any]] = []
    categories_present: set[str] = set()

    for pid, cat, weight, rx in PATTERNS:
        matches = _extract_matches(raw_text, rx)
        if not matches:
            continue
        for start, end, phrase in matches:
            triggers.append({
                "pattern": pid,
                "phrase": phrase,
                "category": cat,
                "weight": weight,
                "span": [start, end],
            })
            categories_present.add(cat)

    # F2 gating: only count retroactive if F1 exists
    has_f1 = any(t["category"] == "Direct Blame Construction" for t in triggers)
    if not has_f1:
        triggers = [t for t in triggers if t["category"] != "Retroactive Fault Framing"]
        categories_present = {t["category"] for t in triggers}

    triggers.sort(key=lambda x: (x["span"][0], x["span"][1], x["pattern"]))

    raw_score = sum(t["weight"] for t in triggers)

    cat_keys_set = {CATEGORY_KEYS.get(c) for c in categories_present if CATEGORY_KEYS.get(c)}
    mods_applied: List[str] = []
    for combo, delta, label in INTERACTION_MODIFIERS:
        if combo.issubset(cat_keys_set):
            raw_score += delta
            mods_applied.append(label)

    score = min(1.0, round(raw_score, 4))
    band_name = _band(score)
    hold = score >= HOLD_THRESHOLD
    stop = score >= STOP_THRESHOLD

    return {
        "axis": 2,
        "friction_score": score,
        "band": band_name,
        "hold": hold,
        "stop": stop,
        "trigger_count": len(triggers),
        "categories_detected": sorted(list(categories_present)),
        "interaction_modifiers": mods_applied,
        "triggers": triggers,
        "word_count": _word_count(raw_text),
    }


# -----------------------------
# V2 Enforcement (Pre-execution)
# -----------------------------

def apply_axis2_v2(text: str, axis2_mode: str, iteration_count: int = 0) -> Dict[str, Any]:
    mode = (axis2_mode or "OBSERVE").upper()
    if mode not in AXIS2_MODES:
        mode = "OBSERVE"

    if mode == "OFF":
        return {"action": "EXECUTE", "template": None, "axis2": analyze_friction("")}

    axis2 = analyze_friction(text)

    if mode in {"OBSERVE", "SUGGEST"}:
        return {"action": "EXECUTE", "template": None, "axis2": axis2}

    if mode == "ASSIST":
        if axis2["band"] == "B3":
            if iteration_count >= 1:
                return {"action": "BLOCK", "template": V2_TEMPLATE_ESCALATION_PAUSED, "axis2": axis2}
            return {"action": "BLOCK", "template": V2_TEMPLATE_B3, "axis2": axis2}
        return {"action": "EXECUTE", "template": None, "axis2": axis2}

    if mode == "ENFORCE":
        if axis2["band"] == "B3":
            if iteration_count >= 1:
                return {"action": "BLOCK", "template": V2_TEMPLATE_ESCALATION_PAUSED, "axis2": axis2}
            return {"action": "BLOCK", "template": V2_TEMPLATE_B3, "axis2": axis2}
        if axis2["band"] == "B2":
            if iteration_count >= 1:
                return {"action": "CLARIFY", "template": V2_TEMPLATE_ESCALATION_PAUSED, "axis2": axis2}
            return {"action": "CLARIFY", "template": V2_TEMPLATE_B2, "axis2": axis2}
        return {"action": "EXECUTE", "template": None, "axis2": axis2}

    return {"action": "EXECUTE", "template": None, "axis2": axis2}


# -----------------------------
# V3 Enforcement (Post-generation)
# -----------------------------

def _strip_phrases(text: str, phrases: List[str]) -> Tuple[str, List[str]]:
    removed: List[str] = []
    out = text
    for p in phrases:
        rx = re.compile(r"\b" + re.escape(p) + r"\b", flags=re.IGNORECASE)
        if rx.search(out):
            removed.append(p)
            out = rx.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out, removed


def _strip_exclamations(text: str) -> Tuple[str, int]:
    if not V3_STRIP_EXCLAMATIONS:
        return text, 0
    count = text.count("!")
    return text.replace("!", ""), count


def _drop_second_person_blame_sentences(text: str) -> Tuple[str, int]:
    parts = re.split(r"(?<=[\.\?\!])\s+", text.strip())
    kept: List[str] = []
    dropped = 0
    blame_rx = re.compile(
        r"\b(you were|you are|you did|you failed|you missed|you forgot|you ignored|you misunderstood|you should have|you shouldn't have|you were supposed to)\b",
        re.IGNORECASE,
    )
    for s in parts:
        if not s:
            continue
        if blame_rx.search(s):
            dropped += 1
            continue
        kept.append(s)
    return " ".join(kept).strip(), dropped


def _compress_first_declarative(text: str) -> str:
    t = text.strip()
    if not t:
        return t
    m = re.search(r"^(.+?[\.!\?])\s", t)
    if m:
        return m.group(1).strip()
    return t.splitlines()[0].strip()


def apply_axis2_v3(ai_text: str, axis2_mode: str) -> Dict[str, Any]:
    mode = (axis2_mode or "OBSERVE").upper()
    if mode not in AXIS2_MODES:
        mode = "OBSERVE"

    raw = _normalize_text(ai_text)

    if mode in {"OFF", "OBSERVE"}:
        return {"text": raw, "transforms": {"applied": False}, "axis2": analyze_friction(raw if mode != "OFF" else "")}

    axis2 = analyze_friction(raw)

    if mode == "SUGGEST":
        return {
            "text": raw,
            "transforms": {"applied": False, "suggestion_available": axis2["band"] in {"B2", "B3"}},
            "axis2": axis2,
        }

    out = raw
    removed_excl = 0
    removed_phrases: List[str] = []
    dropped_sentences = 0
    compressed = False

    if axis2["band"] == "B0":
        return {"text": raw, "transforms": {"applied": False}, "axis2": axis2}

    if axis2["band"] == "B1":
        out, removed_phrases = _strip_phrases(out, V3_STRIP_PHRASES)
        out, removed_excl = _strip_exclamations(out)
        return {
            "text": out,
            "transforms": {
                "applied": (removed_excl > 0 or len(removed_phrases) > 0),
                "removed_phrases": removed_phrases,
                "removed_exclamations": removed_excl,
                "dropped_sentences": 0,
                "compressed": False,
            },
            "axis2": axis2,
        }

    if axis2["band"] == "B2":
        out, removed_phrases = _strip_phrases(out, V3_STRIP_PHRASES)
        out, removed_excl = _strip_exclamations(out)
        out, dropped_sentences = _drop_second_person_blame_sentences(out)
        out2 = _compress_first_declarative(out)
        compressed = (out2 != out)
        out = out2
        axis2_post = analyze_friction(out)
        return {
            "text": out,
            "transforms": {
                "applied": True,
                "removed_phrases": removed_phrases,
                "removed_exclamations": removed_excl,
                "dropped_sentences": dropped_sentences,
                "compressed": compressed,
                "axis2_post": {"friction_score": axis2_post["friction_score"], "band": axis2_post["band"]},
            },
            "axis2": axis2,
        }

    if axis2["band"] == "B3":
        out, removed_phrases = _strip_phrases(out, V3_STRIP_PHRASES)
        out, removed_excl = _strip_exclamations(out)
        out, dropped_sentences = _drop_second_person_blame_sentences(out)
        out = _compress_first_declarative(out)
        compressed = True

        if not out:
            out = V3_FALLBACK_CORRECTED_INFO

        if mode == "ENFORCE":
            axis2_post = analyze_friction(out)
            if axis2_post["band"] == "B3":
                out = V3_FALLBACK_CLARIFICATION_REQUIRED

        axis2_post = analyze_friction(out)
        return {
            "text": out,
            "transforms": {
                "applied": True,
                "removed_phrases": removed_phrases,
                "removed_exclamations": removed_excl,
                "dropped_sentences": dropped_sentences,
                "compressed": compressed,
                "axis2_post": {"friction_score": axis2_post["friction_score"], "band": axis2_post["band"]},
            },
            "axis2": axis2,
        }

    return {"text": raw, "transforms": {"applied": False}, "axis2": axis2}


# -----------------------------
# Optional Flask route
# -----------------------------

def register_axis2_routes(app) -> None:
    """
    Registers POST /nti-friction -> Axis 2 analysis.
    JSON body: {"text": "..."} (also accepts "input" or "content").
    """
    from flask import request, jsonify

    @app.post("/nti-friction")
    def nti_friction_route():
        payload = request.get_json(silent=True) or {}
        text = payload.get("text") or payload.get("input") or payload.get("content") or ""
        return jsonify(analyze_friction(text))


if __name__ == "__main__":
    samples = [
        "Can you resend the file?",
        "I think you missed my point.",
        "You were wrong earlier.",
        "Obviously you don't understand.",
        "Not about routing — about your assumption.",
    ]
    for s in samples:
        a = analyze_friction(s)
        print(s)
        print(a["friction_score"], a["band"], a["categories_detected"], a["interaction_modifiers"])
        print("---")
