"""
gateway.py
Stream Compression Gateway — v1.1

Architecture:
  - Ingest → Strip → Compress → Store
  - NTI filler removal runs before dictionary is consulted
  - Dictionary keys are normalized sequences
  - Original preserved as metadata on first occurrence
  - Any gateway can merge with any other gateway
  - Primacy assigned at read time, not write time

Synonym Map (v1.1):
  - Human-declared equivalences only. Never inferred.
  - Applied at read/query time, never at ingest.
  - "yeah" and "yes" are different inputs. They share a semantic
    pointer only if a human explicitly declares it.
  - Declarations are logged with author and timestamp.
  - A declaration can be revoked. Revocation is also logged.
"""

from __future__ import annotations

import re
import json
import hashlib
import uuid
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# NTI FILLER STRIP LIST
# Deterministic. Zero compute. Runs first, always.
# ---------------------------------------------------------------------------

FILLER_PATTERNS = [
    # Hedge starters
    r"\bi think\b", r"\bi believe\b", r"\bi feel like\b", r"\bkind of\b",
    r"\bsort of\b", r"\bpretty much\b", r"\bbasically\b", r"\bessentially\b",
    r"\bgenerally speaking\b", r"\bfor the most part\b", r"\bin a sense\b",
    r"\bmore or less\b", r"\bto some extent\b", r"\bsomewhat\b",
    # Deferral markers
    r"\bat some point\b", r"\beventually\b", r"\bdown the road\b",
    r"\bin due time\b", r"\bwhen the time comes\b", r"\bwe'll see\b",
    r"\bwe can address that later\b", r"\bwe'll figure it out\b",
    # Reassurance drift
    r"\bdon't worry\b", r"\bno worries\b", r"\bit'll be fine\b",
    r"\bwe should be good\b", r"\bi'm sure\b", r"\btrust me\b",
    # Empty openers
    r"\bgreat question\b", r"\bof course\b", r"\babsolutely\b",
    r"\bcertainly\b", r"\bsure thing\b", r"\bhappy to help\b",
    r"\bfeel free to\b", r"\bdon't hesitate to\b",
    # Throat clearing
    r"\bso basically\b", r"\bso essentially\b", r"\bwhat i mean is\b",
    r"\bwhat i'm saying is\b", r"\bif that makes sense\b",
    r"\bdoes that make sense\b", r"\bhope that helps\b",
    # Redundant connectors
    r"\bwith that being said\b", r"\bthat being said\b",
    r"\bat the end of the day\b", r"\ball things considered\b",
    r"\bin any case\b", r"\banyway\b", r"\banyways\b",
]

_FILLER_RE = re.compile(
    "|".join(FILLER_PATTERNS),
    flags=re.IGNORECASE
)


def strip_filler(text: str) -> str:
    """Remove NTI filler patterns. Runs before anything else."""
    cleaned = _FILLER_RE.sub("", text)
    # Collapse multiple spaces left by removals
    cleaned = re.sub(r" {2,}", " ", cleaned)
    # Collapse multiple newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# NORMALIZATION
# Dictionary keys use normalized form. Original preserved as metadata.
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Normalize for dictionary keying. Lowercase, strip punctuation edges."""
    t = text.lower().strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t


def fingerprint(normalized: str) -> str:
    """Short deterministic hash for a normalized sequence."""
    return hashlib.sha256(normalized.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# SEQUENCE EXTRACTION
# Split stripped text into indexable sequences (sentence-level granularity).
# ---------------------------------------------------------------------------

def extract_sequences(text: str) -> List[str]:
    """Split text into sequences at sentence boundaries."""
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# CORE OBJECTS
# ---------------------------------------------------------------------------

@dataclass
class DictionaryEntry:
    pointer: str          # short hash — the reference ID
    original: str         # first occurrence, preserved exactly
    normalized: str       # normalized form used as key
    frequency: int = 1    # how many times this sequence has appeared
    first_seen: str = ""  # ISO timestamp



@dataclass
class SynonymDeclaration:
    declaration_id: str       # unique ID for this declaration
    pointer_a: str            # first pointer (e.g. pointer for "yeah")
    pointer_b: str            # second pointer (e.g. pointer for "yes")
    canonical: str            # which pointer is the canonical form
    declared_by: str          # who declared this
    declared_at: str          # ISO timestamp
    active: bool = True       # False if revoked
    revoked_at: str = ""
    revoke_reason: str = ""
    note: str = ""


@dataclass
class StreamMessage:
    message_id: str
    source: str           # e.g. "human", "claude", "email:sender", "email:receiver"
    raw: str              # original input, untouched
    stripped: str         # after filler removal
    compressed: List[str] # list of pointers representing the message
    timestamp: str


class Gateway:
    """
    A compression gateway. Receives input streams, strips filler,
    builds a shared dictionary, stores messages as pointer sequences.

    Any gateway can be primary. Gateways can merge.
    """

    def __init__(self, gateway_id: Optional[str] = None, label: str = ""):
        self.gateway_id: str = gateway_id or str(uuid.uuid4())[:8]
        self.label: str = label
        self.dictionary: Dict[str, DictionaryEntry] = {}  # pointer → entry
        self._key_index: Dict[str, str] = {}              # normalized → pointer
        self.stream: List[StreamMessage] = []
        self.created_at: str = _now()
        self.synonym_map: Dict[str, SynonymDeclaration] = {}  # declaration_id → declaration
        self._synonym_index: Dict[str, str] = {}  # pointer → canonical pointer

    # ------------------------------------------------------------------
    # INGEST
    # ------------------------------------------------------------------

    def ingest(self, text: str, source: str) -> StreamMessage:
        """
        Full pipeline: strip → extract sequences → compress → store.
        Returns the stored StreamMessage.
        """
        stripped = strip_filler(text)
        sequences = extract_sequences(stripped)
        pointers = [self._get_or_create(seq) for seq in sequences]

        msg = StreamMessage(
            message_id=str(uuid.uuid4())[:8],
            source=source,
            raw=text,
            stripped=stripped,
            compressed=pointers,
            timestamp=_now(),
        )
        self.stream.append(msg)
        return msg

    def _get_or_create(self, sequence: str) -> str:
        """Return existing pointer or create new dictionary entry."""
        norm = normalize(sequence)
        if not norm:
            return ""
        if norm in self._key_index:
            ptr = self._key_index[norm]
            self.dictionary[ptr].frequency += 1
            return ptr
        ptr = fingerprint(norm)
        # Handle (extremely unlikely) hash collision
        while ptr in self.dictionary and self.dictionary[ptr].normalized != norm:
            ptr = ptr[:-1] + "x"
        entry = DictionaryEntry(
            pointer=ptr,
            original=sequence,
            normalized=norm,
            frequency=1,
            first_seen=_now(),
        )
        self.dictionary[ptr] = entry
        self._key_index[norm] = ptr
        return ptr

    # ------------------------------------------------------------------
    # RECONSTRUCT
    # ------------------------------------------------------------------

    def reconstruct(self, message_id: str, use_original: bool = True) -> str:
        """Reconstruct a message from its pointer sequence."""
        msg = next((m for m in self.stream if m.message_id == message_id), None)
        if not msg:
            return ""
        parts = []
        for ptr in msg.compressed:
            if not ptr:
                continue
            entry = self.dictionary.get(ptr)
            if entry:
                parts.append(entry.original if use_original else entry.normalized)
        return " ".join(parts)

    # ------------------------------------------------------------------
    # MERGE
    # ------------------------------------------------------------------

    def merge(self, other: "Gateway") -> "Gateway":
        """
        Merge another gateway's dictionary into this one.
        Shared sequences collapse. Unique entries append.
        Returns self for chaining.
        """
        for norm, ptr in other._key_index.items():
            if norm in self._key_index:
                # Shared — increment frequency
                existing_ptr = self._key_index[norm]
                self.dictionary[existing_ptr].frequency += other.dictionary[ptr].frequency
            else:
                # Unique to other — append
                entry = other.dictionary[ptr]
                self.dictionary[ptr] = DictionaryEntry(
                    pointer=ptr,
                    original=entry.original,
                    normalized=norm,
                    frequency=entry.frequency,
                    first_seen=entry.first_seen,
                )
                self._key_index[norm] = ptr
        return self


    # ------------------------------------------------------------------
    # SYNONYM MAP
    # Human-declared only. Never inferred. Applied at read time.
    # ------------------------------------------------------------------

    def declare_synonym(
        self,
        term_a: str,
        term_b: str,
        canonical: str,
        declared_by: str,
        note: str = "",
    ) -> Optional[SynonymDeclaration]:
        """
        Declare that term_a and term_b are semantically equivalent.
        Both terms must already exist in the dictionary.
        canonical must be either term_a or term_b.
        declared_by is required — no anonymous declarations.
        """
        ptr_a = self._key_index.get(normalize(term_a))
        ptr_b = self._key_index.get(normalize(term_b))

        if not ptr_a:
            raise ValueError(f"Term not in dictionary: '{term_a}'")
        if not ptr_b:
            raise ValueError(f"Term not in dictionary: '{term_b}'")
        if normalize(canonical) not in (normalize(term_a), normalize(term_b)):
            raise ValueError(f"canonical must be one of the two terms")
        if not declared_by.strip():
            raise ValueError("declared_by is required")

        canon_ptr = self._key_index[normalize(canonical)]
        decl_id = str(uuid.uuid4())[:8]
        decl = SynonymDeclaration(
            declaration_id=decl_id,
            pointer_a=ptr_a,
            pointer_b=ptr_b,
            canonical=canon_ptr,
            declared_by=declared_by,
            declared_at=_now(),
            active=True,
            note=note,
        )
        self.synonym_map[decl_id] = decl
        # Index both pointers to canonical
        self._synonym_index[ptr_a] = canon_ptr
        self._synonym_index[ptr_b] = canon_ptr
        return decl

    def revoke_synonym(self, declaration_id: str, revoked_by: str, reason: str = "") -> bool:
        """
        Revoke a synonym declaration. Removes the runtime index entries.
        Declaration is preserved in synonym_map with active=False.
        """
        decl = self.synonym_map.get(declaration_id)
        if not decl or not decl.active:
            return False
        decl.active = False
        decl.revoked_at = _now()
        decl.revoke_reason = f"[{revoked_by}] {reason}".strip()
        # Remove from live index
        if self._synonym_index.get(decl.pointer_a) == decl.canonical:
            del self._synonym_index[decl.pointer_a]
        if self._synonym_index.get(decl.pointer_b) == decl.canonical:
            del self._synonym_index[decl.pointer_b]
        return True

    def resolve(self, pointer: str) -> str:
        """
        Resolve a pointer to its canonical form if a synonym declaration exists.
        Returns the pointer unchanged if no declaration applies.
        """
        return self._synonym_index.get(pointer, pointer)

    def query(self, term: str, use_synonyms: bool = True) -> List[StreamMessage]:
        """
        Find all messages containing a term.
        If use_synonyms=True, also matches synonym-equivalent terms.
        """
        norm = normalize(term)
        ptr = self._key_index.get(norm)
        if not ptr:
            return []

        target_ptrs = {ptr}
        if use_synonyms:
            canon = self._synonym_index.get(ptr, ptr)
            # Add all pointers that map to the same canonical
            target_ptrs |= {
                p for p, c in self._synonym_index.items() if c == canon
            }
            target_ptrs.add(canon)

        return [
            m for m in self.stream
            if any(p in target_ptrs for p in m.compressed)
        ]

    def synonym_report(self) -> List[Dict]:
        """All declarations — active and revoked."""
        out = []
        for decl in self.synonym_map.values():
            entry_a = self.dictionary.get(decl.pointer_a)
            entry_b = self.dictionary.get(decl.pointer_b)
            canon = self.dictionary.get(decl.canonical)
            out.append({
                "declaration_id": decl.declaration_id,
                "term_a": entry_a.original if entry_a else decl.pointer_a,
                "term_b": entry_b.original if entry_b else decl.pointer_b,
                "canonical": canon.original if canon else decl.canonical,
                "declared_by": decl.declared_by,
                "declared_at": decl.declared_at,
                "active": decl.active,
                "revoked_at": decl.revoked_at,
                "revoke_reason": decl.revoke_reason,
                "note": decl.note,
            })
        return out

    # ------------------------------------------------------------------
    # STATS
    # ------------------------------------------------------------------

    def stats(self) -> Dict:
        total_raw_chars = sum(len(m.raw) for m in self.stream)
        total_stripped_chars = sum(len(m.stripped) for m in self.stream)
        total_pointers = sum(len(m.compressed) for m in self.stream)
        unique_sequences = len(self.dictionary)
        repeated = sum(1 for e in self.dictionary.values() if e.frequency > 1)
        compression_ratio = (
            round(total_pointers / unique_sequences, 2)
            if unique_sequences else 0
        )
        filler_reduction = (
            round((1 - total_stripped_chars / total_raw_chars) * 100, 1)
            if total_raw_chars else 0
        )
        return {
            "gateway_id": self.gateway_id,
            "label": self.label,
            "messages": len(self.stream),
            "unique_sequences": unique_sequences,
            "repeated_sequences": repeated,
            "total_pointers": total_pointers,
            "filler_removed_pct": filler_reduction,
            "compression_ratio": compression_ratio,
            "raw_chars": total_raw_chars,
            "stripped_chars": total_stripped_chars,
        }

    # ------------------------------------------------------------------
    # SERIALIZATION
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict:
        return {
            "gateway_id": self.gateway_id,
            "label": self.label,
            "created_at": self.created_at,
            "dictionary": {
                ptr: {
                    "original": e.original,
                    "normalized": e.normalized,
                    "frequency": e.frequency,
                    "first_seen": e.first_seen,
                }
                for ptr, e in self.dictionary.items()
            },
            "stream": [
                {
                    "message_id": m.message_id,
                    "source": m.source,
                    "compressed": m.compressed,
                    "timestamp": m.timestamp,
                }
                for m in self.stream
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict) -> "Gateway":
        g = cls(gateway_id=data["gateway_id"], label=data.get("label", ""))
        g.created_at = data.get("created_at", "")
        for ptr, e in data["dictionary"].items():
            entry = DictionaryEntry(
                pointer=ptr,
                original=e["original"],
                normalized=e["normalized"],
                frequency=e["frequency"],
                first_seen=e.get("first_seen", ""),
            )
            g.dictionary[ptr] = entry
            g._key_index[e["normalized"]] = ptr
        for m in data["stream"]:
            msg = StreamMessage(
                message_id=m["message_id"],
                source=m.get("source", ""),
                raw="",  # raw not stored in serialized form
                stripped="",
                compressed=m["compressed"],
                timestamp=m["timestamp"],
            )
            g.stream.append(msg)
        return g


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
