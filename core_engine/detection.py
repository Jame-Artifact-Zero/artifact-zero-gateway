"""core_engine/detection.py — Detection Engine

Finds signals. Returns positions and types. Does not score.
Deterministic. No LLM. No inference.

Public API:
- detect_all(text, prompt="", answer="") -> DetectionMap
  Returns everything found: constraints, tilt, failure modes, framing, drift, signals.

- detect_paragraphs(text, prompt="", answer="") -> List[DetectionMap]
  Splits text into paragraphs, runs detect_all on each.
"""

from __future__ import annotations
from typing import Any, Dict, List, Tuple
import re

# ── Utilities ──

WORD_RE = re.compile(r"[A-Za-z0-9']+")
STOPWORDS = frozenset([
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "this", "that", "these",
    "those", "and", "but", "or", "not", "for", "with", "from", "into",
    "than", "then", "also", "just", "about", "above", "below", "between",
    "each", "every", "some", "any", "more", "most", "other", "such",
    "what", "which", "who", "whom", "when", "where", "how", "all",
    "both", "few", "many", "much", "own", "same", "very", "your",
    "they", "them", "their", "its", "our", "his", "her", "here", "there",
    "only", "well", "still", "even", "back", "after", "before", "over",
    "through", "again", "like", "make", "made", "know", "need", "want",
])


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in WORD_RE.findall(text or "")]


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_sentences(text: str) -> List[str]:
    t = normalize_space(text)
    if not t:
        return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if p.strip()]


def split_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs by double newline or significant whitespace."""
    paras = re.split(r"\n\s*\n|\r\n\s*\r\n", text or "")
    return [p.strip() for p in paras if p.strip() and len(p.strip()) > 20]


def jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return round(len(sa & sb) / len(sa | sb), 3)


def extract_domain_tokens(text: str) -> List[str]:
    toks = tokenize(text)
    dom = []
    for t in toks:
        if len(t) >= 4 and t not in STOPWORDS:
            dom.append(t)
    uniq = []
    for x in dom:
        if x not in uniq:
            uniq.append(x)
    return uniq[:80]


def _contains_any(text_lc: str, needles: List[str]) -> bool:
    for n in needles:
        if n in text_lc:
            return True
    return False


# ── L0 Constraint Detection ──

L0_CONSTRAINT_MARKERS = [
    "must", "cannot", "can't", "won't", "requires", "require", "only if",
    "no way", "not possible", "dependency", "dependent", "api key", "legal",
    "policy", "security", "compliance", "budget", "deadline", "production",
    "cannot expose", "secret", "token", "rate limit", "auth",
]


def detect_l0_constraints(text: str) -> List[str]:
    t = (text or "").lower()
    found = []
    for m in L0_CONSTRAINT_MARKERS:
        if m in t:
            found.append(m)
    return found


# ── L2 Framing Detection ──

L2_HEDGE = [
    "maybe", "might", "could", "perhaps", "it seems", "it sounds",
    "generally", "often", "usually", "in general", "likely",
    "approximately", "around",
]
L2_REASSURE = [
    "don't worry", "no problem", "it's okay", "you got this",
    "rest assured", "glad", "happy to",
]
L2_CATEGORY_BLEND = [
    "sort of", "kind of", "a bit like", "somewhat", "partly",
]


def detect_l2_framing(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    hedges = [h for h in L2_HEDGE if h in t]
    reassurances = [r for r in L2_REASSURE if r in t]
    blends = [b for b in L2_CATEGORY_BLEND if b in t]
    return {
        "hedge": hedges,
        "reassurance": reassurances,
        "category_blend": blends,
        "hedge_count": len(hedges),
        "reassurance_count": len(reassurances),
        "blend_count": len(blends),
    }


# ── Tilt Taxonomy ──

TILT_TAXONOMY = {
    "T1_URGENCY_ESCALATION": [
        "asap", "right now", "immediately", "urgent", "hurry", "rush",
        "time is running out", "before it's too late", "drop everything",
        "this can't wait", "top priority",
    ],
    "T3_ACCOUNTABILITY_DISPLACEMENT": [
        "it's not my fault", "i was told", "they said", "not my problem",
        "someone should", "that's above my pay grade",
        "that's not my responsibility", "i was just following",
        "it was supposed to", "no one told me",
    ],
    "T6_VAGUE_QUANTIFICATION": [
        "a lot", "a few", "some people", "many", "tons of",
        "a bunch", "pretty much", "a number of", "various",
    ],
    "T7_EMOTIONAL_FRAMING": [
        "feel like", "makes me feel", "i'm worried", "frustrated",
        "disappointed", "excited", "honestly feel", "deeply concerned",
    ],
    "T8_SOCIAL_PROOF_PRESSURE": [
        "everyone knows", "most people", "nobody thinks",
        "it's common knowledge", "industry standard", "best practice",
        "no one does that", "you're the only one",
    ],
}

CERTAINTY_INFLATION_TOKENS = [
    "clearly", "obviously", "undeniably", "without question",
    "undoubtedly", "of course", "certainly", "definitely",
    "there is no doubt", "the fact is",
]
CERTAINTY_ENFORCEMENT_VERBS = [
    "verify", "confirm", "validate", "test", "check", "audit",
    "measure", "prove", "enforce", "require",
]
ABSOLUTE_LANGUAGE_TOKENS = [
    "always", "never", "every single", "without exception",
    "zero chance", "guaranteed", "impossible",
    "there is no", "completely", "totally", "100%",
]
AUTHORITY_IMPOSITION_TOKENS = [
    "you need to", "you must", "you have to", "i'm telling you",
    "end of discussion", "that's final", "non-negotiable",
    "i've decided", "this is how it is",
]
CAPABILITY_OVERREACH_TOKENS = [
    "we can handle everything", "we do it all", "unlimited",
    "no limitations", "we cover all",
]
CAPABILITY_VERBS = [
    "handle", "manage", "cover", "deliver", "solve", "address",
    "support", "provide", "offer", "guarantee",
]


def classify_tilt(text: str, prompt: str = "", answer: str = "") -> List[str]:
    t = (text or "").lower()
    hits: List[str] = []

    for cat, markers in TILT_TAXONOMY.items():
        for m in markers:
            if m in t:
                hits.append(cat)
                break

    certainty_present = _contains_any(t, CERTAINTY_INFLATION_TOKENS)
    enforcement_present = _contains_any(t, CERTAINTY_ENFORCEMENT_VERBS)
    if certainty_present and not enforcement_present:
        hits.append("T2_CERTAINTY_INFLATION")

    if _contains_any(t, ABSOLUTE_LANGUAGE_TOKENS):
        hits.append("T5_ABSOLUTE_LANGUAGE")

    if _contains_any(t, AUTHORITY_IMPOSITION_TOKENS):
        hits.append("T10_AUTHORITY_IMPOSITION")

    if _contains_any(t, CAPABILITY_OVERREACH_TOKENS):
        hits.append("T4_CAPABILITY_OVERREACH")
    else:
        universal = any(u in t for u in ["all", "every", "any", "everything", "everyone", "no one"])
        capverb = _contains_any(t, CAPABILITY_VERBS)
        if universal and capverb:
            hits.append("T4_CAPABILITY_OVERREACH")

    if prompt and answer:
        p_dom = set(extract_domain_tokens(prompt))
        a_dom = extract_domain_tokens(answer)
        if a_dom:
            new_tokens = [x for x in a_dom if x not in p_dom]
            new_ratio = len(new_tokens) / max(len(a_dom), 1)
            if new_ratio >= 0.55 and len(new_tokens) >= 6:
                hits.append("T9_SCOPE_EXPANSION")

    uniq: List[str] = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    return uniq


# ── Failure Mode Detection ──

def detect_downstream_before_constraint(prompt: str, answer: str, l0_constraints: List[str]) -> bool:
    if not l0_constraints or not answer:
        return False
    a_lc = answer.lower()
    for c in l0_constraints:
        idx = a_lc.find(c)
        if idx > len(a_lc) * 0.6:
            return True
    return False


def detect_boundary_absence(answer: str) -> bool:
    a_lc = (answer or "").lower()
    boundary_markers = ["but", "however", "except", "unless", "only if", "cannot", "must not", "will not"]
    return not any(b in a_lc for b in boundary_markers)


def detect_narrative_stabilization(answer: str) -> bool:
    a_lc = (answer or "").lower()
    stabilizers = ["to summarize", "in conclusion", "overall", "the key point", "in short", "the bottom line"]
    return any(s in a_lc for s in stabilizers)


def detect_dce(answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    a_lc = (answer or "").lower()
    dce_markers = ["later", "eventually", "down the road", "when we get to", "at some point",
                   "we'll address", "can revisit", "circle back", "tbd", "to be determined", "pending"]
    found = [d for d in dce_markers if d in a_lc]
    if found and l0_constraints:
        return {"dce_state": "DCE_CONFIRMED", "markers": found}
    elif found:
        return {"dce_state": "DCE_PROBABLE", "markers": found}
    return {"dce_state": "DCE_FALSE", "markers": []}


def detect_cca(prompt: str, answer: str) -> Dict[str, Any]:
    p_constraints = detect_l0_constraints(prompt)
    a_constraints = detect_l0_constraints(answer)
    if len(p_constraints) >= 2 and len(a_constraints) <= 1:
        return {"cca_state": "CCA_CONFIRMED", "prompt_constraints": p_constraints, "answer_constraints": a_constraints}
    elif len(p_constraints) >= 2 and len(a_constraints) < len(p_constraints):
        return {"cca_state": "CCA_PROBABLE", "prompt_constraints": p_constraints, "answer_constraints": a_constraints}
    return {"cca_state": "CCA_FALSE", "prompt_constraints": p_constraints, "answer_constraints": a_constraints}


def detect_udds(prompt: str, answer: str, l0_constraints: List[str]) -> Dict[str, Any]:
    if not l0_constraints:
        return {"udds_state": "UDDS_FALSE", "detail": "No constraints to substitute."}
    a_lc = (answer or "").lower()
    substitution_markers = ["instead", "alternatively", "rather than", "a better approach",
                           "what if we", "consider", "how about", "another option"]
    found = [s for s in substitution_markers if s in a_lc]
    boundary_absent = detect_boundary_absence(answer)
    narrative_stable = detect_narrative_stabilization(answer)
    if found and boundary_absent:
        state = "UDDS_CONFIRMED"
    elif found or (narrative_stable and boundary_absent):
        state = "UDDS_PROBABLE"
    else:
        state = "UDDS_FALSE"
    return {"udds_state": state, "substitution_markers": found, "boundary_absent": boundary_absent, "narrative_stabilized": narrative_stable}


# ── Objective Detection ──

def objective_extract(prompt: str) -> Dict[str, Any]:
    sents = split_sentences(prompt)
    if not sents:
        return {"objective_found": False, "objective_sentence": None}
    return {"objective_found": True, "objective_sentence": sents[0]}


def objective_drift(prompt: str, answer: str) -> Dict[str, Any]:
    p_tokens = tokenize(prompt)
    a_tokens = tokenize(answer)
    j = jaccard(p_tokens, a_tokens)
    return {"jaccard_similarity": j, "drift_detected": j < 0.15 and len(a_tokens) > 20}


# ── Master Detection ──

def detect_all(text: str, prompt: str = "", answer: str = "") -> Dict[str, Any]:
    """Run all detection engines. Returns a DetectionMap."""
    effective_text = text
    if prompt and answer and not text:
        effective_text = f"{prompt}\n{answer}"

    l0_constraints = detect_l0_constraints(effective_text)
    framing = detect_l2_framing(effective_text)
    tilt = classify_tilt(effective_text, prompt=prompt, answer=answer)

    udds = detect_udds(prompt or "", answer or effective_text, l0_constraints)
    dce = detect_dce(answer or effective_text, l0_constraints)
    cca = detect_cca(prompt or "", answer or effective_text)

    downstream_before = detect_downstream_before_constraint(prompt or "", answer or effective_text, l0_constraints)
    obj = objective_extract(prompt or effective_text)
    drift = objective_drift(prompt or "", answer or "")

    failure_modes = {
        "UDDS": udds,
        "DCE": dce,
        "CCA": cca,
    }

    active_failures = [k for k, v in failure_modes.items()
                       if v.get(f"{k.lower()}_state", "").endswith("CONFIRMED")
                       or v.get(f"{k.lower()}_state", "").endswith("PROBABLE")]

    word_count = len(tokenize(effective_text))
    sentence_count = len(split_sentences(effective_text))

    return {
        "text": effective_text,
        "word_count": word_count,
        "sentence_count": sentence_count,
        "l0_constraints": l0_constraints,
        "framing": framing,
        "tilt_taxonomy": tilt,
        "failure_modes": failure_modes,
        "active_failures": active_failures,
        "downstream_before_constraints": downstream_before,
        "objective": obj,
        "drift": drift,
        "signal_density": round(len(tilt) / max(word_count, 1) * 100, 2),
    }


def detect_paragraphs(text: str, prompt: str = "", answer: str = "") -> List[Dict[str, Any]]:
    """Split text into paragraphs and run detect_all on each."""
    paras = split_paragraphs(text)
    if not paras:
        return [detect_all(text, prompt, answer)]
    results = []
    for i, para in enumerate(paras):
        det = detect_all(para, prompt, answer)
        det["paragraph_index"] = i
        det["paragraph_text"] = para
        results.append(det)
    return results
