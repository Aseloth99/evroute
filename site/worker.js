/* EVRoute web site -- the in-browser planner (a classic Web Worker).
 *
 * Loads Python (Pyodide, from the pinned jsDelivr URL in the page's config),
 * installs the evroute wheel served by the site with micropip, builds an
 * evroute.server.PlanningService and answers the viewer's API calls with
 * evroute.server.api_call_json -- the functions behind `evroute serve`.  The
 * page (live.js, WorkerTransport) talks to it with these messages:
 *
 *   page -> worker  {type: 'init', config, prepare}             prepare: preset to get ready
 *                   {type: 'call', id, endpoint, args}        args: JSON text
 *   worker -> page  {type: 'progress', phase, loaded, total}   phase: download | install | scenario
 *                   {type: 'ready', timings, network, python, evroute}
 *                   {type: 'failed', message, reason}          reason: 'stale' | 'integrity' | undefined
 *                   {type: 'reply', id, status, body, fatal}   body: JSON text
 *
 * Every file taken from the CDN is checked against the SHA-256 digest the
 * site was built with (config.integrity) before it is used, and the worker
 * fetches nothing else from there.  No other code and no other network
 * access: the Pyodide files come from the CDN, the wheel from the site;
 * planning sends nothing anywhere.
 */
'use strict';

let callPy = null;
let failed = false;

/** Tell the page that loading failed (once).  Also called from inside a fetch: Pyodide
 *  swallows an error of its WebAssembly download and would otherwise wait forever. */
function fail(err) {
  if (failed) return;
  failed = true;
  console.warn(err);
  self.postMessage({ type: 'failed', message: errorLine(err), reason: (err && err.reason) || undefined });
}

/** An error the page explains in its own words (reason: 'stale' | 'integrity'). */
function reasonError(reason, message) {
  const err = new Error(message);
  err.reason = reason;
  return err;
}

/** Read a response body chunk by chunk (reporting the bytes) into one ArrayBuffer. */
async function readAll(res, onBytes) {
  if (!res.body) return res.arrayBuffer();
  const reader = res.body.getReader();
  const parts = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    n += value.byteLength;
    onBytes(value.byteLength);
  }
  const out = new Uint8Array(n);
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.byteLength; }
  return out.buffer;
}

function hex(buf) {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * fetch() for the worker: reports the bytes it receives (progress bars of the
 * loading screen) and, for the CDN files, refuses any file that is not in
 * `integrity` ({file name: sha256 hex}) or whose digest differs.  A CDN file
 * that fails ends the loading at once (fail()).
 */
function guardedFetch(prefix, integrity, onBytes) {
  const plain = self.fetch.bind(self);
  const subtle = self.crypto && self.crypto.subtle;
  if (!subtle) console.warn('EVRoute: no WebCrypto here (not a secure context); CDN files are not verified');
  return async function (input, init) {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const cdn = url.startsWith(prefix);
    const name = cdn ? url.slice(prefix.length).split(/[?#]/)[0] : null;
    const refuse = (err) => { if (cdn) fail(err); throw err; };
    if (cdn && !(name in integrity)) refuse(reasonError('integrity', `refusing an unexpected file from the CDN: ${name}`));
    let res, buf;
    try {
      res = await plain(input, init);
      if (!res.ok) {
        if (cdn) refuse(new Error(`${name}: HTTP ${res.status}`));
        return res;
      }
      buf = await readAll(res, onBytes);
    } catch (err) {
      return refuse(err);
    }
    if (cdn && subtle) {
      const got = hex(await subtle.digest('SHA-256', buf));
      if (got !== integrity[name]) refuse(reasonError('integrity', `${name} from the CDN does not match its SHA-256 digest`));
    }
    return new Response(buf, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
}

/** Load a script from the CDN through the guarded fetch (classic worker: importScripts of a blob). */
async function importVerified(url) {
  const res = await self.fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  const blob = new Blob([await res.arrayBuffer()], { type: 'text/javascript' });
  const local = URL.createObjectURL(blob);
  try {
    importScripts(local);
  } finally {
    URL.revokeObjectURL(local);
  }
}

/** Bytes fetched over the network and from the browser cache (Resource Timing; jsDelivr allows it). */
function networkReport() {
  let transferred = 0, cached = 0, files = 0;
  try {
    for (const e of self.performance.getEntriesByType('resource')) {
      if (e.name.startsWith('blob:')) continue;
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
from evroute.server import PlanningService, PlanRequestError, api_call_json

_service = PlanningService(**json.loads(_evr_config))
try:
    _service.base_scenario(_evr_prepare or None).prepare()   # the trip on screen, ready for the first plan
except PlanRequestError:
    _service.base_scenario(None).prepare()


def _evr_call(endpoint, request):
    status, text = api_call_json(_service, endpoint, request)
    return [status, text]

_evr_call
`;

async function init(cfg, prepare) {
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
  self.fetch = guardedFetch(cfg.pyodide, cfg.integrity || {}, (n) => { if (phase in loaded) loaded[phase] += n; report(false); });
  report(true);

  // pyodide.js, then pyodide.asm.js (defines _createPyodideModule, so pyodide.js does not load it again)
  await importVerified(`${cfg.pyodide}pyodide.js`);
  await importVerified(`${cfg.pyodide}pyodide.asm.js`);
  const py = await self.loadPyodide({ indexURL: cfg.pyodide, stdout: (s) => console.log(s), stderr: (s) => console.warn(s) });
  timings.pyodide_ms = Math.round(performance.now() - t0);

  report(true);
  phase = 'install';
  report(true);
  const wheel = new URL(cfg.wheel, self.location.href).href;
  // a page from before the latest deploy names a wheel the site no longer has
  const head = await fetch(wheel, { method: 'HEAD', cache: 'no-store' }).catch(() => null);
  if (head && head.status === 404) throw reasonError('stale', 'the web site has been updated since this page was loaded');
  await py.loadPackage('micropip', { messageCallback: () => {} });
  const micropip = py.pyimport('micropip');
  await micropip.install.callKwargs(wheel, { deps: false });
  micropip.destroy();
  timings.install_ms = Math.round(performance.now() - t0 - timings.pyodide_ms);

  report(true);
  phase = 'scenario';
  report(true);
  py.globals.set('_evr_config', JSON.stringify(cfg.service));
  py.globals.set('_evr_prepare', typeof prepare === 'string' ? prepare : null);
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
    init(m.config, m.prepare).catch(fail);
  } else if (m.type === 'call') {
    call(m);
  }
};
