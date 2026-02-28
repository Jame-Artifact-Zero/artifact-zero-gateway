# safecheck_engine.py
# SafeCheck Observation Engine — Rule-based, No LLM
#
# Takes PRE-COMPUTED engine outputs from V1/Edge/L2.
# Returns observation cards with plain-English feedback + action labels.
# No imports from app.py. No circular dependency.
#
# The human learns to write better. The tool becomes unnecessary.
# That's the product.

from __future__ import annotations
import re
from typing import Any, Dict, List

SAFECHECK_VERSION = "safecheck-v1.0"

# ═══════════════════════════════════════════════════════════
# MARKER LISTS (SafeCheck-specific, human-outbound)
# ═══════════════════════════════════════════════════════════

SOFTENERS = [
    "hope", "wish", "just", "only", "might", "perhaps",
    "possibly", "kind of", "sort of", "i guess", "i think maybe",
    "it would be nice", "if you could maybe", "if that's okay"
]

INDIRECT_CONCERN = [
    "i hope", "i wish", "i was wondering", "it would be nice if",
    "i just feel like", "i feel like maybe", "i was thinking maybe"
]

WORRY_TRANSFERS = [
    "don't worry", "dont worry", "don't have to worry", "dont have to worry",
    "no need to worry", "shouldn't worry", "nothing to worry about",
    "you don't need to stress", "don't stress"
]

OPEN_ENDERS = [
    "what do you think", "what else do you think", "what do you want to do",
    "thoughts?", "how do you feel about", "up to you", "your call",
    "whatever you think", "let me know what you think", "idk what do you think"
]

PASSIVE_CLOSERS = [
    "just let me know", "whenever you get a chance", "no rush",
    "when you get around to it", "if you have time", "no pressure",
    "at your convenience", "whenever works"
]

APOLOGY_OPENERS = [
    "sorry to bother", "sorry if this", "i'm sorry but",
    "sorry to bring this up", "i don't mean to", "i hate to ask",
    "this might be dumb but", "i know this is a lot but"
]

REASSURANCE_MARKERS = [
    "don't worry", "no problem", "it's okay", "you got this",
    "rest assured", "glad", "happy to", "love that", "great that",
    "that's great", "that's awesome", "so happy"
]


def _split_sentences(text: str) -> List[str]:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t:
        return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if p.strip()]


def _contains_any(text_lower: str, markers: List[str]) -> List[str]:
    return [m for m in markers if m in text_lower]


# ═══════════════════════════════════════════════════════════
# OBSERVATION CARD GENERATOR
# ═══════════════════════════════════════════════════════════

def generate_observations(
    text: str,
    nii_result: Dict[str, Any],
    l2_result: Dict[str, Any],
    tilt_tags: List[str],
    edge_result: Dict[str, Any] = None,
) -> List[Dict[str, Any]]:
    """
    Generate observation cards from pre-computed engine signals.
    Each card: { text, action, source, priority, marker }
    Optional: { suggestion }
    
    Priority: 0 = clean, 1 = critical structure, 2 = important pattern, 3 = refinement
    """
    cards: List[Dict[str, Any]] = []
    t_lower = (text or "").lower()
    sents = _split_sentences(text)

    # Extract NII dimensions
    d1 = nii_result.get("d1_constraint_density", nii_result.get("q1", 0))
    d3 = nii_result.get("d3_enforcement_integrity", nii_result.get("q3", 0))
    detail = nii_result.get("detail", {})
    first_ask = detail.get("first_sent_has_ask", False)
    constraint_sents = detail.get("constraint_sents", 0)

    # L2 markers
    reassurances = l2_result.get("reassurance_markers", [])
    hedges = l2_result.get("hedge_markers", [])
    blends = l2_result.get("category_blend_markers", [])

    # ── PRIORITY 1: Structure ──

    if d1 == 0:
        cards.append({
            "text": "You didn't set any rules. The person reading this can interpret it however they want.",
            "suggestion": "Add what you need specifically. Dates, amounts, or conditions.",
            "action": "Add clarity", "source": "v1_d1", "priority": 1, "marker": None,
        })

    if not first_ask and len(sents) >= 2:
        cards.append({
            "text": "Your first sentence isn't a request. They may read it as a comment, not something that needs a response.",
            "action": "Make it direct", "source": "v1_d2", "priority": 1,
            "marker": sents[0] if sents else None,
        })

    if constraint_sents == 0 and d3 < 0.5:
        cards.append({
            "text": "No deadline or boundary. This can be responded to whenever — or never.",
            "suggestion": 'Add when you need it by. "By Friday" or "before we decide."',
            "action": "Add a timeline", "source": "v1_d3", "priority": 1, "marker": None,
        })

    # ── PRIORITY 2: Patterns ──

    # Reassurance before concern (sentence position)
    if len(sents) >= 2:
        s0 = sents[0].lower()
        s1_plus = " ".join(sents[1:]).lower()
        reassurance_in_s0 = any(m in s0 for m in REASSURANCE_MARKERS)
        concern_later = any(m in s1_plus for m in [
            "worry", "concern", "afraid", "scared", "hope", "need",
            "but", "however", "problem", "issue", "important"
        ])
        if reassurance_in_s0 and concern_later:
            cards.append({
                "text": "Your concern is after your support. They may stop processing after they hear you agree.",
                "action": "Make it direct", "source": "safecheck_position", "priority": 2,
                "marker": sents[0],
            })

    if "glad" in reassurances:
        cards.append({
            "text": 'You opened with "glad" — they may hear encouragement, not concern.',
            "action": "Strengthen", "source": "v1_l2_reassurance", "priority": 2, "marker": "glad",
        })

    # Softener: "hope"
    if "hope" in t_lower and "i hope" in t_lower:
        cards.append({
            "text": '"Hope" softens your ask. They may not hear it as something important to you.',
            "action": "Strengthen", "source": "safecheck_softener", "priority": 2, "marker": "hope",
        })

    # Softener: "just"
    if re.search(r"\bi\s+just\b", t_lower):
        cards.append({
            "text": '"Just" minimizes what you\'re saying. It tells them this isn\'t important.',
            "action": "Strengthen", "source": "safecheck_softener", "priority": 2, "marker": "just",
        })

    # Worry transfer
    worry_hits = _contains_any(t_lower, WORRY_TRANSFERS)
    if worry_hits:
        cards.append({
            "text": f'"{worry_hits[0]}" transfers the emotional load instead of stating what you need.',
            "action": "Make it direct", "source": "safecheck_worry", "priority": 2, "marker": worry_hits[0],
        })
    elif "worry" in t_lower:
        cards.append({
            "text": '"Worry" puts the weight on them instead of on the issue. State what you need.',
            "action": "Make it direct", "source": "safecheck_worry", "priority": 2, "marker": "worry",
        })

    # Indirect concern (skip if hope card already exists)
    indirect_hits = _contains_any(t_lower, INDIRECT_CONCERN)
    if indirect_hits and not any(c["source"] == "safecheck_softener" and c["marker"] == "hope" for c in cards):
        cards.append({
            "text": f'"{indirect_hits[0]}" is a wish, not a statement. Say what you need directly.',
            "action": "Make it direct", "source": "safecheck_indirect", "priority": 2, "marker": indirect_hits[0],
        })

    # Open-ended closer
    open_hits = _contains_any(t_lower, OPEN_ENDERS)
    if open_hits:
        cards.append({
            "text": f'"{open_hits[0]}" opens the door to anything. They may not address what matters to you.',
            "suggestion": "Ask the specific question you need answered.",
            "action": "Add clarity", "source": "safecheck_open", "priority": 2, "marker": open_hits[0],
        })

    # Passive closer
    passive_hits = _contains_any(t_lower, PASSIVE_CLOSERS)
    if passive_hits:
        cards.append({
            "text": f'"{passive_hits[0]}" signals this isn\'t urgent. If it is, say so.',
            "action": "Add a timeline", "source": "safecheck_passive", "priority": 2, "marker": passive_hits[0],
        })

    # Apology opener
    apology_hits = _contains_any(t_lower, APOLOGY_OPENERS)
    if apology_hits:
        cards.append({
            "text": f'You started with "{apology_hits[0]}" — this undermines what you\'re about to say.',
            "action": "Strengthen", "source": "safecheck_apology", "priority": 2, "marker": apology_hits[0],
        })

    # ── PRIORITY 2: V1 Tilts (adapted for outbound) ──

    tilt_cards = {
        "T1_REASSURANCE_DRIFT": ("You're reassuring instead of stating what you need.", "Strengthen"),
        "T3_CONSENSUS_CLAIMS": ('Phrases like "most people" or "everyone" weaken your point. Speak for yourself.', "Strengthen"),
        "T5_ABSOLUTE_LANGUAGE": ('"Always" or "never" without evidence invites pushback.', "Add clarity"),
        "T7_CATEGORY_BLEND": ('"Basically" or "sort of" blurs your meaning. Be specific.', "Add clarity"),
        "T8_PRESSURE_OPTIMIZATION": ("Urgency language without substance. They may feel pushed, not informed.", "Add clarity"),
    }
    for tag, (card_text, action) in tilt_cards.items():
        if tag in tilt_tags:
            cards.append({
                "text": card_text, "action": action, "source": "v1_tilt", "priority": 2, "marker": tag,
            })

    # ── PRIORITY 2: Edge Engine (relational patterns) ──

    if edge_result:
        markers = edge_result.get("edge_markers", [])
        triggered = edge_result.get("triggered_patterns", [])

        edge_cards = {
            "dominance_posture": ("tells them what to do. They may shut down before hearing why.", "Soften"),
            "escalation_syntax": ("escalates the temperature. State the issue without framing it as a confrontation.", "Soften"),
            "retroactive_attribution": ("assigns blame. Focus on what needs to happen next.", "Make it direct"),
            "amplification_vector": ("intensifies without adding substance. It can feel dismissive.", "Soften"),
            "vertical_claim": ("positions you above them. It invites defensiveness, not understanding.", "Soften"),
        }
        for pattern_key, (suffix, action) in edge_cards.items():
            if pattern_key in triggered:
                phrases = [m["phrase"] for m in markers if m["pattern"] == pattern_key]
                if phrases:
                    cards.append({
                        "text": f'"{phrases[0]}" {suffix}',
                        "action": action, "source": f"edge_{pattern_key}", "priority": 2, "marker": phrases[0],
                    })

    # ── PRIORITY 3: Refinements ──

    if hedges:
        hedge_str = ", ".join(f'"{h}"' for h in hedges[:3])
        cards.append({
            "text": f"Hedge words detected: {hedge_str}. These signal uncertainty.",
            "action": "Strengthen", "source": "v1_l2_hedge", "priority": 3, "marker": hedges[0],
        })

    if blends:
        blend_str = ", ".join(f'"{b}"' for b in blends[:2])
        cards.append({
            "text": f'{blend_str} blurs what you mean. Say the specific thing.',
            "action": "Add clarity", "source": "v1_l2_blend", "priority": 3, "marker": blends[0],
        })

    # ── CLEAN (no issues) ──

    if len(cards) == 0:
        cards.append({
            "text": "Your message is structurally clear. It says what it means.",
            "action": None, "source": "safecheck_clean", "priority": 0, "marker": None,
        })

    cards.sort(key=lambda c: (c["priority"], c["source"]))
    return cards
