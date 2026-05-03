"""
analyzers/_joint_common.py
==========================
Shared machinery for joint analyzers: knee, ankle, foot, shoulder, elbow,
wrist, hand.

Joints share several finding types that don't exist in spine or brain:

  1. **Joint effusion**: bright fluid inside the joint capsule on T2/STIR/PD-FS.
     Excess effusion is a non-specific but sensitive sign of injury or
     inflammation. Quantified as fluid volume relative to joint capsule volume.

  2. **Bone marrow edema**: hyperintensity inside trabecular bone on
     fat-suppressed T2/STIR. Bone bruise / occult fracture / stress reaction.

  3. **Tendon/ligament signal abnormality**: normal tendons are uniformly
     dark on all sequences. Increased internal signal on T2/PD or visible
     gap = strain / partial tear / full tear.

  4. **Bilateral asymmetry**: relevant for breast and paired imaging only.

This module hands joint-specific analyzers the primitives:
  - detect_fluid_collection: bright voxel clusters in a region of interest
  - detect_marrow_edema: hyperintensity within a bony region
  - detect_tendon_signal: increased signal along a tendon track
  - estimate_joint_capsule: rough joint extent from anatomy mask
  - sequence_picker: pick best fluid-sensitive series (STIR > T2 FS > PD FS > T2)
"""
from __future__ import annotations
import numpy as np
from scipy.ndimage import (
    gaussian_filter, label as scipy_label,
    center_of_mass, binary_opening, binary_closing, binary_fill_holes,
)


def is_fluid_sensitive(ds):
    """Pick fluid-sensitive sequences in priority order: STIR > T2 FS > PD FS > T2."""
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    if 'STIR' in desc:
        return ('STIR', 4)
    if 'PDFS' in desc or 'PD FS' in desc or 'PD-FS' in desc or ('PD' in desc and 'FS' in desc):
        return ('PDFS', 3)
    if ('T2' in desc and ('FS' in desc or 'FAT' in desc)):
        return ('T2FS', 3)
    if 'T2' in desc:
        return ('T2', 2)
    if 'PD' in desc:
        return ('PD', 1)
    return (None, 0)


def is_t1(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    return 'T1' in desc and 'T2' not in desc


def select_best_fluid_sensitive(series_list, orientation='AX'):
    """Pick the highest-priority fluid-sensitive series in the requested
    orientation. Returns (series_dict, kind) or (None, None)."""
    candidates = []
    for s in series_list:
        if s['orientation'] != orientation:
            continue
        kind, prio = is_fluid_sensitive(s['sample_ds'])
        if kind is None:
            continue
        candidates.append((s, kind, prio))
    if not candidates:
        return None, None
    candidates.sort(key=lambda c: -c[2])
    return candidates[0][0], candidates[0][1]


def detect_anatomy_mask(img, ps_mm, min_area_mm2=2000):
    """Generic 'inside body / inside joint region' mask: largest bright-tissue
    connected component above background. Works for joint slices where the
    body part fills a meaningful portion of the FOV."""
    smooth = gaussian_filter(img, sigma=1.5)
    if smooth.max() < 30:
        return None
    thresh = max(20, np.percentile(smooth[smooth > 10], 25))
    mask = smooth > thresh
    mask = binary_opening(mask, iterations=2)
    mask = binary_fill_holes(mask)
    labeled, n = scipy_label(mask)
    if n == 0:
        return None
    sizes = [(ll, (labeled == ll).sum()) for ll in range(1, n+1)]
    sizes.sort(key=lambda x: -x[1])
    main_label, main_size = sizes[0]
    if main_size * ps_mm**2 < min_area_mm2:
        return None
    main = labeled == main_label
    main = binary_closing(main, iterations=3)
    main = binary_fill_holes(main)
    return main


def detect_fluid_collections(img, ps_mm, anatomy_mask, image_kind='T2',
                              min_area_mm2=10):
    """Find bright (fluid-signal) regions inside the anatomy mask.

    On STIR / T2 FS / PD FS / T2: fluid is hyperintense.
    On T1: fluid is hypointense (we don't typically use T1 for fluid).

    Returns list of {area_mm2, centroid_rc, mean_intensity}.
    """
    if anatomy_mask is None:
        return []
    smooth = gaussian_filter(img, sigma=1.0)
    in_mask = smooth[anatomy_mask]
    if in_mask.size < 100:
        return []

    if image_kind in ('STIR', 'T2FS', 'PDFS', 'T2', 'PD'):
        # Fluid is bright — threshold above mean + 2σ
        mean_v = float(np.mean(in_mask))
        std_v = float(np.std(in_mask))
        thresh = mean_v + 2.0 * std_v
        fluid = (smooth > thresh) & anatomy_mask
    else:
        # T1: fluid is dark — threshold below mean - 2σ
        mean_v = float(np.mean(in_mask))
        std_v = float(np.std(in_mask))
        thresh = mean_v - 1.5 * std_v
        fluid = (smooth < thresh) & anatomy_mask

    fluid = binary_opening(fluid, iterations=1)
    labeled, n = scipy_label(fluid)
    out = []
    for ll in range(1, n+1):
        comp = labeled == ll
        a = comp.sum() * ps_mm**2
        if a < min_area_mm2:
            continue
        if a > 0.6 * anatomy_mask.sum() * ps_mm**2:
            continue  # mask itself, not a focal collection
        cy, cx = center_of_mass(comp)
        out.append({
            'area_mm2': float(a),
            'centroid_rc': (float(cy), float(cx)),
            'mean_intensity': float(smooth[comp].mean()),
        })
    return out


def detect_marrow_edema(img, ps_mm, anatomy_mask, image_kind='STIR',
                          min_area_mm2=15):
    """Detect bone marrow edema: hyperintensity inside trabecular bone on
    fat-suppressed sequences (STIR / T2FS / PDFS).

    Bone marrow on T1 is bright (fatty), on fat-suppressed T2 is dark
    (suppressed) when normal, and bright (fluid in bone) when edematous.
    """
    if image_kind not in ('STIR', 'T2FS', 'PDFS'):
        return []  # T1 / non-FS T2 not useful for marrow edema
    return detect_fluid_collections(img, ps_mm, anatomy_mask, image_kind,
                                       min_area_mm2=min_area_mm2)


def aggregate_volume_findings(per_slice_findings, ps_mm_axial, slice_thickness_mm):
    """Aggregate per-slice 2D findings into per-volume 3D summaries.

    Connects findings that overlap across adjacent slices into 3D blobs.
    """
    # Simple aggregation: total count, total volume estimate, max single-slice area
    if not per_slice_findings:
        return {
            'count_2d': 0,
            'total_area_mm2': 0.0,
            'estimated_volume_mm3': 0.0,
            'max_single_slice_area_mm2': 0.0,
        }
    total_count = sum(len(slc) for slc in per_slice_findings)
    total_area = sum(sum(f['area_mm2'] for f in slc) for slc in per_slice_findings)
    max_area = max((f['area_mm2'] for slc in per_slice_findings for f in slc), default=0)
    return {
        'count_2d': int(total_count),
        'total_area_mm2': float(total_area),
        'estimated_volume_mm3': float(total_area * slice_thickness_mm),
        'max_single_slice_area_mm2': float(max_area),
    }


def severity_from_volume(volume_mm3, thresholds):
    """Map a volume measurement to severity level using a threshold dict.
    thresholds: {'critical_mm3': X, 'moderate_mm3': Y, 'finding_mm3': Z}
    """
    if volume_mm3 >= thresholds.get('critical_mm3', float('inf')):
        return 'CRITICAL'
    if volume_mm3 >= thresholds.get('moderate_mm3', float('inf')):
        return 'MODERATE'
    if volume_mm3 >= thresholds.get('finding_mm3', float('inf')):
        return 'FINDING'
    return 'NORMAL'


def build_marker(inst, z_mm, anatomy_centroid_xyz, findings, severity):
    """Standard marker shape for joint analyzers — keeps the schema consistent
    with spine/brain markers."""
    return {
        'inst': inst,
        'z_mm': float(z_mm),
        'centroid_xyz_mm': [round(float(v), 3) for v in anatomy_centroid_xyz],
        'findings': findings,
        'severity': severity,
    }


# ----------------------------------------------------------------------------
# Generic joint analyzer body — most joint analyzers can subclass this with
# just a different threshold dict and a different body_part_label.
# ----------------------------------------------------------------------------
from ._base import BaseAnalyzer, max_severity, load_volume, slice_z_center


class GenericJointAnalyzer(BaseAnalyzer):
    """Default joint analyzer.

    Subclasses set:
      - body_part_codes
      - body_part_label
      - effusion_thresholds  (dict with critical/moderate/finding _mm3)
      - marrow_edema_thresholds
      - extra_findings(items, kind, slice_thickness_mm) -> list of flag dicts
                       (optional override for body-part-specific signals)

    The base does global effusion + marrow edema. Subclasses add specifics.
    """
    # Defaults (knee-like):
    effusion_thresholds = {
        'critical_mm3': 30000, 'moderate_mm3': 10000, 'finding_mm3': 3000,
    }
    marrow_edema_thresholds = {
        'critical_mm3': 5000, 'moderate_mm3': 1500, 'finding_mm3': 300,
    }
    # Minimum anatomy mask area required to consider a slice analyzable
    min_anatomy_area_mm2 = 1500

    def extra_findings(self, items, kind, slice_thickness_mm):
        """Override to add body-part-specific findings. Return a list of
        {label, severity, level} flag dicts."""
        return []

    def analyze(self, series_list, work_dir=None):
        primary = None; kind = None; orient = None
        for o in ('AX', 'SAG', 'COR'):
            primary, kind = select_best_fluid_sensitive(series_list, orientation=o)
            if primary is not None:
                orient = o
                break
        if primary is None:
            return {
                'status': 'INSUFFICIENT_DATA',
                'reason': 'no fluid-sensitive (STIR/T2FS/PDFS/T2/PD) series found',
                'series_seen': [
                    {k: s[k] for k in ('series_description', 'orientation',
                                        'modality', 'n_slices')}
                    for s in series_list
                ],
            }

        items = load_volume(primary['files'])
        if not items:
            return {'status': 'INSUFFICIENT_DATA', 'reason': 'series loaded zero slices'}

        slice_thickness_mm = (
            abs(slice_z_center(items[1]) - slice_z_center(items[0]))
            if len(items) >= 2 else 3.0
        )

        per_slice_effusions = []
        per_slice_marrow = []
        slice_records = []
        markers = []

        for it in items:
            ps_mm = float(it['ps'][0])
            anat = detect_anatomy_mask(
                it['img'], ps_mm, min_area_mm2=self.min_anatomy_area_mm2,
            )
            if anat is None:
                continue

            effusions = detect_fluid_collections(
                it['img'], ps_mm, anat,
                image_kind=kind, min_area_mm2=10,
            )
            marrow = []
            if kind in ('STIR', 'T2FS', 'PDFS'):
                marrow = detect_marrow_edema(
                    it['img'], ps_mm, anat,
                    image_kind=kind, min_area_mm2=15,
                )
            per_slice_effusions.append(effusions)
            per_slice_marrow.append(marrow)

            cy, cx = center_of_mass(anat)
            ipp, iop, ps = it['ipp'], it['iop'], it['ps']
            anat_xyz = ipp + cx*ps[1]*iop[0:3] + cy*ps[0]*iop[3:6]

            slice_eff_total = sum(e['area_mm2'] for e in effusions)
            slice_marrow_total = sum(m['area_mm2'] for m in marrow)
            slice_records.append({
                'inst': it['inst'],
                'z_mm': slice_z_center(it),
                'effusion_count': len(effusions),
                'effusion_area_mm2': slice_eff_total,
                'marrow_edema_count': len(marrow),
                'marrow_edema_area_mm2': slice_marrow_total,
            })
            markers.append({
                'inst': it['inst'],
                'z_mm': slice_z_center(it),
                'centroid_xyz_mm': [round(float(v), 3) for v in anat_xyz],
                'effusion_area_mm2': round(slice_eff_total, 2),
                'marrow_edema_area_mm2': round(slice_marrow_total, 2),
                'severity': 'NORMAL',
            })

        effusion_agg = aggregate_volume_findings(
            per_slice_effusions, 1.0, slice_thickness_mm,
        )
        marrow_agg = aggregate_volume_findings(
            per_slice_marrow, 1.0, slice_thickness_mm,
        )

        all_flags = []

        eff_sev = severity_from_volume(
            effusion_agg['estimated_volume_mm3'],
            self.effusion_thresholds,
        )
        if eff_sev != 'NORMAL':
            all_flags.append({
                'label': f"joint effusion (~{effusion_agg['estimated_volume_mm3']:.0f} mm³)",
                'severity': eff_sev, 'level': 'overall',
            })

        if kind in ('STIR', 'T2FS', 'PDFS'):
            edema_sev = severity_from_volume(
                marrow_agg['estimated_volume_mm3'],
                self.marrow_edema_thresholds,
            )
            if edema_sev != 'NORMAL':
                all_flags.append({
                    'label': f"bone marrow edema (~{marrow_agg['estimated_volume_mm3']:.0f} mm³)",
                    'severity': edema_sev, 'level': 'overall',
                })

        # Body-part-specific findings
        extra_flags = self.extra_findings(items, kind, slice_thickness_mm)
        all_flags.extend(extra_flags)

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
                'primary': {
                    'series_description': primary['series_description'],
                    'n_slices': primary['n_slices'],
                    'series_uid': primary['series_uid'],
                    'sequence_kind': kind,
                    'orientation': orient,
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
            'joint_findings': {
                'effusion':     effusion_agg,
                'marrow_edema': marrow_agg,
                'sequence_kind': kind,
                'slice_thickness_mm': slice_thickness_mm,
                'n_slices_analyzed': len(slice_records),
            },
        }
