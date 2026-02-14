import os

def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")

def get_flags() -> dict:
    """
    Backend on/off switches.
    """
    return {
        "CORE_ENABLED": _env_bool("CORE_ENABLED", True),
        "CORE_PLUS_ENABLED": _env_bool("CORE_PLUS_ENABLED", False),
        "V3_ENABLED": _env_bool("V3_ENABLED", True),
        "ROUTING_ENABLED": _env_bool("ROUTING_ENABLED", True),
        "TRACE_ENABLED": _env_bool("TRACE_ENABLED", True),
        "SHADOW_MODE_ENABLED": _env_bool("SHADOW_MODE_ENABLED", True),
    }
