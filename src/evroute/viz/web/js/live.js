/* EVRoute viewer -- live.js
 *
 * Live planning: origin/destination pickers and map picking, vehicle and
 * trip parameters, algorithm checkboxes and the Plan button, which asks the
 * planner and replaces the results in place.
 *
 * The planner is reached through a *transport* with one interface,
 * request(endpoint, args) and plan(body, onProgress, onPartial), answering
 * with the JSON of evroute.server.api_call (endpoints health, scenarios,
 * scenario, plan):
 *
 *   HttpTransport    `evroute serve` / `python -m evroute.server`: fetch()
 *                    to api/<endpoint> on the local server.
 *   WorkerTransport  the project web site: EVRoute runs in the visitor's
 *                    browser, in a Web Worker under Pyodide (the page's
 *                    payload carries a "worker" block, written by
 *                    tools/build_site.py).  The worker calls the same Python
 *                    functions as the server.  A plan is requested one
 *                    algorithm at a time -- the fast exact planners first --
 *                    so the page can show progress and the results so far,
 *                    let the visitor stop the run, and enforce the web's time
 *                    budget per planner: a planner that runs over it is
 *                    stopped by restarting the worker.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;

  const DEFAULT_ALGOS = ['greedy', 'station-graph', 'bls-astar', 'cfp-astar'];
  /** The planner of everyday use (exact on its charging grid, handles cost, tariffs and holds; fast), then fallbacks. */
  const EVERYDAY_ALGOS = ['bls-astar', 'bls', 'cfp-astar'];
  /** Order in which the in-browser planner runs the ticked planners: usually fastest first. */
  const RUN_ORDER = ['naive', 'greedy', 'cfp-astar', 'bls-astar', 'station-graph', 'genetic', 'cfp', 'bls', 'pareto'];

  function runOrder(algos) {
    const rank = (a) => { const i = RUN_ORDER.indexOf(a); return i < 0 ? RUN_ORDER.length : i; };
    return algos.map((a, i) => [rank(a), i, a]).sort((x, y) => x[0] - y[0] || x[1] - y[1]).map((x) => x[2]);
  }

  /* ------------------------------------------------------------ transports */
  /** The local planning server (evroute serve): one HTTP request per call. */
  class HttpTransport {
    constructor() { this.kind = 'http'; this.canCancel = false; }

    start() { return Promise.resolve(); }

    async request(endpoint, args) {
      let res;
      if (endpoint === 'plan') {
        res = await fetch('api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(args) });
      } else if (endpoint === 'scenario') {
        res = await fetch(`api/scenario?name=${encodeURIComponent(args.name)}`);
      } else {
        res = await fetch(`api/${endpoint}`);
      }
      const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    }

    plan(body) { return this.request('plan', body); }

    cancel() { /* the server plans in one request */ }
  }

  /**
   * EVRoute in the browser: a Web Worker that loads Pyodide and the evroute
   * wheel and answers the same API calls (see site/worker.js).  `cfg` is
   * the payload's "worker" block: script, pyodide (index URL), integrity,
   * wheel, service (PlanningService options), time_budget_s, download_bytes, ...
   */
  class WorkerTransport {
    constructor(cfg) {
      this.kind = 'worker';
      this.canCancel = true;
      this.cfg = cfg;
      this.worker = null;
      this.ready = null;
      this.seq = 0;
      this.pending = new Map();
      this.onBoot = null;        // progress of the first start: {phase, loaded, total} | {phase: 'ready', ...}
      this.started = false;      // a worker has been ready once (later starts are restarts)
      this.prepare = null;       // the preset a (re)started worker gets ready first
      this.running = false;      // a plan() is under way
      this.cancelled = false;    // ... and the visitor stopped it
      this._booting = null;      // {reject} of the start under way
    }

    start() {
      if (!this.ready) {
        this.ready = this._spawn().catch((err) => { this._kill(err); throw err; });
      }
      return this.ready;
    }

    _spawn() {
      return new Promise((resolve, reject) => {
        const w = new Worker(this.cfg.script);
        this.worker = w;
        this._booting = { reject };
        const boot = (m) => { if (this.onBoot) this.onBoot(m, this.started); };
        w.onmessage = (ev) => {
          const m = ev.data || {};
          if (m.type === 'progress') boot(m);
          else if (m.type === 'ready') { this._booting = null; boot(Object.assign({ phase: 'ready' }, m)); this.started = true; resolve(m); }
          else if (m.type === 'failed') {
            const err = new Error(m.message || 'worker failed');
            err.reason = m.reason;
            this._booting = null;
            reject(err);
          } else if (m.type === 'reply') {
            const done = this.pending.get(m.id);
            if (done) { this.pending.delete(m.id); done.reply(m); }
          }
        };
        w.onerror = (ev) => {
          ev.preventDefault();
          const err = new Error(ev.message || 'worker error');
          this._booting = null;
          reject(err);
          this._kill(err);
        };
        w.postMessage({ type: 'init', config: this.cfg, prepare: this.prepare });
      });
    }

    /** Terminate the worker; pending calls (and a start under way) fail with `err`. */
    _kill(err) {
      if (this.worker) this.worker.terminate();
      this.worker = null;
      this.ready = null;
      const e = err || new Error('worker stopped');
      if (this._booting) { const b = this._booting; this._booting = null; b.reject(e); }
      const calls = Array.from(this.pending.values());
      this.pending.clear();
      for (const c of calls) c.fail(e);
    }

    /** Stop the plan under way (the worker is restarted for the next one). */
    cancel() {
      if (!this.running || this.cancelled) return;
      this.cancelled = true;
      const err = new Error('stopped');
      err.cancelled = true;
      this._kill(err);
    }

    async request(endpoint, args, timeoutMs) {
      await this.start();
      const w = this.worker;
      if (!w) { const err = new Error('stopped'); err.cancelled = true; throw err; }
      return new Promise((resolve, reject) => {
        const id = ++this.seq;
        let timer = null;
        const call = {
          reply: (m) => {
            clearTimeout(timer);
            let data;
            try { data = JSON.parse(m.body); } catch (e) { data = { error: `status ${m.status}` }; }
            if (m.fatal) this._kill(new Error(data.error || 'worker failed'));   // Pyodide cannot go on: start afresh next time
            if (m.status >= 200 && m.status < 300) resolve(data); else reject(new Error(data.error || `status ${m.status}`));
          },
          fail: (err) => { clearTimeout(timer); reject(err); },
        };
        this.pending.set(id, call);
        if (timeoutMs > 0) {
          timer = setTimeout(() => {
            const err = new Error('time budget');
            err.timeout = true;
            this._kill(err);           // the only way to stop Python running in a worker
          }, timeoutMs);
        }
        w.postMessage({ type: 'call', id, endpoint, args: JSON.stringify(args || {}) });
      });
    }

    /**
     * Plan one algorithm per request, in RUN_ORDER, and merge the answers in
     * the order asked for: the same results as one request with all of them.
     * onProgress({algorithm, index, total, restarting}); onPartial(answer so
     * far) after each planner but the last.  Planners that did not answer
     * are listed in "errors" with `budget` 'time' (over the time budget),
     * 'cancelled' (stopped by the visitor) or 'skipped' (not run after a
     * stop); without any answer the result has no "scenario".
     */
    async plan(body, onProgress, onPartial) {
      const algos = body.algorithms || [];
      const order = runOrder(algos);
      const budgetMs = 1000 * (+this.cfg.time_budget_s || 0);
      const t0 = performance.now();
      let first = null;
      const got = new Map();
      const errors = [];
      const merged = () => Object.assign({}, first || {}, {
        results: algos.flatMap((a) => got.get(a) || []),
        errors: errors.slice().sort((x, y) => algos.indexOf(x.algorithm) - algos.indexOf(y.algorithm)),
      });
      this.running = true;
      this.cancelled = false;
      try {
        for (let i = 0; i < order.length; i++) {
          const a = order[i];
          if (this.cancelled) { errors.push({ algorithm: a, message: I.t('budget_skipped'), budget: 'skipped' }); continue; }
          if (onProgress) onProgress({ algorithm: a, index: i, total: order.length });
          let data;
          try {
            data = await this.request('plan', Object.assign({}, body, { algorithms: [a] }), budgetMs);
          } catch (err) {
            if (err.cancelled) { errors.push({ algorithm: a, message: I.t('budget_cancelled'), budget: 'cancelled' }); continue; }
            if (!err.timeout) throw err;
            errors.push({ algorithm: a, message: I.t('budget_time_stop', { s: I.num(this.cfg.time_budget_s, 0) }), budget: 'time' });
            if (i + 1 < order.length && !this.cancelled) {
              if (onProgress) onProgress({ algorithm: a, index: i, total: order.length, restarting: true });
              try {
                await this.start();   // a fresh worker (Pyodide comes from the browser cache now)
              } catch (e) {
                if (!e.cancelled) throw e;
              }
            }
            continue;
          }
          if (!first) first = data;
          got.set(a, data.results || []);
          errors.push(...(data.errors || []));
          if (onPartial && i + 1 < order.length && !this.cancelled) onPartial(merged());
        }
      } finally {
        if (this.cancelled) this.start().catch(() => {});   // get the next run's worker ready now
        this.running = false;
        this.cancelled = false;
      }
      return Object.assign(merged(), { elapsed_ms: Math.round(performance.now() - t0) });
    }
  }

  /**
   * Commands that repeat a live request locally without the web limits:
   * `evroute viz` with the same planners and viewer options for `vizAlgos`
   * (node ids; the place names follow as a comment), and for a Pareto front
   * stopped by a limit -- `evroute viz` keeps a budget of 1 million labels --
   * the trip saved with `evroute export-scenario` and planned in Python: with
   * ten times the labels, or, when not a single route was found, on a
   * coarser SoC grid (the exact front can be out of reach on a long trip).
   * The value of time has no command-line option: with the cost objective a
   * comment line names it.
   */
  function cliCommand(body, vizAlgos, pareto, model) {
    const pc = (x) => String(Math.round(x * 100) / 100);
    const dep = Math.round(body.departure_s || 0) % 86400;
    const hh = Math.floor(dep / 3600), mm = Math.floor((dep % 3600) / 60);
    const opts = [`--from ${body.origin}`, `--to ${body.destination}`,
      `--soc ${pc(body.initial_soc)}`, `--temp=${body.temperature_c}`,
      `--reserve ${pc(body.reserve_soc)}`, `--arrive ${pc(body.min_arrival_soc)}`,
      `--depart ${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`];
    if (body.vehicle) opts.push(`--vehicle ${body.vehicle}`);
    if (body.dc_max_kw) opts.push(`--dc-max-kw ${body.dc_max_kw}`);
    if (body.min_stop_soc > 0) opts.push(`--min-stop-soc ${pc(body.min_stop_soc)}`);
    if (body.objective && body.objective !== 'time') opts.push(`--objective ${body.objective}`);
    if ((body.speed_factors || []).length > 1) opts.push('--eco');
    if (body.queue_aware === false) opts.push('--no-queue');
    if (body.tariff) opts.push(`--tariff ${body.tariff}`);
    const trip = `# ${model.nodeLabel(body.origin)} → ${model.nodeLabel(body.destination)}`;
    const lines = [];
    if (vizAlgos.length) lines.push(`evroute viz ${body.scenario} --algorithms ${vizAlgos.join(',')} ${opts.join(' ')} -o evroute.html   ${trip}`);
    if (pareto) {
      const o = pareto.options || {};
      const crit = (o.criteria || ['time', 'money']).map((c) => `'${c}'`).join(', ');
      const report = "print(len(f.alternatives), 'routes, complete:', f.stats.extra['complete'])";
      lines.push(`evroute export-scenario ${body.scenario} ${opts.join(' ')} -o trip.json   ${trip}`);
      if (pareto.empty) {
        lines.push('# no Pareto route within the limits: the exact front may be out of reach on this trip;');
        lines.push('# a coarser SoC grid (charge_step_soc=0.1) gives an approximate one');
        lines.push(`python -c "from evroute import Scenario, plan; f = plan(Scenario.load('trip.json').with_preferences(charge_step_soc=0.1), 'pareto', criteria=(${crit}), max_labels=3000000); ${report}"`);
      } else {
        const n = Math.max(10 * (o.max_labels || 1000000), 1000000);
        lines.push(`python -c "from evroute import Scenario, plan; f = plan(Scenario.load('trip.json'), 'pareto', criteria=(${crit}), max_labels=${n}); ${report}"`);
      }
    }
    if (body.objective === 'cost') lines.push(`# value_of_time_per_h = ${body.value_of_time_per_h} (Python: scenario.with_preferences(value_of_time_per_h=...))`);
    return lines.join('\n');
  }

  /* ------------------------------------------------------------ live mode */
  class Live {
    constructor(app) {
      this.app = app;
      this.pick = 'origin';
      this.busy = false;
      const cfg = app.payload.worker || null;
      this.transport = cfg ? new WorkerTransport(cfg) : new HttpTransport();
      this.boot = { phase: null, loaded: {}, total: {}, error: null, reason: null };
      this.note = null;
      this.running = null;   // the algorithms of the run under way (their checkboxes stay ticked)
      this.held = null;      // the request of a run without any answer: the controls keep showing it
      this.el = {
        card: U.$('#live-card'), scenario: U.$('#in-scenario'), fieldScenario: U.$('#field-scenario'),
        origin: U.$('#in-origin'), destination: U.$('#in-destination'), swap: U.$('#btn-swap'), pickSeg: U.$('#pick-seg'),
        vehicle: U.$('#in-vehicle'), soc: U.$('#in-soc'), temp: U.$('#in-temp'), reserve: U.$('#in-reserve'), arrive: U.$('#in-arrive'),
        objective: U.$('#in-objective'), depart: U.$('#in-depart'), vot: U.$('#in-vot'), votUnit: U.$('#vot-unit'),
        eco: U.$('#in-eco'), queue: U.$('#in-queue'), tariff: U.$('#in-tariff'), fieldTariff: U.$('#field-tariff'), algos: U.$('#algo-picks'), plan: U.$('#btn-plan'), error: U.$('#plan-error'),
        noteBox: U.$('#plan-note'), stop: U.$('#btn-stop'), planOwn: U.$('#plan-own'), pickHint: U.$('#pick-hint'),
        outSoc: U.$('#out-soc'), outTemp: U.$('#out-temp'), outReserve: U.$('#out-reserve'), outArrive: U.$('#out-arrive'),
        minstop: U.$('#in-minstop'), outMinstop: U.$('#out-minstop'), chips: U.$('#style-chips'), prio: U.$('#prio-seg'),
        dcmax: U.$('#in-dcmax'), vehCard: U.$('#veh-card'),
        busyText: U.$('#map-busy-text'), bootBox: U.$('#boot'),
      };
      // searchable pickers; the <select>s stay the source of truth
      const Combo = EVR.combo.Combo;
      this.combos = {
        scenario: new Combo(this.el.scenario, { extra: (v, o) => o.title }),
        origin: new Combo(this.el.origin),
        destination: new Combo(this.el.destination),
        vehicle: new Combo(this.el.vehicle, { describe: (key) => vehicleOption(this.vehicleInfo(key)), extra: (key) => key }),
      };
      this.el.card.hidden = false;
      if (this.el.planOwn) this.el.planOwn.hidden = false;   // phones: a shortcut from the map to the form
      try {
        // no Shift key on a touch screen: the A/B switch chooses
        if (this.el.pickHint && window.matchMedia('(pointer: coarse)').matches) this.el.pickHint.dataset.i18n = 'pick_hint_touch';
      } catch (e) { /* no matchMedia */ }
      if (this.transport.kind === 'worker') {
        this.transport.prepare = app.payload.scenario_preset || null;
        this.transport.onBoot = (m, restart) => this._onBoot(m, restart);
        this._showBoot(true);
      }
      this._wire();
      this.fill();
      this._loadScenarioList();
    }

    _wire() {
      const el = this.el;
      for (const [inp, out, fmt] of [
        [el.soc, el.outSoc, (v) => I.pct(v / 100, 0)], [el.temp, el.outTemp, (v) => `${v} °C`],
        [el.reserve, el.outReserve, (v) => I.pct(v / 100, 0)], [el.arrive, el.outArrive, (v) => I.pct(v / 100, 0)],
        [el.minstop, el.outMinstop, (v) => (v > 0 ? I.pct(v / 100, 0) : I.t('min_stop_off'))],
      ]) {
        const upd = () => {
          out.textContent = fmt(+inp.value);
          const f = (inp.value - inp.min) / (inp.max - inp.min);
          inp.style.setProperty('--fill', `${(f * 100).toFixed(1)}%`);
        };
        inp.addEventListener('input', upd);
        inp._upd = upd;
      }
      // charging style chips: a minimum stop SoC relative to the reserve; the pressed one again: off
      el.minstop.addEventListener('input', () => this._syncChips());
      el.reserve.addEventListener('input', () => this._syncChips());
      for (const b of el.chips.querySelectorAll('button')) {
        b.addEventListener('click', () => {
          const want = this._styleValue(b.dataset.style);
          el.minstop.value = String(+el.minstop.value === want ? 0 : want);
          el.minstop._upd();
          this._syncChips();
        });
      }
      // priority (everyday mode): the objective of the plan
      for (const b of el.prio.querySelectorAll('button')) {
        b.addEventListener('click', () => { el.objective.value = b.dataset.prio; this._syncPrio(); });
      }
      el.objective.addEventListener('change', () => this._syncPrio());
      el.vehicle.addEventListener('change', () => this._renderVehicle());
      el.dcmax.addEventListener('change', () => { this._dcLimit(); this._renderVehicle(); });
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
      if (el.stop) el.stop.addEventListener('click', () => this.transport.cancel());
      const retry = U.$('#boot-retry');
      if (retry) {
        retry.addEventListener('click', () => {
          if (this.boot.reason === 'stale') { window.location.reload(); return; }
          this.boot.error = null;
          this.boot.reason = null;
          this._renderBoot();
          this._loadScenarioList();
          this.plan();
        });
      }
    }

    setPick(which) {
      this.pick = which;
      for (const b of this.el.pickSeg.querySelectorAll('button')) b.setAttribute('aria-pressed', String(b.dataset.pick === which));
    }

    /** The trip parameters of the current payload, shaped like a plan request. */
    _scenarioValues() {
      const sc = this.app.model.scenario || {};
      const prefs = sc.preferences || {};
      const q = this.app.currentQuery();
      return {
        vehicle: (sc.vehicle || {}).key || '',
        initial_soc: q.initial_soc ?? 0.8,
        temperature_c: (sc.conditions || {}).temperature_c ?? 20,
        reserve_soc: prefs.reserve_soc ?? 0.1,
        min_arrival_soc: prefs.min_arrival_soc ?? 0.1,
        min_stop_soc: prefs.min_stop_soc ?? 0,
        dc_max_kw: (sc.vehicle || {}).dc_max_kw ?? null,
        objective: prefs.objective || 'time',
        departure_s: q.departure_s || 0,
        value_of_time_per_h: prefs.value_of_time_per_h ?? 15,
        speed_factors: prefs.speed_factors || [1],
        queue_aware: prefs.queue_aware !== false,
        // the charging tariff starts switched on; after a plan the switch shows what was planned
        tariff_on: this.app.model.results.length ? !!sc.tariff : true,
      };
    }

    /** The trip parameters the controls show now (before fill() rebuilds them). */
    _controlValues() {
      const r = this.request();
      return Object.assign(r, { vehicle: r.vehicle || '', tariff_on: this.el.tariff.checked });
    }

    /** Fill every control from the current payload (or from a request no planner answered). */
    fill() {
      const app = this.app, el = this.el, p = app.payload;
      // during a run (results so far shown) the controls keep what the visitor may be editing
      const v = this.running ? this._controlValues() : this.held ? Object.assign({}, this.held, { tariff_on: this.held.tariff !== 'flat' }) : this._scenarioValues();
      // places: settlements first by importance, then the rest
      const places = app.model.places.filter((x) => x.kind === 'city' || x.kind === 'town');
      const list = (places.length ? places : app.model.places).slice().sort((a, b) => a.name.localeCompare(b.name, I.lang === 'tr' ? 'tr' : 'en'));
      for (const sel of [el.origin, el.destination]) {
        sel.textContent = '';
        for (const pl of list) sel.appendChild(U.el('option', { value: pl.node, text: pl.name }));
      }
      this._syncOD();
      el.vehicle.textContent = '';
      const vehicles = (p.vehicles || []).length ? p.vehicles : [(app.model.scenario || {}).vehicle || {}].filter((x) => x.key);
      for (const veh of vehicles) el.vehicle.appendChild(U.el('option', { value: veh.key, text: `${veh.name}` }));
      el.vehicle.value = v.vehicle || '';
      this.combos.vehicle.sync();
      el.dcmax.value = U.isNum(v.dc_max_kw) ? String(Math.round(v.dc_max_kw)) : '';
      el.soc.value = Math.round(v.initial_soc * 100);
      el.temp.value = Math.round(v.temperature_c);
      el.reserve.value = Math.round(v.reserve_soc * 100);
      el.arrive.value = Math.round(v.min_arrival_soc * 100);
      el.minstop.value = Math.round((v.min_stop_soc || 0) * 100);
      el.objective.value = v.objective;
      el.depart.value = I.clock(v.departure_s, false);
      el.vot.value = v.value_of_time_per_h;
      el.votUnit.textContent = `(${app.model.currency}${I.t('per_hour')})`;
      el.eco.checked = (v.speed_factors || [1]).length > 1;
      el.queue.checked = v.queue_aware !== false;
      // charging-tariff switch: the server's demonstration tariff (or the preset's own tariff)
      const demo = p.demo_tariff || null;
      const base = (p.scenario || {}).tariff || null;
      el.fieldTariff.hidden = !(demo || base);
      el.tariff.checked = v.tariff_on;
      EVR.tooltip.setTip(el.fieldTariff, `${I.t('tip_tariff_toggle')} ${EVR.panels.tariffText(base || demo)} · ${I.t('tariff_illustrative')}`);
      for (const inp of [el.soc, el.temp, el.reserve, el.arrive, el.minstop]) inp._upd();
      this._syncChips();
      this._syncPrio();
      this._renderVehicle();
      // algorithm checkboxes: those of the run under way, else those of the results shown
      const have = new Set(this.running || app.model.results.map((r) => r.algorithm));
      if (!this.running) for (const e of this.note ? this.note.stopped : []) have.add(e);
      el.algos.textContent = '';
      for (const a of p.algorithms || []) {
        const cb = U.el('input', { type: 'checkbox', value: a.name });
        // the server's defaults: without greedy and station graph on a large (real-data) network
        cb.checked = have.size ? have.has(a.name) : (p.default_algorithms || DEFAULT_ALGOS).includes(a.name);
        el.algos.appendChild(U.el('label', { class: 'check', 'data-tip': EVR.panels.algoTip(a), style: { '--algo': app.theme.dark ? a.color_dark : a.color } },
          cb, U.el('span', { text: I.algoTitle(a.name, a.short_title || a.title) })));
      }
      this._renderNote();
      this._renderBoot();
    }

    _syncOD() {
      const q = this.app.currentQuery();
      for (const [sel, node] of [[this.el.origin, q.origin], [this.el.destination, q.destination]]) {
        if (!Array.from(sel.options).some((o) => +o.value === node)) {
          sel.appendChild(U.el('option', { value: node, text: this.app.model.nodeLabel(node) }));
        }
        sel.value = String(node);
      }
      this.combos.origin.sync();
      this.combos.destination.sync();
    }

    /* ------------------------------------------------- vehicle, style, priority */
    /** The payload's entry of vehicle `key` with the scenario's vehicle data when it is that vehicle. */
    vehicleInfo(key) {
      const sc = (this.app.model.scenario || {}).vehicle || {};
      const v = (this.app.payload.vehicles || []).find((x) => x.key === key) || (sc.key === key ? sc : null);
      if (!v) return null;
      return sc.key === key ? Object.assign({}, sc, v, { capacity_kwh: sc.capacity_kwh, dc_curve: sc.dc_curve }) : v;
    }

    /** The DC limit field: empty, or a number clamped to the range the planner accepts (3..500 kW). */
    _dcLimit() {
      const raw = this.el.dcmax.value.trim();
      if (!raw) return null;
      const x = Number(raw.replace(',', '.'));
      if (!isFinite(x) || x <= 0) { this.el.dcmax.value = ''; return null; }
      const v = Math.round(U.clamp(x, 3, 500));
      if (String(v) !== raw) this.el.dcmax.value = String(v);
      return v;
    }

    _renderVehicle() {
      const el = this.el;
      const v = this.vehicleInfo(el.vehicle.value);
      const lim = this._dcLimit();
      U.setChildren(el.vehCard, v ? EVR.panels.vehicleCard(this.app, v, { limit: lim }) : []);
      el.dcmax.placeholder = v && v.dc_peak_kw > 0 ? I.t('dc_limit_ph', { kw: `${I.num(v.dc_peak_kw, 0)} kW` }) : '';
      el.dcmax.disabled = !!v && !(v.dc_peak_kw > 0);
    }

    /** Minimum stop SoC [%] of a charging style: the reserve + 10 / 30 / 40 points, at most the highest charge. */
    _styleValue(style) {
      const add = { hurry: 10, balanced: 30, relaxed: 40 }[style] || 0;
      const prefs = this.app.currentPrefs();
      const top = Math.min(+this.el.minstop.max, Math.round(100 * (prefs.max_charge_soc ?? 1)));
      return Math.min(top, +this.el.reserve.value + add);
    }

    _syncChips() {
      const cur = +this.el.minstop.value;
      for (const b of this.el.chips.querySelectorAll('button')) {
        const val = this._styleValue(b.dataset.style);
        b.setAttribute('aria-pressed', String(cur > 0 && cur === val));
        EVR.tooltip.setTip(b, I.t(`tip_style_${b.dataset.style}`, { p: I.pct(val / 100, 0) }));
      }
    }

    _syncPrio() {
      const obj = this.el.objective.value;
      EVR.tooltip.setTip(this.el.plan, I.t(this.app.everyday ? 'tip_plan_everyday' : 'tip_plan'));
      for (const b of this.el.prio.querySelectorAll('button')) {
        b.setAttribute('aria-pressed', String(b.dataset.prio === obj));
        const vot = `${I.money(+this.el.vot.value || 0, this.app.model.currency, 0)}${I.t('per_hour')}`;
        EVR.tooltip.setTip(b, b.dataset.prio === 'cost' ? I.t('tip_prio_cheap', { v: vot }) : I.t('tip_prio_fast'));
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

    /** Everyday use: one planner, BLS-A* (else the first of EVERYDAY_ALGOS the page offers). */
    everydayAlgorithm() {
      const offered = (this.app.payload.algorithms || []).map((a) => a.name);
      return EVERYDAY_ALGOS.find((a) => offered.includes(a)) || offered[0];
    }

    /** The research view and everyday use show different parts of the form and the note. */
    onViewChanged() {
      this._syncPrio();
      this._renderNote();
    }

    request() {
      const el = this.el, app = this.app;
      const q = app.currentQuery();
      const [hh, mm] = (el.depart.value || '08:00').split(':').map(Number);
      const everyday = app.everyday;
      const algorithms = everyday ? [this.everydayAlgorithm()].filter(Boolean)
        : U.$$('input[type=checkbox]', el.algos).filter((c) => c.checked).map((c) => c.value);
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
        min_stop_soc: +el.minstop.value / 100,
        dc_max_kw: this._dcLimit() || undefined,
        objective: el.objective.value,
        value_of_time_per_h: Math.max(0, +el.vot.value || 0),
        speed_factors: el.eco.checked ? [1.0, 0.9, 0.8] : [1.0],
        queue_aware: everyday ? true : el.queue.checked,   // everyday use: expected queues always count
        departure_s: ((hh || 0) * 3600 + (mm || 0) * 60),
        // on: the preset's own tariff, else the demonstration tariff ("80"); off: "flat"
        tariff: el.fieldTariff.hidden ? undefined : el.tariff.checked ? ((app.payload.scenario || {}).tariff ? undefined : '80') : 'flat',
      };
    }

    showError(msg) {
      this.el.error.hidden = !msg;
      this.el.error.textContent = msg || '';
    }

    setBusy(on, text) {
      this.busy = on;
      this.el.plan.disabled = on;
      this.el.plan.classList.toggle('busy', on);
      // while the in-browser planner loads, the boot card stands in for the spinner
      U.$('#map-busy').hidden = !on || this._bootVisible();
      if (this.el.busyText) this.el.busyText.textContent = text || I.t('planning');
      if (this.el.stop) this.el.stop.hidden = !on || !this.transport.canCancel || !this.transport.running;
    }

    _errorText(err) {
      if (err && err.reason === 'stale') return I.t('boot_stale');
      return I.t('err_generic', { m: (err && err.message) || String(err) });
    }

    async plan() {
      if (this.busy) return;
      const body = this.request();
      if (body.origin === body.destination) { this.showError(I.t('err_same_od')); return; }
      if (!body.algorithms.length) { this.showError(I.t('err_no_algo')); return; }
      this.showError(null);
      this.setBusy(true);
      const titles = new Map((this.app.payload.algorithms || []).map((a) => [a.name, I.algoTitle(a.name, a.short_title || a.title)]));
      try {
        await this.transport.start();
        this.running = body.algorithms.slice();
        const t0 = performance.now();
        let shown = 0;
        const data = await this.transport.plan(body, (p) => {
          const a = titles.get(p.algorithm) || p.algorithm;
          if (this.app.everyday) { this.setBusy(true, I.t('planning')); return; }   // no planner names in everyday use
          this.setBusy(true, p.restarting ? I.t('restarting', { a }) : I.t('planning_algo', { a, i: p.index + 1, n: p.total }));
        }, (partial) => {
          // a long run shows its results so far (a quick one only its final answer)
          const now = performance.now();
          if (now - t0 < 1500 || now - shown < 1500 || !partial.scenario) return;
          shown = now;
          this.note = null;
          this.held = null;
          this.app.applyPlanResponse(partial);
        });
        this.running = null;
        this.note = this._budgetNote(body, data);
        if (data.scenario) {
          this.held = null;
          this.app.applyPlanResponse(data);
        } else {
          this.held = body;          // no planner answered: no results, the controls keep this trip
          this.app.clearResults();
        }
        this.fill();
        const errs = (data.errors || []).filter((e) => !e.budget).map((e) => `${titles.get(e.algorithm) || e.algorithm}: ${e.message}`);
        this.showError(errs.length ? errs.join(' · ') : null);
        if (data.results.length) {
          this.app.toast(this.app.everyday ? I.t('toast_plan_ready')
            : I.t('toast_planned', { n: data.results.length, ms: I.num(performance.now() - t0, 0) }));
        }
      } catch (err) {
        this.running = null;
        if (this.transport.kind === 'worker' && !this.transport.started) this._bootFailed(err);
        else this.showError(this._errorText(err));
      } finally {
        this.running = null;
        this.setBusy(false);
      }
    }

    /* ------------------------------------------------ budgets (web site) */
    /** Which planners a web limit (or the visitor) stopped, and the command that runs them locally. */
    _budgetNote(body, data) {
      if (this.transport.kind !== 'worker') return null;
      const items = [];
      for (const r of data.results || []) {
        if (!EVR.panels.budgetStopped(r)) continue;
        const status = ((r.stats || {}).extra || {}).status;
        items.push(status === 'time_limit' ? { algorithm: r.algorithm, kind: 'time_limit' }
          : { algorithm: r.algorithm, kind: 'labels', labels: (r.options || {}).max_labels || null });
      }
      for (const e of data.errors || []) {
        if (e.budget) items.push({ algorithm: e.algorithm, kind: e.budget });
      }
      if (!items.length) return null;
      const order = (this.app.payload.algorithms || []).map((a) => a.name);
      items.sort((a, b) => order.indexOf(a.algorithm) - order.indexOf(b.algorithm));
      const stopped = items.map((x) => x.algorithm);
      // a Pareto front cut by a limit: `evroute viz` has a label budget as well, so the Python API it is
      const front = (data.results || []).find((r) => r.algorithm === 'pareto' && EVR.panels.budgetStopped(r));
      const viz = stopped.filter((a) => !(front && a === 'pareto'));
      const pareto = front ? { options: front.options || {}, empty: !front.feasible } : null;
      return { stopped, items, install: this.transport.cfg.install || '', cmd: cliCommand(body, viz, pareto, this.app.model) };
    }

    _renderNote() {
      const box = this.el.noteBox;
      if (!box) return;
      const n = this.note;
      box.hidden = !n;
      if (!n) { box.textContent = ''; return; }
      const info = (a) => (this.app.payload.algorithms || []).find((x) => x.name === a) || {};
      const title = (a) => I.algoTitle(a, info(a).short_title || a);
      const cfg = this.transport.cfg || {};
      const what = (x) => {
        if (x.kind === 'time') return I.t('budget_time_stop', { s: I.num(cfg.time_budget_s, 0) });
        if (x.kind === 'time_limit') return I.t('budget_time_limit', { s: I.num(cfg.time_limit_s, 0) });
        if (x.kind === 'cancelled') return I.t('budget_cancelled');
        if (x.kind === 'skipped') return I.t('budget_skipped');
        return x.labels ? I.t('budget_labels', { n: I.num(x.labels, 0) }) : I.t('g_incomplete');
      };
      if (this.app.everyday) {
        // everyday use: that the plan may be incomplete, without planner names or commands
        U.setChildren(box, U.el('p', {}, U.el('b', { text: I.t('budget_title') }), ' ', Array.from(new Set(n.items.map(what))).join(' · ')));
        return;
      }
      const code = [n.install, n.cmd].filter(Boolean).join('\n');
      const copy = U.el('button', { type: 'button', class: 'btn-copy', text: I.t('copy') });
      copy.addEventListener('click', () => {
        const done = () => { copy.textContent = I.t('copied'); setTimeout(() => { copy.textContent = I.t('copy'); }, 1600); };
        try {
          navigator.clipboard.writeText(code).then(done, () => this._selectCode(box));
        } catch (e) {
          this._selectCode(box);
        }
      });
      U.setChildren(box,
        U.el('p', {}, U.el('b', { text: I.t('budget_title') }), ' ', n.items.map((x) => `${title(x.algorithm)}: ${what(x)}`).join(' · ')),
        U.el('p', { text: I.t('budget_hint') }),
        U.el('pre', { class: 'cmd', tabindex: '0' }, U.el('code', { text: code })),
        U.el('div', { class: 'cmd-actions' }, copy));
    }

    /** Without the clipboard API: select the command so that the visitor can copy it. */
    _selectCode(box) {
      try {
        const range = document.createRange();
        range.selectNodeContents(U.$('pre.cmd', box));
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      } catch (e) { /* nothing to do */ }
    }

    /* ------------------------------------------------- loading (web site) */
    _bootVisible() { return !!this.el.bootBox && !this.el.bootBox.hidden; }

    _showBoot(on) {
      if (this.el.bootBox) this.el.bootBox.hidden = !on;
      if (on) U.$('#map-busy').hidden = true;
    }

    _onBoot(m, restart) {
      const b = this.boot;
      if (restart) return;          // a restart after a time budget: the busy text says so
      if (m.phase === 'ready') {
        b.phase = 'ready';
        b.timings = m.timings || null;     // worker-side load times, and bytes from the network / the cache
        b.network = m.network || null;
        b.python = m.python || null;
        this._showBoot(false);
        if (this.busy) this.setBusy(true, this.el.busyText ? this.el.busyText.textContent : null);
        return;
      }
      b.phase = m.phase;
      if (m.total) b.total[m.phase] = m.total;
      if (m.loaded !== undefined) b.loaded[m.phase] = m.loaded;
      this._renderBoot();
    }

    _bootFailed(err) {
      this.boot.error = err.message || String(err);
      this.boot.reason = err.reason || null;
      this._showBoot(true);
      this._renderBoot();
    }

    _renderBoot() {
      const box = this.el.bootBox;
      if (!box || this.transport.kind !== 'worker') return;
      const cfg = this.transport.cfg, b = this.boot;
      const mb = (x) => `${I.num(x / 1e6, 1)} MB`;
      U.$('#boot-lead').textContent = I.t('boot_lead', { mb: mb(cfg.download_bytes || 0), v: cfg.pyodide_version || '' });
      const order = ['download', 'install', 'scenario'];
      const cur = order.indexOf(b.phase);
      for (const li of U.$$('li[data-step]', box)) {
        const k = li.dataset.step, i = order.indexOf(k);
        const state = b.phase === 'ready' || i < cur ? 'done' : i === cur ? 'active' : 'todo';
        li.dataset.state = state;
        const tot = b.total[k], got = b.loaded[k];
        let frac = state === 'done' ? 1 : 0;
        if (state === 'active' && tot) frac = Math.min(1, (got || 0) / tot);
        const bar = U.$('.boot-bar i', li);
        if (bar) bar.style.width = `${(100 * frac).toFixed(1)}%`;
        // the worker counts decoded bytes; the step shows what is transferred (as the lead text does)
        const tr = (cfg.transfer || {})[k];
        const val = U.$('.boot-val', li);
        if (val) val.textContent = tr && state !== 'todo' ? `${mb(frac * tr)} / ${mb(tr)}` : state === 'done' ? '✓' : '';
      }
      const err = U.$('#boot-error'), retry = U.$('#boot-retry');
      let msg = '';
      if (b.error) {
        msg = b.reason === 'stale' ? I.t('boot_stale')
          : b.reason === 'integrity' ? I.t('boot_integrity', { m: b.error })
            : `${I.t('boot_failed', { m: b.error }).replace(/([^.!?])$/, '$1.')} ${I.t('boot_blocked')}`;
      }
      err.hidden = !b.error;
      err.textContent = msg;
      retry.hidden = !b.error;
      retry.textContent = I.t(b.reason === 'stale' ? 'boot_reload' : 'boot_retry');
    }

    async _loadScenarioList(again) {
      try {
        const data = await this.transport.request('scenarios');
        const sel = this.el.scenario;
        sel.textContent = '';
        for (const s of data.scenarios || []) sel.appendChild(U.el('option', { value: s.name, text: s.name, title: s.description || '' }));
        const cur = this.app.payload.scenario_preset || ((this.app.model.scenario.meta || {}).preset) || this.app.model.scenario.name;
        sel.value = cur;
        this.combos.scenario.sync();
        this.el.fieldScenario.hidden = !(data.scenarios || []).length;
      } catch (e) {
        // a worker stopped by a time limit (or by the visitor) while the list was asked for: ask again
        if ((e.timeout || e.cancelled) && !again) { this._loadScenarioList(true); return; }
        this.el.fieldScenario.hidden = true;
      }
    }

    async switchScenario(name) {
      if (!name || this.busy) return;
      this.setBusy(true);
      this.showError(null);
      try {
        const data = await this.transport.request('scenario', { name });
        this.note = null;
        this.held = null;
        if (this.transport.kind === 'worker') this.transport.prepare = name;
        this.app.loadPayload(data);
        this.fill();
      } catch (err) {
        this.showError(this._errorText(err));
        this.setBusy(false);
        return;
      }
      this.setBusy(false);
      this.plan();
    }
  }

  /** What the vehicle picker shows under a vehicle's name: battery, DC power and a chemistry badge. */
  function vehicleOption(v) {
    if (!v) return {};
    const parts = [];
    if (U.isNum(v.battery_kwh)) parts.push(`${I.num(v.battery_kwh, 0)} kWh`);
    if (U.isNum(v.dc_peak_kw)) parts.push(v.dc_peak_kw > 0 ? `${I.num(v.dc_peak_kw, 0)} kW DC` : I.t('veh_no_dc'));
    return { sub: parts.join(' · '), badge: v.chemistry || '', badgeClass: v.chemistry ? `chem-${v.chemistry}` : '' };
  }

  EVR.live = { Live, DEFAULT_ALGOS, RUN_ORDER, runOrder, HttpTransport, WorkerTransport, cliCommand };
})(window.EVR = window.EVR || {});
