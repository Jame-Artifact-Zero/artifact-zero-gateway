"""
analyzers/cervical_spine_v7.py
==============================
Cervical spine v7 — marker-based architecture.

Pipeline:
  1. Pull axial T2 + sagittal T2 series from series_list, sort by z.
  2. analyze_cspine_v7(ax_items_sorted, sag_items) → flat marker list
       Each marker: {type, slice_inst, slice_idx, xyz_mm, confidence}.
       Types: cord_center, csf_anterior/posterior/left/right,
              canal_wall_anterior/posterior/left/right.
  3. markers_to_slice_measurements(markers) → per-slice cord diameters,
     CSF spaces, canal diameters (all in mm, all from patient-coord
     Euclidean distances; no intensity thresholds, no band segmentation).
  4. _synthesize_vertebrae_from_markers(markers) → vertebrae list anchored
     at the kyphosis apex (max cord_y_mm from cord_center markers) and
     walked outward at known cervical inter-vertebra spacings.
  5. classify_severity(slice_measurements) → per-slice severity from
     csf_space_min_mm thresholds (≤1.0 CRITICAL, ≤2.0 MODERATE,
     ≤3.5 FINDING, else NORMAL).
  6. aggregate_levels(slice_measurements, vertebrae) → per-level worst
     severity by nearest-z slice assignment.

Wired into the existing dispatcher: registers under code 'CSPINE_V7' and
label 'cervical_spine_v7' so requests with `?body_part=cervical_spine_v7`
route here.

The legacy CSpineAnalyzer (v3/v4/v5/v6) remains the default for
?body_part=cervical_spine and for auto-routed CSPINE. v7 is opt-in.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

import pydicom

from ._base import BaseAnalyzer


# Cervical inter-vertebra spacings (centroid to centroid), in mm.
# SPACING_MM[(sup, inf)] = distance from level `sup` down to level `inf`.
_SPACING_MM = {
    ('C2', 'C3'): 15.0,
    ('C3', 'C4'): 16.0,
    ('C4', 'C5'): 17.0,
    ('C5', 'C6'): 17.0,
    ('C6', 'C7'): 18.0,
    ('C7', 'T1'): 20.0,
}

# Anatomic order, superior to inferior
_LEVEL_ORDER = ['C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1']


class CSpineV7Analyzer(BaseAnalyzer):
    """v7 analyzer: marker-based architecture, patient-coord measurements,
    fixed millimeter severity thresholds. No image-domain code in the
    wrapper itself; all image work happens inside analyze_cspine_v7."""

    body_part_codes = ('CSPINE_V7', 'CERVICAL_SPINE_V7')
    body_part_label = 'cervical_spine_v7'

    def analyze(self, series_list, work_dir: Optional[str] = None):
        # ── 1. Pull axial T2 + sagittal T2 series from series_list ──────────
        ax_series, sag_series = self._select_axial_and_sagittal_t2(series_list)
        if ax_series is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'body_part_label': self.body_part_label,
                'reason': 'no axial T2 series detected',
                'series_seen': [
                    {k: s.get(k) for k in ('series_description',
                                            'orientation',
                                            'modality',
                                            'n_slices')}
                    for s in series_list
                ],
            }

        # Load + sort axial slices by InstancePosition[2] (cranial to caudal).
        ax_items_sorted = self._load_and_sort_series(ax_series)
        sag_items = (self._load_and_sort_series(sag_series)
                     if sag_series is not None else [])

        if not ax_items_sorted:
            return {
                'status': 'INSUFFICIENT_DATA',
                'body_part_label': self.body_part_label,
                'reason': 'axial T2 series found but no slices readable',
                'series_used': {
                    'axial_t2': {
                        'series_description': ax_series.get('series_description'),
                        'n_slices':           ax_series.get('n_slices'),
                        'series_uid':         ax_series.get('series_uid'),
                    }
                },
            }

        # ── 2. analyze_cspine_v7: image analysis → flat marker list ─────────
        from analyzers.cspine_v7 import analyze_cspine_v7
        markers = analyze_cspine_v7(ax_items_sorted, sag_items)

        if not markers:
            return {
                'status': 'INSUFFICIENT_DATA',
                'body_part_label': self.body_part_label,
                'reason': 'analyze_cspine_v7 returned no markers',
                'series_used': {
                    'axial_t2': {
                        'series_description': ax_series.get('series_description'),
                        'n_slices':           ax_series.get('n_slices'),
                        'series_uid':         ax_series.get('series_uid'),
                    }
                },
            }

        # ── 3. markers → per-slice measurements ─────────────────────────────
        from preimpression.spine_measurements import (
            markers_to_slice_measurements,
            classify_severity,
            aggregate_levels,
        )
        slice_measurements = markers_to_slice_measurements(markers)

        # ── 4. Synthesize vertebrae from cord_center markers (apex-anchored)
        try:
            vertebrae = self._synthesize_vertebrae_from_markers(markers)
        except ValueError as e:
            print(f'cervical_spine_v7: vertebra synthesis skipped: {e}')
            vertebrae = []

        # ── 5. Per-slice severity ───────────────────────────────────────────
        slice_measurements = classify_severity(slice_measurements)

        # ── 6. Per-level aggregation ────────────────────────────────────────
        level_summaries = aggregate_levels(slice_measurements, vertebrae)

        # ── 7. Build response dict in the existing analyzer schema ──────────
        return self._assemble_result(
            ax_series=ax_series,
            sag_series=sag_series,
            markers=markers,
            slice_measurements=slice_measurements,
            level_summaries=level_summaries,
            vertebrae=vertebrae,
        )

    # ── series selection ────────────────────────────────────────────────────

    @staticmethod
    def _select_axial_and_sagittal_t2(series_list):
        """Pick the best axial T2 + sagittal T2 series from series_list.

        Uses series_description heuristics consistent with measure_spine.is_axial_t2:
          - axial keywords: 'ax' or 'axial' or 'tra' or 'transverse'
          - sag keywords:   'sag' or 'sagittal'
          - T2 keywords:    't2'
          - exclude:        't1', 'stir', 'flair', 'dwi', 'survey', 'localizer',
                            'scout', '3d', 'tof'
        """
        AX_KW   = ('ax', 'axial', 'tra', 'transverse')
        SAG_KW  = ('sag', 'sagittal')
        T2_KW   = ('t2',)
        EXCLUDE = ('t1', 'stir', 'flair', 'dwi', 'survey', 'localizer',
                   'scout', '3d', 'tof', 'mra', 'mrv', 'post')

        def matches(desc, kw_set):
            d = desc.lower()
            return any(kw in d for kw in kw_set)

        def is_excluded(desc):
            d = desc.lower()
            return any(kw in d for kw in EXCLUDE)

        ax = None
        sag = None
        for s in series_list:
            desc = str(s.get('series_description', ''))
            orient = str(s.get('orientation', '')).upper()
            modality = str(s.get('modality', '')).upper()

            if modality and modality != 'MR':
                continue
            if is_excluded(desc):
                continue
            if not matches(desc, T2_KW):
                continue

            # AX: orientation field OR description keyword
            if ax is None:
                if orient == 'AX' or matches(desc, AX_KW):
                    ax = s
                    continue
            # SAG: orientation field OR description keyword
            if sag is None:
                if orient == 'SAG' or matches(desc, SAG_KW):
                    sag = s
                    continue

        return ax, sag

    @staticmethod
    def _load_and_sort_series(series: dict):
        """Load each slice as a pydicom Dataset and sort by ImagePositionPatient[2]
        descending (cranial first). Returns [] if the series has no readable slices.
        """
        if not series:
            return []
        items = []
        for fpath in series.get('files', []):
            try:
                ds = pydicom.dcmread(fpath, force=True)
                ipp = getattr(ds, 'ImagePositionPatient', None)
                if ipp is None or len(ipp) < 3:
                    continue
                items.append((float(ipp[2]), ds))
            except Exception:
                continue
        # Sort cranial (higher z) to caudal (lower z) — matches DICOM
        # convention of +z = superior in LPS patient coords.
        items.sort(key=lambda kv: -kv[0])
        return [ds for _, ds in items]

    # ── vertebra synthesis (kyphosis-apex anchored) ─────────────────────────

    def _synthesize_vertebrae_from_markers(self, markers: list) -> list:
        """Anchor C4-C5 disc midpoint at the apex of the cord_y track and
        place 7 vertebrae (C2..T1) outward at known cervical spacings.

        Apex of cervical lordosis = most posterior cord point in patient
        LPS coords = max cord_y_mm across cord_center markers.

        Steps:
          1. Extract cord_center markers with confidence > 0.
          2. Find the apex z (z of max cord_y_mm, restricted to the central
             portion of the z range to avoid skull-base/cervicothoracic
             junction extremes).
          3. Place C4-C5 disc midpoint at apex_z. C4 superior by half C4-C5
             spacing; C5 inferior by half. Walk outward.
          4. Median cord_x/y across cord_center markers becomes the vertebra
             centroid_x/y. (The cord track sits posterior to the vertebra
             column by a near-constant offset; downstream code uses these
             only as an anchor for matching axial slices to levels.)

        Returns a list of vertebra dicts:
          {V_idx, level, centroid_z_mm, centroid_x_mm, centroid_y_mm}

        Raises ValueError when no cord_center markers exist.
        """
        # Step 1: pull cord_center markers
        track = []
        high_track = []
        for m in markers:
            if m.get('type') != 'cord_center':
                continue
            xyz = m.get('xyz_mm')
            if xyz is None or len(xyz) < 3:
                continue
            try:
                conf = float(m.get('confidence', 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            try:
                point = {'z': float(xyz[2]),
                         'cx': float(xyz[0]),
                         'cy': float(xyz[1]),
                         'conf': conf}
            except (TypeError, ValueError):
                continue
            track.append(point)
            # Treat confidence >= 0.7 as "high" (analogous to the HIGH/LOW
            # banding the old measure_spine pipeline used). Tunable here.
            if conf >= 0.7:
                high_track.append(point)

        if not track:
            raise ValueError(
                'No cord_center markers from analyze_cspine_v7 — cannot '
                'synthesize vertebrae.'
            )

        # Step 2: kyphosis apex with edge-margin trimming
        EDGE_MARGIN_MM = 25.0
        apex_pool = high_track if high_track else track
        if len(apex_pool) >= 3:
            z_lo = min(t['z'] for t in apex_pool)
            z_hi = max(t['z'] for t in apex_pool)
            z_span = z_hi - z_lo
            margin = min(EDGE_MARGIN_MM, z_span / 4)
            central = [t for t in apex_pool
                       if z_lo + margin <= t['z'] <= z_hi - margin]
            if central:
                apex_pool = central
        apex_row = max(apex_pool, key=lambda t: t['cy'])
        apex_z = apex_row['z']

        # Step 3: place vertebrae outward from C4-C5 disc at apex_z
        c45 = _SPACING_MM[('C4', 'C5')]
        z_of = {
            'C4': apex_z + c45 / 2,
            'C5': apex_z - c45 / 2,
        }
        z_of['C3'] = z_of['C4'] + _SPACING_MM[('C3', 'C4')]
        z_of['C2'] = z_of['C3'] + _SPACING_MM[('C2', 'C3')]
        z_of['C6'] = z_of['C5'] - _SPACING_MM[('C5', 'C6')]
        z_of['C7'] = z_of['C6'] - _SPACING_MM[('C6', 'C7')]
        z_of['T1'] = z_of['C7'] - _SPACING_MM[('C7', 'T1')]

        # Step 4: median cord position for centroid_x/y
        median_pool = high_track if high_track else track
        med_cx = self._median([t['cx'] for t in median_pool])
        med_cy = self._median([t['cy'] for t in median_pool])

        vertebrae = []
        for i, level in enumerate(_LEVEL_ORDER, start=1):
            vertebrae.append({
                'V_idx':         i,
                'level':         level,
                'centroid_z_mm': round(z_of[level], 3),
                'centroid_x_mm': round(med_cx, 3),
                'centroid_y_mm': round(med_cy, 3),
            })

        # Diagnostic (greppable in CloudWatch)
        print(f'cervical_spine_v7: kyphosis apex at z={apex_z:+.2f}mm '
              f'(max cord_y={apex_row["cy"]:+.2f})')
        print(f'cervical_spine_v7: cord track median position: '
              f'x={med_cx:+.2f}, y={med_cy:+.2f}')
        print(f'cervical_spine_v7: synthesized vertebrae (V_idx → level → z):')
        for v in vertebrae:
            print(f"  V{v['V_idx']} = {v['level']}: z = {v['centroid_z_mm']:+.2f} mm")

        return vertebrae

    # ── result assembly ─────────────────────────────────────────────────────

    def _assemble_result(self, ax_series, sag_series, markers,
                         slice_measurements, level_summaries, vertebrae) -> dict:
        """Build the response dict in the existing analyzer output schema."""
        series_used = {}
        if ax_series is not None:
            series_used['axial_t2'] = {
                'series_description': ax_series.get('series_description'),
                'n_slices':           ax_series.get('n_slices'),
                'series_uid':         ax_series.get('series_uid'),
            }
        if sag_series is not None:
            series_used['sagittal_t2'] = {
                'series_description': sag_series.get('series_description'),
                'n_slices':           sag_series.get('n_slices'),
                'series_uid':         sag_series.get('series_uid'),
            }

        # Map spine_measurements.classify_severity vocabulary directly into
        # the existing analyzer output vocabulary. They already agree:
        # NORMAL / FINDING / MODERATE / CRITICAL.
        # UNMEASURED slices contribute to neither flags nor overall status.
        flags = []
        for ls in level_summaries:
            sev = ls.get('worst_severity', 'UNMEASURED')
            if sev in ('FINDING', 'MODERATE', 'CRITICAL'):
                flags.append({
                    'label':    f"central canal stenosis at level "
                                f"(csf_space_min={ls.get('worst_csf_space_min_mm')}mm)",
                    'level':    ls.get('level'),
                    'severity': sev,
                })

        from ._base import max_severity
        overall = max_severity(flags) if flags else 'NORMAL'
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in flags:
            sev = str(f.get('severity', 'NORMAL')).lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not flags:
            counts['normal'] = 1

        # Build markers list in the existing output format
        out_markers = []
        # Group markers by slice_inst to get one record per slice anchored
        # at cord_center
        by_inst = {}
        for m in markers:
            inst = m.get('slice_inst')
            if inst is None:
                continue
            by_inst.setdefault(inst, {})[m['type']] = m

        for inst in sorted(by_inst.keys()):
            ms = by_inst[inst]
            cc = ms.get('cord_center')
            if cc is None:
                continue
            xyz = cc.get('xyz_mm')
            # Find the per-slice measurement record so we can attach the
            # severity to the marker too
            sm = next((s for s in slice_measurements
                       if s.get('slice_inst') == inst), None)
            out_markers.append({
                'inst':           int(inst),
                'cord_xyz_mm':    [round(float(xyz[0]), 3),
                                   round(float(xyz[1]), 3),
                                   round(float(xyz[2]), 3)] if xyz else None,
                'confidence':     float(cc.get('confidence', 0.0)),
                'severity':       sm.get('severity', 'UNMEASURED') if sm else 'UNMEASURED',
                'csf_space_min_mm': (round(sm['csf_space_min_mm'], 3)
                                      if sm and sm.get('csf_space_min_mm') is not None
                                      else None),
            })

        levels_detected = {}
        for v in vertebrae:
            levels_detected[v['level']] = round(v['centroid_z_mm'], 3)

        cord_track_3d = self._summarize_cord_track(out_markers)

        return {
            'status':              overall,
            'body_part_label':     self.body_part_label,
            'algorithm_version':   'v7',
            'series_used':         series_used,
            'levels_detected':     levels_detected,
            'impression': {
                'overall_status': overall,
                'counts':         counts,
                'flags':          flags,
            },
            'level_summaries':     level_summaries,
            'slice_measurements':  slice_measurements,
            'markers':             out_markers,
            'cord_track_3d':       cord_track_3d,
        }

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _median(values: list) -> float:
        """Median without numpy dependency."""
        s = sorted(values)
        n = len(s)
        if n == 0:
            return 0.0
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2

    @staticmethod
    def _summarize_cord_track(markers: list) -> dict:
        """Compact 3D cord-track summary from marker centroids."""
        if not markers:
            return {'n_markers': 0}
        xs, ys, zs = [], [], []
        for m in markers:
            xyz = m.get('cord_xyz_mm')
            if not xyz or len(xyz) < 3:
                continue
            if xyz[0] is not None:
                xs.append(xyz[0])
            if xyz[1] is not None:
                ys.append(xyz[1])
            if xyz[2] is not None:
                zs.append(xyz[2])
        if not xs or not zs:
            return {'n_markers': len(markers)}
        return {
            'n_markers':       len(markers),
            'cord_x_mean_mm':  sum(xs) / len(xs),
            'cord_x_range_mm': [min(xs), max(xs)],
            'cord_y_mean_mm':  sum(ys) / len(ys) if ys else None,
            'cord_y_range_mm': [min(ys), max(ys)] if ys else None,
            'z_range_mm':      [min(zs), max(zs)],
        }
