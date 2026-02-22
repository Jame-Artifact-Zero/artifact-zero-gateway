"""
db.py — Artifact Zero Database Abstraction Layer
=================================================
Works with SQLite (local dev) or PostgreSQL (production).
Set DATABASE_URL env var for Postgres. If not set, falls back to SQLite.

Usage:
    from db import get_conn, release_conn, db_init

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM requests WHERE id = %s", (rid,))
    # NOTE: Use %s for Postgres, ? for SQLite — use param_placeholder()
    release_conn(conn)
"""

import os
import sqlite3
from contextlib import contextmanager

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_canonical.db")

# Detect which database to use
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import pool

    # Fix Render's postgres:// → postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    # Connection pool — min 1, max 10 connections
    _pool = pool.ThreadedConnectionPool(1, 10, DATABASE_URL)

    def get_conn():
        conn = _pool.getconn()
        conn.autocommit = False
        # Use RealDictCursor so rows come back as dicts
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        return conn

    def release_conn(conn):
        _pool.putconn(conn)

    def _execute_ddl(conn, sql):
        """Execute DDL with Postgres-compatible syntax."""
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()

else:
    # SQLite fallback for local development
    def get_conn():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def release_conn(conn):
        conn.close()

    def _execute_ddl(conn, sql):
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()


@contextmanager
def db_connection():
    """Context manager for database connections.
    
    Usage:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(...)
            conn.commit()
    """
    conn = get_conn()
    try:
        yield conn
    finally:
        release_conn(conn)


def db_init():
    """Initialize all core tables. Safe to call multiple times."""
    conn = get_conn()
    cur = conn.cursor()

    if USE_POSTGRES:
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
            request_id TEXT PRIMARY KEY REFERENCES requests(id),
            version TEXT NOT NULL,
            result_json TEXT NOT NULL
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

        # Indexes for search performance
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_name ON events(event_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_requests_route ON requests(route)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session_id)")

    else:
        # SQLite
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
    release_conn(conn)
    print(f"[DB] Initialized ({'PostgreSQL' if USE_POSTGRES else 'SQLite'})")


def init_loop4_tables():
    """Create Loop 4+5 tables: relay audit, pipeline config, vulnerability disclosures, provider scores.
    Safe to call multiple times (CREATE IF NOT EXISTS)."""
    conn = get_conn()
    cur = conn.cursor()

    # MPI-001: Relay audit log
    cur.execute("""
    CREATE TABLE IF NOT EXISTS relay_audit_log (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL,
        user_id TEXT,
        original_input TEXT NOT NULL,
        modified_output TEXT NOT NULL,
        rules_fired TEXT NOT NULL DEFAULT '[]',
        delta_json TEXT NOT NULL DEFAULT '{}',
        nti_score_json TEXT NOT NULL DEFAULT '{}',
        provider TEXT,
        relay_latency_ms INTEGER,
        created_at TEXT NOT NULL
    )
    """)

    # MPI-003: Relay pipeline config (per-org relay behavior)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS relay_pipeline_config (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL UNIQUE,
        udds_enabled INTEGER NOT NULL DEFAULT 1,
        dce_enabled INTEGER NOT NULL DEFAULT 1,
        cca_enabled INTEGER NOT NULL DEFAULT 1,
        modification_mode TEXT NOT NULL DEFAULT 'flag_only',
        severity_threshold TEXT NOT NULL DEFAULT 'medium',
        phi_detection INTEGER NOT NULL DEFAULT 0,
        sandbox_strip_pii INTEGER NOT NULL DEFAULT 1,
        custom_rules_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # MPA-001: Vulnerability disclosure registry
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vulnerability_disclosures (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'medium',
        title TEXT NOT NULL,
        description TEXT,
        cve_id TEXT,
        bugcrowd_id TEXT,
        discovered_at TEXT NOT NULL,
        reported_at TEXT,
        acknowledged_at TEXT,
        resolved_at TEXT,
        customer_notification_sent INTEGER NOT NULL DEFAULT 0,
        customer_notification_at TEXT,
        affected_orgs_json TEXT DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'discovered',
        disclosure_url TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # MPA-003: Provider risk scores
    cur.execute("""
    CREATE TABLE IF NOT EXISTS provider_risk_scores (
        id TEXT PRIMARY KEY,
        provider TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        risk_score INTEGER NOT NULL DEFAULT 50,
        total_disclosures INTEGER NOT NULL DEFAULT 0,
        open_disclosures INTEGER NOT NULL DEFAULT 0,
        last_disclosure_at TEXT,
        notes TEXT,
        updated_at TEXT NOT NULL
    )
    """)

    # MPI-002: Usage meters for billing
    cur.execute("""
    CREATE TABLE IF NOT EXISTS usage_meters (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL,
        meter_type TEXT NOT NULL,
        quantity INTEGER NOT NULL DEFAULT 0,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    # MPA-005: Runbooks
    cur.execute("""
    CREATE TABLE IF NOT EXISTS runbooks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        trigger_condition TEXT,
        steps_json TEXT NOT NULL DEFAULT '[]',
        last_executed_at TEXT
    )
    """)

    # Indexes (skip failures silently — already exist or not supported)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_relay_audit_org ON relay_audit_log(org_id)",
        "CREATE INDEX IF NOT EXISTS idx_relay_audit_created ON relay_audit_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_vuln_provider ON vulnerability_disclosures(provider)",
        "CREATE INDEX IF NOT EXISTS idx_vuln_status ON vulnerability_disclosures(status)",
        "CREATE INDEX IF NOT EXISTS idx_usage_org ON usage_meters(org_id)",
    ]:
        try:
            cur.execute(idx_sql)
        except Exception:
            pass

    conn.commit()
    release_conn(conn)
    print("[DB] Loop 4+5 tables initialized")


def seed_loop4_data():
    """Seed initial data for Loop 4+5 tables. Idempotent."""
    p = param_placeholder()
    conn = get_conn()
    cur = conn.cursor()

    # Seed OpenAI vulnerability disclosure
    try:
        cur.execute(f"""
        INSERT INTO vulnerability_disclosures (id, provider, severity, title, description, bugcrowd_id, discovered_at, reported_at, status, created_at)
        VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p})
        """, (
            'vuln-001', 'openai', 'high',
            'Cross-Project Context Synthesis Without User Consent',
            'ChatGPT synthesizes context across project boundaries without user consent or disclosure.',
            'bbd51865-295b-4111-b203-14e834e1cab1',
            '2026-02-19T00:00:00Z', '2026-02-19T20:04:00Z', 'reported',
            '2026-02-19T00:00:00Z'
        ))
    except Exception:
        conn.rollback()
        conn = get_conn()
        cur = conn.cursor()

    # Seed provider risk scores
    providers = [
        ('prov-openai', 'openai', 'OpenAI (GPT-4, GPT-4o)', 65, 1, 1, '2026-02-19T00:00:00Z'),
        ('prov-anthropic', 'anthropic', 'Anthropic (Claude)', 30, 0, 0, None),
        ('prov-google', 'google', 'Google (Gemini)', 40, 0, 0, None),
        ('prov-xai', 'xai', 'xAI (Grok)', 50, 0, 0, None),
    ]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for pid, prov, dname, score, total, opendisc, last in providers:
        try:
            cur.execute(f"""
            INSERT INTO provider_risk_scores (id, provider, display_name, risk_score, total_disclosures, open_disclosures, last_disclosure_at, updated_at)
            VALUES ({p},{p},{p},{p},{p},{p},{p},{p})
            """, (pid, prov, dname, score, total, opendisc, last, now))
        except Exception:
            conn.rollback()
            conn = get_conn()
            cur = conn.cursor()

    # Seed disclosure runbook
    import json
    steps = json.dumps([
        {"step": 1, "action": "Document vulnerability with reproduction steps, timestamps, affected scope."},
        {"step": 2, "action": "Insert record into vulnerability_disclosures with status=discovered."},
        {"step": 3, "action": "Assess affected customers: which orgs route traffic through this provider?"},
        {"step": 4, "action": "File disclosure with provider (Bugcrowd, HackerOne, or direct security@)."},
        {"step": 5, "action": "Update disclosure record: status=reported, reported_at=now."},
        {"step": 6, "action": "If severity=critical: route affected org traffic to alternate provider."},
        {"step": 7, "action": "Generate customer notifications within 4 hours of discovery."},
        {"step": 8, "action": "Update disclosure: customer_notification_sent=true."},
        {"step": 9, "action": "Monitor provider response. Follow up at 30, 60, 90 days."},
        {"step": 10, "action": "After resolution: update status, update provider_risk_scores."},
    ])
    try:
        cur.execute(f"""
        INSERT INTO runbooks (id, title, trigger_condition, steps_json)
        VALUES ({p},{p},{p},{p})
        """, (
            'runbook-disclosure',
            'Upstream Vulnerability Discovered',
            'Relay detects anomalous output patterns OR manual security review identifies provider vulnerability',
            steps
        ))
    except Exception:
        conn.rollback()
        conn = get_conn()
        cur = conn.cursor()

    conn.commit()
    release_conn(conn)
    print("[DB] Loop 4+5 seed data loaded")


def upsert_sql(table, columns, conflict_col="id"):
    """Generate an upsert statement that works on both Postgres and SQLite."""
    placeholders = ", ".join(["%s" if USE_POSTGRES else "?" for _ in columns])
    cols = ", ".join(columns)

    if USE_POSTGRES:
        update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in columns if c != conflict_col])
        return f"""
            INSERT INTO {table} ({cols}) VALUES ({placeholders})
            ON CONFLICT ({conflict_col}) DO UPDATE SET {update_set}
        """
    else:
        return f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"


def param_placeholder():
    """Returns the correct placeholder for the current database."""
    return "%s" if USE_POSTGRES else "?"


def like_param(value):
    """Wraps a value for LIKE queries."""
    return f"%{value}%"
