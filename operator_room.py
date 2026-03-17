"""
operator_room.py
================
Flask blueprint for the Artifact Zero Operator Room.

Routes:
  GET  /operator          — operator room UI (admin only)
  POST /operator/api/chat — Claude API proxy
  GET  /operator/sessions — session history from RDS

Add to app.py:
  from operator_room import operator_bp
  app.register_blueprint(operator_bp)

Environment variables required:
  ANTHROPIC_API_KEY   — Claude API key
  OPERATOR_API_KEY    — NTI enterprise key for operator (set in ECS)
"""

import os, json, time
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, render_template, session
import http.client, ssl

operator_bp = Blueprint('operator', __name__)

ANTHROPIC_KEY  = os.environ.get('ANTHROPIC_API_KEY', '')
OPERATOR_NTI_KEY = os.environ.get('OPERATOR_API_KEY', 'az_21f0f7405b504f38840334b53f0e63ae523fb6a3c50f556c')
CLAUDE_MODEL   = 'claude-sonnet-4-6'


def require_admin(f):
    """Simple admin check — user must be logged in with admin role."""
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        # Check session for admin role
        user_role = session.get('role', '')
        user_id   = session.get('user_id', '')
        if not user_id or user_role not in ('admin', 'operator'):
            # For now: check a simple operator token header
            token = request.headers.get('X-Operator-Token', '')
            env_token = os.environ.get('OPERATOR_TOKEN', 'aztempfix2026')
            if token != env_token:
                return jsonify({'error': 'Unauthorized', 'hint': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return wrapper


@operator_bp.route('/operator')
def operator_room():
    """Serve the operator room UI."""
    # Check admin — redirect to login if not authenticated
    user_role = session.get('role', '')
    user_id   = session.get('user_id', '')

    # Allow if admin or if OPERATOR_TOKEN cookie/header is valid
    is_admin = (user_id and user_role in ('admin', 'operator'))

    if not is_admin:
        # Check for operator access token in cookie
        op_token    = request.cookies.get('op_token', '')
        env_token   = os.environ.get('OPERATOR_TOKEN', 'aztempfix2026')
        if op_token != env_token:
            # Redirect to login
            from flask import redirect
            return redirect('/login?next=/operator')

    return render_template(
        'operator.html',
        api_key=OPERATOR_NTI_KEY,
    )


@operator_bp.route('/operator/api/chat', methods=['POST'])
def operator_chat():
    """
    Proxy to Claude API with operator context.
    Input:  { system, messages, jos }
    Output: Claude API response JSON
    """
    if not ANTHROPIC_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured'}), 500

    payload = request.get_json() or {}
    system   = payload.get('system', '')
    messages = payload.get('messages', [])
    jos      = payload.get('jos', {})

    # Inject JOS state into system if fields are set
    jos_context = []
    if jos.get('objective'):    jos_context.append(f"OBJECTIVE: {jos['objective']}")
    if jos.get('constraint'):   jos_context.append(f"CONSTRAINTS: {jos['constraint']}")
    if jos.get('nogo'):         jos_context.append(f"NO-GO ZONES: {jos['nogo']}")
    if jos.get('done'):         jos_context.append(f"DONE WHEN: {jos['done']}")
    jos_context.append("CLOSURE AUTHORITY: Jame")

    if jos_context:
        system += "\n\nCURRENT JOS:\n" + "\n".join(jos_context)

    # Build Claude API request
    claude_payload = {
        'model':      CLAUDE_MODEL,
        'max_tokens': 4096,
        'system':     system,
        'messages':   messages[-40:],  # last 40 messages for context window
    }

    try:
        body = json.dumps(claude_payload).encode()
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection('api.anthropic.com', 443, context=ctx, timeout=60)
        conn.request('POST', '/v1/messages', body=body, headers={
            'Content-Type':      'application/json',
            'Content-Length':    str(len(body)),
            'x-api-key':         ANTHROPIC_KEY,
            'anthropic-version': '2023-06-01',
            'Connection':        'close',
        })
        r   = conn.getresponse()
        raw = r.read()
        conn.close()

        data = json.loads(raw)

        # Store session to database if available
        try:
            _store_session(messages, data, jos)
        except Exception:
            pass

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@operator_bp.route('/operator/sessions', methods=['GET'])
def operator_sessions():
    """Return recent operator sessions from database."""
    try:
        import database
        conn = database.db_connect()
        cur  = conn.cursor()
        if database.USE_PG:
            cur.execute("""
                SELECT id, created_at, summary
                FROM operator_sessions
                ORDER BY created_at DESC
                LIMIT 20
            """)
            rows = cur.fetchall()
            sessions = [{'id': r[0], 'created_at': str(r[1]), 'summary': r[2]} for r in rows]
        else:
            sessions = []
        conn.close()
        return jsonify({'sessions': sessions})
    except Exception as e:
        return jsonify({'sessions': [], 'note': str(e)})


def _store_session(messages, response, jos):
    """Store operator session to RDS."""
    try:
        import database
        # Create table if not exists
        conn = database.db_connect()
        cur  = conn.cursor()
        if database.USE_PG:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operator_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    messages_json TEXT,
                    response_json TEXT,
                    jos_json TEXT,
                    summary TEXT
                )
            """)
            # Extract summary from last user message
            summary = ''
            for m in reversed(messages):
                if m.get('role') == 'user':
                    summary = m.get('content', '')[:120]
                    break

            import secrets
            sid = 'op_' + secrets.token_hex(8)
            cur.execute("""
                INSERT INTO operator_sessions (id, messages_json, response_json, jos_json, summary)
                VALUES (%s, %s, %s, %s, %s)
            """, (sid, json.dumps(messages), json.dumps(response), json.dumps(jos), summary))
            conn.commit()
        conn.close()
    except Exception:
        pass
