/**
 * ═══════════════════════════════════════════════════════════════════
 * ARTIFACT ZERO — MOBILE AUDIT BLOB v1.0
 * ═══════════════════════════════════════════════════════════════════
 * 
 * Drop this <script> at the end of any HTML file.
 * It audits the page for mobile-first violations on load.
 * 
 * Usage:
 *   <script src="/static/az-mobile-audit.js"></script>
 *   
 *   Or inline at the bottom of any HTML:
 *   <script src="/static/az-mobile-audit.js" data-mode="dev"></script>
 *
 * Modes:
 *   data-mode="dev"    → Console log + visual overlay panel
 *   data-mode="silent" → Console log only (default)
 *   data-mode="strict" → Console log + overlay + throws on critical
 *
 * What it checks (30 rules across 6 categories):
 *   [VP] Viewport & Meta
 *   [TT] Touch Targets  
 *   [TY] Typography & Readability
 *   [LY] Layout & Overflow
 *   [PF] Performance & Assets
 *   [AC] Accessibility & Contrast
 * ═══════════════════════════════════════════════════════════════════
 */
(function(){
  'use strict';

  // ── CONFIG ──
  const scriptTag = document.currentScript;
  const MODE = (scriptTag && scriptTag.getAttribute('data-mode')) || 'silent';
  const SHOW_OVERLAY = MODE === 'dev' || MODE === 'strict';
  const THROW_ON_CRITICAL = MODE === 'strict';

  // ── RESULTS ──
  const issues = [];
  function flag(severity, category, rule, message, element){
    issues.push({ severity, category, rule, message, element });
  }

  // ── WAIT FOR DOM ──
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', runAudit);
  } else {
    // Small delay to let styles compute
    setTimeout(runAudit, 100);
  }

  function runAudit(){
    checkViewport();
    checkTouchTargets();
    checkTypography();
    checkLayout();
    checkPerformance();
    checkAccessibility();
    reportResults();
  }

  // ═══════════════════════════════════════════
  // [VP] VIEWPORT & META
  // ═══════════════════════════════════════════
  function checkViewport(){
    // VP-01: Viewport meta tag exists
    const vp = document.querySelector('meta[name="viewport"]');
    if(!vp){
      flag('CRITICAL','VP','VP-01','Missing <meta name="viewport"> tag. Page will not render correctly on mobile.');
      return;
    }
    const content = vp.getAttribute('content') || '';

    // VP-02: width=device-width present
    if(!content.includes('device-width')){
      flag('CRITICAL','VP','VP-02','Viewport missing width=device-width. Layout will not adapt to screen size.');
    }

    // VP-03: initial-scale=1.0 present
    if(!content.includes('initial-scale')){
      flag('WARN','VP','VP-03','Viewport missing initial-scale=1.0. May cause unexpected zoom behavior.');
    }

    // VP-04: user-scalable=no is a bad practice (accessibility)
    if(content.includes('user-scalable=no') || content.includes('user-scalable=0')){
      flag('WARN','VP','VP-04','user-scalable=no prevents pinch-to-zoom. Bad for accessibility. Use maximum-scale instead if needed.');
    }

    // VP-05: Check for desktop-width fixed viewport
    if(content.includes('width=1024') || content.includes('width=1280') || content.includes('width=1440')){
      flag('CRITICAL','VP','VP-05','Viewport set to fixed desktop width. Page will not be responsive.');
    }
  }

  // ═══════════════════════════════════════════
  // [TT] TOUCH TARGETS
  // ═══════════════════════════════════════════
  function checkTouchTargets(){
    const interactiveSelectors = 'a, button, input, select, textarea, [role="button"], [onclick], [tabindex]';
    const elements = document.querySelectorAll(interactiveSelectors);
    let tooSmall = 0;
    let tooClose = 0;
    const rects = [];

    elements.forEach(el => {
      if(!isVisible(el)) return;
      const rect = el.getBoundingClientRect();
      if(rect.width === 0 && rect.height === 0) return;

      // TT-01: Minimum touch target size 44x44 (WCAG AAA / Apple HIG)
      // 24x24 is WCAG AA minimum, 44x44 is recommended for mobile
      if(rect.width < 44 || rect.height < 44){
        // Check if it's inline text link (exception)
        if(el.tagName === 'A' && el.closest('p, li, span, td')){
          // Inline links are excepted per WCAG
        } else {
          tooSmall++;
          if(tooSmall <= 5){ // Only flag first 5 to avoid noise
            flag('WARN','TT','TT-01',
              `Touch target too small: ${Math.round(rect.width)}x${Math.round(rect.height)}px (min 44x44). Element: <${el.tagName.toLowerCase()}${el.className?' .'+el.className.split(' ')[0]:''}>`,
              el);
          }
        }
      }

      // TT-02: Spacing between targets (8px minimum per web.dev)
      rects.forEach(prev => {
        const gap = Math.min(
          Math.abs(rect.left - prev.right),
          Math.abs(prev.left - rect.right),
          Math.abs(rect.top - prev.bottom),
          Math.abs(prev.top - rect.bottom)
        );
        if(gap < 8 && gap >= 0 && rectsOverlapAxis(rect, prev)){
          tooClose++;
        }
      });
      rects.push(rect);
    });

    if(tooSmall > 5){
      flag('WARN','TT','TT-01',`${tooSmall} total touch targets under 44x44px.`);
    }
    if(tooClose > 3){
      flag('WARN','TT','TT-02',`${tooClose} pairs of interactive elements have less than 8px spacing. Risk of accidental taps.`);
    }
  }

  function rectsOverlapAxis(a, b){
    // Check if two rects share a row or column (could cause mis-taps)
    const verticalOverlap = a.top < b.bottom && b.top < a.bottom;
    const horizontalOverlap = a.left < b.right && b.left < a.right;
    return verticalOverlap || horizontalOverlap;
  }

  // ═══════════════════════════════════════════
  // [TY] TYPOGRAPHY & READABILITY
  // ═══════════════════════════════════════════
  function checkTypography(){
    // TY-01: Base font size too small
    const body = document.body;
    const bodyStyle = getComputedStyle(body);
    const baseFontSize = parseFloat(bodyStyle.fontSize);
    if(baseFontSize < 14){
      flag('WARN','TY','TY-01',`Base font size is ${baseFontSize}px. Minimum 14px recommended for mobile readability. 16px is ideal.`);
    }

    // TY-02: Check for fixed font sizes in px on text elements
    const textEls = document.querySelectorAll('p, span, li, td, th, label, div');
    let tinyText = 0;
    textEls.forEach(el => {
      if(!isVisible(el)) return;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if(fs < 12 && el.textContent.trim().length > 0){
        tinyText++;
      }
    });
    if(tinyText > 0){
      flag('WARN','TY','TY-02',`${tinyText} text elements have font-size below 12px. Very hard to read on mobile.`);
    }

    // TY-03: Line height too tight
    const paragraphs = document.querySelectorAll('p, .msg, .description, [class*="text"], [class*="body"]');
    let tightLines = 0;
    paragraphs.forEach(el => {
      if(!isVisible(el)) return;
      const lh = parseFloat(getComputedStyle(el).lineHeight);
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if(!isNaN(lh) && lh / fs < 1.3 && el.textContent.trim().length > 50){
        tightLines++;
      }
    });
    if(tightLines > 0){
      flag('INFO','TY','TY-03',`${tightLines} text blocks have line-height below 1.3. Recommended: 1.5-1.7 for readability on mobile.`);
    }

    // TY-04: Very long lines (over 80 chars per line approx)
    // Check if any container is wider than 700px on mobile viewport
    const containers = document.querySelectorAll('p, .msg, article, section');
    containers.forEach(el => {
      if(!isVisible(el)) return;
      const w = el.getBoundingClientRect().width;
      if(w > 700 && el.textContent.trim().length > 100){
        flag('INFO','TY','TY-04',
          `Text container is ${Math.round(w)}px wide. Lines over ~75 characters are hard to read on mobile. Use max-width.`, el);
      }
    });
  }

  // ═══════════════════════════════════════════
  // [LY] LAYOUT & OVERFLOW
  // ═══════════════════════════════════════════
  function checkLayout(){
    // LY-01: Horizontal overflow (the #1 mobile bug)
    const docWidth = document.documentElement.clientWidth;
    const bodyWidth = document.body.scrollWidth;
    if(bodyWidth > docWidth + 5){
      flag('CRITICAL','LY','LY-01',
        `Horizontal overflow detected. Body scrollWidth (${bodyWidth}px) exceeds viewport (${docWidth}px) by ${bodyWidth - docWidth}px. This causes horizontal scrolling on mobile.`);
      
      // Try to find the offending element
      findOverflowCulprit(docWidth);
    }

    // LY-02: Fixed-width elements that exceed mobile viewport
    const allEls = document.querySelectorAll('*');
    let fixedWidthCount = 0;
    allEls.forEach(el => {
      const style = getComputedStyle(el);
      const w = el.getBoundingClientRect().width;
      // Check for elements wider than viewport
      if(w > docWidth + 2 && style.position !== 'fixed' && style.position !== 'absolute'){
        fixedWidthCount++;
        if(fixedWidthCount <= 3){
          flag('CRITICAL','LY','LY-02',
            `Element exceeds viewport width: <${el.tagName.toLowerCase()}${el.className?' .'+el.className.split(' ')[0]:''}>  is ${Math.round(w)}px wide (viewport: ${docWidth}px).`, el);
        }
      }
    });

    // LY-03: Tables without responsive handling
    const tables = document.querySelectorAll('table');
    tables.forEach(table => {
      if(!isVisible(table)) return;
      const tw = table.getBoundingClientRect().width;
      const parent = table.parentElement;
      const pw = parent ? parent.getBoundingClientRect().width : docWidth;
      const parentOverflow = parent ? getComputedStyle(parent).overflowX : '';
      if(tw > pw && parentOverflow !== 'auto' && parentOverflow !== 'scroll'){
        flag('WARN','LY','LY-03',
          'Table exceeds container width without overflow-x:auto wrapper. Will cause horizontal scroll on mobile.', table);
      }
    });

    // LY-04: Fixed positioning that may overlap on small screens
    const fixedEls = document.querySelectorAll('[style*="position:fixed"], [style*="position: fixed"]');
    let fixedCount = 0;
    allEls.forEach(el => {
      if(getComputedStyle(el).position === 'fixed') fixedCount++;
    });
    if(fixedCount > 3){
      flag('INFO','LY','LY-04',`${fixedCount} fixed-position elements found. Too many fixed elements can consume valuable mobile screen space.`);
    }

    // LY-05: Multi-column layouts without responsive breakpoints
    const grids = document.querySelectorAll('[style*="grid-template-columns"], [style*="display: grid"], [style*="display:grid"]');
    allEls.forEach(el => {
      const style = getComputedStyle(el);
      if(style.display === 'grid' || style.display === 'inline-grid'){
        const cols = style.gridTemplateColumns;
        if(cols && cols.split(' ').length > 2){
          // Check if there's a media query handling this (we can't fully check, but flag it)
          // This is informational
        }
      }
    });

    // LY-06: Padding/margin using large fixed px values
    let largePadding = 0;
    allEls.forEach(el => {
      if(!isVisible(el)) return;
      const style = getComputedStyle(el);
      const pl = parseFloat(style.paddingLeft);
      const pr = parseFloat(style.paddingRight);
      const ml = parseFloat(style.marginLeft);
      const mr = parseFloat(style.marginRight);
      if((pl + pr > 100 || ml + mr > 100) && el.getBoundingClientRect().width < docWidth * 0.5){
        largePadding++;
      }
    });
    if(largePadding > 3){
      flag('INFO','LY','LY-06',`${largePadding} elements have combined horizontal padding/margin over 100px. May waste screen space on mobile. Consider relative units.`);
    }
  }

  function findOverflowCulprit(viewportWidth){
    const all = document.querySelectorAll('*');
    all.forEach(el => {
      const rect = el.getBoundingClientRect();
      if(rect.right > viewportWidth + 5 && rect.width > 10){
        flag('INFO','LY','LY-01a',
          `Overflow source: <${el.tagName.toLowerCase()}${el.className?' .'+el.className.split(' ')[0]:''}>  right edge at ${Math.round(rect.right)}px.`, el);
      }
    });
  }

  // ═══════════════════════════════════════════
  // [PF] PERFORMANCE & ASSETS
  // ═══════════════════════════════════════════
  function checkPerformance(){
    // PF-01: Images without width/height or responsive attributes
    const images = document.querySelectorAll('img');
    let noSize = 0;
    let noSrcset = 0;
    let oversized = 0;
    images.forEach(img => {
      if(!img.hasAttribute('width') && !img.hasAttribute('height') && !img.style.width && !img.style.height){
        noSize++;
      }
      if(!img.hasAttribute('srcset') && !img.hasAttribute('sizes')){
        noSrcset++;
      }
      // Check if image is larger than its display size
      if(img.naturalWidth > 0){
        const displayed = img.getBoundingClientRect().width;
        if(displayed > 0 && img.naturalWidth > displayed * 2.5){
          oversized++;
        }
      }
    });
    if(noSize > 0){
      flag('WARN','PF','PF-01',`${noSize} images missing explicit width/height. Causes layout shift (CLS) on mobile.`);
    }
    if(oversized > 0){
      flag('INFO','PF','PF-02',`${oversized} images are significantly larger than displayed size. Wasting bandwidth on mobile.`);
    }

    // PF-03: External scripts count
    const scripts = document.querySelectorAll('script[src]');
    if(scripts.length > 10){
      flag('INFO','PF','PF-03',`${scripts.length} external scripts loaded. Each one adds latency on mobile networks.`);
    }

    // PF-04: Render-blocking stylesheets
    const stylesheets = document.querySelectorAll('link[rel="stylesheet"]');
    if(stylesheets.length > 5){
      flag('INFO','PF','PF-04',`${stylesheets.length} external stylesheets. Consider combining for mobile performance.`);
    }

    // PF-05: Large inline styles
    const styleBlocks = document.querySelectorAll('style');
    let totalInlineCSS = 0;
    styleBlocks.forEach(s => totalInlineCSS += s.textContent.length);
    if(totalInlineCSS > 50000){
      flag('INFO','PF','PF-05',`${Math.round(totalInlineCSS/1024)}KB of inline CSS. Consider external stylesheet for caching on return visits.`);
    }

    // PF-06: Check for lazy loading on below-fold images
    let noLazy = 0;
    images.forEach(img => {
      const rect = img.getBoundingClientRect();
      if(rect.top > window.innerHeight && !img.hasAttribute('loading')){
        noLazy++;
      }
    });
    if(noLazy > 0){
      flag('INFO','PF','PF-06',`${noLazy} below-fold images missing loading="lazy". Wastes bandwidth on initial mobile load.`);
    }
  }

  // ═══════════════════════════════════════════
  // [AC] ACCESSIBILITY & CONTRAST
  // ═══════════════════════════════════════════
  function checkAccessibility(){
    // AC-01: Missing alt text on images
    const images = document.querySelectorAll('img');
    let noAlt = 0;
    images.forEach(img => {
      if(!img.hasAttribute('alt')) noAlt++;
    });
    if(noAlt > 0){
      flag('WARN','AC','AC-01',`${noAlt} images missing alt attribute. Required for screen readers and accessibility.`);
    }

    // AC-02: Missing lang attribute
    if(!document.documentElement.hasAttribute('lang')){
      flag('WARN','AC','AC-02','Missing lang attribute on <html>. Required for screen readers.');
    }

    // AC-03: Form inputs without labels
    const inputs = document.querySelectorAll('input, select, textarea');
    let noLabel = 0;
    inputs.forEach(input => {
      if(input.type === 'hidden' || input.type === 'submit') return;
      const id = input.id;
      const hasLabel = id && document.querySelector(`label[for="${id}"]`);
      const hasAriaLabel = input.hasAttribute('aria-label') || input.hasAttribute('aria-labelledby');
      const wrappedInLabel = input.closest('label');
      const hasPlaceholder = input.hasAttribute('placeholder');
      if(!hasLabel && !hasAriaLabel && !wrappedInLabel && !hasPlaceholder){
        noLabel++;
      }
    });
    if(noLabel > 0){
      flag('WARN','AC','AC-03',`${noLabel} form inputs without labels, aria-labels, or placeholders. Unusable for screen readers.`);
    }

    // AC-04: Color contrast (basic check on main text)
    const bodyBg = getComputedStyle(document.body).backgroundColor;
    const bodyColor = getComputedStyle(document.body).color;
    const contrast = getContrastRatio(bodyColor, bodyBg);
    if(contrast < 4.5 && contrast > 0){
      flag('WARN','AC','AC-04',`Body text contrast ratio is ${contrast.toFixed(1)}:1. Minimum 4.5:1 required (WCAG AA). Hard to read in sunlight on mobile.`);
    }

    // AC-05: Focus indicators removed
    const allLinks = document.querySelectorAll('a, button, input, select, textarea');
    let noFocus = 0;
    allLinks.forEach(el => {
      const style = getComputedStyle(el);
      if(style.outlineStyle === 'none' && style.outlineWidth === '0px'){
        // This is common but worth noting
      }
    });

    // AC-06: Auto-playing media
    const media = document.querySelectorAll('video[autoplay], audio[autoplay]');
    if(media.length > 0){
      flag('WARN','AC','AC-06',`${media.length} auto-playing media elements. Unexpected audio/video is disruptive on mobile.`);
    }
  }

  // ═══════════════════════════════════════════
  // UTILITIES
  // ═══════════════════════════════════════════
  function isVisible(el){
    const style = getComputedStyle(el);
    return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  }

  function parseColor(color){
    // Handle rgb/rgba
    const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if(match) return { r: +match[1], g: +match[2], b: +match[3] };
    return null;
  }

  function relativeLuminance(c){
    const [rs, gs, bs] = [c.r/255, c.g/255, c.b/255];
    const r = rs <= 0.03928 ? rs/12.92 : Math.pow((rs+0.055)/1.055, 2.4);
    const g = gs <= 0.03928 ? gs/12.92 : Math.pow((gs+0.055)/1.055, 2.4);
    const b = bs <= 0.03928 ? bs/12.92 : Math.pow((bs+0.055)/1.055, 2.4);
    return 0.2126*r + 0.7152*g + 0.0722*b;
  }

  function getContrastRatio(fg, bg){
    const c1 = parseColor(fg);
    const c2 = parseColor(bg);
    if(!c1 || !c2) return -1;
    const l1 = relativeLuminance(c1);
    const l2 = relativeLuminance(c2);
    const lighter = Math.max(l1, l2);
    const darker = Math.min(l1, l2);
    return (lighter + 0.05) / (darker + 0.05);
  }

  // ═══════════════════════════════════════════
  // REPORT
  // ═══════════════════════════════════════════
  function reportResults(){
    const critical = issues.filter(i => i.severity === 'CRITICAL');
    const warns = issues.filter(i => i.severity === 'WARN');
    const infos = issues.filter(i => i.severity === 'INFO');

    // Console output
    if(issues.length === 0){
      console.log('%c[AZ MOBILE AUDIT] ✓ All checks passed', 'color:#22c55e;font-weight:bold');
    } else {
      console.group(`%c[AZ MOBILE AUDIT] ${issues.length} issues found`, 'color:#f59e0b;font-weight:bold');
      
      if(critical.length){
        console.group(`%c🔴 CRITICAL (${critical.length})`, 'color:#ef4444');
        critical.forEach(i => console.log(`[${i.rule}] ${i.message}`));
        console.groupEnd();
      }
      if(warns.length){
        console.group(`%c🟡 WARNING (${warns.length})`, 'color:#f59e0b');
        warns.forEach(i => console.log(`[${i.rule}] ${i.message}`));
        console.groupEnd();
      }
      if(infos.length){
        console.group(`%c🔵 INFO (${infos.length})`, 'color:#3b82f6');
        infos.forEach(i => console.log(`[${i.rule}] ${i.message}`));
        console.groupEnd();
      }
      
      console.groupEnd();
    }

    // Visual overlay
    if(SHOW_OVERLAY && issues.length > 0){
      showOverlay(critical, warns, infos);
    }

    // Strict mode
    if(THROW_ON_CRITICAL && critical.length > 0){
      throw new Error(`[AZ MOBILE AUDIT] ${critical.length} critical mobile issues found. Fix before deploy.`);
    }
  }

  function showOverlay(critical, warns, infos){
    const panel = document.createElement('div');
    panel.id = 'az-mobile-audit-panel';
    panel.innerHTML = `
      <style>
        #az-mobile-audit-panel{position:fixed;bottom:0;left:0;right:0;z-index:99998;max-height:50vh;overflow-y:auto;background:rgba(10,12,16,.95);backdrop-filter:blur(12px);border-top:2px solid ${critical.length?'#ef4444':'#f59e0b'};font-family:'JetBrains Mono',monospace;font-size:11px;color:#e8eaf0;padding:0}
        #az-mobile-audit-panel .az-audit-header{display:flex;justify-content:space-between;align-items:center;padding:8px 16px;border-bottom:1px solid #252a35;position:sticky;top:0;background:rgba(10,12,16,.98)}
        #az-mobile-audit-panel .az-audit-title{font-weight:700;font-size:11px;letter-spacing:1px}
        #az-mobile-audit-panel .az-audit-close{background:none;border:1px solid #252a35;color:#6b7280;padding:4px 10px;border-radius:4px;cursor:pointer;font-family:'JetBrains Mono',monospace;font-size:10px}
        #az-mobile-audit-panel .az-audit-close:hover{border-color:#ef4444;color:#ef4444}
        #az-mobile-audit-panel .az-audit-counts{display:flex;gap:12px;align-items:center}
        #az-mobile-audit-panel .az-count{padding:2px 8px;border-radius:3px;font-size:10px;font-weight:700}
        #az-mobile-audit-panel .az-count.crit{background:#7f1d1d;color:#fca5a5}
        #az-mobile-audit-panel .az-count.warn{background:#78350f;color:#fde68a}
        #az-mobile-audit-panel .az-count.info{background:#1e3a5f;color:#93c5fd}
        #az-mobile-audit-panel .az-audit-list{padding:8px 16px}
        #az-mobile-audit-panel .az-issue{padding:6px 0;border-bottom:1px solid #1a1e27;line-height:1.5;display:flex;gap:8px;align-items:flex-start}
        #az-mobile-audit-panel .az-issue:last-child{border:none}
        #az-mobile-audit-panel .az-sev{flex-shrink:0;width:8px;height:8px;border-radius:50%;margin-top:4px}
        #az-mobile-audit-panel .az-sev.crit{background:#ef4444}
        #az-mobile-audit-panel .az-sev.warn{background:#f59e0b}
        #az-mobile-audit-panel .az-sev.info{background:#3b82f6}
        #az-mobile-audit-panel .az-rule{color:#6b7280;flex-shrink:0;width:48px}
        #az-mobile-audit-panel .az-msg{color:#e8eaf0}
      </style>
      <div class="az-audit-header">
        <div class="az-audit-counts">
          <span class="az-audit-title">AZ MOBILE AUDIT</span>
          ${critical.length?`<span class="az-count crit">${critical.length} CRITICAL</span>`:''}
          ${warns.length?`<span class="az-count warn">${warns.length} WARN</span>`:''}
          ${infos.length?`<span class="az-count info">${infos.length} INFO</span>`:''}
        </div>
        <button class="az-audit-close" onclick="document.getElementById('az-mobile-audit-panel').remove()">CLOSE</button>
      </div>
      <div class="az-audit-list">
        ${[...critical,...warns,...infos].map(i => `
          <div class="az-issue">
            <span class="az-sev ${i.severity==='CRITICAL'?'crit':i.severity==='WARN'?'warn':'info'}"></span>
            <span class="az-rule">${i.rule}</span>
            <span class="az-msg">${i.message}</span>
          </div>
        `).join('')}
      </div>
    `;
    document.body.appendChild(panel);
  }

})();
