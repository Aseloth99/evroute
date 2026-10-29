/* EVRoute viewer -- anim.js
 *
 * The animation controller (the ON/OFF switch in the header).
 *
 * OFF  Everything is static and complete: all routes drawn in full, no car,
 *      no timer and no requestAnimationFrame loop running.
 * ON   One simulated trip clock t (seconds after departure) drives
 *        trip    a car on the selected route, following the evaluator's
 *                node_arrive_s / node_depart_s, with trail, HUD, charging
 *                rings, queue state and the timeline scrubber;
 *        race    every visible route on the same clock with name tags and a
 *                live leaderboard (who arrives first);
 *        search  replay of the search trace of the selected algorithm: the
 *                settled nodes appear in settle order as glowing dots
 *                coloured by their key (viridis), the newest ones form the
 *                frontier; optionally side by side with a second algorithm
 *                (both replayed at the same nodes-per-second rate, so the
 *                one that settles fewer nodes finishes first).
 *
 * The loop runs only while playing and stops at the end of the replay.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;
  const M = EVR.model;

  class Animator {
    constructor(app) {
      this.app = app;
      this.enabled = false;
      this.mode = 'trip';
      this.playing = false;
      this.t = 0;                 // trip seconds
      this.sp = 0;                // search replay: settled events shown
      this.speedSec = 5;          // real seconds per trip hour
      this.compare = false;
      this.compareRi = -1;
      this._raf = 0;
      this._last = 0;
      this._acc = [null, null];   // search accumulation canvases per viewport
      this._sprites = null;
      this._lastWaitRedraw = -1e9;
      this._activeStop = -2;
      this.el = {
        toggle: U.$('#anim-toggle'), state: U.$('#anim-state'),
        playbar: U.$('#playbar'), hud: U.$('#hud'), play: U.$('#btn-play'), restart: U.$('#btn-restart'),
        speed: U.$('#sel-speed'), modeSeg: U.$('#mode-seg'), scrub: U.$('#scrubber'),
        track: U.$('#timeline-track'), ticks: U.$('#timeline-ticks'),
        clock: U.$('#hud-clock'), elapsed: U.$('#hud-elapsed'), soc: U.$('#hud-soc'),
        battery: U.$('#hud-battery'), fill: U.$('#hud-battery-fill'),
        dist: U.$('#hud-dist'), speedOut: U.$('#hud-speed'), elev: U.$('#hud-elev'),
        status: U.$('#hud-status'), race: U.$('#hud-race'),
        searchPanel: U.$('#search-panel'), counters: U.$('#search-counters'),
        cmp: U.$('#search-compare'), cmpWith: U.$('#search-compare-with'),
      };
      this._wire();
    }

    _wire() {
      const el = this.el;
      el.toggle.addEventListener('change', () => this.setEnabled(el.toggle.checked, true));
      el.play.addEventListener('click', () => this.togglePlay());
      el.restart.addEventListener('click', () => this.restart());
      el.speed.addEventListener('change', () => { this.speedSec = +el.speed.value || 5; });
      for (const b of el.modeSeg.querySelectorAll('button')) b.addEventListener('click', () => this.setMode(b.dataset.mode, true));
      el.scrub.addEventListener('input', () => {
        const f = (+el.scrub.value) / 1000;
        if (this.mode === 'search') this.sp = f * this._searchMax();
        else this.t = f * this.tMax();
        this.frame();
      });
      el.cmp.addEventListener('change', () => { this.compare = el.cmp.checked; this._applySplit(); this.frame(); });
      el.cmpWith.addEventListener('change', () => { this.compareRi = +el.cmpWith.value; this._acc = [null, null]; this._applySplit(); this.frame(); });
    }

    /* ------------------------------------------------------------ state */
    setEnabled(on, user) {
      on = !!on;
      this.enabled = on;
      const el = this.el;
      el.toggle.checked = on;
      el.toggle.setAttribute('aria-checked', String(on));
      el.state.textContent = on ? 'ON' : 'OFF';
      el.playbar.hidden = !on;
      document.getElementById('app').dataset.mode = on ? 'anim' : 'static';
      if (!on) {
        this.pause();
        this.app.map.split = false;
        el.hud.hidden = true;
        el.searchPanel.hidden = true;
      } else {
        this._applyModeUI();
        if (user) this.restart(); else this.seek(this.t);
      }
      // the playbar and the HUD change the free map area
      if (this.app.ready && !this.app.map.userMoved) this.app.map.fitDefault(!!user);
      this.app.onAnimationChanged();
      if (user) this.app.toast(I.t(on ? 'toast_anim_on' : 'toast_anim_off'));
    }

    setMode(mode, user) {
      if (!['trip', 'race', 'search'].includes(mode)) return;
      if (mode === 'search' && !this._hasTrace()) { this.app.toast(I.t('toast_no_trace')); return; }
      if (!this.enabled) this.setEnabled(true, false);
      const prev = this.mode;
      this.mode = mode;
      this._applyModeUI();
      this.app.map.invalidate();
      if (mode === 'search') {
        this.sp = 0;
        this._acc = [null, null];
        this._activeStop = -1;
        this.app.panels.highlightStop(-1);
        if (user) this.app.toast(I.t('toast_search', { a: this.app.selectedRoute().title }));
        this.play();
      } else if (prev === 'search' || user) {
        if (mode === 'race' && user) this.app.toast(I.t('toast_race'));
        this.restart();
      }
      // fit after the HUD shows its final content (the race leaderboard is tall)
      if (prev !== mode && !this.app.map.userMoved) this.app.map.fitDefault(true);
      this.app.onAnimationChanged();
    }

    _applyModeUI() {
      const el = this.el;
      for (const b of el.modeSeg.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.mode === this.mode));
      el.hud.hidden = !this.enabled || this.mode === 'search';
      el.race.hidden = this.mode !== 'race';
      el.hud.classList.toggle('racing', this.mode === 'race');
      el.searchPanel.hidden = !this.enabled || this.mode !== 'search';
      if (this.mode === 'search') this._fillCompare();
      this._applySplit();
      this.buildTimeline();
    }

    _applySplit() {
      const want = this.enabled && this.mode === 'search' && this.compare && this._compareRoute() !== null;
      if (want !== this.app.map.split) {
        this.app.map.split = want;
        if (want) this.app.map.chooseSplit(this.app.defaultBBox());
        this._acc = [null, null];
        if (!this.app.map.userMoved) this.app.map.fitDefault(false);
        this.app.map.invalidate();
        this.buildTimeline();
      }
    }

    _hasTrace() {
      const r = this.app.selectedRoute();
      return !!(r && r._trace);
    }

    _fillCompare() {
      const sel = this.app.selectedRoute();
      const sel0 = sel ? this.app.model.results[sel._ri] : null;
      const opts = this.app.model.results.filter((r) => r._trace && r !== sel0);
      const sw = this.el.cmpWith;
      sw.textContent = '';
      for (const r of opts) sw.appendChild(U.el('option', { value: r._ri, text: r.title }));
      if (!opts.length) { this.compareRi = -1; sw.disabled = true; this.el.cmp.disabled = true; return; }
      sw.disabled = false;
      this.el.cmp.disabled = false;
      if (!opts.some((r) => r._ri === this.compareRi)) {
        // natural partner: the same method with/without A*
        const name = sel0 ? sel0.algorithm : '';
        const partner = name.endsWith('-astar') ? name.slice(0, -6) : `${name}-astar`;
        const p = opts.find((r) => r.algorithm === partner) || opts[0];
        this.compareRi = p._ri;
      }
      sw.value = String(this.compareRi);
    }

    _compareRoute() {
      if (this.compareRi < 0) return null;
      const r = this.app.model.results[this.compareRi];
      return r && r._trace ? r : null;
    }

    /** Routes the clock drives in the current mode. */
    activeRoutes() {
      if (this.mode === 'race') return this.app.visibleRoutes().filter((r) => r._ok);
      const r = this.app.selectedRoute();
      return r && r._ok ? [r] : [];
    }

    tMax() {
      let t = 0;
      for (const r of this.activeRoutes()) t = Math.max(t, r._tl.tEnd);
      return t;
    }

    _searchRoutes() {
      const out = [this.app.selectedRoute()];
      if (this.app.map.split) out.push(this._compareRoute());
      return out.filter((r) => r && r._trace);
    }

    _searchMax() {
      let n = 0;
      for (const r of this._searchRoutes()) n = Math.max(n, r._trace.n);
      return n;
    }

    /** Settled nodes per real second for the search replay. */
    _searchRate() {
      const n = this._searchMax();
      const dur = U.clamp(n / 140, 3.5, 11) * (this.speedSec / 5);
      return n / dur;
    }

    carStates() {
      if (!this.enabled || this.mode === 'search') return [];
      return this.activeRoutes().map((r) => ({ r, st: M.stateAt(this.app.model, r, this.t) }));
    }

    /* ---------------------------------------------------------- transport */
    play() {
      if (!this.enabled) return;
      if (this.mode === 'search') { if (this.sp >= this._searchMax()) { this.sp = 0; this._acc = [null, null]; } }
      else if (this.t >= this.tMax() - 1e-6) this.t = 0;
      this.playing = true;
      this.el.playbar.classList.add('playing');
      this._last = performance.now();
      if (!this._raf) this._raf = requestAnimationFrame((ts) => this._tick(ts));
    }

    pause() {
      this.playing = false;
      this.el.playbar.classList.remove('playing');
      if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; }
    }

    togglePlay() { if (this.playing) this.pause(); else this.play(); this.frame(); }

    restart() {
      this.t = 0;
      this.sp = 0;
      this._acc = [null, null];
      this.frame();
      this.play();
    }

    seek(t) {
      this.t = U.clamp(t, 0, this.tMax());
      this.frame();
    }

    /** Arrow keys: step by a fraction of the replay. */
    step(frac) {
      if (!this.enabled) return;
      if (this.mode === 'search') {
        this.sp = U.clamp(this.sp + frac * this._searchMax(), 0, this._searchMax());
        this._acc = frac < 0 ? [null, null] : this._acc;
      } else {
        this.t = U.clamp(this.t + frac * this.tMax(), 0, this.tMax());
      }
      this.frame();
    }

    _tick(now) {
      this._raf = 0;
      if (!this.playing || !this.enabled) return;
      const dt = Math.min(0.1, Math.max(0, (now - this._last) / 1000));
      this._last = now;
      if (this.mode === 'search') {
        const max = this._searchMax();
        this.sp = Math.min(max, this.sp + dt * this._searchRate());
        if (this.sp >= max) this.pause();
      } else {
        const max = this.tMax();
        this.t = Math.min(max, this.t + dt * 3600 / this.speedSec);
        if (this.t >= max) this.pause();
      }
      this.frame();
      if (this.playing) this._raf = requestAnimationFrame((ts) => this._tick(ts));
    }

    /** Re-render everything that depends on the clock. */
    frame() {
      const app = this.app;
      app.map.render();
      if (!this.enabled) return;
      this._updateScrubber();
      if (this.mode === 'search') { this._updateCounters(); return; }
      this._updateHud();
      app.charts.soc.redraw();
      const tod = app.clockTod();
      if (Math.abs(tod - this._lastWaitRedraw) > 120) { this._lastWaitRedraw = tod; app.charts.wait.redraw(); }
      const sel = app.selectedRoute();
      const k = sel && this.mode === 'trip' ? M.stopIndexAt(sel, this.t) : -1;
      if (k !== this._activeStop) { this._activeStop = k; app.panels.highlightStop(k); }
    }

    /* ------------------------------------------------------------ timeline */
    buildTimeline() {
      const el = this.el;
      el.track.textContent = '';
      el.ticks.textContent = '';
      if (!this.enabled) return;
      if (this.mode === 'search') {
        el.track.appendChild(U.el('i', { style: { left: '0', width: '100%', background: 'linear-gradient(90deg,#482475,#414487,#2a788e,#22a884,#7ad151,#fde725)', opacity: '.75' } }));
        const n = this._searchMax();
        for (const f of [0, 0.25, 0.5, 0.75, 1]) {
          el.ticks.appendChild(U.el('span', { style: { left: `${f * 100}%` }, text: I.num(Math.round(f * n), 0) }));
        }
        return;
      }
      const tMax = this.tMax();
      const sel = this.app.selectedRoute();
      if (!(tMax > 0) || !sel || !sel._ok) return;
      // merge consecutive drive segments
      const parts = [];
      for (const g of sel._tl.segs) {
        const t1 = g.t1s !== undefined ? g.t1s : g.t1;
        const last = parts[parts.length - 1];
        if (last && last.k === g.k && g.k === 'drive' && Math.abs(last.t1 - g.t0) < 1) last.t1 = t1;
        else parts.push({ k: g.k, t0: g.t0, t1 });
      }
      for (const p of parts) {
        const l = (p.t0 / tMax) * 100, w = Math.max(0.25, ((p.t1 - p.t0) / tMax) * 100);
        el.track.appendChild(U.el('i', { class: `tl-${p.k}`, style: { left: `${l}%`, width: `${w}%` } }));
      }
      // time-of-day ticks on whole hours
      const dep = this.app.model.departure;
      const width = el.track.clientWidth || 600;
      const hours = tMax / 3600;
      const maxTicks = Math.max(2, Math.floor(width / 64));
      const step = [1, 2, 3, 4, 6, 12, 24].find((s) => hours / s <= maxTicks) || 24;
      const first = Math.ceil(dep / 3600 / step) * step * 3600;
      for (let tod = first; tod <= dep + tMax + 1; tod += step * 3600) {
        const f = (tod - dep) / tMax;
        // keep clear of the departure / arrival labels pinned to the two ends (about 34 px wide)
        if (f * width < 52 || (1 - f) * width < 52) continue;
        el.ticks.appendChild(U.el('span', { style: { left: `${f * 100}%` }, text: I.clock(tod, false) }));
      }
      el.ticks.appendChild(U.el('span', { style: { left: '0%', transform: 'none' }, text: I.clock(dep, false) }));
      el.ticks.appendChild(U.el('span', { style: { left: 'auto', right: '0', transform: 'none' }, text: I.clock(dep + tMax, false) }));
    }

    _updateScrubber() {
      const f = this.mode === 'search' ? this.sp / Math.max(1, this._searchMax()) : this.t / Math.max(1e-9, this.tMax());
      this.el.scrub.value = String(Math.round(U.clamp(f, 0, 1) * 1000));
      const pct = `${(U.clamp(f, 0, 1) * 100).toFixed(2)}%`;
      this.el.scrub.style.setProperty('--fill', pct);
      this.el.scrub.setAttribute('aria-valuetext', this.mode === 'search' ? I.num(Math.round(this.sp), 0) : I.clock(this.app.clockTod()));
    }

    /* ---------------------------------------------------------------- HUD */
    _updateHud() {
      const app = this.app, el = this.el;
      const sel = app.selectedRoute();
      if (!sel || !sel._ok) {
        if (!sel) {   // nothing planned (live mode): no values of an earlier trip
          el.clock.textContent = '--:--';
          el.elapsed.textContent = '';
          el.soc.textContent = '--';
          el.fill.style.width = '0%';
          for (const o of [el.dist, el.speedOut, el.elev]) o.textContent = '–';
        }
        el.status.textContent = I.t(!sel && app.live ? 'not_planned' : 'no_route');
        el.status.className = 'hud-status stranded';
        return;
      }
      const st = M.stateAt(app.model, sel, Math.min(this.t, sel._tl.tEnd));
      el.clock.textContent = I.clock(app.model.departure + Math.min(this.t, sel._tl.tEnd));
      el.elapsed.textContent = `${I.hm(Math.min(this.t, sel._tl.tEnd), true)} / ${I.hm(sel._tl.tEnd)} · ${I.km(sel._tl.dEnd, 0)}`;
      el.soc.textContent = I.pct(Math.max(0, st.soc), 0);
      const soc = U.clamp(st.soc, 0, 1);
      el.fill.style.width = `${(soc * 100).toFixed(1)}%`;
      el.fill.style.background = U.socColor(soc, app.theme);
      el.battery.classList.toggle('charging', st.kind === 'charge');
      el.dist.textContent = I.km(st.dist, 0);
      el.dist.title = `${I.km(st.dist, 0)} / ${I.km(sel._tl.dEnd, 0)}`;
      el.speedOut.textContent = st.kind === 'drive' ? `${I.num(st.speed, 0)} ${I.t('unit_kmh')}` : '–';
      el.elev.textContent = `${I.num(st.elev, 0)} m`;
      let cls = 'drive', txt = '';
      const sname = st.stop ? (st.stop.station_name || `#${st.stop.station}`) : '';
      switch (st.kind) {
        case 'start': cls = 'setup'; txt = I.t('st_start'); break;
        case 'drive': cls = 'drive'; txt = `${I.t('st_drive')}${st.road ? ' · ' + st.road : ''}`; break;
        case 'hold': cls = 'hold'; txt = `${I.t(st.stop.peak ? 'st_hold' : 'st_hold_offpeak')} · ${sname} · ${I.t('min_left', { m: I.minutes(st.left) })}`; break;
        case 'wait': cls = 'queue'; txt = `${I.t('st_queue')} · ${sname} · ${I.t('min_left', { m: I.minutes(st.left) })}`; break;
        case 'setup': cls = 'setup'; txt = `${I.t('st_setup')} · ${sname}`; break;
        case 'charge': cls = 'charge'; txt = `${I.t('st_charge')} · ${I.num(st.power, 0)} kW · ${I.t('min_left', { m: I.minutes(st.left) })}`; break;
        case 'done': cls = 'done'; txt = `${I.t('st_done')} · ${I.clock(app.model.departure + sel._tl.tEnd)} · ${I.pct(st.soc, 0)}`; break;
        case 'stranded': cls = 'stranded'; txt = `${I.t('st_stranded')} · ${I.km(st.dist)}`; break;
        default: txt = '';
      }
      el.status.className = `hud-status ${cls}`;
      U.setChildren(el.status, U.el('span', { class: 'dot' }), U.el('span', { text: txt }));
      if (this.mode === 'race') this._updateRace();
    }

    _updateRace() {
      const app = this.app;
      const rows = this.activeRoutes().map((r) => {
        const st = M.stateAt(app.model, r, this.t);
        const done = st.kind === 'done';
        const full = r.distance_m || r._tl.dEnd;
        return { r, st, done, end: r._tl.tEnd, prog: full > 0 ? st.dist / full : 0 };
      });
      rows.sort((a, b) => {
        if (a.done !== b.done) return a.done ? -1 : 1;
        if (a.done) return a.end - b.end;
        if (a.r.feasible !== b.r.feasible) return a.r.feasible ? -1 : 1;
        return b.prog - a.prog;
      });
      // phones: the top four only, so the map stays visible
      const maxRows = window.innerWidth < 560 ? 4 : rows.length;
      const hidden = rows.length - maxRows;
      const items = rows.slice(0, maxRows).map((x, k) => {
        let txt, cls = 'st';
        if (x.done) { txt = `✓ ${I.hm(x.end)}`; cls += ' done'; }
        else if (x.st.kind === 'stranded') { txt = `✕ ${I.km(x.st.dist, 0)}`; cls += ' bad'; }
        else if (x.st.kind === 'charge') txt = `⚡ ${I.pct(x.st.soc, 0)}`;
        else if (x.st.kind === 'wait') txt = `⏳ ${I.minutes(x.st.left)}`;
        else if (x.st.kind === 'hold') txt = `⏸ ${I.minutes(x.st.left)}`;
        else txt = I.pct(x.prog, 0);
        return U.el('li', { style: { '--algo': app.colorOf(x.r) } },
          U.el('span', { class: 'rank', text: String(k + 1) }),
          U.el('span', { class: 'who' }, U.el('i'), U.el('span', { text: x.r.title })),
          U.el('span', { class: cls, text: txt }),
          U.el('span', { class: 'prog' }, U.el('i', { style: { width: `${(U.clamp(x.prog, 0, 1) * 100).toFixed(1)}%` } })));
      });
      if (hidden > 0) items.push(U.el('li', { class: 'more', text: I.t('race_more', { n: hidden }) }));
      U.setChildren(this.el.race, items);
    }

    _updateCounters() {
      const app = this.app;
      const routes = this._searchRoutes();
      const rows = routes.map((r) => {
        const tr = r._trace;
        const k = Math.min(tr.n, Math.floor(this.sp));
        const done = k >= tr.n;
        const stats = r.stats || {};
        const keyTxt = app.currentPrefs().objective === 'cost' ? I.num(tr.kmax, 0) : I.hm(tr.kmax);
        return U.el('div', { class: 'sc-row', style: { '--algo': app.colorOf(r) } },
          U.el('span', { class: 'who' }, U.el('i'), U.el('span', { text: r.title })),
          U.el('span', { class: 'n', text: `${I.num(k, 0)} ${I.t('sc_of')} ${I.num(tr.n, 0)}` }),
          U.el('span', { class: 'bar' }, U.el('i', { style: { width: `${(100 * k / Math.max(1, this._searchMax())).toFixed(1)}%` } })),
          U.el('span', { class: 'meta', text: `${I.t('sc_explored')} · ${I.num(stats.settled, 0)} ${I.t('sc_labels')} · ${I.t('sc_key')} ≤ ${keyTxt}${done ? ' · ✓ ' + I.t('sc_done') : ''}` }));
      });
      U.setChildren(this.el.counters, rows);
    }

    /* ------------------------------------------------------- map drawing */
    /** Routes for the cached static layer. */
    drawStaticRoutes(ctx, T) {
      const app = this.app, map = app.map, th = app.theme;
      if (this.enabled && this.mode === 'search') return;
      const sel = app.selectedRoute();
      const vis = app.visibleRoutes();
      const others = app.layers.others ? vis.filter((r) => r !== sel) : [];
      const race = this.enabled && this.mode === 'race';
      const trip = this.enabled && this.mode === 'trip';
      for (const r of others) {
        if (!r._ok) continue;
        map.strokeRoute(ctx, r, T, {
          color: app.colorOf(r), width: race ? 3.4 : 3.2, alpha: race ? 0.42 : 0.62, casing: 2.4,
          dash: r.feasible ? null : [7, 6], strand: !r.feasible,
        });
      }
      if (sel && sel._ok) {
        map.strokeRoute(ctx, sel, T, {
          color: app.colorOf(sel), width: 5.4, alpha: trip ? 0.4 : race ? 0.5 : 1, casing: 3.4, casingAlpha: trip ? 0.8 : 1,
          dash: sel.feasible ? null : [8, 6], strand: !sel.feasible,
        });
      }
    }

    /** "Battery empty" crosses of infeasible routes (static layer). */
    drawStaticMarkers(ctx, T) {
      const app = this.app, th = app.theme;
      if (this.enabled && this.mode === 'search') return;
      for (const r of app.visibleRoutes()) {
        if (!r._strand || (!app.layers.others && r !== app.selectedRoute())) continue;
        const x = T.ox + r._strand.x * T.s, y = T.oy - r._strand.y * T.s;
        ctx.beginPath();
        ctx.arc(x, y, 9, 0, Math.PI * 2);
        ctx.fillStyle = th.surface;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = th.bad;
        ctx.stroke();
        EVR.charts.drawCross(ctx, x, y, 3.8, th.bad);
      }
    }

    /** Numbered badges at the charging stops of the selected route. */
    _drawStopBadges(ctx, T) {
      const app = this.app, th = app.theme, m = app.model;
      const sel = app.selectedRoute();
      if (!sel || !sel._ok) return;
      const color = app.colorOf(sel);
      for (const s of sel._stops) {
        const x = T.ox + m.X[s.node] * T.s, y = T.oy - m.Y[s.node] * T.s;
        if (x < -20 || y < -20 || x > T.w + 20 || y > T.h + 20) continue;
        ctx.save();
        ctx.shadowColor = 'rgba(0,0,0,.28)';
        ctx.shadowBlur = 6;
        ctx.shadowOffsetY = 1;
        ctx.beginPath();
        ctx.arc(x, y, 11, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();
        ctx.restore();
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = '#fff';
        ctx.beginPath();
        ctx.arc(x, y, 11, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = '#fff';
        ctx.font = `800 12px ${th.font}`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(s._num), x, y + 0.5);
      }
    }

    /**
     * Per-frame drawing.  phase 'under' is painted below the cached labels
     * (trails, charging rings, search dots), phase 'over' above them (stop
     * badges, cars, name tags).
     */
    overlay(ctx, vp, T, phase) {
      const search = this.enabled && this.mode === 'search';
      if (search) { if (phase === 'under') this._drawSearch(ctx, vp, T); else this._drawSplitCaption(ctx, vp, T); return; }
      if (!this.enabled) { if (phase === 'over') this._drawStopBadges(ctx, T); return; }
      const app = this.app, map = app.map, th = app.theme;
      const cars = this.carStates();
      const race = this.mode === 'race';
      if (phase === 'under') {
        // trails; the selected route last, so it stays on top where routes coincide
        const sel0 = app.selectedRoute();
        for (const { r, st } of cars.slice().sort((a, b) => (a.r === sel0) - (b.r === sel0))) {
          if (st.kind === 'start') continue;
          const tip = st.kind === 'drive' || st.kind === 'stranded' ? [st.x, st.y] : null;
          map.strokeRoute(ctx, r, T, {
            color: app.colorOf(r), width: race ? 4.2 : 5.6, alpha: 1, casing: race ? 2.6 : 3.4,
            upto: st.i, tip, glow: !race, dash: r.feasible ? null : [8, 6],
          });
        }
        // charging (solid, green), queue (dashed, amber), plug-in and tariff-hold (dotted, violet) rings
        const now = performance.now();
        for (const { st } of cars) {
          if (st.kind !== 'charge' && st.kind !== 'wait' && st.kind !== 'setup' && st.kind !== 'hold') continue;
          const x = T.ox + st.x * T.s, y = T.oy - st.y * T.s;
          const color = st.kind === 'charge' ? th.cCharge : st.kind === 'wait' ? th.cQueue : st.kind === 'hold' ? th.cHold : th.cSetup;
          const period = st.kind === 'charge' ? 1300 : st.kind === 'hold' ? 3200 : 2200;
          ctx.beginPath();
          ctx.arc(x, y, 15, 0, Math.PI * 2);
          ctx.fillStyle = U.rgba(color, 0.16);
          ctx.fill();
          for (let k = 0; k < 3; k++) {
            const ph = this.playing ? ((now / period) + k / 3) % 1 : (k + 1) / 4;
            ctx.beginPath();
            ctx.arc(x, y, 13 + ph * (race ? 20 : 34), 0, Math.PI * 2);
            ctx.strokeStyle = U.rgba(color, (1 - ph) * 0.9);
            ctx.lineWidth = 3.2 - ph * 2;
            ctx.setLineDash(st.kind === 'wait' ? [5, 4] : st.kind === 'hold' ? [2, 4] : []);
            ctx.stroke();
          }
          ctx.setLineDash([]);
        }
        return;
      }
      if (this.mode === 'trip') this._drawStopBadges(ctx, T);
      const sel = app.selectedRoute();
      const order = cars.slice().sort((a, b) => (a.r === sel) - (b.r === sel));
      const boxes = [];
      for (const { r, st } of order) {
        const x = T.ox + st.x * T.s, y = T.oy - st.y * T.s;
        drawCar(ctx, x, y, -st.heading, app.colorOf(r), st, th, race ? 0.85 : 1);
      }
      if (race) {
        // cars closer than 18 px share one name tag (all routes often
        // coincide for long stretches): colour dots + "first +n"
        const clusters = [];
        for (const { r, st } of order.slice().reverse()) {
          const x = T.ox + st.x * T.s, y = T.oy - st.y * T.s;
          let c = clusters.find((q) => Math.abs(q.x - x) + Math.abs(q.y - y) < 18);
          if (!c) { c = { x, y, members: [] }; clusters.push(c); }
          c.members.push(r);
        }
        for (const c of clusters) {
          const n = c.members.length;
          const text = n === 1 ? c.members[0].title
            : n === 2 ? `${c.members[0].title} · ${c.members[1].title}`
              : `${c.members[0].title} ${I.t('race_more', { n: n - 1 })}`;
          drawTag(ctx, c.x, c.y, text, app.colorOf(c.members[0]), th, boxes, false, c.members.slice(0, 9).map((r) => app.colorOf(r)));
        }
      } else if (cars.length) {
        const { st } = cars[0];
        if (st.kind === 'charge') {
          // charge progress ring around the car: SoC as an arc
          const x = T.ox + st.x * T.s, y = T.oy - st.y * T.s;
          ctx.save();
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.arc(x, y, 16.5, 0, Math.PI * 2);
          ctx.strokeStyle = U.rgba(th.surface, 0.85);
          ctx.lineWidth = 4.5;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(x, y, 16.5, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * U.clamp(st.soc, 0, 1));
          ctx.strokeStyle = th.cCharge;
          ctx.lineWidth = 3.2;
          ctx.stroke();
          ctx.restore();
        }
        if (st.kind === 'charge' || st.kind === 'wait' || st.kind === 'hold') {
          const x = T.ox + st.x * T.s, y = T.oy - st.y * T.s;
          const txt = st.kind === 'charge'
            ? `⚡ ${I.num(st.power, 0)} kW · ${I.pct(st.soc, 0)} · ${I.t('min_left', { m: I.minutes(st.left) })}`
            : st.kind === 'hold'
              ? `⏸ ${I.t(st.stop.peak ? 'hold_tag' : 'hold_tag_offpeak', { m: I.minutes(st.left) })}`
              : `⏳ ${I.t('in_queue_left', { m: I.minutes(st.left) })}`;
          const tagColor = st.kind === 'charge' ? th.cCharge : st.kind === 'hold' ? th.cHold : th.cQueue;
          drawTag(ctx, x, y - 8, txt, tagColor, th, boxes, true);
        }
      }
    }

    _spriteSet() {
      const th = this.app.theme;
      const dpr = U.dpr();
      const key = `${th.dark}|${dpr}`;
      if (this._sprites && this._sprites.key === key) return this._sprites;
      const N = 32, R = 16;
      const list = [];
      for (let k = 0; k < N; k++) {
        // avoid the darkest purple on dark maps and the palest yellow on light ones
        const t = th.dark ? 0.12 + 0.88 * (k / (N - 1)) : 0.86 * (k / (N - 1));
        const c = U.viridis(t);
        const cv = document.createElement('canvas');
        cv.width = cv.height = Math.ceil(2 * R * dpr);
        const g = cv.getContext('2d');
        g.scale(dpr, dpr);
        const rgb = `${c[0] | 0},${c[1] | 0},${c[2] | 0}`;
        const rg = g.createRadialGradient(R, R, 0, R, R, R);
        rg.addColorStop(0, `rgba(${rgb},${th.dark ? 0.6 : 0.5})`);
        rg.addColorStop(0.45, `rgba(${rgb},${th.dark ? 0.24 : 0.18})`);
        rg.addColorStop(1, `rgba(${rgb},0)`);
        g.fillStyle = rg;
        g.fillRect(0, 0, 2 * R, 2 * R);
        // crisp core
        g.beginPath();
        g.arc(R, R, R * 0.3, 0, Math.PI * 2);
        g.fillStyle = `rgb(${rgb})`;
        g.fill();
        g.lineWidth = R * 0.07;
        g.strokeStyle = th.dark ? 'rgba(255,255,255,.55)' : 'rgba(255,255,255,.95)';
        g.stroke();
        list.push(cv);
      }
      this._sprites = { key, list, R };
      return this._sprites;
    }

    _drawSearch(ctx, vp, T) {
      const app = this.app;
      const r = vp.i === 0 ? app.selectedRoute() : this._compareRoute();
      if (!r || !r._trace) return;
      const tr = r._trace;
      const k = Math.min(tr.n, Math.floor(this.sp));
      const sp = this._spriteSet();
      const size = U.clamp(20 + app.map.cam.s * 1000 * 3, 20, 34);
      const half = size / 2;
      if (k >= tr.n && r._ok) {
        // search finished: the route it found, under its explored nodes
        app.map.strokeRoute(ctx, r, T, { color: app.colorOf(r), width: 5, alpha: 0.9, casing: 3, strand: !r.feasible });
      }
      const camKey = `${T.s}|${T.ox}|${T.oy}|${vp.w}|${vp.h}|${sp.key}`;
      // settled nodes accumulate in an offscreen canvas (dots never disappear)
      let acc = this._acc[vp.i];
      const dpr = U.dpr();
      if (!acc || acc.key !== camKey || acc.k > k) {
        const cv = (acc && acc.canvas) || document.createElement('canvas');
        cv.width = Math.round(vp.w * dpr);
        cv.height = Math.round(vp.h * dpr);
        acc = { canvas: cv, key: camKey, k: 0 };
        this._acc[vp.i] = acc;
      }
      if (acc.k < k) {
        const g = acc.canvas.getContext('2d');
        g.setTransform(dpr, 0, 0, dpr, 0, 0);
        g.globalCompositeOperation = app.theme.dark ? 'lighter' : 'source-over';
        const list = sp.list, last = list.length - 1;
        for (let j = acc.k; j < k; j++) {
          const x = T.ox + tr.xy[2 * j] * T.s, y = T.oy - tr.xy[2 * j + 1] * T.s;
          if (x < -half || y < -half || x > vp.w + half || y > vp.h + half) continue;
          g.drawImage(list[Math.round(tr.key[j] * last)], x - half, y - half, size, size);
        }
        acc.k = k;
      }
      ctx.drawImage(acc.canvas, 0, 0, vp.w, vp.h);
      // frontier: the most recently settled nodes, larger and brighter
      if (k < tr.n) {
        const F = Math.max(6, Math.round(tr.n * 0.03));
        ctx.save();
        ctx.globalCompositeOperation = app.theme.dark ? 'lighter' : 'source-over';
        for (let j = Math.max(0, k - F); j < k; j++) {
          const a = 1 - (k - j) / F;
          const x = T.ox + tr.xy[2 * j] * T.s, y = T.oy - tr.xy[2 * j + 1] * T.s;
          const s2 = size * (1.2 + 1.3 * a);
          ctx.globalAlpha = 0.4 + 0.6 * a;
          ctx.drawImage(sp.list[Math.round(tr.key[j] * (sp.list.length - 1))], x - s2 / 2, y - s2 / 2, s2, s2);
        }
        ctx.restore();
      }
    }

    /** Algorithm name and counter at the top-left of each half in split mode. */
    _drawSplitCaption(ctx, vp, T) {
      const app = this.app;
      if (!app.map.split) return;
      const r = vp.i === 0 ? app.selectedRoute() : this._compareRoute();
      if (!r || !r._trace) return;
      const th = app.theme;
      const k = Math.min(r._trace.n, Math.floor(this.sp));
      ctx.save();
      ctx.font = `700 13px ${th.font}`;
      const label = `${r.title} · ${I.num(k, 0)} ${I.t('sc_explored')}`;
      const w = ctx.measureText(label).width + 34;
      const map = app.map;
      const bottomPanel = !map.splitRows || vp.i === 1;   // the playbar covers the bottom viewport only
      const x = 14, y = bottomPanel ? vp.h - map._insets().b - 50 : vp.h - 44;
      ctx.shadowColor = 'rgba(0,0,0,.18)';
      ctx.shadowBlur = 8;
      U.roundRect(ctx, x, y, w, 30, 15);
      ctx.fillStyle = th.surface;
      ctx.fill();
      ctx.shadowColor = 'transparent';
      ctx.fillStyle = app.colorOf(r);
      ctx.beginPath();
      ctx.arc(x + 15, y + 15, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = th.text;
      ctx.textBaseline = 'middle';
      ctx.fillText(label, x + 26, y + 15.5);
      ctx.restore();
    }
  }

  /** Navigation-style car puck: white disc, coloured core, heading chevron. */
  function drawCar(ctx, x, y, ang, color, st, th, scale) {
    const s = scale || 1;
    ctx.save();
    ctx.translate(x, y);
    ctx.shadowColor = 'rgba(0,0,0,.35)';
    ctx.shadowBlur = 8;
    ctx.shadowOffsetY = 2;
    ctx.beginPath();
    ctx.arc(0, 0, 11 * s, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.beginPath();
    ctx.arc(0, 0, 8.2 * s, 0, Math.PI * 2);
    ctx.fillStyle = st.kind === 'stranded' ? th.bad : color;
    ctx.fill();
    if (st.kind === 'stranded') {
      EVR.charts.drawCross(ctx, 0, 0, 3.5 * s, '#fff');
    } else if (st.kind === 'charge') {
      ctx.fillStyle = '#fff';
      U.bolt(ctx, 0, 0, 11 * s);
      ctx.fill();
    } else if (st.kind === 'done') {
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 2.2 * s;
      ctx.beginPath();
      ctx.moveTo(-3.8 * s, 0.2 * s); ctx.lineTo(-1 * s, 3 * s); ctx.lineTo(4 * s, -2.6 * s);
      ctx.stroke();
    } else {
      ctx.rotate(ang);
      ctx.beginPath();
      ctx.moveTo(5.6 * s, 0);
      ctx.lineTo(-3.4 * s, -4.4 * s);
      ctx.lineTo(-1.4 * s, 0);
      ctx.lineTo(-3.4 * s, 4.4 * s);
      ctx.closePath();
      ctx.fillStyle = '#fff';
      ctx.fill();
    }
    ctx.restore();
  }

  /**
   * Label pill above a point, avoiding previously placed pills; `dots`
   * (colours) are drawn as small discs before the text (race clusters).
   */
  function drawTag(ctx, x, y, text, color, th, boxes, strong, dots) {
    ctx.save();
    ctx.font = `${strong ? 700 : 650} ${strong ? 12 : 11.5}px ${th.font}`;
    const nd = dots && dots.length > 1 ? dots.length : 0;
    const dw = nd ? 6 + (nd - 1) * 5 + 6 : 0;
    const w = ctx.measureText(text).width + 16 + dw, h = strong ? 24 : 21;
    const offsets = [-34, -58, -82, 22, 46, 70];
    let bx = x - w / 2, by = y + offsets[0] - h / 2;
    for (const off of offsets) {
      const cx = x - w / 2, cy = y + off - h / 2;
      if (!boxes.some((b) => cx < b[2] && cx + w > b[0] && cy < b[3] && cy + h > b[1])) { bx = cx; by = cy; break; }
    }
    boxes.push([bx, by, bx + w, by + h]);
    ctx.shadowColor = 'rgba(0,0,0,.22)';
    ctx.shadowBlur = 6;
    U.roundRect(ctx, bx, by, w, h, h / 2);
    ctx.fillStyle = th.surface;
    ctx.fill();
    ctx.shadowColor = 'transparent';
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = color;
    ctx.stroke();
    for (let k = nd - 1; k >= 0; k--) {
      ctx.beginPath();
      ctx.arc(bx + 11 + k * 5, by + h / 2, 3.6, 0, Math.PI * 2);
      ctx.fillStyle = dots[k];
      ctx.fill();
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = th.surface;
      ctx.stroke();
    }
    ctx.fillStyle = th.text;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, bx + dw + (w - dw) / 2, by + h / 2 + 0.5);
    ctx.restore();
  }

  EVR.anim = { Animator, drawCar, drawTag };
})(window.EVR = window.EVR || {});
