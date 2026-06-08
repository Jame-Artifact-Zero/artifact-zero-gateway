"""
app.py
Artifact Zero — Root Application Entry Point
All computation begins here. No file executes independently of this chain.
AZ-PAT-001: T= bilateral examination is the operation discovery method
AZ-PAT-002: R = Phi(Q, S0), S1 = Psi(S0, Q, R), precondition enforced
AZ-PAT-003: Existence coordinates declared before S0, before Phi executes
"""

from .existence import ExistenceCoordinate
from .state import StateStore
from .precondition import PreconditionGate
from .phi import ResponseFunction
from .psi import StateUpdateFunction
from .bilateral import BilateralExaminer
from .output import OutputClass

def run(Q: str, modality: str, receiver_class: str, where: str, when: str, for_: str, why: str):
    """
    Full chain per AZ-PAT-003 Claim 9:
    exists(where, when, for, why) x modality x receiver_class
    -> S_neg1 -> S0 -> Phi(Q, S0) -> R in {response, no_response, delayed_response}
    -> Psi(S0, Q, R) -> S1
    """

    # LAYER 0 — Existence Declaration (AZ-PAT-003 Claim 1)
    existence = ExistenceCoordinate(
        where=where,
        when=when,
        for_=for_,
        why=why,
        modality=modality,
        receiver_class=receiver_class
    )

    # LAYER 1 — Modality Pre-State S_neg1 (AZ-PAT-003 Claim 2)
    S_neg1 = existence.declare_modality_prestate()

    # LAYER 2 — Receiver Class Declaration (AZ-PAT-003 Claim 3)
    receiver = existence.declare_receiver_class()

    # LAYER 3 — Prior State S0 (AZ-PAT-002 Claim 10)
    store = StateStore()
    S0 = store.retrieve()

    # PRECONDITION GATE — Phi does not execute without S0 (AZ-PAT-002 Claim 12)
    gate = PreconditionGate(S0, S_neg1, receiver)
    gate.enforce()  # raises PreconditionFailure if S0 not declared

    # LAYER 4 — Bilateral Examination (AZ-PAT-001 Claims 1-4)
    examiner = BilateralExaminer(Q=Q, S0=S0, target_constraints=receiver.output_constraints())
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
