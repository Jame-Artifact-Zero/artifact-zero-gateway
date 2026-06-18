"""Compose an email reply draft from task-level state."""


def compose_reply(S0: dict) -> dict:
    """Stub reply generation for the email product context."""
    S0 = S0 or {}

    return {
        "draft": S0.get("Q", ""),
        "subject": S0.get("email_subject", ""),
        "to": S0.get("email_to", ""),
    }
