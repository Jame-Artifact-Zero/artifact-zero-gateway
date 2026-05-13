"""
measure_spine.py — Cord / Canal / Disc-Spacing Measurements
============================================================
Input:
    - SliceFacts CSV produced by data_extract.py
    - The original DICOM zip (for pixel data)

Output:
    - cord_canal_per_slice.csv   : one row per axial T2 slice
    - disc_spacing.csv           : one row per adjacent-slice pair

Method:
    Per axial T2 slice (orient_axial_align > 0.85):
      1. Read pixel array from DICOM file
      2. Build very_bright mask (img >= p90 from SliceFacts)
      3. Find the connected component closest to patient (0,0) within
         CORD_BOX_MM, with size 50-3000 px. This is the cord+CSF complex.
      4. Cord = dimmer half of complex (img <= complex_p50).
         CSF  = brighter half (img > complex_p50).
      5. Canal walls: from cord centroid, walk outward in 4 patient-frame
         directions. Step through the cord, through the CSF (very_bright),
         and stop at the first non-very_bright pixel = canal wall.
      6. csf_space_N = distance from cord boundary to canal wall in dir N.
      Disc spacing = z-distance between consecutive cord centroids.

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

import numpy as np
import pydicom
from scipy.ndimage import label, binary_erosion

# ── tunables ────────────────────────────────────────────────────────────────
CORD_BOX_X_MM       = 12.0  # patient x: complex must be within this of anchor
# Asymmetric y range: vertebra is anterior to cord; cord lives posterior.
# In LPS patient coords, posterior = +y. So allow more +y range than -y.
CORD_BOX_Y_MIN_MM   = -4.0   # anterior of anchor
CORD_BOX_Y_MAX_MM   = 18.0   # posterior of anchor
COMPLEX_MIN_MM2     = 6.0    # cord+CSF complex min area in mm²
COMPLEX_MAX_MM2     = 400.0  # cord+CSF complex max area in mm²
COMPLEX_MAX_ASPECT  = 3.0   # bbox aspect ratio cap
# Centroid validation uses the same asymmetric range:
CORD_CENTROID_X_LIMIT_MM    = 6.0    # cord centroid in x: ±6 from anchor
CORD_CENTROID_Y_MIN_MM      = -4.0   # cord centroid in y (anterior of anchor)
CORD_CENTROID_Y_MAX_MM      = 18.0   # cord centroid in y (posterior of anchor)
AXIAL_ALIGN_MIN     = 0.85  # orient_axial_align threshold
SERIES_T2_KEYWORDS  = ['t2', 'tse']        # must contain at least one
SERIES_AX_KEYWORDS  = ['ax', 'axial']      # must contain at least one
SERIES_EXCLUDE      = ['sag', 'cor', 'stir', 'flair', 'dwi', '3d',
                        't2*', 't2 *', 'merge', 'gre', 'medic',
                        't1']
WALK_MAX_MM         = 30.0  # max ray-walk distance from centroid


# ── helpers ──────────────────────────────────────────────────────────────────

def load_slice_facts(csv_path: str) -> list[dict]:
    with open(csv_path, newline='') as f:
        return list(csv.DictReader(f))


def is_axial_t2(row: dict) -> bool:
    if float(row.get('orient_axial_align', 0)) < AXIAL_ALIGN_MIN:
        return False
    desc = (row.get('series_description') or '').lower()
    # Must indicate T2 AND must indicate axial. Both required.
    if not any(k in desc for k in SERIES_T2_KEYWORDS):
        return False
    if not any(k in desc for k in SERIES_AX_KEYWORDS):
        return False
    if any(k in desc for k in SERIES_EXCLUDE):
        return False
    return row.get('status') == 'OK'


def read_pixel_array(extracted_path: str) -> Optional[np.ndarray]:
    try:
        ds = pydicom.dcmread(extracted_path, force=True)
        img = np.asarray(ds.pixel_array, dtype=np.float32)
        if img.ndim == 3:
            img = img[0]
        slope     = float(getattr(ds, 'RescaleSlope',     1.0) or 1.0)
        intercept = float(getattr(ds, 'RescaleIntercept', 0.0) or 0.0)
        if slope != 1.0 or intercept != 0.0:
            img = img * slope + intercept
        return img
    except Exception:
        return None


def _pixel_to_patient_ds(
    r: float, c: float,
    ipp: list[float], row_dir: list[float], col_dir: list[float],
    ps_row: float, ps_col: float,
) -> tuple[float, float, float]:
    x = ipp[0] + c * ps_col * row_dir[0] + r * ps_row * col_dir[0]
    y = ipp[1] + c * ps_col * row_dir[1] + r * ps_row * col_dir[1]
    z = ipp[2] + c * ps_col * row_dir[2] + r * ps_row * col_dir[2]
    return x, y, z


def _patient_center_pixel(
    ipp: list[float], row_dir: list[float], col_dir: list[float],
    ps_row: float, ps_col: float,
) -> tuple[float, float]:
    """Return (row, col) of patient (0,0,0) in this slice's image plane."""
    A = np.array([
        [ps_col * row_dir[0], ps_row * col_dir[0]],
        [ps_col * row_dir[1], ps_row * col_dir[1]],
    ])
    b = np.array([-ipp[0], -ipp[1]])
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        return float(sol[1]), float(sol[0])  # (row, col)
    except np.linalg.LinAlgError:
        return 0.0, 0.0


def _find_best_complex_at_threshold(
    img: np.ndarray,
    thresh: float,
    ipp: list[float],
    row_dir: list[float],
    col_dir: list[float],
    ps_row: float,
    ps_col: float,
    anchor_x_mm: float,
    anchor_y_mm: float,
) -> Optional[tuple]:
    """At a given intensity threshold, return (label_id, lbls, complex_size_mm2,
    distance_from_anchor) for the best cord+CSF complex candidate, or None.
    """
    vbright_mask = img >= thresh
    lbls, n = label(vbright_mask)
    if n == 0:
        return None

    sizes = np.bincount(lbls.ravel())
    sizes[0] = 0

    best_lbl = -1
    best_dist_mm = 1e9
    best_area_mm2 = 0.0
    px_area_mm2 = ps_row * ps_col
    complex_min_px = int(COMPLEX_MIN_MM2 / px_area_mm2)
    complex_max_px = int(COMPLEX_MAX_MM2 / px_area_mm2)
    for lbl_id in range(1, n + 1):
        sz = int(sizes[lbl_id])
        if sz < complex_min_px or sz > complex_max_px:
            continue
        m = lbls == lbl_id
        ys, xs = np.where(m)
        bbox_h_mm = (int(ys.max()) - int(ys.min()) + 1) * ps_row
        bbox_w_mm = (int(xs.max()) - int(xs.min()) + 1) * ps_col
        if min(bbox_h_mm, bbox_w_mm) <= 0:
            continue
        aspect = max(bbox_h_mm, bbox_w_mm) / min(bbox_h_mm, bbox_w_mm)
        if aspect > COMPLEX_MAX_ASPECT:
            continue
        cr, cc = float(ys.mean()), float(xs.mean())
        cx, cy, _ = _pixel_to_patient_ds(cr, cc, ipp, row_dir, col_dir, ps_row, ps_col)
        dx = cx - anchor_x_mm
        dy = cy - anchor_y_mm
        if abs(dx) > CORD_BOX_X_MM or dy < CORD_BOX_Y_MIN_MM or dy > CORD_BOX_Y_MAX_MM:
            continue
        # Pick the LARGEST candidate (not the closest), among those within
        # the search box. Real cord+CSF is the biggest near-circular bright
        # blob in this region — fragments are smaller.
        area_mm2_here = sz * ps_row * ps_col
        if area_mm2_here > best_area_mm2:
            best_area_mm2 = area_mm2_here
            best_dist_mm = float(np.hypot(dx, dy))
            best_lbl = lbl_id

    if best_lbl < 0:
        return None
    return (best_lbl, lbls, best_area_mm2, best_dist_mm)


def find_cord(
    img: np.ndarray,
    row_dict: dict,
    ipp: list[float],
    row_dir: list[float],
    col_dir: list[float],
    ps_row: float,
    ps_col: float,
    anchor_x_mm: float = 0.0,
    anchor_y_mm: float = 0.0,
) -> Optional[dict]:
    """Find cord by:
      1. Build very_bright mask using adaptive threshold (sweep p90 → p85 → p80
         → p75 → p70) until a cord+CSF complex of >= COMPLEX_TARGET_MM2 emerges
         near the anchor
      2. Find connected components in size range COMPLEX_MIN_MM2-COMPLEX_MAX_MM2
      3. Pick the one closest to the anatomic anchor within CORD_BOX_X/Y_MM
      4. Inside it, cord = pixels with img <= complex_p50

    The threshold sweep accommodates scanner-specific intensity normalization.
    On Philips T2 the cord+CSF complex emerges at p90 (matching slice_facts).
    On GE T2 FSE the same anatomy emerges at p80-p85 because the intensity
    distribution has more mass in the high band.
    """
    # Adaptive threshold sweep. Start at p90 (cleanest separation when it works)
    # and lower if the resulting complex is too small.
    p_grid = (90.0, 85.0, 80.0, 75.0, 70.0)
    target_mm2 = 15.0   # complex must be at least this big to be a real complex
    best_threshold_result = None
    chosen_threshold = None
    for pct in p_grid:
        thresh = float(np.percentile(img, pct))
        result = _find_best_complex_at_threshold(
            img, thresh, ipp, row_dir, col_dir, ps_row, ps_col,
            anchor_x_mm, anchor_y_mm,
        )
        if result is None:
            continue
        best_lbl, lbls, area_mm2, dist_mm = result
        # Accept the first threshold that produces a complex >= target size
        if area_mm2 >= target_mm2:
            best_threshold_result = result
            chosen_threshold = pct
            break
        # Otherwise remember it as fallback (in case nothing better appears)
        if best_threshold_result is None or area_mm2 > best_threshold_result[2]:
            best_threshold_result = result
            chosen_threshold = pct

    if best_threshold_result is None:
        return None
    best_lbl, lbls, _, _ = best_threshold_result

    complex_mask = lbls == best_lbl
    complex_intensities = img[complex_mask]
    complex_p50 = float(np.percentile(complex_intensities, 50))

    # Cord = dimmer half of complex. Use the raw threshold mask. The
    # interior-point selection below (via distance transform) handles any
    # boundary speckle naturally — we don't need morphological closing.
    cord_raw = complex_mask & (img <= complex_p50)
    if not cord_raw.any():
        return None
    # Take only the largest connected sub-component (drop satellites)
    sub_lbls, sub_n = label(cord_raw)
    if sub_n == 0:
        return None
    sub_sizes = np.bincount(sub_lbls.ravel())
    sub_sizes[0] = 0
    cord_mask = sub_lbls == int(np.argmax(sub_sizes))

    cord_ys, cord_xs = np.where(cord_mask)
    cr_centroid = float(cord_ys.mean())
    cc_centroid = float(cord_xs.mean())

    # Validation: cord centroid (in patient mm) must be within the asymmetric
    # bounds of the anchor. Cord sits posterior to the vertebra (the anchor).
    cx_cent, cy_cent, cz_cent = _pixel_to_patient_ds(
        cr_centroid, cc_centroid, ipp, row_dir, col_dir, ps_row, ps_col
    )
    dx_cent = cx_cent - anchor_x_mm
    dy_cent = cy_cent - anchor_y_mm
    if (abs(dx_cent) > CORD_CENTROID_X_LIMIT_MM
        or dy_cent < CORD_CENTROID_Y_MIN_MM
        or dy_cent > CORD_CENTROID_Y_MAX_MM):
        return {'__invalid__': True,
                'reason': f'cord centroid off-anchor: ({cx_cent:.1f},{cy_cent:.1f})mm '
                           f'vs anchor ({anchor_x_mm:.1f},{anchor_y_mm:.1f}) '
                           f'Δ=({dx_cent:+.1f},{dy_cent:+.1f}) outside '
                           f'x±{CORD_CENTROID_X_LIMIT_MM} y[{CORD_CENTROID_Y_MIN_MM}..{CORD_CENTROID_Y_MAX_MM}]'}

    # Walk start = cord pixel closest to centroid.
    d2 = (cord_ys - cr_centroid) ** 2 + (cord_xs - cc_centroid) ** 2
    nearest = int(np.argmin(d2))
    cr = float(cord_ys[nearest])
    cc = float(cord_xs[nearest])
    cx, cy, cz = _pixel_to_patient_ds(cr, cc, ipp, row_dir, col_dir, ps_row, ps_col)

    cord_area_mm2 = float(cord_mask.sum()) * ps_row * ps_col
    complex_area_mm2 = float(complex_mask.sum()) * ps_row * ps_col

    return {
        'centroid_row':       cr,
        'centroid_col':       cc,
        'centroid_x_mm':      cx,
        'centroid_y_mm':      cy,
        'centroid_z_mm':      cz,
        'cord_area_mm2':      cord_area_mm2,
        'complex_area_mm2':   complex_area_mm2,
        'complex_p50':        complex_p50,
        'cord_mask':          cord_mask,
        'complex_mask':       complex_mask,
        'threshold_pct':      chosen_threshold,
    }


def measure_csf_space(
    cord: dict,
    img: np.ndarray,
    row_dict: dict,
    ipp: list[float],
    row_dir: list[float],
    col_dir: list[float],
    ps_row: float,
    ps_col: float,
) -> dict[str, Optional[float]]:
    """Walk outward from cord centroid in 4 patient-frame directions.
    For each ray:
      1. Step through cord (img <= complex_p50)         → cord_radius_dir
      2. Step through CSF  (img >  complex_p50 AND very_bright)
      3. Stop at first non-very_bright pixel             → wall_distance_dir
    Return wall_distance - cord_radius per direction (CSF gap from cord edge
    to canal wall).
    """
    H, W = img.shape
    # Use the same percentile threshold that the cord-finder selected for
    # this slice, so the wall-detection criterion matches the complex
    # definition. Fall back to p90 from SliceFacts if not provided.
    thresh_pct = cord.get('threshold_pct')
    if thresh_pct is not None:
        thresh = float(np.percentile(img, thresh_pct))
    else:
        thresh = float(row_dict['i_p90'])
    vbright = img >= thresh
    cord_mask = cord['cord_mask']

    cr = cord['centroid_row']
    cc = cord['centroid_col']

    step_mm = 0.5
    max_steps = int(WALK_MAX_MM / step_mm)

    def patient_vec_to_pixel_step(dx_pat, dy_pat):
        """Convert a 1mm patient-frame direction to a pixel-step (dr, dc)
        of length step_mm in patient mm."""
        A = np.array([
            [ps_col * row_dir[0], ps_row * col_dir[0]],
            [ps_col * row_dir[1], ps_row * col_dir[1]],
        ])
        b = np.array([dx_pat, dy_pat])
        try:
            sol, *_ = np.linalg.lstsq(A, b, rcond=None)
            dc_unit, dr_unit = float(sol[0]), float(sol[1])
        except np.linalg.LinAlgError:
            return 0.0, 0.0
        # Length in patient mm of one (dr_unit, dc_unit) move:
        mag_mm = np.hypot(dc_unit * ps_col, dr_unit * ps_row)
        if mag_mm < 1e-6:
            return 0.0, 0.0
        scale = step_mm / mag_mm
        return dr_unit * scale, dc_unit * scale

    directions = {
        'anterior':  ( 0.0,  1.0),   # +y patient
        'posterior': ( 0.0, -1.0),   # -y patient
        'left':      ( 1.0,  0.0),   # +x patient (patient left is +x in LPS)
        'right':     (-1.0,  0.0),   # -x patient
    }

    result: dict[str, Optional[float]] = {}

    for name, (dx, dy) in directions.items():
        dr, dc = patient_vec_to_pixel_step(dx, dy)
        if abs(dr) < 1e-6 and abs(dc) < 1e-6:
            result[f'cord_radius_{name}_mm'] = None
            result[f'wall_dist_{name}_mm']   = None
            result[f'csf_space_{name}_mm']   = None
            continue

        # Phase 1: walk through cord. Record cord_radius = distance until we
        # exit the cord mask (first step outside cord_mask).
        cur_r, cur_c = cr, cc
        cord_radius_mm = 0.0
        exited_cord = False
        for _ in range(max_steps):
            cur_r += dr
            cur_c += dc
            ir, ic = int(round(cur_r)), int(round(cur_c))
            if ir < 0 or ir >= H or ic < 0 or ic >= W:
                break
            if not cord_mask[ir, ic]:
                exited_cord = True
                break
            cord_radius_mm += step_mm

        if not exited_cord:
            # Ray never exited cord — bad data, skip
            result[f'cord_radius_{name}_mm'] = None
            result[f'wall_dist_{name}_mm']   = None
            result[f'csf_space_{name}_mm']   = None
            continue

        # Phase 2: walk through CSF (still very_bright) until first non-very_bright
        wall_dist_mm = cord_radius_mm
        hit_wall = False
        for _ in range(max_steps):
            cur_r += dr
            cur_c += dc
            ir, ic = int(round(cur_r)), int(round(cur_c))
            if ir < 0 or ir >= H or ic < 0 or ic >= W:
                break
            wall_dist_mm += step_mm
            if not vbright[ir, ic]:
                hit_wall = True
                break

        csf_space_mm = wall_dist_mm - cord_radius_mm if hit_wall else None

        result[f'cord_radius_{name}_mm'] = round(cord_radius_mm, 3)
        result[f'wall_dist_{name}_mm']   = round(wall_dist_mm, 3) if hit_wall else None
        result[f'csf_space_{name}_mm']   = round(csf_space_mm, 3) if csf_space_mm is not None else None

    return result


# ── main ─────────────────────────────────────────────────────────────────────

def run(slice_facts_csv: str, out_dir: str = '.') -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_slice_facts(slice_facts_csv)
    axial_t2 = [r for r in rows if is_axial_t2(r)]
    axial_t2.sort(key=lambda r: float(r['z_position_mm']))

    print(f'SliceFacts total rows : {len(rows)}')
    print(f'Axial T2 slices found : {len(axial_t2)}')

    # Anatomic anchor (x, y) for cord-finding. Default: patient origin.
    # If a sagittal vertebrae CSV is available, use the median vertebra
    # centroid (x, y) as the anchor. On scans where the patient is not at
    # iso-center, the cord/canal is far from (0,0) but close to the vertebra
    # column.
    anchor_x_mm, anchor_y_mm = 0.0, 0.0
    vert_csv = _find_vertebrae_csv(slice_facts_csv)
    if vert_csv:
        xs, ys = [], []
        with open(vert_csv) as vf:
            for r in csv.DictReader(vf):
                try:
                    xs.append(float(r['centroid_x_mm']))
                    ys.append(float(r['centroid_y_mm']))
                except (KeyError, ValueError):
                    continue
        if xs:
            anchor_x_mm = float(np.median(xs))
            anchor_y_mm = float(np.median(ys))
            print(f'Cord anchor from {vert_csv.name}: '
                  f'({anchor_x_mm:+.2f}, {anchor_y_mm:+.2f}) mm')

    cord_canal_rows = []

    for row in axial_t2:
        inst = row.get('instance_number', '?')
        z_mm = float(row['z_position_mm'])
        path = row['extracted_path']

        img = read_pixel_array(path)
        if img is None:
            print(f'  inst {inst}: pixel read failed, skipping')
            continue

        try:
            ds      = pydicom.dcmread(path, force=True)
            iop     = ds.ImageOrientationPatient
            ipp_raw = ds.ImagePositionPatient
            row_dir = [float(iop[i]) for i in range(3)]
            col_dir = [float(iop[i]) for i in range(3, 6)]
            ipp     = [float(ipp_raw[i]) for i in range(3)]
        except Exception as e:
            print(f'  inst {inst}: geometry read failed ({e}), skipping')
            continue

        ps_row = float(row['pixel_spacing_row_mm'])
        ps_col = float(row['pixel_spacing_col_mm'])

        cord = find_cord(img, row, ipp, row_dir, col_dir, ps_row, ps_col,
                         anchor_x_mm=anchor_x_mm, anchor_y_mm=anchor_y_mm)

        is_valid_cord = (cord is not None) and (not cord.get('__invalid__', False))
        out_row = {
            'instance_number':        inst,
            'z_mm':                   round(z_mm, 3),
            'series_description':     row.get('series_description', ''),
            'cord_found':             is_valid_cord,
            'cord_confidence':        '',
            'status_detail':          '',
            'cord_x_mm':              None,
            'cord_y_mm':              None,
            'cord_z_mm':              None,
            'cord_area_mm2':          None,
            'complex_area_mm2':       None,
            'csf_lr_ratio':           None,
            'csf_lr_flag':            '',
            'canal_flag':             '',
            'cord_radius_anterior_mm':  None,
            'cord_radius_posterior_mm': None,
            'cord_radius_left_mm':      None,
            'cord_radius_right_mm':     None,
            'wall_dist_anterior_mm':    None,
            'wall_dist_posterior_mm':   None,
            'wall_dist_left_mm':        None,
            'wall_dist_right_mm':       None,
            'csf_space_anterior_mm':    None,
            'csf_space_posterior_mm':   None,
            'csf_space_left_mm':        None,
            'csf_space_right_mm':       None,
            'csf_space_min_mm':         None,
        }

        if cord and cord.get('__invalid__'):
            out_row['cord_confidence'] = 'INVALID'
            out_row['status_detail'] = cord.get('reason', '')
            # Treat as cord-not-found for downstream classification: it's not
            # a cord, it's a detector artifact. cord_found stays False.
            cord_canal_rows.append(out_row)
            print(f"  inst {inst:>3}  z={z_mm:+7.1f}mm  cord INVALID: {cord['reason']}")
            continue

        if cord:
            out_row.update({
                'cord_x_mm':       round(cord['centroid_x_mm'], 3),
                'cord_y_mm':       round(cord['centroid_y_mm'], 3),
                'cord_z_mm':       round(cord['centroid_z_mm'], 3),
                'cord_area_mm2':   round(cord['cord_area_mm2'], 2),
                'complex_area_mm2': round(cord['complex_area_mm2'], 2),
                'cord_confidence': 'HIGH' if cord['cord_area_mm2'] >= 50.0 else 'LOW',
            })
            csf = measure_csf_space(
                cord, img, row, ipp, row_dir, col_dir, ps_row, ps_col,
            )
            out_row.update(csf)
            csf_spaces = [
                csf.get(f'csf_space_{d}_mm')
                for d in ('anterior', 'posterior', 'left', 'right')
                if csf.get(f'csf_space_{d}_mm') is not None
            ]
            out_row['csf_space_min_mm'] = round(min(csf_spaces), 3) if csf_spaces else None

            # csf_lr_ratio: left / right. Flag if ratio > 2:1 either way.
            csf_l = csf.get('csf_space_left_mm')
            csf_r = csf.get('csf_space_right_mm')
            if csf_l is not None and csf_r is not None and csf_r > 0 and csf_l > 0:
                ratio = csf_l / csf_r
                out_row['csf_lr_ratio'] = round(ratio, 3)
                # Both at the measurement floor (0.5mm) = both tight, not 'symmetric'
                if csf_l <= 0.5 and csf_r <= 0.5:
                    out_row['csf_lr_flag'] = 'BOTH_TIGHT'
                elif ratio >= 2.0:
                    out_row['csf_lr_flag'] = 'L_BIASED'
                elif ratio <= 0.5:
                    out_row['csf_lr_flag'] = 'R_BIASED'
                else:
                    out_row['csf_lr_flag'] = 'SYM'
            elif csf_l is not None and csf_r is not None:
                if csf_l == 0 and csf_r == 0:
                    out_row['csf_lr_flag'] = 'BOTH_ZERO'
                elif csf_l == 0:
                    out_row['csf_lr_flag'] = 'R_BIASED'
                else:
                    out_row['csf_lr_flag'] = 'L_BIASED'

        cord_canal_rows.append(out_row)
        status = (
            f"cord=({out_row['cord_x_mm']},{out_row['cord_y_mm']}) "
            f"area={out_row['cord_area_mm2']} "
            f"csf_min={out_row['csf_space_min_mm']}"
            if cord else "cord NOT FOUND"
        )
        print(f'  inst {inst:>3}  z={z_mm:+7.1f}mm  {status}')

    # ── canal_flag (second pass) ─────────────────────────────────────────────
    # Fires when 2+ CONSECUTIVE slices are LOW confidence AND the cord_y
    # deviation between them exceeds 3mm. Both conditions must hold for the
    # flag to fire on a slice.
    #
    # Walk through cord_canal_rows in their existing z-order. For each pair
    # (i, i+1) where both are LOW and |Δcord_y| > 3mm, mark BOTH slices.
    # Extend the flagged run if subsequent LOW slices continue the cluster.
    for i in range(len(cord_canal_rows) - 1):
        a = cord_canal_rows[i]
        b = cord_canal_rows[i + 1]
        if a['cord_confidence'] != 'LOW' or b['cord_confidence'] != 'LOW':
            continue
        if a.get('cord_y_mm') is None or b.get('cord_y_mm') is None:
            continue
        dy = abs(float(b['cord_y_mm']) - float(a['cord_y_mm']))
        if dy > 3.0:
            a['canal_flag'] = 'ABNORMAL'
            b['canal_flag'] = 'ABNORMAL'

    # Anything that's LOW but didn't get ABNORMAL gets blank (not flagged).
    # HIGH slices and no-cord slices stay blank too.

    cord_canal_path = out_dir / 'cord_canal_per_slice.csv'
    _write_csv(cord_canal_rows, cord_canal_path)
    print(f'\nWrote {len(cord_canal_rows)} rows → {cord_canal_path}')

    # ── per-level severity classification ────────────────────────────────────
    # Requires a vertebrae CSV with columns: V_idx, centroid_z_mm.
    # We look for it next to slice_facts_csv first, then fall back to a
    # default sagittal-derived file in outputs/.
    vert_csv = _find_vertebrae_csv(slice_facts_csv)
    if vert_csv:
        level_rows = _classify_levels(cord_canal_rows, vert_csv)
        if level_rows:
            level_path = out_dir / 'level_severity.csv'
            _write_csv(level_rows, level_path)
            print(f'Wrote {len(level_rows)} rows → {level_path}')

    # ── disc spacing ─────────────────────────────────────────────────────────
    # Only adjacent pairs where BOTH slices have HIGH-confidence cord finds.
    # Adjacency is in z-order (already sorted at the top); pairs are
    # consecutive in cord_canal_rows after dropping non-found slices.
    found_rows = [r for r in cord_canal_rows if r['cord_found']]
    high_rows  = [r for r in found_rows if r['cord_confidence'] == 'HIGH']
    spacing_rows = []
    for i in range(len(high_rows) - 1):
        a = high_rows[i]
        b = high_rows[i + 1]
        dz = abs(float(b['z_mm']) - float(a['z_mm']))
        dx = float(b['cord_x_mm']) - float(a['cord_x_mm'])
        dy = float(b['cord_y_mm']) - float(a['cord_y_mm'])
        spacing_rows.append({
            'inst_inferior':             a['instance_number'],
            'inst_superior':             b['instance_number'],
            'z_inferior_mm':             a['z_mm'],
            'z_superior_mm':             b['z_mm'],
            'slice_spacing_mm':          round(dz, 3),
            'cord_x_inferior_mm':        a['cord_x_mm'],
            'cord_x_superior_mm':        b['cord_x_mm'],
            'cord_y_inferior_mm':        a['cord_y_mm'],
            'cord_y_superior_mm':        b['cord_y_mm'],
            'cord_drift_x_mm':           round(dx, 3),
            'cord_drift_y_mm':           round(dy, 3),
            'cord_area_inferior_mm2':    a['cord_area_mm2'],
            'cord_area_superior_mm2':    b['cord_area_mm2'],
            'csf_space_min_inferior_mm': a['csf_space_min_mm'],
            'csf_space_min_superior_mm': b['csf_space_min_mm'],
        })

    spacing_path = out_dir / 'disc_spacing.csv'
    _write_csv(spacing_rows, spacing_path)
    print(f'Wrote {len(spacing_rows)} rows → {spacing_path}')


def _find_vertebrae_csv(slice_facts_csv: str) -> Optional[Path]:
    """Look for a vertebrae CSV next to slice_facts_csv or in outputs/."""
    sf = Path(slice_facts_csv)
    candidates = [
        sf.parent / 'vertebrae.csv',
        sf.parent / 'vertebrae_sag_t2_cervical_inst8.csv',
        Path('/mnt/user-data/outputs/vertebrae_sag_t2_cervical_inst8.csv'),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _classify_levels(
    cord_canal_rows: list[dict],
    vert_csv: Path,
) -> list[dict]:
    """Per cervical/upper-thoracic level, classify central canal stenosis as
    NORMAL / MILD / MODERATE / SEVERE based on per-slice measurements.

    Rules:
      Posterior CSF space thresholds (mm) per slice:
        > 2.0  → NORMAL
        1.0-2.0 → MILD
        0.5-1.0 → MODERATE
        ≤ 0.5  → SEVERE

      Per-level severity rules:
        - canal_flag = ABNORMAL on 2+ slices at level → SEVERE
        - If no HIGH slices → UNCLASSIFIED
        - Otherwise use the BEST (least severe) posterior CSF among HIGH slices.
          A single bad slice doesn't define a level — the best representative
          slice does.
        - Asymmetry (L_BIASED or R_BIASED) on HIGH slices AND posterior CSF
          ≤ 1.0 → bump severity one step.
    """
    # Load vertebra centroids
    vertebrae = []
    with open(vert_csv) as f:
        for r in csv.DictReader(f):
            try:
                vertebrae.append({
                    'idx': int(r['V_idx']),
                    'z':   float(r['centroid_z_mm']),
                })
            except (KeyError, ValueError):
                continue
    if not vertebrae:
        return []

    # Cervical names. V1 = topmost detected vertebra. For our 6-vertebra
    # cervical scan: V1=C3, V2=C4, V3=C5, V4=C6, V5=C7, V6=T1.
    # If more or fewer vertebrae are detected, name them sequentially from
    # C3 downward (this matches the scan FOV).
    # Naming: vertebrae detected superior-to-inferior. The typical cervical
    # MRI FOV captures C3-T1 (7 vertebrae) or C3-T2 (8). C2 is often partly
    # outside or excluded. If 6 vertebrae: assume C3-T1 (with C7 or T1 cut).
    # If 7: assume C3-T1. If 8: assume C2-T1. If more, extend C2/T2/T3.
    name_seq = ['C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'T1', 'T2', 'T3']
    level_names = {}
    if len(vertebrae) <= 7:
        start = 1  # C3 at top
    else:
        start = 0  # C2 at top
    for i, v in enumerate(vertebrae):
        if start + i < len(name_seq):
            level_names[v['idx']] = name_seq[start + i]
        else:
            level_names[v['idx']] = f"L{v['idx']}"

    # Disc midpoints
    discs = []
    for i in range(len(vertebrae) - 1):
        a, b = vertebrae[i], vertebrae[i+1]
        discs.append({
            'name': f"{level_names[a['idx']]}-{level_names[b['idx']]}",
            'z':    (a['z'] + b['z']) / 2,
        })

    def assign_level(z: float) -> tuple[str, str]:
        """Return (level_name, kind) for an axial slice z. Pick whichever
        vertebra/disc centroid is closer."""
        best_name = None
        best_kind = None
        best_d = 1e9
        for v in vertebrae:
            d = abs(z - v['z'])
            if d < best_d:
                best_d = d
                best_name = level_names[v['idx']]
                best_kind = 'body'
        for disc in discs:
            d = abs(z - disc['z'])
            if d < best_d:
                best_d = d
                best_name = disc['name']
                best_kind = 'disc'
        return best_name, best_kind

    # Group rows by level
    from collections import defaultdict
    by_level = defaultdict(list)
    for r in cord_canal_rows:
        if not r['cord_found']:
            continue
        if r.get('z_mm') is None:
            continue
        name, kind = assign_level(float(r['z_mm']))
        by_level[(name, kind)].append(r)

    # Severity scoring
    SEV_RANK = {'NORMAL': 0, 'MILD': 1, 'MODERATE': 2, 'SEVERE': 3}
    SEV_NAME = {v: k for k, v in SEV_RANK.items()}

    def classify_posterior_csf(post_mm: float) -> int:
        if post_mm > 2.0:   return 0  # NORMAL
        if post_mm > 1.0:   return 1  # MILD
        if post_mm > 0.5:   return 2  # MODERATE
        return 3                       # SEVERE

    out_rows: list[dict] = []
    level_keys = list(by_level.keys())
    level_keys.sort(key=lambda k: -sum(float(r['z_mm']) for r in by_level[k]) / len(by_level[k]))

    for key in level_keys:
        name, kind = key
        slices = by_level[key]
        high = [r for r in slices if r['cord_confidence'] == 'HIGH']
        n_high = len(high)
        n_slices = len(slices)
        n_abnormal = sum(1 for r in slices if r.get('canal_flag') == 'ABNORMAL')
        n_asymmetric_high = sum(
            1 for r in high
            if r.get('csf_lr_flag') in ('L_BIASED', 'R_BIASED')
        )

        # Posterior CSF from HIGH slices
        post_vals = []
        for r in high:
            v = r.get('csf_space_posterior_mm')
            if v not in (None, ''):
                post_vals.append(float(v))

        # cord_area stats
        if len(high) >= 2:
            areas = [float(r['cord_area_mm2']) for r in high]
            area_mean = sum(areas) / len(areas)
            area_std = (sum((a - area_mean) ** 2 for a in areas) / len(areas)) ** 0.5
        elif len(high) == 1:
            areas = [float(high[0]['cord_area_mm2'])]
            area_mean = areas[0]
            area_std = 0.0
        else:
            area_mean = None
            area_std = None

        # Classification
        if n_abnormal >= 2:
            final_sev = 3
            sev_reason = f'canal_flag=ABNORMAL on {n_abnormal} slice(s)'
            best_post = None
        elif n_abnormal >= 1 and n_high == 0:
            # Single abnormal slice at a level with no HIGH-confidence slice =
            # detector failure cluster — likely stenosis preventing detection.
            final_sev = 3
            sev_reason = f'canal_flag=ABNORMAL on {n_abnormal} slice(s), no HIGH slices'
            best_post = None
        elif not post_vals:
            final_sev = None
            sev_reason = 'no HIGH-confidence slices'
            best_post = None
        else:
            # BEST posterior CSF (largest) sets the level.
            best_post = max(post_vals)
            final_sev = classify_posterior_csf(best_post)
            # A single HIGH slice at a level is insufficient to call SEVERE
            # by posterior CSF alone — 0.5mm posterior can be anatomic.
            # Cap single-slice levels at MODERATE.
            if n_high < 2 and final_sev == 3:
                final_sev = 2
                sev_reason = (f'best posterior CSF = {best_post:.2f}mm '
                              f'(capped at MODERATE: only {n_high} HIGH slice)')
            else:
                sev_reason = f'best posterior CSF = {best_post:.2f}mm'

            # Asymmetry bump
            if n_asymmetric_high > 0 and best_post <= 1.0 and final_sev >= 1:
                bumped = min(3, final_sev + 1)
                if bumped > final_sev:
                    sev_reason += (f' + asymmetry on {n_asymmetric_high}/{n_high} HIGH '
                                   f'(bumped {SEV_NAME[final_sev]}→{SEV_NAME[bumped]})')
                    final_sev = bumped

        severity_str = SEV_NAME[final_sev] if final_sev is not None else 'UNCLASSIFIED'

        out_rows.append({
            'level':                  name,
            'kind':                   kind,
            'n_slices':               n_slices,
            'n_high_confidence':      n_high,
            'n_abnormal_flag':        n_abnormal,
            'n_asymmetric_high':      n_asymmetric_high,
            'best_posterior_csf_mm':  round(best_post, 3) if best_post is not None else '',
            'cord_area_mean_mm2':     round(area_mean, 1) if area_mean is not None else '',
            'cord_area_std_mm2':      round(area_std, 1) if area_std is not None else '',
            'severity':               severity_str,
            'reason':                 sev_reason,
        })

    return out_rows


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text('')
        return
    all_keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                all_keys.append(k)
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=all_keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in all_keys})


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Cord/canal measurements from SliceFacts CSV + DICOM files'
    )
    ap.add_argument('slice_facts_csv',
                    help='SliceFacts CSV from data_extract.py')
    ap.add_argument('--out-dir', default='.',
                    help='directory for output CSVs (default: current dir)')
    args = ap.parse_args()
    run(args.slice_facts_csv, args.out_dir)
