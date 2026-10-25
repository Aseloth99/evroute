/* EVRoute viewer -- live.js
 *
 * Live planning (only when the page is served by `python -m evroute.server`
 * or `evroute serve`): origin/destination pickers and map picking,
 * vehicle and trip parameters, algorithm checkboxes and the Plan button,
 * which POSTs to api/plan and replaces the results in place.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;

  const DEFAULT_ALGOS = ['greedy', 'station-graph', 'bls-astar', 'cfp-astar'];

  class Live {
    constructor(app) {
      this.app = app;
      this.pick = 'origin';
      this.busy = false;
      this.el = {
        card: U.$('#live-card'), scenario: U.$('#in-scenario'), fieldScenario: U.$('#field-scenario'),
        origin: U.$('#in-origin'), destination: U.$('#in-destination'), swap: U.$('#btn-swap'), pickSeg: U.$('#pick-seg'),
        vehicle: U.$('#in-vehicle'), soc: U.$('#in-soc'), temp: U.$('#in-temp'), reserve: U.$('#in-reserve'), arrive: U.$('#in-arrive'),
        objective: U.$('#in-objective'), depart: U.$('#in-depart'), vot: U.$('#in-vot'), votUnit: U.$('#vot-unit'),
        eco: U.$('#in-eco'), queue: U.$('#in-queue'), algos: U.$('#algo-picks'), plan: U.$('#btn-plan'), error: U.$('#plan-error'),
        outSoc: U.$('#out-soc'), outTemp: U.$('#out-temp'), outReserve: U.$('#out-reserve'), outArrive: U.$('#out-arrive'),
      };
      this.el.card.hidden = false;
      this._wire();
      this.fill();
      this._loadScenarioList();
    }

    _wire() {
      const el = this.el;
      for (const [inp, out, fmt] of [
        [el.soc, el.outSoc, (v) => I.pct(v / 100, 0)], [el.temp, el.outTemp, (v) => `${v} °C`],
        [el.reserve, el.outReserve, (v) => I.pct(v / 100, 0)], [el.arrive, el.outArrive, (v) => I.pct(v / 100, 0)],
      ]) {
        const upd = () => {
          out.textContent = fmt(+inp.value);
          const f = (inp.value - inp.min) / (inp.max - inp.min);
          inp.style.setProperty('--fill', `${(f * 100).toFixed(1)}%`);
        };
        inp.addEventListener('input', upd);
        inp._upd = upd;
      }
      el.origin.addEventListener('change', () => this.setNode('origin', +el.origin.value, false));
      el.destination.addEventListener('change', () => this.setNode('destination', +el.destination.value, false));
      el.swap.addEventListener('click', () => {
        const q = this.app.currentQuery();
        this.app.setQuery({ origin: q.destination, destination: q.origin });
        this._syncOD();
      });
      for (const b of el.pickSeg.querySelectorAll('button')) b.addEventListener('click', () => this.setPick(b.dataset.pick));
      el.plan.addEventListener('click', () => this.plan());
      el.card.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' && ev.target.tagName !== 'BUTTON' && ev.target.tagName !== 'SELECT') { ev.preventDefault(); this.plan(); }
      });
      el.scenario.addEventListener('change', () => this.switchScenario(el.scenario.value));
    }

    setPick(which) {
      this.pick = which;
      for (const b of this.el.pickSeg.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.pick === which));
    }

    /** Fill every control from the current payload. */
    fill() {
      const app = this.app, el = this.el, p = app.payload;
      const sc = app.model.scenario || {};
      const prefs = sc.preferences || {};
      const q = app.currentQuery();
      // places: settlements first by importance, then the rest
      const places = app.model.places.filter((x) => x.kind === 'city' || x.kind === 'town');
      const list = (places.length ? places : app.model.places).slice().sort((a, b) => a.name.localeCompare(b.name, I.lang === 'tr' ? 'tr' : 'en'));
      for (const sel of [el.origin, el.destination]) {
        sel.textContent = '';
        for (const pl of list) sel.appendChild(U.el('option', { value: pl.node, text: pl.name }));
      }
      this._syncOD();
      el.vehicle.textContent = '';
      for (const v of p.vehicles || []) el.vehicle.appendChild(U.el('option', { value: v.key, text: `${v.name}` }));
      el.vehicle.value = (sc.vehicle || {}).key || '';
      el.soc.value = Math.round((q.initial_soc ?? 0.8) * 100);
      el.temp.value = Math.round((sc.conditions || {}).temperature_c ?? 20);
      el.reserve.value = Math.round((prefs.reserve_soc ?? 0.1) * 100);
      el.arrive.value = Math.round((prefs.min_arrival_soc ?? 0.1) * 100);
      el.objective.value = prefs.objective || 'time';
      el.depart.value = I.clock(q.departure_s || 0, false);
      el.vot.value = prefs.value_of_time_per_h ?? 15;
      el.votUnit.textContent = `(${app.model.currency}${I.t('per_hour')})`;
      el.eco.checked = (prefs.speed_factors || [1]).length > 1;
      el.queue.checked = prefs.queue_aware !== false;
      for (const inp of [el.soc, el.temp, el.reserve, el.arrive]) inp._upd();
      // algorithm checkboxes
      const have = new Set(app.model.results.map((r) => r.algorithm));
      el.algos.textContent = '';
      for (const a of p.algorithms || []) {
        const cb = U.el('input', { type: 'checkbox', value: a.name });
        cb.checked = have.size ? have.has(a.name) : DEFAULT_ALGOS.includes(a.name);
        el.algos.appendChild(U.el('label', { class: 'check', title: I.algoDescription(a), style: { '--algo': app.theme.dark ? a.color_dark : a.color } },
          cb, U.el('span', { text: a.short_title || a.title || a.name })));
      }
    }

    _syncOD() {
      const q = this.app.currentQuery();
      for (const [sel, node] of [[this.el.origin, q.origin], [this.el.destination, q.destination]]) {
        if (!Array.from(sel.options).some((o) => +o.value === node)) {
          sel.appendChild(U.el('option', { value: node, text: this.app.model.nodeLabel(node) }));
        }
        sel.value = String(node);
      }
    }

    setNode(which, node, fromMap) {
      const q = Object.assign({}, this.app.currentQuery());
      q[which] = node;
      this.app.setQuery({ origin: q.origin, destination: q.destination });
      this._syncOD();
      if (fromMap) this.app.toast(I.t(which === 'origin' ? 'toast_origin' : 'toast_destination', { n: this.app.model.nodeLabel(node) }));
    }

    /** Map click in live mode: set origin (or destination with Shift / the B toggle). */
    onMapClick(wx, wy, shift) {
      const app = this.app;
      const which = shift ? 'destination' : this.pick;
      // prefer a named place within ~26 px, else the nearest node
      const named = app.model.nearestNode(wx, wy, true);
      const tol = 26 / app.map.cam.s;
      const hit = named.node >= 0 && named.dist <= tol ? named : app.model.nearestNode(wx, wy, false);
      if (hit.node < 0) return true;
      this.setNode(which, hit.node, true);
      if (!shift && which === 'origin') this.setPick('destination');
      return true;
    }

    request() {
      const el = this.el, app = this.app;
      const q = app.currentQuery();
      const [hh, mm] = (el.depart.value || '08:00').split(':').map(Number);
      const algorithms = U.$$('input[type=checkbox]', el.algos).filter((c) => c.checked).map((c) => c.value);
      return {
        scenario: app.payload.scenario_preset || (app.model.scenario.meta || {}).preset || app.model.scenario.name,
        origin: q.origin,
        destination: q.destination,
        algorithms,
        vehicle: el.vehicle.value || undefined,
        initial_soc: +el.soc.value / 100,
        temperature_c: +el.temp.value,
        reserve_soc: +el.reserve.value / 100,
        min_arrival_soc: +el.arrive.value / 100,
        objective: el.objective.value,
        value_of_time_per_h: Math.max(0, +el.vot.value || 0),
        speed_factors: el.eco.checked ? [1.0, 0.9, 0.8] : [1.0],
        queue_aware: el.queue.checked,
        departure_s: ((hh || 0) * 3600 + (mm || 0) * 60),
      };
    }

    showError(msg) {
      this.el.error.hidden = !msg;
      this.el.error.textContent = msg || '';
    }

    setBusy(on) {
      this.busy = on;
      this.el.plan.disabled = on;
      this.el.plan.classList.toggle('busy', on);
      U.$('#map-busy').hidden = !on;
    }

    async plan() {
      if (this.busy) return;
      const body = this.request();
      if (body.origin === body.destination) { this.showError(I.t('err_same_od')); return; }
      if (!body.algorithms.length) { this.showError(I.t('err_no_algo')); return; }
      this.showError(null);
      this.setBusy(true);
      const t0 = performance.now();
      try {
        const res = await fetch('api/plan', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        this.app.applyPlanResponse(data);
        this.fill();
        const errs = (data.errors || []).map((e) => `${e.algorithm}: ${e.message}`);
        this.showError(errs.length ? errs.join(' · ') : null);
        this.app.toast(I.t('toast_planned', { n: data.results.length, ms: I.num(performance.now() - t0, 0) }));
      } catch (err) {
        this.showError(I.t('err_generic', { m: err.message || String(err) }));
      } finally {
        this.setBusy(false);
      }
    }

    async _loadScenarioList() {
      try {
        const res = await fetch('api/scenarios');
        if (!res.ok) throw new Error(String(res.status));
        const data = await res.json();
        const sel = this.el.scenario;
        sel.textContent = '';
        for (const s of data.scenarios || []) sel.appendChild(U.el('option', { value: s.name, text: s.name, title: s.description || '' }));
        const cur = this.app.payload.scenario_preset || ((this.app.model.scenario.meta || {}).preset) || this.app.model.scenario.name;
        sel.value = cur;
        this.el.fieldScenario.hidden = !(data.scenarios || []).length;
      } catch (e) {
        this.el.fieldScenario.hidden = true;
      }
    }

    async switchScenario(name) {
      if (!name || this.busy) return;
      this.setBusy(true);
      this.showError(null);
      try {
        const res = await fetch(`api/scenario?name=${encodeURIComponent(name)}`);
        const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
        if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
        this.app.loadPayload(data);
        this.fill();
      } catch (err) {
        this.showError(I.t('err_generic', { m: err.message || String(err) }));
        this.setBusy(false);
        return;
      }
      this.setBusy(false);
      this.plan();
    }
  }

  EVR.live = { Live, DEFAULT_ALGOS };
})(window.EVR = window.EVR || {});
