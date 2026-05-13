"""
analyzers/cspine_k7.py - Cervical spine measurement engine
============================================================

k7 is a measurement engine. It places markers at anatomic features and
returns their patient-coordinate positions and intensities. k7 does not
interpret, classify, or generate severity.

The output is a list of marker dicts:

    {
        'type':       str,
        'xyz_mm':     [x, y, z],
        'intensity':  float,
        'confidence': float,    # 0..1
        'slice_inst': int,
        'slice_idx':  int,
    }

See DESIGN.md for full contract.

Architecture: k7 reuses k-space template machinery from v6 (vendor-neutral by
construction) and does not import any v6 calibration constants. v6 stays
intact and unchanged; k7 is purely additive.

Author: Jame Houghton / Artifact Zero Labs, May 2026
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from typing import List, Dict, Any, Optional

from ._base import (
    classify_orientation, load_volume, is_t2, slice_z_center,
    patient_xy_from_pix, pix_from_patient_xy,
)


# ============================================================================
# K-space primitives - measurement, no interpretation
# ============================================================================

# These constants describe the physical anatomy the template is built to
# match. They are NOT calibration knobs - they are dimensions of the cord
# in millimeters, which are the same on every scanner.
CORD_RADIUS_MM = 3.5      # cord cross-section radius
CSF_OUTER_MM   = 7.0      # outer radius of CSF ring around cord
ROI_SIZE_PX    = 80       # k-space ROI side, in pixels (fixed for FFT speed)


def _build_cord_template(roi_size: int, ps_mm: float,
                          cord_radius_mm: float = CORD_RADIUS_MM,
                          csf_outer_mm: float = CSF_OUTER_MM) -> np.ndarray:
    """Idealized cord+CSF+bone radial template.

    Cord radius and CSF outer radius are anatomic constants in millimeters.
    The template adapts to vendor pixel spacing automatically - at 0.156 mm/px
    the cord spans more pixels, at 0.347 mm/px fewer, but the physical
    dimensions are identical.
    """
    yy, xx = np.indices((roi_size, roi_size))
    cy, cx = roi_size // 2, roi_size // 2
    r_pix = np.hypot(yy - cy, xx - cx) * ps_mm
    template = np.zeros((roi_size, roi_size), dtype=np.float32)
    csf_mask = r_pix < csf_outer_mm
    cord_mask = r_pix < cord_radius_mm
    template[csf_mask] = 2.0     # CSF brightest on T2
    template[cord_mask] = 1.0    # cord mid-bright
    return template


def _normalized_cross_correlation_freq(img: np.ndarray,
                                         template: np.ndarray) -> np.ndarray:
    """FFT-based normalized cross-correlation."""
    img_z = img - img.mean()
    tmpl_z = template - template.mean()
    F_img = np.fft.fft2(img_z)
    F_tmpl = np.fft.fft2(tmpl_z)
    cross = F_img * np.conj(F_tmpl)
    return np.fft.fftshift(np.real(np.fft.ifft2(cross)))


def _subpixel_peak(corr: np.ndarray) -> tuple:
    """Parabolic fit on 3x3 around argmax to recover subpixel peak."""
    H, W = corr.shape
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    if 1 <= py < H - 1 and 1 <= px < W - 1:
        y0, y1, y2 = corr[py - 1, px], corr[py, px], corr[py + 1, px]
        denom_y = (y0 - 2 * y1 + y2)
        dy = 0.5 * (y0 - y2) / denom_y if abs(denom_y) > 1e-10 else 0.0
        x0, x1, x2 = corr[py, px - 1], corr[py, px], corr[py, px + 1]
        denom_x = (x0 - 2 * x1 + x2)
        dx = 0.5 * (x0 - x2) / denom_x if abs(denom_x) > 1e-10 else 0.0
        dy = max(-1.0, min(1.0, dy))
        dx = max(-1.0, min(1.0, dx))
    else:
        dy = dx = 0.0
    return float(py + dy), float(px + dx)


def _sample_intensity(img: np.ndarray, rc: tuple,
                       ps_mm: float, radius_mm: float = 1.0) -> float:
    """Mean pixel value in a radius_mm disc around subpixel position rc."""
    cy, cx = rc
    H, W = img.shape
    n = int(radius_mm / ps_mm) + 1
    r1 = max(0, int(cy) - n); r2 = min(H, int(cy) + n + 1)
    c1 = max(0, int(cx) - n); c2 = min(W, int(cx) + n + 1)
    yy, xx = np.indices((r2 - r1, c2 - c1))
    d_mm = np.hypot(yy + r1 - cy, xx + c1 - cx) * ps_mm
    mask = d_mm <= radius_mm
    if not mask.any():
        return float(img[int(cy), int(cx)])
    return float(img[r1:r2, c1:c2][mask].mean())


def _normalize_confidence(raw_peak: float, ref: float = 5e5) -> float:
    """Map raw cross-correlation peak to 0..1 confidence.

    ref is empirically chosen as the upper end of strong matches seen on
    the four reference studies. A normalized confidence of 1.0 means
    "as good as the strongest matches we've seen"; 0.2 means "the template
    matched, but weakly."
    """
    return float(max(0.0, min(1.0, raw_peak / ref)))


# ============================================================================
# Axial marker placement
# ============================================================================

def _place_cord_marker(item: dict, ps_mm: float, template_w: np.ndarray,
                        win: np.ndarray, idx: int) -> Optional[dict]:
    """Place a cord_center marker on a single axial slice via k-space.

    Returns a marker dict, or None if the slice geometry doesn't permit
    k-space matching (e.g. canal too close to image edge).
    """
    img = item['img']
    H, W = img.shape
    try:
        row0, col0 = pix_from_patient_xy(item, 0.0, 0.0)
    except Exception:
        return None
    r0 = int(round(row0)); c0 = int(round(col0))
    half = ROI_SIZE_PX // 2
    r1 = max(0, r0 - half); r2 = min(H, r0 + half)
    c1 = max(0, c0 - half); c2 = min(W, c0 + half)
    if (r2 - r1) < ROI_SIZE_PX or (c2 - c1) < ROI_SIZE_PX:
        return None

    canal = img[r1:r2, c1:c2].astype(np.float32)
    canal_w = canal * win
    corr = _normalized_cross_correlation_freq(canal_w, template_w)
    py, px = _subpixel_peak(corr)
    cord_row = r1 + py
    cord_col = c1 + px

    xyz = patient_xy_from_pix(item, cord_row, cord_col)
    intensity = _sample_intensity(img, (cord_row, cord_col), ps_mm, radius_mm=1.0)
    confidence = _normalize_confidence(float(corr.max()))

    return {
        'type':       'cord_center',
        'xyz_mm':     [float(xyz[0]), float(xyz[1]), float(xyz[2])],
        'intensity':  intensity,
        'confidence': confidence,
        'slice_inst': int(item['inst']),
        'slice_idx':  int(idx),
        # Internal: pixel-coordinate cord position, used by sibling markers
        '_pix_rc':    (float(cord_row), float(cord_col)),
    }


def _place_csf_marker(item: dict, ps_mm: float, cord_rc: tuple,
                       direction: str, idx: int,
                       search_max_mm: float = 12.0) -> Optional[dict]:
    """Place a CSF marker by walking from cord_rc in a direction until
    we find the local maximum pixel along that ray.

    direction: 'anterior', 'posterior', 'left', 'right' in patient frame.
    """
    img = item['img']
    H, W = img.shape
    smooth = gaussian_filter(img.astype(np.float32), sigma=1.0)
    cy, cx = cord_rc
    iop = item['iop']

    # Patient-frame direction -> pixel direction.
    # IOP row vector = patient direction of an image-row step (cols increase)
    # IOP col vector = patient direction of an image-col step (rows increase)
    row_dir = np.array(iop[0:3])  # x patient unit per col-pixel
    col_dir = np.array(iop[3:6])  # y patient unit per row-pixel

    # Target patient-frame direction
    if direction == 'anterior':
        target = np.array([0.0, -1.0, 0.0])    # -y is anterior in DICOM
    elif direction == 'posterior':
        target = np.array([0.0,  1.0, 0.0])    # +y is posterior
    elif direction == 'left':                  # patient left
        target = np.array([1.0,  0.0, 0.0])    # +x is patient left
    elif direction == 'right':
        target = np.array([-1.0, 0.0, 0.0])
    else:
        return None

    # Decompose target into image-row and image-col components.
    # Walking 1 px in col direction moves patient by row_dir * ps[1]
    # Walking 1 px in row direction moves patient by col_dir * ps[0]
    # Step toward target -> solve for (drow, dcol) step.
    A = np.column_stack([col_dir, row_dir])  # 3x2 matrix; cols -> rows, rows -> cols
    # We want A @ [drow_mm, dcol_mm] ~= target
    # Solve least-squares; project to (drow, dcol) in mm
    step_mm, *_ = np.linalg.lstsq(A, target, rcond=None)
    drow_mm, dcol_mm = float(step_mm[0]), float(step_mm[1])
    # Normalize so total step is 0.5 mm
    norm = np.hypot(drow_mm, dcol_mm)
    if norm < 1e-6:
        return None
    drow_px = (drow_mm / norm) * 0.5 / ps_mm
    dcol_px = (dcol_mm / norm) * 0.5 / ps_mm

    n_steps = int(search_max_mm / 0.5)
    best_val = -np.inf
    best_step = 0
    best_rc = (cy, cx)
    for s in range(1, n_steps + 1):
        rr = cy + drow_px * s
        cc = cx + dcol_px * s
        ri = int(round(rr)); ci = int(round(cc))
        if not (0 <= ri < H and 0 <= ci < W):
            break
        val = smooth[ri, ci]
        if val > best_val:
            best_val = float(val)
            best_step = s
            best_rc = (float(rr), float(cc))

    if best_step == 0:
        return None

    xyz = patient_xy_from_pix(item, best_rc[0], best_rc[1])
    intensity = _sample_intensity(item['img'], best_rc, ps_mm, radius_mm=0.5)

    # Confidence: how distinct is the local max from the cord-anchor mean
    # along this ray? Higher contrast -> higher confidence.
    cord_val = float(smooth[int(round(cy)), int(round(cx))])
    contrast = (best_val - cord_val) / max(best_val + cord_val, 1.0)
    confidence = float(max(0.0, min(1.0, contrast)))

    return {
        'type':       f'csf_{direction}',
        'xyz_mm':     [float(xyz[0]), float(xyz[1]), float(xyz[2])],
        'intensity':  intensity,
        'confidence': confidence,
        'slice_inst': int(item['inst']),
        'slice_idx':  int(idx),
        '_pix_rc':    best_rc,
        '_step_from_cord_mm': float(best_step * 0.5),
    }


def _place_canal_wall_marker(item: dict, ps_mm: float, csf_marker: dict,
                              cord_rc: tuple, direction: str, idx: int,
                              search_max_mm: float = 8.0) -> Optional[dict]:
    """Place a canal_wall marker by walking from the CSF marker outward in
    the same direction until the intensity drops below half the CSF intensity
    (the transition to dark bone or ligament).
    """
    img = item['img']
    H, W = img.shape
    smooth = gaussian_filter(img.astype(np.float32), sigma=1.0)
    cy, cx = cord_rc
    csy, csx = csf_marker['_pix_rc']
    csf_val = float(smooth[int(round(csy)), int(round(csx))])

    # Direction unit vector in pixel coords from cord to csf, then continue
    dr = csy - cy; dc = csx - cx
    norm = np.hypot(dr, dc)
    if norm < 1e-6:
        return None
    dr /= norm; dc /= norm

    threshold = csf_val * 0.5
    n_steps = int(search_max_mm / (0.5 * ps_mm))
    # Start walking 0.5 mm beyond csf marker
    start_step = int(0.5 / (0.5 * ps_mm)) + 1  # 0.5 mm in steps of 0.5 pixel
    wall_rc = None
    last_val = csf_val
    val_before_drop = csf_val  # value at the last bright pixel before transition
    val_at_wall = csf_val
    for s in range(start_step, n_steps + 1):
        rr = csy + dr * s * 0.5  # 0.5 px per step
        cc = csx + dc * s * 0.5
        ri = int(round(rr)); ci = int(round(cc))
        if not (0 <= ri < H and 0 <= ci < W):
            break
        val = smooth[ri, ci]
        if val < threshold:
            wall_rc = (float(rr), float(cc))
            val_at_wall = float(val)
            val_before_drop = float(last_val)
            break
        last_val = val

    if wall_rc is None:
        return None

    xyz = patient_xy_from_pix(item, wall_rc[0], wall_rc[1])
    intensity = _sample_intensity(item['img'], wall_rc, ps_mm, radius_mm=0.5)

    # Confidence: normalized intensity drop across the transition.
    # A real bone/ligament wall produces a sharp drop from CSF-bright to
    # bone-dark; a weak or no boundary produces a small drop. The drop is
    # normalized to (csf_val + wall_val) so the metric is scale-invariant -
    # equal on any scanner regardless of absolute intensity scale.
    drop = val_before_drop - val_at_wall
    confidence = float(max(0.0, min(1.0,
                                      drop / max(val_before_drop + val_at_wall, 1.0)
                                      * 2.0)))  # *2 so a perfect step -> 1.0

    # Distance from CSF marker to wall, in mm
    step_mm = float(np.hypot(wall_rc[0] - csy, wall_rc[1] - csx) * ps_mm)

    return {
        'type':       f'canal_wall_{direction}',
        'xyz_mm':     [float(xyz[0]), float(xyz[1]), float(xyz[2])],
        'intensity':  intensity,
        'confidence': confidence,
        'slice_inst': int(item['inst']),
        'slice_idx':  int(idx),
        '_pix_rc':    wall_rc,
        '_step_from_csf_mm': step_mm,
    }


# ============================================================================
# Volume-level marker placement (the public API)
# ============================================================================

def place_markers_axial(ax_items_sorted: list) -> List[Dict[str, Any]]:
    """Place all 9 axial marker types on every slice of an axial volume.

    Markers are returned in slice order. Markers that cannot be placed
    (e.g., k-space ROI clipped by image edge) are absent - there is no
    placeholder, no zero-fill, no recovery. Absence is a real outcome.
    """
    if not ax_items_sorted:
        return []
    ps_mm = float(ax_items_sorted[0]['ps'][0])
    win = np.outer(np.hanning(ROI_SIZE_PX), np.hanning(ROI_SIZE_PX))
    template = _build_cord_template(ROI_SIZE_PX, ps_mm)
    template_w = template * win

    markers = []
    for idx, it in enumerate(ax_items_sorted):
        # 1. Cord - k-space template
        cord_m = _place_cord_marker(it, ps_mm, template_w, win, idx)
        if cord_m is None:
            continue
        markers.append(cord_m)
        cord_rc = cord_m['_pix_rc']

        # 2. CSF markers - walks outward from cord
        csf_markers = {}
        for direction in ['anterior', 'posterior', 'left', 'right']:
            m = _place_csf_marker(it, ps_mm, cord_rc, direction, idx)
            if m is not None:
                markers.append(m)
                csf_markers[direction] = m

        # 3. Canal wall markers - walks outward from CSF
        for direction in ['anterior', 'posterior', 'left', 'right']:
            if direction not in csf_markers:
                continue
            m = _place_canal_wall_marker(it, ps_mm, csf_markers[direction],
                                          cord_rc, direction, idx)
            if m is not None:
                markers.append(m)

    return markers


def place_markers_sagittal(sag_items: list) -> List[Dict[str, Any]]:
    """Place sagittal markers: cord_centerline, vert_body_anterior/posterior,
    disc_center, spinous_tip.

    k7.0 implementation: cord_centerline only. Vertebral body and disc
    markers are scoped for k7.0 but deferred to the next iteration so we
    can validate the axial side first.
    """
    if not sag_items:
        return []

    # Find the midline sagittal slice (closest to patient x=0)
    mid_idx = int(np.argmin([abs(it['ipp'][0]) for it in sag_items]))
    sag_mid = sag_items[mid_idx]
    img = sag_mid['img']
    ps_row, ps_col = float(sag_mid['ps'][0]), float(sag_mid['ps'][1])
    H, W = img.shape
    smooth = gaussian_filter(img.astype(np.float32), sigma=1.0)

    # Sagittal coordinate frame: rows index y (anterior-posterior), cols
    # index z (cranio-caudal) - depends on IOP. Use the IOP-aware path.
    markers = []
    iop = sag_mid['iop']
    ipp = sag_mid['ipp']
    row_dir = np.array(iop[0:3]) * ps_col
    col_dir = np.array(iop[3:6]) * ps_row

    # The sagittal slice's row/col axes correspond to two patient-frame
    # directions, and which direction carries z (cranio-caudal) depends on
    # the IOP. Detect it from the iop col vector's z-component magnitude.
    z_step_mm = 1.0  # one marker per ~1 mm of z
    #
    # col_dir is the patient-direction of a 1-row pixel step; row_dir is
    # the patient-direction of a 1-col pixel step. If |col_dir[z]| >
    # |row_dir[z]|, rows index z; otherwise cols index z.
    if abs(col_dir[2]) > abs(row_dir[2]):
        # Rows index z. Iterate rows, place one marker per ~1mm of z.
        z_axis_size = H
        z_axis_step = abs(col_dir[2])  # mm per row-pixel
        def coord_for(z_idx, other_idx):
            return ipp + other_idx * row_dir + z_idx * col_dir
        def column_for(z_idx):
            # An axial slice through the sagittal image at this z-row
            # is one row of the image
            return smooth[z_idx, :]
        def index_axes(z_idx, other_idx):
            return z_idx, other_idx
    else:
        # Columns index z. Iterate columns, place one marker per ~1mm of z.
        z_axis_size = W
        z_axis_step = abs(row_dir[2])  # mm per col-pixel
        def coord_for(z_idx, other_idx):
            return ipp + z_idx * row_dir + other_idx * col_dir
        def column_for(z_idx):
            # An axial slice through the sagittal image at this z-col
            # is one column of the image
            return smooth[:, z_idx]
        def index_axes(z_idx, other_idx):
            return other_idx, z_idx

    if z_axis_step < 1e-6:
        # Can't make sense of geometry - skip sagittal markers
        return markers

    # Iterate the z-axis at ~1mm steps
    step_n = max(1, int(round(z_step_mm / z_axis_step)))

    # Establish "what counts as signal" floor for the whole sagittal image
    image_signal_ref = float(np.percentile(smooth, 95))
    if image_signal_ref < 1.0:
        return markers

    for z_idx in range(0, z_axis_size, step_n):
        column = column_for(z_idx)
        col_peak = float(column.max())
        if col_peak < image_signal_ref * 0.2:
            continue

        # Cord band: middle 50% of the OTHER axis (the AP direction)
        other_size = len(column)
        ymask = np.zeros(other_size, dtype=bool)
        ymask[other_size // 4: 3 * other_size // 4] = True
        sub = column.copy()
        sub[~ymask] = 0
        peak_idx = int(np.argmax(sub))
        peak_val = float(sub[peak_idx])
        if peak_val < image_signal_ref * 0.2:
            continue

        col_baseline = float(np.median(column[ymask]))
        if peak_val < col_baseline * 1.5:
            continue

        # Subpixel refinement
        if 1 <= peak_idx < other_size - 1:
            y0, y1, y2 = column[peak_idx - 1], column[peak_idx], column[peak_idx + 1]
            denom = (y0 - 2 * y1 + y2)
            dy = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-10 else 0.0
            dy = max(-1.0, min(1.0, dy))
        else:
            dy = 0.0
        sub_other = peak_idx + dy

        xyz = coord_for(z_idx, sub_other)
        intensity = peak_val
        prom = (intensity - col_baseline) / max(intensity + col_baseline, 1.0)
        confidence = float(max(0.0, min(1.0, prom)))

        # Image-pixel coords for downstream use
        row_idx, col_idx = index_axes(z_idx, sub_other)

        markers.append({
            'type':       'cord_centerline',
            'xyz_mm':     [float(xyz[0]), float(xyz[1]), float(xyz[2])],
            'intensity':  intensity,
            'confidence': confidence,
            'slice_inst': int(sag_mid['inst']),
            'slice_idx':  int(mid_idx),
        })

    return markers


def analyze_cspine_k7(ax_items_sorted: list, sag_items: list) -> Dict[str, Any]:
    """Run k7 on a cervical spine study.

    Output is a single dict with the marker list and provenance.
    No status, no flags, no severity.
    """
    axial_markers = place_markers_axial(ax_items_sorted)
    sagittal_markers = place_markers_sagittal(sag_items)

    # Strip private fields from output
    def public(m):
        return {k: v for k, v in m.items() if not k.startswith('_')}

    return {
        'algorithm_version': 'k7',
        'measurement_only':  True,
        'n_axial_slices':    len(ax_items_sorted),
        'n_sagittal_slices': len(sag_items),
        'markers':           [public(m) for m in axial_markers + sagittal_markers],
        'n_markers':         len(axial_markers) + len(sagittal_markers),
        'marker_counts_by_type': _count_by_type(axial_markers + sagittal_markers),
    }


def _count_by_type(markers: list) -> dict:
    counts = {}
    for m in markers:
        t = m['type']
        counts[t] = counts.get(t, 0) + 1
    return counts
