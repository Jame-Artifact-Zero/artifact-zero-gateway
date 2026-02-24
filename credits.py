"""
Artifact Zero — Prepaid Credit System
Pay-per-score. Deposit funds, every score deducts. Low-balance alerts.
Works alongside existing auth.py and app.py.

PRICING (from docs page):
  /api/v1/score (API)       = $0.01/score
  /api/v1/score (Dashboard) = $0.04/score
  /api/v1/score (V3 fix)    = $0.04/score

FLOW:
  1. User signs up (free tier, $0 balance)
  2. User tops up via Stripe ($10, $50, $100, $500, custom)
  3. Every paid API call deducts from balance
  4. Free tier (/api/v1/score/free) = no charge, no balance needed
  5. Low balance email at $1.00 remaining
  6. Zero balance = 402 Payment Required
"""
import os
import uuid
import json
import time
from datetime import datetime, timezone

import db as database

# ═══════════════════════════════════════
# PRICING CONFIG
# ═══════════════════════════════════════
COST_PER_SCORE = {
    "api": 0.01,       # /api/v1/score via API key
    "dashboard": 0.04, # /api/v1/score via dashboard/session
    "v3": 0.04,        # V3 stabilization/rewrite
}

LOW_BALANCE_THRESHOLD = 1.00  # dollars
CREDIT_PACKS = {
    "starter": {"amount": 10.00,  "label": "$10 (1,000 API scores)"},
    "builder": {"amount": 50.00,  "label": "$50 (5,000 API scores)"},
    "scale":   {"amount": 100.00, "label": "$100 (10,000 API scores)"},
    "enterprise": {"amount": 500.00, "label": "$500 (50,000 API scores)"},
}

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "hello@artifact0.com")
SITE_URL = os.getenv("SITE_URL", "https://artifact0.com")


# ═══════════════════════════════════════
# DB — Balance & Transactions
# ═══════════════════════════════════════
def ensure_credit_tables():
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("""CREATE TABLE IF NOT EXISTS credit_balances (
            user_id TEXT PRIMARY KEY REFERENCES users(id),
            balance_cents INTEGER NOT NULL DEFAULT 0,
            total_deposited_cents INTEGER NOT NULL DEFAULT 0,
            total_spent_cents INTEGER NOT NULL DEFAULT 0,
            low_balance_notified BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS credit_transactions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            balance_after_cents INTEGER NOT NULL,
            description TEXT,
            stripe_session_id TEXT,
            api_key_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ct_user ON credit_transactions(user_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ct_created ON credit_transactions(created_at)")
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS credit_balances (
            user_id TEXT PRIMARY KEY,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            total_deposited_cents INTEGER NOT NULL DEFAULT 0,
            total_spent_cents INTEGER NOT NULL DEFAULT 0,
            low_balance_notified INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        cur.execute("""CREATE TABLE IF NOT EXISTS credit_transactions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            balance_after_cents INTEGER NOT NULL,
            description TEXT,
            stripe_session_id TEXT,
            api_key_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
    conn.commit()
    conn.close()


def get_balance(user_id):
    """Get user's current balance in cents. Returns 0 if no record."""
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT balance_cents FROM credit_balances WHERE user_id=%s" if database.USE_PG else "SELECT balance_cents FROM credit_balances WHERE user_id=?"
    cur.execute(q, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row: return 0
    return row[0] if database.USE_PG else row["balance_cents"]


def get_balance_info(user_id):
    """Get full balance info. Returns dict."""
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT balance_cents, total_deposited_cents, total_spent_cents FROM credit_balances WHERE user_id=%s" if database.USE_PG else "SELECT balance_cents, total_deposited_cents, total_spent_cents FROM credit_balances WHERE user_id=?"
    cur.execute(q, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {"balance": 0.0, "total_deposited": 0.0, "total_spent": 0.0, "scores_remaining_api": 0, "scores_remaining_dashboard": 0}
    if database.USE_PG:
        bal, dep, spent = row[0], row[1], row[2]
    else:
        bal, dep, spent = row["balance_cents"], row["total_deposited_cents"], row["total_spent_cents"]
    return {
        "balance": bal / 100,
        "total_deposited": dep / 100,
        "total_spent": spent / 100,
        "scores_remaining_api": bal // int(COST_PER_SCORE["api"] * 100),
        "scores_remaining_dashboard": bal // int(COST_PER_SCORE["dashboard"] * 100),
    }


def add_credits(user_id, amount_cents, description, stripe_session_id=None):
    """Add credits to user balance. Returns new balance in cents."""
    conn = database.db_connect()
    cur = conn.cursor()
    tx_id = "tx_" + uuid.uuid4().hex[:16]

    if database.USE_PG:
        # Upsert balance
        cur.execute("""INSERT INTO credit_balances (user_id, balance_cents, total_deposited_cents)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                balance_cents = credit_balances.balance_cents + %s,
                total_deposited_cents = credit_balances.total_deposited_cents + %s,
                low_balance_notified = FALSE,
                updated_at = NOW()""",
            (user_id, amount_cents, amount_cents, amount_cents, amount_cents))
        cur.execute("SELECT balance_cents FROM credit_balances WHERE user_id=%s", (user_id,))
        new_bal = cur.fetchone()[0]
        cur.execute("""INSERT INTO credit_transactions (id, user_id, type, amount_cents, balance_after_cents, description, stripe_session_id)
            VALUES (%s, %s, 'deposit', %s, %s, %s, %s)""",
            (tx_id, user_id, amount_cents, new_bal, description, stripe_session_id))
    else:
        cur.execute("SELECT balance_cents FROM credit_balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row:
            new_bal = row["balance_cents"] + amount_cents
            cur.execute("UPDATE credit_balances SET balance_cents=?, total_deposited_cents=total_deposited_cents+?, low_balance_notified=0, updated_at=datetime('now') WHERE user_id=?",
                (new_bal, amount_cents, user_id))
        else:
            new_bal = amount_cents
            cur.execute("INSERT INTO credit_balances (user_id, balance_cents, total_deposited_cents) VALUES (?, ?, ?)",
                (user_id, amount_cents, amount_cents))
        cur.execute("INSERT INTO credit_transactions (id, user_id, type, amount_cents, balance_after_cents, description, stripe_session_id) VALUES (?, ?, 'deposit', ?, ?, ?, ?)",
            (tx_id, user_id, amount_cents, new_bal, description, stripe_session_id))
    conn.commit()
    conn.close()
    print(f"[credits] +${amount_cents/100:.2f} for {user_id}. Balance: ${new_bal/100:.2f}", flush=True)
    return new_bal


def deduct_credit(user_id, cost_type, api_key_id=None):
    """Deduct one score from balance. Returns (success, new_balance_cents) or (False, 0)."""
    cost_cents = int(COST_PER_SCORE.get(cost_type, 0.01) * 100)
    conn = database.db_connect()
    cur = conn.cursor()

    if database.USE_PG:
        cur.execute("SELECT balance_cents, low_balance_notified FROM credit_balances WHERE user_id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row or row[0] < cost_cents:
            conn.close()
            return False, 0
        new_bal = row[0] - cost_cents
        was_notified = row[1]
        cur.execute("UPDATE credit_balances SET balance_cents=%s, total_spent_cents=total_spent_cents+%s, updated_at=NOW() WHERE user_id=%s",
            (new_bal, cost_cents, user_id))
        tx_id = "tx_" + uuid.uuid4().hex[:16]
        cur.execute("""INSERT INTO credit_transactions (id, user_id, type, amount_cents, balance_after_cents, description, api_key_id)
            VALUES (%s, %s, 'score', %s, %s, %s, %s)""",
            (tx_id, user_id, -cost_cents, new_bal, f"{cost_type} score", api_key_id))
        # Low balance check
        if new_bal <= int(LOW_BALANCE_THRESHOLD * 100) and not was_notified:
            cur.execute("UPDATE credit_balances SET low_balance_notified=TRUE WHERE user_id=%s", (user_id,))
            conn.commit()
            conn.close()
            _send_low_balance_alert(user_id, new_bal)
            return True, new_bal
    else:
        cur.execute("SELECT balance_cents, low_balance_notified FROM credit_balances WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row or row["balance_cents"] < cost_cents:
            conn.close()
            return False, 0
        new_bal = row["balance_cents"] - cost_cents
        was_notified = row["low_balance_notified"]
        cur.execute("UPDATE credit_balances SET balance_cents=?, total_spent_cents=total_spent_cents+?, updated_at=datetime('now') WHERE user_id=?",
            (new_bal, cost_cents, user_id))
        tx_id = "tx_" + uuid.uuid4().hex[:16]
        cur.execute("INSERT INTO credit_transactions (id, user_id, type, amount_cents, balance_after_cents, description, api_key_id) VALUES (?, ?, 'score', ?, ?, ?, ?)",
            (tx_id, user_id, -cost_cents, new_bal, f"{cost_type} score", api_key_id))
        if new_bal <= int(LOW_BALANCE_THRESHOLD * 100) and not was_notified:
            cur.execute("UPDATE credit_balances SET low_balance_notified=1 WHERE user_id=?", (user_id,))
            conn.commit()
            conn.close()
            _send_low_balance_alert(user_id, new_bal)
            return True, new_bal

    conn.commit()
    conn.close()
    return True, new_bal


def get_transactions(user_id, limit=50):
    """Get recent transactions for a user."""
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("SELECT id, type, amount_cents, balance_after_cents, description, created_at FROM credit_transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit))
        rows = cur.fetchall()
        conn.close()
        keys = ["id", "type", "amount_cents", "balance_after_cents", "description", "created_at"]
        return [dict(zip(keys, r)) for r in rows]
    else:
        cur.execute("SELECT id, type, amount_cents, balance_after_cents, description, created_at FROM credit_transactions WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit))
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_user_id_for_api_key(api_key_id):
    """Look up the user_id that owns an API key."""
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT owner_email FROM api_keys WHERE id=%s" if database.USE_PG else "SELECT owner_email FROM api_keys WHERE id=?"
    cur.execute(q, (api_key_id,))
    row = cur.fetchone()
    conn.close()
    if not row: return None
    email = row[0] if database.USE_PG else row["owner_email"]
    # Look up user by email
    q2 = "SELECT id FROM users WHERE email=%s" if database.USE_PG else "SELECT id FROM users WHERE email=?"
    conn = database.db_connect()
    cur = conn.cursor()
    cur.execute(q2, (email,))
    urow = cur.fetchone()
    conn.close()
    if not urow: return None
    return urow[0] if database.USE_PG else urow["id"]


# ═══════════════════════════════════════
# STRIPE TOP-UP CHECKOUT
# ═══════════════════════════════════════
def create_topup_session(user_id, user_email, amount_cents):
    """Create a Stripe Checkout session for credit top-up. Returns session URL."""
    if not STRIPE_SECRET_KEY:
        return None, "Stripe not configured"
    import urllib.request, urllib.parse

    params = urllib.parse.urlencode({
        "mode": "payment",
        "payment_method_types[0]": "card",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": f"Artifact Zero Credits (${amount_cents/100:.2f})",
        "line_items[0][quantity]": "1",
        "success_url": f"{SITE_URL}/dashboard?topup=success",
        "cancel_url": f"{SITE_URL}/dashboard?topup=cancelled",
        "client_reference_id": user_id,
        "customer_email": user_email,
        "metadata[type]": "credit_topup",
        "metadata[amount_cents]": str(amount_cents),
        "metadata[user_id]": user_id,
    }).encode()

    req = urllib.request.Request("https://api.stripe.com/v1/checkout/sessions", data=params,
        headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            session_data = json.loads(resp.read())
            return session_data["url"], None
    except Exception as e:
        return None, str(e)


def handle_topup_webhook(event):
    """Process a completed top-up payment. Called from stripe webhook."""
    session = event["data"]["object"]
    metadata = session.get("metadata", {})
    if metadata.get("type") != "credit_topup":
        return False
    user_id = metadata.get("user_id") or session.get("client_reference_id")
    amount_cents = int(metadata.get("amount_cents", 0))
    if not user_id or not amount_cents:
        return False
    stripe_session_id = session.get("id")
    add_credits(user_id, amount_cents, f"Stripe top-up ${amount_cents/100:.2f}", stripe_session_id)
    return True


# ═══════════════════════════════════════
# LOW BALANCE ALERT
# ═══════════════════════════════════════
def _send_low_balance_alert(user_id, balance_cents):
    """Send low balance email."""
    if not SENDGRID_API_KEY: return
    # Look up user email
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT email, name FROM users WHERE id=%s" if database.USE_PG else "SELECT email, name FROM users WHERE id=?"
    cur.execute(q, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row: return
    email = row[0] if database.USE_PG else row["email"]
    name = row[1] if database.USE_PG else row["name"]

    greeting = f"Hi {name}," if name else "Hi,"
    bal_str = f"${balance_cents/100:.2f}"
    subject = f"Low balance: {bal_str} remaining — Artifact Zero"
    html = f"""<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#e8eaf0;background:#0a0c10;border-radius:12px">
<div style="font-family:'Courier New',monospace;font-size:13px;letter-spacing:3px;color:#00e89c;margin-bottom:24px">ARTIFACT ZERO</div>
<p style="font-size:16px;line-height:1.6;margin:0 0 20px">{greeting}</p>
<p style="font-size:15px;line-height:1.7;color:#ccc;margin:0 0 16px">Your balance is <strong style="color:#f59e0b">{bal_str}</strong>. Scores will stop when your balance hits $0.00.</p>
<div style="margin:20px 0">
<a href="{SITE_URL}/dashboard" style="display:inline-block;padding:12px 28px;background:#00e89c;color:#0a0c10;font-weight:700;font-size:14px;text-decoration:none;border-radius:8px">Top Up Now</a>
</div>
<p style="font-size:12px;color:#6b7280;margin:24px 0 0">Artifact Zero &middot; Knoxville, Tennessee</p>
</div>"""

    import urllib.request
    body = json.dumps({
        "personalizations": [{"to": [{"email": email}]}],
        "from": {"email": FROM_EMAIL, "name": "Artifact Zero"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
        "tracking_settings": {"click_tracking": {"enable": False}, "open_tracking": {"enable": False}}
    }).encode()
    req = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=body,
        headers={"Authorization": f"Bearer {SENDGRID_API_KEY}", "Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req)
        print(f"[credits] Low balance alert sent to {email} (${balance_cents/100:.2f})", flush=True)
    except Exception as e:
        print(f"[credits] Low balance alert failed: {e}", flush=True)


# ═══════════════════════════════════════
# FLASK ROUTES (register as blueprint)
# ═══════════════════════════════════════
from flask import Blueprint, jsonify, request, redirect, session
credits_bp = Blueprint('credits', __name__)

@credits_bp.route("/api/credits/balance")
def api_balance():
    """Get current balance. Works with session (dashboard) or API key."""
    user_id = _get_user_id_from_request()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    info = get_balance_info(user_id)
    return jsonify(info)

@credits_bp.route("/api/credits/topup", methods=["POST"])
def api_topup():
    """Initiate Stripe top-up. POST {amount: 50.00} or {pack: 'builder'}"""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Login required"}), 401
    data = request.get_json() or request.form or {}
    # Look up user email
    conn = database.db_connect()
    cur = conn.cursor()
    q = "SELECT email FROM users WHERE id=%s" if database.USE_PG else "SELECT email FROM users WHERE id=?"
    cur.execute(q, (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "User not found"}), 404
    email = row[0] if database.USE_PG else row["email"]

    # Determine amount
    pack = data.get("pack")
    if pack and pack in CREDIT_PACKS:
        amount_cents = int(CREDIT_PACKS[pack]["amount"] * 100)
    else:
        amount = float(data.get("amount", 0))
        if amount < 5.0:
            return jsonify({"error": "Minimum top-up is $5.00"}), 400
        if amount > 10000.0:
            return jsonify({"error": "Maximum top-up is $10,000"}), 400
        amount_cents = int(amount * 100)

    url, error = create_topup_session(user_id, email, amount_cents)
    if error:
        return jsonify({"error": error}), 500
    return jsonify({"checkout_url": url})

@credits_bp.route("/api/credits/transactions")
def api_transactions():
    """Get recent transactions."""
    user_id = _get_user_id_from_request()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    limit = min(int(request.args.get("limit", 50)), 200)
    txns = get_transactions(user_id, limit)
    # Format for JSON
    for t in txns:
        t["amount"] = t["amount_cents"] / 100
        t["balance_after"] = t["balance_after_cents"] / 100
        t["created_at"] = str(t["created_at"])
    return jsonify({"transactions": txns})

@credits_bp.route("/api/credits/packs")
def api_packs():
    """List available credit packs."""
    packs = []
    for key, p in CREDIT_PACKS.items():
        packs.append({
            "id": key,
            "amount": p["amount"],
            "label": p["label"],
            "api_scores": int(p["amount"] / COST_PER_SCORE["api"]),
            "dashboard_scores": int(p["amount"] / COST_PER_SCORE["dashboard"]),
        })
    return jsonify({"packs": packs, "pricing": COST_PER_SCORE})


def _get_user_id_from_request():
    """Get user_id from session or API key."""
    uid = session.get("user_id")
    if uid: return uid
    api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
    if api_key:
        return get_user_id_for_api_key(api_key)
    return None


# Initialize tables on import
try:
    ensure_credit_tables()
    print("[credits] Tables initialized.", flush=True)
except Exception as e:
    print(f"[credits] Table init warning: {e}", flush=True)
