"""
Artifact Zero — Fortune 500 Scraper Lambda
Runs daily via EventBridge. Fully self-contained.
Scrapes homepages, scores with built-in NTI engine, writes to PostgreSQL.
No dependency on the web app or API.
"""
import os
import re
import json
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests
import psycopg2
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("f500-lambda")

# ═══════════════════════════════════════════
# DATABASE — pull from SSM at runtime
# ═══════════════════════════════════════════
def get_db_url():
    """Fetch DATABASE_URL from AWS SSM Parameter Store."""
    import boto3
    ssm = boto3.client("ssm", region_name=os.getenv("AWS_REGION", "us-east-1"))
    resp = ssm.get_parameter(Name="/artifact-zero/DATABASE_URL", WithDecryption=True)
    return resp["Parameter"]["Value"]

def get_conn():
    url = os.getenv("DATABASE_URL") or get_db_url()
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
        last_changed TEXT
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
        last_changed TEXT
    )
    """)
    conn.commit()


# ═══════════════════════════════════════════
# NTI SCORING ENGINE — imports from app.py (v2 weighted scoring)
# ═══════════════════════════════════════════
from app import (
    detect_l0_constraints, classify_tilt, detect_udds, detect_dce, detect_cca,
    detect_downstream_before_constraint, detect_l2_framing, compute_nii,
    L0_CONSTRAINT_MARKERS, BOUNDARY_ABSENCE_MARKERS, NARRATIVE_STABILIZATION_MARKERS,
    L2_HEDGE, L2_REASSURE, L2_CATEGORY_BLEND
)

def _contains_any(text_lc, needles):
    for n in needles:
        if n in text_lc:
            return True
    return False

def split_sentences(text):
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if not t: return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if p.strip()]

def tokenize(text):
    return [t.lower() for t in re.findall(r"[A-Za-z0-9']+", text or "")]

def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb: return 1.0
    if not sa or not sb: return 0.0
    return round(len(sa & sb) / len(sa | sb), 3)

def detect_boundary_absence(text):
    t = (text or "").lower()
    return any(m in t for m in BOUNDARY_ABSENCE_MARKERS) or any(m in t for m in L2_CATEGORY_BLEND)

def detect_narrative_stabilization(text):
    t = (text or "").lower()
    return any(m in t for m in NARRATIVE_STABILIZATION_MARKERS) or any(m in t for m in L2_REASSURE)

def detect_objective_drift(text):
    sents = split_sentences(text)
    if len(sents) < 2:
        return {"drift_score": 0.0, "coherence": 1.0}
    first_tokens = tokenize(sents[0])
    all_tokens = tokenize(text)
    sim = jaccard(first_tokens, all_tokens)
    return {"drift_score": round(1.0 - sim, 3), "coherence": round(sim, 3)}


def score_text(text):
    """Run full NTI v2 scoring. Uses 5-dimension weighted model from app.py."""
    l0 = detect_l0_constraints(text)
    tilt = classify_tilt(text)
    udds = detect_udds("", text, l0)
    dce = detect_dce(text, l0)
    cca = detect_cca("", text)
    dbc = detect_downstream_before_constraint("", text, l0)
    nii = compute_nii("", text, l0, dbc, tilt)
    framing = detect_l2_framing(text)
    drift = detect_objective_drift(text)
    boundary = detect_boundary_absence(text)
    narrative = detect_narrative_stabilization(text)

    dominance = []
    if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]: dominance.append("CCA")
    if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]: dominance.append("UDDS")
    if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]: dominance.append("DCE")
    if not dominance: dominance = ["NONE"]

    return {
        "score": {
            "nii": nii["nii_score"],  # 0-100 integer
            "nii_label": nii["nii_label"],
            "components": {
                "q1": nii.get("d1_constraint_density"),
                "q2": nii.get("d2_ask_architecture"),
                "q3": nii.get("d3_enforcement_integrity"),
                "q4": nii.get("d4_tilt_resistance"),
                "d5": nii.get("d5_failure_mode_severity")
            }
        },
        "failure_modes": {"UDDS": udds["udds_state"], "DCE": dce["dce_state"], "CCA": cca["cca_state"],
                          "dominance": dominance,
                          "UDDS_detail": udds, "DCE_detail": dce, "CCA_detail": cca},
        "tilt": {"tags": tilt, "count": len(tilt)},
        "l2_framing": framing,
        "drift": drift,
        "structural_signals": {
            "constraints_found": len(l0),
            "constraints_list": l0[:10],
            "downstream_before_constraint": dbc,
            "boundary_absence": boundary,
            "narrative_stabilization": narrative,
            "hedge_count": len(framing.get("hedge_markers", [])),
            "reassurance_count": len(framing.get("reassurance_markers", [])),
            "category_blend_count": len(framing.get("category_blend_markers", []))
        }
    }


# ═══════════════════════════════════════════
# SCRAPER
# ═══════════════════════════════════════════
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
]

VC_FUNDS = [
    ("sequoia", "Sequoia Capital", 1, "https://www.sequoiacap.com"),
    ("a16z", "Andreessen Horowitz", 2, "https://a16z.com"),
    ("accel", "Accel", 3, "https://www.accel.com"),
    ("general-catalyst", "General Catalyst", 4, "https://www.generalcatalyst.com"),
    ("benchmark", "Benchmark", 5, "https://www.benchmark.com"),
    ("kleiner-perkins", "Kleiner Perkins", 6, "https://www.kleinerperkins.com"),
    ("bessemer", "Bessemer Venture Partners", 7, "https://www.bvp.com"),
    ("lightspeed", "Lightspeed Venture Partners", 8, "https://lsvp.com"),
    ("founders-fund", "Founders Fund", 9, "https://foundersfund.com"),
    ("khosla", "Khosla Ventures", 10, "https://www.khoslaventures.com"),
    ("tiger-global", "Tiger Global Management", 11, "https://www.tigerglobal.com"),
    ("index-ventures", "Index Ventures", 12, "https://www.indexventures.com"),
    ("greylock", "Greylock Partners", 13, "https://greylock.com"),
    ("nea", "New Enterprise Associates", 14, "https://www.nea.com"),
    ("insight-partners", "Insight Partners", 15, "https://www.insightpartners.com"),
    ("500-global", "500 Global", 16, "https://500.co"),
    ("usv", "Union Square Ventures", 17, "https://www.usv.com"),
    ("first-round", "First Round Capital", 18, "https://firstround.com"),
    ("battery", "Battery Ventures", 19, "https://www.battery.com"),
    ("ivp", "IVP", 20, "https://www.ivp.com"),
    ("gv", "GV (Google Ventures)", 21, "https://www.gv.com"),
    ("spark-capital", "Spark Capital", 22, "https://www.sparkcapital.com"),
    ("ribbit", "Ribbit Capital", 23, "https://ribbitcap.com"),
    ("canaan", "Canaan Partners", 24, "https://www.canaan.com"),
    ("redpoint", "Redpoint Ventures", 25, "https://www.redpoint.com"),
    ("coatue", "Coatue Management", 26, "https://www.coatue.com"),
    ("lux-capital", "Lux Capital", 27, "https://www.luxcapital.com"),
    ("felicis", "Felicis Ventures", 28, "https://www.felicis.com"),
    ("thrive-capital", "Thrive Capital", 29, "https://thrivecap.com"),
    ("fifth-wall", "Fifth Wall", 30, "https://fifthwall.com"),
]

def extract_visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    # Remove non-content elements
    for tag in soup(["script", "style", "noscript", "header", "nav", "footer", "iframe", "svg", "form", "button", "select", "option", "input"]):
        tag.decompose()

    # Strategy 1: Pull from paragraph-like tags first
    para_tags = soup.find_all(["p", "article", "blockquote", "figcaption"])
    paragraphs = []
    for tag in para_tags:
        text = tag.get_text(separator=" ", strip=True)
        if len(text) >= 80 and not _is_junk(text):
            paragraphs.append(text)

    # Strategy 2: If not enough paragraph content, pull from divs/sections with real sentences
    if len(paragraphs) < 3:
        for tag in soup.find_all(["div", "section", "main"]):
            text = tag.get_text(separator=" ", strip=True)
            # Only keep blocks that look like actual writing (has periods = sentences)
            if len(text) >= 80 and text.count(".") >= 1 and not _is_junk(text):
                # Avoid duplicates of content already captured
                if not any(text[:60] in p for p in paragraphs):
                    paragraphs.append(text)

    combined = "\n".join(paragraphs)
    # Deduplicate: if a longer block contains a shorter one, keep the longer
    return combined[:5000] if len(combined) > 5000 else combined


JUNK_PATTERNS = re.compile(r"^\$[\d,.]+$|^\d+\s*(ct|oz|lb|ml|pack|count)|add to cart|buy now|shop now|sign in|log in|subscribe|©|cookie|privacy policy", re.IGNORECASE)
PRICE_RE = re.compile(r"\$\d+\.\d{2}")

def _is_junk(text):
    """Filter out product listings, prices, nav items, legal boilerplate."""
    # Too many prices = product listing
    if len(PRICE_RE.findall(text)) >= 3:
        return True
    # Mostly short fragments joined together
    words = text.split()
    if len(words) < 10:
        return True
    # Junk patterns
    if JUNK_PATTERNS.search(text):
        return True
    return False

def fetch_homepage(url, timeout=15):
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

def count_issues(score_data):
    count = 0
    fm = score_data.get("failure_modes", {})
    for key in ["UDDS", "DCE", "CCA"]:
        val = fm.get(key, "")
        if "FALSE" not in str(val):
            count += 1
    tilt = score_data.get("tilt", {})
    count += tilt.get("count", 0)
    return count

def process_company(conn, slug, name, rank, url):
    return process_entity(conn, "fortune500_scores", "company_name", slug, name, rank, url)

def process_vc_fund(conn, slug, name, rank, url):
    return process_entity(conn, "vc_fund_scores", "fund_name", slug, name, rank, url)

def process_entity(conn, table, name_col, slug, name, rank, url):
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
    nii = score_data["score"]["nii"]
    issues = count_issues(score_data)
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO {table} (slug, {name_col}, rank, url, homepage_copy, score_json, nii_score, issue_count, last_checked, last_changed)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET
            homepage_copy=EXCLUDED.homepage_copy, score_json=EXCLUDED.score_json,
            nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count,
            last_checked=EXCLUDED.last_checked,
            last_changed=CASE WHEN {table}.nii_score != EXCLUDED.nii_score THEN EXCLUDED.last_changed ELSE {table}.last_changed END
    """, (slug, name, rank, url, copy, json.dumps(score_data), nii, issues, now, now))
    conn.commit()
    log.info(f"  Score: {nii:.2f} | Issues: {issues} | Copy: {len(copy)} chars")
    return True


# ═══════════════════════════════════════════
# LAMBDA HANDLER
# ═══════════════════════════════════════════
def lambda_handler(event, context):
    """AWS Lambda entry point. Triggered by EventBridge daily.
    Pass {"target": "vc"} to only run VC funds, {"target": "f500"} for Fortune 500 only.
    Default: runs both.
    """
    target = event.get("target", "both")
    limit = event.get("limit", 999)
    
    conn = get_conn()
    ensure_table(conn)
    
    results = []
    
    if target in ("f500", "both"):
        companies = COMPANIES[:min(limit, len(COMPANIES))]
        success = 0
        for slug, name, rank, url in companies:
            try:
                if process_company(conn, slug, name, rank, url):
                    success += 1
                time.sleep(2)
            except Exception as e:
                log.error(f"Error processing {name}: {e}")
        results.append(f"F500: {success}/{len(companies)}")
    
    if target in ("vc", "both"):
        funds = VC_FUNDS[:min(limit, len(VC_FUNDS))]
        success = 0
        for slug, name, rank, url in funds:
            try:
                if process_vc_fund(conn, slug, name, rank, url):
                    success += 1
                time.sleep(2)
            except Exception as e:
                log.error(f"Error processing {name}: {e}")
        results.append(f"VC: {success}/{len(funds)}")
    
    conn.close()
    result = "Done. " + " | ".join(results)
    log.info(result)
    return {"statusCode": 200, "body": result}


# Local testing
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    result = lambda_handler({"limit": args.limit}, None)
    print(result)
