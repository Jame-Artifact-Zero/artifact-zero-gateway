-- ============================================================================
-- preimpression_rules.sql
-- ============================================================================
-- Migration: insert pre-impression severity rules for 12 body parts into
-- az_impression_rules.
--
-- Conventions:
--   - body_part         : uppercase code matching az_body_part_map and
--                         analyzer.body_part_codes
--   - seq_type          : 'T2', 'T2FS', 'STIR', 'PDFS', 'FLAIR', 'T1POST', 'ANY'
--                         For metrics that are series-aggregate (not per-seq),
--                         use 'ANY' so the rule fires regardless of seq_type.
--   - metric            : key the analyzer writes into the seq dict (or
--                         result.preimpression for series-aggregate values)
--   - operator          : 'lt' for "value below threshold fires"
--                         'gt' for "value above threshold fires"
--   - threshold         : calibrated at 1.5T. az_field_strength.adjust_threshold()
--                         scales it for other field strengths via the
--                         GAP_METRICS / FRACTION_METRICS classification.
--   - severity          : 'CRITICAL' | 'MODERATE' | 'FINDING' | 'NORMAL'
--   - source            : 'lit'      = literature-supported threshold
--                         'observed' = derived from a labeled real study
--                         'tbc'      = first-pass estimate, awaits validation
--                         'analog'   = analogized from a different body part
--                         'code'     = was hardcoded in the analyzer module
--
-- CSPINE rules are sourced 'lit' — these were validated end-to-end on one
-- real radiologist-reported study (left paracentral C5-C6 disc extrusion
-- with severe central canal stenosis; pipeline correctly flagged C5-C6
-- CRITICAL with cord-canal contact). All other body parts are 'tbc' —
-- they will fire flags but the threshold values must be calibrated against
-- real labeled studies before clinical use.
--
-- All rules use ON CONFLICT DO NOTHING so re-running this migration is safe.
--
-- Run order: dev → quality → prod, separately. No code deploy required.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- CERVICAL SPINE
-- ----------------------------------------------------------------------------
-- Cord-canal min space at level (per-level minimum across all axial slices
-- at that vertebral level). Validated on real C-spine study.
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('CSPINE', 'T2', 'space_min_mm', 'lt', 0.5, 'CRITICAL',
     'cord-canal contact at level',
     'Cord touching canal wall — surgical-decompression threshold',
     'lit'),
    ('CSPINE', 'T2', 'space_min_mm', 'lt', 1.5, 'MODERATE',
     'severe canal narrowing at level',
     'Cord-canal min space below severe-stenosis cutoff',
     'lit'),
    ('CSPINE', 'T2', 'space_min_mm', 'lt', 2.5, 'FINDING',
     'mild canal narrowing at level',
     'Cord-canal min space below normal-canal margin',
     'lit'),

-- L/R asymmetry of cord-canal space (per-level mean asym_lr signed
-- (L-R)/(L+R)). Sustained-pattern escalation (≥half slices same direction)
-- handled by analyzer; rule fires on aggregated value.
    ('CSPINE', 'T2', 'asym_lr_abs', 'gt', 0.40, 'CRITICAL',
     'marked L-R asymmetry at level',
     'Sustained lateralized canal narrowing — likely focal pathology',
     'lit'),
    ('CSPINE', 'T2', 'asym_lr_abs', 'gt', 0.20, 'MODERATE',
     'moderate L-R asymmetry at level',
     'Lateralized canal narrowing across multiple slices',
     'lit'),
    ('CSPINE', 'T2', 'asym_lr_abs', 'gt', 0.10, 'FINDING',
     'mild L-R asymmetry at level',
     'Mild consistent lateralization',
     'lit')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- THORACIC SPINE
-- Same metrics as CSPINE, same thresholds (cord-canal anatomy similar).
-- T-spine validation pending — reuses CSPINE-validated thresholds as
-- analog starting point.
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('TSPINE', 'T2', 'space_min_mm',  'lt', 0.5,  'CRITICAL',
     'cord-canal contact at level',
     'Cord touching canal wall',
     'analog'),
    ('TSPINE', 'T2', 'space_min_mm',  'lt', 1.5,  'MODERATE',
     'severe canal narrowing at level',
     'Cord-canal min space below severe-stenosis cutoff',
     'analog'),
    ('TSPINE', 'T2', 'space_min_mm',  'lt', 2.5,  'FINDING',
     'mild canal narrowing at level',
     'Mild canal narrowing',
     'analog'),
    ('TSPINE', 'T2', 'asym_lr_abs',   'gt', 0.40, 'CRITICAL',
     'marked L-R asymmetry at level',
     'Sustained lateralized narrowing',
     'analog'),
    ('TSPINE', 'T2', 'asym_lr_abs',   'gt', 0.20, 'MODERATE',
     'moderate L-R asymmetry at level',
     'Lateralized narrowing across multiple slices',
     'analog'),
    ('TSPINE', 'T2', 'asym_lr_abs',   'gt', 0.10, 'FINDING',
     'mild L-R asymmetry at level',
     'Mild consistent lateralization',
     'analog')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- LUMBAR SPINE
-- Lumbar canal is naturally wider (thecal sac, not cord). Thresholds
-- relaxed accordingly. Pending validation.
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('LSPINE', 'T2', 'space_min_mm',  'lt', 1.0,  'CRITICAL',
     'thecal sac compression at level',
     'Thecal sac compromised against canal wall',
     'tbc'),
    ('LSPINE', 'T2', 'space_min_mm',  'lt', 2.5,  'MODERATE',
     'severe lumbar narrowing at level',
     'Thecal-canal min space below severe cutoff',
     'tbc'),
    ('LSPINE', 'T2', 'space_min_mm',  'lt', 4.0,  'FINDING',
     'mild lumbar narrowing at level',
     'Thecal-canal min space below normal margin',
     'tbc'),
    ('LSPINE', 'T2', 'asym_lr_abs',   'gt', 0.40, 'CRITICAL',
     'marked L-R asymmetry at level',
     'Lateral recess narrowing — likely lateralized pathology',
     'tbc'),
    ('LSPINE', 'T2', 'asym_lr_abs',   'gt', 0.20, 'MODERATE',
     'moderate L-R asymmetry at level',
     'Sustained lateral recess narrowing',
     'tbc'),
    ('LSPINE', 'T2', 'asym_lr_abs',   'gt', 0.10, 'FINDING',
     'mild L-R asymmetry at level',
     'Mild consistent lateral recess narrowing',
     'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- BRAIN
-- Series-aggregate metrics (one value per study). seq_type='ANY' fires
-- on any series the analyzer chose as primary (FLAIR > T2 > T1).
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    -- Midline shift (absolute mm)
    ('BRAIN', 'ANY', 'midline_shift_abs_mm', 'gt', 5.0, 'CRITICAL',
     'marked midline shift',
     'Midline shift ≥5 mm — surgical-decompression consideration',
     'lit'),
    ('BRAIN', 'ANY', 'midline_shift_abs_mm', 'gt', 3.0, 'MODERATE',
     'moderate midline shift',
     'Midline shift 3-5 mm — significant mass effect',
     'lit'),
    ('BRAIN', 'ANY', 'midline_shift_abs_mm', 'gt', 1.0, 'FINDING',
     'mild midline shift',
     'Midline shift 1-3 mm — possible mass effect',
     'lit'),

    -- Ventricular asymmetry (signed, but the analyzer publishes |asym| for
    -- threshold purposes)
    ('BRAIN', 'ANY', 'ventricle_asym_abs', 'gt', 0.30, 'CRITICAL',
     'marked ventricular asymmetry',
     '≥30% L-R lateral ventricle volume difference',
     'tbc'),
    ('BRAIN', 'ANY', 'ventricle_asym_abs', 'gt', 0.20, 'MODERATE',
     'moderate ventricular asymmetry',
     '20-30% L-R lateral ventricle volume difference',
     'tbc'),
    ('BRAIN', 'ANY', 'ventricle_asym_abs', 'gt', 0.10, 'FINDING',
     'mild ventricular asymmetry',
     '10-20% L-R lateral ventricle volume difference',
     'tbc'),

    -- FLAIR lesion count
    ('BRAIN', 'FLAIR', 'flair_lesion_count', 'gt', 30, 'CRITICAL',
     'high FLAIR lesion burden',
     '≥30 hyperintense foci — heavy lesion burden',
     'tbc'),
    ('BRAIN', 'FLAIR', 'flair_lesion_count', 'gt', 15, 'MODERATE',
     'moderate FLAIR lesion burden',
     '15-30 hyperintense foci',
     'tbc'),
    ('BRAIN', 'FLAIR', 'flair_lesion_count', 'gt',  5, 'FINDING',
     'FLAIR lesions present',
     '5-15 hyperintense foci',
     'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- KNEE — joint effusion + bone marrow edema, volumetric
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('KNEE', 'ANY', 'effusion_volume_mm3', 'gt', 30000, 'CRITICAL',
     'large joint effusion',
     'Effusion volume above large-effusion threshold',
     'tbc'),
    ('KNEE', 'ANY', 'effusion_volume_mm3', 'gt', 10000, 'MODERATE',
     'moderate joint effusion',
     'Moderate effusion',
     'tbc'),
    ('KNEE', 'ANY', 'effusion_volume_mm3', 'gt',  3000, 'FINDING',
     'small joint effusion',
     'Trace-to-small effusion',
     'tbc'),

    ('KNEE', 'ANY', 'marrow_edema_volume_mm3', 'gt', 5000, 'CRITICAL',
     'extensive bone marrow edema',
     'Marrow edema volume suggests significant injury or stress reaction',
     'tbc'),
    ('KNEE', 'ANY', 'marrow_edema_volume_mm3', 'gt', 1500, 'MODERATE',
     'moderate bone marrow edema',
     'Moderate marrow edema',
     'tbc'),
    ('KNEE', 'ANY', 'marrow_edema_volume_mm3', 'gt',  300, 'FINDING',
     'focal bone marrow edema',
     'Small marrow edema focus',
     'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- ANKLE
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('ANKLE', 'ANY', 'effusion_volume_mm3', 'gt', 12000, 'CRITICAL',
     'large joint effusion', 'Large ankle effusion', 'tbc'),
    ('ANKLE', 'ANY', 'effusion_volume_mm3', 'gt',  4000, 'MODERATE',
     'moderate joint effusion', 'Moderate ankle effusion', 'tbc'),
    ('ANKLE', 'ANY', 'effusion_volume_mm3', 'gt',  1000, 'FINDING',
     'small joint effusion', 'Trace-to-small ankle effusion', 'tbc'),

    ('ANKLE', 'ANY', 'marrow_edema_volume_mm3', 'gt', 3000, 'CRITICAL',
     'extensive bone marrow edema',
     'Marrow edema — talar dome OCD or significant bone bruise',
     'tbc'),
    ('ANKLE', 'ANY', 'marrow_edema_volume_mm3', 'gt', 1000, 'MODERATE',
     'moderate bone marrow edema', 'Moderate marrow edema', 'tbc'),
    ('ANKLE', 'ANY', 'marrow_edema_volume_mm3', 'gt',  200, 'FINDING',
     'focal bone marrow edema', 'Small marrow edema focus', 'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- FOOT — sensitive marrow edema thresholds (small bones, stress fracture risk)
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('FOOT', 'ANY', 'effusion_volume_mm3', 'gt', 8000, 'CRITICAL',
     'large fluid collection', 'Large fluid collection in foot', 'tbc'),
    ('FOOT', 'ANY', 'effusion_volume_mm3', 'gt', 2500, 'MODERATE',
     'moderate fluid collection', 'Moderate soft tissue fluid', 'tbc'),
    ('FOOT', 'ANY', 'effusion_volume_mm3', 'gt',  700, 'FINDING',
     'small fluid collection', 'Trace-to-small soft tissue fluid', 'tbc'),

    ('FOOT', 'ANY', 'marrow_edema_volume_mm3', 'gt', 2000, 'CRITICAL',
     'extensive bone marrow edema',
     'Significant marrow edema — possible stress fracture',
     'tbc'),
    ('FOOT', 'ANY', 'marrow_edema_volume_mm3', 'gt',  500, 'MODERATE',
     'moderate bone marrow edema',
     'Moderate marrow edema — stress reaction',
     'tbc'),
    ('FOOT', 'ANY', 'marrow_edema_volume_mm3', 'gt',  100, 'FINDING',
     'focal bone marrow edema',
     'Small focus — early stress reaction or sesamoiditis',
     'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- SHOULDER
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('SHOULDER', 'ANY', 'effusion_volume_mm3', 'gt', 15000, 'CRITICAL',
     'large glenohumeral effusion', 'Large effusion or bursal fluid', 'tbc'),
    ('SHOULDER', 'ANY', 'effusion_volume_mm3', 'gt',  5000, 'MODERATE',
     'moderate glenohumeral effusion', 'Moderate effusion', 'tbc'),
    ('SHOULDER', 'ANY', 'effusion_volume_mm3', 'gt',  1500, 'FINDING',
     'small glenohumeral effusion', 'Small effusion or trace bursal fluid', 'tbc'),

    ('SHOULDER', 'ANY', 'marrow_edema_volume_mm3', 'gt', 4000, 'CRITICAL',
     'extensive bone marrow edema',
     'Marrow edema — humeral head, glenoid, or acromion',
     'tbc'),
    ('SHOULDER', 'ANY', 'marrow_edema_volume_mm3', 'gt', 1000, 'MODERATE',
     'moderate bone marrow edema', 'Moderate marrow edema', 'tbc'),
    ('SHOULDER', 'ANY', 'marrow_edema_volume_mm3', 'gt',  250, 'FINDING',
     'focal bone marrow edema', 'Small marrow edema focus', 'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- ELBOW
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('ELBOW', 'ANY', 'effusion_volume_mm3', 'gt', 10000, 'CRITICAL',
     'large joint effusion', 'Large elbow effusion', 'tbc'),
    ('ELBOW', 'ANY', 'effusion_volume_mm3', 'gt',  3000, 'MODERATE',
     'moderate joint effusion', 'Moderate elbow effusion', 'tbc'),
    ('ELBOW', 'ANY', 'effusion_volume_mm3', 'gt',   800, 'FINDING',
     'small joint effusion', 'Trace-to-small elbow effusion', 'tbc'),

    ('ELBOW', 'ANY', 'marrow_edema_volume_mm3', 'gt', 3000, 'CRITICAL',
     'extensive bone marrow edema',
     'Marrow edema — capitellum, olecranon, or distal humerus',
     'tbc'),
    ('ELBOW', 'ANY', 'marrow_edema_volume_mm3', 'gt',  800, 'MODERATE',
     'moderate bone marrow edema', 'Moderate marrow edema', 'tbc'),
    ('ELBOW', 'ANY', 'marrow_edema_volume_mm3', 'gt',  200, 'FINDING',
     'focal bone marrow edema', 'Small marrow edema focus', 'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- WRIST — sensitive marrow thresholds (scaphoid AVN, occult fracture risk)
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('WRIST', 'ANY', 'effusion_volume_mm3', 'gt', 5000, 'CRITICAL',
     'large joint effusion', 'Large wrist effusion', 'tbc'),
    ('WRIST', 'ANY', 'effusion_volume_mm3', 'gt', 1500, 'MODERATE',
     'moderate joint effusion', 'Moderate wrist effusion', 'tbc'),
    ('WRIST', 'ANY', 'effusion_volume_mm3', 'gt',  400, 'FINDING',
     'small joint effusion', 'Trace-to-small wrist effusion', 'tbc'),

    ('WRIST', 'ANY', 'marrow_edema_volume_mm3', 'gt', 1500, 'CRITICAL',
     'extensive bone marrow edema',
     'Marrow edema — scaphoid AVN or occult fracture risk',
     'tbc'),
    ('WRIST', 'ANY', 'marrow_edema_volume_mm3', 'gt',  400, 'MODERATE',
     'moderate bone marrow edema',
     'Moderate marrow edema — possible occult injury',
     'tbc'),
    ('WRIST', 'ANY', 'marrow_edema_volume_mm3', 'gt',   80, 'FINDING',
     'focal bone marrow edema',
     'Small focus — possible early bone stress',
     'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- HAND — most sensitive thresholds (smallest bones)
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('HAND', 'ANY', 'effusion_volume_mm3', 'gt', 3000, 'CRITICAL',
     'large fluid collection', 'Large soft tissue fluid (tenosynovitis pattern)', 'tbc'),
    ('HAND', 'ANY', 'effusion_volume_mm3', 'gt',  800, 'MODERATE',
     'moderate fluid collection', 'Moderate fluid (tenosynovitis or capsulitis)', 'tbc'),
    ('HAND', 'ANY', 'effusion_volume_mm3', 'gt',  200, 'FINDING',
     'small fluid collection', 'Trace fluid', 'tbc'),

    ('HAND', 'ANY', 'marrow_edema_volume_mm3', 'gt',  800, 'CRITICAL',
     'extensive bone marrow edema',
     'Significant marrow edema — phalangeal or metacarpal pathology',
     'tbc'),
    ('HAND', 'ANY', 'marrow_edema_volume_mm3', 'gt',  200, 'MODERATE',
     'moderate bone marrow edema', 'Moderate marrow edema', 'tbc'),
    ('HAND', 'ANY', 'marrow_edema_volume_mm3', 'gt',   50, 'FINDING',
     'focal bone marrow edema',
     'Small focus — early stress or RA-pattern',
     'tbc')
ON CONFLICT DO NOTHING;

-- ----------------------------------------------------------------------------
-- BREAST — paired anatomy. Mass diameter (largest detected in any phase).
-- T1POST is the primary sequence; STIR and T2FS fall back.
-- ----------------------------------------------------------------------------
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    ('BREAST', 'T1POST', 'mass_diameter_mm', 'gt', 15.0, 'CRITICAL',
     'large enhancing mass',
     'Mass diameter ≥15 mm on contrast-enhanced sequence',
     'tbc'),
    ('BREAST', 'T1POST', 'mass_diameter_mm', 'gt',  8.0, 'MODERATE',
     'moderate enhancing mass',
     'Mass diameter 8-15 mm on contrast-enhanced sequence',
     'tbc'),
    ('BREAST', 'T1POST', 'mass_diameter_mm', 'gt',  4.0, 'FINDING',
     'small enhancing mass',
     'Mass diameter 4-8 mm on contrast-enhanced sequence',
     'tbc'),

    -- Same diameter rules apply to STIR/T2FS for cyst-like lesions
    ('BREAST', 'STIR',   'mass_diameter_mm', 'gt', 15.0, 'CRITICAL',
     'large fluid-signal mass', 'Mass diameter ≥15 mm on STIR', 'tbc'),
    ('BREAST', 'STIR',   'mass_diameter_mm', 'gt',  8.0, 'MODERATE',
     'moderate fluid-signal mass', 'Mass diameter 8-15 mm on STIR', 'tbc'),
    ('BREAST', 'STIR',   'mass_diameter_mm', 'gt',  4.0, 'FINDING',
     'small fluid-signal mass', 'Mass diameter 4-8 mm on STIR', 'tbc'),

    ('BREAST', 'T2FS',   'mass_diameter_mm', 'gt', 15.0, 'CRITICAL',
     'large mass on fat-suppressed T2', 'Mass diameter ≥15 mm', 'tbc'),
    ('BREAST', 'T2FS',   'mass_diameter_mm', 'gt',  8.0, 'MODERATE',
     'moderate mass on fat-suppressed T2', 'Mass diameter 8-15 mm', 'tbc'),
    ('BREAST', 'T2FS',   'mass_diameter_mm', 'gt',  4.0, 'FINDING',
     'small mass on fat-suppressed T2', 'Mass diameter 4-8 mm', 'tbc'),

    -- Bilateral tissue volume asymmetry
    ('BREAST', 'ANY', 'tissue_asym_abs', 'gt', 0.20, 'MODERATE',
     'marked breast tissue asymmetry',
     'L-R volume difference ≥20% — possible mass effect or developmental',
     'tbc'),
    ('BREAST', 'ANY', 'tissue_asym_abs', 'gt', 0.10, 'FINDING',
     'breast tissue asymmetry',
     'L-R volume difference 10-20%',
     'tbc')
ON CONFLICT DO NOTHING;

COMMIT;

-- ============================================================================
-- Verification queries — run these post-migration to confirm rules loaded
-- ============================================================================

-- Total count by body part:
-- SELECT body_part, COUNT(*) AS n_rules
-- FROM az_impression_rules
-- WHERE source IN ('lit','tbc','analog','observed','code')
-- GROUP BY body_part
-- ORDER BY body_part;
--
-- Expected:
--   ANKLE     6
--   BRAIN     9
--   BREAST   11
--   CSPINE    6
--   ELBOW     6
--   FOOT      6
--   HAND      6
--   KNEE      6
--   LSPINE    6
--   SHOULDER  6
--   TSPINE    6
--   WRIST     6
-- Total: 80

-- Active rules per body part:
-- SELECT body_part, severity, COUNT(*)
-- FROM az_impression_rules
-- WHERE active = TRUE
-- GROUP BY body_part, severity
-- ORDER BY body_part, severity;

-- Rules by source (for tracking calibration progress):
-- SELECT source, COUNT(*) AS n
-- FROM az_impression_rules
-- GROUP BY source
-- ORDER BY n DESC;
--
-- After this migration the only 'lit' rules are CSPINE space + asymmetry
-- and BRAIN midline shift. As studies are validated, run UPDATE statements
-- to flip 'tbc' to 'observed' or 'lit' as appropriate, OR adjust thresholds
-- via UPDATE.

-- ============================================================================
-- Calibration update template (no code push required)
-- ============================================================================
-- After validating against a real study, calibrate a threshold like this:
--
-- UPDATE az_impression_rules
-- SET threshold = 28000, source = 'observed'
-- WHERE body_part = 'KNEE'
--   AND metric = 'effusion_volume_mm3'
--   AND severity = 'CRITICAL';
--
-- To deactivate a rule that produces false positives:
--
-- UPDATE az_impression_rules
-- SET active = FALSE
-- WHERE body_part = 'HAND'
--   AND metric = 'marrow_edema_volume_mm3'
--   AND severity = 'FINDING';

-- ============================================================================
-- Rollback (if migration needs to be reversed)
-- ============================================================================
-- Each INSERT used ON CONFLICT DO NOTHING so re-running this file is a noop.
-- To remove all preimpression rules (DESTRUCTIVE — only do this on dev):
--
-- DELETE FROM az_impression_rules
-- WHERE metric IN (
--     'space_min_mm', 'asym_lr_abs',
--     'midline_shift_abs_mm', 'ventricle_asym_abs', 'flair_lesion_count',
--     'effusion_volume_mm3', 'marrow_edema_volume_mm3',
--     'mass_diameter_mm', 'tissue_asym_abs'
-- );
