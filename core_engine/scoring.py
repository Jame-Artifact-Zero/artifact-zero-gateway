"""core_engine/scoring.py — Scoring Engine

Takes DetectionMap output from detection.py and produces scores.
Multiple lenses on the same detection data.
Does not detect. Does not store.

Public API:
- score_nii(detection_map) -> Dict  (NTI Integrity Index)
- score_nti(detection_map) -> Dict  (NTI composite)
- score_csi(detection_map) -> Dict  (Corporate Structural Integrity)
- score_hcs(detection_map) -> Dict  (Human Communication Score)
- score_composite(detection_map) -> Dict  (all lenses combined)

- score_paragraphs(paragraph_maps) -> Dict  (paragraph → page rollup)
"""

from __future__ import annotations
from typing import Any, Dict, List


# ── NII (NTI Integrity Index) ──
# Measures: how faithfully does the system preserve constraints?

def score_nii(det: Dict[str, Any]) -> Dict[str, Any]:
    """NII scoring from detection map."""
    l0 = det.get("l0_constraints", [])
    tilt = det.get("tilt_taxonomy", [])
    fm = det.get("failure_modes", {})
    downstream = det.get("downstream_before_constraints", False)

    # Q1: Are constraints present and explicit?
    q1 = 1.0 if len(l0) >= 1 else 0.0

    # Q2: Are constraints declared before capability claims?
    q2 = 0.0 if downstream else 1.0

    # Q3: Boundary integrity — penalize failure modes + structural drift tilts
    structural_tilts = {"T2_CERTAINTY_INFLATION", "T4_CAPABILITY_OVERREACH",
                        "T5_ABSOLUTE_LANGUAGE", "T9_SCOPE_EXPANSION", "T10_AUTHORITY_IMPOSITION"}
    drift_count = len([t for t in tilt if t in structural_tilts])
    active_fm = len(det.get("active_failures", []))
    q3_penalty = min((drift_count * 0.15) + (active_fm * 0.25), 1.0)
    q3 = max(1.0 - q3_penalty, 0.0)

    nii_score = round((q1 + q2 + q3) / 3.0, 3)

    return {
        "nii_score": nii_score,
        "q1_constraints_explicit": q1,
        "q2_constraints_before_capability": q2,
        "q3_boundary_integrity": round(q3, 3),
        "structural_tilt_count": drift_count,
        "active_failure_count": active_fm,
    }


# ── NTI Score ──
# Composite: tilt load + failure mode severity + framing noise

def score_nti(det: Dict[str, Any]) -> Dict[str, Any]:
    """NTI composite score (0-100). Higher = cleaner."""
    tilt = det.get("tilt_taxonomy", [])
    framing = det.get("framing", {})
    active_fm = len(det.get("active_failures", []))
    word_count = det.get("word_count", 1)

    # Start at 100, deduct
    score = 100.0

    # Tilt deductions: each tilt category costs 5 points
    score -= len(tilt) * 5.0

    # Failure mode deductions: CONFIRMED = 10, PROBABLE = 5
    fm = det.get("failure_modes", {})
    for key in ["UDDS", "DCE", "CCA"]:
        state = fm.get(key, {}).get(f"{key.lower()}_state", "")
        if "CONFIRMED" in state:
            score -= 10.0
        elif "PROBABLE" in state:
            score -= 5.0

    # Framing noise deduction: hedges and reassurances
    hedge_count = framing.get("hedge_count", 0)
    reassurance_count = framing.get("reassurance_count", 0)
    score -= hedge_count * 2.0
    score -= reassurance_count * 1.5

    # Signal density bonus/penalty
    density = det.get("signal_density", 0)
    if density > 5.0:
        score -= (density - 5.0) * 2.0

    score = max(0.0, min(100.0, round(score, 1)))

    return {
        "nti_score": score,
        "tilt_count": len(tilt),
        "failure_mode_deductions": active_fm,
        "hedge_deductions": hedge_count,
        "signal_density": density,
    }


# ── CSI (Corporate Structural Integrity) ──
# 10-dimension corporate score

def score_csi(det: Dict[str, Any]) -> Dict[str, Any]:
    """CSI scoring — 10 dimensions, 0-100 each, composite average."""
    text = det.get("text", "")
    word_count = det.get("word_count", 1)
    tilt = det.get("tilt_taxonomy", [])
    framing = det.get("framing", {})
    l0 = det.get("l0_constraints", [])
    fm = det.get("failure_modes", {})

    dimensions = {}

    # D1: Constraint Presence (are commitments bounded?)
    dimensions["constraint_presence"] = min(100, len(l0) * 25)

    # D2: Hedge Density (lower = better)
    hedge_ratio = framing.get("hedge_count", 0) / max(word_count / 50, 1)
    dimensions["hedge_control"] = max(0, round(100 - hedge_ratio * 30, 1))

    # D3: Tilt Load (fewer tilt categories = better)
    dimensions["tilt_load"] = max(0, round(100 - len(tilt) * 12, 1))

    # D4: Failure Mode Risk
    active = len(det.get("active_failures", []))
    dimensions["failure_mode_risk"] = max(0, round(100 - active * 25, 1))

    # D5: Certainty Calibration
    cert_hit = 1 if "T2_CERTAINTY_INFLATION" in tilt else 0
    abs_hit = 1 if "T5_ABSOLUTE_LANGUAGE" in tilt else 0
    dimensions["certainty_calibration"] = max(0, round(100 - (cert_hit + abs_hit) * 25, 1))

    # D6: Authority Balance
    auth_hit = 1 if "T10_AUTHORITY_IMPOSITION" in tilt else 0
    dimensions["authority_balance"] = max(0, 100 - auth_hit * 35)

    # D7: Scope Discipline
    scope_hit = 1 if "T9_SCOPE_EXPANSION" in tilt else 0
    cap_hit = 1 if "T4_CAPABILITY_OVERREACH" in tilt else 0
    dimensions["scope_discipline"] = max(0, 100 - (scope_hit + cap_hit) * 25)

    # D8: Accountability Presence
    acc_hit = 1 if "T3_ACCOUNTABILITY_DISPLACEMENT" in tilt else 0
    dimensions["accountability"] = max(0, 100 - acc_hit * 40)

    # D9: Emotional Framing
    emo_hit = 1 if "T7_EMOTIONAL_FRAMING" in tilt else 0
    dimensions["emotional_control"] = max(0, 100 - emo_hit * 30)

    # D10: Social Proof Dependency
    sp_hit = 1 if "T8_SOCIAL_PROOF_PRESSURE" in tilt else 0
    dimensions["social_proof_independence"] = max(0, 100 - sp_hit * 30)

    # Composite: weighted average
    weights = {
        "constraint_presence": 1.5,
        "hedge_control": 1.0,
        "tilt_load": 1.2,
        "failure_mode_risk": 1.5,
        "certainty_calibration": 1.0,
        "authority_balance": 0.8,
        "scope_discipline": 1.0,
        "accountability": 1.0,
        "emotional_control": 0.7,
        "social_proof_independence": 0.7,
    }

    total_weight = sum(weights.values())
    weighted_sum = sum(dimensions[k] * weights[k] for k in dimensions)
    composite = round(weighted_sum / total_weight, 1)

    return {
        "csi_score": composite,
        "dimensions": dimensions,
        "dimension_count": len(dimensions),
    }


# ── HCS (Human Communication Score) ──
# 5 lenses for human-to-human communication quality

def score_hcs(det: Dict[str, Any]) -> Dict[str, Any]:
    """HCS scoring — 5 lenses."""
    tilt = det.get("tilt_taxonomy", [])
    framing = det.get("framing", {})
    fm = det.get("failure_modes", {})

    lenses = {}

    # Lens 1: Clarity (hedges + vague quantification hurt)
    hedge_penalty = framing.get("hedge_count", 0) * 8
    vague_hit = 1 if "T6_VAGUE_QUANTIFICATION" in tilt else 0
    lenses["clarity"] = max(0, round(100 - hedge_penalty - vague_hit * 15, 1))

    # Lens 2: Respect (dominance, authority, blame hurt)
    respect_tilts = {"T10_AUTHORITY_IMPOSITION", "T3_ACCOUNTABILITY_DISPLACEMENT"}
    respect_hits = len([t for t in tilt if t in respect_tilts])
    lenses["respect"] = max(0, round(100 - respect_hits * 20, 1))

    # Lens 3: Honesty (certainty inflation + absolute language hurt)
    honesty_tilts = {"T2_CERTAINTY_INFLATION", "T5_ABSOLUTE_LANGUAGE", "T4_CAPABILITY_OVERREACH"}
    honesty_hits = len([t for t in tilt if t in honesty_tilts])
    lenses["honesty"] = max(0, round(100 - honesty_hits * 18, 1))

    # Lens 4: Commitment Integrity (failure modes hurt)
    active = len(det.get("active_failures", []))
    lenses["commitment_integrity"] = max(0, round(100 - active * 20, 1))

    # Lens 5: Emotional Regulation (urgency + emotional framing hurt)
    emo_tilts = {"T1_URGENCY_ESCALATION", "T7_EMOTIONAL_FRAMING", "T8_SOCIAL_PROOF_PRESSURE"}
    emo_hits = len([t for t in tilt if t in emo_tilts])
    lenses["emotional_regulation"] = max(0, round(100 - emo_hits * 15, 1))

    composite = round(sum(lenses.values()) / len(lenses), 1)

    return {
        "hcs_score": composite,
        "lenses": lenses,
    }


# ── Composite Score ──

def score_composite(det: Dict[str, Any]) -> Dict[str, Any]:
    """All scoring lenses applied to one detection map."""
    nii = score_nii(det)
    nti = score_nti(det)
    csi = score_csi(det)
    hcs = score_hcs(det)

    return {
        "nii": nii,
        "nti": nti,
        "csi": csi,
        "hcs": hcs,
    }


# ── Paragraph → Page Rollup ──

def score_paragraphs(paragraph_maps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score each paragraph, produce paragraph-level and page-level scores."""
    paragraph_scores = []
    for det in paragraph_maps:
        scores = score_composite(det)
        scores["paragraph_index"] = det.get("paragraph_index", 0)
        scores["word_count"] = det.get("word_count", 0)
        paragraph_scores.append(scores)

    if not paragraph_scores:
        return {"paragraph_scores": [], "page_score": {}}

    # Page-level: weighted average by word count
    total_words = sum(p.get("word_count", 1) for p in paragraph_scores)
    if total_words == 0:
        total_words = 1

    def weighted_avg(key_path):
        total = 0
        for ps in paragraph_scores:
            wc = ps.get("word_count", 1)
            val = ps
            for k in key_path:
                val = val.get(k, {}) if isinstance(val, dict) else 0
            if isinstance(val, (int, float)):
                total += val * wc
        return round(total / total_words, 1)

    page_score = {
        "nii_score": weighted_avg(["nii", "nii_score"]),
        "nti_score": weighted_avg(["nti", "nti_score"]),
        "csi_score": weighted_avg(["csi", "csi_score"]),
        "hcs_score": weighted_avg(["hcs", "hcs_score"]),
        "paragraph_count": len(paragraph_scores),
        "total_words": total_words,
    }

    return {
        "paragraph_scores": paragraph_scores,
        "page_score": page_score,
    }
