from typing import Dict, List, Tuple

DEFAULT_ROUTING_KEYWORDS: List[str] = [
    "budget approval", "manager", "finance", "legal", "approval",
    "hr department", "human resources", "compliance", "regulatory",
    "board approval", "executive sign-off", "sign off", "signoff",
    "supervisor", "director", "vp approval", "c-suite", "ceo",
    "cfo", "cto", "coo", "general counsel", "outside counsel",
    "insurance carrier", "underwriter", "claims adjuster",
    "physician", "doctor", "prescriber", "pharmacist",
    "notary", "witness required", "countersignature",
    "two-party consent", "dual authorization", "escalate to",
    "needs authorization", "requires approval", "pending review",
    "committee review", "board review",
]

INDUSTRY_ROUTING: Dict[str, List[str]] = {
    "healthcare": [
        "hipaa", "phi", "protected health", "patient consent", "medical records",
        "prior authorization", "formulary", "clinical trial", "irb", "informed consent",
        "discharge planning", "case manager", "utilization review", "peer review",
        "credentialing", "privileges", "malpractice", "adverse event", "sentinel event",
    ],
    "legal": [
        "attorney-client", "privileged", "work product", "discovery", "deposition",
        "subpoena", "motion to", "filing deadline", "statute of limitations", "jurisdiction",
        "venue", "arbitration", "mediation", "settlement authority", "retainer",
        "conflict check", "engagement letter", "disqualification", "recusal",
    ],
    "insurance": [
        "policy limit", "coverage determination", "exclusion", "endorsement",
        "binder", "declarations page", "loss run", "actuarial", "reinsurance",
        "subrogation", "bad faith", "reservation of rights", "excess carrier",
        "surplus lines", "admitted carrier", "naic", "rate filing",
    ],
    "real_estate": [
        "title search", "title insurance", "escrow", "closing disclosure",
        "earnest money", "contingency", "inspection period", "appraisal",
        "clear to close", "deed of trust", "lien", "encumbrance", "easement",
        "zoning", "survey", "hoa", "special assessment", "transfer tax",
    ],
    "financial": [
        "fiduciary", "accredited investor", "securities", "sec filing",
        "material nonpublic", "insider trading", "kyc", "aml", "anti-money laundering",
        "beneficial owner", "qualified purchaser", "reg d", "private placement",
        "prospectus", "offering memorandum", "term sheet", "cap table",
    ],
    "government": [
        "foia", "freedom of information", "public records", "open meetings",
        "sunshine law", "procurement", "rfp", "sole source", "prevailing wage",
        "davis-bacon", "ada compliance", "eeoc", "title ix", "ferpa", "section 508",
    ],
}


def route_decision(text_lower: str, keywords: List[str]) -> Tuple[str, List[str]]:
    """
    Contract (required by v2_engine.py):
      returns (route: str, route_matches: List[str])
    """
    matches = [k for k in (keywords or []) if k in (text_lower or "")]
    if matches:
        return "HUMAN_INTERNAL", matches
    return "AI_PATH", []
