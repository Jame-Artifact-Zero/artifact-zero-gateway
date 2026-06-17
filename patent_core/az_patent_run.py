"""
az_patent_run.py
Artifact Zero — Canonical Patent Runtime Entry Point

All computation begins here. No file executes independently of this chain.
AZ-PAT-001: T= bilateral examination is the operation discovery method.
AZ-PAT-002: R = Phi(Q, S0), S1 = Psi(S0, Q, R), precondition enforced.
AZ-PAT-003: Existence coordinates declared before S0, before Phi executes.
"""

from .existence import (
    LayeredExistenceCoordinate,
    SensoryCoordinate,
    IdentityCoordinate,
)
from .state import StateStore
from .precondition import PreconditionGate
from .phi import ResponseFunction
from .psi import StateUpdateFunction
from .bilateral import BilateralExaminer
from .output import OutputClass


# Local receiver-class compatibility map.
# Kept here intentionally: receiver output constraints are runtime behavior,
# not part of the new layered existence coordinate declarations.
_RECEIVER_CLASS_MAP = {
    "human": {
        "memory": True,
        "emotional_state": True,
        "context_window": None,
    },
    "llm": {
        "memory": False,
        "emotional_state": False,
        "context_window": 200000,
    },
    "machine": {
        "memory": True,
        "emotional_state": False,
        "context_window": None,
    },
    "deferred": {
        "memory": False,
        "emotional_state": False,
        "context_window": None,
    },
}


_MODALITY_TO_SENSORY = {
    "typed_manual": {
        "inputs": ["text"],
        "missing_inputs": [],
        "fidelity": "high",
        "latency": "real_time",
    },
    "spoken_manual": {
        "inputs": ["voice"],
        "missing_inputs": [],
        "fidelity": "medium",
        "latency": "real_time",
    },
    "spoken_auto": {
        "inputs": ["voice"],
        "missing_inputs": [],
        "fidelity": "low",
        "latency": "real_time",
    },
    "machine": {
        "inputs": ["sensor"],
        "missing_inputs": [],
        "fidelity": "high",
        "latency": "real_time",
    },
    "deferred": {
        "inputs": [],
        "missing_inputs": [],
        "fidelity": "unknown",
        "latency": "unknown",
    },
}


class RuntimeReceiverClass:
    """
    Runtime receiver constraint adapter.

    This replaces the old existence.declare_receiver_class() dependency without
    moving receiver-output behavior back into existence.py.
    """

    def __init__(self, name: str, properties: dict):
        self.name = name
        self.properties = properties

    def output_constraints(self) -> dict:
        """
        Return structural constraints on R based on receiver class.
        AZ-PAT-003 Claim 3: different receiver classes produce different
        valid output sets.
        """
        base = {"receiver": self.name}

        if self.name == "human":
            base["max_complexity"] = "conversational"
            base["silence_valid"] = True

        elif self.name == "llm":
            base["max_tokens"] = self.properties["context_window"]
            base["silence_valid"] = False

        elif self.name == "machine":
            base["format"] = "structured"
            base["silence_valid"] = True

        elif self.name == "deferred":
            base["temporal_gap_declared"] = True
            base["silence_valid"] = True

        return base


def _build_sensory_coordinate(modality: str) -> SensoryCoordinate:
    """
    Map legacy run() modality values into the new SensoryCoordinate layer.
    """
    if modality not in _MODALITY_TO_SENSORY:
        raise ValueError(f"Undeclared modality: {modality}")

    mapped = _MODALITY_TO_SENSORY[modality]
    return SensoryCoordinate(
        inputs=mapped["inputs"],
        missing_inputs=mapped["missing_inputs"],
        fidelity=mapped["fidelity"],
        latency=mapped["latency"],
    )


def _build_identity_coordinate(receiver_class: str) -> IdentityCoordinate:
    """
    Map legacy run() receiver_class into the new IdentityCoordinate layer.
    """
    if receiver_class not in _RECEIVER_CLASS_MAP:
        raise ValueError(f"Undeclared receiver class: {receiver_class}")

    return IdentityCoordinate(
        receiver=receiver_class,
        receiver_knows=[],
        receiver_needs=[],
        receiver_asked=None,
    )


def _build_runtime_receiver(receiver_class: str) -> RuntimeReceiverClass:
    """
    Local helper that preserves the old RECEIVER_CLASS_MAP output behavior
    without re-adding that map to existence.py.
    """
    if receiver_class not in _RECEIVER_CLASS_MAP:
        raise ValueError(f"Undeclared receiver class: {receiver_class}")

    return RuntimeReceiverClass(
        receiver_class,
        _RECEIVER_CLASS_MAP[receiver_class],
    )


def run(
    Q: str,
    modality: str,
    receiver_class: str,
    where: str,
    when: str,
    for_: str,
    why: str,
    s0_override: dict = None,
):
    """
    Full chain per AZ-PAT-003 Claim 9:
    exists(where, when, for, why) x modality x receiver_class
    -> S_neg1 -> S0 -> Phi(Q, S0) -> R in {response, no_response, delayed_response}
    -> Psi(S0, Q, R) -> S1

    Backward-compatible signature: callers still pass modality and
    receiver_class, but those are now converted into layered coordinates.
    """

    # LAYER 0 — Layered Existence Declaration (AZ-PAT-003 Claim 1)
    sensory = _build_sensory_coordinate(modality)
    identity = _build_identity_coordinate(receiver_class)

    existence = LayeredExistenceCoordinate(
        where=where,
        when=when,
        for_=for_,
        why=why,
        sensory=sensory,
        identity=identity,
    )

    # LAYER 1 — Modality/Sensory Pre-State S_neg1 (AZ-PAT-003 Claim 2)
    # The new model derives S_neg1 from the SensoryCoordinate declaration.
    S_neg1 = sensory.as_dict()
    S_neg1["modality"] = modality
    S_neg1["existence"] = existence.as_dict()

    # LAYER 2 — Receiver Class Runtime Constraints (AZ-PAT-003 Claim 3)
    # IdentityCoordinate declares receiver identity. RuntimeReceiverClass supplies
    # the old output_constraints() behavior used downstream by BilateralExaminer.
    receiver = _build_runtime_receiver(receiver_class)

    # LAYER 3 — Prior State S0 (AZ-PAT-002 Claim 10)
    store = StateStore()
    if s0_override is not None:
        S0 = s0_override
    else:
        S0 = store.retrieve()

    # PRECONDITION GATE — Phi does not execute without S0 (AZ-PAT-002 Claim 12)
    gate = PreconditionGate(S0, S_neg1, receiver)
    gate.enforce()  # raises PreconditionFailure if S0 not declared

    # LAYER 4 — Bilateral Examination (AZ-PAT-001 Claims 1-4)
    examiner = BilateralExaminer(
        Q=Q,
        S0=S0,
        target_constraints=receiver.output_constraints(),
    )
    L_findings = examiner.examine_left()
    R_findings = examiner.examine_right()
    operation = examiner.discover_operation(L_findings, R_findings)

    # LAYER 4 — Response Function Phi (AZ-PAT-002 Claim 10)
    phi = ResponseFunction(operation=operation)
    R = phi.execute(Q=Q, S0=S0)

    # OUTPUT CLASS — R is formally selected (AZ-PAT-002 Claim 10, AZ-PAT-003 Claim 6)
    output = OutputClass(R)
    output.validate()  # confirms R is member of {response, no_response, delayed_response}

    # LAYER 5 — State Update Psi — R participates causally (AZ-PAT-002 Claims 10, 17)
    psi = StateUpdateFunction()
    S1 = psi.execute(S0=S0, Q=Q, R=R)

    # Persist S1 as new S0 — state never resets (AZ-PAT-002 Claim 18)
    store.persist(S1)

    return output.transmit()
