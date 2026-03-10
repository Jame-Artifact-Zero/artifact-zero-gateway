-- nti_relay_migration.sql
-- Adds governance_profile column to api_keys table.
-- Safe: uses IF NOT EXISTS / column existence check patterns.
-- Run once against production RDS before deploying relay blueprint.

-- PostgreSQL version (RDS):
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS governance_profile JSONB DEFAULT NULL;

-- SQLite fallback (dev/local only — run manually if needed):
-- SQLite does not support ADD COLUMN IF NOT EXISTS, so check first:
-- ALTER TABLE api_keys ADD COLUMN governance_profile TEXT DEFAULT NULL;

-- Verify:
-- SELECT id, tier, governance_profile FROM api_keys LIMIT 5;
