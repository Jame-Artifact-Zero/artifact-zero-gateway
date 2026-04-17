"""
simulated_thread.py
Layer 4: The infinite memory wrapper.

The AI never sees a window boundary.
It receives a fresh context that feels like a continuation.
It doesn't know it's been relayed. It just keeps going.

Usage:
    thread = SimulatedThread(label="my_conversation")
    thread.add("human", "What is NTI?")
    thread.add("ai", "NTI is a deterministic rule-based engine...")
    # ... conversation continues ...
    # When window fills, SimulatedThread auto-builds a blob
    # and returns it with a flag: inject_now=True
    # Caller passes blob.to_prompt() as system context to fresh API call
    # Thread continues seamlessly
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

from gateway import Gateway
from thread_monitor import ThreadMonitor
from blob_builder import BlobBuilder, InjectionBlob


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# ADD RESULT — returned on every message add
# ---------------------------------------------------------------------------

@dataclass
class AddResult:
    record_id: str
    source: str
    char_count: int
    cumulative_chars: int
    window_pct: float
    window_status: str        # NOMINAL / WATCH / PREPARE / INJECT / CRITICAL
    inject_now: bool          # caller must relay if True
    blob: Optional[InjectionBlob] = None  # populated when inject_now=True


# ---------------------------------------------------------------------------
# SIMULATED THREAD
# ---------------------------------------------------------------------------

class SimulatedThread:
    """
    A thread that never ends.

    Internally manages a Gateway + ThreadMonitor pair.
    When the window approaches capacity, it builds an injection blob
    and signals the caller to start a fresh context with the blob prepended.

    The AI on the other end never knows. It just has context.
    """

    def __init__(self, label: str = "", thread_id: Optional[str] = None):
        self.thread_id: str = thread_id or str(uuid.uuid4())[:8]
        self.label: str = label
        self.relay_number: int = 1
        self.blobs: List[InjectionBlob] = []       # history of all injections
        self.total_messages: int = 0                # across all relays
        self.total_chars: int = 0                   # across all relays

        # Active gateway and monitor — reset on each relay
        self._gateway = Gateway(label=label, gateway_id=self.thread_id)
        self._monitor = ThreadMonitor(thread_id=self.thread_id)

        self.created_at: str = _now()

    # ------------------------------------------------------------------
    # PRIMARY INTERFACE
    # ------------------------------------------------------------------

    def add(self, source: str, content: str) -> AddResult:
        """
        Add a message to the thread.
        Returns AddResult with window status and injection signal.

        If result.inject_now is True:
          - result.blob contains the injection blob
          - Call result.blob.to_prompt() to get the context string
          - Pass that as system context to your next fresh API call
          - Then call self.relay() to reset the active window
        """
        # Record in monitor
        rec = self._monitor.record(content, source)

        # Ingest into gateway
        self._gateway.ingest(content, source=source)

        # Update totals
        self.total_messages += 1
        self.total_chars += rec.char_count

        # Check if injection needed
        inject_now = self._monitor.needs_injection()
        blob = None

        if inject_now:
            builder = BlobBuilder(self._gateway, self._monitor, self.relay_number)
            blob = builder.build()
            self.blobs.append(blob)

        return AddResult(
            record_id=rec.record_id,
            source=source,
            char_count=rec.char_count,
            cumulative_chars=self._monitor.cumulative_chars,
            window_pct=rec.window_pct,
            window_status=rec.window_status,
            inject_now=inject_now,
            blob=blob,
        )

    def relay(self) -> InjectionBlob:
        """
        Execute the relay — reset the active window.
        Builds a blob if one wasn't already built, archives it,
        resets gateway and monitor for the next window.

        Returns the blob that should be injected into the fresh context.
        """
        # Build blob from current state if not already done
        builder = BlobBuilder(self._gateway, self._monitor, self.relay_number)
        blob = builder.build()
        if not self.blobs or self.blobs[-1].blob_id != blob.blob_id:
            self.blobs.append(blob)

        # Reset active window — fresh gateway and monitor
        self.relay_number += 1
        self._gateway = Gateway(label=self.label, gateway_id=self.thread_id)
        self._monitor = ThreadMonitor(thread_id=self.thread_id)

        return blob

    # ------------------------------------------------------------------
    # INSPECTION
    # ------------------------------------------------------------------

    def status(self) -> Dict:
        monitor_stats = self._monitor.stats()
        return {
            "thread_id": self.thread_id,
            "label": self.label,
            "relay_number": self.relay_number,
            "total_messages": self.total_messages,
            "total_chars": self.total_chars,
            "blobs_created": len(self.blobs),
            "active_window_chars": self._monitor.cumulative_chars,
            "active_window_pct": round(self._monitor.cumulative_chars / 800_000 * 100, 2),
            "active_window_status": monitor_stats["current_status"],
            "active_unique_sequences": self._monitor.stats()["messages"],
            "window_remaining_chars": self._monitor.window_remaining_chars(),
        }

    def core(self) -> List[str]:
        """Current irreducible core from the active window."""
        builder = BlobBuilder(self._gateway, self._monitor, self.relay_number)
        seqs, _ = builder._extract_core()
        return seqs

    def last_blob(self) -> Optional[InjectionBlob]:
        return self.blobs[-1] if self.blobs else None

    def full_history_chars(self) -> int:
        return self.total_chars
