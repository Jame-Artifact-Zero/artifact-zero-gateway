-- =============================================================================
-- az_impression_rules INSERT block
-- Append to existing migration. Do NOT create a new table.
-- Table: az_impression_rules
-- Existing columns assumed: anatomy, seq_type, metric, operator,
--                           threshold, severity, label
--
-- Field strength note:
--   Gap thresholds are calibrated at 1.5T baseline.
--   adjust_threshold() in az_pathway_b.py scales them by (field_strength/1.5)
--   at evaluation time. No threshold values here need to change.
--   Fraction metrics (peak_asym, compression_pct, std_fraction, pct_left,
--   peak_disagree, enhancement_delta) are field-strength invariant.
--
-- Sources:
--   [live]  = observed directly from live API studies (April 2026)
--   [K0]    = K0 experimental results, 16-patient Stanford knee dataset
--   [lit]   = clinical literature threshold
--   [analog] = derived by analogy from confirmed anatomy
-- =============================================================================


-- =============================================================================
-- LUMBAR SPINE
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- Canal compromise
('LSPINE', 'T2S', 'min_gap',          'lt', 50,   'CRITICAL', 'Severe canal stenosis — cauda equina risk', NULL, 'team'),          -- [lit]
('LSPINE', 'T2S', 'min_gap',          'lt', 120,  'MODERATE', 'Significant lumbar canal narrowing', NULL, 'team'),
('LSPINE', 'T2S', 'run_width',        'gt', 40,   'FINDING',  'Multi-level disease — >2 levels involved', NULL, 'team'),
-- Disc herniation grading
('LSPINE', 'T2',  'compression_pct',  'gt', 50,   'CRITICAL', 'MSU grade 3 disc herniation — surgical threshold', NULL, 'team'),   -- [lit]
('LSPINE', 'T2',  'compression_pct',  'gt', 33,   'MODERATE', 'MSU grade 2+ disc herniation', NULL, 'team'),                       -- [lit]
('LSPINE', 'T2',  'min_gap',          'lt', 200,  'MODERATE', 'Disc-thecal sac interface signal loss', NULL, 'team'),
-- Nerve root / asymmetry
('LSPINE', 'STIR','peak_asym',        'gt', 0.40, 'MODERATE', 'Unilateral nerve root edema', NULL, 'team'),
('LSPINE', 'T1',  'min_gap',          'lt', 120,  'MODERATE', 'Endplate Modic change — T1 signal', NULL, 'team'),
('LSPINE', 'T1',  'peak_asym',        'gt', 0.35, 'FINDING',  'Asymmetric foraminal compromise', NULL, 'team'),
-- Cross-sequence
('LSPINE', 'ANY', 'peak_disagree',    'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- BRAIN
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- FLAIR — primary workhorse for brain pathology
('BRAIN', 'FLAIR', 'peak_asym',         'gt', 0.40, 'CRITICAL', 'Hemispheric signal asymmetry — stroke or mass effect', NULL, 'team'),  -- [lit]
('BRAIN', 'FLAIR', 'peak_asym',         'gt', 0.20, 'MODERATE', 'Focal T2 hyperintensity — edema or lesion', NULL, 'team'),
('BRAIN', 'FLAIR', 'min_gap',           'lt', 150,  'MODERATE', 'Reduced grey-white contrast — diffuse pathology', NULL, 'team'),
('BRAIN', 'FLAIR', 'std_fraction',      'gt', 0.42, 'FINDING',  'High spatial variability — multifocal lesions', NULL, 'team'),        -- [live]
-- T2
('BRAIN', 'T2',   'min_gap',            'lt', 150,  'MODERATE', 'Reduced tissue contrast', NULL, 'team'),
('BRAIN', 'T2',   'peak_asym',          'gt', 0.30, 'MODERATE', 'Regional signal asymmetry', NULL, 'team'),
-- T1
('BRAIN', 'T1',   'min_gap',            'lt', 150,  'MODERATE', 'T1 hypointensity — chronic lesion or black holes', NULL, 'team'),      -- [lit]
-- Post-contrast enhancement
-- enhancement_delta = mean_fraction(T1+C) - mean_fraction(T1 pre)
-- Requires both sequences present. If only post available, use mean_fraction > 0.35.
('BRAIN', 'T1C', 'enhancement_delta',  'gt', 0.30, 'CRITICAL', 'Significant BBB breakdown — tumor or abscess', NULL, 'team'),
('BRAIN', 'T1C', 'enhancement_delta',  'gt', 0.15, 'FINDING',  'Post-contrast enhancement — active lesion', NULL, 'team'),
-- DWI
('BRAIN', 'DWI',  'peak_asym',          'gt', 0.35, 'CRITICAL', 'Diffusion restriction asymmetry — acute stroke', NULL, 'team'),        -- [lit]
-- Cross-sequence
('BRAIN', 'ANY',  'peak_disagree',      'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- PELVIS
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- T2 soft tissue
('PELVIS', 'T2',   'peak_asym',         'gt', 0.35, 'CRITICAL', 'Dominant pelvic mass — unilateral signal', NULL, 'team'),
('PELVIS', 'T2',   'peak_asym',         'gt', 0.20, 'MODERATE', 'Focal T2 signal — fluid or lesion', NULL, 'team'),
('PELVIS', 'T2',   'min_gap',           'lt', 100,  'MODERATE', 'Low soft tissue contrast — diffuse infiltration', NULL, 'team'),
-- T1 — gap thresholds calibrated from live 3T data (min observed = 68 normal)  [live]
('PELVIS', 'T1',   'min_gap',           'lt', 40,   'CRITICAL', 'Near-zero tissue contrast — severe signal loss', NULL, 'team'),
('PELVIS', 'T1',   'min_gap',           'lt', 60,   'MODERATE', 'Below normal minimum — tissue signal compromise', NULL, 'team'),       -- [live]
-- Post-contrast
('PELVIS', 'T1C', 'enhancement_delta', 'gt', 0.25, 'CRITICAL', 'Significant enhancement — malignancy or abscess', NULL, 'team'),
('PELVIS', 'T1C', 'enhancement_delta', 'gt', 0.12, 'FINDING',  'Post-contrast enhancement — active lesion', NULL, 'team'),
-- DWI
('PELVIS', 'DWI',  'peak_asym',         'gt', 0.30, 'MODERATE', 'Asymmetric diffusion restriction — malignancy', NULL, 'team'),
-- Variability
('PELVIS', 'ANY',  'std_fraction',      'gt', 0.44, 'FINDING',  'High slice variability — heterogeneous mass', NULL, 'team'),          -- [live]
('PELVIS', 'ANY',  'peak_disagree',     'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- KNEE
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- PD / proton density — primary knee cartilage sequence
('KNEE', 'PD',   'min_gap',             'lt', 150,  'CRITICAL', 'Severe cartilage signal loss — bone on bone', NULL, 'team'),
('KNEE', 'PD',   'min_gap',             'lt', 250,  'MODERATE', 'Moderate cartilage thinning', NULL, 'team'),
('KNEE', 'PD',   'peak_asym',           'gt', 0.50, 'CRITICAL', 'Severe medial/lateral compartment asymmetry', NULL, 'team'),          -- [K0]
('KNEE', 'PD',   'peak_asym',           'gt', 0.35, 'MODERATE', 'Compartment asymmetry — compartment narrowing', NULL, 'team'),        -- [K0]
-- T2
('KNEE', 'T2',   'min_gap',             'lt', 100,  'CRITICAL', 'Joint fluid signal collapse — advanced OA or effusion', NULL, 'team'),
('KNEE', 'T2',   'std_fraction',        'gt', 0.40, 'FINDING',  'High variability — irregular cartilage signal', NULL, 'team'),
-- STIR — bone marrow edema
('KNEE', 'STIR', 'peak_asym',           'gt', 0.50, 'CRITICAL', 'Severe bone marrow edema — fracture or osteonecrosis', NULL, 'team'), -- [lit]
('KNEE', 'STIR', 'peak_asym',           'gt', 0.30, 'MODERATE', 'Bone marrow edema — contusion or stress reaction', NULL, 'team'),     -- [lit]
-- T1
('KNEE', 'T1',   'min_gap',             'lt', 80,   'MODERATE', 'Marrow signal loss — infiltration or edema', NULL, 'team'),
-- Pathway B operators (new — added by this migration)
-- b_alg_b_joint: 5th pct k-space magnitude at mid-volume
-- b_pg_center_edge: phase gradient center/edge ratio
('KNEE', 'ANY',  'b_alg_b_joint',       'lt', 50000,'MODERATE', 'Low k-space baseline signal — joint contrast loss', NULL, 'team'),    -- [K0] threshold TBC on 3T data
('KNEE', 'ANY',  'b_pg_center_edge',    'lt', 0.80, 'FINDING',  'Phase gradient imbalance — irregular tissue structure', NULL, 'team'),-- [K0]
-- Cross-sequence
('KNEE', 'ANY',  'peak_disagree',       'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- SHOULDER
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- Rotator cuff — PD fat-sat is the workhorse
('SHOULDER', 'PDFS', 'peak_asym',      'gt', 0.40, 'CRITICAL', 'Full-thickness rotator cuff tear — fluid fills defect', NULL, 'team'), -- [lit]
('SHOULDER', 'PDFS', 'peak_asym',      'gt', 0.20, 'MODERATE', 'Partial tear or tendinosis signal', NULL, 'team'),
('SHOULDER', 'T2FS', 'min_gap',        'lt', 80,   'MODERATE', 'Supraspinatus signal loss', NULL, 'team'),
('SHOULDER', 'T2FS', 'std_fraction',   'gt', 0.42, 'FINDING',  'Irregular signal — partial tear or tendinopathy', NULL, 'team'),
('SHOULDER', 'T1',    'min_gap',        'lt', 60,   'MODERATE', 'Muscle atrophy signal — chronic tear', NULL, 'team'),
('SHOULDER', 'ANY',   'peak_disagree',  'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- HIP
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- AVN — the critical catch
('HIP', 'T1',    'min_gap',             'lt', 50,   'CRITICAL', 'Subchondral T1 signal collapse — avascular necrosis', NULL, 'team'), -- [lit]
('HIP', 'T1',    'peak_asym',           'gt', 0.40, 'CRITICAL', 'Unilateral femoral head signal — AVN or fracture', NULL, 'team'),
-- Stress / edema
('HIP', 'STIR',  'peak_asym',           'gt', 0.35, 'MODERATE', 'Marrow edema — stress reaction or early AVN', NULL, 'team'),
-- Labrum / joint
('HIP', 'T2FS', 'peak_asym',           'gt', 0.30, 'MODERATE', 'Labral or joint signal asymmetry', NULL, 'team'),
('HIP', 'T2FS', 'min_gap',             'lt', 80,   'MODERATE', 'Joint space narrowing — advanced OA', NULL, 'team'),
('HIP', 'ANY',   'peak_disagree',       'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- ABDOMEN
-- =============================================================================
INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source)
VALUES
-- Hepatic lesion detection
('ABDOMEN', 'T2FS', 'peak_asym',          'gt', 0.55, 'CRITICAL', 'Dominant focal lesion — malignancy workup required', NULL, 'team'),
('ABDOMEN', 'T2FS', 'peak_asym',          'gt', 0.35, 'MODERATE', 'Focal T2 hyperintensity — hepatic lesion', NULL, 'team'),
('ABDOMEN', 'T1FS', 'min_gap',            'lt', 60,   'MODERATE', 'Low hepatic T1 signal — iron overload or infiltration', NULL, 'team'),
-- Post-contrast — HCC pattern
('ABDOMEN', 'T1C',  'enhancement_delta',  'gt', 0.35, 'CRITICAL', 'Washout pattern — HCC LI-RADS 4/5', NULL, 'team'),                -- [lit]
('ABDOMEN', 'T1C',  'enhancement_delta',  'gt', 0.20, 'MODERATE', 'Arterial enhancement — HCC arterial phase pattern', NULL, 'team'), -- [lit]
-- DWI
('ABDOMEN', 'DWI',   'peak_asym',          'gt', 0.40, 'FINDING',  'Diffusion restriction — solid lesion vs cyst', NULL, 'team'),
-- Variability
('ABDOMEN', 'ANY',   'std_fraction',       'gt', 0.45, 'FINDING',  'High variability — heterogeneous organ or multifocal disease', NULL, 'team'),
('ABDOMEN', 'ANY',   'peak_disagree',      'gt', 0.25, 'FINDING',  'Sequence disagreement — radiologist review', NULL, 'team');


-- =============================================================================
-- NOTES FOR IMPLEMENTATION TEAM
-- =============================================================================
-- 1. b_alg_b_joint threshold (knee, 50000) is a placeholder.
--    The K0 dataset is 3T GE. Once the first 3T knee DICOM runs through
--    the API with Pathway B active, read the returned b_alg_b_joint value
--    for a known-normal study and set the MODERATE threshold at 80% of that.
--
-- 2. b_pg_center_edge threshold (0.80) is estimated. Same calibration
--    approach — read first live values, adjust.
--
-- 3. enhancement_delta requires both pre and post T1 sequences in the
--    same study. The pipeline needs to detect and pair them by series
--    description or acquisition time before computing the delta.
--    If only post-contrast T1 is present: flag FINDING if mean_fraction > 0.35.
--
-- 4. All gap thresholds above are 1.5T baselines.
--    adjust_threshold() scales them by (field_strength / 1.5) at eval time.
--    No changes needed here.
--
-- 5. Shoulder and hip rows are draft — no live API calibration data yet.
--    Flag as provisional in the UI until validated on real studies.
