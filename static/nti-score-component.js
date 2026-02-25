/**
 * nti-score-component.js v2
 * Findings-first. Score last.
 * 15 metrics from V1 engine + client-side enrichment.
 */
const NTIScore = (() => {

  const TILT_DEFS = {
    T1_REASSURANCE_DRIFT:     { label:'Reassurance Drift',     short:'Reassurance used instead of evidence.', icon:'\u{1F6E1}' },
    T2_CERTAINTY_INFLATION:   { label:'Certainty Inflation',   short:'Claims certainty without proof.', icon:'\u{1F4C8}' },
    T3_SCOPE_EXPANSION:       { label:'Scope Expansion',       short:'Widens the topic without acknowledging it.', icon:'\u{1F504}' },
    T4_CAPABILITY_OVERREACH:  { label:'Capability Overreach',  short:'Promises beyond what can be delivered.', icon:'\u26A1' },
    T5_ABSOLUTE_LANGUAGE:     { label:'Absolute Language',     short:'Uses never/always/every without evidence.', icon:'\u{1F512}' },
    T6_CONSTRAINT_DEFERRAL:   { label:'Constraint Deferral',   short:'Pushes limits to an unspecified later.', icon:'\u23F3' },
    T7_CATEGORY_BLEND:        { label:'Category Blend',        short:'Mixes unrelated things to obscure meaning.', icon:'\u{1F300}' },
    T8_PRESSURE_OPTIMIZATION: { label:'Pressure Pattern',      short:'Uses urgency or social proof without substance.', icon:'\u{1F525}' },
    T9_SCOPE_EXPANSION:       { label:'Scope Expansion',       short:'Widens scope without acknowledging the shift.', icon:'\u{1F4D0}' },
    T10_AUTHORITY_IMPOSITION: { label:'Authority Imposition',  short:'Defers to unnamed authority.', icon:'\u{1F464}' }
  };

  const TILT_TRIGGERS = {
    T1_REASSURANCE_DRIFT:     ['don\'t worry','rest assured','no problem','it\'s okay','everything is fine','i assure you','trust me','under control','handled','taken care'],
    T2_CERTAINTY_INFLATION:   ['guarantee','guaranteed','will definitely','without a doubt','no question','100%','certainly','absolutely'],
    T3_SCOPE_EXPANSION:       ['also','additionally','furthermore','moreover','not only','on top of','plus','another thing','while we\'re at it'],
    T4_CAPABILITY_OVERREACH:  ['we can handle','no problem','easy','simple','of course','absolutely','definitely can'],
    T5_ABSOLUTE_LANGUAGE:     ['never','always','every','all','none','no one','everyone','everything','nothing','impossible','completely','entirely'],
    T6_CONSTRAINT_DEFERRAL:   ['later','eventually','phase 2','we\'ll figure it out','future iteration','down the road','at some point','soon'],
    T7_CATEGORY_BLEND:        ['kind of','sort of','basically','overall','in other words','at the end of the day'],
    T8_PRESSURE_OPTIMIZATION: ['act now','don\'t miss','limited time','running out','already joined','momentum','urgent','immediately'],
    T9_SCOPE_EXPANSION:       ['also','additionally','moreover','while we\'re at it','on top of that','not only'],
    T10_AUTHORITY_IMPOSITION: ['experts agree','industry standard','research shows','studies show','best practice','widely accepted','everyone knows']
  };

  const VAGUE_WORDS = ['values','opportunity','community','leadership','service','vision','future','better','great','special','incredible','amazing','wonderful','fantastic','beautiful','important','committed','dedicated','passionate','believe','proud','honored','privilege','thrilled'];
  const SPECIFIC_WORDS = ['percent','million','billion','budget','tax','fund','program','plan','policy','reduce','increase','build','hire','cut','invest','allocate','deadline','quarter','department','ordinance','resolution','vote','approve','audit','review'];
  const EMO_HIGH = ['outraged','disgusted','thrilled','devastated','ecstatic','furious','terrified','horrified','elated','desperate'];
  const EMO_MOD = ['worried','excited','frustrated','disappointed','proud','grateful','angry','scared','hopeful','concerned'];

  function enrichText(text) {
    const lower = text.toLowerCase(), words = lower.split(/\s+/), total = Math.max(words.length, 1);
    let vague = 0, specific = 0, vagueList = [], specificList = [];
    VAGUE_WORDS.forEach(w => { if (lower.includes(w)) { vague++; vagueList.push(w); }});
    SPECIFIC_WORDS.forEach(w => { if (lower.includes(w)) { specific++; specificList.push(w); }});
    let hH = 0, mH = 0;
    EMO_HIGH.forEach(w => { if (lower.includes(w)) hH++; });
    EMO_MOD.forEach(w => { if (lower.includes(w)) mH++; });
    return { vague, specific, vagueList, specificList, emo: Math.min(100, Math.round((hH*3+mH*1.5)/total*800)), wordCount: words.length };
  }

  function highlightWords(text, tiltTag) {
    const triggers = TILT_TRIGGERS[tiltTag] || [];
    if (!triggers.length) return escHtml(text);
    const lower = text.toLowerCase();
    const sorted = [...triggers].sort((a, b) => b.length - a.length);
    const marks = [];
    sorted.forEach(tr => { let i = lower.indexOf(tr); while (i !== -1) { marks.push({start:i,end:i+tr.length}); i = lower.indexOf(tr, i+1); }});
    marks.sort((a, b) => a.start - b.start);
    const merged = [];
    marks.forEach(m => { if (merged.length && m.start <= merged[merged.length-1].end) merged[merged.length-1].end = Math.max(merged[merged.length-1].end, m.end); else merged.push({...m}); });
    let out = '', pos = 0;
    merged.forEach(m => { out += escHtml(text.substring(pos, m.start)); out += `<mark class="nti-hl">${escHtml(text.substring(m.start, m.end))}</mark>`; pos = m.end; });
    return out + escHtml(text.substring(pos));
  }

  function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  function render(data, originalText, opts = {}) {
    const c = document.createElement('div');
    c.className = 'nti-results';
    const nii = data.nii || {}, score = nii.nii_score ?? 0;
    const fm = data.parent_failure_modes || {}, tilt = data.tilt_taxonomy || [];
    const matrix = data.interaction_matrix || {}, dom = matrix.dominance_detected || ['NONE'];
    const layers = data.layers || {};
    const l0 = layers.L0_reality_substrate || {}, l2 = layers.L2_interpretive_framing || {};
    const l1 = layers.L1_input_freeze || {};
    const lat = data.telemetry?.latency_ms || '-', ver = data.version || '?';
    const enrich = enrichText(originalText || '');
    const uid = 'nti_' + Math.random().toString(36).substring(2, 8);
    const constraints = l0.constraints_found || [];
    const hedges = l2.hedge_markers || [], reassurances = l2.reassurance_markers || [], blends = l2.category_blend_markers || [];
    const objective = l1.objective || '';
    const q1 = nii.q1_constraints_explicit ?? 0, q2 = nii.q2_constraints_before_capability ?? 0, q3 = nii.q3_substitutes_after_enforcement ?? 0;
    const issueCount = (q1===0?1:0)+(q2===0?1:0)+(q3===0?1:0)+hedges.length+reassurances.length+tilt.length;
    const fmCount = ['UDDS','DCE','CCA'].filter(k => { const st = fm[k]?(fm[k].udds_state||fm[k].dce_state||fm[k].cca_state):''; return st.includes('CONFIRMED')||st.includes('PROBABLE'); }).length;
    let verdict, vClass;
    if (issueCount===0 && fmCount===0) { verdict='This message is structurally clean.'; vClass='nti-v-clean'; }
    else if (fmCount>=2 || issueCount>=6) { verdict=`${issueCount} structural issues. ${fmCount} failure mode${fmCount!==1?'s':''} active.`; vClass='nti-v-critical'; }
    else { verdict=`${issueCount} issue${issueCount!==1?'s':''} found.${fmCount?' '+fmCount+' failure mode'+(fmCount>1?'s':'')+'.':['']}`.replace(',',''); vClass='nti-v-warning'; }

    c.innerHTML = `
      <div class="nti-verdict ${vClass}">${verdict}</div>
      <div class="nti-section"><div class="nti-sh">Structure</div>
        <div class="nti-checks">
          ${_ck('Constraints stated', q1)} ${_ck('Constraints before promises', q2)}
          ${_ck('Boundaries enforced', q3)} ${_ck('Objective detected', objective?1:0, !objective)}
        </div>
        ${constraints.length?`<div class="nti-found"><span class="nti-found-label">Constraints found:</span>${constraints.map(x=>`<span class="nti-chip green">${escHtml(x)}</span>`).join('')}</div>`:''}
      </div>
      <div class="nti-section"><div class="nti-sh">What we found</div>
        <div class="nti-metrics">
          ${_met(hedges.length,'Hedge phrases',hedges.length?'bad':'good',hedges.map(h=>`"${escHtml(h)}"`),true)}
          ${_met(reassurances.length,'False reassurances',reassurances.length?'bad':'good',reassurances.map(r=>`"${escHtml(r)}"`),true)}
          ${_met(enrich.vague,'Vague words',enrich.vague>2?'bad':enrich.vague?'warn':'good',enrich.vagueList,false)}
          ${_met(enrich.specific,'Specific words',enrich.specific?'good':'warn',enrich.specificList,false)}
          ${_met(enrich.emo+'%','Emotional charge',enrich.emo>40?'bad':enrich.emo>15?'warn':'good',[],false)}
          ${_met(enrich.wordCount,'Words','neutral',[],false)}
        </div>
      </div>
      <div class="nti-section"><div class="nti-sh">Failure modes</div>
        ${_fm('UDDS','Substituted reassurance for substance',fm.UDDS,'udds_state')}
        ${_fm('DCE','Deferred the hard part to later',fm.DCE,'dce_state')}
        ${_fm('CCA','Collapsed constraints into vague agreement',fm.CCA,'cca_state')}
        ${dom[0]!=='NONE'?`<div class="nti-dom">Dominant pattern: <strong>${dom.join(' \u2192 ')}</strong></div>`:''}
      </div>
      ${tilt.length?`<div class="nti-section"><div class="nti-sh">Communication habits detected</div>
        <div class="nti-tilt-list" id="${uid}_tilts">
          ${tilt.map(t=>{ const d=TILT_DEFS[t]||{label:t.replace(/_/g,' '),short:'',icon:'\u26A0'}; return `<div class="nti-tilt-row" data-tilt="${t}" data-uid="${uid}" onclick="NTIScore._tiltClick(this)"><span class="nti-tilt-icon">${d.icon}</span><div class="nti-tilt-info"><div class="nti-tilt-name">${d.label}</div><div class="nti-tilt-desc">${d.short}</div></div><span class="nti-tilt-arrow">\u203A</span></div>`; }).join('')}
        </div></div>`:''}
      <div class="nti-source-text" id="${uid}_source" style="display:none">
        <div class="nti-sh">Source text \u2014 <span id="${uid}_tiltname"></span></div>
        <div class="nti-source-body" id="${uid}_body"></div>
      </div>
      <div class="nti-score-footer">
        <div class="nti-sf-score ${score>=80?'green':score>=50?'amber':'red'}">${score}</div>
        <div class="nti-sf-meta"><span>Structural Integrity</span><span>${lat}ms \u00B7 ${enrich.wordCount} words \u00B7 v${ver}</span></div>
      </div>`;
    c.dataset.originalText = originalText || '';
    c.dataset.uid = uid;
    return c;
  }

  function _ck(label, val, neutral) {
    const cls = neutral ? 'neutral' : (val ? 'pass' : 'fail');
    const txt = neutral ? 'NONE' : (val ? 'YES' : 'NO');
    return `<div class="nti-check ${cls}"><span class="nti-dot"></span><span class="nti-ck-label">${label}</span><span class="nti-ck-val">${txt}</span></div>`;
  }

  function _met(num, label, cls, items, isAmber) {
    const chipCls = isAmber ? 'amber' : (cls==='good'?'green':'muted');
    return `<div class="nti-metric ${cls}"><div class="nti-m-num">${num}</div><div class="nti-m-label">${label}</div>${items.length?'<div class="nti-m-detail">'+items.map(i=>`<span class="nti-chip ${chipCls}">${i}</span>`).join('')+'</div>':''}</div>`;
  }

  function _fm(name, desc, obj, stateKey) {
    if (!obj) return `<div class="nti-fm clear"><div class="nti-fm-top"><span class="nti-fm-name">${name}</span><span class="nti-fm-status green">CLEAR</span></div></div>`;
    const st = obj[stateKey]||'', isActive = st.includes('CONFIRMED')||st.includes('PROBABLE');
    const label = st.includes('CONFIRMED')?'DETECTED':st.includes('PROBABLE')?'PROBABLE':'CLEAR';
    const cls = st.includes('CONFIRMED')?'red':st.includes('PROBABLE')?'amber':'green';
    return `<div class="nti-fm ${isActive?'active':'clear'}"><div class="nti-fm-top"><span class="nti-fm-name">${name}</span><span class="nti-fm-status ${cls}">${label}</span></div>${isActive?`<div class="nti-fm-desc">${desc}</div>`:''}</div>`;
  }

  function renderInline(data, originalText, containerId) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = '';
    el.appendChild(render(data, originalText));
  }

  function _tiltClick(el) {
    const tiltTag = el.dataset.tilt, uid = el.dataset.uid;
    const container = document.getElementById(uid+'_source'), body = document.getElementById(uid+'_body'), nameEl = document.getElementById(uid+'_tiltname');
    const resultsEl = el.closest('.nti-results'), originalText = resultsEl ? resultsEl.dataset.originalText : '';
    if (container.style.display !== 'none' && el.classList.contains('nti-tilt-active')) { container.style.display='none'; el.classList.remove('nti-tilt-active'); return; }
    el.closest('.nti-tilt-list').querySelectorAll('.nti-tilt-row').forEach(t=>t.classList.remove('nti-tilt-active'));
    el.classList.add('nti-tilt-active');
    nameEl.textContent = (TILT_DEFS[tiltTag]||{label:tiltTag}).label;
    body.innerHTML = highlightWords(originalText, tiltTag);
    container.style.display = 'block';
    container.scrollIntoView({behavior:'smooth',block:'nearest'});
  }

  function injectCSS() {
    if (document.getElementById('nti-score-css')) return;
    const s = document.createElement('style');
    s.id = 'nti-score-css';
    s.textContent = `
.nti-results{width:100%;max-width:640px;margin:0 auto;animation:ntiFadeIn .4s ease}
@keyframes ntiFadeIn{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:translateY(0)}}
.nti-verdict{font-family:'JetBrains Mono',monospace;font-size:14px;font-weight:600;padding:14px 18px;border-radius:8px;margin-bottom:20px;text-align:center}
.nti-v-clean{background:rgba(0,232,156,.08);border:1px solid rgba(0,232,156,.2);color:#00e89c}
.nti-v-warning{background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.2);color:#f59e0b}
.nti-v-critical{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);color:#ef4444}
.nti-section{margin-bottom:16px}
.nti-sh{font-family:'JetBrains Mono',monospace;font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:1.5px;margin-bottom:10px;font-weight:600}
.nti-checks{display:flex;flex-direction:column;gap:6px}
.nti-check{display:flex;align-items:center;gap:10px;padding:8px 12px;background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:6px;font-size:13px}
.nti-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.nti-check.pass .nti-dot{background:#00e89c}.nti-check.fail .nti-dot{background:#ef4444}.nti-check.neutral .nti-dot{background:#6b7280}
.nti-ck-label{flex:1;color:var(--text,#e8eaf0)}
.nti-ck-val{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700}
.nti-check.pass .nti-ck-val{color:#00e89c}.nti-check.fail .nti-ck-val{color:#ef4444}.nti-check.neutral .nti-ck-val{color:#6b7280}
.nti-found{margin-top:8px;font-size:12px;display:flex;flex-wrap:wrap;align-items:center;gap:4px}
.nti-found-label{font-family:'JetBrains Mono',monospace;font-size:10px;color:#6b7280;margin-right:4px}
.nti-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
@media(max-width:500px){.nti-metrics{grid-template-columns:repeat(2,1fr)}}
.nti-metric{background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:8px;padding:12px;text-align:center}
.nti-m-num{font-family:'JetBrains Mono',monospace;font-size:28px;font-weight:700;line-height:1.1}
.nti-metric.good .nti-m-num{color:#00e89c}.nti-metric.bad .nti-m-num{color:#ef4444}.nti-metric.warn .nti-m-num{color:#f59e0b}.nti-metric.neutral .nti-m-num{color:#e8eaf0}
.nti-m-label{font-family:'JetBrains Mono',monospace;font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:1px;margin-top:4px}
.nti-m-detail{margin-top:6px;display:flex;flex-wrap:wrap;gap:3px;justify-content:center}
.nti-chip{font-family:'JetBrains Mono',monospace;font-size:10px;padding:2px 7px;border-radius:3px;display:inline-block}
.nti-chip.green{background:rgba(0,232,156,.1);color:#00e89c}.nti-chip.amber{background:rgba(245,158,11,.1);color:#f59e0b}.nti-chip.red{background:rgba(239,68,68,.1);color:#ef4444}.nti-chip.muted{background:rgba(107,114,128,.1);color:#6b7280}
.nti-fm{padding:10px 12px;background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:6px;margin-bottom:6px}
.nti-fm.active{border-color:rgba(239,68,68,.3)}
.nti-fm-top{display:flex;justify-content:space-between;align-items:center}
.nti-fm-name{font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;color:var(--text,#e8eaf0)}
.nti-fm-status{font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700}
.nti-fm-status.green{color:#00e89c}.nti-fm-status.amber{color:#f59e0b}.nti-fm-status.red{color:#ef4444}
.nti-fm-desc{font-size:12px;color:#6b7280;margin-top:4px}
.nti-dom{font-family:'JetBrains Mono',monospace;font-size:12px;color:#ef4444;margin-top:8px;padding:8px 12px;background:rgba(239,68,68,.05);border-radius:6px}
.nti-tilt-list{display:flex;flex-direction:column;gap:6px}
.nti-tilt-row{display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:8px;cursor:pointer;transition:all .15s}
.nti-tilt-row:hover{border-color:rgba(245,158,11,.3);background:rgba(245,158,11,.03)}
.nti-tilt-row.nti-tilt-active{border-color:rgba(245,158,11,.4);background:rgba(245,158,11,.06)}
.nti-tilt-icon{font-size:20px;flex-shrink:0}
.nti-tilt-info{flex:1}
.nti-tilt-name{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:600;color:#f59e0b}
.nti-tilt-desc{font-size:12px;color:#6b7280;margin-top:2px}
.nti-tilt-arrow{color:#6b7280;font-size:18px}
.nti-source-text{background:var(--surface,#12151b);border:1px solid rgba(245,158,11,.2);border-radius:8px;padding:16px;margin-bottom:16px;animation:ntiFadeIn .3s ease}
.nti-source-body{font-size:14px;line-height:1.7;color:var(--text,#e8eaf0);word-break:break-word;margin-top:8px}
mark.nti-hl{background:rgba(245,158,11,.25);color:#f59e0b;padding:1px 3px;border-radius:3px;font-weight:600}
.nti-score-footer{display:flex;align-items:center;gap:14px;margin-top:20px;padding:14px 16px;background:var(--surface,#12151b);border:1px solid var(--border,#252a35);border-radius:8px}
.nti-sf-score{font-family:'JetBrains Mono',monospace;font-size:32px;font-weight:700;line-height:1}
.nti-sf-score.green{color:#00e89c}.nti-sf-score.amber{color:#f59e0b}.nti-sf-score.red{color:#ef4444}
.nti-sf-meta{display:flex;flex-direction:column;gap:2px}
.nti-sf-meta span:first-child{font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:600;color:var(--text,#e8eaf0)}
.nti-sf-meta span:last-child{font-family:'JetBrains Mono',monospace;font-size:10px;color:#6b7280}
`;
    document.head.appendChild(s);
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', injectCSS);
    else injectCSS();
  }

  return { render, renderInline, _tiltClick, highlightWords, enrichText, TILT_DEFS };
})();
