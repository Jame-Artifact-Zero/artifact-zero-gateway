"""
az_rh_toolkit/runner.py
========================
ARTIFACT ZERO — CONTINUOUS PRIMALITY RUNNER
Version 1.0.0 | April 2026

Continuously generates candidate primes of increasing bit length,
tests each through the primality engine, logs certificates, tracks
performance, and identifies the practical boundary on the host machine.

What this does:
- Generates random candidates at each bit length in the queue
- Tests primality deterministically within verified witness table range
- Logs every result with certificate hash and timing
- Tracks pass rate, average generation time, and throughput
- Writes results to a JSON log file
- Reports the practical performance boundary on this hardware

What this does not do:
- Prove anything about primes outside the tested range
- Run indefinitely without bound (configurable max)

Usage:
    python -m az_rh_toolkit.runner
    python -m az_rh_toolkit.runner --min-bits 128 --max-bits 4096 --per-size 5
    
    or as a library:
    from az_rh_toolkit.runner import ContinuousRunner
    r = ContinuousRunner()
    r.run()
"""

import json
import time
import argparse
import random
import os
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional

from .system2_primality import PrimalityEngine

ISSUER  = "Artifact Zero Labs"
VERSION = "1.0.0"
LOG_FILE = "az_runner_log.json"

# Bit lengths to test in sequence
DEFAULT_BIT_SIZES = [
    128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096
]


# ── Result record ─────────────────────────────────────────────────────────────
@dataclass
class RunRecord:
    timestamp:      str
    bit_length:     int
    candidate_tail: str   # last 12 digits of n
    is_prime:       bool
    deterministic:  bool
    generation_ms:  float
    test_ms:        float
    total_ms:       float
    cert_hash:      str
    witnesses_used: int
    within_table:   bool  # within verified deterministic witness table range


# ── Runner ────────────────────────────────────────────────────────────────────
class ContinuousRunner:
    """
    Continuous prime generation and verification stress tester.

    Runs through a queue of bit lengths, generating and testing
    candidates at each size. Logs all results. Reports performance.
    """

    # Deterministic witness table upper bound
    TABLE_MAX = 3_317_044_064_679_887_385_961_981

    def __init__(self,
                 bit_sizes:   list = None,
                 per_size:    int  = 3,
                 log_file:    str  = LOG_FILE,
                 verbose:     bool = True):
        self.bit_sizes = bit_sizes or DEFAULT_BIT_SIZES
        self.per_size  = per_size
        self.log_file  = log_file
        self.verbose   = verbose
        self.engine    = PrimalityEngine()
        self.rng       = random.SystemRandom()
        self.records   = []
        self.stats     = {}

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _generate_candidate(self, bits: int) -> tuple:
        """Generate a random odd candidate of the given bit length. Returns (n, ms)."""
        t0 = time.perf_counter()
        n = self.rng.getrandbits(bits)
        n |= (1 << (bits - 1))  # ensure correct bit length
        n |= 1                   # ensure odd
        ms = (time.perf_counter() - t0) * 1000
        return n, ms

    def _within_table(self, n: int) -> bool:
        return n < self.TABLE_MAX

    def _run_size(self, bits: int) -> list:
        import math
        """Run per_size tests at a given bit length. Returns list of RunRecord."""
        records = []
        primes_found = 0
        candidates_tested = 0

        if self.verbose:
            print(f"\n  [{bits}-bit] generating {self.per_size} primes...")

        while primes_found < self.per_size:
            n, gen_ms = self._generate_candidate(bits)
            candidates_tested += 1

            t0 = time.perf_counter()
            result = self.engine.is_prime(n)
            test_ms = (time.perf_counter() - t0) * 1000
            total_ms = gen_ms + test_ms

            if result.is_prime:
                primes_found += 1
                rec = RunRecord(
                    timestamp      = self._timestamp(),
                    bit_length     = bits,
                    candidate_tail = str(n)[-12:],
                    is_prime       = True,
                    deterministic  = result.deterministic,
                    generation_ms  = round(gen_ms, 3),
                    test_ms        = round(test_ms, 3),
                    total_ms       = round(total_ms, 3),
                    cert_hash      = result.certificate_hash,
                    witnesses_used = len(result.witnesses_used),
                    within_table   = self._within_table(n),
                )
                records.append(rec)

                if self.verbose:
                    table_flag = "✓ table" if rec.within_table else "~ bach"
                    print(f"    prime #{primes_found}: ...{rec.candidate_tail} "
                          f"| {rec.total_ms:.1f}ms | {table_flag} "
                          f"| {rec.witnesses_used} witnesses")

            # Prime number theorem: ~ln(2^bits) candidates expected per prime
            safety_limit = max(500, int(bits * math.log(2) * 10))
            if candidates_tested > safety_limit:
                if self.verbose:
                    print(f"    NOTE: {candidates_tested} candidates at {bits}-bit, "
                          f"{primes_found} found. Moving on.")
                break

        return records

    def _compute_stats(self) -> dict:
        """Compute performance statistics across all records."""
        if not self.records:
            return {}

        by_size = {}
        for r in self.records:
            if r.bit_length not in by_size:
                by_size[r.bit_length] = []
            by_size[r.bit_length].append(r)

        stats = {}
        for bits, recs in sorted(by_size.items()):
            times = [r.total_ms for r in recs]
            stats[bits] = {
                "count":        len(recs),
                "avg_ms":       round(sum(times) / len(times), 1),
                "min_ms":       round(min(times), 1),
                "max_ms":       round(max(times), 1),
                "within_table": all(r.within_table for r in recs),
                "deterministic": all(r.deterministic for r in recs),
            }
        return stats

    def run(self) -> dict:
        """
        Run the full queue of bit lengths.
        Returns summary report with all records and statistics.
        """
        print("━" * 60)
        print("  ARTIFACT ZERO — CONTINUOUS PRIMALITY RUNNER")
        print(f"  Bit sizes: {self.bit_sizes}")
        print(f"  Primes per size: {self.per_size}")
        print(f"  Witness table max: {str(self.TABLE_MAX)[:10]}... ({len(str(self.TABLE_MAX))} digits)")
        print("━" * 60)

        t_start = time.perf_counter()

        for bits in self.bit_sizes:
            try:
                recs = self._run_size(bits)
                self.records.extend(recs)
            except KeyboardInterrupt:
                print(f"\n  Interrupted at {bits}-bit. Saving results so far.")
                break
            except Exception as e:
                print(f"\n  ERROR at {bits}-bit: {e}. Continuing.")
                continue

        total_ms = (time.perf_counter() - t_start) * 1000
        self.stats = self._compute_stats()

        report = {
            "timestamp":    self._timestamp(),
            "issuer":       ISSUER,
            "version":      VERSION,
            "total_primes": len(self.records),
            "total_ms":     round(total_ms, 1),
            "statistics":   self.stats,
            "records":      [asdict(r) for r in self.records],
        }

        # Write log
        with open(self.log_file, "w") as f:
            json.dump(report, f, indent=2)

        self._print_summary(report, total_ms)
        return report

    def _print_summary(self, report: dict, total_ms: float):
        print(f"\n{'━' * 60}")
        print("  PERFORMANCE SUMMARY")
        print("━" * 60)
        print(f"  Total primes generated: {report['total_primes']}")
        print(f"  Total time: {total_ms/1000:.1f}s")
        print()
        print(f"  {'Bits':>6}  {'Avg ms':>8}  {'Min ms':>8}  {'Max ms':>8}  {'Table':>6}  {'Det':>4}")
        print(f"  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*6}  {'─'*4}")
        for bits, s in report["statistics"].items():
            table = "✓" if s["within_table"] else "~"
            det   = "✓" if s["deterministic"] else "~"
            print(f"  {bits:>6}  {s['avg_ms']:>8.1f}  {s['min_ms']:>8.1f}  "
                  f"{s['max_ms']:>8.1f}  {table:>6}  {det:>4}")
        print()
        print(f"  Log written to: {self.log_file}")
        print(f"  ✓ = within verified deterministic witness table range")
        print(f"  ~ = Bach bound range (larger numbers)")
        print("━" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Artifact Zero Continuous Primality Runner"
    )
    parser.add_argument("--min-bits",  type=int, default=128,
                        help="Minimum bit length (default: 128)")
    parser.add_argument("--max-bits",  type=int, default=4096,
                        help="Maximum bit length (default: 4096)")
    parser.add_argument("--per-size",  type=int, default=3,
                        help="Primes to generate per bit size (default: 3)")
    parser.add_argument("--log-file",  type=str, default=LOG_FILE,
                        help=f"Log file path (default: {LOG_FILE})")
    parser.add_argument("--quiet",     action="store_true",
                        help="Suppress per-prime output")
    args = parser.parse_args()

    # Build bit size queue
    all_sizes = [128, 192, 256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096]
    sizes = [s for s in all_sizes if args.min_bits <= s <= args.max_bits]

    if not sizes:
        print(f"No sizes in range [{args.min_bits}, {args.max_bits}].")
        print(f"Available: {all_sizes}")
        exit(1)

    runner = ContinuousRunner(
        bit_sizes = sizes,
        per_size  = args.per_size,
        log_file  = args.log_file,
        verbose   = not args.quiet,
    )
    runner.run()
