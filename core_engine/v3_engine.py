import re
from typing import Any, Dict, List, Optional, Tuple

HEDGE_WORDS = [
    "maybe", "likely", "possibly", "kind of", "sort of",
    "perhaps", "might", "could be", "i think", "i believe", "i guess",
    "i suppose", "it seems", "it appears", "arguably", "potentially",
    "presumably", "probably", "conceivably", "apparently", "allegedly",
    "just", "a little", "a bit", "somewhat", "fairly", "rather",
    "more or less", "in a way", "to some extent", "to a degree",
    "tend to", "in general", "for the most part", "as far as i know",
    "from my understanding", "if i recall correctly", "not entirely sure",
    "i could be wrong", "don't quote me", "take this with a grain of salt",
    "it would seem", "one might argue", "some would say", "it's been said",
    "there's a chance", "it's possible that", "it's not impossible",
    "we'll see", "time will tell", "remains to be seen", "hard to say",
    "depends on", "it depends", "that's debatable", "up in the air",
]

FILLER_PHRASES = [
    # Core filler
    "it is important to note", "in conclusion", "ultimately", "to summarize",
    # AI sycophancy
    "i'd be happy to", "i'd be glad to", "i would be happy to",
    "certainly", "absolutely", "of course", "great question",
    "that's a great question", "that's an excellent question",
    "thanks for asking", "thank you for asking",
    # Repetitive reference
    "as mentioned earlier", "as previously mentioned", "as noted above",
    "as i mentioned", "as we discussed", "as stated before",
    # Importance signaling
    "it's worth noting", "it's worth mentioning", "it should be noted",
    "it bears mentioning", "it's important to mention",
    # Transition filler
    "that being said", "having said that", "with that being said",
    "that said", "all that being said",
    "at the end of the day", "when all is said and done",
    "moving forward", "going forward", "looking ahead",
    # Prepositional padding
    "in terms of", "with respect to", "with regard to", "in regard to",
    "when it comes to", "as far as", "on the topic of",
    # Obviousness
    "it goes without saying", "needless to say",
    "for what it's worth", "fwiw",
    # False candor
    "to be honest", "honestly", "frankly", "to be frank",
    "in my opinion", "in my humble opinion", "imo", "imho",
    # Closing filler
    "i hope this helps", "hope that helps", "hope this was helpful",
    "let me know if you have any questions",
    "let me know if you need anything else",
    "feel free to", "don't hesitate to",
    "please don't hesitate", "please feel free",
    # Empathy filler
    "i understand your concern", "i completely understand",
    "i appreciate your patience", "thank you for your patience",
    # List padding
    "first and foremost", "last but not least",
    # Urgency inflation
    "it's crucial to", "it's essential to", "it's vital to",
    "it's important to understand that", "it's key to note",
    # Verbose connectors
    "in order to", "so as to", "for the purpose of",
    "due to the fact that", "owing to the fact that",
    "in light of the fact that", "given the fact that",
    "on the other hand", "by the same token",
    "in any case", "in any event", "be that as it may",
    # Rewording filler
    "to put it simply", "to put it another way", "in other words",
    "simply put", "put simply", "to be clear",
    "the bottom line is", "the long and short of it",
    # Temporal filler
    "at this point in time", "at this juncture",
    "in today's world", "in this day and age",
    # Redundant pairs
    "each and every", "any and all", "null and void", "part and parcel",
    # Business filler
    "please be advised", "please note that", "kindly note",
    "i wanted to reach out", "i'm reaching out to",
    "just wanted to follow up", "just checking in",
    "per our conversation", "per our discussion",
    "as per your request", "pursuant to",
    "for your information", "for your reference",
    "attached please find", "please find attached",
    "enclosed please find",
    # Conversational padding
    "so basically", "so essentially", "so the thing is",
    "well actually", "i mean", "you know", "you know what i mean",
    "like i said", "as i said", "if you will", "so to speak",
    "more or less", "by and large", "all things considered",
    "generally speaking", "broadly speaking",
    # Closing filler
    "best regards", "warm regards", "kind regards",
    "sincerely", "respectfully", "with gratitude",
]

# Redundant transition words that can be stripped
TRANSITION_PATTERN = re.compile(
    r"\b(however|moreover|furthermore|additionally|consequently|therefore|thus|hence|"
    r"accordingly|nevertheless|nonetheless|meanwhile|subsequently|alternatively)\s*,?\s*",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _remove_phrases(text: str, phrases: List[str]) -> Tuple[str, int, List[str]]:
    lower = text.lower()
    removed = 0
    removed_items: List[str] = []
    for p in phrases:
        if p in lower:
            pattern = re.compile(re.escape(p), re.IGNORECASE)
            new_text, n = pattern.subn("", text)
            if n > 0:
                removed += n
                removed_items.append(p)
                text = new_text
                lower = text.lower()
    text = _normalize(text)
    return text, removed, removed_items


def _dedupe_sentences(text: str) -> Tuple[str, int]:
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    seen = set()
    out = []
    dupes = 0
    for s in parts:
        if s in seen:
            dupes += 1
            continue
        seen.add(s)
        out.append(s)
    return " ".join(out).strip(), dupes


def _near_dedupe_sentences(sentences: List[str]) -> Tuple[List[str], int]:
    """Remove sentences that share >70% of significant words with a kept sentence."""
    if len(sentences) <= 1:
        return sentences, 0

    final = [sentences[0]]
    removed = 0

    for i in range(1, len(sentences)):
        words_a = set(w for w in sentences[i].lower().split() if len(w) > 3)
        is_near_dupe = False
        for kept in final:
            words_b = set(w for w in kept.lower().split() if len(w) > 3)
            if not words_a:
                break
            overlap = len(words_a & words_b)
            denom = max(len(words_a), len(words_b))
            if denom > 0 and (overlap / denom) > 0.70:
                is_near_dupe = True
                removed += 1
                break
        if not is_near_dupe:
            final.append(sentences[i])

    return final, removed


def _objective_filter(text: str, objective: str) -> str:
    obj = (objective or "").strip().lower()
    if not obj:
        return text
    obj_words = [w for w in re.findall(r"[a-z0-9']+", obj) if len(w) > 3]
    if not obj_words:
        return text

    sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    kept = []
    for s in sentences:
        sl = s.lower()
        if any(w in sl for w in obj_words):
            kept.append(s)
    return " ".join(kept).strip() if kept else text


def approx_tokens(text: str) -> int:
    return max(0, int(round(len(text or "") / 4.0)))


def run_v3(text: str, max_tokens: int = 400, objective: Optional[str] = None) -> Dict[str, Any]:
    """
    V3 = deterministic stabilization (no rewriting, removal/compression only).
    Expanded: near-dupe detection, transition removal, compression stats.
    """
    raw = text or ""
    t = _normalize(raw)
    original_text = t
    removed_items: List[Dict[str, str]] = []

    # Remove hedges
    hedges_removed = 0
    for w in HEDGE_WORDS:
        pattern = re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)
        before = t
        t, n = pattern.subn("", t)
        if n > 0:
            hedges_removed += n
            removed_items.append({"type": "hedge", "phrase": w})
            t = _normalize(t)

    # Remove filler phrases
    t, filler_removed, filler_items = _remove_phrases(t, FILLER_PHRASES)
    for fi in filler_items:
        removed_items.append({"type": "filler", "phrase": fi})

    # Remove redundant transitions
    transitions_removed = 0
    before = t
    t = TRANSITION_PATTERN.sub("", t)
    if t != before:
        transitions_removed = 1
        t = _normalize(t)

    # Dedupe exact sentences
    t, exact_dupes = _dedupe_sentences(t)

    # Near-dedupe
    sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    sentences, near_dupes = _near_dedupe_sentences(sentences)
    t = " ".join(sentences).strip()

    # Optional objective anchoring
    if objective:
        t = _objective_filter(t, objective)

    # Token ceiling (word count proxy)
    words = t.split()
    trimmed = False
    if len(words) > max_tokens:
        t = " ".join(words[:max_tokens])
        trimmed = True

    t = _normalize(t)

    # Compression stats
    tokens_before = approx_tokens(original_text)
    tokens_after = approx_tokens(t)
    tokens_saved = tokens_before - tokens_after
    compression_pct = round((tokens_saved / tokens_before) * 100) if tokens_before > 0 else 0

    return {
        "stabilized_text": t,
        "hedges_removed": hedges_removed,
        "filler_removed": filler_removed,
        "transitions_removed": transitions_removed,
        "exact_dupes_removed": exact_dupes,
        "near_dupes_removed": near_dupes,
        "removed_items": removed_items,
        "trimmed": trimmed,
        "max_tokens": max_tokens,
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "tokens_saved": tokens_saved,
        "compression_pct": compression_pct,
    }
