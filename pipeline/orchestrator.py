"""Run the shared Artifact Zero processing pipeline from event trigger to state update."""

from pipeline.stages import trigger
from pipeline.stages import exist
from pipeline.stages import memory_read
from pipeline.stages import detect
from pipeline.stages import decision
from pipeline.stages import phi_run
from pipeline.stages import psi_run
from products.score import handler as score_handler
from products.email import handler as email_handler


_HANDLER_MAP = {
    "products.score.handler": score_handler.handle,
    "products.email.handler": email_handler.handle,
}


def run_pipeline(event: dict) -> dict:
    """Run the pipeline stages in order and return the final S1 result."""
    S0 = trigger.run(event)
    S0 = exist.run(S0)
    S0 = memory_read.run(S0)
    S0 = detect.run(S0)
    S0 = decision.run(S0)
    handler = S0.get("decision", {}).get("handler")
    if handler in _HANDLER_MAP:
        S0["handler_result"] = _HANDLER_MAP[handler](S0)
    R = phi_run.run(S0)
    return psi_run.run(S0, R)
