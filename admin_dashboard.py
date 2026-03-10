# admin_dashboard.py — FOUNDER COCKPIT
# Not a dashboard. A steering wheel.
#
# Controls: site banner, pricing, kill switches, page copy injection
# Intelligence: pattern narratives, funnel analysis, drop-off detection
# Tracking: page views, NTI runs, relay events, API usage
#
# Auth: session-based admin login (role='admin' in users table)
# Fallback: ADMIN_TOKEN query param for backward compat

import os
import json
import uuid
import psycopg2
import psycopg2.extras
import time as _time
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g, session, redirect, render_template

admin = Blueprint('admin', __name__)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

def _pg_dsn():
    return os.getenv("DATABASE_URL", "")

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def est_now():
    from datetime import timezone as tz
    EST = timezone(timedelta(hours=-5))
    EDT = timezone(timedelta(hours=-4))
    now = datetime.now(timezone.utc)
    # EDT: second Sunday March → first Sunday November
    import time
    lt = time.localtime()
    # Simple DST check: March 8 – Nov 1 approx
    yday = now.timetuple().tm_yday
    eastern = EDT if 67 <= yday <= 304 else EST
    label = "EDT" if 67 <= yday <= 304 else "EST"
    return datetime.now(eastern).strftime('%Y-%m-%d %H:%M:%S') + ' ' + label

class _PgRow(dict):
    """Makes psycopg2 rows accessible by both index and column name, like sqlite3.Row."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class _PgCursor:
    """Wraps psycopg2 cursor to return _PgRow objects and accept ? placeholders."""
    def __init__(self, cur, conn):
        self._cur = cur
        self._conn = conn
    def _fix(self, sql):
        return sql.replace("?", "%s")
    def execute(self, sql, params=None):
        self._cur.execute(self._fix(sql), params)
        return self
    def fetchone(self):
        row = self._cur.fetchone()
        if row is None: return None
        cols = [d[0] for d in self._cur.description]
        return _PgRow(zip(cols, row))
    def fetchall(self):
        rows = self._cur.fetchall()
        if not rows: return []
        cols = [d[0] for d in self._cur.description]
        return [_PgRow(zip(cols, r)) for r in rows]
    def close(self):
        self._cur.close()

class _PgConn:
    """Wraps psycopg2 connection to behave like sqlite3 connection."""
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        sql = sql.replace("?", "%s").replace("INSERT OR IGNORE", "INSERT").replace(
            "ON CONFLICT(key)", "ON CONFLICT(key)").replace(
            "ON CONFLICT DO NOTHING", "ON CONFLICT DO NOTHING")
        # Handle GROUP_CONCAT -> string_agg
        import re
        sql = re.sub(r"GROUP_CONCAT\(DISTINCT\s+(\w+)\)", r"string_agg(DISTINCT \1, ',')", sql)
        sql = re.sub(r"GROUP_CONCAT\((\w+)\)", r"string_agg(\1::text, ',')", sql)
        sql = re.sub(r"strftime\('%H:00',\s*(\w+)\)", r"to_char(\1::timestamptz AT TIME ZONE 'UTC', 'HH24') || ':00'", sql)
        cur.execute(sql, params)
        return _PgCursor(cur, self._conn)
    def cursor(self):
        return _PgCursor(self._conn.cursor(), self._conn)
    def commit(self):
        self._conn.commit()
    def close(self):
        self._conn.close()
    def executescript(self, sql):
        # Not used after migration but kept for safety
        pass

def analytics_db():
    dsn = _pg_dsn()
    if not dsn:
        return None
    conn = psycopg2.connect(dsn)
    return _PgConn(conn)

def init_analytics_db():
    conn = analytics_db()
    if conn is None:
        print("[COCKPIT] No DATABASE_URL — analytics skipped")
        return
    stmts = [
        """CREATE TABLE IF NOT EXISTS page_views (
            id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL,
            path TEXT NOT NULL, method TEXT NOT NULL,
            ip TEXT, user_agent TEXT, referrer TEXT,
            country TEXT, session_id TEXT, latency_ms INTEGER
        )""",
        "CREATE INDEX IF NOT EXISTS idx_pv_created ON page_views(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pv_path ON page_views(path)",
        "CREATE INDEX IF NOT EXISTS idx_pv_ip ON page_views(ip)",
        """CREATE TABLE IF NOT EXISTS nti_runs (
            id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL,
            ip TEXT, input_preview TEXT, word_count INTEGER,
            nii_score REAL, dominance TEXT, tilt_tags TEXT,
            latency_ms INTEGER, session_id TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_nti_created ON nti_runs(created_at)",
        """CREATE TABLE IF NOT EXISTS relay_events (
            id TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL,
            event_type TEXT NOT NULL, ip TEXT, username TEXT, detail TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_re_created ON relay_events(created_at)",
        """CREATE TABLE IF NOT EXISTS cockpit_config (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL
        )""",
    ]
    for stmt in stmts:
        conn.execute(stmt)
    conn.commit()
    conn.close()
    print("[COCKPIT] Analytics tables verified in Postgres")

# ─── Config helpers ───
def config_get(key, default=""):
    try:
        conn = analytics_db()
        if conn is None: return default
        row = conn.execute("SELECT value FROM cockpit_config WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default

def config_set(key, value):
    conn = analytics_db()
    if conn is None: return
    conn.execute("""INSERT INTO cockpit_config (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at
    """, (key, str(value), utc_now()))
    conn.commit()
    conn.close()

def config_get_json(key, default=None):
    raw = config_get(key, "")
    if not raw: return default or {}
    try: return json.loads(raw)
    except Exception: return default or {}

# ─── Public config endpoint (pages call this on load) ───
@admin.route('/api/cockpit/config')
def public_config():
    banner_on = config_get("banner_on", "0") == "1"
    return jsonify({
        "banner": {
            "on": banner_on,
            "text": config_get("banner_text", ""),
            "color": config_get("banner_color", "#00e89c"),
            "bg": config_get("banner_bg", "#064e3b"),
            "link": config_get("banner_link", ""),
        },
        "kills": config_get_json("kill_switches", {}),
        "pricing": config_get_json("pricing", {}),
        "copy": config_get_json("copy_overrides", {}),
        "modal": config_get_json("modal", {}),
    })

# ─── Request tracking middleware ───
SKIP_PATHS = {'/health', '/favicon.ico', '/static', '/api/cockpit/config'}
BOT_MARKERS = {'bot','crawler','spider','curl','wget','python-requests','go-http','uptimerobot','pingdom'}

# Known exploit scanner path fragments — skip logging, don't block
SCANNER_PATHS = {
    '/vendor/phpunit', '/.env', '/index.php', '/wp-', '/wordpress',
    '/admin/vendor', '/backup/vendor', '/blog/vendor', '/cms/vendor',
    '/crm/vendor', '/demo/vendor', '/app/vendor', '/apps/vendor',
    '/api/vendor', '/V2/vendor', '/DrOv', '/Dr0v', '/hello.world',
    '/developmentserver', '/metadatauploader', '/update/picture',
    '/phpinfo', '/.git', '/config.php', '/setup.php', '/install.php',
    '/xmlrpc', '/boaform', '/cgi-bin', '/shell', '/cmd', '/eval',
}

def _is_scanner(path):
    p = path.lower()
    return any(s.lower() in p for s in SCANNER_PATHS)

def _is_bot(ua):
    ua_lower = (ua or "").lower()
    return any(b in ua_lower for b in BOT_MARKERS)

def track_request(app):
    @app.before_request
    def _before():
        g.req_start = _time.time()

    @app.after_request
    def _after(response):
        try:
            path = request.path
            if any(path.startswith(s) for s in SKIP_PATHS):
                return response
            ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            if ip and "," in ip: ip = ip.split(",")[0].strip()
            ua = request.headers.get("User-Agent") or ""
            if _is_bot(ua): return response
            if _is_scanner(path): return response
            latency = int((_time.time() - getattr(g, 'req_start', 0)) * 1000)
            conn = analytics_db()
            if conn is None: return response
            conn.execute("INSERT INTO page_views (id,created_at,path,method,ip,user_agent,referrer,session_id,latency_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), utc_now(), path, request.method, ip, ua[:300],
                 (request.headers.get("Referer") or "")[:500], request.headers.get("X-Session-Id",""), latency))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[COCKPIT] track err: {e}")
        return response

def log_nti_run(request_id, ip, text, result, latency_ms, session_id=""):
    try:
        tilt = result.get("tilt_taxonomy", [])
        if isinstance(tilt, dict): tilt = tilt.get("tags_detected", [])
        if not isinstance(tilt, list): tilt = []
        dom = result.get("parent_failure_modes", {})
        if isinstance(dom, dict): dom = dom.get("dominance_order", "")
        if isinstance(dom, list): dom = " + ".join(str(d) for d in dom)
        nii = result.get("nii", {})
        nii_score = nii.get("nii_score") if isinstance(nii, dict) else nii
        conn = analytics_db()
        if conn is None: return
        conn.execute("INSERT INTO nti_runs (id,created_at,ip,input_preview,word_count,nii_score,dominance,tilt_tags,latency_ms,session_id) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (request_id, utc_now(), ip, (text or "")[:200], len((text or "").split()), nii_score, str(dom), json.dumps(tilt), latency_ms, session_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[COCKPIT] nti log err: {e}")

def log_relay_event(event_type, ip="", username="", detail=""):
    try:
        conn = analytics_db()
        if conn is None: return
        conn.execute("INSERT INTO relay_events (id,created_at,event_type,ip,username,detail) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4()), utc_now(), event_type, ip, username, (detail or "")[:500]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[COCKPIT] relay log err: {e}")

# ─── Auth ───
def _is_admin():
    """Admin access: session role only. No URL token fallback."""
    return session.get("role") == "admin"

# ─── Cockpit API endpoints ───
@admin.route('/az-cockpit/api/banner', methods=['POST'])
def api_banner():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    data = request.get_json() or {}
    config_set("banner_on", "1" if data.get("on") else "0")
    for k in ["text","color","bg","link"]:
        if k in data: config_set(f"banner_{k}", str(data[k])[:500])
    return jsonify({"ok":True})

@admin.route('/az-cockpit/api/kills', methods=['POST'])
def api_kills():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    current = config_get_json("kill_switches", {})
    current.update(request.get_json() or {})
    config_set("kill_switches", json.dumps(current))
    return jsonify({"ok":True, "kills":current})

@admin.route('/az-cockpit/api/pricing', methods=['POST'])
def api_pricing():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    current = config_get_json("pricing", {})
    current.update(request.get_json() or {})
    config_set("pricing", json.dumps(current))
    return jsonify({"ok":True, "pricing":current})

@admin.route('/az-cockpit/api/copy', methods=['POST'])
def api_copy():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    current = config_get_json("copy_overrides", {})
    current.update(request.get_json() or {})
    config_set("copy_overrides", json.dumps(current))
    return jsonify({"ok":True, "copy":current})

@admin.route('/az-cockpit/api/modal', methods=['POST'])
def api_modal():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    config_set("modal", json.dumps(request.get_json() or {}))
    return jsonify({"ok":True})

@admin.route('/az-cockpit/api/config-dump')
def api_config_dump():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    conn = analytics_db()
    rows = conn.execute("SELECT key,value,updated_at FROM cockpit_config ORDER BY key").fetchall()
    conn.close()
    return jsonify({r["key"]: {"value":r["value"],"updated_at":r["updated_at"]} for r in rows})

@admin.route('/az-cockpit/api/set-admin-email', methods=['POST'])
def api_set_admin():
    if not _is_admin(): return jsonify({"error":"Unauthorized"}), 401
    import database
    email = ((request.get_json() or {}).get("email") or "").strip().lower()
    if not email: return jsonify({"error":"email required"}), 400
    conn = database.db_connect()
    cur = conn.cursor()
    q = "UPDATE users SET role='admin' WHERE email=%s" if database.USE_PG else "UPDATE users SET role='admin' WHERE email=?"
    cur.execute(q, (email,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return jsonify({"ok":True, "affected":affected, "email":email})

# ─── Pattern Intelligence ───
def _build_insights(conn):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    yesterday_start = (now - timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    week_start = (now - timedelta(days=7)).isoformat()
    hour_ago = (now - timedelta(hours=1)).isoformat()
    insights = []

    # Active now
    active = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (hour_ago,)).fetchone()["c"]
    insights.append(("🟢" if active else "⚫", f"{active} visitor{'s' if active!=1 else ''} active in the last hour." if active else "No visitors in the last hour."))

    # Today vs yesterday
    today_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (today_start,)).fetchone()["c"]
    yest_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ? AND created_at < ?", (yesterday_start, today_start)).fetchone()["c"]
    if yest_v > 0:
        delta = today_v - yest_v
        pct = int((delta / yest_v) * 100)
        insights.append(("📊", f"Today: {today_v} visitors ({'up' if delta>0 else 'down'} {abs(pct)}% vs yesterday's {yest_v})."))
    else:
        insights.append(("📊", f"Today: {today_v} visitors. No yesterday data to compare."))

    # Page activity — what pages are actually getting traffic this week
    active_pages = conn.execute(
        "SELECT path, COUNT(DISTINCT ip) as v FROM page_views WHERE created_at >= ? AND path NOT LIKE '/api/%' AND path NOT LIKE '/static/%' GROUP BY path ORDER BY v DESC LIMIT 5",
        (week_start,)
    ).fetchall()
    if active_pages:
        page_summary = ", ".join(f"{r['path']} ({r['v']})" for r in active_pages)
        insights.append(("📄", f"Top pages this week: {page_summary}."))

    # SafeCheck usage
    sc_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE path = '/safecheck' AND created_at >= ?", (week_start,)).fetchone()["c"]
    home_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE path = '/' AND created_at >= ?", (week_start,)).fetchone()["c"]
    if home_v > 0 and sc_v > 0:
        rate = int((sc_v / home_v) * 100)
        insights.append(("⚡", f"{rate}% of homepage visitors reached SafeCheck this week ({home_v}→{sc_v})."))
    elif sc_v > 0:
        insights.append(("⚡", f"{sc_v} SafeCheck visitor{'s' if sc_v!=1 else ''} this week."))

    # Scoreboard traffic
    f500_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE path LIKE '/fortune500%' AND created_at >= ?", (week_start,)).fetchone()["c"]
    vc_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE path LIKE '/vc-funds%' AND created_at >= ?", (week_start,)).fetchone()["c"]
    if f500_v or vc_v:
        insights.append(("🏆", f"Scoreboards: Fortune 500 {f500_v} visitors, VC Funds {vc_v} visitors this week."))

    # Signup attempts
    signup_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE path = '/signup' AND created_at >= ?", (week_start,)).fetchone()["c"]
    if signup_v:
        insights.append(("👤", f"{signup_v} visitor{'s' if signup_v!=1 else ''} reached /signup this week."))

    # NTI runs
    nti_today = conn.execute("SELECT COUNT(*) as c FROM nti_runs WHERE created_at >= ?", (today_start,)).fetchone()["c"]
    nti_week = conn.execute("SELECT COUNT(*) as c FROM nti_runs WHERE created_at >= ?", (week_start,)).fetchone()["c"]
    if nti_today: insights.append(("⚡", f"{nti_today} scores today, {nti_week} this week."))
    elif nti_week: insights.append(("💤", f"No scores today. {nti_week} this week."))

    # Average NII
    avg_nii = conn.execute("SELECT AVG(nii_score) as a FROM nti_runs WHERE created_at >= ? AND nii_score IS NOT NULL", (week_start,)).fetchone()["a"]
    if avg_nii is not None:
        d = avg_nii if avg_nii > 1 else avg_nii * 100
        insights.append(("🎯", f"Average NII this week: {d:.0f}."))

    # Top referrers
    refs = conn.execute("SELECT referrer, COUNT(DISTINCT ip) as v FROM page_views WHERE referrer!='' AND referrer IS NOT NULL AND created_at >= ? GROUP BY referrer ORDER BY v DESC LIMIT 3", (week_start,)).fetchall()
    if refs:
        insights.append(("🔗", "Top sources: " + ", ".join(f"{r['referrer'][:40]} ({r['v']})" for r in refs)))

    # Slow requests
    slow = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE latency_ms > 5000 AND created_at >= ?", (week_start,)).fetchone()["c"]
    if slow > 3:
        insights.append(("⚠️", f"{slow} slow requests (>5s) this week."))

    # Signups from relay events
    signups = conn.execute("SELECT COUNT(*) as c FROM relay_events WHERE event_type='signup' AND created_at >= ?", (week_start,)).fetchone()["c"]
    if signups: insights.append(("👤", f"{signups} account signup{'s' if signups!=1 else ''} this week."))
    elif today_v > 0: insights.append(("👤", "No account signups this week."))

    return insights

# ─── Redirect old route ───
@admin.route('/az-admin')
def admin_redirect():
    return redirect('/az-cockpit')

# ─── Cockpit Login (POST) ───
@admin.route('/az-cockpit/login', methods=['POST'])
def cockpit_login():
    token = request.form.get('token', '')
    if token and ADMIN_TOKEN and token == ADMIN_TOKEN:
        session['role'] = 'admin'
        return redirect('/az-cockpit')
    return render_template("az-cockpit-login.html", error="Invalid token"), 200

# ─── COCKPIT MAIN ───
@admin.route('/az-cockpit')
def cockpit():
    if not _is_admin():
        return render_template("az-cockpit-login.html", error=None), 200

    conn = analytics_db()
    now = datetime.now(timezone.utc)
    ts = now.replace(hour=0,minute=0,second=0,microsecond=0).isoformat()
    ws = (now - timedelta(days=7)).isoformat()
    ha = (now - timedelta(hours=1)).isoformat()

    insights = _build_insights(conn)
    active_now = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (ha,)).fetchone()["c"]
    today_views = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE created_at >= ?", (ts,)).fetchone()["c"]
    today_visitors = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (ts,)).fetchone()["c"]
    week_views = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE created_at >= ?", (ws,)).fetchone()["c"]
    total_nti = conn.execute("SELECT COUNT(*) as c FROM nti_runs").fetchone()["c"]
    nti_today = conn.execute("SELECT COUNT(*) as c FROM nti_runs WHERE created_at >= ?", (ts,)).fetchone()["c"]

    banner_on = config_get("banner_on","0") == "1"
    banner_text = config_get("banner_text","")
    banner_color = config_get("banner_color","#00e89c")
    banner_bg = config_get("banner_bg","#064e3b")
    banner_link = config_get("banner_link","")
    kills = config_get_json("kill_switches", {})
    pricing = config_get_json("pricing", {})
    copy_ov = config_get_json("copy_overrides", {})
    modal = config_get_json("modal", {})

    recent_visitors = conn.execute("""SELECT ip, COUNT(*) as hits, COUNT(DISTINCT path) as pages, GROUP_CONCAT(DISTINCT path) as paths, MAX(created_at) as last_seen, MAX(referrer) as ref FROM page_views WHERE created_at >= ? GROUP BY ip ORDER BY last_seen DESC LIMIT 20""", ((now-timedelta(hours=24)).isoformat(),)).fetchall()
    recent_nti = conn.execute("SELECT * FROM nti_runs ORDER BY created_at DESC LIMIT 15").fetchall()
    pages_today = conn.execute("SELECT path, COUNT(*) as hits, COUNT(DISTINCT ip) as visitors FROM page_views WHERE created_at >= ? GROUP BY path ORDER BY hits DESC LIMIT 15", (ts,)).fetchall()
    hourly = conn.execute("SELECT strftime('%H:00', created_at) as hour, COUNT(*) as hits, COUNT(DISTINCT ip) as v FROM page_views WHERE created_at >= ? GROUP BY hour ORDER BY hour DESC", ((now-timedelta(hours=12)).isoformat(),)).fetchall()
    conn.close()

    mx = max((h["hits"] for h in hourly), default=1)
    hourly_data = [{"hour": h["hour"], "hits": h["hits"], "v": h["v"], "pct": int(h["hits"]/mx*100) if mx else 0} for h in hourly]
    kill_features = ["safecheck", "rewrite", "relay", "signup", "api", "scrapers"]

    return render_template(
        "az-cockpit.html",
        est_now=est_now(),
        insights=insights,
        active_now=active_now,
        today_views=today_views,
        today_visitors=today_visitors,
        week_views=week_views,
        nti_today=nti_today,
        total_nti=total_nti,
        banner_on=banner_on,
        banner_text=banner_text,
        banner_color=banner_color,
        banner_bg=banner_bg,
        banner_link=banner_link,
        kills=kills,
        kill_features=kill_features,
        pricing=pricing,
        copy_ov=copy_ov,
        modal=modal,
        pages_today=pages_today,
        hourly=hourly_data,
        recent_visitors=recent_visitors,
        recent_nti=recent_nti,
    )


# ─── Rescrape Trigger ───
_scrape_status = {"running": False, "last_result": None, "started": None}

@admin.route('/az-cockpit/api/rescrape', methods=['POST'])
def api_rescrape():
    if not _is_admin():
        return jsonify(error="unauthorized"), 403
    if _scrape_status["running"]:
        return jsonify(error="Scrape already running", started=_scrape_status["started"]), 409

    data = request.get_json(silent=True) or {}
    target = data.get("target", "both")  # "f500", "vc", "both"
    limit = min(int(data.get("limit", 50)), 100)

    import threading
    def _run():
        _scrape_status["running"] = True
        _scrape_status["started"] = utc_now()
        _scrape_status["last_result"] = None
        try:
            from f500_scraper_v3 import lambda_handler
            result = lambda_handler({"target": target, "limit": limit}, None)
            _scrape_status["last_result"] = result.get("body", str(result))
        except Exception as e:
            _scrape_status["last_result"] = f"Error: {e}"
        finally:
            _scrape_status["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify(ok=True, target=target, limit=limit, started=_scrape_status["started"])

@admin.route('/az-cockpit/api/rescrape-status')
def api_rescrape_status():
    if not _is_admin():
        return jsonify(error="unauthorized"), 403
    return jsonify(**_scrape_status)


# ─── BOOTSTRAP: promote + seed without cockpit login ───
# /api/bootstrap removed — served its purpose, no longer needed


@admin.route('/az-cockpit/api/seed-score', methods=['POST'])
def api_seed_score():
    """Seed a company/fund score: accepts text, scores via NTI engine, writes to DB."""
    if not _is_admin():
        return jsonify(error="unauthorized"), 403
    data = request.get_json(silent=True) or {}
    table = data.get("table", "fortune500_scores")  # or "vc_fund_scores"
    name_col = "company_name" if table == "fortune500_scores" else "fund_name"
    slug = data.get("slug")
    name = data.get("name")
    rank = data.get("rank", 0)
    url = data.get("url", "")
    text = data.get("text", "")
    if not slug or not name or len(text) < 50:
        return jsonify(error="Need slug, name, and text (50+ chars)"), 400

    # Score via NTI engine on the server
    import requests as req
    try:
        r = req.post("http://127.0.0.1:10000/nti", json={"text": text}, timeout=30)
        score_data = r.json()
    except Exception as e:
        return jsonify(error=f"Scoring failed: {e}"), 500

    # Extract NII
    nii_raw = 0
    if "nii" in score_data:
        nii = score_data["nii"]
        nii_raw = nii.get("nii_score", 0) if isinstance(nii, dict) else nii
    nii_display = round(nii_raw * 100) if isinstance(nii_raw, float) and nii_raw <= 1.0 else round(nii_raw)

    # Count issues
    issues = 0
    fm = score_data.get("parent_failure_modes") or score_data.get("failure_modes", {})
    if isinstance(fm, dict):
        for key in ["UDDS", "DCE", "CCA"]:
            val = fm.get(key)
            if isinstance(val, dict):
                st = str(val.get(f"{key.lower()}_state", ""))
                if "CONFIRMED" in st or "PROBABLE" in st:
                    issues += 1
    tilt = score_data.get("tilt_taxonomy") or []
    if isinstance(tilt, list):
        issues += len(tilt)

    now = utc_now()
    conn = None
    try:
        import db as database
        conn = database.db_connect()
        cur = conn.cursor()
        if database.USE_PG:
            cur.execute(f"""
                INSERT INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (slug) DO UPDATE SET
                    homepage_copy=EXCLUDED.homepage_copy, score_json=EXCLUDED.score_json,
                    nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count,
                    last_checked=EXCLUDED.last_checked, last_changed=EXCLUDED.last_changed
            """, (slug, name, rank, url, text, json.dumps(score_data), nii_display, issues, now, now))
        else:
            cur.execute(f"""
                INSERT OR REPLACE INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (slug, name, rank, url, text, json.dumps(score_data), nii_display, issues, now, now))
        conn.commit()
        conn.close()
        return jsonify(ok=True, slug=slug, nii=nii_display, issues=issues)
    except Exception as e:
        if conn: conn.close()
        return jsonify(error=str(e)), 500


@admin.route('/az-cockpit/api/seed-batch', methods=['POST'])
def api_seed_batch():
    """Seed multiple companies in one call."""
    if not _is_admin():
        return jsonify(error="unauthorized"), 403
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    results = []
    for item in items:
        try:
            import requests as req
            text = item.get("text", "")
            if len(text) < 50:
                results.append({"slug": item.get("slug"), "error": "text too short"})
                continue
            r = req.post("http://127.0.0.1:10000/nti", json={"text": text}, timeout=30)
            score_data = r.json()

            nii_raw = 0
            if "nii" in score_data:
                nii = score_data["nii"]
                nii_raw = nii.get("nii_score", 0) if isinstance(nii, dict) else nii
            nii_display = round(nii_raw * 100) if isinstance(nii_raw, float) and nii_raw <= 1.0 else round(nii_raw)

            issues = 0
            fm = score_data.get("parent_failure_modes") or {}
            if isinstance(fm, dict):
                for key in ["UDDS", "DCE", "CCA"]:
                    val = fm.get(key)
                    if isinstance(val, dict):
                        st = str(val.get(f"{key.lower()}_state", ""))
                        if "CONFIRMED" in st or "PROBABLE" in st:
                            issues += 1
            tilt = score_data.get("tilt_taxonomy") or []
            if isinstance(tilt, list):
                issues += len(tilt)

            table = item.get("table", "fortune500_scores")
            name_col = "company_name" if table == "fortune500_scores" else "fund_name"
            now = utc_now()

            import db as database
            conn = database.db_connect()
            cur = conn.cursor()
            if database.USE_PG:
                cur.execute(f"""
                    INSERT INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (slug) DO UPDATE SET
                        homepage_copy=EXCLUDED.homepage_copy, score_json=EXCLUDED.score_json,
                        nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count,
                        last_checked=EXCLUDED.last_checked, last_changed=EXCLUDED.last_changed
                """, (item["slug"], item["name"], item.get("rank", 0), item.get("url", ""), text, json.dumps(score_data), nii_display, issues, now, now))
            else:
                cur.execute(f"""
                    INSERT OR REPLACE INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (item["slug"], item["name"], item.get("rank", 0), item.get("url", ""), text, json.dumps(score_data), nii_display, issues, now, now))
            conn.commit()
            conn.close()
            results.append({"slug": item["slug"], "nii": nii_display, "issues": issues})
        except Exception as e:
            results.append({"slug": item.get("slug"), "error": str(e)})
    return jsonify(results=results)


@admin.route('/az-cockpit/api/run-seed', methods=['POST'])
def api_run_seed():
    """Load seed_data.py and score+store all companies and VC funds."""
    if not _is_admin():
        return jsonify(error="unauthorized"), 403
    if _scrape_status["running"]:
        return jsonify(error="Already running"), 409

    import threading
    def _run():
        _scrape_status["running"] = True
        _scrape_status["started"] = utc_now()
        _scrape_status["last_result"] = None
        try:
            from seed_data import FORTUNE_500, VC_FUNDS
            import requests as req
            import db as database

            ok_f, ok_v, total = 0, 0, 0
            all_items = [(item, "fortune500_scores", "company_name") for item in FORTUNE_500] + \
                        [(item, "vc_fund_scores", "fund_name") for item in VC_FUNDS]

            for item, table, name_col in all_items:
                total += 1
                try:
                    text = item["text"]
                    r = req.post("http://127.0.0.1:10000/nti", json={"text": text}, timeout=30)
                    score_data = r.json()
                    if "error" in score_data:
                        continue

                    nii_raw = 0
                    if "nii" in score_data:
                        nii = score_data["nii"]
                        nii_raw = nii.get("nii_score", 0) if isinstance(nii, dict) else nii
                    nii_display = round(nii_raw * 100) if isinstance(nii_raw, float) and nii_raw <= 1.0 else round(nii_raw)

                    issues = 0
                    fm = score_data.get("parent_failure_modes") or {}
                    if isinstance(fm, dict):
                        for key in ["UDDS", "DCE", "CCA"]:
                            val = fm.get(key)
                            if isinstance(val, dict):
                                st = str(val.get(f"{key.lower()}_state", ""))
                                if "CONFIRMED" in st or "PROBABLE" in st:
                                    issues += 1
                    tilt = score_data.get("tilt_taxonomy") or []
                    if isinstance(tilt, list):
                        issues += len(tilt)

                    now = utc_now()
                    conn = database.db_connect()
                    cur = conn.cursor()
                    if database.USE_PG:
                        cur.execute(f"""
                            INSERT INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (slug) DO UPDATE SET
                                homepage_copy=EXCLUDED.homepage_copy, score_json=EXCLUDED.score_json,
                                nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count,
                                last_checked=EXCLUDED.last_checked, last_changed=EXCLUDED.last_changed
                        """, (item["slug"], item["name"], item.get("rank", 0), item.get("url", ""), text, json.dumps(score_data), nii_display, issues, now, now))
                    else:
                        cur.execute(f"""
                            INSERT OR REPLACE INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (item["slug"], item["name"], item.get("rank", 0), item.get("url", ""), text, json.dumps(score_data), nii_display, issues, now, now))
                    conn.commit()
                    conn.close()
                    if table == "fortune500_scores":
                        ok_f += 1
                    else:
                        ok_v += 1
                    print(f"[SEED] {item['slug']}: NII={nii_display} issues={issues}", flush=True)
                except Exception as e:
                    print(f"[SEED] Error {item.get('slug')}: {e}", flush=True)

            _scrape_status["last_result"] = f"Done. F500: {ok_f}/{len(FORTUNE_500)} | VC: {ok_v}/{len(VC_FUNDS)}"
        except Exception as e:
            _scrape_status["last_result"] = f"Error: {e}"
        finally:
            _scrape_status["running"] = False

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify(ok=True, started=_scrape_status["started"])



# ─── IP Visitor Drill-Down ───
@admin.route('/az-cockpit/visitor/<ip>')
def cockpit_visitor(ip):
    if not _is_admin():
        return redirect('/az-cockpit')
    conn = analytics_db()
    raw_rows = conn.execute(
        "SELECT created_at, path, method, referrer, latency_ms FROM page_views WHERE ip = ? ORDER BY created_at ASC",
        (ip,)
    ).fetchall()
    conn.close()

    def to_est(ts_str):
        try:
            dt = datetime.fromisoformat(ts_str.replace('Z',''))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            yday = dt.timetuple().tm_yday
            offset = timedelta(hours=-4) if 67 <= yday <= 304 else timedelta(hours=-5)
            return (dt + offset).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            return ts_str[:19]

    rows = [{"est_time": to_est(r["created_at"]), "path": r["path"], "method": r["method"],
             "referrer": r["referrer"], "latency_ms": r["latency_ms"]} for r in raw_rows]

    return render_template("az-cockpit-visitor.html", ip=ip, rows=rows)



# ─── Traffic Analytics API ───
@admin.route('/az-cockpit/api/traffic')
def api_traffic():
    if not _is_admin():
        return jsonify(error="unauthorized"), 403

    range_param = request.args.get('range', '7d')
    days_map = {'7d': 7, '30d': 30, '90d': 90}
    days = days_map.get(range_param, 7)
    prev_days = days * 2

    conn = analytics_db()
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    prev_since = (now - timedelta(days=prev_days)).isoformat()

    # Total visitors + views current period
    total_visitors = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (since,)).fetchone()["c"]
    total_views = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE created_at >= ?", (since,)).fetchone()["c"]
    avg_per_day = round(total_visitors / days, 1)

    # Previous period for comparison
    prev_visitors = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ? AND created_at < ?", (prev_since, since)).fetchone()["c"]

    # Repeat visitors — seen on 2+ distinct days
    repeat_rows = conn.execute("""
        SELECT ip, COUNT(DISTINCT date(created_at)) as days, COUNT(*) as hits
        FROM page_views WHERE created_at >= ?
        GROUP BY ip HAVING days >= 2
        ORDER BY days DESC, hits DESC LIMIT 10
    """, (since,)).fetchall()
    repeat_count = len(repeat_rows)

    # Daily breakdown
    daily_rows = conn.execute("""
        SELECT date(created_at) as date, COUNT(DISTINCT ip) as v, COUNT(*) as hits
        FROM page_views WHERE created_at >= ?
        GROUP BY date ORDER BY date ASC
    """, (since,)).fetchall()

    # Top pages (exclude cockpit itself)
    top_pages = conn.execute("""
        SELECT path, COUNT(DISTINCT ip) as visitors, COUNT(*) as hits
        FROM page_views WHERE created_at >= ? AND path NOT LIKE '/az-cockpit%' AND path NOT LIKE '/api/%' AND path NOT LIKE '/static/%'
        GROUP BY path ORDER BY visitors DESC LIMIT 10
    """, (since,)).fetchall()

    # Top referrers (external only)
    top_refs = conn.execute("""
        SELECT referrer as ref, COUNT(DISTINCT ip) as visitors
        FROM page_views WHERE created_at >= ? AND referrer != '' AND referrer IS NOT NULL
        AND referrer NOT LIKE '%artifact0.com%'
        GROUP BY referrer ORDER BY visitors DESC LIMIT 10
    """, (since,)).fetchall()

    # Entry pages — first page per session (approximated by first hit per ip per day)
    entry_rows = conn.execute("""
        SELECT path, COUNT(*) as sessions FROM (
            SELECT ip, date(created_at) as day, MIN(created_at) as first_hit,
                   path
            FROM page_views WHERE created_at >= ?
            GROUP BY ip, day
        ) GROUP BY path ORDER BY sessions DESC LIMIT 10
    """, (since,)).fetchall()

    # High engagement visitors (5+ distinct pages)
    engaged_rows = conn.execute("""
        SELECT ip, COUNT(DISTINCT path) as pages, COUNT(*) as hits,
               MAX(created_at) as last_seen, MIN(path) as first_path
        FROM page_views WHERE created_at >= ?
        GROUP BY ip HAVING pages >= 5
        ORDER BY pages DESC, hits DESC LIMIT 15
    """, (since,)).fetchall()

    conn.close()

    return jsonify(
        total_visitors=total_visitors,
        total_views=total_views,
        avg_per_day=avg_per_day,
        repeat_count=repeat_count,
        prev_visitors=prev_visitors,
        daily=[{"date": r["date"], "v": r["v"], "hits": r["hits"]} for r in daily_rows],
        top_pages=[{"path": r["path"], "visitors": r["visitors"], "hits": r["hits"]} for r in top_pages],
        top_refs=[{"ref": r["ref"][:60], "visitors": r["visitors"]} for r in top_refs],
        entry_pages=[{"path": r["path"], "sessions": r["sessions"]} for r in entry_rows],
        repeat_visitors=[{"ip": r["ip"], "days": r["days"], "hits": r["hits"]} for r in repeat_rows],
        engaged=[{"ip": r["ip"], "pages": r["pages"], "hits": r["hits"], "last_seen": r["last_seen"], "first_path": r["first_path"]} for r in engaged_rows],
    )


def init_admin(app):
    init_analytics_db()
    track_request(app)
    app.register_blueprint(admin)
    print("[COCKPIT] Live at /az-cockpit")
