# interrogative_engine.py
# Interrogative Analysis Engine (interrogative-v1.0)
# Deterministic, rule-based decomposition of questions as structural objects.
# No motive inference. No psychology. No rewriting.
#
# Core thesis: Questions are not neutral. They carry structure, framing,
# scope, and positioning. This engine exposes that structure so the
# responder can see the architecture of what's being asked — not just
# the words.
#
# Integrates with: edge_engine, invocation_governance, middleware, trace

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

INTERROGATIVE_VERSION = "interrogative-v1.0"


# ═══════════════════════════════════════════════════════════════
# SECTION 1: Question Detection & Extraction
# ═══════════════════════════════════════════════════════════════

# Direct question markers
QUESTION_TERMINATORS = re.compile(r"[?]")

# Indirect/embedded question patterns (no question mark needed)
EMBEDDED_QUESTION_PATTERNS = [
    re.compile(r"\bi(?:'m| am)\s+(?:curious|wondering)\b", re.IGNORECASE),
    re.compile(r"\bcan\s+you\s+(?:explain|tell|walk|show|clarify)\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(?:does|do|is|are|would|could|should)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+(?:does|do|is|are|would|could|should)\b", re.IGNORECASE),
    re.compile(r"\bwhy\s+(?:does|do|is|are|would|could|should|did|didn't)\b", re.IGNORECASE),
    re.compile(r"\bwho\s+(?:does|do|is|are|would|could|should)\b", re.IGNORECASE),
    re.compile(r"\bi\s+(?:want|need)\s+to\s+(?:understand|know)\b", re.IGNORECASE),
    re.compile(r"\bcould\s+you\s+(?:explain|walk|break)\b", re.IGNORECASE),
    re.compile(r"\bhelp\s+me\s+understand\b", re.IGNORECASE),
]


def extract_questions(text: str) -> List[Dict[str, Any]]:
    """
    Extract all question units from text.
    A question unit is either:
      - A sentence ending in ?
      - A clause matching an embedded question pattern
    Returns list of question objects with position and raw text.
    """
    if not text or not text.strip():
        return []

    questions: List[Dict[str, Any]] = []
    seen_spans: set = set()

    # Pass 1: Explicit questions (sentences ending in ?)
    # Split on sentence boundaries, find ones with ?
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    char_pos = 0
    for sent in sentences:
        sent_stripped = sent.strip()
        if "?" in sent_stripped:
            span_key = (char_pos, char_pos + len(sent_stripped))
            if span_key not in seen_spans:
                seen_spans.add(span_key)
                questions.append({
                    "raw": sent_stripped,
                    "type": "explicit",
                    "position": char_pos,
                })
        char_pos += len(sent) + 1  # +1 for split whitespace

    # Pass 2: Embedded questions (no ? but interrogative structure)
    # Only add if the clause doesn't substantially overlap with an explicit question
    explicit_texts = {q["raw"].lower().strip() for q in questions}
    for pattern in EMBEDDED_QUESTION_PATTERNS:
        for m in pattern.finditer(text):
            # Get the full sentence containing this match
            start = text.rfind(".", 0, m.start())
            start = start + 1 if start != -1 else 0
            end = text.find(".", m.end())
            end = end + 1 if end != -1 else len(text)
            clause = text[start:end].strip()

            # Skip if this clause is already captured as explicit question
            clause_lower = clause.lower().strip().rstrip("?").strip()
            already_captured = any(
                clause_lower in et or et.rstrip("?").strip() in clause_lower
                for et in explicit_texts
                if len(et) > 10
            )

            span_key = (start, end)
            if span_key not in seen_spans and clause and not already_captured:
                seen_spans.add(span_key)
                questions.append({
                    "raw": clause,
                    "type": "embedded",
                    "position": start,
                })

    # Sort by position
    questions.sort(key=lambda q: q["position"])

    # Assign indices
    for i, q in enumerate(questions):
        q["index"] = i

    return questions


# ═══════════════════════════════════════════════════════════════
# SECTION 2: Question Classification
# ═══════════════════════════════════════════════════════════════

# Question type taxonomy
QUESTION_TYPE_PATTERNS: List[Tuple[str, List[re.Pattern], str]] = [
    # (type_name, patterns, description)
    ("scope_expansion", [
        re.compile(r"\bwhat\s+(?:all|else|other)\b", re.IGNORECASE),
        re.compile(r"\banything\s+else\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+about\b", re.IGNORECASE),
        re.compile(r"\band\s+(?:what|how)\s+about\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+(?:does|do)\s+.*\s+(?:do|handle|cover|include)\b", re.IGNORECASE),
    ], "Forces responder to expand scope beyond what was presented"),

    ("reduction_probe", [
        re.compile(r"\bso\s+(?:basically|essentially|really|just)\b", re.IGNORECASE),
        re.compile(r"\bso\s+(?:it'?s?|this\s+is)\s+(?:just|basically|really)\b", re.IGNORECASE),
        re.compile(r"\bisn'?t\s+(?:this|that|it)\s+(?:just|basically|really)\b", re.IGNORECASE),
        re.compile(r"\b(?:is|does)\s+(?:it|this|that)\s+(?:just|only|merely)\b", re.IGNORECASE),
    ], "Reframes complex system as simple/trivial"),

    ("authority_test", [
        re.compile(r"\bwho\s+(?:says|decided|validated|verified|certified)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+(?:authority|credentials|qualifications)\b", re.IGNORECASE),
        re.compile(r"\bhave\s+you\s+(?:tested|validated|proven|verified)\b", re.IGNORECASE),
        re.compile(r"\bwhere'?s?\s+the\s+(?:proof|evidence|data)\b", re.IGNORECASE),
        re.compile(r"\bcan\s+you\s+prove\b", re.IGNORECASE),
    ], "Challenges legitimacy or authority of the responder"),

    ("false_equivalence", [
        re.compile(r"\bhow\s+is\s+(?:this|that)\s+different\s+(?:from|than)\b", re.IGNORECASE),
        re.compile(r"\bisn'?t\s+(?:this|that)\s+(?:the\s+)?same\s+(?:as|thing)\b", re.IGNORECASE),
        re.compile(r"\bcouldn'?t\s+(?:you|someone)\s+just\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+(?:makes|stops)\s+(?:this|that)\s+(?:different|special)\b", re.IGNORECASE),
    ], "Flattens distinction between the system and something else"),

    ("timeline_pressure", [
        re.compile(r"\bwhen\s+(?:will|does|can|is)\s+(?:this|that|it)\b", re.IGNORECASE),
        re.compile(r"\bhow\s+(?:long|soon|fast|quickly)\b", re.IGNORECASE),
        re.compile(r"\bwhat'?s?\s+the\s+timeline\b", re.IGNORECASE),
        re.compile(r"\bwhen\s+(?:do\s+you|can\s+I|will\s+we)\b", re.IGNORECASE),
    ], "Creates urgency or forces premature commitment"),

    ("scope_inversion", [
        re.compile(r"\bwhat\s+(?:can'?t|doesn'?t|won'?t)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+(?:are|is)\s+the\s+(?:limits?|limitations?|weaknesses?|gaps?|risks?)\b", re.IGNORECASE),
        re.compile(r"\bwhere\s+(?:does|do)\s+(?:this|it)\s+(?:fall\s+short|fail|break)\b", re.IGNORECASE),
    ], "Forces responder to enumerate weaknesses"),

    ("anchor_plant", [
        re.compile(r"\bso\s+(?:you'?re\s+saying|what\s+you'?re\s+saying)\b", re.IGNORECASE),
        re.compile(r"\bif\s+I\s+(?:understand|hear)\s+(?:you|this)\s+(?:correctly|right)\b", re.IGNORECASE),
        re.compile(r"\blet\s+me\s+(?:make\s+sure|see\s+if)\s+I\s+understand\b", re.IGNORECASE),
        re.compile(r"\bso\s+(?:the\s+)?(?:idea|concept|point|claim)\s+is\b", re.IGNORECASE),
    ], "Restates position with subtle reframing to create a false anchor"),

    ("innocence_framed", [
        re.compile(r"\bi'?m?\s+just\s+(?:trying\s+to\s+understand|curious|asking)\b", re.IGNORECASE),
        re.compile(r"\bsimple\s+question\b", re.IGNORECASE),
        re.compile(r"\bjust\s+(?:want\s+to|trying\s+to)\s+(?:understand|know|clarify)\b", re.IGNORECASE),
        re.compile(r"\bhelp\s+me\s+understand\b", re.IGNORECASE),
        re.compile(r"\bfor\s+(?:my|our)\s+(?:own|)\s*understanding\b", re.IGNORECASE),
    ], "Disclaims interrogative intent while maintaining probe structure"),

    ("comparison_trap", [
        re.compile(r"\bhow\s+does\s+(?:this|that|it)\s+compare\s+to\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+about\s+(?:compared\s+to|versus|vs)\b", re.IGNORECASE),
        re.compile(r"\bwhy\s+(?:not\s+)?(?:just\s+)?use\b", re.IGNORECASE),
        re.compile(r"\bwhy\s+(?:wouldn'?t|shouldn'?t|couldn'?t)\s+(?:I|we|someone)\s+just\b", re.IGNORECASE),
    ], "Forces positioning against a competitor or alternative"),

    ("social_proof_extraction", [
        re.compile(r"\bwho\s+else\b", re.IGNORECASE),
        re.compile(r"\banyone\s+(?:else|using)\b", re.IGNORECASE),
        re.compile(r"\bother\s+(?:people|companies|investors|clients|customers)\b", re.IGNORECASE),
        re.compile(r"\bwho\s+(?:has|is)\s+(?:using|seen|invested|looked)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+did\s+they\s+(?:think|say)\b", re.IGNORECASE),
    ], "Extracts social proof to gauge if others have validated"),

    ("hypothetical_threat", [
        re.compile(r"\bwhat\s+if\s+(?:google|microsoft|amazon|openai|a\s+competitor|someone)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+happens\s+(?:if|when)\s+(?:nobody|no\s+one|it\s+doesn'?t|this\s+doesn'?t)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+if\s+.*\s+(?:for\s+free|builds?\s+this|copies|clones)\b", re.IGNORECASE),
    ], "Introduces fatal hypothetical scenario to test founder response"),

    ("clarification_genuine", [
        re.compile(r"\bwhat\s+(?:is|are|does)\s+\w+\b", re.IGNORECASE),
        re.compile(r"\bcan\s+you\s+(?:explain|define|describe)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+do\s+you\s+mean\s+by\b", re.IGNORECASE),
    ], "Straightforward request for information"),
]


def classify_question(question: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify a single question by type.
    Returns the question enriched with type classifications and scores.
    """
    raw = question.get("raw", "")
    if not raw:
        question["classifications"] = []
        question["primary_type"] = "unclassified"
        return question

    classifications: List[Dict[str, str]] = []
    for type_name, patterns, description in QUESTION_TYPE_PATTERNS:
        for p in patterns:
            if p.search(raw):
                classifications.append({
                    "type": type_name,
                    "description": description,
                })
                break  # One match per type is enough

    # Primary type is the first non-genuine match, or genuine, or unclassified
    primary = "unclassified"
    for c in classifications:
        if c["type"] != "clarification_genuine":
            primary = c["type"]
            break
    if primary == "unclassified" and classifications:
        primary = classifications[0]["type"]

    question["classifications"] = classifications
    question["primary_type"] = primary
    return question


# ═══════════════════════════════════════════════════════════════
# SECTION 3: Undertone Detection
# ═══════════════════════════════════════════════════════════════

UNDERTONE_SIGNALS: List[Tuple[str, List[re.Pattern], float]] = [
    # (signal_name, patterns, weight)

    ("minimization_language", [
        re.compile(r"\bjust\b", re.IGNORECASE),
        re.compile(r"\bonly\b", re.IGNORECASE),
        re.compile(r"\bsimply\b", re.IGNORECASE),
        re.compile(r"\bmerely\b", re.IGNORECASE),
        re.compile(r"\bbasically\b", re.IGNORECASE),
    ], 0.10),

    ("vagueness_injection", [
        re.compile(r"\b(?:stuff|things?|and\s+(?:stuff|things?))\b", re.IGNORECASE),
        re.compile(r"\bwhatever\b", re.IGNORECASE),
        re.compile(r"\bor\s+whatever\b", re.IGNORECASE),
        re.compile(r"\band\s+(?:all\s+)?that\b", re.IGNORECASE),
    ], 0.15),

    ("feigned_casualness", [
        re.compile(r"\bjust\s+(?:curious|wondering|asking)\b", re.IGNORECASE),
        re.compile(r"\bout\s+of\s+curiosity\b", re.IGNORECASE),
        re.compile(r"\bby\s+the\s+way\b", re.IGNORECASE),
        re.compile(r"\brandom\s+question\b", re.IGNORECASE),
    ], 0.15),

    ("assumption_embed", [
        re.compile(r"\bso\s+(?:it|this|that)\s+(?:does|doesn't|can't|won't)\b", re.IGNORECASE),
        re.compile(r"\bI\s+(?:assume|suppose|guess|imagine)\b", re.IGNORECASE),
        re.compile(r"\bpresumably\b", re.IGNORECASE),
    ], 0.15),

    ("loaded_framing", [
        re.compile(r"\bstill\b", re.IGNORECASE),
        re.compile(r"\byet\b", re.IGNORECASE),
        re.compile(r"\beven\b", re.IGNORECASE),
        re.compile(r"\breally\b", re.IGNORECASE),
        re.compile(r"\bactually\b", re.IGNORECASE),
    ], 0.10),

    ("social_proof_probe", [
        re.compile(r"\bwho\s+(?:else|other)\b", re.IGNORECASE),
        re.compile(r"\banyone\s+(?:else|using|tried)\b", re.IGNORECASE),
        re.compile(r"\bother\s+(?:people|companies|clients|customers)\b", re.IGNORECASE),
        re.compile(r"\bwho\s+(?:has|is)\s+(?:using|bought|invested|tried)\b", re.IGNORECASE),
    ], 0.15),

    ("exit_construction", [
        re.compile(r"\bwhat\s+if\s+(?:it|this|that)\s+doesn'?t\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+happens\s+if\b", re.IGNORECASE),
        re.compile(r"\bhow\s+do\s+(?:I|we)\s+(?:get\s+out|exit|stop|cancel)\b", re.IGNORECASE),
    ], 0.10),

    ("control_transfer", [
        re.compile(r"\bcan\s+(?:I|we)\s+(?:see|look|review|check|verify)\b", re.IGNORECASE),
        re.compile(r"\bdo\s+(?:I|we)\s+(?:get|have)\s+(?:access|control)\b", re.IGNORECASE),
        re.compile(r"\bwho\s+(?:owns|controls|manages|runs)\b", re.IGNORECASE),
    ], 0.10),
]


def score_undertone(question: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score undertone signals in a question.
    Returns the question enriched with undertone data.
    """
    raw = question.get("raw", "")
    if not raw:
        question["undertone"] = {
            "score": 0.0,
            "signals": [],
            "band": "NEUTRAL",
        }
        return question

    signals: List[Dict[str, Any]] = []
    triggered_names: set = set()

    for signal_name, patterns, weight in UNDERTONE_SIGNALS:
        for p in patterns:
            if p.search(raw):
                if signal_name not in triggered_names:
                    triggered_names.add(signal_name)
                    signals.append({
                        "signal": signal_name,
                        "weight": weight,
                    })
                break

    total = sum(s["weight"] for s in signals)
    total = min(1.0, round(total, 4))

    # Band classification
    if total < 0.15:
        band = "NEUTRAL"
    elif total < 0.35:
        band = "LOW_UNDERTONE"
    elif total < 0.55:
        band = "MODERATE_UNDERTONE"
    elif total < 0.75:
        band = "HIGH_UNDERTONE"
    else:
        band = "PROBE_LIKELY"

    question["undertone"] = {
        "score": total,
        "signals": signals,
        "band": band,
    }
    return question


# ═══════════════════════════════════════════════════════════════
# SECTION 4: Redundancy & Repetition Analysis
# ═══════════════════════════════════════════════════════════════

def analyze_question_redundancy(questions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Across all questions in a text block, detect:
    - Same question asked multiple ways
    - Escalating reformulations of the same probe
    - Pattern of circling back to same topic
    """
    if len(questions) < 2:
        return {
            "redundancy_detected": False,
            "redundancy_score": 0.0,
            "clusters": [],
        }

    # Simple keyword overlap detection between questions
    def extract_content_words(text: str) -> set:
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "do", "does",
            "did", "will", "would", "could", "should", "can", "may",
            "might", "shall", "has", "have", "had", "be", "been",
            "being", "am", "i", "you", "we", "they", "it", "this",
            "that", "what", "how", "why", "when", "where", "who",
            "which", "and", "or", "but", "not", "no", "so", "if",
            "for", "to", "of", "in", "on", "at", "by", "with",
            "from", "about", "into", "just", "your", "my", "our",
        }
        words = set(re.findall(r'\b\w+\b', text.lower()))
        return words - stop_words

    clusters: List[Dict[str, Any]] = []
    paired: set = set()

    for i, q1 in enumerate(questions):
        if i in paired:
            continue
        words1 = extract_content_words(q1.get("raw", ""))
        if not words1:
            continue

        cluster_members = [i]
        for j, q2 in enumerate(questions):
            if j <= i or j in paired:
                continue
            words2 = extract_content_words(q2.get("raw", ""))
            if not words2:
                continue

            overlap = words1 & words2
            union = words1 | words2
            if union and len(overlap) / len(union) > 0.30:
                cluster_members.append(j)
                paired.add(j)

        if len(cluster_members) > 1:
            paired.update(cluster_members)
            clusters.append({
                "question_indices": cluster_members,
                "count": len(cluster_members),
                "shared_topic_words": sorted(list(
                    extract_content_words(questions[cluster_members[0]].get("raw", ""))
                )),
            })

    redundancy_score = 0.0
    if clusters:
        # More clusters + bigger clusters = higher score
        total_redundant = sum(c["count"] for c in clusters)
        redundancy_score = min(1.0, round(total_redundant / max(len(questions), 1) * 0.5, 4))

    return {
        "redundancy_detected": len(clusters) > 0,
        "redundancy_score": redundancy_score,
        "clusters": clusters,
    }


# ═══════════════════════════════════════════════════════════════
# SECTION 5: Question Necessity Scoring
# ═══════════════════════════════════════════════════════════════

def score_question_necessity(question: Dict[str, Any], context_text: str = "") -> Dict[str, Any]:
    """
    Score whether a question was structurally necessary given context.
    Factors:
    - Was information already provided that answers it?
    - Is the question about something the speaker should already know?
    - Does the question advance understanding or create positioning?
    """
    raw = question.get("raw", "")
    necessity_flags: List[str] = []
    score = 0.5  # Start neutral

    # If there's context and the question asks about something in context
    if context_text:
        q_words = set(re.findall(r'\b\w{4,}\b', raw.lower()))
        c_words = set(re.findall(r'\b\w{4,}\b', context_text.lower()))
        overlap = q_words & c_words
        if overlap and len(overlap) / max(len(q_words), 1) > 0.50:
            score -= 0.20
            necessity_flags.append("ANSWER_IN_CONTEXT")

    # Questions with undertone signals are less likely to be pure info-seeking
    undertone = question.get("undertone", {})
    if undertone.get("score", 0) > 0.35:
        score -= 0.15
        necessity_flags.append("UNDERTONE_PRESENT")

    # Innocence-framed questions are self-flagging
    primary_type = question.get("primary_type", "")
    if primary_type == "innocence_framed":
        score -= 0.10
        necessity_flags.append("SELF_DISCLAIMING")

    # Reduction probes are rarely about information
    if primary_type == "reduction_probe":
        score -= 0.15
        necessity_flags.append("REDUCTION_NOT_CLARIFICATION")

    # Anchor plants are positioning moves
    if primary_type == "anchor_plant":
        score -= 0.15
        necessity_flags.append("POSITIONING_MOVE")

    score = max(0.0, min(1.0, round(score, 4)))

    # Band
    if score >= 0.45:
        band = "LIKELY_NECESSARY"
    elif score >= 0.25:
        band = "POSSIBLY_UNNECESSARY"
    else:
        band = "PROBE_STRUCTURE"

    question["necessity"] = {
        "score": score,
        "flags": necessity_flags,
        "band": band,
    }
    return question


# ═══════════════════════════════════════════════════════════════
# SECTION 6: Trap Architecture Detection
# ═══════════════════════════════════════════════════════════════

TRAP_PATTERNS: List[Tuple[str, str, List[re.Pattern]]] = [
    ("binary_force", "Forces yes/no when the answer is nuanced", [
        re.compile(r"\b(?:does|is|can|will)\s+(?:it|this|that)\s+(?:work|do|handle)\b.*\bor\s+not\b", re.IGNORECASE),
        re.compile(r"\byes\s+or\s+no\b", re.IGNORECASE),
        re.compile(r"\bsimple\s+(?:yes|no)\b", re.IGNORECASE),
    ]),
    ("scope_overload", "Asks multiple things at once to create failure surface", [
        re.compile(r"\band\s+(?:also|what\s+about|how\s+about|does\s+it)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+about\s+.{5,}\s+and\s+.{5,}\s+and\b", re.IGNORECASE),
    ]),
    ("retroactive_standard", "Applies criteria that weren't part of original scope", [
        re.compile(r"\bbut\s+(?:does|can|will)\s+it\s+(?:also|even)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+about\s+(?:security|compliance|scale|enterprise)\b", re.IGNORECASE),
    ]),
    ("knowledge_asymmetry", "Asks question they already know the answer to", [
        re.compile(r"\bI\s+(?:already\s+)?know\s+(?:that|this|the\s+answer)\b", re.IGNORECASE),
        re.compile(r"\bI'?ve\s+(?:seen|heard|read)\b", re.IGNORECASE),
        re.compile(r"\bfrom\s+what\s+I(?:'ve)?\s+(?:seen|heard|read|understand)\b", re.IGNORECASE),
    ]),
    ("hypothetical_kill", "Uses hypothetical to introduce fatal scenario", [
        re.compile(r"\bwhat\s+if\s+(?:a|the|your)\s+(?:competitor|someone|google|microsoft|amazon)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+happens\s+when\s+(?:this|that|it)\s+(?:fails?|breaks?|stops?)\b", re.IGNORECASE),
        re.compile(r"\bwhat\s+if\s+(?:nobody|no\s+one)\b", re.IGNORECASE),
    ]),
    ("credibility_erosion", "Questions designed to chip at credibility incrementally", [
        re.compile(r"\bhow\s+(?:long|much)\s+(?:experience|time)\s+(?:do\s+you|have\s+you)\b", re.IGNORECASE),
        re.compile(r"\bwhat'?s?\s+your\s+(?:background|track\s+record|experience)\s+(?:in|with)\b", re.IGNORECASE),
        re.compile(r"\bhave\s+you\s+(?:ever|actually)\s+(?:built|run|managed|shipped)\b", re.IGNORECASE),
    ]),
]


def detect_trap_architecture(question: Dict[str, Any]) -> Dict[str, Any]:
    """
    Detect if a question contains trap architecture — structural
    features designed to create a lose-lose response surface.
    """
    raw = question.get("raw", "")
    if not raw:
        question["trap"] = {
            "detected": False,
            "patterns": [],
            "severity": "NONE",
        }
        return question

    detected_traps: List[Dict[str, str]] = []
    for trap_name, description, patterns in TRAP_PATTERNS:
        for p in patterns:
            if p.search(raw):
                detected_traps.append({
                    "trap": trap_name,
                    "description": description,
                })
                break

    severity = "NONE"
    if len(detected_traps) >= 3:
        severity = "HIGH"
    elif len(detected_traps) >= 2:
        severity = "MODERATE"
    elif len(detected_traps) >= 1:
        severity = "LOW"

    question["trap"] = {
        "detected": len(detected_traps) > 0,
        "patterns": detected_traps,
        "severity": severity,
    }
    return question


# ═══════════════════════════════════════════════════════════════
# SECTION 7: Composite Interrogative Score
# ═══════════════════════════════════════════════════════════════

def compute_interrogative_index(questions: List[Dict[str, Any]], redundancy: Dict[str, Any]) -> float:
    """
    Composite score across all questions.
    0.0 = all questions are genuine clarification
    1.0 = full probe architecture detected
    """
    if not questions:
        return 0.0

    components: List[float] = []

    # Average undertone
    undertone_scores = [q.get("undertone", {}).get("score", 0.0) for q in questions]
    avg_undertone = sum(undertone_scores) / len(undertone_scores) if undertone_scores else 0.0
    components.append(avg_undertone * 0.30)

    # Probe types present (non-genuine, non-unclassified)
    probe_types = set()
    for q in questions:
        pt = q.get("primary_type", "")
        if pt not in ("clarification_genuine", "unclassified"):
            probe_types.add(pt)
    type_diversity = min(1.0, len(probe_types) / 4.0)  # Normalize to ~4 types = max
    components.append(type_diversity * 0.25)

    # Trap detection
    trap_count = sum(1 for q in questions if q.get("trap", {}).get("detected", False))
    trap_ratio = trap_count / len(questions) if questions else 0.0
    components.append(trap_ratio * 0.20)

    # Necessity (inverted — lower necessity = higher probe score)
    necessity_scores = [q.get("necessity", {}).get("score", 0.5) for q in questions]
    avg_necessity = sum(necessity_scores) / len(necessity_scores) if necessity_scores else 0.5
    components.append((1.0 - avg_necessity) * 0.15)

    # Redundancy
    components.append(redundancy.get("redundancy_score", 0.0) * 0.10)

    total = sum(components)
    return min(1.0, round(total, 4))


# ═══════════════════════════════════════════════════════════════
# SECTION 8: Main Entry Point
# ═══════════════════════════════════════════════════════════════

def compute_interrogative_field(text: str, context: str = "") -> Dict[str, Any]:
    """
    Main entry point. Analyze all questions in a text block.

    Returns:
      interrogative_index: 0..1 (higher = more probe-structured)
      question_count: total questions detected
      questions: enriched question objects
      redundancy: cluster analysis
      summary_band: overall classification

    Integrates with middleware via the same pattern as edge_engine.
    """
    if not text or not text.strip():
        return {
            "field": "interrogative",
            "version": INTERROGATIVE_VERSION,
            "interrogative_index": 0.0,
            "question_count": 0,
            "questions": [],
            "redundancy": {
                "redundancy_detected": False,
                "redundancy_score": 0.0,
                "clusters": [],
            },
            "summary_band": "NO_QUESTIONS",
        }

    # Step 1: Extract
    questions = extract_questions(text)

    # Step 2: Classify each question
    for q in questions:
        classify_question(q)

    # Step 3: Score undertone
    for q in questions:
        score_undertone(q)

    # Step 4: Detect traps
    for q in questions:
        detect_trap_architecture(q)

    # Step 5: Score necessity
    for q in questions:
        score_question_necessity(q, context_text=context)

    # Step 6: Redundancy analysis
    redundancy = analyze_question_redundancy(questions)

    # Step 7: Composite index
    interrogative_index = compute_interrogative_index(questions, redundancy)

    # Step 8: Summary band
    if interrogative_index < 0.10:
        band = "GENUINE_INQUIRY"
    elif interrogative_index < 0.25:
        band = "LIGHT_PROBING"
    elif interrogative_index < 0.45:
        band = "STRUCTURED_PROBING"
    elif interrogative_index < 0.65:
        band = "ACTIVE_PROBE"
    else:
        band = "TRAP_ARCHITECTURE"

    return {
        "field": "interrogative",
        "version": INTERROGATIVE_VERSION,
        "interrogative_index": interrogative_index,
        "question_count": len(questions),
        "questions": questions,
        "redundancy": redundancy,
        "summary_band": band,
    }
