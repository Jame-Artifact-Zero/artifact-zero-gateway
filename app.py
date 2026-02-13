import os
import json
import re
from flask import Flask, request, jsonify, render_template
from openai import OpenAI

app = Flask(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)

# ==========================
# SIMPLE DETERMINISTIC HELPERS
# (These make the "stack" real without guessing)
# ==========================

CERTAINTY_WORDS = {
    "always", "never", "clearly", "obviously", "definitely", "certainly", "undeniably", "prove", "proves", "proven"
}
GENERALIZATION_PHRASES = {
    "most people", "many people", "in general", "typically", "usually", "everyone", "no one", "people like you"
}
EMOTIONAL_INTENSIFIERS = {
    "deeply", "truly", "incredibly", "extremely", "highly", "significantly", "powerful", "amazing", "delightful"
}
ASSUMPTION_STARTERS = {
    "you may", "you might", "you probably", "it sounds like", "it seems like", "you tend to", "chances are"
}

def _count_hits(text: str, wordset: set) -> int:
    t = text.lower()
    hits = 0
    for w in wordset:
        # whole word or phrase match
        if " " in w:
            hits += t.count(w)
        else:
            hits += len(re.findall(rf"\b{re.escape(w)}\b", t))
    return hits

def _normalize_score(hit_count: int, length: int) -> float:
    # normalize by length so long text doesn't auto-score higher
    # simple: hits per 250 chars, capped at 1.0
    if length <= 0:
        return 0.0
    rate = hit_count / max(1.0, (length / 250.0))
    return round(min(1.0, rate / 6.0), 2)  # 6 hits/250 chars ~ 1.0

def _deterministic_scores(text: str) -> dict:
    length = len(text)
    certainty_hits = _count_hits(text, CERTAINTY_WORDS)
    gen_hits = _count_hits(text, GENERALIZATION_PHRASES)
    emo_hits = _count_hits(text, EMOTIONAL_INTENSIFIERS)
    assum_hits = _count_hits(text, ASSUMPTION_STARTERS)

    return {
        "emotion": _normalize_score(emo_hits, length),
        "assumption": _normalize_score(assum_hits, length),
        "generalization": _normalize_score(gen_hits, length),
        "authority": _normalize_score(certainty_hits, length),
        "length_chars": length,
        "hits": {
            "emotion": emo_hits,
            "assumption": assum_hits,
            "generalization": gen_hits,
            "authority": certainty_hits,
        }
    }

# ==========================
# ROUTES
# ==========================

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ------------------------------
# DEMO RUN (Raw OpenAI Response)
# ------------------------------
@app.route("/demo-run", methods=["POST"])
def demo_run():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload received"}), 400

    prompt = data.get("prompt")

    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )

        output = response.choices[0].message.content
        return jsonify({"output": output})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ------------------------------
# NTI CORE (Structured JSON tags + scores)
# This is the "stack core" we can build on.
# ------------------------------
@app.route("/nti-core", methods=["POST"])
def nti_core():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload received"}), 400

    text = data.get("text")

    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Deterministic scores (always computed, even if OpenAI fails)
    scores = _deterministic_scores(text)

    # Ask model for tags in STRICT JSON only
    # We do NOT ask for prose. We ask for a finite schema.
    system = (
        "You are NTI-CORE.\n"
        "Task: Tag 'power phrases' inside the provided text.\n"
        "Power phrases are spans that perform one of these functions:\n"
        "1) EMOTION (emotional intensifiers, validation, urgency)\n"
        "2) ASSUMPTION (claims about the user without evidence)\n"
        "3) GENERALIZATION (claims about people/most/everyone)\n"
        "4) AUTHORITY (certainty markers: clearly/obviously/always/never)\n\n"
        "Return STRICT JSON ONLY with this shape:\n"
        "{\n"
        '  "tags": [\n'
        "    {\n"
        '      "type": "EMOTION|ASSUMPTION|GENERALIZATION|AUTHORITY",\n'
        '      "phrase": "exact substring from the text",\n'
        '      "strength": 0.0-1.0\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Use short phrases (3 to 12 words).\n"
        "- Only tag phrases that actually appear in the text.\n"
        "- Max 18 tags.\n"
        "- Do not add commentary. JSON only.\n"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ],
            temperature=0.0,
            # Many OpenAI models support forcing JSON output this way.
            response_format={"type": "json_object"}
        )

        raw = response.choices[0].message.content

        # Parse model JSON safely
        parsed = json.loads(raw)

        # Hard-validate expected shape
        tags = parsed.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        # Clean tags: enforce types + bounds
        cleaned = []
        for t in tags:
            if not isinstance(t, dict):
                continue
            ttype = str(t.get("type", "")).strip().upper()
            phrase = str(t.get("phrase", "")).strip()
            strength = t.get("strength", 0.0)
            try:
                strength = float(strength)
            except Exception:
                strength = 0.0
            strength = max(0.0, min(1.0, strength))

            if ttype not in {"EMOTION", "ASSUMPTION", "GENERALIZATION", "AUTHORITY"}:
                continue
            if not phrase:
                continue
            cleaned.append({
                "type": ttype,
                "phrase": phrase,
                "strength": round(strength, 2)
            })

        return jsonify({
            "version": "nti-core-v1",
            "scores": scores,
            "tags": cleaned
        })

    except Exception as e:
        # Even if model fails, core still returns deterministic scores
        return jsonify({
            "version": "nti-core-v1",
            "scores": scores,
            "tags": [],
            "warning": "OpenAI tagging failed; returned deterministic scores only.",
            "error": str(e)
        }), 200


# ------------------------------
# NTI (Human-readable)
# Keep it for now so the website continues to work.
# ------------------------------
@app.route("/nti", methods=["POST"])
def nti():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON payload received"}), 400

    user_input = data.get("input")

    if not user_input:
        return jsonify({"error": "No input provided"}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You analyze AI responses and expose hidden framing in plain language.\n\n"
                        "Rules:\n"
                        "- No academic language.\n"
                        "- No jargon.\n"
                        "- Short.\n"
                        "- Maximum 6 bullets total.\n"
                        "- Format exactly as:\n\n"
                        "What It Did:\n"
                        "• bullet\n"
                        "• bullet\n\n"
                        "What That Means:\n"
                        "• bullet\n"
                        "• bullet\n\n"
                        "Make it clear. Make it readable. Make it screenshot-worthy."
                    )
                },
                {
                    "role": "user",
                    "content": user_input
                }
            ],
            temperature=0.2
        )

        output = response.choices[0].message.content
        return jsonify({"output": output})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True)
