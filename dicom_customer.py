"""
================================================================================
ARTIFACT ZERO LABS — DICOM Customer Profile Loader
Merge into: services/ or alongside existing auth

Path B auth integration:
  - require_api_key (from api_auth) handles all key validation
  - After it runs, request._api_key_id holds the validated key ID
  - load_dicom_customer() joins to az_customer_profiles on api_key_id
  - Loads DICOM profile into g.customer
  - Returns 403 if key exists but no DICOM profile registered

Every DICOM route calls load_dicom_customer() first, after @require_api_key.
================================================================================
"""

from flask import request, g, jsonify
import db as database


# ════════════════════════════════════════════════════════════════════
# PROFILE LOADER
# Called at the top of every DICOM route after @require_api_key passes.
# ════════════════════════════════════════════════════════════════════

def load_dicom_customer():
    """
    Load DICOM customer profile into g.customer.

    Reads request._api_key_id set by require_api_key decorator.
    Joins to az_customer_profiles on api_key_id.
    Returns g.customer dict if found, None if no DICOM profile exists.

    Caller returns 403 if this returns None — key is valid but has
    no DICOM profile registered.
    """
    key_id = request._api_key_id
    conn = database.db_connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, api_key_id, customer_name, contact_name, contact_email,
                      storage_default, analysis_default, encrypt_public_key,
                      webhook_url, webhook_secret, baa_signed, baa_signed_date,
                      tier, studies_count, last_call_at, active
               FROM az_customer_profiles WHERE api_key_id = %s""",
            (key_id,)
        )
        row = cur.fetchone()
    except Exception as e:
        import logging, traceback
        logging.error(f"load_dicom_customer DB error: {e}")
        traceback.print_exc()
        return None
    finally:
        conn.close()

    if not row:
        return None

    g.customer = {
        'id':                 row[0],
        'api_key_id':         row[1],
        'customer_name':      row[2],
        'contact_name':       row[3],
        'contact_email':      row[4],
        'storage_default':    row[5],
        'analysis_default':   row[6],
        'encrypt_public_key': row[7],
        'webhook_url':        row[8],
        'webhook_secret':     row[9],
        'baa_signed':         row[10],
        'baa_signed_date':    row[11],
        'tier':               row[12],
        'studies_count':      row[13],
        'last_call_at':       row[14],
        'active':             row[15],
    }
    # Expose api_key_id directly for storage operations
    g.customer['api_key_id'] = key_id
    # Expose tier from request if profile doesn't have it
    if not g.customer.get('tier'):
        g.customer['tier'] = getattr(request, '_api_tier', 'standard')
    return g.customer


def dicom_profile_required(f):
    """
    Convenience decorator that calls load_dicom_customer() and returns
    403 if no DICOM profile is found.

    Usage (after @require_api_key):

        @dicom_bp.route('/analyze', methods=['POST'])
        @require_api_key
        @dicom_profile_required
        def analyze():
            ...

    Note: @require_api_key must run FIRST — it sets request._api_key_id.
    """
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        customer = load_dicom_customer()
        if customer is None:
            return jsonify({
                'error': 'No DICOM profile found for this API key. Contact support to enable DICOM access.',
                'code':  'NO_DICOM_PROFILE'
            }), 403
        return f(*args, **kwargs)
    return decorated


# ════════════════════════════════════════════════════════════════════
# PARAMETER MERGING
# Unchanged from original — merges request params over customer defaults.
# ════════════════════════════════════════════════════════════════════

def get_effective_params(customer: dict, request_params: dict) -> dict:
    """
    Merge customer defaults with per-request parameters.
    Request parameters take precedence over customer defaults.
    Customer defaults take precedence over system defaults.
    """
    system_defaults = {
        'storage':            'none',
        'analysis':           'full',
        'body_part':          'auto',
        'return_format':      'json',
        'encrypt_key':        None,
        'webhook_url':        None,
        'customer_study_ref': None,
    }
    customer_defaults = {
        'storage':     customer.get('storage_default', 'none'),
        'analysis':    customer.get('analysis_default', 'full'),
        'encrypt_key': customer.get('encrypt_public_key'),
        'webhook_url': customer.get('webhook_url'),
    }
    return {**system_defaults, **customer_defaults, **{
        k: v for k, v in request_params.items() if v is not None
    }}


def check_baa_required(customer: dict, storage_mode: str) -> bool:
    """
    Returns True if storage mode requires a BAA and customer has not signed.
    """
    baa_required_modes = {'local', 'full'}
    return storage_mode in baa_required_modes and not customer.get('baa_signed')


# ════════════════════════════════════════════════════════════════════
# USAGE TRACKING
# ════════════════════════════════════════════════════════════════════

def record_study_call(db, api_key_id: str, study_id: str):
    """Update customer usage counters after a successful API call."""
    try:
        db.execute(
            """UPDATE az_customer_profiles
               SET studies_count = studies_count + 1,
                   last_call_at  = NOW()
               WHERE api_key_id = %s""",
            (api_key_id,)
        )
        db.commit()
    except Exception as e:
        import logging, traceback
        logging.error(f"record_study_call error: {e}")
        traceback.print_exc()


def get_customer_stats(db, api_key_id: str) -> dict:
    """Return usage statistics for a customer."""
    try:
        row = db.execute(
            """SELECT
                   COUNT(*)                                              as total_studies,
                   COUNT(*) FILTER (WHERE impression_status='CRITICAL') as critical_count,
                   COUNT(*) FILTER (WHERE impression_status='MODERATE') as moderate_count,
                   AVG(response_ms)                                      as avg_response_ms,
                   MAX(called_at)                                        as last_call
               FROM az_studies
               WHERE customer_id = %s""",
            (api_key_id,)
        ).fetchone()
        return {
            'total_studies':   row[0],
            'critical_count':  row[1],
            'moderate_count':  row[2],
            'avg_response_ms': round(float(row[3]), 1) if row[3] else None,
            'last_call':       row[4].isoformat() if row[4] else None,
        }
    except Exception as e:
        import logging, traceback
        logging.error(f"get_customer_stats error: {e}")
        traceback.print_exc()
        return {}


# ════════════════════════════════════════════════════════════════════
# PATIENT DE-IDENTIFICATION
# ════════════════════════════════════════════════════════════════════

def hash_patient_id(patient_id: str, api_key_id: str) -> str:
    """
    One-way SHA-256 hash of patient ID scoped to api_key_id.
    Enables longitudinal matching within a customer without storing PHI.
    Scoped to api_key_id so same patient at two customers gets different hashes.
    """
    import hashlib
    combined = f"{api_key_id}:{patient_id}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]
