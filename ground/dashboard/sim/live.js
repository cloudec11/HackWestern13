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
  var CAP_KEY = 'pod-captures-v1';     // capture snapshots kept in this browser (see "Captured strip" below)
  document.getElementById('cam-reset').onclick = function () {
    try { localStorage.removeItem(CAP_KEY); } catch (e) {}
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

  // ---- Captured strip (bottom of the camera panel) ----
  // pod-infer confirms an animal (CLIP vote) -> scan.json captures +1 -> pod-servo copies it into
  // pod-view's /servo.json. That's the moment to snap: the servo then holds still on the animal for
  // a couple of seconds, so /snapshot.jpg (current frame, boxes drawn on the Pi) shows it. The visit
  // record itself is only written once the animal has left, so it would be too late for a photo.
  // Snapshots are kept in this browser (localStorage, newest 6); "reset counts" clears them.
  var capsList = document.getElementById('cam-caps');
  var capsNote = document.getElementById('cam-caps-note');
  var caps = [];
  try { caps = JSON.parse(localStorage.getItem(CAP_KEY) || '[]'); } catch (e) { caps = []; }
  var lastStart = null, lastCaptures = null, newestT = 0;

  function renderCaps() {
    capsList.innerHTML = caps.length
      ? caps.map(function (c) {
          return '<li' + (c.t === newestT ? ' class="new"' : '') + '><img src="' + c.img + '" alt="' + esc(c.species) +
                 ' captured"><b>' + esc(c.species) + '</b>' + new Date(c.t).toTimeString().slice(0, 8) + '</li>';
        }).join('')
      : '<li class="none">Nothing captured yet. A snapshot appears here when an animal is confirmed.</li>';
  }
  function saveCaps() {
    for (var keep = caps.length; keep > 0; keep--) {
      try { localStorage.setItem(CAP_KEY, JSON.stringify(caps.slice(0, keep))); return; }
      catch (e) { /* storage full: keep fewer */ }
    }
  }
  function snap(species) {
    fetch(POD + '/snapshot.jpg?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('snapshot ' + r.status); return r.blob(); })
      .then(function (b) {
        return new Promise(function (ok, fail) {
          var fr = new FileReader(); fr.onload = function () { ok(fr.result); }; fr.onerror = fail; fr.readAsDataURL(b);
        });
      })
      .then(function (url) {
        newestT = Date.now();
        caps.unshift({ species: species, t: newestT, img: url });
        caps = caps.slice(0, 6);
        saveCaps();
        renderCaps();
      })
      .catch(function () { capsNote.textContent = 'snapshot failed'; });
  }
  function pollCaptures() {
    fetch(POD + '/servo.json', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        var s = r.servo;
        if (!s || s.state === 'error' || s.state === 'stopped' || typeof s.captures !== 'number') {
          capsNote.textContent = 'needs pod-servo running';
          return;
        }
        capsNote.textContent = s.state === 'hold' ? 'looking at ' + (s.seeing || 'an animal') + '…' : '';
        if (s.started_ms !== lastStart || lastCaptures === null || s.captures < lastCaptures) {
          lastStart = s.started_ms; lastCaptures = s.captures;     // page load or servo restart: baseline only
          return;
        }
        if (s.captures > lastCaptures) {
          lastCaptures = s.captures;
          snap(s.confirmed || s.seeing || 'animal');
        }
      })
      .catch(function () { capsNote.textContent = 'camera offline'; })
      .then(function () { setTimeout(pollCaptures, 500); });
  }
  renderCaps();
  pollCaptures();

  // ---- "At the Pod" card (bottom right, replaces the dev controls in live mode) ----
  var lp = new URLSearchParams(location.search);
  var API = (lp.get('api') || '').replace(/\/$/, '');
  var WURL = lp.get('weather_url') || POD + '/weather.json';
  var info = document.getElementById('podinfo');
  info.hidden = false;
  var weather = null, summary = null, sinceCount = null;
  var SINCE = Number(lp.get('since')) || Date.now();
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
    if (sinceCount !== null)
      rows.push(['Sightings since ' + new Date(SINCE).toTimeString().slice(0, 5), String(sinceCount)]);
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
      .then(function () {
        return fetch(API + '/api/events?limit=10000&since_ms=' + SINCE, { cache: 'no-store' })
          .then(function (r) { return r.json(); })
          .then(function (rows) { sinceCount = rows.filter(function (e) { return e.species !== 'unknown'; }).length; })
          .catch(function () { sinceCount = null; });
      })
      .then(renderInfo);
  }
  loadWeather(); loadSummary();
  setInterval(loadWeather, 60000);
  setInterval(loadSummary, 10000);
})();
