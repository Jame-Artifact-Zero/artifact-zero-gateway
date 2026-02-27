"""
tests/test_smoke.py — Automated Smoke Tests for Artifact Zero
================================================================
Runs on every PR. Must all pass before merge to quality or production.

Tests:
  - All routes return 200/302 (not 500)
  - NTI scoring engine produces valid output
  - CSI scoring engine produces valid output
  - CSRF tokens render as hidden inputs (not raw text)
  - API endpoints return valid JSON
  - Auth flow works (login, signup, CSRF)
  - Fortune 500 / VC APIs return data structure
"""

import os
import sys
import json
import pytest

# Setup Flask test env
os.environ["FLASK_SECRET_KEY"] = "test-key-for-ci"
os.environ["AZ_SECRET"] = "test-az-secret"
os.environ["TESTING"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ═══════════════════════════════════════════
# ROUTE HEALTH
# ═══════════════════════════════════════════

PUBLIC_ROUTES = [
    "/", "/health", "/login", "/signup", "/forgot",
    "/score", "/safecheck", "/compose",
    "/docs", "/contact", "/developers", "/examples", "/wall",
    "/glossary", "/fortune500", "/vc-funds",
    "/live", "/relay", "/your-os",
    "/ccs", "/ccs-eval",
]

@pytest.mark.parametrize("route", PUBLIC_ROUTES)
def test_public_routes(client, route):
    """Every public route returns 200 or 302 (redirect), never 500."""
    r = client.get(route)
    assert r.status_code in (200, 302), f"{route} returned {r.status_code}"


def test_health_json(client):
    """Health endpoint returns valid JSON with status ok."""
    r = client.get("/health")
    data = r.get_json()
    assert data["status"] == "ok"
    assert "version" in data


def test_dashboard_requires_auth(client):
    """Dashboard redirects to login when not authenticated."""
    r = client.get("/dashboard")
    assert r.status_code in (302, 401)


# ═══════════════════════════════════════════
# CSRF
# ═══════════════════════════════════════════

def test_csrf_not_visible_login(client):
    """CSRF token renders as hidden input, not raw HTML text on login page."""
    r = client.get("/login")
    html = r.data.decode()
    assert "&lt;input" not in html, "CSRF token showing as raw escaped HTML"
    assert 'name="csrf_token"' in html, "CSRF token input missing"


def test_csrf_not_visible_signup(client):
    """CSRF token renders as hidden input, not raw HTML text on signup page."""
    r = client.get("/signup")
    html = r.data.decode()
    assert "&lt;input" not in html, "CSRF token showing as raw escaped HTML"
    assert 'name="csrf_token"' in html, "CSRF token input missing"


def test_csrf_rejects_bad_token(client):
    """POST to login with wrong CSRF token returns 403."""
    r = client.post("/login", data={
        "email": "test@test.com",
        "password": "test",
        "csrf_token": "wrong-token"
    })
    assert r.status_code == 403


def test_csrf_accepts_correct_token(client):
    """POST to login with correct CSRF token doesn't return 403."""
    with client.session_transaction() as sess:
        sess["csrf_token"] = "valid-test-token"
    r = client.post("/login", data={
        "email": "nonexistent@test.com",
        "password": "wrongpass",
        "csrf_token": "valid-test-token"
    })
    # Should be 401 (bad creds), NOT 403 (CSRF failure)
    assert r.status_code != 403, "CSRF validation rejected a valid token"


# ═══════════════════════════════════════════
# NTI SCORING ENGINE
# ═══════════════════════════════════════════

def test_nti_scoring(client):
    """NTI endpoint returns valid score structure."""
    r = client.post("/nti", json={
        "text": "We will deliver the report by Friday. The client requires HIPAA compliance."
    })
    assert r.status_code == 200
    data = r.get_json()
    assert "nii" in data
    assert "tilt_taxonomy" in data
    assert "parent_failure_modes" in data


def test_nti_rejects_empty(client):
    """NTI endpoint rejects empty text."""
    r = client.post("/nti", json={"text": ""})
    data = r.get_json()
    assert r.status_code != 200 or "error" in data


def test_free_scoring(client):
    """Free scoring endpoint works without API key."""
    r = client.post("/api/v1/score/free", json={
        "text": "Test input for free scoring endpoint."
    })
    assert r.status_code == 200
    data = r.get_json()
    assert "score" in data or "nii" in str(data)


# ═══════════════════════════════════════════
# CSI SCORING ENGINE
# ═══════════════════════════════════════════

def test_csi_scoring():
    """CSI engine produces valid 10-dimension scores."""
    from corporate_score import score_corporate_text

    text = (
        "Walmart's purpose is to save people money so they can live better. "
        "We operate approximately 10,500 stores and clubs under 46 banners in 19 countries. "
        "We employ approximately 2.1 million associates worldwide. "
        "Our strategy focuses on strengthening the core business while building new capabilities."
    )
    result = score_corporate_text(text)

    assert "score" in result
    assert "dimensions" in result
    assert "findings" in result
    assert "label" in result
    assert isinstance(result["score"], (int, float))
    assert 0 <= result["score"] <= 100

    dims = result["dimensions"]
    expected_dims = [
        "d1_specificity", "d2_commitment", "d3_clarity", "d4_hedge_density",
        "d5_tilt_exposure", "d6_empty_commitments", "d7_objective_anchor",
        "d8_accountability", "d9_redundancy", "d10_differentiation"
    ]
    for d in expected_dims:
        assert d in dims, f"Missing dimension: {d}"
        assert 0 <= dims[d] <= 1.0, f"{d} out of range: {dims[d]}"


def test_csi_differentiation():
    """CSI produces different scores for different companies."""
    from corporate_score import score_corporate_text

    text_a = (
        "We believe in creating value for stakeholders through innovative solutions. "
        "Our mission-driven approach enables us to make the world a better place. "
        "We are committed to excellence and passionate about serving our customers."
    )
    text_b = (
        "The company generated $391 billion in revenue in fiscal year 2024. "
        "Our environmental commitments include being carbon neutral by 2030. "
        "We currently use 100% recycled cobalt in all Apple-designed batteries."
    )
    score_a = score_corporate_text(text_a)["score"]
    score_b = score_corporate_text(text_b)["score"]

    assert score_a != score_b, "CSI produced identical scores for different texts"
    assert score_b > score_a, "Specific text should score higher than vague text"


def test_csi_insufficient_text():
    """CSI handles insufficient text gracefully."""
    from corporate_score import score_corporate_text
    result = score_corporate_text("Too short")
    assert result["label"] == "INSUFFICIENT"


# ═══════════════════════════════════════════


# ═══════════════════════════════════════════
# API STRUCTURE
# ═══════════════════════════════════════════

def test_fortune500_api(client):
    """Fortune 500 API returns valid list structure."""
    r = client.get("/api/fortune500")
    assert r.status_code == 200
    data = r.get_json()
    assert "companies" in data
    assert isinstance(data["companies"], list)


def test_vc_funds_api(client):
    """VC Funds API returns valid list structure."""
    r = client.get("/api/vc-funds")
    assert r.status_code == 200
    data = r.get_json()
    assert "funds" in data
    assert isinstance(data["funds"], list)


def test_canonical_status(client):
    """Canonical status endpoint works."""
    r = client.get("/canonical/status")
    assert r.status_code == 200


# ═══════════════════════════════════════════
# AUTH FLOW
# ═══════════════════════════════════════════

def test_auth_status_api(client):
    """Auth status endpoint returns JSON."""
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    data = r.get_json()
    assert "logged_in" in data


# ═══════════════════════════════════════════
# REGRESSION GUARDS
# ═══════════════════════════════════════════

def test_no_duplicate_route_crash(client):
    """App boots without route collision crashes."""
    # If we got here, the app imported and built successfully
    assert True


def test_v3_enforcement_imports():
    """V3 enforcement engine imports without error."""
    from core_engine.v3_enforcement import enforce, self_audit
    assert callable(enforce)
    assert callable(self_audit)


def test_interrogative_engine_imports():
    """Interrogative engine imports without error."""
    from core_engine.interrogative_engine import classify_question
    assert callable(classify_question)


# ═══════════════════════════════════════
# AZ-SHELL INTEGRATION
# ═══════════════════════════════════════

SHELL_PAGES = [
    "/", "/score", "/safecheck", "/compose",
    "/docs", "/contact", "/examples",
    "/login", "/signup", "/fortune500",
]

@pytest.mark.parametrize("route", SHELL_PAGES)
def test_az_shell_loaded(client, route):
    """Every public page loads az-shell.js."""
    r = client.get(route)
    if r.status_code == 200:
        html = r.data.decode()
        assert "az-shell.js" in html, f"{route} does not load az-shell.js"


@pytest.mark.parametrize("route", ["/login", "/signup"])
def test_csrf_renders_hidden(client, route):
    """CSRF token renders as hidden input, not escaped HTML."""
    r = client.get(route)
    html = r.data.decode()
    assert "&lt;input" not in html, f"{route}: CSRF token is showing as raw escaped HTML"
    assert 'name="csrf_token"' in html, f"{route}: CSRF hidden input missing"
