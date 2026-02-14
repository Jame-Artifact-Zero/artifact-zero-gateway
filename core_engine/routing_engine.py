from typing import List, Tuple

DEFAULT_ROUTING_KEYWORDS: List[str] = [
    "budget approval",
    "manager",
    "finance",
    "legal",
    "approval",
]

def route_decision(text_lower: str, keywords: List[str]) -> Tuple[str, List[str]]:
    """
    Contract (required by v2_engine.py):
      returns (route: str, route_matches: List[str])
    """
    matches = [k for k in (keywords or []) if k in (text_lower or "")]
    if matches:
        return "HUMAN_INTERNAL", matches
    return "AI_PATH", []
