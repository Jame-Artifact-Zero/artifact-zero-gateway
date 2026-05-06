"""
az_rh_toolkit/enhancements/bootstrap_prime.py
================================================
ENHANCEMENT 2 — BOOTSTRAP CONFIDENCE FOR PRIMALITY
Ref #13: Bootstrap error quantification template.

For large primes tested with the Bach bound witnesses, a binary
pass/fail is insufficient. This enhancement generates candidates
in the neighborhood of a target prime, tests each, and reports
the density of primes — a real confidence measure.

For the deterministic witness table range (n < 3.3×10²⁴): result
is exact, bootstrap is not needed.
For the Bach bound range (n ≥ 3.3×10²⁴): bootstrap provides
empirical confidence by sampling the prime density in the
neighborhood.

Usage:
    from az_rh_toolkit.enhancements.bootstrap_prime import BootstrapPrime
    bp = BootstrapPrime()
    result = bp.bootstrap(n, iterations=200)
    result = bp.bootstrap_bits(bits=2048, iterations=200)
"""

import math
import random
import time
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict

from ..system2_primality import PrimalityEngine, WITNESS_TABLES

PROOF_DOI  = "10.5281/zenodo.19581553"
ISSUER     = "Artifact Zero Labs"
TABLE_MAX  = 3_317_044_064_679_887_385_961_981


@dataclass
class BootstrapResult:
    """Bootstrap confidence result for a prime neighborhood."""
    timestamp:          str
    target_n:           str     # str to handle big ints
    target_bits:        int
    target_is_prime:    bool
    iterations:         int
    primes_found:       int
    prime_density:      float   # primes_found / iterations
    expected_density:   float   # 1/ln(n) from prime number theorem
    density_ratio:      float   # actual / expected
    within_table:       bool
    confidence_note:    str
    time_ms:            float

    def to_json(self, indent=2) -> str:
        return json.dumps(asdict(self), indent=indent)

    def summary(self) -> str:
        table_note = "deterministic (within witness table)" if self.within_table \
                     else "empirical confidence (Bach bound range)"
        lines = [
            f"━━━ Bootstrap Prime Confidence ━━━",
            f"  Target:         ...{self.target_n[-12:]} ({self.target_bits} bits)",
            f"  Target prime:   {self.target_is_prime}",
            f"  Method:         {table_note}",
            f"  Iterations:     {self.iterations}",
            f"  Primes found:   {self.primes_found}",
            f"  Prime density:  {self.prime_density:.4f} (actual)",
            f"  Expected 1/ln:  {self.expected_density:.4f} (prime number theorem)",
            f"  Density ratio:  {self.density_ratio:.3f}x expected",
            f"  Time:           {self.time_ms:.1f}ms",
            f"  Note:           {self.confidence_note}",
        ]
        return "\n".join(lines)


class BootstrapPrime:
    """
    Bootstrap confidence for primality testing.

    Samples the prime density in the neighborhood of a target number.
    Reports how that density compares to the prime number theorem
    prediction 1/ln(n).

    For numbers within the deterministic witness table range,
    the result is exact — bootstrap adds context, not correction.
    For numbers in the Bach bound range, bootstrap provides
    empirical confidence.
    """

    def __init__(self, precision: int = 50):
        self.engine = PrimalityEngine(precision)
        self.rng    = random.SystemRandom()

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _expected_density(self, n: int) -> float:
        """Prime number theorem: density near n ≈ 1/ln(n)."""
        ln_n = math.log(n)
        return 1.0 / ln_n if ln_n > 0 else 0.0

    def _sample_neighborhood(self, n: int, iterations: int) -> int:
        """
        Sample odd numbers in the neighborhood of n.
        Neighborhood: [n - iterations, n + iterations], odd numbers only.
        Returns count of primes found.
        """
        primes = 0
        # Sample odd offsets centered on n
        offsets = list(range(-iterations * 2, iterations * 2 + 1, 2))
        self.rng.shuffle(offsets)
        tested = 0
        for offset in offsets:
            if tested >= iterations:
                break
            candidate = n + offset
            if candidate < 2:
                continue
            if candidate % 2 == 0:
                continue
            result = self.engine.is_prime(candidate)
            if result.is_prime:
                primes += 1
            tested += 1
        return primes

    def bootstrap(self, n: int, iterations: int = 200) -> BootstrapResult:
        """
        Bootstrap confidence for a specific integer n.

        Args:
            n:          The integer to analyze.
            iterations: Number of neighborhood samples (default 200).

        Returns:
            BootstrapResult with prime density and comparison to
            prime number theorem expectation.
        """
        t0 = time.perf_counter()

        # Test target
        target_result  = self.engine.is_prime(n)
        within_table   = n < TABLE_MAX
        expected       = self._expected_density(n)

        if within_table:
            # Within table: result is deterministic. Bootstrap still
            # characterizes the neighborhood density.
            confidence_note = (
                f"Target is within deterministic witness table range. "
                f"Primality result is exact. "
                f"Bootstrap characterizes neighborhood density only."
            )
            primes_found  = self._sample_neighborhood(n, iterations)
            prime_density = primes_found / iterations
        else:
            # Bach bound range: bootstrap adds confidence
            confidence_note = (
                f"Target is in Bach bound range (n > {str(TABLE_MAX)[:10]}...). "
                f"Result uses {len(self.engine._select_witnesses(n)[0])} witnesses. "
                f"Bootstrap samples neighborhood density for context."
            )
            primes_found  = self._sample_neighborhood(n, iterations)
            prime_density = primes_found / iterations

        density_ratio = (prime_density / expected) if expected > 0 else 0.0
        elapsed       = (time.perf_counter() - t0) * 1000

        return BootstrapResult(
            timestamp         = self._timestamp(),
            target_n          = str(n),
            target_bits       = n.bit_length(),
            target_is_prime   = target_result.is_prime,
            iterations        = iterations,
            primes_found      = primes_found,
            prime_density     = round(prime_density, 6),
            expected_density  = round(expected, 6),
            density_ratio     = round(density_ratio, 4),
            within_table      = within_table,
            confidence_note   = confidence_note,
            time_ms           = round(elapsed, 1),
        )

    def bootstrap_bits(self, bits: int,
                       iterations: int = 200) -> BootstrapResult:
        """
        Generate a prime of the given bit length and bootstrap it.

        Args:
            bits:       Desired prime bit length.
            iterations: Bootstrap iterations.

        Returns:
            BootstrapResult for the generated prime.
        """
        from ..system2_primality import GeneratedPrime
        gp = self.engine.generate_prime(bits)
        return self.bootstrap(gp.p, iterations)

    def batch_bootstrap(self, candidates: list,
                        iterations: int = 100) -> list:
        """Bootstrap a list of candidate integers."""
        return [self.bootstrap(n, iterations) for n in candidates]


if __name__ == "__main__":
    bp = BootstrapPrime()

    print("━" * 60)
    print("BOOTSTRAP PRIME CONFIDENCE")
    print("━" * 60)

    # Known primes at different sizes
    test_cases = [
        (2**31 - 1,  "M31 — within table, known prime"),
        (2**61 - 1,  "M61 — within table, known prime"),
        (2**127 - 1, "M127 — Bach range, known prime"),
    ]

    for n, label in test_cases:
        print(f"\n{label}")
        r = bp.bootstrap(n, iterations=100)
        print(r.summary())

    print(f"\n{'━'*60}")
    print("GENERATE AND BOOTSTRAP 256-BIT PRIME")
    print("━" * 60)
    r = bp.bootstrap_bits(256, iterations=100)
    print(r.summary())
