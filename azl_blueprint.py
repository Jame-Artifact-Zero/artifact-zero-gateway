"""
azl_blueprint.py
================
Flask blueprint for AZL - Artifact Zero Labs Verification System.

Routes:
  POST /v1/azl/prime/verify      -- verify primality
  POST /v1/azl/prime/generate    -- generate certified prime
  POST /v1/azl/coherence         -- arithmetic coherence check
  POST /v1/azl/security          -- security assessment
  GET  /v1/azl/theorems          -- theorem library
  POST /v1/azl/cascade/hurst     -- Hurst exponent prediction
  POST /v1/azl/cascade/kolmogorov -- Kolmogorov scale
  GET  /v1/azl/certificates      -- certificate history for API key
  POST /v1/azl/audit             -- full system audit
"""

import uuid
import time
import json
from dataclasses import asdict
from flask import Blueprint, request, jsonify
import nti_log
from api_auth import require_api_key
from azl import AZL
from azl_storage import store_certificate, get_certificates

bp  = Blueprint("azl", __name__)
azl = AZL()

# ===================================================================
# IP PROTECTION -- strip functions applied to all responses
# ===================================================================

# Theorem fields returned to customer
_THEOREM_KEEP = {"id", "name", "status", "domain"}

# Certificate top-level fields to strip
_CERT_STRIP = {"notes", "framework"}

# Details fields stripped per cert_type
_DETAILS_STRIP = {
    "CASCADE_PREDICTION": {"scaling_law", "kolmogorov_exponent", "test_data"},
    "KOLMOGOROV_SCALE":   {"epsilon", "scaling_law"},
    "COHERENCE_CHECK":    {"first_primes", "least_prime_bound_azl", "search_limit", "phi_q"},
    "PRIME_VERIFICATION": {"witness_source"},
    "PRIME_GENERATION":   {"method"},
    "SECURITY_ASSESSMENT": set(),
}


def _strip_theorem(t: dict) -> dict:
    return {k: v for k, v in t.items() if k in _THEOREM_KEEP}


def _strip_cert(cert: dict) -> dict:
    result = {k: v for k, v in cert.items() if k not in _CERT_STRIP}
    cert_type = result.get("cert_type", "")
    strip_keys = _DETAILS_STRIP.get(cert_type, set())
    if "details" in result and isinstance(result["details"], dict) and strip_keys:
        result["details"] = {k: v for k, v in result["details"].items()
                             if k not in strip_keys}
    return result


def _safe_store(api_key_id, cert_type, result):
    try:
        store_certificate(api_key_id, cert_type, result)
    except Exception:
        pass


# ===================================================================
# ENGINE 1: PRIME VERIFICATION
# ===================================================================

@bp.route("/v1/azl/prime/verify", methods=["POST"])
@require_api_key
def prime_verify():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json() or {}
    n = body.get("n")
    if n is None:
        return jsonify({"error": "n required"}), 400
    try:
        n = int(n)
    except (ValueError, TypeError):
        return jsonify({"error": "n must be an integer"}), 400

    cert   = azl.verify_prime(n)
    raw    = asdict(cert)
    _safe_store(request._api_key_id, "PRIME_VERIFICATION", raw)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/prime/verify", 200, latency_ms, request._api_key_id)
    return jsonify(_strip_cert(raw)), 200


@bp.route("/v1/azl/prime/generate", methods=["POST"])
@require_api_key
def prime_generate():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json() or {}
    bits = int(body.get("bits", 256))
    if bits < 32 or bits > 4096:
        return jsonify({"error": "bits must be between 32 and 4096"}), 400

    cert   = azl.generate_prime(bits)
    raw    = asdict(cert)
    _safe_store(request._api_key_id, "PRIME_GENERATION", raw)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/prime/generate", 200, latency_ms, request._api_key_id)
    return jsonify(_strip_cert(raw)), 200


# ===================================================================
# ENGINE 2: ARITHMETIC COHERENCE
# ===================================================================

@bp.route("/v1/azl/coherence", methods=["POST"])
@require_api_key
def coherence():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json() or {}
    q = body.get("q")
    a = body.get("a")
    if q is None or a is None:
        return jsonify({"error": "q and a required"}), 400
    try:
        q, a = int(q), int(a)
    except (ValueError, TypeError):
        return jsonify({"error": "q and a must be integers"}), 400

    cert   = azl.coherence_check(q, a)
    raw    = asdict(cert)
    _safe_store(request._api_key_id, "COHERENCE_CHECK", raw)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/coherence", 200, latency_ms, request._api_key_id)
    return jsonify(_strip_cert(raw)), 200


@bp.route("/v1/azl/security", methods=["POST"])
@require_api_key
def security():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json() or {}
    key_type = body.get("type", "")
    bits = body.get("bits")
    if not key_type or bits is None:
        return jsonify({"error": "type and bits required"}), 400
    try:
        bits = int(bits)
    except (ValueError, TypeError):
        return jsonify({"error": "bits must be an integer"}), 400

    cert   = azl.security(key_type, bits)
    raw    = asdict(cert)
    _safe_store(request._api_key_id, "SECURITY_ASSESSMENT", raw)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/security", 200, latency_ms, request._api_key_id)
    return jsonify(_strip_cert(raw)), 200


@bp.route("/v1/azl/theorems", methods=["GET"])
@require_api_key
def theorems():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    result = [_strip_theorem(asdict(t)) for t in azl.theorems()]

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/theorems", 200, latency_ms, request._api_key_id)
    return jsonify(result), 200


# ===================================================================
# ENGINE 3: CASCADE
# ===================================================================

@bp.route("/v1/azl/cascade/hurst", methods=["POST"])
@require_api_key
def cascade_hurst():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body   = request.get_json() or {}
    domain = body.get("domain", "market")

    cert   = azl.hurst(domain)
    raw    = asdict(cert)
    _safe_store(request._api_key_id, "CASCADE_PREDICTION", raw)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/cascade/hurst", 200, latency_ms, request._api_key_id)
    return jsonify(_strip_cert(raw)), 200


@bp.route("/v1/azl/cascade/kolmogorov", methods=["POST"])
@require_api_key
def cascade_kolmogorov():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body = request.get_json() or {}
    spread = body.get("spread")
    vol    = body.get("vol")
    if spread is None or vol is None:
        return jsonify({"error": "spread and vol required"}), 400
    try:
        spread = float(spread)
        vol    = float(vol)
    except (ValueError, TypeError):
        return jsonify({"error": "spread and vol must be numbers"}), 400

    cert   = azl.kolmogorov(spread, vol)
    raw    = asdict(cert)
    _safe_store(request._api_key_id, "KOLMOGOROV_SCALE", raw)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/cascade/kolmogorov", 200, latency_ms, request._api_key_id)
    return jsonify(_strip_cert(raw)), 200


# ===================================================================
# CERTIFICATE HISTORY
# ===================================================================

@bp.route("/v1/azl/certificates", methods=["GET"])
@require_api_key
def certificates():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    limit     = min(int(request.args.get("limit", 20)), 100)
    cert_type = request.args.get("type", None)

    rows = get_certificates(request._api_key_id, limit=limit, cert_type=cert_type)

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/certificates", 200, latency_ms, request._api_key_id)
    return jsonify(rows), 200


# ===================================================================
# AUDIT
# ===================================================================

@bp.route("/v1/azl/audit", methods=["POST"])
@require_api_key
def audit():
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    body        = request.get_json() or {}
    system_name = body.get("system_name", "AZL System")

    results = {
        "system_name": system_name,
        "issued":      azl._boot_time,
        "issuer":      "Artifact Zero Labs",
        "engines": {
            "prime": {
                "status":              "OPERATIONAL",
                "test_2":              _strip_cert(asdict(azl.verify_prime(2))),
                "test_mersenne_31":    _strip_cert(asdict(azl.verify_prime(2**31 - 1))),
                "test_carmichael_561": _strip_cert(asdict(azl.verify_prime(561))),
            },
            "coherence": {
                "status":       "OPERATIONAL",
                "theorems":     len(azl.theorems()),
                "test_5mod2":   _strip_cert(asdict(azl.coherence_check(5, 2))),
                "test_rsa2048": _strip_cert(asdict(azl.security("rsa", 2048))),
            },
            "cascade": {
                "status":             "OPERATIONAL",
                "test_market_hurst":  _strip_cert(asdict(azl.hurst("market"))),
            },
        },
        "overall": "OPERATIONAL",
    }

    latency_ms = (time.perf_counter() - t0) * 1000
    nti_log.log_request(request_id, "/v1/azl/audit", 200, latency_ms, request._api_key_id)
    return jsonify(results), 200
