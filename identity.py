"""
identity.py — Enterprise Identity Provider Integration
=======================================================
Covers: SSO, SAML, OAuth2, OIDC, JWT, MFA/2FA, TOTP, passkeys, ABAC

Enterprise flow:
  1. Org admin configures identity provider (Okta, Azure AD, Google Workspace)
  2. User clicks "Sign in with SSO" → redirected to IdP
  3. IdP authenticates → redirects back with SAML assertion or OAuth2 code
  4. We validate, create/match user, issue JWT session
  5. Optional: MFA challenge via TOTP (authenticator app)

Usage:
    from identity import identity_bp
    app.register_blueprint(identity_bp)
"""

import os
import uuid
import json
import time
import hmac
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urlencode

import jwt as pyjwt
import pyotp
from flask import Blueprint, request, jsonify, redirect, url_for, current_app
from authlib.integrations.flask_client import OAuth

from db import db_connection, param_placeholder

identity_bp = Blueprint("identity", __name__)

# ── JWT CONFIG ──
JWT_SECRET = os.getenv("JWT_SECRET", os.getenv("FLASK_SECRET_KEY", "change-me"))
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TTL = int(os.getenv("JWT_ACCESS_TTL_MINUTES", "60"))
JWT_REFRESH_TTL = int(os.getenv("JWT_REFRESH_TTL_DAYS", "30"))

# ── OAuth2 / OIDC registry ──
oauth = OAuth()


def identity_db_init():
    """Create identity-specific tables."""
    p = param_placeholder()
    USE_PG = bool(os.getenv("DATABASE_URL"))

    with db_connection() as conn:
        cur = conn.cursor()

        # ── SSO / SAML / OIDC provider config per org ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS identity_providers (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            provider_type TEXT NOT NULL,
            name TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        # ── MFA / TOTP secrets per user ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mfa_secrets (
            user_id TEXT PRIMARY KEY,
            totp_secret TEXT NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 0,
            backup_codes_json TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            verified_at TEXT
        )
        """)

        # ── JWT refresh tokens ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            device_info TEXT,
            ip TEXT,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        )
        """)

        # ── Passkey / WebAuthn credentials ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS passkeys (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            credential_id TEXT UNIQUE NOT NULL,
            public_key TEXT NOT NULL,
            sign_count INTEGER NOT NULL DEFAULT 0,
            name TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """)

        # ── ABAC: attribute-based access control policies ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS abac_policies (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            name TEXT NOT NULL,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            conditions_json TEXT NOT NULL DEFAULT '{}',
            effect TEXT NOT NULL DEFAULT 'allow',
            priority INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """)

        if USE_PG:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_idp_org ON identity_providers(org_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_passkeys_user ON passkeys(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_abac_org ON abac_policies(org_id)")

        conn.commit()
    print("[IDENTITY] Tables initialized")


# ══════════════════════════════════════════════
# JWT TOKEN MANAGEMENT
# ══════════════════════════════════════════════
def create_access_token(user_id: str, email: str, org_id: str, role: str, scopes: list = None) -> str:
    """Create a short-lived JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "org_id": org_id,
        "role": role,
        "scopes": scopes or ["read", "write"],
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=JWT_ACCESS_TTL),
        "jti": str(uuid.uuid4()),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token, stored server-side."""
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=JWT_REFRESH_TTL)
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO refresh_tokens (id, user_id, token_hash, ip, created_at, expires_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})
        """, (
            str(uuid.uuid4()), user_id, token_hash,
            request.headers.get("X-Forwarded-For", request.remote_addr) if request else None,
            now.isoformat(), expires.isoformat()
        ))
        conn.commit()
    return raw


def validate_access_token(token: str) -> dict:
    """Decode and validate a JWT access token."""
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except pyjwt.ExpiredSignatureError:
        return None
    except pyjwt.InvalidTokenError:
        return None


def rotate_refresh_token(old_token: str) -> tuple:
    """Validate refresh token, revoke it, issue new pair. Returns (access, refresh) or (None, None)."""
    old_hash = hashlib.sha256(old_token.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, user_id, expires_at, revoked_at FROM refresh_tokens
            WHERE token_hash = {p}
        """, (old_hash,))
        row = cur.fetchone()

        if not row:
            return None, None

        rt = dict(row) if hasattr(row, "keys") else dict(zip(["id", "user_id", "expires_at", "revoked_at"], row))

        if rt["revoked_at"] or rt["expires_at"] < now:
            return None, None

        # Revoke old token
        cur.execute(f"UPDATE refresh_tokens SET revoked_at = {p} WHERE id = {p}", (now, rt["id"]))

        # Get user info for new access token
        cur.execute(f"SELECT email, org_id, role FROM users WHERE id = {p}", (rt["user_id"],))
        user_row = cur.fetchone()
        conn.commit()

    if not user_row:
        return None, None

    u = dict(user_row) if hasattr(user_row, "keys") else dict(zip(["email", "org_id", "role"], user_row))
    access = create_access_token(rt["user_id"], u["email"], u["org_id"], u["role"])
    refresh = create_refresh_token(rt["user_id"])
    return access, refresh


# ══════════════════════════════════════════════
# MFA / 2FA / TOTP
# ══════════════════════════════════════════════
def mfa_setup(user_id: str, email: str) -> dict:
    """Generate TOTP secret and backup codes for MFA enrollment."""
    secret = pyotp.random_base32()
    backup_codes = [secrets.token_hex(4) for _ in range(10)]
    now = datetime.now(timezone.utc).isoformat()
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()
        # Upsert
        if os.getenv("DATABASE_URL"):
            cur.execute(f"""
                INSERT INTO mfa_secrets (user_id, totp_secret, backup_codes_json, created_at)
                VALUES ({p}, {p}, {p}, {p})
                ON CONFLICT (user_id) DO UPDATE SET totp_secret = EXCLUDED.totp_secret,
                    backup_codes_json = EXCLUDED.backup_codes_json, created_at = EXCLUDED.created_at
            """, (user_id, secret, json.dumps(backup_codes), now))
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO mfa_secrets (user_id, totp_secret, backup_codes_json, created_at)
                VALUES ({p}, {p}, {p}, {p})
            """, (user_id, secret, json.dumps(backup_codes), now))
        conn.commit()

    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(name=email, issuer_name="Artifact Zero")

    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "backup_codes": backup_codes,
    }


def mfa_verify(user_id: str, code: str) -> bool:
    """Verify a TOTP code or backup code."""
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT totp_secret, backup_codes_json FROM mfa_secrets WHERE user_id = {p}", (user_id,))
        row = cur.fetchone()

    if not row:
        return False

    data = dict(row) if hasattr(row, "keys") else dict(zip(["totp_secret", "backup_codes_json"], row))
    totp = pyotp.TOTP(data["totp_secret"])

    # Check TOTP first
    if totp.verify(code, valid_window=1):
        return True

    # Check backup codes
    backup_codes = json.loads(data["backup_codes_json"] or "[]")
    if code in backup_codes:
        backup_codes.remove(code)
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE mfa_secrets SET backup_codes_json = {p} WHERE user_id = {p}",
                       (json.dumps(backup_codes), user_id))
            conn.commit()
        return True

    return False


def mfa_enable(user_id: str):
    """Mark MFA as verified and enabled."""
    now = datetime.now(timezone.utc).isoformat()
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE mfa_secrets SET is_enabled = 1, verified_at = {p} WHERE user_id = {p}", (now, user_id))
        conn.commit()


# ══════════════════════════════════════════════
# SSO / SAML / OAuth2 / OIDC ROUTES
# ══════════════════════════════════════════════
@identity_bp.route("/api/v1/auth/sso/configure", methods=["POST"])
def configure_sso():
    """Configure an SSO/SAML/OIDC provider for an organization."""
    from auth import require_org_role
    payload = request.get_json() or {}
    org_id = payload.get("org_id")
    provider_type = payload.get("provider_type")  # saml, oidc, oauth2
    name = payload.get("name", "")  # "Okta", "Azure AD", "Google Workspace"
    config = payload.get("config", {})

    if provider_type not in ("saml", "oidc", "oauth2"):
        return jsonify({"error": "provider_type must be saml, oidc, or oauth2"}), 400

    # Config validation per type
    if provider_type == "saml":
        required = ["entity_id", "sso_url", "certificate"]
    elif provider_type == "oidc":
        required = ["issuer", "client_id", "client_secret", "authorization_endpoint", "token_endpoint"]
    else:
        required = ["client_id", "client_secret", "authorize_url", "token_url"]

    missing = [r for r in required if r not in config]
    if missing:
        return jsonify({"error": f"Missing config fields: {missing}"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    idp_id = str(uuid.uuid4())

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO identity_providers (id, org_id, provider_type, name, config_json, created_at, updated_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (idp_id, org_id, provider_type, name, json.dumps(config), now, now))
        conn.commit()

    return jsonify({"id": idp_id, "provider_type": provider_type, "name": name}), 201


@identity_bp.route("/api/v1/auth/sso/login/<org_slug>", methods=["GET"])
def sso_login(org_slug):
    """Initiate SSO login. Redirects user to their org's identity provider."""
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT ip.id, ip.provider_type, ip.config_json
            FROM identity_providers ip
            JOIN organizations o ON ip.org_id = o.id
            WHERE o.slug = {p} AND ip.is_active = 1
            LIMIT 1
        """, (org_slug,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "No SSO provider configured for this organization"}), 404

    idp = dict(row) if hasattr(row, "keys") else dict(zip(["id", "provider_type", "config_json"], row))
    config = json.loads(idp["config_json"])
    state = secrets.token_urlsafe(32)

    if idp["provider_type"] == "oidc":
        params = {
            "client_id": config["client_id"],
            "response_type": "code",
            "scope": "openid email profile",
            "redirect_uri": f"{request.host_url}api/v1/auth/sso/callback",
            "state": state,
            "nonce": secrets.token_urlsafe(16),
        }
        return redirect(f"{config['authorization_endpoint']}?{urlencode(params)}")

    elif idp["provider_type"] == "oauth2":
        params = {
            "client_id": config["client_id"],
            "response_type": "code",
            "redirect_uri": f"{request.host_url}api/v1/auth/sso/callback",
            "state": state,
        }
        return redirect(f"{config['authorize_url']}?{urlencode(params)}")

    elif idp["provider_type"] == "saml":
        # SAML requires XML-based AuthnRequest
        return jsonify({"redirect_url": config["sso_url"], "state": state})

    return jsonify({"error": "Unknown provider type"}), 400


@identity_bp.route("/api/v1/auth/sso/callback", methods=["GET", "POST"])
def sso_callback():
    """Handle SSO callback from identity provider. Exchange code for tokens, create/match user."""
    code = request.args.get("code") or (request.get_json() or {}).get("code")
    state = request.args.get("state")

    if not code:
        return jsonify({"error": "No authorization code received"}), 400

    # In production: validate state, exchange code with IdP, get user info
    # This is the integration point — specific to each IdP
    return jsonify({
        "status": "sso_callback_received",
        "code": code[:10] + "...",
        "note": "Exchange code with IdP token endpoint, extract user info, create/match user, issue JWT"
    })


# ══════════════════════════════════════════════
# JWT AUTH ROUTES
# ══════════════════════════════════════════════
@identity_bp.route("/api/v1/auth/token", methods=["POST"])
def issue_token():
    """Exchange credentials for JWT access + refresh tokens."""
    from auth import _verify_password
    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password", "")

    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, email, password_hash, org_id, role, is_active
            FROM users WHERE email = {p}
        """, (email,))
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "Invalid credentials"}), 401

    user = dict(row) if hasattr(row, "keys") else dict(zip(
        ["id", "email", "password_hash", "org_id", "role", "is_active"], row))

    if not user["is_active"] or not _verify_password(password, user["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401

    # Check if MFA is required
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT is_enabled FROM mfa_secrets WHERE user_id = {p}", (user["id"],))
        mfa_row = cur.fetchone()

    if mfa_row:
        mfa_enabled = (dict(mfa_row) if hasattr(mfa_row, "keys") else {"is_enabled": mfa_row[0]})["is_enabled"]
        if mfa_enabled:
            # Issue a temporary MFA challenge token
            mfa_token = pyjwt.encode({
                "sub": user["id"], "type": "mfa_challenge",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5)
            }, JWT_SECRET, algorithm=JWT_ALGORITHM)
            return jsonify({"mfa_required": True, "mfa_token": mfa_token}), 200

    access = create_access_token(user["id"], user["email"], user["org_id"], user["role"])
    refresh = create_refresh_token(user["id"])
    return jsonify({"access_token": access, "refresh_token": refresh, "token_type": "Bearer"})


@identity_bp.route("/api/v1/auth/token/refresh", methods=["POST"])
def refresh_token_route():
    """Exchange refresh token for new access + refresh token pair."""
    payload = request.get_json() or {}
    old_refresh = payload.get("refresh_token", "")

    access, refresh = rotate_refresh_token(old_refresh)
    if not access:
        return jsonify({"error": "Invalid or expired refresh token"}), 401

    return jsonify({"access_token": access, "refresh_token": refresh, "token_type": "Bearer"})


@identity_bp.route("/api/v1/auth/mfa/verify", methods=["POST"])
def mfa_verify_route():
    """Complete MFA challenge with TOTP code."""
    payload = request.get_json() or {}
    mfa_token = payload.get("mfa_token", "")
    code = payload.get("code", "")

    try:
        claims = pyjwt.decode(mfa_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if claims.get("type") != "mfa_challenge":
            return jsonify({"error": "Invalid MFA token"}), 401
    except Exception:
        return jsonify({"error": "Invalid MFA token"}), 401

    user_id = claims["sub"]
    if not mfa_verify(user_id, code):
        return jsonify({"error": "Invalid MFA code"}), 401

    # Get user info and issue real tokens
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT email, org_id, role FROM users WHERE id = {p}", (user_id,))
        row = cur.fetchone()

    u = dict(row) if hasattr(row, "keys") else dict(zip(["email", "org_id", "role"], row))
    access = create_access_token(user_id, u["email"], u["org_id"], u["role"])
    refresh = create_refresh_token(user_id)
    return jsonify({"access_token": access, "refresh_token": refresh, "token_type": "Bearer"})


@identity_bp.route("/api/v1/auth/mfa/setup", methods=["POST"])
def mfa_setup_route():
    """Begin MFA enrollment. Returns QR code URI and backup codes."""
    from auth import require_auth
    # Inline auth check
    user = _get_jwt_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    result = mfa_setup(user["sub"], user["email"])
    return jsonify(result)


@identity_bp.route("/api/v1/auth/mfa/enable", methods=["POST"])
def mfa_enable_route():
    """Confirm MFA setup with a valid TOTP code."""
    user = _get_jwt_user()
    if not user:
        return jsonify({"error": "Authentication required"}), 401

    code = (request.get_json() or {}).get("code", "")
    if not mfa_verify(user["sub"], code):
        return jsonify({"error": "Invalid code. MFA not enabled."}), 400

    mfa_enable(user["sub"])
    return jsonify({"mfa_enabled": True})


# ══════════════════════════════════════════════
# ABAC: Attribute-Based Access Control
# ══════════════════════════════════════════════
def evaluate_abac(user: dict, resource: str, action: str) -> bool:
    """Evaluate ABAC policies for a user/resource/action combination."""
    p = param_placeholder()
    org_id = user.get("org_id")
    if not org_id:
        return False

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT effect, conditions_json FROM abac_policies
            WHERE org_id = {p} AND resource = {p} AND action = {p} AND is_active = 1
            ORDER BY priority DESC
        """, (org_id, resource, action))
        rows = cur.fetchall()

    if not rows:
        return True  # No policy = allow by default (RBAC handles base access)

    for row in rows:
        policy = dict(row) if hasattr(row, "keys") else dict(zip(["effect", "conditions_json"], row))
        conditions = json.loads(policy["conditions_json"])

        # Evaluate conditions against user attributes
        match = True
        for attr, expected in conditions.items():
            user_val = user.get(attr)
            if isinstance(expected, list):
                if user_val not in expected:
                    match = False
                    break
            elif user_val != expected:
                match = False
                break

        if match:
            return policy["effect"] == "allow"

    return True  # No matching policy = allow


# ══════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════
def _get_jwt_user() -> dict:
    """Extract user from JWT in Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    return validate_access_token(token)


def require_jwt(f):
    """Decorator: require valid JWT access token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_jwt_user()
        if not user:
            return jsonify({"error": "Valid JWT required"}), 401
        request.jwt_user = user
        return f(*args, **kwargs)
    return decorated


def require_scope(*scopes):
    """Decorator: require specific JWT scopes."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            user = _get_jwt_user()
            if not user:
                return jsonify({"error": "Valid JWT required"}), 401
            user_scopes = user.get("scopes", [])
            if not any(s in user_scopes for s in scopes):
                return jsonify({"error": f"Required scopes: {list(scopes)}"}), 403
            request.jwt_user = user
            return f(*args, **kwargs)
        return decorated
    return decorator
