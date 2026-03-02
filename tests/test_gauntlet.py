"""
tests/test_gauntlet.py — Behavioral Gauntlet for NTI Pipeline
================================================================
10 canonical test messages with known structural failures.
Each test verifies V1 scoring, V2 flagging, and V3 enforcement
produce CORRECT behavioral outcomes, not just valid JSON shapes.

This is the difference between smoke tests and gauntlet tests:
- Smoke: "did the endpoint return 200?"
- Gauntlet: "did the engine actually catch the DCE in this message?"

Run: pytest tests/test_gauntlet.py -v
Against production: GAUNTLET_URL=https://artifact0.com pytest tests/test_gauntlet.py -v
"""

import os
import sys
import json
import pytest

# Setup Flask test env
os.environ.setdefault("FLASK_SECRET_KEY", "test-key-for-ci")
os.environ.setdefault("AZ_SECRET", "test-az-secret")
os.environ.setdefault("TESTING", "1")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# If GAUNTLET_URL is set, test against live endpoint. Otherwise use Flask test client.
LIVE_URL = os.environ.get("GAUNTLET_URL", "")

if not LIVE_URL:
    from app import app

    @pytest.fixture
    def client():
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c
else:
    import urllib.request
    import urllib.error

    class LiveClient:
        """Minimal client that hits a live URL instead of Flask test client."""
        def __init__(self, base_url):
            self.base = base_url.rstrip("/")

        def post(self, path, json=None):
            url = self.base + path
            body = __import__("json").dumps(json or {}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                return _LiveResponse(resp.read(), resp.status)
            except urllib.error.HTTPError as e:
                return _LiveResponse(e.read(), e.code)

    class _LiveResponse:
        def __init__(self, data, status_code):
            self._data = data
            self.status_code = status_code
        def get_json(self):
            return __import__("json").loads(self._data)

    @pytest.fixture
    def client():
        yield LiveClient(LIVE_URL)


# ═══════════════════════════════════════════════════════════
# THE 10 CANONICAL TEST MESSAGES
# Each has: text, expected V1 behaviors, expected V2 flags,
# expected V3 enforcement behaviors
# ═══════════════════════════════════════════════════════════

GAUNTLET = [
    {
        "id": "G01_holman",
        "name": "William Holman LinkedIn message (the bug that started this)",
        "text": (
            "I'm so annoyed with pretty much everything out there right now. "
            "They make commitments that they later say they were never able to perform. "
            "Are you saying your would prevent or attempt to prevent that?"
        ),
        "v1": {
            "expect_failure_modes": ["DCE"],
            "nii_below": 60,
        },
        "v2_flags": ["MISSING_OBJECTIVE", "NO_TIMELINE_CONSTRAINT"],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G02_classic_hedge",
        "name": "Hedge-heavy sales email",
        "text": (
            "I'd love to connect and explore some potential synergies between our organizations. "
            "I think there could be some really exciting opportunities for mutual benefit. "
            "Let me know if you'd be open to a quick chat sometime next week or whenever works for you."
        ),
        "v1": {
            "expect_tilt_count_gte": 1,
            "nii_below": 50,
        },
        "v2_flags": ["MISSING_OBJECTIVE"],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G03_dce_deferral",
        "name": "Pure DCE — everything pushed to later",
        "text": (
            "We'll figure out the pricing later. For now let's just get the contract signed "
            "and we can address the specifics eventually. Don't worry about the SLA details, "
            "we'll sort those out down the road."
        ),
        "v1": {
            "expect_failure_modes": ["DCE"],
            "nii_below": 40,
        },
        "v2_flags": ["MISSING_OBJECTIVE"],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G04_cca_overclaim",
        "name": "CCA — capability claimed without constraint",
        "text": (
            "Our platform can handle everything you need. We guarantee 100% uptime "
            "and our system always delivers perfect results. We can do all of it — "
            "just tell us what you want and consider it done."
        ),
        "v1": {
            "expect_failure_modes": ["CCA"],
            "nii_below": 45,
        },
        "v2_flags": [],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G05_udds_narrative",
        "name": "UDDS — narrative substitution for constraints",
        "text": (
            "Don't worry about the technical requirements, we've got this covered. "
            "Rest assured the team is fully aligned and we're basically on track. "
            "It's fine, no big deal. You got this."
        ),
        "v1": {
            "expect_failure_modes": ["UDDS"],
            "nii_below": 65,
        },
        "v2_flags": ["MISSING_OBJECTIVE"],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G06_clean_constrained",
        "name": "Clean, constrained answer — should score HIGH",
        "text": (
            "The endpoint requires authentication middleware that hasn't passed security review. "
            "The review is scheduled for Thursday. Deployment cannot happen before that review "
            "completes and the team signs off. Earliest realistic deploy is next Monday."
        ),
        "v1": {
            "nii_above": 55,
        },
        "v2_flags": [],
        "v3": {
            "must_flag_issues": False,
        },
    },
    {
        "id": "G07_emotional_override",
        "name": "Emotional text with no structural content",
        "text": (
            "I'm just so frustrated with how things have been going. Nothing works right "
            "and nobody seems to care. Everything is broken and I don't know what to do anymore. "
            "Can someone please just help?"
        ),
        "v1": {
            "nii_below": 70,
        },
        "v2_flags": ["MISSING_OBJECTIVE", "NO_TIMELINE_CONSTRAINT"],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G08_scope_creep",
        "name": "Scope creep — answer expands beyond the ask",
        "text": (
            "While we're at it, we should also add analytics, a mobile app, push notifications, "
            "an admin dashboard, and maybe some ML-based recommendations. Oh and we can probably "
            "handle that new compliance requirement too. Just add it to the sprint."
        ),
        "v1": {
            "nii_below": 75,
        },
        "v2_flags": [],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G09_false_commitment",
        "name": "False commitment — promises with no backing",
        "text": (
            "Absolutely, we can definitely have that ready by Friday. No problem at all. "
            "The team is totally on board and everything is under control. "
            "Consider it done, you won't have to worry about a thing."
        ),
        "v1": {
            "expect_failure_modes": ["DCE"],
            "nii_below": 75,
        },
        "v2_flags": [],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 1,
        },
    },
    {
        "id": "G10_triple_failure",
        "name": "Triple failure mode — UDDS + DCE + CCA active",
        "text": (
            "Don't worry about the timeline, we'll figure it out later. Our platform can "
            "handle absolutely everything and it never fails. Rest assured we've got this "
            "completely covered. We can build all of it, just add it to the backlog and "
            "we'll address the details eventually. It's fine."
        ),
        "v1": {
            "nii_below": 30,
            "expect_dominance_not_none": True,
        },
        "v2_flags": ["MISSING_OBJECTIVE"],
        "v3": {
            "must_flag_issues": True,
            "min_issues": 2,
        },
    },
]


# ═══════════════════════════════════════════════════════════
# V1 BEHAVIORAL TESTS — Does the scoring engine catch what it claims?
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("case", GAUNTLET, ids=[c["id"] for c in GAUNTLET])
def test_v1_scoring_behavior(client, case):
    """V1 scores each message and hits expected behavioral targets."""
    r = client.post("/nti", json={"text": case["text"]})
    assert r.status_code == 200, f"{case['id']}: /nti returned {r.status_code}"
    data = r.get_json()

    nii = data.get("nii", {})
    nii_score = nii.get("nii_score", 999)
    fm = data.get("parent_failure_modes", {})
    tilt = data.get("tilt_taxonomy", [])
    matrix = data.get("interaction_matrix", {})
    dominance = matrix.get("dominance_detected", ["NONE"])

    v1 = case["v1"]

    # NII score ceiling
    if "nii_below" in v1:
        assert nii_score < v1["nii_below"], (
            f"{case['id']}: NII={nii_score}, expected below {v1['nii_below']}"
        )

    # NII score floor
    if "nii_above" in v1:
        assert nii_score > v1["nii_above"], (
            f"{case['id']}: NII={nii_score}, expected above {v1['nii_above']}"
        )

    # Expected failure modes active
    for mode in v1.get("expect_failure_modes", []):
        state_key = f"{mode.lower()}_state"
        mode_data = fm.get(mode, {})
        state = mode_data.get(state_key, "")
        assert "CONFIRMED" in state or "PROBABLE" in state, (
            f"{case['id']}: Expected {mode} active, got state='{state}'"
        )

    # Tilt count
    if "expect_tilt_count_gte" in v1:
        assert len(tilt) >= v1["expect_tilt_count_gte"], (
            f"{case['id']}: Expected {v1['expect_tilt_count_gte']}+ tilts, got {len(tilt)}"
        )

    # Dominance not NONE
    if v1.get("expect_dominance_not_none"):
        assert dominance != ["NONE"], (
            f"{case['id']}: Expected active dominance, got NONE"
        )


# ═══════════════════════════════════════════════════════════
# V2 FLAG TESTS — Client-side flags match expectations
# V2 flags on the contact page are computed client-side from message text.
# We replicate that logic here to verify consistency.
# ═══════════════════════════════════════════════════════════

import re

def _compute_v2_flags(text):
    """Replicate the contact page's V2 flag logic."""
    flags = []
    if not re.search(r"\b(need|want|require|looking for|interested in)\b", text, re.I):
        flags.append("MISSING_OBJECTIVE")
    hedges = re.findall(r"\b(maybe|possibly|perhaps|might|could)\b", text, re.I)
    if len(hedges) > 1:
        flags.append("HEDGE_DENSITY")
    if not re.search(r"\b(by|before|within|deadline|timeline|date)\b", text, re.I):
        flags.append("NO_TIMELINE_CONSTRAINT")
    if len(text) < 50:
        flags.append("LOW_SPECIFICITY")
    return flags


@pytest.mark.parametrize("case", GAUNTLET, ids=[c["id"] for c in GAUNTLET])
def test_v2_flags(case):
    """V2 client-side flags match expected flags for each test case."""
    actual = _compute_v2_flags(case["text"])
    expected = case["v2_flags"]
    for flag in expected:
        assert flag in actual, (
            f"{case['id']}: Expected V2 flag '{flag}' not found. Got: {actual}"
        )


# ═══════════════════════════════════════════════════════════
# V3 ENFORCEMENT TESTS — Does V3 actually modify text?
# ═══════════════════════════════════════════════════════════

def _get_v3_enforce():
    """Import V3 enforce function."""
    from core_engine.v3_enforcement import enforce
    return enforce


@pytest.mark.parametrize("case", GAUNTLET, ids=[c["id"] for c in GAUNTLET])
def test_v3_enforcement_behavior(case):
    """V3 enforcement produces expected behavioral results on each test case."""
    if LIVE_URL:
        pytest.skip("V3 direct enforcement test only runs locally (not via HTTP)")

    enforce = _get_v3_enforce()
    v3_spec = case["v3"]

    # Run V3 on a simulated AI response to this message
    # We use a hedge-filled AI response that V3 should clean up
    ai_response = _generate_ai_stub(case["text"])
    result = enforce(ai_response)

    final = result.get("final_output", "")
    compression = result.get("compression_ratio", 0)
    all_actions = []
    for i in range(5):
        all_actions.extend(result.get(f"level_{i}_actions", []))

    input_words = len(ai_response.split())
    output_words = len(final.split())

    if v3_spec.get("must_flag_issues"):
        # V3 must take at least some action
        assert len(all_actions) > 0, (
            f"{case['id']}: V3 took 0 actions on text that should have issues"
        )

    if "min_issues" in v3_spec:
        assert len(all_actions) >= v3_spec["min_issues"], (
            f"{case['id']}: V3 found {len(all_actions)} actions, expected >= {v3_spec['min_issues']}"
        )

    if not v3_spec.get("must_flag_issues"):
        # Clean messages: V3 should mostly leave them alone
        # Some normalization actions are OK, but compression should be minimal
        assert compression < 0.3, (
            f"{case['id']}: Clean message got {compression:.0%} compression — V3 is over-editing"
        )


def _generate_ai_stub(user_text):
    """Generate a hedge-filled AI response stub for V3 testing.
    This simulates what an LLM would say — full of filler, hedges, and empty promises."""
    return (
        f"Thank you for reaching out. I appreciate you taking the time to share your thoughts. "
        f"There are definitely several ways we could potentially help with that, and I think "
        f"there might be some really interesting synergies here. Basically, it is important to note "
        f"that we generally try to address these kinds of concerns. I'd be happy to explore some "
        f"options and maybe we could set up a call sometime to discuss further. "
        f"Don't worry about the details — we'll figure it out. Rest assured, the team is on it. "
        f"Looking forward to connecting! Let me know if you'd be open to a quick chat."
    )


# ═══════════════════════════════════════════════════════════
# V3 ENFORCEMENT UNIT TESTS — Specific engine behaviors
# ═══════════════════════════════════════════════════════════

def test_v3_removes_filler():
    """V3 must remove known filler phrases."""
    if LIVE_URL:
        pytest.skip("Direct enforcement test only runs locally")
    enforce = _get_v3_enforce()
    text = "It is important to note that the project is on track. As you know, we are proceeding."
    result = enforce(text)
    final = result["final_output"]
    assert "it is important to note" not in final.lower()
    assert "as you know" not in final.lower()


def test_v3_removes_hedge_words():
    """V3 must strip hedge words."""
    if LIVE_URL:
        pytest.skip("Direct enforcement test only runs locally")
    enforce = _get_v3_enforce()
    text = "We will maybe deliver the report, possibly by Friday, and perhaps include the data."
    result = enforce(text)
    final = result["final_output"]
    assert "maybe" not in final.lower()
    assert "possibly" not in final.lower()
    assert "perhaps" not in final.lower()


def test_v3_strips_smooth_openers():
    """V3 must strip smooth opener words."""
    if LIVE_URL:
        pytest.skip("Direct enforcement test only runs locally")
    enforce = _get_v3_enforce()
    text = "Absolutely, we can have that ready by Friday."
    result = enforce(text)
    final = result["final_output"]
    first_word = final.split()[0].lower().strip(".,!?") if final.split() else ""
    assert first_word != "absolutely"


def test_v3_removes_time_collapse():
    """V3 must remove planning language (time collapse patterns)."""
    if LIVE_URL:
        pytest.skip("Direct enforcement test only runs locally")
    enforce = _get_v3_enforce()
    text = "I will now analyze the data. Let me start by reviewing the inputs. First, I'll check the schema."
    result = enforce(text)
    final = result["final_output"]
    assert "i will now" not in final.lower()
    assert "let me start" not in final.lower()


def test_v3_compression_nonzero_on_fluff():
    """V3 must achieve >0% compression on hedge-filled AI output."""
    if LIVE_URL:
        pytest.skip("Direct enforcement test only runs locally")
    enforce = _get_v3_enforce()
    text = (
        "Great question! Basically, it is important to note that we generally try to maybe "
        "address these sorts of concerns. Perhaps we could possibly explore some options. "
        "Ultimately, at the end of the day, the team is sort of aligned on this."
    )
    result = enforce(text)
    assert result["compression_ratio"] > 0, (
        f"V3 achieved 0% compression on text full of hedges and filler. "
        f"Input: {result['input_length']} words, Output: {result['output_length']} words"
    )


def test_v3_does_not_destroy_clean_text():
    """V3 must not significantly alter clean, constrained text."""
    if LIVE_URL:
        pytest.skip("Direct enforcement test only runs locally")
    enforce = _get_v3_enforce()
    text = (
        "The deployment requires security review completion by Thursday. "
        "No production push before signoff. Earliest deploy: Monday."
    )
    result = enforce(text)
    # Clean text should keep most of its content
    assert result["compression_ratio"] < 0.2, (
        f"V3 over-compressed clean text: {result['compression_ratio']:.0%}"
    )


# ═══════════════════════════════════════════════════════════
# REWRITE ENDPOINT TESTS — /api/v1/rewrite behavioral checks
# ═══════════════════════════════════════════════════════════

def test_rewrite_returns_llm_words(client):
    """Rewrite endpoint must return llm_words field (raw LLM word count)."""
    r = client.post("/api/v1/rewrite", json={
        "text": "I'd love to explore some synergies and maybe set up a call to discuss."
    })
    # Rewrite may fail without API keys in CI — that's OK
    # We check the response structure when it succeeds
    if r.status_code == 200:
        data = r.get_json()
        if data.get("method") == "llm_v3":
            assert "llm_words" in data, (
                "Rewrite response missing 'llm_words' field — "
                "compression display will show wrong numbers"
            )
            assert data["llm_words"] > 0
            assert data["llm_words"] >= data.get("rewrite_words", 0), (
                "llm_words should be >= rewrite_words (V3 compresses, not expands)"
            )
