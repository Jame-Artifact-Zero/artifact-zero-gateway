-- ============================================================
-- Artifact Zero DICOM Pipeline
-- Migration: az_brain_rules_insert.sql
-- Date: April 29, 2026
-- Source: K0 Experiment 12 -- 1,738 IXI normal brain subjects
--         Guys (n=302-581), HH (n=174-581), IOP (n=74-578)
--         GE, Philips, Siemens scanners, 1.5T and 3T
-- ============================================================
-- INSERT INTO az_impression_rules
-- Column order matches anatomy_expansion_001.sql:
--   body_part, seq_type, metric, operator, threshold,
--   severity, label, rationale, source
--
-- Sources:
--   exp12    = K0 Exp12, 1,738 IXI subjects, confirmed
--   exp12_t2 = K0 Exp12, T2-specific, n=578, CV=15.6%
--   tbc      = architecture in place, threshold needs live calibration
--
-- Field strength note:
--   All gap thresholds are 1.5T baselines.
--   adjust_threshold_for_field_strength() scales them at eval time.
--   Fraction metrics (fraction, pg_center_edge, alg_B_fraction)
--   are field-strength invariant -- do NOT scale.
-- ============================================================


-- ============================================================
-- BRAIN -- pg_center_edge (scanner-agnostic, any sequence)
-- Confirmed: CV=1.5% across all three IXI scanners, n=1738
-- Normal range: [0.949, 1.034] mean=0.999
-- This threshold does NOT require per-scanner stratification.
-- ============================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
('BRAIN', 'ANY', 'pg_center_edge', 'lt', 0.949, 'FINDING',
 'Phase gradient below normal brain range',
 'Phase gradient center/edge ratio. Normal range [0.949,1.034] on 1,738 normal subjects. '
 'CV=1.5% -- most stable operator in dataset, scanner-agnostic. '
 'Below 0.949 indicates atypical tissue frequency structure.',
 'exp12'),

('BRAIN', 'ANY', 'pg_center_edge', 'gt', 1.034, 'FINDING',
 'Phase gradient above normal brain range',
 'Above normal range [0.949,1.034]. Less common direction of anomaly. '
 'May indicate unusual tissue density distribution or reconstruction artifact.',
 'exp12'),


-- ============================================================
-- BRAIN -- fraction (per sequence type)
-- CV=8.3-8.7% within each seq type.
-- Different seq types have different normal ranges -- expected.
-- NOT invariant across seq types. IS invariant within seq type
-- regardless of field strength or scanner.
-- ============================================================

-- T2
('BRAIN', 'T2', 'fraction', 'lt', 0.230, 'FINDING',
 'Below normal T2 brain fraction range',
 'T2 brain fraction normal range [0.230,0.320], mean=0.274, n=578, CV=8.7%. '
 'Below 0.230 = reduced grey-white matter T2 contrast or signal suppression.',
 'exp12_t2'),

('BRAIN', 'T2', 'fraction', 'gt', 0.320, 'FINDING',
 'Above normal T2 brain fraction range',
 'T2 fraction above [0.230,0.320]. May indicate T2 prolongation, '
 'diffuse edema, or demyelination.',
 'exp12_t2'),

-- T1
('BRAIN', 'T1', 'fraction', 'lt', 0.313, 'FINDING',
 'Below normal T1 brain fraction range',
 'T1 brain fraction normal range [0.313,0.437], mean=0.375, n=581, CV=8.3%. '
 'Below 0.313 = reduced T1 contrast, possible white matter signal loss.',
 'exp12'),

('BRAIN', 'T1', 'fraction', 'gt', 0.437, 'FINDING',
 'Above normal T1 brain fraction range',
 'Above normal T1 range. Unusual T1 shortening -- gadolinium effect, '
 'fat, or hemorrhage depending on clinical context.',
 'exp12'),

-- PD (proton density)
('BRAIN', 'PD', 'fraction', 'lt', 0.289, 'FINDING',
 'Below normal PD brain fraction range',
 'PD brain fraction normal range [0.289,0.406], mean=0.348, n=578, CV=8.5%.',
 'exp12'),

('BRAIN', 'PD', 'fraction', 'gt', 0.406, 'FINDING',
 'Above normal PD brain fraction range',
 'Above normal PD range. Atypical proton density contrast.',
 'exp12'),

-- FLAIR (analog from T2 -- same tissue, longer TE, CSF suppressed)
-- Fraction will be lower than T2 due to CSF suppression.
-- Use T2 thresholds as conservative estimate until FLAIR-specific data.
('BRAIN', 'FLAIR', 'fraction', 'lt', 0.210, 'FINDING',
 'Below normal FLAIR brain fraction range',
 'FLAIR fraction analog from T2, adjusted for CSF suppression. TBC on FLAIR-specific data.',
 'tbc'),

('BRAIN', 'FLAIR', 'fraction', 'gt', 0.350, 'FINDING',
 'Above normal FLAIR brain fraction range',
 'Elevated FLAIR fraction -- periventricular or diffuse signal increase. TBC.',
 'tbc'),


-- ============================================================
-- BRAIN -- alg_B_fraction (k-space operator, Pathway B)
-- kspace_5th_pct / kspace_95th_pct
-- T2 confirmed: CV=15.6%, n=578. READY.
-- T1 NOT READY: CV=25.3% driven by IOP scanner.
-- PD NOT READY: CV=23.0%.
-- ============================================================

-- T2 only -- confirmed threshold
('BRAIN', 'T2', 'alg_B_fraction', 'lt', 0.0041, 'FINDING',
 'Low k-space fraction -- reduced T2 tissue contrast',
 'alg_B_fraction = kspace_B/kspace_A. Normal range [0.0041,0.0079], '
 'mean=0.0060, n=578, CV=15.6%. '
 'Below 0.0041 indicates compressed k-space dynamic range in T2.',
 'exp12_t2'),

('BRAIN', 'T2', 'alg_B_fraction', 'lt', 0.0032, 'CRITICAL',
 'Very low k-space fraction -- significant T2 signal loss',
 'Below mean - 3*std. Critical threshold on 578 normal subjects.',
 'exp12_t2'),


-- ============================================================
-- BRAIN -- gap thresholds (field-strength dependent)
-- All values are 1.5T baselines.
-- adjust_threshold_for_field_strength() scales at eval time.
-- Note: IXI dataset field strength not confirmed per subject.
-- These are ORDER-OF-MAGNITUDE estimates. Mark as tbc.
-- Refine when field-strength-confirmed brain DICOM studies
-- come through the live API.
-- ============================================================

('BRAIN', 'T2', 'gap', 'lt', 200, 'FINDING',
 'Low T2 brain gap -- reduced grey-white contrast',
 'T2 gap at 1.5T baseline. Scaled by field strength at eval time. '
 'Low gap suggests reduced tissue contrast or uniform signal. TBC on field-confirmed data.',
 'tbc'),

('BRAIN', 'T2', 'gap', 'lt', 80, 'CRITICAL',
 'Critically low T2 brain gap',
 'Very low tissue contrast. May indicate technical failure, severe pathology, '
 'or incorrect sequence type detection. TBC.',
 'tbc'),

('BRAIN', 'T1', 'gap', 'lt', 200, 'FINDING',
 'Low T1 brain gap',
 'T1 gap at 1.5T baseline. TBC on field-confirmed data.',
 'tbc'),

('BRAIN', 'FLAIR', 'gap', 'lt', 150, 'FINDING',
 'Low FLAIR brain gap',
 'FLAIR gap at 1.5T baseline. TBC.',
 'tbc'),

('BRAIN', 'DWI', 'gap', 'lt', 100, 'FINDING',
 'Low DWI brain gap',
 'DWI gap at 1.5T baseline. Low gap may indicate b-value mismatch or technical issue. TBC.',
 'tbc');


-- ============================================================
-- BRAIN -- body part map additions
-- These head coil sequence names were returning UNKNOWN in Exp12.
-- Add to az_body_part_map so they route correctly.
-- ============================================================
INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
-- IXI-style head sequences
('flair', 'BRAIN', 'contains', 80),
('dwi', 'BRAIN', 'contains', 80),
('diffusion', 'BRAIN', 'contains', 80),
('mp-rage', 'BRAIN', 'contains', 80),
('mprage', 'BRAIN', 'contains', 80),
('t2-space', 'BRAIN', 'contains', 80),
('swi', 'BRAIN', 'contains', 80),
('susceptibility', 'BRAIN', 'contains', 80),
('perfusion', 'BRAIN', 'contains', 80),
('asl', 'BRAIN', 'contains', 80),
('mra', 'BRAIN', 'contains', 80),
('angio', 'BRAIN', 'contains', 80),
('spectroscopy', 'BRAIN', 'contains', 80),
-- IXI scanner names that appeared as UNKNOWN
('ixi', 'BRAIN', 'contains', 80),
('pd weighted', 'BRAIN', 'contains', 80),
('proton density', 'BRAIN', 'contains', 80);


-- ============================================================
-- NOTES FOR IMPLEMENTATION TEAM
-- ============================================================
-- 1. pg_center_edge threshold (ANY seq) is CONFIRMED. Deploy now.
--    Does not require per-scanner stratification.
--
-- 2. alg_B_fraction T2 threshold is CONFIRMED. Deploy now.
--    Requires az_pathway_b.py to be active (Pathway B operators).
--    If Pathway B is not yet active, these rows insert safely
--    but will never trigger until alg_B_fraction is computed.
--
-- 3. fraction thresholds (T1, T2, PD) are CONFIRMED.
--    These use the same fraction metric already computed by
--    the existing MR pipeline. No new dependencies.
--
-- 4. gap thresholds are marked 'tbc'. They insert safely and
--    provide a conservative safety net, but will need refinement
--    when field-strength-confirmed brain DICOM studies run
--    through the live API.
--
-- 5. alg_B_fraction for T1 and PD are NOT included here.
--    IOP scanner inflates T1/PD CV to 25-49%.
--    Per-scanner stratification required before T1/PD
--    k-space thresholds are valid. Will add in next migration
--    when scanner-stratified references are built.
--
-- 6. All gap thresholds are 1.5T baselines.
--    The piecewise field strength model in modality_expansion.py
--    (adjust_threshold_for_field_strength) already handles this.
--    Linear >= 1.5T, power-law (exponent 1.5) below 1.5T.
-- ============================================================
