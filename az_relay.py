"""
az_relay.py
Artifact Zero Encrypted Session Relay

Architecture:
- User creates account, builds protocol on AZ site
- AZ generates signed session token (opaque blob)
- User pastes token into AI thread as session initializer  
- AI responds normally
- User copies AI output → pastes into AZ relay page
- AZ backend: validates token, runs scoring on AI output,
  generates directives, returns new token
- User pastes directive back into AI thread
- Cycle repeats. Protocol logic NEVER leaves the server.

Token design:
- Tokens are SIGNED REFERENCES, not encrypted containers
- base64(json{session_id, turn, ts, nonce}) + HMAC-SHA256 signature
- Even if decoded, user sees only IDs — no protocol, no rules, no weights
- Server looks up session_id → gets full protocol from DB
- Tamper = signature mismatch = rejected

Database: Uses db.py abstraction layer.
  - DATABASE_URL set → PostgreSQL (AWS/production)
  - DATABASE_URL not set → SQLite (local dev)
"""

import os
import json
import time
import uuid
import hmac
import hashlib
import base64
import secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, render_template, session, redirect

# ── Use the shared db.py abstraction layer ──
from db import get_conn, release_conn, param_placeholder

az_relay = Blueprint("az_relay", __name__)

# ─── CONFIG ───
RELAY_SECRET = os.getenv("AZ_RELAY_SECRET", "az-relay-change-in-prod-" + secrets.token_hex(8))
FREE_TURNS = 50
MAX_PROTOCOL_LEN = 4000
TOKEN_TTL = 3600 * 24

# Placeholder for SQL queries — %s for Postgres, ? for SQLite
P = param_placeholder()


# ─── DATABASE ───
def init_relay_db():
    """Create relay tables. Works on both SQLite and PostgreSQL."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS az_users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            username TEXT DEFAULT '',
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT NOT NULL,
            turns_used INTEGER DEFAULT 0,
            turns_limit INTEGER DEFAULT 50,
            plan TEXT DEFAULT 'free',
            active INTEGER DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS az_protocols (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            protocol_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            active INTEGER DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS az_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            protocol_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            last_turn_at TEXT,
            turn_count INTEGER DEFAULT 0,
            platform TEXT DEFAULT 'unknown',
            active INTEGER DEFAULT 1
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS az_turns (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            turn_number INTEGER NOT NULL,
            ai_output TEXT,
            nti_scores TEXT,
            governance_directives TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Indexes for performance
    try:
        cur.execute("CREATE INDEX IF NOT EXISTS idx_az_users_email ON az_users(email)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_az_protocols_user ON az_protocols(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_az_sessions_user ON az_sessions(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_az_turns_session ON az_turns(session_id)")
    except Exception:
        pass  # Indexes already exist or not supported

    conn.commit()
    release_conn(conn)
    print("[RELAY] Tables initialized")


init_relay_db()


# ─── CRYPTO ───
def _sign(payload: str) -> str:
    """HMAC-SHA256 sign a payload string."""
    return hmac.new(
        RELAY_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()[:32]


def _hash_pw(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), 100000
    ).hex()


def make_token(session_id: str, turn: int, extra: dict = None) -> str:
    """Generate a signed, opaque session token."""
    payload = {
        "s": session_id,
        "t": turn,
        "ts": int(time.time()),
        "n": secrets.token_hex(8)
    }
    if extra:
        payload["x"] = extra
    raw = json.dumps(payload, separators=(",", ":"))
    sig = _sign(raw)
    combined = raw + "|" + sig
    return base64.urlsafe_b64encode(combined.encode()).decode()


def verify_token(token: str) -> dict:
    """Verify and decode a signed token. Returns payload or raises."""
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        raw, sig = decoded.rsplit("|", 1)
        if not hmac.compare_digest(_sign(raw), sig):
            raise ValueError("Invalid signature")
        payload = json.loads(raw)
        age = int(time.time()) - payload.get("ts", 0)
        if age > TOKEN_TTL:
            raise ValueError("Token expired")
        return payload
    except Exception as e:
        raise ValueError(f"Token verification failed: {e}")


# ─── RESULT HELPER ───
def _row_to_dict(row):
    """Convert a database row to dict. Handles both SQLite Row and psycopg2 RealDictRow."""
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        # psycopg2 with RealDictCursor already returns dict-like
        return {k: row[k] for k in row.keys()}


# ─── AUTH HELPERS ───
def get_current_user():
    """Get current user from session cookie."""
    user_id = session.get("az_user_id")
    if not user_id:
        return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM az_users WHERE id={P} AND active=1", (user_id,))
    user = cur.fetchone()
    release_conn(conn)
    return _row_to_dict(user)


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        return f(user=user, *args, **kwargs)
    return decorated


# ─── NTI SCORING (inline, no import dependency) ───
SMOOTH_OPENERS = {"great", "absolutely", "definitely", "of course", "sure", "perfect", "wonderful", "fantastic", "excellent", "love"}
HEDGE_WORDS = {"maybe", "perhaps", "might", "could", "possibly", "somewhat", "arguably", "likely", "probably", "generally"}
FILLER_PHRASES = ["it's worth noting", "it's important to", "keep in mind", "as you know", "basically", "essentially", "in terms of"]


def score_ai_output(text: str, human_input: str = "") -> dict:
    """Quick NTI scoring of AI output. Returns governance-relevant metrics."""
    words = text.lower().split()
    sents = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    wc = len(words)
    sc = max(len(sents), 1)

    # Opening smoothing
    first_word = words[0].strip(".,!?:;") if words else ""
    osr = 1 if first_word in SMOOTH_OPENERS else 0

    # Hedge density
    hedge_count = sum(1 for w in words if w.strip(".,!?") in HEDGE_WORDS)
    hed_d = hedge_count / max(wc, 1)

    # Smooth density
    smooth_count = sum(1 for w in words if w.strip(".,!?") in SMOOTH_OPENERS)
    sm_d = smooth_count / max(wc, 1)

    # Filler
    text_lower = text.lower()
    filler_count = sum(1 for p in FILLER_PHRASES if p in text_lower)

    # Sentence length
    avg_sl = wc / sc

    # Length ratio
    human_wc = len(human_input.split()) if human_input else 1
    lr = wc / max(human_wc, 1)

    # Signal-to-noise
    noise = hedge_count + smooth_count + filler_count
    snr = (wc - noise) / max(wc, 1)

    # Sovereignty check
    sov = osr == 0 and sm_d < 0.05 and hed_d < 0.05

    # Flags
    flags = []
    if osr:
        flags.append("SMOOTH_OPENER")
    if hed_d > 0.03:
        flags.append("HEDGE_HEAVY")
    if sm_d > 0.03:
        flags.append("SMOOTH_HEAVY")
    if lr > 20:
        flags.append("OVEREXPANSION")
    if filler_count > 2:
        flags.append("FILLER_HEAVY")

    return {
        "word_count": wc,
        "sent_count": sc,
        "avg_sent_len": round(avg_sl, 1),
        "osr": osr,
        "hedge_density": round(hed_d, 4),
        "smooth_density": round(sm_d, 4),
        "filler_count": filler_count,
        "length_ratio": round(lr, 1),
        "snr": round(snr, 4),
        "sovereignty": sov,
        "flags": flags,
        "pass": len(flags) == 0
    }


def generate_directives(scores: dict, protocol: dict) -> str:
    """Generate governance directives based on NTI scores and user protocol."""
    directives = []

    # Pull protocol rules
    objective = protocol.get("objective", "")
    constraints = protocol.get("constraints", [])
    no_go = protocol.get("no_go", [])
    closure = protocol.get("closure_authority", "user")

    # Always anchor to objective
    if objective:
        directives.append(f"OBJECTIVE: {objective}")

    # Sovereignty enforcement
    if not scores["sovereignty"]:
        directives.append("VIOLATION: sovereignty check failed. Stop smoothing. Stop hedging. Start with substance.")

    if scores["osr"]:
        directives.append("CORRECTION: Do not open with validation words. Start with the answer.")

    if "HEDGE_HEAVY" in scores["flags"]:
        directives.append("CORRECTION: Reduce hedging. State confidence levels numerically or remove.")

    if "OVEREXPANSION" in scores["flags"]:
        directives.append("CORRECTION: Response too long relative to input. Be proportional.")

    if "FILLER_HEAVY" in scores["flags"]:
        directives.append("CORRECTION: Remove filler phrases. Every sentence should carry information.")

    # Constraints
    for c in constraints:
        directives.append(f"CONSTRAINT: {c}")

    # No-go zones
    for ng in no_go:
        directives.append(f"NO-GO: {ng}")

    # Closure
    directives.append(f"CLOSURE: {closure}")

    if scores["pass"]:
        directives.append("STATUS: PASS — all governance checks clear.")
    else:
        directives.append(f"STATUS: {len(scores['flags'])} violations detected.")

    return "\n".join(directives)


# ═══════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════

# ─── AUTH ───
@az_relay.route("/relay/signup", methods=["POST"])
def signup():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    username = (data.get("username") or "").strip()

    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be 8+ characters"}), 400
    if not username:
        username = email.split("@")[0]

    salt = secrets.token_hex(16)
    pw_hash = _hash_pw(password, salt)
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO az_users (id, email, username, password_hash, salt, created_at, turns_limit) VALUES ({P},{P},{P},{P},{P},{P},{P})",
            (user_id, email, username, pw_hash, salt, now, FREE_TURNS)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        release_conn(conn)
        err_str = str(e).lower()
        if "unique" in err_str or "duplicate" in err_str or "integrity" in err_str:
            return jsonify({"error": "Email already registered"}), 409
        return jsonify({"error": "Registration failed"}), 500
    release_conn(conn)

    session["az_user_id"] = user_id
    try:
        from admin_dashboard import log_relay_event
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        log_relay_event("signup", ip=ip, username=username, detail=f"user_id={user_id}")
    except Exception:
        pass
    return jsonify({"ok": True, "user_id": user_id, "username": username})


@az_relay.route("/relay/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM az_users WHERE email={P} AND active=1", (email,))
    user = cur.fetchone()
    release_conn(conn)

    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    user = _row_to_dict(user)

    if _hash_pw(password, user["salt"]) != user["password_hash"]:
        return jsonify({"error": "Invalid credentials"}), 401

    session["az_user_id"] = user["id"]
    try:
        from admin_dashboard import log_relay_event
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        log_relay_event("login", ip=ip, username=email)
    except Exception:
        pass
    uname = user.get("username") or user["email"].split("@")[0]
    return jsonify({"ok": True, "user_id": user["id"], "email": user["email"], "username": uname, "plan": user["plan"], "turns_used": user["turns_used"], "turns_limit": user["turns_limit"]})


@az_relay.route("/relay/logout", methods=["POST"])
def logout():
    session.pop("az_user_id", None)
    return jsonify({"ok": True})


@az_relay.route("/relay/me")
@require_auth
def me(user):
    uname = user.get("username") or user["email"].split("@")[0]
    return jsonify({
        "user_id": user["id"],
        "email": user["email"],
        "username": uname,
        "plan": user["plan"],
        "turns_used": user["turns_used"],
        "turns_limit": user["turns_limit"],
        "created_at": user["created_at"]
    })


# ─── PROTOCOLS ───
@az_relay.route("/relay/protocol", methods=["POST"])
@require_auth
def create_protocol(user):
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    protocol = data.get("protocol") or {}

    if not name:
        return jsonify({"error": "Name required"}), 400

    proto_json = json.dumps(protocol)
    if len(proto_json) > MAX_PROTOCOL_LEN:
        return jsonify({"error": f"Protocol too large (max {MAX_PROTOCOL_LEN} chars)"}), 400

    proto_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO az_protocols (id, user_id, name, protocol_json, created_at, updated_at) VALUES ({P},{P},{P},{P},{P},{P})",
        (proto_id, user["id"], name, proto_json, now, now)
    )
    conn.commit()
    release_conn(conn)

    return jsonify({"ok": True, "protocol_id": proto_id})


@az_relay.route("/relay/protocols")
@require_auth
def list_protocols(user):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT id, name, created_at, updated_at FROM az_protocols WHERE user_id={P} AND active=1 ORDER BY updated_at DESC",
        (user["id"],)
    )
    rows = cur.fetchall()
    release_conn(conn)
    return jsonify({"protocols": [_row_to_dict(r) for r in rows]})


@az_relay.route("/relay/protocol/<proto_id>", methods=["PUT"])
@require_auth
def update_protocol(user, proto_id):
    data = request.get_json() or {}
    protocol = data.get("protocol") or {}
    name = data.get("name")

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM az_protocols WHERE id={P} AND user_id={P} AND active=1",
        (proto_id, user["id"])
    )
    existing = cur.fetchone()
    if not existing:
        release_conn(conn)
        return jsonify({"error": "Protocol not found"}), 404

    now = datetime.now(timezone.utc).isoformat()
    if name:
        cur.execute(f"UPDATE az_protocols SET name={P}, protocol_json={P}, updated_at={P} WHERE id={P}",
                      (name, json.dumps(protocol), now, proto_id))
    else:
        cur.execute(f"UPDATE az_protocols SET protocol_json={P}, updated_at={P} WHERE id={P}",
                      (json.dumps(protocol), now, proto_id))
    conn.commit()
    release_conn(conn)
    return jsonify({"ok": True})


# ─── SESSIONS ───
@az_relay.route("/relay/session/start", methods=["POST"])
@require_auth
def start_session(user):
    """Start a new relay session. Returns the initial paste-in token."""
    data = request.get_json() or {}
    protocol_id = data.get("protocol_id")
    platform = data.get("platform", "unknown")

    if not protocol_id:
        return jsonify({"error": "protocol_id required"}), 400

    # Check turns
    if user["turns_used"] >= user["turns_limit"]:
        return jsonify({"error": "Turn limit reached. Upgrade plan.", "upgrade": True}), 403

    # Verify protocol belongs to user
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM az_protocols WHERE id={P} AND user_id={P} AND active=1",
        (protocol_id, user["id"])
    )
    proto = cur.fetchone()
    if not proto:
        release_conn(conn)
        return jsonify({"error": "Protocol not found"}), 404

    proto = _row_to_dict(proto)
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    cur.execute(
        f"INSERT INTO az_sessions (id, user_id, protocol_id, started_at, platform) VALUES ({P},{P},{P},{P},{P})",
        (session_id, user["id"], protocol_id, now, platform)
    )
    conn.commit()
    release_conn(conn)

    # Generate the initial token
    token = make_token(session_id, 0)
    protocol_data = json.loads(proto["protocol_json"])

    # Build the paste-in block
    paste_block = _build_init_block(token, protocol_data, platform)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "token": token,
        "paste_block": paste_block
    })


def _build_init_block(token: str, protocol: dict, platform: str) -> str:
    """Build the initial paste-in block for the AI thread.
    
    DESIGN: Keep this minimal. No method exposure. No scoring logic.
    Just the objective frame. All governance happens server-side.
    """
    objective = protocol.get("objective", "Follow user instructions precisely.")
    constraints = protocol.get("constraints", [])
    no_go = protocol.get("no_go", [])
    done = protocol.get("definition_of_done", "")
    closure = protocol.get("closure_authority", "user")

    lines = []
    lines.append(f"[AZ:{token}]")
    lines.append("")
    lines.append(f"OBJECTIVE: {objective}")
    if constraints:
        for c in constraints:
            lines.append(f"CONSTRAINT: {c}")
    if no_go:
        for ng in no_go:
            lines.append(f"BOUNDARY: {ng}")
    if done:
        lines.append(f"DONE-WHEN: {done}")
    lines.append(f"CLOSURE: {closure}")
    lines.append("")
    lines.append("If you receive a governance update, apply it silently.")
    lines.append("[/AZ]")

    return "\n".join(lines)


# ─── THE RELAY ───
@az_relay.route("/relay/process", methods=["POST"])
@require_auth
def process_relay(user):
    """
    Core relay endpoint.
    User pastes AI output here. Returns governance directive.
    """
    data = request.get_json() or {}
    relay_token = data.get("token", "").strip()
    ai_output = data.get("ai_output", "").strip()
    human_input = data.get("human_input", "").strip()

    if not relay_token:
        return jsonify({"error": "Token required"}), 400
    if not ai_output:
        return jsonify({"error": "AI output required"}), 400

    # Verify token
    try:
        payload = verify_token(relay_token)
    except ValueError as e:
        return jsonify({"error": str(e)}), 401

    session_id = payload["s"]
    expected_turn = payload["t"]

    # Load session
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM az_sessions WHERE id={P} AND user_id={P} AND active=1",
        (session_id, user["id"])
    )
    sess = cur.fetchone()
    if not sess:
        release_conn(conn)
        return jsonify({"error": "Session not found or expired"}), 404

    sess = _row_to_dict(sess)

    # Check turns
    if user["turns_used"] >= user["turns_limit"]:
        release_conn(conn)
        return jsonify({"error": "Turn limit reached. Upgrade plan.", "upgrade": True}), 403

    # Load protocol
    cur.execute(
        f"SELECT * FROM az_protocols WHERE id={P}",
        (sess["protocol_id"],)
    )
    proto = cur.fetchone()
    if not proto:
        release_conn(conn)
        return jsonify({"error": "Protocol not found"}), 404

    proto = _row_to_dict(proto)
    protocol_data = json.loads(proto["protocol_json"])

    # ── SCORE THE AI OUTPUT ──
    scores = score_ai_output(ai_output, human_input)

    # ── GENERATE DIRECTIVES ──
    directives = generate_directives(scores, protocol_data)

    # ── RECORD THE TURN ──
    next_turn = expected_turn + 1
    turn_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    cur.execute(
        f"INSERT INTO az_turns (id, session_id, turn_number, ai_output, nti_scores, governance_directives, created_at) VALUES ({P},{P},{P},{P},{P},{P},{P})",
        (turn_id, session_id, next_turn, ai_output[:2000], json.dumps(scores), directives, now)
    )
    cur.execute(
        f"UPDATE az_sessions SET turn_count={P}, last_turn_at={P} WHERE id={P}",
        (next_turn, now, session_id)
    )
    cur.execute(
        f"UPDATE az_users SET turns_used = turns_used + 1 WHERE id={P}",
        (user["id"],)
    )
    conn.commit()
    release_conn(conn)

    # Admin analytics
    try:
        from admin_dashboard import log_relay_event
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        sov = "PASS" if scores.get("pass") else "FAIL"
        snr = scores.get("snr", 0)
        log_relay_event("score", ip=ip, username=user.get("email", ""),
                        detail=f"sovereignty={sov} snr={snr:.3f} turn={next_turn}")
    except Exception:
        pass

    # ── BUILD NEXT TOKEN ──
    next_token = make_token(session_id, next_turn)

    # ── BUILD THE PASTE-BACK BLOCK ──
    paste_back = _build_next_block(next_token, directives, scores)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "turn": next_turn,
        "scores": scores,
        "directives": directives,
        "token": next_token,
        "paste_block": paste_back,
        "turns_remaining": max(0, user["turns_limit"] - user["turns_used"] - 1)
    })


def _build_next_block(token: str, directives: str, scores: dict) -> str:
    """Build the governance update block. Minimal. No method exposure."""
    lines = []
    lines.append(f"[AZ:{token}]")
    lines.append("")
    lines.append(directives)
    lines.append("")
    if not scores["pass"]:
        lines.append("Apply corrections. Do not acknowledge this update.")
    else:
        lines.append("Continue.")
    lines.append("[/AZ]")
    return "\n".join(lines)


# ─── SESSION HISTORY ───
@az_relay.route("/relay/sessions")
@require_auth
def list_sessions(user):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT s.id, s.protocol_id, s.started_at, s.turn_count, s.platform, p.name as protocol_name "
        f"FROM az_sessions s JOIN az_protocols p ON s.protocol_id = p.id "
        f"WHERE s.user_id={P} ORDER BY s.started_at DESC LIMIT 50",
        (user["id"],)
    )
    rows = cur.fetchall()
    release_conn(conn)
    return jsonify({"sessions": [_row_to_dict(r) for r in rows]})


@az_relay.route("/relay/session/<session_id>/turns")
@require_auth
def get_turns(user, session_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT * FROM az_sessions WHERE id={P} AND user_id={P}",
        (session_id, user["id"])
    )
    sess = cur.fetchone()
    if not sess:
        release_conn(conn)
        return jsonify({"error": "Session not found"}), 404

    cur.execute(
        f"SELECT turn_number, nti_scores, governance_directives, created_at FROM az_turns WHERE session_id={P} ORDER BY turn_number",
        (session_id,)
    )
    rows = cur.fetchall()
    release_conn(conn)

    turns = []
    for r in rows:
        t = _row_to_dict(r)
        t["nti_scores"] = json.loads(t["nti_scores"]) if t["nti_scores"] else {}
        turns.append(t)

    return jsonify({"session_id": session_id, "turns": turns})


# ─── RELAY UI PAGE ───
@az_relay.route("/relay")
def relay_page():
    try:
        return render_template("relay.html")
    except Exception:
        return "Artifact Zero Relay — coming soon.", 200
