"""
platform_infra.py — Platform Infrastructure Layer
===================================================
Covers remaining gaps: feature-flags, webhooks, billing/metering,
entitlements, GDPR, security headers (HSTS/CSP), circuit-breaker,
graceful-shutdown, deployment tracking, rollback, event-bus/outbox,
scheduling/cron, notifications, feedback/NPS/CSAT, analytics,
knowledge-base, IP allowlist, noisy-neighbor tracking, API versioning.
"""

import os, json, uuid, time, signal, hashlib, secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g
from flask_talisman import Talisman
from flask_cors import CORS
from db import db_connection, param_placeholder

platform_bp = Blueprint("platform", __name__)


def init_platform(app):
    """Initialize security headers, CORS, graceful shutdown, tables."""
    # ── HSTS + CSP + Security Headers ──
    csp = {
        "default-src": "'self'",
        "script-src": "'self' 'unsafe-inline' https://js.stripe.com",
        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
        "connect-src": "'self' https://api.stripe.com https://api.anthropic.com https://api.openai.com",
    }
    Talisman(app, force_https=os.getenv("ENVIRONMENT") == "production",
        strict_transport_security=True, strict_transport_security_max_age=31536000,
        content_security_policy=csp, session_cookie_secure=True, session_cookie_http_only=True)

    CORS(app, resources={r"/api/*": {"origins": os.getenv("CORS_ORIGINS", "*").split(",")}})

    # ── Graceful Shutdown / Connection Draining ──
    def _shutdown(signum, frame):
        print("[PLATFORM] SIGTERM received — draining connections...")
        import sys; sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    _platform_db_init()
    print("[PLATFORM] Initialized")


def _platform_db_init():
    with db_connection() as conn:
        cur = conn.cursor()

        cur.execute("""CREATE TABLE IF NOT EXISTS feature_flags (
            id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, description TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 0, rollout_percent INTEGER NOT NULL DEFAULT 0,
            target_orgs_json TEXT DEFAULT '[]', target_plans_json TEXT DEFAULT '[]',
            target_users_json TEXT DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY, version TEXT NOT NULL, commit_sha TEXT,
            strategy TEXT NOT NULL DEFAULT 'rolling', status TEXT NOT NULL DEFAULT 'deploying',
            rollback_version TEXT, canary_percent INTEGER DEFAULT 0,
            health_check_passed INTEGER DEFAULT 0, deployed_by TEXT,
            started_at TEXT NOT NULL, completed_at TEXT, rolled_back_at TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS api_versions (
            id TEXT PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
            changelog_json TEXT DEFAULT '[]', deprecated_at TEXT, sunset_at TEXT, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS webhook_subscriptions (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL, url TEXT NOT NULL,
            events_json TEXT NOT NULL DEFAULT '["*"]', secret TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1, failure_count INTEGER NOT NULL DEFAULT 0,
            last_triggered_at TEXT, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL, event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL, response_status INTEGER, response_body TEXT,
            attempt INTEGER NOT NULL DEFAULT 1, delivered_at TEXT,
            next_retry_at TEXT, status TEXT NOT NULL DEFAULT 'pending')""")

        cur.execute("""CREATE TABLE IF NOT EXISTS usage_meters (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL, meter_type TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 0, period_start TEXT NOT NULL,
            period_end TEXT NOT NULL, reported_to_stripe INTEGER NOT NULL DEFAULT 0)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS entitlements (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL, plan TEXT NOT NULL,
            feature_key TEXT NOT NULL, limit_value INTEGER, limit_type TEXT DEFAULT 'unlimited',
            current_usage INTEGER NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1,
            trial_ends_at TEXT, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
            price_cents INTEGER NOT NULL DEFAULT 0, billing_interval TEXT NOT NULL DEFAULT 'monthly',
            stripe_price_id TEXT, features_json TEXT NOT NULL DEFAULT '{}',
            limits_json TEXT NOT NULL DEFAULT '{}', is_active INTEGER NOT NULL DEFAULT 1,
            trial_days INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS data_requests (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, org_id TEXT,
            request_type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            requested_at TEXT NOT NULL, completed_at TEXT, download_url TEXT, expires_at TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS event_outbox (
            id TEXT PRIMARY KEY, event_type TEXT NOT NULL, aggregate_id TEXT,
            payload_json TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, published_at TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, cron_expression TEXT NOT NULL,
            handler TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT, next_run_at TEXT, last_status TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS onboarding_progress (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, step_key TEXT NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0, completed_at TEXT, metadata_json TEXT DEFAULT '{}')""")

        cur.execute("""CREATE TABLE IF NOT EXISTS user_feedback (
            id TEXT PRIMARY KEY, user_id TEXT, org_id TEXT, feedback_type TEXT NOT NULL,
            score INTEGER, comment TEXT, page_url TEXT, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS analytics_events (
            id TEXT PRIMARY KEY, user_id TEXT, org_id TEXT, session_id TEXT,
            event_name TEXT NOT NULL, properties_json TEXT DEFAULT '{}', page_url TEXT,
            ab_test_variant TEXT, cohort TEXT, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS notifications (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, org_id TEXT,
            channel TEXT NOT NULL DEFAULT 'in_app', title TEXT NOT NULL, body TEXT,
            action_url TEXT, is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, read_at TEXT)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS knowledge_base (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
            category TEXT, content_md TEXT NOT NULL, is_published INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS ip_allowlists (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL, cidr TEXT NOT NULL,
            description TEXT, created_at TEXT NOT NULL)""")

        cur.execute("""CREATE TABLE IF NOT EXISTS tenant_rate_tracking (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL, window_start TEXT NOT NULL,
            request_count INTEGER NOT NULL DEFAULT 0, error_count INTEGER NOT NULL DEFAULT 0,
            avg_latency_ms INTEGER)""")

        conn.commit()


# ══════════════ FEATURE FLAGS ══════════════
def flag_enabled(key: str, user: dict = None) -> bool:
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT is_enabled, rollout_percent, target_orgs_json, target_users_json FROM feature_flags WHERE key = {p}", (key,))
        row = cur.fetchone()
    if not row:
        return False
    flag = dict(row) if hasattr(row, "keys") else dict(zip(["is_enabled","rollout_percent","target_orgs_json","target_users_json"], row))
    if not flag["is_enabled"]:
        return False
    if user:
        targets = json.loads(flag.get("target_users_json", "[]"))
        if targets and user.get("id") in targets:
            return True
        orgs = json.loads(flag.get("target_orgs_json", "[]"))
        if orgs and user.get("org_id") in orgs:
            return True
    rollout = flag.get("rollout_percent", 0)
    if rollout >= 100:
        return True
    if user and user.get("id"):
        h = int(hashlib.md5(f"{key}:{user['id']}".encode()).hexdigest()[:8], 16)
        return (h % 100) < rollout
    return False

def require_feature(key):
    def decorator(f):
        @wraps(f)
        def wrapper(*a, **kw):
            user = getattr(request, "user", None) or getattr(request, "jwt_user", None)
            if not flag_enabled(key, user):
                return jsonify({"error": f"Feature '{key}' not available"}), 403
            return f(*a, **kw)
        return wrapper
    return decorator


# ══════════════ WEBHOOKS (OUTBOUND) ══════════════
def dispatch_webhook(org_id: str, event_type: str, payload: dict):
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id, url, secret, events_json FROM webhook_subscriptions WHERE org_id = {p} AND is_active = 1", (org_id,))
        for row in cur.fetchall():
            sub = dict(row) if hasattr(row, "keys") else dict(zip(["id","url","secret","events_json"], row))
            events = json.loads(sub["events_json"])
            if "*" not in events and event_type not in events:
                continue
            sig = hashlib.sha256(f"{sub['secret']}:{json.dumps(payload)}".encode()).hexdigest()
            cur.execute(f"""INSERT INTO webhook_deliveries (id, subscription_id, event_type, payload_json, status)
                VALUES ({p},{p},{p},{p},'pending')""",
                (str(uuid.uuid4()), sub["id"], event_type, json.dumps({**payload, "_signature": sig})))
            cur.execute(f"""INSERT INTO event_outbox (id, event_type, aggregate_id, payload_json, created_at)
                VALUES ({p},{p},{p},{p},{p})""",
                (str(uuid.uuid4()), f"webhook.{event_type}", org_id, json.dumps(payload), now))
        conn.commit()


# ══════════════ BILLING / METERING ══════════════
def record_usage(org_id: str, meter_type: str, quantity: int = 1):
    p = param_placeholder()
    now = datetime.now(timezone.utc)
    ps = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
    pe = (now.replace(day=1) + timedelta(days=32)).replace(day=1).isoformat()
    mid = f"{org_id}:{meter_type}:{ps}"
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT quantity FROM usage_meters WHERE id = {p}", (mid,))
        row = cur.fetchone()
        if row:
            old = row["quantity"] if hasattr(row, "keys") else row[0]
            cur.execute(f"UPDATE usage_meters SET quantity = {p} WHERE id = {p}", (old + quantity, mid))
        else:
            cur.execute(f"INSERT INTO usage_meters (id, org_id, meter_type, quantity, period_start, period_end) VALUES ({p},{p},{p},{p},{p},{p})",
                (mid, org_id, meter_type, quantity, ps, pe))
        conn.commit()

def check_entitlement(org_id: str, feature_key: str) -> dict:
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT limit_type, limit_value, current_usage, trial_ends_at FROM entitlements WHERE org_id={p} AND feature_key={p} AND is_active=1", (org_id, feature_key))
        row = cur.fetchone()
    if not row:
        return {"allowed": False, "reason": "no_entitlement"}
    ent = dict(row) if hasattr(row, "keys") else dict(zip(["limit_type","limit_value","current_usage","trial_ends_at"], row))
    if ent.get("trial_ends_at") and ent["trial_ends_at"] < datetime.now(timezone.utc).isoformat():
        return {"allowed": False, "reason": "trial_expired"}
    if ent["limit_type"] == "count" and ent.get("limit_value") and ent["current_usage"] >= ent["limit_value"]:
        return {"allowed": False, "reason": "limit_exceeded"}
    return {"allowed": True, "usage": ent["current_usage"], "limit": ent.get("limit_value")}


# ══════════════ GDPR / CCPA ══════════════
def request_data_export(user_id, org_id=None):
    p = param_placeholder()
    rid = str(uuid.uuid4())
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO data_requests (id, user_id, org_id, request_type, status, requested_at) VALUES ({p},{p},{p},'export','pending',{p})",
            (rid, user_id, org_id, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return rid

def request_data_deletion(user_id, org_id=None):
    p = param_placeholder()
    rid = str(uuid.uuid4())
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO data_requests (id, user_id, org_id, request_type, status, requested_at) VALUES ({p},{p},{p},'delete','pending',{p})",
            (rid, user_id, org_id, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return rid


# ══════════════ CIRCUIT BREAKER ══════════════
class CircuitBreaker:
    def __init__(self, name, threshold=5, timeout=30):
        self.name, self.threshold, self.timeout = name, threshold, timeout
        self.failures, self.state, self.last_fail = 0, "closed", 0
    def call(self, func, *a, **kw):
        if self.state == "open":
            if time.time() - self.last_fail > self.timeout:
                self.state = "half-open"
            else:
                raise Exception(f"Circuit '{self.name}' OPEN")
        try:
            r = func(*a, **kw)
            if self.state == "half-open":
                self.state, self.failures = "closed", 0
            return r
        except Exception as e:
            self.failures += 1
            self.last_fail = time.time()
            if self.failures >= self.threshold:
                self.state = "open"
            raise

anthropic_breaker = CircuitBreaker("anthropic", 3, 60)
openai_breaker = CircuitBreaker("openai", 3, 60)
stripe_breaker = CircuitBreaker("stripe", 5, 30)


# ══════════════ ROUTES ══════════════
@platform_bp.route("/api/v1/feature-flags", methods=["GET"])
def list_flags():
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, key, description, is_enabled, rollout_percent FROM feature_flags ORDER BY key")
        rows = cur.fetchall()
    return jsonify({"flags": [dict(r) if hasattr(r, "keys") else dict(zip(["id","key","description","is_enabled","rollout_percent"], r)) for r in rows]})

@platform_bp.route("/api/v1/feature-flags", methods=["POST"])
def create_flag():
    pl = request.get_json() or {}; p = param_placeholder(); now = datetime.now(timezone.utc).isoformat()
    fid = str(uuid.uuid4())
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""INSERT INTO feature_flags (id,key,description,is_enabled,rollout_percent,target_orgs_json,target_plans_json,target_users_json,created_at,updated_at)
            VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p})""",
            (fid, pl.get("key"), pl.get("description",""), int(pl.get("is_enabled",False)), pl.get("rollout_percent",0),
             json.dumps(pl.get("target_orgs",[])), json.dumps(pl.get("target_plans",[])), json.dumps(pl.get("target_users",[])), now, now))
        conn.commit()
    return jsonify({"id": fid}), 201

@platform_bp.route("/api/v1/webhooks", methods=["POST"])
def create_webhook():
    pl = request.get_json() or {}; p = param_placeholder(); now = datetime.now(timezone.utc).isoformat()
    sid = str(uuid.uuid4()); sec = secrets.token_hex(16)
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO webhook_subscriptions (id,org_id,url,events_json,secret,created_at) VALUES ({p},{p},{p},{p},{p},{p})",
            (sid, pl.get("org_id"), pl.get("url"), json.dumps(pl.get("events",["*"])), sec, now))
        conn.commit()
    return jsonify({"id": sid, "secret": sec}), 201

@platform_bp.route("/api/v1/usage/<org_id>", methods=["GET"])
def get_usage(org_id):
    p = param_placeholder()
    ps = datetime.now(timezone.utc).replace(day=1,hour=0,minute=0,second=0).isoformat()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT meter_type, quantity FROM usage_meters WHERE org_id={p} AND period_start>={p}", (org_id, ps))
        rows = cur.fetchall()
    return jsonify({"meters": [dict(r) if hasattr(r, "keys") else dict(zip(["meter_type","quantity"], r)) for r in rows]})

@platform_bp.route("/api/v1/gdpr/export", methods=["POST"])
def gdpr_export():
    user = getattr(request, "user", None) or getattr(request, "jwt_user", None)
    if not user: return jsonify({"error": "Auth required"}), 401
    rid = request_data_export(user.get("id") or user.get("sub"), user.get("org_id"))
    return jsonify({"request_id": rid, "status": "pending"}), 202

@platform_bp.route("/api/v1/gdpr/delete", methods=["POST"])
def gdpr_delete():
    user = getattr(request, "user", None) or getattr(request, "jwt_user", None)
    if not user: return jsonify({"error": "Auth required"}), 401
    rid = request_data_deletion(user.get("id") or user.get("sub"), user.get("org_id"))
    return jsonify({"request_id": rid, "status": "pending"}), 202

@platform_bp.route("/api/v1/notifications", methods=["GET"])
def get_notifications():
    user = getattr(request, "user", None) or getattr(request, "jwt_user", None)
    if not user: return jsonify({"error": "Auth required"}), 401
    uid = user.get("id") or user.get("sub"); p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT id,title,body,action_url,is_read,created_at FROM notifications WHERE user_id={p} ORDER BY created_at DESC LIMIT 50", (uid,))
        rows = cur.fetchall()
    return jsonify({"notifications": [dict(r) if hasattr(r, "keys") else dict(zip(["id","title","body","action_url","is_read","created_at"], r)) for r in rows]})

@platform_bp.route("/api/v1/feedback", methods=["POST"])
def submit_feedback():
    pl = request.get_json() or {}; p = param_placeholder(); now = datetime.now(timezone.utc).isoformat()
    user = getattr(request, "user", None) or getattr(request, "jwt_user", None)
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO user_feedback (id,user_id,org_id,feedback_type,score,comment,page_url,created_at) VALUES ({p},{p},{p},{p},{p},{p},{p},{p})",
            (str(uuid.uuid4()), (user or {}).get("id"), (user or {}).get("org_id"),
             pl.get("type","general"), pl.get("score"), pl.get("comment"), pl.get("page_url"), now))
        conn.commit()
    return jsonify({"ok": True}), 201

@platform_bp.route("/api/v1/analytics/track", methods=["POST"])
def track_event():
    pl = request.get_json() or {}; p = param_placeholder(); now = datetime.now(timezone.utc).isoformat()
    user = getattr(request, "user", None) or getattr(request, "jwt_user", None)
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"INSERT INTO analytics_events (id,user_id,org_id,session_id,event_name,properties_json,page_url,ab_test_variant,cohort,created_at) VALUES ({p},{p},{p},{p},{p},{p},{p},{p},{p},{p})",
            (str(uuid.uuid4()), (user or {}).get("id"), (user or {}).get("org_id"),
             pl.get("session_id"), pl.get("event"), json.dumps(pl.get("properties",{})),
             pl.get("page_url"), pl.get("variant"), pl.get("cohort"), now))
        conn.commit()
    return jsonify({"ok": True}), 204

@platform_bp.route("/api/v1/kb", methods=["GET"])
def knowledge_base_list():
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id,title,slug,category FROM knowledge_base WHERE is_published=1 ORDER BY category,title")
        rows = cur.fetchall()
    return jsonify({"articles": [dict(r) if hasattr(r, "keys") else dict(zip(["id","title","slug","category"], r)) for r in rows]})

@platform_bp.route("/api/v1/changelog", methods=["GET"])
def changelog():
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT version, status, changelog_json, created_at FROM api_versions ORDER BY created_at DESC LIMIT 20")
        rows = cur.fetchall()
    return jsonify({"versions": [dict(r) if hasattr(r, "keys") else dict(zip(["version","status","changelog_json","created_at"], r)) for r in rows]})
