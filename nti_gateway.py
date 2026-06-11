"""
nti_gateway.py

The Gateway — single API layer for all external integrations.
Every surface (Outlook, Gmail, Salesforce, Slack, Teams, Twilio, Zendesk, etc.)
calls process() with a surface identifier. The gateway handles:
  - Spend cap enforcement
  - NTI scoring
  - Governed rewrite / draft reply (optional)
  - Stamp generation (optional)
  - Full log write
  - Async webhook dispatch

score_batch() handles Snowflake and bulk operations (up to 500 texts).
"""

from __future__ import annotations

import json
import time
import uuid
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ── Spend cap defaults (per account, overridable) ────────────────────────────

DEFAULT_SPEND_CAP_USD = 50.00   # daily cap
_spend_ledger: Dict[str, float] = {}   # account_id -> daily spend USD in memory
                                        # production: read from DB


def _get_daily_spend(account_id: str) -> float:
    return _spend_ledger.get(account_id, 0.0)


def _add_spend(account_id: str, cost_usd: float) -> None:
    _spend_ledger[account_id] = _get_daily_spend(account_id) + cost_usd


def check_spend_cap(account_id: str, estimated_cost: float = 0.01,
                    cap_usd: float = DEFAULT_SPEND_CAP_USD) -> bool:
    """Return True if request is within cap."""
    return (_get_daily_spend(account_id) + estimated_cost) <= cap_usd


# ── Core processor ────────────────────────────────────────────────────────────

def process(
    text: str,
    surface: str = "api",
    account_id: str = "",
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    request_id: Optional[str] = None,
    options: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Main entry point. Called by every integration.

    options keys:
      rewrite (bool)       — run governed rewrite
      draft_reply (bool)   — draft a governed reply (read mode)
      stamp (bool)         — generate stamp if score >= threshold
      metadata (dict)      — passthrough to log
    """
    opts = options or {}
    request_id = request_id or str(uuid.uuid4())
    t0 = time.time()

    if not text or len(text.strip()) < 5:
        return {"ok": False, "error": "text too short", "request_id": request_id}

    # Spend cap check
    if account_id and not check_spend_cap(account_id):
        return {
            "ok": False,
            "error": "spend_cap_exceeded",
            "request_id": request_id,
            "message": "Daily spend cap reached. Resets at midnight UTC."
        }

    # ── NTI Scoring ──────────────────────────────────────────────────────────
    score_result = _run_nti(text)
    nii_score = score_result.get("nii_score", 0)
    signals = score_result.get("signals", [])
    tilt = score_result.get("tilt", [])

    result: Dict[str, Any] = {
        "ok": True,
        "request_id": request_id,
        "surface": surface,
        "score": {
            "nii": nii_score,
            "label": score_result.get("nii_label", ""),
        },
        "signals": signals,
        "tilt": tilt,
        "latency_ms": 0,
    }

    # ── Optional: Governed Rewrite ───────────────────────────────────────────
    if opts.get("rewrite"):
        rewrite_text = _run_rewrite(text, account_id)
        result["rewrite"] = rewrite_text
        _add_spend(account_id, 0.01)

    # ── Optional: Draft Reply ────────────────────────────────────────────────
    if opts.get("draft_reply"):
        draft = _run_rewrite(text, account_id, mode="reply")
        result["draft_reply"] = draft
        _add_spend(account_id, 0.01)

    # ── Optional: Stamp ──────────────────────────────────────────────────────
    if opts.get("stamp") and nii_score >= 80:
        try:
            from nti_stamp import generate_all
            stamp_data = generate_all(request_id=request_id, score=nii_score)
            result["stamp"] = stamp_data
        except Exception as e:
            result["stamp_error"] = str(e)

    # ── Write to The Log ─────────────────────────────────────────────────────
    try:
        from nti_log import log_check, hash_text
        log_check(
            request_id=request_id,
            nti_version="canonical-nti-v3.0",
            score=float(nii_score),
            nii_raw=float(score_result.get("nii_raw", 0)),
            signals=signals,
            char_count=len(text),
            word_count=len(text.split()),
            text_hash=hash_text(text),
            latency_ms=int((time.time() - t0) * 1000),
            surface=surface,
            account_id=account_id,
            user_id=user_id,
            user_email=user_email,
            rewrite_offered=bool(opts.get("rewrite")),
            rewrite_accepted=False,
            metadata=opts.get("metadata"),
        )
    except Exception as e:
        result["log_error"] = str(e)

    # ── Webhook dispatch (async, non-blocking) ───────────────────────────────
    if account_id:
        _dispatch_webhooks_async(account_id, request_id, result.copy())

    result["latency_ms"] = int((time.time() - t0) * 1000)
    return result


# ── Batch scoring ─────────────────────────────────────────────────────────────

def score_batch(
    texts: List[str],
    surface: str = "batch",
    account_id: str = "",
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Score up to 500 texts. Returns ordered results."""
    if len(texts) > 500:
        return {"ok": False, "error": "batch_limit_exceeded", "max": 500}

    t0 = time.time()
    results = []
    for i, text in enumerate(texts):
        r = _run_nti(text)
        results.append({
            "index": i,
            "nii": r.get("nii_score", 0),
            "label": r.get("nii_label", ""),
            "signals": r.get("signals", []),
        })

    return {
        "ok": True,
        "count": len(results),
        "results": results,
        "latency_ms": int((time.time() - t0) * 1000),
    }


# ── Webhook registry (in-memory; production: use DB) ─────────────────────────



def register_webhook(
    account_id,
    user_id,
    url,
    events,
    secret,
):
    """
    Persist a gateway webhook in the canonical PostgreSQL table.
    """
    import hashlib
    import json
    import os
    import uuid

    import db as database

    encryption_key = os.getenv(
        "WEBHOOK_ENCRYPTION_KEY",
        "",
    ).strip()

    if not encryption_key:
        raise RuntimeError(
            "WEBHOOK_ENCRYPTION_KEY not configured"
        )

    try:
        from cryptography.fernet import Fernet

        cipher = Fernet(
            encryption_key.encode()
        )

        secret_encrypted = cipher.encrypt(
            secret.encode()
        ).decode()

    except Exception as error:
        raise RuntimeError(
            "Webhook encryption configuration invalid"
        ) from error

    normalized_events = []

    for event in events:
        event_name = str(event).strip()

        if (
            event_name
            and event_name not in normalized_events
        ):
            normalized_events.append(
                event_name
            )

    if not normalized_events:
        normalized_events = [
            "check.complete"
        ]

    wh_id = (
        "wh_"
        + uuid.uuid4().hex[:16]
    )

    secret_hash = hashlib.sha256(
        secret.encode()
    ).hexdigest()

    conn = database.db_connect()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO webhooks (
                id,
                account_id,
                user_id,
                url,
                secret_hash,
                secret_encrypted,
                events,
                active
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                TRUE
            )
            """,
            (
                wh_id,
                account_id,
                user_id,
                url,
                secret_hash,
                secret_encrypted,
                json.dumps(
                    normalized_events
                ),
            ),
        )

        conn.commit()

        return wh_id

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

def delete_webhook(
    account_id,
    webhook_id,
):
    """
    Deactivate one account-owned webhook.
    """
    import db as database

    conn = database.db_connect()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE webhooks
            SET active = FALSE
            WHERE id = %s
              AND account_id = %s
            """,
            (
                webhook_id,
                account_id,
            ),
        )

        deleted = cur.rowcount > 0

        conn.commit()

        return deleted

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

def _dispatch_webhooks_async(
    account_id,
    request_id,
):
    """
    Load account webhooks from PostgreSQL and deliver each attempt
    in a non-daemon thread. Every attempt is recorded.
    """
    import hashlib
    import hmac
    import json
    import os
    import threading
    import time
    import uuid
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    import db as database

    event_name = "check.complete"

    envelope = {
        "event": event_name,
        "data": request_id,
    }

    payload_json = json.dumps(
        envelope,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    payload_bytes = payload_json.encode(
        "utf-8"
    )

    conn = database.db_connect()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                id,
                url,
                secret_encrypted
            FROM webhooks
            WHERE account_id = %s
              AND active = TRUE
              AND jsonb_exists(events::jsonb, %s)
            ORDER BY created_at
            """,
            (
                account_id,
                event_name,
            ),
        )

        hooks = cur.fetchall()

    finally:
        conn.close()

    def deliver_one(
        webhook_id,
        url,
        secret_encrypted,
    ):
        started = time.perf_counter()
        response_code = None
        success = False

        headers = {
            "Content-Type": "application/json",
            "X-NTI-Event": event_name,
        }

        try:
            if secret_encrypted:
                encryption_key = os.getenv(
                    "WEBHOOK_ENCRYPTION_KEY",
                    "",
                ).strip()

                if not encryption_key:
                    raise RuntimeError(
                        "WEBHOOK_ENCRYPTION_KEY not configured"
                    )

                from cryptography.fernet import Fernet

                cipher = Fernet(
                    encryption_key.encode()
                )

                signing_secret = cipher.decrypt(
                    secret_encrypted.encode()
                )

                signature = hmac.new(
                    signing_secret,
                    payload_bytes,
                    hashlib.sha256,
                ).hexdigest()

                headers["X-NTI-Signature"] = (
                    "sha256="
                    + signature
                )

            request_object = Request(
                url,
                data=payload_bytes,
                headers=headers,
                method="POST",
            )

            with urlopen(
                request_object,
                timeout=10,
            ) as response:
                response_code = int(
                    response.status
                )

                response.read()

            success = (
                200
                <= response_code
                < 300
            )

        except HTTPError as error:
            response_code = int(
                error.code
            )

            success = False

            try:
                error.read()
            except Exception:
                pass

        except Exception as error:
            print(
                f"[nti_gateway] Webhook delivery failed "
                f"for {webhook_id}: {error}",
                flush=True,
            )

            success = False

        latency_ms = int(
            (
                time.perf_counter()
                - started
            )
            * 1000
        )

        delivery_conn = database.db_connect()

        try:
            delivery_cur = delivery_conn.cursor()

            delivery_cur.execute(
                """
                INSERT INTO webhook_deliveries (
                    id,
                    webhook_id,
                    payload_json,
                    response_code,
                    latency_ms,
                    success,
                    retry_count
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    0
                )
                """,
                (
                    "whd_"
                    + uuid.uuid4().hex[:20],
                    webhook_id,
                    payload_json,
                    response_code,
                    latency_ms,
                    success,
                ),
            )

            if success:
                delivery_cur.execute(
                    """
                    UPDATE webhooks
                    SET
                        last_triggered_at = NOW(),
                        failure_count = 0
                    WHERE id = %s
                    """,
                    (webhook_id,),
                )

            else:
                delivery_cur.execute(
                    """
                    UPDATE webhooks
                    SET
                        last_triggered_at = NOW(),
                        failure_count = failure_count + 1
                    WHERE id = %s
                    """,
                    (webhook_id,),
                )

            delivery_conn.commit()

        except Exception:
            delivery_conn.rollback()
            raise

        finally:
            delivery_conn.close()

    threads = []

    for row in hooks:
        webhook_id = row[0]
        url = row[1]
        secret_encrypted = row[2]

        thread = threading.Thread(
            target=deliver_one,
            args=(
                webhook_id,
                url,
                secret_encrypted,
            ),
            daemon=False,
            name=(
                "nti-webhook-"
                + str(webhook_id)
            ),
        )

        thread.start()
        threads.append(thread)

    return len(threads)

# ── Internal NTI scorer (imports from app context) ───────────────────────────

def _run_nti(text: str) -> Dict:
    """Run NTI scoring. Returns dict with nii_score, nii_label, signals, tilt."""
    try:
        from core_engine.app import (
            classify_tilt,
            compute_nii,
            detect_downstream_before_constraint,
            detect_l0_constraints,
        )

        l0 = detect_l0_constraints(text)
        tilt = classify_tilt(text)
        dbc = detect_downstream_before_constraint("", text, l0)
        nii = compute_nii("", text, l0, dbc, tilt)
        return {
            "nii_score": nii.get("nii_score", 0),
            "nii_raw": nii.get("nii_raw", 0),
            "nii_label": nii.get("nii_label", ""),
            "signals": tilt,
            "tilt": tilt,
        }
    except Exception:
        pass

    # Fallback: call /nti internally won't work from subprocess.
    # Return minimal result so gateway doesn't crash.
    return {"nii_score": 0, "nii_raw": 0, "nii_label": "UNAVAILABLE", "signals": [], "tilt": []}


def _run_rewrite(text: str, account_id: str, mode: str = "rewrite") -> str:
    """Call rewrite endpoint internally. Returns rewritten text or empty string."""
    try:
        import sys
        app_mod = sys.modules.get("__main__") or sys.modules.get("app")
        if app_mod and hasattr(app_mod, "_call_llm") and hasattr(app_mod, "_letter_race"):
            model = app_mod._letter_race(text)
            system = (
                "You are a structural communication assistant. "
                "Rewrite the following to remove hedges, clarify commitments, and tighten structure."
                if mode == "rewrite" else
                "You are a structural communication assistant. "
                "Draft a concise, governed reply to the following email."
            )
            result, _ = app_mod._call_llm(model, text, system)
            return result or ""
    except Exception:
        pass
    return ""
