"""
user_feeds.py — Custom Feed Dashboard Backend
═══════════════════════════════════════════════

Lets logged-in users build their own live feed dashboards.
Each user can add RSS sources, political races, and candidate
pages to their personal dashboard.

Integrates with existing az_relay auth (az_users table).

Routes:
  GET  /api/user/feeds          — Get user's custom feed config
  POST /api/user/feeds          — Add a feed source
  DELETE /api/user/feeds/<id>   — Remove a feed source
  POST /api/user/feeds/candidate — Add a candidate to track
  GET  /api/user/feeds/candidate — Get tracked candidates
  GET  /app/my-feed             — Serve personalized feed page

Database tables:
  user_feed_sources   — RSS sources per user
  user_feed_candidates — Candidates per user
"""

import os
import json
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session, render_template

user_feeds_bp = Blueprint("user_feeds", __name__)

# ─── DB HELPERS ───
# Uses the same db connection pattern as az_relay
DB_MODE = os.getenv("AZ_DB_MODE", "sqlite")

def db():
    if DB_MODE == "postgres":
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(os.getenv("DATABASE_URL"))
        conn.autocommit = False
        return conn
    else:
        import sqlite3
        conn = sqlite3.connect(os.getenv("AZ_RELAY_DB", "az_relay.db"))
        conn.row_factory = sqlite3.Row
        return conn


def init_user_feeds_db():
    conn = db()
    cur = conn.cursor()

    if DB_MODE == "postgres":
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_feed_sources (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                category TEXT DEFAULT 'custom',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, rss_url)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_feed_candidates (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                candidate_name TEXT NOT NULL,
                office TEXT,
                party TEXT,
                jurisdiction TEXT,
                election_date TEXT,
                statements_json TEXT DEFAULT '[]',
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ufs_user ON user_feed_sources(user_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ufc_user ON user_feed_candidates(user_id)
        """)
        conn.commit()
    else:
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS user_feed_sources (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                rss_url TEXT NOT NULL,
                category TEXT DEFAULT 'custom',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, rss_url)
            );
            CREATE TABLE IF NOT EXISTS user_feed_candidates (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                candidate_name TEXT NOT NULL,
                office TEXT,
                party TEXT,
                jurisdiction TEXT,
                election_date TEXT,
                statements_json TEXT DEFAULT '[]',
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ufs_user ON user_feed_sources(user_id);
            CREATE INDEX IF NOT EXISTS idx_ufc_user ON user_feed_candidates(user_id);
        """)
        conn.commit()

    conn.close()


init_user_feeds_db()


# ─── AUTH CHECK ───
def get_user_id():
    """Get current user from session. Returns None if not logged in."""
    return session.get("az_user_id")


def require_auth(f):
    """Decorator: requires logged-in user."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = get_user_id()
        if not uid:
            return jsonify({"error": "Login required", "login_url": "/app/relay"}), 401
        return f(uid, *args, **kwargs)
    return decorated


# ─── FEED SOURCE ROUTES ───

@user_feeds_bp.route("/api/user/feeds", methods=["GET"])
@require_auth
def get_feeds(user_id):
    """Get all feed sources for the logged-in user."""
    conn = db()
    cur = conn.cursor()

    if DB_MODE == "postgres":
        cur.execute(
            "SELECT id, name, rss_url, category, active, created_at FROM user_feed_sources WHERE user_id = %s ORDER BY created_at",
            (user_id,)
        )
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    else:
        cur.execute(
            "SELECT id, name, rss_url, category, active, created_at FROM user_feed_sources WHERE user_id = ? ORDER BY created_at",
            (user_id,)
        )
        rows = [dict(r) for r in cur.fetchall()]

    conn.close()

    # Merge with default sources
    defaults = [
        {"id": "default-bbc", "name": "BBC World", "rss_url": "https://feeds.bbci.co.uk/news/world/rss.xml", "category": "news", "default": True},
        {"id": "default-npr", "name": "NPR News", "rss_url": "https://feeds.npr.org/1001/rss.xml", "category": "news", "default": True},
        {"id": "default-wate", "name": "WATE 6", "rss_url": "https://wate.com/feed/", "category": "local", "default": True},
    ]

    return jsonify({"sources": rows, "defaults": defaults})


@user_feeds_bp.route("/api/user/feeds", methods=["POST"])
@require_auth
def add_feed(user_id):
    """Add a custom RSS feed source."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    rss_url = (data.get("rss_url") or "").strip()
    category = (data.get("category") or "custom").strip()

    if not name or not rss_url:
        return jsonify({"error": "Name and rss_url required"}), 400

    if not rss_url.startswith("http"):
        return jsonify({"error": "Invalid URL"}), 400

    feed_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = db()
    cur = conn.cursor()

    try:
        if DB_MODE == "postgres":
            cur.execute(
                "INSERT INTO user_feed_sources (id, user_id, name, rss_url, category, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (feed_id, user_id, name, rss_url, category, now)
            )
        else:
            cur.execute(
                "INSERT INTO user_feed_sources (id, user_id, name, rss_url, category, created_at) VALUES (?,?,?,?,?,?)",
                (feed_id, user_id, name, rss_url, category, now)
            )
        conn.commit()
    except Exception as e:
        conn.close()
        if "UNIQUE" in str(e).upper():
            return jsonify({"error": "Feed already added"}), 409
        return jsonify({"error": str(e)}), 500

    conn.close()
    return jsonify({"ok": True, "id": feed_id, "name": name})


@user_feeds_bp.route("/api/user/feeds/<feed_id>", methods=["DELETE"])
@require_auth
def remove_feed(user_id, feed_id):
    """Remove a feed source."""
    conn = db()
    cur = conn.cursor()

    if DB_MODE == "postgres":
        cur.execute("DELETE FROM user_feed_sources WHERE id = %s AND user_id = %s", (feed_id, user_id))
    else:
        cur.execute("DELETE FROM user_feed_sources WHERE id = ? AND user_id = ?", (feed_id, user_id))

    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ─── CANDIDATE TRACKING ROUTES ───

@user_feeds_bp.route("/api/user/feeds/candidate", methods=["POST"])
@require_auth
def add_candidate(user_id):
    """Add a candidate to track."""
    data = request.get_json() or {}
    name = (data.get("candidate_name") or "").strip()
    office = (data.get("office") or "").strip()
    party = (data.get("party") or "").strip()
    jurisdiction = (data.get("jurisdiction") or "").strip()
    election_date = (data.get("election_date") or "").strip()
    statements = data.get("statements", [])

    if not name:
        return jsonify({"error": "Candidate name required"}), 400

    cand_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = db()
    cur = conn.cursor()

    if DB_MODE == "postgres":
        cur.execute(
            """INSERT INTO user_feed_candidates
            (id, user_id, candidate_name, office, party, jurisdiction, election_date, statements_json, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (cand_id, user_id, name, office, party, jurisdiction, election_date, json.dumps(statements), now)
        )
    else:
        cur.execute(
            """INSERT INTO user_feed_candidates
            (id, user_id, candidate_name, office, party, jurisdiction, election_date, statements_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (cand_id, user_id, name, office, party, jurisdiction, election_date, json.dumps(statements), now)
        )

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": cand_id})


@user_feeds_bp.route("/api/user/feeds/candidate", methods=["GET"])
@require_auth
def get_candidates(user_id):
    """Get all tracked candidates for user."""
    conn = db()
    cur = conn.cursor()

    if DB_MODE == "postgres":
        cur.execute(
            "SELECT * FROM user_feed_candidates WHERE user_id = %s AND active = TRUE ORDER BY created_at",
            (user_id,)
        )
        rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
    else:
        cur.execute(
            "SELECT * FROM user_feed_candidates WHERE user_id = ? AND active = 1 ORDER BY created_at",
            (user_id,)
        )
        rows = [dict(r) for r in cur.fetchall()]

    # Parse statements JSON
    for row in rows:
        try:
            row["statements"] = json.loads(row.get("statements_json") or "[]")
        except:
            row["statements"] = []

    conn.close()
    return jsonify({"candidates": rows})


@user_feeds_bp.route("/api/user/feeds/candidate/<cand_id>/statement", methods=["POST"])
@require_auth
def add_statement(user_id, cand_id):
    """Add a statement to a tracked candidate."""
    data = request.get_json() or {}
    text = (data.get("text") or "").strip()
    source = (data.get("source") or "").strip()

    if not text:
        return jsonify({"error": "Statement text required"}), 400

    conn = db()
    cur = conn.cursor()

    # Get existing statements
    if DB_MODE == "postgres":
        cur.execute("SELECT statements_json FROM user_feed_candidates WHERE id = %s AND user_id = %s", (cand_id, user_id))
    else:
        cur.execute("SELECT statements_json FROM user_feed_candidates WHERE id = ? AND user_id = ?", (cand_id, user_id))

    row = cur.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Candidate not found"}), 404

    statements = json.loads(row[0] if isinstance(row, tuple) else row["statements_json"] or "[]")
    statements.append({"text": text, "source": source, "added_at": datetime.now(timezone.utc).isoformat()})

    if DB_MODE == "postgres":
        cur.execute("UPDATE user_feed_candidates SET statements_json = %s WHERE id = %s AND user_id = %s",
                    (json.dumps(statements), cand_id, user_id))
    else:
        cur.execute("UPDATE user_feed_candidates SET statements_json = ? WHERE id = ? AND user_id = ?",
                    (json.dumps(statements), cand_id, user_id))

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "statement_count": len(statements)})


# ─── PAGE ROUTE ───

@user_feeds_bp.route("/app/my-feed")
def my_feed_page():
    """Serve the personalized feed page."""
    try:
        return render_template("my-feed.html")
    except Exception:
        return "Custom feed page not found. Ensure my-feed.html is in templates/", 404


# ═══════════════════════════════════════
# INTEGRATION
# ═══════════════════════════════════════
# Add to app.py:
#
#   from user_feeds import user_feeds_bp
#   app.register_blueprint(user_feeds_bp)
#
# That's it. Tables auto-create on import.
