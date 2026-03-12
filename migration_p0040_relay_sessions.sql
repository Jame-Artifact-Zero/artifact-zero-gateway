-- migration_p0040_relay_sessions.sql
-- p0040: Create relay_sessions table for RDS-backed session persistence.
--
-- Purpose:
--   Stores SimulatedThread state (window counters, relay number, last records)
--   so that session window accumulates correctly across ECS Fargate tasks.
--   Pre-p0040: each container held an independent in-memory session dict.
--   The window counter reset every time a request hit a different task.
--   This table eliminates that split.
--
-- Run against: production RDS PostgreSQL (DATABASE_URL)
-- Run order:   After deploy. Safe to run while service is live.
-- Idempotent:  Yes (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS relay_sessions (
    session_id   TEXT        NOT NULL PRIMARY KEY,
    label        TEXT        NOT NULL DEFAULT '',
    state_json   JSONB       NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for updated_at — supports future TTL cleanup queries
CREATE INDEX IF NOT EXISTS idx_relay_sessions_updated_at
    ON relay_sessions (updated_at);

-- Verify
SELECT
    'relay_sessions table ready' AS status,
    COUNT(*) AS existing_rows
FROM relay_sessions;
