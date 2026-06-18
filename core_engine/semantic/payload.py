"""Atomic semantic payload tagging."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Counter as CounterType, Dict, List, Tuple

from .common import make_signal_envelope
from . import tags


@dataclass
class AtomicStatement:
    seq: int
    statement: str
    tag: str
    matched_rule: str


FILLER_PREFIX_RE = re.compile(
    r"^(and\s+|but\s+|so\s+|yeah,?\s+|man,?\s+|like,?\s+|i mean,?\s+)+",
    re.IGNORECASE,
)

PAYLOAD_RULES: List[Tuple[str, str, str]] = [
    (tags.AFFILIATION, "love_or_thinking_about_you", r"\b(i love you|love you|thinking about you|care about you)\b"),
    (tags.SUPPORT, "support_offer", r"\b(be here for you|here for you for everything|support you)\b"),
    (tags.CONCERN, "hope_okay", r"\b(i hope you'?re doing okay|hope you are doing okay|hope you'?re ok)\b"),
    (tags.DURATION, "thinking_duration", r"\bsince before the trip\b|\bafter the trip\b|\ba lot after\b"),
    (tags.ASSUMPTION, "assumed_response_or_contact", r"\bprobably wouldn'?t answer\b|\bmaybe wouldn'?t see\b|\bprobably won'?t\b"),
    (tags.MESSAGE_ACTION, "message_action", r"\b(send(?:ing)? (?:you )?(?:this )?(?:voice |video )?message|wanted to send|acknowledging it now)\b"),
    (tags.EXPECTATION, "looking_forward", r"\b(looking forward|wanted to hang out|psyched to see you)\b"),
    (tags.POSITIVE_EVALUATION, "positive_eval", r"\b(amazing trip|enjoyed talking|trooper|she'?s great|great pics|great)\b"),
    (tags.SELF_STATE, "speaker_state", r"\b(it killed me|i was concerned|i was bothered|i got annoyed|i was annoyed|what the heck)\b"),
    (tags.JUDGMENT, "negative_eval_or_role_label", r"\b(absent brother|absent teammate|absent member|weren'?t our brother|not our brother|talking to a wall|weird|strange|super quiet|didn'?t do (?:a )?whole lot|super off|you were off|you were kind of absent|you were effectively absent)\b"),
    (tags.DISSATISFACTION, "wish_different_better_fun", r"\b(i wish (?:it|we|that it) (?:would have been|could have been|had been)|better trip|more fun|could have been different)\b"),
    (tags.FUTURE_EXPECTATION, "next_time_expectation", r"\b(next time|don'?t want the next time|to not be like this|be like this)\b"),
    (tags.CONDITION_PROMPT, "condition_prompt", r"\b(if (?:there'?s|there is|something is|something'?s) (?:something )?(?:wrong|up)|if you have something going on|you got something going on)\b"),
    (tags.SELF_ACCOUNTABILITY_PROMPT, "speaker_accountability_prompt", r"\b(if i did something|if i did something.*what it is)\b"),
    (tags.INVITATION, "talk_invitation", r"\b(want to talk|like to talk|love to talk|talk about it|talk soon)\b"),
    (tags.ACTION_REPORT, "tried_talking", r"\b(tried talking to you|try talking to you)\b"),
    (tags.ANTICIPATED_REACTION, "anticipated_reaction", r"\b(you'?re probably going to hate hearing this|probably going to hate hearing this)\b"),
    (tags.CARE_FRAME, "out_of_love", r"\b(out of love|say it out of love)\b"),
    (tags.DELAY_JUSTIFICATION, "delay_justification", r"\b(if i (?:had )?addressed it|addressing it there|wouldn'?t go well|wouldn'?t be great)\b"),
    (tags.SCARCITY, "scarcity", r"\b(don'?t get (?:a lot of|many) opportunities|not a lot of opportunities)\b"),
    (tags.UNCERTAINTY, "dont_know", r"\b(i don'?t know what|i don'?t know what'?s up|don'?t know what it was)\b"),
    (tags.FUTURE_CONTACT, "future_contact", r"\b(i'?ll talk to you later|talk to you later)\b"),
]


def clean_statement(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.strip(" ,;:-")


def strip_filler_prefix(text: str) -> str:
    return FILLER_PREFIX_RE.sub("", (text or "").strip()).strip()


def split_payload_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [clean_statement(p) for p in parts if clean_statement(p)]


def split_known_payload_compounds(text: str) -> List[str]:
    t = clean_statement(text)
    if not t:
        return []

    compound_patterns = [
        r"^(i was bothered) that (.+)$",
        r"^(i was concerned) (you .+)$",
        r"^(i wanted to send(?: you)?(?: this| a message| this message)?)(?: because )(.+)$",
        r"^(i say it) because (i care about you.*)$",
        r"^(i love you)\s+so\s+(.+)$",
        r"^(i don'?t know what it was) that (you were .+)$",
    ]
    for pattern in compound_patterns:
        m = re.match(pattern, t, flags=re.IGNORECASE)
        if m:
            return [g for g in m.groups() if g]
    return [t]


def split_payload_atomic(sentence: str) -> List[str]:
    s = clean_statement(sentence)
    if not s:
        return []

    work = s
    split_patterns = [
        r"\s+and\s+(?=I\s|I'm\s|I\'m\s|you\s|you\'re\s|you're\s|we\s|it\s|if\s|hopefully\s|maybe\s|but\s)",
        r"\s+but\s+(?=I\s|I'm\s|I\'m\s|you\s|you\'re\s|you're\s|we\s|it\s|if\s|hopefully\s|maybe\s)",
        r"\s+so,?\s+(?=if\s|I'm\s|I\s|hopefully\s)",
        r",\s+(?=I\s(?:was|am|got|wanted|wish|hope|thought|think|knew|say|care|love|enjoyed)\b)",
        r",\s+(?=you\s(?:were|did|went|want|got|have)\b)",
        r",\s+(?=we\s(?:had|don't|do not)\b)",
        r",\s+(?=if\s)",
    ]
    for pattern in split_patterns:
        work = re.sub(pattern, " || ", work, flags=re.IGNORECASE)

    pieces = [clean_statement(p) for p in work.split("||") if clean_statement(p)]
    final: List[str] = []
    for piece in pieces:
        final.extend(split_known_payload_compounds(piece))
    return [clean_statement(x) for x in final if clean_statement(x)]


def classify_payload_statement(statement: str) -> Tuple[str, str]:
    raw = clean_statement(statement)
    text = strip_filler_prefix(raw).lower()
    for tag, name, pattern in PAYLOAD_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return tag, name
    return tags.UNCLASSIFIED, "no_rule_match"


def detect_semantic_payload_atoms(text: str) -> Tuple[List[AtomicStatement], CounterType[str]]:
    atoms: List[AtomicStatement] = []
    for sentence in split_payload_sentences(text):
        for atomic in split_payload_atomic(sentence):
            tag, rule = classify_payload_statement(atomic)
            atoms.append(AtomicStatement(len(atoms) + 1, atomic, tag, rule))
    counts = Counter(a.tag for a in atoms)
    return atoms, counts


def detect_semantic_payloads(text: str, input_type: str = "unknown") -> Dict[str, Any]:
    atoms, counts = detect_semantic_payload_atoms(text or "")
    evidence = [a.statement for a in atoms if a.tag != tags.UNCLASSIFIED][:25]
    total = max(len(atoms), 1)
    classified = sum(count for tag, count in counts.items() if tag != tags.UNCLASSIFIED)
    dominant_payload = counts.most_common(1)[0][0] if counts else None

    return make_signal_envelope(
        tool="detect_semantic_payloads",
        input_type=input_type,
        signal="SEMANTIC_PAYLOADS_DETECTED" if classified else "NO_SEMANTIC_PAYLOADS_DETECTED",
        strength=round(classified / total, 3),
        evidence=evidence,
        detail={
            "statements": [asdict(a) for a in atoms],
            "counts": dict(counts),
            "dominant_payload": dominant_payload,
            "classified_count": classified,
            "statement_count": len(atoms),
        },
        fired=bool(classified),
    )
