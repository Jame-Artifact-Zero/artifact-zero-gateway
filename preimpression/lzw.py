"""
lzw.py — Lazy Walker
====================
The customer's laziness is our problem, not theirs.

Input: anything. A zip. A nested zip inside a zip. A folder. A single .dcm file.
       Files with extensions, files without. CD burns with viewer payload.
       Hospital exports with PR/SR/OT mixed into the imaging.
       Compressed and uncompressed transfer syntaxes.

Output: an extracted work_dir of clean DICOM imaging files, plus a layered
        manifest that records every fact we observed.

The manifest is the contract. It has three layers per file:

  Layer 1 — extraction facts:   what we observed about the file in the zip
  Layer 2 — header facts:       what the DICOM headers said
  Layer 3 — capability facts:   what would be needed to actually use the pixels

Plus three top-level views:

  rejections       — files we filtered out, with reasons (audit trail)
  series_index     — Layer 2 grouped by SeriesInstanceUID
  study_summary    — one-screen view: who/what/when/where + codec requirements

Downstream stages take only the layers they need.

We do not predict future uses. We record observed facts.

Author: Jame Houghton / Artifact Zero, May 2026
Schema version: 1.0
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Iterator

import pydicom

log = logging.getLogger(__name__)

MANIFEST_VERSION = "1.0"

# ─── Imaging modalities (Layer 2 partitions on this) ────────────────────────
# These are the modalities the pipeline downstream actually wants to analyze.
# Files with other modalities (PR, SR, OT, KO, DOC) still get extracted and
# manifested — but they are flagged as non_imaging so downstream code can
# filter cleanly.
IMAGING_MODALITIES = frozenset({
    'MR', 'CT', 'CR', 'DX', 'PT', 'NM', 'XA', 'MG', 'US', 'RF',
})

# Non-imaging DICOM modalities. Kept in the manifest, not loaded as images.
NON_IMAGING_MODALITIES = frozenset({
    'PR',   # Presentation State (annotations on top of images)
    'SR',   # Structured Report (radiologist text)
    'KO',   # Key Object Selection
    'DOC',  # Encapsulated document
    'OT',   # Other / Secondary Capture (often screenshots)
    'SEG',  # Segmentation
    'REG',  # Registration
    'RTSTRUCT', 'RTPLAN', 'RTDOSE',  # Radiation therapy
})

# ─── Recursion limits ───────────────────────────────────────────────────────
# Observed deepest nesting in real-world zips: outer.zip -> DICOM.zip ->
# DICOM/PA/ST/SE/IMG = 5 levels. Cap at 4 levels of zip-in-zip recursion;
# anything deeper is suspect (zip bomb).
MAX_NESTED_ZIP_DEPTH = 4

# ─── Bundle / viewer-payload filters ────────────────────────────────────────
# Rejected at zip-entry-list time, before extraction. Critical for memory
# safety on 1GB ECS tasks: jh_brain_and_stem_wwo.zip is 66% viewer payload
# (2043/3099 entries). Filter at unpack, never write to disk.

# Path components that mark macOS/packaged-app payloads. Match if any
# directory component of the entry's path equals or ends with one.
_BUNDLE_PATH_SUFFIXES = (
    '.app', '.framework', '.bundle', '.kext', '.lproj', '.xcassets',
    '.appex', '.plugin', '.dSYM', '__MACOSX',
)

# Extensions definitely not DICOM. Mirrors and extends the new-pipeline list
# with formats common inside .app bundles (fonts, source code, web resources)
# AND PACS-export sidecar files we observed (Synapse: .fix .blo .idx).
_NON_DICOM_EXTS = frozenset({
    # CD-distribution viewer payloads
    '.exe', '.dll', '.so', '.dylib', '.app',
    # Documents
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.rtf',
    # Config / shortcuts / logs
    '.ini', '.inf', '.lnk', '.url', '.log', '.cfg', '.conf', '.plist',
    # Archives nested inside the outer zip — note: we DON'T put .zip here.
    # Nested .zip is handled separately by the recursive extractor.
    '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2',
    # Images (raster / vector)
    '.png', '.jpg', '.jpeg', '.bmp', '.gif', '.tiff', '.tif',
    '.svg', '.webp', '.ico', '.icns',
    # Fonts (very common in macOS app bundles)
    '.ttf', '.otf', '.eot', '.woff', '.woff2', '.fnt',
    # Web / markup / data
    '.html', '.htm', '.css', '.js', '.map',
    '.json', '.xml', '.yaml', '.yml', '.md', '.txt', '.csv', '.tsv',
    # Source code / headers
    '.h', '.hpp', '.c', '.cpp', '.cc', '.m', '.mm', '.swift', '.py', '.rb',
    # Media
    '.mp4', '.mov', '.avi', '.mkv', '.mp3', '.wav', '.ogg', '.flac',
    # PACS-export sidecar (Synapse)
    '.fix', '.blo', '.idx',
    # OS metadata
    '.ds_store',
})

# Filenames (stem, case-insensitive) that are not DICOM regardless of ext.
_NON_DICOM_NAMES = frozenset({
    'DICOMDIR',  # DICOM directory index, not an image
    'AUTORUN', 'README', 'LICENSE', 'INDEX', 'LOCKFILE',
    'INFO', 'MANIFEST', 'PKGINFO', 'CODERESOURCES',
})

# ─── Codec map (Layer 3) ────────────────────────────────────────────────────
# Maps TransferSyntaxUID → list of Python packages that can decode pixels.
# Downstream production-env check answers: do we have at least one of these?
TRANSFER_SYNTAX_DECODERS: dict[str, list[str]] = {
    # Uncompressed — pydicom alone is sufficient
    '1.2.840.10008.1.2':       ['pydicom'],          # Implicit VR LE
    '1.2.840.10008.1.2.1':     ['pydicom'],          # Explicit VR LE
    '1.2.840.10008.1.2.2':     ['pydicom'],          # Explicit VR BE
    # JPEG family
    '1.2.840.10008.1.2.4.50':  ['pylibjpeg-libjpeg', 'gdcm'],   # JPEG Baseline
    '1.2.840.10008.1.2.4.51':  ['pylibjpeg-libjpeg', 'gdcm'],   # JPEG Extended
    '1.2.840.10008.1.2.4.57':  ['pylibjpeg-libjpeg', 'gdcm'],   # JPEG Lossless P14
    '1.2.840.10008.1.2.4.70':  ['pylibjpeg-libjpeg', 'gdcm'],   # JPEG Lossless P14 SV1
    '1.2.840.10008.1.2.4.80':  ['pylibjpeg-libjpeg', 'gdcm'],   # JPEG-LS Lossless
    '1.2.840.10008.1.2.4.81':  ['pylibjpeg-libjpeg', 'gdcm'],   # JPEG-LS Near-Lossless
    '1.2.840.10008.1.2.4.90':  ['pylibjpeg-openjpeg', 'gdcm'],  # JPEG 2000 Lossless
    '1.2.840.10008.1.2.4.91':  ['pylibjpeg-openjpeg', 'gdcm'],  # JPEG 2000
    # RLE
    '1.2.840.10008.1.2.5':     ['pylibjpeg-rle', 'gdcm'],       # RLE Lossless
}


# ════════════════════════════════════════════════════════════════════════════
# DATA CLASSES — manifest schema, version 1.0
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class ExtractionFacts:
    """Layer 1 — what we observed about the file during extraction."""
    extracted_path: str            # absolute path on disk after extraction
    source_archive: str            # which archive it came from (for nested)
    source_path_in_archive: str    # path inside the archive
    archive_depth: int             # 0 = outer zip, 1 = first nested, ...
    file_size_bytes: int
    has_dicm_magic: bool           # bytes 128:132 == b'DICM'


@dataclass
class HeaderFacts:
    """Layer 2 — what the DICOM headers said. None if header read failed."""
    sop_class_uid: Optional[str] = None
    sop_class_name: Optional[str] = None
    sop_instance_uid: Optional[str] = None
    modality: Optional[str] = None
    is_imaging_modality: bool = False
    study_instance_uid: Optional[str] = None
    series_instance_uid: Optional[str] = None
    series_description: Optional[str] = None
    series_number: Optional[int] = None
    instance_number: Optional[int] = None
    study_description: Optional[str] = None
    study_date: Optional[str] = None
    study_time: Optional[str] = None
    accession_number: Optional[str] = None
    body_part_examined: Optional[str] = None
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    patient_age: Optional[str] = None
    patient_sex: Optional[str] = None
    manufacturer: Optional[str] = None
    manufacturer_model_name: Optional[str] = None
    institution_name: Optional[str] = None
    magnetic_field_strength: Optional[float] = None
    rows: Optional[int] = None
    columns: Optional[int] = None
    image_position_patient: Optional[list[float]] = None
    image_orientation_patient: Optional[list[float]] = None
    pixel_spacing: Optional[list[float]] = None
    slice_thickness: Optional[float] = None
    echo_time: Optional[float] = None
    repetition_time: Optional[float] = None


@dataclass
class CapabilityFacts:
    """Layer 3 — what's needed to actually use the pixels."""
    transfer_syntax_uid: Optional[str] = None
    transfer_syntax_name: Optional[str] = None
    is_compressed: Optional[bool] = None
    pixel_decode_codec_required: Optional[str] = None  # canonical short name
    decoder_packages_acceptable: list[str] = field(default_factory=list)
    has_pixel_data: bool = False                       # PixelData tag present


@dataclass
class FileEntry:
    extraction: ExtractionFacts
    headers: Optional[HeaderFacts] = None
    capabilities: Optional[CapabilityFacts] = None


@dataclass
class Rejection:
    source_archive: str
    source_path_in_archive: str
    archive_depth: int
    reason_code: str   # FILTER_BUNDLE_PATH | FILTER_EXT | FILTER_NAME |
                       # NO_MAGIC | HEADER_READ_FAILED | DUPLICATE_SOP_INSTANCE
    reason_detail: str = ""


@dataclass
class SeriesEntry:
    """series_index — Layer 2 facts grouped by SeriesInstanceUID."""
    series_instance_uid: str
    series_description: Optional[str] = None
    series_number: Optional[int] = None
    modality: Optional[str] = None
    is_imaging_modality: bool = False
    n_files: int = 0
    file_paths: list[str] = field(default_factory=list)
    transfer_syntax_uids: list[str] = field(default_factory=list)
    pixel_decode_codecs_required: list[str] = field(default_factory=list)


@dataclass
class StudySummary:
    """One-screen view. Answers: 'who, what, when, where, can we run it?'"""
    study_instance_uid: Optional[str] = None
    study_date: Optional[str] = None
    study_description: Optional[str] = None
    accession_number: Optional[str] = None
    patient_id: Optional[str] = None
    patient_name: Optional[str] = None
    patient_age: Optional[str] = None
    patient_sex: Optional[str] = None
    manufacturer: Optional[str] = None
    manufacturer_model_name: Optional[str] = None
    institution_name: Optional[str] = None
    body_part_examined: Optional[str] = None
    modalities_present: list[str] = field(default_factory=list)
    n_imaging_files: int = 0
    n_non_imaging_files: int = 0
    n_imaging_series: int = 0
    pixel_decode_codecs_required: list[str] = field(default_factory=list)


@dataclass
class Manifest:
    manifest_version: str = MANIFEST_VERSION
    work_dir: str = ""
    input_path: str = ""
    status: str = "OK"           # OK | NO_DICOM_FOUND | INPUT_NOT_FOUND |
                                  # EXTRACT_FAILED
    status_detail: str = ""
    files: list[FileEntry] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    series_index: list[SeriesEntry] = field(default_factory=list)
    study_summary: StudySummary = field(default_factory=StudySummary)


# ════════════════════════════════════════════════════════════════════════════
# CANDIDATE FILTERING (zip-entry level, before extraction)
# ════════════════════════════════════════════════════════════════════════════

def _is_dicom_candidate(zip_entry_name: str) -> tuple[bool, str, str]:
    """Decide whether a zip entry could plausibly be a DICOM file.

    Returns (accept, reason_code, reason_detail). When accept=False the
    rejection is recorded in the manifest so we have an audit trail.

    Rejected at this stage = never extracted to disk.
    """
    if zip_entry_name.endswith('/'):
        return False, 'FILTER_DIRECTORY', ''

    # Path separator inside zip is always '/' regardless of source OS, except
    # the InteleViewer payload uses backslashes. Normalize.
    normalized = zip_entry_name.replace('\\', '/')
    parts = normalized.split('/')

    # Reject anything inside a known bundle/package path component
    for part in parts[:-1]:
        upper = part.upper()
        for suf in _BUNDLE_PATH_SUFFIXES:
            if upper.endswith(suf.upper()):
                return False, 'FILTER_BUNDLE_PATH', part

    basename = parts[-1]
    if not basename:
        return False, 'FILTER_DIRECTORY', ''

    # Hidden / metadata files
    if basename.startswith('.') or basename.startswith('._'):
        return False, 'FILTER_HIDDEN', basename

    # Filename blocklist (stem)
    stem = basename.rsplit('.', 1)[0] if '.' in basename else basename
    if stem.upper() in _NON_DICOM_NAMES:
        return False, 'FILTER_NAME', stem

    # Extension blocklist (note: .zip is NOT here; nested zips are recursed)
    if '.' in basename:
        ext = '.' + basename.rsplit('.', 1)[1].lower()
        if ext in _NON_DICOM_EXTS:
            return False, 'FILTER_EXT', ext

    return True, '', ''


def _is_zip_entry(zip_entry_name: str) -> bool:
    """True if the entry should be recursed into as a nested archive."""
    return zip_entry_name.lower().endswith('.zip')


# ════════════════════════════════════════════════════════════════════════════
# DICOM HEADER READING
# ════════════════════════════════════════════════════════════════════════════

def _has_dicm_magic(filepath: Path) -> bool:
    """Bytes 128:132 == b'DICM'."""
    try:
        with open(filepath, 'rb') as f:
            preamble = f.read(132)
        if len(preamble) >= 132 and preamble[128:132] == b'DICM':
            return True
        # Some CD-burned DICOM lacks the 128-byte preamble. Check for known
        # opening tags (group 0x0002 file-meta or 0x0008 identifying).
        if len(preamble) >= 4 and preamble[:2] in (b'\x02\x00', b'\x08\x00'):
            return True
        return False
    except OSError:
        return False


def _str_or_none(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _read_header_facts(filepath: Path) -> Optional[HeaderFacts]:
    """Read DICOM headers (no pixel data) and pack into HeaderFacts."""
    try:
        ds = pydicom.dcmread(str(filepath), stop_before_pixels=True, force=True)
    except Exception as e:
        log.debug("Header read failed for %s: %s", filepath, e)
        return None

    if not hasattr(ds, 'SeriesInstanceUID'):
        # Bytes had DICM magic but no usable SeriesInstanceUID — manifest
        # records this so it can't silently disappear.
        return HeaderFacts()

    modality = _str_or_none(getattr(ds, 'Modality', None))

    sop_class = getattr(ds, 'SOPClassUID', None)
    sop_class_name = sop_class.name if (sop_class is not None and hasattr(sop_class, 'name')) else None

    series_num = getattr(ds, 'SeriesNumber', None)
    inst_num = getattr(ds, 'InstanceNumber', None)
    field_str = getattr(ds, 'MagneticFieldStrength', None)
    rows = getattr(ds, 'Rows', None)
    cols = getattr(ds, 'Columns', None)
    slice_thk = getattr(ds, 'SliceThickness', None)
    te = getattr(ds, 'EchoTime', None)
    tr = getattr(ds, 'RepetitionTime', None)

    def _as_list(v):
        if v is None:
            return None
        try:
            return [float(x) for x in v]
        except (TypeError, ValueError):
            return None

    return HeaderFacts(
        sop_class_uid=_str_or_none(sop_class),
        sop_class_name=sop_class_name,
        sop_instance_uid=_str_or_none(getattr(ds, 'SOPInstanceUID', None)),
        modality=modality,
        is_imaging_modality=(modality in IMAGING_MODALITIES) if modality else False,
        study_instance_uid=_str_or_none(getattr(ds, 'StudyInstanceUID', None)),
        series_instance_uid=_str_or_none(getattr(ds, 'SeriesInstanceUID', None)),
        series_description=_str_or_none(getattr(ds, 'SeriesDescription', None)),
        series_number=int(series_num) if series_num is not None else None,
        instance_number=int(inst_num) if inst_num is not None else None,
        study_description=_str_or_none(getattr(ds, 'StudyDescription', None)),
        study_date=_str_or_none(getattr(ds, 'StudyDate', None)),
        study_time=_str_or_none(getattr(ds, 'StudyTime', None)),
        accession_number=_str_or_none(getattr(ds, 'AccessionNumber', None)),
        body_part_examined=_str_or_none(getattr(ds, 'BodyPartExamined', None)),
        patient_id=_str_or_none(getattr(ds, 'PatientID', None)),
        patient_name=_str_or_none(getattr(ds, 'PatientName', None)),
        patient_age=_str_or_none(getattr(ds, 'PatientAge', None)),
        patient_sex=_str_or_none(getattr(ds, 'PatientSex', None)),
        manufacturer=_str_or_none(getattr(ds, 'Manufacturer', None)),
        manufacturer_model_name=_str_or_none(getattr(ds, 'ManufacturerModelName', None)),
        institution_name=_str_or_none(getattr(ds, 'InstitutionName', None)),
        magnetic_field_strength=float(field_str) if field_str is not None else None,
        rows=int(rows) if rows is not None else None,
        columns=int(cols) if cols is not None else None,
        image_position_patient=_as_list(getattr(ds, 'ImagePositionPatient', None)),
        image_orientation_patient=_as_list(getattr(ds, 'ImageOrientationPatient', None)),
        pixel_spacing=_as_list(getattr(ds, 'PixelSpacing', None)),
        slice_thickness=float(slice_thk) if slice_thk is not None else None,
        echo_time=float(te) if te is not None else None,
        repetition_time=float(tr) if tr is not None else None,
    )


def _read_capability_facts(filepath: Path) -> Optional[CapabilityFacts]:
    """Read transfer syntax and decoder requirements (no pixel decode).

    PixelData (0x7FE0,0x0010) presence is detected by scanning for the tag
    in raw bytes — we never load the array. This is critical for memory:
    on a 1024×1024 16-bit image, loading the array is 2MB; checking the
    tag is 4 bytes.
    """
    try:
        ds = pydicom.dcmread(str(filepath), stop_before_pixels=True, force=True)
    except Exception:
        return None

    file_meta = getattr(ds, 'file_meta', None)
    ts_uid = None
    ts_name = None
    is_compressed = None
    if file_meta is not None:
        raw_ts = getattr(file_meta, 'TransferSyntaxUID', None)
        if raw_ts is not None:
            ts_uid = str(raw_ts)
            ts_name = getattr(raw_ts, 'name', None)
            try:
                is_compressed = bool(raw_ts.is_compressed)
            except Exception:
                is_compressed = None

    decoders = TRANSFER_SYNTAX_DECODERS.get(ts_uid, []) if ts_uid else []
    codec_required = decoders[0] if decoders else None

    # PixelData detection: when stop_before_pixels=True, the tag isn't loaded
    # into the dataset — but pydicom records its file offset. Check that.
    # Fallback: scan raw bytes for the 7FE0,0010 tag pair.
    has_pixel = False
    pdo = getattr(ds, '_pixel_data_offset', None)
    if pdo is not None and pdo > 0:
        has_pixel = True
    else:
        # Tag 0x7FE0,0x0010 — for both Implicit and Explicit VR LE the bytes
        # are E0 7F 10 00 (little-endian). We scan the file once.
        try:
            with open(filepath, 'rb') as f:
                raw = f.read()
            has_pixel = b'\xe0\x7f\x10\x00' in raw
        except OSError:
            has_pixel = False

    return CapabilityFacts(
        transfer_syntax_uid=ts_uid,
        transfer_syntax_name=ts_name,
        is_compressed=is_compressed,
        pixel_decode_codec_required=codec_required,
        decoder_packages_acceptable=decoders,
        has_pixel_data=has_pixel,
    )


# ════════════════════════════════════════════════════════════════════════════
# EXTRACTION
# ════════════════════════════════════════════════════════════════════════════

def _selective_extract_zip(
    zf: zipfile.ZipFile,
    work_dir: Path,
    archive_label: str,
    archive_depth: int,
    rejections: list[Rejection],
) -> tuple[list[Path], list[tuple[str, Path]]]:
    """Extract DICOM-candidate entries from one zip into work_dir.

    Returns:
      extracted_files       — list of disk paths (Layer 1 candidates)
      nested_archive_paths  — list of (in-archive name, on-disk path) for
                              .zip entries that should be recursed into.

    Rejections are appended to the rejections list with audit detail.
    """
    extracted: list[Path] = []
    nested: list[tuple[str, Path]] = []

    for name in zf.namelist():
        # Always check the candidate filter first
        accept, reason_code, reason_detail = _is_dicom_candidate(name)

        # Nested zip detection: bypass the extension blocklist (which doesn't
        # have .zip in it anyway), but still respect bundle filtering.
        # If the name is inside a bundle path, do not recurse it.
        is_nested_zip = _is_zip_entry(name) and reason_code != 'FILTER_BUNDLE_PATH'

        if not accept and not is_nested_zip:
            rejections.append(Rejection(
                source_archive=archive_label,
                source_path_in_archive=name,
                archive_depth=archive_depth,
                reason_code=reason_code,
                reason_detail=reason_detail,
            ))
            continue

        # Sanitize the on-disk path. zipfile.extract handles path traversal
        # protection for us; we only need to make a safe relative path under
        # work_dir.
        try:
            zf.extract(name, str(work_dir))
        except Exception as e:
            rejections.append(Rejection(
                source_archive=archive_label,
                source_path_in_archive=name,
                archive_depth=archive_depth,
                reason_code='EXTRACT_FAILED',
                reason_detail=str(e),
            ))
            continue

        # zipfile.extract normalizes path separators to OS-native; but on
        # POSIX it preserves backslashes from the InteleViewer-style paths
        # ("CViewer\foo\bar"), so the file lands as one literal name with
        # embedded backslashes. We just need to find what's on disk.
        on_disk = work_dir / name
        if not on_disk.exists():
            # Try the literal-with-backslashes case
            on_disk = work_dir / name.replace('/', os.sep)
        if not on_disk.exists():
            rejections.append(Rejection(
                source_archive=archive_label,
                source_path_in_archive=name,
                archive_depth=archive_depth,
                reason_code='EXTRACT_PATH_MISSING',
                reason_detail=str(on_disk),
            ))
            continue

        if is_nested_zip:
            nested.append((name, on_disk))
        else:
            extracted.append(on_disk)

    return extracted, nested


def _extract_recursive(
    zip_path: Path,
    work_dir: Path,
    archive_label: str,
    archive_depth: int,
    rejections: list[Rejection],
) -> list[tuple[Path, str, int]]:
    """Recursively extract zips up to MAX_NESTED_ZIP_DEPTH.

    Returns list of (file_on_disk, source_archive_label, depth) tuples for
    every non-zip file extracted along the way.
    """
    if archive_depth > MAX_NESTED_ZIP_DEPTH:
        rejections.append(Rejection(
            source_archive=archive_label,
            source_path_in_archive=str(zip_path),
            archive_depth=archive_depth,
            reason_code='MAX_DEPTH_EXCEEDED',
            reason_detail=f'depth={archive_depth} exceeds cap={MAX_NESTED_ZIP_DEPTH}',
        ))
        return []

    try:
        zf = zipfile.ZipFile(str(zip_path))
    except zipfile.BadZipFile as e:
        rejections.append(Rejection(
            source_archive=archive_label,
            source_path_in_archive=str(zip_path),
            archive_depth=archive_depth,
            reason_code='BAD_ZIP',
            reason_detail=str(e),
        ))
        return []

    with zf:
        files, nested = _selective_extract_zip(
            zf, work_dir, archive_label, archive_depth, rejections,
        )

    out: list[tuple[Path, str, int]] = [
        (p, archive_label, archive_depth) for p in files
    ]

    # Recurse into nested zips
    for nested_name, nested_path in nested:
        nested_label = f'{archive_label} -> {nested_name}'
        # Extract into a subdir named after the nested zip, to avoid clobber
        nested_subdir = work_dir / f'_nested_{archive_depth + 1}_{nested_path.stem}'
        nested_subdir.mkdir(parents=True, exist_ok=True)
        out.extend(_extract_recursive(
            nested_path, nested_subdir, nested_label,
            archive_depth + 1, rejections,
        ))
        # Delete the nested zip itself; we have its contents extracted now
        try:
            nested_path.unlink()
        except OSError:
            pass

    return out


# ════════════════════════════════════════════════════════════════════════════
# DEDUPLICATION
# ════════════════════════════════════════════════════════════════════════════

def _dedupe_by_sop_instance_uid(
    files: list[FileEntry],
    rejections: list[Rejection],
) -> list[FileEntry]:
    """Drop duplicate SOPInstanceUIDs (same image present in multiple paths).

    Real-world case: MRI_C_Spine_2024.zip has DICOM unzipped at DICOM/PA/...
    AND zipped at DICOM.zip (same content). After recursive extract we'd
    have every image twice. SOPInstanceUID is unique per image, so it's the
    right key.

    Files with no SOPInstanceUID are kept as-is (we don't know if they're
    duplicates of each other, and dropping them silently would be worse
    than carrying possibly-duplicates).
    """
    seen: dict[str, FileEntry] = {}
    out: list[FileEntry] = []
    for fe in files:
        sop = fe.headers.sop_instance_uid if fe.headers else None
        if sop is None:
            out.append(fe)
            continue
        if sop in seen:
            # Keep whichever one was extracted from the shallower archive
            # depth; that's the more "canonical" copy. Reject the other.
            existing = seen[sop]
            keep, drop = (existing, fe) if (
                existing.extraction.archive_depth <= fe.extraction.archive_depth
            ) else (fe, existing)
            rejections.append(Rejection(
                source_archive=drop.extraction.source_archive,
                source_path_in_archive=drop.extraction.source_path_in_archive,
                archive_depth=drop.extraction.archive_depth,
                reason_code='DUPLICATE_SOP_INSTANCE',
                reason_detail=f'sop={sop} kept_at={keep.extraction.extracted_path}',
            ))
            # Defensive: unlink the rejected file from disk so directory
            # rescans get the same answer as manifest reads.
            try:
                from pathlib import Path as _P
                _P(drop.extraction.extracted_path).unlink()
            except OSError as e:
                log.debug("Could not unlink duplicate %s: %s",
                          drop.extraction.extracted_path, e)
            seen[sop] = keep
            # Replace the kept entry in `out` if needed
            for i, existing_in_out in enumerate(out):
                if existing_in_out is existing and keep is not existing:
                    out[i] = keep
                    break
        else:
            seen[sop] = fe
            out.append(fe)
    return out


# ════════════════════════════════════════════════════════════════════════════
# AGGREGATIONS — series_index and study_summary
# ════════════════════════════════════════════════════════════════════════════

def _build_series_index(files: list[FileEntry]) -> list[SeriesEntry]:
    grouped: dict[str, list[FileEntry]] = defaultdict(list)
    for fe in files:
        h = fe.headers
        if h is None or not h.series_instance_uid:
            continue
        grouped[h.series_instance_uid].append(fe)

    out: list[SeriesEntry] = []
    for sid, members in grouped.items():
        # Sort by InstanceNumber for stable ordering
        members.sort(key=lambda fe: (fe.headers.instance_number or 0))
        first = members[0]
        h0 = first.headers
        ts_uids: list[str] = []
        codecs: list[str] = []
        for fe in members:
            c = fe.capabilities
            if c is None:
                continue
            if c.transfer_syntax_uid and c.transfer_syntax_uid not in ts_uids:
                ts_uids.append(c.transfer_syntax_uid)
            if c.pixel_decode_codec_required and c.pixel_decode_codec_required not in codecs:
                codecs.append(c.pixel_decode_codec_required)
        out.append(SeriesEntry(
            series_instance_uid=sid,
            series_description=h0.series_description,
            series_number=h0.series_number,
            modality=h0.modality,
            is_imaging_modality=h0.is_imaging_modality,
            n_files=len(members),
            file_paths=[fe.extraction.extracted_path for fe in members],
            transfer_syntax_uids=ts_uids,
            pixel_decode_codecs_required=codecs,
        ))

    # Sort: imaging modalities first, then by series_number
    out.sort(key=lambda s: (
        not s.is_imaging_modality,
        s.series_number if s.series_number is not None else 999_999,
    ))
    return out


def _build_study_summary(
    files: list[FileEntry],
    series: list[SeriesEntry],
) -> StudySummary:
    """One-screen view. Pulled from the first imaging file we can find
    (most reliable for study-level metadata; PR/SR files often carry less
    populated study tags)."""
    summary = StudySummary()

    # Pick a representative imaging file for study-level tags
    rep: Optional[FileEntry] = next(
        (fe for fe in files if fe.headers and fe.headers.is_imaging_modality),
        None,
    )
    # If no imaging files, fall back to anything with headers
    if rep is None:
        rep = next((fe for fe in files if fe.headers and fe.headers.study_instance_uid), None)

    if rep and rep.headers:
        h = rep.headers
        summary.study_instance_uid = h.study_instance_uid
        summary.study_date = h.study_date
        summary.study_description = h.study_description
        summary.accession_number = h.accession_number
        summary.patient_id = h.patient_id
        summary.patient_name = h.patient_name
        summary.patient_age = h.patient_age
        summary.patient_sex = h.patient_sex
        summary.manufacturer = h.manufacturer
        summary.manufacturer_model_name = h.manufacturer_model_name
        summary.institution_name = h.institution_name
        summary.body_part_examined = h.body_part_examined

    # Modality counts
    mods: list[str] = []
    n_imaging_files = 0
    n_non_imaging_files = 0
    for fe in files:
        if fe.headers and fe.headers.modality:
            if fe.headers.modality not in mods:
                mods.append(fe.headers.modality)
            if fe.headers.is_imaging_modality:
                n_imaging_files += 1
            else:
                n_non_imaging_files += 1
    summary.modalities_present = mods
    summary.n_imaging_files = n_imaging_files
    summary.n_non_imaging_files = n_non_imaging_files
    summary.n_imaging_series = sum(1 for s in series if s.is_imaging_modality)

    # Codecs required (union over imaging series only — non-imaging codecs
    # don't matter for the analysis pipeline)
    codecs: list[str] = []
    for s in series:
        if not s.is_imaging_modality:
            continue
        for c in s.pixel_decode_codecs_required:
            if c not in codecs:
                codecs.append(c)
    summary.pixel_decode_codecs_required = codecs

    return summary


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════════════

def walk(
    input_path: str | Path,
    work_dir: Optional[str | Path] = None,
    write_manifest: bool = True,
) -> Manifest:
    """Lazy Walker. Accepts a zip, a directory, or a single .dcm file.

    Args:
        input_path:    file or folder to walk
        work_dir:      where to extract. None = mkdtemp. Caller is responsible
                       for cleanup if they passed one in. If None, the work
                       dir is leaked deliberately (caller decides cleanup).
        write_manifest: also write manifest.json into the work_dir.

    Returns a Manifest. Always returns; never raises on customer-input
    problems. Errors are recorded as status + rejections.
    """
    input_path = Path(input_path)
    manifest = Manifest(input_path=str(input_path))

    if not input_path.exists():
        manifest.status = 'INPUT_NOT_FOUND'
        manifest.status_detail = f'no such file: {input_path}'
        return manifest

    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix='lzw_'))
    else:
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
    manifest.work_dir = str(work_dir)

    # ─── Phase A: extract ────────────────────────────────────────────────
    extracted_triples: list[tuple[Path, str, int]] = []   # (path, archive, depth)

    if input_path.is_file():
        if input_path.suffix.lower() == '.zip':
            extracted_triples = _extract_recursive(
                input_path, work_dir,
                archive_label=input_path.name,
                archive_depth=0,
                rejections=manifest.rejections,
            )
        else:
            # Single file (probably a .dcm). Stage it into work_dir.
            staged = work_dir / input_path.name
            shutil.copy2(input_path, staged)
            extracted_triples = [(staged, input_path.name, 0)]
    elif input_path.is_dir():
        # Treat as already-extracted. Walk recursively.
        for f in input_path.rglob('*'):
            if not f.is_file():
                continue
            extracted_triples.append((f, str(input_path), 0))
    else:
        manifest.status = 'INPUT_NOT_FOUND'
        manifest.status_detail = f'not a file or directory: {input_path}'
        return manifest

    # ─── Phase B: classify each extracted file ───────────────────────────
    file_entries: list[FileEntry] = []
    for path, src_archive, depth in extracted_triples:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        # First, check magic. If there's no DICM and the file doesn't have
        # the implicit-VR opening tag, reject it.
        has_magic = _has_dicm_magic(path)
        if not has_magic:
            manifest.rejections.append(Rejection(
                source_archive=src_archive,
                source_path_in_archive=str(path.relative_to(work_dir))
                    if work_dir in path.parents else str(path),
                archive_depth=depth,
                reason_code='NO_MAGIC',
                reason_detail=f'size={size}',
            ))
            try:
                path.unlink()
            except OSError:
                pass
            continue

        # Build Layer 1
        try:
            rel = str(path.relative_to(work_dir))
        except ValueError:
            rel = str(path)
        extraction = ExtractionFacts(
            extracted_path=str(path),
            source_archive=src_archive,
            source_path_in_archive=rel,
            archive_depth=depth,
            file_size_bytes=size,
            has_dicm_magic=True,
        )

        # Layer 2 + Layer 3
        headers = _read_header_facts(path)
        capabilities = _read_capability_facts(path)

        if headers is None or headers.series_instance_uid is None:
            # Magic was right but the headers are unreadable or unidentifiable.
            # Record and reject.
            manifest.rejections.append(Rejection(
                source_archive=src_archive,
                source_path_in_archive=rel,
                archive_depth=depth,
                reason_code='HEADER_READ_FAILED' if headers is None else 'NO_SERIES_UID',
                reason_detail='',
            ))
            try:
                path.unlink()
            except OSError:
                pass
            continue

        file_entries.append(FileEntry(
            extraction=extraction,
            headers=headers,
            capabilities=capabilities,
        ))

    # ─── Phase C: dedupe by SOPInstanceUID ───────────────────────────────
    file_entries = _dedupe_by_sop_instance_uid(file_entries, manifest.rejections)

    manifest.files = file_entries

    # ─── Phase D: aggregate ──────────────────────────────────────────────
    manifest.series_index = _build_series_index(file_entries)
    manifest.study_summary = _build_study_summary(file_entries, manifest.series_index)

    # ─── Phase E: status ─────────────────────────────────────────────────
    if manifest.study_summary.n_imaging_files == 0:
        manifest.status = 'NO_DICOM_FOUND'
        manifest.status_detail = (
            f'extracted {len(file_entries)} file(s); '
            f'{manifest.study_summary.n_non_imaging_files} non-imaging; '
            f'{len(manifest.rejections)} rejected'
        )
    else:
        manifest.status = 'OK'

    # ─── Phase F: persist manifest ───────────────────────────────────────
    if write_manifest:
        out_path = work_dir / 'manifest.json'
        out_path.write_text(json.dumps(asdict(manifest), indent=2, default=str))

    return manifest


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def _main():
    import argparse
    p = argparse.ArgumentParser(description='Lazy Walker — DICOM unpack.')
    p.add_argument('input', help='zip, directory, or single DICOM file')
    p.add_argument('--work-dir', default=None, help='where to extract')
    p.add_argument('--no-manifest', action='store_true',
                   help='do not write manifest.json')
    p.add_argument('--summary', action='store_true', help='print one-screen summary')
    args = p.parse_args()

    m = walk(args.input, work_dir=args.work_dir, write_manifest=not args.no_manifest)

    if args.summary:
        s = m.study_summary
        print(f'Status:       {m.status}')
        print(f'Work dir:     {m.work_dir}')
        print(f'Patient:      {s.patient_name}  ID={s.patient_id}  '
              f'age={s.patient_age}  sex={s.patient_sex}')
        print(f'Study:        {s.study_description}  date={s.study_date}')
        print(f'Body part:    {s.body_part_examined}')
        print(f'Scanner:      {s.manufacturer} {s.manufacturer_model_name}  '
              f'@ {s.institution_name}')
        print(f'Modalities:   {", ".join(s.modalities_present)}')
        print(f'Imaging files:    {s.n_imaging_files}  '
              f'(in {s.n_imaging_series} series)')
        print(f'Non-imaging:      {s.n_non_imaging_files}')
        print(f'Rejected:         {len(m.rejections)}')
        print(f'Codecs required:  {", ".join(s.pixel_decode_codecs_required) or "(none)"}')
        print()
        print('Series:')
        for ser in m.series_index:
            tag = 'IMG' if ser.is_imaging_modality else 'aux'
            print(f'  [{tag}] #{ser.series_number or "?"} {ser.modality:>3} '
                  f'{ser.n_files:>4} files  '
                  f'{ser.series_description or "(no description)"}')
    else:
        print(json.dumps(asdict(m), indent=2, default=str))


if __name__ == '__main__':
    _main()
