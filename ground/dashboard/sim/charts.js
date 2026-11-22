// The Pod: charts panel for the habitat view (agent-4). Plain JavaScript + SVG, no libraries.
//
// Shows when animals appeared in this area:
//   1. Appearances over time (date on x, appearances per time bucket on y), one line per species
//   2. Time of day each species shows up (hour x species heat strip)
//   3. Most seen species (bars)
// plus headline numbers. Data comes from, in order:
//   ?history=URL                 a JSON list of {species, t_ms} (or API event rows with t_start_ms)
//   ?api=http://host:8000        the ground API: /api/events
//   live_history.json            written by demo/laptop_detector.py (when ?scene=live_scene.json)
//   otherwise                    generated FAKE history, clearly labelled
// Colours: validated categorical palette (dark steps) checked against this panel's surface.

(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var API = (params.get('api') || '').replace(/\/$/, '');
  var SCENE = params.get('scene') || '';
  var HISTORY_URL = params.get('history') ||
    (API ? API + '/api/events?limit=10000&since_ms=' + (Number(params.get('since')) || (Date.now() - 90 * 864e5)) :
     /live_scene/.test(SCENE) ? 'live_history.json' : '');
  var REFRESH_MS = Math.max(5, Number(params.get('refresh')) || 30) * 1000;

  // Categorical slots, fixed order (validated: dark mode, surface #161c24). Colour follows the species.
  var SLOTS = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#0ca30c', '#9085e9', '#e66767'];
  var OTHER = '#7d8590';
  var MAX_LINES = 5;              // more species fold into "other"
  var HOUR = 3600e3, DAY = 864e5;
  var RANGES = [
    { key: 'live', label: 'Last hour', span: HOUR,     bucket: 2 * 60e3 },
    { key: 'day',  label: '24 h',      span: DAY,      bucket: HOUR },
    { key: 'week', label: '7 days',    span: 7 * DAY,  bucket: 6 * HOUR },
    { key: 'month',label: '30 days',   span: 30 * DAY, bucket: DAY },
    { key: 'all',  label: 'All',       span: null,     bucket: null },
  ];

  var state = { events: [], source: '', range: 'month', colors: {}, open: false, table: false };

  // ------------------------------------------------------------ data
  function rng(seed) { return function () { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; }; }

  // Activity profile by hour (0..23) so fake data looks like real behaviour.
  function profile(species) {
    var night = ['fox', 'raccoon', 'owl', 'coyote', 'skunk', 'wolf', 'bat', 'hedgehog', 'mouse'];
    var dusk = ['deer', 'moose', 'elk', 'rabbit', 'bear'];
    return function (h) {
      if (night.indexOf(species) >= 0) return h >= 20 || h <= 4 ? 1 : h >= 18 || h <= 6 ? 0.4 : 0.05;
      if (dusk.indexOf(species) >= 0) return (h >= 5 && h <= 8) || (h >= 17 && h <= 21) ? 1 : 0.15;
      return h >= 7 && h <= 18 ? 1 : 0.05;                         // daytime animals
    };
  }

  function fakeHistory(speciesList) {
    var r = rng(12345), out = [], now = Date.now(), start = now - 45 * DAY;
    speciesList.forEach(function (sp, i) {
      var p = profile(sp), base = [2.2, 1.4, 3.0, 0.5, 4.0, 1.0][i % 6], trend = [0, -0.012, 0.006, 0, 0.004, -0.006][i % 6];
      for (var t = start; t < now; t += HOUR) {
        var d = (t - start) / DAY, h = new Date(t).getHours();
        var rate = (base / 8) * p(h) * Math.exp(trend * d) * (1 + 0.3 * Math.sin(d / 3 + i));
        while (r() < rate) { out.push({ species: sp, t_ms: t + Math.floor(r() * HOUR) }); rate -= 1; }
      }
    });
    return out;
  }

  function normalise(rows) {
    return (rows || []).map(function (e) {
      return { species: e.species, t_ms: e.t_ms != null ? e.t_ms : e.t_start_ms };
    }).filter(function (e) { return e.species && e.species !== 'unknown' && typeof e.t_ms === 'number'; });
  }

  function load() {
    if (!HISTORY_URL) {
      if (!state.events.length) {
        var names = ['deer', 'fox', 'rabbit', 'bird', 'bear'];
        state.events = fakeHistory(names);
        state.source = 'FAKE demo history (no real data source)';
      }
      return Promise.resolve();
    }
    return fetch(HISTORY_URL, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (rows) {
        state.events = normalise(Array.isArray(rows) ? rows : rows.events);
        state.source = API ? 'ground API' : /live_history/.test(HISTORY_URL) ? 'laptop scanner' : HISTORY_URL;
      })
      .catch(function (e) { state.source = 'could not load ' + HISTORY_URL + ' (' + e.message + ')'; });
  }

  // Species colours: assigned once by all-time totals, then never repainted by filtering.
  function assignColors() {
    var totals = {};
    state.events.forEach(function (e) { totals[e.species] = (totals[e.species] || 0) + 1; });
    Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a] || (a < b ? -1 : 1); })
      .forEach(function (sp) {
        if (state.colors[sp]) return;
        var used = Object.keys(state.colors).length;
        state.colors[sp] = used < SLOTS.length ? SLOTS[used] : OTHER;
      });
  }

  function currentRange() {
    var R = RANGES.filter(function (r) { return r.key === state.range; })[0];
    var now = Date.now(), first = state.events.length ? Math.min.apply(null, state.events.map(function (e) { return e.t_ms; })) : now - DAY;
    var span = R.span || Math.max(HOUR, now - first);
    var bucket = R.bucket || (span <= 2 * DAY ? HOUR : span <= 120 * DAY ? DAY : 7 * DAY);
    var end = Math.ceil(now / bucket) * bucket, start = end - Math.ceil(span / bucket) * bucket;
    if (bucket >= DAY) {                                            // align day buckets to local midnight
      var d = new Date(now); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + 1);
      end = d.getTime(); start = end - Math.ceil(span / bucket) * bucket;
    }
    return { start: start, end: end, bucket: bucket };
  }

  // ------------------------------------------------------------ small helpers
  function el(tag, attrs, text) {
    var e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  // Axis scale with whole-number ticks: returns {max, step}.
  function scale(n) {
    var raw = Math.max(1, n) / 4, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    var step = Math.max(1, (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p);
    return { max: step * Math.max(1, Math.ceil(Math.max(1, n) / step)), step: step };
  }
  function label(sp) { return String(sp).replace(/_/g, ' '); }
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function pad(n) { return ('0' + n).slice(-2); }
  function fmtTime(t, bucket, long) {
    var d = new Date(t), date = MONTHS[d.getMonth()] + ' ' + d.getDate(), hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (bucket >= DAY) return date;
    if (bucket >= HOUR) return long ? date + ', ' + hm : (d.getHours() === 0 ? date : hm);
    return hm;
  }
  function ago(t) {
    var s = (Date.now() - t) / 1000;
    return s < 90 ? Math.round(s) + ' s ago' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago';
  }

  var tip;
  function showTip(html, x, y) {
    tip.innerHTML = html; tip.hidden = false;
    var box = document.getElementById('charts').getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    var left = x + 14 + w > box.right - 8 ? x - w - 14 : x + 14;
    tip.style.left = (left - box.left) + 'px';
    tip.style.top = Math.max(4, y - h / 2 - box.top + document.getElementById('charts').scrollTop) + 'px';
  }
  function hideTip() { tip.hidden = true; }
  function swatch(c) { return '<span class="sw" style="background:' + c + '"></span>'; }

  // ------------------------------------------------------------ render
  function render() {
    if (!state.open) return;
    assignColors();
    var R = currentRange();
    var inRange = state.events.filter(function (e) { return e.t_ms >= R.start && e.t_ms < R.end; });
    var totals = {};
    inRange.forEach(function (e) { totals[e.species] = (totals[e.species] || 0) + 1; });
    var ranked = Object.keys(totals).sort(function (a, b) { return totals[b] - totals[a]; });

    document.getElementById('ch-source').textContent = 'Source: ' + state.source;
    renderStats(inRange, totals, ranked);
    renderLine(inRange, ranked, R);
    renderHours(inRange, ranked);
    renderBars(totals, ranked);
  }

  function renderStats(inRange, totals, ranked) {
    var last = inRange.reduce(function (m, e) { return e.t_ms > m.t_ms ? e : m; }, { t_ms: 0 });
    var tiles = [
      ['Appearances', inRange.length.toLocaleString()],
      ['Species', String(ranked.length)],
      ['Most seen', ranked.length ? label(ranked[0]) : '–'],
      ['Last seen', last.t_ms ? label(last.species) + ', ' + ago(last.t_ms) : '–'],
    ];
    document.getElementById('ch-stats').innerHTML = tiles.map(function (t) {
      return '<div class="stat"><div class="k">' + t[0] + '</div><div class="v">' + t[1] + '</div></div>';
    }).join('');
  }

  function renderLine(inRange, ranked, R) {
    var host = document.getElementById('ch-line');
    host.innerHTML = '';
    var n = Math.round((R.end - R.start) / R.bucket);
    var shown = ranked.slice(0, ranked.length > MAX_LINES ? MAX_LINES - 1 : MAX_LINES);
    var series = shown.map(function (sp) { return { name: sp, color: state.colors[sp], v: new Array(n).fill(0) }; });
    if (ranked.length > shown.length) series.push({ name: 'other', color: OTHER, v: new Array(n).fill(0) });
    inRange.forEach(function (e) {
      var i = Math.floor((e.t_ms - R.start) / R.bucket);
      if (i < 0 || i >= n) return;
      var s = series.filter(function (x) { return x.name === e.species; })[0] || (series[series.length - 1].name === 'other' ? series[series.length - 1] : null);
      if (s) s.v[i]++;
    });

    // legend (always for 2+ series)
    var legend = document.getElementById('ch-legend');
    legend.innerHTML = series.length > 1 ? series.map(function (s) { return '<span>' + swatch(s.color) + label(s.name) + '</span>'; }).join('') : '';

    if (!series.length) { host.innerHTML = '<div class="empty">No appearances in this time range yet.</div>'; renderTable([], R, n); return; }

    var W = host.clientWidth || 600, H = 230, m = { l: 40, r: series.length <= 4 ? 84 : 14, t: 10, b: 26 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var sc = scale(Math.max.apply(null, series.map(function (s) { return Math.max.apply(null, s.v); })));
    var max = sc.max;
    function X(i) { return m.l + (n === 1 ? iw / 2 : (i / (n - 1)) * iw); }
    function Y(v) { return m.t + ih - (v / max) * ih; }

    var svg = el('svg', { width: W, height: H, role: 'img', 'aria-label': 'Animal appearances over time' });
    for (var gv = 0; gv <= max; gv += sc.step) {                     // recessive grid + whole-number y labels
      svg.appendChild(el('line', { x1: m.l, x2: W - m.r, y1: Y(gv), y2: Y(gv), class: 'grid' }));
      svg.appendChild(el('text', { x: m.l - 8, y: Y(gv) + 4, class: 'axis', 'text-anchor': 'end' }, String(gv)));
    }
    var step = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 90))));
    for (var i = 0; i < n; i += step) {
      svg.appendChild(el('text', { x: X(i), y: H - 6, class: 'axis', 'text-anchor': 'middle' }, fmtTime(R.start + i * R.bucket, R.bucket)));
    }
    series.forEach(function (s) {
      var d = s.v.map(function (v, k) { return (k ? 'L' : 'M') + X(k).toFixed(1) + ' ' + Y(v).toFixed(1); }).join('');
      svg.appendChild(el('path', { d: d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    });
    if (n <= 60) series.forEach(function (s) {                      // dots where animals were actually seen
      s.v.forEach(function (v, k) {
        if (v) svg.appendChild(el('circle', { cx: X(k), cy: Y(v), r: 3.5, fill: s.color, stroke: '#1b222c', 'stroke-width': 2 }));
      });
    });
    if (series.length <= 4) {                                      // direct labels at line ends, nudged apart
      var ends = series.map(function (s) { return { s: s, y: Y(s.v[n - 1]) }; }).sort(function (a, b) { return a.y - b.y; });
      for (var k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 14) ends[k].y = ends[k - 1].y + 14;
      var bottom = m.t + ih, over = ends.length ? ends[ends.length - 1].y - bottom : 0;
      if (over > 0) ends.forEach(function (e) { e.y -= over; });     // pushed past the axis: shift the stack up
      ends.forEach(function (e) {
        svg.appendChild(el('text', { x: W - m.r + 8, y: e.y + 4, class: 'endlabel' }, label(e.s.name)));
      });
    }

    // hover: crosshair + tooltip
    var cross = el('line', { y1: m.t, y2: m.t + ih, class: 'cross', visibility: 'hidden' });
    svg.appendChild(cross);
    var dots = series.map(function (s) {
      var c = el('circle', { r: 4.5, fill: s.color, stroke: '#161c24', 'stroke-width': 2, visibility: 'hidden' });
      svg.appendChild(c); return c;
    });
    var hit = el('rect', { x: m.l, y: m.t, width: iw, height: ih, fill: 'transparent' });
    svg.appendChild(hit);
    hit.addEventListener('mousemove', function (ev) {
      var rect = svg.getBoundingClientRect();
      var k = Math.max(0, Math.min(n - 1, Math.round(((ev.clientX - rect.left - m.l) / iw) * (n - 1))));
      cross.setAttribute('x1', X(k)); cross.setAttribute('x2', X(k)); cross.setAttribute('visibility', 'visible');
      series.forEach(function (s, j) { dots[j].setAttribute('cx', X(k)); dots[j].setAttribute('cy', Y(s.v[k])); dots[j].setAttribute('visibility', 'visible'); });
      var rows = series.slice().sort(function (a, b) { return b.v[k] - a.v[k]; }).map(function (s) {
        return '<div class="row">' + swatch(s.color) + '<span>' + label(s.name) + '</span><b>' + s.v[k] + '</b></div>';
      }).join('');
      var t0 = R.start + k * R.bucket;
      var sofar = t0 + R.bucket > Date.now() ? ' (so far)' : '';
      showTip('<div class="th">' + fmtTime(t0, R.bucket, true) + (R.bucket < DAY ? ' – ' + fmtTime(t0 + R.bucket, R.bucket, false) : '') + sofar + '</div>' + rows, ev.clientX, ev.clientY);
    });
    hit.addEventListener('mouseleave', function () {
      cross.setAttribute('visibility', 'hidden'); dots.forEach(function (d) { d.setAttribute('visibility', 'hidden'); }); hideTip();
    });
    host.appendChild(svg);
    renderTable(series, R, n);
  }

  function renderTable(series, R, n) {
    var host = document.getElementById('ch-table');
    host.hidden = !state.table;
    if (!state.table) return;
    if (!series.length) { host.innerHTML = ''; return; }
    var html = '<table><thead><tr><th>Time</th>' + series.map(function (s) { return '<th>' + label(s.name) + '</th>'; }).join('') + '</tr></thead><tbody>';
    for (var i = n - 1; i >= 0; i--) {
      var any = series.some(function (s) { return s.v[i]; });
      if (!any) continue;
      html += '<tr><td>' + fmtTime(R.start + i * R.bucket, R.bucket, true) + '</td>' + series.map(function (s) { return '<td>' + s.v[i] + '</td>'; }).join('') + '</tr>';
    }
    host.innerHTML = html + '</tbody></table>';
  }

  // Sequential blue ramp for the heat strip: near zero recedes into the dark surface.
  var RAMP = ['#1b2430', '#184f95', '#2a78d6', '#6da7ec', '#b7d3f6'];
  function rampColor(t) {
    if (t <= 0) return RAMP[0];
    var x = Math.min(0.9999, t) * (RAMP.length - 1), i = Math.floor(x), f = x - i;
    function c(h) { return [parseInt(h.substr(1, 2), 16), parseInt(h.substr(3, 2), 16), parseInt(h.substr(5, 2), 16)]; }
    var a = c(RAMP[i]), b = c(RAMP[i + 1]);
    return 'rgb(' + [0, 1, 2].map(function (j) { return Math.round(a[j] + (b[j] - a[j]) * f); }).join(',') + ')';
  }

  function renderHours(inRange, ranked) {
    var host = document.getElementById('ch-hours');
    host.innerHTML = '';
    var rows = ranked.slice(0, 6);
    if (!rows.length) { host.innerHTML = '<div class="empty">No data yet.</div>'; return; }
    var grid = {}, max = 1;
    rows.forEach(function (sp) { grid[sp] = new Array(24).fill(0); });
    inRange.forEach(function (e) { if (grid[e.species]) { var h = new Date(e.t_ms).getHours(); grid[e.species][h]++; max = Math.max(max, grid[e.species][h]); } });
    var W = host.clientWidth || 400, left = 78, cw = (W - left - 4) / 24, chh = 20, gap = 2, H = rows.length * (chh + gap) + 22;
    var svg = el('svg', { width: W, height: H, role: 'img', 'aria-label': 'Time of day each species appears' });
    rows.forEach(function (sp, r) {
      var y = r * (chh + gap);
      svg.appendChild(el('text', { x: left - 8, y: y + chh / 2 + 4, class: 'axis', 'text-anchor': 'end' }, label(sp)));
      grid[sp].forEach(function (v, h) {
        var cell = el('rect', { x: left + h * cw, y: y, width: Math.max(1, cw - gap), height: chh, rx: 2, fill: rampColor(v / max) });
        cell.addEventListener('mousemove', function (ev) {
          showTip('<div class="th">' + label(sp) + '</div><div class="row"><span>' + pad(h) + ':00–' + pad((h + 1) % 24) + ':00</span><b>' + v + '</b></div>', ev.clientX, ev.clientY);
        });
        cell.addEventListener('mouseleave', hideTip);
        svg.appendChild(cell);
      });
    });
    [0, 6, 12, 18].forEach(function (h) {
      svg.appendChild(el('text', { x: left + h * cw, y: H - 4, class: 'axis' }, pad(h) + ':00'));
    });
    host.appendChild(svg);
  }

  function renderBars(totals, ranked) {
    var host = document.getElementById('ch-bars');
    host.innerHTML = '';
    var rows = ranked.slice(0, 8);
    if (!rows.length) { host.innerHTML = '<div class="empty">No data yet.</div>'; return; }
    var W = host.clientWidth || 300, left = 78, right = 44, bh = 14, gap = 10, H = rows.length * (bh + gap);
    var max = totals[rows[0]];
    var svg = el('svg', { width: W, height: H, role: 'img', 'aria-label': 'Most seen species' });
    rows.forEach(function (sp, i) {
      var y = i * (bh + gap), w = Math.max(3, ((W - left - right) * totals[sp]) / max);
      svg.appendChild(el('text', { x: left - 8, y: y + bh / 2 + 4, class: 'axis', 'text-anchor': 'end' }, label(sp)));
      // flat at the baseline, 4px rounded data end
      svg.appendChild(el('path', { d: 'M' + left + ' ' + y + 'h' + (w - 4) + 'a4 4 0 0 1 4 4v' + (bh - 8) + 'a4 4 0 0 1 -4 4h' + (4 - w) + 'z', fill: state.colors[sp] || OTHER }));
      svg.appendChild(el('text', { x: left + w + 6, y: y + bh / 2 + 4, class: 'val' }, String(totals[sp])));
    });
    host.appendChild(svg);
  }

  // ------------------------------------------------------------ panel wiring
  function build() {
    var btn = document.getElementById('charts-toggle'), panel = document.getElementById('charts');
    tip = document.getElementById('ch-tip');
    var bar = document.getElementById('ch-ranges');
    RANGES.forEach(function (r) {
      var b = document.createElement('button');
      b.textContent = r.label; b.dataset.key = r.key;
      b.onclick = function () { state.range = r.key; syncRanges(); render(); };
      bar.appendChild(b);
    });
    function syncRanges() {
      Array.prototype.forEach.call(bar.children, function (b) { b.setAttribute('aria-pressed', b.dataset.key === state.range); });
    }
    if (/live/.test(state.source) || /live_history/.test(HISTORY_URL)) state.range = 'live';
    syncRanges();
    function setOpen(open) {
      state.open = open; panel.hidden = !open; btn.setAttribute('aria-expanded', open);
      btn.textContent = open ? 'Hide charts' : 'Charts';
      if (open) load().then(render);
    }
    btn.onclick = function () { setOpen(!state.open); };
    document.getElementById('ch-close').onclick = function () { setOpen(false); };
    document.getElementById('ch-table-toggle').onchange = function (e) { state.table = e.target.checked; render(); };
    addEventListener('resize', function () { if (state.open) render(); });
    setInterval(function () { if (state.open) load().then(render); }, REFRESH_MS);
    if (params.get('charts') === '1') setOpen(true);
  }

  build();
})();
