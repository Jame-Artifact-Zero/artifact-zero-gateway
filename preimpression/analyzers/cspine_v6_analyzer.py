"""
analyzers/cspine_v6_analyzer.py — Side-by-side wrapper for the v6 cspine analyzer.
=================================================================================

Same shape as cspine_v5_analyzer.py, registers v6 as a third independent
analyzer alongside v4 (cspine.py) and v5 (cspine_v5_analyzer.py). All three
remain reachable simultaneously:

    body_part=cervical_spine     -> CSpineAnalyzer (current production, v4)
    body_part=cervical_spine_v5  -> CSpineV5Analyzer -> cspine_v5.py
    body_part=cervical_spine_v6  -> CSpineV6Analyzer -> cspine_v6.py

The wrapper mirrors the structure of cspine.py's CSpineAnalyzer.analyze().
Only differences:

  1. body_part_label = 'cervical_spine_v6'
  2. body_part_codes = () (empty - not picked up by autodetect; only
                          reachable via explicit body_part override)
  3. analyze() body forwards to analyze_cspine_v6() instead of the legacy
     detect_levels_cspine + detect_cords_axial + measure_radial chain.
  4. Output adds 'algorithm_version': 'v6' for traceability (also baked
     into v6's own return dict, this just preserves it).

Comparison flow:
    POST /preimpression                            -> CSpineAnalyzer (v4)
    POST /preimpression?body_part=cervical_spine_v5 -> CSpineV5Analyzer (v5.2)
    POST /preimpression?body_part=cervical_spine_v6 -> CSpineV6Analyzer (v6)

Same DICOM, three responses, field-by-field comparison across versions.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

from ._base import BaseAnalyzer, max_severity
from ._spine_common import select_best_t2_axsag

from .cspine_v6 import analyze_cspine_v6


class CSpineV6Analyzer(BaseAnalyzer):
    """
    Cervical spine analyzer, v6 algorithm path (k-space cord detection).

    Pipeline contract identical to CSpineAnalyzer:
      analyze(series_list, work_dir=None) -> dict with keys:
        status, body_part_label, series_used, levels_detected, impression,
        level_summaries, slice_measurements, markers, cord_track_3d
    Plus:
        algorithm_version = 'v6'
    """
    # Empty codes: not picked up by detect_body_part(). Reachable only via
    # explicit ?body_part=cervical_spine_v6 override on the API.
    body_part_codes = ()
    body_part_label = 'cervical_spine_v6'

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

        # All cspine-v6-specific work happens here
        result = analyze_cspine_v6(ax_items, sag_items)

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
            'algorithm_version': 'v6',
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
