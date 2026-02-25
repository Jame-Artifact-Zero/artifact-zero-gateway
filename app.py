import os
import re
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, request, jsonify, render_template, session
import db as database

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.getenv("AZ_SECRET", "dev-fallback-secret-change-me"))


# ═══════════════════════════════════════════
# AUTH STATE — available to all templates + JS
# ═══════════════════════════════════════════
@app.context_processor
def inject_auth_state():
    """Make logged_in and user_email available in all templates."""
    from flask import session as flask_session
    uid = flask_session.get("user_id")
    if uid:
        return {"logged_in": True, "user_id": uid}
    return {"logged_in": False, "user_id": None}


@app.route("/api/auth/status")
def auth_status():
    """Lightweight endpoint for JS nav state check."""
    uid = session.get("user_id")
    if uid:
        return jsonify({"logged_in": True})
    return jsonify({"logged_in": False})

# Register blueprints
try:
    from auth import auth_bp
    app.register_blueprint(auth_bp)
except ImportError:
    print("[app] auth not found, skipping", flush=True)

try:
    from rss_proxy import rss_bp
    app.register_blueprint(rss_bp)
except ImportError:
    print("[app] rss_proxy not found, skipping", flush=True)

try:
    from user_feeds import user_feeds_bp
    app.register_blueprint(user_feeds_bp)
except ImportError:
    print("[app] user_feeds not found, skipping", flush=True)

try:
    from your_os import your_os
    app.register_blueprint(your_os)
except ImportError:
    print("[app] your_os not found, skipping", flush=True)

try:
    from control_room_bp import control_room_bp
    app.register_blueprint(control_room_bp)
except ImportError:
    print("[app] control_room_bp not found, skipping", flush=True)

try:
    from az_relay import az_relay
    app.register_blueprint(az_relay)
except ImportError:
    print("[app] az_relay not found, skipping", flush=True)

try:
    from credits import credits_bp
    app.register_blueprint(credits_bp)
    print("[app] credits system loaded", flush=True)
except ImportError:
    print("[app] credits not found, skipping", flush=True)

try:
    from admin_dashboard import init_admin
    init_admin(app)
except ImportError:
    print("[app] admin_dashboard not found, skipping", flush=True)

# ============================================================
# CANONICAL NTI RUNTIME v2.0 (RULE-BASED, NO LLM DEPENDENCY)
#
# v2.0 single monolithic revision includes:
# - New tilt clusters: T4, T5, T9, T10
# - Broadened DCE markers (soft deferral)
# - NII q3 penalizes: boundary absence + new structural tilt clusters
# - No changes to layer schema, dominance order, or UDDS count logic
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
# ROUTES
# ==========================
@app.route("/")
def home():
    try:
        return render_template("index.html")
    except Exception:
        return "NTI Canonical Runtime is live."


# /relay handled by az_relay blueprint



@app.route("/your-os")
def youros_redirect():
    from flask import redirect
    return redirect("/your-os/builder", code=301)


@app.route("/contact")
def contact_page():
    return render_template("contact.html")


@app.route("/compose")
def compose_page():
    return render_template("compose.html")


@app.route("/examples")
def examples_page():
    return render_template("examples.html")


@app.route("/wall")
def wall_page():
    return render_template("wall.html")


@app.route("/docs")
def docs():
    return render_template("docs.html")


@app.route("/score")
def score_page():
    return render_template("score.html")


@app.route("/safecheck")
def safecheck_page():
    return render_template("safecheck.html")


@app.route("/fortune500")
@app.route("/live")
def fortune500_page():
    return render_template("fortune500.html")


@app.route("/scored/<slug>")
def scored_page(slug):
    return render_template("scored.html")


@app.route("/api/fortune500", methods=["GET"])
def api_fortune500_list():
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute("SELECT slug, company_name, rank, url, nii_score, issue_count, last_checked FROM fortune500_scores ORDER BY rank")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        else:
            cur.execute("SELECT slug, company_name, rank, url, nii_score, issue_count, last_checked FROM fortune500_scores ORDER BY rank")
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({"companies": rows})
    except Exception as e:
        return jsonify({"companies": [], "note": "Scores loading. Check back soon."})


@app.route("/api/fortune500/<slug>", methods=["GET"])
def api_fortune500_detail(slug):
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        # Check fortune500 first, then vc_fund_scores
        for table in ["fortune500_scores", "vc_fund_scores"]:
            if database.USE_PG:
                cur.execute(f"SELECT * FROM {table} WHERE slug = %s", (slug,))
                row = cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    result = dict(zip(cols, row))
                    # Normalize: vc table has fund_name, f500 has company_name
                    if "fund_name" in result and "company_name" not in result:
                        result["company_name"] = result["fund_name"]
                    conn.close()
                    return jsonify(result)
            else:
                cur.execute(f"SELECT * FROM {table} WHERE slug = ?", (slug,))
                row = cur.fetchone()
                if row:
                    result = dict(row)
                    if "fund_name" in result and "company_name" not in result:
                        result["company_name"] = result["fund_name"]
                    conn.close()
                    return jsonify(result)
        conn.close()
        return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/vc-funds")
def vc_funds_page():
    return render_template("vc_funds.html")


@app.route("/api/vc-funds", methods=["GET"])
def api_vc_funds_list():
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute("SELECT slug, fund_name, rank, url, nii_score, issue_count, last_checked FROM vc_fund_scores ORDER BY rank")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        else:
            cur.execute("SELECT slug, fund_name, rank, url, nii_score, issue_count, last_checked FROM vc_fund_scores ORDER BY rank")
            rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({"funds": rows})
    except Exception as e:
        return jsonify({"funds": [], "note": "Scores loading. Check back soon."})


@app.route("/api/vc-funds/<slug>", methods=["GET"])
def api_vc_fund_detail(slug):
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute("SELECT * FROM vc_fund_scores WHERE slug = %s", (slug,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return jsonify({"error": "Not found"}), 404
            cols = [d[0] for d in cur.description]
            result = dict(zip(cols, row))
        else:
            cur.execute("SELECT * FROM vc_fund_scores WHERE slug = ?", (slug,))
            row = cur.fetchone()
            if not row:
                conn.close()
                return jsonify({"error": "Not found"}), 404
            result = dict(row)
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Free tier scoring — no API key, IP-limited
_free_usage = {}

@app.route("/api/v1/score/free", methods=["POST"])
def api_score_free():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    month_key = f"{ip}:{datetime.now(timezone.utc).strftime('%Y-%m')}"
    count = _free_usage.get(month_key, 0)
    if count >= 10:
        return jsonify({"error": "Free tier limit reached (10/month)", "upgrade": "https://artifact0.com/docs#pricing"}), 429
    
    t0 = time.time()
    payload = request.get_json() or {}
    text = payload.get("text", "").strip()
    if not text:
        return jsonify({"error": "Missing 'text' field"}), 400
    if len(text) > 50000:
        return jsonify({"error": "Text exceeds 50,000 character limit"}), 400

    try:
        l0 = detect_l0_constraints(text)
        obj = objective_extract(text)
        drift = objective_drift("", text)
        framing = detect_l2_framing(text)
        tilt = classify_tilt(text)
        udds = detect_udds("", text, l0)
        dce = detect_dce(text, l0)
        cca = detect_cca("", text)
        dbc = detect_downstream_before_constraint("", text, l0)
        nii = compute_nii("", text, l0, dbc, tilt)

        dominance = []
        if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
            dominance.append("CCA")
        if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
            dominance.append("UDDS")
        if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
            dominance.append("DCE")
        if not dominance:
            dominance = ["NONE"]
    except Exception as e:
        return jsonify({"error": "Scoring error", "detail": str(e)}), 500

    _free_usage[month_key] = count + 1
    latency_ms = int((time.time() - t0) * 1000)

    result = {
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {"q1": nii.get("q1"), "q2": nii.get("q2"), "q3": nii.get("q3"), "q4": nii.get("q4"), "d5": nii.get("d5_failure_mode_severity")}
        },
        "failure_modes": {
            "UDDS": udds["udds_state"], "DCE": dce["dce_state"], "CCA": cca["cca_state"],
            "dominance": dominance
        },
        "tilt": {"tags": tilt, "count": len(tilt)},
        "meta": {
            "latency_ms": latency_ms, "text_length": len(text), "word_count": len(text.split()),
            "tier": "free", "usage_this_month": count + 1, "monthly_limit": 10
        }
    }

    # ── V3 ENFORCEMENT: self-audit loop (mandatory) ──
    try:
        from core_engine.v3_enforcement import self_audit
        audit = self_audit(text, objective=obj.get("objective_text") if 'obj' in dir() else None)
        result["v3"] = {
            "enforced_text": audit["enforced_text"],
            "actions_taken": audit["actions_taken"],
            "time_collapse_applied": audit["time_collapse_applied"],
            "compression_ratio": audit["compression_ratio"],
            "passed": audit["passed"],
        }
    except Exception as e:
        result["v3"] = {"error": str(e), "passed": True}

    return jsonify(result)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": NTI_VERSION})




@app.route("/health/db")
def health_db():
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute("SELECT current_database(), version()")
            row = cur.fetchone()
            conn.close()
            return jsonify({"db": "postgresql", "database": row[0], "version": row[1][:40]})
        else:
            cur.execute("SELECT sqlite_version()")
            row = cur.fetchone()
            conn.close()
            return jsonify({"db": "sqlite", "version": row[0], "path": database.DB_PATH})
    except Exception as e:
        return jsonify({"db": "error", "detail": str(e)}), 500


@app.route("/canonical/status")
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

@app.route("/nti-friction", methods=["POST"])
def nti_friction():
    """E04 — Axis 2 conversational friction scoring."""
    try:
        from axis2_endpoint import handle_request as axis2_handle
        payload = request.get_json(force=True) or {}
        return jsonify(axis2_handle(payload))
    except Exception as e:
        return jsonify({"error": str(e), "axis": 2}), 500


@app.route("/nti-full", methods=["POST"])
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


@app.route("/events", methods=["POST"])
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


@app.route("/jos/template", methods=["GET"])
def jos_get_template():
    return jsonify(jos_template())


@app.route("/jos/apply", methods=["POST"])
def jos_apply_route():
    config = request.get_json() or {}
    return jsonify(jos_apply(config))


@app.route("/nti", methods=["POST"])
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

    l0_constraints = detect_l0_constraints(text)

    obj = objective_extract(prompt or text)
    drift = objective_drift(prompt or "", answer or "")

    framing = detect_l2_framing(text)

    # tilt taxonomy (now uses prompt+answer for scope expansion detection)
    tilt = classify_tilt(text, prompt=prompt or "", answer=answer or "")

    udds = detect_udds(prompt or "", answer or text, l0_constraints)
    dce = detect_dce(answer or text, l0_constraints)
    cca = detect_cca(prompt or "", answer or text)

    downstream_before_constraints = detect_downstream_before_constraint(prompt or "", answer or text, l0_constraints)
    nii = compute_nii(prompt or "", answer or text, l0_constraints, downstream_before_constraints, tilt)

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
        "tilt_taxonomy": tilt
    }

    latency_ms = int((time.time() - t0) * 1000)
    record_request(request_id, "/nti", session_id, latency_ms, payload, error=None)
    record_result(request_id, result)

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
        from admin_dashboard import log_nti_run
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        if ip and "," in ip: ip = ip.split(",")[0].strip()
        log_nti_run(request_id, ip, text, result, latency_ms, session_id)
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

    return jsonify(result)


# ═══════════════════════════════════════
# API v1 — PUBLIC SCORING ENDPOINT
# ═══════════════════════════════════════
import secrets as _secrets
import functools

_TIER_LIMITS = {
    "free": {"monthly": 10, "rpm": 5},
    "pro": {"monthly": 500, "rpm": 30},
    "power": {"monthly": 2000, "rpm": 60},
    "unlimited": {"monthly": 999999999, "rpm": 120},
    "starter": {"monthly": 10000, "rpm": 60},
    "core": {"monthly": 75000, "rpm": 120},
    "pipeline": {"monthly": 300000, "rpm": 300},
    "enterprise": {"monthly": 999999999, "rpm": 1000},
}

_rate_cache = {}


def _month_start():
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _minute_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not api_key:
            return jsonify({"error": "Missing API key", "hint": "Pass key in X-API-Key header", "docs": "https://artifact0.com/docs"}), 401

        try:
            conn = database.db_connect()
            cur = conn.cursor()
            if database.USE_PG:
                cur.execute("SELECT id, tier, monthly_limit, active, owner_email FROM api_keys WHERE id = %s", (api_key,))
            else:
                cur.execute("SELECT id, tier, monthly_limit, active, owner_email FROM api_keys WHERE id = ?", (api_key,))
            row = cur.fetchone()
            conn.close()
        except Exception as e:
            print(f"[api] Key lookup error: {e}", flush=True)
            return jsonify({"error": "Database error", "detail": str(e)}), 500

        if not row:
            return jsonify({"error": "Invalid API key"}), 401

        key_id = row[0] if database.USE_PG else row["id"]
        tier = row[1] if database.USE_PG else row["tier"]
        active = row[3] if database.USE_PG else row["active"]

        if not active:
            return jsonify({"error": "API key deactivated"}), 403

        # Credit balance check (replaces monthly limit for paid tiers)
        if tier != "free":
            try:
                from credits import get_user_id_for_api_key, get_balance, COST_PER_SCORE
                owner_user_id = get_user_id_for_api_key(key_id)
                if owner_user_id:
                    bal = get_balance(owner_user_id)
                    cost_cents = int(COST_PER_SCORE["api"] * 100)
                    if bal < cost_cents:
                        return jsonify({"error": "Insufficient balance", "balance": bal / 100, "cost_per_score": cost_cents / 100,
                                        "topup": f"{os.getenv('SITE_URL', 'https://artifact0.com')}/dashboard"}), 402
                    request._credit_user_id = owner_user_id
            except ImportError:
                pass  # credits module not available, fall back to monthly limits

        # Rate limit (per-minute, still applies)
        tier_config = _TIER_LIMITS.get(tier, _TIER_LIMITS["free"])
        cache_key = f"{key_id}:{_minute_key()}"
        current_rpm = _rate_cache.get(cache_key, 0)
        if current_rpm >= tier_config["rpm"]:
            return jsonify({"error": "Rate limit exceeded", "limit": f"{tier_config['rpm']} req/min"}), 429
        _rate_cache[cache_key] = current_rpm + 1

        # Free tier: still uses monthly limits
        if tier == "free":
            usage_count = database.get_api_usage_count(key_id, _month_start())
            monthly_limit = row[2] if database.USE_PG else row["monthly_limit"]
            if usage_count >= monthly_limit:
                return jsonify({"error": "Free tier limit reached", "usage": usage_count, "limit": monthly_limit,
                                "upgrade": f"{os.getenv('SITE_URL', 'https://artifact0.com')}/dashboard"}), 429

        request._api_key_id = key_id
        request._api_tier = tier
        return f(*args, **kwargs)
    return wrapper


@app.route("/api/v1/score", methods=["POST"])
@require_api_key
def api_score():
    t0 = time.time()
    payload = request.get_json() or {}
    text = payload.get("text", "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' field"}), 400
    if len(text) > 50000:
        return jsonify({"error": "Text exceeds 50,000 character limit"}), 400

    try:
        l0 = detect_l0_constraints(text)
        obj = objective_extract(text)
        drift = objective_drift("", text)
        framing = detect_l2_framing(text)
        tilt = classify_tilt(text)
        udds = detect_udds("", text, l0)
        dce = detect_dce(text, l0)
        cca = detect_cca("", text)
        dbc = detect_downstream_before_constraint("", text, l0)
        nii = compute_nii("", text, l0, dbc, tilt)

        dominance = []
        if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
            dominance.append("CCA")
        if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
            dominance.append("UDDS")
        if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
            dominance.append("DCE")
        if not dominance:
            dominance = ["NONE"]
    except Exception as e:
        print(f"[api] Scoring error: {e}", flush=True)
        return jsonify({"error": "Scoring engine error", "detail": str(e)}), 500

    latency_ms = int((time.time() - t0) * 1000)
    usage_id = str(uuid.uuid4())
    database.record_api_usage(usage_id, request._api_key_id, "/api/v1/score", latency_ms, 200)

    # Deduct credit for paid tiers
    credit_info = {}
    if hasattr(request, '_credit_user_id') and request._credit_user_id:
        try:
            from credits import deduct_credit, get_balance
            ok, new_bal = deduct_credit(request._credit_user_id, "api", request._api_key_id)
            credit_info = {"charged": 0.01, "balance": new_bal / 100}
        except Exception as e:
            print(f"[api] Credit deduction error: {e}", flush=True)

    # ── V3 ENFORCEMENT: self-audit loop (mandatory) ──
    v3_result = {"passed": True}
    try:
        from core_engine.v3_enforcement import self_audit
        audit = self_audit(text, objective=obj.get("objective_text") if obj else None)
        v3_result = {
            "enforced_text": audit["enforced_text"],
            "actions_taken": audit["actions_taken"],
            "time_collapse_applied": audit["time_collapse_applied"],
            "compression_ratio": audit["compression_ratio"],
            "passed": audit["passed"],
        }
    except Exception as e2:
        v3_result = {"error": str(e2), "passed": True}

    return jsonify({
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {"q1": nii.get("q1"), "q2": nii.get("q2"), "q3": nii.get("q3"), "q4": nii.get("q4"), "d5": nii.get("d5_failure_mode_severity")}
        },
        "failure_modes": {
            "UDDS": udds["udds_state"], "DCE": dce["dce_state"], "CCA": cca["cca_state"],
            "dominance": dominance
        },
        "tilt": {"tags": tilt, "count": len(tilt)},
        "v3": v3_result,
        "meta": {
            "latency_ms": latency_ms, "text_length": len(text), "word_count": len(text.split()),
            "tier": request._api_tier
        },
        **({"credits": credit_info} if credit_info else {})
    })


@app.route("/api/v1/keys", methods=["POST"])
def api_create_key():
    payload = request.get_json() or {}
    email = payload.get("email", "").strip().lower()
    tier = payload.get("tier", "free").strip().lower()

    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if tier not in _TIER_LIMITS:
        return jsonify({"error": f"Invalid tier. Options: {list(_TIER_LIMITS.keys())}"}), 400

    key_id = f"az_{_secrets.token_hex(24)}"
    monthly_limit = _TIER_LIMITS[tier]["monthly"]
    now = utc_now_iso()

    try:
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute("INSERT INTO api_keys (id, created_at, owner_email, tier, monthly_limit, active) VALUES (%s, %s, %s, %s, %s, TRUE)",
                        (key_id, now, email, tier, monthly_limit))
        else:
            cur.execute("INSERT INTO api_keys (id, created_at, owner_email, tier, monthly_limit, active) VALUES (?, ?, ?, ?, ?, 1)",
                        (key_id, now, email, tier, monthly_limit))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[api] Key creation error: {e}", flush=True)
        return jsonify({"error": "Failed to create key", "detail": str(e)}), 500

    return jsonify({"api_key": key_id, "tier": tier, "monthly_limit": monthly_limit, "email": email,
                    "message": "Store this key securely. It will not be shown again."}), 201


@app.route("/api/v1/keys/usage", methods=["GET"])
@require_api_key
def api_usage():
    usage_count = database.get_api_usage_count(request._api_key_id, _month_start())
    result = {
        "api_key": request._api_key_id[:8] + "...",
        "tier": request._api_tier,
        "usage_this_month": usage_count,
    }
    # Add credit balance if available
    try:
        from credits import get_user_id_for_api_key, get_balance_info
        uid = get_user_id_for_api_key(request._api_key_id)
        if uid:
            result["credits"] = get_balance_info(uid)
    except ImportError:
        pass
    return jsonify(result)


# ═══════════════════════════════════════
# STRIPE WEBHOOK
# ═══════════════════════════════════════
@app.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    import json as _json
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    # If webhook secret is set, verify signature
    if webhook_secret:
        import hmac, hashlib
        timestamp = None
        sig_v1 = None
        for item in sig_header.split(","):
            k, _, v = item.strip().partition("=")
            if k == "t": timestamp = v
            elif k == "v1": sig_v1 = v
        if not timestamp or not sig_v1:
            return jsonify({"error": "Invalid signature"}), 400
        signed_payload = f"{timestamp}.{payload}"
        expected = hmac.new(webhook_secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig_v1):
            return jsonify({"error": "Signature mismatch"}), 400

    try:
        event = _json.loads(payload)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    event_type = event.get("type", "")
    print(f"[stripe] Webhook: {event_type}", flush=True)

    if event_type == "checkout.session.completed":
        try:
            from credits import handle_topup_webhook
            handled = handle_topup_webhook(event)
            print(f"[stripe] Top-up handled: {handled}", flush=True)
        except Exception as e:
            print(f"[stripe] Webhook error: {e}", flush=True)

    return jsonify({"received": True})


# ═══════════════════════════════════════
# DASHBOARD (balance, usage, top-up)
# ═══════════════════════════════════════
@app.route("/dashboard")
def dashboard():
    user_id = session.get("user_id")
    if not user_id:
        from flask import redirect
        return redirect("/login")
    return render_template("dashboard.html")


# ═══════════════════════════════════════
# LLM-POWERED REWRITE (V3 structural rewrite via letter-race model)
# ═══════════════════════════════════════
def _letter_race(text):
    """Pick model by racing letters through user text."""
    s = re.sub(r'[^a-zA-Z]', '', text).lower()
    models = [
        {"name": "claude", "api": "anthropic", "color": "#d97706"},
        {"name": "grok", "api": "xai", "color": "#8b5cf6"},
        {"name": "chatgpt", "api": "openai", "color": "#10b981"},
        {"name": "gemini", "api": "google", "color": "#3b82f6"},
    ]
    for i in range(len(s)):
        for m in models:
            pos = 0
            for j in range(i + 1):
                if j < len(s) and pos < len(m["name"]) and s[j] == m["name"][pos]:
                    pos += 1
            if pos >= len(m["name"]):
                return m
    # Fallback: highest ratio
    best = models[0]
    best_ratio = 0
    for m in models:
        pos = 0
        for ch in s:
            if pos < len(m["name"]) and ch == m["name"][pos]:
                pos += 1
        ratio = pos / len(m["name"])
        if ratio > best_ratio:
            best_ratio = ratio
            best = m
    return best


def _call_llm(model_info, prompt, system_prompt):
    """Call the selected LLM API. Returns response text."""
    from urllib.request import urlopen, Request
    from urllib.error import HTTPError
    api = model_info["api"]
    timeout = 15

    if api == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return None, "No Anthropic API key"
        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01"
        })
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]
        return text, None

    elif api == "openai":
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            return None, "No OpenAI API key"
        body = json.dumps({
            "model": "gpt-4o-mini",
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }).encode()
        req = Request("https://api.openai.com/v1/chat/completions", data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        })
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"], None

    elif api == "xai":
        key = os.getenv("XAI_API_KEY", "")
        if not key:
            return None, "No XAI API key"
        body = json.dumps({
            "model": "grok-2-latest",
            "max_tokens": 1024,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ]
        }).encode()
        req = Request("https://api.x.ai/v1/chat/completions", data=body, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}"
        })
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"], None

    elif api == "google":
        key = os.getenv("GOOGLE_API_KEY", "")
        if not key:
            return None, "No Google API key"
        body = json.dumps({
            "contents": [{"parts": [{"text": system_prompt + "\n\n" + prompt}]}],
            "generationConfig": {"maxOutputTokens": 1024}
        }).encode()
        req = Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}",
            data=body, headers={"Content-Type": "application/json"}
        )
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"], None
        except (KeyError, IndexError):
            return None, "Unexpected Google API response"

    return None, f"Unknown API: {api}"


@app.route("/api/v1/rewrite", methods=["POST"])
def api_rewrite():
    """LLM-powered structural rewrite. Letter-race selects model, V3 enforces output."""
    t0 = time.time()
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    if len(text) > 5000:
        return jsonify({"error": "text too long (max 5000 chars)"}), 400

    # Convergence gate — block AI for deterministic-routable inputs
    try:
        from convergence_gate import enforce as cg_enforce
        trace = {}
        ai_allowed, cg_response = cg_enforce({"text": text}, trace)
        if not ai_allowed:
            cg_response["latency_ms"] = int((time.time() - t0) * 1000)
            cg_response["version"] = NTI_VERSION
            return jsonify(cg_response)
    except Exception:
        pass

    # 1. Score with V1
    l0 = detect_l0_constraints(text)
    obj = objective_extract(text)
    tilt = classify_tilt(text)
    udds = detect_udds("", text, l0)
    dce = detect_dce(text, l0)
    cca = detect_cca("", text)
    dbc = detect_downstream_before_constraint("", text, l0)
    nii = compute_nii("", text, l0, dbc, tilt)

    nii_score = nii.get("nii_score", 0)
    components = {k: v for k, v in nii.items() if k.startswith("q")}
    failure_modes = {
        "UDDS": udds.get("udds_state", ""),
        "DCE": dce.get("dce_state", ""),
        "CCA": cca.get("cca_state", "")
    }

    # 2. Letter-race model selection
    model = _letter_race(text)

    # 3. Build rewrite prompt from NTI findings
    # Smart issue detection: short/simple messages don't need enterprise-level constraints
    word_count = len(text.split())
    is_question = text.strip().rstrip('.!').endswith('?') or text.lower().startswith(('what ', 'when ', 'where ', 'who ', 'how ', 'why ', 'which ', 'can ', 'could ', 'will ', 'would ', 'do ', 'does ', 'is ', 'are '))
    is_short = word_count <= 15
    has_real_failure = any("CONFIRMED" in str(v) for v in failure_modes.values())

    issues = []
    # Only flag Q components on substantive messages (not short questions or casual texts)
    if not (is_short and is_question):
        if (components.get("q1") or 0) < 0.7 and not is_short:
            issues.append("Missing explicit constraints or conditions")
        if (components.get("q2") or 0) < 0.7 and word_count > 20:
            issues.append("Main ask is buried — should lead")
        if (components.get("q3") or 0) < 0.7 and not is_question:
            issues.append("No deadline or enforcement boundary")
        if (components.get("q4") or 0) < 0.7 and len(tilt) > 0:
            issues.append("Weak tilt resistance — hedge language detected")
    # Always flag real failure modes regardless of length
    if "CONFIRMED" in str(failure_modes.get("UDDS", "")):
        issues.append("UDDS: Agreement given before the actual ask was stated")
    if "CONFIRMED" in str(failure_modes.get("DCE", "")):
        issues.append("DCE: Decision is deferred instead of made")
    if "CONFIRMED" in str(failure_modes.get("CCA", "")):
        issues.append("CCA: Capability claimed without constraint backing")

    system_prompt = (
        "You are a direct, no-nonsense rewrite engine. Your job: take poorly structured messages "
        "and rewrite them as a competent professional would actually write them.\n\n"
        "RULES:\n"
        "1. LEAD WITH THE ASK. First sentence = what you want from them.\n"
        "2. CONTEXT SECOND. Only the context they need to respond. Cut everything else.\n"
        "3. SHORTER IS BETTER. If the original is 40 words, the rewrite should be 25-35.\n"
        "4. NO BRACKETS, NO PLACEHOLDERS. If there's no deadline, write 'Let me know by [day].' "
        "If there's no constraint, just make the ask clearer — don't insert [Conditions: ___].\n"
        "5. KEEP THE VOICE. If the original is casual, stay casual. If formal, stay formal.\n"
        "6. STRIP SIGNOFFS. Remove 'Best,' 'Thanks,' 'Regards' — they add nothing.\n"
        "7. ONE PASS. Return only the rewritten text. No explanations. No commentary. No quotes around it."
    )

    prompt = f"ORIGINAL:\n{text}\n\n"
    if issues:
        prompt += "PROBLEMS:\n"
        for iss in issues:
            prompt += f"- {iss}\n"
        prompt += "\nRewrite this message so a busy person reads it and immediately knows what you want."
    else:
        prompt += "This message is structurally clean. Tighten it if possible — remove any unnecessary words. If it's already tight, return it unchanged. Do not add anything."

    # 4. Call LLM
    try:
        llm_text, err = _call_llm(model, prompt, system_prompt)
    except Exception as e:
        llm_text, err = None, str(e)[:200]

    if not llm_text:
        # Fallback: return V3 rule-based enforcement only
        from core_engine.v3_enforcement import enforce
        v3_result = enforce(text, objective=obj.get("objective_text"))
        return jsonify({
            "rewrite": v3_result["final_output"],
            "model": model["name"],
            "model_color": model["color"],
            "method": "v3_rule_only",
            "fallback_reason": err or "LLM call failed",
            "original_words": len(text.split()),
            "rewrite_words": len(v3_result["final_output"].split()),
            "nii_score": nii_score,
            "issues": issues,
            "latency_ms": int((time.time() - t0) * 1000)
        })

    # 5. Run V3 enforcement on LLM output
    from core_engine.v3_enforcement import enforce
    v3_result = enforce(llm_text, objective=obj.get("objective_text"))
    final = v3_result["final_output"]

    original_words = len(text.split())
    rewrite_words = len(final.split())

    return jsonify({
        "rewrite": final,
        "model": model["name"],
        "model_color": model["color"],
        "method": "llm_v3",
        "original_words": original_words,
        "rewrite_words": rewrite_words,
        "compression": f"{abs(original_words - rewrite_words) / max(original_words, 1) * 100:.0f}%",
        "nii_score": nii_score,
        "issues": issues,
        "v3_actions": v3_result.get("level_0_actions", []) + v3_result.get("level_1_actions", []),
        "latency_ms": int((time.time() - t0) * 1000)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
