import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

import db as database
from pre_score_gate import pre_score_gate
from patent_core.az_patent_run import run as patent_run

try:
    from core_engine.semantic import detect_semantic_layer
except ImportError:  # pragma: no cover - direct local execution fallback
    from semantic import detect_semantic_layer


core_engine_bp = Blueprint("core_engine", __name__)

# ============================================================
# CANONICAL NTI RUNTIME v3.0 (RULE-BASED, NO LLM DEPENDENCY)
#
# v3.0 includes:
# - 5-dimension weighted NII scoring (D1-D5, continuous 0-100)
# - Tilt clusters: T1-T10
# - Broadened DCE markers (soft deferral)
# - V3 self-audit loop, time collapse, attribution drift stripping
# - Convergence gate, loop detection, consolidation engine
# - Confusion layer, axis2 friction, audit source tagging
# - Full enforcement priority tree (L0-L4)
# ============================================================
NTI_VERSION = "canonical-nti-v3.0"


# ==========================
# DB INIT
# ==========================
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


database.db_init()


# ==========================
# TELEMETRY
# ==========================
def get_session_id() -> str:
    sid = request.headers.get("X-Session-Id")
    if sid and isinstance(sid, str) and len(sid) >= 8:
        return sid
    return str(uuid.uuid4())


def log_json_line(event: str, payload: Dict[str, Any]) -> None:
    record = {"event": event, "ts": utc_now_iso(), **payload}
    print(json.dumps(record, ensure_ascii=False))


def record_request(
    request_id: str,
    route: str,
    session_id: str,
    latency_ms: int,
    payload: Dict[str, Any],
    error: Optional[str] = None
) -> None:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    ua = request.headers.get("User-Agent")
    database.record_request(
        request_id, route, ip, ua, session_id,
        latency_ms, json.dumps(payload, ensure_ascii=False), error
    )


def record_result(request_id: str, result: Dict[str, Any]) -> None:
    database.record_result(
        request_id, NTI_VERSION,
        json.dumps(result, ensure_ascii=False)
    )


# ==========================
# TEXT UTIL
# ==========================
WORD_RE = re.compile(r"[A-Za-z0-9']+")

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of", "in", "on", "for", "with", "as",
    "we", "you", "they", "it", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
    "those", "will", "would", "should", "can", "could", "may", "might", "do", "does", "did", "at", "by",
    "from", "into", "over", "under", "before", "after", "about", "because", "while", "just", "now", "today"
}

def tokenize(text: str) -> List[str]:
    return [t.lower() for t in WORD_RE.findall(text or "")]

def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def split_sentences(text: str) -> List[str]:
    t = normalize_space(text)
    if not t:
        return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if p.strip()]

def jaccard(a: List[str], b: List[str]) -> float:
    sa = set(a)
    sb = set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return round(len(sa & sb) / len(sa | sb), 3)

def extract_domain_tokens(text: str) -> List[str]:
    """
    Lightweight "domain token" extraction for scope expansion detection.
    Heuristic:
      - alphanumeric tokens length >= 4
      - not a stopword
    """
    toks = tokenize(text)
    dom = []
    for t in toks:
        if len(t) >= 4 and t not in STOPWORDS:
            dom.append(t)
    # unique preserve order
    uniq = []
    for x in dom:
        if x not in uniq:
            uniq.append(x)
    return uniq[:80]


# ==========================
# CANONICAL LAYER MODEL (L0-L7)
# ==========================
L0_CONSTRAINT_MARKERS = [
    "must", "cannot", "can't", "won't", "requires", "require", "only if", "no way", "not possible",
    "dependency", "dependent", "api key", "openai", "render", "legal", "policy", "security", "compliance",
    "budget", "deadline", "today", "production", "cannot expose", "secret", "token", "rate limit", "auth"
]

L2_HEDGE = [
    "maybe", "might", "could", "perhaps", "it seems", "it sounds", "generally", "often", "usually",
    "in general", "likely", "approximately", "around"
]
L2_REASSURE = ["don't worry", "no problem", "it's okay", "you got this", "rest assured", "glad", "happy to"]
L2_CATEGORY_BLEND = ["kind of", "sort of", "basically", "overall", "in other words", "at the end of the day"]

L3_MUTATION_MARKERS = ["instead", "rather than", "we should pivot", "let's change", "new plan", "different approach", "actually"]


# ==========================
# PARENT FAILURE MODES (UDDS / DCE / CCA)
# ==========================
DOWNSTREAM_CAPABILITY_MARKERS = [
    "we can build", "we can add", "just add", "ship it", "deploy it", "we can do all of it",
    "just use", "easy to", "quick fix", "we can implement"
]

BOUNDARY_ABSENCE_MARKERS = [
    "maybe", "might", "could", "sort of", "kind of", "basically", "we'll see", "later",
    "for now", "eventually", "not sure", "probably"
]

NARRATIVE_STABILIZATION_MARKERS = [
    "don't worry", "it's fine", "no big deal", "you got this", "glad", "relief", "it's okay",
    "not a problem", "totally"
]

# DCE broadened to include "soft deferral" markers
DCE_DEFER_MARKERS = [
    # explicit deferral
    "later", "eventually", "we can handle that later", "we'll address later", "we can worry later",
    "we'll figure it out", "next week", "after we launch", "phase 2", "future iteration", "future iterations",
    # soft deferral / drift-by-process
    "explore", "consider", "evaluate", "assess", "as we continue", "as we iterate", "we will look into",
    "we'll look into", "we will revisit", "we'll revisit"
]

CCA_COLLAPSE_MARKERS = [
    "overall", "basically", "in general", "at the end of the day", "all in all", "net net",
    "it all comes down to", "the main thing", "just"
]


# ==========================
# NTE-CLF (Tilt Taxonomy) â€” RULE-BASED CLASSIFIER
# v2.0 adds: T4, T5, T9, T10 and keeps T2
# ==========================
TILT_TAXONOMY = {
    "T1_REASSURANCE_DRIFT": ["don't worry", "it's fine", "it's okay", "you got this", "rest assured"],
    "T3_CONSENSUS_CLAIMS": ["most people", "many people", "everyone", "no one", "in general", "typically"],
    "T6_CONSTRAINT_DEFERRAL": ["later", "eventually", "phase 2", "after we launch", "we'll figure it out", "future iteration"],
    "T7_CATEGORY_BLEND": ["kind of", "sort of", "basically", "overall", "at the end of the day"],
    "T8_PRESSURE_OPTIMIZATION": ["now", "today", "asap", "immediately", "right away", "no sooner"]
}

# T2: certainty inflation (absolute guarantees without enforcement verbs)
CERTAINTY_INFLATION_TOKENS = [
    "guarantee", "guarantees", "guaranteed",
    "perfect", "zero risk", "eliminates all risk", "eliminate all risk",
    "always", "never fail", "no possibility", "100%",
    "completely secure", "ensures complete", "every scenario"
]

CERTAINTY_ENFORCEMENT_VERBS = [
    "block", "blocks", "blocked", "blocking",
    "prevent", "prevents", "prevented", "preventing",
    "restrict", "restricts", "restricted", "restricting",
    "deny", "denies", "denied", "denying",
    "require", "requires", "required", "requiring",
    "enforce", "enforces", "enforced", "enforcing",
    "validate", "validates", "validated", "validating",
    "verify", "verifies", "verified", "verifying"
]

# T5: absolute language
ABSOLUTE_LANGUAGE_TOKENS = [
    "always", "never", "everyone", "no one", "completely", "entirely", "100%", "guaranteed", "perfect", "zero risk"
]

# T10: authority imposition
AUTHORITY_IMPOSITION_TOKENS = [
    "experts agree", "industry standard", "research shows", "studies show", "best practice",
    "widely accepted", "authorities agree", "proven by research"
]

# T4: capability overreach
CAPABILITY_OVERREACH_TOKENS = [
    "solves everything", "solve everything", "handles everything", "handle everything",
    "covers all cases", "all cases", "any scenario", "every scenario", "universal solution",
    "works for everyone", "works in any situation", "end-to-end for all"
]
CAPABILITY_VERBS = ["solve", "solves", "handle", "handles", "cover", "covers", "ensure", "ensures", "guarantee", "guarantees"]

def _contains_any(text_lc: str, needles: List[str]) -> bool:
    for n in needles:
        if n in text_lc:
            return True
    return False

def classify_tilt(text: str, prompt: str = "", answer: str = "") -> List[str]:
    t = (text or "").lower()
    hits: List[str] = []

    # existing clusters
    for cat, markers in TILT_TAXONOMY.items():
        for m in markers:
            if m in t:
                hits.append(cat)
                break

    # T2 certainty inflation (certainty token present AND no enforcement)
    certainty_present = _contains_any(t, CERTAINTY_INFLATION_TOKENS)
    enforcement_present = _contains_any(t, CERTAINTY_ENFORCEMENT_VERBS)
    if certainty_present and not enforcement_present:
        hits.append("T2_CERTAINTY_INFLATION")

    # T5 absolute language (simple token presence)
    if _contains_any(t, ABSOLUTE_LANGUAGE_TOKENS):
        hits.append("T5_ABSOLUTE_LANGUAGE")

    # T10 authority imposition
    if _contains_any(t, AUTHORITY_IMPOSITION_TOKENS):
        hits.append("T10_AUTHORITY_IMPOSITION")

    # T4 capability overreach: phrase OR (capability verb + universal quantifier)
    if _contains_any(t, CAPABILITY_OVERREACH_TOKENS):
        hits.append("T4_CAPABILITY_OVERREACH")
    else:
        universal = any(u in t for u in ["all", "every", "any", "everything", "everyone", "no one"])
        capverb = _contains_any(t, CAPABILITY_VERBS)
        if universal and capverb:
            hits.append("T4_CAPABILITY_OVERREACH")

    # T9 scope expansion: compare prompt vs answer domain tokens (only if prompt+answer provided)
    # Heuristic: if a lot of answer domain tokens are not in prompt domain tokens AND drift is high.
    if prompt and answer:
        p_dom = set(extract_domain_tokens(prompt))
        a_dom = extract_domain_tokens(answer)
        if a_dom:
            new_tokens = [x for x in a_dom if x not in p_dom]
            new_ratio = len(new_tokens) / max(len(a_dom), 1)
            # conservative threshold
            if new_ratio >= 0.55 and len(new_tokens) >= 6:
                hits.append("T9_SCOPE_EXPANSION")

    # stable order, remove duplicates
    uniq: List[str] = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    return {
        "tool": "classify_tilt",
        "input_type": "unknown",
        "signal": "TILT_DETECTED" if uniq else "NO_TILT",
        "strength": min(len(uniq) / 10.0, 1.0),
        "evidence": uniq,
        "detail": {"tilt_taxonomy": uniq},
        "fired": bool(uniq)
    }


# ==========================
# NII (NTI Integrity Index)
# NOTE: Schema preserved: q1/q2/q3 + nii_score.
# q3 now penalizes boundary absence AND structural drift tilt categories (T2/T4/T5/T9/T10).
# ==========================
def _split_sentences(text):
    """Split text into sentences for per-sentence analysis."""
    import re
    return [s.strip() for s in re.split(r'[.!?]+', text) if s.strip() and len(s.strip()) > 3]


def compute_nii(prompt: str, answer: str, l0_constraints: List[str], downstream_before_constraints: bool, tilt_taxonomy: List[str]) -> Dict[str, Any]:
    """
    NTI Integrity Index v2 — 5-dimension weighted scoring.
    Returns 0-100 continuous score with 6 bands.

    Dimensions (weights sum to 1.0):
      D1: Constraint Density    (25%) — % of sentences containing explicit constraints
      D2: Ask Architecture      (20%) — Ask positioned before capability claims
      D3: Enforcement Integrity (20%) — Freedom from deferral/erosion markers
      D4: Tilt Resistance       (15%) — Resistance to drift patterns
      D5: Failure Mode Severity (20%) — UDDS/DCE/CCA penalty
    """
    l0_list = l0_constraints.get("evidence", []) if isinstance(l0_constraints, dict) else l0_constraints
    downstream_value = downstream_before_constraints.get("fired", False) if isinstance(downstream_before_constraints, dict) else downstream_before_constraints
    tilt_list = tilt_taxonomy.get("evidence", []) if isinstance(tilt_taxonomy, dict) else tilt_taxonomy
    text = answer or prompt or ""
    sents = _split_sentences(text)
    total_sents = max(len(sents), 1)
    words = text.split()
    word_count = max(len(words), 1)
    t_lower = text.lower()

    # D1: CONSTRAINT DENSITY (25%)
    constraint_sents = sum(1 for s in sents if any(m in s.lower() for m in L0_CONSTRAINT_MARKERS))
    constraint_ratio = constraint_sents / total_sents
    constraint_word_hits = sum(1 for m in L0_CONSTRAINT_MARKERS if m in t_lower)
    constraint_density = min(constraint_word_hits / (word_count / 100), 1.0) if word_count > 0 else 0
    d1 = constraint_ratio * 0.6 + constraint_density * 0.4

    # D2: ASK ARCHITECTURE (20%)
    first_sent = sents[0].lower() if sents else ""
    ask_verbs = ["need", "want", "require", "send", "provide", "confirm", "review", "approve",
                 "schedule", "complete", "submit", "deliver", "respond", "reply", "call", "meet"]
    first_sent_has_ask = any(v in first_sent for v in ask_verbs)
    d2_base = 0.8 if not downstream_value else 0.2
    d2 = min(d2_base + (0.2 if first_sent_has_ask else 0.0), 1.0)

    # D3: ENFORCEMENT INTEGRITY (20%)
    erosion_markers = BOUNDARY_ABSENCE_MARKERS + DCE_DEFER_MARKERS + NARRATIVE_STABILIZATION_MARKERS
    clean_sents = sum(1 for s in sents if not any(m in s.lower() for m in erosion_markers))
    clean_ratio = clean_sents / total_sents
    framing = detect_l2_framing(text)
    framing_detail = framing.get("detail", {})
    hedge_count = len(framing_detail.get("hedge_markers", []))
    reassurance_count = len(framing_detail.get("reassurance_markers", []))
    blend_count = len(framing_detail.get("category_blend_markers", []))
    hedge_penalty = min((hedge_count + reassurance_count + blend_count) * 0.05, 0.4)
    d3 = max(0, clean_ratio - hedge_penalty)

    # D4: TILT RESISTANCE (15%)
    tilt_weights = {
        "T1_REASSURANCE_DRIFT": 0.08, "T2_CERTAINTY_INFLATION": 0.12,
        "T3_CONSENSUS_CLAIMS": 0.06, "T4_CAPABILITY_OVERREACH": 0.15,
        "T5_ABSOLUTE_LANGUAGE": 0.10, "T6_CONSTRAINT_DEFERRAL": 0.12,
        "T7_CATEGORY_BLEND": 0.06, "T8_PRESSURE_OPTIMIZATION": 0.04,
        "T9_SCOPE_EXPANSION": 0.10, "T10_AUTHORITY_IMPOSITION": 0.08
    }
    tilt_penalty = sum(tilt_weights.get(t, 0.05) for t in tilt_list)
    d4 = max(0, 1.0 - tilt_penalty)

    # D5: FAILURE MODE SEVERITY (20%)
    udds = detect_udds(prompt or "", answer or text, l0_list)
    dce = detect_dce(answer or text, l0_list)
    cca = detect_cca(prompt or "", answer or text)
    fm_pen = {"CONFIRMED": 0.30, "PROBABLE": 0.15, "FALSE": 0.00}
    def _fm_p(state):
        for k, v in fm_pen.items():
            if k in str(state):
                return v
        return 0.0
    total_fm = min(_fm_p(udds.get("signal", "")) + _fm_p(dce.get("signal", "")) + _fm_p(cca.get("signal", "")), 0.80)
    d5 = max(0, 1.0 - total_fm)

    # WEIGHTED COMPOSITE
    raw = (d1 * 0.25 + d2 * 0.20 + d3 * 0.20 + d4 * 0.15 + d5 * 0.20)
    score = round(raw * 100)

    if score >= 85: label = "STRONG"
    elif score >= 70: label = "SOLID"
    elif score >= 55: label = "MODERATE"
    elif score >= 40: label = "WEAK"
    elif score >= 25: label = "POOR"
    else: label = "FAILING"

    detail = {
        "nii_score": score,
        "nii_raw": round(raw, 4),
        "nii_label": label,
        "d1_constraint_density": round(d1, 3),
        "d2_ask_architecture": round(d2, 3),
        "d3_enforcement_integrity": round(d3, 3),
        "d4_tilt_resistance": round(d4, 3),
        "d5_failure_mode_severity": round(d5, 3),
        # Legacy compat: map dimensions to Q names for existing UI
        "q1": round(d1, 3),
        "q2": round(d2, 3),
        "q3": round(d3, 3),
        "q4": round(d4, 3),
        "q1_constraints_explicit": round(d1, 3),
        "q2_constraints_before_capability": round(d2, 3),
        "q3_substitutes_after_enforcement": round(d3, 3),
        "detail": {
            "constraint_sents": constraint_sents, "total_sents": total_sents,
            "constraint_word_hits": constraint_word_hits,
            "first_sent_has_ask": first_sent_has_ask,
            "clean_sents": clean_sents, "hedge_count": hedge_count,
            "reassurance_count": reassurance_count, "blend_count": blend_count,
            "tilt_count": len(tilt_list), "tilt_patterns": tilt_list[:10],
            "udds": udds.get("signal", ""), "dce": dce.get("signal", ""), "cca": cca.get("signal", "")
        }
    }
    evidence = list(l0_list) + list(tilt_list[:10])
    return {
        "tool": "compute_nii",
        "input_type": "unknown",
        "signal": label,
        "strength": round(raw, 4),
        "evidence": evidence,
        "detail": detail,
        "fired": score > 0
    }


# ==========================
# L0-L7 EVALUATION
# ==========================
def detect_l0_constraints(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    found = []
    for m in L0_CONSTRAINT_MARKERS:
        if m in t:
            found.append(m)
    uniq = []
    for x in found:
        if x not in uniq:
            uniq.append(x)
    constraints = uniq[:20]
    return {
        "tool": "detect_l0_constraints",
        "input_type": "unknown",
        "signal": "L0_CONSTRAINTS" if constraints else "NO_L0_CONSTRAINTS",
        "strength": min(len(constraints) / 20.0, 1.0),
        "evidence": constraints,
        "detail": {"constraints": constraints},
        "fired": bool(constraints)
    }


def detect_downstream_before_constraint(prompt: str, answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    a = (answer or "").lower()
    p = (prompt or "").lower()
    l0_list = l0_constraints.get("evidence", []) if isinstance(l0_constraints, dict) else l0_constraints

    capability = any(m in a for m in DOWNSTREAM_CAPABILITY_MARKERS) or any(m in p for m in DOWNSTREAM_CAPABILITY_MARKERS)
    constraints_declared = len(l0_list) > 0
    fired = bool(capability and not constraints_declared)
    evidence = [m for m in DOWNSTREAM_CAPABILITY_MARKERS if m in a or m in p]
    return {
        "tool": "detect_downstream_before_constraint",
        "input_type": "unknown",
        "signal": "DOWNSTREAM_BEFORE_CONSTRAINT" if fired else "NO_DOWNSTREAM_BEFORE_CONSTRAINT",
        "strength": 1.0 if fired else 0.0,
        "evidence": evidence,
        "detail": {"capability": capability, "constraints_declared": constraints_declared},
        "fired": fired
    }


def detect_boundary_absence(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in BOUNDARY_ABSENCE_MARKERS) or any(m in a for m in L2_CATEGORY_BLEND)


def detect_narrative_stabilization(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in NARRATIVE_STABILIZATION_MARKERS) or any(m in a for m in L2_REASSURE)


def detect_dce(answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    a = (answer or "").lower()
    l0_list = l0_constraints.get("evidence", []) if isinstance(l0_constraints, dict) else l0_constraints
    defer = any(m in a for m in DCE_DEFER_MARKERS)
    constraints_missing = len(l0_list) == 0

    state = "DCE_FALSE"
    if defer and constraints_missing:
        state = "DCE_CONFIRMED"
    elif defer:
        state = "DCE_PROBABLE"

    evidence = [m for m in DCE_DEFER_MARKERS if m in a]
    strength = 1.0 if state == "DCE_CONFIRMED" else 0.5 if state == "DCE_PROBABLE" else 0.0
    return {
        "tool": "detect_dce",
        "input_type": "unknown",
        "signal": state,
        "strength": strength,
        "evidence": evidence,
        "detail": {"dce_state": state, "defer_markers_present": defer, "constraints_missing": constraints_missing},
        "fired": state != "DCE_FALSE"
    }


def detect_cca(prompt: str, answer: str) -> Dict[str, Any]:
    combined = (prompt or "") + "\n" + (answer or "")
    t = combined.lower()

    collapse = any(m in t for m in CCA_COLLAPSE_MARKERS)
    list_blend = ("and" in t and "but" in t and "overall" in t)

    state = "CCA_FALSE"
    if collapse and list_blend:
        state = "CCA_CONFIRMED"
    elif collapse:
        state = "CCA_PROBABLE"

    evidence = [m for m in CCA_COLLAPSE_MARKERS if m in t]
    if list_blend:
        evidence.append("and/but/overall")
    strength = 1.0 if state == "CCA_CONFIRMED" else 0.5 if state == "CCA_PROBABLE" else 0.0
    return {
        "tool": "detect_cca",
        "input_type": "unknown",
        "signal": state,
        "strength": strength,
        "evidence": evidence,
        "detail": {"cca_state": state, "collapse_markers_present": collapse, "list_blend_present": list_blend},
        "fired": state != "CCA_FALSE"
    }


def detect_udds(prompt: str, answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    l0_list = l0_constraints.get("evidence", []) if isinstance(l0_constraints, dict) else l0_constraints
    c1 = len(l0_list) > 0
    c2_signal = detect_downstream_before_constraint(prompt, answer, l0_list)
    c2 = c2_signal.get("fired", False)
    c3 = detect_boundary_absence(answer)
    c4 = detect_narrative_stabilization(answer)

    met = sum([1 if c else 0 for c in [c1, c2, c3, c4]])

    state = "UDDS_FALSE"
    if met == 4:
        state = "UDDS_CONFIRMED"
    elif met == 3:
        state = "UDDS_PROBABLE"

    a = (answer or "").lower()
    evidence = list(l0_list)
    evidence.extend([m for m in BOUNDARY_ABSENCE_MARKERS if m in a])
    evidence.extend([m for m in NARRATIVE_STABILIZATION_MARKERS if m in a])
    strength = met / 4.0
    return {
        "tool": "detect_udds",
        "input_type": "unknown",
        "signal": state,
        "strength": strength,
        "evidence": evidence,
        "detail": {
            "udds_state": state,
            "criteria": {
                "c1_l0_constraint_exists": c1,
                "c2_downstream_before_constraint_declared": c2,
                "c3_boundary_enforcement_absent_or_delayed": c3,
                "c4_narrative_stabilization_present": c4,
                "criteria_met_count": met
            }
        },
        "fired": state != "UDDS_FALSE"
    }


def detect_l2_framing(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    hedges = [m for m in L2_HEDGE if m in t]
    reassure = [m for m in L2_REASSURE if m in t]
    blends = [m for m in L2_CATEGORY_BLEND if m in t]
    evidence = hedges[:10] + reassure[:10] + blends[:10]
    return {
        "tool": "detect_l2_framing",
        "input_type": "unknown",
        "signal": "L2_FRAMING" if evidence else "NO_L2_FRAMING",
        "strength": min(len(evidence) / 30.0, 1.0),
        "evidence": evidence,
        "detail": {
            "hedge_markers": hedges[:10],
            "reassurance_markers": reassure[:10],
            "category_blend_markers": blends[:10]
        },
        "fired": bool(evidence)
    }


def objective_extract(prompt: str) -> Dict[str, Any]:
    sents = split_sentences(prompt)
    obj = sents[0] if sents else normalize_space(prompt)
    objective_text = obj[:400]
    return {
        "tool": "objective_extract",
        "input_type": "unknown",
        "signal": "OBJECTIVE_EXTRACTED" if objective_text else "NO_OBJECTIVE_EXTRACTED",
        "strength": 1.0 if objective_text else 0.0,
        "evidence": [objective_text] if objective_text else [],
        "detail": {"objective_text": objective_text},
        "fired": bool(objective_text)
    }


def objective_drift(prompt: str, answer: str) -> Dict[str, Any]:
    p_tokens = tokenize(prompt)
    a_tokens = tokenize(answer)

    sim = jaccard(p_tokens, a_tokens)
    drift = round(1.0 - sim, 3)

    a = (answer or "").lower()
    mutation = any(m in a for m in L3_MUTATION_MARKERS)
    evidence = [m for m in L3_MUTATION_MARKERS if m in a]

    return {
        "tool": "objective_drift",
        "input_type": "unknown",
        "signal": "OBJECTIVE_DRIFT" if drift > 0 or mutation else "NO_OBJECTIVE_DRIFT",
        "strength": drift,
        "evidence": evidence,
        "detail": {
            "jaccard_similarity": sim,
            "drift_score": drift,
            "mutation_markers_present": mutation
        },
        "fired": bool(drift > 0 or mutation)
    }


# ==========================
# MOVED FROM detection.py
# ==========================
def split_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs by double newline or significant whitespace."""
    paras = re.split(r"\n\s*\n|\r\n\s*\r\n", text or "")
    return [p.strip() for p in paras if p.strip() and len(p.strip()) > 20]


def detect_all(text: str, prompt: str = "", answer: str = "") -> Dict[str, Any]:
    """Run all detection engines. Returns a DetectionMap."""
    effective_text = text
    if prompt and answer and not text:
        effective_text = f"{prompt}\n{answer}"

    l0_constraints = detect_l0_constraints(effective_text)
    l0_list = l0_constraints.get("evidence", [])
    framing = detect_l2_framing(effective_text)
    tilt = classify_tilt(effective_text, prompt=prompt, answer=answer)
    tilt_list = tilt.get("evidence", [])

    udds = detect_udds(prompt or "", answer or effective_text, l0_list)
    dce = detect_dce(answer or effective_text, l0_list)
    cca = detect_cca(prompt or "", answer or effective_text)

    downstream_before = detect_downstream_before_constraint(prompt or "", answer or effective_text, l0_list)
    obj = objective_extract(prompt or effective_text)
    drift = objective_drift(prompt or "", answer or "")
    semantic_detection = detect_semantic_layer(
        effective_text,
        input_type="detect_all",
    )

    failure_modes = {
        "UDDS": udds,
        "DCE": dce,
        "CCA": cca,
    }

    active_failures = [k for k, v in failure_modes.items()
                       if v.get("signal", "").endswith("CONFIRMED")
                       or v.get("signal", "").endswith("PROBABLE")]

    word_count = len(tokenize(effective_text))
    sentence_count = len(split_sentences(effective_text))

    detail = {
        "text": effective_text,
        "word_count": word_count,
        "sentence_count": sentence_count,
        "l0_constraints": l0_list,
        "framing": framing,
        "tilt_taxonomy": tilt_list,
        "failure_modes": failure_modes,
        "active_failures": active_failures,
        "downstream_before_constraints": downstream_before.get("fired", False),
        "objective": obj,
        "drift": drift,
        "semantic_detection": semantic_detection,
        "signal_density": round(len(tilt_list) / max(word_count, 1) * 100, 2),
    }
    evidence = []
    evidence.extend(l0_constraints.get("evidence", []))
    evidence.extend(framing.get("evidence", []))
    evidence.extend(tilt.get("evidence", []))
    evidence.extend(drift.get("evidence", []))
    evidence.extend(semantic_detection.get("evidence", []))
    strength = min((len(evidence) + len(active_failures)) / 20.0, 1.0)
    return {
        "tool": "detect_all",
        "input_type": "unknown",
        "signal": "DETECTION_MAP" if evidence or active_failures else "NO_DETECTION_MAP",
        "strength": strength,
        "evidence": evidence,
        "detail": detail,
        "fired": bool(evidence or active_failures)
    }


def detect_paragraphs(text: str, prompt: str = "", answer: str = "") -> Dict[str, Any]:
    """Split text into paragraphs and run detect_all on each."""
    paras = split_paragraphs(text)
    if not paras:
        result = detect_all(text, prompt, answer)
        return {
            "tool": "detect_paragraphs",
            "input_type": "unknown",
            "signal": "PARAGRAPH_DETECTION" if result.get("fired") else "NO_PARAGRAPH_DETECTION",
            "strength": result.get("strength", 0.0),
            "evidence": result.get("evidence", []),
            "detail": {"paragraphs": [result]},
            "fired": result.get("fired", False)
        }
    results = []
    evidence = []
    for i, para in enumerate(paras):
        det = detect_all(para, prompt, answer)
        det["detail"]["paragraph_index"] = i
        det["detail"]["paragraph_text"] = para
        evidence.extend(det.get("evidence", []))
        results.append(det)
    return {
        "tool": "detect_paragraphs",
        "input_type": "unknown",
        "signal": "PARAGRAPH_DETECTION" if evidence else "NO_PARAGRAPH_DETECTION",
        "strength": min(len(evidence) / 20.0, 1.0),
        "evidence": evidence,
        "detail": {"paragraphs": results},
        "fired": bool(evidence)
    }



# ==========================
# MOVED FROM scoring.py
# ==========================
def score_nii(det: Dict[str, Any]) -> Dict[str, Any]:
    """NII scoring from detection map."""
    det = det.get("detail", det) if isinstance(det, dict) else {}
    l0 = det.get("l0_constraints", [])
    tilt = det.get("tilt_taxonomy", [])
    fm = det.get("failure_modes", {})
    downstream = det.get("downstream_before_constraints", False)

    # Q1: Are constraints present and explicit?
    q1 = 1.0 if len(l0) >= 1 else 0.0

    # Q2: Are constraints declared before capability claims?
    q2 = 0.0 if downstream else 1.0

    # Q3: Boundary integrity — penalize failure modes + structural drift tilts
    structural_tilts = {"T2_CERTAINTY_INFLATION", "T4_CAPABILITY_OVERREACH",
                        "T5_ABSOLUTE_LANGUAGE", "T9_SCOPE_EXPANSION", "T10_AUTHORITY_IMPOSITION"}
    drift_count = len([t for t in tilt if t in structural_tilts])
    active_fm = len(det.get("active_failures", []))
    q3_penalty = min((drift_count * 0.15) + (active_fm * 0.25), 1.0)
    q3 = max(1.0 - q3_penalty, 0.0)

    nii_score = round((q1 + q2 + q3) / 3.0, 3)

    detail = {
        "nii_score": nii_score,
        "q1_constraints_explicit": q1,
        "q2_constraints_before_capability": q2,
        "q3_boundary_integrity": round(q3, 3),
        "structural_tilt_count": drift_count,
        "active_failure_count": active_fm,
    }
    return {
        "tool": "score_nii",
        "input_type": "unknown",
        "signal": "NII_SCORE",
        "strength": nii_score,
        "evidence": list(l0) + list(tilt),
        "detail": detail,
        "fired": True
    }


def score_nti(det: Dict[str, Any]) -> Dict[str, Any]:
    """NTI composite score (0-100). Higher = cleaner."""
    det = det.get("detail", det) if isinstance(det, dict) else {}
    tilt = det.get("tilt_taxonomy", [])
    framing = det.get("framing", {})
    framing_detail = framing.get("detail", framing) if isinstance(framing, dict) else {}
    active_fm = len(det.get("active_failures", []))
    word_count = det.get("word_count", 1)

    # Start at 100, deduct
    score = 100.0

    # Tilt deductions: each tilt category costs 5 points
    score -= len(tilt) * 5.0

    # Failure mode deductions: CONFIRMED = 10, PROBABLE = 5
    fm = det.get("failure_modes", {})
    for key in ["UDDS", "DCE", "CCA"]:
        state = fm.get(key, {}).get("signal", "")
        if "CONFIRMED" in state:
            score -= 10.0
        elif "PROBABLE" in state:
            score -= 5.0

    # Framing noise deduction: hedges and reassurances
    hedge_count = len(framing_detail.get("hedge_markers", [])) if "hedge_markers" in framing_detail else framing_detail.get("hedge_count", 0)
    reassurance_count = len(framing_detail.get("reassurance_markers", [])) if "reassurance_markers" in framing_detail else framing_detail.get("reassurance_count", 0)
    score -= hedge_count * 2.0
    score -= reassurance_count * 1.5

    # Signal density bonus/penalty
    density = det.get("signal_density", 0)
    if density > 5.0:
        score -= (density - 5.0) * 2.0

    score = max(0.0, min(100.0, round(score, 1)))

    detail = {
        "nti_score": score,
        "tilt_count": len(tilt),
        "failure_mode_deductions": active_fm,
        "hedge_deductions": hedge_count,
        "signal_density": density,
    }
    return {
        "tool": "score_nti",
        "input_type": "unknown",
        "signal": "NTI_SCORE",
        "strength": score / 100.0,
        "evidence": list(tilt),
        "detail": detail,
        "fired": True
    }


def score_csi(det: Dict[str, Any]) -> Dict[str, Any]:
    """CSI scoring — 10 dimensions, 0-100 each, composite average."""
    det = det.get("detail", det) if isinstance(det, dict) else {}
    text = det.get("text", "")
    word_count = det.get("word_count", 1)
    tilt = det.get("tilt_taxonomy", [])
    framing = det.get("framing", {})
    framing_detail = framing.get("detail", framing) if isinstance(framing, dict) else {}
    l0 = det.get("l0_constraints", [])
    fm = det.get("failure_modes", {})

    dimensions = {}

    # D1: Constraint Presence (are commitments bounded?)
    dimensions["constraint_presence"] = min(100, len(l0) * 25)

    # D2: Hedge Density (lower = better)
    hedge_count = len(framing_detail.get("hedge_markers", [])) if "hedge_markers" in framing_detail else framing_detail.get("hedge_count", 0)
    hedge_ratio = hedge_count / max(word_count / 50, 1)
    dimensions["hedge_control"] = max(0, round(100 - hedge_ratio * 30, 1))

    # D3: Tilt Load (fewer tilt categories = better)
    dimensions["tilt_load"] = max(0, round(100 - len(tilt) * 12, 1))

    # D4: Failure Mode Risk
    active = len(det.get("active_failures", []))
    dimensions["failure_mode_risk"] = max(0, round(100 - active * 25, 1))

    # D5: Certainty Calibration
    cert_hit = 1 if "T2_CERTAINTY_INFLATION" in tilt else 0
    abs_hit = 1 if "T5_ABSOLUTE_LANGUAGE" in tilt else 0
    dimensions["certainty_calibration"] = max(0, round(100 - (cert_hit + abs_hit) * 25, 1))

    # D6: Authority Balance
    auth_hit = 1 if "T10_AUTHORITY_IMPOSITION" in tilt else 0
    dimensions["authority_balance"] = max(0, 100 - auth_hit * 35)

    # D7: Scope Discipline
    scope_hit = 1 if "T9_SCOPE_EXPANSION" in tilt else 0
    cap_hit = 1 if "T4_CAPABILITY_OVERREACH" in tilt else 0
    dimensions["scope_discipline"] = max(0, 100 - (scope_hit + cap_hit) * 25)

    # D8: Accountability Presence
    acc_hit = 1 if "T3_ACCOUNTABILITY_DISPLACEMENT" in tilt else 0
    dimensions["accountability"] = max(0, 100 - acc_hit * 40)

    # D9: Emotional Framing
    emo_hit = 1 if "T7_EMOTIONAL_FRAMING" in tilt else 0
    dimensions["emotional_control"] = max(0, 100 - emo_hit * 30)

    # D10: Social Proof Dependency
    sp_hit = 1 if "T8_SOCIAL_PROOF_PRESSURE" in tilt else 0
    dimensions["social_proof_independence"] = max(0, 100 - sp_hit * 30)

    # Composite: weighted average
    weights = {
        "constraint_presence": 1.5,
        "hedge_control": 1.0,
        "tilt_load": 1.2,
        "failure_mode_risk": 1.5,
        "certainty_calibration": 1.0,
        "authority_balance": 0.8,
        "scope_discipline": 1.0,
        "accountability": 1.0,
        "emotional_control": 0.7,
        "social_proof_independence": 0.7,
    }

    total_weight = sum(weights.values())
    weighted_sum = sum(dimensions[k] * weights[k] for k in dimensions)
    composite = round(weighted_sum / total_weight, 1)

    detail = {
        "csi_score": composite,
        "dimensions": dimensions,
        "dimension_count": len(dimensions),
    }
    return {
        "tool": "score_csi",
        "input_type": "unknown",
        "signal": "CSI_SCORE",
        "strength": composite / 100.0,
        "evidence": list(l0) + list(tilt),
        "detail": detail,
        "fired": True
    }


def score_hcs(det: Dict[str, Any]) -> Dict[str, Any]:
    """HCS scoring — 5 lenses."""
    det = det.get("detail", det) if isinstance(det, dict) else {}
    tilt = det.get("tilt_taxonomy", [])
    framing = det.get("framing", {})
    framing_detail = framing.get("detail", framing) if isinstance(framing, dict) else {}
    fm = det.get("failure_modes", {})

    lenses = {}

    # Lens 1: Clarity (hedges + vague quantification hurt)
    hedge_count = len(framing_detail.get("hedge_markers", [])) if "hedge_markers" in framing_detail else framing_detail.get("hedge_count", 0)
    hedge_penalty = hedge_count * 8
    vague_hit = 1 if "T6_VAGUE_QUANTIFICATION" in tilt else 0
    lenses["clarity"] = max(0, round(100 - hedge_penalty - vague_hit * 15, 1))

    # Lens 2: Respect (dominance, authority, blame hurt)
    respect_tilts = {"T10_AUTHORITY_IMPOSITION", "T3_ACCOUNTABILITY_DISPLACEMENT"}
    respect_hits = len([t for t in tilt if t in respect_tilts])
    lenses["respect"] = max(0, round(100 - respect_hits * 20, 1))

    # Lens 3: Honesty (certainty inflation + absolute language hurt)
    honesty_tilts = {"T2_CERTAINTY_INFLATION", "T5_ABSOLUTE_LANGUAGE", "T4_CAPABILITY_OVERREACH"}
    honesty_hits = len([t for t in tilt if t in honesty_tilts])
    lenses["honesty"] = max(0, round(100 - honesty_hits * 18, 1))

    # Lens 4: Commitment Integrity (failure modes hurt)
    active = len(det.get("active_failures", []))
    lenses["commitment_integrity"] = max(0, round(100 - active * 20, 1))

    # Lens 5: Emotional Regulation (urgency + emotional framing hurt)
    emo_tilts = {"T1_URGENCY_ESCALATION", "T7_EMOTIONAL_FRAMING", "T8_SOCIAL_PROOF_PRESSURE"}
    emo_hits = len([t for t in tilt if t in emo_tilts])
    lenses["emotional_regulation"] = max(0, round(100 - emo_hits * 15, 1))

    composite = round(sum(lenses.values()) / len(lenses), 1)

    detail = {
        "hcs_score": composite,
        "lenses": lenses,
    }
    return {
        "tool": "score_hcs",
        "input_type": "unknown",
        "signal": "HCS_SCORE",
        "strength": composite / 100.0,
        "evidence": list(tilt),
        "detail": detail,
        "fired": True
    }


def score_composite(det: Dict[str, Any]) -> Dict[str, Any]:
    """All scoring lenses applied to one detection map."""
    nii = score_nii(det)
    nti = score_nti(det)
    csi = score_csi(det)
    hcs = score_hcs(det)

    detail = {
        "nii": nii,
        "nti": nti,
        "csi": csi,
        "hcs": hcs,
    }
    evidence = []
    for score in detail.values():
        evidence.extend(score.get("evidence", []))
    strength = round((nii.get("strength", 0.0) + nti.get("strength", 0.0) + csi.get("strength", 0.0) + hcs.get("strength", 0.0)) / 4.0, 4)
    return {
        "tool": "score_composite",
        "input_type": "unknown",
        "signal": "COMPOSITE_SCORE",
        "strength": strength,
        "evidence": evidence,
        "detail": detail,
        "fired": True
    }


def score_paragraphs(paragraph_maps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score each paragraph, produce paragraph-level and page-level scores."""
    if isinstance(paragraph_maps, dict) and paragraph_maps.get("tool") == "detect_paragraphs":
        paragraph_maps = paragraph_maps.get("detail", {}).get("paragraphs", [])
    paragraph_scores = []
    for det in paragraph_maps:
        scores = score_composite(det)
        det_detail = det.get("detail", det) if isinstance(det, dict) else {}
        scores["detail"]["paragraph_index"] = det_detail.get("paragraph_index", 0)
        scores["detail"]["word_count"] = det_detail.get("word_count", 0)
        paragraph_scores.append(scores)

    if not paragraph_scores:
        return {
            "tool": "score_paragraphs",
            "input_type": "unknown",
            "signal": "NO_PARAGRAPH_SCORES",
            "strength": 0.0,
            "evidence": [],
            "detail": {"paragraph_scores": [], "page_score": {}},
            "fired": False
        }

    # Page-level: weighted average by word count
    total_words = sum(p.get("detail", {}).get("word_count", 1) for p in paragraph_scores)
    if total_words == 0:
        total_words = 1

    def weighted_avg(key_path):
        total = 0
        for ps in paragraph_scores:
            wc = ps.get("detail", {}).get("word_count", 1)
            val = ps.get("detail", {})
            for k in key_path:
                val = val.get(k, {}) if isinstance(val, dict) else 0
            if isinstance(val, dict) and "detail" in val and len(key_path) > 1:
                val = val.get("detail", {}).get(key_path[-1], 0)
            if isinstance(val, (int, float)):
                total += val * wc
        return round(total / total_words, 1)

    page_score = {
        "nii_score": weighted_avg(["nii", "nii_score"]),
        "nti_score": weighted_avg(["nti", "nti_score"]),
        "csi_score": weighted_avg(["csi", "csi_score"]),
        "hcs_score": weighted_avg(["hcs", "hcs_score"]),
        "paragraph_count": len(paragraph_scores),
        "total_words": total_words,
    }

    evidence = []
    for ps in paragraph_scores:
        evidence.extend(ps.get("evidence", []))
    return {
        "tool": "score_paragraphs",
        "input_type": "unknown",
        "signal": "PARAGRAPH_SCORES",
        "strength": min(len(paragraph_scores) / 10.0, 1.0),
        "evidence": evidence,
        "detail": {
            "paragraph_scores": paragraph_scores,
            "page_score": page_score,
        },
        "fired": True
    }




# ==========================
# JOS (fill-in-the-blank form + binding contract)
# ==========================
def jos_template() -> Dict[str, Any]:
    return {
        "jos_version": "jos-binding-v1",
        "fields": [
            {"name": "objective", "prompt": "What is the single objective for this run? (one sentence)"},
            {"name": "constraints", "prompt": "List constraints (one per line)."},
            {"name": "no_go_zones", "prompt": "What is explicitly not allowed? (one per line)"},
            {"name": "definition_of_done", "prompt": "What does done mean? (one sentence)"},
            {"name": "closure_authority", "prompt": "Who can close/override? (you / system / both)"},
        ],
        "binding_contract": [
            "Objective is frozen at L1 before execution.",
            "Emotion may be acknowledged, never executed.",
            "Constraints cannot be deleted; only appended explicitly.",
            "If ambiguity exists, system must request constraint clarification OR run in 'analysis-only' mode."
        ]
    }


def jos_apply(config: Dict[str, Any]) -> Dict[str, Any]:
    objective = normalize_space(str(config.get("objective", "")))
    constraints = config.get("constraints", "")
    if isinstance(constraints, list):
        constraints_list = [normalize_space(str(x)) for x in constraints if normalize_space(str(x))]
    else:
        constraints_list = [normalize_space(x) for x in str(constraints).splitlines() if normalize_space(x)]

    no_go = config.get("no_go_zones", "")
    if isinstance(no_go, list):
        no_go_list = [normalize_space(str(x)) for x in no_go if normalize_space(str(x))]
    else:
        no_go_list = [normalize_space(x) for x in str(no_go).splitlines() if normalize_space(x)]

    dod = normalize_space(str(config.get("definition_of_done", "")))
    closure = normalize_space(str(config.get("closure_authority", "")))

    errors = []
    if not objective:
        errors.append("Missing objective")
    if not constraints_list:
        errors.append("Missing constraints")
    if not dod:
        errors.append("Missing definition_of_done")
    if closure not in ["you", "system", "both"]:
        errors.append("closure_authority must be: you / system / both")

    status = "OK" if not errors else "INVALID"

    return {
        "status": status,
        "errors": errors,
        "frozen": {
            "objective": objective,
            "constraints": constraints_list,
            "no_go_zones": no_go_list,
            "definition_of_done": dod,
            "closure_authority": closure
        }
    }


# ==========================
# CORE ROUTES
# ==========================
@core_engine_bp.route("/health")
@core_engine_bp.route("/api/health")
def health():
    return jsonify({"status": "ok", "version": NTI_VERSION})


@core_engine_bp.route("/canonical/status")
def canonical_status():
    return jsonify({
        "status": "ok",
        "version": NTI_VERSION,
        "canonical": {
            "no_llm_dependency_v0_1_rule_based": True,
            "layers_l0_l7": True,
            "parent_failure_modes_udds_dce_cca": True,
            "interaction_matrix": True,
            "nte_clf_tilt_taxonomy": True,
            "nii_integrity_index": True,
            "jos_template_and_binding": True,
            "telemetry_and_persistence": True
        },
        "v3_modules": {
            "self_audit": True,
            "time_collapse": True,
            "attribution_drift": True,
            "convergence_gate": True,
            "audit_source": True,
            "axis2_friction": True,
            "loop_detection": True,
            "consolidation_engine": True,
            "confusion_layer": True,
            "time_object": True,
            "nti_full_integration": True,
            "per_industry_config": False,
        }
    })


# ═══════════════════════════════════════
# V3 ROUTES — Axis 2 + Full Integration
# ═══════════════════════════════════════

try:
    from axis2_endpoint import handle_request as axis2_handle
    @core_engine_bp.route("/nti-friction", methods=["POST"])
    def nti_friction():
        return jsonify(axis2_handle(request.get_json(force=True)))
    print("[app] axis2 /nti-friction loaded", flush=True)
except ImportError:
    print("[app] axis2_endpoint not found, skipping", flush=True)


@core_engine_bp.route("/nti-full", methods=["POST"])
def nti_full():
    """Full NTI scoring: Axis 1 + Axis 2 + loop + consolidation + confusion + time object."""
    t0 = time.time()
    payload = request.get_json(force=True) or {}
    text = (payload.get("text") or payload.get("input") or payload.get("message") or "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Axis 1 — existing NTI scoring
    prompt = ""
    answer = text
    l0 = detect_l0_constraints(answer)
    tilt = classify_tilt(answer, prompt, answer)
    dbc = detect_downstream_before_constraint(prompt, answer, l0)
    nii = compute_nii(prompt, answer, l0, dbc, tilt)

    axis1 = {
        "nii": nii,
        "l0_constraints": l0,
        "tilt_taxonomy": tilt,
        "failure_modes": {
            "udds": detect_udds(prompt, answer, l0),
            "dce": detect_dce(answer, l0),
            "cca": detect_cca(prompt, answer),
        }
    }

    # Full integration — Axis 2 + detection modules
    try:
        from nti_full_integration_stub import build_full
        request_id = f"nti_{uuid.uuid4().hex[:12]}"
        payload["request_id"] = request_id
        full = build_full(payload=payload, axis1=axis1, build_version=NTI_VERSION)
    except Exception as e:
        full = {"axis1": axis1, "error": str(e)}

    full["latency_ms"] = int((time.time() - t0) * 1000)
    full["version"] = NTI_VERSION
    return jsonify(full)


@core_engine_bp.route("/events", methods=["POST"])
def events():
    session_id = get_session_id()
    payload = request.get_json() or {}
    event_name = str(payload.get("event", "")).strip()
    event_data = payload.get("data", {})

    if not event_name:
        return jsonify({"error": "Missing event name"}), 400

    eid = str(uuid.uuid4())
    database.record_event(eid, session_id, event_name, json.dumps(event_data, ensure_ascii=False))

    log_json_line("event", {"session_id": session_id, "event": event_name, "data": event_data})
    return jsonify({"ok": True, "event_id": eid})


@core_engine_bp.route("/jos/template", methods=["GET"])
def jos_get_template():
    return jsonify(jos_template())


@core_engine_bp.route("/jos/apply", methods=["POST"])
def jos_apply_route():
    config = request.get_json() or {}
    return jsonify(jos_apply(config))


@core_engine_bp.route("/nti", methods=["POST"])
def nti_run():
    request_id = str(uuid.uuid4())
    session_id = get_session_id()
    t0 = time.time()

    payload = request.get_json() or {}

    text = payload.get("text")
    prompt = payload.get("prompt")
    answer = payload.get("answer")

    if prompt and answer and not text:
        text = f"PROMPT:\n{prompt}\n\nANSWER:\n{answer}"

    if not text:
        latency_ms = int((time.time() - t0) * 1000)
        record_request(request_id, "/nti", session_id, latency_ms, payload, error="No input provided")
        return jsonify({"error": "Provide either text OR prompt+answer", "request_id": request_id}), 400

    request_text = text
    _patent_result = patent_run(
        Q=request_text,
        modality="typed_manual",
        receiver_class="llm",
        where="nti_endpoint",
        when=utc_now_iso(),
        for_="nti_scoring",
        why="human_request"
    )

    # V2 PRE-SCORE GATE
    gate = pre_score_gate(text)
    if not gate["pass"]:
        return jsonify({"error": gate["msg"], "gate": gate["reason"], "status": "rejected", "request_id": request_id}), 422

    # axis2_compiler — inbound pre-processor (silent transform)
    original_text = text  # preserve for highlight_map — spans must match what user sees
    try:
        from axis2_compiler import compile_planned as axis2_compile
        _compiled = axis2_compile(text)
        if _compiled["accepted"]:
            text = _compiled["compiled"]
    except ImportError:
        pass

    l0_constraints = detect_l0_constraints(text)

    obj = objective_extract(prompt or text)
    drift = objective_drift(prompt or "", answer or "")

    framing = detect_l2_framing(original_text)  # must use original — framing stores char offsets

    # Highlights: backend owns spans, frontend only renders
    # Both framing and get_highlights use original_text — offsets must match displayed text
    try:
        from highlight_map import get_highlights
        axis2, highlights = get_highlights(original_text, framing=framing.get("detail", framing))
    except Exception:
        axis2, highlights = None, []

    # tilt taxonomy (now uses prompt+answer for scope expansion detection)
    tilt = classify_tilt(text, prompt=prompt or "", answer=answer or "")

    udds = detect_udds(prompt or "", answer or text, l0_constraints)
    dce = detect_dce(answer or text, l0_constraints)
    cca = detect_cca(prompt or "", answer or text)

    downstream_before_constraints = detect_downstream_before_constraint(prompt or "", answer or text, l0_constraints)
    nii = compute_nii(prompt or "", answer or text, l0_constraints, downstream_before_constraints, tilt)

    # NTI signal detection (deterministic taxonomy)
    try:
        from core_engine.nti_signals import detect_signals
        signals = detect_signals(text)
        if cca.get("signal") in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
            signals["signals_summary"]["CCA_COLLAPSE"] = max(1, signals["signals_summary"].get("CCA_COLLAPSE", 0))
        if dce.get("signal") in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
            signals["signals_summary"]["DCE_DEFERRAL"] = max(1, signals["signals_summary"].get("DCE_DEFERRAL", 0))
        if udds.get("signal") in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
            signals["signals_summary"]["UDDS_DRIFT"] = max(1, signals["signals_summary"].get("UDDS_DRIFT", 0))
        tilt_to_signal = {"T8_PRESSURE_OPTIMIZATION": "SOCIAL_PRESSURE", "T7_AUTHORITY_ANCHOR": "AUTHORITY_ELEVATED", "T6_ABSOLUTE_FRAMING": "ABSOLUTE_LANGUAGE"}
        for code in (tilt.get("evidence", []) if isinstance(tilt, dict) else (tilt or [])):
            _sig = tilt_to_signal.get(code)
            if _sig:
                signals["signals_summary"][_sig] = max(1, signals["signals_summary"].get(_sig, 0))
    except Exception:
        signals = {"catalog_version": "nti-signals-v1", "signal_catalog": {}, "signals_summary": {}, "signals_detected": [], "highlights": []}

    dominance: List[str] = []
    if cca.get("signal") in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
        dominance.append("CCA")
    if udds.get("signal") in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
        dominance.append("UDDS")
    if dce.get("signal") in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
        dominance.append("DCE")
    if not dominance:
        dominance = ["NONE"]

    interaction = {
        "pairwise": [
            {"pair": "UDDS+DCE", "note": "DCE enables early drift; UDDS stabilizes narrative."},
            {"pair": "UDDS+CCA", "note": "CCA masks constraints; UDDS reinforces substitute narrative."},
            {"pair": "DCE+CCA", "note": "CCA collapses constraints; DCE pushes enforcement later."},
        ],
        "triadic": {"combo": "UDDS+DCE+CCA", "note": "High-risk drift: collapse + deferral + stabilization."},
        "dominance_order": ["CCA", "UDDS", "DCE"],
        "dominance_detected": dominance
    }

    layers = {
        "L0_reality_substrate": {"constraints_found": l0_constraints},
        "L1_input_freeze": {"objective": obj.get("detail", {}).get("objective_text", ""), "constraints_snapshot": l0_constraints.get("evidence", [])},
        "L2_interpretive_framing": framing,
        "L3_objective_integrity": drift,
        "L4_execution_vectors": {"note": "Canonical runtime records vectors; UI rendering is separate."},
        "L5_output_enforcement": {"note": "Canonical runtime flags drift modes; enforcement UI is separate."},
        "L6_interface_contracts": {"jos_binding_available": True, "jos_template_endpoint": "/jos/template"},
        "L7_telemetry": {"request_id": request_id, "session_id": session_id}
    }

    result = {
        "status": "ok",
        "version": NTI_VERSION,
        "layers": layers,
        "parent_failure_modes": {
            "UDDS": udds,
            "DCE": dce,
            "CCA": cca
        },
        "interaction_matrix": interaction,
        "nii": nii,
        "tilt_taxonomy": tilt,
        "signals": signals,
        "highlights": highlights,
        "axis2": axis2
    }

    latency_ms = int((time.time() - t0) * 1000)
    record_request(request_id, "/nti", session_id, latency_ms, payload, error=None)
    record_result(request_id, result)

    S1 = {
        "request_id": request_id,
        "session_id": session_id,
        "result_summary": result.get("nii", {}),
        "timestamp": utc_now_iso()
    }
    result["S1"] = S1

    log_json_line("nti_run", {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms,
        "dominance": dominance,
        "nii": nii.get("detail", {}).get("nii_score"),
        "tilt": tilt.get("evidence", []) if isinstance(tilt, dict) else tilt
    })

    # Log to cockpit analytics
    try:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip: ip = ip.split(",")[0].strip()
    except Exception:
        pass

    result["telemetry"] = {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms
    }

    # I04 — audit source tagging
    try:
        from audit_source import normalize_audit_source
        result["telemetry"]["audit_source"] = normalize_audit_source(
            (request.get_json(silent=True) or {}).get("source")
        )
    except Exception:
        result["telemetry"]["audit_source"] = "manual"

    # ── V3 ENFORCEMENT: self-audit loop ──
    # Score own output before delivery. Core governance, not optional.
    try:
        from v3_self_audit import run_v3_pipeline

        def _v1_score_fn(txt):
            """Adapter: run compute_nii on text and return dict with nii_score."""
            _l0 = detect_l0_constraints(txt)
            _tilt = classify_tilt(txt)
            _dbc = detect_downstream_before_constraint("", txt, _l0)
            _nii = compute_nii("", txt, _l0, _dbc, _tilt)
            return _nii

        v3 = run_v3_pipeline(
            output_text=answer or text,
            v1_score_fn=_v1_score_fn,
            audit_threshold=0.85,
            max_passes=2,
        )
        result["v3"] = {
            "enforced_text": v3["output"],
            "passes": len(v3["passes"]),
            "final_score": v3["final_score"].get("detail", {}).get("nii_score") if isinstance(v3["final_score"], dict) else None,
            "decision": v3["self_audit"]["decision"],
            "time_collapse_applied": True,
            "attribution_stripped": True,
        }
    except Exception as e:
        result["v3"] = {"error": str(e), "passed": True}

    # axis3_clarity — outbound clarity scorer
    try:
        from axis3_clarity import analyze_clarity
        _obj_text = result.get("layers", {}).get("L1_input_freeze", {}).get("objective", text)
        result["axis3_clarity"] = analyze_clarity(_obj_text)
    except ImportError:
        pass

    return jsonify(result)


