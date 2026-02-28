"""
Artifact Zero - Fortune 500 Scorer
====================================
SCORING ONLY. Reads scraped text from DB, runs CSI + NTI, stores scores.

Job 2 of 2:
  - f500_scraper_v2.py  -> scrape pages, store raw text
  - f500_scorer.py (this file)  -> read raw text, run CSI+NTI, store scores

Run:
  python f500_scorer.py                   # score all unscored
  python f500_scorer.py --rescore         # re-score everything
  python f500_scorer.py --limit 10        # score up to 10
  python f500_scorer.py --slug walmart    # score one company

Requires: Flask app running (for NTI endpoint) OR --csi-only flag
"""
import os
import sys
import json
import time
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("scorer")


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


# ═══════════════════════════════════════════
# SCORING ENGINES
# ═══════════════════════════════════════════
SCORE_VERSION = "CSI-1.0+NTI-2.0"

def score_csi(text):
    """Run CSI (Corporate Structure Index) scoring. No network required."""
    try:
        # Add parent dir to path if needed
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from corporate_score import score_corporate_text
        return score_corporate_text(text)
    except ImportError:
        log.error("corporate_score.py not found - CSI scoring unavailable")
        return None
    except Exception as e:
        log.error(f"CSI scoring error: {e}")
        return None


def score_nti(text):
    """Run NTI scoring via local function imports. No HTTP required."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        # Import Flask app to get NTI functions
        os.environ.setdefault("FLASK_SECRET_KEY", "scorer")
        os.environ.setdefault("AZ_SECRET", "scorer")
        os.environ.setdefault("TESTING", "1")

        from app import (
            detect_l0_constraints, objective_extract, classify_tilt,
            detect_udds, detect_dce, detect_cca,
            detect_downstream_before_constraint, compute_nii
        )

        nti_text = text[:3000]
        l0 = detect_l0_constraints(nti_text)
        obj = objective_extract(nti_text)
        tilt = classify_tilt(nti_text)
        udds = detect_udds("", nti_text, l0)
        dce = detect_dce(nti_text, l0)
        cca = detect_cca("", nti_text)
        dbc = detect_downstream_before_constraint("", nti_text, l0)
        nii = compute_nii("", nti_text, l0, dbc, tilt)

        return {
            "nii_score": nii.get("nii_score", 0) if isinstance(nii, dict) else nii,
            "failure_modes": {"UDDS": udds, "DCE": dce, "CCA": cca},
            "tilt_patterns": tilt if isinstance(tilt, list) else [],
            "objective": obj,
            "constraints": l0,
        }
    except Exception as e:
        log.error(f"NTI scoring error: {e}")
        return None


def score_company(text, csi_only=False):
    """Run all scoring engines on text. Returns (csi_result, nti_result)."""
    csi = score_csi(text)
    nti = None
    if not csi_only:
        nti = score_nti(text)
    return csi, nti


# ═══════════════════════════════════════════
# MAIN SCORING LOOP
# ═══════════════════════════════════════════
def get_unscored(conn, limit=999, slug=None, rescore=False):
    """Get companies that need scoring."""
    cur = conn.cursor()

    if slug:
        cur.execute("""
            SELECT slug, company_name, rank, url, homepage_copy
            FROM fortune500_scores
            WHERE slug = %s AND homepage_copy IS NOT NULL AND LENGTH(homepage_copy) >= 80
        """, (slug,))
    elif rescore:
        cur.execute("""
            SELECT slug, company_name, rank, url, homepage_copy
            FROM fortune500_scores
            WHERE homepage_copy IS NOT NULL AND LENGTH(homepage_copy) >= 80
            ORDER BY rank
            LIMIT %s
        """, (limit,))
    else:
        cur.execute("""
            SELECT slug, company_name, rank, url, homepage_copy
            FROM fortune500_scores
            WHERE (score_version = 'unscored' OR score_version IS NULL OR nii_score = 0)
            AND homepage_copy IS NOT NULL AND LENGTH(homepage_copy) >= 80
            ORDER BY rank
            LIMIT %s
        """, (limit,))

    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_unscored_vc(conn, limit=999, rescore=False):
    """Get VC funds that need scoring."""
    cur = conn.cursor()
    if rescore:
        cur.execute("""
            SELECT slug, fund_name, rank, url, homepage_copy
            FROM vc_fund_scores
            WHERE homepage_copy IS NOT NULL AND LENGTH(homepage_copy) >= 80
            ORDER BY rank LIMIT %s
        """, (limit,))
    else:
        cur.execute("""
            SELECT slug, fund_name, rank, url, homepage_copy
            FROM vc_fund_scores
            WHERE (score_version = 'unscored' OR score_version IS NULL OR nii_score = 0)
            AND homepage_copy IS NOT NULL AND LENGTH(homepage_copy) >= 80
            ORDER BY rank LIMIT %s
        """, (limit,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def store_score(conn, table, name_col, slug, csi, nti):
    """Store scoring results back to the scores table."""
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Extract key metrics
    csi_score = csi.get("score", 0) if csi else 0
    csi_label = csi.get("label", "INSUFFICIENT") if csi else "INSUFFICIENT"
    nii_score = 0
    if nti:
        nii_raw = nti.get("nii_score", 0)
        nii_score = round(nii_raw * 100) if isinstance(nii_raw, (int, float)) and 0 < nii_raw <= 1.0 else round(nii_raw)

    # Count issues from NTI failure modes
    issue_count = 0
    if nti:
        fm = nti.get("failure_modes", {})
        for key in ["UDDS", "DCE", "CCA"]:
            val = fm.get(key, {})
            if isinstance(val, dict):
                state = str(val.get(f"{key.lower()}_state", ""))
                if "CONFIRMED" in state or "PROBABLE" in state:
                    issue_count += 1
        tilts = nti.get("tilt_patterns", [])
        if isinstance(tilts, list):
            issue_count += len(tilts)

    # Also count CSI findings
    if csi:
        findings = csi.get("findings", [])
        csi_issues = len([f for f in findings if f.get("severity") in ("high", "medium")])
        issue_count = max(issue_count, csi_issues)

    # Build combined score_json
    score_data = {}
    if csi:
        score_data["csi"] = csi
    if nti:
        score_data["nti"] = nti

    cur.execute(f"""
        UPDATE {table} SET
            score_json = %s,
            nii_score = %s,
            issue_count = %s,
            scored_at = %s,
            score_version = %s,
            last_changed = CASE
                WHEN nii_score != %s THEN %s
                ELSE last_changed
            END
        WHERE slug = %s
    """, (json.dumps(score_data), csi_score, issue_count, now, SCORE_VERSION,
          csi_score, now, slug))
    conn.commit()

    return csi_score, nii_score, issue_count


def run_scoring(limit=999, slug=None, rescore=False, csi_only=False, target="both"):
    """Main scoring loop."""
    conn = get_conn()
    results = []

    if target in ("f500", "both"):
        companies = get_unscored(conn, limit, slug, rescore)
        log.info(f"F500: {len(companies)} companies to score")

        ok = 0
        for c in companies:
            try:
                text = c["homepage_copy"]
                log.info(f"[{c['rank']}] {c['company_name']} ({len(text)} chars)")

                csi, nti = score_company(text, csi_only)

                if csi is None and nti is None:
                    log.warning(f"  SKIP {c['company_name']}: all scoring failed")
                    continue

                score, nii, issues = store_score(conn, "fortune500_scores", "company_name", c["slug"], csi, nti)
                log.info(f"  OK CSI={score:.1f} NII={nii} issues={issues}")
                ok += 1

            except Exception as e:
                log.error(f"  ERROR {c['company_name']}: {e}")

        results.append(f"F500: {ok}/{len(companies)} scored")

    if target in ("vc", "both") and not slug:
        funds = get_unscored_vc(conn, limit, rescore)
        log.info(f"VC: {len(funds)} funds to score")

        ok = 0
        for f in funds:
            try:
                text = f["homepage_copy"]
                name = f.get("fund_name", f["slug"])
                log.info(f"[{f['rank']}] {name} ({len(text)} chars)")

                csi, nti = score_company(text, csi_only)

                if csi is None and nti is None:
                    log.warning(f"  SKIP {name}: all scoring failed")
                    continue

                score, nii, issues = store_score(conn, "vc_fund_scores", "fund_name", f["slug"], csi, nti)
                log.info(f"  OK CSI={score:.1f} NII={nii} issues={issues}")
                ok += 1

            except Exception as e:
                log.error(f"  ERROR {f['slug']}: {e}")

        results.append(f"VC: {ok}/{len(funds)} scored")

    conn.close()
    msg = "Done. " + " | ".join(results)
    log.info(msg)
    return msg


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Artifact Zero F500 Scorer (score only)")
    p.add_argument("--limit", type=int, default=999)
    p.add_argument("--slug", type=str, default=None, help="Score one company by slug")
    p.add_argument("--rescore", action="store_true", help="Re-score all companies")
    p.add_argument("--csi-only", action="store_true", help="CSI only, skip NTI")
    p.add_argument("--target", choices=["f500", "vc", "both"], default="both")
    a = p.parse_args()
    print(run_scoring(
        limit=a.limit,
        slug=a.slug,
        rescore=a.rescore,
        csi_only=a.csi_only,
        target=a.target,
    ))
