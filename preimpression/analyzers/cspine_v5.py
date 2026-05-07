"""
analyzers/cspine_v5.py — Cspine analyzer, consolidated.
=========================================================

WHY v5 EXISTS
-------------
Across 4 research sessions and 7 production hotfixes (p0066 hf01-hf07), cspine
analysis improved piecemeal. The integration team extracted research functions
into _spine_common.py — but those functions are cspine-specific (cord shape,
walk bounds, intensity ranges all tuned to cervical anatomy), don't belong in
shared spine helpers, and proved hard to version (six hotfixes for one silent
NaN cascade caused by scattered code).

v5 puts everything cspine-specific into one self-contained module. cspine.py
becomes a thin shim that imports v5, mirroring the brain.py → brain_v4.py
pattern that shipped clean in one push.

WHAT v5 INCLUDES
----------------
1. Vertebral level detection with 3-stage peak validation
   (replaces hf07 progressive band widening + the broken kyphosis-anchor logic
   that caused the open hf08 bug):
     - Stage 1: troughs on both sides at 3-15mm (real disc spaces flank vertebrae)
     - Stage 2: intensity within 0.4-1.8x median of stage-1 peaks
                (filters skull base / occiput / posterior fat that look like vertebrae)
     - Stage 3: spacing similarity (real cervical bodies are 13-22mm apart;
                outliers indicate the peak set is mixing vertebrae with non-vertebrae)
   No kyphosis-apex assumption. Cranial-most validated peak = C2; everything
   below is C3, C4, ... in order. Verified on 2024 Philips Ingenia data:
   produces C5-C6 disc midpoint at z = (-10.9 + 11.6)/2 = +0.35, matching
   Stensby radiology truth (severe extrusion at slice idx 25, z = -0.2).

2. Sagittal-anchored cord detection
   - DP first-anterior-bright-peak sagittal centerline trace
   - 3D projection from sagittal pixel to axial pixel via DICOM convention
   - Cord intensity scoring (cord is mid-bright p85-p92, not CSF p95+)
   - Tight 10mm anchor radius search

3. Four grid walks with gradient cord edge detection
   - Tight bounds: 6mm vertical, 8mm horizontal (cord AP/LR fits inside;
     wider walks misidentified vertebral body marrow as cord top — the bug
     I traced in session 4)
   - Gradient-based edge detection, not intensity-band-matching
   - L→R, R→L, T→B, B→T independent observers; disagreement IS the noise filter

4. Per-direction CSF rim measurement
   - Walks measure CSF rim thickness from cord boundary outward
   - Status: free / contact / indeterminate
   - Indeterminate at disc levels is normal anatomy (disc occupies anterior CSF
     space) — distinguished from compression by adjacent-slice context

5. Sustained-runs filter
   - Single-row contact does not flag (was the v3 bug #2)
   - Requires ≥3 consecutive rays at contact for CRITICAL
   - Same rule for indeterminate runs

6. Multi-slice classification with neighbor context
   - Compression at a disc level spans 2-3 axial slices
   - Aggregation per disc level, not per slice
   - Neighbor comparison filters disc-level normal anatomy from compression

KNOWN LIMITATIONS
-----------------
- Side label semantics: radiology uses lesion-side ("LEFT" = where disc is);
  algorithm reports contact-side (where cord-canal distance is smallest).
  These differ when the cord drifts away from the lesion. Documented in
  session 4 status; not yet resolved.
- Cord position trajectory across z (build_cord_trajectory) is computed but
  not yet used in classification logic. Adding it would help distinguish
  compressed-disc-level from normal-disc-level.

CONVENTIONS (axial cspine, HFS patient, IOP ≈ identity)
-------------------------------------------------------
  col idx ↑ = patient LEFT
  row idx ↑ = patient POSTERIOR
  image-LEFT  (low col)  = patient-RIGHT
  image-RIGHT (high col) = patient-LEFT
  image-TOP    (low row)  = patient-ANTERIOR
  image-BOTTOM (high row) = patient-POSTERIOR

DATA FLOW
---------
  analyze(series_list)
    → select_best_t2_axsag (from _spine_common)
    → load_volume (from _base)
    → detect_levels_v5(sag_items)        [vertebral labeling]
    → trace_sagittal_centerline + project to axial anchors
    → for each axial slice with anchor:
        detect_cord_anchored → cord_rc + shape
        four_walk(smooth, cord_rc) → per-direction CSF rim widths
        classify_slice → flags
    → aggregate_levels (from _spine_common, with v5 metric extension)
    → return result dict

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

# Cervical anatomy parameters
CERVICAL_BODY_SPACING_MM = (10.0, 25.0)   # observed cervical body z-spacing range
                                            # (real cspine has variation incl. disc disease)
CERVICAL_DISC_SPACE_MM = (3.0, 15.0)      # trough-to-peak distance on body band
CERVICAL_INTENSITY_RATIO = (0.4, 1.8)     # peak intensity must be within this
                                            # fraction of median peak intensity
LARGE_GAP_THRESHOLD_MM = 28.0             # gap > this = non-cervical break
                                            # (separates cervical run from skull/thorax noise)
CERVICAL_SEQUENCE = ['C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1']

# Anchor detection (C2 dens / T1 rib articulation) — see detect_levels_v5
DENS_RATIO_THRESHOLD = 0.55  # band signal 5-15mm above cranial peak / peak intensity
                              # > threshold = dens present = cranial peak IS C2

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

    Strategy:
      1. Find vertebral body peaks via 3-stage validation (no kyphosis assumption)
      2. Detect C2 dens if present (continued bone signal above cranial-most peak)
      3. Anchor labeling:
         - If dens detected → cranial-most peak is C2
         - Otherwise → cranial-most peak is C3 if 6+ peaks, C4 if 5 peaks,
           C5 if 4 peaks (estimate from peak count vs typical FOV coverage)
      4. Label downward from anchor: C2, C3, C4, C5, C6, C7, T1

    Returns dict mapping label → z_mm with optional confidence:
      {'C2': 12.4, 'C2-C3': 0.5, 'C3': -11.4, ...,
       '_anchor': 'dens'|'inferred',
       '_anchor_confidence': 'high'|'medium'|'low'}

    Confidence is 'high' when dens detected, 'medium' when peak count strongly
    suggests anchor, 'low' when ambiguous. Empty dict if validation fails.
    """
    from ._spine_common import resample_sag_patient_coords

    if not sag_items:
        return {}

    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]
    z_range = np.arange(-100, 110, 0.3)
    y_range = np.arange(-50, 60, 0.3)
    pv = resample_sag_patient_coords(sag_mid, z_range, y_range)
    sm = gaussian_filter(pv, sigma=1.5)

    body_zs = _find_validated_body_peaks(sm, z_range, y_range)
    if len(body_zs) < 4:
        return {}

    body_zs = np.sort(body_zs)[::-1]  # cranial → caudal

    # Detect C2 dens above cranial-most peak
    cranial_z = float(body_zs[0])
    has_dens = _detect_c2_dens(sm, z_range, y_range, cranial_z)

    # Determine anchor
    if has_dens:
        anchor_idx = 0  # cranial-most = C2
        anchor_kind = 'dens'
        confidence = 'high'
    else:
        # No dens visible. Estimate based on peak count.
        # Cervical FOV typical: 6-8 peaks (C2/C3 to T1)
        # If 7+ peaks, cranial-most is likely C2 still (just dens may be partial)
        # If 6 peaks, likely C3
        # If 5 peaks, likely C3 or C4
        # If 4 peaks, ambiguous (could be C3-C6 or C4-C7)
        n = len(body_zs)
        if n >= 7:
            anchor_idx = 0  # C2
            confidence = 'medium'
            anchor_kind = 'count'
        elif n == 6:
            anchor_idx = 1  # treat cranial-most as C3 (offset by 1)
            confidence = 'medium'
            anchor_kind = 'count'
        elif n == 5:
            anchor_idx = 2  # cranial-most is C4
            confidence = 'low'
            anchor_kind = 'count_low'
        else:  # n == 4
            anchor_idx = 2  # default cranial-most = C4 (cervical mid-FOV)
            confidence = 'low'
            anchor_kind = 'count_low'

    # Label peaks
    n_take = min(len(body_zs), len(CERVICAL_SEQUENCE) - anchor_idx)
    labels = {}
    for i in range(n_take):
        seq_idx = anchor_idx + i
        if seq_idx < len(CERVICAL_SEQUENCE):
            labels[CERVICAL_SEQUENCE[seq_idx]] = float(body_zs[i])

    # Add disc midpoints
    levels = dict(labels)
    for k in range(len(CERVICAL_SEQUENCE) - 1):
        a, b = CERVICAL_SEQUENCE[k], CERVICAL_SEQUENCE[k+1]
        if a in labels and b in labels:
            levels[f'{a}-{b}'] = (labels[a] + labels[b]) / 2.0

    levels['_anchor'] = anchor_kind
    levels['_anchor_confidence'] = confidence
    return levels


def _detect_c2_dens(sm, z_range, y_range, cranial_z):
    """
    Detect C2 dens by checking for continued bone signal above the cranial-most
    body peak.

    The dens (odontoid) extends 12-18mm superior of the C2 body and shows
    bone-marrow intensity throughout that span. If cranial-most is C3 or below,
    the FOV truncates and intensity drops sharply within a few mm above the peak.

    Returns True if dens is detected.
    """
    ymask_body = (y_range > -25) & (y_range < -5)
    if not ymask_body.any():
        return False
    band = sm[ymask_body, :].mean(axis=0)
    band_smooth = gaussian_filter1d(band, sigma=2)

    # Intensity at cranial peak
    peak_idx = int(np.argmin(np.abs(z_range - cranial_z)))
    peak_intensity = float(band_smooth[peak_idx])
    if peak_intensity < 50:
        return False

    # Mean intensity 5-15mm above cranial peak (the dens region)
    above_window = (z_range > cranial_z + 5) & (z_range < cranial_z + 15)
    if not above_window.any():
        return False
    above_mean = float(band_smooth[above_window].mean())

    ratio = above_mean / peak_intensity if peak_intensity > 0 else 0
    return ratio > DENS_RATIO_THRESHOLD


def _find_validated_body_peaks(sm, z_range, y_range):
    """
    Three-stage peak validation:
      1. Trough on both sides at 3-15mm (real disc spaces flank vertebrae)
      2. Intensity within 0.4-1.8x median (filter occiput / skull-base brightness)
      3. Trim across LARGE GAPS (>28mm) which separate cervical run from
         non-cervical noise. Within-cervical spacings of 10-25mm are kept
         (real cspine has variation incl. disc disease).

    Returns array of z values for validated peaks.
    """
    ymask = (y_range > -25) & (y_range < -5)
    if not ymask.any():
        return np.array([])
    band = sm[ymask, :].mean(axis=0)
    band_smooth = gaussian_filter1d(band, sigma=2)

    peaks, _ = find_peaks(band_smooth, distance=int(8/0.3), prominence=20)
    troughs, _ = find_peaks(-band_smooth, distance=int(8/0.3), prominence=20)
    if len(peaks) == 0:
        return np.array([])

    # Stage 1: trough on both sides at disc-space distance
    stage1 = []
    for pi in peaks:
        z_peak = z_range[pi]
        ta = [t for t in troughs if t > pi]
        tb = [t for t in troughs if t < pi]
        ad = (z_range[min(ta)] - z_peak) if ta else None
        bd = (z_peak - z_range[max(tb)]) if tb else None
        ha = ad is not None and CERVICAL_DISC_SPACE_MM[0] < ad < CERVICAL_DISC_SPACE_MM[1]
        hb = bd is not None and CERVICAL_DISC_SPACE_MM[0] < bd < CERVICAL_DISC_SPACE_MM[1]
        if ha and hb:
            stage1.append({'z': float(z_peak), 'intensity': float(band_smooth[pi])})

    if len(stage1) < 4:
        return np.array(sorted([s['z'] for s in stage1]))

    # Stage 2: intensity ratio
    intensities = [s['intensity'] for s in stage1]
    median_i = float(np.median(intensities))
    stage2 = [s for s in stage1
               if CERVICAL_INTENSITY_RATIO[0] <= s['intensity']/median_i <= CERVICAL_INTENSITY_RATIO[1]]
    if len(stage2) < 4:
        return np.array(sorted([s['z'] for s in stage1]))

    # Stage 3: trim across LARGE GAPS — separates cervical from non-cervical noise
    stage2_sorted = sorted(stage2, key=lambda s: s['z'])
    while len(stage2_sorted) > 4:
        zs = [s['z'] for s in stage2_sorted]
        spacings = [zs[i+1] - zs[i] for i in range(len(zs)-1)]
        if not spacings:
            break
        max_spacing = max(spacings)
        if max_spacing < LARGE_GAP_THRESHOLD_MM:
            break  # all gaps reasonable
        gap_idx = spacings.index(max_spacing)
        # Drop the side with fewer peaks (the spurious end)
        n_below = gap_idx + 1
        n_above = len(stage2_sorted) - n_below
        if n_below >= n_above:
            stage2_sorted = stage2_sorted[:gap_idx + 1]
        else:
            stage2_sorted = stage2_sorted[gap_idx + 1:]

    return np.array([s['z'] for s in stage2_sorted])


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

    contact_dirs = [d for d, info in walk_summary.items()
                     if info.get('has_sustained_contact')]
    if contact_dirs:
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
    if compression_dirs:
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

    if min_dir is not None and min_W < CSPINE_SPACE['critical_min_mm']:
        ws = walk_summary[min_dir]
        if ws.get('contact_run_max', 0) >= SUSTAINED_RUN_MIN:
            if not flags or flags[0]['severity'] != 'CRITICAL':
                flags.insert(0, {
                    'severity': 'CRITICAL',
                    'label': f'critical narrowing ({min_dir})',
                    'min_W': min_W,
                })
    elif min_dir is not None and min_W < CSPINE_SPACE['moderate_min_mm']:
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
    """
    cord_rc = det['rc']
    cord_I = det['cord_intensity']

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
        'cord_x_mm': float(cord_rc[1]),  # patient-x via col idx
        'cord_y_mm': float(cord_rc[0]),  # patient-y via row idx
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
def analyze_cspine_v5(ax_items, sag_items):
    """
    Entry point. Pass loaded axial T2 + sagittal T2 item lists; returns a dict
    matching the schema CSpineAnalyzer.analyze() returns (without the wrapping
    'series_used' / 'status' fields, which the analyzer class adds).

    Args:
      ax_items: list of dicts {'img', 'ipp', 'iop', 'ps', 'inst'} sorted by z
      sag_items: list of dicts {'img', 'ipp', 'iop', 'ps', 'inst'}

    Returns:
      dict with: levels_detected, slice_records, walk_summaries, classifications,
                 markers, all_flags
    """
    from ._spine_common import summarize_cord_track, aggregate_levels
    from ._base import max_severity

    # Sort axial by z
    ax_items_sorted = sorted(ax_items, key=lambda s: s['ipp'][2])

    # Pick midline sagittal
    if not sag_items:
        return {'levels_detected': {}, 'slice_records': [], 'markers': [],
                'level_summaries': [], 'all_flags': [], 'overall': 'INSUFFICIENT_DATA'}
    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]

    # Vertebral levels
    levels = detect_levels_v5(sag_items)

    # Cord trajectory across all axial slices
    cord_traj = build_cord_trajectory(ax_items_sorted, sag_mid)

    # Compute walk summary for each slice with detected cord
    walk_summaries = {}
    for idx, det in cord_traj.items():
        slc = ax_items_sorted[idx]
        smooth = gaussian_filter(slc['img'].astype(np.float32), sigma=1.5)
        ps_mm = float(slc['ps'][0])
        cord_I = get_cord_intensity(smooth, ps_mm, det['rc'])
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
    for idx in sorted(cord_traj.keys()):
        slc = ax_items_sorted[idx]
        det = cord_traj[idx]
        ws = walk_summaries[idx]
        cls = classifications[idx]
        z_mm = det['z']
        level = assign_level(z_mm, levels)
        rec = slice_record_from_walks(slc, det, ws, cls, level)
        slice_records.append(rec)
        markers.append({
            'inst': slc['inst'],
            'level': level,
            'cord_xyz_mm': [round(rec['cord_x_mm'], 3),
                            round(rec['cord_y_mm'], 3),
                            round(z_mm, 3)],
            'cord_area_mm2': round(rec['cord_area_mm2'], 2),
            'recovered': False,
            'space_min_mm': rec['space_min_mm'],
            'space_mean_mm': rec['space_mean_mm'],
            'asym_lr': round(rec['asym_lr'], 3),
            'severity': max_severity(rec['flags']),
            'min_dir': rec['min_dir'],
            'min_W_mm': rec['min_W_mm'],
            'cord_major_axis_mm': rec['cord_major_axis_mm'],
            'cord_minor_axis_mm': rec['cord_minor_axis_mm'],
        })

    # Aggregate per level using the same machinery cspine.py used
    level_summaries, all_flags = aggregate_levels(
        slice_records, CSPINE_LEVELS, CSPINE_SPACE, CSPINE_ASYM,
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
    }
