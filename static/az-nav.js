/**
 * ARTIFACT ZERO — NAV STATE
 * Checks /api/auth/status and swaps nav for logged-in users.
 * Public: Score, Examples, API, Contact
 * Logged-in: Dashboard, SafeCheck, Wall, Compose, Log out
 */
(function(){
  fetch('/api/auth/status')
    .then(function(r){ return r.json(); })
    .then(function(data){
      if(!data.logged_in) return;
      var links = [
        {href:'/dashboard', label:'DASHBOARD'},
        {href:'/safecheck', label:'SAFECHECK'},
        {href:'/wall', label:'WALL'},
        {href:'/compose', label:'COMPOSE'},
        {href:'/logout', label:'LOG OUT'}
      ];
      var navs = document.querySelectorAll('nav, .nav');
      for(var i=0; i<navs.length; i++){
        var nav = navs[i];
        var existing = nav.querySelector('a');
        var cls = existing ? existing.className : '';
        var html = '';
        for(var j=0; j<links.length; j++){
          html += '<a href="'+links[j].href+'"'+(cls?' class="'+cls+'"':'')+'>'+links[j].label+'</a>';
        }
        nav.innerHTML = html;
      }
    })
    .catch(function(){});
})();
