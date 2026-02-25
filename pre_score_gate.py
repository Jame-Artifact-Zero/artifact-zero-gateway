import re

# ═══════════════════════════════════════════════════════════
# V2 PRE-SCORE GATE
# Runs BEFORE scoring. Rejects gibberish, too-short, non-language.
# The system only works if we stick to the system.
# ═══════════════════════════════════════════════════════════

# Function words + common English (top 300+). These appear in real messages
# regardless of domain. Not trying to be a dictionary — just enough to tell
# language from keyboard mash.
_COMMON_WORDS = frozenset("""
the be to of and a in that have i it for not on with he as you do at this but his
by from they we say her she or an will my one all would there their what so up out
if about who get which go me when make can like time no just him know take people
into year your good some could them see other than then now look only come its over
think also back after use two how our work first well way even new want because any
these give day most us is are was were been has had may might shall should would
could did does done am are not do did get got has have may might must need shall
should will would can able just now still also here then than when how what which
who where why all each every both few more most other some such no nor not only
same so than too very already always never often sometimes usually actually also
really being before between during however many much since still such very after
again another because before both each even found here last many more most never
new next now number other own part same several since still such tell their them
then there these they this three through two under until want was way well were
what when where while who will with work would your about above across after
against along around called came come day different end even first from give
good great hand help here high home house into keep know large last left life
line live long look made make many may more much must name near need next off
old only open other our out over own part place point right run same say seem
should show side small something state story take tell than those thing think
through together too turn upon want went well what when where which while why
without word world year young been being both but did does each for from get
had has have her him his how into its let may more much must nor not off one
our own per put run saw set she the too use via was way who won yet you
can per via yet nor let its
said going doing being having making taking getting
also just really very much still even back only also
before after during since until while because although though
please thank thanks sorry yes yeah sure okay right wrong
need want like think know feel see hear find send receive
report review check update confirm deny approve reject
email message call meeting schedule project budget plan
team client customer company business office department
today tomorrow yesterday week month year quarter deadline
""".split())

def pre_score_gate(text: str) -> dict:
    """
    V2 inbound gate. Validates input is real, scorable language.
    Returns {"pass": bool, "reason": str, "msg": str}
    """
    if not text or not text.strip():
        return {"pass": False, "reason": "EMPTY", "msg": "No text provided."}

    cleaned = text.strip()
    words = cleaned.split()

    # Min word count
    if len(words) < 4:
        return {"pass": False, "reason": "TOO_SHORT",
                "msg": "Message too short. Write at least 4 words."}

    # Extract alpha-only words
    alpha_words = [w.lower().strip(".,!?;:'\"()") for w in words
                   if re.match(r'^[a-zA-Z\']+$', w.strip(".,!?;:'\"()"))]

    if len(alpha_words) < 2:
        return {"pass": False, "reason": "NO_LANGUAGE",
                "msg": "No recognizable words detected."}

    # Dictionary word ratio
    known = sum(1 for w in alpha_words if w.lower() in _COMMON_WORDS)
    ratio = known / len(alpha_words) if alpha_words else 0

    if ratio < 0.10 and len(alpha_words) > 4:
        return {"pass": False, "reason": "GIBBERISH",
                "msg": "This doesn't look like a real message. The system scores structured communication — try a real email, message, or business text."}

    # Keyboard mash: avg word length > 14 for alpha words
    avg_len = sum(len(w) for w in alpha_words) / max(len(alpha_words), 1)
    if avg_len > 14 and len(alpha_words) > 2:
        return {"pass": False, "reason": "KEYBOARD_MASH",
                "msg": "This doesn't look like a real message. Try pasting an actual email or business communication."}

    # Repetition: same word > 60% of message
    if alpha_words and len(alpha_words) > 4:
        from collections import Counter
        counts = Counter(alpha_words)
        most_common_count = counts.most_common(1)[0][1]
        if most_common_count / len(alpha_words) > 0.6:
            return {"pass": False, "reason": "REPETITION",
                    "msg": "Repetitive input detected. Paste a real message to score."}

    return {"pass": True, "reason": "OK", "msg": ""}
