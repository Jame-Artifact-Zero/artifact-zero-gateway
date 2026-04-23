"""
az_rh_toolkit/__init__.py
===========================
ARTIFACT ZERO — RH CRYPTOGRAPHIC TOOLKIT
Version 1.0.0 | April 2026
DOI: 10.5281/zenodo.19581553

Three systems, one import:

    from az_rh_toolkit import UnconditionalValidator, PrimalityEngine, GRHEngine

System 1 — UnconditionalValidator
    Validates cryptographic parameters (RSA, ECC, DH) against
    unconditional RH/GRH bounds. Produces audit-ready certificates.
    Prior: GRH-conditional (Bach 1990). Now: unconditional (Houghton 2026).

System 2 — PrimalityEngine
    Deterministic Miller-Rabin primality testing with provenance certificates.
    Includes prime generation, batch verification, and re-certification
    of existing primes to unconditional status.

System 3 — GRHEngine
    GRH extension: Dirichlet L-function zeros, primes in progressions,
    prime counting error bounds, and database of theorems upgraded from
    GRH-conditional to unconditional by Houghton (2026).

Quick start:
    python -m az_rh_toolkit
"""

from .system1_validation import UnconditionalValidator, ValidationCertificate, AuditReport
from .system2_primality   import PrimalityEngine, PrimalityResult, GeneratedPrime
from .system3_grh         import GRHEngine, GRHAuditReport, GRHTheorem

PROOF_DOI    = "10.5281/zenodo.19581553"
PROOF_AUTHOR = "Houghton, Jame"
PROOF_DATE   = "2026-04-14"
VERSION      = "1.0.0"
ISSUER       = "Artifact Zero Labs"

__all__ = [
    "UnconditionalValidator", "ValidationCertificate", "AuditReport",
    "PrimalityEngine", "PrimalityResult", "GeneratedPrime",
    "GRHEngine", "GRHAuditReport", "GRHTheorem",
    "PROOF_DOI", "PROOF_AUTHOR", "PROOF_DATE", "VERSION", "ISSUER",
]
