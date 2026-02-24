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
# NTI SCORING ENGINE — self-contained
# ═══════════════════════════════════════════
WORD_RE = re.compile(r"[A-Za-z0-9']+")
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of", "in", "on", "for", "with", "as",
    "we", "you", "they", "it", "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
    "those", "will", "would", "should", "can", "could", "may", "might", "do", "does", "did", "at", "by",
    "from", "into", "over", "under", "before", "after", "about", "because", "while", "just", "now", "today"
}

def tokenize(text):
    return [t.lower() for t in WORD_RE.findall(text or "")]

def normalize_space(text):
    return re.sub(r"\s+", " ", (text or "")).strip()

def split_sentences(text):
    t = normalize_space(text)
    if not t: return []
    parts = re.split(r"(?<=[.!?])\s+", t)
    return [p.strip() for p in parts if p.strip()]

def jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb: return 1.0
    if not sa or not sb: return 0.0
    return round(len(sa & sb) / len(sa | sb), 3)

# Constraint markers
L0_CONSTRAINT_MARKERS = [
    "must", "cannot", "can't", "won't", "requires", "require", "only if", "no way", "not possible",
    "dependency", "dependent", "api key", "legal", "policy", "security", "compliance",
    "budget", "deadline", "today", "production", "cannot expose", "secret", "token", "rate limit", "auth"
]

DOWNSTREAM_CAPABILITY_MARKERS = [
    "we can build", "we can add", "just add", "ship it", "deploy it", "we can do all of it",
    "just use", "easy to", "quick fix", "we can implement"
]

BOUNDARY_ABSENCE_MARKERS = [
    "maybe", "might", "could", "sort of", "kind of", "basically", "we'll see", "later",
    "for now", "eventually", "not sure", "probably"
]

NARRATIVE_STABILIZATION_MARKERS = [
    "don't worry", "it's fine", "no big deal", "you got this", "glad", "relief", "it's okay",
    "not a problem", "totally"
]

DCE_DEFER_MARKERS = [
    "later", "eventually", "we can handle that later", "we'll address later", "we can worry later",
    "we'll figure it out", "next week", "after we launch", "phase 2", "future iteration",
    "explore", "consider", "evaluate", "assess", "as we continue", "as we iterate",
    "we will look into", "we'll look into", "we will revisit", "we'll revisit"
]

CCA_COLLAPSE_MARKERS = [
    "overall", "basically", "in general", "at the end of the day", "all in all", "net net",
    "it all comes down to", "the main thing", "just"
]

TILT_TAXONOMY = {
    "T1_REASSURANCE_DRIFT": ["don't worry", "it's fine", "it's okay", "you got this", "rest assured"],
    "T3_CONSENSUS_CLAIMS": ["most people", "many people", "everyone", "no one", "in general", "typically"],
    "T6_CONSTRAINT_DEFERRAL": ["later", "eventually", "phase 2", "after we launch", "we'll figure it out", "future iteration"],
    "T7_CATEGORY_BLEND": ["kind of", "sort of", "basically", "overall", "at the end of the day"],
    "T8_PRESSURE_OPTIMIZATION": ["now", "today", "asap", "immediately", "right away", "no sooner"]
}

CERTAINTY_INFLATION_TOKENS = [
    "guarantee", "guarantees", "guaranteed", "perfect", "zero risk", "eliminates all risk",
    "always", "never fail", "no possibility", "100%", "completely secure", "ensures complete", "every scenario"
]
CERTAINTY_ENFORCEMENT_VERBS = [
    "block", "blocks", "prevent", "prevents", "restrict", "restricts", "deny", "denies",
    "require", "requires", "enforce", "enforces", "validate", "validates", "verify", "verifies"
]
ABSOLUTE_LANGUAGE_TOKENS = [
    "always", "never", "everyone", "no one", "completely", "entirely", "100%", "guaranteed", "perfect", "zero risk"
]
AUTHORITY_IMPOSITION_TOKENS = [
    "experts agree", "industry standard", "research shows", "studies show", "best practice",
    "widely accepted", "authorities agree", "proven by research"
]
CAPABILITY_OVERREACH_TOKENS = [
    "solves everything", "handles everything", "covers all cases", "any scenario",
    "every scenario", "universal solution", "works for everyone"
]

def _contains_any(text_lc, needles):
    for n in needles:
        if n in text_lc:
            return True
    return False

def classify_tilt(text, prompt="", answer=""):
    t = (text or "").lower()
    tags = []
    for tag, markers in TILT_TAXONOMY.items():
        if _contains_any(t, markers):
            tags.append(tag)
    cert = _contains_any(t, CERTAINTY_INFLATION_TOKENS)
    enf = _contains_any(t, CERTAINTY_ENFORCEMENT_VERBS)
    if cert and not enf:
        tags.append("T2_CERTAINTY_INFLATION")
    if _contains_any(t, ABSOLUTE_LANGUAGE_TOKENS) and "T2_CERTAINTY_INFLATION" not in tags:
        tags.append("T5_ABSOLUTE_LANGUAGE")
    if _contains_any(t, AUTHORITY_IMPOSITION_TOKENS):
        tags.append("T10_AUTHORITY_IMPOSITION")
    if _contains_any(t, CAPABILITY_OVERREACH_TOKENS):
        tags.append("T4_CAPABILITY_OVERREACH")
    cap_verbs = _contains_any(t, ["solve", "solves", "handle", "handles", "cover", "covers", "ensure", "ensures"])
    if cap_verbs and _contains_any(t, ["everything", "all cases", "any scenario", "every"]):
        if "T4_CAPABILITY_OVERREACH" not in tags:
            tags.append("T4_CAPABILITY_OVERREACH")
    return tags

def detect_l0_constraints(text):
    t = (text or "").lower()
    return [m for m in L0_CONSTRAINT_MARKERS if m in t]

def detect_downstream_before_constraint(prompt, answer, l0):
    if not l0: return False
    a = (answer or "").lower()
    first_cap = -1
    for m in DOWNSTREAM_CAPABILITY_MARKERS:
        idx = a.find(m)
        if idx != -1 and (first_cap == -1 or idx < first_cap):
            first_cap = idx
    if first_cap == -1: return False
    first_con = -1
    for m in l0:
        idx = a.find(m.lower())
        if idx != -1 and (first_con == -1 or idx < first_con):
            first_con = idx
    if first_con == -1: return True
    return first_cap < first_con

def detect_udds(prompt, answer, l0):
    a = (answer or "").lower()
    cap = _contains_any(a, DOWNSTREAM_CAPABILITY_MARKERS)
    bound = not _contains_any(a, [m.lower() for m in l0]) if l0 else True
    narr = _contains_any(a, NARRATIVE_STABILIZATION_MARKERS)
    if cap and bound:
        return {"udds_state": "UDDS_CONFIRMED", "detail": "capability before constraint"}
    if cap and narr:
        return {"udds_state": "UDDS_PROBABLE", "detail": "capability + narrative stabilization"}
    return {"udds_state": "UDDS_FALSE", "detail": ""}

def detect_dce(answer, l0):
    a = (answer or "").lower()
    defer = _contains_any(a, DCE_DEFER_MARKERS)
    has_constraint = bool(l0) and any(m.lower() in a for m in l0)
    if defer and not has_constraint:
        return {"dce_state": "DCE_CONFIRMED", "detail": "deferral without constraint enforcement"}
    if defer:
        return {"dce_state": "DCE_PROBABLE", "detail": "deferral present but constraints exist"}
    return {"dce_state": "DCE_FALSE", "detail": ""}

def detect_cca(prompt, answer):
    a = (answer or "").lower()
    collapse = _contains_any(a, CCA_COLLAPSE_MARKERS)
    sents = split_sentences(answer or "")
    long_sents = [s for s in sents if len(tokenize(s)) > 40]
    if collapse and long_sents:
        return {"cca_state": "CCA_CONFIRMED", "detail": "collapse markers + long undifferentiated sentences"}
    if collapse:
        return {"cca_state": "CCA_PROBABLE", "detail": "collapse markers present"}
    return {"cca_state": "CCA_FALSE", "detail": ""}

def compute_nii(prompt, answer, l0, dbc, tilt):
    sents = split_sentences(answer or "")
    total = max(len(sents), 1)
    constraint_sents = sum(1 for s in sents if any(m in s.lower() for m in L0_CONSTRAINT_MARKERS))
    q1 = round(constraint_sents / total, 3) if total > 0 else 0
    q2 = 0.8 if not dbc else 0.2
    boundary_sents = sum(1 for s in sents if not _contains_any(s.lower(), BOUNDARY_ABSENCE_MARKERS))
    q3 = round(boundary_sents / total, 3) if total > 0 else 0
    tilt_penalty = min(len(tilt) * 0.05, 0.3)
    q4 = round(max(0, 1.0 - tilt_penalty), 3)
    nii = round((q1 + q2 + q3 + q4) / 4, 3)
    label = "HIGH" if nii >= 0.7 else "MEDIUM" if nii >= 0.4 else "LOW"
    return {"nii_score": nii, "nii_label": label, "q1": q1, "q2": q2, "q3": q3, "q4": q4}

def score_text(text):
    """Run full NTI scoring on text. Returns score dict."""
    l0 = detect_l0_constraints(text)
    tilt = classify_tilt(text)
    udds = detect_udds("", text, l0)
    dce = detect_dce(text, l0)
    cca = detect_cca("", text)
    dbc = detect_downstream_before_constraint("", text, l0)
    nii = compute_nii("", text, l0, dbc, tilt)
    
    dominance = []
    if cca["cca_state"] in ["CCA_CONFIRMED", "CCA_PROBABLE"]: dominance.append("CCA")
    if udds["udds_state"] in ["UDDS_CONFIRMED", "UDDS_PROBABLE"]: dominance.append("UDDS")
    if dce["dce_state"] in ["DCE_CONFIRMED", "DCE_PROBABLE"]: dominance.append("DCE")
    if not dominance: dominance = ["NONE"]
    
    return {
        "score": {"nii": nii["nii_score"], "nii_label": nii["nii_label"],
                  "components": {"q1": nii["q1"], "q2": nii["q2"], "q3": nii["q3"], "q4": nii["q4"]}},
        "failure_modes": {"UDDS": udds["udds_state"], "DCE": dce["dce_state"], "CCA": cca["cca_state"],
                          "dominance": dominance},
        "tilt": {"tags": tilt, "count": len(tilt)}
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
    for tag in soup(["script", "style", "noscript", "header", "nav", "footer", "iframe", "svg"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 20]
    combined = "\n".join(lines)
    return combined[:3000] if len(combined) > 3000 else combined

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
