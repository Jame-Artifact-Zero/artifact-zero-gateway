from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)

import db as database
from core_engine.app import log_json_line, utc_now_iso

# safecheck_engine.py â€” local module, confirmed present in repo root
from safecheck_engine import generate_observations


public_bp = Blueprint("public", __name__)


@public_bp.route("/")
def home():
    try:
        return render_template("index.html")
    except Exception:
        return "NTI Canonical Runtime is live."


# /relay handled by az_relay blueprint


def youros_redirect():
    from flask import redirect
    return redirect("/your-os/builder", code=301)


@public_bp.route("/contact")
def contact_page():
    return render_template("contact.html")


@public_bp.route("/developers")
def developers_page():
    return render_template("developers.html")


@public_bp.route("/api/developer-apply", methods=["POST"])
def api_developer_apply():
    """Developer/vendor access request. Stores in DB, sends notification."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    message = (data.get("message") or "").strip()
    if not name or not email or not message:
        return jsonify({"error": "All fields required"}), 400
    try:
        conn = database.db_connect()
        conn.execute(
            "INSERT INTO contact_submissions (name, email, message, type, created_at) VALUES (%s, %s, %s, %s, %s)",
            (name, email, message, "developer_apply", utc_now_iso())
        )
        conn.commit()
    except Exception:
        pass  # DB table may not exist yet â€” fail silently, log below
    log_json_line("developer_apply", {"name": name, "email": email, "message": message[:200]})
    return jsonify({"status": "ok"})


@public_bp.route("/compose")
def compose_page():
    return render_template("compose.html")


@public_bp.route("/examples")
def examples_page():
    return render_template("examples.html")


@public_bp.route("/wall")
def wall_page():
    return render_template("wall.html")


@public_bp.route("/docs")
def docs():
    return render_template("api.html")


@public_bp.route("/use-cases")
def use_cases_page():
    USE_CASES = [
        {'title': 'Pre-LLM Prompt Firewall', 'industry': 'Security', 'wedge': 'Injection surface reduction', 'problem': 'Prompts carry hidden instructions, ambiguity, and adversarial framing into model calls.', 'breaks': 'Prompt injection, unsafe completions, non-deterministic behavior.', 'v1_detects': 'Injection-style framing, dominance directives, ambiguity carriers, scope drift, constraint absence.', 'v3_stabilizes': 'Strip hedges, enforce objective/constraints, normalize instruction structure.', 'model': 'Sell as a gateway middleware for any LLM product (per-request enforcement).'},
        {'title': 'Post-LLM Output Validator', 'industry': 'Security', 'wedge': 'Governed output', 'problem': 'Generated text can include fabricated certainty, missing constraints, and risky commitments.', 'breaks': 'Compliance exposure, escalation, contractual promises.', 'v1_detects': 'Absolutes, implied commitments, missing actor/ownership, drift markers, failure modes (DCE/CCA/UDDS).', 'v3_stabilizes': 'Tighten claims to constraints, remove commitment risk, enforce assignment/timeline.', 'model': 'Add as a validator stage in agent pipelines; charge per validation.'},
        {'title': 'AI Email Governance', 'industry': 'Productivity', 'wedge': 'Outbound control layer', 'problem': 'People send messages that escalate conflict or create hidden commitments.', 'breaks': 'Fires, churn, lawsuits, misalignment.', 'v1_detects': 'Hedges, blame, dominance, escalation words, structure gaps.', 'v3_stabilizes': 'Remove hedges/filler, anchor to objective, add timeline/owner.', 'model': 'Embed in email clients, CRMs, and outreach tools.'},
        {'title': 'Slack/Teams Compliance Filter', 'industry': 'Enterprise IT', 'wedge': 'Realtime policy enforcement', 'problem': 'Sensitive or escalatory messages move fast in chat systems.', 'breaks': 'HR incidents, policy violations, leakage.', 'v1_detects': 'Escalation triggers, blame patterns, absolutes, dominance assertions.', 'v3_stabilizes': 'Rewrite into policy-compliant structure (optional).', 'model': 'Enterprise compliance add-on; per-message scanning.'},
        {'title': 'Sales Commitment Guardrails', 'industry': 'Sales', 'wedge': 'Commitment risk control', 'problem': 'Reps over-promise in email and CRM notes.', 'breaks': 'Contract disputes, churn, refunds.', 'v1_detects': 'Implied/unbounded commitments, missing constraints, timeline vagueness (DCE).', 'v3_stabilizes': 'Bound commitments to scope, add timeline and owners, remove absolute language.', 'model': 'CRM plugin; per-score billing.'},
        {'title': 'Contract Drift Detector', 'industry': 'Legal', 'wedge': 'Structural diff', 'problem': 'Negotiations drift and constraints get abstracted away.', 'breaks': 'Bad deals, missed obligations.', 'v1_detects': 'Constraint collapse (CCA), substitution drift (UDDS), missing enforcement (DCE).', 'v3_stabilizes': 'N/A (usually detect-only); generate structured drift report.', 'model': 'Law firm + procurement tooling; per-document scoring.'},
        {'title': 'Insurance Claim Narrative Risk', 'industry': 'Insurance', 'wedge': 'Fraud/ambiguity triage', 'problem': 'Claims contain vague narratives and missing constraints.', 'breaks': 'Bad payouts, disputes, slow processing.', 'v1_detects': 'Hedge stacking, missing specifics, passive constructions, authority displacement.', 'v3_stabilizes': 'N/A; route to adjuster queues with evidence spans.', 'model': 'Per-claim scoring + routing.'},
        {'title': 'Healthcare Documentation Risk Layer', 'industry': 'Healthcare', 'wedge': 'Clinical note integrity', 'problem': 'Notes contain ambiguity, missing actions, and unclear responsibility.', 'breaks': 'Billing issues, care errors, compliance problems.', 'v1_detects': 'Missing actor, passive voice, vague quantifiers, DCE patterns.', 'v3_stabilizes': 'Optional stabilization into clearer constraints/ownership (non-clinical).', 'model': 'Hospital compliance layer; enterprise contract.'},
        {'title': 'Agent Tool-Use Gate', 'industry': 'AI/Agents', 'wedge': 'Deterministic gating', 'problem': 'Agents choose tools under vague intent and drift.', 'breaks': 'Bad actions, unnecessary calls, cost blowups.', 'v1_detects': 'Objective ambiguity, scope creep markers, unbounded commitments.', 'v3_stabilizes': 'Normalize objective/constraints before tool selection.', 'model': 'Sell to agent framework vendors; per-run enforcement.'},
        {'title': 'Customer Support Escalation Predictor', 'industry': 'Support', 'wedge': 'Escalation prevention', 'problem': 'Support replies accidentally escalate tension.', 'breaks': 'Churn, refunds, reputation hits.', 'v1_detects': 'Dominance/absolutes, blame language, passive aggression triggers.', 'v3_stabilizes': 'Remove edge, add resolution path, clarify next action.', 'model': 'Helpdesk integration; per-ticket scoring.'},
        {'title': 'HR/People Ops Message Safety', 'industry': 'HR', 'wedge': 'Policy-aligned comms', 'problem': 'Sensitive HR messages must be precise and non-escalatory.', 'breaks': 'Legal exposure, employee relations incidents.', 'v1_detects': 'Directive language, absolutes, dominance, missing resolution path.', 'v3_stabilizes': 'Structure into neutral, bounded statements with clear steps.', 'model': 'Enterprise HR suite add-on.'},
        {'title': 'Procurement Vendor Communication Guardrails', 'industry': 'Procurement', 'wedge': 'Commitment control', 'problem': 'Vendor comms often include vague commitments and scope drift.', 'breaks': 'Delays, disputes.', 'v1_detects': 'DCE, UDDS, hedge stacking, missing constraints.', 'v3_stabilizes': 'Add constraints, owners, timeline.', 'model': 'Procurement platform plugin.'},
        {'title': 'Policy Draft Integrity Scoring', 'industry': 'Compliance', 'wedge': 'Policy clarity', 'problem': 'Policies get written in vague, non-enforceable language.', 'breaks': 'Non-compliance, unenforceable standards.', 'v1_detects': 'Ambiguity carriers, missing actor, passive voice, weak constraints.', 'v3_stabilizes': 'Reframe into enforceable clauses (optional).', 'model': 'Compliance authoring tool.'},
        {'title': 'Financial Advisory Email Risk', 'industry': 'Finance', 'wedge': 'Reg-safe language', 'problem': 'Advisors send emails that imply guarantees.', 'breaks': 'Regulatory action, lawsuits.', 'v1_detects': 'Guarantee/absolute language, implied certainty, missing qualifiers.', 'v3_stabilizes': 'Bound claims, remove absolutes, add required disclaimers.', 'model': 'Broker-dealer compliance integration.'},
        {'title': 'Executive Comms Stabilizer', 'industry': 'Executive', 'wedge': 'Tone + structure', 'problem': 'High-stakes comms get distorted by tone and drift.', 'breaks': 'Escalation, reputational harm.', 'v1_detects': 'Dominance posture, escalation triggers, reputation framing.', 'v3_stabilizes': 'Trim, clarify objective, reduce edge.', 'model': 'Private exec tool; subscription.'},
        {'title': 'Meeting Notes Action Integrity', 'industry': 'Ops', 'wedge': 'Actionability enforcement', 'problem': 'Meeting notes lack ownership/timelines.', 'breaks': 'No execution, misalignment.', 'v1_detects': 'Missing actor, no next action, DCE deferral language.', 'v3_stabilizes': 'Convert into assignments with dates (optional).', 'model': 'PM suite integration.'},
        {'title': 'RFP Response Constraint Integrity', 'industry': 'B2B', 'wedge': 'Bid discipline', 'problem': 'Teams answer RFPs with vague claims.', 'breaks': 'Lost deals, scope problems.', 'v1_detects': 'Vague quantifiers, hedges, missing constraints.', 'v3_stabilizes': 'Clarify commitments, add constraints and definitions.', 'model': 'RFP tooling add-on.'},
        {'title': 'Legal Intake Triage', 'industry': 'Legal', 'wedge': 'High-signal intake', 'problem': 'Client messages are messy; triage is slow.', 'breaks': 'Wrong routing, delays.', 'v1_detects': 'Objective ambiguity, missing facts, escalation language.', 'v3_stabilizes': 'N/A; structured intake summary output optional in higher tier.', 'model': 'Law firm intake pipeline.'},
        {'title': 'Vendor SLA Drift Monitor', 'industry': 'Enterprise', 'wedge': 'SLA enforcement', 'problem': 'Service conversations drift away from SLA terms.', 'breaks': 'Hidden risk, missed obligations.', 'v1_detects': 'UDDS substitution drift, DCE deferrals, constraint loss.', 'v3_stabilizes': 'N/A; generate drift alerts with spans.', 'model': 'Enterprise contract monitoring.'},
        {'title': 'Code Review Comment Stabilizer', 'industry': 'Engineering', 'wedge': 'Team velocity', 'problem': 'Code review comments escalate and waste cycles.', 'breaks': 'Conflict, churn.', 'v1_detects': 'Blame framing, dominance phrases, absolutes.', 'v3_stabilizes': 'Rewrite into neutral, actionable requests.', 'model': 'Dev tooling plugin.'},
        {'title': 'Fraud Narrative Consistency Checks', 'industry': 'Risk', 'wedge': 'Narrative integrity', 'problem': 'Fraud often shows as vague or inconsistent narratives.', 'breaks': 'Bad payouts.', 'v1_detects': 'Ambiguity carriers, passive voice, missing specifics.', 'v3_stabilizes': 'N/A; risk scoring + routing.', 'model': 'Risk engine integration.'},
        {'title': 'Public Relations Draft Risk', 'industry': 'PR', 'wedge': 'Reputational protection', 'problem': 'Drafts include absolutes and unbounded commitments.', 'breaks': 'PR crises.', 'v1_detects': 'Absolutes, dominance, resolution closure, reputation protection patterns.', 'v3_stabilizes': 'Bound claims, clarify what is known vs not known.', 'model': 'PR workflow tool.'},
        {'title': 'AI Policy Gate for Employees', 'industry': 'Security', 'wedge': 'Org-wide enforcement', 'problem': 'Employees paste sensitive content into AI tools.', 'breaks': 'Leakage, compliance violations.', 'v1_detects': 'Sensitive-pattern prefilters + structural risk signals (optional).', 'v3_stabilizes': 'N/A; allow/block + user feedback.', 'model': 'Enterprise gateway; seat or volume.'},
        {'title': 'Underwriter Decision Support Clarity', 'industry': 'Insurance', 'wedge': 'Decision integrity', 'problem': 'Underwriting notes contain vague reasoning and hidden deferrals.', 'breaks': 'Bad risk decisions.', 'v1_detects': 'Causal justification, missing constraints, DCE, CCA.', 'v3_stabilizes': 'N/A; evidence highlighting.', 'model': 'Carrier integration; enterprise.'},
    ]
    return render_template("use_cases.html", use_cases=USE_CASES)


@public_bp.route("/score")
def score_page():
    return render_template("score.html")


@public_bp.route("/security")
def security_page():
    return render_template("security.html")


@public_bp.route("/ai")
def ai_page():
    return render_template("ai.html")


@public_bp.route("/safecheck")
def safecheck_page():
    return render_template("safecheck.html")


@public_bp.route("/safecheck", methods=["POST"])
def safecheck():
    payload = request.get_json(force=True) or {}
    text = payload.get("text", "")
    nii_result = payload.get("nii_result", {})
    l2_result = payload.get("l2_result", {})
    tilt_tags = payload.get("tilt_tags", [])
    edge_result = payload.get("edge_result", None)
    cards = generate_observations(
        text,
        nii_result,
        l2_result,
        tilt_tags,
        edge_result,
    )
    return jsonify({"cards": cards, "count": len(cards)})


def glossary_page():
    return render_template("glossary.html")


@public_bp.route("/experiment")
def experiment_page():
    return render_template("experiment.html")


@public_bp.route("/engine-bench")
def engine_bench():
    return render_template("engine-bench.html")


@public_bp.route("/voice")
def voice_page():
    return render_template("voice.html",
                           logged_in=session.get('logged_in', False),
                           user_id=session.get('user_id'))


@public_bp.route("/fortune500")
def fortune500_page():
    return render_template("fortune500.html")


@public_bp.route("/scored/<slug>")
def scored_page(slug):
    return render_template("scored.html")


@public_bp.route("/knoxville")
def knoxville_page():
    return render_template("knoxville.html")


@public_bp.route("/birkbeck")
def birkbeck_page():
    return render_template("birkbeck.html")


@public_bp.route("/anderson")
@public_bp.route("/anderson-county")
def anderson_page():
    return render_template("anderson.html")


@public_bp.route("/vc-funds")
def vc_funds_page():
    return render_template("vc_funds.html")


@public_bp.route("/robots.txt")
def robots_txt():
    return current_app.send_static_file("robots.txt")


@public_bp.route("/static/manifest.xml")
def manifest_xml():
    from flask import send_from_directory
    return send_from_directory(
        current_app.static_folder,
        "manifest.xml",
        mimetype="application/xml",
        as_attachment=False
    )


@public_bp.route("/manifest.xml")
def manifest_xml_root():
    from flask import send_from_directory
    return send_from_directory(
        current_app.static_folder,
        "manifest.xml",
        mimetype="application/xml",
        as_attachment=False
    )
