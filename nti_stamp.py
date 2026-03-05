"""
nti_stamp.py

NTI Verified Stamp — generation and append logic.
The stamp is appended to an outbound email body after a clean check passes.
The verify link points to /verify/<request_id> served by nti_log_routes.

Three stamp variants:
  plain  — plain text, safe for all email clients
  html   — rich clients (Outlook, Gmail web)
  short  — single signature line

White-label config: override STAMP_CONFIG at startup or per-account.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any

# ── Default stamp config ────────────────────────────────────────────────────

STAMP_CONFIG = {
    "company_name": "Artifact Zero",
    "prefix": "NTI Verified",
    "base_url": "https://artifact0.com",
    "score_visible": True,        # show score in stamp
    "custom_footer": None,        # optional extra line
    "score_threshold": 80,        # minimum score to stamp
}

# ── Threshold guard ──────────────────────────────────────────────────────────

def should_stamp(score: float, config: Optional[Dict] = None) -> bool:
    """Return True only if score meets threshold."""
    threshold = (config or STAMP_CONFIG).get("score_threshold", 80)
    return score >= threshold


# ── Domain extraction ────────────────────────────────────────────────────────

def extract_recipient_domain(to_address: str) -> Optional[str]:
    """Extract domain from email address. Never logs full address."""
    match = re.search(r"@([\w.\-]+)", to_address or "")
    return match.group(1).lower() if match else None


# ── Stamp generation ─────────────────────────────────────────────────────────

def _verify_url(request_id: str, base_url: str) -> str:
    return f"{base_url}/verify/{request_id}"


def generate_plain(
    request_id: str,
    score: float,
    checked_at: Optional[str] = None,
    config: Optional[Dict] = None,
) -> str:
    """Plain text stamp — safe for all clients."""
    cfg = config or STAMP_CONFIG
    ts = checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    url = _verify_url(request_id, cfg["base_url"])
    score_line = f" · Score {int(score)}/100" if cfg.get("score_visible") else ""
    footer = f"\n{cfg['custom_footer']}" if cfg.get("custom_footer") else ""

    return (
        f"\n\n--\n"
        f"✦ {cfg['prefix']}{score_line} · {ts}\n"
        f"Verify: {url}{footer}"
    )


def generate_html(
    request_id: str,
    score: float,
    checked_at: Optional[str] = None,
    config: Optional[Dict] = None,
) -> str:
    """HTML stamp for rich email clients."""
    cfg = config or STAMP_CONFIG
    ts = checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    url = _verify_url(request_id, cfg["base_url"])
    score_text = f" &middot; Score {int(score)}/100" if cfg.get("score_visible") else ""
    footer_html = f"<br><span style='color:#6b7280'>{cfg['custom_footer']}</span>" if cfg.get("custom_footer") else ""

    return (
        f"<br><br><hr style='border:none;border-top:1px solid #e5e7eb;margin:16px 0'>"
        f"<p style='font-family:monospace;font-size:11px;color:#6b7280;margin:0'>"
        f"<span style='color:#00c46a'>✦</span> "
        f"<strong>{cfg['prefix']}</strong>"
        f"{score_text} &middot; {ts}"
        f"<br><a href='{url}' style='color:#6b7280'>Verify: {url}</a>"
        f"{footer_html}"
        f"</p>"
    )


def generate_short(
    request_id: str,
    score: float,
    config: Optional[Dict] = None,
) -> str:
    """Single-line signature stamp."""
    cfg = config or STAMP_CONFIG
    url = _verify_url(request_id, cfg["base_url"])
    return f"✦ {cfg['prefix']} | {url}"


def generate_all(
    request_id: str,
    score: float,
    checked_at: Optional[str] = None,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Return all three stamp variants."""
    cfg = config or STAMP_CONFIG
    ts = checked_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "request_id": request_id,
        "score": int(score),
        "checked_at": ts,
        "verify_url": _verify_url(request_id, cfg["base_url"]),
        "stamps": {
            "plain": generate_plain(request_id, score, ts, cfg),
            "html": generate_html(request_id, score, ts, cfg),
            "short": generate_short(request_id, score, cfg),
        }
    }
