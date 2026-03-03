"""core_engine/storage.py — Storage Engine

Persists everything with timestamps. Detects changes.
Does not detect signals. Does not score.

Public API:
- store_snapshot(conn, company_id, page_url, raw_text) -> snapshot_id
- detect_changes(conn, company_id, page_url, new_text) -> ChangeResult
- store_paragraph_scores(conn, company_id, page_url, scores) -> None
- store_stock_price(conn, ticker, date, prices) -> None
- get_latest_snapshot(conn, company_id, page_url) -> snapshot or None
- get_stock_history(conn, ticker, days=7) -> List[Dict]
- init_storage_tables(conn) -> None
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import json
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _diff_paragraphs(old_paras: List[str], new_paras: List[str]) -> Dict[str, Any]:
    """Simple paragraph-level diff. Returns added, removed, modified indices."""
    old_set = {p.strip(): i for i, p in enumerate(old_paras)}
    new_set = {p.strip(): i for i, p in enumerate(new_paras)}

    old_texts = set(old_set.keys())
    new_texts = set(new_set.keys())

    added = [{"index": new_set[t], "text": t} for t in new_texts - old_texts]
    removed = [{"index": old_set[t], "text": t} for t in old_texts - new_texts]

    return {
        "added": added,
        "removed": removed,
        "added_count": len(added),
        "removed_count": len(removed),
        "total_changes": len(added) + len(removed),
        "changed": len(added) + len(removed) > 0,
    }


def _split_paras(text: str) -> List[str]:
    import re
    paras = re.split(r"\n\s*\n|\r\n\s*\r\n", text or "")
    return [p.strip() for p in paras if p.strip() and len(p.strip()) > 20]


# ── Table Initialization ──

INIT_SQL_PG = """
CREATE TABLE IF NOT EXISTS content_snapshots (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    page_url TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    word_count INTEGER DEFAULT 0,
    scraped_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS content_changes (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    page_url TEXT NOT NULL,
    old_snapshot_id INTEGER,
    new_snapshot_id INTEGER,
    changes_json TEXT,
    detected_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paragraph_scores (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    page_url TEXT NOT NULL,
    snapshot_id INTEGER,
    paragraph_index INTEGER,
    paragraph_text TEXT,
    nii_score REAL,
    nti_score REAL,
    csi_score REAL,
    hcs_score REAL,
    scores_json TEXT,
    scored_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS page_scores (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL,
    page_url TEXT NOT NULL,
    snapshot_id INTEGER,
    nii_score REAL,
    nti_score REAL,
    csi_score REAL,
    hcs_score REAL,
    paragraph_count INTEGER,
    total_words INTEGER,
    scores_json TEXT,
    scored_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS company_tickers (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL UNIQUE,
    ticker_symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'NYSE'
);

CREATE TABLE IF NOT EXISTS stock_prices (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    price_date DATE NOT NULL,
    open_price REAL,
    close_price REAL,
    high_price REAL,
    low_price REAL,
    volume BIGINT,
    market_cap BIGINT,
    fetched_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, price_date)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_company ON content_snapshots(company_id, page_url);
CREATE INDEX IF NOT EXISTS idx_changes_company ON content_changes(company_id);
CREATE INDEX IF NOT EXISTS idx_para_scores ON paragraph_scores(company_id, page_url);
CREATE INDEX IF NOT EXISTS idx_page_scores ON page_scores(company_id, page_url);
CREATE INDEX IF NOT EXISTS idx_stock_ticker ON stock_prices(ticker, price_date);
"""

INIT_SQL_SQLITE = INIT_SQL_PG.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("NOW()", "CURRENT_TIMESTAMP")


def init_storage_tables(conn, use_pg: bool = True) -> None:
    """Create all storage tables if they don't exist."""
    sql = INIT_SQL_PG if use_pg else INIT_SQL_SQLITE
    cur = conn.cursor()
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                cur.execute(stmt)
            except Exception:
                pass  # table already exists
    conn.commit()


# ── Snapshot Operations ──

def store_snapshot(conn, company_id: int, page_url: str, raw_text: str, use_pg: bool = True) -> int:
    """Store a content snapshot. Returns snapshot_id."""
    h = _content_hash(raw_text)
    wc = len(raw_text.split())
    cur = conn.cursor()
    if use_pg:
        cur.execute(
            "INSERT INTO content_snapshots (company_id, page_url, raw_text, content_hash, word_count) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (company_id, page_url, raw_text, h, wc)
        )
        sid = cur.fetchone()[0]
    else:
        cur.execute(
            "INSERT INTO content_snapshots (company_id, page_url, raw_text, content_hash, word_count) VALUES (?, ?, ?, ?, ?)",
            (company_id, page_url, raw_text, h, wc)
        )
        sid = cur.lastrowid
    conn.commit()
    return sid


def get_latest_snapshot(conn, company_id: int, page_url: str, use_pg: bool = True) -> Optional[Dict]:
    """Get the most recent snapshot for a company/page."""
    cur = conn.cursor()
    ph = "%s" if use_pg else "?"
    cur.execute(
        f"SELECT id, content_hash, raw_text, word_count, scraped_at FROM content_snapshots WHERE company_id = {ph} AND page_url = {ph} ORDER BY scraped_at DESC LIMIT 1",
        (company_id, page_url)
    )
    row = cur.fetchone()
    if not row:
        return None
    if use_pg:
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    return dict(row) if hasattr(row, 'keys') else {"id": row[0], "content_hash": row[1], "raw_text": row[2], "word_count": row[3], "scraped_at": row[4]}


# ── Change Detection ──

def detect_changes(conn, company_id: int, page_url: str, new_text: str, use_pg: bool = True) -> Dict[str, Any]:
    """Compare new text against latest snapshot. Store change record if different."""
    new_hash = _content_hash(new_text)
    latest = get_latest_snapshot(conn, company_id, page_url, use_pg)

    if latest is None:
        # First snapshot — no comparison possible
        sid = store_snapshot(conn, company_id, page_url, new_text, use_pg)
        return {"changed": False, "first_snapshot": True, "snapshot_id": sid}

    if latest["content_hash"] == new_hash:
        # No change
        return {"changed": False, "first_snapshot": False, "snapshot_id": latest["id"]}

    # Content changed — store new snapshot and diff
    new_sid = store_snapshot(conn, company_id, page_url, new_text, use_pg)

    old_paras = _split_paras(latest["raw_text"])
    new_paras = _split_paras(new_text)
    diff = _diff_paragraphs(old_paras, new_paras)

    cur = conn.cursor()
    ph = "%s" if use_pg else "?"
    cur.execute(
        f"INSERT INTO content_changes (company_id, page_url, old_snapshot_id, new_snapshot_id, changes_json) VALUES ({ph}, {ph}, {ph}, {ph}, {ph})",
        (company_id, page_url, latest["id"], new_sid, json.dumps(diff))
    )
    conn.commit()

    return {
        "changed": True,
        "first_snapshot": False,
        "old_snapshot_id": latest["id"],
        "new_snapshot_id": new_sid,
        "diff": diff,
    }


# ── Score Storage ──

def store_paragraph_scores(conn, company_id: int, page_url: str, snapshot_id: int, scores: List[Dict], use_pg: bool = True) -> None:
    """Store paragraph-level scores."""
    cur = conn.cursor()
    ph = "%s" if use_pg else "?"
    for ps in scores:
        nii = ps.get("nii", {}).get("nii_score", 0)
        nti = ps.get("nti", {}).get("nti_score", 0)
        csi = ps.get("csi", {}).get("csi_score", 0)
        hcs = ps.get("hcs", {}).get("hcs_score", 0)
        idx = ps.get("paragraph_index", 0)
        txt = ps.get("paragraph_text", "")[:2000]  # truncate for safety
        cur.execute(
            f"INSERT INTO paragraph_scores (company_id, page_url, snapshot_id, paragraph_index, paragraph_text, nii_score, nti_score, csi_score, hcs_score, scores_json) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
            (company_id, page_url, snapshot_id, idx, txt, nii, nti, csi, hcs, json.dumps(ps))
        )
    conn.commit()


def store_page_scores(conn, company_id: int, page_url: str, snapshot_id: int, page_score: Dict, use_pg: bool = True) -> None:
    """Store page-level rollup scores."""
    cur = conn.cursor()
    ph = "%s" if use_pg else "?"
    cur.execute(
        f"INSERT INTO page_scores (company_id, page_url, snapshot_id, nii_score, nti_score, csi_score, hcs_score, paragraph_count, total_words, scores_json) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
        (company_id, page_url, snapshot_id,
         page_score.get("nii_score", 0), page_score.get("nti_score", 0),
         page_score.get("csi_score", 0), page_score.get("hcs_score", 0),
         page_score.get("paragraph_count", 0), page_score.get("total_words", 0),
         json.dumps(page_score))
    )
    conn.commit()


# ── Stock Price Storage ──

def store_stock_price(conn, ticker: str, price_date: str, prices: Dict[str, Any], use_pg: bool = True) -> None:
    """Store a single day's stock price."""
    cur = conn.cursor()
    ph = "%s" if use_pg else "?"
    try:
        cur.execute(
            f"INSERT INTO stock_prices (ticker, price_date, open_price, close_price, high_price, low_price, volume, market_cap) VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}) ON CONFLICT (ticker, price_date) DO UPDATE SET close_price = EXCLUDED.close_price, volume = EXCLUDED.volume, fetched_at = NOW()",
            (ticker, price_date, prices.get("open"), prices.get("close"),
             prices.get("high"), prices.get("low"),
             prices.get("volume"), prices.get("market_cap"))
        )
        conn.commit()
    except Exception:
        conn.rollback()


def get_stock_history(conn, ticker: str, days: int = 7, use_pg: bool = True) -> List[Dict]:
    """Get recent stock price history."""
    cur = conn.cursor()
    ph = "%s" if use_pg else "?"
    cur.execute(
        f"SELECT ticker, price_date, open_price, close_price, high_price, low_price, volume, market_cap FROM stock_prices WHERE ticker = {ph} ORDER BY price_date DESC LIMIT {ph}",
        (ticker, days)
    )
    if use_pg:
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    return [dict(r) if hasattr(r, 'keys') else {} for r in cur.fetchall()]
