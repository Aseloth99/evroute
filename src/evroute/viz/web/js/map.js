/* EVRoute viewer -- map.js
 *
 * Canvas 2D map with a cached static layer.
 *
 *   static layer (offscreen canvas, rebuilt only when the camera, the size,
 *   the theme, the layers or the selection change):
 *       water/land, terrain hillshade, roads (casing + fill by class),
 *       routes, stations, stop badges, origin/destination pins, labels
 *   overlay (drawn every frame on top of the cached image):
 *       animated cars, trails, charging rings, search-replay dots, hover ring
 *
 * World coordinates are the network's projected metres (y up); the camera is
 * a centre (cx, cy) and a scale s in CSS pixels per metre.  Pan with
 * drag/touch, zoom with the wheel, pinch or the +/- buttons; the map is
 * devicePixelRatio aware.  In search-comparison mode the canvas is split into
 * two viewports that share one camera (and one cached static image).
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;

  const ROAD_WIDTH = { motorway: 3.4, trunk: 2.6, primary: 1.9, secondary: 1.6, urban: 1.3, residential: 0.9, link: 1.2 };
  /** Level of detail (large networks only, model.lod): a class is drawn from this zoom on [px per km]. */
  const MIN_PX_PER_KM = { link: 8, residential: 6, urban: 3, secondary: 1.5 };
  /** Below this zoom a large network is stroked without round caps and casings [px per km]. */
  const COARSE_PX_PER_KM = 2;
  /** Road vertices closer than this to the previous drawn one are skipped [px]. */
  const SUBPIXEL = 0.75;

  class MapView {
    constructor(app, canvas) {
      this.app = app;
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.cam = { cx: 0, cy: 0, s: 0.001 };
      this.w = 1; this.h = 1; this.dpr = 1;
      this.static = document.createElement('canvas');   // base: terrain, roads, routes, stations
      this.labels = document.createElement('canvas');   // pins and place names (above trails)
      this.staticB = document.createElement('canvas');  // lower half of the stacked split view
      this.labelsB = document.createElement('canvas');
      this.staticDirty = true;
      this.terrainImg = null;
      this.stationHits = [];       // [sx, sy, node] in viewport 0 coordinates
      this.hoverNode = -1;
      this.split = false;
      this.splitRows = false;      // split into top/bottom halves instead of left/right
      this.overlay = null;         // function (ctx, vp, T) set by the animator
      this._raf = 0;
      this._fly = null;
      this.userMoved = false;      // once the user pans/zooms, resizes keep the camera
      this._pointers = new Map();
      this._installEvents();
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(canvas.parentElement);
      this.resize();
    }

    get model() { return this.app.model; }

    /* ------------------------------------------------------------- sizing */
    resize() {
      const r = this.canvas.getBoundingClientRect();
      const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
      const dpr = U.dpr();
      if (w === this.w && h === this.h && dpr === this.dpr && this.canvas.width) return;
      this.w = w; this.h = h; this.dpr = dpr;
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
      if (this.app.ready && !this.userMoved) this.fitDefault(false);
      this.invalidate();
    }

    viewports() {
      if (!this.split) return [{ x: 0, y: 0, w: this.w, h: this.h, i: 0 }];
      if (this.splitRows) {
        // the playbar covers the bottom of the lower half: its content is
        // centred in the free part (cyOff), so both halves show the same area
        const hh = Math.floor(this.h / 2);
        return [{ x: 0, y: 0, w: this.w, h: hh, i: 0, cyOff: this._splitTopPad() / 2 },
          { x: 0, y: hh, w: this.w, h: this.h - hh, i: 1, cyOff: -this._splitBottomPad() / 2 }];
      }
      const hw = Math.floor(this.w / 2);
      return [{ x: 0, y: 0, w: hw, h: this.h, i: 0 }, { x: hw, y: 0, w: this.w - hw, h: this.h, i: 1 }];
    }

    /** Viewport under screen point (sx, sy). */
    viewportAt(sx, sy) {
      const vps = this.viewports();
      if (vps.length === 1) return vps[0];
      return this.splitRows ? vps[sy >= vps[1].y ? 1 : 0] : vps[sx >= vps[1].x ? 1 : 0];
    }

    /**
     * Side-by-side or stacked comparison: choose the orientation that shows
     * `bbox` at the larger scale (wide regions stack, tall regions sit side
     * by side).
     */
    chooseSplit(bbox) {
      if (!bbox) { this.splitRows = false; return; }
      const bw = Math.max(bbox[2] - bbox[0], 1), bh = Math.max(bbox[3] - bbox[1], 1);
      const cols = Math.min((this.w / 2 - 40) / bw, (this.h - 280) / bh);
      const rows = Math.min((this.w - 48) / bw, (this.h / 2 - 90) / bh);
      this.splitRows = rows > cols;
    }

    /** Transform constants for a viewport: sx = ox + x * s, sy = oy - y * s. */
    T(vp) {
      const s = this.cam.s;
      return { s, ox: vp.w / 2 - this.cam.cx * s, oy: vp.h / 2 + (vp.cyOff || 0) + this.cam.cy * s, w: vp.w, h: vp.h };
    }

    /** Height hidden by the playbar at the bottom of the map (stacked split view). */
    _splitBottomPad() {
      const pb = document.getElementById('playbar');
      return pb && !pb.hidden ? pb.offsetHeight + 22 : 20;
    }

    /** Height hidden by a full-width search panel at the top (phones; stacked split view). */
    _splitTopPad() {
      const sp = document.getElementById('search-panel');
      if (!sp || sp.hidden || sp.offsetWidth < 0.6 * this.w) return 0;
      return sp.offsetTop + sp.offsetHeight + 6;
    }

    screenToWorld(sx, sy) {
      const vp = this.viewportAt(sx, sy);
      const t = this.T(vp);
      return [(sx - vp.x - t.ox) / t.s, (t.oy - (sy - vp.y)) / t.s];
    }

    /* ------------------------------------------------------------ camera */
    /**
     * Screen margins kept free of content when fitting: the HUD (top-left),
     * the map tools (right) and the playbar (bottom).  With the HUD visible
     * either the left or the top margin is reserved, whichever leaves the
     * larger scale for `bbox`.
     */
    _insets(bbox) {
      const anim = this.app.anim && this.app.anim.enabled;
      const hud = anim && this.app.anim.mode !== 'search';
      const narrow = this.w < 560;
      const meta = (this.model && this.model.scenario && this.model.scenario.meta) || {};
      const credit = meta.attribution ? 16 : 0;   // the data credit line above the playbar
      const base = { l: 28, r: 64, t: 58, b: (anim ? (narrow ? 120 : 112) : 34) + credit };   // t: room for the pins
      if (!hud) return base;
      const hudEl = document.getElementById('hud');
      const hw = (hudEl && hudEl.offsetWidth) || 252, hh = (hudEl && hudEl.offsetHeight) || 150;
      const left = Object.assign({}, base, { l: hw + 28 });
      const top = Object.assign({}, base, { t: hh + 50 });
      if (!bbox) return narrow ? top : left;
      const sc = (p) => Math.min((this.w - p.l - p.r) / Math.max(bbox[2] - bbox[0], 1), (this.h - p.t - p.b) / Math.max(bbox[3] - bbox[1], 1));
      return sc(top) >= sc(left) ? top : left;
    }

    camFor(bbox, vpw, vph) {
      const bw = Math.max(bbox[2] - bbox[0], 1000), bh = Math.max(bbox[3] - bbox[1], 1000);
      if (this.split && this.splitRows) {
        // stacked halves share one camera; the lower half loses the playbar strip
        const free = vph - Math.max(this._splitBottomPad(), this._splitTopPad()) - 36;
        const s = Math.min((vpw - 48) / bw, Math.max(60, free) / bh);
        return { s, cx: (bbox[0] + bbox[2]) / 2, cy: (bbox[1] + bbox[3]) / 2 };
      }
      const ins = this._insets(bbox);
      const pad = !this.split ? ins : { l: 20, r: 20, t: 150, b: ins.b };
      const aw = Math.max(80, vpw - pad.l - pad.r), ah = Math.max(80, vph - pad.t - pad.b);
      const s = Math.min(aw / bw, ah / bh);
      // centre of the padded area, mapped back to world coordinates
      const cxs = pad.l + aw / 2, cys = pad.t + ah / 2;
      const mx = (bbox[0] + bbox[2]) / 2, my = (bbox[1] + bbox[3]) / 2;
      return { s, cx: mx - (cxs - vpw / 2) / s, cy: my + (cys - vph / 2) / s };
    }

    fitBounds(bbox, animate) {
      const vp = this.viewports()[0];
      const target = this.camFor(bbox, vp.w, vp.h);
      this.flyTo(target, animate);
    }

    /** Fit all visible routes (or the network when there is none). */
    fitDefault(animate) {
      const bb = this.app.defaultBBox() || this.model.bbox;
      this.fitBounds(bb, animate);
    }

    fitSelected(animate) {
      const r = this.app.selectedRoute();
      this.fitBounds((r && r._bbox && r._ok) ? r._bbox : (this.app.visibleBBox() || this.model.bbox), animate);
    }

    _limits(c) {
      const [x0, y0, x1, y1] = this.model.bbox;
      const span = Math.max(x1 - x0, y1 - y0, 2000);
      const sMin = Math.min(this.w, this.h) / (span * 4);
      const sMax = 0.25;   // 4 m per pixel
      c.s = U.clamp(c.s, sMin, sMax);
      c.cx = U.clamp(c.cx, x0 - span * 0.6, x1 + span * 0.6);
      c.cy = U.clamp(c.cy, y0 - span * 0.6, y1 + span * 0.6);
      return c;
    }

    setCam(c) {
      this.cam = this._limits({ cx: c.cx, cy: c.cy, s: c.s });
      this.invalidate();
    }

    /** Smooth camera move (log-space zoom); instant with reduced motion. */
    flyTo(target, animate) {
      target = this._limits(Object.assign({}, target));
      if (!animate || U.prefersReducedMotion() || !this.cam.s) { this._fly = null; this.setCam(target); return; }
      const from = Object.assign({}, this.cam);
      const t0 = performance.now(), dur = 520;
      this._fly = { from, target, t0, dur };
      const step = (now) => {
        if (!this._fly || this._fly.t0 !== t0) return;
        const k = U.clamp((now - t0) / dur, 0, 1);
        const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
        const ls = U.lerp(Math.log(from.s), Math.log(target.s), e);
        this.cam = { cx: U.lerp(from.cx, target.cx, e), cy: U.lerp(from.cy, target.cy, e), s: Math.exp(ls) };
        this.invalidate();
        if (k < 1) requestAnimationFrame(step); else this._fly = null;
      };
      requestAnimationFrame(step);
    }

    zoomAt(factor, sx, sy) {
      this._fly = null;
      this.userMoved = true;
      const [wx, wy] = this.screenToWorld(sx, sy);
      const c = this._limits({ cx: this.cam.cx, cy: this.cam.cy, s: this.cam.s * factor });
      // keep the world point under the cursor fixed
      const vp = this.viewportAt(sx, sy);
      const lx = sx - vp.x, ly = sy - vp.y;
      c.cx = wx - (lx - vp.w / 2) / c.s;
      c.cy = wy + (ly - vp.h / 2 - (vp.cyOff || 0)) / c.s;
      this.setCam(c);
    }

    zoomBy(factor) {
      this.userMoved = true;
      const c = Object.assign({}, this.cam, { s: this.cam.s * factor });
      this.flyTo(c, true);
    }

    /* ---------------------------------------------------------- render */
    invalidate() {
      this.staticDirty = true;
      this.requestRender();
    }

    requestRender() {
      if (this._raf) return;
      this._raf = requestAnimationFrame(() => { this._raf = 0; this.render(); });
    }

    render() {
      const vps = this.viewports();
      if (this.staticDirty) {
        // the lower stacked half is offset (cyOff): it needs its own cached layers
        if (this._ownLayer(vps, vps[1])) this._renderStatic(vps[1], this.staticB, this.labelsB);
        this._renderStatic(vps[0], this.static, this.labels);
      }
      const ctx = this.ctx, dpr = this.dpr;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      for (const vp of vps) {
        const T = this.T(vp);
        const own = this._ownLayer(vps, vp);
        // 1. cached base layer
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(own ? this.staticB : this.static, Math.round(vp.x * dpr), Math.round(vp.y * dpr));
        ctx.setTransform(dpr, 0, 0, dpr, vp.x * dpr, vp.y * dpr);
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, 0, vp.w, vp.h);
        ctx.clip();
        // 2. dynamic layer under the labels: trails, charging rings, search dots
        if (this.overlay) this.overlay(ctx, vp, T, 'under');
        // 3. cached labels and pins
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(own ? this.labelsB : this.labels, Math.round(vp.x * dpr), Math.round(vp.y * dpr));
        ctx.setTransform(dpr, 0, 0, dpr, vp.x * dpr, vp.y * dpr);
        // 4. dynamic layer on top: stop badges, cars, name tags, hover
        if (this.overlay) this.overlay(ctx, vp, T, 'over');
        this._drawHover(ctx, T);
        ctx.restore();
      }
      if (this.split) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.fillStyle = this.app.theme.border;
        if (this.splitRows) ctx.fillRect(0, vps[1].y - 1, this.w, 2);
        else ctx.fillRect(vps[1].x - 1, 0, 2, this.h);
      }
    }

    /** True when viewport `vp` is offset differently from the first one (needs its own cached layers). */
    _ownLayer(vps, vp) {
      return !!vp && vp.i === 1 && (vp.cyOff || 0) !== (vps[0].cyOff || 0);
    }

    _layer(c, vp) {
      const dpr = this.dpr;
      const pw = Math.round(vp.w * dpr), ph = Math.round(vp.h * dpr);
      if (c.width !== pw || c.height !== ph) { c.width = pw; c.height = ph; }
      const ctx = c.getContext('2d');
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, pw, ph);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return ctx;
    }

    _renderStatic(vp, staticCanvas, labelCanvas) {
      this.staticDirty = false;
      const T = this.T(vp);
      const app = this.app, th = app.theme, L = app.layers;
      const ctx = this._layer(staticCanvas, vp);

      // background: water + shaded relief (fading to flat land when zoomed in
      // beyond the resolution of the interpolated elevation grid), or flat land
      if (L.terrain) {
        ctx.fillStyle = th.mapWater;
        ctx.fillRect(0, 0, vp.w, vp.h);
        if (!this.terrainImg) this.terrainImg = EVR.terrain.build(this.model, th);
        const ti = this.terrainImg;
        const [x0, y0, x1, y1] = ti.bbox;
        const dx = T.ox + x0 * T.s, dy = T.oy - y1 * T.s, dw = (x1 - x0) * T.s, dh = (y1 - y0) * T.s;
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        const pxPerCell = ti.cell * T.s;
        const fade = U.smoothstep(10, 36, pxPerCell) * 0.75;
        if (fade > 0) ctx.drawImage(ti.flat, dx, dy, dw, dh);
        ctx.globalAlpha = 1 - fade;
        ctx.drawImage(ti.canvas, dx, dy, dw, dh);
        ctx.globalAlpha = 1;
      } else {
        ctx.fillStyle = th.mapLand;
        ctx.fillRect(0, 0, vp.w, vp.h);
      }
      this._drawRoads(ctx, T);
      app.drawStaticRoutes(ctx, T);
      if (L.stations) this._drawStations(ctx, T);
      app.drawStaticMarkers(ctx, T);

      const lc = this._layer(labelCanvas, vp);
      const panels = this.split ? [] : this.overlayBoxes();
      const boxes = panels.concat([this._drawScale(lc, T, panels)]);
      const credit = this._placeAttribution(lc, T, panels);
      if (credit) boxes.push(credit.box);
      this._drawPins(lc, T);
      if (L.labels) this._drawLabels(lc, T, boxes);
      if (credit) this._drawAttribution(lc, credit);
    }

    /**
     * Screen boxes [x0, y0, x1, y1] (map CSS pixels) covered by the floating
     * panels -- HUD, map tools, search panel, playbar -- so that labels and
     * the scale bar are not placed underneath them.
     */
    overlayBoxes() {
      const host = this.canvas.getBoundingClientRect();
      const out = [];
      for (const id of ['hud', 'search-panel', 'playbar']) {
        const el = document.getElementById(id);
        if (!el || el.hidden) continue;
        const r = el.getBoundingClientRect();
        if (r.width && r.height) out.push([r.left - host.left - 4, r.top - host.top - 4, r.right - host.left + 4, r.bottom - host.top + 4]);
      }
      const tools = document.querySelector('.map-tools');
      if (tools) {
        const r = tools.getBoundingClientRect();
        out.push([r.left - host.left - 4, r.top - host.top - 4, r.right - host.left + 4, r.bottom - host.top + 4]);
      }
      return out;
    }

    zoomFactor() {
      const pxPerKm = this.cam.s * 1000;
      return U.clamp(0.78 + 0.26 * Math.log2(pxPerKm / 0.55), 0.62, 2.3);
    }

    _drawRoads(ctx, T) {
      const m = this.model, th = this.app.theme;
      const X = m.X, Y = m.Y;
      const s = T.s, ox = T.ox, oy = T.oy;
      const z = this.zoomFactor();
      const pad = 20, w = T.w + pad, h = T.h + pad;
      const pxPerKm = s * 1000;
      // a large network seen from afar: thin lines without round ends and casings
      // (but the motorways'), which cost most of the stroking time
      const coarse = m.lod && pxPerKm < COARSE_PX_PER_KM;
      ctx.lineCap = coarse ? 'butt' : 'round';
      ctx.lineJoin = coarse ? 'bevel' : 'round';
      const fills = {
        motorway: th.roadMotorway, trunk: th.roadTrunk, primary: th.roadPrimary,
      };
      for (const g of m.roadGroups) {
        if (m.lod && pxPerKm < (MIN_PX_PER_KM[g.cls] || 0)) continue;
        const width = (ROAD_WIDTH[g.cls] || 1.4) * z;
        const path = new Path2D();
        // one polyline per chain; vertices closer than SUBPIXEL to the last drawn
        // one are skipped (the chain's ends always count)
        const P = g.chains.pts, O = g.chains.offs, B = g.chains.bbox;
        for (let c = 0, nc = O.length - 1; c < nc; c++) {
          const cx0 = ox + B[4 * c] * s, cx1 = ox + B[4 * c + 2] * s, cy0 = oy - B[4 * c + 3] * s, cy1 = oy - B[4 * c + 1] * s;
          if (cx1 < -pad || cx0 > w || cy1 < -pad || cy0 > h) continue;
          // a chain smaller than a pixel lies under the line ends of its neighbours
          if (m.lod && cx1 - cx0 < SUBPIXEL && cy1 - cy0 < SUBPIXEL) continue;
          const end = O[c + 1] - 1;
          let v = P[O[c]];
          let px = ox + X[v] * s, py = oy - Y[v] * s;
          path.moveTo(px, py);
          for (let i = O[c] + 1; i <= end; i++) {
            v = P[i];
            const x = ox + X[v] * s, y = oy - Y[v] * s;
            if (i < end && Math.abs(x - px) + Math.abs(y - py) < SUBPIXEL) continue;
            path.lineTo(x, y);
            px = x; py = y;
          }
        }
        if (!coarse || g.cls === 'motorway') {
          ctx.strokeStyle = th.roadCasing;
          ctx.globalAlpha = th.dark ? 0.9 : 0.85;
          ctx.lineWidth = width + (g.cls === 'motorway' ? 2.2 : 1.6);
          ctx.stroke(path);
          ctx.globalAlpha = 1;
        }
        ctx.strokeStyle = fills[g.cls] || th.roadMinor;
        ctx.lineWidth = width;
        ctx.stroke(path);
      }
    }

    /** Draw a route polyline from node 0 up to node `upto` (inclusive) plus an optional tip point. */
    tracePath(ctx, r, T, upto, tip) {
      const xy = r._xy, s = T.s, ox = T.ox, oy = T.oy;
      const L = r.nodes.length;
      const last = upto === undefined ? L - 1 : Math.min(upto, L - 1);
      ctx.beginPath();
      ctx.moveTo(ox + xy[0] * s, oy - xy[1] * s);
      let px = ox + xy[0] * s, py = oy - xy[1] * s;
      for (let i = 1; i <= last; i++) {
        const x = ox + xy[2 * i] * s, y = oy - xy[2 * i + 1] * s;
        // skip sub-pixel segments: long routes stay cheap when zoomed out
        if (Math.abs(x - px) + Math.abs(y - py) < 0.6 && i < last) continue;
        ctx.lineTo(x, y);
        px = x; py = y;
      }
      if (tip) ctx.lineTo(ox + tip[0] * s, oy - tip[1] * s);
    }

    /** Route line with casing; `alpha` multiplies both. */
    strokeRoute(ctx, r, T, opt) {
      const th = this.app.theme;
      const color = opt.color;
      const width = opt.width;
      ctx.save();
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      if (opt.strand && r._strand) {
        // infeasible: up to the point where the battery is empty
        const g = r._tl.segs[r._strand.seg];
        this.tracePath(ctx, r, T, g.i, [r._strand.x, r._strand.y]);
      } else {
        this.tracePath(ctx, r, T, opt.upto, opt.tip);
      }
      ctx.globalAlpha = (opt.alpha === undefined ? 1 : opt.alpha) * (opt.casingAlpha === undefined ? 1 : opt.casingAlpha);
      ctx.strokeStyle = th.routeCasing;
      ctx.lineWidth = width + (opt.casing === undefined ? 3 : opt.casing);
      if (opt.dash) ctx.setLineDash([]);
      ctx.stroke();
      ctx.globalAlpha = opt.alpha === undefined ? 1 : opt.alpha;
      if (opt.glow && !U.prefersReducedMotion()) {
        ctx.shadowColor = U.rgba(color, 0.55);
        ctx.shadowBlur = 10;
      }
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      if (opt.dash) ctx.setLineDash(opt.dash);
      ctx.stroke();
      ctx.restore();
    }

    _stationNodes() {
      // one icon per node: the most powerful class there the vehicle can use
      // (hollow when the vehicle has no compatible connector at the node)
      if (this._stNodes && this._stNodesModel === this.model) return this._stNodes;
      const order = { AC: 0, DC: 1, HPC: 2 };
      const ok = (st) => st.usable !== false;
      const out = [];
      for (const [node, idxs] of this.model.stationsAt) {
        let best = idxs[0];
        for (const i of idxs) {
          const a = this.model.stations[i], b = this.model.stations[best];
          if (ok(a) !== ok(b)) { if (ok(a)) best = i; continue; }
          if (order[a.cls] > order[b.cls] || (a.cls === b.cls && a.power_kw > b.power_kw)) best = i;
        }
        out.push({ node, best, n: idxs.length, off: !ok(this.model.stations[best]) });
      }
      out.sort((a, b) => (b.off - a.off) || order[this.model.stations[a.best].cls] - order[this.model.stations[b.best].cls]);
      this._stNodes = out;
      this._stNodesModel = this.model;
      return out;
    }

    stationColor(cls) {
      const th = this.app.theme;
      return cls === 'HPC' ? th.stHPC : cls === 'DC' ? th.stDC : th.stAC;
    }

    stationRadius() {
      const pxPerKm = this.cam.s * 1000;
      return U.clamp(1.4 + pxPerKm * 1.9, 2.3, 8.5);
    }

    /**
     * Station icon: AC circle, DC rounded square, HPC diamond; bolt when
     * large.  `off`: the vehicle cannot plug in there -- a hollow, greyed icon.
     */
    drawStationIcon(ctx, x, y, cls, r, off) {
      const th = this.app.theme;
      ctx.fillStyle = off ? th.surface : this.stationColor(cls);
      ctx.strokeStyle = off ? U.mix(this.stationColor(cls), th.text3, 0.55) : th.stStroke;
      ctx.lineWidth = off ? (r > 5 ? 1.8 : 1.3) : r > 5 ? 1.6 : r > 3.2 ? 1.1 : 0.9;
      if (cls === 'AC') {
        ctx.beginPath();
        ctx.arc(x, y, r * 0.92, 0, Math.PI * 2);
      } else if (cls === 'DC') {
        U.roundRect(ctx, x - r, y - r, 2 * r, 2 * r, r * 0.42);
      } else {
        const q = r * 1.28;
        ctx.beginPath();
        ctx.moveTo(x, y - q); ctx.lineTo(x + q, y); ctx.lineTo(x, y + q); ctx.lineTo(x - q, y);
        ctx.closePath();
      }
      ctx.fill();
      ctx.stroke();
      if (r >= 5.5 && !off) {
        ctx.fillStyle = '#fff';
        U.bolt(ctx, x, y, r * 1.35);
        ctx.fill();
      }
    }

    _drawStations(ctx, T) {
      const m = this.model;
      const s = T.s, ox = T.ox, oy = T.oy;
      const r = this.stationRadius();
      const hits = [];
      ctx.save();
      ctx.shadowColor = 'rgba(0,0,0,.18)';
      ctx.shadowBlur = r > 5 ? 3 : 0;
      // overview zoom: small and slightly translucent, the network stays readable
      ctx.globalAlpha = U.clamp(0.55 + (r - 2.3) * 0.25, 0.55, 1);
      for (const sn of this._stationNodes()) {
        const x = ox + m.X[sn.node] * s, y = oy - m.Y[sn.node] * s;
        if (x < -10 || y < -10 || x > T.w + 10 || y > T.h + 10) continue;
        const st = m.stations[sn.best];
        if (sn.off) ctx.globalAlpha *= 0.8;
        this.drawStationIcon(ctx, x, y, st.cls, sn.off ? r * 0.9 : r, sn.off);
        if (sn.off) ctx.globalAlpha /= 0.8;
        hits.push([x, y, sn.node]);
      }
      ctx.restore();
      this.stationHits = hits;
    }

    /** Teardrop pins at origin (A) and destination (B). */
    _drawPins(ctx, T) {
      const m = this.model, th = this.app.theme;
      const q = this.app.currentQuery();
      const pins = [[q.origin, th.pinA, 'A'], [q.destination, th.pinB, 'B']];
      for (const [node, color, letter] of pins) {
        if (!(node >= 0 && node < m.n)) continue;
        const x = T.ox + m.X[node] * T.s, y = T.oy - m.Y[node] * T.s;
        drawPin(ctx, x, y, color, letter);
      }
    }

    /** Place-name labels with greedy collision avoidance (most important first). */
    _drawLabels(ctx, T, reserved) {
      const m = this.model, th = this.app.theme;
      const kmPerPx = 1 / (T.s * 1000);
      const q = this.app.currentQuery();
      const boxes = (reserved || []).slice();
      const font = th.font;
      const collide = (b) => {
        if (b[0] < 2 || b[1] < 2 || b[2] > T.w - 2 || b[3] > T.h - 2) return true;
        for (const o of boxes) if (b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]) return true;
        return false;
      };
      // reserve the pins and the numbered stop badges
      for (const node of [q.origin, q.destination]) {
        if (!(node >= 0 && node < m.n)) continue;
        const x = T.ox + m.X[node] * T.s, y = T.oy - m.Y[node] * T.s;
        boxes.push([x - 12, y - 34, x + 12, y + 2]);
      }
      for (const node of this.app.markerNodes()) {
        const x = T.ox + m.X[node] * T.s, y = T.oy - m.Y[node] * T.s;
        boxes.push([x - 13, y - 13, x + 13, y + 13]);
      }
      const maxRank = kmPerPx > 3.2 ? 1 : kmPerPx > 1.7 ? 2 : kmPerPx > 0.9 ? 3 : kmPerPx > 0.45 ? 5 : 6;
      const cands = [];
      for (const p of m.places) {
        const od = p.node === q.origin || p.node === q.destination;
        const rank = od ? 0 : p.rank;
        if (rank > maxRank) continue;
        if (p.kind === 'pass' && kmPerPx > 1.2) continue;
        cands.push([rank, p, od]);
      }
      if (!m.places.length) {
        for (const k of Object.keys(m.nodeName)) {
          const node = +k;
          const od = node === q.origin || node === q.destination;
          cands.push([od ? 0 : 2, { node, name: m.nodeName[k], kind: 'city', tier: 2 }, od]);
        }
      }
      cands.sort((a, b) => a[0] - b[0]);
      ctx.textBaseline = 'middle';
      ctx.lineJoin = 'round';
      for (const [rank, p, od] of cands) {
        const x = T.ox + m.X[p.node] * T.s, y = T.oy - m.Y[p.node] * T.s;
        if (x < -40 || y < -20 || x > T.w + 40 || y > T.h + 20) continue;
        const isPass = p.kind === 'pass';
        const size = od ? 13 : rank <= 1 ? 12.5 : rank === 2 ? 12 : rank <= 4 ? 11 : 10.5;
        const weight = od || rank <= 2 ? 650 : rank <= 4 ? 550 : 500;
        ctx.font = `${isPass ? 'italic ' : ''}${weight} ${size}px ${font}`;
        let text = p.name;
        if (isPass) text = `▲ ${p.name}`;
        const tw = ctx.measureText(text).width;
        const hgt = size + 2;
        const dot = rank <= 3 && !isPass && !od;
        const off = od ? 14 : dot ? 7 : 4;
        const tries = od
          ? [[x + off, y - 18, 'left'], [x - off, y - 18, 'right'], [x - tw / 2, y + 12, 'left']]
          : [[x + off, y, 'left'], [x - off, y, 'right'], [x - tw / 2, y - 12, 'left'], [x - tw / 2, y + 12, 'left']];
        // the place dot itself must be free too (e.g. not on the scale bar)
        if (dot && collide([x - 4, y - 4, x + 4, y + 4])) continue;
        let placed = null;
        for (const [tx, ty, align] of tries) {
          const bx0 = align === 'left' ? tx : tx - tw;
          const b = [bx0 - 2, ty - hgt / 2, bx0 + tw + 2, ty + hgt / 2];
          if (!collide(b)) { placed = [bx0, ty, b]; break; }
        }
        if (!placed) continue;
        boxes.push(placed[2]);
        if (dot) {
          ctx.beginPath();
          ctx.arc(x, y, rank <= 1 ? 3.2 : 2.6, 0, Math.PI * 2);
          ctx.fillStyle = th.labelHalo;
          ctx.fill();
          ctx.lineWidth = 1.5;
          ctx.strokeStyle = th.label;
          ctx.stroke();
          boxes.push([x - 4, y - 4, x + 4, y + 4]);
        }
        ctx.textAlign = 'left';
        ctx.lineWidth = 3.2;
        ctx.strokeStyle = th.labelHalo;
        ctx.strokeText(text, placed[0], placed[1]);
        ctx.fillStyle = rank <= 2 || od ? th.label : th.labelMinor;
        ctx.fillText(text, placed[0], placed[1]);
      }
    }

    /** Scale bar at the bottom left, above the playbar when it is shown. */
    _drawScale(ctx, T, panels) {
      const th = this.app.theme;
      const target = 90 / T.s;            // metres in ~90 px
      const pow = Math.pow(10, Math.floor(Math.log10(target)));
      const nice = [1, 2, 5, 10].map((k) => k * pow).filter((v) => v <= target).pop() || pow;
      const px = nice * T.s;
      let y = T.h - 16;
      for (const b of panels || []) if (b[0] < 16 + px && b[2] > 16 && b[3] > T.h - 40) y = Math.min(y, b[1] - 8);
      const x = 16;
      ctx.save();
      ctx.lineWidth = 4;
      ctx.strokeStyle = th.labelHalo;
      ctx.beginPath(); ctx.moveTo(x, y - 5); ctx.lineTo(x, y); ctx.lineTo(x + px, y); ctx.lineTo(x + px, y - 5); ctx.stroke();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = th.labelMinor;
      ctx.stroke();
      ctx.font = `600 11px ${th.font}`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      const label = nice >= 1000 ? `${I.num(nice / 1000, 0)} km` : `${I.num(nice, 0)} m`;
      ctx.lineWidth = 3;
      ctx.strokeStyle = th.labelHalo;
      ctx.strokeText(label, x + px / 2, y - 3);
      ctx.fillStyle = th.labelMinor;
      ctx.fillText(label, x + px / 2, y - 3);
      ctx.restore();
      return [x - 4, y - 18, x + px + 4, y + 3];
    }

    /**
     * The data credit of the scenario (``scenario.meta.attribution``, e.g.
     * "© OpenStreetMap contributors" -- required by the ODbL for maps made
     * from OpenStreetMap data): bottom-right, above the floating panels.
     * Returns {text, x, y, box} (box: the screen box labels must avoid), or
     * null without a credit; _drawAttribution draws it on top of the layer.
     */
    _placeAttribution(ctx, T, panels) {
      const meta = (this.model.scenario && this.model.scenario.meta) || {};
      const text = meta.attribution;
      if (!text) return null;
      ctx.save();
      ctx.font = `500 10.5px ${this.app.theme.font}`;
      const tw = ctx.measureText(text).width;
      ctx.restore();
      const x = T.w - 10;
      let y = T.h - 6;
      for (const b of panels || []) if (b[0] < x + 4 && b[2] > x - tw - 4 && b[3] > y - 16) y = Math.min(y, b[1] - 3);
      return { text, x, y, w: tw, box: [x - tw - 6, y - 15, x + 6, y + 3] };
    }

    _drawAttribution(ctx, a) {
      const th = this.app.theme;
      ctx.save();
      ctx.font = `500 10.5px ${th.font}`;
      ctx.fillStyle = th.labelHalo;
      U.roundRect(ctx, a.x - a.w - 5, a.y - 14, a.w + 10, 16, 5);
      ctx.fill();
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillStyle = th.labelMinor;
      ctx.fillText(a.text, a.x, a.y);
      ctx.restore();
    }

    _drawHover(ctx, T) {
      if (this.hoverNode < 0) return;
      const m = this.model;
      const x = T.ox + m.X[this.hoverNode] * T.s, y = T.oy - m.Y[this.hoverNode] * T.s;
      const r = this.stationRadius() + 5;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.lineWidth = 2;
      ctx.strokeStyle = this.app.theme.accent;
      ctx.stroke();
    }

    /* -------------------------------------------------------- hit tests */
    stationAt(sx, sy) {
      if (!this.app.layers.stations || this.split) return -1;
      let best = -1, bd = 14 * 14;
      for (const [x, y, node] of this.stationHits) {
        const d = (x - sx) * (x - sx) + (y - sy) * (y - sy);
        if (d < bd) { bd = d; best = node; }
      }
      return best;
    }

    /** Index of the visible route closest to (sx, sy) within 9 px, or -1. */
    routeAt(sx, sy) {
      const T = this.T(this.viewports()[0]);
      let best = -1, bd = 9;
      for (const r of this.app.visibleRoutes()) {
        if (!r._ok) continue;
        const xy = r._xy;
        for (let i = 0; i + 1 < r.nodes.length; i++) {
          const ax = T.ox + xy[2 * i] * T.s, ay = T.oy - xy[2 * i + 1] * T.s;
          const bx = T.ox + xy[2 * i + 2] * T.s, by = T.oy - xy[2 * i + 3] * T.s;
          const d = segDist(sx, sy, ax, ay, bx, by);
          if (d < bd) { bd = d; best = r._ri; }
        }
      }
      return best;
    }

    /* ------------------------------------------------------------ events */
    _installEvents() {
      const cv = this.canvas;
      cv.addEventListener('pointerdown', (ev) => {
        if (ev.button !== undefined && ev.button > 0) return;
        cv.setPointerCapture(ev.pointerId);
        this._pointers.set(ev.pointerId, { x: ev.offsetX, y: ev.offsetY });
        this._fly = null;
        if (this._pointers.size === 1) {
          this._drag = { x: ev.offsetX, y: ev.offsetY, cam: Object.assign({}, this.cam), t: performance.now(), moved: false };
        } else if (this._pointers.size === 2) {
          const [a, b] = Array.from(this._pointers.values());
          this.userMoved = true;
          this._pinch = { d: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, cam: Object.assign({}, this.cam) };
          if (this._drag) this._drag.moved = true;
        }
      });
      cv.addEventListener('pointermove', (ev) => {
        const p = this._pointers.get(ev.pointerId);
        if (p) { p.x = ev.offsetX; p.y = ev.offsetY; }
        if (this._pinch && this._pointers.size >= 2) {
          const [a, b] = Array.from(this._pointers.values());
          const d = Math.hypot(a.x - b.x, a.y - b.y);
          const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
          const pc = this._pinch.cam;
          const s = pc.s * (d / Math.max(1, this._pinch.d));
          // world point under the initial midpoint stays under the current midpoint
          const vp = this.viewports()[0];
          const wx = pc.cx + (this._pinch.mx - vp.w / 2) / pc.s;
          const wy = pc.cy - (this._pinch.my - vp.h / 2) / pc.s;
          this.setCam({ s, cx: wx - (mx - vp.w / 2) / s, cy: wy + (my - vp.h / 2) / s });
          return;
        }
        if (this._drag && p) {
          const dx = ev.offsetX - this._drag.x, dy = ev.offsetY - this._drag.y;
          if (!this._drag.moved && Math.abs(dx) + Math.abs(dy) > 4) {
            this._drag.moved = true;
            this.userMoved = true;
            cv.classList.add('dragging');
            this.app.hideTip();
          }
          if (this._drag.moved) {
            const c = this._drag.cam;
            this.setCam({ s: c.s, cx: c.cx - dx / c.s, cy: c.cy + dy / c.s });
          }
          return;
        }
        if (ev.pointerType === 'mouse') this._hover(ev.offsetX, ev.offsetY);
      });
      const end = (ev) => {
        const had = this._pointers.has(ev.pointerId);
        this._pointers.delete(ev.pointerId);
        if (this._pointers.size < 2) this._pinch = null;
        if (!had) return;
        if (this._drag && this._pointers.size === 0) {
          const d = this._drag;
          this._drag = null;
          cv.classList.remove('dragging');
          if (!d.moved && ev.type === 'pointerup' && performance.now() - d.t < 600) this._click(ev.offsetX, ev.offsetY, ev);
        }
      };
      cv.addEventListener('pointerup', end);
      cv.addEventListener('pointercancel', end);
      cv.addEventListener('pointerleave', () => {
        if (this.hoverNode >= 0) { this.hoverNode = -1; this.requestRender(); }
        this.app.hideTip();
        cv.classList.remove('hovering');
      });
      cv.addEventListener('wheel', (ev) => {
        ev.preventDefault();
        const k = ev.deltaMode === 1 ? 0.05 : 0.0018;
        this.zoomAt(Math.exp(-ev.deltaY * k), ev.offsetX, ev.offsetY);
      }, { passive: false });
      cv.addEventListener('dblclick', (ev) => { ev.preventDefault(); this.zoomAt(2, ev.offsetX, ev.offsetY); });
    }

    _hover(sx, sy) {
      const node = this.stationAt(sx, sy);
      if (node !== this.hoverNode) {
        this.hoverNode = node;
        this.requestRender();
      }
      if (node >= 0) {
        this.canvas.classList.add('hovering');
        this.app.showStationTip(node, sx, sy);
      } else {
        this.app.hideTip();
        const onRoute = !this.app.live && this.routeAt(sx, sy) >= 0;
        this.canvas.classList.toggle('hovering', onRoute);
      }
    }

    _click(sx, sy, ev) {
      const [wx, wy] = this.screenToWorld(sx, sy);
      if (this.app.live && this.app.liveCtl && this.app.liveCtl.onMapClick(wx, wy, ev.shiftKey)) return;
      const node = this.stationAt(sx, sy);
      if (node >= 0) { this.app.pinStationNode(node); return; }
      const ri = this.routeAt(sx, sy);
      if (ri >= 0) this.app.select(ri, -1);
    }
  }

  function segDist(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay;
    const l2 = dx * dx + dy * dy;
    let t = l2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
  }

  /**
   * Teardrop map pin whose tip is at (x, y).  The pin is snapped to the
   * device-pixel grid, so its circle and letter are drawn the same way
   * wherever it sits (the letter stays visibly centred).
   */
  function drawPin(ctx, x, y, color, letter) {
    const dpr = U.dpr();
    x = Math.round(x * dpr) / dpr;
    y = Math.round(y * dpr) / dpr;
    const r = 10.5, cy = y - 19;
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,.28)';
    ctx.shadowBlur = 6;
    ctx.shadowOffsetY = 2;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.bezierCurveTo(x - 4, y - 7, x - r, cy + 6, x - r, cy);
    ctx.arc(x, cy, r, Math.PI, 0, false);
    ctx.bezierCurveTo(x + r, cy + 6, x + 4, y - 7, x, y);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#fff';
    ctx.stroke();
    // the letter as an outline whose ink is centred on the circle's centre
    // (U.GLYPH), not as text: a font's glyph sits higher or lower
    ctx.fillStyle = '#fff';
    const k = PIN_GLYPH_SCALE;
    ctx.translate(x - 8 * k, cy - 8 * k);
    ctx.scale(k, k);
    ctx.fill(glyphPath(letter), 'evenodd');
    ctx.restore();
  }

  /** Scale of the 16 x 16 glyph box in a pin (letter height 8 * k px). */
  const PIN_GLYPH_SCALE = 1.1;
  const _glyphs = {};
  function glyphPath(letter) {
    const key = letter === 'B' ? 'B' : 'A';
    if (!_glyphs[key]) _glyphs[key] = new Path2D(U.GLYPH[key]);
    return _glyphs[key];
  }

  EVR.map = { MapView, drawPin, segDist };
})(window.EVR = window.EVR || {});
