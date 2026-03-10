"""
RELAY MEMORY SYSTEM v4
Product: Thread

Changes from v3:
    1. Dynamic artifact retrieval — limit scales with task complexity (1-12), not fixed at 6
    2. Classification fallback chain — confidence scoring, keyword fallback, domain token overlap,
       global search, explicit flag when nothing matched
    3. Signal-based intent router — uses NTI signal markers instead of keyword matching.
       Natural language routes correctly. Constraint density boosts execution modes.
    4. P0 anchor — seeded at init_db(), locked, injected into every prompt. Tells AI to treat
       artifacts as ground truth and flag unsupported claims.
"""

import os
import re
import sqlite3
import hashlib
from datetime import datetime, timezone
from typing import Optional


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DB_PATH = os.getenv("RELAY_DB_PATH", "/tmp/relay_memory.db")

P0 = 0  # Deterministic procedures — locked
P1 = 1  # Canonical system definitions
P2 = 2  # Reference documents
P3 = 3  # Conversation history / prior outputs

PRIORITY_LABELS   = {P0: "PROCEDURE", P1: "CANONICAL", P2: "REFERENCE", P3: "HISTORY"}
PRIORITY_WEIGHT   = {P0: 100, P1: 60, P2: 30, P3: 10}
RECENCY_MAX_BONUS = 20

CLASSIFICATION_CONFIDENCE_THRESHOLD = 2

# (min_sentences, max_sentences, artifact_limit)
COMPLEXITY_BANDS = [
    (0,   2,  2),
    (3,   5,  4),
    (6,  10,  6),
    (11, 20,  9),
    (21, 999, 12),
]

TOPIC_MAP = {
    "deploy":       ["deploy", "deployment", "push", "release", "ci", "ecs", "fargate", "branch", "pipeline"],
    "nti":          ["nti", "no-tilt", "tilt", "udds", "dce", "cca", "nii", "v2_engine", "v3_engine", "score"],
    "relay":        ["relay", "injection", "memory", "artifact", "retrieval", "thread"],
    "architecture": ["architecture", "stack", "schema", "database", "rds", "postgres", "sqlite"],
    "sales":        ["michael", "title group", "glen", "lerner", "simplelife", "gtm", "outreach", "prospect"],
    "finance":      ["stripe", "revenue", "pricing", "invoice", "credits", "payment"],
    "code":         ["python", "flask", "function", "class", "route", "endpoint", "bug", "error", "fix"],
    "jos":          ["jos", "objective", "constraint", "closure", "done-when", "binding"],
    "scraper":      ["scraper", "fortune 500", "cloudflare", "curl_cffi", "rescrape"],
    "personal":     ["sleep", "health", "inspire", "surgery", "medical"],
}

MODE_INSTRUCTIONS = {
    "RETRIEVE": "Return the requested artifact verbatim. Do not modify, improve, or extend it. Do not offer alternatives.",
    "EXECUTE":  "Return only commands. No explanation. No commentary. Exact sequence only.",
    "BUILD":    "Use the injected procedure template exactly. Fill variables only. Do not change structure. Do not add steps.",
    "VERIFY":   "Compare expected state vs actual state. Return: PASS or FAIL with one-line reason. Nothing else.",
    "RETRAIN":  "The previous procedure failed. Identify the failure point. Return the corrected template only. Note: P0 procedures are locked — output will be staged for manual promotion.",
    "SPECIFY":  "Produce a structured spec: objective, steps, artifacts required, constraints. No code. No implementation.",
    "EXPLORE":  "Reason openly. Analyze the problem. Return options and analysis. No implementation unless asked.",
}

# ─────────────────────────────────────────────
# P0 ANCHOR
# ─────────────────────────────────────────────
P0_ANCHOR_KEY = "system_anchor"
P0_ANCHOR_CONTENT = """ARTIFACT ZERO — SYSTEM ANCHOR (LOCKED PROCEDURE)

Before generating any response, follow this sequence:

1. RETRIEVE FIRST — Check injected artifacts above. If an artifact addresses the query,
   answer from it. Do not answer from general training when an artifact is present.

2. ARTIFACT AUTHORITY — Injected artifacts are ground truth. They override your training.
   If an artifact says X, return X. Do not improve, reframe, or extend artifact content
   unless in BUILD mode.

3. UNSUPPORTED CLAIMS — If you are about to state something not supported by an injected
   artifact, prefix it with [GENERAL] so the operator can identify training-sourced content
   vs artifact-sourced content.

4. DRIFT PREVENTION — If the conversation is moving away from injected artifacts into general
   territory, stop. State: "I am operating outside injected context. Retrieve relevant
   artifacts or continue with [GENERAL] content?" Do not drift silently.

5. MODE COMPLIANCE — Follow the MODE instruction in [SYSTEM] exactly. Do not switch modes
   mid-response.

This anchor is locked. It cannot be modified by RETRAIN. It is injected into every prompt.
"""

# ─────────────────────────────────────────────
# SIGNAL SETS FOR INTENT ROUTER
# ─────────────────────────────────────────────
_INTENT_SIGNALS = [
    (["failed", "broken", "not working", "wrong output", "bad output",
      "fix the procedure", "correct the template", "update canonical",
      "encoding error", "incorrect result", "it broke"], "RETRAIN", 10),

    (["what was", "show me", "find the", "what did we", "get the",
      "recall", "pull up", "retrieve", "what's the", "show the",
      "what is the current", "give me the", "what did we decide",
      "what was decided", "what do we have"], "RETRIEVE", 11),

    (["deploy", "push to", "run this", "execute", "go live", "do it",
      "proceed", "send it", "release", "migrate", "ship it",
      "make it happen", "run it", "kick it off", "fire it",
      "can we push", "push the", "let's push", "go ahead",
      "move forward", "do that now", "that thing", "the fix",
      "take it live"], "EXECUTE", 9),

    (["confirm", "did it work", "check if", "verify", "validate",
      "did that", "test the", "ping", "is it working",
      "did it go", "check the", "is it up", "still working",
      "did that work", "is the endpoint", "is the route",
      "is it running", "is it deployed", "is it live",
      "is it there"], "VERIFY", 9),

    (["write", "build", "create", "code", "generate", "implement",
      "draft", "make a", "script", "function", "endpoint", "file",
      "produce", "output a", "give me a", "need a"], "BUILD", 7),

    (["plan", "design", "spec", "diagram", "define", "structure",
      "map out", "architecture for", "how should", "layout",
      "what should", "outline", "blueprint"], "SPECIFY", 6),
]

_CONSTRAINT_SIGNALS = [
    "must", "cannot", "can't", "won't", "only if", "requires", "required",
    "dependency", "production", "api key", "token", "secret",
    "deadline", "blocking", "critical", "urgent",
]


# ─────────────────────────────────────────────
# DB INIT
# ─────────────────────────────────────────────
def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id          TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        topic       TEXT,
        mode        TEXT,
        session_id  TEXT
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS artifacts (
        id          TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        key         TEXT UNIQUE NOT NULL,
        topic       TEXT NOT NULL,
        priority    INTEGER NOT NULL DEFAULT 2,
        content     TEXT NOT NULL,
        version     INTEGER DEFAULT 1,
        locked      INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS staged_artifacts (
        id          TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL,
        key         TEXT NOT NULL,
        topic       TEXT NOT NULL,
        priority    INTEGER NOT NULL DEFAULT 0,
        content     TEXT NOT NULL,
        source_mode TEXT,
        promoted    INTEGER DEFAULT 0
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS injections (
        id              TEXT PRIMARY KEY,
        created_at      TEXT NOT NULL,
        message_id      TEXT NOT NULL,
        artifact_key    TEXT NOT NULL,
        topic           TEXT NOT NULL,
        priority        INTEGER NOT NULL DEFAULT 2,
        mode            TEXT
    )""")

    for migration in [
        "ALTER TABLE artifacts ADD COLUMN locked INTEGER DEFAULT 0",
        "ALTER TABLE artifacts ADD COLUMN priority INTEGER NOT NULL DEFAULT 2",
    ]:
        try:
            cur.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_artifacts_topic    ON artifacts(topic)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_priority ON artifacts(priority)",
        "CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_topic     ON messages(topic)",
        "CREATE INDEX IF NOT EXISTS idx_staged_promoted    ON staged_artifacts(promoted)",
    ]:
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

    # Seed P0 anchor once
    if not get_artifact(P0_ANCHOR_KEY):
        _seed_anchor()


def _seed_anchor() -> None:
    conn = get_db()
    anchor_id = hashlib.sha256(P0_ANCHOR_KEY.encode()).hexdigest()[:16]
    conn.execute(
        "INSERT OR IGNORE INTO artifacts "
        "(id, created_at, updated_at, key, topic, priority, content, version, locked) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (anchor_id, utc_now(), utc_now(), P0_ANCHOR_KEY,
         "general", P0, P0_ANCHOR_CONTENT, 1, 1)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# TOPIC CLASSIFICATION — FALLBACK CHAIN
# ─────────────────────────────────────────────
def classify_topic(text: str) -> tuple:
    """
    Returns (topic, confidence, method).
    method: "signal" | "keyword" | "domain_token" | "none"

    Fallback chain:
        1. Signal scoring (TOPIC_MAP phrase hits)
        2. Keyword exact match (single-word topics)
        3. Domain token overlap against artifact store
        4. "general" with method="none" — flagged
    """
    t = text.lower()

    # Stage 1: Signal scoring
    scores = {}
    for topic, signals in TOPIC_MAP.items():
        score = sum(1 for s in signals if s in t)
        if score:
            scores[topic] = score

    if scores:
        best = max(scores, key=scores.get)
        if scores[best] >= CLASSIFICATION_CONFIDENCE_THRESHOLD:
            return best, scores[best], "signal"

    # Stage 2: Keyword exact match
    words = set(re.findall(r'\b\w+\b', t))
    for topic in TOPIC_MAP:
        if topic in words:
            return topic, 1, "keyword"

    # Stage 3: Domain token overlap against artifact store
    tokens = [w for w in words if len(w) >= 4]
    if tokens:
        conn = get_db()
        rows = conn.execute(
            "SELECT key, topic, content FROM artifacts ORDER BY priority ASC LIMIT 50"
        ).fetchall()
        conn.close()
        best_topic = None
        best_overlap = 0
        for row in rows:
            artifact_tokens = set(re.findall(r'\b\w{4,}\b', row["content"].lower()))
            overlap = len(set(tokens) & artifact_tokens)
            if overlap > best_overlap:
                best_overlap = overlap
                best_topic = row["topic"]
        if best_topic and best_overlap >= 2:
            return best_topic, best_overlap, "domain_token"

    return "general", 0, "none"


# ─────────────────────────────────────────────
# SIGNAL-BASED INTENT ROUTER
# ─────────────────────────────────────────────
def detect_mode(text: str) -> str:
    """
    Signal-based intent router. Scores all mode signal sets.
    Constraint density boosts execution modes.
    EXPLORE is floor.
    """
    t = text.lower()
    mode_scores = {}

    for signals, mode, weight in _INTENT_SIGNALS:
        hits = sum(1 for s in signals if s in t)
        if hits:
            mode_scores[mode] = mode_scores.get(mode, 0) + (hits * weight)

    constraint_hits = sum(1 for s in _CONSTRAINT_SIGNALS if s in t)
    if constraint_hits >= 2:
        for mode in ("EXECUTE", "RETRAIN"):
            if mode in mode_scores:
                mode_scores[mode] += constraint_hits * 3

    if not mode_scores:
        return "EXPLORE"

    return max(mode_scores, key=mode_scores.get)


# ─────────────────────────────────────────────
# DYNAMIC COMPLEXITY LIMIT
# ─────────────────────────────────────────────
def _complexity_limit(text: str) -> int:
    """
    Score complexity from clause count (sentences + conjunctive clauses).
    Splits on sentence terminals AND clause connectors for long queries.
    """
    clauses = re.split(r'[.!?,;]+|\b(?:and|also|then|plus|as well as)\b', text.strip())
    clause_count = len([c for c in clauses if c.strip()])
    for min_s, max_s, limit in COMPLEXITY_BANDS:
        if min_s <= clause_count <= max_s:
            return limit
    return 6


# ─────────────────────────────────────────────
# MESSAGE STORAGE
# ─────────────────────────────────────────────
def store_message(role: str, content: str, session_id: str = "default") -> dict:
    msg_id = hashlib.sha256(f"{utc_now()}{content[:50]}".encode()).hexdigest()[:16]
    topic, confidence, method = classify_topic(content)
    mode = detect_mode(content) if role == "user" else None

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO messages (id, created_at, role, content, topic, mode, session_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (msg_id, utc_now(), role, content, topic, mode, session_id)
    )
    conn.commit()
    conn.close()
    return {"id": msg_id, "topic": topic, "confidence": confidence,
            "classification_method": method, "mode": mode}


# ─────────────────────────────────────────────
# ARTIFACT MANAGEMENT
# ─────────────────────────────────────────────
def store_artifact(key: str, topic: str, content: str, priority: int = P2,
                   source_mode: str = None) -> dict:
    priority = max(0, min(3, priority))
    conn = get_db()
    existing = conn.execute(
        "SELECT version, priority, locked FROM artifacts WHERE key = ?", (key,)
    ).fetchone()

    if existing and existing["locked"] == 1:
        staged_id = hashlib.sha256(f"staged_{key}_{utc_now()}".encode()).hexdigest()[:16]
        conn.execute(
            "INSERT INTO staged_artifacts "
            "(id, created_at, key, topic, priority, content, source_mode) "
            "VALUES (?,?,?,?,?,?,?)",
            (staged_id, utc_now(), key, topic, priority, content, source_mode or "manual")
        )
        conn.commit()
        conn.close()
        return {"key": key, "topic": topic, "priority": priority,
                "version": existing["version"], "staged": True,
                "message": "P0 artifact is locked. Content staged for manual promotion."}

    version = (existing["version"] + 1) if existing else 1
    locked = 1 if priority == P0 else 0

    if existing:
        conn.execute(
            "UPDATE artifacts SET content=?, updated_at=?, version=?, topic=?, priority=?, locked=? "
            "WHERE key=?",
            (content, utc_now(), version, topic, priority, locked, key)
        )
    else:
        artifact_id = hashlib.sha256(key.encode()).hexdigest()[:16]
        conn.execute(
            "INSERT INTO artifacts "
            "(id, created_at, updated_at, key, topic, priority, content, version, locked) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, utc_now(), utc_now(), key, topic, priority, content, version, locked)
        )

    conn.commit()
    conn.close()
    return {"key": key, "topic": topic, "priority": priority, "version": version, "staged": False}


def promote_staged_artifact(staged_id: str) -> dict:
    conn = get_db()
    staged = conn.execute(
        "SELECT * FROM staged_artifacts WHERE id = ? AND promoted = 0", (staged_id,)
    ).fetchone()

    if not staged:
        conn.close()
        return {"error": "staged artifact not found or already promoted"}

    key = staged["key"]
    existing = conn.execute("SELECT version FROM artifacts WHERE key = ?", (key,)).fetchone()
    version = (existing["version"] + 1) if existing else 1

    conn.execute(
        "UPDATE artifacts SET content=?, updated_at=?, version=?, locked=1 WHERE key=?",
        (staged["content"], utc_now(), version, key)
    )
    conn.execute("UPDATE staged_artifacts SET promoted=1 WHERE id=?", (staged_id,))
    conn.commit()
    conn.close()
    return {"key": key, "version": version, "promoted": True}


def get_artifact(key: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT key, content, priority, version, locked FROM artifacts WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_staged_artifacts(key: str = None) -> list:
    conn = get_db()
    if key:
        rows = conn.execute(
            "SELECT * FROM staged_artifacts WHERE key=? AND promoted=0 ORDER BY created_at DESC",
            (key,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM staged_artifacts WHERE promoted=0 ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# RETRIEVAL — DYNAMIC LIMIT + FALLBACK CHAIN
# ─────────────────────────────────────────────
def _recency_score(updated_at_iso: str) -> float:
    try:
        updated = datetime.fromisoformat(updated_at_iso)
        age_days = max(0, (datetime.now(timezone.utc) - updated).days)
        return round(RECENCY_MAX_BONUS * max(0, 1 - (age_days / 30)), 2)
    except Exception:
        return 0.0


def get_artifacts_by_topic(topic: str, user_message: str = "",
                            global_fallback: bool = True) -> tuple:
    """
    Returns (artifacts, retrieval_meta).
    P0 anchor always included first.
    Limit is dynamically set from task complexity.
    """
    if user_message:
        topic, confidence, method = classify_topic(user_message)
        limit = _complexity_limit(user_message)
    else:
        confidence, method, limit = 0, "direct", 6

    conn = get_db()

    anchor_row = conn.execute(
        "SELECT key, content, priority, version, updated_at FROM artifacts WHERE key=?",
        (P0_ANCHOR_KEY,)
    ).fetchone()

    rows = conn.execute(
        "SELECT key, content, priority, version, updated_at FROM artifacts "
        "WHERE topic=? AND key!=? ORDER BY priority ASC, updated_at DESC LIMIT ?",
        (topic, P0_ANCHOR_KEY, limit * 2)
    ).fetchall()
    results = [dict(r) for r in rows]

    fallback_used = False
    if global_fallback and len(results) < 2 and topic != "general":
        global_rows = conn.execute(
            "SELECT key, content, priority, version, updated_at FROM artifacts "
            "WHERE topic!=? AND key!=? ORDER BY priority ASC, updated_at DESC LIMIT ?",
            (topic, P0_ANCHOR_KEY, limit)
        ).fetchall()
        results += [dict(r) for r in global_rows]
        fallback_used = len(global_rows) > 0

    conn.close()

    for r in results:
        r["_score"] = PRIORITY_WEIGHT.get(r["priority"], 0) + _recency_score(r.get("updated_at", ""))
    results.sort(key=lambda x: x["_score"], reverse=True)
    results = results[:limit]

    if anchor_row:
        anchor = dict(anchor_row)
        results = [anchor] + [r for r in results if r["key"] != P0_ANCHOR_KEY]

    retrieval_meta = {
        "topic": topic,
        "confidence": confidence,
        "classification_method": method,
        "limit_used": limit,
        "fallback_used": fallback_used,
        "artifact_count": len(results),
        "classification_flagged": method == "none",
    }

    return results, retrieval_meta


# ─────────────────────────────────────────────
# ARTIFACT XML WRAPPING
# ─────────────────────────────────────────────
def wrap_artifact(a: dict) -> str:
    p_label = f"P{a['priority']}"
    return (
        f'<artifact key="{a["key"]}" priority="{p_label}" version="{a["version"]}">\n'
        f'{a["content"]}\n'
        f'</artifact>'
    )


# ─────────────────────────────────────────────
# DETERMINISTIC PROMPT ASSEMBLY v4
# ─────────────────────────────────────────────
def build_injected_prompt(user_message: str, session_id: str = "default") -> dict:
    """
    Fixed assembly order:
        [SYSTEM] → [ARTIFACTS P0→P3] → [CONTEXT] → [USER]

    P0 anchor always first in artifacts.
    Classification warning injected if method="none".
    """
    mode = detect_mode(user_message)
    msg_meta = store_message("user", user_message, session_id)

    artifacts, retrieval_meta = get_artifacts_by_topic(
        retrieval_meta["topic"] if (retrieval_meta := None) else "general",
        user_message=user_message
    )
    # Note: retrieval_meta is set correctly inside get_artifacts_by_topic
    artifacts, retrieval_meta = get_artifacts_by_topic("general", user_message=user_message)

    recent = get_recent_messages(session_id, limit=6)

    # ── SYSTEM ──
    system_block = f"[SYSTEM]\nMODE: {mode}\n{MODE_INSTRUCTIONS.get(mode, '')}\n"
    if retrieval_meta.get("classification_flagged"):
        system_block += (
            "\n[WARNING] Topic classification returned no match. "
            "Artifacts may not be relevant. Operator review recommended.\n"
        )

    # ── ARTIFACTS ──
    artifacts_block = ""
    injected_keys = []

    if artifacts:
        artifacts_block = "\n[ARTIFACTS]\n"
        for p in [P0, P1, P2, P3]:
            group = [a for a in artifacts if a["priority"] == p]
            if group:
                artifacts_block += f"\n<!-- {PRIORITY_LABELS[p]} -->\n"
                cap = len(group) if p == P0 else 2
                for a in group[:cap]:
                    artifacts_block += wrap_artifact(a) + "\n"
                    injected_keys.append({"key": a["key"], "priority": p})

    # ── CONTEXT ──
    context_block = ""
    if len(recent) > 1:
        context_block = "\n[CONTEXT]\n"
        for msg in recent[:-1]:
            label = "USER" if msg["role"] == "user" else "ASSISTANT"
            context_block += f"{label}: {msg['content'][:300]}\n"

    # ── USER ──
    user_block = f"\n[USER]\n{user_message}"

    prompt = system_block + artifacts_block + context_block + user_block

    if injected_keys:
        conn = get_db()
        for item in injected_keys:
            inj_id = hashlib.sha256(f"{msg_meta['id']}{item['key']}".encode()).hexdigest()[:16]
            conn.execute(
                "INSERT OR IGNORE INTO injections "
                "(id, created_at, message_id, artifact_key, topic, priority, mode) "
                "VALUES (?,?,?,?,?,?,?)",
                (inj_id, utc_now(), msg_meta["id"], item["key"],
                 retrieval_meta["topic"], item["priority"], mode)
            )
        conn.commit()
        conn.close()

    return {
        "original": user_message,
        "topic": retrieval_meta["topic"],
        "mode": mode,
        "injected_artifacts": injected_keys,
        "retrieval_meta": retrieval_meta,
        "prompt": prompt,
    }


# ─────────────────────────────────────────────
# CONVERSATION HISTORY
# ─────────────────────────────────────────────
def get_recent_messages(session_id: str = "default", limit: int = 20) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def search_messages(query: str, topic: Optional[str] = None, limit: int = 10) -> list:
    conn = get_db()
    if topic:
        rows = conn.execute(
            "SELECT role, content, topic, created_at FROM messages "
            "WHERE topic=? AND content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (topic, f"%{query}%", limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT role, content, topic, created_at FROM messages "
            "WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit)
        ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"],
             "topic": r["topic"], "created_at": r["created_at"]} for r in rows]


# ─────────────────────────────────────────────
# INIT ON IMPORT
# ─────────────────────────────────────────────
init_db()
