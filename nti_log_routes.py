"""
nti_log_routes.py

Flask blueprint for all log-facing endpoints.
Register in app.py:
    from nti_log_routes import log_bp
    app.register_blueprint(log_bp)

Endpoints:
    GET  /verify/<request_id>           — public stamp verify (no auth)
    POST /api/v1/log/check              — write a check event (API key auth)
    POST /api/v1/log/override           — write an override event (API key auth)
    POST /api/v1/log/rewrite-accepted   — mark rewrite accepted (API key auth)
    POST /api/v1/log/stamp              — write a stamp event (API key auth)
    POST /api/v1/log/spend              — write a spend event (API key auth)
    GET  /api/v1/reporting/stats        — account stats (API key auth)
    GET  /api/v1/reporting/export       — CSV export (API key auth)
"""

import csv
import io
import json
from flask import Blueprint, request, jsonify, render_template_string, Response
from nti_log import (
    log_check, log_override, log_rewrite_accepted, log_stamp, log_spend,
    get_check, get_account_stats, export_checks_csv,
    make_request_id, hash_text, init_log_tables
)

log_bp = Blueprint("log_bp", __name__)

# ── AUTH ──────────────────────────────────────────────────────────────────────

def _get_account_from_key(api_key: str):
    """
    Resolve API key to account_id and user context.
    Plug into your existing API key / credits system.
    Returns dict with account_id, user_id, user_email or None if invalid.
    """
    if not api_key:
        return None
    # TODO: replace with real lookup from your az_users / credits table
    # For now: accept any non-empty key, return it as account_id
    return {"account_id": api_key[:8], "user_id": None, "user_email": None}


def _auth():
    """Extract and validate API key from request. Returns account dict or None."""
    key = (
        request.headers.get("X-API-Key") or
        request.headers.get("Authorization", "").replace("Bearer ", "") or
        request.args.get("api_key", "")
    )
    return _get_account_from_key(key)


# ── PUBLIC: VERIFY ────────────────────────────────────────────────────────────

VERIFY_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NTI Verification — {{ request_id[:8] }}...</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=DM+Sans:wght@400;600&display=swap');
  :root{--bg:#0b1120;--accent:#00ff88;--amber:#f59e0b;--red:#ef4444;--text:rgba(255,255,255,.88);--dim:rgba(255,255,255,.45);--mono:'JetBrains Mono',monospace;--sans:'DM Sans',sans-serif;}
  *{margin:0;padding:0;box-sizing:border-box;}
  body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}
  .card{max-width:540px;width:100%;background:#111827;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:32px;}
  .logo{font-family:var(--mono);font-size:10px;font-weight:700;letter-spacing:3px;color:var(--accent);margin-bottom:24px;}
  .status{display:flex;align-items:center;gap:14px;margin-bottom:24px;}
  .status-icon{font-size:36px;}
  .status-title{font-family:var(--mono);font-size:14px;font-weight:700;letter-spacing:1px;margin-bottom:4px;}
  .status-title.valid{color:var(--accent);}
  .status-title.invalid{color:var(--red);}
  .status-sub{font-size:12px;color:var(--dim);}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px;}
  .field{background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:12px;}
  .field-label{font-family:var(--mono);font-size:9px;font-weight:700;letter-spacing:2px;color:var(--dim);margin-bottom:4px;text-transform:uppercase;}
  .field-value{font-family:var(--mono);font-size:13px;font-weight:700;}
  .field-value.green{color:var(--accent);}
  .field-value.amber{color:var(--amber);}
  .field-value.red{color:var(--red);}
  .signals{margin-bottom:20px;}
  .signal-wrap{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
  .pill{padding:3px 8px;border-radius:4px;font-family:var(--mono);font-size:9px;font-weight:700;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.25);color:var(--red);}
  .pill.none{background:rgba(0,255,136,.06);border-color:rgba(0,255,136,.2);color:var(--accent);}
  .rid{font-family:var(--mono);font-size:10px;color:var(--dim);word-break:break-all;margin-bottom:16px;}
  .footer{font-size:11px;color:var(--dim);border-top:1px solid rgba(255,255,255,.06);padding-top:16px;}
  .footer a{color:var(--accent);text-decoration:none;}
</style>
</head>
<body>
<div class="card">
  <div class="logo">ARTIFACT ZERO · NTI VERIFICATION</div>
  {% if found %}
  <div class="status">
    <div class="status-icon">✓</div>
    <div>
      <div class="status-title valid">VERIFIED</div>
      <div class="status-sub">This email was checked by NTI before it was sent.</div>
    </div>
  </div>
  <div class="grid">
    <div class="field">
      <div class="field-label">Score</div>
      <div class="field-value {{ score_color }}">{{ score }}</div>
    </div>
    <div class="field">
      <div class="field-label">Result</div>
      <div class="field-value {{ score_color }}">{{ band }}</div>
    </div>
    <div class="field">
      <div class="field-label">Checked At</div>
      <div class="field-value" style="font-size:11px;color:rgba(255,255,255,.7)">{{ checked_at }}</div>
    </div>
    <div class="field">
      <div class="field-label">Engine Version</div>
      <div class="field-value" style="font-size:11px;color:rgba(255,255,255,.7)">{{ nti_version }}</div>
    </div>
  </div>
  {% if signals %}
  <div class="signals">
    <div class="field-label" style="font-family:'JetBrains Mono',monospace;font-size:9px;font-weight:700;letter-spacing:2px;color:rgba(255,255,255,.45);text-transform:uppercase;margin-bottom:4px;">Signals Detected</div>
    <div class="signal-wrap">
      {% for s in signals %}<span class="pill">{{ s.replace('_',' ') }}</span>{% endfor %}
    </div>
  </div>
  {% else %}
  <div class="signals">
    <div class="signal-wrap"><span class="pill none">NO FLAGS — CLEAN</span></div>
  </div>
  {% endif %}
  <div class="rid">Verification ID: {{ request_id }}</div>
  {% else %}
  <div class="status">
    <div class="status-icon">✗</div>
    <div>
      <div class="status-title invalid">NOT FOUND</div>
      <div class="status-sub">No record found for this verification ID.</div>
    </div>
  </div>
  {% endif %}
  <div class="footer">Powered by <a href="https://artifact0.com">Artifact Zero</a> · NTI deterministic structural enforcement · <a href="https://artifact0.com/docs">Learn more</a></div>
</div>
</body>
</html>"""


@log_bp.route("/verify/<request_id>", methods=["GET"])
def verify(request_id: str):
    """Public endpoint. No auth. Returns verification page."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    result = get_check(request_id, lookup_ip=ip)

    if result:
        score = result["score"] or 0
        if score >= 80:
            score_color = "green"
            band = "STRUCTURALLY SOLID"
        elif score >= 60:
            score_color = "amber"
            band = "REVIEW RECOMMENDED"
        else:
            score_color = "red"
            band = "HIGH RISK"

        # Format timestamp
        checked_at = str(result.get("checked_at", ""))[:19].replace("T", " ") + " UTC"

        return render_template_string(
            VERIFY_TEMPLATE,
            found=True,
            request_id=request_id,
            score=int(score),
            score_color=score_color,
            band=band,
            checked_at=checked_at,
            nti_version=result.get("nti_version", ""),
            signals=result.get("signals", []),
        )
    else:
        return render_template_string(
            VERIFY_TEMPLATE,
            found=False,
            request_id=request_id,
        ), 404


@log_bp.route("/verify/<request_id>.json", methods=["GET"])
def verify_json(request_id: str):
    """JSON variant for API consumers."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    result = get_check(request_id, lookup_ip=ip)
    if result:
        return jsonify(result)
    return jsonify({"error": "not_found", "request_id": request_id}), 404


# ── LOG: CHECK ────────────────────────────────────────────────────────────────

@log_bp.route("/api/v1/log/check", methods=["POST"])
def api_log_check():
    """
    Called by Outlook add-in, Gmail add-on, any surface after a check completes.
    Surfaces generate request_id themselves so they can reference it for override/stamp.
    """
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    request_id = body.get("request_id") or make_request_id()
    text = body.get("text", "")

    ok = log_check(
        request_id=request_id,
        nti_version=body.get("nti_version", "unknown"),
        score=float(body.get("score", 0)),
        nii_raw=float(body.get("nii_raw", 0)),
        signals=body.get("signals", []),
        char_count=len(text),
        word_count=len(text.split()),
        text_hash=hash_text(text) if text else "",
        latency_ms=int(body.get("latency_ms", 0)),
        surface=body.get("surface", "api"),
        account_id=acct["account_id"],
        user_id=body.get("user_id") or acct["user_id"],
        user_email=body.get("user_email") or acct["user_email"],
        rewrite_offered=body.get("rewrite_offered", False),
        rewrite_accepted=body.get("rewrite_accepted", False),
        ip=request.headers.get("X-Forwarded-For", request.remote_addr),
        user_agent=request.headers.get("User-Agent"),
        metadata=body.get("metadata"),
    )

    return jsonify({"ok": ok, "request_id": request_id})


# ── LOG: OVERRIDE ─────────────────────────────────────────────────────────────

@log_bp.route("/api/v1/log/override", methods=["POST"])
def api_log_override():
    """Called when user bypasses a failed check and sends anyway."""
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    ok = log_override(
        request_id=body.get("request_id", make_request_id()),
        score=float(body.get("score", 0)),
        signals=body.get("signals", []),
        account_id=acct["account_id"],
        user_id=body.get("user_id"),
        user_email=body.get("user_email"),
        override_reason=body.get("reason"),
    )
    return jsonify({"ok": ok})


# ── LOG: REWRITE ACCEPTED ─────────────────────────────────────────────────────

@log_bp.route("/api/v1/log/rewrite-accepted", methods=["POST"])
def api_log_rewrite_accepted():
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    ok = log_rewrite_accepted(body.get("request_id", ""))
    return jsonify({"ok": ok})


# ── LOG: STAMP ────────────────────────────────────────────────────────────────

@log_bp.route("/api/v1/log/stamp", methods=["POST"])
def api_log_stamp():
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    ok = log_stamp(
        request_id=body.get("request_id", make_request_id()),
        score=float(body.get("score", 0)),
        stamp_text=body.get("stamp_text", ""),
        account_id=acct["account_id"],
        user_id=body.get("user_id"),
        recipient_domain=body.get("recipient_domain"),
    )
    return jsonify({"ok": ok})


# ── LOG: SPEND ────────────────────────────────────────────────────────────────

@log_bp.route("/api/v1/log/spend", methods=["POST"])
def api_log_spend():
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    ok = log_spend(
        model=body.get("model", "unknown"),
        call_type=body.get("call_type", "unknown"),
        input_tokens=int(body.get("input_tokens", 0)),
        output_tokens=int(body.get("output_tokens", 0)),
        cost_usd=float(body.get("cost_usd", 0)),
        markup_pct=float(body.get("markup_pct", 0.15)),
        request_id=body.get("request_id"),
        account_id=acct["account_id"],
    )
    return jsonify({"ok": ok})


# ── REPORTING: STATS ──────────────────────────────────────────────────────────

@log_bp.route("/api/v1/reporting/stats", methods=["GET"])
def api_reporting_stats():
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    days = int(request.args.get("days", 30))
    stats = get_account_stats(acct["account_id"], days=days)
    return jsonify(stats)


# ── REPORTING: EXPORT ─────────────────────────────────────────────────────────

@log_bp.route("/api/v1/reporting/export", methods=["GET"])
def api_reporting_export():
    acct = _auth()
    if not acct:
        return jsonify({"error": "unauthorized"}), 401

    days = int(request.args.get("days", 30))
    rows = export_checks_csv(acct["account_id"], days=days)

    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    else:
        output.write("no data\n")

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=nti_checks_{days}d.csv"
        }
    )


# ── INIT ON IMPORT ────────────────────────────────────────────────────────────
# Tables are created on first import. Safe to call multiple times.
try:
    init_log_tables()
except Exception as e:
    import logging
    logging.getLogger("nti_log_routes").warning(f"init_log_tables on import failed: {e}")
