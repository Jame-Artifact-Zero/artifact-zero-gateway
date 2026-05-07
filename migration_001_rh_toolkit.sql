-- migration_001_rh_toolkit.sql
-- Artifact Zero — RH Cryptographic Toolkit
-- Run after deploy: psql $DATABASE_URL -f migration_001_rh_toolkit.sql

CREATE TABLE IF NOT EXISTS az_certificates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_key_id      TEXT NOT NULL,
    param_type      TEXT NOT NULL,
    parameters      JSONB NOT NULL,
    security_bits   NUMERIC(8,2),
    compliant       BOOLEAN NOT NULL,
    assessment      JSONB NOT NULL,
    signature       TEXT NOT NULL,
    proof_doi       TEXT NOT NULL,
    proof_status    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_az_cert_api_key  ON az_certificates(api_key_id);
CREATE INDEX IF NOT EXISTS idx_az_cert_created  ON az_certificates(created_at);
CREATE INDEX IF NOT EXISTS idx_az_cert_type     ON az_certificates(param_type);
CREATE INDEX IF NOT EXISTS idx_az_cert_compliant ON az_certificates(compliant);

CREATE TABLE IF NOT EXISTS az_runner_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_key_id      TEXT,
    bit_length      INTEGER NOT NULL,
    is_prime        BOOLEAN NOT NULL,
    deterministic   BOOLEAN NOT NULL,
    within_table    BOOLEAN NOT NULL,
    total_ms        NUMERIC(10,3) NOT NULL,
    cert_hash       TEXT NOT NULL,
    witnesses_used  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_az_runner_created    ON az_runner_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_az_runner_bit_length ON az_runner_logs(bit_length);
