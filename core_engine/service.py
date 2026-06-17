"""Toolbox dispatcher. Receives full pipeline S0 and selects which core_engine tools to run based on task context. Single entry point for all tool invocations from the pipeline."""

from core_engine.app import (
    detect_l0_constraints,
    objective_extract,
    objective_drift,
    detect_l2_framing,
    classify_tilt,
    detect_udds,
    detect_dce,
    detect_cca,
    detect_downstream_before_constraint,
    compute_nii
)


def dispatch(S0: dict) -> dict:
    """Select and run core_engine tools for the pipeline task context."""
    S0 = S0 or {}
    missing = []

    if "Q" not in S0:
        missing.append("Q")
    if "decision" not in S0:
        missing.append("decision")
    if "action" not in S0.get("decision", {}):
        missing.append("decision.action")

    if missing:
        return {
            "error": "missing required S0 keys",
            "detail": missing
        }

    action = S0["decision"].get("action")
    intent = S0.get("signal", {}).get("intent")
    text = S0["Q"]
    result = {}
    tools_run = []

    if action == "respond" and intent == "email":
        l0 = detect_l0_constraints(text)
        result["detect_l0_constraints"] = l0
        tools_run.append("detect_l0_constraints")

        obj = objective_extract(text)
        result["objective_extract"] = obj
        tools_run.append("objective_extract")

        framing = detect_l2_framing(text)
        result["detect_l2_framing"] = framing
        tools_run.append("detect_l2_framing")

        tilt = classify_tilt(text)
        result["classify_tilt"] = tilt
        tools_run.append("classify_tilt")

        udds = detect_udds("", text, l0)
        result["detect_udds"] = udds
        tools_run.append("detect_udds")

    elif action == "score" and intent is not None:
        l0 = detect_l0_constraints(text)
        result["detect_l0_constraints"] = l0
        tools_run.append("detect_l0_constraints")

        obj = objective_extract(text)
        result["objective_extract"] = obj
        tools_run.append("objective_extract")

        drift = objective_drift("", text)
        result["objective_drift"] = drift
        tools_run.append("objective_drift")

        framing = detect_l2_framing(text)
        result["detect_l2_framing"] = framing
        tools_run.append("detect_l2_framing")

        tilt = classify_tilt(text)
        result["classify_tilt"] = tilt
        tools_run.append("classify_tilt")

        udds = detect_udds("", text, l0)
        result["detect_udds"] = udds
        tools_run.append("detect_udds")

        dce = detect_dce(text, l0)
        result["detect_dce"] = dce
        tools_run.append("detect_dce")

        cca = detect_cca("", text)
        result["detect_cca"] = cca
        tools_run.append("detect_cca")

        dbc = detect_downstream_before_constraint("", text, l0)
        result["detect_downstream_before_constraint"] = dbc
        tools_run.append("detect_downstream_before_constraint")

        nii = compute_nii("", text, l0, dbc, tilt)
        result["compute_nii"] = nii
        tools_run.append("compute_nii")

    else:
        l0 = detect_l0_constraints(text)
        result["detect_l0_constraints"] = l0
        tools_run.append("detect_l0_constraints")

        obj = objective_extract(text)
        result["objective_extract"] = obj
        tools_run.append("objective_extract")

        tilt = classify_tilt(text)
        dbc = detect_downstream_before_constraint("", text, l0)
        nii = compute_nii("", text, l0, dbc, tilt)
        result["compute_nii"] = nii
        tools_run.append("compute_nii")

    return {
        "action": action,
        "intent": intent,
        "tools_run": tools_run,
        "result": result
    }
