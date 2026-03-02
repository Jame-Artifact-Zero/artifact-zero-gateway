
import re
import unicodedata
from typing import Dict, Any, List

FILLERS = {"actually","basically","literally","just","really","very","totally","honestly","frankly","clearly","obviously"}
HEDGES = {"maybe","probably","perhaps","possibly","kind of","sort of","i think","i guess","might","could"}
MODALS = {"could","should","might","would"}
INTENSIFIERS = {"always","never","everyone","no one","absolutely","completely","guaranteed"}
CONSTRAINT_WORDS = {"must","cannot","required","limited","confirmed","verified","assigned","scope","boundary","deadline"}

def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()

def _tokens(text: str) -> List[str]:
    return re.findall(r"[A-Za-z']+", text.lower())

def analyze_clarity(text: str) -> Dict[str, Any]:
    text = _normalize(text)
    tokens = _tokens(text)
    total = len(tokens) or 1

    filler = sum(1 for t in tokens if t in FILLERS)
    hedge = sum(text.lower().count(h) for h in HEDGES)
    modal = sum(1 for t in tokens if t in MODALS)
    intensifier = sum(1 for t in tokens if t in INTENSIFIERS)
    constraint = sum(1 for t in tokens if t in CONSTRAINT_WORDS)

    filler_density = filler/total
    hedge_density = hedge/total
    modal_ratio = modal/total
    intensifier_density = intensifier/total
    constraint_density = constraint/total
    ambiguity_ratio = hedge/(constraint+1)

    sentences = re.split(r"[.!?]+", text)
    passive = sum(1 for s in sentences if re.search(r"\b(is|was|were|are)\b\s+\w+(ed|en)\b", s))
    passive_ratio = passive / max(len([s for s in sentences if s.strip()]),1)

    avg_sentence_length = total / max(len([s for s in sentences if s.strip()]),1)

    clarity_score = 1.0
    flags = []

    if filler_density > 0.02: clarity_score -= 0.05; flags.append("HIGH_FILLER")
    if hedge_density > 0.02: clarity_score -= 0.10; flags.append("HIGH_HEDGE")
    if passive_ratio > 0.40: clarity_score -= 0.10; flags.append("PASSIVE_OVERUSE")
    if ambiguity_ratio > 1.0: clarity_score -= 0.15; flags.append("AMBIGUITY_HIGH")
    if avg_sentence_length > 28: clarity_score -= 0.05; flags.append("LONG_SENTENCE")

    clarity_score = max(0.0, round(clarity_score,3))

    return {
        "clarity_score": clarity_score,
        "metrics": {
            "filler_density": round(filler_density,4),
            "hedge_density": round(hedge_density,4),
            "passive_ratio": round(passive_ratio,4),
            "modal_ratio": round(modal_ratio,4),
            "intensifier_density": round(intensifier_density,4),
            "constraint_density": round(constraint_density,4),
            "ambiguity_ratio": round(ambiguity_ratio,4),
            "avg_sentence_length": round(avg_sentence_length,2),
        },
        "flags": flags,
        "axis": 3
    }
