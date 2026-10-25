/* EVRoute viewer -- app.js
 *
 * Application state and wiring: selection (result + optional Pareto
 * alternative), route visibility, layers, theme, language, keyboard
 * shortcuts, tooltips and the boot sequence.  It reads the payload from the
 * <script id="evroute-data" type="application/json"> block (or, if that is
 * empty, from api/scenario when served).
 *
 * Keyboard: Space play/pause · A animation on/off · R race · S search
 * replay · ←/→ scrub (Shift: larger steps) · F fit route · +/- zoom.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;

  class App {
    constructor(payload) {
      this.ready = false;
      this.payload = payload;
      this.live = !!payload.live;
      this.model = new EVR.model.Model(payload);
      this.layers = { terrain: true, stations: true, labels: true, others: true };
      this.visible = new Set(this.model.results.map((r, i) => i));
      this.selection = { ri: this._defaultSelection(), ai: -1 };
      this.liveQuery = null;
      this.waitPinned = -1;
      this.waitHover = -1;
      this.themePref = U.storage.get('evroute.theme', 'auto');
      this._applyThemeAttr();
      this.theme = U.readTheme();
      I.setLang(payload.lang === 'en' ? 'en' : 'tr');

      this.map = new EVR.map.MapView(this, U.$('#map'));
      this.anim = new EVR.anim.Animator(this);
      this.map.overlay = (ctx, vp, T, phase) => this.anim.overlay(ctx, vp, T, phase);
      this.charts = {
        soc: new EVR.charts.SocChart(this, U.$('#chart-soc')),
        time: new EVR.charts.TimeChart(this, U.$('#chart-time')),
        pareto: new EVR.charts.ParetoChart(this, U.$('#chart-pareto')),
        wait: new EVR.charts.WaitChart(this, U.$('#chart-wait')),
      };
      this.panels = new EVR.panels.Panels(this);
      this.tipEl = U.$('#map-tip');
      this._wireHeader();
      this._wireMapTools();
      this._wireKeyboard();
      this.liveCtl = this.live ? new EVR.live.Live(this) : null;
      this.ready = true;

      this.applyLang();
      const animate = !!payload.animate && !U.prefersReducedMotion();
      this.anim.setEnabled(animate, false);
      this.map.fitDefault(false);
      if (animate && this.model.results.length) this.anim.play();
      if (this.live && !this.model.results.length) this.liveCtl.plan();
    }

    /* ------------------------------------------------------------ selection */
    _defaultSelection() {
      const res = this.model.results;
      if (!res.length) return 0;
      const flagship = res.findIndex((r) => r.algorithm === 'bls-astar' && r.feasible);
      if (flagship >= 0) return flagship;
      let best = -1;
      res.forEach((r, i) => {
        if (r.feasible && (best < 0 || r.objective_value < res[best].objective_value)) best = i;
      });
      return best >= 0 ? best : 0;
    }

    selectedRoute() {
      const s = this.selection;
      if (s.ai >= 0) return this.model.alternative(s.ri, s.ai) || this.model.results[s.ri] || null;
      return this.model.results[s.ri] || null;
    }

    /** Visible routes in result order (a selected Pareto alternative is appended). */
    visibleRoutes() {
      const out = this.model.results.filter((r, i) => this.visible.has(i));
      if (this.selection.ai >= 0) {
        const a = this.selectedRoute();
        if (a && !out.includes(a)) out.push(a);
      }
      return out;
    }

    select(ri, ai) {
      if (!this.model.results[ri]) return;
      const same = this.selection.ri === ri && this.selection.ai === ai;
      this.selection = { ri, ai: ai === undefined ? -1 : ai };
      this.visible.add(ri);
      if (same) return;
      this.onSelectionChanged();
      const r = this.selectedRoute();
      if (r) this.toast(I.t('toast_select', { a: r.title }));
    }

    setVisible(ri, on) {
      if (on) this.visible.add(ri); else this.visible.delete(ri);
      if (!on && this.selection.ri === ri) {
        const next = this.model.results.findIndex((r, i) => this.visible.has(i) && r.feasible);
        if (next >= 0) { this.selection = { ri: next, ai: -1 }; this.onSelectionChanged(); return; }
      }
      this.panels.renderLegends();
      this.redrawAll();
      if (this.anim.enabled && this.anim.mode === 'race') this.anim.buildTimeline();
    }

    onSelectionChanged() {
      this.panels.renderAll();
      this.anim.buildTimeline();
      if (this.anim.enabled) {
        if (this.anim.mode === 'trip') this.anim.restart();
        else if (this.anim.mode === 'search') {
          if (this.selectedRoute() && this.selectedRoute()._trace) { this.anim._fillCompare(); this.anim._applySplit(); this.anim.restart(); }
          else this.anim.setMode('trip', false);
        }
      }
      this.redrawAll();
    }

    onAnimationChanged() {
      this.map.invalidate();
      this.anim.buildTimeline();
      this.anim.frame();
      this.redrawCharts();
    }

    redrawCharts() {
      for (const c of Object.values(this.charts)) c.redraw();
    }

    redrawAll() {
      this.map.invalidate();
      this.redrawCharts();
    }

    /* -------------------------------------------------------------- queries */
    colorOf(r) { return (this.theme.dark ? r.color_dark : r.color) || r.color || '#888'; }

    algoInfo(name) { return (this.payload.algorithms || []).find((a) => a.name === name); }

    currentQuery() { return this.liveQuery || this.model.query; }

    currentPrefs() { return (this.model.scenario && this.model.scenario.preferences) || {}; }

    clockTod() {
      const a = this.anim;
      const t = a && a.enabled && a.mode !== 'search' ? a.t : 0;
      return this.model.departure + t;
    }

    bestFeasibleTime() {
      let b = null;
      for (const r of this.model.results) if (r.feasible && (b === null || r.total_time_s < b)) b = r.total_time_s;
      return b;
    }

    visibleBBox() {
      let bb = null;
      const grow = (x, y) => {
        if (!bb) bb = [x, y, x, y];
        else { bb[0] = Math.min(bb[0], x); bb[1] = Math.min(bb[1], y); bb[2] = Math.max(bb[2], x); bb[3] = Math.max(bb[3], y); }
      };
      for (const r of this.visibleRoutes()) if (r._bbox && r._ok) { grow(r._bbox[0], r._bbox[1]); grow(r._bbox[2], r._bbox[3]); }
      const q = this.currentQuery(), m = this.model;
      for (const v of [q.origin, q.destination]) if (v >= 0 && v < m.n) grow(m.X[v], m.Y[v]);
      if (!bb) return null;
      // never zoom in further than ~60 km across
      const cx = (bb[0] + bb[2]) / 2, cy = (bb[1] + bb[3]) / 2;
      const hw = Math.max(30000, (bb[2] - bb[0]) / 2), hh = Math.max(30000, (bb[3] - bb[1]) / 2);
      return [cx - hw, cy - hh, cx + hw, cy + hh];
    }

    /** Station shown in the 24-h queue chart: hovered > pinned > first stop. */
    waitStation() {
      if (this.waitHover >= 0) return this.waitHover;
      if (this.waitPinned >= 0) return this.waitPinned;
      const r = this.selectedRoute();
      if (r && r._stops && r._stops.length) return r._stops[0].station;
      return -1;
    }

    _mainStation(node) {
      const idxs = this.model.stationsAt.get(node) || [];
      let best = -1;
      for (const i of idxs) if (best < 0 || this.model.stations[i].power_kw > this.model.stations[best].power_kw) best = i;
      return best;
    }

    pinStationNode(node) {
      this.waitPinned = this._mainStation(node);
      this.charts.wait.redraw();
    }

    focusStop(k) {
      const r = this.selectedRoute();
      if (!r || !r._stops[k]) return;
      const s = r._stops[k];
      this.waitPinned = s.station;
      this.charts.wait.redraw();
      const m = this.model;
      const x = m.X[s.node], y = m.Y[s.node];
      const half = 85000;
      this.map.fitBounds([x - half, y - half, x + half, y + half], true);
      if (this.anim.enabled && this.anim.mode !== 'search') {
        if (this.anim.mode === 'race') this.anim.setMode('trip', false);
        this.anim.seek(s.arrival_s + 1);
      }
      this.panels.highlightStop(k);
    }

    setQuery(q) {
      this.liveQuery = Object.assign({}, this.currentQuery(), q);
      this.map.invalidate();
    }

    /** Nodes covered by static markers (stop badges) that labels must avoid. */
    markerNodes() {
      if (this.anim.enabled && this.anim.mode === 'search') return [];
      const r = this.selectedRoute();
      return r && r._ok ? r._stops.map((s) => s.node) : [];
    }

    /* ------------------------------------------------------- map delegates */
    drawStaticRoutes(ctx, T) { this.anim.drawStaticRoutes(ctx, T); }

    drawStaticMarkers(ctx, T) { this.anim.drawStaticMarkers(ctx, T); }

    showStationTip(node, sx, sy) {
      const tip = this.panels.stationTip(node);
      U.setChildren(this.tipEl, tip.kids);
      this.tipEl.hidden = false;
      EVR.charts.sparkline(tip.spark, tip.profile, tip.tod, this.theme);
      const host = this.tipEl.parentElement;
      const w = this.tipEl.offsetWidth, h = this.tipEl.offsetHeight;
      let x = sx + 16, y = sy + 16;
      if (x + w > host.clientWidth - 8) x = sx - w - 16;
      if (y + h > host.clientHeight - 8) y = sy - h - 16;
      this.tipEl.style.left = `${U.clamp(x, 8, Math.max(8, host.clientWidth - w - 8))}px`;
      this.tipEl.style.top = `${U.clamp(y, 8, Math.max(8, host.clientHeight - h - 8))}px`;
      const main = this._mainStation(node);
      if (main !== this.waitHover) { this.waitHover = main; this.charts.wait.redraw(); }
    }

    hideTip() {
      this.tipEl.hidden = true;
      if (this.waitHover !== -1) { this.waitHover = -1; this.charts.wait.redraw(); }
    }

    toast(msg) {
      const t = U.$('#toast');
      t.textContent = msg;
      t.classList.add('show');
      clearTimeout(this._toastT);
      this._toastT = setTimeout(() => t.classList.remove('show'), 1800);
    }

    /* ------------------------------------------------------ theme and lang */
    _applyThemeAttr() {
      let dark = this.themePref === 'dark';
      if (this.themePref === 'auto') {
        try { dark = window.matchMedia('(prefers-color-scheme: dark)').matches; } catch (e) { dark = false; }
      }
      document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    }

    setTheme(pref) {
      this.themePref = pref;
      U.storage.set('evroute.theme', pref);
      this._applyThemeAttr();
      this.theme = U.readTheme();
      this.map.terrainImg = null;
      this.anim._sprites = null;
      this.anim._acc = [null, null];
      for (const b of U.$$('#theme-seg button')) b.setAttribute('aria-pressed', String(b.dataset.themeChoice === pref));
      this.panels.renderAll();
      if (this.liveCtl) this.liveCtl.fill();
      this.redrawAll();
      this.anim.frame();
    }

    setLang(lang) {
      I.setLang(lang);
      this.applyLang();
    }

    applyLang() {
      I.apply(document);
      for (const b of U.$$('#lang-seg button')) b.setAttribute('aria-pressed', String(b.dataset.lang === I.lang));
      for (const b of U.$$('#theme-seg button')) b.setAttribute('aria-pressed', String(b.dataset.themeChoice === this.themePref));
      const sc = this.model.scenario || {};
      U.$('#title').textContent = this.payload.title || sc.name || 'EVRoute';
      const veh = (sc.vehicle || {}).name;
      const parts = [sc.name, veh, `${I.t('departs')} ${I.clock(this.currentQuery().departure_s || 0, false)}`].filter(Boolean);
      U.$('#subtitle').textContent = parts.join(' · ');
      const g = this.payload.generator || {};
      U.$('#footer-text').textContent = I.t('generated_by', { v: g.version || '' });
      this._speedLabels();
      U.$('#bottom').classList.toggle('no-pareto', !this._hasPareto());
      U.$('#pareto-card').hidden = !this._hasPareto();
      this.panels.renderAll();
      if (this.liveCtl) this.liveCtl.fill();
      this.anim.buildTimeline();
      this.anim.frame();
      this.redrawAll();
    }

    /** "1 h = 5 s" (wide screens) or "5 s/h" (phones). */
    _speedLabels() {
      const narrow = window.innerWidth < 560;
      const sec = I.lang === 'tr' ? 'sn' : 's';
      for (const o of U.$$('#sel-speed option')) {
        o.textContent = narrow ? `${o.value} ${sec}/${I.t('unit_h')}` : `1 ${I.t('unit_h')} = ${o.value} ${sec}`;
      }
    }

    _hasPareto() { return this.model.results.some((r) => r.pareto_points && r.pareto_points.length > 1); }

    /* ------------------------------------------------------- live updates */
    applyPlanResponse(data) {
      if (data.scenario) {
        this.model.scenario = data.scenario;
        this.model.query = data.scenario.query;
        this.model.departure = data.scenario.query.departure_s || 0;
        this.model.currency = data.scenario.currency || this.model.currency;
      }
      if (Array.isArray(data.wait_profiles)) {
        data.wait_profiles.forEach((p, i) => { if (this.model.stations[i]) this.model.stations[i].wait_profile = p; });
      }
      this.liveQuery = null;
      this.model.setResults(data.results || []);
      this.visible = new Set(this.model.results.map((r, i) => i));
      this.selection = { ri: this._defaultSelection(), ai: -1 };
      this.waitPinned = -1;
      this.applyLang();
      this.map.fitDefault(true);
      if (this.anim.enabled) {
        if (this.anim.mode === 'search' && !(this.selectedRoute() && this.selectedRoute()._trace)) this.anim.setMode('trip', false);
        this.anim.restart();
      }
    }

    loadPayload(payload) {
      payload.live = true;
      this.payload = payload;
      this.model = new EVR.model.Model(payload);
      this.visible = new Set(this.model.results.map((r, i) => i));
      this.selection = { ri: this._defaultSelection(), ai: -1 };
      this.liveQuery = null;
      this.waitPinned = -1;
      this.map.terrainImg = null;
      this.map._stNodes = null;
      this.anim._acc = [null, null];
      this.applyLang();
      this.map.fitDefault(false);
    }

    /* --------------------------------------------------------------- wiring */
    _wireHeader() {
      for (const b of U.$$('#lang-seg button')) b.addEventListener('click', () => this.setLang(b.dataset.lang));
      for (const b of U.$$('#theme-seg button')) b.addEventListener('click', () => this.setTheme(b.dataset.themeChoice));
      try {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (this.themePref === 'auto') this.setTheme('auto'); });
      } catch (e) { /* old browsers */ }
      const layer = (id, key) => {
        const cb = U.$(id);
        cb.checked = this.layers[key];
        cb.addEventListener('change', () => { this.layers[key] = cb.checked; this.map.invalidate(); });
      };
      layer('#layer-terrain', 'terrain');
      layer('#layer-stations', 'stations');
      layer('#layer-labels', 'labels');
      layer('#layer-others', 'others');
    }

    _wireMapTools() {
      U.$('#btn-zoom-in').addEventListener('click', () => this.map.zoomBy(1.6));
      U.$('#btn-zoom-out').addEventListener('click', () => this.map.zoomBy(1 / 1.6));
      U.$('#btn-fit').addEventListener('click', () => this.map.fitSelected(true));
      window.addEventListener('resize', U.debounce(() => { this.anim.buildTimeline(); this._speedLabels(); }, 150));
    }

    _wireKeyboard() {
      document.addEventListener('keydown', (ev) => {
        if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
        const tgt = ev.target || {};
        const tag = tgt.tagName || '';
        const typing = tag === 'TEXTAREA' || tag === 'SELECT' || tgt.isContentEditable ||
          (tag === 'INPUT' && !['checkbox', 'radio', 'range', 'button'].includes(tgt.type));
        if (typing) return;
        const activatable = tag === 'BUTTON' || tag === 'INPUT' || tag === 'LI' || tag === 'TR' || tag === 'A';
        const k = ev.key;
        if ((k === 'ArrowRight' || k === 'ArrowLeft') && tag === 'INPUT' && tgt.type === 'range') return;
        if (k === ' ' || k === 'Spacebar') {
          if (activatable) return;
          if (!this.anim.enabled) return;
          ev.preventDefault();
          this.anim.togglePlay();
        } else if (k === 'a' || k === 'A') {
          this.anim.setEnabled(!this.anim.enabled, true);
        } else if (k === 'r' || k === 'R') {
          this.anim.setMode(this.anim.enabled && this.anim.mode === 'race' ? 'trip' : 'race', true);
        } else if (k === 's' || k === 'S') {
          this.anim.setMode(this.anim.enabled && this.anim.mode === 'search' ? 'trip' : 'search', true);
        } else if (k === 'ArrowRight' || k === 'ArrowLeft') {
          if (!this.anim.enabled) return;
          ev.preventDefault();
          this.anim.step((k === 'ArrowRight' ? 1 : -1) * (ev.shiftKey ? 0.1 : 0.02));
        } else if (k === 'f' || k === 'F') {
          this.map.fitSelected(true);
        } else if (k === '+' || k === '=') {
          this.map.zoomBy(1.6);
        } else if (k === '-' || k === '_') {
          this.map.zoomBy(1 / 1.6);
        }
      });
    }
  }

  /* ----------------------------------------------------------------- boot */
  function readPayload() {
    const tag = document.getElementById('evroute-data');
    if (!tag) return null;
    try { return JSON.parse(tag.textContent || 'null'); } catch (e) { return null; }
  }

  function fatal(msg) {
    const el = document.getElementById('fatal');
    el.hidden = false;
    el.textContent = msg;
  }

  async function boot() {
    let payload = readPayload();
    if (!payload) {
      try {
        const res = await fetch('api/scenario');
        if (res.ok) payload = await res.json();
      } catch (e) { /* opened from disk without data */ }
    }
    if (!payload || payload.format !== 'evroute-viewer/1') {
      fatal(EVR.i18n.t('err_no_data'));
      return;
    }
    try {
      EVR.app = new App(payload);
    } catch (err) {
      fatal(`EVRoute viewer: ${err && err.message ? err.message : err}`);
      throw err;
    }
  }

  EVR.App = App;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window.EVR = window.EVR || {});
