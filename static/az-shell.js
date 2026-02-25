/**
 * az-shell.js v1.0
 * Unified header + footer for all Artifact Zero pages.
 * Drop <script src="/static/az-shell.js"></script> in any page.
 * Auto-detects current page and highlights nav.
 * Respects pages that set window.AZ_SHELL_SKIP = true to opt out.
 */
(function(){
  if(window.AZ_SHELL_SKIP) return;

  const path = window.location.pathname;

  // ═══════════════════════════════════════
  // NAV STRUCTURE
  // ═══════════════════════════════════════
  const NAV_LEFT = [
    { label: 'ARTIFACT ZERO', href: '/', isLogo: true },
  ];

  const NAV_RIGHT = [
    { label: 'SafeCheck', href: '/safecheck' },
    { label: 'Score',     href: '/score' },
    { label: 'Live',      href: '/live' },
    { label: 'Examples',  href: '/examples' },
    { label: 'API',       href: '/docs' },
    { label: 'Contact',   href: '/contact' },
  ];

  // ═══════════════════════════════════════
  // STYLES (injected once)
  // ═══════════════════════════════════════
  const STYLE_ID = 'az-shell-styles';
  if(!document.getElementById(STYLE_ID)){
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      .az-topbar{position:fixed;top:0;left:0;right:0;z-index:9000;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;background:rgba(10,12,16,.88);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid rgba(37,42,53,.5)}
      .az-topbar .az-logo{font-family:'JetBrains Mono',monospace;font-weight:700;font-size:14px;letter-spacing:2px;color:#00e89c;text-decoration:none}
      .az-topbar nav{display:flex;align-items:center;gap:18px}
      .az-topbar nav a{font-family:'JetBrains Mono',monospace;font-size:10px;color:#6b7280;text-decoration:none;letter-spacing:1.5px;text-transform:uppercase;transition:color .15s;padding:4px 0}
      .az-topbar nav a:hover{color:#e8eaf0}
      .az-topbar nav a.active{color:#00e89c}
      .az-footer{border-top:1px solid #252a35;padding:14px 24px;background:#0a0c10;display:flex;justify-content:space-between;align-items:center;font-family:'JetBrains Mono',monospace;font-size:11px}
      .az-footer-left{color:#4b5563}
      .az-footer-right{display:flex;gap:16px}
      .az-footer-right a{color:#6b7280;text-decoration:none;font-size:11px;letter-spacing:1px;transition:color .15s}
      .az-footer-right a:hover{color:#00e89c}
      @media(max-width:640px){
        .az-topbar{padding:12px 16px}
        .az-topbar nav{gap:12px}
        .az-topbar nav a{font-size:9px;letter-spacing:1px}
        .az-footer{flex-direction:column;gap:8px;text-align:center;padding:12px 16px}
        .az-footer-right{flex-wrap:wrap;justify-content:center}
      }
    `;
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════
  // DETECT & REMOVE EXISTING TOPBAR
  // ═══════════════════════════════════════
  function removeExisting(){
    // Common patterns for existing topbars/footers across all AZ pages
    const selectors = [
      '.topbar',           // safecheck, docs, score, dashboards, etc
      '.nav',              // contact, examples, wall
      '.header-bar',
      'header.topbar',
      '[class*="topbar"]',
      '.footer',           // old inline footers
    ];
    selectors.forEach(sel => {
      document.querySelectorAll(sel).forEach(el => {
        if(!el.classList.contains('az-topbar') && !el.classList.contains('az-footer')){
          el.remove();
        }
      });
    });
    // Also remove inline-styled nav bars (control-room, relay, lab patterns)
    // These have an <a> with "ARTIFACT ZERO" text inside a sidebar or top div
    document.querySelectorAll('.sb-head, .sidebar').forEach(el => {
      // Don't remove sidebars — just the redundant top logo/nav if present
    });
  }

  // ═══════════════════════════════════════
  // BUILD TOPBAR
  // ═══════════════════════════════════════
  function buildTopbar(){
    if(document.querySelector('.az-topbar')) return;

    const bar = document.createElement('div');
    bar.className = 'az-topbar';

    // Logo
    const logo = document.createElement('a');
    logo.href = '/';
    logo.className = 'az-logo';
    logo.textContent = 'ARTIFACT ZERO';
    bar.appendChild(logo);

    // Nav
    const nav = document.createElement('nav');
    NAV_RIGHT.forEach(item => {
      const a = document.createElement('a');
      a.href = item.href;
      a.textContent = item.label.toUpperCase();
      // Active detection
      if(path === item.href || (item.href !== '/' && path.startsWith(item.href))){
        a.className = 'active';
      }
      nav.appendChild(a);
    });
    bar.appendChild(nav);

    // Insert at top of body
    document.body.insertBefore(bar, document.body.firstChild);
  }

  // ═══════════════════════════════════════
  // BUILD FOOTER
  // ═══════════════════════════════════════
  function buildFooter(){
    if(document.querySelector('.az-footer')) return;

    const footer = document.createElement('div');
    footer.className = 'az-footer';

    const left = document.createElement('span');
    left.className = 'az-footer-left';
    left.textContent = `\u00A9 ${new Date().getFullYear()} Artifact Zero Labs \u00B7 Knoxville, TN`;
    footer.appendChild(left);

    const right = document.createElement('span');
    right.className = 'az-footer-right';
    const footerLinks = [
      {label:'SafeCheck',href:'/safecheck'},
      {label:'API',href:'/docs'},
      {label:'Examples',href:'/examples'},
      {label:'Live',href:'/live'},
      {label:'Contact',href:'/contact'}
    ];
    footerLinks.forEach(link => {
      const a = document.createElement('a');
      a.href = link.href;
      a.textContent = link.label;
      right.appendChild(a);
    });
    footer.appendChild(right);

    document.body.appendChild(footer);
  }

  // ═══════════════════════════════════════
  // ADJUST BODY PADDING
  // ═══════════════════════════════════════
  function adjustPadding(){
    // Add top padding to body if not already handled
    const cs = getComputedStyle(document.body);
    const pt = parseInt(cs.paddingTop) || 0;
    if(pt < 50){
      // Don't override if the page already has substantial padding
      // Just ensure content doesn't hide under the fixed topbar
      const spacer = document.createElement('div');
      spacer.style.height = '56px';
      spacer.className = 'az-topbar-spacer';
      if(!document.querySelector('.az-topbar-spacer')){
        document.body.insertBefore(spacer, document.body.children[1]); // after topbar
      }
    }
  }

  // ═══════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════
  function init(){
    removeExisting();
    buildTopbar();
    buildFooter();
    adjustPadding();
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
