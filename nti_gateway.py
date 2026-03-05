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

_webhook_registry: Dict[str, List[Dict]] = {}   # account_id -> [webhook_config]


def register_webhook(account_id: str, url: str, events: List[str],
                     secret: str = "") -> str:
    wid = str(uuid.uuid4())
    if account_id not in _webhook_registry:
        _webhook_registry[account_id] = []
    _webhook_registry[account_id].append({
        "id": wid, "url": url, "events": events, "secret": secret, "active": True
    })
    return wid


def delete_webhook(account_id: str, webhook_id: str) -> bool:
    hooks = _webhook_registry.get(account_id, [])
    before = len(hooks)
    _webhook_registry[account_id] = [h for h in hooks if h["id"] != webhook_id]
    return len(_webhook_registry[account_id]) < before


def _dispatch_webhooks_async(account_id: str, request_id: str,
                             payload: Dict) -> None:
    hooks = _webhook_registry.get(account_id, [])
    if not hooks:
        return

    def _send(hook: Dict, body: bytes) -> None:
        try:
            import urllib.request
            req = urllib.request.Request(
                hook["url"],
                data=body,
                headers={"Content-Type": "application/json",
                         "X-NTI-Event": "check.complete",
                         "X-NTI-RequestId": request_id}
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # fire and forget

    body = json.dumps({"event": "check.complete",
                        "request_id": request_id,
                        "data": payload}).encode()
    for hook in hooks:
        if hook.get("active") and "check.complete" in hook.get("events", []):
            threading.Thread(target=_send, args=(hook, body), daemon=True).start()


# ── Internal NTI scorer (imports from app context) ───────────────────────────

def _run_nti(text: str) -> Dict:
    """Run NTI scoring. Returns dict with nii_score, nii_label, signals, tilt."""
    try:
        # Import from app.py's globals — same process, no HTTP round-trip
        import importlib, sys
        app_mod = sys.modules.get("__main__") or sys.modules.get("app")

        if app_mod and hasattr(app_mod, "compute_nii"):
            detect_l0 = app_mod.detect_l0_constraints
            classify_t = app_mod.classify_tilt
            dbc_fn = app_mod.detect_downstream_before_constraint
            compute = app_mod.compute_nii

            l0 = detect_l0(text)
            tilt = classify_t(text)
            dbc = dbc_fn("", text, l0)
            nii = compute("", text, l0, dbc, tilt)
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
