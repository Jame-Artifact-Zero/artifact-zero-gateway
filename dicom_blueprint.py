"""
================================================================================
ARTIFACT ZERO LABS — DICOM Analysis Blueprint
Merge into: blueprints/ or routes/ directory

Register in app.py:
    from dicom_blueprint import dicom_bp
    app.register_blueprint(dicom_bp, url_prefix='/dicom')

Endpoints:
    POST /dicom/analyze          — submit a DICOM for analysis
    GET  /dicom/study/<study_id> — retrieve a stored study result
    GET  /dicom/status           — API health and customer usage stats
    POST /dicom/customer         — create a new customer (admin only)
    GET  /dicom/demo             — public demo UI (no auth)
================================================================================
"""

import time
import json
import db as _db_module
from flask import Blueprint, request, jsonify, g, render_template

from api_auth          import require_api_key
from dicom_customer    import load_dicom_customer, dicom_profile_required, \
                              get_effective_params, check_baa_required, \
                              record_study_call, get_customer_stats
from dicom_processor_api import process_dicom_bytes
from dicom_storage     import store_study_record, store_measurements, find_prior_study
from dicom_return      import build_response

dicom_bp = Blueprint('dicom', __name__)


class _DBWrapper:
    """
    Thin wrapper around a psycopg2 connection that mimics the
    SQLAlchemy-style db.execute() / db.commit() interface used
    throughout the DICOM blueprint.
    """
    def __init__(self, conn):
        self._conn = conn
        self._cur  = conn.cursor()

    def execute(self, sql, params=None):
        self._cur.execute(sql, params or ())
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def commit(self):
        self._conn.commit()

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass


def _get_db() -> _DBWrapper:
    """Open a new DB connection and return a wrapper."""
    return _DBWrapper(_db_module.db_connect())

# ════════════════════════════════════════════════════════════════════
# GET /dicom/demo
# Public demo UI — no auth required
# ════════════════════════════════════════════════════════════════════

@dicom_bp.route('/demo', methods=['GET'])
def demo():
    """Public demo page. No auth. Synthetic data only."""
    return render_template('az_demo.html')


# ════════════════════════════════════════════════════════════════════
# POST /dicom/analyze
# The main endpoint. Accepts a DICOM file, returns analysis.
# ════════════════════════════════════════════════════════════════════

@dicom_bp.route('/analyze', methods=['POST'])
@require_api_key
@dicom_profile_required
def analyze():
    """
    Submit a DICOM file for analysis.

    Multipart form:
      dicom           — the DICOM file (required)
      params          — JSON string of analysis parameters (optional)

    Or JSON body with base64-encoded DICOM:
      { "dicom_b64": "...", "params": {...} }

    Parameters (all optional — customer defaults apply if not specified):
      storage         — none | session | local | encrypted | customer | full
      analysis        — speed | profile | standard | full | impression | longitudinal
      body_part       — auto | cspine | brain | wrist | pelvis
      return_format   — json | fhir | dicom_sr | webhook
      encrypt_key     — PEM public key for payload encryption
      webhook_url     — URL to POST results to (async)
      customer_study_ref — your reference ID for this study

    Returns:
      JSON with study_id, status, impression flags, sequence measurements.
    """
    t_start = time.perf_counter()
    customer = g.customer

    # ── Parse request parameters ──────────────────────────────────
    request_params = {}
    if request.content_type and 'multipart' in request.content_type:
        raw_params = request.form.get('params', '{}')
        try:
            request_params = json.loads(raw_params)
        except Exception:
            request_params = {}
        dicom_file = request.files.get('dicom')
        if not dicom_file:
            return jsonify({'error': 'No DICOM file provided', 'code': 'MISSING_FILE'}), 400
        raw_bytes = dicom_file.read()
    elif request.is_json:
        data = request.get_json(silent=True) or {}
        request_params = data.get('params', {})
        dicom_b64 = data.get('dicom_b64')
        if not dicom_b64:
            return jsonify({'error': 'No DICOM data provided', 'code': 'MISSING_FILE'}), 400
        import base64
        try:
            raw_bytes = base64.b64decode(dicom_b64)
        except Exception:
            return jsonify({'error': 'Invalid base64 DICOM data', 'code': 'INVALID_DATA'}), 400
    else:
        raw_bytes = request.data
        if not raw_bytes:
            return jsonify({'error': 'No DICOM data provided', 'code': 'MISSING_FILE'}), 400

    if len(raw_bytes) < 128:
        return jsonify({'error': 'File too small to be a valid DICOM', 'code': 'INVALID_FILE'}), 400

    # ── Zip extraction -- unpack and pass all .dcm files to pipeline ──
    import zipfile, io as _io, tempfile, os
    if raw_bytes[:2] == b'PK':
        try:
            zf = zipfile.ZipFile(_io.BytesIO(raw_bytes))
            dcm_names = [n for n in zf.namelist()
                         if not n.startswith('__MACOSX') and
                         not os.path.basename(n).startswith('.') and
                         (n.lower().endswith('.dcm') or '.' not in os.path.basename(n))]
            if not dcm_names:
                return jsonify({'error': 'No DICOM files found in zip', 'code': 'INVALID_FILE'}), 400

            # Write all files to a temp dir and pass the dir to the pipeline
            tmp_dir = tempfile.mkdtemp()
            extracted = []
            for name in dcm_names:
                data = zf.read(name)
                if len(data) < 128:
                    continue
                # Preserve subdirectory structure to avoid filename collisions
                safe_name = name.replace('/', os.sep).replace('\\', os.sep)
                dest = os.path.join(tmp_dir, safe_name)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, 'wb') as fh:
                    fh.write(data)
                extracted.append(dest)
            zf.close()

            if not extracted:
                return jsonify({'error': 'No valid DICOM files in zip', 'code': 'INVALID_FILE'}), 400

            # Pass the extracted file list via params so processor_api can use them
            request_params['_extracted_dcm_paths'] = extracted
            request_params['_tmp_dir'] = tmp_dir
            # Use first file bytes as the primary input (pipeline will use _extracted_dcm_paths)
            raw_bytes = open(extracted[0], 'rb').read()
        except zipfile.BadZipFile:
            return jsonify({'error': 'Invalid zip file', 'code': 'INVALID_FILE'}), 400

    # ── Merge parameters: request > customer defaults > system defaults ──
    params = get_effective_params(customer, request_params)

    # ── BAA check for local storage ───────────────────────────────
    if check_baa_required(customer, params.get('storage', 'none')):
        return jsonify({
            'error': 'Storage mode "local" requires a signed BAA. Contact support.',
            'code':  'BAA_REQUIRED'
        }), 403

    # ── Run the pipeline ──────────────────────────────────────────
    t_pipeline_start = time.perf_counter()

    prior_study = None
    if params.get('analysis') == 'longitudinal':
        prior_study = _get_prior_study(customer, params)

    result = process_dicom_bytes(raw_bytes, params, prior_study)

    t_pipeline_ms = round((time.perf_counter() - t_pipeline_start) * 1000, 2)
    result['pipeline_ms'] = t_pipeline_ms

    if result.get('error') and not result.get('sequences'):
        return jsonify({'error': result['error'], 'code': 'PIPELINE_ERROR'}), 500

    # ── Store audit record (always) ───────────────────────────────
    t_storage_start = time.perf_counter()
    study_id = None

    try:
        db = _get_db()
        dicom_meta = _extract_meta_for_storage(result)
        timing = {
            'total_ms':    round((time.perf_counter() - t_start) * 1000, 2),
            'pipeline_ms': t_pipeline_ms,
            'storage_ms':  None,
            'return_ms':   None,
        }
        study_id = store_study_record(db, customer, params, dicom_meta, result, timing)

        # Store measurements if requested
        storage_mode = params.get('storage', 'none')
        if storage_mode in ('local', 'full', 'encrypted'):
            store_measurements(db, study_id, result, storage_mode,
                               params.get('encrypt_key'))

        # Update customer usage
        record_study_call(db, customer['api_key_id'], study_id)

    except Exception as e:
        # Storage failure should not block the response
        import logging
        import traceback
        logging.error(f"Storage error for customer {customer['api_key_id']}: {e}")
        traceback.print_exc()

    t_storage_ms = round((time.perf_counter() - t_storage_start) * 1000, 2)

    # ── Log the call ──────────────────────────────────────────────
    try:
        from nti_log import log_event  # existing logging module
        log_event('dicom_analyzed', {
            'customer_id':   customer['api_key_id'],
            'customer_name': customer['customer_name'],
            'study_id':      study_id,
            'body_part':     result.get('body_part'),
            'status':        result.get('impression', {}).get('status'),
            'pipeline_ms':   t_pipeline_ms,
            'storage_ms':    t_storage_ms,
            'storage_mode':  params.get('storage', 'none'),
            'analysis_level': params.get('analysis', 'full'),
        })
    except Exception as e:
        import logging
        logging.warning(f"log_event failed (non-blocking): {e}")

    # ── Build and return response ─────────────────────────────────
    t_return_start = time.perf_counter()
    try:
        db = _get_db()
        response = build_response(result, params, customer, study_id or 'unsaved', db)
    except Exception as e:
        logging.warning(f"Response build fallback (no db): {e}")
        response = build_response(result, params, customer, study_id or 'unsaved', None)

    t_total_ms = round((time.perf_counter() - t_start) * 1000, 2)
    response['_meta'] = {
        'total_ms':    t_total_ms,
        'pipeline_ms': t_pipeline_ms,
    }

    return jsonify(response), 200


# ════════════════════════════════════════════════════════════════════
# GET /dicom/study/<study_id>
# Retrieve a stored study result
# ════════════════════════════════════════════════════════════════════

@dicom_bp.route('/study/<study_id>', methods=['GET'])
@require_api_key
@dicom_profile_required
def get_study(study_id):
    """
    Retrieve a stored study result by study_id.
    Only returns studies belonging to the authenticated customer.
    """
    customer = g.customer
    try:
        db = _get_db()
        row = db.execute("""
            SELECT id, study_date, body_part, impression_status,
                   flags_critical, flags_moderate, flags_finding,
                   storage_mode, called_at, payload_encrypted
            FROM az_studies
            WHERE id = %s AND customer_id = %s""",
            (study_id, customer['api_key_id'])
        ).fetchone()

        if not row:
            return jsonify({'error': 'Study not found', 'code': 'NOT_FOUND'}), 404

        # Get sequences
        seqs = db.execute("""
            SELECT series_description, seq_type, orientation,
                   min_gap, compression_pct, peak_left_asym,
                   peak_disagree_score, flags_json, impression_text
            FROM az_sequences WHERE study_id = %s""",
            (study_id,)
        ).fetchall()

        return jsonify({
            'study_id':        str(row[0]),
            'study_date':      row[1].isoformat() if row[1] else None,
            'body_part':       row[2],
            'status':          row[3],
            'flags_critical':  row[4],
            'flags_moderate':  row[5],
            'flags_finding':   row[6],
            'storage_mode':    row[7],
            'called_at':       row[8].isoformat() if row[8] else None,
            'payload_encrypted': row[9],
            'sequences': [
                {
                    'description':   s[0],
                    'type':          s[1],
                    'orientation':   s[2],
                    'min_gap':       float(s[3]) if s[3] else None,
                    'compression':   float(s[4]) if s[4] else None,
                    'peak_left_asym': float(s[5]) if s[5] else None,
                    'peak_disagree': float(s[6]) if s[6] else None,
                    'flags':         s[7],
                    'impression':    s[8],
                }
                for s in seqs
            ]
        }), 200

    except Exception as e:
        return jsonify({'error': str(e), 'code': 'SERVER_ERROR'}), 500


# ════════════════════════════════════════════════════════════════════
# GET /dicom/status
# API health and customer usage
# ════════════════════════════════════════════════════════════════════

@dicom_bp.route('/status', methods=['GET'])
@require_api_key
@dicom_profile_required
def status():
    """API health check and customer usage statistics."""
    customer = g.customer
    try:
        db = _get_db()
        stats = get_customer_stats(db, customer['api_key_id'])
    except Exception:
        stats = {}

    return jsonify({
        'status':          'ok',
        'customer_name':   customer['customer_name'],
        'tier':            customer['tier'],
        'baa_signed':      customer['baa_signed'],
        'usage':           stats,
        'analysis_levels': ['speed','profile','standard','full','impression','longitudinal'],
        'return_formats':  ['json','fhir','dicom_sr','webhook'],
    }), 200


# ════════════════════════════════════════════════════════════════════
# POST /dicom/customer  (admin only)
# ════════════════════════════════════════════════════════════════════

@dicom_bp.route('/customer', methods=['POST'])
@require_api_key
@dicom_profile_required
def create_customer_endpoint():
    """
    Create a new customer account. Admin tier only.
    Returns the raw API key — shown once, never stored.
    """
    customer = g.customer
    if request._api_tier != 'enterprise':
        return jsonify({'error': 'Admin access required', 'code': 'FORBIDDEN'}), 403

    data = request.get_json(silent=True) or {}
    required = ['customer_name', 'contact_name', 'contact_email']
    missing  = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({'error': f"Missing fields: {missing}", 'code': 'MISSING_FIELDS'}), 400

    try:
        db = _get_db()
        from dicom_customer import create_customer
        cid, raw_key = create_customer(
            db,
            data['customer_name'],
            data['contact_name'],
            data['contact_email'],
            data.get('tier', 'standard')
        )
        return jsonify({
            'customer_id':  cid,
            'api_key':      raw_key,
            'warning':      'Store this API key securely. It will not be shown again.',
        }), 201
    except Exception as e:
        return jsonify({'error': str(e), 'code': 'CREATE_FAILED'}), 500


# ════════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════════

def _extract_meta_for_storage(result: dict) -> dict:
    """Extract DICOM metadata fields needed for study record storage."""
    return {
        'study_instance_uid': result.get('study_instance_uid'),
        'study_date':         result.get('study_date'),
        'study_description':  result.get('study_description'),
        'accession_number':   result.get('accession_number'),
        'institution_name':   result.get('institution_name'),
        'body_part':          result.get('body_part'),
        'modality':           result.get('modality'),
        'manufacturer':       result.get('manufacturer'),
        'model_name':         result.get('model_name'),
        'device_serial':      result.get('device_serial'),
        'field_strength':     result.get('field_strength'),
        'patient_id':         result.get('patient_id'),
        'patient_age':        result.get('patient_age'),
        'patient_sex':        result.get('patient_sex'),
        'software_version':   result.get('software_version'),
    }


def _get_prior_study(customer: dict, params: dict):
    """Look up prior study for longitudinal comparison."""
    try:
        db = _get_db()
        from dicom_storage import find_prior_study_with_measurements
        from dicom_customer import hash_patient_id
        patient_hash = hash_patient_id(
            params.get('patient_id', ''), str(customer['api_key_id']))
        return find_prior_study_with_measurements(
            db, customer['api_key_id'], patient_hash,
            params.get('body_part', 'auto').upper())
    except Exception:
        return None