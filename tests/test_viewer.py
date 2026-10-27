"""Tests of the web viewer: payload contract, single-file export, live server.

The browser side (rendering, animation, interaction) is verified with
Playwright outside the unit tests (see ``examples/viewer_demo.py``); here we
check everything the page relies on: the ``evroute-viewer/1`` payload, that
the exported HTML is self-contained and its embedded JSON round-trips, and
the HTTP endpoints of :mod:`evroute.server`.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from html.parser import HTMLParser

import pytest

from evroute.algorithms import REGISTRY
from evroute.generators import grid_scenario
from evroute.server import MAX_BODY_BYTES, PlanRequestError, build_plan_scenario, make_server
from evroute.viz import export
from evroute.viz.export import (
    ALGORITHM_COLORS,
    CSS_FILES,
    FORMAT,
    JS_FILES,
    WEB_DIR,
    algorithm_color,
    build_viewer_html,
    dumps_for_html,
    render_viewer_html,
    run_all,
    viewer_payload,
)


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def scenario():
    return grid_scenario(rows=6, cols=6, seed=1, initial_soc=0.3)


@pytest.fixture(scope="module")
def plans(scenario):
    return run_all(scenario)


@pytest.fixture(scope="module")
def payload(scenario, plans):
    return viewer_payload(scenario, plans, title="Test trip", lang="en", animate=False)


def _data_block(html: str) -> str:
    m = re.search(r'<script id="evroute-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "data block missing"
    return m.group(1)


class _Refs(HTMLParser):
    """Collects src/href attributes of script, link, img, iframe, ... tags."""

    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str, str]] = []
        self.scripts = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts += 1
        for k, v in attrs:
            if k in ("src", "href") and v is not None:
                self.refs.append((tag, k, v))


# ------------------------------------------------------------------ run_all
def test_run_all_keeps_registry_order_and_skips_failures(scenario, monkeypatch):
    monkeypatch.setitem(REGISTRY, "broken", ("evroute.algorithms.does_not_exist", "Nope"))
    errors = []
    wanted = ["bls-astar", "broken", "greedy", "unknown-name"]
    got = run_all(scenario, wanted, on_error=lambda name, exc: errors.append(name))
    assert [p.algorithm for p in got] == ["greedy", "bls-astar"]  # registry order, not request order
    assert sorted(errors) == ["broken", "unknown-name"]


def test_run_all_covers_every_algorithm(plans):
    assert [p.algorithm for p in plans] == list(REGISTRY)
    assert any(p.alternatives for p in plans if p.algorithm == "pareto")


# ------------------------------------------------------------------ payload
def test_payload_contract(payload, scenario, plans):
    assert payload["format"] == FORMAT == "evroute-viewer/1"
    assert payload["title"] == "Test trip" and payload["lang"] == "en"
    assert payload["animate"] is False and payload["live"] is False
    sc = payload["scenario"]
    assert sc["name"] == scenario.name and sc["currency"] == scenario.currency
    for k in ("key", "name", "battery_kwh", "capacity_kwh", "dc_peak_kw"):
        assert k in sc["vehicle"]
    assert sc["vehicle"]["capacity_kwh"] == pytest.approx(scenario.prepare().capacity, abs=1e-3)
    for k in ("origin", "destination", "initial_soc", "departure_s"):
        assert sc["query"][k] == getattr(scenario.query, k)
    assert sc["preferences"]["objective"] == "time" and "temperature_c" in sc["conditions"]

    net = payload["network"]
    n, m = scenario.network.num_nodes, scenario.network.num_edges
    for k in ("x", "y", "elevation"):
        assert len(net[k]) == n
    for k in ("tail", "head", "road_class", "speed_kmh", "twin", "length"):
        assert len(net[k]) == m
    assert isinstance(net["node_name"], dict)

    P = scenario.prepare()
    assert len(payload["stations"]) == len(P.stations)
    for i, st in enumerate(payload["stations"]):
        assert st["node"] == P.stations[i].node and st["kind"] in ("AC", "DC")
        assert st["wait_profile"] == P.wait_profiles[i].to_dict()

    names = [a["name"] for a in payload["algorithms"]]
    assert names == list(REGISTRY)
    for a in payload["algorithms"]:
        assert a["exact"] in ("exact", "grid", "heuristic") and a["color"].startswith("#")
    assert {v["key"] for v in payload["vehicles"]} >= {scenario.vehicle.key}

    assert [r["algorithm"] for r in payload["results"]] == [p.algorithm for p in plans]
    for r, p in zip(payload["results"], plans):
        assert r["color"] == algorithm_color(p.algorithm)[0] and r["color_dark"] == algorithm_color(p.algorithm)[1]
        assert r["title"] and r["exact"] in ("exact", "grid", "heuristic")
        assert r["feasible"] == p.feasible
        L = len(r["nodes"])
        for k in ("node_arrive_s", "node_depart_s", "node_energy_arrive", "node_energy_depart", "node_dist_m"):
            assert len(r[k]) == L
        assert r["summary"]["algorithm"] == p.algorithm
        if p.trace is not None:
            assert len(r["trace"]["nodes"]) == len(r["trace"]["keys"]) > 0


def test_payload_is_strict_json(payload):
    text = json.dumps(payload, allow_nan=False)  # raises on NaN/inf
    assert json.loads(text)["format"] == FORMAT


def test_stop_curves_are_exact_charging_trajectories(payload, scenario):
    P = scenario.prepare()
    seen = 0
    for r in payload["results"]:
        for st in r["stops"]:
            c = st["curve"]
            assert len(c["t"]) == len(c["e"]) >= 1
            assert c["t"] == sorted(c["t"]) and c["e"] == sorted(c["e"])
            assert c["e"][0] == pytest.approx(st["energy_in_kwh"], abs=1e-4)
            assert c["e"][-1] == pytest.approx(st["energy_out_kwh"], abs=1e-4)
            assert c["t"][-1] == pytest.approx(st["charge_s"], abs=0.02)
            f = P.cf[st["station"]]
            for t, e in zip(c["t"], c["e"]):  # every breakpoint lies on F
                assert f.charge_time(st["energy_in_kwh"], e) == pytest.approx(t, abs=0.02)
            seen += 1
    assert seen > 0


def test_timelines_are_consistent(payload):
    """node_arrive/depart and the stops tile the trip without gaps (what the animation relies on)."""
    for r in payload["results"]:
        if not r["feasible"]:
            continue
        arr, dep = r["node_arrive_s"], r["node_depart_s"]
        stops = {s["position"]: s for s in r["stops"]}
        for i in range(len(arr)):
            dwell = 0.0
            if i in stops:
                s = stops[i]
                assert s["arrival_s"] == pytest.approx(arr[i], abs=0.02)
                dwell = s["wait_s"] + s["setup_s"] + s["charge_s"]
            assert dep[i] == pytest.approx(arr[i] + dwell, abs=0.05)
            if i + 1 < len(arr):
                assert arr[i + 1] >= dep[i] - 1e-6
        assert arr[-1] == pytest.approx(r["total_time_s"], abs=0.05)


def test_pareto_points_and_alternatives(payload):
    par = next(r for r in payload["results"] if r["algorithm"] == "pareto")
    pts = par["pareto_points"]
    assert len(pts) == par["alternatives_total"] >= 2
    times = [p[0] for p in pts]
    assert times == sorted(times)
    # a Pareto front: sorted by time, money never increases
    money = [p[1] for p in pts]
    assert all(b <= a + 1e-6 for a, b in zip(money, money[1:]))
    with_route = [p[3] for p in pts if p[3] >= 0]
    assert sorted(with_route) == list(range(len(par["alternatives"])))
    for k, alt in enumerate(par["alternatives"]):
        assert alt["member"] >= 0 and alt["title"].startswith(par["title"])
        assert pts[alt["member"]][3] == k
        assert alt["total_time_s"] == pytest.approx(pts[alt["member"]][0], abs=0.01)
    assert par["alternative_selected"] in range(-1, len(par["alternatives"]))


def test_reliability_badges(payload):
    for r in payload["results"]:
        rel = r["reliability"]
        if not r["edges"]:
            assert rel is None
            continue
        assert rel["n"] == 200
        for k in ("p_strand", "p_reserve_breach", "p_arrival_short"):
            assert 0.0 <= rel[k] <= 1.0
            lo, hi = rel["ci95"][k]
            assert lo <= rel[k] <= hi
        # a stranding is a reserve breach and an arrival shortfall
        assert rel["p_strand"] <= rel["p_reserve_breach"] + 1e-12
        assert rel["p_strand"] <= rel["p_arrival_short"] + 1e-12
    naive = next(r for r in payload["results"] if r["algorithm"] == "naive")
    if not naive["feasible"] and naive["reliability"]:
        assert naive["reliability"]["p_arrival_short"] > 0.5


def test_trace_cap_and_robustness_switch(scenario, plans):
    p = viewer_payload(scenario, plans, trace_events=5, robustness_runs=0)
    for r in p["results"]:
        if r["trace"]:
            assert len(r["trace"]["nodes"]) <= 5
        assert r["reliability"] is None


def test_colors_stable_distinct_and_valid():
    hexes = re.compile(r"^#[0-9A-Fa-f]{6}$")
    lights = [c[0] for c in ALGORITHM_COLORS.values()]
    darks = [c[1] for c in ALGORITHM_COLORS.values()]
    assert len(set(lights)) == len(lights) and len(set(darks)) == len(darks)
    assert all(hexes.match(c) for c in lights + darks)
    assert set(REGISTRY) <= set(ALGORITHM_COLORS)
    assert algorithm_color("my-new-algorithm") == algorithm_color("my-new-algorithm")
    assert algorithm_color("bls-astar") == ALGORITHM_COLORS["bls-astar"]


def test_lang_validation(scenario):
    with pytest.raises(ValueError):
        viewer_payload(scenario, [], lang="de")


# ------------------------------------------------------------------ HTML
def test_html_is_self_contained(payload):
    html = render_viewer_html(payload)
    refs = _Refs()
    refs.feed(html)
    for tag, attr, value in refs.refs:
        # only the inline favicon (a data: URI) may carry a reference
        assert value.startswith("data:") or value.startswith("#"), (tag, attr, value)
    assert not re.search(r"<script[^>]+src=", html, re.I)
    assert not re.search(r"<link[^>]+stylesheet", html, re.I)
    assert not re.search(r"@import|url\(\s*['\"]?https?:", html, re.I)
    assert "<!-- evroute:scripts -->" not in html and "<!-- evroute:styles -->" not in html
    for name in JS_FILES:
        assert f"// ---- {name} ----" in html
    for name in CSS_FILES:
        assert f"/* ---- {name} ---- */" in html
    assert '<html lang="en">' in html
    assert "<title>Test trip · EVRoute</title>" in html
    assert json.loads(_data_block(html)) == json.loads(json.dumps(payload))


def test_json_escaping_cannot_break_out_of_the_script_tag(scenario, plans):
    evil = '</script><script>alert(1)</script><!-- &    "quoted"'
    net = scenario.network
    sc = scenario.with_(network=type(net).from_dict({**net.to_dict(), "node_name": {"0": evil}}))
    p = viewer_payload(sc, plans[:1], title=evil)
    text = dumps_for_html(p)
    assert "<" not in text and ">" not in text and "&" not in text
    assert " " not in text and " " not in text
    assert json.loads(text) == json.loads(json.dumps(p))
    html = render_viewer_html(p)
    block = _data_block(html)
    assert json.loads(block)["network"]["node_name"]["0"] == evil
    assert "</script><script>alert" not in html
    # the title is HTML-escaped as well
    assert "<title>&lt;/script&gt;" in html


def test_build_viewer_html_writes_file(tmp_path, scenario, plans):
    out = build_viewer_html(scenario, plans, tmp_path / "sub" / "trip.html", lang="tr")
    text = out.read_text(encoding="utf-8")
    assert out.exists() and '<html lang="tr">' in text
    data = json.loads(_data_block(text))
    assert data["animate"] is True and data["lang"] == "tr"


def test_cli_entry_point(tmp_path, scenario):
    path = tmp_path / "s.json"
    scenario.save(path)
    out = tmp_path / "cli.html"
    rc = export.main(["--scenario", str(path), "--out", str(out), "--algorithms", "greedy,bls-astar",
                      "--no-animate", "--lang", "en"])
    assert rc == 0
    data = json.loads(_data_block(out.read_text(encoding="utf-8")))
    assert [r["algorithm"] for r in data["results"]] == ["greedy", "bls-astar"]
    assert data["animate"] is False and data["lang"] == "en"


def test_web_assets_exist_and_are_inlinable():
    for name in CSS_FILES:
        assert "</style" not in (WEB_DIR / "css" / name).read_text(encoding="utf-8").lower()
    for name in JS_FILES:
        js = (WEB_DIR / "js" / name).read_text(encoding="utf-8")
        assert "</script" not in js.lower() and "<!--" not in js
        assert "http://" not in js.replace("http://www.w3.org/2000/svg", "") and "https://" not in js
    tpl = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    for name in JS_FILES:
        assert f'src="js/{name}"' in tpl  # the template loads the same files in the same order
    order = [tpl.index(f'src="js/{name}"') for name in JS_FILES]
    assert order == sorted(order)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_javascript_parses():
    for name in JS_FILES:
        res = subprocess.run(["node", "--check", str(WEB_DIR / "js" / name)], capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, f"{name}: {res.stderr}"


_JS_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const [webDir, dataPath] = process.argv.slice(2);
const ctx = { console, Math, JSON, Float64Array, Float32Array, Int32Array, Map, Set, Number, Intl, isFinite };
ctx.window = ctx;
vm.createContext(ctx);
for (const f of ['util.js', 'i18n.js', 'model.js']) vm.runInContext(fs.readFileSync(webDir + '/js/' + f, 'utf8'), ctx, { filename: f });
const req = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
const out = vm.runInContext(`(function (req) {
  const M = EVR.model, model = new M.Model(req.payload);
  const waits = req.waits.map(([s, t]) => M.waitAt(model.stations[s].wait_profile, t));
  const states = req.probes.map(([ri, t]) => {
    const st = M.stateAt(model, model.results[ri], t);
    return [st.kind, st.dist, st.energy, st.x, st.y];
  });
  return { waits, states };
})`, ctx)(req);
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_model_reproduces_the_evaluator(tmp_path, scenario, payload):
    """The browser's timeline (model.js) agrees with the Python evaluator and queue model.

    The animation interpolates between the node states measured by the
    evaluator and follows the exact charging trajectory during a stop; at
    every node and at every charging breakpoint its state must equal the
    evaluator's, and its port of WaitProfile.__call__ must equal Python's.
    """
    P = scenario.prepare()
    net = scenario.network
    waits, want_w = [], []
    for s in range(len(P.stations)):
        for t in (0.0, 1799.0, 1800.0, 5000.5, 43210.0, 84000.0, 86399.0, 90000.0):
            waits.append([s, t])
            want_w.append(P.wait_profiles[s](t))
    probes, want = [], []
    for ri, r in enumerate(payload["results"]):
        if not r["feasible"]:
            continue
        stops = {st["position"]: st for st in r["stops"]}
        for i, v in enumerate(r["nodes"][:-1]):
            if i in stops:
                st = stops[i]
                t_charge = st["arrival_s"] + st["wait_s"] + st["setup_s"]
                # interior breakpoints (the payload rounds node times to 0.01 s)
                for tc, e in zip(st["curve"]["t"][1:-1], st["curve"]["e"][1:-1]):
                    if 0.05 < tc < st["charge_s"] - 0.05:
                        probes.append([ri, t_charge + tc])
                        want.append(("charge", r["node_dist_m"][i], e, net.x[v], net.y[v]))
            # departing node i: start of the drive segment along edge i
            probes.append([ri, r["node_depart_s"][i] + 1e-6])
            want.append(("drive", r["node_dist_m"][i], r["node_energy_depart"][i], net.x[v], net.y[v]))
            # half way along edge i: energy interpolated between the two node states
            a, b = r["node_depart_s"][i], r["node_arrive_s"][i + 1]
            u, w = r["nodes"][i], r["nodes"][i + 1]
            probes.append([ri, (a + b) / 2])
            want.append(("drive", (r["node_dist_m"][i] + r["node_dist_m"][i + 1]) / 2,
                         (r["node_energy_depart"][i] + r["node_energy_arrive"][i + 1]) / 2,
                         (net.x[u] + net.x[w]) / 2, (net.y[u] + net.y[w]) / 2))
        probes.append([ri, r["total_time_s"] + 1.0])
        last = r["nodes"][-1]
        want.append(("done", r["node_dist_m"][-1], r["node_energy_depart"][-1], net.x[last], net.y[last]))
    data = tmp_path / "req.json"
    data.write_text(json.dumps({"payload": payload, "waits": waits, "probes": probes}), encoding="utf-8")
    script = tmp_path / "harness.js"
    script.write_text(_JS_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(script), str(WEB_DIR), str(data)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["waits"] == pytest.approx(want_w, abs=0.02)  # payload rounds waits to 0.01 s
    assert len(got["states"]) == len(want) > 10
    for (kind, dist, energy, x, y), (wk, wd, we, wx, wy) in zip(got["states"], want):
        assert kind == wk
        assert dist == pytest.approx(wd, abs=0.05)
        assert energy == pytest.approx(we, abs=2e-4)
        assert (x, y) == (pytest.approx(wx, abs=0.05), pytest.approx(wy, abs=0.05))


# ------------------------------------------------------------------ charging tariffs
def _peak_line(tariff=None):
    """O --60 km--> S --150 km--> D, one 50 kW charger at S reached at 19:30; the cost
    optimum under a 16-20 h peak price waits until 20:00 (a voluntary hold)."""
    from evroute import NetworkBuilder, Tariff
    from evroute.scenario import Conditions, Preferences, Query, Scenario, Station
    from evroute.vehicle import get_vehicle

    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, 100.0, name="O")
    s = b.add_node(60e3, 0.0, 100.0, name="S")
    d = b.add_node(210e3, 0.0, 100.0, name="D")
    b.add_road(o, s, road_class="motorway")
    b.add_road(s, d, road_class="motorway")
    st = [Station(0, s, 50.0, "DC", price_per_kwh=0.5, setup_s=180.0, plugs=2, daily_sessions=40.0, name="S")]
    return Scenario("peak line", b.build(), st, get_vehicle("compact50"),
                    Query(o, d, initial_soc=0.4, departure_s=19 * 3600.0), Conditions(temperature_c=20.0),
                    Preferences(objective="cost", value_of_time_per_h=15.0, charge_step_soc=0.05),
                    tariff=tariff or Tariff())


@pytest.fixture(scope="module")
def tariff_case():
    from evroute import Tariff

    sc = _peak_line(Tariff(peak_hours=((16, 20),), peak_factor=3.0, high_soc_factor=1.2))
    plans = run_all(sc, ["greedy", "bls-astar"], trace=True)
    return sc, plans, viewer_payload(sc, plans, lang="en", robustness_runs=20)


def test_neutral_payload_has_no_tariff_data(payload, scenario):
    """Without a tariff the viewer shows nothing new: no tariff keys, no holds."""
    assert "tariff" not in payload["scenario"]
    assert not any("peak_s" in st or "price_factors" in st for st in payload["stations"])
    for r in payload["results"]:
        assert r["hold_time_s"] == 0.0 and all(s["hold_s"] == 0.0 and s["peak"] is False for s in r["stops"])
    assert "station_tariffs" not in export.results_payload(scenario, [], robustness_runs=0)


def test_tariff_payload_and_hold_timeline(tariff_case):
    sc, plans, d = tariff_case
    assert d["scenario"]["tariff"] == sc.tariff.to_dict()
    assert d["stations"][0]["peak_s"] == [[16 * 3600.0, 20 * 3600.0]]
    assert d["stations"][0]["price_factors"] == {"offpeak": [1.0, 1.2], "peak": [3.0, 3.0 * 1.2]}
    r = next(r for r in d["results"] if r["algorithm"] == "bls-astar")
    (st,) = r["stops"]
    assert st["hold_s"] == pytest.approx(1800.0) and st["peak"] is False
    assert r["hold_time_s"] == pytest.approx(1800.0) and r["summary"]["hold_time_s"] == pytest.approx(1800.0)
    # the node timeline tiles the stop: arrival, hold, queue (at the end of the hold), set-up, charging
    i = st["position"]
    assert r["node_depart_s"][i] == pytest.approx(
        r["node_arrive_s"][i] + st["hold_s"] + st["wait_s"] + st["setup_s"] + st["charge_s"], abs=0.05)
    assert st["wait_s"] == pytest.approx(sc.prepare().wait_s(0, st["arrival_s"] + st["hold_s"]))
    assert st["curve"]["t"][-1] == pytest.approx(st["charge_s"], abs=0.02)  # counts from the start of charging
    # the live API carries the station periods with the planned scenario
    data = export.results_payload(sc, plans, robustness_runs=0)
    assert data["station_tariffs"] == [{"peak_s": [[57600.0, 72000.0]], "price_factors": d["stations"][0]["price_factors"]}]
    json.dumps(d, allow_nan=False)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_model_animates_the_hold(tmp_path, tariff_case):
    """model.js: the car waits at the station (state 'hold', energy unchanged) before it queues."""
    _, _, d = tariff_case
    ri = next(k for k, r in enumerate(d["results"]) if r["algorithm"] == "bls-astar")
    st = d["results"][ri]["stops"][0]
    t0, h, w, su = st["arrival_s"], st["hold_s"], st["wait_s"], st["setup_s"]
    probes = [[ri, t0 + 1.0], [ri, t0 + h / 2], [ri, t0 + h - 1.0], [ri, t0 + h + w / 2], [ri, t0 + h + w + su / 2],
              [ri, t0 + h + w + su + st["charge_s"] / 2]]
    data = tmp_path / "req.json"
    data.write_text(json.dumps({"payload": d, "waits": [], "probes": probes}), encoding="utf-8")
    script = tmp_path / "harness.js"
    script.write_text(_JS_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(script), str(WEB_DIR), str(data)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    states = json.loads(res.stdout)["states"]
    assert [s[0] for s in states] == ["hold", "hold", "hold", "wait", "setup", "charge"]
    for s in states[:5]:
        assert s[2] == pytest.approx(st["energy_in_kwh"], abs=1e-4)
    assert st["energy_in_kwh"] < states[5][2] < st["energy_out_kwh"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_ui_strings_exist_in_both_languages(tmp_path):
    """Every TR key has an EN twin and every literal I.t('key') / data-i18n key exists."""
    harness = r"""
const fs = require('fs'), vm = require('vm');
const dir = process.argv[2];
const ctx = { console, Intl, document: { documentElement: { setAttribute() {} } } };
ctx.window = ctx; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(dir + '/js/i18n.js', 'utf8'), ctx);
const S = ctx.EVR.i18n.STR;
process.stdout.write(JSON.stringify({ tr: Object.keys(S.tr), en: Object.keys(S.en) }));
"""
    script = tmp_path / "keys.js"
    script.write_text(harness, encoding="utf-8")
    res = subprocess.run(["node", str(script), str(WEB_DIR)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    keys = json.loads(res.stdout)
    assert sorted(keys["tr"]) == sorted(keys["en"])
    used = set()
    for name in JS_FILES:
        used.update(re.findall(r"I\.t\('([a-z0-9_]+)'", (WEB_DIR / "js" / name).read_text(encoding="utf-8")))
    used.update(re.findall(r'data-i18n(?:-aria|-title)?="([a-z0-9_]+)"', (WEB_DIR / "index.html").read_text(encoding="utf-8")))
    assert used and not used - set(keys["en"])
    for k in ("st_hold_offpeak", "hold", "stop_hold_offpeak", "tariff_toggle", "kv_tariff_peak"):
        assert k in keys["en"]


# ------------------------------------------------------------------ server
@pytest.fixture(scope="module")
def server():
    srv = make_server("grid-small", port=0, lang="en", robustness_runs=50)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _get(srv, path):
    try:
        with urllib.request.urlopen(srv.url.rstrip("/") + path, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def _post(srv, path, body=None, raw=None, headers=None):
    data = raw if raw is not None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(srv.url.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_server_page_and_assets(server):
    status, ctype, body = _get(server, "/")
    assert status == 200 and ctype.startswith("text/html")
    html = body.decode("utf-8")
    data = json.loads(_data_block(html))
    assert data["live"] is True and data["results"] == [] and data["scenario_preset"] == "grid-small"
    status, ctype, body = _get(server, "/js/app.js")
    assert status == 200 and "javascript" in ctype and b"EVR" in body
    assert _get(server, "/css/theme.css")[0] == 200
    for bad in ("/js/../server.py", "/js/%2e%2e/server.py", "/css/../../setup.py", "/etc/passwd",
                "/js/", "/js/app.js/../../x", "/index.html/../export.py"):
        assert _get(server, bad)[0] == 404, bad


def test_server_api_endpoints(server):
    status, _, body = _get(server, "/api/health")
    assert status == 200 and json.loads(body)["ok"] is True
    status, _, body = _get(server, "/api/scenarios")
    data = json.loads(body)
    names = [s["name"] for s in data["scenarios"]]
    assert status == 200 and data["default"] == "grid-small" and "grid-small" in names
    assert all(s.get("approx_nodes") is None or s["approx_nodes"] <= 15000 for s in data["scenarios"])
    status, _, body = _get(server, "/api/scenario?name=grid-small")
    p = json.loads(body)
    assert status == 200 and p["format"] == FORMAT and p["live"] is True and p["results"] == []
    status, _, body = _get(server, "/api/scenario?name=no-such-thing")
    assert status == 400 and "error" in json.loads(body)
    assert _get(server, "/api/nothing")[0] == 404


def test_server_plan(server):
    base = server.base_scenario("grid-small")
    o, d = base.query.origin, base.query.destination
    status, data = _post(server, "/api/plan", {
        "scenario": "grid-small", "origin": o, "destination": d, "algorithms": ["bls-astar", "greedy"],
        "initial_soc": 0.4, "temperature_c": 5, "reserve_soc": 0.12, "min_arrival_soc": 0.15,
        "objective": "time", "value_of_time_per_h": 20, "vehicle": "compact50", "speed_factors": [1.0],
        "queue_aware": True, "departure_s": "17:30",
    })
    assert status == 200, data
    assert [r["algorithm"] for r in data["results"]] == ["greedy", "bls-astar"]
    assert data["errors"] == [] and data["elapsed_ms"] >= 0
    sc = data["scenario"]
    assert sc["query"]["initial_soc"] == 0.4 and sc["query"]["departure_s"] == 17.5 * 3600
    assert sc["preferences"]["reserve_soc"] == 0.12 and sc["conditions"]["temperature_c"] == 5
    assert len(data["wait_profiles"]) == len(base.stations)
    best = {r["algorithm"]: r for r in data["results"]}
    assert best["bls-astar"]["feasible"]
    assert best["bls-astar"]["reliability"]["n"] == 50
    assert best["bls-astar"]["nodes"][0] == o and best["bls-astar"]["nodes"][-1] == d
    # the base scenario is not modified by a request
    assert server.base_scenario("grid-small").query.initial_soc == base.query.initial_soc


@pytest.mark.parametrize(
    "body, fragment",
    [
        ({"origin": 0, "destination": 0}, "differ"),
        ({"algorithms": ["nope"]}, "unknown algorithm"),
        ({"algorithms": []}, "at least one"),
        ({"initial_soc": 3}, "initial_soc"),
        ({"initial_soc": "abc"}, "initial_soc"),
        ({"initial_soc": True}, "initial_soc"),
        ({"temperature_c": float("nan")}, "temperature_c"),
        ({"objective": "fun"}, "objective"),
        ({"vehicle": "rocket"}, "vehicle"),
        ({"speed_factors": [2.0]}, "speed_factors"),
        ({"speed_factors": "fast"}, "speed_factors"),
        ({"queue_aware": "yes"}, "queue_aware"),
        ({"departure_s": "25:00"}, "departure_s"),
        ({"origin": 10 ** 9}, "origin"),
        ({"origin": "Atlantis"}, "origin"),
        ({"scenario": "no-such-preset"}, "scenario"),
        ({"surprise": 1}, "unknown field"),
        ({"tariff": "90"}, "tariff"),
        ({"tariff": True}, "tariff"),
        ([1, 2, 3], "JSON object"),
    ],
)
def test_server_rejects_bad_requests(server, body, fragment):
    if isinstance(body, dict) and "temperature_c" in body:
        raw = b'{"temperature_c": NaN}'
        status, data = _post(server, "/api/plan", raw=raw)
    else:
        status, data = _post(server, "/api/plan", body)
    assert status == 400, data
    assert fragment.lower() in data["error"].lower()


def test_server_rejects_malformed_and_oversized_bodies(server):
    status, data = _post(server, "/api/plan", raw=b"{not json")
    assert status == 400 and "invalid JSON" in data["error"]
    status, data = _post(server, "/api/plan", raw=b" " * (MAX_BODY_BYTES + 1))
    assert status == 413
    assert _post(server, "/api/other", {})[0] == 404
    # SRV-1: deep nesting is a client error, not a RecursionError (500)
    for raw in (b"[" * 30000 + b"]" * 30000, b'{"a":' * 10000 + b"1" + b"}" * 10000):
        status, data = _post(server, "/api/plan", raw=raw)
        assert status == 400 and "invalid JSON" in data["error"]
    # ... and a negative Content-Length is invalid, not "too large"
    host, port = server.server_address[:2]
    with socket.create_connection((host, port), timeout=30) as s:
        s.sendall(b"POST /api/plan HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: -5\r\n\r\n")
        reply = s.makefile("rb").read()  # the server closes the connection
    assert reply.startswith(b"HTTP/1.1 400 ") and b"invalid Content-Length" in reply


def test_build_plan_scenario_unit():
    base = grid_scenario(rows=4, cols=4, seed=2)
    sc, algos = build_plan_scenario(base, {"origin": "3", "destination": 12, "speed_factors": [0.8, 1.0, 0.8]})
    assert (sc.query.origin, sc.query.destination) == (3, 12)
    assert sc.preferences.speed_factors == (1.0, 0.8)
    assert algos and all(a in REGISTRY for a in algos)
    assert (base.query.origin, base.query.destination) == (0, 15)
    with pytest.raises(PlanRequestError):
        build_plan_scenario(base, {"reserve_soc": 0.9})


def test_build_plan_scenario_tariff_switch():
    from evroute import Tariff
    from evroute.generators.presets import DEMO_TARIFF, get_scenario

    base = grid_scenario(rows=4, cols=4, seed=2)
    assert build_plan_scenario(base, {})[0].tariff == Tariff()
    assert build_plan_scenario(base, {"tariff": "80"})[0].tariff == DEMO_TARIFF
    preset = get_scenario("turkey-karaman-istanbul-tariff")
    assert build_plan_scenario(preset, {})[0].tariff == DEMO_TARIFF  # absent: the preset's own tariff
    assert build_plan_scenario(preset, {"tariff": "flat"})[0].tariff == Tariff()
    assert preset.tariff == DEMO_TARIFF  # the base scenario is not modified


def test_server_tariff_switch(server):
    status, _, body = _get(server, "/api/scenario?name=grid-small")
    p = json.loads(body)
    assert status == 200 and p["demo_tariff"]["high_soc_factor"] > 1.0 and "tariff" not in p["scenario"]
    base = server.base_scenario("grid-small")
    status, data = _post(server, "/api/plan", {"algorithms": ["bls-astar"], "tariff": "80", "objective": "cost",
                                              "departure_s": "15:00"})
    assert status == 200, data
    assert data["scenario"]["tariff"]["peak_hours"] == [[16.0, 20.0]]
    assert len(data["station_tariffs"]) == len(base.stations)
    assert data["station_tariffs"][0]["peak_s"] == [[57600.0, 72000.0]]
    status, data = _post(server, "/api/plan", {"algorithms": ["bls-astar"], "tariff": "flat"})
    assert status == 200 and "tariff" not in data["scenario"] and "station_tariffs" not in data
