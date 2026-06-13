"""
Artifact Zero — PostgreSQL database layer.

DATABASE_URL is required. PostgreSQL connection failures propagate so the
ECS task exits and can be restarted. No alternate database backend is used.
"""
import logging
import os

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required")

if not DATABASE_URL.startswith(("postgresql://", "postgres://")):
    raise RuntimeError("DATABASE_URL must use PostgreSQL")

print("[db] Testing PostgreSQL connection...", flush=True)
_test_conn = psycopg2.connect(
    DATABASE_URL,
    connect_timeout=5,
)
_test_conn.close()
print("[db] PostgreSQL connection successful", flush=True)


def db_connect():
    """Return a PostgreSQL database connection."""
    conn = psycopg2.connect(DATABASE_URL)
    return conn


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
    return "%s"


def db_execute(conn, sql, params=None):
    cur = conn.cursor()
    cur.execute(sql, params or ())
    return cur


def db_init():
    """Create PostgreSQL tables if they do not exist."""
    conn = db_connect()
    cur = conn.cursor()
    locked = False

    try:
        cur.execute("SELECT pg_advisory_lock(hashtext('artifact_zero_db_init'))")
        locked = True
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
        cur.execute("""
                    DO $$ BEGIN
                        ALTER TABLE api_keys ADD COLUMN owner_user_id TEXT;
                    EXCEPTION WHEN duplicate_column THEN NULL;
                    END $$;
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
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                name TEXT NOT NULL DEFAULT '',
                owner_user_id TEXT,
                plan TEXT NOT NULL DEFAULT 'free',
                active BOOLEAN NOT NULL DEFAULT TRUE,
                stripe_customer_id TEXT
            )
            """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id                TEXT PRIMARY KEY,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                email             TEXT NOT NULL UNIQUE,
                password_hash     TEXT,
                role              TEXT NOT NULL DEFAULT 'user',
                active            BOOLEAN NOT NULL DEFAULT TRUE,
                account_id        TEXT,
                email_verified_at TIMESTAMPTZ,
                last_login_at     TIMESTAMPTZ,
                login_count       INTEGER NOT NULL DEFAULT 0
            )
            """)
        for col, defn in [
            ("account_id",        "TEXT"),
            ("email_verified_at", "TIMESTAMPTZ"),
            ("last_login_at",     "TIMESTAMPTZ"),
            ("login_count",       "INTEGER NOT NULL DEFAULT 0"),
        ]:
            cur.execute(f"""
                        DO $$ BEGIN
                            ALTER TABLE users ADD COLUMN {col} {defn};
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;
                    """)
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
        for col, defn in [
            ("account_id",   "TEXT"),
            ("name",         "TEXT NOT NULL DEFAULT ''"),
            ("key_type",     "TEXT NOT NULL DEFAULT 'live'"),
            ("last_used_at", "TIMESTAMPTZ"),
            ("expires_at",   "TIMESTAMPTZ"),
            ("revoked_at",   "TIMESTAMPTZ"),
            ("usage_count",  "INTEGER NOT NULL DEFAULT 0"),
        ]:
            cur.execute(f"""
                        DO $$ BEGIN
                            ALTER TABLE api_keys ADD COLUMN {col} {defn};
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;
                    """)
        for col, defn in [
            ("user_id",    "TEXT"),
            ("account_id", "TEXT"),
            ("key_type",   "TEXT"),
        ]:
            cur.execute(f"""
                        DO $$ BEGIN
                            ALTER TABLE api_usage ADD COLUMN {col} {defn};
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;
                    """)
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

        cur.execute("""
            DO $$ BEGIN
                ALTER TABLE webhooks ADD COLUMN secret_encrypted TEXT;
            EXCEPTION WHEN duplicate_column THEN NULL;
            END $$;
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_webhooks_account ON webhooks(account_id)")
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS credit_transactions (
                id                  TEXT PRIMARY KEY,
                user_id             TEXT NOT NULL REFERENCES users(id),
                type                TEXT NOT NULL,
                amount_cents        INTEGER NOT NULL,
                balance_after_cents INTEGER NOT NULL,
                description         TEXT,
                stripe_session_id   TEXT,
                api_key_id          TEXT,
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                account_id          TEXT,
                key_type            TEXT NOT NULL DEFAULT 'live'
            )
            """)

        for col, defn in [
            ("account_id", "TEXT"),
            ("key_type",   "TEXT NOT NULL DEFAULT 'live'"),
        ]:
            cur.execute(f"""
                        DO $$ BEGIN
                            ALTER TABLE credit_transactions ADD COLUMN {col} {defn};
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;
                    """)
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

        # ── OPERATOR MEMORY ──────────────────────────────────────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS operator_context (
                id          TEXT PRIMARY KEY,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                blob_json   TEXT NOT NULL,
                source      TEXT DEFAULT 'auto',
                summary     TEXT
            )
            """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS operator_sessions (
                id            TEXT PRIMARY KEY,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                messages_json TEXT,
                response_json TEXT,
                jos_json      TEXT,
                summary       TEXT
            )
            """)


        conn.commit()
        logger.info("Database initialized")

    except Exception:
        conn.rollback()
        raise

    finally:
        if locked:
            try:
                cur.execute("SELECT pg_advisory_unlock(hashtext('artifact_zero_db_init'))")
                conn.commit()
            except Exception:
                conn.rollback()
        conn.close()

def init_loop4_tables():
    """Stub — loop4 table initialization. No-op until loop4 schema is defined."""
    logger.info("init_loop4_tables called (stub, no-op)")


def init_loop5_tables():
    """Stub — loop5 table initialization. No-op until loop5 schema is defined."""
    logger.info("init_loop5_tables called (stub, no-op)")


def record_request(request_id, route, ip, user_agent, session_id, latency_ms, payload_json, error=None):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
            INSERT INTO requests (id, created_at, route, ip, user_agent, session_id, latency_ms, payload_json, error)
            VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET latency_ms=EXCLUDED.latency_ms, error=EXCLUDED.error
        """, (request_id, route, ip, user_agent, session_id, latency_ms, payload_json, error))
    conn.commit()
    conn.close()


def record_result(request_id, version, result_json):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
            INSERT INTO results (request_id, version, result_json)
            VALUES (%s, %s, %s)
            ON CONFLICT (request_id) DO UPDATE SET result_json=EXCLUDED.result_json
        """, (request_id, version, result_json))
    conn.commit()
    conn.close()


def record_event(event_id, session_id, event_name, event_json):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
            INSERT INTO events (id, created_at, session_id, event_name, event_json)
            VALUES (%s, NOW(), %s, %s, %s)
        """, (event_id, session_id, event_name, event_json))
    conn.commit()
    conn.close()


def record_api_usage(usage_id, api_key_id, endpoint, latency_ms, status_code):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
            INSERT INTO api_usage (id, api_key_id, created_at, endpoint, latency_ms, status_code)
            VALUES (%s, %s, NOW(), %s, %s, %s)
        """, (usage_id, api_key_id, endpoint, latency_ms, status_code))
    conn.commit()
    conn.close()


def get_api_usage_count(api_key_id, month_start):
    """Count API invocations for a key since month_start."""
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
            SELECT COUNT(*) FROM api_usage
            WHERE api_key_id = %s AND created_at >= %s
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
        val = "%s"
        return str.__new__(cls, val)
    def __call__(self):
        return str(self)

param_placeholder = _ParamPlaceholder()