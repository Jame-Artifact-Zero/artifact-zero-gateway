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

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
import db as database
from psycopg2.extras import RealDictCursor


# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
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


def get_db():
    """Return a canonical PostgreSQL connection."""
    return database.db_connect()



def init_db() -> None:
    """Create the Relay artifact tables and indexes in PostgreSQL."""
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                topic TEXT,
                mode TEXT,
                session_id TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                key TEXT UNIQUE NOT NULL,
                topic TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 2,
                content TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                locked BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS staged_artifacts (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                key TEXT NOT NULL,
                topic TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                content TEXT NOT NULL,
                source_mode TEXT,
                promoted BOOLEAN NOT NULL DEFAULT FALSE
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS injections (
                id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                message_id TEXT NOT NULL,
                artifact_key TEXT NOT NULL,
                topic TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 2,
                mode TEXT
            )
        """)

        cur.execute("""
            DO $$
            BEGIN
                ALTER TABLE artifacts
                ADD COLUMN locked BOOLEAN NOT NULL DEFAULT FALSE;
            EXCEPTION
                WHEN duplicate_column THEN NULL;
            END $$;
        """)

        cur.execute("""
            DO $$
            BEGIN
                ALTER TABLE artifacts
                ADD COLUMN priority INTEGER NOT NULL DEFAULT 2;
            EXCEPTION
                WHEN duplicate_column THEN NULL;
            END $$;
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_artifacts_topic
            ON artifacts(topic)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_artifacts_priority
            ON artifacts(priority)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_topic
            ON messages(topic)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_staged_promoted
            ON staged_artifacts(promoted)
        """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
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
def store_message(
    role: str,
    content: str,
    session_id: str = "default",
) -> dict:
    msg_id = hashlib.sha256(
        f"{utc_now()}{content[:50]}".encode()
    ).hexdigest()[:16]

    topic = classify_topic(content)
    mode = detect_mode(content) if role == "user" else None

    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO messages (
                id,
                created_at,
                role,
                content,
                topic,
                mode,
                session_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            msg_id,
            utc_now(),
            role,
            content,
            topic,
            mode,
            session_id,
        ))

        conn.commit()

        return {
            "id": msg_id,
            "topic": topic,
            "mode": mode,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()



# ─────────────────────────────────────────────
# ARTIFACT MANAGEMENT
# ─────────────────────────────────────────────
def store_artifact(
    key: str,
    topic: str,
    content: str,
    priority: int = P2,
    source_mode: str = None,
) -> dict:
    """
    Store or update a canonical artifact.

    A locked P0 artifact is never overwritten. Replacement content is
    written to staged_artifacts for explicit promotion.
    """
    priority = max(0, min(3, priority))
    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT
                version,
                priority,
                locked
            FROM artifacts
            WHERE key = %s
            FOR UPDATE
        """, (key,))

        existing = cur.fetchone()

        if existing and existing["locked"]:
            staged_id = hashlib.sha256(
                f"staged_{key}_{utc_now()}".encode()
            ).hexdigest()[:16]

            cur.execute("""
                INSERT INTO staged_artifacts (
                    id,
                    created_at,
                    key,
                    topic,
                    priority,
                    content,
                    source_mode
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (
                staged_id,
                utc_now(),
                key,
                topic,
                priority,
                content,
                source_mode or "manual",
            ))

            conn.commit()

            return {
                "key": key,
                "topic": topic,
                "priority": priority,
                "version": existing["version"],
                "staged": True,
                "message": (
                    "P0 artifact is locked. Content staged "
                    "for manual promotion."
                ),
            }

        version = (
            existing["version"] + 1
            if existing
            else 1
        )

        locked = priority == P0

        if existing:
            cur.execute("""
                UPDATE artifacts
                SET
                    content = %s,
                    updated_at = %s,
                    version = %s,
                    topic = %s,
                    priority = %s,
                    locked = %s
                WHERE key = %s
            """, (
                content,
                utc_now(),
                version,
                topic,
                priority,
                locked,
                key,
            ))

        else:
            artifact_id = hashlib.sha256(
                key.encode()
            ).hexdigest()[:16]

            cur.execute("""
                INSERT INTO artifacts (
                    id,
                    created_at,
                    updated_at,
                    key,
                    topic,
                    priority,
                    content,
                    version,
                    locked
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """, (
                artifact_id,
                utc_now(),
                utc_now(),
                key,
                topic,
                priority,
                content,
                version,
                locked,
            ))

        conn.commit()

        return {
            "key": key,
            "topic": topic,
            "priority": priority,
            "version": version,
            "staged": False,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()



def promote_staged_artifact(staged_id: str) -> dict:
    """
    Promote one staged artifact to the locked canonical P0 record.
    """
    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM staged_artifacts
            WHERE id = %s
              AND promoted = FALSE
            FOR UPDATE
        """, (staged_id,))

        staged = cur.fetchone()

        if not staged:
            conn.rollback()

            return {
                "error": (
                    "staged artifact not found "
                    "or already promoted"
                )
            }

        key = staged["key"]

        cur.execute("""
            SELECT version
            FROM artifacts
            WHERE key = %s
            FOR UPDATE
        """, (key,))

        existing = cur.fetchone()

        version = (
            existing["version"] + 1
            if existing
            else 1
        )

        if existing:
            cur.execute("""
                UPDATE artifacts
                SET
                    content = %s,
                    updated_at = %s,
                    version = %s,
                    locked = TRUE
                WHERE key = %s
            """, (
                staged["content"],
                utc_now(),
                version,
                key,
            ))

        else:
            artifact_id = hashlib.sha256(
                key.encode()
            ).hexdigest()[:16]

            cur.execute("""
                INSERT INTO artifacts (
                    id,
                    created_at,
                    updated_at,
                    key,
                    topic,
                    priority,
                    content,
                    version,
                    locked
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, TRUE
                )
            """, (
                artifact_id,
                utc_now(),
                utc_now(),
                key,
                staged["topic"],
                P0,
                staged["content"],
                version,
            ))

        cur.execute("""
            UPDATE staged_artifacts
            SET promoted = TRUE
            WHERE id = %s
        """, (staged_id,))

        conn.commit()

        return {
            "key": key,
            "version": version,
            "promoted": True,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()



def get_artifact(key: str) -> Optional[dict]:
    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT
                key,
                content,
                priority,
                version,
                locked
            FROM artifacts
            WHERE key = %s
        """, (key,))

        row = cur.fetchone()

        return dict(row) if row else None

    finally:
        conn.close()



def get_staged_artifacts(
    key: str = None,
) -> list:
    """List staged artifacts awaiting promotion."""
    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        if key:
            cur.execute("""
                SELECT *
                FROM staged_artifacts
                WHERE key = %s
                  AND promoted = FALSE
                ORDER BY created_at DESC
            """, (key,))

        else:
            cur.execute("""
                SELECT *
                FROM staged_artifacts
                WHERE promoted = FALSE
                ORDER BY created_at DESC
            """)

        return [
            dict(row)
            for row in cur.fetchall()
        ]

    finally:
        conn.close()



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


def get_artifacts_by_topic(
    topic: str,
    global_fallback: bool = True,
    limit: int = 6,
) -> list:
    """
    Retrieve artifacts by topic with priority and recency weighting.
    """
    safe_limit = max(
        1,
        min(int(limit), 100),
    )

    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT
                key,
                content,
                priority,
                version,
                updated_at
            FROM artifacts
            WHERE topic = %s
            ORDER BY
                priority ASC,
                updated_at DESC
            LIMIT %s
        """, (
            topic,
            safe_limit * 2,
        ))

        results = [
            dict(row)
            for row in cur.fetchall()
        ]

        if (
            global_fallback
            and len(results) < 2
            and topic != "general"
        ):
            cur.execute("""
                SELECT
                    key,
                    content,
                    priority,
                    version,
                    updated_at
                FROM artifacts
                WHERE topic <> %s
                ORDER BY
                    priority ASC,
                    updated_at DESC
                LIMIT %s
            """, (
                topic,
                safe_limit,
            ))

            results.extend(
                dict(row)
                for row in cur.fetchall()
            )

    finally:
        conn.close()

    for result in results:
        result["_score"] = (
            PRIORITY_WEIGHT.get(
                result["priority"],
                0,
            )
            + _recency_score(
                result.get("updated_at", "")
            )
        )

    results.sort(
        key=lambda item: item["_score"],
        reverse=True,
    )

    return results[:safe_limit]



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
def build_injected_prompt(
    user_message: str,
    session_id: str = "default",
) -> dict:
    """
    Assemble the deterministic Relay prompt and log injections.
    """
    topic = classify_topic(user_message)
    mode = detect_mode(user_message)

    msg_meta = store_message(
        "user",
        user_message,
        session_id,
    )

    artifacts = get_artifacts_by_topic(topic)
    recent = get_recent_messages(
        session_id,
        limit=6,
    )

    system_block = (
        f"[SYSTEM]\n"
        f"MODE: {mode}\n"
        f"{MODE_INSTRUCTIONS.get(mode, '')}\n"
    )

    artifacts_block = ""
    injected_keys = []

    if artifacts:
        artifacts_block = "\n[ARTIFACTS]\n"

        for priority in (
            P0,
            P1,
            P2,
            P3,
        ):
            group = [
                artifact
                for artifact in artifacts
                if artifact["priority"] == priority
            ]

            if not group:
                continue

            artifacts_block += (
                f"\n<!-- "
                f"{PRIORITY_LABELS[priority]} "
                f"-->\n"
            )

            for artifact in group[:2]:
                artifacts_block += (
                    wrap_artifact(artifact)
                    + "\n"
                )

                injected_keys.append({
                    "key": artifact["key"],
                    "priority": priority,
                })

    context_block = ""

    if len(recent) > 1:
        context_block = "\n[CONTEXT]\n"

        for message in recent[:-1]:
            label = (
                "USER"
                if message["role"] == "user"
                else "ASSISTANT"
            )

            context_block += (
                f"{label}: "
                f"{message['content'][:300]}\n"
            )

    user_block = (
        f"\n[USER]\n"
        f"{user_message}"
    )

    prompt = (
        system_block
        + artifacts_block
        + context_block
        + user_block
    )

    if injected_keys:
        conn = get_db()

        try:
            cur = conn.cursor()

            for item in injected_keys:
                injection_id = hashlib.sha256(
                    (
                        f"{msg_meta['id']}"
                        f"{item['key']}"
                    ).encode()
                ).hexdigest()[:16]

                cur.execute("""
                    INSERT INTO injections (
                        id,
                        created_at,
                        message_id,
                        artifact_key,
                        topic,
                        priority,
                        mode
                    )
                    VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                """, (
                    injection_id,
                    utc_now(),
                    msg_meta["id"],
                    item["key"],
                    topic,
                    item["priority"],
                    mode,
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
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
def get_recent_messages(
    session_id: str = "default",
    limit: int = 20,
) -> list:
    safe_limit = max(
        1,
        min(int(limit), 200),
    )

    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT
                role,
                content
            FROM messages
            WHERE session_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (
            session_id,
            safe_limit,
        ))

        rows = [
            dict(row)
            for row in cur.fetchall()
        ]

    finally:
        conn.close()

    return list(reversed(rows))



def search_messages(
    query: str,
    topic: Optional[str] = None,
    limit: int = 10,
) -> list:
    safe_limit = max(
        1,
        min(int(limit), 200),
    )

    query_pattern = f"%{query}%"
    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        if topic:
            cur.execute("""
                SELECT
                    role,
                    content,
                    topic,
                    created_at
                FROM messages
                WHERE topic = %s
                  AND content ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (
                topic,
                query_pattern,
                safe_limit,
            ))

        else:
            cur.execute("""
                SELECT
                    role,
                    content,
                    topic,
                    created_at
                FROM messages
                WHERE content ILIKE %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (
                query_pattern,
                safe_limit,
            ))

        return [
            dict(row)
            for row in cur.fetchall()
        ]

    finally:
        conn.close()



# ─────────────────────────────────────────────
# INIT ON IMPORT
# ─────────────────────────────────────────────
init_db()
