"""
analyzers/cervical_spine_v7.py
==============================
Cervical spine v7 — z-profile architecture.

Replaces the legacy series-list-based cspine pipeline with:
  1. data_extract.run_from_series(series_list) → SliceFacts rows
  2. measure_spine.run(slice_facts_csv, out_dir) → cord_canal_per_slice.csv
                                                    + level_severity.csv (if vertebrae.csv present)
                                                    + disc_spacing.csv
  3. profile_spine.run(per_slice_csv, out_dir) → continuous_profile.csv
                                                  + anomaly_profile.csv

Wired into the existing dispatcher: registers under code 'CSPINE_V7' and label
'cervical_spine_v7' so requests with `?body_part=cervical_spine_v7` route here.

The legacy CSpineAnalyzer (v3/v4/v5/v6) remains the default for ?body_part=
cervical_spine and for auto-routed CSPINE. v7 is opt-in for now.

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

            # ── 1b. Synthesize vertebrae.csv inline ─────────────────────────
            # measure_spine's level classifier requires vertebrae.csv with
            # V_idx + centroid_z_mm (and uses centroid_x/y for cord anchor).
            # Rather than require a pre-existing vertebrae.csv from a separate
            # detection stage, we synthesize vertebra centroids by evenly
            # spacing N levels across the axial T2 z-range at typical
            # cervical anatomic spacing (~17 mm centroid-to-centroid).
            # measure_spine._classify_levels handles the C2/C3..T1 naming
            # automatically based on the count of vertebrae we emit.
            self._write_synthesized_vertebrae_csv(
                axial_t2_rows=axial_t2_rows,
                out_csv=scratch / 'vertebrae.csv',
            )

            # ── 2. measure_spine: per-slice cord/canal/CSF measurements ─────
            #     With vertebrae.csv now present at scratch/, measure_spine's
            #     _find_vertebrae_csv() picks it up and emits level_severity.csv
            #     in the same run.
            _ms.run(str(slice_facts_csv), out_dir=str(scratch))
            per_slice_csv = scratch / 'cord_canal_per_slice.csv'

            # ── 3. profile_spine: continuous + anomaly profile ──────────────
            _ps.run(str(per_slice_csv), out_dir=str(scratch))

            # ── 4. Assemble result dict in the existing analyzer schema ─────
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

    # ── helpers ─────────────────────────────────────────────────────────────

    # Typical cervical vertebral-body centroid-to-centroid spacing in mm.
    # C2-C3 ≈ 16, C3-C4 ≈ 17, C4-C5 ≈ 17, C5-C6 ≈ 18, C6-C7 ≈ 19, C7-T1 ≈ 22.
    # 17 mm is the median and works as a uniform spacing for an MVP.
    _CERVICAL_VERTEBRA_SPACING_MM = 17.0

    # Anatomic prior on z-range coverage: a cervical scan FOV almost always
    # covers C3-T1 (6 vertebrae) at minimum and often extends to C2 or T2.
    # We clamp the synthesized count to [4, 9] so degenerate z-ranges still
    # produce a plausible vertebra count rather than 0 or 50.
    _MIN_VERTEBRAE = 4
    _MAX_VERTEBRAE = 9

    def _write_synthesized_vertebrae_csv(self,
                                          axial_t2_rows: list,
                                          out_csv: Path) -> int:
        """Synthesize vertebra centroids from the axial T2 z-range and write
        them to `out_csv` as a vertebrae.csv that measure_spine can consume.

        Strategy:
          1. Take z_position_mm from every axial T2 SliceFacts row.
          2. Compute z_min, z_max. Total span = z_max - z_min.
          3. Vertebra count N = round(span / 17.0), clamped to [4, 9].
          4. Place vertebra centroids evenly across the span. centroid_x/y
             default to 0.0 (matches measure_spine's no-vertebrae anchor
             fallback, so cord-finding behavior is unchanged from the
             unsynthesized baseline).
          5. Write rows with columns V_idx, centroid_x_mm, centroid_y_mm,
             centroid_z_mm (the four columns measure_spine reads).

        Returns the number of vertebrae written. Returns 0 (and writes an
        empty CSV with headers) if the z-range is too small to host
        _MIN_VERTEBRAE at the assumed spacing.

        This is heuristic: real anatomic spacing varies (C2-C3 ≈ 16mm vs
        C7-T1 ≈ 22mm). Even spacing is a defensible MVP that the
        measure_spine._classify_levels naming logic handles cleanly
        (C3-top for ≤7, C2-top for 8+, sequential L{idx} extension beyond).
        Production-quality vertebra detection from sagittal T2 image
        intensities is out of scope for this push.
        """
        zs = []
        for r in axial_t2_rows:
            z = r.get('z_position_mm')
            try:
                zs.append(float(z))
            except (TypeError, ValueError):
                continue
        if not zs:
            self._write_vertebrae_rows([], out_csv)
            return 0

        z_min = min(zs)
        z_max = max(zs)
        span = z_max - z_min

        # How many vertebrae fit at the typical spacing?
        n_est = int(round(span / self._CERVICAL_VERTEBRA_SPACING_MM)) + 1
        n_vert = max(self._MIN_VERTEBRAE, min(self._MAX_VERTEBRAE, n_est))

        # If span is too small to host even _MIN_VERTEBRAE at meaningful
        # spacing, emit empty so measure_spine's no-vertebrae fallback kicks
        # in (cord_canal_per_slice.csv still gets written, just no
        # level_severity.csv).
        if span < (self._MIN_VERTEBRAE - 1) * 1.0:
            self._write_vertebrae_rows([], out_csv)
            return 0

        # Evenly space n_vert centroids across [z_min, z_max].
        if n_vert == 1:
            zs_centroids = [(z_min + z_max) / 2.0]
        else:
            step = span / (n_vert - 1)
            zs_centroids = [z_min + i * step for i in range(n_vert)]

        # Vertebrae are typically labeled superior-to-inferior (top-down).
        # In LPS patient coordinates, larger z = more superior. So V_idx=1
        # goes to the LARGEST z. measure_spine._classify_levels reads rows
        # in CSV order (it doesn't re-sort by z), and its naming walks the
        # sequence cranial→caudal, so we emit rows in descending z order.
        zs_centroids.sort(reverse=True)

        rows = []
        for i, z in enumerate(zs_centroids, start=1):
            rows.append({
                'V_idx':          i,
                'centroid_x_mm':  0.0,
                'centroid_y_mm':  0.0,
                'centroid_z_mm':  round(z, 4),
            })
        self._write_vertebrae_rows(rows, out_csv)
        return len(rows)

    @staticmethod
    def _write_vertebrae_rows(rows: list, out_csv: Path) -> None:
        """Write vertebrae rows in the CSV format measure_spine expects."""
        cols = ['V_idx', 'centroid_x_mm', 'centroid_y_mm', 'centroid_z_mm']
        with open(out_csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, '') for c in cols})

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
    def _summarize_cord_track(markers: list[dict]) -> dict:
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
