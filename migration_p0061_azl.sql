-- migration_p0061_azl.sql
-- Artifact Zero Labs -- AZL Verification System
-- Run after deploy: psql $DATABASE_URL -f migration_p0061_azl.sql
-- Safe to re-run: uses IF NOT EXISTS throughout

CREATE TABLE IF NOT EXISTS az_azl_certificates (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_key_id  TEXT        NOT NULL,
    cert_type   TEXT        NOT NULL,
    subject     TEXT        NOT NULL,
    result      TEXT        NOT NULL,
    details     JSONB       NOT NULL DEFAULT '{}',
    signature   TEXT        NOT NULL,
    time_ms     NUMERIC(10,3) NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_azl_cert_api_key  ON az_azl_certificates(api_key_id);
CREATE INDEX IF NOT EXISTS idx_azl_cert_created  ON az_azl_certificates(created_at);
CREATE INDEX IF NOT EXISTS idx_azl_cert_type     ON az_azl_certificates(cert_type);
CREATE INDEX IF NOT EXISTS idx_azl_cert_result   ON az_azl_certificates(result);

CREATE TABLE IF NOT EXISTS az_azl_runner_logs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_key_id      TEXT,
    n_bits          INTEGER     NOT NULL,
    is_prime        BOOLEAN     NOT NULL,
    deterministic   BOOLEAN     NOT NULL,
    within_table    BOOLEAN     NOT NULL,
    witnesses_used  INTEGER     NOT NULL,
    witness_source  TEXT        NOT NULL,
    total_ms        NUMERIC(10,3) NOT NULL,
    cert_id         UUID
);

CREATE INDEX IF NOT EXISTS idx_azl_runner_created  ON az_azl_runner_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_azl_runner_bits     ON az_azl_runner_logs(n_bits);
CREATE INDEX IF NOT EXISTS idx_azl_runner_api_key  ON az_azl_runner_logs(api_key_id);
