# your_os.py
# Your OS — Multi-Model AI Control Room Backend
# Flask Blueprint. Plugs into existing app.py.
# No LLM in the engine. We proxy to user's chosen models.

import os
import json
import uuid
import hashlib
import hmac
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, render_template, session

import db as database
from psycopg2.extras import RealDictCursor

# Optional: real API calls when network is available
try:
    import requests as http_requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

your_os = Blueprint('your_os', __name__)

# ============================================================
# CONFIG
# ============================================================
YOUR_OS_VERSION = "your-os-v1.1"
ENCRYPTION_KEY = os.getenv("YOUR_OS_ENC_KEY", "artifact-zero-default-key-change-in-prod")

# --- FREE TRIAL CONFIG ---
# House key for "New to AI" users — 5 free messages, your dime
HOUSE_PROVIDER = "openai"  # which model free users get
HOUSE_API_KEY = os.getenv("OPENAI_API_KEY", "")  # your key from Render env vars
FREE_TRIAL_LIMIT = 5  # messages per session
DB_WRITE_WARN_MS = 200  # log warning if db write exceeds this

# ============================================================
# DATABASE
# ============================================================
def os_db():
    """Return a canonical PostgreSQL connection."""
    return database.db_connect()



def db_commit_timed(conn):
    """Commit with latency monitoring. Logs warning if slow."""
    t0 = time.time()
    conn.commit()
    ms = int((time.time() - t0) * 1000)
    if ms > DB_WRITE_WARN_MS:
        print(f"[WARN] your_os db commit took {ms}ms — consider Postgres migration")


def os_db_init():
    """Create the Your OS PostgreSQL schema and indexes."""
    conn = os_db()
    try:
        cur = conn.cursor()
        statements = [
            """
            CREATE TABLE IF NOT EXISTS os_users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT,
                os_name TEXT DEFAULT '_OS',
                tier TEXT DEFAULT 'free',
                stripe_customer_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_protocols (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES os_users(id) ON DELETE CASCADE,
                os_name TEXT NOT NULL,
                objective TEXT,
                constraints TEXT,
                no_go_zones TEXT,
                definition_of_done TEXT,
                closure_authority TEXT DEFAULT 'human',
                version INTEGER DEFAULT 1,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES os_users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                encrypted_key TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES os_users(id) ON DELETE CASCADE,
                title TEXT,
                task_number TEXT,
                keywords TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES os_conversations(id) ON DELETE CASCADE,
                group_id TEXT NOT NULL,
                role TEXT NOT NULL,
                provider TEXT,
                content TEXT NOT NULL,
                nti_score DOUBLE PRECISION,
                constraints_followed INTEGER,
                constraints_total INTEGER,
                chosen BOOLEAN NOT NULL DEFAULT FALSE,
                tokens_in INTEGER,
                tokens_out INTEGER,
                latency_ms INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES os_users(id) ON DELETE CASCADE,
                conversation_id TEXT REFERENCES os_conversations(id) ON DELETE SET NULL,
                task_number TEXT NOT NULL,
                title TEXT,
                keywords TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_trial_sessions (
                id TEXT PRIMARY KEY,
                ip TEXT,
                user_agent TEXT,
                user_name TEXT,
                os_name TEXT,
                path TEXT,
                provider TEXT,
                message_count INTEGER DEFAULT 0,
                has_own_key BOOLEAN NOT NULL DEFAULT FALSE,
                protocol_json JSONB,
                first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS os_trial_messages (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES os_trial_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                provider TEXT,
                content TEXT,
                nti_score DOUBLE PRECISION,
                is_trial BOOLEAN NOT NULL DEFAULT TRUE,
                latency_ms INTEGER,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_os_api_keys_user_provider ON os_api_keys(user_id, provider)",
            "CREATE INDEX IF NOT EXISTS idx_os_protocols_user_updated ON os_protocols(user_id, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_os_conversations_user_updated ON os_conversations(user_id, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_os_messages_conversation_created ON os_messages(conversation_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_os_messages_group ON os_messages(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_os_tasks_user_created ON os_tasks(user_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_os_trial_sessions_last_seen ON os_trial_sessions(last_seen DESC)",
            "CREATE INDEX IF NOT EXISTS idx_os_trial_messages_session ON os_trial_messages(session_id, created_at)",
        ]
        for statement in statements:
            cur.execute(statement)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



# ============================================================
# CRYPTO (simple key encryption for API keys)
# ============================================================
def encrypt_key(plaintext: str) -> str:
    """XOR-based encryption with HMAC. Not production-grade — use Fernet in prod."""
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    encrypted = bytes([b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(plaintext.encode())])
    mac = hmac.new(key_bytes, encrypted, hashlib.sha256).hexdigest()[:16]
    return mac + ":" + encrypted.hex()


def decrypt_key(ciphertext: str) -> str:
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    parts = ciphertext.split(":", 1)
    if len(parts) != 2:
        return ""
    mac_expected, hex_data = parts
    encrypted = bytes.fromhex(hex_data)
    mac_actual = hmac.new(key_bytes, encrypted, hashlib.sha256).hexdigest()[:16]
    if mac_expected != mac_actual:
        return ""
    return bytes([b ^ key_bytes[i % len(key_bytes)] for i, b in enumerate(encrypted)]).decode()


# ============================================================
# AUTH
# ============================================================
def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
    return salt + ":" + h


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split(":", 1)
    if len(parts) != 2:
        return False
    salt, expected = parts
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000).hex()
    return h == expected


def get_user_id():
    """Get user ID from session or bearer token."""
    uid = session.get("your_os_user_id")
    if uid:
        return uid
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    conn = os_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id FROM os_users WHERE id = %s", (token,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()



def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = get_user_id()
        if not uid:
            return jsonify({"error": "Authentication required"}), 401
        return f(uid, *args, **kwargs)
    return decorated


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# PROTOCOL BUILDER
# ============================================================
def build_system_prompt(protocol: dict) -> str:
    """Build the system prompt from a protocol config."""
    name = protocol.get('os_name', '_OS')
    obj = protocol.get('objective', '')
    constraints = protocol.get('constraints', '')
    no_go = protocol.get('no_go_zones', '')
    done = protocol.get('definition_of_done', '')
    closure = protocol.get('closure_authority', 'human')

    closure_map = {
        'human': 'The human decides when the task is complete.',
        'system': 'The system closes when the definition of done is met.',
        'both': 'Either the human or the system can declare closure.'
    }

    prompt = f"## {name} — Operating System\n"
    prompt += "## Enforcement: Binding. No bypasses.\n\n"
    prompt += f"OBJECTIVE:\n{obj}\n\n"

    if constraints:
        prompt += "CONSTRAINTS:\n"
        for line in constraints.split('\n'):
            line = line.strip()
            if line:
                prompt += f"— {line}\n"
        prompt += "\n"

    if no_go:
        prompt += "NO-GO ZONES:\n"
        for line in no_go.split('\n'):
            line = line.strip()
            if line:
                prompt += f"✗ {line}\n"
        prompt += "\n"

    prompt += f"DEFINITION OF DONE:\n{done}\n\n"
    prompt += f"CLOSURE AUTHORITY:\n{closure_map.get(closure, closure_map['human'])}\n\n"
    prompt += "## BINDING CONTRACT (enforced every conversation)\n"
    prompt += "1. Objective is frozen before execution begins.\n"
    prompt += "2. Emotion may be acknowledged, never executed.\n"
    prompt += "3. Constraints cannot be deleted; only appended explicitly.\n"
    prompt += "4. If ambiguity exists, request clarification OR run analysis-only mode.\n"

    return prompt


# ============================================================
# MULTI-MODEL PROXY
# ============================================================
PROVIDER_CONFIGS = {
    'openai': {
        'url': 'https://api.openai.com/v1/chat/completions',
        'model': 'gpt-4.1-mini',
        'auth_header': 'Authorization',
        'auth_prefix': 'Bearer ',
    },
    'anthropic': {
        'url': 'https://api.anthropic.com/v1/messages',
        'model': 'claude-sonnet-4-5-20250929',
        'auth_header': 'x-api-key',
        'auth_prefix': '',
    },
    'google': {
        'url': 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent',
        'auth_header': None,  # key goes in URL param
        'auth_prefix': '',
    }
}


def call_openai(api_key: str, system_prompt: str, messages: list) -> dict:
    """Call OpenAI API."""
    t0 = time.time()
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    body = {
        'model': 'gpt-4.1-mini',
        'messages': [{'role': 'system', 'content': system_prompt}] + messages,
        'max_tokens': 2000
    }
    try:
        resp = http_requests.post(
            'https://api.openai.com/v1/chat/completions',
            headers=headers, json=body, timeout=30
        )
        data = resp.json()
        latency = int((time.time() - t0) * 1000)

        if 'choices' in data:
            choice = data['choices'][0]
            return {
                'content': choice['message']['content'],
                'tokens_in': data.get('usage', {}).get('prompt_tokens', 0),
                'tokens_out': data.get('usage', {}).get('completion_tokens', 0),
                'latency_ms': latency,
                'error': None
            }
        else:
            return {'content': '', 'tokens_in': 0, 'tokens_out': 0, 'latency_ms': latency,
                    'error': data.get('error', {}).get('message', 'Unknown error')}
    except Exception as e:
        return {'content': '', 'tokens_in': 0, 'tokens_out': 0,
                'latency_ms': int((time.time() - t0) * 1000), 'error': str(e)}


def call_anthropic(api_key: str, system_prompt: str, messages: list) -> dict:
    """Call Anthropic API."""
    t0 = time.time()
    headers = {
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json'
    }
    body = {
        'model': 'claude-sonnet-4-5-20250929',
        'max_tokens': 2000,
        'system': system_prompt,
        'messages': messages
    }
    try:
        resp = http_requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers, json=body, timeout=30
        )
        data = resp.json()
        latency = int((time.time() - t0) * 1000)

        if 'content' in data:
            text = ''.join(b.get('text', '') for b in data['content'] if b.get('type') == 'text')
            return {
                'content': text,
                'tokens_in': data.get('usage', {}).get('input_tokens', 0),
                'tokens_out': data.get('usage', {}).get('output_tokens', 0),
                'latency_ms': latency,
                'error': None
            }
        else:
            return {'content': '', 'tokens_in': 0, 'tokens_out': 0, 'latency_ms': latency,
                    'error': data.get('error', {}).get('message', 'Unknown error')}
    except Exception as e:
        return {'content': '', 'tokens_in': 0, 'tokens_out': 0,
                'latency_ms': int((time.time() - t0) * 1000), 'error': str(e)}


def call_google(api_key: str, system_prompt: str, messages: list) -> dict:
    """Call Google Gemini API."""
    t0 = time.time()
    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}'

    # Convert messages to Gemini format
    contents = []
    for msg in messages:
        role = 'user' if msg['role'] == 'user' else 'model'
        contents.append({'role': role, 'parts': [{'text': msg['content']}]})

    body = {
        'system_instruction': {'parts': [{'text': system_prompt}]},
        'contents': contents,
        'generationConfig': {'maxOutputTokens': 2000}
    }
    try:
        resp = http_requests.post(url, json=body, timeout=30)
        data = resp.json()
        latency = int((time.time() - t0) * 1000)

        if 'candidates' in data:
            text = data['candidates'][0].get('content', {}).get('parts', [{}])[0].get('text', '')
            usage = data.get('usageMetadata', {})
            return {
                'content': text,
                'tokens_in': usage.get('promptTokenCount', 0),
                'tokens_out': usage.get('candidatesTokenCount', 0),
                'latency_ms': latency,
                'error': None
            }
        else:
            return {'content': '', 'tokens_in': 0, 'tokens_out': 0, 'latency_ms': latency,
                    'error': json.dumps(data.get('error', 'Unknown error'))}
    except Exception as e:
        return {'content': '', 'tokens_in': 0, 'tokens_out': 0,
                'latency_ms': int((time.time() - t0) * 1000), 'error': str(e)}


PROVIDER_CALLERS = {
    'openai': call_openai,
    'anthropic': call_anthropic,
    'google': call_google,
}


# ============================================================
# NTI SCORING (lightweight, for response quality)
# ============================================================
def quick_nti_score(text: str, protocol: dict) -> dict:
    """Quick NTI-style scoring of a response against protocol constraints."""
    if not text:
        return {'score': 0.0, 'followed': 0, 'total': 0}

    text_lower = text.lower()
    constraints = protocol.get('constraints', '').split('\n')
    constraints = [c.strip() for c in constraints if c.strip()]
    no_gos = protocol.get('no_go_zones', '').split('\n')
    no_gos = [n.strip() for n in no_gos if n.strip()]

    total_rules = len(constraints) + len(no_gos)
    if total_rules == 0:
        return {'score': 0.75, 'followed': 0, 'total': 0}

    violations = 0

    # Check no-go zones
    for ng in no_gos:
        ng_lower = ng.lower().replace("never ", "").replace("don't ", "").replace("no ", "")
        # Simple keyword check
        if any(word in text_lower for word in ng_lower.split() if len(word) > 4):
            violations += 1

    # Check constraints (inverted — looking for violations)
    for con in constraints:
        con_lower = con.lower()
        if 'no emoji' in con_lower and any(ord(c) > 127 for c in text):
            violations += 1
        if 'no preamble' in con_lower and text_lower.startswith(('sure', 'of course', 'absolutely', 'great question')):
            violations += 1
        if 'no filler' in con_lower and any(f in text_lower for f in ['i hope this helps', 'feel free to', 'don\'t hesitate']):
            violations += 1

    followed = total_rules - violations
    score = max(0.0, min(1.0, followed / total_rules)) if total_rules > 0 else 0.75

    return {'score': round(score, 2), 'followed': max(0, followed), 'total': total_rules}


# ============================================================
# ROUTES: PAGES
# ============================================================
@your_os.route('/your-os')
def your_os_home():
    """Landing page — two-door entry, protocol builder."""
    try:
        return render_template('your-os.html')
    except Exception:
        return "Your OS — coming soon."


@your_os.route('/your-os/app')
def your_os_app():
    """The control room app."""
    try:
        return render_template('your-os-app.html')
    except Exception:
        return "Your OS App — coming soon."


# ============================================================
# ROUTES: FREE TRIAL CHAT (no auth required)
# ============================================================
@your_os.route('/api/os/trial', methods=['POST'])
def os_trial_chat():
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    protocol = data.get("protocol", {})
    provider = data.get("provider", HOUSE_PROVIDER)
    if not message:
        return jsonify({"error": "Message required"}), 400
    trial_count = session.get("your_os_trial_count", 0)
    if trial_count >= FREE_TRIAL_LIMIT:
        return jsonify({
            "error": "trial_exhausted",
            "message": f"Free trial complete ({FREE_TRIAL_LIMIT} messages). Connect your own API key to continue.",
            "trial_count": trial_count,
            "trial_limit": FREE_TRIAL_LIMIT,
        }), 429

    user_key = data.get("api_key", "").strip()
    if user_key:
        api_key = user_key
        is_trial = False
    else:
        if not HOUSE_API_KEY:
            return jsonify({"error": "no_house_key", "message": "Trial not available. Connect your own API key."}), 503
        api_key = HOUSE_API_KEY
        provider = HOUSE_PROVIDER
        is_trial = True

    system_prompt = build_system_prompt(protocol)
    messages_for_api = [
        {"role": item.get("role", "user"), "content": item.get("content", "")}
        for item in data.get("history", [])[-10:]
    ]
    messages_for_api.append({"role": "user", "content": message})

    caller = PROVIDER_CALLERS.get(provider)
    if not caller or not HAS_REQUESTS:
        return jsonify({"error": "network_unavailable", "message": "API calls not available on this server."}), 503
    result = caller(api_key, system_prompt, messages_for_api)
    if result.get("error"):
        return jsonify({"error": "api_error", "message": result["error"], "provider": provider}), 502

    nti = quick_nti_score(result.get("content", ""), protocol)
    if is_trial:
        session["your_os_trial_count"] = trial_count + 1

    conn = None
    try:
        conn = os_db()
        cur = conn.cursor()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        user_agent = request.headers.get("User-Agent", "")[:200]
        session_id = session.get("your_os_session_id")
        now = utc_now()
        if not session_id:
            session_id = str(uuid.uuid4())
            session["your_os_session_id"] = session_id
            cur.execute("""
                INSERT INTO os_trial_sessions (
                    id, ip, user_agent, user_name, os_name, path, provider,
                    message_count, has_own_key, protocol_json, first_seen, last_seen
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s, %s::jsonb, %s, %s)
                ON CONFLICT (id) DO NOTHING
            """, (
                session_id, ip, user_agent, protocol.get("user_name", ""),
                protocol.get("os_name", ""), data.get("path", "unknown"),
                provider, bool(user_key), json.dumps(protocol), now, now,
            ))
        cur.execute("""
            UPDATE os_trial_sessions
            SET message_count = message_count + 1,
                last_seen = %s,
                provider = %s,
                has_own_key = %s
            WHERE id = %s
        """, (now, provider, bool(user_key), session_id))
        cur.execute("""
            INSERT INTO os_trial_messages (
                id, session_id, role, provider, content, nti_score,
                is_trial, latency_ms, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), session_id, "user", None, message, None, is_trial, 0, now))
        cur.execute("""
            INSERT INTO os_trial_messages (
                id, session_id, role, provider, content, nti_score,
                is_trial, latency_ms, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            str(uuid.uuid4()), session_id, "assistant", provider,
            result.get("content", "")[:500], nti["score"], is_trial,
            result.get("latency_ms", 0), now,
        ))
        db_commit_timed(conn)
    except Exception as error:
        if conn is not None:
            conn.rollback()
        print(f"[WARN] trial tracking error: {error}", flush=True)
    finally:
        if conn is not None:
            conn.close()

    return jsonify({
        "ok": True,
        "provider": provider,
        "is_trial": is_trial,
        "trial_count": session.get("your_os_trial_count", 0),
        "trial_limit": FREE_TRIAL_LIMIT,
        "content": result.get("content", ""),
        "nti_score": nti["score"],
        "constraints_followed": nti["followed"],
        "constraints_total": nti["total"],
        "tokens_in": result.get("tokens_in", 0),
        "tokens_out": result.get("tokens_out", 0),
        "latency_ms": result.get("latency_ms", 0),
    })



@your_os.route('/api/os/signup', methods=['POST'])
def os_signup():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    name = data.get("name", "")
    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be 6+ characters"}), 400

    user_id = str(uuid.uuid4())
    os_name = name[0].upper() + "OS" if name else "_OS"
    conn = os_db()
    try:
        cur = conn.cursor()
        now = utc_now()
        cur.execute("""
            INSERT INTO os_users (id, email, password_hash, display_name, os_name, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, email, hash_password(password), name, os_name, now))
        cur.execute("""
            INSERT INTO os_protocols (
                id, user_id, os_name, objective, constraints, no_go_zones,
                definition_of_done, closure_authority, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), user_id, os_name, "", "", "", "", "human", now, now))
        db_commit_timed(conn)
    except Exception as error:
        conn.rollback()
        if getattr(error, "pgcode", None) == "23505":
            return jsonify({"error": "Email already registered"}), 409
        raise
    finally:
        conn.close()

    session["your_os_user_id"] = user_id
    return jsonify({"ok": True, "user_id": user_id, "os_name": os_name})



@your_os.route('/api/os/login', methods=['POST'])
def os_login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, password_hash, os_name FROM os_users WHERE email = %s", (email,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or not verify_password(password, row["password_hash"]):
        return jsonify({"error": "Invalid email or password"}), 401
    session["your_os_user_id"] = row["id"]
    return jsonify({"ok": True, "user_id": row["id"], "os_name": row["os_name"]})



@your_os.route('/api/os/logout', methods=['POST'])
def os_logout():
    session.pop('your_os_user_id', None)
    return jsonify({'ok': True})


# ============================================================
# ROUTES: PROTOCOL
# ============================================================
@your_os.route('/api/os/protocol', methods=['GET'])
@require_auth
def os_get_protocol(user_id):
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM os_protocols
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
        """, (user_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "No protocol found"}), 404
    return jsonify(dict(row))



@your_os.route('/api/os/protocol', methods=['POST'])
@require_auth
def os_save_protocol(user_id):
    data = request.get_json() or {}
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, version FROM os_protocols
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
            FOR UPDATE
        """, (user_id,))
        existing = cur.fetchone()
        now = utc_now()
        version = (existing["version"] or 0) + 1 if existing else 1
        values = (
            data.get("os_name", "_OS"),
            data.get("objective", ""),
            data.get("constraints", ""),
            data.get("no_go_zones", ""),
            data.get("definition_of_done", ""),
            data.get("closure_authority", "human"),
        )
        if existing:
            cur.execute("""
                UPDATE os_protocols
                SET os_name = %s, objective = %s, constraints = %s,
                    no_go_zones = %s, definition_of_done = %s,
                    closure_authority = %s, version = %s, updated_at = %s
                WHERE id = %s
            """, values + (version, now, existing["id"]))
        else:
            cur.execute("""
                INSERT INTO os_protocols (
                    id, user_id, os_name, objective, constraints, no_go_zones,
                    definition_of_done, closure_authority, version, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id) + values + (version, now, now))
        cur.execute("UPDATE os_users SET os_name = %s WHERE id = %s", (values[0], user_id))
        db_commit_timed(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify({"ok": True, "version": version})



# ============================================================
# ROUTES: API KEYS
# ============================================================
@your_os.route('/api/os/keys', methods=['POST'])
@require_auth
def os_save_keys(user_id):
    data = request.get_json() or {}
    conn = os_db()
    try:
        cur = conn.cursor()
        now = utc_now()
        for provider in ("openai", "anthropic", "google"):
            key_value = data.get(provider, "").strip()
            if not key_value:
                continue
            cur.execute("""
                INSERT INTO os_api_keys (id, user_id, provider, encrypted_key, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, provider)
                DO UPDATE SET encrypted_key = EXCLUDED.encrypted_key
            """, (str(uuid.uuid4()), user_id, provider, encrypt_key(key_value), now))
        db_commit_timed(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify({"ok": True})



@your_os.route('/api/os/keys', methods=['GET'])
@require_auth
def os_get_keys(user_id):
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT provider, encrypted_key FROM os_api_keys WHERE user_id = %s", (user_id,))
        rows = cur.fetchall()
    finally:
        conn.close()
    connected = {}
    for row in rows:
        key = decrypt_key(row["encrypted_key"])
        connected[row["provider"]] = bool(key and len(key) > 5)
    return jsonify({"connected": connected})



# ============================================================
# ROUTES: CHAT (THE CORE)
# ============================================================
@your_os.route('/api/os/chat', methods=['POST'])
@require_auth
def os_chat(user_id):
    data = request.get_json() or {}
    message = data.get("message", "").strip()
    conversation_id = data.get("conversation_id")
    providers = data.get("providers", ["openai", "anthropic", "google"])
    if not message:
        return jsonify({"error": "Message required"}), 400

    now = utc_now()
    group_id = str(uuid.uuid4())
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM os_protocols
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT 1
        """, (user_id,))
        protocol_row = cur.fetchone()
        protocol = dict(protocol_row) if protocol_row else {}
        system_prompt = build_system_prompt(protocol)

        conversation = None
        if conversation_id:
            cur.execute("""
                SELECT * FROM os_conversations
                WHERE id = %s AND user_id = %s
            """, (conversation_id, user_id))
            conversation = cur.fetchone()

        if not conversation:
            cur.execute("SELECT COUNT(*) AS cnt FROM os_conversations WHERE user_id = %s", (user_id,))
            count = cur.fetchone()["cnt"]
            conversation_id = str(uuid.uuid4())
            task_number = f"T-{count + 1}"
            title = message[:60] + ("..." if len(message) > 60 else "")
            keywords = " ".join(dict.fromkeys(message.lower().split()[:10]))
            cur.execute("""
                INSERT INTO os_conversations (
                    id, user_id, title, task_number, keywords, status, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (conversation_id, user_id, title, task_number, keywords, "active", now, now))
            cur.execute("""
                INSERT INTO os_tasks (
                    id, user_id, conversation_id, task_number, title, keywords, status, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (str(uuid.uuid4()), user_id, conversation_id, task_number, title, keywords, "open", now))

        cur.execute("""
            SELECT role, provider, content, chosen
            FROM os_messages
            WHERE conversation_id = %s
            ORDER BY created_at
        """, (conversation_id,))
        history_rows = cur.fetchall()

        cur.execute("""
            INSERT INTO os_messages (id, conversation_id, group_id, role, content, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (str(uuid.uuid4()), conversation_id, group_id, "user", message, now))

        cur.execute("""
            SELECT provider, encrypted_key
            FROM os_api_keys
            WHERE user_id = %s
        """, (user_id,))
        encrypted_keys = {row["provider"]: row["encrypted_key"] for row in cur.fetchall()}
        db_commit_timed(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    messages_for_api = []
    allow_first_assistant = False
    for row in history_rows:
        if row["role"] == "user":
            messages_for_api.append({"role": "user", "content": row["content"]})
            allow_first_assistant = True
        elif row["role"] == "assistant" and (row["chosen"] or allow_first_assistant):
            messages_for_api.append({"role": "assistant", "content": row["content"]})
            allow_first_assistant = False
    messages_for_api.append({"role": "user", "content": message})

    responses = {}
    response_rows = []
    for provider in providers:
        encrypted_key = encrypted_keys.get(provider)
        if not encrypted_key:
            responses[provider] = {
                "content": f"No API key configured for {provider}. Add your key in Settings.",
                "error": "no_key", "nti_score": 0, "followed": 0, "total": 0,
                "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
            }
            continue

        api_key = decrypt_key(encrypted_key)
        if not api_key:
            responses[provider] = {
                "content": f"Failed to decrypt {provider} API key.",
                "error": "decrypt_failed", "nti_score": 0, "followed": 0, "total": 0,
                "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
            }
            continue

        caller = PROVIDER_CALLERS.get(provider)
        if caller and HAS_REQUESTS:
            result = caller(api_key, system_prompt, messages_for_api)
        else:
            result = {
                "content": f"[Simulated {provider} response — requests library not available or network disabled]",
                "tokens_in": len(message.split()) * 2,
                "tokens_out": 50,
                "latency_ms": 100,
                "error": None,
            }

        nti = quick_nti_score(result.get("content", ""), protocol)
        response = {
            "content": result.get("content", ""),
            "error": result.get("error"),
            "nti_score": nti["score"],
            "followed": nti["followed"],
            "total": nti["total"],
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "latency_ms": result.get("latency_ms", 0),
        }
        responses[provider] = response
        response_rows.append((
            str(uuid.uuid4()), conversation_id, group_id, "assistant", provider,
            response["content"], response["nti_score"], response["followed"],
            response["total"], response["tokens_in"], response["tokens_out"],
            response["latency_ms"], utc_now(),
        ))

    conn = os_db()
    try:
        cur = conn.cursor()
        for row in response_rows:
            cur.execute("""
                INSERT INTO os_messages (
                    id, conversation_id, group_id, role, provider, content,
                    nti_score, constraints_followed, constraints_total,
                    tokens_in, tokens_out, latency_ms, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, row)
        cur.execute("""
            UPDATE os_conversations
            SET updated_at = %s
            WHERE id = %s AND user_id = %s
        """, (utc_now(), conversation_id, user_id))
        db_commit_timed(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "conversation_id": conversation_id,
        "group_id": group_id,
        "responses": responses,
    })



@your_os.route('/api/os/chat/choose', methods=['POST'])
@require_auth
def os_choose_response(user_id):
    data = request.get_json() or {}
    group_id = data.get("group_id")
    provider = data.get("provider")
    if not group_id or not provider:
        return jsonify({"error": "group_id and provider required"}), 400

    conn = os_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE os_messages AS message
            SET chosen = FALSE
            FROM os_conversations AS conversation
            WHERE message.conversation_id = conversation.id
              AND conversation.user_id = %s
              AND message.group_id = %s
              AND message.role = 'assistant'
        """, (user_id, group_id))
        cur.execute("""
            UPDATE os_messages AS message
            SET chosen = TRUE
            FROM os_conversations AS conversation
            WHERE message.conversation_id = conversation.id
              AND conversation.user_id = %s
              AND message.group_id = %s
              AND message.provider = %s
        """, (user_id, group_id, provider))
        db_commit_timed(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify({"ok": True})



# ============================================================
# ROUTES: CONVERSATIONS & SEARCH
# ============================================================
@your_os.route('/api/os/conversations', methods=['GET'])
@require_auth
def os_list_conversations(user_id):
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, title, task_number, keywords, status, created_at, updated_at
            FROM os_conversations
            WHERE user_id = %s
            ORDER BY updated_at DESC
            LIMIT 50
        """, (user_id,))
        rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify({"conversations": [dict(row) for row in rows]})



@your_os.route('/api/os/conversations/<conv_id>', methods=['GET'])
@require_auth
def os_get_conversation(user_id, conv_id):
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM os_conversations
            WHERE id = %s AND user_id = %s
        """, (conv_id, user_id))
        conversation = cur.fetchone()
        if not conversation:
            return jsonify({"error": "Not found"}), 404
        cur.execute("""
            SELECT * FROM os_messages
            WHERE conversation_id = %s
            ORDER BY created_at
        """, (conv_id,))
        messages = cur.fetchall()
        return jsonify({
            "conversation": dict(conversation),
            "messages": [dict(message) for message in messages],
        })
    finally:
        conn.close()



@your_os.route('/api/os/search', methods=['GET'])
@require_auth
def os_search(user_id):
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"results": []})
    query_pattern = f"%{query}%"
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, title, task_number, keywords, status, updated_at
            FROM os_conversations
            WHERE user_id = %s
              AND (title ILIKE %s OR task_number ILIKE %s OR keywords ILIKE %s)
            ORDER BY updated_at DESC
            LIMIT 20
        """, (user_id, query_pattern, query_pattern, query_pattern))
        conversations = cur.fetchall()
        cur.execute("""
            SELECT message.id, message.conversation_id, message.role,
                   message.provider, message.content, message.created_at,
                   conversation.title AS conv_title, conversation.task_number
            FROM os_messages AS message
            JOIN os_conversations AS conversation
              ON message.conversation_id = conversation.id
            WHERE conversation.user_id = %s
              AND message.content ILIKE %s
            ORDER BY message.created_at DESC
            LIMIT 20
        """, (user_id, query_pattern))
        messages = cur.fetchall()
    finally:
        conn.close()
    return jsonify({
        "conversations": [dict(row) for row in conversations],
        "messages": [dict(row) for row in messages],
    })



# ============================================================
# ROUTES: TASK REGISTRY
# ============================================================
@your_os.route('/api/os/tasks', methods=['GET'])
@require_auth
def os_list_tasks(user_id):
    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT * FROM os_tasks
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (user_id,))
        rows = cur.fetchall()
    finally:
        conn.close()
    return jsonify({"tasks": [dict(row) for row in rows]})



@your_os.route('/api/os/tasks/<task_id>/close', methods=['POST'])
@require_auth
def os_close_task(user_id, task_id):
    conn = os_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE os_tasks
            SET status = 'closed', closed_at = %s
            WHERE id = %s AND user_id = %s
        """, (utc_now(), task_id, user_id))
        db_commit_timed(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify({"ok": True})



# ============================================================
# ADMIN DASHBOARD (token-protected)
# ============================================================
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


@your_os.route('/your-os/admin')
def os_admin_page():
    """Admin dashboard protected by ADMIN_TOKEN."""
    token = request.args.get("token", "")
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return "Unauthorized", 401

    conn = os_db()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) AS cnt FROM os_trial_sessions")
        total_sessions = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM os_trial_messages")
        total_messages = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(DISTINCT ip) AS cnt FROM os_trial_sessions")
        unique_ips = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM os_trial_sessions WHERE has_own_key = TRUE")
        own_key_users = cur.fetchone()["cnt"]
        cur.execute("SELECT COUNT(*) AS cnt FROM os_users")
        registered_users = cur.fetchone()["cnt"]

        cur.execute("""
            SELECT session_record.*,
                   (
                       SELECT COUNT(*)
                       FROM os_trial_messages AS message
                       WHERE message.session_id = session_record.id
                         AND message.role = 'user'
                   ) AS msg_count
            FROM os_trial_sessions AS session_record
            ORDER BY session_record.last_seen DESC
            LIMIT 50
        """)
        sessions = cur.fetchall()

        cur.execute("""
            SELECT message.*, session_record.user_name,
                   session_record.os_name, session_record.ip
            FROM os_trial_messages AS message
            LEFT JOIN os_trial_sessions AS session_record
              ON message.session_id = session_record.id
            ORDER BY message.created_at DESC
            LIMIT 100
        """)
        messages = cur.fetchall()
    finally:
        conn.close()

    def display_time(value):
        if value is None:
            return "—"
        if isinstance(value, datetime):
            return value.isoformat()[:19]
        return str(value)[:19]

    sessions_html = ""
    for item in sessions:
        sessions_html += f"""<tr>
            <td>{item['user_name'] or '—'}</td>
            <td>{item['os_name'] or '—'}</td>
            <td>{item['provider'] or '—'}</td>
            <td>{item['message_count']}</td>
            <td>{'✓' if item['has_own_key'] else '—'}</td>
            <td>{item['ip'] or '—'}</td>
            <td>{display_time(item['first_seen'])}</td>
            <td>{display_time(item['last_seen'])}</td>
        </tr>"""

    messages_html = ""
    for item in messages:
        raw_content = item["content"] or ""
        content_preview = raw_content[:120].replace("<", "&lt;")
        title_content = raw_content.replace('"', "&quot;")[:500]
        messages_html += f"""<tr>
            <td>{item['user_name'] or '—'}</td>
            <td>{item['role']}</td>
            <td>{item['provider'] or '—'}</td>
            <td title="{title_content}">{content_preview}{'...' if len(raw_content) > 120 else ''}</td>
            <td>{item['nti_score'] if item['nti_score'] is not None else '—'}</td>
            <td>{'trial' if item['is_trial'] else 'own key'}</td>
            <td>{display_time(item['created_at'])}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your OS — Admin</title>
<style>
body{{background:#080a10;color:#e4e7f0;font-family:'Courier New',monospace;padding:20px;font-size:13px}}
h1{{color:#00e89c;font-size:18px;letter-spacing:3px;margin-bottom:8px}}
h2{{color:#60a5fa;font-size:14px;margin:24px 0 8px;letter-spacing:2px}}
.stats{{display:flex;gap:20px;flex-wrap:wrap;margin:16px 0}}
.stat{{background:#0c0f18;border:1px solid #1e2538;border-radius:8px;padding:16px 20px;min-width:120px}}
.stat-num{{font-size:28px;font-weight:700;color:#00e89c}}
.stat-label{{font-size:10px;color:#5a6378;letter-spacing:1px;margin-top:4px;text-transform:uppercase}}
table{{width:100%;border-collapse:collapse;margin:8px 0}}
th{{text-align:left;padding:8px 10px;border-bottom:2px solid #1e2538;font-size:10px;color:#5a6378;letter-spacing:1px;text-transform:uppercase}}
td{{padding:6px 10px;border-bottom:1px solid #12161f;font-size:12px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
tr:hover td{{background:#0c0f18}}
.refresh{{color:#00e89c;text-decoration:none;font-size:11px;letter-spacing:1px}}
</style></head><body>
<h1>YOUR OS — ADMIN DASHBOARD</h1>
<a class="refresh" href="?token={token}">↻ REFRESH</a>

<div class="stats">
<div class="stat"><div class="stat-num">{total_sessions}</div><div class="stat-label">Total Sessions</div></div>
<div class="stat"><div class="stat-num">{unique_ips}</div><div class="stat-label">Unique Visitors</div></div>
<div class="stat"><div class="stat-num">{total_messages}</div><div class="stat-label">Total Messages</div></div>
<div class="stat"><div class="stat-num">{own_key_users}</div><div class="stat-label">Brought Own Key</div></div>
<div class="stat"><div class="stat-num">{registered_users}</div><div class="stat-label">Registered Users</div></div>
</div>

<h2>SESSIONS (last 50)</h2>
<table>
<tr><th>Name</th><th>OS Name</th><th>Provider</th><th>Messages</th><th>Own Key</th><th>IP</th><th>First Seen</th><th>Last Seen</th></tr>
{sessions_html}
</table>

<h2>MESSAGES (last 100)</h2>
<table>
<tr><th>User</th><th>Role</th><th>Provider</th><th>Content</th><th>NTI</th><th>Type</th><th>Time</th></tr>
{messages_html}
</table>

<div style="margin-top:40px;color:#5a6378;font-size:10px;letter-spacing:1px">ARTIFACT ZERO LABS · YOUR OS ADMIN · {utc_now()[:19]}</div>
</body></html>"""



# ============================================================
# INIT
# ============================================================
os_db_init()
