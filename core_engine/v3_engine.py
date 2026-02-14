import re
from typing import Any, Dict, Optional, Tuple, List

HEDGE_WORDS = ["maybe", "likely", "possibly", "kind of", "sort of"]

FILLER_PHRASES = [
    "it is important to note",
    "in conclusion",
    "ultimately",
    "to summarize",
]

def _normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text

def _remove_phrases(text: str, phrases: List[str]) -> Tuple[str, int]:
    lower = text.lower()
    removed = 0
    for p in phrases:
        if p in lower:
            # simple deterministic removal (case-insensitive)
            pattern = re.compile(re.escape(p), re.IGNORECASE)
            new_text, n = pattern.subn("", text)
            if n > 0:
                removed += n
                text = new_text
                lower = text.lower()
    # normalize spacing after removals
    text = _normalize(text)
    return text, removed

def _dedupe_sentences(text: str) -> str:
    # Deterministic exact de-dupe.
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    seen = set()
    out = []
    for s in parts:
        key = s
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return " ".join(out).strip()

def _objective_filter(text: str, objective: str) -> str:
    # Strict string-match objective anchoring (optional).
    obj = (objective or "").strip().lower()
    if not obj:
        return text
    obj_words = [w for w in re.findall(r"[a-z0-9']+", obj) if len(w) > 3]
    if not obj_words:
        return text

    sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    kept = []
    for s in sentences:
        sl = s.lower()
        if any(w in sl for w in obj_words):
            kept.append(s)
    # If we filtered everything, keep original (fail-safe).
    return " ".join(kept).strip() if kept else text

def run_v3(text: str, max_tokens: int = 400, objective: Optional[str] = None) -> Dict[str, Any]:
    """
    V3 = deterministic stabilization (no rewriting, removal/compression only).
    """
    raw = text or ""
    t = _normalize(raw)

    # Remove hedges
    hedges_removed = 0
    for w in HEDGE_WORDS:
        pattern = re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)
        t, n = pattern.subn("", t)
        if n > 0:
            hedges_removed += n
            t = _normalize(t)

    # Remove filler phrases
    t, filler_removed = _remove_phrases(t, FILLER_PHRASES)

    # Dedupe sentences
    t = _dedupe_sentences(t)

    # Optional objective anchoring
    if objective:
        t = _objective_filter(t, objective)

    # Token ceiling (word count proxy)
    words = t.split()
    trimmed = False
    if len(words) > max_tokens:
        t = " ".join(words[:max_tokens])
        trimmed = True

    t = _normalize(t)

    return {
        "stabilized_text": t,
        "hedges_removed": hedges_removed,
        "filler_removed": filler_removed,
        "trimmed": trimmed,
        "max_tokens": max_tokens
    }
