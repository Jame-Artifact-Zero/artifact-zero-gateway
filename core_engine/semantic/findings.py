"""Machine-readable semantic findings."""

from __future__ import annotations

from typing import Any, Dict, List

from . import tags


def build_semantic_findings(
    payload_result: Dict[str, Any],
    pattern_result: Dict[str, Any],
    bridge_result: Dict[str, Any],
) -> Dict[str, Any]:
    counts = payload_result.get("detail", {}).get("counts", {}) if payload_result else {}
    sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    dominant_payload = sorted_counts[0][0] if sorted_counts else None
    secondary_payloads = [tag for tag, count in sorted_counts[1:4] if count > 0]

    relationship_count = sum(int(counts.get(tag, 0)) for tag in tags.RELATIONSHIP_POSITIVE)
    judgment_count = int(counts.get(tags.JUDGMENT, 0))
    affiliation_count = int(counts.get(tags.AFFILIATION, 0))
    judgment_to_affiliation_ratio = None
    if affiliation_count:
        judgment_to_affiliation_ratio = round(judgment_count / affiliation_count, 3)
    elif judgment_count:
        judgment_to_affiliation_ratio = float("inf")

    findings: List[str] = []
    if dominant_payload:
        findings.append(f"DOMINANT_PAYLOAD_{dominant_payload}")
    if judgment_count and relationship_count:
        findings.append("JUDGMENT_WITH_RELATIONSHIP_FRAMING")
    if judgment_count >= max(1, relationship_count * 2):
        findings.append("JUDGMENT_HEAVY_PAYLOAD")
    if pattern_result.get("flagged_transitions"):
        findings.append("SEMANTIC_TRANSITION_PATTERN_PRESENT")
    if bridge_result.get("bridge_count", 0):
        findings.append("SEMANTIC_SCORE_BRIDGE_PRESENT")

    return {
        "dominant_payload": dominant_payload,
        "secondary_payloads": secondary_payloads,
        "relationship_language_present": relationship_count > 0,
        "judgment_to_affiliation_ratio": judgment_to_affiliation_ratio,
        "findings": findings,
    }
