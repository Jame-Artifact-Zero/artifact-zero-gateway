/**
 * az-shell.js v2.0
 * Unified header + footer for all Artifact Zero pages.
 * Drop <script src="/static/az-shell.js"></script> in any page.
 * Auto-detects current page and highlights nav.
 * Respects pages that set window.AZ_SHELL_SKIP = true to opt out.
 *
 * v2.0 CHANGES:
 * - Footer sticks to bottom via flex column body (fixes huge gap on short pages)
 * - Mobile hamburger menu (fixes nav wrapping on small screens)
 * - Proper topbar spacer that accounts for actual topbar height
 * - Removes orphaned .footer/.topbar elements from page templates
 */
(function(){
  if(window.AZ_SHELL_SKIP) return;

  var path = window.location.pathname;

  // ═══════════════════════════════════════
  // NAV STRUCTURE
  // ═══════════════════════════════════════
  var NAV_LINKS = [
    { label: 'SafeCheck', href: '/safecheck' },
    { label: 'Score',     href: '/score' },
    { label: 'Live',      href: '/live' },
    { label: 'Examples',  href: '/examples' },
    { label: 'API',       href: '/docs' },
    { label: 'Contact',   href: '/contact' },
    { label: 'Sign Up',   href: '/signup' },
  ];

  // ═══════════════════════════════════════
  // STYLES (injected once)
  // ═══════════════════════════════════════
  var STYLE_ID = 'az-shell-styles';
  if(!document.getElementById(STYLE_ID)){
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      /* Body flex column — footer sticks to bottom on short pages */
      'html{height:100%}',
      'body{min-height:100%;display:flex;flex-direction:column}',
      'body>.az-topbar-spacer~*:not(.az-topbar):not(.az-footer):not(.az-topbar-spacer){flex:1 0 auto}',
      /* Catch-all: first non-shell child grows */
      '.az-shell-content-wrap{flex:1 0 auto}',

      /* Topbar */
      '.az-topbar{position:fixed;top:0;left:0;right:0;z-index:9000;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;background:rgba(10,12,16,.95);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-bottom:1px solid rgba(37,42,53,.5)}',
      '.az-topbar .az-logo{font-family:"JetBrains Mono",monospace;font-weight:700;font-size:13px;letter-spacing:2px;color:#00e89c;text-decoration:none;flex-shrink:0;white-space:nowrap}',

      /* Desktop nav */
      '.az-topbar nav{display:flex;align-items:center;gap:18px}',
      '.az-topbar nav a{font-family:"JetBrains Mono",monospace;font-size:10px;color:#6b7280;text-decoration:none;letter-spacing:1.5px;text-transform:uppercase;transition:color .15s;padding:4px 0;white-space:nowrap}',
      '.az-topbar nav a:hover{color:#e8eaf0}',
      '.az-topbar nav a.active{color:#00e89c}',

      /* Hamburger button */
      '.az-hamburger{display:none;background:none;border:none;cursor:pointer;padding:6px;color:#6b7280;font-size:20px;line-height:1}',
      '.az-hamburger:hover{color:#e8eaf0}',

      /* Mobile menu overlay */
      '.az-mobile-menu{display:none;position:fixed;top:0;left:0;right:0;bottom:0;z-index:8999;background:rgba(10,12,16,.98);flex-direction:column;align-items:center;justify-content:center;gap:24px}',
      '.az-mobile-menu.open{display:flex}',
      '.az-mobile-menu a{font-family:"JetBrains Mono",monospace;font-size:14px;color:#6b7280;text-decoration:none;letter-spacing:2px;text-transform:uppercase;transition:color .15s}',
      '.az-mobile-menu a:hover,.az-mobile-menu a.active{color:#00e89c}',
      '.az-mobile-close{position:absolute;top:16px;right:20px;background:none;border:none;color:#6b7280;font-size:24px;cursor:pointer;padding:8px}',
      '.az-mobile-close:hover{color:#e8eaf0}',

      /* Footer — no margin-top, flex pushes it down naturally */
      '.az-footer{border-top:1px solid #252a35;padding:14px 24px;background:#0a0c10;display:flex;justify-content:space-between;align-items:center;font-family:"JetBrains Mono",monospace;font-size:11px;flex-shrink:0}',
      '.az-footer-left{color:#4b5563}',
      '.az-footer-right{display:flex;gap:16px}',
      '.az-footer-right a{color:#6b7280;text-decoration:none;font-size:11px;letter-spacing:1px;transition:color .15s}',
      '.az-footer-right a:hover{color:#00e89c}',

      /* Spacer */
      '.az-topbar-spacer{height:52px;flex-shrink:0}',

      /* Mobile */
      '@media(max-width:700px){',
        '.az-topbar{padding:10px 16px}',
        '.az-topbar nav{display:none}',
        '.az-hamburger{display:block}',
        '.az-topbar-spacer{height:48px}',
        '.az-footer{flex-direction:column;gap:8px;text-align:center;padding:12px 16px}',
        '.az-footer-right{flex-wrap:wrap;justify-content:center}',
      '}',
    ].join('\n');
    document.head.appendChild(style);
  }

  // ═══════════════════════════════════════
  // DETECT & REMOVE EXISTING TOPBAR/FOOTER
  // ═══════════════════════════════════════
  function removeExisting(){
    var selectors = [
      '.topbar', '.nav', '.header-bar', 'header.topbar',
      '[class*="topbar"]',
    ];
    selectors.forEach(function(sel){
      document.querySelectorAll(sel).forEach(function(el){
        if(!el.classList.contains('az-topbar') && !el.classList.contains('az-footer')){
          el.remove();
        }
      });
    });
    // Remove orphaned .footer elements (old inline footers from templates)
    // az-shell builds its own .az-footer
    document.querySelectorAll('.footer').forEach(function(el){
      if(!el.classList.contains('az-footer')){
        el.remove();
      }
    });
  }

  // ═══════════════════════════════════════
  // BUILD TOPBAR
  // ═══════════════════════════════════════
  function buildTopbar(){
    if(document.querySelector('.az-topbar')) return;

    var bar = document.createElement('div');
    bar.className = 'az-topbar';

    // Logo
    var logo = document.createElement('a');
    logo.href = '/';
    logo.className = 'az-logo';
    logo.textContent = 'ARTIFACT ZERO';
    bar.appendChild(logo);

    // Desktop nav
    var nav = document.createElement('nav');
    NAV_LINKS.forEach(function(item){
      var a = document.createElement('a');
      a.href = item.href;
      a.textContent = item.label.toUpperCase();
      if(path === item.href || (item.href !== '/' && path.startsWith(item.href))){
        a.className = 'active';
      }
      nav.appendChild(a);
    });
    bar.appendChild(nav);

    // Hamburger button (mobile only, shown via CSS)
    var burger = document.createElement('button');
    burger.className = 'az-hamburger';
    burger.innerHTML = '&#9776;';
    burger.setAttribute('aria-label', 'Menu');
    burger.onclick = function(){ openMobileMenu(); };
    bar.appendChild(burger);

    // Auth check — swap logo href if logged in
    fetch('/api/auth/status').then(function(r){ return r.json(); }).then(function(d){
      if(d.logged_in) logo.href = '/dashboard';
    }).catch(function(){});

    document.body.insertBefore(bar, document.body.firstChild);
  }

  // ═══════════════════════════════════════
  // MOBILE MENU
  // ═══════════════════════════════════════
  function openMobileMenu(){
    var existing = document.querySelector('.az-mobile-menu');
    if(existing){ existing.classList.add('open'); return; }

    var overlay = document.createElement('div');
    overlay.className = 'az-mobile-menu open';

    var close = document.createElement('button');
    close.className = 'az-mobile-close';
    close.innerHTML = '&times;';
    close.onclick = function(){ overlay.classList.remove('open'); };
    overlay.appendChild(close);

    NAV_LINKS.forEach(function(item){
      var a = document.createElement('a');
      a.href = item.href;
      a.textContent = item.label.toUpperCase();
      if(path === item.href || (item.href !== '/' && path.startsWith(item.href))){
        a.className = 'active';
      }
      a.onclick = function(){ overlay.classList.remove('open'); };
      overlay.appendChild(a);
    });

    document.body.appendChild(overlay);
  }

  // ═══════════════════════════════════════
  // BUILD FOOTER
  // ═══════════════════════════════════════
  function buildFooter(){
    if(document.querySelector('.az-footer')) return;

    var footer = document.createElement('div');
    footer.className = 'az-footer';

    var left = document.createElement('span');
    left.className = 'az-footer-left';
    left.textContent = '\u00A9 ' + new Date().getFullYear() + ' Artifact Zero Labs \u00B7 Knoxville, TN';
    footer.appendChild(left);

    var right = document.createElement('span');
    right.className = 'az-footer-right';
    [
      {label:'SafeCheck',href:'/safecheck'},
      {label:'API',href:'/docs'},
      {label:'Examples',href:'/examples'},
      {label:'Contact',href:'/contact'},
    ].forEach(function(link){
      var a = document.createElement('a');
      a.href = link.href;
      a.textContent = link.label;
      right.appendChild(a);
    });
    footer.appendChild(right);

    document.body.appendChild(footer);
  }

  // ═══════════════════════════════════════
  // ADJUST BODY PADDING (spacer for fixed topbar)
  // ═══════════════════════════════════════
  function adjustPadding(){
    if(document.querySelector('.az-topbar-spacer')) return;
    var spacer = document.createElement('div');
    spacer.className = 'az-topbar-spacer';
    // Insert after topbar (which is first child)
    var topbar = document.querySelector('.az-topbar');
    if(topbar && topbar.nextSibling){
      document.body.insertBefore(spacer, topbar.nextSibling);
    }
  }

  // ═══════════════════════════════════════
  // WRAP CONTENT for flex layout
  // ═══════════════════════════════════════
  function wrapContent(){
    // Wrap everything between spacer and footer in a flex-grow div
    // This ensures footer sticks to bottom on short pages
    var spacer = document.querySelector('.az-topbar-spacer');
    var footer = document.querySelector('.az-footer');
    if(!spacer || !footer) return;
    // Already wrapped?
    if(document.querySelector('.az-shell-content-wrap')) return;

    var wrap = document.createElement('div');
    wrap.className = 'az-shell-content-wrap';

    // Collect all nodes between spacer and footer
    var nodes = [];
    var node = spacer.nextSibling;
    while(node && node !== footer){
      nodes.push(node);
      node = node.nextSibling;
    }
    // Move them into the wrapper
    nodes.forEach(function(n){ wrap.appendChild(n); });
    // Insert wrapper before footer
    document.body.insertBefore(wrap, footer);
  }

  // ═══════════════════════════════════════
  // COCKPIT CLIENT — reads admin controls
  // ═══════════════════════════════════════
  function loadCockpit(){
    fetch('/api/cockpit/config').then(function(r){ return r.json(); }).then(function(cfg){
      // Banner
      if(cfg.banner && cfg.banner.on && cfg.banner.text){
        var old = document.getElementById('az-cockpit-banner');
        if(old) old.remove();
        var b = document.createElement('div');
        b.id = 'az-cockpit-banner';
        b.style.cssText = 'position:fixed;top:48px;left:0;right:0;z-index:9998;padding:8px 16px;text-align:center;font-family:monospace;font-size:13px;font-weight:bold;cursor:pointer;';
        b.style.color = cfg.banner.color || '#00e89c';
        b.style.background = cfg.banner.bg || '#064e3b';
        b.textContent = cfg.banner.text;
        if(cfg.banner.link){
          b.onclick = function(){ window.location.href = cfg.banner.link; };
        }
        document.body.appendChild(b);
        var spacers = document.querySelectorAll('.az-topbar-spacer');
        spacers.forEach(function(s){ s.style.height = '90px'; });
      }

      // Modal pop-up
      if(cfg.modal && cfg.modal.on && cfg.modal.title){
        var shown = sessionStorage.getItem('az-modal-shown');
        var pages = (cfg.modal.pages || '*').split(',').map(function(p){ return p.trim(); });
        var onPage = pages.includes('*') || pages.includes(window.location.pathname);
        if(!shown && onPage){
          sessionStorage.setItem('az-modal-shown', '1');
          var overlay = document.createElement('div');
          overlay.id = 'az-modal-overlay';
          overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
          var box = document.createElement('div');
          box.style.cssText = 'background:#12151b;border:1px solid #252a35;border-radius:12px;padding:32px;max-width:420px;width:90%;text-align:center;position:relative;';
          box.innerHTML = '<div style="position:absolute;top:12px;right:16px;color:#6b7280;cursor:pointer;font-size:18px" onclick="this.parentElement.parentElement.remove()">\u2715</div>'
            + '<h2 style="color:#00e89c;font-family:monospace;font-size:16px;letter-spacing:2px;margin-bottom:12px">' + (cfg.modal.title||'') + '</h2>'
            + '<p style="color:#e8eaf0;font-size:14px;line-height:1.6;margin-bottom:20px">' + (cfg.modal.body||'') + '</p>'
            + (cfg.modal.cta ? '<a href="'+(cfg.modal.cta_link||'#')+'" style="display:inline-block;background:#00e89c;color:#000;padding:10px 24px;border-radius:6px;text-decoration:none;font-family:monospace;font-weight:bold;font-size:13px;letter-spacing:1px">' + cfg.modal.cta + '</a>' : '');
          overlay.appendChild(box);
          overlay.onclick = function(e){ if(e.target === overlay) overlay.remove(); };
          document.body.appendChild(overlay);
        }
      }

      // Copy overrides
      if(cfg.copy){
        if(cfg.copy.hero_h1){
          var h1 = document.querySelector('h1');
          if(h1) h1.textContent = cfg.copy.hero_h1;
        }
        if(cfg.copy.hero_sub){
          var sub = document.querySelector('.subline, .subtitle, .hero-sub, h2');
          if(sub) sub.textContent = cfg.copy.hero_sub;
        }
        if(cfg.copy.cta_btn){
          var cta = document.querySelector('.score-button, .cta-button, button[type="submit"]');
          if(cta) cta.textContent = cfg.copy.cta_btn;
        }
        if(cfg.copy.custom_selector && cfg.copy.custom_value){
          var el = document.querySelector(cfg.copy.custom_selector);
          if(el) el.textContent = cfg.copy.custom_value;
        }
      }
    }).catch(function(){});
  }

  // ═══════════════════════════════════════
  // INIT
  // ═══════════════════════════════════════
  function init(){
    removeExisting();
    buildTopbar();
    buildFooter();
    adjustPadding();
    wrapContent();
    loadCockpit();
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
