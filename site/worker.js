/* EVRoute web site -- the in-browser planner (a classic Web Worker).
 *
 * Loads Python (Pyodide, from the pinned jsDelivr URL in the page's config),
 * installs the evroute wheel served by the site with micropip, builds an
 * evroute.server.PlanningService and answers the viewer's API calls with
 * evroute.server.api_call_json -- the functions behind `evroute serve`.  The
 * page (live.js, WorkerTransport) talks to it with these messages:
 *
 *   page -> worker  {type: 'init', config}
 *                   {type: 'call', id, endpoint, args}        args: JSON text
 *   worker -> page  {type: 'progress', phase, loaded, total}   phase: download | install | scenario
 *                   {type: 'ready', timings, network, python, evroute}
 *                   {type: 'failed', message}
 *                   {type: 'reply', id, status, body, fatal}   body: JSON text
 *
 * No other code and no other network access: the Pyodide files come from the
 * CDN, the wheel from the site; planning sends nothing anywhere.
 */
'use strict';

let callPy = null;

/** fetch() that reports the bytes it receives, for the progress bars of the loading screen. */
function countingFetch(onBytes) {
  const plain = self.fetch.bind(self);
  return async function (input, init) {
    const res = await plain(input, init);
    if (!res.ok || !res.body) return res;
    const reader = res.body.getReader();
    const body = new ReadableStream({
      async pull(ctrl) {
        const { done, value } = await reader.read();
        if (done) { ctrl.close(); return; }
        onBytes(value.byteLength);
        ctrl.enqueue(value);
      },
      cancel(reason) { return reader.cancel(reason); },
    });
    return new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
}

/** Bytes fetched over the network and from the browser cache (Resource Timing; jsDelivr allows it). */
function networkReport() {
  let transferred = 0, cached = 0, files = 0;
  try {
    for (const e of self.performance.getEntriesByType('resource')) {
      files += 1;
      if (e.transferSize > 0) transferred += e.transferSize;
      else cached += e.encodedBodySize || e.decodedBodySize || 0;
    }
  } catch (e) {
    return null;   // no Resource Timing in this worker
  }
  return { files, transferred, cached };
}

const PY_SETUP = `
import json
from evroute.server import PlanningService, api_call_json

_service = PlanningService(**json.loads(_evr_config))
_service.base_scenario(None).prepare()   # the default trip, ready for the first plan


def _evr_call(endpoint, request):
    status, text = api_call_json(_service, endpoint, request)
    return [status, text]

_evr_call
`;

async function init(cfg) {
  const t0 = performance.now();
  const timings = {};
  const loaded = { download: 0, install: 0 };
  let phase = 'download', last = 0;
  const report = (force) => {
    const now = performance.now();
    if (!force && now - last < 100) return;
    last = now;
    self.postMessage({ type: 'progress', phase, loaded: loaded[phase] || 0, total: (cfg.sizes || {})[phase] || 0 });
  };
  self.fetch = countingFetch((n) => { if (phase in loaded) loaded[phase] += n; report(false); });
  report(true);

  importScripts(`${cfg.pyodide}pyodide.js`);
  const py = await self.loadPyodide({ indexURL: cfg.pyodide, stdout: (s) => console.log(s), stderr: (s) => console.warn(s) });
  timings.pyodide_ms = Math.round(performance.now() - t0);

  report(true);
  phase = 'install';
  report(true);
  await py.loadPackage('micropip', { messageCallback: () => {} });
  const micropip = py.pyimport('micropip');
  await micropip.install.callKwargs(new URL(cfg.wheel, self.location.href).href, { deps: false });
  micropip.destroy();
  timings.install_ms = Math.round(performance.now() - t0 - timings.pyodide_ms);

  report(true);
  phase = 'scenario';
  report(true);
  py.globals.set('_evr_config', JSON.stringify(cfg.service));
  callPy = py.runPython(PY_SETUP);
  timings.scenario_ms = Math.round(performance.now() - t0 - timings.pyodide_ms - timings.install_ms);
  timings.total_ms = Math.round(performance.now() - t0);
  const proxy = py.runPython('import sys, evroute; [sys.version.split()[0], evroute.__version__]');
  const versions = proxy.toJs();
  proxy.destroy();
  self.postMessage({ type: 'ready', timings, network: networkReport(), python: versions[0], evroute: versions[1] });
}

function call(m) {
  if (!callPy) {
    self.postMessage({ type: 'reply', id: m.id, status: 503, body: JSON.stringify({ error: 'the planner is not ready' }) });
    return;
  }
  try {
    const out = callPy(m.endpoint, m.args);
    const [status, body] = out.toJs();
    out.destroy();
    self.postMessage({ type: 'reply', id: m.id, status, body });
  } catch (err) {
    // api_call answers every Python error itself; this is Pyodide failing (e.g. out of memory)
    console.warn(err);
    const msg = `the in-browser planner failed: ${errorLine(err)}`;
    self.postMessage({ type: 'reply', id: m.id, status: 500, body: JSON.stringify({ error: msg }), fatal: true });
  }
}

/** The line of an error worth showing: for a Python traceback its last line. */
function errorLine(err) {
  const lines = String(err && err.message ? err.message : err).split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines[lines.length - 1] : 'unknown error';
}

self.onmessage = (ev) => {
  const m = ev.data || {};
  if (m.type === 'init') {
    init(m.config).catch((err) => {
      console.warn(err);
      self.postMessage({ type: 'failed', message: errorLine(err) });
    });
  } else if (m.type === 'call') {
    call(m);
  }
};
