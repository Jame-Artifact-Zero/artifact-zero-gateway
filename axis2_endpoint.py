"""
axis2_endpoint.py
-----------------
E04 endpoint handler (Flask-agnostic).
Updated to use axis2_friction.py (standalone module with full V2/V3 support).

In app.py:
  @app.route('/nti-friction', methods=['POST'])
  def nti_friction():
      return jsonify(axis2_endpoint.handle_request(request.get_json(force=True)))
"""

from typing import Dict, Any

try:
    from axis2_friction import analyze_friction, apply_axis2_v2, apply_axis2_v3
except ImportError:
    analyze_friction = None
    apply_axis2_v2 = None
    apply_axis2_v3 = None

try:
    from axis2_adapter import compute_axis2
except ImportError:
    compute_axis2 = None


def handle_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = (payload.get("text") or payload.get("input") or payload.get("message") or "").strip()
    mode = (payload.get("mode") or payload.get("axis2_mode") or "OBSERVE").upper()
    direction = (payload.get("direction") or "analyze").lower()

    # Prefer new axis2_friction module
    if analyze_friction:
        if direction == "v2":
            return apply_axis2_v2(text, mode)
        elif direction == "v3":
            return apply_axis2_v3(text, mode)
        else:
            return analyze_friction(text)

    # Fallback to old adapter
    if compute_axis2:
        return compute_axis2(text)

    return {"friction_score": 0.0, "triggers": [], "axis": 2, "status": "no_axis2_engine"}
