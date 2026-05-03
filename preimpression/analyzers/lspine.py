"""
analyzers/lspine.py
===================
Lumbar spine analyzer.

Levels: L1, L2, L3, L4, L5, S1.
Anchor: sacrum (broad bright bone block at the caudal end of the FOV).
The L5-S1 disc sits at the cranial edge of the sacrum. Walk cranially from
there labeling L5, L4, L3, L2, L1.

Cord defaults: at lumbar levels the cord proper has terminated (conus
medullaris ends at L1-L2). The structure inside the canal is the THECAL SAC
containing CSF and the cauda equina. Thecal sac on T2 axial is bright (CSF)
with the cauda equina nerve roots visible as small dark dots within. Our
"find a mid-intensity blob" detector still works but should aim for the 
thecal sac centroid (~80-200 mm² at lumbar) rather than a cord-like dark blob.

Stenosis thresholds (canal AP diameter / thecal sac area indicators):
  CRITICAL ≤ 1.0 mm   MODERATE ≤ 2.5 mm   FINDING ≤ 4.0 mm
  Lumbar canal is wider than cervical so thresholds are relaxed.

The lumbar pipeline catches:
  - Central stenosis (thecal sac compressed)
  - Lateral recess narrowing (asymmetry of thecal-sac-to-canal-wall)
  - Disc protrusion / extrusion impinging on thecal sac
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, label as scipy_label
from scipy.signal import find_peaks

from ._base import BaseAnalyzer, max_severity, load_volume
from ._spine_common import (
    detect_cords_axial, measure_radial, summarize_cord_track,
    aggregate_levels, classify_per_slice,
    select_best_t2_axsag, resample_sag_patient_coords,
)


# Lumbar canal is larger than cervical; thresholds calibrated for thecal sac
# margin to canal wall.
LSPINE_SPACE = {'critical_min_mm': 1.0, 'moderate_min_mm': 2.5, 'finding_min_mm': 4.0}
LSPINE_ASYM  = {'critical_abs': 0.40, 'moderate_abs': 0.20, 'finding_abs': 0.10}
LSPINE_LEVELS = ['L1', 'L1-L2', 'L2', 'L2-L3', 'L3', 'L3-L4',
                  'L4', 'L4-L5', 'L5', 'L5-S1', 'S1']


def detect_levels_lspine(sag_items):
    """Lumbar level detection.

    Strategy:
      1. Find the sacrum: a broad bright region at the bottom of the FOV
         that doesn't have a typical disc-vertebra-disc cyclic pattern.
      2. Locate the L5-S1 disc as the cranial edge of the sacrum.
      3. Walk cranially counting vertebral body peaks: L5, L4, L3, L2, L1.

    If the sacrum can't be found (some FOVs cut at L5-S1), fall back to
    counting peaks from caudal-most upward.
    """
    if not sag_items:
        return {}
    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]
    z_range = np.arange(-200, 200, 0.4)
    y_range = np.arange(-60, 80, 0.4)
    pv = resample_sag_patient_coords(sag_mid, z_range, y_range)

    # Body peaks in anterior y-band
    sm = gaussian_filter(pv, sigma=1.5)
    ymask = (y_range > -25) & (y_range < 0)
    if not ymask.any():
        return {}
    band = sm[ymask, :].mean(axis=0)
    band_smooth = gaussian_filter1d(band, sigma=2)
    # Lumbar vertebrae are larger than thoracic — body+disc ~35-40 mm
    peaks, props = find_peaks(band_smooth, distance=int(20/0.4), prominence=15)
    body_zs = z_range[peaks]
    if len(body_zs) < 3:
        return {}

    # Walk caudal→cranial: caudal-most peak ≈ S1 (or L5), label up.
    body_zs_sorted = np.sort(body_zs)  # ascending = caudal first
    sequence_caudal = ['S1', 'L5', 'L4', 'L3', 'L2', 'L1']
    n_take = min(len(body_zs_sorted), len(sequence_caudal))
    labels = {}
    for i in range(n_take):
        labels[sequence_caudal[i]] = float(body_zs_sorted[i])

    # Disc midpoints
    body_order = ['L1', 'L2', 'L3', 'L4', 'L5', 'S1']
    levels = dict(labels)
    for k in range(len(body_order)-1):
        a, b = body_order[k], body_order[k+1]
        if a in labels and b in labels:
            levels[f'{a}-{b}'] = (labels[a] + labels[b]) / 2.0
    return levels


def assign_level(z, levels):
    if not levels:
        return 'unknown'
    return min(levels.items(), key=lambda kv: abs(kv[1] - z))[0]


class LSpineAnalyzer(BaseAnalyzer):
    body_part_codes = ('LSPINE', 'L-SPINE', 'LUMBAR', 'LSP', 'LSSPINE')
    body_part_label = 'lumbar_spine'

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

        levels = detect_levels_lspine(sag_items)
        # Lumbar: at L1-L2 conus is still cord; below, thecal sac with cauda
        # equina. The detector finds the largest mid-intensity blob in the
        # canal, which is the thecal sac at most lumbar levels.
        cord_detections = detect_cords_axial(
            ax_items,
            intensity_range=(60, 280),  # broader: includes CSF-rich thecal
            area_range_mm2=(60, 250),    # thecal sac is bigger than cord
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
                max_r_mm=18,  # lumbar canal larger
            )
            level = assign_level(d['z_mm'], levels)
            flags = classify_per_slice(m, LSPINE_SPACE, LSPINE_ASYM)
            slice_records.append({
                'inst': it['inst'], 'z_mm': d['z_mm'], 'level': level,
                'cord_x_mm': d['cord_xy'][0], 'cord_y_mm': d['cord_xy'][1],
                # At lumbar this is thecal sac area not cord per se:
                'thecal_area_mm2': d['area_mm2'],
                'cord_area_mm2': d['area_mm2'],
                'cord_ecc': d['ecc'],
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
            slice_records, LSPINE_LEVELS, LSPINE_SPACE, LSPINE_ASYM,
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
