"""Compose an email reply draft from task-level state."""


def compose_reply(S0: dict) -> dict:
    """Stub reply generation for the email product context."""
    S0 = S0 or {}
    context = S0.get("context", {})
    event = context.get("event", {})

    return {
        "draft": "",
        "subject": event.get("subject", ""),
        "to": event.get("from") or event.get("to", ""),
    }
