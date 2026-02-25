"""
nti_full_integration_stub.py
----------------------------
Example aggregator for /nti-full without touching Axis 1 core.
You will adapt this to your repo.

Assumes you already have:
  axis1 = nti_full_axis1(payload)  # your existing function

This stub shows how to add:
  axis2, loop, consolidation, confusion, time_object
"""

from typing import Dict, Any, List
from axis2_adapter import compute_axis2
from loop_engine import detect_silent_loop
from consolidation_engine import consolidate
from confusion_layer import analyze as confusion_analyze
from time_object import make_time_object


def build_full(payload: Dict[str, Any], axis1: Dict[str, Any], build_version: str) -> Dict[str, Any]:
    text = (payload.get("text") or payload.get("input") or payload.get("message") or "").strip()

    axis2 = compute_axis2(text)
    loop = detect_silent_loop(text)
    confusion = confusion_analyze(text)

    # If you pass options, consolidate them
    options = payload.get("options") or []
    consolidation = consolidate(options) if isinstance(options, list) else {"merged": False, "similarity": 0.0}

    t_obj = make_time_object(build_version=build_version, request_id=payload.get("request_id"), tz=payload.get("tz"))

    return {
        "axis1": axis1,
        "axis2": axis2,
        "loop": loop,
        "consolidation": consolidation,
        "confusion": confusion,
        "time_object": t_obj
    }
