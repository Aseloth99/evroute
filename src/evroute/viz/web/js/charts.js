/* EVRoute viewer -- charts.js
 *
 * Hand-written, themed Canvas 2D charts with a hover crosshair and tooltip:
 *
 *   SocChart     state of charge vs distance for every visible route, the
 *                elevation profile of the selected route as a shaded area on
 *                a secondary axis, the reserve line, charging jumps and the
 *                animated car(s);
 *   TimeChart    stacked bars per algorithm: driving, (tariff hold,) queue,
 *                plug-in, charging;
 *   ParetoChart  trip time vs money of the Pareto front (point size = battery
 *                wear), other algorithms as reference; click selects a route;
 *   WaitChart    24-hour expected-wait profile of one station (with its peak
 *                price period shaded when the scenario has a charging tariff);
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
  /**
   * State of charge (left axis, %) against distance for every visible
   * route, with the elevation profile of the selected route as a shaded
   * area on the right axis.  Charging stops are vertical jumps (numbered on
   * the selected route), an infeasible route ends in a cross where the
   * battery runs empty, and the reserve is a dashed line.  The animated
   * car(s) are mirrored as dots; the selected car also gets a "now" guide.
   */
  class SocChart extends Chart {
    constructor(app, host) { super(app, host, 'chart_soc'); }

    draw(ctx, w, h, th) {
      const app = this.app;
      const routes = app.visibleRoutes().filter((r) => r._soc && r._soc.length > 1);
      const sel = app.selectedRoute();
      if (!routes.length) { this.empty(I.t('no_route')); return; }
      this.empty(null);
      const narrow = w < 480;
      const pad = { l: narrow ? 40 : 48, r: narrow ? 46 : 56, t: 16, b: 36 };
      const x0 = pad.l, x1 = w - pad.r, y0 = pad.t, y1 = h - pad.b;
      if (x1 - x0 < 40 || y1 - y0 < 40) return;
      let dmax = 0;
      for (const r of routes) dmax = Math.max(dmax, r._soc[r._soc.length - 1][0]);
      const xd = U.niceDomain(0, Math.max(dmax, 1), Math.max(3, Math.floor((x1 - x0) / 90)));
      const xmax = xd.hi;
      const X = (d) => x0 + (d / xmax) * (x1 - x0);
      const Y = (s) => y1 - s * (y1 - y0);

      // elevation (secondary axis), scaled to the lower 85 % of the plot
      const er = (sel && sel._elev && sel._elev.length > 1) ? sel : routes[0];
      let zmin = Infinity, zmax = -Infinity;
      for (const p of er._elev) { zmin = Math.min(zmin, p[1]); zmax = Math.max(zmax, p[1]); }
      const zd = U.niceDomain(Math.min(0, zmin), Math.max(zmax, zmin + 100), 4);
      const Z = (z) => y1 - ((z - zd.lo) / Math.max(1, zd.hi - zd.lo)) * (y1 - y0) * 0.85;

      gridY(ctx, th, [0, 0.2, 0.4, 0.6, 0.8, 1], Y, x0, x1, (v) => I.num(v * 100, 0));
      gridX(ctx, th, xd.ticks, X, y0, y1, (v) => I.num(v, 0));
      axisText(ctx, th);
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      for (const z of zd.ticks) ctx.fillText(I.num(z, 0), x1 + 7, Z(z));
      axisTitle(ctx, th, I.t('ax_soc'), 11, (y0 + y1) / 2, -Math.PI / 2);
      axisTitle(ctx, th, I.t('ax_elev'), w - 9, (y0 + y1) / 2, Math.PI / 2);
      axisTitle(ctx, th, I.t('ax_dist'), x1, h - 6, 0, 'right');

      ctx.save();
      ctx.beginPath();
      ctx.rect(x0, y0 - 6, x1 - x0, y1 - y0 + 6);
      ctx.clip();

      // elevation area with a soft vertical gradient
      const eg = ctx.createLinearGradient(0, Z(zd.hi), 0, y1);
      eg.addColorStop(0, th.elevFill);
      eg.addColorStop(1, U.rgba(th.elevFill, 0.04));
      ctx.beginPath();
      ctx.moveTo(X(er._elev[0][0]), y1);
      for (const p of er._elev) ctx.lineTo(X(p[0]), Z(p[1]));
      ctx.lineTo(X(er._elev[er._elev.length - 1][0]), y1);
      ctx.closePath();
      ctx.fillStyle = eg;
      ctx.fill();
      ctx.beginPath();
      er._elev.forEach((p, k) => (k ? ctx.lineTo(X(p[0]), Z(p[1])) : ctx.moveTo(X(p[0]), Z(p[1]))));
      ctx.strokeStyle = th.elevLine;
      ctx.lineWidth = 1.1;
      ctx.lineJoin = 'round';
      ctx.stroke();

      // reserve line
      const prefs = app.currentPrefs();
      const res = prefs.reserve_soc || 0;
      ctx.save();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = th.reserve;
      ctx.globalAlpha = 0.85;
      ctx.lineWidth = 1.3;
      ctx.beginPath(); ctx.moveTo(x0, Math.round(Y(res)) + 0.5); ctx.lineTo(x1, Math.round(Y(res)) + 0.5); ctx.stroke();
      ctx.restore();
      ctx.font = `650 10.5px ${th.font}`;
      ctx.fillStyle = th.reserve;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'bottom';
      ctx.fillText(I.t('reserve_line'), x0 + 6, Y(res) - 3);

      // charging tariff: the SoC above which the high-SoC price applies (only with such a tier)
      const tariff = app.tariff();
      const tf = tariff ? EVR.panels.tariffFactors(tariff) : null;
      if (tf && (tf.off[1] !== tf.off[0] || tf.peak[1] !== tf.peak[0])) {
        const yt = Math.round(Y(tariff.high_soc)) + 0.5;
        ctx.save();
        ctx.setLineDash([2, 3]);
        ctx.strokeStyle = th.cHold;
        ctx.globalAlpha = 0.8;
        ctx.lineWidth = 1.2;
        ctx.beginPath(); ctx.moveTo(x0, yt); ctx.lineTo(x1, yt); ctx.stroke();
        ctx.restore();
        ctx.font = `650 10.5px ${th.font}`;
        ctx.fillStyle = th.cHold;
        ctx.textAlign = 'right';
        ctx.textBaseline = 'bottom';
        ctx.fillText(I.t('tariff_line', { p: I.pct(tariff.high_soc, 0) }), x1 - 6, yt - 3);
      }

      // selected route: gradient area under its SoC line
      const ordered = routes.filter((r) => r !== sel).concat(routes.includes(sel) ? [sel] : []);
      if (sel && routes.includes(sel)) {
        const c = app.colorOf(sel);
        const g = ctx.createLinearGradient(0, y0, 0, y1);
        g.addColorStop(0, U.rgba(c, th.dark ? 0.30 : 0.20));
        g.addColorStop(1, U.rgba(c, 0));
        ctx.beginPath();
        const p0 = sel._soc[0], pn = sel._soc[sel._soc.length - 1];
        ctx.moveTo(X(p0[0]), y1);
        for (const p of sel._soc) ctx.lineTo(X(p[0]), Y(U.clamp(p[1], 0, 1)));
        ctx.lineTo(X(pn[0]), y1);
        ctx.closePath();
        ctx.fillStyle = g;
        ctx.fill();
      }

      // routes, selected last (on top)
      for (const r of ordered) {
        const isSel = r === sel;
        const c = app.colorOf(r);
        ctx.save();
        ctx.beginPath();
        r._soc.forEach((p, k) => (k ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        if (!r.feasible) ctx.setLineDash([5, 4]);
        if (isSel) {
          ctx.strokeStyle = th.surface;
          ctx.lineWidth = 5.2;
          ctx.stroke();
        }
        ctx.globalAlpha = isSel ? 1 : 0.5;
        ctx.strokeStyle = c;
        ctx.lineWidth = isSel ? 2.6 : 1.5;
        ctx.stroke();
        ctx.restore();
      }
      ctx.restore();
      // where infeasible routes run empty (outside the clip: on the axis)
      for (const r of ordered) {
        if (!r._strand) continue;
        const p = r._soc[r._soc.length - 1];
        drawCross(ctx, X(p[0]), Y(Math.max(0, p[1])), 4.5, th.bad);
      }

      // charging markers of the selected route: in/out dots and the stop number
      if (sel && sel._stops && routes.includes(sel)) {
        const c = app.colorOf(sel);
        for (const s of sel._stops) {
          const d = (sel.node_dist_m[s.position] || 0) / 1000;
          const xa = X(d), ya = Y(s.soc_in), yb = Y(s.soc_out);
          ctx.fillStyle = th.surface;
          ctx.strokeStyle = c;
          ctx.lineWidth = 2;
          for (const yy of [ya, yb]) { ctx.beginPath(); ctx.arc(xa, yy, 3.3, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); }
          badge(ctx, xa, Math.max(y0 + 2, yb - 14), String(s._num), c, th);
        }
      }

      // animated car(s); the selected one also gets a "now" guide line
      for (const cs of app.anim.carStates()) {
        if (!cs.r._soc) continue;
        const x = X(cs.st.dist / 1000), y = Y(U.clamp(cs.st.soc, 0, 1));
        const c = app.colorOf(cs.r);
        if (cs.r === sel) {
          ctx.save();
          ctx.strokeStyle = U.rgba(c, 0.55);
          ctx.setLineDash([2, 3]);
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, y0); ctx.lineTo(Math.round(x) + 0.5, y1); ctx.stroke();
          ctx.restore();
        }
        ctx.beginPath();
        ctx.arc(x, y, 8, 0, Math.PI * 2);
        ctx.fillStyle = U.rgba(c, 0.22);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(x, y, 4.4, 0, Math.PI * 2);
        ctx.fillStyle = c;
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
          ctx.arc(m.x, Y(U.clamp(v, 0, 1)), 3.6, 0, Math.PI * 2);
          ctx.fillStyle = app.colorOf(r);
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = th.surface;
          ctx.stroke();
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
    ['hold', 'hold_time_s', 'cHold'],     // charging tariffs only (absent or 0 otherwise)
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
          ...(r.hold_time_s > 0 ? [[th.cHold, I.t('hold'), I.duration(r.hold_time_s)]] : []),
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
  /**
   * Trip time vs money of the Pareto front (point area ~ battery wear).
   *
   * The axes are fitted to the *front*: another algorithm's result is drawn
   * inside the plot when it does not squeeze the front into a corner (it may
   * widen each axis to at most 2.5 times the front's own span), otherwise it
   * is pinned to the plot border with an arrow pointing to where it lies.
   * The region dominated by the front (slower *and* dearer) is shaded, the
   * two extremes are labelled, and the member that is shown as a route is
   * ringed with guide lines to both axes.  Clicking a member selects it.
   */
  class ParetoChart extends Chart {
    constructor(app, host) {
      super(app, host, 'chart_pareto');
      this.onClick = (x, y) => {
        const p = this._pick(x, y);
        if (!p) return;
        if (p.kind === 'front') { if (p.ai >= 0) app.select(p.ri, p.ai); else app.select(p.ri, -1); }
        else app.select(p.ri, -1);
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
      this._pts = [];
      if (!owner) { this.empty(''); return; }
      this.empty(null);
      const cur = app.model.currency;
      const pts = owner.pareto_points.filter((p) => U.isNum(p[0]) && U.isNum(p[1]));
      const front = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);

      // ------------------------------------------------------------ domain
      let t0 = Infinity, t1 = -Infinity, m0 = Infinity, m1 = -Infinity, s0 = Infinity, s1 = -Infinity;
      for (const p of front) {
        t0 = Math.min(t0, p[0]); t1 = Math.max(t1, p[0]);
        m0 = Math.min(m0, p[1]); m1 = Math.max(m1, p[1]);
        const s = U.isNum(p[2]) ? p[2] : 0;
        s0 = Math.min(s0, s); s1 = Math.max(s1, s);
      }
      const tSpan = Math.max(t1 - t0, 600, 0.02 * t0), mSpan = Math.max(m1 - m0, 1, 0.03 * Math.abs(m0));
      const others = res.filter((r) => r !== owner && r.feasible && U.isNum(r.total_time_s) && U.isNum(r.money))
        .sort((a, b) => Math.hypot((a.total_time_s - t0) / tSpan, (a.money - m0) / mSpan) - Math.hypot((b.total_time_s - t0) / tSpan, (b.money - m0) / mSpan));
      let T0 = t0, T1 = t1, M0 = m0, M1 = m1;
      for (const r of others) {
        const nt0 = Math.min(T0, r.total_time_s), nt1 = Math.max(T1, r.total_time_s);
        const nm0 = Math.min(M0, r.money), nm1 = Math.max(M1, r.money);
        if (nt1 - nt0 <= 2.5 * tSpan && nm1 - nm0 <= 2.5 * mSpan) { T0 = nt0; T1 = nt1; M0 = nm0; M1 = nm1; }
      }
      const tp = Math.max((T1 - T0) * 0.08, 120), mp = Math.max((M1 - M0) * 0.1, 0.5);
      const narrow = w < 420;
      const pad = { l: narrow ? 58 : 66, r: 16, t: 16, b: 40 };
      const x0 = pad.l, x1 = w - pad.r, y0 = pad.t, y1 = h - pad.b;
      if (x1 - x0 < 60 || y1 - y0 < 60) return;
      const xd = timeDomain(T0 - tp, T1 + tp, Math.max(3, Math.floor((x1 - x0) / 84)));
      const yd = U.niceDomain(Math.max(0, M0 - mp), M1 + mp, Math.max(3, Math.floor((y1 - y0) / 46)));
      const X = (t) => x0 + ((t - xd.lo) / (xd.hi - xd.lo)) * (x1 - x0);
      const Y = (mo) => y1 - ((mo - yd.lo) / (yd.hi - yd.lo)) * (y1 - y0);
      const mDigits = yd.step < 1 ? 2 : 0;
      gridY(ctx, th, yd.ticks, Y, x0, x1, (v) => I.money(v, cur, mDigits));
      gridX(ctx, th, xd.ticks, X, y0, y1, (v) => I.hm(v));
      frame(ctx, th, x0, y0, x1, y1);
      axisTitle(ctx, th, I.t('total_time'), x1, h - 6, 0, 'right');

      const color = app.colorOf(owner);
      const sel = app.selection;
      const ownerRi = owner._ri;

      ctx.save();
      ctx.beginPath();
      ctx.rect(x0, y0, x1 - x0, y1 - y0);
      ctx.clip();

      // ------------------------------- dominated region and staircase front
      const stair = [];
      front.forEach((p, k) => {
        if (k) stair.push([X(p[0]), Y(front[k - 1][1])]);
        stair.push([X(p[0]), Y(p[1])]);
      });
      ctx.beginPath();
      ctx.moveTo(stair[0][0], y0 - 2);
      for (const [x, y] of stair) ctx.lineTo(x, y);
      ctx.lineTo(x1 + 2, stair[stair.length - 1][1]);
      ctx.lineTo(x1 + 2, y0 - 2);
      ctx.closePath();
      const grad = ctx.createLinearGradient(x0, y1, x1, y0);
      grad.addColorStop(0, U.rgba(color, th.dark ? 0.16 : 0.10));
      grad.addColorStop(1, U.rgba(color, 0.02));
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.beginPath();
      stair.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.strokeStyle = U.rgba(color, 0.7);
      ctx.lineWidth = 1.6;
      ctx.lineJoin = 'round';
      ctx.stroke();

      // ----------------------------------------------------------- members
      const dense = front.length > 60;   // large fronts: smaller dots so the staircase stays visible
      const rad = (s) => (dense ? 2.1 : 2.8) + (dense ? 3.4 : 5.4) * Math.sqrt(s1 > s0 && U.isNum(s) ? (s - s0) / (s1 - s0) : 0.35);
      let selPt = null;
      for (const p of front) {
        const x = X(p[0]), y = Y(p[1]), r = rad(p[2]);
        const hasRoute = p[3] >= 0;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fillStyle = U.rgba(color, hasRoute ? (dense ? 0.7 : 0.78) : 0.3);
        ctx.fill();
        ctx.lineWidth = dense ? 1 : 1.4;
        ctx.strokeStyle = th.surface;
        ctx.stroke();
        if (sel.ri === ownerRi && sel.ai >= 0 && sel.ai === p[3]) selPt = { x, y, r, t: p[0], m: p[1] };
        this._pts.push({ x, y, ri: ownerRi, ai: p[3], p, kind: 'front' });
      }
      // the member the planner returned (minimum generalised cost)
      const ret = U.isNum(owner.total_time_s) ? front.reduce((b, p) => (Math.abs(p[0] - owner.total_time_s) + Math.abs(p[1] - owner.money) < Math.abs(b[0] - owner.total_time_s) + Math.abs(b[1] - owner.money) ? p : b), front[0]) : null;
      if (sel.ri === ownerRi && sel.ai < 0 && ret) selPt = { x: X(ret[0]), y: Y(ret[1]), r: rad(ret[2]), t: ret[0], m: ret[1] };
      ctx.restore();

      // ------------------------------------------------- labels
      // greedy placement: every label tries a few positions around its
      // anchor and is skipped when all of them collide with something
      const boxes = front.map((p) => { const x = X(p[0]), y = Y(p[1]), r = rad(p[2]); return [x - r, y - r, x + r, y + r]; });
      const place = (text, ax, ay, r, fill, font, cands) => {
        ctx.font = font;
        const tw = ctx.measureText(text).width;
        const d = r + 5;
        const at = cands || [
          [ax + d, ay], [ax - d - tw, ay], [ax - tw / 2, ay - r - 9], [ax - tw / 2, ay + r + 9],
          [ax + d, ay - r - 8], [ax - d - tw, ay - r - 8], [ax + d, ay + r + 8], [ax - d - tw, ay + r + 8],
          [ax - tw / 2, ay - r - 22], [ax + d, ay - r - 22],
        ];
        for (const [lx, ly] of at) {
          const bb = [lx - 2, ly - 7, lx + tw + 2, ly + 7];
          if (bb[0] < x0 + 2 || bb[2] > x1 - 2 || bb[1] < y0 + 1 || bb[3] > y1 - 1) continue;
          if (boxes.some((o) => bb[0] < o[2] && bb[2] > o[0] && bb[1] < o[3] && bb[3] > o[1])) continue;
          boxes.push(bb);
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.lineWidth = 3;
          ctx.lineJoin = 'round';
          ctx.strokeStyle = th.surface;
          ctx.strokeText(text, lx, ly);
          ctx.fillStyle = fill;
          ctx.fillText(text, lx, ly);
          return true;
        }
        return false;
      };
      const small = `600 10.5px ${th.font}`;

      // --------------------------------------------- other algorithms
      // results at the same spot (e.g. BLS, BLS-A* and the Pareto pick) share one label
      const groups = [];
      for (const r of others) {
        const x = X(r.total_time_s), y = Y(r.money);
        const out = x < x0 || x > x1 || y < y0 || y > y1;
        const cx = U.clamp(x, x0 + 7, x1 - 7), cy = U.clamp(y, y0 + 7, y1 - 7);
        let g = groups.find((q) => Math.abs(q.cx - cx) + Math.abs(q.cy - cy) < 5);
        if (!g) { g = { x, y, cx, cy, out, members: [] }; groups.push(g); }
        g.members.push(r);
      }
      for (const g of groups) {
        for (const r of g.members.slice().reverse()) {
          const c = app.colorOf(r);
          const isSel = sel.ri === r._ri && sel.ai < 0;
          ctx.save();
          ctx.translate(g.cx, g.cy);
          if (g.out) {
            // arrow towards the true position
            ctx.rotate(Math.atan2(g.y - g.cy, g.x - g.cx));
            ctx.beginPath();
            ctx.moveTo(7, 0); ctx.lineTo(-4, -5.5); ctx.lineTo(-1.5, 0); ctx.lineTo(-4, 5.5);
            ctx.closePath();
            ctx.fillStyle = c;
            ctx.fill();
          } else {
            ctx.rotate(Math.PI / 4);
            ctx.fillStyle = th.surface;
            ctx.strokeStyle = c;
            ctx.lineWidth = 2.2;
            ctx.fillRect(-4.2, -4.2, 8.4, 8.4);
            ctx.strokeRect(-4.2, -4.2, 8.4, 8.4);
            if (isSel) { ctx.strokeStyle = th.accent; ctx.lineWidth = 1.6; ctx.strokeRect(-7.5, -7.5, 15, 15); }
          }
          ctx.restore();
          this._pts.push({ x: g.cx, y: g.cy, ri: r._ri, r, kind: 'other' });
        }
        boxes.push([g.cx - 7, g.cy - 7, g.cx + 7, g.cy + 7]);
      }
      // corner notes: the dominated region (top right) and the front size
      // (bottom left: nothing on the grid is both faster and cheaper there)
      ctx.font = small;
      const txtDom = I.t('pareto_dominated');
      const wDom = ctx.measureText(txtDom).width;
      place(txtDom, 0, 0, 0, th.text3, small, [[x1 - 8 - wDom, y0 + 12], [x1 - 8 - wDom, y0 + 28], [x1 - 8 - wDom, (y0 + y1) / 2]]);
      const txtN = I.t('pareto_members', { n: I.num(owner.alternatives_total || front.length, 0) });
      place(txtN, 0, 0, 0, th.text3, small, [[x0 + 8, y1 - 12], [x0 + 8, y1 - 28]]);
      const bold = `650 10.5px ${th.font}`;
      for (const g of groups) {
        const names = g.members.map((r) => r.title).join(' · ');
        const text = g.out ? `${names} · ${I.t('off_axis')}` : names;
        place(text, g.cx, g.cy, 7, app.colorOf(g.members[0]), bold);
      }
      // extremes of the front
      const fst = front[0], chp = front[front.length - 1];
      place(I.t('pareto_fastest'), X(fst[0]), Y(fst[1]), rad(fst[2]) + 1, th.text2, small);
      if (chp !== fst) place(I.t('pareto_cheapest'), X(chp[0]), Y(chp[1]), rad(chp[2]) + 1, th.text2, small);

      // ------------------------------------- selected member + guides
      if (selPt) {
        ctx.save();
        ctx.setLineDash([3, 3]);
        ctx.strokeStyle = U.rgba(th.accent, 0.7);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(selPt.x + 0.5, selPt.y + selPt.r + 3); ctx.lineTo(selPt.x + 0.5, y1);
        ctx.moveTo(selPt.x - selPt.r - 3, selPt.y + 0.5); ctx.lineTo(x0, selPt.y + 0.5);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(selPt.x, selPt.y, selPt.r + 4, 0, Math.PI * 2);
        ctx.lineWidth = 2.2;
        ctx.strokeStyle = th.accent;
        ctx.stroke();
        ctx.restore();
        axisPill(ctx, th, I.hm(selPt.t), selPt.x, y1 + 12, 'center');
        axisPill(ctx, th, I.money(selPt.m, cur, mDigits), x0 - 4, selPt.y, 'right');
      }

      // ------------------------------------------------------------ hover
      const mouse = this.mouse;
      if (mouse) {
        const p = this._pick(mouse.x, mouse.y);
        if (p) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, (p.kind === 'front' ? rad(p.p[2]) : 6) + 3, 0, Math.PI * 2);
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = th.crosshair;
          ctx.stroke();
          if (p.kind === 'front') {
            const v = p.p;
            const alt = p.ai >= 0 ? owner.alternatives[p.ai] : null;
            this.setTip(`${owner.title}${alt ? ' #' + (alt.member + 1) : ''}`, [
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

  /** Time axis in seconds with ticks on whole minutes/hours: {lo, hi, step, ticks}. */
  function timeDomain(lo, hi, n) {
    const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400];
    const span = Math.max(hi - lo, 60);
    const step = steps.find((s) => span / s <= n) || 86400;
    const a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step;
    const ticks = [];
    for (let v = a; v <= b + 1e-6; v += step) ticks.push(v);
    return { lo: a, hi: b, step, ticks };
  }

  /** Thin plot frame on the left and bottom axes. */
  function frame(ctx, th, x0, y0, x1, y1) {
    ctx.strokeStyle = th.chartAxis;
    ctx.globalAlpha = 0.55;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x0 + 0.5, y0); ctx.lineTo(x0 + 0.5, y1 + 0.5); ctx.lineTo(x1, y1 + 0.5);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  /** Value pill on an axis (highlighted coordinate of the selection). */
  function axisPill(ctx, th, text, x, y, align) {
    ctx.save();
    ctx.font = `700 10.5px ${th.font}`;
    const w = ctx.measureText(text).width + 10, hh = 17;
    const bx = align === 'right' ? x - w : x - w / 2;
    U.roundRect(ctx, bx, y - hh / 2, w, hh, 6);
    ctx.fillStyle = th.accent;
    ctx.fill();
    ctx.fillStyle = th.dark ? '#0b1020' : '#fff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, bx + w / 2, y + 0.5);
    ctx.restore();
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
      // charging tariff: the station's peak price period (clock intervals [a, b))
      const peaks = (st.peak_s || []).filter((iv) => iv[1] > iv[0]);
      if (peaks.length) {
        ctx.save();
        ctx.fillStyle = U.rgba(th.cHold, th.dark ? 0.16 : 0.10);
        for (const [a, b] of peaks) ctx.fillRect(X(a), y0, X(b) - X(a), y1 - y0);
        ctx.font = `650 10px ${th.font}`;
        ctx.fillStyle = th.cHold;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        const widest = peaks.reduce((m, iv) => (iv[1] - iv[0] > m[1] - m[0] ? iv : m), peaks[0]);
        if (X(widest[1]) - X(widest[0]) > 40) ctx.fillText(I.t('tariff_peak_band'), (X(widest[0]) + X(widest[1])) / 2, y1 - 3);
        ctx.restore();
      }
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
          // the wait is evaluated when the stop joins the queue: after a tariff hold, if any
          const t = ((app.model.departure + s.arrival_s + (s.hold_s || 0)) % 86400 + 86400) % 86400;
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
        const rows = [[th.cQueue, I.t('wait'), I.minutes(v * 60)]];
        if (st.peak_s) rows.push([th.cHold, I.t('tariff'), I.t(M.isPeakAt(st, t) ? 'tariff_peak_short' : 'tariff_offpeak_short')]);
        this.setTip(I.clock(t, false), rows, m.x, m.y);
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
