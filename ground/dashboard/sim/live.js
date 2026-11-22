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

  // Live settings, written into the URL so a reload keeps them. Loaded BEFORE sim.js and charts.js,
  // so they read the filled-in URL. since = when Live was entered: animal counts start at 0 there.
  function liveParams(p, resetSince) {
    p.set('mode', 'live');
    if (!p.get('api')) p.set('api', location.protocol + '//' + (location.hostname || 'localhost') + ':8000');
    if (!p.get('refresh')) p.set('refresh', '5');
    if (resetSince || !p.get('since')) p.set('since', String(Date.now()));
    if (!p.get('weather_url')) p.set('weather_url', POD + '/weather.json');
    return p;
  }
  if (mode === 'live') {
    var filled = liveParams(new URLSearchParams(location.search), false).toString();
    if (filled !== location.search.slice(1)) history.replaceState(null, '', '?' + filled);
  }

  // ---- toggle ----
  var bar = document.getElementById('mode');
  bar.querySelectorAll('button').forEach(function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
    b.onclick = function () {
      if (b.dataset.mode === mode) return;
      var p = new URLSearchParams(location.search);
      if (b.dataset.mode === 'live') liveParams(p, true);
      else {
        p.set('mode', 'sim');
        ['api', 'scene', 'history', 'refresh', 'since', 'weather_url'].forEach(function (k) { p.delete(k); });
      }
      location.search = p.toString();
    };
  });
  document.getElementById('cam-reset').onclick = function () {
    location.search = liveParams(new URLSearchParams(location.search), true).toString();
  };
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

  // ---- "At the Pod" card (bottom right, replaces the dev controls in live mode) ----
  var lp = new URLSearchParams(location.search);
  var API = (lp.get('api') || '').replace(/\/$/, '');
  var WURL = lp.get('weather_url') || POD + '/weather.json';
  var info = document.getElementById('podinfo');
  info.hidden = false;
  var weather = null, summary = null;
  var CONDITION = { clear: 'Clear', cloudy: 'Cloudy', rain: 'Rain', storm: 'Storm', snow: 'Snow', fog: 'Fog' };

  function ago(ms) {
    var s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    return s < 60 ? s + ' s ago' : s < 3600 ? Math.round(s / 60) + ' min ago' : Math.round(s / 3600) + ' h ago';
  }
  function renderInfo() {
    var rows = [];
    if (weather && weather.condition) {
      document.getElementById('pi-weather').textContent =
        (CONDITION[weather.condition] || weather.condition) + ' · ' + weather.temp_c + '°C';
      rows.push(['Wind', weather.wind_kmh + ' km/h'], ['Humidity', weather.humidity != null ? weather.humidity + '%' : '–'],
                ['Location', weather.place || (weather.lat + ', ' + weather.lon)]);
    } else {
      document.getElementById('pi-weather').textContent = 'Weather unavailable';
    }
    var pod = summary && summary.pods && summary.pods[0];
    var hb = pod && pod.last_heartbeat;
    if (pod) rows.push(['Sightings (all time)', String(pod.n_events)]);
    if (hb) {
      rows.push(['Pi temperature', hb.temp_c + '°C'], ['AI per frame', Math.round(hb.infer_p50_ms) + ' ms'],
                ['Camera → result', Math.round(hb.e2e_p50_ms) + ' ms'], ['Free disk', Math.round(hb.disk_free_mb / 1024) + ' GB'],
                ['Last heartbeat', ago(hb.received_ms)]);
    } else if (pod) {
      rows.push(['Last heartbeat', 'none yet']);
    }
    document.getElementById('pi-rows').innerHTML = rows.map(function (r) {
      return '<dt>' + esc(r[0]) + '</dt><dd>' + esc(r[1]) + '</dd>';
    }).join('');
    document.getElementById('pi-src').textContent = weather && weather.condition
      ? 'Weather: ' + weather.source + ', ' + ago(weather.t_ms) + '. Location ' + weather.location_source + '.'
      : 'Weather comes from the Pi (' + WURL + ').';
  }
  function loadWeather() {
    fetch(WURL, { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (w) { weather = w; }).catch(function () { weather = null; })
      .then(renderInfo);
  }
  function loadSummary() {
    if (!API) return;
    fetch(API + '/api/summary', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (s) { summary = s; }).catch(function () { summary = null; })
      .then(renderInfo);
  }
  loadWeather(); loadSummary();
  setInterval(loadWeather, 60000);
  setInterval(loadSummary, 10000);
})();
