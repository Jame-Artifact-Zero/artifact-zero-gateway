# banding.py
# Deterministic band classification helpers (band-v1.0)

from __future__ import annotations

from typing import Literal

Band = Literal[str]


def band_structural(structural_score: float) -> Band:
    if structural_score < 0.50:
        return "UNSTABLE"
    if structural_score < 0.75:
        return "PARTIAL"
    if structural_score < 0.90:
        return "STABLE"
    return "OPTIMAL"


def band_relational(edge_index: float) -> Band:
    if edge_index < 0.30:
        return "CALM"
    if edge_index < 0.60:
        return "TENSE"
    if edge_index < 0.80:
        return "ELEVATED"
    return "DESTABILIZING"


def band_execution(execution_score: float) -> Band:
    if execution_score < 0.50:
        return "DRIFTING"
    if execution_score < 0.75:
        return "CONSISTENT"
    if execution_score < 0.90:
        return "DISCIPLINED"
    return "LOCKED"


def band_cost(cost: float) -> Band:
    if cost < 0.002:
        return "LOW_COST"
    if cost < 0.010:
        return "MODERATE_COST"
    if cost < 0.050:
        return "HIGH_COST"
    return "HEAVY_COST"


def band_observability(composite_score: float) -> Band:
    # composite here is "risk composite" (0=stable, 1=critical)
    if composite_score < 0.30:
        return "STABLE"
    if composite_score < 0.60:
        return "WATCH"
    if composite_score < 0.80:
        return "TENSION"
    return "CRITICAL"
