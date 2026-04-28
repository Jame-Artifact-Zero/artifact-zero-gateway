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
    "min_gap",
    "run_width",
    "b_alg_b_joint",
    "gap",
}

# Metrics that are field-strength invariant (normalized ratios)
FRACTION_METRICS = {
    "peak_asym",
    "compression_pct",
    "std_fraction",
    "pct_left",
    "peak_disagree",
    "enhancement_delta",
    "b_pg_center_edge",
}

# Reference field strength — all thresholds in az_impression_rules
# are calibrated at this value.
REFERENCE_FIELD_STRENGTH = 1.5


def adjust_threshold(
    base_threshold: float,
    metric: str,
    field_strength: float,
) -> float:
    """
    Adjust a rule threshold for the field strength of the current study.

    Args:
        base_threshold: The threshold value as stored in az_impression_rules.
                        Calibrated at REFERENCE_FIELD_STRENGTH (1.5T).
        metric:         The metric name (e.g. 'min_gap', 'peak_asym').
        field_strength: The field strength of the current study in Tesla,
                        from result['field_strength']. Defaults to 1.5
                        if not present.

    Returns:
        adjusted_threshold: float. For gap metrics, scaled by
                            field_strength / REFERENCE_FIELD_STRENGTH.
                            For fraction metrics, returned unchanged.

    Examples:
        adjust_threshold(200, 'min_gap', 1.5)  -> 200.0   (no change at ref)
        adjust_threshold(200, 'min_gap', 3.0)  -> 400.0   (doubled at 3T)
        adjust_threshold(200, 'min_gap', 1.0)  -> 133.3   (reduced at 1T)
        adjust_threshold(0.25, 'peak_asym', 3.0) -> 0.25  (unchanged)
        adjust_threshold(200, 'min_gap', None) -> 200.0   (safe default)
    """
    # Safe default if field strength missing or zero
    if not field_strength or field_strength <= 0:
        field_strength = REFERENCE_FIELD_STRENGTH

    # Fraction metrics are invariant — return unchanged
    if metric in FRACTION_METRICS:
        return float(base_threshold)

    # Gap metrics scale linearly with field strength
    if metric in GAP_METRICS:
        multiplier = field_strength / REFERENCE_FIELD_STRENGTH
        return float(base_threshold) * multiplier

    # Unknown metric — return unchanged with no error
    # Allows new metrics to be added to the rules table without
    # requiring a code change here first.
    return float(base_threshold)


def get_field_strength(result: dict, default: float = 1.5) -> float:
    """
    Safely extract field strength from a result dict.

    Args:
        result:  The sequence result dict from the decomposition pipeline.
        default: Value to return if field_strength is missing or invalid.

    Returns:
        field_strength as float.
    """
    fs = result.get("field_strength", default)
    try:
        fs = float(fs)
        return fs if fs > 0 else default
    except (TypeError, ValueError):
        return default
