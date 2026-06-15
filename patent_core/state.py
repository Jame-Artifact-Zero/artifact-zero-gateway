"""
state.py
Declares, retrieves, and persists prior state S0.
AZ-PAT-002: S0 is a precondition. State never resets. S1 accumulates.
"""

import shelve
import os
from datetime import datetime

STATE_PATH = os.path.join(os.path.dirname(__file__), "az_state")


class StateStore:
    """
    AZ-PAT-002 Claim 10: prior state S0 retrieved from state storage.
    AZ-PAT-002 Claim 18: S0 never resets. S1 accumulates all prior state.
    AZ-PAT-002 Claim 19: trace of each computation persisted for deterministic replay.
    """

    PRIOR_STATE_KEY = "S0"
    TRACE_KEY = "trace"

    def retrieve(self) -> dict | None:
        """
        Returns S0 if declared. Returns None if not declared.
        Caller (PreconditionGate) enforces — this function does not default.
        """
        with shelve.open(STATE_PATH) as shelf:
            return shelf.get(self.PRIOR_STATE_KEY, None)

    def persist(self, S1: dict):
        """
        Persists S1 as new S0. Appends to trace.
        State never resets — append only.
        """
        S1["persisted_at"] = datetime.utcnow().isoformat()
        with shelve.open(STATE_PATH) as shelf:
            shelf[self.PRIOR_STATE_KEY] = S1
            trace = shelf.get(self.TRACE_KEY, [])
            trace.append(S1)
            shelf[self.TRACE_KEY] = trace

    def initialize(self, seed_state: dict):
        """
        First-time declaration of S0. Called once at system setup.
        After this point persist() is the only valid write path.
        """
        seed_state["initialized_at"] = datetime.utcnow().isoformat()
        seed_state["iteration"] = 0
        with shelve.open(STATE_PATH) as shelf:
            if self.PRIOR_STATE_KEY in shelf:
                raise RuntimeError("S0 already initialized. Use persist() to update state.")
            shelf[self.PRIOR_STATE_KEY] = seed_state
            shelf[self.TRACE_KEY] = [seed_state]

    def get_trace(self) -> list:
        """Returns full computation trace for deterministic replay. AZ-PAT-002 Claim 19."""
        with shelve.open(STATE_PATH) as shelf:
            return shelf.get(self.TRACE_KEY, [])
