"""
data_extract.py — Manifest → SliceFacts rows
=============================================
Bridges the lzw manifest (file/header/capability facts) to the SliceFacts
row shape that measure_spine.py expects.

Two entry points:

  run(manifest, out_csv=None) -> list[dict]
      Given an lzw.Manifest, return one row per imaging file with the column
      set measure_spine.is_axial_t2() and measure_spine.run() consume.
      If out_csv is given, also write the rows as a CSV at that path.

  run_from_series(series_list) -> list[dict]
      Given a series_list as produced by analyzers._base.group_series(), build
      the same SliceFacts row shape directly (no manifest needed). Used by the
      analyzer wrapper at /preimpression dispatch time, when the pipeline has
      already grouped series in-memory.

SliceFacts column contract (consumed by measure_spine.py):
  - instance_number
  - z_position_mm
  - series_description
  - extracted_path
  - pixel_spacing_row_mm
  - pixel_spacing_col_mm
  - orient_axial_align      ( |cross(row_dir, col_dir)·z_hat| ; 1.0 = pure axial )
  - status                  ( 'OK' or a one-word failure code )

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Iterable

import numpy as np
import pydicom


# ─── SliceFacts column contract ─────────────────────────────────────────────
SLICE_FACTS_COLUMNS = [
    'instance_number',
    'z_position_mm',
    'series_description',
    'extracted_path',
    'pixel_spacing_row_mm',
    'pixel_spacing_col_mm',
    'orient_axial_align',
    'status',
]


def _axial_align_from_iop(iop: list[float]) -> float:
    """Return |cross(row_dir, col_dir) · ẑ|.

    1.0  = perfectly axial slice (normal points along z)
    0.0  = sagittal or coronal
    measure_spine.AXIAL_ALIGN_MIN (0.85) is the threshold.
    """
    if iop is None or len(iop) < 6:
        return 0.0
    try:
        row = np.array([float(iop[0]), float(iop[1]), float(iop[2])])
        col = np.array([float(iop[3]), float(iop[4]), float(iop[5])])
        normal = np.cross(row, col)
        return float(abs(normal[2]))
    except (TypeError, ValueError):
        return 0.0


def _row_from_header_and_path(
    extracted_path: str,
    headers,
    series_desc_fallback: Optional[str] = None,
) -> dict:
    """Build one SliceFacts row from a HeaderFacts-like object and a path.

    `headers` may be:
      - an lzw.HeaderFacts dataclass (has attribute access), or
      - a pydicom Dataset (has attribute access with DICOM tag names), or
      - None / failed-read

    For Dataset inputs we expect the tag names DICOM uses (ImagePositionPatient
    etc.). For HeaderFacts we expect the snake_case attribute names defined in
    lzw.py.
    """
    row = {col: '' for col in SLICE_FACTS_COLUMNS}

    if headers is None:
        row['status'] = 'NO_HEADER'
        row['extracted_path'] = extracted_path
        return row

    # Try lzw.HeaderFacts attribute names first
    inst = getattr(headers, 'instance_number', None)
    ipp = getattr(headers, 'image_position_patient', None)
    iop = getattr(headers, 'image_orientation_patient', None)
    pixel_spacing = getattr(headers, 'pixel_spacing', None)
    series_desc = getattr(headers, 'series_description', None)

    # Fall back to pydicom Dataset tag names
    if inst is None:
        inst = getattr(headers, 'InstanceNumber', None)
    if ipp is None:
        ipp = getattr(headers, 'ImagePositionPatient', None)
    if iop is None:
        iop = getattr(headers, 'ImageOrientationPatient', None)
    if pixel_spacing is None:
        pixel_spacing = getattr(headers, 'PixelSpacing', None)
    if series_desc is None:
        series_desc = getattr(headers, 'SeriesDescription', None)

    if series_desc is None and series_desc_fallback is not None:
        series_desc = series_desc_fallback

    row['extracted_path'] = extracted_path
    row['series_description'] = str(series_desc) if series_desc is not None else ''

    if inst is not None:
        try:
            row['instance_number'] = int(inst)
        except (TypeError, ValueError):
            row['instance_number'] = ''

    # z_position_mm from ImagePositionPatient[2]
    if ipp is not None:
        try:
            row['z_position_mm'] = float(ipp[2])
        except (TypeError, ValueError, IndexError):
            row['z_position_mm'] = ''

    # pixel_spacing is [row_mm, col_mm] in DICOM ordering
    if pixel_spacing is not None:
        try:
            row['pixel_spacing_row_mm'] = float(pixel_spacing[0])
            row['pixel_spacing_col_mm'] = float(pixel_spacing[1])
        except (TypeError, ValueError, IndexError):
            row['pixel_spacing_row_mm'] = ''
            row['pixel_spacing_col_mm'] = ''

    if iop is not None:
        row['orient_axial_align'] = round(_axial_align_from_iop(iop), 6)
    else:
        row['orient_axial_align'] = 0.0

    # Mark status OK only if we got the fields measure_spine actually needs
    needed = ('z_position_mm', 'pixel_spacing_row_mm', 'pixel_spacing_col_mm',
              'orient_axial_align')
    if all(row[k] != '' for k in needed):
        row['status'] = 'OK'
    else:
        row['status'] = 'INCOMPLETE_HEADER'

    return row


def _write_slice_facts_csv(rows: list[dict], path: Path) -> None:
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=SLICE_FACTS_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, '') for c in SLICE_FACTS_COLUMNS})


# ─── Public API ─────────────────────────────────────────────────────────────

def run(manifest, out_csv: Optional[str] = None) -> list[dict]:
    """Convert an lzw.Manifest into SliceFacts rows.

    Iterates manifest.files; each FileEntry's ExtractionFacts.extracted_path
    and HeaderFacts populate one row. Non-imaging modalities (PR/SR/OT/KO) are
    skipped — measure_spine only consumes axial T2, so non-MR/CT entries are
    noise.

    Optionally writes the rows as a CSV to out_csv.
    """
    rows: list[dict] = []
    if manifest is None or not getattr(manifest, 'files', None):
        if out_csv:
            _write_slice_facts_csv([], Path(out_csv))
        return rows

    for fe in manifest.files:
        extraction = getattr(fe, 'extraction', None)
        headers = getattr(fe, 'headers', None)
        if extraction is None:
            continue
        extracted_path = getattr(extraction, 'extracted_path', None)
        if not extracted_path:
            continue
        # Skip non-imaging modalities; measure_spine wants T2 axial MR
        if headers is not None:
            if not getattr(headers, 'is_imaging_modality', True):
                continue
        rows.append(_row_from_header_and_path(extracted_path, headers))

    if out_csv:
        _write_slice_facts_csv(rows, Path(out_csv))
    return rows


def run_from_series(series_list: Iterable[dict],
                     work_dir: Optional[str] = None,
                     out_csv: Optional[str] = None) -> list[dict]:
    """Convert a series_list (from group_series) into SliceFacts rows.

    series_list entries shape (per analyzers._base.group_series):
        {'series_uid': str, 'series_description': str, 'orientation': str,
         'modality': str, 'n_slices': int, 'files': [path, ...],
         'sample_ds': pydicom.Dataset}

    The sample_ds is one representative header for the series. For per-file
    z_position and instance_number we re-read each file's header (cheap with
    stop_before_pixels=True).

    work_dir is accepted for symmetry with the lzw path; unused here because
    series_list entries already carry absolute file paths.
    """
    rows: list[dict] = []
    if not series_list:
        if out_csv:
            _write_slice_facts_csv([], Path(out_csv))
        return rows

    for s in series_list:
        series_desc = s.get('series_description', '')
        modality = str(s.get('modality', '')).upper()
        # Skip non-imaging modalities cheaply
        if modality and modality not in (
            'MR', 'CT', 'CR', 'DX', 'PT', 'NM', 'XA', 'MG', 'US', 'RF'
        ):
            continue

        for fpath in s.get('files', []):
            try:
                ds = pydicom.dcmread(fpath, stop_before_pixels=True, force=True)
            except Exception:
                row = {col: '' for col in SLICE_FACTS_COLUMNS}
                row['extracted_path'] = fpath
                row['series_description'] = series_desc
                row['status'] = 'HEADER_READ_FAILED'
                rows.append(row)
                continue
            rows.append(_row_from_header_and_path(
                extracted_path=fpath,
                headers=ds,
                series_desc_fallback=series_desc,
            ))

    if out_csv:
        _write_slice_facts_csv(rows, Path(out_csv))
    return rows


# ─── CLI ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Convert an lzw manifest into a SliceFacts CSV.'
    )
    ap.add_argument('manifest_json',
                    help='manifest.json produced by lzw.walk() (or a directory '
                         'containing it)')
    ap.add_argument('-o', '--out', default='slice_facts.csv',
                    help='output CSV path (default: slice_facts.csv)')
    args = ap.parse_args()

    # Minimal CLI loader — most callers go through the Python API
    import json
    p = Path(args.manifest_json)
    if p.is_dir():
        p = p / 'manifest.json'
    with open(p) as f:
        data = json.load(f)

    # Reconstruct minimal manifest-like object for run()
    class _M:
        def __init__(self, files):
            self.files = files
    class _FE:
        def __init__(self, extraction, headers):
            self.extraction = extraction
            self.headers = headers
    class _Obj:
        def __init__(self, d):
            for k, v in d.items():
                setattr(self, k, v)

    files = []
    for fe in data.get('files', []):
        ex = _Obj(fe.get('extraction', {}))
        hdr_d = fe.get('headers')
        hdr = _Obj(hdr_d) if hdr_d else None
        files.append(_FE(ex, hdr))

    rows = run(_M(files), out_csv=args.out)
    print(f'Wrote {len(rows)} rows -> {args.out}')
