import os
import json
import re
import time
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, request, jsonify, render_template
from openai import OpenAI

app = Flask(__name__)

# ==========================
# ENV
# ==========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    print("WARNING: OPENAI_API_KEY not set")

client = OpenAI(api_key=OPENAI_API_KEY)

# SQLite DB file (Render ephemeral unless you add a persistent disk)
DB_PATH = os.getenv("NTI_DB_PATH", "/tmp/nti_core.db")

# ==========================
# RULE REGISTRY (Deterministic Layer)
# ==========================
CERTAINTY_WORDS = {
    "always", "never", "clearly", "obviously", "definitely", "certainly", "undeniably",
    "prove", "proves", "proven", "fact", "facts", "guarantee", "guaranteed"
}

GENERALIZATION_PHRASES = {
    "most people", "many people", "in general", "typically", "usually", "everyone",
    "no one", "people like you", "a lot of people"
}

EMOTIONAL_INTENSIFIERS = {
    "deeply", "truly", "incredibly", "extremely", "highly", "significantly", "powerful",
    "amazing", "delightful", "remarkable", "shocking", "terrifying", "inspiring"
}

ASSUMPTION_STARTERS = {
    "you may", "you might", "you probably", "it sounds like", "it seems like",
    "you tend to", "chances are", "you likely", "you are the kind of"
}

# Optional: common "soft authority" steering phrases (counts toward authority-ish steering)
SOFT_AUTHORITY_PHRASES = {
    "it may be helpful to", "it might help to", "it’s important to", "you should consider",
    "a good next step is", "you might want to"
}


# ==========================
# DB INIT
# ==========================
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_init() -> None:
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS nti_requests (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        ip TEXT,
        user_agent TEXT,
        session_id TEXT,
        route TEXT NOT NULL,
        model TEXT,
        latency_ms INTEGER,
        openai_total_tokens INTEGER,
        openai_prompt_tokens INTEGER,
        openai_completion_tokens INTEGER,
        input_length_chars INTEGER,
        error TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS nti_core_results (
        request_id TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        scores_json TEXT NOT NULL,
        tilt_json TEXT NOT NULL,
        tags_json TEXT NOT NULL,
        FOREIGN KEY(request_id) REFERENCES nti_requests(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS nti_events (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        session_id TEXT,
        event_name TEXT NOT NULL,
        event_json TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


db_init()


# ==========================
# UTIL: Time / IDs / Logging
# ==========================
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_session_id() -> str:
    # UI can send this header later; for now we generate if missing.
    sid = request.headers.get("X-Session-Id")
    if sid and isinstance(sid, str) and len(sid) >= 8:
        return sid
    return str(uuid.uuid4())


def log_json_line(event: str, payload: Dict[str, Any]) -> None:
    # Structured log for Render logs (acts like telemetry even without DB)
    record = {"event": event, "ts": utc_now_iso(), **payload}
    print(json.dumps(record, ensure_ascii=False))


def record_request_base(
    request_id: str,
    route: str,
    session_id: str,
    model: Optional[str],
    latency_ms: Optional[int],
    usage: Optional[Dict[str, int]],
    input_len: Optional[int],
    error: Optional[str]
) -> None:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    ua = request.headers.get("User-Agent")

    prompt_tokens = None
    completion_tokens = None
    total_tokens = None
    if usage:
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO nti_requests
        (id, created_at, ip, user_agent, session_id, route, model, latency_ms,
         openai_total_tokens, openai_prompt_tokens, openai_completion_tokens, input_length_chars, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        request_id,
        utc_now_iso(),
        ip,
        ua,
        session_id,
        route,
        model,
        latency_ms,
        total_tokens,
        prompt_tokens,
        completion_tokens,
        input_len,
        error
    ))
    conn.commit()
    conn.close()


# ==========================
# UTIL: Deterministic Scoring
# ==========================
def _count_hits(text: str, wordset: set) -> int:
    t = text.lower()
    hits = 0
    for w in wordset:
        if " " in w:
            hits += t.count(w)
        else:
            hits += len(re.findall(rf"\b{re.escape(w)}\b", t))
    return hits


def _normalize_score(hit_count: int, length: int) -> float:
    if length <= 0:
        return 0.0
    # hits per 250 chars (cap)
    rate = hit_count / max(1.0, (length / 250.0))
    # convert to 0..1 (6 hits/250 ~= 1.0)
    return round(min(1.0, rate / 6.0), 2)


def deterministic_scores(text: str) -> Dict[str, Any]:
    length = len(text)
    certainty_hits = _count_hits(text, CERTAINTY_WORDS)
    gen_hits = _count_hits(text, GENERALIZATION_PHRASES)
    emo_hits = _count_hits(text, EMOTIONAL_INTENSIFIERS)
    assum_hits = _count_hits(text, ASSUMPTION_STARTERS)
    soft_auth_hits = _count_hits(text, SOFT_AUTHORITY_PHRASES)

    # Treat authority as certainty + soft authority steering
    authority_hits = certainty_hits + soft_auth_hits

    scores = {
        "emotion": _normalize_score(emo_hits, length),
        "assumption": _normalize_score(assum_hits, length),
        "generalization": _normalize_score(gen_hits, length),
        "authority": _normalize_score(authority_hits, length),
        "length_chars": length,
        "hits": {
            "emotion": emo_hits,
            "assumption": assum_hits,
            "generalization": gen_hits,
            "authority": authority_hits,
            "certainty_only": certainty_hits,
            "soft_authority_only": soft_auth_hits
        }
    }
    return scores


def composite_tilt(scores: Dict[str, Any]) -> Dict[str, Any]:
    # A simple weighted composite (no vibes).
    # Weights chosen for salience: emotion + assumption slightly higher.
    emo = float(scores.get("emotion", 0.0))
    assum = float(scores.get("assumption", 0.0))
    gen = float(scores.get("generalization", 0.0))
    auth = float(scores.get("authority", 0.0))

    # Weighted sum
    raw = (0.35 * emo) + (0.35 * assum) + (0.20 * gen) + (0.10 * auth)
    raw = max(0.0, min(1.0, raw))
    raw = round(raw, 2)

    # Snap bands for UI later (LOW/BASELINE/EXPOSED mapping)
    if raw < 0.25:
        band = "LOW"
    elif raw < 0.55:
        band = "BASELINE"
    else:
        band = "EXPOSED"

    return {"score": raw, "band": band, "weights": {"emotion": 0.35, "assumption": 0.35, "generalization": 0.20, "authority": 0.10}}


# ==========================
# UTIL: Phrase Offset Mapping
# ==========================
def find_all_spans(haystack: str, needle: str) -> List[Tuple[int, int]]:
    spans = []
    if not needle:
        return spans

    # Case-insensitive search but we must return offsets in original string.
    pattern = re.escape(needle)
    for m in re.finditer(pattern, haystack, flags=re.IGNORECASE):
        spans.append((m.start(), m.end()))
    return spans


def attach_offsets(text: str, tags: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    used_spans = set()

    for t in tags:
        phrase = str(t.get("phrase", "")).strip()
        if not phrase:
            continue

        spans = find_all_spans(text, phrase)
        if not spans:
            # If the phrase can’t be found exactly, skip it (integrity rule)
            continue

        # Pick the first unused span (so we don’t stack duplicates)
        chosen = None
        for sp in spans:
            if sp not in used_spans:
                chosen = sp
                break
        if chosen is None:
            chosen = spans[0]

        used_spans.add(chosen)

        out.append({
            "type": t.get("type"),
            "phrase": phrase,
            "strength": t.get("strength"),
            "start": chosen[0],
            "end": chosen[1]
        })

    return out


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
# EVENT LOGGING (UI uses this later; engine already supports it)
# ------------------------------
@app.route("/events", methods=["POST"])
def events():
    session_id = get_session_id()
    payload = request.get_json() or {}
    event_name = str(payload.get("event", "")).strip()
    event_data = payload.get("data", {})

    if not event_name:
        return jsonify({"error": "Missing event name"}), 400

    eid = str(uuid.uuid4())

    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO nti_events (id, created_at, session_id, event_name, event_json)
        VALUES (?, ?, ?, ?, ?)
    """, (eid, utc_now_iso(), session_id, event_name, json.dumps(event_data, ensure_ascii=False)))
    conn.commit()
    conn.close()

    log_json_line("nti_event", {"session_id": session_id, "event": event_name, "data": event_data})

    return jsonify({"ok": True, "event_id": eid})


# ------------------------------
# DEMO RUN (Raw OpenAI Response)
# ------------------------------
@app.route("/demo-run", methods=["POST"])
def demo_run():
    request_id = str(uuid.uuid4())
    session_id = get_session_id()
    t0 = time.time()

    data = request.get_json() or {}
    prompt = data.get("prompt")

    if not prompt:
        record_request_base(request_id, "/demo-run", session_id, None, None, None, None, "No prompt provided")
        return jsonify({"error": "No prompt provided", "request_id": request_id}), 400

    model = "gpt-4o-mini"
    usage = None
    error = None

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7
        )
        out = resp.choices[0].message.content
        if hasattr(resp, "usage") and resp.usage:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                "completion_tokens": getattr(resp.usage, "completion_tokens", None),
                "total_tokens": getattr(resp.usage, "total_tokens", None)
            }

        latency_ms = int((time.time() - t0) * 1000)
        record_request_base(request_id, "/demo-run", session_id, model, latency_ms, usage, len(prompt), None)
        log_json_line("demo_run", {"request_id": request_id, "session_id": session_id, "latency_ms": latency_ms, "model": model})

        return jsonify({"output": out, "request_id": request_id})

    except Exception as e:
        error = str(e)
        latency_ms = int((time.time() - t0) * 1000)
        record_request_base(request_id, "/demo-run", session_id, model, latency_ms, usage, len(prompt), error)
        log_json_line("demo_run_error", {"request_id": request_id, "session_id": session_id, "error": error})
        return jsonify({"error": error, "request_id": request_id}), 500


# ------------------------------
# NTI CORE (FULL STACK ENGINE OUTPUT)
# ------------------------------
@app.route("/nti-core", methods=["POST"])
def nti_core():
    request_id = str(uuid.uuid4())
    session_id = get_session_id()
    t0 = time.time()
    model = "gpt-4o-mini"

    data = request.get_json() or {}

    # We support either:
    # - text only
    # - prompt + answer
    text = data.get("text")
    prompt = data.get("prompt")
    answer = data.get("answer")

    if prompt and answer and not text:
        text = f"PROMPT:\n{prompt}\n\nANSWER:\n{answer}"

    if not text:
        record_request_base(request_id, "/nti-core", session_id, model, None, None, None, "No text provided")
        return jsonify({"error": "No text provided", "request_id": request_id}), 400

    # Deterministic layer always runs
    scores = deterministic_scores(text)
    tilt = composite_tilt(scores)

    # Model tagging layer: STRICT JSON output
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

    usage = None
    error = None
    tags_clean: List[Dict[str, Any]] = []

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text}
            ],
            temperature=0.0,
            response_format={"type": "json_object"}
        )

        raw = resp.choices[0].message.content

        if hasattr(resp, "usage") and resp.usage:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                "completion_tokens": getattr(resp.usage, "completion_tokens", None),
                "total_tokens": getattr(resp.usage, "total_tokens", None)
            }

        parsed = json.loads(raw)
        tags = parsed.get("tags", [])
        if not isinstance(tags, list):
            tags = []

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

            tags_clean.append({"type": ttype, "phrase": phrase, "strength": round(strength, 2)})

        # Attach offsets (integrity)
        tags_with_offsets = attach_offsets(text, tags_clean)

        latency_ms = int((time.time() - t0) * 1000)
        record_request_base(request_id, "/nti-core", session_id, model, latency_ms, usage, len(text), None)

        # Persist results
        conn = db_connect()
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO nti_core_results
            (request_id, version, scores_json, tilt_json, tags_json)
            VALUES (?, ?, ?, ?, ?)
        """, (
            request_id,
            "nti-core-v1.1",
            json.dumps(scores, ensure_ascii=False),
            json.dumps(tilt, ensure_ascii=False),
            json.dumps(tags_with_offsets, ensure_ascii=False)
        ))
        conn.commit()
        conn.close()

        log_json_line("nti_core", {
            "request_id": request_id,
            "session_id": session_id,
            "latency_ms": latency_ms,
            "model": model,
            "tilt": tilt,
            "tag_count": len(tags_with_offsets)
        })

        return jsonify({
            "version": "nti-core-v1.1",
            "scores": scores,
            "tilt": tilt,
            "tags": tags_with_offsets,
            "telemetry": {
                "request_id": request_id,
                "session_id": session_id,
                "latency_ms": latency_ms,
                "model": model,
                "usage": usage
            }
        })

    except Exception as e:
        error = str(e)
        latency_ms = int((time.time() - t0) * 1000)
        record_request_base(request_id, "/nti-core", session_id, model, latency_ms, usage, len(text), error)

        log_json_line("nti_core_error", {
            "request_id": request_id,
            "session_id": session_id,
            "error": error
        })

        # Still return deterministic layers so stack never returns empty
        return jsonify({
            "version": "nti-core-v1.1",
            "scores": scores,
            "tilt": tilt,
            "tags": [],
            "telemetry": {
                "request_id": request_id,
                "session_id": session_id,
                "latency_ms": latency_ms,
                "model": model,
                "usage": usage
            },
            "warning": "OpenAI tagging failed; returned deterministic scores only.",
            "error": error
        }), 200


# ------------------------------
# NTI (Legacy human-readable endpoint, kept for compatibility)
# ------------------------------
@app.route("/nti", methods=["POST"])
def nti():
    request_id = str(uuid.uuid4())
    session_id = get_session_id()
    t0 = time.time()
    model = "gpt-4o-mini"
    usage = None

    data = request.get_json() or {}
    user_input = data.get("input")

    if not user_input:
        record_request_base(request_id, "/nti", session_id, model, None, None, None, "No input provided")
        return jsonify({"error": "No input provided", "request_id": request_id}), 400

    try:
        resp = client.chat.completions.create(
            model=model,
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
                {"role": "user", "content": user_input}
            ],
            temperature=0.2
        )

        out = resp.choices[0].message.content
        if hasattr(resp, "usage") and resp.usage:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                "completion_tokens": getattr(resp.usage, "completion_tokens", None),
                "total_tokens": getattr(resp.usage, "total_tokens", None)
            }

        latency_ms = int((time.time() - t0) * 1000)
        record_request_base(request_id, "/nti", session_id, model, latency_ms, usage, len(user_input), None)

        log_json_line("nti_legacy", {
            "request_id": request_id,
            "session_id": session_id,
            "latency_ms": latency_ms,
            "model": model
        })

        return jsonify({"output": out, "request_id": request_id})

    except Exception as e:
        error = str(e)
        latency_ms = int((time.time() - t0) * 1000)
        record_request_base(request_id, "/nti", session_id, model, latency_ms, usage, len(user_input), error)
        log_json_line("nti_legacy_error", {"request_id": request_id, "session_id": session_id, "error": error})
        return jsonify({"error": error, "request_id": request_id}), 500


if __name__ == "__main__":
    app.run(debug=True)
