-- ============================================================
-- Artifact Zero DICOM Pipeline — Anatomy Parameter Expansion
-- Migration: anatomy_expansion_001.sql
-- Date: April 28, 2026
-- ============================================================

-- ============================================================
-- 1. BODY PART MAP — New anatomy detection rows
-- ============================================================

INSERT INTO az_body_part_map (dicom_tag, body_part, match_type, priority) VALUES
-- ANKLE
('ANKLE', 'ANKLE', 'exact', 100),
('ankle', 'ANKLE', 'contains', 80),
('hindfoot', 'ANKLE', 'contains', 80),
('talocrural', 'ANKLE', 'contains', 80),
('malleol', 'ANKLE', 'contains', 80),
-- WRIST
('WRIST', 'WRIST', 'exact', 100),
('wrist', 'WRIST', 'contains', 80),
('carpal', 'WRIST', 'contains', 80),
('upper joint', 'WRIST', 'contains', 80),
('distal radius', 'WRIST', 'contains', 80),
('scaphoid', 'WRIST', 'contains', 80),
('tfcc', 'WRIST', 'contains', 80),
-- FOOT
('FOOT', 'FOOT', 'exact', 100),
('foot', 'FOOT', 'contains', 80),
('forefoot', 'FOOT', 'contains', 80),
('midfoot', 'FOOT', 'contains', 80),
('metatarsal', 'FOOT', 'contains', 80),
('plantar', 'FOOT', 'contains', 80),
('calcaneus', 'FOOT', 'contains', 80),
-- ELBOW
('ELBOW', 'ELBOW', 'exact', 100),
('elbow', 'ELBOW', 'contains', 80),
('cubital', 'ELBOW', 'contains', 80),
('olecranon', 'ELBOW', 'contains', 80),
('radial head', 'ELBOW', 'contains', 80),
-- HAND
('HAND', 'HAND', 'exact', 100),
('hand', 'HAND', 'contains', 80),
('metacarpal', 'HAND', 'contains', 80),
('phalanx', 'HAND', 'contains', 80),
('palmar', 'HAND', 'contains', 80),
-- FINGER
('finger', 'FINGER', 'contains', 80),
('digit', 'FINGER', 'contains', 80),
('phalanges', 'FINGER', 'contains', 80),
('interphalangeal', 'FINGER', 'contains', 80),
-- TSPINE
('TSPINE', 'TSPINE', 'exact', 100),
('thoracic', 'TSPINE', 'contains', 80),
('t-spine', 'TSPINE', 'contains', 80),
('thoracic spine', 'TSPINE', 'contains', 80),
('t spine', 'TSPINE', 'contains', 80),
-- SACRUM
('sacrum', 'SACRUM', 'contains', 80),
('sacral', 'SACRUM', 'contains', 80),
('si joint', 'SACRUM', 'contains', 80),
('sacroiliac', 'SACRUM', 'contains', 80),
('coccyx', 'SACRUM', 'contains', 80);


-- ============================================================
-- 2. IMPRESSION RULES — New anatomy thresholds
-- ============================================================
-- Column order: body_part, seq_type, metric, operator, threshold, severity, label, rationale, source
--
-- Sources:
--   lit    = grounded in published clinical literature
--   analog = derived from confirmed anatomy with similar tissue structure
--   tbc    = needs live calibration against real data

-- ============================================================
-- ANKLE
-- Analog: KNEE (similar joint structure, cartilage, tendons, fluid)
-- Literature: Rosenberg et al. 2000 (ankle MRI protocols), Bencardino et al. 2011
-- ============================================================
INSERT INTO az_impression_rules (body_part, seq_type, metric, operator, threshold, severity, label, rationale, source) VALUES
('ANKLE', 'T2FS', 'gap', 'gt', 200, 'NORMAL', 'Normal fluid/cartilage contrast', 'Ankle T2 FS gap analogous to knee; high gap = good fluid-cartilage separation', 'analog'),
('ANKLE', 'T2FS', 'gap', 'lt', 80, 'FINDING', 'Reduced fluid contrast', 'Low gap suggests effusion or cartilage signal abnormality', 'analog'),
('ANKLE', 'T2FS', 'gap', 'lt', 40, 'CRITICAL', 'Critically low fluid contrast', 'Very low gap at ankle consistent with diffuse edema or hardware artifact', 'analog'),
('ANKLE', 'T1', 'gap', 'gt', 300, 'NORMAL', 'Normal bone/soft tissue contrast', 'T1 gap reflects marrow vs soft tissue; high = normal marrow signal', 'analog'),
('ANKLE', 'T1', 'gap', 'lt', 100, 'FINDING', 'Reduced marrow contrast', 'Low T1 gap may indicate marrow edema or AVN (talus)', 'lit'),
('ANKLE', 'STIR', 'gap', 'gt', 150, 'NORMAL', 'Normal STIR contrast', 'STIR gap analogous to knee STIR', 'analog'),
('ANKLE', 'STIR', 'gap', 'lt', 60, 'FINDING', 'Elevated STIR signal', 'Low gap on STIR suggests bone marrow edema or ligament injury', 'analog'),
('ANKLE', 'STIR', 'peak_left_asym', 'gt', 0.5, 'FINDING', 'Asymmetry detected', 'Unilateral edema pattern; compare to contralateral if available', 'analog'),

-- ============================================================
-- WRIST
-- Analog: HAND/KNEE hybrid. Small joints, complex anatomy.
-- Literature: Hobby et al. 2001 (wrist MRI), Magee 2011 (TFCC imaging)
-- ============================================================
('WRIST', 'T2FS', 'gap', 'gt', 150, 'NORMAL', 'Normal fluid contrast', 'Wrist T2 FS: smaller anatomy = lower absolute gap than knee', 'analog'),
('WRIST', 'T2FS', 'gap', 'lt', 50, 'FINDING', 'Reduced fluid contrast', 'Low gap may indicate TFCC tear, effusion, or ganglion', 'lit'),
('WRIST', 'T1', 'gap', 'gt', 400, 'NORMAL', 'Normal bone/soft tissue contrast', 'Wrist T1: bone marrow vs tendon. Large gap expected (confirmed: SAG T1 gap=1532 in personal wrist study)', 'lit'),
('WRIST', 'T1', 'gap', 'lt', 150, 'FINDING', 'Reduced marrow contrast', 'Low T1 gap may indicate scaphoid AVN, Kienbock disease, or fracture', 'lit'),
('WRIST', 'STIR', 'gap', 'gt', 100, 'NORMAL', 'Normal STIR contrast', 'STIR suppression of marrow signal', 'analog'),
('WRIST', 'STIR', 'gap', 'lt', 40, 'FINDING', 'Elevated STIR signal', 'Bone marrow edema at wrist; scaphoid fracture rule-out', 'lit'),

-- ============================================================
-- FOOT
-- Analog: ANKLE (overlapping anatomy). Plantar-specific additions.
-- Literature: Ashman et al. 2001 (foot MRI), Morrison et al. 1993
-- ============================================================
('FOOT', 'T2FS', 'gap', 'gt', 180, 'NORMAL', 'Normal fluid contrast', 'Foot T2 FS analog to ankle', 'analog'),
('FOOT', 'T2FS', 'gap', 'lt', 70, 'FINDING', 'Reduced fluid contrast', 'May indicate plantar fasciitis, stress fracture, or Morton neuroma', 'lit'),
('FOOT', 'T1', 'gap', 'gt', 300, 'NORMAL', 'Normal bone/soft tissue contrast', 'Metatarsal marrow vs soft tissue', 'analog'),
('FOOT', 'T1', 'gap', 'lt', 100, 'FINDING', 'Reduced marrow contrast', 'Stress fracture, Freiberg infraction, or infection', 'lit'),
('FOOT', 'STIR', 'gap', 'gt', 130, 'NORMAL', 'Normal STIR contrast', 'STIR suppression', 'analog'),
('FOOT', 'STIR', 'gap', 'lt', 50, 'FINDING', 'Elevated STIR signal', 'Bone marrow edema; stress response vs fracture', 'analog'),

-- ============================================================
-- ELBOW
-- Analog: KNEE/SHOULDER hybrid. Joint with tendons, cartilage, bursa.
-- Literature: Waldt et al. 2005 (elbow MRI), Bucknor et al. 2015
-- ============================================================
('ELBOW', 'T2FS', 'gap', 'gt', 180, 'NORMAL', 'Normal fluid contrast', 'Elbow T2 FS: joint fluid vs cartilage', 'analog'),
('ELBOW', 'T2FS', 'gap', 'lt', 70, 'FINDING', 'Reduced fluid contrast', 'May indicate lateral epicondylitis, UCL tear, or loose body', 'lit'),
('ELBOW', 'T1', 'gap', 'gt', 300, 'NORMAL', 'Normal bone/soft tissue contrast', 'Olecranon marrow vs soft tissue', 'analog'),
('ELBOW', 'T1', 'gap', 'lt', 100, 'FINDING', 'Reduced marrow contrast', 'OCD of capitellum, marrow edema', 'lit'),
('ELBOW', 'STIR', 'gap', 'gt', 140, 'NORMAL', 'Normal STIR contrast', 'STIR suppression', 'analog'),
('ELBOW', 'STIR', 'gap', 'lt', 55, 'FINDING', 'Elevated STIR signal', 'Bone marrow edema or tendinopathy', 'analog'),

-- ============================================================
-- HAND
-- Analog: WRIST (small joints, similar tissue types)
-- Literature: Clavero et al. 2002 (hand MRI), Bencardino et al. 2006
-- ============================================================
('HAND', 'T2FS', 'gap', 'gt', 140, 'NORMAL', 'Normal fluid contrast', 'Hand T2 FS: small joint fluid', 'analog'),
('HAND', 'T2FS', 'gap', 'lt', 45, 'FINDING', 'Reduced fluid contrast', 'May indicate synovitis, erosion, or tenosynovitis', 'lit'),
('HAND', 'T1', 'gap', 'gt', 350, 'NORMAL', 'Normal bone/soft tissue contrast', 'Metacarpal marrow vs tendon/muscle', 'analog'),
('HAND', 'T1', 'gap', 'lt', 120, 'FINDING', 'Reduced marrow contrast', 'Erosive arthropathy or infection', 'lit'),
('HAND', 'STIR', 'gap', 'gt', 100, 'NORMAL', 'Normal STIR contrast', 'STIR suppression', 'analog'),
('HAND', 'STIR', 'gap', 'lt', 35, 'FINDING', 'Elevated STIR signal', 'Bone marrow edema; inflammatory vs traumatic', 'analog'),

-- ============================================================
-- FINGER
-- Analog: HAND (subset anatomy)
-- Literature: Clavero et al. 2002
-- ============================================================
('FINGER', 'T2FS', 'gap', 'gt', 120, 'NORMAL', 'Normal fluid contrast', 'Finger T2 FS: very small anatomy, lower absolute gap', 'analog'),
('FINGER', 'T2FS', 'gap', 'lt', 35, 'FINDING', 'Reduced fluid contrast', 'May indicate collateral ligament tear, volar plate injury', 'lit'),
('FINGER', 'T1', 'gap', 'gt', 300, 'NORMAL', 'Normal bone/soft tissue contrast', 'Phalangeal marrow', 'analog'),
('FINGER', 'T1', 'gap', 'lt', 100, 'FINDING', 'Reduced marrow contrast', 'Enchondroma, glomus tumor, or infection', 'lit'),

-- ============================================================
-- TSPINE
-- Analog: CSPINE/LSPINE (same tissue types, different anatomy)
-- Literature: Goldberg et al. 1988, Ross et al. 1987 (thoracic spine MRI)
-- ============================================================
('TSPINE', 'T2', 'gap', 'gt', 200, 'NORMAL', 'Normal cord/CSF contrast', 'Thoracic cord vs CSF; analog to cervical', 'analog'),
('TSPINE', 'T2', 'gap', 'lt', 80, 'FINDING', 'Reduced cord contrast', 'May indicate demyelination, compression, or syrinx', 'lit'),
('TSPINE', 'T2', 'gap', 'lt', 35, 'CRITICAL', 'Critically low cord contrast', 'Cord compression or myelopathy', 'lit'),
('TSPINE', 'T1', 'gap', 'gt', 250, 'NORMAL', 'Normal vertebral contrast', 'Vertebral body marrow vs disc/cord', 'analog'),
('TSPINE', 'T1', 'gap', 'lt', 80, 'FINDING', 'Reduced vertebral contrast', 'Compression fracture, metastasis, or infection', 'lit'),
('TSPINE', 'STIR', 'gap', 'gt', 150, 'NORMAL', 'Normal STIR contrast', 'STIR suppression of marrow', 'analog'),
('TSPINE', 'STIR', 'gap', 'lt', 60, 'FINDING', 'Elevated STIR signal', 'Vertebral edema; acute fracture vs metastasis', 'lit'),
('TSPINE', 'STIR', 'peak_left_asym', 'gt', 0.4, 'FINDING', 'Cord asymmetry detected', 'Unilateral cord signal change; demyelination or mass', 'analog'),
('TSPINE', 'T2S', 'gap', 'gt', 100, 'NORMAL', 'Normal T2* contrast', 'Gradient echo: hemorrhage screening', 'analog'),
('TSPINE', 'T2S', 'gap', 'lt', 30, 'CRITICAL', 'T2* signal loss', 'Hemorrhagic cord lesion or calcification', 'lit'),

-- ============================================================
-- SACRUM
-- Analog: LSPINE/PELVIS hybrid
-- Literature: Braun et al. 2000 (sacroiliac MRI), Hermann et al. 2009
-- ============================================================
('SACRUM', 'T2FS', 'gap', 'gt', 180, 'NORMAL', 'Normal fluid contrast', 'SI joint fluid and periarticular tissue', 'analog'),
('SACRUM', 'T2FS', 'gap', 'lt', 70, 'FINDING', 'Reduced fluid contrast', 'May indicate sacroiliitis or insufficiency fracture', 'lit'),
('SACRUM', 'T1', 'gap', 'gt', 250, 'NORMAL', 'Normal marrow contrast', 'Sacral marrow vs soft tissue', 'analog'),
('SACRUM', 'T1', 'gap', 'lt', 80, 'FINDING', 'Reduced marrow contrast', 'Insufficiency fracture, metastasis, or infection', 'lit'),
('SACRUM', 'STIR', 'gap', 'gt', 140, 'NORMAL', 'Normal STIR contrast', 'STIR suppression of sacral marrow', 'analog'),
('SACRUM', 'STIR', 'gap', 'lt', 50, 'FINDING', 'Elevated STIR signal', 'Sacral bone marrow edema; SI joint inflammation', 'lit'),
('SACRUM', 'STIR', 'peak_left_asym', 'gt', 0.3, 'FINDING', 'SI joint asymmetry', 'Unilateral sacroiliitis pattern; compare bilateral', 'lit');
