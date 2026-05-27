"""
analyzers/__init__.py
=====================
Body-part analyzer registry and dispatch.

Public API:
    from analyzers import get_analyzer, ANALYZERS
    a = get_analyzer('CSPINE')   # or 'BRAIN', 'TSPINE', 'LSPINE'
    result = a.analyze(series_list, work_dir=...)

Routing strategy:
    The dispatcher is data-driven: each analyzer module declares
    `body_part_codes` and a class. The registry is built at import time.

    Routing happens in three stages:

    1. Exact match against _CODE_TO_ANALYZER (uppercase keys: 'CSPINE',
       'CERVICAL_SPINE_V7', 'BRAIN', etc.).
    2. Exact match against ANALYZERS (lowercase label keys: 'cervical_spine',
       'cervical_spine_v7', 'brain', etc.), case-insensitive.
    3. Substring match (case-insensitive) against _SUBSTRING_RULES - catches
       vendor-prefixed body-part strings like 'SMG MRI CERVICAL' that don't
       exact-match anything in the registry but obviously route to CSPINE.
       Defensive routing: stays correct regardless of what
       detect_body_part_from_dicom returns.

    The keyword list and ordering match detect_body_part_from_dicom so the
    two routers agree on every input. CERVICAL->CSPINE, LUMBAR->LSPINE,
    THORACIC->TSPINE, BRAIN->BRAIN (and BRAIN keywords HEAD, NEURO).
    Opt-in codes (e.g. CERVICAL_SPINE_V7, CERVICAL_SPINE_K7) only match
    exact stage 1/2 so they cannot be hijacked by substring routing on the
    bare keyword.
"""
from __future__ import annotations
from typing import Optional

from ._base import (
    BaseAnalyzer, max_severity, classify_orientation, slice_z_center,
    load_slice, load_volume, group_series, detect_body_part,
)
from .cspine import CSpineAnalyzer
from .cervical_spine_v7 import CSpineV7Analyzer
from .cspine_k7_analyzer import CSpineK7Analyzer
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
    'cervical_spine':    CSpineAnalyzer,
    'cervical_spine_v7': CSpineV7Analyzer,
    'cervical_spine_k7': CSpineK7Analyzer,
    'thoracic_spine':    TSpineAnalyzer,
    'lumbar_spine':      LSpineAnalyzer,
    'brain':             BrainAnalyzer,
    'knee':              KneeAnalyzer,
    'ankle':             AnkleAnalyzer,
    'foot':              FootAnalyzer,
    'shoulder':          ShoulderAnalyzer,
    'elbow':             ElbowAnalyzer,
    'wrist':             WristAnalyzer,
    'hand':              HandAnalyzer,
    'breast':            BreastAnalyzer,
}

# Map common body-part codes to analyzer classes
_CODE_TO_ANALYZER = {}
for cls in (CSpineAnalyzer, CSpineV7Analyzer, CSpineK7Analyzer,
            TSpineAnalyzer, LSpineAnalyzer, BrainAnalyzer,
            KneeAnalyzer, AnkleAnalyzer, FootAnalyzer, ShoulderAnalyzer,
            ElbowAnalyzer, WristAnalyzer, HandAnalyzer, BreastAnalyzer):
    for code in cls.body_part_codes:
        _CODE_TO_ANALYZER[code.upper()] = cls


# Substring routing rules - ordered, first-match wins. Keys are uppercase
# keyword fragments to search inside any body-part string. Values are the
# canonical body-part code that drives _CODE_TO_ANALYZER lookup.
#
# These match the keyword set used by dicom_processor_api.detect_body_part_from_dicom
# so the two routers agree on every input. If a vendor prefixes their body
# part string ('SMG MRI CERVICAL', 'GE NEURO HEAD', 'IDX LUMBAR SPINE WWO'),
# substring routing catches it here too, defensively.
_SUBSTRING_RULES = [
    # Spine
    (('CSPINE', 'C-SPINE', 'C SPINE', 'CERVICAL'), 'CSPINE'),
    (('LSPINE', 'L-SPINE', 'L SPINE', 'LUMBAR'),   'LSPINE'),
    (('TSPINE', 'T-SPINE', 'T SPINE', 'THORACIC'), 'TSPINE'),
    # Head / brain
    (('BRAIN', 'HEAD', 'NEURO'),                   'BRAIN'),
    # Joints / extremities
    (('KNEE',),                                    'KNEE'),
    (('ANKLE', 'HINDFOOT'),                        'ANKLE'),
    (('WRIST', 'CARPAL'),                          'WRIST'),
    (('FOOT', 'FOREFOOT', 'PLANTAR'),              'FOOT'),
    (('ELBOW', 'CUBITAL'),                         'ELBOW'),
    (('SHOULDER',),                                'SHOULDER'),
    (('HAND', 'METACARPAL'),                       'HAND'),
    (('BREAST',),                                  'BREAST'),
]


def _route_by_substring(key_upper: str) -> Optional[str]:
    """Return a canonical body-part code matched by substring, or None.

    `key_upper` must be uppercased and stripped already. Returns the first
    matching canonical code (e.g. 'CSPINE') so the caller can lookup
    _CODE_TO_ANALYZER[code] to get the analyzer class.
    """
    if not key_upper:
        return None
    for keywords, canonical_code in _SUBSTRING_RULES:
        for kw in keywords:
            if kw in key_upper:
                return canonical_code
    return None


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
    # brain removed - v3 algorithm validated on phantom (9/9 tests passing)
    # real-data validation pending post-deploy on GE FLAIR study
    'knee', 'ankle', 'foot', 'shoulder', 'elbow', 'wrist', 'hand',
    'breast',
}
# Currently validated: cervical_spine (1 real study, Philips; GE regression
# under investigation in hf06)
# cervical_spine_v7 is opt-in via explicit ?body_part=cervical_spine_v7 and
# is NOT in UNVALIDATED_BODY_PARTS so that opt-in routing reaches it.
# cervical_spine_k7 is opt-in via explicit ?body_part=cervical_spine_k7 and
# is NOT in UNVALIDATED_BODY_PARTS so that opt-in routing reaches it.


def get_analyzer(body_part_or_code: str) -> Optional[BaseAnalyzer]:
    """Return an analyzer instance for the given body-part code or label.
    Returns None if no analyzer matches OR if the analyzer is gated as
    UNVALIDATED.

    Three-stage lookup:
      1. Exact match in _CODE_TO_ANALYZER (uppercase codes).
      2. Exact match in ANALYZERS (lowercase labels, case-insensitive).
      3. Substring match against _SUBSTRING_RULES - catches vendor-prefixed
         body-part strings like 'SMG MRI CERVICAL'.

    Opt-in routing (e.g. 'cervical_spine_v7', 'cervical_spine_k7') only
    matches stage 1 or 2 by exact code/label so it never gets auto-selected
    via substring.
    """
    if not body_part_or_code:
        return None
    key = body_part_or_code.strip().upper()

    # Stage 1 - exact code
    cls = _CODE_TO_ANALYZER.get(key)

    # Stage 2 - exact label (case-insensitive)
    if cls is None:
        for label, klass in ANALYZERS.items():
            if label.upper() == key:
                cls = klass
                break

    # Stage 3 - substring routing for vendor-prefixed bp strings.
    # Only applied when the key is NOT a known exact code/label, so opt-in
    # codes like CERVICAL_SPINE_V7 and CERVICAL_SPINE_K7 cannot be hijacked
    # by 'CERVICAL' substring.
    if cls is None:
        canonical = _route_by_substring(key)
        if canonical is not None:
            cls = _CODE_TO_ANALYZER.get(canonical)

    if cls is None:
        return None

    # Gate unvalidated body parts - return None so pipeline emits
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
