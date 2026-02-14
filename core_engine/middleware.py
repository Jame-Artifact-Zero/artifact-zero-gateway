from typing import Callable, Dict, Any, Optional
from .convergence import run_core_pipeline

def process_request(
    user_text: str,
    ai_callable: Optional[Callable[[str], str]] = None,
    threshold: float = 0.80,
    max_iter: int = 3,
    v3_max_tokens: int = 400,
    objective: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Clean production boundary.
    Middleware does NOT import routing_engine / v2_engine directly.
    It wraps the canonical pipeline:
      Human → V2 → AI → V3 → Human
    """

    # If no LLM is wired yet, use a deterministic placeholder that won't break.
    # (Swap this later with your real model call function.)
    if ai_callable is None:
        def ai_callable(x: str) -> str:
            return x  # pass-through (zero-LLM mode)

    return run_core_pipeline(
        user_text=user_text,
        ai_callable=ai_callable,
        threshold=threshold,
        max_iter=max_iter,
        v3_max_tokens=v3_max_tokens,
        objective=objective,
    )
