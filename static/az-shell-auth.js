/**
 * az-shell-auth.js — Artifact Zero shell utilities
 * Tiny replacement for az-shell.js. Base.html handles all layout.
 * This script handles: auth nav swap, mobile menu close, cockpit config.
 */
(function(){
  // ── Auth: swap Sign Up → Dashboard if logged in ──
  fetch('/api/auth/status').then(function(r){ return r.json(); }).then(function(d){
    if(d.logged_in){
      document.querySelectorAll('.az-nav-auth').forEach(function(el){
        el.textContent = 'DASHBOARD';
        el.href = '/dashboard';
      });
      document.querySelectorAll('.az-logo').forEach(function(el){
        el.href = '/dashboard';
      });
    }
  }).catch(function(){});

  // ── Mobile menu: close on link tap ──
  document.querySelectorAll('.az-mobile-menu a').forEach(function(a){
    a.addEventListener('click', function(){
      var menu = document.getElementById('az-mobile-menu');
      if(menu) menu.classList.remove('open');
    });
  });

  // ── Cockpit config ──
  fetch('/api/cockpit/config').then(function(r){ return r.json(); }).then(function(cfg){
    if(cfg.banner && cfg.banner.on && cfg.banner.text){
      var b = document.createElement('div');
      b.id = 'az-cockpit-banner';
      b.style.cssText = 'padding:8px 16px;text-align:center;font-family:monospace;font-size:13px;font-weight:bold;cursor:pointer;';
      b.style.color = cfg.banner.color || '#00e89c';
      b.style.background = cfg.banner.bg || '#064e3b';
      b.textContent = cfg.banner.text;
      if(cfg.banner.link) b.onclick = function(){ window.location.href = cfg.banner.link; };
      var topbar = document.querySelector('.az-topbar');
      if(topbar) topbar.after(b);
    }
    if(cfg.modal && cfg.modal.on && cfg.modal.title){
      var shown = sessionStorage.getItem('az-modal-shown');
      var pages = (cfg.modal.pages||'*').split(',').map(function(p){return p.trim();});
      var onPage = pages.includes('*') || pages.includes(window.location.pathname);
      if(!shown && onPage){
        sessionStorage.setItem('az-modal-shown','1');
        var ov = document.createElement('div');
        ov.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';
        var box = document.createElement('div');
        box.style.cssText = 'background:#12151b;border:1px solid #252a35;border-radius:12px;padding:32px;max-width:420px;width:90%;text-align:center;position:relative;';
        box.innerHTML = '<div style="position:absolute;top:12px;right:16px;color:#6b7280;cursor:pointer;font-size:18px" onclick="this.parentElement.parentElement.remove()">\u2715</div><h2 style="color:#00e89c;font-family:monospace;font-size:16px;letter-spacing:2px;margin-bottom:12px">'+(cfg.modal.title||'')+'</h2><p style="color:#e8eaf0;font-size:14px;line-height:1.6;margin-bottom:20px">'+(cfg.modal.body||'')+'</p>'+(cfg.modal.cta?'<a href="'+(cfg.modal.cta_link||'#')+'" style="display:inline-block;background:#00e89c;color:#000;padding:10px 24px;border-radius:6px;text-decoration:none;font-family:monospace;font-weight:bold;font-size:13px;letter-spacing:1px">'+cfg.modal.cta+'</a>':'');
        ov.appendChild(box);
        ov.onclick = function(e){ if(e.target===ov) ov.remove(); };
        document.body.appendChild(ov);
      }
    }
    if(cfg.copy){
      if(cfg.copy.hero_h1){var h=document.querySelector('h1');if(h)h.textContent=cfg.copy.hero_h1;}
      if(cfg.copy.hero_sub){var s=document.querySelector('.subline,.subtitle,.hero-sub,h2');if(s)s.textContent=cfg.copy.hero_sub;}
      if(cfg.copy.cta_btn){var c=document.querySelector('.score-button,.cta-button,button[type="submit"]');if(c)c.textContent=cfg.copy.cta_btn;}
      if(cfg.copy.custom_selector&&cfg.copy.custom_value){var e=document.querySelector(cfg.copy.custom_selector);if(e)e.textContent=cfg.copy.custom_value;}
    }
  }).catch(function(){});
})();
