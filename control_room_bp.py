"""
control_room_bp.py
Blueprint for NTI Control Room and Landing Page.
Routes: /chat, /landing, /lab
Safe to remove: app.py uses try/except ImportError pattern.
"""

from flask import Blueprint, render_template

control_room_bp = Blueprint("control_room", __name__)


@control_room_bp.route("/chat")
def chat():
    """NTI Control Room — live-scored AI chat."""
    try:
        return render_template("control-room.html")
    except Exception:
        return "NTI Control Room — coming soon.", 200


@control_room_bp.route("/landing")
def landing():
    """NTI Landing — document showcase and story."""
    try:
        return render_template("nti-landing.html")
    except Exception:
        return "NTI Landing — coming soon.", 200


@control_room_bp.route("/lab")
def lab():
    """NTI Governance Lab — standalone analysis tool."""
    try:
        return render_template("nti-governance-lab.html")
    except Exception:
        return "NTI Governance Lab — coming soon.", 200
