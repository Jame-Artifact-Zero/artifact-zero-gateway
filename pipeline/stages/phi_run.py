"""Run the core engine scoring step for the current task state."""

from core_engine.app import detect_all, score_composite


def run(S0: dict) -> dict:
    S0 = S0 or {}

    text = str(
        S0.get("Q")
        or S0.get("text")
        or S0.get("input")
        or S0.get("message")
        or ""
    )
    prompt = str(S0.get("prompt") or "")
    answer = str(S0.get("answer") or "")

    detection = detect_all(text=text, prompt=prompt, answer=answer)
    scoring   = score_composite(detection)

    return {
        "type": "response",
        "content": {
            "detection": detection,
            "scoring":   scoring,
        }
    }
