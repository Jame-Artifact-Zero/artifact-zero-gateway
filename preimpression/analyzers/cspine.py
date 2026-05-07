"""
analyzers/cspine.py
===================
Cervical spine analyzer.

Levels: C2, C3, C4, C5, C6, C7 (+ T1 if in FOV)
Anchor: kyphosis apex (most posterior cord position) approximates C4-C5.
Cord defaults: 60-150 mm² area, T2 intensity 80-220.

Stenosis thresholds (cord-canal min space):
  CRITICAL ≤ 0.5 mm   MODERATE ≤ 1.5 mm   FINDING ≤ 2.5 mm
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from scipy.signal import find_peaks

from ._base import BaseAnalyzer, max_severity, is_t2
from ._spine_common import (
    detect_cords_axial, measure_radial, summarize_cord_track,
    aggregate_levels, classify_per_slice,
    select_best_t2_axsag, resample_sag_patient_coords,
    four_walk_v3, get_cord_intensity,
    _classify_lesion_side, _majority_side,
)


CSPINE_SPACE = {'critical_min_mm': 0.5, 'moderate_min_mm': 1.5, 'finding_min_mm': 2.5}
CSPINE_ASYM  = {'critical_abs': 0.40, 'moderate_abs': 0.20, 'finding_abs': 0.10}
CSPINE_LEVELS = ['C2', 'C2-C3', 'C3', 'C3-C4', 'C4', 'C4-C5',
                  'C5', 'C5-C6', 'C6', 'C6-C7', 'C7', 'C7-T1', 'T1']


def detect_levels_cspine(sag_items):
    """Kyphosis-anchored cervical level detection.

    Strategy:
      1. Resample midline sagittal into (y, z) patient coords.
      2. Detect cord centerline (brightest in cord y-band per z).
      3. Find kyphosis apex (most posterior cord y).
      4. Detect vertebral body z-positions from anterior bright band peaks.
      5. Walk cranial→caudal labeling C2 down; sanity-check apex sits at C4.
    """
    if not sag_items:
        return {}
    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]
    z_range = np.arange(-100, 110, 0.3)
    y_range = np.arange(-50, 60, 0.3)
    pv = resample_sag_patient_coords(sag_mid, z_range, y_range)

    # Cord centerline
    smoothed = gaussian_filter(pv, sigma=2.0)
    cord_zs = []; cord_ys = []
    for zi, z in enumerate(z_range):
        col = smoothed[:, zi]
        if col.max() < 30: continue
        ymask = (y_range > -15) & (y_range < 30)
        if not ymask.any(): continue
        sub = col.copy(); sub[~ymask] = 0
        peak_idx = int(np.argmax(sub))
        if sub[peak_idx] < 30: continue
        cord_zs.append(z)
        cord_ys.append(float(y_range[peak_idx]))
    cord_zs = np.array(cord_zs); cord_ys = np.array(cord_ys)

    # Kyphosis apex
    apex = None
    if len(cord_zs) >= 5:
        sm_y = gaussian_filter1d(cord_ys, sigma=3)
        apex = float(cord_zs[int(np.argmax(sm_y))])

    # Body peaks (anterior bright band)
    # Wide initial search, then refine. Philips: y≈-25 to -5.
    # GE sagittal FOV may differ — try progressively wider bands.
    sm = gaussian_filter(pv, sigma=1.5)
    body_zs = np.array([])
    for y_lo, y_hi in [(-25, -5), (-35, -5), (-40, 0), (-50, 10)]:
        ymask = (y_range > y_lo) & (y_range < y_hi)
        if not ymask.any():
            continue
        band = sm[ymask, :].mean(axis=0)
        band_smooth = gaussian_filter1d(band, sigma=2)
        peaks, _ = find_peaks(band_smooth, distance=int(8/0.3), prominence=15)
        body_zs = z_range[peaks]
        if len(body_zs) >= 4:
            break
    if len(body_zs) < 4:
        return {}

    # Label C2-T1 cranial→caudal, with kyphosis sanity check
    body_zs_sorted = np.sort(body_zs)[::-1]
    sequence = ['C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1']
    n_take = min(len(body_zs_sorted), len(sequence))
    labels = {sequence[i]: float(body_zs_sorted[i]) for i in range(n_take)}

    if apex is not None and labels:
        apex_label = min(labels.items(), key=lambda kv: abs(kv[1] - apex))[0]
        target_idx = 2  # apex should be near C4
        cur_idx = sequence.index(apex_label)
        shift = cur_idx - target_idx
        if shift > 0 and shift < n_take:
            new_labels = {}
            for i in range(min(n_take, len(sequence) - shift)):
                if (i + shift) < len(body_zs_sorted):
                    new_labels[sequence[i]] = float(body_zs_sorted[i + shift])
            if new_labels:
                labels = new_labels

    # Disc levels as midpoints
    ordered = ['C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1']
    levels = dict(labels)
    for k in range(len(ordered)-1):
        a, b = ordered[k], ordered[k+1]
        if a in labels and b in labels:
            levels[f'{a}-{b}'] = (labels[a] + labels[b]) / 2.0
    return levels


def assign_level(z, levels):
    if not levels:
        return 'unknown'
    return min(levels.items(), key=lambda kv: abs(kv[1] - z))[0]


class CSpineAnalyzer(BaseAnalyzer):
    body_part_codes = ('CSPINE', 'C-SPINE', 'CERVICAL', 'CSP')
    body_part_label = 'cervical_spine'

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

        levels = detect_levels_cspine(sag_items)
        cord_detections = detect_cords_axial(
            ax_items,
            intensity_range=(80, 220),
            area_range_mm2=(40, 150),
        )

        slice_records = []
        markers = []
        for it in ax_items:
            if it['inst'] not in cord_detections:
                continue
            d = cord_detections[it['inst']]
            m = measure_radial(
                it['img'], float(it['ps'][0]), d['cord_rc'], it['iop'],
                ipp=it['ipp'], ps=it['ps'], return_boundaries=True,
            )
            level = assign_level(d['z_mm'], levels)
            flags = classify_per_slice(m, CSPINE_SPACE, CSPINE_ASYM)
            # v3: four-walk measurement alongside existing radial
            # Contract (confirmed with research thread):
            #   1. four_walk_v3 expects Gaussian-smoothed image, sigma=1.5, float32
            #   2. cord_rc is (row, col) pixel indices — matches detect_cords_axial output
            #   3. lr_sum_mm = patient_left['mean'] + patient_right['mean']
            #   4. _classify_lesion_side takes the full four_walk_v3 return dict
            try:
                import numpy as _np
                _smooth = gaussian_filter(
                    it['img'].astype(_np.float32), sigma=1.5
                )
                cord_I = get_cord_intensity(_smooth, float(it['ps'][0]), d['cord_rc'])
                fw = four_walk_v3(_smooth, float(it['ps'][0]), d['cord_rc'], cord_I)
                pL = fw['patient_left'].get('mean')
                pR = fw['patient_right'].get('mean')
                lr_sum_mm = float(pL + pR) if (pL is not None and pR is not None) else float('nan')
                lesion_side = _classify_lesion_side(fw)
            except Exception:
                lr_sum_mm = float('nan')
                lesion_side = 'unknown'
                fw = {}

            slice_records.append({
                'inst': it['inst'], 'z_mm': d['z_mm'], 'level': level,
                'cord_x_mm': d['cord_xy'][0], 'cord_y_mm': d['cord_xy'][1],
                'cord_area_mm2': d['area_mm2'], 'cord_ecc': d['ecc'],
                'recovered': d['recovered'],
                'space_min_mm': m['space_min_mm'],
                'space_mean_mm': m['space_mean_mm'],
                'space_max_mm': m['space_max_mm'],
                'left_space_mm': m['left_space_mm'],
                'right_space_mm': m['right_space_mm'],
                'asym_lr': m['asym_lr'],
                'cord_intensity': m['cord_intensity'],
                'lr_sum_mm': lr_sum_mm,
                'lesion_side_signal': lesion_side,
                'flags': flags,
            })
            markers.append({
                'inst': it['inst'], 'level': level,
                'cord_xyz_mm': [round(float(d['cord_xy'][0]), 3),
                                 round(float(d['cord_xy'][1]), 3),
                                 round(float(d['z_mm']), 3)],
                'cord_area_mm2': round(float(d['area_mm2']), 2),
                'recovered': bool(d['recovered']),
                'space_min_mm': round(float(m['space_min_mm']), 2),
                'space_mean_mm': round(float(m['space_mean_mm']), 2),
                'asym_lr': round(float(m['asym_lr']), 3),
                'severity': max_severity(flags),
                'cord_boundary_3d': m['cord_boundary_3d'],
                'canal_boundary_3d': m['canal_boundary_3d'],
                'radial_angles_rad': m['radial_angles_rad'],
            })

        level_summaries, all_flags = aggregate_levels(
            slice_records, CSPINE_LEVELS, CSPINE_SPACE, CSPINE_ASYM,
        )

        # v3: add lr_sum and lesion_side to each level summary
        from collections import defaultdict
        _by_level = defaultdict(list)
        for s in slice_records:
            _by_level[s['level']].append(s)
        for ls in level_summaries:
            rows = _by_level.get(ls['level'], [])
            lr_sums = [s['lr_sum_mm'] for s in rows
                       if isinstance(s.get('lr_sum_mm'), float) and s['lr_sum_mm'] == s['lr_sum_mm']]
            ls['lr_sum_min_mm'] = float(min(lr_sums)) if lr_sums else None
            ls['lr_sum_mean_mm'] = float(sum(lr_sums) / len(lr_sums)) if lr_sums else None
            ls['lesion_side'] = _majority_side(rows)

        overall = max_severity(all_flags)
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in all_flags:
            sev = f['severity'].lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not all_flags:
            counts['normal'] = 1

        return {
            'status': overall,
            'body_part_label': self.body_part_label,
            'series_used': {
                'axial_t2': {'series_description': ax_t2['series_description'],
                              'n_slices': ax_t2['n_slices'],
                              'series_uid': ax_t2['series_uid']},
                'sagittal_t2': {'series_description': sag_t2['series_description'],
                                 'n_slices': sag_t2['n_slices'],
                                 'series_uid': sag_t2['series_uid']},
            },
            'levels_detected': levels,
            'impression': {
                'overall_status': overall,
                'counts': counts,
                'flags': all_flags,
            },
            'level_summaries': level_summaries,
            'slice_measurements': slice_records,
            'markers': markers,
            'cord_track_3d': summarize_cord_track(markers),
        }
