"""Tests of the Monte Carlo robustness analysis (``evroute.robustness``).

Every Monte Carlo statement is cross-checked against the independent
evaluator (``evroute.evaluate.evaluate``): zero noise must reproduce the
deterministic evaluation bit for bit, and every sampled run must agree with
an evaluation of the same sampled world.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from itertools import pairwise
from statistics import NormalDist

import pytest

import evroute.robustness as rb
from evroute import (
    ChargeDecision,
    NetworkBuilder,
    PlanDecision,
    Preferences,
    Query,
    Scenario,
    evaluate,
    get_vehicle,
)
from evroute.algorithms import plan
from evroute.evaluate import infeasible_plan
from evroute.generators import grid_scenario
from evroute.robustness import (
    NotARouteError,
    Summary,
    lognormal_params,
    monte_carlo,
    quantile,
    replay_run,
    reserve_sweep,
    safety_sweep,
    wilson_interval,
)
from evroute.scenario import Station
from evroute.search import dijkstra, min_level_weights, path_from_pred

NOISY = {"trip_cv": 0.15, "edge_cv": 0.25}


# ------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def one_stop():
    """8x8 grid, one charging stop, tight at the destination."""
    sc = grid_scenario(rows=8, cols=8, seed=0, initial_soc=0.3)
    route = plan(sc, "bls-astar")
    assert route.feasible and route.num_stops == 1
    return sc, sc.prepare(), route


@pytest.fixture(scope="module")
def two_stops():
    """Small car, two stops with non-trivial time-dependent queues."""
    sc = grid_scenario(rows=12, cols=12, seed=3, initial_soc=0.2, vehicle="city27", n_stations=25)
    route = plan(sc, "bls-astar")
    assert route.feasible and route.num_stops == 2
    assert sc.prepare().time_dependent
    return sc, sc.prepare(), route


@pytest.fixture(scope="module")
def low_reserve():
    """Thin reserve so that stranding is observable at moderate noise."""
    sc = grid_scenario(rows=8, cols=8, seed=1, initial_soc=0.3, reserve_soc=0.03, min_arrival_soc=0.03)
    route = plan(sc, "bls-astar")
    assert route.feasible
    return sc, sc.prepare(), route


def _driving_min(p) -> float:
    """Lowest energy while driving, read from an evaluated RoutePlan."""
    if not p.edges:
        return p.node_energy_depart[-1]
    return min(min(p.node_energy_depart[:-1]), min(p.node_energy_arrive[1:]))


def _strip(d: dict) -> dict:
    d = dict(d)
    d.pop("runtime_s", None)
    return d


# ------------------------------------------------------------ statistics
def test_lognormal_params_have_mean_one_and_requested_cv():
    for cv in (0.0, 0.01, 0.06, 0.3, 1.0, 2.5):
        mu, s = lognormal_params(cv)
        assert math.exp(mu + s * s / 2.0) == pytest.approx(1.0, abs=1e-15)
        assert math.sqrt(math.expm1(s * s)) == pytest.approx(cv, rel=1e-12, abs=1e-15)
    assert lognormal_params(0.0) == (0.0, 0.0)
    for bad in (-0.1, math.nan, math.inf):
        with pytest.raises(ValueError):
            lognormal_params(bad)


def test_splitmix64_reference_vectors_and_open_unit_interval():
    # first outputs of SplitMix64 seeded with 0 (Steele, Lea & Flood 2014)
    expected = [0xE220A8397B1DCDAF, 0x6E789E6AA1B965F4, 0x06C45D188009454F]
    for j, want in enumerate(expected):
        u = rb._uniform(0, rb._stream_offset(j))
        assert u == ((want >> 12) + 0.5) * 2.0**-52
    # the mapping of the extreme 52-bit integers stays strictly inside (0, 1)
    assert 0.0 < (0 + 0.5) * rb._INV_2_52 and ((2**52 - 1) + 0.5) * rb._INV_2_52 < 1.0
    us = [rb._uniform(123456789, rb._stream_offset(j)) for j in range(20000)]
    assert all(0.0 < u < 1.0 for u in us)
    assert statistics.fmean(us) == pytest.approx(0.5, abs=0.01)
    for q in (0.1, 0.5, 0.9):
        assert sum(u < q for u in us) / len(us) == pytest.approx(q, abs=0.015)


def test_quantile_matches_statistics_inclusive_method():
    rng = random.Random(4)
    data = [rng.gauss(0, 1) for _ in range(257)]
    xs = sorted(data)
    cuts = statistics.quantiles(data, n=20, method="inclusive")
    assert quantile(xs, 0.05) == pytest.approx(cuts[0], abs=1e-12)
    assert quantile(xs, 0.50) == pytest.approx(cuts[9], abs=1e-12)
    assert quantile(xs, 0.95) == pytest.approx(cuts[18], abs=1e-12)
    assert quantile(xs, 0.0) == xs[0] and quantile(xs, 1.0) == xs[-1]
    assert quantile([3.0], 0.37) == 3.0
    with pytest.raises(ValueError):
        quantile([], 0.5)


def test_wilson_interval():
    z = 1.959963984540054
    lo, hi = wilson_interval(0, 100)
    assert lo == 0.0 and hi == pytest.approx(z * z / (100 + z * z))
    lo, hi = wilson_interval(30, 100)
    assert lo < 0.3 < hi
    lo2, hi2 = wilson_interval(70, 100)
    assert lo2 == pytest.approx(1 - hi) and hi2 == pytest.approx(1 - lo)
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_summary_of():
    rng = random.Random(1)
    xs = [rng.uniform(0, 10) for _ in range(101)]
    s = Summary.of(xs)
    assert s.count == 101
    assert s.mean == pytest.approx(statistics.fmean(xs))
    assert s.std == pytest.approx(statistics.stdev(xs))
    assert s.min == min(xs) and s.max == max(xs)
    assert s.p50 == pytest.approx(statistics.median(xs))
    empty = Summary.of([])
    assert empty.count == 0 and empty.mean is None and empty.to_dict()["p95"] is None


# ------------------------------------------------------- reproducibility
def test_determinism_by_seed(one_stop):
    _, P, route = one_stop
    a = monte_carlo(P, route, n=200, seed=7, keep_samples=True)
    b = monte_carlo(P, route, n=200, seed=7, keep_samples=True)
    c = monte_carlo(P, route, n=200, seed=8, keep_samples=True)
    assert _strip(a.to_dict()) == _strip(b.to_dict())
    assert a.samples["total_time_s"] != c.samples["total_time_s"]
    assert a.samples["min_soc"] != c.samples["min_soc"]


def test_common_random_numbers_prefix_property(one_stop):
    """Run r is a function of (seed, r) only: small runs are a prefix of large ones."""
    _, P, route = one_stop
    small = monte_carlo(P, route, n=40, seed=3, keep_samples=True).samples
    large = monte_carlo(P, route, n=120, seed=3, keep_samples=True).samples
    for k, v in small.items():
        assert large[k][:40] == v


def test_inputs_scenario_and_plan_decision_are_equivalent(one_stop):
    sc, P, route = one_stop
    a = monte_carlo(P, route, n=100, seed=2).to_dict()
    b = monte_carlo(sc, route.decision, n=100, seed=2).to_dict()
    for d in (a, b):
        d.pop("runtime_s")
        d.pop("algorithm")
    assert a == b


# ---------------------------------------------------- exactness (zero noise)
@pytest.mark.parametrize("policy", ["same-target", "adaptive"])
def test_zero_noise_reproduces_the_evaluator_exactly(one_stop, two_stops, policy):
    for _, P, route in (one_stop, two_stops):
        rep = monte_carlo(
            P, route, n=5, seed=0, trip_cv=0.0, edge_cv=0.0, wait_model="expected",
            policy=policy, keep_samples=True, verify=5,
        )
        assert rep.p_strand == rep.p_reserve_breach == rep.p_arrival_short == 0.0
        assert rep.n_completed == 5
        assert set(rep.samples["total_time_s"]) == {route.total_time_s}  # bit for bit
        assert set(rep.samples["arrival_soc"]) == {route.arrival_soc}
        assert set(rep.samples["min_soc"]) == {_driving_min(route) / P.capacity}
        assert set(rep.samples["charged_kwh"]) == {route.energy_charged_kwh}
        assert set(rep.samples["trip_factor"]) == {1.0}
        assert rep.total_time_s.std == 0.0
        assert rep.planned_feasible and rep.planned_time_s == route.total_time_s
        assert rep.planned_arrival_soc == route.arrival_soc
        assert rep.planned_stops == route.num_stops


def test_zero_noise_without_queues_removes_exactly_the_waits(two_stops):
    _, P, route = two_stops
    rep = monte_carlo(P, route, n=3, trip_cv=0.0, edge_cv=0.0, wait_model="none", keep_samples=True)
    assert rep.p_arrival_short == 0.0
    assert rep.samples["wait_s"] == [0.0] * 3
    assert rep.total_time_s.mean == pytest.approx(route.total_time_s - route.wait_time_s, abs=1e-6)
    assert rep.wait_s.max == 0.0


def test_zero_noise_with_safety_factor_replays_the_nominal_model():
    sc = grid_scenario(rows=8, cols=8, seed=2, initial_soc=0.3, safety_factor=1.15)
    P = sc.prepare()
    route = plan(sc, "bls-astar")
    assert route.feasible
    dec = route.decision
    nominal = [P.edge_energy_nominal[k][a] for a, k in zip(dec.edges, dec.levels)]
    assert any(c != P.edge_energy[k][a] for c, a, k in zip(nominal, dec.edges, dec.levels))
    ref = evaluate(P, dec, energy_override=nominal)
    for policy in ("same-target", "adaptive"):  # the planner over-estimated: no adaptive reaction
        rep = monte_carlo(P, route, n=3, trip_cv=0.0, edge_cv=0.0, wait_model="expected",
                          policy=policy, keep_samples=True)
        assert set(rep.samples["total_time_s"]) == {ref.total_time_s}
        assert set(rep.samples["arrival_soc"]) == {ref.arrival_soc}
        assert rep.p_strand == rep.p_reserve_breach == rep.p_arrival_short == 0.0
        # the nominal world is kinder than the plan expected
        assert ref.arrival_soc > route.arrival_soc


# ------------------------------------------ cross-checks with the evaluator
@pytest.mark.parametrize("policy", ["same-target", "adaptive"])
@pytest.mark.parametrize("wait_model", ["exponential", "expected", "none"])
def test_every_run_agrees_with_the_evaluator(one_stop, two_stops, low_reserve, policy, wait_model):
    events = {"strand": 0, "breach": 0, "ok": 0}
    for _, P, route in (one_stop, two_stops, low_reserve):
        # verify=n re-evaluates every run with evaluate(); raises on any mismatch
        rep = monte_carlo(P, route, n=60, seed=11, policy=policy, wait_model=wait_model, verify=60, **NOISY)
        events["strand"] += rep.n_strand
        events["breach"] += rep.n_reserve_breach - rep.n_strand
        events["ok"] += rep.n - rep.n_arrival_short
    assert all(v > 0 for v in events.values())  # every outcome class was cross-checked


def test_replay_run_is_the_sampled_world(low_reserve):
    _, P, route = low_reserve
    kw = {"seed": 5, "trip_cv": 0.2, "edge_cv": 0.3}
    rep = monte_carlo(P, route, n=60, keep_samples=True, **kw)
    s = rep.samples
    assert 0 < rep.n_strand < 60  # both outcomes occur in the checked runs
    for r in range(60):
        p = replay_run(P, route, r, **kw)
        assert p.total_time_s == s["total_time_s"][r]
        assert p.arrival_soc == s["arrival_soc"][r]
        assert _driving_min(p) / P.capacity == s["min_soc"][r]
        assert (p.stranded_at_m is not None) == s["stranded"][r]
        assert p.feasible == (not s["reserve_breach"][r] and not s["arrival_short"][r])
        assert p.algorithm.endswith(f"MC run {r}")


def test_expected_waits_are_taken_at_the_realised_arrival_time(two_stops):
    _, P, route = two_stops
    for r in range(15):
        p = replay_run(P, route, r, seed=1, wait_model="expected", **NOISY)
        for st in p.stops:
            assert st.wait_s == P.wait_s(st.station, st.arrival_s)
    # exponential: the draw scales the expected wait at the realised time
    ratios = []
    for r in range(15):
        p = replay_run(P, route, r, seed=1, wait_model="exponential", **NOISY)
        st = p.stops[1]
        ratios.append(st.wait_s / P.wait_s(st.station, st.arrival_s))
    assert all(x > 0 for x in ratios) and len(set(ratios)) == len(ratios)


def test_exponential_wait_has_the_expected_mean(one_stop):
    _, P, route = one_stop
    stop = route.stops[0]
    mean = P.wait_s(stop.station, stop.arrival_s)
    assert mean > 1.0
    n = 4000
    rep = monte_carlo(P, route, n=n, seed=9, trip_cv=0.0, edge_cv=0.0, keep_samples=True)
    w = rep.samples["wait_s"]
    assert statistics.fmean(w) == pytest.approx(mean, abs=4.0 * mean / math.sqrt(n))
    assert statistics.stdev(w) / statistics.fmean(w) == pytest.approx(1.0, abs=0.08)  # CV of Exp = 1
    assert rep.p_strand == 0.0  # waits never change energies


def test_consumption_factors_have_the_requested_distribution(one_stop):
    _, P, route = one_stop
    rt = rb._Route(P, route.decision)
    keys = rb._world_keys(0, 3000)
    out = rb._simulate(rt, keys, 0.06, 0.10, "none", False, record=True)
    ratios = []
    for energies, _, _ in out.records:
        ratios.extend(c / c0 for c, c0 in zip(energies, rt.nominal) if c0 > 0)
    target_cv = math.sqrt((1 + 0.06**2) * (1 + 0.10**2) - 1)
    assert statistics.fmean(ratios) == pytest.approx(1.0, abs=0.005)
    assert statistics.stdev(ratios) == pytest.approx(target_cv, rel=0.05)
    xi = out.trip_factor
    assert statistics.fmean(xi) == pytest.approx(1.0, abs=3 * 0.06 / math.sqrt(len(xi)))
    assert statistics.stdev(xi) == pytest.approx(0.06, rel=0.06)
    # without edge noise every positive edge is scaled by exactly the trip factor
    out = rb._simulate(rt, keys[:50], 0.06, 0.0, "none", False, record=True)
    for (energies, _, _), f in zip(out.records, out.trip_factor):
        for c, c0 in zip(energies, rt.nominal):
            assert c == (c0 * f if c0 > 0 else c0 / f)


def test_regeneration_is_divided_by_the_factor_and_bad_plans_can_be_analysed():
    # over a mountain pass: 800 m up, then 800 m down over 8 km (regenerating)
    b = NetworkBuilder()
    a = b.add_node(0, 0, 0.0)
    top = b.add_node(8000, 0, 800.0)
    z = b.add_node(16000, 0, 0.0)
    b.add_road(a, top, road_class="trunk")
    b.add_road(top, z, road_class="trunk")
    net = b.build()
    sc = Scenario("pass", net, [], get_vehicle("compact50"), Query(a, z, initial_soc=0.9))
    P = sc.prepare()
    dec = PlanDecision([net.edge_between(a, top), net.edge_between(top, z)], [0, 0])
    rt = rb._Route(P, dec)
    assert rt.nominal[0] > 0 > rt.nominal[1]
    out = rb._simulate(rt, rb._world_keys(1, 50), 0.2, 0.0, "none", False, record=True)
    for (energies, _, _), f in zip(out.records, out.trip_factor):
        assert energies == [rt.nominal[0] * f, rt.nominal[1] / f]
    # a bad day consumes more *and* recovers less
    worst = max(range(50), key=lambda r: out.trip_factor[r])
    assert out.trip_factor[worst] > 1.0 and abs(out.records[worst][0][1]) < abs(rt.nominal[1])
    rep = monte_carlo(P, dec, n=50, verify=50, **NOISY)  # a plan without any stop
    assert rep.planned_stops == 0 and rep.p_strand == 0.0


def _straight_road(km: float, initial_soc: float, **prefs):
    """Two nodes, one flat motorway edge, no stations."""
    b = NetworkBuilder()
    a = b.add_node(0, 0, 0.0)
    z = b.add_node(km * 1000.0, 0, 0.0)
    b.add_road(a, z, road_class="motorway")
    net = b.build()
    sc = Scenario("road", net, [], get_vehicle("compact50"), Query(a, z, initial_soc=initial_soc),
                  preferences=Preferences(**prefs))
    return sc, PlanDecision([net.edge_between(a, z)], [0])


def test_probabilities_match_the_closed_form_lognormal_model():
    """One edge, no stop: every event is a threshold on a single log-normal factor.

    ``xi = xi_trip * xi_edge`` is log-normal with ``sigma^2 = sigma_t^2 +
    sigma_e^2`` and ``mu = mu_t + mu_e``, so for example
    ``P(strand) = P(e_init - xi C < 0) = 1 - Phi((ln(e_init / C) - mu) / sigma)``.
    """
    sc, dec = _straight_road(150.0, 0.62, reserve_soc=0.15, min_arrival_soc=0.2)
    P = sc.prepare()
    C = P.edge_energy_nominal[0][dec.edges[0]]
    trip_cv, edge_cv = 0.15, 0.2
    mu_t, s_t = lognormal_params(trip_cv)
    mu_e, s_e = lognormal_params(edge_cv)
    law = NormalDist(mu_t + mu_e, math.sqrt(s_t**2 + s_e**2))

    def p_exceed(x: float) -> float:  # P(xi > x)
        return 1.0 - law.cdf(math.log(x))

    tol = 1e-6
    expected = {
        "p_strand": p_exceed(P.e_init / C),
        "p_reserve_breach": p_exceed((P.e_init - P.e_min + tol) / C),
        "p_arrival_short": p_exceed((P.e_init - P.e_dest_min + tol) / C),
    }
    assert 0.02 < expected["p_strand"] < expected["p_reserve_breach"] < expected["p_arrival_short"] < 0.9
    n = 20000
    rep = monte_carlo(P, dec, n=n, seed=13, trip_cv=trip_cv, edge_cv=edge_cv)
    for k, p in expected.items():
        se = math.sqrt(p * (1 - p) / n)
        assert getattr(rep, k) == pytest.approx(p, abs=4.5 * se), k
    # a stop-free trip on a flat road can only lose energy
    assert rep.arrival_soc.max <= P.e_init / P.capacity


def test_shared_edges_get_the_same_factor_in_every_plan(one_stop):
    """Common random numbers across *different* routes of one network."""
    _, P, route = one_stop
    net = P.net
    rt1 = rb._Route(P, route.decision)
    # a second route through a node off the planned path
    w = min_level_weights(P, "time")
    via = next(v for v in route.nodes[::-1] for e in net.out_edges[v] if net.head[e] not in route.nodes)
    off = next(net.head[e] for e in net.out_edges[via] if net.head[e] not in route.nodes)
    _, p1 = dijkstra(net, P.origin, w)
    _, p2 = dijkstra(net, off, w)
    edges = path_from_pred(net, p1, P.origin, off) + path_from_pred(net, p2, off, P.destination)
    rt2 = rb._Route(P, PlanDecision(edges, [0] * len(edges)))
    shared = set(route.edges) & set(edges)
    assert shared and set(route.edges) != set(edges)
    keys = rb._world_keys(21, 30)
    r1 = rb._simulate(rt1, keys, 0.1, 0.2, "exponential", False, record=True)
    r2 = rb._simulate(rt2, keys, 0.1, 0.2, "exponential", False, record=True)
    assert r1.trip_factor == r2.trip_factor
    for (c1, _, _), (c2, _, _) in zip(r1.records, r2.records):
        f1 = dict(zip(route.edges, c1))
        f2 = dict(zip(edges, c2))
        for a in shared:
            assert f1[a] == f2[a]
        assert len({f1[a] / rt1.nominal[route.edges.index(a)] for a in route.edges}) > 1  # edges differ


def test_repeated_visits_of_a_station_draw_independent_queues(one_stop):
    _, P, route = one_stop
    net = P.net
    stop = route.decision.charges[0]
    pos = stop.position
    edges, levels = list(route.decision.edges), list(route.decision.levels)
    v = route.nodes[pos]
    out_e = net.out_edges[v][0]
    back = net.twin[out_e]
    assert back >= 0
    detour = edges[:pos] + [out_e, back] + edges[pos:]
    lv = levels[:pos] + [0, 0] + levels[pos:]
    charges = [
        ChargeDecision(pos, stop.station, stop.target_kwh),
        ChargeDecision(pos + 2, stop.station, P.e_charge_cap),
    ]
    dec = PlanDecision(detour, lv, charges)
    rt = rb._Route(P, dec)
    offsets = [x[4] for stops in rt.stops_at for x in stops]
    assert len(offsets) == 2 and offsets[0] != offsets[1]
    rep = monte_carlo(P, dec, n=50, verify=50, keep_samples=True, **NOISY)
    assert rep.planned_stops == 2


# ------------------------------------------------------------- policies
def test_adaptive_policy_dominates_same_target_pathwise(one_stop, two_stops, low_reserve):
    totals = {"same-target": 0.0, "adaptive": 0.0}
    strictly_better = False
    for _, P, route in (one_stop, two_stops, low_reserve):
        reps = {
            pol: monte_carlo(P, route, n=400, seed=4, policy=pol, keep_samples=True, **NOISY)
            for pol in ("same-target", "adaptive")
        }
        a, s = reps["adaptive"].samples, reps["same-target"].samples
        # common random numbers + monotone dynamics: never less energy, run by run
        assert all(x >= y for x, y in zip(a["min_soc"], s["min_soc"]))
        assert all(x >= y for x, y in zip(a["arrival_soc"], s["arrival_soc"]))
        for k in ("p_strand", "p_reserve_breach", "p_arrival_short"):
            assert getattr(reps["adaptive"], k) <= getattr(reps["same-target"], k)
        for pol, rep in reps.items():
            totals[pol] += rep.p_reserve_breach
        strictly_better |= reps["adaptive"].p_reserve_breach < reps["same-target"].p_reserve_breach
    assert totals["adaptive"] <= totals["same-target"]
    assert strictly_better


def test_adaptive_targets_respect_the_charging_cap(two_stops):
    _, P, route = two_stops
    for r in range(30):
        p = replay_run(P, route, r, seed=2, policy="adaptive", trip_cv=0.4, edge_cv=0.4)
        planned = [c.target_kwh for c in route.decision.charges]
        for st, y in zip(p.stops, planned):
            assert st.energy_out_kwh <= P.e_charge_cap + 1e-12
            assert st.energy_out_kwh >= min(y, P.e_charge_cap) or st.energy_out_kwh == st.energy_in_kwh


@pytest.mark.parametrize("policy", ["same-target", "adaptive"])
def test_energy_outcomes_do_not_depend_on_the_wait_model(two_stops, policy):
    _, P, route = two_stops
    res = [
        monte_carlo(P, route, n=150, seed=6, wait_model=wm, policy=policy, keep_samples=True, **NOISY)
        for wm in ("exponential", "expected", "none")
    ]
    for rep in res[1:]:
        assert rep.samples["min_soc"] == res[0].samples["min_soc"]
        assert rep.samples["arrival_soc"] == res[0].samples["arrival_soc"]
        assert rep.p_strand == res[0].p_strand
    means = [rep.total_time_s.mean for rep in res]
    assert means[2] < means[1] and means[2] < means[0]  # queues cost time


# ------------------------------------------------------------------ sweeps
def test_safety_sweep_trades_time_for_reliability():
    factors = (1.0, 1.05, 1.1, 1.15, 1.2, 1.3)
    for seed in (0, 1, 2, 3):
        sc = grid_scenario(rows=8, cols=8, seed=seed, initial_soc=0.3, reserve_soc=0.03, min_arrival_soc=0.03)
        rows = safety_sweep(sc, factors=factors, n=1000, seed=0, trip_cv=0.1, edge_cv=0.2)
        assert [r["safety_factor"] for r in rows] == list(factors)
        assert all(r["feasible"] for r in rows)
        for prev, cur in pairwise(rows):
            # exact planner: a larger factor only shrinks the feasible set
            assert cur["planned_time_s"] >= prev["planned_time_s"] - 1e-6
            # common random numbers: the risk curve is monotone up to MC noise
            assert cur["p_strand"] <= prev["p_strand"] + 0.01
            assert cur["p_reserve_breach"] <= prev["p_reserve_breach"] + 0.01
        assert rows[-1]["p_strand"] < rows[0]["p_strand"]
        assert rows[-1]["p_reserve_breach"] < rows[0]["p_reserve_breach"]
        for r in rows:
            assert r["p_strand_lo"] <= r["p_strand"] <= r["p_strand_hi"]
            assert r["p_strand"] <= r["p_reserve_breach"]
            assert all(v is None or isinstance(v, (int, float, str, bool)) for v in r.values())


def test_reserve_sweep_rows_and_thresholds():
    sc = grid_scenario(rows=8, cols=8, seed=0, initial_soc=0.3, reserve_soc=0.1, min_arrival_soc=0.05)
    rows = reserve_sweep(sc, reserves=(0.03, 0.1, 0.2), n=300, trip_cv=0.1, edge_cv=0.2)
    assert [r["reserve_soc"] for r in rows] == [0.03, 0.1, 0.2]
    base = rows[1]
    assert base["p_own_reserve_breach"] == base["p_reserve_breach"]
    assert base["p_own_arrival_short"] == base["p_arrival_short"]
    for prev, cur in pairwise(rows):
        assert cur["planned_time_s"] >= prev["planned_time_s"] - 1e-6
        assert cur["p_strand"] <= prev["p_strand"] + 0.02
        assert cur["p_reserve_breach"] <= prev["p_reserve_breach"] + 0.02
    # a plan made with a thinner reserve breaches the reference reserve more often
    assert rows[0]["p_reserve_breach"] > rows[0]["p_own_reserve_breach"]


def test_sweep_rows_for_plans_without_route():
    sc = grid_scenario(rows=6, cols=6, seed=1, initial_soc=0.05, reserve_soc=0.1)
    rows = safety_sweep(sc, factors=(1.0,), n=10)
    assert rows[0]["feasible"] is False
    assert rows[0]["p_strand"] is None and rows[0]["planned_time_s"] is None


# ----------------------------------------------------------- edge cases
def test_origin_charging_below_reserve_is_not_a_breach():
    sc = grid_scenario(rows=6, cols=6, seed=4, initial_soc=0.05)
    origin = sc.query.origin
    stations = list(sc.stations) + [
        Station(id=len(sc.stations), node=origin, power_kw=100.0, kind="DC", plugs=4, name="origin")
    ]
    sc = sc.with_(stations=stations)
    P = sc.prepare()
    route = plan(sc, "bls-astar")
    assert route.feasible and route.decision.charges[0].position == 0
    assert P.e_init < P.e_min
    rep = monte_carlo(P, route, n=20, trip_cv=0.0, edge_cv=0.0, wait_model="expected", keep_samples=True)
    assert rep.p_reserve_breach == 0.0
    assert rep.planned_min_soc == pytest.approx(0.05)  # the evaluator's min includes the start
    assert rep.min_soc.min >= P.e_min / P.capacity - 1e-9  # driving minimum does not
    monte_carlo(P, route, n=40, verify=40, **NOISY)


def test_trivial_route_origin_equals_destination():
    sc = grid_scenario(rows=4, cols=4, seed=1, initial_soc=0.5)
    sc = sc.with_query(destination=sc.query.origin)
    route = plan(sc, "bls-astar")
    rep = monte_carlo(sc, route, n=25, verify=25, keep_samples=True)
    assert rep.p_strand == rep.p_reserve_breach == rep.p_arrival_short == 0.0
    assert rep.n_completed == 25 and rep.total_time_s.max == 0.0
    assert set(rep.samples["arrival_soc"]) == {0.5}


def test_plan_without_decision_is_rebuilt_from_its_stops(one_stop):
    _, P, route = one_stop
    bare = evaluate(P, route.decision)
    bare.decision = None
    a = monte_carlo(P, bare, n=50, seed=1, keep_samples=True).samples
    b = monte_carlo(P, route, n=50, seed=1, keep_samples=True).samples
    assert a == b


def test_invalid_arguments(one_stop):
    sc, P, route = one_stop
    with pytest.raises(ValueError):
        monte_carlo(P, route, policy="panic")
    with pytest.raises(ValueError):
        monte_carlo(P, route, wait_model="poisson")
    with pytest.raises(ValueError):
        monte_carlo(P, route, n=0)
    with pytest.raises(ValueError):
        monte_carlo(P, route, trip_cv=-0.1)
    with pytest.raises(ValueError):
        replay_run(P, route, -1)
    with pytest.raises(ValueError):
        safety_sweep(sc, factors=(1.0,), edge_cv=math.nan)
    with pytest.raises(NotARouteError):
        monte_carlo(P, infeasible_plan(P, "x", "nothing found"))
    broken = PlanDecision(route.edges[1:], route.levels[1:])
    with pytest.raises(NotARouteError):
        monte_carlo(P, broken)
    wrong_station = PlanDecision(route.edges, route.levels, [ChargeDecision(0, 0, P.e_charge_cap)])
    with pytest.raises(NotARouteError):
        monte_carlo(P, wrong_station)


def test_thresholds_can_be_overridden(one_stop):
    _, P, route = one_stop
    base = monte_carlo(P, route, n=300, seed=3, keep_samples=True, **NOISY)
    lax = monte_carlo(P, route, n=300, seed=3, reserve_kwh=0.0, arrival_kwh=0.0, keep_samples=True, **NOISY)
    assert lax.samples["min_soc"] == base.samples["min_soc"]
    assert lax.p_strand == base.p_strand
    assert lax.p_reserve_breach == lax.p_strand <= base.p_reserve_breach
    assert lax.p_arrival_short <= base.p_arrival_short
    assert lax.reserve_soc == 0.0 and base.reserve_soc == pytest.approx(P.e_min / P.capacity)


def test_report_serialisation(two_stops):
    _, P, route = two_stops
    rep = monte_carlo(P, route, n=80, seed=0, keep_samples=True, **NOISY)
    d = json.loads(json.dumps(rep.to_dict()))
    for k in ("p_strand", "p_reserve_breach", "p_arrival_short", "ci95", "arrival_soc", "total_time_s",
              "min_soc", "planned", "thresholds", "samples", "combined_cv"):
        assert k in d
    assert len(d["samples"]["total_time_s"]) == 80
    assert d["ci95"]["p_strand"][0] <= d["p_strand"] <= d["ci95"]["p_strand"][1]
    assert d["total_time_s"]["count"] == rep.n_completed
    assert rep.p_success == pytest.approx(1.0 - rep.p_arrival_short)
    assert "P(strand)" in str(rep)
    assert "samples" not in monte_carlo(P, route, n=5).to_dict()
