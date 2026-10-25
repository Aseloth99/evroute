"""The interactive, animated web viewer: export, serve, and screenshot it.

    python examples/viewer_demo.py                      # -> viewer_demo.html (Turkey, every algorithm)
    python examples/viewer_demo.py --lang en --out trip.html
    python examples/viewer_demo.py --full-front         # exhaustive (time, money) Pareto front (~30 s)
    python examples/viewer_demo.py --serve              # live planning in the browser (Ctrl+C to stop)
    python examples/viewer_demo.py --screenshots docs/img   # PNG screenshots + demo.gif (Node + Playwright)

The exported file is one self-contained HTML page (inline CSS, JavaScript and
data): open it in any browser, offline.  Things to try in the page:

* the **Animation** switch (or key ``A``): the car drives the selected route on
  a simulated clock -- queueing, plugging in and charging at every stop with
  the exact charging curve -- while the HUD, the timeline and the state-of-charge
  chart follow it; ``Space`` plays/pauses, ``←``/``→`` scrub;
* **Race** (``R``): every visible algorithm drives at once, who arrives first?
* **Search** (``S``): replay the order in which the search settled nodes;
  tick *Compare* to see BLS and BLS-A* explore side by side;
* hover a charging station for its 24-hour queue profile, click a route, a
  table row or a Pareto point to select it, switch TR/EN and light/dark.

``--screenshots`` regenerates the images of the documentation.  It needs
Node.js with the ``playwright`` package and a Chromium (``npx playwright
install chromium``); the GIF is encoded with ``ffmpeg`` when available,
otherwise with Pillow.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from evroute.generators.presets import get_scenario
from evroute.viz.export import build_viewer_html, run_all

#: Planner options for an exhaustive (time, money) Pareto front.
FULL_FRONT = {"pareto": {"criteria": ("time", "money"), "targets": "grid", "max_labels": 3_000_000}}


def export(args: argparse.Namespace) -> Path:
    sc = get_scenario(args.scenario)
    options = FULL_FRONT if args.full_front else None
    t0 = time.perf_counter()

    def skipped(name: str, exc: BaseException) -> None:
        print(f"  {name:14s} skipped: {exc}")

    plans = run_all(sc, options=options, on_error=skipped)
    for p in plans:
        ms = p.stats.runtime_s * 1000 if p.stats else float("nan")
        print(f"  {p.algorithm:14s} {ms:9.1f} ms  {p}")
    out = build_viewer_html(sc, plans, args.out, lang=args.lang, animate=not args.no_animate, options=options)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB) in {time.perf_counter() - t0:.1f} s -- open it in a browser")
    return out


# ============================================================ screenshots
_CAPTURE_JS = r"""
// Screenshot / GIF-frame capture of the EVRoute viewer (driven by viewer_demo.py).
const { chromium } = require('playwright');
const path = require('path');
const [html, outDir, framesDir] = process.argv.slice(2);
const url = 'file://' + path.resolve(html);
const NO_MOTION = '*,*::before,*::after{transition:none!important;animation-duration:0s!important}.toast{display:none!important}';

async function open(browser, opts, theme, lang) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(url);
  await page.waitForFunction(() => window.EVR && EVR.app && EVR.app.ready);
  await page.addStyleTag({ content: NO_MOTION });
  await page.evaluate(([theme, lang]) => {
    const app = EVR.app;
    app.setTheme(theme);
    app.setLang(lang);
    if (!app.anim.enabled) app.anim.setEnabled(true, false);
    app.anim.pause();
  }, [theme, lang]);
  page._errors = errors;
  return page;
}

// put the animation into `mode` at trip time t (or search position sp), paused but "playing" for the pulses
async function at(page, mode, t, extra) {
  await page.evaluate(([mode, t, extra]) => {
    const app = EVR.app, a = app.anim;
    if (extra && extra.select) app.select(extra.select[0], extra.select[1]);
    const changed = a.mode !== mode;
    if (changed) a.setMode(mode, false);
    if (extra && extra.compare !== undefined && a.compare !== extra.compare) {
      const cb = document.getElementById('search-compare');
      cb.checked = extra.compare;
      cb.dispatchEvent(new Event('change'));
    }
    a.pause();
    a.playing = true;
    document.getElementById('playbar').classList.add('playing');
    if (mode !== 'search') { a.t = t; a.frame(); }      // the race leaderboard sizes the HUD
    if (changed || (extra && extra.compare !== undefined)) app.map.fitDefault(false);
    if (mode === 'search') { a.sp = t * a._searchMax(); a._acc = [null, null]; } else a.t = t;
    app.map.render();
    a.frame();
    app.redrawCharts();
  }, [mode, t, extra || null]);
  await page.waitForTimeout(60);
}

const info = (page) => page.evaluate(() => {
  const app = EVR.app, r = app.selectedRoute();
  const res = app.model.results;
  const par = res.findIndex((x) => x.alternatives && x.alternatives.length > 2);
  return {
    stops: r._stops.map((s) => ({ a: s.arrival_s, w: s.wait_s + s.setup_s, c: s.charge_s })),
    tEnd: r._tl.tEnd, raceEnd: Math.max(...res.filter((x) => x._ok).map((x) => x._tl.tEnd)),
    pareto: par, mid: par >= 0 ? Math.floor(res[par].alternatives.length * 0.55) : -1,
  };
});

(async () => {
  const browser = await chromium.launch();
  const desk = { viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 };
  const errors = [];
  let page = await open(browser, desk, 'light', 'en');
  const I = await info(page);
  const s0 = I.stops[0], s1 = I.stops[1] || I.stops[0];

  await at(page, 'trip', s0 ? s0.a - 1500 : I.tEnd * 0.3);
  await page.screenshot({ path: outDir + '/viewer-light.png' });

  if (s1) {
    await page.evaluate(() => EVR.app.focusStop(Math.min(1, EVR.app.selectedRoute()._stops.length - 1)));
    await page.waitForTimeout(800);
    await at(page, 'trip', s1.a + s1.w + 0.45 * s1.c);
    await page.screenshot({ path: outDir + '/viewer-charging.png' });
    await page.evaluate(() => EVR.app.map.fitDefault(false));
  }

  await at(page, 'search', 0.55, { compare: true });
  await page.screenshot({ path: outDir + '/viewer-search.png' });
  await at(page, 'search', 1.0, { compare: false });

  // charts: a Pareto alternative selected, car mid-way
  await at(page, 'trip', I.tEnd * 0.42, I.pareto >= 0 ? { select: [I.pareto, I.mid] } : null);
  await page.locator('#bottom').screenshot({ path: outDir + '/viewer-charts.png' });
  errors.push(...page._errors);
  await page.context().close();

  page = await open(browser, desk, 'dark', 'tr');
  await at(page, 'race', I.raceEnd * 0.6);
  await page.screenshot({ path: outDir + '/viewer-dark.png' });
  await at(page, 'trip', I.tEnd * 0.42);
  await page.locator('#bottom').screenshot({ path: outDir + '/viewer-charts-dark.png' });
  errors.push(...page._errors);
  await page.context().close();

  page = await open(browser, { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }, 'light', 'tr');
  await at(page, 'trip', s0 ? s0.a + s0.w + 0.5 * s0.c : I.tEnd * 0.3);
  await page.screenshot({ path: outDir + '/viewer-mobile.png' });
  errors.push(...page._errors);
  await page.context().close();

  // GIF frames: 960 x 600, trip with both charging moments, then the race
  if (framesDir) {
    page = await open(browser, { viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 0.6 }, 'light', 'en');
    const times = [];
    const ramp = (a, b, n) => { for (let k = 0; k < n; k++) times.push(['trip', a + (b - a) * k / n]); };
    let t = 0;
    for (const s of I.stops) {
      ramp(t, s.a, 24);                      // drive to the stop
      ramp(s.a, s.a + s.w, 6);               // queue + plug in
      ramp(s.a + s.w, s.a + s.w + s.c, 22);  // charge
      t = s.a + s.w + s.c;
    }
    ramp(t, I.tEnd, 22);
    for (let k = 0; k < 12; k++) times.push(['trip', I.tEnd]);
    for (let k = 0; k <= 64; k++) times.push(['race', I.raceEnd * Math.min(1, k / 60)]);
    for (let k = 0; k < 10; k++) times.push(['race', I.raceEnd]);
    let n = 0;
    for (const [mode, tt] of times) {
      await at(page, mode, tt);
      await page.screenshot({ path: `${framesDir}/f${String(n++).padStart(4, '0')}.png` });
    }
    errors.push(...page._errors);
    await page.context().close();
  }
  await browser.close();
  if (errors.length) { console.error(errors.join('\n')); process.exit(2); }
})();
"""


def screenshots(html: Path, out_dir: Path, gif: bool = True, fps: int = 15) -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("--screenshots needs Node.js with the 'playwright' package")
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "capture.js"
        script.write_text(_CAPTURE_JS, encoding="utf-8")
        frames = Path(tmp) / "frames"
        frames.mkdir()
        cmd = [node, str(script), str(html), str(out_dir)] + ([str(frames)] if gif else [])
        subprocess.run(cmd, check=True, timeout=1800)
        for png in sorted(out_dir.glob("viewer-*.png")):
            print(f"wrote {png} ({png.stat().st_size / 1e3:.0f} kB)")
        if gif:
            out = out_dir / "demo.gif"
            _encode_gif(sorted(frames.glob("f*.png")), out, fps)
            print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(list(frames.glob('f*.png')))} frames)")


def _encode_gif(frames: list[Path], out: Path, fps: int) -> None:
    """ffmpeg (global palette, transparency diffing) or, without it, Pillow."""
    if not frames:
        raise RuntimeError("no frames captured")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        pattern = str(frames[0].parent / "f%04d.png")
        graph = ("split[a][b];[a]palettegen=max_colors=200:stats_mode=full[p];"
                 "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
        subprocess.run([ffmpeg, "-v", "error", "-y", "-framerate", str(fps), "-i", pattern,
                        "-vf", graph, "-loop", "0", str(out)], check=True, timeout=1800)
        return
    from PIL import Image  # optional dependency of this example only

    imgs = [Image.open(f).convert("RGB") for f in frames]
    palette = imgs[len(imgs) // 3].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pal = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in imgs]
    pal[0].save(out, save_all=True, append_images=pal[1:], duration=int(1000 / fps), loop=0, optimize=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scenario", default="turkey-karaman-istanbul", help="preset name")
    ap.add_argument("--out", default="viewer_demo.html")
    ap.add_argument("--lang", default="tr", choices=["tr", "en"])
    ap.add_argument("--no-animate", action="store_true", help="start with the animation switched off")
    ap.add_argument("--full-front", action="store_true", help="exhaustive (time, money) Pareto front")
    ap.add_argument("--serve", action="store_true", help="start the live planning server instead")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--screenshots", metavar="DIR", help="also write PNG screenshots and demo.gif into DIR")
    ap.add_argument("--no-gif", action="store_true", help="with --screenshots: skip the GIF")
    args = ap.parse_args(argv)
    if args.serve:
        from evroute.server import serve

        serve(args.scenario, port=args.port, open_browser=True, lang=args.lang)
        return 0
    html = export(args)
    if args.screenshots:
        screenshots(html, Path(args.screenshots), gif=not args.no_gif)
    return 0


if __name__ == "__main__":
    sys.exit(main())
