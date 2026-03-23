"""
operator_room.py
================
Flask blueprint for the Artifact Zero Operator Room.

Routes:
  GET  /operator             — operator room UI (admin only)
  POST /operator/api/chat    — Claude API proxy with NTI governance
  GET  /operator/sessions    — session history from RDS
  POST /operator/run         — server-side tool execution (signal scan, S&P model, fortune500, score)
  POST /operator/upload      — file upload → text extraction → NTI scoring → result in chat

Environment variables required:
  ANTHROPIC_API_KEY   — Claude API key (must be set in ECS)
  OPERATOR_API_KEY    — NTI enterprise key for operator scoring (set in ECS)
"""

import os, json, time, io, re
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, render_template, session
import http.client, ssl

operator_bp = Blueprint('operator', __name__)

OPERATOR_NTI_KEY = os.environ.get('OPERATOR_API_KEY', 'az_21f0f7405b504f38840334b53f0e63ae523fb6a3c50f556c')
CLAUDE_MODEL     = 'claude-sonnet-4-6'


def _get_anthropic_key():
    """Read ANTHROPIC_API_KEY at request time — not module load time."""
    return os.environ.get('ANTHROPIC_API_KEY', '')


def require_admin(f):
    """Simple admin check — user must be logged in with admin role."""
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


# ── UI ─────────────────────────────────────────────────────────────────────────

@operator_bp.route('/operator')
def operator_room():
    """Serve the operator room UI."""
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


# ── CHAT PROXY ─────────────────────────────────────────────────────────────────

@operator_bp.route('/operator/api/chat', methods=['POST'])
def operator_chat():
    """
    Proxy to Claude API with operator context.
    Input:  { system, messages, jos }
    Output: Claude API response JSON
    """
    anthropic_key = _get_anthropic_key()
    if not anthropic_key:
        return jsonify({'error': 'ANTHROPIC_API_KEY not configured in ECS'}), 500

    payload  = request.get_json() or {}
    system   = payload.get('system', '')
    messages = payload.get('messages', [])
    jos      = payload.get('jos', {})

    # Inject JOS state
    jos_context = []
    if jos.get('objective'):  jos_context.append(f"OBJECTIVE: {jos['objective']}")
    if jos.get('constraint'): jos_context.append(f"CONSTRAINTS: {jos['constraint']}")
    if jos.get('nogo'):       jos_context.append(f"NO-GO ZONES: {jos['nogo']}")
    if jos.get('done'):       jos_context.append(f"DONE WHEN: {jos['done']}")
    jos_context.append("CLOSURE AUTHORITY: Jame")

    if jos_context:
        system += "\n\nCURRENT JOS:\n" + "\n".join(jos_context)

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
        except Exception:
            pass

        return jsonify(data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── TOOL EXECUTION ─────────────────────────────────────────────────────────────

@operator_bp.route('/operator/run', methods=['POST'])
def operator_run():
    """
    Server-side tool execution.
    Input:  { tool: 'signal' | 'market' | 'fortune500' | 'score', text?: str }
    Output: { tool, result, summary, s0_delta? }
    """
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
    """
    Fetch live RSS feeds, score top headlines through NTI, compute S0 delta.
    Returns structured signal summary for chat injection.
    """
    SIGNAL_FEEDS = [
        ('CNBC',       'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114'),
        ('BBC',        'https://feeds.bbci.co.uk/news/rss.xml'),
        ('NPR',        'https://feeds.npr.org/1001/rss.xml'),
        ('TechCrunch', 'https://techcrunch.com/feed/'),
        ('ARS',        'https://feeds.arstechnica.com/arstechnica/index'),
    ]

    results     = []
    s0_delta    = 0.0
    total_nii   = 0
    scored_count = 0
    errors      = []

    for source, url in SIGNAL_FEEDS:
        try:
            from rss_proxy import fetch_rss
            feed_data = fetch_rss(url, max_items=3)
            items = feed_data.get('items', [])
            for item in items[:2]:
                title = item.get('title', '')
                if not title:
                    continue
                # Score through NTI (internal, no API key needed for internal call)
                score_result = _score_text_internal(title)
                nii = score_result.get('nii', 0)
                total_nii   += nii
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

    # S0 delta: positive if avg NII is high integrity (market comms clear), negative if low
    if avg_nii >= 70:
        s0_delta = +0.02
        direction = 'CLEAR — high-integrity signal environment'
    elif avg_nii >= 50:
        s0_delta = 0.00
        direction = 'MIXED — moderate integrity, no strong directional signal'
    else:
        s0_delta = -0.03
        direction = 'NOISY — low-integrity signal environment, elevated uncertainty'

    # Sort by NII ascending (most flagged first)
    results.sort(key=lambda x: x['nii'])

    # Build summary text for chat
    lines = [f"NTI SIGNAL SCAN — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]
    lines.append(f"Sources scanned: {len(SIGNAL_FEEDS)} | Headlines scored: {scored_count}")
    lines.append(f"Avg NII: {avg_nii}% | S₀ delta: {s0_delta:+.3f}")
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
    """
    Build current S0 from available market indicators.
    Returns directional call and S0 components.
    """
    components = {}
    lines      = [f"S&P itB₀ MODEL — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]

    # Attempt to pull live breadth from Yahoo Finance via yfinance
    try:
        import yfinance as yf

        # Sample breadth from key S&P components
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

        # Compute SPY momentum
        if 'SPY' in closes:
            spy_chg = (closes['SPY']['curr'] - closes['SPY']['prev']) / closes['SPY']['prev']
            components['spy_momentum'] = round(spy_chg, 4)
        else:
            spy_chg = 0.0
            components['spy_momentum'] = 'unavailable'

        # VIX level (risk-off signal)
        if 'VIX' in closes:
            vix = closes['VIX']['curr']
            components['vix'] = round(vix, 2)
            vix_signal = -0.05 if vix > 25 else (0.02 if vix < 15 else 0.0)
        else:
            vix = None
            vix_signal = 0.0
            components['vix'] = 'unavailable'

        # IWM vs SPY (breadth proxy)
        if 'IWM' in closes and 'SPY' in closes:
            iwm_chg = (closes['IWM']['curr'] - closes['IWM']['prev']) / closes['IWM']['prev']
            breadth_signal = 0.02 if (iwm_chg > 0 and spy_chg > 0) else (-0.02 if (iwm_chg < 0 and spy_chg < 0) else 0.0)
            components['breadth_signal'] = round(breadth_signal, 3)
        else:
            breadth_signal = 0.0
            components['breadth_signal'] = 'unavailable'

        # Composite S0
        s0 = round(0.50 + (spy_chg * 5) + vix_signal + breadth_signal, 3)
        s0 = max(0.0, min(1.0, s0))
        components['s0_computed'] = s0

        # Directional call
        if s0 > 0.55:
            call = 'UP'
            confidence = 'MODERATE' if s0 < 0.65 else 'HIGH'
        elif s0 < 0.45:
            call = 'DOWN'
            confidence = 'MODERATE' if s0 > 0.35 else 'HIGH'
        else:
            call = 'FLAT/UNCERTAIN'
            confidence = 'LOW'

        components['call'] = call
        components['confidence'] = confidence

        lines.append(f"S₀ = {s0} | Call: {call} | Confidence: {confidence}")
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
        lines.append("S₀ cannot be computed server-side without market data access.")
        lines.append("Run sp500_itb0_full.py locally for full model output.")
        components['error'] = 'yfinance unavailable'

    except Exception as e:
        lines.append(f"Market data error: {str(e)[:120]}")
        components['error'] = str(e)[:120]

    return jsonify({
        'tool':       'market',
        'result':     '\n'.join(lines),
        'summary':    components,
        's0_delta':   components.get('s0_computed', None),
    })


def _run_fortune500():
    """
    Return lowest NTI scoring Fortune 500 companies from DB.
    """
    lines = [f"FORTUNE 500 SCOREBOARD — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"]

    try:
        import db as database
        conn = database.db_connect()
        cur  = conn.cursor()

        if database.USE_PG:
            cur.execute("""
                SELECT company_name, nii_score, band, flags
                FROM fortune500_scores
                ORDER BY nii_score ASC
                LIMIT 10
            """)
            rows = cur.fetchall()
            conn.close()

            if rows:
                lines.append(f"10 LOWEST NII SCORES:")
                for r in rows:
                    name, score, band, flags = r[0], r[1], r[2] or '', r[3] or ''
                    flag_display = flags[:80] if flags else 'none'
                    lines.append(f"  {name[:35]:<35} NII {score}%  [{band}]")
                    if flags:
                        lines.append(f"    flags: {flag_display}")
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
    """Score provided text through NTI engine, return full result."""
    result = _score_text_internal(text)

    nii   = result.get('nii', 0)
    flags = result.get('flags', [])
    label = 'HIGH INTEGRITY' if nii >= 70 else 'MODERATE' if nii >= 50 else 'LOW INTEGRITY'

    lines = [f"NTI SCORE RESULT"]
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


def _score_text_internal(text: str) -> dict:
    """
    Score text using internal NTI engine functions (no HTTP, no API key).
    Mirrors what /api/v1/score does internally.
    """
    try:
        # Import scoring functions from app context
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
            'nii': nii_val,
            'flags': flags,
            'failure_modes': {
                'UDDS': udds.get('udds_state', 'FALSE'),
                'DCE':  dce.get('dce_state', 'FALSE'),
                'CCA':  cca.get('cca_state', 'FALSE'),
            },
            'tilt': tilt,
        }
    except Exception as e:
        return {'nii': 0, 'flags': [], 'error': str(e)}


# ── FILE UPLOAD ────────────────────────────────────────────────────────────────

@operator_bp.route('/operator/upload', methods=['POST'])
def operator_upload():
    """
    File upload → text extraction → NTI scoring → result for chat.
    Accepts: .txt, .pdf, .docx, .csv, .md
    Returns: { filename, char_count, nii, flags, preview, tool_result }
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file in request'}), 400

    f         = request.files['file']
    filename  = f.filename or 'upload'
    ext       = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    raw_bytes = f.read()

    ALLOWED = {'txt', 'pdf', 'docx', 'csv', 'md', 'html'}
    if ext not in ALLOWED:
        return jsonify({'error': f'File type .{ext} not supported. Allowed: {", ".join(ALLOWED)}'}), 400

    MAX_BYTES = 2 * 1024 * 1024  # 2MB
    if len(raw_bytes) > MAX_BYTES:
        return jsonify({'error': 'File exceeds 2MB limit'}), 400

    # Extract text
    text = ''
    extraction_note = ''

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

    # Truncate for scoring (NTI engine limit)
    score_text = text[:50000]

    # Score
    score_result = _score_text_internal(score_text)
    nii   = score_result.get('nii', 0)
    flags = score_result.get('flags', [])
    label = 'HIGH INTEGRITY' if nii >= 70 else 'MODERATE' if nii >= 50 else 'LOW INTEGRITY'

    preview  = score_text[:400].replace('\n', ' ')
    char_cnt = len(text)
    word_cnt = len(text.split())

    lines = [f"FILE UPLOAD — NTI SCORE"]
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


# ── SESSION STORAGE ────────────────────────────────────────────────────────────

@operator_bp.route('/operator/sessions', methods=['GET'])
def operator_sessions():
    """Return recent operator sessions from database."""
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


def _store_session(messages, response, jos):
    """Store operator session to RDS."""
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
            import secrets
            sid = 'op_' + secrets.token_hex(8)
            cur.execute("""
                INSERT INTO operator_sessions (id, messages_json, response_json, jos_json, summary)
                VALUES (%s, %s, %s, %s, %s)
            """, (sid, json.dumps(messages), json.dumps(response), json.dumps(jos), summary))
            conn.commit()
        conn.close()
    except Exception:
        pass
