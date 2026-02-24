import os
import re
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, request, jsonify, render_template
import db as database

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.getenv("AZ_SECRET", "dev-fallback-secret-change-me"))

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

# ============================================================
# CANONICAL NTI RUNTIME v2.0 (RULE-BASED, NO LLM DEPENDENCY)
#
# v2.0 single monolithic revision includes:
# - New tilt clusters: T4, T5, T9, T10
# - Broadened DCE markers (soft deferral)
# - NII q3 penalizes: boundary absence + new structural tilt clusters
# - No changes to layer schema, dominance order, or UDDS count logic
# ============================================================
NTI_VERSION = "canonical-nti-v2.0"


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

    # Structural risk categories that should reduce integrity (q3)
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


@app.route("/relay")
def relay_redirect():
    from flask import redirect
    return redirect("/app/relay", code=301)



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

    return jsonify({
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {"q1": nii.get("q1"), "q2": nii.get("q2"), "q3": nii.get("q3"), "q4": nii.get("q4")}
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
    })


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

    result["telemetry"] = {
        "request_id": request_id,
        "session_id": session_id,
        "latency_ms": latency_ms
    }

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
        monthly_limit = row[2] if database.USE_PG else row["monthly_limit"]
        active = row[3] if database.USE_PG else row["active"]

        if not active:
            return jsonify({"error": "API key deactivated"}), 403

        usage_count = database.get_api_usage_count(key_id, _month_start())
        if usage_count >= monthly_limit:
            return jsonify({"error": "Monthly limit reached", "usage": usage_count, "limit": monthly_limit, "tier": tier}), 429

        tier_config = _TIER_LIMITS.get(tier, _TIER_LIMITS["free"])
        cache_key = f"{key_id}:{_minute_key()}"
        current_rpm = _rate_cache.get(cache_key, 0)
        if current_rpm >= tier_config["rpm"]:
            return jsonify({"error": "Rate limit exceeded", "limit": f"{tier_config['rpm']} req/min"}), 429
        _rate_cache[cache_key] = current_rpm + 1

        request._api_key_id = key_id
        request._api_tier = tier
        request._api_usage = usage_count + 1
        request._api_limit = monthly_limit
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

    return jsonify({
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {"q1": nii.get("q1"), "q2": nii.get("q2"), "q3": nii.get("q3"), "q4": nii.get("q4")}
        },
        "failure_modes": {
            "UDDS": udds["udds_state"], "DCE": dce["dce_state"], "CCA": cca["cca_state"],
            "dominance": dominance
        },
        "tilt": {"tags": tilt, "count": len(tilt)},
        "meta": {
            "latency_ms": latency_ms, "text_length": len(text), "word_count": len(text.split()),
            "tier": request._api_tier, "usage_this_month": request._api_usage, "monthly_limit": request._api_limit
        }
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
    return jsonify({
        "api_key": request._api_key_id[:8] + "...",
        "tier": request._api_tier,
        "usage_this_month": usage_count,
        "monthly_limit": request._api_limit,
        "remaining": max(0, request._api_limit - usage_count)
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
