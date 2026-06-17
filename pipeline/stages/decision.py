"""Determine the product action route for the current pipeline state."""


def run(S0: dict) -> dict:
    """Read preferences and context, then attach the route decision."""
    S0 = S0 or {}
    context = S0.get("context", {})
    preferences = S0.get("memory", {}).get("user_profile", {}).get("preferences", {})
    product = context.get("product") or preferences.get("default_product") or "score"
    action = context.get("action") or preferences.get("default_action") or "score"

    S0["decision"] = {
        "action": action,
        "product": product,
        "handler": f"products.{product}.handler",
    }
    return S0
