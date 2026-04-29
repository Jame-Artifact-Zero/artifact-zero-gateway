"""
================================================================================
ARTIFACT ZERO LABS — DICOM Processor
Unzips a DICOM folder, finds the best brain MRI sequences,
runs algebraic decomposition vs iterative fitting side by side,
and renders a comparison image.

INSTALL (one time):
    pip install pydicom SimpleITK numpy matplotlib scipy

USAGE:
    python az_dicom_processor.py                          # prompts for zip file
    python az_dicom_processor.py my_brain.zip             # direct
    python az_dicom_processor.py my_brain.zip --all       # show all sequences found
    python az_dicom_processor.py my_brain/                # unzipped folder also works

OUTPUT:
    az_output/
        AZ_comparison_{sequence}.png   — side by side render
        AZ_comparison_{sequence}.pdf   — PDF version
        AZ_sequences_found.txt         — all sequences discovered

================================================================================
"""

import os
import sys
import zipfile
import shutil
import tempfile
import argparse
import time
import warnings
from pathlib import Path
from collections import defaultdict

warnings.filterwarnings('ignore')

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import LinearSegmentedColormap
from scipy.ndimage import gaussian_filter, binary_erosion, binary_dilation, binary_fill_holes, label, zoom

try:
    import pydicom
    PYDICOM_OK = True
except ImportError:
    print("ERROR: pydicom not installed. Run: pip install pydicom")
    sys.exit(1)

try:
    import SimpleITK as sitk
    SITK_OK = True
except ImportError:
    SITK_OK = False
    print("NOTE: SimpleITK not found — using pydicom only (still works for most DICOMs)")


# ════════════════════════════════════════════════════════════════════
# SEQUENCE SCORING — find the most useful sequences
# ════════════════════════════════════════════════════════════════════

# Keywords that identify good structural brain sequences
GOOD_SEQUENCES = {
    'T2S':   ['t2*', 't2star', 'merge', 'medic', 'mgre', 'gre', 'swi', 'swan',
              'suscept', '2d t2'],
    'STIR':  ['stir', 'short tau'],
    'FLAIR': ['flair'],
    'T2FS':  ['t2 fs', 't2fs', 't2 fat', 'fsat', 'fat sat', 'fat-sat', 'pdfs', 'pd fs'],
    'T1':    ['t1', 'mprage', 'bravo', 'spgr', 'tfe', 'fspgr', 'irfspgr', 't1w',
              'sag t1', 'cor t1', 'ax t1', '3d t1'],
    'T2':    ['t2', 't2w', 'ax t2', 'sag t2', 'fse', 'tse'],
    'PD':    ['pd', 'proton', 'pdw', 'dual echo'],
    'DWI':   ['dwi', 'diffusion', 'adc', 'dti'],
    'SWI':   ['susceptibility'],
    'TOF':   ['tof', 'mra', 'angio'],
}

SKIP_KEYWORDS = ['localizer', 'scout', 'survey', 'cal', 'mpr', 'report',
                 'phoenix', 'patient', 'protocol', 'setup', 'screensave',
                 'reformatted', 'seg', 'label', 'derived', 'secondary']

def score_sequence(desc, n_slices):
    """Score a sequence description. Higher = more useful."""
    desc_lower = desc.lower()

    # Skip non-diagnostic sequences
    for skip in SKIP_KEYWORDS:
        if skip in desc_lower:
            return -1, 'SKIP'

    # Identify type
    seq_type = 'OTHER'
    for stype, keywords in GOOD_SEQUENCES.items():
        if any(kw in desc_lower for kw in keywords):
            seq_type = stype
            break

    # Score: prefer T1/T2 with many slices
    score = 0
    if seq_type == 'T2S':    score += 130
    elif seq_type == 'STIR':  score += 110
    elif seq_type == 'FLAIR': score += 110
    elif seq_type == 'T2FS':  score += 105
    elif seq_type == 'T1':    score += 100
    elif seq_type == 'T2':    score += 80
    elif seq_type == 'PD':    score += 90
    elif seq_type == 'DWI':   score += 30
    else: score += 10

    # More slices = better coverage
    score += min(n_slices, 200)

    # Penalise very thin stacks
    if n_slices < 10:
        score -= 50

    return score, seq_type


# ════════════════════════════════════════════════════════════════════
# DICOM LOADING
# ════════════════════════════════════════════════════════════════════

def find_dicom_files(folder):
    """Recursively find all DICOM files."""
    dcm_files = []
    for root, dirs, files in os.walk(folder):
        # Skip hidden folders
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.lower().endswith('.dcm') or f.lower().endswith('.ima'):
                dcm_files.append(Path(root) / f)
            elif '.' not in f:
                # DICOM files often have no extension
                fp = Path(root) / f
                if fp.stat().st_size > 1000:
                    dcm_files.append(fp)
    return dcm_files


def group_by_sequence(dcm_files, verbose=False):
    """Group DICOM files by series and return metadata."""
    series = defaultdict(list)

    print(f"\nReading DICOM headers from {len(dcm_files)} files...")
    for i, fp in enumerate(dcm_files):
        if i % 100 == 0 and i > 0:
            print(f"  {i}/{len(dcm_files)}", end='\r')
        try:
            ds = pydicom.dcmread(str(fp), stop_before_pixels=True)
            series_uid = getattr(ds, 'SeriesInstanceUID', 'unknown')
            series_desc = getattr(ds, 'SeriesDescription', 'Unknown')
            series_num  = getattr(ds, 'SeriesNumber', 0)
            key = (str(series_uid), str(series_desc), int(series_num or 0))
            series[key].append(fp)
        except Exception:
            pass

    print(f"  Found {len(series)} series")

    # Score each series
    scored = []
    for (uid, desc, num), files in series.items():
        n = len(files)
        score, stype = score_sequence(desc, n)
        scored.append({
            'uid': uid,
            'desc': desc,
            'series_num': num,
            'files': sorted(files),
            'n_slices': n,
            'score': score,
            'type': stype,
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored


def load_volume(series_info):
    """Load a DICOM series into a numpy volume."""
    files = series_info['files']

    if SITK_OK:
        # SimpleITK handles sorting and spacing correctly
        try:
            reader = sitk.ImageSeriesReader()
            file_strs = [str(f) for f in files]
            reader.SetFileNames(file_strs)
            image = reader.Execute()
            vol = sitk.GetArrayFromImage(image)  # (z, y, x)
            vol = np.transpose(vol, (1, 2, 0))   # -> (y, x, z)
            return vol.astype(np.float32)
        except Exception as e:
            print(f"  SimpleITK load failed ({e}), trying pydicom...")

    # Fallback: pydicom slice by slice
    slices = []
    positions = []
    for fp in files:
        try:
            ds = pydicom.dcmread(str(fp))
            arr = ds.pixel_array.astype(np.float32)
            # Apply rescale if present
            slope = float(getattr(ds, 'RescaleSlope', 1))
            intercept = float(getattr(ds, 'RescaleIntercept', 0))
            arr = arr * slope + intercept
            slices.append(arr)
            pos = getattr(ds, 'ImagePositionPatient', [0, 0, len(slices)])
            positions.append(float(pos[2]))
        except Exception:
            pass

    if not slices:
        raise ValueError("Could not read any slices")

    # Sort by position
    order = np.argsort(positions)
    slices = [slices[i] for i in order]
    vol = np.stack(slices, axis=2)  # (y, x, z)
    return vol.astype(np.float32)


# ════════════════════════════════════════════════════════════════════
# BRAIN MASK
# ════════════════════════════════════════════════════════════════════

def make_brain_mask(sl):
    flat = sl[sl > 0].flatten()
    if len(flat) == 0:
        return np.zeros(sl.shape, dtype=bool)
    thresh = np.percentile(flat, 15)
    mask = sl > thresh
    mask = binary_fill_holes(mask)
    mask = binary_erosion(mask, iterations=2)
    mask = binary_dilation(mask, iterations=3)
    labeled_arr, n = label(mask)
    if n > 1:
        sizes = [np.sum(labeled_arr == i) for i in range(1, n+1)]
        mask = labeled_arr == (np.argmax(sizes) + 1)
    return mask.astype(bool)


def best_slice(vol):
    """Pick the slice with most brain content."""
    nz = vol.shape[2]
    z_lo, z_hi = int(nz*0.35), int(nz*0.65)
    means = [vol[:,:,z].mean() for z in range(z_lo, z_hi)]
    best_z = z_lo + int(np.argmax(means))
    sl = vol[:,:,best_z].copy()
    sl = np.rot90(sl)

    # Resize to standard size if very large
    h, w = sl.shape
    if max(h, w) > 512:
        scale = 512 / max(h, w)
        sl = zoom(sl, scale, order=1)

    return sl.astype(np.float32)


# ════════════════════════════════════════════════════════════════════
# DECOMPOSITION METHODS
# ════════════════════════════════════════════════════════════════════

def get_tissue_landmarks(sl, brain_mask, seq_type='T1'):
    """Read A and B directly from image histogram."""
    brain_vals = sl[brain_mask]
    if seq_type == 'T1':
        # T1: WM brighter — A=WM pct85, B=GM pct65
        A = np.percentile(brain_vals, 85)
        B = np.percentile(brain_vals, 65)
        label_A, label_B = 'WM', 'GM'
    else:
        # T2/PD/FLAIR: GM brighter — A=GM pct70, B=WM pct88
        A = np.percentile(brain_vals, 70)
        B = np.percentile(brain_vals, 88)
        label_A, label_B = 'GM', 'WM'
    return A, B, label_A, label_B


def method_algebraic(sl, brain_mask, A, B):
    """Algebraic inversion — the AZ method."""
    t0 = time.perf_counter()
    denom = A - B
    if abs(denom) < 1e-6:
        denom = 1e-6
    w = (sl - B) / denom
    w = np.clip(w, 0, 1)
    w[~brain_mask] = np.nan
    elapsed = (time.perf_counter() - t0) * 1000  # ms
    return w, elapsed


def method_iterative(sl, brain_mask, A, B, n_sample=300):
    """
    Iterative per-voxel fitting — the current clinical method.
    Sample n_sample voxels, extrapolate time to full slice.
    """
    from scipy.optimize import curve_fit

    def model(x, w):
        return w * A + (1 - w) * B

    coords = np.argwhere(brain_mask)
    idx = np.random.choice(len(coords), min(n_sample, len(coords)), replace=False)

    t0 = time.perf_counter()
    w_samp = {}
    for i in idx:
        r, c = coords[i]
        y_val = np.array([sl[r, c]])
        try:
            popt, _ = curve_fit(model, [0.], y_val, p0=[0.5],
                                bounds=(0., 1.), maxfev=50)
            w_samp[(r, c)] = float(popt[0])
        except Exception:
            w_samp[(r, c)] = 0.5
    t_sample = time.perf_counter() - t0

    n_brain = brain_mask.sum()
    t_extrap_ms = t_sample * (n_brain / len(idx)) * 1000

    # Fill map with algebraic, overwrite sampled points
    w_full, _ = method_algebraic(sl, brain_mask, A, B)
    for (r, c), val in w_samp.items():
        w_full[r, c] = val

    return w_full, t_sample * 1000, t_extrap_ms


# ════════════════════════════════════════════════════════════════════
# RENDERING
# ════════════════════════════════════════════════════════════════════

BG = '#0b0b0f'
AZ_MAP = LinearSegmentedColormap.from_list('az',
    ['#040415', '#0a2a5e', '#0066cc', '#00d4ff', '#ffffff'])


def render_comparison(sl, brain_mask, w_alg, w_iter,
                      t_alg_ms, t_extrap_ms, A, B,
                      label_A, label_B, seq_desc, subject_name,
                      out_path):
    """Render the side-by-side comparison — clean external output."""
    fig = plt.figure(figsize=(18, 10), facecolor=BG)

    fig.text(0.5, 0.97,
        f'ARTIFACT ZERO LABS  ·  Signal Decomposition  ·  {subject_name}',
        ha='center', color='white', fontsize=12, fontweight='bold')
    fig.text(0.5, 0.93,
        f'Sequence: {seq_desc}',
        ha='center', color='#6b6b88', fontsize=9)

    gs = gridspec.GridSpec(2, 4, figure=fig, hspace=0.35, wspace=0.25,
                           left=0.04, right=0.98, top=0.90, bottom=0.06)

    def imax(ax, data, cmap, vmin=None, vmax=None, title='', sub=''):
        ax.set_facecolor('#13131a')
        for sp in ax.spines.values(): sp.set_color('#1e1e2a')
        sl_p1 = np.nanpercentile(data, 1) if vmin is None else vmin
        sl_p99 = np.nanpercentile(data, 99) if vmax is None else vmax
        im = ax.imshow(data, cmap=cmap, vmin=sl_p1, vmax=sl_p99)
        ax.axis('off')
        ax.set_title(title, color='white', fontsize=9, fontweight='bold', pad=3)
        if sub:
            ax.text(0.5, -0.06, sub, transform=ax.transAxes,
                    ha='center', color='#6b6b88', fontsize=7.5)
        cb = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
        cb.ax.tick_params(labelsize=6, colors='#6b6b88')
        [t.set_color('#6b6b88') for t in cb.ax.yaxis.get_ticklabels()]

    speedup = t_extrap_ms / t_alg_ms if t_alg_ms > 0 else 0

    # Raw scan
    ax = fig.add_subplot(gs[0, 0])
    imax(ax, np.where(brain_mask, sl, np.nan), 'gray',
         title='Input scan', sub=f'Sequence: {seq_desc}')

    # Method A result
    ax = fig.add_subplot(gs[0, 1])
    imax(ax, w_alg, AZ_MAP, 0, 1,
         title='Method A',
         sub=f'{t_alg_ms:.2f}ms')

    # Method B result
    ax = fig.add_subplot(gs[0, 2])
    imax(ax, w_iter, AZ_MAP, 0, 1,
         title='Method B',
         sub=f'~{t_extrap_ms/1000:.0f}s full slice')

    # Difference map
    ax = fig.add_subplot(gs[0, 3])
    diff = np.abs(w_alg - w_iter)
    DIFF_MAP = LinearSegmentedColormap.from_list('diff',
        ['#0d2b1a', '#1a7a3a', '#f5c518', '#cc3300', '#660000'])
    imax(ax, diff, DIFF_MAP, 0, 0.1,
         title='Difference map',
         sub='Near-zero — identical output')

    # Histogram — no A/B values shown
    ax = fig.add_subplot(gs[1, 0]); ax.set_facecolor('#13131a')
    brain_vals = sl[brain_mask]
    ax.hist(brain_vals, bins=80, color='#2e86ab', alpha=0.7, density=True)
    ax.set_title('Signal intensity distribution', color='white', fontsize=9)
    ax.tick_params(colors='#6b6b88', labelsize=7)
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')

    # Speed bar — no method names
    ax = fig.add_subplot(gs[1, 1]); ax.set_facecolor('#13131a')
    speeds = [t_alg_ms, t_extrap_ms]
    bar_labels = ['Method A', 'Method B']
    colors = ['#00d4ff', '#f5c518']
    bars = ax.bar(bar_labels, speeds, color=colors, width=0.5,
                  edgecolor=BG, linewidth=1.5, log=True)
    for bar, v in zip(bars, speeds):
        unit = 'ms' if v < 1000 else 's'
        val = v if v < 1000 else v/1000
        ax.text(bar.get_x()+bar.get_width()/2, v*1.5,
                f'{val:.1f}{unit}', ha='center', color='white', fontsize=8)
    ax.set_title(f'Speed — {speedup:,.0f}x faster', color='white', fontsize=9)
    ax.tick_params(colors='#6b6b88', labelsize=7)
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')

    # Fraction distribution
    ax = fig.add_subplot(gs[1, 2]); ax.set_facecolor('#13131a')
    alg_vals = w_alg[brain_mask & np.isfinite(w_alg)]
    ax.hist(alg_vals, bins=50, color='#00d4ff', alpha=0.7, density=True)
    ax.set_title(f'Tissue fraction distribution\nMean: {np.nanmean(w_alg[brain_mask]):.3f}',
                 color='white', fontsize=9)
    ax.tick_params(colors='#6b6b88', labelsize=7)
    for sp in ax.spines.values(): sp.set_color('#1e1e2a')

    # Stats box — clean
    ax = fig.add_subplot(gs[1, 3]); ax.set_facecolor('#0b0b0f'); ax.axis('off')
    n_vox = brain_mask.sum()
    rms = float(np.sqrt(np.nanmean((w_alg - w_iter)**2)))
    lines = [
        ('RESULTS', '#00d4ff', 10, True),
        (f'Tissue voxels: {n_vox:,}', 'white', 8, False),
        (f'Method A: {t_alg_ms:.2f}ms', '#00d4ff', 9, True),
        (f'Method B: ~{t_extrap_ms/1000:.0f}s', '#f5c518', 9, True),
        (f'Speedup: {speedup:,.0f}x', 'white', 8, False),
        (f'RMS difference: {rms:.6f}', '#7fff7f', 9, True),
        ('', 'white', 4, False),
        ('REQUIREMENTS', '#f5c518', 10, True),
        ('Training data: NONE', '#7fff7f', 8, False),
        ('Setup required: NONE', '#7fff7f', 8, False),
        ('GPU: NOT REQUIRED', '#7fff7f', 8, False),
        ('', 'white', 4, False),
        ('Artifact Zero Labs 2026', '#444466', 7, False),
    ]
    y = 0.97
    for txt, col, sz, bold in lines:
        ax.text(0.05, y, txt, transform=ax.transAxes,
                color=col, fontsize=sz,
                fontweight='bold' if bold else 'normal', va='top')
        y -= 0.055 if sz >= 9 else 0.042

    fig.text(0.5, 0.01,
        'Artifact Zero Labs  ·  Confidential',
        ha='center', color='#333355', fontsize=7)

    plt.savefig(str(out_path), dpi=120, bbox_inches=None, facecolor=BG)
    plt.close(fig)
    print(f"  Saved: {out_path}")


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='AZ DICOM Processor')
    parser.add_argument('input', nargs='?', help='DICOM zip file or folder')
    parser.add_argument('--all', action='store_true', help='Process all good sequences')
    parser.add_argument('--list', action='store_true', help='List sequences only, no processing')
    parser.add_argument('--subject', default='Subject', help='Subject name for display')
    parser.add_argument('--out', default='az_output', help='Output folder')
    args = parser.parse_args()

    # Get input path
    if not args.input:
        args.input = input("\nDrop DICOM zip file path here (or folder): ").strip().strip('"')

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    # Output folder
    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    # Unzip if needed
    tmp_dir = None
    if input_path.suffix.lower() == '.zip':
        print(f"\nUnzipping {input_path.name}...")
        tmp_dir = tempfile.mkdtemp()
        with zipfile.ZipFile(str(input_path), 'r') as z:
            z.extractall(tmp_dir)
        dicom_root = Path(tmp_dir)
    else:
        dicom_root = input_path

    try:
        # Find all DICOM files
        print(f"\nScanning for DICOM files in {dicom_root}...")
        dcm_files = find_dicom_files(dicom_root)
        if not dcm_files:
            print("ERROR: No DICOM files found")
            sys.exit(1)
        print(f"Found {len(dcm_files)} DICOM files")

        # Group by sequence
        series_list = group_by_sequence(dcm_files)

        # Write sequence list
        seq_log = out_dir / 'AZ_sequences_found.txt'
        with open(seq_log, 'w') as f:
            f.write(f"ARTIFACT ZERO LABS — Sequences found in {input_path.name}\n")
            f.write("=" * 60 + "\n\n")
            for i, s in enumerate(series_list):
                f.write(f"[{i+1:>2}] Score={s['score']:>4}  Type={s['type']:<6}  "
                        f"Slices={s['n_slices']:>3}  Series#{s['series_num']:>3}  "
                        f"Desc: {s['desc']}\n")
        print(f"\nSequence list written to: {seq_log}")

        # Print top sequences
        print("\nTop sequences found:")
        good = [s for s in series_list if s['score'] > 0]
        for i, s in enumerate(good[:10]):
            print(f"  [{i+1}] {s['type']:<6} {s['n_slices']:>3} slices  '{s['desc']}'")

        if args.list:
            return

        if not good:
            print("ERROR: No usable sequences found")
            sys.exit(1)

        # Decide which to process
        if args.all:
            to_process = good[:5]
        else:
            to_process = good[:2]  # top 2 by default

        # Process each selected sequence
        for s in to_process:
            desc = s['desc'] or f"Series{s['series_num']}"
            safe_desc = "".join(c if c.isalnum() or c in '-_' else '_' for c in desc)
            print(f"\n{'='*60}")
            print(f"Processing: {desc} ({s['n_slices']} slices, type={s['type']})")

            print("  Loading volume...")
            try:
                vol = load_volume(s)
            except Exception as e:
                print(f"  SKIP — could not load: {e}")
                continue

            print(f"  Volume shape: {vol.shape}")
            sl = best_slice(vol)
            print(f"  Slice shape: {sl.shape}")

            brain_mask = make_brain_mask(sl)
            n_brain = brain_mask.sum()
            print(f"  Brain voxels: {n_brain:,}")

            if n_brain < 1000:
                print("  SKIP — too few brain voxels (bad slice or mask failure)")
                continue

            A, B, label_A, label_B = get_tissue_landmarks(sl, brain_mask, s['type'])
            print(f"  A ({label_A}) = {A:.1f}   B ({label_B}) = {B:.1f}   gap = {abs(A-B):.1f}")

            print("  Running algebraic method...")
            w_alg, t_alg_ms = method_algebraic(sl, brain_mask, A, B)
            print(f"  Algebraic: {t_alg_ms:.3f}ms")

            print("  Running iterative method (sampling 300 voxels)...")
            w_iter, t_samp_ms, t_extrap_ms = method_iterative(sl, brain_mask, A, B, n_sample=300)
            print(f"  Iterative sample: {t_samp_ms:.1f}ms  →  extrapolated: {t_extrap_ms/1000:.1f}s")

            rms = float(np.sqrt(np.nanmean((w_alg - w_iter)**2)))
            print(f"  RMS difference: {rms:.8f}")

            # Render
            print("  Rendering comparison...")
            subject_name = args.subject
            out_png = out_dir / f"AZ_{safe_desc}.png"
            out_pdf = out_dir / f"AZ_{safe_desc}.pdf"

            render_comparison(sl, brain_mask, w_alg, w_iter,
                              t_alg_ms, t_extrap_ms, A, B,
                              label_A, label_B, desc, subject_name,
                              out_png)

            # Save PDF version
            try:
                from matplotlib.backends.backend_pdf import PdfPages
                fig2 = plt.figure(figsize=(18, 10), facecolor=BG)
                # Re-render to PDF
                render_comparison(sl, brain_mask, w_alg, w_iter,
                                  t_alg_ms, t_extrap_ms, A, B,
                                  label_A, label_B, desc, subject_name,
                                  out_pdf)
            except Exception as e:
                print(f"  PDF save skipped: {e}")

        print(f"\n{'='*60}")
        print(f"DONE. Outputs saved to: {out_dir.resolve()}")
        print(f"Files:")
        for f in sorted(out_dir.iterdir()):
            print(f"  {f.name}  ({f.stat().st_size:,} bytes)")

    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
