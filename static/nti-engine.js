/**
 * nti-engine.js v1.0
 * Unified NTI engine for all pages.
 * One script tag = V1 scoring + V3 rewrite + letter-race + drop-in UI.
 *
 * Usage:
 *   <script src="/static/nti-engine.js"></script>
 *
 *   // Score text
 *   const result = await NTI.score("your text here");
 *
 *   // Rewrite text (LLM-powered, server-side)
 *   const rewrite = await NTI.rewrite("your text here");
 *
 *   // Letter-race model pick
 *   const model = NTI.pickModel("your text here");
 *
 *   // Mount score + rewrite widget into any container
 *   NTI.mount('#my-container');          // full widget with textarea
 *   NTI.mountInline('#my-container');    // compact score bar only
 *
 *   // Score bar (attach to any textarea, live scoring on pause)
 *   NTI.scoreBar('#my-textarea', '#score-output');
 *
 *   // V3 enforce on any AI response before displaying
 *   NTI.enforceResponse(aiText, containerEl);
 */
const NTI = (() => {
  // ═══════════════════════════════════════
  // CONFIG
  // ═══════════════════════════════════════
  const SCORE_URL = '/nti';
  const REWRITE_URL = '/api/v1/rewrite';
  const DEBOUNCE_MS = 800;

  // ═══════════════════════════════════════
  // LETTER-RACE MODEL SELECTION
  // ═══════════════════════════════════════
  const MODELS = [
    { name: 'Claude',  letters: 'claude',  color: '#d97706', api: 'anthropic' },
    { name: 'Grok',    letters: 'grok',    color: '#8b5cf6', api: 'xai' },
    { name: 'ChatGPT', letters: 'chatgpt', color: '#10b981', api: 'openai' },
    { name: 'Gemini',  letters: 'gemini',  color: '#3b82f6', api: 'google' }
  ];

  function pickModel(text) {
    const s = text.replace(/[^a-zA-Z]/g, '').toLowerCase();
    for (let i = 0; i < s.length; i++) {
      for (const m of MODELS) {
        let pos = 0;
        for (let j = 0; j <= i && pos < m.letters.length; j++) {
          if (s[j] === m.letters[pos]) pos++;
        }
        if (pos >= m.letters.length) return { ...m };
      }
    }
    // Fallback: highest ratio
    let best = MODELS[0], bestR = 0;
    for (const m of MODELS) {
      let pos = 0;
      for (const ch of s) { if (pos < m.letters.length && ch === m.letters[pos]) pos++; }
      const r = pos / m.letters.length;
      if (r > bestR) { bestR = r; best = m; }
    }
    return { ...best };
  }

  // ═══════════════════════════════════════
  // CORE API CALLS
  // ═══════════════════════════════════════
  async function score(text, options = {}) {
    const body = { text };
    if (options.prior) body.prior_ai = options.prior;
    if (options.objective) body.objective = options.objective;
    try {
      const r = await fetch(SCORE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      return await r.json();
    } catch (e) {
      return { error: e.message };
    }
  }

  async function rewrite(text) {
    try {
      const r = await fetch(REWRITE_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      return await r.json();
    } catch (e) {
      return { error: e.message };
    }
  }

  // ═══════════════════════════════════════
  // V3 ENFORCE (wraps AI responses before display)
  // ═══════════════════════════════════════
  async function enforceResponse(aiText, containerEl, options = {}) {
    const result = await score(aiText, { prior: options.humanInput || '' });
    const rw = await rewrite(aiText);

    if (containerEl && typeof containerEl === 'string') {
      containerEl = document.querySelector(containerEl);
    }
    if (!containerEl) return { score: result, rewrite: rw };

    // Display enforced version
    const v3Text = rw.rewrite || aiText;
    const badge = rw.model ? `<span style="font-family:monospace;font-size:11px;color:${rw.model_color || '#00e89c'}">${rw.model}</span>` : '';
    containerEl.innerHTML = v3Text;
    if (options.showBadge && rw.model) {
      containerEl.insertAdjacentHTML('afterend',
        `<div style="font-size:11px;color:#5a6275;margin-top:4px">V3 · ${badge} · ${rw.latency_ms || '?'}ms</div>`
      );
    }
    return { score: result, rewrite: rw };
  }

  // ═══════════════════════════════════════
  // SCORE BAR (attach to any textarea)
  // ═══════════════════════════════════════
  function scoreBar(textareaSelector, outputSelector, options = {}) {
    const textarea = document.querySelector(textareaSelector);
    const output = document.querySelector(outputSelector);
    if (!textarea || !output) return;

    let timer = null;
    const debounce = options.debounce || DEBOUNCE_MS;

    function renderBar(data) {
      if (data.error) {
        output.innerHTML = `<span style="color:#ef4444">Error</span>`;
        return;
      }
      const s = data.score || {};
      const nii = s.nii_score != null ? s.nii_score : (data.nii_score || 0);
      const label = nii >= 0.8 ? 'STRONG' : nii >= 0.5 ? 'MODERATE' : 'WEAK';
      const color = nii >= 0.8 ? '#00e89c' : nii >= 0.5 ? '#f59e0b' : '#ef4444';
      const issues = (data.findings || []).length;
      const lat = data.latency_ms || '?';

      output.innerHTML = `<span style="color:${color};font-weight:700;font-family:monospace">${(nii * 100).toFixed(0)}</span>`
        + `<span style="color:#5a6275;margin-left:8px;font-size:12px">${label}</span>`
        + (issues ? `<span style="color:#f59e0b;margin-left:8px;font-size:12px">${issues} issue${issues > 1 ? 's' : ''}</span>` : '')
        + `<span style="color:#3d4452;margin-left:8px;font-size:11px">${lat}ms</span>`;
    }

    textarea.addEventListener('input', () => {
      clearTimeout(timer);
      const text = textarea.value.trim();
      if (!text) { output.innerHTML = ''; return; }
      timer = setTimeout(async () => {
        output.innerHTML = '<span style="color:#5a6275">scoring...</span>';
        const data = await score(text);
        renderBar(data);
      }, debounce);
    });
  }

  // ═══════════════════════════════════════
  // REWRITE BUTTON (add to any page)
  // ═══════════════════════════════════════
  function rewriteButton(textareaSelector, outputSelector, options = {}) {
    const textarea = document.querySelector(textareaSelector);
    const output = document.querySelector(outputSelector);
    if (!textarea) return;

    // Create button if not exists
    let btn = document.querySelector(options.buttonSelector || '#nti-rewrite-btn');
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'nti-rewrite-btn';
      btn.textContent = 'Rewrite with V3';
      btn.style.cssText = 'background:#00e89c;color:#000;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-family:monospace;font-size:13px;font-weight:600;margin-top:8px;width:100%';
      textarea.parentElement.appendChild(btn);
    }

    btn.addEventListener('click', async () => {
      const text = textarea.value.trim();
      if (!text) return;
      btn.disabled = true;

      const model = pickModel(text);
      btn.innerHTML = `<span style="letter-spacing:1px">SELECTING MODEL...</span>`;
      await new Promise(r => setTimeout(r, 300));
      btn.innerHTML = `<span style="color:${model.color}">${model.name.toUpperCase()}</span> rewriting...`;

      const data = await rewrite(text);

      if (data.error) {
        btn.innerHTML = `<span style="color:#ef4444">${data.error}</span>`;
        btn.disabled = false;
        return;
      }

      const target = output ? (typeof output === 'string' ? document.querySelector(output) : output) : null;
      if (target) {
        target.style.display = 'block';
        target.innerHTML = `<div style="font-family:monospace;font-size:11px;letter-spacing:1px;color:#00e89c;margin-bottom:8px">V3 STRUCTURAL REWRITE</div>`
          + `<div style="font-size:11px;color:#5a6275;margin-bottom:12px">${data.model} · ${data.original_words}→${data.rewrite_words} words · ${data.compression} · ${data.latency_ms}ms</div>`
          + `<div style="font-size:15px;line-height:1.7;color:#e0e4ea">${_esc(data.rewrite)}</div>`
          + (data.issues && data.issues.length ? `<div style="font-family:monospace;font-size:11px;color:#5a6275;margin-top:12px;padding-top:8px;border-top:1px solid #252a35">${data.issues.map(i => `<div>${i}</div>`).join('')}</div>` : '');
      }

      btn.innerHTML = `<span style="color:${data.model_color || model.color}">${data.model}</span> — Rewritten ✓ <span style="font-size:11px;color:#5a6275">${data.latency_ms}ms</span>`;
    });
  }

  // ═══════════════════════════════════════
  // MOUNT: Full widget (textarea + score + rewrite)
  // ═══════════════════════════════════════
  function mount(selector) {
    const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!el) return;

    el.innerHTML = `
      <div style="max-width:640px;margin:0 auto">
        <textarea id="nti-w-input" style="width:100%;min-height:120px;background:#12151b;border:1px solid #252a35;border-radius:10px;color:#e0e4ea;padding:14px;font-size:15px;font-family:inherit;resize:vertical" placeholder="Paste anything. Score it."></textarea>
        <div style="display:flex;gap:8px;margin-top:8px">
          <button id="nti-w-score" style="flex:1;background:#00e89c;color:#000;border:none;padding:10px;border-radius:8px;cursor:pointer;font-weight:600;font-size:14px">Score it</button>
          <button id="nti-w-rewrite" style="flex:1;background:transparent;color:#00e89c;border:1px solid #00e89c;padding:10px;border-radius:8px;cursor:pointer;font-weight:600;font-size:14px;display:none">Rewrite</button>
        </div>
        <div id="nti-w-bar" style="margin-top:8px;font-family:monospace;min-height:20px"></div>
        <div id="nti-w-results" style="margin-top:12px;display:none"></div>
        <div id="nti-w-rewrite-out" style="margin-top:12px;display:none;background:#12151b;border:1px solid #00e89c;border-radius:10px;padding:16px"></div>
      </div>`;

    const inp = el.querySelector('#nti-w-input');
    const scoreBtn = el.querySelector('#nti-w-score');
    const rwBtn = el.querySelector('#nti-w-rewrite');
    const bar = el.querySelector('#nti-w-bar');
    const results = el.querySelector('#nti-w-results');
    const rwOut = el.querySelector('#nti-w-rewrite-out');

    scoreBtn.addEventListener('click', async () => {
      const text = inp.value.trim();
      if (!text) return;
      scoreBtn.disabled = true;
      bar.innerHTML = '<span style="color:#5a6275">scoring...</span>';
      const data = await score(text);
      scoreBtn.disabled = false;
      rwBtn.style.display = 'block';

      const nii = data.score?.nii_score ?? data.nii_score ?? 0;
      const color = nii >= 0.8 ? '#00e89c' : nii >= 0.5 ? '#f59e0b' : '#ef4444';
      bar.innerHTML = `<span style="color:${color};font-size:28px;font-weight:700">${(nii * 100).toFixed(0)}</span> <span style="color:#5a6275;font-size:13px">${data.latency_ms || '?'}ms</span>`;

      // Show findings
      const findings = data.findings || [];
      if (findings.length) {
        results.style.display = 'block';
        results.innerHTML = findings.map(f =>
          `<div style="padding:10px 0;border-bottom:1px solid #1a1e27"><div style="color:#e0e4ea;font-size:14px">${_esc(f.finding || f.label || '')}</div><div style="color:#00e89c;font-size:13px;margin-top:4px">${_esc(f.fix || f.recommendation || '')}</div></div>`
        ).join('');
      }
    });

    rewriteButton('#nti-w-input', '#nti-w-rewrite-out', { buttonSelector: '#nti-w-rewrite' });
  }

  // ═══════════════════════════════════════
  // MOUNT INLINE: Compact score bar only
  // ═══════════════════════════════════════
  function mountInline(selector) {
    const el = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!el) return;
    el.innerHTML = `<div id="nti-inline-bar" style="font-family:monospace;font-size:12px;padding:6px 12px;background:#12151b;border-radius:6px;display:inline-block"></div>`;
    return {
      update: async (text) => {
        const bar = el.querySelector('#nti-inline-bar');
        if (!text.trim()) { bar.innerHTML = ''; return; }
        bar.innerHTML = '<span style="color:#5a6275">···</span>';
        const data = await score(text);
        const nii = data.score?.nii_score ?? data.nii_score ?? 0;
        const color = nii >= 0.8 ? '#00e89c' : nii >= 0.5 ? '#f59e0b' : '#ef4444';
        bar.innerHTML = `<span style="color:${color};font-weight:700">${(nii * 100).toFixed(0)}</span> <span style="color:#5a6275">${data.latency_ms || ''}ms</span>`;
        return data;
      }
    };
  }

  // ═══════════════════════════════════════
  // HELPERS
  // ═══════════════════════════════════════
  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ═══════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════
  return {
    score,
    rewrite,
    pickModel,
    enforceResponse,
    scoreBar,
    rewriteButton,
    mount,
    mountInline,
    MODELS,
    version: '1.0'
  };
})();
