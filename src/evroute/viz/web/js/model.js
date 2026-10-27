/* EVRoute viewer -- model.js
 *
 * Read-only view of the evroute-viewer/1 payload plus the derived data the
 * renderers need: typed coordinate arrays, de-duplicated road geometry,
 * station look-ups, and one *timeline* per route.
 *
 * Timeline.  A route of the payload lists, for every node i of its path,
 * the arrival and departure trip times (node_arrive_s / node_depart_s), the
 * battery energy on arrival and departure and the cumulative distance --
 * all measured by the independent evaluator.  The timeline turns this into
 * consecutive segments
 *
 *     drive(i) : [depart_i, arrive_{i+1}]   edge i, energy linear in time
 *     hold     : [arrival, arrival + hold]  voluntary hold for a cheaper price
 *                                           period (charging tariffs; usually absent)
 *     wait     : [.., .. + wait]            queue at a station (energy constant)
 *     setup    : [.., .. + setup]           plugging in / out (energy constant)
 *     charge   : [.., .. + charge]          energy follows the exact charging
 *                                           trajectory (stop.curve breakpoints)
 *
 * so that stateAt(route, t) gives position, heading, energy, power and
 * status at any simulated trip time t by binary search plus one linear
 * interpolation.  Energy along an edge is interpolated linearly in time:
 * the evaluator only measures node states, so this is a display
 * approximation *inside* an edge and exact at every node.  Charging is
 * exact because power is constant between the curve's breakpoints.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const DAY = 86400;

  /** Drawing order of road classes (minor first, motorways on top). */
  const CLASS_ORDER = ['residential', 'link', 'urban', 'secondary', 'primary', 'trunk', 'motorway'];

  /** Expected wait [s] of a FIFO-safe WaitProfile at time of day `tod` (port of queueing.WaitProfile.__call__). */
  function waitAt(prof, tod) {
    if (!prof || !prof.knots_s || !prof.knots_s.length) return 0;
    const k = prof.knots_s, w = prof.waits_s, n = k.length;
    if (n === 1) return w[0];
    const t = ((tod % DAY) + DAY) % DAY;
    if (t < k[0] || t >= k[n - 1]) {
      const span = k[0] + DAY - k[n - 1];
      const dt = t >= k[n - 1] ? t - k[n - 1] : t + DAY - k[n - 1];
      return w[n - 1] + (w[0] - w[n - 1]) * dt / span;
    }
    const i = Math.min(U.bisectRight(k, t), n - 2);
    return w[i] + (w[i + 1] - w[i]) * (t - k[i]) / (k[i + 1] - k[i]);
  }

  function stationClass(st) {
    if (st.kind === 'AC') return 'AC';
    return st.power_kw >= 250 ? 'HPC' : 'DC';
  }

  class Model {
    constructor(payload) {
      this.payload = payload;
      const net = payload.network;
      const n = net.x.length, m = net.tail.length;
      this.n = n;
      this.m = m;
      this.X = Float64Array.from(net.x);
      this.Y = Float64Array.from(net.y);
      this.Z = Float64Array.from(net.elevation);
      this.tail = Int32Array.from(net.tail);
      this.head = Int32Array.from(net.head);
      this.roadClass = net.road_class;
      this.edgeName = net.edge_name || {};
      this.nodeName = net.node_name || {};
      const len = new Float64Array(m);
      for (let e = 0; e < m; e++) {
        len[e] = net.length ? net.length[e] : Math.hypot(this.X[this.head[e]] - this.X[this.tail[e]], this.Y[this.head[e]] - this.Y[this.tail[e]]);
      }
      this.length = len;

      // bounding box
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, z0 = Infinity, z1 = -Infinity;
      for (let i = 0; i < n; i++) {
        const x = this.X[i], y = this.Y[i], z = this.Z[i];
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
        if (z < z0) z0 = z; if (z > z1) z1 = z;
      }
      if (!n) { x0 = y0 = z0 = 0; x1 = y1 = z1 = 1; }
      this.bbox = [x0, y0, x1, y1];
      this.zRange = [z0, z1];

      // roads grouped by class, each undirected road drawn once
      const twin = net.twin || [];
      const groups = new Map();
      for (let e = 0; e < m; e++) {
        const tw = twin[e];
        if (tw !== undefined && tw >= 0 && tw < e) continue;
        const c = this.roadClass[e] || 'primary';
        if (!groups.has(c)) groups.set(c, []);
        groups.get(c).push(e);
      }
      const order = CLASS_ORDER.filter((c) => groups.has(c));
      for (const c of groups.keys()) if (!order.includes(c)) order.unshift(c);
      this.roadGroups = order.map((c) => ({ cls: c, edges: Int32Array.from(groups.get(c)) }));
      let mean = 0;
      for (let e = 0; e < m; e++) mean += len[e];
      this.meanEdge = m ? mean / m : 1000;

      // stations
      this.stations = (payload.stations || []).map((st, i) => Object.assign({}, st, { index: i, cls: stationClass(st) }));
      this.stationsAt = new Map();
      for (const st of this.stations) {
        if (!this.stationsAt.has(st.node)) this.stationsAt.set(st.node, []);
        this.stationsAt.get(st.node).push(st.index);
      }

      // places with a label priority (lower = more important)
      const sc = payload.scenario || {};
      this.scenario = sc;
      this.query = sc.query || { origin: 0, destination: 0, departure_s: 0, initial_soc: 1 };
      this.currency = sc.currency || 'EUR';
      this.departure = this.query.departure_s || 0;
      this.places = (payload.places || []).map((p) => {
        let rank;
        if (p.kind === 'city' || p.kind === 'town') rank = p.tier >= 1 ? p.tier : 3;
        else if (p.kind === 'pass') rank = 5;
        else rank = 6;
        return Object.assign({}, p, { rank });
      });
      this.placeByNode = new Map(this.places.map((p) => [p.node, p]));

      this.setResults(payload.results || []);
    }

    setResults(results) {
      this.payload.results = results;
      this.results = results.map((r, i) => prepareRoute(this, r, i, null));
    }

    /** Re-translate the route titles after a language switch. */
    localizeTitles() {
      for (const r of this.results) {
        r.title = localTitle(r);
        for (const a of r.alternatives || []) if (a._tl) a.title = localTitle(a);
      }
    }

    /** Alternative `ai` of result `ri` (Pareto member), prepared lazily. */
    alternative(ri, ai) {
      const r = this.results[ri];
      if (!r || !r.alternatives || !r.alternatives[ai]) return null;
      const a = r.alternatives[ai];
      if (!a._tl) prepareRoute(this, a, ri, ai);
      return a;
    }

    /** Name of node `v`, else "near <closest place>" (within 40 km), else "#v". */
    nodeLabel(v) {
      const nm = this.nodeName[v] !== undefined ? this.nodeName[v] : this.nodeName[String(v)];
      if (nm) return nm;
      if (v >= 0 && v < this.n && this.places.length) {
        const hit = this.nearestNode(this.X[v], this.Y[v], true);
        if (hit.node >= 0 && hit.dist <= 40000) {
          const p = this.placeByNode.get(hit.node);
          if (p) return EVR.i18n.t('near', { p: p.name });
        }
      }
      return `#${v}`;
    }

    /** Nearest node to world point (x, y); `named` restricts to labelled places. */
    nearestNode(x, y, named) {
      let best = -1, bd = Infinity;
      if (named) {
        for (const p of this.places) {
          const d = (this.X[p.node] - x) ** 2 + (this.Y[p.node] - y) ** 2;
          if (d < bd) { bd = d; best = p.node; }
        }
        return { node: best, dist: Math.sqrt(bd) };
      }
      const X = this.X, Y = this.Y;
      for (let i = 0; i < this.n; i++) {
        const d = (X[i] - x) * (X[i] - x) + (Y[i] - y) * (Y[i] - y);
        if (d < bd) { bd = d; best = i; }
      }
      return { node: best, dist: Math.sqrt(bd) };
    }

    waitAt(stationIndex, tod) {
      const st = this.stations[stationIndex];
      return st ? waitAt(st.wait_profile, tod) : 0;
    }
  }

  /* ------------------------------------------------------------ routes */
  /** Title in the current language; Pareto members keep their "#k" suffix. */
  function localTitle(r) {
    if (r._title0 === undefined) r._title0 = r.title || r.algorithm || '';
    const I = EVR.i18n;
    if (r._ai !== null && r._ai !== undefined && Number.isInteger(r.member)) {
      return `${I.algoTitle(r.algorithm, r._title0.replace(/ #\d+$/, ''))} #${r.member + 1}`;
    }
    return I.algoTitle(r.algorithm, r._title0);
  }

  function prepareRoute(model, r, ri, ai) {
    r._ri = ri;
    r._ai = ai;
    r.title = localTitle(r);
    const nodes = r.nodes || [];
    const edges = r.edges || [];
    const L = nodes.length;
    r._ok = edges.length > 0 && L === edges.length + 1;
    const cap = r.capacity_kwh || 1;
    r._cap = cap;

    const xy = new Float64Array(2 * L);
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
    for (let i = 0; i < L; i++) {
      const v = nodes[i];
      const x = model.X[v], y = model.Y[v];
      xy[2 * i] = x;
      xy[2 * i + 1] = y;
      if (x < bx0) bx0 = x; if (x > bx1) bx1 = x;
      if (y < by0) by0 = y; if (y > by1) by1 = y;
    }
    r._xy = xy;
    r._bbox = L ? [bx0, by0, bx1, by1] : null;

    // stops: numbered in route order
    const stops = (r.stops || []).slice().sort((a, b) => a.position - b.position || a.arrival_s - b.arrival_s);
    stops.forEach((s, k) => { s._num = k + 1; });
    r._stops = stops;
    const byPos = new Map();
    for (const s of stops) {
      if (!byPos.has(s.position)) byPos.set(s.position, []);
      byPos.get(s.position).push(s);
    }

    // timeline segments
    const segs = [];
    const arr = r.node_arrive_s || [], dep = r.node_depart_s || [];
    const ea = r.node_energy_arrive || [], ed = r.node_energy_depart || [];
    const dist = r.node_dist_m || [];
    for (let i = 0; i < L; i++) {
      const here = byPos.get(i);
      if (here) {
        for (const s of here) {
          let t = s.arrival_s;
          if (s.hold_s > 0) { segs.push({ k: 'hold', t0: t, t1: t + s.hold_s, i, stop: s }); t += s.hold_s; }
          if (s.wait_s > 0) { segs.push({ k: 'wait', t0: t, t1: t + s.wait_s, i, stop: s }); t += s.wait_s; }
          if (s.setup_s > 0) { segs.push({ k: 'setup', t0: t, t1: t + s.setup_s, i, stop: s }); t += s.setup_s; }
          if (s.charge_s > 0) { segs.push({ k: 'charge', t0: t, t1: t + s.charge_s, i, stop: s }); t += s.charge_s; }
        }
      }
      if (i < edges.length && r._ok) {
        segs.push({
          k: 'drive', t0: dep[i], t1: arr[i + 1], i, e: edges[i],
          e0: ed[i], e1: ea[i + 1], d0: dist[i], d1: dist[i + 1],
        });
      }
    }
    let tEnd = L ? Math.max(arr[L - 1] || 0, dep[L - 1] || 0) : 0;
    let dEnd = L ? dist[L - 1] || 0 : 0;
    let strand = null;
    if (!r.feasible && U.isNum(r.stranded_at_m) && r._ok) {
      const s = r.stranded_at_m;
      for (let j = 0; j < segs.length; j++) {
        const g = segs[j];
        if (g.k !== 'drive' || s > g.d1 + 1e-6) continue;
        const f = g.d1 > g.d0 ? U.clamp((s - g.d0) / (g.d1 - g.d0), 0, 1) : 0;
        const u = nodes[g.i], v = nodes[g.i + 1];
        strand = {
          t: g.t0 + f * (g.t1 - g.t0), dist: s, seg: j, f,
          x: U.lerp(model.X[u], model.X[v], f), y: U.lerp(model.Y[u], model.Y[v], f),
        };
        segs.length = j + 1;
        g.t1s = strand.t;
        tEnd = strand.t;
        dEnd = s;
        break;
      }
    }
    r._strand = strand;
    r._tl = { segs, t0s: Float64Array.from(segs.map((g) => g.t0)), tEnd, dEnd };

    // SoC and elevation profiles (km, fraction / m)
    const soc = [];
    const elev = [];
    for (let i = 0; i < L; i++) {
      const dk = (dist[i] || 0) / 1000;
      if (strand && dist[i] > strand.dist) break;
      soc.push([dk, ea[i] / cap, i]);
      if (Math.abs(ed[i] - ea[i]) > 1e-9) soc.push([dk, ed[i] / cap, i]);
      elev.push([dk, model.Z[nodes[i]]]);
    }
    if (strand) {
      soc.push([strand.dist / 1000, 0, -1]);
      const g = segs[strand.seg];
      elev.push([strand.dist / 1000, U.lerp(model.Z[nodes[g.i]], model.Z[nodes[g.i + 1]], strand.f)]);
    }
    r._soc = soc;
    r._elev = elev;

    // search trace, normalised keys
    const tr = r.trace;
    if (tr && tr.nodes && tr.nodes.length) {
      const N = tr.nodes.length;
      const pxy = new Float32Array(2 * N);
      const keys = tr.keys || [];
      let kmin = Infinity, kmax = -Infinity;
      for (let j = 0; j < N; j++) {
        const v = tr.nodes[j];
        pxy[2 * j] = model.X[v];
        pxy[2 * j + 1] = model.Y[v];
        const k = keys[j];
        if (U.isNum(k)) { if (k < kmin) kmin = k; if (k > kmax) kmax = k; }
      }
      const kn = new Float32Array(N);
      const span = kmax > kmin ? kmax - kmin : 1;
      let tx0 = Infinity, ty0 = Infinity, tx1 = -Infinity, ty1 = -Infinity;
      for (let j = 0; j < N; j++) {
        kn[j] = U.isNum(keys[j]) ? (keys[j] - kmin) / span : 0;
        const x = pxy[2 * j], y = pxy[2 * j + 1];
        if (x < tx0) tx0 = x; if (x > tx1) tx1 = x;
        if (y < ty0) ty0 = y; if (y > ty1) ty1 = y;
      }
      r._trace = { n: N, xy: pxy, key: kn, kmin, kmax, bbox: [tx0, ty0, tx1, ty1] };
    } else {
      r._trace = null;
    }
    return r;
  }

  /**
   * State of route `r` at trip time `t` [s]:
   * {kind, x, y, heading, dist, energy, soc, speed, elev, stop, left, power, road, i}
   * kind is one of start | drive | hold | wait | setup | charge | done | stranded | none.
   */
  function stateAt(model, r, t) {
    const nodes = r.nodes || [];
    const cap = r._cap;
    if (!r._ok) {
      const v = nodes.length ? nodes[0] : model.query.origin;
      const e0 = r.node_energy_arrive ? r.node_energy_arrive[0] : 0;
      return { kind: 'none', x: model.X[v], y: model.Y[v], heading: 0, dist: 0, energy: e0, soc: e0 / cap, speed: 0, elev: model.Z[v], i: 0 };
    }
    const tl = r._tl;
    const segs = tl.segs;
    const L = nodes.length;
    if (t >= tl.tEnd) {
      if (r._strand) {
        const g = segs[r._strand.seg];
        const u = nodes[g.i], v = nodes[g.i + 1];
        return {
          kind: 'stranded', x: r._strand.x, y: r._strand.y,
          heading: Math.atan2(model.Y[v] - model.Y[u], model.X[v] - model.X[u]),
          dist: r._strand.dist, energy: 0, soc: 0, speed: 0,
          elev: U.lerp(model.Z[u], model.Z[v], r._strand.f), i: g.i, f: r._strand.f, road: model.edgeName[g.e],
        };
      }
      const v = nodes[L - 1];
      const u = nodes[L - 2];
      const e = r.node_energy_depart[L - 1];
      return {
        kind: 'done', x: model.X[v], y: model.Y[v],
        heading: Math.atan2(model.Y[v] - model.Y[u], model.X[v] - model.X[u]),
        dist: r.node_dist_m[L - 1], energy: e, soc: e / cap, speed: 0, elev: model.Z[v], i: L - 1, f: 0,
      };
    }
    if (t <= 0 && segs.length && segs[0].k === 'drive') {
      const v = nodes[0], w = nodes[1];
      const e = r.node_energy_depart[0];
      return {
        kind: 'start', x: model.X[v], y: model.Y[v], heading: Math.atan2(model.Y[w] - model.Y[v], model.X[w] - model.X[v]),
        dist: 0, energy: e, soc: e / cap, speed: 0, elev: model.Z[v], i: 0, f: 0,
      };
    }
    let j = U.bisectRight(tl.t0s, t);
    if (j < 0) j = 0;
    const g = segs[j];
    if (g.k === 'drive') {
      const span = g.t1 - g.t0;
      const f = span > 0 ? U.clamp((t - g.t0) / span, 0, 1) : 1;
      const u = nodes[g.i], v = nodes[g.i + 1];
      const energy = g.e0 + (g.e1 - g.e0) * f;
      return {
        kind: 'drive',
        x: model.X[u] + (model.X[v] - model.X[u]) * f,
        y: model.Y[u] + (model.Y[v] - model.Y[u]) * f,
        heading: Math.atan2(model.Y[v] - model.Y[u], model.X[v] - model.X[u]),
        dist: g.d0 + (g.d1 - g.d0) * f,
        energy, soc: energy / cap,
        speed: span > 0 ? (g.d1 - g.d0) / span * 3.6 : 0,
        elev: model.Z[u] + (model.Z[v] - model.Z[u]) * f,
        i: g.i, f, road: model.edgeName[g.e],
      };
    }
    const s = g.stop;
    const v = nodes[g.i];
    // heading: direction of arrival, or of departure when standing at the origin
    const a = g.i > 0 ? nodes[g.i - 1] : v;
    const b = g.i > 0 ? v : nodes[Math.min(1, L - 1)];
    const end = stopEnd(s);
    let energy = s.energy_in_kwh, power = 0;
    if (g.k === 'charge') {
      const c = s.curve;
      const tc = t - g.t0;
      if (c && c.t && c.t.length > 1) {
        const k = U.clamp(U.bisectRight(c.t, tc), 0, c.t.length - 2);
        const dt = c.t[k + 1] - c.t[k];
        const f = dt > 0 ? U.clamp((tc - c.t[k]) / dt, 0, 1) : 1;
        energy = c.e[k] + (c.e[k + 1] - c.e[k]) * f;
        power = dt > 0 ? (c.e[k + 1] - c.e[k]) / dt * 3600 : 0;
      } else {
        const f = s.charge_s > 0 ? U.clamp(tc / s.charge_s, 0, 1) : 1;
        energy = s.energy_in_kwh + (s.energy_out_kwh - s.energy_in_kwh) * f;
        power = s.power_kw || 0;
      }
    }
    return {
      kind: g.k, x: model.X[v], y: model.Y[v],
      heading: Math.atan2(model.Y[b] - model.Y[a], model.X[b] - model.X[a]),
      dist: r.node_dist_m[g.i], energy, soc: energy / cap, speed: 0, elev: model.Z[v],
      i: g.i, f: 0, stop: s, power,
      left: g.t1 - t, dwellLeft: end - t, queueLeft: g.k === 'wait' ? g.t1 - t : 0,
    };
  }

  /** Is station `st` in its peak price period at clock time `tod` [s]?  (`st.peak_s`: clock intervals [a, b), charging tariffs only) */
  function isPeakAt(st, tod) {
    const ivs = st && st.peak_s;
    if (!ivs || !ivs.length) return false;
    const t = ((tod % DAY) + DAY) % DAY;
    return ivs.some((iv) => t >= iv[0] && t < iv[1]);
  }

  /** Trip time at which stop `s` ends (hold, queue, set-up and charging after its arrival). */
  function stopEnd(s) {
    return s.arrival_s + (s.hold_s || 0) + s.wait_s + s.setup_s + s.charge_s;
  }

  /** Index of the stop whose dwell contains `t`, or -1. */
  function stopIndexAt(r, t) {
    const stops = r._stops || [];
    for (let k = 0; k < stops.length; k++) {
      const s = stops[k];
      if (t >= s.arrival_s && t <= stopEnd(s)) return k;
    }
    return -1;
  }

  /** Value of the (piecewise linear) SoC profile at distance `dk` km (last value at a jump). */
  function socAtDistance(r, dk) {
    const p = r._soc;
    if (!p || !p.length || dk < p[0][0] || dk > p[p.length - 1][0] + 1e-9) return null;
    for (let j = 1; j < p.length; j++) {
      if (p[j][0] >= dk) {
        const a = p[j - 1], b = p[j];
        if (b[0] - a[0] < 1e-12) continue;
        return a[1] + (b[1] - a[1]) * (dk - a[0]) / (b[0] - a[0]);
      }
    }
    return p[p.length - 1][1];
  }

  EVR.model = { Model, prepareRoute, localTitle, stateAt, stopIndexAt, stopEnd, isPeakAt, socAtDistance, waitAt, stationClass, CLASS_ORDER };
})(window.EVR = window.EVR || {});
