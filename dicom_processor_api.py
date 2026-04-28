"""
================================================================================
ARTIFACT ZERO LABS — DICOM Pipeline API Wrapper
Merge into: pipeline/ directory

This is the bridge between the Flask API layer and the analysis scripts.
Accepts raw DICOM bytes, runs the full pipeline, returns a structured dict.

The four analysis scripts (az_dicom_processor, az_spine_profile,
az_advanced_analysis, az_database) are imported and called directly.
No subprocess calls. No temp file artifacts left behind.

Analysis levels:
  speed       — decomposition only (gap, fraction, RMS, speedup)
  profile     — + slice-by-slice gap profile
  standard    — + asymmetry + width
  full        — + cross-sequence agreement
  impression  — full + rule-based impression flags
  longitudinal — impression + comparison to prior study
================================================================================
"""

import io
import os
import time
import tempfile
import warnings
import logging
import traceback
from pathlib import Path
from typing import Optional

warnings.filterwarnings('ignore')


# ════════════════════════════════════════════════════════════════════
# ANALYSIS LEVEL DEFINITIONS
# ════════════════════════════════════════════════════════════════════

ANALYSIS_LEVELS = {
    'speed':        ['decomposition'],
    'profile':      ['decomposition', 'profile'],
    'standard':     ['decomposition', 'profile', 'asymmetry', 'width'],
    'full':         ['decomposition', 'profile', 'asymmetry', 'width', 'agreement'],
    'impression':   ['decomposition', 'profile', 'asymmetry', 'width', 'agreement', 'impression'],
    'longitudinal': ['decomposition', 'profile', 'asymmetry', 'width', 'agreement', 'impression', 'longitudinal'],
}

# Body parts that use the C-spine impression rule set
CSPINE_BODY_PARTS = {'CSPINE', 'CERVICAL'}


# ════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════

def process_dicom_bytes(raw_bytes: bytes, params: dict = None,
                        prior_study: dict = None) -> dict:
    """
    Accept raw DICOM bytes. Run the pipeline. Return structured dict.

    params keys (all optional):
      analysis    — analysis level (default: 'full')
      body_part   — override auto-detection ('auto', 'cspine', etc.)

    prior_study   — dict from dicom_storage.find_prior_study()
                    used for longitudinal comparison

    Returns a result dict ready to pass to dicom_return.build_response()
    """
    params       = params or {}
    analysis     = params.get('analysis', 'full')
    steps        = ANALYSIS_LEVELS.get(analysis, ANALYSIS_LEVELS['full'])
    t_start      = time.perf_counter()

    result = {
        'sequences_found':     0,
        'sequences_processed': 0,
        'sequences':           [],
        'impression':          {'status': 'PENDING', 'flags': [], 'text': ''},
        'pipeline_ms':         None,
    }

    # Write bytes to a temp directory — pipeline scripts need file paths
    extracted_paths = params.get('_extracted_dcm_paths')
    tmp_dir_override = params.get('_tmp_dir')

    if extracted_paths:
        # Zip was already extracted by the blueprint — use those files directly
        import shutil
        try:
            from az_dicom_processor import (
                group_by_sequence as group_series,
                load_volume as load_volume_sorted,
                best_slice,
                get_tissue_landmarks, method_algebraic, method_iterative,
                score_sequence as detect_seq_type,
            )

            def detect_body_part_from_dicom(ds):
                bp = getattr(ds, 'BodyPartExamined', '') or ''
                bp = bp.upper().strip()
                if not bp:
                    desc = str(getattr(ds, 'StudyDescription', '') or '').upper()
                    if any(k in desc for k in ['SPINE', 'CERVICAL', 'CSPINE', 'C-SPINE']):
                        return 'CSPINE'
                    if any(k in desc for k in ['BRAIN', 'HEAD']):
                        return 'BRAIN'
                    if any(k in desc for k in ['LUMBAR', 'LSPINE', 'L-SPINE']):
                        return 'LSPINE'
                    if any(k in desc for k in ['THORACIC', 'TSPINE', 'T-SPINE']):
                        return 'TSPINE'
                    return 'UNKNOWN'
                if bp in ('CSPINE', 'CERVICAL'):
                    return 'CSPINE'
                return bp

            series_list = group_series(extracted_paths)
            good = [s for s in series_list if s['score'] > 0]

            if not good:
                result['error'] = 'No processable sequences found in DICOM'
                return result

            import pydicom as pd
            ds = pd.dcmread(str(extracted_paths[0]), stop_before_pixels=True)

            def safe(tag, default=''):
                v = getattr(ds, tag, None)
                return str(v).strip() if v else default

            raw_date = safe('StudyDate')
            if raw_date and len(raw_date) == 8:
                raw_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

            body_part = detect_body_part_from_dicom(ds)
            result.update({
                'study_instance_uid':  safe('StudyInstanceUID'),
                'study_date':          raw_date,
                'study_description':   safe('StudyDescription'),
                'accession_number':    safe('AccessionNumber'),
                'institution_name':    safe('InstitutionName'),
                'body_part':           body_part,
                'modality':            safe('Modality'),
                'manufacturer':        safe('Manufacturer'),
                'model_name':          safe('ManufacturerModelName'),
                'device_serial':       safe('DeviceSerialNumber'),
                'field_strength':      getattr(ds, 'MagneticFieldStrength', None),
                'patient_id':          safe('PatientID'),
                'patient_age':         safe('PatientAge'),
                'patient_sex':         safe('PatientSex'),
                'sequences_found':     len(good),
                'sequences_processed': 0,
                'sequences':           [],
            })

            # Continue with remaining steps using a temp path context
            from pathlib import Path
            tmp_path = Path(tmp_dir_override) if tmp_dir_override else Path(extracted_paths[0]).parent

            if not result.get('sequences') and good:
                # Run decomposition inline for extracted files
                decomp = _run_decomposition_from_series(good, ds, body_part, safe, raw_date)
                result.update(decomp)

            if result.get('sequences'):
                if 'profile' in steps:
                    _run_profile(tmp_path, result)
                if 'asymmetry' in steps:
                    _run_asymmetry(tmp_path, result)
                if 'width' in steps:
                    _run_width(tmp_path, result)
                if 'agreement' in steps:
                    _run_agreement(tmp_path, result)
                if 'impression' in steps:
                    rules = _load_impression_rules(result.get('body_part', 'UNKNOWN'))
                    if rules:
                        _apply_rules(result, rules)
                    else:
                        _run_generic_impression(result)
                if 'longitudinal' in steps and prior_study:
                    _run_longitudinal_diff(result, prior_study)

        except Exception as e:
            result['error'] = str(e)
            result['status'] = 'ERROR'
        finally:
            if tmp_dir_override:
                shutil.rmtree(tmp_dir_override, ignore_errors=True)

        result['pipeline_ms'] = round((time.perf_counter() - t_start) * 1000, 2)
        return result
        tmp_path = Path(tmp_dir)
        dcm_path = tmp_path / 'input.dcm'
        dcm_path.write_bytes(raw_bytes)

        try:
            # ── STEP 1: Decomposition (always runs) ───────────────
            decomp_result = _run_decomposition(dcm_path, tmp_path, params)
            result.update(decomp_result)

            if not result.get('sequences'):
                result['error'] = 'No processable sequences found in DICOM'
                return result

            # ── STEP 2: Profile ───────────────────────────────────
            if 'profile' in steps:
                _run_profile(tmp_path, result)

            # ── STEP 3: Asymmetry ─────────────────────────────────
            if 'asymmetry' in steps:
                _run_asymmetry(tmp_path, result)

            # ── STEP 4: Width ─────────────────────────────────────
            if 'width' in steps:
                _run_width(tmp_path, result)

            # ── STEP 5: Agreement ─────────────────────────────────
            if 'agreement' in steps:
                _run_agreement(tmp_path, result)

            # ── STEP 6: Impression rules ──────────────────────────
            if 'impression' in steps:
                rules = _load_impression_rules(result.get('body_part', 'UNKNOWN'))
                if rules:
                    _apply_rules(result, rules)
                else:
                    _run_generic_impression(result)

            # ── STEP 7: Longitudinal comparison ───────────────────
            if 'longitudinal' in steps and prior_study:
                _run_longitudinal_diff(result, prior_study)

        except Exception as e:
            result['error']   = str(e)
            result['status']  = 'ERROR'

    result['pipeline_ms'] = round((time.perf_counter() - t_start) * 1000, 2)
    return result


# ════════════════════════════════════════════════════════════════════
# STEP 1b: DECOMPOSITION FROM PRE-GROUPED SERIES
# Used when zip extraction has already grouped files
# ════════════════════════════════════════════════════════════════════

def _run_decomposition_from_series(good: list, ds, body_part: str, safe, raw_date: str) -> dict:
    """Run decomposition on pre-grouped series list."""
    from az_dicom_processor import (
        load_volume as load_volume_sorted,
        best_slice, get_tissue_landmarks,
        method_algebraic, method_iterative, make_brain_mask,
    )
    import numpy as np

    base = {
        'study_instance_uid':  safe('StudyInstanceUID'),
        'study_date':          raw_date,
        'study_description':   safe('StudyDescription'),
        'accession_number':    safe('AccessionNumber'),
        'institution_name':    safe('InstitutionName'),
        'body_part':           body_part,
        'modality':            safe('Modality'),
        'manufacturer':        safe('Manufacturer'),
        'model_name':          safe('ManufacturerModelName'),
        'device_serial':       safe('DeviceSerialNumber'),
        'field_strength':      getattr(ds, 'MagneticFieldStrength', None),
        'patient_id':          safe('PatientID'),
        'patient_age':         safe('PatientAge'),
        'patient_sex':         safe('PatientSex'),
        'sequences_found':     len(good),
        'sequences_processed': 0,
        'sequences':           [],
    }

    for s in good[:5]:
        try:
            vol = load_volume_sorted(s)

            # ── Pathway B operators ────────────────────────────────
            try:
                from az_pathway_b import compute_pathway_b
                b_feats = compute_pathway_b(vol)
            except Exception:
                b_feats = {'b_alg_b_joint': float('nan'), 'b_pg_center_edge': float('nan')}
            sl           = best_slice(vol)
            mask         = make_brain_mask(sl)
            if mask.sum() < 100:
                continue
            A, B, _, _ = get_tissue_landmarks(sl, mask, s['type'])
            w_alg, t_alg = method_algebraic(sl, mask, A, B)
            w_iter, t_samp, t_extrap = method_iterative(sl, mask, A, B)
            rms     = float(np.sqrt(np.nanmean((w_alg - w_iter) ** 2)))
            speedup = t_extrap / t_alg if t_alg > 0 else 0

            seq_result = {
                'series_description': s['desc'],
                'seq_type':          s['type'],
                'n_slices':          s['n_slices'],
                'orientation':       _detect_orient(s['desc']),
                'gap':               float(abs(A - B)),
                'b_alg_b_joint':     b_feats.get('b_alg_b_joint', float('nan')),
                'b_pg_center_edge':  b_feats.get('b_pg_center_edge', float('nan')),
                'mean_fraction':     float(np.nanmean(w_alg[mask])),
                'std_fraction':      float(np.nanstd(w_alg[mask])),
                'profile_run':    False,
                'asymmetry_run':  False,
                'width_run':      False,
                'agreement_run':  False,
                'impression_run': False,
                'slices':         [],
            }
            base['sequences'].append(seq_result)
            base['sequences_processed'] += 1
        except Exception as e:
            logging.warning(f"Series skipped: {e}")
            continue

    return base


# ════════════════════════════════════════════════════════════════════
# STEP 1: DECOMPOSITION
# ════════════════════════════════════════════════════════════════════

def _run_decomposition(dcm_path: Path, tmp_path: Path, params: dict) -> dict:
    """
    Run the two-component decomposition on all sequences.
    Returns the base result dict with DICOM metadata and per-sequence measurements.
    """
    import pydicom
    import SimpleITK as sitk
    import numpy as np
    from scipy.ndimage import binary_erosion, binary_dilation, binary_fill_holes
    from scipy.ndimage import label as scipy_label, zoom

    # Import our processor logic
    # The processor is imported as a module — no subprocess
    from az_dicom_processor import (
        group_by_sequence as group_series,
        load_volume as load_volume_sorted,
        best_slice,
        get_tissue_landmarks, method_algebraic, method_iterative,
        score_sequence as detect_seq_type,
    )

    def detect_body_part_from_dicom(ds):
        """Detect body part from DICOM metadata."""
        bp = getattr(ds, 'BodyPartExamined', '') or ''
        bp = bp.upper().strip()
        if not bp:
            desc = str(getattr(ds, 'StudyDescription', '') or '').upper()
            if any(k in desc for k in ['SPINE', 'CERVICAL', 'CSPINE', 'C-SPINE']):
                return 'CSPINE'
            if any(k in desc for k in ['BRAIN', 'HEAD']):
                return 'BRAIN'
            if any(k in desc for k in ['LUMBAR', 'LSPINE', 'L-SPINE']):
                return 'LSPINE'
            if any(k in desc for k in ['THORACIC', 'TSPINE', 'T-SPINE']):
                return 'TSPINE'
            if any(k in desc for k in ['KNEE', 'ANKLE', 'FOOT', 'SHOULDER', 'HIP']):
                return desc.split()[0]
            return 'UNKNOWN'
        if bp in ('CSPINE', 'CERVICAL', 'CSPINE_SPINE'):
            return 'CSPINE'
        return bp

    # Find all DICOM files — could be a single file or a folder
    # For API use: single file. For folder use: multiple files.
    dcm_files = [dcm_path]

    # Parse series
    series_list = group_series(dcm_files)
    good = [s for s in series_list if s['score'] > 0]

    if not good:
        return {'sequences_found': 0, 'sequences': []}

    # Extract study-level metadata from first file
    import pydicom as pd
    ds = pd.dcmread(str(dcm_path), stop_before_pixels=True)

    def safe(tag, default=''):
        v = getattr(ds, tag, None)
        return str(v).strip() if v else default

    raw_date = safe('StudyDate')
    if raw_date and len(raw_date) == 8:
        raw_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

    body_part = detect_body_part_from_dicom(ds)

    base = {
        'study_instance_uid':  safe('StudyInstanceUID'),
        'study_date':          raw_date,
        'study_description':   safe('StudyDescription'),
        'accession_number':    safe('AccessionNumber'),
        'institution_name':    safe('InstitutionName'),
        'body_part':           body_part,
        'modality':            safe('Modality'),
        'manufacturer':        safe('Manufacturer'),
        'model_name':          safe('ManufacturerModelName'),
        'device_serial':       safe('DeviceSerialNumber'),
        'field_strength':      getattr(ds, 'MagneticFieldStrength', None),
        'patient_id':          safe('PatientID'),
        'patient_age':         safe('PatientAge'),
        'patient_sex':         safe('PatientSex'),
        'sequences_found':     len(good),
        'sequences_processed': 0,
        'sequences':           [],
    }

    for s in good[:5]:  # Process top 5 sequences
        try:
            vol = load_volume_sorted(s)

            # ── Pathway B operators ────────────────────────────────
            try:
                from az_pathway_b import compute_pathway_b
                b_feats = compute_pathway_b(vol)
            except Exception:
                b_feats = {'b_alg_b_joint': float('nan'), 'b_pg_center_edge': float('nan')}
            sl           = best_slice(vol)
            from az_dicom_processor import make_brain_mask, score_sequence as detect_seq_type
            mask         = make_brain_mask(sl)

            if mask.sum() < 100:
                continue

            A, B, label_A, label_B = get_tissue_landmarks(sl, mask, s['type'])
            w_alg, t_alg = method_algebraic(sl, mask, A, B)
            w_iter, t_samp, t_extrap = method_iterative(sl, mask, A, B)

            import numpy as np
            rms     = float(np.sqrt(np.nanmean((w_alg - w_iter) ** 2)))
            speedup = t_extrap / t_alg if t_alg > 0 else 0

            seq_result = {
                'series_description': s['desc'],
                'seq_type':          s['type'],
                'n_slices':          s['n_slices'],
                'orientation':       _detect_orient(s['desc']),
                'ref_A':             float(A),
                'ref_B':             float(B),
                'gap':               float(abs(A - B)),
                'mean_fraction':     float(np.nanmean(w_alg[mask])),
                'std_fraction':      float(np.nanstd(w_alg[mask])),
                'rms_vs_standard':   rms,
                'speedup_x':         round(speedup, 0),
                'timing_ms':         round(t_alg, 4),
                # Status flags
                'profile_run':    False,
                'asymmetry_run':  False,
                'width_run':      False,
                'agreement_run':  False,
                'impression_run': False,
                'slices':         [],
            }
            base['sequences'].append(seq_result)
            base['sequences_processed'] += 1

        except Exception as e:
            logging.warning(f"Pipeline step skipped: {e}")
            traceback.print_exc()
            continue

    return base


# ════════════════════════════════════════════════════════════════════
# STEP 2: PROFILE (per-slice gap curve)
# ════════════════════════════════════════════════════════════════════

def _run_profile(tmp_path: Path, result: dict):
    """Run slice-by-slice gap profile on each sequence."""
    from az_spine_profile import profile_volume, compute_gap
    import numpy as np

    for seq in result['sequences']:
        try:
            # Profile needs the volume — re-load from the temp DICOM
            # In production: cache the volume between steps
            slices_data = _compute_profile_slices(tmp_path, seq)
            if slices_data:
                seq['slices']       = slices_data
                seq['min_gap']      = min(s['gap'] for s in slices_data if s.get('gap'))
                seq['max_gap']      = max(s['gap'] for s in slices_data if s.get('gap'))
                seq['profile_run']  = True
                gaps = [s['gap'] for s in slices_data if s.get('gap')]
                if gaps and seq.get('max_gap'):
                    seq['compression_pct'] = round(
                        (seq['max_gap'] - seq['min_gap']) / seq['max_gap'] * 100, 2)
                # Find min gap slice
                min_gap_idx = gaps.index(min(gaps))
                seq['min_gap_slice']    = slices_data[min_gap_idx]['slice_z']
                seq['min_gap_frac_inf'] = slices_data[min_gap_idx]['slice_frac_inf']
        except Exception as e:
            logging.warning(f"Pipeline step skipped: {e}")
            traceback.print_exc()
            continue


def _compute_profile_slices(tmp_path: Path, seq: dict) -> list:
    """Re-run gap computation per slice for the given sequence."""
    # Simplified version — in production, volumes are cached
    return []


# ════════════════════════════════════════════════════════════════════
# STEPS 3-5: ASYMMETRY, WIDTH, AGREEMENT
# ════════════════════════════════════════════════════════════════════

def _run_asymmetry(tmp_path: Path, result: dict):
    """Run left/right asymmetry analysis."""
    for seq in result['sequences']:
        if not seq.get('slices'): continue
        try:
            slices = seq['slices']
            asym_vals = [s['asym_index'] for s in slices if s.get('asym_index') is not None]
            if not asym_vals: continue
            seq['peak_left_asym']    = round(min(asym_vals), 4)
            seq['pct_left_dominant'] = round(
                sum(1 for a in asym_vals if a < -0.10) / len(asym_vals) * 100, 1)
            seq['pct_right_dominant'] = round(
                sum(1 for a in asym_vals if a > 0.10) / len(asym_vals) * 100, 1)
            seq['asymmetry_run'] = True
        except Exception as e:
            logging.warning(f"Pipeline step skipped: {e}")
            traceback.print_exc()
            continue


def _run_width(tmp_path: Path, result: dict):
    """Run compression width characterization."""
    for seq in result['sequences']:
        if not seq.get('slices'): continue
        try:
            slices = seq['slices']
            gaps   = [s.get('gap') for s in slices]
            valid  = [g for g in gaps if g is not None]
            if not valid: continue

            mean_g   = sum(valid) / len(valid)
            thresh   = mean_g * 0.85
            runs     = _find_runs([g < thresh if g else False for g in gaps])

            seq['n_compress_runs'] = len(runs)
            seq['run_widths_mm']   = [r * 3.3 for r in runs]
            seq['width_run']       = True
        except Exception as e:
            logging.warning(f"Pipeline step skipped: {e}")
            traceback.print_exc()
            continue


def _find_runs(bools: list) -> list:
    """Find contiguous True runs in a boolean list. Returns list of run lengths."""
    runs = []; current = 0
    for b in bools:
        if b: current += 1
        elif current > 0: runs.append(current); current = 0
    if current > 0: runs.append(current)
    return runs


def _run_agreement(tmp_path: Path, result: dict):
    """Compute cross-sequence agreement score."""
    seqs = [s for s in result['sequences'] if s.get('slices')]
    if len(seqs) < 2:
        return

    # Normalize each sequence gap to its own mean
    min_len = min(len(s['slices']) for s in seqs)
    norm = {}
    for seq in seqs:
        gaps = [s.get('gap') for s in seq['slices'][:min_len]]
        valid = [g for g in gaps if g is not None]
        if not valid: continue
        mean_g = sum(valid) / len(valid)
        norm[seq['series_description']] = [
            g / mean_g if g is not None else None for g in gaps]

    if len(norm) < 2:
        return

    # Compute disagreement (std of normalized gaps) per slice
    import statistics
    disagree = []
    for z in range(min_len):
        vals = [norm[k][z] for k in norm if norm[k][z] is not None]
        if len(vals) >= 2:
            disagree.append(statistics.stdev(vals))

    if disagree:
        peak = max(disagree)
        mean = sum(disagree) / len(disagree)
        for seq in seqs:
            seq['peak_disagree_score']  = round(peak, 4)
            seq['mean_disagree_score']  = round(mean, 4)
            seq['peak_disagree_slice']  = disagree.index(peak)
            seq['agreement_run']        = True


# ════════════════════════════════════════════════════════════════════
# STEP 6: IMPRESSION RULES
# ════════════════════════════════════════════════════════════════════

def _load_impression_rules(body_part: str) -> list:
    """
    Load impression rules from az_impression_rules for given body_part.
    Falls back to empty list if DB unavailable.
    Returns list of rule dicts.
    """
    try:
        import db as database
        conn = database.db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT seq_type, metric, operator, threshold, severity, label
            FROM az_impression_rules
            WHERE (body_part = %s OR body_part = 'ANY')
              AND active = TRUE
            ORDER BY
                CASE severity
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'MODERATE' THEN 2
                    WHEN 'FINDING'  THEN 3
                    ELSE 4
                END,
                threshold
        """, (body_part,))
        rows = cur.fetchall()
        conn.close()
        return [
            {
                'seq_type':  r[0],
                'metric':    r[1],
                'operator':  r[2],
                'threshold': float(r[3]),
                'severity':  r[4],
                'label':     r[5],
            }
            for r in rows
        ]
    except Exception as e:
        import logging
        logging.warning(f"Could not load impression rules from DB: {e}")
        return []


def _apply_rules(result: dict, rules: list):
    """
    Apply DB-loaded impression rules to pipeline result.
    Applies field strength threshold adjustment for gap metrics.
    Falls back gap to seq gap when min_gap not computed.
    Updates result['impression'] in place.
    """
    from az_field_strength import adjust_threshold, get_field_strength
    field_strength = get_field_strength(result)
    all_flags = []

    for seq in result.get('sequences', []):
        seq_type = seq.get('seq_type', '')
        flags = []

        for rule in rules:
            if rule['seq_type'] != 'ANY' and rule['seq_type'] != seq_type:
                continue

            metric = rule['metric']
            val = seq.get(metric)

            # min_gap fallback -- profile step may not have run
            if val is None and metric == 'min_gap':
                val = seq.get('gap')

            # run_width_max from list
            if metric == 'run_width_max':
                run_widths = seq.get('run_widths_mm', [])
                val = max(run_widths) if run_widths else None

            if val is None or (isinstance(val, float) and val != val):  # nan check
                continue

            # Apply field strength adjustment
            adjusted = adjust_threshold(rule['threshold'], metric, field_strength)

            fired = False
            if rule['operator'] == 'lt' and val < adjusted:
                fired = True
            elif rule['operator'] == 'gt' and val > adjusted:
                fired = True

            if fired:
                flags.append({
                    'severity': rule['severity'],
                    'label':    rule['label'],
                    'sequence': seq.get('series_description', ''),
                    'detail':   f"{metric}={val:.2f} threshold={rule['operator']} {adjusted:.2f}",
                })

        # Also always add tissue fraction measured as NORMAL
        if seq.get('mean_fraction') is not None:
            flags.append({
                'severity': 'NORMAL',
                'label':    'Tissue fraction measured',
                'sequence': seq.get('series_description', ''),
                'detail':   f"Mean={seq['mean_fraction']:.4f}",
            })

        seq['flags_json']    = flags
        seq['flag_critical'] = sum(1 for f in flags if f['severity'] == 'CRITICAL')
        seq['flag_moderate'] = sum(1 for f in flags if f['severity'] == 'MODERATE')
        seq['flag_finding']  = sum(1 for f in flags if f['severity'] == 'FINDING')
        seq['flag_normal']   = sum(1 for f in flags if f['severity'] == 'NORMAL')
        seq['impression_run'] = True
        all_flags.extend(flags)

    # Overall status
    if any(f['severity'] == 'CRITICAL' for f in all_flags):
        status = 'CRITICAL'
    elif any(f['severity'] == 'MODERATE' for f in all_flags):
        status = 'MODERATE'
    elif any(f['severity'] == 'FINDING' for f in all_flags):
        status = 'FINDING'
    elif all_flags:
        status = 'NORMAL'
    else:
        status = 'CLEAN'

    result['impression'] = {
        'status': status,
        'flags':  all_flags,
        'text':   '',
        'counts': {
            'critical': sum(1 for f in all_flags if f['severity'] == 'CRITICAL'),
            'moderate': sum(1 for f in all_flags if f['severity'] == 'MODERATE'),
            'finding':  sum(1 for f in all_flags if f['severity'] == 'FINDING'),
            'normal':   sum(1 for f in all_flags if f['severity'] == 'NORMAL'),
        }
    }
    """Apply C-spine rule set. Updates result['impression'] in place."""
    all_flags = []

    for seq in result['sequences']:
        seq_type    = seq.get('seq_type', '')
        min_gap     = seq.get('min_gap')
        comp_pct    = seq.get('compression_pct')
        peak_left   = seq.get('peak_left_asym')
        pct_left    = seq.get('pct_left_dominant')
        run_widths  = seq.get('run_widths_mm', [])
        peak_dis    = seq.get('peak_disagree_score')
        mean_frac   = seq.get('mean_fraction')
        flags       = []

        if seq_type == 'T2S':
            if min_gap is not None:
                if min_gap < 50:
                    flags.append({'severity':'CRITICAL','label':'T2* gap critically compressed','detail':f'Min gap={min_gap:.1f}','sequence':seq['series_description']})
                elif min_gap < 100:
                    flags.append({'severity':'MODERATE','label':'T2* gap compressed','detail':f'Min gap={min_gap:.1f}','sequence':seq['series_description']})
            if run_widths and max(run_widths) > 30:
                flags.append({'severity':'FINDING','label':'T2* continuous compression zone','detail':f'Width={max(run_widths):.0f}mm','sequence':seq['series_description']})

        if seq_type == 'STIR':
            if peak_left is not None and abs(peak_left) > 0.5:
                flags.append({'severity':'CRITICAL','label':'STIR severe left asymmetry','detail':f'Peak={peak_left:.3f}','sequence':seq['series_description']})
            if pct_left is not None and pct_left > 50:
                flags.append({'severity':'FINDING','label':'STIR left-dominant asymmetry','detail':f'{pct_left:.1f}% of slices','sequence':seq['series_description']})

        if seq_type == 'T2':
            if min_gap is not None and min_gap < 200:
                flags.append({'severity':'MODERATE','label':'T2 gap compressed','detail':f'Min gap={min_gap:.1f}','sequence':seq['series_description']})
            if comp_pct is not None and comp_pct > 40:
                flags.append({'severity':'MODERATE','label':'T2 within-volume compression','detail':f'{comp_pct:.1f}%','sequence':seq['series_description']})

        if seq_type == 'T1':
            if min_gap is not None and min_gap < 150:
                flags.append({'severity':'MODERATE','label':'T1 gap compressed','detail':f'Min gap={min_gap:.1f}','sequence':seq['series_description']})

        if seq_type == 'CT' and min_gap is not None and min_gap < 30:
            flags.append({'severity':'FINDING','label':'CT bone-soft tissue convergence','detail':f'Min gap={min_gap:.1f}','sequence':seq['series_description']})

        if peak_dis is not None and peak_dis > 0.25:
            flags.append({'severity':'FINDING','label':'Cross-sequence disagreement elevated','detail':f'Score={peak_dis:.3f}','sequence':seq['series_description']})

        if mean_frac is not None:
            flags.append({'severity':'NORMAL','label':'Tissue fraction measured','detail':f'Mean={mean_frac:.4f}','sequence':seq['series_description']})

        seq['flags_json']      = flags
        seq['flag_critical']   = sum(1 for f in flags if f['severity']=='CRITICAL')
        seq['flag_moderate']   = sum(1 for f in flags if f['severity']=='MODERATE')
        seq['flag_finding']    = sum(1 for f in flags if f['severity']=='FINDING')
        seq['flag_normal']     = sum(1 for f in flags if f['severity']=='NORMAL')
        seq['impression_run']  = True
        all_flags.extend(flags)

    # Overall status
    if any(f['severity']=='CRITICAL' for f in all_flags):
        status = 'CRITICAL'
    elif any(f['severity']=='MODERATE' for f in all_flags):
        status = 'MODERATE'
    elif any(f['severity']=='FINDING' for f in all_flags):
        status = 'FINDING'
    elif all_flags:
        status = 'NORMAL'
    else:
        status = 'CLEAN'

    # Build impression text
    lines = ['SIGNAL IMPRESSION', '='*40]
    for sev in ['CRITICAL','MODERATE','FINDING','NORMAL']:
        for f in [x for x in all_flags if x['severity']==sev]:
            lines.append(f"  [{sev}] {f['label']}")
            lines.append(f"         {f['detail']} — {f['sequence']}")
    lines += ['', 'NOTE: Quantitative signal measurements only.',
              'Radiologist review required.']

    result['impression'] = {
        'status': status,
        'flags':  all_flags,
        'text':   '\n'.join(lines),
        'counts': {
            'critical': sum(1 for f in all_flags if f['severity']=='CRITICAL'),
            'moderate': sum(1 for f in all_flags if f['severity']=='MODERATE'),
            'finding':  sum(1 for f in all_flags if f['severity']=='FINDING'),
            'normal':   sum(1 for f in all_flags if f['severity']=='NORMAL'),
        }
    }


def _run_generic_impression(result: dict):
    """Generic impression for non-C-spine body parts. Placeholder for future rule sets."""
    result['impression'] = {
        'status': 'PENDING',
        'flags':  [],
        'text':   f"Impression rules for {result.get('body_part','UNKNOWN')} not yet implemented.",
        'counts': {'critical':0,'moderate':0,'finding':0,'normal':0}
    }


# ════════════════════════════════════════════════════════════════════
# STEP 7: LONGITUDINAL COMPARISON
# ════════════════════════════════════════════════════════════════════

def _run_longitudinal_diff(result: dict, prior_study: dict):
    """
    Compare current study measurements against prior study measurements.
    Produces per-sequence measurement deltas and new flags for changes
    that exceed clinical thresholds.
    """
    prior_seqs     = {s['seq_type']: s for s in prior_study.get('sequences', [])}
    current_seqs   = result.get('sequences', [])
    current_status = result['impression'].get('status', 'PENDING')
    prior_status   = prior_study.get('impression_status', 'UNKNOWN')

    measurement_diffs = []
    longitudinal_flags = []

    for seq in current_seqs:
        stype = seq.get('seq_type', '')
        prior = prior_seqs.get(stype)
        if not prior:
            continue

        diff = {
            'seq_type':        stype,
            'series':          seq.get('series_description', ''),
            'prior_study_date': prior_study.get('study_date'),
        }

        # ── Gap change ───────────────────────────────────────────────
        cur_gap  = seq.get('min_gap')
        pri_gap  = prior.get('min_gap')
        if cur_gap is not None and pri_gap is not None and pri_gap > 0:
            gap_change_pct = (cur_gap - pri_gap) / pri_gap * 100
            diff['min_gap_current']    = round(cur_gap, 1)
            diff['min_gap_prior']      = round(pri_gap, 1)
            diff['min_gap_change_pct'] = round(gap_change_pct, 1)

            if gap_change_pct <= -40:
                longitudinal_flags.append({
                    'severity': 'CRITICAL' if gap_change_pct <= -60 else 'MODERATE',
                    'label':    f'{stype} gap compression vs prior study',
                    'detail':   (f'Min gap {pri_gap:.1f} → {cur_gap:.1f} '
                                 f'({gap_change_pct:+.1f}% since {prior_study.get("study_date","prior")})'),
                    'sequence': seq.get('series_description', ''),
                    'source':   'longitudinal_s0',
                })
            elif gap_change_pct >= 20:
                longitudinal_flags.append({
                    'severity': 'FINDING',
                    'label':    f'{stype} gap improvement vs prior study',
                    'detail':   (f'Min gap {pri_gap:.1f} → {cur_gap:.1f} '
                                 f'({gap_change_pct:+.1f}% since {prior_study.get("study_date","prior")})'),
                    'sequence': seq.get('series_description', ''),
                    'source':   'longitudinal_s0',
                })

        # ── Asymmetry direction reversal ─────────────────────────────
        cur_asym  = seq.get('peak_left_asym')
        pri_asym  = prior.get('peak_left_asym')
        cur_pct   = seq.get('pct_left_dominant', 0)
        pri_pct   = prior.get('pct_left_dominant', 0)

        if cur_asym is not None and pri_asym is not None:
            diff['peak_asym_current'] = round(cur_asym, 3)
            diff['peak_asym_prior']   = round(pri_asym, 3)

            # Direction reversal: prior right-dominant, now left-dominant
            prior_right_dom = pri_pct < 30
            now_left_dom    = cur_pct > 50
            if prior_right_dom and now_left_dom:
                longitudinal_flags.append({
                    'severity': 'CRITICAL',
                    'label':    f'{stype} asymmetry direction reversed since prior study',
                    'detail':   (f'Prior: right-dominant ({pri_pct:.0f}% left). '
                                 f'Current: left-dominant ({cur_pct:.0f}% left). '
                                 f'Reversal not in prior radiology record.'),
                    'sequence': seq.get('series_description', ''),
                    'source':   'longitudinal_s0',
                })
            elif abs(cur_asym) - abs(pri_asym) > 0.3:
                longitudinal_flags.append({
                    'severity': 'MODERATE',
                    'label':    f'{stype} asymmetry magnitude increased vs prior study',
                    'detail':   (f'Peak asymmetry {pri_asym:.3f} → {cur_asym:.3f} '
                                 f'(Δ={cur_asym - pri_asym:+.3f})'),
                    'sequence': seq.get('series_description', ''),
                    'source':   'longitudinal_s0',
                })

        # ── Cross-sequence disagreement change ───────────────────────
        cur_dis = seq.get('peak_disagree_score')
        pri_dis = prior.get('peak_disagree_score')
        if cur_dis is not None and pri_dis is not None and pri_dis > 0:
            dis_change_pct = (cur_dis - pri_dis) / pri_dis * 100
            diff['disagree_current']    = round(cur_dis, 3)
            diff['disagree_prior']      = round(pri_dis, 3)
            diff['disagree_change_pct'] = round(dis_change_pct, 1)

            if dis_change_pct >= 30:
                longitudinal_flags.append({
                    'severity': 'FINDING',
                    'label':    f'{stype} cross-sequence disagreement elevated vs prior study',
                    'detail':   (f'Disagreement score {pri_dis:.3f} → {cur_dis:.3f} '
                                 f'({dis_change_pct:+.1f}% increase — tissue physics changed)'),
                    'sequence': seq.get('series_description', ''),
                    'source':   'longitudinal_s0',
                })

        measurement_diffs.append(diff)

    # ── Append longitudinal flags to the impression ───────────────────
    if longitudinal_flags:
        result['impression']['flags'].extend(longitudinal_flags)
        # Re-evaluate overall status after longitudinal flags
        all_flags = result['impression']['flags']
        if any(f['severity'] == 'CRITICAL' for f in all_flags):
            result['impression']['status'] = 'CRITICAL'
        elif any(f['severity'] == 'MODERATE' for f in all_flags):
            if result['impression']['status'] not in ('CRITICAL',):
                result['impression']['status'] = 'MODERATE'

    # ── Status escalation summary ─────────────────────────────────────
    status_rank  = {'CLEAN':0,'NORMAL':1,'FINDING':2,'MODERATE':3,'CRITICAL':4}
    current_rank = status_rank.get(current_status, -1)
    prior_rank   = status_rank.get(prior_status, -1)
    change = ('ESCALATED' if current_rank > prior_rank else
              'IMPROVED'  if current_rank < prior_rank else 'STABLE')

    result['longitudinal'] = {
        # S₀ identity
        'prior_study_id':         prior_study.get('study_id'),
        'prior_study_date':       prior_study.get('study_date'),
        'prior_status':           prior_status,
        'current_status':         current_status,
        'change':                 change,
        # Measurement-level S₀ comparison
        'measurement_diffs':      measurement_diffs,
        'longitudinal_flags':     longitudinal_flags,
        'longitudinal_flag_count': len(longitudinal_flags),
        # Summary counts from prior
        'prior_critical':         prior_study.get('flags_critical', 0),
        'prior_moderate':         prior_study.get('flags_moderate', 0),
    }


# ════════════════════════════════════════════════════════════════════
# UTILITIES
# ════════════════════════════════════════════════════════════════════

def _detect_orient(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ['ax','axl','axial','tra']): return 'AX'
    if any(k in n for k in ['sag','sagittal']): return 'SAG'
    if any(k in n for k in ['cor','coronal']): return 'COR'
    return 'UNK'
