import re
from typing import Any, Dict, List, Optional
from .routing_engine import route_decision, DEFAULT_ROUTING_KEYWORDS

HEDGE_WORDS = ["maybe", "likely", "possibly", "kind of", "sort of"]

ACTION_VERBS = [
    "create", "analyze", "explain", "summarize", "define",
    "build", "calculate", "design", "review", "draft",
    "write", "generate", "compare", "audit", "test"
]

DEFERRED_PHRASES = [
    "we'll fix later",
    "we will fix later",
    "for now just",
    "adjust after",
    "fix later",
    "later we can",
]

CONFLICT_PAIRS = [
    ("always", "never"),
    ("short", "detailed"),
    ("minimal", "expand"),
]

def _normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text

def _contains_any(text_lower: str, items: List[str]) -> bool:
    return any(i in text_lower for i in items)

def run_v2(
    text: str,
    routing_keywords: Optional[List[str]] = None,
    threshold: float = 0.80
) -> Dict[str, Any]:
    """
    V2 = audit/structure, no LLM.
    Returns deterministic score + violations + routing.
    """
    raw = text or ""
    norm = _normalize(raw)
    lower = norm.lower()

    score = 1.0
    violations: List[str] = []

    # 1) Hedge words
    hedge_hits = [w for w in HEDGE_WORDS if w in lower]
    if hedge_hits:
        score -= 0.10
        violations.append("HEDGE_WORD")

    # 2) Missing objective (no action verb)
    if not _contains_any(lower, ACTION_VERBS):
        score -= 0.10
        violations.append("MISSING_OBJECTIVE")

    # 3) Conflicting directives
    for a, b in CONFLICT_PAIRS:
        if a in lower and b in lower:
            score -= 0.10
            violations.append("CONFLICTING_DIRECTIVE")
            break

    # 4) Deferred enforcement
    if _contains_any(lower, DEFERRED_PHRASES):
        score -= 0.10
        violations.append("DEFERRED_ENFORCEMENT")

    # Clamp
    if score < 0.0:
        score = 0.0

    # Routing
    if routing_keywords is None:
        routing_keywords = DEFAULT_ROUTING_KEYWORDS

    route, route_matches = route_decision(lower, routing_keywords)

    return {
        "normalized_text": norm,
        "score": round(score, 2),
        "violations": violations,
        "hedge_hits": hedge_hits,
        "route": route,
        "route_matches": route_matches,
        "threshold": threshold
    }

def v2_feedback_message(v2_result: Dict[str, Any]) -> str:
    """
    Deterministic compiler-style feedback. No LLM.
    """
    score = v2_result.get("score", 0)
    violations = v2_result.get("violations", [])
    route = v2_result.get("route", "AI")

    lines = [f"Score: {score}"]

    if route == "HUMAN_INTERNAL":
        lines.append("Route: HUMAN_INTERNAL")
        matches = v2_result.get("route_matches", [])
        if matches:
            lines.append(f"Trigger: {', '.join(matches)}")
        lines.append("Action: This looks like an internal routing/approval question. Talk to the appropriate human owner (manager/finance/legal).")
        return "\n".join(lines)

    if not violations:
        lines.append("Issues: none")
        return "\n".join(lines)

    lines.append("Issues detected:")
    if "MISSING_OBJECTIVE" in violations:
        lines.append("- State a clear objective using an action verb (e.g., define/analyze/create).")
    if "HEDGE_WORD" in violations:
        lines.append("- Remove uncertain language (maybe/likely/possibly/kind of/sort of).")
    if "CONFLICTING_DIRECTIVE" in violations:
        lines.append("- Remove conflicting instructions (e.g., short + detailed).")
    if "DEFERRED_ENFORCEMENT" in violations:
        lines.append("- Define enforcement now; avoid 'fix later' directives.")

    lines.append("Revise and resubmit.")
    return "\n".join(lines)
