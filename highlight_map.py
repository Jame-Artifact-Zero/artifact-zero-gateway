"""highlight_map.py

Deterministic highlight consolidation for Artifact Zero.

Goal:
- Backend owns detection + span positions
- Frontend only renders spans + CSS classes

This module builds a single `highlights` array returned by /nti and /api/v1/score/free.

Public API:
- get_highlights(text, framing=None) -> (axis2_result, highlights_list)
- build_highlights(text, framing, axis2, ...) -> highlights_list
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple


AXIS_PRIORITY = {2: 3, 1: 2, 3: 1, 0: 0}

# Map Axis2 friction categories to stable pattern keys (CSS classes).
AXIS2_CATEGORY_TO_PATTERN = {
    "Direct Blame Construction": "direct_blame",
    "Retroactive Fault Framing": "retroactive_fault",
    "Passive Aggression": "passive_aggression",
    "Tone Escalation": "tone_escalation",
    "Authority / Finality": "authority_finality",
    "Threat / Consequence": "threat_consequence",
    "Character Attack": "character_attack",
    "Shaming / Moralizing": "shaming_moralizing",
    "Gatekeeping / Exclusion": "gatekeeping_exclusion",
    "Dismissal / Invalidating": "dismissal_invalidating",
    "Dominance Posture": "dominance_posture",
    "Escalation Trigger": "escalation_trigger",
}

L2_KEY_TO_PATTERN = {
    "hedge": "hedge",
    "reassurance": "reassurance",
    "category_blend": "category_blend",
}


def _find_marker_spans(text: str, markers: List[str]) -> List[Dict[str, Any]]:
    """Find character-level spans for marker strings in text.

    detect_l2_framing returns marker strings but no positions.
    This generates the spans so highlight_map can work without
    changing detect_l2_framing in app.py.
    """
    lo = text.lower()
    spans = []
    # Longest first to avoid partial overlaps
    for m in sorted(markers, key=len, reverse=True):
        idx = 0
        ml = m.lower()
        while True:
            pos = lo.find(ml, idx)
            if pos == -1:
                break
            spans.append({"start": pos, "end": pos + len(m)})
            idx = pos + len(m)
    return spans


def _dedupe_overlaps(spans: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic overlap resolution.

    Rules:
    - Sort by start asc, then axis priority desc, then length desc
    - Higher axis priority wins overlaps
    - Equal priority: longer span wins
    - Equal both: first encountered wins (stable)
    """
    if not spans:
        return []

    def key(s):
        start = int(s["start"])
        end = int(s["end"])
        axis = int(s.get("axis", 0))
        length = end - start
        return (start, -AXIS_PRIORITY.get(axis, 0), -length)

    spans_sorted = sorted(spans, key=key)
    kept: List[Dict[str, Any]] = []

    def overlaps(a, b) -> bool:
        return not (a["end"] <= b["start"] or b["end"] <= a["start"])

    for s in spans_sorted:
        s_start = int(s["start"])
        s_end = int(s["end"])
        if s_end <= s_start:
            continue

        s_pri = AXIS_PRIORITY.get(int(s.get("axis", 0)), 0)
        s_len = s_end - s_start

        conflict = False
        to_remove = None

        for i, k in enumerate(kept):
            if not overlaps(s, k):
                continue
            k_pri = AXIS_PRIORITY.get(int(k.get("axis", 0)), 0)
            k_len = int(k["end"]) - int(k["start"])

            if s_pri > k_pri or (s_pri == k_pri and s_len > k_len):
                to_remove = i
            conflict = True
            break

        if not conflict:
            kept.append(s)
        elif to_remove is not None:
            kept.pop(to_remove)
            kept.append(s)

    kept.sort(key=lambda x: (int(x["start"]), int(x["end"])))
    return kept


def build_highlights(
    text: str,
    framing: Optional[Dict[str, Any]] = None,
    axis2: Optional[Dict[str, Any]] = None,
    *,
    include_text_snippet: bool = False,
    max_spans: int = 200,
) -> List[Dict[str, Any]]:
    """Build highlight spans from existing detector outputs.

    Inputs:
    - framing: output of detect_l2_framing (marker strings, no spans required)
    - axis2: output of axis2_friction.analyze_friction (triggers with span)
    """
    src = text or ""
    highlights: List[Dict[str, Any]] = []

    # L2 framing (axis 1) — generate spans from marker strings
    if framing and isinstance(framing, dict):
        for markers_key, pattern_name in [
            ("hedge_markers", "hedge"),
            ("reassurance_markers", "reassurance"),
            ("category_blend_markers", "category_blend"),
        ]:
            markers = framing.get(markers_key) or []
            if not markers:
                continue
            for span in _find_marker_spans(src, markers):
                start, end = span["start"], span["end"]
                if start < 0 or end > len(src) or end <= start:
                    continue
                item = {
                    "start": start,
                    "end": end,
                    "pattern": L2_KEY_TO_PATTERN.get(pattern_name, pattern_name),
                    "axis": 1,
                }
                if include_text_snippet:
                    item["text"] = src[start:end]
                highlights.append(item)

    # Axis2 friction triggers (axis 2) — spans already in trigger data
    if axis2 and isinstance(axis2, dict):
        for t in (axis2.get("triggers") or []):
            span = t.get("span") or []
            if not (isinstance(span, (list, tuple)) and len(span) == 2):
                continue
            start, end = int(span[0]), int(span[1])
            if start < 0 or end > len(src) or end <= start:
                continue
            cat = t.get("category") or ""
            pat = AXIS2_CATEGORY_TO_PATTERN.get(
                cat,
                "axis2_" + re.sub(r"[^a-z0-9]+", "_", cat.lower()).strip("_") if cat else "axis2_trigger"
            )
            item = {
                "start": start,
                "end": end,
                "pattern": pat,
                "axis": 2,
                "pattern_id": t.get("pattern"),
                "category": cat,
                "weight": t.get("weight"),
            }
            if include_text_snippet:
                item["text"] = src[start:end]
            highlights.append(item)

    highlights = _dedupe_overlaps(highlights)

    if max_spans and len(highlights) > max_spans:
        highlights = highlights[:max_spans]

    return highlights


def get_highlights(text: str, framing: Optional[Dict[str, Any]] = None):
    """One-call helper for endpoints. Returns (axis2_result, highlights_list).

    Each endpoint calls this instead of duplicating the axis2 + highlight block.
    framing should be passed in from the caller since detect_l2_framing lives in app.py.
    """
    axis2 = None
    try:
        from axis2_friction import analyze_friction
        axis2 = analyze_friction(text)
    except Exception:
        pass

    try:
        highlights = build_highlights(text, framing=framing, axis2=axis2, include_text_snippet=False)
    except Exception:
        highlights = []

    return axis2, highlights
