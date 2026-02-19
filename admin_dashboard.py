# admin_dashboard.py
# Unified admin dashboard for Artifact Zero
# Tracks ALL page visits, API calls, relay sessions, and Your OS usage
# Stores on persistent disk (/var/data) to survive deploys
#
# Add to app.py:
#   from admin_dashboard import init_admin
#   init_admin(app)

import os
import json
import uuid
import sqlite3
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g

admin = Blueprint('admin', __name__)

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Persistent disk path (same as relay)
def _db_dir():
    for d in ["/var/data", "/tmp"]:
        if os.path.isdir(d):
            return d
    return "/tmp"

ANALYTICS_DB = os.path.join(_db_dir(), "az_analytics.db")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def analytics_db():
    conn = sqlite3.connect(ANALYTICS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_analytics_db():
    conn = analytics_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS page_views (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            path TEXT NOT NULL,
            method TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT,
            referrer TEXT,
            country TEXT,
            session_id TEXT,
            latency_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_pv_created ON page_views(created_at);
        CREATE INDEX IF NOT EXISTS idx_pv_path ON page_views(path);
        CREATE INDEX IF NOT EXISTS idx_pv_ip ON page_views(ip);

        CREATE TABLE IF NOT EXISTS nti_runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            ip TEXT,
            input_preview TEXT,
            word_count INTEGER,
            nii_score REAL,
            dominance TEXT,
            tilt_tags TEXT,
            latency_ms INTEGER,
            session_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_nti_created ON nti_runs(created_at);

        CREATE TABLE IF NOT EXISTS relay_events (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            ip TEXT,
            username TEXT,
            detail TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_re_created ON relay_events(created_at);
    """)
    conn.commit()
    conn.close()
    print(f"[ADMIN] Analytics DB initialized at {ANALYTICS_DB}")


# ── MIDDLEWARE: Track every request ──

SKIP_PATHS = {'/health', '/favicon.ico', '/static'}

def track_request(app):
    """Register before/after request hooks on the Flask app."""

    @app.before_request
    def _before():
        g.req_start = __import__('time').time()

    @app.after_request
    def _after(response):
        try:
            path = request.path
            # Skip health checks and static files
            if any(path.startswith(s) for s in SKIP_PATHS):
                return response

            latency = int((__import__('time').time() - getattr(g, 'req_start', 0)) * 1000)
            ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            if ip and "," in ip:
                ip = ip.split(",")[0].strip()

            conn = analytics_db()
            conn.execute("""
                INSERT INTO page_views (id, created_at, path, method, ip, user_agent, referrer, session_id, latency_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()),
                utc_now(),
                path,
                request.method,
                ip,
                (request.headers.get("User-Agent") or "")[:300],
                (request.headers.get("Referer") or "")[:500],
                request.headers.get("X-Session-Id", ""),
                latency,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[ADMIN] tracking error: {e}")

        return response


def log_nti_run(request_id, ip, text, result, latency_ms, session_id=""):
    """Call this from the /nti endpoint after scoring."""
    try:
        conn = analytics_db()
        conn.execute("""
            INSERT OR IGNORE INTO nti_runs (id, created_at, ip, input_preview, word_count, nii_score, dominance, tilt_tags, latency_ms, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            request_id,
            utc_now(),
            ip,
            (text or "")[:200],
            len((text or "").split()),
            result.get("nii", {}).get("nii_score"),
            result.get("parent_failure_modes", {}).get("dominance_order", ""),
            json.dumps(result.get("tilt_taxonomy", {}).get("tags_detected", [])),
            latency_ms,
            session_id,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ADMIN] nti_run log error: {e}")


def log_relay_event(event_type, ip="", username="", detail=""):
    """Call from relay endpoints for login/signup/score/correction events."""
    try:
        conn = analytics_db()
        conn.execute("""
            INSERT INTO relay_events (id, created_at, event_type, ip, username, detail)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), utc_now(), event_type, ip, username, (detail or "")[:500]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ADMIN] relay event log error: {e}")


# ── ADMIN DASHBOARD ROUTE ──

@admin.route('/az-admin')
def admin_dashboard():
    token = request.args.get('token', '')
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return "Unauthorized", 401

    conn = analytics_db()

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()
    hour_ago = (now - timedelta(hours=1)).isoformat()

    # ── Summary stats ──
    total_views = conn.execute("SELECT COUNT(*) as c FROM page_views").fetchone()['c']
    today_views = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE created_at >= ?", (today_start,)).fetchone()['c']
    week_views = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE created_at >= ?", (week_start,)).fetchone()['c']
    unique_ips_total = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views").fetchone()['c']
    unique_ips_today = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (today_start,)).fetchone()['c']
    active_now = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (hour_ago,)).fetchone()['c']

    # ── Page breakdown ──
    pages = conn.execute("""
        SELECT path, COUNT(*) as hits, COUNT(DISTINCT ip) as visitors
        FROM page_views
        GROUP BY path
        ORDER BY hits DESC
        LIMIT 30
    """).fetchall()

    pages_today = conn.execute("""
        SELECT path, COUNT(*) as hits, COUNT(DISTINCT ip) as visitors
        FROM page_views
        WHERE created_at >= ?
        GROUP BY path
        ORDER BY hits DESC
        LIMIT 20
    """, (today_start,)).fetchall()

    # ── NTI runs ──
    total_nti = conn.execute("SELECT COUNT(*) as c FROM nti_runs").fetchone()['c']
    nti_today = conn.execute("SELECT COUNT(*) as c FROM nti_runs WHERE created_at >= ?", (today_start,)).fetchone()['c']

    recent_nti = conn.execute("""
        SELECT * FROM nti_runs ORDER BY created_at DESC LIMIT 30
    """).fetchall()

    # ── Relay events ──
    total_relay = conn.execute("SELECT COUNT(*) as c FROM relay_events").fetchone()['c']
    relay_signups = conn.execute("SELECT COUNT(*) as c FROM relay_events WHERE event_type = 'signup'").fetchone()['c']
    relay_logins = conn.execute("SELECT COUNT(*) as c FROM relay_events WHERE event_type = 'login'").fetchone()['c']
    relay_scores = conn.execute("SELECT COUNT(*) as c FROM relay_events WHERE event_type = 'score'").fetchone()['c']

    recent_relay = conn.execute("""
        SELECT * FROM relay_events ORDER BY created_at DESC LIMIT 30
    """).fetchall()

    # ── Recent visitors ──
    recent_visitors = conn.execute("""
        SELECT ip, 
               MIN(created_at) as first_seen,
               MAX(created_at) as last_seen,
               COUNT(*) as page_hits,
               COUNT(DISTINCT path) as unique_pages,
               GROUP_CONCAT(DISTINCT path) as paths,
               MAX(user_agent) as ua,
               MAX(referrer) as ref
        FROM page_views
        WHERE created_at >= ?
        GROUP BY ip
        ORDER BY last_seen DESC
        LIMIT 50
    """, (week_start,)).fetchall()

    # ── Referrers ──
    referrers = conn.execute("""
        SELECT referrer, COUNT(*) as hits, COUNT(DISTINCT ip) as visitors
        FROM page_views
        WHERE referrer != '' AND referrer IS NOT NULL
        GROUP BY referrer
        ORDER BY hits DESC
        LIMIT 20
    """).fetchall()

    # ── Hourly traffic (last 24h) ──
    hourly = conn.execute("""
        SELECT strftime('%Y-%m-%d %H:00', created_at) as hour,
               COUNT(*) as hits,
               COUNT(DISTINCT ip) as visitors
        FROM page_views
        WHERE created_at >= ?
        GROUP BY hour
        ORDER BY hour DESC
    """, ((now - timedelta(hours=24)).isoformat(),)).fetchall()

    conn.close()

    # ── BUILD HTML ──
    def esc(s):
        return str(s or '—').replace('<', '&lt;').replace('>', '&gt;')

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AZ Admin</title>
<style>
:root{{--bg:#0a0c10;--s:#12151b;--s2:#1a1e27;--b:#252a35;--t:#e8eaf0;--m:#6b7280;--a:#3b82f6;--g:#22c55e;--r:#ef4444;--am:#f59e0b;--p:#a78bfa}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--t);font-family:'Courier New',monospace;font-size:13px;padding:20px;max-width:1200px;margin:0 auto}}
h1{{color:var(--g);font-size:20px;margin-bottom:4px}}
h2{{color:var(--a);font-size:14px;margin:24px 0 8px;text-transform:uppercase;letter-spacing:2px}}
h3{{color:var(--p);font-size:12px;margin:16px 0 6px;text-transform:uppercase;letter-spacing:1px}}
.sub{{color:var(--m);font-size:11px;margin-bottom:20px}}
a{{color:var(--g);text-decoration:none}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}}
.card{{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:14px;text-align:center}}
.card .val{{font-size:28px;font-weight:bold;color:var(--g)}}
.card .lbl{{font-size:10px;color:var(--m);text-transform:uppercase;letter-spacing:1px;margin-top:4px}}
.card.blue .val{{color:var(--a)}}
.card.amber .val{{color:var(--am)}}
.card.purple .val{{color:var(--p)}}
table{{width:100%;border-collapse:collapse;margin-bottom:16px;font-size:12px}}
th{{text-align:left;padding:6px 8px;color:var(--m);border-bottom:1px solid var(--b);font-size:10px;text-transform:uppercase;letter-spacing:1px}}
td{{padding:6px 8px;border-bottom:1px solid var(--b);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
tr:hover{{background:var(--s2)}}
.tag{{display:inline-block;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:bold}}
.tag-green{{background:#14532d;color:#86efac}}
.tag-red{{background:#7f1d1d;color:#fca5a5}}
.tag-amber{{background:#78350f;color:#fde68a}}
.tag-blue{{background:#1e3a5f;color:#93c5fd}}
.bar{{height:6px;border-radius:3px;background:var(--s2);overflow:hidden;margin-top:4px}}
.bar-fill{{height:100%;border-radius:3px;background:var(--g)}}
.refresh{{color:var(--g);cursor:pointer;font-size:12px}}
</style>
</head><body>

<h1>ARTIFACT ZERO — COMMAND CENTER</h1>
<div class="sub">{utc_now()[:19]} UTC · <a class="refresh" href="/az-admin?token={token}">↻ REFRESH</a></div>

<h2>Traffic Overview</h2>
<div class="grid">
  <div class="card"><div class="val">{active_now}</div><div class="lbl">Active (1hr)</div></div>
  <div class="card"><div class="val">{today_views}</div><div class="lbl">Views Today</div></div>
  <div class="card blue"><div class="val">{unique_ips_today}</div><div class="lbl">Visitors Today</div></div>
  <div class="card amber"><div class="val">{week_views}</div><div class="lbl">Views (7d)</div></div>
  <div class="card purple"><div class="val">{unique_ips_total}</div><div class="lbl">All-Time Visitors</div></div>
  <div class="card"><div class="val">{total_views}</div><div class="lbl">All-Time Views</div></div>
</div>

<h2>Product Usage</h2>
<div class="grid">
  <div class="card"><div class="val">{total_nti}</div><div class="lbl">NTI Audits</div></div>
  <div class="card blue"><div class="val">{nti_today}</div><div class="lbl">Audits Today</div></div>
  <div class="card amber"><div class="val">{relay_signups}</div><div class="lbl">Relay Signups</div></div>
  <div class="card purple"><div class="val">{relay_scores}</div><div class="lbl">Relay Scores</div></div>
  <div class="card"><div class="val">{relay_logins}</div><div class="lbl">Relay Logins</div></div>
  <div class="card"><div class="val">{total_relay}</div><div class="lbl">Total Relay Events</div></div>
</div>

<h2>Hourly Traffic (24h)</h2>
<table>
<tr><th>Hour</th><th>Hits</th><th>Visitors</th><th>Bar</th></tr>
"""

    max_hourly = max((h['hits'] for h in hourly), default=1)
    for h in hourly:
        pct = int(h['hits'] / max_hourly * 100)
        html += f"<tr><td>{esc(h['hour'])}</td><td>{h['hits']}</td><td>{h['visitors']}</td>"
        html += f'<td><div class="bar" style="width:200px"><div class="bar-fill" style="width:{pct}%"></div></div></td></tr>\n'

    html += """</table>

<h2>Pages — Today</h2>
<table>
<tr><th>Path</th><th>Hits</th><th>Visitors</th></tr>
"""
    for p in pages_today:
        html += f"<tr><td>{esc(p['path'])}</td><td>{p['hits']}</td><td>{p['visitors']}</td></tr>\n"

    html += """</table>

<h2>Pages — All Time</h2>
<table>
<tr><th>Path</th><th>Hits</th><th>Visitors</th></tr>
"""
    for p in pages:
        html += f"<tr><td>{esc(p['path'])}</td><td>{p['hits']}</td><td>{p['visitors']}</td></tr>\n"

    html += """</table>

<h2>Referrers</h2>
<table>
<tr><th>Source</th><th>Hits</th><th>Visitors</th></tr>
"""
    for r in referrers:
        html += f"<tr><td>{esc(r['referrer'][:80])}</td><td>{r['hits']}</td><td>{r['visitors']}</td></tr>\n"

    html += """</table>

<h2>Recent Visitors (7d)</h2>
<table>
<tr><th>IP</th><th>Pages</th><th>Unique</th><th>First</th><th>Last</th><th>Paths</th><th>Referrer</th></tr>
"""
    for v in recent_visitors:
        paths_short = (v['paths'] or '')[:80]
        ref_short = (v['ref'] or '')[:60]
        html += f"<tr><td>{esc(v['ip'])}</td><td>{v['page_hits']}</td><td>{v['unique_pages']}</td>"
        html += f"<td>{esc(v['first_seen'][:16])}</td><td>{esc(v['last_seen'][:16])}</td>"
        html += f"<td>{esc(paths_short)}</td><td>{esc(ref_short)}</td></tr>\n"

    html += """</table>

<h2>Recent NTI Audits</h2>
<table>
<tr><th>Time</th><th>IP</th><th>Input</th><th>Words</th><th>NII</th><th>Dominance</th><th>Tilts</th><th>ms</th></tr>
"""
    for n in recent_nti:
        nii_val = n['nii_score'] or 0
        nii_class = 'tag-green' if nii_val >= 0.7 else ('tag-amber' if nii_val >= 0.4 else 'tag-red')
        html += f"<tr><td>{esc(n['created_at'][:16])}</td><td>{esc(n['ip'])}</td>"
        html += f"<td>{esc((n['input_preview'] or '')[:60])}</td><td>{n['word_count'] or 0}</td>"
        html += f'<td><span class="tag {nii_class}">{nii_val:.2f}</span></td>'
        html += f"<td>{esc(n['dominance'])}</td><td>{esc(n['tilt_tags'])}</td><td>{n['latency_ms'] or 0}</td></tr>\n"

    html += """</table>

<h2>Recent Relay Activity</h2>
<table>
<tr><th>Time</th><th>Event</th><th>User</th><th>IP</th><th>Detail</th></tr>
"""
    for r in recent_relay:
        evt = r['event_type']
        evt_class = 'tag-green' if evt == 'signup' else ('tag-blue' if evt == 'login' else 'tag-amber')
        html += f"<tr><td>{esc(r['created_at'][:16])}</td>"
        html += f'<td><span class="tag {evt_class}">{esc(evt)}</span></td>'
        html += f"<td>{esc(r['username'])}</td><td>{esc(r['ip'])}</td><td>{esc((r['detail'] or '')[:80])}</td></tr>\n"

    html += f"""</table>

<div style="margin-top:40px;padding:16px;border-top:1px solid var(--b);color:var(--m);font-size:10px;text-align:center">
  Artifact Zero Labs · Command Center · DB: {ANALYTICS_DB} · {utc_now()[:19]} UTC
</div>

</body></html>"""

    return html


# ── INIT FUNCTION ──

def init_admin(app):
    """Call this from app.py to wire up admin dashboard + request tracking."""
    init_analytics_db()
    track_request(app)
    app.register_blueprint(admin)
    print("[ADMIN] Dashboard live at /az-admin?token=ADMIN_TOKEN")
