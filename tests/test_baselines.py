"""Tests of the four baseline planners: naive, greedy, station-graph, genetic.

Correctness is established by

* the independent evaluator replaying every returned plan (feasibility,
  structure, internal objective);
* comparison with the exact planners: no heuristic may be faster than CFP
  (continuous charging) on a static-wait instance, nor faster than BLS on the
  1 % grid beyond one grid step per charging stop;
* brute force: the station-graph optimum equals an exhaustive enumeration of
  station sequences (with the evaluator as the oracle), also with
  time-dependent waits;
* white-box checks of the genetic decoder's energy profile against the
  evaluator;
* determinism, options, edge cases and the viewer serialisation.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import json
import math
import random

import pytest

from evroute import Conditions, NetworkBuilder, Preferences, Query, Scenario, Station, get_vehicle
from evroute.algorithms import REGISTRY, get_planner, planner_class
from evroute.algorithms.genetic import GeneticPlanner, _Evolution, _Problem
from evroute.algorithms.greedy import GreedyPlanner, run_greedy, target_energy
from evroute.algorithms.labels import fastest_feasible_tree
from evroute.algorithms.naive import NaivePlanner, dijkstra_traced, drive_path, fastest_levels, time_weights
from evroute.algorithms.station_graph import StationGraphPlanner, detour_candidates
from evroute.evaluate import check_objective, evaluate
from evroute.generators import grid_scenario
from evroute.generators.basic import grid_network, place_stations
from evroute.plan import ChargeDecision, PlanDecision
from evroute.search import LowerBounds, dijkstra

try:  # CFP is the exact continuous-charging reference; BLS on a fine grid is the fallback
    from evroute.algorithms.cfp import CFPPlanner  # noqa: F401

    HAVE_CFP = True
except ImportError:  # pragma: no cover - depends on the repository state
    HAVE_CFP = False

HEURISTICS = ("greedy", "station-graph", "genetic")

# (rows, cols, seed, initial_soc, n_stations, vehicle, spacing_m): random grids on which
# charging is needed (the naive plan is infeasible) and the exact planners find a route
CONFIGS = [
    (8, 6, 404, 0.40, 11, "compact50", 8000.0),
    (9, 8, 71, 0.27, 18, "city27", 8000.0),
    (10, 10, 406, 0.21, 13, "compact50", 8000.0),
    (8, 8, 74, 0.24, 16, "compact50", 12000.0),
    (10, 8, 269, 0.28, 12, "city27", 12000.0),
    (9, 9, 817, 0.37, 16, "city27", 12000.0),
    (11, 10, 367, 0.40, 16, "compact50", 8000.0),
    (9, 8, 198, 0.41, 15, "city27", 12000.0),
]


def _scenario(cfg, **prefs) -> Scenario:
    rows, cols, seed, soc, n_st, veh, spacing = cfg
    prefs.setdefault("time_dependent_waits", False)
    return grid_scenario(rows, cols, spacing, n_st, vehicle=veh, seed=seed, initial_soc=soc, **prefs)


@functools.lru_cache(maxsize=None)
def _suite(i: int) -> dict:
    """All planners on configuration ``i`` (static waits), computed once."""
    P = _scenario(CONFIGS[i]).prepare()
    out = {"P": P}
    for name in ("naive", "greedy", "station-graph", "genetic", "bls", "cfp"):
        if name == "cfp" and not HAVE_CFP:
            continue
        out[name] = get_planner(name).plan(P)
    out["bls1"] = get_planner("bls").plan(
        _scenario(CONFIGS[i], charge_step_soc=0.01).prepare()
    )
    return out


def _line_scenario(
    n: int = 6,
    spacing: float = 30000.0,
    soc: float = 0.55,
    stations=((1, "AC", 22.0), (2, "DC", 150.0), (3, "DC", 50.0)),
) -> Scenario:
    """Flat motorway line 0 - 1 - ... - n-1 with chargers at the given nodes."""
    b = NetworkBuilder()
    ids = [b.add_node(i * spacing, 0.0, 100.0) for i in range(n)]
    for u, v in zip(ids, ids[1:]):
        b.add_road(u, v, road_class="motorway")
    sts = [
        Station(id=i, node=node, power_kw=p, kind=kind, plugs=4, price_per_kwh=0.5, name=f"L{i}",
                daily_sessions=10.0, setup_s=120.0)
        for i, (node, kind, p) in enumerate(stations)
    ]
    return Scenario(
        name="line",
        network=b.build(),
        stations=sts,
        vehicle=get_vehicle("compact50"),
        query=Query(0, n - 1, soc, 9 * 3600.0),
        preferences=Preferences(time_dependent_waits=False),
    )


def _replay_independently(P, plan) -> None:
    """Re-check a plan without the evaluator: path structure, reserve and arrival energy."""
    net = P.net
    assert plan.nodes[0] == P.origin and plan.nodes[-1] == P.destination
    nodes = [P.origin]
    for e in plan.edges:
        assert net.tail[e] == nodes[-1]
        nodes.append(net.head[e])
    assert nodes == plan.nodes
    stops = {s.position: s for s in plan.stops}
    x = P.e_init
    for i, node in enumerate(nodes):
        if i > 0:
            assert x >= P.e_min - 1e-6, f"reserve undercut at position {i}"
        if i in stops:
            st = stops[i]
            assert P.stations[st.station].node == node
            assert st.energy_out_kwh <= P.e_charge_cap + 1e-6
            x = max(x, st.energy_out_kwh)
        if i < len(plan.edges):
            assert x >= P.e_min - 1e-6, f"departing below reserve at position {i}"
            x = min(x - P.edge_energy[plan.levels[i]][plan.edges[i]], P.capacity)
    assert x >= P.e_dest_min - 1e-6


def _step_tolerance(P, ref_plan) -> float:
    """Worst-case time of one 1 % charging step per stop of ``ref_plan`` [s]."""
    tol = 0.0
    for stop in ref_plan.stops:
        f = P.cf[stop.station]
        tol += 0.01 * P.capacity / min(f.power_kw) * 3600.0
    return max(tol, 1.0)


# ---------------------------------------------------------------------------
# registry and metadata
# ---------------------------------------------------------------------------
def test_registry_and_metadata():
    expected = {
        "naive": (NaivePlanner, "Naive fastest path"),
        "greedy": (GreedyPlanner, "Greedy (charge when needed)"),
        "station-graph": (StationGraphPlanner, "Station graph (fixed charge target)"),
        "genetic": (GeneticPlanner, "Genetic algorithm"),
    }
    for name, (cls, title) in expected.items():
        assert name in REGISTRY
        assert planner_class(name) is cls
        assert cls.name == name and cls.title == title and cls.exact == "heuristic"
        info = get_planner(name).info()
        assert info["name"] == name and info["exact"] == "heuristic" and info["description"]
    assert "cost" in GeneticPlanner.objectives


# ---------------------------------------------------------------------------
# every heuristic plan is verified; never better than the exact methods
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("i", range(len(CONFIGS)))
def test_heuristic_plans_pass_the_evaluator(i):
    s = _suite(i)
    P = s["P"]
    assert not s["naive"].feasible, "instance must need charging"
    assert s["bls"].feasible
    for name in HEURISTICS:
        plan = s[name]
        assert plan.algorithm == name
        assert plan.feasible, f"{name}: {plan.message}"
        assert plan.num_stops >= 0
        _replay_independently(P, plan)
        again = evaluate(P, plan.decision)
        assert again.feasible
        assert again.total_time_s == pytest.approx(plan.total_time_s, rel=1e-12, abs=1e-9)
        assert plan.total_time_s == pytest.approx(
            plan.drive_time_s + plan.wait_time_s + plan.setup_time_s + plan.charge_time_s
        )
        assert check_objective(plan)
        assert plan.stats is not None and plan.stats.settled > 0 and plan.stats.runtime_s > 0


@pytest.mark.parametrize("i", range(len(CONFIGS)))
def test_heuristics_never_faster_than_exact(i):
    s = _suite(i)
    P = s["P"]
    bls1 = s["bls1"]
    assert bls1.feasible
    # off-grid charging targets may beat the 1 % grid optimum by up to one grid step per stop
    tol = _step_tolerance(P, bls1)
    for name in HEURISTICS:
        t = s[name].total_time_s
        assert t >= bls1.total_time_s - tol, f"{name} beats BLS(1 %) by more than one charge step"
        if HAVE_CFP:
            assert s["cfp"].feasible
            assert t >= s["cfp"].total_time_s - 1e-6, f"{name} is faster than the exact CFP optimum"
    # cross-check of the references themselves
    if HAVE_CFP:
        assert s["cfp"].total_time_s <= s["bls"].total_time_s + 1e-6
        assert s["cfp"].total_time_s <= bls1.total_time_s + 1e-6


@pytest.mark.parametrize("i", [3, 5, 7])
def test_heuristics_vs_bls_with_time_dependent_waits(i):
    cfg = CONFIGS[i]
    sc = _scenario(cfg, time_dependent_waits=True, charge_step_soc=0.01)
    P = sc.prepare()
    ref = get_planner("bls").plan(P)
    assert ref.feasible
    tol = _step_tolerance(P, ref)
    for name in HEURISTICS:
        plan = get_planner(name).plan(P)
        assert plan.feasible, f"{name}: {plan.message}"
        _replay_independently(P, plan)
        assert plan.total_time_s >= ref.total_time_s - tol
        assert check_objective(plan)


def test_baselines_are_not_trivially_the_optimum():
    """The comparison is informative: somewhere the heuristics lose to the exact planner."""
    gaps = {name: [] for name in HEURISTICS}
    for i in range(len(CONFIGS)):
        s = _suite(i)
        ref = s["cfp" if HAVE_CFP else "bls"].total_time_s
        for name in HEURISTICS:
            gaps[name].append(s[name].total_time_s / ref - 1.0)
    for name in ("greedy", "station-graph"):
        assert max(gaps[name]) > 0.05
    assert all(g > -1e-9 for gs in gaps.values() for g in gs)


# ---------------------------------------------------------------------------
# naive
# ---------------------------------------------------------------------------
def test_naive_strands_and_keeps_the_plan():
    sc = grid_scenario(10, 10, seed=3, initial_soc=0.2, n_stations=0, time_dependent_waits=False)
    P = sc.prepare()
    plan = get_planner("naive").plan(P)
    assert not plan.feasible
    assert plan.stranded_at_m is not None and 0.0 < plan.stranded_at_m < plan.distance_m
    assert plan.nodes[0] == P.origin and plan.nodes[-1] == P.destination, "the full route is kept"
    assert plan.edges and plan.stops == []
    assert "battery empty" in plan.message or any("battery empty" in v for v in plan.violations)
    # independent replay: where does the battery hit zero?
    x, dist, expect = P.e_init, 0.0, None
    for e, k in zip(plan.edges, plan.levels):
        c = P.edge_energy[k][e]
        if x - c < 0.0:
            expect = dist + x / c * P.net.length[e]
            break
        x = min(x - c, P.capacity)
        dist += P.net.length[e]
    assert expect is not None
    assert plan.stranded_at_m == pytest.approx(expect)
    assert plan.internal_objective is None


def test_naive_feasible_when_the_battery_suffices():
    sc = grid_scenario(6, 6, seed=1, initial_soc=1.0, n_stations=0, time_dependent_waits=False)
    P = sc.prepare()
    plan = get_planner("naive").plan(P)
    assert plan.feasible and plan.stranded_at_m is None and plan.num_stops == 0
    _replay_independently(P, plan)
    # it is the fastest path: its time equals an independent Dijkstra distance
    dist, _ = dijkstra(P.net, P.origin, [o[0][1] for o in P.edge_options])
    assert plan.drive_time_s == pytest.approx(dist[P.destination])
    # and the optimum when no charging is needed
    exact = get_planner("bls-astar").plan(P)
    assert plan.total_time_s == pytest.approx(exact.total_time_s)


def test_naive_metric_option():
    sc = grid_scenario(8, 8, seed=2, initial_soc=0.9, n_stations=0, time_dependent_waits=False)
    P = sc.prepare()
    by_time = get_planner("naive", metric="time").plan(P)
    by_dist = get_planner("naive", metric="distance").plan(P)
    assert by_time.drive_time_s <= by_dist.drive_time_s + 1e-9
    assert by_dist.distance_m <= by_time.distance_m + 1e-6
    d, _ = dijkstra(P.net, P.origin, P.net.length)
    assert by_dist.distance_m == pytest.approx(d[P.destination])
    with pytest.raises(ValueError):
        get_planner("naive", metric="comfort").plan(P)


def test_naive_unreachable_destination_is_infeasible_plan():
    b = NetworkBuilder()
    a = b.add_node(0, 0, 0)
    z = b.add_node(1000, 0, 0)
    c = b.add_node(2000, 0, 0)
    b.add_road(a, z)
    sc = Scenario("split", b.build(), [], get_vehicle("compact50"), Query(a, c, 0.9))
    plan = get_planner("naive").plan(sc)
    assert not plan.feasible and "not reachable" in plan.message and plan.edges == []


# ---------------------------------------------------------------------------
# greedy
# ---------------------------------------------------------------------------
def test_greedy_target_energy_defaults_and_options():
    sc = grid_scenario(6, 6, seed=1, n_stations=10)
    P = sc.prepare()
    dc = next(i for i, s in enumerate(P.stations) if s.kind == "DC")
    ac = next(i for i, s in enumerate(P.stations) if s.kind == "AC")
    assert target_energy(P, dc, None) == pytest.approx(0.8 * P.capacity)
    assert target_energy(P, ac, None) == pytest.approx(P.capacity)
    assert target_energy(P, dc, 0.6) == pytest.approx(0.6 * P.capacity)
    assert target_energy(P, ac, {"AC": 0.7}) == pytest.approx(0.7 * P.capacity)
    assert target_energy(P, dc, {"AC": 0.7}) == pytest.approx(0.8 * P.capacity)
    capped = sc.with_preferences(max_charge_soc=0.7).prepare()
    assert target_energy(capped, ac, None) == pytest.approx(0.7 * capped.capacity)
    for bad in (0.0, 1.5, -0.1):
        with pytest.raises(ValueError):
            target_energy(P, dc, bad)


@pytest.mark.parametrize("i", [0, 2, 4, 6])
def test_greedy_first_stop_follows_the_documented_rule(i):
    """Replicates the selection rule independently: min (time to station + lower bound), ties -> closest to goal."""
    s = _suite(i)
    P, plan = s["P"], s["greedy"]
    assert plan.num_stops >= 1
    tree = fastest_feasible_tree(P, P.origin, P.e_init)
    lb = LowerBounds(P).time_lb
    keys = {
        k: (round(tree[st.node][0] + lb[st.node], 6), lb[st.node])
        for k, st in enumerate(P.stations)
        if st.node in tree and tree[st.node][1] < P.e_charge_cap - 1e-9
    }
    first = plan.stops[0].station
    assert keys[first] == min(keys.values()), "the first stop minimises (key, distance to goal)"
    # the nominal target is respected: charged to at least the nominal SoC (80 % DC / 100 % AC)
    for stop in plan.stops:
        nominal = 1.0 if stop.station_kind == "AC" else 0.8
        assert stop.soc_out >= min(nominal, P.e_charge_cap / P.capacity) - 1e-9
    assert len({stop.station for stop in plan.stops}) == plan.num_stops, "no station is used twice"


@pytest.mark.parametrize("final_leg", ["fastest", "feasible"])
def test_greedy_final_leg_modes(final_leg):
    for i in (1, 3, 6):
        s = _suite(i)
        P = s["P"]
        plan = get_planner("greedy", final_leg=final_leg).plan(P)
        assert plan.feasible, plan.message
        _replay_independently(P, plan)
        if HAVE_CFP:
            assert plan.total_time_s >= s["cfp"].total_time_s - 1e-6
    with pytest.raises(ValueError):
        get_planner("greedy", final_leg="slowest").plan(_suite(0)["P"])


def test_greedy_needs_no_stop_when_the_fastest_path_is_drivable():
    sc = grid_scenario(6, 6, seed=1, initial_soc=1.0, n_stations=8, time_dependent_waits=False)
    P = sc.prepare()
    plan = get_planner("greedy").plan(P)
    naive = get_planner("naive").plan(P)
    assert plan.feasible and plan.num_stops == 0
    assert plan.edges == naive.edges


def test_greedy_failure_modes():
    P = _suite(0)["P"]
    # no stop allowed although one is needed
    plan = get_planner("greedy", max_stops=0).plan(P)
    assert not plan.feasible and "maximum of 0" in plan.message and plan.edges == []
    # no stations at all
    sc = grid_scenario(8, 8, seed=3, initial_soc=0.25, n_stations=0, time_dependent_waits=False)
    plan = get_planner("greedy").plan(sc)
    assert not plan.feasible and "no unused charging station" in plan.message
    # the start energy lies below the reserve and there is no charger at the origin
    sc = grid_scenario(8, 8, seed=3, initial_soc=0.05, n_stations=6, time_dependent_waits=False)
    plan = get_planner("greedy").plan(sc)
    assert not plan.feasible
    with pytest.raises(ValueError):
        get_planner("greedy", max_stops=-1).plan(P)


def test_greedy_can_charge_at_the_origin():
    sc = grid_scenario(6, 6, seed=2, initial_soc=0.05, n_stations=8, time_dependent_waits=False)
    st = list(sc.stations)
    st[0] = dataclasses.replace(st[0], node=sc.query.origin, power_kw=150.0, kind="DC")
    sc = sc.with_(stations=st)
    P = sc.prepare()
    plan = get_planner("greedy").plan(P)
    assert plan.feasible, plan.message
    assert plan.stops[0].position == 0 and plan.stops[0].station == 0
    _replay_independently(P, plan)


def test_greedy_progress_rule_raises_the_target():
    """A nominal target that cannot bridge the next gap is raised, not abandoned."""
    sc = _line_scenario(n=7, soc=0.5, stations=((2, "DC", 150.0), (4, "DC", 150.0)))
    P = sc.prepare()
    low = get_planner("greedy", charge_to_soc=0.15).plan(P)
    assert low.feasible, low.message
    assert all(stop.soc_out > 0.15 for stop in low.stops)
    assert low.num_stops >= 1
    _replay_independently(P, low)


# ---------------------------------------------------------------------------
# station graph
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tdw", [False, True])
@pytest.mark.parametrize("i", [0, 3, 5, 7])
def test_station_graph_internal_objective_matches_the_evaluator(i, tdw):
    P = _scenario(CONFIGS[i], time_dependent_waits=tdw).prepare()
    plan = get_planner("station-graph").plan(P)
    assert plan.feasible, plan.message
    assert plan.internal_objective is not None
    assert plan.internal_objective == pytest.approx(plan.objective_value, rel=1e-12, abs=1e-8)
    assert check_objective(plan)
    # every stop charges to the fixed target (100 % by default)
    for stop in plan.stops:
        assert stop.energy_out_kwh == pytest.approx(P.e_charge_cap) or stop.energy_in_kwh >= P.e_charge_cap


def test_station_graph_waits_use_the_real_arrival_time():
    """With strongly time-varying queues the meta Dijkstra must still match the evaluator."""
    sc = _scenario(CONFIGS[3], time_dependent_waits=True)
    sts = [dataclasses.replace(s, plugs=2, daily_sessions=34.0, mean_service_min=40.0, demand_profile="highway")
           for s in sc.stations]
    sc = sc.with_(stations=sts).with_query(departure_s=6.5 * 3600.0)
    P = sc.prepare()
    assert P.time_dependent
    plan = get_planner("station-graph").plan(P)
    assert plan.feasible, plan.message
    assert plan.num_stops >= 1
    assert plan.internal_objective == pytest.approx(plan.objective_value, rel=1e-12, abs=1e-8)
    # the queue at arrival differs from the snapshot at departure: waits really are time dependent
    assert any(abs(s.wait_s - P.wait_s(s.station, 0.0)) > 1.0 for s in plan.stops)


@pytest.mark.parametrize("tdw", [False, True])
def test_station_graph_equals_brute_force_over_station_sequences(tdw):
    """Optimum of the meta graph == best of all station sequences, scored by the evaluator."""
    P = grid_scenario(6, 6, seed=0, initial_soc=0.3, n_stations=7, time_dependent_waits=tdw).prepare()
    T = P.e_charge_cap
    trees: dict = {}

    def tree(node, energy):
        key = (node, energy)
        if key not in trees:
            trees[key] = fastest_feasible_tree(P, node, energy, min_arrival_energy={P.destination: P.e_dest_min})
        return trees[key]

    best, best_len = math.inf, -1
    for k in range(0, 3):
        for seq in itertools.permutations(range(len(P.stations)), k):
            node, energy = P.origin, P.e_init
            edges, levels, charges, ok = [], [], [], True
            for s in seq:
                rec = tree(node, energy).get(P.stations[s].node)
                if rec is None or P.stations[s].node == node and node != P.origin:
                    ok = False
                    break
                edges += rec[2]
                levels += rec[3]
                charges.append(ChargeDecision(len(edges), s, T))
                node, energy = P.stations[s].node, T
            if not ok:
                continue
            rec = tree(node, energy).get(P.destination)
            if rec is None:
                continue
            plan = evaluate(P, PlanDecision(edges + rec[2], levels + rec[3], charges))
            assert plan.feasible
            if plan.total_time_s < best:
                best, best_len = plan.total_time_s, k
    assert best < math.inf and best_len >= 1, "instance must need a stop"
    sg = get_planner("station-graph", detour_factor=math.inf, widen=False).plan(P)
    assert sg.feasible
    assert sg.total_time_s <= best + 1e-6
    if sg.num_stops <= 2:
        assert sg.total_time_s == pytest.approx(best, abs=1e-6)


def test_station_graph_options_target_and_ellipse():
    P = _suite(3)["P"]
    plan = get_planner("station-graph", charge_to_soc=0.6).plan(P)
    assert plan.feasible
    assert plan.stats.extra["target_soc"] == pytest.approx(0.6)
    for stop in plan.stops:
        assert stop.soc_out == pytest.approx(0.6) or stop.soc_in >= 0.6
    # a degenerate ellipse contains no station: infeasible without widening, feasible with it
    strict = get_planner("station-graph", detour_factor=1.0, widen=False).plan(P)
    assert not strict.feasible and strict.stats.extra["stations_in_ellipse"] == 0
    widened = get_planner("station-graph", detour_factor=1.0).plan(P)
    assert widened.feasible and widened.stats.extra["attempts"] > 1
    with pytest.raises(ValueError):
        get_planner("station-graph", charge_to_soc=1.2).plan(P)
    with pytest.raises(ValueError):
        get_planner("station-graph", detour_factor=0.5).plan(P)


def test_detour_candidates_are_monotone_in_the_factor():
    P = _suite(1)["P"]
    sizes = [len(detour_candidates(P, f)) for f in (1.0, 1.2, 1.6, 2.5, math.inf)]
    assert sizes == sorted(sizes) and sizes[-1] == len(P.stations)
    assert set(detour_candidates(P, 1.2)) <= set(detour_candidates(P, 1.6))
    net = P.net
    o, d = P.origin, P.destination
    d_od = math.hypot(net.x[d] - net.x[o], net.y[d] - net.y[o])
    for s in detour_candidates(P, 1.6):
        v = P.stations[s].node
        detour = math.hypot(net.x[v] - net.x[o], net.y[v] - net.y[o]) + math.hypot(net.x[v] - net.x[d], net.y[v] - net.y[d])
        assert detour <= 1.6 * d_od + 1e-6


# ---------------------------------------------------------------------------
# genetic algorithm
# ---------------------------------------------------------------------------
def test_genetic_is_deterministic_for_a_fixed_seed():
    P = _suite(3)["P"]
    a = get_planner("genetic", seed=5).plan(P)
    b = get_planner("genetic", seed=5).plan(P)
    assert a.feasible and b.feasible
    assert a.edges == b.edges and a.levels == b.levels
    assert [(c.position, c.station, c.target_kwh) for c in a.decision.charges] == [
        (c.position, c.station, c.target_kwh) for c in b.decision.charges
    ]
    assert a.stats.extra["history"] == b.stats.extra["history"]
    assert a.stats.extra["history_mean"] == b.stats.extra["history_mean"]
    assert a.stats.settled == b.stats.settled
    # the default seed (0) is just as reproducible, and other seeds give valid plans too
    c0 = get_planner("genetic").plan(P)
    assert c0.edges == get_planner("genetic", seed=0).plan(P).edges
    for seed in (1, 2):
        other = get_planner("genetic", seed=seed).plan(P)
        assert other.feasible
        _replay_independently(P, other)


def test_genetic_does_not_touch_the_global_random_state():
    P = _suite(0)["P"]
    random.seed(1234)
    expected = random.random()
    random.seed(1234)
    get_planner("genetic", population=10, generations=5).plan(P)
    assert random.random() == expected


def test_genetic_convergence_history():
    P = _suite(3)["P"]
    plan = get_planner("genetic", population=24, generations=30, patience=100).plan(P)
    ex = plan.stats.extra
    hist = ex["history"]
    assert len(hist) == ex["generations_run"] + 1 == 31
    assert ex["stop_reason"] == "generations"
    feas = [h for h in hist if h is not None]
    assert feas and all(b <= a + 1e-9 for a, b in zip(feas, feas[1:])), "best feasible objective never worsens"
    assert hist[-1] == pytest.approx(plan.objective_value), "the history ends at the returned plan"
    assert len(ex["history_fitness"]) == len(ex["history_mean"]) == len(ex["history_feasible"]) == len(hist)
    assert all(0.0 <= f <= 1.0 for f in ex["history_feasible"])
    assert all(b <= a + 1e-9 for a, b in zip(ex["history_fitness"], ex["history_fitness"][1:])), "elitism"
    assert ex["decodes"] == plan.stats.settled and ex["cache_hits"] >= 0
    assert ex["greedy_seed"] is not None
    # early stopping on stagnation
    short = get_planner("genetic", population=24, generations=60, patience=2).plan(P)
    assert short.stats.extra["stop_reason"] == "stagnation" and short.stats.extra["generations_run"] < 60
    # the history is JSON serialisable (convergence plots, viewer)
    json.dumps(plan.stats.to_dict())


def test_genetic_improves_on_its_greedy_seed():
    """With a generation budget the GA beats the plain greedy plan on at least one of the instances."""
    wins = 0
    for i in range(len(CONFIGS)):
        s = _suite(i)
        assert s["genetic"].total_time_s <= s["genetic"].stats.extra["history"][0] + 1e-9
        if s["genetic"].total_time_s < s["greedy"].total_time_s - 1.0:
            wins += 1
    assert wins >= len(CONFIGS) // 2


def test_genetic_options_and_edge_cases():
    P = _suite(0)["P"]
    zero = get_planner("genetic", generations=0, population=8).plan(P)
    assert zero.feasible and len(zero.stats.extra["history"]) == 1 and zero.stats.extra["generations_run"] == 0
    # without any stop the plain fastest path is infeasible here
    none = get_planner("genetic", max_stops=0).plan(P)
    assert not none.feasible
    # without the greedy seed the GA still finds a feasible plan
    noseed = get_planner("genetic", seed_greedy=False, population=30, generations=40).plan(P)
    assert noseed.feasible and noseed.stats.extra["greedy_seed"] is None
    for bad in ({"population": 1}, {"elite": 40}, {"detour_factor": 0.2}):
        with pytest.raises(ValueError):
            get_planner("genetic", **bad).plan(P)


def test_genetic_supports_the_cost_objective():
    sc = _scenario(CONFIGS[3], objective="cost")
    P = sc.prepare()
    plan = get_planner("genetic", population=30, generations=40).plan(P)
    assert plan.feasible and plan.objective == "cost"
    assert plan.objective_value == pytest.approx(plan.generalized_cost)
    assert check_objective(plan)
    ref = get_planner("bls").plan(_scenario(CONFIGS[3], objective="cost", charge_step_soc=0.01).prepare())
    assert plan.generalized_cost >= ref.generalized_cost - 0.05 * ref.generalized_cost


def _profile_scenario(kind: str) -> Scenario:
    if kind == "flat":
        return grid_scenario(7, 7, 12000.0, 6, vehicle="city27", seed=11, initial_soc=0.5, time_dependent_waits=False)
    # tiny battery on a hilly network: regeneration is clamped at full capacity on the descents
    net = grid_network(6, 6, 6000.0, seed=2, relief_m=1500.0)
    return Scenario(
        "hilly",
        net,
        place_stations(net, 5, seed=2, exclude={0, net.num_nodes - 1}),
        get_vehicle("city27").with_(battery_kwh=8.0),
        Query(0, net.num_nodes - 1, 0.9, 9 * 3600.0),
        Conditions(),
        Preferences(time_dependent_waits=False),
    )


@pytest.mark.parametrize("kind", ["flat", "hilly"])
def test_genetic_leg_profile_matches_the_evaluator(kind):
    """``need`` / ``ok`` / ``out`` of a leg are exactly the feasibility threshold of the evaluator."""
    sc = _profile_scenario(kind)
    P = sc.prepare()
    prob = _Problem(P, None, 20.0)
    prob.target_nodes = set(range(P.net.num_nodes))
    rng = random.Random(5)
    n_feasible = n_infeasible = n_clamped = 0
    for _ in range(40):
        u, v = rng.sample(range(P.net.num_nodes), 2)
        lg = prob.leg(u, v)
        assert lg is not None and lg.edges
        drivable = lg.ok and lg.out >= P.e_dest_min - 1e-9 and lg.need_last <= P.capacity - 1e-3
        decision = PlanDecision(lg.edges, lg.levels)
        if drivable:
            for x, expect in ((lg.need_last + 1e-4, True), (lg.need_last - 1e-3, False)):
                P2 = sc.with_query(origin=u, destination=v, initial_soc=x / P.capacity).prepare()
                assert evaluate(P2, decision).feasible is expect
            n_feasible += 1
        else:
            P2 = sc.with_query(origin=u, destination=v, initial_soc=1.0).prepare()
            assert not evaluate(P2, decision).feasible
            n_infeasible += 1
        # the final energy is f(x) = min(x - cost, out), also where regeneration was clamped
        P3 = sc.with_query(origin=u, destination=v, initial_soc=0.9).prepare()
        plan = evaluate(P3, decision)
        assert plan.node_energy_arrive[-1] == pytest.approx(min(P3.e_init - lg.cost, lg.out), abs=1e-9)
        if lg.out < P.capacity - lg.cost - 1e-9:
            n_clamped += 1
    assert n_feasible > 3
    if kind == "hilly":
        assert n_infeasible >= 1, "the test must exercise undrivable legs"
        assert n_clamped >= 1, "the test must exercise the capacity clamp"


def test_genetic_decoder_feasibility_criterion():
    """A decoded chromosome is feasible iff every leg is drivable with the allowed charge."""
    sc = _scenario(CONFIGS[4])
    P = sc.prepare()
    prob = _Problem(P, None, 20.0)
    prob.target_nodes = {st.node for st in P.stations} | {P.destination}
    cand = list(range(len(P.stations)))
    dist_o, _ = prob.tree(P.origin)
    rank = {s: dist_o[P.stations[s].node] for s in cand}
    evo = _Evolution(prob, random.Random(3), cand, rank, max_stops=4, range_s=3000.0, route_s=dist_o[P.destination])
    scale = 10.0 * prob.calibrate()
    assert prob.scale == pytest.approx(scale) and scale > 0
    seen = {True: 0, False: 0}
    for n in range(120):
        genes = evo.random_chromosome()
        if genes and n % 3 == 0:
            genes = evo.mutate(genes)
        dec = prob.decode(genes)
        assert dec is not None
        plan = evaluate(P, PlanDecision(*dec))
        nodes = [P.origin] + [P.stations[g[0]].node for g in genes] + [P.destination]
        legs = [prob.leg(a, b) for a, b in zip(nodes, nodes[1:])]
        k = len(genes)
        expect = all(lg.ok for lg in legs) and legs[-1].out >= P.e_dest_min - 1e-9
        for j, lg in enumerate(legs):
            need = lg.need_last if j == k else lg.need
            if j == 0:
                expect = expect and (not lg.edges or need <= P.e_init + 1e-9)
            else:
                expect = expect and need <= P.e_charge_cap + 1e-9
        assert plan.feasible == expect, (genes, plan.message)
        seen[plan.feasible] += 1
        # fitness: the objective when feasible, a strictly larger penalised value otherwise
        fit, ok, obj = prob.fitness(genes)
        assert ok == plan.feasible and obj == pytest.approx(plan.objective_value)
        if plan.feasible:
            assert fit == pytest.approx(plan.objective_value)
        else:
            assert fit >= prob.scale + plan.objective_value - 1e-9
            assert prob.shortfall_kwh(plan) > 0.0
        assert prob.fitness(genes) == (fit, ok, obj), "cached"
    assert seen[True] > 5 and seen[False] > 5


def test_genetic_penalty_grows_with_the_violation():
    """Shortfall below the reserve is measured on the replay and drives the penalty."""
    P = _suite(0)["P"]
    prob = _Problem(P, None, 20.0)
    prob.target_nodes = {P.destination}
    prob.calibrate()
    naive = _suite(0)["naive"]
    short = prob.shortfall_kwh(naive)
    arrive, depart = naive.node_energy_arrive, naive.node_energy_depart
    worst = min(min(arrive[1:]), min(depart[:-1]))
    assert short == pytest.approx(max(0.0, P.e_min - worst) + max(0.0, P.e_dest_min - arrive[-1]))
    assert short > 0.0
    # a longer drive on the same battery undercuts the reserve by more
    P2 = _scenario(CONFIGS[0]).with_query(initial_soc=0.2).prepare()
    plan2 = get_planner("naive").plan(P2)
    assert prob.shortfall_kwh(plan2) > short


def test_genetic_operators_keep_chromosomes_valid():
    P = _suite(4)["P"]
    prob = _Problem(P, None, 20.0)
    prob.target_nodes = {st.node for st in P.stations} | {P.destination}
    dist_o, _ = prob.tree(P.origin)
    cand = [s for s in range(len(P.stations)) if dist_o[P.stations[s].node] < math.inf]
    rank = {s: dist_o[P.stations[s].node] for s in cand}
    evo = _Evolution(prob, random.Random(8), cand, rank, max_stops=3, range_s=4000.0, route_s=dist_o[P.destination])

    def valid(c):
        assert len(c) <= 3
        nodes = [P.stations[g[0]].node for g in c]
        assert len(set(nodes)) == len(nodes), "one stop per node"
        assert all(g[0] in cand and evo.soc_lo - 1e-9 <= g[1] <= evo.soc_hi + 1e-9 for g in c)

    pop = [evo.random_chromosome() for _ in range(30)]
    for c in pop:
        valid(c)
    for _ in range(300):
        a, b = evo.rng.sample(pop, 2)
        for child in evo.crossover(a, b):
            valid(child)
            # order preserving: genes of a child that come from one parent keep that parent's relative order
            order_a = {g[0]: j for j, g in enumerate(a)}
            from_a = [order_a[g[0]] for g in child if g[0] in order_a and g in a]
            assert from_a == sorted(from_a)
        valid(evo.mutate(a))
    assert evo.mutate(()) != ()  # an empty chromosome can only grow


# ---------------------------------------------------------------------------
# cross-cutting: trace, stats, options, serialisation, edge cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["naive", "greedy", "station-graph", "genetic"])
def test_trace_stats_and_json(name):
    P = _suite(2)["P"]
    plan = get_planner(name).plan(P, trace=True)
    assert plan.trace is not None and len(plan.trace.nodes) > 1
    assert len(set(plan.trace.nodes)) == len(plan.trace.nodes)
    assert all(0 <= v < P.net.num_nodes for v in plan.trace.nodes)
    assert len(plan.trace.keys) == len(plan.trace.nodes)
    assert P.origin in plan.trace.nodes
    assert plan.stats.algorithm == name and plan.stats.settled > 0 and plan.stats.runtime_s > 0
    plain = get_planner(name).plan(P)
    assert plain.trace is None
    assert plain.edges == plan.edges, "tracing does not change the result"
    blob = json.dumps(plan.to_dict())
    assert json.loads(blob)["algorithm"] == name


@pytest.mark.parametrize("name", ["naive", "greedy", "station-graph", "genetic"])
def test_origin_equals_destination(name):
    sc = grid_scenario(5, 5, seed=1, initial_soc=0.6, n_stations=5)
    sc = sc.with_query(destination=sc.query.origin)
    plan = get_planner(name).plan(sc)
    assert plan.feasible and plan.edges == [] and plan.total_time_s == 0.0 and plan.num_stops == 0


@pytest.mark.parametrize("name", ["greedy", "station-graph", "genetic"])
def test_start_below_reserve_with_a_charger_at_the_origin(name):
    sc = grid_scenario(6, 6, seed=2, initial_soc=0.05, n_stations=8, time_dependent_waits=False)
    st = list(sc.stations)
    st[0] = dataclasses.replace(st[0], node=sc.query.origin, power_kw=150.0, kind="DC")
    P = sc.with_(stations=st).prepare()
    assert P.e_init < P.e_min
    plan = get_planner(name).plan(P)
    assert plan.feasible, plan.message
    assert plan.stops and plan.stops[0].position == 0
    _replay_independently(P, plan)
    if HAVE_CFP:
        assert plan.total_time_s >= get_planner("cfp").plan(P).total_time_s - 1e-6


@pytest.mark.parametrize("name", ["greedy", "station-graph", "genetic"])
def test_infeasible_instances_return_infeasible_plans(name):
    sc = grid_scenario(8, 8, seed=3, initial_soc=0.25, n_stations=0, time_dependent_waits=False)
    plan = get_planner(name).plan(sc)
    assert not plan.feasible and plan.message and plan.stats is not None
    assert plan.stranded_at_m is None


def test_line_instance_all_baselines():
    sc = _line_scenario()
    P = sc.prepare()
    naive = get_planner("naive").plan(P)
    assert not naive.feasible and naive.stranded_at_m is not None
    assert naive.stranded_at_m == pytest.approx(150000.0, rel=0.01)
    exact = get_planner("cfp" if HAVE_CFP else "bls").plan(P)
    assert exact.feasible and exact.num_stops == 1
    plans = {n: get_planner(n).plan(P) for n in HEURISTICS}
    for name, plan in plans.items():
        assert plan.feasible, plan.message
        _replay_independently(P, plan)
        stations_on_route = [P.stations[s.station].node for s in plan.stops]
        assert stations_on_route == sorted(stations_on_route), "stops in driving order"
        assert plan.total_time_s >= exact.total_time_s - 1e-6
    # the stations are collinear, so the greedy keys tie and the station closest to the goal
    # within reach wins (a driver goes as far as the battery allows): node 3 (50 kW DC)
    assert P.stations[plans["greedy"].stops[0].station].node == 3
    # the GA solves a one-stop problem (charge just enough at the best charger) to optimality
    assert plans["genetic"].total_time_s == pytest.approx(exact.total_time_s, rel=5e-3)
    # the fixed 100 % target (and 80 % for greedy) costs time compared with charging just enough
    assert plans["station-graph"].total_time_s > exact.total_time_s * 1.2
    assert plans["greedy"].total_time_s > exact.total_time_s * 1.1


def test_multiple_speed_levels():
    sc = grid_scenario(8, 8, seed=4, initial_soc=0.3, n_stations=12, speed_factors=(1.0, 0.85), time_dependent_waits=False)
    P = sc.prepare()
    assert len(P.speed_factors) == 2
    ref = get_planner("cfp" if HAVE_CFP else "bls").plan(P)
    for name in ("naive",) + HEURISTICS:
        plan = get_planner(name, **({"population": 20, "generations": 20} if name == "genetic" else {})).plan(P)
        if name != "naive":
            assert plan.feasible, f"{name}: {plan.message}"
            _replay_independently(P, plan)
            assert plan.total_time_s >= ref.total_time_s - (1e-6 if HAVE_CFP else 60.0)
        # the naive/greedy/GA legs use the fastest level of every edge
        if name in ("naive", "genetic"):
            fastest = fastest_levels(P)
            assert all(lv == fastest[e] for e, lv in zip(plan.edges, plan.levels))


def test_shared_helpers():
    P = _suite(0)["P"]
    w = time_weights(P)
    dist, pred, settled = dijkstra_traced(P.net, P.origin, w)
    ref, _ = dijkstra(P.net, P.origin, w)
    assert dist == ref and settled == P.net.num_nodes
    # early stopping settles fewer nodes but gets the target right
    d2, _, s2 = dijkstra_traced(P.net, P.origin, w, target=P.destination)
    assert d2[P.destination] == ref[P.destination] and s2 <= settled
    rd, _, _ = dijkstra_traced(P.net, P.destination, w, reverse=True)
    assert rd[P.origin] == pytest.approx(ref[P.destination])
    # drive_path replays the evaluator's arithmetic
    plan = _suite(0)["greedy"]
    first = plan.stops[0].position
    ok, x = drive_path(P, plan.edges[:first], plan.levels[:first], P.e_init, e_final_min=P.e_min)
    assert ok and x == pytest.approx(plan.stops[0].energy_in_kwh)
    assert not drive_path(P, _suite(0)["naive"].edges, _suite(0)["naive"].levels, P.e_init)[0]
    ok, x = run_greedy(P).ok, None
    assert ok
