"""
patent_core/detection.py

T= bilateral examination applied to existence coordinate detection.
Left side: what the environment currently is.
Right side: what existence coordinates require to be declared.
Operation discovered: populate what can be found. Declare unknown what cannot.

Detection never prescribes. It examines both sides and reports what each requires.
AZ-PAT-001: T= governs the detection flow.
AZ-PAT-003: detection is the autonomous path to LayeredExistenceCoordinate.
"""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .existence import (
    LayeredExistenceCoordinate,
    PhysicalCoordinate,
    RelationalCoordinate,
    SensoryCoordinate,
    TemporalCoordinate,
    OperationalCoordinate,
    IdentityCoordinate,
)


# ---------------------------------------------------------------------------
# LOCAL NORMALIZATION — match patent_core/existence.py dataclass options
# ---------------------------------------------------------------------------

_ENVIRONMENT_OPTIONS = {"air", "water", "space", "enclosed", "open", "unknown"}
_WORKING_WITH_OPTIONS = {"human", "machine", "both", "unknown"}
_TRUST_LEVEL_OPTIONS = {"admin", "operator", "user", "machine", "unknown"}
_INPUT_OPTIONS = {"text", "voice", "visual", "sensor", "telemetry"}
_FIDELITY_OPTIONS = {"high", "medium", "low", "unknown"}
_LATENCY_OPTIONS = {"real_time", "delayed", "batch", "unknown"}
_TEMPORAL_MODE_OPTIONS = {"real_time", "batch", "replay", "unknown"}
_COST_OF_ERROR_OPTIONS = {"critical", "high", "medium", "low", "unknown"}


def _normalize_option(value: Any, allowed: set[str], default: str = "unknown") -> str:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in allowed:
        return normalized
    return default


def _normalize_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalize_input_list(value: Any) -> list:
    return [
        item
        for item in (_normalize_option(v, _INPUT_OPTIONS, default="") for v in _normalize_list(value))
        if item
    ]


def _normalize_trust_level(value: Any) -> str:
    """
    AZ_TRUST_LEVEL is an environment signal, not guaranteed to already match
    RelationalCoordinate.TRUST_LEVEL_OPTIONS. Keep detection tolerant and pass
    only valid constructor values into existence.py.
    """
    if value is None:
        return "unknown"

    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _TRUST_LEVEL_OPTIONS:
        return normalized

    aliases = {
        "root": "admin",
        "owner": "admin",
        "administrator": "admin",
        "op": "operator",
        "human_operator": "operator",
        "trusted_operator": "operator",
        "end_user": "user",
        "human": "user",
        "system": "machine",
        "automation": "machine",
        "ci": "machine",
        "bot": "machine",
    }
    return aliases.get(normalized, "unknown")


# ---------------------------------------------------------------------------
# LEFT SIDE — environment interrogation
# What the environment currently is.
# ---------------------------------------------------------------------------

class EnvironmentProbe:
    """
    Examines the left side of the detection equation.
    Reads the environment. Does not interpret. Does not prescribe.
    Returns raw findings. Detection examines those findings against
    what existence coordinates require.
    """

    def probe_process(self) -> Dict[str, Any]:
        return {
            "pid": os.getpid(),
            "platform": platform.system(),
            "python_version": sys.version,
            "executable": sys.executable,
        }

    def probe_time(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "utc_iso": now.isoformat(),
            "utc_timestamp": now.timestamp(),
            "mode": "real_time",
        }

    def probe_inputs(self) -> Dict[str, Any]:
        """
        Determines what input modalities are present.
        stdin check: is input being piped or is it a terminal.
        """
        is_terminal = sys.stdin.isatty() if hasattr(sys.stdin, "isatty") else False
        return {
            "stdin_is_terminal": is_terminal,
            "inputs": ["text"] if is_terminal else ["text"],
            "fidelity": "high" if is_terminal else "medium",
            "latency": "real_time",
        }

    def probe_environment(self) -> Dict[str, Any]:
        return {
            "platform": platform.system(),
            "node": platform.node(),
            "environment": self._classify_environment(),
            "constraints": self._detect_constraints(),
        }

    def _classify_environment(self) -> str:
        system = platform.system().lower()
        if "linux" in system:
            # Containerized or server — treat as enclosed
            return "enclosed"
        if "darwin" in system or "windows" in system:
            return "enclosed"
        return "unknown"

    def _detect_constraints(self) -> list:
        constraints = []
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            constraints.append("kubernetes")
        if os.environ.get("DOCKER_CONTAINER") or os.path.exists("/.dockerenv"):
            constraints.append("docker")
        if os.environ.get("CI"):
            constraints.append("ci_environment")
        return constraints

    def probe_trust(self) -> Dict[str, Any]:
        """
        Reads trust signals from environment.
        Does not assign trust. Reports what is present.
        """
        az_trust = os.environ.get("AZ_TRUST_LEVEL", None)
        az_operator = os.environ.get("AZ_OPERATOR", None)
        az_push = os.environ.get("AZ_PUSH_LABEL", None)
        return {
            "az_trust_level": az_trust,
            "az_operator": az_operator,
            "az_push_label": az_push,
        }

    def probe_all(self) -> Dict[str, Any]:
        return {
            "process": self.probe_process(),
            "time": self.probe_time(),
            "inputs": self.probe_inputs(),
            "environment": self.probe_environment(),
            "trust": self.probe_trust(),
        }


# ---------------------------------------------------------------------------
# RIGHT SIDE — existence layer requirements
# What existence coordinates require to be declared.
# ---------------------------------------------------------------------------

class ExistenceRequirements:
    """
    Examines the right side of the detection equation.
    States what each existence layer requires to be populated.
    These are fixed requirements. They do not change based on environment.
    """

    PHYSICAL_REQUIRES = ["location", "environment"]
    RELATIONAL_REQUIRES = ["working_with", "trust_level"]
    SENSORY_REQUIRES = ["inputs", "fidelity", "latency"]
    TEMPORAL_REQUIRES = ["mode", "when"]
    OPERATIONAL_REQUIRES = ["objective"]
    IDENTITY_REQUIRES = ["receiver"]

    def as_dict(self) -> Dict[str, list]:
        return {
            "physical": self.PHYSICAL_REQUIRES,
            "relational": self.RELATIONAL_REQUIRES,
            "sensory": self.SENSORY_REQUIRES,
            "temporal": self.TEMPORAL_REQUIRES,
            "operational": self.OPERATIONAL_REQUIRES,
            "identity": self.IDENTITY_REQUIRES,
        }


# ---------------------------------------------------------------------------
# THE GAP — bilateral examination
# What the environment cannot answer becomes declared_unknown.
# Declared unknown is not failure. It is correct output.
# ---------------------------------------------------------------------------

class DetectionExaminer:
    """
    T= bilateral examination for existence coordinate detection.

    Left side:  EnvironmentProbe findings
    Right side: ExistenceRequirements
    Gap:        what environment cannot satisfy -> declared_unknown
    Operation:  populate what can be found, declare unknown what cannot

    AZ-PAT-001 Claim 1: both sides examined independently before
    any coordinate is populated.
    AZ-PAT-001 Claim 2: operation determined only after both sides examined.
    """

    def __init__(
        self,
        probe: Optional[EnvironmentProbe] = None,
        requirements: Optional[ExistenceRequirements] = None,
    ):
        self.probe = probe or EnvironmentProbe()
        self.requirements = requirements or ExistenceRequirements()

    def examine_left(self) -> Dict[str, Any]:
        """Left side examination — what the environment currently is."""
        return self.probe.probe_all()

    def examine_right(self) -> Dict[str, list]:
        """Right side examination — what existence coordinates require."""
        return self.requirements.as_dict()

    def discover_operation(
        self,
        left: Dict[str, Any],
        right: Dict[str, list],
    ) -> Dict[str, Any]:
        """
        Operation discovered from examination of both sides.
        Not prescribed. Derived from the gap between what exists
        and what is required.

        Returns a findings dict: populated fields and declared_unknown fields
        per layer.
        """
        findings = {}

        # PHYSICAL
        findings["physical"] = {
            "location": left["environment"].get("node", None),
            "environment": left["environment"].get("environment", "unknown"),
            "jurisdiction": None,
            "constraints": left["environment"].get("constraints", []),
            "can_populate": ["location", "environment", "constraints"],
            "declared_unknown": ["jurisdiction"],
        }

        # RELATIONAL
        trust_raw = left["trust"].get("az_trust_level", None)
        operator_raw = left["trust"].get("az_operator", None)
        findings["relational"] = {
            "working_with": "human" if trust_raw else None,
            "trust_level": trust_raw if trust_raw else None,
            "authority": None,
            "declared_by": operator_raw,
            "revocable_by": None,
            "can_populate": ["working_with", "trust_level", "declared_by"] if trust_raw else [],
            "declared_unknown": ["working_with", "trust_level", "authority", "revocable_by"] if not trust_raw else ["authority", "revocable_by"],
        }

        # SENSORY
        findings["sensory"] = {
            "inputs": left["inputs"].get("inputs", []),
            "fidelity": left["inputs"].get("fidelity", "unknown"),
            "latency": left["inputs"].get("latency", "unknown"),
            "missing_inputs": [],
            "can_populate": ["inputs", "fidelity", "latency"],
            "declared_unknown": [],
        }

        # TEMPORAL
        findings["temporal"] = {
            "mode": left["time"].get("mode", "unknown"),
            "when": left["time"].get("utc_iso", None),
            "deadline": None,
            "prior_event": left["trust"].get("az_push_label", None),
            "can_populate": ["mode", "when"],
            "declared_unknown": ["deadline"],
        }

        # OPERATIONAL
        # Objective cannot be detected from environment alone.
        # It requires JOS or caller declaration.
        findings["operational"] = {
            "objective": None,
            "output_constraints": [],
            "acceptable_failure_modes": [],
            "cost_of_error": "unknown",
            "can_populate": [],
            "declared_unknown": ["objective"],
        }

        # IDENTITY
        # Receiver cannot be detected from environment alone
        # without explicit trust/operator signal.
        receiver = operator_raw if operator_raw else None
        findings["identity"] = {
            "receiver": receiver,
            "receiver_knows": None,
            "receiver_needs": None,
            "receiver_asked": None,
            "can_populate": ["receiver"] if receiver else [],
            "declared_unknown": ["receiver"] if not receiver else [],
        }

        return findings

    def run(self) -> Dict[str, Any]:
        """
        Full T= detection pass.
        Returns findings from bilateral examination.
        """
        left = self.examine_left()
        right = self.examine_right()
        operation = self.discover_operation(left, right)
        return {
            "left": left,
            "right": right,
            "findings": operation,
        }


# ---------------------------------------------------------------------------
# DETECTION OUTPUT — LayeredExistenceCoordinate
# The autonomous path. Same output type as manual declaration.
# The chain does not care which path populated the coordinates.
# ---------------------------------------------------------------------------

class DetectionRunner:
    """
    Runs detection and produces a LayeredExistenceCoordinate.

    This is the autonomous path. Manual declaration produces the same type.
    Both feed exists() identically.

    Omega= note: each call to run() is one pass. Findings from prior passes
    should be fed back as S0 to the next pass. Prior state never resets.
    Multi-pass detection compounds findings. Single-pass detection finds
    only what is visible from one direction.
    """

    def __init__(self, examiner: Optional[DetectionExaminer] = None):
        self.examiner = examiner or DetectionExaminer()

    def run(
        self,
        for_: Optional[str] = None,
        why: Optional[str] = None,
        override: Optional[Dict[str, Any]] = None,
    ) -> LayeredExistenceCoordinate:
        """
        Run detection. Produce LayeredExistenceCoordinate.

        for_ and why cannot be detected from environment.
        Pass them explicitly or they become declared_unknown.

        override: caller-supplied values that take precedence over detected values.
        Used when partial manual declaration supplements detection.
        """
        result = self.examiner.run()
        findings = result["findings"]
        ov = override or {}

        # PHYSICAL
        phys_f = findings["physical"]
        physical_location = ov.get("location", phys_f.get("location"))
        physical_environment = _normalize_option(
            ov.get("environment", phys_f.get("environment", "unknown")),
            _ENVIRONMENT_OPTIONS,
        )
        physical_constraints = _normalize_list(ov.get("constraints", phys_f.get("constraints", [])))
        physical_jurisdiction = ov.get("jurisdiction", phys_f.get("jurisdiction"))
        physical = PhysicalCoordinate(
            location=physical_location,
            environment=physical_environment,
            jurisdiction=physical_jurisdiction,
            constraints=physical_constraints,
            declared_unknown=physical_location is None and physical_environment == "unknown",
        )

        # RELATIONAL
        rel_f = findings["relational"]
        working_with_raw = ov.get("working_with", rel_f.get("working_with"))
        trust_level_raw = ov.get("trust_level", rel_f.get("trust_level"))
        working_with = _normalize_option(working_with_raw, _WORKING_WITH_OPTIONS)
        trust_level = _normalize_trust_level(trust_level_raw)
        relational = RelationalCoordinate(
            working_with=working_with,
            trust_level=trust_level,
            authority=ov.get("authority", rel_f.get("authority")),
            declared_by=ov.get("declared_by", rel_f.get("declared_by")),
            revocable_by=ov.get("revocable_by", rel_f.get("revocable_by")),
            declared_unknown=working_with == "unknown" and trust_level == "unknown",
        )

        # SENSORY
        sens_f = findings["sensory"]
        sensory_inputs = _normalize_input_list(ov.get("inputs", sens_f.get("inputs", [])))
        sensory_missing_inputs = _normalize_input_list(
            ov.get("missing_inputs", sens_f.get("missing_inputs", []))
        )
        sensory = SensoryCoordinate(
            inputs=sensory_inputs,
            missing_inputs=sensory_missing_inputs,
            fidelity=_normalize_option(
                ov.get("fidelity", sens_f.get("fidelity", "unknown")),
                _FIDELITY_OPTIONS,
            ),
            latency=_normalize_option(
                ov.get("latency", sens_f.get("latency", "unknown")),
                _LATENCY_OPTIONS,
            ),
            declared_unknown=not sensory_inputs,
        )

        # TEMPORAL
        temp_f = findings["temporal"]
        temporal_when = ov.get("when", temp_f.get("when"))
        temporal = TemporalCoordinate(
            mode=_normalize_option(
                ov.get("mode", temp_f.get("mode", "unknown")),
                _TEMPORAL_MODE_OPTIONS,
            ),
            when=temporal_when,
            deadline=ov.get("deadline", temp_f.get("deadline")),
            prior_event=ov.get("prior_event", temp_f.get("prior_event")),
            declared_unknown=temporal_when is None,
        )

        # OPERATIONAL — cannot be detected, must be declared
        objective = ov.get("objective", findings["operational"].get("objective"))
        op_f = findings["operational"]
        operational = OperationalCoordinate(
            objective=objective,
            output_constraints=_normalize_list(
                ov.get("output_constraints", op_f.get("output_constraints", []))
            ),
            acceptable_failure_modes=_normalize_list(
                ov.get("acceptable_failure_modes", op_f.get("acceptable_failure_modes", []))
            ),
            cost_of_error=_normalize_option(
                ov.get("cost_of_error", op_f.get("cost_of_error", "unknown")),
                _COST_OF_ERROR_OPTIONS,
            ),
            declared_unknown=objective is None,
        )

        # IDENTITY
        ident_f = findings["identity"]
        receiver = ov.get("receiver", ident_f.get("receiver"))
        identity = IdentityCoordinate(
            receiver=receiver,
            receiver_knows=ov.get("receiver_knows", ident_f.get("receiver_knows")),
            receiver_needs=ov.get("receiver_needs", ident_f.get("receiver_needs")),
            receiver_asked=ov.get("receiver_asked", ident_f.get("receiver_asked")),
            declared_unknown=receiver is None,
        )

        return LayeredExistenceCoordinate(
            where=ov.get("where", findings["physical"].get("location")),
            when=ov.get("when", findings["temporal"].get("when")),
            for_=for_,
            why=why,
            physical=physical,
            relational=relational,
            sensory=sensory,
            temporal=temporal,
            operational=operational,
            identity=identity,
        )


# ---------------------------------------------------------------------------
# PUBLIC INTERFACE
# ---------------------------------------------------------------------------

def detect(
    for_: Optional[str] = None,
    why: Optional[str] = None,
    override: Optional[Dict[str, Any]] = None,
) -> LayeredExistenceCoordinate:
    """
    Primary entry point for autonomous existence coordinate detection.

    Returns LayeredExistenceCoordinate populated from environment examination.
    Anything the environment cannot answer is declared_unknown.
    Declared unknown is not failure. It is correct output.

    for_ and why must be passed explicitly — they cannot be detected.
    override supplies caller-declared values that take precedence over detection.
    """
    return DetectionRunner().run(for_=for_, why=why, override=override)
