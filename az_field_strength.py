"""
az_field_strength.py
====================
Field strength threshold modifier.

Insert point: _apply_rules() in dicom_processor_api.py
Wrap every threshold comparison with adjust_threshold() before the lt/gt check.

Usage in _apply_rules():

    from az_field_strength import adjust_threshold

    # existing pattern (before):
    if rule['operator'] == 'lt' and value < rule['threshold']:
        flag(...)

    # updated pattern (after):
    adjusted = adjust_threshold(rule['threshold'], rule['metric'],
                                result.get('field_strength', 1.5))
    if rule['operator'] == 'lt' and value < adjusted:
        flag(...)

Background:
    Higher field strength increases absolute signal (and therefore gap values)
    roughly linearly with field strength. A gap of 200 at 1.5T corresponds
    to approximately 400 at 3T for the same tissue state.

    Fraction-based metrics (peak_asym, compression_pct, std_fraction,
    pct_left, peak_disagree, enhancement_delta, b_pg_center_edge) are
    normalized ratios — they are field-strength invariant. No adjustment needed.

    Gap-based metrics (min_gap, run_width, b_alg_b_joint) are absolute
    signal values — they scale with field strength.

    Calibration basis:
        1.5T C-spine T2 gap observed: 196-307 (live API, GE and Philips)
        3T knee dataset (K0): gap values ~1.5-2x higher for same tissue
        Multiplier: field_strength / 1.5 (linear approximation)
"""

# Metrics that scale with field strength (absolute signal values)
GAP_METRICS = {
    # Existing
    "gap", "min_gap", "run_width", "b_alg_b_joint", "alg_B_joint",
    "hu_mean", "hu_std",
    # Pre-impression: spine cord-canal geometry (absolute mm)
    "space_min_mm", "space_mean_mm", "space_max_mm",
    "left_space_mm", "right_space_mm",
    # Pre-impression: spine cord cross-section (absolute mm2)
    "cord_area_mm2", "cord_area_mean_mm2",
    # Pre-impression: brain (absolute distances and counts)
    "midline_shift_mm", "midline_shift_abs_mm",
    "flair_lesion_count", "flair_lesion_total_area_mm2",
    # Pre-impression: joint volumetric findings (absolute mm3)
    "effusion_volume_mm3", "marrow_edema_volume_mm3",
    "effusion_total_area_mm2", "marrow_edema_total_area_mm2",
    # Pre-impression: breast mass morphology (absolute mm)
    "mass_diameter_mm", "mass_area_mm2",
}

# Metrics that are field-strength invariant (normalized ratios)
# Confirmed invariant in K0 Exp12 (1,738 subjects, three scanners)
FRACTION_METRICS = {
    # Existing
    "fraction", "mean_fraction", "peak_asym", "peak_left_asym",
    "compression_pct", "std_fraction", "pct_left", "pct_left_dominant",
    "peak_disagree", "peak_disagree_score", "enhancement_delta",
    "b_pg_center_edge", "pg_center_edge", "alg_B_fraction",
    "alg_B_calibrated", "symmetry_index", "asymmetry_index",
    "uptake_ratio", "lung_field_ratio",
    # Pre-impression: spine L/R asymmetry (signed and absolute ratios)
    "asym_lr", "asym_lr_abs", "asym_lr_mean", "asym_lr_max_abs",
    # Pre-impression: brain asymmetry ratios
    "ventricle_asym_lr", "ventricle_asym_abs",
    "brain_asym_lr", "brain_asym_abs",
    # Pre-impression: breast paired-anatomy ratios
    "tissue_asym_lr", "tissue_asym_abs", "mass_count_asym",
}

REFERENCE_FIELD_STRENGTH = 1.5


def _field_strength_multiplier(field_strength: float) -> float:
    """
    Piecewise field strength multiplier.
    Linear >= 1.5T (confirmed C-spine live data).
    Power-law below 1.5T (exponent 1.5 fits observed 0.3T gap inflation).
    TBC: refine exponent when 0.55T and 1.0T data available.
    """
    if not field_strength or field_strength <= 0:
        return 1.0
    if field_strength >= REFERENCE_FIELD_STRENGTH:
        return field_strength / REFERENCE_FIELD_STRENGTH
    return (field_strength / REFERENCE_FIELD_STRENGTH) ** 1.5


def adjust_threshold(
    base_threshold: float,
    metric: str,
    field_strength: float,
) -> float:
    """
    Adjust a rule threshold for the field strength of the current study.

    Gap metrics scale with field strength (piecewise model).
    Fraction metrics are invariant -- returned unchanged.
    Unknown metrics returned unchanged.

    Examples:
        adjust_threshold(200, 'gap', 1.5)   -> 200.0
        adjust_threshold(200, 'gap', 3.0)   -> 400.0
        adjust_threshold(200, 'gap', 0.3)   -> 17.9
        adjust_threshold(0.274, 'fraction', 3.0) -> 0.274
        adjust_threshold(0.949, 'pg_center_edge', 3.0) -> 0.949
    """
    if not field_strength or field_strength <= 0:
        field_strength = REFERENCE_FIELD_STRENGTH

    if metric in FRACTION_METRICS:
        return float(base_threshold)

    if metric in GAP_METRICS:
        return float(base_threshold) * _field_strength_multiplier(field_strength)

    return float(base_threshold)


def get_field_strength(result: dict, default: float = 1.5) -> float:
    """Safely extract field strength from a result dict."""
    fs = result.get("field_strength", default)
    try:
        fs = float(fs)
        return fs if fs > 0 else default
    except (TypeError, ValueError):
        return default


def apply_rule(rule: dict, value, field_strength: float) -> bool:
    """
    Apply a single impression rule against a measured value
    with field strength adjustment.

    Returns True if the rule fires, else False.
    Handles None, non-numeric, and non-finite values safely.
    """
    if value is None:
        return False
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    import math
    if not math.isfinite(value):
        return False

    adjusted = adjust_threshold(rule['threshold'], rule['metric'], field_strength)
    op = rule.get('operator', rule.get('op', ''))
    if op in ('<', 'lt'):   return value < adjusted
    if op in ('>', 'gt'):   return value > adjusted
    if op in ('<=', 'lte'): return value <= adjusted
    if op in ('>=', 'gte'): return value >= adjusted
    if op in ('==', 'eq'):  return abs(value - adjusted) < 1e-9
    return False
