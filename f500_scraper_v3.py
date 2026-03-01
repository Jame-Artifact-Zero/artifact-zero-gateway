"""
Artifact Zero - Fortune 500 Scraper v3
=======================================
SCRAPE ONLY. No scoring. Stores raw page text per URL.

Strategy: Method A (Link Discovery) primary + Method B (Sitemap) fallback.
- Fetch homepage, parse ALL links from nav/footer/body
- Classify links by text keywords (sustainability, investors, newsroom, etc)
- Follow the best corporate links
- If < 2 pages found, try sitemap.xml as fallback
- Store each page individually in company_pages table
- Change detection via MD5 checksum
- Data lives forever — old versions marked is_current=FALSE

Run:
  python f500_scraper_v3.py --limit 5
  python f500_scraper_v3.py --limit 500 --target f500
  python f500_scraper_v3.py --limit 30 --target vc

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
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import warnings
try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

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

    # ── Parent: companies ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS companies (
        id              SERIAL PRIMARY KEY,
        slug            TEXT UNIQUE NOT NULL,
        name            TEXT NOT NULL,
        entity_type     TEXT NOT NULL DEFAULT 'f500',
        rank            INTEGER,
        base_url        TEXT,
        stock_ticker    TEXT,
        sector          TEXT,
        is_active       BOOLEAN DEFAULT TRUE,
        created_at      TIMESTAMP DEFAULT NOW(),
        updated_at      TIMESTAMP DEFAULT NOW(),
        latest_score    REAL DEFAULT 0,
        latest_score_label TEXT DEFAULT 'UNSCORED',
        latest_nii      REAL DEFAULT 0,
        total_issues    INTEGER DEFAULT 0,
        total_pages     INTEGER DEFAULT 0,
        page_types      TEXT DEFAULT '[]',
        total_words     INTEGER DEFAULT 0,
        last_scraped    TIMESTAMP,
        last_scored     TIMESTAMP,
        last_changed    TIMESTAMP,
        score_version   TEXT DEFAULT 'unscored'
    )
    """)

    # ── Child: company_pages ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_pages (
        id              SERIAL PRIMARY KEY,
        company_id      INTEGER NOT NULL REFERENCES companies(id),
        slug            TEXT NOT NULL,
        page_url        TEXT NOT NULL,
        page_type       TEXT NOT NULL DEFAULT 'about',
        page_label      TEXT,
        raw_text        TEXT,
        word_count      INTEGER DEFAULT 0,
        char_count      INTEGER DEFAULT 0,
        checksum        TEXT,
        scrape_method   TEXT DEFAULT 'link_discovery',
        scraped_at      TIMESTAMP DEFAULT NOW(),
        is_current      BOOLEAN DEFAULT TRUE,
        previous_id     INTEGER REFERENCES company_pages(id),
        content_changed BOOLEAN DEFAULT FALSE
    )
    """)

    # ── Child: company_page_scores (for scorer) ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_page_scores (
        id              SERIAL PRIMARY KEY,
        company_id      INTEGER NOT NULL REFERENCES companies(id),
        page_id         INTEGER NOT NULL REFERENCES company_pages(id),
        slug            TEXT NOT NULL,
        csi_score       REAL DEFAULT 0,
        csi_label       TEXT DEFAULT 'UNSCORED',
        csi_json        TEXT,
        nii_score       REAL DEFAULT 0,
        nti_json        TEXT,
        issue_count     INTEGER DEFAULT 0,
        findings_json   TEXT,
        hedge_words     TEXT,
        hedge_count     INTEGER DEFAULT 0,
        failure_modes   TEXT,
        tilt_patterns   TEXT,
        score_version   TEXT NOT NULL DEFAULT 'unscored',
        scored_at       TIMESTAMP DEFAULT NOW(),
        is_current      BOOLEAN DEFAULT TRUE
    )
    """)

    # ── Child: company_scores (roll-up, for scorer) ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_scores (
        id              SERIAL PRIMARY KEY,
        company_id      INTEGER NOT NULL REFERENCES companies(id),
        slug            TEXT NOT NULL,
        composite_score REAL DEFAULT 0,
        composite_label TEXT DEFAULT 'UNSCORED',
        composite_nii   REAL DEFAULT 0,
        total_issues    INTEGER DEFAULT 0,
        pages_scored    INTEGER DEFAULT 0,
        page_breakdown  TEXT,
        best_page_type  TEXT,
        best_score      REAL DEFAULT 0,
        worst_page_type TEXT,
        worst_score     REAL DEFAULT 0,
        score_version   TEXT NOT NULL DEFAULT 'unscored',
        scored_at       TIMESTAMP DEFAULT NOW(),
        is_current      BOOLEAN DEFAULT TRUE
    )
    """)

    # ── Future: company_market_data ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS company_market_data (
        id              SERIAL PRIMARY KEY,
        company_id      INTEGER NOT NULL REFERENCES companies(id),
        slug            TEXT NOT NULL,
        trade_date      DATE NOT NULL,
        stock_price     REAL,
        market_cap      BIGINT,
        volume          BIGINT,
        price_change    REAL,
        content_changed_within_7d BOOLEAN DEFAULT FALSE,
        score_at_time   REAL,
        UNIQUE(company_id, trade_date)
    )
    """)

    # ── User submitted sites ──
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_submitted_sites (
        id              SERIAL PRIMARY KEY,
        slug            TEXT UNIQUE NOT NULL,
        company_name    TEXT NOT NULL,
        url             TEXT NOT NULL,
        email           TEXT,
        status          TEXT DEFAULT 'queued',
        company_id      INTEGER REFERENCES companies(id),
        submitted_at    TIMESTAMP DEFAULT NOW(),
        scraped_at      TIMESTAMP,
        scored_at       TIMESTAMP,
        error_message   TEXT
    )
    """)

    # ── Backward compat: fortune500_scores ──
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

    # Add missing columns to existing tables
    for table in ["fortune500_scores", "vc_fund_scores"]:
        for col, ctype, default in [
            ("scored_at", "TEXT", None),
            ("score_version", "TEXT", "'unscored'"),
            ("pages_scraped", "TEXT", "'[]'"),
        ]:
            try:
                dflt = f" DEFAULT {default}" if default else ""
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ctype}{dflt}")
            except Exception:
                conn.rollback()

    # Indexes
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_companies_slug ON companies(slug)",
        "CREATE INDEX IF NOT EXISTS idx_companies_rank ON companies(rank)",
        "CREATE INDEX IF NOT EXISTS idx_pages_company ON company_pages(company_id, is_current)",
        "CREATE INDEX IF NOT EXISTS idx_pages_slug ON company_pages(slug, is_current)",
        "CREATE INDEX IF NOT EXISTS idx_pscores_company ON company_page_scores(company_id, is_current)",
        "CREATE INDEX IF NOT EXISTS idx_cscores_slug ON company_scores(slug, is_current)",
    ]:
        try:
            cur.execute(stmt)
        except Exception:
            conn.rollback()

    conn.commit()


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

# ── Sentence-level noise patterns to strip from extracted text ──
NOISE_PATTERNS = [
    re.compile(r"Powered by generative AI\..*?policies\.", re.IGNORECASE | re.DOTALL),
    re.compile(r"We received a GPC signal.*?Privacy Notice\.", re.IGNORECASE | re.DOTALL),
    re.compile(r"\*?Restrictions apply\..*?terms\.", re.IGNORECASE | re.DOTALL),
    re.compile(r"Sign up today and receive.*?inbox\.", re.IGNORECASE | re.DOTALL),
    re.compile(r"Read more about .*?\.(?:\s|$)", re.IGNORECASE),
    re.compile(r"Follow us on .*?\.(?:\s|$)", re.IGNORECASE),
    re.compile(r"We use cookies.*?\.(?:\s|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"By (continuing|using|browsing).*?policy\.", re.IGNORECASE | re.DOTALL),
    re.compile(r"^\d+ [A-Z][a-z]+ (?:Drive|Street|Avenue|Blvd|Road),? [A-Z]{2} \d{5}.*$", re.MULTILINE),
]

# Lines that are just job titles (no scorable content)
TITLE_RE = re.compile(
    r"^(?:Executive |Senior |Chief |Vice |President|Global |Managing )"
    r".*(?:Officer|President|Partner|Director|Counsel|Executive)\b",
    re.IGNORECASE
)

def clean_corporate_text(text):
    """Strip noise patterns and boilerplate from extracted text."""
    if not text:
        return text
    # Remove known noise patterns
    for pat in NOISE_PATTERNS:
        text = pat.sub("", text)
    # Remove lines that are just leadership titles
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if TITLE_RE.match(stripped) and len(stripped.split()) < 15:
            continue
        cleaned.append(line)
    text = "\n".join(cleaned)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
PRICE_RE = re.compile(r"\$\d+\.?\d{0,2}")

CORPORATE_KEYWORDS = {
    "sustainability": ["sustainability", "sustainable", "esg", "environment", "climate", "carbon", "impact", "responsibility", "responsible", "csr", "social impact"],
    "investor-relations": ["investor", "investors", "shareholder", "annual report", "financial", "earnings", "ir "],
    "newsroom": ["newsroom", "press", "media", "press release", "news room", "news"],
    "values": ["values", "purpose", "mission", "culture", "principles", "commitment", "our purpose"],
    "about": ["about", "our story", "who we are", "our company", "company overview", "history", "company profile"],
    "leadership": ["leadership", "team", "executives", "management", "board", "directors", "our people", "our management"],
    "careers": ["careers", "jobs", "join us", "work with us", "life at", "hiring"],
    "ceo-letter": ["ceo", "chairman", "letter to shareholders", "annual report", "message from"],
}


def fetch_page(url, timeout=12):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text, resp.url
    except Exception as e:
        log.debug(f"  Could not fetch {url}: {e}")
        return None, None


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
            if len(t) >= 60 and not _is_junk(t):
                blocks.append(t)

    # Priority 2: all paragraphs
    if len(blocks) < 3:
        for p in soup.find_all(["p", "blockquote"]):
            t = p.get_text(separator=" ", strip=True)
            if len(t) >= 60 and not _is_junk(t):
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
            if len(t) >= 60 and not _is_junk(t):
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

    return "\n\n".join(unique)[:5000]


def classify_link(text, href):
    """Classify a link by its text and URL. Returns (page_type, confidence)."""
    text_lower = (text or "").lower().strip()
    href_lower = (href or "").lower()
    best_type, best_score = None, 0

    for ptype, keywords in CORPORATE_KEYWORDS.items():
        for kw in keywords:
            score = 0
            if kw in text_lower:
                score = 3  # Text match is strongest
            elif kw in href_lower:
                score = 2  # URL match is good
            if score > best_score:
                best_score = score
                best_type = ptype

    return best_type, best_score


# ═══════════════════════════════════════════
# METHOD A: LINK DISCOVERY (PRIMARY)
# ═══════════════════════════════════════════
def discover_links(html, base_url, final_url):
    """Parse homepage for all corporate links. Returns [(page_type, url, link_text), ...]"""
    soup = BeautifulSoup(html, "html.parser")
    base_domain = urlparse(base_url).netloc.replace("www.", "").replace("corporate.", "").replace("about.", "")

    classified = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        text = a.get_text(separator=" ", strip=True)

        # Skip junk links
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        if len(text) < 2 or len(text) > 100:
            continue

        full_url = urljoin(final_url or base_url, href)

        # Only follow same-domain or corporate subdomain links
        link_domain = urlparse(full_url).netloc.replace("www.", "").replace("corporate.", "").replace("about.", "")
        if base_domain not in link_domain and link_domain not in base_domain:
            continue

        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        ptype, score = classify_link(text, full_url)
        if ptype and score >= 2:
            classified.append((ptype, score, text, full_url))

    # Sort by confidence then type priority
    type_priority = {
        "ceo-letter": 0, "sustainability": 1, "investor-relations": 2,
        "newsroom": 3, "values": 4, "about": 5, "leadership": 6, "careers": 7
    }
    classified.sort(key=lambda x: (-x[1], type_priority.get(x[0], 99)))

    # Dedupe by type — keep the best link per type
    seen_types = set()
    top_links = []
    for ptype, score, text, url in classified:
        if ptype not in seen_types:
            seen_types.add(ptype)
            top_links.append((ptype, url, text))
        if len(top_links) >= 6:
            break

    return top_links


# ═══════════════════════════════════════════
# METHOD B: SITEMAP FALLBACK
# ═══════════════════════════════════════════
def discover_from_sitemap(base_url):
    """Parse sitemap.xml for corporate pages. Returns [(page_type, url), ...]"""
    sitemap_urls = [
        base_url.rstrip("/") + "/sitemap.xml",
        base_url.rstrip("/") + "/sitemap_index.xml",
    ]

    all_page_urls = []
    for surl in sitemap_urls:
        html, _ = fetch_page(surl, timeout=10)
        if html and "<loc>" in html:
            found = re.findall(r"<loc>(https?://[^<]+)</loc>", html)
            children = [u for u in found if "sitemap" in u.lower() and u.endswith(".xml")]
            all_page_urls.extend([u for u in found if u not in children])
            # Expand up to 3 child sitemaps
            for child in children[:3]:
                chtml, _ = fetch_page(child, timeout=10)
                if chtml:
                    all_page_urls.extend(re.findall(r"<loc>(https?://[^<]+)</loc>", chtml))
                time.sleep(0.3)
            break
        time.sleep(0.3)

    if not all_page_urls:
        return []

    # Classify and dedupe
    scored = []
    for url in all_page_urls:
        ptype, score = classify_link("", url)
        if ptype and score >= 2:
            scored.append((ptype, score, url))

    scored.sort(key=lambda x: -x[1])
    seen_types = set()
    top = []
    for ptype, score, url in scored:
        if ptype not in seen_types:
            seen_types.add(ptype)
            top.append((ptype, url))
        if len(top) >= 5:
            break

    return top


# ═══════════════════════════════════════════
# MASTER SCRAPE FUNCTION
# ═══════════════════════════════════════════
def scrape_company(base_url, subpages):
    """
    Scrape a company using Method A (Link Discovery) + Method B (Sitemap fallback).
    Returns list of (page_url, page_type, page_label, raw_text, scrape_method) tuples.
    """
    results = []
    seen_checksums = set()  # Deduplicate identical content

    def add_page(url, ptype, label, text, method):
        text = clean_corporate_text(text)
        if not text or len(text.strip()) < 30:
            return False  # Nothing left after cleaning
        cs = hashlib.md5(text.strip()[:500].encode()).hexdigest()
        if cs in seen_checksums:
            return False  # Duplicate content
        seen_checksums.add(cs)
        results.append((url, ptype, label, text, method))
        return True

    # ── Step 1: Fetch homepage ──
    log.info(f"  -> {base_url}")
    html, final_url = fetch_page(base_url)
    if not html:
        log.warning(f"  Could not fetch homepage")
        return results

    hp_text = extract_text(html)
    if len(hp_text) >= 80:
        add_page(base_url, "homepage", "Homepage", hp_text, "link_discovery")

    # ── Step 2: Company-specific subpages (hand-verified) ──
    for path in (subpages or []):
        if len(results) >= 6:
            break
        url = path if path.startswith("http") else base_url.rstrip("/") + path
        log.info(f"  -> {url}")
        page_html, _ = fetch_page(url)
        if page_html:
            text = extract_text(page_html)
            if len(text) >= 80:
                lower = path.lower()
                if any(k in lower for k in ["sustainab", "esg", "responsib", "impact"]):
                    ptype = "sustainability"
                elif any(k in lower for k in ["investor", "ir", "annual"]):
                    ptype = "investor-relations"
                elif any(k in lower for k in ["news", "press", "media"]):
                    ptype = "newsroom"
                elif any(k in lower for k in ["value", "purpose", "mission"]):
                    ptype = "values"
                elif any(k in lower for k in ["leader", "team", "executive"]):
                    ptype = "leadership"
                else:
                    ptype = "about"
                label = path.strip("/").replace("-", " ").replace("/", " > ").title()
                add_page(url, ptype, label, text, "hand_verified")
        time.sleep(0.3)

    # ── Step 3: METHOD A — Link Discovery ──
    links = discover_links(html, base_url, final_url)
    covered_types = {r[1] for r in results}

    for ptype, url, link_text in links:
        if len(results) >= 6:
            break
        if ptype in covered_types:
            continue
        log.info(f"  -> {url}")
        page_html, _ = fetch_page(url)
        if page_html:
            text = extract_text(page_html)
            if len(text) >= 80:
                if add_page(url, ptype, link_text, text, "link_discovery"):
                    covered_types.add(ptype)
        time.sleep(0.3)

    # ── Step 4: METHOD B — Sitemap fallback (only if thin) ──
    if len(results) < 2:
        log.info(f"  -> sitemap fallback (only {len(results)} pages found)")
        sitemap_pages = discover_from_sitemap(base_url)
        for ptype, url in sitemap_pages:
            if len(results) >= 6:
                break
            if ptype in covered_types:
                continue
            log.info(f"  -> {url}")
            page_html, _ = fetch_page(url)
            if page_html:
                text = extract_text(page_html)
                if len(text) >= 80:
                    if add_page(url, ptype, f"Sitemap: {ptype}", text, "sitemap"):
                        covered_types.add(ptype)
            time.sleep(0.3)

    return results


# ═══════════════════════════════════════════
# STORAGE
# ═══════════════════════════════════════════
def get_or_create_company(conn, slug, name, rank, base_url, entity_type):
    """Get or create a company record. Returns company_id."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM companies WHERE slug = %s", (slug,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO companies (slug, name, entity_type, rank, base_url)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
    """, (slug, name, entity_type, rank, base_url))
    company_id = cur.fetchone()[0]
    conn.commit()
    return company_id


def store_pages(conn, company_id, slug, pages):
    """
    Store scraped pages. Returns (new_count, unchanged_count).
    Uses checksum to detect changes.
    """
    cur = conn.cursor()
    new_count = 0
    unchanged_count = 0

    for page_url, page_type, page_label, raw_text, scrape_method in pages:
        checksum = hashlib.md5(raw_text.encode()).hexdigest()
        word_count = len(raw_text.split())
        char_count = len(raw_text)

        # Check for existing current page with same URL
        cur.execute("""
            SELECT id, checksum FROM company_pages
            WHERE slug = %s AND page_url = %s AND is_current = TRUE
            ORDER BY scraped_at DESC LIMIT 1
        """, (slug, page_url))
        existing = cur.fetchone()

        if existing and existing[1] == checksum:
            unchanged_count += 1
            continue

        # Mark old version as not current
        previous_id = None
        if existing:
            previous_id = existing[0]
            cur.execute("""
                UPDATE company_pages SET is_current = FALSE
                WHERE slug = %s AND page_url = %s AND is_current = TRUE
            """, (slug, page_url))

        # Insert new version
        cur.execute("""
            INSERT INTO company_pages
            (company_id, slug, page_url, page_type, page_label, raw_text,
             word_count, char_count, checksum, scrape_method, is_current,
             previous_id, content_changed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        """, (company_id, slug, page_url, page_type, page_label, raw_text,
              word_count, char_count, checksum, scrape_method,
              previous_id, previous_id is not None))

        new_count += 1

    conn.commit()
    return new_count, unchanged_count


def update_company_rollup(conn, company_id, slug, pages):
    """Update the parent company record with latest scrape stats."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    total_words = sum(len(p[3].split()) for p in pages)
    page_types = list(set(p[1] for p in pages))
    page_urls = [p[0] for p in pages]

    # Combine all text for backward compat — clean noise first
    combined_text = "\n\n---\n\n".join(
        f"[{p[1].upper()}]\n{clean_corporate_text(p[3])}" for p in pages
        if p[1].lower() not in ("careers", "contact", "newsroom")  # skip low-value pages
    )

    # Update companies table
    cur.execute("""
        UPDATE companies SET
            total_pages = %s,
            page_types = %s,
            total_words = %s,
            last_scraped = %s,
            updated_at = %s,
            score_version = CASE
                WHEN total_words != %s THEN 'unscored'
                ELSE score_version
            END
        WHERE id = %s
    """, (len(pages), json.dumps(page_types), total_words, now, now,
          total_words, company_id))

    # Update backward-compat fortune500_scores / vc_fund_scores
    cur.execute("SELECT entity_type, name, rank, base_url FROM companies WHERE id = %s", (company_id,))
    company = cur.fetchone()
    if company:
        entity_type, name, rank, base_url = company
        if entity_type == "vc":
            table, name_col = "vc_fund_scores", "fund_name"
        else:
            table, name_col = "fortune500_scores", "company_name"

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
        """, (slug, name, rank, base_url, combined_text,
              now.isoformat(), json.dumps(page_urls)))

    conn.commit()


# ═══════════════════════════════════════════
# PROCESS ONE ENTITY
# ═══════════════════════════════════════════
def process_entity(conn, slug, name, rank, base_url, subpages, entity_type):
    """Scrape one company. No scoring."""
    log.info(f"[{rank}] {name}")

    # Get or create company record
    company_id = get_or_create_company(conn, slug, name, rank, base_url, entity_type)

    # Scrape pages
    pages = scrape_company(base_url, subpages)

    if not pages:
        log.warning(f"  SKIP {name}: no pages with sufficient text")
        return False

    # Store individual pages
    new_count, unchanged_count = store_pages(conn, company_id, slug, pages)

    # Update roll-ups
    update_company_rollup(conn, company_id, slug, pages)

    total_words = sum(len(p[3].split()) for p in pages)
    page_types = set(p[1] for p in pages)
    methods = set(p[4] for p in pages)

    log.info(f"  OK {name}: {len(pages)} pages ({new_count} new, {unchanged_count} unchanged) | "
             f"{total_words} words | types: {', '.join(page_types)} | via: {', '.join(methods)}")
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
# ENTRY POINT
# ═══════════════════════════════════════════
def lambda_handler(event, context):
    target = event.get("target", "both")
    limit = event.get("limit", 999)
    conn = get_conn()
    ensure_tables(conn)
    results = []

    if target in ("f500", "both"):
        start = event.get('start', 1)
        companies = [(s,n,r,u,sub) for s,n,r,u,sub in COMPANIES if r >= start][:limit]
        ok = 0
        for slug, name, rank, url, subs in companies:
            try:
                # Reconnect if connection died
                try:
                    conn.isolation_level
                except Exception:
                    log.warning("  DB reconnecting...")
                    try: conn.close()
                    except: pass
                    conn = get_conn()

                if process_entity(conn, slug, name, rank, url, subs, "f500"):
                    ok += 1
                time.sleep(1)
            except Exception as e:
                log.error(f"Error {name}: {e}")
                try:
                    conn.rollback()
                except Exception:
                    try: conn.close()
                    except: pass
                    conn = get_conn()
        results.append(f"F500: {ok}/{len(companies)} scraped")

    if target in ("vc", "both"):
        funds = VC_FUNDS[:min(limit, len(VC_FUNDS))]
        ok = 0
        for slug, name, rank, url, subs in funds:
            try:
                try:
                    conn.isolation_level
                except Exception:
                    try: conn.close()
                    except: pass
                    conn = get_conn()

                if process_entity(conn, slug, name, rank, url, subs, "vc"):
                    ok += 1
                time.sleep(1)
            except Exception as e:
                log.error(f"Error {name}: {e}")
                try:
                    conn.rollback()
                except Exception:
                    try: conn.close()
                    except: pass
                    conn = get_conn()
        results.append(f"VC: {ok}/{len(funds)} scraped")

    conn.close()
    msg = "Done. " + " | ".join(results)
    log.info(msg)
    return {"statusCode": 200, "body": msg}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Artifact Zero F500 Scraper v3")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--target", choices=["f500", "vc", "both"], default="both")
    p.add_argument("--start", type=int, default=1, help="Start at rank")
    a = p.parse_args()
    print(lambda_handler({"target": a.target, "limit": a.limit, "start": a.start}, None))
