"""Command-line interface: ``evroute <command> ...``.

Examples::

    evroute scenarios
    evroute plan turkey-karaman-istanbul
    evroute plan turkey-karaman-istanbul --from Konya --to Ankara --soc 0.35 -a cfp
    evroute compare turkey-antalya-ankara --temp -5
    evroute viz turkey-karaman-istanbul -o karaman-istanbul.html
    evroute viz corridor -o corridor.html --no-animate --lang en
    evroute serve turkey-karaman-istanbul
    evroute animate turkey-karaman-istanbul -o race.gif
    evroute robustness turkey-izmir-erzurum -n 2000
    evroute experiments --quick -o results/

Every command accepts a preset name or the path of a scenario ``.json`` file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .units import format_clock, format_duration


# ---------------------------------------------------------------- helpers
def _load_scenario(args: argparse.Namespace):
    from .scenario import Scenario

    name = args.scenario
    if name.endswith(".json") and Path(name).exists():
        sc = Scenario.load(name)
    else:
        from .generators.presets import get_scenario

        sc = get_scenario(name)
    return _apply_overrides(sc, args)


def _node(sc, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return sc.network.node_by_name(value)


def _apply_overrides(sc, args: argparse.Namespace):
    q: dict[str, Any] = {}
    if getattr(args, "origin", None):
        q["origin"] = _node(sc, args.origin)
    if getattr(args, "destination", None):
        q["destination"] = _node(sc, args.destination)
    if getattr(args, "soc", None) is not None:
        q["initial_soc"] = args.soc
    if getattr(args, "depart", None):
        hh, mm = args.depart.split(":")
        q["departure_s"] = int(hh) * 3600 + int(mm) * 60
    if q:
        sc = sc.with_query(**q)
    c: dict[str, Any] = {}
    if getattr(args, "temp", None) is not None:
        c["temperature_c"] = args.temp
    if c:
        sc = sc.with_conditions(**c)
    p: dict[str, Any] = {}
    if getattr(args, "objective", None):
        p["objective"] = args.objective
    if getattr(args, "reserve", None) is not None:
        p["reserve_soc"] = args.reserve
    if getattr(args, "arrive", None) is not None:
        p["min_arrival_soc"] = args.arrive
    if getattr(args, "eco", False):
        p["speed_factors"] = (1.0, 0.9, 0.8)
    if getattr(args, "no_queue", False):
        p["queue_aware"] = False
    if getattr(args, "step", None) is not None:
        p["charge_step_soc"] = args.step
    if getattr(args, "safety", None) is not None:
        p["safety_factor"] = args.safety
    if p:
        sc = sc.with_preferences(**p)
    if getattr(args, "vehicle", None):
        from .vehicle import get_vehicle

        sc = sc.with_(vehicle=get_vehicle(args.vehicle))
    return sc


def _algorithms(spec: str | None) -> list[str]:
    from .algorithms import available

    if not spec or spec == "all":
        return available()
    return [a.strip() for a in spec.split(",") if a.strip()]


def _table(rows: list[list[str]], header: list[str], left: int = 1) -> str:
    """Plain-text table; the first ``left`` columns are left-aligned."""
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    line = "  ".join(h.ljust(w) for h, w in zip(header, widths))
    sep = "  ".join("-" * w for w in widths)
    body = [
        "  ".join(str(x).ljust(w) if i < left else str(x).rjust(w) for i, (x, w) in enumerate(zip(r, widths)))
        for r in rows
    ]
    return "\n".join([line, sep, *body])


def _print_plan(plan, sc) -> None:
    print(plan)
    if not plan.feasible:
        return
    net = sc.network
    dep = sc.query.departure_s
    rows = []
    for i, s in enumerate(plan.stops, 1):
        rows.append(
            [
                str(i),
                s.station_name or net.node_name.get(s.node, f"node {s.node}"),
                f"{s.station_kind} {s.station_power_kw:.0f} kW",
                format_clock(dep + s.arrival_s),
                f"{s.wait_s / 60:.0f} min",
                f"{s.charge_s / 60:.0f} min",
                f"{100 * s.soc_in:.0f}% -> {100 * s.soc_out:.0f}%",
                f"{s.money:.2f} {plan.currency}",
            ]
        )
    if rows:
        print(_table(rows, ["#", "station", "charger", "arrive", "queue", "charge", "SoC", "cost"], left=3))
    print(
        f"arrival {format_clock(dep + plan.total_time_s)} | drive {format_duration(plan.drive_time_s)}"
        f" | charge {format_duration(plan.charge_time_s)} | queue+setup "
        f"{format_duration(plan.wait_time_s + plan.setup_time_s)} | battery wear {plan.soh_total_pct:.4f} %SoH"
    )


def _comparison_rows(plans) -> list[list[str]]:
    rows = []
    for p in plans:
        s = p.summary()
        rows.append(
            [
                p.algorithm,
                "yes" if p.feasible else "NO",
                s["total_time"] if p.feasible else "-",
                f"{s['distance_km']:.1f}",
                str(s["stops"]),
                f"{p.money:.2f}",
                f"{100 * p.arrival_soc:.1f}%",
                f"{s['runtime_ms']:.1f}" if s["runtime_ms"] is not None else "-",
                str(s["settled"]) if s["settled"] is not None else "-",
            ]
        )
    return rows


# --------------------------------------------------------------- commands
def cmd_scenarios(args: argparse.Namespace) -> int:
    from .generators.presets import list_scenarios

    rows = [[s["name"], s.get("description", "")] for s in list_scenarios()]
    print(_table(rows, ["name", "description"], left=2))
    return 0


def cmd_algorithms(args: argparse.Namespace) -> int:
    from .algorithms import describe

    rows = [[d["name"], d.get("title", ""), d.get("exact", d.get("error", ""))] for d in describe()]
    print(_table(rows, ["name", "title", "guarantee"], left=3))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from .algorithms import plan

    sc = _load_scenario(args)
    result = plan(sc, args.algorithm)
    _print_plan(result, sc)
    if args.json:
        Path(args.json).write_text(json.dumps(result.to_dict(trace_events=0), ensure_ascii=False, indent=1))
        print(f"wrote {args.json}")
    return 0 if result.feasible else 2


def cmd_compare(args: argparse.Namespace) -> int:
    from .algorithms import plan

    sc = _load_scenario(args)
    plans = []
    for name in _algorithms(args.algorithms):
        try:
            plans.append(plan(sc, name))
        except Exception as exc:  # keep comparing the others
            print(f"{name}: failed ({exc})", file=sys.stderr)
    print(
        _table(
            _comparison_rows(plans),
            ["algorithm", "feasible", "time", "km", "stops", "money", "arrive", "ms", "settled"],
        )
    )
    return 0


def cmd_viz(args: argparse.Namespace) -> int:
    from .viz.export import build_viewer_html, run_all

    sc = _load_scenario(args)
    plans = run_all(sc, _algorithms(args.algorithms), trace=True)
    out = build_viewer_html(sc, plans, args.out, animate=not args.no_animate, lang=args.lang)
    print(f"wrote {out}  (open it in any browser; works offline)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    serve(args.scenario, host=args.host, port=args.port, open_browser=args.open)
    return 0


def cmd_animate(args: argparse.Namespace) -> int:
    from .algorithms import plan
    from .viz.animate import animate_route

    sc = _load_scenario(args)
    plans = [plan(sc, a, trace=True) for a in _algorithms(args.algorithms)]
    out = animate_route(sc, [p for p in plans if p.edges], args.out, animate=not args.no_animate, fps=args.fps)
    print(f"wrote {out}")
    return 0


def cmd_figures(args: argparse.Namespace) -> int:
    from .experiments.figures import case_study_figures

    sc = _load_scenario(args)
    paths = case_study_figures(sc, Path(args.outdir), algorithms=_algorithms(args.algorithms))
    for p in paths:
        print(f"wrote {p}")
    return 0


def cmd_robustness(args: argparse.Namespace) -> int:
    from .algorithms import plan
    from .robustness import monte_carlo

    sc = _load_scenario(args)
    route = plan(sc, args.algorithm)
    print(route)
    if not route.feasible:
        return 2
    rep = monte_carlo(sc.prepare(), route, n=args.n, seed=args.seed)
    print(json.dumps(rep.to_dict(), indent=1))
    return 0


def cmd_experiments(args: argparse.Namespace) -> int:
    from .experiments.runner import run_experiments

    run_experiments(args.names or None, out_dir=Path(args.out), quick=args.quick)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    sc = _load_scenario(args)
    sc.save(args.out)
    print(f"wrote {args.out}")
    return 0


# ------------------------------------------------------------------ parser
def _scenario_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("scenario", nargs="?", default="turkey-karaman-istanbul", help="preset name or scenario .json")
    p.add_argument("--from", dest="origin", help="origin node id or city name")
    p.add_argument("--to", dest="destination", help="destination node id or city name")
    p.add_argument("--soc", type=float, help="initial state of charge (0..1)")
    p.add_argument("--depart", help="departure time HH:MM")
    p.add_argument("--temp", type=float, help="ambient temperature [degC]")
    p.add_argument("--vehicle", help="vehicle preset key")
    p.add_argument("--objective", choices=["time", "cost"])
    p.add_argument("--reserve", type=float, help="reserve SoC (0..1)")
    p.add_argument("--arrive", type=float, help="minimum arrival SoC (0..1)")
    p.add_argument("--eco", action="store_true", help="allow eco speeds (100/90/80 %%)")
    p.add_argument("--no-queue", action="store_true", help="ignore charging queues")
    p.add_argument("--step", type=float, help="charging-target grid step (SoC fraction)")
    p.add_argument("--safety", type=float, help="consumption safety factor (>= 1)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="evroute", description="EV route planning with charging stations")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("scenarios", help="list preset scenarios").set_defaults(func=cmd_scenarios)
    sub.add_parser("algorithms", help="list algorithms").set_defaults(func=cmd_algorithms)

    p = sub.add_parser("plan", help="plan one route")
    _scenario_options(p)
    p.add_argument("-a", "--algorithm", default="bls-astar")
    p.add_argument("--json", help="write the plan as JSON")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("compare", help="compare algorithms on one scenario")
    _scenario_options(p)
    p.add_argument("--algorithms", default="all")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("viz", help="export the interactive animated HTML viewer")
    _scenario_options(p)
    p.add_argument("-o", "--out", default="evroute.html")
    p.add_argument("--algorithms", default="all")
    p.add_argument("--no-animate", action="store_true", help="start with animation switched off")
    p.add_argument("--lang", default="tr", choices=["tr", "en"])
    p.set_defaults(func=cmd_viz)

    p = sub.add_parser("serve", help="live planning server with the web viewer")
    p.add_argument("scenario", nargs="?", default="turkey-karaman-istanbul")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--open", action="store_true", help="open a browser tab")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("animate", help="render a GIF/MP4 animation with matplotlib")
    _scenario_options(p)
    p.add_argument("-o", "--out", default="evroute.gif")
    p.add_argument("--algorithms", default="greedy,bls-astar,cfp")
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--no-animate", action="store_true", help="write a static PNG instead")
    p.set_defaults(func=cmd_animate)

    p = sub.add_parser("figures", help="publication figures for one scenario")
    _scenario_options(p)
    p.add_argument("--outdir", default="figures")
    p.add_argument("--algorithms", default="all")
    p.set_defaults(func=cmd_figures)

    p = sub.add_parser("robustness", help="Monte Carlo reliability of a plan")
    _scenario_options(p)
    p.add_argument("-a", "--algorithm", default="bls-astar")
    p.add_argument("-n", type=int, default=1000)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_robustness)

    p = sub.add_parser("experiments", help="run the paper experiments")
    p.add_argument("names", nargs="*", help="experiment ids (default: all)")
    p.add_argument("-o", "--out", default="results")
    p.add_argument("--quick", action="store_true", help="small instances, fast smoke run")
    p.set_defaults(func=cmd_experiments)

    p = sub.add_parser("export-scenario", help="save a scenario as JSON")
    _scenario_options(p)
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(func=cmd_export)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
