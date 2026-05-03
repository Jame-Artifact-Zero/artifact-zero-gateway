"""
analyzers/_spine_common.py
==========================
Shared spine analyzer machinery: cord-first detection and radial measurement.

Used by cervical, thoracic, and lumbar analyzers. Each spine analyzer differs
only in:
  - level-detection strategy (kyphosis-anchored / rib-counting / sacrum-anchored)
  - severity thresholds (canal sizes differ by region)
  - intensity priors (cervical cord is small, lumbar thecal sac is bigger)

This module hands them the cord-tracking primitives.
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import (
    gaussian_filter, gaussian_filter1d, label as scipy_label,
    center_of_mass, binary_opening, binary_closing,
)

from ._base import slice_z_center, patient_xy_from_pix, pix_from_patient_xy


def find_cord_intensity(img, ps_mm, search_center_rc=None,
                         search_radius_mm=10,
                         intensity_range=(80, 220),
                         area_range_mm2=(40, 150),
                         ecc_max=0.85):
    """Find cord (or analogous central structure) by intensity + shape +
    location. Returns dict {comp, rc, area, ecc, score, d_to_s} or None.

    Defaults are tuned for cervical cord on T2. Override via kwargs:
      - cervical cord: area 40-150, intensity 80-220
      - lumbar thecal sac (mostly CSF): higher intensity, larger area
      - thoracic cord: similar to cervical
    """
    smooth = gaussian_filter(img, sigma=1.5)
    H, W = smooth.shape
    cm = (smooth >= intensity_range[0]) & (smooth <= intensity_range[1])
    cm = binary_closing(cm, iterations=2)
    cm = binary_opening(cm, iterations=1)
    if search_center_rc is not None:
        yy, xx = np.indices(cm.shape)
        d = np.hypot(yy - search_center_rc[0], xx - search_center_rc[1]) * ps_mm
        cm &= (d < search_radius_mm)
    labeled, n = scipy_label(cm)
    if n == 0:
        return None
    target_area = (area_range_mm2[0] + area_range_mm2[1]) / 2.0
    cands = []
    for ll in range(1, n+1):
        comp = labeled == ll
        a = comp.sum() * ps_mm**2
        if not (area_range_mm2[0] <= a <= area_range_mm2[1]):
            continue
        cy, cx = center_of_mass(comp)
        coords = np.argwhere(comp)
        if len(coords) < 5:
            continue
        rys = coords[:, 0] - cy
        rxs = coords[:, 1] - cx
        Ixx = (rys**2).mean(); Iyy = (rxs**2).mean(); Ixy = (rys*rxs).mean()
        T = (Ixx + Iyy)/2
        det = Ixx*Iyy - Ixy**2
        rad = max(0, T**2 - det)
        l1 = T + np.sqrt(rad); l2 = T - np.sqrt(rad)
        if l1 <= 0:
            continue
        ecc = np.sqrt(1 - l2/l1) if (l1 > l2 > 0) else 0.99
        if ecc > ecc_max:
            continue
        d_to_s = (np.hypot(cy - search_center_rc[0],
                           cx - search_center_rc[1]) * ps_mm
                  if search_center_rc is not None else 0)
        ar_score = np.exp(-((a - target_area)**2) / (40**2))
        ec_score = 1 - ecc
        loc_score = np.exp(-d_to_s / 5.0)
        cands.append({
            'comp': comp, 'rc': (float(cy), float(cx)),
            'area': float(a), 'ecc': float(ecc),
            'd_to_s': float(d_to_s),
            'score': float(ar_score * ec_score * loc_score),
        })
    if not cands:
        return None
    cands.sort(key=lambda c: -c['score'])
    return cands[0]


def _compute_cord_intensity_range(img, ps_mm):
    """Percentile-based cord intensity bracket from in-body voxel distribution.

    Cord on T2 sits at ~25th-70th percentile of in-body voxels regardless
    of scanner make. Replaces hardcoded (80, 220) range that was calibrated
    on Philips Ingenia only.
    """
    from scipy.ndimage import gaussian_filter
    smooth = gaussian_filter(img, sigma=1.5)
    in_body = smooth > (smooth.max() * 0.05)
    if in_body.sum() < 100:
        return (80.0, 220.0)  # fallback
    pcts = np.percentile(smooth[in_body], [25, 70])
    return (float(pcts[0]), float(pcts[1]))


def detect_cords_axial(ax_items, intensity_range=(80, 220),
                        area_range_mm2=(40, 150)):
    """Run cord-first detection across an axial volume.

    intensity_range=(0,0) signals adaptive percentile-based range per slice.
    Returns dict {inst: {'cord_xy', 'cord_rc', 'area_mm2', 'ecc',
                          'recovered': bool, 'z_mm'}}.
    """
    if not ax_items:
        return {}

    # Adaptive intensity range — compute from the mid-slice if (0,0) signalled
    _use_adaptive = (intensity_range == (0, 0) or intensity_range is None)
    if _use_adaptive:
        mid_img = ax_items[len(ax_items) // 2]['img']
        mid_ps  = float(ax_items[len(ax_items) // 2]['ps'][0])
        intensity_range = _compute_cord_intensity_range(mid_img, mid_ps)

    n = len(ax_items)
    mid_idx = n // 2
    anchor_idx = None
    anchor_res = None

    # Try slices in order: middle, then ±1, ±2, ...
    for offset in range(n):
        for sign in (0, 1, -1):
            if sign == 0 and offset != 0:
                continue
            i = mid_idx + sign * offset
            if not (0 <= i < n):
                continue
            it = ax_items[i]
            ipp, iop, ps = it['ipp'], it['iop'], it['ps']
            try:
                row0, col0 = pix_from_patient_xy(it, 0, 0)
                search_rc = (row0, col0)
            except np.linalg.LinAlgError:
                continue
            res = find_cord_intensity(
                it['img'], float(ps[0]),
                search_center_rc=search_rc, search_radius_mm=10,
                intensity_range=intensity_range,
                area_range_mm2=area_range_mm2,
            )
            if res is not None:
                anchor_idx = i
                anchor_res = res
                break
        if anchor_res is not None:
            break

    if anchor_res is None:
        return {}

    detections = {}
    anchor_it = ax_items[anchor_idx]

    def _record(it, res, recovered=False):
        cy, cx = res['rc']
        ipp, iop, ps = it['ipp'], it['iop'], it['ps']
        cord_xy3 = ipp + cx*ps[1]*iop[0:3] + cy*ps[0]*iop[3:6]
        detections[it['inst']] = {
            'cord_xy': (float(cord_xy3[0]), float(cord_xy3[1])),
            'cord_rc': (float(cy), float(cx)),
            'area_mm2': float(res['area']),
            'ecc': float(res['ecc']),
            'recovered': bool(recovered),
            'z_mm': slice_z_center(it),
        }

    _record(anchor_it, anchor_res, recovered=False)

    for direction in (-1, 1):
        last_rc = anchor_res['rc']
        idx = anchor_idx + direction
        while 0 <= idx < n:
            it = ax_items[idx]
            res = find_cord_intensity(
                it['img'], float(it['ps'][0]),
                search_center_rc=last_rc, search_radius_mm=8,
                intensity_range=intensity_range,
                area_range_mm2=area_range_mm2,
            )
            if res is None:
                res = find_cord_intensity(
                    it['img'], float(it['ps'][0]),
                    search_center_rc=last_rc, search_radius_mm=15,
                    intensity_range=intensity_range,
                    area_range_mm2=area_range_mm2,
                )
            if res is not None:
                _record(it, res, recovered=False)
                last_rc = res['rc']
            idx += direction

    # Recovery pass
    if not detections:
        return {}
    found_zs = np.array([d['z_mm'] for d in detections.values()])
    found_xs = np.array([d['cord_xy'][0] for d in detections.values()])
    found_ys = np.array([d['cord_xy'][1] for d in detections.values()])
    order = np.argsort(found_zs)
    found_zs = found_zs[order]
    found_xs = gaussian_filter1d(found_xs[order], sigma=2)
    found_ys = gaussian_filter1d(found_ys[order], sigma=2)

    for it in ax_items:
        if it['inst'] in detections:
            continue
        z = slice_z_center(it)
        if len(found_zs) < 2:
            continue
        pred_x = float(np.interp(z, found_zs, found_xs))
        pred_y = float(np.interp(z, found_zs, found_ys))
        try:
            row_p, col_p = pix_from_patient_xy(it, pred_x, pred_y)
        except np.linalg.LinAlgError:
            continue

        # Three relaxation steps for stenotic regions
        i_lo, i_hi = intensity_range
        a_lo, a_hi = area_range_mm2
        for il, ih, al, ah, ec in [
            (i_lo, i_hi, a_lo, a_hi, 0.85),
            (i_lo, i_hi+20, max(20, a_lo-10), a_hi+50, 0.92),
            (max(50, i_lo-20), i_hi+40, max(15, a_lo-20), a_hi+100, 0.95),
        ]:
            res = find_cord_intensity(
                it['img'], float(it['ps'][0]),
                search_center_rc=(float(row_p), float(col_p)),
                search_radius_mm=6,
                intensity_range=(il, ih),
                area_range_mm2=(al, ah),
                ecc_max=ec,
            )
            if res is not None:
                _record(it, res, recovered=True)
                break

    return detections


def measure_radial(img, ps_mm, cord_rc, iop, n_angles=72,
                    max_r_mm=12, bone_threshold=60,
                    ipp=None, ps=None, return_boundaries=False):
    """From cord centroid, march outward at n_angles directions and find
    the cord boundary (intensity transition) and canal/thecal-sac boundary
    (intensity drop below bone_threshold)."""
    smooth = gaussian_filter(img, sigma=1.0)
    H, W = smooth.shape
    cy, cx = cord_rc
    angles = np.linspace(0, 2*np.pi, n_angles, endpoint=False)
    yy, xx = np.indices(smooth.shape)
    d_to_cord = np.hypot(yy - cy, xx - cx) * ps_mm
    cord_zone = d_to_cord < 2.0
    cord_intensity = float(smooth[cord_zone].mean()) if cord_zone.any() else 150

    cord_radius_mm = np.zeros(n_angles)
    canal_radius_mm = np.zeros(n_angles)

    for k, th in enumerate(angles):
        dy = np.sin(th); dx = np.cos(th)
        cord_edge = 0.0
        canal_edge = 0.0
        in_cord = True
        for r_mm in np.arange(0, max_r_mm, 0.3):
            r_pix = r_mm / ps_mm
            yp = int(round(cy + r_pix*dy))
            xp = int(round(cx + r_pix*dx))
            if not (0 <= yp < H and 0 <= xp < W):
                break
            v = smooth[yp, xp]
            if in_cord:
                if v < cord_intensity * 0.5 or v > cord_intensity * 1.6:
                    cord_edge = r_mm
                    in_cord = False
            if v < bone_threshold:
                canal_edge = r_mm
                break
        if cord_edge == 0 and canal_edge == 0:
            cord_edge = max_r_mm * 0.4
            canal_edge = max_r_mm
        elif canal_edge == 0:
            canal_edge = max_r_mm
        elif cord_edge == 0:
            cord_edge = canal_edge * 0.5
        cord_radius_mm[k] = cord_edge
        canal_radius_mm[k] = canal_edge

    distances = canal_radius_mm - cord_radius_mm
    if iop[0] > 0:
        right_a = (angles >= np.pi/2) & (angles < 3*np.pi/2)
    else:
        right_a = ~((angles >= np.pi/2) & (angles < 3*np.pi/2))
    left_d = float(distances[~right_a].mean())
    right_d = float(distances[right_a].mean())
    asym = (left_d - right_d) / (left_d + right_d + 1e-9)

    out = {
        'space_min_mm': float(distances.min()),
        'space_mean_mm': float(distances.mean()),
        'space_max_mm': float(distances.max()),
        'left_space_mm': left_d,
        'right_space_mm': right_d,
        'asym_lr': float(asym),
        'cord_intensity': cord_intensity,
    }

    if return_boundaries and ipp is not None and ps is not None:
        cord_3d = []
        canal_3d = []
        for k, th in enumerate(angles):
            dy_a = np.sin(th); dx_a = np.cos(th)
            ce_y = cy + (cord_radius_mm[k] / ps_mm) * dy_a
            ce_x = cx + (cord_radius_mm[k] / ps_mm) * dx_a
            ce_3d = ipp + ce_x*ps[1]*iop[0:3] + ce_y*ps[0]*iop[3:6]
            cord_3d.append(ce_3d.tolist())

            cn_y = cy + (canal_radius_mm[k] / ps_mm) * dy_a
            cn_x = cx + (canal_radius_mm[k] / ps_mm) * dx_a
            cn_3d = ipp + cn_x*ps[1]*iop[0:3] + cn_y*ps[0]*iop[3:6]
            canal_3d.append(cn_3d.tolist())
        out['cord_boundary_3d'] = cord_3d
        out['canal_boundary_3d'] = canal_3d
        out['radial_angles_rad'] = angles.tolist()
        out['cord_radii_mm'] = cord_radius_mm.tolist()
        out['canal_radii_mm'] = canal_radius_mm.tolist()

    return out


def summarize_cord_track(markers):
    """3D track stats from per-slice markers."""
    if not markers:
        return {}

    xs = np.array([m['cord_xyz_mm'][0] for m in markers])
    ys = np.array([m['cord_xyz_mm'][1] for m in markers])
    zs = np.array([m['cord_xyz_mm'][2] for m in markers])
    levels_per = [m.get('level', '') for m in markers]

    order = np.argsort(zs)[::-1]
    xs = xs[order]; ys = ys[order]; zs = zs[order]
    levels_sorted = [levels_per[i] for i in order]

    sm_x = gaussian_filter1d(xs, sigma=2)
    sm_y = gaussian_filter1d(ys, sigma=2)
    devs_x = xs - sm_x
    devs_y = ys - sm_y

    return {
        'n_markers': int(len(markers)),
        'z_range_mm': [float(zs.min()), float(zs.max())],
        'cord_x_range_mm': [float(xs.min()), float(xs.max())],
        'cord_y_range_mm': [float(ys.min()), float(ys.max())],
        'cord_x_mean_mm': float(xs.mean()),
        'cord_y_mean_mm': float(ys.mean()),
        'cord_x_max_lateral_mm': float(np.abs(xs).max()),
        'curve_apex_y_mm': float(ys.max()),
        'curve_apex_z_mm': float(zs[int(np.argmax(ys))]),
        'curve_nadir_y_mm': float(ys.min()),
        'curve_nadir_z_mm': float(zs[int(np.argmin(ys))]),
        'max_focal_lateral_dev_mm': float(np.abs(devs_x).max()),
        'max_focal_anteroposterior_dev_mm': float(np.abs(devs_y).max()),
    }


def aggregate_levels(slice_records, level_order, space_thresholds, asym_thresholds):
    """Common per-level aggregation shared by all spine analyzers.

    Args:
        slice_records: list of per-slice records with 'level', 'space_min_mm', etc.
        level_order: list of level names in cranial→caudal order for output.
        space_thresholds: dict with keys 'critical_min_mm', 'moderate_min_mm',
                          'finding_min_mm'
        asym_thresholds: dict with keys 'critical_abs', 'moderate_abs', 'finding_abs'
    """
    from collections import defaultdict
    by_level = defaultdict(list)
    for s in slice_records:
        by_level[s['level']].append(s)

    level_summaries = []
    all_flags = []
    for level in level_order:
        if level not in by_level:
            continue
        rows = by_level[level]
        summary = _aggregate_one_level(rows, space_thresholds, asym_thresholds)
        summary['level'] = level
        level_summaries.append(summary)
        for f in summary['flags']:
            all_flags.append({**f, 'level': level})

    return level_summaries, all_flags


def _aggregate_one_level(rows, space_thr, asym_thr):
    n = len(rows)
    cord_areas = [s['cord_area_mm2'] for s in rows]
    smins = [s['space_min_mm'] for s in rows]
    smeans = [s['space_mean_mm'] for s in rows]
    asyms = [s['asym_lr'] for s in rows]
    lspaces = [s['left_space_mm'] for s in rows]
    rspaces = [s['right_space_mm'] for s in rows]

    summary = {
        'n_slices': n,
        'n_recovered': sum(1 for s in rows if s.get('recovered')),
        'cord_area_mean_mm2': float(np.mean(cord_areas)),
        'space_min_mm': float(min(smins)),
        'space_mean_mm': float(np.mean(smeans)),
        'asym_lr_mean': float(np.mean(asyms)),
        'asym_lr_max_abs': float(max(asyms, key=abs)),
        'left_space_mm': float(np.mean(lspaces)),
        'right_space_mm': float(np.mean(rspaces)),
    }

    flags = []
    if summary['space_min_mm'] <= space_thr['critical_min_mm']:
        flags.append({'label': 'cord-canal contact at level', 'severity': 'CRITICAL'})
    elif summary['space_min_mm'] <= space_thr['moderate_min_mm']:
        flags.append({'label': 'severe narrowing at level', 'severity': 'MODERATE'})
    elif summary['space_min_mm'] <= space_thr['finding_min_mm']:
        flags.append({'label': 'mild narrowing at level', 'severity': 'FINDING'})

    if n >= 2:
        same_side = sum(1 for a in asyms if (a > 0) == (summary['asym_lr_mean'] > 0))
        side = 'left' if summary['asym_lr_mean'] > 0 else 'right'
        if abs(summary['asym_lr_mean']) >= asym_thr['critical_abs'] and same_side >= n - 1:
            flags.append({'label': f'sustained marked {side}-side asymmetry',
                          'severity': 'CRITICAL'})
        elif abs(summary['asym_lr_mean']) >= asym_thr['moderate_abs'] and same_side >= n - 1:
            flags.append({'label': f'sustained {side}-side asymmetry',
                          'severity': 'MODERATE'})
        elif abs(summary['asym_lr_mean']) >= asym_thr['finding_abs'] and same_side == n:
            flags.append({'label': f'consistent {side}-side asymmetry',
                          'severity': 'FINDING'})

    summary['flags'] = flags
    return summary


def classify_per_slice(meas, space_thr, asym_thr):
    """Per-slice severity tags."""
    flags = []
    smin = meas['space_min_mm']
    asym = abs(meas['asym_lr'])
    if smin <= space_thr['critical_min_mm']:
        flags.append({'label': 'cord-canal contact', 'severity': 'CRITICAL'})
    elif smin <= space_thr['moderate_min_mm']:
        flags.append({'label': 'severe canal narrowing', 'severity': 'MODERATE'})
    elif smin <= space_thr['finding_min_mm']:
        flags.append({'label': 'mild canal narrowing', 'severity': 'FINDING'})

    if asym >= asym_thr['critical_abs']:
        side = 'left' if meas['asym_lr'] > 0 else 'right'
        flags.append({'label': f'marked {side}-side asymmetry', 'severity': 'CRITICAL'})
    elif asym >= asym_thr['moderate_abs']:
        side = 'left' if meas['asym_lr'] > 0 else 'right'
        flags.append({'label': f'moderate {side}-side asymmetry', 'severity': 'MODERATE'})
    elif asym >= asym_thr['finding_abs']:
        side = 'left' if meas['asym_lr'] > 0 else 'right'
        flags.append({'label': f'mild {side}-side asymmetry', 'severity': 'FINDING'})
    return flags


def select_best_t2_axsag(series_list):
    """Pick the highest-resolution axial T2 and sagittal T2 series."""
    from ._base import is_t2

    axials = [s for s in series_list
              if s['orientation'] == 'AX' and is_t2(s['sample_ds'])]
    sagittals = [s for s in series_list
                  if s['orientation'] == 'SAG' and is_t2(s['sample_ds'])]

    def rank(s):
        ds = s['sample_ds']
        rows = int(getattr(ds, 'Rows', 256))
        cols = int(getattr(ds, 'Columns', 256))
        return s['n_slices'] * rows * cols

    axial = max(axials, key=rank) if axials else None
    sag = max(sagittals, key=rank) if sagittals else None
    return axial, sag


def resample_sag_patient_coords(item, z_range, y_range):
    """Resample a sagittal slice into a patient (y, z) grid."""
    img = item['img']
    H, W = img.shape
    ipp, iop, ps = item['ipp'], item['iop'], item['ps']
    Y, Z = np.meshgrid(y_range, z_range, indexing='ij')

    A = np.array([
        [ps[0]*iop[4], ps[1]*iop[1]],
        [ps[0]*iop[5], ps[1]*iop[2]],
    ])
    try:
        A_inv = np.linalg.inv(A)
    except np.linalg.LinAlgError:
        return np.zeros_like(Y)
    by = Y - ipp[1]
    bz = Z - ipp[2]
    rows = A_inv[0, 0] * by + A_inv[0, 1] * bz
    cols = A_inv[1, 0] * by + A_inv[1, 1] * bz

    valid = (rows >= 0) & (rows < H-1) & (cols >= 0) & (cols < W-1)
    r0 = np.clip(np.floor(rows).astype(int), 0, H-1)
    c0 = np.clip(np.floor(cols).astype(int), 0, W-1)
    r1 = np.clip(r0 + 1, 0, H-1)
    c1 = np.clip(c0 + 1, 0, W-1)
    fr = rows - np.floor(rows); fc = cols - np.floor(cols)
    out = ((1-fr)*(1-fc)*img[r0, c0] + fr*(1-fc)*img[r1, c0] +
           (1-fr)*fc*img[r0, c1] + fr*fc*img[r1, c1])
    out[~valid] = 0
    return out
