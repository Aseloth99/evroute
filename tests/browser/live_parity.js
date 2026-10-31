/* The live page of the web site in Chromium: plan trips in the browser and
 * print what the page computed, for tests/test_site.py
 * (test_live_page_in_chromium_matches_the_cli) to compare with `evroute plan`.
 *
 *   node tests/browser/live_parity.js URL CASES_JSON
 *
 * URL is the live page of a built site served over HTTP; CASES_JSON a list
 * of {"scenario": preset, "algorithms": [...]} planned with the page's
 * default trip of that preset.  Prints one JSON object: {"cases": [{scenario,
 * algorithms, results: [...result without the viewer's "_" fields...],
 * shown: {algorithm: trip time as the route list shows it}, errors: [...]}],
 * "console_errors": [...], "hosts": [...], "boot_ms": ..., "plan_ms": [...]}.
 * Needs Node.js with Playwright (require('playwright')) and access to the
 * Pyodide CDN.
 */
'use strict';

const { chromium } = require('playwright');

async function main() {
  const [url, casesJson] = process.argv.slice(2);
  const cases = JSON.parse(casesJson || '[]');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const consoleErrors = [];
  const hosts = new Set();
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push(String(e)));
  page.on('request', (r) => { if (/^https?:/.test(r.url())) hosts.add(new URL(r.url()).host); });   // (not blob: or data:)
  const t0 = Date.now();
  await page.goto(url);
  // the first plan of the default trip ends the boot: results, no busy overlay
  const idle = () => page.waitForFunction(() => {
    const app = window.EVR && window.EVR.app;
    return app && app.liveCtl && !app.liveCtl.busy && app.liveCtl.boot.phase === 'ready';
  }, null, { timeout: 300000, polling: 200 });
  await idle();
  const bootMs = Date.now() - t0;
  const out = [];
  const planMs = [];
  for (const c of cases) {
    const current = await page.evaluate(() => window.EVR.app.payload.scenario_preset);
    if (current !== c.scenario) {
      // the searchable scenario picker, as a visitor uses it: type the name, Enter
      await page.click('#in-scenario-combo');
      await page.fill('#in-scenario-combo', c.scenario);
      await page.keyboard.press('Enter');
      await page.waitForFunction((name) => window.EVR.app.payload.scenario_preset === name, c.scenario, { timeout: 120000 });
      await idle();
    }
    await page.evaluate((algos) => {
      for (const cb of document.querySelectorAll('#algo-picks input[type=checkbox]')) cb.checked = algos.includes(cb.value);
    }, c.algorithms);
    const t = Date.now();
    await page.click('#btn-plan');
    await page.waitForFunction((algos) => {
      const app = window.EVR.app;
      const got = app.model.results.map((r) => r.algorithm);
      return !app.liveCtl.busy && got.length === algos.length && algos.every((a) => got.includes(a));
    }, c.algorithms, { timeout: 300000, polling: 200 });
    planMs.push(Date.now() - t);
    const res = await page.evaluate(() => {
      const strip = (k, v) => (typeof k === 'string' && k.startsWith('_') ? undefined : v);
      const app = window.EVR.app;
      const shown = {};
      for (const li of document.querySelectorAll('#algo-list li')) {
        const i = Array.prototype.indexOf.call(li.parentNode.children, li);
        const r = app.model.results[i];
        if (r) shown[r.algorithm] = (li.querySelector('.algo-time') || {}).textContent || '';
      }
      const sel = app.selectedRoute();
      const hero = document.querySelector('#summary-card .summary-hero .big');
      return {
        results: JSON.parse(JSON.stringify(app.model.results, strip)),
        shown,
        // the trip time of the selected route as the summary card shows it
        summary: { algorithm: sel ? sel.algorithm : null, text: hero ? hero.textContent : null },
        error: document.getElementById('plan-error').hidden ? null : document.getElementById('plan-error').textContent,
      };
    });
    out.push(Object.assign({ scenario: c.scenario, algorithms: c.algorithms }, res));
  }
  await browser.close();
  process.stdout.write(JSON.stringify({ cases: out, console_errors: consoleErrors, hosts: Array.from(hosts).sort(), boot_ms: bootMs, plan_ms: planMs }));
}

main().catch((e) => { console.error(e); process.exit(1); });
