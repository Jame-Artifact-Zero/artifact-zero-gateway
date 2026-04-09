import os, json, time, io, re, secrets, threading
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, render_template, session
import http.client, ssl

operator_bp = Blueprint('operator', __name__)

OPERATOR_NTI_KEY = os.environ.get('OPERATOR_API_KEY', 'az_21f0f7405b504f38840334b53f0e63ae523fb6a3c50f556c')
CLAUDE_MODEL     = 'claude-sonnet-4-6'


def _get_anthropic_key():
    return os.environ.get('ANTHROPIC_API_KEY', '')


def require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_role = session.get('role', '')
        user_id   = session.get('user_id', '')
        if not user_id or user_role not in ('admin', 'operator'):
            token     = request.headers.get('X-Operator-Token', '')
            env_token = os.environ.get('OPERATOR_TOKEN', 'aztempfix2026')
            if token != env_token:
                return jsonify({'error': 'Unauthorized', 'hint': 'Admin access required'}), 403
        return f(*args, **kwargs)
    return wrapper


# -- UI -------------------------------------------------------------------------

@operator_bp.route('/operator')
def operator_room():
    user_role = session.get('role', '')
    user_id   = session.get('user_id', '')
    is_admin  = (user_id and user_role in ('admin', 'operator'))

    if not is_admin:
        op_token  = request.cookies.get('op_token', '')
        env_token = os.environ.get('OPERATOR_TOKEN', 'aztempfix2026')
        if op_token != env_token:
            from flask import redirect
            return redirect('/login?next=/operator')

    return render_template('operator.html', api_key=OPERATOR_NTI_KEY)


# -- CHAT PROXY -----------------------------------------------------------------

@operator_bp.route('/operator/api/chat', methods=['POST'])
def operator_chat():
    anthropic_key = _get_anthropic_key()
    if not anthropic_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured in ECS'}), 500

    payload  = request.get_json() or {}
    system   = payload.get('system', '')
    messages = payload.get('messages', [])
    jos      = payload.get('jos', {})

    jos_context = []
    if jos.get('objective'):  jos_context.append(f"OBJECTIVE: {jos['objective']}")
    if jos.get('constraint'): jos_context.append(f"CONSTRAINTS: {jos['constraint']}")
    if jos.get('nogo'):       jos_context.append(f"NO-GO ZONES: {jos['nogo']}")
    if jos.get('done'):       jos_context.append(f"DONE WHEN: {jos['done']}")
    jos_context.append("CLOSURE AUTHORITY: Jame")

    if jos_context:
        system += "\n\nCURRENT JOS:\n" + "\n".join(jos_context)

    # -- Inject prior session context ------------------------------------------
    prior = _get_prior_session_context()
    if prior:
        system = "PRIOR SESSION CONTEXT:\n" + prior + "\n\n" + system

    claude_payload = {
        'model':      CLAUDE_MODEL,
        'max_tokens': 4096,
        'system':     system,
        'messages':   messages[-40:],
    }

    try:
        body = json.dumps(claude_payload).encode()
        ctx  = ssl.create_default_context()
        conn = http.client.HTTPSConnection('api.anthropic.com', 443, context=ctx, timeout=60)
        conn.request('POST', '/v1/messages', body=body, headers={
            'Content-Type':      'application/json',
            'Content-Length':    str(len(body)),
            'x-api-key':         anthropic_key,
            'anthropic-version': '2023-06-01',
            'Connection':        'close',
        })
        r   = conn.getresponse()
        raw = r.read()
        conn.close()
        data = json.loads(raw)

        try:
            _store_session(messages, data, jos)
            threading.Thread(target=_auto_write_context, args=(messages, data), daemon=True).start()
        except Exception:
            pass
        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# -- TOOL EXECUTION -------------------------------------------------------------

@operator_bp.route('/operator/run', methods=['POST'])
def operator_run():
    payload = request.get_json() or {}
    tool    = payload.get('tool', '')

    if tool == 'signal':
        return _run_signal_scan()
    elif tool == 'market':
        return _run_market_model()
    elif tool == 'fortune500':
        return _run_fortune500()
    elif tool == 'score':
        text = payload.get('text', '').strip()
        if not text:
            return jsonify({'error': 'No text provided'}), 400
        return _run_nti_score(text)
    else:
        return jsonify({'error': f'Unknown tool: {tool}'}), 400


def _run_signal_scan():
    SIGNAL_FEEDS = [
        ('CNBC',       'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
        ('BBC',        'https://feeds.bbci.co.uk/news/rss.xml'),
        ('NPR',        'https://feeds.npr.org/1001/rss.xml'),
        ('TechCrunch', 'https://techcrunch.com/feed/'),
        ('ARS',        'https://feeds.arstechnica.com/arstechnica/index'),
    ]

    results      = []
    s0_delta     = 0.0
    total_nii    = 0
    scored_count = 0
    errors       = []

    for source, url in SIGNAL_FEEDS:
        try:
            from rss_proxy import fetch_rss
            feed_data = fetch_rss(url, max_items=3)
            items = feed_data.get('items', [])
            for item in items[:2]:
                title = item.get('title', '')
                if not title:
                    continue
                score_result = _score_text_internal(title)
                nii = score_result.get('nii', 0)
                total_nii    += nii
                scored_count += 1
                results.append({
                    'source': source,
                    'title':  title[:100],
                    'nii':    nii,
                    'flags':  score_result.get('flags', []),
                })
        except Exception as e:
            errors.append(f"{source}: {str(e)[:60]}")

    avg_nii = round(total_nii / max(1, scored_count))

    if avg_nii >= 70:
        s0_delta  = +0.02
        direction = 'CLEAR — high-integrity signal environment'
    elif avg_nii >= 50:
        s0_delta  = 0.00
        direction = 'MIXED — moderate integrity, no strong directional signal'
    else:
        s0_delta  = -0.03
        direction = 'NOISY — low-integrity signal environment, elevated uncertainty'

    results.sort(key=lambda x: x['nii'])

    lines = [f"NTI SIGNAL SCAN — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]
    lines.append(f"Sources scanned: {len(SIGNAL_FEEDS)} | Headlines scored: {scored_count}")
    lines.append(f"Avg NII: {avg_nii}% | S0 delta: {s0_delta:+.3f}")
    lines.append(f"Environment: {direction}")
    lines.append("")
    lines.append("LOWEST INTEGRITY HEADLINES:")
    for r in results[:5]:
        flag_str = ', '.join(r['flags'][:2]) if r['flags'] else 'none'
        lines.append(f"  [{r['source']}] NII {r['nii']}% — {r['title']}")
        if r['flags']:
            lines.append(f"    flags: {flag_str}")

    if errors:
        lines.append(f"\nFeed errors: {'; '.join(errors)}")

    return jsonify({
        'tool':     'signal',
        'result':   '\n'.join(lines),
        'summary':  {'avg_nii': avg_nii, 's0_delta': s0_delta, 'scored': scored_count},
        's0_delta': s0_delta,
    })


def _run_market_model():
    lines      = [f"S&P itB0 MODEL — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]
    components = {}

    try:
        import yfinance as yf

        SAMPLE_TICKERS = ['SPY', 'QQQ', 'IWM', 'DIA', 'VIX']
        data = yf.download(SAMPLE_TICKERS, period='2d', interval='1d', progress=False, auto_adjust=True)

        closes = {}
        if hasattr(data['Close'], 'columns'):
            for t in SAMPLE_TICKERS:
                try:
                    vals = data['Close'][t].dropna().values
                    if len(vals) >= 2:
                        closes[t] = {'prev': float(vals[-2]), 'curr': float(vals[-1])}
                except Exception:
                    pass

        if 'SPY' in closes:
            spy_chg = (closes['SPY']['curr'] - closes['SPY']['prev']) / closes['SPY']['prev']
            components['spy_momentum'] = round(spy_chg, 4)
        else:
            spy_chg = 0.0
            components['spy_momentum'] = 'unavailable'

        if 'VIX' in closes:
            vix = closes['VIX']['curr']
            components['vix'] = round(vix, 2)
            vix_signal = -0.05 if vix > 25 else (0.02 if vix < 15 else 0.0)
        else:
            vix        = None
            vix_signal = 0.0
            components['vix'] = 'unavailable'

        if 'IWM' in closes and 'SPY' in closes:
            iwm_chg        = (closes['IWM']['curr'] - closes['IWM']['prev']) / closes['IWM']['prev']
            breadth_signal = 0.02 if (iwm_chg > 0 and spy_chg > 0) else (-0.02 if (iwm_chg < 0 and spy_chg < 0) else 0.0)
            components['breadth_signal'] = round(breadth_signal, 3)
        else:
            breadth_signal = 0.0
            components['breadth_signal'] = 'unavailable'

        s0 = round(0.50 + (spy_chg * 5) + vix_signal + breadth_signal, 3)
        s0 = max(0.0, min(1.0, s0))
        components['s0_computed'] = s0

        if s0 > 0.55:
            call       = 'UP'
            confidence = 'MODERATE' if s0 < 0.65 else 'HIGH'
        elif s0 < 0.45:
            call       = 'DOWN'
            confidence = 'MODERATE' if s0 > 0.35 else 'HIGH'
        else:
            call       = 'FLAT/UNCERTAIN'
            confidence = 'LOW'

        components['call']       = call
        components['confidence'] = confidence

        lines.append(f"S0 = {s0} | Call: {call} | Confidence: {confidence}")
        lines.append("")
        lines.append("COMPONENTS:")
        lines.append(f"  SPY momentum: {components.get('spy_momentum', 'n/a')}")
        lines.append(f"  VIX:          {components.get('vix', 'n/a')}")
        lines.append(f"  Breadth:      {components.get('breadth_signal', 'n/a')}")
        lines.append("")
        lines.append("NOTE: Layer 1 breadth model. FOMC day — elevated override probability.")
        lines.append("Named override variables: Fed decision (4% cut probability CME FedWatch).")

    except ImportError:
        lines.append("yfinance not available in this environment.")
        lines.append("S0 cannot be computed server-side without market data access.")
        components['error'] = 'yfinance unavailable'

    except Exception as e:
        lines.append(f"Market data error: {str(e)[:120]}")
        components['error'] = str(e)[:120]

    return jsonify({
        'tool':     'market',
        'result':   '\n'.join(lines),
        'summary':  components,
        's0_delta': components.get('s0_computed', None),
    })


def _run_fortune500():
    lines = [f"FORTUNE 500 SCOREBOARD — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]

    try:
        import db as database
        conn = database.db_connect()
        cur  = conn.cursor()

        if database.USE_PG:
            cur.execute("""
                SELECT company_name, nii_score, issue_count, score_json
                FROM fortune500_scores
                ORDER BY nii_score ASC
                LIMIT 10
            """)
            rows = cur.fetchall()
            conn.close()

            if rows:
                lines.append("10 LOWEST NII SCORES:")
                for r in rows:
                    name        = r[0]
                    score       = r[1]
                    issue_count = r[2] or 0
                    score_json  = r[3] or ''

                    # Derive band from nii_score
                    if score >= 70:
                        band = 'HIGH'
                    elif score >= 50:
                        band = 'MODERATE'
                    else:
                        band = 'LOW'

                    # Pull flags from score_json if present
                    flags_display = ''
                    if score_json:
                        try:
                            sj = json.loads(score_json) if isinstance(score_json, str) else score_json
                            flags = sj.get('flags', [])
                            if flags:
                                flags_display = ', '.join(flags[:3])
                        except Exception:
                            pass

                    lines.append(f"  {name[:35]:<35} NII {score:.1f}%  [{band}]  issues: {issue_count}")
                    if flags_display:
                        lines.append(f"    flags: {flags_display}")
            else:
                lines.append("No scored companies in database.")
        else:
            conn.close()
            lines.append("Database unavailable — PostgreSQL required.")

    except Exception as e:
        lines.append(f"DB error: {str(e)[:120]}")

    return jsonify({
        'tool':    'fortune500',
        'result':  '\n'.join(lines),
        'summary': {},
    })


def _run_nti_score(text: str):
    result = _score_text_internal(text)

    nii   = result.get('nii', 0)
    flags = result.get('flags', [])
    label = 'HIGH INTEGRITY' if nii >= 70 else 'MODERATE' if nii >= 50 else 'LOW INTEGRITY'

    lines = ["NTI SCORE RESULT"]
    lines.append(f"NII: {nii}% — {label}")
    lines.append(f"Text length: {len(text)} chars")
    if flags:
        lines.append(f"Flags: {', '.join(flags)}")
    else:
        lines.append("Flags: none")

    fm = result.get('failure_modes', {})
    if fm:
        lines.append("")
        lines.append("FAILURE MODES:")
        for k, v in fm.items():
            if str(v) not in ('FALSE', 'false', ''):
                lines.append(f"  {k}: {v}")

    return jsonify({
        'tool':    'score',
        'result':  '\n'.join(lines),
        'summary': {'nii': nii, 'flags': flags},
        'raw':     result,
    })


@operator_bp.route('/operator/score', methods=['POST'])
def operator_score():
    payload = request.get_json() or {}
    text    = payload.get('text', '').strip()
    if not text:
        return jsonify({'nii': 0, 'flags': [], 'error': 'no text'}), 400
    result = _score_text_internal(text)
    return jsonify(result)


def _score_text_internal(text: str) -> dict:
    """
    Score text using internal NTI engine.
    Imports from core_engine to avoid ECS app import path breakage.
    Falls back to app-level imports if core_engine unavailable.
    """
    try:
        from core_engine.v3_engine import run_v3
        from core_engine.scoring import compute_nii
        from core_engine.detection import (
            detect_l0_constraints,
            detect_downstream_before_constraint,
            detect_udds,
            detect_dce,
            detect_cca,
        )
        from core_engine.v2_engine import classify_tilt

        l0   = detect_l0_constraints(text)
        tilt = classify_tilt(text)
        dbc  = detect_downstream_before_constraint('', text, l0)
        nii  = compute_nii('', text, l0, dbc, tilt)
        udds = detect_udds('', text, l0)
        dce  = detect_dce(text, l0)
        cca  = detect_cca('', text)

        nii_val = nii.get('nii_score', 0)
        if nii_val <= 1.0:
            nii_val = round(nii_val * 100)

        flags = []
        if udds.get('udds_state', '') in ('UDDS_CONFIRMED', 'UDDS_PROBABLE'):
            flags.append('UDDS')
        if dce.get('dce_state', '') in ('DCE_CONFIRMED', 'DCE_PROBABLE'):
            flags.append('DCE')
        if cca.get('cca_state', '') in ('CCA_CONFIRMED', 'CCA_PROBABLE'):
            flags.append('CCA')

        return {
            'nii':   nii_val,
            'flags': flags,
            'failure_modes': {
                'UDDS': udds.get('udds_state', 'FALSE'),
                'DCE':  dce.get('dce_state',  'FALSE'),
                'CCA':  cca.get('cca_state',  'FALSE'),
            },
            'tilt': tilt,
        }

    except ImportError:
        pass

    # Fallback: app-level imports
    try:
        import app as main_app
        l0   = main_app.detect_l0_constraints(text)
        tilt = main_app.classify_tilt(text)
        dbc  = main_app.detect_downstream_before_constraint('', text, l0)
        nii  = main_app.compute_nii('', text, l0, dbc, tilt)
        udds = main_app.detect_udds('', text, l0)
        dce  = main_app.detect_dce(text, l0)
        cca  = main_app.detect_cca('', text)

        nii_val = nii.get('nii_score', 0)
        if nii_val <= 1.0:
            nii_val = round(nii_val * 100)

        flags = []
        if udds.get('udds_state', '') in ('UDDS_CONFIRMED', 'UDDS_PROBABLE'):
            flags.append('UDDS')
        if dce.get('dce_state', '') in ('DCE_CONFIRMED', 'DCE_PROBABLE'):
            flags.append('DCE')
        if cca.get('cca_state', '') in ('CCA_CONFIRMED', 'CCA_PROBABLE'):
            flags.append('CCA')

        return {
            'nii':   nii_val,
            'flags': flags,
            'failure_modes': {
                'UDDS': udds.get('udds_state', 'FALSE'),
                'DCE':  dce.get('dce_state',  'FALSE'),
                'CCA':  cca.get('cca_state',  'FALSE'),
            },
            'tilt': tilt,
        }

    except Exception as e:
        return {'nii': 0, 'flags': [], 'error': str(e)}


# -- FILE UPLOAD ----------------------------------------------------------------

@operator_bp.route('/operator/upload', methods=['POST'])
def operator_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'No file in request'}), 400

    f         = request.files['file']
    filename  = f.filename or 'upload'
    ext       = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    raw_bytes = f.read()

    ALLOWED = {'txt', 'pdf', 'docx', 'csv', 'md', 'html'}
    if ext not in ALLOWED:
        return jsonify({'error': f'File type .{ext} not supported. Allowed: {", ".join(ALLOWED)}'}), 400

    MAX_BYTES = 2 * 1024 * 1024
    if len(raw_bytes) > MAX_BYTES:
        return jsonify({'error': 'File exceeds 2MB limit'}), 400

    text             = ''
    extraction_note  = ''

    try:
        if ext in ('txt', 'md', 'csv', 'html'):
            text = raw_bytes.decode('utf-8', errors='replace')

        elif ext == 'pdf':
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                    pages = [p.extract_text() or '' for p in pdf.pages[:20]]
                text = '\n'.join(pages)
            except ImportError:
                extraction_note = 'pdfplumber not available'
                text = raw_bytes.decode('utf-8', errors='replace')

        elif ext == 'docx':
            try:
                from docx import Document
                doc   = Document(io.BytesIO(raw_bytes))
                paras = [p.text for p in doc.paragraphs if p.text.strip()]
                text  = '\n'.join(paras)
            except ImportError:
                extraction_note = 'python-docx not available'
                text = raw_bytes.decode('utf-8', errors='replace')

    except Exception as e:
        return jsonify({'error': f'Text extraction failed: {str(e)[:120]}'}), 500

    text = text.strip()
    if not text:
        return jsonify({'error': 'No text could be extracted from file'}), 422

    score_text   = text[:50000]
    score_result = _score_text_internal(score_text)
    nii          = score_result.get('nii', 0)
    flags        = score_result.get('flags', [])
    label        = 'HIGH INTEGRITY' if nii >= 70 else 'MODERATE' if nii >= 50 else 'LOW INTEGRITY'

    preview  = score_text[:400].replace('\n', ' ')
    char_cnt = len(text)
    word_cnt = len(text.split())

    lines = ["FILE UPLOAD — NTI SCORE"]
    lines.append(f"File: {filename}")
    lines.append(f"Size: {char_cnt:,} chars | {word_cnt:,} words")
    if extraction_note:
        lines.append(f"Note: {extraction_note}")
    lines.append("")
    lines.append(f"NII: {nii}% — {label}")
    if flags:
        lines.append(f"Flags: {', '.join(flags)}")
    fm = score_result.get('failure_modes', {})
    for k, v in fm.items():
        if str(v) not in ('FALSE', 'false', ''):
            lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append(f"PREVIEW: {preview[:200]}...")

    return jsonify({
        'tool':       'upload',
        'filename':   filename,
        'char_count': char_cnt,
        'word_count': word_cnt,
        'nii':        nii,
        'flags':      flags,
        'label':      label,
        'result':     '\n'.join(lines),
        'summary':    score_result,
    })


# -- DAILY UPDATE ENDPOINT (p0047) --------------------------------------------

@operator_bp.route('/operator/run_daily_update', methods=['POST'])
def operator_run_daily_update():
    from daily_update_cloud import run_daily_update
    try:
        result = run_daily_update()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'output': str(e), 'error': str(e)}), 500


# -- CONTEXT ENDPOINT (p0045) ---------------------------------------------------

@operator_bp.route('/operator/context', methods=['POST'])
def operator_context():
    """
    Accept a session blob JSON body and write to RDS operator_context table.
    Called by exp_append scripts after writing local JSON file.
    Returns { status: ok, id: ... }
    """
    payload = request.get_json() or {}
    if not payload:
        return jsonify({'error': 'Empty payload'}), 400

    try:
        import db as database
        conn = database.db_connect()
        cur  = conn.cursor()

        if database.USE_PG:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operator_context (
                    id          TEXT PRIMARY KEY,
                    created_at  TIMESTAMPTZ DEFAULT NOW(),
                    blob_json   TEXT NOT NULL,
                    source      TEXT,
                    summary     TEXT
                )
            """)

            ctx_id  = 'ctx_' + secrets.token_hex(8)
            source  = payload.get('source', 'exp_append')
            # Build a short summary from blob keys
            summary_parts = []
            for key in ('push', 'experiment', 'objective', 'status'):
                if payload.get(key):
                    summary_parts.append(f"{key}={payload[key]}")
            summary = ' | '.join(summary_parts[:4])

            cur.execute("""
                INSERT INTO operator_context (id, blob_json, source, summary)
                VALUES (%s, %s, %s, %s)
            """, (ctx_id, json.dumps(payload), source, summary))
            conn.commit()
            conn.close()

            return jsonify({'status': 'ok', 'id': ctx_id, 'summary': summary})
        else:
            conn.close()
            return jsonify({'status': 'ok', 'id': 'local', 'note': 'SQLite — blob not persisted to RDS'})

    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 500


# -- SESSION STORAGE ------------------------------------------------------------

@operator_bp.route('/operator/context', methods=['GET'])
@require_admin
def operator_context_get():
    """
    Return last N operator_context rows as JSON for the memory panel.
    Query param: limit (default 10, max 50)
    """
    try:
        import db as database
        if not database.USE_PG:
            return jsonify({'status': 'ok', 'rows': [], 'note': 'SQLite - no RDS'})

        limit = min(int(request.args.get('limit', 10)), 50)

        conn = database.db_connect()
        cur  = conn.cursor()
        cur.execute("""
            SELECT id, created_at, source, summary, blob_json
            FROM operator_context
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
        conn.close()

        result = []
        for row in rows:
            blob = {}
            try:
                blob = json.loads(row[4]) if row[4] else {}
            except Exception:
                pass
            result.append({
                'id':         row[0],
                'created_at': str(row[1])[:19],
                'source':     row[2],
                'summary':    row[3],
                'push':       blob.get('push', ''),
                'status':     blob.get('status', ''),
                'objective':  blob.get('objective', ''),
            })

        return jsonify({'status': 'ok', 'rows': result, 'count': len(result)})

    except Exception as e:
        return jsonify({'error': str(e)[:200]}), 500


@operator_bp.route('/operator/sessions', methods=['GET'])
def operator_sessions():
    try:
        import db as database
        conn = database.db_connect()
        cur  = conn.cursor()
        if database.USE_PG:
            cur.execute("""
                SELECT id, created_at, summary
                FROM operator_sessions
                ORDER BY created_at DESC
                LIMIT 20
            """)
            rows     = cur.fetchall()
            sessions = [{'id': r[0], 'created_at': str(r[1]), 'summary': r[2]} for r in rows]
        else:
            sessions = []
        conn.close()
        return jsonify({'sessions': sessions})
    except Exception as e:
        return jsonify({'sessions': [], 'note': str(e)})


def _get_prior_session_context() -> str:
    """
    Fetch the last 10 operator session blobs from RDS.
    Merges decisions, key_facts, named_concepts, open_questions across rows.
    Returns a plain text block for system prompt injection.
    Returns empty string on any failure ? never blocks the request.
    """
    try:
        import db as database
        if not database.USE_PG:
            return ''
        conn = database.db_connect()
        cur  = conn.cursor()

        # Pull last session summary
        last_session_line = ''
        try:
            cur.execute("""
                SELECT summary, created_at
                FROM operator_sessions
                ORDER BY created_at DESC
                LIMIT 1
            """)
            row = cur.fetchone()
            if row and row[0]:
                ts = str(row[1])[:16] if row[1] else ''
                last_session_line = f"[LAST SESSION {ts}] {row[0]}"
        except Exception:
            pass

        # Pull last 10 context blobs and merge
        ctx_lines = []
        try:
            cur.execute("""
                SELECT summary, blob_json, created_at
                FROM operator_context
                ORDER BY created_at DESC
                LIMIT 10
            """)
            rows = cur.fetchall()
            conn.close()

            if rows:
                # Merge fields across all rows (newest first = highest priority)
                merged_push            = ''
                merged_status          = ''
                merged_objective       = ''
                merged_key_facts       = []
                merged_decisions       = []
                merged_named_concepts  = {}
                merged_open_questions  = []
                latest_ts              = ''

                for row in rows:
                    ts   = str(row[2])[:16] if row[2] else ''
                    blob = {}
                    try:
                        blob = json.loads(row[1]) if row[1] else {}
                    except Exception:
                        pass

                    # Take push/status/objective from most recent non-empty
                    if not merged_push and blob.get('push'):
                        merged_push = blob['push']
                    if not merged_status and blob.get('status'):
                        merged_status = blob['status']
                    if not merged_objective and blob.get('objective'):
                        merged_objective = blob['objective']
                    if not latest_ts:
                        latest_ts = ts

                    # Accumulate lists ? deduplicate
                    kf = blob.get('key_facts', '')
                    if isinstance(kf, list):
                        for item in kf:
                            if item and item not in merged_key_facts:
                                merged_key_facts.append(item)
                    elif isinstance(kf, str) and kf:
                        if kf not in merged_key_facts:
                            merged_key_facts.append(kf)

                    for d in (blob.get('decisions') or []):
                        if d and d not in merged_decisions:
                            merged_decisions.append(d)

                    for q in (blob.get('open_questions') or []):
                        if q and q not in merged_open_questions:
                            merged_open_questions.append(q)

                    # Merge named concepts ? earlier rows don't overwrite newer
                    for k, v in (blob.get('named_concepts') or {}).items():
                        if k not in merged_named_concepts:
                            merged_named_concepts[k] = v

                # Build injection block
                parts = [f"[CONTEXT BLOB {latest_ts}] push={merged_push} | status={merged_status}"]
                if merged_objective:
                    parts.append(f"  objective: {merged_objective[:300]}")
                if merged_decisions:
                    parts.append("  decisions:")
                    for d in merged_decisions[:20]:
                        parts.append(f"    - {str(d)[:200]}")
                if merged_key_facts:
                    parts.append("  key_facts:")
                    for f in merged_key_facts[:20]:
                        parts.append(f"    - {str(f)[:200]}")
                if merged_named_concepts:
                    parts.append("  named_concepts:")
                    for k, v in list(merged_named_concepts.items())[:15]:
                        parts.append(f"    {k}: {str(v)[:300]}")
                if merged_open_questions:
                    parts.append("  open_questions:")
                    for q in merged_open_questions[:10]:
                        parts.append(f"    - {str(q)[:200]}")

                ctx_lines = parts

        except Exception:
            try:
                conn.close()
            except Exception:
                pass

        if not last_session_line and not ctx_lines:
            return ''

        result_parts = []
        if last_session_line:
            result_parts.append(last_session_line)
        if ctx_lines:
            result_parts.extend(ctx_lines)

        # Token budget cap ? 3000 chars max
        result = '\n'.join(result_parts)
        if len(result) > 3000:
            result = result[:3000] + '\n  ...[truncated]'

        return result

    except Exception:
        return ''



def _auto_write_context(messages, response):
    """
    Background thread: extract push/status/decisions/named_concepts from
    the latest exchange and POST to /operator/context silently.
    Fires after every assistant response. Never blocks the request.
    """
    try:
        import db as database
        if not database.USE_PG:
            return

        # Extract assistant response text
        assistant_text = ''
        try:
            assistant_text = response.get('content', [{}])[0].get('text', '')[:3000]
        except Exception:
            pass

        # Extract last user message
        user_text = ''
        for m in reversed(messages):
            if m.get('role') == 'user':
                user_text = m.get('content', '')[:1000]
                break

        if not assistant_text and not user_text:
            return

        # Build blob from exchange content
        # Extract push tag if present in user message
        push = ''
        push_match = re.search(r'p\d{4}[_\w]*', user_text + assistant_text)
        if push_match:
            push = push_match.group(0)

        # Extract decisions ? lines starting with decision markers
        decisions = []
        for line in (assistant_text + '\n' + user_text).split('\n'):
            line = line.strip()
            if any(line.lower().startswith(w) for w in ('decided:', 'decision:', 'approved:', 'confirmed:', 'done:')):
                decisions.append(line[:200])

        # Extract key facts ? bullet lines
        key_facts = []
        for line in assistant_text.split('\n'):
            line = line.strip()
            if line.startswith('- ') or line.startswith('* '):
                key_facts.append(line[2:200])
        key_facts = key_facts[:10]

        blob = {
            'push':        push or 'auto',
            'status':      'active',
            'objective':   f'Auto-captured exchange ? {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")}',
            'key_facts':   key_facts,
            'decisions':   decisions,
            'named_concepts': {},
            'open_questions': [],
            'source':      'auto_writer',
            'assistant_snippet': assistant_text[:500],
        }

        summary = f"push={blob['push']} | auto-write | {blob['objective']}"

        conn = database.db_connect()
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS operator_context (
                id          TEXT PRIMARY KEY,
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                blob_json   TEXT NOT NULL,
                source      TEXT DEFAULT 'auto',
                summary     TEXT
            )
        """)
        ctx_id = 'ctx_auto_' + secrets.token_hex(8)
        cur.execute("""
            INSERT INTO operator_context (id, blob_json, source, summary)
            VALUES (%s, %s, %s, %s)
        """, (ctx_id, json.dumps(blob), 'auto_writer', summary))
        conn.commit()
        conn.close()

    except Exception:
        pass


def _store_session(messages, response, jos):
    try:
        import db as database
        conn = database.db_connect()
        cur  = conn.cursor()
        if database.USE_PG:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS operator_sessions (
                    id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    messages_json TEXT,
                    response_json TEXT,
                    jos_json TEXT,
                    summary TEXT
                )
            """)
            summary = ''
            for m in reversed(messages):
                if m.get('role') == 'user':
                    summary = m.get('content', '')[:120]
                    break
            sid = 'op_' + secrets.token_hex(8)
            cur.execute("""
                INSERT INTO operator_sessions (id, messages_json, response_json, jos_json, summary)
                VALUES (%s, %s, %s, %s, %s)
            """, (sid, json.dumps(messages), json.dumps(response), json.dumps(jos), summary))
            conn.commit()
        conn.close()
    except Exception:
        pass
