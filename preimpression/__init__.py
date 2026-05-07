"""
preimpression
=============
Multi-body-part DICOM pre-impression pipeline.

Public API:

    from preimpression import (
        run_pipeline,                # standalone: zip in → result dict out
        run_pipeline_from_series,    # in-memory: pre-loaded series → result dict
        merge_into_result,           # STEP 7 helper: fold result into existing pipeline
        run_preimpression_step,      # safe-by-default STEP 7 entry point
        get_analyzer,                # registry lookup
        supported_body_parts,        # registered codes
    )

    # Flask blueprint:
    from preimpression.server import preimpression_bp
    app.register_blueprint(preimpression_bp)
"""
from .pipeline import run_pipeline, run_pipeline_from_series
from .merge import (
    merge_into_result, run_preimpression_step,
    PREIMP_SEQ_KEEP_ADDITIONS, PREIMP_SEQ_DETAIL_ONLY,
)
from .analyzers import (
    get_analyzer, supported_body_parts, ANALYZERS,
    BaseAnalyzer, max_severity,
)

__all__ = [
    'run_pipeline',
    'run_pipeline_from_series',
    'merge_into_result',
    'run_preimpression_step',
    'PREIMP_SEQ_KEEP_ADDITIONS',
    'PREIMP_SEQ_DETAIL_ONLY',
    'get_analyzer',
    'supported_body_parts',
    'ANALYZERS',
    'BaseAnalyzer',
    'max_severity',
]
