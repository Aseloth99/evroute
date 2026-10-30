"""Interactive, animated web viewer: data payload and single-file HTML export.

The viewer is a hand-written, dependency-free web application (vanilla
JavaScript ES2020, Canvas 2D, no CDN) that lives in ``evroute/viz/web``.
This module turns a :class:`~evroute.scenario.Scenario` and the
:class:`~evroute.plan.RoutePlan` objects computed for it into

* a JSON-serialisable *payload* that follows the ``evroute-viewer/1``
  contract of ``docs/DEV_SPEC.md`` section 3 (:func:`viewer_payload`), and
* **one self-contained HTML file** -- CSS, JavaScript and data inlined --
  that opens in any modern browser and works offline
  (:func:`build_viewer_html`).

Every number the viewer shows is taken from the payload, i.e. from the
independent evaluator (:func:`evroute.evaluate.evaluate`); the browser never
re-implements the energy or charging model.  The only derived quantities are
computed here, exactly, from the prepared scenario:

* ``stops[k]["curve"]`` -- the breakpoints ``(t, e)`` of the charging
  function between the stop's arrival and departure energy, so that the
  animation can show the exact state of charge and charging power at any
  instant of a stop (power is constant between breakpoints);
* ``stations[s]["wait_profile"]`` -- the FIFO-safe expected-wait profile the
  planners used (:class:`~evroute.queueing.WaitProfile`);
* ``stations[s]["connectors"]`` -- the station's connector standards
  (:attr:`~evroute.scenario.Station.sockets`) and ``stations[s]["usable"]``
  -- whether the scenario's vehicle can plug in there
  (:attr:`~evroute.scenario.PreparedScenario.station_usable`; the planners
  never stop where it is ``false``);
* ``vehicles[]`` and ``scenario["vehicle"]`` -- battery, chemistry,
  connectors, AC power and the *effective* DC peak ``dc_peak_kw`` (capped at
  ``dc_max_kw``, 0 without a DC inlet);
* with a charging tariff other than ``Tariff()`` only: ``scenario["tariff"]``
  (:meth:`~evroute.scenario.Tariff.to_dict`), and per station ``peak_s``
  (its peak period as clock intervals ``[a, b)`` in seconds of the day) and
  ``price_factors`` (``{"offpeak": [below, above], "peak": [below, above]}``
  high-SoC price factors);
* ``pareto_points`` -- the criteria vectors of every Pareto front member;
* ``reliability`` -- a Monte Carlo estimate of how often the route strands or
  arrives short under uncertain consumption and random queues
  (:func:`evroute.robustness.monte_carlo`, 200 replays by default).

The served live viewer (:mod:`evroute.server`) uses the same page and
:func:`results_payload` for its ``POST /api/plan`` answers.

Quick start::

    from evroute.generators.presets import get_scenario
    from evroute.viz.export import build_viewer_html, run_all

    sc = get_scenario("turkey-karaman-istanbul")
    build_viewer_html(sc, run_all(sc), "karaman-istanbul.html", lang="en")

or from the shell::

    python -m evroute.viz.export --scenario turkey-karaman-istanbul --out trip.html
"""

from __future__ import annotations

import argparse
import logging
import math
import re
import sys
import zlib
from dataclasses import asdict
from html import escape as _html_escape
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..plan import RoutePlan
from ..scenario import PreparedScenario, Scenario, Tariff

__all__ = [
    "FORMAT",
    "ALGORITHM_COLORS",
    "VIEWER_OPTIONS",
    "algorithm_color",
    "build_viewer_html",
    "dumps_for_html",
    "render_viewer_html",
    "results_payload",
    "run_all",
    "viewer_payload",
]

log = logging.getLogger(__name__)

#: Contract identifier written into every payload.
FORMAT = "evroute-viewer/1"

#: Directory holding ``index.html``, ``css/`` and ``js/``.
WEB_DIR = Path(__file__).resolve().parent / "web"

#: Stylesheets, inlined in this order.
CSS_FILES: tuple[str, ...] = ("theme.css", "layout.css", "components.css")

#: Scripts, inlined in this order.  Each file is one IIFE that adds its part
#: to the shared ``window.EVR`` namespace, so the order is the dependency
#: order (``app.js`` boots the application and must come last).
JS_FILES: tuple[str, ...] = (
    "util.js",
    "i18n.js",
    "model.js",
    "terrain.js",
    "map.js",
    "charts.js",
    "anim.js",
    "panels.js",
    "live.js",
    "app.js",
)

#: Stable colour per algorithm: ``(light theme, dark theme)``.  The hues are
#: those of the colour-blind-safe palette of Okabe & Ito (2008, "Color
#: Universal Design"); the dark-theme variants are lightened so that every
#: route keeps enough contrast on a dark map, and the yellow is darkened on the
#: light theme for the same reason.  Algorithms of one family share a hue
#: family (BLS sky blue / BLS-A* blue).
ALGORITHM_COLORS: dict[str, tuple[str, str]] = {
    "naive": ("#7F7F7F", "#A3A3A3"),
    "greedy": ("#D55E00", "#F0782E"),
    "station-graph": ("#CC79A7", "#E191BE"),
    "genetic": ("#E69F00", "#F5B731"),
    "bls": ("#56B4E9", "#7CC8F2"),
    "bls-astar": ("#0072B2", "#3D9EE6"),
    "cfp": ("#009E73", "#21C495"),
    "cfp-astar": ("#A89200", "#F0E442"),
    "pareto": ("#262626", "#ECECEC"),
}
_FALLBACK_COLORS: tuple[tuple[str, str], ...] = (
    ("#0072B2", "#3D9EE6"),
    ("#D55E00", "#F0782E"),
    ("#009E73", "#21C495"),
    ("#CC79A7", "#E191BE"),
    ("#E69F00", "#F5B731"),
    ("#56B4E9", "#7CC8F2"),
    ("#A89200", "#F0E442"),
)

#: Short labels for legends, cars and table rows.
SHORT_TITLES: dict[str, str] = {
    "naive": "Naive",
    "greedy": "Greedy",
    "station-graph": "Station graph",
    "genetic": "Genetic",
    "bls": "BLS",
    "bls-astar": "BLS-A*",
    "cfp": "CFP",
    "cfp-astar": "CFP-A*",
    "pareto": "Pareto",
}

#: Planner options :func:`run_all` uses unless told otherwise.  The exact
#: three-criteria Pareto search can need millions of labels on country-sized
#: instances; for an interactive page the viewer computes the (time, money)
#: front under a label budget.  When the budget is hit the front may be
#: incomplete: the payload then carries ``"complete": false`` and the viewer
#: marks the result as a partial front instead of claiming optimality.
VIEWER_OPTIONS: dict[str, dict[str, Any]] = {
    "pareto": {"criteria": ("time", "money"), "max_labels": 1_000_000},
}

_TEMPLATE_DATA_TAG = '<script id="evroute-data" type="application/json">null</script>'
_STYLES_BLOCK = re.compile(r"<!-- evroute:styles -->.*?<!-- /evroute:styles -->", re.S)
_SCRIPTS_BLOCK = re.compile(r"<!-- evroute:scripts -->.*?<!-- /evroute:scripts -->", re.S)


# ====================================================================== colours
def algorithm_color(name: str) -> tuple[str, str]:
    """``(light, dark)`` colour of algorithm ``name`` (stable across runs).

    Unknown names get a palette entry chosen by a CRC-32 of the name, so the
    same name always gets the same colour on every machine.
    """
    try:
        return ALGORITHM_COLORS[name]
    except KeyError:
        return _FALLBACK_COLORS[zlib.crc32(name.encode("utf-8")) % len(_FALLBACK_COLORS)]


def _short_title(name: str, info: Mapping[str, Any] | None) -> str:
    if name in SHORT_TITLES:
        return SHORT_TITLES[name]
    title = str((info or {}).get("title") or name)
    return title.split(" (")[0]


# ====================================================================== running
def _algorithm_infos() -> dict[str, dict[str, Any]]:
    """``describe()`` keyed by name, without the entries that fail to import."""
    from ..algorithms import describe

    out: dict[str, dict[str, Any]] = {}
    for d in describe():
        if "error" not in d:
            out[d["name"]] = d
    return out


def run_all(
    scenario: Scenario | PreparedScenario,
    algorithms: Iterable[str] | None = None,
    trace: bool = True,
    *,
    options: Mapping[str, Mapping[str, Any]] | None = None,
    on_error: Callable[[str, BaseException], None] | None = None,
) -> list[RoutePlan]:
    """Run every available algorithm (or the given ones) on ``scenario``.

    Results come back in the order of :data:`evroute.algorithms.REGISTRY`,
    whatever the order of ``algorithms``.  An algorithm that cannot be
    imported, rejects the scenario or raises is skipped: ``on_error(name,
    exc)`` is called (default: a logged warning) and the others still run --
    a broken baseline must never take the viewer down.  Infeasible plans are
    *kept*: showing where the battery-unaware route runs empty is part of the
    story.

    ``options`` maps an algorithm name to keyword options of its planner and
    replaces the defaults of :data:`VIEWER_OPTIONS` for that algorithm.
    """
    from ..algorithms import REGISTRY, get_planner

    prepared = scenario.prepare() if isinstance(scenario, Scenario) else scenario
    wanted = list(REGISTRY) if algorithms is None else [a.strip() for a in algorithms if a and a.strip()]
    report = on_error or (lambda name, exc: log.warning("algorithm %s skipped: %r", name, exc))
    for name in wanted:
        if name not in REGISTRY:
            report(name, KeyError(f"unknown algorithm {name!r}"))
    opts_all: dict[str, Mapping[str, Any]] = dict(VIEWER_OPTIONS)
    if options:
        opts_all.update(options)
    plans: list[RoutePlan] = []
    for name in REGISTRY:
        if name not in wanted:
            continue
        try:
            planner = get_planner(name, **dict(opts_all.get(name, {})))
            plans.append(planner.plan(prepared, trace=trace))
        except Exception as exc:  # noqa: BLE001 - keep the other algorithms running
            report(name, exc)
    return plans


# ====================================================================== payload
def _clean(obj: Any) -> Any:
    """JSON-safe copy: non-finite floats -> ``None``, tuples -> lists."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _r(x: float, nd: int) -> float | None:
    return round(x, nd) if math.isfinite(x) else None


def _stop_curve(P: PreparedScenario, stop: Mapping[str, Any]) -> dict[str, list[float]]:
    """Exact charging trajectory of one stop: breakpoints of ``F_s`` in time.

    ``t[j]`` are seconds after the charging phase started (after waiting and
    set-up) and ``e[j]`` the battery energy then; power is constant between
    consecutive breakpoints, so linear interpolation is exact.
    """
    s = int(stop["station"])
    e_in, e_out = float(stop["energy_in_kwh"]), float(stop["energy_out_kwh"])
    if not 0 <= s < len(P.cf) or not e_out > e_in:
        return {"t": [0.0], "e": [_r(e_in, 5) or 0.0]}
    f = P.cf[s]
    pts = [e_in, *f.breakpoints_between(e_in, e_out), e_out]
    return {
        "t": [round(f.charge_time(e_in, e), 2) for e in pts],
        "e": [round(e, 5) for e in pts],
    }


def _plan_dict(
    P: PreparedScenario,
    plan: RoutePlan,
    trace_events: int | None,
) -> dict[str, Any]:
    d = plan.to_dict(trace_events=trace_events)
    d.pop("alternatives", None)
    for st in d.get("stops", []):
        st["curve"] = _stop_curve(P, st)
    stats = d.get("stats")
    if isinstance(stats, dict) and isinstance(stats.get("extra"), dict):
        # the criteria vectors are re-published as ``pareto_points``
        stats["extra"] = {k: v for k, v in stats["extra"].items() if k != "front"}
    return d


def _thin_indices(n: int, keep: int, must: Iterable[int] = ()) -> list[int]:
    """At most ``keep`` indices of ``range(n)``, evenly spread, ends included."""
    if n <= keep:
        return list(range(n))
    picked = {round(i * (n - 1) / (keep - 1)) for i in range(keep)}
    picked.update(i for i in must if 0 <= i < n)
    return sorted(picked)


def _reliability(P: PreparedScenario, plan: RoutePlan, runs: int) -> dict[str, Any] | None:
    """Monte Carlo reliability of ``plan`` (:func:`evroute.robustness.monte_carlo`).

    ``runs`` replays of the plan's decision under uncertain consumption
    (log-normal trip and edge factors) and random queues, with the module's
    default uncertainty model and seed 0 -- the same worlds for every
    algorithm (common random numbers), so the badges of two routes are a
    paired comparison.  ``None`` when the plan has no origin-destination
    route to replay or the analysis is unavailable.
    """
    if runs <= 0 or plan.decision is None or not plan.edges:
        return None
    try:
        from ..robustness import monte_carlo

        rep = monte_carlo(P, plan, n=runs, seed=0)
    except Exception as exc:  # noqa: BLE001 - a missing badge must not break the viewer
        log.debug("reliability of %s skipped: %r", plan.algorithm, exc)
        return None
    return {
        "n": rep.n,
        "p_strand": rep.p_strand,
        "p_reserve_breach": rep.p_reserve_breach,
        "p_arrival_short": rep.p_arrival_short,
        "ci95": {k: [round(v[0], 5), round(v[1], 5)] for k, v in rep.ci95.items()},
        "trip_cv": rep.trip_cv,
        "edge_cv": rep.edge_cv,
        "wait_model": rep.wait_model,
        "policy": rep.policy,
    }


def _result_dict(
    P: PreparedScenario,
    plan: RoutePlan,
    infos: Mapping[str, Mapping[str, Any]],
    trace_events: int | None,
    max_alternatives: int,
    options: Mapping[str, Any] | None,
    robustness_runs: int = 0,
) -> dict[str, Any]:
    name = plan.algorithm
    info = infos.get(name, {})
    light, dark = algorithm_color(name)
    d = _plan_dict(P, plan, trace_events)
    d.update(
        color=light,
        color_dark=dark,
        title=_short_title(name, info),
        long_title=info.get("title", name),
        exact=info.get("exact", "heuristic"),
        options=dict(options or {}),
        reliability=_reliability(P, plan, robustness_runs),
    )
    extra = plan.stats.extra if plan.stats else {}
    d["complete"] = bool(extra.get("complete", True))
    alts = list(plan.alternatives)
    d["alternatives"] = []
    d["pareto_points"] = []
    if alts:
        selected = -1
        if plan.decision is not None:
            for i, a in enumerate(alts):
                if a.decision is not None and a.decision == plan.decision:
                    selected = i
                    break
        keep = _thin_indices(len(alts), max(2, max_alternatives), [selected])
        where = {i: k for k, i in enumerate(keep)}
        for k, i in enumerate(keep):
            ad = _plan_dict(P, alts[i], 0)
            ad.update(color=light, color_dark=dark, title=f"{d['title']} #{i + 1}", exact=d["exact"], member=i)
            d["alternatives"].append(ad)
        d["pareto_points"] = [
            [_r(a.total_time_s, 2), _r(a.money, 4), _r(a.soh_total_pct, 7), where.get(i, -1), bool(a.feasible)]
            for i, a in enumerate(alts)
        ]
        d["alternatives_total"] = len(alts)
        d["alternative_selected"] = where.get(selected, -1)
    return _clean(d)


def _places(scenario: Scenario) -> list[dict[str, Any]]:
    """Named places for labels and the origin/destination pickers."""
    net = scenario.network
    meta_places = scenario.meta.get("places") if isinstance(scenario.meta, dict) else None
    out: list[dict[str, Any]] = []
    if isinstance(meta_places, list) and meta_places:
        for p in meta_places:
            try:
                node = int(p["node"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= node < net.num_nodes:
                out.append(
                    {
                        "node": node,
                        "name": str(p.get("name") or net.node_name.get(node, node)),
                        "kind": str(p.get("kind", "city")),
                        "tier": int(p.get("tier") or 0),
                    }
                )
        return out
    for node, name in sorted(net.node_name.items()):
        if 0 <= node < net.num_nodes:
            out.append({"node": node, "name": name, "kind": "city", "tier": 2})
    return out


def _scenario_block(scenario: Scenario, P: PreparedScenario) -> dict[str, Any]:
    veh = scenario.vehicle
    net = scenario.network
    q = scenario.query
    prefs = asdict(scenario.preferences)
    prefs["speed_factors"] = list(scenario.preferences.speed_factors)
    meta = scenario.meta if isinstance(scenario.meta, dict) else {}
    return _clean(
        {
            "name": scenario.name,
            "description": scenario.description,
            "currency": scenario.currency,
            "vehicle": {
                "key": veh.key,
                "name": veh.name,
                "battery_kwh": veh.battery_kwh,
                "capacity_kwh": round(P.capacity, 4),
                "dc_peak_kw": round(veh.dc_peak_kw, 1),
                "ac_max_kw": veh.ac_max_kw,
                "mass_kg": veh.mass_kg,
                "chemistry": veh.chemistry,
                "connectors": list(veh.connectors),
                "dc_max_kw": veh.dc_max_kw,
            },
            "query": {
                "origin": q.origin,
                "destination": q.destination,
                "initial_soc": q.initial_soc,
                "departure_s": q.departure_s,
                "origin_name": net.node_name.get(q.origin, meta.get("origin_name", "")),
                "destination_name": net.node_name.get(q.destination, meta.get("destination_name", "")),
            },
            "conditions": asdict(scenario.conditions),
            "preferences": prefs,
            **({"tariff": scenario.tariff.to_dict()} if scenario.tariff != Tariff() else {}),
            "meta": {k: v for k, v in meta.items() if k in (
                "generator", "preset", "data_version", "seed", "disclaimer", "attribution", "licence")},
            "prepared": P.summary(),
        }
    )


def _vehicle_entry(v: Any) -> dict[str, Any]:
    """One entry of the payload's vehicle list (the pickers and the vehicle card)."""
    return _clean(
        {
            "key": v.key,
            "name": v.name,
            "battery_kwh": v.battery_kwh,
            "dc_peak_kw": round(v.dc_peak_kw, 1),
            "ac_max_kw": v.ac_max_kw,
            "chemistry": v.chemistry,
            "connectors": list(v.connectors),
            "notes": v.notes,
        }
    )


def _stations_block(P: PreparedScenario) -> list[dict[str, Any]]:
    out = []
    for i, st in enumerate(P.stations):
        out.append(
            {
                "id": st.id,
                "index": i,
                "node": st.node,
                "name": st.name,
                "power_kw": st.power_kw,
                "kind": st.kind,
                "plugs": st.plugs,
                "price_per_kwh": st.price_per_kwh,
                "operator": st.operator,
                "setup_s": st.setup_s,
                "wait_profile": P.wait_profiles[i].to_dict(),
                "connectors": list(st.sockets),
                "usable": P.station_usable[i],
            }
        )
        if P.tariff != Tariff():
            out[-1].update(_station_tariff(P, i))
    return _clean(out)


def _station_tariff(P: PreparedScenario, i: int) -> dict[str, Any]:
    """Peak period and price factors of station ``i`` (scenarios with a tariff only)."""
    return {
        "peak_s": [list(iv) for iv in P.peak_intervals[i]],
        "price_factors": {"offpeak": list(P.tariff.factors(False)), "peak": list(P.tariff.factors(True))},
    }


def _network_block(scenario: Scenario) -> dict[str, Any]:
    d = scenario.network.to_dict(compact=True)
    return d


def results_payload(
    scenario: Scenario,
    plans: Sequence[RoutePlan],
    *,
    trace_events: int | None = 20000,
    max_alternatives: int = 48,
    options: Mapping[str, Mapping[str, Any]] | None = None,
    robustness_runs: int = 200,
) -> dict[str, Any]:
    """The part of the payload that changes when a trip is re-planned.

    ``{"scenario": ..., "results": [...], "wait_profiles": [...]}`` -- what
    the live server returns from ``POST /api/plan`` (the network and the
    station list stay in the page).  ``wait_profiles`` follow the station
    order and change with ``queue_aware`` / ``time_dependent_waits``;
    ``station_usable`` (also in station order) tells which stations the
    request's vehicle can plug into (it changes with the vehicle); with a
    charging tariff other than ``Tariff()``, ``station_tariffs`` lists the
    ``peak_s`` and ``price_factors`` of every station (they change with the
    tariff and the departure-independent busy hours).
    """
    P = scenario.prepare()
    infos = _algorithm_infos()
    opts_all: dict[str, Mapping[str, Any]] = dict(VIEWER_OPTIONS)
    if options:
        opts_all.update(options)
    data = {
        "scenario": _scenario_block(scenario, P),
        "results": [
            _result_dict(P, p, infos, trace_events, max_alternatives, opts_all.get(p.algorithm), robustness_runs)
            for p in plans
        ],
        "wait_profiles": [_clean(prof.to_dict()) for prof in P.wait_profiles],
        "station_usable": list(P.station_usable),
    }
    if P.tariff != Tariff():
        data["station_tariffs"] = _clean([_station_tariff(P, i) for i in range(len(P.stations))])
    return data


def viewer_payload(
    scenario: Scenario,
    plans: Sequence[RoutePlan] = (),
    *,
    title: str | None = None,
    lang: str = "tr",
    animate: bool = True,
    live: bool = False,
    trace_events: int | None = 20000,
    max_alternatives: int = 48,
    options: Mapping[str, Mapping[str, Any]] | None = None,
    extra: Mapping[str, Any] | None = None,
    robustness_runs: int = 200,
) -> dict[str, Any]:
    """The ``evroute-viewer/1`` payload for ``scenario`` and its ``plans``.

    Parameters
    ----------
    scenario, plans:
        The instance and the evaluated plans to show (any order; the viewer
        keeps the order given here, :func:`run_all` uses registry order).
    title:
        Page title; default ``"<origin> → <destination>"`` or the scenario name.
    lang:
        ``"tr"`` (default) or ``"en"`` -- the initial UI language.
    animate:
        Initial state of the animation switch (the viewer starts with the
        animation *off* anyway when the user prefers reduced motion).
    live:
        ``True`` when served by :mod:`evroute.server` (enables the planning
        controls).
    trace_events:
        Cap on search-trace events per result (evenly thinned).
    max_alternatives:
        Cap on Pareto members sent with full route data; the scatter plot
        still gets the criteria of every member (``pareto_points``).
    options:
        Planner options that produced the plans (shown in the viewer).
    extra:
        Additional top-level keys (e.g. the preset list in live mode).
    robustness_runs:
        Monte Carlo replays per result for the reliability badge
        (``result["reliability"]``, see :func:`evroute.robustness.monte_carlo`;
        about 20 ms per route for 200 runs on the Turkey case study); ``0``
        switches the analysis off.
    """
    if lang not in ("tr", "en"):
        raise ValueError(f"lang must be 'tr' or 'en', not {lang!r}")
    P = scenario.prepare()
    net = scenario.network
    infos = _algorithm_infos()
    q = scenario.query
    o_name = net.node_name.get(q.origin)
    d_name = net.node_name.get(q.destination)
    if title is None:
        title = f"{o_name} → {d_name}" if o_name and d_name else scenario.name
    opts_all: dict[str, Mapping[str, Any]] = dict(VIEWER_OPTIONS)
    if options:
        opts_all.update(options)

    algorithms = []
    for name, info in infos.items():
        light, dark = algorithm_color(name)
        algorithms.append({**info, "short_title": _short_title(name, info), "color": light, "color_dark": dark})

    from .. import __version__
    from ..vehicle import VEHICLES

    payload: dict[str, Any] = {
        "format": FORMAT,
        "title": title,
        "lang": lang,
        "animate": bool(animate),
        "live": bool(live),
        "generator": {"name": "evroute", "version": __version__},
        "scenario": _scenario_block(scenario, P),
        "network": _network_block(scenario),
        "stations": _stations_block(P),
        "places": _places(scenario),
        "results": [
            _result_dict(P, p, infos, trace_events, max_alternatives, opts_all.get(p.algorithm), robustness_runs)
            for p in plans
        ],
        "algorithms": _clean(algorithms),
        "vehicles": [_vehicle_entry(v) for v in VEHICLES.values()],
    }
    if extra:
        payload.update(_clean(dict(extra)))
    return payload


# ====================================================================== HTML
def dumps_for_html(payload: Any) -> str:
    """Serialise ``payload`` for a ``<script type="application/json">`` block.

    ``<``, ``>`` and ``&`` are written as ``\\u003c``, ``\\u003e`` and
    ``\\u0026``.  They can only occur inside JSON strings, where these escapes
    are valid, so ``JSON.parse`` returns the original text while the HTML
    parser can never see ``</script>`` or ``<!--`` in the data.  U+2028/9 are
    escaped as well (harmless in JSON, but historically not in JavaScript).
    """
    import json

    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _read_asset(rel: str) -> str:
    return (WEB_DIR / rel).read_text(encoding="utf-8")


def render_viewer_html(payload: Mapping[str, Any]) -> str:
    """The self-contained viewer page (CSS, JS and data inlined) as a string."""
    template = _read_asset("index.html")
    css = "\n".join(f"/* ---- {name} ---- */\n{_read_asset('css/' + name)}" for name in CSS_FILES)
    js = "\n".join(f"// ---- {name} ----\n{_read_asset('js/' + name)}" for name in JS_FILES)
    if re.search(r"</style", css, re.I):
        raise ValueError("a stylesheet contains '</style'; it cannot be inlined")
    if re.search(r"</script|<!--", js, re.I):
        raise ValueError("a script contains '</script' or '<!--'; it cannot be inlined")
    if _TEMPLATE_DATA_TAG not in template or not _STYLES_BLOCK.search(template) or not _SCRIPTS_BLOCK.search(template):
        raise ValueError("viewer template is missing an evroute marker")
    lang = "en" if payload.get("lang") == "en" else "tr"
    title = str(payload.get("title") or "EVRoute")
    page = _STYLES_BLOCK.sub(lambda _m: f"<style>\n{css}\n</style>", template, count=1)
    page = _SCRIPTS_BLOCK.sub(lambda _m: f"<script>\n{js}\n</script>", page, count=1)
    data_tag = f'<script id="evroute-data" type="application/json">{dumps_for_html(payload)}</script>'
    page = page.replace(_TEMPLATE_DATA_TAG, data_tag, 1)
    page = page.replace('<html lang="tr">', f'<html lang="{lang}">', 1)
    page = re.sub(r"<title>.*?</title>", lambda _m: f"<title>{_html_escape(title)} · EVRoute</title>", page, count=1)
    return page


def build_viewer_html(
    scenario: Scenario,
    plans: Sequence[RoutePlan],
    out_path: str | Path,
    *,
    title: str | None = None,
    lang: str = "tr",
    animate: bool = True,
    live: bool = False,
    trace_events: int | None = 20000,
    max_alternatives: int = 48,
    options: Mapping[str, Mapping[str, Any]] | None = None,
    robustness_runs: int = 200,
) -> Path:
    """Write the single-file viewer for ``scenario`` and ``plans``; return its path.

    The file embeds everything (no ``http(s)`` references), so it can be
    e-mailed, archived with a paper's supplementary material or opened from a
    USB stick without network access.  Keyword arguments are those of
    :func:`viewer_payload`.
    """
    payload = viewer_payload(
        scenario,
        plans,
        title=title,
        lang=lang,
        animate=animate,
        live=live,
        trace_events=trace_events,
        max_alternatives=max_alternatives,
        options=options,
        robustness_runs=robustness_runs,
    )
    out = Path(out_path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_viewer_html(payload), encoding="utf-8")
    return out


# ====================================================================== CLI
def _load_scenario(spec: str) -> Scenario:
    p = Path(spec)
    if p.suffix.lower() == ".json" and p.exists():
        return Scenario.load(p)
    from ..generators.presets import get_scenario

    return get_scenario(spec)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m evroute.viz.export",
        description="Export the interactive, animated EVRoute viewer as one self-contained HTML file.",
    )
    ap.add_argument("--scenario", default="turkey-karaman-istanbul", help="preset name or scenario .json")
    ap.add_argument("--out", default="evroute.html", help="output HTML file")
    ap.add_argument("--algorithms", default="all", help="comma-separated registry names (default: all)")
    ap.add_argument("--no-animate", action="store_true", help="start with the animation switched off")
    ap.add_argument("--lang", default="tr", choices=["tr", "en"])
    ap.add_argument("--title", default=None)
    ap.add_argument("--trace-events", type=int, default=20000, help="max search-trace events per algorithm")
    ap.add_argument("--robustness-runs", type=int, default=200,
                    help="Monte Carlo replays per route for the reliability badge (0: off)")
    args = ap.parse_args(argv)

    sc = _load_scenario(args.scenario)
    algos = None if args.algorithms in ("", "all") else args.algorithms.split(",")

    def report(name: str, exc: BaseException) -> None:
        print(f"  {name}: skipped ({exc})", file=sys.stderr)

    plans = run_all(sc, algos, trace=True, on_error=report)
    for p in plans:
        print(f"  {p}")
    out = build_viewer_html(
        sc,
        plans,
        args.out,
        title=args.title,
        lang=args.lang,
        animate=not args.no_animate,
        trace_events=args.trace_events,
        robustness_runs=max(0, args.robustness_runs),
    )
    print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KiB; open it in any browser, works offline)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
