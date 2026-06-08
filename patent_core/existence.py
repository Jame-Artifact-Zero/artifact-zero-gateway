"""
existence.py
Declares existence coordinates before any computation begins.
AZ-PAT-003: Layer 0 (existence declaration), Layer 1 (modality pre-state),
Layer 2 (receiver class declaration)
"""

MODALITY_PRESTATE_MAP = {
    "typed_manual":   {"entropy": "low",    "drift_risk": "low",  "code": "TM"},
    "spoken_manual":  {"entropy": "medium", "drift_risk": "medium","code": "SM"},
    "spoken_auto":    {"entropy": "high",   "drift_risk": "high", "code": "SA"},
    "machine":        {"entropy": "none",   "drift_risk": "none", "code": "MC"},
    "deferred":       {"entropy": "unknown","drift_risk": "high", "code": "DF"},
}

RECEIVER_CLASS_MAP = {
    "human":    {"memory": True,  "emotional_state": True,  "context_window": None},
    "llm":      {"memory": False, "emotional_state": False, "context_window": 200000},
    "machine":  {"memory": True,  "emotional_state": False, "context_window": None},
    "deferred": {"memory": False, "emotional_state": False, "context_window": None},
}


class ExistenceCoordinate:
    """
    AZ-PAT-003 Claim 1: declares where, when, for, why before computation.
    AZ-PAT-003 Claim 2: modality establishes S_neg1.
    AZ-PAT-003 Claim 3: receiver class declared before Phi executes.
    """

    def __init__(self, where, when, for_, why, modality, receiver_class):
        self.where = where
        self.when = when
        self.for_ = for_
        self.why = why
        self.modality = modality
        self.receiver_class = receiver_class
        self._validate()

    def _validate(self):
        if self.modality not in MODALITY_PRESTATE_MAP:
            raise ValueError(f"Undeclared modality: {self.modality}")
        if self.receiver_class not in RECEIVER_CLASS_MAP:
            raise ValueError(f"Undeclared receiver class: {self.receiver_class}")

    def declare_modality_prestate(self) -> dict:
        """Returns S_neg1 — formal pre-state established by modality before content arrives."""
        base = MODALITY_PRESTATE_MAP[self.modality].copy()
        base["modality"] = self.modality
        base["when"] = self.when
        base["where"] = self.where
        return base

    def declare_receiver_class(self):
        """Returns receiver object with structural constraints for this receiver class."""
        return ReceiverClass(self.receiver_class, RECEIVER_CLASS_MAP[self.receiver_class])

    def as_dict(self) -> dict:
        return {
            "where": self.where,
            "when": self.when,
            "for": self.for_,
            "why": self.why,
            "modality": self.modality,
            "receiver_class": self.receiver_class,
        }


class ReceiverClass:
    def __init__(self, name: str, properties: dict):
        self.name = name
        self.properties = properties

    def output_constraints(self) -> dict:
        """
        Returns structural constraints on R based on receiver class.
        AZ-PAT-003 Claim 3: different receiver classes produce different valid output sets.
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
