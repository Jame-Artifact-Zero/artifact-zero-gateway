"""
Artifact Zero — Database Abstraction Layer
Auto-detects DATABASE_URL for PostgreSQL, falls back to SQLite.
"""
import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_canonical.db")
USE_PG = False

if DATABASE_URL and DATABASE_URL.startswith("postgresql"):
    try:
        import psycopg2
        import psycopg2.extras
        USE_PG = True
        logger.info("Database: PostgreSQL")
    except ImportError:
        logger.warning("psycopg2 not installed — falling back to SQLite")
        USE_PG = False
else:
    logger.info("Database: SQLite (%s)", DB_PATH)


def db_connect():
    """Return a database connection. PostgreSQL if available, else SQLite."""
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


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
            tier TEXT NOT NULL DEFAULT 'free',
            monthly_limit INTEGER NOT NULL DEFAULT 10,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """)
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
            tier TEXT NOT NULL DEFAULT 'free',
            monthly_limit INTEGER NOT NULL DEFAULT 10,
            active INTEGER NOT NULL DEFAULT 1
        )
        """)
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
