"""
phi.py
Response function Phi. Executes only after precondition gate passes.
AZ-PAT-002 Claim 10: R = Phi(Q, S0). Q and S0 both required simultaneously.
R is member of {response, no_response, delayed_response}.
"""

from .output import OutputClass, OutputType


class ResponseFunction:
    """
    AZ-PAT-002 Claim 10: response function Phi receives Q and S0 jointly.
    Cannot execute without both. Returns formally typed R.
    AZ-PAT-002 Claim 14: rule-based deterministic path available.
    AZ-PAT-002 Claim 13: LLM path available.
    AZ-PAT-002 Claim 23: runtime routes to response, deterministic, or silence.
    """

    def __init__(self, operation: dict):
        self.operation = operation

    def execute(self, Q: str, S0: dict) -> dict:
        route = self.operation.get("route")

        if route == "no_response":
            return {
                "type": OutputType.NO_RESPONSE,
                "content": None,
                "determined_by": "bilateral_examination",
                "Q": Q,
                "S0_iteration": S0.get("iteration", 0),
            }

        if route == "delayed_response":
            return {
                "type": OutputType.DELAYED_RESPONSE,
                "content": None,
                "pending_condition": S0.get("pending_condition"),
                "determined_by": "bilateral_examination",
                "Q": Q,
                "S0_iteration": S0.get("iteration", 0),
            }

        if route == "response":
            # AZ-PAT-002 Claim 23: route to deterministic or LLM based on S0
            if self._use_deterministic(Q, S0):
                content = self._deterministic_response(Q, S0)
            else:
                content = self._llm_response(Q, S0)

            return {
                "type": OutputType.RESPONSE,
                "content": content,
                "loop_diagnostic": self.operation.get("loop_diagnostic", False),
                "determined_by": "phi",
                "Q": Q,
                "S0_iteration": S0.get("iteration", 0),
            }

        raise ValueError(f"Unknown route: {route}")

    def _use_deterministic(self, Q: str, S0: dict) -> bool:
        """
        AZ-PAT-002 Claim 14: deterministic path used when S0 contains
        sufficient constraints to resolve Q without invoking external model.
        """
        rules = S0.get("deterministic_rules", {})
        return Q in rules

    def _deterministic_response(self, Q: str, S0: dict) -> str:
        rules = S0.get("deterministic_rules", {})
        return rules[Q]

    def _llm_response(self, Q: str, S0: dict) -> str:
        """
        Placeholder for LLM call. In live system this calls the governed
        LLM endpoint with Q and S0 as joint inputs.
        AZ-PAT-002 Claim 13: language model operates on both Q and S0.
        """
        # Production: call Claude/GPT endpoint here with S0 as context
        return f"[LLM_RESPONSE placeholder for Q={Q!r} at iteration {S0.get('iteration',0)}]"
