"""
self_service.py — Customer Self-Implementation Portal
======================================================
THE BOTTLENECK KILLER.

Problem: Enterprise SaaS companies hire 10-50 implementation engineers
to onboard customers. That costs $1M-5M/year in salary alone.
At Artifact Zero's stage, hiring an implementation team before revenue
is a death sentence.

Solution: The customer designates their own Implementation Lead.
That person gets a scoped admin portal with guided steps, validation
at every stage, and zero ability to break things.

Flow:
  1. Customer signs contract, pays via Stripe
  2. System auto-provisions their org, creates Implementation Lead invite
  3. Implementation Lead receives email with magic link
  4. They land on /setup — a step-by-step wizard
  5. Each step validates before unlocking the next
  6. When all steps complete, org goes live
  7. You (Jame) get a Slack/email notification: "Bethany is live"
  8. You never touched it

What the Implementation Lead can do:
  - Configure SSO/identity provider
  - Upload governance protocols (relay rules)
  - Invite their staff users
  - Set role permissions
  - Configure webhook endpoints
  - Set up email relay addresses
  - Test relay with sandbox mode
  - View audit logs for their org
  - Manage API keys
  - View usage/billing dashboard

What the Implementation Lead CANNOT do:
  - Access other orgs' data
  - Modify billing/plan (only org owner)
  - Disable audit logging
  - Access raw database
  - Change their own permission level
  - See system-level configuration

Usage:
    from self_service import self_service_bp, provision_customer
    app.register_blueprint(self_service_bp)
"""

import os
import uuid
import json
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import Blueprint, request, jsonify, render_template_string

from db import db_connection, param_placeholder

self_service_bp = Blueprint("self_service", __name__)


def self_service_db_init():
    """Create self-service portal tables."""
    with db_connection() as conn:
        cur = conn.cursor()

        # ── IMPLEMENTATION SETUP TRACKER ──
        # Each org goes through a defined setup sequence.
        # Steps unlock sequentially. Each step has validation.
        cur.execute("""
        CREATE TABLE IF NOT EXISTS setup_steps (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            step_key TEXT NOT NULL,
            step_order INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'locked',
            completed_by TEXT,
            completed_at TEXT,
            validation_json TEXT DEFAULT '{}',
            config_json TEXT DEFAULT '{}'
        )
        """)

        # ── IMPLEMENTATION INVITES ──
        # Magic link invites for the customer's designated implementer
        cur.execute("""
        CREATE TABLE IF NOT EXISTS implementation_invites (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'implementation_lead',
            token TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            accepted_at TEXT,
            expires_at TEXT NOT NULL
        )
        """)

        # ── SANDBOX ENVIRONMENT ──
        # Each org gets a sandbox for testing before go-live
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sandbox_sessions (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            input_text TEXT NOT NULL,
            nti_result_json TEXT,
            relay_result TEXT,
            created_at TEXT NOT NULL
        )
        """)

        # ── GOVERNANCE PROTOCOL TEMPLATES ──
        # Pre-built protocol templates customers can start from
        cur.execute("""
        CREATE TABLE IF NOT EXISTS protocol_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            vertical TEXT NOT NULL,
            description TEXT,
            rules_json TEXT NOT NULL,
            is_public INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """)

        # ── ORG PROTOCOLS (customer-configured) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS org_protocols (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            name TEXT NOT NULL,
            rules_json TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        # ── SETUP COMPLETION LOG ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS setup_completion_log (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            overall_status TEXT NOT NULL DEFAULT 'in_progress',
            started_at TEXT NOT NULL,
            completed_at TEXT,
            go_live_at TEXT,
            implementation_lead_id TEXT,
            notes TEXT
        )
        """)

        USE_PG = bool(os.getenv("DATABASE_URL"))
        if USE_PG:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_setup_org ON setup_steps(org_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_invite_token ON implementation_invites(token)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_invite_org ON implementation_invites(org_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sandbox_org ON sandbox_sessions(org_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_protocols_org ON org_protocols(org_id)")

        conn.commit()
    print("[SELF-SERVICE] Tables initialized")


# ══════════════════════════════════════════════
# PROVISIONING — happens when customer pays
# ══════════════════════════════════════════════
SETUP_STEPS = [
    {
        "key": "welcome",
        "order": 1,
        "title": "Welcome & Account Verification",
        "description": "Verify your email and set your organization display name. This takes 30 seconds.",
        "auto_complete": False,
    },
    {
        "key": "identity",
        "order": 2,
        "title": "Identity Provider (Optional)",
        "description": "Connect your SSO provider (Okta, Azure AD, Google Workspace) so your staff can log in with corporate credentials. Skip this if your team will use email/password.",
        "auto_complete": False,
    },
    {
        "key": "team",
        "order": 3,
        "title": "Invite Your Team",
        "description": "Add the staff members who will use the relay. You can assign roles: Admin, User, or Viewer. Minimum: 1 additional user to validate the setup.",
        "auto_complete": False,
    },
    {
        "key": "protocols",
        "order": 4,
        "title": "Configure Governance Protocols",
        "description": "Choose a protocol template for your industry or create custom rules. Protocols define what the NTI relay checks: false commitments, hedge words, dominance patterns, etc.",
        "auto_complete": False,
    },
    {
        "key": "integration",
        "order": 5,
        "title": "API & Webhook Setup",
        "description": "Generate API keys for programmatic access. Configure webhook URLs to receive relay results in your existing systems. Both are optional for web-only usage.",
        "auto_complete": False,
    },
    {
        "key": "sandbox",
        "order": 6,
        "title": "Sandbox Testing",
        "description": "Test the relay with sample text. Run at least 3 test relays to validate your protocols work correctly. Results are not billed.",
        "auto_complete": False,
    },
    {
        "key": "billing_confirm",
        "order": 7,
        "title": "Billing Confirmation",
        "description": "Review your plan, usage limits, and billing details. Confirm everything looks correct.",
        "auto_complete": False,
    },
    {
        "key": "go_live",
        "order": 8,
        "title": "Go Live",
        "description": "Activate your organization. All users can now access the relay in production. Audit logging begins. Usage metering begins.",
        "auto_complete": False,
    },
]


def provision_customer(org_id: str, org_name: str, plan: str,
                       implementation_lead_email: str,
                       implementation_lead_name: str = "") -> dict:
    """
    Called when a new customer pays. Auto-provisions everything they need.
    Returns the invite URL for their Implementation Lead.

    This is the ONLY function Jame calls. Everything else is self-service.
    """
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    invite_token = secrets.token_urlsafe(48)
    invite_expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()

        # 1. Create setup steps for this org
        for step in SETUP_STEPS:
            step_id = str(uuid.uuid4())
            status = "available" if step["order"] == 1 else "locked"
            cur.execute(f"""
                INSERT INTO setup_steps (id, org_id, step_key, step_order, title, description, status)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
            """, (step_id, org_id, step["key"], step["order"], step["title"], step["description"], status))

        # 2. Create implementation lead invite
        invite_id = str(uuid.uuid4())
        cur.execute(f"""
            INSERT INTO implementation_invites
                (id, org_id, email, role, token, status, created_at, expires_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, 'pending', {p}, {p})
        """, (invite_id, org_id, implementation_lead_email,
              "implementation_lead", invite_token, now, invite_expires))

        # 3. Create setup completion tracker
        cur.execute(f"""
            INSERT INTO setup_completion_log (id, org_id, overall_status, started_at)
            VALUES ({p}, {p}, 'provisioned', {p})
        """, (str(uuid.uuid4()), org_id, now))

        # 4. Seed protocol templates for their vertical
        _seed_protocol_templates(cur, p)

        conn.commit()

    base_url = os.getenv("BASE_URL", "https://dontgofulltilt.com")
    invite_url = f"{base_url}/setup/accept?token={invite_token}"

    return {
        "org_id": org_id,
        "org_name": org_name,
        "plan": plan,
        "implementation_lead_email": implementation_lead_email,
        "invite_url": invite_url,
        "invite_expires": invite_expires,
        "setup_steps": len(SETUP_STEPS),
        "message": f"Send this link to {implementation_lead_email}. They complete setup. You do nothing."
    }


def _seed_protocol_templates(cur, p):
    """Pre-load industry-specific governance protocol templates."""
    templates = [
        {
            "name": "Healthcare — HIPAA Communication Guard",
            "vertical": "healthcare",
            "description": "Prevents false commitment in patient communication. Flags hedge words that create ambiguity in treatment plans. Detects dominance patterns in provider-patient text.",
            "rules": {
                "udds_enabled": True,
                "dce_enabled": True,
                "cca_enabled": True,
                "severity_threshold": "medium",
                "block_on_false_commitment": True,
                "flag_hedge_words": True,
                "phi_detection": True,
            }
        },
        {
            "name": "Foster Care — Case Worker Communication",
            "vertical": "social_services",
            "description": "Governs AI-assisted communication between case workers, foster parents, and agencies. Prevents commitment to placement timelines that can't be guaranteed. Detects emotional manipulation patterns.",
            "rules": {
                "udds_enabled": True,
                "dce_enabled": True,
                "cca_enabled": True,
                "severity_threshold": "low",
                "block_on_false_commitment": True,
                "flag_hedge_words": True,
                "child_safety_mode": True,
                "emotional_escalation_detection": True,
            }
        },
        {
            "name": "Insurance — Claims Communication",
            "vertical": "insurance",
            "description": "Prevents false commitment on coverage decisions. Flags ambiguous denial language. Detects dominance/control patterns in adjuster communications.",
            "rules": {
                "udds_enabled": True,
                "dce_enabled": True,
                "cca_enabled": True,
                "severity_threshold": "medium",
                "block_on_false_commitment": True,
                "regulatory_language_check": True,
            }
        },
        {
            "name": "Legal — Contract & Correspondence",
            "vertical": "legal",
            "description": "Governs AI-drafted legal correspondence. Prevents unauthorized commitment. Detects liability-creating language patterns.",
            "rules": {
                "udds_enabled": True,
                "dce_enabled": True,
                "cca_enabled": True,
                "severity_threshold": "high",
                "block_on_false_commitment": True,
                "attorney_review_required": True,
            }
        },
        {
            "name": "General Enterprise — AI Output Governance",
            "vertical": "enterprise",
            "description": "Default governance for any enterprise using AI assistants. Catches false commitments, hedge patterns, and quality issues across all AI-generated text.",
            "rules": {
                "udds_enabled": True,
                "dce_enabled": True,
                "cca_enabled": True,
                "severity_threshold": "medium",
                "block_on_false_commitment": False,
                "flag_for_review": True,
            }
        },
    ]

    now = datetime.now(timezone.utc).isoformat()
    for t in templates:
        # Check if already exists
        cur.execute(f"SELECT id FROM protocol_templates WHERE name = {p}", (t["name"],))
        if not cur.fetchone():
            cur.execute(f"""
                INSERT INTO protocol_templates (id, name, vertical, description, rules_json, created_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p})
            """, (str(uuid.uuid4()), t["name"], t["vertical"], t["description"],
                  json.dumps(t["rules"]), now))


# ══════════════════════════════════════════════
# IMPLEMENTATION LEAD ROUTES
# ══════════════════════════════════════════════
@self_service_bp.route("/api/v1/setup/accept", methods=["POST"])
def accept_invite():
    """Accept implementation invite via magic link token. Creates user account."""
    payload = request.get_json() or {}
    token = payload.get("token", "")
    password = payload.get("password", "")
    display_name = payload.get("display_name", "")

    if not token or not password:
        return jsonify({"error": "token and password required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()

        # Find and validate invite
        cur.execute(f"""
            SELECT id, org_id, email, role, status, expires_at
            FROM implementation_invites WHERE token = {p}
        """, (token,))
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Invalid invite token"}), 404

        invite = dict(row) if hasattr(row, "keys") else dict(zip(
            ["id", "org_id", "email", "role", "status", "expires_at"], row))

        if invite["status"] != "pending":
            return jsonify({"error": "Invite already used"}), 409
        if invite["expires_at"] < now:
            return jsonify({"error": "Invite expired. Contact your account representative."}), 410

        # Create user account
        from auth import _hash_password
        user_id = str(uuid.uuid4())
        cur.execute(f"""
            INSERT INTO users (id, email, password_hash, display_name, org_id, role, created_at, updated_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (user_id, invite["email"], _hash_password(password),
              display_name or invite["email"].split("@")[0],
              invite["org_id"], invite["role"], now, now))

        # Create org membership as implementation_lead
        cur.execute(f"""
            INSERT INTO org_memberships (id, user_id, org_id, role, created_at)
            VALUES ({p}, {p}, {p}, 'implementation_lead', {p})
        """, (str(uuid.uuid4()), user_id, invite["org_id"], now))

        # Mark invite as accepted
        cur.execute(f"UPDATE implementation_invites SET status = 'accepted', accepted_at = {p} WHERE id = {p}",
                   (now, invite["id"]))

        # Update setup completion log with implementation lead
        cur.execute(f"""
            UPDATE setup_completion_log SET implementation_lead_id = {p}, overall_status = 'in_progress'
            WHERE org_id = {p}
        """, (user_id, invite["org_id"]))

        conn.commit()

    # Issue session token
    from auth import _create_session
    session_token = _create_session(user_id)

    return jsonify({
        "user_id": user_id,
        "org_id": invite["org_id"],
        "role": invite["role"],
        "token": session_token,
        "next": "/setup",
        "message": "Welcome. Your setup wizard is ready."
    }), 201


@self_service_bp.route("/api/v1/setup/status", methods=["GET"])
def setup_status():
    """Get current setup progress for the Implementation Lead's org."""
    user = getattr(request, "user", None)
    if not user:
        return jsonify({"error": "Auth required"}), 401

    org_id = user.get("org_id")
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT step_key, step_order, title, description, status, completed_at
            FROM setup_steps WHERE org_id = {p}
            ORDER BY step_order
        """, (org_id,))
        rows = cur.fetchall()

    steps = []
    for r in rows:
        s = dict(r) if hasattr(r, "keys") else dict(zip(
            ["step_key", "step_order", "title", "description", "status", "completed_at"], r))
        steps.append(s)

    completed = sum(1 for s in steps if s["status"] == "completed")
    total = len(steps)

    return jsonify({
        "org_id": org_id,
        "steps": steps,
        "completed": completed,
        "total": total,
        "progress_percent": int((completed / total) * 100) if total > 0 else 0,
        "overall_status": "complete" if completed == total else "in_progress"
    })


@self_service_bp.route("/api/v1/setup/step/<step_key>/complete", methods=["POST"])
def complete_step(step_key):
    """Mark a setup step as complete. Validates requirements, unlocks next step."""
    user = getattr(request, "user", None)
    if not user:
        return jsonify({"error": "Auth required"}), 401

    org_id = user.get("org_id")
    user_id = user.get("id")
    payload = request.get_json() or {}
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()

        # Get this step
        cur.execute(f"""
            SELECT id, step_key, step_order, status FROM setup_steps
            WHERE org_id = {p} AND step_key = {p}
        """, (org_id, step_key))
        step_row = cur.fetchone()

        if not step_row:
            return jsonify({"error": "Step not found"}), 404

        step = dict(step_row) if hasattr(step_row, "keys") else dict(zip(
            ["id", "step_key", "step_order", "status"], step_row))

        if step["status"] == "completed":
            return jsonify({"error": "Step already completed"}), 409
        if step["status"] == "locked":
            return jsonify({"error": "Complete previous steps first"}), 403

        # ── VALIDATE STEP-SPECIFIC REQUIREMENTS ──
        validation_errors = _validate_step(step_key, org_id, payload)
        if validation_errors:
            return jsonify({"error": "Validation failed", "issues": validation_errors}), 400

        # Mark complete
        cur.execute(f"""
            UPDATE setup_steps SET status = 'completed', completed_by = {p},
                completed_at = {p}, config_json = {p}
            WHERE id = {p}
        """, (user_id, now, json.dumps(payload), step["id"]))

        # Unlock next step
        next_order = step["step_order"] + 1
        cur.execute(f"""
            UPDATE setup_steps SET status = 'available'
            WHERE org_id = {p} AND step_order = {p} AND status = 'locked'
        """, (org_id, next_order))

        # Check if ALL steps are done
        cur.execute(f"""
            SELECT COUNT(*) FROM setup_steps WHERE org_id = {p} AND status != 'completed'
        """, (org_id,))
        remaining_row = cur.fetchone()
        remaining = remaining_row[0] if remaining_row else 1

        if remaining == 0:
            # ORG IS FULLY SET UP — GO LIVE
            cur.execute(f"""
                UPDATE setup_completion_log SET overall_status = 'complete',
                    completed_at = {p}, go_live_at = {p}
                WHERE org_id = {p}
            """, (now, now, org_id))

            # Activate the org
            cur.execute(f"""
                UPDATE organizations SET settings_json =
                    json_set(COALESCE(settings_json, '{{}}'), '$.is_live', 1)
                WHERE id = {p}
            """ if not os.getenv("DATABASE_URL") else f"""
                UPDATE organizations SET settings_json =
                    jsonb_set(COALESCE(settings_json::jsonb, '{{}}'::jsonb), '{{is_live}}', 'true')::text
                WHERE id = {p}
            """, (org_id,))

            _notify_go_live(org_id)

        conn.commit()

    return jsonify({
        "step": step_key,
        "status": "completed",
        "remaining": remaining,
        "go_live": remaining == 0,
    })


def _validate_step(step_key: str, org_id: str, config: dict) -> list:
    """Validate step-specific requirements. Returns list of errors (empty = valid)."""
    errors = []
    p = param_placeholder()

    if step_key == "welcome":
        if not config.get("org_display_name"):
            errors.append("Organization display name is required")
        if not config.get("contact_email"):
            errors.append("Primary contact email is required")

    elif step_key == "identity":
        # Optional step — skip is valid
        pass

    elif step_key == "team":
        # Must have invited at least 1 additional user
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM org_memberships WHERE org_id = {p}", (org_id,))
            row = cur.fetchone()
            count = row[0] if row else 0
        if count < 2:  # implementation lead + at least 1 more
            errors.append("Invite at least 1 team member before proceeding")

    elif step_key == "protocols":
        # Must have at least 1 active protocol
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM org_protocols WHERE org_id = {p} AND is_active = 1", (org_id,))
            row = cur.fetchone()
            count = row[0] if row else 0
        if count < 1:
            errors.append("Configure at least 1 governance protocol")

    elif step_key == "integration":
        # Optional — API keys and webhooks are not required
        pass

    elif step_key == "sandbox":
        # Must have run at least 3 sandbox tests
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM sandbox_sessions WHERE org_id = {p}", (org_id,))
            row = cur.fetchone()
            count = row[0] if row else 0
        if count < 3:
            errors.append(f"Run at least 3 sandbox tests ({count}/3 completed)")

    elif step_key == "billing_confirm":
        if not config.get("confirmed"):
            errors.append("You must confirm billing details")

    elif step_key == "go_live":
        if not config.get("acknowledged"):
            errors.append("Acknowledge that production usage and billing will begin")

    return errors


def _notify_go_live(org_id: str):
    """Notify Jame that a customer just self-onboarded and went live."""
    p = param_placeholder()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT name, slug, plan FROM organizations WHERE id = {p}", (org_id,))
        row = cur.fetchone()

    if row:
        org = dict(row) if hasattr(row, "keys") else dict(zip(["name", "slug", "plan"], row))
        print(json.dumps({
            "EVENT": "CUSTOMER_GO_LIVE",
            "org_id": org_id,
            "org_name": org.get("name"),
            "plan": org.get("plan"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "Send congratulations email. Check audit log for their sandbox tests."
        }))
    # In production: send to Slack webhook, email, PagerDuty, etc.


# ══════════════════════════════════════════════
# PROTOCOL MANAGEMENT (Implementation Lead)
# ══════════════════════════════════════════════
@self_service_bp.route("/api/v1/setup/protocols/templates", methods=["GET"])
def get_protocol_templates():
    """Get available protocol templates for the customer's vertical."""
    vertical = request.args.get("vertical")
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()
        if vertical:
            cur.execute(f"""
                SELECT id, name, vertical, description, rules_json
                FROM protocol_templates WHERE is_public = 1 AND vertical = {p}
            """, (vertical,))
        else:
            cur.execute("SELECT id, name, vertical, description, rules_json FROM protocol_templates WHERE is_public = 1")
        rows = cur.fetchall()

    templates = [dict(r) if hasattr(r, "keys") else dict(zip(
        ["id", "name", "vertical", "description", "rules_json"], r)) for r in rows]
    return jsonify({"templates": templates})


@self_service_bp.route("/api/v1/setup/protocols", methods=["POST"])
def create_org_protocol():
    """Create a governance protocol for the org (from template or custom)."""
    user = getattr(request, "user", None)
    if not user:
        return jsonify({"error": "Auth required"}), 401

    payload = request.get_json() or {}
    org_id = user.get("org_id")
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()

    # If starting from a template, load it
    template_id = payload.get("template_id")
    rules = payload.get("rules", {})

    if template_id and not rules:
        with db_connection() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT rules_json FROM protocol_templates WHERE id = {p}", (template_id,))
            row = cur.fetchone()
            if row:
                rules = json.loads(row["rules_json"] if hasattr(row, "keys") else row[0])

    if not rules:
        return jsonify({"error": "rules or template_id required"}), 400

    protocol_id = str(uuid.uuid4())
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO org_protocols (id, org_id, name, rules_json, created_by, created_at, updated_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
        """, (protocol_id, org_id, payload.get("name", "Default Protocol"),
              json.dumps(rules), user.get("id"), now, now))
        conn.commit()

    return jsonify({"protocol_id": protocol_id, "name": payload.get("name")}), 201


# ══════════════════════════════════════════════
# TEAM MANAGEMENT (Implementation Lead)
# ══════════════════════════════════════════════
@self_service_bp.route("/api/v1/setup/team/invite", methods=["POST"])
def invite_team_member():
    """Invite a staff member to the org. Implementation Lead capability."""
    user = getattr(request, "user", None)
    if not user:
        return jsonify({"error": "Auth required"}), 401

    payload = request.get_json() or {}
    email = (payload.get("email") or "").strip().lower()
    role = payload.get("role", "user")

    if not email:
        return jsonify({"error": "email required"}), 400
    if role not in ("user", "viewer", "admin"):
        return jsonify({"error": "role must be user, viewer, or admin"}), 400

    # Implementation leads cannot create other implementation leads or owners
    org_id = user.get("org_id")
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    invite_token = secrets.token_urlsafe(32)
    invite_expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    with db_connection() as conn:
        cur = conn.cursor()

        # Check if already invited or exists
        cur.execute(f"SELECT id FROM users WHERE email = {p} AND org_id = {p}", (email, org_id))
        if cur.fetchone():
            return jsonify({"error": "User already exists in this organization"}), 409

        cur.execute(f"""
            INSERT INTO implementation_invites (id, org_id, email, role, token, status, created_at, expires_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, 'pending', {p}, {p})
        """, (str(uuid.uuid4()), org_id, email, role, invite_token, now, invite_expires))
        conn.commit()

    base_url = os.getenv("BASE_URL", "https://dontgofulltilt.com")
    invite_url = f"{base_url}/join?token={invite_token}"

    return jsonify({"invited": email, "role": role, "invite_url": invite_url}), 201


# ══════════════════════════════════════════════
# SANDBOX TESTING (Implementation Lead)
# ══════════════════════════════════════════════
@self_service_bp.route("/api/v1/setup/sandbox/test", methods=["POST"])
def sandbox_test():
    """Run a test relay in sandbox mode. Not billed. Results stored for validation."""
    user = getattr(request, "user", None)
    if not user:
        return jsonify({"error": "Auth required"}), 401

    payload = request.get_json() or {}
    text = payload.get("text", "")
    if not text:
        return jsonify({"error": "text required"}), 400

    org_id = user.get("org_id")
    user_id = user.get("id")

    # Run NTI scoring
    from nti_engine import classify_tilt, compute_nii, detect_udds, detect_dce, detect_cca

    tilt = classify_tilt(text)
    nii = compute_nii(text)
    udds = detect_udds(text)
    dce = detect_dce(text)
    cca = detect_cca(text)

    result = {
        "tilt": tilt,
        "nii": nii,
        "udds": udds,
        "dce": dce,
        "cca": cca,
    }

    # Store sandbox session
    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO sandbox_sessions (id, org_id, user_id, input_text, nti_result_json, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})
        """, (str(uuid.uuid4()), org_id, user_id, text, json.dumps(result), now))
        conn.commit()

    return jsonify({
        "sandbox": True,
        "billed": False,
        "result": result,
        "message": "This is a sandbox test. Results are not billed."
    })


# ══════════════════════════════════════════════
# ORG DASHBOARD (Implementation Lead + Admins)
# ══════════════════════════════════════════════
@self_service_bp.route("/api/v1/org/dashboard", methods=["GET"])
def org_dashboard():
    """Self-service dashboard: usage, team, audit, billing — everything the customer manages themselves."""
    user = getattr(request, "user", None)
    if not user:
        return jsonify({"error": "Auth required"}), 401

    org_id = user.get("org_id")
    p = param_placeholder()

    with db_connection() as conn:
        cur = conn.cursor()

        # Team count
        cur.execute(f"SELECT COUNT(*) FROM org_memberships WHERE org_id = {p}", (org_id,))
        team_count = cur.fetchone()[0]

        # Active protocols
        cur.execute(f"SELECT COUNT(*) FROM org_protocols WHERE org_id = {p} AND is_active = 1", (org_id,))
        protocol_count = cur.fetchone()[0]

        # Usage this month
        now = datetime.now(timezone.utc)
        period_start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
        cur.execute(f"""
            SELECT meter_type, SUM(quantity) FROM usage_meters
            WHERE org_id = {p} AND period_start >= {p}
            GROUP BY meter_type
        """, (org_id, period_start))
        usage_rows = cur.fetchall()
        usage = {}
        for r in usage_rows:
            if hasattr(r, "keys"):
                usage[r["meter_type"]] = r["sum"]
            else:
                usage[r[0]] = r[1]

        # Recent audit events (last 20)
        cur.execute(f"""
            SELECT timestamp, action, details_json FROM auth_audit_log
            WHERE org_id = {p} ORDER BY timestamp DESC LIMIT 20
        """, (org_id,))
        audit_rows = cur.fetchall()
        audit = [dict(r) if hasattr(r, "keys") else dict(zip(
            ["timestamp", "action", "details_json"], r)) for r in audit_rows]

        # API keys
        cur.execute(f"""
            SELECT key_prefix, name, scopes, last_used_at, is_active
            FROM api_keys WHERE org_id = {p}
        """, (org_id,))
        key_rows = cur.fetchall()
        keys = [dict(r) if hasattr(r, "keys") else dict(zip(
            ["key_prefix", "name", "scopes", "last_used_at", "is_active"], r)) for r in key_rows]

    return jsonify({
        "org_id": org_id,
        "team_count": team_count,
        "active_protocols": protocol_count,
        "usage_this_month": usage,
        "recent_audit": audit,
        "api_keys": keys,
    })


# ══════════════════════════════════════════════
# ADMIN PROVISIONING ROUTE (Jame only)
# ══════════════════════════════════════════════
@self_service_bp.route("/api/v1/admin/provision", methods=["POST"])
def admin_provision():
    """
    Jame calls this ONE endpoint after a customer pays.
    Everything else is self-service.

    POST /api/v1/admin/provision
    {
        "org_name": "Bethany Christian Services",
        "org_slug": "bethany",
        "plan": "enterprise",
        "implementation_lead_email": "sarah.jones@bethany.org",
        "implementation_lead_name": "Sarah Jones"
    }

    Returns invite URL. Send it to Sarah. Done.
    """
    from auth import require_role
    # In production: @require_role("admin")

    payload = request.get_json() or {}
    org_name = payload.get("org_name", "").strip()
    org_slug = payload.get("org_slug", "").strip().lower()
    plan = payload.get("plan", "starter")
    lead_email = payload.get("implementation_lead_email", "").strip().lower()
    lead_name = payload.get("implementation_lead_name", "")

    if not org_name or not org_slug or not lead_email:
        return jsonify({"error": "org_name, org_slug, and implementation_lead_email required"}), 400

    p = param_placeholder()
    now = datetime.now(timezone.utc).isoformat()
    org_id = str(uuid.uuid4())

    # Create the org
    with db_connection() as conn:
        cur = conn.cursor()

        cur.execute(f"SELECT id FROM organizations WHERE slug = {p}", (org_slug,))
        if cur.fetchone():
            return jsonify({"error": "Organization slug already taken"}), 409

        cur.execute(f"""
            INSERT INTO organizations (id, name, slug, plan, created_at, updated_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p})
        """, (org_id, org_name, org_slug, plan, now, now))
        conn.commit()

    # Provision everything
    result = provision_customer(org_id, org_name, plan, lead_email, lead_name)

    return jsonify(result), 201
