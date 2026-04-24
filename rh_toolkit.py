"""
routes/rh_toolkit.py
=====================
Artifact Zero — RH Cryptographic Toolkit endpoints
Version 1.0.0 | April 2026

Endpoints:
    POST /v1/certify          Single parameter certificate
    POST /v1/audit            Full system audit report
    GET  /v1/theorems         Mathematical reference library
    GET  /v1/zeros            Numerical zeta zero verification
    GET  /v1/runner/status    Runner performance summary

Registration in app.py:
    try:
        from routes.rh_toolkit import bp as rh_toolkit_bp
        app.register_blueprint(rh_toolkit_bp)
        print("[app] rh_toolkit loaded", flush=True)
    except ImportError:
        print("[app] rh_toolkit not found, skipping", flush=True)
"""

import uuid
import time
import json
import traceback

from flask import Blueprint, request, jsonify

import db as database
import nti_log
from api_auth import require_api_key

from az_rh_toolkit.api import CertificateAPI

bp  = Blueprint("rh_toolkit", __name__)
api = CertificateAPI()


# ── /v1/certify ───────────────────────────────────────────────────────────────
@bp.route("/v1/certify", methods=["POST"])
@require_api_key
def certify():
    """
    Issue a signed certificate for a cryptographic parameter set.

    Body (JSON):
        { "type": "rsa",   "key_bits": 2048 }
        { "type": "ecc",   "curve": "P-256" }
        { "type": "dh",    "p_bits": 3072, "q_bits": 256 }
        { "type": "prime", "n": 170141183460469231731687303715884105727 }

    Returns:
        Certificate JSON with HMAC-SHA256 signature.
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json(silent=True)
    if not body or "type" not in body:
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/certify", 400, latency_ms, request._api_key_id)
        return jsonify({"error": "request body must include 'type'"}), 400

    try:
        cert = api.certify(**body)
    except ValueError as e:
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/certify", 400, latency_ms, request._api_key_id)
        return jsonify({"error": str(e)}), 400
    except Exception:
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/certify", 500, latency_ms, request._api_key_id)
        return jsonify({"error": "internal error"}), 500

    # Persist certificate to RDS
    try:
        conn = database.db_connect()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO az_certificates
                (api_key_id, param_type, parameters, security_bits,
                 compliant, assessment, signature, proof_doi, proof_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            request._api_key_id,
            cert.param_type,
            json.dumps(cert.parameters),
            cert.assessment.get("security_bits"),
            cert.assessment.get("compliant", False),
            json.dumps(cert.assessment),
            cert.signature,
            cert.proof_doi,
            cert.proof_status,
        ))
        conn.commit()
        conn.close()
    except Exception:
        # Log but don't fail the request — cert was computed correctly
        traceback.print_exc()

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/certify", 200, latency_ms, request._api_key_id)

    return jsonify(json.loads(cert.to_json())), 200


# ── /v1/audit ─────────────────────────────────────────────────────────────────
@bp.route("/v1/audit", methods=["POST"])
@require_api_key
def audit():
    """
    Full system audit across all standard parameter sets.

    Body (JSON, optional):
        { "system_name": "MyProduct v1.0" }

    Returns:
        Audit report JSON with all certificates and overall compliance status.
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json(silent=True) or {}
    system_name = body.get("system_name", "Unnamed System")

    try:
        report = api.audit(system_name)
    except Exception:
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/audit", 500, latency_ms, request._api_key_id)
        return jsonify({"error": "internal error"}), 500

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/audit", 200, latency_ms, request._api_key_id)

    return jsonify(report), 200


# ── /v1/theorems ──────────────────────────────────────────────────────────────
@bp.route("/v1/theorems", methods=["GET"])
@require_api_key
def theorems():
    """
    Mathematical reference library.
    Returns theorems connecting prime distribution to cryptographic security.
    Each entry includes established status and cryptographic relevance.
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    result = api.theorems()

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/theorems", 200, latency_ms, request._api_key_id)

    return jsonify(result), 200


# ── /v1/zeros ─────────────────────────────────────────────────────────────────
@bp.route("/v1/zeros", methods=["GET"])
@require_api_key
def zeros():
    """
    Numerical zeta zero verification.
    Computes and returns the first N non-trivial zeros of the Riemann zeta
    function, each verified at Re(s) = 0.5 to 50 decimal places.

    Query params:
        count (int, default 10, max 100)
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    try:
        count = min(int(request.args.get("count", 10)), 100)
    except ValueError:
        count = 10

    result = api.zeros(count)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/zeros", 200, latency_ms, request._api_key_id)

    return jsonify(result), 200


# ── /v1/runner/status ─────────────────────────────────────────────────────────
@bp.route("/v1/runner/status", methods=["GET"])
@require_api_key
def runner_status():
    """
    Runner performance summary from stored logs.
    Returns aggregate timing statistics by bit length from az_runner_logs.
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    try:
        conn = database.db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT
                bit_length,
                COUNT(*)                        AS count,
                ROUND(AVG(total_ms)::numeric, 1) AS avg_ms,
                ROUND(MIN(total_ms)::numeric, 1) AS min_ms,
                ROUND(MAX(total_ms)::numeric, 1) AS max_ms,
                BOOL_AND(deterministic)          AS deterministic,
                BOOL_AND(within_table)           AS within_table
            FROM az_runner_logs
            GROUP BY bit_length
            ORDER BY bit_length
        """)
        rows = cur.fetchall()
        conn.close()
        result = [dict(r) for r in rows]
    except Exception:
        traceback.print_exc()
        result = []

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/runner/status", 200, latency_ms, request._api_key_id)

    return jsonify(result), 200


# ── /v1/certificates ──────────────────────────────────────────────────────────
@bp.route("/v1/certificates", methods=["GET"])
@require_api_key
def certificates():
    """
    Retrieve certificates issued for this API key.

    Query params:
        type      Filter by param_type (RSA, ECC, DH, PRIME)
        limit     Max results (default 50, max 200)
        compliant Filter by compliance (true/false)
    """
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    param_type = request.args.get("type", "").upper() or None
    compliant  = request.args.get("compliant")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50

    try:
        conn = database.db_connect()
        cur  = conn.cursor()

        filters = ["api_key_id = %s"]
        values  = [request._api_key_id]

        if param_type:
            filters.append("param_type = %s")
            values.append(param_type)

        if compliant is not None:
            filters.append("compliant = %s")
            values.append(compliant.lower() == "true")

        values.append(limit)

        cur.execute(f"""
            SELECT id, created_at, param_type, parameters,
                   security_bits, compliant, signature, proof_status
            FROM az_certificates
            WHERE {' AND '.join(filters)}
            ORDER BY created_at DESC
            LIMIT %s
        """, values)

        rows = cur.fetchall()
        conn.close()

        result = []
        for r in rows:
            row = dict(r)
            row["created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
            row["id"] = str(row["id"])
            result.append(row)

    except Exception:
        traceback.print_exc()
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/certificates", 500, latency_ms, request._api_key_id)
        return jsonify({"error": "internal error"}), 500

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/certificates", 200, latency_ms, request._api_key_id)

    return jsonify(result), 200
