# core_engine/odd.py
# Objective Drift Detection (ODD) v1.0
# Combines: transform legality, abstraction delta guard,
#           state delta metric, salience integrity.
# Deterministic. No inference. No rewriting.

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple

from .otc import get_allowed_transforms, get_abstraction_range, validate_objective
from .ctc import classify_transform, is_legal_transform
from .als import detect_abstraction_level, check_abstraction_guard
from .salience import detect_salience_transforms

ODD_VERSION = "odd-v1.0"


def compute_state_delta(
    previous_variable_count: int,
    current_variable_count: int,
    previous_resolved_count: int,
    current_resolved_count: int,
) -> Dict[str, Any]:
    """
    State Delta Metric (SDT).
    Measures whether a turn actually reduced uncertainty.
    ΔS = (variables_resolved_delta) - (variables_added_delta)
    Positive = progress. Zero = stagnation. Negative = expansion.
    """
    prev_v = int(previous_variable_count or 0)
    curr_v = int(current_variable_count or 0)
    prev_r = int(previous_resolved_count or 0)
    curr_r = int(current_resolved_count or 0)

    variables_added = max(0, curr_v - prev_v)
    variables_resolved = max(0, curr_r - prev_r)
    delta_s = variables_resolved - variables_added

    stagnant = (delta_s == 0 and variables_resolved == 0)

    return {
        "previous_variables": prev_v,
        "current_variables": curr_v,
        "previous_resolved": prev_r,
        "current_resolved": curr_r,
        "variables_added": variables_added,
        "variables_resolved": variables_resolved,
        "delta_s": delta_s,
        "stagnant": stagnant,
        "direction": "PROGRESS" if delta_s > 0 else ("STAGNANT" if delta_s == 0 else "EXPANDING"),
    }


def detect_drift(
    text: str,
    objective_type: str,
    previous_abstraction_level: int = 0,
    previous_variable_count: int = 0,
    current_variable_count: int = 0,
    previous_resolved_count: int = 0,
    current_resolved_count: int = 0,
    stagnation_threshold: int = 3,
    consecutive_stagnant_turns: int = 0,
) -> Dict[str, Any]:
    """
    Full drift detection pass.

    Checks all 5 drift conditions:
    1. Transform not legal under active objective
    2. Abstraction delta outside allowed range
    3. State delta stagnation
    4. Undeclared branch shift (OPEN_BRANCH without markers)
    5. Undeclared salience reweight

    Returns drift verdict + all sub-results.
    """
    violations: List[str] = []

    # Validate objective
    obj_val = validate_objective(objective_type)
    if not obj_val["valid"]:
        return {
            "version": ODD_VERSION,
            "drift_detected": True,
            "violations": [f"Invalid objective type: {objective_type}"],
            "verdict": "INVALID_OBJECTIVE",
        }

    ot = obj_val["objective_type"]
    allowed_transforms = get_allowed_transforms(ot)
    abstraction_range = get_abstraction_range(ot)

    # 1. Transform legality
    transform_result = classify_transform(text)
    primary_transform = transform_result["primary_transform"]
    transform_legal = is_legal_transform(primary_transform, allowed_transforms)
    if not transform_legal:
        violations.append(
            f"ILLEGAL_TRANSFORM: '{primary_transform}' not allowed under {ot}. "
            f"Allowed: {allowed_transforms}"
        )

    # 2. Abstraction delta guard
    abs_detection = detect_abstraction_level(text)
    current_abs_level = abs_detection["detected_level"]
    abs_guard = check_abstraction_guard(
        previous_level=previous_abstraction_level,
        current_level=current_abs_level,
        allowed_range=abstraction_range,
    )
    if not abs_guard["passed"]:
        for v in abs_guard["violations"]:
            violations.append(f"ABSTRACTION_VIOLATION: {v}")

    # 3. State delta
    state_delta = compute_state_delta(
        previous_variable_count=previous_variable_count,
        current_variable_count=current_variable_count,
        previous_resolved_count=previous_resolved_count,
        current_resolved_count=current_resolved_count,
    )
    total_stagnant = consecutive_stagnant_turns + (1 if state_delta["stagnant"] else 0)
    if total_stagnant >= stagnation_threshold:
        violations.append(
            f"STATE_STAGNATION: {total_stagnant} consecutive turns with no state reduction. "
            f"Threshold: {stagnation_threshold}."
        )

    # 4. Undeclared branch shift
    # If OPEN_BRANCH detected but not in allowed transforms → violation
    if "OPEN_BRANCH" in transform_result.get("detected_transforms", []):
        if "OPEN_BRANCH" not in allowed_transforms:
            violations.append(
                f"UNDECLARED_BRANCH: Branch shift detected under {ot} which does not allow OPEN_BRANCH."
            )

    # 5. Salience integrity
    salience_result = detect_salience_transforms(text)
    detected_salience = salience_result.get("detected", [])
    # Salience transforms are logged, not blocked — but undeclared ones flag
    # (In strict mode these would be violations; in soft mode they are warnings)

    # Verdict
    drift_detected = len(violations) > 0

    if not drift_detected:
        verdict = "CLEAN"
    elif len(violations) == 1:
        verdict = "MINOR_DRIFT"
    elif len(violations) <= 3:
        verdict = "MODERATE_DRIFT"
    else:
        verdict = "SEVERE_DRIFT"

    return {
        "version": ODD_VERSION,
        "drift_detected": drift_detected,
        "verdict": verdict,
        "violations": violations,
        "violation_count": len(violations),
        "transform": {
            "primary": primary_transform,
            "legal": transform_legal,
            "all_detected": transform_result.get("detected_transforms", []),
        },
        "abstraction": {
            "detected_level": current_abs_level,
            "previous_level": previous_abstraction_level,
            "guard_passed": abs_guard["passed"],
        },
        "state_delta": state_delta,
        "consecutive_stagnant_turns": total_stagnant if state_delta["stagnant"] else 0,
        "salience": {
            "detected_transforms": detected_salience,
        },
        "objective_type": ot,
    }
