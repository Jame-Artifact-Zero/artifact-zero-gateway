"""
Corporate Structural Integrity (CSI) Scoring Engine
=====================================================
Purpose-built for scoring CORPORATE WEBSITE COMMUNICATION.
NOT the NII — the NII is for AI conversation pairs.

The NII clusters everything at 64-72 for corporate text because it measures
prompt→answer dynamics that don't exist in one-directional corporate copy.

CSI measures 10 dimensions that actually differentiate corporate communication:

D1: SPECIFICITY INDEX (15%)
    - Ratio of sentences containing measurable claims (numbers, dates, metrics)
    - Penalizes vague generalities ("we believe in excellence")
    - Rewards concrete evidence ("$648B revenue, 10,500 stores, 2.1M employees")

D2: COMMITMENT INTEGRITY (12%)
    - Ratio of verifiable commitments vs aspirational language
    - "Will deliver by Q3" vs "We strive to be the best"
    - Penalizes commitment language without specifics attached

D3: STRUCTURAL CLARITY (10%)
    - Sentence-level clarity: avg length, variation, readability
    - Penalizes run-on compound sentences, rewards crisp structure
    - Measures whether each sentence carries exactly one claim

D4: HEDGE DENSITY (10%)
    - Inverse of hedging/qualifying language density
    - "approximately", "generally", "may", "could", "some"
    - High hedging = low structural commitment to claims

D5: TILT EXPOSURE (12%)
    - Uses existing NTI tilt taxonomy (T1-T10)
    - Corporate text often carries T1 (reassurance), T3 (consensus), T7 (category blend)
    - Weighted by tilt severity

D6: EMPTY COMMITMENT RATIO (12%)
    - Sentences that make claims without evidence, timeline, or specifics
    - "We are committed to creating a better future" = empty
    - "We reduced emissions 40% since 2019" = substantive

D7: OBJECTIVE ANCHOR STRENGTH (8%)
    - Does the text state clear organizational objectives?
    - Can you extract WHAT the company does, for WHOM, with WHAT constraints?
    - Missing any of the three = deduction

D8: ACCOUNTABILITY LANGUAGE (8%)
    - Presence of ownership, responsibility, measurement, timeline language
    - "Our leadership team is accountable for" vs "We believe in"
    - Reports, reviews, audits, measurements = structural accountability

D9: REDUNDANCY & PADDING (8%)
    - Sentence-level semantic repetition (jaccard overlap between sentences)
    - Filler phrases: "at the end of the day", "it is important to note"
    - Higher redundancy = lower structural efficiency

D10: DIFFERENTIATION SIGNAL (5%)
    - How much of the text could be copy-pasted between any two companies?
    - Generic phrases: "world-class", "industry-leading", "committed to excellence"
    - Unique domain language that couldn't apply to competitors = high signal

SCORING:
  Raw composite 0.0-1.0 → displayed as percentage.
  Each dimension produces its own 0.0-1.0 sub-score.
  Weighted composite produces unique, differentiated scores.
  No two companies should score the same unless their text is structurally identical.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

CSI_VERSION = "csi-v1.0"

# ═══════════════════════════════════════════
# WORD LISTS & PATTERNS
# ═══════════════════════════════════════════

HEDGE_WORDS = [
    "approximately", "roughly", "about", "around", "nearly", "generally",
    "typically", "usually", "often", "sometimes", "may", "might", "could",
    "possibly", "potentially", "somewhat", "fairly", "relatively", "perhaps",
    "probably", "likely", "some", "several", "various", "many", "numerous",
]

COMMITMENT_VERBS = [
    "will", "shall", "must", "requires", "guarantees", "ensures", "delivers",
    "achieves", "commits", "pledges", "targets", "mandates", "enforces",
    "maintains", "operates", "executes", "implements", "reports", "measures",
    "tracks", "audits", "publishes", "discloses",
]

ASPIRATION_VERBS = [
    "strive", "strives", "striving", "aim", "aims", "aiming",
    "hope", "hopes", "hoping", "believe", "believes", "believing",
    "envision", "envisions", "aspire", "aspires", "dream", "dreams",
    "seek", "seeks", "seeking", "endeavor", "endeavors",
    "working toward", "working towards", "looking to", "planning to",
]

ACCOUNTABILITY_MARKERS = [
    "accountable", "accountability", "responsible", "responsibility",
    "measured", "measurement", "reported", "reporting", "quarterly",
    "annually", "annual report", "audit", "audited", "review", "reviewed",
    "disclosed", "disclosure", "transparent", "transparency",
    "board oversight", "governance", "compliance", "certified", "verified",
    "benchmark", "benchmarked", "kpi", "metrics", "scorecard",
    "by 2025", "by 2026", "by 2027", "by 2028", "by 2029", "by 2030",
    "by 2035", "by 2040", "by 2050",
    "since 2018", "since 2019", "since 2020", "since 2021", "since 2022",
    "since 2023", "since 2024",
]

FILLER_PHRASES = [
    "it is important to note", "at the end of the day", "in conclusion",
    "to summarize", "in other words", "as a matter of fact",
    "needless to say", "it goes without saying", "the fact of the matter",
    "first and foremost", "last but not least", "each and every",
    "at this point in time", "for all intents and purposes",
    "in order to", "with regard to", "in terms of",
    "moving forward", "going forward", "looking ahead",
]

GENERIC_PHRASES = [
    "world-class", "world class", "industry-leading", "industry leading",
    "best-in-class", "best in class", "cutting-edge", "cutting edge",
    "state-of-the-art", "state of the art", "next-generation",
    "innovative solutions", "transformative", "synergy", "synergies",
    "holistic approach", "paradigm shift", "value proposition",
    "thought leadership", "thought leader", "thought leaders",
    "committed to excellence", "passionate about", "dedicated to serving",
    "making the world a better place", "making a difference",
    "creating value", "driving innovation", "leading the way",
    "reimagining", "revolutionizing", "disrupting",
    "proud to", "excited to", "thrilled to", "delighted to",
    "stakeholder value", "shareholder value",
    "one-stop shop", "end-to-end", "turnkey",
    "mission-driven", "purpose-driven", "values-driven",
    "people-first", "customer-first", "customer-centric",
    "forward-thinking", "future-proof", "future-ready",
    "gold standard", "second to none", "unmatched", "unparalleled",
    "seamlessly", "effortlessly", "frictionless",
]

OBJECTIVE_WHAT_VERBS = [
    "designs", "manufactures", "builds", "develops", "provides", "offers",
    "operates", "manages", "distributes", "produces", "delivers", "creates",
    "sells", "markets", "supplies", "serves", "generates", "processes",
    "invests", "finances", "insures", "underwrites", "consults",
]

OBJECTIVE_WHOM_MARKERS = [
    "customers", "clients", "patients", "members", "users", "consumers",
    "businesses", "enterprises", "organizations", "communities", "families",
    "homeowners", "investors", "partners", "providers", "physicians",
    "developers", "employees", "associates", "team members",
]


# ═══════════════════════════════════════════
# TEXT UTILITIES
# ═══════════════════════════════════════════

def _split_sents(text: str) -> List[str]:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t:
        return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if p.strip() and len(p.strip()) > 5]


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[A-Za-z0-9']+", text or "") if w]


def _lower_words(text: str) -> List[str]:
    return [w.lower() for w in _words(text)]


def _contains_any(text_lc: str, needles: List[str]) -> int:
    return sum(1 for n in needles if n in text_lc)


def _jaccard(a: str, b: str) -> float:
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


# ═══════════════════════════════════════════
# DIMENSION SCORERS
# ═══════════════════════════════════════════

def _d1_specificity(text: str, sents: List[str]) -> Tuple[float, Dict]:
    """D1: SPECIFICITY INDEX — ratio of sentences with measurable claims."""
    if not sents:
        return 0.0, {"measurable_sents": 0, "total_sents": 0, "numbers_found": 0, "examples": []}

    number_re = re.compile(r"\b\d[\d,.]*\b")
    dollar_re = re.compile(r"\$[\d,.]+")
    percent_re = re.compile(r"\d+(\.\d+)?%")
    year_re = re.compile(r"\b(19|20)\d{2}\b")

    measurable = []
    examples = []
    total_numbers = len(number_re.findall(text))

    for s in sents:
        has_num = bool(number_re.search(s))
        has_dollar = bool(dollar_re.search(s))
        has_pct = bool(percent_re.search(s))
        has_year = bool(year_re.search(s))
        if has_num or has_dollar or has_pct:
            measurable.append(s)
            if len(examples) < 3:
                flags = []
                if has_dollar:
                    flags.append("$")
                if has_pct:
                    flags.append("%")
                if has_num:
                    flags.append("#")
                examples.append({"sentence": s[:120], "flags": flags})

    ratio = len(measurable) / len(sents)
    # Bonus for high number density
    density_bonus = min(total_numbers / max(len(sents), 1) * 0.1, 0.15)
    score = min(ratio + density_bonus, 1.0)

    return round(score, 4), {
        "measurable_sents": len(measurable),
        "total_sents": len(sents),
        "numbers_found": total_numbers,
        "ratio": round(ratio, 3),
        "examples": examples,
    }


def _d2_commitment(text: str, words_lower: List[str]) -> Tuple[float, Dict]:
    """D2: COMMITMENT INTEGRITY — verifiable commitments vs aspirational language."""
    if not words_lower:
        return 0.0, {"commit_count": 0, "aspire_count": 0}

    t_lower = text.lower()
    commit = sum(1 for w in words_lower if w in COMMITMENT_VERBS)
    aspire = _contains_any(t_lower, ASPIRATION_VERBS)

    total = commit + aspire
    if total == 0:
        # No commitment language at all — neutral but not great
        return 0.4, {"commit_count": 0, "aspire_count": 0, "ratio": 0.0, "note": "No commitment language detected"}

    ratio = commit / total
    # Pure aspiration = low score. Pure commitment = high score.
    # But commitment without specifics is also penalized (handled in D6)
    score = 0.3 + (ratio * 0.7)

    return round(score, 4), {
        "commit_count": commit,
        "aspire_count": aspire,
        "ratio": round(ratio, 3),
        "commit_words": [w for w in set(words_lower) if w in COMMITMENT_VERBS][:8],
        "aspire_hits": [a for a in ASPIRATION_VERBS if a in t_lower][:5],
    }


def _d3_clarity(sents: List[str]) -> Tuple[float, Dict]:
    """D3: STRUCTURAL CLARITY — sentence structure quality."""
    if not sents:
        return 0.0, {"avg_length": 0, "long_sents": 0, "short_sents": 0}

    lengths = [len(s.split()) for s in sents]
    avg = sum(lengths) / len(lengths)

    # Ideal sentence length: 10-25 words
    # Penalize very long (>35) or very short (<8)
    long_sents = sum(1 for l in lengths if l > 35)
    short_sents = sum(1 for l in lengths if l < 8)
    ideal_sents = sum(1 for l in lengths if 10 <= l <= 25)

    ideal_ratio = ideal_sents / len(sents)

    # Variety: standard deviation of lengths (some variety is good)
    mean_l = sum(lengths) / len(lengths)
    variance = sum((l - mean_l) ** 2 for l in lengths) / len(lengths)
    std_dev = variance ** 0.5

    # Moderate variety (std 3-10) is good. Too uniform or too wild is bad.
    variety_score = 1.0
    if std_dev < 2:
        variety_score = 0.6  # Too uniform
    elif std_dev > 15:
        variety_score = 0.5  # Too wild

    # Compound sentence penalty: "and" + "," heavy sentences
    compound_heavy = sum(1 for s in sents if s.count(" and ") >= 3 or s.count(",") >= 5)
    compound_penalty = min(compound_heavy / max(len(sents), 1) * 0.3, 0.25)

    score = (ideal_ratio * 0.5 + variety_score * 0.3 + (1 - long_sents / max(len(sents), 1)) * 0.2) - compound_penalty
    score = max(0, min(1.0, score))

    return round(score, 4), {
        "avg_length": round(avg, 1),
        "ideal_sents": ideal_sents,
        "long_sents": long_sents,
        "short_sents": short_sents,
        "total_sents": len(sents),
        "std_dev": round(std_dev, 1),
        "compound_heavy": compound_heavy,
    }


def _d4_hedge_density(text: str, words_lower: List[str]) -> Tuple[float, Dict]:
    """D4: HEDGE DENSITY — inverse of hedging language density."""
    if not words_lower:
        return 1.0, {"hedge_count": 0}

    t_lower = text.lower()
    hedge_hits = [h for h in HEDGE_WORDS if h in t_lower]
    count = len(hedge_hits)

    # Penalty scales with density
    word_count = max(len(words_lower), 1)
    density = count / word_count
    # Typical corporate text: 1-5 hedges per 100 words
    # >5 per 100 = heavy hedging
    penalty = min(density * 15, 0.7)
    score = max(0, 1.0 - penalty)

    return round(score, 4), {
        "hedge_count": count,
        "word_count": word_count,
        "density_per_100": round(density * 100, 2),
        "hedge_words_found": hedge_hits[:10],
    }


def _d5_tilt_exposure(text: str) -> Tuple[float, Dict]:
    """D5: TILT EXPOSURE — corporate tilt pattern detection."""
    # Import the existing tilt classifier
    try:
        from app import classify_tilt
        tilts = classify_tilt(text)
    except Exception:
        tilts = []

    tilt_weights = {
        "T1_REASSURANCE_DRIFT": 0.06,
        "T2_CERTAINTY_INFLATION": 0.10,
        "T3_CONSENSUS_CLAIMS": 0.05,
        "T4_CAPABILITY_OVERREACH": 0.12,
        "T5_ABSOLUTE_LANGUAGE": 0.08,
        "T6_CONSTRAINT_DEFERRAL": 0.10,
        "T7_CATEGORY_BLEND": 0.05,
        "T8_PRESSURE_OPTIMIZATION": 0.03,
        "T9_SCOPE_EXPANSION": 0.08,
        "T10_AUTHORITY_IMPOSITION": 0.06,
    }

    total_penalty = sum(tilt_weights.get(t, 0.04) for t in tilts)
    score = max(0, 1.0 - total_penalty)

    return round(score, 4), {
        "tilt_count": len(tilts),
        "tilts": tilts[:10],
        "penalty": round(total_penalty, 3),
    }


def _d6_empty_commitments(text: str, sents: List[str]) -> Tuple[float, Dict]:
    """D6: EMPTY COMMITMENT RATIO — claims without evidence."""
    if not sents:
        return 0.0, {"empty_count": 0, "total": 0}

    commitment_markers = [
        "committed", "commitment", "dedicated", "driven", "focused on",
        "passionate", "believe", "strive", "aim", "proud", "excited",
        "our mission", "our purpose", "our vision", "our values",
        "creating", "building", "transforming", "advancing", "improving",
        "we are", "we will", "we can",
    ]

    number_re = re.compile(r"\b\d[\d,.]*\b")
    dollar_re = re.compile(r"\$[\d,.]+")
    evidence_re = re.compile(r"\b(percent|%|\d+\s*(stores|employees|locations|centers|countries|users|members|patients|people|associates|team members))\b", re.I)

    empty = []
    substantive = []
    examples = []

    for s in sents:
        s_lower = s.lower()
        has_commitment = any(m in s_lower for m in commitment_markers)
        has_evidence = bool(number_re.search(s)) or bool(dollar_re.search(s)) or bool(evidence_re.search(s))

        if has_commitment and not has_evidence:
            empty.append(s)
            if len(examples) < 3:
                examples.append(s[:120])
        elif has_evidence:
            substantive.append(s)

    if not empty and not substantive:
        return 0.6, {"empty_count": 0, "substantive_count": 0, "total": len(sents), "note": "No commitment language found"}

    total_claims = len(empty) + len(substantive)
    if total_claims == 0:
        return 0.6, {"empty_count": 0, "substantive_count": 0, "total": len(sents)}

    empty_ratio = len(empty) / total_claims
    score = max(0, 1.0 - empty_ratio)

    return round(score, 4), {
        "empty_count": len(empty),
        "substantive_count": len(substantive),
        "total": len(sents),
        "empty_ratio": round(empty_ratio, 3),
        "empty_examples": examples,
    }


def _d7_objective_anchor(text: str) -> Tuple[float, Dict]:
    """D7: OBJECTIVE ANCHOR STRENGTH — clear WHO, WHAT, constraints."""
    t_lower = text.lower()

    # WHAT: does the text say what the company actually does?
    has_what = any(v in t_lower for v in OBJECTIVE_WHAT_VERBS)

    # WHOM: does the text identify who they serve?
    has_whom = any(m in t_lower for m in OBJECTIVE_WHOM_MARKERS)

    # CONSTRAINT: does the text acknowledge any limitations, boundaries, or trade-offs?
    constraint_markers = [
        "must", "require", "cannot", "limit", "only", "except", "unless",
        "regulated", "compliance", "within", "boundary", "scope",
        "not a substitute", "subject to", "does not",
    ]
    has_constraint = any(m in t_lower for m in constraint_markers)

    anchors = sum([has_what, has_whom, has_constraint])
    # 3 = fully anchored, 2 = mostly, 1 = weak, 0 = no anchor
    scores = {0: 0.15, 1: 0.45, 2: 0.75, 3: 1.0}
    score = scores.get(anchors, 0.15)

    return round(score, 4), {
        "has_what": has_what,
        "has_whom": has_whom,
        "has_constraint": has_constraint,
        "anchor_count": anchors,
    }


def _d8_accountability(text: str) -> Tuple[float, Dict]:
    """D8: ACCOUNTABILITY LANGUAGE — ownership, measurement, reporting."""
    t_lower = text.lower()
    hits = [m for m in ACCOUNTABILITY_MARKERS if m in t_lower]

    # Score based on density and variety of accountability language
    if not hits:
        return 0.15, {"accountability_hits": 0, "markers": []}

    # Group by category
    categories = {
        "measurement": ["measured", "measurement", "kpi", "metrics", "scorecard", "benchmark", "benchmarked"],
        "reporting": ["reported", "reporting", "quarterly", "annually", "annual report", "disclosed", "disclosure"],
        "oversight": ["board oversight", "governance", "compliance", "certified", "verified", "audit", "audited", "review", "reviewed"],
        "ownership": ["accountable", "accountability", "responsible", "responsibility", "transparent", "transparency"],
        "timeline": [m for m in ACCOUNTABILITY_MARKERS if m.startswith("by 20") or m.startswith("since 20")],
    }

    cat_hits = {}
    for cat, markers in categories.items():
        cat_hits[cat] = sum(1 for m in markers if m in t_lower)

    categories_present = sum(1 for c, v in cat_hits.items() if v > 0)
    # More categories = higher score (breadth of accountability)
    breadth = min(categories_present / 4, 1.0)
    depth = min(len(hits) / 6, 1.0)
    score = breadth * 0.6 + depth * 0.4

    return round(score, 4), {
        "accountability_hits": len(hits),
        "categories_present": categories_present,
        "category_detail": {k: v for k, v in cat_hits.items() if v > 0},
        "markers": hits[:10],
    }


def _d9_redundancy(sents: List[str], text: str) -> Tuple[float, Dict]:
    """D9: REDUNDANCY & PADDING — semantic repetition and filler."""
    if len(sents) < 2:
        return 0.7, {"overlap_pairs": 0, "filler_count": 0}

    t_lower = text.lower()

    # Filler phrase count
    filler_count = sum(1 for f in FILLER_PHRASES if f in t_lower)

    # Pairwise sentence overlap
    high_overlap_pairs = 0
    total_pairs = 0
    for i in range(len(sents)):
        for j in range(i + 1, min(i + 4, len(sents))):  # Only nearby sentences
            jac = _jaccard(sents[i], sents[j])
            total_pairs += 1
            if jac > 0.35:  # High overlap
                high_overlap_pairs += 1

    overlap_ratio = high_overlap_pairs / max(total_pairs, 1)
    filler_penalty = min(filler_count * 0.08, 0.3)
    overlap_penalty = min(overlap_ratio * 0.8, 0.5)

    score = max(0, 1.0 - filler_penalty - overlap_penalty)

    return round(score, 4), {
        "overlap_pairs": high_overlap_pairs,
        "total_pairs": total_pairs,
        "overlap_ratio": round(overlap_ratio, 3),
        "filler_count": filler_count,
        "filler_phrases_found": [f for f in FILLER_PHRASES if f in t_lower][:5],
    }


def _d10_differentiation(text: str, words_lower: List[str]) -> Tuple[float, Dict]:
    """D10: DIFFERENTIATION SIGNAL — unique vs generic corporate language."""
    if not words_lower:
        return 0.0, {"generic_count": 0}

    t_lower = text.lower()
    generic_hits = [g for g in GENERIC_PHRASES if g in t_lower]
    generic_count = len(generic_hits)

    word_count = max(len(words_lower), 1)
    # Penalty per generic phrase (they take up space without meaning)
    generic_penalty = min(generic_count * 0.06, 0.6)

    # Bonus for unique domain vocabulary (5+ char words not in generic list)
    stopwords = {
        "their", "about", "which", "these", "those", "other", "every",
        "would", "could", "should", "where", "there", "being", "after",
        "while", "years", "since", "based", "through", "between",
    }
    domain_words = set(w for w in words_lower if len(w) >= 6 and w not in stopwords and w.isalpha())
    # More unique domain words = more differentiated text
    domain_bonus = min(len(domain_words) / 40, 0.2)

    score = max(0, min(1.0, 0.7 - generic_penalty + domain_bonus))

    return round(score, 4), {
        "generic_count": generic_count,
        "generic_phrases_found": generic_hits[:8],
        "unique_domain_words": len(domain_words),
        "domain_sample": sorted(list(domain_words))[:10],
    }


# ═══════════════════════════════════════════
# MAIN SCORER
# ═══════════════════════════════════════════

# Dimension weights (sum to 1.0)
WEIGHTS = {
    "d1_specificity": 0.15,
    "d2_commitment": 0.12,
    "d3_clarity": 0.10,
    "d4_hedge_density": 0.10,
    "d5_tilt_exposure": 0.12,
    "d6_empty_commitments": 0.12,
    "d7_objective_anchor": 0.08,
    "d8_accountability": 0.08,
    "d9_redundancy": 0.08,
    "d10_differentiation": 0.05,
}


def score_corporate_text(text: str) -> Dict[str, Any]:
    """
    Score corporate website communication on structural integrity.
    Returns a detailed breakdown with per-dimension scores and a composite.
    """
    if not text or len(text.strip()) < 50:
        return {
            "version": CSI_VERSION,
            "score": 0,
            "label": "INSUFFICIENT",
            "error": "Text too short for meaningful analysis",
        }

    sents = _split_sents(text)
    words = _words(text)
    words_lower = [w.lower() for w in words]

    # Score each dimension
    d1, d1_detail = _d1_specificity(text, sents)
    d2, d2_detail = _d2_commitment(text, words_lower)
    d3, d3_detail = _d3_clarity(sents)
    d4, d4_detail = _d4_hedge_density(text, words_lower)
    d5, d5_detail = _d5_tilt_exposure(text)
    d6, d6_detail = _d6_empty_commitments(text, sents)
    d7, d7_detail = _d7_objective_anchor(text)
    d8, d8_detail = _d8_accountability(text)
    d9, d9_detail = _d9_redundancy(sents, text)
    d10, d10_detail = _d10_differentiation(text, words_lower)

    dimensions = {
        "d1_specificity": d1,
        "d2_commitment": d2,
        "d3_clarity": d3,
        "d4_hedge_density": d4,
        "d5_tilt_exposure": d5,
        "d6_empty_commitments": d6,
        "d7_objective_anchor": d7,
        "d8_accountability": d8,
        "d9_redundancy": d9,
        "d10_differentiation": d10,
    }

    # Weighted composite
    raw = sum(dimensions[k] * WEIGHTS[k] for k in WEIGHTS)
    score = round(raw * 100, 1)

    # Band labels
    if score >= 80:
        label = "STRUCTURALLY SOUND"
    elif score >= 65:
        label = "MOSTLY CLEAR"
    elif score >= 50:
        label = "MIXED SIGNALS"
    elif score >= 35:
        label = "STRUCTURALLY WEAK"
    elif score >= 20:
        label = "POOR STRUCTURE"
    else:
        label = "STRUCTURAL FAILURE"

    # Generate human-readable findings
    findings = _generate_findings(dimensions, d1_detail, d2_detail, d3_detail,
                                   d4_detail, d5_detail, d6_detail, d7_detail,
                                   d8_detail, d9_detail, d10_detail)

    return {
        "version": CSI_VERSION,
        "score": score,
        "score_raw": round(raw, 4),
        "label": label,
        "dimensions": {k: round(v, 3) for k, v in dimensions.items()},
        "weights": WEIGHTS,
        "detail": {
            "d1_specificity": d1_detail,
            "d2_commitment": d2_detail,
            "d3_clarity": d3_detail,
            "d4_hedge_density": d4_detail,
            "d5_tilt_exposure": d5_detail,
            "d6_empty_commitments": d6_detail,
            "d7_objective_anchor": d7_detail,
            "d8_accountability": d8_detail,
            "d9_redundancy": d9_detail,
            "d10_differentiation": d10_detail,
        },
        "findings": findings,
        "meta": {
            "word_count": len(words),
            "sentence_count": len(sents),
            "char_count": len(text),
        },
    }


def _generate_findings(dims, d1d, d2d, d3d, d4d, d5d, d6d, d7d, d8d, d9d, d10d) -> List[Dict[str, str]]:
    """Generate specific, actionable findings from dimension details."""
    findings = []

    # D1: Specificity
    if dims["d1_specificity"] < 0.4:
        measurable = d1d.get("measurable_sents", 0)
        total = d1d.get("total_sents", 1)
        findings.append({
            "dimension": "D1 — Specificity",
            "severity": "high" if dims["d1_specificity"] < 0.2 else "medium",
            "finding": f"Only {measurable} of {total} sentences contain measurable claims. Most statements are unverifiable.",
            "evidence": "No numbers, dates, or metrics attached to key claims.",
        })
    elif dims["d1_specificity"] >= 0.7:
        findings.append({
            "dimension": "D1 — Specificity",
            "severity": "pass",
            "finding": f"{d1d.get('measurable_sents', 0)} of {d1d.get('total_sents', 0)} sentences backed by data. Strong evidence base.",
            "evidence": "",
        })

    # D2: Commitment
    if dims["d2_commitment"] < 0.5:
        findings.append({
            "dimension": "D2 — Commitment Integrity",
            "severity": "high" if dims["d2_commitment"] < 0.35 else "medium",
            "finding": f"Aspirational language ({d2d.get('aspire_count', 0)} instances) outweighs commitment language ({d2d.get('commit_count', 0)}).",
            "evidence": ", ".join(d2d.get("aspire_hits", [])[:3]) or "Heavy use of 'strive', 'aim', 'hope' without delivery commitments.",
        })

    # D3: Clarity
    if dims["d3_clarity"] < 0.5:
        findings.append({
            "dimension": "D3 — Structural Clarity",
            "severity": "medium",
            "finding": f"{d3d.get('long_sents', 0)} sentences exceed 35 words. Compound structures reduce readability.",
            "evidence": f"Average sentence: {d3d.get('avg_length', 0)} words.",
        })

    # D4: Hedging
    if dims["d4_hedge_density"] < 0.6:
        findings.append({
            "dimension": "D4 — Hedge Density",
            "severity": "medium",
            "finding": f"{d4d.get('hedge_count', 0)} hedge words detected ({d4d.get('density_per_100', 0):.1f} per 100 words). Weakens structural commitment.",
            "evidence": ", ".join(d4d.get("hedge_words_found", [])[:5]),
        })

    # D5: Tilt
    if dims["d5_tilt_exposure"] < 0.8:
        findings.append({
            "dimension": "D5 — Tilt Exposure",
            "severity": "medium" if dims["d5_tilt_exposure"] >= 0.6 else "high",
            "finding": f"{d5d.get('tilt_count', 0)} tilt patterns detected in corporate language.",
            "evidence": ", ".join(t.replace("_", " ") for t in d5d.get("tilts", [])[:4]),
        })

    # D6: Empty commitments
    if dims["d6_empty_commitments"] < 0.5:
        findings.append({
            "dimension": "D6 — Empty Commitments",
            "severity": "high",
            "finding": f"{d6d.get('empty_count', 0)} commitment statements lack supporting evidence. Claims made without data.",
            "evidence": (d6d.get("empty_examples", [""])[0])[:100] if d6d.get("empty_examples") else "",
        })

    # D7: Objective anchor
    if dims["d7_objective_anchor"] < 0.75:
        missing = []
        if not d7d.get("has_what"):
            missing.append("WHAT the company does")
        if not d7d.get("has_whom"):
            missing.append("WHO they serve")
        if not d7d.get("has_constraint"):
            missing.append("constraints or boundaries")
        findings.append({
            "dimension": "D7 — Objective Anchor",
            "severity": "medium" if dims["d7_objective_anchor"] >= 0.45 else "high",
            "finding": f"Missing structural anchor: {', '.join(missing)}.",
            "evidence": f"{d7d.get('anchor_count', 0)} of 3 anchor elements present.",
        })

    # D8: Accountability
    if dims["d8_accountability"] < 0.4:
        findings.append({
            "dimension": "D8 — Accountability",
            "severity": "medium",
            "finding": "No accountability language detected. No mention of measurement, reporting, auditing, or timeline commitments.",
            "evidence": "",
        })

    # D9: Redundancy
    if dims["d9_redundancy"] < 0.6:
        findings.append({
            "dimension": "D9 — Redundancy",
            "severity": "low",
            "finding": f"{d9d.get('overlap_pairs', 0)} sentence pairs with high semantic overlap. {d9d.get('filler_count', 0)} filler phrases detected.",
            "evidence": ", ".join(d9d.get("filler_phrases_found", [])[:3]),
        })

    # D10: Differentiation
    if dims["d10_differentiation"] < 0.4:
        findings.append({
            "dimension": "D10 — Differentiation",
            "severity": "low" if dims["d10_differentiation"] >= 0.25 else "medium",
            "finding": f"{d10d.get('generic_count', 0)} generic corporate phrases detected. Language could apply to any company.",
            "evidence": ", ".join(d10d.get("generic_phrases_found", [])[:4]),
        })

    return findings
