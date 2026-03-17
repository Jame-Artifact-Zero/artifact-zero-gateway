# gateway_routes.py
# Admin-gated Gateway test interface.
# Route: GET /gateway
#
# Access rules:
#   - Must be logged in (session["user_id"])
#   - Must have role == "admin" (session["role"])
#   - Returns 403 with redirect to login otherwise
#
# Registration in app.py:
#   from gateway_routes import gateway_bp
#   app.register_blueprint(gateway_bp)

from flask import Blueprint, render_template, session, redirect, url_for, jsonify

gateway_bp = Blueprint("gateway_bp", __name__)


def _require_admin():
    """
    Returns None if user is admin. Returns a redirect/error response otherwise.
    Checks session["user_id"] and session["role"] == "admin".
    """
    uid = session.get("user_id")
    if not uid:
        return redirect("/login?next=/gateway")

    role = session.get("role", "user")
    if role != "admin":
        return jsonify({"error": "Admin access required."}), 403

    return None


@gateway_bp.route("/gateway")
def gateway_page():
    """
    Admin-only gateway test interface.
    Renders gateway.html which wires directly to /api/v1/relay/session.
    All API calls are made client-side — AZ key and provider key stay in
    the browser session only (never stored server-side from this page).
    """
    auth_error = _require_admin()
    if auth_error is not None:
        return auth_error

    return render_template("gateway.html")
