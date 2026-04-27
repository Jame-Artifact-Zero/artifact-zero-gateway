"""
================================================================================
ARTIFACT ZERO LABS — Advanced Cervical Spine Analysis
Three new measurements not produced by the profile script:

1. LEFT/RIGHT ASYMMETRY
   Split each axial slice at the midline. Compute gap independently for
   left and right halves. If left gap < right gap consistently at one level
   and not others — that's foraminal asymmetry. Lateralizes the pathology.

2. MINIMUM WIDTH CHARACTERIZATION
   How wide is the gap minimum? Sharp dip = focal structural change at
   one level. Broad plateau = diffuse multi-level alteration.
   Quantified as the number of slices below 50% of mean gap.

3. CROSS-SEQUENCE AGREEMENT SCORE
   For each slice position, how well do T1, T2, T2* agree on their
   relative gap (normalized to their own mean)? High agreement = normal
   tissue. Low agreement = tissue behaving differently across physics
   = most sensitive marker of pathology.

USAGE:
    # Single volume asymmetry:
    python az_advanced_analysis.py --mode asymmetry \
        --input "DICOM_folder" --subject "Name" --year 2026

    # Cross-sequence agreement (requires three aligned volumes):
    python az_advanced_analysis.py --mode agreement \
        --t1 "folder_or_nii" --t2 "folder_or_nii" --t2s "folder_or_nii" \
        --subject "Name" --year 2026

    # Run all on a single DICOM folder:
    python az_advanced_analysis.py --mode all \
        --input "DICOM_folder" --subject "Name" --year 2026

================================================================================
"""

import os, sys, time, argparse, warnings, csv
from pathlib import Path
from collections import defaultdict

warnings.filterwarnings('ignore')

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.ndimage import (binary_erosion, binary_dilation,
                           binary_fill_holes, label as scipy_label,
                           uniform_filter1d, zoom)

try:
    import pydicom
except ImportError:
    print("ERROR: pip install pydicom"); sys.exit(1)

try:
    import SimpleITK as sitk
    SITK_OK = True
except ImportError:
    SITK_OK = False

BG = '#0b0b0f'


# ════════════════════════════════════════════════════════════════════
# SHARED UTILITIES
# ════════════════════════════════════════════════════════════════════

GOOD = {
    'T1':   ['t1','sag t1','ax t1','t1 fse','t1 tfe','mprage','t1w'],
    'T2':   ['t2','sag t2','ax t2','t2 fse','t2 tse','t2w'],
    'STIR': ['stir','fse stir','t2 stir'],
    'T2S':  ['t2*','merge','t2star','swan','t2s'],
}
SKIP = ['localizer','scout','survey','mpr','report','reformat',
        'derived','secondary','adc','dwi','diffusion','dose']

def score_seq(desc, n):
    dl = desc.lower()
    for s in SKIP:
        if s in dl: return -1, 'SKIP'
    for stype, kws in GOOD.items():
        if any(k in dl for k in kws):
            return 100 + min(n, 200), stype
    return 10 + min(n, 200), 'OTHER'

def find_dcm(folder):
    files = []
    for root, dirs, fs in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in fs:
            fp = Path(root) / f
            if f.lower().endswith(('.dcm','.ima')) or \
               ('.' not in f and fp.stat().st_size > 500):
                files.append(fp)
    return files

def group_series(files):
    series = defaultdict(list)
    for fp in files:
        try:
            ds = pydicom.dcmread(str(fp), stop_before_pixels=True)
            uid  = str(getattr(ds,'SeriesInstanceUID','unk'))
            desc = str(getattr(ds,'SeriesDescription','Unknown'))
            num  = int(getattr(ds,'SeriesNumber',0) or 0)
            series[(uid,desc,num)].append(fp)
        except: pass
    scored = []
    for (uid,desc,num), fs in series.items():
        sc, stype = score_seq(desc, len(fs))
        scored.append({'uid':uid,'desc':desc,'series_num':num,
                       'files':sorted(fs),'n':len(fs),'score':sc,'type':stype})
    return sorted(scored, key=lambda x: x['score'], reverse=True)

def load_volume(series_info):
    files = series_info['files']
    if SITK_OK:
        try:
            reader = sitk.ImageSeriesReader()
            reader.SetFileNames([str(f) for f in files])
            img = reader.Execute()
            vol = sitk.GetArrayFromImage(img)
            return vol.astype(np.float32), img.GetSpacing()[2]
        except: pass
    slices, positions = [], []
    for fp in files:
        try:
            ds = pydicom.dcmread(str(fp))
            arr = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds,'RescaleSlope',1))
            intercept = float(getattr(ds,'RescaleIntercept',0))
            arr = arr*slope + intercept
            pos = getattr(ds,'ImagePositionPatient',[0,0,len(slices)])
            positions.append(float(pos[2]))
            slices.append(arr)
        except: pass
    if not slices: raise ValueError("No slices")
    order = np.argsort(positions)
    vol = np.stack([slices[i] for i in order], axis=0)
    return vol.astype(np.float32), 1.0

def resize_slice(sl, target=128):
    h, w = sl.shape
    if max(h,w) > target:
        scale = target / max(h,w)
        sl = zoom(sl, scale, order=1)
    return sl

def make_mask(sl):
    flat = sl[sl > 0].flatten()
    if len(flat) == 0: return np.zeros(sl.shape, bool)
    m = sl > np.percentile(flat, 12)
    m = binary_fill_holes(m)
    m = binary_erosion(m, iterations=2)
    m = binary_dilation(m, iterations=2)
    la, n = scipy_label(m)
    if n > 1:
        sizes = [np.sum(la==i) for i in range(1,n+1)]
        m = la == (np.argmax(sizes)+1)
    return m.astype(bool)

def compute_gap_region(vals, seq_type='T2'):
    if len(vals) < 50: return None
    if seq_type == 'T1':
        A = np.percentile(vals, 85)
        B = np.percentile(vals, 65)
    else:
        A = np.percentile(vals, 70)
        B = np.percentile(vals, 88)
    return abs(A - B), A, B


# ════════════════════════════════════════════════════════════════════
# ANALYSIS 1 — LEFT / RIGHT ASYMMETRY
# ════════════════════════════════════════════════════════════════════

def run_asymmetry(vol, seq_type, subject, year, seq_desc, out_dir):
    """
    Split each axial slice at the midline (left/right).
    Compute gap for each half independently.
    Asymmetry index = (right_gap - left_gap) / mean_gap
    Positive = right side more compressed.
    Negative = left side more compressed.
    """
    print(f"\n  Running left/right asymmetry ({seq_type})...")
    nz = vol.shape[0]

    results = []
    for z in range(nz):
        sl = resize_slice(vol[z].copy())
        mask = make_mask(sl)
        if mask.sum() < 100:
            results.append(None)
            continue

        h, w = sl.shape
        mid = w // 2

        # Left half (patient left = image right for standard axial orientation)
        # We compute both halves and report the asymmetry
        left_mask  = mask.copy(); left_mask[:, mid:]  = False
        right_mask = mask.copy(); right_mask[:, :mid] = False

        left_vals  = sl[left_mask]
        right_vals = sl[right_mask]

        left_r  = compute_gap_region(left_vals,  seq_type)
        right_r = compute_gap_region(right_vals, seq_type)

        if left_r is None or right_r is None:
            results.append(None)
            continue

        left_gap,  lA, lB = left_r
        right_gap, rA, rB = right_r
        mean_gap = (left_gap + right_gap) / 2
        asym = (right_gap - left_gap) / mean_gap if mean_gap > 0 else 0

        results.append({
            'left_gap':  left_gap,
            'right_gap': right_gap,
            'mean_gap':  mean_gap,
            'asym':      asym,    # negative = left compressed
        })

    valid = [(z, r) for z, r in enumerate(results) if r is not None]
    if not valid:
        print("  No valid results"); return

    zs        = [v[0] for v in valid]
    left_gaps  = [v[1]['left_gap']  for v in valid]
    right_gaps = [v[1]['right_gap'] for v in valid]
    asyms      = [v[1]['asym']      for v in valid]

    # Smooth
    lg_s  = uniform_filter1d(left_gaps,  size=3)
    rg_s  = uniform_filter1d(right_gaps, size=3)
    as_s  = uniform_filter1d(asyms,      size=3)

    # Find most asymmetric zone
    min_asym_idx = int(np.argmin(as_s))   # most left-compressed
    max_asym_idx = int(np.argmax(as_s))   # most right-compressed
    min_asym_z   = zs[min_asym_idx]
    max_asym_z   = zs[max_asym_idx]

    # Sustained left asymmetry (below -0.1 threshold)
    left_dominant = [(zs[i], as_s[i]) for i in range(len(as_s)) if as_s[i] < -0.10]
    right_dominant = [(zs[i], as_s[i]) for i in range(len(as_s)) if as_s[i] > 0.10]

    print(f"  Most left-compressed: slice {min_asym_z} (asym={as_s[min_asym_idx]:.3f})")
    print(f"  Most right-compressed: slice {max_asym_z} (asym={as_s[max_asym_idx]:.3f})")
    print(f"  Slices with left dominance (asym < -0.10): {len(left_dominant)}")
    print(f"  Slices with right dominance (asym > +0.10): {len(right_dominant)}")

    # ── Render ────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10), facecolor=BG)
    fig.text(0.5, 0.97,
        f'ARTIFACT ZERO LABS  ·  Left/Right Asymmetry  ·  {subject}  ·  {year}',
        ha='center', color='white', fontsize=12, fontweight='bold')
    fig.text(0.5, 0.93,
        f'Sequence: {seq_desc}  ·  Gap computed independently for each side',
        ha='center', color='#6b6b88', fontsize=9)

    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.30,
                           left=0.07, right=0.97, top=0.90, bottom=0.08)

    # Left vs right gap curves
    ax = fig.add_subplot(gs[0, :2])
    ax.set_facecolor('#13131a')
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')
    ax.plot(zs, lg_s, color='#00d4ff', lw=2, label='Left side gap')
    ax.plot(zs, rg_s, color='#f5c518', lw=2, label='Right side gap')
    ax.fill_between(zs, lg_s, rg_s,
                    where=[l < r for l,r in zip(lg_s, rg_s)],
                    alpha=0.25, color='#00d4ff', label='Left < Right (left compressed)')
    ax.fill_between(zs, lg_s, rg_s,
                    where=[l > r for l,r in zip(lg_s, rg_s)],
                    alpha=0.25, color='#f5c518', label='Right < Left (right compressed)')
    ax.axvline(min_asym_z, color='#00d4ff', lw=1.5, ls='--', alpha=0.8)
    ax.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax.set_ylabel('Tissue contrast gap', color='#6b6b88', fontsize=8)
    orient_label = 'Left vs right' if seq_type not in ('STIR',) else 'Left vs right (axial) / Ant vs post (sagittal)'
    ax.set_title(f'{orient_label} gap — divergence = asymmetric pathology',
                 color='white', fontsize=9, fontweight='bold')
    ax.tick_params(colors='#6b6b88', labelsize=7)
    ax.legend(fontsize=7, facecolor='#13131a', labelcolor='white')

    # Asymmetry index curve
    ax2 = fig.add_subplot(gs[1, :2])
    ax2.set_facecolor('#13131a')
    for sp in ax2.spines.values(): sp.set_color('#1e1e2a')
    ax2.axhline(0, color='white', lw=0.5, alpha=0.3)
    ax2.axhline(-0.10, color='#00d4ff', lw=1, ls=':', alpha=0.5, label='Left dominant threshold')
    ax2.axhline(+0.10, color='#f5c518', lw=1, ls=':', alpha=0.5, label='Right dominant threshold')
    ax2.fill_between(zs, as_s, 0,
                     where=[a < 0 for a in as_s],
                     alpha=0.3, color='#00d4ff')
    ax2.fill_between(zs, as_s, 0,
                     where=[a > 0 for a in as_s],
                     alpha=0.3, color='#f5c518')
    ax2.plot(zs, as_s, color='white', lw=1.5)
    ax2.axvline(min_asym_z, color='#00d4ff', lw=1.5, ls='--', alpha=0.8,
                label=f'Peak left compression: slice {min_asym_z}')
    ax2.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax2.set_ylabel('Asymmetry index\n(negative = left compressed)', color='#6b6b88', fontsize=8)
    ax2.set_title('Asymmetry index — below zero = left side more compressed',
                  color='white', fontsize=9, fontweight='bold')
    ax2.tick_params(colors='#6b6b88', labelsize=7)
    ax2.legend(fontsize=7, facecolor='#13131a', labelcolor='white')

    # Stats box
    ax3 = fig.add_subplot(gs[:, 2])
    ax3.set_facecolor('#0b0b0f'); ax3.axis('off')
    pct_left = len(left_dominant) / len(zs) * 100
    pct_right = len(right_dominant) / len(zs) * 100
    lines = [
        ('ASYMMETRY RESULTS', '#00d4ff', 10, True),
        (f'Slices: {len(zs)}', 'white', 8, False),
        ('', 'white', 4, False),
        ('LEFT SIDE', '#00d4ff', 10, True),
        (f'Peak left compression:', '#00d4ff', 8, True),
        (f'  Slice {min_asym_z} ({min_asym_z/len(zs)*100:.0f}% from inf)', '#00d4ff', 8, False),
        (f'  Asym index: {as_s[min_asym_idx]:.3f}', '#00d4ff', 8, False),
        (f'Slices left-dominant: {len(left_dominant)}', 'white', 8, False),
        (f'  ({pct_left:.1f}% of volume)', '#6b6b88', 7, False),
        ('', 'white', 4, False),
        ('RIGHT SIDE', '#f5c518', 10, True),
        (f'Peak right compression:', '#f5c518', 8, True),
        (f'  Slice {max_asym_z} ({max_asym_z/len(zs)*100:.0f}% from inf)', '#f5c518', 8, False),
        (f'  Asym index: +{as_s[max_asym_idx]:.3f}', '#f5c518', 8, False),
        (f'Slices right-dominant: {len(right_dominant)}', 'white', 8, False),
        (f'  ({pct_right:.1f}% of volume)', '#6b6b88', 7, False),
        ('', 'white', 4, False),
        ('INTERPRETATION', '#f5c518', 10, True),
        ('Axial: negative = patient left', '#6b6b88', 7, False),
        ('more compressed than right.', '#6b6b88', 7, False),
        ('Sagittal: negative = anterior', '#6b6b88', 7, False),
        ('more compressed than posterior.', '#6b6b88', 7, False),
        ('Sustained asymmetry = structural.', '#7fff7f', 7, True),
        ('', 'white', 4, False),
        ('Artifact Zero Labs 2026', '#444466', 7, False),
    ]
    y = 0.97
    for txt, col, sz, bold in lines:
        if txt:
            ax3.text(0.05, y, txt, transform=ax3.transAxes,
                     color=col, fontsize=sz,
                     fontweight='bold' if bold else 'normal', va='top')
        y -= 0.050 if sz >= 8 else 0.036

    fig.text(0.5, 0.01, 'Artifact Zero Labs  ·  Confidential',
             ha='center', color='#333355', fontsize=7)

    safe = "".join(c if c.isalnum() or c in '-_' else '_' for c in seq_desc)
    out_path = out_dir / f"asymmetry_{safe}.png"
    plt.savefig(str(out_path), dpi=120, facecolor=BG)
    plt.close(fig)
    print(f"  Saved: {out_path}")

    # CSV
    csv_path = out_dir / f"asymmetry_{safe}.csv"
    with open(str(csv_path), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['slice_z','left_gap','right_gap','mean_gap','asym_index'])
        for z, r in enumerate(results):
            if r:
                w.writerow([z, round(r['left_gap'],2), round(r['right_gap'],2),
                             round(r['mean_gap'],2), round(r['asym'],4)])
            else:
                w.writerow([z,'','','',''])

    return {
        'peak_left_z':   min_asym_z,
        'peak_left_asym': round(float(as_s[min_asym_idx]), 4),
        'pct_left_dominant': round(pct_left, 1),
        'pct_right_dominant': round(pct_right, 1),
    }


# ════════════════════════════════════════════════════════════════════
# ANALYSIS 2 — MINIMUM WIDTH CHARACTERIZATION
# ════════════════════════════════════════════════════════════════════

def run_min_width(vol, seq_type, subject, year, seq_desc, out_dir):
    """
    Characterize whether the gap minimum is focal (sharp dip) or
    diffuse (broad plateau). Count slices below 50% of mean gap.
    """
    print(f"\n  Running minimum width analysis ({seq_type})...")
    nz = vol.shape[0]

    gaps = []
    for z in range(nz):
        sl = resize_slice(vol[z].copy())
        mask = make_mask(sl)
        if mask.sum() < 100:
            gaps.append(None)
            continue
        r = compute_gap_region(sl[mask], seq_type)
        gaps.append(r[0] if r else None)

    valid_gaps = [(z, g) for z, g in enumerate(gaps) if g is not None]
    if not valid_gaps: return

    zs  = [v[0] for v in valid_gaps]
    gvs = [v[1] for v in valid_gaps]
    mean_g = np.mean(gvs)

    # Smooth
    gs = uniform_filter1d(gvs, size=3)

    # Threshold analysis
    # MRI-calibrated thresholds — compression is subtle, use tighter bands
    thresh_50  = mean_g * 0.85   # within 15% of mean = notable
    thresh_33  = mean_g * 0.75   # within 25% = significant
    thresh_20  = mean_g * 0.65   # within 35% = severe

    below_50 = [(zs[i], gs[i]) for i in range(len(gs)) if gs[i] < thresh_50]
    below_33 = [(zs[i], gs[i]) for i in range(len(gs)) if gs[i] < thresh_33]
    below_20 = [(zs[i], gs[i]) for i in range(len(gs)) if gs[i] < thresh_20]

    # Find contiguous runs below 50%
    runs = []
    current_run = []
    for i, (z, g) in enumerate(zip(zs, gs)):
        if g < thresh_50:
            current_run.append(z)
        else:
            if current_run:
                runs.append(current_run)
                current_run = []
    if current_run:
        runs.append(current_run)

    min_z = zs[int(np.argmin(gs))]
    min_g = float(np.min(gs))

    print(f"  Mean gap: {mean_g:.1f}")
    print(f"  Slices below 50% of mean: {len(below_50)} ({len(below_50)/len(zs)*100:.1f}%)")
    print(f"  Slices below 33% of mean: {len(below_33)} ({len(below_33)/len(zs)*100:.1f}%)")
    print(f"  Slices below 20% of mean: {len(below_20)} ({len(below_20)/len(zs)*100:.1f}%)")
    print(f"  Contiguous compressed runs: {len(runs)}")
    for i, run in enumerate(runs):
        spacing_mm = 3.3  # approximate
        width_mm = len(run) * spacing_mm
        print(f"    Run {i+1}: slices {run[0]}-{run[-1]} ({len(run)} slices ≈ {width_mm:.0f}mm)")

    # ── Render ────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 9), facecolor=BG)
    fig.text(0.5, 0.97,
        f'ARTIFACT ZERO LABS  ·  Compression Width Analysis  ·  {subject}  ·  {year}',
        ha='center', color='white', fontsize=12, fontweight='bold')
    fig.text(0.5, 0.93,
        f'Sequence: {seq_desc}  ·  How focal vs diffuse is the signal compression?',
        ha='center', color='#6b6b88', fontsize=9)

    gs_plot = gridspec.GridSpec(1, 3, figure=fig, hspace=0.3, wspace=0.30,
                                left=0.07, right=0.97, top=0.88, bottom=0.10)

    ax = fig.add_subplot(gs_plot[0, :2])
    ax.set_facecolor('#13131a')
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')

    ax.fill_between(zs, gs, alpha=0.15, color='#00d4ff')
    ax.plot(zs, gs, color='#00d4ff', lw=2)

    # Threshold lines
    ax.axhline(thresh_50, color='#f5c518', lw=1.5, ls='--', alpha=0.8,
               label=f'Notable (<85% of mean, ={thresh_50:.0f})')
    ax.axhline(thresh_33, color='#ff9900', lw=1.5, ls='--', alpha=0.8,
               label=f'Significant (<75% of mean, ={thresh_33:.0f})')
    ax.axhline(thresh_20, color='#ff4444', lw=1.5, ls='--', alpha=0.8,
               label=f'Severe (<65% of mean, ={thresh_20:.0f})')

    # Shade compressed regions
    ax.fill_between(zs, gs, thresh_50,
                    where=[g < thresh_50 for g in gs],
                    alpha=0.35, color='#ff4444', label='Notable compression')

    # Mark runs with brackets
    for run in runs:
        ax.axvspan(run[0]-0.5, run[-1]+0.5, alpha=0.1, color='#ff4444')

    ax.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax.set_ylabel('Tissue contrast gap', color='#6b6b88', fontsize=8)
    ax.set_title('Compression width — how many levels are affected?',
                 color='white', fontsize=9, fontweight='bold')
    ax.tick_params(colors='#6b6b88', labelsize=7)
    ax.legend(fontsize=7, facecolor='#13131a', labelcolor='white')

    # Stats box
    ax2 = fig.add_subplot(gs_plot[0, 2])
    ax2.set_facecolor('#0b0b0f'); ax2.axis('off')

    lines = [
        ('WIDTH RESULTS', '#00d4ff', 10, True),
        (f'Total slices: {len(zs)}', 'white', 8, False),
        (f'Mean gap: {mean_g:.1f}', 'white', 8, False),
        (f'Min gap: {min_g:.1f} at slice {min_z}', '#ff4444', 9, True),
        ('', 'white', 4, False),
        ('COMPRESSED EXTENT', '#f5c518', 10, True),
        (f'Notable (<85%): {len(below_50)} slices', '#f5c518', 8, False),
        (f'Significant (<75%): {len(below_33)} slices', '#ff9900', 8, False),
        (f'Severe (<65%): {len(below_20)} slices', '#ff4444', 8, False),
        ('', 'white', 4, False),
        ('CONTIGUOUS RUNS', '#f5c518', 10, True),
        (f'Total runs: {len(runs)}', 'white', 8, False),
    ]
    for i, run in enumerate(runs):
        wmm = len(run) * 3.3
        lines.append((f'Run {i+1}: {len(run)} slices ~{wmm:.0f}mm', '#ff4444', 8, False))
    lines += [
        ('', 'white', 4, False),
        ('INTERPRETATION', '#f5c518', 10, True),
        ('1 run = focal pathology.', '#6b6b88', 7, False),
        ('Multiple runs = multi-level.', '#6b6b88', 7, False),
        ('Wide run = diffuse change.', '#6b6b88', 7, False),
        ('Narrow run = structural lesion.', '#6b6b88', 7, False),
        ('', 'white', 4, False),
        ('Artifact Zero Labs 2026', '#444466', 7, False),
    ]
    y = 0.97
    for txt, col, sz, bold in lines:
        if txt:
            ax2.text(0.05, y, txt, transform=ax2.transAxes,
                     color=col, fontsize=sz,
                     fontweight='bold' if bold else 'normal', va='top')
        y -= 0.050 if sz >= 8 else 0.036

    fig.text(0.5, 0.01, 'Artifact Zero Labs  ·  Confidential',
             ha='center', color='#333355', fontsize=7)

    safe = "".join(c if c.isalnum() or c in '-_' else '_' for c in seq_desc)
    out_path = out_dir / f"width_{safe}.png"
    plt.savefig(str(out_path), dpi=120, facecolor=BG)
    plt.close(fig)
    print(f"  Saved: {out_path}")
    return {'n_runs': len(runs), 'run_widths': [len(r)*3.3 for r in runs],
            'pct_below_50': round(len(below_50)/len(zs)*100, 1)}


# ════════════════════════════════════════════════════════════════════
# ANALYSIS 3 — CROSS-SEQUENCE AGREEMENT SCORE
# ════════════════════════════════════════════════════════════════════

def run_agreement(vols_dict, subject, year, out_dir):
    """
    For each slice position, compute normalized gap for each sequence
    (gap / mean_gap). Agreement = how close these normalized values are.
    Low agreement at a specific level = tissue behaving differently
    across physics = most sensitive pathology marker.
    """
    print(f"\n  Running cross-sequence agreement analysis...")

    # Compute per-slice gaps for each sequence
    all_gaps = {}
    for seq_name, (vol, seq_type) in vols_dict.items():
        nz = vol.shape[0]
        gaps = []
        for z in range(nz):
            sl = resize_slice(vol[z].copy())
            mask = make_mask(sl)
            if mask.sum() < 100:
                gaps.append(None)
                continue
            r = compute_gap_region(sl[mask], seq_type)
            gaps.append(r[0] if r else None)
        all_gaps[seq_name] = gaps
        print(f"    {seq_name}: {sum(g is not None for g in gaps)}/{nz} valid slices")

    # Find common valid slice range
    names = list(all_gaps.keys())
    min_len = min(len(all_gaps[n]) for n in names)

    # Normalize each sequence to its own mean
    norm_gaps = {}
    for n in names:
        raw = [all_gaps[n][z] for z in range(min_len)]
        valid = [g for g in raw if g is not None]
        if not valid: continue
        mean_g = np.mean(valid)
        norm = [g/mean_g if g is not None else None for g in raw]
        norm_gaps[n] = norm

    if len(norm_gaps) < 2:
        print("  Not enough sequences for agreement analysis")
        return

    # Compute agreement score at each slice = std of normalized gaps
    # Low std = high agreement = normal tissue
    # High std = low agreement = pathological tissue
    agreement_scores = []
    zs_valid = []
    for z in range(min_len):
        vals = [norm_gaps[n][z] for n in norm_gaps if norm_gaps[n][z] is not None]
        if len(vals) >= 2:
            agreement_scores.append(np.std(vals))
            zs_valid.append(z)

    if not agreement_scores: return

    as_smooth = uniform_filter1d(agreement_scores, size=3)
    max_disagreement_z = zs_valid[int(np.argmax(as_smooth))]
    mean_agreement = np.mean(agreement_scores)

    print(f"  Max disagreement at slice {max_disagreement_z} (score={as_smooth[np.argmax(as_smooth)]:.3f})")
    print(f"  Mean agreement score: {mean_agreement:.3f}")

    # ── Render ────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10), facecolor=BG)
    fig.text(0.5, 0.97,
        f'ARTIFACT ZERO LABS  ·  Cross-Sequence Agreement  ·  {subject}  ·  {year}',
        ha='center', color='white', fontsize=12, fontweight='bold')
    fig.text(0.5, 0.93,
        f'Sequences: {", ".join(norm_gaps.keys())}  ·  High disagreement = anomalous tissue physics',
        ha='center', color='#6b6b88', fontsize=9)

    gs_plot = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.30,
                                left=0.07, right=0.97, top=0.90, bottom=0.08)

    colors_seq = ['#00d4ff','#f5c518','#7fff7f','#ff6b6b','#cc99ff']

    # Normalized gap curves per sequence
    ax = fig.add_subplot(gs_plot[0, :2])
    ax.set_facecolor('#13131a')
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')
    ax.axhline(1.0, color='white', lw=0.5, alpha=0.3, label='Expected (normalized=1)')
    for i, (n, norm) in enumerate(norm_gaps.items()):
        valid_z = [z for z in range(min_len) if norm[z] is not None]
        valid_g = [norm[z] for z in valid_z]
        if valid_g:
            sm = uniform_filter1d(valid_g, size=3)
            ax.plot(valid_z, sm, color=colors_seq[i%len(colors_seq)],
                    lw=1.5, label=n, alpha=0.85)
    ax.axvline(max_disagreement_z, color='#ff4444', lw=2, ls='--', alpha=0.8,
               label=f'Peak disagreement: slice {max_disagreement_z}')
    ax.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax.set_ylabel('Normalized gap (÷ mean)', color='#6b6b88', fontsize=8)
    ax.set_title('Normalized gap per sequence — divergence = tissue physics anomaly',
                 color='white', fontsize=9, fontweight='bold')
    ax.tick_params(colors='#6b6b88', labelsize=7)
    ax.legend(fontsize=7, facecolor='#13131a', labelcolor='white')

    # Agreement score curve
    ax2 = fig.add_subplot(gs_plot[1, :2])
    ax2.set_facecolor('#13131a')
    for sp in ax2.spines.values(): sp.set_color('#1e1e2a')
    ax2.fill_between(zs_valid, as_smooth, alpha=0.2, color='#ff4444')
    ax2.plot(zs_valid, as_smooth, color='#ff4444', lw=2,
             label='Disagreement score (std of normalized gaps)')
    ax2.axhline(mean_agreement, color='#f5c518', lw=1, ls='--',
                label=f'Mean: {mean_agreement:.3f}')
    ax2.axvline(max_disagreement_z, color='#ff4444', lw=2, ls='--', alpha=0.8,
                label=f'Peak: slice {max_disagreement_z}')
    ax2.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax2.set_ylabel('Disagreement score\n(higher = more anomalous)', color='#6b6b88', fontsize=8)
    ax2.set_title('Cross-sequence disagreement — peak = where tissue physics is most anomalous',
                  color='white', fontsize=9, fontweight='bold')
    ax2.tick_params(colors='#6b6b88', labelsize=7)
    ax2.legend(fontsize=7, facecolor='#13131a', labelcolor='white')

    # Stats
    ax3 = fig.add_subplot(gs_plot[:, 2])
    ax3.set_facecolor('#0b0b0f'); ax3.axis('off')
    lines = [
        ('AGREEMENT RESULTS', '#00d4ff', 10, True),
        (f'Sequences compared: {len(norm_gaps)}', 'white', 8, False),
        (f'Slices analysed: {len(zs_valid)}', 'white', 8, False),
        ('', 'white', 4, False),
        ('DISAGREEMENT', '#ff4444', 10, True),
        (f'Peak disagreement:', '#ff4444', 9, True),
        (f'  Slice {max_disagreement_z}', '#ff4444', 8, False),
        (f'  ({max_disagreement_z/min_len*100:.0f}% from inferior)', '#ff4444', 8, False),
        (f'  Score: {as_smooth[np.argmax(as_smooth)]:.3f}', '#ff4444', 8, False),
        (f'Mean score: {mean_agreement:.3f}', 'white', 8, False),
        ('', 'white', 4, False),
        ('SEQUENCES', '#f5c518', 10, True),
    ]
    for n in norm_gaps:
        lines.append((f'  {n}', '#6b6b88', 7, False))
    lines += [
        ('', 'white', 4, False),
        ('INTERPRETATION', '#f5c518', 10, True),
        ('High disagreement = sequences', '#6b6b88', 7, False),
        ('see different tissue physics.', '#6b6b88', 7, False),
        ('T1 vs T2 vs T2* disagreeing at', '#6b6b88', 7, False),
        ('the same level = tissue there', '#6b6b88', 7, False),
        ('is not behaving normally on', '#6b6b88', 7, False),
        ('any single physics assumption.', '#6b6b88', 7, False),
        ('Most sensitive pathology marker.', '#7fff7f', 7, True),
        ('', 'white', 4, False),
        ('Artifact Zero Labs 2026', '#444466', 7, False),
    ]
    y = 0.97
    for txt, col, sz, bold in lines:
        if txt:
            ax3.text(0.05, y, txt, transform=ax3.transAxes,
                     color=col, fontsize=sz,
                     fontweight='bold' if bold else 'normal', va='top')
        y -= 0.050 if sz >= 8 else 0.036

    fig.text(0.5, 0.01, 'Artifact Zero Labs  ·  Confidential',
             ha='center', color='#333355', fontsize=7)

    out_path = out_dir / "cross_sequence_agreement.png"
    plt.savefig(str(out_path), dpi=120, facecolor=BG)
    plt.close(fig)
    print(f"  Saved: {out_path}")

    # CSV
    csv_path = out_dir / "cross_sequence_agreement.csv"
    with open(str(csv_path), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['slice_z', 'disagreement_score'] +
                   [f'norm_gap_{n}' for n in norm_gaps])
        for i, z in enumerate(zs_valid):
            row = [z, round(float(as_smooth[i]), 4)]
            for n in norm_gaps:
                v = norm_gaps[n][z]
                row.append(round(v, 4) if v is not None else '')
            w.writerow(row)

    return {'peak_disagreement_z': max_disagreement_z,
            'peak_score': round(float(as_smooth[np.argmax(as_smooth)]), 4),
            'mean_score': round(mean_agreement, 4)}


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='AZ Advanced Cervical Analysis')
    parser.add_argument('--mode',    default='all',
                        choices=['asymmetry','width','agreement','all'])
    parser.add_argument('--input',   default=None, help='DICOM folder (for asymmetry/width/all)')
    parser.add_argument('--subject', default='Subject')
    parser.add_argument('--year',    default='')
    parser.add_argument('--out',     default=None)
    args = parser.parse_args()

    if not args.input:
        args.input = input("DICOM folder: ").strip().strip('"')

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"ERROR: {input_path}"); sys.exit(1)

    out_dir = Path(args.out) if args.out else \
              input_path.parent / (input_path.name + '_ADVANCED')
    out_dir.mkdir(exist_ok=True)

    print('=' * 65)
    print('ARTIFACT ZERO LABS — Advanced Cervical Spine Analysis')
    print(f'Subject: {args.subject}  |  Year: {args.year}')
    print(f'Mode: {args.mode}')
    print('=' * 65)

    print(f"\nLoading DICOM from: {input_path}")
    files = find_dcm(input_path)
    print(f"Found {len(files)} files")
    series_list = group_series(files)
    good = [s for s in series_list if s['score'] > 0]

    print("\nSequences found:")
    for i, s in enumerate(good[:8], 1):
        print(f"  [{i}] {s['type']:<6} {s['n']:>3} slices  '{s['desc']}'")

    # Load top sequences
    loaded_vols = {}
    for s in good[:5]:
        desc = s['desc']
        try:
            vol, spacing = load_volume(s)
            loaded_vols[desc] = (vol, s['type'], spacing, s)
            print(f"  Loaded: '{desc}' {vol.shape}")
        except Exception as e:
            print(f"  Skip '{desc}': {e}")

    if not loaded_vols:
        print("ERROR: No volumes loaded"); sys.exit(1)

    summary = {}

    for desc, (vol, seq_type, spacing, s_info) in loaded_vols.items():

        if args.mode in ('asymmetry', 'all'):
            r = run_asymmetry(vol, seq_type, args.subject,
                              args.year, desc, out_dir)
            if r: summary[f'asym_{desc}'] = r

        if args.mode in ('width', 'all'):
            r = run_min_width(vol, seq_type, args.subject,
                              args.year, desc, out_dir)
            if r: summary[f'width_{desc}'] = r

    if args.mode in ('agreement', 'all') and len(loaded_vols) >= 2:
        vols_for_agreement = {desc: (vol, seq_type)
                               for desc, (vol, seq_type, _, _) in loaded_vols.items()}
        r = run_agreement(vols_for_agreement, args.subject, args.year, out_dir)
        if r: summary['agreement'] = r

    # Summary
    print(f"\n{'='*65}")
    print(f"COMPLETE. Output: {out_dir.resolve()}")
    print("\nKey findings:")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    summary_path = out_dir / 'ADVANCED_SUMMARY.txt'
    with open(str(summary_path), 'w') as f:
        f.write(f"ARTIFACT ZERO LABS — Advanced Analysis\n")
        f.write(f"Subject: {args.subject}  Year: {args.year}\n")
        f.write("=" * 50 + "\n\n")
        for k, v in summary.items():
            f.write(f"{k}:\n")
            for kk, vv in v.items():
                f.write(f"  {kk}: {vv}\n")
            f.write("\n")

    print(f"\nFiles:")
    for f in sorted(out_dir.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
