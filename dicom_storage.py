"""
================================================================================
ARTIFACT ZERO LABS — Storage Processor
Merge into: services/ or pipeline/ directory

Handles all storage modes:
  none        — process in memory, store nothing
  session     — store for 24 hours, auto-delete
  local       — store permanently (requires BAA)
  encrypted   — store encrypted with customer key (we cannot read it)
  customer    — compute only, POST measurements to their endpoint
  full        — store everything including per-slice data

Every mode stores the audit record (study metadata, flags, timing).
Only 'local' and 'full' store per-slice measurements.
Only 'encrypted' stores the payload in encrypted form.
================================================================================
"""

import json
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional


# ════════════════════════════════════════════════════════════════════
# STORAGE MODES
# ════════════════════════════════════════════════════════════════════

STORAGE_MODES = {
    'none':      'Process in memory. Nothing persisted. Zero retention.',
    'session':   'Store for 24 hours. Auto-delete. Same-day follow-up enabled.',
    'local':     'Store permanently. Longitudinal comparison enabled. BAA required.',
    'encrypted': 'Store encrypted with customer key. We cannot read stored data.',
    'customer':  'Compute only. POST measurements to customer endpoint. We store nothing.',
    'full':      'Store everything — DICOM hash, all measurements, per-slice data, audit log.',
}

SESSION_TTL_HOURS = 24


# ════════════════════════════════════════════════════════════════════
# PATIENT DE-IDENTIFICATION
# ════════════════════════════════════════════════════════════════════

def hash_patient_id(patient_id: str, customer_id: str) -> str:
    """
    One-way hash of patient ID scoped to customer.
    Allows longitudinal matching within a customer without storing PHI.
    """
    combined = f"{customer_id}:{patient_id}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


# ════════════════════════════════════════════════════════════════════
# STORE STUDY RECORD
# Runs for ALL storage modes — this is the audit record.
# ════════════════════════════════════════════════════════════════════

def store_study_record(db, customer: dict, params: dict,
                       dicom_meta: dict, result: dict,
                       timing: dict) -> str:
    """
    Store the study audit record. Runs for every API call regardless of
    storage mode. The audit record contains no PHI — only metadata,
    flags, and timing.

    Returns the study_id UUID.
    """
    study_id = str(uuid.uuid4())

    # Scanner upsert
    scanner_id = _upsert_scanner(db, dicom_meta)

    # Patient hash — one-way, customer-scoped, no PHI stored
    patient_hash = None
    if dicom_meta.get('patient_id'):
        patient_hash = hash_patient_id(
            dicom_meta['patient_id'], customer['api_key_id'])

    # Expiry for session mode
    expires_at = None
    if params.get('storage') == 'session':
        expires_at = datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)

    impression = result.get('impression', {})
    flags      = impression.get('flags', [])

    db.execute("""
        INSERT INTO az_studies (
            id, customer_id, customer_study_ref,
            study_instance_uid, study_date, study_description,
            accession_number, institution_name, body_part, modality,
            scanner_id, patient_hash, patient_age, patient_sex,
            storage_mode, analysis_level, return_format,
            payload_encrypted, webhook_url,
            sequences_found, sequences_processed,
            flags_critical, flags_moderate, flags_finding, flags_normal,
            impression_status,
            response_ms, pipeline_ms, storage_ms, return_ms,
            measurements_stored, expires_at, called_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, NOW()
        )""",
        (
            study_id, customer['api_key_id'], params.get('customer_study_ref'),
            dicom_meta.get('study_instance_uid'),
            dicom_meta.get('study_date'),
            dicom_meta.get('study_description'),
            dicom_meta.get('accession_number'),
            dicom_meta.get('institution_name'),
            dicom_meta.get('body_part'),
            dicom_meta.get('modality'),
            scanner_id,
            patient_hash,
            dicom_meta.get('patient_age'),
            dicom_meta.get('patient_sex'),
            params.get('storage', 'none'),
            params.get('analysis', 'full'),
            params.get('return_format', 'json'),
            params.get('encrypt_key') is not None,
            params.get('webhook_url'),
            result.get('sequences_found', 0),
            result.get('sequences_processed', 0),
            sum(1 for f in flags if f.get('severity') == 'CRITICAL'),
            sum(1 for f in flags if f.get('severity') == 'MODERATE'),
            sum(1 for f in flags if f.get('severity') == 'FINDING'),
            sum(1 for f in flags if f.get('severity') == 'NORMAL'),
            impression.get('status', 'PENDING'),
            timing.get('total_ms'),
            timing.get('pipeline_ms'),
            timing.get('storage_ms'),
            timing.get('return_ms'),
            params.get('storage') in ('local', 'full', 'encrypted'),
            expires_at,
        )
    )
    db.commit()
    return study_id


# ════════════════════════════════════════════════════════════════════
# STORE MEASUREMENTS
# Only runs for: local, full, encrypted
# ════════════════════════════════════════════════════════════════════

def store_measurements(db, study_id: str, result: dict,
                       storage_mode: str, encrypt_key: Optional[str] = None):
    """
    Store sequence and slice measurements.
    Called after store_study_record for modes: local, full, encrypted.
    """
    sequences = result.get('sequences', [])

    for seq in sequences:
        seq_id = str(uuid.uuid4())

        # For encrypted mode — encrypt the flags and impression text
        flags_json = seq.get('flags_json', [])
        impression_text = seq.get('impression_text', '')

        if storage_mode == 'encrypted' and encrypt_key:
            from dicom_encryption import encrypt_payload
            flags_json = encrypt_payload(
                {'flags': flags_json, 'impression': impression_text},
                encrypt_key
            )
            impression_text = '[ENCRYPTED]'

        db.execute("""
            INSERT INTO az_sequences (
                id, study_id,
                series_description, seq_type, orientation, n_slices,
                slice_thickness_mm, pixel_spacing_row, pixel_spacing_col,
                rows, cols, flip_angle, repetition_time_ms, echo_time_ms,
                inversion_time_ms, field_strength, scanning_sequence,
                image_type, image_orientation,
                image_position_first, image_position_last,
                slice_location_first, slice_location_last,
                ref_A, ref_B, gap, mean_fraction, std_fraction,
                min_gap, min_gap_slice, min_gap_frac_inf, max_gap,
                compression_pct, gap_cv, rms_vs_standard, speedup_x, timing_ms,
                peak_left_asym, peak_left_slice, peak_left_frac_inf,
                pct_left_dominant, pct_right_dominant,
                n_compress_runs, run_widths_mm,
                peak_disagree_score, peak_disagree_slice, mean_disagree_score,
                flags_json, impression_text,
                flag_critical, flag_moderate, flag_finding, flag_normal,
                profile_run, asymmetry_run, width_run, agreement_run, impression_run
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )""",
            (
                seq_id, study_id,
                seq.get('series_description'), seq.get('seq_type'),
                seq.get('orientation'), seq.get('n_slices'),
                seq.get('slice_thickness_mm'), seq.get('pixel_spacing_row'),
                seq.get('pixel_spacing_col'), seq.get('rows'), seq.get('cols'),
                seq.get('flip_angle'), seq.get('repetition_time_ms'),
                seq.get('echo_time_ms'), seq.get('inversion_time_ms'),
                seq.get('field_strength'), seq.get('scanning_sequence'),
                seq.get('image_type'),
                json.dumps(seq.get('image_orientation')),
                json.dumps(seq.get('image_position_first')),
                json.dumps(seq.get('image_position_last')),
                seq.get('slice_location_first'), seq.get('slice_location_last'),
                seq.get('ref_A'), seq.get('ref_B'), seq.get('gap'),
                seq.get('mean_fraction'), seq.get('std_fraction'),
                seq.get('min_gap'), seq.get('min_gap_slice'),
                seq.get('min_gap_frac_inf'), seq.get('max_gap'),
                seq.get('compression_pct'), seq.get('gap_cv'),
                seq.get('rms_vs_standard'), seq.get('speedup_x'),
                seq.get('timing_ms'),
                seq.get('peak_left_asym'), seq.get('peak_left_slice'),
                seq.get('peak_left_frac_inf'),
                seq.get('pct_left_dominant'), seq.get('pct_right_dominant'),
                seq.get('n_compress_runs'),
                json.dumps(seq.get('run_widths_mm', [])),
                seq.get('peak_disagree_score'), seq.get('peak_disagree_slice'),
                seq.get('mean_disagree_score'),
                json.dumps(flags_json), impression_text,
                seq.get('flag_critical', 0), seq.get('flag_moderate', 0),
                seq.get('flag_finding', 0), seq.get('flag_normal', 0),
                seq.get('profile_run', False), seq.get('asymmetry_run', False),
                seq.get('width_run', False), seq.get('agreement_run', False),
                seq.get('impression_run', False),
            )
        )

        # Store per-slice data for 'full' mode only
        if storage_mode == 'full':
            _store_slices(db, seq_id, seq.get('slices', []))

    db.commit()


def _store_slices(db, seq_id: str, slices: list):
    """Store per-slice measurements. Only called for storage_mode='full'."""
    for sl in slices:
        db.execute("""
            INSERT INTO az_slices (
                id, sequence_id, slice_z, slice_frac_inf, slice_position_mm,
                gap, ref_A, ref_B, fraction,
                left_gap, right_gap, asym_index,
                norm_gap, disagree_score, n_voxels
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sequence_id, slice_z) DO NOTHING""",
            (
                str(uuid.uuid4()), seq_id,
                sl.get('slice_z'), sl.get('slice_frac_inf'),
                sl.get('slice_position_mm'),
                sl.get('gap'), sl.get('ref_A'), sl.get('ref_B'),
                sl.get('fraction'), sl.get('left_gap'), sl.get('right_gap'),
                sl.get('asym_index'), sl.get('norm_gap'),
                sl.get('disagree_score'), sl.get('n_voxels'),
            )
        )


# ════════════════════════════════════════════════════════════════════
# SCANNER UPSERT
# ════════════════════════════════════════════════════════════════════

def _upsert_scanner(db, dicom_meta: dict) -> Optional[str]:
    mfr   = dicom_meta.get('manufacturer', '')
    model = dicom_meta.get('model_name', '')
    ser   = dicom_meta.get('device_serial', '')

    if not mfr and not model:
        return None

    row = db.execute(
        "SELECT id FROM az_scanners WHERE manufacturer=%s AND model_name=%s AND device_serial=%s",
        (mfr, model, ser)
    ).fetchone()

    if row:
        return str(row[0])

    scanner_id = str(uuid.uuid4())
    db.execute("""
        INSERT INTO az_scanners
            (id, manufacturer, model_name, device_serial, software_version,
             field_strength, institution_name, station_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (scanner_id, mfr, model, ser,
         dicom_meta.get('software_version'),
         dicom_meta.get('field_strength'),
         dicom_meta.get('institution_name'),
         dicom_meta.get('station_name'))
    )
    db.commit()
    return scanner_id


# ════════════════════════════════════════════════════════════════════
# SESSION CLEANUP
# Run as a scheduled task (cron or Celery beat)
# ════════════════════════════════════════════════════════════════════

def cleanup_expired_sessions(db) -> int:
    """
    Delete studies and their measurements where storage_mode='session'
    and expires_at has passed. Returns number of studies deleted.
    """
    result = db.execute("""
        DELETE FROM az_studies
        WHERE storage_mode = 'session'
          AND expires_at < NOW()
        RETURNING id""")
    deleted = len(result.fetchall())
    db.commit()
    return deleted


# ════════════════════════════════════════════════════════════════════
# LONGITUDINAL LOOKUP
# ════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════
# LONGITUDINAL LOOKUP — S₀ RETRIEVAL
#
# The prior study IS S₀. Retrieving it correctly means retrieving
# the actual measurements — gap, asymmetry, disagreement — not just
# the impression labels. Labels are summaries. Measurements are S₀.
#
# find_prior_study_with_measurements() is the correct function to call
# for longitudinal analysis. find_prior_study() is kept for cases where
# only metadata is needed (e.g., checking whether a prior study exists
# before deciding whether to run longitudinal analysis).
# ════════════════════════════════════════════════════════════════════

def find_prior_study(db, customer_id: str, patient_hash: str,
                     body_part: str) -> Optional[dict]:
    """
    Find the most recent prior study for the same patient and body part.
    Returns metadata only — no sequence measurements.
    Use find_prior_study_with_measurements() for longitudinal analysis.
    """
    row = db.execute("""
        SELECT id, study_date, impression_status,
               flags_critical, flags_moderate
        FROM az_studies
        WHERE customer_id = %s
          AND patient_hash = %s
          AND body_part = %s
          AND storage_mode IN ('local', 'full', 'session', 'encrypted')
        ORDER BY called_at DESC
        LIMIT 1""",
        (customer_id, patient_hash, body_part)
    ).fetchone()

    if not row:
        return None

    return {
        'study_id':          str(row[0]),
        'study_date':        row[1].isoformat() if row[1] else None,
        'impression_status': row[2],
        'flags_critical':    row[3],
        'flags_moderate':    row[4],
        'sequences':         [],  # empty — use find_prior_study_with_measurements
    }


def find_prior_study_with_measurements(db, customer_id: str, patient_hash: str,
                                       body_part: str) -> Optional[dict]:
    """
    Find the most recent prior study WITH per-sequence measurements.

    Returns the prior study dict with a 'sequences' list containing
    actual gap, asymmetry, and disagreement values per sequence type.
    These measurements are S₀ — the prior state the current study
    arrives into.

    The longitudinal diff in dicom_processor_api.py compares current
    measurements against these values directly, producing findings like:
      'T2* gap 54.8 → 31.2 (-43% since 2024-05-14)'
    rather than comparing label strings.
    """
    # Find most recent prior study
    row = db.execute("""
        SELECT id, study_date, impression_status,
               flags_critical, flags_moderate
        FROM az_studies
        WHERE customer_id = %s
          AND patient_hash = %s
          AND body_part = %s
          AND storage_mode IN ('local', 'full', 'session', 'encrypted')
        ORDER BY called_at DESC
        LIMIT 1""",
        (customer_id, patient_hash, body_part)
    ).fetchone()

    if not row:
        return None

    prior_study_id = str(row[0])

    # Fetch per-sequence measurements from that study
    seq_rows = db.execute("""
        SELECT seq_type, series_description,
               min_gap, max_gap, compression_pct, gap_cv,
               mean_fraction, std_fraction,
               peak_left_asym, peak_left_frac_inf,
               pct_left_dominant, pct_right_dominant,
               peak_disagree_score, mean_disagree_score,
               n_compress_runs, run_widths_mm,
               flag_critical, flag_moderate, flag_finding
        FROM az_sequences
        WHERE study_id = %s""",
        (prior_study_id,)
    ).fetchall()

    sequences = []
    for sr in seq_rows:
        sequences.append({
            'seq_type':            sr[0],
            'series_description':  sr[1],
            'min_gap':             float(sr[2])  if sr[2]  is not None else None,
            'max_gap':             float(sr[3])  if sr[3]  is not None else None,
            'compression_pct':     float(sr[4])  if sr[4]  is not None else None,
            'gap_cv':              float(sr[5])  if sr[5]  is not None else None,
            'mean_fraction':       float(sr[6])  if sr[6]  is not None else None,
            'std_fraction':        float(sr[7])  if sr[7]  is not None else None,
            'peak_left_asym':      float(sr[8])  if sr[8]  is not None else None,
            'peak_left_frac_inf':  float(sr[9])  if sr[9]  is not None else None,
            'pct_left_dominant':   float(sr[10]) if sr[10] is not None else None,
            'pct_right_dominant':  float(sr[11]) if sr[11] is not None else None,
            'peak_disagree_score': float(sr[12]) if sr[12] is not None else None,
            'mean_disagree_score': float(sr[13]) if sr[13] is not None else None,
            'n_compress_runs':     sr[14],
            'run_widths_mm':       sr[15],
            'flag_critical':       sr[16] or 0,
            'flag_moderate':       sr[17] or 0,
            'flag_finding':        sr[18] or 0,
        })

    return {
        'study_id':          prior_study_id,
        'study_date':        row[1].isoformat() if row[1] else None,
        'impression_status': row[2],
        'flags_critical':    row[3],
        'flags_moderate':    row[4],
        'sequences':         sequences,
    }
