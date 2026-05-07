"""
analyzers/cspine_v5_analyzer.py — Side-by-side wrapper for the v5 cspine analyzer.
=================================================================================

This file exists so the v5 algorithm in cspine_v5.py can be invoked alongside
the current production cspine analyzer (cspine.py / CSpineAnalyzer), without
disturbing it. Both analyzers can run on the same DICOM in the same pipeline
deploy, accessed via different body-part labels:

    body_part=cervical_spine     -> existing CSpineAnalyzer (current production)
    body_part=cervical_spine_v5  -> CSpineV5Analyzer (this file -> cspine_v5.py)

The wrapper mirrors the structure of cspine.py's CSpineAnalyzer.analyze()
exactly. The only differences are:

  1. body_part_label = 'cervical_spine_v5'
  2. body_part_codes = ()        (empty -> not picked up by autodetect; only
                                  reachable via explicit body_part override)
  3. The analyze() body forwards to analyze_cspine_v5() instead of running
     the legacy detect_levels_cspine + detect_cords_axial + measure_radial
     chain.
  4. Output adds 'algorithm_version': 'v5' for traceability.

Comparison flow:
    POST /preimpression                            -> CSpineAnalyzer (v4)
    POST /preimpression?body_part=cervical_spine_v5 -> CSpineV5Analyzer

Same DICOM, two responses, field-by-field comparison.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

from ._base import BaseAnalyzer, max_severity
from ._spine_common import select_best_t2_axsag

from .cspine_v5 import analyze_cspine_v5


class CSpineV5Analyzer(BaseAnalyzer):
    """
    Cervical spine analyzer, v5 algorithm path.

    Pipeline contract identical to CSpineAnalyzer:
      analyze(series_list, work_dir=None) -> dict with keys:
        status, body_part_label, series_used, levels_detected, impression,
        level_summaries, slice_measurements, markers, cord_track_3d
    Plus:
        algorithm_version = 'v5'
    """
    # Empty codes: not picked up by detect_body_part(). Reachable only via
    # explicit ?body_part=cervical_spine_v5 override on the API.
    body_part_codes = ()
    body_part_label = 'cervical_spine_v5'

    def analyze(self, series_list, work_dir=None):
        ax_t2, sag_t2 = select_best_t2_axsag(series_list)
        if ax_t2 is None or sag_t2 is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'reason': ('axial T2 not found' if ax_t2 is None
                           else 'sagittal T2 not found'),
                'series_seen': [
                    {k: s[k] for k in ('series_description', 'orientation',
                                        'modality', 'n_slices')}
                    for s in series_list
                ],
            }

        from ._base import load_volume
        ax_items = load_volume(ax_t2['files'])
        sag_items = load_volume(sag_t2['files'])

        # All cspine-v5-specific work happens here
        result = analyze_cspine_v5(ax_items, sag_items)

        # Build counts for impression block (same logic as CSpineAnalyzer)
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in result['all_flags']:
            sev = f['severity'].lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not result['all_flags']:
            counts['normal'] = 1

        return {
            'status': result['overall'] or 'NORMAL',
            'body_part_label': self.body_part_label,
            'algorithm_version': 'v5',
            'series_used': {
                'axial_t2': {'series_description': ax_t2['series_description'],
                              'n_slices': ax_t2['n_slices'],
                              'series_uid': ax_t2['series_uid']},
                'sagittal_t2': {'series_description': sag_t2['series_description'],
                                 'n_slices': sag_t2['n_slices'],
                                 'series_uid': sag_t2['series_uid']},
            },
            'levels_detected': result['levels_detected'],
            'impression': {
                'overall_status': result['overall'] or 'NORMAL',
                'counts': counts,
                'flags': result['all_flags'],
            },
            'level_summaries': result['level_summaries'],
            'slice_measurements': result['slice_records'],
            'markers': result['markers'],
            'cord_track_3d': result['cord_track_3d'],
        }
