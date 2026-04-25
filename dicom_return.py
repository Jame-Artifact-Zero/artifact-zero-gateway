"""
================================================================================
ARTIFACT ZERO LABS — Return Path Handler
Merge into: services/ or pipeline/ directory

Handles all return formats:
  json        — standard HTTP JSON response (synchronous)
  fhir        — HL7 FHIR Observation resource
  dicom_sr    — DICOM Structured Report (write back to PACS)
  webhook     — async POST to customer endpoint

Also handles:
  - Payload encryption before return
  - Webhook queue for async delivery
  - Retry logic for failed webhooks
================================================================================
"""

import json
import hmac
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests


# ════════════════════════════════════════════════════════════════════
# MAIN RETURN DISPATCHER
# ════════════════════════════════════════════════════════════════════

def build_response(result: dict, params: dict, customer: dict,
                   study_id: str, db=None) -> dict:
    """
    Build the final response payload based on requested return format.
    Applies encryption if requested.
    Queues webhook if requested.

    Returns the dict that Flask will jsonify and send back to the caller.
    """
    return_format = params.get('return_format', 'json')
    encrypt_key   = params.get('encrypt_key') or customer.get('encrypt_public_key')
    webhook_url   = params.get('webhook_url') or customer.get('webhook_url')

    # Build the base payload
    if return_format == 'fhir':
        payload = _build_fhir_observation(result, study_id, customer)
    elif return_format == 'dicom_sr':
        payload = _build_dicom_sr_reference(result, study_id)
    else:
        payload = _build_json_response(result, study_id, customer)

    # Encrypt if requested
    if encrypt_key:
        from dicom_encryption import encrypt_payload, validate_public_key
        if validate_public_key(encrypt_key):
            payload = encrypt_payload(payload, encrypt_key)
        else:
            payload['encryption_warning'] = 'Invalid public key — payload not encrypted'

    # Queue webhook if requested (non-blocking)
    if webhook_url and db is not None:
        _queue_webhook(db, study_id, customer['api_key_id'], webhook_url,
                       payload, bool(encrypt_key))

    return payload


# ════════════════════════════════════════════════════════════════════
# JSON RESPONSE (default)
# ════════════════════════════════════════════════════════════════════

def _build_json_response(result: dict, study_id: str, customer: dict) -> dict:
    """Standard JSON response — the default return format."""
    impression = result.get('impression', {})
    flags      = impression.get('flags', [])
    longitudinal = result.get('longitudinal')

    response = {
        'study_id':    study_id,
        'status':      impression.get('status', 'PENDING'),
        'called_at':   datetime.now(timezone.utc).isoformat(),

        # Study metadata
        'study': {
            'date':        result.get('study_date'),
            'description': result.get('study_description'),
            'body_part':   result.get('body_part'),
            'modality':    result.get('modality'),
        },

        # Scanner
        'scanner': {
            'manufacturer':   result.get('manufacturer'),
            'model':          result.get('model_name'),
            'field_strength': result.get('field_strength'),
            'institution':    result.get('institution_name'),
        },

        # Impression — includes longitudinal flags if analysis=longitudinal
        'impression': {
            'status': impression.get('status', 'PENDING'),
            'flags':  flags,
            'text':   impression.get('text', ''),
            'counts': {
                'critical': sum(1 for f in flags if f.get('severity') == 'CRITICAL'),
                'moderate': sum(1 for f in flags if f.get('severity') == 'MODERATE'),
                'finding':  sum(1 for f in flags if f.get('severity') == 'FINDING'),
                'normal':   sum(1 for f in flags if f.get('severity') == 'NORMAL'),
            }
        },

        # Sequences
        'sequences': result.get('sequences', []),

        # Performance
        'performance': {
            'sequences_found':     result.get('sequences_found', 0),
            'sequences_processed': result.get('sequences_processed', 0),
            'pipeline_ms':         result.get('pipeline_ms'),
        }
    }

    # Prior state (S₀) — only present when analysis=longitudinal
    # This is the clinical value: measurement-level comparison against
    # the prior study, not just a status label comparison.
    # Structure mirrors O = f(Q, S₀) — current study is Q, prior is S₀.
    if longitudinal:
        response['prior_state'] = {
            'study_id':         longitudinal.get('prior_study_id'),
            'study_date':       longitudinal.get('prior_study_date'),
            'status':           longitudinal.get('prior_status'),
            'change':           longitudinal.get('change'),
            # Per-sequence measurement deltas (the actual clinical findings)
            'measurement_diffs': longitudinal.get('measurement_diffs', []),
            # Flags generated from S₀ comparison (appended to impression above)
            'longitudinal_flags': longitudinal.get('longitudinal_flags', []),
            'longitudinal_flag_count': longitudinal.get('longitudinal_flag_count', 0),
        }

    return response


# ════════════════════════════════════════════════════════════════════
# FHIR OBSERVATION
# ════════════════════════════════════════════════════════════════════

def _build_fhir_observation(result: dict, study_id: str, customer: dict) -> dict:
    """
    Build an HL7 FHIR R4 Observation resource.
    Compatible with Epic, Cerner, Oracle Health FHIR APIs.
    """
    impression = result.get('impression', {})
    flags      = impression.get('flags', [])
    status_map = {
        'CRITICAL': 'abnormal',
        'MODERATE': 'abnormal',
        'FINDING':  'borderline',
        'NORMAL':   'normal',
        'CLEAN':    'normal',
    }

    # LOINC code for imaging study observation
    # 18748-4 = Diagnostic imaging study
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
            'text': f"AZ Signal Analysis — {result.get('body_part', 'Unknown')}"
        },
        'effectiveDateTime': result.get('study_date'),
        'issued': datetime.now(timezone.utc).isoformat(),
        'interpretation': [{
            'coding': [{
                'system': 'http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation',
                'code':   status_map.get(impression.get('status', 'NORMAL'), 'N'),
            }]
        }],
        'note': [{
            'text': impression.get('text', '')
        }],
        'component': [
            {
                'code': {'text': f['label']},
                'valueString': f['detail'],
                'interpretation': [{'coding': [{'code': f['severity']}]}]
            }
            for f in flags
        ],
        'extension': [{
            'url': 'https://artifact0.com/fhir/extensions/az-study-id',
            'valueString': study_id
        }]
    }


# ════════════════════════════════════════════════════════════════════
# DICOM SR REFERENCE
# Full DICOM SR write-back requires pydicom and PACS credentials.
# This returns the SR metadata — actual write-back is a separate service.
# ════════════════════════════════════════════════════════════════════

def _build_dicom_sr_reference(result: dict, study_id: str) -> dict:
    """
    Returns a reference dict describing the DICOM SR to be created.
    The DICOM C-STORE service picks this up and writes it to the PACS.
    """
    impression = result.get('impression', {})
    return {
        'dicom_sr': True,
        'study_id': study_id,
        'study_instance_uid': result.get('study_instance_uid'),
        'sr_document_title': f"AZ Signal Analysis — {result.get('body_part', 'Unknown')}",
        'completion_flag': 'COMPLETE',
        'verification_flag': 'UNVERIFIED',
        'content_sequence': [
            {
                'relationship_type': 'CONTAINS',
                'value_type': 'TEXT',
                'concept_name': {'code': f['label']},
                'text_value': f"{f['severity']}: {f['detail']}"
            }
            for f in impression.get('flags', [])
        ],
        'impression_text': impression.get('text', ''),
        'impression_status': impression.get('status', 'PENDING'),
    }


# ════════════════════════════════════════════════════════════════════
# WEBHOOK
# ════════════════════════════════════════════════════════════════════

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
    Called by the webhook worker task.
    """
    webhook_id  = queue_row['id']
    study_id    = queue_row['study_id']
    customer_id = queue_row['customer_id']
    url         = queue_row['webhook_url']
    payload     = queue_row['payload_json']
    attempts    = queue_row['attempts']

    # Sign the payload with webhook secret if available
    secret = _get_webhook_secret(db, customer_id)
    headers = {
        'Content-Type':     'application/json',
        'X-AZ-Study-ID':    str(study_id),
        'X-AZ-Delivery-ID': str(uuid.uuid4()),
    }
    if secret:
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers['X-AZ-Signature'] = f"sha256={sig}"

    try:
        resp = requests.post(url, data=payload, headers=headers, timeout=10)
        delivered = resp.status_code < 300

        db.execute("""
            UPDATE az_webhook_queue SET
                attempts        = attempts + 1,
                last_attempt_at = NOW(),
                last_status_code = %s,
                delivered       = %s,
                delivered_at    = CASE WHEN %s THEN NOW() ELSE NULL END,
                next_attempt_at = CASE WHEN NOT %s
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

    except Exception as e:
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
        "SELECT webhook_secret FROM az_customer_profiles WHERE api_key_id=%s", (customer_id,)
    ).fetchone()
    return row[0] if row else None


def process_webhook_queue(db, max_per_run: int = 50) -> dict:
    """
    Process pending webhooks. Called by scheduled task or Celery beat.
    Returns summary of what was processed.
    """
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
