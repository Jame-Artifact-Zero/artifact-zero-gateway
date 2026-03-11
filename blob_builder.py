"""
blob_builder.py
Layer 3: Build the injection blob from the gateway's compressed core.

The blob is the baton in the relay race.
It contains everything the AI needs to continue the thread
without knowing a window boundary was crossed.

Blob structure:
  - Thread identity (id, label, message count, time range)
  - Irreducible core sequences (survived all reduction passes)
  - Dictionary snapshot (pointer → original for core sequences only)
  - Source balance (human vs AI char ratio)
  - Last N messages verbatim (recent context window)
  - Window state at time of injection
  - Continuation directive (what the AI should understand about this blob)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from gateway import Gateway
from thread_monitor import ThreadMonitor


RECENT_MESSAGES_TO_KEEP = 8   # Last N verbatim messages always included
CORE_MIN_FREQUENCY = 2        # Minimum repeat count to be considered core


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# BLOB
# ---------------------------------------------------------------------------

@dataclass
class InjectionBlob:
    blob_id: str
    thread_id: str
    created_at: str
    message_count_at_injection: int
    window_pct_at_injection: float

    # The irreducible core
    core_sequences: List[str]          # ordered by frequency desc
    core_dictionary: Dict[str, str]    # pointer → original text

    # Recent verbatim context
    recent_messages: List[Dict]        # last N messages as {source, content}

    # Thread identity
    thread_label: str
    ai_char_count: int
    human_char_count: int

    # Injection count (how many times this thread has been relayed)
    relay_number: int

    def to_prompt(self) -> str:
        """
        Render the blob as a plain-text prompt injection.
        This is what gets prepended to the fresh context window.
        The AI reads this and continues as if the thread never broke.
        """
        lines = []
        lines.append("=== THREAD CONTINUATION ===")
        lines.append(f"Thread: {self.thread_label or self.thread_id}")
        lines.append(f"Messages processed: {self.message_count_at_injection}")
        lines.append(f"Relay number: {self.relay_number}")
        lines.append(f"Window was {round(self.window_pct_at_injection * 100, 1)}% full at handoff.")
        lines.append("")
        lines.append("--- IRREDUCIBLE CORE ---")
        lines.append("These sequences are the load-bearing content of the thread.")
        lines.append("They survived every compression pass. They are the thread.")
        lines.append("")
        for seq in self.core_sequences:
            lines.append(f"  • {seq}")
        lines.append("")
        lines.append("--- RECENT CONTEXT ---")
        lines.append("Last messages verbatim:")
        lines.append("")
        for msg in self.recent_messages:
            label = "Human" if msg["source"] == "human" else "AI"
            lines.append(f"[{label}]: {msg['content']}")
        lines.append("")
        lines.append("=== CONTINUE FROM HERE ===")
        lines.append("The thread is live. Respond to the next human message as normal.")
        lines.append("You have full context. No gaps.")
        return "\n".join(lines)

    def to_dict(self) -> Dict:
        return {
            "blob_id": self.blob_id,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "message_count_at_injection": self.message_count_at_injection,
            "window_pct_at_injection": self.window_pct_at_injection,
            "relay_number": self.relay_number,
            "thread_label": self.thread_label,
            "ai_char_count": self.ai_char_count,
            "human_char_count": self.human_char_count,
            "core_sequences": self.core_sequences,
            "core_dictionary": self.core_dictionary,
            "recent_messages": self.recent_messages,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def char_count(self) -> int:
        """How many chars does this blob consume when injected."""
        return len(self.to_prompt())


# ---------------------------------------------------------------------------
# BUILDER
# ---------------------------------------------------------------------------

class BlobBuilder:
    """
    Builds injection blobs from a Gateway + ThreadMonitor pair.
    Called when ThreadMonitor signals PREPARE or INJECT.
    """

    def __init__(self, gateway: Gateway, monitor: ThreadMonitor, relay_number: int = 1):
        self.gateway = gateway
        self.monitor = monitor
        self.relay_number = relay_number

    def build(self) -> InjectionBlob:
        """Build and return the injection blob."""
        core_seqs, core_dict = self._extract_core()
        recent = self._extract_recent()
        ai_chars = sum(r.char_count for r in self.monitor.records if r.source == "ai")
        human_chars = sum(r.char_count for r in self.monitor.records if r.source == "human")

        blob = InjectionBlob(
            blob_id=str(uuid.uuid4())[:8],
            thread_id=self.monitor.thread_id,
            created_at=_now(),
            message_count_at_injection=len(self.monitor.records),
            window_pct_at_injection=self.monitor.cumulative_chars / 800_000,
            core_sequences=core_seqs,
            core_dictionary=core_dict,
            recent_messages=recent,
            thread_label=self.gateway.label,
            ai_char_count=ai_chars,
            human_char_count=human_chars,
            relay_number=self.relay_number,
        )
        self.monitor.injection_count += 1
        return blob

    def _extract_core(self) -> Tuple[List[str], Dict[str, str]]:
        """
        Extract the irreducible core — sequences that repeated.
        Ordered by frequency descending.
        """
        core_entries = sorted(
            [e for e in self.gateway.dictionary.values() if e.frequency >= CORE_MIN_FREQUENCY],
            key=lambda e: e.frequency,
            reverse=True
        )
        # If nothing repeated, take top 15 by... take all unique sequences
        # that appeared in both human and AI messages (cross-source survival)
        if not core_entries:
            human_ptrs = set(
                p for m in self.gateway.stream
                if m.source == "human"
                for p in m.compressed
            )
            ai_ptrs = set(
                p for m in self.gateway.stream
                if m.source == "ai"
                for p in m.compressed
            )
            cross_ptrs = human_ptrs & ai_ptrs
            core_entries = [
                self.gateway.dictionary[ptr]
                for ptr in cross_ptrs
                if ptr in self.gateway.dictionary
            ]

        core_seqs = [e.original for e in core_entries]
        core_dict = {e.pointer: e.original for e in core_entries}
        return core_seqs, core_dict

    def _extract_recent(self) -> List[Dict]:
        """Last N messages verbatim from the monitor."""
        recent_records = self.monitor.records[-RECENT_MESSAGES_TO_KEEP:]
        return [
            {"source": r.source, "content": r.content}
            for r in recent_records
        ]
