"""
Fortune 500 Homepage Scraper & Scorer
Run daily: python fortune500_scraper.py
Pulls visible text from each company's homepage, scores via NTI, stores in DB.
"""
import os
import sys
import json
import time
import re
import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# Add parent dir so we can import local modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("f500")

# The NTI scoring endpoint — hit our own API
SCORE_URL = os.getenv("SCORE_URL", "http://localhost:5000/nti")

# Fortune 500 seed list — top companies by revenue (2025)
# slug, company_name, rank, url
COMPANIES = [
    ("walmart", "Walmart", 1, "https://www.walmart.com"),
    ("amazon", "Amazon", 2, "https://www.amazon.com"),
    ("apple", "Apple", 3, "https://www.apple.com"),
    ("unitedhealth", "UnitedHealth Group", 4, "https://www.unitedhealthgroup.com"),
    ("berkshire-hathaway", "Berkshire Hathaway", 5, "https://www.berkshirehathaway.com"),
    ("cvs-health", "CVS Health", 6, "https://www.cvshealth.com"),
    ("exxonmobil", "ExxonMobil", 7, "https://corporate.exxonmobil.com"),
    ("alphabet", "Alphabet (Google)", 8, "https://about.google"),
    ("mckesson", "McKesson", 9, "https://www.mckesson.com"),
    ("amerisourcebergen", "Cencora", 10, "https://www.cencora.com"),
    ("costco", "Costco", 11, "https://www.costco.com"),
    ("microsoft", "Microsoft", 12, "https://www.microsoft.com"),
    ("cigna", "Cigna", 13, "https://www.cigna.com"),
    ("cardinal-health", "Cardinal Health", 14, "https://www.cardinalhealth.com"),
    ("chevron", "Chevron", 15, "https://www.chevron.com"),
    ("marathon-petroleum", "Marathon Petroleum", 16, "https://www.marathonpetroleum.com"),
    ("jpmorgan-chase", "JPMorgan Chase", 17, "https://www.jpmorganchase.com"),
    ("fannie-mae", "Fannie Mae", 18, "https://www.fanniemae.com"),
    ("centene", "Centene", 19, "https://www.centene.com"),
    ("kroger", "Kroger", 20, "https://www.kroger.com"),
    ("walgreens", "Walgreens Boots Alliance", 21, "https://www.walgreensbootsalliance.com"),
    ("bank-of-america", "Bank of America", 22, "https://www.bankofamerica.com"),
    ("phillips-66", "Phillips 66", 23, "https://www.phillips66.com"),
    ("ford", "Ford Motor", 24, "https://www.ford.com"),
    ("general-motors", "General Motors", 25, "https://www.gm.com"),
    ("verizon", "Verizon", 26, "https://www.verizon.com"),
    ("att", "AT&T", 27, "https://about.att.com"),
    ("comcast", "Comcast", 28, "https://corporate.comcast.com"),
    ("meta", "Meta Platforms", 29, "https://about.meta.com"),
    ("elevance-health", "Elevance Health", 30, "https://www.elevancehealth.com"),
    ("home-depot", "Home Depot", 31, "https://www.homedepot.com"),
    ("valero", "Valero Energy", 32, "https://www.valero.com"),
    ("dell", "Dell Technologies", 33, "https://www.dell.com"),
    ("unitedparcel", "UPS", 34, "https://about.ups.com"),
    ("target", "Target", 35, "https://corporate.target.com"),
    ("pfizer", "Pfizer", 36, "https://www.pfizer.com"),
    ("state-farm", "State Farm", 37, "https://www.statefarm.com"),
    ("johnson-johnson", "Johnson & Johnson", 38, "https://www.jnj.com"),
    ("humana", "Humana", 39, "https://www.humana.com"),
    ("freddie-mac", "Freddie Mac", 40, "https://www.freddiemac.com"),
    ("procter-gamble", "Procter & Gamble", 41, "https://us.pg.com"),
    ("tesla", "Tesla", 42, "https://www.tesla.com"),
    ("general-electric", "GE Aerospace", 43, "https://www.geaerospace.com"),
    ("disney", "Walt Disney", 44, "https://thewaltdisneycompany.com"),
    ("raytheon", "RTX", 45, "https://www.rtx.com"),
    ("abbvie", "AbbVie", 46, "https://www.abbvie.com"),
    ("caterpillar", "Caterpillar", 47, "https://www.caterpillar.com"),
    ("ibm", "IBM", 48, "https://www.ibm.com"),
    ("lockheed-martin", "Lockheed Martin", 49, "https://www.lockheedmartin.com"),
    ("boeing", "Boeing", 50, "https://www.boeing.com"),
    # ... extend to 500 — this is the seed. Full list added via CSV import.
]


def extract_visible_text(html):
    """Extract visible text from HTML, skip scripts/styles/nav."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove non-content elements
    for tag in soup(["script", "style", "noscript", "header", "nav", "footer", "iframe", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Clean up: collapse whitespace, remove very short lines
    lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 20]
    # Take first ~2000 chars of meaningful content (hero + first few sections)
    combined = "\n".join(lines)
    if len(combined) > 3000:
        combined = combined[:3000]
    return combined


def fetch_homepage(url, timeout=15):
    """Fetch homepage HTML with browser-like headers."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
        return None


def score_text(text):
    """Score text through the NTI engine."""
    try:
        resp = requests.post(SCORE_URL, json={"text": text}, timeout=30)
        return resp.json()
    except Exception as e:
        log.warning(f"Scoring failed: {e}")
        return None


def count_issues(score_data):
    """Count total issues from score response."""
    count = 0
    fm = score_data.get("failure_modes") or score_data.get("parent_failure_modes", {})
    if isinstance(fm, dict):
        for key in ["UDDS", "DCE", "CCA"]:
            val = fm.get(key)
            if isinstance(val, str) and "FALSE" not in val:
                count += 1
            elif isinstance(val, dict):
                state = val.get(f"{key.lower()}_state", val.get(f"{key}_state", ""))
                if "FALSE" not in str(state):
                    count += 1
    tilt = score_data.get("tilt", {})
    if isinstance(tilt, dict):
        count += tilt.get("count", 0)
    elif isinstance(tilt, list):
        count += len(tilt)
    return count


def get_nii(score_data):
    """Extract NII score from response."""
    # Handle different response shapes
    if "score" in score_data and isinstance(score_data["score"], dict):
        return score_data["score"].get("nii", 0)
    if "nii" in score_data:
        nii = score_data["nii"]
        if isinstance(nii, dict):
            return nii.get("nii_score", 0)
        return nii
    return 0


def process_company(slug, name, rank, url):
    """Fetch, extract, score, store for one company."""
    log.info(f"[{rank}] {name} — {url}")

    html = fetch_homepage(url)
    if not html:
        log.warning(f"  Skipped: could not fetch")
        return False

    copy = extract_visible_text(html)
    if len(copy) < 50:
        log.warning(f"  Skipped: insufficient text ({len(copy)} chars)")
        return False

    score_data = score_text(copy)
    if not score_data or "error" in score_data:
        log.warning(f"  Skipped: scoring error")
        return False

    nii = get_nii(score_data)
    issues = count_issues(score_data)
    now = datetime.now(timezone.utc).isoformat()

    # Store in DB
    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute("""
            INSERT INTO fortune500_scores (slug, company_name, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (slug) DO UPDATE SET
                homepage_copy=EXCLUDED.homepage_copy, score_json=EXCLUDED.score_json,
                nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count,
                last_checked=EXCLUDED.last_checked,
                last_changed=CASE WHEN fortune500_scores.nii_score != EXCLUDED.nii_score THEN EXCLUDED.last_changed ELSE fortune500_scores.last_changed END
        """, (slug, name, rank, url, copy, json.dumps(score_data), nii, issues, now, now))
    else:
        cur.execute("""
            INSERT OR REPLACE INTO fortune500_scores (slug, company_name, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (slug, name, rank, url, copy, json.dumps(score_data), nii, issues, now, now))
    conn.commit()
    conn.close()

    log.info(f"  Score: {nii:.2f} | Issues: {issues} | Copy: {len(copy)} chars")
    return True


def run(limit=None):
    """Process all companies."""
    database.db_init()
    companies = COMPANIES[:limit] if limit else COMPANIES
    success = 0
    for slug, name, rank, url in companies:
        try:
            if process_company(slug, name, rank, url):
                success += 1
            time.sleep(2)  # Be polite
        except Exception as e:
            log.error(f"Error processing {name}: {e}")
    log.info(f"Done. {success}/{len(companies)} companies scored.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Only process first N companies")
    args = parser.parse_args()
    run(limit=args.limit)
