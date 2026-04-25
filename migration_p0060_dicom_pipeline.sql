-- ================================================================================
-- ARTIFACT ZERO LABS — DICOM Pipeline Schema
-- Migration: p0041_dicom_pipeline
-- Pattern: follows existing migration_p0040_relay_sessions.sql structure
-- ================================================================================

BEGIN;

-- ── CUSTOMER PROFILES ────────────────────────────────────────────────────────
-- Links to existing api_keys table via api_key_id FK.
-- All auth is handled by the existing require_api_key decorator.
-- This table holds DICOM-specific profile data only.
CREATE TABLE IF NOT EXISTS az_customer_profiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id          TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    customer_name       TEXT NOT NULL,
    contact_name        TEXT,
    contact_email       TEXT,
    storage_default     TEXT NOT NULL DEFAULT 'none'
                        CHECK (storage_default IN ('none','session','local','encrypted','customer','full')),
    analysis_default    TEXT NOT NULL DEFAULT 'full'
                        CHECK (analysis_default IN ('speed','profile','standard','full','impression','longitudinal')),
    encrypt_public_key  TEXT,
    webhook_url         TEXT,
    webhook_secret      TEXT,
    baa_signed          BOOLEAN NOT NULL DEFAULT FALSE,
    baa_signed_date     TIMESTAMPTZ,
    tier                TEXT NOT NULL DEFAULT 'standard'
                        CHECK (tier IN ('free','standard','enterprise')),
    studies_count       INTEGER NOT NULL DEFAULT 0,
    last_call_at        TIMESTAMPTZ,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(api_key_id)
);

-- ── SCANNERS ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS az_scanners (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    manufacturer        TEXT,
    model_name          TEXT,
    device_serial       TEXT,
    software_version    TEXT,
    field_strength      NUMERIC(4,2),
    institution_name    TEXT,
    station_name        TEXT,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(manufacturer, model_name, device_serial)
);

-- ── STUDIES ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS az_studies (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         TEXT NOT NULL REFERENCES api_keys(id),
    customer_study_ref  TEXT,
    study_instance_uid  TEXT,
    study_date          DATE,
    study_description   TEXT,
    accession_number    TEXT,
    institution_name    TEXT,
    body_part           TEXT,
    modality            TEXT,
    scanner_id          UUID REFERENCES az_scanners(id),

    -- Patient (de-identified — hash only unless customer opted into full storage)
    patient_hash        TEXT,
    patient_age         TEXT,
    patient_sex         TEXT,

    -- Request parameters
    storage_mode        TEXT NOT NULL DEFAULT 'none'
                        CHECK (storage_mode IN ('none','session','local','encrypted','customer','full')),
    analysis_level      TEXT NOT NULL DEFAULT 'full'
                        CHECK (analysis_level IN ('speed','profile','standard','full','impression','longitudinal')),
    return_format       TEXT NOT NULL DEFAULT 'json'
                        CHECK (return_format IN ('json','fhir','dicom_sr','webhook')),
    payload_encrypted   BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_url         TEXT,

    -- Results summary
    sequences_found     INTEGER,
    sequences_processed INTEGER,
    flags_critical      INTEGER NOT NULL DEFAULT 0,
    flags_moderate      INTEGER NOT NULL DEFAULT 0,
    flags_finding       INTEGER NOT NULL DEFAULT 0,
    flags_normal        INTEGER NOT NULL DEFAULT 0,
    impression_status   TEXT CHECK (impression_status IN ('CRITICAL','MODERATE','FINDING','NORMAL','CLEAN','PENDING')),

    -- Performance
    response_ms         INTEGER,
    pipeline_ms         INTEGER,
    storage_ms          INTEGER,
    return_ms           INTEGER,

    -- Return tracking
    returned_via        TEXT,
    webhook_delivered   BOOLEAN,
    webhook_status_code INTEGER,
    webhook_attempts    INTEGER NOT NULL DEFAULT 0,

    -- Storage
    measurements_stored BOOLEAN NOT NULL DEFAULT FALSE,
    expires_at          TIMESTAMPTZ,
    called_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

-- ── SEQUENCES ─────────────────────────────────────────────────────────────────
-- prior_sequence_id implements S₀ at the sequence level.
-- When analysis_level='longitudinal', each sequence links to the corresponding
-- sequence in the prior study. The prior sequence measurements ARE S₀.
-- This is O = f(Q, S₀) applied to imaging — same principle as AZ-PAT-003.
-- NULL when no longitudinal comparison was run for this sequence.
CREATE TABLE IF NOT EXISTS az_sequences (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    study_id            UUID NOT NULL REFERENCES az_studies(id) ON DELETE CASCADE,
    scanner_id          UUID REFERENCES az_scanners(id),
    prior_sequence_id   UUID REFERENCES az_sequences(id),  -- S0 link

    series_instance_uid TEXT,
    series_description  TEXT,
    seq_type            TEXT,
    orientation         TEXT,
    n_slices            INTEGER,

    -- Acquisition parameters
    slice_thickness_mm  NUMERIC(6,3),
    pixel_spacing_row   NUMERIC(8,4),
    pixel_spacing_col   NUMERIC(8,4),
    rows                INTEGER,
    cols                INTEGER,
    flip_angle          NUMERIC(6,2),
    repetition_time_ms  NUMERIC(10,2),
    echo_time_ms        NUMERIC(8,3),
    inversion_time_ms   NUMERIC(8,2),
    field_strength      NUMERIC(4,2),
    scanning_sequence   TEXT,
    image_type          TEXT,
    image_orientation   JSONB,
    image_position_first JSONB,
    image_position_last  JSONB,
    slice_location_first NUMERIC(10,4),
    slice_location_last  NUMERIC(10,4),

    -- Computed measurements
    ref_A               NUMERIC(10,4),
    ref_B               NUMERIC(10,4),
    gap                 NUMERIC(10,4),
    mean_fraction       NUMERIC(8,6),
    std_fraction        NUMERIC(8,6),
    min_gap             NUMERIC(10,4),
    min_gap_slice       INTEGER,
    min_gap_frac_inf    NUMERIC(6,4),
    max_gap             NUMERIC(10,4),
    compression_pct     NUMERIC(6,2),
    gap_cv              NUMERIC(6,2),
    rms_vs_standard     NUMERIC(12,8),
    speedup_x           NUMERIC(12,2),
    timing_ms           NUMERIC(10,3),

    -- Asymmetry
    peak_left_asym      NUMERIC(8,4),
    peak_left_slice     INTEGER,
    peak_left_frac_inf  NUMERIC(6,4),
    pct_left_dominant   NUMERIC(6,2),
    pct_right_dominant  NUMERIC(6,2),

    -- Width
    n_compress_runs     INTEGER,
    run_widths_mm       JSONB,
    run_slice_ranges    JSONB,

    -- Agreement
    peak_disagree_score NUMERIC(8,4),
    peak_disagree_slice INTEGER,
    mean_disagree_score NUMERIC(8,4),

    -- Impression
    flags_json          JSONB,
    impression_text     TEXT,
    flag_critical       INTEGER NOT NULL DEFAULT 0,
    flag_moderate       INTEGER NOT NULL DEFAULT 0,
    flag_finding        INTEGER NOT NULL DEFAULT 0,
    flag_normal         INTEGER NOT NULL DEFAULT 0,

    -- Status
    profile_run         BOOLEAN NOT NULL DEFAULT FALSE,
    asymmetry_run       BOOLEAN NOT NULL DEFAULT FALSE,
    width_run           BOOLEAN NOT NULL DEFAULT FALSE,
    agreement_run       BOOLEAN NOT NULL DEFAULT FALSE,
    impression_run      BOOLEAN NOT NULL DEFAULT FALSE
);

-- ── SLICES ────────────────────────────────────────────────────────────────────
-- Only created when storage_mode = 'local' or 'full'
CREATE TABLE IF NOT EXISTS az_slices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sequence_id         UUID NOT NULL REFERENCES az_sequences(id) ON DELETE CASCADE,
    slice_z             INTEGER NOT NULL,
    slice_frac_inf      NUMERIC(6,4),
    slice_position_mm   NUMERIC(10,4),
    gap                 NUMERIC(10,4),
    ref_A               NUMERIC(10,4),
    ref_B               NUMERIC(10,4),
    fraction            NUMERIC(8,6),
    left_gap            NUMERIC(10,4),
    right_gap           NUMERIC(10,4),
    asym_index          NUMERIC(8,4),
    norm_gap            NUMERIC(8,4),
    disagree_score      NUMERIC(8,4),
    n_voxels            INTEGER,
    UNIQUE(sequence_id, slice_z)
);

-- ── AUDIT LOG ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS az_audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    customer_id         TEXT REFERENCES api_keys(id),
    study_id            UUID REFERENCES az_studies(id),
    event_type          TEXT NOT NULL,
    event_detail        JSONB,
    ip_address          TEXT,
    user_agent          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── WEBHOOK QUEUE ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS az_webhook_queue (
    id                  BIGSERIAL PRIMARY KEY,
    study_id            UUID NOT NULL REFERENCES az_studies(id),
    customer_id         TEXT NOT NULL REFERENCES api_keys(id),
    webhook_url         TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    payload_encrypted   BOOLEAN NOT NULL DEFAULT FALSE,
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 5,
    last_attempt_at     TIMESTAMPTZ,
    last_status_code    INTEGER,
    delivered           BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_attempt_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── INDICES ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_studies_customer    ON az_studies(customer_id);
CREATE INDEX IF NOT EXISTS idx_studies_called_at   ON az_studies(called_at);
CREATE INDEX IF NOT EXISTS idx_studies_status      ON az_studies(impression_status);
CREATE INDEX IF NOT EXISTS idx_studies_uid         ON az_studies(study_instance_uid);
CREATE INDEX IF NOT EXISTS idx_sequences_study     ON az_sequences(study_id);
CREATE INDEX IF NOT EXISTS idx_sequences_type      ON az_sequences(seq_type);
CREATE INDEX IF NOT EXISTS idx_slices_sequence     ON az_slices(sequence_id);
CREATE INDEX IF NOT EXISTS idx_slices_gap          ON az_slices(gap);
CREATE INDEX IF NOT EXISTS idx_audit_customer      ON az_audit_log(customer_id);
CREATE INDEX IF NOT EXISTS idx_audit_created       ON az_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_next        ON az_webhook_queue(next_attempt_at) WHERE NOT delivered;

-- ── UPDATED_AT TRIGGER ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER az_customer_profiles_updated_at
    BEFORE UPDATE ON az_customer_profiles
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

COMMIT;
