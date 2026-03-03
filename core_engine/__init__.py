"""core_engine — Three-Engine Architecture

Detection: finds signals, returns positions and types
Scoring: consumes detection maps, produces scores through multiple lenses
Storage: persists everything with timestamps, detects changes

Usage:
    from core_engine.detection import detect_all, detect_paragraphs
    from core_engine.scoring import score_composite, score_paragraphs
    from core_engine.storage import detect_changes, store_paragraph_scores, init_storage_tables
"""

from core_engine.detection import detect_all, detect_paragraphs
from core_engine.scoring import score_composite, score_paragraphs, score_nii, score_nti, score_csi, score_hcs
from core_engine.storage import (
    init_storage_tables, store_snapshot, get_latest_snapshot,
    detect_changes, store_paragraph_scores, store_page_scores,
    store_stock_price, get_stock_history,
)
from core_engine.nti_signals import detect_signals
