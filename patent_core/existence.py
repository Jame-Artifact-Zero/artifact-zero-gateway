"""
patent_core/existence.py

Existence-coordinate primitives for Artifact Zero patent-core flows.

The original flat ExistenceCoordinate interface is preserved:
    ExistenceCoordinate(where, when, for_, why)

Layered coordinates are additive and sit below the existing code so older callers,
including az_patent_run.py, can keep using the flat fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# EXISTING FLAT COORDINATE INTERFACE
# ---------------------------------------------------------------------------

@dataclass
class ExistenceCoordinate:
    """
    Original flat existence coordinate.

    Keep this interface stable. Existing code may construct it with:
        ExistenceCoordinate(where=..., when=..., for_=..., why=...)

    The Python attribute remains `for_` because `for` is reserved.
    The serialized key uses `for` and also includes `for_` for compatibility.
    """

    where: Any = None
    when: Any = None
    for_: Any = None
    why: Any = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "where": self.where,
            "when": self.when,
            "for": self.for_,
            "for_": self.for_,
            "why": self.why,
        }


# ---------------------------------------------------------------------------
# LAYERED COORDINATE HELPERS
# ---------------------------------------------------------------------------

_UNSET = object()


class CoordinateValidationError(ValueError):
    """Raised when a coordinate field is outside its declared option set."""


def _validate_option(field_name: str, value: Optional[str], allowed: set[str]) -> Optional[str]:
    if value is None:
        return None
    if value not in allowed:
        raise CoordinateValidationError(
            f"{field_name} must be one of {sorted(allowed)}; got {value!r}"
        )
    return value


def _coerce_list(field_name: str, value: Optional[List[str]]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} must be a list")
    return list(value)


def _coerce_input_list(field_name: str, value: Optional[List[str]], allowed: set[str]) -> List[str]:
    items = _coerce_list(field_name, value)
    bad = [item for item in items if item not in allowed]
    if bad:
        raise CoordinateValidationError(
            f"{field_name} contains invalid values {bad!r}; allowed={sorted(allowed)}"
        )
    return items


@dataclass
class UnknownCoordinate:
    """
    Placeholder for an omitted or explicitly unknown coordinate layer.

    declared_unknown=True means the caller intentionally declared this layer as
    unknown/omitted in the layered object.

    undeclared_unknown=True means an explicit None was supplied. That preserves
    the distinction between an intentionally omitted layer and a layer that was
    passed but not declared with content.
    """

    coordinate_type: str
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": self.coordinate_type,
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
        }


# ---------------------------------------------------------------------------
# NEW LAYERED COORDINATES
# ---------------------------------------------------------------------------

@dataclass
class PhysicalCoordinate:
    location: Any = None
    environment: str = "unknown"
    jurisdiction: Optional[str] = None
    constraints: List[str] = field(default_factory=list)
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    ENVIRONMENT_OPTIONS = {"air", "water", "space", "enclosed", "open", "unknown"}

    def __post_init__(self) -> None:
        self.environment = _validate_option("environment", self.environment, self.ENVIRONMENT_OPTIONS) or "unknown"
        self.constraints = _coerce_list("constraints", self.constraints)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": "physical",
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
            "location": self.location,
            "environment": self.environment,
            "jurisdiction": self.jurisdiction,
            "constraints": self.constraints,
        }


@dataclass
class RelationalCoordinate:
    working_with: str = "unknown"
    trust_level: str = "unknown"
    authority: Any = None
    declared_by: Any = None
    revocable_by: Any = None
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    WORKING_WITH_OPTIONS = {"human", "machine", "both", "unknown"}
    TRUST_LEVEL_OPTIONS = {"admin", "operator", "user", "machine", "unknown"}

    def __post_init__(self) -> None:
        self.working_with = _validate_option("working_with", self.working_with, self.WORKING_WITH_OPTIONS) or "unknown"
        self.trust_level = _validate_option("trust_level", self.trust_level, self.TRUST_LEVEL_OPTIONS) or "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": "relational",
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
            "working_with": self.working_with,
            "trust_level": self.trust_level,
            "authority": self.authority,
            "declared_by": self.declared_by,
            "revocable_by": self.revocable_by,
        }


@dataclass
class SensoryCoordinate:
    inputs: List[str] = field(default_factory=list)
    missing_inputs: List[str] = field(default_factory=list)
    fidelity: str = "unknown"
    latency: str = "unknown"
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    INPUT_OPTIONS = {"text", "voice", "visual", "sensor", "telemetry"}
    FIDELITY_OPTIONS = {"high", "medium", "low", "unknown"}
    LATENCY_OPTIONS = {"real_time", "delayed", "batch", "unknown"}

    def __post_init__(self) -> None:
        self.inputs = _coerce_input_list("inputs", self.inputs, self.INPUT_OPTIONS)
        self.missing_inputs = _coerce_input_list("missing_inputs", self.missing_inputs, self.INPUT_OPTIONS)
        self.fidelity = _validate_option("fidelity", self.fidelity, self.FIDELITY_OPTIONS) or "unknown"
        self.latency = _validate_option("latency", self.latency, self.LATENCY_OPTIONS) or "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": "sensory",
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
            "inputs": self.inputs,
            "missing_inputs": self.missing_inputs,
            "fidelity": self.fidelity,
            "latency": self.latency,
        }


@dataclass
class TemporalCoordinate:
    mode: str = "unknown"
    when: Any = None
    deadline: Optional[Any] = None
    prior_event: Optional[str] = None
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    MODE_OPTIONS = {"real_time", "batch", "replay", "unknown"}

    def __post_init__(self) -> None:
        self.mode = _validate_option("mode", self.mode, self.MODE_OPTIONS) or "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": "temporal",
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
            "mode": self.mode,
            "when": self.when,
            "deadline": self.deadline,
            "prior_event": self.prior_event,
        }


@dataclass
class OperationalCoordinate:
    objective: Any = None
    output_constraints: List[str] = field(default_factory=list)
    acceptable_failure_modes: List[str] = field(default_factory=list)
    cost_of_error: str = "unknown"
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    COST_OF_ERROR_OPTIONS = {"critical", "high", "medium", "low", "unknown"}

    def __post_init__(self) -> None:
        self.output_constraints = _coerce_list("output_constraints", self.output_constraints)
        self.acceptable_failure_modes = _coerce_list(
            "acceptable_failure_modes", self.acceptable_failure_modes
        )
        self.cost_of_error = _validate_option(
            "cost_of_error", self.cost_of_error, self.COST_OF_ERROR_OPTIONS
        ) or "unknown"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": "operational",
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
            "objective": self.objective,
            "output_constraints": self.output_constraints,
            "acceptable_failure_modes": self.acceptable_failure_modes,
            "cost_of_error": self.cost_of_error,
        }


@dataclass
class IdentityCoordinate:
    receiver: Any = None
    receiver_knows: Any = None
    receiver_needs: Any = None
    receiver_asked: Any = None
    declared_unknown: bool = False
    undeclared_unknown: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinate_type": "identity",
            "declared_unknown": self.declared_unknown,
            "undeclared_unknown": self.undeclared_unknown,
            "receiver": self.receiver,
            "receiver_knows": self.receiver_knows,
            "receiver_needs": self.receiver_needs,
            "receiver_asked": self.receiver_asked,
        }


# ---------------------------------------------------------------------------
# LAYERED EXISTENCE COORDINATE
# ---------------------------------------------------------------------------

class LayeredExistenceCoordinate:
    """
    Backward-compatible layered existence coordinate.

    Flat compatibility:
        LayeredExistenceCoordinate(where=..., when=..., for_=..., why=...)

    Layered form:
        LayeredExistenceCoordinate(
            physical=PhysicalCoordinate(...),
            relational=RelationalCoordinate(...),
            sensory=SensoryCoordinate(...),
            temporal=TemporalCoordinate(...),
            operational=OperationalCoordinate(...),
            identity=IdentityCoordinate(...),
        )

    Omitted layer arguments become declared_unknown=True.
    Explicit None layer arguments become undeclared_unknown=True.
    """

    def __init__(
        self,
        *,
        where: Any = None,
        when: Any = None,
        for_: Any = None,
        why: Any = None,
        physical: Any = _UNSET,
        relational: Any = _UNSET,
        sensory: Any = _UNSET,
        temporal: Any = _UNSET,
        operational: Any = _UNSET,
        identity: Any = _UNSET,
    ) -> None:
        self.where = where
        self.when = when
        self.for_ = for_
        self.why = why
        self.flat = ExistenceCoordinate(where=where, when=when, for_=for_, why=why)

        self.physical = self._resolve_layer("physical", physical)
        self.relational = self._resolve_layer("relational", relational)
        self.sensory = self._resolve_layer("sensory", sensory)
        self.temporal = self._resolve_layer("temporal", temporal)
        self.operational = self._resolve_layer("operational", operational)
        self.identity = self._resolve_layer("identity", identity)

    @staticmethod
    def _resolve_layer(name: str, value: Any) -> Any:
        if value is _UNSET:
            return UnknownCoordinate(
                coordinate_type=name,
                declared_unknown=True,
                undeclared_unknown=False,
            )
        if value is None:
            return UnknownCoordinate(
                coordinate_type=name,
                declared_unknown=False,
                undeclared_unknown=True,
            )
        if not hasattr(value, "as_dict"):
            raise TypeError(f"{name} coordinate must expose as_dict(); got {type(value).__name__}")
        return value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "where": self.where,
            "when": self.when,
            "for": self.for_,
            "for_": self.for_,
            "why": self.why,
            "flat": self.flat.as_dict(),
            "layers": {
                "physical": self.physical.as_dict(),
                "relational": self.relational.as_dict(),
                "sensory": self.sensory.as_dict(),
                "temporal": self.temporal.as_dict(),
                "operational": self.operational.as_dict(),
                "identity": self.identity.as_dict(),
            },
        }

    def declared_unknown_layers(self) -> List[str]:
        layers = self.as_dict()["layers"]
        return [name for name, data in layers.items() if data.get("declared_unknown") is True]

    def undeclared_unknown_layers(self) -> List[str]:
        layers = self.as_dict()["layers"]
        return [name for name, data in layers.items() if data.get("undeclared_unknown") is True]
