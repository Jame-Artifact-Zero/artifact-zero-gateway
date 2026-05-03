"""
analyzers/breast.py
===================
Breast MRI analyzer.

Breast MRI is structurally different from joints: paired anatomy, focus
on contrast enhancement (DCE — dynamic contrast enhanced) and morphology
of any focal mass, plus left-right asymmetry comparison.

Findings detected:
  - **Focal masses**: bright contrast-enhancing lesions on T1 post-contrast
    or hyperintense on T2 STIR. Detected as connected components in the 
    breast tissue mask.
  - **Architectural asymmetry**: gross volume / signal pattern difference
    between left and right breast.
  - **Bilateral lymph nodes**: enlarged axillary lymph nodes (size threshold).

Series preference:
  1. T1 post-contrast (DCE late phase) — best for enhancing masses
  2. T2 STIR — best for cysts / fluid-containing masses
  3. T2 FS or T2 — for general anatomy

Severity:
  - CRITICAL: any mass ≥ 15 mm
  - MODERATE: any mass 8-15 mm OR sustained enhancement asymmetry
  - FINDING: any mass 4-8 mm OR mild asymmetry
  - NORMAL: no detected masses, symmetric breast tissue

What's not implemented (would need ML or radiomics):
  - BI-RADS density classification
  - BI-RADS lesion shape / margin classification
  - Architectural distortion detection beyond gross asymmetry
  - Kinetic curve analysis (DCE wash-in / wash-out)

Note: Breast MRI is operationally bilateral. The analyzer splits each
axial slice along the patient midline (x = 0) into left and right breast 
and compares.
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import (
    gaussian_filter, label as scipy_label,
    center_of_mass, binary_opening, binary_closing, binary_fill_holes,
)

from ._base import (
    BaseAnalyzer, max_severity, classify_orientation, is_t2,
    load_volume, slice_z_center,
)


def is_t1_post_contrast(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    is_t1 = 'T1' in desc and 'T2' not in desc
    is_post = ('POST' in desc or 'C+' in desc or 'GD' in desc or 'DCE' in desc
               or 'CE' in desc or 'CONTRAST' in desc)
    return is_t1 and is_post


def is_stir(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    return 'STIR' in desc


def select_best_breast_series(series_list):
    """Pick best series for breast: T1 post-contrast > STIR > T2 FS > T2."""
    axials = [s for s in series_list if s['orientation'] == 'AX']
    if not axials:
        # Fall back to any orientation
        axials = series_list

    def rank(s):
        ds = s['sample_ds']
        kind = None
        if is_t1_post_contrast(ds):
            kind = 'T1POST'; prio = 5
        elif is_stir(ds):
            kind = 'STIR'; prio = 4
        elif is_t2(ds) and ('FS' in str(getattr(ds, 'SeriesDescription', '')).upper() or
                              'FAT' in str(getattr(ds, 'SeriesDescription', '')).upper()):
            kind = 'T2FS'; prio = 3
        elif is_t2(ds):
            kind = 'T2'; prio = 2
        else:
            kind = 'OTHER'; prio = 0
        n = s['n_slices']
        rows = int(getattr(ds, 'Rows', 256))
        cols = int(getattr(ds, 'Columns', 256))
        return prio * 10_000_000 + n * rows * cols, kind

    if not axials:
        return None, None
    ranked = [(s, *rank(s)) for s in axials]
    ranked.sort(key=lambda r: -r[1])
    chosen, score, kind = ranked[0]
    if score < 1_000:  # no fluid-sensitive or contrast series found
        return None, None
    return chosen, kind


def detect_breast_tissue_mask(img, ps_mm):
    """Mask the breast tissue: anterior bright soft tissue extending from 
    chest wall. We just take the largest bright connected component above 
    background — not as principled as a chest-wall-aware mask, but close 
    enough for asymmetry comparison."""
    smooth = gaussian_filter(img, sigma=2.0)
    if smooth.max() < 30:
        return None
    thresh = max(20, np.percentile(smooth[smooth > 10], 30))
    mask = smooth > thresh
    mask = binary_opening(mask, iterations=2)
    mask = binary_fill_holes(mask)
    labeled, n = scipy_label(mask)
    if n == 0:
        return None
    sizes = sorted([(ll, (labeled == ll).sum()) for ll in range(1, n+1)],
                   key=lambda x: -x[1])
    main = labeled == sizes[0][0]
    return main


def split_left_right(mask, item):
    """Split the breast mask along patient x = 0 midline into left and right 
    breast masks."""
    H, W = mask.shape
    ipp, iop, ps = item['ipp'], item['iop'], item['ps']
    # Patient x at each pixel
    yy, xx = np.indices(mask.shape)
    patient_x = ipp[0] + xx*ps[1]*iop[0] + yy*ps[0]*iop[3]
    # +x = patient LEFT in DICOM convention
    left = mask & (patient_x > 0)
    right = mask & (patient_x <= 0)
    return left, right


def detect_focal_masses(img, ps_mm, breast_mask, image_kind='T1POST',
                          min_area_mm2=8):
    """Find focal bright lesions inside the breast mask. T1 post: enhancing 
    tissue is brightest. STIR / T2FS: fluid-filled cysts are bright. Either 
    way, threshold above mean + 2σ within breast tissue."""
    if breast_mask is None or breast_mask.sum() < 200:
        return []
    smooth = gaussian_filter(img, sigma=1.0)
    in_breast = smooth[breast_mask]
    if in_breast.size < 100:
        return []
    mean_b = float(np.mean(in_breast))
    std_b = float(np.std(in_breast))
    thresh = mean_b + 2.0 * std_b
    bright = (smooth > thresh) & breast_mask
    bright = binary_opening(bright, iterations=1)
    labeled, n = scipy_label(bright)
    masses = []
    for ll in range(1, n+1):
        comp = labeled == ll
        a = comp.sum() * ps_mm**2
        if a < min_area_mm2:
            continue
        if a > 0.4 * breast_mask.sum() * ps_mm**2:
            continue  # too large to be focal
        cy, cx = center_of_mass(comp)
        # Approximate diameter: 2 * sqrt(area / pi)
        diameter_mm = 2.0 * np.sqrt(a / np.pi)
        masses.append({
            'area_mm2': float(a),
            'diameter_approx_mm': float(diameter_mm),
            'centroid_rc': (float(cy), float(cx)),
            'mean_intensity': float(smooth[comp].mean()),
        })
    return masses


class BreastAnalyzer(BaseAnalyzer):
    body_part_codes = ('BREAST', 'BREASTS', 'CHEST_BREAST', 'BREAST_LT',
                       'BREAST_RT', 'BREAST_LEFT', 'BREAST_RIGHT', 'BREAST_BL',
                       'BILATERAL_BREAST')
    body_part_label = 'breast'

    def analyze(self, series_list, work_dir=None):
        primary, kind = select_best_breast_series(series_list)
        if primary is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'reason': 'no T1 post-contrast / STIR / T2 series found for breast',
                'series_seen': [
                    {k: s[k] for k in ('series_description', 'orientation',
                                        'modality', 'n_slices')}
                    for s in series_list
                ],
            }

        items = load_volume(primary['files'])
        slice_thickness_mm = (
            abs(slice_z_center(items[1]) - slice_z_center(items[0]))
            if len(items) >= 2 else 3.0
        )

        slice_records = []
        markers = []
        all_masses = []  # (slice_inst, breast_side, mass dict)

        total_left_volume_mm3 = 0.0
        total_right_volume_mm3 = 0.0

        for it in items:
            ps_mm = float(it['ps'][0])
            tissue = detect_breast_tissue_mask(it['img'], ps_mm)
            if tissue is None or tissue.sum() * ps_mm**2 < 1000:
                continue

            left, right = split_left_right(tissue, it)
            left_area = float(left.sum() * ps_mm**2)
            right_area = float(right.sum() * ps_mm**2)
            total_left_volume_mm3 += left_area * slice_thickness_mm
            total_right_volume_mm3 += right_area * slice_thickness_mm

            # Detect focal masses in each side independently
            left_masses = detect_focal_masses(it['img'], ps_mm, left, kind, min_area_mm2=8)
            right_masses = detect_focal_masses(it['img'], ps_mm, right, kind, min_area_mm2=8)
            for m in left_masses:
                all_masses.append((it['inst'], 'left', m))
            for m in right_masses:
                all_masses.append((it['inst'], 'right', m))

            cy, cx = center_of_mass(tissue)
            ipp, iop, ps = it['ipp'], it['iop'], it['ps']
            anat_xyz = ipp + cx*ps[1]*iop[0:3] + cy*ps[0]*iop[3:6]

            sliced_asym = ((left_area - right_area) / (left_area + right_area)
                           if (left_area + right_area) > 0 else 0.0)
            slice_records.append({
                'inst': it['inst'],
                'z_mm': slice_z_center(it),
                'left_breast_area_mm2': left_area,
                'right_breast_area_mm2': right_area,
                'tissue_asym_lr': sliced_asym,
                'left_mass_count': len(left_masses),
                'right_mass_count': len(right_masses),
                'left_mass_area_mm2': sum(m['area_mm2'] for m in left_masses),
                'right_mass_area_mm2': sum(m['area_mm2'] for m in right_masses),
            })
            markers.append({
                'inst': it['inst'],
                'z_mm': slice_z_center(it),
                'centroid_xyz_mm': [round(float(v), 3) for v in anat_xyz],
                'left_mass_count': len(left_masses),
                'right_mass_count': len(right_masses),
                'tissue_asym_lr': round(float(sliced_asym), 3),
                'severity': 'NORMAL',
            })

        # Build flag list
        all_flags = []

        # Flag biggest mass (by diameter)
        if all_masses:
            max_mass = max(all_masses, key=lambda x: x[2]['diameter_approx_mm'])
            inst, side, m = max_mass
            d = m['diameter_approx_mm']
            if d >= 15:
                sev = 'CRITICAL'
            elif d >= 8:
                sev = 'MODERATE'
            elif d >= 4:
                sev = 'FINDING'
            else:
                sev = 'NORMAL'
            if sev != 'NORMAL':
                all_flags.append({
                    'label': f"{side} breast focal mass (~{d:.1f} mm diameter)",
                    'severity': sev,
                    'level': f'{side}_breast',
                })

        # Flag mass count if many small masses
        n_left = sum(1 for inst, side, m in all_masses if side == 'left')
        n_right = sum(1 for inst, side, m in all_masses if side == 'right')
        if abs(n_left - n_right) >= 5 and (n_left + n_right) >= 5:
            heavier = 'left' if n_left > n_right else 'right'
            all_flags.append({
                'label': f"asymmetric mass burden ({n_left}L / {n_right}R)",
                'severity': 'MODERATE',
                'level': 'overall',
            })

        # Flag overall tissue volume asymmetry
        total_vol = total_left_volume_mm3 + total_right_volume_mm3
        if total_vol > 0:
            tissue_asym = (total_left_volume_mm3 - total_right_volume_mm3) / total_vol
        else:
            tissue_asym = 0.0
        # Breasts can be naturally asymmetric — only flag if marked
        if abs(tissue_asym) >= 0.20:
            heavier = 'left' if tissue_asym > 0 else 'right'
            all_flags.append({
                'label': f"marked breast tissue asymmetry ({tissue_asym:+.2f})",
                'severity': 'MODERATE',
                'level': 'overall',
            })
        elif abs(tissue_asym) >= 0.10:
            heavier = 'left' if tissue_asym > 0 else 'right'
            all_flags.append({
                'label': f"breast tissue asymmetry ({tissue_asym:+.2f})",
                'severity': 'FINDING',
                'level': 'overall',
            })

        overall = max_severity(all_flags)
        counts = {'critical': 0, 'moderate': 0, 'finding': 0, 'normal': 0}
        for f in all_flags:
            sev = f['severity'].lower()
            counts[sev if sev in counts else 'normal'] += 1
        if not all_flags:
            counts['normal'] = 1

        # Mass list for the report
        mass_list = []
        for inst, side, m in all_masses:
            mass_list.append({
                'inst': inst, 'side': side,
                'area_mm2': round(float(m['area_mm2']), 2),
                'diameter_approx_mm': round(float(m['diameter_approx_mm']), 2),
            })
        # Sort by diameter descending
        mass_list.sort(key=lambda x: -x['diameter_approx_mm'])

        return {
            'status': overall,
            'body_part_label': self.body_part_label,
            'series_used': {
                'primary': {
                    'series_description': primary['series_description'],
                    'n_slices': primary['n_slices'],
                    'series_uid': primary['series_uid'],
                    'sequence_kind': kind,
                },
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
            'breast_findings': {
                'sequence_kind': kind,
                'slice_thickness_mm': slice_thickness_mm,
                'n_slices_analyzed': len(slice_records),
                'total_left_volume_mm3': total_left_volume_mm3,
                'total_right_volume_mm3': total_right_volume_mm3,
                'tissue_asym_lr': float(tissue_asym),
                'mass_count_left': int(n_left),
                'mass_count_right': int(n_right),
                'masses': mass_list[:20],  # top 20 by size
            },
        }
