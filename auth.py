"""
Artifact Zero — Auth & Billing Module
Signup, login, Stripe checkout, SendGrid email, /dashboard
"""
import os
import uuid
import json
import hashlib
import hmac
from functools import wraps

from flask import Blueprint, request, jsonify, render_template, redirect, session

auth_bp = Blueprint('auth', __name__)

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "jame@artifact0.com")

import db as database

# ═══════════════════════════════════════
# PASSWORD HASHING — PBKDF2-SHA256 (no external deps)
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
            stripe_customer_id TEXT, stripe_subscription_id TEXT,
            score_count INTEGER NOT NULL DEFAULT 0, active BOOLEAN NOT NULL DEFAULT TRUE)""")
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '', tier TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id TEXT, stripe_subscription_id TEXT,
            score_count INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1)""")
    conn.commit()
    conn.close()

def _user_by_email(email):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT id,email,password_hash,name,tier,score_count,stripe_customer_id FROM users WHERE email=%s" if database.USE_PG else "SELECT id,email,password_hash,name,tier,score_count,stripe_customer_id FROM users WHERE email=?"
    cur.execute(q, (email.lower(),))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    keys = ["id","email","password_hash","name","tier","score_count","stripe_customer_id"]
    return dict(zip(keys, row)) if database.USE_PG else dict(row)

def _user_by_id(uid):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT id,email,name,tier,score_count,stripe_customer_id,created_at FROM users WHERE id=%s" if database.USE_PG else "SELECT id,email,name,tier,score_count,stripe_customer_id,created_at FROM users WHERE id=?"
    cur.execute(q, (uid,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    keys = ["id","email","name","tier","score_count","stripe_customer_id","created_at"]
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

def _update_stripe(uid, cust_id, sub_id, tier):
    conn = database.db_connect()
    cur = conn.cursor()
    q = "UPDATE users SET stripe_customer_id=%s,stripe_subscription_id=%s,tier=%s WHERE id=%s" if database.USE_PG else "UPDATE users SET stripe_customer_id=?,stripe_subscription_id=?,tier=? WHERE id=?"
    cur.execute(q, (cust_id, sub_id, tier, uid))
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
    return redirect("/dashboard")

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")

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
# SENDGRID
# ═══════════════════════════════════════
def _send_welcome(email, name):
    if not SENDGRID_API_KEY:
        print(f"[auth] No SendGrid key, skipping welcome to {email}", flush=True)
        return
    import urllib.request
    body = json.dumps({
        "personalizations": [{"to": [{"email": email}]}],
        "from": {"email": FROM_EMAIL, "name": "Artifact Zero"},
        "subject": f"Welcome to Artifact Zero{', '+name if name else ''}",
        "content": [{"type": "text/plain", "value": f"{'Hi '+name+',' if name else 'Hi,'}\n\nYour account is live.\n\n  Score: https://www.artifact0.com/score\n  Examples: https://www.artifact0.com/examples\n  Compose: https://www.artifact0.com/compose\n  API: https://www.artifact0.com/docs\n  Dashboard: https://www.artifact0.com/dashboard\n\n— Artifact Zero\nKnoxville, Tennessee\n"}]
    }).encode()
    req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=body,
        headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req)
        print(f"[auth] Welcome sent to {email}", flush=True)
    except Exception as e:
        print(f"[auth] Welcome failed for {email}: {e}", flush=True)

_ensure_users_table()
print(f"[auth] Loaded. Stripe={'OK' if STRIPE_SECRET_KEY else 'NO'}. SendGrid={'OK' if SENDGRID_API_KEY else 'NO'}.", flush=True)
