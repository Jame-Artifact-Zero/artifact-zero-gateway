"""
Artifact Zero — Database Abstraction Layer
Auto-detects DATABASE_URL for PostgreSQL, falls back to SQLite.
"""
import os
import sys
import sqlite3
import logging
import traceback

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_canonical.db")
USE_PG = False

print(f"[db] DATABASE_URL present: {bool(DATABASE_URL)}", flush=True)
if DATABASE_URL:
    print(f"[db] DATABASE_URL starts with: {DATABASE_URL[:20]}...", flush=True)

if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
    try:
        import psycopg2
        import psycopg2.extras
        # Test the connection immediately
        print("[db] psycopg2 imported, testing connection...", flush=True)
        test_conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        test_conn.close()
        USE_PG = True
        print("[db] PostgreSQL connection successful", flush=True)
    except ImportError as e:
        print(f"[db] psycopg2 not installed, falling back to SQLite: {e}", flush=True)
        USE_PG = False
    except Exception as e:
        print(f"[db] PostgreSQL connection FAILED, falling back to SQLite: {e}", flush=True)
        traceback.print_exc()
        USE_PG = False
else:
    print(f"[db] Using SQLite ({DB_PATH})", flush=True)


def db_connect():
    """Return a database connection. PostgreSQL if available, else SQLite."""
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


# V3 compatibility — context manager and placeholder helpers for standalone modules
from contextlib import contextmanager

@contextmanager
def db_connection():
    """Context manager wrapping db_connect. Auto-closes on exit."""
    conn = db_connect()
    try:
        yield conn
    finally:
        conn.close()

def param_placeholder():
    """Return '%s' for PostgreSQL or '?' for SQLite."""
    return "%s" if USE_PG else "?"


def db_execute(conn, sql, params=None):
    """Execute SQL, converting ? placeholders to %s for PostgreSQL."""
    if USE_PG:
        sql = sql.replace("?", "%s")
        sql = sql.replace("INSERT OR REPLACE", "INSERT")
        # Add ON CONFLICT for upsert on PostgreSQL
        # We handle this per-table in db_init
    cur = conn.cursor()
    cur.execute(sql, params or ())
    return cur


def db_init():
    """Create tables if they don't exist. Works on both SQLite and PostgreSQL."""
    try:
        conn = db_connect()
        cur = conn.cursor()
    except Exception as e:
        print(f"[db] db_init connection failed, falling back to SQLite: {e}", flush=True)
        global USE_PG
        USE_PG = False
        conn = db_connect()
        cur = conn.cursor()

    if USE_PG:
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            owner_email TEXT NOT NULL,
            owner_user_id TEXT,
            tier TEXT NOT NULL DEFAULT 'free',
            monthly_limit INTEGER NOT NULL DEFAULT 10,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """)
        # Migration: add owner_user_id if missing
        try:
            cur.execute("ALTER TABLE api_keys ADD COLUMN owner_user_id TEXT")
        except Exception:
            conn.rollback()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id TEXT PRIMARY KEY,
            api_key_id TEXT NOT NULL REFERENCES api_keys(id),
            created_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            latency_ms INTEGER,
            status_code INTEGER
        )
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_api_usage_key_date 
        ON api_usage(api_key_id, created_at)
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS fortune500_scores (
            slug TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            rank INTEGER,
            url TEXT,
            homepage_copy TEXT,
            score_json TEXT,
            nii_score REAL DEFAULT 0,
            issue_count INTEGER DEFAULT 0,
            last_checked TEXT,
            last_changed TEXT
        )
        """)

        # ── ACCOUNTS ────────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            name TEXT NOT NULL DEFAULT '',
            owner_user_id TEXT,
            plan TEXT NOT NULL DEFAULT 'free',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            stripe_customer_id TEXT
        )
        """)

        # ── USERS MIGRATIONS ────────────────────────────────────────────────
        for col, defn in [
            ("account_id",        "TEXT"),
            ("email_verified_at", "TIMESTAMPTZ"),
            ("last_login_at",     "TIMESTAMPTZ"),
            ("login_count",       "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {col} {defn}")
            except Exception:
                conn.rollback()

        # ── LOGIN HISTORY ────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS login_history (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id TEXT NOT NULL REFERENCES users(id),
            ip TEXT,
            user_agent TEXT,
            success BOOLEAN NOT NULL DEFAULT TRUE
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_login_user ON login_history(user_id)")

        # ── API KEYS MIGRATIONS ──────────────────────────────────────────────
        for col, defn in [
            ("account_id",   "TEXT"),
            ("name",         "TEXT NOT NULL DEFAULT ''"),
            ("key_type",     "TEXT NOT NULL DEFAULT 'live'"),
            ("last_used_at", "TIMESTAMPTZ"),
            ("expires_at",   "TIMESTAMPTZ"),
            ("revoked_at",   "TIMESTAMPTZ"),
            ("usage_count",  "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                cur.execute(f"ALTER TABLE api_keys ADD COLUMN {col} {defn}")
            except Exception:
                conn.rollback()

        # ── API USAGE MIGRATIONS ─────────────────────────────────────────────
        for col, defn in [
            ("user_id",    "TEXT"),
            ("account_id", "TEXT"),
            ("key_type",   "TEXT"),
        ]:
            try:
                cur.execute(f"ALTER TABLE api_usage ADD COLUMN {col} {defn}")
            except Exception:
                conn.rollback()

        # ── WEBHOOKS ─────────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            account_id TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(id),
            url TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            events TEXT NOT NULL DEFAULT '[]',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            last_triggered_at TIMESTAMPTZ,
            failure_count INTEGER NOT NULL DEFAULT 0
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_account ON webhooks(account_id)")

        # ── WEBHOOK DELIVERIES ───────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            webhook_id TEXT NOT NULL REFERENCES webhooks(id),
            request_id TEXT,
            payload_json TEXT,
            response_code INTEGER,
            response_body TEXT,
            latency_ms INTEGER,
            success BOOLEAN NOT NULL DEFAULT FALSE,
            retry_count INTEGER NOT NULL DEFAULT 0
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wdel_webhook ON webhook_deliveries(webhook_id)")

        # ── CREDIT TRANSACTIONS MIGRATIONS ───────────────────────────────────
        for col, defn in [
            ("account_id", "TEXT"),
            ("key_type",   "TEXT NOT NULL DEFAULT 'live'"),
        ]:
            try:
                cur.execute(f"ALTER TABLE credit_transactions ADD COLUMN {col} {defn}")
            except Exception:
                conn.rollback()

        # ── SPEND ALERTS ──────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS spend_alerts (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            account_id TEXT NOT NULL,
            threshold_cents INTEGER NOT NULL DEFAULT 100,
            notify_email TEXT NOT NULL,
            last_triggered_at TIMESTAMPTZ,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """)

        # ── AUTO RECHARGE ─────────────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS auto_recharge (
            id TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            account_id TEXT NOT NULL UNIQUE,
            trigger_cents INTEGER NOT NULL DEFAULT 100,
            recharge_cents INTEGER NOT NULL DEFAULT 1000,
            stripe_payment_method_id TEXT,
            active BOOLEAN NOT NULL DEFAULT FALSE,
            last_triggered_at TIMESTAMPTZ
        )
        """)

    else:
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            owner_email TEXT NOT NULL,
            owner_user_id TEXT,
            tier TEXT NOT NULL DEFAULT 'free',
            monthly_limit INTEGER NOT NULL DEFAULT 10,
            active INTEGER NOT NULL DEFAULT 1
        )
        """)
        # Migration: add owner_user_id if missing
        try:
            cur.execute("ALTER TABLE api_keys ADD COLUMN owner_user_id TEXT")
        except Exception:
            pass
        cur.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id TEXT PRIMARY KEY,
            api_key_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            latency_ms INTEGER,
            status_code INTEGER,
            FOREIGN KEY(api_key_id) REFERENCES api_keys(id)
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS fortune500_scores (
            slug TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            rank INTEGER,
            url TEXT,
            homepage_copy TEXT,
            score_json TEXT,
            nii_score REAL DEFAULT 0,
            issue_count INTEGER DEFAULT 0,
            last_checked TEXT,
            last_changed TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT \'\',
            owner_user_id TEXT,
            plan TEXT NOT NULL DEFAULT \'free\',
            active INTEGER NOT NULL DEFAULT 1,
            stripe_customer_id TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS login_history (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            user_id TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            success INTEGER NOT NULL DEFAULT 1
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            account_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            url TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            events TEXT NOT NULL DEFAULT \'[]\',
            active INTEGER NOT NULL DEFAULT 1,
            last_triggered_at TEXT,
            failure_count INTEGER NOT NULL DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            webhook_id TEXT NOT NULL,
            request_id TEXT,
            payload_json TEXT,
            response_code INTEGER,
            response_body TEXT,
            latency_ms INTEGER,
            success INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS spend_alerts (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            account_id TEXT NOT NULL,
            threshold_cents INTEGER NOT NULL DEFAULT 100,
            notify_email TEXT NOT NULL,
            last_triggered_at TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS auto_recharge (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            account_id TEXT NOT NULL UNIQUE,
            trigger_cents INTEGER NOT NULL DEFAULT 100,
            recharge_cents INTEGER NOT NULL DEFAULT 1000,
            stripe_payment_method_id TEXT,
            active INTEGER NOT NULL DEFAULT 0,
            last_triggered_at TEXT
        )
        """)

        for table, col, defn in [
            ("users",               "account_id",        "TEXT"),
            ("users",               "email_verified_at", "TEXT"),
            ("users",               "last_login_at",     "TEXT"),
            ("users",               "login_count",       "INTEGER NOT NULL DEFAULT 0"),
            ("api_keys",            "account_id",        "TEXT"),
            ("api_keys",            "name",              "TEXT NOT NULL DEFAULT \'\'"),
            ("api_keys",            "key_type",          "TEXT NOT NULL DEFAULT \'live\'"),
            ("api_keys",            "last_used_at",      "TEXT"),
            ("api_keys",            "expires_at",        "TEXT"),
            ("api_keys",            "revoked_at",        "TEXT"),
            ("api_keys",            "usage_count",       "INTEGER NOT NULL DEFAULT 0"),
            ("api_usage",           "user_id",           "TEXT"),
            ("api_usage",           "account_id",        "TEXT"),
            ("api_usage",           "key_type",          "TEXT"),
            ("credit_transactions", "account_id",        "TEXT"),
            ("credit_transactions", "key_type",          "TEXT NOT NULL DEFAULT \'live\'"),
        ]:
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            except Exception:
                pass

    conn.commit()
    conn.close()
    logger.info("Database initialized (PG=%s)", USE_PG)


def record_request(request_id, route, ip, user_agent, session_id, latency_ms, payload_json, error=None):
    conn = db_connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            INSERT INTO requests (id, created_at, route, ip, user_agent, session_id, latency_ms, payload_json, error)
            VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET latency_ms=EXCLUDED.latency_ms, error=EXCLUDED.error
        """, (request_id, route, ip, user_agent, session_id, latency_ms, payload_json, error))
    else:
        cur.execute("""
            INSERT OR REPLACE INTO requests (id, created_at, route, ip, user_agent, session_id, latency_ms, payload_json, error)
            VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?)
        """, (request_id, route, ip, user_agent, session_id, latency_ms, payload_json, error))
    conn.commit()
    conn.close()


def record_result(request_id, version, result_json):
    conn = db_connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            INSERT INTO results (request_id, version, result_json)
            VALUES (%s, %s, %s)
            ON CONFLICT (request_id) DO UPDATE SET result_json=EXCLUDED.result_json
        """, (request_id, version, result_json))
    else:
        cur.execute("""
            INSERT OR REPLACE INTO results (request_id, version, result_json)
            VALUES (?, ?, ?)
        """, (request_id, version, result_json))
    conn.commit()
    conn.close()


def record_event(event_id, session_id, event_name, event_json):
    conn = db_connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            INSERT INTO events (id, created_at, session_id, event_name, event_json)
            VALUES (%s, NOW(), %s, %s, %s)
        """, (event_id, session_id, event_name, event_json))
    else:
        cur.execute("""
            INSERT INTO events (id, created_at, session_id, event_name, event_json)
            VALUES (?, datetime('now'), ?, ?, ?)
        """, (event_id, session_id, event_name, event_json))
    conn.commit()
    conn.close()


def record_api_usage(usage_id, api_key_id, endpoint, latency_ms, status_code):
    conn = db_connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            INSERT INTO api_usage (id, api_key_id, created_at, endpoint, latency_ms, status_code)
            VALUES (%s, %s, NOW(), %s, %s, %s)
        """, (usage_id, api_key_id, endpoint, latency_ms, status_code))
    else:
        cur.execute("""
            INSERT INTO api_usage (id, api_key_id, created_at, endpoint, latency_ms, status_code)
            VALUES (?, ?, datetime('now'), ?, ?, ?)
        """, (usage_id, api_key_id, endpoint, latency_ms, status_code))
    conn.commit()
    conn.close()


def get_api_usage_count(api_key_id, month_start):
    """Count API invocations for a key since month_start."""
    conn = db_connect()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            SELECT COUNT(*) FROM api_usage 
            WHERE api_key_id = %s AND created_at >= %s
        """, (api_key_id, month_start))
    else:
        cur.execute("""
            SELECT COUNT(*) FROM api_usage 
            WHERE api_key_id = ? AND created_at >= ?
        """, (api_key_id, month_start))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


# ═══════════════════════════════════════════
# COMPATIBILITY WRAPPERS — used by az_relay.py
# ═══════════════════════════════════════════
def get_conn():
    return db_connect()

def release_conn(conn):
    if conn:
        try:
            conn.close()
        except Exception:
            pass

class _ParamPlaceholder(str):
    """Works as both a string and a callable for compatibility."""
    def __new__(cls):
        val = "%s" if USE_PG else "?"
        return str.__new__(cls, val)
    def __call__(self):
        return str(self)

param_placeholder = _ParamPlaceholder()
