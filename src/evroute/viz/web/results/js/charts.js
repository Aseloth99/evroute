/* EVRoute results panel -- charts.js
 *
 * A small, dependency-free SVG chart kit.  Every chart is drawn in CSS
 * pixels into an <svg> sized to its container (redrawn on resize), coloured
 * only through CSS custom properties (so switching the theme needs no
 * redraw) and made interactive through one mechanism: the chart registers
 * *targets* (an x position, a point or a rectangle, each with tooltip rows);
 * the pointer activates the nearest target and the keyboard walks through
 * them (arrow keys when the chart has focus), so every value is reachable
 * without a mouse.
 *
 *   lines     line chart, linear or log axes, confidence bands, end labels
 *   hbars     horizontal (stacked) bars with value labels
 *   columns   grouped vertical columns around a zero baseline
 *   boxes     box plot with the individual observations as a strip
 *   scatter   scatter / Pareto front with size encoding and reference marks
 *   dots      Wilkinson dot plot (a histogram whose units are visible)
 *   dumbbell  two values per category joined by a bar
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.RI;
  const NS = 'http://www.w3.org/2000/svg';
  const RC = {};
  const finite = (x) => typeof x === 'number' && isFinite(x);

  /* ------------------------------------------------------------ helpers */
  function S(tag, attrs, ...kids) {
    const n = document.createElementNS(NS, tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === 'style' && typeof v === 'object') {
          for (const [sk, sv] of Object.entries(v)) if (sv !== undefined && sv !== null) n.style.setProperty(sk, sv);
        } else if (k === 'text') n.textContent = v;
        else n.setAttribute(k, String(v));
      }
    }
    for (const c of kids.flat()) if (c) n.appendChild(c);
    return n;
  }
  RC.S = S;

  let _ctx = null, _font = null;
  /** Width of `text` in CSS pixels for the panel font. */
  function textW(text, size, weight) {
    if (!_ctx) _ctx = document.createElement('canvas').getContext('2d');
    if (!_font) _font = getComputedStyle(document.documentElement).getPropertyValue('--font').trim() || 'system-ui, sans-serif';
    _ctx.font = `${weight || 500} ${size || 11}px ${_font}`;
    return _ctx.measureText(String(text)).width;
  }
  RC.textW = textW;

  function reduced() { return U.prefersReducedMotion(); }

  /* ------------------------------------------------------------- scales */
  function linear(d0, d1, r0, r1) {
    const k = (r1 - r0) / ((d1 - d0) || 1);
    const f = (v) => r0 + (v - d0) * k;
    f.invert = (p) => d0 + (p - r0) / k;
    return f;
  }
  function logScale(d0, d1, r0, r1) {
    const l0 = Math.log10(d0), l1 = Math.log10(d1);
    const k = (r1 - r0) / ((l1 - l0) || 1);
    const f = (v) => r0 + (Math.log10(Math.max(v, 1e-300)) - l0) * k;
    f.invert = (p) => Math.pow(10, l0 + (p - r0) / k);
    return f;
  }

  /**
   * Log axis: the data range padded by a few percent of a decade, ticks on
   * 1-2-5 (narrow ranges), 1-3 or powers of ten inside it; at least two
   * ticks are kept by widening the domain to the nearest ones.
   */
  function logDomain(lo, hi, maxTicks) {
    lo = Math.max(lo, 1e-12);
    if (!(hi > lo)) { hi = lo * 3; lo = lo / 3; }
    const l0 = Math.log10(lo), l1 = Math.log10(hi);
    const pad = Math.max(0.04, (l1 - l0) * 0.06);
    let d0 = Math.pow(10, l0 - pad), d1 = Math.pow(10, l1 + pad);
    const a = Math.floor(l0 - pad) - 1, b = Math.ceil(l1 + pad) + 1;
    for (const mult of [[1, 2, 5], [1, 3], [1]]) {
      const all = [];
      for (let e = a; e <= b; e++) for (const m of mult) all.push(+(m * Math.pow(10, e)).toPrecision(6));
      all.sort((x, y) => x - y);
      let inside = all.filter((t) => t >= d0 * (1 - 1e-9) && t <= d1 * (1 + 1e-9));
      if (inside.length < 2) {
        const below = all.filter((t) => t < d0), above = all.filter((t) => t > d1);
        if (!inside.length && below.length) { d0 = below[below.length - 1]; }
        if (above.length) d1 = above[0];
        inside = all.filter((t) => t >= d0 * (1 - 1e-9) && t <= d1 * (1 + 1e-9));
      }
      if (inside.length <= (maxTicks || 7) || mult.length === 1) return { lo: d0, hi: d1, ticks: inside };
    }
    return { lo: d0, hi: d1, ticks: [] };
  }
  RC.logDomain = logDomain;

  function linDomain(lo, hi, n, zero) {
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    return U.niceDomain(lo, hi, n);
  }

  /** Decimals that a tick step needs (0.5 -> 1, 0.25 -> 2, 5 -> 0). */
  function dec(step) {
    if (!(step > 0)) return 0;
    for (let d = 0; d < 6; d++) if (Math.abs(Math.round(step * Math.pow(10, d)) - step * Math.pow(10, d)) < 1e-6) return d;
    return 6;
  }
  RC.dec = dec;

  /** Minute axis with ticks on whole minutes / hours. */
  function timeDomain(lo, hi, n, zero) {
    if (zero) lo = Math.min(lo, 0);
    const steps = [1, 2, 5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440];
    const span = Math.max(hi - lo, 1);
    const step = steps.find((st) => span / st <= Math.max(2, n)) || 1440;
    const a = Math.floor(lo / step + 1e-9) * step, b = Math.ceil(hi / step - 1e-9) * step;
    const ticks = [];
    for (let v = a; v <= b + 1e-6; v += step) ticks.push(v);
    return { lo: a, hi: Math.max(b, a + step), step, ticks };
  }

  function makeAxis(spec, lo, hi, r0, r1, nTicks) {
    if (spec.domain) { lo = spec.domain[0]; hi = spec.domain[1]; }
    let ax;
    if (spec.log) {
      const d = logDomain(lo, hi, nTicks + 1);
      ax = { scale: logScale(d.lo, d.hi, r0, r1), ticks: d.ticks, lo: d.lo, hi: d.hi, log: true };
    } else if (spec.ticks) {
      ax = { scale: linear(spec.domain ? lo : Math.min(lo, spec.ticks[0]), spec.domain ? hi : Math.max(hi, spec.ticks[spec.ticks.length - 1]), r0, r1),
        ticks: spec.ticks, lo, hi, step: spec.ticks.length > 1 ? Math.abs(spec.ticks[1] - spec.ticks[0]) : 1 };
    } else if (spec.time) {
      const d = timeDomain(lo, hi, nTicks, spec.zero);
      ax = { scale: linear(d.lo, d.hi, r0, r1), ticks: d.ticks, lo: d.lo, hi: d.hi, step: d.step };
    } else {
      const d = linDomain(lo, hi, nTicks, spec.zero);
      ax = { scale: linear(d.lo, d.hi, r0, r1), ticks: d.ticks, lo: d.lo, hi: d.hi, step: d.step };
    }
    const user = spec.fmt;
    ax.fmt = (v) => (user ? user(v, ax.step) : (ax.log ? I.compact(v) : I.num(v, dec(ax.step))));
    return ax;
  }

  /* ------------------------------------------------------------- mount */
  /**
   * Mount a chart into `host`.  `opts.draw(ctx)` paints into ctx.svg and
   * registers hover targets with ctx.add(target); it is called again on
   * every resize.  Targets:
   *   {kind: 'x',    x, y0, y1, ax, ay, tip, dots: [{x, y, color}]}
   *   {kind: 'pt',   x, y, r, tip, el?}
   *   {kind: 'rect', x, y, w, h, ax?, ay?, tip, el?}
   * with tip = {head, rows: [[color, label, value, key?]]}.
   */
  RC.mount = function (host, opts) {
    host.classList.add('rc');
    const svg = S('svg', { class: 'rc-svg', role: 'img', tabindex: '0', focusable: 'true' });
    const tip = U.el('div', { class: 'rc-tip', hidden: true, 'aria-hidden': 'true' });
    const live = U.el('div', { class: 'sr-only', 'aria-live': 'polite' });
    host.replaceChildren(svg, tip, live);
    const st = { host, svg, tip, targets: [], active: -1, w: 0, h: 0, first: true };

    function render() {
      const w = Math.round(host.clientWidth);
      if (!w) return;
      const h = Math.round(typeof opts.height === 'function' ? opts.height(w) : (opts.height || 280));
      st.w = w; st.h = h;
      svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      svg.setAttribute('width', w);
      svg.setAttribute('height', h);
      svg.setAttribute('aria-label', opts.aria ? (typeof opts.aria === 'function' ? opts.aria() : opts.aria) : '');
      svg.replaceChildren();
      st.targets = [];
      st.active = -1;
      tip.hidden = true;
      const overlay = S('g', { class: 'rc-overlay', 'pointer-events': 'none' });
      const ctx = { svg, w, h, narrow: w < 520, add: (t) => st.targets.push(t), overlay };
      if (st.first && !reduced()) svg.classList.add('rc-enter');
      opts.draw(ctx);
      svg.appendChild(overlay);
      st.overlay = overlay;
      if (st.first) {
        st.first = false;
        setTimeout(() => svg.classList.remove('rc-enter'), 1100);
      }
    }

    function local(ev) {
      const r = svg.getBoundingClientRect();
      return { x: (ev.clientX - r.left) * (st.w / (r.width || st.w)), y: (ev.clientY - r.top) * (st.h / (r.height || st.h)) };
    }

    function hit(p) {
      let best = -1, bd = Infinity;
      st.targets.forEach((t, i) => {
        if (t.kind !== 'pt') return;
        const d = Math.hypot(t.x - p.x, t.y - p.y);
        const reach = Math.max(13, (t.r || 4) + 9);
        if (d <= reach && d < bd) { bd = d; best = i; }
      });
      if (best >= 0) return best;
      for (let i = st.targets.length - 1; i >= 0; i--) {
        const t = st.targets[i];
        if (t.kind === 'rect' && p.x >= t.x - 2 && p.x <= t.x + t.w + 2 && p.y >= t.y - 2 && p.y <= t.y + t.h + 2) return i;
      }
      st.targets.forEach((t, i) => {
        if (t.kind !== 'x') return;
        if (p.y < t.y0 - 12 || p.y > t.y1 + 12 || p.x < t.px0 - 12 || p.x > t.px1 + 12) return;
        const d = Math.abs(t.x - p.x);
        if (d < bd) { bd = d; best = i; }
      });
      return best;
    }

    function activate(i) {
      if (i === st.active) return;
      const prev = st.targets[st.active];
      if (prev && prev.el) prev.el.classList.remove('hl');
      if (prev && prev.onLeave) prev.onLeave();
      st.active = i;
      st.overlay.replaceChildren();
      const t = st.targets[i];
      if (!t) { tip.hidden = true; return; }
      if (t.el) t.el.classList.add('hl');
      if (t.onEnter) t.onEnter();
      if (t.kind === 'x') {
        st.overlay.appendChild(S('line', { class: 'rc-cross', x1: Math.round(t.x) + 0.5, x2: Math.round(t.x) + 0.5, y1: t.y0, y2: t.y1 }));
        for (const d of t.dots || []) {
          st.overlay.appendChild(S('circle', { class: 'rc-hidot', cx: d.x, cy: d.y, r: 4.6, style: { fill: d.color } }));
        }
      } else if (t.kind === 'pt') {
        st.overlay.appendChild(S('circle', { class: 'rc-ring', cx: t.x, cy: t.y, r: (t.r || 4) + 4 }));
      }
      showTip(t);
    }

    function showTip(t) {
      const d = t.tip;
      if (!d) { tip.hidden = true; return; }
      const rows = (d.rows || []).filter(Boolean).map(([c, label, value, kind]) => U.el('div', { class: 'tt-row' },
        U.el('i', { class: `tt-key tt-${kind || 'line'}`, style: { '--c': c || 'transparent' } }),
        U.el('span', { text: label }), U.el('b', { text: value })));
      tip.replaceChildren(...[
        d.head ? U.el('div', { class: 'tt-head', text: d.head }) : null,
        d.sub ? U.el('div', { class: 'tt-sub', text: d.sub }) : null,
        ...rows].filter(Boolean));
      tip.hidden = false;
      live.textContent = [d.head, ...(d.rows || []).filter(Boolean).map((r) => `${r[1]}: ${r[2]}`)].filter(Boolean).join(', ');
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      const ax = t.ax !== undefined ? t.ax : (t.kind === 'rect' ? t.x + t.w : t.x);
      const ay = t.ay !== undefined ? t.ay : (t.kind === 'rect' ? t.y + t.h / 2 : t.y);
      let x = ax + 16;
      if (x + tw > st.w - 2) x = ax - tw - 16;
      if (x < 2) x = Math.max(2, Math.min(st.w - tw - 2, ax - tw / 2));
      let y = ay - th / 2;
      y = U.clamp(y, -6, Math.max(-6, st.h - th + 6));
      tip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
    }

    svg.addEventListener('pointermove', (ev) => activate(hit(local(ev))));
    svg.addEventListener('pointerdown', (ev) => activate(hit(local(ev))));
    svg.addEventListener('pointerleave', (ev) => { if (ev.pointerType !== 'touch') activate(-1); });
    svg.addEventListener('focus', () => { if (st.active < 0 && st.targets.length) activate(0); });
    svg.addEventListener('blur', () => activate(-1));
    svg.addEventListener('keydown', (ev) => {
      const n = st.targets.length;
      if (!n) return;
      let i = st.active;
      if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') i = (i + 1) % n;
      else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') i = (i - 1 + n) % n;
      else if (ev.key === 'Home') i = 0;
      else if (ev.key === 'End') i = n - 1;
      else if (ev.key === 'Escape') i = -1;
      else return;
      ev.preventDefault();
      activate(i);
    });

    let queued = false;
    const ro = new ResizeObserver(() => {
      if (queued || Math.round(host.clientWidth) === st.w) return;
      queued = true;
      requestAnimationFrame(() => { queued = false; render(); });
    });
    ro.observe(host);
    st.render = render;
    st.destroy = () => ro.disconnect();
    render();
    return st;
  };

  /* --------------------------------------------------------- axis drawing */
  function gridY(g, ticks, Y, x0, x1, fmt) {
    for (const v of ticks) {
      const y = Math.round(Y(v)) + 0.5;
      g.appendChild(S('line', { class: 'rc-grid', x1: x0, x2: x1, y1: y, y2: y }));
      g.appendChild(S('text', { class: 'rc-tick', x: x0 - 8, y, 'text-anchor': 'end', 'dominant-baseline': 'central', text: fmt(v) }));
    }
  }
  function ticksX(g, ticks, X, y1, fmt, grid, y0) {
    let last = -Infinity;
    for (const v of ticks.slice().sort((a, b) => X(a) - X(b))) {
      const x = Math.round(X(v)) + 0.5;
      const label = fmt(v);
      const w = textW(label);
      if (grid) g.appendChild(S('line', { class: 'rc-grid rc-grid-v', x1: x, x2: x, y1: y0, y2: y1 }));
      g.appendChild(S('line', { class: 'rc-tickmark', x1: x, x2: x, y1: y1, y2: y1 + 4 }));
      if (x - w / 2 < last + 6) continue;   // skip a label that would collide
      g.appendChild(S('text', { class: 'rc-tick', x, y: y1 + 17, 'text-anchor': 'middle', text: label }));
      last = x + w / 2;
    }
  }
  function baseline(g, x0, x1, y) {
    g.appendChild(S('line', { class: 'rc-axis', x1: x0, x2: x1, y1: Math.round(y) + 0.5, y2: Math.round(y) + 0.5 }));
  }
  function titleY(g, text, x, y) {
    if (text) g.appendChild(S('text', { class: 'rc-title', x, y, 'text-anchor': 'start', text }));
  }
  function titleX(g, text, x, y) {
    if (text) g.appendChild(S('text', { class: 'rc-title', x, y, 'text-anchor': 'end', text }));
  }
  function maxW(labels, size, weight) {
    let m = 0;
    for (const l of labels) m = Math.max(m, textW(l, size, weight));
    return m;
  }

  /** Path through points, broken at nulls. */
  function pathD(pts) {
    let d = '', pen = false;
    for (const p of pts) {
      if (!p) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
      pen = true;
    }
    return d;
  }

  /** Bar with a 4-px rounded data end (right for horizontal, top for vertical). */
  function barPath(x, y, w, h, dir, r) {
    r = Math.max(0, Math.min(r === undefined ? 4 : r, Math.abs(dir === 'h' ? w : h) / 2, Math.abs(dir === 'h' ? h : w) / 2));
    if (dir === 'h') {
      if (w <= 0) return '';
      return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
    }
    if (dir === 'up') {
      return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
    }
    return `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`;
  }
  RC.barPath = barPath;

  /* ================================================================ lines */
  /**
   * spec: {height, aria, x: {log, label, fmt, tipFmt, domain, ticks, grid},
   *        y: {log, label, fmt, tipFmt, zero, domain, floor},
   *        series: [{key, label, color, dash, band, points: [{x, y, lo, hi, tip}]}],
   *        hidden: Set, head: (x) => string, endLabels: bool, extraRows: (x) => rows}
   */
  RC.lines = function (host, spec) {
    return RC.mount(host, {
      height: spec.height || ((w) => (w < 520 ? 250 : 300)),
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const hidden = spec.hidden || new Set();
        const series = spec.series.filter((s) => !hidden.has(s.key) && s.points.some((p) => finite(p.y)));
        const floor = spec.y.floor;
        const yv = (v) => (spec.y.log && floor !== undefined && finite(v) && v < floor ? floor : v);
        let xs = [], ys = [];
        for (const s of series) {
          for (const p of s.points) {
            if (!finite(p.y)) continue;
            xs.push(p.x);
            ys.push(yv(p.y));
            if (s.band && finite(p.lo)) ys.push(yv(p.lo));
            if (s.band && finite(p.hi)) ys.push(yv(p.hi));
          }
        }
        if (!xs.length) {
          svg.appendChild(S('text', { class: 'rc-empty', x: w / 2, y: h / 2, 'text-anchor': 'middle', text: I.t('no_data') }));
          return;
        }
        if (spec.y.log) ys = ys.filter((v) => v > 0);
        const xlo = Math.min(...xs), xhi = Math.max(...xs);
        const ylo = Math.min(...ys), yhi = Math.max(...ys);
        const top = 30, bottom = 40;
        const y1 = h - bottom, y0 = top;
        const yAxis = makeAxis(spec.y, ylo, yhi, y1, y0, Math.max(3, Math.floor((y1 - y0) / 44)));
        const yfmt = yAxis.fmt;
        const left = Math.max(30, maxW(yAxis.ticks.map(yfmt)) + 14);
        // end labels need room on the right
        const endLabels = spec.endLabels !== false && series.length <= 6 && !ctx.narrow;
        const right = endLabels ? Math.min(150, maxW(series.map((s) => s.label), 11.5, 600) + 22) : 18;
        const x0 = left, x1 = w - right;
        const xAxis = makeAxis(spec.x, xlo, xhi, spec.x.reverse ? x1 : x0, spec.x.reverse ? x0 : x1, Math.max(3, Math.floor((x1 - x0) / 80)));
        const X = xAxis.scale, Y = yAxis.scale;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        gridY(g, yAxis.ticks, Y, x0, x1, yfmt);
        ticksX(g, xAxis.ticks, X, y1, xAxis.fmt, spec.x.grid, y0);
        baseline(g, x0, x1, y1);
        titleY(g, spec.y.label, 2, 13);
        titleX(g, spec.x.label, x1, h - 4);
        for (const r of spec.refLines || []) {
          const y = Math.round(Y(r.y)) + 0.5;
          g.appendChild(S('line', { class: 'rc-ref', x1: x0, x2: x1, y1: y, y2: y }));
          if (r.label) g.appendChild(S('text', { class: 'rc-ref-label', x: x0 + 6, y: y - 5, text: r.label }));
        }

        const plot = S('g', { class: 'rc-plot' });
        svg.appendChild(plot);
        const clipId = `c${Math.random().toString(36).slice(2, 9)}`;
        plot.appendChild(S('clipPath', { id: clipId }, S('rect', { x: x0 - 6, y: y0 - 8, width: x1 - x0 + 12, height: y1 - y0 + 14 })));
        const markers = [];
        for (const s of series) {
          const sg = S('g', { class: 'rc-series', 'data-k': s.key, 'clip-path': `url(#${clipId})` });
          plot.appendChild(sg);
          if (s.band) {
            const up = [], dn = [];
            for (const p of s.points) {
              if (!finite(p.lo) || !finite(p.hi)) continue;
              up.push([X(p.x), Y(yv(p.hi))]);
              dn.push([X(p.x), Y(yv(p.lo))]);
            }
            if (up.length > 1) {
              const d = pathD(up) + 'L' + dn.reverse().map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join('L') + 'Z';
              sg.appendChild(S('path', { class: `rc-band${s.band === 'focus' ? ' rc-band-focus' : ''}`, d, style: { fill: s.color } }));
            }
          }
          const pts = s.points.map((p) => (finite(p.y) ? [X(p.x), Y(yv(p.y))] : null));
          sg.appendChild(S('path', { class: `rc-line${s.dash ? ' rc-dash' : ''}`, d: pathD(pts), pathLength: s.dash ? null : '1', style: { stroke: s.color } }));
          const showMarkers = s.markers !== false && s.points.length <= 40;
          s.points.forEach((p) => {
            if (!finite(p.y)) return;
            const hollow = p.hollow || (spec.y.log && floor !== undefined && p.y < floor);
            if (showMarkers || hollow) {
              sg.appendChild(S('circle', { class: `rc-dot${hollow ? ' rc-hollow' : ''}`, cx: X(p.x), cy: Y(yv(p.y)), r: hollow ? 3.6 : 3.4, style: { '--c': s.color } }));
            }
          });
          const last = [...s.points].reverse().find((p) => finite(p.y));
          if (last) markers.push({ s, x: X(last.x), y: Y(yv(last.y)) });
        }
        // direct end labels (dropped when they would have to move too far)
        if (endLabels) {
          markers.sort((a, b) => a.y - b.y);
          const placed = [];
          for (const m of markers) {
            let y = m.y;
            const prev = placed[placed.length - 1];
            if (prev && y < prev.y + 14) y = prev.y + 14;
            if (Math.abs(y - m.y) > 20 || y > y1 + 6) continue;
            placed.push({ ...m, y });
          }
          for (const m of placed) {
            const lx = Math.min(m.x + 8, x1 + 8);
            const lg = S('g', { class: 'rc-endlabel', 'data-k': m.s.key });
            lg.appendChild(S('line', { class: 'rc-endkey', x1: lx, x2: lx + 8, y1: m.y, y2: m.y, style: { stroke: m.s.color } }));
            lg.appendChild(S('text', { class: 'rc-label', x: lx + 12, y: m.y, 'dominant-baseline': 'central', text: m.s.label }));
            svg.appendChild(lg);
          }
        }
        // hover: one target per distinct x
        const ux = [...new Set(xs)].sort((a, b) => a - b);
        const xfmt = spec.x.tipFmt || xAxis.fmt;
        const yt = spec.y.tipFmt || yfmt;
        for (const xv of ux) {
          const rows = [], dots = [];
          let ys2 = 0, n = 0;
          for (const s of series) {
            const p = s.points.find((q) => q.x === xv && finite(q.y));
            if (!p) continue;
            const yy = Y(yv(p.y));
            dots.push({ x: X(xv), y: yy, color: s.color });
            ys2 += yy; n++;
            let val = yt(p.y);
            if (s.band && finite(p.lo) && finite(p.hi) && spec.bandInTip !== false && Math.abs(p.hi - p.lo) > 1e-9 * Math.abs(p.hi)) val += `  (${yt(p.lo)}–${yt(p.hi)})`;
            rows.push([s.color, s.label, p.tip || val, s.dash ? 'dash' : 'line']);
          }
          if (!rows.length) continue;
          if (spec.extraRows) rows.push(...spec.extraRows(xv));
          ctx.add({ kind: 'x', x: X(xv), y0, y1, px0: x0, px1: x1, ax: X(xv), ay: ys2 / n, dots,
            tip: { head: spec.head ? spec.head(xv) : xfmt(xv), sub: spec.sub ? spec.sub(xv) : null, rows } });
        }
      },
    });
  };

  /* ================================================================ hbars */
  /**
   * spec: {rows: [{key, label, swatch, segments: [{v, color}], total, valueText, note, muted, best, tip}],
   *        x: {log, label, fmt, min}, rowH, aria}
   */
  RC.hbars = function (host, spec) {
    const rowH = spec.rowH || 18, gap = spec.gap || 14;
    const n = spec.rows.length;
    const top = spec.x && spec.x.label ? 10 : 6;
    return RC.mount(host, {
      height: () => top + n * (rowH + gap) + 34,
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const labels = spec.rows.map((r) => r.label);
        const lw = Math.min(ctx.narrow ? 112 : 240, maxW(labels, 12, 600) + 24);
        const vt = (r) => (ctx.narrow && r.valueShort ? r.valueShort : r.valueText);
        const vw = Math.max(64, maxW(spec.rows.map((r) => vt(r) || ''), 12, 700) + (spec.rows.some((r) => r.best) ? 26 : 12));
        const x0 = lw + 8, x1 = w - vw - 8;
        const totals = spec.rows.map((r) => r.total).filter(finite);
        let lo = spec.x.log ? Math.min(...totals.filter((v) => v > 0)) : 0;
        let hi = totals.length ? Math.max(...totals) : 1;
        if (spec.x.log && !(lo > 0)) lo = 1;
        const ax = makeAxis({ ...spec.x, zero: !spec.x.log }, lo, Math.max(hi, spec.x.log ? lo * 10 : 1e-9), x0, x1, Math.max(2, Math.floor((x1 - x0) / 90)));
        const X = ax.scale;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        const yEnd = top + n * (rowH + gap);
        const fmt = ax.fmt;
        for (const v of ax.ticks) {
          const x = Math.round(X(v)) + 0.5;
          g.appendChild(S('line', { class: 'rc-grid', x1: x, x2: x, y1: top, y2: yEnd }));
        }
        ticksX(g, ax.ticks, X, yEnd, fmt, false, top);
        titleX(g, spec.x.label, x1, h - 3);
        const base = spec.x.log ? X(ax.lo) : X(0);
        spec.rows.forEach((r, i) => {
          const y = top + i * (rowH + gap) + gap / 2;
          const rg = S('g', { class: `rc-row${r.muted ? ' rc-muted' : ''}` });
          svg.appendChild(rg);
          rg.appendChild(S('rect', { class: 'rc-rowbg', x: 0, y: y - gap / 2 + 2, width: w, height: rowH + gap - 4, rx: 8 }));
          // label with a colour key
          if (r.swatch) rg.appendChild(S('rect', { class: 'rc-swatch', x: 2, y: y + rowH / 2 - 5, width: 10, height: 10, rx: 3, style: { fill: r.swatch } }));
          let label = r.label;
          while (label.length > 3 && textW(label, 12, 600) > lw - 22) label = label.slice(0, -2);
          if (label !== r.label) label = label.replace(/\s+$/, '') + '…';
          rg.appendChild(S('text', { class: 'rc-rowlabel', x: r.swatch ? 18 : 2, y: y + rowH / 2, 'dominant-baseline': 'central', text: label }));
          // stacked segments with 2-px surface gaps
          let acc = spec.x.log ? ax.lo : 0;
          const segs = (r.segments || []).filter((s) => finite(s.v) && s.v > 0);
          const bars = S('g', { class: 'rc-bars' });
          rg.appendChild(bars);
          let xa = base;
          segs.forEach((s, k) => {
            const end = spec.x.log ? s.v : acc + s.v;
            const xb = X(end);
            const last = k === segs.length - 1;
            const xs = k ? xa + 1 : xa, xe = last ? xb : xb - 1;
            if (xe - xs > 0.3) {
              bars.appendChild(S('path', { class: 'rc-bar', d: last ? barPath(xs, y, xe - xs, rowH, 'h') : `M${xs},${y}h${xe - xs}v${rowH}h${-(xe - xs)}Z`, style: { fill: s.color } }));
            }
            acc = end;
            xa = xb;
          });
          if (r.note && !segs.length) {
            rg.appendChild(S('text', { class: 'rc-note', x: base + 4, y: y + rowH / 2, 'dominant-baseline': 'central', text: r.note }));
          }
          if (vt(r)) {
            const vx = (segs.length ? xa : base + (r.note ? textW(r.note, 11.5, 650) + 10 : 0)) + 8;
            const t = S('text', { class: 'rc-value', x: vx, y: y + rowH / 2, 'dominant-baseline': 'central', text: vt(r) });
            rg.appendChild(t);
            if (r.best) {
              const bx = vx + textW(vt(r), 12, 700) + 6;
              rg.appendChild(S('path', { class: 'rc-star', d: star(bx + 5, y + rowH / 2, 5.2) }));
            }
          }
          const target = { kind: 'rect', x: 0, y: y - gap / 2 + 1, w, h: rowH + gap - 2, ax: Math.min(w - 40, Math.max(x0, xa)), ay: y + rowH / 2, tip: r.tip, el: rg };
          ctx.add(target);
        });
        baseline(g, x0, x1, yEnd);
        g.appendChild(S('line', { class: 'rc-axis', x1: Math.round(base) + 0.5, x2: Math.round(base) + 0.5, y1: top, y2: yEnd }));
      },
    });
  };

  function star(cx, cy, r) {
    let d = '';
    for (let k = 0; k < 10; k++) {
      const rr = k % 2 ? r * 0.45 : r;
      const a = -Math.PI / 2 + k * Math.PI / 5;
      d += `${k ? 'L' : 'M'}${(cx + rr * Math.cos(a)).toFixed(2)},${(cy + rr * Math.sin(a)).toFixed(2)}`;
    }
    return d + 'Z';
  }

  /* ============================================================== columns */
  /**
   * spec: {categories: [{key, label}], series: [{key, label, color}],
   *        value: (catKey, serKey) => {v, tip} | null, y: {label, fmt}, head: (cat) => string}
   */
  RC.columns = function (host, spec) {
    return RC.mount(host, {
      height: spec.height || ((w) => (w < 520 ? 240 : 280)),
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const cats = spec.categories, sers = spec.series;
        const vals = [];
        for (const c of cats) for (const s of sers) { const q = spec.value(c.key, s.key); if (q && finite(q.v)) vals.push(q.v); }
        if (!vals.length) return;
        const top = 30, bottom = 40, y1 = h - bottom, y0 = top;
        const ax = makeAxis({ ...spec.y, zero: true }, Math.min(0, ...vals), Math.max(0, ...vals), y1, y0, Math.max(3, Math.floor((y1 - y0) / 44)));
        const Y = ax.scale;
        const fmt = ax.fmt;
        const x0 = Math.max(30, maxW(ax.ticks.map(fmt)) + 14), x1 = w - 12;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        gridY(g, ax.ticks, Y, x0, x1, fmt);
        titleY(g, spec.y.label, 2, 13);
        titleX(g, spec.x && spec.x.label, x1, h - 4);
        const slot = (x1 - x0) / cats.length;
        const bw = Math.max(3, Math.min(24, (slot * 0.72 - (sers.length - 1) * 2) / sers.length));
        const groupW = bw * sers.length + (sers.length - 1) * 2;
        const zy = Y(0);
        let lastLabel = -Infinity;
        cats.forEach((c, ci) => {
          const cx = x0 + slot * (ci + 0.5);
          const lab = c.label;
          const tw = textW(lab);
          if (cx - tw / 2 > lastLabel + 4) {
            g.appendChild(S('text', { class: 'rc-tick', x: cx, y: y1 + 17, 'text-anchor': 'middle', text: lab }));
            lastLabel = cx + tw / 2;
          }
          const cg = S('g', { class: 'rc-col' });
          svg.appendChild(cg);
          cg.appendChild(S('rect', { class: 'rc-rowbg', x: cx - slot / 2 + 2, y: y0 - 6, width: slot - 4, height: y1 - y0 + 6, rx: 8 }));
          const rows = [];
          sers.forEach((s, si) => {
            const q = spec.value(c.key, s.key);
            if (!q || !finite(q.v)) return;
            const bx = cx - groupW / 2 + si * (bw + 2);
            const yv = Y(q.v);
            const up = q.v >= 0;
            const hh = Math.abs(zy - yv);
            if (hh > 0.2) cg.appendChild(S('path', { class: 'rc-bar rc-vbar', d: barPath(bx, up ? yv : zy, bw, hh, up ? 'up' : 'down', Math.min(4, bw / 2)), style: { fill: s.color } }));
            else cg.appendChild(S('line', { class: 'rc-zero-mark', x1: bx, x2: bx + bw, y1: zy, y2: zy, style: { stroke: s.color } }));
            rows.push([s.color, s.label, q.tip || fmt(q.v), 'rect']);
          });
          ctx.add({ kind: 'rect', x: cx - slot / 2, y: y0, w: slot, h: y1 - y0, ax: cx + groupW / 2, ay: (y0 + y1) / 2, el: cg,
            tip: { head: spec.head ? spec.head(c) : c.label, rows } });
        });
        g.appendChild(S('line', { class: 'rc-axis rc-zero', x1: x0, x2: x1, y1: Math.round(zy) + 0.5, y2: Math.round(zy) + 0.5 }));
      },
    });
  };

  /* ================================================================ boxes */
  /** spec: {groups: [{key, label, color, stats, points: [{y, tip}], note}], y: {label, fmt, tipFmt}} */
  RC.boxes = function (host, spec) {
    return RC.mount(host, {
      height: spec.height || ((w) => (w < 520 ? 300 : 340)),
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const groups = spec.groups;
        const ys = [];
        for (const gr of groups) for (const p of gr.points) if (finite(p.y)) ys.push(p.y);
        if (!ys.length) return;
        const top = 30, bottom = 54, y1 = h - bottom, y0 = top;
        const ax = makeAxis({ ...spec.y, zero: true }, Math.min(0, ...ys), Math.max(...ys), y1, y0, Math.max(3, Math.floor((y1 - y0) / 44)));
        const Y = ax.scale;
        const fmt = ax.fmt;
        const tfmt = spec.y.tipFmt || fmt;
        const x0 = Math.max(30, maxW(ax.ticks.map(fmt)) + 14), x1 = w - 10;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        gridY(g, ax.ticks, Y, x0, x1, fmt);
        titleY(g, spec.y.label, 2, 13);
        const slot = (x1 - x0) / groups.length;
        const bw = Math.max(18, Math.min(64, slot * 0.4));
        groups.forEach((gr, gi) => {
          const cx = x0 + slot * (gi + 0.5);
          const st = gr.stats || {};
          const bg = S('g', { class: 'rc-boxg', 'data-k': gr.key });
          svg.appendChild(bg);
          // group label and note under the axis (compact when the slot is narrow)
          const roomy = textW(gr.label, 12, 650) + 22 <= slot - 6;
          let label = gr.label;
          while (!roomy && label.length > 3 && textW(label, 11, 650) > slot - 8) label = label.slice(0, -2);
          if (label !== gr.label) label = label.replace(/\s+$/, '') + '…';
          if (roomy) g.appendChild(S('rect', { class: 'rc-swatch', x: cx - textW(gr.label, 12, 650) / 2 - 15, y: y1 + 11, width: 10, height: 10, rx: 3, style: { fill: gr.color } }));
          g.appendChild(S('text', { class: `rc-rowlabel${roomy ? '' : ' rc-small'}`, x: roomy ? cx + 7 : cx, y: y1 + 16.5, 'text-anchor': 'middle', 'dominant-baseline': 'central', text: label }));
          const note = gr.note && textW(gr.note, 10.5) <= slot - 4 ? gr.note : gr.shortNote;
          if (note) g.appendChild(S('text', { class: 'rc-subtick', x: cx, y: y1 + 34, 'text-anchor': 'middle', text: note }));
          if (st.n) {
            const wl = Y(st.whisker_lo), wh = Y(st.whisker_hi), q1 = Y(st.q1), q3 = Y(st.q3), md = Y(st.median);
            bg.appendChild(S('line', { class: 'rc-whisker', x1: cx, x2: cx, y1: wl, y2: q1, style: { stroke: gr.color } }));
            bg.appendChild(S('line', { class: 'rc-whisker', x1: cx, x2: cx, y1: q3, y2: wh, style: { stroke: gr.color } }));
            bg.appendChild(S('line', { class: 'rc-whisker', x1: cx - bw * 0.22, x2: cx + bw * 0.22, y1: wl, y2: wl, style: { stroke: gr.color } }));
            bg.appendChild(S('line', { class: 'rc-whisker', x1: cx - bw * 0.22, x2: cx + bw * 0.22, y1: wh, y2: wh, style: { stroke: gr.color } }));
            bg.appendChild(S('rect', { class: 'rc-box', x: cx - bw / 2, y: q3, width: bw, height: Math.max(1.5, q1 - q3), rx: 5, style: { '--c': gr.color } }));
            bg.appendChild(S('line', { class: 'rc-median', x1: cx - bw / 2 + 1, x2: cx + bw / 2 - 1, y1: md, y2: md }));
            const my = Y(st.mean);
            bg.appendChild(S('path', { class: 'rc-mean', d: `M${cx},${my - 4.5}L${cx + 4.5},${my}L${cx},${my + 4.5}L${cx - 4.5},${my}Z` }));
            ctx.add({ kind: 'rect', x: cx - bw / 2 - 4, y: Math.min(wh, q3) - 2, w: bw + 8, h: Math.max(wl, q1) - Math.min(wh, q3) + 4, ax: cx + bw / 2, ay: md, el: bg,
              tip: { head: gr.label, rows: [
                [gr.color, I.t('median'), tfmt(st.median), 'rect'],
                [gr.color, I.t('mean'), tfmt(st.mean), 'rect'],
                [gr.color, I.t('quartiles'), `${tfmt(st.q1)} – ${tfmt(st.q3)}`, 'rect'],
                [gr.color, I.t('max'), tfmt(st.max), 'rect'],
                [null, 'n', I.num(st.n, 0)],
              ] } });
          }
          // the observations, jittered deterministically inside the box width
          const pts = gr.points.filter((p) => finite(p.y));
          pts.forEach((p, k) => {
            const j = (((k * 2654435761) >>> 0) % 1000) / 1000 - 0.5;
            const px = cx + j * bw * 0.8, py = Y(p.y);
            const c = S('circle', { class: 'rc-obs', cx: px, cy: py, r: pts.length > 80 ? 2.4 : 3.1, style: { '--c': gr.color } });
            bg.appendChild(c);
            ctx.add({ kind: 'pt', x: px, y: py, r: 3, tip: p.tip, el: c });
          });
        });
        baseline(g, x0, x1, Y(0));
      },
    });
  };

  /* ============================================================== scatter */
  /**
   * spec: {points: [{x, y, s, tip, key}], color, line: 'stair'|'poly',
   *        sizeDomain: [lo, hi], refs: [{x, y, label, color, tip}],
   *        notes: [{i, text}], x: {label, fmt}, y: {label, fmt}}
   */
  RC.scatter = function (host, spec) {
    return RC.mount(host, {
      height: spec.height || ((w) => (w < 520 ? 300 : 360)),
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const pts = spec.points;
        if (!pts.length) return;
        let tx = pts.map((p) => p.x), my = pts.map((p) => p.y);
        let t0 = Math.min(...tx), t1 = Math.max(...tx), m0 = Math.min(...my), m1 = Math.max(...my);
        const tSpan = Math.max(t1 - t0, 1e-6 + Math.abs(t0) * 0.02), mSpan = Math.max(m1 - m0, 1e-6 + Math.abs(m0) * 0.03);
        // reference points widen the axes only while the front keeps most of the plot
        const refs = (spec.refs || []).filter((r) => finite(r.x) && finite(r.y));
        for (const r of refs.slice().sort((a, b) => Math.hypot((a.x - t0) / tSpan, (a.y - m0) / mSpan) - Math.hypot((b.x - t0) / tSpan, (b.y - m0) / mSpan))) {
          const n0 = Math.min(t0, r.x), n1 = Math.max(t1, r.x), k0 = Math.min(m0, r.y), k1 = Math.max(m1, r.y);
          if (n1 - n0 <= 1.7 * tSpan && k1 - k0 <= 1.7 * mSpan) { t0 = n0; t1 = n1; m0 = k0; m1 = k1; }
        }
        const padT = Math.max((t1 - t0) * 0.06, tSpan * 0.04), padM = Math.max((m1 - m0) * 0.08, mSpan * 0.05);
        const top = 30, bottom = 40, y1 = h - bottom, y0 = top;
        const ay = makeAxis(spec.y, m0 - padM, m1 + padM, y1, y0, Math.max(3, Math.floor((y1 - y0) / 46)));
        const yfmt = ay.fmt;
        const x0 = Math.max(36, maxW(ay.ticks.map(yfmt)) + 14), x1 = w - 16;
        const axx = makeAxis(spec.x, t0 - padT, t1 + padT, x0, x1, Math.max(3, Math.floor((x1 - x0) / 84)));
        const X = axx.scale, Y = ay.scale;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        gridY(g, ay.ticks, Y, x0, x1, yfmt);
        ticksX(g, axx.ticks, X, y1, axx.fmt, true, y0);
        baseline(g, x0, x1, y1);
        titleY(g, spec.y.label, 2, 13);
        titleX(g, spec.x.label, x1, h - 4);
        const plot = S('g', { class: 'rc-plot' });
        svg.appendChild(plot);
        const color = spec.color;
        // dominated region + staircase through the points that are
        // non-dominated in the two plotted criteria (the others are on the
        // front because of a third criterion)
        const sorted = pts.map((p, i) => ({ ...p, i })).sort((a, b) => a.x - b.x || a.y - b.y);
        const front2 = [];
        let best = Infinity;
        for (const p of sorted) if (p.y < best - 1e-12) { front2.push(p); best = p.y; }
        if (spec.line && front2.length > 0) {
          const path = [];
          front2.forEach((p, k) => {
            if (k && spec.line === 'stair') path.push([X(p.x), Y(front2[k - 1].y)]);
            path.push([X(p.x), Y(p.y)]);
          });
          if (spec.shade !== false) {
            const d = `M${path[0][0].toFixed(1)},${y0}` + path.map((q) => `L${q[0].toFixed(1)},${q[1].toFixed(1)}`).join('') + `L${x1},${path[path.length - 1][1].toFixed(1)}L${x1},${y0}Z`;
            plot.appendChild(S('path', { class: 'rc-dominated', d, style: { fill: color } }));
          }
          if (path.length > 1) plot.appendChild(S('path', { class: 'rc-front', d: pathD(path), style: { stroke: color } }));
        }
        const [s0, s1] = spec.sizeDomain || [0, 1];
        const dense = pts.length > 120;
        const rad = (s) => (dense ? 2.2 : 3.2) + (dense ? 3.2 : 5.6) * Math.sqrt(s1 > s0 && finite(s) ? U.clamp((s - s0) / (s1 - s0), 0, 1) : 0.4);
        const placed = [];
        for (const p of sorted) {
          const cx = X(p.x), cy = Y(p.y), r = rad(p.s);
          const c = S('circle', { class: `rc-pt${p.mark ? ' rc-pt-mark' : ''}`, cx, cy, r, style: { '--c': color } });
          plot.appendChild(c);
          placed.push([cx - r, cy - r, cx + r, cy + r]);
          ctx.add({ kind: 'pt', x: cx, y: cy, r, tip: p.tip, el: c });
        }
        // other algorithms as diamonds (pinned to the border with an arrow when outside)
        const groups = [];
        for (const r of refs) {
          const x = X(r.x), y = Y(r.y);
          const out = x < x0 || x > x1 || y < y0 || y > y1;
          const cx = U.clamp(x, x0 + 8, x1 - 8), cy = U.clamp(y, y0 + 8, y1 - 8);
          let gp = groups.find((q) => Math.abs(q.cx - cx) + Math.abs(q.cy - cy) < 6);
          if (!gp) { gp = { x, y, cx, cy, out, members: [] }; groups.push(gp); }
          gp.members.push(r);
        }
        for (const gp of groups) {
          const r = gp.members[0];
          const mk = S('g', { class: 'rc-refmark', transform: `translate(${gp.cx.toFixed(1)},${gp.cy.toFixed(1)})` });
          if (gp.out) {
            const ang = Math.atan2(gp.y - gp.cy, gp.x - gp.cx) * 180 / Math.PI;
            mk.appendChild(S('path', { d: 'M8,0L-4,-6L-1.5,0L-4,6Z', transform: `rotate(${ang.toFixed(1)})`, style: { fill: r.color } }));
          } else {
            mk.appendChild(S('rect', { x: -4.6, y: -4.6, width: 9.2, height: 9.2, transform: 'rotate(45)', class: 'rc-diamond', style: { stroke: r.color } }));
          }
          svg.appendChild(mk);
          placed.push([gp.cx - 8, gp.cy - 8, gp.cx + 8, gp.cy + 8]);
          ctx.add({ kind: 'pt', x: gp.cx, y: gp.cy, r: 6, tip: { head: gp.members.map((m) => m.label).join(' · '), sub: gp.out ? I.t('off_axis') : null, rows: gp.members[0].tip.rows } });
        }
        // labels: greedy placement, skipped when every candidate collides
        const place = (text, ax, ay2, r, cls) => {
          const tw = textW(text, 11, 650);
          const d = r + 6;
          const cands = [[ax + d, ay2], [ax - d - tw, ay2], [ax - tw / 2, ay2 - r - 10], [ax - tw / 2, ay2 + r + 11],
            [ax + d, ay2 - r - 9], [ax - d - tw, ay2 - r - 9], [ax + d, ay2 + r + 9], [ax - d - tw, ay2 + r + 9]];
          for (const [lx, ly] of cands) {
            const bb = [lx - 3, ly - 8, lx + tw + 3, ly + 8];
            if (bb[0] < x0 + 2 || bb[2] > x1 - 2 || bb[1] < y0 || bb[3] > y1) continue;
            if (placed.some((o) => bb[0] < o[2] && bb[2] > o[0] && bb[1] < o[3] && bb[3] > o[1])) continue;
            placed.push(bb);
            svg.appendChild(S('text', { class: `rc-annot ${cls || ''}`, x: lx, y: ly, 'dominant-baseline': 'central', text }));
            return true;
          }
          return false;
        };
        for (const nt of spec.notes || []) {
          const p = pts[nt.i];
          if (p) place(nt.text, X(p.x), Y(p.y), rad(p.s), 'rc-annot-strong');
        }
        for (const gp of groups) place(gp.members.map((m) => m.label).join(' · '), gp.cx, gp.cy, 7);
      },
    });
  };

  /* ================================================================= dots */
  /** spec: {values: [{v, color, tip}], x: {label, fmt, domain}, diameter} */
  RC.dots = function (host, spec) {
    let layout = null;
    function compute(w) {
      const vals = spec.values.filter((q) => finite(q.v));
      const x0 = 18, x1 = w - 18;
      let lo = Math.min(0, ...vals.map((q) => q.v)), hi = Math.max(...vals.map((q) => q.v), 0);
      if (!(hi > lo)) hi = lo + 1;
      const ax = makeAxis({ ...spec.x, zero: true }, lo, hi, x0, x1, Math.max(3, Math.floor((x1 - x0) / 80)));
      let d = spec.diameter || 11;
      for (let k = 0; k < 4; k++) {
        const tallest = new Map();
        for (const q of vals) { const b = Math.round((ax.scale(q.v) - x0) / d); tallest.set(b, (tallest.get(b) || 0) + 1); }
        const t = Math.max(1, ...tallest.values());
        if (t * d <= 240 || d <= 5) break;
        d = Math.max(5, Math.floor(240 / t));
      }
      const bins = new Map();
      const order = vals.slice().sort((a, b) => a.v - b.v || String(a.color).localeCompare(String(b.color)));
      for (const q of order) {
        const b = Math.round((ax.scale(q.v) - x0) / d);
        if (!bins.has(b)) bins.set(b, []);
        bins.get(b).push(q);
      }
      let tall = 1;
      for (const v of bins.values()) tall = Math.max(tall, v.length);
      return { ax, d, bins, tall, x0, x1 };
    }
    return RC.mount(host, {
      height: (w) => { layout = compute(w); return Math.max(150, 60 + layout.tall * layout.d + 16); },
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const L = layout || compute(w);
        const y1 = h - 40;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        const X = L.ax.scale;
        ticksX(g, L.ax.ticks, X, y1, L.ax.fmt, true, 16);
        baseline(g, L.x0, L.x1, y1);
        titleX(g, spec.x.label, L.x1, h - 4);
        const zx = Math.round(X(0)) + 0.5;
        g.appendChild(S('line', { class: 'rc-axis rc-zero', x1: zx, x2: zx, y1: 16, y2: y1 }));
        const plot = S('g', { class: 'rc-plot' });
        svg.appendChild(plot);
        const r = L.d / 2 - 0.6;
        for (const [b, list] of L.bins) {
          list.forEach((q, k) => {
            const cx = L.x0 + b * L.d, cy = y1 - L.d / 2 - k * L.d - 1;
            const c = S('circle', { class: 'rc-pt rc-dotplot', cx, cy, r, style: { '--c': q.color } });
            plot.appendChild(c);
            ctx.add({ kind: 'pt', x: cx, y: cy, r, tip: q.tip, el: c });
          });
        }
      },
    });
  };

  /* ============================================================= dumbbell */
  /** spec: {categories: [{key, label}], pairs: [{cat, a, b, tip}], a: {label, color}, b: {label, color}, y: {label, fmt}} */
  RC.dumbbell = function (host, spec) {
    return RC.mount(host, {
      height: spec.height || ((w) => (w < 520 ? 240 : 280)),
      aria: spec.aria,
      draw(ctx) {
        const { svg, w, h } = ctx;
        const vals = [];
        for (const p of spec.pairs) { if (finite(p.a)) vals.push(p.a); if (finite(p.b)) vals.push(p.b); }
        if (!vals.length) return;
        const top = 30, bottom = 40, y1 = h - bottom, y0 = top;
        const ax = makeAxis({ ...spec.y, zero: true }, 0, Math.max(...vals), y1, y0, Math.max(3, Math.floor((y1 - y0) / 44)));
        const Y = ax.scale;
        const fmt = ax.fmt;
        const x0 = Math.max(30, maxW(ax.ticks.map(fmt)) + 14), x1 = w - 12;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        gridY(g, ax.ticks, Y, x0, x1, fmt);
        baseline(g, x0, x1, y1);
        titleY(g, spec.y.label, 2, 13);
        titleX(g, spec.x && spec.x.label, x1, h - 4);
        const slot = (x1 - x0) / spec.categories.length;
        let lastLabel = -Infinity;
        spec.categories.forEach((c, ci) => {
          const cx = x0 + slot * (ci + 0.5);
          const tw = textW(c.label);
          if (cx - tw / 2 > lastLabel + 4) {
            g.appendChild(S('text', { class: 'rc-tick', x: cx, y: y1 + 17, 'text-anchor': 'middle', text: c.label }));
            lastLabel = cx + tw / 2;
          }
          const p = spec.pairs.find((q) => q.cat === c.key);
          if (!p) return;
          const cg = S('g', { class: 'rc-col' });
          svg.appendChild(cg);
          cg.appendChild(S('rect', { class: 'rc-rowbg', x: cx - slot / 2 + 2, y: y0 - 6, width: slot - 4, height: y1 - y0 + 6, rx: 8 }));
          if (finite(p.a) && finite(p.b)) cg.appendChild(S('line', { class: 'rc-bell', x1: cx, x2: cx, y1: Y(p.a), y2: Y(p.b) }));
          if (finite(p.a)) cg.appendChild(S('circle', { class: 'rc-pt rc-hollow-pt', cx, cy: Y(p.a), r: 5, style: { '--c': spec.a.color } }));
          if (finite(p.b)) cg.appendChild(S('circle', { class: 'rc-pt', cx, cy: Y(p.b), r: 5, style: { '--c': spec.b.color } }));
          ctx.add({ kind: 'rect', x: cx - slot / 2, y: y0, w: slot, h: y1 - y0, ax: cx + 6, ay: Y(Math.max(p.a || 0, p.b || 0)), el: cg, tip: p.tip });
        });
      },
    });
  };

  /* =============================================================== legend */
  /**
   * Interactive legend: items [{key, label, color, kind}]; clicking toggles
   * a series (`onToggle(hiddenSet)`), hovering emphasises it in `chartHost`.
   */
  RC.legend = function (el, items, opts) {
    opts = opts || {};
    const hidden = opts.hidden || new Set();
    el.replaceChildren();
    el.classList.add('lg');
    for (const it of items) {
      const b = U.el('button', { type: 'button', class: 'lg-item', 'aria-pressed': hidden.has(it.key) ? 'false' : 'true', dataset: { k: it.key } },
        U.el('i', { class: `lg-key lg-${it.kind || 'line'}`, style: { '--c': it.color } }),
        U.el('span', { text: it.label }));
      if (!opts.onToggle) { b.disabled = true; b.classList.add('lg-static'); }
      b.addEventListener('click', () => {
        if (!opts.onToggle) return;
        if (hidden.has(it.key)) hidden.delete(it.key);
        else if (items.length - hidden.size > 1) hidden.add(it.key);
        b.setAttribute('aria-pressed', hidden.has(it.key) ? 'false' : 'true');
        opts.onToggle(hidden);
      });
      const hosts = () => (opts.hosts ? opts.hosts() : []);
      b.addEventListener('mouseenter', () => focusSeries(hosts(), it.key));
      b.addEventListener('mouseleave', () => focusSeries(hosts(), null));
      b.addEventListener('focus', () => focusSeries(hosts(), it.key));
      b.addEventListener('blur', () => focusSeries(hosts(), null));
      el.appendChild(b);
    }
    return hidden;
  };

  function focusSeries(hosts, key) {
    for (const h of hosts) {
      if (!h) continue;
      for (const n of h.querySelectorAll('[data-k]')) {
        n.classList.toggle('dim', key !== null && n.getAttribute('data-k') !== key);
        n.classList.toggle('focus', key !== null && n.getAttribute('data-k') === key);
      }
    }
  }

  EVR.RC = RC;
})(window.EVR = window.EVR || {});
