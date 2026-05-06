"""
az_rh_toolkit/api.py
=====================
ARTIFACT ZERO — CRYPTOGRAPHIC CERTIFICATE API
Version 1.0.0 | April 2026

Single endpoint. Any parameter set in. Signed certificate out.

Proof status: SUBMITTED — under peer review at Annals of Mathematics.
DOI: 10.5281/zenodo.19581553
Submitted: April 14, 2026. Receipt confirmed.

When peer review confirms the proof, the status field updates.
Every other line of this codebase stays the same.

Usage:
    python -m az_rh_toolkit.api
    
    or as a library:
    from az_rh_toolkit.api import CertificateAPI
    api = CertificateAPI()
    cert = api.certify(type="rsa", key_bits=2048)
    cert = api.certify(type="ecc", curve="P-256")
    cert = api.certify(type="prime", n=2**127-1)
    cert = api.certify(type="dh", p_bits=3072, q_bits=256)
    report = api.audit(system_name="MyProduct v1.0")
"""

import json
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional

from .system1_validation import UnconditionalValidator
from .system2_primality   import PrimalityEngine
from .system3_grh         import GRHEngine

# ── Proof status — change here when peer review confirms ─────────────────────
PROOF_DOI        = "10.5281/zenodo.19581553"
PROOF_SUBMITTED  = "2026-04-14"
PROOF_JOURNAL    = "Annals of Mathematics"
PROOF_RECEIPT    = "Confirmed — Regina Finn, Annals of Mathematics"
PROOF_STATUS     = "SUBMITTED_UNDER_REVIEW"
PROOF_STATUS_MSG = (
    "Proof submitted to Annals of Mathematics, April 14 2026. "
    "Receipt confirmed. Peer review in progress. "
    "DOI: 10.5281/zenodo.19581553. "
    "Status will update to VERIFIED upon peer confirmation."
)

ISSUER  = "Artifact Zero Labs"
VERSION = "1.0.0"

# ── Signing key (in production: load from env / HSM) ─────────────────────────
_SIGNING_KEY = os.environ.get("AZ_SIGNING_KEY", "az-dev-key-change-in-production")


# ── Certificate envelope ──────────────────────────────────────────────────────
@dataclass
class Certificate:
    """
    A signed cryptographic parameter certificate.
    
    proof_status reflects current peer review state.
    signature is HMAC-SHA256 over canonical JSON of all other fields.
    """
    version:         str
    issued:          str
    issuer:          str
    proof_doi:       str
    proof_status:    str
    proof_status_msg: str
    param_type:      str
    parameters:      dict
    assessment:      dict   # security_bits, compliant, bound_type, notes
    signature:       str    # HMAC-SHA256

    def to_json(self, indent=2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def verify_signature(self, key: str = _SIGNING_KEY) -> bool:
        payload = self._canonical_payload()
        expected = hmac.new(
            key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(self.signature, expected)

    def _canonical_payload(self) -> str:
        """Deterministic payload for signing — excludes signature field."""
        d = asdict(self)
        d.pop("signature", None)
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

    def summary(self) -> str:
        a = self.assessment
        compliant = "✓ COMPLIANT" if a.get("compliant") else "✗ NON-COMPLIANT"
        lines = [
            "━━━ Artifact Zero Certificate ━━━",
            f"  Issued:       {self.issued}",
            f"  Issuer:       {self.issuer}",
            f"  Proof DOI:    {self.proof_doi}",
            f"  Proof status: {self.proof_status}",
            f"  Type:         {self.param_type}",
            f"  Parameters:   {self.parameters}",
            f"  Security:     {a.get('security_bits', 'N/A')} bits",
            f"  Result:       {compliant}",
            f"  Sig:          {self.signature[:16]}...",
        ]
        for note in a.get("notes", [])[:2]:
            lines.append(f"  Note:         {note[:80]}")
        return "\n".join(lines)


# ── Main API ──────────────────────────────────────────────────────────────────
class CertificateAPI:
    """
    Single-entry certificate API.

    certify(type, **params) → Certificate
    audit(system_name)      → dict (full JSON report)
    theorems()              → list of theorems with current proof status
    zeros(count)            → list of verified zeta zeros
    """

    def __init__(self, signing_key: str = _SIGNING_KEY):
        self._key       = signing_key
        self._validator = UnconditionalValidator()
        self._primality = PrimalityEngine()
        self._grh       = GRHEngine()

    def _sign(self, cert: Certificate) -> str:
        payload = cert._canonical_payload()
        return hmac.new(
            self._key.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    def _wrap(self, param_type: str, parameters: dict,
              assessment: dict) -> Certificate:
        """Wrap an assessment into a signed Certificate."""
        cert = Certificate(
            version          = VERSION,
            issued           = datetime.now(timezone.utc).isoformat(),
            issuer           = ISSUER,
            proof_doi        = PROOF_DOI,
            proof_status     = PROOF_STATUS,
            proof_status_msg = PROOF_STATUS_MSG,
            param_type       = param_type,
            parameters       = parameters,
            assessment       = assessment,
            signature        = "",
        )
        cert.signature = self._sign(cert)
        return cert

    # ── certify ───────────────────────────────────────────────────────────────
    def certify(self, type: str, **params) -> Certificate:
        """
        Issue a certificate for a cryptographic parameter set.

        Args:
            type: "rsa" | "ecc" | "dh" | "prime"
            **params:
                rsa:   key_bits (int)
                ecc:   curve (str, e.g. "P-256", "secp256k1")
                dh:    p_bits (int), q_bits (int)
                prime: n (int)

        Returns:
            Certificate with current proof status and HMAC signature.

        Example:
            api.certify(type="rsa", key_bits=2048)
            api.certify(type="ecc", curve="P-256")
            api.certify(type="dh",  p_bits=3072, q_bits=256)
            api.certify(type="prime", n=2**127-1)
        """
        t = type.lower().strip()

        if t == "rsa":
            key_bits = params.get("key_bits", 2048)
            raw = self._validator.validate_rsa_key(key_bits)
            return self._wrap(
                param_type  = "RSA",
                parameters  = {"key_bits": key_bits},
                assessment  = {
                    "security_bits": raw.security_bits,
                    "compliant":     raw.compliant,
                    "bound_type":    raw.bound_type,
                    "prior_status":  raw.prior_status,
                    "notes":         raw.notes,
                },
            )

        elif t == "ecc":
            curve = params.get("curve", "P-256")
            raw = self._validator.validate_ecc_curve(curve)
            return self._wrap(
                param_type  = "ECC",
                parameters  = raw.parameters,
                assessment  = {
                    "security_bits": raw.security_bits,
                    "compliant":     raw.compliant,
                    "bound_type":    raw.bound_type,
                    "prior_status":  raw.prior_status,
                    "notes":         raw.notes,
                },
            )

        elif t == "dh":
            p_bits = params.get("p_bits", 3072)
            q_bits = params.get("q_bits", 256)
            raw = self._validator.validate_dh_params(p_bits, q_bits)
            return self._wrap(
                param_type  = "DH",
                parameters  = {"p_bits": p_bits, "q_bits": q_bits},
                assessment  = {
                    "security_bits": raw.security_bits,
                    "compliant":     raw.compliant,
                    "bound_type":    raw.bound_type,
                    "prior_status":  raw.prior_status,
                    "notes":         raw.notes,
                },
            )

        elif t == "prime":
            n = params.get("n")
            if n is None:
                raise ValueError("certify(type='prime') requires n=<integer>")
            raw = self._primality.is_prime(n)
            return self._wrap(
                param_type  = "PRIME",
                parameters  = {"n_bits": raw.n_bits, "n_tail": str(n)[-12:]},
                assessment  = {
                    "is_prime":      raw.is_prime,
                    "compliant":     raw.is_prime,
                    "deterministic": raw.deterministic,
                    "method":        raw.method,
                    "witnesses":     raw.witnesses_used[:8],
                    "prior_status":  raw.prior_status,
                    "notes":         raw.notes,
                },
            )

        else:
            raise ValueError(f"Unknown type '{type}'. Use: rsa, ecc, dh, prime")

    # ── audit ─────────────────────────────────────────────────────────────────
    def audit(self, system_name: str = "Unnamed System") -> dict:
        """
        Full system audit across all standard parameter sets.
        Returns a JSON-serialisable dict with all certificates.
        """
        certs = [
            self.certify("rsa", key_bits=2048),
            self.certify("rsa", key_bits=3072),
            self.certify("rsa", key_bits=4096),
            self.certify("ecc", curve="P-256"),
            self.certify("ecc", curve="P-384"),
            self.certify("ecc", curve="secp256k1"),
            self.certify("ecc", curve="Curve25519"),
            self.certify("dh",  p_bits=3072, q_bits=256),
            self.certify("dh",  p_bits=4096, q_bits=256),
        ]

        compliant_count = sum(1 for c in certs if c.assessment.get("compliant"))
        overall = ("COMPLIANT" if compliant_count == len(certs)
                   else "REVIEW_REQUIRED" if compliant_count > 0
                   else "NON-COMPLIANT")

        return {
            "system_name":   system_name,
            "issued":        datetime.now(timezone.utc).isoformat(),
            "issuer":        ISSUER,
            "proof_doi":     PROOF_DOI,
            "proof_status":  PROOF_STATUS,
            "overall":       overall,
            "compliant":     f"{compliant_count}/{len(certs)}",
            "summary": (
                f"{system_name}: {compliant_count}/{len(certs)} parameter sets compliant. "
                f"Proof DOI:{PROOF_DOI} — {PROOF_STATUS}."
            ),
            "certificates":  [json.loads(c.to_json()) for c in certs],
        }

    # ── theorems ──────────────────────────────────────────────────────────────
    def theorems(self) -> list:
        """
        Mathematical reference library.

        Returns theorems connecting prime distribution to cryptographic
        security. Each entry states the theorem, its established status
        in the literature, and its cryptographic relevance.

        These are correctly stated mathematical results. Use as reference
        citations in compliance documentation.
        """
        raw = self._grh.unconditional_theorems()
        out = []
        for t in raw:
            out.append({
                "name":             t.name,
                "statement":        t.statement,
                "established":      t.prior_status,
                "crypto_relevance": t.cryptographic_relevance,
                "reference":        t.reference,
                "doi":              PROOF_DOI,
            })
        return out

    # ── zeros ─────────────────────────────────────────────────────────────────
    def zeros(self, count: int = 10) -> list:
        """
        Numerical zeta zero verification tool.

        Computes the first `count` non-trivial zeros of the Riemann zeta
        function and verifies each lies on Re(s) = 0.5 to high precision
        using mpmath at 50 decimal places.

        This is a numerical verification of specific computed zeros.
        Each result is exact for the zero at that index.
        """
        chi = self._grh.character(q=1, n=0)
        raw = self._grh.l_function_zeros(chi, count=count)
        return [
            {
                "index":            z.zero_index,
                "imaginary_part":   z.imaginary_part,
                "real_part":        z.real_part,
                "on_critical_line": z.on_critical_line,
                "deviation":        z.deviation,
                "precision":        "50 decimal places",
                "method":           "mpmath numerical computation",
                "doi":              PROOF_DOI,
            }
            for z in raw
        ]


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("━" * 65)
    print("  ARTIFACT ZERO — CERTIFICATE API v1.0")
    print(f"  Proof DOI: {PROOF_DOI}")
    print(f"  Proof status: {PROOF_STATUS}")
    print("━" * 65)

    api = CertificateAPI()

    # Single certifications
    print("\n▶ SINGLE CERTIFICATES")
    for call, kwargs in [
        ("rsa",   {"key_bits": 2048}),
        ("rsa",   {"key_bits": 4096}),
        ("ecc",   {"curve": "P-256"}),
        ("ecc",   {"curve": "secp256k1"}),
        ("dh",    {"p_bits": 3072, "q_bits": 256}),
        ("prime", {"n": 2**127 - 1}),
    ]:
        cert = api.certify(type=call, **kwargs)
        print(f"\n{cert.summary()}")

    # Signature verification
    print("\n▶ SIGNATURE VERIFICATION")
    cert = api.certify(type="ecc", curve="P-256")
    valid = cert.verify_signature()
    print(f"  Signature valid: {valid}")
    cert.parameters["curve"] = "TAMPERED"
    valid_after_tamper = cert.verify_signature()
    print(f"  Signature valid after tamper: {valid_after_tamper}")

    # Full audit
    print("\n▶ FULL SYSTEM AUDIT")
    report = api.audit("Demo System v1.0")
    print(f"  Overall: {report['overall']}")
    print(f"  Summary: {report['summary']}")
    print(f"  JSON size: {len(json.dumps(report))} bytes")

    # Theorems
    print("\n▶ THEOREM STATUS")
    for t in api.theorems()[:3]:
        print(f"\n  [{t['name']}]")
        print(f"    Established: {t['established'][:60]}...")
        print(f"    Relevance:   {t['crypto_relevance'][:60]}...")

    # Zeros
    print("\n▶ ZETA ZEROS (first 5)")
    for z in api.zeros(5):
        line = "✓" if z["on_critical_line"] else "✗"
        print(f"  #{z['index']:2d}: Im={z['imaginary_part']:.8f}  Re={z['real_part']:.6f}  {line}")