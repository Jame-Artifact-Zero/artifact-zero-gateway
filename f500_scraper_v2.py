"""
Artifact Zero - Fortune 500 Scraper v2
=======================================
SCRAPE ONLY. No scoring. Stores raw page text per URL.

Job 1 of 2:
  - f500_scraper_v2.py  (this file)  -> scrape pages, store raw text
  - f500_scorer.py                   -> read raw text, run CSI+NTI, store scores

Run:
  python f500_scraper_v2.py --limit 5
  python f500_scraper_v2.py --limit 500 --target f500
  python f500_scraper_v2.py --limit 30 --target vc

Lambda:
  lambda_handler({"target": "both", "limit": 500})
"""
import os
import re
import json
import time
import hashlib
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("scraper")


# ═══════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════
def get_db_url():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    try:
        import boto3
        ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-east-1"))
        resp = ssm.get_parameter(Name="/artifact-zero/DATABASE_URL", WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception:
        return None

def get_conn():
    url = get_db_url()
    if not url:
        raise RuntimeError("No DATABASE_URL found")
    import psycopg2
    return psycopg2.connect(url, connect_timeout=10)


def ensure_tables(conn):
    cur = conn.cursor()

    # Raw scraped pages - one row per URL per scrape
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fortune500_pages (
        id SERIAL PRIMARY KEY,
        slug TEXT NOT NULL,
        company_name TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT 'f500',
        rank INTEGER,
        base_url TEXT,
        page_url TEXT NOT NULL,
        page_type TEXT NOT NULL DEFAULT 'about',
        raw_text TEXT,
        word_count INTEGER DEFAULT 0,
        checksum TEXT,
        scraped_at TIMESTAMP DEFAULT NOW(),
        is_current BOOLEAN DEFAULT TRUE
    )
    """)

    # Index for fast lookups
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_f500_pages_slug ON fortune500_pages(slug, is_current)
    """)
    cur.execute("""
    CREATE INDEX IF NOT EXISTS idx_f500_pages_unscored ON fortune500_pages(is_current) WHERE is_current = TRUE
    """)

    # Keep existing fortune500_scores table for backward compat
    # The scorer will populate it
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fortune500_scores (
        slug TEXT PRIMARY KEY,
        company_name TEXT NOT NULL,
        rank INTEGER,
        url TEXT,
        homepage_copy TEXT,
        score_json TEXT,
        nii_score REAL DEFAULT 0,
        issue_count INTEGER DEFAULT 0,
        last_checked TEXT,
        last_changed TEXT,
        pages_scraped TEXT DEFAULT '[]',
        scored_at TEXT,
        score_version TEXT DEFAULT 'unscored'
    )
    """)

    # Same for VC funds
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vc_fund_scores (
        slug TEXT PRIMARY KEY,
        fund_name TEXT NOT NULL,
        rank INTEGER,
        url TEXT,
        homepage_copy TEXT,
        score_json TEXT,
        nii_score REAL DEFAULT 0,
        issue_count INTEGER DEFAULT 0,
        last_checked TEXT,
        last_changed TEXT,
        pages_scraped TEXT DEFAULT '[]',
        scored_at TEXT,
        score_version TEXT DEFAULT 'unscored'
    )
    """)

    # Add scored_at and score_version columns if missing
    for table in ["fortune500_scores", "vc_fund_scores"]:
        for col, ctype, default in [
            ("scored_at", "TEXT", None),
            ("score_version", "TEXT", "'unscored'"),
        ]:
            try:
                dflt = f" DEFAULT {default}" if default else ""
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ctype}{dflt}")
            except Exception:
                conn.rollback()

    # User-submitted sites
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_submitted_sites (
        id SERIAL PRIMARY KEY,
        slug TEXT UNIQUE NOT NULL,
        company_name TEXT NOT NULL,
        url TEXT NOT NULL,
        email TEXT,
        status TEXT DEFAULT 'queued',
        submitted_at TIMESTAMP DEFAULT NOW(),
        scraped_at TIMESTAMP,
        scored_at TIMESTAMP
    )
    """)

    conn.commit()


# ═══════════════════════════════════════════
# TIER 1+2 SUBPAGE TARGETS
# ═══════════════════════════════════════════
# These are tried in priority order for every company.
# Company-specific subpages from the COMPANIES list are tried first,
# then these universal targets fill in the gaps.

TIER1_PATHS = [
    # Sustainability / ESG - promise-heavy, commitment language
    ("/sustainability", "sustainability"),
    ("/esg", "sustainability"),
    ("/corporate-responsibility", "sustainability"),
    ("/responsibility", "sustainability"),
    ("/impact", "sustainability"),
    # Investor Relations - strategic vision, performance narrative
    ("/investors", "investor-relations"),
    ("/investor-relations", "investor-relations"),
    ("/ir", "investor-relations"),
    # Newsroom - fresh announcements, strategy language
    ("/newsroom", "newsroom"),
    ("/press", "newsroom"),
    ("/news", "newsroom"),
    ("/media", "newsroom"),
]

TIER2_PATHS = [
    # Values / Purpose / Culture
    ("/our-values", "values"),
    ("/values", "values"),
    ("/our-purpose", "values"),
    ("/purpose", "values"),
    ("/culture", "values"),
    # About / Company
    ("/about", "about"),
    ("/about-us", "about"),
    ("/our-company", "about"),
    ("/company", "about"),
    ("/who-we-are", "about"),
    ("/our-story", "about"),
    # Leadership
    ("/leadership", "leadership"),
    ("/team", "leadership"),
    ("/executives", "leadership"),
]


# ═══════════════════════════════════════════
# SCRAPING ENGINE
# ═══════════════════════════════════════════
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

JUNK_RE = re.compile(
    r"^\$[\d,.]+$|^\d+\s*(ct|oz|lb|ml|pack|count)"
    r"|add to cart|buy now|shop now|sign in|log in|subscribe"
    r"|©|cookie|privacy policy|terms of service|all rights reserved"
    r"|your cart|checkout|shipping|free delivery|save \$"
    r"|^\d+ reviews?$|^\d+ stars?$",
    re.IGNORECASE
)
PRICE_RE = re.compile(r"\$\d+\.?\d{0,2}")


def fetch_page(url, timeout=15):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.debug(f"  Could not fetch {url}: {e}")
        return None


def _is_junk(text):
    if len(PRICE_RE.findall(text)) >= 3:
        return True
    if len(text.split()) < 8:
        return True
    if JUNK_RE.search(text):
        return True
    return False


def extract_text(html):
    """Extract meaningful paragraph-level text from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "nav", "footer",
                      "iframe", "svg", "form", "button", "select", "option",
                      "input", "aside", "menu"]):
        tag.decompose()

    blocks = []

    # Priority 1: main/article paragraphs
    for container in soup.find_all(["main", "article"]):
        for p in container.find_all(["p", "blockquote", "li"]):
            t = p.get_text(separator=" ", strip=True)
            if len(t) >= 80 and not _is_junk(t):
                blocks.append(t)

    # Priority 2: all paragraphs
    if len(blocks) < 3:
        for p in soup.find_all(["p", "blockquote"]):
            t = p.get_text(separator=" ", strip=True)
            if len(t) >= 80 and not _is_junk(t):
                if not any(t[:50] in b for b in blocks):
                    blocks.append(t)

    # Priority 3: heading + next sibling
    for h in soup.find_all(["h1", "h2", "h3"]):
        heading = h.get_text(separator=" ", strip=True)
        if len(heading) < 5 or len(heading) > 200:
            continue
        sib = h.find_next_sibling(["p", "div"])
        if sib:
            t = sib.get_text(separator=" ", strip=True)
            if len(t) >= 80 and not _is_junk(t):
                combo = f"{heading}. {t}"
                if not any(t[:50] in b for b in blocks):
                    blocks.append(combo)

    # Priority 4: divs with sentences
    if len(blocks) < 3:
        for div in soup.find_all(["div", "section"]):
            t = div.get_text(separator=" ", strip=True)
            if len(t) >= 100 and t.count(".") >= 2 and not _is_junk(t):
                if not any(t[:60] in b for b in blocks):
                    blocks.append(t)

    # Deduplicate
    seen = set()
    unique = []
    for b in blocks:
        key = b[:80].lower()
        if key not in seen:
            seen.add(key)
            unique.append(b)

    combined = "\n\n".join(unique)
    return combined[:5000] if len(combined) > 5000 else combined


def parse_sitemap(base_url):
    """Try to find high-value pages from sitemap.xml."""
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return []
        # Quick regex parse for URLs (faster than XML parser for this)
        urls = re.findall(r"<loc>(https?://[^<]+)</loc>", resp.text)
        # Filter for high-value page types
        valuable = []
        keywords = [
            "sustainability", "esg", "corporate-responsibility", "impact",
            "investor", "annual-report", "shareholder",
            "newsroom", "press", "news",
            "values", "purpose", "mission", "culture",
            "about", "our-company", "who-we-are", "our-story",
            "leadership", "ceo", "chairman",
        ]
        for url in urls:
            lower = url.lower()
            for kw in keywords:
                if kw in lower:
                    # Classify page type
                    if any(k in lower for k in ["sustainab", "esg", "responsib", "impact"]):
                        valuable.append((url, "sustainability"))
                    elif any(k in lower for k in ["investor", "annual-report", "shareholder"]):
                        valuable.append((url, "investor-relations"))
                    elif any(k in lower for k in ["newsroom", "press", "news"]):
                        valuable.append((url, "newsroom"))
                    elif any(k in lower for k in ["values", "purpose", "mission", "culture"]):
                        valuable.append((url, "values"))
                    elif any(k in lower for k in ["leadership", "ceo", "chairman"]):
                        valuable.append((url, "leadership"))
                    else:
                        valuable.append((url, "about"))
                    break
        # Deduplicate by page_type (keep first of each type)
        seen_types = set()
        result = []
        for url, ptype in valuable:
            if ptype not in seen_types:
                seen_types.add(ptype)
                result.append((url, ptype))
        return result[:8]  # Max 8 pages from sitemap
    except Exception:
        return []


def scrape_company_pages(base_url, subpages):
    """
    Scrape a company's pages and return list of (url, page_type, text) tuples.
    Does NOT score. Just returns raw text per page.
    """
    results = []
    tried_urls = set()

    def try_url(url, page_type):
        if url in tried_urls:
            return
        tried_urls.add(url)
        log.info(f"  -> {url}")
        html = fetch_page(url)
        if html:
            text = extract_text(html)
            if len(text) >= 80:
                results.append((url, page_type, text))
                return True
        time.sleep(0.5)
        return False

    # 1. Base URL (homepage)
    try_url(base_url, "homepage")

    # 2. Company-specific subpages from COMPANIES list
    for path in (subpages or []):
        if path.startswith("http"):
            url = path
        else:
            url = base_url.rstrip("/") + path
        # Guess page type from path
        lower = path.lower()
        if any(k in lower for k in ["sustainab", "esg", "responsib", "impact"]):
            ptype = "sustainability"
        elif any(k in lower for k in ["investor", "ir", "annual", "shareholder"]):
            ptype = "investor-relations"
        elif any(k in lower for k in ["news", "press", "media"]):
            ptype = "newsroom"
        elif any(k in lower for k in ["value", "purpose", "mission", "culture"]):
            ptype = "values"
        elif any(k in lower for k in ["leader", "team", "executive", "ceo"]):
            ptype = "leadership"
        else:
            ptype = "about"
        try_url(url, ptype)

    # 3. Sitemap discovery
    sitemap_pages = parse_sitemap(base_url)
    for url, ptype in sitemap_pages:
        try_url(url, ptype)

    # 4. Tier 1 universal paths (if not already covered)
    covered_types = {r[1] for r in results}
    for path, ptype in TIER1_PATHS:
        if ptype in covered_types:
            continue
        url = base_url.rstrip("/") + path
        if try_url(url, ptype):
            covered_types.add(ptype)

    # 5. Tier 2 universal paths (if still thin)
    if len(results) < 3:
        for path, ptype in TIER2_PATHS:
            if ptype in covered_types:
                continue
            url = base_url.rstrip("/") + path
            if try_url(url, ptype):
                covered_types.add(ptype)
            if len(results) >= 5:
                break

    return results


# ═══════════════════════════════════════════
# STORAGE (scrape only, no scoring)
# ═══════════════════════════════════════════
def store_scraped_pages(conn, slug, company_name, rank, base_url, entity_type, pages):
    """
    Store scraped pages in fortune500_pages.
    Uses checksum to detect changes - only stores new data if content changed.
    Returns (pages_stored, pages_unchanged, combined_text).
    """
    cur = conn.cursor()
    stored = 0
    unchanged = 0
    all_text_parts = []

    for page_url, page_type, raw_text in pages:
        checksum = hashlib.md5(raw_text.encode()).hexdigest()
        word_count = len(raw_text.split())

        # Check if we already have this exact content
        cur.execute("""
            SELECT checksum FROM fortune500_pages
            WHERE slug = %s AND page_url = %s AND is_current = TRUE
            ORDER BY scraped_at DESC LIMIT 1
        """, (slug, page_url))
        existing = cur.fetchone()

        if existing and existing[0] == checksum:
            unchanged += 1
            # Still include in combined text
            all_text_parts.append(f"[{page_type.upper()}]\n{raw_text}")
            continue

        # Mark old versions as not current
        cur.execute("""
            UPDATE fortune500_pages SET is_current = FALSE
            WHERE slug = %s AND page_url = %s AND is_current = TRUE
        """, (slug, page_url))

        # Insert new version
        cur.execute("""
            INSERT INTO fortune500_pages
            (slug, company_name, entity_type, rank, base_url, page_url, page_type, raw_text, word_count, checksum, is_current)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
        """, (slug, company_name, entity_type, rank, base_url, page_url, page_type, raw_text, word_count, checksum))

        stored += 1
        all_text_parts.append(f"[{page_type.upper()}]\n{raw_text}")

    # Update the scores table with combined text (for backward compat with list API)
    # But do NOT score - just store the text and mark as unscored
    combined_text = "\n\n---\n\n".join(all_text_parts)
    now = datetime.now(timezone.utc).isoformat()
    pages_list = [p[0] for p in pages]

    if entity_type == "vc":
        name_col = "fund_name"
        table = "vc_fund_scores"
    else:
        name_col = "company_name"
        table = "fortune500_scores"

    cur.execute(f"""
        INSERT INTO {table} (slug, {name_col}, rank, url, homepage_copy, last_checked, pages_scraped, score_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'unscored')
        ON CONFLICT (slug) DO UPDATE SET
            homepage_copy = EXCLUDED.homepage_copy,
            last_checked = EXCLUDED.last_checked,
            pages_scraped = EXCLUDED.pages_scraped,
            score_version = CASE
                WHEN {table}.homepage_copy IS DISTINCT FROM EXCLUDED.homepage_copy
                THEN 'unscored'
                ELSE {table}.score_version
            END
    """, (slug, company_name, rank, base_url, combined_text, now, json.dumps(pages_list)))

    conn.commit()
    return stored, unchanged, combined_text


def process_entity(conn, slug, name, rank, base_url, subpages, entity_type):
    """Scrape one company. No scoring."""
    log.info(f"[{rank}] {name}")

    pages = scrape_company_pages(base_url, subpages)

    if not pages:
        log.warning(f"  SKIP {name}: no pages with sufficient text")
        return False

    stored, unchanged, combined = store_scraped_pages(
        conn, slug, name, rank, base_url, entity_type, pages
    )

    total_words = len(combined.split())
    page_types = [p[1] for p in pages]

    log.info(f"  OK {name}: {len(pages)} pages ({stored} new, {unchanged} unchanged) | {total_words} words | types: {', '.join(set(page_types))}")
    return True


# ═══════════════════════════════════════════
# COMPANY LISTS
# ═══════════════════════════════════════════
try:
    from f500_companies import COMPANIES
except ImportError:
    COMPANIES = []

VC_FUNDS = [
    ("sequoia", "Sequoia Capital", 1, "https://www.sequoiacap.com", []),
    ("a16z", "Andreessen Horowitz", 2, "https://a16z.com", ["/about"]),
    ("accel", "Accel", 3, "https://www.accel.com", ["/about"]),
    ("general-catalyst", "General Catalyst", 4, "https://www.generalcatalyst.com", ["/who-we-are"]),
    ("benchmark", "Benchmark", 5, "https://www.benchmark.com", []),
    ("kleiner-perkins", "Kleiner Perkins", 6, "https://www.kleinerperkins.com", ["/about"]),
    ("bessemer", "Bessemer Venture Partners", 7, "https://www.bvp.com", ["/about"]),
    ("lightspeed", "Lightspeed Venture Partners", 8, "https://lsvp.com", ["/about"]),
    ("founders-fund", "Founders Fund", 9, "https://foundersfund.com", []),
    ("khosla", "Khosla Ventures", 10, "https://www.khoslaventures.com", ["/about"]),
    ("tiger-global", "Tiger Global Management", 11, "https://www.tigerglobal.com", []),
    ("index-ventures", "Index Ventures", 12, "https://www.indexventures.com", ["/about"]),
    ("greylock", "Greylock Partners", 13, "https://greylock.com", []),
    ("nea", "New Enterprise Associates", 14, "https://www.nea.com", ["/about"]),
    ("insight-partners", "Insight Partners", 15, "https://www.insightpartners.com", ["/about"]),
    ("500-global", "500 Global", 16, "https://500.co", ["/about"]),
    ("usv", "Union Square Ventures", 17, "https://www.usv.com", []),
    ("first-round", "First Round Capital", 18, "https://firstround.com", []),
    ("battery", "Battery Ventures", 19, "https://www.battery.com", ["/about"]),
    ("ivp", "IVP", 20, "https://www.ivp.com", ["/about"]),
    ("gv", "GV (Google Ventures)", 21, "https://www.gv.com", []),
    ("spark-capital", "Spark Capital", 22, "https://www.sparkcapital.com", []),
    ("ribbit", "Ribbit Capital", 23, "https://ribbitcap.com", []),
    ("canaan", "Canaan Partners", 24, "https://www.canaan.com", ["/about"]),
    ("redpoint", "Redpoint Ventures", 25, "https://www.redpoint.com", ["/about"]),
    ("coatue", "Coatue Management", 26, "https://www.coatue.com", []),
    ("lux-capital", "Lux Capital", 27, "https://www.luxcapital.com", ["/about"]),
    ("felicis", "Felicis Ventures", 28, "https://www.felicis.com", ["/about"]),
    ("thrive-capital", "Thrive Capital", 29, "https://thrivecap.com", []),
    ("fifth-wall", "Fifth Wall", 30, "https://fifthwall.com", ["/about"]),
]


# ═══════════════════════════════════════════
# ENTRY
# ═══════════════════════════════════════════
def lambda_handler(event, context):
    target = event.get("target", "both")
    limit = event.get("limit", 999)
    conn = get_conn()
    ensure_tables(conn)
    results = []

    if target in ("f500", "both"):
        companies = COMPANIES[:min(limit, len(COMPANIES))]
        ok = 0
        for slug, name, rank, url, subs in companies:
            try:
                if process_entity(conn, slug, name, rank, url, subs, "f500"):
                    ok += 1
                time.sleep(1)
            except Exception as e:
                log.error(f"Error {name}: {e}")
        results.append(f"F500: {ok}/{len(companies)} scraped")

    if target in ("vc", "both"):
        funds = VC_FUNDS[:min(limit, len(VC_FUNDS))]
        ok = 0
        for slug, name, rank, url, subs in funds:
            try:
                if process_entity(conn, slug, name, rank, url, subs, "vc"):
                    ok += 1
                time.sleep(1)
            except Exception as e:
                log.error(f"Error {name}: {e}")
        results.append(f"VC: {ok}/{len(funds)} scraped")

    conn.close()
    msg = "Done. " + " | ".join(results)
    log.info(msg)
    return {"statusCode": 200, "body": msg}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Artifact Zero F500 Scraper v2 (scrape only)")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--target", choices=["f500", "vc", "both"], default="both")
    a = p.parse_args()
    print(lambda_handler({"target": a.target, "limit": a.limit}, None))
