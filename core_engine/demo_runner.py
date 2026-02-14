import os
from core_engine.convergence import run_core_pipeline

def dummy_ai(text: str) -> str:
    # Replace with your real AI call when wiring in.
    return (
        "It is important to note that maybe you asked: " + text +
        ". Ultimately, in conclusion, here is an answer. " +
        "Here is an answer."
    )

if __name__ == "__main__":
    # Defaults: shadow on, trace on, routing on, v3 on
    os.environ.setdefault("CORE_ENABLED", "true")
    os.environ.setdefault("V3_ENABLED", "true")
    os.environ.setdefault("ROUTING_ENABLED", "true")
    os.environ.setdefault("TRACE_ENABLED", "true")
    os.environ.setdefault("SHADOW_MODE_ENABLED", "true")
    os.environ.setdefault("CORE_PLUS_ENABLED", "false")

    tests = [
        "Maybe we should kind of figure out who I should talk to about budget approval.",
        "Explain the operating range rule in a short paragraph and avoid unnecessary detail."
    ]

    for t in tests:
        r = run_core_pipeline(t, dummy_ai)
        print("\nINPUT:", t)
        print("STATUS:", r["status"])
        print("OUTPUT:", r["output"])
        if "shadow_output" in r:
            print("SHADOW_OUTPUT:", r["shadow_output"])
        print("TRACE_ID:", r.get("trace_id"))
