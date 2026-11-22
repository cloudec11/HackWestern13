// Simulated / Live switch and the Pod camera panel.
//
// Simulated: fake scene + generated chart history (no Pod needed).
// Live:      the ground API (?api=, default this host :8000) for scene and charts, plus the
//            Pod's live camera with detection boxes (drawn on the Pi by pod-view) and the
//            current detections list.
//
// URL options:
//   ?mode=live|sim                 which one to show (default: live if ?api= is given, else sim)
//   ?api=http://host:8000          ground API for live mode
//   ?pod=http://qnxpi.local:8080   the Pod's pod-view (camera stream + /status.json)
//
// sim.js and charts.js read their settings once at load, so switching reloads the page.
(function () {
  'use strict';
  var params = new URLSearchParams(location.search);
  var mode = params.get('mode') || (params.get('api') ? 'live' : 'sim');
  var POD = (params.get('pod') || 'http://qnxpi.local:8080').replace(/\/$/, '');

  // ---- toggle ----
  var bar = document.getElementById('mode');
  bar.querySelectorAll('button').forEach(function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
    b.onclick = function () {
      if (b.dataset.mode === mode) return;
      var p = new URLSearchParams(location.search);
      p.set('mode', b.dataset.mode);
      if (b.dataset.mode === 'live') {
        if (!p.get('api')) p.set('api', location.protocol + '//' + (location.hostname || 'localhost') + ':8000');
        if (!p.get('refresh')) p.set('refresh', '5');
      } else {
        p.delete('api'); p.delete('scene'); p.delete('history'); p.delete('refresh');
      }
      location.search = p.toString();
    };
  });
  document.body.classList.toggle('live', mode === 'live');
  if (mode !== 'live') return;

  // ---- camera panel (live only) ----
  var cam = document.getElementById('cam');
  var img = document.getElementById('cam-img');
  var off = document.getElementById('cam-off');
  var list = document.getElementById('cam-dets');
  var stats = document.getElementById('cam-stats');
  cam.hidden = false;
  document.getElementById('cam-link').href = POD;

  function startStream() { img.src = POD + '/stream?t=' + Date.now(); }
  img.onload = function () { off.hidden = true; img.hidden = false; };
  img.onerror = function () {
    img.hidden = true; off.hidden = false;
    setTimeout(startStream, 5000);     // pod-view restarted or Pi unreachable: retry
  };
  startStream();

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return '&#' + c.charCodeAt(0) + ';'; }); }

  function poll() {
    fetch(POD + '/status.json', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (s) {
        stats.textContent = s.fps.toFixed(1) + ' fps' + (s.e2e_ms >= 0 ? ' · ' + Math.round(s.e2e_ms) + ' ms camera→AI' : '');
        var d = s.detections || [];
        list.innerHTML = d.length
          ? d.map(function (x) {
              return '<li><b>' + esc(x.species) + '</b><span>' + Math.round(x.score * 100) + '%</span></li>';
            }).join('')
          : '<li class="none">Nothing detected right now</li>';
      })
      .catch(function () {
        stats.textContent = 'camera offline';
        list.innerHTML = '';
      })
      .then(function () { setTimeout(poll, 700); });
  }
  poll();
})();
