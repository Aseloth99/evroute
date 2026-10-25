/* EVRoute viewer -- charts.js
 *
 * Hand-written, themed Canvas 2D charts with a hover crosshair and tooltip:
 *
 *   SocChart     state of charge vs distance for every visible route, the
 *                elevation profile of the selected route as a shaded area on
 *                a secondary axis, the reserve line, charging jumps and the
 *                animated car(s);
 *   TimeChart    stacked bars per algorithm: driving, queue, plug-in, charging;
 *   ParetoChart  trip time vs money of the Pareto front (point size = battery
 *                wear), other algorithms as reference; click selects a route;
 *   WaitChart    24-hour expected-wait profile of one station;
 *   sparkline    small wait profile used in the station tooltip.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;
  const M = EVR.model;

  class Chart {
    constructor(app, host, aria) {
      this.app = app;
      this.host = host;
      this.canvas = U.el('canvas', { role: 'img' });
      this.tip = U.el('div', { class: 'chart-tip', hidden: true });
      this.emptyEl = U.el('div', { class: 'chart-empty', hidden: true });
      host.append(this.canvas, this.tip, this.emptyEl);
      this.aria = aria;
      this.mouse = null;
      this.redraw = U.rafThrottle(() => this._draw());
      new ResizeObserver(() => this.redraw()).observe(host);
      this.canvas.addEventListener('pointermove', (ev) => { this.mouse = { x: ev.offsetX, y: ev.offsetY }; this.redraw(); });
      this.canvas.addEventListener('pointerleave', () => { this.mouse = null; this.tip.hidden = true; this.redraw(); });
      this.canvas.addEventListener('click', (ev) => this.onClick && this.onClick(ev.offsetX, ev.offsetY));
    }

    _draw() {
      if (!this.host.isConnected || this.host.offsetParent === null) return;
      const { ctx, w, h } = U.fitCanvas(this.canvas);
      ctx.clearRect(0, 0, w, h);
      this.canvas.setAttribute('aria-label', I.t(this.aria));
      this.tipRows = null;
      this.draw(ctx, w, h, this.app.theme);
      if (this.tipRows && this.mouse) this._showTip(); else this.tip.hidden = true;
    }

    empty(msg) {
      this.emptyEl.hidden = !msg;
      this.emptyEl.textContent = msg || '';
    }

    /** Tooltip content: {head, rows: [[color, label, value]]}. */
    setTip(head, rows, anchorX, anchorY) {
      this.tipRows = { head, rows, x: anchorX, y: anchorY };
    }

    _showTip() {
      const t = this.tip, d = this.tipRows;
      U.setChildren(t,
        d.head ? U.el('div', { class: 'tt-head', text: d.head }) : null,
        d.rows.map(([c, label, value]) => U.el('div', { class: 'tt-row' },
          U.el('i', { style: { '--algo': c || 'transparent', background: c || 'transparent' } }),
          U.el('span', { text: label }), U.el('b', { text: value }))));
      t.hidden = false;
      const hw = this.host.clientWidth, hh = this.host.clientHeight;
      const tw = t.offsetWidth, th = t.offsetHeight;
      let x = d.x + 14, y = d.y - th / 2;
      if (x + tw > hw - 4) x = d.x - tw - 14;
      x = U.clamp(x, 4, Math.max(4, hw - tw - 4));
      y = U.clamp(y, 4, Math.max(4, hh - th - 4));
      t.style.left = `${x}px`;
      t.style.top = `${y}px`;
    }
  }

  /* -------------------------------------------------------- axis helpers */
  function axisText(ctx, th, size) {
    ctx.font = `500 ${size || 11}px ${th.font}`;
    ctx.fillStyle = th.chartText;
  }

  function gridY(ctx, th, ticks, ymap, x0, x1, fmt, side) {
    axisText(ctx, th);
    ctx.textBaseline = 'middle';
    ctx.textAlign = side === 'right' ? 'left' : 'right';
    for (const v of ticks) {
      const y = Math.round(ymap(v)) + 0.5;
      if (side !== 'right') {
        ctx.strokeStyle = th.chartGrid;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
        ctx.fillText(fmt(v), x0 - 7, y);
      } else {
        ctx.fillText(fmt(v), x1 + 7, y);
      }
    }
  }

  function gridX(ctx, th, ticks, xmap, y0, y1, fmt) {
    axisText(ctx, th);
    ctx.textBaseline = 'top';
    ctx.textAlign = 'center';
    for (const v of ticks) {
      const x = Math.round(xmap(v)) + 0.5;
      ctx.strokeStyle = th.chartGrid;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
      ctx.fillText(fmt(v), x, y1 + 6);
    }
  }

  function axisTitle(ctx, th, text, x, y, rot, align) {
    ctx.save();
    ctx.translate(x, y);
    if (rot) ctx.rotate(rot);
    ctx.font = `600 10.5px ${th.font}`;
    ctx.fillStyle = th.text3;
    ctx.textAlign = align || 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 0, 0);
    ctx.restore();
  }

  /* ============================================================ SoC chart */
  class SocChart extends Chart {
    constructor(app, host) { super(app, host, 'chart_soc'); }

    draw(ctx, w, h, th) {
      const app = this.app;
      const routes = app.visibleRoutes().filter((r) => r._soc && r._soc.length > 1);
      const sel = app.selectedRoute();
      if (!routes.length) { this.empty(I.t('no_route')); return; }
      this.empty(null);
      const pad = { l: 46, r: 54, t: 14, b: 34 };
      const x0 = pad.l, x1 = w - pad.r, y0 = pad.t, y1 = h - pad.b;
      let dmax = 0;
      for (const r of routes) dmax = Math.max(dmax, r._soc[r._soc.length - 1][0]);
      dmax = Math.max(dmax, 1);
      const xt = U.niceTicks(0, dmax, Math.max(3, Math.floor((x1 - x0) / 90)));
      const xmax = Math.max(dmax, xt[xt.length - 1]);
      const X = (d) => x0 + (d / xmax) * (x1 - x0);
      const Y = (s) => y1 - s * (y1 - y0);

      // elevation (secondary axis)
      const er = (sel && sel._elev && sel._elev.length > 1) ? sel : routes[0];
      let zmin = Infinity, zmax = -Infinity;
      for (const p of er._elev) { zmin = Math.min(zmin, p[1]); zmax = Math.max(zmax, p[1]); }
      const zt = U.niceTicks(Math.max(0, zmin - 50), zmax + 50, 4);
      const zlo = Math.min(zt[0], zmin), zhi = Math.max(zt[zt.length - 1], zmax);
      const Z = (z) => y1 - ((z - zlo) / Math.max(1, zhi - zlo)) * (y1 - y0) * 0.9;

      gridY(ctx, th, [0, 0.2, 0.4, 0.6, 0.8, 1], Y, x0, x1, (v) => I.num(v * 100, 0));
      gridX(ctx, th, xt.filter((v) => v <= xmax + 1e-9), X, y0, y1, (v) => I.num(v, 0));
      axisText(ctx, th);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      for (const z of zt) if (z >= zlo && z <= zhi) ctx.fillText(I.num(z, 0), x1 + 7, Z(z));
      axisTitle(ctx, th, I.t('ax_soc'), 12, (y0 + y1) / 2, -Math.PI / 2);
      axisTitle(ctx, th, I.t('ax_elev'), w - 10, (y0 + y1) / 2, Math.PI / 2);
      axisTitle(ctx, th, I.t('ax_dist'), x1, h - 6, 0, 'right');

      // elevation area
      ctx.beginPath();
      ctx.moveTo(X(er._elev[0][0]), y1);
      for (const p of er._elev) ctx.lineTo(X(p[0]), Z(p[1]));
      ctx.lineTo(X(er._elev[er._elev.length - 1][0]), y1);
      ctx.closePath();
      ctx.fillStyle = th.elevFill;
      ctx.fill();
      ctx.beginPath();
      er._elev.forEach((p, k) => (k ? ctx.lineTo(X(p[0]), Z(p[1])) : ctx.moveTo(X(p[0]), Z(p[1]))));
      ctx.strokeStyle = th.elevLine;
      ctx.lineWidth = 1.2;
      ctx.stroke();

      // reserve and arrival requirement
      const prefs = app.currentPrefs();
      const res = prefs.reserve_soc || 0;
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = th.reserve;
      ctx.globalAlpha = 0.8;
      ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.moveTo(x0, Y(res) + 0.5); ctx.lineTo(x1, Y(res) + 0.5); ctx.stroke();
      ctx.restore();
      ctx.font = `600 10.5px ${th.font}`;
      ctx.fillStyle = th.reserve;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(I.t('reserve_line'), x0 + 6, Y(res) - 3);

      // routes, selected last
      const ordered = routes.filter((r) => r !== sel).concat(routes.includes(sel) ? [sel] : []);
      for (const r of ordered) {
        const isSel = r === sel;
        const c = app.colorOf(r);
        ctx.save();
        ctx.beginPath();
        r._soc.forEach((p, k) => (k ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.globalAlpha = isSel ? 1 : 0.55;
        if (!r.feasible) ctx.setLineDash([5, 4]);
        ctx.strokeStyle = c;
        ctx.lineWidth = isSel ? 2.6 : 1.6;
        ctx.stroke();
        ctx.restore();
        if (r._strand) {
          const p = r._soc[r._soc.length - 1];
          drawCross(ctx, X(p[0]), Y(p[1]), 5, th.bad);
        }
      }
      // charging markers of the selected route
      if (sel && sel._stops) {
        for (const s of sel._stops) {
          const d = (sel.node_dist_m[s.position] || 0) / 1000;
          const xa = X(d), ya = Y(s.soc_in), yb = Y(s.soc_out);
          ctx.fillStyle = th.surface;
          ctx.strokeStyle = app.colorOf(sel);
          ctx.lineWidth = 2;
          for (const yy of [ya, yb]) { ctx.beginPath(); ctx.arc(xa, yy, 3.2, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }
          badge(ctx, xa, Math.max(y0 + 9, yb - 13), String(s._num), app.colorOf(sel), th);
        }
      }

      // animated car(s)
      for (const cs of app.anim.carStates()) {
        if (!cs.r._soc) continue;
        const x = X(cs.st.dist / 1000), y = Y(U.clamp(cs.st.soc, 0, 1));
        ctx.beginPath();
        ctx.arc(x, y, 6.5, 0, Math.PI * 2);
        ctx.fillStyle = U.rgba(app.colorOf(cs.r), 0.22);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = app.colorOf(cs.r);
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = th.surface;
        ctx.stroke();
      }

      // hover crosshair
      const m = this.mouse;
      if (m && m.x >= x0 && m.x <= x1 && m.y >= y0 - 4 && m.y <= y1 + 4) {
        const d = ((m.x - x0) / (x1 - x0)) * xmax;
        ctx.strokeStyle = th.crosshair;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(Math.round(m.x) + 0.5, y0); ctx.lineTo(Math.round(m.x) + 0.5, y1); ctx.stroke();
        const rows = [];
        for (const r of ordered.slice().reverse()) {
          const v = M.socAtDistance(r, d);
          if (v === null) continue;
          ctx.beginPath();
          ctx.arc(m.x, Y(U.clamp(v, 0, 1)), 3.5, 0, Math.PI * 2);
          ctx.fillStyle = app.colorOf(r);
          ctx.fill();
          rows.push([app.colorOf(r), r.title, I.pct(v, 1)]);
        }
        const z = interp(er._elev, d);
        if (z !== null) rows.push([th.elevLine, I.t('elevation'), `${I.num(z, 0)} m`]);
        this.setTip(`${I.num(d, d < 10 ? 1 : 0)} km`, rows, m.x, m.y);
      }
    }
  }

  function interp(pts, x) {
    if (!pts || !pts.length || x < pts[0][0] || x > pts[pts.length - 1][0]) return null;
    for (let j = 1; j < pts.length; j++) {
      if (pts[j][0] >= x) {
        const a = pts[j - 1], b = pts[j];
        return b[0] > a[0] ? a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]) : b[1];
      }
    }
    return pts[pts.length - 1][1];
  }

  function drawCross(ctx, x, y, r, color) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.4;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(x - r, y - r); ctx.lineTo(x + r, y + r);
    ctx.moveTo(x + r, y - r); ctx.lineTo(x - r, y + r);
    ctx.stroke();
    ctx.restore();
  }

  function badge(ctx, x, y, text, color, th) {
    ctx.save();
    ctx.font = `800 10px ${th.font}`;
    const w = Math.max(16, ctx.measureText(text).width + 8);
    U.roundRect(ctx, x - w / 2, y - 8, w, 16, 8);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = th.surface;
    ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y + 0.5);
    ctx.restore();
  }

  /* ====================================================== time breakdown */
  const PARTS = [
    ['drive', 'drive_time_s', 'cDrive'],
    ['queue', 'wait_time_s', 'cQueue'],
    ['setup', 'setup_time_s', 'cSetup'],
    ['charge', 'charge_time_s', 'cCharge'],
  ];

  class TimeChart extends Chart {
    constructor(app, host) {
      super(app, host, 'chart_time');
      this.onClick = (x, y) => {
        const row = this._rowAt(y);
        if (row >= 0) app.select(row, -1);
      };
    }

    layoutHeight() {
      const n = this.app.model.results.length;
      return Math.max(160, 8 + n * 30 + 40);
    }

    _rowAt(y) {
      if (!this._rows) return -1;
      for (const r of this._rows) if (y >= r.y - 4 && y <= r.y + r.h + 4) return r.ri;
      return -1;
    }

    draw(ctx, w, h, th) {
      const app = this.app;
      const res = app.model.results;
      const want = `${this.layoutHeight()}px`;
      if (this.host.style.height !== want) { this.host.style.height = want; this.redraw(); return; }
      if (!res.length) { this.empty(I.t('no_route')); return; }
      this.empty(null);
      ctx.font = `650 12px ${th.font}`;
      let lw = 60;
      for (const r of res) lw = Math.max(lw, ctx.measureText(r.title).width);
      const pad = { l: Math.min(150, lw + 30), r: 70, t: 8, b: 30 };
      const x0 = pad.l, x1 = w - pad.r;
      let tmax = 0;
      for (const r of res) if (r._ok) tmax = Math.max(tmax, (r._strand ? r._strand.t : r.total_time_s) || 0);
      tmax = Math.max(tmax / 3600, 0.5);
      const ticks = U.niceTicks(0, tmax, Math.max(3, Math.floor((x1 - x0) / 80)));
      const hmax = Math.max(tmax, ticks[ticks.length - 1]);
      const X = (hrs) => x0 + (hrs / hmax) * (x1 - x0);
      const rowH = 18, gap = 12;
      const y1 = pad.t + res.length * (rowH + gap);
      gridX(ctx, th, ticks, X, pad.t, y1, (v) => I.num(v, v % 1 ? 1 : 0));
      axisTitle(ctx, th, I.t('ax_time_h'), x1, Math.min(h - 6, y1 + 26), 0, 'right');
      const sel = app.selectedRoute();
      const selRi = sel ? sel._ri : -1;
      this._rows = [];
      let hover = null;
      const m = this.mouse;
      res.forEach((r, ri) => {
        const y = pad.t + ri * (rowH + gap) + gap / 2;
        this._rows.push({ ri, y, h: rowH });
        const visible = app.visible.has(ri);
        if (ri === selRi) {
          U.roundRect(ctx, 4, y - 5, w - 8, rowH + 10, 9);
          ctx.fillStyle = U.rgba(th.accent, th.dark ? 0.12 : 0.07);
          ctx.fill();
        }
        // label
        ctx.globalAlpha = visible ? 1 : 0.45;
        ctx.fillStyle = app.colorOf(r);
        U.roundRect(ctx, 12, y + rowH / 2 - 5, 10, 10, 3);
        ctx.fill();
        ctx.font = `650 12px ${th.font}`;
        ctx.fillStyle = r.feasible ? th.text : th.text3;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(r.title, 28, y + rowH / 2);
        // bars
        let acc = 0;
        const segs = [];
        for (const [key, field, tok] of PARTS) {
          const v = (r[field] || 0) / 3600;
          if (!r._ok || v <= 0) continue;
          const xa = X(acc), xb = X(acc + v);
          segs.push([key, xa, xb, th[tok], v]);
          acc += v;
        }
        if (r._strand) acc = r._strand.t / 3600;
        ctx.save();
        U.roundRect(ctx, x0, y, Math.max(2, X(acc) - x0), rowH, 5);
        ctx.clip();
        for (const [key, xa, xb, color] of segs) {
          ctx.fillStyle = color;
          ctx.globalAlpha = (visible ? 1 : 0.45) * (r.feasible ? 1 : 0.4);
          ctx.fillRect(xa, y, Math.max(0.5, xb - xa + 0.4), rowH);
        }
        ctx.restore();
        ctx.globalAlpha = 1;
        // total
        ctx.font = `700 12px ${th.font}`;
        ctx.fillStyle = r.feasible ? th.text : th.bad;
        ctx.textAlign = 'left';
        const label = !r._ok ? `✕ ${I.t('infeasible')}` : r.feasible ? I.hm(r.total_time_s) : `✕ ${I.t('infeasible')}`;
        ctx.fillText(label, (r._ok ? X(acc) : x0) + 8, y + rowH / 2);
        if (m && m.y >= y - 4 && m.y <= y + rowH + 4) hover = { r, segs, y };
      });
      ctx.globalAlpha = 1;
      if (hover) {
        const r = hover.r;
        const rows = [
          [th.cDrive, I.t('drive'), I.duration(r.drive_time_s)],
          [th.cQueue, I.t('queue'), I.duration(r.wait_time_s)],
          [th.cSetup, I.t('setup'), I.duration(r.setup_time_s)],
          [th.cCharge, I.t('charge'), I.duration(r.charge_time_s)],
          [null, I.t('total_time'), r.feasible ? I.duration(r.total_time_s) : I.t('infeasible')],
        ];
        this.setTip(r.title, rows, m.x, hover.y + rowH / 2);
      }
    }
  }

  /* ========================================================= Pareto chart */
  class ParetoChart extends Chart {
    constructor(app, host) {
      super(app, host, 'chart_pareto');
      this.onClick = (x, y) => {
        const p = this._pick(x, y);
        if (!p) return;
        if (p.ai !== undefined && p.ai >= 0) app.select(p.ri, p.ai);
        else if (p.ai === undefined) app.select(p.ri, -1);
      };
    }

    _pick(x, y) {
      let best = null, bd = 14 * 14;
      for (const p of this._pts || []) {
        const d = (p.x - x) ** 2 + (p.y - y) ** 2;
        if (d < bd) { bd = d; best = p; }
      }
      return best;
    }

    draw(ctx, w, h, th) {
      const app = this.app;
      const res = app.model.results;
      const owner = res.find((r) => r.pareto_points && r.pareto_points.length > 1);
      if (!owner) { this.empty(''); return; }
      this.empty(null);
      const pts = owner.pareto_points.filter((p) => p[0] !== null && p[1] !== null);
      const others = res.filter((r) => r !== owner && r.feasible && U.isNum(r.total_time_s));
      let tmin = Infinity, tmax = -Infinity, mmin = Infinity, mmax = -Infinity, smin = Infinity, smax = -Infinity;
      for (const p of pts) {
        tmin = Math.min(tmin, p[0]); tmax = Math.max(tmax, p[0]);
        mmin = Math.min(mmin, p[1]); mmax = Math.max(mmax, p[1]);
        smin = Math.min(smin, p[2]); smax = Math.max(smax, p[2]);
      }
      for (const r of others) {
        tmin = Math.min(tmin, r.total_time_s); tmax = Math.max(tmax, r.total_time_s);
        mmin = Math.min(mmin, r.money); mmax = Math.max(mmax, r.money);
      }
      const tPad = Math.max((tmax - tmin) * 0.08, 300), mPad = Math.max((mmax - mmin) * 0.1, 1);
      const pad = { l: 64, r: 18, t: 14, b: 36 };
      const x0 = pad.l, x1 = w - pad.r, y0 = pad.t, y1 = h - pad.b;
      const xt = U.niceTicks((tmin - tPad) / 3600, (tmax + tPad) / 3600, Math.max(3, Math.floor((x1 - x0) / 80)));
      const yt = U.niceTicks(Math.max(0, mmin - mPad), mmax + mPad, 5);
      const xa = xt[0], xb = xt[xt.length - 1], ya = yt[0], yb = yt[yt.length - 1];
      const X = (t) => x0 + ((t / 3600 - xa) / (xb - xa || 1)) * (x1 - x0);
      const Y = (mo) => y1 - ((mo - ya) / (yb - ya || 1)) * (y1 - y0);
      const cur = app.model.currency;
      gridY(ctx, th, yt, Y, x0, x1, (v) => I.money(v, cur, 0));
      gridX(ctx, th, xt, (v) => x0 + ((v - xa) / (xb - xa || 1)) * (x1 - x0), y0, y1, (v) => I.num(v, 1));
      axisTitle(ctx, th, I.t('ax_time_h'), x1, h - 6, 0, 'right');

      const color = app.colorOf(owner);
      const sel = app.selection;
      // front staircase
      const sorted = pts.slice().sort((a, b) => a[0] - b[0]);
      ctx.save();
      ctx.beginPath();
      sorted.forEach((p, k) => {
        const x = X(p[0]), y = Y(p[1]);
        if (!k) ctx.moveTo(x, y); else { ctx.lineTo(x, Y(sorted[k - 1][1])); ctx.lineTo(x, y); }
      });
      ctx.strokeStyle = U.rgba(color, 0.35);
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();

      this._pts = [];
      const rad = (s) => 3 + 8 * Math.sqrt(smax > smin ? (s - smin) / (smax - smin) : 0.4);
      const ownerRi = owner._ri;
      for (const p of pts) {
        const x = X(p[0]), y = Y(p[1]), r = rad(p[2]);
        const isSel = sel.ri === ownerRi && sel.ai >= 0 && sel.ai === p[3];
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = U.rgba(color, p[3] >= 0 ? 0.72 : 0.28);
        ctx.fill();
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = th.surface;
        ctx.stroke();
        if (isSel) {
          ctx.beginPath();
          ctx.arc(x, y, r + 4, 0, Math.PI * 2);
          ctx.lineWidth = 2;
          ctx.strokeStyle = th.accent;
          ctx.stroke();
        }
        this._pts.push({ x, y, ri: ownerRi, ai: p[3], p, kind: 'front' });
      }
      // the plan the Pareto planner returned (min objective) and the other algorithms
      for (const r of others) {
        const x = X(r.total_time_s), y = Y(r.money);
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate(Math.PI / 4);
        ctx.fillStyle = th.surface;
        ctx.strokeStyle = app.colorOf(r);
        ctx.lineWidth = 2.2;
        const isSel = sel.ri === r._ri && sel.ai < 0;
        ctx.fillRect(-4, -4, 8, 8);
        ctx.strokeRect(-4, -4, 8, 8);
        if (isSel) { ctx.strokeStyle = th.accent; ctx.lineWidth = 1.6; ctx.strokeRect(-7.5, -7.5, 15, 15); }
        ctx.restore();
        this._pts.push({ x, y, ri: r._ri, r, kind: 'other' });
      }
      const m = this.mouse;
      if (m) {
        const p = this._pick(m.x, m.y);
        if (p) {
          if (p.kind === 'front') {
            const v = p.p;
            this.setTip(`${owner.title} · ${p.ai >= 0 ? '#' + (owner.alternatives[p.ai].member + 1) : ''}`, [
              [color, I.t('total_time'), I.duration(v[0])],
              [color, I.t('money'), I.money(v[1], cur)],
              [color, I.t('soh_short'), I.soh(v[2])],
            ], p.x, p.y);
          } else {
            const r = p.r;
            this.setTip(r.title, [
              [app.colorOf(r), I.t('total_time'), I.duration(r.total_time_s)],
              [app.colorOf(r), I.t('money'), I.money(r.money, cur)],
              [app.colorOf(r), I.t('soh_short'), I.soh(r.soh_drive_pct + r.soh_charge_pct)],
            ], p.x, p.y);
          }
          this.canvas.style.cursor = 'pointer';
        } else this.canvas.style.cursor = 'default';
      }
    }
  }

  /* =========================================================== wait chart */
  class WaitChart extends Chart {
    constructor(app, host) { super(app, host, 'chart_wait'); }

    draw(ctx, w, h, th) {
      const app = this.app;
      const si = app.waitStation();
      const label = U.$('#wait-station');
      if (si < 0) { this.empty(I.t('wait_none')); if (label) label.textContent = ''; return; }
      const st = app.model.stations[si];
      if (label) label.textContent = `${st.name || '#' + st.id} · ${st.cls} ${I.num(st.power_kw, 0)} kW`;
      const prof = st.wait_profile;
      const N = 145;
      const xs = [], ys = [];
      let ymax = 0;
      for (let k = 0; k < N; k++) {
        const t = (k / (N - 1)) * 86400;
        const v = M.waitAt(prof, t) / 60;
        xs.push(t); ys.push(v);
        ymax = Math.max(ymax, v);
      }
      this.empty(ymax <= 1e-9 ? I.t('wait_zero') : null);
      const pad = { l: 40, r: 14, t: 12, b: 32 };
      const x0 = pad.l, x1 = w - pad.r, y0 = pad.t, y1 = h - pad.b;
      const yt = U.niceTicks(0, Math.max(ymax * 1.1, 5), 4);
      const yTop = yt[yt.length - 1];
      const X = (t) => x0 + (t / 86400) * (x1 - x0);
      const Y = (v) => y1 - (v / yTop) * (y1 - y0);
      gridY(ctx, th, yt, Y, x0, x1, (v) => I.num(v, 0));
      gridX(ctx, th, [0, 3, 6, 9, 12, 15, 18, 21, 24], (hh) => X(hh * 3600), y0, y1, (v) => String(v).padStart(2, '0'));
      axisTitle(ctx, th, I.t('ax_wait'), 12, (y0 + y1) / 2, -Math.PI / 2);
      if (ymax <= 1e-9) return;
      const grad = ctx.createLinearGradient(0, y0, 0, y1);
      grad.addColorStop(0, U.rgba(th.cQueue, 0.38));
      grad.addColorStop(1, U.rgba(th.cQueue, 0.04));
      ctx.beginPath();
      ctx.moveTo(X(0), y1);
      for (let k = 0; k < N; k++) ctx.lineTo(X(xs[k]), Y(ys[k]));
      ctx.lineTo(X(86400), y1);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.beginPath();
      for (let k = 0; k < N; k++) (k ? ctx.lineTo(X(xs[k]), Y(ys[k])) : ctx.moveTo(X(xs[k]), Y(ys[k])));
      ctx.strokeStyle = th.cQueue;
      ctx.lineWidth = 2;
      ctx.stroke();

      // "now" (trip clock) and the selected route's arrival at this station
      const tod = ((app.clockTod() % 86400) + 86400) % 86400;
      ctx.strokeStyle = th.text2;
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(X(tod) + 0.5, y0); ctx.lineTo(X(tod) + 0.5, y1); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = `650 10.5px ${th.font}`;
      ctx.fillStyle = th.text2;
      ctx.textAlign = X(tod) > x1 - 60 ? 'right' : 'left';
      ctx.textBaseline = 'top';
      ctx.fillText(`${I.t('now')} ${I.clock(tod, false)}`, X(tod) + (ctx.textAlign === 'left' ? 4 : -4), y0);
      const sel = app.selectedRoute();
      if (sel && sel._stops) {
        for (const s of sel._stops) {
          if (s.station !== si) continue;
          const t = ((app.model.departure + s.arrival_s) % 86400 + 86400) % 86400;
          const x = X(t), y = Y(M.waitAt(prof, t) / 60);
          ctx.beginPath();
          ctx.arc(x, y, 5, 0, Math.PI * 2);
          ctx.fillStyle = app.colorOf(sel);
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = th.surface;
          ctx.stroke();
          badge(ctx, x, Math.max(y0 + 8, y - 15), String(s._num), app.colorOf(sel), th);
        }
      }
      const m = this.mouse;
      if (m && m.x >= x0 && m.x <= x1) {
        const t = ((m.x - x0) / (x1 - x0)) * 86400;
        const v = M.waitAt(prof, t) / 60;
        ctx.strokeStyle = th.crosshair;
        ctx.beginPath(); ctx.moveTo(m.x + 0.5, y0); ctx.lineTo(m.x + 0.5, y1); ctx.stroke();
        ctx.beginPath(); ctx.arc(m.x, Y(v), 3.5, 0, Math.PI * 2); ctx.fillStyle = th.cQueue; ctx.fill();
        this.setTip(I.clock(t, false), [[th.cQueue, I.t('wait'), I.minutes(v * 60)]], m.x, m.y);
      }
    }
  }

  /** 24-h wait sparkline into a small canvas (station tooltip). */
  function sparkline(canvas, prof, tod, th) {
    const { ctx, w, h } = U.fitCanvas(canvas);
    ctx.clearRect(0, 0, w, h);
    const N = 97;
    let ymax = 0;
    const ys = [];
    for (let k = 0; k < N; k++) { const v = M.waitAt(prof, (k / (N - 1)) * 86400); ys.push(v); ymax = Math.max(ymax, v); }
    const top = Math.max(ymax, 300);
    const X = (k) => 1 + (k / (N - 1)) * (w - 2);
    const Y = (v) => h - 2 - (v / top) * (h - 6);
    ctx.beginPath();
    ctx.moveTo(X(0), h);
    for (let k = 0; k < N; k++) ctx.lineTo(X(k), Y(ys[k]));
    ctx.lineTo(X(N - 1), h);
    ctx.closePath();
    ctx.fillStyle = U.rgba(th.cQueue, 0.22);
    ctx.fill();
    ctx.beginPath();
    for (let k = 0; k < N; k++) (k ? ctx.lineTo(X(k), Y(ys[k])) : ctx.moveTo(X(k), Y(ys[k])));
    ctx.strokeStyle = th.cQueue;
    ctx.lineWidth = 1.6;
    ctx.stroke();
    const t = ((tod % 86400) + 86400) % 86400;
    const x = 1 + (t / 86400) * (w - 2);
    ctx.strokeStyle = th.text2;
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 2]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.arc(x, Y(M.waitAt(prof, t)), 3, 0, Math.PI * 2);
    ctx.fillStyle = th.cQueue;
    ctx.fill();
  }

  EVR.charts = { Chart, SocChart, TimeChart, ParetoChart, WaitChart, sparkline, badge, drawCross };
})(window.EVR = window.EVR || {});
