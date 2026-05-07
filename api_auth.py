import os
import functools
from datetime import datetime, timezone

from flask import request, jsonify
import db as database

_TIER_LIMITS = {
    "free":       {"monthly": 10,        "rpm": 5},
    "pro":        {"monthly": 500,       "rpm": 30},
    "power":      {"monthly": 2000,      "rpm": 60},
    "unlimited":  {"monthly": 999999999, "rpm": 120},
    "starter":    {"monthly": 10000,     "rpm": 60},
    "core":       {"monthly": 75000,     "rpm": 120},
    "pipeline":   {"monthly": 300000,    "rpm": 300},
    "enterprise": {"monthly": 999999999, "rpm": 1000},
}

_rate_cache = {}


def _month_start():
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _minute_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")
        if not api_key:
            return jsonify({"error": "Missing API key", "hint": "Pass key in X-API-Key header", "docs": "https://artifact0.com/docs"}), 401

        try:
            conn = database.db_connect()
            cur = conn.cursor()
            if database.USE_PG:
                cur.execute("SELECT id, tier, monthly_limit, active, owner_email FROM api_keys WHERE id = %s", (api_key,))
            else:
                cur.execute("SELECT id, tier, monthly_limit, active, owner_email FROM api_keys WHERE id = ?", (api_key,))
            row = cur.fetchone()
            conn.close()
        except Exception as e:
            print(f"[api] Key lookup error: {e}", flush=True)
            return jsonify({"error": "Database error", "detail": str(e)}), 500

        if not row:
            return jsonify({"error": "Invalid API key"}), 401

        key_id = row[0] if database.USE_PG else row["id"]
        tier   = row[1] if database.USE_PG else row["tier"]
        active = row[3] if database.USE_PG else row["active"]

        if not active:
            return jsonify({"error": "API key deactivated"}), 403

        # Credit balance check (replaces monthly limit for paid tiers)
        if tier != "free":
            try:
                from credits import get_user_id_for_api_key, get_balance, COST_PER_SCORE
                owner_user_id = get_user_id_for_api_key(key_id)
                if owner_user_id:
                    bal = get_balance(owner_user_id)
                    cost_cents = int(COST_PER_SCORE["api"] * 100)
                    if bal < cost_cents:
                        return jsonify({
                            "error": "Insufficient balance",
                            "balance": bal / 100,
                            "cost_per_score": cost_cents / 100,
                            "topup": f"{os.getenv('SITE_URL', 'https://artifact0.com')}/dashboard"
                        }), 402
                    request._credit_user_id = owner_user_id
            except ImportError:
                pass

        # Rate limit (per-minute)
        tier_config = _TIER_LIMITS.get(tier, _TIER_LIMITS["free"])
        cache_key   = f"{key_id}:{_minute_key()}"
        current_rpm = _rate_cache.get(cache_key, 0)
        if current_rpm >= tier_config["rpm"]:
            return jsonify({"error": "Rate limit exceeded", "limit": f"{tier_config['rpm']} req/min"}), 429
        _rate_cache[cache_key] = current_rpm + 1

        # Free tier: monthly limits
        if tier == "free":
            usage_count   = database.get_api_usage_count(key_id, _month_start())
            monthly_limit = row[2] if database.USE_PG else row["monthly_limit"]
            if usage_count >= monthly_limit:
                return jsonify({
                    "error": "Free tier limit reached",
                    "usage": usage_count,
                    "limit": monthly_limit,
                    "upgrade": f"{os.getenv('SITE_URL', 'https://artifact0.com')}/dashboard"
                }), 429

        request._api_key_id = key_id
        request._api_tier   = tier

        try:
            from account import _update_key_last_used
            _update_key_last_used(key_id)
        except Exception:
            pass

        return f(*args, **kwargs)
    return wrapper
