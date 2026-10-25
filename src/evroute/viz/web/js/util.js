/* EVRoute viewer -- util.js
 *
 * Small, dependency-free helpers shared by every module: DOM construction
 * (never innerHTML for data), numeric helpers, colour maths, theme tokens,
 * safe storage and canvas sizing.  Every file of the viewer is one IIFE that
 * adds its part to the shared namespace `window.EVR`; export.py concatenates
 * them in a fixed order (util, i18n, model, terrain, map, charts, anim,
 * panels, live, app).
 */
(function (EVR) {
  'use strict';

  const U = {};

  /* ------------------------------------------------------------------ DOM */
  U.$ = (sel, root) => (root || document).querySelector(sel);
  U.$$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /**
   * Create an element.  `attrs` keys: class, text, style (object), dataset
   * (object), on<Event> (listener) and any attribute; children are nodes or
   * strings (always inserted as text, never parsed as HTML).
   */
  U.el = function (tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'style' && typeof v === 'object') {
          for (const [sk, sv] of Object.entries(v)) {
            if (sk.startsWith('--')) node.style.setProperty(sk, sv);
            else node.style[sk] = sv;
          }
        } else if (k === 'dataset') Object.assign(node.dataset, v);
        else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else if (v === true) node.setAttribute(k, '');
        else node.setAttribute(k, String(v));
      }
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(typeof c === 'string' || typeof c === 'number' ? document.createTextNode(String(c)) : c);
    }
    return node;
  };

  /** Replace all children of `node`. */
  U.setChildren = function (node, ...children) {
    node.textContent = '';
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
  };

  /** Static SVG icon markup (trusted constant strings only). */
  U.svg = function (pathData, cls) {
    const ns = 'http://www.w3.org/2000/svg';
    const s = document.createElementNS(ns, 'svg');
    s.setAttribute('viewBox', '0 0 24 24');
    s.setAttribute('aria-hidden', 'true');
    if (cls) s.setAttribute('class', cls);
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', pathData);
    s.appendChild(p);
    return s;
  };

  /* -------------------------------------------------------------- numbers */
  U.clamp = (x, lo, hi) => (x < lo ? lo : x > hi ? hi : x);
  U.lerp = (a, b, t) => a + (b - a) * t;
  U.smoothstep = (e0, e1, x) => {
    const t = U.clamp((x - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  };
  U.isNum = (x) => typeof x === 'number' && isFinite(x);

  /** Largest index i with a[i] <= x (or -1); `a` sorted ascending. */
  U.bisectRight = function (a, x, lo, hi) {
    lo = lo || 0;
    hi = hi === undefined ? a.length : hi;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (a[mid] <= x) lo = mid + 1;
      else hi = mid;
    }
    return lo - 1;
  };

  /** "Nice" axis ticks covering [lo, hi] with about `n` steps. */
  U.niceTicks = function (lo, hi, n) {
    if (!(hi > lo)) return [lo];
    const raw = (hi - lo) / Math.max(1, n);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / mag;
    const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  };

  /**
   * Axis domain that *contains* [lo, hi] and ends on nice ticks:
   * {lo, hi, step, ticks}.  (niceTicks alone only returns the ticks inside
   * [lo, hi], so the outermost data could fall outside the axis.)
   */
  U.niceDomain = function (lo, hi, n) {
    if (!(hi > lo)) { const d = Math.abs(lo) * 0.1 || 1; lo -= d; hi += d; }
    const raw = (hi - lo) / Math.max(1, n);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / mag;
    const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
    const a = Math.floor(lo / step + 1e-9) * step, b = Math.ceil(hi / step - 1e-9) * step;
    const ticks = [];
    for (let k = 0, v = a; v <= b + step * 1e-6 && k < 200; k++, v = a + k * step) ticks.push(+v.toFixed(10));
    return { lo: a, hi: b, step, ticks };
  };

  /* --------------------------------------------------------------- colour */
  const _rgbCache = new Map();
  /** '#rrggbb' | '#rgb' | 'rgb(a)(...)' -> [r, g, b, a] (0-255, alpha 0-1). */
  U.parseColor = function (c) {
    if (_rgbCache.has(c)) return _rgbCache.get(c);
    let out = [128, 128, 128, 1];
    const s = String(c).trim();
    if (s[0] === '#') {
      let h = s.slice(1);
      if (h.length === 3) h = h.split('').map((x) => x + x).join('');
      const v = parseInt(h.slice(0, 6), 16);
      out = [(v >> 16) & 255, (v >> 8) & 255, v & 255, h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1];
    } else {
      const m = s.match(/rgba?\(([^)]+)\)/);
      if (m) {
        const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
        out = [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
      }
    }
    _rgbCache.set(c, out);
    return out;
  };
  U.rgba = function (c, a) {
    const p = U.parseColor(c);
    return `rgba(${p[0]},${p[1]},${p[2]},${a === undefined ? p[3] : a})`;
  };
  U.mix = function (c1, c2, t) {
    const a = U.parseColor(c1), b = U.parseColor(c2);
    return `rgb(${Math.round(U.lerp(a[0], b[0], t))},${Math.round(U.lerp(a[1], b[1], t))},${Math.round(U.lerp(a[2], b[2], t))})`;
  };

  /** Viridis colormap (van der Walt & Smith, matplotlib 2.0): 10 anchors, linear in between. */
  const VIRIDIS = [
    [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142],
    [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37],
  ];
  U.viridis = function (t) {
    t = U.clamp(t, 0, 1) * (VIRIDIS.length - 1);
    const i = Math.min(VIRIDIS.length - 2, Math.floor(t));
    const f = t - i, a = VIRIDIS[i], b = VIRIDIS[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  };

  /** Battery colour: green above 50 %, amber around 30 %, red at 10 % and below. */
  U.socColor = function (soc, theme) {
    const hi = theme.socHigh, mid = theme.socMid, lo = theme.socLow;
    if (soc >= 0.5) return hi;
    if (soc >= 0.3) return U.mix(mid, hi, (soc - 0.3) / 0.2);
    if (soc >= 0.1) return U.mix(lo, mid, (soc - 0.1) / 0.2);
    return lo;
  };

  /* ---------------------------------------------------------------- theme */
  const TOKENS = {
    bg: '--bg', surface: '--surface', surface2: '--surface-2', surface3: '--surface-3',
    border: '--border', borderStrong: '--border-strong',
    text: '--text', text2: '--text-2', text3: '--text-3', accent: '--accent',
    ok: '--ok', warn: '--warn', bad: '--bad',
    cDrive: '--c-drive', cQueue: '--c-queue', cSetup: '--c-setup', cCharge: '--c-charge',
    mapWater: '--map-water', mapLand: '--map-land',
    roadCasing: '--road-casing', roadMotorway: '--road-motorway', roadTrunk: '--road-trunk',
    roadPrimary: '--road-primary', roadMinor: '--road-minor',
    label: '--label', labelMinor: '--label-minor', labelHalo: '--label-halo',
    stAC: '--st-ac', stDC: '--st-dc', stHPC: '--st-hpc', stStroke: '--st-stroke',
    routeCasing: '--route-casing', routeDim: '--route-dim', pinA: '--pin-a', pinB: '--pin-b',
    chartGrid: '--chart-grid', chartAxis: '--chart-axis', chartText: '--chart-text',
    elevFill: '--elev-fill', elevLine: '--elev-line', reserve: '--reserve', crosshair: '--crosshair',
    socHigh: '--soc-high', socMid: '--soc-mid', socLow: '--soc-low', font: '--font',
  };
  /** Snapshot of the current theme tokens for canvas renderers. */
  U.readTheme = function () {
    const cs = getComputedStyle(document.documentElement);
    const t = {};
    for (const [k, v] of Object.entries(TOKENS)) t[k] = cs.getPropertyValue(v).trim();
    t.routeDim = parseFloat(t.routeDim) || 0.3;
    t.dark = document.documentElement.getAttribute('data-theme') === 'dark';
    t.font = t.font || 'system-ui, sans-serif';
    return t;
  };

  /* --------------------------------------------------------------- misc */
  U.prefersReducedMotion = function () {
    try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
  };
  U.storage = {
    get(key, dflt) {
      try { const v = window.localStorage.getItem(key); return v === null ? dflt : v; } catch (e) { return dflt; }
    },
    set(key, value) {
      try { window.localStorage.setItem(key, value); } catch (e) { /* private mode / blocked */ }
    },
  };
  U.dpr = () => Math.min(3, Math.max(1, window.devicePixelRatio || 1));

  /**
   * Size a canvas to its CSS box times devicePixelRatio; returns
   * {w, h, dpr} in CSS pixels and a context already scaled to CSS pixels.
   */
  U.fitCanvas = function (canvas) {
    const r = canvas.getBoundingClientRect();
    const dpr = U.dpr();
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw;
      canvas.height = ph;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h, dpr };
  };

  /** Run `fn` at most once per animation frame. */
  U.rafThrottle = function (fn) {
    let queued = false, lastArgs = null;
    return function (...args) {
      lastArgs = args;
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; fn(...lastArgs); });
    };
  };

  U.debounce = function (fn, ms) {
    let t = 0;
    return function (...args) { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  };

  /** Rounded rectangle path (works where ctx.roundRect is missing). */
  U.roundRect = function (ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };

  /** Draw a lightning bolt centred at (x, y) with height `s`. */
  U.bolt = function (ctx, x, y, s) {
    const k = s / 20;
    ctx.beginPath();
    ctx.moveTo(x + 1.2 * k, y - 10 * k);
    ctx.lineTo(x - 6.5 * k, y + 1.4 * k);
    ctx.lineTo(x - 0.9 * k, y + 1.4 * k);
    ctx.lineTo(x - 2.2 * k, y + 10 * k);
    ctx.lineTo(x + 6.5 * k, y - 2.2 * k);
    ctx.lineTo(x + 0.6 * k, y - 2.2 * k);
    ctx.closePath();
  };

  EVR.util = U;
})(window.EVR = window.EVR || {});
