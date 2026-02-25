"""
consolidation_engine.py
-----------------------
E07 Consolidation Engine (rule-based).

Detects A/B/C option overlap and auto-merges when similarity > threshold.
Similarity heuristic: token overlap + structural marker overlap.

Input:
  options: list[str]

Output:
  {
    merged: bool,
    similarity: 0..1,
    merged_text: str,
    kept_deltas: [str]
  }
"""

from typing import List, Dict, Any
import re


def consolidate(options: List[str], threshold: float = 0.80) -> Dict[str, Any]:
    opts = [o.strip() for o in (options or []) if (o or "").strip()]
    if len(opts) < 2:
        return {"merged": False, "similarity": 0.0, "merged_text": opts[0] if opts else "", "kept_deltas": []}

    token_sets = [_tokens(o) for o in opts]
    base = token_sets[0]
    overlaps = []
    for s in token_sets[1:]:
        overlaps.append(_jaccard(base, s))

    sim = min(overlaps) if overlaps else 0.0

    if sim >= threshold:
        merged = _merge(opts)
        deltas = _deltas(opts)
        return {"merged": True, "similarity": sim, "merged_text": merged, "kept_deltas": deltas}

    return {"merged": False, "similarity": sim, "merged_text": "", "kept_deltas": []}


def _tokens(text: str) -> set:
    words = re.findall(r"[A-Za-z0-9_]+", text.lower())
    stop = {"the","a","an","and","or","to","of","in","on","for","with","is","are","be","we","you","it","this","that"}
    return set([w for w in words if w not in stop])


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _merge(opts: List[str]) -> str:
    # Deterministic merge: keep first, append unique sentences from others.
    seen = set()
    out_sentences: List[str] = []
    for o in opts:
        for s in re.split(r"(?<=[.!?])\s+", o):
            s2 = s.strip()
            if not s2:
                continue
            key = s2.lower()
            if key in seen:
                continue
            seen.add(key)
            out_sentences.append(s2)
    return " ".join(out_sentences).strip()


def _deltas(opts: List[str]) -> List[str]:
    # Capture sentences that appear only in later options
    base_sents = set([s.strip().lower() for s in re.split(r"(?<=[.!?])\s+", opts[0]) if s.strip()])
    deltas = []
    for o in opts[1:]:
        for s in re.split(r"(?<=[.!?])\s+", o):
            s2 = s.strip()
            if s2 and s2.lower() not in base_sents:
                deltas.append(s2)
    return deltas
