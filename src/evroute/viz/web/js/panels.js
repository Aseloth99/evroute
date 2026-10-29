/* EVRoute viewer -- panels.js
 *
 * DOM panels: the route list (visibility + selection), the trip summary,
 * the charging-stop cards, the sortable comparison table, the scenario facts
 * and the station tooltip.  Everything is built with EVR.util.el (text
 * nodes only), so names coming from data can never inject markup.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;
  const M = EVR.model;

  /**
   * A result its label budget stopped: an exact planner that ran into
   * max_labels (stats.extra.status "limit": no route, or a route whose
   * optimality is not proven) or a Pareto front that may be incomplete.
   */
  function budgetStopped(r) {
    const ex = (r && r.stats && r.stats.extra) || {};
    return ex.status === 'limit' || r.complete === false;
  }

  function guaranteeBadge(r) {
    const g = r.exact || 'heuristic';
    const wrap = U.el('span', { class: 'badges' });
    wrap.appendChild(U.el('span', { class: `badge badge-${g}`, title: I.t(`g_${g}_title`), text: I.t(`g_${g}`) }));
    if (r.complete === false) wrap.appendChild(U.el('span', { class: 'badge badge-partial', title: I.t('partial_front'), text: I.t('g_partial') }));
    else if (budgetStopped(r)) wrap.appendChild(U.el('span', { class: 'badge badge-partial', title: I.t('label_limit'), text: I.t('g_incomplete') }));
    return wrap;
  }

  /** Monte Carlo probability `k` of route `r` (null when not analysed). */
  function relP(r, k) {
    const rel = r.reliability;
    return rel && U.isNum(rel[k]) ? rel[k] : null;
  }

  function fmtP(v) {
    return I.pct(v, v > 0 && v < 0.095 ? 1 : 0);
  }

  /* -------------------------------------------------------- charging tariff */
  /** Price factor without trailing zeros: 1.3 -> "×1,3" (tr) / "×1.3" (en). */
  function fmtF(f) {
    return `×${I._nf({ minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(f)}`;
  }

  /** Price factors of a tariff dict (Tariff.to_dict()): {off: [below, above], peak: [below, above]}. */
  function tariffFactors(t) {
    const hi = t.peak_high_soc_factor === null || t.peak_high_soc_factor === undefined ? t.high_soc_factor : t.peak_high_soc_factor;
    return { off: [1, t.high_soc_factor], peak: [t.peak_factor, t.peak_factor * hi] };
  }

  /** Clock intervals of the peak period plus the busy-hour rule, e.g. "16:00–20:00". */
  function peakWhen(t) {
    const parts = (t.peak_hours || []).map((iv) => `${I.clock(iv[0] * 3600, false)}–${I.clock(iv[1] * 3600, false)}`);
    if (U.isNum(t.busy_utilisation)) parts.push(I.t('tariff_busy', { u: I.pct(t.busy_utilisation, 0) }));
    return parts.join(' + ');
  }

  /** One-line description of a tariff dict, e.g. "above 80 %: ×1.3 · peak 16:00–20:00: ×1.15 (above 80 %: ×2.3)". */
  function tariffText(t) {
    if (!t) return '';
    const f = tariffFactors(t);
    const p = I.pct(t.high_soc, 0);
    const parts = [];
    if (f.off[1] !== 1) parts.push(I.t('tariff_high', { p, f: fmtF(f.off[1]) }));
    const when = peakWhen(t);
    if (when) {
      let txt = I.t('tariff_peak', { h: when, f: fmtF(f.peak[0]) });
      if (f.peak[1] !== f.peak[0]) txt += ` (${I.t('tariff_high', { p, f: fmtF(f.peak[1]) })})`;
      parts.push(txt);
    }
    return parts.join(' · ') || I.t('tariff_flat');
  }

  /** Tariff pills of one stop: its price period and whether energy was bought above the high-SoC threshold. */
  function stopTariffPills(t, s) {
    const out = [];
    if ((t.peak_hours && t.peak_hours.length) || U.isNum(t.busy_utilisation)) {
      out.push(U.el('span', { class: `tariff-pill ${s.peak ? 'peak' : 'offpeak'}`, text: I.t(s.peak ? 'tariff_peak_short' : 'tariff_offpeak_short') }));
    }
    const f = tariffFactors(t)[s.peak ? 'peak' : 'off'];
    if (f[1] !== f[0] && s.soc_out > t.high_soc + 1e-9 && s.energy_out_kwh > s.energy_in_kwh) {
      out.push(U.el('span', { class: 'tariff-pill high', text: I.t('tariff_high_short', { p: I.pct(t.high_soc, 0) }), title: I.t('tariff_high', { p: I.pct(t.high_soc, 0), f: fmtF(f[1] / f[0]) }) }));
    }
    return out;
  }

  /** Columns of the comparison table: [key, label, value(r), format(v, r), better, title]. */
  function columns(app) {
    const cur = app.model.currency;
    const T = (r) => (r.feasible ? r.total_time_s : null);
    const nRel = (app.model.results.find((r) => r.reliability) || {}).reliability;
    const relTitle = (key) => (nRel ? I.t(key, { n: I.num(nRel.n, 0) }) : '');
    return [
      ['title', 'th_algorithm', (r) => r.title, null, null],
      ['exact', 'th_guarantee', (r) => ({ exact: 0, grid: 1, heuristic: 2 }[r.exact] ?? 3), null, null],
      ['feasible', 'th_feasible', (r) => (r.feasible ? 1 : 0), null, null],
      ['total', 'th_total', T, (v) => I.hms(v), 'min'],
      ['drive', 'th_drive', (r) => (r._ok ? r.drive_time_s : null), (v) => I.hms(v), 'min'],
      ['charge', 'th_charge', (r) => (r._ok ? r.charge_time_s : null), (v) => I.hms(v), 'min'],
      ['wait', 'th_wait', (r) => (r._ok ? r.wait_time_s + r.setup_time_s : null), (v) => I.hms(v), 'min'],
      ['hold', 'th_hold', (r) => (r._ok ? r.hold_time_s || 0 : null), (v) => I.hms(v), 'min', I.t('hold_title')],
      ['dist', 'th_distance', (r) => (r._ok ? r.distance_m : null), (v) => I.km(v, 0), 'min'],
      ['stops', 'th_stops', (r) => (r._ok ? (r.stops || []).length : null), (v) => I.num(v, 0), 'min'],
      ['energy', 'th_energy', (r) => (r._ok ? r.energy_consumed_kwh - r.energy_regen_kwh : null), (v) => `${I.num(v, 1)} kWh`, 'min'],
      ['money', 'th_money', (r) => (r._ok ? r.money : null), (v) => I.money(v, cur), 'min'],
      ['soh', 'th_soh', (r) => (r._ok ? r.soh_drive_pct + r.soh_charge_pct : null), (v) => I.soh(v), 'min'],
      ['pstrand', 'th_p_strand', (r) => relP(r, 'p_strand'), fmtP, 'min', relTitle('rel_strand_title')],
      ['pshort', 'th_p_short', (r) => relP(r, 'p_arrival_short'), fmtP, 'min', relTitle('rel_short_title')],
      ['runtime', 'th_runtime', (r) => (r.stats ? r.stats.runtime_s * 1000 : null), (v) => I.num(v, v < 10 ? 2 : v < 100 ? 1 : 0), 'min'],
      ['settled', 'th_settled', (r) => (r.stats ? r.stats.settled : null), (v) => I.num(v, 0), null],
    ];
  }

  class Panels {
    constructor(app) {
      this.app = app;
      this.sort = { key: null, dir: 1 };
      this.el = {
        list: U.$('#algo-list'), summary: U.$('#summary-card'), stops: U.$('#stops'), stopsCount: U.$('#stops-count'),
        table: U.$('#cmp-table'), kv: U.$('#scenario-kv'), disclaimer: U.$('#disclaimer'),
        legendSoc: U.$('#legend-soc'), legendTime: U.$('#legend-time'),
      };
    }

    renderAll() {
      this.renderList();
      this.renderSummary();
      this.renderStops();
      this.renderTable();
      this.renderScenario();
      this.renderLegends();
    }

    /* ----------------------------------------------------------- route list */
    renderList() {
      const app = this.app;
      const sel = app.selection;
      const items = app.model.results.map((r, ri) => {
        const c = app.colorOf(r);
        const vis = U.el('input', { type: 'checkbox', class: 'algo-vis', 'aria-label': `${r.title}` });
        vis.checked = app.visible.has(ri);
        vis.addEventListener('click', (ev) => ev.stopPropagation());
        vis.addEventListener('change', () => app.setVisible(ri, vis.checked));
        const meta = r._ok
          ? `${I.stops((r.stops || []).length)} · ${I.km(r.distance_m, 0)} · ${I.num(r.stats ? r.stats.runtime_s * 1000 : NaN, 0)} ms`
          : (r.message || I.t('no_route'));
        const li = U.el('li', {
          class: `algo-item${r.feasible ? '' : ' infeasible'}`, role: 'option', tabindex: '0',
          'aria-selected': String(sel.ri === ri), style: { '--algo': c },
          title: I.algoDescription({ name: r.algorithm, description: (app.algoInfo(r.algorithm) || {}).description }),
        },
        vis,
        U.el('div', { class: 'algo-text' },
          U.el('div', { class: 'algo-name' }, U.el('span', { text: r.title }), guaranteeBadge(r)),
          U.el('div', { class: 'algo-meta', text: r.feasible ? meta : budgetStopped(r) ? `⚠ ${I.t('label_limit')}` : `✕ ${r._strand ? I.t('stranded_at', { km: I.km(r._strand.dist, 0) }) : I.t('infeasible')}` })),
        U.el('div', { class: 'algo-time', text: r.feasible ? I.hm(r.total_time_s) : '—' }));
        li.addEventListener('click', () => app.select(ri, -1));
        li.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); app.select(ri, -1); }
          else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
            ev.preventDefault();
            const next = li[ev.key === 'ArrowDown' ? 'nextElementSibling' : 'previousElementSibling'];
            if (next) next.focus();
          }
        });
        return li;
      });
      U.setChildren(this.el.list, items);
    }

    /* -------------------------------------------------------------- summary */
    renderSummary() {
      const app = this.app, el = this.el.summary;
      const r = app.selectedRoute();
      if (!r) { U.setChildren(el, U.el('p', { class: 'muted', text: I.t('no_route') })); return; }
      const cur = app.model.currency;
      const best = app.bestFeasibleTime();
      const kids = [];
      kids.push(U.el('div', { class: 'summary-top', style: { '--algo': app.colorOf(r) } },
        U.el('div', { class: 'summary-algo' }, U.el('i', { class: 'swatch' }), U.el('span', { text: r.long_title || r.title })),
        guaranteeBadge(r)));
      const q = app.currentQuery();
      const od = `${app.model.nodeLabel(q.origin)} → ${app.model.nodeLabel(q.destination)}`;
      if (r.feasible) {
        kids.push(U.el('div', { class: 'summary-hero' },
          U.el('span', { class: 'big', text: I.duration(r.total_time_s) }),
          U.el('span', { class: 'arrive', text: `${I.t('arrival')} ${I.clock(app.model.departure + r.total_time_s)}` })));
      } else {
        kids.push(U.el('div', { class: 'summary-hero' }, U.el('span', { class: 'big', text: '—' }),
          budgetStopped(r) ? U.el('span', { class: 'badge badge-partial', text: I.t('g_incomplete') })
            : U.el('span', { class: 'badge badge-bad', text: I.t('infeasible') })));
      }
      kids.push(U.el('div', { class: 'summary-route', text: `${od} · ${I.t('departs')} ${I.clock(app.model.departure)}` }));
      if (r._ok) {
        const th = app.theme;
        const hold = r.hold_time_s || 0;   // voluntary holds for a cheaper tariff (charging tariffs only)
        const total = r.drive_time_s + hold + r.wait_time_s + r.setup_time_s + r.charge_time_s || 1;
        const parts = [['drive', r.drive_time_s, th.cDrive], ['queue', r.wait_time_s, th.cQueue], ['setup', r.setup_time_s, th.cSetup], ['charge', r.charge_time_s, th.cCharge]];
        if (hold > 0) parts.splice(1, 0, ['hold', hold, th.cHold]);
        kids.push(U.el('div', { class: 'summary-bar', role: 'img', 'aria-label': I.t('chart_time') },
          parts.map(([, v, c]) => U.el('i', { style: { width: `${(100 * v / total).toFixed(2)}%`, background: c } }))));
        kids.push(U.el('div', { class: 'summary-bar-legend' },
          parts.map(([k, v, c]) => U.el('span', {}, U.el('i', { style: { background: c } }), `${I.t(k)} ${I.duration(v)}`))));
        const metrics = [
          ['distance', I.km(r.distance_m, 0)],
          ['stops', I.num((r.stops || []).length, 0)],
          ['energy', `${I.num(r.energy_consumed_kwh - r.energy_regen_kwh, 1)} kWh`],
          ['charged', `${I.num(r.energy_charged_kwh, 1)} kWh`],
          ['money', I.money(r.money, cur)],
          ['arrival_soc', I.pct(r.arrival_soc, 0)],
          ['soh_short', I.soh(r.soh_drive_pct + r.soh_charge_pct)],
          ['min_soc', I.pct(r.min_soc, 0)],
          ['runtime', r.stats ? `${I.num(r.stats.runtime_s * 1000, r.stats.runtime_s < 0.01 ? 2 : 0)} ms` : '–'],
        ];
        const rel = r.reliability;
        if (rel) {
          const tip = I.t('rel_title', { n: I.num(rel.n, 0), s: fmtP(rel.p_strand), b: fmtP(rel.p_reserve_breach), a: fmtP(rel.p_arrival_short) });
          const level = (p, warn, bad) => (p >= bad ? 'bad' : p >= warn ? 'warn' : 'good');
          metrics.push(['p_strand_m', fmtP(rel.p_strand), level(rel.p_strand, 1e-9, 0.01), tip]);
          metrics.push(['p_short_m', fmtP(rel.p_arrival_short), level(rel.p_arrival_short, 0.05, 0.5), tip]);
          metrics.push(['settled', r.stats ? I.num(r.stats.settled, 0) : '–']);
        }
        kids.push(U.el('div', { class: 'metrics' }, metrics.map(([k, v, cls, tip]) =>
          U.el('div', { class: `metric${cls ? ' ' + cls : ''}`, title: tip || undefined },
            U.el('small', { text: I.t(k) }), U.el('b', { text: v, title: tip ? undefined : v })))));
      }
      if (!r.feasible) {
        const msg = r._strand ? I.t('stranded_at', { km: I.km(r._strand.dist, 0) }) : (r.message || I.t('no_route'));
        kids.push(U.el('div', { class: 'summary-warn', role: 'note', text: msg }));
      } else if (best !== null && r.total_time_s > best + 30) {
        kids.push(U.el('p', { class: 'summary-note', text: I.t('vs_best', { d: `+${I.duration(r.total_time_s - best)}` }) }));
      }
      if (app.selection.ai >= 0) {
        const parent = app.model.results[app.selection.ri];
        kids.push(U.el('p', { class: 'summary-note', text: I.t('alternative', { i: (r.member || 0) + 1, n: parent.alternatives_total || parent.alternatives.length }) }));
      }
      if (r.complete === false) kids.push(U.el('p', { class: 'summary-note', text: I.t('partial_front') }));
      U.setChildren(el, kids);
    }

    /* ---------------------------------------------------------- stop cards */
    renderStops() {
      const app = this.app;
      const r = app.selectedRoute();
      const stops = r && r._ok ? r._stops : [];
      this.el.stopsCount.textContent = stops.length ? I.stops(stops.length) : '';
      if (!stops.length) {
        U.setChildren(this.el.stops, U.el('li', { class: 'stops-empty', text: r && r.feasible ? I.t('no_stops') : I.t('no_route') }));
        return;
      }
      const cur = app.model.currency;
      const tariff = app.tariff();
      const items = stops.map((s, k) => {
        const st = app.model.stations[s.station] || {};
        const cls = st.cls || (s.station_kind === 'AC' ? 'AC' : 'DC');
        const inPct = U.clamp(s.soc_in, 0, 1) * 100, outPct = U.clamp(s.soc_out, 0, 1) * 100;
        const hold = s.hold_s > 0
          ? U.el('div', { class: 'stop-hold' }, U.el('i', { 'aria-hidden': 'true' }),
            U.el('span', { text: I.t(s.peak ? 'stop_hold' : 'stop_hold_offpeak', { m: I.minutes(s.hold_s), c: I.clock(app.model.departure + s.arrival_s + s.hold_s) }) }))
          : null;
        const li = U.el('li', { class: 'stop', tabindex: '0', style: { '--algo': app.colorOf(r) }, dataset: { k: String(k) } },
          U.el('div', { class: 'stop-num', text: String(s._num) }),
          U.el('div', { class: 'stop-body' },
            U.el('div', { class: 'stop-title' },
              U.el('b', { text: s.station_name || st.name || `#${s.station}` }),
              U.el('span', { text: I.clock(app.model.departure + s.arrival_s) })),
            U.el('div', { class: 'stop-kind' },
              U.el('span', { class: `kind-pill kind-${cls}`, text: cls }),
              U.el('span', { text: `${I.num(s.station_power_kw, 0)} kW${st.operator ? ' · ' + st.operator : ''} · ${I.pct(s.soc_in, 0)} → ${I.pct(s.soc_out, 0)}` }),
              tariff ? stopTariffPills(tariff, s) : null),
            hold,
            U.el('div', { class: 'soc-bar', role: 'img', 'aria-label': `${I.pct(s.soc_in, 0)} → ${I.pct(s.soc_out, 0)}` },
              U.el('i', { class: 'soc-in', style: { width: `${inPct}%` } }),
              U.el('i', { class: 'soc-add', style: { left: `${inPct}%`, width: `${Math.max(0, outPct - inPct)}%` } })),
            U.el('div', { class: 'stop-grid' },
              U.el('div', { class: 'q' }, U.el('small', { text: I.t('stop_queue') }), U.el('b', { text: I.minutes(s.wait_s) })),
              U.el('div', {}, U.el('small', { text: I.t('stop_charge') }), U.el('b', { text: I.minutes(s.charge_s) })),
              U.el('div', {}, U.el('small', { text: I.t('stop_energy') }), U.el('b', { text: `${I.num(s.energy_out_kwh - s.energy_in_kwh, 1)} kWh` })),
              U.el('div', {}, U.el('small', { text: I.t('stop_money') }), U.el('b', { text: I.money(s.money, cur, 0) })))));
        li.addEventListener('click', () => app.focusStop(k));
        li.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); app.focusStop(k); } });
        return li;
      });
      U.setChildren(this.el.stops, items);
    }

    highlightStop(k) {
      for (const li of this.el.stops.querySelectorAll('.stop')) li.classList.toggle('active', +li.dataset.k === k);
    }

    /* --------------------------------------------------------------- table */
    renderTable() {
      const app = this.app;
      const anyRel = app.model.results.some((r) => r.reliability);
      const anyHold = app.model.results.some((r) => r.hold_time_s > 0);
      const cols = columns(app).filter((c) => (anyRel || (c[0] !== 'pstrand' && c[0] !== 'pshort')) && (anyHold || c[0] !== 'hold'));
      const res = app.model.results.slice();
      // best values among feasible results
      const best = {};
      for (const [key, , val, , better] of cols) {
        if (!better) continue;
        let b = null, worst = null;
        for (const r of res) {
          if (!r.feasible) continue;
          const v = val(r);
          if (v === null || !isFinite(v)) continue;
          if (b === null || v < b) b = v;
          if (worst === null || v > worst) worst = v;
        }
        // highlight only when the column actually discriminates
        best[key] = b !== null && worst - b > Math.max(1e-6, Math.abs(b) * 1e-6) ? b : null;
      }
      if (this.sort.key) {
        const col = cols.find((c) => c[0] === this.sort.key);
        const dir = this.sort.dir;
        res.sort((a, b) => {
          const va = col[2](a), vb = col[2](b);
          if (va === null && vb === null) return 0;
          if (va === null) return 1;
          if (vb === null) return -1;
          return (typeof va === 'string' ? va.localeCompare(vb) : va - vb) * dir;
        });
      }
      const head = U.el('tr', {}, cols.map(([key, label, , , , title]) => {
        const th = U.el('th', { scope: 'col', class: key === 'exact' || key === 'feasible' ? 'center' : '', title: title || undefined },
          U.el('button', { type: 'button' }, I.t(label), U.el('span', { class: 'arrow', 'aria-hidden': 'true', text: this.sort.key === key && this.sort.dir < 0 ? '▼' : '▲' })));
        th.setAttribute('aria-sort', this.sort.key === key ? (this.sort.dir > 0 ? 'ascending' : 'descending') : 'none');
        th.querySelector('button').addEventListener('click', () => {
          if (this.sort.key === key) this.sort.dir = -this.sort.dir;
          else this.sort = { key, dir: 1 };
          this.renderTable();
        });
        return th;
      }));
      const sel = app.selection;
      const rows = res.map((r) => {
        const tr = U.el('tr', {
          class: r.feasible ? '' : 'infeasible', tabindex: '0', 'aria-selected': String(sel.ri === r._ri),
          style: { '--algo': app.colorOf(r) },
        });
        for (const [key, , val, fmt] of cols) {
          let td;
          if (key === 'title') {
            td = U.el('td', {}, U.el('span', { class: 'algo-cell' }, U.el('i'), r.title));
          } else if (key === 'exact') {
            td = U.el('td', { class: 'center' }, guaranteeBadge(r));
          } else if (key === 'feasible') {
            td = U.el('td', { class: 'center' }, U.el('span', { class: r.feasible ? 'yes' : 'no', text: r.feasible ? '✓' : '✕', 'aria-label': I.t(r.feasible ? 'feasible' : 'infeasible') }));
          } else {
            const v = val(r);
            const isBest = r.feasible && best[key] !== undefined && best[key] !== null && v !== null && Math.abs(v - best[key]) <= Math.max(1e-6, Math.abs(best[key]) * 1e-6);
            td = U.el('td', { class: isBest ? 'best' : '', text: v === null ? '–' : fmt(v, r) });
          }
          tr.appendChild(td);
        }
        tr.addEventListener('click', () => app.select(r._ri, -1));
        tr.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); app.select(r._ri, -1); } });
        return tr;
      });
      U.setChildren(this.el.table, U.el('thead', {}, head), U.el('tbody', {}, rows));
    }

    /* ------------------------------------------------------------ scenario */
    renderScenario() {
      const app = this.app;
      const sc = app.model.scenario || {};
      const v = sc.vehicle || {};
      const c = sc.conditions || {};
      const p = app.currentPrefs();
      const q = app.currentQuery();
      const net = app.model;
      const rows = [
        ['kv_vehicle', v.name || v.key || '–'],
        ['kv_battery', `${I.num(v.battery_kwh, 1)} kWh · ${I.t('kv_usable').toLowerCase()} ${I.num(v.capacity_kwh, 1)}`],
        ['kv_temperature', `${I.num(c.temperature_c, 0)} °C`],
        ['kv_departure', I.clock(q.departure_s)],
        ['kv_initial', I.pct(q.initial_soc, 0)],
        ['kv_reserve', `${I.pct(p.reserve_soc, 0)} / ${I.pct(p.min_arrival_soc, 0)}`],
        ['kv_objective', I.t(p.objective === 'cost' ? 'objective_cost' : 'objective_time')],
        ['kv_queues', I.t(p.queue_aware ? 'on' : 'off')],
        ['kv_speeds', (p.speed_factors || [1]).map((f) => I.pct(f, 0)).join(' / ')],
        ['kv_network', I.t('nodes_edges', { n: I.num(net.n, 0), m: I.num(net.m, 0) })],
        ['kv_stations', I.num(net.stations.length, 0)],
      ];
      const t = app.tariff();
      const n0 = rows.length;
      if (t) {
        // charging tariff (only for a scenario that has one): the high-SoC price and the peak period
        const f = tariffFactors(t);
        const p = I.pct(t.high_soc, 0);
        const when = peakWhen(t);
        if (f.off[1] !== 1 || f.peak[1] !== f.peak[0]) {
          const hi = when && f.peak[1] !== f.off[1] ? `${fmtF(f.off[1])} · ${I.t('tariff_peak_short')} ${fmtF(f.peak[1])}` : fmtF(f.off[1]);
          rows.push(['kv_tariff_high', hi, I.t('kv_tariff_high', { p })]);
        }
        if (when) rows.push(['kv_tariff_peak', `${when} · ${fmtF(f.peak[0])}`]);
        if (rows.length === n0) rows.push(['kv_tariff', I.t('tariff_flat')]);
      }
      U.setChildren(this.el.kv, rows.flatMap(([k, val, label]) => [U.el('dt', { text: label || I.t(k) }), U.el('dd', { text: val, title: val })]));
      const credit = sc.meta && sc.meta.attribution;
      const licence = sc.meta && sc.meta.licence;   // e.g. "ODbL 1.0 (data), MIT (code)" (OpenStreetMap data)
      const disc = [sc.meta && sc.meta.disclaimer, credit && I.t('data_credit', { c: credit }),
        licence && I.t('data_licence', { l: licence })].filter(Boolean).join(' ');
      this.el.disclaimer.hidden = !disc;
      this.el.disclaimer.textContent = disc || '';
    }

    renderLegends() {
      const app = this.app, th = app.theme;
      const routes = app.visibleRoutes();
      U.setChildren(this.el.legendSoc,
        routes.map((r) => U.el('span', { style: { '--algo': app.colorOf(r) } }, U.el('i'), r.title)),
        U.el('span', {}, U.el('i', { class: 'dash' }), I.t('reserve_line')),
        U.el('span', {}, U.el('i', { class: 'area' }), I.t('elev_profile')));
      const parts = [['drive', th.cDrive], ['queue', th.cQueue], ['setup', th.cSetup], ['charge', th.cCharge]];
      if (app.model.results.some((r) => r.hold_time_s > 0)) parts.splice(1, 0, ['hold', th.cHold]);
      U.setChildren(this.el.legendTime,
        parts.map(([k, c]) => U.el('span', {}, U.el('i', { class: 'sq', style: { background: c } }), I.t(k))));
    }

    /* -------------------------------------------------------- station tip */
    stationTip(node) {
      const app = this.app;
      const idxs = app.model.stationsAt.get(node) || [];
      const tod = app.clockTod();
      const cur = app.model.currency;
      const kids = [];
      const place = app.model.nodeName[node] || app.model.nodeName[String(node)];
      const sts = idxs.map((i) => app.model.stations[i]).sort((a, b) => b.power_kw - a.power_kw);
      const main = sts[0];
      kids.push(U.el('div', { class: 'tip-title', text: main.name || place || `#${main.id}` }));
      kids.push(U.el('div', { class: 'tip-sub' },
        U.el('span', { class: `kind-pill kind-${main.cls}`, text: main.cls }),
        U.el('span', { text: `${I.t('tip_operator')}: ${main.operator || '–'}` })));
      const grid = U.el('div', { class: 'tip-grid' });
      for (const st of sts) {
        if (sts.length > 1) {
          grid.append(U.el('span', { text: st.name || st.cls, style: { gridColumn: '1 / 3', fontWeight: '650', color: 'var(--text)', marginTop: '4px' } }));
        }
        grid.append(
          U.el('span', { text: I.t('tip_power') }), U.el('span', { text: `${st.kind} ${I.num(st.power_kw, 0)} kW` }),
          U.el('span', { text: I.t('tip_plugs') }), U.el('span', { text: I.num(st.plugs, 0) }),
          U.el('span', { text: I.t('tip_price') }), U.el('span', { text: `${I.money(st.price_per_kwh, cur)}/kWh` }),
          ...tipTariff(st, tod, cur),
          U.el('span', { text: I.t('tip_wait', { c: I.clock(tod, false) }) }), U.el('span', { text: I.minutes(M.waitAt(st.wait_profile, tod)) }));
      }
      kids.push(grid);
      const spark = U.el('canvas', { class: 'tip-spark', 'aria-hidden': 'true' });
      kids.push(spark);
      kids.push(U.el('div', { class: 'tip-spark-label' }, U.el('span', { text: '00' }), U.el('span', { text: I.t('tip_wait_24') }), U.el('span', { text: '24' })));
      return { kids, spark, profile: main.wait_profile, tod };
    }
  }

  /** Station tooltip rows of a charging tariff: the price below the high-SoC threshold now ([] without a tariff). */
  function tipTariff(st, tod, cur) {
    const f = st.price_factors;
    if (!f || !st.peak_s) return [];
    const peak = M.isPeakAt(st, tod);
    const below = (peak ? f.peak : f.offpeak)[0];
    return [
      U.el('span', { text: I.t('tip_tariff', { c: I.clock(tod, false) }) }),
      U.el('span', { text: `${I.money(st.price_per_kwh * below, cur)}/kWh · ${I.t(peak ? 'tariff_peak_short' : 'tariff_offpeak_short')}` }),
    ];
  }

  EVR.panels = { Panels, guaranteeBadge, budgetStopped, tariffText, tariffFactors };
})(window.EVR = window.EVR || {});
