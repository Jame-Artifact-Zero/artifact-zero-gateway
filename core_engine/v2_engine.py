import re
from typing import Any, Dict, List, Optional, Tuple
from .routing_engine import route_decision, DEFAULT_ROUTING_KEYWORDS

# =============================================================================
# HEDGE WORDS — 52 patterns with individual severity weights
# =============================================================================
HEDGE_WORDS = [
    # Uncertainty hedges
    "maybe", "likely", "possibly", "kind of", "sort of",
    "perhaps", "might", "could be", "i think", "i believe", "i guess",
    "i suppose", "it seems", "it appears", "arguably", "potentially",
    "presumably", "probably", "conceivably", "apparently", "allegedly",
    # Softeners
    "just", "a little", "a bit", "somewhat", "fairly", "rather",
    "more or less", "in a way", "to some extent", "to a degree",
    # Qualifiers
    "tend to", "in general", "for the most part", "as far as i know",
    "from my understanding", "if i recall correctly", "not entirely sure",
    "i could be wrong", "don't quote me", "take this with a grain of salt",
    # Passive deflection
    "it would seem", "one might argue", "some would say", "it's been said",
    "there's a chance", "it's possible that", "it's not impossible",
    # Commitment avoidance
    "we'll see", "time will tell", "remains to be seen", "hard to say",
    "depends on", "it depends", "that's debatable", "up in the air",
]

HEDGE_SEVERITY: Dict[str, float] = {
    "maybe": 0.04, "likely": 0.03, "possibly": 0.04, "kind of": 0.05, "sort of": 0.05,
    "perhaps": 0.04, "might": 0.03, "could be": 0.04, "i think": 0.05, "i believe": 0.04,
    "i guess": 0.06, "i suppose": 0.05, "it seems": 0.04, "it appears": 0.03,
    "arguably": 0.03, "potentially": 0.03, "presumably": 0.04, "probably": 0.03,
    "conceivably": 0.04, "apparently": 0.03, "allegedly": 0.05,
    "just": 0.02, "a little": 0.03, "a bit": 0.03, "somewhat": 0.03, "fairly": 0.02,
    "rather": 0.02, "more or less": 0.04, "in a way": 0.04, "to some extent": 0.04,
    "to a degree": 0.03, "tend to": 0.03, "in general": 0.02,
    "for the most part": 0.04, "as far as i know": 0.05,
    "from my understanding": 0.05, "if i recall correctly": 0.05,
    "not entirely sure": 0.07, "i could be wrong": 0.07,
    "don't quote me": 0.06, "take this with a grain of salt": 0.06,
    "it would seem": 0.05, "one might argue": 0.04, "some would say": 0.04,
    "it's been said": 0.04, "there's a chance": 0.05, "it's possible that": 0.05,
    "it's not impossible": 0.06, "we'll see": 0.05, "time will tell": 0.05,
    "remains to be seen": 0.05, "hard to say": 0.06, "depends on": 0.04,
    "it depends": 0.05, "that's debatable": 0.04, "up in the air": 0.06,
}

# =============================================================================
# ACTION VERBS — 85 patterns
# =============================================================================
ACTION_VERBS = [
    "create", "analyze", "explain", "summarize", "define",
    "build", "calculate", "design", "review", "draft",
    "write", "generate", "compare", "audit", "test",
    "fix", "update", "remove", "add", "install", "configure", "deploy", "migrate",
    "implement", "refactor", "debug", "validate", "verify", "confirm", "approve",
    "reject", "send", "submit", "execute", "run", "launch", "schedule", "assign",
    "delegate", "escalate", "resolve", "close", "complete", "finalize", "sign",
    "file", "report", "document", "measure", "assess", "evaluate", "diagnose",
    "prescribe", "recommend", "negotiate", "settle", "invoice", "bill", "collect",
    "transfer", "process", "convert", "extract", "import", "export", "merge",
    "split", "archive", "delete", "restore", "encrypt", "decrypt", "authorize",
    "revoke", "list", "rank", "prioritize", "outline", "map", "trace", "monitor",
    "scan", "inspect", "investigate", "research", "compile", "synthesize", "translate",
    "transcribe", "proofread", "edit", "rewrite", "publish", "distribute", "notify",
]

# =============================================================================
# DEFERRED PHRASES — 44 patterns
# =============================================================================
DEFERRED_PHRASES = [
    "we'll fix later", "we will fix later", "for now just", "adjust after",
    "fix later", "later we can",
    "we can revisit", "circle back", "table that for now", "tbd",
    "to be determined", "let's park that", "put a pin in", "come back to this",
    "deal with that later", "worry about that later", "not a priority right now",
    "we'll get to it", "we will get to it", "down the road", "at some point",
    "eventually", "when we have time", "in a future phase", "phase 2",
    "phase two", "next sprint", "next quarter", "backlog it", "add to backlog",
    "not now", "later", "soon", "we'll figure it out", "we will figure it out",
    "can wait", "low priority for now", "revisit next week", "follow up later",
    "pending further review", "subject to change", "placeholder for now",
    "temporary fix", "temp fix", "stopgap", "band-aid", "workaround for now",
    "good enough for now", "ship it and fix later",
]

# =============================================================================
# CONFLICT PAIRS — 24 pairs
# =============================================================================
CONFLICT_PAIRS = [
    ("always", "never"), ("short", "detailed"), ("minimal", "expand"),
    ("simple", "complex"), ("fast", "thorough"), ("cheap", "premium"),
    ("formal", "casual"), ("public", "private"), ("urgent", "no rush"),
    ("mandatory", "optional"), ("strict", "flexible"), ("all", "none"),
    ("include", "exclude"), ("approve", "reject"), ("start", "stop"),
    ("increase", "decrease"), ("before", "after"), ("internal", "external"),
    ("conservative", "aggressive"), ("automated", "manual"),
    ("centralized", "distributed"), ("transparent", "confidential"),
    ("permanent", "temporary"), ("required", "voluntary"),
    ("fixed", "variable"), ("standard", "custom"),
]

# =============================================================================
# PASSIVE VOICE PATTERNS
# =============================================================================
PASSIVE_PATTERNS = [
    re.compile(r"\bwas\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bwere\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bbeen\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bis\s+being\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bwas\s+being\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bhas\s+been\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bhave\s+been\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bwill\s+be\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bshould\s+be\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bcould\s+be\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\bmight\s+be\s+\w+ed\b", re.IGNORECASE),
]

# =============================================================================
# VAGUE QUANTIFIERS
# =============================================================================
VAGUE_QUANTIFIERS = [
    "some", "several", "many", "a few", "a lot", "numerous",
    "various", "multiple", "a number of", "a couple", "most", "certain",
]

# =============================================================================
# TIME INDICATORS
# =============================================================================
TIME_INDICATORS = [
    "by", "before", "deadline", "due", "until", "within",
    "end of day", "eod", "eow", "asap", "immediately", "today", "tomorrow",
    "this week", "next week", "by friday", "by monday", "q1", "q2", "q3", "q4",
]

# =============================================================================
# AMBIGUOUS PRONOUNS
# =============================================================================
AMBIGUOUS_PRONOUNS = ["it", "this", "that", "they", "them", "those", "these"]


def _normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _contains_any(text_lower: str, items: List[str]) -> bool:
    return any(i in text_lower for i in items)


def run_v2(
    text: str,
    routing_keywords: Optional[List[str]] = None,
    threshold: float = 0.80,
    industry: Optional[str] = None,
) -> Dict[str, Any]:
    """
    V2 = audit/structure, no LLM.
    Returns deterministic score + violations + routing.
    Severity-weighted scoring. Industry-aware routing.
    """
    raw = text or ""
    norm = _normalize(raw)
    lower = norm.lower()

    score = 1.0
    violations: List[str] = []
    violation_details: List[Dict[str, Any]] = []

    # 1) Hedge words — severity weighted
    hedge_hits = [w for w in HEDGE_WORDS if w in lower]
    if hedge_hits:
        total_severity = sum(HEDGE_SEVERITY.get(h, 0.04) for h in hedge_hits)
        score -= min(0.40, total_severity)  # cap hedge penalty
        violations.append("HEDGE_WORD")
        for h in hedge_hits:
            sev = HEDGE_SEVERITY.get(h, 0.04)
            violation_details.append({"type": "HEDGE_WORD", "trigger": h, "severity": sev})

    # 2) Missing objective — severity 0.10
    if not _contains_any(lower, ACTION_VERBS):
        score -= 0.10
        violations.append("MISSING_OBJECTIVE")
        violation_details.append({"type": "MISSING_OBJECTIVE", "trigger": "no action verb detected", "severity": 0.10})

    # 3) Conflicting directives — severity 0.15 each
    conflicts_found = []
    for a, b in CONFLICT_PAIRS:
        if a in lower and b in lower:
            conflicts_found.append((a, b))
    if conflicts_found:
        penalty = min(0.30, len(conflicts_found) * 0.15)
        score -= penalty
        violations.append("CONFLICTING_DIRECTIVE")
        for a, b in conflicts_found:
            violation_details.append({"type": "CONFLICTING_DIRECTIVE", "trigger": f"{a} / {b}", "severity": 0.15})

    # 4) Deferred enforcement — severity 0.12 each
    deferrals_found = [p for p in DEFERRED_PHRASES if p in lower]
    if deferrals_found:
        penalty = min(0.25, len(deferrals_found) * 0.12)
        score -= penalty
        violations.append("DEFERRED_ENFORCEMENT")
        for d in deferrals_found:
            violation_details.append({"type": "DEFERRED_ENFORCEMENT", "trigger": d, "severity": 0.12})

    # 5) Passive voice — severity 0.05 each, threshold 3
    passive_count = 0
    for pat in PASSIVE_PATTERNS:
        passive_count += len(pat.findall(norm))
    if passive_count >= 3:
        score -= min(0.15, passive_count * 0.05)
        violations.append("EXCESSIVE_PASSIVE")
        violation_details.append({"type": "EXCESSIVE_PASSIVE", "trigger": f"{passive_count} passive constructions", "severity": 0.05})

    # 6) Vague quantifiers — severity 0.04 each, threshold 2
    vague_regex_hits = []
    for v in VAGUE_QUANTIFIERS:
        pattern = re.compile(r"\b" + re.escape(v) + r"\b", re.IGNORECASE)
        if pattern.search(lower):
            vague_regex_hits.append(v)
    if len(vague_regex_hits) >= 2:
        score -= min(0.12, len(vague_regex_hits) * 0.04)
        violations.append("VAGUE_QUANTIFIER")
        for v in vague_regex_hits:
            violation_details.append({"type": "VAGUE_QUANTIFIER", "trigger": v, "severity": 0.04})

    # 7) Missing timeframe — severity 0.06
    has_time = _contains_any(lower, TIME_INDICATORS)
    has_action = _contains_any(lower, ACTION_VERBS)
    if has_action and not has_time and len(norm) > 100:
        score -= 0.06
        violations.append("MISSING_TIMEFRAME")
        violation_details.append({"type": "MISSING_TIMEFRAME", "trigger": "action detected but no deadline", "severity": 0.06})

    # 8) Ambiguous pronoun reference — severity 0.05
    sentences = re.split(r"(?<=[.!?])\s+", norm)
    ambiguous_count = 0
    for s in sentences:
        first_word = s.strip().split()[0].lower() if s.strip() else ""
        if first_word in AMBIGUOUS_PRONOUNS:
            ambiguous_count += 1
    if ambiguous_count >= 2:
        score -= min(0.10, ambiguous_count * 0.05)
        violations.append("AMBIGUOUS_REFERENCE")
        violation_details.append({"type": "AMBIGUOUS_REFERENCE", "trigger": f"{ambiguous_count} sentences start with ambiguous pronouns", "severity": 0.05})

    # Clamp
    score = max(0.0, min(1.0, score))

    # Routing — base + industry
    if routing_keywords is None:
        routing_keywords = list(DEFAULT_ROUTING_KEYWORDS)

    if industry:
        from .routing_engine import INDUSTRY_ROUTING
        industry_kw = INDUSTRY_ROUTING.get(industry, [])
        routing_keywords = routing_keywords + industry_kw

    route, route_matches = route_decision(lower, routing_keywords)

    return {
        "normalized_text": norm,
        "score": round(score, 2),
        "violations": violations,
        "violation_details": violation_details,
        "hedge_hits": hedge_hits,
        "route": route,
        "route_matches": route_matches,
        "threshold": threshold,
        "industry": industry or "all",
    }


def v2_feedback_message(v2_result: Dict[str, Any]) -> str:
    """
    Deterministic compiler-style feedback. No LLM.
    """
    score = v2_result.get("score", 0)
    violations = v2_result.get("violations", [])
    violation_details = v2_result.get("violation_details", [])
    route = v2_result.get("route", "AI")

    lines = [f"Score: {score}"]

    if route == "HUMAN_INTERNAL":
        lines.append("Route: HUMAN_INTERNAL")
        matches = v2_result.get("route_matches", [])
        if matches:
            lines.append(f"Trigger: {', '.join(matches)}")
        lines.append("Action: This contains routing triggers requiring human involvement.")
        return "\n".join(lines)

    if not violations:
        lines.append("Issues: none")
        return "\n".join(lines)

    lines.append(f"Issues detected ({len(violations)} types, {len(violation_details)} instances):")
    if "MISSING_OBJECTIVE" in violations:
        lines.append("- State a clear objective using an action verb.")
    if "HEDGE_WORD" in violations:
        hedge_hits = v2_result.get("hedge_hits", [])
        sample = ", ".join(hedge_hits[:5])
        extra = f" (+{len(hedge_hits)-5} more)" if len(hedge_hits) > 5 else ""
        lines.append(f"- Remove uncertain language: {sample}{extra}")
    if "CONFLICTING_DIRECTIVE" in violations:
        conflicts = [d["trigger"] for d in violation_details if d["type"] == "CONFLICTING_DIRECTIVE"]
        lines.append(f"- Resolve conflicting instructions: {', '.join(conflicts)}")
    if "DEFERRED_ENFORCEMENT" in violations:
        deferrals = [d["trigger"] for d in violation_details if d["type"] == "DEFERRED_ENFORCEMENT"]
        lines.append(f"- Define enforcement now: {', '.join(deferrals[:3])}")
    if "EXCESSIVE_PASSIVE" in violations:
        lines.append("- Reduce passive voice. Use active constructions.")
    if "VAGUE_QUANTIFIER" in violations:
        vagues = [d["trigger"] for d in violation_details if d["type"] == "VAGUE_QUANTIFIER"]
        lines.append(f"- Replace vague quantifiers with specific numbers: {', '.join(vagues)}")
    if "MISSING_TIMEFRAME" in violations:
        lines.append("- Add a deadline or timeframe to the objective.")
    if "AMBIGUOUS_REFERENCE" in violations:
        lines.append("- Sentences start with ambiguous pronouns. Name the subject.")

    lines.append("Revise and resubmit.")
    return "\n".join(lines)
