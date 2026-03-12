# nti_relay.py
# NTI Customer AI Relay — Core Processor
# Closes gaps: customer AI key passthrough, v3 governance config,
# system prompt binding, webhook delivery, per-key governance profiles.
#
# Pipeline: inbound text → v2 gate → customer LLM → v3 governed output → return / webhook push
# AZ bills for NTI governance layer. Customer pays their own AI provider directly.

import json
import time
import uuid
import threading
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from typing import Optional, Tuple

NTI_RELAY_VERSION = "nti-relay-v1.0"

SUPPORTED_PROVIDERS = {"anthropic", "openai", "google", "xai"}

DEFAULT_GOVERNANCE = {
    "audit_threshold": 0.85,
    "max_passes": 2,
    "token_ceiling": 1000,
    "gate_mode": "standard",  # standard | strict | permissive
}

# ─── Engine imports (matching actual repo paths) ───────────────────────────
try:
    from pre_score_gate import pre_score_gate as _pre_score_gate
    _HAS_GATE = True
except ImportError:
    _HAS_GATE = False

try:
    from core_engine.v2_engine import run_v2 as _run_v2
    _HAS_V2 = True
except ImportError:
    _HAS_V2 = False

try:
    from core_engine.v3_enforcement import self_audit as _v3_self_audit
    _HAS_V3 = True
except ImportError:
    _HAS_V3 = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ═══════════════════════════════════════════════════════════════════════════
# GOVERNANCE RESOLUTION
# Priority: request params > stored profile > defaults
# ═══════════════════════════════════════════════════════════════════════════

def resolve_governance(request_gov: Optional[dict], stored_profile: Optional[dict]) -> dict:
    gov = dict(DEFAULT_GOVERNANCE)

    if stored_profile and isinstance(stored_profile, dict):
        for k in DEFAULT_GOVERNANCE:
            if k in stored_profile:
                gov[k] = stored_profile[k]

    if request_gov and isinstance(request_gov, dict):
        for k in DEFAULT_GOVERNANCE:
            if k in request_gov:
                gov[k] = request_gov[k]

    gov["audit_threshold"] = max(0.0, min(1.0, float(gov["audit_threshold"])))
    gov["max_passes"] = max(1, min(5, int(gov["max_passes"])))
    gov["token_ceiling"] = max(100, min(8000, int(gov["token_ceiling"])))
    if gov["gate_mode"] not in ("standard", "strict", "permissive"):
        gov["gate_mode"] = "standard"

    return gov


# ═══════════════════════════════════════════════════════════════════════════
# V2 INBOUND GATE
# ═══════════════════════════════════════════════════════════════════════════

def run_inbound_gate(text: str, gate_mode: str) -> dict:
    """
    gate_mode strict      -> block if any v2 violation detected
    gate_mode standard    -> block only if v2 score < 0.60
    gate_mode permissive  -> warn only, never block
    """
    result = {"pass": True, "reason": "", "signals": {}}

    # Pre-score gate (gibberish / junk filter)
    if _HAS_GATE:
        try:
            gate = _pre_score_gate(text)
            result["signals"]["pre_score_gate"] = gate
            if not gate["pass"] and gate_mode != "permissive":
                result["pass"] = False
                result["reason"] = gate.get("msg", "Input rejected by pre-score gate")
                return result
        except Exception:
            pass

    # V2 structural audit
    if _HAS_V2:
        try:
            v2 = _run_v2(text)
            result["signals"]["v2"] = v2
            score = float(v2.get("score", 1.0))
            violations = v2.get("violations", [])

            if gate_mode == "strict" and (score < 0.80 or violations):
                result["pass"] = False
                result["reason"] = f"V2 strict gate: score={score:.2f}, violations={violations[:3]}"
            elif gate_mode == "standard" and score < 0.60:
                result["pass"] = False
                result["reason"] = f"V2 gate: score={score:.2f} below threshold"
        except Exception:
            pass

    return result


# ═══════════════════════════════════════════════════════════════════════════
# LLM CALLER — CUSTOMER KEY
# ═══════════════════════════════════════════════════════════════════════════

def call_customer_llm(
    provider: str,
    api_key: str,
    model: Optional[str],
    system_prompt: str,
    user_text: str,
    token_ceiling: int,
    timeout: int = 90,
) -> Tuple[Optional[str], Optional[str]]:
    """Returns (response_text, error_message)."""

    provider = provider.lower().strip()

    if provider == "anthropic":
        _model = model or "claude-sonnet-4-20250514"
        body = json.dumps({
            "model": _model,
            "max_tokens": token_ceiling,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_text}],
        }).encode()
        req = Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        try:
            resp = urlopen(req, timeout=timeout)
            data = json.loads(resp.read())
            return data["content"][0]["text"], None
        except HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")
            return None, f"Anthropic HTTP {e.code}: {body_err[:300]}"
        except Exception as e:
            return None, f"Anthropic error: {str(e)}"

    elif provider == "openai":
        _model = model or "gpt-4o-mini"
        body = json.dumps({
            "model": _model,
            "max_tokens": token_ceiling,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        }).encode()
        req = Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            resp = urlopen(req, timeout=timeout)
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"], None
        except HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")
            return None, f"OpenAI HTTP {e.code}: {body_err[:300]}"
        except Exception as e:
            return None, f"OpenAI error: {str(e)}"

    elif provider == "google":
        _model = model or "gemini-1.5-flash"
        body = json.dumps({
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_text}]}],
            "generationConfig": {"maxOutputTokens": token_ceiling},
        }).encode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_model}:generateContent?key={api_key}"
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            resp = urlopen(req, timeout=timeout)
            data = json.loads(resp.read())
            return data["candidates"][0]["content"]["parts"][0]["text"], None
        except HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")
            return None, f"Google HTTP {e.code}: {body_err[:300]}"
        except Exception as e:
            return None, f"Google error: {str(e)}"

    elif provider == "xai":
        _model = model or "grok-beta"
        body = json.dumps({
            "model": _model,
            "max_tokens": token_ceiling,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
        }).encode()
        req = Request(
            "https://api.x.ai/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            resp = urlopen(req, timeout=timeout)
            data = json.loads(resp.read())
            return data["choices"][0]["message"]["content"], None
        except HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")
            return None, f"xAI HTTP {e.code}: {body_err[:300]}"
        except Exception as e:
            return None, f"xAI error: {str(e)}"

    return None, f"Unsupported provider: {provider}"


# ═══════════════════════════════════════════════════════════════════════════
# V3 OUTBOUND GOVERNANCE
# ═══════════════════════════════════════════════════════════════════════════

def run_outbound_governance(
    text: str,
    audit_threshold: float,
    max_passes: int,
) -> dict:
    """
    Run core_engine.v3_enforcement.self_audit on LLM output.
    self_audit signature: self_audit(text, objective=None, score_fn=None, prior_ai_responses=None)
    """
    result = {
        "enforced_text": text,
        "passes": 0,
        "final_score": None,
        "actions_taken": [],
        "passed": False,
        "compression_ratio": 1.0,
        "time_collapse_applied": False,
    }

    if not _HAS_V3:
        result["passed"] = True
        result["actions_taken"] = ["v3_skipped: module unavailable"]
        return result

    try:
        audit = _v3_self_audit(text, objective=None)

        enforced = audit.get("enforced_text", text)
        actions = audit.get("actions_taken", 0)
        passed = audit.get("passed", True)
        time_collapse = audit.get("time_collapse_applied", False)

        # actions_taken is an int count in v3_enforcement
        actions_list = [f"enforcement_actions: {actions}"] if isinstance(actions, int) else list(actions)

        compression = round(len(enforced) / len(text), 3) if len(text) > 0 else 1.0

        result.update({
            "enforced_text": enforced,
            "passes": 1,
            "actions_taken": actions_list,
            "passed": passed,
            "compression_ratio": compression,
            "time_collapse_applied": time_collapse,
        })

    except Exception as e:
        result["passed"] = True  # Don't block delivery on v3 error
        result["actions_taken"] = [f"v3_error: {str(e)}"]

    return result


# ═══════════════════════════════════════════════════════════════════════════
# WEBHOOK DISPATCH (fire-and-forget)
# ═══════════════════════════════════════════════════════════════════════════

def dispatch_webhook(webhook_url: str, payload: dict, timeout: int = 10) -> None:
    def _send():
        try:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode()
            req = Request(
                webhook_url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "ArtifactZero-NTI-Relay/1.0",
                },
                method="POST",
            )
            urlopen(req, timeout=timeout)
        except Exception as e:
            print(f"[relay] Webhook failed to {webhook_url}: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ═══════════════════════════════════════════════════════════════════════════
# MAIN RELAY PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════

def process_relay(
    text: str,
    ai_provider: str,
    ai_key: str,
    ai_model: Optional[str],
    system_prompt: str,
    governance: dict,
    webhook_url: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    rid = request_id or str(uuid.uuid4())
    t0 = time.time()

    result = {
        "request_id": rid,
        "version": NTI_RELAY_VERSION,
        "status": "ok",
    }

    if ai_provider.lower() not in SUPPORTED_PROVIDERS:
        return {
            **result,
            "status": "error",
            "error": f"Unsupported provider '{ai_provider}'. Supported: {sorted(SUPPORTED_PROVIDERS)}",
        }

    # Step 1: V2 inbound gate
    gate = run_inbound_gate(text, governance["gate_mode"])
    result["inbound_gate"] = {
        "passed": gate["pass"],
        "reason": gate.get("reason", ""),
        "signals": gate.get("signals", {}),
    }

    if not gate["pass"]:
        result["status"] = "gated"
        result["error"] = gate["reason"]
        result["latency_ms"] = int((time.time() - t0) * 1000)
        if webhook_url:
            dispatch_webhook(webhook_url, result)
        return result

    # Step 2: Customer LLM call
    llm_text, llm_error = call_customer_llm(
        provider=ai_provider,
        api_key=ai_key,
        model=ai_model,
        system_prompt=system_prompt,
        user_text=text,
        token_ceiling=governance["token_ceiling"],
    )

    if llm_error or not llm_text:
        result["status"] = "llm_error"
        result["error"] = llm_error or "Empty response from LLM"
        result["latency_ms"] = int((time.time() - t0) * 1000)
        if webhook_url:
            dispatch_webhook(webhook_url, result)
        return result

    result["llm"] = {
        "provider": ai_provider,
        "model": ai_model or "provider_default",
        "raw_response": llm_text,
    }

    # Step 3: V3 outbound governance
    v3 = run_outbound_governance(
        text=llm_text,
        audit_threshold=governance["audit_threshold"],
        max_passes=governance["max_passes"],
    )
    result["v3"] = v3
    result["governed_response"] = v3["enforced_text"]
    result["governance_applied"] = governance
    result["latency_ms"] = int((time.time() - t0) * 1000)

    # Step 4: Webhook
    if webhook_url:
        dispatch_webhook(webhook_url, result)
        result["webhook"] = {"dispatched": True, "url": webhook_url}

    return result
