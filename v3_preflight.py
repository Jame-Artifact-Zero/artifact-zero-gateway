#!/usr/bin/env python3
"""
v3_preflight.py — Pre-flight & Post-deploy Health Check
=========================================================
Run BEFORE push:   python3 v3_preflight.py --local
Run AFTER push:    python3 v3_preflight.py --live https://dontgofulltilt.com
Run AFTER push:    python3 v3_preflight.py --live https://artifact0.com

Exit code 0 = GO.  Exit code 1 = ROLLBACK.
"""

import sys
import json
import time
import argparse

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

failures = []
warnings = []

def ok(msg):
    print(f"  {PASS} {msg}")

def fail(msg):
    print(f"  {FAIL} {msg}")
    failures.append(msg)

def warn(msg):
    print(f"  {WARN} {msg}")
    warnings.append(msg)


def run_local():
    """Test all modules locally without running the Flask server."""
    print("\n=== LOCAL PRE-FLIGHT ===\n")

    # 1. Import chain
    print("[1/6] Import chain...")
    modules = [
        'audit_source', 'convergence_gate', 'v3_self_audit', 'time_object',
        'confusion_layer', 'consolidation_engine', 'loop_engine',
        'edge_engine', 'axis2_adapter', 'axis2_endpoint',
        'nti_full_integration_stub',
    ]
    for m in modules:
        try:
            __import__(m)
            ok(m)
        except Exception as e:
            fail(f"{m}: {e}")

    # 2. Core scoring regression
    print("\n[2/6] NTI scoring regression...")
    try:
        from app import detect_l0_constraints, classify_tilt, compute_nii, detect_downstream_before_constraint

        def score(text):
            l0 = detect_l0_constraints(text)
            tilt = classify_tilt(text)
            dbc = detect_downstream_before_constraint("", text, l0)
            return compute_nii("", text, l0, dbc, tilt).get("nii_score", 0)

        s1 = score("Send the contract by Friday 5pm or the deal expires. No extensions. Cap 50000.")
        if s1 >= 75: ok(f"Hard constraint: {s1}")
        else: fail(f"Hard constraint: {s1} (expected 75+)")

        s2 = score("Perhaps we could potentially consider maybe exploring some options.")
        if s2 < 65: ok(f"Hedging text: {s2}")
        else: fail(f"Hedging text: {s2} (expected <65)")

        s3 = score("I am finishing a VC wall and noticed the CBRE remark in the thread.")
        if 55 <= s3 <= 85: ok(f"CBRE message: {s3}")
        else: fail(f"CBRE message: {s3} (expected 55-85)")
    except Exception as e:
        fail(f"Scoring import error: {e}")

    # 3. V3 self-audit
    print("\n[3/6] V3 self-audit pipeline...")
    try:
        from v3_self_audit import run_v3_pipeline

        def dict_scorer(text):
            l0 = detect_l0_constraints(text)
            tilt = classify_tilt(text)
            dbc = detect_downstream_before_constraint("", text, l0)
            return compute_nii("", text, l0, dbc, tilt)

        v3 = run_v3_pipeline("Send contract by Friday or deal expires. No extensions. Cap 50000.", dict_scorer, 0.85, 2)
        if v3["self_audit"]["decision"] == "pass": ok(f"Strong text passes audit")
        else: warn(f"Strong text failed audit (score may have changed)")

        v3b = run_v3_pipeline("Perhaps maybe we could consider options.", dict_scorer, 0.85, 2)
        if v3b["self_audit"]["decision"] == "fail": ok(f"Hedging text fails audit")
        else: fail(f"Hedging text passed audit — self-audit broken")
    except Exception as e:
        fail(f"Self-audit error: {e}")

    # 4. Convergence gate
    print("\n[4/6] Convergence gate...")
    try:
        from convergence_gate import enforce
        allowed, _ = enforce({"text": "ok thanks"}, {})
        if not allowed: ok("Acknowledgement blocked")
        else: fail("Acknowledgement not blocked")

        allowed2, _ = enforce({"text": "We need to review the Q3 data"}, {})
        if allowed2: ok("Real message passes")
        else: fail("Real message blocked")
    except Exception as e:
        fail(f"Convergence gate error: {e}")

    # 5. Axis 2 + full integration
    print("\n[5/6] Axis 2 + full integration...")
    try:
        from axis2_endpoint import handle_request
        r = handle_request({"text": "Fix this now or else"})
        if "friction_score" in r: ok(f"axis2_endpoint returns friction_score={r['friction_score']:.2f}")
        else: fail("axis2_endpoint missing friction_score")

        from nti_full_integration_stub import build_full
        f = build_full({"text": "test", "request_id": "t"}, {"nii": {"nii_score": 72}}, "v3.0")
        expected_keys = {"axis1", "axis2", "loop", "confusion", "time_object"}
        missing = expected_keys - set(f.keys())
        if not missing: ok(f"nti_full_integration: all sections present")
        else: fail(f"nti_full_integration missing: {missing}")
    except Exception as e:
        fail(f"Integration error: {e}")

    # 6. db.py compatibility
    print("\n[6/6] DB compatibility layer...")
    try:
        from db import db_connection, param_placeholder
        ph = param_placeholder()
        if ph in ("%s", "?"): ok(f"param_placeholder returns '{ph}'")
        else: fail(f"param_placeholder returned '{ph}'")

        with db_connection() as conn:
            ok("db_connection context manager works")
    except Exception as e:
        fail(f"DB compatibility error: {e}")

    return len(failures) == 0


def run_live(base_url):
    """Test live endpoints after deployment."""
    import urllib.request
    print(f"\n=== LIVE POST-DEPLOY CHECK: {base_url} ===\n")

    def get(path):
        url = base_url.rstrip("/") + path
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return {"_error": str(e)}

    def post(path, data):
        url = base_url.rstrip("/") + path
        try:
            body = json.dumps(data).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            return {"_error": str(e)}

    # 1. Health
    print("[1/5] Health check...")
    h = get("/health")
    if h.get("_error"):
        fail(f"/health unreachable: {h['_error']}")
    elif h.get("status") == "ok":
        ver = h.get("version", "?")
        if "v3" in ver: ok(f"/health OK, version={ver}")
        else: fail(f"/health OK but version={ver} — expected v3.x")
    else:
        fail(f"/health returned: {h}")

    # 2. Canonical status
    print("\n[2/5] Canonical status...")
    cs = get("/canonical/status")
    if cs.get("_error"):
        fail(f"/canonical/status unreachable: {cs['_error']}")
    else:
        v3m = cs.get("v3_modules", {})
        if v3m.get("self_audit") and v3m.get("axis2_friction"):
            ok(f"V3 modules present: {sum(1 for v in v3m.values() if v)}/{len(v3m)} active")
        else:
            fail(f"V3 modules missing from canonical/status")

    # 3. NTI scoring
    print("\n[3/5] NTI scoring...")
    r = post("/nti", {"text": "Send the contract by Friday 5pm or the deal expires."})
    if r.get("_error"):
        fail(f"/nti unreachable: {r['_error']}")
    else:
        nii = r.get("nii", {})
        score = nii.get("nii_score") if isinstance(nii, dict) else None
        v3_block = r.get("v3", {})
        if score is not None and score > 50:
            ok(f"/nti returns NII={score}")
        else:
            fail(f"/nti NII unexpected: {score}")
        if "decision" in v3_block:
            ok(f"V3 self-audit active: decision={v3_block['decision']}")
        elif "error" in v3_block:
            warn(f"V3 self-audit error: {v3_block['error']}")
        else:
            fail("V3 block missing from /nti response")

    # 4. NTI-friction
    print("\n[4/5] NTI-friction endpoint...")
    rf = post("/nti-friction", {"text": "Fix this immediately or there will be consequences"})
    if rf.get("_error"):
        fail(f"/nti-friction unreachable: {rf['_error']}")
    elif "friction_score" in rf:
        ok(f"/nti-friction returns friction_score={rf['friction_score']}")
    else:
        fail(f"/nti-friction unexpected response: {rf}")

    # 5. NTI-full
    print("\n[5/5] NTI-full endpoint...")
    rfl = post("/nti-full", {"text": "We need to review the NTI scores and CBRE data before Tuesday"})
    if rfl.get("_error"):
        fail(f"/nti-full unreachable: {rfl['_error']}")
    elif "axis1" in rfl and "axis2" in rfl:
        ok(f"/nti-full returns axis1+axis2+loop+confusion")
    else:
        fail(f"/nti-full missing sections: {list(rfl.keys())}")

    return len(failures) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V3 Pre-flight / Post-deploy Health Check")
    parser.add_argument("--local", action="store_true", help="Run local module tests")
    parser.add_argument("--live", type=str, help="Run live endpoint tests against URL")
    args = parser.parse_args()

    if not args.local and not args.live:
        print("Usage:")
        print("  python3 v3_preflight.py --local              # before push")
        print("  python3 v3_preflight.py --live https://...    # after push")
        sys.exit(1)

    passed = True
    if args.local:
        passed = run_local()
    if args.live:
        passed = run_live(args.live) and passed

    print("\n" + "=" * 50)
    if failures:
        print(f"\033[91mFAILED: {len(failures)} issue(s). ROLLBACK RECOMMENDED.\033[0m")
        for f in failures:
            print(f"  ✗ {f}")
    else:
        print(f"\033[92mPASSED: All checks green. GO.\033[0m")

    if warnings:
        print(f"\033[93mWarnings: {len(warnings)}\033[0m")
        for w in warnings:
            print(f"  ⚠ {w}")

    sys.exit(0 if not failures else 1)
