/* The viewer's user interface in Chromium, for tests/test_viewer.py
 * (test_viewer_ui_in_chromium): the live page of `evroute serve` driven like
 * a user, printing what it found as one JSON object.
 *
 *   node tests/browser/viewer_ui.js URL
 *
 * URL is the live viewer of a server planning a Turkey preset (Turkish place
 * names, real coordinates).  Checked here, asserted by the Python test:
 *
 *   start      the research view (the everyday switch off), no console error
 *   search     the origin picker filters Turkish-aware and accent-insensitive
 *              (İstanbul / istanbul / ISTANBUL / Istanbul / ıstanbul), shows
 *              a "no match" row, keys choose an option (the <select> and the
 *              trip follow), Escape restores
 *   tooltip    hover and keyboard focus show the tooltip, Escape hides it
 *   controls   a charging-style chip sets the minimum stop charge, the DC
 *              limit is sent, both reach the request body
 *   everyday   the switch plans with BLS-A* only and shows the trip plan
 *              (steps, totals, a Google Maps link opened only by the user)
 *   centring   the letters of the A/B circles (form and map pins) are centred
 *              in their circles: the ink's bounding box, measured on a
 *              screenshot with sub-pixel edges, at DPR 1 and 2
 *   widths     no horizontal scroll at 390 ... 3440 px, in both modes
 *
 * Needs Node.js with Playwright (require('playwright')).
 */
'use strict';

const { chromium } = require('playwright');

const idle = (page) => page.waitForFunction(() => {
  const app = window.EVR && window.EVR.app;
  return app && app.liveCtl && !app.liveCtl.busy && app.model.results.length > 0;
}, null, { timeout: 120000, polling: 100 });

/**
 * Bounding box of the white letter inside a coloured circle, in device
 * pixels of a screenshot clip, with sub-pixel edges: a pixel's coverage is
 * its whiteness between the circle's colour and white, and an edge column
 * (row) adds the part of the pixel its strongest coverage fills.  Only
 * pixels within `rIn` CSS px of the expected centre count (not the white
 * ring around a map pin).
 */
async function letterBox(page, png, dpr, cx, cy, rIn) {
  return page.evaluate(async ({ b64, dpr, cx, cy, rIn }) => {
    const img = new Image();
    img.src = `data:image/png;base64,${b64}`;
    await img.decode();
    const cv = document.createElement('canvas');
    cv.width = img.width; cv.height = img.height;
    const g = cv.getContext('2d');
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, cv.width, cv.height).data;
    const W = cv.width, H = cv.height;
    const X = cx * dpr, Y = cy * dpr, R = rIn * dpr;
    // the circle's colour: the darkest-in-min-channel pixels in the ring between the letter and the edge
    let bgMin = 255;
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const r = Math.hypot(x + 0.5 - X, y + 0.5 - Y);
        if (r > R * 0.55 && r < R) {
          const i = 4 * (y * W + x);
          bgMin = Math.min(bgMin, Math.min(d[i], d[i + 1], d[i + 2]));
        }
      }
    }
    const cols = new Float64Array(W), rows = new Float64Array(H);
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        if (Math.hypot(x + 0.5 - X, y + 0.5 - Y) > R) continue;
        const i = 4 * (y * W + x);
        const cov = Math.max(0, Math.min(1, (Math.min(d[i], d[i + 1], d[i + 2]) - bgMin) / Math.max(1, 255 - bgMin)));
        if (cov > cols[x]) cols[x] = cov;
        if (cov > rows[y]) rows[y] = cov;
      }
    }
    const edge = (a) => {
      let lo = -1, hi = -1;
      for (let k = 0; k < a.length; k++) if (a[k] > 0.08) { if (lo < 0) lo = k; hi = k; }
      if (lo < 0) return null;
      return [lo + (1 - a[lo]), hi + a[hi]];
    };
    const ex = edge(cols), ey = edge(rows);
    if (!ex || !ey) return null;
    return { dx: ((ex[0] + ex[1]) / 2 - X) / dpr, dy: ((ey[0] + ey[1]) / 2 - Y) / dpr, w: (ex[1] - ex[0]) / dpr, h: (ey[1] - ey[0]) / dpr, bgMin };
  }, { b64: png.toString('base64'), dpr, cx, cy, rIn });
}

/** Centring of the A/B circles of the form (DOM) and of the map pins (canvas) at device pixel ratio `dpr`. */
async function centring(browser, url, dpr) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: dpr });
  const page = await ctx.newPage();
  await page.goto(url);
  await idle(page);
  await page.evaluate(() => { EVR.app.anim.setEnabled(false, false); EVR.app.tooltip.hide(); });
  await page.mouse.move(1, 1);
  await page.waitForTimeout(300);
  const out = [];
  // the form's circles: the SVG's box is the circle's box
  for (const which of ['a', 'b']) {
    const r = await page.evaluate((w) => {
      const el = document.querySelector(`#live-card svg.od-dot.od-${w}`);
      const b = el.getBoundingClientRect();
      return { x: b.left, y: b.top, w: b.width, h: b.height };
    }, which);
    const pad = 3;
    const clip = { x: Math.floor(r.x) - pad, y: Math.floor(r.y) - pad, width: Math.ceil(r.w) + 2 * pad, height: Math.ceil(r.h) + 2 * pad };
    const png = await page.screenshot({ clip });
    const box = await letterBox(page, png, dpr, r.x + r.w / 2 - clip.x, r.y + r.h / 2 - clip.y, r.w / 2 - 1.2);
    out.push(Object.assign({ where: `form-${which}`, dpr }, box));
  }
  // the map pins: circle centre 19 px above the tip, the tip snapped to device pixels (map.js drawPin)
  const pins = await page.evaluate(() => {
    const app = EVR.app, m = app.model, map = app.map;
    const T = map.T(map.viewports()[0]);
    const cr = map.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio;
    const q = app.currentQuery();
    return [['A', q.origin], ['B', q.destination]].map(([k, v]) => {
      const x = Math.round((T.ox + m.X[v] * T.s) * dpr) / dpr, y = Math.round((T.oy - m.Y[v] * T.s) * dpr) / dpr;
      return { k, x: cr.left + x, y: cr.top + y - 19 };
    });
  });
  for (const p of pins) {
    const clip = { x: Math.floor(p.x) - 14, y: Math.floor(p.y) - 14, width: 28, height: 28 };
    const png = await page.screenshot({ clip });
    const box = await letterBox(page, png, dpr, p.x - clip.x, p.y - clip.y, 8.6);
    out.push(Object.assign({ where: `pin-${p.k}`, dpr }, box));
  }
  await ctx.close();
  return out;
}

async function main() {
  const [url] = process.argv.slice(2);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push(String(e)));
  const res = {};
  await page.goto(url);
  await idle(page);

  // ------------------------------------------------------------- start state
  res.start = await page.evaluate(() => ({
    everyday: EVR.app.everyday, view: document.getElementById('app').dataset.view,
    toggle: document.getElementById('everyday-toggle').checked,
    tariff: document.getElementById('in-tariff').checked,
    minArrivalLabel: document.querySelector('label[for="in-arrive"] [data-i18n]').textContent,
    arrivalShown: (document.querySelector('#summary-card .arrive-soc') || {}).textContent || '',
    algorithms: EVR.app.model.results.map((r) => r.algorithm),
  }));

  // ------------------------------------------------------------- search
  const combo = '#in-origin-combo';
  const firstFor = async (text) => {
    await page.click(combo);
    await page.fill(combo, text);
    return page.evaluate(() => {
      const li = document.querySelector('#in-origin-list li');
      return { first: li ? li.textContent : null, empty: !!document.querySelector('#in-origin-list .combo-empty'), mark: !!document.querySelector('#in-origin-list li mark') };
    });
  };
  res.search = {};
  for (const t of ['İstanbul', 'istanbul', 'ISTANBUL', 'Istanbul', 'ıstanbul', 'eskisehir', 'ESKİŞEHİR', 'zzzqx']) res.search[t] = await firstFor(t);
  await page.keyboard.press('Escape');
  res.escape = await page.evaluate(() => ({ text: document.querySelector('#in-origin-combo').value, open: !document.querySelector('#in-origin-list').hidden }));
  await page.click(combo);
  await page.fill(combo, 'konya');
  await page.keyboard.press('Enter');
  res.choose = await page.evaluate(() => {
    const sel = document.getElementById('in-origin');
    return { value: sel.value, label: sel.options[sel.selectedIndex].textContent, origin: EVR.app.currentQuery().origin, input: document.querySelector('#in-origin-combo').value };
  });
  await page.click(combo);
  await page.keyboard.press('Escape');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Home');
  res.keys = await page.evaluate(() => ({
    open: !document.querySelector('#in-origin-list').hidden,
    active: (document.querySelector('#in-origin-list li.active') || {}).textContent || null,
    expanded: document.querySelector('#in-origin-combo').getAttribute('aria-expanded'),
    activedescendant: document.querySelector('#in-origin-combo').getAttribute('aria-activedescendant'),
  }));
  await page.keyboard.press('Escape');

  // ------------------------------------------------------------- tooltip
  await page.mouse.move(1, 1);
  await page.hover('#btn-swap');
  await page.waitForTimeout(700);
  res.tooltipHover = await page.evaluate(() => { const t = document.getElementById('ui-tip'); return { shown: !t.hidden, text: t.textContent }; });
  await page.mouse.move(1, 1);
  await page.focus('#in-dcmax');
  await page.keyboard.press('Shift+Tab');
  await page.keyboard.press('Tab');
  await page.waitForTimeout(100);
  res.tooltipFocus = await page.evaluate(() => {
    const t = document.getElementById('ui-tip');
    const a = document.activeElement;
    return { shown: !t.hidden, text: t.textContent, focused: a ? a.id : null, describedby: a ? a.getAttribute('aria-describedby') : null };
  });
  await page.keyboard.press('Escape');
  res.tooltipEscape = await page.evaluate(() => document.getElementById('ui-tip').hidden);

  // ------------------------------------------------------------- controls
  await page.click('#style-chips [data-style="balanced"]');
  await page.fill('#in-dcmax', '60');
  await page.dispatchEvent('#in-dcmax', 'change');
  res.controls = await page.evaluate(() => {
    const body = EVR.app.liveCtl.request();
    return { min_stop_soc: body.min_stop_soc, dc_max_kw: body.dc_max_kw, reserve: body.reserve_soc, tariff: body.tariff,
      pressed: document.querySelector('#style-chips [aria-pressed="true"]') ? document.querySelector('#style-chips [aria-pressed="true"]').dataset.style : null };
  });
  await page.click('#style-chips [data-style="balanced"]');   // pressed again: off
  res.controlsOff = await page.evaluate(() => EVR.app.liveCtl.request().min_stop_soc);
  await page.fill('#in-dcmax', '');
  await page.dispatchEvent('#in-dcmax', 'change');

  // ------------------------------------------------------------- everyday use
  await page.click('#everyday-switch');
  res.everydayOn = await page.evaluate(() => ({
    view: document.getElementById('app').dataset.view, request: EVR.app.liveCtl.request(),
    hidden: ['#algo-card', '#summary-card', '#stops-card', '.table-card', '.algo-picks', '#mode-seg'].map((s) => document.querySelector(s).offsetParent === null),
    shown: ['#itinerary-card', '#prio-seg'].map((s) => document.querySelector(s).offsetParent !== null),
  }));
  await page.click('#prio-seg [data-prio="cost"]');
  await page.click('#btn-plan');
  await page.waitForFunction(() => !EVR.app.liveCtl.busy && EVR.app.model.results.length === 1, null, { timeout: 120000 });
  res.everydayPlan = await page.evaluate(() => {
    const app = EVR.app, r = app.selectedRoute();
    const it = EVR.panels.itinerary(app, r);
    const card = document.getElementById('itinerary-card');
    const a = card.querySelector('a[href]');
    return {
      algorithms: app.model.results.map((x) => x.algorithm), objective: app.model.scenario.preferences.objective,
      steps: it.steps.map((s) => s.kind), stops: r.stops.length, total: r.total_time_s, arrival: it.totals.arrival - app.model.departure,
      drive: it.steps.filter((s) => s.kind === 'drive').reduce((x, s) => x + s.dist, 0), distance: r.distance_m,
      text: EVR.panels.itineraryText(app, r, it), link: a ? { href: a.getAttribute('href'), rel: a.getAttribute('rel'), target: a.getAttribute('target') } : null,
      cardText: card.textContent, research: /SoH|BLS|Etiket|Label|C oran|C-rate/.test(card.textContent),
    };
  });
  await page.click('#everyday-switch');
  res.everydayOff = await page.evaluate(() => document.getElementById('app').dataset.view);

  // ------------------------------------------------------------- widths
  res.widths = [];
  for (const w of [390, 1280, 1920, 2560, 3440]) {
    for (const view of ['research', 'everyday']) {
      await page.setViewportSize({ width: w, height: w > 2000 ? 1440 : 900 });
      await page.evaluate((v) => EVR.app.setEveryday(v === 'everyday', false), view);
      await page.waitForTimeout(250);
      res.widths.push(await page.evaluate(({ w, v }) => ({ width: w, view: v, scroll: document.documentElement.scrollWidth, inner: window.innerWidth }), { w, v: view }));
    }
  }
  await page.evaluate(() => EVR.app.setEveryday(false, false));
  await ctx.close();

  // ------------------------------------------------------------- centring
  res.centring = [...await centring(browser, url, 1), ...await centring(browser, url, 2)];
  await browser.close();
  res.console_errors = consoleErrors;
  process.stdout.write(JSON.stringify(res));
}

main().catch((e) => { console.error(e); process.exit(1); });
