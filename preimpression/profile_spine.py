"""
profile_spine.py — Continuous z-profile of cord/canal metrics and anomaly detection
====================================================================================
Input:
    cord_canal_per_slice.csv  (output of measure_spine.py)
Output:
    continuous_profile.csv    one row per 1mm of z, interpolated between acquired
                               slices, with cord area, centroid, CSF spaces, canal
                               diameter, csf_min, csf_lr_ratio
    anomaly_profile.csv       one row per z position whose ANY metric deviates
                               > 2σ from a ±10mm local window
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path
import numpy as np


METRICS = [
    'cord_area_mm2',
    'cord_centroid_x_mm',
    'cord_centroid_y_mm',
    'csf_anterior_mm',
    'csf_posterior_mm',
    'csf_left_mm',
    'csf_right_mm',
    'canal_diameter_mm',
    'csf_min_mm',
    'csf_lr_ratio',
]

# Map the per-slice CSV column names → metric names used here
SOURCE_COLS = {
    'cord_area_mm2':       'cord_area_mm2',
    'cord_centroid_x_mm':  'cord_x_mm',
    'cord_centroid_y_mm':  'cord_y_mm',
    'csf_anterior_mm':     'csf_space_anterior_mm',
    'csf_posterior_mm':    'csf_space_posterior_mm',
    'csf_left_mm':         'csf_space_left_mm',
    'csf_right_mm':        'csf_space_right_mm',
    'csf_min_mm':          'csf_space_min_mm',
    'csf_lr_ratio':        'csf_lr_ratio',
    # canal_diameter_mm is derived: ant + post + cord AP diameter
    # We compute it = csf_anterior + csf_posterior + (cord_radius_ant + cord_radius_post)
}


def _f(v):
    """Parse a CSV cell as float, returning np.nan for empty/non-numeric."""
    if v is None or v == '':
        return np.nan
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def load_per_slice(csv_path: Path) -> list[dict]:
    """Return per-slice records sorted by z, with metric fields extracted as
    floats (np.nan when missing)."""
    out = []
    with open(csv_path, newline='') as f:
        for r in csv.DictReader(f):
            # Only use slices where cord was actually found (cord_found=True
            # AND cord_confidence != INVALID). Other rows have no measurements
            # and pollute interpolation with nan that linear-fill might bridge.
            if r.get('cord_found', 'False') != 'True':
                continue
            if r.get('cord_confidence', '') == 'INVALID':
                continue
            z = _f(r.get('z_mm'))
            if np.isnan(z):
                continue
            # Build the metric record
            rec = {'z_mm': z,
                   'cord_confidence': r.get('cord_confidence', '')}
            for m, src_col in SOURCE_COLS.items():
                rec[m] = _f(r.get(src_col))
            # Canal AP diameter = cord_radius_ant + cord_radius_post + csf_ant + csf_post
            cr_a = _f(r.get('cord_radius_anterior_mm'))
            cr_p = _f(r.get('cord_radius_posterior_mm'))
            cs_a = rec['csf_anterior_mm']
            cs_p = rec['csf_posterior_mm']
            if not any(np.isnan(v) for v in (cr_a, cr_p, cs_a, cs_p)):
                rec['canal_diameter_mm'] = cr_a + cr_p + cs_a + cs_p
            else:
                rec['canal_diameter_mm'] = np.nan
            out.append(rec)
    out.sort(key=lambda r: r['z_mm'])
    return out


def build_continuous_profile(
    slices: list[dict],
    step_mm: float = 1.0,
) -> list[dict]:
    """Interpolate each metric linearly across z at step_mm spacing.

    z grid: from floor(min_z) to ceil(max_z) inclusive, step_mm.
    Each metric is interpolated separately using only the slices where that
    metric is not nan. If a metric has fewer than 2 valid samples, it's
    returned as nan everywhere.
    """
    if not slices:
        return []

    zs_all = np.array([r['z_mm'] for r in slices], dtype=float)
    z_lo = float(np.floor(zs_all.min()))
    z_hi = float(np.ceil(zs_all.max()))
    n_steps = int(round((z_hi - z_lo) / step_mm)) + 1
    z_grid = np.linspace(z_lo, z_hi, n_steps)

    # Per-metric interpolation
    profile = [{'z_mm': float(z)} for z in z_grid]

    for metric in METRICS:
        vals = np.array([r[metric] for r in slices], dtype=float)
        mask = ~np.isnan(vals)
        if mask.sum() < 2:
            # not enough samples to interpolate
            for p in profile:
                p[metric] = float('nan')
            continue
        zs_valid = zs_all[mask]
        vs_valid = vals[mask]
        # numpy.interp does linear interpolation; outside the data range it
        # returns the boundary value. That's fine for our case — we don't
        # invent measurements past the acquired range, but the z_grid was
        # built from the acquired range so this only affects exact endpoints.
        interp = np.interp(z_grid, zs_valid, vs_valid)
        for i, p in enumerate(profile):
            p[metric] = float(interp[i])

    return profile


def detect_anomalies(
    profile: list[dict],
    window_mm: float = 10.0,
    n_sigma: float = 2.0,
    edge_margin_mm: float = 10.0,
    lateral_noise_metrics: tuple = ('csf_lr_ratio', 'csf_left_mm', 'csf_right_mm'),
    slice_records: list[dict] = None,
) -> list[dict]:
    """For each z position and each metric, compute the local mean and stdev
    using a ±window_mm window (excluding the point itself). Flag z positions
    where any metric deviates more than n_sigma * local_std from local_mean.

    Filters applied:
      - Suppress flags within edge_margin_mm of the stack top or bottom
        (insufficient neighbors → noisy σ).
      - Suppress positions whose ONLY firing metrics are in
        lateral_noise_metrics (csf_lr_ratio, csf_left, csf_right alone).
        These are inherently noisy. A position must have at least one
        non-lateral-only metric flagged to be reported.

    Annotation:
      - nearest_slice_conf: confidence of the closest acquired slice to each
        flagged z. HIGH = anomaly is between reliable measurements. LOW =
        anomaly may be a LOW-confidence measurement artifact.
    """
    if not profile:
        return []

    zs = np.array([p['z_mm'] for p in profile], dtype=float)
    step = zs[1] - zs[0] if len(zs) > 1 else 1.0
    win_pts = max(2, int(round(window_mm / step)))

    z_lo = float(zs.min())
    z_hi = float(zs.max())
    lateral_set = set(lateral_noise_metrics)

    anomaly_rows = []
    for i, p in enumerate(profile):
        z = p['z_mm']
        # Edge-of-volume filter
        if z - z_lo < edge_margin_mm or z_hi - z < edge_margin_mm:
            continue

        flagged = []
        details = {}
        for metric in METRICS:
            val = p[metric]
            if val is None or (isinstance(val, float) and np.isnan(val)):
                continue
            lo = max(0, i - win_pts)
            hi = min(len(profile), i + win_pts + 1)
            neighbors = [profile[j][metric] for j in range(lo, hi)
                         if j != i
                         and not (isinstance(profile[j][metric], float)
                                  and np.isnan(profile[j][metric]))]
            if len(neighbors) < 3:
                continue
            nbr_arr = np.array(neighbors, dtype=float)
            mu = float(nbr_arr.mean())
            sd = float(nbr_arr.std())
            if sd <= 0:
                continue
            dev = abs(val - mu) / sd
            if dev > n_sigma:
                flagged.append(metric)
                details[metric] = {
                    'value':   round(float(val), 3),
                    'local_mean':  round(mu, 3),
                    'local_std':   round(sd, 3),
                    'sigma_dev':   round(dev, 2),
                }
        if not flagged:
            continue

        # Lateral-only filter: at least one non-lateral metric must fire.
        non_lateral_flags = [m for m in flagged if m not in lateral_set]
        if not non_lateral_flags:
            continue

        # Annotate with nearest acquired slice confidence
        nearest_conf = ''
        if slice_records:
            best_d = 1e9
            for s in slice_records:
                sz = s.get('z_mm')
                if sz is None:
                    continue
                d = abs(sz - p['z_mm'])
                if d < best_d:
                    best_d = d
                    nearest_conf = s.get('cord_confidence', '')

        anomaly_rows.append({
            'z_mm':            round(p['z_mm'], 1),
            'n_metrics_flag':  len(flagged),
            'metrics_flag':    ';'.join(flagged),
            'nearest_slice_conf': nearest_conf,
            'details':         '; '.join(
                f"{m}={details[m]['value']} "
                f"(μ={details[m]['local_mean']},σ={details[m]['local_std']},"
                f"dev={details[m]['sigma_dev']}σ)"
                for m in flagged
            ),
        })

    return anomaly_rows


def _write_csv(rows: list[dict], path: Path, fieldnames: list[str]) -> None:
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fieldnames})


def run(per_slice_csv: str, out_dir: str = '.') -> None:
    per_slice_csv = Path(per_slice_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    slices = load_per_slice(per_slice_csv)
    print(f'Loaded {len(slices)} usable slices from {per_slice_csv.name}')
    if not slices:
        print('No usable slices — nothing to profile.')
        return

    print(f'Acquired z range: {slices[0]["z_mm"]:.2f} to {slices[-1]["z_mm"]:.2f} mm')

    profile = build_continuous_profile(slices, step_mm=1.0)
    print(f'Continuous profile: {len(profile)} z positions at 1mm spacing')

    # Round metrics for output readability
    for p in profile:
        for m in METRICS:
            v = p.get(m)
            if isinstance(v, float) and not np.isnan(v):
                p[m] = round(v, 3)
            elif isinstance(v, float) and np.isnan(v):
                p[m] = ''

    prof_path = out_dir / 'continuous_profile.csv'
    _write_csv(profile, prof_path, ['z_mm'] + METRICS)
    print(f'Wrote {prof_path}')

    # Anomaly detection runs on the raw (non-rounded) numerics, so re-load
    # numerics from the just-written CSV would round-trip. Instead, rebuild
    # the float-typed profile for the detector.
    profile_floats = build_continuous_profile(slices, step_mm=1.0)
    anomalies = detect_anomalies(profile_floats, window_mm=10.0, n_sigma=2.0,
                                 slice_records=slices)
    print(f'Anomaly detection: {len(anomalies)} z positions flagged '
          f'(any metric > 2σ from ±10mm window)')

    anom_path = out_dir / 'anomaly_profile.csv'
    _write_csv(anomalies, anom_path,
               ['z_mm', 'n_metrics_flag', 'metrics_flag',
                'nearest_slice_conf', 'details'])
    print(f'Wrote {anom_path}')

    # Brief summary
    if anomalies:
        from collections import Counter
        metric_counts = Counter()
        for a in anomalies:
            for m in a['metrics_flag'].split(';'):
                metric_counts[m] += 1
        print('\nMetric flag counts (across all flagged z):')
        for m, c in metric_counts.most_common():
            print(f'  {m}: {c}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Continuous z-profile and 2σ local-window anomaly detector'
    )
    ap.add_argument('per_slice_csv',
                    help='cord_canal_per_slice.csv from measure_spine.py')
    ap.add_argument('--out-dir', default='.',
                    help='directory for output CSVs (default: cwd)')
    args = ap.parse_args()
    run(args.per_slice_csv, args.out_dir)
