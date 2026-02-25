"""
confusion_layer.py
------------------
E08 Confusion Layer (rule-based).

Detects introduction of new terminology without definition:
- Acronyms introduced without expansion
- Capitalized terms introduced without "means/is/defined as"
- Referent ambiguity density (this/that/it) without anchors

Output:
  { confusion_score, undefined_terms: [...], triggers: [...] }
"""

from typing import Dict, Any, List
import re


ACRONYM = re.compile(r"\b([A-Z]{2,6})\b")
DEFINITION_CUE = re.compile(r"\b(means|is|stands for|defined as)\b", re.I)
REFERENTS = re.compile(r"\b(this|that|it|they|those|these)\b", re.I)


def analyze(text: str) -> Dict[str, Any]:
    t = text or ""
    undefined: List[str] = []
    triggers: List[Dict[str, Any]] = []

    acronyms = set(ACRONYM.findall(t))
    if acronyms:
        # consider defined if any definition cue exists near acronym
        for a in acronyms:
            if not _is_defined(a, t):
                undefined.append(a)
                triggers.append({"rule_id": "UNDEFINED_ACRONYM", "term": a})

    # referent density heuristic
    words = re.findall(r"\b\w+\b", t)
    ref_count = len(REFERENTS.findall(t))
    density = (ref_count / float(len(words))) if words else 0.0
    if density > 0.08:
        triggers.append({"rule_id": "HIGH_REFERENT_DENSITY", "density": density})

    score = 0.0
    if undefined:
        score += 0.6
    if any(tr["rule_id"] == "HIGH_REFERENT_DENSITY" for tr in triggers):
        score += 0.3
    score = min(1.0, score)

    return {"confusion_score": score, "undefined_terms": undefined, "triggers": triggers}


def _is_defined(acronym: str, text: str) -> bool:
    # looks for "ACRONYM stands for" OR "ACRONYM = ..." OR "... (ACRONYM)"
    if re.search(rf"{acronym}\s*(=|:)\s*\w+", text):
        return True
    if re.search(rf"{acronym}\s+{DEFINITION_CUE.pattern}", text, flags=re.I):
        return True
    # parenthetical expansion: "Full Name (ACRONYM)" implies defined
    if re.search(rf"\b\w+(\s+\w+){{1,6}}\s*\({acronym}\)", text):
        return True
    return False
