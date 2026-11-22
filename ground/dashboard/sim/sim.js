// The Pod: immersive habitat view (agent-4).
// Plain JavaScript, no libraries, no build step. Drawn with the browser's 2D canvas,
// so it runs without a GPU (e.g. a software-rendered browser on the Pi) and offline.
//
// You stand in the middle of the habitat and turn to look around, like a 360 video.
// Trees, rocks, hills and animals are flat pictures ("billboards") placed around you
// in 3D and drawn far-to-near with perspective and fog. Weather and time of day change
// the sky, light, fog, clouds, rain and snow.

(function () {
  'use strict';

  // ------------------------------------------------------------ config
  var params = new URLSearchParams(location.search);
  var API = (params.get('api') || '').replace(/\/$/, '');
  var SCENE_URL = params.get('scene') || (API ? API + '/api/scene' : './fake_scene.json');
  var REFRESH_MS = params.has('refresh') ? Math.max(1, Number(params.get('refresh'))) * 1000 : 30000;
  // Live mode (live.js): count only animals seen since this time (ms), so counts start at 0 on entering
  // Live, and take the weather from the Pod (pod-view /weather.json) instead of the scene's placeholder.
  var SINCE = Number(params.get('since')) || 0;
  var WEATHER_URL = params.get('weather_url') || '';
  var EYE = 1.6;                 // viewer eye height, metres
  var FOG_LEVELS = 8;            // pre-tinted copies per picture

  // Same as fake_scene.json, used when the JSON cannot be fetched (e.g. opened from disk).
  var BUILTIN_SCENE = {
    pod_id: 'pod-01', biome: 'forest', biome_source: 'fake',
    weather: { condition: 'rain', temp_c: 11, wind_kmh: 18, hour: 14, source: 'fake' },
    animals: [
      { species: 'deer', count: 3 }, { species: 'fox', count: 1 }, { species: 'rabbit', count: 4 },
      { species: 'bear', count: 1 }, { species: 'bird', count: 5 },
    ],
  };

  // sky top/bottom, fog [near, far] m, cloud count/colour, sun strength, dim = extra darkness
  var WEATHER = {
    clear:  { label: 'Clear',  top: '#2f6fc9', bottom: '#bfe2ff', fog: [80, 450], clouds: 6,  cloud: '#ffffff', sun: 1.0,  dim: 0 },
    cloudy: { label: 'Cloudy', top: '#76869a', bottom: '#c8d0d8', fog: [50, 320], clouds: 28, cloud: '#e8ebee', sun: 0.35, dim: 0.08 },
    rain:   { label: 'Rain',   top: '#46505b', bottom: '#8c959d', fog: [20, 180], clouds: 34, cloud: '#7c858e', sun: 0.12, dim: 0.2,  rain: 420 },
    storm:  { label: 'Storm',  top: '#262c33', bottom: '#5d656d', fog: [15, 130], clouds: 38, cloud: '#4f565d', sun: 0.05, dim: 0.35, rain: 750, lightning: true },
    snow:   { label: 'Snow',   top: '#9ba7b4', bottom: '#e2e8ee', fog: [15, 150], clouds: 30, cloud: '#d6dce2', sun: 0.2,  dim: 0.05, snow: 380 },
    fog:    { label: 'Fog',    top: '#a7b0b8', bottom: '#d0d6db', fog: [2, 60],   clouds: 0,  cloud: '#ffffff', sun: 0.1,  dim: 0.05 },
  };

  // h = height m, speed m/s
  var SPECIES = {
    deer:     { h: 1.5,  color: '#a8693b', speed: 1.2 },
    fox:      { h: 0.7,  color: '#d9692b', speed: 1.6 },
    rabbit:   { h: 0.45, color: '#c9b79c', speed: 1.4, hop: true },
    bear:     { h: 1.7,  color: '#5b3e2b', speed: 0.8 },
    bird:     { h: 0.4,  color: '#4f86c6', speed: 4.5, fly: true },
    raccoon:  { h: 0.5,  color: '#7d7a76', speed: 1.0 },
    squirrel: { h: 0.35, color: '#a0522d', speed: 1.8, hop: true },
    coyote:   { h: 0.8,  color: '#9c8466', speed: 1.5 },
    moose:    { h: 2.2,  color: '#4b3426', speed: 0.9 },
    elephant: { h: 3.0,  color: '#8e9499', speed: 0.7 },
    giraffe:  { h: 4.5,  color: '#d9a441', speed: 0.9 },
    zebra:    { h: 1.5,  color: '#ececec', speed: 1.3 },
    horse:    { h: 1.7,  color: '#7b4a2a', speed: 1.4 },
    cow:      { h: 1.5,  color: '#6b4b3a', speed: 0.7 },
    sheep:    { h: 0.9,  color: '#f1ede2', speed: 0.8 },
    dog:      { h: 0.7,  color: '#b07040', speed: 1.6 },
    cat:      { h: 0.4,  color: '#e0a060', speed: 1.2 },
    camel:    { h: 2.2,  color: '#c99a5b', speed: 0.9 },
    elk:        { h: 1.8,  color: '#8a5a36', speed: 1.1 },
    wolf:       { h: 0.85, color: '#7f7f86', speed: 1.5 },
    polar_bear: { h: 1.6,  color: '#f2efe6', speed: 0.8 },
    chipmunk:   { h: 0.25, color: '#9b6a3f', speed: 1.8, hop: true },
    beaver:     { h: 0.45, color: '#6b4a2f', speed: 0.7 },
    skunk:      { h: 0.4,  color: '#26262a', speed: 0.9 },
    porcupine:  { h: 0.45, color: '#5d5248', speed: 0.6 },
    otter:      { h: 0.4,  color: '#5e4330', speed: 1.2 },
    hedgehog:   { h: 0.25, color: '#8a7460', speed: 0.6 },
    mouse:      { h: 0.15, color: '#9a8f86', speed: 1.4 },
    owl:        { h: 0.6,  color: '#8b6f4e', speed: 3.5, fly: true },
    eagle:      { h: 0.9,  color: '#5a4030', speed: 5.0, fly: true },
    parrot:     { h: 0.45, color: '#3fae4f', speed: 4.0, fly: true },
    butterfly:  { h: 0.2,  color: '#f2a03d', speed: 1.5, fly: true },
    duck:       { h: 0.5,  color: '#6f8f4a', speed: 0.8 },
    goose:      { h: 0.8,  color: '#8d8a84', speed: 0.9 },
    penguin:    { h: 0.8,  color: '#2b2f36', speed: 0.6 },
    turtle:     { h: 0.3,  color: '#5c7a3d', speed: 0.3 },
    frog:       { h: 0.2,  color: '#5fa04a', speed: 1.0, hop: true },
    snake:      { h: 0.2,  color: '#6e7f3a', speed: 0.6 },
    lizard:     { h: 0.2,  color: '#8fa34a', speed: 1.2 },
    crocodile:  { h: 0.6,  color: '#4f6b3a', speed: 0.5 },
    pig:        { h: 0.9,  color: '#e9a9a0', speed: 0.8 },
    goat:       { h: 0.9,  color: '#d8cfc0', speed: 1.0 },
    hippo:      { h: 1.5,  color: '#7d6f78', speed: 0.6 },
    rhino:      { h: 1.7,  color: '#8a8580', speed: 0.7 },
    lion:       { h: 1.2,  color: '#c99a4b', speed: 1.0 },
    tiger:      { h: 1.1,  color: '#e08a2e', speed: 1.1 },
    leopard:    { h: 0.8,  color: '#d9a650', speed: 1.3 },
    cheetah:    { h: 0.85, color: '#d8b060', speed: 1.8 },
    monkey:     { h: 0.7,  color: '#8b6a4a', speed: 1.2, hop: true },
    gorilla:    { h: 1.6,  color: '#3a3533', speed: 0.6 },
    kangaroo:   { h: 1.5,  color: '#b08060', speed: 1.8, hop: true },
    koala:      { h: 0.7,  color: '#9a9a9e', speed: 0.4 },
    panda:      { h: 1.2,  color: '#f0f0f0', speed: 0.5 },
  };
  var UNKNOWN_SPECIES = { h: 1.0, color: '#9a9a9a', speed: 1.0 };

  // ------------------------------------------------------------ small helpers
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function hashStr(s) { var h = 7; for (var i = 0; i < s.length; i++) h = (Math.imul(h, 31) + s.charCodeAt(i)) | 0; return h; }
  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smooth(x, a, b) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function ring(rng, rMin, rMax) {
    var r = Math.sqrt(rng() * (rMax * rMax - rMin * rMin) + rMin * rMin), a = rng() * Math.PI * 2;
    return [Math.sin(a) * r, Math.cos(a) * r];
  }
  function rgb(h) { h = h.replace('#', ''); return [parseInt(h.substr(0, 2), 16), parseInt(h.substr(2, 2), 16), parseInt(h.substr(4, 2), 16)]; }
  function mix(a, b, t) { return [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)]; }
  function css(c, alpha) {
    var s = (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0);
    return alpha === undefined ? 'rgb(' + s + ')' : 'rgba(' + s + ',' + alpha + ')';
  }
  function shade(h, amt) { return css(mix(rgb(h), amt < 0 ? [0, 0, 0] : [255, 255, 255], Math.abs(amt))); }
  function vary(h, rng, amt) { return shade(h, (rng() - 0.5) * 2 * (amt || 0.12)); }
  function newCanvas(w, h) { var c = document.createElement('canvas'); c.width = w; c.height = h; return c; }

  // Canvas shape helpers (own roundRect so older browsers work).
  function rrect(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath(); g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
  }
  function ell(g, x, y, rx, ry, fill) { g.beginPath(); g.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2); g.fillStyle = fill; g.fill(); }
  function tri(g, x1, y1, x2, y2, x3, y3, fill) { g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.lineTo(x3, y3); g.closePath(); g.fillStyle = fill; g.fill(); }
  function poly(g, pts, fill) { g.beginPath(); g.moveTo(pts[0], pts[1]); for (var i = 2; i < pts.length; i += 2) g.lineTo(pts[i], pts[i + 1]); g.closePath(); g.fillStyle = fill; g.fill(); }

  // A picture drawn in metres: w x h metres, origin top-left, ground at y = h.
  function sprite(w, h, draw) {
    var ppm = Math.min(64, 256 / Math.max(w, h));
    var c = newCanvas(Math.max(2, Math.round(w * ppm)), Math.max(2, Math.round(h * ppm)));
    var g = c.getContext('2d');
    g.scale(c.width / w, c.height / h);
    draw(g, w, h);
    return { img: c, w: w, h: h };
  }

  // ------------------------------------------------------------ pictures of things
  var DRAW = {
    pine: function (leaf) {
      return sprite(3.4, 6.4, function (g, w, h) {
        g.fillStyle = '#5a3d26'; g.fillRect(w / 2 - 0.15, h - 1.2, 0.3, 1.2);
        [[h - 0.9, 1.7, 2.6], [h - 2.4, 1.35, 2.4], [h - 3.8, 1.0, 2.3]].forEach(function (L) {
          tri(g, w / 2 - L[1], L[0], w / 2 + L[1], L[0], w / 2, L[0] - L[2], leaf);
          tri(g, w / 2, L[0], w / 2 + L[1], L[0], w / 2, L[0] - L[2], 'rgba(0,0,0,0.18)');
        });
      });
    },
    snowpine: function (leaf) {
      return sprite(3.0, 6.6, function (g, w, h) {
        g.fillStyle = '#4a3626'; g.fillRect(w / 2 - 0.14, h - 1.1, 0.28, 1.1);
        [[h - 0.8, 1.5, 2.7], [h - 2.3, 1.2, 2.5], [h - 3.8, 0.9, 2.5]].forEach(function (L) {
          tri(g, w / 2 - L[1], L[0], w / 2 + L[1], L[0], w / 2, L[0] - L[2], leaf);
          tri(g, w / 2, L[0], w / 2 + L[1], L[0], w / 2, L[0] - L[2], 'rgba(0,0,0,0.2)');
          tri(g, w / 2 - L[1] * 0.5, L[0] - L[2] * 0.5, w / 2 + L[1] * 0.5, L[0] - L[2] * 0.5, w / 2, L[0] - L[2], '#f2f6fa');
        });
      });
    },
    acacia: function (leaf) {
      return sprite(7, 4.2, function (g, w, h) {
        g.strokeStyle = '#5b4632'; g.lineCap = 'round';
        g.lineWidth = 0.28; g.beginPath(); g.moveTo(w / 2, h); g.lineTo(w / 2, 1.9); g.stroke();
        g.lineWidth = 0.14; g.beginPath(); g.moveTo(w / 2, 2.4); g.lineTo(w / 2 - 1.6, 1.3); g.moveTo(w / 2, 2.2); g.lineTo(w / 2 + 1.5, 1.2); g.stroke();
        ell(g, w / 2, 1.1, 3.3, 0.75, leaf);
        ell(g, w / 2, 1.4, 3.0, 0.4, 'rgba(0,0,0,0.15)');
      });
    },
    cactus: function (color, arms) {
      return sprite(2.2, 4, function (g, w, h) {
        g.fillStyle = color;
        rrect(g, w / 2 - 0.35, 0.3, 0.7, h - 0.3, 0.35); g.fill();
        if (arms & 1) { rrect(g, 0.25, 1.2, 0.4, 1.3, 0.2); g.fill(); g.fillRect(0.45, 2.15, 0.5, 0.35); }
        if (arms & 2) { rrect(g, w - 0.65, 0.9, 0.4, 1.2, 0.2); g.fill(); g.fillRect(w - 0.95, 1.75, 0.5, 0.35); }
        g.strokeStyle = 'rgba(0,0,0,0.18)'; g.lineWidth = 0.05;
        g.beginPath(); g.moveTo(w / 2 - 0.12, 0.5); g.lineTo(w / 2 - 0.12, h); g.moveTo(w / 2 + 0.12, 0.5); g.lineTo(w / 2 + 0.12, h); g.stroke();
      });
    },
    bush: function (color) {
      return sprite(1.7, 1.1, function (g) {
        ell(g, 0.5, 0.72, 0.45, 0.38, color); ell(g, 1.05, 0.6, 0.55, 0.5, color); ell(g, 1.3, 0.82, 0.38, 0.28, color);
        ell(g, 1.1, 0.85, 0.5, 0.22, 'rgba(0,0,0,0.15)');
      });
    },
    rock: function (color) {
      return sprite(1.4, 0.8, function (g) {
        poly(g, [0.05, 0.8, 0.2, 0.3, 0.6, 0.05, 1.1, 0.2, 1.35, 0.8], color);
        poly(g, [0.2, 0.32, 0.6, 0.06, 1.1, 0.2, 0.7, 0.35], 'rgba(255,255,255,0.15)');
      });
    },
    grass: function (color) {
      return sprite(0.6, 0.5, function (g, w, h) {
        g.strokeStyle = color; g.lineWidth = 0.04; g.lineCap = 'round'; g.beginPath();
        for (var i = 0; i < 7; i++) { var tx = 0.05 + i * 0.08; g.moveTo(w / 2, h); g.quadraticCurveTo(w / 2, h * 0.5, tx, 0.05 + (i % 3) * 0.08); }
        g.stroke();
      });
    },
    shrub: function (color) {
      return sprite(1, 0.6, function (g, w, h) {
        g.strokeStyle = color; g.lineWidth = 0.04; g.beginPath();
        for (var i = 0; i < 9; i++) { g.moveTo(w / 2, h); g.lineTo(0.08 + i * 0.105, 0.1 + (i % 2) * 0.15); }
        g.stroke(); ell(g, w / 2, 0.35, 0.35, 0.2, 'rgba(110,100,60,0.5)');
      });
    },
    dune: function (color) {
      return sprite(60, 6, function (g, w, h) {
        var gr = g.createLinearGradient(0, 0, w, 0);
        gr.addColorStop(0, shade(color, 0.12)); gr.addColorStop(0.55, color); gr.addColorStop(1, shade(color, -0.18));
        g.beginPath(); g.moveTo(0, h); g.quadraticCurveTo(w * 0.3, -h * 0.2, w * 0.55, h * 0.25); g.quadraticCurveTo(w * 0.8, h * 0.6, w, h);
        g.closePath(); g.fillStyle = gr; g.fill();
      });
    },
    hill: function (color, style, seed) {
      var rng = mulberry32(seed);
      return sprite(100, 45, function (g, w, h) {
        var pts = [0, h], i, n = 9;
        if (style === 'mesa') {
          var top = h * (0.25 + rng() * 0.2);
          pts.push(w * 0.12, top, w * 0.85, top + rng() * 3, w, h);
        } else {
          for (i = 1; i < n; i++) {
            var y = style === 'peaks' ? (i % 2 ? h * (0.05 + rng() * 0.3) : h * (0.4 + rng() * 0.2))
                                      : h * (0.25 + rng() * 0.35);
            pts.push((w * i) / n, y);
          }
          pts.push(w, h);
        }
        poly(g, pts, color);
        if (style === 'peaks') {                                  // snow caps
          for (i = 2; i + 3 < pts.length; i += 4) {
            var px = pts[i], py = pts[i + 1];
            tri(g, px - w * 0.035, py + h * 0.12, px + w * 0.035, py + h * 0.12, px, py, '#f4f8fb');
          }
        }
        var gr = g.createLinearGradient(0, 0, 0, h);
        gr.addColorStop(0, 'rgba(255,255,255,0)'); gr.addColorStop(1, 'rgba(0,0,0,0.18)');
        g.globalCompositeOperation = 'source-atop'; g.fillStyle = gr; g.fillRect(0, 0, w, h);
      });
    },
    patch: function (color) {
      return sprite(1, 1, function (g) { ell(g, 0.5, 0.5, 0.48, 0.48, color); });
    },
    cloud: function (color) {
      return sprite(40, 14, function (g) {
        [[10, 9, 8, 4.5], [18, 6.5, 9, 6], [27, 8, 9, 5], [33, 10, 6, 3.5], [20, 10, 14, 3.8]].forEach(function (p) { ell(g, p[0], p[1], p[2], p[3], color); });
        var gr = g.createLinearGradient(0, 0, 0, 14);
        gr.addColorStop(0, 'rgba(255,255,255,0.12)'); gr.addColorStop(1, 'rgba(0,0,0,0.18)');
        g.globalCompositeOperation = 'source-atop'; g.fillStyle = gr; g.fillRect(0, 0, 40, 14);
      });
    },
  };

  // ------------------------------------------------------------ biomes
  // Each biome: ground colours, hill style, and a function that places its objects.
  var BIOMES = {
    forest: {
      label: 'Forest', groundNear: '#3f6127', groundFar: '#5b7d3c', patch: ['#33521f', '#6a8e44'], hill: '#3a5a34', hillStyle: 'round',
      sprites: function (S) {
        ['#2f5a26', '#3a6b2c', '#28502a'].forEach(function (c, i) { S['tree' + i] = DRAW.pine(c); });
        S.bush = DRAW.bush('#3f6b2a'); S.rock = DRAW.rock('#7b7d78');
      },
      place: function (add, rng) {
        for (var i = 0; i < 340; i++) add('tree' + (i % 3), ring(rng, 7, 170), 0.8 + rng() * 0.9);
        for (i = 0; i < 160; i++) add('bush', ring(rng, 4, 110), 0.6 + rng() * 0.8);
        for (i = 0; i < 40; i++) add('rock', ring(rng, 5, 120), 0.5 + rng() * 1.2);
      },
    },
    savanna: {
      label: 'Savanna', groundNear: '#a38f4b', groundFar: '#c2ab62', patch: ['#8f7b3c', '#cdb870'], hill: '#8d7b58', hillStyle: 'mesa',
      sprites: function (S) {
        S.acacia0 = DRAW.acacia('#5e6b2c'); S.acacia1 = DRAW.acacia('#6b7633');
        S.grass0 = DRAW.grass('#b9a352'); S.grass1 = DRAW.grass('#9c8a40'); S.rock = DRAW.rock('#8a7560');
      },
      place: function (add, rng) {
        for (var i = 0; i < 48; i++) add('acacia' + (i % 2), ring(rng, 12, 200), 0.9 + rng() * 0.8);
        for (i = 0; i < 700; i++) add('grass' + (i % 2), ring(rng, 2.5, 60), 0.7 + rng() * 0.9);
        for (i = 0; i < 30; i++) add('rock', ring(rng, 6, 140), 0.5 + rng() * 1.2);
      },
    },
    desert: {
      label: 'Desert', groundNear: '#d6b273', groundFar: '#e9c98c', patch: ['#c49e62', '#f0d39c'], hill: '#b8845a', hillStyle: 'mesa',
      sprites: function (S) {
        S.cactus0 = DRAW.cactus('#4f7a3a', 3); S.cactus1 = DRAW.cactus('#5a8441', 1); S.cactus2 = DRAW.cactus('#47703a', 2);
        S.shrub = DRAW.shrub('#7a6a3c'); S.rock = DRAW.rock('#a0633e'); S.dune = DRAW.dune('#e2bf82');
      },
      place: function (add, rng) {
        for (var i = 0; i < 26; i++) add('dune', ring(rng, 70, 230), 0.6 + rng() * 0.9);
        for (i = 0; i < 75; i++) add('cactus' + (i % 3), ring(rng, 8, 160), 0.7 + rng() * 0.6);
        for (i = 0; i < 90; i++) add('shrub', ring(rng, 4, 120), 0.5 + rng() * 0.9);
        for (i = 0; i < 110; i++) add('rock', ring(rng, 5, 160), 0.4 + rng() * 1.3);
      },
    },
    snow: {
      label: 'Snowy taiga', groundNear: '#e4eaf0', groundFar: '#ffffff', patch: ['#cfd9e3', '#ffffff'], hill: '#9fb0c2', hillStyle: 'peaks',
      sprites: function (S) {
        S.tree0 = DRAW.snowpine('#1f3b2c'); S.tree1 = DRAW.snowpine('#24432f'); S.rock = DRAW.rock('#8c939a');
      },
      place: function (add, rng) {
        for (var i = 0; i < 280; i++) add('tree' + (i % 2), ring(rng, 7, 170), 0.8 + rng() * 1.0);
        for (i = 0; i < 40; i++) add('rock', ring(rng, 5, 130), 0.5 + rng() * 1.2);
      },
    },
  };

  var biomeKey = null, sprites = {}, objects = [];

  function buildWorld(key) {
    if (!BIOMES[key]) key = 'forest';
    biomeKey = key;
    var B = BIOMES[key], rng = mulberry32(hashStr(key));
    sprites = {};
    B.sprites(sprites);
    for (var i = 0; i < 3; i++) sprites['hill' + i] = DRAW.hill(B.hill, B.hillStyle, 11 + i);
    sprites.patch0 = DRAW.patch(B.patch[0]); sprites.patch1 = DRAW.patch(B.patch[1]);

    objects = [];
    function add(type, xz, s, extra) {
      var o = { type: type, x: xz[0], z: xz[1], s: s, dist: Math.hypot(xz[0], xz[1]), sink: 0, flat: false };
      if (extra) for (var k in extra) o[k] = extra[k];
      objects.push(o);
    }
    for (i = 0; i < 42; i++) {
      var a = (i / 42) * Math.PI * 2 + rng() * 0.1, r = 260 + rng() * 80;
      add('hill' + (i % 3), [Math.sin(a) * r, Math.cos(a) * r], 0.9 + rng() * 0.9, { sink: 2 });
    }
    for (i = 0; i < 450; i++) add('patch' + (i % 2), ring(rng, 2, 60), 0.5 + rng() * 1.8, { flat: true });
    B.place(add, rng);
    tintCache = {};
  }

  // ------------------------------------------------------------ canvas, camera, input
  var canvas = document.createElement('canvas');
  document.getElementById('app').appendChild(canvas);
  var ctx = canvas.getContext('2d');

  var forcedScale = params.has('scale') ? clamp(Number(params.get('scale')), 0.25, 2) : null;
  var renderScale = forcedScale || Math.min(window.devicePixelRatio || 1, 1.5);
  var W = 0, H = 0, F = 0, horizon = 0;

  var yaw = 0, pitch = 0.05, vfov = 1.05;          // radians
  var lastYaw = 0, idle = 0;

  function resize() {
    W = canvas.width = Math.round(innerWidth * renderScale);
    H = canvas.height = Math.round(innerHeight * renderScale);
    makePrecip();
  }
  addEventListener('resize', resize);

  var dragging = false, lastX = 0, lastY = 0;
  canvas.addEventListener('pointerdown', function (e) {
    dragging = true; lastX = e.clientX; lastY = e.clientY; idle = 0;
    if (canvas.setPointerCapture) canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', function (e) {
    if (!dragging) return;
    var k = vfov / innerHeight;                     // radians per CSS pixel
    yaw -= (e.clientX - lastX) * k;
    pitch = clamp(pitch + (e.clientY - lastY) * k, -0.5, 0.7);
    lastX = e.clientX; lastY = e.clientY; idle = 0;
  });
  function endDrag() { dragging = false; }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
  canvas.addEventListener('wheel', function (e) {
    e.preventDefault(); vfov = clamp(vfov + e.deltaY * 0.0008, 0.6, 1.5); idle = 0;
  }, { passive: false });
  addEventListener('keydown', function (e) {
    var step = 0.06;
    if (e.key === 'ArrowLeft') yaw -= step;
    else if (e.key === 'ArrowRight') yaw += step;
    else if (e.key === 'ArrowUp') pitch = clamp(pitch + step, -0.5, 0.7);
    else if (e.key === 'ArrowDown') pitch = clamp(pitch - step, -0.5, 0.7);
    else return;
    idle = 0;
  });

  // ------------------------------------------------------------ weather state
  var NIGHT_TOP = rgb('#050a18'), NIGHT_BOTTOM = rgb('#17213a'), SUNSET = rgb('#ff9a5a'), NIGHT_GROUND = rgb('#0a0f1e');
  var look = {};                 // colours and numbers derived from weather + time, used every frame
  var tintCache = {};

  function localHour() { var d = new Date(); return d.getHours() + d.getMinutes() / 60; }

  function applyWeather(w) {
    var p = WEATHER[w.condition] || WEATHER.clear;
    var hour = typeof w.hour === 'number' ? w.hour : localHour();
    var elev = Math.sin(((hour - 6) / 12) * Math.PI);
    var day = smooth(elev, -0.12, 0.25);
    var dusk = clamp(1 - Math.abs(elev - 0.05) / 0.2, 0, 1) * (p.sun > 0.3 ? 1 : 0.3);
    var B = BIOMES[biomeKey];

    var top = mix(rgb(p.top), NIGHT_TOP, 1 - day);
    var bottom = mix(mix(rgb(p.bottom), SUNSET, dusk * 0.5), NIGHT_BOTTOM, 1 - day);
    var dark = clamp((1 - day) * 0.72 + p.dim, 0, 0.85);

    look = {
      p: p, day: day, dark: dark,
      skyTop: css(top), skyBottom: css(bottom), fogRgb: bottom,
      groundNear: css(mix(rgb(B.groundNear), NIGHT_GROUND, dark)),
      groundFar: css(mix(mix(rgb(B.groundFar), NIGHT_GROUND, dark), bottom, 0.35)),
      fogNear: p.fog[0], fogFar: p.fog[1],
      sunAz: ((hour - 12) / 6) * (Math.PI / 2), sunEl: elev * 1.0,
      showSun: elev > -0.05 && p.sun >= 0.3, showMoon: elev < 0.05 && p.sun >= 0.3,
      stars: (1 - day) * (p.sun >= 0.3 ? 1 : 0.15),
      cloudImg: DRAW.cloud(css(mix(rgb(p.cloud), NIGHT_BOTTOM, (1 - day) * 0.8))).img,
      wind: (typeof w.wind_kmh === 'number' ? w.wind_kmh : 8) / 3.6,
    };
    rain.count = Math.min(p.rain || 0, rain.list.length);
    snow.count = Math.min(p.snow || 0, snow.list.length);
    tintCache = {};
  }

  // Picture darkened for night and blended into the fog colour, cached per fog level.
  function tinted(key, img, level) {
    if (level === 0 && look.dark < 0.02) return img;
    var ck = key + '|' + level, c = tintCache[ck];
    if (c) return c;
    c = newCanvas(img.width, img.height);
    var g = c.getContext('2d');
    g.drawImage(img, 0, 0);
    g.globalCompositeOperation = 'source-atop';
    if (look.dark > 0.02) { g.fillStyle = css(NIGHT_GROUND, look.dark); g.fillRect(0, 0, c.width, c.height); }
    if (level > 0) { g.fillStyle = css(look.fogRgb, level / (FOG_LEVELS - 1)); g.fillRect(0, 0, c.width, c.height); }
    tintCache[ck] = c;
    return c;
  }
  function fogAt(d) { return smooth(d, look.fogNear, look.fogFar); }

  // ------------------------------------------------------------ sky things: clouds, stars
  var clouds = [], starList = [];
  (function () {
    var rng = mulberry32(42), i;
    for (i = 0; i < 40; i++) clouds.push({ az: rng() * Math.PI * 2, el: 0.08 + rng() * 0.4, s: 0.7 + rng() * 0.8 });
    for (i = 0; i < 260; i++) starList.push({ az: rng() * Math.PI * 2, el: 0.05 + Math.pow(rng(), 0.7) * 1.2, b: 0.4 + rng() * 0.6 });
  })();

  // Screen position of a direction in the sky (azimuth, elevation), or null if behind.
  function skyPos(az, el) {
    var d = az - yaw;
    d = ((d + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
    if (Math.abs(d) > 1.35) return null;
    return [W / 2 + Math.tan(d) * F, horizon - Math.tan(el) * F];
  }

  // ------------------------------------------------------------ rain and snow (screen space)
  var rain = { list: [], count: 0 }, snow = { list: [], count: 0 };
  function makePrecip() {
    var i;
    rain.list = []; snow.list = [];
    for (i = 0; i < 800; i++) rain.list.push({ x: Math.random() * W, y: Math.random() * H, v: 0.9 + Math.random() * 0.5, l: 0.6 + Math.random() * 0.6 });
    for (i = 0; i < 420; i++) snow.list.push({ x: Math.random() * W, y: Math.random() * H, v: 0.6 + Math.random() * 0.8, r: 0.6 + Math.random() * 1.6, ph: Math.random() * 6.28 });
    if (look.p) { rain.count = Math.min(look.p.rain || 0, rain.list.length); snow.count = Math.min(look.p.snow || 0, snow.list.length); }
  }

  function drawRain(dt, shift) {
    var u = H / 600, slant = look.wind * 1.5 * u, i, d;
    ctx.strokeStyle = 'rgba(190,205,220,0.55)'; ctx.lineWidth = Math.max(1, u);
    ctx.beginPath();
    for (i = 0; i < rain.count; i++) {
      d = rain.list[i];
      d.y += d.v * 900 * u * dt; d.x += shift + slant * 20 * dt;
      if (d.y > H) { d.y -= H + 20 * u; d.x = Math.random() * W; }
      if (d.x < 0) d.x += W; else if (d.x > W) d.x -= W;
      ctx.moveTo(d.x, d.y); ctx.lineTo(d.x - slant * d.l, d.y - 22 * u * d.l);
    }
    ctx.stroke();
  }

  function drawSnow(dt, shift, t) {
    var u = H / 600, i, d;
    ctx.fillStyle = 'rgba(255,255,255,0.9)';
    ctx.beginPath();
    for (i = 0; i < snow.count; i++) {
      d = snow.list[i];
      d.y += d.v * 60 * u * dt; d.x += shift + (look.wind * 4 + Math.sin(t + d.ph) * 12) * u * dt;
      if (d.y > H) { d.y -= H + 10; d.x = Math.random() * W; }
      if (d.x < 0) d.x += W; else if (d.x > W) d.x -= W;
      ctx.moveTo(d.x + d.r * u, d.y); ctx.arc(d.x, d.y, d.r * u, 0, 6.283);
    }
    ctx.fill();
  }

  // ------------------------------------------------------------ animals
  var animals = [], showLabels = true;

  // Cartoon "plushie" placeholder, facing right. Replaced by a real PNG when one is given.
  function placeholder(species, color) {
    var c = newCanvas(256, 256), g = c.getContext('2d');
    var line = shade(color, -0.45);
    g.lineWidth = 6; g.strokeStyle = line; g.lineJoin = 'round';
    function blob(x, y, rx, ry, fill) { g.beginPath(); g.ellipse(x, y, rx, ry, 0, 0, Math.PI * 2); g.fillStyle = fill; g.fill(); g.stroke(); }
    function dot(x, y, r, fill) { g.beginPath(); g.arc(x, y, r, 0, 7); g.fillStyle = fill; g.fill(); }
    if (species === 'bird') {
      blob(120, 150, 70, 52, color);
      blob(105, 145, 42, 26, shade(color, -0.15));
      blob(175, 105, 38, 36, color);
      g.beginPath(); g.moveTo(208, 98); g.lineTo(240, 108); g.lineTo(208, 118); g.closePath(); g.fillStyle = '#f2b233'; g.fill(); g.stroke();
      dot(186, 98, 6, '#1b1b1b');
      g.fillStyle = line; g.fillRect(105, 198, 6, 30); g.fillRect(135, 198, 6, 30);
    } else {
      [62, 94, 146, 176].forEach(function (lx) { rrect(g, lx, 165, 24, 60, 11); g.fillStyle = shade(color, -0.15); g.fill(); g.stroke(); });
      blob(30, 128, 20, 14, shade(color, 0.1));
      blob(120, 150, 92, 58, color);
      blob(120, 168, 50, 26, shade(color, 0.35));
      [-24, 6].forEach(function (ex) {
        g.beginPath(); g.moveTo(190 + ex, 70); g.lineTo(200 + ex, 18); g.lineTo(220 + ex, 66); g.closePath(); g.fillStyle = color; g.fill(); g.stroke();
      });
      blob(198, 98, 46, 42, color);
      dot(214, 90, 7, '#1b1b1b'); dot(216, 88, 2.5, '#ffffff'); dot(240, 104, 6, '#2a1d17'); dot(206, 114, 9, 'rgba(255,120,140,0.45)');
      g.setLineDash([6, 7]); g.lineWidth = 2; g.beginPath(); g.ellipse(120, 150, 74, 42, 0, 3.6, 5.8); g.stroke(); g.setLineDash([]);
    }
    return c;
  }

  function flipped(img) {
    var c = newCanvas(img.width, img.height), g = c.getContext('2d');
    g.translate(img.width, 0); g.scale(-1, 1); g.drawImage(img, 0, 0);
    return c;
  }

  function loadImage(src) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = reject;
      img.src = src;
    });
  }

  function buildAnimals(list) {
    var rng = mulberry32(99);
    return Promise.all((list || []).map(function (entry) {
      var info = SPECIES[entry.species] || UNKNOWN_SPECIES;
      var fallback = function () { return { img: placeholder(entry.species, info.color), facesRight: true }; };
      if (!entry.image) return Promise.resolve(fallback());
      return loadImage(entry.image).then(function (img) {
        var c = newCanvas(img.naturalWidth, img.naturalHeight);   // copy to canvas so it can be tinted
        c.getContext('2d').drawImage(img, 0, 0);
        return { img: c, facesRight: entry.faces !== 'left' };
      }, function () { console.warn('could not load ' + entry.image + ', using placeholder'); return fallback(); });
    })).then(function (pics) {
      animals = [];
      (list || []).forEach(function (entry, n) {
        var info = SPECIES[entry.species] || UNKNOWN_SPECIES;
        var pic = pics[n], copies = clamp(entry.count | 0, 1, 8);
        var right = pic.facesRight ? pic.img : flipped(pic.img), left = pic.facesRight ? flipped(pic.img) : pic.img;
        for (var k = 0; k < copies; k++) {
          var xz = ring(rng, 6, 26);
          animals.push({
            key: entry.species + n, info: info, imgR: right, imgL: left, faceRight: rng() < 0.5,
            aspect: right.width / right.height, x: xz[0], z: xz[1], y: 0, tx: xz[0], tz: xz[1], vx: 0, vz: 0,
            pause: rng() * 3, phase: rng() * 6.28, alt: 5 + rng() * 7, radius: 8 + rng() * 14, angle: rng() * 6.28,
            label: k === 0 ? entry.species + ' ×' + entry.count : null, isAnimal: true,
          });
        }
      });
    });
  }

  function pickTarget(a) {   // stay in a ring around the viewer, never walk through the middle
    var ang = Math.atan2(a.x, a.z) + (Math.random() - 0.5) * 1.6, r = 5 + Math.random() * 23;
    a.tx = Math.sin(ang) * r; a.tz = Math.cos(ang) * r;
  }

  function updateAnimals(dt, t) {
    var rx = Math.cos(yaw), rz = -Math.sin(yaw);   // viewer's "right" direction on the ground
    animals.forEach(function (a) {
      var info = a.info, moving = false;
      if (info.fly) {
        a.angle += (info.speed / a.radius) * dt;
        var nx = Math.sin(a.angle) * a.radius, nz = Math.cos(a.angle) * a.radius;
        a.vx = nx - a.x; a.vz = nz - a.z; a.x = nx; a.z = nz;
        a.y = a.alt + Math.sin(t * 2 + a.phase) * 0.6;
        moving = true;
      } else if (a.pause > 0) {
        a.pause -= dt;
      } else {
        var dx = a.tx - a.x, dz = a.tz - a.z, dist = Math.hypot(dx, dz);
        if (dist < 0.3) { pickTarget(a); a.pause = 1 + Math.random() * 4; }
        else {
          var step = Math.min(dist, info.speed * dt);
          a.vx = dx / dist; a.vz = dz / dist; a.x += a.vx * step; a.z += a.vz * step; moving = true;
        }
      }
      if (!info.fly) a.y = moving ? Math.abs(Math.sin(t * info.speed * 5 + a.phase)) * (info.hop ? 0.35 : 0.05) * info.h : 0;
      var s = a.vx * rx + a.vz * rz;
      if (Math.abs(s) > 1e-4) a.faceRight = s > 0;
      a.dist = Math.hypot(a.x, a.z);
    });
  }

  // ------------------------------------------------------------ drawing a frame
  var visible = [];

  function drawSky(t) {
    var grad = ctx.createLinearGradient(0, horizon - F * 1.6, 0, horizon);
    grad.addColorStop(0, look.skyTop); grad.addColorStop(1, look.skyBottom);
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, W, Math.max(0, horizon) + 2);

    if (look.stars > 0.02) {
      ctx.fillStyle = '#ffffff';
      var sz = Math.max(1, H / 500);
      starList.forEach(function (s) {
        var p = skyPos(s.az, s.el);
        if (!p || p[1] > horizon) return;
        ctx.globalAlpha = look.stars * s.b * (0.75 + 0.25 * Math.sin(t * 2 + s.az * 10));
        ctx.fillRect(p[0], p[1], sz, sz);
      });
      ctx.globalAlpha = 1;
    }
    var body = look.showSun ? [look.sunAz, look.sunEl, '255,243,196', 0.035] : look.showMoon ? [look.sunAz + Math.PI, -look.sunEl, '230,236,245', 0.022] : null;
    if (body) {
      var sp = skyPos(body[0], body[1]);
      if (sp) {
        var r = F * body[3];
        var glow = ctx.createRadialGradient(sp[0], sp[1], r * 0.8, sp[0], sp[1], r * 5);
        glow.addColorStop(0, 'rgba(' + body[2] + ',0.45)'); glow.addColorStop(1, 'rgba(' + body[2] + ',0)');
        ctx.fillStyle = glow; ctx.fillRect(sp[0] - r * 5, sp[1] - r * 5, r * 10, r * 10);
        ctx.fillStyle = 'rgb(' + body[2] + ')'; ctx.beginPath(); ctx.arc(sp[0], sp[1], r, 0, 6.283); ctx.fill();
      }
    }
    var n = look.p.clouds, img = look.cloudImg;
    for (var i = 0; i < n; i++) {
      var c = clouds[i], p = skyPos(c.az, c.el);
      if (!p) continue;
      var w = F * 0.55 * c.s * (0.5 + c.el), h = w * (img.height / img.width);
      ctx.drawImage(img, p[0] - w / 2, p[1] - h / 2, w, h);
    }
  }

  function drawGround() {
    var grad = ctx.createLinearGradient(0, horizon, 0, H);
    grad.addColorStop(0, css(look.fogRgb));
    grad.addColorStop(0.08, look.groundFar);
    grad.addColorStop(1, look.groundNear);
    ctx.fillStyle = grad;
    ctx.fillRect(0, Math.max(0, horizon), W, H - Math.max(0, horizon));
  }

  function drawItem(o) {
    var scale = F / o.cz, groundY = horizon + EYE * scale, fog = fogAt(o.dist);
    if (fog > 0.97) return;
    var level = Math.round(fog * (FOG_LEVELS - 1));
    if (o.isAnimal) {
      var info = o.info, hp = info.h * scale, wp = hp * o.aspect;
      if (hp < 1) return;
      if (!info.fly) {
        ctx.fillStyle = 'rgba(0,0,0,' + (0.25 * (1 - fog)) + ')';
        ctx.beginPath(); ctx.ellipse(o.sx, groundY, wp * 0.4, Math.max(1, wp * 0.4 * Math.min(1, EYE / o.cz)), 0, 0, 6.283); ctx.fill();
      }
      var img = tinted(o.key + (o.faceRight ? 'R' : 'L'), o.faceRight ? o.imgR : o.imgL, level);
      var top = groundY - (o.y + info.h) * scale;
      ctx.drawImage(img, o.sx - wp / 2, top, wp, hp);
      if (o.label && showLabels && o.cz < 45) {
        var fs = Math.max(11, Math.min(16, 220 / o.cz)) * renderScale;
        ctx.font = '600 ' + fs + 'px system-ui, sans-serif';
        var tw = ctx.measureText(o.label).width + fs;
        ctx.fillStyle = 'rgba(12,16,22,0.6)'; rrect(ctx, o.sx - tw / 2, top - fs * 1.9, tw, fs * 1.5, fs * 0.75); ctx.fill();
        ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(o.label, o.sx, top - fs * 1.15);
      }
      return;
    }
    var sp = sprites[o.type], w = sp.w * o.s * scale, h = sp.h * o.s * scale;
    if (h < 0.7) return;
    var pic = tinted(o.type, sp.img, level);
    if (o.flat) {
      var hf = Math.max(0.5, w * Math.min(1, EYE / o.cz));
      ctx.globalAlpha = 0.55; ctx.drawImage(pic, o.sx - w / 2, groundY - hf / 2, w, hf); ctx.globalAlpha = 1;
    } else {
      ctx.drawImage(pic, o.sx - w / 2, groundY - h + o.sink * scale, w, h);
    }
  }

  function render(dt, t) {
    F = (H / 2) / Math.tan(vfov / 2);
    horizon = H / 2 + Math.tan(pitch) * F;
    var sinY = Math.sin(yaw), cosY = Math.cos(yaw);

    drawSky(t);
    drawGround();

    // Project everything, keep what is in view, draw far to near.
    visible.length = 0;
    function consider(o, halfW) {
      var cz = o.x * sinY + o.z * cosY;
      if (cz < 0.4) return;
      var cx = o.x * cosY - o.z * sinY, scale = F / cz, sx = W / 2 + cx * scale, hw = halfW * scale;
      if (sx + hw < 0 || sx - hw > W) return;
      o.cz = cz; o.sx = sx;
      visible.push(o);
    }
    for (var i = 0; i < objects.length; i++) { var o = objects[i]; consider(o, sprites[o.type].w * o.s / 2); }
    for (i = 0; i < animals.length; i++) consider(animals[i], animals[i].info.h * animals[i].aspect / 2);
    visible.sort(function (a, b) { return b.cz - a.cz; });
    for (i = 0; i < visible.length; i++) drawItem(visible[i]);

    var shift = -(yaw - lastYaw) * F;             // keep rain anchored to the world while turning
    lastYaw = yaw;
    if (rain.count) drawRain(dt, shift);
    if (snow.count) drawSnow(dt, shift, t);

    if (look.p.lightning) {
      flashTimer -= dt;
      if (flashTimer <= 0) { flash = 1; flashTimer = 4 + Math.random() * 8; }
    }
    if (flash > 0) {
      flash = Math.max(0, flash - dt * 5);
      var f = flash > 0.5 || (flash > 0.15 && flash < 0.3) ? flash : 0;   // double flicker
      if (f) { ctx.fillStyle = 'rgba(235,240,255,' + f * 0.55 + ')'; ctx.fillRect(0, 0, W, H); }
    }
  }
  var flash = 0, flashTimer = 3;

  // ------------------------------------------------------------ data, HUD, dev controls
  var data = BUILTIN_SCENE, dataNote = '';
  var overrides = {
    biome: params.get('biome') || '',
    condition: params.get('weather') || '',
    hour: params.has('hour') ? Number(params.get('hour')) : null,
  };
  var animalsKey = null;

  function loadScene() {
    return fetch(SCENE_URL, { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (json) { showError(''); dataNote = ''; return json; })
      .then(liveExtras)
      .catch(function (e) {
        if (location.protocol === 'file:') dataNote = ' (built-in copy)';
        else showError('Could not load ' + SCENE_URL + ' (' + e.message + '). Showing built-in fake scene.');
        return BUILTIN_SCENE;
      });
  }

  function getJson(url) {
    return fetch(url, { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
  }

  // Live mode only: recount animals since SINCE, and merge the Pod's real weather.
  function liveExtras(json) {
    var jobs = [];
    if (API && SINCE) {
      jobs.push(getJson(API + '/api/events?limit=10000&since_ms=' + SINCE +
                        (json.pod_id && json.pod_id !== 'no-pod-yet' ? '&pod_id=' + encodeURIComponent(json.pod_id) : ''))
        .then(function (rows) {
          var n = {};
          rows.forEach(function (e) { if (e.species && e.species !== 'unknown') n[e.species] = (n[e.species] || 0) + 1; });
          json.animals = Object.keys(n).sort(function (a, b) { return n[b] - n[a] || (a < b ? -1 : 1); })
            .map(function (sp) { return { species: sp, count: n[sp] }; });
        })
        .catch(function () {}));
    }
    if (WEATHER_URL) {
      jobs.push(getJson(WEATHER_URL).then(function (w) {
        if (!w || !w.condition) return;
        var stale = w.t_ms && Date.now() - w.t_ms > 3 * 3600e3;
        json.weather = {
          condition: w.condition, temp_c: w.temp_c, wind_kmh: w.wind_kmh,
          source: (w.source || 'Pod') + (w.place ? ' · ' + w.place : '') +
                  (w.location_source ? ' (' + w.location_source + ')' : '') + (stale ? ' · STALE' : '')
        };
      }).catch(function () {}));
    }
    return Promise.all(jobs).then(function () { return json; });
  }

  function effective() {
    var w = {}, k;
    for (k in data.weather || {}) w[k] = data.weather[k];
    if (overrides.condition) w.condition = overrides.condition;
    if (overrides.hour !== null) w.hour = overrides.hour;
    return { biome: overrides.biome || data.biome, weather: w };
  }

  function applyAll() {
    var eff = effective();
    if ((BIOMES[eff.biome] ? eff.biome : 'forest') !== biomeKey) buildWorld(eff.biome);
    applyWeather(eff.weather);
    updateHud(eff);
    var key = JSON.stringify(data.animals || []);
    if (key !== animalsKey) { animalsKey = key; return buildAnimals(data.animals); }
    return Promise.resolve();
  }

  function updateHud(eff) {
    var w = eff.weather, p = WEATHER[w.condition] || WEATHER.clear;
    var hour = typeof w.hour === 'number' ? w.hour : localHour();
    var mins = Math.round(hour * 60) % 1440;
    var hhmm = ('0' + Math.floor(mins / 60)).slice(-2) + ':' + ('0' + (mins % 60)).slice(-2);
    setText('hud-title', (data.pod_id || 'pod') + ' · ' + BIOMES[biomeKey].label);
    setText('hud-weather', p.label + (w.temp_c != null ? ' · ' + w.temp_c + '°C' : '') +
      (w.wind_kmh != null ? ' · wind ' + w.wind_kmh + ' km/h' : '') + ' · ' + hhmm);
    setText('hud-animals', ((data.animals || []).map(function (a) { return a.species + ' ×' + a.count; }).join(' · ') ||
      'No animals seen yet') + (SINCE && API ? '  (since ' + new Date(SINCE).toTimeString().slice(0, 5) + ')' : ''));
    var ov = overrides.biome || overrides.condition || overrides.hour !== null ? ' · dev override on' : '';
    setText('hud-src', 'biome: ' + (data.biome_source || '?') + ' · weather: ' + (w.source || '?') + dataNote + ov);
  }
  function setText(id, s) { document.getElementById(id).textContent = s; }
  function showError(msg) { var el = document.getElementById('error'); el.textContent = msg; el.hidden = !msg; }

  var $biome = document.getElementById('dev-biome'), $weather = document.getElementById('dev-weather');
  var $hour = document.getElementById('dev-hour'), $labels = document.getElementById('dev-labels');
  $biome.value = overrides.biome; $weather.value = overrides.condition;
  $hour.value = overrides.hour !== null ? overrides.hour : localHour();
  $biome.onchange = function () { overrides.biome = $biome.value; applyAll(); };
  $weather.onchange = function () { overrides.condition = $weather.value; applyAll(); };
  $hour.oninput = function () { overrides.hour = Number($hour.value); applyAll(); };
  $labels.onchange = function () { showLabels = $labels.checked; };

  // ------------------------------------------------------------ main loop
  // If frames are slow (e.g. software rendering on the Pi), draw fewer pixels.
  var frames = 0, slowTime = 0;
  function adapt(dt) {
    if (forcedScale) return;
    frames++; slowTime += dt;
    if (frames < 90) return;
    var avg = slowTime / frames;
    frames = 0; slowTime = 0;
    if (avg > 0.045 && renderScale > 0.5) { renderScale = Math.max(0.5, renderScale * 0.75); resize(); }
  }

  var lastT = 0, t = 0;
  function frame(now) {
    var dt = lastT ? Math.min((now - lastT) / 1000, 0.1) : 0.016;
    lastT = now; t += dt;
    idle += dt;
    if (idle > 8 && !dragging) yaw += 0.03 * dt;    // slow pan when nobody is touching it
    for (var i = 0; i < clouds.length; i++) clouds[i].az += look.wind * 0.0015 * dt;
    updateAnimals(dt, t);
    render(dt, t);
    adapt(dt);
    requestAnimationFrame(frame);
  }

  resize();
  loadScene().then(function (json) {
    data = json;
    return applyAll();
  }).then(function () {
    requestAnimationFrame(frame);
    setInterval(function () { loadScene().then(function (json) { data = json; applyAll(); }); }, REFRESH_MS);
  });
})();
