/**
 * nti-score-component.js
 * Shared scoring UI component for Artifact Zero
 * Used on: homepage, /score, /wall, /contact, /compose
 * 
 * Usage:
 *   const el = NTIScore.render(ntiResponse, originalText);
 *   container.appendChild(el);
 * 
 *   NTIScore.renderInline(ntiResponse, originalText, containerId);
 */
const NTIScore = (() => {

  // ═══════════════════════════════════════
  // TILT DEFINITIONS
  // ═══════════════════════════════════════
  const TILT_DEFS = {
    T1_VAGUE_ASSURANCE:       { label:'Vague Assurance',       desc:'Uses reassuring language without specific evidence or commitments.' },
    T2_CERTAINTY_INFLATION:   { label:'Certainty Inflation',   desc:'Claims certainty beyond what the evidence supports.' },
    T3_SCOPE_EXPANSION:       { label:'Scope Expansion',       desc:'Widens the topic without acknowledging the shift.' },
    T4_FALSE_COMPLETION:      { label:'False Completion',      desc:'Signals the issue is resolved when it isn\'t.' },
    T5_HEDGE_STACKING:        { label:'Hedge Stacking',        desc:'Layers qualifiers to avoid making a concrete statement.' },
    T6_SUPERLATIVE_LOADING:   { label:'Superlative Loading',   desc:'Uses absolute/extreme language (best, worst, greatest) without evidence.' },
    T7_FILLER_DENSITY:        { label:'Filler Density',        desc:'High ratio of filler words to substantive content.' },
    T8_PRESSURE_OPTIMIZATION: { label:'Pressure Optimization', desc:'Uses urgency or social proof to push action without substance.' },
    T9_AUTHORITY_DISPLACEMENT:{ label:'Authority Displacement', desc:'Defers to unnamed authority instead of making a direct claim.' },
    T10_TEMPORAL_DEFLECTION:  { label:'Temporal Deflection',   desc:'Pushes resolution to an unspecified future time.' }
  };

  // Tilt trigger words for highlighting
  const TILT_TRIGGERS = {
    T1_VAGUE_ASSURANCE:       ['everything is fine','rest assured','don\'t worry','i assure you','trust me','no concerns','under control','covered','handled','taken care'],
    T2_CERTAINTY_INFLATION:   ['guarantee','guarantees','guaranteed','will definitely','without a doubt','no question','100%','certainly','absolutely'],
    T3_SCOPE_EXPANSION:       ['also','additionally','furthermore','moreover','not only','on top of','plus','another thing','while we\'re at it'],
    T4_FALSE_COMPLETION:      ['addressed all','resolved','taken care of','handled','completed','wrapped up','all set','we\'ve fixed','all issues','fully addressed'],
    T5_HEDGE_STACKING:        ['basically','generally speaking','kind of','sort of','probably','might','perhaps','essentially','in a way','more or less','roughly'],
    T6_SUPERLATIVE_LOADING:   ['best','worst','greatest','most incredible','most important','number one','top','premier','world class','unmatched','unparalleled'],
    T7_FILLER_DENSITY:        ['actually','really','very','just','quite','rather','somewhat','fairly','pretty much','to be honest','at the end of the day'],
    T8_PRESSURE_OPTIMIZATION: ['act now','don\'t miss','limited time','running out','already joined','momentum','urgent','immediately','asap','time is running'],
    T9_AUTHORITY_DISPLACEMENT:['experts say','studies show','research indicates','it is known','people say','they say','everyone knows','widely accepted'],
    T10_TEMPORAL_DEFLECTION:  ['eventually','soon','in the future','down the road','we\'ll figure it out','at some point','later','when the time comes','phase 2']
  };

  // ═══════════════════════════════════════
  // CLIENT-SIDE HCS ENRICHMENT
  // ═══════════════════════════════════════
  const VAGUE_WORDS = ['values','opportunity','community','leadership','service','vision','future','better',
    'great','special','incredible','amazing','wonderful','fantastic','beautiful','important',
    'committed','dedicated','passionate','believe','proud','honored','privilege','thrilled'];
  const SPECIFIC_WORDS = ['percent','million','billion','budget','tax','fund','program','plan','policy',
    'reduce','increase','build','hire','cut','invest','allocate','deadline','quarter',
    'department','ordinance','resolution','vote','approve','audit','review'];
  const EMOTIONAL_HIGH = ['outraged','disgusted','thrilled','devastated','ecstatic','furious','terrified','horrified','elated','desperate'];
  const EMOTIONAL_MOD = ['worried','excited','frustrated','disappointed','proud','grateful','angry','scared','hopeful','concerned'];

  function enrichText(text) {
    const lower = text.toLowerCase();
    const words = lower.split(/\s+/);
    const total = Math.max(words.length, 1);
    let vague = 0, specific = 0;
    VAGUE_WORDS.forEach(w => { if (lower.includes(w)) vague++; });
    SPECIFIC_WORDS.forEach(w => { if (lower.includes(w)) specific++; });
    let highHits = 0, modHits = 0;
    EMOTIONAL_HIGH.forEach(w => { if (lower.includes(w)) highHits++; });
    EMOTIONAL_MOD.forEach(w => { if (lower.includes(w)) modHits++; });
    const emo = Math.min(100, Math.round((highHits * 3 + modHits * 1.5) / total * 800));
    return { vague, specific, emo, wordCount: words.length };
  }

  // ═══════════════════════════════════════
  // HIGHLIGHT ENGINE
  // ═══════════════════════════════════════
  function highlightWords(text, tiltTag) {
    const triggers = TILT_TRIGGERS[tiltTag] || [];
    if (!triggers.length) return escHtml(text);
    let result = text;
    const lower = result.toLowerCase();
    // Sort triggers longest first to avoid partial matches
    const sorted = [...triggers].sort((a, b) => b.length - a.length);
    const marks = [];
    sorted.forEach(trigger => {
      let idx = lower.indexOf(trigger);
      while (idx !== -1) {
        marks.push({ start: idx, end: idx + trigger.length });
        idx = lower.indexOf(trigger, idx + 1);
      }
    });
    // Merge overlapping
    marks.sort((a, b) => a.start - b.start);
    const merged = [];
    marks.forEach(m => {
      if (merged.length && m.start <= merged[merged.length - 1].end) {
        merged[merged.length - 1].end = Math.max(merged[merged.length - 1].end, m.end);
      } else {
        merged.push({ ...m });
      }
    });
    // Build highlighted string
    let out = '';
    let pos = 0;
    merged.forEach(m => {
      out += escHtml(result.substring(pos, m.start));
      out += `<mark class="nti-hl">${escHtml(result.substring(m.start, m.end))}</mark>`;
      pos = m.end;
    });
    out += escHtml(result.substring(pos));
    return out;
  }

  // ═══════════════════════════════════════
  // UTILITIES
  // ═══════════════════════════════════════
  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  function scoreClass(n) { return n >= 0.8 ? 'nti-high' : n >= 0.5 ? 'nti-mid' : 'nti-low'; }
  function scoreLabel(n) { return n >= 0.8 ? 'HIGH INTEGRITY' : n >= 0.5 ? 'MODERATE' : n >= 0.2 ? 'LOW INTEGRITY' : 'CRITICAL'; }
  function fmState(s) {
    if (!s) return { cls: 'nti-high', label: 'CLEAR' };
    if (s.includes('CONFIRMED')) return { cls: 'nti-low', label: 'DETECTED' };
    if (s.includes('PROBABLE')) return { cls: 'nti-mid', label: 'PROBABLE' };
    return { cls: 'nti-high', label: 'CLEAR' };
  }

  // ═══════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════
  function render(data, originalText, opts = {}) {
    const container = document.createElement('div');
    container.className = 'nti-results';

    const nii = data.nii || {};
    const score = nii.nii_score ?? 0;
    const fm = data.parent_failure_modes || {};
    const tilt = data.tilt_taxonomy || [];
    const matrix = data.interaction_matrix || {};
    const dom = matrix.dominance_detected || ['NONE'];
    const lat = data.telemetry?.latency_ms || '—';
    const rid = data.telemetry?.request_id?.substring(0, 8) || '';
    const ver = data.version || '?';

    // Client-side enrichment
    const enrich = enrichText(originalText || '');

    // Unique ID for this instance
    const uid = 'nti_' + Math.random().toString(36).substring(2, 8);

    container.innerHTML = `
      <div class="nti-score-ring">
        <div class="nti-num ${scoreClass(score)}">${score.toFixed(2)}</div>
        <div class="nti-label">${scoreLabel(score)}</div>
      </div>
      <div class="nti-badges">
        <span class="nti-badge nti-badge-score ${scoreClass(score)}">NII ${(score * 100).toFixed(0)}</span>
        <span class="nti-badge nti-badge-vague">Vague:${enrich.vague} Spec:${enrich.specific}</span>
        <span class="nti-badge nti-badge-emo">Emo ${enrich.emo}%</span>
        ${tilt.length === 0 ? '<span class="nti-badge nti-badge-clean">Clean</span>' : ''}
      </div>
      <div class="nti-cards">
        <div class="nti-card">
          <div class="nti-card-h">FAILURE MODES</div>
          ${['UDDS','DCE','CCA'].map(k => {
            const st = fm[k] ? (fm[k].udds_state || fm[k].dce_state || fm[k].cca_state) : 'FALSE';
            const f = fmState(st);
            return `<div class="nti-fm-row"><span>${k}</span><span class="nti-v ${f.cls}">${f.label}</span></div>`;
          }).join('')}
        </div>
        <div class="nti-card">
          <div class="nti-card-h">DOMINANCE</div>
          <div class="nti-v ${dom[0] === 'NONE' ? 'nti-high' : 'nti-low'}">${dom.join(' → ')}</div>
        </div>
        <div class="nti-card">
          <div class="nti-card-h">TILT PATTERNS</div>
          <div class="nti-tilt-tags" id="${uid}_tilts">
            ${tilt.length ? tilt.map(t => {
              const def = TILT_DEFS[t] || { label: t, desc: '' };
              return `<span class="nti-tilt-tag" data-tilt="${t}" data-uid="${uid}" onclick="NTIScore._tiltClick(this)" title="${def.label}: ${def.desc}">${t.replace(/_/g,' ')}</span>`;
            }).join('') : '<span class="nti-v nti-high">None detected</span>'}
          </div>
        </div>
        <div class="nti-card">
          <div class="nti-card-h">RESPONSE</div>
          <div class="nti-v">${lat}ms</div>
          <div style="font-size:10px;color:var(--muted,#6b7280);margin-top:4px">${enrich.wordCount} words</div>
        </div>
      </div>
      <div class="nti-source-text" id="${uid}_source" style="display:none">
        <div class="nti-card-h">SOURCE TEXT — <span id="${uid}_tiltname"></span></div>
        <div class="nti-card-h" style="font-weight:400;color:var(--muted,#6b7280);margin-bottom:8px" id="${uid}_tiltdesc"></div>
        <div class="nti-source-body" id="${uid}_body"></div>
      </div>
      <div class="nti-meta">${lat}ms · ${rid} · v${ver}</div>
    `;

    // Store original text for highlighting
    container.dataset.originalText = originalText || '';
    container.dataset.uid = uid;

    return container;
  }

  function renderInline(data, originalText, containerId, opts = {}) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = '';
    el.appendChild(render(data, originalText, opts));
  }

  // Tilt tag click handler
  function _tiltClick(el) {
    const tiltTag = el.dataset.tilt;
    const uid = el.dataset.uid;
    const container = document.getElementById(uid + '_source');
    const body = document.getElementById(uid + '_body');
    const nameEl = document.getElementById(uid + '_tiltname');
    const descEl = document.getElementById(uid + '_tiltdesc');

    // Find the results container to get original text
    const resultsEl = el.closest('.nti-results');
    const originalText = resultsEl ? resultsEl.dataset.originalText : '';

    // Toggle off if clicking same tag
    if (container.style.display !== 'none' && el.classList.contains('nti-tilt-active')) {
      container.style.display = 'none';
      el.classList.remove('nti-tilt-active');
      return;
    }

    // Remove active from siblings
    const parent = el.parentElement;
    parent.querySelectorAll('.nti-tilt-tag').forEach(t => t.classList.remove('nti-tilt-active'));
    el.classList.add('nti-tilt-active');

    const def = TILT_DEFS[tiltTag] || { label: tiltTag, desc: '' };
    nameEl.textContent = def.label;
    descEl.textContent = def.desc;
    body.innerHTML = highlightWords(originalText, tiltTag);
    container.style.display = 'block';
    container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  // ═══════════════════════════════════════
  // CSS (injected once)
  // ═══════════════════════════════════════
  function injectCSS() {
    if (document.getElementById('nti-score-css')) return;
    const style = document.createElement('style');
    style.id = 'nti-score-css';
    style.textContent = `
      .nti-results{width:100%;max-width:640px;margin:0 auto;animation:ntiFadeIn .4s ease}
      @keyframes ntiFadeIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
      .nti-score-ring{text-align:center;margin-bottom:20px}
      .nti-num{font-family:'JetBrains Mono',monospace;font-size:72px;font-weight:700;line-height:1}
      .nti-label{font-family:'JetBrains Mono',monospace;font-size:12px;text-transform:uppercase;letter-spacing:2px;color:var(--muted,#6b7280);margin-top:4px}
      .nti-high{color:var(--accent,#00e89c)}.nti-mid{color:var(--amber,#f59e0b)}.nti-low{color:var(--red,#ef4444)}
      .nti-badges{display:flex;flex-wrap:wrap;justify-content:center;gap:6px;margin-bottom:16px}
      .nti-badge{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:600;padding:3px 10px;border-radius:4px;border:1px solid}
      .nti-badge-score{border-color:currentColor;background:rgba(0,232,156,.08)}
      .nti-badge-score.nti-mid{background:rgba(245,158,11,.08)}.nti-badge-score.nti-low{background:rgba(239,68,68,.08)}
      .nti-badge-vague{background:rgba(34,211,238,.08);color:#22d3ee;border-color:rgba(34,211,238,.2)}
      .nti-badge-emo{background:rgba(236,72,153,.08);color:#ec4899;border-color:rgba(236,72,153,.2)}
      .nti-badge-clean{background:rgba(0,232,156,.08);color:var(--accent,#00e89c);border-color:rgba(0,232,156,.2)}
      .nti-cards{display:grid;grid-template-columns:1fr 1fr;gap:10px}
      @media(max-width:500px){.nti-cards{grid-template-columns:1fr}}
      .nti-card{background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:10px;padding:14px}
      .nti-card-h{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--muted,#6b7280);text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;font-weight:600}
      .nti-v{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:600}
      .nti-fm-row{display:flex;justify-content:space-between;padding:3px 0;font-size:12px}
      .nti-tilt-tags{display:flex;flex-wrap:wrap;gap:4px}
      .nti-tilt-tag{font-family:'JetBrains Mono',monospace;font-size:10px;padding:3px 8px;border-radius:4px;background:rgba(245,158,11,.1);color:var(--amber,#f59e0b);border:1px solid rgba(245,158,11,.2);cursor:pointer;transition:all .15s;user-select:none}
      .nti-tilt-tag:hover{background:rgba(245,158,11,.2);transform:translateY(-1px)}
      .nti-tilt-tag.nti-tilt-active{background:rgba(245,158,11,.25);box-shadow:0 0 0 2px rgba(245,158,11,.3)}
      .nti-source-text{background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:10px;padding:16px;margin-top:10px;animation:ntiFadeIn .3s ease}
      .nti-source-body{font-size:14px;line-height:1.7;color:var(--text,#e8eaf0);word-break:break-word}
      mark.nti-hl{background:rgba(245,158,11,.25);color:var(--amber,#f59e0b);padding:1px 3px;border-radius:3px;font-weight:600;text-decoration:none}
      .nti-meta{text-align:center;margin-top:16px;font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted,#6b7280)}
    `;
    document.head.appendChild(style);
  }

  // Auto-inject CSS when loaded
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', injectCSS);
    } else {
      injectCSS();
    }
  }

  return { render, renderInline, _tiltClick, highlightWords, enrichText, TILT_DEFS };
})();
