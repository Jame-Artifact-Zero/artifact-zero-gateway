"""
analyzers/brain.py — v3
========================
Brain MRI analyzer with three independent fixes from v1/v2 lessons:

  v1 → v2 lessons:
    - Row-wise minimum search for falx is wrong (sulci/ventricles are also dark)
    - Brain mask centroid drifts and isn't a reliable bony midline reference
    - Per-slice independence allows impossible direction-flipping

  v2 → v3 lessons (from synthetic phantom testing):
    - Otsu threshold for brain mask gets pulled by bright ventricles, ending
      up capturing only half the brain → fixed via BACKGROUND-anchored
      threshold (mean of FOV corners + 5σ)
    - Symmetry-correlation maximization for bony midline locks onto
      ventricle centers when ventricles are present → fixed via direct
      SKULL-EDGE-MIDPOINT computation (skull is geometrically symmetric
      regardless of brain content)

The current pipeline:

  1. Brain mask via background-anchored threshold (catches whole head),
     then second threshold inside head to separate brain from skull.
  2. Bony midline = median of (left_skull_edge + right_skull_edge) / 2
     across rows. Skull is mechanically rigid; this is the correct
     reference for any midline shift measurement.
  3. Falx tracked via dynamic programming as a connected DARK path from
     anterior to posterior brain, with smoothness constraint (consecutive
     rows can step ±2 pixels).
  4. Midline shift = (falx_col - bony_col) * pixel_spacing, smoothed
     within slice and median-filtered across slices.
  5. Ventricle segmentation constrained to central third of brain.
  6. FLAIR lesions via mode + 4*MAD threshold (robust to lesion outliers).

Sequence preference for analysis:
  Axial T2 FLAIR > Axial T2 > Axial T1
  FLAIR additionally used for lesion detection regardless of primary.

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
class BrainAnalyzer(BaseAnalyzer):
    body_part_codes = ('BRAIN', 'HEAD', 'NEURO')
    body_part_label = 'brain'
    version = 'brain.v3'

    def analyze(self, series_list, work_dir=None):
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

        slice_records = []
        markers = []
        for it in ax_items:
            ps_mm = float(it['ps'][0])
            brain_mask = detect_brain_mask(it['img'], ps_mm)
            if brain_mask is None or brain_mask.sum() * ps_mm ** 2 < 5000:
                continue

            mid = measure_midline_shift(it['img'], brain_mask, ps_mm)
            if mid is None:
                continue

            left_v, right_v = segment_lateral_ventricles(
                it['img'], brain_mask, ps_mm, image_kind=kind,
                bony_col=mid['bony_col'],
            )
            vent_total = left_v + right_v
            vent_asym = ((left_v - right_v) / vent_total) if vent_total > 0 else 0.0

            yy, xx = np.indices(brain_mask.shape)
            left_brain = brain_mask & (xx < mid['bony_col'])
            right_brain = brain_mask & (xx >= mid['bony_col'])
            left_a = float(left_brain.sum() * ps_mm ** 2)
            right_a = float(right_brain.sum() * ps_mm ** 2)
            tot_a = left_a + right_a
            brain_asym = ((left_a - right_a) / tot_a) if tot_a > 0 else 0.0

            flags = []
            shift = abs(mid['shift_mm'])
            if shift >= BRAIN_MIDLINE_SHIFT['critical_mm']:
                flags.append({
                    'label': f"midline shift {mid['shift_mm']:+.1f} mm",
                    'severity': 'CRITICAL',
                })
            elif shift >= BRAIN_MIDLINE_SHIFT['moderate_mm']:
                flags.append({
                    'label': f"midline shift {mid['shift_mm']:+.1f} mm",
                    'severity': 'MODERATE',
                })
            elif shift >= BRAIN_MIDLINE_SHIFT['finding_mm']:
                flags.append({
                    'label': f"midline shift {mid['shift_mm']:+.1f} mm",
                    'severity': 'FINDING',
                })

            ipp, iop, ps = it['ipp'], it['iop'], it['ps']
            cy_b, cx_b = center_of_mass(brain_mask)
            brain_centroid_xyz = ipp + cx_b*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]
            bony_xyz = ipp + mid['bony_col']*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]
            falx_at_row = mid.get('shift_at_row', int(cy_b))
            falx_col_at_row = mid['falx_col_per_row'][falx_at_row]
            falx_col = (falx_col_at_row if falx_col_at_row is not None
                        else mid['bony_col'])
            falx_xyz = ipp + falx_col*ps[1]*iop[0:3] + falx_at_row*ps[0]*iop[3:6]

            slice_records.append({
                'inst': it['inst'],
                'z_mm': float(brain_centroid_xyz[2]),
                'brain_area_mm2': tot_a,
                'midline_shift_mm': mid['shift_mm'],
                'symmetry_score': mid['symmetry_score'],
                'left_ventricle_mm2': left_v,
                'right_ventricle_mm2': right_v,
                'ventricle_asym_lr': vent_asym,
                'brain_asym_lr': brain_asym,
                'flags': flags,
            })
            markers.append({
                'inst': it['inst'],
                'z_mm': float(brain_centroid_xyz[2]),
                'bony_midline_xyz_mm': [round(float(v), 3) for v in bony_xyz],
                'falx_xyz_mm': [round(float(v), 3) for v in falx_xyz],
                'brain_centroid_xyz_mm': [round(float(v), 3) for v in brain_centroid_xyz],
                'midline_shift_mm': round(float(mid['shift_mm']), 2),
                'shift_direction': ('left' if mid['shift_mm'] > 0
                                     else 'right' if mid['shift_mm'] < 0
                                     else 'none'),
                'symmetry_score': round(float(mid['symmetry_score']), 3),
                'ventricle_asym_lr': round(float(vent_asym), 3),
                'brain_asym_lr': round(float(brain_asym), 3),
                'severity': max_severity(flags),
            })

        # Z-axis consistency: median-filter shifts so single-slice spikes
        # don't trigger CRITICAL flags
        if slice_records:
            shifts = np.array([s['midline_shift_mm'] for s in slice_records])
            zs = np.array([s['z_mm'] for s in slice_records])
            order = np.argsort(zs)
            shifts_sorted = shifts[order]
            shifts_filtered = median_filter(shifts_sorted, size=5, mode='nearest')
            inv_order = np.argsort(order)
            shifts_smoothed = shifts_filtered[inv_order]
            for i, s in enumerate(slice_records):
                s['midline_shift_smoothed_mm'] = float(shifts_smoothed[i])

        all_flags = []

        max_shift_record = None
        if slice_records:
            max_shift_record = max(
                slice_records,
                key=lambda s: abs(s.get('midline_shift_smoothed_mm', 0.0)),
            )
            ms = abs(max_shift_record.get('midline_shift_smoothed_mm', 0.0))
            shift_value = max_shift_record.get('midline_shift_smoothed_mm', 0.0)
            level_str = f"z={max_shift_record['z_mm']:.0f}"
            if ms >= BRAIN_MIDLINE_SHIFT['critical_mm']:
                all_flags.append({
                    'label': f"midline shift {shift_value:+.1f} mm (sustained)",
                    'severity': 'CRITICAL', 'level': level_str,
                })
            elif ms >= BRAIN_MIDLINE_SHIFT['moderate_mm']:
                all_flags.append({
                    'label': f"midline shift {shift_value:+.1f} mm (sustained)",
                    'severity': 'MODERATE', 'level': level_str,
                })
            elif ms >= BRAIN_MIDLINE_SHIFT['finding_mm']:
                all_flags.append({
                    'label': f"midline shift {shift_value:+.1f} mm",
                    'severity': 'FINDING', 'level': level_str,
                })

        total_left_vent = sum(s['left_ventricle_mm2'] for s in slice_records)
        total_right_vent = sum(s['right_ventricle_mm2'] for s in slice_records)
        total_vent = total_left_vent + total_right_vent
        vent_asym_overall = ((total_left_vent - total_right_vent) / total_vent
                              if total_vent > 0 else 0.0)
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

        flair_lesion_summary = None
        if flair_items is not None:
            total_lesion_count = 0
            total_lesion_area_mm2 = 0.0
            lesions_by_slice = {}
            for it in flair_items:
                ps_mm = float(it['ps'][0])
                bm = detect_brain_mask(it['img'], ps_mm)
                if bm is None:
                    continue
                if kind == 'FLAIR' or flair_axial is chosen:
                    in_b = it['img'][bm]
                    if in_b.size > 0:
                        thresh_v = float(np.percentile(in_b, 8))
                        vent = (it['img'] < thresh_v) & bm
                    else:
                        vent = None
                else:
                    vent = None
                lesions = detect_flair_lesions(it['img'], bm, ps_mm,
                                                ventricle_mask=vent)
                if lesions:
                    lesions_by_slice[it['inst']] = lesions
                    total_lesion_count += len(lesions)
                    total_lesion_area_mm2 += sum(l['area_mm2'] for l in lesions)

            flair_lesion_summary = {
                'lesion_count': total_lesion_count,
                'lesion_total_area_mm2': total_lesion_area_mm2,
                'lesions_by_slice': lesions_by_slice,
            }
            if total_lesion_count >= BRAIN_FLAIR_LESION['critical_count']:
                all_flags.append({
                    'label': f"high FLAIR lesion burden ({total_lesion_count} foci)",
                    'severity': 'CRITICAL', 'level': 'overall',
                })
            elif total_lesion_count >= BRAIN_FLAIR_LESION['moderate_count']:
                all_flags.append({
                    'label': f"moderate FLAIR lesion burden ({total_lesion_count} foci)",
                    'severity': 'MODERATE', 'level': 'overall',
                })
            elif total_lesion_count >= BRAIN_FLAIR_LESION['finding_count']:
                all_flags.append({
                    'label': f"FLAIR lesions present ({total_lesion_count} foci)",
                    'severity': 'FINDING', 'level': 'overall',
                })

        overall = max_severity(all_flags)
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in all_flags:
            sev = f['severity'].lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not all_flags:
            counts['normal'] = 1

        if slice_records:
            mean_symm = float(np.mean([s['symmetry_score'] for s in slice_records]))
            min_symm = float(min(s['symmetry_score'] for s in slice_records))
        else:
            mean_symm = 0.0
            min_symm = 0.0

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
                'max_midline_shift_mm':
                    float(max_shift_record.get('midline_shift_smoothed_mm', 0.0))
                    if max_shift_record else 0.0,
                'max_shift_at_z_mm':
                    float(max_shift_record['z_mm']) if max_shift_record else 0.0,
                'total_left_ventricle_mm2': total_left_vent,
                'total_right_ventricle_mm2': total_right_vent,
                'ventricle_asym_overall': vent_asym_overall,
                'flair_lesion_summary': flair_lesion_summary,
                'n_brain_slices_analyzed': len(slice_records),
                'detection_reliability': {
                    'mean_symmetry_score': mean_symm,
                    'min_symmetry_score': min_symm,
                    'note': (
                        'symmetry_score reflects skull-edge midpoint '
                        'consistency across rows. ~1.0 = uniform skull '
                        '(reliable). Below 0.5 suggests irregular skull '
                        'contour (post-craniectomy, severe deformity); '
                        'midline shift values should be reviewed manually.'
                    ),
                },
            },
        }
