"""
axis2_adapter.py
----------------
E03/E04/E05 adapter around existing edge_engine / relational_engine modules.

Default uses:
  from edge_engine import compute_relational_field

Expected return shape (from your handoff):
  { edge_index: 0..1, edge_markers: [...], triggered_patterns: [...] }

This adapter normalizes the output into:
  {
    friction_score: 0..1,
    triggers: [{pattern, phrase, category}?],
    axis: 2
  }
"""

from typing import Dict, Any, List

try:
    from edge_engine import compute_relational_field
except ImportError:
    compute_relational_field = None


def compute_axis2(text: str) -> Dict[str, Any]:
    if not compute_relational_field:
        return {"friction_score": 0.0, "triggers": [], "axis": 2, "status": "missing_edge_engine"}

    raw = compute_relational_field(text) or {}
    triggers = raw.get("triggered_patterns") or []
    # Normalize triggers into dict objects when possible
    norm: List[Dict[str, Any]] = []
    for t in triggers:
        if isinstance(t, dict):
            norm.append(t)
        else:
            norm.append({"pattern": str(t), "phrase": "", "category": "axis2"})
    return {
        "friction_score": float(raw.get("edge_index", 0.0)),
        "triggers": norm,
        "axis": 2
    }
