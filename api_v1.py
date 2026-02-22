"""
Artifact Zero — Public API v1
POST /api/v1/score — API key auth, usage metering, rate limiting
"""
import os
import uuid
import time
import json
import secrets
import functools
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

import db as database

api_v1 = Blueprint("api_v1", __name__)

# ═══════════════════════════════════════
# AUTH & RATE LIMITING
# ═══════════════════════════════════════

TIER_LIMITS = {
    "free": {"monthly": 10, "rpm": 5},
    "pro": {"monthly": 500, "rpm": 30},
    "power": {"monthly": 2000, "rpm": 60},
    "unlimited": {"monthly": 999999999, "rpm": 120},
    "starter": {"monthly": 10000, "rpm": 60},
    "core": {"monthly": 75000, "rpm": 120},
    "pipeline": {"monthly": 300000, "rpm": 300},
    "enterprise": {"monthly": 999999999, "rpm": 1000},
}

# In-memory rate limiter (per-key, per-minute)
_rate_cache = {}


def _month_start():
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _minute_key():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M")


def validate_api_key(f):
    """Decorator: validates API key from X-API-Key header or api_key param."""
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.headers.get("X-API-Key") or request.args.get("api_key")

        if not api_key:
            return jsonify({
                "error": "Missing API key",
                "hint": "Pass your key in the X-API-Key header or api_key query parameter",
                "docs": "https://artifact0.com/docs"
            }), 401

        # Look up key
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute("SELECT id, tier, monthly_limit, active, owner_email FROM api_keys WHERE id = %s", (api_key,))
        else:
            cur.execute("SELECT id, tier, monthly_limit, active, owner_email FROM api_keys WHERE id = ?", (api_key,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return jsonify({"error": "Invalid API key"}), 401

        if database.USE_PG:
            key_id, tier, monthly_limit, active, owner = row[0], row[1], row[2], row[3], row[4]
        else:
            key_id, tier, monthly_limit, active, owner = row["id"], row["tier"], row["monthly_limit"], row["active"], row["owner_email"]

        if not active:
            return jsonify({"error": "API key is deactivated"}), 403

        # Check monthly usage
        usage_count = database.get_api_usage_count(key_id, _month_start())
        if usage_count >= monthly_limit:
            return jsonify({
                "error": "Monthly limit reached",
                "usage": usage_count,
                "limit": monthly_limit,
                "tier": tier,
                "upgrade": "https://artifact0.com/pricing"
            }), 429

        # Check rate limit (requests per minute)
        tier_config = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
        rpm_limit = tier_config["rpm"]
        cache_key = f"{key_id}:{_minute_key()}"
        current_rpm = _rate_cache.get(cache_key, 0)
        if current_rpm >= rpm_limit:
            return jsonify({
                "error": "Rate limit exceeded",
                "limit": f"{rpm_limit} requests/minute",
                "tier": tier
            }), 429
        _rate_cache[cache_key] = current_rpm + 1

        # Clean old rate cache entries (keep last 5 minutes)
        now_min = _minute_key()
        stale = [k for k in _rate_cache if k.split(":")[1] < now_min and k != cache_key]
        for k in stale[:50]:
            _rate_cache.pop(k, None)

        # Inject key info into request context
        request._api_key_id = key_id
        request._api_tier = tier
        request._api_usage = usage_count + 1
        request._api_limit = monthly_limit

        return f(*args, **kwargs)
    return wrapper


# ═══════════════════════════════════════
# SCORING ENDPOINT
# ═══════════════════════════════════════

@api_v1.route("/api/v1/score", methods=["POST"])
@validate_api_key
def api_score():
    """Score text through the NTI engine. Returns structural analysis."""
    t0 = time.time()
    payload = request.get_json() or {}
    text = payload.get("text", "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' field in request body"}), 400

    if len(text) > 50000:
        return jsonify({"error": "Text exceeds 50,000 character limit"}), 400

    # Import scoring functions from app (avoid circular import)
    from app import (
        detect_l0_constraints, objective_extract, objective_drift,
        detect_l2_framing, classify_tilt, detect_udds, detect_dce,
        detect_cca, detect_downstream_before_constraint, compute_nii,
        NTI_VERSION
    )

    # Run the engine
    l0 = detect_l0_constraints(text)
    obj = objective_extract(text)
    drift = objective_drift("", text)
    framing = detect_l2_framing(text)
    tilt = classify_tilt(text)
    udds = detect_udds("", text, l0)
    dce = detect_dce(text, l0)
    cca = detect_cca("", text)
    dbc = detect_downstream_before_constraint("", text, l0)
    nii = compute_nii("", text, l0, dbc, tilt)

    dominance = []
    if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]:
        dominance.append("CCA")
    if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]:
        dominance.append("UDDS")
    if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]:
        dominance.append("DCE")
    if not dominance:
        dominance = ["NONE"]

    latency_ms = int((time.time() - t0) * 1000)

    # Record usage
    usage_id = str(uuid.uuid4())
    database.record_api_usage(usage_id, request._api_key_id, "/api/v1/score", latency_ms, 200)

    result = {
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {
                "q1_objective": nii.get("q1"),
                "q2_constraints": nii.get("q2"),
                "q3_structural": nii.get("q3"),
                "q4_drift": nii.get("q4"),
            }
        },
        "failure_modes": {
            "UDDS": udds["udds_state"],
            "DCE": dce["dce_state"],
            "CCA": cca["cca_state"],
            "dominance": dominance
        },
        "tilt": {
            "tags": tilt.get("tags", []),
            "count": tilt.get("count", 0)
        },
        "framing": framing,
        "meta": {
            "latency_ms": latency_ms,
            "text_length": len(text),
            "word_count": len(text.split()),
            "tier": request._api_tier,
            "usage_this_month": request._api_usage,
            "monthly_limit": request._api_limit
        }
    }

    return jsonify(result)


# ═══════════════════════════════════════
# KEY MANAGEMENT
# ═══════════════════════════════════════

@api_v1.route("/api/v1/keys", methods=["POST"])
def create_api_key():
    """Create a new API key. Requires email."""
    payload = request.get_json() or {}
    email = payload.get("email", "").strip().lower()
    tier = payload.get("tier", "free").strip().lower()

    if not email or "@" not in email:
        return jsonify({"error": "Valid email required"}), 400

    if tier not in TIER_LIMITS:
        return jsonify({"error": f"Invalid tier. Options: {list(TIER_LIMITS.keys())}"}), 400

    key_id = f"az_{secrets.token_hex(24)}"
    monthly_limit = TIER_LIMITS[tier]["monthly"]
    now = datetime.now(timezone.utc).isoformat()

    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("""
            INSERT INTO api_keys (id, created_at, owner_email, tier, monthly_limit, active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
        """, (key_id, now, email, tier, monthly_limit))
    else:
        cur.execute("""
            INSERT INTO api_keys (id, created_at, owner_email, tier, monthly_limit, active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (key_id, now, email, tier, monthly_limit))
    conn.commit()
    conn.close()

    return jsonify({
        "api_key": key_id,
        "tier": tier,
        "monthly_limit": monthly_limit,
        "email": email,
        "message": "Store this key securely. It will not be shown again."
    }), 201


@api_v1.route("/api/v1/keys/usage", methods=["GET"])
@validate_api_key
def get_usage():
    """Get current usage for the authenticated API key."""
    usage_count = database.get_api_usage_count(request._api_key_id, _month_start())
    return jsonify({
        "api_key": request._api_key_id[:8] + "...",
        "tier": request._api_tier,
        "usage_this_month": usage_count,
        "monthly_limit": request._api_limit,
        "remaining": max(0, request._api_limit - usage_count)
    })
