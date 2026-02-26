"""
csrf.py — Lightweight CSRF protection for Artifact Zero.
Generates a per-session token, validates on POST forms.
"""
import os
import hmac
import hashlib
from functools import wraps
from flask import session, request, abort, g


def _get_csrf_token():
    """Get or create a CSRF token for the current session."""
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(32).hex()
    return session["csrf_token"]


def csrf_token_input():
    """Return an HTML hidden input with the current CSRF token."""
    return f'<input type="hidden" name="csrf_token" value="{_get_csrf_token()}">'


def validate_csrf():
    """Check that the submitted csrf_token matches session. Call on POST routes."""
    token = request.form.get("csrf_token") or ""
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(token, expected):
        abort(403)


def csrf_protect(f):
    """Decorator: validate CSRF on POST, pass through on GET."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method == "POST":
            validate_csrf()
        return f(*args, **kwargs)
    return wrapper


def init_csrf(app):
    """Register csrf_token as a Jinja global so templates can use {{ csrf_token_input() }}."""
    app.jinja_env.globals["csrf_token_input"] = csrf_token_input
    app.jinja_env.globals["csrf_token"] = _get_csrf_token
