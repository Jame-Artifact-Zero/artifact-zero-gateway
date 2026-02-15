# economic_layer.py
# Axis 5 - Economic Stabilization (economic-v1.0)
# Deterministic token+cost estimates. No vendor coupling.

from __future__ import annotations

from typing import Any, Dict, Union

ECONOMIC_VERSION = "economic-v1.0"

# Deterministic token estimate: average ~4 chars per token
def approx_tokens(text: str) -> int:
    t = text or ""
    return max(0, int(round(len(t) / 4.0)))

# Deterministic placeholder cost multiplier (not "price"; purely internal estimate unit)
DEFAULT_UNIT_COST = 0.00001


def compute_economic_layer(
    input_text: str,
    output_text: str,
    route_hint: str,
    ai_invoked: Union[bool, str],
    unit_cost: float = DEFAULT_UNIT_COST
) -> Dict[str, Any]:
    in_tokens = approx_tokens(input_text or "")
    out_tokens = approx_tokens(output_text or "")

    est_cost = round((in_tokens + out_tokens) * float(unit_cost), 6)

    # invocation avoided state
    if route_hint == "HUMAN_REQUIRED":
        avoided = True
    elif route_hint == "AI_OPTIMAL":
        avoided = False
    else:
        avoided = "conditional"

    return {
        "version": ECONOMIC_VERSION,
        "estimated_input_tokens": in_tokens,
        "estimated_output_tokens": out_tokens,
        "estimated_roundtrip_cost": est_cost,
        "ai_invoked": ai_invoked,
        "invocation_avoided": avoided,
    }
