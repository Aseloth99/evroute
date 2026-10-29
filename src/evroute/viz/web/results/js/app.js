/* EVRoute results panel -- app.js
 *
 * Boots the page: reads the embedded payload (format evroute-results/1),
 * builds the header, the section navigation (with scroll spy), the
 * overview (key numbers, exactness checks, run status) and one card per
 * experiment (finding, meaning, charts, table view, CSV download).
 * Language and theme switch without reloading; the theme preference is
 * shared with the route viewer.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.RI;
  const RS = EVR.RS;
  const finite = (x) => typeof x === 'number' && isFinite(x);

  const ICON = {
    check: 'M20 6 9 17l-5-5',
    cross: 'M18 6 6 18M6 6l12 12',
    dash: 'M5 12h14',
    download: 'M12 4v11M7 10l5 5 5-5M5 20h14',
    table: 'M3 5h18v14H3zM3 10h18M3 15h18M9 5v14',
    spark: 'M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8',
    info: 'M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
    arrow: 'M5 12h14M13 6l6 6-6 6',
  };

  const app = {
    data: null,
    state: {},           // per-section UI state (selected case, hidden series)
    tables: new Set(),   // sections whose table view is open
  };

  /* ------------------------------------------------------------ theme */
  function setTheme(pref) {
    const dark = pref === 'dark' || (pref === 'auto' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    U.storage.set('evroute.theme', pref);
    for (const b of U.$$('#theme-seg button')) b.setAttribute('aria-pressed', b.dataset.themeChoice === pref ? 'true' : 'false');
  }

  function setLang(lang) {
    I.setLang(lang);
    U.storage.set('evroute.lang', I.lang);
    for (const b of U.$$('#lang-seg button')) b.setAttribute('aria-pressed', b.dataset.lang === I.lang ? 'true' : 'false');
    render();
  }

  /* ------------------------------------------------------------ helpers */
  function icon(name, cls) { return U.svg(ICON[name], cls || 'ico'); }

  function sectionTitle(id) {
    const s = app.data.sections.find((x) => x.id === id);
    return s ? I.tx(s.title) : id;
  }

  function csvHref(text) {
    return `data:text/csv;charset=utf-8,${encodeURIComponent(text)}`;
  }

  function fmtCell(v, col) {
    if (v === null || v === undefined) return '–';
    if (typeof v === 'boolean') return v ? '✓' : '✕';
    if (typeof v === 'number') {
      if (Number.isInteger(v)) return /(^|_)(pair|nodes|stops|n|feasible|preconditioning|departure_h|settled|generated|slowed_edges|n_completed|fixed_stops|eco_stops)$/.test(col) ? String(v) : I.num(v, 0);
      const a = Math.abs(v);
      return I.num(v, a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 3 : 5);
    }
    return String(v);
  }

  function dataTable(t) {
    const numeric = t.columns.map((c, k) => t.rows.some((r) => typeof r[k] === 'number'));
    const table = U.el('table', { class: 'dt' },
      U.el('thead', {}, U.el('tr', {}, t.columns.map((c, k) => U.el('th', { scope: 'col', class: numeric[k] ? 'num' : null, text: c })))),
      U.el('tbody', {}, t.rows.map((r) => U.el('tr', {}, r.map((v, k) => U.el('td', { class: numeric[k] ? 'num' : null, text: fmtCell(v, t.columns[k]) }))))));
    return U.el('div', { class: 'dt-block' },
      U.el('div', { class: 'dt-name' }, icon('table'), U.el('span', { text: t.name }), U.el('span', { class: 'muted', text: `${I.num(t.total, 0)} × ${t.columns.length}` })),
      U.el('div', { class: 'dt-wrap', tabindex: '0', role: 'region', 'aria-label': t.name }, table),
      t.truncated ? U.el('p', { class: 'cc-foot', text: I.t('rows_truncated', { n: t.rows.length, total: t.total }) }) : null);
  }

  /* ------------------------------------------------------------ header */
  function renderHeader() {
    const d = app.data;
    U.$('#title').textContent = d.title || `EVRoute · ${I.t('page_title')}`;
    const known = d.sections.filter((s) => s.status !== 'missing');
    const bits = [d.source, I.t('n_experiments', { n: known.length })];
    if (finite(d.run.total_seconds)) bits.push(I.t('total_time', { t: I.minutes(d.run.total_seconds / 60) }));
    U.$('#subtitle').textContent = bits.filter(Boolean).join(' · ');
    document.title = `${d.title || I.t('page_title')} · EVRoute`;
    I.apply(document);
  }

  /* --------------------------------------------------------------- nav */
  function renderNav() {
    const track = U.$('#secnav-track');
    const items = [U.el('a', { href: '#overview', class: 'nav-pill', dataset: { target: 'overview' } }, I.t('overview'))];
    for (const s of app.data.sections) {
      items.push(U.el('a', { href: `#sec-${s.id}`, class: `nav-pill${s.status === 'missing' ? ' is-missing' : ''}${s.status === 'failed' || s.status === 'error' ? ' is-bad' : ''}`, dataset: { target: `sec-${s.id}` } },
        s.index ? U.el('span', { class: 'nav-num', text: String(s.index) }) : null, I.tx(s.title).split(':')[0]));
    }
    track.replaceChildren(...items);
  }

  /** Scroll spy: the active pill is the last section whose top has passed below the nav bar. */
  function spy() {
    const pills = U.$$('.nav-pill');
    const targets = pills.map((p) => document.getElementById(p.dataset.target));
    let current = null;
    const update = () => {
      let idx = 0;
      targets.forEach((el, i) => { if (el && el.getBoundingClientRect().top <= 140) idx = i; });
      if ((window.innerHeight + window.scrollY) >= document.documentElement.scrollHeight - 4) idx = targets.length - 1;
      const pill = pills[idx];
      if (pill === current) return;
      current = pill;
      for (const p of pills) p.classList.toggle('active', p === pill);
      const tr = pill.parentElement;
      const l = pill.offsetLeft - 24, r = pill.offsetLeft + pill.offsetWidth + 24;
      if (l < tr.scrollLeft || r > tr.scrollLeft + tr.clientWidth) tr.scrollTo({ left: Math.max(0, l), behavior: 'smooth' });
    };
    if (app.onScroll) window.removeEventListener('scroll', app.onScroll);
    app.onScroll = U.rafThrottle(update);
    window.addEventListener('scroll', app.onScroll, { passive: true });
    update();
    // a partial run (e.g. one experiment): at the top of the page, bring the first
    // experiment that was run into the bar instead of the pills of those not run
    const first = pills.find((p, i) => i > 0 && !p.classList.contains('is-missing'));
    if (current === pills[0] && first && pills.some((p) => p.classList.contains('is-missing'))) {
      const tr = first.parentElement;
      if (first.offsetLeft + first.offsetWidth + 24 > tr.scrollLeft + tr.clientWidth) tr.scrollLeft = Math.max(0, first.offsetLeft - 24);
    }
  }

  /* ---------------------------------------------------------- overview */
  function renderOverview() {
    const d = app.data;
    const root = U.$('#overview');
    const kpis = d.kpis || [];
    const hero = kpis.find((k) => k.hero) || null;
    const rest = kpis.filter((k) => k !== hero);
    const known = d.sections.filter((s) => s.status !== 'missing');
    const meta = [
      I.t('n_experiments', { n: known.length }),
      finite(d.run.total_seconds) ? I.t('total_time', { t: I.minutes(d.run.total_seconds / 60) }) : null,
      d.generated ? I.t('generated', { d: I.date(d.generated) }) : null,
    ].filter(Boolean);
    const intro = U.el('div', { class: 'ov-intro' },
      U.el('p', { class: 'kicker', text: I.t('overview_kicker') }),
      U.el('h2', { id: 'overview-title', text: d.title || I.t('page_title') }),
      U.el('p', { class: 'lead', text: I.t('overview_lead') }),
      U.el('div', { class: 'ov-meta' }, meta.map((m) => U.el('span', { text: m }))));
    const heroEl = hero ? heroTile(hero) : null;
    const n = rest.length;
    const cols = n <= 4 ? Math.max(1, n) : n <= 6 ? 3 : 4;
    const tiles = U.el('div', { class: 'kpi-grid', style: { '--cols': String(cols) } }, rest.map(kpiTile));
    root.replaceChildren(
      U.el('div', { class: 'ov-top card' }, intro, heroEl),
      tiles,
      U.el('div', { class: 'ov-row' }, checksCard(), runCard()));
  }

  function heroTile(k) {
    const sc = app.data.sections.find((s) => s.id === 'scalability');
    let bars = null;
    if (sc && sc.data && sc.data.speedup) {
      // the speed-up pairs at the headline's network size; geometric mean runtimes over the
      // same instances, so the ratio of two bars is the speed-up factor shown above
      const pairs = sc.data.speedup.map((s) => [s.baseline, s.algorithm, s.points.find((p) => p.nodes === sc.data.headline_nodes)])
        .filter((x) => x[2] && finite(x[2].baseline_runtime_ms) && finite(x[2].runtime_ms))
        .map(([a, b, p]) => [a, b, p.baseline_runtime_ms, p.runtime_ms]);
      if (pairs.length) {
        const mx = Math.max(...pairs.flatMap((p) => [p[2], p[3]]));
        bars = U.el('div', { class: 'hero-bars', role: 'img', 'aria-label': `${pairs.map((p) => `${I.algo(p[0])} ${I.ms(p[2])}, ${I.algo(p[1])} ${I.ms(p[3])}`).join('; ')} (${I.t('hero_geo')})` },
          pairs.flatMap(([a, b, ra, rb]) => [[a, ra], [b, rb]]).map(([a, ms]) => U.el('div', { class: 'hb-row' },
            U.el('span', { class: 'hb-name', text: I.algo(a) }),
            U.el('span', { class: 'hb-track' }, U.el('i', { style: { width: `${Math.max(1.2, (100 * ms) / mx)}%`, background: `var(--a-${a})` } })),
            U.el('span', { class: 'hb-val', text: I.ms(ms) }))),
          U.el('span', { class: 'hb-foot', text: I.t('hero_geo') }));
      }
    }
    return U.el('a', { class: `hero-kpi tone-${k.tone}`, href: `#sec-${k.section}` },
      U.el('span', { class: 'kpi-label', text: I.tx(k.label) }),
      U.el('span', { class: 'hero-value', text: I.tx(k.value) }),
      U.el('span', { class: 'kpi-sub', text: I.tx(k.sub) }),
      bars);
  }

  function kpiTile(k) {
    const href = k.section === 'checks' ? '#checks' : `#sec-${k.section}`;
    return U.el('a', { class: `kpi tone-${k.tone}`, href },
      U.el('span', { class: 'kpi-label' }, k.tone === 'ok' || k.tone === 'bad' || k.tone === 'warn' ? U.el('i', { class: 'kpi-dot' }) : null, I.tx(k.label)),
      U.el('span', { class: 'kpi-value', text: I.tx(k.value) }),
      U.el('span', { class: 'kpi-sub', text: I.tx(k.sub) }));
  }

  function checksCard() {
    const c = app.data.checks || { passed: 0, total: 0, groups: [] };
    const ok = c.passed === c.total;
    const head = U.el('div', { class: 'card-head' },
      U.el('h2', { text: I.t('checks_title') }),
      c.total ? U.el('span', { class: `badge ${ok ? 'badge-ok' : 'badge-bad'}` }, icon(ok ? 'check' : 'cross'), ok ? I.t('checks_all') : I.t('checks_some', { n: c.total - c.passed })) : null);
    const list = U.el('ul', { class: 'checks' }, c.groups.map((g) => {
      const good = g.passed === g.total;
      return U.el('li', { class: good ? 'ok' : 'bad' },
        U.el('span', { class: 'ck-ico' }, icon(good ? 'check' : 'cross')),
        U.el('span', { class: 'ck-text' }, U.el('span', { text: I.tx(g.title) }),
          U.el('span', { class: 'ck-secs' }, g.sections.map((s) => U.el('a', { href: `#sec-${s}`, text: sectionTitle(s).split(':')[0] })))),
        U.el('span', { class: 'ck-count', text: `${I.num(g.passed, 0)}/${I.num(g.total, 0)}` }),
        !good && g.failures.length ? U.el('details', { class: 'ck-fail' }, U.el('summary', { text: I.t('checks_some', { n: g.total - g.passed }) }),
          U.el('ul', {}, g.failures.map((f) => U.el('li', { text: f })))) : null);
    }));
    return U.el('section', { class: 'card checks-card', id: 'checks', 'aria-label': I.t('checks_title') }, head,
      U.el('p', { class: 'card-lead', text: I.t('checks_lead') }),
      c.total ? list : U.el('p', { class: 'muted', text: I.t('checks_none') }));
  }

  function runCard() {
    const d = app.data;
    const rows = d.sections.map((s) => {
      const st = s.status || 'ok';
      const cls = st === 'ok' ? 'ok' : st === 'missing' || st === 'empty' ? 'muted' : 'bad';
      return U.el('li', { class: cls },
        U.el('i', { class: 'run-dot' }),
        U.el('a', { href: `#sec-${s.id}`, text: I.tx(s.title).split(':')[0] }),
        U.el('span', { class: 'run-st', text: st === 'ok' ? (finite(s.seconds) ? I.minutes(s.seconds / 60) : I.t('status_ok')) : I.t(`status_${st}`) }));
    });
    return U.el('section', { class: 'card run-card', 'aria-label': I.t('run_title') },
      U.el('div', { class: 'card-head' }, U.el('h2', { text: I.t('run_title') }), U.el('span', { class: 'hint', text: d.source || '' })),
      U.el('ul', { class: 'runs' }, rows));
  }

  /* ---------------------------------------------------------- sections */
  function renderSections() {
    const root = U.$('#sections');
    root.replaceChildren();
    for (const s of app.data.sections) root.appendChild(sectionCard(s));
  }

  function sectionCard(s) {
    const el = U.el('section', { class: `sec card status-${s.status}`, id: `sec-${s.id}`, 'aria-labelledby': `h-${s.id}` });
    const tools = U.el('div', { class: 'sec-tools' });
    const head = U.el('header', { class: 'sec-head' },
      U.el('div', { class: 'sec-title' },
        U.el('span', { class: 'sec-num', text: s.index ? String(s.index).padStart(2, '0') : '–' }),
        U.el('h2', { id: `h-${s.id}`, text: I.tx(s.title) }),
        s.status === 'ok' && finite(s.seconds) ? U.el('span', { class: 'sec-time', text: I.minutes(s.seconds / 60) }) : null,
        s.status !== 'ok' ? U.el('span', { class: `badge ${s.status === 'missing' || s.status === 'empty' ? 'badge-muted' : 'badge-bad'}`, text: I.t(`status_${s.status}`) }) : null),
      tools);
    el.appendChild(head);
    if (s.finding) {
      el.appendChild(U.el('p', { class: 'finding' }, U.el('span', { class: 'finding-ico', 'aria-hidden': 'true' }, icon('spark')),
        U.el('span', {}, U.el('span', { class: 'sr-only', text: `${I.t('finding')}: ` }), I.tx(s.finding))));
    }
    if (s.meaning && I.tx(s.meaning)) {
      el.appendChild(U.el('p', { class: 'meaning' }, U.el('b', { text: `${I.t('what_it_means')} ` }), I.tx(s.meaning)));
    }
    const body = U.el('div', { class: 'sec-body' });
    el.appendChild(body);
    if (s.status === 'missing') {
      body.appendChild(U.el('div', { class: 'sec-note' }, icon('info'),
        U.el('div', {}, U.el('b', { text: I.t('missing_title') }), U.el('p', { text: I.t('missing_body') }),
          U.el('code', { text: `python3 -m evroute experiments ${s.id} -o <results>` }))));
      return el;
    }
    if (s.status === 'failed' || s.status === 'error') {
      body.appendChild(U.el('div', { class: 'sec-note bad' }, icon('cross'),
        U.el('div', {}, U.el('b', { text: I.t('failed_body') }), U.el('code', { text: s.error || '' }))));
    }
    if (s.status === 'empty') body.appendChild(U.el('p', { class: 'sec-empty', text: I.t('empty_body') }));
    const st = app.state[s.id] || (app.state[s.id] = {});
    const renderer = RS[s.id];
    if (renderer && s.data) {
      try {
        renderer(s, { body, tools, state: st });
      } catch (err) {
        console.error(err);    // eslint-disable-line no-console
        body.appendChild(U.el('div', { class: 'sec-note bad' }, icon('cross'), U.el('code', { text: String(err) })));
      }
    } else if (!s.known && s.tables && s.tables.length) {
      body.appendChild(U.el('p', { class: 'sec-empty', text: I.t('unknown_body') }));
      app.tables.add(s.id);
    }
    // table view and CSV downloads
    const tableBox = U.el('div', { class: 'sec-table', hidden: !app.tables.has(s.id) });
    if (s.tables && s.tables.length) {
      const btn = U.el('button', { type: 'button', class: 'btn-sm', 'aria-expanded': app.tables.has(s.id) ? 'true' : 'false', 'aria-controls': `tbl-${s.id}` },
        icon('table'), U.el('span', { text: app.tables.has(s.id) ? I.t('hide_table') : I.t('table') }));
      btn.addEventListener('click', () => {
        const open = tableBox.hidden;
        tableBox.hidden = !open;
        if (open) {
          app.tables.add(s.id);
          if (!tableBox.childElementCount) tableBox.append(...s.tables.map(dataTable));
        } else app.tables.delete(s.id);
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.lastChild.textContent = open ? I.t('hide_table') : I.t('table');
      });
      tools.appendChild(btn);
      tableBox.id = `tbl-${s.id}`;
      if (!tableBox.hidden) tableBox.append(...s.tables.map(dataTable));
    }
    for (const f of s.csv || []) {
      if (!f.text) continue;
      const name = (s.csv.length > 1 ? `${s.id}_${f.name}` : `${s.id}.csv`).replace(/[^\w.+-]+/g, '_');
      tools.appendChild(U.el('a', { class: 'btn-sm', href: csvHref(f.text), download: name, title: f.name },
        icon('download'), U.el('span', { text: s.csv.length > 1 ? f.name.replace(/\.csv$/, '').replace(/^turkey-/, '') : 'CSV' })));
    }
    el.appendChild(tableBox);
    return el;
  }

  function renderFooter() {
    U.$('#footer').replaceChildren(
      U.el('span', { text: I.t('footer', { src: app.data.source || '' }) }),
      U.el('span', { class: 'muted', text: `${app.data.format}` }));
  }

  /* ------------------------------------------------------------ render */
  function render() {
    const y = window.scrollY;
    renderHeader();
    renderNav();
    renderOverview();
    renderSections();
    renderFooter();
    spy();
    window.scrollTo(0, y);
  }

  function fatal(msg) {
    const f = U.$('#fatal');
    f.hidden = false;
    f.textContent = msg;
  }

  function boot() {
    let data = null;
    try {
      data = JSON.parse(U.$('#evroute-results').textContent || 'null');
    } catch (e) {
      fatal(`EVRoute: ${e}`);
      return;
    }
    if (!data || data.format !== 'evroute-results/1') {
      fatal('EVRoute: this page is a template. Build it with:  python -m evroute.viz.results <results_dir> -o results.html');
      return;
    }
    app.data = data;
    I.payload = data;
    const stored = U.storage.get('evroute.lang', null);
    I.setLang(stored === 'tr' || stored === 'en' ? stored : data.lang);
    for (const b of U.$$('#lang-seg button')) {
      b.setAttribute('aria-pressed', b.dataset.lang === I.lang ? 'true' : 'false');
      b.addEventListener('click', () => setLang(b.dataset.lang));
    }
    const pref = U.storage.get('evroute.theme', 'auto');
    for (const b of U.$$('#theme-seg button')) {
      b.setAttribute('aria-pressed', b.dataset.themeChoice === pref ? 'true' : 'false');
      b.addEventListener('click', () => setTheme(b.dataset.themeChoice));
    }
    if (window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      const onChange = () => { if (U.storage.get('evroute.theme', 'auto') === 'auto') setTheme('auto'); };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
    }
    U.$('#btn-print').addEventListener('click', () => window.print());
    render();
    if (location.hash) {
      const t = document.getElementById(location.hash.slice(1));
      if (t) t.scrollIntoView();
    }
    window.EVR_RESULTS_READY = true;
  }

  EVR.resultsApp = app;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window.EVR = window.EVR || {});
