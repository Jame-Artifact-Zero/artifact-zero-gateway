"""
spine_measurements.py — v7-marker-based measurements and severity classification
================================================================================

Consumes the marker list returned by `analyze_cspine_v7()` in cspine_v7.py.
Every measurement is a Euclidean distance between marker positions in patient
coordinates (mm). No intensity thresholds. No band-based segmentation. No
vendor-specific tunables.

Three functions:

  markers_to_slice_measurements(markers)
      Group markers by slice_inst, compute per-slice cord diameters, CSF
      spaces, and canal diameters from the marker positions.

  classify_severity(slice_measurements)
      Apply fixed millimeter thresholds to csf_space_min_mm per slice:
        <= 1.0 mm  -> CRITICAL
        <= 2.0 mm  -> MODERATE
        <= 3.5 mm  -> FINDING
        otherwise  -> NORMAL

  aggregate_levels(slice_measurements, vertebrae)
      Assign each slice to its nearest vertebral level by z, then take the
      worst severity per level.
"""
from __future__ import annotations
import numpy as np


# ── severity scale ─────────────────────────────────────────────────────────
# Order matters: index = severity rank, higher = worse.
SEVERITY_ORDER = ['NORMAL', 'FINDING', 'MODERATE', 'CRITICAL']
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}

# Threshold (csf_space_min_mm) → severity. Applied in order of decreasing
# strictness so the lowest matching threshold wins.
SEVERITY_THRESHOLDS_MM = [
    (1.0, 'CRITICAL'),
    (2.0, 'MODERATE'),
    (3.5, 'FINDING'),
]


# ── helpers ────────────────────────────────────────────────────────────────

def _dist_mm(a, b) -> float:
    """Euclidean distance between two xyz points (lists/tuples/arrays)."""
    if a is None or b is None:
        return float('nan')
    p = np.asarray(a, dtype=float)
    q = np.asarray(b, dtype=float)
    return float(np.linalg.norm(p - q))


def _group_by_slice_inst(markers: list) -> dict:
    """Bucket markers by slice_inst. Returns {slice_inst: {type: marker}}.
    If the same type appears more than once on a slice, the last one wins —
    v7 only places one marker per type per slice in normal operation.
    """
    by_slice: dict = {}
    for m in markers:
        inst = m.get('slice_inst')
        if inst is None:
            continue
        if inst not in by_slice:
            by_slice[inst] = {}
        by_slice[inst][m['type']] = m
    return by_slice


# ── 1. markers_to_slice_measurements ───────────────────────────────────────

def markers_to_slice_measurements(markers: list) -> list:
    """Convert a flat v7 marker list into one measurement dict per slice.

    Per slice the function attempts to compute every metric below. If a
    required marker is missing on a slice, the dependent metric is left as
    None (not zero, not omitted — explicit None so downstream callers can
    tell the difference between "this slice had no posterior CSF marker"
    and "posterior CSF was zero").

    Returned schema per slice:
        slice_inst, slice_idx, z_mm,
        cord_diameter_ap_mm, cord_diameter_lr_mm,
        csf_anterior_mm, csf_posterior_mm, csf_left_mm, csf_right_mm,
        canal_anterior_mm, canal_posterior_mm,
        canal_diameter_mm, csf_space_min_mm,
        cord_confidence  (float 0..1, from the cord_center marker)
    """
    by_slice = _group_by_slice_inst(markers)
    out = []

    # Slice insts in ascending order — stable output
    for inst in sorted(by_slice.keys()):
        ms = by_slice[inst]
        cc = ms.get('cord_center')
        if cc is None:
            # No cord_center on this slice → nothing measurable. Skip.
            # This is the v7 architectural contract: absence is a real
            # outcome, no placeholders.
            continue

        cc_xyz = cc.get('xyz_mm')
        slice_idx = cc.get('slice_idx')
        z_mm = float(cc_xyz[2]) if cc_xyz is not None and len(cc_xyz) >= 3 else None

        # Lookup helpers
        def xyz(t):
            m = ms.get(t)
            return m.get('xyz_mm') if m is not None else None

        csf_a_xyz = xyz('csf_anterior')
        csf_p_xyz = xyz('csf_posterior')
        csf_l_xyz = xyz('csf_left')
        csf_r_xyz = xyz('csf_right')
        wall_a_xyz = xyz('canal_wall_anterior')
        wall_p_xyz = xyz('canal_wall_posterior')
        wall_l_xyz = xyz('canal_wall_left')
        wall_r_xyz = xyz('canal_wall_right')

        # Cord diameters: opposing CSF marker to opposing CSF marker.
        # AP = anterior CSF position to posterior CSF position.
        cord_dia_ap = _dist_mm(csf_a_xyz, csf_p_xyz) if (csf_a_xyz and csf_p_xyz) else None
        cord_dia_lr = _dist_mm(csf_l_xyz, csf_r_xyz) if (csf_l_xyz and csf_r_xyz) else None

        # CSF spaces: cord center to the CSF marker in each direction.
        csf_anterior_mm  = _dist_mm(cc_xyz, csf_a_xyz) if csf_a_xyz else None
        csf_posterior_mm = _dist_mm(cc_xyz, csf_p_xyz) if csf_p_xyz else None
        csf_left_mm      = _dist_mm(cc_xyz, csf_l_xyz) if csf_l_xyz else None
        csf_right_mm     = _dist_mm(cc_xyz, csf_r_xyz) if csf_r_xyz else None

        # Canal walls (cord center to canal wall marker).
        canal_anterior_mm  = _dist_mm(cc_xyz, wall_a_xyz) if wall_a_xyz else None
        canal_posterior_mm = _dist_mm(cc_xyz, wall_p_xyz) if wall_p_xyz else None
        canal_left_mm      = _dist_mm(cc_xyz, wall_l_xyz) if wall_l_xyz else None
        canal_right_mm     = _dist_mm(cc_xyz, wall_r_xyz) if wall_r_xyz else None

        # csf_space_min_mm: min of the four CSF distances (those present).
        csf_distances = [d for d in (csf_anterior_mm, csf_posterior_mm,
                                      csf_left_mm, csf_right_mm)
                         if d is not None]
        csf_space_min_mm = min(csf_distances) if csf_distances else None

        # canal_diameter_mm: AP canal extent measured cord_center → wall_a
        # + cord_center → wall_p. Only defined when both walls are placed.
        if canal_anterior_mm is not None and canal_posterior_mm is not None:
            canal_diameter_mm = canal_anterior_mm + canal_posterior_mm
        else:
            canal_diameter_mm = None

        out.append({
            'slice_inst':           int(inst),
            'slice_idx':            int(slice_idx) if slice_idx is not None else None,
            'z_mm':                 z_mm,
            'cord_diameter_ap_mm':  cord_dia_ap,
            'cord_diameter_lr_mm':  cord_dia_lr,
            'csf_anterior_mm':      csf_anterior_mm,
            'csf_posterior_mm':     csf_posterior_mm,
            'csf_left_mm':          csf_left_mm,
            'csf_right_mm':         csf_right_mm,
            'canal_anterior_mm':    canal_anterior_mm,
            'canal_posterior_mm':   canal_posterior_mm,
            'canal_left_mm':        canal_left_mm,
            'canal_right_mm':       canal_right_mm,
            'canal_diameter_mm':    canal_diameter_mm,
            'csf_space_min_mm':     csf_space_min_mm,
            'cord_confidence':      float(cc.get('confidence', 0.0)),
        })

    return out


# ── 2. classify_severity ───────────────────────────────────────────────────

def classify_severity(slice_measurements: list) -> list:
    """Per-slice severity from csf_space_min_mm.

    Thresholds (mm):
      <= 1.0  -> CRITICAL
      <= 2.0  -> MODERATE
      <= 3.5  -> FINDING
      else    -> NORMAL

    If csf_space_min_mm is None on a slice, severity is set to 'UNMEASURED'
    so downstream code can distinguish a slice that couldn't be measured
    from one that measured normal.

    Returns a new list of dicts — the originals are not mutated.
    """
    out = []
    for s in slice_measurements:
        sev = _csf_min_to_severity(s.get('csf_space_min_mm'))
        rec = dict(s)
        rec['severity'] = sev
        out.append(rec)
    return out


def _csf_min_to_severity(csf_min_mm) -> str:
    """Apply thresholds in strict-to-loose order; first match wins."""
    if csf_min_mm is None:
        return 'UNMEASURED'
    try:
        v = float(csf_min_mm)
    except (TypeError, ValueError):
        return 'UNMEASURED'
    for limit_mm, label in SEVERITY_THRESHOLDS_MM:
        if v <= limit_mm:
            return label
    return 'NORMAL'


# ── 3. aggregate_levels ────────────────────────────────────────────────────

def aggregate_levels(slice_measurements: list, vertebrae: list) -> list:
    """Assign each slice to its nearest vertebral level by z, then take the
    worst severity per level.

    Input:
      slice_measurements: list of dicts (output of classify_severity OR of
                          markers_to_slice_measurements — if 'severity' is
                          missing, it's computed here).
      vertebrae:          list of dicts. Each MUST contain:
                            - either 'centroid_z_mm' or 'z_mm'  (z in mm)
                          Each MAY contain:
                            - 'level' or 'name'   (human label like 'C5')
                            - 'V_idx' or 'idx'    (numeric label)
                          The level identifier used in the output is the
                          first present of: level, name, V_idx, idx.

    Output:
      list of per-level dicts, sorted superior-to-inferior by vertebra z:
        {
          'level':                <level identifier>,
          'level_z_mm':           <vertebra z>,
          'n_slices':             <slice count assigned to this level>,
          'worst_severity':       <SEVERITY_ORDER label>,
          'worst_csf_space_min_mm': <the csf_space_min_mm that determined it>,
          'worst_slice_inst':     <slice_inst of the worst slice>,
          'severity_counts':      {severity: count, ...},
        }

      Levels with no slices assigned still appear in the output with
      n_slices=0 and worst_severity='UNMEASURED'.
    """
    if not vertebrae:
        return []

    # Normalize vertebra records
    verts = []
    for v in vertebrae:
        z = v.get('centroid_z_mm')
        if z is None:
            z = v.get('z_mm')
        if z is None:
            continue
        # Pick a level identifier
        label = (v.get('level')
                 or v.get('name')
                 or v.get('V_idx')
                 or v.get('idx'))
        if label is None:
            continue
        verts.append({'level': label, 'z_mm': float(z)})
    if not verts:
        return []

    # Sort superior to inferior (higher z first in DICOM patient coords:
    # +z = cranial). Output order follows this.
    verts.sort(key=lambda v: -v['z_mm'])

    # Ensure each slice carries a severity. If not present, compute it now.
    slices_with_sev = []
    for s in slice_measurements:
        rec = dict(s)
        if 'severity' not in rec:
            rec['severity'] = _csf_min_to_severity(rec.get('csf_space_min_mm'))
        slices_with_sev.append(rec)

    # Assign each slice to its nearest vertebra by z (skip slices with no z).
    buckets = {v['level']: [] for v in verts}
    for s in slices_with_sev:
        sz = s.get('z_mm')
        if sz is None:
            continue
        # Find vertebra with minimum |Δz|
        best_v = min(verts, key=lambda v: abs(v['z_mm'] - sz))
        buckets[best_v['level']].append(s)

    # Aggregate
    out = []
    for v in verts:
        slices = buckets[v['level']]
        n_slices = len(slices)
        if n_slices == 0:
            out.append({
                'level':                  v['level'],
                'level_z_mm':             v['z_mm'],
                'n_slices':               0,
                'worst_severity':         'UNMEASURED',
                'worst_csf_space_min_mm': None,
                'worst_slice_inst':       None,
                'severity_counts':        {},
            })
            continue

        # Severity counts and worst (highest-rank) severity
        sev_counts: dict = {}
        worst_rank = -1
        worst_slice = None
        worst_csf_min = None
        for s in slices:
            sev = s.get('severity', 'UNMEASURED')
            sev_counts[sev] = sev_counts.get(sev, 0) + 1
            rank = SEVERITY_RANK.get(sev, -1)
            if rank > worst_rank:
                worst_rank = rank
                worst_slice = s
                worst_csf_min = s.get('csf_space_min_mm')

        worst_sev = (SEVERITY_ORDER[worst_rank] if 0 <= worst_rank < len(SEVERITY_ORDER)
                     else 'UNMEASURED')

        out.append({
            'level':                  v['level'],
            'level_z_mm':             v['z_mm'],
            'n_slices':               n_slices,
            'worst_severity':         worst_sev,
            'worst_csf_space_min_mm': worst_csf_min,
            'worst_slice_inst':       worst_slice.get('slice_inst') if worst_slice else None,
            'severity_counts':        sev_counts,
        })

    return out
