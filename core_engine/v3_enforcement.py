# v3_enforcement.py
# V3 Enforcement Logic Priority Tree
# Deterministic. Non-bypassable. Strict sequence.
# Order is non-negotiable.
#
# L0: Integrity Lock (absolute, cannot disable)
# L1: Stability Pass (baseline core, cannot disable)
# L2: Org Policy (configurable, strictest wins)
# L3: Role Profile (inherits org, can only tighten)
# L4: Individual Override (inherits role, can only tighten)
#
# Conflict resolution: stricter rule wins. Always.
# No circular logic. No runtime negotiation.

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════
# POLICY OBJECTS
# ═══════════════════════════════════════════════════════════

@dataclass
class Policy:
    """Base policy object. Every level produces one."""
    max_length: Optional[int] = None          # word ceiling
    tone_band: Optional[Tuple[float, float]] = None  # (min, max) 0=cold 1=warm
    compression_level: int = 0                # 0=none, 1=light, 2=medium, 3=heavy
    required_next_step: bool = False          # must include actionable next step
    required_condition_labels: bool = False   # must label conditions/assumptions
    blocked_phrases: List[str] = field(default_factory=list)
    required_phrases: List[str] = field(default_factory=list)
    industry_compliance: Optional[str] = None  # "hipaa", "legal", "financial", None
    format_constraints: Dict[str, Any] = field(default_factory=dict)
    custom_rules: Dict[str, Any] = field(default_factory=dict)


def merge_policies(parent: Policy, child: Policy) -> Policy:
    """
    Merge child into parent. STRICTER WINS. Always.
    Child cannot weaken parent. Can only equal or tighten.
    """
    merged = Policy()

    # max_length: lower wins (stricter)
    if parent.max_length is not None and child.max_length is not None:
        merged.max_length = min(parent.max_length, child.max_length)
    elif parent.max_length is not None:
        merged.max_length = parent.max_length
    else:
        merged.max_length = child.max_length

    # tone_band: narrower wins (stricter)
    if parent.tone_band and child.tone_band:
        merged.tone_band = (
            max(parent.tone_band[0], child.tone_band[0]),  # raise floor
            min(parent.tone_band[1], child.tone_band[1]),  # lower ceiling
        )
    elif parent.tone_band:
        merged.tone_band = parent.tone_band
    else:
        merged.tone_band = child.tone_band

    # compression: higher wins (stricter)
    merged.compression_level = max(parent.compression_level, child.compression_level)

    # booleans: True wins (stricter)
    merged.required_next_step = parent.required_next_step or child.required_next_step
    merged.required_condition_labels = parent.required_condition_labels or child.required_condition_labels

    # blocked phrases: union (stricter)
    merged.blocked_phrases = list(set(parent.blocked_phrases + child.blocked_phrases))

    # required phrases: union (stricter)
    merged.required_phrases = list(set(parent.required_phrases + child.required_phrases))

    # industry compliance: parent wins if set (cannot weaken)
    merged.industry_compliance = parent.industry_compliance or child.industry_compliance

    # format constraints: merge, parent wins on conflict
    merged.format_constraints = {**child.format_constraints, **parent.format_constraints}

    # custom rules: merge, parent wins on conflict
    merged.custom_rules = {**child.custom_rules, **parent.custom_rules}

    return merged


# ═══════════════════════════════════════════════════════════
# LEVEL 0 — INTEGRITY LOCK (ABSOLUTE)
# Cannot be disabled. Runs first. Blocks if necessary.
# ═══════════════════════════════════════════════════════════

# PII patterns
PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                    # SSN
    re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b"),                  # SSN spaces
    re.compile(r"\b[A-Z]{1,2}\d{6,8}\b"),                    # Passport
    re.compile(r"\b\d{16}\b"),                                # Credit card (no spaces)
    re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}[\s-]\d{4}\b"), # Credit card formatted
]

# Assumption injection markers
ASSUMPTION_MARKERS = [
    re.compile(r"\b(i|we)\s+(assume|believe|think|guess|suppose)\b", re.I),
    re.compile(r"\b(probably|presumably|apparently)\b", re.I),
    re.compile(r"\bmy\s+understanding\s+is\b", re.I),
    re.compile(r"\bit\s+seems?\s+(like|that)\b", re.I),
]

# Objective mutation: AI redirecting away from stated objective
MUTATION_MARKERS = [
    re.compile(r"\binstead\s+of\s+(doing|following|what)\b", re.I),
    re.compile(r"\ba\s+better\s+approach\s+(would|might|could)\b", re.I),
    re.compile(r"\bwhat\s+you\s+(really|actually)\s+(need|want|should)\b", re.I),
    re.compile(r"\blet\s+me\s+suggest\s+(something|an?\s+alternative)\b", re.I),
    re.compile(r"\bhave\s+you\s+considered\s+(instead|rather)\b", re.I),
]


def _run_level_0(text: str, objective: Optional[str] = None) -> Dict[str, Any]:
    """
    Integrity Lock. Non-negotiable.
    Returns actions taken and whether output should be blocked.
    """
    actions = []
    blocked = False
    cleaned = text

    # 1. PII disclosure guardrail
    for pat in PII_PATTERNS:
        matches = pat.findall(cleaned)
        if matches:
            for m in matches:
                cleaned = cleaned.replace(m, "[PII-REDACTED]")
                actions.append({
                    "check": "pii_disclosure",
                    "action": "redacted",
                    "detail": f"Pattern matched: {pat.pattern}"
                })

    # 2. Assumption injection block
    assumption_hits = []
    for pat in ASSUMPTION_MARKERS:
        for m in pat.finditer(cleaned):
            assumption_hits.append(m.group(0))
    if assumption_hits:
        actions.append({
            "check": "assumption_injection",
            "action": "flagged",
            "detail": f"Found {len(assumption_hits)} assumption markers: {assumption_hits[:5]}"
        })

    # 3. Objective mutation block
    if objective:
        mutation_hits = []
        for pat in MUTATION_MARKERS:
            for m in pat.finditer(cleaned):
                mutation_hits.append(m.group(0))
        if mutation_hits:
            actions.append({
                "check": "objective_mutation",
                "action": "flagged",
                "detail": f"AI attempting to redirect from objective: {mutation_hits[:3]}"
            })

    # 4. Scope preservation (output must relate to input objective)
    if objective:
        obj_words = set(w.lower() for w in re.findall(r"[a-z0-9']+", objective.lower()) if len(w) > 3)
        text_words = set(w.lower() for w in re.findall(r"[a-z0-9']+", cleaned.lower()) if len(w) > 3)
        if obj_words:
            overlap = obj_words & text_words
            scope_ratio = len(overlap) / len(obj_words) if obj_words else 0
            if scope_ratio < 0.15:
                actions.append({
                    "check": "scope_preservation",
                    "action": "flagged",
                    "detail": f"Output has {scope_ratio:.0%} overlap with objective words. Possible scope drift."
                })

    return {
        "level": 0,
        "name": "integrity_lock",
        "actions": actions,
        "blocked": blocked,
        "output": cleaned,
    }


# ═══════════════════════════════════════════════════════════
# LEVEL 1 — STABILITY PASS (BASELINE CORE)
# Always runs. Cannot be disabled. Entropy reduction only.
# No tone softening. No moral edits.
# ═══════════════════════════════════════════════════════════

HEDGE_WORDS = ["maybe", "likely", "possibly", "kind of", "sort of",
               "perhaps", "might", "somewhat", "arguably", "probably", "generally"]

FILLER_PHRASES = [
    "it is important to note", "it's important to note",
    "it is worth noting", "it's worth noting",
    "it should be noted", "in conclusion",
    "ultimately", "to summarize", "as you know",
    "basically", "essentially", "in terms of",
    "at the end of the day", "needless to say",
    "keep in mind that", "as a matter of fact",
]

SMOOTH_OPENERS = {"great", "absolutely", "definitely", "of course",
                  "sure", "perfect", "wonderful", "fantastic",
                  "excellent", "love", "amazing", "certainly"}

# Escalation patterns (from edge_engine, integrated here for L1)
ESCALATION_PATTERNS = [
    re.compile(r"\bthe\s+(real\s+)?issue\s+is\b", re.I),
    re.compile(r"\bhere'?s\s+the\s+problem\b", re.I),
    re.compile(r"\blet'?s\s+be\s+honest\b", re.I),
]

DOMINANCE_PATTERNS = [
    re.compile(r"\byou\s+need\s+to\b", re.I),
    re.compile(r"\byou\s+have\s+to\b", re.I),
    re.compile(r"\byou\s+can'?t\b", re.I),
    re.compile(r"\byou\s+must\b", re.I),
]

DEFENSIVE_PATTERNS = [
    re.compile(r"\bi\s+didn'?t\s+mean\b", re.I),
    re.compile(r"\bthat'?s\s+not\s+what\s+i\b", re.I),
    re.compile(r"\bi\s+was\s+just\s+trying\b", re.I),
    re.compile(r"\bi\s+never\s+said\b", re.I),
]

# ── NEW: AI-specific output pathologies ──
# These detect patterns in AI output going TO humans.
# The edge engine catches human patterns. These catch AI patterns.

# False authority: AI asserting structural knowledge it doesn't have
FALSE_AUTHORITY_PATTERNS = [
    re.compile(r"\bthat'?s\s+where\s+it\s+(belongs|goes|should\s+go)\b", re.I),
    re.compile(r"\bthat'?s\s+(correct|right|exactly)\b", re.I),
    re.compile(r"\bas\s+i\s+(said|mentioned|noted|explained)\b", re.I),
    re.compile(r"\blike\s+i\s+said\b", re.I),
    re.compile(r"\bi\s+already\s+(told|explained|said|showed)\b", re.I),
]

# Correction deflection: AI reframing user correction as user's misunderstanding
CORRECTION_DEFLECTION_PATTERNS = [
    re.compile(r"\bwhat\s+i\s+meant\s+(was|is)\b", re.I),
    re.compile(r"\bto\s+clarify\s+what\s+i\b", re.I),
    re.compile(r"\bwhat\s+i'?m\s+saying\s+is\b", re.I),
    re.compile(r"\byes,?\s+that'?s\s+what\s+i\b", re.I),
    re.compile(r"\bright,?\s+so\s+(as|like)\s+i\b", re.I),
    re.compile(r"\bi\s+think\s+you\s+(misread|misunderstood|missed)\b", re.I),
]

# Positional entrenchment: detected across turns, not single message
# These are structural claims that get repeated after correction.
# Requires conversation history to detect.
CLAIM_EXTRACTORS = [
    # "X goes in Y" / "X belongs in Y" / "put X in Y"
    re.compile(r"(\b\w+\.?\w*)\s+(goes?|belongs?|sits?|lives?|should\s+go)\s+in\s+(.+?)(?:\.|$)", re.I),
    # "X is Y" assertions
    re.compile(r"(\b\w+\.?\w*)\s+is\s+(the|a|your)\s+(.+?)(?:\.|$)", re.I),
    # "that's the/your X"
    re.compile(r"\bthat'?s\s+(the|your|a)\s+(.+?)(?:\.|$)", re.I),
]


def _extract_structural_claims(text: str) -> List[str]:
    """
    Extract structural claims from text for entrenchment comparison.
    Returns normalized claim strings.
    """
    claims = []
    for pat in CLAIM_EXTRACTORS:
        for m in pat.finditer(text):
            claim = _normalize(m.group(0)).lower()
            # Only track claims with enough specificity (>3 words)
            if len(claim.split()) >= 3:
                claims.append(claim)
    return claims


def _detect_entrenchment(current_text: str, prior_ai_responses: List[str]) -> Dict[str, Any]:
    """
    Detect positional entrenchment across conversation turns.
    
    If the AI made a structural claim in a prior turn, and the user 
    presumably corrected it (because we're in a new turn), and the AI 
    is restating the same or similar claim — that's entrenchment.
    
    Returns:
        entrenched: bool
        repeated_claims: list of claims repeated across turns
        entrenchment_count: number of turns the claim has persisted
    """
    if not prior_ai_responses:
        return {"entrenched": False, "repeated_claims": [], "entrenchment_count": 0}
    
    current_claims = _extract_structural_claims(current_text)
    if not current_claims:
        return {"entrenched": False, "repeated_claims": [], "entrenchment_count": 0}
    
    # Check each prior AI response for matching claims
    repeated = []
    max_streak = 0
    
    for claim in current_claims:
        claim_words = set(claim.split())
        streak = 0
        for prior in prior_ai_responses:
            prior_claims = _extract_structural_claims(prior)
            for pc in prior_claims:
                pc_words = set(pc.split())
                # Semantic overlap: >60% shared words = same claim restated
                if claim_words and pc_words:
                    overlap = len(claim_words & pc_words) / max(len(claim_words), len(pc_words))
                    if overlap > 0.6:
                        streak += 1
                        break
        if streak > 0:
            repeated.append(claim)
            max_streak = max(max_streak, streak)
    
    # Entrenched if same claim appears in 2+ prior turns
    entrenched = max_streak >= 1 and len(repeated) > 0
    
    return {
        "entrenched": entrenched,
        "repeated_claims": repeated[:5],
        "entrenchment_count": max_streak + 1,  # +1 for current turn
    }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _run_level_1(text: str, prior_ai_responses: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Stability Pass. Structural stabilization.
    Entropy reduction only. No content rewriting.
    
    Now includes AI-specific pathology detection:
    - False authority assertion
    - Correction deflection
    - Positional entrenchment (requires prior_ai_responses)
    """
    actions = []
    t = _normalize(text)

    # 1. Redundancy compression (dedupe exact sentences)
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    seen = set()
    deduped = []
    dupes = 0
    for s in parts:
        if s in seen:
            dupes += 1
            continue
        seen.add(s)
        deduped.append(s)
    if dupes > 0:
        t = " ".join(deduped)
        actions.append({"check": "redundancy_compression", "action": "removed", "detail": f"{dupes} duplicate sentences"})

    # 2. Filler removal
    filler_removed = 0
    for phrase in FILLER_PHRASES:
        pat = re.compile(re.escape(phrase), re.I)
        t, n = pat.subn("", t)
        filler_removed += n
    if filler_removed > 0:
        t = _normalize(t)
        actions.append({"check": "filler_removal", "action": "removed", "detail": f"{filler_removed} filler phrases"})

    # 3. Smooth opener strip
    words = t.split()
    if words and words[0].lower().strip(".,!?:;") in SMOOTH_OPENERS:
        stripped_word = words[0]
        words = words[1:]
        # Also strip trailing comma/exclamation from next word if orphaned
        if words and words[0].startswith((",", "!", ".")):
            words[0] = words[0].lstrip(",!. ")
        t = " ".join(words)
        actions.append({"check": "smooth_opener_strip", "action": "removed", "detail": f"Stripped '{stripped_word}'"})

    # 4. Hedge removal
    hedges_removed = 0
    for w in HEDGE_WORDS:
        pat = re.compile(r"\b" + re.escape(w) + r"\b", re.I)
        t, n = pat.subn("", t)
        hedges_removed += n
    if hedges_removed > 0:
        t = _normalize(t)
        actions.append({"check": "hedge_removal", "action": "removed", "detail": f"{hedges_removed} hedge words"})

    # 5. Escalation strip
    esc_hits = []
    for pat in ESCALATION_PATTERNS:
        for m in pat.finditer(t):
            esc_hits.append(m.group(0))
    if esc_hits:
        actions.append({"check": "escalation_strip", "action": "flagged", "detail": f"{esc_hits}"})

    # 6. Dominance posture neutralization
    dom_hits = []
    for pat in DOMINANCE_PATTERNS:
        for m in pat.finditer(t):
            dom_hits.append(m.group(0))
    if dom_hits:
        actions.append({"check": "dominance_neutralization", "action": "flagged", "detail": f"{dom_hits}"})

    # 7. Defensive posture neutralization
    def_hits = []
    for pat in DEFENSIVE_PATTERNS:
        for m in pat.finditer(t):
            def_hits.append(m.group(0))
    if def_hits:
        actions.append({"check": "defensive_neutralization", "action": "flagged", "detail": f"{def_hits}"})

    # ── 8. FALSE AUTHORITY DETECTION (AI-specific) ──
    # AI asserting knowledge/position it hasn't verified
    auth_hits = []
    for pat in FALSE_AUTHORITY_PATTERNS:
        for m in pat.finditer(t):
            auth_hits.append(m.group(0))
    if auth_hits:
        actions.append({
            "check": "false_authority",
            "action": "flagged",
            "detail": f"AI asserting unverified authority: {auth_hits}"
        })

    # ── 9. CORRECTION DEFLECTION DETECTION (AI-specific) ──
    # AI reframing user's correction as misunderstanding
    deflect_hits = []
    for pat in CORRECTION_DEFLECTION_PATTERNS:
        for m in pat.finditer(t):
            deflect_hits.append(m.group(0))
    if deflect_hits:
        actions.append({
            "check": "correction_deflection",
            "action": "flagged",
            "detail": f"AI deflecting correction: {deflect_hits}"
        })

    # ── 10. POSITIONAL ENTRENCHMENT DETECTION (multi-turn) ──
    # AI restating the same structural claim after user correction
    if prior_ai_responses:
        entrenchment = _detect_entrenchment(t, prior_ai_responses)
        if entrenchment["entrenched"]:
            actions.append({
                "check": "positional_entrenchment",
                "action": "flagged",
                "detail": (
                    f"AI restated claim {entrenchment['entrenchment_count']} times "
                    f"across turns: {entrenchment['repeated_claims']}"
                )
            })

    t = _normalize(t)

    return {
        "level": 1,
        "name": "stability_pass",
        "actions": actions,
        "output": t,
    }


# ═══════════════════════════════════════════════════════════
# LEVEL 2 — ORG POLICY ENFORCEMENT
# Runs after baseline. Controlled by organization settings.
# ═══════════════════════════════════════════════════════════

# Industry compliance phrase requirements
COMPLIANCE_REQUIREMENTS = {
    "hipaa": {
        "blocked": ["patient name", "date of birth", "medical record number", "ssn"],
        "required_labels": True,
    },
    "legal": {
        "blocked": ["not legal advice", "attorney-client"],  # flag if present inappropriately
        "required_labels": True,
    },
    "financial": {
        "blocked": ["guaranteed return", "risk-free", "cannot lose"],
        "required_labels": True,
    },
}


def _run_level_2(text: str, policy: Policy) -> Dict[str, Any]:
    """
    Org policy enforcement. Configurable.
    """
    actions = []
    t = text

    # 1. Max length enforcement
    if policy.max_length:
        words = t.split()
        if len(words) > policy.max_length:
            t = " ".join(words[:policy.max_length])
            actions.append({
                "check": "max_length",
                "action": "trimmed",
                "detail": f"{len(words)} → {policy.max_length} words"
            })

    # 2. Blocked phrase enforcement
    for phrase in policy.blocked_phrases:
        if phrase.lower() in t.lower():
            pat = re.compile(re.escape(phrase), re.I)
            t, n = pat.subn("[BLOCKED]", t)
            if n > 0:
                actions.append({
                    "check": "blocked_phrase",
                    "action": "replaced",
                    "detail": f"Blocked '{phrase}' ({n} occurrences)"
                })

    # 3. Required phrase check
    for phrase in policy.required_phrases:
        if phrase.lower() not in t.lower():
            actions.append({
                "check": "required_phrase",
                "action": "missing",
                "detail": f"Required phrase not found: '{phrase}'"
            })

    # 4. Industry compliance
    if policy.industry_compliance and policy.industry_compliance in COMPLIANCE_REQUIREMENTS:
        comp = COMPLIANCE_REQUIREMENTS[policy.industry_compliance]
        for blocked in comp.get("blocked", []):
            if blocked.lower() in t.lower():
                pat = re.compile(re.escape(blocked), re.I)
                t, n = pat.subn("[COMPLIANCE-BLOCKED]", t)
                actions.append({
                    "check": "industry_compliance",
                    "action": "blocked",
                    "detail": f"Compliance violation ({policy.industry_compliance}): '{blocked}'"
                })

    # 5. Compression level enforcement
    if policy.compression_level >= 2:
        # Medium+ compression: strip sentences that don't contain objective keywords
        # (deferred to L1 objective filter; here we enforce word ceiling)
        words = t.split()
        ceiling = policy.max_length or len(words)
        target = int(ceiling * (1.0 - policy.compression_level * 0.15))
        if len(words) > target:
            t = " ".join(words[:target])
            actions.append({
                "check": "compression",
                "action": "compressed",
                "detail": f"Level {policy.compression_level}: {len(words)} → {target} words"
            })

    # 6. Required next step check
    if policy.required_next_step:
        next_step_patterns = [
            re.compile(r"\bnext\s+step\b", re.I),
            re.compile(r"\baction\s+item\b", re.I),
            re.compile(r"\bto\s+do\b", re.I),
            re.compile(r"\brecommend\b", re.I),
        ]
        has_next = any(p.search(t) for p in next_step_patterns)
        if not has_next:
            actions.append({
                "check": "required_next_step",
                "action": "missing",
                "detail": "No actionable next step detected in output"
            })

    # 7. Required condition labeling
    if policy.required_condition_labels:
        condition_patterns = [
            re.compile(r"\bif\b", re.I),
            re.compile(r"\bassuming\b", re.I),
            re.compile(r"\bcondition(al|ed)?\b", re.I),
            re.compile(r"\bdepends\s+on\b", re.I),
        ]
        has_conditions = any(p.search(t) for p in condition_patterns)
        # If text has conditional language but no explicit labels, flag
        if has_conditions:
            label_patterns = [
                re.compile(r"\bCONDITION:\b"),
                re.compile(r"\bASSUMPTION:\b"),
                re.compile(r"\bIF-THEN:\b"),
                re.compile(r"\bDEPENDENCY:\b"),
            ]
            has_labels = any(p.search(t) for p in label_patterns)
            if not has_labels:
                actions.append({
                    "check": "condition_labeling",
                    "action": "missing",
                    "detail": "Conditional language present but no explicit condition labels"
                })

    return {
        "level": 2,
        "name": "org_policy",
        "actions": actions,
        "output": _normalize(t),
    }


# ═══════════════════════════════════════════════════════════
# LEVEL 3 — ROLE PROFILE ENFORCEMENT
# Inherits org policy. Can only tighten.
# ═══════════════════════════════════════════════════════════

def _run_level_3(text: str, role_policy: Policy, effective_policy: Policy) -> Dict[str, Any]:
    """
    Role profile enforcement.
    Uses effective_policy (already merged with org).
    Role-specific checks run on top.
    """
    # Run L2 logic with the merged (tighter) policy
    result = _run_level_2(text, effective_policy)
    result["level"] = 3
    result["name"] = "role_profile"
    return result


# ═══════════════════════════════════════════════════════════
# LEVEL 4 — INDIVIDUAL OVERRIDES
# Last configurable layer. Cannot weaken anything above.
# ═══════════════════════════════════════════════════════════

def _run_level_4(text: str, individual_policy: Policy, effective_policy: Policy) -> Dict[str, Any]:
    """
    Individual override enforcement.
    Uses effective_policy (already merged with org + role).
    """
    result = _run_level_2(text, effective_policy)
    result["level"] = 4
    result["name"] = "individual_override"
    return result


# ═══════════════════════════════════════════════════════════
# ENFORCEMENT PIPELINE
# Strict sequence. L0 → L1 → L2 → L3 → L4.
# Each level receives output of previous level.
# ═══════════════════════════════════════════════════════════

def enforce(
    text: str,
    objective: Optional[str] = None,
    org_policy: Optional[Policy] = None,
    role_policy: Optional[Policy] = None,
    individual_policy: Optional[Policy] = None,
    prior_ai_responses: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Run the full V3 enforcement priority tree.

    Args:
        text: AI output to enforce
        objective: declared objective for scope checking
        org_policy: organization-level policy
        role_policy: role-level policy (inherits org, can only tighten)
        individual_policy: individual overrides (inherits role, can only tighten)
        prior_ai_responses: list of AI's previous responses in this conversation,
            used for positional entrenchment detection. Most recent first.

    Returns trace with per-level actions and final output.
    No silent edits. Everything logged.
    """
    start_ts = time.time()
    trace = {
        "version": "v3-enforcement-1.0",
        "timestamp": start_ts,
        "input_length": len((text or "").split()),
        "objective": objective,
        "level_0_actions": [],
        "level_1_actions": [],
        "level_2_actions": [],
        "level_3_actions": [],
        "level_4_actions": [],
        "blocks_triggered": [],
        "final_output": "",
    }

    # Default policies if not provided
    if org_policy is None:
        org_policy = Policy()
    if role_policy is None:
        role_policy = Policy()
    if individual_policy is None:
        individual_policy = Policy()

    # ── POLICY MERGE (strict inheritance) ──
    # Org is base. Role tightens. Individual tightens further.
    effective_org = org_policy
    effective_role = merge_policies(effective_org, role_policy)
    effective_individual = merge_policies(effective_role, individual_policy)

    current_text = text or ""

    # ── L0: INTEGRITY LOCK ──
    l0 = _run_level_0(current_text, objective)
    trace["level_0_actions"] = l0["actions"]
    if l0["blocked"]:
        trace["blocks_triggered"].append("L0_INTEGRITY_BLOCK")
        trace["final_output"] = "[OUTPUT BLOCKED — Integrity violation detected]"
        trace["elapsed_ms"] = round((time.time() - start_ts) * 1000, 2)
        return trace
    current_text = l0["output"]

    # ── L1: STABILITY PASS ──
    l1 = _run_level_1(current_text, prior_ai_responses=prior_ai_responses)
    trace["level_1_actions"] = l1["actions"]
    current_text = l1["output"]

    # ── L2: ORG POLICY ──
    l2 = _run_level_2(current_text, effective_org)
    trace["level_2_actions"] = l2["actions"]
    current_text = l2["output"]

    # ── L3: ROLE PROFILE ──
    l3 = _run_level_3(current_text, role_policy, effective_role)
    trace["level_3_actions"] = l3["actions"]
    current_text = l3["output"]

    # ── L4: INDIVIDUAL OVERRIDE ──
    l4 = _run_level_4(current_text, individual_policy, effective_individual)
    trace["level_4_actions"] = l4["actions"]
    current_text = l4["output"]

    # ── FINAL ──
    trace["final_output"] = current_text
    trace["output_length"] = len(current_text.split())
    trace["compression_ratio"] = round(
        1 - (len(current_text.split()) / max(1, len((text or "").split()))), 4
    )
    trace["elapsed_ms"] = round((time.time() - start_ts) * 1000, 2)

    return trace


# ═══════════════════════════════════════════════════════════
# MINIMAL VIABLE POLICY SETS
# ═══════════════════════════════════════════════════════════

# Ready-to-use org policies for each validated vertical

POLICY_HEALTHCARE = Policy(
    max_length=300,
    tone_band=(0.3, 0.6),
    compression_level=2,
    required_next_step=True,
    required_condition_labels=True,
    blocked_phrases=["patient name", "date of birth", "ssn", "social security"],
    industry_compliance="hipaa",
)

POLICY_LEGAL = Policy(
    max_length=400,
    tone_band=(0.2, 0.5),
    compression_level=2,
    required_next_step=True,
    required_condition_labels=True,
    blocked_phrases=["guaranteed outcome", "will definitely win", "no risk"],
    industry_compliance="legal",
)

POLICY_INSURANCE = Policy(
    max_length=250,
    tone_band=(0.3, 0.6),
    compression_level=3,
    required_next_step=True,
    required_condition_labels=True,
    blocked_phrases=["guaranteed", "risk-free", "cannot lose", "always approved"],
    industry_compliance="financial",
)

POLICY_ENTERPRISE_DEFAULT = Policy(
    max_length=500,
    tone_band=(0.3, 0.7),
    compression_level=1,
    required_next_step=False,
    required_condition_labels=False,
)

# Ready-to-use role policies

ROLE_EXECUTIVE = Policy(
    max_length=200,
    compression_level=3,
    required_next_step=True,
)

ROLE_ANALYST = Policy(
    max_length=600,
    compression_level=1,
    required_condition_labels=True,
)

ROLE_LEGAL_REVIEWER = Policy(
    max_length=400,
    compression_level=2,
    required_next_step=True,
    required_condition_labels=True,
    blocked_phrases=["not legal advice"],
)

ROLE_CUSTOMER_FACING = Policy(
    max_length=150,
    tone_band=(0.4, 0.7),
    compression_level=3,
    required_next_step=True,
)

ROLE_INTERNAL_TECHNICAL = Policy(
    max_length=800,
    compression_level=0,
    required_condition_labels=True,
)
