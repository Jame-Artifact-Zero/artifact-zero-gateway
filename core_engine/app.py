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
    return uniq


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
    d2_base = 0.8 if not downstream_before_constraints else 0.2
    d2 = min(d2_base + (0.2 if first_sent_has_ask else 0.0), 1.0)

    # D3: ENFORCEMENT INTEGRITY (20%)
    erosion_markers = BOUNDARY_ABSENCE_MARKERS + DCE_DEFER_MARKERS + NARRATIVE_STABILIZATION_MARKERS
    clean_sents = sum(1 for s in sents if not any(m in s.lower() for m in erosion_markers))
    clean_ratio = clean_sents / total_sents
    framing = detect_l2_framing(text)
    hedge_count = len(framing.get("hedge_markers", []))
    reassurance_count = len(framing.get("reassurance_markers", []))
    blend_count = len(framing.get("category_blend_markers", []))
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
    tilt_penalty = sum(tilt_weights.get(t, 0.05) for t in tilt_taxonomy)
    d4 = max(0, 1.0 - tilt_penalty)

    # D5: FAILURE MODE SEVERITY (20%)
    udds = detect_udds(prompt or "", answer or text, l0_constraints)
    dce = detect_dce(answer or text, l0_constraints)
    cca = detect_cca(prompt or "", answer or text)
    fm_pen = {"CONFIRMED": 0.30, "PROBABLE": 0.15, "FALSE": 0.00}
    def _fm_p(state):
        for k, v in fm_pen.items():
            if k in str(state):
                return v
        return 0.0
    total_fm = min(_fm_p(udds.get("udds_state", "")) + _fm_p(dce.get("dce_state", "")) + _fm_p(cca.get("cca_state", "")), 0.80)
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

    return {
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
            "tilt_count": len(tilt_taxonomy), "tilt_patterns": tilt_taxonomy[:10],
            "udds": udds.get("udds_state", ""), "dce": dce.get("dce_state", ""), "cca": cca.get("cca_state", "")
        }
    }


# ==========================
# L0-L7 EVALUATION
# ==========================
def detect_l0_constraints(text: str) -> List[str]:
    t = (text or "").lower()
    found = []
    for m in L0_CONSTRAINT_MARKERS:
        if m in t:
            found.append(m)
    uniq = []
    for x in found:
        if x not in uniq:
            uniq.append(x)
    return uniq[:20]


def detect_downstream_before_constraint(prompt: str, answer: str, l0_constraints: List[str]) -> bool:
    a = (answer or "").lower()
    p = (prompt or "").lower()

    capability = any(m in a for m in DOWNSTREAM_CAPABILITY_MARKERS) or any(m in p for m in DOWNSTREAM_CAPABILITY_MARKERS)
    constraints_declared = len(l0_constraints) > 0
    return bool(capability and not constraints_declared)


def detect_boundary_absence(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in BOUNDARY_ABSENCE_MARKERS) or any(m in a for m in L2_CATEGORY_BLEND)


def detect_narrative_stabilization(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in NARRATIVE_STABILIZATION_MARKERS) or any(m in a for m in L2_REASSURE)


def detect_dce(answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    a = (answer or "").lower()
    defer = any(m in a for m in DCE_DEFER_MARKERS)
    constraints_missing = len(l0_constraints) == 0

    state = "DCE_FALSE"
    if defer and constraints_missing:
        state = "DCE_CONFIRMED"
    elif defer:
        state = "DCE_PROBABLE"

    return {"dce_state": state, "defer_markers_present": defer, "constraints_missing": constraints_missing}


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

    return {"cca_state": state, "collapse_markers_present": collapse, "list_blend_present": list_blend}


def detect_udds(prompt: str, answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    c1 = len(l0_constraints) > 0
    c2 = detect_downstream_before_constraint(prompt, answer, l0_constraints)
    c3 = detect_boundary_absence(answer)
    c4 = detect_narrative_stabilization(answer)

    met = sum([1 if c else 0 for c in [c1, c2, c3, c4]])

    state = "UDDS_FALSE"
    if met == 4:
        state = "UDDS_CONFIRMED"
    elif met == 3:
        state = "UDDS_PROBABLE"

    return {
        "udds_state": state,
        "criteria": {
            "c1_l0_constraint_exists": c1,
            "c2_downstream_before_constraint_declared": c2,
            "c3_boundary_enforcement_absent_or_delayed": c3,
            "c4_narrative_stabilization_present": c4,
            "criteria_met_count": met
        }
    }


def detect_l2_framing(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    hedges = [m for m in L2_HEDGE if m in t]
    reassure = [m for m in L2_REASSURE if m in t]
    blends = [m for m in L2_CATEGORY_BLEND if m in t]
    return {
        "hedge_markers": hedges[:10],
        "reassurance_markers": reassure[:10],
        "category_blend_markers": blends[:10]
    }


def objective_extract(prompt: str) -> Dict[str, Any]:
    sents = split_sentences(prompt)
    obj = sents[0] if sents else normalize_space(prompt)
    return {"objective_text": obj[:400]}


def objective_drift(prompt: str, answer: str) -> Dict[str, Any]:
    p_tokens = tokenize(prompt)
    a_tokens = tokenize(answer)

    sim = jaccard(p_tokens, a_tokens)
    drift = round(1.0 - sim, 3)

    a = (answer or "").lower()
    mutation = any(m in a for m in L3_MUTATION_MARKERS)

    return {
        "jaccard_similarity": sim,
        "drift_score": drift,
        "mutation_markers_present": mutation
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
        axis2, highlights = get_highlights(original_text, framing=framing)
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
        if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
            signals["signals_summary"]["CCA_COLLAPSE"] = max(1, signals["signals_summary"].get("CCA_COLLAPSE", 0))
        if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
            signals["signals_summary"]["DCE_DEFERRAL"] = max(1, signals["signals_summary"].get("DCE_DEFERRAL", 0))
        if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
            signals["signals_summary"]["UDDS_DRIFT"] = max(1, signals["signals_summary"].get("UDDS_DRIFT", 0))
        tilt_to_signal = {"T8_PRESSURE_OPTIMIZATION": "SOCIAL_PRESSURE", "T7_AUTHORITY_ANCHOR": "AUTHORITY_ELEVATED", "T6_ABSOLUTE_FRAMING": "ABSOLUTE_LANGUAGE"}
        for code in (tilt or []):
            _sig = tilt_to_signal.get(code)
            if _sig:
                signals["signals_summary"][_sig] = max(1, signals["signals_summary"].get(_sig, 0))
    except Exception:
        signals = {"catalog_version": "nti-signals-v1", "signal_catalog": {}, "signals_summary": {}, "signals_detected": [], "highlights": []}

    dominance: List[str] = []
    if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
        dominance.append("CCA")
    if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
        dominance.append("UDDS")
    if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
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
        "L1_input_freeze": {"objective": obj.get("objective_text", ""), "constraints_snapshot": l0_constraints},
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
        "nii": nii.get("nii_score"),
        "tilt": tilt
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
            "final_score": v3["final_score"].get("nii_score") if isinstance(v3["final_score"], dict) else None,
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


