-- ===========================================================================
-- BRAIN — preimpression rules for brain.v3 detection algorithm
-- ===========================================================================
-- Source: 'lit' = literature-calibrated thresholds
-- Active: TRUE for ungate after phantom validation 9/9
--
-- Metrics emitted by brain.v3:
--   midline_shift_abs_mm      → from brain_findings.max_midline_shift_mm (abs)
--   ventricle_asym_abs        → from brain_findings.ventricle_asym_overall (abs)
--   flair_lesion_count        → from brain_findings.flair_lesion_summary.lesion_count
--
-- Severity thresholds chosen per clinical literature:
--   Midline shift: 5mm = surgical decompression threshold
--                  3mm = significant mass effect
--                  1mm = detectable change (above noise floor)
--   Vent asymmetry: 0.30 = severe (e.g., obstructive hydrocephalus)
--                   0.20 = moderate (clinically apparent)
--                   0.10 = mild (above measurement noise)
--   Lesion count:   30 = heavy burden (likely advanced MS or extensive SVD)
--                   15 = moderate burden (Fazekas-equivalent grade 2-3)
--                    5 = mild burden (above incidental WM hyperintensity rate)
-- ===========================================================================

INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
    -- Midline shift
        ('BRAIN', 'ANY', 'midline_shift_abs_mm', 'gt', 5.0, 'CRITICAL', 'midline shift ≥5mm (sustained)', NULL, 'lit'),
        ('BRAIN', 'ANY', 'midline_shift_abs_mm', 'gt', 3.0, 'MODERATE', 'midline shift ≥3mm', NULL, 'lit'),
        ('BRAIN', 'ANY', 'midline_shift_abs_mm', 'gt', 1.0, 'FINDING', 'midline shift detected', NULL, 'lit'),

    -- Ventricle asymmetry
        ('BRAIN', 'ANY', 'ventricle_asym_abs', 'gt', 0.30, 'CRITICAL', 'marked ventricular asymmetry', NULL, 'lit'),
        ('BRAIN', 'ANY', 'ventricle_asym_abs', 'gt', 0.20, 'MODERATE', 'moderate ventricular asymmetry', NULL, 'lit'),
        ('BRAIN', 'ANY', 'ventricle_asym_abs', 'gt', 0.10, 'FINDING', 'mild ventricular asymmetry', NULL, 'lit'),

    -- FLAIR lesion burden
        ('BRAIN', 'ANY', 'flair_lesion_count', 'gt', 30, 'CRITICAL', 'high FLAIR lesion burden', NULL, 'lit'),
        ('BRAIN', 'ANY', 'flair_lesion_count', 'gt', 15, 'MODERATE', 'moderate FLAIR lesion burden', NULL, 'lit'),
        ('BRAIN', 'ANY', 'flair_lesion_count', 'gt', 5, 'FINDING', 'FLAIR lesions present', NULL, 'lit')
ON CONFLICT DO NOTHING;

-- If brain rules were previously inserted as inactive placeholders,
-- reactivate them now that v3 is validated:
UPDATE az_impression_rules
   SET active = TRUE,
       source = 'lit'
 WHERE body_part = 'BRAIN'
   AND active IS NOT TRUE;
