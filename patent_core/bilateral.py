"""
bilateral.py
Implements the T= bilateral examination method.
AZ-PAT-001 Claims 1-4: examine L independently, examine R independently,
discover operation from both examinations. Never prescribe before examining.
"""


class BilateralExaminer:
    """
    AZ-PAT-001 Claim 1: examine left side independently, examine right side independently,
    determine operation from examination results.
    AZ-PAT-001 Claim 4: two examination components + operation determination component.
    Operation determination component does not run until both examinations are complete.
    """

    def __init__(self, Q: str, S0: dict, target_constraints: dict):
        self.Q = Q
        self.S0 = S0
        self.target_constraints = target_constraints
        self.L_findings = None
        self.R_findings = None

    def examine_left(self) -> dict:
        """
        AZ-PAT-001 Claim 2: examine left side (Q + S0) independently.
        Determine what Q requires given S0. Do not prescribe operation here.
        """
        findings = {
            "input_length": len(self.Q),
            "has_prior_context": bool(self.S0),
            "prior_iteration": self.S0.get("iteration", 0),
            "loop_risk": self._detect_loop_risk(),
            "constraint_density": self._measure_constraint_density(),
            "requires_silence": self._check_silence_condition(),
            "requires_delay": self._check_delay_condition(),
        }
        self.L_findings = findings
        return findings

    def examine_right(self) -> dict:
        """
        AZ-PAT-001 Claim 2: examine right side (target/required output) independently.
        Determine what the required output needs. Do not prescribe operation here.
        """
        findings = {
            "receiver": self.target_constraints.get("receiver"),
            "silence_valid": self.target_constraints.get("silence_valid", False),
            "format_required": self.target_constraints.get("format", "natural"),
            "max_complexity": self.target_constraints.get("max_complexity", "unrestricted"),
            "temporal_gap_declared": self.target_constraints.get("temporal_gap_declared", False),
        }
        self.R_findings = findings
        return findings

    def discover_operation(self, L_findings: dict, R_findings: dict) -> dict:
        """
        AZ-PAT-001 Claim 1: operation is discovered from examination results.
        Not prescribed. Determined jointly from L and R findings.
        AZ-PAT-001 Claim 3: gap between L and R is information.
        """
        if L_findings is None or R_findings is None:
            raise RuntimeError("Both sides must be examined before operation can be discovered.")

        operation = {
            "route": None,
            "silence": False,
            "delay": False,
            "format": R_findings["format_required"],
        }

        # Silence is valid only if R-side permits it AND L-side indicates it
        if L_findings["requires_silence"] and R_findings["silence_valid"]:
            operation["silence"] = True
            operation["route"] = "no_response"
            return operation

        # Delay if L-side requires it
        if L_findings["requires_delay"]:
            operation["delay"] = True
            operation["route"] = "delayed_response"
            return operation

        # Loop risk detected — surface diagnostic rather than prescribe correction
        # AZ-PAT-001 Claim 6: loop condition resolved by bilateral examination
        if L_findings["loop_risk"]:
            operation["route"] = "response"
            operation["loop_diagnostic"] = True
            return operation

        # Default: response
        operation["route"] = "response"
        return operation

    def _detect_loop_risk(self) -> bool:
        """
        AZ-PAT-002 Claim 20: detect S1 ≈ S0 loop condition.
        AZ-PAT-001 Claim 6: loop produced by prescribing without examining both sides.
        """
        prior_inputs = self.S0.get("prior_inputs", [])
        if len(prior_inputs) >= 3:
            last_three = prior_inputs[-3:]
            if all(p == self.Q for p in last_three):
                return True
        return False

    def _measure_constraint_density(self) -> str:
        """Rough measure of how constrained Q is given S0."""
        constraints = self.S0.get("active_constraints", [])
        if len(constraints) == 0:
            return "none"
        if len(constraints) <= 3:
            return "low"
        if len(constraints) <= 7:
            return "medium"
        return "high"

    def _check_silence_condition(self) -> bool:
        """
        AZ-PAT-002 Claim 15: R = null when Q matches pattern in S0.
        Silence is a formally computed output, not a failure.
        """
        prior_inputs = self.S0.get("prior_inputs", [])
        return self.Q in prior_inputs

    def _check_delay_condition(self) -> bool:
        """
        AZ-PAT-002 Claim 16: R = delayed when S0 contains pending condition.
        """
        return bool(self.S0.get("pending_condition", None))
