-- migration_p0066_cspine_v3_metrics.sql
-- Adds LR-sum impression rules for cervical spine v3 measurements.
-- source='observed' — calibrated from JH 2024 one labeled study.
-- Re-calibrate when cohort data lands; update source to 'lit'.
--
-- lr_sum_min_mm = sum of patient-left and patient-right cord-canal CSF
-- rim distances at the worst slice in the level. Complements space_min_mm
-- (single-worst-rim) with a bilateral envelope measure.
-- Thresholds at 1.5T baseline. adjust_threshold() scales at runtime.

BEGIN;

INSERT INTO az_impression_rules
    (body_part, seq_type, metric, operator, threshold, severity,
     label, rationale, source)
VALUES
    ('CSPINE', 'T2', 'lr_sum_min_mm', 'lt', 9.0, 'CRITICAL',
     'severe central canal stenosis (bilateral)',
     'Sum of L+R cord-canal CSF rims < 9mm — bilateral severe narrowing',
     'observed'),
    ('CSPINE', 'T2', 'lr_sum_min_mm', 'lt', 11.0, 'MODERATE',
     'central canal narrowing (bilateral)',
     'Sum of L+R cord-canal CSF rims < 11mm — bilateral moderate narrowing',
     'observed'),
    ('CSPINE', 'T2FS', 'lr_sum_min_mm', 'lt', 9.0, 'CRITICAL',
     'severe central canal stenosis (bilateral)',
     'Sum of L+R cord-canal CSF rims < 9mm — bilateral severe narrowing',
     'observed'),
    ('CSPINE', 'ANY', 'lr_sum_min_mm', 'lt', 9.0, 'CRITICAL',
     'severe central canal stenosis (bilateral)',
     'Sum of L+R cord-canal CSF rims < 9mm — bilateral severe narrowing',
     'observed')
ON CONFLICT DO NOTHING;

COMMIT;
