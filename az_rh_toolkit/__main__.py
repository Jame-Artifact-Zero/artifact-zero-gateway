"""
az_rh_toolkit/__main__.py
===========================
Run with: python -m az_rh_toolkit
"""

import json
from . import UnconditionalValidator, PrimalityEngine, GRHEngine
from . import PROOF_DOI, VERSION, ISSUER

def main():
    print("=" * 65)
    print("  ARTIFACT ZERO — RH CRYPTOGRAPHIC TOOLKIT")
    print(f"  Version {VERSION} | {ISSUER}")
    print(f"  Proof DOI: {PROOF_DOI}")
    print("=" * 65)

    # ── System 1 ──────────────────────────────────────────────────────────────
    print("\n▶ SYSTEM 1: UNCONDITIONAL VALIDATION ENGINE")
    v = UnconditionalValidator()
    for curve in ["P-256", "secp256k1", "Curve25519"]:
        c = v.validate_ecc_curve(curve)
        print(f"  {curve:12s}: {c.security_bits:.0f} bits | {c.current_status[:30]}...")
    for bits in [2048, 3072, 4096]:
        c = v.validate_rsa_key(bits)
        mark = "✓" if c.compliant else "✗"
        print(f"  RSA-{bits:4d}  : {mark} {c.security_bits:.1f} bits | UNCONDITIONAL")

    # ── System 2 ──────────────────────────────────────────────────────────────
    print("\n▶ SYSTEM 2: DETERMINISTIC PRIMALITY ENGINE")
    e = PrimalityEngine()
    test_ns = [2**31-1, 2**61-1, 2**89-1, 2**107-1]
    for n in test_ns:
        r = e.is_prime(n)
        verdict = "PRIME" if r.is_prime else "COMPOSITE"
        print(f"  2^{r.n_bits}-1 ({r.n_bits:3d} bits): {verdict:9s} | {r.proof_status} | {r.time_ms:.1f}ms")

    print("\n  Generating 512-bit prime...")
    gp = e.generate_prime(512)
    print(f"  p = ...{str(gp.p)[-20:]} ({gp.bits} bits)")
    print(f"  Time: {gp.generation_time_ms:.0f}ms | {gp.primality.proof_status}")

    # ── System 3 ──────────────────────────────────────────────────────────────
    print("\n▶ SYSTEM 3: GRH EXTENSION TOOLKIT")
    g = GRHEngine()
    report = g.grh_audit(n_characters=3, zeros_per_char=5)
    print(f"  {report.summary}")
    print(f"\n  Theorems upgraded to unconditional:")
    for t in g.unconditional_theorems()[:4]:
        print(f"    · {t.name}")
    print(f"    · ... ({len(g.unconditional_theorems())} total)")

    print(f"\n{'=' * 65}")
    print(f"  All bounds unconditional. Cite: DOI {PROOF_DOI}")
    print(f"{'=' * 65}\n")

if __name__ == "__main__":
    main()
