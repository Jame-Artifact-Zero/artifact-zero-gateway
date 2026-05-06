"""
preimpression/merge.py
======================
Folds the multi-body-part pre-impression analyzer output into the existing
dicom_processor_api result dict so that:

  1. New metrics live alongside old metrics on the appropriate seq dicts
     so _apply_rules can fire DB rules on them.
  2. Body-part-specific findings (markers, level_summaries, cord_track_3d,
     brain_findings, joint_findings, breast_findings) are attached at the
     top level of `result`.
  3. The existing impression flags array is extended with new flags.
  4. _SEQ_KEEP additions documented in this file are applied via
     dicom_return.py separately.

This module is called from STEP 7 in dicom_processor_api.py:

    if 'preimpression' in steps:
        from preimpression.pipeline import run_pipeline_from_series
        from preimpression.merge import merge_into_result
        try:
            preimp = run_pipeline_from_series(
                series_list=existing_series,
                body_part=result.get('body_part', 'UNKNOWN'),
            )
            merge_into_result(result, preimp)
        except Exception as e:
            result['preimpression_error'] = str(e)

It is critical that merge_into_result() runs BEFORE STEP 6 (_apply_rules)
so the DB rules see the new metrics. Recommended step order:

    STEP 1   az_dicom_processor.py          (decomposition)
    STEP 1b  az_pathway_b.py                (pathway B)
    STEP 2   az_spine_profile.py            (existing stub, returns [])
    STEP 3   _run_asymmetry()
    STEP 4   _run_width()
    STEP 5   _run_agreement()
    STEP 6a  _run_preimpression() ←─── NEW (merge happens here)
    STEP 6b  _load_impression_rules()       (DB lookup unchanged)
    STEP 6c  _apply_rules()                 (now sees both old and new metrics)

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations
from typing import Optional


# ============================================================================
# Required _SEQ_KEEP additions for IP-protected response
# ============================================================================
# These keys must be added to _SEQ_KEEP in dicom_return.py so that the new
# metrics flow through to the API response. Boundary polygon arrays are
# intentionally OMITTED from _SEQ_KEEP (too detailed for standard response).
# Callers wanting them should request format=full or use the dedicated
# /preimpression endpoint.
PREIMP_SEQ_KEEP_ADDITIONS = {
    # v3 cspine additions
    'lr_sum_min_mm', 'lr_sum_mean_mm',
    'lesion_side',
    # Spine cord-canal geometry
    'space_min_mm', 'space_mean_mm', 'space_max_mm',
    'left_space_mm', 'right_space_mm',
    'asym_lr', 'asym_lr_abs',
    'cord_area_mm2',

    # Brain
    'midline_shift_mm', 'midline_shift_abs_mm',
    'ventricle_asym_lr', 'ventricle_asym_abs',
    'brain_asym_lr', 'brain_asym_abs',
    'flair_lesion_count', 'flair_lesion_total_area_mm2',

    # Joint
    'effusion_volume_mm3', 'marrow_edema_volume_mm3',
    'effusion_total_area_mm2', 'marrow_edema_total_area_mm2',

    # Breast
    'mass_diameter_mm', 'mass_area_mm2',
    'tissue_asym_lr', 'tissue_asym_abs',
    'mass_count_asym',
}

# Keys NEVER added to _SEQ_KEEP — too detailed for standard response.
# Available via /preimpression endpoint (full format) only.
PREIMP_SEQ_DETAIL_ONLY = {
    'cord_boundary_3d', 'canal_boundary_3d', 'radial_angles_rad',
    'cord_radii_mm', 'canal_radii_mm',
    'midline_col_per_row',
    'lesions_by_slice',
    'masses',
}


# ============================================================================
# Helpers — body-part-specific metric promotion onto seq dicts
# ============================================================================
def _seq_match(seq, target_seq_type):
    """Return True if seq's seq_type matches target. 'ANY' matches anything.
    seq_type matching is exact uppercase string comparison (per existing
    _apply_rules convention)."""
    if target_seq_type == 'ANY':
        return True
    return seq.get('seq_type') == target_seq_type


def _find_or_promote_seq(result, target_seq_types):
    """Find an existing seq dict in result['sequences'] whose seq_type matches
    one of target_seq_types. If none matches but we need to attach metrics
    somewhere, return the first seq (best-effort)."""
    seqs = result.get('sequences', [])
    if not seqs:
        return None
    for tst in target_seq_types:
        for seq in seqs:
            if _seq_match(seq, tst):
                return seq
    return seqs[0]


# ============================================================================
# Spine merge — attach per-level metrics to the axial T2 seq
# ============================================================================
def _merge_spine(result, preimp):
    """Spine analyzers produce per-level summaries. We attach the WORST level
    severity per metric to the seq dict so DB rules fire correctly. Per-level
    detail goes to a top-level result['preimpression_levels'] block.

    Why "worst level": the DB rules system fires per-seq, not per-level.
    A rule like `space_min_mm < 0.5 → CRITICAL` should fire if ANY level has
    space_min_mm < 0.5. Promoting the worst level gives us that semantic.
    """
    level_summaries = preimp.get('level_summaries', [])
    if not level_summaries:
        return

    # Worst across all levels:
    worst_space_min = min((ls['space_min_mm'] for ls in level_summaries),
                           default=float('inf'))
    worst_asym_abs = max((abs(ls['asym_lr_mean']) for ls in level_summaries),
                          default=0.0)

    # Find the axial T2 seq to attach to
    seq = _find_or_promote_seq(result, ['T2', 'T2FS'])
    if seq is None:
        return

    seq['space_min_mm'] = float(worst_space_min)
    seq['asym_lr_abs'] = float(worst_asym_abs)

    # v3 additions: lr_sum_min and lesion_side from worst level
    lr_sums = [ls['lr_sum_min_mm'] for ls in level_summaries
               if ls.get('lr_sum_min_mm') is not None]
    seq['lr_sum_min_mm'] = float(min(lr_sums)) if lr_sums else None

    # Lesion side from the worst-severity level
    def _sev_rank(ls):
        ranks = {'CRITICAL': 3, 'MODERATE': 2, 'FINDING': 1, 'NORMAL': 0}
        sev = max((f.get('severity', 'NORMAL') for f in ls.get('flags', [])),
                  key=lambda s: ranks.get(s, 0), default='NORMAL')
        return ranks.get(sev, 0)
    if level_summaries:
        worst_ls = max(level_summaries, key=_sev_rank)
        seq['lesion_side'] = worst_ls.get('lesion_side', 'unknown')
    else:
        seq['lesion_side'] = 'unknown'
    # Per-level detail at the top level
    result['preimpression_levels'] = level_summaries
    result['cord_track_3d'] = preimp.get('cord_track_3d', {})


# ============================================================================
# Brain merge — series-aggregate metrics
# ============================================================================
def _merge_brain(result, preimp):
    """Brain produces one set of metrics per study. Attach to the primary
    series (FLAIR if available, else T2 axial)."""
    bf = preimp.get('brain_findings', {})
    if not bf:
        return

    seq = _find_or_promote_seq(result, ['FLAIR', 'T2', 'T1'])
    if seq is None:
        return

    seq['midline_shift_mm'] = float(bf.get('max_midline_shift_mm', 0.0))
    seq['midline_shift_abs_mm'] = abs(float(bf.get('max_midline_shift_mm', 0.0)))
    seq['ventricle_asym_lr'] = float(bf.get('ventricle_asym_overall', 0.0))
    seq['ventricle_asym_abs'] = abs(float(bf.get('ventricle_asym_overall', 0.0)))

    flair = bf.get('flair_lesion_summary')
    if flair:
        # Attach lesion count to the FLAIR seq specifically (rule has
        # seq_type='FLAIR' for this metric)
        flair_seq = _find_or_promote_seq(result, ['FLAIR'])
        if flair_seq is not None:
            flair_seq['flair_lesion_count'] = int(flair.get('lesion_count', 0))
            flair_seq['flair_lesion_total_area_mm2'] = float(
                flair.get('lesion_total_area_mm2', 0.0)
            )

    result['brain_findings'] = bf


# ============================================================================
# Joint merge — volumetric summaries
# ============================================================================
def _merge_joint(result, preimp):
    """Joint analyzers produce series-aggregate effusion + marrow edema
    volumes. Attach to the primary fluid-sensitive seq."""
    jf = preimp.get('joint_findings', {})
    if not jf:
        return

    primary_kind = jf.get('sequence_kind', 'T2')
    seq = _find_or_promote_seq(result, [primary_kind, 'T2FS', 'PDFS', 'STIR', 'T2'])
    if seq is None:
        return

    eff = jf.get('effusion', {})
    edema = jf.get('marrow_edema', {})

    seq['effusion_volume_mm3'] = float(eff.get('estimated_volume_mm3', 0.0))
    seq['effusion_total_area_mm2'] = float(eff.get('total_area_mm2', 0.0))
    seq['marrow_edema_volume_mm3'] = float(edema.get('estimated_volume_mm3', 0.0))
    seq['marrow_edema_total_area_mm2'] = float(edema.get('total_area_mm2', 0.0))

    result['joint_findings'] = jf


# ============================================================================
# Breast merge — paired anatomy with mass detection
# ============================================================================
def _merge_breast(result, preimp):
    """Breast analyzer produces masses and tissue asymmetry. Attach largest
    mass diameter and tissue asymmetry to the primary series."""
    bf = preimp.get('breast_findings', {})
    if not bf:
        return

    primary_kind = bf.get('sequence_kind', 'T1POST')
    seq = _find_or_promote_seq(result, [primary_kind, 'T1POST', 'STIR', 'T2FS'])
    if seq is None:
        return

    masses = bf.get('masses', [])
    if masses:
        # Attach the largest mass's diameter (already sorted by analyzer)
        largest = masses[0]
        seq['mass_diameter_mm'] = float(largest.get('diameter_approx_mm', 0.0))
        seq['mass_area_mm2'] = float(largest.get('area_mm2', 0.0))
    else:
        seq['mass_diameter_mm'] = 0.0
        seq['mass_area_mm2'] = 0.0

    tissue_asym = float(bf.get('tissue_asym_lr', 0.0))
    seq['tissue_asym_lr'] = tissue_asym
    seq['tissue_asym_abs'] = abs(tissue_asym)

    n_left = int(bf.get('mass_count_left', 0))
    n_right = int(bf.get('mass_count_right', 0))
    total = n_left + n_right
    seq['mass_count_asym'] = (n_left - n_right) / total if total > 0 else 0.0

    result['breast_findings'] = bf


# ============================================================================
# Public API
# ============================================================================
def merge_into_result(result: dict, preimp: dict) -> dict:
    """Merge preimpression analyzer output into the existing pipeline result.

    Operates in place on `result` and also returns it for chainability.

    Routes by preimp['body_part_label']. Unknown labels are no-ops (the
    preimpression markers/metadata still get attached at top level for
    debugging, but no per-seq metrics are added).

    Args:
        result: existing pipeline result dict
        preimp: pre-impression analyzer output dict

    Returns:
        result (modified in place)
    """
    if not preimp or not isinstance(preimp, dict):
        return result

    # Always attach top-level preimpression block (markers, status, timing)
    # regardless of body part — useful for debugging and downstream consumers
    # that want the full output.
    result['preimpression'] = {
        'status':           preimp.get('status'),
        'body_part_label':  preimp.get('body_part_label'),
        'detected_body_part': preimp.get('detected_body_part'),
        'body_part_source': preimp.get('body_part_source'),
        'levels_detected':  preimp.get('levels_detected', {}),
        'markers':          preimp.get('markers', []),
        'slice_measurements': preimp.get('slice_measurements', []),
        'pipeline_version': preimp.get('pipeline_version'),
        'timing_ms':        preimp.get('timing_ms', {}),
    }

    # Promote per-body-part metrics onto seq dicts for DB rule firing
    label = preimp.get('body_part_label', '')
    if label in ('cervical_spine', 'thoracic_spine', 'lumbar_spine'):
        _merge_spine(result, preimp)
    elif label == 'brain':
        _merge_brain(result, preimp)
    elif label in ('knee', 'ankle', 'foot', 'shoulder', 'elbow', 'wrist', 'hand'):
        _merge_joint(result, preimp)
    elif label == 'breast':
        _merge_breast(result, preimp)
    # else: unsupported — top-level block already attached above

    # Pre-impression flags get appended into result['impression']['flags']
    # IF the existing impression has been populated. Otherwise they live
    # only in result['preimpression']['markers'][...].severity.
    #
    # _apply_rules is the canonical flag generator. We don't pre-fire flags
    # here — we let _apply_rules fire them based on the metrics we just
    # promoted to seq dicts. This keeps a single flag-generation path.

    return result


def run_preimpression_step(result: dict, work_dir: Optional[str] = None,
                            series_list: Optional[list] = None,
                            body_part: Optional[str] = None) -> dict:
    """STEP 7 entry point. Called from dicom_processor_api.py.

    Looks up the body part, runs the appropriate analyzer, merges the
    output into result. Designed to be safe-by-default: any error is
    caught and recorded on result['preimpression_error'] without breaking
    the surrounding pipeline.

    Args:
        result:      existing pipeline result dict (modified in place)
        work_dir:    unpacked DICOM directory (zip path mode)
        series_list: pre-loaded series list (in-memory mode, preferred when
                     called from inside dicom_processor_api.py to avoid
                     double DICOM reads)
        body_part:   override for body-part dispatch. If None, uses
                     result['body_part'] then falls back to autodetect.

    Returns:
        result (modified in place)
    """
    try:
        # Defer import so this module can be imported without the analyzers
        # package available at install time.
        from preimpression.pipeline import run_pipeline_from_series
    except ImportError:
        result['preimpression_error'] = 'preimpression package not available'
        return result

    if body_part is None:
        body_part = result.get('body_part', 'UNKNOWN')

    try:
        if series_list is not None:
            preimp = run_pipeline_from_series(
                series_list=series_list,
                body_part=body_part,
            )
        elif work_dir is not None:
            from preimpression.pipeline import run_pipeline
            preimp = run_pipeline(
                zip_path=None,
                work_dir=work_dir,
                body_part_override=body_part,
                # Note: this requires run_pipeline to accept work_dir without
                # zip_path. See pipeline.py refactor in this push.
            )
        else:
            result['preimpression_error'] = 'no series_list or work_dir provided'
            return result

        merge_into_result(result, preimp)

    except Exception as e:
        # Pipeline-internal failure: record but don't break the surrounding
        # pipeline. The existing gap+fraction findings stay intact.
        import traceback
        result['preimpression_error'] = str(e)
        result['preimpression_traceback'] = traceback.format_exc()

    return result
