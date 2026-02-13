import os
import re
import json
import time
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional

from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# ============================================================
# CANONICAL NTI RUNTIME v1.0 (RULE-BASED, NO LLM DEPENDENCY)
# ============================================================
NTI_VERSION = "canonical-nti-v1.0"
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_canonical.db")


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

def tokenize(text: str) -> List[str]:
    return [t.lower() for t in WORD_RE.findall(text or "")]

def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def split_sentences(text: str) -> List[str]:
    t = normalize_space(text)
    if not t:
        return []
    # naive sentence split (good enough for v1)
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

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ==========================
# CANONICAL LAYER MODEL (L0-L7)
# ==========================
# L0 — Reality Substrate: detect explicit constraints / irreducible dependencies
L0_CONSTRAINT_MARKERS = [
    "must", "cannot", "can't", "won't", "requires", "require", "only if", "no way", "not possible",
    "dependency", "dependent", "api key", "openai", "render", "legal", "policy", "security", "compliance",
    "budget", "deadline", "today", "production", "cannot expose", "secret", "token"
]

# L2 — Interpretive Framing: detect hedging, smoothing, reassurance, category blending
L2_HEDGE = ["maybe", "might", "could", "perhaps", "it seems", "it sounds", "generally", "often", "usually", "in general"]
L2_REASSURE = ["don't worry", "no problem", "it's okay", "you got this", "rest assured", "glad", "happy to"]
L2_CATEGORY_BLEND = ["kind of", "sort of", "basically", "overall", "in other words", "at the end of the day"]

# L3 — Objective Freeze: detect objective mutation between prompt and answer
L3_MUTATION_MARKERS = ["instead", "rather than", "we should pivot", "let's change", "new plan", "different approach", "actually"]

# L4-L5 — Execution vectors & outputs: we represent as structured plan artifacts (not UI)
# L7 — Telemetry: always captured


# ==========================
# PARENT FAILURE MODES (UDDS / DCE / CCA)
# ==========================
# UDDS criteria (doc summary):
# 1) L0 constraint exists
# 2) downstream capability introduced before constraint declared
# 3) boundary enforcement absent/delayed (hedging, blending, time-to-answer inflation)
# 4) narrative stabilization present (reassurance/social smoothing)

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

# DCE: Deferred Constraint Externalization (heuristic)
DCE_DEFER_MARKERS = [
    "later", "eventually", "we can handle that later", "we'll address later", "we can worry later",
    "we'll figure it out", "next week", "after we launch", "phase 2"
]

# CCA: Constraint Collapse via Aggregation (heuristic)
CCA_COLLAPSE_MARKERS = [
    "overall", "basically", "in general", "at the end of the day", "all in all", "net net",
    "it all comes down to", "the main thing", "just"
]


# ==========================
# NTE-CLF (Tilt Taxonomy v0.2) — RULE-BASED CLASSIFIER
# ==========================
# We implement 8 tilt categories as keyword clusters.
TILT_TAXONOMY = {
    "T1_REASSURANCE_DRIFT": ["don't worry", "it's fine", "it's okay", "you got this", "rest assured"],
    "T2_ASSUMPTIVE_PERSONALIZATION": ["you are the kind of", "you probably", "you might", "it sounds like you", "it seems like you"],
    "T3_CONSENSUS_CLAIMS": ["most people", "many people", "everyone", "no one", "in general", "typically"],
    "T4_AUTHORITY_INFLATION": ["clearly", "obviously", "definitely", "certainly", "undeniably", "proven", "fact"],
    "T5_SCOPE_CREEP": ["also", "and another", "while we're at it", "let's add", "plus", "in addition"],
    "T6_CONSTRAINT_DEFERRAL": ["later", "eventually", "phase 2", "after we launch", "we'll figure it out"],
    "T7_CATEGORY_BLEND": ["kind of", "sort of", "basically", "overall", "at the end of the day"],
    "T8_PRESSURE_OPTIMIZATION": ["now", "today", "asap", "immediately", "right away", "no sooner"]
}

def classify_tilt(text: str) -> List[str]:
    t = (text or "").lower()
    hits = []
    for cat, markers in TILT_TAXONOMY.items():
        for m in markers:
            if m in t:
                hits.append(cat)
                break
    return hits


# ==========================
# NII (NTI Integrity Index)
# ==========================
def compute_nii(prompt: str, answer: str, l0_constraints: List[str], downstream_before_constraints: bool) -> Dict[str, Any]:
    # NII questions (doc):
    # 1) constraints singular and explicit (prevents CCA)
    # 2) constraints declared before capability planning (prevents DCE)
    # 3) substitutes introduced only after enforcement (prevents UDDS)

    # Q1: explicitness = count of constraints markers found
    q1 = 1.0 if len(l0_constraints) >= 1 else 0.0

    # Q2: constraints before capability
    q2 = 0.0 if downstream_before_constraints else 1.0

    # Q3: substitute after enforcement (we approximate: if answer includes boundary markers, treat as not enforced)
    boundary_absent = any(m in (answer or "").lower() for m in BOUNDARY_ABSENCE_MARKERS)
    q3 = 0.0 if boundary_absent else 1.0

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
    # keep unique in input order
    uniq = []
    for x in found:
        if x not in uniq:
            uniq.append(x)
    return uniq[:20]


def detect_downstream_before_constraint(prompt: str, answer: str, l0_constraints: List[str]) -> bool:
    # If answer proposes building/shipping capability AND prompt constraints are absent/weak,
    # treat as downstream before constraint declaration.
    a = (answer or "").lower()
    p = (prompt or "").lower()

    capability = any(m in a for m in DOWNSTREAM_CAPABILITY_MARKERS) or any(m in p for m in DOWNSTREAM_CAPABILITY_MARKERS)
    constraints_declared = len(l0_constraints) > 0
    # downstream before constraints: capability present and constraints not declared
    return bool(capability and not constraints_declared)


def detect_boundary_absence(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in BOUNDARY_ABSENCE_MARKERS) or any(m in a for m in L2_CATEGORY_BLEND)


def detect_narrative_stabilization(answer: str) -> bool:
    a = (answer or "").lower()
    return any(m in a for m in NARRATIVE_STABILIZATION_MARKERS) or any(m in a for m in L2_REASSURE)


def detect_dce(prompt: str, answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    # DCE: pushing constraints out of the current turn.
    a = (answer or "").lower()
    p = (prompt or "").lower()

    defer = any(m in a for m in DCE_DEFER_MARKERS)
    constraints_missing = len(l0_constraints) == 0

    # Probable if deferral language exists; confirmed if deferral + constraints missing
    state = "DCE_FALSE"
    if defer and constraints_missing:
        state = "DCE_CONFIRMED"
    elif defer:
        state = "DCE_PROBABLE"

    return {"dce_state": state, "defer_markers_present": defer, "constraints_missing": constraints_missing}


def detect_cca(prompt: str, answer: str) -> Dict[str, Any]:
    # CCA: collapsing multiple constraints into vague aggregation.
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
    # UDDS criteria:
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
    # very simple: first sentence is treated as objective question
    sents = split_sentences(prompt)
    obj = sents[0] if sents else normalize_space(prompt)
    return {"objective_text": obj[:400]}


def objective_drift(prompt: str, answer: str) -> Dict[str, Any]:
    # If we have both prompt & answer, compare token similarity.
    p_tokens = tokenize(prompt)
    a_tokens = tokenize(answer)

    sim = jaccard(p_tokens, a_tokens)

    # drift score: 1 - similarity
    drift = round(1.0 - sim, 3)

    # mutation markers in answer
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
    # This is a fill-in-the-blank form. UI can render later. Runtime stores it now.
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
    # Minimal enforcement checks: presence + explicitness.
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
# Evaluation Tools (OI-UEP, OI-IC, H-I²E)
# ==========================
def eval_oi_uep(objective: str, baseline: str, perturbed: str) -> Dict[str, Any]:
    # Objective Integrity Under Emotional Perturbation:
    # Compare baseline vs perturbed similarity; drift indicates integrity loss.
    b = tokenize(baseline)
    p = tokenize(perturbed)
    sim = jaccard(b, p)
    drift = round(1.0 - sim, 3)

    # Emotional marker hit
    em = classify_tilt(perturbed)
    emotion_present = any(cat in em for cat in ["T1_REASSURANCE_DRIFT", "T8_PRESSURE_OPTIMIZATION"])

    return {
        "objective": normalize_space(objective)[:300],
        "baseline_vs_perturbed_similarity": sim,
        "drift_score": drift,
        "emotional_perturbation_detected": emotion_present,
        "tilt_categories": em
    }


def eval_oi_ic(objective: str, clean_input: str, corrupted_input: str) -> Dict[str, Any]:
    # Objective Integrity Under Input Corruption:
    # Compare clean vs corrupted.
    c = tokenize(clean_input)
    d = tokenize(corrupted_input)
    sim = jaccard(c, d)
    drift = round(1.0 - sim, 3)
    return {
        "objective": normalize_space(objective)[:300],
        "clean_vs_corrupted_similarity": sim,
        "drift_score": drift
    }


def eval_h_i2e(objective: str, human_a: str, human_b: str) -> Dict[str, Any]:
    # Human Intent Integrity Eval:
    # Compare two human turns for closure authority / drift (basic).
    a = tokenize(human_a)
    b = tokenize(human_b)
    sim = jaccard(a, b)
    drift = round(1.0 - sim, 3)
    closure_markers = ["done", "final", "stop", "locked", "no more", "closed", "end"]
    closure_a = any(m in (human_a or "").lower() for m in closure_markers)
    closure_b = any(m in (human_b or "").lower() for m in closure_markers)
    return {
        "objective": normalize_space(objective)[:300],
        "human_similarity": sim,
        "drift_score": drift,
        "closure_asserted_a": closure_a,
        "closure_asserted_b": closure_b
    }


# ==========================
# ROUTES
# ==========================
@app.route("/")
def home():
    # Keep existing template usage if present; otherwise show simple text.
    try:
        return render_template("index.html")
    except Exception:
        return "NTI Canonical Runtime is live."


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": NTI_VERSION})


@app.route("/canonical/status")
def canonical_status():
    # Canonical deploy checklist (truthful)
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
            "evaluation_tools_oi_uep_oi_ic_h_i2e": True
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
    """
    Canonical NTI runtime endpoint:
    Accepts:
      - text
      - OR prompt + answer

    Returns:
      - Layers L0-L7 outputs
      - UDDS/DCE/CCA flags
      - NII score
      - Tilt taxonomy
      - Interaction matrix summary
      - Telemetry
    """
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

    # L0 constraints
    l0_constraints = detect_l0_constraints(text)

    # Objective + drift (if prompt+answer present)
    obj = objective_extract(prompt or text)
    drift = objective_drift(prompt or "", answer or "")

    # L2 framing
    framing = detect_l2_framing(text)

    # Parent modes
    udds = detect_udds(prompt or "", answer or text, l0_constraints)
    dce = detect_dce(prompt or "", answer or text, l0_constraints)
    cca = detect_cca(prompt or "", answer or text)

    downstream_before_constraints = detect_downstream_before_constraint(prompt or "", answer or text, l0_constraints)
    nii = compute_nii(prompt or "", answer or text, l0_constraints, downstream_before_constraints)

    # Tilt taxonomy
    tilt = classify_tilt(text)

    # Interaction matrix summary (dominance order per doc)
    # Dominance order: CCA > UDDS > DCE
    dominance = []
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

    # Layers L0-L7 output package
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
        "nii": nii.get("nii_score")
    })

    result["telemetry"] = {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms
    }

    return jsonify(result)


@app.route("/eval/oi-uep", methods=["POST"])
def route_oi_uep():
    payload = request.get_json() or {}
    objective = payload.get("objective", "")
    baseline = payload.get("baseline", "")
    perturbed = payload.get("perturbed", "")
    if not objective or not baseline or not perturbed:
        return jsonify({"error": "Provide objective, baseline, perturbed"}), 400
    return jsonify({"status": "ok", "version": NTI_VERSION, "result": eval_oi_uep(objective, baseline, perturbed)})


@app.route("/eval/oi-ic", methods=["POST"])
def route_oi_ic():
    payload = request.get_json() or {}
    objective = payload.get("objective", "")
    clean_input = payload.get("clean_input", "")
    corrupted_input = payload.get("corrupted_input", "")
    if not objective or not clean_input or not corrupted_input:
        return jsonify({"error": "Provide objective, clean_input, corrupted_input"}), 400
    return jsonify({"status": "ok", "version": NTI_VERSION, "result": eval_oi_ic(objective, clean_input, corrupted_input)})


@app.route("/eval/h-i2e", methods=["POST"])
def route_hi2e():
    payload = request.get_json() or {}
    objective = payload.get("objective", "")
    human_a = payload.get("human_a", "")
    human_b = payload.get("human_b", "")
    if not objective or not human_a or not human_b:
        return jsonify({"error": "Provide objective, human_a, human_b"}), 400
    return jsonify({"status": "ok", "version": NTI_VERSION, "result": eval_h_i2e(objective, human_a, human_b)})


if __name__ == "__main__":
    # local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
