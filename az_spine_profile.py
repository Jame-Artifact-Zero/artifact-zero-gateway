"""
================================================================================
ARTIFACT ZERO LABS — Cervical Spine Level-by-Level Gap Profile
Runs the tissue contrast gap computation at every slice position through
a cervical spine MRI volume, producing a gap profile curve.

If the gap compression is localized to C5-C6 and normal at adjacent levels,
that's anatomical localization of the signal change.

USAGE:
    python az_spine_profile.py "path/to/DICOM/folder" --subject "Name"

    # Compare two timepoints side by side:
    python az_spine_profile.py "MRI C Spine 2024" --subject "Jame Houghton" --year 2024
    python az_spine_profile.py "HOUGHTONJAMEST_MRI C-Spine Without Contrast 72141" --subject "Jame Houghton" --year 2026

OUTPUT:
    {folder}_profile/
        gap_profile_{sequence}.png   — gap vs slice position curve
        gap_profile_{sequence}.csv   — raw numbers per slice
        PROFILE_SUMMARY.txt          — key findings

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
                           binary_fill_holes, label as scipy_label, zoom)

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
# SEQUENCE SCORING
# ════════════════════════════════════════════════════════════════════

GOOD = {
    'T1': ['t1','sag t1','ax t1','t1 fse','t1 tfe','mprage','t1w'],
    'T2': ['t2','sag t2','ax t2','t2 fse','t2 tse','t2w'],
    'STIR': ['stir','fse stir','t2 stir'],
    'T2S': ['t2*','merge','t2star','swan','t2s'],
}
SKIP = ['localizer','scout','survey','mpr','report','reformat',
        'derived','secondary','adc','dwi','diffusion']

def score_seq(desc, n):
    dl = desc.lower()
    for s in SKIP:
        if s in dl: return -1, 'SKIP'
    for stype, kws in GOOD.items():
        if any(k in dl for k in kws):
            return 100 + min(n, 200), stype
    return 10 + min(n, 200), 'OTHER'


# ════════════════════════════════════════════════════════════════════
# DICOM LOADING
# ════════════════════════════════════════════════════════════════════

def find_dcm(folder):
    files = []
    for root, dirs, fs in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in fs:
            fp = Path(root) / f
            if f.lower().endswith(('.dcm','.ima')) or ('.' not in f and fp.stat().st_size > 500):
                files.append(fp)
    return files


def group_series(files):
    series = defaultdict(list)
    for fp in files:
        try:
            ds = pydicom.dcmread(str(fp), stop_before_pixels=True)
            uid  = getattr(ds, 'SeriesInstanceUID', 'unk')
            desc = getattr(ds, 'SeriesDescription', 'Unknown')
            num  = getattr(ds, 'SeriesNumber', 0)
            series[(str(uid), str(desc), int(num or 0))].append(fp)
        except: pass
    scored = []
    for (uid, desc, num), fs in series.items():
        sc, stype = score_seq(desc, len(fs))
        scored.append({'uid':uid,'desc':desc,'series_num':num,
                       'files':sorted(fs),'n':len(fs),'score':sc,'type':stype})
    return sorted(scored, key=lambda x: x['score'], reverse=True)


def load_volume_sorted(series_info):
    """Load volume with slices sorted by position (sup→inf)."""
    files = series_info['files']
    if SITK_OK:
        try:
            reader = sitk.ImageSeriesReader()
            reader.SetFileNames([str(f) for f in files])
            img = reader.Execute()
            vol = sitk.GetArrayFromImage(img)   # (z, y, x)
            spacing = img.GetSpacing()           # (x, y, z)
            return vol.astype(np.float32), float(spacing[2])
        except: pass
    # Fallback
    slices, positions = [], []
    for fp in files:
        try:
            ds = pydicom.dcmread(str(fp))
            arr = ds.pixel_array.astype(np.float32)
            slope = float(getattr(ds,'RescaleSlope',1))
            intercept = float(getattr(ds,'RescaleIntercept',0))
            arr = arr * slope + intercept
            pos = getattr(ds,'ImagePositionPatient',[0,0,len(slices)])
            positions.append(float(pos[2]))
            slices.append(arr)
        except: pass
    if not slices: raise ValueError("No slices loaded")
    order = np.argsort(positions)
    vol = np.stack([slices[i] for i in order], axis=0)  # (z, y, x)
    return vol.astype(np.float32), 1.0


# ════════════════════════════════════════════════════════════════════
# SLICE-LEVEL ANALYSIS
# ════════════════════════════════════════════════════════════════════

def make_mask_2d(sl):
    flat = sl[sl > 0].flatten()
    if len(flat) == 0: return np.zeros(sl.shape, bool)
    thresh = np.percentile(flat, 12)
    m = sl > thresh
    m = binary_fill_holes(m)
    m = binary_erosion(m, iterations=2)
    m = binary_dilation(m, iterations=2)
    la, n = scipy_label(m)
    if n > 1:
        sizes = [np.sum(la==i) for i in range(1,n+1)]
        m = la == (np.argmax(sizes)+1)
    return m.astype(bool)


def compute_gap(sl, seq_type='T2'):
    """
    Compute the tissue contrast gap for a single slice.
    Returns: A, B, gap, n_voxels, fraction
    Returns None if slice has insufficient tissue.
    """
    mask = make_mask_2d(sl)
    n = mask.sum()
    if n < 200:
        return None

    brain_vals = sl[mask]

    if seq_type in ('T1',):
        A = np.percentile(brain_vals, 85)
        B = np.percentile(brain_vals, 65)
    else:  # T2, STIR, T2*
        A = np.percentile(brain_vals, 70)
        B = np.percentile(brain_vals, 88)

    gap = abs(A - B)
    denom = A - B
    if abs(denom) < 1e-6: denom = 1e-6
    w = np.clip((sl - B) / denom, 0, 1)
    w[~mask] = np.nan
    frac = float(np.nanmean(w[mask]))

    return {'A': A, 'B': B, 'gap': gap, 'n': n, 'fraction': frac}


def profile_volume(vol, seq_type, min_voxels=500, resize=128):
    """
    Run gap computation at every slice position.
    Returns list of per-slice results indexed by z.
    """
    nz = vol.shape[0]
    results = []
    for z in range(nz):
        sl = vol[z].copy()
        # Resize to standard size
        h, w = sl.shape
        if max(h, w) > resize:
            scale = resize / max(h, w)
            sl = zoom(sl, scale, order=1)
        r = compute_gap(sl, seq_type)
        results.append(r)
    return results


# ════════════════════════════════════════════════════════════════════
# RENDERING
# ════════════════════════════════════════════════════════════════════

def render_profile(results, seq_desc, seq_type, subject_name, year, out_path):
    """
    Render the gap profile curve — gap vs slice position.
    Annotate the minimum gap location.
    """
    zs    = [i for i, r in enumerate(results) if r is not None]
    gaps  = [results[z]['gap'] for z in zs]
    fracs = [results[z]['fraction'] for z in zs]
    ns    = [results[z]['n'] for z in zs]

    if not gaps:
        print("  No valid slices for profile")
        return

    # Find minimum gap (most compressed) region
    min_gap = min(gaps)
    min_z   = zs[gaps.index(min_gap)]
    mean_gap = np.mean(gaps)
    max_gap  = max(gaps)

    # Smooth for display
    from scipy.ndimage import uniform_filter1d
    gaps_smooth  = uniform_filter1d(gaps,  size=3)
    fracs_smooth = uniform_filter1d(fracs, size=3)

    fig = plt.figure(figsize=(16, 9), facecolor=BG)
    fig.text(0.5, 0.97,
        f'ARTIFACT ZERO LABS  ·  Cervical Spine Level Profile  ·  {subject_name}  ·  {year}',
        ha='center', color='white', fontsize=12, fontweight='bold')
    fig.text(0.5, 0.93,
        f'Sequence: {seq_desc}  ·  Tissue contrast gap at every slice position',
        ha='center', color='#6b6b88', fontsize=9)

    gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.30,
                           left=0.07, right=0.97, top=0.90, bottom=0.08)

    # ── Gap profile curve ──────────────────────────────────────────
    ax = fig.add_subplot(gs[0, :2])
    ax.set_facecolor('#13131a')
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')

    ax.fill_between(zs, gaps_smooth, alpha=0.15, color='#00d4ff')
    ax.plot(zs, gaps_smooth, color='#00d4ff', lw=2, label='Tissue contrast gap')
    ax.axhline(mean_gap, color='#f5c518', lw=1, ls='--', alpha=0.7, label=f'Mean gap: {mean_gap:.0f}')

    # Mark minimum
    ax.axvline(min_z, color='#ff4444', lw=2, ls='--', alpha=0.8)
    ax.scatter([min_z], [min_gap], color='#ff4444', s=80, zorder=5)
    ax.annotate(f'Min gap: {min_gap:.0f}\n(slice {min_z})',
                xy=(min_z, min_gap), xytext=(min_z+3, min_gap + mean_gap*0.15),
                color='#ff4444', fontsize=8,
                arrowprops=dict(arrowstyle='->', color='#ff4444', lw=1.5))

    # Shade bottom 20% of gap range as "compressed" zone
    compress_thresh = mean_gap * 0.5
    ax.axhspan(0, compress_thresh, alpha=0.08, color='#ff4444', label=f'Compressed zone (<{compress_thresh:.0f})')

    ax.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax.set_ylabel('Tissue contrast gap', color='#6b6b88', fontsize=8)
    ax.set_title('Gap profile — lower = more compressed tissue signal',
                 color='white', fontsize=9, fontweight='bold')
    ax.tick_params(colors='#6b6b88', labelsize=7)
    ax.legend(fontsize=7, facecolor='#13131a', labelcolor='white', loc='upper right')

    # ── Fraction profile ──────────────────────────────────────────
    ax2 = fig.add_subplot(gs[1, :2])
    ax2.set_facecolor('#13131a')
    for sp in ax2.spines.values(): sp.set_color('#1e1e2a')
    ax2.fill_between(zs, fracs_smooth, alpha=0.15, color='#7fff7f')
    ax2.plot(zs, fracs_smooth, color='#7fff7f', lw=2, label='Tissue fraction')
    ax2.axvline(min_z, color='#ff4444', lw=2, ls='--', alpha=0.8, label=f'Min gap at slice {min_z}')
    ax2.set_xlabel('Slice position (inferior → superior)', color='#6b6b88', fontsize=8)
    ax2.set_ylabel('Tissue fraction', color='#6b6b88', fontsize=8)
    ax2.set_title('Fraction profile — stable = no volume loss',
                  color='white', fontsize=9, fontweight='bold')
    ax2.tick_params(colors='#6b6b88', labelsize=7)
    ax2.legend(fontsize=7, facecolor='#13131a', labelcolor='white')

    # ── Stats box ─────────────────────────────────────────────────
    ax3 = fig.add_subplot(gs[:, 2])
    ax3.set_facecolor('#0b0b0f'); ax3.axis('off')

    compression_pct = (max_gap - min_gap) / max_gap * 100
    gap_cv = np.std(gaps) / np.mean(gaps) * 100  # coefficient of variation

    lines = [
        ('PROFILE RESULTS', '#00d4ff', 10, True),
        (f'Slices analysed: {len(zs)}', 'white', 8, False),
        (f'Sequence: {seq_type}', 'white', 8, False),
        (f'Year: {year}', 'white', 8, False),
        ('', 'white', 4, False),
        ('GAP STATISTICS', '#f5c518', 10, True),
        (f'Mean gap: {mean_gap:.1f}', 'white', 8, False),
        (f'Max gap:  {max_gap:.1f}', '#7fff7f', 8, False),
        (f'Min gap:  {min_gap:.1f}', '#ff4444', 9, True),
        (f'Min at slice: {min_z}', '#ff4444', 8, False),
        (f'Compression: {compression_pct:.1f}%', '#ff4444', 9, True),
        (f'Variability (CV): {gap_cv:.1f}%', 'white', 8, False),
        ('', 'white', 4, False),
        ('FRACTION STATISTICS', '#f5c518', 10, True),
        (f'Mean: {np.mean(fracs):.4f}', 'white', 8, False),
        (f'Std:  {np.std(fracs):.4f}', 'white', 8, False),
        (f'Range: [{min(fracs):.3f}, {max(fracs):.3f}]', 'white', 7, False),
        ('', 'white', 4, False),
        ('INTERPRETATION', '#f5c518', 10, True),
        ('Gap = tissue contrast separation.', '#6b6b88', 7, False),
        ('Low gap = compressed signal.', '#6b6b88', 7, False),
        ('Stable fraction = no volume', '#6b6b88', 7, False),
        ('loss at that level.', '#6b6b88', 7, False),
        ('Red line = most compressed', '#ff4444', 7, False),
        ('position in the volume.', '#ff4444', 7, False),
        ('', 'white', 4, False),
        ('Artifact Zero Labs 2026', '#444466', 7, False),
    ]
    y = 0.97
    for txt, col, sz, bold in lines:
        if txt:
            ax3.text(0.05, y, txt, transform=ax3.transAxes,
                     color=col, fontsize=sz,
                     fontweight='bold' if bold else 'normal', va='top')
        y -= 0.052 if sz >= 8 else 0.038

    fig.text(0.5, 0.01,
        'Artifact Zero Labs  ·  Confidential',
        ha='center', color='#333355', fontsize=7)

    plt.savefig(str(out_path), dpi=120, facecolor=BG)
    plt.close(fig)
    print(f"  Saved: {out_path}")
    return {
        'mean_gap': round(mean_gap, 1),
        'max_gap':  round(max_gap, 1),
        'min_gap':  round(min_gap, 1),
        'min_z':    min_z,
        'compression_pct': round(compression_pct, 1),
        'gap_cv':   round(gap_cv, 1),
        'mean_frac': round(float(np.mean(fracs)), 4),
        'std_frac':  round(float(np.std(fracs)), 4),
    }


# ════════════════════════════════════════════════════════════════════
# CSV OUTPUT
# ════════════════════════════════════════════════════════════════════

def write_profile_csv(results, path):
    with open(str(path), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['slice_z', 'gap', 'A', 'B', 'fraction', 'n_voxels'])
        for z, r in enumerate(results):
            if r:
                w.writerow([z, round(r['gap'],2), round(r['A'],2),
                             round(r['B'],2), round(r['fraction'],4), r['n']])
            else:
                w.writerow([z, '', '', '', '', ''])


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='AZ Cervical Spine Level Profile')
    parser.add_argument('input', nargs='?')
    parser.add_argument('--subject', default='Subject')
    parser.add_argument('--year',    default='')
    parser.add_argument('--out',     default=None)
    args = parser.parse_args()

    if not args.input:
        args.input = input("DICOM folder: ").strip().strip('"')

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"ERROR: {input_path} not found"); sys.exit(1)

    out_dir = Path(args.out) if args.out else \
              input_path.parent / (input_path.name + '_PROFILE')
    out_dir.mkdir(exist_ok=True)

    print('=' * 65)
    print('ARTIFACT ZERO LABS — Cervical Spine Level-by-Level Profile')
    print(f'Subject: {args.subject}  |  Year: {args.year}')
    print('=' * 65)

    print(f"\nScanning: {input_path}")
    files = find_dcm(input_path)
    print(f"Found {len(files)} DICOM files")

    series_list = group_series(files)
    good = [s for s in series_list if s['score'] > 0]
    print(f"\nTop sequences:")
    for i, s in enumerate(good[:8], 1):
        print(f"  [{i}] {s['type']:<6} {s['n']:>3} slices  '{s['desc']}'")

    # Process top sequences — prefer STIR, T2, T1 in that order
    # Pick up to 3 best sequences
    to_process = good[:3]

    summary_lines = [
        f"ARTIFACT ZERO LABS — Cervical Spine Profile",
        f"Subject: {args.subject}  |  Year: {args.year}",
        f"Input: {input_path.name}",
        "=" * 60,
    ]

    for s in to_process:
        desc = s['desc']
        safe = "".join(c if c.isalnum() or c in '-_' else '_' for c in desc)
        print(f"\n{'='*50}")
        print(f"Profiling: {desc} ({s['n']} slices, {s['type']})")

        try:
            vol, spacing = load_volume_sorted(s)
            print(f"  Volume: {vol.shape}  z-spacing: {spacing:.2f}mm")

            t0 = time.perf_counter()
            results = profile_volume(vol, s['type'])
            elapsed = time.perf_counter() - t0

            valid = [r for r in results if r is not None]
            print(f"  Valid slices: {len(valid)}/{len(results)}  ({elapsed*1000:.0f}ms)")

            if len(valid) < 5:
                print("  SKIP — too few valid slices")
                continue

            out_png = out_dir / f"profile_{safe}.png"
            out_csv = out_dir / f"profile_{safe}.csv"

            stats = render_profile(results, desc, s['type'],
                                   args.subject, args.year, out_png)
            write_profile_csv(results, out_csv)

            if stats:
                summary_lines += [
                    f"",
                    f"Sequence: {desc} ({s['type']})",
                    f"  Mean gap:    {stats['mean_gap']}",
                    f"  Max gap:     {stats['max_gap']}",
                    f"  Min gap:     {stats['min_gap']}  (slice {stats['min_z']})",
                    f"  Compression: {stats['compression_pct']}%  (max-min)/max",
                    f"  Gap CV:      {stats['gap_cv']}%  (slice-to-slice variability)",
                    f"  Mean frac:   {stats['mean_frac']} ± {stats['std_frac']}",
                ]

                print(f"\n  PROFILE STATS:")
                print(f"    Mean gap:    {stats['mean_gap']}")
                print(f"    Min gap:     {stats['min_gap']}  at slice {stats['min_z']}")
                print(f"    Compression: {stats['compression_pct']}%")
                print(f"    Gap CV:      {stats['gap_cv']}%")
                print(f"    Mean frac:   {stats['mean_frac']} ± {stats['std_frac']}")

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            continue

    # Write summary
    summary_path = out_dir / 'PROFILE_SUMMARY.txt'
    with open(str(summary_path), 'w') as f:
        f.write('\n'.join(summary_lines))

    print(f"\n{'='*65}")
    print(f"DONE. Output: {out_dir.resolve()}")
    print(f"Files:")
    for f in sorted(out_dir.iterdir()):
        print(f"  {f.name}  ({f.stat().st_size:,} bytes)")


if __name__ == '__main__':
    main()
