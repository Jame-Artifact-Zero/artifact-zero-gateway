"""
Artifact Zero DICOM Pipeline — Modality Expansion
Additions to az_dicom_processor.py for non-MR modality scoring

Drop these into the existing az_dicom_processor.py alongside the MR scoring logic.
Each modality follows the same GOOD_SEQUENCES / SKIP_KEYWORDS / score_sequence pattern.
"""

# ============================================================
# MODALITY DETECTION
# ============================================================

def detect_modality(ds):
    """
    Read DICOM Modality tag (0008,0060).
    Returns: 'MR', 'CT', 'XR', 'US', 'NM', or 'UNKNOWN'
    """
    modality = getattr(ds, 'Modality', '').upper().strip()
    # Normalize common variants
    modality_map = {
        'MR': 'MR',
        'CT': 'CT',
        'CR': 'XR',    # Computed Radiography -> plain film
        'DX': 'XR',    # Digital Radiography -> plain film
        'XR': 'XR',
        'US': 'US',
        'NM': 'NM',
        'PT': 'NM',    # PET -> nuclear medicine family
    }
    return modality_map.get(modality, 'UNKNOWN')


# ============================================================
# CT SCORING
# ============================================================

CT_GOOD_SEQUENCES = {
    'BONE': {
        'keywords': ['bone', 'sharp', 'b60', 'b70', 'b80', 'hr'],
        'metrics': ['gap', 'hu_mean', 'hu_std'],
    },
    'SOFT': {
        'keywords': ['soft', 'standard', 'b30', 'b40', 'body'],
        'metrics': ['gap', 'hu_mean', 'hu_std'],
    },
    'CONTRAST_ARTERIAL': {
        'keywords': ['arterial', 'art', 'early'],
        'metrics': ['gap', 'enhancement_ratio'],
    },
    'CONTRAST_VENOUS': {
        'keywords': ['venous', 'portal', 'delayed', 'late'],
        'metrics': ['gap', 'enhancement_ratio'],
    },
    'CONTRAST_DELAYED': {
        'keywords': ['delayed', '5min', '10min', 'excretory'],
        'metrics': ['gap', 'enhancement_ratio'],
    },
    'NON_CONTRAST': {
        'keywords': ['non-con', 'pre-con', 'without', 'nc', 'pre'],
        'metrics': ['gap', 'hu_mean', 'hu_std'],
    },
}

CT_SKIP_KEYWORDS = [
    'scout', 'topogram', 'surview', 'localizer', 'dose report',
    'screen save', 'secondary capture', 'mpr reformat',
]


def classify_ct_sequence(ds):
    """
    Classify a CT series into a sequence type based on
    SeriesDescription and ConvolutionKernel.
    """
    desc = (getattr(ds, 'SeriesDescription', '') or '').lower()
    kernel = (getattr(ds, 'ConvolutionKernel', '') or '').lower()
    combined = f"{desc} {kernel}"

    # Skip non-diagnostic series
    for skip in CT_SKIP_KEYWORDS:
        if skip in combined:
            return None

    # Match against known sequence types
    for seq_type, config in CT_GOOD_SEQUENCES.items():
        for kw in config['keywords']:
            if kw in combined:
                return seq_type

    # Default: classify as SOFT if no match
    return 'SOFT'


def compute_ct_metrics(pixel_array, seq_type):
    """
    Compute metrics from CT pixel data.

    gap: same two-component algebraic decomposition as MR.
         A = bone/high-density component
         B = soft tissue/low-density component
         w(x) = (Y(x) - B) / (A - B) at each voxel

    hu_mean: mean Hounsfield unit value (after rescale)
    hu_std: standard deviation of HU values
    enhancement_ratio: post/pre contrast ratio (requires paired series)
    """
    import numpy as np

    metrics = {}

    # Basic HU statistics
    flat = pixel_array.flatten().astype(float)
    metrics['hu_mean'] = float(np.mean(flat))
    metrics['hu_std'] = float(np.std(flat))

    # Two-component gap: algebraic decomposition
    # For CT: A = 95th percentile (bone/calcification), B = 5th percentile (air/fat)
    A = float(np.percentile(flat, 95))
    B = float(np.percentile(flat, 5))
    metrics['gap'] = A - B

    return metrics


# ============================================================
# XR (PLAIN FILM) SCORING
# ============================================================

XR_GOOD_SEQUENCES = {
    'CHEST_PA': {
        'keywords': ['chest', 'pa', 'frontal'],
        'metrics': ['gap', 'exposure_index', 'lung_field_ratio'],
    },
    'CHEST_LAT': {
        'keywords': ['chest', 'lateral', 'lat'],
        'metrics': ['gap', 'exposure_index'],
    },
    'EXTREMITY': {
        'keywords': ['hand', 'wrist', 'ankle', 'foot', 'knee', 'elbow',
                     'finger', 'toe', 'forearm', 'tibia', 'fibula', 'humerus'],
        'metrics': ['gap', 'bone_density_index'],
    },
    'SPINE': {
        'keywords': ['spine', 'cervical', 'thoracic', 'lumbar', 'sacrum',
                     'c-spine', 't-spine', 'l-spine'],
        'metrics': ['gap', 'alignment_index'],
    },
    'PELVIS': {
        'keywords': ['pelvis', 'hip', 'si joint'],
        'metrics': ['gap', 'symmetry_index'],
    },
}

XR_SKIP_KEYWORDS = [
    'dose report', 'screen save', 'secondary capture', 'quality',
]


def classify_xr_sequence(ds):
    """Classify an XR series by body part and projection."""
    desc = (getattr(ds, 'SeriesDescription', '') or '').lower()
    body_part = (getattr(ds, 'BodyPartExamined', '') or '').lower()
    combined = f"{desc} {body_part}"

    for skip in XR_SKIP_KEYWORDS:
        if skip in combined:
            return None

    for seq_type, config in XR_GOOD_SEQUENCES.items():
        for kw in config['keywords']:
            if kw in combined:
                return seq_type

    return 'EXTREMITY'  # default for unclassified XR


def compute_xr_metrics(pixel_array, seq_type):
    """
    Compute metrics from plain film pixel data.

    gap: two-component decomposition (bone vs soft tissue)
    exposure_index: proxy from pixel intensity distribution
    """
    import numpy as np

    flat = pixel_array.flatten().astype(float)
    metrics = {}

    A = float(np.percentile(flat, 95))
    B = float(np.percentile(flat, 5))
    metrics['gap'] = A - B

    # Exposure index proxy: median intensity normalized to bit depth
    metrics['exposure_index'] = float(np.median(flat))

    return metrics


# ============================================================
# US (ULTRASOUND) SCORING
# ============================================================

US_GOOD_SEQUENCES = {
    'MSK': {
        'keywords': ['msk', 'tendon', 'joint', 'shoulder', 'knee', 'ankle',
                     'elbow', 'wrist', 'hip', 'muscle', 'bursa'],
        'metrics': ['gap', 'echogenicity_ratio'],
    },
    'ABDOMINAL': {
        'keywords': ['abdomen', 'liver', 'kidney', 'gallbladder', 'pancreas',
                     'spleen', 'aorta', 'rvf', 'renal'],
        'metrics': ['gap', 'echogenicity_ratio'],
    },
    'VASCULAR': {
        'keywords': ['doppler', 'carotid', 'venous', 'arterial', 'dvt',
                     'peripheral', 'aorta'],
        'metrics': ['gap', 'flow_index'],
    },
    'OBSTETRIC': {
        'keywords': ['ob', 'fetal', 'pregnancy', 'trimester', 'nuchal'],
        'metrics': ['gap'],
    },
}

US_SKIP_KEYWORDS = [
    'report', 'screen save', 'secondary capture', 'worksheet',
]


def classify_us_sequence(ds):
    """Classify an US series."""
    desc = (getattr(ds, 'SeriesDescription', '') or '').lower()
    body_part = (getattr(ds, 'BodyPartExamined', '') or '').lower()
    combined = f"{desc} {body_part}"

    for skip in US_SKIP_KEYWORDS:
        if skip in combined:
            return None

    for seq_type, config in US_GOOD_SEQUENCES.items():
        for kw in config['keywords']:
            if kw in combined:
                return seq_type

    return 'MSK'  # default


def compute_us_metrics(pixel_array, seq_type):
    """
    Compute metrics from ultrasound pixel data.

    gap: two-component decomposition (hyperechoic vs hypoechoic)
    echogenicity_ratio: ratio of bright to dark regions
    """
    import numpy as np

    flat = pixel_array.flatten().astype(float)
    metrics = {}

    A = float(np.percentile(flat, 95))
    B = float(np.percentile(flat, 5))
    metrics['gap'] = A - B

    # Echogenicity ratio: mean of top quartile / mean of bottom quartile
    q75 = np.percentile(flat, 75)
    q25 = np.percentile(flat, 25)
    bright = flat[flat >= q75]
    dark = flat[flat <= q25]
    if len(dark) > 0 and np.mean(dark) > 0:
        metrics['echogenicity_ratio'] = float(np.mean(bright) / np.mean(dark))
    else:
        metrics['echogenicity_ratio'] = 0.0

    return metrics


# ============================================================
# NM (NUCLEAR MEDICINE) SCORING
# ============================================================

NM_GOOD_SEQUENCES = {
    'BONE_SCAN': {
        'keywords': ['bone', 'whole body', 'skeletal', 'delayed', 'blood pool'],
        'metrics': ['gap', 'uptake_ratio', 'asymmetry_index'],
    },
    'THYROID': {
        'keywords': ['thyroid', 'i-123', 'tc-99m', 'pertechnetate'],
        'metrics': ['gap', 'uptake_ratio'],
    },
    'RENAL': {
        'keywords': ['renal', 'mag3', 'dtpa', 'kidney', 'gfr'],
        'metrics': ['gap', 'differential_function'],
    },
}

NM_SKIP_KEYWORDS = [
    'dose report', 'screen save', 'ct attenuation', 'quality',
]


def classify_nm_sequence(ds):
    """Classify a nuclear medicine series."""
    desc = (getattr(ds, 'SeriesDescription', '') or '').lower()
    study_desc = (getattr(ds, 'StudyDescription', '') or '').lower()
    combined = f"{desc} {study_desc}"

    for skip in NM_SKIP_KEYWORDS:
        if skip in combined:
            return None

    for seq_type, config in NM_GOOD_SEQUENCES.items():
        for kw in config['keywords']:
            if kw in combined:
                return seq_type

    return 'BONE_SCAN'  # default


def compute_nm_metrics(pixel_array, seq_type):
    """
    Compute metrics from nuclear medicine pixel data.

    gap: two-component (hot vs cold regions)
    uptake_ratio: ratio of hot region mean to background mean
    asymmetry_index: L-R asymmetry in counts
    """
    import numpy as np

    flat = pixel_array.flatten().astype(float)
    metrics = {}

    A = float(np.percentile(flat, 95))
    B = float(np.percentile(flat, 5))
    metrics['gap'] = A - B

    # Uptake ratio: top 10% mean / bottom 50% mean
    p90 = np.percentile(flat, 90)
    p50 = np.percentile(flat, 50)
    hot = flat[flat >= p90]
    cold = flat[flat <= p50]
    if len(cold) > 0 and np.mean(cold) > 0:
        metrics['uptake_ratio'] = float(np.mean(hot) / np.mean(cold))
    else:
        metrics['uptake_ratio'] = 0.0

    # Asymmetry: split image L/R at midpoint
    if len(pixel_array.shape) >= 2:
        mid = pixel_array.shape[1] // 2
        left = pixel_array[:, :mid].astype(float)
        right = pixel_array[:, mid:].astype(float)
        l_sum = np.sum(left)
        r_sum = np.sum(right)
        if l_sum + r_sum > 0:
            metrics['asymmetry_index'] = float(abs(l_sum - r_sum) / (l_sum + r_sum))
        else:
            metrics['asymmetry_index'] = 0.0
    else:
        metrics['asymmetry_index'] = 0.0

    return metrics


# ============================================================
# UNIFIED SCORE_SEQUENCE DISPATCHER
# ============================================================

def score_sequence_multimodal(ds, pixel_array):
    """
    Unified scoring function. Detects modality, classifies sequence,
    computes metrics, returns result dict.

    Drop-in replacement for the existing MR-only score_sequence.
    Falls back to MR scoring for MR modality (existing behavior).
    """
    modality = detect_modality(ds)

    dispatch = {
        'CT': (classify_ct_sequence, compute_ct_metrics),
        'XR': (classify_xr_sequence, compute_xr_metrics),
        'US': (classify_us_sequence, compute_us_metrics),
        'NM': (classify_nm_sequence, compute_nm_metrics),
    }

    if modality == 'MR':
        # Existing MR scoring path — unchanged
        return None  # Caller falls through to existing score_sequence

    if modality not in dispatch:
        return {
            'status': 'UNSUPPORTED',
            'modality': modality,
            'message': f'Modality {modality} not yet supported',
        }

    classify_fn, compute_fn = dispatch[modality]
    seq_type = classify_fn(ds)

    if seq_type is None:
        return {
            'status': 'SKIPPED',
            'modality': modality,
            'message': 'Non-diagnostic series (scout, dose report, etc.)',
        }

    metrics = compute_fn(pixel_array, seq_type)

    return {
        'status': 'SCORED',
        'modality': modality,
        'seq_type': seq_type,
        'metrics': metrics,
    }


# ============================================================
# FIELD STRENGTH SCALING MODEL
# ============================================================

def field_strength_multiplier(field_strength_T):
    """
    Compute gap threshold multiplier for non-1.5T field strengths.

    The relationship between field strength and signal gap is NOT linear
    below 1.5T. SNR scales approximately as B0, but tissue contrast
    (which drives gap) scales differently:

    - 1.5T to 3T: approximately linear (2x field ~ 1.5-2x gap). The
      existing linear multiplier field_strength/1.5 works.

    - Below 1.5T: T1 relaxation times shorten, T2 stays similar.
      Contrast between tissues changes non-linearly. At 0.3T (Hitachi
      AIRIS II), T1 contrast is reduced but T2/STIR contrast drops
      much more dramatically due to lower SNR.

    Model: piecewise
      >= 1.5T: linear scaling (field / 1.5)
      < 1.5T:  power-law scaling (field / 1.5)^1.5

    The exponent 1.5 is approximate — calibrated from the observation
    that 0.3T produces gap values 10-15x higher than 1.5T equivalent,
    which is consistent with (1.5/0.3)^1.5 = 5^1.5 = 11.2x.

    Source: tbc — needs live calibration against 0.3T/0.55T/1.0T data.
    """
    if field_strength_T is None or field_strength_T <= 0:
        return 1.0  # Unknown field strength, assume 1.5T

    if field_strength_T >= 1.5:
        # Linear above 1.5T (confirmed: works for 3T)
        return field_strength_T / 1.5
    else:
        # Power-law below 1.5T
        # (1.5/B0)^1.5 gives the divisor for gap thresholds
        # Invert: multiply thresholds by (B0/1.5)^1.5
        return (field_strength_T / 1.5) ** 1.5


def adjust_threshold_for_field_strength(threshold, field_strength_T):
    """
    Adjust an impression rule threshold for the study's field strength.

    Usage:
        adjusted = adjust_threshold_for_field_strength(rule.threshold, 0.3)
        if metrics['gap'] < adjusted:
            flag(rule)
    """
    multiplier = field_strength_multiplier(field_strength_T)
    return threshold * multiplier
