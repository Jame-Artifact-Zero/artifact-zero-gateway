# core_engine/middleware.py
# Unified NTI Middleware Layer — v2.1 (wiring bugs fixed)
# Frozen core preserved. Deterministic extensions only.

from core_engine.v2_engine import run_v2
from core_engine.v3_engine import run_v3
from core_engine.routing_engine import route_decision, DEFAULT_ROUTING_KEYWORDS
from core_engine.trace import TraceLogger, new_trace_context
from core_engine.edge_engine import compute_relational_field
from core_engine.interrogative_engine import compute_interrogative_field
from core_engine.economic_layer import compute_economic_layer
from core_engine.banding import band_cost
from core_engine.invocation_governance import compute_invocation_governance
from core_engine.observability_layer import compute_observability


def process_request(text: str) -> dict:
    """
    Unified processing pipeline:

    Human -> V2 -> route -> V3 -> Human
    + Relational Field
    + Interrogative Field
    + Economic Layer
    + Banding
    + Invocation Governance
    + Observability

    Deterministic. No LLM calls here.
    """

    if text is None:
        text = ""

    trace_ctx = new_trace_context()
    logger = TraceLogger()

    # =========================
    # Spine (Frozen Core Flow)
    # =========================

    v2_output = run_v2(text)

    route, route_matches = route_decision(
        text.lower(),
        DEFAULT_ROUTING_KEYWORDS
    )

    v3_output = run_v3(v2_output["normalized_text"])

    structural_field = {
        "v2": v2_output,
        "route": route,
        "route_matches": route_matches,
        "v3": v3_output,
    }

    # =========================
    # Relational Field
    # =========================

    relational_field = compute_relational_field(text)
    edge_index = float(relational_field.get("edge_index", 0.0))

    # =========================
    # Interrogative Field
    # =========================

    interrogative_field = compute_interrogative_field(text)

    # =========================
    # Economic Layer
    # =========================

    economic = compute_economic_layer(
        input_text=text,
        output_text=v3_output.get("stabilized_text", ""),
        route_hint=route,
        ai_invoked=(route == "AI_PATH"),
    )

    # =========================
    # Band Classification
    # =========================

    estimated_cost = float(economic.get("estimated_roundtrip_cost", 0.0))
    economic["band"] = band_cost(estimated_cost)

    # =========================
    # Invocation Governance
    # =========================

    structural_score = float(v2_output.get("score", 0.0))

    invocation = compute_invocation_governance(
        structural_score=structural_score,
        edge_index=edge_index,
    )

    # =========================
    # Observability
    # =========================

    obs = compute_observability(
        structural_score=structural_score,
        edge_index=edge_index,
    )

    # =========================
    # Unified Response Object
    # =========================

    response = {
        "structural_field": structural_field,
        "relational_field": relational_field,
        "interrogative_field": interrogative_field,
        "economic": economic,
        "invocation_governance": invocation,
        "observability": obs,
        "trace": trace_ctx,
    }

    # =========================
    # Write Trace
    # =========================

    logger.write({**trace_ctx, "summary": {
        "route": route,
        "v2_score": v2_output.get("score"),
        "edge_index": edge_index,
        "route_hint": invocation.get("route_hint"),
        "obs_composite": obs.get("composite_score"),
    }})

    return response
