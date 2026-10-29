"""Reproducible experiments behind every table and figure of the paper.

Run everything::

    python -m evroute.experiments.runner            # full (tens of minutes, real-data about 1.5 h more)
    python -m evroute.experiments.runner --quick    # smoke run (about a minute)
    evroute experiments scalability optimality-gap -o results/

Each experiment writes into ``<out>/<id>/``: ``results.csv`` (one row per
run), ``summary.json``, ``table.tex`` (booktabs) and figures (PDF + PNG) when
seaborn is installed.  All randomness is seeded.
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


def _can_plot() -> bool:
    """``True`` if seaborn (which draws every figure) is installed."""
    from ..viz.style import has_seaborn

    return has_seaborn()


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
        if _can_plot():
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
    if _can_plot():
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
    if _can_plot():
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
    if _can_plot() and rows:
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
    if _can_plot():
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
    if _can_plot() and rows:
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
        if _can_plot():
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
    if _can_plot() and rows:
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


# ============================================================ E11 tariffs
#: Order and names of the tariffs compared by :func:`exp_tariffs`.
TARIFF_NAMES = ("flat", "tier", "peak", "tier+peak")
#: SoC above which charged energy counts as "above 80 %" in the tariff rows.
TARIFF_HIGH_SOC = 0.8


def tariff_set() -> dict[str, Any]:
    """The tariffs of the ``tariffs`` experiment, built from the illustrative
    :data:`~evroute.generators.presets.DEMO_TARIFF`: flat prices, its 80 % tier
    alone, its evening peak alone (no extra high-SoC factor) and both."""
    from ..generators.presets import DEMO_TARIFF
    from ..scenario import Tariff

    d = DEMO_TARIFF
    return {
        "flat": Tariff(),
        "tier": Tariff(high_soc=d.high_soc, high_soc_factor=d.high_soc_factor),
        "peak": Tariff(peak_hours=d.peak_hours, peak_factor=d.peak_factor),
        "tier+peak": d,
    }


def tariff_metrics(plan, capacity_kwh: float, vot_per_s: float) -> dict[str, Any]:
    """What a tariff changes in ``plan``: the highest SoC charged to, the energy
    charged above :data:`TARIFF_HIGH_SOC`, voluntary holds, sessions priced in
    the peak and ``money_wear`` = generalised cost minus the value of time
    (weighted charging money + battery-wear money)."""
    hi = TARIFF_HIGH_SOC * capacity_kwh
    stops = plan.stops if plan.feasible else []
    return {
        "max_soc": max((s.soc_out for s in stops), default=math.nan),
        "above80_kwh": sum(max(0.0, s.energy_out_kwh - max(s.energy_in_kwh, hi)) for s in stops),
        "holds": sum(1 for s in stops if s.hold_s > 0.0),
        "hold_min": plan.hold_time_s / 60.0,
        "peak_stops": sum(1 for s in stops if s.peak),
        "money_wear": plan.generalized_cost - vot_per_s * plan.total_time_s if plan.feasible else math.nan,
    }


def exp_tariffs(out: Path, quick: bool) -> dict[str, Any]:
    """Charging tariffs ("the 80 % rule", peak hours): time- and cost-optimal
    plans and the (time, money) Pareto front by departure time.

    Every search runs on a 5 % SoC charging grid (so that the exact fronts
    fit the viewer's label budget); a front stopped by the budget is written
    with ``complete = 0``.  ``results.csv`` has one row per plan (``kind`` =
    ``time``, ``cost`` or ``pareto``), ``fronts.csv`` one row per front member.
    """
    from ..viz.export import VIEWER_OPTIONS

    tariffs = tariff_set()
    names = ["flat", "tier+peak"] if quick else list(TARIFF_NAMES)
    hours = [14, 18] if quick else list(range(6, 23, 2))
    step = 0.05
    budget = 200_000 if quick else int(VIEWER_OPTIONS["pareto"]["max_labels"])
    crit = ("time", "money")
    rows: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    showcase: dict[str, tuple[float, int, Any, list[Any]]] = {}
    for case in CASES[:2]:
        for h in hours:
            for name in names:
                sc = _scenario(case, departure=f"{h:02d}:00", tariff=tariffs[name]).with_preferences(charge_step_soc=step)
                P = sc.prepare()
                key = {"case": case, "departure_h": h, "tariff": name}
                plans = {"time": safe_plan(sc, "bls-astar"),
                         "cost": safe_plan(sc.with_preferences(objective="cost"), "bls-astar")}
                for kind, p in plans.items():
                    if p is not None:
                        rows.append({**plan_row(p, **key, kind=kind), **tariff_metrics(p, P.capacity, P.vot_per_s)})
                pf = safe_plan(sc.with_preferences(objective="cost"), "pareto", criteria=crit, max_labels=budget)
                if pf is None:
                    continue
                ex = pf.stats.extra if pf.stats else {}
                front = sorted(pf.alternatives or ([pf] if pf.feasible else []), key=lambda a: (a.total_time_s, a.money))
                st = pf.stats
                rows.append({**key, "kind": "pareto", "algorithm": pf.algorithm, "feasible": int(bool(front)),
                             "front_size": len(front), "complete": int(ex.get("status") == "exhausted"),
                             "runtime_ms": st.runtime_s * 1000.0 if st else math.nan,
                             "settled": st.settled if st else 0, "generated": st.generated if st else 0,
                             "status": str(ex.get("status", ""))})
                for i, a in enumerate(front):
                    m = tariff_metrics(a, P.capacity, P.vot_per_s)
                    members.append({**key, "member": i, "time_min": a.total_time_s / 60.0, "money": a.money,
                                    "soh_pct": a.soh_total_pct, "stops": a.num_stops, "holds": m["holds"],
                                    "hold_min": m["hold_min"], "max_soc": m["max_soc"], "above80_kwh": m["above80_kwh"]})
                # the viewer shows the trip whose cost-optimal plan saves most under the full tariff:
                # the cost objective's BLS-A* plan and the front (its fastest member is the time optimum)
                t, c = plans["time"], plans["cost"]
                if name == names[-1] and t is not None and c is not None and t.feasible and c.feasible:
                    gain = t.money - c.money
                    if case not in showcase or gain > showcase[case][0]:
                        showcase[case] = (gain, h, sc.with_preferences(objective="cost"), [c, pf])
            print(f"  tariffs: {case} {h:02d}:00 done")
        write_csv(out / "results.csv", rows)  # after every case: a long run keeps what it measured
        write_csv(out / "fronts.csv", members)
    summary: dict[str, Any] = {"tariffs": {n: tariffs[n].to_dict() for n in names}, "high_soc": TARIFF_HIGH_SOC,
                               "charge_step_soc": step, "pareto_max_labels": budget, "criteria": list(crit),
                               "cases": {}}
    for case in CASES[:2]:
        per: dict[str, Any] = {}
        for name in names:
            sel = {k: [r for r in rows if r["case"] == case and r["tariff"] == name and r["kind"] == k]
                   for k in ("time", "cost", "pareto")}
            per[name] = {
                "time_money_mean": describe(r["money"] for r in sel["time"] if r["feasible"]).get("mean"),
                "cost_money_mean": describe(r["money"] for r in sel["cost"] if r["feasible"]).get("mean"),
                "cost_holds": sum(r["holds"] for r in sel["cost"]),
                "front_sizes": [r["front_size"] for r in sel["pareto"]],
                "incomplete_fronts": sum(1 for r in sel["pareto"] if not r["complete"]),
            }
        if case in showcase:
            per["viewer_departure_h"] = showcase[case][1]
        summary["cases"][case] = per
    write_json(out / "summary.json", summary)
    tex = []
    for case, per in summary["cases"].items():
        for name in names:
            v = per[name]
            sizes = v["front_sizes"]
            tex.append([case.replace("turkey-", ""), name, v["time_money_mean"], v["cost_money_mean"], v["cost_holds"],
                        f"{min(sizes)}--{max(sizes)}" if sizes else "--", v["incomplete_fronts"]])
    (out / "table.tex").write_text(
        latex_table(tex, ["case", "tariff", "money (time-opt.)", "money (cost-opt.)", "holds", "front size", "incomplete"],
                    "Charging tariffs: mean charging cost of the time- and cost-optimal plans over the departures, "
                    "voluntary holds and (time, money) front sizes.", "tab:tariffs", "llrrrrr")
    )
    for case, (_, h, sc, plans) in showcase.items():
        try:
            from ..viz.export import build_viewer_html

            build_viewer_html(sc, [p for p in plans if p is not None], out / case / "viewer.html", lang="tr",
                              title=f"{case_title(case)} · {h:02d}:00")
        except Exception as exc:
            print(f"  ! viewer for {case}: {exc}")
    if _can_plot() and rows:
        for case in CASES[:2]:
            series = {}
            for name in names:
                sel = sorted((r for r in rows if r["case"] == case and r["tariff"] == name and r["kind"] == "time"),
                             key=lambda r: r["departure_h"])
                series[name] = ([r["departure_h"] for r in sel], [r["money"] if r["feasible"] else math.nan for r in sel])
            plots.line_plot(series, "departure hour", "charging cost of the fastest plan", out / f"fig_money_{case}")
    return {"rows": len(rows), "members": len(members)}


# ========================================================= E12 real data
#: Trips of the ``real-data`` experiment: id, the preset on the approximate
#: network of the case study and its counterpart on the OpenStreetMap network
#: (same origin, destination, vehicle, charge, departure and temperature).
REAL_TRIPS: tuple[tuple[str, str, str], ...] = (
    ("karaman-istanbul", "turkey-karaman-istanbul", "turkey-osm-karaman-istanbul"),
    ("antalya-ankara", "turkey-antalya-ankara", "turkey-osm-antalya-ankara"),
    ("istanbul-ankara", "turkey-istanbul-ankara-rush", "turkey-osm-istanbul-ankara"),
    ("ankara-adana", "turkey-ankara-adana-evening", "turkey-osm-ankara-adana"),
    ("karaman-mersin", "turkey-karaman-mersin", "turkey-osm-karaman-mersin"),
    ("izmir-erzurum", "turkey-izmir-erzurum", "turkey-osm-izmir-erzurum"),
)
#: The three data sets: (a) the approximate network with its synthetic
#: stations, (b) the OpenStreetMap network with the OSM charging stations,
#: (c) the OSM network with the hybrid set (OSM stations plus flagged
#: synthetic DC gap-fillers).
REAL_NETWORKS: tuple[str, ...] = ("approx", "osm", "hybrid")
REAL_EXACT: tuple[str, ...] = ("bls-astar", "cfp-astar")
REAL_HEURISTICS: tuple[str, ...] = ("greedy", "station-graph", "genetic")
#: Wall-clock budget of one heuristic run on the OpenStreetMap data sets [s]
#: (``None``: no budget).  A run still going when it is spent is stopped and
#: recorded with the status :data:`OVER_BUDGET` and the time it had run (a
#: lower bound of its runtime); the exact planners have no budget.
REAL_HEURISTIC_BUDGET_S: float | None = 600.0
OVER_BUDGET = "not run: over the time budget"


def plan_within_budget(scenario, algorithm: str, budget_s: float | None, **options: Any) -> tuple[Any, float, bool]:
    """Run :func:`safe_plan` in a forked child process, stopped after ``budget_s`` seconds of wall-clock time.

    Returns ``(plan, seconds, over)``: the plan (``None`` if the planner failed
    or ran over the budget), the wall-clock time the child ran and whether the
    budget stopped it.  The child runs the same code on a copy-on-write copy of
    ``scenario`` and sends the plan back pickled, so the plan (and the runtime
    in its statistics) is the one :func:`safe_plan` returns in this process.
    Without a budget, or where processes cannot be forked (Windows), the plan
    runs here and is never stopped.
    """
    import multiprocessing
    import pickle

    t0 = time.perf_counter()
    if budget_s is None or "fork" not in multiprocessing.get_all_start_methods():
        return safe_plan(scenario, algorithm, **options), time.perf_counter() - t0, False
    ctx = multiprocessing.get_context("fork")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_plan_child, args=(send, scenario, algorithm, options), daemon=True)
    proc.start()
    send.close()  # the child holds the sending end: EOF here if it dies without a plan
    try:
        if not recv.poll(max(0.0, budget_s)):
            return None, time.perf_counter() - t0, True
        try:
            return pickle.loads(recv.recv_bytes()), time.perf_counter() - t0, False
        except EOFError:  # the child died (it printed why)
            return None, time.perf_counter() - t0, False
    finally:
        if proc.is_alive():
            proc.terminate()
        proc.join()
        recv.close()


def _plan_child(conn, scenario, algorithm: str, options: dict[str, Any]) -> None:
    """Body of the child process of :func:`plan_within_budget`."""
    import pickle

    try:
        conn.send_bytes(pickle.dumps(safe_plan(scenario, algorithm, **options), protocol=pickle.HIGHEST_PROTOCOL))
    finally:
        conn.close()


def over_budget_row(algorithm: str, seconds: float, **extra: Any) -> dict[str, Any]:
    """CSV record of a run stopped by the time budget: the columns of
    :func:`~evroute.experiments.common.plan_row`, no plan, ``runtime_ms`` the
    time it had run (a lower bound) and ``status`` :data:`OVER_BUDGET`."""
    nan = math.nan
    return {**extra, "algorithm": algorithm, "feasible": 0, "time_min": nan, "drive_min": nan, "charge_min": nan,
            "queue_min": nan, "distance_km": nan, "stops": "", "energy_kwh": nan, "charged_kwh": nan, "money": nan,
            "soh_pct": nan, "gcost": nan, "arrival_soc": nan, "runtime_ms": seconds * 1000.0, "settled": "",
            "generated": "", "status": OVER_BUDGET}


def real_data_scenario(trip: str, network: str):
    """The scenario of ``trip`` (an id of :data:`REAL_TRIPS`) on ``network``
    (``"approx"``, ``"osm"`` or ``"hybrid"``)."""
    approx, osm = next((a, o) for t, a, o in REAL_TRIPS if t == trip)
    return _scenario(approx) if network == "approx" else _scenario(osm, stations=network)


def exp_real_data(out: Path, quick: bool) -> dict[str, Any]:
    """The same trips on the approximate network and on real OpenStreetMap data.

    Every trip of :data:`REAL_TRIPS` is planned on the three data sets of
    :data:`REAL_NETWORKS` with BLS-A* and CFP-A* (exact) and the heuristics
    greedy, station graph and genetic; ``gap_pct`` is the extra trip time of a
    plan over the continuous optimum (CFP-A*) on the same data set.  The
    heuristics run only where an exact planner found a route (otherwise there
    is no gap to measure).  Quick:
    Karaman -> Mersin only, without greedy and station graph (their label
    trees cover the whole 44 673-node network).  ``results.csv`` has one row
    per plan; the viewer of the flagship trip on OSM data is written to
    ``karaman-istanbul/viewer.html`` (full run).

    Nearly all of a full run is the heuristics on the OSM network: the
    station graph grows one feasible-path label tree (30-50 s each, up to the
    5 million label cap) per station it expands, which took up to almost four
    hours per trip and data set (about seven hours of planning in all),
    greedy and genetic up to a few minutes each; the exact planners take
    seconds (minutes where the OSM stations leave a trip without fast
    chargers and the search runs into its label budget).  Every heuristic run on the OSM data
    sets therefore has a wall-clock budget of :data:`REAL_HEURISTIC_BUDGET_S`
    (see :func:`plan_within_budget`); a run over it gets a row with the
    status :data:`OVER_BUDGET`, ``runtime_ms`` the time it had run and no
    plan, so a full run takes about an hour and a half on one core.  The
    trips are independent, so they can be run in parallel processes (one per
    trip, by narrowing :data:`REAL_TRIPS`).
    """
    trips = [t for t in REAL_TRIPS if t[0] == "karaman-mersin"] if quick else list(REAL_TRIPS)
    algos = REAL_EXACT + (("genetic",) if quick else REAL_HEURISTICS)
    rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {"trips": {}, "networks": list(REAL_NETWORKS), "algorithms": list(algos),
                               "reference": "cfp-astar", "heuristic_budget_s": REAL_HEURISTIC_BUDGET_S}
    for trip, approx_preset, osm_preset in trips:
        per: dict[str, Any] = {"presets": {"approx": approx_preset, "osm": osm_preset}}
        for net in REAL_NETWORKS:
            sc = real_data_scenario(trip, net)
            P = sc.prepare()
            info = {"trip": trip, "network": net, "nodes": sc.network.num_nodes, "edges": sc.network.num_edges,
                    "stations": len(sc.stations), "dc_stations": sum(1 for s in sc.stations if s.kind != "AC")}
            plans: dict[str, Any] = {}
            over: dict[str, float] = {}  # heuristic -> seconds it had run when the budget stopped it
            for a in algos:
                if a not in REAL_EXACT and not any(p.feasible for p in plans.values()):
                    # no exact optimum, no gap: and on the OSM network a heuristic would
                    # spend its time on label trees that hit their 5 million label cap
                    continue
                if a in REAL_EXACT or net == "approx":
                    p = safe_plan(P, a, trace=a in REAL_EXACT and trip == "karaman-istanbul")
                else:
                    p, secs, stopped = plan_within_budget(P, a, REAL_HEURISTIC_BUDGET_S)
                    if stopped:
                        over[a] = secs
                        print(f"  real-data: {trip} on {net}: {a} {OVER_BUDGET} ({secs:.0f} s)")
                if p is not None:
                    plans[a] = p
            ref = plans.get("cfp-astar")
            ref_t = ref.total_time_s if ref is not None and ref.feasible else math.nan
            for a in algos:
                if a in over:
                    rows.append({**over_budget_row(a, over[a], **info), "gap_pct": math.nan, "trees": ""})
                    continue
                p = plans.get(a)
                if p is None:
                    continue
                gap = 100.0 * (p.total_time_s / ref_t - 1.0) if p.feasible and math.isfinite(ref_t) else math.nan
                trees = (p.stats.extra or {}).get("trees") if p.stats else None
                rows.append({**plan_row(p, **info), "gap_pct": gap, "trees": trees if trees is not None else ""})
            per[net] = {**{k: v for k, v in info.items() if k not in ("trip", "network")},
                        "feasible": {a: bool(p.feasible) for a, p in plans.items()},
                        **({"over_budget": {a: round(s, 1) for a, s in over.items()}} if over else {})}
            if trip == "karaman-istanbul" and net == "osm" and not quick:
                try:
                    from ..viz.export import build_viewer_html

                    build_viewer_html(sc, list(plans.values()), out / trip / "viewer.html", lang="tr")
                except Exception as exc:
                    print(f"  ! viewer for {trip}: {exc}")
                if _can_plot():
                    try:
                        from .figures import case_study_figures

                        case_study_figures(sc, out / trip, plans=list(plans.values()))
                    except Exception as exc:  # figures are optional
                        print(f"  ! figures for {trip}: {exc}")
            write_csv(out / "results.csv", rows)  # after every data set: a long run keeps what it measured
            print(f"  real-data: {trip} on {net} done")
        summary["trips"][trip] = per
    write_json(out / "summary.json", summary)
    tex = []
    for r in rows:
        if r["algorithm"] in REAL_EXACT:
            tex.append([r["trip"], r["network"], r["algorithm"], r["time_min"] if r["feasible"] else "--",
                        r["distance_km"], r["stops"], r["charge_min"], r["money"], r["runtime_ms"] / 1000.0, r["settled"]])
    (out / "table.tex").write_text(
        latex_table(tex, ["trip", "data", "algorithm", "time [min]", "distance [km]", "stops", "charge [min]", "money",
                          "runtime [s]", "labels"],
                    "The same trips on the approximate network with synthetic stations (approx), on OpenStreetMap "
                    "roads with the OSM charging stations (osm) and with OSM plus synthetic DC gap-fillers (hybrid). "
                    "Road network and stations: © OpenStreetMap contributors (ODbL).", "tab:real-data", "lllrrrrrrr")
    )
    return {"rows": len(rows), "trips": len(trips)}


def case_title(case: str) -> str:
    """``turkey-karaman-istanbul`` -> ``Karaman → İstanbul`` (from the preset's defaults)."""
    from ..generators.presets import PRESETS

    d = (PRESETS[case].info().get("defaults") or {}) if case in PRESETS else {}
    return f"{d['origin']} → {d['destination']}" if d.get("origin") and d.get("destination") else case


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
    "tariffs": exp_tariffs,
    "real-data": exp_real_data,
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
