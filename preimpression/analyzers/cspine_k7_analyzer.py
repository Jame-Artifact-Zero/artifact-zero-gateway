"""
preimpression/analyzers/cspine_k7_analyzer.py
=============================================
Gateway-side wrapper for the K7 cervical spine measurement engine.

K7 is the k-space-based cervical spine analyzer. The actual measurement
engine lives in the standalone `cspine_k7_pipeline` package (installed
via requirements.txt from the vendored wheel in wheels/).

Routing: opt-in only via explicit body_part override.

    POST /preimpression?body_part=cervical_spine_k7

No traffic reaches K7 unless the caller asks for it by label. Auto-routed
CSPINE traffic continues to hit CSpineAnalyzer (v3-v6). K7 is additive.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

from ._base import BaseAnalyzer


class CSpineK7Analyzer(BaseAnalyzer):
    """K7 cervical spine analyzer - k-space measurement engine.

    Returns a markers-only result (no severity, no flags, no impression).
    Downstream interpretation is the caller's responsibility.
    """

    # Empty body_part_codes - K7 is only reachable via explicit
    # ?body_part=cervical_spine_k7 override, not via auto-detection.
    body_part_codes = ()
    body_part_label = 'cervical_spine_k7'

    def analyze(self, series_list, work_dir=None):
        # Lazy-import the installed package so any import error surfaces
        # as a clean INSUFFICIENT_DATA response with a specific reason,
        # rather than crashing module load for the whole dispatcher.
        try:
            from cspine_k7_pipeline.analyzers.cspine_k7 import analyze_cspine_k7
            from cspine_k7_pipeline.analyzers._spine_common import select_best_t2_axsag
            from cspine_k7_pipeline.analyzers._base import load_volume
        except ImportError as e:
            return {
                'status': 'INSUFFICIENT_DATA',
                'body_part_label': self.body_part_label,
                'algorithm_version': 'k7',
                'reason': 'cspine_k7_pipeline package not installed: ' + str(e),
            }

        # Pick the best axial T2 + sagittal T2 from the gateway's series_list.
        ax_t2, sag_t2 = select_best_t2_axsag(series_list)
        if ax_t2 is None or sag_t2 is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'body_part_label': self.body_part_label,
                'algorithm_version': 'k7',
                'reason': ('axial T2 not found' if ax_t2 is None
                           else 'sagittal T2 not found'),
            }

        # load_volume returns list of slice dicts:
        # {filepath, inst, img, ipp, iop, ps, sl}
        # This is the exact input shape analyze_cspine_k7 expects.
        ax_items = load_volume(ax_t2['files'])
        sag_items = load_volume(sag_t2['files'])

        # Sort axial cranio-caudally (ascending z)
        ax_items_sorted = sorted(ax_items, key=lambda s: s['ipp'][2])

        # Run the K7 measurement engine
        result = analyze_cspine_k7(ax_items_sorted, sag_items)

        # Attach gateway-side provenance to match existing analyzer schema
        result.update({
            'body_part_label': self.body_part_label,
            'series_used': {
                'axial_t2': {
                    'series_description': ax_t2.get('series_description'),
                    'n_slices': ax_t2.get('n_slices'),
                    'series_uid': ax_t2.get('series_uid'),
                },
                'sagittal_t2': {
                    'series_description': sag_t2.get('series_description'),
                    'n_slices': sag_t2.get('n_slices'),
                    'series_uid': sag_t2.get('series_uid'),
                },
            },
        })
        return result
