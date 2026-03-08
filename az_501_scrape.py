"""az_501_scrape.py - Score Artifact Zero as Company #501

Run from project root:
  python az_501_scrape.py

Scrapes artifact0.com pages using curl_cffi Chrome TLS fingerprint,
extracts clean body text via BeautifulSoup, deduplicates by checksum,
scores through NTI engine, writes score + homepage_copy to fortune500_scores.
"""

import os
import sys
import json
import re
import hashlib
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import db as database
except ImportError:
    print("ERROR: run from project root")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: pip install beautifulsoup4 --break-system-packages")
    sys.exit(1)

AZ = {
    "company_name": "Artifact Zero",
    "slug": "artifact-zero",
    "rank": 501,
    "url": "https://artifact0.com",
}

# Content-rich pages — not app/tool pages
PAGES = [
    "https://artifact0.com",
    "https://artifact0.com/safecheck",
    "https://artifact0.com/score",
    "https://artifact0.com/examples",
    "https://artifact0.com/docs",
    "https://artifact0.com/contact",
]

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


# ── FETCH ─────────────────────────────────────────────────────────────────────

def fetch(url):
    """Fetch with curl_cffi Chrome impersonation. Falls back to requests."""
    try:
        from curl_cffi import requests as cffi_requests
        resp = cffi_requests.get(
            url, headers=HEADERS, timeout=15,
            allow_redirects=True, impersonate="chrome"
        )
        resp.raise_for_status()
        return resp.text
    except ImportError:
        import requests
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  WARN fetch {url}: {e}")
        return None


# ── EXTRACT ───────────────────────────────────────────────────────────────────

def extract_text(html):
    """Extract clean body text. Strip nav, footer, script, style, buttons."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                      "iframe", "svg", "form", "button", "select", "input",
                      "aside", "menu", "meta", "link"]):
        tag.decompose()

    blocks = []

    # Priority 1: main/article paragraphs
    for container in soup.find_all(["main", "article"]):
        for el in container.find_all(["p", "h1", "h2", "h3", "li", "blockquote"]):
            t = el.get_text(separator=" ", strip=True)
            if len(t) >= 40:
                blocks.append(t)

    # Priority 2: all paragraphs if main was sparse
    if len(blocks) < 5:
        for el in soup.find_all(["p", "h1", "h2", "h3", "blockquote"]):
            t = el.get_text(separator=" ", strip=True)
            if len(t) >= 40:
                if not any(t[:40] in b for b in blocks):
                    blocks.append(t)

    return " ".join(blocks).strip()


# ── SCRAPE ALL PAGES ──────────────────────────────────────────────────────────

def scrape_all():
    texts = []
    seen = set()

    for url in PAGES:
        print(f"  Scraping: {url}")
        html = fetch(url)
        if not html:
            print(f"    -> FAILED")
            continue

        text = extract_text(html)
        if not text or len(text.split()) < 20:
            print(f"    -> too thin ({len(text.split()) if text else 0} words), skipping")
            continue

        # Deduplicate by checksum of first 300 chars
        cs = hashlib.md5(text[:300].encode()).hexdigest()
        if cs in seen:
            print(f"    -> duplicate content, skipping")
            continue
        seen.add(cs)

        wc = len(text.split())
        print(f"    -> {wc} words")
        texts.append(text)
        time.sleep(0.5)

    return "\n\n".join(texts)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("Artifact Zero - Company #501 Self-Score")
    print("=" * 50)

    combined = scrape_all()
    wc = len(combined.split())
    print(f"\n  Total: {wc} words")

    if wc < 50:
        print("ERROR: not enough text scraped")
        sys.exit(1)

    print("\n  Scoring...")
    nii = nti = csi = hcs = 0
    tilt = []
    issues = 0
    score_dims = {}

    try:
        from core_engine.detection import detect_all
        from core_engine.scoring import score_composite
        det = detect_all(combined)
        sc = score_composite(det)
        nii = sc["nii"]["nii_score"]
        nti = sc["nti"]["nti_score"]
        csi = sc["csi"]["csi_score"]
        hcs = sc["hcs"]["hcs_score"]
        tilt = det.get("tilt_taxonomy", [])
        issues = len(tilt) + len(det.get("active_failures", []))
        # Capture per-dimension data for template rendering
        score_dims = sc.get("dimensions", sc.get("csi", {}).get("dimensions", {}))
    except Exception as e:
        print(f"  Engine import error: {e}")
        print("  Trying API fallback...")
        try:
            import requests
            r = requests.post(
                "http://localhost:5000/nti",
                json={"text": combined},
                timeout=30
            )
            d = r.json()
            nii = d.get("nii", {}).get("nii_score", 0)
            nti = d.get("nti", {}).get("nti_score", 0)
            csi = d.get("csi", {}).get("csi_score", 0)
            hcs = d.get("hcs", {}).get("hcs_score", 0)
            tilt = d.get("tilt_taxonomy", [])
            issues = len(tilt) + len(d.get("active_failures", []))
            score_dims = d.get("dimensions", {})
        except Exception as e2:
            print(f"  API fallback error: {e2}")
            print("ERROR: cannot score — no engine available")
            sys.exit(1)

    display_score = round(nii * 100, 1) if nii <= 1 else round(nii, 1)
    print(f"\n  NII: {nii}  NTI: {nti}  CSI: {csi}  HCS: {hcs}")
    print(f"  Issues: {issues}  Tilt: {tilt}")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sj = json.dumps({
        "nii": {"nii_score": nii},
        "nti": {"nti_score": nti},
        "csi": {"csi_score": csi},
        "hcs": {"hcs_score": hcs},
        "tilt": tilt,
        "words": wc,
        "dimensions": score_dims,
        "pages_scraped": len(PAGES),
    })

    print("\n  Writing to DB...")
    conn = database.db_connect()
    cur = conn.cursor()

    if database.USE_PG:
        cur.execute(
            "INSERT INTO fortune500_scores "
            "(slug, company_name, rank, url, nii_score, issue_count, last_checked, score_json, homepage_copy) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET "
            "nii_score=EXCLUDED.nii_score, "
            "issue_count=EXCLUDED.issue_count, "
            "last_checked=EXCLUDED.last_checked, "
            "score_json=EXCLUDED.score_json, "
            "homepage_copy=EXCLUDED.homepage_copy",
            (AZ["slug"], AZ["company_name"], AZ["rank"], AZ["url"],
             display_score, issues, now, sj, combined)
        )
    else:
        cur.execute("DELETE FROM fortune500_scores WHERE slug = ?", (AZ["slug"],))
        cur.execute(
            "INSERT INTO fortune500_scores "
            "(slug, company_name, rank, url, nii_score, issue_count, last_checked, score_json, homepage_copy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (AZ["slug"], AZ["company_name"], AZ["rank"], AZ["url"],
             display_score, issues, now, sj, combined)
        )

    conn.commit()
    conn.close()

    print(f"\n  DONE. Company #501 scored at {display_score}")
    print(f"  View: /scored/artifact-zero")


if __name__ == "__main__":
    main()
