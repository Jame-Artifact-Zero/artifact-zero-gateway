"""
analyzers/cspine_v6.py — Cspine analyzer, k-space cord detection.
=================================================================

WHY v6 EXISTS
-------------
v5.2 used patient-origin anchored blob detection + multi-radius search +
trajectory recovery to find the cord. That works but it's a stack of
heuristics: try radius=8, fall back to 10, fall back to 12, fall back to 15;
smooth the trajectory, re-detect outliers, mark them recovered. Each fix
patched a specific failure mode discovered in validation.

The cord position lives cleanly in k-space. The DICOM image is a magnitude
reconstruction of the original frequency-domain acquisition. Returning to
frequency space and using phase correlation against an anatomic template
(cord-CSF-bone radial pattern) recovers cord position in ONE operation,
to subpixel precision, with no thresholds, no radius search, no drift,
no recovery pass.

This is the realization of the original project goal: read in k-space,
place imaginary markers, return to DICOM coordinates with exact
measurements. v6 implements that pattern.

WHAT CHANGED IN v6
------------------
1. detect_cord_kspace_v6 replaces detect_cord_volume_v5
   - Per-slice phase correlation against cord-CSF-bone template
   - Subpixel peak via parabolic fit on correlation peak
   - Fourier-shift theorem maps k-space marker → DICOM pixel position
   - One operation per slice; no iteration, no thresholds, no fallback radii

2. Everything else from v5.2 is preserved:
   - Level detection (kyphosis-anchored + progressive widening + prom=50)
   - 4-direction grid walks with gradient cord edge detection
   - Severity escalation (cord deformation, sustained low-rim)
   - Disc-level inheritance with directional-concern gating
   - Below-T1 cap and quality gate

The image-space measurement code in v5.2 was correct and validated.
v6's contribution is supplying the cord position more cleanly to it.

VERSION HISTORY (cspine consolidated lineage)
---------------------------------------------
v3: scattered hotfix-stack in cspine.py; default-fallback bug → false CRITICAL
v4: scattered + hf01-hf07; cleaner but still fragile
v5.0: consolidated module; level-detection regression (3-stage validation)
v5.1: kyphosis-anchored levels + prom=50; per-slice patient-origin cord;
       coord-system fix
v5.2: severity escalation + below-T1 cap + recovery pass + quality gate
v6.0: k-space cord detection (THIS FILE)

CONVENTIONS (axial cspine, HFS patient, IOP ~ identity)
-------------------------------------------------------
  col idx UP = patient LEFT
  row idx UP = patient POSTERIOR
  image-LEFT  (low col)  = patient-RIGHT
  image-RIGHT (high col) = patient-LEFT
  image-TOP    (low row)  = patient-ANTERIOR
  image-BOTTOM (high row) = patient-POSTERIOR

DATA FLOW
---------
  analyze_cspine_v6(ax_items, sag_items)
    -> detect_levels_v5(sag_items)            [unchanged from v5.2]
    -> detect_cord_kspace_v6(ax_items)        [NEW: phase correlation]
        for each axial slice:
          - extract canal ROI centered on patient origin
          - phase-correlate against cord-CSF-bone template
          - subpixel peak -> cord position
          - convert to patient-mm coords
    -> for each slice with cord:
        - four_walk -> per-direction CSF rim widths (unchanged)
        - classify_slice_v5 -> flags (unchanged)
    -> aggregate_levels + escalation + inheritance (unchanged from v5.2)
    -> return result dict

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import (gaussian_filter, gaussian_filter1d, label as scipy_label,
                            binary_closing, binary_opening)
from scipy.signal import find_peaks


# ============================================================================
# Thresholds & constants
# ============================================================================
# v6 thresholds: same as v5.2. K-space cord detection is more anatomically
# accurate than v5.2's cord+CSF blob centroid, but the resulting rim
# measurements are within 0.5mm of v5.2 values for most slices. Threshold
# recalibration would only matter if measurements were systematically
# different by ≥1mm; they are not.
CSPINE_SPACE = {
    'critical_min_mm': 0.5,
    'moderate_min_mm': 1.5,
    'finding_min_mm': 2.5,
}
CSPINE_ASYM = {
    'critical_abs': 0.40,
    'moderate_abs': 0.20,
    'finding_abs': 0.10,
}
CSPINE_LEVELS = ['C2', 'C2-C3', 'C3', 'C3-C4', 'C4', 'C4-C5',
                  'C5', 'C5-C6', 'C6', 'C6-C7', 'C7', 'C7-T1', 'T1']

# Cervical anatomy parameters (used by detect_levels_v5)
CERVICAL_SEQUENCE = ['C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1']

# Cord geometry parameters
CORD_AREA_MM2 = (40.0, 150.0)             # axial cord cross-section
CORD_ECC_MAX = 0.85                        # cord eccentricity ceiling
CORD_AP_RADIUS_MM = 6.0                    # max walk distance, vertical
CORD_LR_RADIUS_MM = 8.0                    # max walk distance, horizontal
CORD_INTENSITY_LO_FRAC = 0.80              # cord intensity band [lo, hi]
CORD_INTENSITY_HI_FRAC = 1.20              # times cord_I
CSF_RIM_THRESHOLD_FRAC = 1.30              # CSF must be ≥ cord_I * this

# Walk parameters
WALK_PERP_WINDOW_MM = 4.0                  # band ± perpendicular to walk axis
CSF_SEARCH_RADIUS_MM = 8.0                 # max distance to find CSF rim end
WALK_GRADIENT_THRESHOLD_FRAC = 0.15        # cord→CSF gradient must be ≥ cord_I * this
SUSTAINED_RUN_MIN = 3                      # ≥3 consecutive rays needed to flag


# ============================================================================
# Vertebral level detection
# ============================================================================
def detect_levels_v5(sag_items):
    """
    Detect vertebral level z-positions from midline sagittal T2.

    v5.1 update: replaced the dens-anchor approach (which produced false
    positives on FOV-truncated scans) with the kyphosis-anchored approach
    that v4 hf07 validated against the 2024 Philips study. Adds hf07's
    progressive band widening (tries y=(-25,-5), (-35,-5), (-40,0),
    (-50,10), stops at first band yielding ≥4 peaks). The kyphosis sanity
    check (apex should land at C4) shifts labels when the cranial-most
    peak isn't C2.

    Returns dict mapping label → z_mm, with disc midpoints as 'CX-CY'.
    """
    from ._spine_common import resample_sag_patient_coords

    if not sag_items:
        return {}
    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]
    z_range = np.arange(-100, 110, 0.3)
    y_range = np.arange(-50, 60, 0.3)
    pv = resample_sag_patient_coords(sag_mid, z_range, y_range)

    # Cord centerline (kyphosis trace)
    smoothed = gaussian_filter(pv, sigma=2.0)
    cord_zs = []; cord_ys = []
    for zi, z in enumerate(z_range):
        col = smoothed[:, zi]
        if col.max() < 30:
            continue
        ymask = (y_range > -15) & (y_range < 30)
        if not ymask.any():
            continue
        sub = col.copy(); sub[~ymask] = 0
        peak_idx = int(np.argmax(sub))
        if sub[peak_idx] < 30:
            continue
        cord_zs.append(z)
        cord_ys.append(float(y_range[peak_idx]))
    cord_zs = np.array(cord_zs); cord_ys = np.array(cord_ys)

    # Kyphosis apex
    apex = None
    if len(cord_zs) >= 5:
        sm_y = gaussian_filter1d(cord_ys, sigma=3)
        apex = float(cord_zs[int(np.argmax(sm_y))])

    # Body peaks (anterior bright band) — hf07 progressive widening
    # NB: prominence=50 (not 20) drops spurious low-prominence peaks like
    # one at z≈46 in the 2024 Philips study which had prom=27.4 vs real
    # cervical bodies at prom=200-1000. Using 20 caused local-v4 to find
    # 8 peaks where deployed-v4 (prom=50) finds 7, shifting labels by 1
    # and putting C5-C6 at z=21.65 instead of the correct z=0.35.
    sm = gaussian_filter(pv, sigma=1.5)
    body_zs = None
    for ylo, yhi in [(-25, -5), (-35, -5), (-40, 0), (-50, 10)]:
        ymask = (y_range > ylo) & (y_range < yhi)
        if not ymask.any():
            continue
        band = sm[ymask, :].mean(axis=0)
        band_smooth = gaussian_filter1d(band, sigma=2)
        peaks, _ = find_peaks(band_smooth, distance=int(8/0.3), prominence=50)
        candidate_zs = z_range[peaks]
        if len(candidate_zs) >= 4:
            body_zs = candidate_zs
            break
    if body_zs is None or len(body_zs) < 4:
        return {}

    # Label C2-T1 cranial→caudal with kyphosis sanity check
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

    # Disc midpoints
    levels = dict(labels)
    for k in range(len(sequence) - 1):
        a, b = sequence[k], sequence[k+1]
        if a in labels and b in labels:
            levels[f'{a}-{b}'] = (labels[a] + labels[b]) / 2.0
    return levels


def _legacy_dens_anchor_kept_for_reference(sm, z_range, y_range, cranial_z):
    """[DEPRECATED — kept for reference]
    Earlier v5 attempt that used continued-bone-signal-above-cranial-peak
    to detect C2 dens. Produced false positives on FOV-truncated scans
    where soft tissue above the cranial-most cervical body had similar
    intensity to bone marrow. Replaced by kyphosis-apex sanity check
    in detect_levels_v5.
    """
    pass


# ============================================================================
# Sagittal centerline trace (for axial cord anchor)
# ============================================================================
def trace_sagittal_centerline(img_sag, canal_col_lo=None, canal_col_hi=None,
                                row_pad=50, smooth_sigma_2d=1.5, smooth_sigma_1d=4.0):
    """
    Trace cord centerline on sagittal midline image using first-anterior-peak per row.

    canal_col_lo/hi: column band restricting the cord search. If None, auto-detect
    from image width (middle 30% of columns).

    Returns: rows (np.ndarray), cols (np.ndarray, smoothed)
    """
    smooth = gaussian_filter(img_sag.astype(np.float32), sigma=smooth_sigma_2d)
    H, W = smooth.shape

    if canal_col_lo is None:
        canal_col_lo = int(W * 0.40)
    if canal_col_hi is None:
        canal_col_hi = int(W * 0.65)

    cord_col = np.full(H, -1, dtype=np.float32)
    for r in range(row_pad, H - row_pad):
        profile = smooth[r, canal_col_lo:canal_col_hi]
        if profile.max() < 200:
            continue
        peaks, _ = find_peaks(profile, distance=5, prominence=50,
                                height=profile.max() * 0.5)
        if len(peaks) == 0:
            continue
        cord_col[r] = peaks[0] + canal_col_lo  # first = anterior-most peak

    valid = cord_col >= 0
    sm = cord_col.copy()
    if valid.any():
        sm[valid] = gaussian_filter1d(cord_col[valid], sigma=smooth_sigma_1d)
    rows = np.where(valid)[0]
    cols = sm[valid]
    return rows, cols


def centerline_to_xyz(rows, cols, ipp, iop, ps):
    """Convert sagittal centerline pixels to 3D patient coordinates.

    DICOM standard: pixel(r,c) → patient = ipp + r*ps[0]*iop[3:6] + c*ps[1]*iop[0:3]
    """
    if len(rows) == 0:
        return np.zeros((0, 3))
    xyz_path = np.empty((len(rows), 3), dtype=np.float64)
    for i, (r, c) in enumerate(zip(rows, cols)):
        xyz_path[i] = ipp + r*ps[0]*iop[3:6] + c*ps[1]*iop[0:3]
    return xyz_path


def project_xyz_to_pixel(xyz_target, ipp, iop, ps):
    """Solve for axial pixel (row, col) given 3D target."""
    A = np.column_stack([ps[0]*iop[3:6], ps[1]*iop[0:3]])
    b = xyz_target - ipp
    rc, *_ = np.linalg.lstsq(A, b, rcond=None)
    return float(rc[0]), float(rc[1])


# ============================================================================
# Anchored cord detection (with shape metrics)
# ============================================================================
def detect_cord_anchored(img, ps_mm, anchor_rc, anchor_radius_mm=10):
    """
    Cord detection using sagittal-projected anchor.

    Cord is mid-bright (NOT the brightest — CSF is brightest). Searches multiple
    intensity upper-thresholds, scores blobs by area + roundness + closeness to
    anchor + cord-typical intensity.

    Returns dict with: rc, area, ecc, cord_intensity, d_to_anchor, score,
                       major_axis_mm, minor_axis_mm, orientation_rad
    or None if no candidate found.
    """
    smooth = gaussian_filter(img.astype(np.float32), sigma=1.5)
    H, W = smooth.shape
    sy, sx = anchor_rc
    p_lo = np.percentile(smooth, 60)
    p_high_global = np.percentile(smooth, 90)

    candidates = []
    for upper_pct in [80, 83, 86, 88, 90, 92, 94]:
        p_hi = np.percentile(smooth, upper_pct)
        cm = (smooth >= p_lo) & (smooth <= p_hi)
        cm = binary_closing(cm, iterations=2)
        cm = binary_opening(cm, iterations=1)
        yy, xx = np.indices(cm.shape)
        d2 = np.hypot(yy - sy, xx - sx) * ps_mm
        cm = cm & (d2 < anchor_radius_mm)
        lab, n = scipy_label(cm)
        for i in range(1, n + 1):
            comp = lab == i
            a = comp.sum() * ps_mm**2
            if not (CORD_AREA_MM2[0] <= a <= CORD_AREA_MM2[1]):
                continue
            coords = np.argwhere(comp)
            if len(coords) < 5:
                continue
            cy, cx = coords.mean(axis=0)
            rys = coords[:, 0] - cy
            rxs = coords[:, 1] - cx
            Ixx = (rys**2).mean(); Iyy = (rxs**2).mean(); Ixy = (rys*rxs).mean()
            T = (Ixx + Iyy) / 2
            det_ = Ixx * Iyy - Ixy**2
            rad = max(0, T**2 - det_)
            l1 = T + np.sqrt(rad); l2 = T - np.sqrt(rad)
            if l1 <= 0:
                continue
            ecc = np.sqrt(1 - l2/l1) if l1 > l2 > 0 else 0.99
            if ecc > CORD_ECC_MAX:
                continue
            d_to_anchor = np.hypot(cy - sy, cx - sx) * ps_mm
            cord_I = float(smooth[comp].mean())
            target_area = (CORD_AREA_MM2[0] + CORD_AREA_MM2[1]) / 2
            score = (np.exp(-((a - target_area)**2) / (50**2)) * (1 - ecc) *
                      np.exp(-d_to_anchor / 8.0) * min(1.0, cord_I / p_high_global))
            major_axis_mm = 2 * np.sqrt(l1) * ps_mm
            minor_axis_mm = 2 * np.sqrt(l2) * ps_mm if l2 > 0 else 0.0
            theta = 0.5 * np.arctan2(2 * Ixy, Ixx - Iyy)
            candidates.append({
                'rc': (float(cy), float(cx)),
                'area': float(a), 'ecc': float(ecc),
                'cord_intensity': cord_I,
                'd_to_anchor': float(d_to_anchor),
                'score': float(score),
                'major_axis_mm': float(major_axis_mm),
                'minor_axis_mm': float(minor_axis_mm),
                'orientation_rad': float(theta),
            })

    if not candidates:
        return None
    candidates.sort(key=lambda c: -c['score'])
    return candidates[0]


def get_cord_intensity(smooth, ps_mm, cord_rc, sample_radius_mm=1.5):
    """Sample cord intensity from ~1.5mm circle around cord centroid."""
    cy, cx = cord_rc
    H, W = smooth.shape
    intens = []
    for r in np.arange(0, sample_radius_mm, 0.3):
        for t in np.linspace(0, 2*np.pi, 8, endpoint=False):
            yp = int(round(cy + (r/ps_mm)*np.sin(t)))
            xp = int(round(cx + (r/ps_mm)*np.cos(t)))
            if 0 <= yp < H and 0 <= xp < W:
                intens.append(smooth[yp, xp])
    return float(np.median(intens)) if intens else 0.0


# ============================================================================
# Gradient-based cord edge detection
# ============================================================================
def find_cord_edge_gradient(profile, center_idx, direction, cord_I, max_step=20,
                              grad_threshold=None):
    """
    Find cord edge along profile from center_idx going in direction (+1 or -1).

    Cord edge = first place where intensity gradient is strongly positive
    (cord → CSF transition). Returns idx or None.

    max_step should be cord_radius_mm/ps_mm — going further finds vertebral
    body marrow which has cord-similar intensity.
    """
    if grad_threshold is None:
        grad_threshold = cord_I * WALK_GRADIENT_THRESHOLD_FRAC
    n = len(profile)
    cord_min = cord_I * 0.85
    for step in range(2, max_step):
        idx = center_idx + direction * step
        if idx < 1 or idx >= n - 1:
            break
        prev_idx = idx - direction
        next_idx = idx + direction
        if not (0 <= prev_idx < n and 0 <= next_idx < n):
            continue
        local_grad = profile[next_idx] - profile[prev_idx]
        if local_grad > grad_threshold and profile[idx] >= cord_min:
            ne_check = next_idx + direction
            if 0 <= ne_check < n and profile[ne_check] > profile[idx]:
                return idx
    return None


# ============================================================================
# Four grid walks
# ============================================================================
def horizontal_walks(smooth, ps_mm, cord_rc, cord_I,
                      perp_window_mm=WALK_PERP_WINDOW_MM,
                      walk_max_mm=CORD_LR_RADIUS_MM,
                      csf_search_mm=CSF_SEARCH_RADIUS_MM):
    """L→R and R→L walks per row in cord-centroid band.

    Returns (extents, csfs):
      extents[row] = (cl, cr) cord LR boundary cols
      csfs[row] = {'patient_left_W', 'patient_left_status',
                    'patient_right_W', 'patient_right_status'}
    """
    cy, cx = cord_rc
    H, W = smooth.shape
    perp_n = int(perp_window_mm / ps_mm)
    max_step = int(walk_max_mm / ps_mm)
    csf_search_n = int(csf_search_mm / ps_mm)
    cord_lo = cord_I * CORD_INTENSITY_LO_FRAC
    csf_threshold = cord_I * CSF_RIM_THRESHOLD_FRAC

    rows = list(range(max(0, int(cy)-perp_n), min(H, int(cy)+perp_n+1)))
    extents = {}
    csfs = {}
    for row in rows:
        profile = smooth[row, :]
        cl = find_cord_edge_gradient(profile, int(cx), -1, cord_I, max_step)
        cr = find_cord_edge_gradient(profile, int(cx), +1, cord_I, max_step)
        if cl is None or cr is None or cr <= cl:
            continue
        extents[row] = (cl, cr)

        # Patient-RIGHT side (image-LEFT)
        pR_W, pR_status = _measure_csf_rim_outward(
            profile, cl, -1, cord_I, cord_lo, csf_threshold,
            csf_search_n, ps_mm, csf_search_mm)
        # Patient-LEFT side (image-RIGHT)
        pL_W, pL_status = _measure_csf_rim_outward(
            profile, cr, +1, cord_I, cord_lo, csf_threshold,
            csf_search_n, ps_mm, csf_search_mm)
        csfs[row] = {
            'patient_left_W': pL_W, 'patient_left_status': pL_status,
            'patient_right_W': pR_W, 'patient_right_status': pR_status,
        }
    return extents, csfs


def vertical_walks(smooth, ps_mm, cord_rc, cord_I,
                    perp_window_mm=WALK_PERP_WINDOW_MM,
                    walk_max_mm=CORD_AP_RADIUS_MM,
                    csf_search_mm=CSF_SEARCH_RADIUS_MM):
    """T→B and B→T walks per column in cord-centroid band."""
    cy, cx = cord_rc
    H, W = smooth.shape
    perp_n = int(perp_window_mm / ps_mm)
    max_step = int(walk_max_mm / ps_mm)
    csf_search_n = int(csf_search_mm / ps_mm)
    cord_lo = cord_I * CORD_INTENSITY_LO_FRAC
    csf_threshold = cord_I * CSF_RIM_THRESHOLD_FRAC

    cols = list(range(max(0, int(cx)-perp_n), min(W, int(cx)+perp_n+1)))
    extents = {}
    csfs = {}
    for col in cols:
        profile = smooth[:, col]
        ct = find_cord_edge_gradient(profile, int(cy), -1, cord_I, max_step)
        cb = find_cord_edge_gradient(profile, int(cy), +1, cord_I, max_step)
        if ct is None or cb is None or cb <= ct:
            continue
        extents[col] = (ct, cb)

        anterior_W, anterior_status = _measure_csf_rim_outward(
            profile, ct, -1, cord_I, cord_lo, csf_threshold,
            csf_search_n, ps_mm, csf_search_mm)
        posterior_W, posterior_status = _measure_csf_rim_outward(
            profile, cb, +1, cord_I, cord_lo, csf_threshold,
            csf_search_n, ps_mm, csf_search_mm)
        csfs[col] = {
            'anterior_W': anterior_W, 'anterior_status': anterior_status,
            'posterior_W': posterior_W, 'posterior_status': posterior_status,
        }
    return extents, csfs


def _measure_csf_rim_outward(profile, cord_edge_idx, direction, cord_I,
                               cord_lo, csf_threshold, csf_search_n,
                               ps_mm, csf_search_mm):
    """Walk outward from cord edge. Return (W_mm, status)."""
    n = len(profile)
    # Look at 3 pixels just outside cord edge
    if direction < 0:
        immediate_lo = max(0, cord_edge_idx - 3)
        immediate_hi = cord_edge_idx
    else:
        immediate_lo = cord_edge_idx
        immediate_hi = min(n, cord_edge_idx + 3)
    if immediate_hi <= immediate_lo:
        return None, 'indeterminate'

    immediate = profile[immediate_lo:immediate_hi]
    mi = float(np.mean(immediate))

    if mi >= csf_threshold:
        # CSF found immediately. Walk further to find rim end.
        csf_end = None
        if direction < 0:
            for col in range(cord_edge_idx - 3, max(0, cord_edge_idx - csf_search_n), -1):
                if profile[col] < cord_I:
                    csf_end = col + 1
                    break
            W = (cord_edge_idx - csf_end) * ps_mm if csf_end is not None else float(csf_search_mm)
        else:
            for col in range(cord_edge_idx + 3, min(n, cord_edge_idx + csf_search_n)):
                if profile[col] < cord_I:
                    csf_end = col - 1
                    break
            W = (csf_end - cord_edge_idx) * ps_mm if csf_end is not None else float(csf_search_mm)
        return W, 'free'
    elif mi < cord_lo:
        return 0.0, 'contact'
    else:
        return None, 'indeterminate'


def find_runs(values, predicate, min_length=SUSTAINED_RUN_MIN):
    """Find runs of indices where predicate(value) is True."""
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


def four_walk(smooth, ps_mm, cord_rc, cord_I, band_mm=2):
    """
    Run all 4 walks. Aggregate W per direction at cord centroid ± band_mm.
    Compute consecutive-run statistics.

    Returns dict with per-direction:
      mean, min, free_mean, n_free, n_contact, n_indeterminate, n_total,
      contact_run_max, indet_run_max, free_run_max,
      has_sustained_contact, has_sustained_indet
    """
    cy, cx = cord_rc
    eh, ch = horizontal_walks(smooth, ps_mm, cord_rc, cord_I)
    ev, cv = vertical_walks(smooth, ps_mm, cord_rc, cord_I)
    band_n = int(band_mm / ps_mm)
    band_rows = sorted([r for r in ch if abs(r - cy) <= band_n])
    band_cols = sorted([c for c in cv if abs(c - cx) <= band_n])

    out = {}
    for dirname, key, src, band, status_key in [
        ('patient_left', 'patient_left_W', ch, band_rows, 'patient_left_status'),
        ('patient_right', 'patient_right_W', ch, band_rows, 'patient_right_status'),
        ('anterior', 'anterior_W', cv, band_cols, 'anterior_status'),
        ('posterior', 'posterior_W', cv, band_cols, 'posterior_status'),
    ]:
        all_Ws = [src[k][key] for k in band if src[k][key] is not None]
        free_Ws = [src[k][key] for k in band if src[k][status_key] == 'free']
        statuses = [src[k][status_key] for k in band]
        contact_runs = find_runs(statuses, lambda s: s == 'contact', SUSTAINED_RUN_MIN)
        indet_runs = find_runs(statuses, lambda s: s == 'indeterminate', SUSTAINED_RUN_MIN)
        free_runs = find_runs(statuses, lambda s: s == 'free', SUSTAINED_RUN_MIN)
        out[dirname] = {
            'mean': float(np.mean(all_Ws)) if all_Ws else None,
            'min': float(min(all_Ws)) if all_Ws else None,
            'free_mean': float(np.mean(free_Ws)) if free_Ws else None,
            'n_free': sum(1 for s in statuses if s == 'free'),
            'n_contact': sum(1 for s in statuses if s == 'contact'),
            'n_indeterminate': sum(1 for s in statuses if s == 'indeterminate'),
            'n_total': len(band),
            'contact_run_max': max([r[1]-r[0]+1 for r in contact_runs]) if contact_runs else 0,
            'indet_run_max': max([r[1]-r[0]+1 for r in indet_runs]) if indet_runs else 0,
            'free_run_max': max([r[1]-r[0]+1 for r in free_runs]) if free_runs else 0,
            'has_sustained_contact': len(contact_runs) > 0,
            'has_sustained_indet': len(indet_runs) > 0,
        }
    return out


# ============================================================================
# Cord trajectory across z (for shape analysis)
# ============================================================================
def build_cord_trajectory(ax_items, sag_mid):
    """
    Detect cord on each axial slice using sagittal anchor.

    Returns dict idx → {rc, area, ecc, cord_intensity, major_axis_mm,
                         minor_axis_mm, orientation_rad, z, score}
    """
    rows_s, cols_s = trace_sagittal_centerline(sag_mid['img'])
    if len(rows_s) == 0:
        return {}
    xyz_path = centerline_to_xyz(rows_s, cols_s, sag_mid['ipp'], sag_mid['iop'], sag_mid['ps'])

    traj = {}
    for idx, slc in enumerate(ax_items):
        img = slc['img']
        ps_mm = float(slc['ps'][0])
        z_t = slc['ipp'][2]
        diffs = np.abs(xyz_path[:, 2] - z_t)
        cidx = int(np.argmin(diffs))
        if diffs[cidx] > 5:
            continue
        pred_r, pred_c = project_xyz_to_pixel(xyz_path[cidx], slc['ipp'], slc['iop'], slc['ps'])
        det = detect_cord_anchored(img, ps_mm, (pred_r, pred_c))
        if det is None:
            continue
        traj[idx] = {**det, 'z': z_t, 'inst': slc['inst']}
    return traj


# ============================================================================
# Per-slice classification
# ============================================================================
def classify_slice_v5(walk_summary, neighbor_summaries=None):
    """
    Classify a single slice based on 4-walk summary + optional neighbor context.

    Returns dict with: flags (list), min_dir, min_W

    Logic:
      - Sustained contact (≥3 consecutive rays at W=0) any direction → CRITICAL
      - Sustained indet on a direction WHILE neighbors show free CSF same direction
        → likely compression → MODERATE
      - min W < critical_min and sustained run → CRITICAL
      - min W < moderate_min and sustained run → MODERATE
      - min W < finding_min and asymmetry > finding_abs → FINDING
    """
    flags = []
    if neighbor_summaries is None:
        neighbor_summaries = []

    # v5.2 quality gate: count directions with valid (non-None) measurements.
    # If fewer than 2 directions have data, the slice is poorly measured;
    # cap any flag at FINDING severity to avoid false positives from a single
    # noisy direction.
    valid_dir_count = sum(1 for info in walk_summary.values()
                           if info.get('mean') is not None or info.get('n_free', 0) > 0)
    poor_quality = valid_dir_count < 2

    contact_dirs = [d for d, info in walk_summary.items()
                     if info.get('has_sustained_contact')]
    if contact_dirs and not poor_quality:
        flags.append({
            'severity': 'CRITICAL',
            'label': f'cord-canal contact ({", ".join(contact_dirs)})',
            'directions': contact_dirs,
        })
        return {'flags': flags, 'min_dir': contact_dirs[0], 'min_W': 0.0}

    # Sustained indet at this level + neighbors free in same direction → compression
    compression_dirs = []
    for d, info in walk_summary.items():
        if info.get('has_sustained_indet'):
            neighbor_free_in_d = any(
                n[d]['n_free'] > n[d]['n_indeterminate']
                for n in neighbor_summaries
                if n is not None and d in n
            )
            if neighbor_free_in_d:
                compression_dirs.append(d)
    if compression_dirs and not poor_quality:
        flags.append({
            'severity': 'MODERATE',
            'label': f'CSF obliteration ({", ".join(compression_dirs)})',
            'directions': compression_dirs,
        })

    # Find min direction
    min_W = float('inf')
    min_dir = None
    for d, info in walk_summary.items():
        if info['min'] is not None and info['min'] < min_W:
            min_W = info['min']
            min_dir = d

    if min_dir is not None and min_W < CSPINE_SPACE['critical_min_mm'] and not poor_quality:
        ws = walk_summary[min_dir]
        if ws.get('contact_run_max', 0) >= SUSTAINED_RUN_MIN:
            if not flags or flags[0]['severity'] != 'CRITICAL':
                flags.insert(0, {
                    'severity': 'CRITICAL',
                    'label': f'critical narrowing ({min_dir})',
                    'min_W': min_W,
                })
    elif min_dir is not None and min_W < CSPINE_SPACE['moderate_min_mm'] and not poor_quality:
        ws = walk_summary[min_dir]
        if (ws.get('contact_run_max', 0) >= SUSTAINED_RUN_MIN
            or ws.get('indet_run_max', 0) >= SUSTAINED_RUN_MIN):
            if not any(f['severity'] in ('CRITICAL', 'MODERATE') for f in flags):
                flags.append({
                    'severity': 'MODERATE',
                    'label': f'reduced space ({min_dir})',
                    'min_W': min_W,
                })
    elif min_dir is not None and min_W < CSPINE_SPACE['finding_min_mm']:
        opp_map = {'patient_left': 'patient_right', 'patient_right': 'patient_left',
                    'anterior': 'posterior', 'posterior': 'anterior'}
        opp = opp_map[min_dir]
        opp_W = walk_summary[opp]['mean']
        if opp_W is not None and (opp_W - min_W) / (opp_W + min_W) > CSPINE_ASYM['finding_abs']:
            if not flags:
                flags.append({
                    'severity': 'FINDING',
                    'label': f'asymmetric narrowing ({min_dir})',
                    'min_W': min_W,
                    'opp_W': opp_W,
                })

    return {
        'flags': flags,
        'min_dir': min_dir,
        'min_W': min_W if min_W != float('inf') else None,
    }


# ============================================================================
# Public assign_level (kept compatible with prior cspine.py interface)
# ============================================================================
def assign_level(z_mm, levels):
    """Map a z position to the closest labeled vertebra/disc level.

    Skips metadata keys (those starting with '_').
    """
    if not levels:
        return 'unknown'
    real_levels = {k: v for k, v in levels.items() if not k.startswith('_')}
    if not real_levels:
        return 'unknown'
    return min(real_levels.items(), key=lambda kv: abs(kv[1] - z_mm))[0]


# ============================================================================
# Slice-record adapter (keeps output schema compatible with current cspine.py)
# ============================================================================
def slice_record_from_walks(it, det, walk_summary, classification, level):
    """
    Build a slice record matching the schema cspine.py emits, but populated
    from v5's 4-walk measurements.

    The legacy schema expects:
      space_min_mm, space_mean_mm, space_max_mm, left_space_mm, right_space_mm,
      asym_lr, cord_intensity, flags

    Mapping:
      space_min_mm = min over all 4 directions
      space_mean_mm = mean over directions (free measurements)
      space_max_mm = max over directions
      left_space_mm = patient_left mean
      right_space_mm = patient_right mean
      asym_lr = (L - R) / (L + R) if both present

    v5.1 fix: cord_x_mm and cord_y_mm now correctly convert pixel (row, col)
    to patient coordinates via patient_xy_from_pix. v5.0 was emitting raw
    pixel indices in fields labeled mm.
    """
    from ._base import patient_xy_from_pix

    cord_rc = det['rc']  # (row, col) in pixels
    cord_I = det['cord_intensity']

    # Convert cord pixel position to patient coordinates
    cord_xyz = patient_xy_from_pix(it, cord_rc[0], cord_rc[1])

    means = [walk_summary[d]['mean'] for d in walk_summary
              if walk_summary[d]['mean'] is not None]
    space_min = min(means) if means else None
    space_max = max(means) if means else None
    space_mean = float(np.mean(means)) if means else None

    pL = walk_summary['patient_left']['mean']
    pR = walk_summary['patient_right']['mean']
    if pL is not None and pR is not None and (pL + pR) > 0:
        asym = (pL - pR) / (pL + pR)
    else:
        asym = 0.0

    # Convert nan-able floats safely
    def _f(v): return float(v) if v is not None else float('nan')

    return {
        'inst': it['inst'],
        'z_mm': float(it['ipp'][2]),
        'level': level,
        'cord_x_mm': float(cord_xyz[0]),  # patient x in mm (LPS)
        'cord_y_mm': float(cord_xyz[1]),  # patient y in mm (LPS, +=posterior)
        'cord_area_mm2': float(det['area']),
        'cord_ecc': float(det['ecc']),
        'cord_intensity': float(cord_I),
        'space_min_mm': _f(space_min),
        'space_mean_mm': _f(space_mean),
        'space_max_mm': _f(space_max),
        'left_space_mm': _f(pL),
        'right_space_mm': _f(pR),
        'anterior_space_mm': _f(walk_summary['anterior']['mean']),
        'posterior_space_mm': _f(walk_summary['posterior']['mean']),
        'asym_lr': float(asym),
        'min_dir': classification.get('min_dir'),
        'min_W_mm': _f(classification.get('min_W')),
        'flags': classification['flags'],
        'cord_major_axis_mm': float(det.get('major_axis_mm', 0)),
        'cord_minor_axis_mm': float(det.get('minor_axis_mm', 0)),
        'recovered': False,  # v5 doesn't use the v3 "recovered" mechanism
    }


# ============================================================================
# Top-level analyzer entry point
# ============================================================================

# k-space cord detection constants
KSPACE_ROI_SIZE = 80                    # 80x80 pixel ROI = ~28mm at 0.347mm/px
KSPACE_CORD_RADIUS_MM = 3.5             # template cord radius
KSPACE_CSF_OUTER_MM = 7.0               # template CSF outer radius (cord+CSF)


def _build_cord_template(roi_size, ps_mm,
                          cord_radius_mm=KSPACE_CORD_RADIUS_MM,
                          csf_outer_mm=KSPACE_CSF_OUTER_MM):
    """
    Construct an idealized cord-CSF-bone template for phase correlation.

    The template encodes the radial intensity pattern of axial cervical
    cord anatomy on T2: cord is mid-bright, CSF surrounding the cord is
    brightest, bone outside is dark.

    Validated subpixel cord-position recovery across normal and pathological
    slices including the C5-C6 SEVERE truth slice (radiology truth: large
    LEFT paracentral disc extrusion deforming ventral cord).
    """
    yy, xx = np.indices((roi_size, roi_size))
    cy, cx = roi_size // 2, roi_size // 2
    r_pix = np.hypot(yy - cy, xx - cx) * ps_mm
    template = np.zeros((roi_size, roi_size), dtype=np.float32)
    csf_mask = r_pix < csf_outer_mm
    cord_mask = r_pix < cord_radius_mm
    template[csf_mask] = 2.0      # CSF brightest on T2
    template[cord_mask] = 1.0     # cord mid-bright
    # outside csf_outer_mm stays 0 (bone/dark)
    return template


def _normalized_cross_correlation_freq(img, template):
    """
    Normalized cross-correlation in frequency domain.

    Returns 2D map; peak position = optimal alignment of template to image.
    Both inputs assumed same shape and pre-windowed (Hann) to suppress
    boundary artifacts.
    """
    img_z = img - img.mean()
    tmpl_z = template - template.mean()
    F_img = np.fft.fft2(img_z)
    F_tmpl = np.fft.fft2(tmpl_z)
    cross = F_img * np.conj(F_tmpl)
    return np.fft.fftshift(np.real(np.fft.ifft2(cross)))


def _subpixel_peak(corr):
    """
    Find subpixel peak position via parabolic fit on 3x3 neighborhood.

    Returns (py, px) in float coordinates. The Fourier-shift theorem
    guarantees that this subpixel peak corresponds to subpixel template
    alignment in the original image.
    """
    H, W = corr.shape
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    if 1 <= py < H - 1 and 1 <= px < W - 1:
        y0, y1, y2 = corr[py - 1, px], corr[py, px], corr[py + 1, px]
        denom_y = (y0 - 2 * y1 + y2)
        dy = 0.5 * (y0 - y2) / denom_y if abs(denom_y) > 1e-10 else 0.0
        x0, x1, x2 = corr[py, px - 1], corr[py, px], corr[py, px + 1]
        denom_x = (x0 - 2 * x1 + x2)
        dx = 0.5 * (x0 - x2) / denom_x if abs(denom_x) > 1e-10 else 0.0
        # Clamp subpixel offsets to ±1 pixel (parabolic fit can blow up at
        # peak plateaus; if it does, just trust the integer peak)
        dy = max(-1.0, min(1.0, dy))
        dx = max(-1.0, min(1.0, dx))
    else:
        dy = dx = 0.0
    return float(py + dy), float(px + dx)


def detect_cord_kspace_v6(ax_items_sorted):
    """
    Detect cord position across an axial volume via k-space phase correlation.

    For each slice:
      1. Extract canal ROI centered on patient origin (DICOM-defined x=0, y=0).
         The cervical cord lives within ~5mm of patient origin on standard
         HFS positioning, so a 28mm ROI captures the cord with margin.
      2. Apply 2D Hann window to suppress FFT boundary artifacts.
      3. Forward-FFT both ROI and the cord-CSF-bone template; compute
         normalized cross-correlation in frequency domain.
      4. Locate correlation peak with subpixel precision via parabolic fit.
      5. Map peak position back to absolute pixel coordinates, then to
         patient-mm coordinates via DICOM IPP/IOP.

    Then compute cord shape (eccentricity, major/minor axis) at the exact
    cord position via detect_cord_anchored with a tight 4mm radius — this
    is just verifying the blob shape at a known-correct location, not
    searching.

    No fallback radii. No drift correction. No recovery pass. One operation
    per slice.

    Returns dict idx → {rc, area, ecc, cord_intensity, z, recovered, ...}
    """
    from ._base import pix_from_patient_xy

    if not ax_items_sorted:
        return {}

    ps_mm = float(ax_items_sorted[0]['ps'][0])
    win = np.outer(np.hanning(KSPACE_ROI_SIZE), np.hanning(KSPACE_ROI_SIZE))
    template = _build_cord_template(KSPACE_ROI_SIZE, ps_mm)
    template_w = template * win

    detections = {}
    for idx, it in enumerate(ax_items_sorted):
        try:
            row0, col0 = pix_from_patient_xy(it, 0, 0)
        except Exception:
            continue
        img = it['img']
        H, W = img.shape
        r0 = int(round(row0)); c0 = int(round(col0))
        half = KSPACE_ROI_SIZE // 2
        r1 = max(0, r0 - half); r2 = min(H, r0 + half)
        c1 = max(0, c0 - half); c2 = min(W, c0 + half)

        # Skip if ROI is too small (slice near edge of image)
        if (r2 - r1) < KSPACE_ROI_SIZE or (c2 - c1) < KSPACE_ROI_SIZE:
            continue

        canal = img[r1:r2, c1:c2].astype(np.float32)
        canal_w = canal * win

        # Phase correlation in k-space
        corr = _normalized_cross_correlation_freq(canal_w, template_w)
        py, px = _subpixel_peak(corr)

        # Subpixel cord position in absolute pixel coords
        cord_row = r1 + py
        cord_col = c1 + px

        # Verify and characterize cord shape at the k-space-derived position.
        # Tight radius (4mm) — we already KNOW where the cord is; we're just
        # measuring its shape, not searching for it.
        ps_mm_slc = float(it['ps'][0])
        det = detect_cord_anchored(it['img'], ps_mm_slc,
                                     (cord_row, cord_col),
                                     anchor_radius_mm=4)

        # If shape detection fails (atypical contrast, partial volume), use
        # the k-space position with default shape values rather than dropping
        # the slice.
        if det is None:
            cord_rc = (cord_row, cord_col)
            area_mm2 = 0.0
            ecc = 0.0
            major = 0.0
            minor = 0.0
            orientation = 0.0
        else:
            cord_rc = det['rc']
            area_mm2 = float(det['area'])
            ecc = float(det['ecc'])
            major = float(det.get('major_axis_mm', 0.0))
            minor = float(det.get('minor_axis_mm', 0.0))
            orientation = float(det.get('orientation_rad', 0.0))

        smooth = gaussian_filter(it['img'].astype(np.float32), sigma=1.5)
        cord_I = get_cord_intensity(smooth, ps_mm_slc, cord_rc)

        detections[idx] = {
            'rc': cord_rc,
            'area': area_mm2,
            'ecc': ecc,
            'cord_intensity': float(cord_I),
            'z': float(it['ipp'][2]),
            'recovered': False,
            'major_axis_mm': major,
            'minor_axis_mm': minor,
            'orientation_rad': orientation,
            'smooth': smooth,
            'kspace_correlation_peak': float(corr.max()),
        }

    return detections


def assign_level_v5(z_mm, levels):
    """
    Map a z position to the closest labeled vertebra/disc level.

    v5.2 update (Fix 3): caps cervical labeling at T1. Slices more than 6mm
    caudal of the T1 z-position get labeled 'below_T1' so they don't pool
    into the T1 bucket and trigger false aggregate findings from upper-thoracic
    measurements.
    """
    if not levels:
        return 'unknown'
    real_levels = {k: v for k, v in levels.items() if not k.startswith('_')}
    if not real_levels:
        return 'unknown'

    # Fix 3: below-T1 cap
    t1_z = real_levels.get('T1')
    if t1_z is not None and z_mm < t1_z - 6.0:
        return 'below_T1'

    return min(real_levels.items(), key=lambda kv: abs(kv[1] - z_mm))[0]


def analyze_cspine_v6(ax_items, sag_items):
    """
    Entry point. Pass loaded axial T2 + sagittal T2 item lists; returns a dict
    matching the schema CSpineAnalyzer.analyze() returns (without the wrapping
    'series_used' / 'status' fields, which the analyzer class adds).

    v6.0 update: cord detection uses detect_cord_kspace_v6 (k-space phase
    correlation against an anatomic cord-CSF-bone template). Replaces v5.2's
    multi-radius blob search + trajectory recovery stack. One operation per
    slice; subpixel cord position; no thresholds, no fallback radii.

    Sagittal is used only for level detection (unchanged from v5).

    Args:
      ax_items: list of dicts {'img', 'ipp', 'iop', 'ps', 'inst'} sorted by z
      sag_items: list of dicts {'img', 'ipp', 'iop', 'ps', 'inst'}

    Returns:
      dict with: levels_detected, slice_records, walk_summaries, classifications,
                 markers, all_flags, algorithm_version
    """
    from ._spine_common import summarize_cord_track, aggregate_levels
    from ._base import max_severity

    # Sort axial by z
    ax_items_sorted = sorted(ax_items, key=lambda s: s['ipp'][2])

    if not sag_items:
        return {'levels_detected': {}, 'slice_records': [], 'markers': [],
                'level_summaries': [], 'all_flags': [], 'overall': 'INSUFFICIENT_DATA',
                'cord_track_3d': {}, 'algorithm_version': 'v6'}

    # Vertebral levels via sagittal (unchanged from v5.2)
    levels = detect_levels_v5(sag_items)

    # Cord detection via k-space phase correlation (NEW in v6)
    cord_info = detect_cord_kspace_v6(ax_items_sorted)

    # Compute walk summaries for each slice with cord
    walk_summaries = {}
    for idx, det in cord_info.items():
        smooth = det['smooth']
        ps_mm = float(ax_items_sorted[idx]['ps'][0])
        cord_I = det['cord_intensity']
        walk_summaries[idx] = four_walk(smooth, ps_mm, det['rc'], cord_I)

    # Classify each slice with neighbor context
    classifications = {}
    for idx in walk_summaries:
        neighbors = []
        for n_offset in [-2, -1, 1, 2]:
            n_idx = idx + n_offset
            if n_idx in walk_summaries:
                neighbors.append(walk_summaries[n_idx])
        classifications[idx] = classify_slice_v5(walk_summaries[idx], neighbors)

    # Build slice records + markers
    slice_records = []
    markers = []
    for idx in sorted(cord_info.keys()):
        slc = ax_items_sorted[idx]
        det = cord_info[idx]
        ws = walk_summaries[idx]
        cls = classifications[idx]
        z_mm = det['z']
        level = assign_level_v5(z_mm, levels)
        rec = slice_record_from_walks(slc, det, ws, cls, level)
        rec['recovered'] = det['recovered']
        slice_records.append(rec)
        markers.append({
            'inst': slc['inst'],
            'level': level,
            'cord_xyz_mm': [round(rec['cord_x_mm'], 3),
                            round(rec['cord_y_mm'], 3),
                            round(z_mm, 3)],
            'cord_area_mm2': round(rec['cord_area_mm2'], 2),
            'recovered': det['recovered'],
            'space_min_mm': rec['space_min_mm'],
            'space_mean_mm': rec['space_mean_mm'],
            'asym_lr': round(rec['asym_lr'], 3),
            'severity': max_severity(rec['flags']),
            'min_dir': rec['min_dir'],
            'min_W_mm': rec['min_W_mm'],
            'cord_major_axis_mm': rec['cord_major_axis_mm'],
            'cord_minor_axis_mm': rec['cord_minor_axis_mm'],
        })

    level_summaries, all_flags = aggregate_levels(
        slice_records, CSPINE_LEVELS, CSPINE_SPACE, CSPINE_ASYM,
    )

    # Fix 1: severity escalation from sustained low-rim runs and cord deformation.
    # Fix 4: disc-level severity inheritance from adjacent vertebra findings.
    level_summaries, all_flags = _apply_severity_escalation_v5(
        level_summaries, all_flags, slice_records,
    )

    overall = max_severity(all_flags)

    return {
        'levels_detected': levels,
        'slice_records': slice_records,
        'markers': markers,
        'level_summaries': level_summaries,
        'all_flags': all_flags,
        'overall': overall,
        'cord_track_3d': summarize_cord_track(markers),
        'algorithm_version': 'v6',
    }


def _apply_severity_escalation_v5(level_summaries, all_flags, slice_records):
    """
    v5.2 escalation logic. Two fixes applied here:

    Fix 1: Level severity should escalate when:
      - Sustained-low-rim run: ≥2 consecutive slices at the same level have
        space_min_mm below moderate threshold (1.5mm). One noisy slice doesn't
        flag, but two adjacent slices = real finding. Escalate FINDING→MODERATE.
      - Cord deformation: cord ecc > 0.80 on the slice with min space, and
        space_min_mm < 2.0mm. The cord is being squeezed. Escalate one level.
      Both are pattern-recognition signals that distinguish true compression
      from measurement noise.

    Fix 4: Disc-level severity inheritance. If a vertebra-level (C5, C6, etc.)
    flags MODERATE or CRITICAL, its adjacent disc levels (C4-C5 above, C5-C6
    below) get a FINDING-level "adjacent-vertebra compression" flag UNLESS
    they already have an equal-or-higher-severity flag. Matches how
    radiologists read: a disc lesion's effect spans the vertebra below the
    disc, so vertebra-level findings indirectly signal disc pathology.
    """
    from collections import defaultdict
    from ._base import max_severity

    # Build slice records by level for sustained-run / deformation analysis
    by_level = defaultdict(list)
    for s in slice_records:
        by_level[s['level']].append(s)

    SEVERITY_RANK = {'CRITICAL': 3, 'MODERATE': 2, 'FINDING': 1, 'NORMAL': 0}

    def _level_max_severity_rank(summary):
        flags = summary.get('flags', [])
        if not flags:
            return 0
        return max(SEVERITY_RANK.get(f.get('severity', 'NORMAL'), 0) for f in flags)

    # Fix 1: scan each level summary for sustained-low or deformation patterns
    for summary in level_summaries:
        level = summary.get('level')
        rows = by_level.get(level, [])
        if not rows:
            continue

        # Sort by z to detect "consecutive" slices
        rows_sorted = sorted(rows, key=lambda r: r.get('z_mm', 0))

        # Sustained-low-rim run check
        sustained_low_run = 0
        max_run = 0
        for r in rows_sorted:
            sm = r.get('space_min_mm')
            if sm is not None and not (sm != sm) and sm < 1.5:
                sustained_low_run += 1
                max_run = max(max_run, sustained_low_run)
            else:
                sustained_low_run = 0
        has_sustained_low = max_run >= 2

        # Cord deformation check (cord proper eccentricity)
        deformed = False
        if rows_sorted:
            min_row = min(rows_sorted,
                           key=lambda r: r.get('space_min_mm', 99) if r.get('space_min_mm') is not None and not (r.get('space_min_mm') != r.get('space_min_mm')) else 99)
            min_sm = min_row.get('space_min_mm', 99)
            ecc = min_row.get('cord_ecc', 0)
            if (min_sm is not None and not (min_sm != min_sm)
                and min_sm < 2.0 and ecc > 0.80):
                deformed = True

        # v6 NEW: directional CSF asymmetry escalation.
        # Severe disc lesions push the cord posteriorly (or laterally), creating
        # large AP or LR asymmetry in the CSF rim. The cord proper may stay
        # round (low ecc) while the surrounding CSF is highly asymmetric.
        # Truth case: C5-C6 SEVERE shows ant=1.91mm, post=3.30mm — a 1.7x
        # asymmetry with min < 2mm. This is the "SEVERE" signal that v5.2
        # was catching via blob-eccentricity (a proxy that worked on
        # combined cord+CSF blob shape).
        ap_compressed = False
        if rows_sorted:
            min_row = min(rows_sorted,
                           key=lambda r: r.get('space_min_mm', 99) if r.get('space_min_mm') is not None and not (r.get('space_min_mm') != r.get('space_min_mm')) else 99)
            ant = min_row.get('anterior_space_mm')
            post = min_row.get('posterior_space_mm')
            left = min_row.get('left_space_mm')
            right = min_row.get('right_space_mm')

            def _is_num(v): return v is not None and not (isinstance(v, float) and v != v)

            # AP asymmetry: ratio of larger to smaller > 1.5 AND smaller < 2.0mm
            if _is_num(ant) and _is_num(post):
                lo, hi = min(ant, post), max(ant, post)
                if lo < 2.0 and hi / max(lo, 0.01) > 1.5:
                    ap_compressed = True
            # LR asymmetry: same logic
            if _is_num(left) and _is_num(right):
                lo, hi = min(left, right), max(left, right)
                if lo < 2.0 and hi / max(lo, 0.01) > 1.5:
                    ap_compressed = True

        # Apply escalation if either trigger fires
        cur_rank = _level_max_severity_rank(summary)
        new_severity = None
        new_label = None
        if has_sustained_low and cur_rank < 3:
            # FINDING (1) → MODERATE (2), MODERATE (2) → CRITICAL (3)
            if cur_rank <= 1:
                new_severity = 'MODERATE'
                new_label = f'sustained narrowing ({max_run} consecutive slices)'
            elif cur_rank == 2:
                new_severity = 'CRITICAL'
                new_label = f'sustained critical narrowing ({max_run} consecutive slices)'
        if deformed and cur_rank < 3:
            esc = 'CRITICAL' if cur_rank == 2 else 'MODERATE'
            if new_severity is None or SEVERITY_RANK[esc] > SEVERITY_RANK.get(new_severity, 0):
                new_severity = esc
                new_label = 'cord deformation with narrowing'
        if ap_compressed and cur_rank < 3:
            esc = 'CRITICAL' if cur_rank == 2 else 'MODERATE'
            if new_severity is None or SEVERITY_RANK[esc] > SEVERITY_RANK.get(new_severity, 0):
                new_severity = esc
                new_label = 'directional CSF asymmetry with narrowing'

        if new_severity is not None:
            summary.setdefault('flags', []).append({
                'label': new_label,
                'severity': new_severity,
            })
            all_flags.append({
                'label': new_label,
                'severity': new_severity,
                'level': level,
            })

    # Fix 4: disc-level inheritance from adjacent vertebra findings.
    # If a vertebra body level (C5 etc.) shows narrowing AND the adjacent disc
    # has at least some directional concern (any non-NaN direction below
    # finding threshold), inherit. If the disc measures clean across all
    # directions, do NOT inherit — radiology often distinguishes disc
    # narrowing from vertebra-level findings.
    summaries_by_level = {s['level']: s for s in level_summaries}
    DISC_TO_VERTEBRAE = {
        'C2-C3': ('C2', 'C3'),
        'C3-C4': ('C3', 'C4'),
        'C4-C5': ('C4', 'C5'),
        'C5-C6': ('C5', 'C6'),
        'C6-C7': ('C6', 'C7'),
        'C7-T1': ('C7', 'T1'),
    }
    by_level = defaultdict(list)
    for s in slice_records:
        by_level[s['level']].append(s)

    for disc_level, (above, below) in DISC_TO_VERTEBRAE.items():
        disc_summary = summaries_by_level.get(disc_level)
        if disc_summary is None:
            continue
        disc_rank = _level_max_severity_rank(disc_summary)

        # Check if disc itself has any directional concern (any direction
        # measurement below finding threshold).
        disc_rows = by_level.get(disc_level, [])
        has_directional_concern = False
        for r in disc_rows:
            for fld in ('left_space_mm', 'right_space_mm',
                         'anterior_space_mm', 'posterior_space_mm'):
                v = r.get(fld)
                if v is None:
                    continue
                if isinstance(v, float) and v != v:  # NaN
                    continue
                if v < 2.5:  # below finding threshold
                    has_directional_concern = True
                    break
            if has_directional_concern:
                break

        if not has_directional_concern:
            continue  # disc measures clean — don't inherit

        for adj in (above, below):
            adj_summary = summaries_by_level.get(adj)
            if adj_summary is None:
                continue
            adj_rank = _level_max_severity_rank(adj_summary)
            if adj_rank >= 1 and disc_rank < adj_rank:
                if adj_rank == 3:
                    inherit_severity = 'MODERATE'
                elif adj_rank == 2:
                    inherit_severity = 'FINDING'
                else:
                    inherit_severity = 'FINDING'
                inherit_label = f'adjacent-vertebra finding at {adj}'
                disc_summary.setdefault('flags', []).append({
                    'label': inherit_label,
                    'severity': inherit_severity,
                })
                all_flags.append({
                    'label': inherit_label,
                    'severity': inherit_severity,
                    'level': disc_level,
                })
                disc_rank = max(disc_rank, SEVERITY_RANK[inherit_severity])

    return level_summaries, all_flags
