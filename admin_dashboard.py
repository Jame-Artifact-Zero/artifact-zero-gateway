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
import sqlite3
import time as _time
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g, session, redirect

admin = Blueprint('admin', __name__)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

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
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            path TEXT NOT NULL, method TEXT NOT NULL,
            ip TEXT, user_agent TEXT, referrer TEXT,
            country TEXT, session_id TEXT, latency_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_pv_created ON page_views(created_at);
        CREATE INDEX IF NOT EXISTS idx_pv_path ON page_views(path);
        CREATE INDEX IF NOT EXISTS idx_pv_ip ON page_views(ip);
        CREATE TABLE IF NOT EXISTS nti_runs (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            ip TEXT, input_preview TEXT, word_count INTEGER,
            nii_score REAL, dominance TEXT, tilt_tags TEXT,
            latency_ms INTEGER, session_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_nti_created ON nti_runs(created_at);
        CREATE TABLE IF NOT EXISTS relay_events (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            event_type TEXT NOT NULL, ip TEXT, username TEXT, detail TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_re_created ON relay_events(created_at);
        CREATE TABLE IF NOT EXISTS cockpit_config (
            key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print(f"[COCKPIT] Analytics DB at {ANALYTICS_DB}")

# ─── Config helpers ───
def config_get(key, default=""):
    try:
        conn = analytics_db()
        row = conn.execute("SELECT value FROM cockpit_config WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default

def config_set(key, value):
    conn = analytics_db()
    conn.execute("""INSERT INTO cockpit_config (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
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
            latency = int((_time.time() - getattr(g, 'req_start', 0)) * 1000)
            conn = analytics_db()
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
        conn.execute("INSERT OR IGNORE INTO nti_runs (id,created_at,ip,input_preview,word_count,nii_score,dominance,tilt_tags,latency_ms,session_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (request_id, utc_now(), ip, (text or "")[:200], len((text or "").split()), nii_score, str(dom), json.dumps(tilt), latency_ms, session_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[COCKPIT] nti log err: {e}")

def log_relay_event(event_type, ip="", username="", detail=""):
    try:
        conn = analytics_db()
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

    active = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (hour_ago,)).fetchone()["c"]
    insights.append(("🟢" if active else "⚫", f"{active} visitor{'s' if active!=1 else ''} active in the last hour." if active else "No visitors in the last hour."))

    today_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ?", (today_start,)).fetchone()["c"]
    yest_v = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE created_at >= ? AND created_at < ?", (yesterday_start, today_start)).fetchone()["c"]
    if yest_v > 0:
        delta = today_v - yest_v
        pct = int((delta / yest_v) * 100)
        insights.append(("📊", f"Today: {today_v} visitors ({'up' if delta>0 else 'down'} {abs(pct)}% vs yesterday's {yest_v})."))
    else:
        insights.append(("📊", f"Today: {today_v} visitors. No yesterday data to compare."))

    # Funnel
    funnel = [("/","Landing"), ("/safecheck","SafeCheck"), ("/nti","Scored"), ("/api/v1/rewrite","Rewrite"), ("/dashboard","Pricing"), ("/signup","Signup")]
    fc = []
    for path, name in funnel:
        c = conn.execute("SELECT COUNT(DISTINCT ip) as c FROM page_views WHERE path LIKE ? AND created_at >= ?", (path+"%", week_start)).fetchone()["c"]
        fc.append((name, c))
    drops = []
    for i in range(len(fc)-1):
        nf, cf = fc[i]
        nt, ct = fc[i+1]
        if cf > 0 and ct == 0:
            drops.append(f"Everyone drops at {nf} → {nt}.")
        elif cf > 3 and ct > 0:
            rate = int((ct/cf)*100)
            if rate < 20:
                drops.append(f"Only {rate}% convert {nf}→{nt} ({cf}→{ct}).")
    if drops:
        insights.append(("🚨", " ".join(drops)))

    nti_today = conn.execute("SELECT COUNT(*) as c FROM nti_runs WHERE created_at >= ?", (today_start,)).fetchone()["c"]
    nti_week = conn.execute("SELECT COUNT(*) as c FROM nti_runs WHERE created_at >= ?", (week_start,)).fetchone()["c"]
    if nti_today: insights.append(("⚡", f"{nti_today} scores today, {nti_week} this week."))
    elif nti_week: insights.append(("💤", f"No scores today. {nti_week} this week."))

    avg_nii = conn.execute("SELECT AVG(nii_score) as a FROM nti_runs WHERE created_at >= ? AND nii_score IS NOT NULL", (week_start,)).fetchone()["a"]
    if avg_nii is not None:
        d = avg_nii if avg_nii > 1 else avg_nii * 100
        insights.append(("🎯", f"Average NII this week: {d:.0f}."))

    refs = conn.execute("SELECT referrer, COUNT(DISTINCT ip) as v FROM page_views WHERE referrer!='' AND referrer IS NOT NULL AND created_at >= ? GROUP BY referrer ORDER BY v DESC LIMIT 3", (week_start,)).fetchall()
    if refs:
        insights.append(("🔗", "Top sources: " + ", ".join(f"{r['referrer'][:40]} ({r['v']})" for r in refs)))

    slow = conn.execute("SELECT COUNT(*) as c FROM page_views WHERE latency_ms > 5000 AND created_at >= ?", (week_start,)).fetchone()["c"]
    if slow > 3:
        insights.append(("⚠️", f"{slow} slow requests (>5s) this week."))

    signups = conn.execute("SELECT COUNT(*) as c FROM relay_events WHERE event_type='signup' AND created_at >= ?", (week_start,)).fetchone()["c"]
    if signups: insights.append(("👤", f"{signups} signups this week."))
    elif today_v > 0: insights.append(("👤", "No signups this week."))

    return insights

# ─── Redirect old route ───
@admin.route('/az-admin')
def admin_redirect():
    t = request.args.get('token','')
    return redirect(f'/az-cockpit?token={t}' if t else '/az-cockpit')

# ─── COCKPIT MAIN ───
@admin.route('/az-cockpit')
def cockpit():
    if not _is_admin():
        return COCKPIT_LOGIN_HTML, 200

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

    tp = ""
    t = request.args.get("token","")
    if t: tp = f"?token={t}"

    def e(s): return str(s or '').replace('<','&lt;').replace('>','&gt;').replace('"','&quot;')

    insight_html = "".join(f'<div class="insight"><span class="ii">{icon}</span> {e(text)}</div>' for icon, text in insights)

    visitor_html = "".join(f'<tr><td>{e(v["ip"])}</td><td>{v["hits"]}</td><td>{v["pages"]}</td><td>{e(v["last_seen"][:16])}</td><td>{e((v["paths"] or "")[:80])}</td><td>{e((v["ref"] or "")[:50])}</td></tr>' for v in recent_visitors)

    nti_html = ""
    for n in recent_nti:
        nii = n["nii_score"] or 0
        nd = nii if nii > 1 else nii*100
        cls = "cg" if nd>=70 else ("ca" if nd>=40 else "cr")
        nti_html += f'<tr><td>{e(n["created_at"][:16])}</td><td>{e(n["ip"])}</td><td>{e((n["input_preview"] or "")[:50])}</td><td class="{cls}">{nd:.0f}</td><td>{n["latency_ms"] or 0}ms</td></tr>'

    page_html = "".join(f'<tr><td>{e(p["path"])}</td><td>{p["hits"]}</td><td>{p["visitors"]}</td></tr>' for p in pages_today)

    mx = max((h["hits"] for h in hourly), default=1)
    hour_html = "".join(f'<tr><td>{e(h["hour"])}</td><td>{h["hits"]}</td><td>{h["v"]}</td><td><div class="bar"><div class="bf" style="width:{int(h["hits"]/mx*100) if mx else 0}%"></div></div></td></tr>' for h in hourly)

    kill_features = ["safecheck","rewrite","relay","signup","api","scrapers"]
    kill_html = "".join(f'<label class="tr"><span class="tl">{f.upper()}</span><input type="checkbox" {"" if kills.get(f) else "checked"} onchange="tK(\'{f}\',!this.checked)"><span class="ts"></span></label>' for f in kill_features)

    return f'''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>COCKPIT</title>
<style>
:root{{--bg:#0a0c10;--s:#12151b;--s2:#1a1e27;--b:#252a35;--t:#e8eaf0;--m:#6b7280;--a:#00e89c;--g:#22c55e;--r:#ef4444;--am:#f59e0b;--bl:#3b82f6;--p:#a78bfa}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:var(--bg);color:var(--t);font-family:'Courier New',monospace;font-size:13px}}
.ck{{max-width:1100px;margin:0 auto;padding:20px}}
.hd{{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;padding-bottom:12px;border-bottom:1px solid var(--b)}}
.hd h1{{font-size:16px;color:var(--a);letter-spacing:3px}}
.hd .mt{{color:var(--m);font-size:11px}}
.hd a{{color:var(--a);text-decoration:none;margin-left:16px;font-size:12px}}
.sc{{margin-bottom:28px}}
.st{{font-size:12px;color:var(--bl);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px;display:flex;align-items:center;gap:8px}}
.st::after{{content:'';flex:1;height:1px;background:var(--b)}}
.insight{{background:var(--s);border-left:3px solid var(--a);padding:10px 14px;margin-bottom:6px;border-radius:0 6px 6px 0;font-size:13px;line-height:1.5}}
.ii{{margin-right:6px}}
.stats{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:20px}}
@media(max-width:800px){{.stats{{grid-template-columns:repeat(3,1fr)}}}}
.sd{{background:var(--s);border:1px solid var(--b);border-radius:6px;padding:14px 10px;text-align:center}}
.sd .v{{font-size:26px;font-weight:bold;color:var(--a)}}
.sd .l{{font-size:9px;color:var(--m);text-transform:uppercase;letter-spacing:1px;margin-top:4px}}
.cg2{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:800px){{.cg2{{grid-template-columns:1fr}}}}
.cp{{background:var(--s);border:1px solid var(--b);border-radius:8px;padding:16px}}
.cp h3{{font-size:11px;color:var(--p);text-transform:uppercase;letter-spacing:2px;margin-bottom:12px}}
input[type="text"],textarea{{background:var(--s2);border:1px solid var(--b);color:var(--t);padding:8px 10px;border-radius:4px;font-family:inherit;font-size:12px;width:100%}}
input[type="text"]:focus,textarea:focus{{border-color:var(--a);outline:none}}
textarea{{resize:vertical;min-height:60px}}
.ir{{display:flex;gap:8px;margin-bottom:8px;align-items:center}}
.ir label{{font-size:10px;color:var(--m);min-width:50px;text-transform:uppercase}}
.btn{{background:var(--a);color:#000;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:11px;font-weight:bold;text-transform:uppercase;letter-spacing:1px}}
.btn:hover{{opacity:.9}}
.btn-r{{background:var(--r);color:#fff}}
.btn-s{{padding:4px 10px;font-size:10px}}
.tr{{display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--b)}}
.tr:last-child{{border-bottom:none}}
.tl{{font-size:11px;color:var(--t);letter-spacing:1px}}
input[type="checkbox"]{{appearance:none;width:36px;height:20px;background:var(--r);border-radius:10px;position:relative;cursor:pointer;transition:.2s}}
input[type="checkbox"]:checked{{background:var(--g)}}
input[type="checkbox"]::before{{content:'';position:absolute;width:16px;height:16px;background:#fff;border-radius:50%;top:2px;left:2px;transition:.2s}}
input[type="checkbox"]:checked::before{{left:18px}}
input[type="color"]{{width:32px;height:28px;border:1px solid var(--b);border-radius:4px;cursor:pointer;padding:2px;background:var(--s2)}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th{{text-align:left;padding:6px 8px;color:var(--m);border-bottom:1px solid var(--b);font-size:9px;text-transform:uppercase;letter-spacing:1px}}
td{{padding:5px 8px;border-bottom:1px solid var(--b);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
tr:hover{{background:var(--s2)}}
.cg{{color:var(--g);font-weight:bold}}.ca{{color:var(--am);font-weight:bold}}.cr{{color:var(--r);font-weight:bold}}
.bar{{height:6px;border-radius:3px;background:var(--s2);overflow:hidden;width:120px}}
.bf{{height:100%;border-radius:3px;background:var(--a)}}
.ld{{width:8px;height:8px;border-radius:50%;background:var(--g);display:inline-block;animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.tabs{{display:flex;gap:4px;margin-bottom:12px}}
.tab{{padding:6px 14px;font-size:11px;color:var(--m);cursor:pointer;border-radius:4px 4px 0 0;border:1px solid transparent;border-bottom:none;text-transform:uppercase;letter-spacing:1px}}
.tab.active{{color:var(--a);border-color:var(--b);background:var(--s)}}
.tab:hover{{color:var(--t)}}
.tc{{display:none}}.tc.active{{display:block}}
.pb{{padding:10px 16px;text-align:center;font-size:13px;font-weight:bold;border-radius:4px;margin:8px 0}}
.toast{{position:fixed;bottom:20px;right:20px;background:var(--g);color:#000;padding:10px 20px;border-radius:6px;font-weight:bold;font-size:12px;display:none;z-index:999}}
</style>
</head><body>
<div class="ck">
<div class="hd">
  <h1><span class="ld"></span> &nbsp;COCKPIT</h1>
  <div class="mt">
    <span>{utc_now()[:19]} UTC</span>
    <a href="/az-cockpit{tp}">↻ REFRESH</a>
    <a href="/" target="_blank">SITE →</a>
    <a href="/logout">LOGOUT</a>
  </div>
</div>

<div class="sc"><div class="st">Intelligence</div>{insight_html}</div>

<div class="sc"><div class="st">Pulse</div>
<div class="stats">
  <div class="sd"><div class="v">{active_now}</div><div class="l">Active Now</div></div>
  <div class="sd"><div class="v">{today_views}</div><div class="l">Views Today</div></div>
  <div class="sd"><div class="v">{today_visitors}</div><div class="l">Visitors Today</div></div>
  <div class="sd"><div class="v">{week_views}</div><div class="l">Views 7d</div></div>
  <div class="sd"><div class="v">{nti_today}</div><div class="l">Scores Today</div></div>
  <div class="sd"><div class="v">{total_nti}</div><div class="l">All-Time Scores</div></div>
</div></div>

<div class="sc"><div class="st">Controls</div>
<div class="tabs">
  <div class="tab active" onclick="sT('banner')">Banner</div>
  <div class="tab" onclick="sT('modal')">Pop-Up</div>
  <div class="tab" onclick="sT('kills')">Kill Switches</div>
  <div class="tab" onclick="sT('pricing')">Pricing</div>
  <div class="tab" onclick="sT('copy')">Copy</div>
  <div class="tab" onclick="sT('admin')">Admin</div>
  <div class="tab" onclick="sT('scraper')">Scraper</div>
</div>

<div class="tc active" id="t-banner"><div class="cp">
  <h3>Site Banner — shows on every page</h3>
  <div class="ir"><label>On/Off</label><input type="checkbox" id="b-on" {"checked" if banner_on else ""} style="width:36px"></div>
  <div class="ir"><label>Text</label><input type="text" id="b-text" value="{e(banner_text)}" placeholder="🚀 First 100 users get 50% off"></div>
  <div class="ir"><label>Link</label><input type="text" id="b-link" value="{e(banner_link)}" placeholder="/signup"></div>
  <div class="ir"><label>Text</label><input type="color" id="b-color" value="{banner_color}"><label>BG</label><input type="color" id="b-bg" value="{banner_bg}"></div>
  <div class="pb" id="b-preview" style="background:{banner_bg};color:{banner_color}">{e(banner_text) or "Banner preview..."}</div>
  <button class="btn" onclick="sB()">DEPLOY BANNER</button>
  <button class="btn btn-r btn-s" onclick="kB()" style="margin-left:8px">KILL</button>
</div></div>

<div class="tc" id="t-modal"><div class="cp">
  <h3>Pop-Up Modal — shows once per visitor</h3>
  <div class="ir"><label>Active</label><input type="checkbox" id="m-on" {"checked" if modal.get("on") else ""} style="width:36px"></div>
  <div class="ir"><label>Title</label><input type="text" id="m-title" value="{e(modal.get('title',''))}" placeholder="Welcome to Artifact Zero"></div>
  <div class="ir" style="align-items:start"><label>Body</label><textarea id="m-body" placeholder="Score your next email before you send it.">{e(modal.get('body',''))}</textarea></div>
  <div class="ir"><label>CTA</label><input type="text" id="m-cta" value="{e(modal.get('cta',''))}" placeholder="Try SafeCheck →"></div>
  <div class="ir"><label>Link</label><input type="text" id="m-link" value="{e(modal.get('cta_link',''))}" placeholder="/safecheck"></div>
  <div class="ir"><label>Pages</label><input type="text" id="m-pages" value="{e(modal.get('pages',''))}" placeholder="/ , /examples (* for all)"></div>
  <button class="btn" onclick="sM()">DEPLOY POP-UP</button>
  <button class="btn btn-r btn-s" onclick="kM()" style="margin-left:8px">KILL</button>
</div></div>

<div class="tc" id="t-kills"><div class="cp">
  <h3>Feature Switches — off = disabled for visitors</h3>
  {kill_html}
</div></div>

<div class="tc" id="t-pricing"><div class="cp">
  <h3>Pricing Controls</h3>
  <div class="ir"><label>Pack 1</label><input type="text" id="p1n" value="{e(pricing.get('pack1_name','Starter'))}" style="width:30%"><input type="text" id="p1p" value="{e(pricing.get('pack1_price','5'))}" placeholder="$" style="width:20%"><input type="text" id="p1s" value="{e(pricing.get('pack1_scores','25'))}" placeholder="scores" style="width:20%"></div>
  <div class="ir"><label>Pack 2</label><input type="text" id="p2n" value="{e(pricing.get('pack2_name','Pro'))}" style="width:30%"><input type="text" id="p2p" value="{e(pricing.get('pack2_price','15'))}" placeholder="$" style="width:20%"><input type="text" id="p2s" value="{e(pricing.get('pack2_scores','100'))}" placeholder="scores" style="width:20%"></div>
  <div class="ir"><label>Pack 3</label><input type="text" id="p3n" value="{e(pricing.get('pack3_name','Team'))}" style="width:30%"><input type="text" id="p3p" value="{e(pricing.get('pack3_price','49'))}" placeholder="$" style="width:20%"><input type="text" id="p3s" value="{e(pricing.get('pack3_scores','500'))}" placeholder="scores" style="width:20%"></div>
  <div class="ir"><label>Promo</label><input type="text" id="pc" value="{e(pricing.get('promo_code',''))}" placeholder="EARLY100"><input type="text" id="pp" value="{e(pricing.get('promo_pct',''))}" placeholder="% off" style="width:20%"></div>
  <button class="btn" onclick="sP()">UPDATE PRICING</button>
</div></div>

<div class="tc" id="t-copy"><div class="cp">
  <h3>Live Copy — change page text without deploying</h3>
  <div class="ir"><label>Hero H1</label><input type="text" id="c-h1" value="{e(copy_ov.get('hero_h1',''))}" placeholder="Score your message before you send it."></div>
  <div class="ir"><label>Hero Sub</label><input type="text" id="c-sub" value="{e(copy_ov.get('hero_sub',''))}" placeholder="NTI finds what humans miss."></div>
  <div class="ir"><label>CTA Btn</label><input type="text" id="c-cta" value="{e(copy_ov.get('cta_btn',''))}" placeholder="SafeCheck ✓"></div>
  <div class="ir"><label>Custom</label><input type="text" id="c-sel" value="{e(copy_ov.get('custom_selector',''))}" placeholder=".tagline" style="width:35%"><input type="text" id="c-val" value="{e(copy_ov.get('custom_value',''))}" placeholder="New text" style="width:60%"></div>
  <button class="btn" onclick="sC()">UPDATE COPY</button>
</div></div>

<div class="tc" id="t-admin"><div class="cp">
  <h3>Admin Controls</h3>
  <div class="ir"><label>Promote</label><input type="text" id="a-email" placeholder="email@example.com"><button class="btn btn-s" onclick="pA()">MAKE ADMIN</button></div>
  <p style="color:var(--m);font-size:10px;margin-top:8px">Grants admin role to an existing user.</p>
</div></div>

<div class="tc" id="t-scraper"><div class="cp">
  <h3>Fortune 500 + VC Fund Scraper</h3>
  <p style="color:var(--m);font-size:11px;margin-bottom:12px">Re-scrapes corporate pages and re-scores. Takes 5-15 minutes.</p>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <button class="btn" onclick="runScrape('both',50)">RESCRAPE ALL (50)</button>
    <button class="btn btn-s" onclick="runScrape('f500',50)">F500 ONLY</button>
    <button class="btn btn-s" onclick="runScrape('vc',30)">VC ONLY</button>
    <button class="btn btn-s" onclick="runScrape('both',5)">TEST (5)</button>
  </div>
  <div id="scrape-status" style="margin-top:12px;font-family:monospace;font-size:11px;color:var(--m);min-height:24px"></div>
</div></div>
</div>

<div class="sc"><div class="st">Traffic — Today</div>
<div class="cg2">
<div><table><tr><th>Page</th><th>Hits</th><th>Visitors</th></tr>{page_html}</table></div>
<div><table><tr><th>Hour</th><th>Hits</th><th>Visitors</th><th></th></tr>{hour_html}</table></div>
</div></div>

<div class="sc"><div class="st">Visitors — 24h</div>
<table><tr><th>IP</th><th>Hits</th><th>Pages</th><th>Last Seen</th><th>Paths</th><th>Referrer</th></tr>{visitor_html}</table></div>

<div class="sc"><div class="st">Recent Scores</div>
<table><tr><th>Time</th><th>IP</th><th>Input</th><th>NII</th><th>Latency</th></tr>{nti_html}</table></div>

<div style="margin-top:40px;padding:16px;border-top:1px solid var(--b);color:var(--m);font-size:10px;text-align:center">Artifact Zero Labs · Cockpit · {utc_now()[:19]} UTC</div>
</div>
<div class="toast" id="toast">Saved ✓</div>

<script>
const TP='{tp}';
function aU(p){{const s=p.includes('?')?'&':'?';return TP?p+s+TP.replace('?',''):p}}
function toast(m){{const t=document.getElementById('toast');t.textContent=m||'Saved ✓';t.style.display='block';setTimeout(()=>t.style.display='none',2000)}}
function sT(n){{document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.tc').forEach(t=>t.classList.remove('active'));event.target.classList.add('active');document.getElementById('t-'+n).classList.add('active')}}
function uBP(){{const p=document.getElementById('b-preview');p.textContent=document.getElementById('b-text').value||'Banner preview...';p.style.color=document.getElementById('b-color').value;p.style.background=document.getElementById('b-bg').value}}
document.getElementById('b-text').addEventListener('input',uBP);
document.getElementById('b-color').addEventListener('input',uBP);
document.getElementById('b-bg').addEventListener('input',uBP);
function sB(){{fetch(aU('/az-cockpit/api/banner'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{on:document.getElementById('b-on').checked,text:document.getElementById('b-text').value,color:document.getElementById('b-color').value,bg:document.getElementById('b-bg').value,link:document.getElementById('b-link').value}})}}).then(r=>r.json()).then(()=>toast('Banner deployed'))}}
function kB(){{fetch(aU('/az-cockpit/api/banner'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{on:false}})}}).then(r=>r.json()).then(()=>{{document.getElementById('b-on').checked=false;toast('Banner killed')}})}}
function sM(){{fetch(aU('/az-cockpit/api/modal'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{on:document.getElementById('m-on').checked,title:document.getElementById('m-title').value,body:document.getElementById('m-body').value,cta:document.getElementById('m-cta').value,cta_link:document.getElementById('m-link').value,pages:document.getElementById('m-pages').value}})}}).then(r=>r.json()).then(()=>toast('Pop-up deployed'))}}
function kM(){{fetch(aU('/az-cockpit/api/modal'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{on:false}})}}).then(r=>r.json()).then(()=>{{document.getElementById('m-on').checked=false;toast('Pop-up killed')}})}}
function tK(f,k){{const p={{}};p[f]=k;fetch(aU('/az-cockpit/api/kills'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(p)}}).then(r=>r.json()).then(()=>toast(f+(k?' DISABLED':' ENABLED')))}}
function sP(){{fetch(aU('/az-cockpit/api/pricing'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{pack1_name:document.getElementById('p1n').value,pack1_price:document.getElementById('p1p').value,pack1_scores:document.getElementById('p1s').value,pack2_name:document.getElementById('p2n').value,pack2_price:document.getElementById('p2p').value,pack2_scores:document.getElementById('p2s').value,pack3_name:document.getElementById('p3n').value,pack3_price:document.getElementById('p3p').value,pack3_scores:document.getElementById('p3s').value,promo_code:document.getElementById('pc').value,promo_pct:document.getElementById('pp').value}})}}).then(r=>r.json()).then(()=>toast('Pricing updated'))}}
function sC(){{fetch(aU('/az-cockpit/api/copy'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{hero_h1:document.getElementById('c-h1').value,hero_sub:document.getElementById('c-sub').value,cta_btn:document.getElementById('c-cta').value,custom_selector:document.getElementById('c-sel').value,custom_value:document.getElementById('c-val').value}})}}).then(r=>r.json()).then(()=>toast('Copy updated'))}}
function pA(){{const em=document.getElementById('a-email').value;if(!em)return;if(!confirm('Grant admin to '+em+'?'))return;fetch(aU('/az-cockpit/api/set-admin-email'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email:em}})}}).then(r=>r.json()).then(d=>toast(d.affected?'Admin granted':'User not found'))}}
function runScrape(target,limit){{const st=document.getElementById('scrape-status');st.textContent='Starting scrape...';st.style.color='#f59e0b';fetch(aU('/az-cockpit/api/rescrape'),{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{target:target,limit:limit}})}}).then(r=>r.json()).then(d=>{{if(d.error){{st.textContent=d.error;st.style.color='#ef4444';}}else{{st.textContent='Running: '+target+' (limit '+limit+')... started '+d.started;st.style.color='#00e89c';pollScrape();}}}}).catch(e=>{{st.textContent='Error: '+e;st.style.color='#ef4444';}})}}
function pollScrape(){{const st=document.getElementById('scrape-status');const iv=setInterval(()=>{{fetch(aU('/az-cockpit/api/rescrape-status')).then(r=>r.json()).then(d=>{{if(d.running){{st.textContent='⏳ Scraping... started '+d.started;st.style.color='#f59e0b';}}else if(d.last_result){{st.textContent='✓ '+d.last_result;st.style.color='#00e89c';clearInterval(iv);}}}})}},5000)}}
setTimeout(()=>location.reload(),60000);
</script></body></html>'''


COCKPIT_LOGIN_HTML = '''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Cockpit</title>
<style>
body{background:#0a0c10;color:#e8eaf0;font-family:'Courier New',monospace;display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#12151b;border:1px solid #252a35;border-radius:8px;padding:32px;width:320px;text-align:center}
h1{font-size:14px;color:#00e89c;letter-spacing:3px;margin-bottom:20px}
input{width:100%;padding:10px;background:#1a1e27;border:1px solid #252a35;border-radius:4px;color:#e8eaf0;font-family:inherit;margin-bottom:10px;font-size:13px}
input:focus{border-color:#00e89c;outline:none}
button{width:100%;padding:10px;background:#00e89c;color:#000;border:none;border-radius:4px;cursor:pointer;font-family:inherit;font-size:12px;font-weight:bold;letter-spacing:1px;text-transform:uppercase}
</style></head><body>
<div class="box"><h1>COCKPIT</h1>
<p style="color:#6b7280;font-size:11px;margin-bottom:16px">Admin access required</p>
<form method="GET"><input type="password" name="token" placeholder="Admin token" autofocus><button type="submit">Enter</button></form>
</div></body></html>'''


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
            from f500_scraper import lambda_handler
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


def init_admin(app):
    init_analytics_db()
    track_request(app)
    app.register_blueprint(admin)
    print("[COCKPIT] Live at /az-cockpit")
