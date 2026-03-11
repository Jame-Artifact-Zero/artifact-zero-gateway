"""
thread_monitor.py
Layer 1 + 2: Record every AI output, track character/token count,
signal when window is approaching capacity.

Window math:
  - Claude context window: 200,000 tokens
  - Rough token estimate: 1 token ≈ 4 chars (conservative)
  - Safe injection threshold: 80% capacity = 160,000 tokens ≈ 640,000 chars
  - Hard limit: 95% = 190,000 tokens ≈ 760,000 chars
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional


# ---------------------------------------------------------------------------
# WINDOW CONFIG
# ---------------------------------------------------------------------------

CHARS_PER_TOKEN = 4
MAX_TOKENS = 200_000
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN          # 800,000

THRESHOLD_WARN  = 0.70   # 70%  — start watching
THRESHOLD_PREP  = 0.80   # 80%  — prepare blob
THRESHOLD_INJECT = 0.90  # 90%  — inject now
THRESHOLD_HARD  = 0.95   # 95%  — hard limit


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# MESSAGE RECORD
# ---------------------------------------------------------------------------

@dataclass
class MessageRecord:
    record_id: str
    source: str          # "ai" or "human"
    content: str         # verbatim, untouched
    char_count: int
    token_estimate: int
    cumulative_chars: int
    cumulative_tokens: int
    window_pct: float    # 0.0 → 1.0
    window_status: str   # NOMINAL / WATCH / PREPARE / INJECT / CRITICAL
    timestamp: str
    injection_triggered: bool = False  # was a blob injected after this message


# ---------------------------------------------------------------------------
# THREAD MONITOR
# ---------------------------------------------------------------------------

class ThreadMonitor:
    """
    Records every message in a thread.
    Tracks cumulative character and token counts.
    Signals when the context window needs a relay injection.
    """

    def __init__(self, thread_id: Optional[str] = None):
        self.thread_id: str = thread_id or str(uuid.uuid4())[:8]
        self.records: List[MessageRecord] = []
        self.cumulative_chars: int = 0
        self.cumulative_tokens: int = 0
        self.injection_count: int = 0
        self.created_at: str = _now()

    def record(self, content: str, source: str) -> MessageRecord:
        """Record a message. Returns the record with window status."""
        chars = len(content)
        tokens = estimate_tokens(content)
        self.cumulative_chars += chars
        self.cumulative_tokens += tokens
        pct = self.cumulative_chars / MAX_CHARS

        if pct >= THRESHOLD_HARD:
            status = "CRITICAL"
        elif pct >= THRESHOLD_INJECT:
            status = "INJECT"
        elif pct >= THRESHOLD_PREP:
            status = "PREPARE"
        elif pct >= THRESHOLD_WARN:
            status = "WATCH"
        else:
            status = "NOMINAL"

        rec = MessageRecord(
            record_id=str(uuid.uuid4())[:8],
            source=source,
            content=content,
            char_count=chars,
            token_estimate=tokens,
            cumulative_chars=self.cumulative_chars,
            cumulative_tokens=self.cumulative_tokens,
            window_pct=round(pct, 4),
            window_status=status,
            timestamp=_now(),
        )
        self.records.append(rec)
        return rec

    def needs_injection(self) -> bool:
        """True if the thread has crossed the injection threshold."""
        return self.cumulative_chars >= (MAX_CHARS * THRESHOLD_INJECT)

    def needs_preparation(self) -> bool:
        """True if the thread should begin building a blob now."""
        return self.cumulative_chars >= (MAX_CHARS * THRESHOLD_PREP)

    def window_remaining_chars(self) -> int:
        return max(0, MAX_CHARS - self.cumulative_chars)

    def window_remaining_pct(self) -> float:
        return round(max(0, 1.0 - self.cumulative_chars / MAX_CHARS), 4)

    def stats(self) -> Dict:
        return {
            "thread_id": self.thread_id,
            "messages": len(self.records),
            "ai_messages": len([r for r in self.records if r.source == "ai"]),
            "human_messages": len([r for r in self.records if r.source == "human"]),
            "cumulative_chars": self.cumulative_chars,
            "cumulative_tokens": self.cumulative_tokens,
            "window_pct_used": round(self.cumulative_chars / MAX_CHARS * 100, 2),
            "window_remaining_chars": self.window_remaining_chars(),
            "injection_count": self.injection_count,
            "current_status": self.records[-1].window_status if self.records else "EMPTY",
        }

    def last_status(self) -> str:
        return self.records[-1].window_status if self.records else "EMPTY"
