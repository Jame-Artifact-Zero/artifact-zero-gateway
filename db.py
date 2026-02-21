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
    # NOTE: Always use %s placeholders, even for SQLite (we patch it)
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
