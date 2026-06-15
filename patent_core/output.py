"""
output.py
Formally typed output set. R is always a member of this set.
AZ-PAT-002 Claim 10: R in {response, no_response, delayed_response}.
AZ-PAT-003 Claim 6: no_response and delayed_response are formally determined
outputs, not failure states or defaults.
"""


class OutputType:
    RESPONSE = "response"
    NO_RESPONSE = "no_response"
    DELAYED_RESPONSE = "delayed_response"

    ALL = {RESPONSE, NO_RESPONSE, DELAYED_RESPONSE}


class OutputClass:
    """
    AZ-PAT-002 Claim 10: output is a member of formally declared set.
    AZ-PAT-003 Claim 6: output class determined by existence coordinates,
    modality pre-state, and receiver class upstream.
    """

    def __init__(self, R: dict):
        self.R = R

    def validate(self):
        if self.R.get("type") not in OutputType.ALL:
            raise ValueError(
                f"R type {self.R.get('type')!r} is not a member of the formal output set. "
                f"Valid: {OutputType.ALL}"
            )

    def transmit(self):
        """
        AZ-PAT-002 Claim 10: transmit R or withhold if no_response or delayed.
        Withholding is not a failure. It is a formally determined output.
        """
        if self.R["type"] == OutputType.NO_RESPONSE:
            return None  # Formally determined silence. Not a default.

        if self.R["type"] == OutputType.DELAYED_RESPONSE:
            return {
                "status": "delayed",
                "pending_condition": self.R.get("pending_condition"),
            }

        return self.R.get("content")
