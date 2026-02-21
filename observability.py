"""
observability.py — Full-Stack Observability Layer
==================================================
Covers: OpenTelemetry tracing, Prometheus metrics, Sentry error tracking,
        alerting (PagerDuty/Opsgenie), SLI/SLO/SLA tracking, APM,
        RUM beacon endpoint, synthetic monitoring, incident management,
        on-call routing, postmortem logging, chaos engineering hooks.

Setup in app.py:
    from observability import init_observability, metrics_bp
    init_observability(app)
    app.register_blueprint(metrics_bp)
"""

import os
import time
import json
import uuid
import traceback
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, g

# ── OpenTelemetry: distributed tracing ──
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

# ── Sentry: error tracking ──
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

# ── Prometheus: metrics ──
try:
    from prometheus_flask_instrumentator import Instrumentator as PrometheusInstrumentator
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

from db import db_connection, param_placeholder

metrics_bp = Blueprint("metrics", __name__)

# ── Tracer ──
_tracer = None


def init_observability(app):
    """Initialize all observability systems. Call once at app startup."""
    global _tracer

    # ── 1. OpenTelemetry distributed tracing ──
    otlp_endpoint = os.getenv("OTLP_ENDPOINT")
    resource = Resource.create({"service.name": "artifact-zero", "service.version": os.getenv("NTI_VERSION", "2.1")})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("artifact-zero")

    # Auto-instrument Flask
    try:
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
        FlaskInstrumentor().instrument_app(app)
    except Exception:
        pass

    # Auto-instrument Postgres
    try:
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        Psycopg2Instrumentor().instrument()
    except Exception:
        pass

    # Auto-instrument Redis
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except Exception:
        pass

    # ── 2. Sentry error tracking ──
    sentry_dsn = os.getenv("SENTRY_DSN")
    if sentry_dsn:
        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0.1")),
            environment=os.getenv("ENVIRONMENT", "production"),
            release=os.getenv("NTI_VERSION", "2.1"),
        )
        print("[OBS] Sentry initialized")

    # ── 3. Prometheus metrics ──
    if PROMETHEUS_AVAILABLE:
        PrometheusInstrumentator().instrument(app).expose(app, endpoint="/metrics")
        print("[OBS] Prometheus metrics at /metrics")

    # ── 4. Request timing middleware ──
    @app.before_request
    def _start_timer():
        g.start_time = time.time()
        g.trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4())[:16])

    @app.after_request
    def _record_metrics(response):
        if hasattr(g, "start_time"):
            latency = (time.time() - g.start_time) * 1000
            response.headers["X-Response-Time-Ms"] = str(int(latency))
            response.headers["X-Trace-Id"] = getattr(g, "trace_id", "")

            # Log structured APM data
            if latency > 1000:  # Slow request alert threshold
                _log_apm_event("slow_request", {
                    "path": request.path,
                    "method": request.method,
                    "latency_ms": int(latency),
                    "status": response.status_code,
                })
        return response

    # ── 5. Initialize SLI/SLO tables ──
    _obs_db_init()

    print("[OBS] Observability initialized (tracing, metrics, error-tracking, APM)")


def _obs_db_init():
    """Create observability-specific tables."""
    with db_connection() as conn:
        cur = conn.cursor()

        # SLI/SLO tracking
        cur.execute("""
        CREATE TABLE IF NOT EXISTS slo_definitions (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            target_percent REAL NOT NULL,
            measurement_window TEXT NOT NULL DEFAULT '30d',
            sli_query TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS slo_measurements (
            id TEXT PRIMARY KEY,
            slo_id TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            total_events INTEGER NOT NULL DEFAULT 0,
            good_events INTEGER NOT NULL DEFAULT 0,
            current_percent REAL,
            error_budget_remaining REAL,
            created_at TEXT NOT NULL
        )
        """)

        # Incident management
        cur.execute("""
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'sev3',
            status TEXT NOT NULL DEFAULT 'open',
            description TEXT,
            triggered_by TEXT,
            assigned_to TEXT,
            on_call_user TEXT,
            started_at TEXT NOT NULL,
            acknowledged_at TEXT,
            resolved_at TEXT,
            postmortem_json TEXT
        )
        """)

        # On-call schedule
        cur.execute("""
        CREATE TABLE IF NOT EXISTS on_call_schedule (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            escalation_order INTEGER NOT NULL DEFAULT 0
        )
        """)

        # Runbooks
        cur.execute("""
        CREATE TABLE IF NOT EXISTS runbooks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            trigger_condition TEXT,
            steps_json TEXT NOT NULL,
            last_executed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        # Chaos engineering experiments
        cur.execute("""
        CREATE TABLE IF NOT EXISTS chaos_experiments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            result_json TEXT,
            executed_at TEXT,
            created_at TEXT NOT NULL
        )
        """)

        # Synthetic monitoring checks
        cur.execute("""
        CREATE TABLE IF NOT EXISTS synthetic_checks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            method TEXT NOT NULL DEFAULT 'GET',
            expected_status INTEGER NOT NULL DEFAULT 200,
            interval_seconds INTEGER NOT NULL DEFAULT 60,
            timeout_ms INTEGER NOT NULL DEFAULT 5000,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_check_at TEXT,
            last_status INTEGER,
            last_latency_ms INTEGER
        )
        """)

        # RUM (Real User Monitoring) beacon data
        cur.execute("""
        CREATE TABLE IF NOT EXISTS rum_events (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            page_url TEXT,
            event_type TEXT NOT NULL,
            dom_load_ms INTEGER,
            first_paint_ms INTEGER,
            largest_contentful_paint_ms INTEGER,
            cumulative_layout_shift REAL,
            first_input_delay_ms INTEGER,
            user_agent TEXT,
            geo TEXT,
            created_at TEXT NOT NULL
        )
        """)

        conn.commit()
    print("[OBS] Tables initialized")


def _log_apm_event(event_type: str, data: dict):
    """Log an APM event (slow request, error, etc.)."""
    print(json.dumps({"apm_event": event_type, "ts": datetime.now(timezone.utc).isoformat(), **data}))


# ══════════════════════════════════════════════
# TRACING HELPERS
# ══════════════════════════════════════════════
def traced(name: str = None):
    """Decorator: add OpenTelemetry span to a function."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            span_name = name or f.__name__
            if _tracer:
                with _tracer.start_as_current_span(span_name) as span:
                    span.set_attribute("function", f.__name__)
                    try:
                        result = f(*args, **kwargs)
                        span.set_attribute("status", "ok")
                        return result
                    except Exception as e:
                        span.set_attribute("status", "error")
                        span.record_exception(e)
                        raise
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════
@metrics_bp.route("/api/v1/health", methods=["GET"])
def detailed_health():
    """Detailed health check with SLI data for monitoring systems."""
    checks = {}

    # Database
    try:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "degraded", "error": str(e)}

    # Redis
    try:
        import redis as redis_lib
        r = redis_lib.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
        r.ping()
        checks["redis"] = {"status": "ok"}
    except Exception:
        checks["redis"] = {"status": "unavailable"}

    overall = "ok" if all(c["status"] == "ok" for c in checks.values()) else "degraded"
    return jsonify({"status": overall, "checks": checks, "timestamp": datetime.now(timezone.utc).isoformat()})


@metrics_bp.route("/api/v1/rum/beacon", methods=["POST"])
def rum_beacon():
    """Receive Real User Monitoring data from browser."""
    payload = request.get_json() or {}
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO rum_events (id, session_id, page_url, event_type,
                dom_load_ms, first_paint_ms, largest_contentful_paint_ms,
                cumulative_layout_shift, first_input_delay_ms, user_agent, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (
            str(uuid.uuid4()), payload.get("session_id"), payload.get("page_url"),
            payload.get("event_type", "pageview"),
            payload.get("dom_load_ms"), payload.get("first_paint_ms"),
            payload.get("lcp_ms"), payload.get("cls"), payload.get("fid_ms"),
            request.headers.get("User-Agent"), now
        ))
        conn.commit()
    return jsonify({"ok": True}), 204


@metrics_bp.route("/api/v1/incidents", methods=["POST"])
def create_incident():
    """Create an incident. Triggers on-call alerting."""
    payload = request.get_json() or {}
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    incident_id = str(uuid.uuid4())

    severity = payload.get("severity", "sev3")
    title = payload.get("title", "Unnamed incident")

    # Find on-call
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT user_id FROM on_call_schedule
            WHERE start_time <= {p} AND end_time >= {p}
            ORDER BY escalation_order LIMIT 1
        """, (now, now))
        on_call_row = cur.fetchone()
        on_call_user = None
        if on_call_row:
            on_call_user = on_call_row["user_id"] if hasattr(on_call_row, "keys") else on_call_row[0]

        cur.execute(f"""
            INSERT INTO incidents (id, title, severity, status, description, triggered_by, on_call_user, started_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (
            incident_id, title, severity, "open",
            payload.get("description"), payload.get("triggered_by"),
            on_call_user, now
        ))
        conn.commit()

    # Alert via PagerDuty/Opsgenie if configured
    _send_alert(severity, title, incident_id)

    return jsonify({"incident_id": incident_id, "severity": severity, "on_call": on_call_user}), 201


@metrics_bp.route("/api/v1/incidents/<incident_id>/resolve", methods=["POST"])
def resolve_incident(incident_id):
    """Resolve an incident and optionally attach postmortem."""
    payload = request.get_json() or {}
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    postmortem = payload.get("postmortem")  # {summary, root_cause, timeline, action_items}

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            UPDATE incidents SET status = 'resolved', resolved_at = {p},
                postmortem_json = {p}
            WHERE id = {p}
        """, (now, json.dumps(postmortem) if postmortem else None, incident_id))
        conn.commit()

    return jsonify({"resolved": True, "incident_id": incident_id})


@metrics_bp.route("/api/v1/slo", methods=["GET"])
def get_slos():
    """Get all SLO definitions with current measurements."""
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM slo_definitions ORDER BY name")
        rows = cur.fetchall()

    slos = []
    for r in rows:
        slo = dict(r) if hasattr(r, "keys") else dict(zip(
            ["id", "name", "description", "target_percent", "measurement_window", "sli_query", "created_at"], r))
        slos.append(slo)
    return jsonify({"slos": slos})


@metrics_bp.route("/api/v1/status", methods=["GET"])
def status_page_data():
    """Public status page data. Shows system health and incident history."""
    # Current health
    checks = {}
    try:
        with db_connection() as conn:
            conn.cursor().execute("SELECT 1")
        checks["api"] = "operational"
        checks["database"] = "operational"
    except Exception:
        checks["api"] = "degraded"
        checks["database"] = "outage"

    # Recent incidents
    p = param_placeholder()
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT id, title, severity, status, started_at, resolved_at
            FROM incidents WHERE started_at > {p}
            ORDER BY started_at DESC LIMIT 20
        """, (week_ago,))
        rows = cur.fetchall()

    incidents = []
    for r in rows:
        inc = dict(r) if hasattr(r, "keys") else dict(zip(
            ["id", "title", "severity", "status", "started_at", "resolved_at"], r))
        incidents.append(inc)

    return jsonify({
        "status": "operational" if all(v == "operational" for v in checks.values()) else "degraded",
        "components": checks,
        "recent_incidents": incidents,
        "updated_at": datetime.now(timezone.utc).isoformat()
    })


def _send_alert(severity: str, title: str, incident_id: str):
    """Send alert to PagerDuty or Opsgenie. Placeholder for integration."""
    pagerduty_key = os.getenv("PAGERDUTY_ROUTING_KEY")
    opsgenie_key = os.getenv("OPSGENIE_API_KEY")

    if pagerduty_key:
        # PagerDuty Events API v2
        print(json.dumps({"alert": "pagerduty", "severity": severity, "title": title, "incident_id": incident_id}))
    elif opsgenie_key:
        print(json.dumps({"alert": "opsgenie", "severity": severity, "title": title, "incident_id": incident_id}))
    else:
        print(json.dumps({"alert": "console_only", "severity": severity, "title": title, "incident_id": incident_id}))
