"""
analyzers/__init__.py
=====================
Body-part analyzer registry and dispatch.

Public API:
    from analyzers import get_analyzer, ANALYZERS
    a = get_analyzer('CSPINE')   # or 'BRAIN', 'TSPINE', 'LSPINE'
    result = a.analyze(series_list, work_dir=...)

The dispatch is data-driven: each analyzer module declares
`body_part_codes` and a class. The registry is built at import time.
"""
from __future__ import annotations
from typing import Optional

from ._base import (
    BaseAnalyzer, max_severity, classify_orientation, slice_z_center,
    load_slice, load_volume, group_series, detect_body_part,
)
from .cspine import CSpineAnalyzer
from .tspine import TSpineAnalyzer
from .lspine import LSpineAnalyzer
from .brain import BrainAnalyzer
from .knee import KneeAnalyzer
from .ankle import AnkleAnalyzer
from .foot import FootAnalyzer
from .shoulder import ShoulderAnalyzer
from .elbow import ElbowAnalyzer
from .wrist import WristAnalyzer
from .hand import HandAnalyzer
from .breast import BreastAnalyzer


ANALYZERS = {
    'cervical_spine': CSpineAnalyzer,
    'thoracic_spine': TSpineAnalyzer,
    'lumbar_spine':   LSpineAnalyzer,
    'brain':          BrainAnalyzer,
    'knee':           KneeAnalyzer,
    'ankle':          AnkleAnalyzer,
    'foot':           FootAnalyzer,
    'shoulder':       ShoulderAnalyzer,
    'elbow':          ElbowAnalyzer,
    'wrist':          WristAnalyzer,
    'hand':           HandAnalyzer,
    'breast':         BreastAnalyzer,
}

# Map common body-part codes to analyzer classes
_CODE_TO_ANALYZER = {}
for cls in (CSpineAnalyzer, TSpineAnalyzer, LSpineAnalyzer, BrainAnalyzer,
            KneeAnalyzer, AnkleAnalyzer, FootAnalyzer, ShoulderAnalyzer,
            ElbowAnalyzer, WristAnalyzer, HandAnalyzer, BreastAnalyzer):
    for code in cls.body_part_codes:
        _CODE_TO_ANALYZER[code.upper()] = cls


# Body parts whose detection algorithms have NOT been validated against
# real radiologist-reported studies. Returns None from get_analyzer so
# run_pipeline emits UNVALIDATED_BODY_PART instead of misleading flags.
#
# Remove a body part from this set ONLY when:
#   1. Detection validated against >= 5 real reported studies
#   2. False-positive rate acceptable to clinical stakeholder
#   3. False-negative rate characterized and documented
#   4. DB threshold tuning happened DURING validation
UNVALIDATED_BODY_PARTS = {
    'thoracic_spine',
    'lumbar_spine',
    'brain',
    'knee', 'ankle', 'foot', 'shoulder', 'elbow', 'wrist', 'hand',
    'breast',
}
# Currently validated: cervical_spine (1 real study, Philips; GE regression
# under investigation in hf06)


def get_analyzer(body_part_or_code: str) -> Optional[BaseAnalyzer]:
    """Return an analyzer instance for the given body-part code or label.
    Returns None if no analyzer matches OR if the analyzer is gated as
    UNVALIDATED."""
    if not body_part_or_code:
        return None
    key = body_part_or_code.strip().upper()

    cls = _CODE_TO_ANALYZER.get(key)
    if cls is None:
        for label, klass in ANALYZERS.items():
            if label.upper() == key:
                cls = klass
                break

    if cls is None:
        return None

    # Gate unvalidated body parts — return None so pipeline emits
    # UNVALIDATED_BODY_PART instead of running broken detection
    if cls.body_part_label in UNVALIDATED_BODY_PARTS:
        return None

    return cls()


def supported_body_parts():
    """List all body-part codes the registry recognizes."""
    return sorted(set(_CODE_TO_ANALYZER.keys()) | set(ANALYZERS.keys()))


__all__ = [
    'get_analyzer', 'supported_body_parts', 'ANALYZERS',
    'BaseAnalyzer', 'max_severity', 'classify_orientation',
    'slice_z_center', 'load_slice', 'load_volume',
    'group_series', 'detect_body_part',
]
