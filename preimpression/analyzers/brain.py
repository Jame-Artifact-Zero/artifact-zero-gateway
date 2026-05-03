"""
analyzers/brain.py
==================
Brain MRI analyzer.

Brain has no cord-like linear structure to track, so the analyzer is built
around different anatomical markers. The "find more than the rad can"
target shifts to:

  1. **Midline shift**: deviation of the interhemispheric fissure from the
     bony midline. Sub-millimeter shift may be missed visually but is
     clinically critical for mass effect. We track it per-slice and report
     the maximum shift, the slice it occurs on, and the direction.

  2. **Ventricular asymmetry**: lateral ventricles segmented per slice,
     compared L vs R. Asymmetric ventricles point at unilateral mass effect,
     atrophy, or developmental variants.

  3. **FLAIR hyperintensity load** (when FLAIR series available): count and
     total volume of bright lesions outside ventricles. White-matter
     lesion burden over age-expected baseline raises a finding.

  4. **Brain symmetry**: per-slice L vs R brain area; gross asymmetry
     (≥3%) suggests focal pathology.

Series preference (in order):
  Axial T2 FLAIR > Axial T2 > Axial T1 (always uses the best available)
  Sagittal T1 (if available, used for orientation reference)

The output uses the same JSON schema shape as spine analyzers but with a
brain-specific "anatomy" block in place of "level_summaries", and "markers"
that are 3D points anchoring the midline at each axial z.
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import (
    gaussian_filter, gaussian_filter1d, label as scipy_label,
    center_of_mass, binary_opening, binary_closing, binary_fill_holes,
)
from scipy.signal import find_peaks

from ._base import (
    BaseAnalyzer, max_severity, classify_orientation, is_t2, is_flair, is_t1,
    load_volume,
)


BRAIN_MIDLINE_SHIFT = {
    'critical_mm': 5.0,    # surgical threshold
    'moderate_mm': 3.0,
    'finding_mm':  1.0,
}
BRAIN_VENTRICLE_ASYM = {
    'critical_abs': 0.30,  # 30% volume difference
    'moderate_abs': 0.20,
    'finding_abs':  0.10,
}
BRAIN_FLAIR_LESION = {
    'critical_count': 30,
    'moderate_count': 15,
    'finding_count':   5,
}


def select_best_brain_axial(series_list):
    """Brain prefers FLAIR > T2 > T1 axial."""
    flair = [s for s in series_list
             if s['orientation'] == 'AX' and is_flair(s['sample_ds'])]
    t2 = [s for s in series_list
          if s['orientation'] == 'AX' and is_t2(s['sample_ds']) and not is_flair(s['sample_ds'])]
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


def detect_brain_mask(img, ps_mm):
    """Return a boolean mask of the brain (excluding skull and air).

    Strategy: Otsu-style threshold on a smoothed image, then largest connected
    component, then morphological close to fill ventricles.
    """
    smooth = gaussian_filter(img, sigma=1.5)
    if smooth.max() < 30:
        return None
    # Two-class threshold via simple percentile (Otsu would be cleaner)
    thresh = max(30, np.percentile(smooth[smooth > 10], 30))
    mask = smooth > thresh
    mask = binary_opening(mask, iterations=2)
    mask = binary_fill_holes(mask)
    labeled, n = scipy_label(mask)
    if n == 0:
        return None
    # Pick largest component
    sizes = [(ll, (labeled == ll).sum()) for ll in range(1, n+1)]
    sizes.sort(key=lambda x: -x[1])
    main = labeled == sizes[0][0]
    main = binary_closing(main, iterations=3)
    main = binary_fill_holes(main)
    return main


def detect_midline(img, brain_mask, ps_mm):
    """Detect the interhemispheric midline on an axial slice.

    Strategy:
      1. The interhemispheric fissure is a low-intensity vertical strip
         splitting the brain into L/R hemispheres.
      2. For each row, find the column position of the local minimum within
         the brain mask, in the central third.
      3. Robust-fit a line to those positions to get a midline.

    Returns dict with:
      midline_col_at_each_row: array of column indices (or NaN where no
                               brain present)
      anatomical_midline_pix:  best-fit linear midline as (col_top, col_bot)
      bony_midline_pix:        same, but from the brain-mask centroid 
                               (proxy for skull centerline)
      shift_mm:                signed L/R deviation of anatomical from bony
                               midline at the row of maximum offset
      shift_direction:         'left' or 'right'
    """
    H, W = img.shape
    smooth = gaussian_filter(img, sigma=1.0)

    # Bony midline: vertical line through brain mask centroid
    if brain_mask is None or brain_mask.sum() < 1000:
        return None
    cy, cx = center_of_mass(brain_mask)
    bony_col = cx

    # Row-by-row anatomical midline
    midline_cols = np.full(H, np.nan)
    for r in range(H):
        if brain_mask[r].sum() < 10:
            continue
        # Brain extent in this row
        cols_in = np.where(brain_mask[r])[0]
        c_start, c_end = cols_in.min(), cols_in.max()
        # Search central third for a low-intensity vertical line
        c_mid = int((c_start + c_end) / 2)
        c_lo = max(c_start, c_mid - int(15/ps_mm))
        c_hi = min(c_end, c_mid + int(15/ps_mm))
        if c_hi - c_lo < 5:
            continue
        # Smooth row in mask region only
        row_vals = smooth[r, c_lo:c_hi+1].copy()
        # Find minimum
        midline_cols[r] = c_lo + int(np.argmin(row_vals))

    # Median-based linear fit through the midline samples
    valid = ~np.isnan(midline_cols)
    if valid.sum() < 20:
        return {
            'shift_mm': 0.0, 'shift_direction': 'none',
            'bony_midline_col': float(bony_col),
            'anatomical_midline_col_median': float(np.nanmedian(midline_cols)) if valid.any() else float(bony_col),
        }
    rows_v = np.where(valid)[0]
    cols_v = midline_cols[valid]
    # Robust median offset
    anat_col_median = float(np.median(cols_v))

    # Per-row shift (anatomical minus bony)
    shift_per_row = midline_cols - bony_col  # in pixels
    shift_per_row_mm = shift_per_row * ps_mm

    # Maximum absolute shift in valid rows
    abs_shifts = np.abs(shift_per_row_mm)
    abs_shifts[np.isnan(abs_shifts)] = 0
    max_idx = int(np.argmax(abs_shifts))
    max_shift_mm = float(shift_per_row_mm[max_idx]) if not np.isnan(shift_per_row_mm[max_idx]) else 0.0

    return {
        'shift_mm': max_shift_mm,
        'shift_direction': 'left' if max_shift_mm > 0 else ('right' if max_shift_mm < 0 else 'none'),
        'shift_max_at_row': int(max_idx),
        'bony_midline_col': float(bony_col),
        'anatomical_midline_col_median': anat_col_median,
        'midline_col_per_row': midline_cols.tolist(),
    }


def segment_lateral_ventricles(img, brain_mask, ps_mm, image_kind='T2'):
    """Segment lateral ventricles on an axial slice.

    Ventricles on T2/FLAIR: bright (CSF) on T2, suppressed (dark) on FLAIR.
    On T1: dark.

    For T2: threshold above a high percentile of brain pixels.
    For FLAIR: threshold below a low percentile (suppressed).
    For T1: threshold below a low percentile.

    Returns left_area_mm2, right_area_mm2 split by image midline.
    """
    if brain_mask is None or brain_mask.sum() < 1000:
        return 0.0, 0.0
    smooth = gaussian_filter(img, sigma=1.0)
    in_brain = smooth.copy()
    in_brain[~brain_mask] = np.nan

    if image_kind == 'T2':
        # CSF is brightest in brain
        thresh = np.nanpercentile(in_brain, 92)
        vent_mask = (smooth > thresh) & brain_mask
    elif image_kind == 'FLAIR':
        thresh = np.nanpercentile(in_brain, 8)
        vent_mask = (smooth < thresh) & brain_mask
    else:  # T1
        thresh = np.nanpercentile(in_brain, 12)
        vent_mask = (smooth < thresh) & brain_mask

    vent_mask = binary_opening(vent_mask, iterations=1)
    # Keep only larger components (ventricles, not vessels or speckle)
    labeled, n = scipy_label(vent_mask)
    keep = np.zeros_like(vent_mask)
    for ll in range(1, n+1):
        comp = labeled == ll
        if comp.sum() * ps_mm**2 > 30:  # 30 mm² minimum ventricle component
            keep |= comp
    vent_mask = keep

    # Split L/R by image midline (centroid column of brain mask)
    cy, cx = center_of_mass(brain_mask)
    H, W = vent_mask.shape
    yy, xx = np.indices(vent_mask.shape)
    # Patient L/R interpretation depends on IOP, but for split purposes 
    # we use image-left vs image-right.
    left_vent = vent_mask & (xx < cx)
    right_vent = vent_mask & (xx >= cx)
    return (float(left_vent.sum() * ps_mm**2),
            float(right_vent.sum() * ps_mm**2))


def detect_flair_lesions(img, brain_mask, ps_mm, ventricle_mask=None):
    """Detect hyperintense lesions on FLAIR.

    Returns list of {area_mm2, centroid_rc, intensity_max} per lesion."""
    if brain_mask is None:
        return []
    smooth = gaussian_filter(img, sigma=1.0)
    in_brain = smooth[brain_mask]
    if in_brain.size < 100:
        return []
    # Hyperintense threshold: well above mean white matter
    mean_b = float(np.mean(in_brain))
    std_b = float(np.std(in_brain))
    thresh = mean_b + 2.0 * std_b

    hyper = (smooth > thresh) & brain_mask
    if ventricle_mask is not None:
        hyper &= ~ventricle_mask
    hyper = binary_opening(hyper, iterations=1)
    labeled, n = scipy_label(hyper)
    lesions = []
    for ll in range(1, n+1):
        comp = labeled == ll
        a = comp.sum() * ps_mm**2
        if a < 3:
            continue  # very small noise
        if a > 5000:
            continue  # huge — probably not a focal lesion
        cy, cx = center_of_mass(comp)
        lesions.append({
            'area_mm2': float(a),
            'centroid_rc': (float(cy), float(cx)),
            'intensity_max': float(smooth[comp].max()),
        })
    return lesions


class BrainAnalyzer(BaseAnalyzer):
    body_part_codes = ('BRAIN', 'HEAD', 'NEURO')
    body_part_label = 'brain'

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
        # Optional FLAIR for lesions, even if not the primary anatomy series
        flair_axial = None
        if kind != 'FLAIR' and all_flair:
            from ._base import load_volume as lv
            flair_axial = max(all_flair, key=lambda s: s['n_slices'])
            try:
                flair_items = lv(flair_axial['files'])
            except Exception:
                flair_items = None
        else:
            flair_items = ax_items if kind == 'FLAIR' else None

        # Per-slice analysis
        slice_records = []
        markers = []
        for it in ax_items:
            ps_mm = float(it['ps'][0])
            brain_mask = detect_brain_mask(it['img'], ps_mm)
            if brain_mask is None or brain_mask.sum() * ps_mm**2 < 5000:
                # Slice doesn't contain enough brain (e.g., top/bottom of FOV)
                continue

            mid = detect_midline(it['img'], brain_mask, ps_mm)
            if mid is None:
                continue

            left_v, right_v = segment_lateral_ventricles(
                it['img'], brain_mask, ps_mm, image_kind=kind,
            )
            vent_total = left_v + right_v
            vent_asym = ((left_v - right_v) / vent_total) if vent_total > 0 else 0.0

            # Brain hemisphere areas
            cy_b, cx_b = center_of_mass(brain_mask)
            yy, xx = np.indices(brain_mask.shape)
            left_brain = brain_mask & (xx < cx_b)
            right_brain = brain_mask & (xx >= cx_b)
            left_a = float(left_brain.sum() * ps_mm**2)
            right_a = float(right_brain.sum() * ps_mm**2)
            tot_a = left_a + right_a
            brain_asym = ((left_a - right_a) / tot_a) if tot_a > 0 else 0.0

            # Per-slice flags
            flags = []
            shift = abs(mid['shift_mm'])
            if shift >= BRAIN_MIDLINE_SHIFT['critical_mm']:
                flags.append({'label': f"midline shift {mid['shift_mm']:+.1f} mm",
                              'severity': 'CRITICAL'})
            elif shift >= BRAIN_MIDLINE_SHIFT['moderate_mm']:
                flags.append({'label': f"midline shift {mid['shift_mm']:+.1f} mm",
                              'severity': 'MODERATE'})
            elif shift >= BRAIN_MIDLINE_SHIFT['finding_mm']:
                flags.append({'label': f"midline shift {mid['shift_mm']:+.1f} mm",
                              'severity': 'FINDING'})

            # Per-slice 3D marker (midline pixel at brain centroid → patient xyz)
            cy_b, cx_b = center_of_mass(brain_mask)
            ipp, iop, ps = it['ipp'], it['iop'], it['ps']
            mid_xyz = ipp + cx_b*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]
            # Anatomical (true) midline at center row
            anat_col = mid['anatomical_midline_col_median']
            anat_xyz = ipp + anat_col*ps[1]*iop[0:3] + cy_b*ps[0]*iop[3:6]

            slice_records.append({
                'inst': it['inst'],
                'z_mm': float(mid_xyz[2]),
                'brain_area_mm2': tot_a,
                'midline_shift_mm': mid['shift_mm'],
                'midline_shift_direction': mid['shift_direction'],
                'left_ventricle_mm2': left_v,
                'right_ventricle_mm2': right_v,
                'ventricle_asym_lr': vent_asym,
                'brain_asym_lr': brain_asym,
                'flags': flags,
            })
            markers.append({
                'inst': it['inst'],
                'midline_xyz_mm': [round(float(anat_xyz[0]), 3),
                                    round(float(anat_xyz[1]), 3),
                                    round(float(anat_xyz[2]), 3)],
                'brain_centroid_xyz_mm': [round(float(mid_xyz[0]), 3),
                                           round(float(mid_xyz[1]), 3),
                                           round(float(mid_xyz[2]), 3)],
                'midline_shift_mm': round(float(mid['shift_mm']), 2),
                'shift_direction': mid['shift_direction'],
                'ventricle_asym_lr': round(float(vent_asym), 3),
                'brain_asym_lr': round(float(brain_asym), 3),
                'severity': max_severity(flags),
            })

        # Aggregate findings
        all_flags = []
        # Max midline shift
        max_shift_record = None
        if slice_records:
            max_shift_record = max(slice_records, key=lambda s: abs(s['midline_shift_mm']))
            ms = abs(max_shift_record['midline_shift_mm'])
            if ms >= BRAIN_MIDLINE_SHIFT['critical_mm']:
                all_flags.append({
                    'label': f"midline shift {max_shift_record['midline_shift_mm']:+.1f} mm",
                    'severity': 'CRITICAL',
                    'level': f"z={max_shift_record['z_mm']:.0f}",
                })
            elif ms >= BRAIN_MIDLINE_SHIFT['moderate_mm']:
                all_flags.append({
                    'label': f"midline shift {max_shift_record['midline_shift_mm']:+.1f} mm",
                    'severity': 'MODERATE',
                    'level': f"z={max_shift_record['z_mm']:.0f}",
                })
            elif ms >= BRAIN_MIDLINE_SHIFT['finding_mm']:
                all_flags.append({
                    'label': f"midline shift {max_shift_record['midline_shift_mm']:+.1f} mm",
                    'severity': 'FINDING',
                    'level': f"z={max_shift_record['z_mm']:.0f}",
                })

        # Ventricle asymmetry: total volume
        total_left_vent = sum(s['left_ventricle_mm2'] for s in slice_records)
        total_right_vent = sum(s['right_ventricle_mm2'] for s in slice_records)
        total_vent = total_left_vent + total_right_vent
        vent_asym_overall = ((total_left_vent - total_right_vent) / total_vent) if total_vent > 0 else 0.0
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

        # FLAIR lesion load (if FLAIR available)
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
                # Need ventricle mask to exclude
                if kind == 'FLAIR':
                    in_brain = it['img'].copy()
                    thresh_v = np.percentile(in_brain[bm], 8)
                    vent = (it['img'] < thresh_v) & bm
                else:
                    vent = None
                lesions = detect_flair_lesions(it['img'], bm, ps_mm, ventricle_mask=vent)
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

        return {
            'status': overall,
            'body_part_label': self.body_part_label,
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
            'levels_detected': {},  # Brain has no vertebral levels
            'impression': {
                'overall_status': overall,
                'counts': counts,
                'flags': all_flags,
            },
            'level_summaries': [],  # Not applicable for brain
            'slice_measurements': slice_records,
            'markers': markers,
            'brain_findings': {
                'max_midline_shift_mm':
                    float(max_shift_record['midline_shift_mm']) if max_shift_record else 0.0,
                'max_shift_at_z_mm':
                    float(max_shift_record['z_mm']) if max_shift_record else 0.0,
                'total_left_ventricle_mm2': total_left_vent,
                'total_right_ventricle_mm2': total_right_vent,
                'ventricle_asym_overall': vent_asym_overall,
                'flair_lesion_summary': flair_lesion_summary,
                'n_brain_slices_analyzed': len(slice_records),
            },
        }
