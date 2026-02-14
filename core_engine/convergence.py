from typing import Any, Callable, Dict, Optional
from .v2_engine import run_v2, v2_feedback_message
from .v3_engine import run_v3
from .feature_flags import get_flags
from .trace import TraceLogger, approx_tokens, new_trace_context

DEFAULT_THRESHOLD = 0.80
DEFAULT_MAX_ITER = 3
DEFAULT_MAX_TOKENS_V3 = 400

def run_core_pipeline(
    user_text: str,
    ai_callable: Callable[[str], str],
    threshold: float = DEFAULT_THRESHOLD,
    max_iter: int = DEFAULT_MAX_ITER,
    v3_max_tokens: int = DEFAULT_MAX_TOKENS_V3,
    trace_logger: Optional[TraceLogger] = None,
    objective: Optional[str] = None
) -> Dict[str, Any]:
    """
    Human → V2 → AI → V3 → Human (with optional iterative convergence)
    Shadow mode supported via flags: returns raw output but logs stabilized output.
    """
    flags = get_flags()
    ctx = new_trace_context()

    if trace_logger is None:
        trace_logger = TraceLogger()

    trace: Dict[str, Any] = {
        **ctx,
        "flags": flags,
        "status": "INIT",
        "input_text": user_text,
        "token_estimate_input": approx_tokens(user_text or ""),
        "iterations": 0,
        "errors": []
    }

    try:
        if not flags["CORE_ENABLED"]:
            trace["status"] = "CORE_DISABLED"
            trace_logger.write(trace)
            return {"status": "CORE_DISABLED", "output": ai_callable(user_text)}

        # First V2 audit
        v2 = run_v2(user_text, threshold=threshold)
        trace["v2_initial"] = v2

        # Routing decision (optional)
        if flags["ROUTING_ENABLED"] and v2["route"] == "HUMAN_INTERNAL":
            trace["status"] = "ROUTED_INTERNAL"
            trace["ai_invoked"] = False
            trace["v2_feedback"] = v2_feedback_message(v2)
            trace["token_estimate_saved"] = 0  # unknown without AI call
            trace_logger.write(trace)
            return {
                "status": "ROUTED_INTERNAL",
                "score": v2["score"],
                "output": v2_feedback_message(v2),
                "trace_id": trace["request_id"]
            }

        # If score below threshold, return V2 feedback instead of calling AI (Core Plus may override later)
        if v2["score"] < threshold and not flags["CORE_PLUS_ENABLED"]:
            trace["status"] = "V2_BELOW_THRESHOLD"
            trace["ai_invoked"] = False
            trace["v2_feedback"] = v2_feedback_message(v2)
            trace_logger.write(trace)
            return {
                "status": "V2_BELOW_THRESHOLD",
                "score": v2["score"],
                "output": v2_feedback_message(v2),
                "trace_id": trace["request_id"]
            }

        # Convergence loop
        current_text = v2["normalized_text"]
        final_output = ""
        final_score = v2["score"]
        ai_raw_output = ""
        v3_result = None

        for i in range(max_iter):
            trace["iterations"] = i + 1

            # Call AI
            ai_raw_output = ai_callable(current_text)
            trace.setdefault("ai_calls", []).append({
                "iteration": i + 1,
                "input_text": current_text,
                "token_estimate_input": approx_tokens(current_text),
                "raw_output_len": len(ai_raw_output or ""),
                "token_estimate_raw_output": approx_tokens(ai_raw_output or "")
            })

            # V3 stabilize (optional)
            if flags["V3_ENABLED"]:
                v3_result = run_v3(ai_raw_output, max_tokens=v3_max_tokens, objective=objective)
                final_output = v3_result["stabilized_text"]
            else:
                final_output = ai_raw_output

            # Re-audit stabilized output using V2 (structural check of output)
            v2_out = run_v2(final_output, threshold=threshold)
            trace.setdefault("v2_output_audits", []).append({
                "iteration": i + 1,
                "v2": v2_out
            })
            final_score = v2_out["score"]

            if final_score >= threshold:
                break

            # Next iteration uses stabilized output
            current_text = final_output

        trace["status"] = "COMPLETE"
        trace["ai_invoked"] = True
        trace["final_score"] = final_score
        trace["final_output_len"] = len(final_output or "")
        trace["token_estimate_output"] = approx_tokens(final_output or "")
        if v3_result:
            trace["v3"] = v3_result

        # token saved estimate (vs raw AI final output)
        trace["token_estimate_saved"] = max(
            0,
            approx_tokens(ai_raw_output or "") - approx_tokens(final_output or "")
        )

        trace_logger.write(trace)

        # Shadow mode: return raw AI output but keep trace
        if flags["SHADOW_MODE_ENABLED"]:
            return {
                "status": "SHADOW_COMPLETE",
                "score": final_score,
                "output": ai_raw_output,          # user sees current behavior
                "shadow_output": final_output,    # available for review
                "trace_id": trace["request_id"]
            }

        return {
            "status": "COMPLETE",
            "score": final_score,
            "output": final_output,
            "trace_id": trace["request_id"]
        }

    except Exception as e:
        trace["status"] = "ERROR"
        trace["errors"].append(str(e))
        trace_logger.write(trace)

        # Fail-safe: never break production
        try:
            raw = ai_callable(user_text)
        except Exception:
            raw = "ERROR: AI invocation failed."
        return {
            "status": "ERROR_FALLBACK",
            "output": raw,
            "trace_id": trace.get("request_id")
        }
