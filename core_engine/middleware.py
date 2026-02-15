# core_engine/middleware.py
# Unified NTI Middleware Layer
# Frozen core preserved
# Deterministic extensions only

from core_engine.v2_engine import run_v2
from core_engine.v3_engine import run_v3
from core_engine.routing_engine import route
from core_engine.trace import start_trace, end_trace

from core_engine.edge_engine import compute_relational_field
from core_engine.economic_layer import compute_economic_layer
from core_engine.banding import band_cost
from core_engine.invocation_governance import evaluate_invocation
from core_engine.observability_layer import attach_observability


def process_request(text: str) -> dict:
    """
    Unified processing pipeline:

    Human → V2 → AI → V3 → Human
    + Relational Field
    + Economic Layer
    + Banding
    + Invocation Governance
    + Observability

    Deterministic.
    No LLM calls here.
    """

    if text is None:
        text = ""

    trace = start_trace(text)

    # =========================
    # Spine (Frozen Core Flow)
    # =========================

    v2_output = run_v2(text)
    route_decision = route(v2_output)
    v3_output = run_v3(v2_output)

    structural_field = {
        "v2": v2_output,
        "route": route_decision,
        "v3": v3_output
    }

    # =========================
    # Relational Field (Axis 2 equivalent)
    # =========================

    relational_field = compute_relational_field(text)

    # =========================
    # Economic Layer
    # =========================

    economic = compute_economic_layer(text)

    # =========================
    # Band Classification
    # =========================

    estimated_cost = float(economic.get("estimated_roundtrip_cost", 0.0))
    economic["band"] = band_cost(estimated_cost)

    # =========================
    # Invocation Governance
    # =========================

    invocation = evaluate_invocation(
        structural_field=structural_field,
        relational_field=relational_field,
        economic=economic
    )

    # =========================
    # Unified Response Object
    # =========================

    response = {
        "structural_field": structural_field,
        "relational_field": relational_field,
        "economic": economic,
        "invocation_governance": invocation,
    }

    # =========================
    # Observability Attachment
    # =========================

    response = attach_observability(
        trace=trace,
        response=response
    )

    end_trace(trace)

    return response
