# relay_artifacts.py
# Renamed from relay_memory.py — static artifact store (P0-P3 injection, canonical definitions).
# For thread continuity / infinite memory, see: relay_session.py + gateway.py

"""
RELAY MEMORY SYSTEM v3
Product: Thread

Changes from v2:
    - Artifact XML wrapping: <artifact key="..." priority="P0" version="3">...</artifact>
    - P0 lock: P0 artifacts cannot be overwritten by RETRAIN; new version staged, manual promotion required
    - Recency weighting in retrieval: score = priority_weight + recency_weight (priority dominates)
    - P3 (CONTEXT) always below all artifact tiers in prompt assembly
"""

import os
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DB_PATH = os.getenv("RELAY_DB_PATH", "/tmp/relay_memory.db")

P0 = 0  # Deterministic procedures — locked, cannot be overwritten by RETRAIN
P1 = 1  # Canonical system definitions
P2 = 2  # Reference documents
P3 = 3  # Conversation history / prior outputs

PRIORITY_LABELS = {P0: "PROCEDURE", P1: "CANONICAL", P2: "REFERENCE", P3: "HISTORY"}

# Retrieval scoring weights (priority always dominates)
PRIORITY_WEIGHT = {P0: 100, P1: 60, P2: 30, P3: 10}
RECENCY_MAX_BONUS = 20  # max recency bonus — never enough to flip priority tier

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

    # Staged artifacts: RETRAIN outputs for P0 — await manual promotion
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

    # Migrations for older schema
    for migration in [
        "ALTER TABLE artifacts ADD COLUMN locked INTEGER DEFAULT 0",
        "ALTER TABLE artifacts ADD COLUMN priority INTEGER NOT NULL DEFAULT 2",
    ]:
        try:
            cur.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass

    # Indexes — prevent retrieval latency as artifact/message stores grow
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_artifacts_topic    ON artifacts(topic)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_priority ON artifacts(priority)",
        "CREATE INDEX IF NOT EXISTS idx_messages_session   ON messages(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_messages_topic     ON messages(topic)",
        "CREATE INDEX IF NOT EXISTS idx_staged_promoted    ON staged_artifacts(promoted)",
    ]
    for stmt in index_statements:
        try:
            cur.execute(stmt)
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# TOPIC CLASSIFICATION
# ─────────────────────────────────────────────
def classify_topic(text: str) -> str:
    t = text.lower()
    scores = {}
    for topic, signals in TOPIC_MAP.items():
        score = sum(1 for s in signals if s in t)
        if score:
            scores[topic] = score
    return max(scores, key=scores.get) if scores else "general"


# ─────────────────────────────────────────────
# MODE DETECTION — RULE-BASED PRIORITY CHAIN
# ─────────────────────────────────────────────
def detect_mode(text: str) -> str:
    """Priority chain. First match wins. EXPLORE is floor."""
    t = text.lower()

    if any(v in t for v in ["failed", "broken", "not working", "wrong output",
                              "fix the procedure", "correct the template",
                              "update canonical", "encoding error", "bad output"]):
        return "RETRAIN"

    if any(s in t for s in ["what was", "show me", "find the", "what did we",
                              "get the", "recall", "pull up", "retrieve",
                              "what's the", "show the"]):
        return "RETRIEVE"

    if any(v in t for v in ["deploy", "push to", "run this", "execute",
                              "go live", "do it", "proceed", "send it",
                              "release", "migrate", "ship it"]):
        return "EXECUTE"

    if any(s in t for s in ["confirm", "did it work", "check if", "verify",
                              "validate", "is it live", "did that",
                              "test the endpoint", "ping"]):
        return "VERIFY"

    if any(s in t for s in ["write", "build", "create", "code", "generate",
                              "implement", "draft", "make a", "script",
                              "function", "endpoint", "file"]):
        return "BUILD"

    if any(s in t for s in ["plan", "design", "spec", "diagram", "define",
                              "structure", "map out", "architecture for",
                              "how should", "layout"]):
        return "SPECIFY"

    return "EXPLORE"


# ─────────────────────────────────────────────
# MESSAGE STORAGE
# ─────────────────────────────────────────────
def store_message(role: str, content: str, session_id: str = "default") -> dict:
    msg_id = hashlib.sha256(f"{utc_now()}{content[:50]}".encode()).hexdigest()[:16]
    topic = classify_topic(content)
    mode = detect_mode(content) if role == "user" else None

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO messages (id, created_at, role, content, topic, mode, session_id) VALUES (?,?,?,?,?,?,?)",
        (msg_id, utc_now(), role, content, topic, mode, session_id)
    )
    conn.commit()
    conn.close()
    return {"id": msg_id, "topic": topic, "mode": mode}


# ─────────────────────────────────────────────
# ARTIFACT MANAGEMENT
# ─────────────────────────────────────────────
def store_artifact(key: str, topic: str, content: str, priority: int = P2,
                   source_mode: str = None) -> dict:
    """
    Store or update a canonical artifact.

    P0 LOCK: If the existing artifact is P0 (locked), this call stages the content
    instead of overwriting. Manual promotion required via promote_staged_artifact().

    Returns {"key", "topic", "priority", "version", "staged": bool}
    """
    priority = max(0, min(3, priority))
    conn = get_db()
    existing = conn.execute(
        "SELECT version, priority, locked FROM artifacts WHERE key = ?", (key,)
    ).fetchone()

    # P0 lock check
    if existing and existing["locked"] == 1:
        # Stage instead of overwrite
        staged_id = hashlib.sha256(f"staged_{key}_{utc_now()}".encode()).hexdigest()[:16]
        conn.execute(
            "INSERT INTO staged_artifacts (id, created_at, key, topic, priority, content, source_mode) VALUES (?,?,?,?,?,?,?)",
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
            "UPDATE artifacts SET content = ?, updated_at = ?, version = ?, topic = ?, priority = ?, locked = ? WHERE key = ?",
            (content, utc_now(), version, topic, priority, locked, key)
        )
    else:
        artifact_id = hashlib.sha256(key.encode()).hexdigest()[:16]
        conn.execute(
            "INSERT INTO artifacts (id, created_at, updated_at, key, topic, priority, content, version, locked) VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, utc_now(), utc_now(), key, topic, priority, content, version, locked)
        )

    conn.commit()
    conn.close()
    return {"key": key, "topic": topic, "priority": priority, "version": version, "staged": False}


def promote_staged_artifact(staged_id: str) -> dict:
    """
    Manually promote a staged artifact to replace the locked P0.
    This is the only path to update a P0 artifact.
    """
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
        "UPDATE artifacts SET content = ?, updated_at = ?, version = ?, locked = 1 WHERE key = ?",
        (staged["content"], utc_now(), version, key)
    )
    conn.execute(
        "UPDATE staged_artifacts SET promoted = 1 WHERE id = ?", (staged_id,)
    )
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
    """List pending staged artifacts awaiting promotion."""
    conn = get_db()
    if key:
        rows = conn.execute(
            "SELECT * FROM staged_artifacts WHERE key = ? AND promoted = 0 ORDER BY created_at DESC", (key,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM staged_artifacts WHERE promoted = 0 ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────
# RETRIEVAL WITH RECENCY WEIGHTING
# ─────────────────────────────────────────────
def _recency_score(updated_at_iso: str) -> float:
    """
    Returns a recency bonus (0-RECENCY_MAX_BONUS).
    Decays linearly over 30 days. Never enough to flip a priority tier.
    """
    try:
        updated = datetime.fromisoformat(updated_at_iso)
        age_days = (datetime.now(timezone.utc) - updated).days
        age_days = max(0, age_days)
        bonus = RECENCY_MAX_BONUS * max(0, 1 - (age_days / 30))
        return round(bonus, 2)
    except Exception:
        return 0.0


def get_artifacts_by_topic(topic: str, global_fallback: bool = True, limit: int = 6) -> list:
    """
    Topic-scoped retrieval with recency weighting.
    Score = priority_weight + recency_bonus. Priority always dominates.
    Falls back to global if fewer than 2 topic results.
    """
    conn = get_db()

    rows = conn.execute(
        "SELECT key, content, priority, version, updated_at FROM artifacts WHERE topic = ? ORDER BY priority ASC, updated_at DESC LIMIT ?",
        (topic, limit * 2)  # fetch extra for scoring
    ).fetchall()
    results = [dict(r) for r in rows]

    if global_fallback and len(results) < 2 and topic != "general":
        global_rows = conn.execute(
            "SELECT key, content, priority, version, updated_at FROM artifacts WHERE topic != ? ORDER BY priority ASC, updated_at DESC LIMIT ?",
            (topic, limit)
        ).fetchall()
        results += [dict(r) for r in global_rows]

    conn.close()

    # Score and sort
    for r in results:
        r["_score"] = PRIORITY_WEIGHT.get(r["priority"], 0) + _recency_score(r.get("updated_at", ""))

    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:limit]


# ─────────────────────────────────────────────
# ARTIFACT XML WRAPPING
# ─────────────────────────────────────────────
def wrap_artifact(a: dict) -> str:
    """
    Wrap artifact content in XML tags to prevent model blending.
    <artifact key="deploy_procedure" priority="P0" version="3">
    ...content...
    </artifact>
    """
    p_label = f"P{a['priority']}"
    return (
        f'<artifact key="{a["key"]}" priority="{p_label}" version="{a["version"]}">\n'
        f'{a["content"]}\n'
        f'</artifact>'
    )


# ─────────────────────────────────────────────
# DETERMINISTIC PROMPT ASSEMBLY
# ─────────────────────────────────────────────
def build_injected_prompt(user_message: str, session_id: str = "default") -> dict:
    """
    Deterministic prompt assembly v3.

    Structure (fixed order, no exceptions):
        [SYSTEM]     ← mode instruction
        [ARTIFACTS]  ← P0 → P1 → P2 → P3, XML-wrapped
        [CONTEXT]    ← recent history (P3 tier, always below artifacts)
        [USER]       ← user message
    """
    topic = classify_topic(user_message)
    mode = detect_mode(user_message)

    msg_meta = store_message("user", user_message, session_id)

    artifacts = get_artifacts_by_topic(topic)
    recent = get_recent_messages(session_id, limit=6)

    # ── SYSTEM ──
    system_block = f"[SYSTEM]\nMODE: {mode}\n{MODE_INSTRUCTIONS.get(mode, '')}\n"

    # ── ARTIFACTS (P0→P1→P2→P3, XML-wrapped) ──
    artifacts_block = ""
    injected_keys = []

    if artifacts:
        artifacts_block = "\n[ARTIFACTS]\n"
        for p in [P0, P1, P2, P3]:
            group = [a for a in artifacts if a["priority"] == p]
            if group:
                artifacts_block += f"\n<!-- {PRIORITY_LABELS[p]} -->\n"
                for a in group[:2]:
                    artifacts_block += wrap_artifact(a) + "\n"
                    injected_keys.append({"key": a["key"], "priority": p})

    # ── CONTEXT (history — always below artifacts) ──
    context_block = ""
    if len(recent) > 1:
        context_block = "\n[CONTEXT]\n"
        for msg in recent[:-1]:
            label = "USER" if msg["role"] == "user" else "ASSISTANT"
            context_block += f"{label}: {msg['content'][:300]}\n"

    # ── USER ──
    user_block = f"\n[USER]\n{user_message}"

    prompt = system_block + artifacts_block + context_block + user_block

    # Log injections
    if injected_keys:
        conn = get_db()
        for item in injected_keys:
            inj_id = hashlib.sha256(f"{msg_meta['id']}{item['key']}".encode()).hexdigest()[:16]
            conn.execute(
                "INSERT OR IGNORE INTO injections (id, created_at, message_id, artifact_key, topic, priority, mode) VALUES (?,?,?,?,?,?,?)",
                (inj_id, utc_now(), msg_meta["id"], item["key"], topic, item["priority"], mode)
            )
        conn.commit()
        conn.close()

    return {
        "original": user_message,
        "topic": topic,
        "mode": mode,
        "injected_artifacts": injected_keys,
        "prompt": prompt,
    }


# ─────────────────────────────────────────────
# CONVERSATION HISTORY
# ─────────────────────────────────────────────
def get_recent_messages(session_id: str = "default", limit: int = 20) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def search_messages(query: str, topic: Optional[str] = None, limit: int = 10) -> list:
    conn = get_db()
    if topic:
        rows = conn.execute(
            "SELECT role, content, topic, created_at FROM messages WHERE topic = ? AND content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (topic, f"%{query}%", limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT role, content, topic, created_at FROM messages WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit)
        ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"], "topic": r["topic"], "created_at": r["created_at"]} for r in rows]


# ─────────────────────────────────────────────
# INIT ON IMPORT
# ─────────────────────────────────────────────
init_db()
