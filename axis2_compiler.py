
import re
import unicodedata
from typing import Dict, List, Any, Tuple

from axis2_friction import analyze_friction

HOLD_THRESHOLD = 0.55

INTENSIFIERS = {
    "obviously","clearly","literally","actually","just","really","totally",
    "completely","absolutely","always","never","everyone","no one","nobody",
    "ridiculous","insane","crazy"
}

BLAME_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("T-BLAME-WRONG", re.compile(r"\b(you)\s+(were|are)\s+wrong\b", re.IGNORECASE)),
    ("T-BLAME-DIDNT", re.compile(r"\b(you)\s+(did not|didn't)\b", re.IGNORECASE)),
    ("T-BLAME-DONT", re.compile(r"\b(you)\s+(do not|don't)\b", re.IGNORECASE)),
    ("T-BLAME-KEEP", re.compile(r"\b(you)\s+(keep|kept)\b", re.IGNORECASE)),
]

TEMPLATES = {
    "T-BLAME-WRONG": "Earlier assumption was incorrect.",
    "T-BLAME-DIDNT": "That step was not completed.",
    "T-BLAME-DONT": "That condition is not currently met.",
    "T-BLAME-KEEP": "That pattern is recurring.",
}

def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()

def _strip_intensifiers(text: str):
    transforms = []
    for word in INTENSIFIERS:
        pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
        if pattern.search(text):
            text = pattern.sub("", text)
            transforms.append({"id": "T-INTENSIFIER", "detail": word})
    return re.sub(r"\s{2,}", " ", text).strip(), transforms

def _neutralize_blame(text: str):
    transforms = []
    for tid, pat in BLAME_PATTERNS:
        if pat.search(text):
            text = pat.sub(TEMPLATES[tid], text)
            transforms.append({"id": tid, "detail": "neutralized"})
    return re.sub(r"\s{2,}", " ", text).strip(), transforms

def compile_planned(text: str) -> Dict[str, Any]:
    original = _norm(text)
    pre = analyze_friction(original)
    candidate, t1 = _strip_intensifiers(original)
    candidate, t2 = _neutralize_blame(candidate)
    post = analyze_friction(candidate)
    delta = post["friction_score"] - pre["friction_score"]
    accepted = delta <= 0 and pre["friction_score"] >= HOLD_THRESHOLD
    return {
        "original": original,
        "compiled": candidate if accepted else original,
        "pre": pre,
        "post": post,
        "delta": round(delta,4),
        "accepted": accepted,
        "transforms": t1 + t2,
        "axis": 2
    }
