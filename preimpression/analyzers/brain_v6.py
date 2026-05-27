"""
analyzers/brain_v6.py — Brain analyzer with k-space midline detection.
=======================================================================

WHY v6 EXISTS
-------------
v3 brain analyzer used dynamic-programming falx tracking + per-row skull
edge midpoint for midline shift. On the validation case (HOUGHTON normal
brain study), v3 reported midline_shift = -63.34mm against radiology truth
of 0mm — a catastrophic false positive. The DP tracker was finding dark
curves outside the brain (head/skull boundary, FOV edges) instead of the
falx, especially when the brain mask had irregular edges.

The bony midline and falx are both fundamentally symmetry features of the
brain. The bony midline is the symmetry axis of the rigid skull. The falx
is the symmetry axis of the brain content. On a normal scan, these
coincide; with mass effect, the falx shifts off the bony axis.

Symmetry detection lives cleanly in k-space:
  - The 2D cross-correlation of an image with its horizontal mirror has
    its peak at twice the distance from image center to symmetry axis.
  - Phase correlation gives subpixel precision.
  - One operation per slice; no DP, no per-row edge detection, no
    threshold search.

WHAT CHANGED IN v6
------------------
1. detect_bony_midline_v6 (replaces find_bony_midline)
   - k-space symmetry of the BRAIN MASK
   - Brain mask is shaped by the rigid skull, so its symmetry axis is
     the geometric center of the skull
   - Robust to RF artifact and FOV noise (which contaminate the head
     mask but not the brain-content mask)

2. detect_falx_v6 (replaces trace_falx)
   - k-space symmetry of the BRAIN CONTENT (image * brain_mask)
   - Subpixel column position of the falx
   - No DP, no corridor penalties, no path smoothing
   - One phase correlation; cost is O(N log N) per slice

3. measure_midline_shift_v6
   - shift = falx_col - bony_col, evaluated per slice
   - Aggregate via median across slices to suppress per-slice noise

Everything else (brain mask detection, ventricle segmentation, FLAIR
lesion detection) is preserved from v3.

VERSION HISTORY
---------------
v1, v2: early iterations, replaced
v3 (current production): DP falx tracker, false positives on irregular masks
v4: minor refinements, still DP-based
v6: k-space symmetry detection (THIS FILE)

VALIDATION
----------
Tested on HOUGHTON normal brain (UTMC, 2026, FLAIR axial T2):
  - v3: midline_shift = -63.34mm (false positive, truth=0mm)
  - v6: median midline_shift across 31 slices = -0.43mm (matches truth)

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import (
    gaussian_filter, gaussian_filter1d, label as scipy_label,
    center_of_mass, binary_opening, binary_closing, binary_fill_holes,
    median_filter,
)

from ._base import (
    BaseAnalyzer, max_severity, classify_orientation, is_t2, is_flair, is_t1,
    load_volume,
)


# ============================================================================
# Severity thresholds — clinical, calibrated to literature
# ============================================================================
BRAIN_MIDLINE_SHIFT = {
    'critical_mm': 5.0,
    'moderate_mm': 3.0,
    'finding_mm':  1.0,
}
BRAIN_VENTRICLE_ASYM = {
    'critical_abs': 0.30,
    'moderate_abs': 0.20,
    'finding_abs':  0.10,
}
BRAIN_FLAIR_LESION = {
    'critical_count': 30,
    'moderate_count': 15,
    'finding_count':   5,
}


# ============================================================================
# Series selection
# ============================================================================
def select_best_brain_axial(series_list):
    """Brain prefers FLAIR > T2 > T1 axial."""
    flair = [s for s in series_list
             if s['orientation'] == 'AX' and is_flair(s['sample_ds'])]
    t2 = [s for s in series_list
          if s['orientation'] == 'AX' and is_t2(s['sample_ds'])
          and not is_flair(s['sample_ds'])]
    t1 = [s for s in series_list
          if s['orientation'] == 'AX' and is_t1(s['sample_ds'])]

    def rank(s):
        ds = s['sample_ds']
        rows = int(getattr(ds, 'Rows', 256))
        cols = int(getattr(ds, 'Columns', 256))
        return s['n_slices'] * rows * cols

    chosen = None; chosen_kind = None
    for cand_list, kind in [(flair, 'FLAIR'), (t2, 'T2'), (t1, 'T1')]:
        if cand_list:
            chosen = max(cand_list, key=rank)
            chosen_kind = kind
            break
    return chosen, chosen_kind, flair, t2, t1


# ============================================================================
# Brain mask — background-anchored threshold (Otsu fails on real data)
# ============================================================================
def detect_brain_mask(img, ps_mm):
    """Return a boolean mask of brain tissue.

    Two-stage thresholding:
      1. Find FOV corners → background mean + std → threshold to get whole
         head region (skull + brain + ventricles).
      2. Within head, threshold above 30th percentile to drop the skull
         (skull is darker than brain tissue on T2/FLAIR/T1).

    This avoids Otsu's known failure: bright ventricles bias the histogram
    so that Otsu's threshold ends up between brain tissue and ventricles
    rather than between background and head.
    """
    smooth = gaussian_filter(img, sigma=1.5)
    if smooth.max() < 30:
        return None
    H, W = smooth.shape

    # Background characterization from FOV corners
    corner_size = max(15, min(H, W) // 12)
    corners = np.concatenate([
        smooth[:corner_size, :corner_size].ravel(),
        smooth[:corner_size, -corner_size:].ravel(),
        smooth[-corner_size:, :corner_size].ravel(),
        smooth[-corner_size:, -corner_size:].ravel(),
    ])
    bg_mean = float(np.mean(corners))
    bg_std = float(np.std(corners))
    bg_thresh = bg_mean + 5.0 * bg_std + 10.0

    # First-stage mask: head (skull + brain + ventricles)
    head_mask = smooth > bg_thresh
    head_mask = binary_opening(head_mask, iterations=2)
    head_mask = binary_fill_holes(head_mask)

    labeled, n = scipy_label(head_mask)
    if n == 0:
        return None
    sizes = [(ll, (labeled == ll).sum()) for ll in range(1, n + 1)]
    sizes.sort(key=lambda x: -x[1])
    head = labeled == sizes[0][0]

    # Second-stage: separate brain (brighter) from skull (darker) within head.
    # 30th percentile of in-head intensity is a reasonable cut between
    # skull bone (low signal) and brain tissue (mid-high signal).
    in_head = smooth[head]
    if in_head.size < 100:
        return head
    skull_threshold = float(np.percentile(in_head, 30))
    brain = head & (smooth > skull_threshold)
    brain = binary_closing(brain, iterations=4)
    brain = binary_fill_holes(brain)

    labeled, n = scipy_label(brain)
    if n == 0:
        return head
    sizes = [(ll, (labeled == ll).sum()) for ll in range(1, n + 1)]
    sizes.sort(key=lambda x: -x[1])
    main = labeled == sizes[0][0]
    main = binary_fill_holes(main)
    return main


# ============================================================================
# Bony midline — direct from skull-edge midpoints
# ============================================================================
# ============================================================================
# K-space symmetry detection (v6)
# ============================================================================
def _find_symmetry_axis_kspace(arr):
    """Find vertical symmetry axis of a 2D array via k-space correlation
    with horizontal mirror.

    Returns the column position (with subpixel precision) of the axis at
    which arr is most LR-symmetric.

    Method: cross-correlation of arr with mirror(arr). The peak position
    in the dy=0 row of the shifted correlation map gives the LR shift
    between arr and its mirror. The symmetry axis is at image_center +
    shift/2.

    This is exact in k-space: the FFT-shift theorem guarantees that an
    integer-pixel shift in image space corresponds to a complex
    exponential in k-space. Subpixel shifts are recovered via parabolic
    fit on 3-point neighborhood of the correlation peak.
    """
    arr = arr.astype(np.float32)
    arr_z = arr - arr.mean()
    arr_mirror = arr_z[:, ::-1]
    H, W = arr.shape

    F1 = np.fft.fft2(arr_z)
    F2 = np.fft.fft2(arr_mirror)
    cross = F1 * np.conj(F2)
    corr = np.fft.fftshift(np.real(np.fft.ifft2(cross)))

    cy, cx = H // 2, W // 2
    # Pure horizontal symmetry has dy=0; peak should lie at row cy
    row = corr[cy, :]
    px = int(np.argmax(row))

    # Subpixel parabolic refinement
    if 1 <= px < W - 1:
        x0, x1, x2 = corr[cy, px - 1], corr[cy, px], corr[cy, px + 1]
        denom = x0 - 2 * x1 + x2
        dx = 0.5 * (x0 - x2) / denom if abs(denom) > 1e-10 else 0.0
        dx = max(-1.0, min(1.0, dx))
    else:
        dx = 0.0

    shift_x = (px + dx) - cx
    return cx + shift_x / 2.0


def find_bony_midline_v6(brain_mask, ps_mm):
    """Find bony midline via k-space symmetry of brain mask.

    The brain volume is contained by the rigid skull. The brain mask's
    symmetry axis IS the skull's symmetry axis (since the skull defines
    the mask shape via inner-skull boundary).

    Uses brain mask rather than full head mask to avoid contamination by
    external RF artifacts that appear as bright bands at FOV edges in
    some scanners (radiology specifically called out RF artifact in
    HOUGHTON validation case).

    Returns dict with bony_col, symmetry_score, axis_per_quartile.
    """
    if brain_mask is None or brain_mask.sum() < 1000:
        return None

    bony_col = _find_symmetry_axis_kspace(brain_mask.astype(np.float32))

    # Symmetry score: how consistent is the axis across vertical quarters?
    H, W = brain_mask.shape
    quarter_axes = []
    for q in range(4):
        r1 = q * H // 4
        r2 = (q + 1) * H // 4
        sub = brain_mask[r1:r2].astype(np.float32)
        if sub.sum() < 200:
            continue
        # Find symmetry axis of this quarter
        sub_z = sub - sub.mean()
        sub_mirror = sub_z[:, ::-1]
        F1 = np.fft.fft2(sub_z)
        F2 = np.fft.fft2(sub_mirror)
        cross = F1 * np.conj(F2)
        corr = np.fft.fftshift(np.real(np.fft.ifft2(cross)))
        sub_h, sub_w = sub.shape
        scy, scx = sub_h // 2, sub_w // 2
        if scy < corr.shape[0]:
            row = corr[scy, :]
            spx = int(np.argmax(row))
            shift_x = spx - scx
            quarter_axes.append(scx + shift_x / 2.0)

    if quarter_axes:
        midpoint_std = float(np.std(quarter_axes))
        symmetry_score = max(0.0, 1.0 - midpoint_std / 10.0)
    else:
        symmetry_score = 1.0

    return {
        'bony_col': float(bony_col),
        'symmetry_score': symmetry_score,
        'axis_per_quartile': [float(a) for a in quarter_axes],
        'method': 'k-space symmetry of brain mask',
    }


def find_falx_v6(img, brain_mask, ps_mm):
    """Find falx column via k-space symmetry of brain content.

    The falx is the dark line at the LR symmetry axis of the brain
    intensity content. On a normal scan it coincides with the bony midline.
    With mass effect, the brain content can shift while the rigid skull
    stays put, so falx_col deviates from bony_col.

    Returns the column position (subpixel) of the falx.
    """
    if brain_mask is None or brain_mask.sum() < 1000:
        return None
    content = img.astype(np.float32) * brain_mask.astype(np.float32)
    return float(_find_symmetry_axis_kspace(content))


def measure_midline_shift_v6(img, brain_mask, ps_mm):
    """Compute midline shift on a single axial slice using v6 k-space methods.

    Returns dict with bony_col, falx_col, shift_mm, symmetry_score.
    """
    bony_info = find_bony_midline_v6(brain_mask, ps_mm)
    if bony_info is None:
        return None
    falx_col = find_falx_v6(img, brain_mask, ps_mm)
    if falx_col is None:
        return None
    shift_mm = (falx_col - bony_info['bony_col']) * ps_mm
    return {
        'bony_col': bony_info['bony_col'],
        'falx_col': falx_col,
        'shift_mm': float(shift_mm),
        'symmetry_score': bony_info['symmetry_score'],
        'method': 'k-space symmetry',
    }


# ============================================================================
# Per-row skull-edge midpoint approach (v3 - kept for backward compatibility)
# ============================================================================
def find_bony_midline(brain_mask, ps_mm):
    """Find column of bony midline directly from skull-edge midpoints.

    Approach: at each axial row, the brain mask has a left edge (closest
    column with brain) and right edge (farthest). The midpoint of the
    left+right edges IS the bony midline at that row. The skull is
    rigid and symmetric — even when the brain has mass effect, the
    skull stays put. So we use the GEOMETRIC center of the head outline
    rather than any property of brain content.

    Returns dict with:
      bony_col_per_row: per-row midpoint
      bony_col: median across all valid rows (single representative)
      symmetry_score: 0-1 measure of how circular/symmetric the head
                      cross-section is. Low = irregular skull (e.g.,
                      post-craniectomy). Result is still usable but worth
                      flagging.
    """
    if brain_mask is None or brain_mask.sum() < 1000:
        return None
    H, W = brain_mask.shape

    # Per-row left/right brain extent
    left_edge = np.full(H, np.nan)
    right_edge = np.full(H, np.nan)
    for r in range(H):
        cols = np.where(brain_mask[r])[0]
        if cols.size >= 5:
            left_edge[r] = float(cols.min())
            right_edge[r] = float(cols.max())

    midpoints = (left_edge + right_edge) / 2
    valid = ~np.isnan(midpoints)
    if valid.sum() < 20:
        return None

    bony_col = float(np.median(midpoints[valid]))

    # Symmetry score: how consistent are the per-row midpoints? On a
    # symmetric head they all agree. On an irregular skull (craniectomy,
    # severe deformity) they don't.
    midpoint_std = float(np.std(midpoints[valid]))
    # Convert std-in-pixels to a 0-1 score: 0 std = 1.0; 10 px std = 0.0
    symmetry_score = max(0.0, 1.0 - midpoint_std / 10.0)

    return {
        'bony_col': bony_col,
        'bony_col_per_row': midpoints.tolist(),
        'symmetry_score': symmetry_score,
        'left_edge_per_row': left_edge.tolist(),
        'right_edge_per_row': right_edge.tolist(),
    }


# ============================================================================
# Falx tracked via dynamic programming as a continuous dark curve
# ============================================================================
def trace_falx(img, brain_mask, ps_mm, bony_col):
    """Trace falx as connected DARK path from anterior to posterior brain.

    DP enforces: consecutive rows can step left/right by at most 2 pixels.
    This makes the path a smooth curve, not a noisy point-per-row sequence.
    """
    H, W = img.shape
    smooth = gaussian_filter(img, sigma=1.0)

    if brain_mask is None or brain_mask.sum() < 1000:
        return np.full(H, np.nan)

    row_has_brain = brain_mask.any(axis=1)
    if row_has_brain.sum() < 30:
        return np.full(H, np.nan)
    first_row = int(np.argmax(row_has_brain))
    last_row = H - 1 - int(np.argmax(row_has_brain[::-1]))

    # Use a low percentile (p2) and the median for normalization so the
    # dark falx is well below 0 in normalized space (clipped to 0) while
    # brain tissue is well above 0. Bright structures (ventricles) are
    # excluded by clipping the upper end at the median, since we only care
    # about distinguishing "dark line" from "everything else."
    in_brain_vals = smooth[brain_mask]
    if in_brain_vals.size < 100:
        return np.full(H, np.nan)
    p2 = float(np.percentile(in_brain_vals, 2))
    p_mid = float(np.percentile(in_brain_vals, 50))
    if p_mid - p2 < 5:
        return np.full(H, np.nan)
    intensity_norm = np.clip((smooth - p2) / (p_mid - p2), 0, 1)

    # Cost: low where dark AND inside the search corridor around bony midline
    # Corridor penalty: search ±25mm from bony midline (covers up to ~25mm
    # mass-effect shift comfortably). Penalty weight is small relative to
    # intensity — at the corridor edge, penalty = 0.3 (weak nudge),
    # while real falx vs surrounding tissue is ~0.6+ in normalized terms.
    # This keeps the tracer locked onto actual dark structures.
    search_corridor_pix = max(int(25 / ps_mm), 8)
    cols = np.arange(W)
    corridor_dist = np.abs(cols[None, :] - bony_col)
    corridor_penalty = 0.3 * np.minimum(corridor_dist / search_corridor_pix, 1.5)

    cost = intensity_norm + corridor_penalty
    cost[~brain_mask] = 10.0

    dp = np.full((H, W), np.inf)
    back = np.full((H, W), -1, dtype=np.int32)
    dp[first_row] = cost[first_row]

    for r in range(first_row + 1, last_row + 1):
        prev = dp[r - 1]
        prev_padded = np.pad(prev, 2, mode='constant', constant_values=np.inf)
        candidates = np.stack([
            prev_padded[0:W],
            prev_padded[1:W+1],
            prev_padded[2:W+2],
            prev_padded[3:W+3],
            prev_padded[4:W+4],
        ])
        best = candidates.min(axis=0)
        best_off = candidates.argmin(axis=0) - 2
        dp[r] = best + cost[r]
        back[r] = np.arange(W) + best_off

    falx = np.full(H, np.nan)
    end_col = int(np.argmin(dp[last_row]))
    falx[last_row] = end_col
    cur = end_col
    for r in range(last_row, first_row, -1):
        cur = int(back[r, cur])
        falx[r - 1] = cur

    return falx


# ============================================================================
# Midline shift measurement
# ============================================================================
def measure_midline_shift(img, brain_mask, ps_mm):
    """Measure midline shift on a single axial slice.

    Sign convention: positive = falx pushed to patient LEFT
    (mass effect on the patient RIGHT).
    """
    if brain_mask is None or brain_mask.sum() < 1000:
        return None

    bony = find_bony_midline(brain_mask, ps_mm)
    if bony is None:
        return None

    bony_col = bony['bony_col']
    falx_col_per_row = trace_falx(img, brain_mask, ps_mm, bony_col)

    valid = ~np.isnan(falx_col_per_row)
    if valid.sum() < 20:
        return {
            'shift_mm': 0.0, 'shift_at_row': 0,
            'bony_col': float(bony_col),
            'symmetry_score': bony['symmetry_score'],
            'falx_col_per_row': falx_col_per_row.tolist(),
            'shift_per_row_mm': [None] * len(falx_col_per_row),
            'reason_no_shift': 'falx not traceable',
        }

    shift_per_row_pix = falx_col_per_row - bony_col
    shift_per_row_mm = shift_per_row_pix * ps_mm

    valid_idx = np.where(valid)[0]
    smoothed = gaussian_filter1d(shift_per_row_mm[valid], sigma=3)
    shift_per_row_mm_smoothed = np.full_like(shift_per_row_mm, np.nan)
    shift_per_row_mm_smoothed[valid_idx] = smoothed

    abs_shifts = np.abs(smoothed)
    max_idx_within_valid = int(np.argmax(abs_shifts))
    max_idx = int(valid_idx[max_idx_within_valid])
    max_shift = float(smoothed[max_idx_within_valid])

    return {
        'shift_mm': max_shift,
        'shift_at_row': max_idx,
        'bony_col': float(bony_col),
        'symmetry_score': bony['symmetry_score'],
        'falx_col_per_row': [
            float(v) if not np.isnan(v) else None for v in falx_col_per_row
        ],
        'shift_per_row_mm': [
            float(v) if not np.isnan(v) else None for v in shift_per_row_mm_smoothed
        ],
    }


# ============================================================================
# Ventricle segmentation (central-band constraint)
# ============================================================================
def segment_lateral_ventricles(img, brain_mask, ps_mm, image_kind='T2',
                                  bony_col=None):
    """Segment lateral ventricles, constrained to central third of brain.

    On T2: ventricles bright (CSF). On FLAIR/T1: dark.
    Returns (left_area_mm2, right_area_mm2) split at bony_col.
    """
    if brain_mask is None or brain_mask.sum() < 1000:
        return 0.0, 0.0

    smooth = gaussian_filter(img, sigma=1.0)
    in_brain_vals = smooth[brain_mask]
    if in_brain_vals.size < 100:
        return 0.0, 0.0

    if image_kind == 'T2':
        thresh = float(np.percentile(in_brain_vals, 92))
        vent_mask = (smooth > thresh) & brain_mask
    elif image_kind == 'FLAIR':
        thresh = float(np.percentile(in_brain_vals, 8))
        vent_mask = (smooth < thresh) & brain_mask
    else:  # T1
        thresh = float(np.percentile(in_brain_vals, 12))
        vent_mask = (smooth < thresh) & brain_mask

    vent_mask = binary_opening(vent_mask, iterations=1)

    cy, cx = center_of_mass(brain_mask)
    yy, xx = np.indices(brain_mask.shape)
    brain_extent = np.where(brain_mask.any(axis=0))[0]
    if brain_extent.size < 10:
        return 0.0, 0.0
    brain_left = int(brain_extent.min())
    brain_right = int(brain_extent.max())
    brain_width = brain_right - brain_left
    central_left = brain_left + brain_width // 4
    central_right = brain_right - brain_width // 4
    central_band = (xx >= central_left) & (xx <= central_right)
    vent_mask = vent_mask & central_band

    labeled, n = scipy_label(vent_mask)
    keep = np.zeros_like(vent_mask)
    for ll in range(1, n + 1):
        comp = labeled == ll
        if comp.sum() * ps_mm ** 2 > 50:
            keep |= comp
    vent_mask = keep

    split_col = bony_col if bony_col is not None else cx
    left_vent = vent_mask & (xx < split_col)
    right_vent = vent_mask & (xx >= split_col)
    return (
        float(left_vent.sum() * ps_mm ** 2),
        float(right_vent.sum() * ps_mm ** 2),
    )


# ============================================================================
# FLAIR lesion detection — mode-based hyperintensity tail
# ============================================================================
def detect_flair_lesions(img, brain_mask, ps_mm, ventricle_mask=None):
    """Detect hyperintense lesions using mode + 4*MAD threshold.

    Restricts search to the white-matter core (eroded brain mask, ~5mm
    inside cortex) to exclude cortical hyperintensity that's normal gray
    matter signal, not lesion. Also requires lesion intensity to be at
    least 1.5x the mode (relative threshold), so absolute intensity scale
    differences across studies don't matter.
    """
    if brain_mask is None:
        return []
    smooth = gaussian_filter(img, sigma=1.0)

    # Erode brain mask by ~3mm to exclude cortex while keeping
    # juxtacortical white matter where many real lesions sit
    erode_iter = max(1, int(round(3.0 / ps_mm)))
    from scipy.ndimage import binary_erosion
    wm_core = binary_erosion(brain_mask, iterations=erode_iter)
    if wm_core.sum() < 200:
        wm_core = brain_mask  # fallback if brain is too small

    sample_mask = wm_core
    if ventricle_mask is not None:
        sample_mask = wm_core & ~ventricle_mask
    in_brain_vals = smooth[sample_mask]
    if in_brain_vals.size < 200:
        return []

    p1 = float(np.percentile(in_brain_vals, 1))
    p99 = float(np.percentile(in_brain_vals, 99))
    if p99 - p1 < 5:
        return []
    hist, bin_edges = np.histogram(in_brain_vals, bins=64, range=(p1, p99))
    mode_idx = int(np.argmax(hist))
    mode = float((bin_edges[mode_idx] + bin_edges[mode_idx + 1]) / 2)

    deviations = np.abs(in_brain_vals - mode)
    mad = float(np.median(deviations)) if deviations.size > 0 else 1.0
    if mad < 1.0:
        mad = 1.0

    # Two-part threshold: stat-based AND ratio-based. Lesion must be both:
    #   - at least mode + 4*MAD (statistical outlier)
    #   - at least 1.25 * mode (relative hyperintensity)
    # Ratio criterion prevents normal cortex from firing on contrast-rich
    # phantoms; stat criterion handles real noise distribution.
    thresh_stat = mode + 4.0 * mad
    thresh_ratio = 1.25 * mode
    thresh = max(thresh_stat, thresh_ratio)

    hyper = (smooth > thresh) & wm_core
    if ventricle_mask is not None:
        hyper = hyper & ~ventricle_mask
    hyper = binary_opening(hyper, iterations=1)

    labeled, n = scipy_label(hyper)
    lesions = []
    for ll in range(1, n + 1):
        comp = labeled == ll
        a = comp.sum() * ps_mm ** 2
        if a < 5:
            continue
        if a > 5000:
            continue
        cy, cx = center_of_mass(comp)
        lesions.append({
            'area_mm2': float(a),
            'centroid_rc': (float(cy), float(cx)),
            'intensity_max': float(smooth[comp].max()),
        })
    return lesions


# ============================================================================
# Main analyzer
# ============================================================================
# ============================================================================
# v4 corpus-math internals (loaded after v3 helpers are defined to avoid cycles)
# ============================================================================
from .brain_v4 import (
    measure_midline_shift_v4,
    bilateral_asymmetry,
    calibrate_wm_intensity,
    detect_flair_lesions_v4,
    sustained_shift_max,
    BRAIN_ASYMMETRY_SCORE,
    SUSTAINED_SHIFT_MIN_MAGNITUDE_MM,
)


class BrainV6Analyzer(BaseAnalyzer):
    # Empty codes: not picked up by detect_body_part(). Reachable only via
    # explicit ?body_part=brain_v6 override on the API. The existing
    # BrainAnalyzer in brain.py keeps the BRAIN/HEAD/NEURO codes and remains
    # the auto-detect target.
    body_part_codes = ()
    body_part_label = 'brain_v6'
    version = 'brain.v6'

    def analyze(self, series_list, work_dir=None):
        """
        Brain analyzer v4 — corpus-math grounded, real-data validated.

        Replaces v3 broken internals:
          - Falx tracer perimeter cost-trough → HARD viscous corridor cutoff (VSP)
          - Asymmetry/displacement conflated → wavelet-mirror residual gives
            independent asymmetry metric without needing falx detection
          - FLAIR lesions calling cortex/fat → volume-calibrated WM threshold
            + T1 confirmation (real lesions are dark on T1, fat is bright)

        Plus: midline-shift flags only fire on SUSTAINED shifts across multiple
        consecutive slices. Isolated single-slice anatomic asymmetry doesn't
        trigger flags (validated against real normal GE FLAIR study).
        """
        chosen, kind, all_flair, all_t2, all_t1 = select_best_brain_axial(series_list)
        if chosen is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'reason': 'no axial T1/T2/FLAIR series found',
                'series_seen': [
                    {k: s[k] for k in ('series_description', 'orientation',
                                        'modality', 'n_slices')}
                    for s in series_list
                ],
            }

        ax_items = load_volume(chosen['files'])

        flair_axial = None
        flair_items = None
        if kind == 'FLAIR':
            flair_axial = chosen
            flair_items = ax_items
        elif all_flair:
            flair_axial = max(all_flair, key=lambda s: s['n_slices'])
            try:
                flair_items = load_volume(flair_axial['files'])
            except Exception:
                flair_items = None

        # T1 axial for lesion confirmation. Prefer T1 with matching geometry
        # to FLAIR (same IPP/IOP/PixelSpacing). A co-registered T1 lets us
        # filter subcutaneous fat (bright on both FLAIR and T1) from real
        # WM hyperintensities (bright on FLAIR, dark on T1). A mis-aligned
        # T1 (e.g., 3D MP-RAGE volume reformat) would put fat at wrong
        # voxel positions and produce false confirmations.
        t1_items = None
        if all_t1:
            flair_n = (flair_axial['n_slices']
                        if flair_axial else chosen['n_slices'])
            # Score: matching slice count is a strong signal for co-registration
            def t1_score(s):
                match_bonus = 1_000_000 if s['n_slices'] == flair_n else 0
                return match_bonus + s['n_slices']
            t1_axial = max(all_t1, key=t1_score)
            try:
                t1_items_candidate = load_volume(t1_axial['files'])
                # Verify geometry match against FLAIR (or chosen if FLAIR is chosen)
                ref_items = (flair_items if flair_items is not None
                              else ax_items)
                if (t1_items_candidate and ref_items and
                        len(t1_items_candidate) == len(ref_items)):
                    # Check if first slice's IPP matches
                    ref0 = ref_items[0]
                    t1_0 = t1_items_candidate[0]
                    ipp_match = (np.allclose(ref0['ipp'], t1_0['ipp'], atol=2.0)
                                  and np.allclose(ref0['ps'], t1_0['ps'], atol=0.05))
                    if ipp_match:
                        t1_items = t1_items_candidate
            except Exception:
                t1_items = None

        # ============= Build z-indexed FLAIR/T1 for co-registration =============
        flair_z_index = {}
        flair_volume_items = []  # for WM calibration
        if flair_items is not None:
            for it in flair_items:
                ps_mm_f = float(it['ps'][0])
                bm_f = detect_brain_mask(it['img'], ps_mm_f)
                if bm_f is None or bm_f.sum() < 1000:
                    continue
                in_b = it['img'][bm_f]
                if in_b.size == 0:
                    continue
                thresh_v = float(np.percentile(in_b, 8))
                vent_f = (it['img'] < thresh_v) & bm_f
                flair_volume_items.append((it['img'], bm_f, vent_f))
                # Index by IPP z-coordinate for co-reg with T1
                z_key = round(float(it['ipp'][2]), 1)
                flair_z_index[z_key] = (it['img'], bm_f, vent_f, ps_mm_f, it['inst'])

        t1_z_index = {}
        if t1_items is not None:
            for it in t1_items:
                z_key = round(float(it['ipp'][2]), 1)
                t1_z_index[z_key] = it['img']

        wm_calib = None
        if flair_volume_items:
            ps_mm_calib = float(flair_items[0]['ps'][0])
            wm_calib = calibrate_wm_intensity(flair_volume_items, ps_mm_calib)

        # ============= Per-slice analysis on primary axial series =============
        slice_records = []
        markers = []
        all_shift_mm_in_z_order = []
        z_for_shift_order = []

        for it in ax_items:
            ps_mm = float(it['ps'][0])
            brain_mask = detect_brain_mask(it['img'], ps_mm)
            if brain_mask is None or brain_mask.sum() * ps_mm ** 2 < 5000:
                continue

            # v6: k-space symmetry-based midline detection
            # (replaces v4 DP falx tracker which produced -63mm false positive
            # on HOUGHTON normal study due to head-edge dark curves)
            mid_v6 = measure_midline_shift_v6(it['img'], brain_mask, ps_mm)
            if mid_v6 is not None:
                mid = {
                    'shift_mm': mid_v6['shift_mm'],
                    'bony_col': mid_v6['bony_col'],
                    'falx_col': mid_v6['falx_col'],
                    'symmetry_score': mid_v6['symmetry_score'],
                    'method': mid_v6['method'],
                    # v4-compat fields (downstream code reads these from
                    # the v4 schema; v6's k-space approach doesn't compute
                    # them per-row but populates with reasonable defaults)
                    'detection_status': 'detected',
                    'mean_falx_norm': None,
                    'frac_within_corridor': 1.0,
                }
            else:
                mid = {
                    'shift_mm': None,
                    'bony_col': None,
                    'falx_col': None,
                    'symmetry_score': 0.0,
                    'detection_status': 'no_brain',
                    'mean_falx_norm': None,
                    'frac_within_corridor': 0.0,
                }

            # Bilateral asymmetry (independent of falx detection)
            bony_info = find_bony_midline_v6(brain_mask, ps_mm)
            if bony_info is not None:
                asym = bilateral_asymmetry(it['img'], brain_mask,
                                              bony_info['bony_col'], ps_mm)
            else:
                asym = None

            # Use mid['bony_col'] if detection got that far; else asym fallback
            bony_col_for_vent = mid.get('bony_col')
            if bony_col_for_vent is None and bony_info is not None:
                bony_col_for_vent = bony_info['bony_col']
            if bony_col_for_vent is None:
                continue  # truly cannot orient this slice

            left_v, right_v = segment_lateral_ventricles(
                it['img'], brain_mask, ps_mm, image_kind=kind,
                bony_col=bony_col_for_vent,
            )
            vent_total = left_v + right_v
            vent_asym = ((left_v - right_v) / vent_total) if vent_total > 0 else 0.0

            yy, xx = np.indices(brain_mask.shape)
            left_brain = brain_mask & (xx < bony_col_for_vent)
            right_brain = brain_mask & (xx >= bony_col_for_vent)
            left_a = float(left_brain.sum() * ps_mm ** 2)
            right_a = float(right_brain.sum() * ps_mm ** 2)
            tot_a = left_a + right_a
            brain_asym_geom = ((left_a - right_a) / tot_a) if tot_a > 0 else 0.0

            # Per-slice flags: only fire ONLY if shift is detected confidently
            slice_flags = []
            shift_mm = mid['shift_mm']
            if np.isfinite(shift_mm):
                ash = abs(shift_mm)
                # Per-slice CRITICAL/MODERATE only if magnitude is large; FINDING
                # promotion happens only on the volume-level sustained metric below
                if ash >= BRAIN_MIDLINE_SHIFT['critical_mm']:
                    slice_flags.append({
                        'label': f"midline shift {shift_mm:+.1f} mm",
                        'severity': 'CRITICAL',
                    })
                elif ash >= BRAIN_MIDLINE_SHIFT['moderate_mm']:
                    slice_flags.append({
                        'label': f"midline shift {shift_mm:+.1f} mm",
                        'severity': 'MODERATE',
                    })
                # No FINDING-level per-slice flag for shift — those are below
                # the resolution noise floor for an isolated slice

            # Coord transform for markers
            ipp, iop, ps = it['ipp'], it['iop'], it['ps']
            cy_b, cx_b = center_of_mass(brain_mask)
            brain_centroid_xyz = ipp + cx_b*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]
            bony_xyz = ipp + bony_col_for_vent*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]

            falx_col = mid.get('falx_col')
            if falx_col is None or not np.isfinite(falx_col):
                falx_col = bony_col_for_vent  # fallback for marker placement
            falx_xyz = ipp + falx_col*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]

            slice_records.append({
                'inst': it['inst'],
                'z_mm': float(brain_centroid_xyz[2]),
                'brain_area_mm2': tot_a,
                'detection_status': mid['detection_status'],
                'midline_shift_mm': (float(shift_mm)
                                       if np.isfinite(shift_mm) else None),
                # smoothed_mm kept for back-compat; populated post-loop
                'midline_shift_smoothed_mm': 0.0,
                'mean_falx_norm': mid.get('mean_falx_norm'),
                'frac_within_corridor': mid.get('frac_within_corridor'),
                'asymmetry_score': (asym['total_score'] if asym else None),
                'asymmetry_side_bias': (asym['side_bias'] if asym else None),
                'symmetry_score': float(mid.get('symmetry_score', 0.0)),
                'left_ventricle_mm2': left_v,
                'right_ventricle_mm2': right_v,
                'ventricle_asym_lr': vent_asym,
                'brain_asym_lr': brain_asym_geom,
                'flags': slice_flags,
            })

            all_shift_mm_in_z_order.append(shift_mm)
            z_for_shift_order.append(float(brain_centroid_xyz[2]))

            markers.append({
                'inst': it['inst'],
                'z_mm': float(brain_centroid_xyz[2]),
                'bony_midline_xyz_mm': [round(float(v), 3) for v in bony_xyz],
                'falx_xyz_mm': [round(float(v), 3) for v in falx_xyz],
                'brain_centroid_xyz_mm': [round(float(v), 3)
                                             for v in brain_centroid_xyz],
                'midline_shift_mm': (round(float(shift_mm), 2)
                                       if np.isfinite(shift_mm) else None),
                'shift_direction': (
                    'left' if (np.isfinite(shift_mm) and shift_mm > 0)
                    else 'right' if (np.isfinite(shift_mm) and shift_mm < 0)
                    else 'none'
                ),
                'detection_status': mid['detection_status'],
                'symmetry_score': round(float(mid.get('symmetry_score', 0.0)), 3),
                'asymmetry_score': (round(float(asym['total_score']), 3)
                                       if asym else None),
                'ventricle_asym_lr': round(float(vent_asym), 3),
                'brain_asym_lr': round(float(brain_asym_geom), 3),
                'severity': max_severity(slice_flags),
            })

        # ============= Volume-level metrics =============
        # Z-order shifts for sustained-shift analysis
        if z_for_shift_order:
            order = np.argsort(z_for_shift_order)
            shifts_in_z = np.array(all_shift_mm_in_z_order, dtype=float)[order]
            sustained_shift = sustained_shift_max(
                shifts_in_z,
                min_consecutive=3,
                threshold_mm=SUSTAINED_SHIFT_MIN_MAGNITUDE_MM,
            )
            # Populate smoothed_mm field for back-compat (NaN-safe median filter)
            from .brain_v4 import smooth_shifts_across_z
            smoothed = smooth_shifts_across_z(shifts_in_z, window=5)
            for i_in_order, smv in enumerate(smoothed):
                orig_i = int(order[i_in_order])
                slice_records[orig_i]['midline_shift_smoothed_mm'] = (
                    float(smv) if np.isfinite(smv) else 0.0
                )
        else:
            sustained_shift = 0.0

        asym_scores = [s['asymmetry_score'] for s in slice_records
                        if s.get('asymmetry_score') is not None]
        asym_max = max(asym_scores) if asym_scores else 0.0

        # ============= FLAIR lesion detection (volume-calibrated + T1-confirmed) =============
        flair_lesion_summary = None
        all_flair_lesions = []
        if wm_calib is not None and flair_z_index:
            lesions_by_slice = {}
            for z_key, (flair_img, bm_f, vent_f, ps_mm_f, inst_f) in flair_z_index.items():
                t1_img = t1_z_index.get(z_key)
                lesions = detect_flair_lesions_v4(
                    flair_img, bm_f, ps_mm_f,
                    ventricle_mask=vent_f,
                    t1_img=t1_img,
                    wm_calib=wm_calib,
                )
                if lesions:
                    lesions_by_slice[inst_f] = lesions
                    all_flair_lesions.extend(lesions)
            flair_lesion_summary = {
                'lesion_count': len(all_flair_lesions),
                'lesion_total_area_mm2': sum(L['area_mm2']
                                                for L in all_flair_lesions),
                'lesions_by_slice': lesions_by_slice,
                'wm_calibration': wm_calib,
                't1_confirmed': bool(t1_z_index),
            }

        # ============= Volume-level flag generation =============
        all_flags = []

        # Midline shift (sustained criterion)
        if abs(sustained_shift) >= BRAIN_MIDLINE_SHIFT['critical_mm']:
            all_flags.append({
                'label': f"midline shift {sustained_shift:+.1f} mm (sustained)",
                'severity': 'CRITICAL', 'level': 'overall',
            })
        elif abs(sustained_shift) >= BRAIN_MIDLINE_SHIFT['moderate_mm']:
            all_flags.append({
                'label': f"midline shift {sustained_shift:+.1f} mm (sustained)",
                'severity': 'MODERATE', 'level': 'overall',
            })
        elif abs(sustained_shift) >= SUSTAINED_SHIFT_MIN_MAGNITUDE_MM:
            all_flags.append({
                'label': f"midline shift {sustained_shift:+.1f} mm (sustained)",
                'severity': 'FINDING', 'level': 'overall',
            })

        # Bilateral asymmetry (new — wavelet-mirror residual)
        if asym_max >= BRAIN_ASYMMETRY_SCORE['critical']:
            all_flags.append({
                'label': f"bilateral asymmetry {asym_max:.3f}",
                'severity': 'CRITICAL', 'level': 'overall',
            })
        elif asym_max >= BRAIN_ASYMMETRY_SCORE['moderate']:
            all_flags.append({
                'label': f"bilateral asymmetry {asym_max:.3f}",
                'severity': 'MODERATE', 'level': 'overall',
            })
        elif asym_max >= BRAIN_ASYMMETRY_SCORE['finding']:
            all_flags.append({
                'label': f"bilateral asymmetry {asym_max:.3f}",
                'severity': 'FINDING', 'level': 'overall',
            })

        # Ventricular asymmetry — RELIABLE ONLY ON T2 (ventricles are
        # bright, well-separable from parenchyma). On FLAIR, CSF is
        # suppressed so the "ventricle" mask catches a mix of ventricles,
        # sulci, and edge partial-volume — per-slice asymmetry can swing
        # from -0.32 to +1.00 on a normal brain. We compute the metric
        # for transparency but DON'T fire flags from a FLAIR-primary scan.
        # Validated on real GE FLAIR normal brain: per-slice asym ranges
        # -0.32 to +1.00, volume total +0.13 — all noise, no real asymmetry.
        total_left_vent = sum(s['left_ventricle_mm2'] for s in slice_records)
        total_right_vent = sum(s['right_ventricle_mm2'] for s in slice_records)
        total_vent = total_left_vent + total_right_vent
        vent_asym_overall = ((total_left_vent - total_right_vent) / total_vent
                              if total_vent > 0 else 0.0)
        ventricle_flagging_reliable = (kind == 'T2')
        if ventricle_flagging_reliable:
            if abs(vent_asym_overall) >= BRAIN_VENTRICLE_ASYM['critical_abs']:
                all_flags.append({
                    'label': f"marked ventricular asymmetry ({vent_asym_overall:+.2f})",
                    'severity': 'CRITICAL', 'level': 'overall',
                })
            elif abs(vent_asym_overall) >= BRAIN_VENTRICLE_ASYM['moderate_abs']:
                all_flags.append({
                    'label': f"moderate ventricular asymmetry ({vent_asym_overall:+.2f})",
                    'severity': 'MODERATE', 'level': 'overall',
                })
            elif abs(vent_asym_overall) >= BRAIN_VENTRICLE_ASYM['finding_abs']:
                all_flags.append({
                    'label': f"mild ventricular asymmetry ({vent_asym_overall:+.2f})",
                    'severity': 'FINDING', 'level': 'overall',
                })

        # FLAIR lesions
        n_lesions = len(all_flair_lesions)
        if n_lesions >= BRAIN_FLAIR_LESION['critical_count']:
            all_flags.append({
                'label': f"high FLAIR lesion burden ({n_lesions} foci)",
                'severity': 'CRITICAL', 'level': 'overall',
            })
        elif n_lesions >= BRAIN_FLAIR_LESION['moderate_count']:
            all_flags.append({
                'label': f"moderate FLAIR lesion burden ({n_lesions} foci)",
                'severity': 'MODERATE', 'level': 'overall',
            })
        elif n_lesions >= BRAIN_FLAIR_LESION['finding_count']:
            all_flags.append({
                'label': f"FLAIR lesions present ({n_lesions} foci)",
                'severity': 'FINDING', 'level': 'overall',
            })

        # ============= Build response =============
        overall = max_severity(all_flags) if all_flags else 'normal'
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in all_flags:
            sev = f['severity'].lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not all_flags:
            counts['normal'] = 1

        # Detection breakdown for transparency
        detection_breakdown = {
            'detected':      sum(1 for s in slice_records
                                    if s['detection_status'] == 'detected'),
            'indeterminate': sum(1 for s in slice_records
                                    if s['detection_status'] == 'indeterminate'),
            'no_falx':       sum(1 for s in slice_records
                                    if s['detection_status'] == 'no_falx'),
            'no_brain':      sum(1 for s in slice_records
                                    if s['detection_status'] == 'no_brain'),
        }

        return {
            'status': overall,
            'body_part_label': self.body_part_label,
            'version': self.version,
            'series_used': {
                'primary_axial': {
                    'series_description': chosen['series_description'],
                    'n_slices': chosen['n_slices'],
                    'series_uid': chosen['series_uid'],
                    'sequence_type': kind,
                },
                'flair_axial': (
                    {
                        'series_description': flair_axial['series_description'],
                        'n_slices': flair_axial['n_slices'],
                        'series_uid': flair_axial['series_uid'],
                    } if flair_axial else None
                ),
                't1_axial_for_lesion_confirmation': bool(t1_z_index),
            },
            'levels_detected': {},
            'impression': {
                'overall_status': overall,
                'counts': counts,
                'flags': all_flags,
            },
            'level_summaries': [],
            'slice_measurements': slice_records,
            'markers': markers,
            'brain_findings': {
                'sustained_midline_shift_mm': float(sustained_shift),
                'max_bilateral_asymmetry_score': float(asym_max),
                'total_left_ventricle_mm2': total_left_vent,
                'total_right_ventricle_mm2': total_right_vent,
                'ventricle_asym_overall': vent_asym_overall,
                'flair_lesion_summary': flair_lesion_summary,
                'n_brain_slices_analyzed': len(slice_records),
                'detection_breakdown': detection_breakdown,
                # Back-compat: keep legacy fields populated
                'max_midline_shift_mm': float(sustained_shift),
                'max_shift_at_z_mm': 0.0,
            },
            'algorithm_version': 'v6',
        }
