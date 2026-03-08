"""az_501_scrape.py - Score Artifact Zero as Company #501

Run from project root:
  python az_501_scrape.py

Scrapes artifact0.com pages, scores through NTI engine,
inserts into fortune500_scores as rank 501.
The scored page at /scored/artifact-zero displays identically
to every other company. Same engine. Same rules.
"""

import os, sys, json, re
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import requests
except ImportError:
    print("ERROR: pip install requests"); sys.exit(1)

try:
    import db as database
except ImportError:
    print("ERROR: run from project root"); sys.exit(1)

AZ = {
    "company_name": "Artifact Zero",
    "slug": "artifact-zero",
    "rank": 501,
    "url": "https://artifact0.com",
}

PAGES = [
    "https://artifact0.com",
    "https://artifact0.com/relay",
    "https://artifact0.com/ai",
    "https://artifact0.com/use-cases",
]


def scrape(url):
    try:
        r = requests.get(url, headers={"User-Agent": "AZ-SelfScore/1.0"}, timeout=15)
        r.raise_for_status()
        h = r.text
        h = re.sub(r"<script[^>]*>.*?</script>", "", h, flags=re.DOTALL | re.IGNORECASE)
        h = re.sub(r"<style[^>]*>.*?</style>", "", h, flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", h)
        return re.sub(r"\s+", " ", t).strip()
    except Exception as e:
        print(f"  WARN: {url}: {e}")
        return ""


def main():
    print("=" * 50)
    print("Artifact Zero - Company #501 Self-Score")
    print("=" * 50)

    texts = []
    for url in PAGES:
        print(f"  Scraping: {url}")
        t = scrape(url)
        if t:
            print(f"    -> {len(t.split())} words")
            texts.append(t)

    combined = "\n\n".join(texts)
    wc = len(combined.split())
    print(f"\n  Total: {wc} words from {len(texts)} pages")
    if wc < 50:
        print("ERROR: not enough text")
        sys.exit(1)

    print("\n  Scoring...")
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
    except Exception as e:
        print(f"  Engine error: {e}, using API fallback...")
        try:
            r = requests.post("http://localhost:5000/nti", json={"text": combined}, timeout=30)
            d = r.json()
            nii = d.get("nii", {}).get("nii_score", 0)
            nti = csi = hcs = 0
            tilt = d.get("tilt_taxonomy", [])
            issues = len(tilt)
        except Exception:
            print("ERROR: cannot score")
            sys.exit(1)

    display_score = round(nii * 100, 1) if nii <= 1 else nii
    print(f"\n  NII: {nii}  NTI: {nti}  CSI: {csi}  HCS: {hcs}")
    print(f"  Issues: {issues}  Tilt: {tilt}")

    print("\n  Writing to DB...")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sj = json.dumps({
        "nii": {"nii_score": nii},
        "nti": {"nti_score": nti},
        "csi": {"csi_score": csi},
        "hcs": {"hcs_score": hcs},
        "tilt": tilt,
        "words": wc,
    })

    conn = database.db_connect()
    cur = conn.cursor()
    if database.USE_PG:
        cur.execute(
            "INSERT INTO fortune500_scores (slug, company_name, rank, url, nii_score, issue_count, last_checked, score_json, homepage_copy) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET nii_score=EXCLUDED.nii_score, issue_count=EXCLUDED.issue_count, "
            "last_checked=EXCLUDED.last_checked, score_json=EXCLUDED.score_json, homepage_copy=EXCLUDED.homepage_copy",
            (AZ["slug"], AZ["company_name"], AZ["rank"], AZ["url"], display_score, issues, now, sj, combined)
        )
    else:
        cur.execute("DELETE FROM fortune500_scores WHERE slug = ?", (AZ["slug"],))
        cur.execute(
            "INSERT INTO fortune500_scores (slug, company_name, rank, url, nii_score, issue_count, last_checked, score_json, homepage_copy) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (AZ["slug"], AZ["company_name"], AZ["rank"], AZ["url"], display_score, issues, now, sj, combined)
        )
    conn.commit()
    conn.close()

    print(f"\n  DONE. Company #501 scored at {display_score}")
    print(f"  View: /scored/artifact-zero")


if __name__ == "__main__":
    main()
