"""Public semantic layer API for core_engine."""

from __future__ import annotations

from typing import Any, Dict

from .common import make_signal_envelope
from .payload import detect_semantic_payloads
from .contextual import detect_contextual_language_functions
from .orientation import detect_orientation
from .patterns import analyze_semantic_patterns
from .score_bridge import bridge_semantic_to_score
from .findings import build_semantic_findings


def detect_semantic_layer(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    raw_text = text or ""
    payload = detect_semantic_payloads(raw_text, input_type=input_type)
    contextual = detect_contextual_language_functions(raw_text, input_type=input_type)
    orientation = detect_orientation(raw_text, input_type=input_type)

    statements = payload.get("detail", {}).get("statements", [])
    patterns = analyze_semantic_patterns(statements)
    bridge = bridge_semantic_to_score(payload, contextual_result=contextual)
    findings = build_semantic_findings(payload, patterns, bridge)

    fired_children = [
        child for child in [payload, contextual, orientation]
        if child.get("fired")
    ]
    evidence = []
    for child in fired_children:
        evidence.extend(child.get("evidence", []))

    strength = 0.0
    if fired_children:
        strength = round(sum(float(child.get("strength", 0.0)) for child in fired_children) / len(fired_children), 3)

    return make_signal_envelope(
        tool="detect_semantic_layer",
        input_type=input_type,
        signal="SEMANTIC_LAYER_DETECTED" if fired_children else "NO_SEMANTIC_LAYER_DETECTED",
        strength=strength,
        evidence=list(dict.fromkeys(evidence))[:25],
        detail={
            "payload": payload,
            "contextual_language_functions": contextual,
            "orientation": orientation,
            "patterns": patterns,
            "score_bridge": bridge,
            "findings": findings,
        },
        fired=bool(fired_children),
    )


def detect_semantic_detection_layers(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    """Backward-compatible alias for the first wired semantic file."""
    return detect_semantic_layer(text, input_type=input_type)


__all__ = [
    "detect_semantic_layer",
    "detect_semantic_detection_layers",
    "detect_semantic_payloads",
    "detect_contextual_language_functions",
    "detect_orientation",
]
