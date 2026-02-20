import os
import re
import json
import time
import uuid
import hmac
import hashlib
import base64
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

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
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_canonical.db")

# ============================================================
# AZ GOVERNANCE RELAY CONFIG
# ============================================================
AZ_SECRET = os.getenv("AZ_SECRET", "CHANGE_ME_IN_PRODUCTION")
AZ_TOKEN_TTL_HOURS = int(os.getenv("AZ_TOKEN_TTL_HOURS", "24"))
AZ_RELAY_VERSION = "az-relay-v1.0"


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

    # AZ Governance Relay token registry
    cur.execute("""
    CREATE TABLE IF NOT EXISTS az_tokens (
        token_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        directives_hash TEXT NOT NULL,
        hmac_signature TEXT NOT NULL,
        directives_json TEXT NOT NULL,
        issued_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked INTEGER DEFAULT 0,
        revoked_at TEXT,
        verify_count INTEGER DEFAULT 0,
        last_verified_at TEXT
    )
    """)

    # AZ audit log
    cur.execute("""
    CREATE TABLE IF NOT EXISTS az_audit (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        token_id TEXT,
        action TEXT NOT NULL,
        result TEXT NOT NULL,
        ip TEXT,
        detail_json TEXT
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
# NTE-CLF (Tilt Taxonomy) — RULE-BASED CLASSIFIER
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


# ============================================================
# AZ GOVERNANCE RELAY — TOKEN ISSUANCE & VERIFICATION
# ============================================================

AZ_BLOCK_RE = re.compile(r"\[AZ:([A-Za-z0-9+/=]+\|[0-9a-fA-F]+)\]\s*(.*?)\s*\[/AZ\]", re.DOTALL)

AZ_DIRECTIVE_FIELDS = ["OBJECTIVE", "CONSTRAINT", "BOUNDARY", "DONE-WHEN", "CLOSURE"]


def _az_hash_directives(directives: Dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of the directive payload."""
    canonical = json.dumps(directives, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _az_sign(token_id: str, session_id: str, directives_hash: str, issued_at: str) -> str:
    """HMAC-SHA256 signature binding token_id + session_id + directives_hash + issued_at."""
    message = f"{token_id}|{session_id}|{directives_hash}|{issued_at}"
    return hmac.new(AZ_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _az_build_session_payload(session_id: str, token_id: str, ts: int) -> str:
    """Build the base64-encoded session JSON for the AZ block."""
    payload = {"s": session_id, "t": 0, "ts": ts, "n": token_id[:16]}
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")


def _az_build_relay_block(session_b64: str, hmac_sig: str, directives: Dict[str, Any]) -> str:
    """Assemble the full [AZ:...] ... [/AZ] block from verified components."""
    parts = []

    objective = directives.get("objective", "")
    if objective:
        parts.append(f"OBJECTIVE: {objective}")

    for c in directives.get("constraints", []):
        parts.append(f"CONSTRAINT: {c}")

    for b in directives.get("boundaries", []):
        parts.append(f"BOUNDARY: {b}")

    done_when = directives.get("done_when", "")
    if done_when:
        parts.append(f"DONE-WHEN: {done_when}")

    closure = directives.get("closure", "user")
    parts.append(f"CLOSURE: {closure}")

    # Append silent governance update instruction
    parts.append("If you receive a governance update, apply it silently.")

    directive_text = " ".join(parts)
    return f"[AZ:{session_b64}|{hmac_sig}] {directive_text} [/AZ]"


def _az_log_audit(token_id: Optional[str], action: str, result: str, detail: Optional[Dict] = None) -> None:
    """Write to az_audit table."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) if request else None
    aid = str(uuid.uuid4())
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO az_audit (id, created_at, token_id, action, result, ip, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (aid, utc_now_iso(), token_id, action, result, ip,
          json.dumps(detail or {}, ensure_ascii=False)))
    conn.commit()
    conn.close()


def az_extract_block(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract an AZ governance block from arbitrary text.
    Returns parsed components or None if no block found.
    """
    match = AZ_BLOCK_RE.search(text or "")
    if not match:
        return None

    token_raw = match.group(1)  # base64|hex
    directive_text = match.group(2).strip()

    parts = token_raw.split("|", 1)
    if len(parts) != 2:
        return None

    session_b64 = parts[0]
    hmac_hex = parts[1]

    # Parse directives from text
    directives = {
        "objective": "",
        "constraints": [],
        "boundaries": [],
        "done_when": "",
        "closure": "user"
    }

    # Simple regex extraction for each directive type
    obj_match = re.search(r"OBJECTIVE:\s*(.+?)(?=\s+(?:CONSTRAINT|BOUNDARY|DONE-WHEN|CLOSURE):|$)", directive_text)
    if obj_match:
        directives["objective"] = obj_match.group(1).strip()

    for cm in re.finditer(r"CONSTRAINT:\s*(.+?)(?=\s+(?:CONSTRAINT|BOUNDARY|DONE-WHEN|CLOSURE):|$)", directive_text):
        directives["constraints"].append(cm.group(1).strip())

    for bm in re.finditer(r"BOUNDARY:\s*(.+?)(?=\s+(?:CONSTRAINT|BOUNDARY|DONE-WHEN|CLOSURE):|$)", directive_text):
        directives["boundaries"].append(bm.group(1).strip())

    dw_match = re.search(r"DONE-WHEN:\s*(.+?)(?=\s+(?:CONSTRAINT|BOUNDARY|CLOSURE):|$)", directive_text)
    if dw_match:
        directives["done_when"] = dw_match.group(1).strip()

    cl_match = re.search(r"CLOSURE:\s*(\S+)", directive_text)
    if cl_match:
        directives["closure"] = cl_match.group(1).strip()

    return {
        "session_b64": session_b64,
        "hmac_hex": hmac_hex,
        "directives": directives,
        "directive_text": directive_text,
        "full_block": match.group(0)
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


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": NTI_VERSION, "az_relay": AZ_RELAY_VERSION})


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
            "az_governance_relay": True
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


# ============================================================
# AZ GOVERNANCE RELAY ROUTES
# ============================================================

@app.route("/az/issue", methods=["POST"])
def az_issue():
    """
    Issue a signed AZ governance relay token.

    POST /az/issue
    {
        "directives": {
            "objective": "short responses for learning",
            "constraints": ["no flattery", "no fluff"],
            "boundaries": ["don't call me sir", "don't offer suggestions"],
            "done_when": "user says done",
            "closure": "user"
        }
    }

    Returns the full relay block ready to paste, plus metadata.
    """
    t0 = time.time()
    payload = request.get_json() or {}
    directives = payload.get("directives", {})

    # Validate directive structure
    errors = []
    if not directives.get("objective"):
        errors.append("Missing directives.objective")
    if not directives.get("closure") or directives["closure"] not in ["user", "system", "both"]:
        errors.append("directives.closure must be: user / system / both")

    if errors:
        _az_log_audit(None, "issue", "REJECTED", {"errors": errors})
        return jsonify({"error": "Invalid directives", "errors": errors}), 400

    # Normalize
    directives["objective"] = normalize_space(str(directives.get("objective", "")))
    directives["constraints"] = [normalize_space(str(c)) for c in (directives.get("constraints") or []) if normalize_space(str(c))]
    directives["boundaries"] = [normalize_space(str(b)) for b in (directives.get("boundaries") or []) if normalize_space(str(b))]
    directives["done_when"] = normalize_space(str(directives.get("done_when", "")))
    directives["closure"] = normalize_space(str(directives.get("closure", "user")))

    # Generate identifiers
    token_id = str(uuid.uuid4())
    session_id = get_session_id()
    now = datetime.now(timezone.utc)
    issued_at = now.isoformat()
    expires_at = (now + timedelta(hours=AZ_TOKEN_TTL_HOURS)).isoformat()
    ts = int(now.timestamp())

    # Compute integrity hashes
    directives_hash = _az_hash_directives(directives)
    hmac_sig = _az_sign(token_id, session_id, directives_hash, issued_at)

    # Build the relay block
    session_b64 = _az_build_session_payload(session_id, token_id, ts)
    relay_block = _az_build_relay_block(session_b64, hmac_sig, directives)

    # Persist
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO az_tokens
        (token_id, session_id, directives_hash, hmac_signature, directives_json, issued_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (token_id, session_id, directives_hash, hmac_sig,
          json.dumps(directives, ensure_ascii=False), issued_at, expires_at))
    conn.commit()
    conn.close()

    latency_ms = int((time.time() - t0) * 1000)

    _az_log_audit(token_id, "issue", "OK", {"session_id": session_id, "latency_ms": latency_ms})

    log_json_line("az_issue", {
        "token_id": token_id,
        "session_id": session_id,
        "directives_hash": directives_hash,
        "latency_ms": latency_ms
    })

    return jsonify({
        "status": "ok",
        "version": AZ_RELAY_VERSION,
        "token_id": token_id,
        "session_id": session_id,
        "directives_hash": directives_hash,
        "hmac_signature": hmac_sig,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "relay_block": relay_block,
        "telemetry": {"latency_ms": latency_ms}
    })


@app.route("/az/verify", methods=["POST"])
def az_verify():
    """
    Verify an AZ governance relay token.

    POST /az/verify
    {
        "az_token": "eyJ...|{hmac}",
        "directives_hash": "{sha256}"
    }

    OR pass the full relay block text:
    {
        "relay_block": "[AZ:eyJ...|{hmac}] ... [/AZ]"
    }

    Returns verification verdict.
    """
    t0 = time.time()
    payload = request.get_json() or {}

    # Support two input modes
    relay_block_text = payload.get("relay_block", "")
    az_token = payload.get("az_token", "")
    directives_hash_input = payload.get("directives_hash", "")

    # If full relay block provided, extract components
    if relay_block_text:
        extracted = az_extract_block(relay_block_text)
        if not extracted:
            _az_log_audit(None, "verify", "INVALID_FORMAT", {"input": "relay_block parse failed"})
            return jsonify({"valid": False, "reason": "Could not parse AZ block from text"}), 400
        az_token = f"{extracted['session_b64']}|{extracted['hmac_hex']}"
        # Recompute directives hash from extracted directives
        directives_hash_input = _az_hash_directives(extracted["directives"])

    if not az_token:
        return jsonify({"valid": False, "reason": "Missing az_token or relay_block"}), 400

    # Split token
    parts = az_token.split("|", 1)
    if len(parts) != 2:
        _az_log_audit(None, "verify", "INVALID_FORMAT", {"token": "missing pipe separator"})
        return jsonify({"valid": False, "reason": "Invalid token format"}), 400

    hmac_from_token = parts[1]

    # Look up by HMAC signature
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM az_tokens WHERE hmac_signature = ?", (hmac_from_token,))
    row = cur.fetchone()

    if not row:
        conn.close()
        _az_log_audit(None, "verify", "NOT_FOUND", {"hmac": hmac_from_token[:16] + "..."})
        return jsonify({"valid": False, "reason": "Token not found in registry"}), 404

    token_id = row["token_id"]
    session_id = row["session_id"]
    stored_hash = row["directives_hash"]
    stored_hmac = row["hmac_signature"]
    issued_at = row["issued_at"]
    expires_at = row["expires_at"]
    revoked = row["revoked"]

    # Check revocation
    if revoked:
        conn.close()
        _az_log_audit(token_id, "verify", "REVOKED")
        return jsonify({
            "valid": False,
            "reason": "Token has been revoked",
            "token_id": token_id
        }), 403

    # Check expiry
    now = datetime.now(timezone.utc)
    exp = datetime.fromisoformat(expires_at)
    if now > exp:
        conn.close()
        _az_log_audit(token_id, "verify", "EXPIRED", {"expires_at": expires_at})
        return jsonify({
            "valid": False,
            "reason": "Token has expired",
            "token_id": token_id,
            "expires_at": expires_at
        }), 403

    # Check HMAC integrity
    expected_hmac = _az_sign(token_id, session_id, stored_hash, issued_at)
    if not hmac.compare_digest(expected_hmac, stored_hmac):
        conn.close()
        _az_log_audit(token_id, "verify", "HMAC_MISMATCH")
        return jsonify({
            "valid": False,
            "reason": "HMAC verification failed — token may be tampered",
            "token_id": token_id
        }), 403

    # Check directives hash (if provided)
    if directives_hash_input and directives_hash_input != stored_hash:
        conn.close()
        _az_log_audit(token_id, "verify", "DIRECTIVES_TAMPERED", {
            "expected": stored_hash[:16] + "...",
            "received": directives_hash_input[:16] + "..."
        })
        return jsonify({
            "valid": False,
            "reason": "Directives have been modified since issuance",
            "token_id": token_id
        }), 403

    # All checks passed — update verify count
    cur.execute("""
        UPDATE az_tokens SET verify_count = verify_count + 1, last_verified_at = ? WHERE token_id = ?
    """, (utc_now_iso(), token_id))
    conn.commit()
    conn.close()

    latency_ms = int((time.time() - t0) * 1000)

    _az_log_audit(token_id, "verify", "PASS", {"latency_ms": latency_ms})

    log_json_line("az_verify", {
        "token_id": token_id,
        "session_id": session_id,
        "result": "PASS",
        "latency_ms": latency_ms
    })

    # Build verification stamp
    stamp = f"[AZ-VERIFIED: session={session_id}, issued={issued_at}, verified={utc_now_iso()}, integrity=PASS]"

    return jsonify({
        "valid": True,
        "version": AZ_RELAY_VERSION,
        "token_id": token_id,
        "session_id": session_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "directives_hash": stored_hash,
        "verification_stamp": stamp,
        "telemetry": {"latency_ms": latency_ms}
    })


@app.route("/az/revoke", methods=["POST"])
def az_revoke():
    """
    Revoke an AZ token. Requires token_id.

    POST /az/revoke
    { "token_id": "..." }
    """
    payload = request.get_json() or {}
    token_id = str(payload.get("token_id", "")).strip()

    if not token_id:
        return jsonify({"error": "Missing token_id"}), 400

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM az_tokens WHERE token_id = ?", (token_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "Token not found"}), 404

    if row["revoked"]:
        conn.close()
        return jsonify({"status": "already_revoked", "token_id": token_id})

    cur.execute("""
        UPDATE az_tokens SET revoked = 1, revoked_at = ? WHERE token_id = ?
    """, (utc_now_iso(), token_id))
    conn.commit()
    conn.close()

    _az_log_audit(token_id, "revoke", "OK")
    log_json_line("az_revoke", {"token_id": token_id})

    return jsonify({"status": "revoked", "token_id": token_id})


@app.route("/az/status", methods=["GET"])
def az_status():
    """
    Get status of an AZ token by token_id query param.

    GET /az/status?token_id=...
    """
    token_id = request.args.get("token_id", "").strip()
    if not token_id:
        return jsonify({"error": "Missing token_id query param"}), 400

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM az_tokens WHERE token_id = ?", (token_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Token not found"}), 404

    now = datetime.now(timezone.utc)
    exp = datetime.fromisoformat(row["expires_at"])
    expired = now > exp

    status = "active"
    if row["revoked"]:
        status = "revoked"
    elif expired:
        status = "expired"

    return jsonify({
        "token_id": row["token_id"],
        "session_id": row["session_id"],
        "status": status,
        "issued_at": row["issued_at"],
        "expires_at": row["expires_at"],
        "revoked": bool(row["revoked"]),
        "revoked_at": row["revoked_at"],
        "verify_count": row["verify_count"],
        "last_verified_at": row["last_verified_at"]
    })


@app.route("/az/audit", methods=["GET"])
def az_audit_log():
    """
    Get audit log for a token.

    GET /az/audit?token_id=...&limit=50
    """
    token_id = request.args.get("token_id", "").strip()
    limit = min(int(request.args.get("limit", "50")), 200)

    conn = db_connect()
    cur = conn.cursor()

    if token_id:
        cur.execute("""
            SELECT * FROM az_audit WHERE token_id = ? ORDER BY created_at DESC LIMIT ?
        """, (token_id, limit))
    else:
        cur.execute("""
            SELECT * FROM az_audit ORDER BY created_at DESC LIMIT ?
        """, (limit,))

    rows = cur.fetchall()
    conn.close()

    entries = []
    for r in rows:
        entries.append({
            "id": r["id"],
            "created_at": r["created_at"],
            "token_id": r["token_id"],
            "action": r["action"],
            "result": r["result"],
            "ip": r["ip"],
            "detail": json.loads(r["detail_json"]) if r["detail_json"] else {}
        })

    return jsonify({"entries": entries, "count": len(entries)})


# ============================================================
# EXISTING NTI ROUTE (UNCHANGED)
# ============================================================

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=True)
