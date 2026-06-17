"""Route the email product context to import or compose behavior."""

from products.email import importer
from products.email import composer


def handle(S0: dict) -> dict:
    """Handle an email product action and return the result."""
    S0 = S0 or {}
    action = S0.get("decision", {}).get("action") or S0.get("context", {}).get("action")
    user_id = S0.get("context", {}).get("user_id")

    if action == "import":
        return importer.import_mail(user_id or "")
    return composer.compose_reply(S0)
