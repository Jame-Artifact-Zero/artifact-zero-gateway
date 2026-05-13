"""
analyzers/_base.py
==================
Shared analyzer interface and common utilities.

Every body-part analyzer implements:
    class XAnalyzer(BaseAnalyzer):
        body_part_codes = ['CSPINE', 'C-SPINE', ...]
        body_part_label = 'cervical_spine'

        def analyze(self, ax_items=None, sag_items=None, cor_items=None,
                    series_list=None, work_dir=None) -> dict:
            return {'status': ..., 'levels_detected': {}, 'markers': [...],
                    'level_summaries': [...], 'slice_measurements': [...],
                    'impression': {'flags': [...], 'counts': {...}},
                    'cord_track_3d': {...}, 'analyzer_specific': {...}}

The shared shell handles:
  - DICOM unpack, scan, series grouping
  - Body-part dispatch
  - Severity aggregation
  - Output rendering (text/markdown/json/visualization)

Author: Jame Houghton / Artifact Zero, May 2026
"""
from __future__ import annotations
import numpy as np
import pydicom
from pathlib import Path
from collections import defaultdict


SEVERITY_RANK = {'NORMAL': 0, 'FINDING': 1, 'MODERATE': 2, 'CRITICAL': 3}


def max_severity(flags):
    if not flags:
        return 'NORMAL'
    rank = max(SEVERITY_RANK.get(f.get('severity', 'NORMAL'), 0) for f in flags)
    for k, v in SEVERITY_RANK.items():
        if v == rank:
            return k
    return 'NORMAL'


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


def is_flair(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    return 'FLAIR' in desc


def is_t1(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    return 'T1' in desc and 'T2' not in desc


def is_dwi(ds):
    desc = str(getattr(ds, 'SeriesDescription', '')).upper()
    return 'DWI' in desc or 'DIFF' in desc


def slice_z_center(it):
    H, W = it['img'].shape
    ctr = it['ipp'] + (W/2)*it['ps'][1]*it['iop'][0:3] + (H/2)*it['ps'][0]*it['iop'][3:6]
    return float(ctr[2])


def _read_pixel_array(ds):
    """
    Read pixel array from a pydicom dataset.
    Handles JPEG Lossless and other compressed transfer syntaxes that
    require gdcm or pylibjpeg by falling back to raw pixel data.
    This matches the approach used in az_dicom_processor.py.
    """
    try:
        return ds.pixel_array.astype(np.float64)
    except Exception:
        pass
    # Fallback: force uncompressed read by overriding transfer syntax
    try:
        import copy
        ds2 = copy.copy(ds)
        ds2.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
        return ds2.pixel_array.astype(np.float64)
    except Exception:
        pass
    # Last resort: read raw pixel bytes
    try:
        raw = np.frombuffer(ds.PixelData, dtype=np.uint16)
        rows = int(getattr(ds, 'Rows', 512))
        cols = int(getattr(ds, 'Columns', 512))
        return raw[:rows * cols].reshape(rows, cols).astype(np.float64)
    except Exception:
        rows = int(getattr(ds, 'Rows', 512))
        cols = int(getattr(ds, 'Columns', 512))
        return np.zeros((rows, cols), dtype=np.float64)


def load_slice(filepath):
    ds = pydicom.dcmread(filepath)
    return {
        'filepath': filepath,
        'inst': int(getattr(ds, 'InstanceNumber', 0)),
        'img': _read_pixel_array(ds),
        'ipp': np.array(getattr(ds, 'ImagePositionPatient', [0, 0, 0]), dtype=float),
        'iop': np.array(getattr(ds, 'ImageOrientationPatient', [1, 0, 0, 0, 1, 0]), dtype=float),
        'ps': np.array(getattr(ds, 'PixelSpacing', [1.0, 1.0]), dtype=float),
        'sl': float(getattr(ds, 'SliceLocation', 0)),
    }


def load_volume(files):
    items = [load_slice(f) for f in files]
    items.sort(key=slice_z_center)
    return items


def _is_likely_dicom(filepath):
    """Quick magic-byte check: real DICOM files have 'DICM' at byte offset 128.
    Returns False fast for executables, PDFs, archives, etc. that happen to be
    inside a CD-distribution zip alongside the actual DICOM files."""
    try:
        with open(filepath, 'rb') as fh:
            preamble = fh.read(132)
        if len(preamble) < 132:
            return False
        # Standard DICOM file: 128-byte preamble then "DICM"
        if preamble[128:132] == b'DICM':
            return True
        # Some CD-burned DICOM lacks the 128-byte preamble. Fall back to
        # checking common DICOM data-element opening tags. A real DICOM
        # always starts with a known group/element pair.
        # Group 0x0002 (file meta) or 0x0008 (identifying) is what we'd see
        # in the first 4 bytes of an implicit-VR DICOM with no preamble.
        first4 = preamble[0:4]
        if first4[:2] in (b'\x02\x00', b'\x08\x00'):
            return True
        return False
    except Exception:
        return False


# File extensions that are definitely not DICOM. Skip them without opening.
_NON_DICOM_EXTS = {
    # Image / text / data already in original list
    '.PNG', '.JPG', '.JPEG', '.JSON', '.TXT', '.HTML', '.MD', '.CSV',
    # CD-distribution junk that's typically in hospital-burned DICOM zips
    '.EXE', '.DLL', '.SO', '.DYLIB', '.APP',
    '.PDF', '.DOC', '.DOCX', '.XLS', '.XLSX', '.PPT', '.PPTX',
    '.INI', '.INF', '.LNK', '.URL', '.LOG',
    '.ZIP', '.RAR', '.7Z', '.TAR', '.GZ',
    '.BMP', '.GIF', '.TIFF', '.TIF', '.SVG', '.WEBP',
    '.MP4', '.MOV', '.AVI', '.MP3', '.WAV',
    '.XML', '.YAML', '.YML',
    '.DS_STORE',
}

# Filename patterns that are definitely not DICOM (no extension or unusual).
_NON_DICOM_NAMES = {
    'DICOMDIR',  # DICOM directory index file -- not an image, do not load
    'AUTORUN',
    'README',
    'LICENSE',
    'INDEX',
    'LOCKFILE',
}


def group_series(root):
    """Walk root finding all DICOM image files, group by SeriesInstanceUID.

    Memory-safe scan:
      1. Skip non-DICOM file extensions (CD-distribution viewer binaries,
         PDFs, autorun.inf, README.txt, etc.) without opening them.
      2. Skip known non-image filenames like DICOMDIR (directory index, not
         an image).
      3. Magic-byte pre-check before pydicom.dcmread() -- avoids force=True
         memory leak on binary files that happen to have a non-skippable
         extension or no extension at all.
      4. Use stop_before_pixels=True so the header scan does NOT load the
         pixel array of each file. The pixel array is only loaded later in
         load_volume() for the chosen series. This is the dominant memory
         savings on multi-series CD-distribution zips.
    """
    series = defaultdict(lambda: {'files': [], 'meta': None})
    # Non-image DICOM modalities to skip entirely (structured reports,
    # presentation states, key object selections, etc.)
    SKIP_MODALITIES = {'SR', 'PR', 'KO', 'DOC', 'OT'}

    # Sort the file list before iterating. Path.rglob returns entries in
    # filesystem (inode) order, which is non-deterministic across runs and
    # across machines. Sorting here ensures the "first-arrival" file we
    # pick for series[uid]['meta'] is reproducibly the alphabetically-first
    # file in the series — same `sample_ds` chosen every run, every host.
    # This eliminates the 0.3-1mm marker xyz drift observed on pelvis
    # cspine_v6 runs where which slice's IPP/IOP gets stored as the
    # series representative depended on filesystem layout.
    for f in sorted(Path(root).rglob('*')):
        if not f.is_file():
            continue

        # Cheap filters first: extension and filename
        suffix_upper = f.suffix.upper()
        if suffix_upper in _NON_DICOM_EXTS:
            continue
        if f.stem.upper() in _NON_DICOM_NAMES:
            continue

        # Magic-byte pre-check before opening with pydicom. This is the
        # critical fix: pydicom.dcmread(force=True) on a non-DICOM file
        # like a viewer executable would parse arbitrary bytes as DICOM
        # tags and could allocate hundreds of MB before failing.
        if not _is_likely_dicom(str(f)):
            continue

        try:
            # stop_before_pixels=True: only read header tags, NOT the pixel
            # array. Pixel arrays are large (hundreds of KB to several MB
            # per slice). For a 47-slice study with multiple series, loading
            # all pixels just to check SeriesInstanceUID would consume
            # hundreds of MB. We only need pixels later, in load_volume(),
            # for the one series the analyzer actually uses.
            ds = pydicom.dcmread(str(f), stop_before_pixels=True, force=True)
        except Exception:
            continue
        if not hasattr(ds, 'SeriesInstanceUID'):
            continue
        modality = str(getattr(ds, 'Modality', '')).upper()
        if modality in SKIP_MODALITIES:
            continue
        uid = ds.SeriesInstanceUID
        series[uid]['files'].append(str(f))
        if series[uid]['meta'] is None:
            series[uid]['meta'] = ds

    out = []
    for uid, s in series.items():
        ds = s['meta']
        out.append({
            'series_uid': uid,
            'series_description': str(getattr(ds, 'SeriesDescription', '')).strip(),
            'modality': str(getattr(ds, 'Modality', '')),
            'orientation': classify_orientation(getattr(ds, 'ImageOrientationPatient', None)),
            'n_slices': len(s['files']),
            'files': sorted(s['files']),
            'sample_ds': ds,
        })
    return out


def detect_body_part(series_list):
    """Best guess at body part from DICOM headers.

    Delegates to the existing detect_body_part_from_dicom() in
    dicom_processor_api.py so body-part detection is consistent across
    /dicom/analyze and /preimpression.

    Falls back to a local heuristic when running standalone (CLI, tests).
    """
    if not series_list:
        return 'UNKNOWN'

    try:
        from dicom_processor_api import detect_body_part_from_dicom
        counts = defaultdict(int)
        sample_dss = []
        for s in series_list:
            ds = s.get('sample_ds')
            if ds is not None:
                sample_dss.append(ds)
                bp = str(getattr(ds, 'BodyPartExamined', '')).upper()
                if bp:
                    counts[bp] += 1
        if sample_dss:
            chosen_ds = sample_dss[0]
            if counts:
                primary_bp = max(counts.items(), key=lambda kv: kv[1])[0]
                for ds in sample_dss:
                    if str(getattr(ds, 'BodyPartExamined', '')).upper() == primary_bp:
                        chosen_ds = ds
                        break
            return detect_body_part_from_dicom(chosen_ds) or 'UNKNOWN'
    except ImportError:
        pass

    return _local_detect_body_part(series_list)


def _local_detect_body_part(series_list):
    """Standalone fallback when dicom_processor_api is not available."""
    counts = defaultdict(int)
    for s in series_list:
        ds = s['sample_ds']
        bp = str(getattr(ds, 'BodyPartExamined', '')).upper()
        if bp:
            counts[bp] += 1
    if counts:
        return max(counts.items(), key=lambda kv: kv[1])[0]
    for s in series_list:
        sd = str(getattr(s['sample_ds'], 'StudyDescription', '')).upper()
        for kw, code in [
            ('CERVIC', 'CSPINE'), ('C-SPINE', 'CSPINE'), ('C SPINE', 'CSPINE'),
            ('THORAC', 'TSPINE'), ('T-SPINE', 'TSPINE'), ('T SPINE', 'TSPINE'),
            ('LUMBAR', 'LSPINE'), ('L-SPINE', 'LSPINE'), ('L SPINE', 'LSPINE'),
            ('BRAIN', 'BRAIN'), ('HEAD', 'BRAIN'),
            ('KNEE', 'KNEE'), ('ANKLE', 'ANKLE'), ('FOOT', 'FOOT'),
            ('SHOULDER', 'SHOULDER'), ('ELBOW', 'ELBOW'),
            ('WRIST', 'WRIST'), ('HAND', 'HAND'), ('FINGER', 'HAND'),
            ('BREAST', 'BREAST'),
        ]:
            if kw in sd:
                return code
    return 'UNKNOWN'


class BaseAnalyzer:
    """Subclasses set body_part_codes and body_part_label, override analyze()."""
    body_part_codes: tuple = ()
    body_part_label: str = 'unknown'

    def analyze(self, series_list, work_dir=None):
        raise NotImplementedError


# ----------------------------------------------------------------------------
# Common geometry helpers used by spine analyzers
# ----------------------------------------------------------------------------
def patient_xy_from_pix(item, row, col):
    """Convert axial pixel (row, col) to patient (x, y, z)."""
    ipp, iop, ps = item['ipp'], item['iop'], item['ps']
    return ipp + col*ps[1]*iop[0:3] + row*ps[0]*iop[3:6]


def pix_from_patient_xy(item, x, y):
    """Convert patient (x, y) to axial pixel (row, col), assuming the slice's
    plane contains (x, y)."""
    ipp, iop, ps = item['ipp'], item['iop'], item['ps']
    A = np.array([[ps[1]*iop[0], ps[0]*iop[3]],
                  [ps[1]*iop[1], ps[0]*iop[4]]])
    b = np.array([x - ipp[0], y - ipp[1]])
    col, row = np.linalg.solve(A, b)
    return float(row), float(col)
