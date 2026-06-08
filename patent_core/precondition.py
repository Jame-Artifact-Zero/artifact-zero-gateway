"""
precondition.py
Enforces that S0 is declared before Phi executes.
AZ-PAT-002 Claim 12: runtime refuses to execute Phi if S0 not declared.
Returns PreconditionFailure — not None, not a default output.
"""


class PreconditionFailure(Exception):
    """
    Formally typed precondition failure.
    AZ-PAT-002 Claim 12: this is not a default output. It is a typed failure state.
    """
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"PRECONDITION FAILURE: {reason}")


class PreconditionGate:
    """
    AZ-PAT-002 Claim 12: runtime engine refuses to execute Phi if S0 not declared.
    AZ-PAT-003 Claim 5: S0 cannot be correctly declared without existence coordinates upstream.
    """

    def __init__(self, S0, S_neg1: dict, receiver):
        self.S0 = S0
        self.S_neg1 = S_neg1
        self.receiver = receiver

    def enforce(self):
        if self.S0 is None:
            raise PreconditionFailure("S0 not declared. Phi cannot execute.")

        if not isinstance(self.S0, dict):
            raise PreconditionFailure("S0 is not a valid state object.")

        if self.S_neg1 is None:
            raise PreconditionFailure("Modality pre-state S_neg1 not declared.")

        if self.receiver is None:
            raise PreconditionFailure("Receiver class not declared.")

        # AZ-PAT-003 Claim 5: S0 is downstream of existence coordinates.
        # If S0 exists but was initialized without existence coordinates, flag it.
        if "initialized_at" not in self.S0:
            raise PreconditionFailure("S0 present but not formally initialized.")
