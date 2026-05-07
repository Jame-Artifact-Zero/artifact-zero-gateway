"""
================================================================================
ARTIFACT ZERO LABS — DICOM C-STORE Listener
Deploy as: separate ECS service in the same cluster

This service listens on port 11112 for incoming DICOM C-STORE requests
from hospital PACS systems. When a study arrives, it POSTs the DICOM
to the Flask API endpoint internally and optionally writes the SR back.

Hospital PACS configuration:
  Add a secondary DICOM destination:
  AE Title:  ARTIFACTZERO
  Host:      [your ECS service IP or internal DNS]
  Port:      11112

ECS Task Definition:
  Image:   same Docker image as the Flask app
  Command: python dicom_cstore_listener.py
  Memory:  2048 MB
  CPU:     512
  Port:    11112 (TCP, open in security group)

Environment variables:
  API_ENDPOINT      — internal Flask API URL (e.g. http://internal-alb/dicom/analyze)
  INTERNAL_API_KEY  — API key for internal service-to-service calls
  AE_TITLE          — our DICOM AE title (default: ARTIFACTZERO)
  LISTEN_PORT       — port to listen on (default: 11112)
  MAX_PDU_SIZE      — max PDU size in bytes (default: 16384)
================================================================================
"""

import os
import io
import sys
import logging
import tempfile
import requests
from pathlib import Path

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [CSTORE] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger('az_cstore')

# ── Config from environment ───────────────────────────────────────────
API_ENDPOINT     = os.environ.get('API_ENDPOINT', 'http://localhost:5000/dicom/analyze')
INTERNAL_API_KEY = os.environ.get('INTERNAL_API_KEY', '')
AE_TITLE         = os.environ.get('AE_TITLE', 'ARTIFACTZERO')
LISTEN_PORT      = int(os.environ.get('LISTEN_PORT', '11112'))
LISTEN_HOST      = os.environ.get('LISTEN_HOST', '0.0.0.0')  # bind all interfaces — override via env
MAX_PDU_SIZE     = int(os.environ.get('MAX_PDU_SIZE', '16384'))

# Default analysis params for PACS-sourced studies
# Hospital can override by sending custom DICOM private tags
DEFAULT_PARAMS = {
    'storage':        os.environ.get('DEFAULT_STORAGE',   'local'),
    'analysis':       os.environ.get('DEFAULT_ANALYSIS',  'impression'),
    'return_format':  os.environ.get('DEFAULT_FORMAT',    'json'),
}

try:
    from pynetdicom import AE, evt, build_role
    from pynetdicom.sop_class import (
        MRImageStorage,
        CTImageStorage,
        DigitalXRayImageStorageForPresentation,
        UltrasoundImageStorage,
        UltrasoundMultiframeImageStorage,
        SecondaryCaptureImageStorage,
        EnhancedMRImageStorage,
        EnhancedCTImageStorage,
    )
    import pydicom
    PYNETDICOM_OK = True
except ImportError:
    logger.error("pynetdicom not installed. Run: pip install pynetdicom pydicom")
    PYNETDICOM_OK = False


# ════════════════════════════════════════════════════════════════════
# SUPPORTED SOP CLASSES
# ════════════════════════════════════════════════════════════════════

SUPPORTED_SOP_CLASSES = [
    MRImageStorage,
    CTImageStorage,
    DigitalXRayImageStorageForPresentation,
    UltrasoundImageStorage,
    UltrasoundMultiframeImageStorage,
    SecondaryCaptureImageStorage,
    EnhancedMRImageStorage,
    EnhancedCTImageStorage,
]


# ════════════════════════════════════════════════════════════════════
# C-STORE HANDLER
# ════════════════════════════════════════════════════════════════════

def handle_store(event):
    """
    Handle an incoming C-STORE request.
    Called once per DICOM instance (image).
    Groups instances by StudyInstanceUID before sending to API.
    """
    try:
        ds = event.dataset
        ds.file_meta = event.file_meta

        study_uid  = str(getattr(ds, 'StudyInstanceUID', 'unknown'))
        series_uid = str(getattr(ds, 'SeriesInstanceUID', 'unknown'))
        modality   = str(getattr(ds, 'Modality', 'UNK'))

        logger.info(f"Received: study={study_uid[:20]}... series={series_uid[:20]}... modality={modality}")

        # Save to temp file
        with tempfile.NamedTemporaryFile(suffix='.dcm', delete=False) as f:
            tmp_path = f.name
            ds.save_as(tmp_path, write_like_original=False)

        # Send to API
        _send_to_api(tmp_path, study_uid, modality)

        # Clean up
        Path(tmp_path).unlink(missing_ok=True)

        # Return Success status
        return 0x0000

    except Exception as e:
        logger.error(f"C-STORE handler error: {e}")
        return 0xA700  # Refused: Out of Resources


def _send_to_api(dcm_path: str, study_uid: str, modality: str):
    """POST the DICOM file to the Flask analysis endpoint."""
    try:
        with open(dcm_path, 'rb') as f:
            raw_bytes = f.read()

        import json
        params_json = json.dumps({
            **DEFAULT_PARAMS,
            'customer_study_ref': study_uid,
        })

        resp = requests.post(
            API_ENDPOINT,
            files={
                'dicom':  ('study.dcm', raw_bytes, 'application/dicom'),
                'params': ('params.json', params_json, 'application/json'),
            },
            headers={
                'X-API-Key':    INTERNAL_API_KEY,
                'X-Source':     'cstore-listener',
                'X-Study-UID':  study_uid,
            },
            timeout=60
        )

        if resp.status_code == 200:
            result = resp.json()
            status = result.get('status', 'UNKNOWN')
            flags  = result.get('impression', {}).get('counts', {})
            logger.info(
                f"Analysis complete: study={study_uid[:20]}... "
                f"status={status} "
                f"critical={flags.get('critical',0)} "
                f"moderate={flags.get('moderate',0)}"
            )

            # Log CRITICAL findings prominently
            if status == 'CRITICAL':
                logger.warning(
                    f"CRITICAL FLAG: study={study_uid} "
                    f"— {flags.get('critical',0)} critical finding(s)"
                )
        else:
            logger.error(
                f"API error {resp.status_code} for study {study_uid}: {resp.text[:200]}")

    except requests.exceptions.Timeout:
        logger.error(f"API timeout for study {study_uid}")
    except Exception as e:
        logger.error(f"API call failed for study {study_uid}: {e}")


# ════════════════════════════════════════════════════════════════════
# C-ECHO HANDLER (required — lets PACS verify connectivity)
# ════════════════════════════════════════════════════════════════════

def handle_echo(event):
    """Respond to C-ECHO (ping) from PACS. Always return success."""
    logger.info(f"C-ECHO from {event.assoc.requestor.address}")
    return 0x0000


# ════════════════════════════════════════════════════════════════════
# SERVICE ENTRY POINT
# ════════════════════════════════════════════════════════════════════

def start_listener():
    """Start the DICOM C-STORE listener service."""
    if not PYNETDICOM_OK:
        logger.error("Cannot start — pynetdicom not available")
        sys.exit(1)

    logger.info(f"Starting DICOM C-STORE listener")
    logger.info(f"  AE Title:    {AE_TITLE}")
    logger.info(f"  Host:        {LISTEN_HOST}")
    logger.info(f"  Port:        {LISTEN_PORT}")
    logger.info(f"  API endpoint: {API_ENDPOINT}")
    logger.info(f"  Storage mode: {DEFAULT_PARAMS['storage']}")
    logger.info(f"  Analysis:     {DEFAULT_PARAMS['analysis']}")

    ae = AE(ae_title=AE_TITLE)
    ae.maximum_pdu_size = MAX_PDU_SIZE

    # Add supported SOP classes
    for sop_class in SUPPORTED_SOP_CLASSES:
        ae.add_supported_context(sop_class)

    # Event handlers
    handlers = [
        (evt.EVT_C_STORE, handle_store),
        (evt.EVT_C_ECHO,  handle_echo),
    ]

    # Start server — blocking
    logger.info(f"Listening on port {LISTEN_PORT}...")
    ae.start_server(
        (LISTEN_HOST, LISTEN_PORT),
        evt_handlers=handlers,
        block=True
    )


if __name__ == '__main__':
    start_listener()
