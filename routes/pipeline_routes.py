"""Expose the pipeline trigger route as a Flask blueprint."""

from flask import Blueprint, jsonify, request

from pipeline.orchestrator import run_pipeline

pipeline_bp = Blueprint("pipeline", __name__)


@pipeline_bp.route("/pipeline/trigger", methods=["POST"])
def trigger_pipeline():
    """Trigger the shared processing pipeline from an HTTP request."""
    event = {
        "json": request.get_json(silent=True) or {},
        "headers": dict(request.headers),
        "method": request.method,
        "path": request.path,
    }
    result = run_pipeline(event)
    return jsonify(result)
