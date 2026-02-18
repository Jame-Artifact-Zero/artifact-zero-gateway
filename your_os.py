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
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify, render_template, session

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
YOUR_OS_DB = os.getenv("YOUR_OS_DB", "/tmp/your_os.db")
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
    conn = sqlite3.connect(YOUR_OS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def db_commit_timed(conn):
    """Commit with latency monitoring. Logs warning if slow."""
    t0 = time.time()
    db_commit_timed(conn)
    ms = int((time.time() - t0) * 1000)
    if ms > DB_WRITE_WARN_MS:
        print(f"[WARN] your_os db commit took {ms}ms — consider Postgres migration")


def os_db_init():
    conn = os_db()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS os_users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        display_name TEXT,
        os_name TEXT DEFAULT '_OS',
        tier TEXT DEFAULT 'free',
        stripe_customer_id TEXT,
        created_at TEXT NOT NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS os_protocols (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        os_name TEXT NOT NULL,
        objective TEXT,
        constraints TEXT,
        no_go_zones TEXT,
        definition_of_done TEXT,
        closure_authority TEXT DEFAULT 'human',
        version INTEGER DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES os_users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS os_api_keys (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        provider TEXT NOT NULL,
        encrypted_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES os_users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS os_conversations (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT,
        task_number TEXT,
        keywords TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES os_users(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS os_messages (
        id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        group_id TEXT NOT NULL,
        role TEXT NOT NULL,
        provider TEXT,
        content TEXT NOT NULL,
        nti_score REAL,
        constraints_followed INTEGER,
        constraints_total INTEGER,
        chosen INTEGER DEFAULT 0,
        tokens_in INTEGER,
        tokens_out INTEGER,
        latency_ms INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY (conversation_id) REFERENCES os_conversations(id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS os_tasks (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        conversation_id TEXT,
        task_number TEXT NOT NULL,
        title TEXT,
        keywords TEXT,
        status TEXT DEFAULT 'open',
        created_at TEXT NOT NULL,
        closed_at TEXT,
        FOREIGN KEY (user_id) REFERENCES os_users(id)
    )""")

    # Trial tracking — every visitor interaction
    c.execute("""CREATE TABLE IF NOT EXISTS os_trial_sessions (
        id TEXT PRIMARY KEY,
        ip TEXT,
        user_agent TEXT,
        user_name TEXT,
        os_name TEXT,
        path TEXT,
        provider TEXT,
        message_count INTEGER DEFAULT 0,
        has_own_key INTEGER DEFAULT 0,
        protocol_json TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS os_trial_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT,
        role TEXT NOT NULL,
        provider TEXT,
        content TEXT,
        nti_score REAL,
        is_trial INTEGER DEFAULT 1,
        latency_ms INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES os_trial_sessions(id)
    )""")

    db_commit_timed(conn)
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
    """Get user ID from session or auth header."""
    # Session-based
    uid = session.get('your_os_user_id')
    if uid:
        return uid
    # Token-based (for API access)
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token = auth[7:]
        conn = os_db()
        row = conn.execute("SELECT id FROM os_users WHERE id = ?", (token,)).fetchone()
        conn.close()
        if row:
            return row['id']
    return None


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
    """
    Free trial endpoint — uses house API key.
    No login required. Session-based rate limiting.
    5 messages max per session.
    """
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    protocol = data.get('protocol', {})
    provider = data.get('provider', HOUSE_PROVIDER)

    if not message:
        return jsonify({'error': 'Message required'}), 400

    # Session-based trial counter
    trial_count = session.get('your_os_trial_count', 0)
    if trial_count >= FREE_TRIAL_LIMIT:
        return jsonify({
            'error': 'trial_exhausted',
            'message': f'Free trial complete ({FREE_TRIAL_LIMIT} messages). Connect your own API key to continue.',
            'trial_count': trial_count,
            'trial_limit': FREE_TRIAL_LIMIT
        }), 429

    # Determine which key to use
    user_key = data.get('api_key', '').strip()

    if user_key:
        # User provided their own key — no trial decrement
        api_key = user_key
        is_trial = False
    else:
        # Use house key
        if not HOUSE_API_KEY:
            return jsonify({
                'error': 'no_house_key',
                'message': 'Trial not available. Connect your own API key.'
            }), 503
        api_key = HOUSE_API_KEY
        provider = HOUSE_PROVIDER
        is_trial = True

    # Build system prompt from protocol
    system_prompt = build_system_prompt(protocol)

    # Build messages (include conversation history if provided)
    history = data.get('history', [])
    messages_for_api = []
    for h in history[-10:]:  # last 10 messages max for context
        messages_for_api.append({'role': h.get('role', 'user'), 'content': h.get('content', '')})
    messages_for_api.append({'role': 'user', 'content': message})

    # Call the provider
    caller = PROVIDER_CALLERS.get(provider)
    if caller and HAS_REQUESTS:
        result = caller(api_key, system_prompt, messages_for_api)
    else:
        return jsonify({
            'error': 'network_unavailable',
            'message': 'API calls not available on this server.'
        }), 503

    if result.get('error'):
        return jsonify({
            'error': 'api_error',
            'message': result['error'],
            'provider': provider
        }), 502

    # NTI score the response
    nti = quick_nti_score(result.get('content', ''), protocol)

    # Increment trial count
    if is_trial:
        session['your_os_trial_count'] = trial_count + 1

    # --- TRACK THIS INTERACTION ---
    try:
        conn = os_db()
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        ua = request.headers.get('User-Agent', '')[:200]
        sess_id = session.get('your_os_session_id')
        now = utc_now()

        if not sess_id:
            sess_id = str(uuid.uuid4())
            session['your_os_session_id'] = sess_id
            # New session
            conn.execute("""
                INSERT OR IGNORE INTO os_trial_sessions
                (id, ip, user_agent, user_name, os_name, path, provider, message_count, has_own_key, protocol_json, first_seen, last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (sess_id, ip, ua,
                  protocol.get('user_name', ''),
                  protocol.get('os_name', ''),
                  data.get('path', 'unknown'),
                  provider, 0,
                  1 if user_key else 0,
                  json.dumps(protocol),
                  now, now))

        # Update session
        conn.execute("""
            UPDATE os_trial_sessions
            SET message_count = message_count + 1, last_seen = ?, provider = ?, has_own_key = ?
            WHERE id = ?
        """, (now, provider, 1 if user_key else 0, sess_id))

        # Log the message
        conn.execute("""
            INSERT INTO os_trial_messages (id, session_id, role, provider, content, nti_score, is_trial, latency_ms, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), sess_id, 'user', None, message, None, 1 if is_trial else 0, 0, now))

        # Log the response
        conn.execute("""
            INSERT INTO os_trial_messages (id, session_id, role, provider, content, nti_score, is_trial, latency_ms, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), sess_id, 'assistant', provider,
              result.get('content', '')[:500],  # truncate for storage
              nti['score'], 1 if is_trial else 0,
              result.get('latency_ms', 0), now))

        db_commit_timed(conn)
        conn.close()
    except Exception as e:
        print(f"[WARN] trial tracking error: {e}")

    return jsonify({
        'ok': True,
        'provider': provider,
        'is_trial': is_trial,
        'trial_count': session.get('your_os_trial_count', 0),
        'trial_limit': FREE_TRIAL_LIMIT,
        'content': result.get('content', ''),
        'nti_score': nti['score'],
        'constraints_followed': nti['followed'],
        'constraints_total': nti['total'],
        'tokens_in': result.get('tokens_in', 0),
        'tokens_out': result.get('tokens_out', 0),
        'latency_ms': result.get('latency_ms', 0)
    })


@your_os.route('/api/os/signup', methods=['POST'])
def os_signup():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name = data.get('name', '')

    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be 6+ characters'}), 400

    user_id = str(uuid.uuid4())
    os_name = (name[0].upper() + 'OS') if name else '_OS'

    conn = os_db()
    try:
        conn.execute(
            "INSERT INTO os_users (id, email, password_hash, display_name, os_name, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, email, hash_password(password), name, os_name, utc_now())
        )
        # Create default protocol
        conn.execute(
            "INSERT INTO os_protocols (id, user_id, os_name, objective, constraints, no_go_zones, definition_of_done, closure_authority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, os_name, '', '', '', '', 'human', utc_now(), utc_now())
        )
        db_commit_timed(conn)
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Email already registered'}), 409
    conn.close()

    session['your_os_user_id'] = user_id
    return jsonify({'ok': True, 'user_id': user_id, 'os_name': os_name})


@your_os.route('/api/os/login', methods=['POST'])
def os_login():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    conn = os_db()
    row = conn.execute("SELECT id, password_hash, os_name FROM os_users WHERE email = ?", (email,)).fetchone()
    conn.close()

    if not row or not verify_password(password, row['password_hash']):
        return jsonify({'error': 'Invalid email or password'}), 401

    session['your_os_user_id'] = row['id']
    return jsonify({'ok': True, 'user_id': row['id'], 'os_name': row['os_name']})


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
    row = conn.execute(
        "SELECT * FROM os_protocols WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'No protocol found'}), 404
    return jsonify(dict(row))


@your_os.route('/api/os/protocol', methods=['POST'])
@require_auth
def os_save_protocol(user_id):
    data = request.get_json() or {}
    conn = os_db()

    # Get existing
    existing = conn.execute(
        "SELECT id, version FROM os_protocols WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()

    now = utc_now()
    if existing:
        conn.execute("""
            UPDATE os_protocols SET os_name=?, objective=?, constraints=?, no_go_zones=?,
            definition_of_done=?, closure_authority=?, version=?, updated_at=? WHERE id=?
        """, (
            data.get('os_name', '_OS'), data.get('objective', ''),
            data.get('constraints', ''), data.get('no_go_zones', ''),
            data.get('definition_of_done', ''), data.get('closure_authority', 'human'),
            (existing['version'] or 0) + 1, now, existing['id']
        ))
    else:
        conn.execute(
            "INSERT INTO os_protocols (id, user_id, os_name, objective, constraints, no_go_zones, definition_of_done, closure_authority, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, data.get('os_name', '_OS'),
             data.get('objective', ''), data.get('constraints', ''),
             data.get('no_go_zones', ''), data.get('definition_of_done', ''),
             data.get('closure_authority', 'human'), now, now)
        )

    # Update user's OS name
    conn.execute("UPDATE os_users SET os_name = ? WHERE id = ?", (data.get('os_name', '_OS'), user_id))
    db_commit_timed(conn)
    conn.close()
    return jsonify({'ok': True, 'version': (existing['version'] or 0) + 1 if existing else 1})


# ============================================================
# ROUTES: API KEYS
# ============================================================
@your_os.route('/api/os/keys', methods=['POST'])
@require_auth
def os_save_keys(user_id):
    data = request.get_json() or {}
    conn = os_db()
    now = utc_now()

    for provider in ['openai', 'anthropic', 'google']:
        key_val = data.get(provider, '').strip()
        if key_val:
            # Upsert
            existing = conn.execute(
                "SELECT id FROM os_api_keys WHERE user_id = ? AND provider = ?",
                (user_id, provider)
            ).fetchone()
            encrypted = encrypt_key(key_val)
            if existing:
                conn.execute("UPDATE os_api_keys SET encrypted_key = ? WHERE id = ?",
                             (encrypted, existing['id']))
            else:
                conn.execute(
                    "INSERT INTO os_api_keys (id, user_id, provider, encrypted_key, created_at) VALUES (?,?,?,?,?)",
                    (str(uuid.uuid4()), user_id, provider, encrypted, now)
                )

    db_commit_timed(conn)
    conn.close()
    return jsonify({'ok': True})


@your_os.route('/api/os/keys', methods=['GET'])
@require_auth
def os_get_keys(user_id):
    conn = os_db()
    rows = conn.execute(
        "SELECT provider, encrypted_key FROM os_api_keys WHERE user_id = ?", (user_id,)
    ).fetchall()
    conn.close()

    # Return which providers are connected (not the actual keys)
    connected = {}
    for row in rows:
        key = decrypt_key(row['encrypted_key'])
        connected[row['provider']] = bool(key and len(key) > 5)

    return jsonify({'connected': connected})


# ============================================================
# ROUTES: CHAT (THE CORE)
# ============================================================
@your_os.route('/api/os/chat', methods=['POST'])
@require_auth
def os_chat(user_id):
    """
    Main chat endpoint.
    Accepts: message, conversation_id (optional), providers (list)
    Fans out to requested providers with protocol injected.
    Returns: responses keyed by provider with NTI scores.
    """
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    conv_id = data.get('conversation_id')
    providers = data.get('providers', ['openai', 'anthropic', 'google'])

    if not message:
        return jsonify({'error': 'Message required'}), 400

    conn = os_db()
    now = utc_now()

    # Get protocol
    proto_row = conn.execute(
        "SELECT * FROM os_protocols WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    protocol = dict(proto_row) if proto_row else {}
    system_prompt = build_system_prompt(protocol)

    # Get or create conversation
    if conv_id:
        conv = conn.execute("SELECT * FROM os_conversations WHERE id = ? AND user_id = ?",
                            (conv_id, user_id)).fetchone()
    else:
        conv = None

    if not conv:
        # Count existing conversations for task number
        count = conn.execute(
            "SELECT COUNT(*) as cnt FROM os_conversations WHERE user_id = ?", (user_id,)
        ).fetchone()['cnt']
        conv_id = str(uuid.uuid4())
        task_num = f"T-{count + 1}"
        title = message[:60] + ('...' if len(message) > 60 else '')
        keywords = ' '.join(set(message.lower().split()[:10]))

        conn.execute("""
            INSERT INTO os_conversations (id, user_id, title, task_number, keywords, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (conv_id, user_id, title, task_num, keywords, 'active', now, now))

        # Create task registry entry
        conn.execute("""
            INSERT INTO os_tasks (id, user_id, conversation_id, task_number, title, keywords, status, created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), user_id, conv_id, task_num, title, keywords, 'open', now))

    # Save user message
    group_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO os_messages (id, conversation_id, group_id, role, content, created_at)
        VALUES (?,?,?,?,?,?)
    """, (str(uuid.uuid4()), conv_id, group_id, 'user', message, now))

    # Get conversation history for context
    history_rows = conn.execute("""
        SELECT role, provider, content, chosen FROM os_messages
        WHERE conversation_id = ? AND id != ? ORDER BY created_at
    """, (conv_id, group_id)).fetchall()

    # Build message history (use chosen responses, or first available)
    messages_for_api = []
    current_group = None
    for row in history_rows:
        if row['role'] == 'user':
            messages_for_api.append({'role': 'user', 'content': row['content']})
            current_group = True
        elif row['role'] == 'assistant' and (row['chosen'] or current_group):
            messages_for_api.append({'role': 'assistant', 'content': row['content']})
            current_group = False

    # Add current message
    messages_for_api.append({'role': 'user', 'content': message})

    db_commit_timed(conn)

    # Fan out to providers
    responses = {}
    for provider in providers:
        # Get API key
        key_row = conn.execute(
            "SELECT encrypted_key FROM os_api_keys WHERE user_id = ? AND provider = ?",
            (user_id, provider)
        ).fetchone()

        if not key_row:
            responses[provider] = {
                'content': f'No API key configured for {provider}. Add your key in Settings.',
                'error': 'no_key', 'nti_score': 0, 'followed': 0, 'total': 0,
                'tokens_in': 0, 'tokens_out': 0, 'latency_ms': 0
            }
            continue

        api_key = decrypt_key(key_row['encrypted_key'])
        if not api_key:
            responses[provider] = {
                'content': f'Failed to decrypt {provider} API key.',
                'error': 'decrypt_failed', 'nti_score': 0, 'followed': 0, 'total': 0,
                'tokens_in': 0, 'tokens_out': 0, 'latency_ms': 0
            }
            continue

        # Call the provider
        caller = PROVIDER_CALLERS.get(provider)
        if caller and HAS_REQUESTS:
            result = caller(api_key, system_prompt, messages_for_api)
        else:
            # Fallback: simulated response
            result = {
                'content': f'[Simulated {provider} response — requests library not available or network disabled]',
                'tokens_in': len(message.split()) * 2,
                'tokens_out': 50,
                'latency_ms': 100,
                'error': None
            }

        # NTI score the response
        nti = quick_nti_score(result.get('content', ''), protocol)

        # Save response
        conn.execute("""
            INSERT INTO os_messages (id, conversation_id, group_id, role, provider, content,
            nti_score, constraints_followed, constraints_total, tokens_in, tokens_out, latency_ms, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (str(uuid.uuid4()), conv_id, group_id, 'assistant', provider,
              result.get('content', ''), nti['score'], nti['followed'], nti['total'],
              result.get('tokens_in', 0), result.get('tokens_out', 0),
              result.get('latency_ms', 0), utc_now()))

        responses[provider] = {
            'content': result.get('content', ''),
            'error': result.get('error'),
            'nti_score': nti['score'],
            'followed': nti['followed'],
            'total': nti['total'],
            'tokens_in': result.get('tokens_in', 0),
            'tokens_out': result.get('tokens_out', 0),
            'latency_ms': result.get('latency_ms', 0)
        }

    # Update conversation
    conn.execute("UPDATE os_conversations SET updated_at = ? WHERE id = ?", (utc_now(), conv_id))
    db_commit_timed(conn)
    conn.close()

    return jsonify({
        'ok': True,
        'conversation_id': conv_id,
        'group_id': group_id,
        'responses': responses
    })


@your_os.route('/api/os/chat/choose', methods=['POST'])
@require_auth
def os_choose_response(user_id):
    """Mark a response as chosen for thread continuation."""
    data = request.get_json() or {}
    group_id = data.get('group_id')
    provider = data.get('provider')

    if not group_id or not provider:
        return jsonify({'error': 'group_id and provider required'}), 400

    conn = os_db()
    conn.execute("UPDATE os_messages SET chosen = 0 WHERE group_id = ? AND role = 'assistant'", (group_id,))
    conn.execute("UPDATE os_messages SET chosen = 1 WHERE group_id = ? AND provider = ?", (group_id, provider))
    db_commit_timed(conn)
    conn.close()

    return jsonify({'ok': True})


# ============================================================
# ROUTES: CONVERSATIONS & SEARCH
# ============================================================
@your_os.route('/api/os/conversations', methods=['GET'])
@require_auth
def os_list_conversations(user_id):
    conn = os_db()
    rows = conn.execute("""
        SELECT id, title, task_number, keywords, status, created_at, updated_at
        FROM os_conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 50
    """, (user_id,)).fetchall()
    conn.close()
    return jsonify({'conversations': [dict(r) for r in rows]})


@your_os.route('/api/os/conversations/<conv_id>', methods=['GET'])
@require_auth
def os_get_conversation(user_id, conv_id):
    conn = os_db()
    conv = conn.execute(
        "SELECT * FROM os_conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
    ).fetchone()
    if not conv:
        conn.close()
        return jsonify({'error': 'Not found'}), 404

    messages = conn.execute("""
        SELECT * FROM os_messages WHERE conversation_id = ? ORDER BY created_at
    """, (conv_id,)).fetchall()
    conn.close()

    return jsonify({
        'conversation': dict(conv),
        'messages': [dict(m) for m in messages]
    })


@your_os.route('/api/os/search', methods=['GET'])
@require_auth
def os_search(user_id):
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'results': []})

    conn = os_db()
    q_like = f'%{q}%'

    # Search conversations
    convs = conn.execute("""
        SELECT id, title, task_number, keywords, status, updated_at
        FROM os_conversations WHERE user_id = ? AND (
            title LIKE ? OR task_number LIKE ? OR keywords LIKE ?
        ) ORDER BY updated_at DESC LIMIT 20
    """, (user_id, q_like, q_like, q_like)).fetchall()

    # Search messages
    msgs = conn.execute("""
        SELECT m.id, m.conversation_id, m.role, m.provider, m.content, m.created_at,
               c.title as conv_title, c.task_number
        FROM os_messages m JOIN os_conversations c ON m.conversation_id = c.id
        WHERE c.user_id = ? AND m.content LIKE ?
        ORDER BY m.created_at DESC LIMIT 20
    """, (user_id, q_like)).fetchall()

    conn.close()

    return jsonify({
        'conversations': [dict(r) for r in convs],
        'messages': [dict(r) for r in msgs]
    })


# ============================================================
# ROUTES: TASK REGISTRY
# ============================================================
@your_os.route('/api/os/tasks', methods=['GET'])
@require_auth
def os_list_tasks(user_id):
    conn = os_db()
    rows = conn.execute("""
        SELECT * FROM os_tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT 50
    """, (user_id,)).fetchall()
    conn.close()
    return jsonify({'tasks': [dict(r) for r in rows]})


@your_os.route('/api/os/tasks/<task_id>/close', methods=['POST'])
@require_auth
def os_close_task(user_id, task_id):
    conn = os_db()
    conn.execute("UPDATE os_tasks SET status = 'closed', closed_at = ? WHERE id = ? AND user_id = ?",
                 (utc_now(), task_id, user_id))
    db_commit_timed(conn)
    conn.close()
    return jsonify({'ok': True})


# ============================================================
# ADMIN DASHBOARD (token-protected)
# ============================================================
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


@your_os.route('/your-os/admin')
def os_admin_page():
    """Admin dashboard — requires ADMIN_TOKEN as query param."""
    token = request.args.get('token', '')
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return "Unauthorized", 401

    conn = os_db()

    # Summary stats
    total_sessions = conn.execute("SELECT COUNT(*) as cnt FROM os_trial_sessions").fetchone()['cnt']
    total_messages = conn.execute("SELECT COUNT(*) as cnt FROM os_trial_messages").fetchone()['cnt']
    unique_ips = conn.execute("SELECT COUNT(DISTINCT ip) as cnt FROM os_trial_sessions").fetchone()['cnt']
    own_key_users = conn.execute("SELECT COUNT(*) as cnt FROM os_trial_sessions WHERE has_own_key = 1").fetchone()['cnt']
    registered_users = conn.execute("SELECT COUNT(*) as cnt FROM os_users").fetchone()['cnt']

    # Recent sessions
    sessions = conn.execute("""
        SELECT s.*, 
               (SELECT COUNT(*) FROM os_trial_messages WHERE session_id = s.id AND role = 'user') as msg_count
        FROM os_trial_sessions s
        ORDER BY s.last_seen DESC
        LIMIT 50
    """).fetchall()

    # Recent messages (last 100)
    messages = conn.execute("""
        SELECT m.*, s.user_name, s.os_name, s.ip
        FROM os_trial_messages m
        LEFT JOIN os_trial_sessions s ON m.session_id = s.id
        ORDER BY m.created_at DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    # Build HTML
    sessions_html = ''
    for s in sessions:
        sessions_html += f"""<tr>
            <td>{s['user_name'] or '—'}</td>
            <td>{s['os_name'] or '—'}</td>
            <td>{s['provider'] or '—'}</td>
            <td>{s['message_count']}</td>
            <td>{'✓' if s['has_own_key'] else '—'}</td>
            <td>{s['ip'] or '—'}</td>
            <td>{s['first_seen'][:19] if s['first_seen'] else '—'}</td>
            <td>{s['last_seen'][:19] if s['last_seen'] else '—'}</td>
        </tr>"""

    messages_html = ''
    for m in messages:
        content_preview = (m['content'] or '')[:120].replace('<', '&lt;')
        messages_html += f"""<tr>
            <td>{m['user_name'] or '—'}</td>
            <td>{m['role']}</td>
            <td>{m['provider'] or '—'}</td>
            <td title="{(m['content'] or '').replace('"', '&quot;')[:500]}">{content_preview}{'...' if len(m['content'] or '') > 120 else ''}</td>
            <td>{m['nti_score'] if m['nti_score'] else '—'}</td>
            <td>{'trial' if m['is_trial'] else 'own key'}</td>
            <td>{m['created_at'][:19] if m['created_at'] else '—'}</td>
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
