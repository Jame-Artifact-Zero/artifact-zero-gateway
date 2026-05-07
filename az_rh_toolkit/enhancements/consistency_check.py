"""
az_rh_toolkit/enhancements/consistency_check.py
=================================================
ENHANCEMENT 4 — INTERNAL CONSISTENCY CHECK
Ref #25: Internal consistency check template.

When two methods measure the same quantity, their agreement
validates both. Disagreement flags a problem.

This module checks whether security claims across different
parameter types are internally consistent — e.g., an RSA key
and an ECC curve claiming equivalent security should have
security bit estimates within a stated tolerance.

Also catches circular parameter sets: using key sizes derived
from GRH-conditional analysis to certify compliance against
GRH-conditional bounds. The circularity check (ref #59) flags
this explicitly.

Usage:
    from az_rh_toolkit.enhancements.consistency_check import ConsistencyChecker
    cc = ConsistencyChecker()
    result = cc.check_equivalence("rsa", 3072, "ecc", "P-256")
    result = cc.check_circularity("rsa", 2048)
    report = cc.full_consistency_audit()
"""

import math
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from ..system1_validation import BoundsEngine, NIST_MINIMUMS

PROOF_DOI = "10.5281/zenodo.19581553"
ISSUER    = "Artifact Zero Labs"
TOLERANCE = 15.0  # bits — acceptable difference for cross-algorithm comparisons
                  # GNFS (RSA/DH) and Pollard rho (ECC) use different formulas
                  # NIST pairs RSA-3072 with P-256 despite ~10-bit formula difference


@dataclass
class ConsistencyResult:
    """Result of a consistency check between two parameter sets."""
    timestamp:       str
    check_type:      str   # "equivalence" | "circularity"
    param_a:         dict
    param_b:         dict
    security_a:      float
    security_b:      float
    difference:      float
    tolerance:       float
    consistent:      bool
    flag:            str   # "CONSISTENT" | "INCONSISTENT" | "CIRCULAR"
    note:            str

    def to_json(self, indent=2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def summary(self) -> str:
        icon = "✓" if self.consistent else "✗"
        lines = [
            f"━━━ Consistency Check: {self.check_type} ━━━",
            f"  {icon} Flag:       {self.flag}",
            f"  Param A:     {self.param_a} → {self.security_a:.1f} bits",
            f"  Param B:     {self.param_b} → {self.security_b:.1f} bits",
            f"  Difference:  {self.difference:.1f} bits (tolerance: {self.tolerance:.1f})",
            f"  Note:        {self.note}",
        ]
        return "\n".join(lines)


@dataclass
class CircularityResult:
    """Result of a circularity check on a single parameter set."""
    timestamp:   str
    param_type:  str
    parameters:  dict
    circular:    bool
    flag:        str
    chain:       list   # the dependency chain that creates circularity
    note:        str

    def to_json(self, indent=2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def summary(self) -> str:
        icon = "⚠" if self.circular else "✓"
        lines = [
            f"━━━ Circularity Check: {self.param_type} {self.parameters} ━━━",
            f"  {icon} Flag:  {self.flag}",
        ]
        if self.chain:
            lines.append(f"  Chain: {' → '.join(self.chain)}")
        lines.append(f"  Note:  {self.note}")
        return "\n".join(lines)


class ConsistencyChecker:
    """
    Internal consistency checker for cryptographic parameter sets.

    Two types of checks:

    1. Equivalence check: two parameter sets claiming equivalent
       security should have security bit estimates within tolerance.
       The 0.7 km/s Vflat gap is the template — small expected
       difference, flag if larger.

    2. Circularity check: detects when a parameter set's security
       claim depends on a bound that itself depends on GRH, and the
       parameter was derived using a GRH-conditional tool.
       The circular calibration finding (#58) is the template.
    """

    # Standard equivalent security pairs (NIST SP 800-57 Table 2)
    EQUIVALENT_PAIRS = [
        (("rsa", 1024),  ("ecc", 160),  ("dh", 1024)),   # 80-bit
        (("rsa", 2048),  ("ecc", 224),  ("dh", 2048)),   # 112-bit
        (("rsa", 3072),  ("ecc", 256),  ("dh", 3072)),   # 128-bit
        (("rsa", 7680),  ("ecc", 384),  ("dh", 7680)),   # 192-bit
        (("rsa", 15360), ("ecc", 521),  ("dh", 15360)),  # 256-bit
    ]

    def __init__(self, tolerance: float = TOLERANCE):
        self.bounds    = BoundsEngine()
        self.tolerance = tolerance

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _security_bits(self, ptype: str, **params) -> float:
        if ptype == "rsa":
            return self.bounds.rsa_security_bits(params["key_bits"])
        elif ptype == "ecc":
            return self.bounds.ecc_security_bits(params["curve_bits"])
        elif ptype == "dh":
            return self.bounds.dh_security_bits(params["p_bits"], params["q_bits"])
        raise ValueError(f"Unknown type: {ptype}")

    def check_equivalence(self, type_a: str, type_b: str,
                          params_a: dict, params_b: dict) -> ConsistencyResult:
        """
        Check whether two parameter sets are internally consistent
        in their security claims.

        Args:
            type_a:   "rsa" | "ecc" | "dh"
            type_b:   "rsa" | "ecc" | "dh"
            params_a: dict of parameters for type_a
            params_b: dict of parameters for type_b

        Returns:
            ConsistencyResult — CONSISTENT if within tolerance,
            INCONSISTENT if the security estimates diverge.
        """
        sec_a = self._security_bits(type_a, **params_a)
        sec_b = self._security_bits(type_b, **params_b)
        diff  = abs(sec_a - sec_b)
        consistent = diff <= self.tolerance

        if consistent:
            flag = "CONSISTENT"
            note = (
                f"{type_a.upper()} {params_a} and {type_b.upper()} {params_b} "
                f"are within {self.tolerance:.0f}-bit tolerance. "
                f"Difference: {diff:.1f} bits. Both provide equivalent security."
            )
        else:
            flag = "INCONSISTENT"
            note = (
                f"{type_a.upper()} {params_a} ({sec_a:.1f} bits) and "
                f"{type_b.upper()} {params_b} ({sec_b:.1f} bits) "
                f"differ by {diff:.1f} bits — outside {self.tolerance:.0f}-bit tolerance. "
                f"If these are claimed as equivalent, one is mis-sized."
            )

        return ConsistencyResult(
            timestamp   = self._timestamp(),
            check_type  = "equivalence",
            param_a     = {**{"type": type_a}, **params_a},
            param_b     = {**{"type": type_b}, **params_b},
            security_a  = round(sec_a, 2),
            security_b  = round(sec_b, 2),
            difference  = round(diff, 2),
            tolerance   = self.tolerance,
            consistent  = consistent,
            flag        = flag,
            note        = note,
        )

    def check_circularity(self, ptype: str, **params) -> CircularityResult:
        """
        Check whether the security assessment of this parameter set
        is circular — i.e., the parameter was likely derived using
        a GRH-conditional tool to certify compliance against a
        GRH-conditional bound.

        Circularity pattern: NIST key size recommendations embed
        GRH-conditional GNFS analysis. Using those sizes to certify
        against GRH-conditional bounds is not circular per se, but
        when the derivation tool and the certification bound share
        the same GRH assumption, it must be flagged explicitly.
        """
        # Build the dependency chain
        chain = []
        circular = False

        if ptype == "rsa":
            key_bits = params.get("key_bits", 0)
            chain = [
                f"RSA-{key_bits} key size",
                "NIST SP 800-57 recommendation",
                "GNFS complexity analysis (GRH-conditional: Bach 1990)",
                "Certification bound: GNFS (GRH-conditional)",
            ]
            # The shared dependency is GRH — flag if both derive from it
            circular = True
            note = (
                f"RSA-{key_bits} key size is recommended by NIST SP 800-57, "
                f"which uses GRH-conditional GNFS analysis for its derivation. "
                f"The certification bound also uses GRH-conditional GNFS analysis. "
                f"Both depend on the same GRH assumption (DOI:{PROOF_DOI}). "
                f"This is not invalid — the assumption is consistently applied — "
                f"but it must be disclosed in compliance documentation. "
                f"When GRH is verified, both the recommendation and the bound "
                f"become unconditional simultaneously."
            )
        elif ptype == "ecc":
            curve = params.get("curve", "")
            chain = [
                f"ECC curve {curve}",
                "NIST curve selection (Koblitz/Miller criteria)",
                "Group order distribution (GRH-conditional: Chebotarev)",
                "Certification bound: ECDLP (GRH-conditional)",
            ]
            circular = True
            note = (
                f"ECC curve {curve} was selected using criteria that include "
                f"GRH-conditional group order distribution analysis. "
                f"The certification bound also uses GRH-conditional ECDLP hardness. "
                f"Consistently GRH-dependent. Disclose in compliance documentation."
            )
        else:
            note = f"No known circularity pattern for {ptype}."

        flag = "CIRCULAR_DEPENDENCY" if circular else "NO_CIRCULARITY"

        return CircularityResult(
            timestamp  = self._timestamp(),
            param_type = ptype,
            parameters = params,
            circular   = circular,
            flag       = flag,
            chain      = chain,
            note       = note,
        )

    def full_consistency_audit(self) -> dict:
        """
        Full consistency audit: check standard equivalent pairs
        and run circularity checks on all.
        """
        equivalence_results = []

        # Check NIST standard equivalent pairs
        standard_pairs = [
            ("rsa", "ecc", {"key_bits": 2048}, {"curve_bits": 224}),
            ("rsa", "ecc", {"key_bits": 3072}, {"curve_bits": 256}),
            ("rsa", "dh",  {"key_bits": 3072}, {"p_bits": 3072, "q_bits": 256}),
            ("ecc", "dh",  {"curve_bits": 256}, {"p_bits": 3072, "q_bits": 256}),
        ]

        for ta, tb, pa, pb in standard_pairs:
            r = self.check_equivalence(ta, tb, pa, pb)
            equivalence_results.append(r)

        # Circularity checks
        circularity_results = [
            self.check_circularity("rsa", key_bits=2048),
            self.check_circularity("rsa", key_bits=3072),
            self.check_circularity("ecc", curve="P-256"),
        ]

        consistent_count = sum(1 for r in equivalence_results if r.consistent)
        circular_count   = sum(1 for r in circularity_results if r.circular)

        return {
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "issuer":             ISSUER,
            "proof_doi":          PROOF_DOI,
            "equivalence_checks": len(equivalence_results),
            "consistent":         consistent_count,
            "inconsistent":       len(equivalence_results) - consistent_count,
            "circularity_checks": len(circularity_results),
            "circular_flags":     circular_count,
            "summary": (
                f"{consistent_count}/{len(equivalence_results)} equivalence checks consistent. "
                f"{circular_count}/{len(circularity_results)} parameters have "
                f"circular GRH dependency (disclosed, not invalid)."
            ),
            "equivalence": [json.loads(r.to_json()) for r in equivalence_results],
            "circularity":  [json.loads(r.to_json()) for r in circularity_results],
        }


if __name__ == "__main__":
    cc = ConsistencyChecker()

    print("━" * 60)
    print("INTERNAL CONSISTENCY CHECKER")
    print("━" * 60)

    print("\n▶ EQUIVALENCE CHECKS (NIST standard pairs)")
    pairs = [
        ("rsa", "ecc", {"key_bits": 3072}, {"curve_bits": 256}, "RSA-3072 vs P-256"),
        ("rsa", "ecc", {"key_bits": 2048}, {"curve_bits": 256}, "RSA-2048 vs P-256 (mis-paired)"),
        ("rsa", "dh",  {"key_bits": 3072}, {"p_bits": 3072, "q_bits": 256}, "RSA-3072 vs DH-3072"),
    ]
    for ta, tb, pa, pb, label in pairs:
        r = cc.check_equivalence(ta, tb, pa, pb)
        print(f"\n  {label}:")
        print(r.summary())

    print(f"\n▶ CIRCULARITY CHECKS")
    for ptype, params in [("rsa", {"key_bits": 2048}), ("ecc", {"curve": "P-256"})]:
        r = cc.check_circularity(ptype, **params)
        print(f"\n  {ptype.upper()} {params}:")
        print(r.summary())

    print(f"\n▶ FULL AUDIT")
    report = cc.full_consistency_audit()
    print(f"  {report['summary']}")
