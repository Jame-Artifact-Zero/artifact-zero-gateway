import functools
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

import db as database
from pre_score_gate import pre_score_gate

from core_engine.app import (
    NTI_VERSION,
    classify_tilt,
    compute_nii,
    detect_cca,
    detect_dce,
    detect_downstream_before_constraint,
    detect_l0_constraints,
    detect_l2_framing,
    detect_udds,
    objective_drift,
    objective_extract,
)


api_bp = Blueprint("api", __name__)


# Free tier scoring — no API key, IP-limited, database-persisted

_TIER_LIMITS = {
    "free": {"monthly": 10, "rpm": 5},
    "pro": {"monthly": 500, "rpm": 30},
    "power": {"monthly": 2000, "rpm": 60},
    "unlimited": {"monthly": 999999999, "rpm": 120},
    "starter": {"monthly": 10000, "rpm": 60},
    "core": {"monthly": 75000, "rpm": 120},
    "pipeline": {"monthly": 300000, "rpm": 300},
    "enterprise": {"monthly": 999999999, "rpm": 1000},
}



def _parse_score_json(result):
    """Parse score_json string and merge csi/nti into result dict."""
    sj = result.pop("score_json", None)

    if sj and isinstance(sj, str):
        try:
            parsed = json.loads(sj)
            if "csi" in parsed:
                result["csi"] = parsed["csi"]
            if "nti" in parsed:
                result["nti"] = parsed["nti"]
        except (json.JSONDecodeError, TypeError):
            pass

    elif sj and isinstance(sj, dict):
        if "csi" in sj:
            result["csi"] = sj["csi"]
        if "nti" in sj:
            result["nti"] = sj["nti"]

    return result


def _split_sentences(text):
    """Split text into sentences for per-sentence analysis."""
    return [
        sentence.strip()
        for sentence in re.split(r"[.!?]+", text)
        if sentence.strip() and len(sentence.strip()) > 3
    ]


def _fm_p(state):
    fm_pen = {
        "CONFIRMED": 0.30,
        "PROBABLE": 0.15,
        "FALSE": 0.00,
    }

    for key, value in fm_pen.items():
        if key in str(state):
            return value

    return 0.0


def _v1_score_fn(txt):
    """Adapter: run compute_nii on text and return dict with nii_score."""
    l0 = detect_l0_constraints(txt)
    tilt = classify_tilt(txt)
    dbc = detect_downstream_before_constraint("", txt, l0)
    nii = compute_nii("", txt, l0, dbc, tilt)
    return nii


def _month_start():
    now = datetime.now(timezone.utc)
    return now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    ).isoformat()


def _minute_key():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _consume_rate_limit(key_id, minute_key, rpm_limit):
    """Atomically consume one request from the PostgreSQL rate-limit bucket."""
    conn = database.db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS api_rate_limits (
                key_id TEXT NOT NULL,
                minute_key TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (key_id, minute_key)
            )
        """)

        cur.execute("""
            DELETE FROM api_rate_limits
            WHERE updated_at < NOW() - INTERVAL '2 hours'
        """)

        cur.execute(
            """
            INSERT INTO api_rate_limits (
                key_id,
                minute_key,
                request_count,
                updated_at
            )
            VALUES (%s, %s, 1, NOW())
            ON CONFLICT (key_id, minute_key)
            DO UPDATE SET
                request_count = api_rate_limits.request_count + 1,
                updated_at = NOW()
            WHERE api_rate_limits.request_count < %s
            RETURNING request_count
            """,
            (
                key_id,
                minute_key,
                rpm_limit,
            ),
        )

        row = cur.fetchone()

        if row is None:
            conn.rollback()
            return None

        request_count = int(row[0])
        conn.commit()
        return request_count

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def _free_usage_month():
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _ensure_free_usage_table(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS free_tier_usage (
            client_key TEXT NOT NULL,
            month_key TEXT NOT NULL,
            usage_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (client_key, month_key)
        )
    """)
    conn.commit()


def _consume_free_usage(client_key, month_key, monthly_limit=10):
    """Atomically consume one free use when the monthly limit allows it."""
    conn = database.db_connect()

    try:
        _ensure_free_usage_table(conn)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()

        cur.execute(
            """
                INSERT INTO free_tier_usage
                    (client_key, month_key, usage_count, updated_at)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT (client_key, month_key)
                DO UPDATE SET
                    usage_count = free_tier_usage.usage_count + 1,
                    updated_at = EXCLUDED.updated_at
                WHERE free_tier_usage.usage_count < %s
                RETURNING usage_count
                """,
            (
                client_key,
                month_key,
                now,
                monthly_limit,
            ),
        )

        row = cur.fetchone()

        if row is None:
            conn.rollback()
            return None

        usage_count = int(row[0])
        conn.commit()
        return usage_count

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def require_api_key(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        api_key = (
            request.headers.get("X-API-Key")
            or request.args.get("api_key")
        )

        if not api_key:
            return jsonify({
                "error": "Missing API key",
                "hint": "Pass key in X-API-Key header",
                "docs": "https://artifact0.com/docs",
            }), 401

        try:
            conn = database.db_connect()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, tier, monthly_limit, active, owner_email "
                "FROM api_keys WHERE id = %s",
                (api_key,),
            )

            row = cur.fetchone()
            conn.close()

        except Exception as e:
            print(f"[api] Key lookup error: {e}", flush=True)
            return jsonify({
                "error": "Database error",
                "detail": str(e),
            }), 500

        if not row:
            return jsonify({"error": "Invalid API key"}), 401

        key_id = row[0]
        tier = row[1]
        active = row[3]

        if not active:
            return jsonify({"error": "API key deactivated"}), 403

        # Credit balance check (replaces monthly limit for paid tiers)
        if tier != "free":
            try:
                from credits import (
                    COST_PER_SCORE,
                    get_balance,
                    get_user_id_for_api_key,
                )

                owner_user_id = get_user_id_for_api_key(key_id)

                if owner_user_id:
                    bal = get_balance(owner_user_id)
                    cost_cents = int(COST_PER_SCORE["api"] * 100)

                    if bal < cost_cents:
                        return jsonify({
                            "error": "Insufficient balance",
                            "balance": bal / 100,
                            "cost_per_score": cost_cents / 100,
                            "topup": (
                                f"{os.getenv('SITE_URL', 'https://artifact0.com')}"
                                "/dashboard"
                            ),
                        }), 402

                    request._credit_user_id = owner_user_id

            except ImportError:
                pass

        tier_config = _TIER_LIMITS.get(
            tier,
            _TIER_LIMITS["free"],
        )

        try:
            request_count = _consume_rate_limit(
                key_id,
                _minute_key(),
                tier_config["rpm"],
            )
        except Exception as e:
            print(f"[api] Rate-limit tracking error: {e}", flush=True)
            return jsonify({
                "error": "Rate limit unavailable",
            }), 500

        if request_count is None:
            return jsonify({
                "error": "Rate limit exceeded",
                "limit": f"{tier_config['rpm']} req/min",
            }), 429

        if tier == "free":
            usage_count = database.get_api_usage_count(
                key_id,
                _month_start(),
            )

            monthly_limit = row[2]

            if usage_count >= monthly_limit:
                return jsonify({
                    "error": "Free tier limit reached",
                    "usage": usage_count,
                    "limit": monthly_limit,
                    "upgrade": (
                        f"{os.getenv('SITE_URL', 'https://artifact0.com')}"
                        "/dashboard"
                    ),
                }), 429

        request._api_key_id = key_id
        request._api_tier = tier

        try:
            from account import _update_key_last_used

            _update_key_last_used(key_id)
        except Exception:
            pass

        return f(*args, **kwargs)

    return wrapper


@api_bp.route("/api/fortune500", methods=["GET"])
def api_fortune500_list():
    try:
        conn = database.db_connect()
        cur = conn.cursor()

        cur.execute(
            "SELECT slug, company_name, rank, url, nii_score, "
            "issue_count, last_checked "
            "FROM fortune500_scores ORDER BY rank"
        )
        cols = [description[0] for description in cur.description]
        rows = [
            dict(zip(cols, row))
            for row in cur.fetchall()
        ]

        conn.close()
        return jsonify({"companies": rows})

    except Exception:
        return jsonify({
            "companies": [],
            "note": "Scores loading. Check back soon.",
        })


@api_bp.route("/api/fortune500/<slug>", methods=["GET"])
def api_fortune500_detail(slug):
    try:
        conn = database.db_connect()
        cur = conn.cursor()

        for table in ["fortune500_scores", "vc_fund_scores"]:
            cur.execute(
                f"SELECT * FROM {table} WHERE slug = %s",
                (slug,),
            )
            row = cur.fetchone()

            if row:
                cols = [
                    description[0]
                    for description in cur.description
                ]
                result = dict(zip(cols, row))

                if (
                    "fund_name" in result
                    and "company_name" not in result
                ):
                    result["company_name"] = result["fund_name"]

                result = _parse_score_json(result)
                conn.close()
                return jsonify(result)

        conn.close()
        return jsonify({"error": "Not found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/vc-funds", methods=["GET"])
def api_vc_funds_list():
    try:
        conn = database.db_connect()
        cur = conn.cursor()

        cur.execute(
            "SELECT slug, fund_name, rank, url, nii_score, "
            "issue_count, last_checked "
            "FROM vc_fund_scores ORDER BY rank"
        )
        cols = [description[0] for description in cur.description]
        rows = [
            dict(zip(cols, row))
            for row in cur.fetchall()
        ]

        conn.close()
        return jsonify({"funds": rows})

    except Exception:
        return jsonify({
            "funds": [],
            "note": "Scores loading. Check back soon.",
        })


@api_bp.route("/api/vc-funds/<slug>", methods=["GET"])
def api_vc_fund_detail(slug):
    try:
        conn = database.db_connect()
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM vc_fund_scores WHERE slug = %s",
            (slug,),
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404

        cols = [
            description[0]
            for description in cur.description
        ]
        result = dict(zip(cols, row))

        result = _parse_score_json(result)
        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_bp.route("/api/v1/score/free", methods=["POST"])
def api_score_free():
    ip = request.headers.get(
        "X-Forwarded-For",
        request.remote_addr,
    )

    client_key = (ip or "unknown").split(",")[0].strip()
    month_key = _free_usage_month()

    t0 = time.time()
    payload = request.get_json() or {}
    text = payload.get("text", "").strip()

    if not text:
        return jsonify({
            "error": "Missing 'text' field",
        }), 400

    if len(text) > 50000:
        return jsonify({
            "error": "Text exceeds 50,000 character limit",
        }), 400

    gate = pre_score_gate(text)

    if not gate["pass"]:
        return jsonify({
            "error": gate["msg"],
            "gate": gate["reason"],
            "status": "rejected",
        }), 422

    try:
        l0 = detect_l0_constraints(text)
        obj = objective_extract(text)
        tilt = classify_tilt(text)

        framing = detect_l2_framing(text)

        try:
            from highlight_map import get_highlights

            axis2, highlights = get_highlights(
                text,
                framing=framing,
            )
        except Exception:
            axis2, highlights = None, []

        dbc = detect_downstream_before_constraint(
            "",
            text,
            l0,
        )

        nii = compute_nii(
            "",
            text,
            l0,
            dbc,
            tilt,
        )

        try:
            from core_engine.nti_signals import detect_signals

            signals = detect_signals(text)
            detail = nii.get("detail", {})

            if (
                "CONFIRMED" in str(detail.get("cca", ""))
                or "PROBABLE" in str(detail.get("cca", ""))
            ):
                signals["signals_summary"]["CCA_COLLAPSE"] = max(
                    1,
                    signals["signals_summary"].get(
                        "CCA_COLLAPSE",
                        0,
                    ),
                )

            if (
                "CONFIRMED" in str(detail.get("dce", ""))
                or "PROBABLE" in str(detail.get("dce", ""))
            ):
                signals["signals_summary"]["DCE_DEFERRAL"] = max(
                    1,
                    signals["signals_summary"].get(
                        "DCE_DEFERRAL",
                        0,
                    ),
                )

            if (
                "CONFIRMED" in str(detail.get("udds", ""))
                or "PROBABLE" in str(detail.get("udds", ""))
            ):
                signals["signals_summary"]["UDDS_DRIFT"] = max(
                    1,
                    signals["signals_summary"].get(
                        "UDDS_DRIFT",
                        0,
                    ),
                )

            tilt_to_signal = {
                "T8_PRESSURE_OPTIMIZATION": "SOCIAL_PRESSURE",
                "T7_AUTHORITY_ANCHOR": "AUTHORITY_ELEVATED",
                "T6_ABSOLUTE_FRAMING": "ABSOLUTE_LANGUAGE",
            }

            for code in tilt or []:
                signal_name = tilt_to_signal.get(code)

                if signal_name:
                    signals["signals_summary"][signal_name] = max(
                        1,
                        signals["signals_summary"].get(
                            signal_name,
                            0,
                        ),
                    )

        except Exception:
            signals = {
                "catalog_version": "nti-signals-v1",
                "signal_catalog": {},
                "signals_summary": {},
                "signals_detected": [],
                "highlights": [],
            }

        detail = nii.get("detail", {})
        udds_state = detail.get("udds", "FALSE")
        dce_state = detail.get("dce", "FALSE")
        cca_state = detail.get("cca", "FALSE")

        dominance = []

        if (
            "CONFIRMED" in udds_state
            or "PROBABLE" in udds_state
        ):
            dominance.append("UDDS")

        if (
            "CONFIRMED" in dce_state
            or "PROBABLE" in dce_state
        ):
            dominance.append("DCE")

        if (
            "CONFIRMED" in cca_state
            or "PROBABLE" in cca_state
        ):
            dominance.append("CCA")

        if not dominance:
            dominance = ["NONE"]

    except Exception as e:
        return jsonify({
            "error": "Scoring error",
            "detail": str(e),
        }), 500

    try:
        usage_count = _consume_free_usage(
            client_key,
            month_key,
            monthly_limit=10,
        )
    except Exception as e:
        print(f"[api] Free-tier usage consumption failed: {e}", flush=True)
        return jsonify({
            "error": "Usage tracking unavailable",
        }), 500

    if usage_count is None:
        return jsonify({
            "error": "Free tier limit reached (10/month)",
            "upgrade": "https://artifact0.com/docs#pricing",
        }), 429

    latency_ms = int((time.time() - t0) * 1000)

    result = {
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {
                "q1": nii.get("q1"),
                "q2": nii.get("q2"),
                "q3": nii.get("q3"),
                "q4": nii.get("q4"),
                "d5": nii.get(
                    "d5_failure_mode_severity"
                ),
            },
        },
        "failure_modes": {
            "UDDS": udds_state,
            "DCE": dce_state,
            "CCA": cca_state,
            "dominance": dominance,
        },
        "tilt": {
            "tags": tilt,
            "count": len(tilt),
        },
        "signals": signals,
        "highlights": highlights,
        "axis2": axis2,
        "framing": framing,
        "meta": {
            "latency_ms": latency_ms,
            "text_length": len(text),
            "word_count": len(text.split()),
            "tier": "free",
            "usage_this_month": usage_count,
            "monthly_limit": 10,
        },
    }

    try:
        from core_engine.v3_enforcement import self_audit

        audit = self_audit(
            text,
            objective=(
                obj.get("objective_text")
                if obj
                else None
            ),
        )

        result["v3"] = {
            "enforced_text": audit["enforced_text"],
            "actions_taken": audit["actions_taken"],
            "time_collapse_applied": audit[
                "time_collapse_applied"
            ],
            "compression_ratio": audit[
                "compression_ratio"
            ],
            "passed": audit["passed"],
        }

    except Exception as e:
        result["v3"] = {
            "error": str(e),
            "passed": True,
        }

    return jsonify(result)


@api_bp.route("/api/v1/score", methods=["POST"])
@require_api_key
def api_score():
    t0 = time.time()
    payload = request.get_json() or {}
    text = payload.get("text", "").strip()

    if not text:
        return jsonify({
            "error": "Missing 'text' field",
        }), 400

    if len(text) > 50000:
        return jsonify({
            "error": "Text exceeds 50,000 character limit",
        }), 400

    gate = pre_score_gate(text)

    if not gate["pass"]:
        return jsonify({
            "error": gate["msg"],
            "gate": gate["reason"],
            "status": "rejected",
        }), 422

    try:
        l0 = detect_l0_constraints(text)
        obj = objective_extract(text)
        drift = objective_drift("", text)
        framing = detect_l2_framing(text)
        tilt = classify_tilt(text)
        udds = detect_udds("", text, l0)
        dce = detect_dce(text, l0)
        cca = detect_cca("", text)

        dbc = detect_downstream_before_constraint(
            "",
            text,
            l0,
        )

        nii = compute_nii(
            "",
            text,
            l0,
            dbc,
            tilt,
        )

        dominance = []

        if cca["cca_state"] in [
            "CCA_CONFIRMED",
            "CCA_PROBABLE",
        ]:
            dominance.append("CCA")

        if udds["udds_state"] in [
            "UDDS_CONFIRMED",
            "UDDS_PROBABLE",
        ]:
            dominance.append("UDDS")

        if dce["dce_state"] in [
            "DCE_CONFIRMED",
            "DCE_PROBABLE",
        ]:
            dominance.append("DCE")

        if not dominance:
            dominance = ["NONE"]

    except Exception as e:
        print(f"[api] Scoring error: {e}", flush=True)
        return jsonify({
            "error": "Scoring engine error",
            "detail": str(e),
        }), 500

    latency_ms = int((time.time() - t0) * 1000)
    usage_id = str(uuid.uuid4())

    database.record_api_usage(
        usage_id,
        request._api_key_id,
        "/api/v1/score",
        latency_ms,
        200,
    )

    credit_info = {}

    if (
        hasattr(request, "_credit_user_id")
        and request._credit_user_id
    ):
        try:
            from credits import deduct_credit

            ok, new_bal = deduct_credit(
                request._credit_user_id,
                "api",
                request._api_key_id,
            )

            credit_info = {
                "charged": 0.01,
                "balance": new_bal / 100,
            }

        except Exception as e:
            print(
                f"[api] Credit deduction error: {e}",
                flush=True,
            )

    v3_result = {"passed": True}

    try:
        from core_engine.v3_enforcement import self_audit

        audit = self_audit(
            text,
            objective=(
                obj.get("objective_text")
                if obj
                else None
            ),
        )

        v3_result = {
            "enforced_text": audit["enforced_text"],
            "actions_taken": audit["actions_taken"],
            "time_collapse_applied": audit[
                "time_collapse_applied"
            ],
            "compression_ratio": audit[
                "compression_ratio"
            ],
            "passed": audit["passed"],
        }

    except Exception as e:
        v3_result = {
            "error": str(e),
            "passed": True,
        }

    return jsonify({
        "status": "ok",
        "version": NTI_VERSION,
        "score": {
            "nii": nii.get("nii_score"),
            "nii_label": nii.get("nii_label"),
            "components": {
                "q1": nii.get("q1"),
                "q2": nii.get("q2"),
                "q3": nii.get("q3"),
                "q4": nii.get("q4"),
                "d5": nii.get(
                    "d5_failure_mode_severity"
                ),
            },
        },
        "failure_modes": {
            "UDDS": udds["udds_state"],
            "DCE": dce["dce_state"],
            "CCA": cca["cca_state"],
            "dominance": dominance,
        },
        "tilt": {
            "tags": tilt,
            "count": len(tilt),
        },
        "v3": v3_result,
        "meta": {
            "latency_ms": latency_ms,
            "text_length": len(text),
            "word_count": len(text.split()),
            "tier": request._api_tier,
        },
        **(
            {"credits": credit_info}
            if credit_info
            else {}
        ),
    })


def _letter_race(text):
    """Pick model by racing letters through user text."""
    source = re.sub(
        r"[^a-zA-Z]",
        "",
        text,
    ).lower()

    models = [
        {
            "name": "claude",
            "api": "anthropic",
            "color": "#d97706",
        },
        {
            "name": "chatgpt",
            "api": "openai",
            "color": "#10b981",
        },
    ]

    for index in range(len(source)):
        for model in models:
            position = 0

            for character_index in range(index + 1):
                if (
                    character_index < len(source)
                    and position < len(model["name"])
                    and source[character_index]
                    == model["name"][position]
                ):
                    position += 1

            if position >= len(model["name"]):
                return model

    best = models[0]
    best_ratio = 0

    for model in models:
        position = 0

        for character in source:
            if (
                position < len(model["name"])
                and character == model["name"][position]
            ):
                position += 1

        ratio = position / len(model["name"])

        if ratio > best_ratio:
            best_ratio = ratio
            best = model

    return best


def _call_llm(model_info, prompt, system_prompt):
    """Call the selected LLM API. Returns response text."""
    from urllib.request import Request, urlopen

    api = model_info["api"]
    timeout = 15

    if api == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "")

        if not key:
            return None, "No Anthropic API key"

        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }).encode()

        req = Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
        )

        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        text = ""

        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        return text, None

    if api == "openai":
        key = os.getenv("OPENAI_API_KEY", "")

        if not key:
            return None, "No OpenAI API key"

        body = json.dumps({
            "model": "gpt-4o-mini",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }).encode()

        req = Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )

        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())

        return (
            data["choices"][0]["message"]["content"],
            None,
        )

    if api == "xai":
        key = os.getenv("XAI_API_KEY", "")

        if not key:
            return None, "No XAI API key"

        body = json.dumps({
            "model": "grok-2-latest",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        }).encode()

        req = Request(
            "https://api.x.ai/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )

        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())

        return (
            data["choices"][0]["message"]["content"],
            None,
        )

    if api == "google":
        key = os.getenv("GOOGLE_API_KEY", "")

        if not key:
            return None, "No Google API key"

        body_dict = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": prompt,
                        }
                    ],
                }
            ],
            "generationConfig": {
                "maxOutputTokens": 1024,
            },
        }

        if system_prompt:
            body_dict["systemInstruction"] = {
                "parts": [
                    {
                        "text": system_prompt,
                    }
                ]
            }

        body = json.dumps(body_dict).encode()

        req = Request(
            (
                "https://generativelanguage.googleapis.com/"
                "v1beta/models/gemini-2.0-flash:"
                f"generateContent?key={key}"
            ),
            data=body,
            headers={
                "Content-Type": "application/json",
            },
        )

        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())

        try:
            candidates = data.get("candidates", [])

            if not candidates:
                reason = data.get(
                    "promptFeedback",
                    {},
                ).get(
                    "blockReason",
                    "no candidates",
                )
                return None, f"Gemini blocked: {reason}"

            parts = candidates[0].get(
                "content",
                {},
            ).get(
                "parts",
                [],
            )

            if not parts:
                return None, "Gemini empty response"

            return parts[0]["text"], None

        except (KeyError, IndexError) as e:
            return (
                None,
                f"Unexpected Google API response: {e}",
            )

    return None, f"Unknown API: {api}"


def _call_ungov(
    model,
    ungoverned_prompt,
    ungoverned_system,
):
    try:
        result, err = _call_llm(
            model,
            ungoverned_prompt,
            ungoverned_system,
        )

        if not result:
            print(
                f"[contact] ungoverned failed: {err}",
                flush=True,
            )

        return result

    except Exception as e:
        print(
            f"[contact] ungoverned exception: {e}",
            flush=True,
        )
        return None


def _call_gov(
    model,
    governed_prompt,
    governed_system,
):
    try:
        result, err = _call_llm(
            model,
            governed_prompt,
            governed_system,
        )
        return result, err

    except Exception as e:
        return None, str(e)[:200]


def _normalize_text(text):
    text = re.sub(
        r"artifact0\s*\.\s*[Cc]om",
        "artifact0.com",
        text,
    )
    text = re.sub(
        r"(\w)\s+\.\s+(com|io|org|net)",
        r"\1.\2",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(\w+)\s*@\s*(\w)",
        r"\1@\2",
        text,
    )
    text = re.sub(
        r"/safe\s+[Cc]heck",
        "/safecheck",
        text,
    )
    return text


@api_bp.route("/api/v1/rewrite", methods=["POST"])
def api_rewrite():
    """LLM-powered structural rewrite. Letter-race selects model, V3 enforces output."""
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.time()
    data = request.get_json(
        force=True,
        silent=True,
    ) or {}

    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "error": "text required",
        }), 400

    if len(text) > 5000:
        return jsonify({
            "error": "text too long (max 5000 chars)",
        }), 400

    gate = pre_score_gate(text)

    if not gate["pass"]:
        return jsonify({
            "error": gate["msg"],
            "gate": gate["reason"],
            "status": "rejected",
        }), 422

    l0 = detect_l0_constraints(text)
    obj = objective_extract(text)
    tilt = classify_tilt(text)
    udds = detect_udds("", text, l0)
    dce = detect_dce(text, l0)
    cca = detect_cca("", text)

    dbc = detect_downstream_before_constraint(
        "",
        text,
        l0,
    )

    nii = compute_nii(
        "",
        text,
        l0,
        dbc,
        tilt,
    )

    nii_score = nii.get("nii_score", 0)

    components = {
        key: value
        for key, value in nii.items()
        if key.startswith("q")
    }

    failure_modes = {
        "UDDS": udds.get("udds_state", ""),
        "DCE": dce.get("dce_state", ""),
        "CCA": cca.get("cca_state", ""),
    }

    model = _letter_race(text)

    word_count = len(text.split())

    is_question = (
        text.strip().rstrip(".!").endswith("?")
        or text.lower().startswith((
            "what ",
            "when ",
            "where ",
            "who ",
            "how ",
            "why ",
            "which ",
            "can ",
            "could ",
            "will ",
            "would ",
            "do ",
            "does ",
            "is ",
            "are ",
        ))
    )

    is_short = word_count <= 15

    issues = []

    if not (is_short and is_question):
        if (
            (components.get("q1") or 0) < 0.7
            and not is_short
        ):
            issues.append(
                "Missing explicit constraints or conditions"
            )

        if (
            (components.get("q2") or 0) < 0.7
            and word_count > 20
        ):
            issues.append(
                "Main ask is buried — should lead"
            )

        if (
            (components.get("q3") or 0) < 0.7
            and not is_question
        ):
            issues.append(
                "No deadline or enforcement boundary"
            )

        if (
            (components.get("q4") or 0) < 0.7
            and len(tilt) > 0
        ):
            issues.append(
                "Weak tilt resistance — hedge language detected"
            )

    if "CONFIRMED" in str(
        failure_modes.get("UDDS", "")
    ):
        issues.append(
            "UDDS: Agreement given before the actual ask was stated"
        )

    if "CONFIRMED" in str(
        failure_modes.get("DCE", "")
    ):
        issues.append(
            "DCE: Decision is deferred instead of made"
        )

    if "CONFIRMED" in str(
        failure_modes.get("CCA", "")
    ):
        issues.append(
            "CCA: Capability claimed without constraint backing"
        )

    ungoverned_prompt = (
        "Write a reply to this message.\n\n"
        f"MESSAGE:\n{text}"
    )

    ungoverned_system = (
        "You are a customer service representative. "
        "Reply to the message you receive."
    )

    governed_system = (
        "You are Artifact Zero, a structural enforcement company in Knoxville, Tennessee. "
        "You built NTI — a deterministic engine that scores and stabilizes communication. "
        "No LLM in the scoring. Same input, same output, every time.\n\n"
        "Someone sent a message through the contact page. Reply directly on behalf of Artifact Zero.\n\n"
        "VOICE:\n"
        "- Direct, sharp, confident. No filler.\n"
        "- Short sentences. Fragments fine.\n"
        "- Never: revolutionary, game-changing, synergy, seamless, excited, thrilled, ecosystem.\n"
        "- No exclamation marks. Warm but not performative.\n\n"
        "HARD RULES — these override everything else:\n"
        "1. NEVER schedule or promise a meeting or call. NEVER give out any email address. Direct all connection to artifact0.com/docs\n"
        "2. IF SELLING SOMETHING (warranties, SEO, software, services, anything): acknowledge with dry humor, "
        "then suggest they run their pitch through the API. "
        "Example: The engine caught commitment hedges in that pitch. Try artifact0.com/safecheck before the next send.\n"
        "3. IF GIVING FEEDBACK OR SUGGESTIONS: thank them genuinely, say it will be reviewed, zero commitments on what changes.\n"
        "4. IF WANTING TO PARTNER OR INVEST: direct to artifact0.com/docs for API access and contact info.\n"
        "5. IF A REAL PROSPECT: one sentence on what NTI solves for their specific situation. "
        "Direct them to artifact0.com/docs — full API access, unlimited use cases, total control. "
        "Tell your team. Not your competitors.\n\n"
        "REPLY RULES:\n"
        "1. First sentence shows you read their message.\n"
        "2. Answer the question or address the need directly.\n"
        "3. One next step — artifact0.com/docs. Never give an email address. Never 'we will be in touch.'\n"
        "4. 40-80 words total.\n"
        "5. No sign-off. No Best, Thanks, Regards.\n"
        "6. Return only the reply text. No commentary. No quotes."
    )

    governed_prompt = (
        f"THEIR MESSAGE:\n{text}\n\n"
        "Write a reply from Artifact Zero to this person."
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_ungoverned = executor.submit(
            _call_ungov,
            model,
            ungoverned_prompt,
            ungoverned_system,
        )

        future_governed = executor.submit(
            _call_gov,
            model,
            governed_prompt,
            governed_system,
        )

        llm_ungoverned = future_ungoverned.result()
        llm_governed, err = future_governed.result()

    llm_text = llm_governed

    if not llm_text:
        return jsonify({
            "rewrite": "",
            "llm_raw": llm_ungoverned or "",
            "llm_ungoverned": llm_ungoverned or "",
            "model": model["name"],
            "model_color": model["color"],
            "method": "v3_rule_only",
            "fallback_reason": err or "LLM call failed",
            "original_words": len(text.split()),
            "rewrite_words": 0,
            "nii_score": nii_score,
            "issues": issues,
            "latency_ms": int(
                (time.time() - t0) * 1000
            ),
        })

    from core_engine.v3_enforcement import enforce

    llm_words = len(llm_text.split())

    v3_result = enforce(
        llm_text,
        objective=obj.get("objective_text"),
    )

    final = _normalize_text(
        v3_result["final_output"]
    )

    if llm_ungoverned:
        llm_ungoverned = _normalize_text(
            llm_ungoverned
        )

    original_words = len(text.split())
    rewrite_words = len(final.split())

    v3_compression = (
        abs(llm_words - rewrite_words)
        / max(llm_words, 1)
        * 100
    )

    return jsonify({
        "rewrite": final,
        "llm_raw": llm_ungoverned or "",
        "llm_governed": llm_text,
        "model": model["name"],
        "model_color": model["color"],
        "method": "llm_v3",
        "original_words": original_words,
        "llm_words": llm_words,
        "rewrite_words": rewrite_words,
        "compression": f"{v3_compression:.0f}%",
        "nii_score": nii_score,
        "issues": issues,
        "v3_actions": (
            v3_result.get("level_0_actions", [])
            + v3_result.get("level_1_actions", [])
        ),
        "latency_ms": int(
            (time.time() - t0) * 1000
        ),
    })
