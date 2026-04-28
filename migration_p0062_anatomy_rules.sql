-- migration_p0062_anatomy_rules.sql
-- Artifact Zero -- Anatomy Rule Database
-- Run after deploy: psql $DATABASE_URL -f migration_p0062_anatomy_rules.sql
-- Safe to re-run: uses IF NOT EXISTS and ON CONFLICT DO NOTHING

-- ============================================================
-- TABLE 1: az_body_part_map
-- Maps DICOM BodyPartExamined tags and description keywords
-- to internal body_part codes used throughout the pipeline
-- ============================================================

CREATE TABLE IF NOT EXISTS az_body_part_map (
    id           SERIAL PRIMARY KEY,
    dicom_tag    TEXT NOT NULL,      -- from BodyPartExamined or keyword match
    body_part    TEXT NOT NULL,      -- internal code: CSPINE, LSPINE, BRAIN, etc
    match_type   TEXT NOT NULL,      -- 'exact' or 'contains'
    priority     INTEGER DEFAULT 0,  -- higher = checked first
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_body_part_map_tag
    ON az_body_part_map(dicom_tag, match_type);
CREATE INDEX IF NOT EXISTS idx_body_part_map_body_part
    ON az_body_part_map(body_part);

-- C-spine
INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
    ('CSPINE',          'CSPINE', 'exact',    100),
    ('CERVICAL SPINE',  'CSPINE', 'exact',    100),
    ('CERVICAL',        'CSPINE', 'exact',     90),
    ('C-SPINE',         'CSPINE', 'exact',     90),
    ('c-spine',         'CSPINE', 'contains',  80),
    ('cervical',        'CSPINE', 'contains',  80),
    ('cspine',          'CSPINE', 'contains',  80)
ON CONFLICT DO NOTHING;

-- L-spine
INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
    ('LSPINE',          'LSPINE', 'exact',    100),
    ('LUMBAR SPINE',    'LSPINE', 'exact',    100),
    ('LUMBAR',          'LSPINE', 'exact',     90),
    ('L-SPINE',         'LSPINE', 'exact',     90),
    ('lumbar',          'LSPINE', 'contains',  80),
    ('lspine',          'LSPINE', 'contains',  80)
ON CONFLICT DO NOTHING;

-- Brain
INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
    ('BRAIN',           'BRAIN',  'exact',    100),
    ('HEAD',            'BRAIN',  'exact',     90),
    ('NEURO',           'BRAIN',  'exact',     90),
    ('brain',           'BRAIN',  'contains',  80),
    ('head',            'BRAIN',  'contains',  70)
ON CONFLICT DO NOTHING;

-- Pelvis
INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
    ('PELVIS',          'PELVIS', 'exact',    100),
    ('PELVIC',          'PELVIS', 'exact',     90),
    ('pelvis',          'PELVIS', 'contains',  80)
ON CONFLICT DO NOTHING;

-- Knee
INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
    ('KNEE',            'KNEE',   'exact',    100),
    ('knee',            'KNEE',   'contains',  80)
ON CONFLICT DO NOTHING;


-- ============================================================
-- TABLE 2: az_sequence_types
-- Maps sequence description keywords to seq_type per body_part
-- seq_type is used to select the correct impression rules
-- ============================================================

CREATE TABLE IF NOT EXISTS az_sequence_types (
    id           SERIAL PRIMARY KEY,
    body_part    TEXT NOT NULL,      -- CSPINE, LSPINE, BRAIN, PELVIS, KNEE, ANY
    seq_type     TEXT NOT NULL,      -- T1, T2, T2S, STIR, FLAIR, PD, DWI, OTHER
    keyword      TEXT NOT NULL,      -- lowercase keyword to match in series description
    score_bonus  INTEGER DEFAULT 0,  -- added to base score when matched
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_seq_types_unique
    ON az_sequence_types(body_part, seq_type, keyword);
CREATE INDEX IF NOT EXISTS idx_seq_types_body_part
    ON az_sequence_types(body_part);

-- Universal sequence types (ANY body_part)
INSERT INTO az_sequence_types (body_part, seq_type, keyword, score_bonus) VALUES
    -- T1
    ('ANY', 'T1',   't1',       0),
    ('ANY', 'T1',   'mprage',  20),
    ('ANY', 'T1',   'bravo',   20),
    ('ANY', 'T1',   'spgr',    10),
    ('ANY', 'T1',   'tfe',     10),
    ('ANY', 'T1',   'fspgr',   10),
    ('ANY', 'T1',   't1w',      0),
    -- T2
    ('ANY', 'T2',   't2',       0),
    ('ANY', 'T2',   't2w',      0),
    ('ANY', 'T2',   'fse',      5),
    ('ANY', 'T2',   'tse',      5),
    -- T2S (T2-star, susceptibility-weighted, cord sequences)
    ('ANY', 'T2S',  't2*',     30),
    ('ANY', 'T2S',  't2star',  30),
    ('ANY', 'T2S',  'merge',   20),
    ('ANY', 'T2S',  'medic',   20),
    ('ANY', 'T2S',  'mgre',    20),
    ('ANY', 'T2S',  'gre',     10),
    ('ANY', 'T2S',  'swi',     25),
    ('ANY', 'T2S',  'swan',    25),
    ('ANY', 'T2S',  'suscept', 20),
    -- STIR
    ('ANY', 'STIR', 'stir',    20),
    ('ANY', 'STIR', 'short tau',20),
    -- FLAIR
    ('ANY', 'FLAIR','flair',   20),
    -- PD
    ('ANY', 'PD',   'pd',       0),
    ('ANY', 'PD',   'proton',   0),
    ('ANY', 'PD',   'pdw',      0),
    ('ANY', 'PD',   'dual echo',5),
    -- DWI
    ('ANY', 'DWI',  'dwi',     10),
    ('ANY', 'DWI',  'diffusion',10),
    ('ANY', 'DWI',  'adc',     10),
    ('ANY', 'DWI',  'dti',     10),
    -- T1 contrast
    ('ANY', 'T1C',  '+c',      10),
    ('ANY', 'T1C',  'post',     5),
    ('ANY', 'T1C',  'gad',     10),
    ('ANY', 'T1C',  'contrast', 5)
ON CONFLICT DO NOTHING;


-- ============================================================
-- TABLE 3: az_impression_rules
-- Impression flag thresholds per body_part and seq_type
-- operator: 'lt' = less than, 'gt' = greater than
-- ============================================================

CREATE TABLE IF NOT EXISTS az_impression_rules (
    id           SERIAL PRIMARY KEY,
    body_part    TEXT NOT NULL,
    seq_type     TEXT NOT NULL,      -- T1, T2, T2S, STIR, FLAIR, PD, DWI, ANY
    metric       TEXT NOT NULL,      -- min_gap, peak_asym, compression_pct, etc
    operator     TEXT NOT NULL,      -- 'lt' or 'gt'
    threshold    NUMERIC NOT NULL,
    severity     TEXT NOT NULL,      -- CRITICAL, MODERATE, FINDING, NORMAL
    label        TEXT NOT NULL,      -- human-readable flag label
    rationale    TEXT,               -- clinical rationale
    source       TEXT DEFAULT 'code',-- 'code', 'lit', 'observed'
    active       BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_impression_rules_body_seq
    ON az_impression_rules(body_part, seq_type, active);

-- ============================================================
-- CSPINE RULES
-- ============================================================
INSERT INTO az_impression_rules (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source) VALUES
    ('CSPINE', 'T2S',  'min_gap',         'lt', 50,   'CRITICAL', 'T2* gap critically compressed',      'Cord compression -- near-complete signal collapse', 'code'),
    ('CSPINE', 'T2S',  'min_gap',         'lt', 100,  'MODERATE', 'T2* gap compressed',                 'Significant cord narrowing', 'code'),
    ('CSPINE', 'T2S',  'run_width_max',   'gt', 30,   'FINDING',  'T2* continuous compression zone',    'Extended compression segment', 'code'),
    ('CSPINE', 'STIR', 'peak_left_asym',  'gt', 0.5,  'CRITICAL', 'STIR severe left asymmetry',         'Cord edema / unilateral signal', 'code'),
    ('CSPINE', 'STIR', 'pct_left_dominant','gt',50,   'FINDING',  'STIR left-dominant asymmetry',       'Left-sided signal asymmetry', 'code'),
    ('CSPINE', 'T2',   'min_gap',         'lt', 200,  'MODERATE', 'T2 gap compressed',                  'Low contrast -- disc/cord interface indistinct', 'code'),
    ('CSPINE', 'T2',   'compression_pct', 'gt', 40,   'MODERATE', 'T2 within-volume compression',       'Cord diameter reduction', 'code'),
    ('CSPINE', 'T1',   'min_gap',         'lt', 150,  'MODERATE', 'T1 gap compressed',                  'T1 hypointensity -- chronic cord injury signal', 'code'),
    ('CSPINE', 'CT',   'min_gap',         'lt', 30,   'FINDING',  'CT bone-soft tissue convergence',    'Canal narrowing on CT', 'code'),
    ('CSPINE', 'ANY',  'peak_disagree_score','gt',0.25,'FINDING', 'Cross-sequence disagreement elevated','Sequence disagreement -- review needed', 'code');

-- ============================================================
-- LSPINE RULES
-- ============================================================
INSERT INTO az_impression_rules (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source) VALUES
    ('LSPINE', 'T2S',  'min_gap',         'lt', 50,   'CRITICAL', 'T2* cauda equina compromise',        'Cauda equina compromise -- surgical urgency', 'lit'),
    ('LSPINE', 'T2S',  'min_gap',         'lt', 120,  'MODERATE', 'T2* canal narrowing',                'Significant canal narrowing -- grade 3 stenosis', 'code'),
    ('LSPINE', 'T2S',  'run_width_max',   'gt', 40,   'FINDING',  'T2* multi-level disease',            'Multi-level disease (>2 levels involved)', 'code'),
    ('LSPINE', 'STIR', 'peak_left_asym',  'gt', 0.4,  'MODERATE', 'STIR unilateral nerve root edema',   'Unilateral nerve root edema', 'code'),
    ('LSPINE', 'T2',   'min_gap',         'lt', 200,  'MODERATE', 'T2 disc-thecal interface loss',      'Disc-thecal interface loss', 'code'),
    ('LSPINE', 'T2',   'compression_pct', 'gt', 33,   'MODERATE', 'T2 herniation grade 2+',             'MSU grade 2+ herniation', 'lit'),
    ('LSPINE', 'T2',   'compression_pct', 'gt', 50,   'CRITICAL', 'T2 herniation grade 3',              'MSU grade 3 -- surgical threshold', 'lit'),
    ('LSPINE', 'T1',   'min_gap',         'lt', 120,  'MODERATE', 'T1 Modic change signal',             'Endplate Modic change signal', 'code'),
    ('LSPINE', 'T1',   'peak_left_asym',  'gt', 0.35, 'FINDING',  'T1 asymmetric foraminal compromise', 'Asymmetric foraminal compromise', 'code'),
    ('LSPINE', 'ANY',  'peak_disagree_score','gt',0.25,'FINDING', 'Cross-sequence disagreement elevated','Sequence disagreement', 'code');

-- ============================================================
-- BRAIN RULES
-- ============================================================
INSERT INTO az_impression_rules (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source) VALUES
    ('BRAIN', 'FLAIR', 'peak_left_asym',  'gt', 0.40, 'CRITICAL', 'FLAIR hemispheric asymmetry',        'Hemispheric asymmetry -- stroke / mass effect', 'lit'),
    ('BRAIN', 'FLAIR', 'peak_left_asym',  'gt', 0.20, 'MODERATE', 'FLAIR focal T2 hyperintensity',      'Focal T2 hyperintensity -- edema/lesion', 'code'),
    ('BRAIN', 'FLAIR', 'min_gap',         'lt', 150,  'MODERATE', 'FLAIR low grey-white contrast',      'Low grey-white contrast -- diffuse pathology', 'code'),
    ('BRAIN', 'FLAIR', 'std_fraction',    'gt', 0.42, 'FINDING',  'FLAIR high spatial variability',     'High spatial variability -- multifocal lesions', 'code'),
    ('BRAIN', 'T2',    'min_gap',         'lt', 150,  'MODERATE', 'T2 reduced tissue contrast',         'Reduced tissue contrast', 'code'),
    ('BRAIN', 'T2',    'peak_left_asym',  'gt', 0.30, 'MODERATE', 'T2 regional signal asymmetry',      'Regional signal asymmetry', 'code'),
    ('BRAIN', 'T1',    'min_gap',         'lt', 150,  'MODERATE', 'T1 hypointensity',                   'T1 hypointensity -- chronic lesion / black holes', 'lit'),
    ('BRAIN', 'DWI',   'peak_left_asym',  'gt', 0.35, 'CRITICAL', 'DWI diffusion restriction asymmetry','Diffusion restriction asymmetry -- acute stroke', 'lit'),
    ('BRAIN', 'ANY',   'peak_disagree_score','gt',0.25,'FINDING', 'Cross-sequence disagreement elevated','Sequence disagreement', 'code');

-- ============================================================
-- PELVIS RULES
-- ============================================================
INSERT INTO az_impression_rules (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source) VALUES
    ('PELVIS', 'T2',  'peak_left_asym',  'gt', 0.35, 'CRITICAL', 'T2 asymmetric pelvic mass',          'Asymmetric pelvic mass -- dominant unilateral signal', 'code'),
    ('PELVIS', 'T2',  'peak_left_asym',  'gt', 0.20, 'MODERATE', 'T2 focal pelvic signal',             'Focal T2 signal -- fluid / lesion', 'code'),
    ('PELVIS', 'T2',  'min_gap',         'lt', 100,  'MODERATE', 'T2 low soft tissue contrast',        'Low soft tissue contrast -- diffuse infiltration', 'code'),
    ('PELVIS', 'T1',  'min_gap',         'lt', 40,   'CRITICAL', 'T1 near-zero contrast',              'Near-zero contrast -- severe signal loss', 'observed'),
    ('PELVIS', 'T1',  'min_gap',         'lt', 60,   'MODERATE', 'T1 below normal minimum',            'Below observed normal minimum of 68', 'observed'),
    ('PELVIS', 'DWI', 'peak_left_asym',  'gt', 0.30, 'MODERATE', 'DWI asymmetric diffusion restriction','Asymmetric diffusion restriction -- malignancy', 'code'),
    ('PELVIS', 'ANY', 'std_fraction',    'gt', 0.44, 'FINDING',  'High slice-to-slice variability',    'High spatial variability -- heterogeneous mass', 'code'),
    ('PELVIS', 'ANY', 'peak_disagree_score','gt',0.25,'FINDING', 'Cross-sequence disagreement elevated','Sequence disagreement', 'code');

-- ============================================================
-- KNEE RULES
-- ============================================================
INSERT INTO az_impression_rules (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source) VALUES
    ('KNEE', 'PD',   'min_gap',         'lt', 150,  'CRITICAL', 'PD severe cartilage signal loss',    'Severe cartilage signal loss -- bone-on-bone', 'observed'),
    ('KNEE', 'PD',   'min_gap',         'lt', 250,  'MODERATE', 'PD moderate cartilage thinning',     'Moderate cartilage thinning', 'observed'),
    ('KNEE', 'PD',   'peak_left_asym',  'gt', 0.35, 'MODERATE', 'PD compartment asymmetry',          'Medial/lateral compartment asymmetry -- compartment narrowing', 'observed'),
    ('KNEE', 'PD',   'peak_left_asym',  'gt', 0.50, 'CRITICAL', 'PD severe compartment asymmetry',   'Severe compartment asymmetry', 'observed'),
    ('KNEE', 'T2',   'std_fraction',    'gt', 0.40, 'FINDING',  'T2 irregular cartilage signal',     'High variability -- irregular cartilage signal', 'observed'),
    ('KNEE', 'ANY',  'peak_disagree_score','gt',0.25,'FINDING', 'Cross-sequence disagreement elevated','Sequence disagreement', 'observed');
