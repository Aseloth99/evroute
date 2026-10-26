/* EVRoute results panel -- sections.js
 *
 * One renderer per experiment.  A renderer receives the section payload
 * (computed by evroute.viz.results) and a context {body, tools, state,
 * rerender} and fills `body` with chart cards; `tools` takes extra controls
 * (case selectors).  Colours always follow the entity: algorithms use the
 * viewer's palette (--a-<name>), cases a separate palette (--case-<i>),
 * warm/cold series --warm/--cold and risks the status tokens.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.RI;
  const RC = EVR.RC;
  const S = RC.S;
  const finite = (x) => typeof x === 'number' && isFinite(x);

  const algoColor = (a) => `var(--a-${a})`;
  const caseColor = (i) => `var(--case-${((i % 4) + 4) % 4})`;
  const PARTS = [
    ['drive_min', 'drive', 'var(--c-drive)'],
    ['queue_min', 'queue', 'var(--c-queue)'],
    ['charge_min', 'charge', 'var(--c-charge)'],
  ];

  /* ------------------------------------------------------------- helpers */
  /** A chart card: {el, host, legend, foot}. */
  function card(title, opts) {
    opts = opts || {};
    const legend = U.el('div', { class: 'cc-legend' });
    const host = U.el('div', { class: 'cc-chart' });
    const foot = U.el('p', { class: 'cc-foot', hidden: !opts.foot, text: opts.foot || '' });
    const el = U.el('figure', { class: `cc${opts.span ? ' span-' + opts.span : ''}` },
      U.el('figcaption', { class: 'cc-head' }, U.el('h3', { text: title }), opts.aside || null),
      legend, host, foot);
    return { el, host, legend, foot };
  }

  function grid(cls, ...cards) {
    return U.el('div', { class: `cc-grid ${cls || ''}` }, ...cards.map((c) => c.el || c));
  }

  /** Segmented selector; returns the element. */
  function seg(label, options, value, onChange) {
    const wrap = U.el('div', { class: 'seg seg-sm seg-scroll', role: 'group', 'aria-label': label });
    for (const o of options) {
      const b = U.el('button', { type: 'button', 'aria-pressed': o.value === value ? 'true' : 'false', title: o.title || null },
        o.color ? U.el('i', { class: 'seg-dot', style: { background: o.color } }) : null, o.label);
      b.addEventListener('click', () => {
        for (const x of wrap.children) x.setAttribute('aria-pressed', 'false');
        b.setAttribute('aria-pressed', 'true');
        onChange(o.value);
      });
      wrap.appendChild(b);
    }
    return wrap;
  }

  function empty(body, text) {
    body.appendChild(U.el('p', { class: 'sec-empty', text: text || I.t('no_data') }));
  }

  function statChips(items) {
    return U.el('div', { class: 'chips' }, items.filter(Boolean).map(([label, value, tone]) =>
      U.el('div', { class: `chip${tone ? ' chip-' + tone : ''}` }, U.el('b', { text: value }), U.el('span', { text: label }))));
  }

  function timeRows(r) {
    return PARTS.map(([f, key, c]) => [c, I.t(key), I.minutes(r[f]), 'rect']);
  }

  /* ========================================================= case studies */
  function caseStudies(sec, ctx) {
    const cases = (sec.data && sec.data.cases) || [];
    if (!cases.length) return empty(ctx.body);
    const st = ctx.state;
    if (!cases.some((c) => c.id === st.case)) st.case = cases[0].id;

    // 1. all cases at a glance: extra time over the best plan, one dot per algorithm
    const overview = card(I.t('cs_excess'), { span: 'full' });
    const algos = [];
    for (const c of cases) for (const r of c.rows) if (!algos.includes(r.algorithm)) algos.push(r.algorithm);
    RC.legend(overview.legend, algos.map((a) => ({ key: a, label: I.algo(a), color: algoColor(a), kind: 'dot' })),
      { hosts: () => [overview.host] });
    ctx.body.appendChild(grid('', overview));
    excessStrip(overview.host, cases);

    // 2. one case in detail
    const pick = cases.length > 1 ? seg(I.t('case'), cases.map((c, i) => ({ value: c.id, label: c.title, color: caseColor(i) })), st.case,
      (v) => { st.case = v; drawCase(); }) : null;
    const detail = U.el('div', { class: 'case-detail' });
    const head = U.el('div', { class: 'case-head' });
    ctx.body.appendChild(U.el('div', { class: 'case-bar' }, pick, head));
    ctx.body.appendChild(detail);

    function drawCase() {
      const c = cases.find((x) => x.id === st.case);
      const ci = cases.indexOf(c);
      head.replaceChildren(...[
        U.el('span', { class: 'case-dot', style: { background: caseColor(ci) } }),
        U.el('b', { text: c.title }),
        U.el('span', { class: 'muted', text: [I.tx(c.subtitle), finite(c.distance_km) ? `${I.num(c.distance_km, 0)} km` : ''].filter(Boolean).join(' · ') }),
        c.viewer ? U.el('a', { class: 'btn-sm btn-link', href: c.viewer, target: '_blank', rel: 'noopener' },
          U.svg('M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'), I.t('open_viewer')) : null].filter(Boolean));
      const bd = card(I.t('cs_breakdown'));
      RC.legend(bd.legend, PARTS.map(([, key, color]) => ({ key, label: I.t(key), color, kind: 'rect' })));
      const m1 = card(I.t('stops'));
      const m2 = card(I.t('money'));
      const m3 = card(I.t('runtime'));
      detail.replaceChildren(grid('', bd), grid('cols-3', m1, m2, m3));
      const rows = c.rows;
      const best = c.rows.filter((r) => r.best).map((r) => r.algorithm);
      RC.hbars(bd.host, {
        aria: `${I.t('cs_breakdown')} · ${c.title}`,
        x: { label: `${I.t('ax_time_h')} [${I.t('unit_h')}:${I.t('unit_min')}]`, fmt: (v) => I.hm(v), time: true },
        rows: rows.map((r) => ({
          key: r.algorithm, label: I.algo(r.algorithm), swatch: algoColor(r.algorithm), muted: !r.feasible,
          segments: r.feasible ? PARTS.map(([f, , color]) => ({ v: r[f] || 0, color })) : [],
          total: r.time_min, valueText: r.feasible ? I.minutes(r.time_min) : null, valueShort: r.feasible ? I.hm(r.time_min) : null,
          note: r.feasible ? null : `✕ ${I.t('infeasible')}`, best: best.includes(r.algorithm),
          tip: { head: I.algo(r.algorithm), sub: r.feasible ? (finite(r.excess_pct) && r.excess_pct > 1e-6 ? `+${I.pct(r.excess_pct, 1)}` : I.t('best')) : I.t('infeasible'),
            rows: [...timeRows(r), [null, I.t('trip_time'), r.feasible ? I.minutes(r.time_min) : '–']] },
        })),
      });
      const small = (host, field, fmt, log, label, tick) => RC.hbars(host, {
        aria: `${label} · ${c.title}`, rowH: 14, gap: 10,
        x: { log, fmt: tick, label: '' },
        rows: rows.map((r) => ({
          key: r.algorithm, label: I.algo(r.algorithm), swatch: algoColor(r.algorithm), muted: !r.feasible,
          segments: finite(r[field]) && (r.feasible || field === 'runtime_ms') ? [{ v: r[field], color: algoColor(r.algorithm) }] : [],
          total: r[field], valueText: finite(r[field]) && (r.feasible || field === 'runtime_ms') ? fmt(r[field]) : '–',
          tip: { head: I.algo(r.algorithm), rows: [[algoColor(r.algorithm), label, finite(r[field]) ? fmt(r[field]) : '–', 'rect']] },
        })),
      });
      small(m1.host, 'stops', (v) => I.num(v, 0), false, I.t('stops'), (v, st) => I.num(v, RC.dec(st)));
      small(m2.host, 'money', (v) => I.money(v, c.currency), false, I.t('money'), (v, st) => I.num(v, RC.dec(st)));
      small(m3.host, 'runtime_ms', (v) => I.ms(v), true, I.t('runtime'), (v) => I.ms(v));
    }
    drawCase();
  }

  /** Row height of the excess strip: room for the dots that coincide (the exact algorithms at 0 %). */
  function stripRowH(cases, narrow, w) {
    const half = stripHalf(cases, w);
    return narrow ? 22 + 2 * half + 4 : Math.max(44, 2 * half + 12);
  }

  /**
   * Half the height a row's dot stack needs: dots closer than 10 px move up
   * and down in 8-px steps; `w` is the chart width (the plot is a bit narrower).
   */
  function stripHalf(cases, w) {
    const plotW = Math.max(120, (w || 800) * 0.62);
    let most = 1;
    for (const c of cases) {
      const xs = c.rows.filter((r) => r.feasible && finite(r.excess_pct)).map((r) => r.excess_pct);
      const span = Math.max(1, ...xs);
      const px = xs.map((x) => (x / span) * plotW);
      for (const a of px) most = Math.max(most, px.filter((b) => Math.abs(b - a) < 10).length);
    }
    return 8 * Math.ceil((most - 1) / 2) + 7;
  }

  /** Rows = cases, x = extra trip time over the best plan of the case (%), dots = algorithms. */
  function excessStrip(host, cases) {
    RC.mount(host, {
      height: (w) => 44 + cases.length * stripRowH(cases, w < 520, w) + 16,
      aria: I.t('cs_excess'),
      draw(ctx) {
        const { svg, w, h } = ctx;
        const narrow = ctx.narrow;
        const labels = cases.map((c) => c.title);
        const lw = narrow ? 0 : Math.min(230, Math.max(...labels.map((l) => RC.textW(l, 12, 650))) + 26);
        let hi = 0;
        for (const c of cases) for (const r of c.rows) if (finite(r.excess_pct)) hi = Math.max(hi, r.excess_pct);
        const strandLabel = `✕ ${I.t('infeasible')}`;
        const nStrand = Math.max(0, ...cases.map((c) => c.rows.filter((r) => !r.feasible).length));
        const strandW = nStrand ? Math.max(RC.textW(strandLabel, 11, 650) + 16, 18 + nStrand * 13) : 0;
        const x0 = lw + (narrow ? 8 : 10), x1 = w - 10 - (strandW ? strandW + 22 : 0);
        const dom = U.niceDomain(0, Math.max(hi, 1), Math.max(2, Math.floor((x1 - x0) / 70)));
        const X = (v) => x0 + (v - dom.lo) / (dom.hi - dom.lo) * (x1 - x0);
        const rowH = stripRowH(cases, narrow, w), top = 12;
        const y1 = top + cases.length * rowH;
        const g = S('g', { class: 'rc-axes' });
        svg.appendChild(g);
        let last = -Infinity;
        for (const v of dom.ticks) {
          const x = Math.round(X(v)) + 0.5;
          g.appendChild(S('line', { class: 'rc-grid', x1: x, x2: x, y1: top, y2: y1 }));
          const t = v === 0 ? '0' : `+${I.pct(v, RC.dec(dom.step))}`;
          const tw = RC.textW(t);
          if (x - tw / 2 < last + 6) continue;
          g.appendChild(S('text', { class: 'rc-tick', x, y: y1 + 17, 'text-anchor': 'middle', text: t }));
          last = x + tw / 2;
        }
        g.appendChild(S('text', { class: 'rc-title', x: x1, y: h - 4, 'text-anchor': 'end', text: I.t('cs_excess_ax') }));
        const sx = x1 + 22;
        if (strandW) {
          g.appendChild(S('rect', { class: 'rc-strand-zone', x: sx, y: top, width: strandW, height: y1 - top, rx: 8 }));
          g.appendChild(S('text', { class: 'rc-tick rc-bad', x: sx + strandW / 2, y: y1 + 17, 'text-anchor': 'middle', text: strandLabel }));
        }
        cases.forEach((c, ci) => {
          const rt = top + ci * rowH;
          const yc = narrow ? rt + 22 + stripHalf(cases, w) : rt + rowH / 2;
          const rg = S('g', { class: 'rc-row' });
          svg.appendChild(rg);
          rg.appendChild(S('rect', { class: 'rc-rowbg', x: 0, y: rt + 2, width: w, height: rowH - 4, rx: 8 }));
          const ly = narrow ? rt + 14 : yc;
          rg.appendChild(S('rect', { class: 'rc-swatch', x: narrow ? x0 : 2, y: ly - 5, width: 10, height: 10, rx: 3, style: { fill: caseColor(ci) } }));
          rg.appendChild(S('text', { class: 'rc-rowlabel', x: (narrow ? x0 : 2) + 16, y: ly, 'dominant-baseline': 'central', text: c.title }));
          rg.appendChild(S('line', { class: 'rc-rowline', x1: x0, x2: x1, y1: Math.round(yc) + 0.5, y2: Math.round(yc) + 0.5 }));
          // dots: the best (left) drawn last; dots that would overlap move up/down
          const used = [];
          const rows = c.rows.slice().sort((a, b) => (b.excess_pct || 0) - (a.excess_pct || 0));
          let ns = 0;
          for (const r of rows) {
            const feas = r.feasible && finite(r.excess_pct);
            const cx = feas ? X(r.excess_pct) : sx + 12 + (ns++) * 13;
            let cy = yc, k = 0;
            while (used.some((u) => Math.abs(u[0] - cx) < 10 && Math.abs(u[1] - cy) < 8) && k < 10) {
              k++;
              cy = yc + (k % 2 ? -1 : 1) * Math.ceil(k / 2) * 8;
            }
            used.push([cx, cy]);
            rg.appendChild(S('circle', { class: `rc-pt${feas ? '' : ' rc-pt-x'}`, cx, cy, r: 5, style: { '--c': algoColor(r.algorithm) }, 'data-k': r.algorithm }));
          }
          const sorted = c.rows.slice().sort((a, b) => (a.feasible === b.feasible ? (a.time_min || 0) - (b.time_min || 0) : a.feasible ? -1 : 1));
          ctx.add({ kind: 'rect', x: 0, y: rt, w, h: rowH, ax: Math.min(x1, X(hi) + 10), ay: yc, el: rg,
            tip: { head: c.title, sub: I.tx(c.subtitle),
              rows: sorted.map((r) => [algoColor(r.algorithm), I.algo(r.algorithm),
                r.feasible ? `${I.minutes(r.time_min)}${r.excess_pct > 1e-6 ? `  +${I.pct(r.excess_pct, 1)}` : '  ★'}` : `✕ ${I.t('infeasible')}`, 'dot']) } });
        });
        g.appendChild(S('line', { class: 'rc-axis', x1: Math.round(X(0)) + 0.5, x2: Math.round(X(0)) + 0.5, y1: top, y2: y1 }));
      },
    });
  }

  /* ========================================================== scalability */
  function scalability(sec, ctx) {
    const d = sec.data;
    if (!d || !d.series.length) return empty(ctx.body);
    const st = ctx.state;
    st.hidden = st.hidden || new Set();
    const c1 = card(I.t('sc_runtime'));
    const c2 = card(I.t('sc_settled'));
    const c3 = card(I.t('sc_speedup'));
    const legend = U.el('div', { class: 'cc-legend sec-legend' });
    ctx.body.appendChild(legend);
    ctx.body.appendChild(grid('cols-3', c1, c2, c3));
    const draw = () => {
      const mk = (field, lo, hi) => d.series.map((s) => ({
        key: s.algorithm, label: I.algo(s.algorithm), color: algoColor(s.algorithm), band: 'focus',
        points: s.points.map((p) => ({ x: p.nodes, y: p[field], lo: p[lo], hi: p[hi] })),
      }));
      const head = (x) => I.t('nodes_n', { n: I.num(x, 0) });
      RC.lines(c1.host, {
        aria: I.t('sc_runtime'), hidden: st.hidden, head, bandInTip: true,
        x: { log: true, label: I.t('ax_nodes'), fmt: (v) => I.compact(v) },
        y: { log: true, label: I.t('ax_runtime').replace(' [ms]', ''), fmt: (v) => I.ms(v), tipFmt: (v) => I.ms(v) },
        series: mk('runtime_ms', 'runtime_min', 'runtime_max'),
      });
      RC.lines(c2.host, {
        aria: I.t('sc_settled'), hidden: st.hidden, head,
        x: { log: true, label: I.t('ax_nodes'), fmt: (v) => I.compact(v) },
        y: { log: true, label: I.t('ax_settled'), fmt: (v) => I.compact(v), tipFmt: (v) => I.num(v, 0) },
        series: mk('settled', 'settled_min', 'settled_max'),
      });
      const sp = (d.speedup || []).filter((s) => !st.hidden.has(s.algorithm));
      RC.lines(c3.host, {
        aria: I.t('sc_speedup'), head,
        x: { log: true, label: I.t('ax_nodes'), fmt: (v) => I.compact(v) },
        y: { log: false, zero: true, label: I.t('ax_speedup'), fmt: (v, st) => `${I.num(v, RC.dec(st))}×`, tipFmt: (v) => I.factor(v) },
        refLines: [{ y: 1 }],
        series: sp.map((s) => ({
          key: s.algorithm, label: I.t('speedup_vs', { a: I.algo(s.algorithm), b: I.algo(s.baseline) }), color: algoColor(s.algorithm),
          points: s.points.map((p) => ({ x: p.nodes, y: p.speedup })),
        })),
        extraRows: (x) => sp.map((s) => {
          const p = s.points.find((q) => q.nodes === x);
          return p && finite(p.settled_ratio) ? [algoColor(s.algorithm), `${I.t('settled')} ÷`, I.factor(p.settled_ratio), 'dash'] : null;
        }).filter(Boolean),
      });
    };
    RC.legend(legend, d.series.map((s) => ({ key: s.algorithm, label: I.algo(s.algorithm) + (finite(s.slope) && d.sizes.length > 2 ? `  · n^${I.num(s.slope, 1)}` : ''), color: algoColor(s.algorithm) })),
      { hidden: st.hidden, onToggle: draw, hosts: () => [c1.host, c2.host, c3.host] });
    draw();
    c1.foot.hidden = false;
    c1.foot.textContent = I.lang === 'tr'
      ? `nokta: ${Math.max(...d.series[0].points.map((p) => p.n))} örneğin ortalaması · göstergedeki bir algoritmanın üzerine gelin: en küçük–en büyük aralığı`
      : `dot: mean of ${Math.max(...d.series[0].points.map((p) => p.n))} instances · hover an algorithm in the legend for its min–max range`;
    // searches stopped by their label budget: the dot averages only the instances they solved
    const lim = (d.budget || []).map((b) => (I.lang === 'tr'
      ? `${I.algo(b.algorithm)} ${I.num(b.nodes, 0)} düğümde ${b.total} örneğin ${b.unsolved} tanesinde etiket bütçesini aştı`
      : `${I.algo(b.algorithm)} hit its label budget on ${b.unsolved} of ${b.total} instances at ${I.num(b.nodes, 0)} nodes`));
    if (lim.length) {
      c1.foot.textContent += I.lang === 'tr'
        ? ` · ${lim.join('; ')} (nokta yalnızca çözülen örneklerin ortalamasıdır)`
        : ` · ${lim.join('; ')} (the dot averages the solved instances only)`;
    }
    c3.foot.hidden = !(d.speedup || []).length;
    c3.foot.textContent = I.t('sc_geo');
  }

  /* ======================================================= optimality gap */
  function optimalityGap(sec, ctx) {
    const d = sec.data;
    if (!d || !d.groups.length) return empty(ctx.body);
    const groups = d.groups.filter((g) => g.stats && g.stats.n).slice().sort((a, b) => a.stats.mean - b.stats.mean);
    const c1 = card(I.t('og_title'));
    const c2 = card(I.lang === 'tr' ? 'Optimuma yakınlık: yolculukların payı' : 'Share of trips within a gap');
    const legend = U.el('div', { class: 'cc-legend sec-legend' });
    ctx.body.appendChild(legend);
    ctx.body.appendChild(grid('cols-2', c1, c2));
    RC.boxes(c1.host, {
      aria: I.t('og_title'),
      y: { label: I.t('ax_gap_pct'), fmt: (v, st) => I.pct(v, RC.dec(st)), tipFmt: (v) => I.pct(v, 2) },
      groups: groups.map((g) => ({
        key: g.algorithm, label: I.algo(g.algorithm), color: algoColor(g.algorithm), stats: g.stats,
        note: [I.t('optimal_n', { k: g.optimal, n: g.points.length }), g.infeasible ? I.t('infeasible_n', { k: g.infeasible }) : null].filter(Boolean).join(' · '),
        shortNote: `✓ ${g.optimal}/${g.points.length}`,
        points: g.points.filter((p) => finite(p.gap_pct)).map((p) => ({ y: p.gap_pct, tip: {
          head: `${I.algo(g.algorithm)} · ${I.t('pair')} ${p.pair}`,
          rows: [[algoColor(g.algorithm), I.t('ax_gap_pct'), I.pct(p.gap_pct, 2), 'dot'],
            [algoColor(g.algorithm), I.t('trip_time'), I.minutes(p.time_min), 'dot'],
            ['var(--a-cfp)', `${I.t('opt_time')} (CFP)`, I.minutes(p.opt_min), 'dot'],
            [null, I.t('stops'), I.num(p.stops, 0)]] } })),
      })),
    });
    // empirical CDF on a common grid so that one hover shows every algorithm
    let gmax = 0;
    for (const g of groups) gmax = Math.max(gmax, g.stats.max || 0);
    gmax = Math.max(gmax, 0.5);
    const N = 90;
    const xs = Array.from({ length: N + 1 }, (_, k) => (k / N) * gmax * 1.02);
    RC.lines(c2.host, {
      aria: c2.el.querySelector('h3').textContent,
      head: (x) => (I.lang === 'tr' ? `fark ≤ ${I.pct(x, 1)}` : `gap ≤ ${I.pct(x, 1)}`), endLabels: false,
      x: { label: I.t('ax_gap_pct'), fmt: (v, st) => I.pct(v, RC.dec(st)), zero: true },
      y: { label: I.lang === 'tr' ? 'yolculuk payı [%]' : 'share of trips [%]', fmt: (v) => I.pct(v, 0), domain: [0, 100], ticks: [0, 25, 50, 75, 100] },
      series: groups.map((g) => {
        const v = g.points.map((p) => p.gap_pct).filter(finite).sort((a, b) => a - b);
        const n = g.points.length;
        return { key: g.algorithm, label: I.algo(g.algorithm), color: algoColor(g.algorithm), markers: false,
          points: xs.map((x) => { let k = 0; while (k < v.length && v[k] <= x + 1e-9) k++; return { x, y: (100 * k) / n }; }) };
      }),
    });
    RC.legend(legend, groups.map((g) => ({ key: g.algorithm, label: I.algo(g.algorithm), color: algoColor(g.algorithm), kind: 'rect' })),
      { hosts: () => [c1.host, c2.host] });
    c1.foot.hidden = false;
    c1.foot.textContent = I.lang === 'tr' ? 'kutu: çeyrekler · çizgi: medyan · ◆ ortalama · bıyıklar: 1,5 IQR' : 'box: quartiles · line: median · ◆ mean · whiskers: 1.5 IQR';
  }

  /* ====================================================== grid resolution */
  function gridResolution(sec, ctx) {
    const d = sec.data;
    if (!d || !d.series.length) return empty(ctx.body);
    const c1 = card(I.t('gr_gap'));
    const c2 = card(I.t('gr_runtime'));
    const legend = U.el('div', { class: 'cc-legend sec-legend' });
    ctx.body.appendChild(legend);
    ctx.body.appendChild(grid('cols-2', c1, c2));
    const tname = (t) => (t === 'grid' ? I.t('targets_grid') : t === 'grid+breaks' ? I.t('targets_breaks') : t);
    const title = (c) => (d.cases.find((x) => x.id === c) || { title: c }).title;
    const multi = d.cases.length > 1;
    const st = ctx.state;
    st.off = st.off || new Set();
    const series = (field) => d.series.map((s) => ({
      key: `${s.case}|${s.targets}`, label: multi ? `${title(s.case)} · ${tname(s.targets)}` : tname(s.targets),
      color: caseColor(s.case_index), dash: s.targets === 'grid',
      points: s.points.map((p) => ({ x: p.step_pct, y: p[field] })),
    }));
    const hiddenSeries = () => new Set(d.series.filter((s) => st.off.has(`case:${s.case}`) || st.off.has(`tg:${s.targets}`)).map((s) => `${s.case}|${s.targets}`));
    const head = (x) => `${I.t('step')} ${I.pct(x, x < 1 ? 1 : 0)} SoC`;
    const draw = () => {
      const hidden = hiddenSeries();
      RC.lines(c1.host, {
        aria: I.t('gr_gap'), hidden, head, endLabels: !multi,
        x: { log: true, reverse: true, label: I.t('ax_step'), fmt: (v) => I.pct(v, v < 1 ? 1 : 0) },
        y: { log: true, floor: 0.1, label: I.t('ax_gap_s'),
          tipFmt: (v) => (v < 0.1 ? `≈ 0 ${I.t('unit_s')}` : `${I.num(v, v < 10 ? 1 : 0)} ${I.t('unit_s')}`) },
        series: series('gap_s'),
      });
      RC.lines(c2.host, {
        aria: I.t('gr_runtime'), hidden, head, endLabels: !multi,
        x: { log: true, reverse: true, label: I.t('ax_step'), fmt: (v) => I.pct(v, v < 1 ? 1 : 0) },
        y: { log: true, label: I.t('ax_runtime').replace(' [ms]', ''), fmt: (v) => I.ms(v), tipFmt: (v) => I.ms(v) },
        series: series('runtime_ms'),
      });
    };
    const items = [];
    if (multi) for (const c of d.cases) items.push({ key: `case:${c.id}`, label: c.title, color: caseColor(d.cases.indexOf(c)), kind: 'rect' });
    for (const t of d.targets) items.push({ key: `tg:${t}`, label: tname(t), color: 'var(--text-2)', kind: t === 'grid' ? 'dash' : 'line' });
    RC.legend(legend, items, { hidden: st.off, onToggle: draw });
    draw();
    c1.foot.hidden = false;
    c1.foot.textContent = I.lang === 'tr' ? '○ boş nokta: fark 0,1 sn\'den küçük (logaritmik eksende sıfır çizilemez)' : '○ hollow marker: gap below 0.1 s (zero cannot be drawn on a log axis)';
  }

  /* ========================================================== temperature */
  function temperature(sec, ctx) {
    const d = sec.data;
    if (!d || !d.series.length) return empty(ctx.body);
    const c1 = card(I.t('te_time'));
    const c2 = card(I.t('te_energy'));
    const legend = U.el('div', { class: 'cc-legend sec-legend' });
    ctx.body.appendChild(legend);
    ctx.body.appendChild(grid('cols-2', c1, c2));
    const meta = { preconditioned: [I.t('preconditioned'), 'var(--warm)'], cold: [I.t('cold_battery'), 'var(--cold)'] };
    const st = ctx.state;
    st.hidden = st.hidden || new Set();
    const mk = (field) => d.series.map((s) => ({ key: s.key, label: meta[s.key][0], color: meta[s.key][1], dash: s.key === 'cold',
      points: s.points.map((p) => ({ x: p.temperature_c, y: p[field] })) }));
    const extra = (x) => {
      const rows = [];
      for (const s of d.series) {
        if (st.hidden.has(s.key)) continue;
        const p = s.points.find((q) => q.temperature_c === x);
        if (p) rows.push([meta[s.key][1], `${I.t('stops')} · ${I.t('charge')}`, `${I.num(p.stops, 0)} · ${I.minutes(p.charge_min)}`, 'dot']);
      }
      return rows;
    };
    const temps = d.series[0].points.map((p) => p.temperature_c);
    const draw = () => {
      RC.lines(c1.host, {
        aria: I.t('te_time'), hidden: st.hidden, head: (x) => I.temp(x), extraRows: extra, endLabels: false,
        x: { label: I.t('ax_temp'), grid: false },
        y: { label: `${I.t('ax_time_h')} [${I.t('unit_h')}:${I.t('unit_min')}]`, fmt: (v) => I.hm(v), tipFmt: (v) => I.minutes(v), time: true },
        series: mk('time_min'),
      });
      RC.lines(c2.host, {
        aria: I.t('te_energy'), hidden: st.hidden, head: (x) => I.temp(x), endLabels: false,
        x: { label: I.t('ax_temp') },
        y: { label: I.t('ax_energy'), tipFmt: (v) => `${I.num(v, 1)} kWh` },
        series: mk('energy_kwh'),
      });
    };
    RC.legend(legend, d.series.map((s) => ({ key: s.key, label: meta[s.key][0], color: meta[s.key][1], kind: s.key === 'cold' ? 'dash' : 'line' })),
      { hidden: st.hidden, onToggle: draw, hosts: () => [c1.host, c2.host] });
    draw();
    if (temps.length) {
      c1.foot.hidden = false;
      c1.foot.textContent = I.lang === 'tr' ? 'BLS-A*: aynı yolculuk, değişen ortam sıcaklığı' : 'BLS-A*: the same trip at different ambient temperatures';
    }
  }

  /* ====================================================== queue awareness */
  function queueAwareness(sec, ctx) {
    const d = sec.data;
    if (!d || !d.series.length) return empty(ctx.body);
    const st = ctx.state;
    if (!d.series.some((s) => s.case === st.case)) st.case = d.series[0].case;
    const c1 = card(I.t('qu_saving'));
    const c2 = card(I.t('qu_wait'), {
      aside: d.series.length > 1 ? seg(I.t('case'), d.series.map((s) => ({ value: s.case, label: s.title, color: caseColor(s.case_index) })), st.case,
        (v) => { st.case = v; drawWait(); }) : null,
    });
    ctx.body.appendChild(grid('cols-2', c1, c2));
    RC.legend(c1.legend, d.series.map((s) => ({ key: s.case, label: s.title, color: caseColor(s.case_index), kind: 'rect' })));
    RC.columns(c1.host, {
      aria: I.t('qu_saving'),
      categories: d.hours.map((h) => ({ key: h, label: I.hour(h) })),
      series: d.series.map((s) => ({ key: s.case, label: s.title, color: caseColor(s.case_index) })),
      value: (h, cs) => {
        const s = d.series.find((x) => x.case === cs);
        const p = s && s.points.find((q) => q.hour === h);
        return p && finite(p.saving_min) ? { v: p.saving_min, tip: I.minutes(p.saving_min) } : null;
      },
      head: (c) => `${I.t('departure')} ${c.label}`,
      y: { label: I.t('ax_saving') },
      x: { label: I.t('ax_hour') },
    });
    function drawWait() {
      const s = d.series.find((x) => x.case === st.case);
      RC.legend(c2.legend, [
        { key: 'blind', label: I.t('blind'), color: 'var(--c-queue)', kind: 'ring' },
        { key: 'aware', label: I.t('aware'), color: 'var(--c-charge)', kind: 'dot' },
      ]);
      RC.dumbbell(c2.host, {
        aria: I.t('qu_wait'),
        categories: s.points.map((p) => ({ key: p.hour, label: I.hour(p.hour) })),
        a: { label: I.t('blind'), color: 'var(--c-queue)' },
        b: { label: I.t('aware'), color: 'var(--c-charge)' },
        pairs: s.points.map((p) => ({ cat: p.hour, a: p.blind_wait_min, b: p.aware_wait_min, tip: {
          head: `${s.title} · ${I.hour(p.hour)}`,
          rows: [['var(--c-queue)', I.t('blind'), I.minutes(p.blind_wait_min), 'ring'],
            ['var(--c-charge)', I.t('aware'), I.minutes(p.aware_wait_min), 'dot'],
            [null, I.t('saving'), I.minutes(p.saving_min)]] } })),
        y: { label: I.t('ax_wait') },
        x: { label: I.t('ax_hour') },
      });
    }
    drawWait();
  }

  /* =============================================================== pareto */
  function pareto(sec, ctx) {
    const d = sec.data;
    if (!d) return empty(ctx.body);
    const fronts = d.fronts || [];
    if (!fronts.length) {
      empty(ctx.body, `${I.t('pa_empty')}: ${(d.empty || []).map((e) => e.title).join(', ')}`);
      return;
    }
    const st = ctx.state;
    if (!fronts.some((f) => f.case === st.case)) st.case = fronts[0].case;
    const host = U.el('div');
    ctx.body.appendChild(host);
    const draw = () => {
      const f = fronts.find((x) => x.case === st.case);
      const c1 = card(I.t('pa_front'), {
        span: 2,
        aside: fronts.length > 1 ? seg(I.t('case'), fronts.map((x, i) => ({ value: x.case, label: x.title, color: caseColor(i) })), st.case,
          (v) => { st.case = v; draw(); }) : null,
      });
      const side = U.el('div', { class: 'pa-side' });
      host.replaceChildren(grid('cols-3', c1, U.el('div', { class: 'cc pa-cards' }, side)));
      const color = 'var(--a-pareto)';
      const cur = f.currency;
      const ws = f.points.map((p) => p.soh_pct).filter(finite);
      const s0 = ws.length ? Math.min(...ws) : 0, s1 = ws.length ? Math.max(...ws) : 1;
      const tipOf = (p, i) => ({ head: `${I.t('member')} ${i + 1} / ${f.points.length}`, rows: [
        [color, I.t('trip_time'), I.minutes(p.time_min), 'dot'],
        [color, I.t('money'), I.money(p.money, cur), 'dot'],
        [color, I.t('wear'), I.soh(p.soh_pct), 'dot'],
        [null, I.t('stops'), I.num(p.stops, 0)],
        [null, I.t('charge'), I.minutes(p.charge_min)]] });
      RC.legend(c1.legend, [
        { key: 'front', label: `${I.algo('pareto')} · ${I.t('front_members', { n: f.points.length })}`, color, kind: 'dot' },
        { key: 'size', label: `${I.t('wear')}: ${I.soh(s0)} – ${I.soh(s1)}`, color, kind: 'size' },
        ...(f.references.length ? [{ key: 'refs', label: I.t('other_algos'), color: 'var(--text-3)', kind: 'diamond' }] : []),
      ]);
      RC.scatter(c1.host, {
        aria: `${I.t('pa_front')} · ${f.title}`,
        color, line: 'stair', sizeDomain: [s0, s1],
        points: f.points.map((p, i) => ({ x: p.time_min, y: p.money, s: p.soh_pct, tip: tipOf(p, i), mark: i === f.fastest || i === f.cheapest || i === f.knee })),
        refs: f.references.map((r) => ({ x: r.time_min, y: r.money, label: I.algo(r.algorithm), color: algoColor(r.algorithm),
          tip: { rows: [[algoColor(r.algorithm), I.t('trip_time'), I.minutes(r.time_min), 'diamond'],
            [algoColor(r.algorithm), I.t('money'), I.money(r.money, cur), 'diamond'],
            [algoColor(r.algorithm), I.t('wear'), I.soh(r.soh_pct), 'diamond']] } })),
        notes: [{ i: f.fastest, text: I.t('fastest') }, { i: f.cheapest, text: I.t('cheapest') }, ...(f.knee !== f.fastest && f.knee !== f.cheapest ? [{ i: f.knee, text: I.t('balanced') }] : [])],
        x: { label: `${I.t('trip_time')} [${I.t('unit_h')}:${I.t('unit_min')}]`, fmt: (v) => I.hm(v), time: true },
        y: { label: `${I.t('ax_money')} [${cur || ''}]` },
      });
      // the front is exact on its charging grid only: name the references that beat a member
      const beat = f.references.filter((r) => r.dominates > 0);
      const k = (f.beaten_by_references || []).length;
      if (beat.length && k) {
        const names = beat.map((r) => I.algo(r.algorithm)).join(', ');
        c1.foot.hidden = false;
        c1.foot.textContent = I.lang === 'tr'
          ? `◆ ${names}, ızgara hedeflerinin arasında şarj ederek ${k} cephe planını her ölçütte geçiyor: bu bir kesinlik hatası değil, şarj ızgarasının sınırı.`
          : `◆ ${names} ${beat.length > 1 ? 'beat' : 'beats'} ${k} front plan${k === 1 ? '' : 's'} in every criterion by charging between the grid's targets: a limit of the charging grid, not an exactness failure.`;
      }
      // the three highlighted plans as cards
      const pick = [[f.fastest, 'fastest'], [f.knee, 'balanced'], [f.cheapest, 'cheapest']].filter((x, k, arr) => arr.findIndex((y) => y[0] === x[0]) === k);
      const p0 = f.points[f.fastest];
      for (const [i, key] of pick) {
        const p = f.points[i];
        side.appendChild(U.el('div', { class: 'pa-card' },
          U.el('div', { class: 'pa-card-head' }, U.el('span', { class: 'pa-tag', text: I.t(key) }), U.el('span', { class: 'muted', text: `#${i + 1}` })),
          U.el('div', { class: 'pa-grid' },
            U.el('div', {}, U.el('small', { text: I.t('trip_time') }), U.el('b', { text: I.minutes(p.time_min) }),
              i !== f.fastest ? U.el('em', { text: `+${I.minutes(p.time_min - p0.time_min)}` }) : null),
            U.el('div', {}, U.el('small', { text: I.t('money') }), U.el('b', { text: I.money(p.money, cur) }),
              i !== f.fastest ? U.el('em', { class: 'good', text: `−${I.money(p0.money - p.money, cur)}` }) : null),
            U.el('div', {}, U.el('small', { text: I.t('wear') }), U.el('b', { text: I.soh(p.soh_pct) })),
            U.el('div', {}, U.el('small', { text: I.t('stops') }), U.el('b', { text: I.num(p.stops, 0) })))));
      }
      if ((d.empty || []).length) side.appendChild(U.el('p', { class: 'cc-foot', text: `${I.t('pa_empty')}: ${d.empty.map((e) => e.title).join(', ')}` }));
    };
    draw();
  }

  /* =========================================================== robustness */
  function robustness(sec, ctx) {
    const d = sec.data;
    if (!d || !d.points.length) return empty(ctx.body);
    const c1 = card(I.t('ro_risk'));
    const c2 = card(I.t('ro_time'));
    ctx.body.appendChild(grid('cols-2', c1, c2));
    const pts = d.points;
    const st = ctx.state;
    st.hidden = st.hidden || new Set();
    const defs = [
      ['p_breach', I.t('p_breach'), 'var(--warn)'],
      ['p_strand', I.t('p_strand'), 'var(--bad)'],
      ['p_short', I.t('p_short'), 'var(--c-drive)'],
    ].filter(([k]) => pts.some((p) => finite(p[k])));
    const pct = (v) => (finite(v) ? 100 * v : null);
    const head = (x) => (I.lang === 'tr' ? `katsayı ${I.num(x, 2)}` : `factor ${I.num(x, 2)}`);
    const n = pts[0].n;
    const draw = () => RC.lines(c1.host, {
      aria: I.t('ro_risk'), hidden: st.hidden, head, endLabels: false, sub: () => (n ? I.t('replays', { n: I.num(n, 0) }) : null),
      x: { label: I.t('ax_factor'), fmt: (v) => I.num(v, 2), ticks: pts.map((p) => p.factor), domain: [pts[0].factor, pts[pts.length - 1].factor] },
      y: { label: I.t('ax_prob'), fmt: (v, st) => I.pct(v, RC.dec(st)), tipFmt: (v) => I.pct(v, 1), zero: true },
      refLines: [{ y: 5, label: I.pct(5, 0) }],
      series: defs.map(([k, label, color]) => ({ key: k, label, color, band: true,
        points: pts.map((p) => ({ x: p.factor, y: pct(p[k]), lo: pct(p[k + '_lo']), hi: pct(p[k + '_hi']) })) })),
    });
    RC.legend(c1.legend, defs.map(([k, label, color]) => ({ key: k, label, color })), { hidden: st.hidden, onToggle: draw, hosts: () => [c1.host] });
    draw();
    c1.foot.hidden = false;
    c1.foot.textContent = `${I.t('ci95')} (Wilson)${n ? ' · ' + I.t('replays', { n: I.num(n, 0) }) : ''}`;
    const ac = algoColor(d.algorithm || 'bls-astar');
    RC.legend(c2.legend, [
      { key: 'planned', label: I.t('planned'), color: ac },
      { key: 'realised', label: I.t('realised'), color: 'var(--text-3)', kind: 'band' },
    ]);
    RC.lines(c2.host, {
      aria: I.t('ro_time'), head, endLabels: false,
      x: { label: I.t('ax_factor'), fmt: (v) => I.num(v, 2), ticks: pts.map((p) => p.factor), domain: [pts[0].factor, pts[pts.length - 1].factor] },
      y: { label: `${I.t('ax_time_h')} [${I.t('unit_h')}:${I.t('unit_min')}]`, fmt: (v) => I.hm(v), tipFmt: (v) => I.minutes(v), time: true },
      extraRows: (x) => {
        const p = pts.find((q) => q.factor === x);
        return p ? [[null, I.t('stops'), I.num(p.stops, 0)]] : [];
      },
      series: [
        { key: 'realised', label: I.t('realised'), color: 'var(--text-3)', band: true, dash: true, markers: false,
          points: pts.map((p) => ({ x: p.factor, y: p.time_p50_min, lo: p.time_p50_min, hi: p.time_p95_min })) },
        { key: 'planned', label: I.t('planned'), color: ac, points: pts.map((p) => ({ x: p.factor, y: p.planned_min })) },
      ],
      bandInTip: true,
    });
  }

  /* ============================================================ eco speed */
  function ecoSpeed(sec, ctx) {
    const d = sec.data;
    if (!d || !d.points.length) return empty(ctx.body);
    const f = sec.facts || {};
    const temps = d.temperatures || [];
    const tcol = (t) => (temps.length > 1 ? (t === temps[0] ? 'var(--cold)' : 'var(--warm)') : (t < 10 ? 'var(--cold)' : 'var(--warm)'));
    ctx.body.appendChild(statChips([
      [I.lang === 'tr' ? 'yolculuk kısalıyor' : 'trips shorter', `${f.improved}/${f.n}`, f.improved ? 'ok' : null],
      [I.lang === 'tr' ? 'en büyük kazanç' : 'largest saving', I.minutes(f.max_saving_min)],
      [I.lang === 'tr' ? 'bir durak daha az' : 'one stop fewer', I.num(f.fewer_stops || 0, 0)],
    ]));
    const c1 = card(I.t('ec_dist'), { span: 'full' });
    ctx.body.appendChild(grid('', c1));
    const shown = d.points.filter((p) => finite(p.saving_min) && p.saving_min > 0.05);
    const nZero = d.points.filter((p) => finite(p.saving_min)).length - shown.length;
    c1.foot.hidden = false;
    c1.foot.textContent = I.lang === 'tr'
      ? `${nZero} yolculukta kazanç 3 sn'den az: tam hız zaten en iyisi (grafikte gösterilmiyor).`
      : `${nZero} trips save less than 3 s: full speed is already best (not drawn).`;
    RC.legend(c1.legend, temps.map((t) => ({ key: String(t), label: I.temp(t), color: tcol(t), kind: 'dot' })));
    RC.dots(c1.host, {
      aria: I.t('ec_dist'),
      x: { label: I.t('ax_saving_s') },
      values: shown.map((p) => ({
        v: p.saving_min * 60, color: tcol(p.temperature_c),
        tip: { head: `${I.t('pair')} ${p.pair} · ${I.temp(p.temperature_c)}`, rows: [
          [tcol(p.temperature_c), I.t('saving'), I.minutes(p.saving_min), 'dot'],
          [null, I.t('fixed'), I.minutes(p.fixed_min)],
          [null, I.t('eco'), I.minutes(p.eco_min)],
          [null, I.t('stops'), `${I.num(p.fixed_stops, 0)} → ${I.num(p.eco_stops, 0)}`],
          [null, I.t('slowed'), I.num(p.slowed_edges, 0)]] },
      })),
    });
  }

  /* ============================================================= vehicles */
  /** "Sedan EV 75 kWh (400 V)" -> "Sedan EV 75 kWh"; the full name stays in the tooltip. */
  function shortName(name) {
    return String(name || '').replace(/\s*\(.*\)\s*$/, '').replace(/\s+usable$/i, '');
  }

  function vehicles(sec, ctx) {
    const d = sec.data;
    if (!d || !d.vehicles.length) return empty(ctx.body);
    const vs = d.vehicles.slice().sort((a, b) => (a.time_min === null) - (b.time_min === null) || (a.time_min || 0) - (b.time_min || 0));
    const c1 = card(I.t('ve_time'));
    const c2 = card(I.t('ve_energy'));
    ctx.body.appendChild(grid('cols-2', c1, c2));
    RC.legend(c1.legend, PARTS.map(([, key, color]) => ({ key, label: I.t(key), color, kind: 'rect' })));
    const best = vs[0] && vs[0].time_min !== null ? vs[0].vehicle : null;
    const sub = (v) => [finite(v.battery_kwh) ? `${I.num(v.battery_kwh, 0)} kWh` : null, finite(v.dc_peak_kw) ? `DC ${I.num(v.dc_peak_kw, 0)} kW` : null].filter(Boolean).join(' · ');
    RC.hbars(c1.host, {
      aria: I.t('ve_time'),
      x: { label: `${I.t('ax_time_h')} [${I.t('unit_h')}:${I.t('unit_min')}]`, fmt: (v) => I.hm(v), time: true },
      rows: vs.map((v) => ({
        key: v.vehicle, label: shortName(v.name), muted: !v.feasible, best: v.vehicle === best,
        segments: v.feasible ? PARTS.map(([fld, , color]) => ({ v: v[fld] || 0, color })) : [],
        total: v.time_min, valueText: v.feasible ? `${I.minutes(v.time_min)} · ${I.num(v.stops, 0)} ${I.lang === 'tr' ? 'durak' : (v.stops === 1 ? 'stop' : 'stops')}` : null,
        valueShort: v.feasible ? I.hm(v.time_min) : null,
        note: v.feasible ? null : `✕ ${I.t('infeasible')}`,
        tip: { head: v.name, sub: sub(v), rows: [...timeRows(v), [null, I.t('stops'), I.num(v.stops, 0)], [null, I.t('trip_time'), I.minutes(v.time_min)]] },
      })),
    });
    RC.legend(c2.legend, [{ key: 'e', label: I.t('energy'), color: 'var(--accent)', kind: 'rect' }]);
    RC.hbars(c2.host, {
      aria: I.t('ve_energy'),
      x: { label: `${I.t('energy')} [kWh]` },
      rows: vs.map((v) => ({
        key: v.vehicle, label: shortName(v.name), muted: !v.feasible,
        segments: finite(v.energy_kwh) ? [{ v: v.energy_kwh, color: 'var(--accent)' }] : [],
        total: v.energy_kwh, valueText: `${I.num(v.energy_kwh, 0)} kWh · ${I.money(v.money, d.currency)}`, valueShort: `${I.num(v.energy_kwh, 0)} kWh`,
        tip: { head: v.name, sub: sub(v), rows: [['var(--accent)', I.t('energy'), `${I.num(v.energy_kwh, 1)} kWh`, 'rect'],
          [null, I.t('charged'), `${I.num(v.charged_kwh, 1)} kWh`], [null, I.t('money'), I.money(v.money, d.currency)],
          [null, I.t('wear'), I.soh(v.soh_pct)]] },
      })),
    });
  }

  EVR.RS = {
    'case-studies': caseStudies,
    scalability,
    'optimality-gap': optimalityGap,
    'grid-resolution': gridResolution,
    temperature,
    'queue-awareness': queueAwareness,
    pareto,
    robustness,
    'eco-speed': ecoSpeed,
    vehicles,
    helpers: { card, grid, seg, empty, algoColor, caseColor },
  };
})(window.EVR = window.EVR || {});
