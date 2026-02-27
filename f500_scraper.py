"""
Artifact Zero — Fortune 500 + VC Fund Multi-Page Scraper
=========================================================
Scrapes CORPORATE pages (About, Mission, Leadership, Contact, Investor Relations)
NOT shopping homepages. Combines text from multiple pages per company for
richer, more differentiated NTI scores.

Run: python f500_scraper.py --limit 5
Lambda: lambda_handler({"target": "both", "limit": 50})
"""
import os
import re
import json
import time
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("f500")


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

def ensure_table(conn):
    cur = conn.cursor()
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
        pages_scraped TEXT DEFAULT '[]'
    )""")
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
        pages_scraped TEXT DEFAULT '[]'
    )""")
    conn.commit()


# ═══════════════════════════════════════════
# NTI SCORING
# ═══════════════════════════════════════════
SCORE_URL = os.getenv("SCORE_URL", "http://localhost:5000/nti")

def score_text(text):
    try:
        resp = requests.post(SCORE_URL, json={"text": text}, timeout=30)
        return resp.json()
    except Exception as e:
        log.warning(f"Scoring failed: {e}")
        return None


# ═══════════════════════════════════════════
# COMPANY LIST — CORPORATE URLS
# ═══════════════════════════════════════════
# Format: (slug, name, rank, corporate_base_url, [subpage_paths])

COMPANIES = [
    ("walmart", "Walmart", 1, "https://corporate.walmart.com", ["/about", "/purpose", "/leadership"]),
    ("amazon", "Amazon", 2, "https://www.aboutamazon.com", ["/about-us", "/news"]),
    ("apple", "Apple", 3, "https://www.apple.com", ["/leadership", "/environment", "/privacy"]),
    ("unitedhealth", "UnitedHealth Group", 4, "https://www.unitedhealthgroup.com", ["/about", "/people-and-businesses"]),
    ("berkshire-hathaway", "Berkshire Hathaway", 5, "https://www.berkshirehathaway.com", []),
    ("cvs-health", "CVS Health", 6, "https://www.cvshealth.com", ["/about", "/about/our-strategy"]),
    ("exxonmobil", "ExxonMobil", 7, "https://corporate.exxonmobil.com", ["/about", "/who-we-are"]),
    ("alphabet", "Alphabet (Google)", 8, "https://about.google", ["/intl/en/", "/our-story/"]),
    ("mckesson", "McKesson", 9, "https://www.mckesson.com", ["/about", "/about/our-company"]),
    ("amerisourcebergen", "Cencora", 10, "https://www.cencora.com", ["/about", "/about-us"]),
    ("costco", "Costco", 11, "https://www.costco.com/about.html", ["/sustainability-introduction.html"]),
    ("microsoft", "Microsoft", 12, "https://www.microsoft.com/en-us/about", ["/", "/leadership"]),
    ("cigna", "Cigna", 13, "https://www.cigna.com", ["/about-us", "/about-us/company-profile"]),
    ("cardinal-health", "Cardinal Health", 14, "https://www.cardinalhealth.com", ["/about"]),
    ("chevron", "Chevron", 15, "https://www.chevron.com", ["/who-we-are", "/sustainability"]),
    ("marathon-petroleum", "Marathon Petroleum", 16, "https://www.marathonpetroleum.com", ["/About/"]),
    ("jpmorgan-chase", "JPMorgan Chase", 17, "https://www.jpmorganchase.com", ["/about"]),
    ("fannie-mae", "Fannie Mae", 18, "https://www.fanniemae.com", ["/about-us"]),
    ("centene", "Centene", 19, "https://www.centene.com", ["/who-we-are/about-us"]),
    ("kroger", "Kroger", 20, "https://www.thekrogerco.com", ["/about-kroger"]),
    ("walgreens", "Walgreens Boots Alliance", 21, "https://www.walgreensbootsalliance.com", ["/about"]),
    ("bank-of-america", "Bank of America", 22, "https://about.bankofamerica.com", ["/our-company"]),
    ("phillips-66", "Phillips 66", 23, "https://www.phillips66.com", ["/about"]),
    ("ford", "Ford Motor", 24, "https://corporate.ford.com", ["/about", "/leadership"]),
    ("general-motors", "General Motors", 25, "https://www.gm.com", ["/company/about-gm"]),
    ("verizon", "Verizon", 26, "https://www.verizon.com/about", ["/our-company"]),
    ("att", "AT&T", 27, "https://about.att.com", ["/story"]),
    ("comcast", "Comcast", 28, "https://corporate.comcast.com", ["/company"]),
    ("meta", "Meta Platforms", 29, "https://about.meta.com", ["/company-info"]),
    ("elevance-health", "Elevance Health", 30, "https://www.elevancehealth.com", ["/who-we-are"]),
    ("home-depot", "Home Depot", 31, "https://corporate.homedepot.com", ["/about/"]),
    ("valero", "Valero Energy", 32, "https://www.valero.com", ["/about"]),
    ("dell", "Dell Technologies", 33, "https://www.dell.com/en-us/dt/corporate/about-us.htm", []),
    ("unitedparcel", "UPS", 34, "https://about.ups.com", ["/who-we-are"]),
    ("target", "Target", 35, "https://corporate.target.com", ["/about"]),
    ("pfizer", "Pfizer", 36, "https://www.pfizer.com", ["/about", "/about/leadership"]),
    ("state-farm", "State Farm", 37, "https://www.statefarm.com/about-us", ["/company-overview"]),
    ("johnson-johnson", "Johnson & Johnson", 38, "https://www.jnj.com", ["/about-jnj"]),
    ("humana", "Humana", 39, "https://www.humana.com/about", ["/leadership"]),
    ("freddie-mac", "Freddie Mac", 40, "https://www.freddiemac.com", ["/about"]),
    ("procter-gamble", "Procter & Gamble", 41, "https://us.pg.com", ["/our-brands", "/our-company"]),
    ("tesla", "Tesla", 42, "https://www.tesla.com", ["/about", "/impact"]),
    ("general-electric", "GE Aerospace", 43, "https://www.geaerospace.com", ["/company/about-us"]),
    ("disney", "Walt Disney", 44, "https://thewaltdisneycompany.com", ["/about/"]),
    ("raytheon", "RTX", 45, "https://www.rtx.com", ["/who-we-are"]),
    ("abbvie", "AbbVie", 46, "https://www.abbvie.com", ["/about"]),
    ("caterpillar", "Caterpillar", 47, "https://www.caterpillar.com", ["/company"]),
    ("ibm", "IBM", 48, "https://www.ibm.com", ["/about"]),
    ("lockheed-martin", "Lockheed Martin", 49, "https://www.lockheedmartin.com", ["/about"]),
    ("boeing", "Boeing", 50, "https://www.boeing.com", ["/company"]),
]

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
    r"|^[\d,.]+ reviews?$|^\d+ stars?$",
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
            # p0003: raised min from 40 to 80 for quality paragraphs
            if len(t) >= 80 and not _is_junk(t):
                blocks.append(t)

    # Priority 2: all paragraphs
    if len(blocks) < 3:
        for p in soup.find_all(["p", "blockquote"]):
            t = p.get_text(separator=" ", strip=True)
            # p0003: raised min from 40 to 80 for quality paragraphs
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
            # p0003: raised min from 40 to 80 for quality paragraphs
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


def scrape_multi_page(base_url, subpages):
    """Scrape base URL + subpages, combine text. Returns (text, [urls_scraped])."""
    all_text = []
    pages_scraped = []

    # Base URL
    log.info(f"  → {base_url}")
    html = fetch_page(base_url)
    if html:
        text = extract_text(html)
        if len(text) >= 50:
            all_text.append(f"[HOMEPAGE]\n{text}")
            pages_scraped.append(base_url)

    # Subpages
    for path in (subpages or []):
        if path.startswith("http"):
            url = path
        else:
            url = base_url.rstrip("/") + path
        log.info(f"  → {url}")
        html = fetch_page(url)
        if html:
            text = extract_text(html)
            if len(text) >= 50:
                label = path.strip("/").upper().replace("-", " ") or "PAGE"
                all_text.append(f"[{label}]\n{text}")
                pages_scraped.append(url)
        time.sleep(1)

    # Fallback if too little content
    if len(all_text) < 2:
        for fb in ["/about", "/about-us", "/company", "/who-we-are"]:
            if fb in (subpages or []):
                continue
            url = base_url.rstrip("/") + fb
            log.info(f"  → fallback {url}")
            html = fetch_page(url)
            if html:
                text = extract_text(html)
                if len(text) >= 50:
                    all_text.append(f"[{fb.strip('/').upper()}]\n{text}")
                    pages_scraped.append(url)
                    break
            time.sleep(1)

    combined = "\n\n---\n\n".join(all_text)
    return combined[:8000], pages_scraped


# ═══════════════════════════════════════════
# SCORING + STORAGE
# ═══════════════════════════════════════════
def count_issues(score_data):
    count = 0
    fm = score_data.get("parent_failure_modes") or score_data.get("failure_modes", {})
    if isinstance(fm, dict):
        for key in ["UDDS", "DCE", "CCA"]:
            val = fm.get(key)
            if isinstance(val, str) and "FALSE" not in val:
                count += 1
            elif isinstance(val, dict):
                state = str(val.get(f"{key.lower()}_state", ""))
                if "CONFIRMED" in state or "PROBABLE" in state:
                    count += 1
    tilt = score_data.get("tilt_taxonomy") or score_data.get("tilt", {})
    if isinstance(tilt, list):
        count += len(tilt)
    elif isinstance(tilt, dict):
        count += tilt.get("count", 0)
    return count


def get_nii(score_data):
    if "nii" in score_data:
        nii = score_data["nii"]
        if isinstance(nii, dict):
            return nii.get("nii_score", 0)
        return nii
    if "score" in score_data and isinstance(score_data["score"], dict):
        return score_data["score"].get("nii", 0)
    return 0


def process_entity(conn, table, name_col, slug, name, rank, base_url, subpages):
    log.info(f"[{rank}] {name}")

    combined_text, pages_scraped = scrape_multi_page(base_url, subpages)
    if len(combined_text) < 100:
        log.warning(f"  SKIP {name}: insufficient text ({len(combined_text)} chars)")
        return False

    score_data = score_text(combined_text)
    if not score_data or "error" in score_data:
        log.warning(f"  SKIP {name}: scoring error")
        return False

    nii_raw = get_nii(score_data)
    # p0003: always normalize to 0-100 scale
    nii = round(nii_raw * 100) if isinstance(nii_raw, (int, float)) and 0 < nii_raw <= 1.0 else round(nii_raw)
    issues = count_issues(score_data)
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.cursor()
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS pages_scraped TEXT DEFAULT '[]'")
        conn.commit()
    except Exception:
        conn.rollback()

    cur.execute(f"""
        INSERT INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed, pages_scraped)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET
            homepage_copy=EXCLUDED.homepage_copy, score_json=EXCLUDED.score_json,
            nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count,
            last_checked=EXCLUDED.last_checked, pages_scraped=EXCLUDED.pages_scraped,
            last_changed=CASE WHEN {table}.nii_score != EXCLUDED.nii_score THEN EXCLUDED.last_changed ELSE {table}.last_changed END
    """, (slug, name, rank, base_url, combined_text, json.dumps(score_data),
          nii, issues, now, now, json.dumps(pages_scraped)))
    conn.commit()

    log.info(f"  ✓ Score: {nii} | Issues: {issues} | Pages: {len(pages_scraped)} | {len(combined_text)} chars")
    return True


# ═══════════════════════════════════════════
# ENTRY
# ═══════════════════════════════════════════
def lambda_handler(event, context):
    target = event.get("target", "both")
    limit = event.get("limit", 999)
    conn = get_conn()
    ensure_table(conn)
    results = []

    if target in ("f500", "both"):
        companies = COMPANIES[:min(limit, len(COMPANIES))]
        ok = 0
        for slug, name, rank, url, subs in companies:
            try:
                if process_entity(conn, "fortune500_scores", "company_name", slug, name, rank, url, subs):
                    ok += 1
                time.sleep(2)
            except Exception as e:
                log.error(f"Error {name}: {e}")
        results.append(f"F500: {ok}/{len(companies)}")

    if target in ("vc", "both"):
        funds = VC_FUNDS[:min(limit, len(VC_FUNDS))]
        ok = 0
        for slug, name, rank, url, subs in funds:
            try:
                if process_entity(conn, "vc_fund_scores", "fund_name", slug, name, rank, url, subs):
                    ok += 1
                time.sleep(2)
            except Exception as e:
                log.error(f"Error {name}: {e}")
        results.append(f"VC: {ok}/{len(funds)}")

    conn.close()
    msg = "Done. " + " | ".join(results)
    log.info(msg)
    return {"statusCode": 200, "body": msg}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--target", choices=["f500", "vc", "both"], default="both")
    a = p.parse_args()
    print(lambda_handler({"target": a.target, "limit": a.limit}, None))
