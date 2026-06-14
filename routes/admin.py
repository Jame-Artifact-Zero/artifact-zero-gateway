from flask import Blueprint, jsonify, session

import db as database


admin_bp = Blueprint("admin_routes", __name__)


@admin_bp.app_context_processor
def inject_auth_state():
    """Make logged_in and user_id available in all templates."""
    uid = session.get("user_id")

    if uid:
        return {
            "logged_in": True,
            "user_id": uid,
        }

    return {
        "logged_in": False,
        "user_id": None,
    }


@admin_bp.route("/api/auth/status")
def auth_status():
    """Lightweight endpoint for JS nav state check."""
    uid = session.get("user_id")

    if uid:
        return jsonify({"logged_in": True})

    return jsonify({"logged_in": False})


@admin_bp.route("/health/db")
def health_db():
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        cur.execute("SELECT current_database(), version()")
        row = cur.fetchone()
        conn.close()

        return jsonify({
            "db": "postgresql",
            "database": row[0],
            "version": row[1][:40],
        })


    except Exception as e:
        return jsonify({
            "db": "error",
            "detail": str(e),
        }), 500


