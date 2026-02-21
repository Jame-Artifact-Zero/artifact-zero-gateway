"""
auth.py — Artifact Zero Server-Side Authentication & Identity
=============================================================
Handles: user registration, login, sessions, organizations,
roles, API keys, and audit logging.

All state lives in Postgres. Nothing in the browser except a session token.

Usage:
    from auth import auth_bp, require_auth, require_role
    app.register_blueprint(auth_bp)

    @app.route("/api/something")
    @require_auth
    def something():
        user = request.user  # injected by middleware
        return jsonify({"user_id": user["id"]})

    @app.route("/api/admin-thing")
    @require_role("org_admin")
    def admin_thing():
        ...
"""

import os
import uuid
import hashlib
import secrets
import hmac
import json
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional, Dict, Any

from flask import Blueprint, request, jsonify, g
from db import get_conn, release_conn, db_connection, param_placeholder

auth_bp = Blueprint("auth", __name__)

# ── CONFIG ──
SESSION_DURATION_HOURS = int(os.getenv("SESSION_DURATION_HOURS", "720"))  # 30 days default
BCRYPT_ROUNDS = 12
MIN_PASSWORD_LENGTH = 8


# ==========================
# TABLE INITIALIZATION
# ==========================
def auth_db_init():
    """Create all auth tables. Safe to call multiple times."""
    p = param_placeholder()
    USE_PG = bool(os.getenv("DATABASE_URL"))

    with db_connection() as conn:
        cur = conn.cursor()

        # ── ORGANIZATIONS ──
        # Every enterprise customer is an org.
        # Free users belong to a default "personal" org.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL DEFAULT 'free',
            settings_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        # ── USERS ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            org_id TEXT REFERENCES organizations(id),
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            email_verified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_login_at TEXT
        )
        """)

        # ── SESSIONS ──
        # Server-side session store. Browser gets only the token.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            ip TEXT,
            user_agent TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """)

        # ── API KEYS ──
        # For programmatic access (enterprise integrations, CI/CD)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            key_hash TEXT UNIQUE NOT NULL,
            key_prefix TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(id),
            org_id TEXT NOT NULL REFERENCES organizations(id),
            name TEXT NOT NULL,
            scopes TEXT NOT NULL DEFAULT 'read',
            rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            expires_at TEXT
        )
        """)

        # ── AUDIT LOG ──
        # Every auth event. Required for HIPAA, SOC 2, FedRAMP.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS auth_audit_log (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            user_id TEXT,
            org_id TEXT,
            action TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            details_json TEXT DEFAULT '{}'
        )
        """)

        # ── ORG MEMBERSHIPS ──
        # Users can belong to multiple orgs (consultant model)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS org_memberships (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            org_id TEXT NOT NULL REFERENCES organizations(id),
            role TEXT NOT NULL DEFAULT 'member',
            invited_by TEXT,
            created_at TEXT NOT NULL
        )
        """)

        # ── INDEXES ──
        if USE_PG:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON auth_audit_log(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON auth_audit_log(org_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON auth_audit_log(action)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON auth_audit_log(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_memberships_user ON org_memberships(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_memberships_org ON org_memberships(org_id)")

        conn.commit()
    print("[AUTH] Tables initialized")


# ==========================
# PASSWORD HASHING
# ==========================
def _hash_password(password: str) -> str:
    """SHA-256 + salt. Replace with bcrypt when you add the dependency."""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}:{h.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    salt, hash_hex = stored.split(":", 1)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return hmac.compare_digest(h.hex(), hash_hex)


# ==========================
# AUDIT LOGGING
# ==========================
def _audit(action: str, user_id: str = None, org_id: str = None, details: dict = None):
    p = param_placeholder()
    try:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"""
                INSERT INTO auth_audit_log (id, timestamp, user_id, org_id, action, ip, user_agent, details_json)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
            """, (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                user_id,
                org_id,
                action,
                request.headers.get("X-Forwarded-For", request.remote_addr) if request else None,
                request.headers.get("User-Agent") if request else None,
                json.dumps(details or {})
            ))
            conn.commit()
    except Exception as e:
        print(f"[AUTH AUDIT ERROR] {e}")


# ==========================
# SESSION MANAGEMENT
# ==========================
def _create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_DURATION_HOURS)
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO sessions (token, user_id, ip, user_agent, created_at, expires_at, is_active)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, 1)
        """, (
            token,
            user_id,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent"),
            now.isoformat(),
            expires.isoformat()
        ))
        conn.commit()
    return token


def _validate_session(token: str) -> Optional[Dict]:
    """Returns user dict if session is valid, None otherwise."""
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT u.id, u.email, u.display_name, u.org_id, u.role, u.is_active,
                   s.expires_at, o.name as org_name, o.plan as org_plan, o.slug as org_slug
            FROM sessions s
            JOIN users u ON s.user_id = u.id
            LEFT JOIN organizations o ON u.org_id = o.id
            WHERE s.token = {p} AND s.is_active = 1 AND s.expires_at > {p} AND u.is_active = 1
        """, (token, now))
        row = cur.fetchone()

        if row is None:
            return None

        # Convert row to dict depending on db type
        if hasattr(row, "keys"):
            return dict(row)
        else:
            cols = ["id", "email", "display_name", "org_id", "role", "is_active",
                    "expires_at", "org_name", "org_plan", "org_slug"]
            return dict(zip(cols, row))


def _validate_api_key(key: str) -> Optional[Dict]:
    """Validate an API key and return associated user/org."""
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT ak.id as key_id, ak.scopes, ak.rate_limit_per_min,
                   u.id as user_id, u.email, u.display_name, u.role,
                   o.id as org_id, o.name as org_name, o.plan as org_plan, o.slug as org_slug
            FROM api_keys ak
            JOIN users u ON ak.user_id = u.id
            JOIN organizations o ON ak.org_id = o.id
            WHERE ak.key_hash = {p} AND ak.is_active = 1 AND u.is_active = 1
              AND (ak.expires_at IS NULL OR ak.expires_at > {p})
        """, (key_hash, now))
        row = cur.fetchone()

        if row is None:
            return None

        # Update last_used_at
        if hasattr(row, "keys"):
            key_id = row["key_id"]
        else:
            key_id = row[0]

        cur.execute(f"UPDATE api_keys SET last_used_at = {p} WHERE id = {p}", (now, key_id))
        conn.commit()

        if hasattr(row, "keys"):
            return dict(row)
        cols = ["key_id", "scopes", "rate_limit_per_min",
                "user_id", "email", "display_name", "role",
                "org_id", "org_name", "org_plan", "org_slug"]
        return dict(zip(cols, row))


# ==========================
# AUTH MIDDLEWARE / DECORATORS
# ==========================
def _get_current_user() -> Optional[Dict]:
    """Extract user from session token or API key."""
    # Check Authorization header first (API keys)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer az_"):
        key = auth_header.replace("Bearer ", "")
        user = _validate_api_key(key)
        if user:
            user["_auth_method"] = "api_key"
            return user

    # Check session token (cookie or header)
    token = request.cookies.get("az_session") or request.headers.get("X-Session-Token")
    if token:
        user = _validate_session(token)
        if user:
            user["_auth_method"] = "session"
            return user

    return None


def require_auth(f):
    """Decorator: endpoint requires authenticated user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        request.user = user
        return f(*args, **kwargs)
    return decorated


def require_role(*roles):
    """Decorator: endpoint requires specific role(s)."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = _get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            if user.get("role") not in roles:
                _audit("unauthorized_access", user_id=user["id"],
                       details={"required_roles": list(roles), "user_role": user.get("role")})
                return jsonify({"error": "Insufficient permissions"}), 403
            request.user = user
            return f(*args, **kwargs)
        return decorated
    return decorator


def require_org_role(*roles):
    """Decorator: endpoint requires org-level role. Checks org_memberships table."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = _get_current_user()
            if not user:
                return jsonify({"error": "Authentication required"}), 401

            org_id = request.headers.get("X-Org-Id") or user.get("org_id")
            if not org_id:
                return jsonify({"error": "Organization context required"}), 400

            p = param_placeholder()
            with db_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT role FROM org_memberships
                    WHERE user_id = {p} AND org_id = {p}
                """, (user["id"], org_id))
                row = cur.fetchone()

            if not row:
                return jsonify({"error": "Not a member of this organization"}), 403

            member_role = row["role"] if hasattr(row, "keys") else row[0]
            if member_role not in roles:
                return jsonify({"error": "Insufficient organization permissions"}), 403

            request.user = user
            request.org_role = member_role
            return f(*args, **kwargs)
        return decorated
    return decorator


# ==========================
# ROUTES: REGISTRATION & LOGIN
# ==========================
@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")
    display_name = (payload.get("display_name") or "").strip()

    # Validation
    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400
    if len(password) < MIN_PASSWORD_LENGTH:
        return jsonify({"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    try:
        with db_connection() as conn:
            cur = conn.cursor()

            # Check if email exists
            cur.execute(f"SELECT id FROM users WHERE email = {p}", (email,))
            if cur.fetchone():
                return jsonify({"error": "Email already registered"}), 409

            # Create personal org
            cur.execute(f"""
                INSERT INTO organizations (id, name, slug, plan, created_at, updated_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
            """, (org_id, display_name or email.split("@")[0], f"personal-{user_id[:8]}", "free", now, now))

            # Create user
            cur.execute(f"""
                INSERT INTO users (id, email, password_hash, display_name, org_id, role, created_at, updated_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
            """, (user_id, email, _hash_password(password), display_name or email.split("@")[0],
                  org_id, "user", now, now))

            # Create org membership
            cur.execute(f"""
                INSERT INTO org_memberships (id, user_id, org_id, role, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p})
            """, (str(uuid.uuid4()), user_id, org_id, "owner", now))

            conn.commit()

        # Create session
        token = _create_session(user_id)
        _audit("register", user_id=user_id, org_id=org_id)

        response = jsonify({
            "user_id": user_id,
            "email": email,
            "display_name": display_name,
            "org_id": org_id,
            "token": token
        })
        response.set_cookie("az_session", token,
                           httponly=True, secure=True, samesite="Lax",
                           max_age=SESSION_DURATION_HOURS * 3600)
        return response, 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, email, password_hash, display_name, org_id, role, is_active
            FROM users WHERE email = {p}
        """, (email,))
        row = cur.fetchone()

    if not row:
        _audit("login_failed", details={"email": email, "reason": "not_found"})
        return jsonify({"error": "Invalid email or password"}), 401

    user = dict(row) if hasattr(row, "keys") else dict(zip(
        ["id", "email", "password_hash", "display_name", "org_id", "role", "is_active"], row
    ))

    if not user["is_active"]:
        _audit("login_failed", user_id=user["id"], details={"reason": "disabled"})
        return jsonify({"error": "Account is disabled"}), 403

    if not _verify_password(password, user["password_hash"]):
        _audit("login_failed", user_id=user["id"], details={"reason": "bad_password"})
        return jsonify({"error": "Invalid email or password"}), 401

    # Update last login
    now = datetime.now(timezone.utc).isoformat()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET last_login_at = {p} WHERE id = {p}", (now, user["id"]))
        conn.commit()

    token = _create_session(user["id"])
    _audit("login", user_id=user["id"], org_id=user["org_id"])

    response = jsonify({
        "user_id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "org_id": user["org_id"],
        "role": user["role"],
        "token": token
    })
    response.set_cookie("az_session", token,
                       httponly=True, secure=True, samesite="Lax",
                       max_age=SESSION_DURATION_HOURS * 3600)
    return response


@auth_bp.route("/api/auth/logout", methods=["POST"])
def logout():
    token = request.cookies.get("az_session") or request.headers.get("X-Session-Token")
    if token:
        p = param_placeholder()
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE sessions SET is_active = 0 WHERE token = {p}", (token,))
            conn.commit()
        user = _validate_session(token)  # won't work anymore but try to log
        _audit("logout", user_id=user["id"] if user else None)

    response = jsonify({"ok": True})
    response.delete_cookie("az_session")
    return response


@auth_bp.route("/api/auth/me", methods=["GET"])
@require_auth
def me():
    user = request.user
    return jsonify({
        "user_id": user["id"],
        "email": user["email"],
        "display_name": user["display_name"],
        "org_id": user["org_id"],
        "org_name": user.get("org_name"),
        "org_plan": user.get("org_plan"),
        "role": user["role"]
    })


# ==========================
# ROUTES: ORGANIZATION MANAGEMENT
# ==========================
@auth_bp.route("/api/orgs", methods=["POST"])
@require_auth
def create_org():
    """Create a new organization (enterprise customer onboarding)."""
    user = request.user
    payload = request.get_json() or {}
    name = (payload.get("name") or "").strip()
    slug = (payload.get("slug") or "").strip().lower()
    plan = payload.get("plan", "starter")

    if not name or not slug:
        return jsonify({"error": "name and slug required"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    org_id = str(uuid.uuid4())

    try:
        with db_connection() as conn:
            cur = conn.cursor()

            cur.execute(f"SELECT id FROM organizations WHERE slug = {p}", (slug,))
            if cur.fetchone():
                return jsonify({"error": "Organization slug already taken"}), 409

            cur.execute(f"""
                INSERT INTO organizations (id, name, slug, plan, created_at, updated_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
            """, (org_id, name, slug, plan, now, now))

            # Add creator as org owner
            cur.execute(f"""
                INSERT INTO org_memberships (id, user_id, org_id, role, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p})
            """, (str(uuid.uuid4()), user["id"], org_id, "owner", now))

            conn.commit()

        _audit("org_created", user_id=user["id"], org_id=org_id, details={"name": name, "plan": plan})
        return jsonify({"org_id": org_id, "name": name, "slug": slug, "plan": plan}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@auth_bp.route("/api/orgs/<org_id>/invite", methods=["POST"])
@require_org_role("owner", "admin")
def invite_user(org_id):
    """Invite a user to an organization."""
    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    role = payload.get("role", "member")

    if not email:
        return jsonify({"error": "email required"}), 400
    if role not in ["member", "admin", "viewer"]:
        return jsonify({"error": "role must be member, admin, or viewer"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()

        # Find user by email
        cur.execute(f"SELECT id FROM users WHERE email = {p}", (email,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "User not found. They must register first."}), 404

        target_user_id = row["id"] if hasattr(row, "keys") else row[0]

        # Check if already a member
        cur.execute(f"""
            SELECT id FROM org_memberships WHERE user_id = {p} AND org_id = {p}
        """, (target_user_id, org_id))
        if cur.fetchone():
            return jsonify({"error": "User is already a member of this organization"}), 409

        cur.execute(f"""
            INSERT INTO org_memberships (id, user_id, org_id, role, invited_by, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})
        """, (str(uuid.uuid4()), target_user_id, org_id, role, request.user["id"], now))
        conn.commit()

    _audit("user_invited", user_id=request.user["id"], org_id=org_id,
           details={"invited_email": email, "role": role})
    return jsonify({"ok": True, "invited": email, "role": role}), 201


@auth_bp.route("/api/orgs/<org_id>/members", methods=["GET"])
@require_org_role("owner", "admin")
def list_members(org_id):
    """List all members of an organization."""
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT u.id, u.email, u.display_name, u.last_login_at, m.role, m.created_at as joined_at
            FROM org_memberships m
            JOIN users u ON m.user_id = u.id
            WHERE m.org_id = {p}
            ORDER BY m.created_at
        """, (org_id,))
        rows = cur.fetchall()

    members = []
    for r in rows:
        if hasattr(r, "keys"):
            members.append(dict(r))
        else:
            members.append(dict(zip(["id", "email", "display_name", "last_login_at", "role", "joined_at"], r)))

    return jsonify({"org_id": org_id, "members": members, "count": len(members)})


# ==========================
# ROUTES: API KEY MANAGEMENT
# ==========================
@auth_bp.route("/api/keys", methods=["POST"])
@require_auth
def create_api_key():
    """Generate an API key for programmatic access."""
    user = request.user
    payload = request.get_json() or {}
    name = (payload.get("name") or "").strip()
    scopes = payload.get("scopes", "read")
    expires_days = payload.get("expires_days")  # None = no expiry

    if not name:
        return jsonify({"error": "name required"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    key_id = str(uuid.uuid4())
    raw_key = f"az_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:10]

    expires_at = None
    if expires_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=int(expires_days))).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO api_keys (id, key_hash, key_prefix, user_id, org_id, name, scopes, created_at, expires_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (key_id, key_hash, key_prefix, user["id"], user["org_id"], name, scopes, now, expires_at))
        conn.commit()

    _audit("api_key_created", user_id=user["id"], org_id=user["org_id"],
           details={"key_id": key_id, "name": name, "scopes": scopes})

    return jsonify({
        "key_id": key_id,
        "key": raw_key,  # Only shown ONCE
        "prefix": key_prefix,
        "name": name,
        "scopes": scopes,
        "expires_at": expires_at,
        "warning": "Save this key now. It will not be shown again."
    }), 201


# ==========================
# ROUTES: AUDIT LOG
# ==========================
@auth_bp.route("/api/audit", methods=["GET"])
@require_role("admin", "org_admin")
def get_audit_log():
    """Query the auth audit log. Required for compliance."""
    org_id = request.args.get("org_id") or request.user.get("org_id")
    action = request.args.get("action")
    limit = min(int(request.args.get("limit", 100)), 500)

    p = param_placeholder()
    conditions = []
    params = []

    if org_id:
        conditions.append(f"org_id = {p}")
        params.append(org_id)
    if action:
        conditions.append(f"action = {p}")
        params.append(action)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, timestamp, user_id, org_id, action, ip, details_json
            FROM auth_audit_log
            {where}
            ORDER BY timestamp DESC
            LIMIT {limit}
        """, tuple(params))
        rows = cur.fetchall()

    entries = []
    for r in rows:
        if hasattr(r, "keys"):
            entries.append(dict(r))
        else:
            entries.append(dict(zip(["id", "timestamp", "user_id", "org_id", "action", "ip", "details_json"], r)))

    return jsonify({"entries": entries, "count": len(entries)})
