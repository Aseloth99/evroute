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
   * A result a safeguard stopped: an exact planner that ran into max_labels
   * or time_limit_s (stats.extra.status "limit" / "time_limit": no route, or
   * a route whose optimality is not proven) or a Pareto front that may be
   * incomplete.
   */
  function budgetStopped(r) {
    const ex = (r && r.stats && r.stats.extra) || {};
    return ex.status === 'limit' || ex.status === 'time_limit' || r.complete === false;
  }

  /** "label budget reached" / "time limit reached" for a stopped result. */
  function stoppedText(r) {
    const ex = (r && r.stats && r.stats.extra) || {};
    return I.t(ex.status === 'time_limit' ? 'time_limit_hit' : 'label_limit');
  }

  /** "Not planned yet" in live mode before the first answer, else "no feasible route". */
  function noRouteText(app) {
    return app.live && !app.model.results.length ? I.t('not_planned') : I.t('no_route');
  }

  /**
   * A text of the scenario data (meta[key], in English) in the current
   * language: the translation of a preset's known text, else the text itself.
   */
  function metaText(sc, key) {
    const text = sc.meta && sc.meta[key];
    if (!text) return text;
    const k = `meta_${key}_${sc.meta.generator}`;
    return I.STR.en[k] === text ? I.t(k) : text;
  }

  function guaranteeBadge(r) {
    const g = r.exact || 'heuristic';
    const wrap = U.el('span', { class: 'badges' });
    wrap.appendChild(U.el('span', { class: `badge badge-${g}`, 'data-tip': I.t(`g_${g}_title`), text: I.t(`g_${g}`) }));
    if (r.complete === false) wrap.appendChild(U.el('span', { class: 'badge badge-partial', 'data-tip': I.t('partial_front'), text: I.t('g_partial') }));
    else if (budgetStopped(r)) wrap.appendChild(U.el('span', { class: 'badge badge-partial', 'data-tip': stoppedText(r), text: I.t('g_incomplete') }));
    return wrap;
  }

  /**
   * The plan's actual arrival charge with the minimum it had to reach:
   * "Arrival charge 14% (min. 10%)" -- the result, not the setting.
   */
  function arrivalSoc(app, r) {
    const min = app.currentPrefs().min_arrival_soc;
    return U.el('span', { class: 'arrive-soc', 'data-tip': I.t('tip_arrival_soc'), tabindex: '0' },
      `${I.t('arrival_soc')} `, U.el('b', { text: I.pct(r.arrival_soc, 0) }),
      U.isNum(min) ? U.el('small', { text: ` (${I.t('arrival_min', { p: I.pct(min, 0) })})` }) : null);
  }

  /* ------------------------------------------------------------ itinerary */
  /** Connector a stop uses: one the station has and the vehicle takes (a DC one at a DC station). */
  function stopConnector(app, s) {
    const st = app.model.stations[s.station] || {};
    const veh = ((app.model.scenario || {}).vehicle || {}).connectors;
    const socks = Array.isArray(st.connectors) ? st.connectors : [];
    const both = Array.isArray(veh) ? socks.filter((c) => veh.includes(c)) : socks;
    const dc = both.filter((c) => DC_CONNECTORS.has(c));
    const c = (s.station_kind === 'AC' ? both.find((x) => !DC_CONNECTORS.has(x)) : dc[0]) || both[0];
    return c ? (CONNECTOR_LABEL[c] || c) : '';
  }

  /** The most-driven named roads of a leg (edges i0 .. i1 - 1 of route r), longest first. */
  function legRoads(app, r, i0, i1) {
    const m = app.model, len = new Map();
    for (let i = i0; i < i1; i++) {
      const e = r.edges[i];
      const nm = m.edgeName[e] !== undefined ? m.edgeName[e] : m.edgeName[String(e)];
      if (nm) len.set(nm, (len.get(nm) || 0) + (m.length[e] || 0));
    }
    return Array.from(len.entries()).sort((a, b) => b[1] - a[1]).slice(0, 2).map((x) => x[0]);
  }

  /**
   * The trip plan of a feasible route: {totals, steps}; a step is
   * {kind: 'start' | 'drive' | 'stop' | 'end', ...} with clock times in
   * seconds after midnight.
   */
  function itinerary(app, r) {
    const m = app.model, dep = m.departure;
    const q = app.currentQuery(), prefs = app.currentPrefs();
    const L = r.nodes.length;
    const steps = [{ kind: 'start', place: m.nodeLabel(r.nodes[0] ?? q.origin), clock: dep, soc: r.node_energy_depart[0] / r._cap }];
    let pos = 0;
    let charging = 0;
    const leg = (to) => {
      if (to <= pos) return;
      const dist = (r.node_dist_m[to] || 0) - (r.node_dist_m[pos] || 0);
      steps.push({ kind: 'drive', dist, dur: r.node_arrive_s[to] - r.node_depart_s[pos], roads: legRoads(app, r, pos, to) });
      pos = to;
    };
    for (const s of r._stops) {
      leg(s.position);
      const st = m.stations[s.station] || {};
      const end = EVR.model.stopEnd(s);
      charging += end - s.arrival_s;
      steps.push({
        kind: 'stop', num: s._num, name: s.station_name || st.name || `#${s.station}`, operator: st.operator || '',
        acdc: s.station_kind || st.kind || '', power: s.station_power_kw, connector: stopConnector(app, s),
        arrive: dep + s.arrival_s, leave: dep + end, socIn: s.soc_in, socOut: s.soc_out,
        kwh: s.energy_out_kwh - s.energy_in_kwh, wait: s.wait_s + s.setup_s, charge: s.charge_s, hold: s.hold_s || 0,
        holdUntil: dep + s.arrival_s + (s.hold_s || 0), money: s.money, peak: !!s.peak, longQueue: s.wait_s >= 15 * 60,
        waitOnly: s.wait_s, stop: s,
      });
    }
    leg(L - 1);
    steps.push({ kind: 'end', place: m.nodeLabel(r.nodes[L - 1] ?? q.destination), clock: dep + r.total_time_s, soc: r.arrival_soc });
    return {
      steps,
      totals: {
        arrival: dep + r.total_time_s, total: r.total_time_s, drive: r.drive_time_s, charging, stops: r._stops.length,
        money: r.money, arrivalSoc: r.arrival_soc, minArrival: prefs.min_arrival_soc, dist: r.distance_m,
      },
    };
  }

  /** Text lines of a stop step (shared by the card and the plain-text copy). */
  function stopLines(app, st) {
    const cur = app.model.currency;
    const head = [st.name, st.operator].filter(Boolean).join(' · ');
    const kind = [`${st.acdc} ${I.num(st.power, 0)} kW`, st.connector].filter(Boolean).join(' · ');
    const times = `${I.clock(st.arrive)} ${I.pct(st.socIn, 0)} → ${I.clock(st.leave)} ${I.pct(st.socOut, 0)}`;
    const work = [st.wait > 0 ? I.t('it_wait', { m: I.minutes(st.wait) }) : null, I.t('it_charge_min', { m: I.minutes(st.charge) }),
      `${I.num(st.kwh, 1)} kWh`, I.money(st.money, cur, 0)].filter(Boolean).join(' · ');
    const notes = [];
    if (st.hold > 0) notes.push(I.t(st.peak ? 'stop_hold' : 'stop_hold_offpeak', { m: I.minutes(st.hold), c: I.clock(st.holdUntil, false) }));
    if (st.peak) notes.push(I.t('warn_peak'));
    if (st.longQueue) notes.push(I.t('warn_queue', { m: I.minutes(st.waitOnly) }));
    return { head, kind, times, work, notes };
  }

  function driveText(st) {
    return [`${I.km(st.dist, 0)} · ${I.duration(st.dur)}`, st.roads.length ? I.t('it_via', { r: st.roads.join(', ') }) : null].filter(Boolean).join(' · ');
  }

  function itinStep(app, st) {
    if (st.kind === 'start' || st.kind === 'end') {
      const which = st.kind === 'start' ? 'A' : 'B';
      return U.el('li', { class: `itin-step itin-${st.kind}` },
        U.el('span', { class: 'itin-mark' }, U.odDot(which)),
        U.el('div', { class: 'itin-body' },
          U.el('div', { class: 'itin-title' }, U.el('b', { text: st.place }), U.el('span', { class: 'itin-clock', text: I.clock(st.clock) })),
          U.el('div', { class: 'itin-sub', text: `${I.t(st.kind === 'start' ? 'initial_soc' : 'arrival_soc')} ${I.pct(st.soc, 0)}` })));
    }
    if (st.kind === 'drive') {
      return U.el('li', { class: 'itin-step itin-drive' }, U.el('span', { class: 'itin-mark' }, U.el('i')),
        U.el('div', { class: 'itin-body itin-sub', text: `${I.t('it_drive')} · ${driveText(st)}` }));
    }
    const t = stopLines(app, st);
    return U.el('li', { class: 'itin-step itin-stop' },
      U.el('span', { class: 'itin-mark' }, U.el('span', { class: 'itin-num', text: String(st.num) })),
      U.el('div', { class: 'itin-body' },
        U.el('div', { class: 'itin-title' }, U.el('b', { text: t.head }), U.el('span', { class: 'itin-clock', text: I.clock(st.arrive) })),
        U.el('div', { class: 'itin-sub' }, U.el('span', { class: `kind-pill kind-${st.acdc === 'AC' ? 'AC' : st.power >= 250 ? 'HPC' : 'DC'}`, text: st.acdc }), ` ${t.kind}`),
        U.el('div', { class: 'itin-soc' }, `${I.t('it_charge')}: ${t.times}`),
        U.el('div', { class: 'itin-sub', text: t.work }),
        t.notes.length ? U.el('div', { class: 'itin-notes' }, t.notes.map((n) => U.el('span', { class: 'itin-warn', text: n }))) : null));
  }

  /** The trip plan as plain text (Copy). */
  function itineraryText(app, r, it) {
    const t = it.totals, cur = app.model.currency;
    const veh = ((app.model.scenario || {}).vehicle || {}).name;
    const start = it.steps[0], end = it.steps[it.steps.length - 1];
    const out = [`EVRoute · ${I.t('trip_plan')}: ${start.place} → ${end.place}`];
    if (veh) out.push(`${I.t('it_vehicle')}: ${veh}`);
    out.push('');
    for (const st of it.steps) {
      if (st.kind === 'start') out.push(`A  ${I.t('it_depart')} ${st.place} ${I.clock(st.clock)} · ${I.pct(st.soc, 0)}`);
      else if (st.kind === 'end') out.push(`B  ${I.t('it_dest')} ${st.place} ${I.clock(st.clock)} · ${I.t('arrival_soc')} ${I.pct(st.soc, 0)}`);
      else if (st.kind === 'drive') out.push(`   ${I.t('it_drive')}: ${driveText(st)}`);
      else {
        const l = stopLines(app, st);
        out.push(`${String(st.num).padEnd(2)} ${l.head} · ${l.kind}`);
        out.push(`   ${I.t('it_charge')}: ${l.times}`);
        out.push(`   ${l.work}`);
        for (const n of l.notes) out.push(`   ! ${n}`);
      }
    }
    out.push('');
    out.push(`${I.t('total_time')} ${I.duration(t.total)} · ${I.t('it_drive')} ${I.duration(t.drive)} · ${I.t('it_charging')} ${I.duration(t.charging)} · ${I.km(t.dist, 0)}`);
    out.push(`${I.t('it_cost')} ${I.money(t.money, cur)} · ${I.t('arrival_soc')} ${I.pct(t.arrivalSoc, 0)} (${I.t('arrival_min', { p: I.pct(t.minArrival, 0) })})`);
    out.push(I.t('it_note'));
    return out.join('\n');
  }

  /**
   * Google Maps directions with the stops as waypoints -- only for a
   * scenario with real coordinates (network lat/lon, the Turkey presets).
   * The app only builds the link; nothing is sent unless the user follows it.
   */
  function mapsUrl(app, r) {
    const lat = app.model.lat, lon = app.model.lon;
    if (!lat || !lon || !r._ok) return null;
    const ll = (v) => (U.isNum(lat[v]) && U.isNum(lon[v]) ? `${lat[v].toFixed(5)},${lon[v].toFixed(5)}` : null);
    const o = ll(r.nodes[0]), d = ll(r.nodes[r.nodes.length - 1]);
    if (!o || !d) return null;
    const way = r._stops.map((s) => ll(s.node)).filter(Boolean).slice(0, 9);
    let url = `${GMAPS_DIR}&origin=${encodeURIComponent(o)}&destination=${encodeURIComponent(d)}&travelmode=driving`;
    if (way.length) url += `&waypoints=${encodeURIComponent(way.join('|'))}`;
    return url;
  }

  /** Print the trip plan only (print stylesheet: body.print-itinerary). */
  function printItinerary() {
    document.body.classList.add('print-itinerary');
    const done = () => { document.body.classList.remove('print-itinerary'); window.removeEventListener('afterprint', done); };
    window.addEventListener('afterprint', done);
    try { window.print(); } catch (e) { /* no printing */ }
    setTimeout(done, 1000);
  }

  /**
   * A collapsible section (<details>) whose open state survives re-renders
   * (panels.open, by `key`); `tip` explains the section in its title.
   */
  function details(panels, key, title, tip, ...body) {
    const d = U.el('details', { class: 'sec' }, U.el('summary', {}, U.el('span', { text: title, 'data-tip': tip || null })), ...body);
    if (panels.open.has(key)) d.open = true;
    d.addEventListener('toggle', () => { if (d.open) panels.open.add(key); else panels.open.delete(key); });
    return d;
  }

  /** Tooltip of a summary metric `k` (tip_m_<k>, or the arrival-charge explanation). */
  function metricTip(k) {
    if (k === 'arrival_soc') return I.t('tip_arrival_soc');
    return I.STR.en[`tip_m_${k}`] ? I.t(`tip_m_${k}`) : null;
  }

  /** Tooltip of a comparison-table header `th_<x>` (tip_th_<x>). */
  function headTip(label) {
    return I.STR.en[`tip_${label}`] ? I.t(`tip_${label}`) : null;
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
      out.push(U.el('span', { class: `tariff-pill ${s.peak ? 'peak' : 'offpeak'}`, text: I.t(s.peak ? 'tariff_peak_short' : 'tariff_offpeak_short'), 'data-tip': I.t(s.peak ? 'tip_tariff_peak' : 'tip_tariff_offpeak') }));
    }
    const f = tariffFactors(t)[s.peak ? 'peak' : 'off'];
    if (f[1] !== f[0] && s.soc_out > t.high_soc + 1e-9 && s.energy_out_kwh > s.energy_in_kwh) {
      out.push(U.el('span', { class: 'tariff-pill high', text: I.t('tariff_high_short', { p: I.pct(t.high_soc, 0) }), 'data-tip': I.t('tariff_high', { p: I.pct(t.high_soc, 0), f: fmtF(f[1] / f[0]) }) }));
    }
    return out;
  }

  /* ------------------------------------------------- vehicles and connectors */
  /** Google Maps directions ("Maps URLs", api=1): a link the user may follow. */
  const GMAPS_DIR = 'https://www.google.com/maps/dir/?api=1';

  /** Display names of the connector standards (Station.sockets / Vehicle.connectors). */
  const CONNECTOR_LABEL = { Type2: 'Type 2', Type1: 'Type 1', CCS2: 'CCS2', CCS1: 'CCS1', CHAdeMO: 'CHAdeMO', NACS: 'NACS' };
  /** Connectors that carry DC (fast charging). */
  const DC_CONNECTORS = new Set(['CCS2', 'CCS1', 'CHAdeMO', 'NACS']);

  /** Small chips of connector standards, each with its explanation as a tooltip. */
  function connectorChips(list, focusable) {
    return U.el('span', { class: 'conn-chips' }, (list || []).map((c) => U.el('span', {
      class: `conn${DC_CONNECTORS.has(c) ? ' conn-dc' : ''}`, text: CONNECTOR_LABEL[c] || c,
      'data-tip': I.STR.en[`tip_conn_${c}`] ? I.t(`tip_conn_${c}`) : null, tabindex: focusable ? '0' : null,
    })));
  }

  /** Chemistry badge (LFP / NMC / NCA) with its explanation, or null without a chemistry. */
  function chemBadge(chem, focusable) {
    if (!chem) return null;
    return U.el('span', { class: `chem-badge chem-${chem}`, text: chem, 'data-tip': I.STR.en[`tip_chem_${chem}`] ? I.t(`tip_chem_${chem}`) : null, tabindex: focusable ? '0' : null });
  }

  /**
   * The vehicle details card: chemistry, battery, highest DC power (with a
   * user limit `opts.limit`), on-board AC power and connectors.  `v` is a
   * payload vehicle (vehicles[] or scenario.vehicle; any field may be
   * missing in an older payload).
   */
  function vehicleCard(app, v, opts) {
    const o = opts || {};
    const peak = U.isNum(v.dc_peak_kw) ? v.dc_peak_kw : null;
    const lim = U.isNum(o.limit) && o.limit > 0 ? o.limit : U.isNum(v.dc_max_kw) ? v.dc_max_kw : null;
    let dc = I.t('veh_no_dc');
    let dcTip = I.t('tip_veh_dc');
    if (peak !== null && peak > 0) {
      const capped = lim !== null && lim < peak;
      dc = `${I.num(capped ? lim : peak, 0)} kW`;
      if (capped) dcTip += ` · ${I.t('curve_limit', { kw: dc })} (${I.t('dc_limit_ph', { kw: `${I.num(peak, 0)} kW` })})`;
    } else if (peak === null) dc = '–';
    const usable = U.isNum(v.capacity_kwh) ? `${I.num(v.capacity_kwh, 1)} kWh` : '–';
    const stat = (k, val, tip) => U.el('div', { class: 'veh-stat', 'data-tip': tip, tabindex: o.focusable ? '0' : null },
      U.el('small', { text: I.t(k) }), U.el('b', { text: val }));
    const conns = Array.isArray(v.connectors) && v.connectors.length ? connectorChips(v.connectors, true) : null;
    const kids = [];
    // chemistry and connectors in one line, the name when asked for (the picker shows it already)
    kids.push(U.el('div', { class: 'veh-head' }, chemBadge(v.chemistry, true),
      o.title ? U.el('b', { class: 'veh-name', text: v.name || v.key || '' }) : null,
      conns ? U.el('span', { class: 'veh-conn', 'aria-label': I.t('veh_connectors') }, conns) : null));
    kids.push(U.el('div', { class: 'veh-stats' },
      stat('veh_battery', U.isNum(v.battery_kwh) ? `${I.num(v.battery_kwh, v.battery_kwh % 1 ? 1 : 0)} kWh` : '–', I.t('tip_veh_battery', { u: usable })),
      stat('veh_dc', dc, dcTip),
      stat('veh_ac', U.isNum(v.ac_max_kw) ? `${I.num(v.ac_max_kw, v.ac_max_kw % 1 ? 1 : 0)} kW` : '–', I.t('tip_veh_ac'))));
    return kids;
  }

  /** Tooltip of an algorithm: its plain-language description and its guarantee. */
  function algoTip(info) {
    const g = info.exact || 'heuristic';
    const desc = I.algoDescription(info);
    return [desc, `${I.t(`g_${g}`)}: ${I.t(`g_${g}_title`)}`].filter(Boolean).join(' · ');
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
      this.open = new Set();   // keys of the detail sections the user opened
      this.el = {
        list: U.$('#algo-list'), summary: U.$('#summary-card'), stops: U.$('#stops'), stopsCount: U.$('#stops-count'),
        table: U.$('#cmp-table'), kv: U.$('#scenario-kv'), disclaimer: U.$('#disclaimer'),
        legendSoc: U.$('#legend-soc'), legendTime: U.$('#legend-time'),
        itinerary: U.$('#itinerary-card'), tripCard: U.$('#trip-card'), tripFacts: U.$('#trip-facts'),
      };
    }

    renderAll() {
      this.renderList();
      this.renderSummary();
      this.renderStops();
      this.renderTable();
      this.renderScenario();
      this.renderLegends();
      this.renderItinerary();
      this.renderTripFacts();
    }

    /* ------------------------------------------------- everyday: trip plan */
    /**
     * The itinerary of the selected route ("Trip plan"): totals, the steps
     * from A to B (drive legs and stops) and Copy / Print / Google Maps.
     * Only what a driver needs: times, distances, charge, kWh, money,
     * stations and warnings -- no wear, generalised cost or planner names.
     */
    renderItinerary() {
      const app = this.app, box = this.el.itinerary;
      if (!box) return;
      const r = app.selectedRoute();
      const head = U.el('div', { class: 'card-head' }, U.el('h2', { text: I.t('trip_plan') }));
      if (!r || !r.feasible || !r._ok) {
        const msg = !r ? noRouteText(app) : r._strand ? I.t('stranded_at', { km: I.km(r._strand.dist, 0) }) : (r.message || I.t('no_route'));
        U.setChildren(box, head, U.el('p', { class: r ? 'summary-warn' : 'muted', text: msg }));
        return;
      }
      const it = itinerary(app, r);
      const actions = U.el('div', { class: 'itin-actions' });
      const copy = U.el('button', { type: 'button', class: 'btn btn-sm', 'data-tip': I.t('tip_copy') },
        U.svg('M9 9h10v12H9zM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1'), U.el('span', { text: I.t('copy') }));
      copy.addEventListener('click', () => {
        const text = itineraryText(app, r, it);
        const done = () => { U.$('span', copy).textContent = I.t('copied'); setTimeout(() => { U.$('span', copy).textContent = I.t('copy'); }, 1600); };
        try { navigator.clipboard.writeText(text).then(done, () => app.toast(I.t('err_generic', { m: 'clipboard' }))); } catch (e) { app.toast(I.t('err_generic', { m: 'clipboard' })); }
      });
      const print = U.el('button', { type: 'button', class: 'btn btn-sm', 'data-tip': I.t('tip_print') },
        U.svg('M6 9V3h12v6M6 18H4a1 1 0 0 1-1-1v-6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v6a1 1 0 0 1-1 1h-2M6 14h12v7H6z'), U.el('span', { text: I.t('btn_print') }));
      print.addEventListener('click', () => printItinerary());
      actions.append(copy, print);
      const maps = mapsUrl(app, r);
      if (maps) {
        // a link the driver follows: the app itself sends nothing anywhere
        actions.append(U.el('a', { class: 'btn btn-sm', href: maps, target: '_blank', rel: 'noopener noreferrer', 'data-tip': I.t('tip_gmaps') },
          U.svg('M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z'), U.el('span', { text: I.t('btn_gmaps') })));
      }
      head.append(actions);
      const t = it.totals;
      const tile = (k, v, tip, cls) => U.el('div', { class: `itin-total${cls ? ' ' + cls : ''}`, 'data-tip': tip || null, tabindex: tip ? '0' : null },
        U.el('small', { text: I.t(k) }), U.el('b', {}, v));
      const totals = U.el('div', { class: 'itin-totals' },
        tile('it_arrival', I.clock(t.arrival)),
        tile('total_time', I.duration(t.total)),
        tile('it_drive', I.duration(t.drive)),
        tile('it_charging', I.duration(t.charging), I.t('tip_it_charging')),
        tile('stops', I.num(t.stops, 0)),
        tile('it_cost', I.money(t.money, app.model.currency)),
        tile('arrival_soc', [I.pct(t.arrivalSoc, 0), U.isNum(t.minArrival) ? U.el('small', { text: ` (${I.t('arrival_min', { p: I.pct(t.minArrival, 0) })})` }) : null],
          I.t('tip_arrival_soc'), 'good'));
      const steps = U.el('ol', { class: 'itin-steps' }, it.steps.map((st) => itinStep(app, st)));
      U.setChildren(box, head, totals, steps, U.el('p', { class: 'itin-note', text: I.t('it_note') }));
    }

    /** Static pages in everyday mode: the trip's assumptions (vehicle, departure, charge). */
    renderTripFacts() {
      const app = this.app, card = this.el.tripCard;
      if (!card) return;
      card.hidden = app.live;
      if (app.live) return;
      const sc = app.model.scenario || {};
      const v = sc.vehicle || {};
      const q = app.currentQuery(), p = app.currentPrefs();
      const kids = [U.el('div', { class: 'trip-veh' }, U.el('b', { text: v.name || v.key || '–' }))];
      kids.push(U.el('div', { class: 'veh-card' }, vehicleCard(app, v)));
      const rows = [['departure', I.clock(q.departure_s || 0)], ['initial_soc', I.pct(q.initial_soc, 0)],
        ['min_arrival', I.pct(p.min_arrival_soc, 0)]];
      if (p.min_stop_soc > 0) rows.push(['kv_min_stop', I.pct(p.min_stop_soc, 0)]);
      kids.push(U.el('dl', { class: 'kv' }, rows.flatMap(([k, val]) => [U.el('dt', { text: I.t(k) }), U.el('dd', { text: val })])));
      U.setChildren(this.el.tripFacts, kids);
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
          'data-tip': algoTip(Object.assign({ name: r.algorithm, exact: r.exact }, app.algoInfo(r.algorithm) || {})),
        },
        vis,
        U.el('div', { class: 'algo-text' },
          U.el('div', { class: 'algo-name' }, U.el('span', { text: r.title }), guaranteeBadge(r)),
          U.el('div', { class: 'algo-meta', text: r.feasible ? meta : budgetStopped(r) ? `⚠ ${stoppedText(r)}` : `✕ ${r._strand ? I.t('stranded_at', { km: I.km(r._strand.dist, 0) }) : I.t('infeasible')}` })),
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
      if (!r) { U.setChildren(el, U.el('p', { class: 'muted', text: noRouteText(app) })); return; }
      const cur = app.model.currency;
      const best = app.bestFeasibleTime();
      const kids = [];
      kids.push(U.el('div', { class: 'summary-top', style: { '--algo': app.colorOf(r) } },
        U.el('div', { class: 'summary-algo', 'data-tip': algoTip(Object.assign({ name: r.algorithm, exact: r.exact }, app.algoInfo(r.algorithm) || {})) },
          U.el('i', { class: 'swatch' }), U.el('span', { text: r.long_title || r.title })),
        guaranteeBadge(r)));
      const q = app.currentQuery();
      const od = `${app.model.nodeLabel(q.origin)} → ${app.model.nodeLabel(q.destination)}`;
      if (r.feasible) {
        kids.push(U.el('div', { class: 'summary-hero' },
          U.el('span', { class: 'big', text: I.duration(r.total_time_s) }),
          U.el('span', { class: 'arrive', text: `${I.t('arrival')} ${I.clock(app.model.departure + r.total_time_s)}` }),
          arrivalSoc(app, r)));
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
          U.el('div', { class: `metric${cls ? ' ' + cls : ''}`, 'data-tip': tip || metricTip(k), tabindex: '0' },
            U.el('small', { text: I.t(k) }), U.el('b', { text: v })))));
        kids.push(...this._routeDetails(r));
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

    /**
     * Research detail of a route (collapsed sections): energy breakdown,
     * battery wear, the generalised cost and the search statistics.
     */
    _routeDetails(r) {
      const app = this.app, cur = app.model.currency;
      const veh = (app.model.scenario || {}).vehicle || {};
      const p = app.currentPrefs();
      const key = `${r._ri}:${r._ai}`;
      const kwh = (x) => `${I.num(x, 1)} kWh`;
      const dl = (rows) => U.el('dl', { class: 'kv kv-detail' }, rows.filter(Boolean).flatMap(([k, v, tip]) => [
        U.el('dt', { text: k, 'data-tip': tip || null, class: tip ? 'tip-term' : null }), U.el('dd', { text: v })]));
      const out = [];
      // energy: driving consumption, recovery, auxiliaries, charging and its losses
      const L = (r.node_energy_arrive || []).length;
      const net = r.energy_consumed_kwh - r.energy_regen_kwh;
      const aux = U.isNum(veh.aux_kw) ? veh.aux_kw * r.drive_time_s / 3600 : null;
      out.push(details(this, `energy:${key}`, I.t('sec_energy'), I.t('tip_sec_energy'), dl([
        [I.t('e_consumed'), kwh(r.energy_consumed_kwh)],
        [I.t('e_regen'), `−${kwh(r.energy_regen_kwh)}`],
        [I.t('e_net'), kwh(net), I.t('tip_m_energy')],
        aux !== null ? [I.t('e_aux'), `≈ ${kwh(aux)}`, I.t('tip_e_aux', { kw: `${I.num(veh.aux_kw, 2)} kW` })] : null,
        [I.t('e_charged'), kwh(r.energy_charged_kwh), I.t('tip_m_charged')],
        U.isNum(r.grid_energy_kwh) ? [I.t('e_grid'), kwh(r.grid_energy_kwh)] : null,
        U.isNum(r.grid_energy_kwh) ? [I.t('e_loss'), kwh(r.grid_energy_kwh - r.energy_charged_kwh), I.t('tip_e_loss')] : null,
        L ? [I.t('e_start_end'), `${kwh(r.node_energy_depart[0])} → ${kwh(r.node_energy_arrive[L - 1])}`] : null,
      ])));
      // battery wear and its money value in the model
      out.push(details(this, `wear:${key}`, I.t('sec_wear'), I.t('tip_sec_wear'), dl([
        [I.t('w_drive'), I.soh(r.soh_drive_pct)],
        [I.t('w_charge'), I.soh(r.soh_charge_pct)],
        [I.t('w_total'), I.soh(r.soh_drive_pct + r.soh_charge_pct), I.t('tip_m_soh_short')],
        U.isNum(r.degradation_money) ? [I.t('w_money'), I.money(r.degradation_money, cur), I.t('tip_d_wear', { c: U.isNum(p.soh_cost_per_pct) ? I.money(p.soh_cost_per_pct, cur, 0) : '–' })] : null,
      ])));
      // generalised cost: what the cost objective minimises
      if (U.isNum(r.generalized_cost) && U.isNum(p.value_of_time_per_h)) {
        const wm = U.isNum(p.weight_money) ? p.weight_money : 1, wd = U.isNum(p.weight_degradation) ? p.weight_degradation : 1;
        const fmtW = (w) => I.num(w, w % 1 ? 2 : 0);
        out.push(details(this, `cost:${key}`, I.t('sec_cost'), I.t('tip_sec_cost'), dl([
          [I.t('c_time', { v: I.money(p.value_of_time_per_h, cur, 0) }), I.money(p.value_of_time_per_h * r.total_time_s / 3600, cur), I.t('tip_vot')],
          [I.t('c_money', { w: fmtW(wm) }), I.money(wm * r.money, cur)],
          U.isNum(r.degradation_money) ? [I.t('c_wear', { w: fmtW(wd) }), I.money(wd * r.degradation_money, cur)] : null,
          [I.t('c_total'), I.money(r.generalized_cost, cur)],
        ])));
      }
      // the planner's work
      const st = r.stats;
      if (st) {
        out.push(details(this, `search:${key}`, I.t('sec_search'), I.t('tip_sec_search'), dl([
          [I.t('s_settled'), I.num(st.settled, 0), I.t('tip_m_settled')],
          [I.t('s_generated'), I.num(st.generated, 0)],
          [I.t('s_pushes'), I.num(st.pushes, 0)],
          [I.t('s_pruned'), I.num(st.pruned, 0)],
          [I.t('s_peak_queue'), I.num(st.peak_queue, 0)],
          [I.t('runtime'), `${I.num(st.runtime_s * 1000, st.runtime_s < 0.01 ? 2 : 0)} ms`, I.t('tip_m_runtime')],
        ])));
      }
      return out;
    }

    /* ---------------------------------------------------------- stop cards */
    renderStops() {
      const app = this.app;
      const r = app.selectedRoute();
      const stops = r && r._ok ? r._stops : [];
      this.el.stopsCount.textContent = stops.length ? I.stops(stops.length) : '';
      if (!stops.length) {
        U.setChildren(this.el.stops, U.el('li', { class: 'stops-empty', text: r && r.feasible ? I.t('no_stops') : r ? I.t('no_route') : noRouteText(app) }));
        return;
      }
      const cur = app.model.currency;
      const tariff = app.tariff();
      const items = stops.map((s, k) => {
        const st = app.model.stations[s.station] || {};
        const cls = st.cls || (s.station_kind === 'AC' ? 'AC' : 'DC');
        const inPct = U.clamp(s.soc_in, 0, 1) * 100, outPct = U.clamp(s.soc_out, 0, 1) * 100;
        const hold = s.hold_s > 0
          ? U.el('div', { class: 'stop-hold', 'data-tip': I.t('hold_title') }, U.el('i', { 'aria-hidden': 'true' }),
            U.el('span', { text: I.t(s.peak ? 'stop_hold' : 'stop_hold_offpeak', { m: I.minutes(s.hold_s), c: I.clock(app.model.departure + s.arrival_s + s.hold_s) }) }))
          : null;
        const cell = (label, value, tip, cls2) => U.el('div', { class: cls2 || null, 'data-tip': tip },
          U.el('small', { text: label }), U.el('b', { text: value }));
        const queueTip = `${I.t('tip_stop_queue')} · ${I.t('setup')} ${I.minutes(s.setup_s)} (${I.t('tip_stop_setup').toLowerCase()})`;
        const li = U.el('li', { class: 'stop', tabindex: '0', style: { '--algo': app.colorOf(r) }, dataset: { k: String(k) } },
          U.el('div', { class: 'stop-num', text: String(s._num) }),
          U.el('div', { class: 'stop-body' },
            U.el('div', { class: 'stop-title' },
              U.el('b', { text: s.station_name || st.name || `#${s.station}` }),
              U.el('span', { text: I.clock(app.model.departure + s.arrival_s) })),
            U.el('div', { class: 'stop-kind' },
              U.el('span', { class: `kind-pill kind-${cls}`, text: cls, 'data-tip': I.t(`tip_leg_${cls.toLowerCase()}`) }),
              U.el('span', { text: `${I.num(s.station_power_kw, 0)} kW${st.operator ? ' · ' + st.operator : ''} · ${I.pct(s.soc_in, 0)} → ${I.pct(s.soc_out, 0)}` }),
              tariff ? stopTariffPills(tariff, s) : null),
            hold,
            U.el('div', { class: 'soc-bar', role: 'img', 'aria-label': `${I.pct(s.soc_in, 0)} → ${I.pct(s.soc_out, 0)}` },
              U.el('i', { class: 'soc-in', style: { width: `${inPct}%` } }),
              U.el('i', { class: 'soc-add', style: { left: `${inPct}%`, width: `${Math.max(0, outPct - inPct)}%` } })),
            U.el('div', { class: 'stop-grid' },
              cell(I.t('stop_queue'), I.minutes(s.wait_s), queueTip, 'q'),
              cell(I.t('stop_charge'), I.minutes(s.charge_s), I.t('tip_stop_charge')),
              cell(I.t('stop_energy'), `${I.num(s.energy_out_kwh - s.energy_in_kwh, 1)} kWh`, I.t('tip_stop_energy')),
              cell(I.t('stop_money'), I.money(s.money, cur, 0), I.t('tip_stop_money'))),
            this._stopDetails(r, s, st, tariff)));
        li.addEventListener('click', (ev) => { if (!ev.target.closest('details')) app.focusStop(k); });
        li.addEventListener('keydown', (ev) => {
          if ((ev.key === 'Enter' || ev.key === ' ') && ev.target === li) { ev.preventDefault(); app.focusStop(k); }
        });
        return li;
      });
      U.setChildren(this.el.stops, items);
    }

    /**
     * The research detail of one stop (collapsed): charge and energy in and
     * out, battery vs grid energy, average and peak power against the
     * station's and the vehicle's limit, C-rate, the time split, price
     * period and high-SoC share, money and battery wear.
     */
    _stopDetails(r, s, st, tariff) {
      const app = this.app, cur = app.model.currency;
      const veh = (app.model.scenario || {}).vehicle || {};
      const prefs = app.currentPrefs();
      const kwh = s.energy_out_kwh - s.energy_in_kwh;
      const avg = s.charge_s > 0 ? kwh / s.charge_s * 3600 : 0;
      let peak = 0;
      const c = s.curve;
      if (c && c.t && c.t.length > 1) {
        for (let j = 0; j + 1 < c.t.length; j++) {
          const dt = c.t[j + 1] - c.t[j];
          if (dt > 0.5) peak = Math.max(peak, (c.e[j + 1] - c.e[j]) / dt * 3600);
        }
      }
      peak = Math.max(peak, avg);
      const ac = s.station_kind === 'AC';
      const vehLim = ac ? veh.ac_max_kw : veh.dc_peak_kw;
      const cap = veh.battery_kwh || r._cap;
      const rows = [];
      rows.push(['d_soc', `${I.pct(s.soc_in, 0)} → ${I.pct(s.soc_out, 0)} · ${I.num(s.energy_in_kwh, 1)} → ${I.num(s.energy_out_kwh, 1)} kWh`, I.t('tip_d_soc')]);
      if (U.isNum(s.grid_energy_kwh)) {
        rows.push(['d_energy', `${I.num(kwh, 1)} / ${I.num(s.grid_energy_kwh, 1)} kWh · ${I.t('d_loss', { k: `${I.num(s.grid_energy_kwh - kwh, 1)} kWh` })}`, I.t('tip_d_energy')]);
      }
      if (kwh > 0) {
        rows.push(['d_power', `${I.num(avg, 0)} / ${I.num(peak, 0)} kW`, I.t('tip_d_power')]);
        rows.push(['d_limits', `${I.num(s.station_power_kw, 0)} / ${U.isNum(vehLim) ? I.num(vehLim, 0) : '–'} kW`, I.t('tip_d_limits')]);
        if (cap > 0) rows.push(['d_crate', `${I.num(avg / cap, 2)}C / ${I.num(peak / cap, 2)}C`, I.t('tip_d_crate')]);
      }
      const split = [[I.t('hold'), s.hold_s], [I.t('queue'), s.wait_s], [I.t('setup'), s.setup_s], [I.t('charge'), s.charge_s]]
        .filter(([, v]) => v > 0).map(([k, v]) => `${k.toLowerCase()} ${I.minutes(v)}`).join(' · ');
      rows.push(['d_split', split || '–', I.t('tip_d_split')]);
      if (tariff) {
        const hiE = tariff.high_soc * r._cap;
        const share = kwh > 0 ? Math.max(0, s.energy_out_kwh - Math.max(s.energy_in_kwh, hiE)) / kwh : 0;
        const p = I.pct(tariff.high_soc, 0);
        rows.push(['d_period', `${I.t(s.peak ? 'tariff_peak_short' : 'tariff_offpeak_short')} · ${I.t('d_high_share', { p })} ${I.pct(share, 0)}`,
          `${I.t('tip_d_period')} · ${I.t('tip_d_high_share', { p })}`]);
      }
      rows.push(['stop_money', I.money(s.money, cur), I.t('tip_stop_money')]);
      if (U.isNum(s.soh_pct)) {
        const per = prefs.soh_cost_per_pct;
        rows.push(['d_wear', `${I.soh(s.soh_pct)}${U.isNum(per) ? ' · ' + I.money(s.soh_pct * per, cur) : ''}`,
          I.t('tip_d_wear', { c: U.isNum(per) ? I.money(per, cur, 0) : '–' })]);
      }
      return details(this, `stop:${r._ri}:${r._ai}:${s._num}`, I.t('details'), null,
        U.el('dl', { class: 'kv kv-detail' }, rows.flatMap(([k, v, tip]) => [
          U.el('dt', { text: I.t(k), 'data-tip': tip, class: 'tip-term' }), U.el('dd', { text: v })])));
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
        const th = U.el('th', { scope: 'col', class: key === 'exact' || key === 'feasible' ? 'center' : '', 'data-tip': title || headTip(label) },
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
            td = U.el('td', {}, U.el('span', { class: 'algo-cell', 'data-tip': algoTip(Object.assign({ name: r.algorithm, exact: r.exact }, app.algoInfo(r.algorithm) || {})) }, U.el('i'), r.title));
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
      this._renderVehiclePanel(v);
      const rows = [
        ['kv_battery', `${I.num(v.battery_kwh, 1)} kWh · ${I.t('kv_usable').toLowerCase()} ${I.num(v.capacity_kwh, 1)}`],
        ['kv_temperature', `${I.num(c.temperature_c, 0)} °C`],
        ['kv_departure', I.clock(q.departure_s)],
        ['kv_initial', I.pct(q.initial_soc, 0)],
        ['kv_reserve', `${I.pct(p.reserve_soc, 0)} / ${I.pct(p.min_arrival_soc, 0)}`],
        p.min_stop_soc > 0 ? ['kv_min_stop', I.pct(p.min_stop_soc, 0)] : null,
        ['kv_objective', I.t(p.objective === 'cost' ? 'objective_cost' : 'objective_time')],
        ['kv_queues', I.t(p.queue_aware ? 'on' : 'off')],
        ['kv_speeds', (p.speed_factors || [1]).map((f) => I.pct(f, 0)).join(' / ')],
        ['kv_network', I.t('nodes_edges', { n: I.num(net.n, 0), m: I.num(net.m, 0) })],
        ['kv_stations', I.num(net.stations.length, 0)],
      ];
      for (let i = rows.length - 1; i >= 0; i--) if (!rows[i]) rows.splice(i, 1);
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
      U.setChildren(this.el.kv, rows.flatMap(([k, val, label]) => [U.el('dt', { text: label || I.t(k) }), U.el('dd', { text: val, 'data-tip': String(val).length > 22 ? val : null })]));
      const credit = sc.meta && sc.meta.attribution;
      const licence = metaText(sc, 'licence');   // e.g. "ODbL 1.0 (data), MIT (code)" (OpenStreetMap data)
      const disc = [metaText(sc, 'disclaimer'), credit && I.t('data_credit', { c: credit }),
        licence && I.t('data_licence', { l: licence })].filter(Boolean).join(' ');
      this.el.disclaimer.hidden = !disc;
      this.el.disclaimer.textContent = disc || '';
    }

    /** Research view: the scenario's vehicle with its details and its DC charging curve. */
    _renderVehiclePanel(v) {
      const box = U.$('#scenario-vehicle');
      if (!box) return;
      const kids = [];
      if (v && (v.name || v.key)) kids.push(U.el('div', { class: 'veh-card' }, vehicleCard(this.app, v, { title: true })));
      this._curve = null;
      const c = v && v.dc_curve;
      if (c && Array.isArray(c.soc_breaks) && Array.isArray(c.power_kw) && c.power_kw.length && v.dc_peak_kw > 0) {
        const cv = U.el('canvas', { class: 'curve-canvas', role: 'img', 'aria-label': I.t('veh_curve') });
        kids.push(U.el('div', { class: 'curve-box' },
          U.el('div', { class: 'curve-head' }, U.el('span', { class: 'tip-term', text: I.t('veh_curve'), 'data-tip': I.t('tip_veh_curve'), tabindex: '0' })),
          cv));
        this._curve = { cv, v };
        if (!this._curveRO) {
          this._curveRO = new ResizeObserver(() => this._drawCurve());
          this._curveRO.observe(box);
        }
      }
      U.setChildren(box, kids);
      requestAnimationFrame(() => this._drawCurve());
    }

    /**
     * The vehicle's DC charging curve: power into the battery against SoC
     * (the curve before the cap thin, the effective one filled), the
     * effective limit dashed, and the power of the DC stations the selected
     * route stops at dotted.
     */
    _drawCurve() {
      const cur = this._curve;
      if (!cur || !cur.cv.isConnected || cur.cv.offsetParent === null) return;
      const app = this.app, th = app.theme, v = cur.v;
      const { ctx, w, h } = U.fitCanvas(cur.cv);
      ctx.clearRect(0, 0, w, h);
      const sb = v.dc_curve.soc_breaks, pk = v.dc_curve.power_kw;
      const lim = v.dc_peak_kw;
      const raw = Math.max(...pk);
      const sel = app.selectedRoute();
      const powers = Array.from(new Set((sel && sel._stops ? sel._stops : []).filter((s) => s.station_kind !== 'AC').map((s) => s.station_power_kw))).sort((a, b) => a - b);
      const yd = U.niceDomain(0, Math.max(raw, lim, ...powers) * 1.05, 3);
      const pad = { l: 34, r: 8, t: 8, b: 18 };
      const x0 = pad.l, x1 = w - pad.r, y0 = pad.t, y1 = h - pad.b;
      if (x1 - x0 < 40 || y1 - y0 < 30) return;
      const X = (soc) => x0 + soc * (x1 - x0), Y = (kw) => y1 - (kw / yd.hi) * (y1 - y0);
      ctx.font = `500 10px ${th.font}`;
      ctx.fillStyle = th.chartText;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (const t of yd.ticks) {
        ctx.strokeStyle = th.chartGrid;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(x0, Math.round(Y(t)) + 0.5); ctx.lineTo(x1, Math.round(Y(t)) + 0.5); ctx.stroke();
        ctx.fillText(I.num(t, 0), x0 - 5, Y(t));
      }
      ctx.textBaseline = 'top';
      for (const f of [0, 0.5, 1]) {
        ctx.textAlign = f === 0 ? 'left' : f === 1 ? 'right' : 'center';
        ctx.fillText(I.pct(f, 0), X(f), y1 + 4);
      }
      const step = (cap) => {
        ctx.beginPath();
        for (let i = 0; i < pk.length; i++) {
          const p = Math.min(pk[i], cap);
          if (i === 0) ctx.moveTo(X(sb[0]), Y(p)); else ctx.lineTo(X(sb[i]), Y(p));
          ctx.lineTo(X(sb[i + 1]), Y(p));
        }
      };
      if (raw > lim + 0.5) {   // the part the limit cuts off
        step(Infinity);
        ctx.strokeStyle = U.rgba(th.text3, 0.7);
        ctx.lineWidth = 1.2;
        ctx.setLineDash([2, 2]);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      step(lim);
      ctx.lineTo(X(sb[sb.length - 1]), y1);
      ctx.lineTo(X(sb[0]), y1);
      ctx.closePath();
      ctx.fillStyle = U.rgba(th.accent, th.dark ? 0.22 : 0.14);
      ctx.fill();
      step(lim);
      ctx.strokeStyle = th.accent;
      ctx.lineWidth = 2;
      ctx.lineJoin = 'round';
      ctx.stroke();
      const hline = (kw, color, dash, label, right) => {
        ctx.save();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.2;
        ctx.setLineDash(dash);
        ctx.beginPath(); ctx.moveTo(x0, Math.round(Y(kw)) + 0.5); ctx.lineTo(x1, Math.round(Y(kw)) + 0.5); ctx.stroke();
        ctx.restore();
        ctx.font = `650 9.5px ${th.font}`;
        ctx.fillStyle = color;
        ctx.textAlign = right ? 'right' : 'left';
        ctx.textBaseline = 'bottom';
        ctx.fillText(label, right ? x1 - 2 : x0 + 3, Y(kw) - 2);
      };
      // labels on the right, where the curve has tapered off; a label too close to the one above is left out
      hline(lim, th.text2, [5, 3], I.t('curve_limit', { kw: `${I.num(lim, 0)} kW` }), true);
      let lastY = Y(lim);
      for (const p of powers.slice().reverse()) {
        if (Math.abs(p - lim) <= 1) continue;
        const near = Math.abs(Y(p) - lastY) < 12;
        hline(p, th.cCharge, [1.5, 2.5], near ? '' : `${I.num(p, 0)} kW`, true);
        if (!near) lastY = Y(p);
      }
    }

    renderLegends() {
      const app = this.app, th = app.theme;
      const off = U.$('#legend-off');
      if (off) off.hidden = !app.model.stations.some((st) => st.usable === false);
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
      // the vehicle of the shown plan cannot plug in here (payload "usable")
      const off = sts.filter((st) => st.usable === false);
      if (off.length) kids.push(U.el('div', { class: 'tip-warn', text: off.length === sts.length ? I.t('st_unusable') : `${off.map((st) => st.name || st.cls).join(', ')}: ${I.t('st_unusable')}` }));
      const grid = U.el('div', { class: 'tip-grid' });
      for (const st of sts) {
        if (sts.length > 1) {
          grid.append(U.el('span', { class: 'tip-grid-head', text: st.name || st.cls }));
        }
        grid.append(...kv(I.t('tip_power'), `${st.kind} ${I.num(st.power_kw, 0)} kW`), ...kv(I.t('tip_plugs'), I.num(st.plugs, 0)));
        if (Array.isArray(st.connectors) && st.connectors.length) {
          grid.append(...kv(I.t('tip_connectors'), connectorChips(st.connectors)));
        }
        grid.append(...kv(I.t('tip_price'), `${I.money(st.price_per_kwh, cur)}/kWh`), ...tipTariff(st, tod, cur));
        if (st.peak_s && st.peak_s.length) {
          grid.append(...kv(I.t('tip_peak_hours'), st.peak_s.map((iv) => `${I.clock(iv[0], false)}–${I.clock(iv[1], false)}`).join(', ')));
        }
        grid.append(...kv(I.t('tip_wait', { c: I.clock(tod, false) }), I.minutes(M.waitAt(st.wait_profile, tod))));
        if (st.usable !== undefined && sts.length === 1) {
          grid.append(...kv(I.t('tip_vehicle_fit'), st.usable ? `✓ ${I.t('st_usable')}` : `✕ ${I.t('st_unusable')}`, st.usable ? 'ok' : 'bad'));
        }
      }
      kids.push(grid);
      const spark = U.el('canvas', { class: 'tip-spark', 'aria-hidden': 'true' });
      kids.push(spark);
      kids.push(U.el('div', { class: 'tip-spark-label' }, U.el('span', { text: '00' }), U.el('span', { text: I.t('tip_wait_24') }), U.el('span', { text: '24' })));
      return { kids, spark, profile: main.wait_profile, tod };
    }
  }

  /** One row of a key/value grid: [label, value] (value: text or a node; `cls` added to the value). */
  function kv(label, value, cls) {
    return [U.el('span', { class: 'k', text: label }),
      U.el('span', { class: `v${cls ? ' ' + cls : ''}` }, value)];
  }

  /** Station tooltip rows of a charging tariff: the price below the high-SoC threshold now ([] without a tariff). */
  function tipTariff(st, tod, cur) {
    const f = st.price_factors;
    if (!f || !st.peak_s) return [];
    const peak = M.isPeakAt(st, tod);
    const below = (peak ? f.peak : f.offpeak)[0];
    return kv(I.t('tip_tariff', { c: I.clock(tod, false) }),
      `${I.money(st.price_per_kwh * below, cur)}/kWh · ${I.t(peak ? 'tariff_peak_short' : 'tariff_offpeak_short')}`);
  }

  EVR.panels = {
    Panels, guaranteeBadge, budgetStopped, stoppedText, tariffText, tariffFactors, arrivalSoc, itinerary, itineraryText, mapsUrl, GMAPS_DIR,
    vehicleCard, connectorChips, chemBadge, algoTip, CONNECTOR_LABEL, DC_CONNECTORS,
  };
})(window.EVR = window.EVR || {});
