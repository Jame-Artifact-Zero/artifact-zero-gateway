"""
audit_source.py
---------------
I04 — Live feed vs manual audit separation primitives.

This module provides a single function to normalize audit source tags.
You will call it when recording audits/scorings.

Expected values:
- 'manual'
- 'live_feed'

Any other string is normalized to 'manual' unless explicitly allowed.
"""

from typing import Optional


def normalize_audit_source(source: Optional[str]) -> str:
    if not source:
        return "manual"
    s = source.strip().lower()
    if s in ("manual", "live_feed", "live-feed"):
        return "live_feed" if s in ("live_feed", "live-feed") else "manual"
    return "manual"
