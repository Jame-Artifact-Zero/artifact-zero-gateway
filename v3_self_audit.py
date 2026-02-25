"""
v3_self_audit.py
----------------
E01/E02/E10 — V3 core hardening:
- time collapse (expanded)
- attribution drift stripping (expanded)
- self-audit loop (real scorer integration via adapter)

This module is REPO-AGNOSTIC.
You must pass a callable `v1_score_fn(text)->dict` that returns at least:
- nii_score (float 0..1)
- optionally: tilt_hits, failure_modes, etc.

Self-audit policy:
- max_passes=2
- audit_threshold default 0.85
"""

from typing import Callable, Dict, Any, List, Tuple
import re


# ---------- E10: Attribution drift stripping (expandable) ----------

ATTRIB_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\byou created\b", re.I), "the creation is"),
    (re.compile(r"\byour thoughts were\b", re.I), "the thoughts were"),
    (re.compile(r"\byou assumed\b", re.I), "the assumption is"),
    (re.compile(r"\byou believed\b", re.I), "the belief is"),
    (re.compile(r"\byou failed\b", re.I), "the failure is"),
    (re.compile(r"\byou misunderstood\b", re.I), "the misunderstanding is"),
    (re.compile(r"\byour reasoning\b", re.I), "the reasoning"),
    (re.compile(r"\byou caused\b", re.I), "the cause is"),
    (re.compile(r"\byou decided\b", re.I), "the decision is"),
    (re.compile(r"\byou overlooked\b", re.I), "the oversight is"),
    (re.compile(r"\byou forgot\b", re.I), "the omission is"),
    (re.compile(r"\byou misread\b", re.I), "the misread is"),
    (re.compile(r"\byour approach\b", re.I), "the approach"),
]


def strip_attribution_drift(text: str) -> str:
    out = text
    for rx, repl in ATTRIB_PATTERNS:
        out = rx.sub(repl, out)
    return out


# ---------- E02: Time collapse (expanded rulepack) ----------

TIME_COLLAPSE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bfirst\b[:,]?", re.I), ""),
    (re.compile(r"\bsecond\b[:,]?", re.I), ""),
    (re.compile(r"\bthird\b[:,]?", re.I), ""),
    (re.compile(r"\bnext\b[:,]?", re.I), ""),
    (re.compile(r"\bthen\b[:,]?", re.I), ""),
    (re.compile(r"\bafter that\b[:,]?", re.I), ""),
    (re.compile(r"\bonce we\b", re.I), ""),
    (re.compile(r"\bwe will\b", re.I), ""),
    (re.compile(r"\bwe'll\b", re.I), ""),
    (re.compile(r"\bthe next step is\b", re.I), ""),
    (re.compile(r"\bstep by step\b", re.I), ""),
    (re.compile(r"\blet's walk through\b", re.I), ""),
    (re.compile(r"\bin order to\b", re.I), ""),
    (re.compile(r"\bto summarize\b[:,]?", re.I), ""),
    (re.compile(r"\bin conclusion\b[:,]?", re.I), ""),
    (re.compile(r"\bmoving forward\b[:,]?", re.I), ""),
    (re.compile(r"\bultimately\b[:,]?", re.I), ""),
    (re.compile(r"\bthe reason (is|why)\b", re.I), ""),
    (re.compile(r"\bthis will allow us to\b", re.I), ""),
    (re.compile(r"\bas mentioned\b", re.I), ""),
]


def time_collapse(text: str) -> str:
    out = text
    for rx, repl in TIME_COLLAPSE_PATTERNS:
        out = rx.sub(repl, out)
    # normalize whitespace
    out = re.sub(r"\s{2,}", " ", out).strip()
    # remove list inflation headers
    out = re.sub(r"^\s*(here are|below are|the steps are)\b[:\-]*\s*", "", out, flags=re.I)
    return out


# ---------- E01: Self-audit loop ----------

def run_v3_pipeline(
    output_text: str,
    v1_score_fn: Callable[[str], Dict[str, Any]],
    audit_threshold: float = 0.85,
    max_passes: int = 2
) -> Dict[str, Any]:
    """
    Returns:
      {
        "output": final_text,
        "passes": [
            {"pass":1, "text":..., "score":...},
            {"pass":2, ...}
        ],
        "final_score": ...,
        "self_audit": {"threshold":..., "max_passes":...}
      }
    """

    passes = []
    text = output_text

    for i in range(1, max_passes + 1):
        # deterministic transforms before scoring
        text = strip_attribution_drift(time_collapse(text))

        score = v1_score_fn(text) or {}
        passes.append({"pass": i, "text": text, "score": score})

        nii = float(score.get("nii_score", 1.0))
        # Normalize: if nii > 1, it's 0-100 scale; convert threshold to match
        effective_threshold = audit_threshold * 100 if nii > 1 else audit_threshold

        if nii >= effective_threshold:
            return {
                "output": text,
                "passes": passes,
                "final_score": score,
                "self_audit": {"threshold": audit_threshold, "max_passes": max_passes, "decision": "pass"}
            }

        # corrective transform (deterministic)
        text = _corrective_transform(text)

    # fail closed: return last text + fail decision
    final_score = passes[-1]["score"] if passes else {}
    return {
        "output": text,
        "passes": passes,
        "final_score": final_score,
        "self_audit": {"threshold": audit_threshold, "max_passes": max_passes, "decision": "fail"}
    }


def _corrective_transform(text: str) -> str:
    # Remove hedges + planning noise (deterministic)
    out = text
    out = re.sub(r"\b(maybe|perhaps|possibly|likely|probably)\b", "", out, flags=re.I)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out
