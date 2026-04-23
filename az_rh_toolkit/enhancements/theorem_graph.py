"""
az_rh_toolkit/enhancements/theorem_graph.py
=============================================
ENHANCEMENT 3 — THEOREM DEPENDENCY GRAPH
Ref #4: Tier structure / dependency mapping.

The seven theorems in GRHEngine are not independent — they have
a dependency order. GNFS depends on prime distribution in
progressions, which depends on Chebotarev, which depends on GRH.
When proof status changes, knowing which theorem to update first
matters.

This module maps the dependency graph explicitly and provides:
- Topological sort (update order when proof status changes)
- Impact analysis (if theorem X becomes unconditional, what else upgrades)
- Critical path (which theorems are deepest dependencies)

Usage:
    from az_rh_toolkit.enhancements.theorem_graph import TheoremGraph
    tg = TheoremGraph()
    order = tg.update_order()
    impact = tg.impact_of("Bach Bound (Miller-Rabin)")
    path = tg.critical_path()
    report = tg.full_graph()
"""

import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional


PROOF_DOI = "10.5281/zenodo.19581553"
ISSUER    = "Artifact Zero Labs"


# ── Theorem dependency definitions ────────────────────────────────────────────
# Each theorem lists what it depends on (must be true first).
# GRH is the root — everything else depends on it.

THEOREM_DEPS = {
    "GRH_ROOT": {
        "name":        "Generalized Riemann Hypothesis",
        "depends_on":  [],   # root node
        "description": "All non-trivial zeros of all Dirichlet L-functions L(s,χ) "
                        "lie on Re(s) = 1/2. Proved by Houghton (2026) extension "
                        "of the Mayer operator chain. Submitted: DOI " + PROOF_DOI,
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "Foundation. Everything below depends on this.",
    },
    "Chebotarev Density Theorem — Effective Version": {
        "name":        "Chebotarev Density Theorem — Effective Version",
        "depends_on":  ["GRH_ROOT"],
        "description": "Effective error bound on prime splitting in Galois extensions. "
                        "Lagarias-Odlyzko 1977. GRH-conditional.",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "Primality proving (ECPP), class group computations, "
                            "isogeny-based cryptography (CSIDH, SQISign).",
    },
    "Least Prime in Arithmetic Progression (GRH bound)": {
        "name":        "Least Prime in Arithmetic Progression (GRH bound)",
        "depends_on":  ["GRH_ROOT"],
        "description": "Least prime p ≡ a (mod q) satisfies p = O(q² log² q). "
                        "GRH-conditional tightening of Linnik's L bound.",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "DSA/DH/RSA safe prime generation in arithmetic progressions.",
    },
    "Bombieri-Vinogradov on Average — Extended Range": {
        "name":        "Bombieri-Vinogradov on Average — Extended Range",
        "depends_on":  ["GRH_ROOT"],
        "description": "Extended range Q > x^{1/2} for Bombieri-Vinogradov "
                        "prime equidistribution. GRH gives pointwise bounds for all q.",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "Average-case security analyses for lattice problems (LWE, SIS).",
    },
    "Elliptic Curve Group Order Distribution": {
        "name":        "Elliptic Curve Group Order Distribution",
        "depends_on":  ["GRH_ROOT", "Chebotarev Density Theorem — Effective Version"],
        "description": "Group order #E(F_p) equidistributed in Hasse interval. "
                        "Deuring CM theory + Chebotarev for tight distribution.",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "ECC security (secp256k1, P-256, all NIST curves). "
                            "ECDLP hardness depends on near-prime group orders.",
    },
    "GNFS Complexity for Discrete Logarithm": {
        "name":        "GNFS Complexity for Discrete Logarithm",
        "depends_on":  ["GRH_ROOT", "Least Prime in Arithmetic Progression (GRH bound)"],
        "description": "GNFS subexponential DLP complexity. Distribution of primes "
                        "in progressions used in sieve analysis.",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "DH and DSA security. NIST 3072-bit DH minimum derived "
                            "from this GRH-conditional analysis.",
    },
    "AKS Primality Test — Tight Complexity": {
        "name":        "AKS Primality Test — Tight Complexity",
        "depends_on":  ["GRH_ROOT", "Chebotarev Density Theorem — Effective Version"],
        "description": "AKS tight O((log n)³) complexity bound. GRH-conditional "
                        "tightening of the unconditional O((log n)^{6+ε}).",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "AKS/BPSW used in Sage, Mathematica, certified primality. "
                            "Tighter complexity enables tighter performance guarantees.",
    },
    "Bach Bound (Miller-Rabin)": {
        "name":        "Bach Bound (Miller-Rabin)",
        "depends_on":  ["GRH_ROOT", "Chebotarev Density Theorem — Effective Version"],
        "description": "For composite n, a Miller-Rabin witness exists below 2(ln n)². "
                        "Makes deterministic Miller-Rabin feasible for all n. "
                        "Bach 1990, GRH-conditional.",
        "status":      "SUBMITTED_UNDER_REVIEW",
        "crypto_relevance": "Every prime generation routine in every crypto library "
                            "(OpenSSL, BoringSSL, NSS, LibreSSL). Foundation of "
                            "deterministic primality for cryptographic key generation.",
    },
}

# Tier assignment (0 = root, higher = deeper dependency)
THEOREM_TIERS = {
    "GRH_ROOT":                                               0,
    "Chebotarev Density Theorem — Effective Version":         1,
    "Least Prime in Arithmetic Progression (GRH bound)":      1,
    "Bombieri-Vinogradov on Average — Extended Range":         1,
    "Elliptic Curve Group Order Distribution":                 2,
    "GNFS Complexity for Discrete Logarithm":                 2,
    "AKS Primality Test — Tight Complexity":                  2,
    "Bach Bound (Miller-Rabin)":                              2,
}


@dataclass
class TheoremNode:
    """A theorem with its dependencies and dependents."""
    key:           str
    name:          str
    tier:          int
    depends_on:    list
    dependents:    list   # theorems that depend on this one
    status:        str
    description:   str
    crypto_relevance: str


class TheoremGraph:
    """
    Dependency graph for GRH-conditional cryptographic theorems.

    Provides topological ordering, impact analysis, and critical path.
    When proof status changes, the graph determines what order to
    update theorem statuses and what cascade of upgrades follows.
    """

    def __init__(self):
        self.nodes = self._build_graph()

    def _build_graph(self) -> dict:
        """Build the full graph with both directions (depends_on and dependents)."""
        nodes = {}
        for key, data in THEOREM_DEPS.items():
            nodes[key] = TheoremNode(
                key              = key,
                name             = data["name"],
                tier             = THEOREM_TIERS.get(key, 99),
                depends_on       = data["depends_on"],
                dependents       = [],
                status           = data["status"],
                description      = data["description"],
                crypto_relevance = data["crypto_relevance"],
            )
        # Build reverse edges (dependents)
        for key, node in nodes.items():
            for dep in node.depends_on:
                if dep in nodes:
                    nodes[dep].dependents.append(key)
        return nodes

    def update_order(self) -> list:
        """
        Topological sort: the order in which theorem statuses should
        be updated when proof verification arrives.

        Tier 0 (GRH root) updates first. Tier 2 updates last.
        Within a tier, alphabetical order.
        """
        by_tier = {}
        for key, node in self.nodes.items():
            t = node.tier
            if t not in by_tier:
                by_tier[t] = []
            by_tier[t].append(node)

        ordered = []
        for tier in sorted(by_tier.keys()):
            tier_nodes = sorted(by_tier[tier], key=lambda n: n.name)
            for node in tier_nodes:
                ordered.append({
                    "tier":   tier,
                    "key":    node.key,
                    "name":   node.name,
                    "status": node.status,
                    "update_note": (
                        "Update first — root dependency"
                        if tier == 0 else
                        f"Update after tier {tier-1} theorems are confirmed"
                    ),
                })
        return ordered

    def impact_of(self, theorem_key: str) -> dict:
        """
        Impact analysis: if this theorem becomes unconditional,
        what cascade of other theorems also upgrades?

        Returns all theorems downstream in the dependency graph.
        """
        if theorem_key not in self.nodes:
            # Try matching by name
            for k, n in self.nodes.items():
                if theorem_key in n.name:
                    theorem_key = k
                    break
            else:
                raise ValueError(f"Theorem not found: {theorem_key}")

        node = self.nodes[theorem_key]

        # BFS to find all downstream theorems
        downstream = set()
        queue = list(node.dependents)
        while queue:
            dep_key = queue.pop(0)
            if dep_key not in downstream:
                downstream.add(dep_key)
                queue.extend(self.nodes[dep_key].dependents)

        downstream_nodes = [self.nodes[k] for k in downstream]
        downstream_nodes.sort(key=lambda n: (n.tier, n.name))

        return {
            "theorem":          node.name,
            "tier":             node.tier,
            "if_confirmed":     "status → UNCONDITIONAL",
            "downstream_count": len(downstream_nodes),
            "downstream": [
                {"name": n.name, "tier": n.tier,
                 "crypto_relevance": n.crypto_relevance[:80] + "..."}
                for n in downstream_nodes
            ],
            "note": (
                f"If '{node.name}' is confirmed unconditional, "
                f"{len(downstream_nodes)} downstream theorem(s) also upgrade."
            ),
        }

    def critical_path(self) -> list:
        """
        The critical path: theorems with the most downstream dependents.
        These are highest priority to confirm — they unlock the most.
        """
        path = []
        for key, node in self.nodes.items():
            # Count all downstream recursively
            downstream = set()
            queue = list(node.dependents)
            while queue:
                dep_key = queue.pop(0)
                if dep_key not in downstream:
                    downstream.add(dep_key)
                    queue.extend(self.nodes[dep_key].dependents)
            path.append({
                "key":              key,
                "name":             node.name,
                "tier":             node.tier,
                "downstream_count": len(downstream),
                "crypto_relevance": node.crypto_relevance[:80] + "...",
            })
        path.sort(key=lambda x: (-x["downstream_count"], x["tier"]))
        return path

    def full_graph(self) -> dict:
        """Complete graph report: all nodes, edges, tiers, update order."""
        return {
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "issuer":       ISSUER,
            "proof_doi":    PROOF_DOI,
            "total_nodes":  len(self.nodes),
            "update_order": self.update_order(),
            "critical_path": self.critical_path(),
            "impact_of_grh": self.impact_of("GRH_ROOT"),
            "nodes": [
                {
                    "key":             n.key,
                    "name":            n.name,
                    "tier":            n.tier,
                    "status":          n.status,
                    "depends_on":      n.depends_on,
                    "dependents":      n.dependents,
                    "crypto_relevance": n.crypto_relevance,
                }
                for n in sorted(self.nodes.values(), key=lambda x: (x.tier, x.name))
            ],
        }


if __name__ == "__main__":
    tg = TheoremGraph()

    print("━" * 60)
    print("THEOREM DEPENDENCY GRAPH")
    print("━" * 60)

    print("\n▶ UPDATE ORDER (when proof verified)")
    for item in tg.update_order():
        indent = "  " * item["tier"]
        print(f"  {indent}Tier {item['tier']}: {item['name']}")
        print(f"  {indent}  → {item['update_note']}")

    print(f"\n▶ CRITICAL PATH (most downstream impact)")
    for item in tg.critical_path():
        print(f"  {item['name']}: {item['downstream_count']} downstream theorems")

    print(f"\n▶ IMPACT OF GRH CONFIRMATION")
    impact = tg.impact_of("GRH_ROOT")
    print(f"  {impact['note']}")
    for d in impact["downstream"]:
        print(f"    Tier {d['tier']}: {d['name']}")

    print(f"\n▶ IMPACT OF BACH BOUND CONFIRMATION")
    impact2 = tg.impact_of("Bach Bound (Miller-Rabin)")
    print(f"  {impact2['note']}")
