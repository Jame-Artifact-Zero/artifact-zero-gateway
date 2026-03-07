"""
Artifact Zero — Account & API Key Management
Owns all /api/v1/keys/*, /api/v1/account/*, /api/v1/webhooks/* routes.
Session-aware throughout. No email required in payload — user pulled from session.
"""
import os
import uuid
import json
import hmac
import hashlib
import secrets
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, session

import db as database
from auth import login_required, _user_by_id

account_bp = Blueprint('account', __name__)

SITE_URL = os.getenv("SITE_URL", "https://artifact0.com")

# ─── TIER LIMITS (mirrors app.py) ───────────────────────────────────────────
_TIER_LIMITS = {
    "free":       {"monthly": 10,     "rpm": 5},
    "starter":    {"monthly": 1000,   "rpm": 30},
    "builder":    {"monthly": 5000,   "rpm": 60},
    "scale":      {"monthly": 10000,  "rpm": 120},
    "enterprise": {"monthly": 100000, "rpm": 300},
}


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_or_create_account(user_id, email):
    """Return account_id for a user. Create personal account if none exists."""
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    # Check if user already has an account
    cur.execute(f"SELECT account_id FROM users WHERE id={p}", (user_id,))
    row = cur.fetchone()
    acct_id = (row[0] if database.USE_PG else row["account_id"]) if row else None

    if not acct_id:
        acct_id = "acct_" + uuid.uuid4().hex[:16]
        name = email.split("@")[0]
        if database.USE_PG:
            cur.execute(
                "INSERT INTO accounts (id, name, owner_user_id) VALUES (%s, %s, %s)",
                (acct_id, name, user_id)
            )
            cur.execute(
                "UPDATE users SET account_id=%s WHERE id=%s",
                (acct_id, user_id)
            )
        else:
            cur.execute(
                "INSERT INTO accounts (id, created_at, name, owner_user_id) VALUES (?, datetime('now'), ?, ?)",
                (acct_id, name, user_id)
            )
            cur.execute(
                "UPDATE users SET account_id=? WHERE id=?",
                (acct_id, user_id)
            )
        conn.commit()

    conn.close()
    return acct_id


def _keys_for_user(user_id):
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"
    cur.execute(
        f"SELECT id, name, key_type, tier, monthly_limit, active, created_at, last_used_at, usage_count, revoked_at "
        f"FROM api_keys WHERE owner_user_id={p} ORDER BY created_at DESC",
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()
    cols = ["id", "name", "key_type", "tier", "monthly_limit", "active",
            "created_at", "last_used_at", "usage_count", "revoked_at"]
    result = []
    for row in rows:
        d = dict(zip(cols, row)) if database.USE_PG else dict(row)
        # Mask key — show prefix only
        d["key_preview"] = d["id"][:12] + "..." + d["id"][-4:]
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        d["last_used_at"] = str(d["last_used_at"]) if d["last_used_at"] else None
        d["revoked_at"] = str(d["revoked_at"]) if d["revoked_at"] else None
        result.append(d)
    return result


def _create_key(user_id, account_id, name, tier, key_type):
    key_id = "az_" + secrets.token_hex(24)
    monthly_limit = _TIER_LIMITS.get(tier, _TIER_LIMITS["free"])["monthly"]
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute(
            "INSERT INTO api_keys (id, created_at, owner_email, owner_user_id, account_id, name, key_type, tier, monthly_limit, active) "
            "VALUES (%s, NOW(), (SELECT email FROM users WHERE id=%s), %s, %s, %s, %s, %s, %s, TRUE)",
            (key_id, user_id, user_id, account_id, name, key_type, tier, monthly_limit)
        )
    else:
        cur.execute(
            "INSERT INTO api_keys (id, created_at, owner_email, owner_user_id, account_id, name, key_type, tier, monthly_limit, active) "
            "VALUES (?, datetime('now'), (SELECT email FROM users WHERE id=?), ?, ?, ?, ?, ?, ?, 1)",
            (key_id, user_id, user_id, account_id, name, key_type, tier, monthly_limit)
        )
    conn.commit()
    conn.close()
    return key_id, monthly_limit


def _revoke_key(key_id, user_id):
    """Revoke key only if it belongs to this user."""
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"
    if database.USE_PG:
        cur.execute(
            "UPDATE api_keys SET active=FALSE, revoked_at=NOW() WHERE id=%s AND owner_user_id=%s",
            (key_id, user_id)
        )
    else:
        cur.execute(
            "UPDATE api_keys SET active=0, revoked_at=datetime('now') WHERE id=? AND owner_user_id=?",
            (key_id, user_id)
        )
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def _update_key_last_used(key_id):
    """Called by require_api_key after each successful request."""
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute(
            "UPDATE api_keys SET last_used_at=NOW(), usage_count=usage_count+1 WHERE id=%s",
            (key_id,)
        )
    else:
        cur.execute(
            "UPDATE api_keys SET last_used_at=datetime('now'), usage_count=usage_count+1 WHERE id=?",
            (key_id,)
        )
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════
# API KEY ROUTES
# ═══════════════════════════════════════════════════════════════════

@account_bp.route("/api/v1/keys", methods=["GET"])
@login_required
def list_keys():
    user = request._user
    keys = _keys_for_user(user["id"])
    return jsonify({"keys": keys, "count": len(keys)})


@account_bp.route("/api/v1/keys", methods=["POST"])
@login_required
def create_key():
    user = request._user
    data = request.get_json() or {}

    name = (data.get("name") or "Default Key").strip()[:64]
    tier = data.get("tier", "free").strip().lower()
    key_type = data.get("key_type", "live").strip().lower()

    if tier not in _TIER_LIMITS:
        return jsonify({"error": f"Invalid tier. Options: {list(_TIER_LIMITS.keys())}"}), 400
    if key_type not in ("test", "live"):
        return jsonify({"error": "key_type must be 'test' or 'live'"}), 400

    account_id = _get_or_create_account(user["id"], user["email"])
    key_id, monthly_limit = _create_key(user["id"], account_id, name, tier, key_type)

    return jsonify({
        "api_key": key_id,
        "key_preview": key_id[:12] + "..." + key_id[-4:],
        "name": name,
        "tier": tier,
        "key_type": key_type,
        "monthly_limit": monthly_limit,
        "message": "Store this key securely. It will not be shown again."
    }), 201


@account_bp.route("/api/v1/keys/revoke", methods=["POST"])
@login_required
def revoke_key():
    user = request._user
    data = request.get_json() or {}
    key_id = (data.get("key_id") or "").strip()

    if not key_id:
        return jsonify({"error": "key_id required"}), 400

    ok = _revoke_key(key_id, user["id"])
    if not ok:
        return jsonify({"error": "Key not found or not owned by you"}), 404

    return jsonify({"revoked": True, "key_id": key_id})


@account_bp.route("/api/v1/keys/<key_id>/usage", methods=["GET"])
@login_required
def key_usage(key_id):
    user = request._user
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    # Verify ownership
    cur.execute(f"SELECT id, tier, usage_count, last_used_at FROM api_keys WHERE id={p} AND owner_user_id={p}",
                (key_id, user["id"]))
    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Key not found"}), 404

    cols = ["id", "tier", "usage_count", "last_used_at"]
    key = dict(zip(cols, row)) if database.USE_PG else dict(row)

    # Usage breakdown — last 30 days
    if database.USE_PG:
        cur.execute(
            "SELECT endpoint, COUNT(*) as cnt, AVG(latency_ms) as avg_ms "
            "FROM api_usage WHERE api_key_id=%s AND created_at >= NOW() - INTERVAL '30 days' "
            "GROUP BY endpoint ORDER BY cnt DESC",
            (key_id,)
        )
    else:
        cur.execute(
            "SELECT endpoint, COUNT(*) as cnt, AVG(latency_ms) as avg_ms "
            "FROM api_usage WHERE api_key_id=? AND created_at >= datetime('now', '-30 days') "
            "GROUP BY endpoint ORDER BY cnt DESC",
            (key_id,)
        )
    usage_rows = cur.fetchall()
    conn.close()

    breakdown = [
        {"endpoint": r[0] if database.USE_PG else r["endpoint"],
         "count": r[1] if database.USE_PG else r["cnt"],
         "avg_latency_ms": round(r[2] or 0, 1) if database.USE_PG else round(r["avg_ms"] or 0, 1)}
        for r in usage_rows
    ]

    return jsonify({
        "key_id": key_id,
        "tier": key["tier"],
        "total_usage": key["usage_count"],
        "last_used_at": str(key["last_used_at"]) if key["last_used_at"] else None,
        "last_30_days": breakdown
    })


# ═══════════════════════════════════════════════════════════════════
# ACCOUNT ROUTES
# ═══════════════════════════════════════════════════════════════════

@account_bp.route("/api/v1/account", methods=["GET"])
@login_required
def get_account():
    user = request._user
    account_id = _get_or_create_account(user["id"], user["email"])

    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    cur.execute(f"SELECT id, name, plan, active, created_at FROM accounts WHERE id={p}", (account_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Account not found"}), 404

    cols = ["id", "name", "plan", "active", "created_at"]
    acct = dict(zip(cols, row)) if database.USE_PG else dict(row)
    acct["created_at"] = str(acct["created_at"]) if acct["created_at"] else None

    return jsonify({
        "account": acct,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "tier": user["tier"],
            "login_count": user.get("login_count", 0),
        }
    })


@account_bp.route("/api/v1/account/login-history", methods=["GET"])
@login_required
def login_history():
    user = request._user
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    if database.USE_PG:
        cur.execute(
            "SELECT id, created_at, ip, user_agent, success FROM login_history "
            "WHERE user_id=%s ORDER BY created_at DESC LIMIT 50",
            (user["id"],)
        )
    else:
        cur.execute(
            "SELECT id, created_at, ip, user_agent, success FROM login_history "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
            (user["id"],)
        )
    rows = cur.fetchall()
    conn.close()

    cols = ["id", "created_at", "ip", "user_agent", "success"]
    history = []
    for row in rows:
        d = dict(zip(cols, row)) if database.USE_PG else dict(row)
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        history.append(d)

    return jsonify({"history": history})


# ═══════════════════════════════════════════════════════════════════
# WEBHOOK ROUTES
# ═══════════════════════════════════════════════════════════════════

@account_bp.route("/api/v1/webhooks", methods=["GET"])
@login_required
def list_webhooks():
    user = request._user
    account_id = _get_or_create_account(user["id"], user["email"])
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    cur.execute(
        f"SELECT id, url, events, active, created_at, last_triggered_at, failure_count "
        f"FROM webhooks WHERE account_id={p} ORDER BY created_at DESC",
        (account_id,)
    )
    rows = cur.fetchall()
    conn.close()

    cols = ["id", "url", "events", "active", "created_at", "last_triggered_at", "failure_count"]
    result = []
    for row in rows:
        d = dict(zip(cols, row)) if database.USE_PG else dict(row)
        d["events"] = json.loads(d["events"]) if isinstance(d["events"], str) else d["events"]
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        d["last_triggered_at"] = str(d["last_triggered_at"]) if d["last_triggered_at"] else None
        result.append(d)

    return jsonify({"webhooks": result})


@account_bp.route("/api/v1/webhooks", methods=["POST"])
@login_required
def create_webhook():
    user = request._user
    data = request.get_json() or {}

    url = (data.get("url") or "").strip()
    events = data.get("events", ["score.completed"])

    if not url or not url.startswith("https://"):
        return jsonify({"error": "url must be a valid https:// URL"}), 400
    if not isinstance(events, list) or not events:
        return jsonify({"error": "events must be a non-empty list"}), 400

    account_id = _get_or_create_account(user["id"], user["email"])
    wh_id = "wh_" + uuid.uuid4().hex[:16]
    secret = "whsec_" + secrets.token_hex(24)
    secret_hash = hashlib.sha256(secret.encode()).hexdigest()
    events_json = json.dumps(events)

    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute(
            "INSERT INTO webhooks (id, account_id, user_id, url, secret_hash, events) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (wh_id, account_id, user["id"], url, secret_hash, events_json)
        )
    else:
        cur.execute(
            "INSERT INTO webhooks (id, created_at, account_id, user_id, url, secret_hash, events) "
            "VALUES (?, datetime('now'), ?, ?, ?, ?, ?)",
            (wh_id, account_id, user["id"], url, secret_hash, events_json)
        )
    conn.commit()
    conn.close()

    return jsonify({
        "webhook_id": wh_id,
        "url": url,
        "events": events,
        "secret": secret,
        "message": "Store this secret securely. It will not be shown again."
    }), 201


@account_bp.route("/api/v1/webhooks/<wh_id>", methods=["DELETE"])
@login_required
def delete_webhook(wh_id):
    user = request._user
    account_id = _get_or_create_account(user["id"], user["email"])
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    cur.execute(
        f"UPDATE webhooks SET active=FALSE WHERE id={p} AND account_id={p}" if database.USE_PG else
        f"UPDATE webhooks SET active=0 WHERE id={p} AND account_id={p}",
        (wh_id, account_id)
    )
    affected = cur.rowcount
    conn.commit()
    conn.close()

    if not affected:
        return jsonify({"error": "Webhook not found"}), 404
    return jsonify({"deleted": True, "webhook_id": wh_id})


@account_bp.route("/api/v1/webhooks/<wh_id>/deliveries", methods=["GET"])
@login_required
def webhook_deliveries(wh_id):
    user = request._user
    account_id = _get_or_create_account(user["id"], user["email"])
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    # Verify ownership
    cur.execute(f"SELECT id FROM webhooks WHERE id={p} AND account_id={p}", (wh_id, account_id))
    if not cur.fetchone():
        conn.close()
        return jsonify({"error": "Webhook not found"}), 404

    cur.execute(
        f"SELECT id, created_at, response_code, latency_ms, success, retry_count "
        f"FROM webhook_deliveries WHERE webhook_id={p} ORDER BY created_at DESC LIMIT 100",
        (wh_id,)
    )
    rows = cur.fetchall()
    conn.close()

    cols = ["id", "created_at", "response_code", "latency_ms", "success", "retry_count"]
    deliveries = []
    for row in rows:
        d = dict(zip(cols, row)) if database.USE_PG else dict(row)
        d["created_at"] = str(d["created_at"]) if d["created_at"] else None
        deliveries.append(d)

    return jsonify({"deliveries": deliveries})


# ═══════════════════════════════════════════════════════════════════
# SPEND ALERTS + AUTO RECHARGE
# ═══════════════════════════════════════════════════════════════════

@account_bp.route("/api/v1/account/spend-alert", methods=["POST"])
@login_required
def set_spend_alert():
    user = request._user
    data = request.get_json() or {}
    account_id = _get_or_create_account(user["id"], user["email"])

    threshold = int(data.get("threshold_dollars", 1) * 100)
    notify_email = data.get("notify_email", user["email"])

    alert_id = "alrt_" + uuid.uuid4().hex[:12]
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    # Upsert — one alert per account
    cur.execute(f"SELECT id FROM spend_alerts WHERE account_id={p}", (account_id,))
    existing = cur.fetchone()

    if existing:
        existing_id = existing[0] if database.USE_PG else existing["id"]
        if database.USE_PG:
            cur.execute(
                "UPDATE spend_alerts SET threshold_cents=%s, notify_email=%s, active=TRUE WHERE id=%s",
                (threshold, notify_email, existing_id)
            )
        else:
            cur.execute(
                "UPDATE spend_alerts SET threshold_cents=?, notify_email=?, active=1 WHERE id=?",
                (threshold, notify_email, existing_id)
            )
    else:
        if database.USE_PG:
            cur.execute(
                "INSERT INTO spend_alerts (id, account_id, threshold_cents, notify_email) VALUES (%s, %s, %s, %s)",
                (alert_id, account_id, threshold, notify_email)
            )
        else:
            cur.execute(
                "INSERT INTO spend_alerts (id, created_at, account_id, threshold_cents, notify_email) VALUES (?, datetime('now'), ?, ?, ?)",
                (alert_id, account_id, threshold, notify_email)
            )

    conn.commit()
    conn.close()
    return jsonify({"spend_alert": {"threshold_dollars": threshold / 100, "notify_email": notify_email}})


@account_bp.route("/api/v1/account/auto-recharge", methods=["POST"])
@login_required
def set_auto_recharge():
    user = request._user
    data = request.get_json() or {}
    account_id = _get_or_create_account(user["id"], user["email"])

    trigger = int(data.get("trigger_dollars", 1) * 100)
    recharge = int(data.get("recharge_dollars", 10) * 100)
    active = bool(data.get("active", True))
    pm_id = data.get("stripe_payment_method_id", "")

    ar_id = "ar_" + uuid.uuid4().hex[:12]
    conn = database.db_connect()
    cur = conn.cursor()
    p = "%s" if database.USE_PG else "?"

    cur.execute(f"SELECT id FROM auto_recharge WHERE account_id={p}", (account_id,))
    existing = cur.fetchone()

    if existing:
        existing_id = existing[0] if database.USE_PG else existing["id"]
        if database.USE_PG:
            cur.execute(
                "UPDATE auto_recharge SET trigger_cents=%s, recharge_cents=%s, active=%s, stripe_payment_method_id=%s WHERE id=%s",
                (trigger, recharge, active, pm_id, existing_id)
            )
        else:
            cur.execute(
                "UPDATE auto_recharge SET trigger_cents=?, recharge_cents=?, active=?, stripe_payment_method_id=? WHERE id=?",
                (trigger, recharge, 1 if active else 0, pm_id, existing_id)
            )
    else:
        if database.USE_PG:
            cur.execute(
                "INSERT INTO auto_recharge (id, account_id, trigger_cents, recharge_cents, active, stripe_payment_method_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (ar_id, account_id, trigger, recharge, active, pm_id)
            )
        else:
            cur.execute(
                "INSERT INTO auto_recharge (id, created_at, account_id, trigger_cents, recharge_cents, active, stripe_payment_method_id) "
                "VALUES (?, datetime('now'), ?, ?, ?, ?, ?)",
                (ar_id, account_id, trigger, recharge, 1 if active else 0, pm_id)
            )

    conn.commit()
    conn.close()
    return jsonify({"auto_recharge": {"trigger_dollars": trigger / 100, "recharge_dollars": recharge / 100, "active": active}})
