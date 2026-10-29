"""Tests of the interactive results panel (evroute.viz.results).

Most tests use a small, hand-made experiments folder with the runner's file
layout, so they are fast and independent of the algorithms; one test runs
two real (quick) experiments end to end.  Browser checks run only when Node
and Playwright are installed (and the browser test is marked slow).
"""

from __future__ import annotations

import csv
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from evroute.viz import results as R
from evroute.viz.export import ALGORITHM_COLORS

COLS = ["algorithm", "feasible", "time_min", "drive_min", "charge_min", "queue_min", "distance_km", "stops",
        "energy_kwh", "charged_kwh", "money", "soh_pct", "gcost", "arrival_soc", "runtime_ms", "settled", "generated"]


# ------------------------------------------------------------------ helpers
def row(algorithm: str, time_min: float | None, **extra):
    feasible = time_min is not None
    r = {
        "algorithm": algorithm, "feasible": int(feasible), "time_min": "" if time_min is None else time_min,
        "drive_min": 400.0, "charge_min": 0.0 if time_min is None else time_min - 410.0, "queue_min": 10.0,
        "distance_km": 700.0, "stops": 2 if feasible else 0, "energy_kwh": 130.0, "charged_kwh": 70.0,
        "money": 1200.0, "soh_pct": 0.019, "gcost": 3600.0, "arrival_soc": 0.1, "runtime_ms": 20.0,
        "settled": 1000, "generated": 5000,
    }
    r.update(extra)
    return r


def write_csv(path: Path, rows: list[dict]) -> None:
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def make_results(root: Path, *, break_bls: bool = False, dominated_pareto: bool = False) -> Path:
    """A results folder with every known experiment (tiny, consistent data)."""
    cases = ["turkey-karaman-istanbul", "turkey-antalya-ankara"]
    # case studies
    rows = []
    for c in cases:
        rows += [{"case": c, **row("naive", None)}, {"case": c, **row("greedy", 510.0)},
                 {"case": c, **row("station-graph", 560.0)}, {"case": c, **row("bls", 487.0)},
                 {"case": c, **row("bls-astar", 487.5 if break_bls else 487.0, runtime_ms=4.0)},
                 {"case": c, **row("cfp-astar", 486.9)}]
    write_csv(root / "case-studies" / "results.csv", rows)
    (root / "case-studies" / cases[0]).mkdir(parents=True)
    (root / "case-studies" / cases[0] / "viewer.html").write_text("<!doctype html>", encoding="utf-8")
    # scalability: two sizes, two pairs
    rows = []
    for n, k in ((1000, 1.0), (4000, 4.0)):
        for pair in (0, 1):
            t = 300.0 + 10 * pair
            rows += [{"nodes": n, "pair": pair, **row("bls", t, runtime_ms=100.0 * k, settled=int(20000 * k))},
                     {"nodes": n, "pair": pair, **row("bls-astar", t, runtime_ms=10.0 * k, settled=int(800 * k))},
                     {"nodes": n, "pair": pair, **row("cfp", t - 0.5, runtime_ms=150.0 * k, settled=int(3000 * k))},
                     {"nodes": n, "pair": pair, **row("cfp-astar", t - 0.5, runtime_ms=12.0 * k, settled=int(100 * k))}]
    write_csv(root / "scalability" / "results.csv", rows)
    # optimality gap
    rows = []
    for pair, opt in enumerate((500.0, 600.0, 700.0)):
        for a, gap in (("greedy", 4.0 + pair), ("station-graph", 10.0 + pair), ("genetic", 0.5 * pair), ("bls-astar", 0.1)):
            rows.append({"pair": pair, **row(a, opt * (1 + gap / 100)), "gap_pct": gap, "opt_min": opt})
    write_csv(root / "optimality-gap" / "results.csv", rows)
    # grid resolution
    rows = []
    for step, g_grid, g_breaks in ((0.1, 200.0, 130.0), (0.05, 130.0, 130.0), (0.02, 20.0, 16.0)):
        for tg, gap in (("grid", g_grid), ("grid+breaks", g_breaks)):
            rows.append({"case": cases[0], "step": step, "targets": tg, **row("bls-astar", 486.0 + gap / 60.0), "gap_s": gap})
    write_csv(root / "grid-resolution" / "results.csv", rows)
    # temperature
    rows = []
    for pre in (1, 0):
        for t, base in ((-10, 514.0), (0, 500.0), (20, 487.0)):
            rows.append({"temperature_c": t, "preconditioning": pre, **row("bls-astar", base + (0 if pre or t >= 20 else 60.0),
                                                                           energy_kwh=165.0 - t)})
    write_csv(root / "temperature" / "results.csv", rows)
    # queue awareness
    write_csv(root / "queue-awareness" / "results.csv", [
        {"case": cases[0], "departure_h": 8, "aware_min": 486.9, "blind_min": 492.2, "aware_wait_min": 6.1,
         "blind_wait_min": 11.8, "saving_min": 5.3},
        {"case": cases[0], "departure_h": 16, "aware_min": 485.5, "blind_min": 487.1, "aware_wait_min": 4.6,
         "blind_wait_min": 6.7, "saving_min": 1.6},
    ])
    # pareto (one CSV per case, no results.csv)
    front = [(488.4, 1203.2, 0.0193), (490.6, 1163.7, 0.0190), (493.8, 1148.8, 0.0189), (500.0, 1150.0, 0.0180)]
    if dominated_pareto:
        front.append((495.0, 1160.0, 0.0195))  # worse than (493.8, 1148.8, 0.0189) in every criterion
    write_csv(root / "pareto" / f"{cases[0]}.csv",
              [{"case": cases[0], **row("pareto", t, money=m, soh_pct=w, runtime_ms="", settled=0)} for t, m, w in front])
    (root / "pareto" / "summary.json").write_text(json.dumps({cases[0]: {"front_size": len(front)}}))
    # robustness
    write_csv(root / "robustness" / "results.csv", [
        {"safety_factor": f, "algorithm": "bls-astar", "feasible": True, "planned_time_min": pt, "stops": 2, "n": 200,
         "p_strand": ps, "p_strand_lo": 0.0, "p_strand_hi": ps + 0.02, "p_reserve_breach": pb,
         "p_reserve_breach_lo": max(0.0, pb - 0.05), "p_reserve_breach_hi": pb + 0.05, "time_p50_s": pt * 60 - 30,
         "time_p95_s": pt * 60 + 300, "distance_km": 747.3}
        for f, pt, ps, pb in ((1.0, 486.9, 0.0, 0.525), (1.1, 494.7, 0.0, 0.015), (1.2, 500.8, 0.0, 0.0))
    ])
    # eco speed
    write_csv(root / "eco-speed" / "results.csv", [
        {"pair": i, "temperature_c": 5.0, "fixed_min": 500.0 + i, "eco_min": 500.0 + i - s, "fixed_stops": 3,
         "eco_stops": 3 - (s > 1), "saving_min": s, "slowed_edges": int(s * 10)}
        for i, s in enumerate((0.0, 0.0, 0.12, 2.5))
    ])
    # vehicles
    write_csv(root / "vehicles" / "results.csv", [
        {"vehicle": "city27", **row("bls-astar", 657.3, stops=5)},
        {"vehicle": "sedan75", **row("bls-astar", 470.2, stops=2)},
    ])
    make_tariffs(root / "tariffs")
    make_real_data(root / "real-data")
    report = {name: {"status": "ok", "seconds": 1.5, "result": {}} for name in R.SECTION_IDS}
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return root


#: (hour, tariff) -> time-optimal (time, money), cost-optimal (time, money, hold), front [(time, money)]
#: for Karaman -> Istanbul; under flat prices the cost-optimal plan never holds.
TARIFF_DATA = {
    (14, "flat"): ((490.0, 1200.0), (500.0, 1100.0, 0.0), [(490.0, 1200.0), (495.0, 1150.0), (500.0, 1100.0), (520.0, 900.0)]),
    (14, "tier+peak"): ((490.0, 1320.0), (503.0, 1120.0, 2.4), [(490.0, 1320.0), (497.0, 1180.0), (503.0, 1120.0), (525.0, 950.0)]),
    (18, "flat"): ((482.0, 1200.0), (486.0, 1100.0, 0.0), [(482.0, 1200.0), (486.0, 1100.0)]),
    (18, "tier+peak"): ((482.0, 1260.0), (488.0, 1105.0, 0.0), [(482.0, 1260.0), (488.0, 1105.0)]),
}
VOT_PER_MIN = 4.0  # generalised cost = VOT_PER_MIN * time + money (+ no wear in this data)


#: (trip, data set) -> (CFP-A* minutes or None, distance km, stops, money, runtime ms, heuristic gaps [%]);
#: OSM alone has no route for İzmir -> Erzurum, the hybrid set (a superset of the OSM stations) never is slower.
REAL_DATA = {
    ("karaman-istanbul", "approx"): (487.0, 747.0, 2, 1134.0, 30.0, {"greedy": 5.0, "station-graph": 15.0, "genetic": 0.4}),
    ("karaman-istanbul", "osm"): (554.0, 755.0, 2, 1021.0, 23000.0, {"greedy": 163.0, "station-graph": 5.6, "genetic": 17.7}),
    ("karaman-istanbul", "hybrid"): (540.0, 760.0, 2, 1050.0, 21000.0, {"greedy": 80.0, "station-graph": 4.0, "genetic": 9.0}),
    ("izmir-erzurum", "approx"): (1049.0, 1467.0, 4, 3240.0, 70.0, {"greedy": 6.3, "station-graph": 13.4, "genetic": 0.8}),
    ("izmir-erzurum", "osm"): (None, 0.0, 0, 0.0, 190000.0, {}),
    ("izmir-erzurum", "hybrid"): (1070.0, 1466.0, 6, 3329.0, 20000.0, {"greedy": 12.0, "station-graph": 9.0, "genetic": 2.0}),
}


def make_real_data(d: Path, data=REAL_DATA, runs=None) -> None:
    """The ``real-data`` experiment's files (runner layout): results.csv and summary.json.

    ``runs`` maps (trip, network, heuristic) to (runtime_ms, stopped): the
    runtime of that run, and ``stopped`` for a run the time budget stopped
    (no plan, status "not run: over the time budget")."""
    runs = runs or {}
    rows = []
    presets = {"karaman-istanbul": ("turkey-karaman-istanbul", "turkey-osm-karaman-istanbul"),
               "izmir-erzurum": ("turkey-izmir-erzurum", "turkey-osm-izmir-erzurum")}
    for (trip, net), (t, km, stops, money, rt, gaps) in data.items():
        info = {"trip": trip, "network": net, "nodes": 1279 if net == "approx" else 44673, "stations": 290 if net == "approx" else 711,
                "dc_stations": 199 if net == "approx" else 93}
        for a, f in (("bls-astar", 1.0003), ("cfp-astar", 1.0)):
            tm = None if t is None else t * f
            rows.append({**info, **row(a, tm, distance_km=km, stops=stops, money=money, runtime_ms=rt * (0.3 if a == "bls-astar" else 1.0),
                                       status="limit" if t is None else "goal"), "gap_pct": "" if t is None else 100 * (f - 1)})
        for a, g in gaps.items():
            ms, stopped = runs.get((trip, net, a), (40000.0, False))
            if stopped:
                rows.append({**info, **row(a, None, runtime_ms=ms, stops="", settled="", generated="",
                                           status="not run: over the time budget"), "gap_pct": ""})
            else:
                rows.append({**info, **row(a, t * (1 + g / 100), runtime_ms=ms), "gap_pct": g})
    write_csv(d / "results.csv", rows)
    summary = {"trips": {k: {"presets": {"approx": a, "osm": o}} for k, (a, o) in presets.items()},
               "networks": ["approx", "osm", "hybrid"], "algorithms": ["bls-astar", "cfp-astar", "greedy", "station-graph", "genetic"],
               "reference": "cfp-astar", "heuristic_budget_s": 600.0}
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")


def make_tariffs(d: Path, data=TARIFF_DATA, complete: bool = True) -> None:
    """The ``tariffs`` experiment's files (runner layout): results.csv, fronts.csv, summary.json."""
    case = "turkey-karaman-istanbul"
    rows, members = [], []
    for (h, tariff), ((tt, tm), (ct, cm, hold), front) in data.items():
        key = {"case": case, "departure_h": h, "tariff": tariff}
        for kind, t, m, hm in (("time", tt, tm, 0.0), ("cost", ct, cm, hold)):
            rows.append({**key, "kind": kind, **row("bls-astar", t, money=m, gcost=VOT_PER_MIN * t + m),
                         "max_soc": 0.85 if kind == "cost" else 0.55, "above80_kwh": 4.0 if kind == "cost" else 0.0,
                         "holds": int(hm > 0), "hold_min": hm, "peak_stops": 0, "money_wear": m})
        rows.append({**key, "kind": "pareto", "algorithm": "pareto", "feasible": 1, "front_size": len(front),
                     "complete": int(complete), "runtime_ms": 900.0, "settled": 50000, "generated": 90000,
                     "status": "exhausted" if complete else "limit"})
        for i, (t, m) in enumerate(front):
            members.append({**key, "member": i, "time_min": t, "money": m, "soh_pct": 0.019, "stops": 2,
                            "holds": int(m == cm and hold > 0), "hold_min": hold if m == cm else 0.0,
                            "max_soc": 0.8, "above80_kwh": 0.0})
    write_csv(d / "results.csv", rows)
    write_csv(d / "fronts.csv", members)
    tier_peak = {"high_soc": 0.8, "high_soc_factor": 1.3, "peak_hours": [[16.0, 20.0]], "peak_factor": 1.15,
                 "peak_high_soc_factor": 2.0, "busy_utilisation": None}
    (d / "summary.json").write_text(json.dumps({
        "tariffs": {"flat": {"high_soc": 0.8, "high_soc_factor": 1.0, "peak_hours": [], "peak_factor": 1.0,
                             "peak_high_soc_factor": None, "busy_utilisation": None}, "tier+peak": tier_peak},
        "high_soc": 0.8, "charge_step_soc": 0.05, "pareto_max_labels": 1000000, "criteria": ["time", "money"],
        "cases": {case: {"viewer_departure_h": 14}}}), encoding="utf-8")


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    return make_results(tmp_path / "results")


def section(payload, sid):
    return next(s for s in payload["sections"] if s["id"] == sid)


def embedded_payload(html: str) -> dict:
    m = re.search(r'<script id="evroute-results" type="application/json">(.*?)</script>', html, re.S)
    assert m, "payload block missing"
    return json.loads(m.group(1))


# ------------------------------------------------------------- formatting
def test_number_formatting_both_languages():
    assert R.fmt_num(1234.56, 1, "en") == "1,234.6"
    assert R.fmt_num(1234.56, 1, "tr") == "1.234,6"
    assert R.fmt_num(-3.0, 0, "en") == "−3"
    assert R.fmt_num(-0.001, 1, "en") == "0.0"
    assert R.fmt_pct(4.73, 1, "en") == "4.7%"
    assert R.fmt_pct(4.73, 1, "tr") == "%4,7"
    assert R.fmt_factor(8.46, "en") == "8.5×" and R.fmt_factor(22.4, "tr") == "22×"
    assert R.fmt_minutes(0.12, "en") == "7 s"
    assert R.fmt_minutes(7.84, "tr") == "7,8 dk"
    assert R.fmt_minutes(487.0, "en") == "8 h 07 min"
    assert R.fmt_minutes(487.0, "tr") == "8 sa 07 dk"
    assert R.fmt_money(1177.8, "TRY", "tr") == "1.178 ₺"
    assert R.fmt_temp(-10, "en") == "−10 °C"
    assert R.fmt_num(float("nan")) == "–"


def test_box_stats_quartiles_and_whiskers():
    st = R.box_stats([1, 2, 3, 4, 100])
    assert st["n"] == 5 and st["median"] == 3 and st["q1"] == 2 and st["q3"] == 4
    assert st["whisker_hi"] == 4 and st["max"] == 100   # 100 is an outlier beyond 1.5 IQR
    assert R.box_stats([]) == {"n": 0}


# ------------------------------------------------------------------ payload
def test_payload_has_every_section_with_findings(results_dir):
    p = R.results_payload(results_dir)
    assert p["format"] == R.FORMAT and p["lang"] == "tr"
    assert [s["id"] for s in p["sections"]] == list(R.SECTION_IDS)
    for s in p["sections"]:
        assert s["status"] == "ok", s["id"]
        assert s["finding"] and s["finding"]["tr"] and s["finding"]["en"], s["id"]
        assert s["meaning"]["tr"] and s["meaning"]["en"]
        assert s["csv"] and s["csv"][0]["text"].strip(), s["id"]
        assert s["data"] is not None, s["id"]
    # colours of the algorithms are exactly the viewer's
    for a, (light, dark) in ALGORITHM_COLORS.items():
        assert p["theme"]["algorithms"][a]["color"] == [light, dark]
    json.dumps(p, allow_nan=False)  # strictly serialisable


def test_findings_are_computed_from_the_data(results_dir):
    p = R.results_payload(results_dir)
    q = section(p, "queue-awareness")
    assert math.isclose(q["facts"]["mean_saving_min"], (5.3 + 1.6) / 2)
    assert "3.5 min" in q["finding"]["en"] and "3,5 dk" in q["finding"]["tr"]
    sc = section(p, "scalability")
    assert math.isclose(sc["facts"]["speedup"]["bls-astar"], 10.0)
    assert math.isclose(sc["facts"]["settled_ratio"]["bls-astar"], 25.0)
    assert "10×" in sc["finding"]["en"] and "4,000 nodes" in sc["finding"]["en"]
    assert "identical optima" in sc["finding"]["en"] and "aynı optimumu" in sc["finding"]["tr"]
    og = section(p, "optimality-gap")
    assert og["facts"]["mean_gap_pct"]["greedy"] == pytest.approx(5.0)
    assert "Genetic" in og["finding"]["en"] and "Genetik" in og["finding"]["tr"]
    rb = section(p, "robustness")
    assert rb["facts"]["target_factor"] == 1.1 and "52.5%" in rb["finding"]["en"]
    cs = section(p, "case-studies")
    assert cs["facts"]["exact_best"] == 2 and cs["facts"]["strands"]["naive"] == 2
    eco = section(p, "eco-speed")
    assert eco["facts"]["improved"] == 2 and eco["facts"]["fewer_stops"] == 1
    # a different data set gives a different sentence (nothing is hard-coded)
    other = make_results(results_dir.parent / "other")
    write_csv(other / "queue-awareness" / "results.csv", [
        {"case": "turkey-antalya-ankara", "departure_h": 12, "aware_min": 400.0, "blind_min": 421.0,
         "aware_wait_min": 3.0, "blind_wait_min": 24.0, "saving_min": 21.0}])
    q2 = section(R.results_payload(other), "queue-awareness")
    assert "21 min" in q2["finding"]["en"] and "Antalya → Ankara" in q2["finding"]["en"] and "12:00" in q2["finding"]["en"]


def test_exactness_checks_pass_on_consistent_data(results_dir):
    c = R.results_payload(results_dir)["checks"]
    assert c["total"] > 20 and c["passed"] == c["total"]
    ids = {g["id"] for g in c["groups"]}
    assert {"bls-astar-equals-bls", "cfp-astar-equals-cfp", "cfp-lower-bound", "breakpoints-help",
            "queue-aware-optimal", "eco-not-worse", "safety-monotone", "pareto-nondominated"} <= ids
    kpi = next(k for k in R.results_payload(results_dir)["kpis"] if k["id"] == "checks")
    assert kpi["tone"] == "ok"


def test_exactness_checks_catch_violations(tmp_path):
    p = R.results_payload(make_results(tmp_path / "bad", break_bls=True, dominated_pareto=True))
    groups = {g["id"]: g for g in p["checks"]["groups"]}
    assert groups["bls-astar-equals-bls"]["passed"] < groups["bls-astar-equals-bls"]["total"]
    assert groups["bls-astar-equals-bls"]["failures"]
    assert groups["pareto-nondominated"]["passed"] == groups["pareto-nondominated"]["total"] - 1
    assert next(k for k in p["kpis"] if k["id"] == "checks")["tone"] == "bad"


def test_missing_failed_and_unknown_experiments(tmp_path):
    root = make_results(tmp_path / "r")
    shutil.rmtree(root / "pareto")
    shutil.rmtree(root / "robustness")
    report = json.loads((root / "report.json").read_text())
    del report["pareto"]
    report["robustness"] = {"status": "failed", "seconds": 0.1, "result": {"error": "RuntimeError('boom')"}}
    (root / "report.json").write_text(json.dumps(report))
    write_csv(root / "my-extra" / "results.csv", [{"a": 1, "b": 2.5}, {"a": 2, "b": 3.5}])
    p = R.results_payload(root)
    assert section(p, "pareto")["status"] == "missing" and section(p, "pareto")["index"] is None
    rb = section(p, "robustness")
    assert rb["status"] == "failed" and "boom" in rb["error"]
    extra = section(p, "my-extra")
    assert extra["known"] is False and extra["tables"][0]["columns"] == ["a", "b"]
    assert extra["tables"][0]["rows"] == [[1, 2.5], [2, 3.5]]
    assert not any(k["section"] == "pareto" for k in p["kpis"])
    html = R.render_results_html(p)
    assert "my-extra" in html


def test_empty_folder_and_quick_runner_output(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    p = R.results_payload(empty)
    assert all(s["status"] == "missing" for s in p["sections"])
    assert p["checks"]["total"] == 0
    out = R.build_results_html(empty, tmp_path / "empty.html")
    assert out.exists()
    with pytest.raises(FileNotFoundError):
        R.load_results(tmp_path / "does-not-exist")


def test_pareto_without_feasible_front(tmp_path):
    root = make_results(tmp_path / "r")
    write_csv(root / "pareto" / "turkey-karaman-istanbul.csv",
              [{"case": "turkey-karaman-istanbul", **row("pareto", None, settled=186979, generated=2275584)}])
    s = section(R.results_payload(root), "pareto")
    assert s["data"]["fronts"] == [] and s["data"]["empty"][0]["case"] == "turkey-karaman-istanbul"
    assert "no feasible front" in s["finding"]["en"]


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_scalability_headline_compares_at_one_size_when_bls_is_skipped(tmp_path):
    root = make_results(tmp_path / "r")
    path = root / "scalability" / "results.csv"
    rows = read_rows(path)
    for pair in (0, 1):  # a full run skips BLS on the largest network, so BLS-A* has no speed-up there
        t = 300.0 + 10 * pair
        rows += [{"nodes": 16000, "pair": pair, **row("bls-astar", t, runtime_ms=160.0, settled=12800)},
                 {"nodes": 16000, "pair": pair, **row("cfp", t - 0.5, runtime_ms=9600.0, settled=48000)},
                 {"nodes": 16000, "pair": pair, **row("cfp-astar", t - 0.5, runtime_ms=32.0, settled=1600)}]
    write_csv(path, rows)
    p = R.results_payload(root)
    sc = section(p, "scalability")
    assert sc["data"]["headline_nodes"] == 4000 and sc["facts"]["nodes"] == {"bls-astar": 4000, "cfp-astar": 4000}
    assert sc["facts"]["speedup"]["cfp-astar"] == pytest.approx(12.5)  # 150 / 12 ms at 4,000 nodes, not 300x
    cfp = next(s for s in sc["data"]["speedup"] if s["algorithm"] == "cfp-astar")
    assert cfp["points"][-1]["nodes"] == 16000 and cfp["points"][-1]["speedup"] == pytest.approx(300.0)  # still charted
    en = sc["finding"]["en"]
    assert "4,000 nodes" in en and "16,000" not in en and "300×" not in en
    assert "4,000 nodes" in next(k for k in p["kpis"] if k["id"] == "speedup-cfp")["sub"]["en"]


def test_scalability_mismatch_is_stated_not_hidden(tmp_path):
    root = make_results(tmp_path / "r")
    path = root / "scalability" / "results.csv"
    rows = read_rows(path)
    for r in rows:  # BLS-A* misses the optimum on both pairs of the largest size
        if r["algorithm"] == "bls-astar" and r["nodes"] == "4000":
            r["time_min"] = float(r["time_min"]) + 5.0
    write_csv(path, rows)
    p = R.results_payload(root)
    sc = section(p, "scalability")
    assert sc["data"]["headline_nodes"] == 4000  # no silent fallback to the size where they agree
    assert sc["facts"]["mismatches"] == {"bls-astar": [2, 4], "cfp-astar": [0, 4]}
    en, tr = sc["finding"]["en"], sc["finding"]["tr"]
    assert "identical optima" not in en and "BLS-A* and BLS disagree on 2 of 4 instances" in en and "4,000 nodes" in en
    assert "aynı optimumu" not in tr and "4 örneğin 2 tanesinde farklı sonuç" in tr
    hero = next(k for k in p["kpis"] if k["id"] == "speedup-bls")
    assert hero["tone"] == "bad" and "same optimum" not in hero["sub"]["en"] and "differs on 2 of 4" in hero["sub"]["en"]
    assert "same optimum" in next(k for k in p["kpis"] if k["id"] == "speedup-cfp")["sub"]["en"]


def test_scalability_label_budget_is_not_a_mismatch(tmp_path):
    root = make_results(tmp_path / "r")
    path = root / "scalability" / "results.csv"
    rows = read_rows(path)
    for r in rows:  # BLS runs out of its label budget on one pair of the largest size
        r["status"] = ""
        if r["algorithm"] == "bls" and r["nodes"] == "4000" and r["pair"] == "1":
            r.update(feasible=0, time_min="nan", status="limit")
    write_csv(path, rows)
    p = R.results_payload(root)
    sc = section(p, "scalability")
    assert all(g["passed"] == g["total"] for g in p["checks"]["groups"])  # "not solved" is no disagreement
    assert sc["facts"]["mismatches"]["bls-astar"] == [0, 3]
    assert sc["data"]["budget"] == [{"algorithm": "bls", "nodes": 4000, "unsolved": 1, "total": 2}]
    assert "BLS ran out of its label budget on 1 of 2 instances at 4,000 nodes" in sc["finding"]["en"]
    assert "etiket bütçesini 4.000 düğümde 2 örneğin 1 tanesinde aştı" in sc["finding"]["tr"]


def test_speedup_is_a_labelled_geometric_mean(tmp_path):
    root = make_results(tmp_path / "r")
    path = root / "scalability" / "results.csv"
    rows = read_rows(path)
    for r in rows:  # per-instance speed-ups 4x and 16x at 4,000 nodes (BLS-A* takes 40 ms)
        if r["algorithm"] == "bls" and r["nodes"] == "4000":
            r["runtime_ms"] = 160.0 if r["pair"] == "0" else 640.0
    write_csv(path, rows)
    sc = section(R.results_payload(root), "scalability")
    pt = next(s for s in sc["data"]["speedup"] if s["algorithm"] == "bls-astar")["points"][-1]
    assert pt["speedup"] == pytest.approx(8.0) and sc["facts"]["speedup"]["bls-astar"] == pytest.approx(8.0)
    # the runtimes shown beside the factor are aggregated the same way: their ratio is the factor
    assert pt["runtime_ms"] == pytest.approx(40.0) and pt["baseline_runtime_ms"] == pytest.approx(320.0)
    bls = next(s for s in sc["data"]["series"] if s["algorithm"] == "bls")["points"][-1]
    assert bls["runtime_ms"] == pytest.approx(400.0)  # the arithmetic mean (runtime chart) would suggest 10x
    assert "geometric means" in sc["finding"]["en"] and "geometrik ortalama" in sc["finding"]["tr"]


def test_pareto_references_beating_a_member_are_flagged(results_dir):
    p = R.results_payload(results_dir)
    s = section(p, "pareto")
    f = s["data"]["fronts"][0]
    # CFP-A*'s continuous-charging plan (486.9 min, 1,200 TRY, 0.019 %SoH) beats member 0
    # (488.4 min, 1,203.2 TRY, 0.0193 %SoH) in every criterion: the front is exact on its grid only
    assert f["beaten_by_references"] == [0] and s["facts"]["beaten_by_references"] == {"turkey-karaman-istanbul": 1}
    refs = {r["algorithm"]: r["dominates"] for r in f["references"]}
    assert refs["cfp-astar"] == 1 and refs["greedy"] == 0
    g = next(g for g in p["checks"]["groups"] if g["id"] == "pareto-nondominated")
    assert g["passed"] == g["total"]  # a grid limitation, not an exactness failure
    assert "grid" in s["meaning"]["en"] and "guarantees" not in s["meaning"]["en"] and "ızgara" in s["meaning"]["tr"]


def test_tariffs_section_is_computed_from_the_data(results_dir):
    p = R.results_payload(results_dir, out_dir=results_dir)
    s = section(p, "tariffs")
    f = s["facts"]
    assert f["main_tariff"] == "tier+peak" and f["n"] == 2
    assert f["premium_mean_pct"] == pytest.approx(7.5) and f["premium_max_pct"] == pytest.approx(10.0)
    assert f["saving_mean"] == pytest.approx(177.5) and f["extra_time_mean_min"] == pytest.approx(9.5)
    assert f["holds"] == 1 and f["max_hold_min"] == pytest.approx(2.4)
    assert (f["front_min"], f["front_max"], f["incomplete_fronts"]) == (2, 4, 0)
    en, tr = s["finding"]["en"], s["finding"]["tr"]
    assert "80 % tier + peak" in en and "8% more" in en and "up to 10%" in en and "leaving at 14:00" in en
    assert "₺178" in en and "9.5 min more" in en and "in 1 of 2 trips it waits" in en and "2.4 min" in en
    assert "no single best route" in en and "2–4 plans" in en and "label budget" not in en
    assert "%80 kademesi + puant" in tr and "178 ₺" in tr and "2 yolculuğun 1 tanesinde" in tr and "Tek bir en iyi rota yok" in tr
    d = s["data"]
    assert [t["key"] for t in d["tariffs"]] == ["flat", "tier+peak"] and d["tariffs"][1]["spec"]["peak_factor"] == 1.15
    assert d["hours"] == [14, 18] and d["step_pct"] == pytest.approx(5.0) and d["budget"] == 1000000
    case = d["cases"][0]
    assert case["title"] == "Karaman → İstanbul" and case["currency"] == "TRY" and case["viewer_hour"] == 14
    p14 = case["series"]["tier+peak"][0]
    assert p14["hour"] == 14 and p14["cost"]["hold_min"] == pytest.approx(2.4) and p14["front"] == {
        "size": 4, "complete": True, "status": "exhausted", "runtime_ms": 900.0, "settled": 50000}
    assert d["fronts"]["turkey-karaman-istanbul|14|tier+peak"][0][:2] == [490.0, 1320.0]
    groups = {g["id"]: g for g in p["checks"]["groups"]}
    for cid in ("tariff-time-optimal", "tariff-cost-gcost", "tariff-cost-money", "tariff-front-extremes"):
        assert groups[cid]["total"] == 4 and groups[cid]["passed"] == 4, cid
    assert "tariffs" in groups["pareto-nondominated"]["sections"]
    kpi = next(k for k in p["kpis"] if k["id"] == "tariffs")
    assert kpi["value"]["en"] == "−₺178" and "1/2 with a hold" in kpi["sub"]["en"]
    # the tariff colours are emitted for both themes; flat prices are the neutral reference
    assert p["theme"]["tariffs"]["flat"]["color"] is None and p["theme"]["tariffs"]["tier+peak"]["color"] == ["#C2185B", "#D43D84"]
    html = R.render_results_html(p)
    assert "--tariff-0: var(--text-3);" in html and "--tariff-3: #C2185B;" in html and "--tariff-3: #D43D84;" in html


def test_tariff_checks_catch_violations(tmp_path):
    root = make_results(tmp_path / "r")
    bad = dict(TARIFF_DATA)
    # 14:00: no member of the complete front covers the cost optimum, and a member is beaten in
    # both criteria; 18:00: the cost-optimal plan is dearer in generalised cost and money than
    # the fastest one (tier+peak) and the front's fastest member misses the time optimum (flat)
    tp14, tp18 = bad[(14, "tier+peak")], bad[(18, "tier+peak")]
    bad[(14, "tier+peak")] = (tp14[0], (495.0, 1150.0, 2.4), tp14[2] + [(510.0, 1200.0)])
    bad[(18, "tier+peak")] = (tp18[0], (488.0, 1300.0, 0.0), tp18[2])
    bad[(18, "flat")] = ((482.0, 1200.0), (486.0, 1100.0, 0.0), [(483.0, 1200.0), (486.0, 1100.0)])
    make_tariffs(root / "tariffs", bad)
    p = R.results_payload(root)
    groups = {g["id"]: g for g in p["checks"]["groups"]}
    assert groups["tariff-cost-gcost"]["passed"] == 3 and groups["tariff-cost-money"]["passed"] == 3
    assert groups["tariff-front-extremes"]["passed"] == 2
    assert any("no member covers" in x for x in groups["tariff-front-extremes"]["failures"])
    assert any("fastest member 483.0" in x for x in groups["tariff-front-extremes"]["failures"])
    pd = groups["pareto-nondominated"]
    assert pd["passed"] == pd["total"] - 1 and any("tier+peak" in x for x in pd["failures"])
    assert next(k for k in p["kpis"] if k["id"] == "checks")["tone"] == "bad"
    # a time-optimal plan that holds, or a plan faster than it, is a failure too
    bad2 = dict(TARIFF_DATA)
    bad2[(18, "flat")] = ((482.0, 1200.0), (481.0, 1100.0, 0.0), [(482.0, 1200.0), (486.0, 1100.0)])
    make_tariffs(root / "tariffs", bad2)
    rows = read_rows(root / "tariffs" / "results.csv")
    for r in rows:
        if r["departure_h"] == "14" and r["tariff"] == "flat" and r["kind"] == "time":
            r["hold_min"], r["holds"] = 1.5, 1
    write_csv(root / "tariffs" / "results.csv", rows)
    g = next(g for g in R.results_payload(root)["checks"]["groups"] if g["id"] == "tariff-time-optimal")
    assert g["passed"] == 2 and g["total"] == 4


def test_tariff_fronts_stopped_by_the_budget_are_marked(tmp_path):
    root = make_results(tmp_path / "r")
    make_tariffs(root / "tariffs", complete=False)
    p = R.results_payload(root)
    s = section(p, "tariffs")
    assert s["facts"]["incomplete_fronts"] == 4 and all(
        not pt["front"]["complete"] for pts in s["data"]["cases"][0]["series"].values() for pt in pts)
    assert "(4 of 4 fronts stopped at the label budget)" in s["finding"]["en"]
    assert "(4 cepheden 4 tanesi etiket bütçesinde kesildi)" in s["finding"]["tr"]
    # an incomplete front proves nothing about the cost optimum, so only completeness-free checks run
    assert all(g["passed"] == g["total"] for g in p["checks"]["groups"])


def test_real_quick_tariffs_experiment(tmp_path, monkeypatch):
    from evroute.experiments import runner

    monkeypatch.setattr(runner, "CASES", ["turkey-antalya-ankara"])  # the faster trip only
    runner.run_experiments(["tariffs"], tmp_path / "res", quick=True)
    out = tmp_path / "res" / "tariffs"
    rows = read_rows(out / "results.csv")
    assert {(r["departure_h"], r["tariff"], r["kind"]) for r in rows} == {
        (h, t, k) for h in ("14", "18") for t in ("flat", "tier+peak") for k in ("time", "cost", "pareto")}
    for r in rows:
        if r["kind"] == "time":
            assert float(r["hold_min"]) == 0.0 and r["status"] == "goal"
        if r["kind"] == "pareto":
            assert int(r["front_size"]) > 0 and r["complete"] in ("0", "1")
    assert read_rows(out / "fronts.csv") and json.loads((out / "summary.json").read_text())["charge_step_soc"] == 0.05
    assert (out / "turkey-antalya-ankara" / "viewer.html").exists()
    p = embedded_payload((tmp_path / "res" / "results.html").read_text(encoding="utf-8"))
    s = section(p, "tariffs")
    assert s["status"] == "ok" and s["finding"]["en"] and s["data"]["cases"][0]["viewer"]
    assert p["checks"]["total"] >= 16 and p["checks"]["passed"] == p["checks"]["total"]


def test_real_data_section_is_computed_from_the_data(results_dir):
    p = R.results_payload(results_dir)
    s = section(p, "real-data")
    assert s["status"] == "ok"
    d, f = s["data"], s["facts"]
    assert [n["key"] for n in d["networks"]] == ["approx", "osm", "hybrid"]
    assert [t["id"] for t in d["trips"]] == ["karaman-istanbul", "izmir-erzurum"]
    assert d["trips"][0]["title"] == "Karaman → İstanbul" and d["credit"] == "© OpenStreetMap contributors"
    assert f["feasible"] == {"approx": 2, "osm": 1, "hybrid": 2}
    assert f["infeasible_trips"]["osm"] == ["İzmir → Erzurum"]
    assert math.isclose(f["time_change_pct"]["osm"], 100 * (554.0 / 487.0 - 1))
    assert math.isclose(f["time_change_pct"]["hybrid"], 100 * ((540.0 / 487.0 - 1) + (1070.0 / 1049.0 - 1)) / 2)
    assert math.isclose(f["runtime_s"]["osm"]["cfp-astar"], (23000.0 + 190000.0) / 2 / 1000)
    assert math.isclose(f["mean_gap_pct"]["osm"]["greedy"], 163.0)
    # all rows drive 400 min (make_real_data): the whole change is time at the stops
    assert f["drive_change_min"]["osm"] == 0.0 and math.isclose(f["stop_time_change_min"]["osm"], 554.0 - 487.0)
    assert math.isclose(f["heuristic_runtime_s"]["osm"]["greedy"], 40.0)
    assert "driving +0 s, stops +1 h 07 min on average" in s["finding"]["en"]
    assert "the heuristics take a median of Greedy 40 s, " in s["finding"]["en"]
    assert "Genetic 40 s, against 1.8 min for CFP-A*." in s["finding"]["en"]
    assert math.isclose(f["heuristic_runtime_max_s"]["osm"]["greedy"], 40.0) and "up to" not in s["finding"]["en"]
    assert "sezgisellerin medyan çalışma süresi Açgözlü 40 sn" in s["finding"]["tr"]
    plan = d["trips"][1]["nets"]["osm"]["plans"]["cfp-astar"]
    assert plan["feasible"] is False and plan["limit"] is True and plan["time_min"] is None
    assert "1 of 2 trips with the OSM stations (not İzmir → Erzurum)" in s["finding"]["en"]
    assert "Greedy" in s["finding"]["en"] and "→ 163.0%" in s["finding"]["en"] and "%163,0" in s["finding"]["tr"]
    groups = {g["id"]: g for g in p["checks"]["groups"]}
    assert groups["more-stations-not-slower"]["total"] == 1 and groups["more-stations-not-slower"]["passed"] == 1
    assert "real-data" in groups["cfp-lower-bound"]["sections"]
    # every heuristic run took 40 s: within the 10 min budget
    assert f["heuristic_budget_s"] == 600.0 and d["budget_s"] == 600.0 and f["over_budget"]["osm"]["station-graph"] == 0
    assert "time budget" not in s["finding"]["en"] and "süre sınırı" not in s["finding"]["tr"]


def test_real_data_runs_over_the_time_budget(tmp_path):
    root = make_results(tmp_path / "res")
    shutil.rmtree(root / "real-data")
    make_real_data(root / "real-data", runs={("karaman-istanbul", "osm", "station-graph"): (601000.0, True),
                                             ("karaman-istanbul", "hybrid", "station-graph"): (3600000.0, False),
                                             ("karaman-istanbul", "approx", "station-graph"): (900000.0, False)})
    p = R.results_payload(root)
    s = section(p, "real-data")
    f, nets = s["facts"], s["data"]["trips"][0]["nets"]
    sg = nets["osm"]["plans"]["station-graph"]
    assert sg["stopped"] and sg["over"] and sg["feasible"] is False and sg["gap_pct"] is None and sg["runtime_ms"] == 601000.0
    h = nets["hybrid"]["plans"]["station-graph"]
    assert h["over"] and not h["stopped"] and h["feasible"]
    assert nets["approx"]["plans"]["station-graph"]["over"] is False  # the budget is for the OSM data sets only
    assert nets["osm"]["plans"]["greedy"]["over"] is False
    assert f["heuristic_runs"]["osm"]["station-graph"] == 1 and f["heuristic_runs"]["hybrid"]["station-graph"] == 2
    assert f["over_budget"]["osm"]["station-graph"] == 1 and f["stopped"]["osm"]["station-graph"] == 1
    assert f["over_budget"]["hybrid"]["station-graph"] == 1 and f["stopped"]["hybrid"]["station-graph"] == 0
    assert f["over_budget"]["osm"]["greedy"] == 0
    assert f["mean_gap_pct"]["osm"]["station-graph"] is None  # the stopped run has no plan, so no gap
    assert math.isclose(f["heuristic_runtime_s"]["osm"]["station-graph"], 601.0)  # counted with its lower bound
    en, tr = s["finding"]["en"], s["finding"]["tr"]
    assert ("The time budget of 10 min per heuristic run on the OSM data sets is exceeded by Station graph in 2 of 3 runs "
            "(the slowest 1 h 00 min, Karaman → İstanbul with the hybrid set; 1 stopped at the budget): ") in en
    assert "impractical at this scale" in en and "Greedy in" not in en
    assert "süre sınırını aşan sezgisel: İstasyon grafı 2/3 çalıştırmada (en yavaşı 1 sa 00 dk" in tr
    assert p["checks"]["passed"] == p["checks"]["total"]


def test_real_data_checks_catch_violations(tmp_path):
    root = make_results(tmp_path / "res")
    bad = dict(REAL_DATA)
    t, *rest = bad[("karaman-istanbul", "hybrid")]
    bad[("karaman-istanbul", "hybrid")] = (560.0, *rest)  # slower than with the OSM stations alone
    shutil.rmtree(root / "real-data")
    make_real_data(root / "real-data", bad)
    p = R.results_payload(root)
    groups = {g["id"]: g for g in p["checks"]["groups"]}
    assert groups["more-stations-not-slower"]["passed"] == 0 and "karaman-istanbul" in groups["more-stations-not-slower"]["failures"][0]


def test_plan_within_budget_returns_the_in_process_plan_or_stops():
    from evroute.experiments import runner
    from evroute.experiments.common import safe_plan
    from evroute.generators.presets import get_scenario

    P = get_scenario("turkey-karaman-mersin").prepare()
    ref = safe_plan(P, "greedy")
    p, secs, over = runner.plan_within_budget(P, "greedy", 300.0)
    assert not over and secs > 0 and p is not None
    assert (p.feasible, p.total_time_s, p.money, p.num_stops, [s.node for s in p.stops]) == \
        (ref.feasible, ref.total_time_s, ref.money, ref.num_stops, [s.node for s in ref.stops])
    p, secs, over = runner.plan_within_budget(P, "greedy", 0.0)
    assert over and p is None
    p, secs, over = runner.plan_within_budget(P, "greedy", None)  # no budget: in this process
    assert not over and p.total_time_s == ref.total_time_s


def test_real_data_experiment_records_runs_over_the_budget(tmp_path, monkeypatch):
    from evroute.experiments import runner

    monkeypatch.setattr(runner, "REAL_HEURISTIC_BUDGET_S", 0.0)
    monkeypatch.setattr(runner, "REAL_NETWORKS", ("approx", "hybrid"))
    out = tmp_path / "real-data"
    out.mkdir()
    runner.exp_real_data(out, quick=True)  # Karaman -> Mersin, exact planners and genetic
    rows = {(r["network"], r["algorithm"]): r for r in read_rows(out / "results.csv")}
    assert set(rows) == {(n, a) for n in ("approx", "hybrid") for a in ("bls-astar", "cfp-astar", "genetic")}
    stopped = rows[("hybrid", "genetic")]
    assert stopped["status"] == runner.OVER_BUDGET and stopped["feasible"] == "0" and stopped["time_min"] == ""
    assert float(stopped["runtime_ms"]) >= 0.0 and stopped["gap_pct"] == ""
    assert rows[("approx", "genetic")]["feasible"] == "1"  # the budget is for the OSM data sets only
    assert rows[("hybrid", "cfp-astar")]["feasible"] == "1"  # ... and for the heuristics only
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["heuristic_budget_s"] == 0.0 and "genetic" in summary["trips"]["karaman-mersin"]["hybrid"]["over_budget"]


@pytest.mark.slow
def test_real_quick_real_data_experiment(tmp_path):
    from evroute.experiments import runner

    runner.run_experiments(["real-data"], tmp_path / "res", quick=True)
    rows = read_rows(tmp_path / "res" / "real-data" / "results.csv")
    assert {(r["trip"], r["network"]) for r in rows} == {("karaman-mersin", n) for n in runner.REAL_NETWORKS}
    assert {r["algorithm"] for r in rows} == {"bls-astar", "cfp-astar", "genetic"}
    for r in rows:
        assert r["feasible"] == "1"
        if r["algorithm"] == "cfp-astar":
            assert float(r["gap_pct"]) == 0.0
        assert float(r["gap_pct"]) >= -1e-6
    assert {r["nodes"] for r in rows if r["network"] != "approx"} == {"44673"}
    p = embedded_payload((tmp_path / "res" / "results.html").read_text(encoding="utf-8"))
    s = section(p, "real-data")
    assert s["status"] == "ok" and s["finding"]["en"] and s["finding"]["tr"]
    assert p["checks"]["passed"] == p["checks"]["total"] > 0


def test_tariff_metrics_of_a_plan():
    from evroute.experiments.runner import TARIFF_NAMES, tariff_metrics, tariff_set
    from evroute.generators.presets import DEMO_TARIFF, get_scenario
    from evroute.scenario import Tariff

    ts = tariff_set()
    assert tuple(ts) == TARIFF_NAMES and ts["flat"] == Tariff() and ts["tier+peak"] == DEMO_TARIFF
    assert ts["tier"].peak_hours == () and ts["tier"].high_soc_factor == DEMO_TARIFF.high_soc_factor
    assert ts["peak"].high_soc_factor == 1.0 and ts["peak"].peak_hours == DEMO_TARIFF.peak_hours
    sc = get_scenario("turkey-karaman-istanbul-tariff").with_preferences(objective="cost", charge_step_soc=0.05)
    P = sc.prepare()
    from evroute.algorithms import plan

    p = plan(sc, "bls-astar")
    m = tariff_metrics(p, P.capacity, P.vot_per_s)
    assert m["holds"] == sum(1 for s in p.stops if s.hold_s > 0) >= 1 and m["hold_min"] == pytest.approx(p.hold_time_s / 60)
    assert m["max_soc"] == max(s.soc_out for s in p.stops)
    hi = 0.8 * P.capacity
    assert m["above80_kwh"] == pytest.approx(sum(max(0.0, s.energy_out_kwh - max(s.energy_in_kwh, hi)) for s in p.stops))
    assert m["money_wear"] == pytest.approx(p.generalized_cost - P.vot_per_s * p.total_time_s)
    assert m["money_wear"] >= p.money


def test_optimality_gap_finding_gives_mean_and_max_of_bls_astar(tmp_path):
    root = make_results(tmp_path / "r")
    rows = []
    for pair, opt in enumerate((500.0, 600.0, 700.0)):
        for a, gap in (("greedy", 4.0 + pair), ("bls-astar", (0.1, 0.1, 0.4)[pair])):
            rows.append({"pair": pair, **row(a, opt * (1 + gap / 100)), "gap_pct": gap, "opt_min": opt})
    write_csv(root / "optimality-gap" / "results.csv", rows)
    s = section(R.results_payload(root), "optimality-gap")
    assert "BLS-A* is 0.20% from the optimum on average (at most 0.40%)" in s["finding"]["en"]
    assert "ortalama yalnızca %0,20, en çok %0,40 uzakta" in s["finding"]["tr"]


def test_empty_result_files_are_shown_as_no_data(results_dir):
    (results_dir / "queue-awareness" / "results.csv").write_text("", encoding="utf-8")  # the runner's "no rows"
    (results_dir / "vehicles" / "results.csv").write_text("vehicle,algorithm,time_min\n", encoding="utf-8")
    p = R.results_payload(results_dir)
    for sid in ("queue-awareness", "vehicles"):
        s = section(p, sid)
        assert s["status"] == "empty" and s["finding"] is None and s["data"] is None, sid
    assert section(p, "eco-speed")["status"] == "ok"


def test_csv_with_byte_order_mark(results_dir):
    for sid in ("case-studies", "vehicles"):  # as re-saved by a spreadsheet
        f = results_dir / sid / "results.csv"
        f.write_text(f.read_text(encoding="utf-8"), encoding="utf-8-sig")
    p = R.results_payload(results_dir)
    assert section(p, "case-studies")["facts"]["exact_best"] == 2
    v = section(p, "vehicles")
    assert v["facts"]["fastest"] == "sedan75" and "None" not in v["finding"]["en"]
    assert v["tables"][0]["columns"][0] == "vehicle"


def test_rerun_keeps_the_report_of_earlier_runs(tmp_path, monkeypatch):
    from evroute.experiments import runner

    root = make_results(tmp_path / "r")
    report = json.loads((root / "report.json").read_text())
    report["scalability"] = {"status": "failed", "seconds": 60.0, "result": {"error": "MemoryError()"}}
    report["temperature"] = {"status": "failed", "seconds": 2.0, "result": {"error": "RuntimeError('cold')"}}
    (root / "report.json").write_text(json.dumps(report))

    def vehicles(out, quick):
        write_csv(out / "results.csv", [{"vehicle": "sedan75", **row("bls-astar", 470.2)}])
        return {"rows": 1}

    monkeypatch.setitem(runner.EXPERIMENTS, "vehicles", vehicles)
    runner.run_experiments(["vehicles"], root, quick=True)
    merged = json.loads((root / "report.json").read_text())
    assert set(merged) == set(R.SECTION_IDS)
    assert merged["scalability"]["status"] == "failed" and merged["temperature"]["status"] == "failed"
    assert merged["vehicles"]["status"] == "ok" and merged["vehicles"]["quick"] is True and merged["vehicles"]["finished"]
    p = embedded_payload((root / "results.html").read_text(encoding="utf-8"))
    assert section(p, "scalability")["status"] == "failed" and section(p, "temperature")["status"] == "failed"
    others = len(R.SECTION_IDS) - 3  # every experiment but scalability, temperature and vehicles: 1.5 s each
    assert p["run"]["total_seconds"] == pytest.approx(others * 1.5 + 60.0 + 2.0 + merged["vehicles"]["seconds"], abs=0.02)
    # a malformed report is replaced, not merged
    (root / "report.json").write_text("[]")
    runner.run_experiments(["vehicles"], root, quick=True, panel=False)
    assert list(json.loads((root / "report.json").read_text())) == ["vehicles"]


def test_case_study_metadata_and_viewer_link(results_dir, tmp_path):
    p = R.results_payload(results_dir, out_dir=results_dir)
    cs = section(p, "case-studies")["data"]["cases"]
    assert cs[0]["title"] == "Karaman → İstanbul" and cs[0]["currency"] == "TRY"
    assert cs[0]["viewer"] == "case-studies/turkey-karaman-istanbul/viewer.html"
    assert cs[1]["viewer"] is None
    p2 = R.results_payload(results_dir, out_dir=tmp_path / "elsewhere")
    assert section(p2, "case-studies")["data"]["cases"][0]["viewer"].startswith("../results/case-studies/")
    rows = cs[0]["rows"]
    assert [r["algorithm"] for r in rows] == ["naive", "greedy", "station-graph", "bls", "bls-astar", "cfp-astar"]
    assert next(r for r in rows if r["algorithm"] == "cfp-astar")["best"] is True


# ------------------------------------------------------------------- HTML
def test_build_html_is_self_contained(results_dir, tmp_path):
    out = R.build_results_html(results_dir, tmp_path / "panel.html", lang="en")
    html = out.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and '<html lang="en">' in html
    assert not re.search(r'<(script|link|img)[^>]+(src|href)="https?:', html), "external reference"
    assert "<!-- evroute:" not in html and 'src="js/' not in html
    payload = embedded_payload(html)
    assert payload["format"] == R.FORMAT and payload["lang"] == "en"
    for a, (light, dark) in ALGORITHM_COLORS.items():
        assert f"--a-{a}: {light};" in html and f"--a-{a}: {dark};" in html
    # the viewer's design tokens are inlined, not copied
    theme = (R.WEB_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    assert theme.strip()[:200] in html
    assert "<title>Experiment results · EVRoute</title>" in html


def test_payload_cannot_break_out_of_the_script_tag(results_dir, tmp_path):
    write_csv(results_dir / "evil" / "results.csv", [{"name": "</script><script>alert(1)</script>", "x": 1}])
    html = R.render_results_html(R.results_payload(results_dir))
    assert "</script><script>alert(1)" not in html
    assert embedded_payload(html)["sections"][-1]["tables"][0]["rows"][0][0].startswith("</script>")


def test_cli_writes_default_file(results_dir, capsys):
    assert R.main([str(results_dir), "--lang", "en"]) == 0
    assert (results_dir / "results.html").exists()
    assert "wrote" in capsys.readouterr().out
    assert R.main([str(results_dir / "nope")]) == 2


def test_real_quick_experiments_end_to_end(tmp_path):
    from evroute.experiments.runner import run_experiments

    run_experiments(["vehicles", "queue-awareness"], tmp_path / "res", quick=True)
    out = R.build_results_html(tmp_path / "res", tmp_path / "res" / "results.html")
    p = embedded_payload(out.read_text(encoding="utf-8"))
    for sid in ("vehicles", "queue-awareness"):
        s = section(p, sid)
        assert s["status"] == "ok" and s["finding"]["en"], sid
    assert section(p, "scalability")["status"] == "missing"
    assert p["checks"]["passed"] == p["checks"]["total"] > 0


# ------------------------------------------------------- JavaScript / browser
def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not installed")
def test_javascript_files_parse():
    for rel in R.JS_FILES:
        path = (R.PANEL_DIR / rel).resolve()
        res = subprocess.run([_node(), "--check", str(path)], capture_output=True, text=True)
        assert res.returncode == 0, f"{rel}: {res.stderr}"


def _has_playwright() -> bool:
    if _node() is None:
        return False
    res = subprocess.run([_node(), "-e", "require('playwright')"], capture_output=True, text=True)
    return res.returncode == 0


BROWSER_SCRIPT = r"""
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const out = { errors: [] };
  for (const vp of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport: vp });
    page.on('console', (m) => { if (m.type() === 'error') out.errors.push(m.text()); });
    page.on('pageerror', (e) => out.errors.push(e.message));
    await page.goto('file://' + process.argv[2]);
    await page.waitForFunction(() => window.EVR_RESULTS_READY === true, null, { timeout: 15000 });
    await page.waitForTimeout(200);
    out['svgs' + vp.width] = await page.$$eval('svg.rc-svg', (n) => n.filter((s) => s.childElementCount > 2).length);
    out['overflow' + vp.width] = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    const svg = await page.$('#sec-queue-awareness .cc-chart svg');
    await svg.scrollIntoViewIfNeeded();
    const b = await svg.boundingBox();
    await page.mouse.move(b.x + b.width * 0.3, b.y + b.height * 0.5);
    out['tip' + vp.width] = await page.$eval('#sec-queue-awareness .cc-chart .rc-tip', (t) => !t.hidden && t.textContent.length > 0);
    await page.close();
  }
  console.log(JSON.stringify(out));
  await browser.close();
})().catch((e) => { console.log(JSON.stringify({ fatal: String(e) })); });
"""


@pytest.mark.slow
@pytest.mark.skipif(not _has_playwright(), reason="playwright for node not installed")
def test_dashboard_renders_in_headless_chromium(results_dir, tmp_path):
    out = R.build_results_html(results_dir, tmp_path / "panel.html")
    script = tmp_path / "check.js"
    script.write_text(BROWSER_SCRIPT, encoding="utf-8")
    res = subprocess.run([_node(), str(script), str(out)], capture_output=True, text=True, timeout=120,
                         cwd=str(tmp_path), env={**__import__("os").environ, "NODE_PATH": _node_path()})
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert "fatal" not in data, data
    assert data["errors"] == []
    assert data["svgs1280"] >= 15 and data["svgs390"] >= 15
    assert data["overflow1280"] <= 0 and data["overflow390"] <= 0
    assert data["tip1280"] and data["tip390"]


def _node_path() -> str:
    res = subprocess.run([_node(), "-e", "console.log(require.resolve('playwright'))"], capture_output=True, text=True)
    p = Path(res.stdout.strip())
    return str(p.parent.parent) if p.exists() else ""
