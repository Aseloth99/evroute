"""Command-line interface: ``evroute <command> ...``.

Examples::

    evroute scenarios
    evroute plan turkey-karaman-istanbul
    evroute plan turkey-karaman-istanbul --from Konya --to Ankara --soc 0.35 -a cfp
    evroute plan turkey-karaman-istanbul --depart 14:00 --tariff 80 --objective cost
    evroute plan turkey-karaman-istanbul --peak 16-20 --peak-factor 1.2 --high-soc-factor 1.5
    evroute compare turkey-antalya-ankara --temp -5
    evroute viz turkey-karaman-istanbul -o karaman-istanbul.html
    evroute viz corridor -o corridor.html --no-animate --lang en
    evroute serve turkey-karaman-istanbul
    evroute gallery -o docs/img
    evroute robustness turkey-izmir-erzurum -n 2000
    evroute experiments --quick -o results/      # also writes results/results.html
    evroute results results/ --lang en

Commands that take a scenario accept a preset name or the path of a scenario
``.json`` file; ``serve`` takes preset names only.  Bad input (an unknown
name, an out-of-range value, an unreadable file) is reported as one
``error: ...`` line on stderr with exit code 2.

Charging tariff (:class:`~evroute.scenario.Tariff`): ``--tariff 80`` switches
on the illustrative demonstration tariff
(:data:`~evroute.generators.presets.DEMO_TARIFF`: +30 % above 80 % SoC,
16:00-20:00 peak at +15 %, x2 above 80 % in the peak), ``--tariff flat``
switches any tariff off; ``--high-soc``, ``--high-soc-factor``, ``--peak``,
``--peak-factor``, ``--peak-high-soc-factor`` and ``--busy`` then set single
fields.  ``plan`` adds a ``hold`` column to its stop table when a stop waits
for a cheaper price period, and a ``tariff`` column for a scenario with a
tariff.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from .units import format_clock, format_duration


class UsageError(Exception):
    """Bad command-line input: :func:`main` prints ``error: <message>`` and returns 2."""


# ---------------------------------------------------------------- helpers
def _load_scenario(args: argparse.Namespace):
    from .scenario import Scenario

    name = args.scenario
    if name.endswith(".json"):
        if not Path(name).is_file():
            raise UsageError(f"no scenario file {name}")
        try:
            sc = Scenario.load(name)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:  # unreadable or malformed
            raise UsageError(f"cannot read scenario {name}: {type(exc).__name__}: {exc}") from None
    else:
        from .generators.presets import PRESETS, get_scenario

        try:
            sc = get_scenario(name)
        except KeyError as exc:
            if name in PRESETS:  # a bug in the preset builder, not a typo
                raise
            raise UsageError(exc.args[0]) from None
    sc = _apply_overrides(sc, args)
    try:
        sc.validate()
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    if sc.query.origin == sc.query.destination:
        raise UsageError("origin and destination must differ")
    return sc


def _node(sc, value: str) -> int:
    try:
        return int(value)
    except ValueError:
        try:
            return sc.network.node_by_name(value)
        except KeyError as exc:
            raise UsageError(exc.args[0]) from None


def _apply_overrides(sc, args: argparse.Namespace):
    q: dict[str, Any] = {}
    if getattr(args, "origin", None):
        q["origin"] = _node(sc, args.origin)
    if getattr(args, "destination", None):
        q["destination"] = _node(sc, args.destination)
    if getattr(args, "soc", None) is not None:
        q["initial_soc"] = args.soc
    if getattr(args, "depart", None):
        m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", args.depart)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            raise UsageError(f"--depart must be a clock time HH:MM (00:00 to 23:59), got {args.depart!r}")
        q["departure_s"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60
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

        try:
            sc = sc.with_(vehicle=get_vehicle(args.vehicle))
        except KeyError as exc:
            raise UsageError(exc.args[0]) from None
    return _apply_tariff(sc, args)


#: ``--option`` -> :class:`~evroute.scenario.Tariff` field of the numeric tariff overrides
_TARIFF_OPTIONS = {
    "high_soc": "high_soc",
    "high_soc_factor": "high_soc_factor",
    "peak_factor": "peak_factor",
    "peak_high_soc_factor": "peak_high_soc_factor",
    "busy": "busy_utilisation",
}


def _apply_tariff(sc, args: argparse.Namespace):
    """``--tariff`` (a whole tariff) first, then the single-field tariff options.

    The values are checked by :meth:`Scenario.validate` afterwards (one
    ``error:`` line), like every other override.
    """
    shortcut = getattr(args, "tariff", None)
    if shortcut is not None:
        from .scenario import Tariff

        if shortcut.strip().lower() == "80":
            from .generators.presets import DEMO_TARIFF

            sc = sc.with_(tariff=DEMO_TARIFF)
        elif shortcut.strip().lower() == "flat":
            sc = sc.with_(tariff=Tariff())
        else:
            raise UsageError(
                f"--tariff must be 80 (the illustrative demonstration tariff) or flat (no tariff), got {shortcut!r}"
            )
    t: dict[str, Any] = {}
    for opt, name in _TARIFF_OPTIONS.items():
        value = getattr(args, opt, None)
        if value is not None:
            t[name] = value
    if getattr(args, "peak", None) is not None:
        t["peak_hours"] = _peak_hours(args.peak)
    return sc.with_tariff(**t) if t else sc


def _peak_hours(spec: str) -> tuple[tuple[float, float], ...]:
    """``"16-20"``, ``"7-9,16-20"``, ``"22-6"`` (wraps midnight), ``"16:30-20"`` or
    ``"none"`` -> ``Tariff.peak_hours`` (clock hours)."""
    if spec.strip().lower() == "none":
        return ()
    usage = f"--peak must be clock hours START-END[,START-END...] such as 16-20, 7-9,16-20 or 22-6, got {spec!r}"
    out = []
    for item in spec.split(","):
        m = re.fullmatch(r"\s*(\d{1,2}(?:\.\d+|:\d{2})?)\s*-\s*(\d{1,2}(?:\.\d+|:\d{2})?)\s*", item)
        if not m:
            raise UsageError(usage)
        a, b = (_clock_hours(g, usage) for g in m.groups())
        if a == b:
            raise UsageError(f"--peak interval {item.strip()!r} is empty (start = end)")
        out.append((a, b))
    return tuple(out)


def _clock_hours(text: str, usage: str) -> float:
    """``"16"`` -> 16.0, ``"16.5"`` -> 16.5, ``"16:30"`` -> 16.5 (range checked by ``Tariff.validate``)."""
    if ":" in text:
        h, mnt = text.split(":")
        if int(mnt) > 59:
            raise UsageError(usage)
        return int(h) + int(mnt) / 60.0
    return float(text)


def _algorithm(name: str) -> str:
    from .algorithms import available

    if name not in available():
        raise UsageError(f"unknown algorithm {name!r}; choose from {available()}")
    return name


def _algorithms(spec: str | None) -> list[str]:
    from .algorithms import available

    if not spec or spec == "all":
        return available()
    names = [_algorithm(a.strip()) for a in spec.split(",") if a.strip()]
    if not names:
        raise UsageError("no algorithm given")
    return names


def _cannot_write(path: str, exc: OSError) -> UsageError:
    return UsageError(f"cannot write {path}: {exc.strerror or exc}")


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


def _tariff_label(stop, tariff) -> str:
    """Price period and high-SoC tier of one stop, e.g. ``"peak >80%"`` (``"-"``: base price)."""
    parts = []
    if tariff.peak_hours or tariff.busy_utilisation is not None:
        parts.append("peak" if stop.peak else "off-peak")
    lo, hi = tariff.factors(stop.peak)
    if hi != lo and stop.soc_out > tariff.high_soc + 1e-9 and stop.energy_out_kwh > stop.energy_in_kwh:
        parts.append(f">{100 * tariff.high_soc:.0f}%")  # energy bought in the high-SoC tier
    return " ".join(parts) or "-"


def _print_plan(plan, sc) -> None:
    from .scenario import Tariff

    print(plan)
    if not plan.feasible:
        return
    net = sc.network
    dep = sc.query.departure_s
    # extra columns only when they say something: no change for plans without holds / tariff
    holds = any(s.hold_s > 0.0 for s in plan.stops)
    tariff = sc.tariff if sc.tariff != Tariff() else None
    rows = []
    for i, s in enumerate(plan.stops, 1):
        rows.append(
            [
                str(i),
                s.station_name or net.node_name.get(s.node, f"node {s.node}"),
                f"{s.station_kind} {s.station_power_kw:.0f} kW",
                format_clock(dep + s.arrival_s),
                *([format_duration(s.hold_s) if s.hold_s > 0.0 else "-"] if holds else []),
                f"{s.wait_s / 60:.0f} min",
                f"{s.charge_s / 60:.0f} min",
                f"{100 * s.soc_in:.0f}% -> {100 * s.soc_out:.0f}%",
                *([_tariff_label(s, tariff)] if tariff else []),
                f"{s.money:.2f} {plan.currency}",
            ]
        )
    if rows:
        header = ["#", "station", "charger", "arrive", *(["hold"] if holds else []), "queue", "charge", "SoC",
                  *(["tariff"] if tariff else []), "cost"]
        print(_table(rows, header, left=3))
    hold = f" | hold {format_duration(plan.hold_time_s)}" if plan.hold_time_s > 0.0 else ""
    print(
        f"arrival {format_clock(dep + plan.total_time_s)} | drive {format_duration(plan.drive_time_s)}"
        f" | charge {format_duration(plan.charge_time_s)} | queue+setup "
        f"{format_duration(plan.wait_time_s + plan.setup_time_s)}{hold} | battery wear {plan.soh_total_pct:.4f} %SoH"
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
    result = plan(sc, _algorithm(args.algorithm))
    _print_plan(result, sc)
    if args.json:
        try:
            Path(args.json).write_text(json.dumps(result.to_dict(trace_events=0), ensure_ascii=False, indent=1))
        except OSError as exc:
            raise _cannot_write(args.json, exc) from None
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
    return 0 if plans else 1  # every planner raised


def cmd_viz(args: argparse.Namespace) -> int:
    from .viz.export import build_viewer_html, run_all

    sc = _load_scenario(args)
    plans = run_all(sc, _algorithms(args.algorithms), trace=True)
    out = build_viewer_html(sc, plans, args.out, animate=not args.no_animate, lang=args.lang)
    print(f"wrote {out}  (open it in any browser; works offline)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .generators.presets import PRESETS
    from .server import serve

    if args.scenario not in PRESETS:  # the live viewer re-plans presets, not .json files
        raise UsageError(f"unknown preset {args.scenario!r}; serve takes a preset name (see 'evroute scenarios')")
    try:
        serve(args.scenario, host=args.host, port=args.port, open_browser=args.open)
    except (OSError, OverflowError) as exc:  # port in use, bad host or port number
        raise UsageError(f"cannot serve on {args.host}:{args.port}: {exc}") from None
    return 0


def cmd_gallery(args: argparse.Namespace) -> int:
    from .viz.figures import make_gallery

    paths = make_gallery(args.out, quick=args.quick)
    for p in paths or []:
        print(f"wrote {p}")
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

    if args.n < 1:
        raise UsageError(f"-n must be at least 1, got {args.n}")
    sc = _load_scenario(args)
    route = plan(sc, _algorithm(args.algorithm))
    print(route)
    if not route.feasible:
        return 2
    rep = monte_carlo(sc.prepare(), route, n=args.n, seed=args.seed)
    print(json.dumps(rep.to_dict(), indent=1))
    return 0


def cmd_experiments(args: argparse.Namespace) -> int:
    from .experiments.runner import run_experiments

    run_experiments(args.names or None, out_dir=Path(args.out), quick=args.quick, panel=not args.no_panel, lang=args.lang)
    return 0


def cmd_results(args: argparse.Namespace) -> int:
    from .viz.results import main as results_main

    argv = [args.results_dir, "--lang", args.lang] + (["-o", args.out] if args.out else [])
    return results_main(argv + (["--title", args.title] if args.title else []))


def cmd_export(args: argparse.Namespace) -> int:
    sc = _load_scenario(args)
    try:
        sc.save(args.out)
    except OSError as exc:
        raise _cannot_write(args.out, exc) from None
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
    g = p.add_argument_group(
        "charging tariff",
        "Prices per kWh by state of charge and time of day; a factor multiplies the station price. "
        "--tariff is applied first, the other options then change single values.",
    )
    g.add_argument(
        "--tariff", metavar="{80,flat}",
        help="80: the demonstration tariff (illustrative values: x1.3 above 80 %% SoC, peak 16-20 at x1.15, "
             "x2 above 80 %% in the peak); flat: no tariff",
    )
    g.add_argument("--high-soc", type=float, metavar="S", help="SoC above which the high-SoC price applies (default 0.8)")
    g.add_argument("--high-soc-factor", type=float, metavar="F", help="price factor above --high-soc (default 1)")
    g.add_argument("--peak", metavar="H1-H2[,H3-H4]",
                   help="daily peak hours, e.g. 16-20 or 7-9,16-20; 22-6 wraps midnight; none: no peak hours")
    g.add_argument("--peak-factor", type=float, metavar="F", help="price factor in the peak period (default 1)")
    g.add_argument("--peak-high-soc-factor", type=float, metavar="F",
                   help="high-SoC factor in the peak period, on top of --peak-factor (default: --high-soc-factor)")
    g.add_argument("--busy", type=float, metavar="U",
                   help="also a peak hour at a station whose expected charger utilisation is >= U (e.g. 0.7)")


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

    p = sub.add_parser("gallery", help="render the seaborn figure gallery (needs matplotlib + seaborn)")
    p.add_argument("-o", "--out", default="docs/img")
    p.add_argument("--quick", action="store_true", help="fewer instances, faster")
    p.set_defaults(func=cmd_gallery)

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
    p.add_argument("--no-panel", action="store_true", help="do not write the results.html dashboard")
    p.add_argument("--lang", default="tr", choices=["tr", "en"], help="initial language of results.html")
    p.set_defaults(func=cmd_experiments)

    p = sub.add_parser("results", help="build the interactive results dashboard of an experiments folder")
    p.add_argument("results_dir", nargs="?", default="results")
    p.add_argument("-o", "--out", help="output HTML (default: <results_dir>/results.html)")
    p.add_argument("--lang", default="tr", choices=["tr", "en"])
    p.add_argument("--title")
    p.set_defaults(func=cmd_results)

    p = sub.add_parser("export-scenario", help="save a scenario as JSON")
    _scenario_options(p)
    p.add_argument("-o", "--out", required=True)
    p.set_defaults(func=cmd_export)
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
