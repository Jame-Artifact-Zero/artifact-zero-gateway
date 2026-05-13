"""
analyzers/cervical_spine_v7.py
==============================
Cervical spine v7 — z-profile architecture.

Pipeline:
  1. data_extract.run_from_series(series_list) → SliceFacts rows
  2. measure_spine.run(slice_facts_csv) → cord_canal_per_slice.csv
                                          + disc_spacing.csv
                                          (level_severity.csv only if
                                           vertebrae.csv is present)
  3. _write_synthesized_vertebrae_csv(cord_canal_csv) → vertebrae.csv
       Kyphosis-apex anchored: finds the most posterior point of the cord
       track (apex of cervical lordosis = C4-C5 disc level anatomically) and
       walks outward at known cervical inter-vertebra spacings.
  4. measure_spine.run(slice_facts_csv) AGAIN, now with vertebrae.csv
       present → emits level_severity.csv with proper C-level labels.
  5. profile_spine.run(per_slice_csv) → continuous_profile.csv
                                        + anomaly_profile.csv

Wired into the existing dispatcher: registers under code 'CSPINE_V7' and
label 'cervical_spine_v7' so requests with `?body_part=cervical_spine_v7`
route here.

The legacy CSpineAnalyzer (v3/v4/v5/v6) remains the default for
?body_part=cervical_spine and for auto-routed CSPINE. v7 is opt-in for now.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

import csv
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from ._base import BaseAnalyzer


# Cervical inter-vertebra spacings (centroid to centroid), in mm.
# Indexed by superior level: SPACING_MM[level] = distance from level to the
# next inferior level. C4-C5 spacing is the distance C4 → C5, etc.
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
    """v7 analyzer: z-profile architecture, continuous interpolation + 2σ anomaly
    detection. Bridges to measure_spine + profile_spine via SliceFacts CSV."""

    body_part_codes = ('CSPINE_V7', 'CERVICAL_SPINE_V7')
    body_part_label = 'cervical_spine_v7'

    def analyze(self, series_list, work_dir: Optional[str] = None):
        # ── 1. Convert series_list → SliceFacts CSV in a scratch dir ────────
        scratch = Path(tempfile.mkdtemp(prefix='cspine_v7_'))
        try:
            from preimpression import data_extract as _de
            from preimpression import measure_spine as _ms
            from preimpression import profile_spine as _ps

            slice_facts_csv = scratch / 'slice_facts.csv'
            slice_rows = _de.run_from_series(
                series_list,
                work_dir=work_dir,
                out_csv=str(slice_facts_csv),
            )

            # If no axial T2 candidates exist at all, short-circuit with the
            # same INSUFFICIENT_DATA shape the other analyzers use.
            axial_t2_rows = [r for r in slice_rows if _ms.is_axial_t2(r)]
            if not axial_t2_rows:
                return {
                    'status': 'INSUFFICIENT_DATA',
                    'body_part_label': self.body_part_label,
                    'reason': 'no axial T2 series passed measure_spine filters',
                    'series_seen': [
                        {k: s.get(k) for k in ('series_description',
                                                'orientation',
                                                'modality',
                                                'n_slices')}
                        for s in series_list
                    ],
                }

            # ── 2. First measure_spine pass: cord measurements per slice ────
            # No vertebrae.csv yet, so this pass emits cord_canal_per_slice.csv
            # and disc_spacing.csv but NOT level_severity.csv. Cord-finding
            # uses anchor (0,0) by default — unchanged from the pre-vertebrae
            # baseline behavior.
            _ms.run(str(slice_facts_csv), out_dir=str(scratch))
            per_slice_csv = scratch / 'cord_canal_per_slice.csv'

            # ── 3. Synthesize vertebrae.csv from the cord track ─────────────
            # Kyphosis-apex anchored: finds the most posterior point of the
            # cord track (max cord_y in LPS coords = anatomically C4-C5) and
            # places vertebrae outward at known cervical spacings. Robust to
            # asymmetric FOV in a way that even-spacing-from-bounds is not.
            try:
                self._write_synthesized_vertebrae_csv(
                    cord_canal_csv=per_slice_csv,
                    out_csv=scratch / 'vertebrae.csv',
                )
                vertebrae_synthesized = True
            except ValueError as e:
                # No cord-found rows at all — skip vertebrae synthesis. The
                # level_severity.csv will simply not be emitted, and
                # _assemble_result will return empty level_summaries.
                print(f'cervical_spine_v7: vertebra synthesis skipped: {e}')
                vertebrae_synthesized = False

            # ── 4. Second measure_spine pass: with vertebrae.csv present ───
            # measure_spine._find_vertebrae_csv now picks up the just-written
            # vertebrae.csv, recomputes the cord-anchor from its median x/y,
            # and emits level_severity.csv with proper C-level labels.
            if vertebrae_synthesized:
                _ms.run(str(slice_facts_csv), out_dir=str(scratch))

            # ── 5. profile_spine: continuous + anomaly profile ──────────────
            _ps.run(str(per_slice_csv), out_dir=str(scratch))

            # ── 6. Assemble result dict in the existing analyzer schema ─────
            return self._assemble_result(
                series_list=series_list,
                slice_rows=slice_rows,
                per_slice_csv=per_slice_csv,
                level_csv=scratch / 'level_severity.csv',
                continuous_csv=scratch / 'continuous_profile.csv',
                anomaly_csv=scratch / 'anomaly_profile.csv',
            )
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # ────────────────────────────────────────────────────────────────────────
    # Result-assembly. Output contract matches existing analyzers:
    #   status, body_part_label, series_used, levels_detected, impression
    #   (counts/flags/overall_status), level_summaries, slice_measurements,
    #   markers, cord_track_3d. Plus a v7-specific 'profile' block.
    # ────────────────────────────────────────────────────────────────────────

    def _assemble_result(self, series_list, slice_rows,
                          per_slice_csv: Path,
                          level_csv: Path,
                          continuous_csv: Path,
                          anomaly_csv: Path) -> dict:
        # series_used — pick the axial T2 and sagittal T2 measure_spine ran on
        from preimpression import measure_spine as _ms
        ax_rows = [r for r in slice_rows if _ms.is_axial_t2(r)]
        ax_series_desc = ax_rows[0]['series_description'] if ax_rows else ''

        # Find the matching series objects in series_list for richer metadata
        ax_series = next(
            (s for s in series_list
             if str(s.get('series_description', '')) == ax_series_desc),
            None,
        )
        sag_series = None
        for s in series_list:
            desc = str(s.get('series_description', '')).lower()
            orient = str(s.get('orientation', '')).upper()
            if orient == 'SAG' and 't2' in desc:
                sag_series = s
                break

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

        # ── per-slice rows ──────────────────────────────────────────────────
        slice_measurements: list[dict] = []
        markers: list[dict] = []
        if per_slice_csv.exists():
            with open(per_slice_csv, newline='') as f:
                for r in csv.DictReader(f):
                    rec = {
                        'inst':              self._as_int(r.get('instance_number')),
                        'z_mm':              self._as_float(r.get('z_mm')),
                        'series_description': r.get('series_description', ''),
                        'cord_found':        r.get('cord_found') == 'True',
                        'cord_confidence':   r.get('cord_confidence', ''),
                        'status_detail':     r.get('status_detail', ''),
                        'cord_x_mm':         self._as_float(r.get('cord_x_mm')),
                        'cord_y_mm':         self._as_float(r.get('cord_y_mm')),
                        'cord_area_mm2':     self._as_float(r.get('cord_area_mm2')),
                        'csf_space_anterior_mm':  self._as_float(r.get('csf_space_anterior_mm')),
                        'csf_space_posterior_mm': self._as_float(r.get('csf_space_posterior_mm')),
                        'csf_space_left_mm':      self._as_float(r.get('csf_space_left_mm')),
                        'csf_space_right_mm':     self._as_float(r.get('csf_space_right_mm')),
                        'csf_space_min_mm':       self._as_float(r.get('csf_space_min_mm')),
                        'csf_lr_ratio':           self._as_float(r.get('csf_lr_ratio')),
                        'csf_lr_flag':            r.get('csf_lr_flag', ''),
                        'canal_flag':             r.get('canal_flag', ''),
                    }
                    slice_measurements.append(rec)

                    # Markers: one per cord-found slice with HIGH or LOW conf
                    if rec['cord_found'] and rec['cord_x_mm'] is not None:
                        markers.append({
                            'inst':           rec['inst'],
                            'cord_xyz_mm':    [
                                round(rec['cord_x_mm'], 3),
                                round(rec['cord_y_mm'], 3) if rec['cord_y_mm'] is not None else None,
                                round(rec['z_mm'], 3) if rec['z_mm'] is not None else None,
                            ],
                            'cord_area_mm2': round(rec['cord_area_mm2'], 2)
                                                if rec['cord_area_mm2'] is not None else None,
                            'confidence':     rec['cord_confidence'],
                            'csf_space_min_mm': round(rec['csf_space_min_mm'], 2)
                                                if rec['csf_space_min_mm'] is not None else None,
                            'canal_flag':     rec['canal_flag'],
                        })

        # ── level_summaries from level_severity.csv ─────────────────────────
        level_summaries: list[dict] = []
        if level_csv.exists():
            with open(level_csv, newline='') as f:
                for r in csv.DictReader(f):
                    level_summaries.append({
                        'level':                  r.get('level'),
                        'kind':                   r.get('kind'),
                        'n_slices':               self._as_int(r.get('n_slices')),
                        'n_high_confidence':      self._as_int(r.get('n_high_confidence')),
                        'n_abnormal_flag':        self._as_int(r.get('n_abnormal_flag')),
                        'n_asymmetric_high':      self._as_int(r.get('n_asymmetric_high')),
                        'best_posterior_csf_mm':  self._as_float(r.get('best_posterior_csf_mm')),
                        'cord_area_mean_mm2':     self._as_float(r.get('cord_area_mean_mm2')),
                        'cord_area_std_mm2':     self._as_float(r.get('cord_area_std_mm2')),
                        'severity':               r.get('severity', ''),
                        'reason':                 r.get('reason', ''),
                    })

        # ── flags from level_summaries + anomaly_profile ────────────────────
        # Map measure_spine severity vocabulary → analyzer-output vocabulary.
        sev_to_out = {
            'NORMAL':   'NORMAL',
            'MILD':     'FINDING',
            'MODERATE': 'MODERATE',
            'SEVERE':   'CRITICAL',
        }
        flags: list[dict] = []
        for ls in level_summaries:
            sev_in = ls.get('severity', '')
            sev_out = sev_to_out.get(sev_in)
            if sev_out and sev_out != 'NORMAL':
                flags.append({
                    'label':    f"central canal stenosis at level ({ls.get('reason', '')})",
                    'level':    ls.get('level'),
                    'severity': sev_out,
                })

        # ── anomaly profile rows (informational) ────────────────────────────
        anomalies: list[dict] = []
        if anomaly_csv.exists():
            with open(anomaly_csv, newline='') as f:
                for r in csv.DictReader(f):
                    anomalies.append({
                        'z_mm':                self._as_float(r.get('z_mm')),
                        'n_metrics_flag':      self._as_int(r.get('n_metrics_flag')),
                        'metrics_flag':        r.get('metrics_flag', ''),
                        'nearest_slice_conf':  r.get('nearest_slice_conf', ''),
                        'details':             r.get('details', ''),
                    })

        # ── overall + counts ─────────────────────────────────────────────────
        from ._base import max_severity
        overall = max_severity(flags) if flags else 'NORMAL'
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in flags:
            sev = str(f.get('severity', 'NORMAL')).lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not flags:
            counts['normal'] = 1

        # ── levels_detected from level_summaries (centroid-based level z) ───
        levels_detected: dict = {}
        if level_summaries:
            # Use the level_severity rows for the names; z values are not in
            # level_severity.csv directly so we leave levels_detected as a
            # name->None map for now. Downstream renderers tolerate this.
            for ls in level_summaries:
                if ls.get('level'):
                    levels_detected[ls['level']] = None

        # ── cord_track_3d from markers ──────────────────────────────────────
        cord_track_3d = self._summarize_cord_track(markers)

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
            'markers':             markers,
            'cord_track_3d':       cord_track_3d,
            'profile': {
                'continuous_csv_present':    continuous_csv.exists(),
                'anomaly_z_positions_count': len(anomalies),
                'anomalies':                 anomalies,
            },
        }

    # ── vertebra synthesis (kyphosis-apex anchored) ─────────────────────────

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

    def _write_synthesized_vertebrae_csv(
        self,
        cord_canal_csv,
        out_csv,
    ):
        """Build vertebrae.csv anchored at the kyphosis apex.

        Steps:
          1. Read cord_canal_per_slice.csv. Use only rows where cord_found=True
             AND cord_confidence != INVALID.
          2. Find the z-position of maximum cord_y_mm. In LPS patient coords
             posterior = +y, so max cord_y = the most posterior cord point =
             kyphosis apex = anatomically the C4-C5 disc level.
          3. Anchor C4-C5 disc midpoint at apex_z. Place C4 superior by half
             the C4-C5 spacing, C5 inferior by half. Walk outward from there
             using _SPACING_MM.
          4. Centroid x and y are taken from the median cord position across
             all cord-found slices (the cord track sits posterior to the
             vertebra column by a near-constant offset, so the cord median is
             a stable x,y reference even though the y is the cord y rather
             than the vertebra body y — downstream code uses these only as
             an anatomic anchor for matching axial slices to levels).

        Args:
          cord_canal_csv: path to cord_canal_per_slice.csv (output of
                          measure_spine.py)
          out_csv:        path to write vertebrae.csv

        Returns:
          Path to the written vertebrae.csv

        Raises:
          ValueError when no cord-found rows are present in the input.
        """
        cord_canal_csv = Path(cord_canal_csv)
        out_csv = Path(out_csv)

        # Step 1: load cord track from cord-found rows only
        track = []
        high_track = []
        with open(cord_canal_csv, newline='') as f:
            for r in csv.DictReader(f):
                if r.get('cord_found', 'False') != 'True':
                    continue
                conf = r.get('cord_confidence', '')
                if conf == 'INVALID':
                    continue
                try:
                    z = float(r['z_mm'])
                    cx = float(r['cord_x_mm'])
                    cy = float(r['cord_y_mm'])
                except (KeyError, ValueError, TypeError):
                    continue
                point = {'z': z, 'cx': cx, 'cy': cy, 'conf': conf}
                track.append(point)
                if conf == 'HIGH':
                    high_track.append(point)

        if not track:
            raise ValueError(
                f'No cord-found rows in {cord_canal_csv} — cannot synthesize '
                f'vertebrae. Pipeline upstream produced no usable measurements.'
            )

        # Step 2: kyphosis apex = z position of maximum cord_y_mm
        # (most posterior cord point in LPS patient coords).
        # Use HIGH-confidence rows only when available — LOW-confidence cord_y
        # values can include large outliers from the detector locking onto
        # non-cord structures, which would pull the apex to an anatomically
        # impossible location.
        # Also restrict to the central portion of the cord track: the cervical
        # apex sits anatomically in the middle of the cervical FOV (around
        # C4-C5), not at the top of the stack (skull base) or the bottom
        # (cervicothoracic junction). Drop EDGE_MARGIN_MM from each end of the
        # z range when picking the apex.
        EDGE_MARGIN_MM = 25.0
        apex_pool = high_track if high_track else track
        if len(apex_pool) >= 3:
            z_lo = min(t['z'] for t in apex_pool)
            z_hi = max(t['z'] for t in apex_pool)
            z_span = z_hi - z_lo
            # If the stack is too short to apply the full margin, use a
            # smaller one.
            margin = min(EDGE_MARGIN_MM, z_span / 4)
            central = [t for t in apex_pool
                       if z_lo + margin <= t['z'] <= z_hi - margin]
            if central:
                apex_pool = central
        apex_row = max(apex_pool, key=lambda t: t['cy'])
        apex_z = apex_row['z']

        # Step 3: place vertebrae outward from C4-C5 disc at apex_z
        # C4-C5 disc midpoint = apex_z
        # C4 centroid_z = apex_z + (C4-C5 spacing) / 2  (superior)
        # C5 centroid_z = apex_z - (C4-C5 spacing) / 2  (inferior)
        c45 = _SPACING_MM[('C4', 'C5')]
        z_of = {
            'C4': apex_z + c45 / 2,
            'C5': apex_z - c45 / 2,
        }
        # Walk superior from C4
        z_of['C3'] = z_of['C4'] + _SPACING_MM[('C3', 'C4')]
        z_of['C2'] = z_of['C3'] + _SPACING_MM[('C2', 'C3')]
        # Walk inferior from C5
        z_of['C6'] = z_of['C5'] - _SPACING_MM[('C5', 'C6')]
        z_of['C7'] = z_of['C6'] - _SPACING_MM[('C6', 'C7')]
        z_of['T1'] = z_of['C7'] - _SPACING_MM[('C7', 'T1')]

        # Step 4: median cord position for centroid_x/centroid_y.
        # Use HIGH-confidence rows when available — same reason as apex
        # selection.
        median_pool = high_track if high_track else track
        med_cx = self._median([t['cx'] for t in median_pool])
        med_cy = self._median([t['cy'] for t in median_pool])

        # Build output rows in superior-to-inferior order (V1 = C2, V7 = T1)
        rows = []
        for i, level in enumerate(_LEVEL_ORDER, start=1):
            rows.append({
                'V_idx':          i,
                'centroid_z_mm':  round(z_of[level], 3),
                'centroid_x_mm':  round(med_cx, 3),
                'centroid_y_mm':  round(med_cy, 3),
            })

        # Write CSV
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with open(out_csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['V_idx', 'centroid_z_mm',
                                              'centroid_x_mm', 'centroid_y_mm'])
            w.writeheader()
            for r in rows:
                w.writerow(r)

        # Diagnostic print (kept; runs once per /preimpression call)
        print(f'cervical_spine_v7: kyphosis apex at z={apex_z:+.2f}mm '
              f'(max cord_y={apex_row["cy"]:+.2f})')
        print(f'cervical_spine_v7: cord track median position: '
              f'x={med_cx:+.2f}, y={med_cy:+.2f}')
        print(f'cervical_spine_v7: synthesized vertebrae (V_idx → level → z):')
        for i, level in enumerate(_LEVEL_ORDER, start=1):
            print(f'  V{i} = {level}: z = {z_of[level]:+.2f} mm')

        return out_csv

    # ── result-extraction helpers ──────────────────────────────────────────

    @staticmethod
    def _as_float(v):
        if v is None or v == '' or v == 'None':
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(v):
        if v is None or v == '' or v == 'None':
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _summarize_cord_track(markers: list) -> dict:
        """Compact 3D cord-track summary from marker centroids."""
        if not markers:
            return {'n_markers': 0}
        xs = [m['cord_xyz_mm'][0] for m in markers
              if m.get('cord_xyz_mm') and m['cord_xyz_mm'][0] is not None]
        ys = [m['cord_xyz_mm'][1] for m in markers
              if m.get('cord_xyz_mm') and m['cord_xyz_mm'][1] is not None]
        zs = [m['cord_xyz_mm'][2] for m in markers
              if m.get('cord_xyz_mm') and m['cord_xyz_mm'][2] is not None]
        if not xs or not zs:
            return {'n_markers': len(markers)}
        return {
            'n_markers':      len(markers),
            'cord_x_mean_mm': sum(xs) / len(xs),
            'cord_x_range_mm': [min(xs), max(xs)],
            'cord_y_mean_mm': sum(ys) / len(ys) if ys else None,
            'cord_y_range_mm': [min(ys), max(ys)] if ys else None,
            'z_range_mm':     [min(zs), max(zs)],
        }
