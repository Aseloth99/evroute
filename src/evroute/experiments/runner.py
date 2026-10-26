"""Reproducible experiments behind every table and figure of the paper.

Run everything::

    python -m evroute.experiments.runner            # full (tens of minutes)
    python -m evroute.experiments.runner --quick    # smoke run (about a minute)
    evroute experiments scalability optimality-gap -o results/

Each experiment writes into ``<out>/<id>/``: ``results.csv`` (one row per
run), ``summary.json``, ``table.tex`` (booktabs) and figures (PDF + PNG) when
matplotlib is available.  All randomness is seeded.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import plots
from .common import describe, ensure_dir, latex_table, plan_row, safe_plan, sample_od_pairs, write_csv, write_json

EXACT_TIME = "cfp"  # reference optimum for time (continuous charging)
CASES = [
    "turkey-karaman-istanbul",
    "turkey-antalya-ankara",
    "turkey-izmir-erzurum",
    "turkey-istanbul-antalya-winter",
]
ALL_ALGOS = ["naive", "greedy", "station-graph", "genetic", "bls", "bls-astar", "cfp", "cfp-astar"]


def _scenario(name: str, **overrides: Any):
    from ..generators.presets import get_scenario

    return get_scenario(name, **overrides)


def _has_mpl() -> bool:
    try:
        import matplotlib  # noqa: F401

        return True
    except Exception:
        return False


# =============================================================== E1 cases
def exp_case_studies(out: Path, quick: bool) -> dict[str, Any]:
    """All algorithms on the Turkey case studies (Table 2, Figs 1-3)."""
    rows = []
    cases = CASES[:2] if quick else CASES
    algos = ["naive", "greedy", "station-graph", "bls-astar", "cfp-astar"] if quick else ALL_ALGOS
    for case in cases:
        sc = _scenario(case)
        plans = []
        for a in algos:
            p = safe_plan(sc, a, trace=True) if a not in ("genetic",) else safe_plan(sc, a)
            if p is None:
                continue
            plans.append(p)
            rows.append(plan_row(p, case=case))
        if _has_mpl():
            try:
                from .figures import case_study_figures

                case_study_figures(sc, out / case, plans=plans)
            except Exception as exc:  # figures are optional
                print(f"  ! figures for {case}: {exc}")
        try:
            from ..viz.export import build_viewer_html

            build_viewer_html(sc, plans, out / case / "viewer.html", lang="tr")
        except Exception as exc:
            print(f"  ! viewer for {case}: {exc}")
    write_csv(out / "results.csv", rows)
    tex_rows = [
        [r["case"].replace("turkey-", ""), r["algorithm"], "yes" if r["feasible"] else "no",
         r["time_min"] if r["feasible"] else "--", r["stops"], r["charge_min"], r["money"], r["runtime_ms"]]
        for r in rows
    ]
    (out / "table.tex").write_text(
        latex_table(tex_rows, ["case", "algorithm", "feas.", "time [min]", "stops", "charge [min]", "money", "runtime [ms]"],
                    "Turkey intercity case studies: all algorithms.", "tab:cases", "llrrrrrr")
    )
    return {"rows": len(rows)}


# =========================================================== E2 scaling
def exp_scalability(out: Path, quick: bool) -> dict[str, Any]:
    """Runtime and search effort versus network size (Fig. 4)."""
    from ..generators.synthetic import country_scenario

    sizes = [800, 2000] if quick else [1000, 2500, 5000, 10000, 20000, 40000]
    pairs_per = 2 if quick else 8
    algos = ["station-graph", "bls", "bls-astar", "cfp", "cfp-astar"]
    rows = []
    for size in sizes:
        base = country_scenario(nodes_target=size, seed=size)
        base = base.with_preferences(time_dependent_waits=False)
        pairs = sample_od_pairs(base, pairs_per, seed=size, min_km=0.45 * _diameter_km(base), named_only=False)
        for i, (o, d) in enumerate(pairs):
            sc = base.with_query(origin=o, destination=d, initial_soc=0.6)
            for a in algos:
                if quick is False and a == "bls" and size > 20000:
                    continue
                p = safe_plan(sc, a)
                if p is None:
                    continue
                rows.append(plan_row(p, nodes=base.network.num_nodes, pair=i))
        write_csv(out / "results.csv", rows)  # after every size: a long run keeps what it measured
        print(f"  scalability: {base.network.num_nodes} nodes done")
    summary = {}
    for a in algos:
        xs, ys, ss = [], [], []
        for n in sorted({r["nodes"] for r in rows}):
            sel = [r for r in rows if r["algorithm"] == a and r["nodes"] == n and r["feasible"]]
            if sel:
                xs.append(n)
                ys.append(sum(r["runtime_ms"] for r in sel) / len(sel))
                ss.append(sum(r["settled"] for r in sel) / len(sel))
        summary[a] = {"nodes": xs, "runtime_ms": ys, "settled": ss}
    write_json(out / "summary.json", summary)
    if _has_mpl():
        plots.line_plot({a: (v["nodes"], v["runtime_ms"]) for a, v in summary.items()}, "nodes", "mean runtime [ms]",
                        out / "fig_runtime", logx=True, logy=True)
        plots.line_plot({a: (v["nodes"], v["settled"]) for a, v in summary.items()}, "nodes", "settled labels",
                        out / "fig_settled", logx=True, logy=True)
    return summary


def _diameter_km(sc) -> float:
    x0, y0, x1, y1 = sc.network.bbox()
    return math.hypot(x1 - x0, y1 - y0) / 1000.0


# ====================================================== E3 optimality gap
def exp_optimality_gap(out: Path, quick: bool) -> dict[str, Any]:
    """Relative gap of heuristics to the continuous optimum (Fig. 5)."""
    base = _scenario("turkey-karaman-istanbul").with_preferences(time_dependent_waits=False)
    n = 6 if quick else 60
    pairs = sample_od_pairs(base, n, seed=42, min_km=300)
    heur = ["greedy", "station-graph", "genetic", "bls-astar"]
    rows = []
    import random

    rng = random.Random(7)
    for i, (o, d) in enumerate(pairs):
        sc = base.with_query(origin=o, destination=d, initial_soc=round(rng.uniform(0.3, 0.9), 2))
        ref = safe_plan(sc, EXACT_TIME)
        if ref is None or not ref.feasible:
            continue
        for a in heur:
            p = safe_plan(sc, a)
            if p is None:
                continue
            gap = (p.total_time_s - ref.total_time_s) / ref.total_time_s * 100.0 if p.feasible else math.nan
            rows.append({**plan_row(p, pair=i), "gap_pct": gap, "opt_min": ref.total_time_s / 60.0})
    write_csv(out / "results.csv", rows)
    groups = {a: [r["gap_pct"] for r in rows if r["algorithm"] == a and math.isfinite(r["gap_pct"])] for a in heur}
    feas = {a: sum(r["feasible"] for r in rows if r["algorithm"] == a) / max(1, sum(1 for r in rows if r["algorithm"] == a)) for a in heur}
    summary = {a: {**describe(groups[a]), "feasible_rate": feas[a]} for a in heur}
    write_json(out / "summary.json", summary)
    (out / "table.tex").write_text(
        latex_table([[a, f"{100 * summary[a]['feasible_rate']:.0f}\\%", summary[a].get("mean", math.nan), summary[a].get("p95", math.nan), summary[a].get("max", math.nan)] for a in heur],
                    ["algorithm", "feasible", "mean gap [\\%]", "p95 gap [\\%]", "max gap [\\%]"],
                    "Optimality gap of heuristics with respect to the continuous optimum (CFP).", "tab:gap")
    )
    if _has_mpl():
        plots.box_plot(groups, "gap to optimum [%]", out / "fig_gap")
    return summary


# ===================================================== E4 grid resolution
def exp_grid_resolution(out: Path, quick: bool) -> dict[str, Any]:
    """BLS converges to CFP as the charging-target grid is refined (Fig. 6)."""
    steps = [0.1, 0.05, 0.02] if quick else [0.2, 0.1, 0.05, 0.02, 0.01, 0.005]
    cases = CASES[:1] if quick else CASES[:3]
    rows = []
    for case in cases:
        sc = _scenario(case).with_preferences(time_dependent_waits=False)
        ref = safe_plan(sc, EXACT_TIME)
        if ref is None or not ref.feasible:
            continue
        for step in steps:
            for targets in ("grid", "grid+breaks"):
                p = safe_plan(sc.with_preferences(charge_step_soc=step), "bls-astar", targets=targets)
                if p is None or not p.feasible:
                    continue
                rows.append({**plan_row(p, case=case, step=step, targets=targets),
                             "gap_s": p.total_time_s - ref.total_time_s})
    write_csv(out / "results.csv", rows)
    if _has_mpl() and rows:
        series = {}
        for case in cases:
            for targets in ("grid", "grid+breaks"):
                sel = sorted((r for r in rows if r["case"] == case and r["targets"] == targets), key=lambda r: r["step"])
                if sel:
                    series[f"{case.replace('turkey-', '')} ({targets})"] = ([r["step"] * 100 for r in sel], [max(r["gap_s"], 1e-3) for r in sel])
        plots.line_plot(series, "charging grid step [% SoC]", "gap to CFP [s]", out / "fig_grid_gap", logx=True, logy=True)
    return {"rows": len(rows)}


# ======================================================== E5 temperature
def exp_temperature(out: Path, quick: bool) -> dict[str, Any]:
    """Trip time, stops and energy versus ambient temperature (Fig. 7)."""
    temps = [-10, 0, 10, 20, 30] if quick else [-15, -10, -5, 0, 5, 10, 15, 20, 25, 30, 35]
    rows = []
    for pre in (True, False):
        for t in temps:
            sc = _scenario("turkey-karaman-istanbul").with_conditions(temperature_c=t, preconditioning=pre)
            p = safe_plan(sc, "bls-astar")
            if p is None:
                continue
            rows.append(plan_row(p, temperature_c=t, preconditioning=int(pre)))
    write_csv(out / "results.csv", rows)
    if _has_mpl():
        series = {}
        for pre in (1, 0):
            sel = [r for r in rows if r["preconditioning"] == pre and r["feasible"]]
            series["preconditioned" if pre else "cold battery"] = ([r["temperature_c"] for r in sel], [r["time_min"] for r in sel])
        plots.line_plot(series, "ambient temperature [°C]", "trip time [min]", out / "fig_temperature")
        sel = [r for r in rows if r["preconditioning"] == 1 and r["feasible"]]
        plots.line_plot({"energy": ([r["temperature_c"] for r in sel], [r["energy_kwh"] for r in sel])},
                        "ambient temperature [°C]", "net energy [kWh]", out / "fig_temperature_energy")
    return {"rows": len(rows)}


# ================================================== E6 queue awareness
def exp_queue_awareness(out: Path, quick: bool) -> dict[str, Any]:
    """Value of planning with expected queues (Table 3)."""
    from ..evaluate import evaluate

    departures = [8, 16] if quick else [6, 8, 10, 12, 14, 16, 17, 18, 20]
    cases = CASES[:1] if quick else CASES[:3]
    rows = []
    for case in cases:
        for h in departures:
            sc = _scenario(case).with_query(departure_s=h * 3600.0)
            aware = safe_plan(sc, "bls-astar")
            blind_plan = safe_plan(sc.with_preferences(queue_aware=False), "bls-astar")
            if not aware or not blind_plan or not aware.feasible or not blind_plan.feasible:
                continue
            truth = sc.prepare()
            blind = evaluate(truth, blind_plan.decision, algorithm="queue-blind")
            rows.append({
                "case": case, "departure_h": h,
                "aware_min": aware.total_time_s / 60.0, "blind_min": blind.total_time_s / 60.0,
                "aware_wait_min": aware.wait_time_s / 60.0, "blind_wait_min": blind.wait_time_s / 60.0,
                "saving_min": (blind.total_time_s - aware.total_time_s) / 60.0,
            })
    write_csv(out / "results.csv", rows)
    summary = describe(r["saving_min"] for r in rows)
    write_json(out / "summary.json", summary)
    if _has_mpl() and rows:
        series = {}
        for case in cases:
            sel = [r for r in rows if r["case"] == case]
            series[case.replace("turkey-", "")] = ([r["departure_h"] for r in sel], [r["saving_min"] for r in sel])
        plots.line_plot(series, "departure hour", "saving vs. queue-blind [min]", out / "fig_queue")
    return summary


# =============================================================== E7 Pareto
def exp_pareto(out: Path, quick: bool) -> dict[str, Any]:
    """Time / money / battery-wear trade-off (Fig. 8)."""
    cases = CASES[:1] if quick else CASES[:2]
    res = {}
    for case in cases:
        sc = _scenario(case).with_preferences(objective="cost", charge_step_soc=0.05 if quick else 0.02)
        p = safe_plan(sc, "pareto", epsilon=0.01 if quick else 0.0)
        if p is None:
            continue
        alts = p.alternatives or [p]
        rows = [plan_row(a, case=case) for a in alts]
        write_csv(out / f"{case}.csv", rows)
        res[case] = {"front_size": len(alts)}
        if _has_mpl():
            plots.scatter_plot([r["time_min"] for r in rows], [r["money"] for r in rows], "trip time [min]",
                               f"charging cost [{sc.currency}]", out / f"fig_pareto_{case}",
                               c=[r["soh_pct"] * 1000 for r in rows], clabel="wear [m%SoH]")
    write_json(out / "summary.json", res)
    return res


# ========================================================= E8 robustness
def exp_robustness(out: Path, quick: bool) -> dict[str, Any]:
    """Probability of stranding versus planning safety factor (Fig. 9)."""
    from ..robustness import safety_sweep

    sc = _scenario("turkey-izmir-erzurum" if not quick else "turkey-karaman-istanbul")
    factors = (1.0, 1.1, 1.2) if quick else (1.0, 1.05, 1.1, 1.15, 1.2, 1.3)
    rows = safety_sweep(sc, factors=factors, n=200 if quick else 2000, seed=0)
    write_csv(out / "results.csv", [dict(r) for r in rows])
    if _has_mpl() and rows:
        xs = [r.get("safety_factor", r.get("factor")) for r in rows]
        plots.line_plot({"P(reserve breach)": (xs, [r.get("p_reserve_breach") for r in rows]),
                         "P(strand)": (xs, [r.get("p_strand") for r in rows])},
                        "planning safety factor", "probability", out / "fig_safety", markers=True)
    return {"rows": len(rows)}


# ========================================================= E9 eco speeds
def exp_eco_speed(out: Path, quick: bool) -> dict[str, Any]:
    """Driving slower can make the trip shorter (fewer/shorter stops)."""
    base = _scenario("turkey-karaman-istanbul")
    n = 5 if quick else 40
    pairs = sample_od_pairs(base, n, seed=11, min_km=300)
    rows = []
    for i, (o, d) in enumerate(pairs):
        for temp in ((5.0,) if quick else (-5.0, 15.0)):
            sc = base.with_query(origin=o, destination=d, initial_soc=0.5).with_conditions(temperature_c=temp)
            fixed = safe_plan(sc, "bls-astar")
            eco = safe_plan(sc.with_preferences(speed_factors=(1.0, 0.9, 0.8)), "bls-astar")
            if not fixed or not eco or not fixed.feasible or not eco.feasible:
                continue
            rows.append({"pair": i, "temperature_c": temp, "fixed_min": fixed.total_time_s / 60.0,
                         "eco_min": eco.total_time_s / 60.0, "fixed_stops": fixed.num_stops, "eco_stops": eco.num_stops,
                         "saving_min": (fixed.total_time_s - eco.total_time_s) / 60.0,
                         "slowed_edges": sum(1 for k in eco.levels if k > 0)})
    write_csv(out / "results.csv", rows)
    summary = {**describe(r["saving_min"] for r in rows), "improved": sum(1 for r in rows if r["saving_min"] > 0.05)}
    write_json(out / "summary.json", summary)
    return summary


# =========================================================== E10 vehicles
def exp_vehicles(out: Path, quick: bool) -> dict[str, Any]:
    """Same trip, different vehicles (Table 4)."""
    from ..vehicle import VEHICLES

    rows = []
    for key in VEHICLES:
        sc = _scenario("turkey-karaman-istanbul", vehicle=key)
        p = safe_plan(sc, "bls-astar")
        if p is not None:
            rows.append(plan_row(p, vehicle=key))
    write_csv(out / "results.csv", rows)
    (out / "table.tex").write_text(
        latex_table([[r["vehicle"], r["time_min"] if r["feasible"] else "--", r["stops"], r["charge_min"], r["energy_kwh"], r["money"]] for r in rows],
                    ["vehicle", "time [min]", "stops", "charge [min]", "energy [kWh]", "money"],
                    "Karaman to Istanbul with different vehicles (BLS-A*).", "tab:vehicles")
    )
    return {"rows": len(rows)}


EXPERIMENTS: dict[str, Callable[[Path, bool], dict[str, Any]]] = {
    "case-studies": exp_case_studies,
    "scalability": exp_scalability,
    "optimality-gap": exp_optimality_gap,
    "grid-resolution": exp_grid_resolution,
    "temperature": exp_temperature,
    "queue-awareness": exp_queue_awareness,
    "pareto": exp_pareto,
    "robustness": exp_robustness,
    "eco-speed": exp_eco_speed,
    "vehicles": exp_vehicles,
}


def run_experiments(
    names: list[str] | None = None,
    out_dir: Path = Path("results"),
    quick: bool = False,
    *,
    panel: bool = True,
    lang: str = "tr",
) -> dict[str, Any]:
    """Run ``names`` (default: all) into ``out_dir``; also write the interactive
    ``results.html`` dashboard (:mod:`evroute.viz.results`) unless ``panel`` is false.

    ``report.json`` describes the whole folder: a re-run of some experiments
    replaces their entries and keeps the others (an earlier failure must not
    disappear); every entry records when it finished and whether it was quick.
    Returns that merged report.
    """
    names = names or list(EXPERIMENTS)
    unknown = [n for n in names if n not in EXPERIMENTS]
    if unknown:
        raise SystemExit(f"unknown experiment(s) {unknown}; choose from {list(EXPERIMENTS)}")
    report_path = ensure_dir(Path(out_dir)) / "report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {}
    if not isinstance(report, dict):
        report = {}
    for name in names:
        fn = EXPERIMENTS[name]
        out = ensure_dir(Path(out_dir) / name)
        t0 = time.perf_counter()
        print(f"[{name}] running{' (quick)' if quick else ''} ...")
        try:
            res = fn(out, quick)
            status = "ok"
        except Exception as exc:
            import traceback

            traceback.print_exc()
            res, status = {"error": repr(exc)}, "failed"
        dt = time.perf_counter() - t0
        print(f"[{name}] {status} in {dt:.1f} s")
        report[name] = {"status": status, "seconds": round(dt, 2), "quick": quick,
                        "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"), "result": res}
    write_json(report_path, report)
    if panel:
        try:
            from ..viz.results import build_results_html

            path = build_results_html(out_dir, Path(out_dir) / "results.html", lang=lang)
            print(f"results panel: {path}")
        except Exception as exc:  # the panel is a convenience; never lose the run for it
            print(f"results panel failed: {exc!r}")
    return report


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser(description="EVRoute paper experiments")
    ap.add_argument("names", nargs="*", help=f"any of: {', '.join(EXPERIMENTS)}")
    ap.add_argument("-o", "--out", default="results")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-panel", action="store_true", help="do not write results.html")
    ap.add_argument("--lang", default="tr", choices=["tr", "en"], help="initial language of results.html")
    a = ap.parse_args()
    run_experiments(a.names or None, Path(a.out), a.quick, panel=not a.no_panel, lang=a.lang)


if __name__ == "__main__":  # pragma: no cover
    main()
