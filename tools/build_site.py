#!/usr/bin/env python3
"""Build the EVRoute web site (GitHub Pages) into a folder.

::

    python tools/build_site.py -o _site                 # the whole site
    python tools/build_site.py -o _site --quick         # small examples (tests, previews)
    python tools/build_site.py -o _site --wheel dist/evroute-1.0.0-py3-none-any.whl
    python -m http.server -d _site 8000                 # look at it: http://127.0.0.1:8000/

The site (``.github/workflows/pages.yml`` builds and deploys it on every push
to ``main``):

``index.html``
    The landing page (Turkish and English, light and dark), from
    ``site/index.html`` and ``site/partials/``; ``assets/`` holds the
    viewer's design tokens (``theme.css``), ``site.css``, ``site.js`` and the
    demo GIF.  No external fonts, scripts or trackers.
``live/``
    The viewer in live mode, planning in the visitor's browser: EVRoute runs
    in a Web Worker (``site/worker.js``) under Pyodide, loaded from the pinned
    jsDelivr URL :data:`PYODIDE_URL`, and the evroute wheel served by the
    site is installed with micropip.  The worker answers the viewer with
    :func:`evroute.server.api_call_json`, the functions behind ``evroute
    serve``, so a plan is the one the command line computes.  Only the
    synthetic presets are offered (not ``turkey-osm-*``: the real-data network
    is not planned live), with the label and time limits :data:`WEB_OPTIONS`
    and the time budget :data:`TIME_BUDGET_S` per planner.  The worker checks
    every CDN file against its SHA-256 digest (:data:`PYODIDE_FILES`).  The
    wheel is the package without the tables of the real-data network
    (:data:`WEB_WHEEL_EXCLUDE`, 1.5 MB the live page never uses), written
    reproducibly (the same sources give the same bytes); its folder name is a
    content hash, so browsers never keep a stale copy, and a page cached from
    before a deploy that changed it asks the visitor to reload.
``examples/``
    Prebuilt viewer pages (nothing is computed when they open): Karaman →
    İstanbul on the approximate network, the same trip under the
    demonstration tariff, Karaman → İstanbul on the OpenStreetMap data, and
    the results dashboard (a copy of the committed ``docs/demo/results.html``:
    the long experiments cannot run in CI).
``site.json``
    What was built: versions, sizes, budgets.

Standard library only, plus building the wheel (``python -m build``, or
``pip wheel`` when ``build`` is not installed) unless ``--wheel`` names one.
The planner is imported from this repository's ``src/`` folder, the code the
wheel is built from.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SITE_SRC = ROOT / "site"
sys.path.insert(0, str(ROOT / "src"))

#: The repository; every documentation link points into it.
REPO = "https://github.com/Aseloth99/evroute"
#: Where the site is published (GitHub Pages of the repository).
SITE_URL = "https://aseloth99.github.io/evroute/"

#: Pyodide release of the live planner: 0.29.5 (2026-09-16), the maintenance
#: release of the stable 0.29 line, with Python 3.13.2 -- a Python version
#: the test suite covers (3.10-3.13).  Routes, stops and every displayed
#: value match CPython; a float may differ in its last digit, since the
#: browser's maths library (sin, atan2, log10, ...) rounds differently from
#: glibc.  (The 314.x line runs Python 3.14.)
PYODIDE_VERSION = "0.29.5"
PYODIDE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
#: Every file the live page takes from jsDelivr: (bytes transferred with brotli, bytes
#: decoded, SHA-256 of the decoded file), measured on 2026-10-29.  The worker refuses any
#: other file from the CDN and any file whose digest differs.
PYODIDE_FILES: dict[str, tuple[int, int, str]] = {
    "pyodide.js": (7_491, 18_597, "7f832a350240263d9946a9c3c877f7bcdab6c37d6dc65f72cd3be5905dca62dd"),
    "pyodide.asm.js": (226_427, 1_074_322, "356c42f69e1695397e9d8670bd3c2e678248cde76e18bdae1731e848537b47d7"),
    "pyodide.asm.wasm": (2_672_398, 8_647_684, "54309a5a2cfd757b1f0fdbc9093c92503f7b341500110329d932487183912718"),
    "python_stdlib.zip": (2_386_522, 2_424_003, "831fd1e535084b972f87c6a35275de3d236efd5166f5e77ce89497710635e73c"),
    "pyodide-lock.json": (26_288, 122_027, "14d2c2dba101277999e17135e653d8f15389ad1437f53eae213bf0c3cdff723d"),
    # micropip (it brings its own copy of packaging)
    "micropip-0.11.1-py3-none-any.whl": (111_109, 115_486,
                                         "a5569ea4002b9cdd6cf50eda0c14fb3fcb0da9d8c702feadcfd04459b4c3040a"),
}
#: The files of the loading screen's two download steps (the wheel joins the second).
_DOWNLOAD_PHASE = ("pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
_INSTALL_PHASE = ("micropip-0.11.1-py3-none-any.whl",)

#: The live page's planner (evroute.server.PlanningService options).
DEFAULT_SCENARIO = "turkey-karaman-istanbul"
#: Time limit of the label-setting planners in the browser [s] (their option
#: ``time_limit_s``): a search still running then stops by itself and is
#: reported like a label limit, well before :data:`TIME_BUDGET_S`.
WEB_TIME_LIMIT_S = 45
#: Limits of the in-browser planner.  The label budgets bound its memory (a label
#: is a few hundred bytes): BLS and BLS-A* count created labels (library default
#: 5 million), CFP and CFP-A* pushed labels (default 3 million); every synthetic
#: preset's own trip stays within them (the largest, country-medium, needs 1.4
#: million BLS labels and 0.8 million CFP pushes).  They do not bound the time:
#: labels that are generated and then dominated are not counted (with the cost
#: objective and eco speeds BLS-A* generates tens of millions of them on
#: Karaman -> Istanbul), so the label-setting planners also get
#: :data:`WEB_TIME_LIMIT_S`.  The Pareto search keeps the viewer's (time, money)
#: front and its budget of 1 million labels (evroute.viz.export.VIEWER_OPTIONS).
#: A planner that hits a limit says so (status "limit" or "time_limit", or an
#: incomplete front), and the page marks the run as possibly incomplete, with the
#: command that repeats it locally.
WEB_OPTIONS: dict[str, dict[str, Any]] = {
    "bls": {"max_labels": 1_500_000, "time_limit_s": WEB_TIME_LIMIT_S},
    "bls-astar": {"max_labels": 1_500_000, "time_limit_s": WEB_TIME_LIMIT_S},
    "cfp": {"max_labels": 1_000_000, "time_limit_s": WEB_TIME_LIMIT_S},
    "cfp-astar": {"max_labels": 1_000_000, "time_limit_s": WEB_TIME_LIMIT_S},
    "pareto": {"criteria": ["time", "money"], "max_labels": 1_000_000, "time_limit_s": WEB_TIME_LIMIT_S},
}
#: Wall-clock budget of one planner in the browser [s]: a planner still running
#: then (one without a time limit of its own: greedy, the station graph, the
#: genetic algorithm) is stopped -- the page restarts the worker -- and reported.
TIME_BUDGET_S = 60
#: Presets with more nodes than this are not offered (as `evroute serve`).
MAX_NODES = 15_000
#: On a network of more than this many nodes the page ticks only BLS-A* and CFP-A* at
#: first (`evroute serve`: 20 000): on country-medium (10 000 nodes) greedy and the
#: station graph take 4 s and 30 s in a browser, BLS-A* and CFP-A* under 3 s.
LARGE_NETWORK_NODES = 5_000
#: Preset families never planned live: the real-data network (44 673 nodes).
EXCLUDE_FAMILIES = ("turkey-osm",)

#: Prebuilt viewer pages: (file, preset, preference overrides).
EXAMPLES: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("karaman-istanbul.html", "turkey-karaman-istanbul", {}),
    ("tariff.html", "turkey-karaman-istanbul-tariff", {"objective": "cost", "charge_step_soc": 0.05}),
    ("osm-karaman-istanbul.html", "turkey-osm-karaman-istanbul", {}),
)
#: Language of the viewer pages when neither a link (?lang=tr|en), a saved choice nor
#: the browser's language chooses one.
PAGE_LANG = "en"
#: What the site's viewer pages (live planner, examples) add to their payload: the way
#: back to the site and the repository, and the browser's language as a default.
SITE_LINKS: dict[str, Any] = {"site": {"home": "../", "repo": REPO}, "lang_auto": True}


# ====================================================================== helpers
def log(msg: str) -> None:
    print(msg, flush=True)


def render(template: str, partials: dict[str, str], values: dict[str, str]) -> str:
    """``{{> partial}}`` and ``{{key}}`` substitution; an unknown name is an error."""
    text = re.sub(r"\{\{>\s*([a-z_]+)\s*\}\}", lambda m: partials[m.group(1)], template)
    out = re.sub(r"\{\{([a-z_]+)\}\}", lambda m: values[m.group(1)], text)
    left = re.findall(r"\{\{\s*>?\s*[a-z_]+\s*\}\}", out)
    if left:
        raise ValueError(f"unresolved template names: {left}")
    return out


def mb(n: float, lang: str) -> str:
    """``6.1 MB`` (en) / ``6,1 MB`` (tr)."""
    s = f"{n / 1e6:.1f}"
    return (s.replace(".", ",") if lang == "tr" else s) + " MB"


def both_mb(n: float) -> str:
    return f'<span lang="tr">{mb(n, "tr")}</span><span lang="en">{mb(n, "en")}</span>'


def short_hash(data: bytes, n: int = 12) -> str:
    return hashlib.sha256(data).hexdigest()[:n]


# ====================================================================== wheel
def build_wheel(work: Path) -> Path:
    """Build the evroute wheel from a clean copy of the repository's sources."""
    src = work / "src-copy"
    shutil.copytree(ROOT / "src", src / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "MANIFEST.in"):
        shutil.copy2(ROOT / name, src / name)
    out = work / "dist"
    try:
        import build  # noqa: F401  (python -m build)

        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(out), str(src)]
    except ImportError:
        cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(out), str(src)]
    log(f"  {' '.join(cmd[1:4])} ...")
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if res.returncode != 0:
        sys.stderr.write(res.stdout[-4000:] + res.stderr[-4000:])
        raise RuntimeError(f"building the wheel failed ({' '.join(cmd[1:4])}, exit code {res.returncode})")
    wheels = sorted(out.glob("evroute-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel in {out}, found {wheels}")
    return wheels[0]


#: Wheel members the live page never needs: the tables of the real-data network (ODbL data,
#: 1.5 MB).  Its small meta.json and prices.json stay (evroute.generators.osm_turkey reads
#: meta.json on import).
WEB_WHEEL_EXCLUDE = tuple(f"evroute/data/osm_turkey/{t}.csv.gz" for t in ("nodes", "edges", "places", "stations"))


#: Time stamp of every member of the web wheel (the zip format's earliest date): the
#: wheel's bytes depend on its content only, not on when or where it was built, so a
#: deploy that does not change the package keeps the wheel's URL (browsers keep their copy).
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _zip_member(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # Unix, so that the mode below means what it says
    info.external_attr = ((mode & 0o777) | 0o100000) << 16  # a regular file
    return info


def web_wheel(wheel: Path) -> bytes:
    """The wheel without :data:`WEB_WHEEL_EXCLUDE`, its RECORD rewritten to match.

    Reproducible: the members are written in name order (the ``.dist-info``
    folder last, its RECORD at the very end), with the time stamp
    :data:`_ZIP_EPOCH`, mode 644 and deflate level 9.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(wheel) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        record_name = next(n for n in zin.namelist() if n.endswith(".dist-info/RECORD"))
        dist_info = record_name.rsplit("/", 1)[0] + "/"
        dropped = set()
        names = []
        for info in zin.infolist():
            if info.is_dir():
                continue
            if info.filename in WEB_WHEEL_EXCLUDE:
                dropped.add(info.filename)
                continue
            if info.filename != record_name:
                names.append(info.filename)
        names.sort(key=lambda n: (n.startswith(dist_info), n))
        for name in names:
            zout.writestr(_zip_member(name, 0o644), zin.read(name), compresslevel=9)
        rows = list(csv.reader(io.StringIO(zin.read(record_name).decode("utf-8"))))
        kept = sorted((r for r in rows if r and r[0] not in dropped and r[0] != record_name),
                      key=lambda r: (r[0].startswith(dist_info), r[0]))
        kept.append([record_name, "", ""])
        text = io.StringIO()
        csv.writer(text, lineterminator="\n").writerows(kept)
        zout.writestr(_zip_member(record_name, 0o644), text.getvalue().encode("utf-8"), compresslevel=9)
    if dropped != set(WEB_WHEEL_EXCLUDE):
        raise RuntimeError(f"{wheel.name} lacks {sorted(set(WEB_WHEEL_EXCLUDE) - dropped)}: is it an evroute wheel?")
    return buf.getvalue()


def check_wheel(data: bytes) -> dict[str, Any]:
    """Every RECORD entry is present with its hash; returns a summary."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        record = next(n for n in names if n.endswith(".dist-info/RECORD"))
        rows = [r for r in csv.reader(io.StringIO(z.read(record).decode("utf-8"))) if r]
        for path, digest, _size in rows:
            if path == record:
                continue
            if path not in names:
                raise RuntimeError(f"RECORD lists {path}, which the wheel does not hold")
            algo, _, want = digest.partition("=")
            got = base64.urlsafe_b64encode(hashlib.new(algo, z.read(path)).digest()).rstrip(b"=").decode()
            if got != want:
                raise RuntimeError(f"hash mismatch for {path}")
        missing = {n for n in names if not n.endswith("/")} - {r[0] for r in rows}
        if missing:
            raise RuntimeError(f"files missing from RECORD: {sorted(missing)[:5]}")
        return {"files": len(rows), "package_files": sum(1 for r in rows if r[0].startswith("evroute/"))}


_SMOKE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import evroute
from evroute.server import PlanningService, api_call_json
assert evroute.__file__.startswith(sys.argv[1]), evroute.__file__
cfg = json.loads(sys.argv[2])
svc = PlanningService(**cfg)
status, text = api_call_json(svc, "scenarios")
names = [s["name"] for s in json.loads(text)["scenarios"]]
for name in names:
    status, text = api_call_json(svc, "scenario", json.dumps({"name": name}))
    assert status == 200, (name, text[:200])
status, text = api_call_json(svc, "plan", json.dumps({"algorithms": ["bls-astar"]}))
assert status == 200 and json.loads(text)["results"][0]["feasible"], text[:300]
print(json.dumps(names))
"""


def smoke_test_wheel(data: bytes, service: dict[str, Any]) -> list[str]:
    """Unpack the web wheel into an empty folder and, in a fresh interpreter that sees nothing
    else of this repository, load every offered preset and plan the default trip."""
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        res = subprocess.run([sys.executable, "-I", "-c", _SMOKE, tmp, json.dumps(service)],
                             check=False, capture_output=True, text=True, cwd=tmp)
    if res.returncode != 0:
        sys.stderr.write(res.stderr[-4000:])
        raise RuntimeError("the web wheel failed its smoke test (see above)")
    return json.loads(res.stdout.strip().splitlines()[-1])


# ====================================================================== pages
def viewer_page(preset: str, prefs: dict[str, Any], quick: bool) -> tuple[str, list[str]]:
    """A prebuilt viewer page (the CLI's ``evroute viz``) and the planners on it."""
    from evroute.algorithms import available
    from evroute.cli import LARGE_NETWORK_NODES, SLOW_ON_LARGE
    from evroute.generators.presets import get_scenario
    from evroute.viz.export import render_viewer_html, run_all, viewer_payload

    sc = get_scenario(preset)
    if prefs:
        sc = sc.with_preferences(**prefs)
    large = sc.network.num_nodes > LARGE_NETWORK_NODES
    if quick:
        algos = ["naive"] if large else ["greedy", "bls-astar"]
    else:  # what `evroute viz` runs by default ("all"; on a large network without the slow ones)
        algos = [a for a in available() if not (large and a in SLOW_ON_LARGE)]
    plans = run_all(sc, algos, trace=True)
    payload = viewer_payload(sc, plans, lang=PAGE_LANG, robustness_runs=0 if quick else 200, extra=SITE_LINKS)
    return render_viewer_html(payload), [p.algorithm for p in plans]


def worker_config(wheel_path: str, wheel_size: int, worker_hash: str) -> dict[str, Any]:
    """The ``worker`` block of the live page's payload (read by live.js and site/worker.js)."""
    transferred = sum(f[0] for f in PYODIDE_FILES.values())
    return {
        "script": f"worker.js?v={worker_hash}",
        "pyodide": PYODIDE_URL,
        "pyodide_version": PYODIDE_VERSION,
        "wheel": wheel_path,
        "service": {
            "default_scenario": DEFAULT_SCENARIO,
            "lang": PAGE_LANG,
            "max_nodes": MAX_NODES,
            "robustness_runs": 200,
            "options": WEB_OPTIONS,
            "exclude_families": list(EXCLUDE_FAMILIES),
            "reuse_last": True,
            "large_network_nodes": LARGE_NETWORK_NODES,
        },
        "time_budget_s": TIME_BUDGET_S,
        "time_limit_s": WEB_TIME_LIMIT_S,
        # what a first visit downloads once (bytes transferred) and, per step of the loading
        # screen, the decoded bytes the worker counts (sizes) and the bytes transferred for them
        "download_bytes": transferred + wheel_size,
        "sizes": {
            "download": sum(PYODIDE_FILES[f][1] for f in _DOWNLOAD_PHASE),
            "install": sum(PYODIDE_FILES[f][1] for f in _INSTALL_PHASE) + wheel_size,
        },
        "transfer": {
            "download": sum(PYODIDE_FILES[f][0] for f in _DOWNLOAD_PHASE),
            "install": sum(PYODIDE_FILES[f][0] for f in _INSTALL_PHASE) + wheel_size,
        },
        "integrity": {name: f[2] for name, f in PYODIDE_FILES.items()},
        "install": f"pip install git+{REPO}",
    }


def live_page(cfg: dict[str, Any]) -> tuple[str, list[str]]:
    """The live viewer page -- the default preset's payload as `evroute serve` sends it, plus the
    worker block -- and the names of the presets the page offers."""
    from evroute.server import PlanningService
    from evroute.viz.export import render_viewer_html

    service = PlanningService(**cfg["service"])
    payload = service.scenario_payload(None)
    payload["worker"] = cfg
    payload.update(json.loads(json.dumps(SITE_LINKS)))
    return render_viewer_html(payload), [p["name"] for p in service.preset_names()]


# ====================================================================== main
def build_site(out: Path, *, wheel: Path | None = None, quick: bool = False) -> dict[str, Any]:
    """Write the site into ``out`` (emptied first) and return the manifest (also ``site.json``)."""
    import evroute

    if not Path(evroute.__file__).resolve().is_relative_to(ROOT / "src"):
        raise RuntimeError(f"evroute is imported from {evroute.__file__}, not from {ROOT / 'src'}")
    t0 = time.perf_counter()
    if out.exists():
        if not out.is_dir() or (any(out.iterdir()) and not (out / "site.json").exists()):
            raise SystemExit(f"error: {out} exists and is not an earlier site build; choose another folder")
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    (out / "live").mkdir()
    (out / "examples").mkdir()
    version = evroute.__version__

    # ---------------------------------------------------------------- wheel
    log("wheel")
    with tempfile.TemporaryDirectory() as tmp:
        src_wheel = wheel if wheel is not None else build_wheel(Path(tmp))
        full_size = src_wheel.stat().st_size
        data = web_wheel(src_wheel)
        wheel_name = src_wheel.name
    if not wheel_name.startswith(f"evroute-{version}-"):
        raise RuntimeError(f"{wheel_name} is not the wheel of evroute {version} (the source tree)")
    wheel_info = check_wheel(data)
    wheel_rel = f"py/{short_hash(data)}/{wheel_name}"
    (out / "live" / wheel_rel).parent.mkdir(parents=True)
    (out / "live" / wheel_rel).write_bytes(data)
    log(f"  {wheel_name}: {full_size / 1e6:.2f} MB -> {len(data) / 1e6:.2f} MB without the real-data network")

    # ----------------------------------------------------------------- live
    log("live planner")
    worker_js = (SITE_SRC / "worker.js").read_bytes()
    (out / "live" / "worker.js").write_bytes(worker_js)
    cfg = worker_config(wheel_rel, len(data), short_hash(worker_js, 10))
    html, offered = live_page(cfg)
    (out / "live" / "index.html").write_text(html, encoding="utf-8")
    log(f"  {len(offered)} presets: {', '.join(offered)}")
    if smoke_test_wheel(data, cfg["service"]) != offered:
        raise RuntimeError("the web wheel offers other presets than the source tree")
    log("  the web wheel loads every offered preset and plans the default trip")

    # ------------------------------------------------------------- examples
    log("examples")
    examples: dict[str, Any] = {}
    for name, preset, prefs in EXAMPLES:
        t = time.perf_counter()
        page, algos = viewer_page(preset, prefs, quick)
        (out / "examples" / name).write_text(page, encoding="utf-8")
        examples[name] = {"preset": preset, "preferences": prefs, "algorithms": algos,
                          "bytes": len(page.encode("utf-8"))}
        log(f"  {name}: {', '.join(algos)} ({time.perf_counter() - t:.1f} s)")
    results = ROOT / "docs" / "demo" / "results.html"
    shutil.copy2(results, out / "examples" / "results.html")
    examples["results.html"] = {"source": "docs/demo/results.html", "bytes": results.stat().st_size}

    # ---------------------------------------------------------------- pages
    log("landing page")
    shutil.copy2(ROOT / "src" / "evroute" / "viz" / "web" / "css" / "theme.css", out / "assets" / "theme.css")
    for name in ("site.css", "site.js"):
        shutil.copy2(SITE_SRC / name, out / "assets" / name)
    shutil.copy2(ROOT / "docs" / "img" / "demo.gif", out / "assets" / "demo.gif")
    partials = {p.stem: p.read_text(encoding="utf-8").rstrip("\n") for p in (SITE_SRC / "partials").glob("*.html")}
    notes = ROOT / "docs" / f"release-notes-v{version}.md"
    # the release notes of this version until it is published as a GitHub release
    releases = f"{REPO}/blob/main/docs/{notes.name}" if notes.is_file() else f"{REPO}/releases"
    common = {"version": version, "repo": REPO, "pyodide": PYODIDE_VERSION, "releases": releases}
    landing = render(SITE_SRC.joinpath("index.html").read_text(encoding="utf-8"), partials, {
        **common, "root": "",
        "download_mb_en": mb(cfg["download_bytes"], "en"), "download_mb_tr": mb(cfg["download_bytes"], "tr"),
    })
    (out / "index.html").write_text(landing, encoding="utf-8")
    ex_page = render(SITE_SRC.joinpath("examples.html").read_text(encoding="utf-8"), partials, {
        **common, "root": "../",
        "size_karaman": both_mb(examples["karaman-istanbul.html"]["bytes"]),
        "size_tariff": both_mb(examples["tariff.html"]["bytes"]),
        "size_osm": both_mb(examples["osm-karaman-istanbul.html"]["bytes"]),
        "size_results": both_mb(examples["results.html"]["bytes"]),
    })
    (out / "examples" / "index.html").write_text(ex_page, encoding="utf-8")

    manifest = {
        "site": SITE_URL,
        "evroute": version,
        "quick": quick,
        "pyodide": {"version": PYODIDE_VERSION, "url": PYODIDE_URL,
                    "files": {k: {"transferred": t, "decoded": d, "sha256": h}
                              for k, (t, d, h) in PYODIDE_FILES.items()}},
        "wheel": {"path": f"live/{wheel_rel}", "bytes": len(data), "full_bytes": full_size,
                  "excluded": list(WEB_WHEEL_EXCLUDE), **wheel_info},
        "live": {"default_scenario": DEFAULT_SCENARIO, "presets": offered, "options": WEB_OPTIONS,
                 "time_budget_s": TIME_BUDGET_S, "time_limit_s": WEB_TIME_LIMIT_S,
                 "download_bytes": cfg["download_bytes"],
                 "page_bytes": len(html.encode("utf-8")),
                 "page_gzip_bytes": len(gzip.compress(html.encode("utf-8"), 6))},
        "examples": examples,
        "build_s": round(time.perf_counter() - t0, 1),
    }
    (out / "site.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"wrote {out} in {manifest['build_s']} s; first visit of the live page downloads "
        f"{mb(cfg['download_bytes'], 'en')} once")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the EVRoute web site (GitHub Pages).")
    ap.add_argument("-o", "--out", default="_site", help="output folder (default: _site)")
    ap.add_argument("--wheel", type=Path, help="use this evroute wheel instead of building one")
    ap.add_argument("--quick", action="store_true", help="examples with one or two planners (fast)")
    args = ap.parse_args(argv)
    build_site(Path(args.out), wheel=args.wheel, quick=args.quick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
