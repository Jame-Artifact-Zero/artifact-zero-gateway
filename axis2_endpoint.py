"""
axis2_endpoint.py
-----------------
E04 endpoint handler (Flask-agnostic).

In app.py:
  @app.route('/nti-friction', methods=['POST'])
  def nti_friction():
      return jsonify(axis2_endpoint.handle_request(request.get_json(force=True)))
"""

from typing import Dict, Any
from axis2_adapter import compute_axis2


def handle_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = (payload.get("text") or payload.get("input") or payload.get("message") or "").strip()
    return compute_axis2(text)
