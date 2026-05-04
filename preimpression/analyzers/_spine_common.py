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


def detect_cords_axial(ax_items, intensity_range=(80, 220),
                        area_range_mm2=(40, 150)):
    """Run cord-first detection across an axial volume.

    Returns dict {inst: {'cord_xy', 'cord_rc', 'area_mm2', 'ecc',
                          'recovered': bool, 'z_mm'}}.
    """
    if not ax_items:
        return {}

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



# ── Pathway v3 four-walk measurement additions ──────────────────────────────


def get_cord_intensity(smooth, ps_mm, cord_rc, sample_radius_mm=1.5):
    """Sample cord intensity from ~1.5mm circle around cord centroid."""
    cy, cx = cord_rc
    H, W = smooth.shape
    intens = []
    for r in np.arange(0, sample_radius_mm, 0.3):
        for t in np.linspace(0, 2 * np.pi, 8, endpoint=False):
            yp = int(round(cy + (r / ps_mm) * np.sin(t)))
            xp = int(round(cx + (r / ps_mm) * np.cos(t)))
            if 0 <= yp < H and 0 <= xp < W:
                intens.append(smooth[yp, xp])
    return float(np.median(intens)) if intens else 0.0

def find_cord_edge_gradient(profile, center_idx, direction, cord_I,
                              max_step=20, grad_threshold=None):
    """
    Find cord edge along profile starting from center_idx going in direction (+1 or -1).

    Cord edge = location where intensity gradient rises sharply (cord→CSF transition).
    Returns idx of edge or None if not found within max_step.

    NOTE: max_step should be set to cord_AP_radius_mm/ps_mm for vertical (~6mm)
    or cord_LR_radius_mm/ps_mm for horizontal (~8mm). Going further finds vertebral
    body marrow which has cord-similar intensity.
    """
    if grad_threshold is None:
        grad_threshold = cord_I * 0.15  # 15% of cord_I per pixel rise
    n = len(profile)
    for step in range(2, max_step):
        idx = center_idx + direction * step
        if idx < 1 or idx >= n - 1:
            break
        prev_idx = idx - direction
        next_idx = idx + direction
        if prev_idx < 0 or prev_idx >= n:
            continue
        if next_idx < 0 or next_idx >= n:
            continue
        local_grad = profile[next_idx] - profile[prev_idx]
        # First place where gradient is strongly positive (cord → CSF)
        # AND we're transitioning from cord level
        if local_grad > grad_threshold and profile[idx] >= cord_I * 0.85:
            # Verify rising trend continues
            ne_check = next_idx + direction
            if 0 <= ne_check < n and profile[ne_check] > profile[idx]:
                return idx
    return None

def horizontal_walks_v3(smooth, ps_mm, cord_rc, cord_I,
                          perp_window_mm=4, walk_max_mm=8,
                          csf_search_mm=8, csf_rel_thresh=1.30):
    """
    L→R and R→L walks with gradient edge detection.

    Tight bounds: walk only 8mm from cord centroid (cord LR radius ~5mm + buffer).
    """
    cy, cx = cord_rc
    H, W = smooth.shape
    perp_n = int(perp_window_mm / ps_mm)
    max_step = int(walk_max_mm / ps_mm)
    csf_search_n = int(csf_search_mm / ps_mm)
    cord_I_lo = cord_I * 0.80
    csf_threshold = cord_I * csf_rel_thresh

    rows = list(range(max(0, int(cy) - perp_n), min(H, int(cy) + perp_n + 1)))
    extents = {}
    csfs = {}
    for row in rows:
        profile = smooth[row, :]
        # Cord LEFT edge (image-low col, going to patient-right)
        cl = find_cord_edge_gradient(profile, int(cx), -1, cord_I, max_step)
        # Cord RIGHT edge (image-high col, going to patient-left)
        cr = find_cord_edge_gradient(profile, int(cx), +1, cord_I, max_step)
        if cl is None or cr is None or cr <= cl:
            continue
        extents[row] = (cl, cr)

        # PATIENT-RIGHT side (image-LEFT, walk further negative col)
        pR_W = None; pR_status = 'indeterminate'; pR_peak = None
        if cl - 3 >= 0:
            # Peak intensity in the rim region (3px past cord edge to csf_search distance)
            rim_lo = max(0, cl - csf_search_n)
            rim_hi = cl  # rim is between cord-edge and (cord-edge - csf_search)
            if rim_hi > rim_lo:
                pR_peak = float(np.max(profile[rim_lo:rim_hi]))
            mi = float(np.mean(profile[max(0, cl - 3):cl]))
            if mi >= csf_threshold:
                csf_end = None
                for col in range(cl - 3, max(0, cl - csf_search_n), -1):
                    if profile[col] < cord_I:
                        csf_end = col + 1
                        break
                pR_W = (cl - csf_end) * ps_mm if csf_end is not None else float(csf_search_mm)
                pR_status = 'free'
            elif mi < cord_I_lo:
                pR_W = 0.0
                pR_status = 'contact'

        # PATIENT-LEFT side (image-RIGHT, walk further positive col)
        pL_W = None; pL_status = 'indeterminate'; pL_peak = None
        if cr + 3 < W:
            rim_lo = cr
            rim_hi = min(W, cr + csf_search_n)
            if rim_hi > rim_lo:
                pL_peak = float(np.max(profile[rim_lo:rim_hi]))
            mi = float(np.mean(profile[cr:min(W, cr + 3)]))
            if mi >= csf_threshold:
                csf_end = None
                for col in range(cr + 3, min(W, cr + csf_search_n)):
                    if profile[col] < cord_I:
                        csf_end = col - 1
                        break
                pL_W = (csf_end - cr) * ps_mm if csf_end is not None else float(csf_search_mm)
                pL_status = 'free'
            elif mi < cord_I_lo:
                pL_W = 0.0
                pL_status = 'contact'

        csfs[row] = {
            'patient_left_W': pL_W, 'patient_left_status': pL_status, 'patient_left_peak': pL_peak,
            'patient_right_W': pR_W, 'patient_right_status': pR_status, 'patient_right_peak': pR_peak,
        }
    return extents, csfs

def vertical_walks_v3(smooth, ps_mm, cord_rc, cord_I,
                        perp_window_mm=4, walk_max_mm=6,
                        csf_search_mm=8, csf_rel_thresh=1.30):
    """
    T→B and B→T walks with gradient edge detection.

    Tight bounds: walk only 6mm from cord centroid (cord AP radius ~3-4mm + buffer).
    Going further finds vertebral body marrow.
    """
    cy, cx = cord_rc
    H, W = smooth.shape
    perp_n = int(perp_window_mm / ps_mm)
    max_step = int(walk_max_mm / ps_mm)
    csf_search_n = int(csf_search_mm / ps_mm)
    cord_I_lo = cord_I * 0.80
    csf_threshold = cord_I * csf_rel_thresh

    cols = list(range(max(0, int(cx) - perp_n), min(W, int(cx) + perp_n + 1)))
    extents = {}
    csfs = {}
    for col in cols:
        profile = smooth[:, col]
        # Cord TOP edge (image-low row, going to patient-anterior)
        ct = find_cord_edge_gradient(profile, int(cy), -1, cord_I, max_step)
        # Cord BOTTOM edge (image-high row, going to patient-posterior)
        cb = find_cord_edge_gradient(profile, int(cy), +1, cord_I, max_step)
        if ct is None or cb is None or cb <= ct:
            continue
        extents[col] = (ct, cb)

        # ANTERIOR side (image-TOP, walk further negative row)
        anterior_W = None; anterior_status = 'indeterminate'; anterior_peak = None
        if ct - 3 >= 0:
            rim_lo = max(0, ct - csf_search_n)
            rim_hi = ct
            if rim_hi > rim_lo:
                anterior_peak = float(np.max(profile[rim_lo:rim_hi]))
            mi = float(np.mean(profile[max(0, ct - 3):ct]))
            if mi >= csf_threshold:
                csf_end = None
                for row in range(ct - 3, max(0, ct - csf_search_n), -1):
                    if profile[row] < cord_I:
                        csf_end = row + 1
                        break
                anterior_W = (ct - csf_end) * ps_mm if csf_end is not None else float(csf_search_mm)
                anterior_status = 'free'
            elif mi < cord_I_lo:
                anterior_W = 0.0
                anterior_status = 'contact'

        # POSTERIOR side (image-BOTTOM, walk further positive row)
        posterior_W = None; posterior_status = 'indeterminate'; posterior_peak = None
        if cb + 3 < H:
            rim_lo = cb
            rim_hi = min(H, cb + csf_search_n)
            if rim_hi > rim_lo:
                posterior_peak = float(np.max(profile[rim_lo:rim_hi]))
            mi = float(np.mean(profile[cb:min(H, cb + 3)]))
            if mi >= csf_threshold:
                csf_end = None
                for row in range(cb + 3, min(H, cb + csf_search_n)):
                    if profile[row] < cord_I:
                        csf_end = row - 1
                        break
                posterior_W = (csf_end - cb) * ps_mm if csf_end is not None else float(csf_search_mm)
                posterior_status = 'free'
            elif mi < cord_I_lo:
                posterior_W = 0.0
                posterior_status = 'contact'

        csfs[col] = {
            'anterior_W': anterior_W, 'anterior_status': anterior_status, 'anterior_peak': anterior_peak,
            'posterior_W': posterior_W, 'posterior_status': posterior_status, 'posterior_peak': posterior_peak,
        }
    return extents, csfs


def find_runs(values, predicate, min_length=3):
    """Find runs of indices where predicate(value) is True.
    Returns list of (start, end) inclusive."""
    runs = []
    i = 0
    n = len(values)
    while i < n:
        if predicate(values[i]):
            j = i
            while j < n and predicate(values[j]):
                j += 1
            if j - i >= min_length:
                runs.append((i, j - 1))
            i = j
        else:
            i += 1
    return runs


def four_walk_v3(smooth, ps_mm, cord_rc, cord_I, band_mm=2):
    """
    Run all 4 walks (with v2 tight bounds + gradient edges).
    Aggregate W per direction at cord centroid ± band_mm.
    Compute consecutive-run statistics for filtering single-row spurious findings.

    Returns dict with per-direction:
      mean, min, free_mean, n_free, n_contact, n_indeterminate, n_total
      contact_run_max, indet_run_max, free_run_max
    """
    cy, cx = cord_rc
    eh, ch = horizontal_walks_v3(smooth, ps_mm, cord_rc, cord_I)
    ev, cv = vertical_walks_v3(smooth, ps_mm, cord_rc, cord_I)
    band_n = int(band_mm / ps_mm)
    band_rows = sorted([r for r in ch if abs(r - cy) <= band_n])
    band_cols = sorted([c for c in cv if abs(c - cx) <= band_n])

    out = {}
    for dirname, key, peak_key, src, band, status_key in [
        ('patient_left', 'patient_left_W', 'patient_left_peak', ch, band_rows, 'patient_left_status'),
        ('patient_right', 'patient_right_W', 'patient_right_peak', ch, band_rows, 'patient_right_status'),
        ('anterior', 'anterior_W', 'anterior_peak', cv, band_cols, 'anterior_status'),
        ('posterior', 'posterior_W', 'posterior_peak', cv, band_cols, 'posterior_status'),
    ]:
        all_Ws = [src[k][key] for k in band if src[k][key] is not None]
        free_Ws = [src[k][key] for k in band if src[k][status_key] == 'free']
        peaks = [src[k][peak_key] for k in band if src[k].get(peak_key) is not None]
        statuses = [src[k][status_key] for k in band]
        contact_runs = find_runs(statuses, lambda s: s == 'contact', 3)
        indet_runs = find_runs(statuses, lambda s: s == 'indeterminate', 3)
        free_runs = find_runs(statuses, lambda s: s == 'free', 3)
        out[dirname] = {
            'mean': float(np.mean(all_Ws)) if all_Ws else None,
            'min': float(min(all_Ws)) if all_Ws else None,
            'free_mean': float(np.mean(free_Ws)) if free_Ws else None,
            'peak_max': float(max(peaks)) if peaks else None,
            'peak_median': float(np.median(peaks)) if peaks else None,
            'n_free': sum(1 for s in statuses if s == 'free'),
            'n_contact': sum(1 for s in statuses if s == 'contact'),
            'n_indeterminate': sum(1 for s in statuses if s == 'indeterminate'),
            'n_total': len(band),
            'contact_run_max': max([r[1] - r[0] + 1 for r in contact_runs]) if contact_runs else 0,
            'indet_run_max': max([r[1] - r[0] + 1 for r in indet_runs]) if indet_runs else 0,
            'free_run_max': max([r[1] - r[0] + 1 for r in free_runs]) if free_runs else 0,
            'has_sustained_contact': len(contact_runs) > 0,
            'has_sustained_indet': len(indet_runs) > 0,
        }
    return out

def _classify_lesion_side(walk, peak_diff=100.0, max_csf=1500.0):
    """Determine lesion side from peak CSF intensity in L/R walks.

    Returns 'LEFT', 'RIGHT', or 'CENTRAL'.
    A lower peak on one side means the CSF rim is compressed or absent
    (disc/osteophyte intrusion). CSF is bright on T2 (~1400+); disc is dark.
    """
    pLpk = walk['patient_left'].get('peak_max')
    pRpk = walk['patient_right'].get('peak_max')
    if pLpk is None or pRpk is None:
        return 'unknown'
    if pLpk < pRpk - peak_diff and pLpk < max_csf:
        return 'LEFT'
    if pRpk < pLpk - peak_diff and pRpk < max_csf:
        return 'RIGHT'
    return 'CENTRAL'


def _majority_side(slices_at_level, weight_by_severity=True):
    """Aggregate lesion side across slices at one level.

    Weights CRITICAL slices more heavily so unclassified normal slices
    don't dilute the signal from the stenotic slice.
    """
    severity_weight = {'CRITICAL': 3, 'MODERATE': 2, 'FINDING': 1, 'NORMAL': 0}
    votes = {'LEFT': 0.0, 'RIGHT': 0.0, 'CENTRAL': 0.0}
    for s in slices_at_level:
        side = s.get('lesion_side_signal', 'unknown')
        if side not in votes:
            continue
        if weight_by_severity:
            sev = max_severity(s.get('flags', []))
            w = severity_weight.get(sev, 1)
        else:
            w = 1
        votes[side] += w
    if not any(votes.values()):
        return 'unknown'
    return max(votes, key=votes.get)
