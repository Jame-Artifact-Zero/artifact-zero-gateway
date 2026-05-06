"""
analyzers/tspine.py
===================
Thoracic spine analyzer.

Levels: T1-T12.
Anchor: simplest reliable anchor is the C7-T1 junction (transition from 
cervical lordosis to thoracic kyphosis on the sagittal cord curve) or the 
last pair of ribs visible. Failing those, count vertebral bodies cranial→
caudal from the top of FOV; if FOV doesn't include C7, the cranial-most 
body is assumed T1 (with sanity check against rib visibility if available).

Cord defaults: same intensity as cervical (T2 80-220), area range slightly 
smaller (cord narrows below cervical enlargement: 30-110 mm²).

Stenosis thresholds:
  CRITICAL ≤ 0.5 mm   MODERATE ≤ 1.5 mm   FINDING ≤ 2.5 mm
  (same as cervical — clinical thresholds for cord-canal contact)

Note: T-spine acquisitions often have larger slice spacing (3-5 mm) than
C-spine (1-2 mm), so per-slice resolution is coarser. The pipeline still
works but the cord_area uncertainty is higher.
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from scipy.signal import find_peaks

from ._base import BaseAnalyzer, max_severity, load_volume
from ._spine_common import (
    detect_cords_axial, measure_radial, summarize_cord_track,
    aggregate_levels, classify_per_slice,
    select_best_t2_axsag, resample_sag_patient_coords,
)


TSPINE_SPACE = {'critical_min_mm': 0.5, 'moderate_min_mm': 1.5, 'finding_min_mm': 2.5}
TSPINE_ASYM  = {'critical_abs': 0.40, 'moderate_abs': 0.20, 'finding_abs': 0.10}
TSPINE_LEVELS = []
for i in range(1, 13):
    TSPINE_LEVELS.append(f'T{i}')
    if i < 12:
        TSPINE_LEVELS.append(f'T{i}-T{i+1}')
TSPINE_LEVELS.append('T12-L1')


def detect_levels_tspine(sag_items):
    """Thoracic level detection.

    Anchor: cranial-most vertebral body in cervical-thoracic transition region.
    If the FOV starts above the cervico-thoracic junction (sometimes T-spine
    studies include C7), the cervical body is detected via lordosis sign change
    and labeled relative to T1.
    """
    if not sag_items:
        return {}
    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]
    z_range = np.arange(-300, 100, 0.4)
    y_range = np.arange(-50, 60, 0.4)
    pv = resample_sag_patient_coords(sag_mid, z_range, y_range)

    # Vertebral body peaks in anterior y-band
    sm = gaussian_filter(pv, sigma=1.5)
    ymask = (y_range > -25) & (y_range < -5)
    if not ymask.any():
        return {}
    band = sm[ymask, :].mean(axis=0)
    band_smooth = gaussian_filter1d(band, sigma=2)
    # Thoracic vertebral spacing ~25 mm body+disc
    peaks, _ = find_peaks(band_smooth, distance=int(15/0.4), prominence=15)
    body_zs = z_range[peaks]
    if len(body_zs) < 3:
        return {}

    # Thoracic spans 12 levels. Walk cranial→caudal labeling T1, T2, ..., T12.
    body_zs_sorted = np.sort(body_zs)[::-1]
    sequence = [f'T{i}' for i in range(1, 13)]
    n_take = min(len(body_zs_sorted), len(sequence))
    labels = {sequence[i]: float(body_zs_sorted[i]) for i in range(n_take)}

    # Disc midpoints
    levels = dict(labels)
    body_seq = [f'T{i}' for i in range(1, 13)]
    for k in range(len(body_seq)-1):
        a, b = body_seq[k], body_seq[k+1]
        if a in labels and b in labels:
            levels[f'{a}-{b}'] = (labels[a] + labels[b]) / 2.0
    return levels


def assign_level(z, levels):
    if not levels:
        return 'unknown'
    return min(levels.items(), key=lambda kv: abs(kv[1] - z))[0]


class TSpineAnalyzer(BaseAnalyzer):
    body_part_codes = ('TSPINE', 'T-SPINE', 'THORACIC', 'TSP')
    body_part_label = 'thoracic_spine'

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

        ax_items = load_volume(ax_t2['files'])
        sag_items = load_volume(sag_t2['files'])

        levels = detect_levels_tspine(sag_items)
        cord_detections = detect_cords_axial(
            ax_items,
            intensity_range=(0, 0),  # signal: use adaptive percentile range
            # Thoracic cord narrower than cervical except at lumbar enlargement
            area_range_mm2=(25, 110),
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
            flags = classify_per_slice(m, TSPINE_SPACE, TSPINE_ASYM)
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
            slice_records, TSPINE_LEVELS, TSPINE_SPACE, TSPINE_ASYM,
        )

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
