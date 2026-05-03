"""
preimpression/pipeline.py
=========================
Multi-body-part pre-impression pipeline. Two entry points:

  run_pipeline(zip_path)
    Standalone mode — unpacks zip, scans, dispatches. Used by the
    /preimpression endpoint and the CLI.

  run_pipeline_from_series(series_list, body_part)
    In-memory mode — used by STEP 7 inside dicom_processor_api.py. The
    existing pipeline already loaded the series; we re-use it instead of
    double-reading DICOM files.

This is a refactor of the original az_pipeline_v2.py for landing into the
preimpression/ package. Imports use relative paths now.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations
import os
import json
import time
import zipfile
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

from .analyzers import (
    get_analyzer, supported_body_parts, ANALYZERS,
    group_series, detect_body_part, max_severity,
)


# ============================================================================
# Public API
# ============================================================================
def run_pipeline_from_series(series_list, body_part=None, study_meta=None,
                              scanner_meta=None):
    """In-memory dispatch — used by STEP 7 inside dicom_processor_api.py.

    Args:
        series_list: list of series dicts as produced by group_series() or by
                     the existing pipeline's series-loading step. Each dict
                     must have 'series_uid', 'series_description',
                     'orientation', 'modality', 'n_slices', 'files',
                     'sample_ds'.
        body_part:   uppercase body-part code matching detect_body_part_from_dicom
                     return values ('CSPINE', 'KNEE', 'BRAIN', etc.). If None,
                     falls back to detect_body_part(series_list).
        study_meta:  optional dict with study tags (study_uid, date,
                     description, modality, body_part). If None, derived from
                     the first series' sample_ds.
        scanner_meta: optional dict with scanner tags (manufacturer, model,
                      field_strength, institution). If None, derived from
                      the first series' sample_ds.

    Returns the pre-impression result dict in the same schema as run_pipeline.
    """
    t0 = time.perf_counter()

    # Determine body part
    if body_part:
        detected_bp = body_part.upper()
        bp_source = 'override'
    else:
        detected_bp = detect_body_part(series_list)
        bp_source = 'auto'

    analyzer = get_analyzer(detected_bp)
    if analyzer is None:
        # Distinguish 'unknown body part' from 'known but gated for validation'
        from .analyzers import _CODE_TO_ANALYZER, ANALYZERS, UNVALIDATED_BODY_PARTS
        cls = _CODE_TO_ANALYZER.get(detected_bp.upper() if detected_bp else '')
        if cls is None:
            for label, klass in ANALYZERS.items():
                if detected_bp and label.upper() == detected_bp.upper():
                    cls = klass
                    break

        if cls is not None and cls.body_part_label in UNVALIDATED_BODY_PARTS:
            status = 'UNVALIDATED_BODY_PART'
            reason = (
                f'{detected_bp} is registered but its detection algorithm '
                f'has not been validated against real radiologist-reported '
                f'studies. Flagging is gated until validation completes.'
            )
        else:
            status = 'UNSUPPORTED_BODY_PART'
            reason = f'no analyzer registered for body part {detected_bp}'

        return {
            'status': status,
            'reason': reason,
            'detected_body_part': detected_bp,
            'body_part_source': bp_source,
            'supported_body_parts': supported_body_parts(),
            'currently_validated_body_parts': ['cervical_spine'],
            'series_seen': [
                {k: s[k] for k in ('series_description', 'orientation',
                                    'modality', 'n_slices')}
                for s in series_list
            ],'timing_ms': {'total': (time.perf_counter() - t0) * 1000},
        }

    t_dispatch = time.perf_counter()
    result = analyzer.analyze(series_list, work_dir=None)
    t_analyze = time.perf_counter()

    # Wrap with shell metadata
    if study_meta is None or scanner_meta is None:
        # Prefer an MR/CT series over non-image modalities (SR, PR, etc.)
        ds = None
        for s in series_list:
            cand = s.get('sample_ds')
            if cand is None:
                continue
            mod = str(getattr(cand, 'Modality', '')).upper()
            if mod in ('MR', 'CT', 'PT', 'NM', 'XA', 'CR', 'DX'):
                ds = cand
                break
        if ds is None:
            ds = next((s.get('sample_ds') for s in series_list
                       if s.get('sample_ds') is not None), None)
        if ds is not None:
            if study_meta is None:
                study_meta = {
                    'study_uid':   str(getattr(ds, 'StudyInstanceUID', '')),
                    'date':        str(getattr(ds, 'StudyDate', '')),
                    'description': str(getattr(ds, 'StudyDescription', '')),
                    'modality':    str(getattr(ds, 'Modality', '')),
                    'body_part':   str(getattr(ds, 'BodyPartExamined', '')),
                }
            if scanner_meta is None:
                scanner_meta = {
                    'manufacturer':   str(getattr(ds, 'Manufacturer', '')),
                    'model':          str(getattr(ds, 'ManufacturerModelName', '')),
                    'field_strength': float(getattr(ds, 'MagneticFieldStrength', 0.0)),
                    'institution':    str(getattr(ds, 'InstitutionName', '')),
                }
        else:
            study_meta = study_meta or {}
            scanner_meta = scanner_meta or {}

    result.update({
        'detected_body_part': detected_bp,
        'body_part_source': bp_source,
        'study': study_meta,
        'scanner': scanner_meta,
        'timing_ms': {
            'dispatch': (t_dispatch - t0) * 1000,
            'analyze':  (t_analyze - t_dispatch) * 1000,
            'total':    (time.perf_counter() - t0) * 1000,
        },
        'pipeline_version': 'multi-body.v1',
        'generated_at':     datetime.now(timezone.utc).isoformat(),
    })
    return result


def run_pipeline(zip_path=None, body_part_override=None, work_dir=None,
                  keep_work=False):
    """Standalone dispatch — used by /preimpression endpoint and CLI.

    Args:
        zip_path: path to DICOM zip. If None, work_dir must already contain
                  unpacked DICOM files.
        body_part_override: 'CSPINE' | 'TSPINE' | 'LSPINE' | 'BRAIN' | etc.
                            None for auto-detect.
        work_dir: where to unpack. If None, uses a tempdir.
        keep_work: don't delete the work dir after running.
    """
    t0 = time.perf_counter()

    if work_dir is None:
        work_dir = tempfile.mkdtemp(prefix='az_pipeline_')
        cleanup = not keep_work
    else:
        cleanup = False
        os.makedirs(work_dir, exist_ok=True)

    try:
        if zip_path is not None:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(work_dir)
        t_unpack = time.perf_counter()

        series_list = group_series(work_dir)
        t_scan = time.perf_counter()

        result = run_pipeline_from_series(
            series_list=series_list,
            body_part=body_part_override,
        )

        # Add unpack/scan timing to the result
        timing = result.get('timing_ms', {})
        timing.update({
            'unpack': (t_unpack - t0) * 1000,
            'scan':   (t_scan - t_unpack) * 1000,
            'total':  (time.perf_counter() - t0) * 1000,
        })
        result['timing_ms'] = timing
        return result

    finally:
        if cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)


# ============================================================================
# CLI
# ============================================================================
def _print_summary(r):
    print(f"\nStatus: {r.get('status')}")
    print(f"Body part: {r.get('detected_body_part', '?')} "
          f"({r.get('body_part_source', '?')}) -> {r.get('body_part_label', '?')}")

    if r.get('status') in ('INSUFFICIENT_DATA', 'UNSUPPORTED_BODY_PART'):
        print(f"Reason: {r.get('reason') or r.get('detected_body_part')}")
        if 'series_seen' in r:
            print(f"\nSeries seen ({len(r['series_seen'])}):")
            for s in r['series_seen']:
                print(f"  {s.get('orientation', '?'):>4}  "
                      f"{s.get('series_description', '?'):<40}  "
                      f"{s.get('n_slices', '?')} slices")
        return

    print(f"Study: {r['study'].get('description', '?')} ({r['study'].get('date', '?')})")
    print(f"Scanner: {r['scanner'].get('manufacturer', '?')} "
          f"{r['scanner'].get('model', '?')}, "
          f"{r['scanner'].get('field_strength', '?')}T")

    counts = r.get('impression', {}).get('counts', {})
    print(f"\nFindings: {counts.get('critical', 0)} critical, "
          f"{counts.get('moderate', 0)} moderate, "
          f"{counts.get('finding', 0)} finding")

    if r.get('level_summaries'):
        print(f"\n{'Level':<8} {'n':>2}  {'cord_a':>6}  {'min_sp':>6}  "
              f"{'mean_sp':>7}  {'asym':>7}  {'L/R':>10}  status")
        for ls in r['level_summaries']:
            sev = max_severity(ls.get('flags', []))
            ams = '%+.3f' % ls['asym_lr_mean']
            lr = '%4.2f/%4.2f' % (ls['left_space_mm'], ls['right_space_mm'])
            print('%-8s %2d  %6.0f  %6.2f  %7.2f  %7s  %10s  %s' % (
                ls['level'], ls['n_slices'], ls['cord_area_mean_mm2'],
                ls['space_min_mm'], ls['space_mean_mm'], ams, lr, sev,
            ))

    if r.get('brain_findings'):
        bf = r['brain_findings']
        print(f"\nBrain analysis ({bf.get('n_brain_slices_analyzed', '?')} slices):")
        print(f"  Max midline shift: {bf.get('max_midline_shift_mm', 0):+.2f} mm "
              f"at z={bf.get('max_shift_at_z_mm', 0):.0f}")

    flags = r.get('impression', {}).get('flags', [])
    if flags:
        print('\nFlags:')
        for f in flags:
            print(f"  [{f['severity']:<8}] {f.get('level', '-'):<10} {f['label']}")

    print(f"\nPipeline: {r.get('pipeline_version', '')} — "
          f"{r['timing_ms']['total']:.0f} ms total")


def _cli():
    import argparse
    p = argparse.ArgumentParser(
        description='Run the multi-body-part pre-impression pipeline.'
    )
    p.add_argument('zip_path', nargs='?', default=None, help='path to DICOM zip')
    p.add_argument('--body-part', default=None,
                   help='override detected body part (CSPINE/TSPINE/LSPINE/BRAIN/...)')
    p.add_argument('--out', default=None, help='write JSON to this file')
    p.add_argument('--summary', action='store_true',
                   help='print human-readable summary')
    p.add_argument('--keep-work', action='store_true',
                   help='preserve unpacked working directory')
    p.add_argument('--list-body-parts', action='store_true',
                   help='list supported body parts and exit')
    args = p.parse_args()

    if args.list_body_parts:
        print('Supported body parts:')
        for bp in supported_body_parts():
            print(f'  {bp}')
        return

    if not args.zip_path:
        p.error('zip_path is required (unless --list-body-parts)')

    result = run_pipeline(
        args.zip_path,
        body_part_override=args.body_part,
        keep_work=args.keep_work,
    )

    if args.summary:
        _print_summary(result)
    else:
        s = json.dumps(result, indent=2, default=str)
        if args.out:
            Path(args.out).write_text(s)
            print(f'Wrote {args.out}')
        else:
            print(s)


if __name__ == '__main__':
    _cli()
