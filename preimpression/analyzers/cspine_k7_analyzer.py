"""
analyzers/cspine_k7_analyzer.py - k7 analyzer wrapper

Mirrors the cspine_v6_analyzer.py wrapper pattern. Registers k7 as an
independent analyzer reachable only via explicit body_part override:

    POST /preimpression?body_part=cervical_spine_k7

k7 is a measurement engine, not a classifier. The output dict has no
'status', no 'impression', no 'flags'. Just markers.

Author: Jame Houghton / Artifact Zero Labs, May 2026
"""
from __future__ import annotations

from ._base import BaseAnalyzer, load_volume
from ._spine_common import select_best_t2_axsag
from .cspine_k7 import analyze_cspine_k7


class CSpineK7Analyzer(BaseAnalyzer):
    """Cervical spine measurement engine (k7).

    Pipeline contract: returns a markers-only result. No severity, no flags,
    no impression. Downstream interpretation operates on the marker list.
    """
    body_part_codes = ()
    body_part_label = 'cervical_spine_k7'

    def analyze(self, series_list, work_dir=None):
        ax_t2, sag_t2 = select_best_t2_axsag(series_list)
        if ax_t2 is None or sag_t2 is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'reason': ('axial T2 not found' if ax_t2 is None
                           else 'sagittal T2 not found'),
                'algorithm_version': 'k7',
            }

        ax_items = load_volume(ax_t2['files'])
        sag_items = load_volume(sag_t2['files'])

        # Sort axial cranio-caudally (ascending z)
        ax_items_sorted = sorted(ax_items, key=lambda s: s['ipp'][2])

        result = analyze_cspine_k7(ax_items_sorted, sag_items)

        # Attach series provenance
        result.update({
            'body_part_label': self.body_part_label,
            'series_used': {
                'axial_t2': {'series_description': ax_t2['series_description'],
                              'n_slices': ax_t2['n_slices'],
                              'series_uid': ax_t2['series_uid']},
                'sagittal_t2': {'series_description': sag_t2['series_description'],
                                 'n_slices': sag_t2['n_slices'],
                                 'series_uid': sag_t2['series_uid']},
            },
        })
        return result
