"""Tests of the project web site (``tools/build_site.py``) and of the in-browser planner's API.

The live page plans in the visitor's browser: a Web Worker runs EVRoute under
Pyodide and answers the viewer with :func:`evroute.server.api_call_json`.
These tests check, in CPython, that

* the generator writes the expected files and links, with no external URL in
  the live app but the pinned Pyodide CDN, and pages whose JavaScript parses;
* the worker's API -- the same function, configured as the site configures
  it -- returns exactly what ``evroute plan`` returns for the request the
  page sends, also when the page asks one algorithm at a time.

The browser half (Pyodide in Chromium, the same plans as the command line) is
checked with Playwright outside the unit tests.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

import evroute
from evroute.cli import main as cli_main
from evroute.server import API_ENDPOINTS, PlanningService, api_call, api_call_json

ROOT = Path(__file__).resolve().parent.parent


if not all((ROOT / p).exists() for p in ("tools/build_site.py", "site/worker.js", "docs/demo/results.html")):
    pytest.skip("the web site sources are in the repository, not in the source distribution",
                allow_module_level=True)


def _load_build_site():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "tools" / "build_site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bs = _load_build_site()

pytestmark = pytest.mark.skipif(
    not Path(evroute.__file__).resolve().is_relative_to(ROOT / "src"),
    reason="the site is built from the repository's src/ folder",
)


# ------------------------------------------------------------------ fixtures
def _make_wheel(dest: Path) -> Path:
    """A wheel of src/evroute made with the standard library (no build tools, no network)."""
    v = evroute.__version__
    dist = f"evroute-{v}.dist-info"
    files: dict[str, bytes] = {}
    pkg = ROOT / "src" / "evroute"
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            files["evroute/" + p.relative_to(pkg).as_posix()] = p.read_bytes()
    files[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: evroute\nVersion: {v}\n".encode()
    files[f"{dist}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    record = io.StringIO()
    w = csv.writer(record, lineterminator="\n")
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        w.writerow([name, f"sha256={digest}", len(data)])
    w.writerow([f"{dist}/RECORD", "", ""])
    path = dest / f"evroute-{v}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        z.writestr(f"{dist}/RECORD", record.getvalue())
    return path


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("site")
    wheel = _make_wheel(tmp)
    out = tmp / "_site"
    manifest = bs.build_site(out, wheel=wheel, quick=True)
    return out, manifest


@pytest.fixture(scope="module")
def live_payload(site):
    out, _ = site
    return _payload((out / "live" / "index.html").read_text(encoding="utf-8"))


def _payload(html: str) -> dict:
    m = re.search(r'<script id="evroute-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "data block missing"
    return json.loads(m.group(1))


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self.scripts: list[str] = []
        self._in_script = False
        self._script_type = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("href", "src"):
            if a.get(k) is not None:
                self.links.append((tag, k, a[k]))
        if tag == "script":
            self._in_script = True
            self._script_type = a.get("type")
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
            if self._script_type == "application/json":
                self.scripts.pop()

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1] += data


def _links(path: Path) -> _Links:
    p = _Links()
    p.feed(path.read_text(encoding="utf-8"))
    return p


# ------------------------------------------------------------------ the generator
def test_site_files(site):
    out, manifest = site
    for rel in ("index.html", "assets/theme.css", "assets/site.css", "assets/site.js", "assets/demo.gif",
                "live/index.html", "live/worker.js", "examples/index.html", "examples/karaman-istanbul.html",
                "examples/tariff.html", "examples/osm-karaman-istanbul.html", "examples/results.html", "site.json"):
        assert (out / rel).is_file(), rel
    # the viewer's design tokens, the committed dashboard, the worker: copied unchanged
    assert (out / "assets/theme.css").read_bytes() == (ROOT / "src/evroute/viz/web/css/theme.css").read_bytes()
    assert (out / "examples/results.html").read_bytes() == (ROOT / "docs/demo/results.html").read_bytes()
    assert (out / "live/worker.js").read_bytes() == (ROOT / "site/worker.js").read_bytes()
    assert json.loads((out / "site.json").read_text(encoding="utf-8")) == json.loads(json.dumps(manifest))
    assert manifest["evroute"] == evroute.__version__ and manifest["pyodide"]["version"] == bs.PYODIDE_VERSION
    assert re.fullmatch(r"\d+\.\d+\.\d+", bs.PYODIDE_VERSION)
    assert bs.PYODIDE_URL == f"https://cdn.jsdelivr.net/pyodide/v{bs.PYODIDE_VERSION}/full/"


def test_web_wheel(site):
    out, manifest = site
    wheel = out / manifest["wheel"]["path"]
    assert wheel.is_file() and wheel.name == f"evroute-{evroute.__version__}-py3-none-any.whl"
    data = wheel.read_bytes()
    assert wheel.parent.name == bs.short_hash(data)  # the folder name changes with the content
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
    assert not names & set(bs.WEB_WHEEL_EXCLUDE)  # the real-data tables are left out ...
    assert "evroute/data/osm_turkey/meta.json" in names and "evroute/server.py" in names  # ... nothing else
    assert bs.check_wheel(data)["package_files"] > 50  # RECORD matches the content


def test_landing_page_links(site):
    out, manifest = site
    page = _links(out / "index.html")
    html = (out / "index.html").read_text(encoding="utf-8")
    hrefs = [v for _, _, v in page.links]
    # no external scripts, stylesheets or images: everything is served by the site
    for tag, attr, value in page.links:
        if tag in ("script", "link", "img"):
            assert not re.match(r"(https?:)?//", value), (tag, value)
    for rel in ("live/", "examples/", "examples/karaman-istanbul.html", "examples/tariff.html",
                "examples/osm-karaman-istanbul.html", "examples/results.html", "assets/demo.gif"):
        assert rel in hrefs, rel
        assert (out / rel).exists()
    repo = bs.REPO
    for doc in ("#readme", "/blob/main/README.tr.md", "/blob/main/docs/guide.md", "/blob/main/docs/guide.tr.md",
                "/blob/main/docs/real-data.md", "/blob/main/docs/real-data.tr.md", "/blob/main/docs/model.md",
                "/blob/main/docs/algorithms.md", "/releases", "/blob/main/CITATION.cff"):
        assert repo + doc in hrefs, doc
    for doc in ("guide.md", "guide.tr.md", "real-data.md", "real-data.tr.md", "model.md", "algorithms.md"):
        assert (ROOT / "docs" / doc).is_file()
    # both languages (<html lang> is the extra "en"), the citation and the download size
    n_tr, n_en = (len(re.findall(rf'\slang="{lang}"', html)) for lang in ("tr", "en"))
    assert n_tr > 40 and n_en - n_tr == 1
    assert "@software{acik_evroute_2026" in html and f"version = {{{evroute.__version__}}}" in html
    assert "{{" not in html.replace("{{EVRoute}", "")
    assert bs.mb(manifest["live"]["download_bytes"], "en") in html and bs.mb(manifest["live"]["download_bytes"], "tr") in html


def test_examples_page_and_osm_credit(site):
    out, _ = site
    page = _links(out / "examples" / "index.html")
    for name in ("karaman-istanbul.html", "tariff.html", "osm-karaman-istanbul.html", "results.html"):
        assert name in [v for _, _, v in page.links]
    ex = (out / "examples" / "index.html").read_text(encoding="utf-8")
    assert "ODbL 1.0" in ex and "© OpenStreetMap" in ex
    osm = _payload((out / "examples" / "osm-karaman-istanbul.html").read_text(encoding="utf-8"))
    meta = osm["scenario"]["meta"]
    assert meta["attribution"] == "© OpenStreetMap contributors"  # drawn on the map and in the scenario card
    assert meta["licence"].startswith("ODbL 1.0")  # the licence note of the scenario card
    tariff = _payload((out / "examples" / "tariff.html").read_text(encoding="utf-8"))
    assert tariff["scenario"]["preferences"]["objective"] == "cost" and tariff["scenario"]["tariff"]


def test_live_page_config(site, live_payload):
    out, manifest = site
    p = live_payload
    assert p["live"] is True and p["scenario_preset"] == bs.DEFAULT_SCENARIO and p["results"] == []
    cfg = p["worker"]
    assert cfg["pyodide"] == bs.PYODIDE_URL and cfg["pyodide_version"] == bs.PYODIDE_VERSION
    assert (out / "live" / cfg["wheel"]).is_file() and cfg["script"].startswith("worker.js?v=")
    svc = cfg["service"]
    assert svc["options"] == bs.WEB_OPTIONS and svc["exclude_families"] == ["turkey-osm"]
    assert cfg["time_budget_s"] == bs.TIME_BUDGET_S > 0
    assert cfg["download_bytes"] == sum(t for t, _ in bs.PYODIDE_FILES.values()) + manifest["wheel"]["bytes"]
    # only synthetic presets: never the real-data network
    offered = manifest["live"]["presets"]
    assert bs.DEFAULT_SCENARIO in offered and "corridor" in offered and "country-small" in offered
    assert not [n for n in offered if n.startswith("turkey-osm")]
    assert offered == [s["name"] for s in json.loads(api_call_json(PlanningService(**svc), "scenarios")[1])["scenarios"]]


_URL = re.compile(r"""https?://[^\s"'<>`)\\]+""")


def test_live_app_has_no_external_url_but_pyodide(site):
    """The live app loads nothing from outside the site but the pinned Pyodide files.

    The only other URLs are the SVG namespace (an identifier, never fetched)
    and the repository in the install hint (shown to the reader)."""
    out, _ = site
    allowed_text = {"http://www.w3.org/2000/svg", f"git+{bs.REPO}"}
    for rel in ("live/index.html", "live/worker.js"):
        text = (out / rel).read_text(encoding="utf-8")
        for url in _URL.findall(text):
            if url.startswith(bs.PYODIDE_URL):
                continue
            assert url in allowed_text or url == bs.REPO, (rel, url)
    worker = (out / "live" / "worker.js").read_text(encoding="utf-8")
    assert not _URL.findall(worker)  # the worker gets the CDN URL from the page
    assert "importScripts(`${cfg.pyodide}pyodide.js`)" in worker
    for tag, attr, value in _links(out / "live" / "index.html").links:  # nothing loaded by markup either
        assert value.startswith(("data:", "#")), (tag, attr, value)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_site_javascript_parses(site, tmp_path):
    out, _ = site
    files = [out / "live" / "worker.js", out / "assets" / "site.js"]
    for page in ("live/index.html", "index.html", "examples/index.html"):
        for i, js in enumerate(_links(out / page).scripts):
            if js.strip():
                f = tmp_path / f"{page.replace('/', '_')}_{i}.js"
                f.write_text(js, encoding="utf-8")
                files.append(f)
    assert len(files) >= 5
    for f in files:
        res = subprocess.run(["node", "--check", str(f)], check=False, capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, f"{f.name}: {res.stderr}"


def test_build_refuses_a_foreign_folder(tmp_path):
    (tmp_path / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        bs.build_site(tmp_path, wheel=tmp_path / "missing.whl", quick=True)
    assert (tmp_path / "keep.txt").exists()


# ------------------------------------------------------------------ the worker's API
def _service() -> PlanningService:
    """The planner of the live page, configured as the site configures it."""
    return PlanningService(**json.loads(json.dumps(bs.worker_config("w.whl", 0, "h")["service"])))


def _page_request(payload: dict, algorithms: list[str]) -> dict:
    """The body live.js (Live.request) sends for a payload's trip with its controls untouched."""
    sc = payload["scenario"]
    q, prefs = sc["query"], sc["preferences"]
    dep = q["departure_s"]
    body = {
        "scenario": payload["scenario_preset"], "origin": q["origin"], "destination": q["destination"],
        "algorithms": algorithms, "vehicle": sc["vehicle"]["key"],
        "initial_soc": round(q["initial_soc"] * 100) / 100,
        "temperature_c": round(sc["conditions"]["temperature_c"]),
        "reserve_soc": round(prefs["reserve_soc"] * 100) / 100,
        "min_arrival_soc": round(prefs["min_arrival_soc"] * 100) / 100,
        "objective": prefs["objective"], "value_of_time_per_h": prefs["value_of_time_per_h"],
        "speed_factors": [1.0, 0.9, 0.8] if len(prefs["speed_factors"]) > 1 else [1.0],
        "queue_aware": prefs["queue_aware"], "departure_s": int(dep // 3600) * 3600 + int(dep % 3600 // 60) * 60,
    }
    if not payload["scenario"].get("tariff"):
        body["tariff"] = "flat"  # the tariff switch is shown (demonstration tariff) and off
    return body


#: Fields of a plan that must agree with the command line (to_dict of the same RoutePlan).
_PLAN_FIELDS = ("algorithm", "feasible", "total_time_s", "drive_time_s", "charge_time_s", "wait_time_s",
                "setup_time_s", "hold_time_s", "distance_m", "money", "soh_drive_pct", "soh_charge_pct",
                "arrival_soc", "objective_value", "generalized_cost", "nodes", "edges", "levels",
                "node_arrive_s", "node_energy_arrive")
_STOP_FIELDS = ("station", "position", "arrival_s", "hold_s", "wait_s", "charge_s", "energy_in_kwh",
                "energy_out_kwh", "money", "peak")


def _cli_plan(preset: str, algo: str, tmp_path: Path, capsys) -> dict:
    path = tmp_path / f"{preset}-{algo}.json"
    rc = cli_main(["plan", preset, "-a", algo, "--json", str(path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    d = json.loads(path.read_text(encoding="utf-8"))
    d["_line"] = out.splitlines()[0]
    return d


@pytest.mark.parametrize(
    "preset, algos",
    [
        ("turkey-karaman-istanbul", ["bls-astar", "cfp-astar"]),
        ("turkey-karaman-istanbul-tariff", ["greedy", "bls-astar"]),
        ("corridor", ["station-graph", "bls", "cfp"]),
    ],
)
def test_worker_api_returns_what_the_cli_returns(preset, algos, tmp_path, capsys):
    svc = _service()
    status, text = api_call_json(svc, "scenario", json.dumps({"name": preset}))
    assert status == 200
    payload = json.loads(text)
    results = {}
    for a in algos:  # one algorithm per request, as the page asks
        status, text = api_call_json(svc, "plan", json.dumps(_page_request(payload, [a])))
        assert status == 200, text[:300]
        data = json.loads(text)
        assert data["errors"] == [] and [r["algorithm"] for r in data["results"]] == [a]
        results[a] = data["results"][0]
    for a in algos:
        cli = _cli_plan(preset, a, tmp_path, capsys)
        web = results[a]
        for k in _PLAN_FIELDS:
            assert web[k] == cli[k], (a, k)
        assert len(web["stops"]) == len(cli["stops"])
        for ws, cs in zip(web["stops"], cli["stops"]):
            for k in _STOP_FIELDS:
                assert ws[k] == cs[k], (a, k)
        # the trip time the page shows is the one the command line prints
        assert web["summary"]["total_time"] in cli["_line"] and web["feasible"]


def test_one_algorithm_per_request_equals_one_request():
    """The page plans one algorithm per request (progress, time budget); the merged answer
    equals one request with all of them (as `evroute serve` answers it)."""
    svc = _service()
    payload = json.loads(api_call_json(svc, "scenario", json.dumps({"name": "grid-small"}))[1])
    algos = ["naive", "greedy", "genetic", "bls-astar", "cfp-astar", "pareto"]
    whole = json.loads(api_call_json(svc, "plan", json.dumps(_page_request(payload, algos)))[1])
    parts = [json.loads(api_call_json(svc, "plan", json.dumps(_page_request(payload, [a])))[1]) for a in algos]

    def strip(r):
        r = dict(r)
        stats = dict(r.pop("stats"))
        stats.pop("runtime_s", None)
        extra = {k: v for k, v in (stats.pop("extra", None) or {}).items() if not k.endswith("_s")}
        r["summary"] = {k: v for k, v in r["summary"].items() if k != "runtime_ms"}  # timings differ, nothing else
        return r, stats, extra

    assert [strip(r) for r in whole["results"]] == [strip(p["results"][0]) for p in parts]
    for p in parts:
        assert p["scenario"] == whole["scenario"] and p["wait_profiles"] == whole["wait_profiles"]


def test_web_budgets_are_reported():
    """A planner stopped by a web label budget says so (the page marks it possibly incomplete)."""
    svc = PlanningService("corridor", options={"bls": {"max_labels": 50}, "pareto": {"criteria": ["time", "money"],
                                                                                      "max_labels": 2000}})
    payload = json.loads(api_call_json(svc, "scenario", "{}")[1])
    data = json.loads(api_call_json(svc, "plan", json.dumps(_page_request(payload, ["bls", "pareto"])))[1])
    bls, pareto = data["results"]
    assert bls["stats"]["extra"]["status"] == "limit" and not bls["feasible"] and "label limit" in bls["message"]
    assert bls["options"] == {"max_labels": 50}
    assert pareto["complete"] is False and pareto["options"]["criteria"] == ["time", "money"]


def test_web_defaults_on_the_largest_preset():
    """country-medium (10 000 nodes): the page ticks only BLS-A* and CFP-A* at first; the server keeps its defaults."""
    from evroute.server import DEFAULT_ALGORITHMS, LARGE_NETWORK_ALGORITHMS

    web = _service().scenario_payload("country-medium")["default_algorithms"]
    assert web == list(LARGE_NETWORK_ALGORITHMS)
    assert _service().scenario_payload("country-small")["default_algorithms"] == list(DEFAULT_ALGORITHMS)
    assert PlanningService("country-medium").scenario_payload(None)["default_algorithms"] == list(DEFAULT_ALGORITHMS)


def test_api_call_status_codes():
    svc = PlanningService("grid-small", robustness_runs=0)
    assert API_ENDPOINTS == ("health", "scenarios", "scenario", "plan")
    assert api_call(svc, "health") == (200, {"ok": True, "version": evroute.__version__})
    assert api_call(svc, "nothing")[0] == 404
    assert api_call(svc, "scenario", {"name": "no-such-preset"})[0] == 400
    assert api_call(svc, "plan", [1, 2])[0] == 400
    status, body = api_call(svc, "plan", {"initial_soc": 7})
    assert status == 400 and "initial_soc" in body["error"]
    status, text = api_call_json(svc, "plan", "{not json")
    assert status == 400 and "invalid JSON" in json.loads(text)["error"]
    status, text = api_call_json(svc, "plan", "")  # an empty body is {} (as the server reads it)
    assert status == 200 and json.loads(text)["results"]
    # the real-data presets are offered only where nobody excluded them
    web = PlanningService("turkey-karaman-istanbul", exclude_families=["turkey-osm"], max_nodes=10 ** 6)
    assert not [p for p in web.preset_names() if p["family"] == "turkey-osm"]
    assert api_call(web, "scenario", {"name": "turkey-osm-karaman-konya"})[0] == 400
