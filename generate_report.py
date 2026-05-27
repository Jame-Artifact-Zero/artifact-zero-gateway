#!/usr/bin/env python3
"""
generate_report.py
==================
HTML report generator for k7 cervical spine measurement pipeline output.

Usage:
    python generate_report.py <k7_json> <dicom_zip> <output_html>
"""
import sys
import io
import os
import json
import base64
import zipfile
import tempfile
import shutil
import html
import math
from datetime import datetime
from collections import defaultdict
from pathlib import Path

import numpy as np
import pydicom
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
CSF_FLAG_MM = 2.0
CONF_FLAG = 0.3

MARKER_COLORS = {
    'cord_center':          '#FF0000',
    'csf_anterior':         '#1E90FF',
    'csf_posterior':        '#1E90FF',
    'csf_left':             '#1E90FF',
    'csf_right':            '#1E90FF',
    'canal_wall_anterior':  '#00C000',
    'canal_wall_posterior': '#00C000',
    'canal_wall_left':      '#00C000',
    'canal_wall_right':     '#00C000',
    'cord_centerline':      '#FFD700',
}


# ──────────────────────────────────────────────────────────────────────
# Geometry
# ──────────────────────────────────────────────────────────────────────
def euclid(a, b):
    """3D Euclidean distance between two xyz_mm lists."""
    return math.sqrt(sum((float(a[i]) - float(b[i])) ** 2 for i in range(3)))


def xyz_to_pixel(xyz_mm, ipp, iop, ps):
    """Convert a patient-coordinate (x,y,z) point to image (row, col) using
    the slice's ImagePositionPatient, ImageOrientationPatient, PixelSpacing.

    DICOM affine: P = IPP + col*ps[1]*row_dir + row*ps[0]*col_dir
    Solve the 3-equation, 2-unknown system via least-squares.
    """
    ipp = np.array(ipp, dtype=float)
    iop = np.array(iop, dtype=float)
    row_dir = iop[0:3]  # patient direction per +1 col
    col_dir = iop[3:6]  # patient direction per +1 row
    delta = np.array(xyz_mm, dtype=float) - ipp
    A = np.column_stack([ps[1] * row_dir, ps[0] * col_dir])  # 3x2
    try:
        sol, *_ = np.linalg.lstsq(A, delta, rcond=None)
        col, row = float(sol[0]), float(sol[1])
    except np.linalg.LinAlgError:
        return None, None
    return row, col


# ──────────────────────────────────────────────────────────────────────
# DICOM scan
# ──────────────────────────────────────────────────────────────────────
def is_likely_dicom(path):
    """Cheap magic-byte check before pydicom.dcmread to avoid memory leaks
    on binary viewer files / autorun stubs in hospital-burned zips."""
    try:
        with open(path, 'rb') as fh:
            preamble = fh.read(132)
        if len(preamble) < 132:
            return False
        if preamble[128:132] == b'DICM':
            return True
        if preamble[0:2] in (b'\x02\x00', b'\x08\x00'):
            return True
        return False
    except Exception:
        return False


_SKIP_EXT = {'.PNG','.JPG','.JPEG','.PDF','.EXE','.DLL','.INI','.INF',
             '.LOG','.ZIP','.TXT','.HTML','.MD','.CSV','.XML','.YML','.YAML',
             '.DS_STORE','.LNK','.DOC','.DOCX','.XLS','.XLSX','.PPT','.PPTX',
             '.RAR','.7Z','.TAR','.GZ','.BMP','.GIF','.TIFF','.TIF','.SVG',
             '.MP4','.MOV','.AVI','.MP3','.WAV','.SO','.DYLIB','.APP','.URL'}
_SKIP_NAMES = {'DICOMDIR','AUTORUN','README','INDEX','LICENSE','LOCKFILE'}


def classify_orientation(iop):
    if iop is None or len(iop) < 6:
        return 'UNK'
    iop = [float(v) for v in iop]
    row = np.array(iop[0:3])
    col = np.array(iop[3:6])
    normal = np.cross(row, col)
    return {0: 'SAG', 1: 'COR', 2: 'AX'}[int(np.argmax(np.abs(normal)))]


def is_t2(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    if 'T1' in desc and 'T2' not in desc:
        return False
    if 'T2' in desc:
        return True
    te = getattr(ds, 'EchoTime', None)
    if te is not None and float(te) > 60:
        return True
    return False


def scan_dicom_dir(work_dir):
    """Walk work_dir, return dict series_uid -> list[(path, header_ds)]."""
    by_series = defaultdict(list)
    for p in Path(work_dir).rglob('*'):
        if not p.is_file():
            continue
        if p.suffix.upper() in _SKIP_EXT:
            continue
        if p.stem.upper() in _SKIP_NAMES:
            continue
        if not is_likely_dicom(str(p)):
            continue
        try:
            ds = pydicom.dcmread(str(p), stop_before_pixels=True, force=True)
        except Exception:
            continue
        if not hasattr(ds, 'SeriesInstanceUID'):
            continue
        if str(getattr(ds, 'Modality', '')).upper() != 'MR':
            continue
        by_series[ds.SeriesInstanceUID].append((str(p), ds))
    return by_series


def pick_axial_series(by_series_uid, preferred_uid=None):
    """Return list of (path, header_ds) for the chosen axial T2 series,
    sorted by IPP[2] ascending. Prefer the series whose UID matches
    series_used.axial_t2.series_uid from the k7 response."""
    if preferred_uid and preferred_uid in by_series_uid:
        chosen = by_series_uid[preferred_uid]
    else:
        cands = []
        for uid, items in by_series_uid.items():
            if not items:
                continue
            ds0 = items[0][1]
            iop = getattr(ds0, 'ImageOrientationPatient', None)
            if classify_orientation(iop) != 'AX':
                continue
            if not is_t2(ds0):
                continue
            cands.append((len(items), items))
        if not cands:
            return []
        cands.sort(key=lambda x: -x[0])
        chosen = cands[0][1]

    def z_of(item):
        ds = item[1]
        ipp = getattr(ds, 'ImagePositionPatient', [0, 0, 0])
        return float(ipp[2])
    return sorted(chosen, key=z_of)


def pick_sagittal_series(by_series_uid, preferred_uid=None):
    """Return list of (path, header_ds) for the chosen sagittal T2 series,
    sorted by IPP[0] ascending (patient left → right). Prefer the series
    whose UID matches series_used.sagittal_t2.series_uid from the k7
    response."""
    if preferred_uid and preferred_uid in by_series_uid:
        chosen = by_series_uid[preferred_uid]
    else:
        cands = []
        for uid, items in by_series_uid.items():
            if not items:
                continue
            ds0 = items[0][1]
            iop = getattr(ds0, 'ImageOrientationPatient', None)
            if classify_orientation(iop) != 'SAG':
                continue
            if not is_t2(ds0):
                continue
            cands.append((len(items), items))
        if not cands:
            return []
        cands.sort(key=lambda x: -x[0])
        chosen = cands[0][1]

    def x_of(item):
        ds = item[1]
        ipp = getattr(ds, 'ImagePositionPatient', [0, 0, 0])
        return float(ipp[0])
    return sorted(chosen, key=x_of)


def read_pixels(path):
    """Read pixel array, falling back to raw bytes if compressed reads fail."""
    ds = pydicom.dcmread(path, force=True)
    try:
        arr = ds.pixel_array.astype(np.float64)
    except Exception:
        try:
            import copy
            ds2 = copy.copy(ds)
            ds2.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
            arr = ds2.pixel_array.astype(np.float64)
        except Exception:
            rows = int(getattr(ds, 'Rows', 512))
            cols = int(getattr(ds, 'Columns', 512))
            try:
                raw = np.frombuffer(ds.PixelData, dtype=np.uint16)
                arr = raw[:rows * cols].reshape(rows, cols).astype(np.float64)
            except Exception:
                arr = np.zeros((rows, cols), dtype=np.float64)
    return ds, arr


# ──────────────────────────────────────────────────────────────────────
# Per-slice measurement aggregation from markers
# ──────────────────────────────────────────────────────────────────────
def group_markers_by_slice(markers):
    """Return (by_slice, centerline).
    by_slice: dict slice_inst -> {type: marker_dict} for axial-side markers.
    centerline: list of cord_centerline markers (sagittal-side).
    """
    by_slice = defaultdict(dict)
    centerline = []
    for m in markers:
        t = m.get('type', '')
        if t == 'cord_centerline':
            centerline.append(m)
            continue
        si = m.get('slice_inst')
        if si is None:
            continue
        by_slice[si][t] = m
    return by_slice, centerline


def per_slice_row(slice_inst, mks):
    """Build the measurement row for a single slice from its marker dict."""
    cc = mks.get('cord_center')
    row = {
        'slice_inst': slice_inst,
        'z_mm':       None,
        'confidence': None,
        'csf_a':      None, 'csf_p':    None, 'csf_l': None, 'csf_r': None,
        'canal_ap':   None, 'canal_lr': None,
        'cord_x':     None, 'cord_y':   None,
        'flags':      [],
    }
    if cc is None:
        row['flags'].append('NO_CORD_CENTER')
        return row
    xyz = cc.get('xyz_mm', [None] * 3)
    row['cord_x'] = xyz[0] if len(xyz) > 0 else None
    row['cord_y'] = xyz[1] if len(xyz) > 1 else None
    row['z_mm']   = xyz[2] if len(xyz) > 2 else None
    row['confidence'] = cc.get('confidence')

    def dist(t):
        m = mks.get(t)
        if m is None:
            return None
        return euclid(cc['xyz_mm'], m['xyz_mm'])

    row['csf_a'] = dist('csf_anterior')
    row['csf_p'] = dist('csf_posterior')
    row['csf_l'] = dist('csf_left')
    row['csf_r'] = dist('csf_right')

    a = mks.get('canal_wall_anterior')
    p = mks.get('canal_wall_posterior')
    l = mks.get('canal_wall_left')
    r = mks.get('canal_wall_right')
    if a and p:
        row['canal_ap'] = euclid(a['xyz_mm'], p['xyz_mm'])
    if l and r:
        row['canal_lr'] = euclid(l['xyz_mm'], r['xyz_mm'])

    for key, label in [('csf_a', 'CSF_A'), ('csf_p', 'CSF_P'),
                       ('csf_l', 'CSF_L'), ('csf_r', 'CSF_R')]:
        v = row[key]
        if v is not None and v < CSF_FLAG_MM:
            row['flags'].append(f'{label}<{CSF_FLAG_MM}mm')
    if row['confidence'] is not None and row['confidence'] < CONF_FLAG:
        row['flags'].append(f'CONF<{CONF_FLAG}')
    return row


# ──────────────────────────────────────────────────────────────────────
# Image rendering
# ──────────────────────────────────────────────────────────────────────
def fig_to_b64(fig, dpi=110):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


def window_image(arr):
    """Auto-window to 1–99 percentile for display."""
    lo, hi = np.percentile(arr, [1, 99])
    if hi <= lo:
        hi = lo + 1
    img = np.clip((arr - lo) / (hi - lo), 0, 1)
    return img


def _crop_window(arr, cy, cx, ps_mm, pad_mm=30):
    """Return (cropped_arr, (r0, c0)) where (r0, c0) is the offset of the
    crop within the full image. cy/cx are the pixel coordinates the crop
    is centered on. pad_mm is the half-window in millimeters."""
    H, W = arr.shape
    pad_r = int(round(pad_mm / float(ps_mm[0])))
    pad_c = int(round(pad_mm / float(ps_mm[1])))
    r0 = max(0, int(cy) - pad_r)
    r1 = min(H, int(cy) + pad_r + 1)
    c0 = max(0, int(cx) - pad_c)
    c1 = min(W, int(cx) + pad_c + 1)
    if r1 <= r0 or c1 <= c0:
        return arr, (0, 0)
    return arr[r0:r1, c0:c1], (r0, c0)


def _draw_axial_base(ax, cropped_img, title):
    ax.imshow(cropped_img, cmap='gray', interpolation='bilinear')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title(title, fontsize=11, weight='bold')


def render_axial_panels(arr, ds, slice_markers, slice_row):
    """Render three side-by-side axial panels for a flagged slice:
      1) Canal Geometry   -- cord_center (red) + canal_wall_* (green)
      2) CSF Spaces       -- csf_* (blue) with cord→csf lines + mm labels
      3) Source           -- raw image, no overlays

    All three panels are cropped to ±30 mm around the cord_center.
    Returns a single base64 PNG containing the 3 panels side-by-side.
    """
    ipp = getattr(ds, 'ImagePositionPatient', [0, 0, 0])
    iop = getattr(ds, 'ImageOrientationPatient', [1, 0, 0, 0, 1, 0])
    ps  = getattr(ds, 'PixelSpacing', [1.0, 1.0])
    H, W = arr.shape
    img = window_image(arr)

    # Resolve all marker pixel positions
    marker_positions = {}
    for t, m in slice_markers.items():
        if t == 'cord_centerline':
            continue
        r, c = xyz_to_pixel(m['xyz_mm'], ipp, iop, ps)
        if r is None or c is None:
            continue
        marker_positions[t] = (r, c)

    cc = marker_positions.get('cord_center')
    if cc is None:
        cy, cx = H // 2, W // 2  # fall back to center of frame
    else:
        cy, cx = cc

    cropped, (r0, c0) = _crop_window(img, cy, cx, ps, pad_mm=30)

    def to_crop(rc):
        return (rc[0] - r0, rc[1] - c0)

    fig, axes = plt.subplots(1, 3, figsize=(15, 5.2))

    # ── Panel 1: Canal Geometry ─────────────────────────────────────
    _draw_axial_base(axes[0], cropped, 'Canal Geometry')
    if cc is not None:
        ccy, ccx = to_crop(cc)
        axes[0].plot(ccx, ccy, 'o', color=MARKER_COLORS['cord_center'],
                     markersize=14, markeredgecolor='black',
                     markeredgewidth=0.8, zorder=3)
    for t in ('canal_wall_anterior', 'canal_wall_posterior',
              'canal_wall_left', 'canal_wall_right'):
        pos = marker_positions.get(t)
        if pos is None:
            continue
        wy, wx = to_crop(pos)
        axes[0].plot(wx, wy, 'o', color=MARKER_COLORS[t], markersize=9,
                     markeredgecolor='black', markeredgewidth=0.6, zorder=3)
    # Canal AP/LR labels OUTSIDE the image, in the matplotlib axes annotation
    # using xycoords='axes fraction' so they sit beside the panel, not on top.
    geom_lines = []
    if slice_row.get('canal_ap') is not None:
        geom_lines.append(f"Canal AP: {slice_row['canal_ap']:.2f} mm")
    if slice_row.get('canal_lr') is not None:
        geom_lines.append(f"Canal LR: {slice_row['canal_lr']:.2f} mm")
    if cc is not None:
        geom_lines.append(
            f"cord_x: {slice_row['cord_x']:.2f}  cord_y: {slice_row['cord_y']:.2f}"
        )
    if geom_lines:
        axes[0].text(0.02, -0.05, '\n'.join(geom_lines),
                     transform=axes[0].transAxes,
                     fontsize=9, color='#222',
                     verticalalignment='top', family='monospace')

    # ── Panel 2: CSF Spaces ─────────────────────────────────────────
    _draw_axial_base(axes[1], cropped, 'CSF Spaces')
    if cc is not None:
        ccy, ccx = to_crop(cc)
        # small black dot for cord_center reference (not the focus here)
        axes[1].plot(ccx, ccy, 'o', color='#000000', markersize=4,
                     markeredgecolor='white', markeredgewidth=0.4, zorder=3)
        csf_specs = [
            ('csf_anterior',  'csf_a', 'CSF A'),
            ('csf_posterior', 'csf_p', 'CSF P'),
            ('csf_left',      'csf_l', 'CSF L'),
            ('csf_right',     'csf_r', 'CSF R'),
        ]
        csf_lines = []
        for marker_type, row_key, lbl in csf_specs:
            pos = marker_positions.get(marker_type)
            val = slice_row.get(row_key)
            if pos is None or val is None:
                continue
            py, px = to_crop(pos)
            axes[1].plot(px, py, 'o', color=MARKER_COLORS[marker_type],
                         markersize=9, markeredgecolor='black',
                         markeredgewidth=0.6, zorder=3)
            axes[1].plot([ccx, px], [ccy, py],
                         color=MARKER_COLORS[marker_type], linewidth=1.2,
                         alpha=0.85, zorder=2)
            flag = ' (FLAGGED)' if val < CSF_FLAG_MM else ''
            csf_lines.append(f"{lbl}: {val:.2f} mm{flag}")
        if csf_lines:
            axes[1].text(0.02, -0.05, '\n'.join(csf_lines),
                         transform=axes[1].transAxes,
                         fontsize=9, color='#222',
                         verticalalignment='top', family='monospace')

    # ── Panel 3: Source ─────────────────────────────────────────────
    _draw_axial_base(axes[2], cropped, 'Source')
    z = slice_row.get('z_mm')
    src_lines = [f"slice_inst: {slice_row['slice_inst']}"]
    if z is not None:
        src_lines.append(f"z = {z:.2f} mm")
    if slice_row.get('confidence') is not None:
        src_lines.append(f"confidence: {slice_row['confidence']:.3f}")
    axes[2].text(0.02, -0.05, '\n'.join(src_lines),
                 transform=axes[2].transAxes,
                 fontsize=9, color='#222',
                 verticalalignment='top', family='monospace')

    plt.subplots_adjust(wspace=0.08, bottom=0.18)
    return fig_to_b64(fig)


def render_sagittal_panel(sag_ds, sag_arr, z_mm, slice_inst):
    """Render the sagittal reference panel with a horizontal line at z_mm.

    Returns a base64 PNG. sag_ds/sag_arr are the chosen sagittal slice
    (mid-series). The horizontal line is drawn at the pixel row that
    corresponds to z_mm in patient coordinates.
    """
    if sag_ds is None or sag_arr is None:
        return None
    img = window_image(sag_arr)
    H, W = img.shape
    ipp = np.array(getattr(sag_ds, 'ImagePositionPatient', [0, 0, 0]),
                   dtype=float)
    iop = np.array(getattr(sag_ds, 'ImageOrientationPatient',
                            [0, 1, 0, 0, 0, -1]), dtype=float)
    ps  = np.array(getattr(sag_ds, 'PixelSpacing', [1.0, 1.0]),
                   dtype=float)

    # On a sagittal slice, image rows usually carry the cranio-caudal (z)
    # axis. The row direction in patient frame is ps[0] * iop[3:6].
    row_step = ps[0] * iop[3:6]
    # Map a target z back to pixel row. dz_total = (row_step[2]) * row
    if abs(row_step[2]) > 1e-6 and z_mm is not None:
        row_for_z = (z_mm - ipp[2]) / row_step[2]
    else:
        row_for_z = None

    fig, ax = plt.subplots(figsize=(4.6, 5.2))
    ax.imshow(img, cmap='gray', interpolation='bilinear', aspect='auto')
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title('Sagittal Reference', fontsize=11, weight='bold')

    if row_for_z is not None and 0 <= row_for_z < H:
        ax.axhline(row_for_z, color='#FF3030', linewidth=1.6, alpha=0.9)
        ax.text(W * 0.02, row_for_z - 6, f'z = {z_mm:.1f} mm',
                color='#FF3030', fontsize=9, weight='bold',
                bbox=dict(boxstyle='round,pad=0.2',
                          facecolor='black', edgecolor='#FF3030',
                          alpha=0.75))

    foot = f"sagittal mid-slice · axial slice_inst {slice_inst}"
    ax.text(0.02, -0.05, foot, transform=ax.transAxes,
            fontsize=9, color='#222', verticalalignment='top',
            family='monospace')

    plt.subplots_adjust(bottom=0.18)
    return fig_to_b64(fig)


def render_centerline_plot(centerline_markers):
    if not centerline_markers:
        return None
    xs = np.array([m['xyz_mm'][0] for m in centerline_markers], dtype=float)
    zs = np.array([m['xyz_mm'][2] for m in centerline_markers], dtype=float)
    order = np.argsort(zs)[::-1]  # superior (high z) first
    xs = xs[order]
    zs = zs[order]

    mean_x = float(np.mean(xs))
    devs = xs - mean_x
    flagged = np.abs(devs) > 2.0

    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(zs, xs, '-', color='#444', linewidth=1, alpha=0.6,
            label='centerline')
    ax.plot(zs[~flagged], xs[~flagged], 'o',
            color=MARKER_COLORS['cord_centerline'], markersize=4,
            markeredgecolor='black', markeredgewidth=0.3,
            label='within ±2 mm')
    if flagged.any():
        ax.plot(zs[flagged], xs[flagged], 'o', color='#FF3030',
                markersize=6, markeredgecolor='black', markeredgewidth=0.5,
                label='deviation > 2 mm')
    ax.axhline(mean_x, color='#1E90FF', linestyle='--', linewidth=1,
               label=f'mean x = {mean_x:.2f} mm')
    ax.axhline(mean_x + 2, color='#FF3030', linestyle=':', linewidth=0.8,
               alpha=0.6)
    ax.axhline(mean_x - 2, color='#FF3030', linestyle=':', linewidth=0.8,
               alpha=0.6)
    ax.set_xlabel('z (mm, superior → inferior, left = superior)')
    ax.set_ylabel('x (mm, left-right deviation)')
    ax.set_title('Cord centerline lateral deviation')
    ax.invert_xaxis()
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best', fontsize=9)
    return fig_to_b64(fig)


def render_anatomy_diagram():
    """Stylized axial cross-section of cervical spine — canal, CSF, cord.
    Pure matplotlib, returned as base64 PNG. No external image deps."""
    from matplotlib.patches import Ellipse, FancyArrowPatch

    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    ax.set_xlim(-5, 5)
    ax.set_ylim(-5, 5)
    ax.set_aspect('equal')
    ax.axis('off')

    # Outer = spinal canal (bone), tan
    canal = Ellipse((0, 0), width=6.0, height=5.4,
                    facecolor='#D4B896', edgecolor='#8B6F3A',
                    linewidth=1.5, zorder=1)
    ax.add_patch(canal)
    # CSF ring (light blue) — drawn as a slightly smaller ellipse to leave
    # a visible tan bone rim
    csf = Ellipse((0, 0), width=5.2, height=4.6,
                  facecolor='#AED6F1', edgecolor='#6FA8D6',
                  linewidth=1.0, zorder=2)
    ax.add_patch(csf)
    # Inner = spinal cord, pink
    cord = Ellipse((0, 0), width=2.4, height=2.0,
                   facecolor='#F4C2C2', edgecolor='#B07070',
                   linewidth=1.2, zorder=3)
    ax.add_patch(cord)

    # Anatomy labels with leader lines
    label_specs = [
        ('Spinal Cord',          (1.6, 0.3),   (3.6, 1.4)),
        ('Fluid (CSF)',          (2.0, -1.4),  (3.9, -2.2)),
        ('Spinal Canal (Bone)',  (-2.4, 2.0),  (-4.6, 3.1)),
    ]
    for txt, xy_src, xy_text in label_specs:
        ax.annotate(txt, xy=xy_src, xytext=xy_text,
                    fontsize=11, color='#222', weight='bold',
                    arrowprops=dict(arrowstyle='-', color='#222', lw=0.8),
                    ha='left', va='center',
                    bbox=dict(boxstyle='round,pad=0.25',
                              facecolor='white', edgecolor='#aaa',
                              alpha=0.9))

    # Four measurement arrows from the cord center outward.
    # Colors match the table column header classes.
    arrow_specs = [
        ('A — Front', (0,  1.0), (0,  2.3), '#E53935'),  # anterior, red
        ('P — Back',  (0, -1.0), (0, -2.3), '#1E88E5'),  # posterior, blue
        ('L — Left',  (-1.2, 0), (-2.6, 0), '#2E7D32'),  # left, green
        ('R — Right', ( 1.2, 0), ( 2.6, 0), '#EF6C00'),  # right, orange
    ]
    for lbl, src, dst, color in arrow_specs:
        ax.add_patch(FancyArrowPatch(src, dst, arrowstyle='->',
                                     mutation_scale=14, color=color,
                                     linewidth=2.0, zorder=4))
        # Label outside the arrow tip
        lx = dst[0] + (0.6 if dst[0] >= 0 else -0.6)
        ly = dst[1] + (0.5 if dst[1] >= 0 else -0.5)
        ha = 'left' if dst[0] > 0 else ('right' if dst[0] < 0 else 'center')
        va = 'bottom' if dst[1] > 0 else ('top' if dst[1] < 0 else 'center')
        if dst[0] == 0:
            lx = 0
        if dst[1] == 0:
            ly = 0
        ax.text(lx, ly, lbl, fontsize=11, color=color, weight='bold',
                ha=ha, va=va,
                bbox=dict(boxstyle='round,pad=0.2',
                          facecolor='white', edgecolor=color, alpha=0.85))

    ax.set_title('Axial cross-section: what we measure',
                 fontsize=12, weight='bold')
    return fig_to_b64(fig)


# ──────────────────────────────────────────────────────────────────────
# HTML assembly
# ──────────────────────────────────────────────────────────────────────
CSS = """
body { font-family: Arial, Helvetica, sans-serif; background:#f5f5f5;
       color:#222; margin:0; padding:24px; }
h1 { font-size:22px; margin:0 0 8px 0; }
h2 { font-size:17px; margin:24px 0 8px 0; border-bottom:1px solid #999;
     padding-bottom:4px; }
h3 { font-size:14px; margin:18px 0 6px 0; }
.container { max-width:1200px; margin:0 auto; background:white;
             padding:24px; box-shadow:0 1px 4px rgba(0,0,0,0.1); }
table { border-collapse:collapse; width:100%; font-size:13px; margin:8px 0; }
th, td { padding:6px 8px; border:1px solid #ccc; text-align:right;
         font-size:13px; }
th { background:#305496; color:white; text-align:center; font-size:13px; }
td.lbl { text-align:left; background:#f0f0f0; font-weight:bold; }
tr.flagged td { background:#ffe0e0; }
tr.flagged td:first-child { background:#ffb0b0; font-weight:bold; }
.kv { display:grid; grid-template-columns:200px 1fr; gap:4px 12px;
      font-size:13px; margin:6px 0 12px 0; }
.kv div:nth-child(odd) { color:#555; }
.kv div:nth-child(even) { font-weight:bold; }
.flag-badge { display:inline-block; background:#cc0000; color:white;
              padding:1px 6px; border-radius:3px; font-size:11px;
              margin-left:4px; }
.slice-block { margin:18px 0; padding:14px; border:1px solid #ccc;
               background:#fafafa; }
.panels { display:grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap:10px; align-items:start; margin-bottom:12px; }
.panels .panel img { max-width:100%; height:auto; display:block; }
.panel-caption { font-size:11px; color:#555; text-align:center;
                 margin-top:4px; }
.centerline-img { max-width:100%; height:auto; display:block;
                  margin:8px auto; }
.anatomy-block { margin:12px 0 20px 0; background:#fcfcfc;
                 border:1px solid #ddd; padding:14px; border-radius:4px; }
.anatomy-block img { max-width:480px; height:auto; display:block;
                     margin:0 auto 10px auto; }
.anatomy-block p { font-size:13px; color:#333; line-height:1.5;
                   margin:8px 0 0 0; max-width:740px; }
.muted { color:#888; font-size:11px; }
.legend { font-size:11px; color:#555; margin:4px 0 8px 0; }
.legend span { margin-right:14px; }
.dot { display:inline-block; width:10px; height:10px; border-radius:50%;
       margin-right:4px; vertical-align:middle; border:1px solid #333; }

/* Section 2 summary table — relative-scale bars */
.summary-table .bar-cell { padding:3px 6px; }
.bar-row { display:flex; align-items:center; gap:6px;
           justify-content:flex-end; white-space:nowrap; }
.bar-val { display:inline-block; min-width:46px; text-align:right;
           font-variant-numeric: tabular-nums; font-size:13px; }
.bar-track { display:inline-block; width:60px; height:8px;
             background:#eee; border:1px solid #ccc; border-radius:2px;
             overflow:hidden; position:relative; }
.bar-fill { display:block; height:100%; background:#888; }
.bar-pct { display:inline-block; min-width:30px; font-size:11px;
           color:#666; text-align:right;
           font-variant-numeric: tabular-nums; }

/* Section 2 — two tables side by side, with matched row highlight */
.tables-pair { display:grid; grid-template-columns:1fr 1fr;
               gap:16px; align-items:flex-start; }
.tables-pair table { margin:0; }
@media (max-width: 1000px) {
  .tables-pair { grid-template-columns:1fr; }
}

/* Column header color codes — match the anatomy diagram */
th.col-csf-a   { color:#E53935; }   /* red    — anterior  */
th.col-csf-p   { color:#1E88E5; }   /* blue   — posterior */
th.col-csf-l   { color:#2E7D32; }   /* green  — left      */
th.col-csf-r   { color:#EF6C00; }   /* orange — right     */
th.col-canal-ap { color:#6A1B9A; }  /* purple             */
th.col-canal-lr { color:#00695C; }  /* dark teal          */

@media (max-width: 1100px) {
  .panels { grid-template-columns: repeat(2, 1fr); }
}
"""


def fmt(v, dp=2):
    if v is None:
        return '—'
    if isinstance(v, float):
        if math.isnan(v):
            return '—'
        return f'{v:.{dp}f}'
    return str(v)


def render_summary_tables(rows):
    """Build the Section 2 two-table layout.

    Table A: slice_inst, z_mm, confidence, CSF A, CSF P, CSF L, CSF R
    Table B: slice_inst, z_mm, Canal AP, Canal LR, cord_x, cord_y, FLAGS

    Returns a single HTML string containing the .tables-pair wrapper.
    """
    BAR_COLS = ('csf_a', 'csf_p', 'csf_l', 'csf_r', 'canal_ap', 'canal_lr')

    col_max = {}
    for k in BAR_COLS:
        vals = [r.get(k) for r in rows
                if r.get(k) is not None and not (isinstance(r.get(k), float)
                                                  and math.isnan(r.get(k)))]
        col_max[k] = max(vals) if vals else None

    # Per-column display config:
    #   key, label, decimal places, optional <th> class (header color)
    table_a_cols = [
        ('slice_inst', 'slice_inst', 0,   None),
        ('z_mm',       'z_mm',       2,   None),
        ('confidence', 'confidence', 3,   None),
        ('csf_a',      'CSF A (mm)', 2,   'col-csf-a'),
        ('csf_p',      'CSF P (mm)', 2,   'col-csf-p'),
        ('csf_l',      'CSF L (mm)', 2,   'col-csf-l'),
        ('csf_r',      'CSF R (mm)', 2,   'col-csf-r'),
    ]
    table_b_cols = [
        ('slice_inst', 'slice_inst',    0,   None),
        ('z_mm',       'z_mm',          2,   None),
        ('canal_ap',   'Canal AP (mm)', 2,   'col-canal-ap'),
        ('canal_lr',   'Canal LR (mm)', 2,   'col-canal-lr'),
        ('cord_x',     'cord_x (mm)',   2,   None),
        ('cord_y',     'cord_y (mm)',   2,   None),
        ('flags',      'FLAGS',         None, None),
    ]

    def bar_cell(val, mx, dp):
        if val is None or mx is None or mx <= 0:
            return f'<td class="bar-cell">{fmt(val, dp)}</td>'
        pct = max(0.0, min(100.0, (val / mx) * 100.0))
        return (
            '<td class="bar-cell">'
            '<div class="bar-row">'
            f'<span class="bar-val">{fmt(val, dp)}</span>'
            '<span class="bar-track">'
            f'<span class="bar-fill" style="width:{pct:.1f}%"></span>'
            '</span>'
            f'<span class="bar-pct">{pct:.0f}%</span>'
            '</div>'
            '</td>'
        )

    def render_one(cols):
        out = ['<table class="summary-table">', '<thead><tr>']
        for _, lbl, _, css_cls in cols:
            cls_attr = f' class="{css_cls}"' if css_cls else ''
            out.append(f'<th{cls_attr}>{html.escape(lbl)}</th>')
        out.append('</tr></thead><tbody>')
        for r in rows:
            cls = ' class="flagged"' if r['flags'] else ''
            out.append(f'<tr{cls}>')
            for key, _, dp, _css in cols:
                if key == 'flags':
                    txt = ', '.join(r['flags']) if r['flags'] else ''
                    out.append(
                        f'<td style="text-align:left">{html.escape(txt)}</td>'
                    )
                elif key == 'slice_inst':
                    out.append(f'<td>{r[key]}</td>')
                elif key in BAR_COLS:
                    out.append(bar_cell(r.get(key), col_max.get(key),
                                        dp if dp is not None else 2))
                else:
                    out.append(
                        f'<td>{fmt(r.get(key), dp if dp is not None else 2)}</td>'
                    )
            out.append('</tr>')
        out.append('</tbody></table>')
        return ''.join(out)

    return ('<div class="tables-pair">'
            + render_one(table_a_cols)
            + render_one(table_b_cols)
            + '</div>')


def render_single_slice_table(r):
    parts = ['<table>']
    rows = [
        ('slice_inst',           r['slice_inst']),
        ('z (mm)',               fmt(r['z_mm'], 2)),
        ('confidence',           fmt(r['confidence'], 4)),
        ('CSF anterior (mm)',    fmt(r['csf_a'], 3)),
        ('CSF posterior (mm)',   fmt(r['csf_p'], 3)),
        ('CSF left (mm)',        fmt(r['csf_l'], 3)),
        ('CSF right (mm)',       fmt(r['csf_r'], 3)),
        ('Canal AP (mm)',        fmt(r['canal_ap'], 3)),
        ('Canal LR (mm)',        fmt(r['canal_lr'], 3)),
        ('cord_x (mm)',          fmt(r['cord_x'], 3)),
        ('cord_y (mm)',          fmt(r['cord_y'], 3)),
        ('flags',                ', '.join(r['flags']) if r['flags'] else '—'),
    ]
    for k, v in rows:
        parts.append(f'<tr><td class="lbl">{html.escape(str(k))}</td>'
                     f'<td style="text-align:left">{html.escape(str(v))}</td></tr>')
    parts.append('</table>')
    return ''.join(parts)


def render_legend():
    return (
        '<div class="legend">'
        '<span><span class="dot" style="background:#FF0000"></span>cord_center</span>'
        '<span><span class="dot" style="background:#1E90FF"></span>csf_*</span>'
        '<span><span class="dot" style="background:#00C000"></span>canal_wall_*</span>'
        '<span><span class="dot" style="background:#FFD700"></span>cord_centerline</span>'
        '</div>'
    )


def build_html(meta, summary_rows, flagged_blocks, centerline_b64,
               anatomy_b64=None):
    title = f"Cervical Spine Measurement Report — {meta.get('study_date','')}"
    parts = [
        '<!DOCTYPE html>',
        '<html lang="en"><head><meta charset="utf-8">',
        f'<title>{html.escape(title)}</title>',
        f'<style>{CSS}</style></head><body><div class="container">',
        f'<h1>{html.escape(title)}</h1>',
        f'<div class="muted">Generated {html.escape(meta["generated"])}</div>',

        '<h2>Section 1 — Study Header</h2>',
        '<div class="kv">',
    ]
    kv_items = [
        ('Study date',         meta.get('study_date', '')),
        ('Study description',  meta.get('study_desc', '')),
        ('Body part',          meta.get('body_part', '')),
        ('Institution',        meta.get('institution', '')),
        ('Manufacturer',       meta.get('manufacturer', '')),
        ('Model',              meta.get('model', '')),
        ('Field strength (T)', meta.get('field_strength', '')),
        ('Modality',           meta.get('modality', '')),
        ('Slices analyzed',    str(meta.get('n_slices', ''))),
        ('z range (mm)',       meta.get('z_range', '')),
        ('Algorithm version',  meta.get('algorithm_version', '')),
        ('Pipeline version',   meta.get('pipeline_version', '')),
        ('Detected body part', meta.get('detected_body_part', '')),
        ('Body part source',   meta.get('body_part_source', '')),
        ('Generated (k7)',     meta.get('generated_at', '')),
    ]
    for k, v in kv_items:
        parts.append(f'<div>{html.escape(str(k))}</div>'
                     f'<div>{html.escape(str(v))}</div>')
    parts.append('</div>')

    # ── Section 1b — What we are measuring ──────────────────────────
    parts.append('<h2>Section 1b — What We Are Measuring</h2>')
    parts.append('<div class="anatomy-block">')
    if anatomy_b64:
        parts.append(
            f'<img src="data:image/png;base64,{anatomy_b64}" '
            f'alt="anatomy diagram">'
        )
    parts.append(
        '<p>This is a cross-section view &mdash; like slicing a hot dog '
        'and looking at the end. The spinal cord (pink) carries signals '
        'between your brain and body. It sits inside a bony canal (tan) '
        'surrounded by protective fluid (blue). We measure the distance '
        'from the cord to the canal wall in four directions. When these '
        'distances get small, the cord may be getting compressed.</p>'
    )
    parts.append('</div>')

    parts.append('<h2>Section 2 — Per-Slice Measurement Summary</h2>')
    n_flagged = sum(1 for r in summary_rows if r['flags'])
    parts.append(
        f'<div class="muted">{len(summary_rows)} slices total · '
        f'{n_flagged} flagged (CSF &lt; {CSF_FLAG_MM}mm or '
        f'confidence &lt; {CONF_FLAG})</div>'
    )
    parts.append(render_summary_tables(summary_rows))

    parts.append('<h2>Section 3 — Flagged Slices</h2>')
    parts.append(render_legend())
    if not flagged_blocks:
        parts.append('<div class="muted">No flagged slices.</div>')
    else:
        for block in flagged_blocks:
            r = block['row']
            badges = ''.join(f'<span class="flag-badge">{html.escape(f)}</span>'
                             for f in r['flags'])
            parts.append('<div class="slice-block">')
            z_part = (f"z = {fmt(r['z_mm'], 2)} mm"
                      if r['z_mm'] is not None else '')
            parts.append(f'<h3>Slice instance {r["slice_inst"]} · {z_part} '
                         f'{badges}</h3>')

            axial_b64 = block.get('axial_b64')
            sag_b64   = block.get('sag_b64')

            parts.append('<div class="panels">')
            # Panels 1+2+3 are baked into a single side-by-side PNG;
            # we still want each labeled, so we present the composite
            # under a single span-3 grid cell, and the sagittal in cell 4.
            if axial_b64:
                parts.append(
                    '<div class="panel" style="grid-column: span 3;">'
                    f'<img src="data:image/png;base64,{axial_b64}" '
                    f'alt="axial panels slice {r["slice_inst"]}">'
                    '<div class="panel-caption">'
                    'Canal Geometry &middot; CSF Spaces &middot; Source'
                    '</div></div>'
                )
            else:
                parts.append(
                    '<div class="panel" style="grid-column: span 3;">'
                    '<div class="muted">[Axial image unavailable — DICOM '
                    'for this slice_inst not found in zip]</div></div>'
                )
            if sag_b64:
                parts.append(
                    '<div class="panel">'
                    f'<img src="data:image/png;base64,{sag_b64}" '
                    f'alt="sagittal ref slice {r["slice_inst"]}">'
                    '<div class="panel-caption">Sagittal Reference</div>'
                    '</div>'
                )
            else:
                parts.append(
                    '<div class="panel">'
                    '<div class="muted">[Sagittal reference unavailable]'
                    '</div></div>'
                )
            parts.append('</div>')

            parts.append(render_single_slice_table(r))
            parts.append('</div>')

    parts.append('<h2>Section 4 — Cord Centerline Plot</h2>')
    if centerline_b64:
        parts.append(f'<img class="centerline-img" '
                     f'src="data:image/png;base64,{centerline_b64}" '
                     f'alt="cord centerline">')
    else:
        parts.append('<div class="muted">No cord_centerline markers present.</div>')

    parts.append('</div></body></html>')
    return '\n'.join(parts)


# ──────────────────────────────────────────────────────────────────────
# Public callable entry point
# ──────────────────────────────────────────────────────────────────────
def generate_report(k7_response: dict, zip_path: str, out_path: str) -> dict:
    """Generate the HTML report from a k7 API response dict and a DICOM zip.

    Args:
        k7_response: parsed JSON dict from the k7 /preimpression endpoint
                     (the same shape as result['preimpression'] inside
                     process_dicom_bytes, or the top-level response from
                     the standalone /preimpression endpoint).
        zip_path:    path to the DICOM zip on disk (the original study).
        out_path:    where to write the HTML report.

    Returns:
        dict with summary counts:
          {'n_slices': int, 'n_flagged': int, 'n_centerline': int,
           'out_path': str}

    Raises:
        FileNotFoundError if zip_path doesn't exist.
        zipfile.BadZipFile if zip_path isn't a valid zip.
        OSError for write failures on out_path.
    """
    resp = k7_response or {}
    markers = resp.get('markers', [])
    by_slice, centerline = group_markers_by_slice(markers)

    rows = []
    for si in sorted(by_slice.keys()):
        rows.append(per_slice_row(si, by_slice[si]))

    work_dir = tempfile.mkdtemp(prefix='gen_report_')
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(work_dir)

        by_series_uid = scan_dicom_dir(work_dir)
        preferred_uid = None
        su = resp.get('series_used', {}).get('axial_t2', {})
        if isinstance(su, dict):
            preferred_uid = su.get('series_uid')
        axial_items = pick_axial_series(by_series_uid,
                                        preferred_uid=preferred_uid)
        inst_to_path = {}
        for path, ds in axial_items:
            inst = getattr(ds, 'InstanceNumber', None)
            if inst is not None:
                inst_to_path[int(inst)] = path

        # Sagittal reference: pick the middle slice of the sagittal T2 series
        sag_preferred_uid = None
        sag_su = resp.get('series_used', {}).get('sagittal_t2', {})
        if isinstance(sag_su, dict):
            sag_preferred_uid = sag_su.get('series_uid')
        sag_items = pick_sagittal_series(by_series_uid,
                                          preferred_uid=sag_preferred_uid)
        sag_mid_ds, sag_mid_arr = None, None
        if sag_items:
            mid_idx = len(sag_items) // 2
            sag_path = sag_items[mid_idx][0]
            try:
                sag_mid_ds, sag_mid_arr = read_pixels(sag_path)
            except Exception:
                sag_mid_ds, sag_mid_arr = None, None

        # Header metadata
        study   = resp.get('study', {})
        scanner = resp.get('scanner', {})
        sd = study.get('date', '')
        if sd and len(sd) == 8 and sd.isdigit():
            sd = f'{sd[:4]}-{sd[4:6]}-{sd[6:8]}'
        zs = [r['z_mm'] for r in rows if r['z_mm'] is not None]
        z_range = (f'{min(zs):.2f} to {max(zs):.2f}'
                   if zs else '—')
        meta = {
            'study_date':         sd,
            'study_desc':         study.get('description', ''),
            'body_part':          study.get('body_part', ''),
            'modality':           study.get('modality', ''),
            'institution':        scanner.get('institution', ''),
            'manufacturer':       scanner.get('manufacturer', ''),
            'model':              scanner.get('model', ''),
            'field_strength':     scanner.get('field_strength', ''),
            'n_slices':           resp.get('n_axial_slices', len(rows)),
            'z_range':            z_range,
            'algorithm_version':  resp.get('algorithm_version', ''),
            'pipeline_version':   resp.get('pipeline_version', ''),
            'detected_body_part': resp.get('detected_body_part', ''),
            'body_part_source':   resp.get('body_part_source', ''),
            'generated_at':       resp.get('generated_at', ''),
            'generated':          datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        # Flagged slice image blocks: render 3 axial panels + 1 sagittal panel
        flagged_blocks = []
        for r in rows:
            if not r['flags']:
                continue
            si = r['slice_inst']
            path = inst_to_path.get(si)
            axial_b64 = None
            sag_b64   = None
            if path is not None:
                try:
                    ds, arr = read_pixels(path)
                    axial_b64 = render_axial_panels(arr, ds, by_slice[si], r)
                except Exception:
                    axial_b64 = None
            if sag_mid_ds is not None and sag_mid_arr is not None:
                try:
                    sag_b64 = render_sagittal_panel(
                        sag_mid_ds, sag_mid_arr,
                        z_mm=r.get('z_mm'), slice_inst=si,
                    )
                except Exception:
                    sag_b64 = None
            flagged_blocks.append({
                'row':       r,
                'axial_b64': axial_b64,
                'sag_b64':   sag_b64,
            })

        centerline_b64 = render_centerline_plot(centerline)
        anatomy_b64 = render_anatomy_diagram()

        html_doc = build_html(meta, rows, flagged_blocks, centerline_b64,
                              anatomy_b64=anatomy_b64)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html_doc)

        return {
            'n_slices':     len(rows),
            'n_flagged':    len(flagged_blocks),
            'n_centerline': len(centerline),
            'out_path':     out_path,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────
# CLI wrapper
# ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) != 4:
        print('Usage: generate_report.py <k7_json> <dicom_zip> <output_html>',
              file=sys.stderr)
        sys.exit(2)

    json_path = sys.argv[1]
    zip_path  = sys.argv[2]
    out_path  = sys.argv[3]

    try:
        with open(json_path) as f:
            resp = json.load(f)
    except FileNotFoundError:
        print(f'ERROR: {json_path} not found', file=sys.stderr)
        sys.exit(3)
    except json.JSONDecodeError as e:
        print(f'ERROR: {json_path} is not valid JSON: {e}', file=sys.stderr)
        sys.exit(3)

    try:
        info = generate_report(resp, zip_path, out_path)
    except FileNotFoundError:
        print(f'ERROR: {zip_path} not found', file=sys.stderr)
        sys.exit(3)
    except zipfile.BadZipFile:
        print(f'ERROR: {zip_path} is not a valid zip file', file=sys.stderr)
        sys.exit(3)

    print(f'Wrote {info["out_path"]}')
    print(f'  slices summarized: {info["n_slices"]}')
    print(f'  flagged slices:    {info["n_flagged"]}')
    print(f'  centerline points: {info["n_centerline"]}')


if __name__ == '__main__':
    main()
