"""
azl_storage.py
==============
RDS storage layer for AZL certificate persistence.
Matches db.py connection pattern exactly -- %s placeholders, explicit commit/close.
"""

import json
import uuid
import db as database


def store_certificate(api_key_id: str, cert_type: str, cert_dict: dict) -> str:
    """
    Persist an AZL certificate to az_azl_certificates.
    Returns the new certificate UUID.
    """
    cert_id = str(uuid.uuid4())
    conn = database.db_connect()
    try:
        cur = conn.cursor()
        cur.execute("""
                INSERT INTO az_azl_certificates
                    (id, api_key_id, cert_type, subject, result, details, signature, time_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
            cert_id,
            api_key_id,
            cert_type,
            cert_dict.get("subject", ""),
            cert_dict.get("result", ""),
            json.dumps(cert_dict.get("details", {})),
            cert_dict.get("signature", ""),
            cert_dict.get("time_ms", 0),
        ))
        conn.commit()
    finally:
        conn.close()
    return cert_id


def get_certificates(api_key_id: str, limit: int = 20, cert_type: str = None) -> list:
    """
    Retrieve certificate history for an API key.
    Optionally filter by cert_type.
    """
    conn = database.db_connect()
    try:
        cur = conn.cursor()
        if cert_type:
            cur.execute("""
                    SELECT id, created_at, cert_type, subject, result,
                           details, signature, time_ms
                    FROM az_azl_certificates
                    WHERE api_key_id = %s AND cert_type = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (api_key_id, cert_type, limit))
        else:
            cur.execute("""
                    SELECT id, created_at, cert_type, subject, result,
                           details, signature, time_ms
                    FROM az_azl_certificates
                    WHERE api_key_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (api_key_id, limit))
        rows = cur.fetchall()
        result = []
        for r in rows:
            details = r[5]
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    details = {}
            result.append({
                "id":         r[0],
                "created_at": str(r[1]),
                "cert_type":  r[2],
                "subject":    r[3],
                "result":     r[4],
                "details":    details,
                "signature":  r[6],
                "time_ms":    float(r[7]) if r[7] else 0,
            })
        return result
    finally:
        conn.close()
