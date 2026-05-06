"""
================================================================================
ARTIFACT ZERO LABS -- Return Path Handler

Handles all return formats:
  json      -- standard HTTP JSON response (synchronous)
  fhir      -- HL7 FHIR Observation resource
  dicom_sr  -- DICOM Structured Report (write back to PACS)
  webhook   -- async POST to customer endpoint

Also handles:
  - Payload encryption before return
  - Webhook queue for async delivery
  - Retry logic for failed webhooks
================================================================================
"""

import json
import hmac
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests


# ================================================================================
# IP PROTECTION -- sanitize functions applied to all response paths
# ================================================================================

_SEQ_KEEP = {
    'series_description', 'seq_type', 'n_slices', 'orientation',
    'gap', 'mean_fraction', 'std_fraction',
    'min_gap', 'max_gap', 'compression_pct',
    'min_gap_slice', 'min_gap_frac_inf',
    'peak_left_asym', 'pct_left_dominant', 'pct_right_dominant',
    'peak_disagree_score',
    'flag_critical', 'flag_moderate', 'flag_finding', 'flag_normal',
    'flags_json',
}


def _sanitize_sequence(seq: dict) -> dict:
    """
    Strip internal pipeline fields and threshold-revealing details.
    Removes: ref_A, ref_B, rms_vs_standard, speedup_x, timing_ms,
             all _run flags, mean_disagree_score, peak_disagree_slice,
             n_compress_runs, run_widths_mm, slices array.
    """
    out = {k: v for k, v in seq.items() if k in _SEQ_KEEP}
    if 'flags_json' in out:
        out['flags_json'] = [
            {
                'severity': f.get('severity'),
                'label':    f.get('label'),
                'sequence': f.get('sequence'),
            }
            for f in (out['flags_json'] or [])
        ]
    return out


def _sanitize_flags(flags: list) -> list:
    """Strip detail and source fields from impression flags."""
    return [
        {
            'severity': f.get('severity'),
            'label':    f.get('label'),
            'sequence': f.get('sequence'),
        }
        for f in (flags or [])
    ]


def _sanitize_longitudinal(longitudinal: dict) -> dict:
    """Strip measurement deltas and raw longitudinal flags. Return summary only."""
    return {
        'prior_study_id':          longitudinal.get('prior_study_id'),
        'prior_study_date':        longitudinal.get('prior_study_date'),
        'prior_status':            longitudinal.get('prior_status'),
        'current_status':          longitudinal.get('current_status'),
        'change':                  longitudinal.get('change'),
        'longitudinal_flag_count': longitudinal.get('longitudinal_flag_count', 0),
        'prior_critical':          longitudinal.get('prior_critical', 0),
        'prior_moderate':          longitudinal.get('prior_moderate', 0),
    }


# ================================================================================
# MAIN RETURN DISPATCHER
# ================================================================================

def build_response(result: dict, params: dict, customer: dict,
                   study_id: str, db=None) -> dict:
    """
    Build the final response payload based on requested return format.
    Applies encryption if requested. Queues webhook if requested.
    Returns the dict that Flask will jsonify and send back to the caller.
    """
    return_format = params.get('return_format', 'json')
    encrypt_key   = params.get('encrypt_key') or customer.get('encrypt_public_key')
    webhook_url   = params.get('webhook_url') or customer.get('webhook_url')

    if return_format == 'fhir':
        payload = _build_fhir_observation(result, study_id, customer)
    elif return_format == 'dicom_sr':
        payload = _build_dicom_sr_reference(result, study_id)
    else:
        payload = _build_json_response(result, study_id, customer)

    if encrypt_key:
        from dicom_encryption import encrypt_payload, validate_public_key
        if validate_public_key(encrypt_key):
            payload = encrypt_payload(payload, encrypt_key)
        else:
            payload['encryption_warning'] = 'Invalid public key -- payload not encrypted'

    if webhook_url and db is not None:
        _queue_webhook(db, study_id, customer['api_key_id'], webhook_url,
                       payload, bool(encrypt_key))

    return payload


# ================================================================================
# JSON RESPONSE (default)
# ================================================================================

def _build_json_response(result: dict, study_id: str, customer: dict) -> dict:
    """Standard JSON response -- the default return format."""
    impression   = result.get('impression', {})
    flags        = _sanitize_flags(impression.get('flags', []))
    longitudinal = result.get('longitudinal')

    response = {
        'study_id':  study_id,
        'status':    impression.get('status', 'PENDING'),
        'called_at': datetime.now(timezone.utc).isoformat(),

        'study': {
            'date':        result.get('study_date'),
            'description': result.get('study_description'),
            'body_part':   result.get('body_part'),
            'modality':    result.get('modality'),
        },

        'scanner': {
            'manufacturer':   result.get('manufacturer'),
            'model':          result.get('model_name'),
            'field_strength': result.get('field_strength'),
            'institution':    result.get('institution_name'),
        },

        'impression': {
            'status': impression.get('status', 'PENDING'),
            'flags':  flags,
            'counts': {
                'critical': sum(1 for f in flags if f.get('severity') == 'CRITICAL'),
                'moderate': sum(1 for f in flags if f.get('severity') == 'MODERATE'),
                'finding':  sum(1 for f in flags if f.get('severity') == 'FINDING'),
                'normal':   sum(1 for f in flags if f.get('severity') == 'NORMAL'),
            }
        },

        'sequences': [_sanitize_sequence(s) for s in result.get('sequences', [])],

        'performance': {
            'sequences_found':     result.get('sequences_found', 0),
            'sequences_processed': result.get('sequences_processed', 0),
            'pipeline_ms':         result.get('pipeline_ms'),
        }
    }

    if longitudinal:
        response['prior_state'] = _sanitize_longitudinal(longitudinal)

    return response


# ================================================================================
# FHIR OBSERVATION
# ================================================================================

def _build_fhir_observation(result: dict, study_id: str, customer: dict) -> dict:
    """Build an HL7 FHIR R4 Observation resource."""
    impression = result.get('impression', {})
    flags      = _sanitize_flags(impression.get('flags', []))
    status_map = {
        'CRITICAL': 'abnormal',
        'MODERATE': 'abnormal',
        'FINDING':  'borderline',
        'NORMAL':   'normal',
        'CLEAN':    'normal',
    }

    return {
        'resourceType': 'Observation',
        'id': study_id,
        'status': 'final',
        'category': [{
            'coding': [{
                'system':  'http://terminology.hl7.org/CodeSystem/observation-category',
                'code':    'imaging',
                'display': 'Imaging'
            }]
        }],
        'code': {
            'coding': [{
                'system':  'http://loinc.org',
                'code':    '18748-4',
                'display': 'Diagnostic imaging study'
            }],
            'text': f"AZ Signal Analysis -- {result.get('body_part', 'Unknown')}"
        },
        'effectiveDateTime': result.get('study_date'),
        'issued': datetime.now(timezone.utc).isoformat(),
        'interpretation': [{
            'coding': [{
                'system': 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
                'code':   status_map.get(impression.get('status', 'NORMAL'), 'N'),
            }]
        }],
        'component': [
            {
                'code': {'text': f['label']},
                'interpretation': [{'coding': [{'code': f['severity']}]}]
            }
            for f in flags
        ],
        'extension': [{
            'url': 'https://artifact0.com/fhir/extensions/az-study-id',
            'valueString': study_id
        }]
    }


# ================================================================================
# DICOM SR REFERENCE
# ================================================================================

def _build_dicom_sr_reference(result: dict, study_id: str) -> dict:
    """Returns a reference dict describing the DICOM SR to be created."""
    impression = result.get('impression', {})
    return {
        'dicom_sr':           True,
        'study_id':           study_id,
        'study_instance_uid': result.get('study_instance_uid'),
        'sr_document_title':  f"AZ Signal Analysis -- {result.get('body_part', 'Unknown')}",
        'completion_flag':    'COMPLETE',
        'verification_flag':  'UNVERIFIED',
        'content_sequence': [
            {
                'relationship_type': 'CONTAINS',
                'value_type':        'TEXT',
                'concept_name':      {'code': f['label']},
                'text_value':        f['severity']
            }
            for f in _sanitize_flags(impression.get('flags', []))
        ],
        'impression_status': impression.get('status', 'PENDING'),
    }


# ================================================================================
# WEBHOOK
# ================================================================================

def _queue_webhook(db, study_id: str, customer_id: str,
                   webhook_url: str, payload: dict, encrypted: bool):
    """Queue a webhook for async delivery."""
    db.execute("""
        INSERT INTO az_webhook_queue
            (study_id, customer_id, webhook_url, payload_json,
             payload_encrypted, next_attempt_at)
        VALUES (%s, %s, %s, %s, %s, NOW())""",
        (study_id, customer_id, webhook_url,
         json.dumps(payload, default=str), encrypted)
    )
    db.commit()


def deliver_webhook(db, queue_row: dict) -> bool:
    """
    Attempt to deliver a queued webhook.
    Returns True if delivered successfully.
    """
    webhook_id  = queue_row['id']
    study_id    = queue_row['study_id']
    customer_id = queue_row['customer_id']
    url         = queue_row['webhook_url']
    payload     = queue_row['payload_json']
    attempts    = queue_row['attempts']

    secret  = _get_webhook_secret(db, customer_id)
    headers = {
        'Content-Type':     'application/json',
        'X-AZ-Study-ID':    str(study_id),
        'X-AZ-Delivery-ID': str(uuid.uuid4()),
    }
    if secret:
        payload_bytes = payload.encode() if isinstance(payload, str) else payload
        sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        headers['X-AZ-Signature'] = f"sha256={sig}"

    try:
        resp      = requests.post(url, data=payload, headers=headers, timeout=10)
        delivered = resp.status_code < 300

        db.execute("""
            UPDATE az_webhook_queue SET
                attempts         = attempts + 1,
                last_attempt_at  = NOW(),
                last_status_code = %s,
                delivered        = %s,
                delivered_at     = CASE WHEN %s THEN NOW() ELSE NULL END,
                next_attempt_at  = CASE WHEN NOT %s
                    THEN NOW() + INTERVAL '1 hour' * POWER(2, attempts)
                    ELSE next_attempt_at END
            WHERE id = %s""",
            (resp.status_code, delivered, delivered, delivered, webhook_id)
        )
        db.commit()

        if delivered:
            db.execute(
                "UPDATE az_studies SET webhook_delivered=TRUE, webhook_status_code=%s WHERE id=%s",
                (resp.status_code, study_id)
            )
            db.commit()

        return delivered

    except Exception:
        db.execute("""
            UPDATE az_webhook_queue SET
                attempts        = attempts + 1,
                last_attempt_at = NOW(),
                last_status_code = 0,
                next_attempt_at  = NOW() + INTERVAL '1 hour' * POWER(2, attempts)
            WHERE id = %s""",
            (webhook_id,)
        )
        db.commit()
        return False


def _get_webhook_secret(db, customer_id: str) -> Optional[str]:
    row = db.execute(
        "SELECT webhook_secret FROM az_customer_profiles WHERE api_key_id=%s",
        (customer_id,)
    ).fetchone()
    return row[0] if row else None


def process_webhook_queue(db, max_per_run: int = 50) -> dict:
    """Process pending webhooks. Called by scheduled task."""
    rows = db.execute("""
        SELECT id, study_id, customer_id, webhook_url,
               payload_json, attempts, payload_encrypted
        FROM az_webhook_queue
        WHERE NOT delivered
          AND attempts < max_attempts
          AND next_attempt_at <= NOW()
        ORDER BY next_attempt_at
        LIMIT %s""",
        (max_per_run,)
    ).fetchall()

    delivered = 0
    failed    = 0

    for row in rows:
        result = deliver_webhook(db, {
            'id': row[0], 'study_id': row[1], 'customer_id': row[2],
            'webhook_url': row[3], 'payload_json': row[4], 'attempts': row[5]
        })
        if result: delivered += 1
        else:      failed    += 1

    return {'processed': len(rows), 'delivered': delivered, 'failed': failed}
