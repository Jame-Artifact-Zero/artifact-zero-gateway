import uuid
import time
import json
import traceback

from flask import Blueprint, request, jsonify
import psycopg2.extras

import db as database
import nti_log
from api_auth import require_api_key

from az_rh_toolkit.api import CertificateAPI

bp  = Blueprint("rh_toolkit", __name__)
api = CertificateAPI()

# Fields stripped from theorem responses — IP protection
_THEOREM_KEEP = {"id", "name", "status", "domain"}

# Fields stripped from certificate assessment — IP protection
_ASSESSMENT_STRIP = {"notes", "prior_status"}

# Fields stripped from certify response top level — IP protection
_CERT_STRIP = {"proof_status_msg"}


def _strip_theorem(t: dict) -> dict:
    return {k: v for k, v in t.items() if k in _THEOREM_KEEP}


def _strip_assessment(assessment: dict) -> dict:
    return {k: v for k, v in assessment.items() if k not in _ASSESSMENT_STRIP}


def _strip_cert(cert: dict) -> dict:
    result = {k: v for k, v in cert.items() if k not in _CERT_STRIP}
    if "assessment" in result and isinstance(result["assessment"], dict):
        result["assessment"] = _strip_assessment(result["assessment"])
    return result


def _dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


@bp.route("/v1/certify", methods=["POST"])
@require_api_key
def certify():
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
    except Exception as e:
        traceback.print_exc()
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/certify", 500, latency_ms, request._api_key_id)
        return jsonify({"error": "internal error"}), 500

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
        traceback.print_exc()

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/certify", 200, latency_ms, request._api_key_id)

    raw = json.loads(cert.to_json())
    return jsonify(_strip_cert(raw)), 200


@bp.route("/v1/audit", methods=["POST"])
@require_api_key
def audit():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json(silent=True) or {}
    system_name = body.get("system_name", "Unnamed System")

    try:
        report = api.audit(system_name)
    except Exception as e:
        traceback.print_exc()
        latency_ms = (time.perf_counter() - t0) * 1000
        nti_log.log_request(request_id, "/v1/audit", 500, latency_ms, request._api_key_id)
        return jsonify({"error": "internal error"}), 500

    # Strip notes from each certificate in the audit report
    if "certificates" in report and isinstance(report["certificates"], list):
        report["certificates"] = [_strip_cert(c) for c in report["certificates"]]

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/audit", 200, latency_ms, request._api_key_id)
    return jsonify(report), 200


@bp.route("/v1/theorems", methods=["GET"])
@require_api_key
def theorems():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    raw    = api.theorems()
    result = [_strip_theorem(t) for t in raw]

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/theorems", 200, latency_ms, request._api_key_id)
    return jsonify(result), 200


@bp.route("/v1/zeros", methods=["GET"])
@require_api_key
def zeros():
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


@bp.route("/v1/runner/status", methods=["GET"])
@require_api_key
def runner_status():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    try:
        conn = database.db_connect()
        cur  = _dict_cursor(conn)
        cur.execute("""
            SELECT
                bit_length,
                COUNT(*)                         AS count,
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


@bp.route("/v1/certificates", methods=["GET"])
@require_api_key
def certificates():
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
        cur  = _dict_cursor(conn)

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
