"""
time_object.py
--------------
E09 Robust Time Object.

Provides a deterministic time/version anchor object for each request.
Use this in trace + outputs to prevent version confusion across sessions.

Fields:
- server_utc_iso
- tz (if provided)
- build_version (string passed in)
- request_id (pass-through)
"""

from typing import Dict, Any, Optional
from datetime import datetime, timezone


def make_time_object(build_version: str, request_id: Optional[str] = None, tz: Optional[str] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "server_utc_iso": now,
        "tz": tz or "UTC",
        "build_version": build_version,
        "request_id": request_id or ""
    }
