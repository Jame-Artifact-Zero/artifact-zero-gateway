"""
az_rh_toolkit/enhancements/filter_audit.py
============================================
ENHANCEMENT 1 — MULTI-FILTER SIMULTANEOUS VERIFICATION
Ref #11: Four-filter SPARC pipeline as template.

Apply all validity gates simultaneously, not sequentially.
Report only what passes all gates. Report what failed each gate
independently. Sequential checking stops at first failure and
loses information. Simultaneous checking shows the full picture.

Usage:
    from az_rh_toolkit.enhancements.filter_audit import FilterAudit
    fa = FilterAudit()
    report = fa.audit_rsa(key_bits=2048)
    report = fa.audit_ecc(curve="P-256")
    report = fa.audit_all()
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..system1_validation import BoundsEngine, NIST_MINIMUMS, NIST_RECOMMENDED

PROOF_DOI = "10.5281/zenodo.19581553"
ISSUER    = "Artifact Zero Labs"


@dataclass
class GateResult:
    """Result of a single filter gate."""
    gate:        str
    passed:      bool
    value:       float
    threshold:   float
    description: str


@dataclass 
class FilterAuditResult:
    """Result of simultaneous multi-gate filter audit."""
    timestamp:      str
    param_type:     str
    parameters:     dict
    gates:          list        # list of GateResult
    passed_all:     bool
    passed_count:   int
    total_gates:    int
    survivors:      list        # parameters that passed ALL gates
    eliminated_by:  dict        # gate_name -> list of parameters eliminated

    def to_json(self, indent=2) -> str:
        d = asdict(self)
        return json.dumps(d, indent=indent)

    def summary(self) -> str:
        status = "✓ PASSES ALL GATES" if self.passed_all else "✗ ELIMINATED"
        lines = [
            f"━━━ Filter Audit: {self.param_type} {self.parameters} ━━━",
            f"  Result:  {status}",
            f"  Gates:   {self.passed_count}/{self.total_gates} passed",
        ]
        for g in self.gates:
            # gates are stored as dicts via asdict() in _build_result
            gd = g if isinstance(g, dict) else asdict(g)
            icon = "✓" if gd["passed"] else "✗"
            lines.append(
                f"  {icon} {gd['gate']}: {gd['value']:.1f} vs "
                f"threshold {gd['threshold']:.1f} — {gd['description']}"
            )
        return "\n".join(lines)


class FilterAudit:
    """
    Simultaneous multi-gate filter for cryptographic parameters.

    All gates are evaluated independently before any decision is made.
    This prevents sequential filtering from masking information:
    a parameter that fails gate 2 still reports its gate 3 and 4 status.

    Gate definitions:
        G1 — Minimum bit size (NIST SP 800-57 minimum)
        G2 — Security bits (symmetric-equivalent security level)
        G3 — Recommended bit size (post-2030 readiness)
        G4 — Proof basis (RH/GRH — submitted, under review)

    The four gates applied simultaneously, not in sequence.
    """

    def __init__(self):
        self.bounds = BoundsEngine()

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _run_gates_rsa(self, key_bits: int) -> list:
        sec = self.bounds.rsa_security_bits(key_bits)
        return [
            GateResult("G1_min_bits",    key_bits >= NIST_MINIMUMS["rsa_bits"],
                       key_bits, NIST_MINIMUMS["rsa_bits"],
                       f"NIST minimum {NIST_MINIMUMS['rsa_bits']}-bit RSA"),
            GateResult("G2_security",    sec >= NIST_MINIMUMS["security_bits"],
                       round(sec, 1), NIST_MINIMUMS["security_bits"],
                       f"NIST minimum {NIST_MINIMUMS['security_bits']} symmetric bits"),
            GateResult("G3_recommended", key_bits >= NIST_RECOMMENDED["rsa_bits"],
                       key_bits, NIST_RECOMMENDED["rsa_bits"],
                       f"NIST recommended {NIST_RECOMMENDED['rsa_bits']}-bit for post-2030"),
            GateResult("G4_proof_basis", True, 1.0, 1.0,
                       f"RH basis: SUBMITTED DOI:{PROOF_DOI}"),
        ]

    def _run_gates_ecc(self, curve_bits: int, curve_name: str) -> list:
        sec = self.bounds.ecc_security_bits(curve_bits)
        return [
            GateResult("G1_min_bits",    curve_bits >= NIST_MINIMUMS["ecc_bits"],
                       curve_bits, NIST_MINIMUMS["ecc_bits"],
                       f"NIST minimum {NIST_MINIMUMS['ecc_bits']}-bit ECC"),
            GateResult("G2_security",    sec >= NIST_MINIMUMS["security_bits"],
                       round(sec, 1), NIST_MINIMUMS["security_bits"],
                       f"NIST minimum {NIST_MINIMUMS['security_bits']} symmetric bits"),
            GateResult("G3_recommended", curve_bits >= NIST_RECOMMENDED["ecc_bits"],
                       curve_bits, NIST_RECOMMENDED["ecc_bits"],
                       f"NIST recommended {NIST_RECOMMENDED['ecc_bits']}-bit curve"),
            GateResult("G4_proof_basis", True, 1.0, 1.0,
                       f"GRH basis: SUBMITTED DOI:{PROOF_DOI}"),
        ]

    def _run_gates_dh(self, p_bits: int, q_bits: int) -> list:
        sec = self.bounds.dh_security_bits(p_bits, q_bits)
        return [
            GateResult("G1_min_p_bits",  p_bits >= NIST_MINIMUMS["dh_p_bits"],
                       p_bits, NIST_MINIMUMS["dh_p_bits"],
                       f"NIST minimum {NIST_MINIMUMS['dh_p_bits']}-bit DH prime"),
            GateResult("G2_min_q_bits",  q_bits >= NIST_MINIMUMS["dh_q_bits"],
                       q_bits, NIST_MINIMUMS["dh_q_bits"],
                       f"NIST minimum {NIST_MINIMUMS['dh_q_bits']}-bit subgroup"),
            GateResult("G3_security",    sec >= NIST_MINIMUMS["security_bits"],
                       round(sec, 1), NIST_MINIMUMS["security_bits"],
                       f"NIST minimum {NIST_MINIMUMS['security_bits']} symmetric bits"),
            GateResult("G4_proof_basis", True, 1.0, 1.0,
                       f"GRH basis: SUBMITTED DOI:{PROOF_DOI}"),
        ]

    def _build_result(self, param_type, parameters, gates) -> FilterAuditResult:
        passed_all   = all(g.passed for g in gates)
        passed_count = sum(1 for g in gates if g.passed)
        eliminated   = {g.gate: [] for g in gates if not g.passed}
        for g in gates:
            if not g.passed:
                eliminated[g.gate].append(str(parameters))

        return FilterAuditResult(
            timestamp    = self._timestamp(),
            param_type   = param_type,
            parameters   = parameters,
            gates        = [asdict(g) for g in gates],
            passed_all   = passed_all,
            passed_count = passed_count,
            total_gates  = len(gates),
            survivors    = [parameters] if passed_all else [],
            eliminated_by = eliminated,
        )

    def audit_rsa(self, key_bits: int) -> FilterAuditResult:
        """Run all RSA gates simultaneously."""
        gates = self._run_gates_rsa(key_bits)
        return self._build_result("RSA", {"key_bits": key_bits}, gates)

    def audit_ecc(self, curve: str) -> FilterAuditResult:
        """Run all ECC gates simultaneously."""
        from ..system1_validation import UnconditionalValidator
        known = UnconditionalValidator.KNOWN_CURVES
        if curve not in known:
            raise ValueError(f"Unknown curve: {curve}")
        bits = known[curve]["bits"]
        gates = self._run_gates_ecc(bits, curve)
        return self._build_result("ECC", {"curve": curve, "bits": bits}, gates)

    def audit_dh(self, p_bits: int, q_bits: int) -> FilterAuditResult:
        """Run all DH gates simultaneously."""
        gates = self._run_gates_dh(p_bits, q_bits)
        return self._build_result("DH", {"p_bits": p_bits, "q_bits": q_bits}, gates)

    def audit_all(self) -> dict:
        """
        Run simultaneous multi-gate filter across all standard parameter sets.

        Returns a report showing:
        - Which parameter sets pass ALL gates
        - Which gate eliminated each failing set
        - Gate-by-gate pass rates across the full parameter space

        This is the simultaneous filter. Every parameter set is evaluated
        against every gate before any elimination decision is recorded.
        """
        candidates = [
            ("rsa",  {"key_bits": 1024}),
            ("rsa",  {"key_bits": 2048}),
            ("rsa",  {"key_bits": 3072}),
            ("rsa",  {"key_bits": 4096}),
            ("ecc",  {"curve": "P-192"}),
            ("ecc",  {"curve": "P-224"}),
            ("ecc",  {"curve": "P-256"}),
            ("ecc",  {"curve": "P-384"}),
            ("ecc",  {"curve": "secp256k1"}),
            ("ecc",  {"curve": "Curve25519"}),
            ("dh",   {"p_bits": 1024, "q_bits": 160}),
            ("dh",   {"p_bits": 2048, "q_bits": 224}),
            ("dh",   {"p_bits": 3072, "q_bits": 256}),
        ]

        results = []
        for ptype, params in candidates:
            if ptype == "rsa":
                results.append(self.audit_rsa(**params))
            elif ptype == "ecc":
                results.append(self.audit_ecc(**params))
            elif ptype == "dh":
                results.append(self.audit_dh(**params))

        survivors  = [r for r in results if r.passed_all]
        eliminated = [r for r in results if not r.passed_all]

        # Gate-by-gate pass rates
        gate_names = ["G1_min_bits", "G2_security", "G3_recommended", "G4_proof_basis",
                      "G1_min_p_bits", "G2_min_q_bits", "G3_security"]
        gate_stats = {}
        for gname in gate_names:
            relevant = [r for r in results
                        if any(g["gate"] == gname for g in r.gates)]
            if relevant:
                passed = sum(1 for r in relevant
                             if any(g["gate"] == gname and g["passed"]
                                    for g in r.gates))
                gate_stats[gname] = f"{passed}/{len(relevant)}"

        # What eliminated each failing set
        elimination_map = {}
        for r in eliminated:
            failing_gates = [g["gate"] for g in r.gates if not g["passed"]]
            key = f"{r.param_type} {r.parameters}"
            elimination_map[key] = failing_gates

        return {
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "total_candidates": len(results),
            "survivors":       len(survivors),
            "eliminated":      len(eliminated),
            "survivor_list":   [r.parameters for r in survivors],
            "elimination_map": elimination_map,
            "gate_pass_rates": gate_stats,
            "note": (
                "All gates evaluated simultaneously. "
                "A parameter set failing G1 still reports G2/G3/G4 status. "
                "Sequential checking would mask this information."
            ),
        }


if __name__ == "__main__":
    fa = FilterAudit()

    print("━" * 60)
    print("SIMULTANEOUS MULTI-GATE FILTER AUDIT")
    print("━" * 60)

    for audit_fn, label in [
        (lambda: fa.audit_rsa(1024),  "RSA-1024 (below minimum)"),
        (lambda: fa.audit_rsa(2048),  "RSA-2048"),
        (lambda: fa.audit_rsa(4096),  "RSA-4096"),
        (lambda: fa.audit_ecc("P-192"), "P-192 (below minimum)"),
        (lambda: fa.audit_ecc("P-256"), "P-256"),
        (lambda: fa.audit_dh(1024, 160), "DH-1024/160 (below minimum)"),
        (lambda: fa.audit_dh(3072, 256), "DH-3072/256"),
    ]:
        print(f"\n{label}:")
        r = audit_fn()
        print(r.summary())

    print(f"\n{'━'*60}")
    print("FULL AUDIT — ALL CANDIDATES SIMULTANEOUSLY")
    print("━" * 60)
    report = fa.audit_all()
    print(f"\n  Candidates: {report['total_candidates']}")
    print(f"  Survivors (pass all gates): {report['survivors']}")
    print(f"  Eliminated: {report['eliminated']}")
    print(f"\n  Survivors: {report['survivor_list']}")
    print(f"\n  Elimination map:")
    for param, gates in report['elimination_map'].items():
        print(f"    {param} → eliminated by: {gates}")
    print(f"\n  Gate pass rates: {report['gate_pass_rates']}")
    print(f"\n  Note: {report['note']}")
