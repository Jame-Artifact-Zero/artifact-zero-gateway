import os
import re
import json
import time
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, request, jsonify, render_template

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "artifact-zero-change-in-prod")

from rss_proxy import rss_bp
app.register_blueprint(rss_bp)

# --- YOUR OS INTEGRATION ---
try:
    from your_os import your_os
    app.register_blueprint(your_os)
    YOUR_OS_AVAILABLE = True
except ImportError as e:
    YOUR_OS_AVAILABLE = False
    print(f"[WARN] Your OS module not loaded: {e}")
# --- END YOUR OS INTEGRATION ---

# --- CONTROL ROOM INTEGRATION ---
try:
    from control_room_bp import control_room_bp
    app.register_blueprint(control_room_bp)
    CONTROL_ROOM_AVAILABLE = True
except ImportError as e:
    CONTROL_ROOM_AVAILABLE = False
    print(f"[WARN] Control Room not loaded: {e}")
# --- END CONTROL ROOM INTEGRATION ---

# --- RELAY INTEGRATION ---
try:
    from az_relay import az_relay
    app.register_blueprint(az_relay)
    RELAY_AVAILABLE = True
except ImportError as e:
    RELAY_AVAILABLE = False
    print(f"[WARN] Relay not loaded: {e}")
# --- END RELAY INTEGRATION ---

# --- ADMIN DASHBOARD INTEGRATION ---
try:
    from admin_dashboard import init_admin, log_nti_run
    init_admin(app)
    ADMIN_AVAILABLE = True
except ImportError as e:
    ADMIN_AVAILABLE = False
    log_nti_run = None
    print(f"[WARN] Admin dashboard not loaded: {e}")
# --- END ADMIN DASHBOARD INTEGRATION ---

# ============================================================
# CANONICAL NTI RUNTIME v2.1 (RULE-BASED, NO LLM DEPENDENCY)
#
# v2.1 additions:
# - Conversational Physics Layer: OTC, CTC, ALS, Salience, ODD
# - /physics endpoint
# - /physics/declare endpoint
# - All v2.0 logic preserved unchanged
# ============================================================
NTI_VERSION = "canonical-nti-v2.1"
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_canonical.db")


# ==========================
# PHYSICS IMPORTS
# ==========================
try:
    from core_engine.otc import declare_objective, validate_objective, get_allowed_transforms, get_abstraction_range
    from core_engine.ctc import classify_transform, audit_transform
    from core_engine.als import detect_abstraction_level, check_abstraction_guard
    from core_engine.salience import detect_salience_transforms
    from core_engine.odd import detect_drift, compute_state_delta
    PHYSICS_AVAILABLE = True
except ImportError:
    PHYSICS_AVAILABLE = False


# ==========================
# DB INIT
# ==========================
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_init() -> None:
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        route TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT,
        session_id TEXT,
        latency_ms INTEGER,
        payload_json TEXT,
        error TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS results (
        request_id TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        result_json TEXT NOT NULL,
        FOREIGN KEY(request_id) REFERENCES requests(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        session_id TEXT,
        event_name TEXT NOT NULL,
        event_json TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


db_init()


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
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO requests
        (id, created_at, route, ip, user_agent, session_id, latency_ms, payload_json, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        request_id,
        utc_now_iso(),
        route,
        ip,
        ua,
        session_id,
        latency_ms,
        json.dumps(payload, ensure_ascii=False),
        error
    ))
    conn.commit()
    conn.close()


def record_result(request_id: str, result: Dict[str, Any]) -> None:
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO results
        (request_id, version, result_json)
        VALUES (?, ?, ?)
    """, (
        request_id,
        NTI_VERSION,
        json.dumps(result, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()


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
    toks = tokenize(text)
    dom = []
    for t in toks:
        if len(t) >= 4 and t not in STOPWORDS:
            dom.append(t)
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

DCE_DEFER_MARKERS = [
    "later", "eventually", "we can handle that later", "we'll address later", "we can worry later",
    "we'll figure it out", "next week", "after we launch", "phase 2", "future iteration", "future iterations",
    "explore", "consider", "evaluate", "assess", "as we continue", "as we iterate", "we will look into",
    "we'll look into", "we will revisit", "we'll revisit"
]

CCA_COLLAPSE_MARKERS = [
    "overall", "basically", "in general", "at the end of the day", "all in all", "net net",
    "it all comes down to", "the main thing", "just"
]


# ==========================
# NTE-CLF (Tilt Taxonomy)
# ==========================
TILT_TAXONOMY = {
    "T1_REASSURANCE_DRIFT": ["don't worry", "it's fine", "it's okay", "you got this", "rest assured"],
    "T3_CONSENSUS_CLAIMS": ["most people", "many people", "everyone", "no one", "in general", "typically"],
    "T6_CONSTRAINT_DEFERRAL": ["later", "eventually", "phase 2", "after we launch", "we'll figure it out", "future iteration"],
    "T7_CATEGORY_BLEND": ["kind of", "sort of", "basically", "overall", "at the end of the day"],
    "T8_PRESSURE_OPTIMIZATION": ["now", "today", "asap", "immediately", "right away", "no sooner"]
}

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

ABSOLUTE_LANGUAGE_TOKENS = [
    "always", "never", "everyone", "no one", "completely", "entirely", "100%", "guaranteed", "perfect", "zero risk"
]

AUTHORITY_IMPOSITION_TOKENS = [
    "experts agree", "industry standard", "research shows", "studies show", "best practice",
    "widely accepted", "authorities agree", "proven by research"
]

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

    for cat, markers in TILT_TAXONOMY.items():
        for m in markers:
            if m in t:
                hits.append(cat)
                break

    certainty_present = _contains_any(t, CERTAINTY_INFLATION_TOKENS)
    enforcement_present = _contains_any(t, CERTAINTY_ENFORCEMENT_VERBS)
    if certainty_present and not enforcement_present:
        hits.append("T2_CERTAINTY_INFLATION")

    if _contains_any(t, ABSOLUTE_LANGUAGE_TOKENS):
        hits.append("T5_ABSOLUTE_LANGUAGE")

    if _contains_any(t, AUTHORITY_IMPOSITION_TOKENS):
        hits.append("T10_AUTHORITY_IMPOSITION")

    if _contains_any(t, CAPABILITY_OVERREACH_TOKENS):
        hits.append("T4_CAPABILITY_OVERREACH")
    else:
        universal = any(u in t for u in ["all", "every", "any", "everything", "everyone", "no one"])
        capverb = _contains_any(t, CAPABILITY_VERBS)
        if universal and capverb:
            hits.append("T4_CAPABILITY_OVERREACH")

    if prompt and answer:
        p_dom = set(extract_domain_tokens(prompt))
        a_dom = extract_domain_tokens(answer)
        if a_dom:
            new_tokens = [x for x in a_dom if x not in p_dom]
            new_ratio = len(new_tokens) / max(len(a_dom), 1)
            if new_ratio >= 0.55 and len(new_tokens) >= 6:
                hits.append("T9_SCOPE_EXPANSION")

    uniq: List[str] = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    return uniq


# ==========================
# NII (NTI Integrity Index)
# ==========================
def compute_nii(prompt: str, answer: str, l0_constraints: List[str], downstream_before_constraints: bool, tilt_taxonomy: List[str]) -> Dict[str, Any]:
    q1 = 1.0 if len(l0_constraints) >= 1 else 0.0
    q2 = 0.0 if downstream_before_constraints else 1.0

    a_lc = (answer or "").lower()

    boundary_absent = (
        any(m in a_lc for m in BOUNDARY_ABSENCE_MARKERS) or
        any(m in a_lc for m in L2_CATEGORY_BLEND) or
        any(m in a_lc for m in DCE_DEFER_MARKERS) or
        any(m in a_lc for m in NARRATIVE_STABILIZATION_MARKERS)
    )

    structural_tilt_risk = any(t in tilt_taxonomy for t in [
        "T2_CERTAINTY_INFLATION",
        "T4_CAPABILITY_OVERREACH",
        "T5_ABSOLUTE_LANGUAGE",
        "T9_SCOPE_EXPANSION",
        "T10_AUTHORITY_IMPOSITION",
        "T7_CATEGORY_BLEND",
        "T6_CONSTRAINT_DEFERRAL",
        "T1_REASSURANCE_DRIFT"
    ])

    q3 = 0.0 if (boundary_absent or structural_tilt_risk) else 1.0

    score = round((q1 + q2 + q3) / 3.0, 2)
    return {
        "q1_constraints_explicit": q1,
        "q2_constraints_before_capability": q2,
        "q3_substitutes_after_enforcement": q3,
        "nii_score": score
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
# JOS
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
        try:
            return render_template("nti-final (2).html")
        except Exception:
            return "NTI Canonical Runtime is live."


@app.route("/app")
@app.route("/app/<section>")
def unified_shell(section=None):
    """Unified shell — persistent frame, content swaps."""
    return render_template("shell.html")


@app.route("/start")
def start_page():
    """Navigation hub — routes users by use case to relay signup."""
    try:
        return render_template("start.html")
    except Exception:
        return "Navigation hub — coming soon."


@app.route("/your-os/builder")
def your_os_builder():
    """Protocol builder page (redirect to main your-os landing)."""
    try:
        return render_template("your-os.html")
    except Exception:
        return "Your OS Builder — coming soon."


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": NTI_VERSION})


@app.route("/favicon.ico")
def favicon():
    try:
        return app.send_static_file("favicon.svg")
    except Exception:
        return app.send_static_file("favicon.png")


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
            "telemetry_and_persistence": True,
            "physics_otc": PHYSICS_AVAILABLE,
            "physics_ctc": PHYSICS_AVAILABLE,
            "physics_als": PHYSICS_AVAILABLE,
            "physics_salience": PHYSICS_AVAILABLE,
            "physics_odd": PHYSICS_AVAILABLE,
            "your_os": YOUR_OS_AVAILABLE,
        }
    })


@app.route("/events", methods=["POST"])
def events():
    session_id = get_session_id()
    payload = request.get_json() or {}
    event_name = str(payload.get("event", "")).strip()
    event_data = payload.get("data", {})

    if not event_name:
        return jsonify({"error": "Missing event name"}), 400

    eid = str(uuid.uuid4())
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO events (id, created_at, session_id, event_name, event_json)
        VALUES (?, ?, ?, ?, ?)
    """, (eid, utc_now_iso(), session_id, event_name, json.dumps(event_data, ensure_ascii=False)))
    conn.commit()
    conn.close()

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

    # Admin analytics tracking
    if ADMIN_AVAILABLE and log_nti_run:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        log_nti_run(request_id, ip, text, result, latency_ms, session_id)

    log_json_line("nti_run", {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms,
        "dominance": dominance,
        "nii": nii.get("nii_score"),
        "tilt": tilt
    })

    result["telemetry"] = {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms
    }

    return jsonify(result)


# ==========================
# PHYSICS ROUTES
# ==========================
@app.route("/physics/declare", methods=["POST"])
def physics_declare():
    """Declare an objective. Returns frozen objective state."""
    if not PHYSICS_AVAILABLE:
        return jsonify({"error": "Physics modules not available"}), 503

    payload = request.get_json() or {}
    objective_type = str(payload.get("objective_type", "")).strip()
    description = str(payload.get("description", "")).strip()

    if not objective_type:
        return jsonify({"error": "Missing objective_type. Must be one of: RESOLVE, EXPLORE, STRUCTURE, EXECUTE, DIAGNOSE, ALIGN"}), 400

    result = declare_objective(objective_type, description)
    return jsonify(result)


@app.route("/physics", methods=["POST"])
def physics_run():
    """
    Full physics audit on a text turn.
    Accepts:
      text (required)
      objective_type (required)
      previous_abstraction_level (optional, default 0)
      previous_variable_count (optional, default 0)
      current_variable_count (optional, default 0)
      previous_resolved_count (optional, default 0)
      current_resolved_count (optional, default 0)
      consecutive_stagnant_turns (optional, default 0)
    """
    if not PHYSICS_AVAILABLE:
        return jsonify({"error": "Physics modules not available"}), 503

    request_id = str(uuid.uuid4())
    session_id = get_session_id()
    t0 = time.time()

    payload = request.get_json() or {}
    text = payload.get("text", "")
    objective_type = str(payload.get("objective_type", "")).strip()

    if not text:
        return jsonify({"error": "Missing text"}), 400
    if not objective_type:
        return jsonify({"error": "Missing objective_type"}), 400

    # Run full drift detection
    drift_result = detect_drift(
        text=text,
        objective_type=objective_type,
        previous_abstraction_level=int(payload.get("previous_abstraction_level", 0)),
        previous_variable_count=int(payload.get("previous_variable_count", 0)),
        current_variable_count=int(payload.get("current_variable_count", 0)),
        previous_resolved_count=int(payload.get("previous_resolved_count", 0)),
        current_resolved_count=int(payload.get("current_resolved_count", 0)),
        stagnation_threshold=int(payload.get("stagnation_threshold", 3)),
        consecutive_stagnant_turns=int(payload.get("consecutive_stagnant_turns", 0)),
    )

    # Salience detection (separate from drift — logged, not enforced)
    salience_result = detect_salience_transforms(text)

    # Transform classification
    transform_result = classify_transform(text)

    # Abstraction detection
    abstraction_result = detect_abstraction_level(text)

    latency_ms = int((time.time() - t0) * 1000)

    result = {
        "status": "ok",
        "version": NTI_VERSION,
        "physics_version": "physics-v1.0",
        "objective_type": objective_type,
        "drift": drift_result,
        "transform": transform_result,
        "abstraction": abstraction_result,
        "salience": salience_result,
        "telemetry": {
            "request_id": request_id,
            "session_id": session_id,
            "latency_ms": latency_ms,
        }
    }

    record_request(request_id, "/physics", session_id, latency_ms, payload, error=None)
    record_result(request_id, result)

    log_json_line("physics_run", {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms,
        "objective_type": objective_type,
        "verdict": drift_result.get("verdict"),
        "violation_count": drift_result.get("violation_count"),
    })

    return jsonify(result)


# ==========================
# SEARCH (unified cross-product search)
# ==========================
@app.route("/api/search", methods=["POST"])
def unified_search():
    """Search across protocols, sessions, audits, events."""
    payload = request.get_json() or {}
    q = (payload.get("q") or "").strip()
    if not q or len(q) < 2:
        return jsonify({"results": [], "query": q})

    results = []
    like = f"%{q}%"

    # Search relay protocols
    try:
        from az_relay import db as relay_db
        conn = relay_db()
        rows = conn.execute(
            "SELECT id, name, created_at, 'protocol' as type FROM az_protocols WHERE name LIKE ? OR protocol_json LIKE ? LIMIT 10",
            (like, like)
        ).fetchall()
        for r in rows:
            results.append({"type": "protocol", "id": r["id"], "title": r["name"], "date": r["created_at"]})

        # Search relay sessions
        rows = conn.execute(
            "SELECT s.id, p.name, s.started_at, s.turn_count FROM az_sessions s JOIN az_protocols p ON s.protocol_id=p.id WHERE p.name LIKE ? LIMIT 10",
            (like,)
        ).fetchall()
        for r in rows:
            results.append({"type": "session", "id": r["id"], "title": f"{r['name']} ({r['turn_count']} turns)", "date": r["started_at"]})
        conn.close()
    except Exception:
        pass

    # Search NTI audit logs
    try:
        from admin_dashboard import get_analytics_db
        aconn = get_analytics_db()
        rows = aconn.execute(
            "SELECT id, timestamp, input_preview, nii_score FROM nti_runs WHERE input_preview LIKE ? ORDER BY timestamp DESC LIMIT 10",
            (like,)
        ).fetchall()
        for r in rows:
            results.append({"type": "audit", "id": str(r["id"]), "title": r["input_preview"][:80], "date": r["timestamp"], "score": r["nii_score"]})
        aconn.close()
    except Exception:
        pass

    # Search events
    try:
        conn2 = db_connect()
        rows = conn2.execute(
            "SELECT id, created_at, event_name, event_json FROM events WHERE event_name LIKE ? OR event_json LIKE ? ORDER BY created_at DESC LIMIT 10",
            (like, like)
        ).fetchall()
        for r in rows:
            results.append({"type": "event", "id": r["id"], "title": r["event_name"], "date": r["created_at"]})
        conn2.close()
    except Exception:
        pass

    return jsonify({"results": results, "query": q, "count": len(results)})


# ==========================
# CHAT PROXY (Control Room free tier)
# ==========================
@app.route("/api/chat", methods=["POST"])
def chat_proxy():
    """Proxy chat requests using server-side API keys for free-tier users."""
    import requests as http_req
    payload = request.get_json() or {}
    model_key = payload.get("model", "claude")
    messages = payload.get("messages", [])
    system_prompt = payload.get("system", "You are a helpful assistant.")

    if not messages:
        return jsonify({"error": "No messages provided"}), 400

    try:
        if model_key == "claude":
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
            if not api_key:
                return jsonify({"error": "Claude not configured on server"}), 503
            r = http_req.post("https://api.anthropic.com/v1/messages", headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            }, json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": messages
            }, timeout=60)
            data = r.json()
            if "error" in data:
                return jsonify({"error": data["error"].get("message", str(data["error"]))})
            reply = "".join(c.get("text", "") for c in data.get("content", []))
            return jsonify({"reply": reply})

        elif model_key == "gpt":
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                return jsonify({"error": "ChatGPT not configured on server"}), 503
            r = http_req.post("https://api.openai.com/v1/chat/completions", headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }, json={
                "model": "gpt-4o",
                "max_tokens": 4096,
                "messages": [{"role": "system", "content": system_prompt}] + messages
            }, timeout=60)
            data = r.json()
            if "error" in data:
                return jsonify({"error": data["error"].get("message", str(data["error"]))})
            reply = data.get("choices", [{}])[0].get("message", {}).get("content", "No response")
            return jsonify({"reply": reply})

        else:
            return jsonify({"error": f"Unknown model: {model_key}"}), 400

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
