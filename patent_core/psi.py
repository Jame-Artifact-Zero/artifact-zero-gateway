"""
psi.py
State update function Psi. R participates causally in state transition.
AZ-PAT-002 Claim 10: S1 = Psi(S0, Q, R).
AZ-PAT-002 Claim 17: different R values produce different S1 when Q and S0 held constant.
AZ-PAT-002 Claim 18: state never resets.
"""

from .output import OutputType
from datetime import datetime


class StateUpdateFunction:
    """
    AZ-PAT-002 Claim 17: R is a causal participant in state transition.
    This is the structural departure from all prior state machines.
    Mealy: S' = delta(S, Q). R does not participate.
    This invention: S1 = Psi(S0, Q, R). R participates.
    """

    def execute(self, S0: dict, Q: str, R: dict) -> dict:
        S1 = S0.copy()

        # Increment iteration — state accumulates, never resets
        S1["iteration"] = S0.get("iteration", 0) + 1
        S1["last_Q"] = Q
        S1["last_R_type"] = R["type"]
        S1["updated_at"] = datetime.utcnow().isoformat()

        # Accumulate prior inputs for loop detection
        prior_inputs = S0.get("prior_inputs", []).copy()
        prior_inputs.append(Q)
        S1["prior_inputs"] = prior_inputs[-20:]  # rolling window, not reset

        # AZ-PAT-002 Claim 17: R type causally determines state branch
        if R["type"] == OutputType.NO_RESPONSE:
            # Silence was the computed output — record it distinctly
            silence_log = S0.get("silence_log", []).copy()
            silence_log.append({
                "Q": Q,
                "iteration": S1["iteration"],
                "timestamp": S1["updated_at"],
            })
            S1["silence_log"] = silence_log
            S1["last_response_was_silence"] = True

        elif R["type"] == OutputType.DELAYED_RESPONSE:
            # Pending condition recorded — different S1 than silence or response
            S1["pending_condition"] = R.get("pending_condition")
            S1["last_response_was_silence"] = False
            S1["has_pending"] = True

        elif R["type"] == OutputType.RESPONSE:
            # Normal response — clear any pending, record content
            S1["pending_condition"] = None
            S1["has_pending"] = False
            S1["last_response_was_silence"] = False
            S1["last_response_content"] = R.get("content")

            # Loop diagnostic surfaced from bilateral examiner
            if R.get("loop_diagnostic"):
                loop_log = S0.get("loop_log", []).copy()
                loop_log.append({
                    "Q": Q,
                    "iteration": S1["iteration"],
                    "timestamp": S1["updated_at"],
                })
                S1["loop_log"] = loop_log

        return S1
