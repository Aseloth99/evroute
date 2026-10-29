/* EVRoute viewer -- live.js
 *
 * Live planning: origin/destination pickers and map picking, vehicle and
 * trip parameters, algorithm checkboxes and the Plan button, which asks the
 * planner and replaces the results in place.
 *
 * The planner is reached through a *transport* with one interface,
 * request(endpoint, args) and plan(body, onProgress), answering with the
 * JSON of evroute.server.api_call (endpoints health, scenarios, scenario,
 * plan):
 *
 *   HttpTransport    `evroute serve` / `python -m evroute.server`: fetch()
 *                    to api/<endpoint> on the local server.
 *   WorkerTransport  the project web site: EVRoute runs in the visitor's
 *                    browser, in a Web Worker under Pyodide (the page's
 *                    payload carries a "worker" block, written by
 *                    tools/build_site.py).  The worker calls the same Python
 *                    functions as the server.  A plan is requested one
 *                    algorithm at a time, so the page can show progress and
 *                    enforce the web's time budget per planner: a planner
 *                    that runs over it is stopped by restarting the worker.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;

  const DEFAULT_ALGOS = ['greedy', 'station-graph', 'bls-astar', 'cfp-astar'];

  /* ------------------------------------------------------------ transports */
  /** The local planning server (evroute serve): one HTTP request per call. */
  class HttpTransport {
    constructor() { this.kind = 'http'; }

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
  }

  /**
   * EVRoute in the browser: a Web Worker that loads Pyodide and the evroute
   * wheel and answers the same API calls (see site/worker.js).  `cfg` is
   * the payload's "worker" block: script, pyodide (index URL), wheel,
   * service (PlanningService options), time_budget_s, download_bytes, ...
   */
  class WorkerTransport {
    constructor(cfg) {
      this.kind = 'worker';
      this.cfg = cfg;
      this.worker = null;
      this.ready = null;
      this.seq = 0;
      this.pending = new Map();
      this.onBoot = null;        // progress of the first start: {phase, loaded, total} | {phase: 'ready', ...}
      this.started = false;      // a worker has been ready once (later starts are restarts)
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
        const boot = (m) => { if (this.onBoot) this.onBoot(m, this.started); };
        w.onmessage = (ev) => {
          const m = ev.data || {};
          if (m.type === 'progress') boot(m);
          else if (m.type === 'ready') { boot(Object.assign({ phase: 'ready' }, m)); this.started = true; resolve(m); }
          else if (m.type === 'failed') reject(new Error(m.message || 'worker failed'));
          else if (m.type === 'reply') {
            const done = this.pending.get(m.id);
            if (done) { this.pending.delete(m.id); done.reply(m); }
          }
        };
        w.onerror = (ev) => {
          ev.preventDefault();
          const err = new Error(ev.message || 'worker error');
          reject(err);
          this._kill(err);
        };
        w.postMessage({ type: 'init', config: this.cfg });
      });
    }

    /** Terminate the worker; pending calls fail with `err`. */
    _kill(err) {
      if (this.worker) this.worker.terminate();
      this.worker = null;
      this.ready = null;
      const calls = Array.from(this.pending.values());
      this.pending.clear();
      for (const c of calls) c.fail(err || new Error('worker stopped'));
    }

    async request(endpoint, args, timeoutMs) {
      await this.start();
      const w = this.worker;
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
     * Plan one algorithm per request, in the order given (registry order),
     * and merge the answers: the same results as one request with all of
     * them.  `onProgress({algorithm, index, total, restarting})`.
     */
    async plan(body, onProgress) {
      const algos = body.algorithms || [];
      const budgetMs = 1000 * (+this.cfg.time_budget_s || 0);
      const t0 = performance.now();
      let first = null;
      const results = [], errors = [];
      for (let i = 0; i < algos.length; i++) {
        const a = algos[i];
        if (onProgress) onProgress({ algorithm: a, index: i, total: algos.length });
        let data;
        try {
          data = await this.request('plan', Object.assign({}, body, { algorithms: [a] }), budgetMs);
        } catch (err) {
          if (!err.timeout) throw err;
          errors.push({ algorithm: a, message: I.t('budget_time_stop', { s: I.num(this.cfg.time_budget_s, 0) }), budget: 'time' });
          if (i + 1 < algos.length) {
            if (onProgress) onProgress({ algorithm: a, index: i, total: algos.length, restarting: true });
            await this.start();   // a fresh worker (Pyodide comes from the browser cache now)
          }
          continue;
        }
        if (!first) first = data;
        results.push(...(data.results || []));
        errors.push(...(data.errors || []));
      }
      if (!first) {
        const err = new Error(I.t('budget_all_stopped', { s: I.num(this.cfg.time_budget_s, 0) }));
        err.errors = errors;
        throw err;
      }
      return Object.assign(first, { results, errors, elapsed_ms: Math.round(performance.now() - t0) });
    }
  }

  /**
   * Commands that repeat a live request locally without the web budgets:
   * `evroute viz` with the same planners and viewer options for `vizAlgos`
   * (node ids; the place names follow as a comment), and for a Pareto front
   * stopped by its label budget -- the budget `evroute viz` uses as well --
   * the trip saved with `evroute export-scenario` and planned in Python with
   * ten times the labels.  The value of time has no command-line option:
   * with the cost objective a comment line names it.
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
    if (body.objective && body.objective !== 'time') opts.push(`--objective ${body.objective}`);
    if ((body.speed_factors || []).length > 1) opts.push('--eco');
    if (body.queue_aware === false) opts.push('--no-queue');
    if (body.tariff) opts.push(`--tariff ${body.tariff}`);
    const trip = `# ${model.nodeLabel(body.origin)} → ${model.nodeLabel(body.destination)}`;
    const lines = [];
    if (vizAlgos.length) lines.push(`evroute viz ${body.scenario} --algorithms ${vizAlgos.join(',')} ${opts.join(' ')} -o evroute.html   ${trip}`);
    if (pareto) {
      const crit = (pareto.criteria || ['time', 'money']).map((c) => `'${c}'`).join(', ');
      const n = Math.max(10 * (pareto.max_labels || 1000000), 1000000);
      lines.push(`evroute export-scenario ${body.scenario} ${opts.join(' ')} -o trip.json   ${trip}`);
      lines.push(`python -c "from evroute import Scenario, plan; f = plan(Scenario.load('trip.json'), 'pareto', criteria=(${crit}), max_labels=${n}); print(len(f.alternatives), 'routes, complete:', f.stats.extra['complete'])"`);
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
      this.boot = { phase: null, loaded: {}, total: {}, error: null };
      this.note = null;
      this.el = {
        card: U.$('#live-card'), scenario: U.$('#in-scenario'), fieldScenario: U.$('#field-scenario'),
        origin: U.$('#in-origin'), destination: U.$('#in-destination'), swap: U.$('#btn-swap'), pickSeg: U.$('#pick-seg'),
        vehicle: U.$('#in-vehicle'), soc: U.$('#in-soc'), temp: U.$('#in-temp'), reserve: U.$('#in-reserve'), arrive: U.$('#in-arrive'),
        objective: U.$('#in-objective'), depart: U.$('#in-depart'), vot: U.$('#in-vot'), votUnit: U.$('#vot-unit'),
        eco: U.$('#in-eco'), queue: U.$('#in-queue'), tariff: U.$('#in-tariff'), fieldTariff: U.$('#field-tariff'), algos: U.$('#algo-picks'), plan: U.$('#btn-plan'), error: U.$('#plan-error'),
        noteBox: U.$('#plan-note'),
        outSoc: U.$('#out-soc'), outTemp: U.$('#out-temp'), outReserve: U.$('#out-reserve'), outArrive: U.$('#out-arrive'),
        busyText: U.$('#map-busy-text'), bootBox: U.$('#boot'),
      };
      this.el.card.hidden = false;
      if (this.transport.kind === 'worker') {
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
      const retry = U.$('#boot-retry');
      if (retry) retry.addEventListener('click', () => { this.boot.error = null; this._renderBoot(); this._loadScenarioList(); this.plan(); });
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
      // charging-tariff switch: the server's demonstration tariff (or the preset's own tariff)
      const demo = p.demo_tariff || null;
      const base = (p.scenario || {}).tariff || null;
      el.fieldTariff.hidden = !(demo || base);
      el.tariff.checked = !!sc.tariff;
      el.fieldTariff.title = `${EVR.panels.tariffText(base || demo)} · ${I.t('tariff_illustrative')}`;
      for (const inp of [el.soc, el.temp, el.reserve, el.arrive]) inp._upd();
      // algorithm checkboxes
      const have = new Set(app.model.results.map((r) => r.algorithm));
      for (const e of this.note ? this.note.stopped : []) have.add(e);
      el.algos.textContent = '';
      for (const a of p.algorithms || []) {
        const cb = U.el('input', { type: 'checkbox', value: a.name });
        // the server's defaults: without greedy and station graph on a large (real-data) network
        cb.checked = have.size ? have.has(a.name) : (p.default_algorithms || DEFAULT_ALGOS).includes(a.name);
        el.algos.appendChild(U.el('label', { class: 'check', title: I.algoDescription(a), style: { '--algo': app.theme.dark ? a.color_dark : a.color } },
          cb, U.el('span', { text: a.short_title || a.title || a.name })));
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
    }

    async plan() {
      if (this.busy) return;
      const body = this.request();
      if (body.origin === body.destination) { this.showError(I.t('err_same_od')); return; }
      if (!body.algorithms.length) { this.showError(I.t('err_no_algo')); return; }
      this.showError(null);
      this.setBusy(true);
      const titles = new Map((this.app.payload.algorithms || []).map((a) => [a.name, a.short_title || a.title || a.name]));
      try {
        await this.transport.start();
        const t0 = performance.now();
        const data = await this.transport.plan(body, (p) => {
          const a = titles.get(p.algorithm) || p.algorithm;
          this.setBusy(true, p.restarting ? I.t('restarting', { a }) : I.t('planning_algo', { a, i: p.index + 1, n: p.total }));
        });
        this.note = this._budgetNote(body, data);
        this.app.applyPlanResponse(data);
        this.fill();
        const errs = (data.errors || []).filter((e) => !e.budget).map((e) => `${e.algorithm}: ${e.message}`);
        this.showError(errs.length ? errs.join(' · ') : null);
        this.app.toast(I.t('toast_planned', { n: data.results.length, ms: I.num(performance.now() - t0, 0) }));
      } catch (err) {
        if (this.transport.kind === 'worker' && !this.transport.started) this._bootFailed(err);
        else this.showError(I.t('err_generic', { m: err.message || String(err) }));
      } finally {
        this.setBusy(false);
      }
    }

    /* ------------------------------------------------ budgets (web site) */
    /** Which planners a web budget stopped, and the command that runs them locally. */
    _budgetNote(body, data) {
      if (this.transport.kind !== 'worker') return null;
      const items = [];
      for (const r of data.results || []) {
        if (EVR.panels.budgetStopped(r)) items.push({ algorithm: r.algorithm, labels: (r.options || {}).max_labels || null });
      }
      for (const e of data.errors || []) {
        if (e.budget === 'time') items.push({ algorithm: e.algorithm, time: true });
      }
      if (!items.length) return null;
      const order = (this.app.payload.algorithms || []).map((a) => a.name);
      items.sort((a, b) => order.indexOf(a.algorithm) - order.indexOf(b.algorithm));
      const stopped = items.map((x) => x.algorithm);
      // a Pareto front cut by its label budget: `evroute viz` has the same budget, so the Python API it is
      const front = (data.results || []).find((r) => r.algorithm === 'pareto' && EVR.panels.budgetStopped(r));
      const viz = stopped.filter((a) => !(front && a === 'pareto'));
      return { stopped, items, install: this.transport.cfg.install || '',
        cmd: cliCommand(body, viz, front ? front.options || {} : null, this.app.model) };
    }

    _renderNote() {
      const box = this.el.noteBox;
      if (!box) return;
      const n = this.note;
      box.hidden = !n;
      if (!n) { box.textContent = ''; return; }
      const title = (a) => ((this.app.payload.algorithms || []).find((x) => x.name === a) || {}).short_title || a;
      const what = (x) => (x.time ? I.t('budget_time_stop', { s: I.num(this.transport.cfg.time_budget_s, 0) })
        : x.labels ? I.t('budget_labels', { n: I.num(x.labels, 0) }) : I.t('g_incomplete'));
      const code = [n.install, n.cmd].filter(Boolean).join('\n');
      U.setChildren(box,
        U.el('p', {}, U.el('b', { text: I.t('budget_title') }), ' ', n.items.map((x) => `${title(x.algorithm)}: ${what(x)}`).join(' · ')),
        U.el('p', { text: I.t('budget_hint') }),
        U.el('pre', { class: 'cmd', tabindex: '0' }, U.el('code', { text: code })));
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
        const val = U.$('.boot-val', li);
        if (val) val.textContent = k === 'download' && tot && state !== 'todo' ? `${mb(Math.min(got || 0, tot))} / ${mb(tot)}` : state === 'done' ? '✓' : '';
      }
      const err = U.$('#boot-error'), retry = U.$('#boot-retry');
      err.hidden = !b.error;
      err.textContent = b.error ? I.t('boot_failed', { m: b.error }) : '';
      retry.hidden = !b.error;
    }

    async _loadScenarioList() {
      try {
        const data = await this.transport.request('scenarios');
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
        const data = await this.transport.request('scenario', { name });
        this.note = null;
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

  EVR.live = { Live, DEFAULT_ALGOS, HttpTransport, WorkerTransport, cliCommand };
})(window.EVR = window.EVR || {});
