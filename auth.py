"""
Artifact Zero — Auth & Billing Module
Signup, login, password reset, Stripe checkout, SendGrid email, /dashboard
"""
import os
import uuid
import json
import hashlib
import hmac
import secrets
from functools import wraps

from flask import Blueprint, request, jsonify, render_template, redirect, session

auth_bp = Blueprint('auth', __name__)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "hello@artifact0.com")
SITE_URL = os.getenv("SITE_URL", "https://artifact0.com")

import db as database

# ═══════════════════════════════════════
# PASSWORD HASHING — PBKDF2-SHA256
# ═══════════════════════════════════════
def hash_password(pw):
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', pw.encode(), salt, 100000)
    return (salt + key).hex()

def verify_password(pw, stored):
    b = bytes.fromhex(stored)
    return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', pw.encode(), b[:32], 100000), b[32:])

# ═══════════════════════════════════════
# DB
# ═══════════════════════════════════════
def _ensure_users_table():
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'free',
            role TEXT NOT NULL DEFAULT 'user',
            stripe_customer_id TEXT, stripe_subscription_id TEXT,
            score_count INTEGER NOT NULL DEFAULT 0, active BOOLEAN NOT NULL DEFAULT TRUE)""")
        # Add role column if missing (migration)
        try:
            cur.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        except Exception:
            conn.rollback()
        cur.execute("""CREATE TABLE IF NOT EXISTS password_resets (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), used BOOLEAN NOT NULL DEFAULT FALSE)""")
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'free',
            role TEXT NOT NULL DEFAULT 'user',
            stripe_customer_id TEXT, stripe_subscription_id TEXT,
            score_count INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS password_resets (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), used INTEGER NOT NULL DEFAULT 0)""")
    conn.commit()
    conn.close()

def _user_by_email(email):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT id,email,password_hash,name,tier,score_count,stripe_customer_id,role FROM users WHERE email=%s" if database.USE_PG else "SELECT id,email,password_hash,name,tier,score_count,stripe_customer_id,role FROM users WHERE email=?"
    cur.execute(q, (email.lower(),))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    keys = ["id","email","password_hash","name","tier","score_count","stripe_customer_id","role"]
    return dict(zip(keys, row)) if database.USE_PG else dict(row)

def _user_by_id(uid):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT id,email,name,tier,score_count,stripe_customer_id,created_at,role FROM users WHERE id=%s" if database.USE_PG else "SELECT id,email,name,tier,score_count,stripe_customer_id,created_at,role FROM users WHERE id=?"
    cur.execute(q, (uid,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    keys = ["id","email","name","tier","score_count","stripe_customer_id","created_at","role"]
    return dict(zip(keys, [str(v) if i==6 else v for i,v in enumerate(row)])) if database.USE_PG else dict(row)

def _create_user(email, pw, name=""):
    uid = "usr_" + uuid.uuid4().hex[:16]
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("INSERT INTO users (id,email,password_hash,name) VALUES (%s,%s,%s,%s)", (uid, email.lower(), hash_password(pw), name))
    else:
        cur.execute("INSERT INTO users (id,created_at,email,password_hash,name) VALUES (?,datetime('now'),?,?,?)", (uid, email.lower(), hash_password(pw), name))
    conn.commit()
    conn.close()
    return uid

def _update_password(uid, new_pw):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "UPDATE users SET password_hash=%s WHERE id=%s" if database.USE_PG else "UPDATE users SET password_hash=? WHERE id=?"
    cur.execute(q, (hash_password(new_pw), uid))
    conn.commit()
    conn.close()

def _update_stripe(uid, cust_id, sub_id, tier):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "UPDATE users SET stripe_customer_id=%s,stripe_subscription_id=%s,tier=%s WHERE id=%s" if database.USE_PG else "UPDATE users SET stripe_customer_id=?,stripe_subscription_id=?,tier=? WHERE id=?"
    cur.execute(q, (cust_id, sub_id, tier, uid))
    conn.commit()
    conn.close()

def _create_reset_token(user_id):
    token = secrets.token_urlsafe(32)
    rid = "rst_" + uuid.uuid4().hex[:16]
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("UPDATE password_resets SET used=TRUE WHERE user_id=%s AND used=FALSE", (user_id,))
        cur.execute("INSERT INTO password_resets (id,user_id,token) VALUES (%s,%s,%s)", (rid, user_id, token))
    else:
        cur.execute("UPDATE password_resets SET used=1 WHERE user_id=? AND used=0", (user_id,))
        cur.execute("INSERT INTO password_resets (id,user_id,token) VALUES (?,?,?)", (rid, user_id, token))
    conn.commit()
    conn.close()
    return token

def _validate_reset_token(token):
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("SELECT user_id,created_at FROM password_resets WHERE token=%s AND used=FALSE", (token,))
    else:
        cur.execute("SELECT user_id,created_at FROM password_resets WHERE token=? AND used=0", (token,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    user_id = row[0] if database.USE_PG else row["user_id"]
    created = row[1] if database.USE_PG else row["created_at"]
    from datetime import datetime, timezone, timedelta
    if database.USE_PG:
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(hours=1):
            return None
    return user_id

def _consume_reset_token(token):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "UPDATE password_resets SET used=TRUE WHERE token=%s" if database.USE_PG else "UPDATE password_resets SET used=1 WHERE token=?"
    cur.execute(q, (token,))
    conn.commit()
    conn.close()

# ═══════════════════════════════════════
# SESSION
# ═══════════════════════════════════════
def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        uid = session.get("user_id")
        if not uid: return redirect("/login")
        user = _user_by_id(uid)
        if not user:
            session.clear()
            return redirect("/login")
        request._user = user
        return f(*a, **kw)
    return dec

# ═══════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════
@auth_bp.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")
    data = request.form or request.get_json() or {}
    email = (data.get("email") or "").strip()
    pw = data.get("password") or ""
    name = (data.get("name") or "").strip()
    if not email or not pw:
        return render_template("signup.html", error="Email and password required."), 400
    if len(pw) < 8:
        return render_template("signup.html", error="Password must be at least 8 characters."), 400
    if _user_by_email(email):
        return render_template("signup.html", error="Account exists. Log in instead."), 400
    try:
        uid = _create_user(email, pw, name)
    except:
        return render_template("signup.html", error="Could not create account."), 500
    session["user_id"] = uid
    _send_welcome(email, name)
    return redirect("/dashboard")

@auth_bp.route("/login", methods=["GET","POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    data = request.form or request.get_json() or {}
    email = (data.get("email") or "").strip()
    pw = data.get("password") or ""
    user = _user_by_email(email)
    if not user or not verify_password(pw, user["password_hash"]):
        return render_template("login.html", error="Invalid email or password."), 401
    session["user_id"] = user["id"]
    session["role"] = user.get("role", "user")
    return redirect("/dashboard")

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@auth_bp.route("/forgot", methods=["GET","POST"])
def forgot_password():
    if request.method == "GET":
        return render_template("forgot.html")
    data = request.form or request.get_json() or {}
    email = (data.get("email") or "").strip()
    if not email:
        return render_template("forgot.html", error="Enter your email address."), 400
    user = _user_by_email(email)
    if user:
        token = _create_reset_token(user["id"])
        _send_reset_email(email, user.get("name", ""), token)
    return render_template("forgot.html", success=True)

@auth_bp.route("/reset", methods=["GET","POST"])
def reset_password():
    token = request.args.get("token") or (request.form or {}).get("token") or ""
    if request.method == "GET":
        if not token:
            return redirect("/forgot")
        user_id = _validate_reset_token(token)
        if not user_id:
            return render_template("reset.html", error="This reset link has expired or already been used.", expired=True)
        return render_template("reset.html", token=token)
    data = request.form or request.get_json() or {}
    token = data.get("token") or ""
    pw = data.get("password") or ""
    if not token:
        return redirect("/forgot")
    user_id = _validate_reset_token(token)
    if not user_id:
        return render_template("reset.html", error="This reset link has expired or already been used.", expired=True)
    if len(pw) < 8:
        return render_template("reset.html", token=token, error="Password must be at least 8 characters.")
    _update_password(user_id, pw)
    _consume_reset_token(token)
    session["user_id"] = user_id
    return redirect("/dashboard")

@auth_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", user=request._user)

@auth_bp.route("/checkout", methods=["POST"])
@login_required
def checkout():
    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({"error": "Payment not configured"}), 500
    import urllib.request, urllib.parse
    user = request._user
    params = urllib.parse.urlencode({
        "mode": "subscription", "payment_method_types[0]": "card",
        "line_items[0][price]": STRIPE_PRICE_ID, "line_items[0][quantity]": "1",
        "success_url": request.host_url.rstrip("/") + "/dashboard?upgraded=1",
        "cancel_url": request.host_url.rstrip("/") + "/dashboard?cancelled=1",
        "client_reference_id": user["id"], "customer_email": user["email"],
    }).encode()
    req = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions", data=params,
        headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return redirect(json.loads(resp.read())["url"])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@auth_bp.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    try: event = json.loads(request.get_data(as_text=True))
    except: return "Invalid JSON", 400
    t = event.get("type","")
    if t == "checkout.session.completed":
        s = event["data"]["object"]
        uid = s.get("client_reference_id")
        if uid: _update_stripe(uid, s.get("customer"), s.get("subscription"), "personal")
    elif t == "customer.subscription.deleted":
        cid = event["data"]["object"].get("customer")
        conn = database.db_connect()
        cur = conn.cursor()
        q = "UPDATE users SET tier='free',stripe_subscription_id=NULL WHERE stripe_customer_id=%s" if database.USE_PG else "UPDATE users SET tier='free',stripe_subscription_id=NULL WHERE stripe_customer_id=?"
        cur.execute(q, (cid,))
        conn.commit()
        conn.close()
    return "ok", 200

# ═══════════════════════════════════════
# STRIPE HEALTH CHECK
# ═══════════════════════════════════════
@auth_bp.route("/api/stripe/status")
def stripe_status():
    result = {"stripe_key_loaded": bool(STRIPE_SECRET_KEY), "price_id_loaded": bool(STRIPE_PRICE_ID),
              "key_type": "live" if STRIPE_SECRET_KEY.startswith("sk_live") else "test" if STRIPE_SECRET_KEY.startswith("sk_test") else "unknown"}
    if STRIPE_SECRET_KEY:
        import urllib.request
        try:
            req = urllib.request.Request("https://api.stripe.com/v1/balance",
                headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"})
            with urllib.request.urlopen(req) as resp:
                bal = json.loads(resp.read())
                result["stripe_connected"] = True
                result["balance"] = bal.get("available", [{}])[0]
        except Exception as e:
            result["stripe_connected"] = False
            result["error"] = str(e)
    return jsonify(result)

# ═══════════════════════════════════════
# SENDGRID
# ═══════════════════════════════════════
def _send_email(to_email, subject, html_body, text_body=None):
    if not SENDGRID_API_KEY:
        print(f"[auth] No SendGrid key, skipping email to {to_email}", flush=True)
        return False
    import urllib.request
    content = [{"type": "text/html", "value": html_body}]
    if text_body:
        content.insert(0, {"type": "text/plain", "value": text_body})
    body = json.dumps({
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": FROM_EMAIL, "name": "Artifact Zero"},
        "subject": subject,
        "content": content,
        "tracking_settings": {"click_tracking": {"enable": False}, "open_tracking": {"enable": False}}
    }).encode()
    req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=body,
        headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req)
        print(f"[auth] Email sent to {to_email}: {subject}", flush=True)
        return True
    except Exception as e:
        print(f"[auth] Email failed for {to_email}: {e}", flush=True)
        return False

def _send_welcome(email, name):
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Welcome to Artifact Zero"
    html = f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#e8eaf0;background:#0a0c10;border-radius:12px">
<div style="font-family:'Courier New',monospace;font-size:13px;letter-spacing:3px;color:#00e89c;margin-bottom:24px">ARTIFACT ZERO</div>
<p style="font-size:16px;line-height:1.6;margin:0 0 20px">{greeting}</p>
<p style="font-size:15px;line-height:1.7;color:#ccc;margin:0 0 24px">Your account is live. Structure check your words before they go anywhere.</p>
<div style="margin:20px 0">
<a href="{SITE_URL}/safecheck" style="display:inline-block;padding:12px 28px;background:#00e89c;color:#0a0c10;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">SafeCheck Now</a>
</div>
<div style="margin:24px 0;padding:16px;background:#12151b;border-radius:8px;border:1px solid #252a35">
<div style="font-size:12px;color:#6b7280;margin-bottom:8px">YOUR TOOLS</div>
<a href="{SITE_URL}/safecheck" style="display:block;color:#00e89c;text-decoration:none;padding:4px 0;font-size:14px">SafeCheck</a>
<a href="{SITE_URL}/relay" style="display:block;color:#00e89c;text-decoration:none;padding:4px 0;font-size:14px">Relay</a>
<a href="{SITE_URL}/wall" style="display:block;color:#00e89c;text-decoration:none;padding:4px 0;font-size:14px">Wall</a>
<a href="{SITE_URL}/dashboard" style="display:block;color:#00e89c;text-decoration:none;padding:4px 0;font-size:14px">Dashboard</a>
<a href="{SITE_URL}/docs" style="display:block;color:#00e89c;text-decoration:none;padding:4px 0;font-size:14px">API Docs</a>
</div>
<p style="font-size:12px;color:#6b7280;margin:24px 0 0">Artifact Zero &middot; Knoxville, Tennessee</p>
</div>"""
    text = f"""{greeting}\n\nYour account is live.\n\nSafeCheck: {SITE_URL}/safecheck\nRelay: {SITE_URL}/relay\nWall: {SITE_URL}/wall\nDashboard: {SITE_URL}/dashboard\nAPI: {SITE_URL}/docs\n\n— Artifact Zero\nKnoxville, Tennessee"""
    _send_email(email, subject, html, text)

def _send_reset_email(email, name, token):
    reset_url = f"{SITE_URL}/reset?token={token}"
    greeting = f"Hi {name}," if name else "Hi,"
    subject = "Reset your password — Artifact Zero"
    html = f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#e8eaf0;background:#0a0c10;border-radius:12px">
<div style="font-family:'Courier New',monospace;font-size:13px;letter-spacing:3px;color:#00e89c;margin-bottom:24px">ARTIFACT ZERO</div>
<p style="font-size:16px;line-height:1.6;margin:0 0 20px">{greeting}</p>
<p style="font-size:15px;line-height:1.7;color:#ccc;margin:0 0 24px">Someone requested a password reset for your account. If that was you, click below. If not, ignore this email.</p>
<div style="margin:20px 0">
<a href="{reset_url}" style="display:inline-block;padding:12px 28px;background:#00e89c;color:#0a0c10;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Reset Password</a>
</div>
<p style="font-size:12px;color:#6b7280;margin:20px 0 0">This link expires in 1 hour.</p>
<p style="font-size:12px;color:#6b7280;margin:24px 0 0">Artifact Zero &middot; Knoxville, Tennessee</p>
</div>"""
    text = f"""{greeting}\n\nReset your password: {reset_url}\n\nThis link expires in 1 hour. If you didn't request this, ignore this email.\n\n— Artifact Zero\nKnoxville, Tennessee"""
    _send_email(email, subject, html, text)

_ensure_users_table()
print(f"[auth] Loaded. Stripe={'OK' if STRIPE_SECRET_KEY else 'NO'}. SendGrid={'OK' if SENDGRID_API_KEY else 'NO'}.", flush=True)
