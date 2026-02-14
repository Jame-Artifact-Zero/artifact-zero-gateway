import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DEFAULT_LOG_DIR = os.path.join(os.getcwd(), "core_engine", "logs")
DEFAULT_LOG_FILE = "core_trace.jsonl"

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_trace_context() -> Dict[str, Any]:
    return {
        "request_id": str(uuid.uuid4()),
        "timestamp": utc_now_iso(),
    }

class TraceLogger:
    """
    ALSO TRACK EVERYTHING — Rule:
    Always writes a deterministic trace object (JSONL).
    Fail-safe: never block execution if logging fails.
    """
    def __init__(self, log_dir: str = DEFAULT_LOG_DIR, filename: str = DEFAULT_LOG_FILE) -> None:
        self.log_dir = log_dir
        self.filename = filename
        self.path = os.path.join(self.log_dir, self.filename)
        os.makedirs(self.log_dir, exist_ok=True)

    def write(self, trace_obj: Dict[str, Any]) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace_obj, ensure_ascii=False) + "\n")
        except Exception:
            # Fail-safe: never interrupt production
            pass

def approx_tokens(text: str) -> int:
    # Simple, deterministic estimate.
    return len(text.split())
