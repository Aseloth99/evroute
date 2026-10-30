"""Tests of the minimum stop SoC -- the *floor rule* -- in every planner.

``Preferences.min_stop_soc`` (``P.e_stop_min`` kWh): every charging stop ends at
the floor or above unless the vehicle arrives there with at least that much
(no "+2 %" top-ups).  Correctness is checked in layers:

* the model: the target sets, the evaluator's verdict on hand-made decisions,
  serialisation and the CLI option;
* ``min_stop_soc = 0`` -- and a floor that can never bind -- changes nothing;
* every planner's plans obey the rule (the evaluator and an independent check of
  the stops), for floors 0.2 / 0.4 / 0.6, on presets and random instances;
* BLS, BLS-A* and the Pareto front equal an exhaustive enumeration with the rule
  on tiny digraphs (the brute force of ``tests/test_crossval.py``), also with
  time-dependent tariffs and holds (the one of ``tests/test_crossval_tariffs.py``,
  holds on a 5-minute grid included); the whole cross-validation battery
  (I1-I11) runs on random instances with a floor;
* CFP with the floor: Lemma 1F (the candidate switch times stay complete) against
  a dense scan, the planner against an exact dynamic programme over stop sequences
  and breakpoint energies extended by the floor, against a dense scan of the
  departure energies on line networks, ``CFP(floor) <= BLS(floor)`` on every grid,
  ``CFP(floor) >= CFP(0)`` and the convergence of fine-grid BLS from above;
* monotonicity in the floor (a higher floor never helps) for BLS, CFP and the
  Pareto front, whose extremes equal BLS for both objectives;
* Monte Carlo replays keep the rule.

``@pytest.mark.slow`` runs the randomised checks on many more seeds.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from itertools import pairwise

import pytest
import test_cfp as tc
import test_crossval as cv
import test_crossval_tariffs as cvt

from evroute.algorithms import get_planner
from evroute.algorithms.cfp import evaluate_function, label_function, switch_candidates
from evroute.algorithms.labels import charging_targets
from evroute.cli import main
from evroute.evaluate import TOL_KWH, check_objective, evaluate
from evroute.generators import get_scenario, grid_scenario
from evroute.plan import ChargeDecision, PlanDecision, RoutePlan
from evroute.robustness import monte_carlo, replay_run
from evroute.scenario import PreparedScenario, Scenario

INF = math.inf
FLOORS = (0.2, 0.4, 0.6)
ALL = ("naive", "greedy", "station-graph", "genetic", "bls", "bls-astar", "cfp", "cfp-astar", "pareto")
EXACT = ("bls", "bls-astar", "cfp", "cfp-astar", "pareto")


def _run(P: PreparedScenario, name: str, **opts) -> RoutePlan:
    return get_planner(name, **opts).plan(P)


def _floor_breaches(P: PreparedScenario, plan: RoutePlan) -> list[str]:
    """Stops that start *and* end below the floor (independent of the evaluator's own test),
    plus every floor violation the evaluator reported."""
    out = [v for v in plan.violations if "minimum stop" in v]
    if P.e_stop_min > 0.0:
        lim = P.e_stop_min - TOL_KWH
        out += [f"stop {i}: {s.energy_in_kwh!r} -> {s.energy_out_kwh!r} kWh, floor {P.e_stop_min!r}"
                for i, s in enumerate(plan.stops) if s.energy_in_kwh < lim and s.energy_out_kwh < lim]
    return out


def _binding(P: PreparedScenario, plan: RoutePlan) -> bool:
    """Does some stop of the plan start below the floor (so that the rule shaped it)?"""
    return any(s.energy_in_kwh < P.e_stop_min - 1e-6 for s in plan.stops)


def _with_floor(inst: cv.Instance, floor: float) -> cv.Instance:
    prefs = dict(inst.prefs)
    prefs["min_stop_soc"] = floor
    return replace(inst, prefs=tuple(sorted(prefs.items())))


def _floor_for(inst: cv.Instance, rng: random.Random) -> float:
    """A floor between the reserve and the charging cap (so that "a higher cap is no worse"
    stays a valid metamorphic relation: the floor is capped at the charging cap)."""
    prefs = dict(inst.prefs)
    lo = prefs.get("reserve_soc", 0.1)
    hi = prefs.get("max_charge_soc", 1.0)
    return round(min(hi, lo + rng.choice((0.05, 0.1, 0.2, 0.3, 0.4, rng.uniform(0.0, 0.6)))), 3)


def _line(floor: float, soc: float = 0.3, **prefs) -> Scenario:
    """O --60 km--> S (DC 150 kW) --160 km--> D on a flat trunk road."""
    return tc._line_scenario([60e3, 160e3], [(1, 150.0, "DC", 120.0)], soc, min_stop_soc=floor, **prefs)


# =============================================================================
# 1. the model
# =============================================================================
def test_floor_energy_is_capped_and_is_a_target():
    sc = grid_scenario(5, 5, seed=3, n_stations=4)
    P0 = sc.prepare()
    assert P0.e_stop_min == 0.0
    base = {(s, m): charging_targets(P0, s, m) for s in range(len(P0.stations)) for m in ("grid", "grid+breaks")}
    # a floor off the grid becomes a target, in both modes and at every station
    P = sc.with_preferences(min_stop_soc=0.33).prepare()
    assert P.e_stop_min == pytest.approx(0.33 * P.capacity)
    for s in range(len(P.stations)):
        for mode in ("grid", "grid+breaks"):
            tg = charging_targets(P, s, mode)
            assert P.e_stop_min in tg
            assert sorted(set(base[s, mode]) | {P.e_stop_min}) == tg
    # a floor on the grid leaves the target set unchanged
    P = sc.with_preferences(min_stop_soc=0.4).prepare()
    assert all(charging_targets(P, s, m) == base[s, m] for s, m in base)
    # capped at the charging cap
    P = sc.with_preferences(min_stop_soc=0.95, max_charge_soc=0.8).prepare()
    assert P.e_stop_min == pytest.approx(P.e_charge_cap)
    assert all(charging_targets(P, s)[-1] == P.e_charge_cap for s in range(len(P.stations)))
    with pytest.raises(ValueError, match="min_stop_soc"):
        sc.with_preferences(min_stop_soc=1.2).prepare()
    with pytest.raises(ValueError, match="min_stop_soc"):
        sc.with_preferences(min_stop_soc=-0.1).prepare()


def test_evaluator_reports_stops_below_the_floor():
    P = _line(0.4).prepare()
    net, q = P.net, P.capacity
    edges = [net.edge_between(0, 1), net.edge_between(1, 2)]
    arrive = P.e_init - P.edge_energy[0][edges[0]]
    assert arrive < 0.4 * q

    def verdict(*targets_soc: float, floor: float = 0.4) -> list[str]:
        Pf = P if floor == 0.4 else _line(floor).prepare()
        d = PlanDecision(edges, [0, 0], [ChargeDecision(1, 0, y * q) for y in targets_soc])
        return [v for v in evaluate(Pf, d).violations if "minimum stop" in v]

    assert verdict(0.9) == []  # a real stop
    assert verdict(0.4) == []  # exactly to the floor
    assert verdict(0.4 - 0.5e-6 / q) == []  # within the tolerance
    assert len(verdict(0.3)) == 1  # a top-up below the floor
    assert len(verdict(0.1)) == 1  # a stop that adds nothing while below the floor
    assert verdict(0.3, floor=0.0) == []  # the rule is off
    assert verdict(0.3, floor=0.2) == []  # the vehicle arrives above this floor: any target is fine
    # consecutive stops at one node: only the first one starts below the floor
    P2 = P.scenario.with_(stations=P.scenario.stations + [replace(P.scenario.stations[0], id=1, kind="AC",
                                                                    power_kw=22.0)]).prepare()
    d = PlanDecision(edges, [0, 0], [ChargeDecision(1, 0, 0.4 * q), ChargeDecision(1, 1, 0.42 * q)])
    assert not [v for v in evaluate(P2, d).violations if "minimum stop" in v]
    d = PlanDecision(edges, [0, 0], [ChargeDecision(1, 0, 0.3 * q), ChargeDecision(1, 1, 0.9 * q)])
    assert len([v for v in evaluate(P2, d).violations if "minimum stop" in v]) == 1


def test_scenario_json_round_trip_keeps_the_floor():
    sc = get_scenario("grid-small").with_preferences(min_stop_soc=0.45)
    d = sc.to_dict()
    assert d["preferences"]["min_stop_soc"] == 0.45
    back = Scenario.from_dict(d)
    assert back.preferences.min_stop_soc == 0.45
    a, b = _run(sc.prepare(), "bls-astar"), _run(back.prepare(), "bls-astar")
    assert a.objective_value == b.objective_value and a.edges == b.edges
    assert "min_stop_soc" not in get_scenario("grid-small").to_dict()["preferences"]


def test_cli_option_and_summary(capsys):
    assert main(["plan", "corridor", "-a", "bls-astar", "--min-stop-soc", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "minimum stop SoC 50%" in out
    assert main(["plan", "corridor", "-a", "bls-astar"]) == 0
    assert "minimum stop SoC" not in capsys.readouterr().out
    assert main(["plan", "corridor", "--min-stop-soc", "1.5"]) == 2
    assert "min_stop_soc" in capsys.readouterr().err


# =============================================================================
# 2. a floor of zero, or one that cannot bind, changes nothing
# =============================================================================
@pytest.mark.parametrize("preset", ["corridor", "grid-small", "random-geometric"])
def test_a_floor_that_cannot_bind_changes_no_plan(preset):
    """With the initial SoC above the reserve every stop starts at the reserve or above, so a
    floor at the reserve never binds: every planner returns the plan it returns without one
    (``min_stop_soc = 0`` is the code path of the earlier versions: the target sets and every
    comparison are the same, which the rest of the test-suite pins down)."""
    sc = get_scenario(preset)
    assert sc.query.initial_soc > sc.preferences.reserve_soc
    for obj in ("time", "cost"):
        P0 = sc.with_preferences(objective=obj).prepare()
        P1 = sc.with_preferences(objective=obj, min_stop_soc=sc.preferences.reserve_soc).prepare()
        assert P1.e_stop_min == P1.e_min > 0.0
        for name in ALL:
            opts = {"max_labels": 300_000} if name == "pareto" else {}
            a, b = _run(P0, name, **opts), _run(P1, name, **opts)
            assert a.feasible == b.feasible, (preset, obj, name)
            if not a.feasible:
                continue
            assert a.objective_value == pytest.approx(b.objective_value, rel=1e-12, abs=1e-9), (preset, obj, name)
            if not name.startswith("cfp"):  # (CFP may fold a stop that starts 1e-9 kWh below the reserve)
                assert a.edges == b.edges and a.decision.charges == b.decision.charges, (preset, obj, name)
            assert len(a.alternatives) == len(b.alternatives)


# =============================================================================
# 3. every planner obeys the rule
# =============================================================================
@pytest.mark.parametrize("floor", FLOORS)
@pytest.mark.parametrize("preset", ["corridor", "grid-small", "random-geometric", "turkey-karaman-istanbul"])
def test_every_planner_obeys_the_rule_on_presets(preset, floor):
    sc = get_scenario(preset)
    big = sc.network.num_nodes > 500
    binding = 0
    for obj in ("time", "cost"):
        P = sc.with_preferences(objective=obj, min_stop_soc=floor).prepare()
        P0 = sc.with_preferences(objective=obj).prepare()
        plans = {}
        for name in ALL:
            if big and name in ("bls", "cfp", "pareto"):
                continue
            opts = {"max_labels": 300_000} if name == "pareto" else {}
            plans[name] = p = _run(P, name, **opts)
            assert not _floor_breaches(P, p), (preset, floor, obj, name, _floor_breaches(P, p))
            if p.feasible:
                assert check_objective(p), (preset, floor, obj, name)
                for alt in p.alternatives:
                    assert alt.feasible and not _floor_breaches(P, alt)
            binding += _binding(P, p)
        for name in ("bls-astar", "cfp-astar"):
            assert plans[name].feasible, (preset, floor, obj, name, plans[name].message)
            p0 = _run(P0, name)
            # a floor never helps (the routes it allows are routes without it; CFP minimises the time)
            v, v0 = ((plans[name].total_time_s, p0.total_time_s) if name == "cfp-astar"
                     else (plans[name].internal_objective, p0.internal_objective))
            assert v >= v0 - 1e-6 - 1e-9 * abs(v0), (preset, floor, obj, name)
        # continuous charging is a lower bound on the time of every plan
        t_cfp = plans["cfp-astar"].total_time_s
        for name, p in plans.items():
            if p.feasible:
                assert p.total_time_s >= t_cfp - 1e-6, (preset, floor, obj, name)
        if "bls" in plans:
            assert plans["bls"].internal_objective == pytest.approx(plans["bls-astar"].internal_objective, rel=1e-9)
            assert plans["cfp"].total_time_s == pytest.approx(t_cfp, rel=1e-9, abs=1e-6)
        if "pareto" in plans and plans["pareto"].feasible:
            crit = [(m.total_time_s, m.generalized_cost) for m in plans["pareto"].alternatives]
            want = plans["bls-astar"].internal_objective
            best = min(c[0] if obj == "time" else c[1] for c in crit)
            assert best == pytest.approx(want, rel=1e-7), (preset, floor, obj)
    if floor >= 0.4:
        assert binding  # the rule shaped some plans


def _assert_clean(tally) -> None:
    assert tally.n_failed == 0, "\n" + tally.report()


def _random_battery(blocks: range, per_block: int, profile: str, budget: cv.Budget) -> None:
    tally = cv.Tally()
    for block in blocks:
        for seed in range(block * per_block, (block + 1) * per_block):
            base = cv.sample_instance(90_000 + seed, profile)
            inst = _with_floor(base, _floor_for(base, random.Random(f"evroute-floor-{profile}-{seed}")))
            cv.check_instance(inst, budget, tally)
            # and the rule itself, for every planner
            P = cv.build_scenario(inst).prepare()
            for name in ("bls-astar", "cfp-astar", "greedy", "station-graph", "genetic"):
                p = _run(P, name, **({"max_labels": budget.bls_labels} if name == "bls-astar" else {}))
                tally.check("F1", not _floor_breaches(P, p), lambda: f"{name}: {_floor_breaches(P, p)} "
                                                                     f"{inst.describe()}")
    _assert_clean(tally)
    assert tally.checked["I1"] > 20 * per_block * len(blocks)


@pytest.mark.parametrize("block", range(3))
def test_cross_validation_battery_with_a_floor_fast(block):
    """I1-I11 of the cross-validation suite (planners against each other, the evaluator,
    metamorphic relations, local search, robustness, serialisation) with a random floor."""
    _random_battery(range(block, block + 1), 10, "fast", cv.FAST)


@pytest.mark.slow
@pytest.mark.parametrize("block", range(10))
def test_cross_validation_battery_with_a_floor_slow(block):
    _random_battery(range(block, block + 1), 30, "slow", cv.SLOW)


# =============================================================================
# 4. exhaustive enumeration (BLS, BLS-A*, Pareto; CFP no slower)
# =============================================================================
def _brute_force_battery(seeds: range) -> None:
    tally = cv.Tally()
    binding = 0
    for seed in seeds:
        base = cv.sample_tiny(seed)
        rng = random.Random(f"evroute-floor-tiny-{seed}")
        prefs = dict(base.prefs)
        # a floor near or above the initial SoC, so that it binds at the first stop
        floor = round(min(prefs["max_charge_soc"], rng.choice((base.initial_soc + 0.05, prefs["reserve_soc"] + 0.1,
                                                                 0.3, 0.5, rng.uniform(0.1, 0.8)))), 3)
        inst = _with_floor(base, floor)
        cv.check_brute_force(inst, tally)
        P = cv.build_scenario(inst).prepare()
        p = _run(P, "bls")
        tally.check("F1", not _floor_breaches(P, p), lambda: f"bls {_floor_breaches(P, p)} {inst.describe()}")
        binding += p.feasible and _binding(P, p)
    _assert_clean(tally)
    assert tally.checked["I9"] >= len(seeds)
    assert binding >= len(seeds) // 6


@pytest.mark.parametrize("block", range(3))
def test_brute_force_with_a_floor_fast(block):
    _brute_force_battery(range(block * 16, (block + 1) * 16))


@pytest.mark.slow
@pytest.mark.parametrize("block", range(10))
def test_brute_force_with_a_floor_slow(block):
    _brute_force_battery(range(1000 + block * 60, 1000 + (block + 1) * 60))


def _tariff_battery(seeds: range, *, tiniest: bool) -> None:
    """BLS and the Pareto front against the enumeration with holds until price changes
    (``tiniest``: on the O-A-D instances where holding pays, also holding until every
    5-minute step of the next 24 h) -- with a floor."""
    tally = cvt.Tally()
    holds = 0
    for seed in seeds:
        inst, sc = cvt.tiniest_instance(seed) if tiniest else cvt.tiny_tariff_instance(seed)
        rng = random.Random(f"evroute-floor-tariff-{seed}")
        floor = round(min(sc.preferences.max_charge_soc, rng.choice((0.3, 0.4, 0.5, 0.6, rng.uniform(0.15, 0.7)))), 3)
        sc = sc.with_preferences(min_stop_soc=floor)
        inst = _with_floor(inst, floor)
        cvt.check_brute_force(inst, sc, tally, grid_s=300.0 if tiniest else None)
        P = sc.prepare()
        for name in ("bls-astar", "pareto"):
            p = _run(P, name)
            tally.check("F1", not _floor_breaches(P, p), lambda: f"{name} {_floor_breaches(P, p)} {inst.describe()}")
            holds += cvt._holds(p)
    _assert_clean(tally)
    assert tally.checked["T8"] >= len(seeds)
    if tiniest:
        assert holds >= 2


def test_tariff_brute_force_with_a_floor_fast():
    """The cost objective with time-dependent tariffs and voluntary holds, with a floor."""
    _tariff_battery(range(0, 8), tiniest=False)
    _tariff_battery(range(2, 8), tiniest=True)


@pytest.mark.slow
@pytest.mark.parametrize("block", range(4))
def test_tariff_brute_force_with_a_floor_slow(block):
    _tariff_battery(range(100 + 20 * block, 120 + 20 * block), tiniest=False)
    _tariff_battery(range(10 + 12 * block, 22 + 12 * block), tiniest=True)


def test_demo_tariff_with_holds_and_a_floor():
    """The tariff preset under the cost objective with a floor: BLS = BLS-A* = the cheapest
    Pareto member, every plan obeys the rule, and a higher floor never makes it cheaper."""
    sc = get_scenario("turkey-karaman-istanbul-tariff").with_preferences(objective="cost", charge_step_soc=0.05)
    last = -INF
    for floor in (0.0, 0.3, 0.5, 0.7):
        P = sc.with_preferences(min_stop_soc=floor).prepare()
        assert P.tariff_time_dependent
        a = _run(P, "bls-astar")
        b = _run(P, "bls")
        assert a.feasible and not _floor_breaches(P, a)
        assert a.internal_objective == pytest.approx(b.internal_objective, rel=1e-9)
        assert a.internal_objective >= last - 1e-9
        last = a.internal_objective
        if floor >= 0.5:
            assert _binding(P, a)


# =============================================================================
# 5. CFP with the floor
# =============================================================================
def _lemma_floor_trial(rng: random.Random, concave: bool, time_dep: bool) -> tuple[int, int]:
    """One random instance of Lemma 1F: ``(leave times tau for which the candidates miss the
    best energy of a switch that obeys the floor, cases where they miss the earliest time
    at which the vehicle can leave with the floor)``."""
    f2 = tc._rand_cf(rng, concave)
    cap = 0.95 * f2.capacity
    ts, bs = tc._rand_B(rng, cap)
    phi = min(rng.uniform(bs[0] - 5.0, bs[-1] + 15.0), cap)
    su = rng.uniform(0.0, 300.0)
    knots = sorted(rng.uniform(ts[0] - 200.0, ts[-1] + 200.0) for _ in range(3))
    wv = [rng.uniform(0.0, 400.0) for _ in knots]
    for i in range(1, 3):  # FIFO
        wv[i] = max(wv[i], wv[i - 1] - 0.9 * (knots[i] - knots[i - 1]))

    def wait(t):
        return evaluate_function(knots, wv, t) if time_dep else su

    def post(t, tau):
        """Energy when leaving at ``tau`` after switching at ``t``; ``-1`` if the stop breaks the floor."""
        b = evaluate_function(ts, bs, t)
        if b >= cap:
            return b
        y = min(f2.energy_at(f2.time_at(b) + tau - t - wait(t)), cap)
        return y if (b >= phi or y >= phi - 1e-12) else -1.0

    def reach(t):
        """Earliest leave time with the floor after switching at ``t`` (``t`` itself if ``B(t) >= phi``)."""
        b = evaluate_function(ts, bs, t)
        return t + wait(t) + max(0.0, f2.time_at(phi) - f2.time_at(b))

    ups = [f2.e_breaks[i] for i in range(1, len(f2.power_kw)) if f2.power_kw[i] > f2.power_kw[i - 1]]
    kt = [k for k in knots if ts[0] < k < ts[-1]] if time_dep else []
    cand, _ = switch_candidates(ts, bs, ups, kt)
    hi_t = ts[-1] + 500.0
    dense_t = [ts[0] + (hi_t - ts[0]) * i / 600 for i in range(601)]
    bad = 0
    for tau in (rng.uniform(ts[0], ts[-1] + 2000.0) for _ in range(6)):
        dense = max((post(t, tau) for t in dense_t if t <= tau and tau >= t + wait(t)), default=-1.0)
        best = max((post(t, tau) for t in cand if tau >= t + wait(t)), default=-1.0)
        best = max(best, evaluate_function(ts, bs, tau))  # not stopping at s2 (always allowed)
        bad += dense > best + 1e-6
    # the earliest departure with the floor: a candidate switch, or not stopping (B reaches phi)
    cross = next((ts[j] + (phi - bs[j]) / (bs[j + 1] - bs[j]) * (ts[j + 1] - ts[j])
                  for j in range(len(ts) - 1) if bs[j] < phi <= bs[j + 1]), ts[0] if bs[0] >= phi else INF)
    early = min(reach(t) for t in dense_t)
    bad_early = min(min(reach(t) for t in cand), cross) > early + 1e-6
    return bad, bad_early


@pytest.mark.parametrize("concave, time_dep", [(True, False), (False, False), (True, True), (False, True)])
def test_lemma_1f_candidates_stay_complete_with_a_floor(concave, time_dep):
    rng = random.Random(4100 + 2 * concave + time_dep)
    bad = bad_early = 0
    for _ in range(250):
        b, e = _lemma_floor_trial(rng, concave, time_dep)
        bad += b
        bad_early += e
    assert bad == 0 and bad_early == 0


def test_folded_label_is_the_same_function():
    """A stop label folded to start at the floor describes the same ``(time, energy)`` pairs
    from the floor on, and none below it."""
    rng = random.Random(77)
    for _ in range(200):
        f = tc._rand_cf(rng, rng.random() < 0.5)
        cap = 0.95 * f.capacity
        b = rng.uniform(0.0, 0.5 * cap)
        phi = rng.uniform(b, cap)
        t0 = rng.uniform(0.0, 5000.0)
        t0f = t0 + (f.time_at(phi) - f.time_at(b))
        ts, bs = label_function(f, cap, t0, f.time_at(b), b, 0.0, 0.0, f.capacity)
        tsf, bsf = label_function(f, cap, t0f, f.time_at(phi), phi, 0.0, 0.0, f.capacity)
        assert tsf[0] == pytest.approx(t0f) and bsf[0] == pytest.approx(phi)
        for t in (t0f + (ts[-1] + 100.0 - t0f) * i / 50 for i in range(51)):
            assert evaluate_function(tsf, bsf, t) == pytest.approx(evaluate_function(ts, bs, t), abs=1e-9)


def _stack_optimum_floor(P: PreparedScenario, at_origin: bool) -> float:
    """Exact optimum of a :func:`test_cfp._stack_line` instance with a floor.

    The dynamic programme of :func:`test_cfp._stack_optimum` (shortest path over
    (breakpoint energy, station charging now)) with the floor rule: when the vehicle reaches
    the chargers below the floor, the first stop ends at the floor or above -- no switch to
    another charger below it -- and the trip leaves with ``max(need, floor)``.  The constraint
    ``y_1 >= floor`` is one more interval bound of the chain ``b0 <= y_1 <= ... <= y_k``, so with
    the floor among the energies a vertex at the energies of ``U`` is still optimal."""
    net, emin, inf = P.net, P.e_min, math.inf

    def edge(u, w):
        return next(e for e in range(net.num_edges) if net.tail[e] == u and net.head[e] == w)

    o, d = P.origin, P.destination
    if at_origin:
        e2 = edge(o, d)
        b0, drive = P.e_init, P.edge_time[0][e2]
    else:
        s = P.stations[0].node
        e1, e2 = edge(o, s), edge(s, d)
        b0 = min(P.e_init - P.edge_energy[0][e1], P.capacity)
        if P.e_init < emin - 1e-9 or b0 < emin - 1e-9:
            return inf
        drive = P.edge_time[0][e1] + P.edge_time[0][e2]
    need = max(P.e_dest_min + P.edge_energy[0][e2], emin)
    if b0 >= need:
        return drive  # no stop is needed: the floor does not force one
    phi = P.e_stop_min
    forced = b0 < phi
    goal = max(need, phi) if forced else need
    if goal > P.e_charge_cap:
        return inf
    U = sorted({b0, goal} | {e for f in P.cf for e in f.e_breaks if b0 < e < goal}
               | ({phi} if forced and b0 < phi < goal else set()))
    ns = len(P.stations)
    over = [P.wait_s(s, 0.0) + P.setup_s[s] for s in range(ns)]
    F = [f.time_at for f in P.cf]
    best = [inf] * ns
    for i, y in enumerate(U):
        if i:
            best = [best[s] + F[s](y) - F[s](U[i - 1]) for s in range(ns)]
        switch_ok = i == 0 or not forced or y >= phi  # a stop that started below the floor ends at it
        before = [0.0 if i == 0 else (min((best[s] for s in range(ns) if s != s2), default=inf) if switch_ok else inf)
                  for s2 in range(ns)]
        best = [min(best[s2], over[s2] + before[s2]) for s2 in range(ns)]
    return drive + min(best)


def _stack_battery(seed: int, n: int) -> None:
    rng = random.Random(seed)
    forced = multi = 0
    for i in range(n):
        for _ in range(30):  # rejection sampling: feasible instances
            sc, at_origin = tc._stack_line(rng)
            P = sc.with_preferences(min_stop_soc=round(rng.uniform(0.0, 1.0), 3)).prepare()
            want = _stack_optimum_floor(P, at_origin)
            if want < math.inf:
                break
        for name, opts in (("cfp", {}), ("cfp-astar", {}), ("cfp-astar", {"potential": "basic"})):
            p = _run(P, name, **opts)
            tc._assert_consistent(p)
            assert not _floor_breaches(P, p), (seed, i, name)
            assert p.total_time_s == pytest.approx(want, rel=1e-12, abs=1e-6), (seed, i, name, opts)
            for s1, s2 in pairwise(p.stops):
                assert s1.position != s2.position or s1.station != s2.station
        forced += _binding(P, p)
        multi += len({s.position for s in p.stops}) < len(p.stops)
    assert forced >= n // 5
    assert multi >= 1


@pytest.mark.parametrize("block", range(3))
def test_cfp_with_a_floor_matches_an_exact_dynamic_programme(block):
    """CFP and CFP-A* (both potentials) equal the exact optimum over any number of stops at a
    node with 2-3 chargers when the first stop must end at the floor."""
    _stack_battery(9300 + block, 120)


@pytest.mark.slow
@pytest.mark.parametrize("block", range(8))
def test_cfp_with_a_floor_matches_an_exact_dynamic_programme_slow(block):
    _stack_battery(19300 + block, 400)


@pytest.mark.parametrize("case", sorted(tc.ORACLE_CASES))
@pytest.mark.parametrize("floor", (0.35, 0.6))
def test_cfp_with_a_floor_matches_a_dense_scan_on_a_line(case, floor):
    """Two stations on a line, the departure energies scanned densely through the evaluator
    (which rejects plans that break the floor): CFP is never worse than any scanned plan and
    within 1 s of the best."""
    lengths, stations, soc = tc.ORACLE_CASES[case]
    P = tc._line_scenario(lengths, stations, soc, min_stop_soc=floor).prepare()
    scan_t, targets = tc._scan_best(P, [(1, 0), (2, 1)])
    assert math.isfinite(scan_t)
    for name in ("cfp", "cfp-astar"):
        p = _run(P, name)
        tc._assert_consistent(p)
        assert not _floor_breaches(P, p)
        assert p.total_time_s <= scan_t + 1e-6, (name, p.total_time_s, scan_t)
        assert scan_t - p.total_time_s <= 1.0, (name, p.total_time_s, scan_t, targets)


def _grid_battery(configs: list[dict], floors=FLOORS) -> None:
    gaps = []
    for cfg in configs:
        sc0 = tc._scenario(cfg)
        c0 = _run(sc0.prepare(), "cfp-astar")
        prev_c = prev_b = None
        for floor in floors:
            sc = sc0.with_preferences(min_stop_soc=floor)
            P = sc.prepare()
            c = _run(P, "cfp-astar")
            if not c.feasible:
                continue
            assert c0.feasible, cfg
            tc._assert_consistent(c)
            assert not _floor_breaches(P, c)
            assert c.total_time_s >= c0.total_time_s - 1e-6, (cfg, floor)  # CFP(floor) >= CFP(0)
            assert c.total_time_s == pytest.approx(_run(P, "cfp").total_time_s, rel=1e-9, abs=1e-6)
            times = []
            for step in (0.1, 0.05, 0.02, 0.005):
                b = _run(sc.with_preferences(charge_step_soc=step).prepare(), "bls-astar")
                assert b.feasible and not _floor_breaches(P, b), (cfg, floor, step)
                assert c.total_time_s <= b.total_time_s + 1e-6, (cfg, floor, step)  # CFP(floor) <= BLS(floor)
                times.append(b.total_time_s)
            # the 0.05 grid contains the 0.1 grid, the 0.005 grid all of them
            assert times[1] <= times[0] + 1e-6 and times[3] <= min(times) + 1e-6
            gaps.append(times[3] - c.total_time_s)
            # monotone in the floor (the floors lie on every grid used here)
            if prev_c is not None:
                assert c.total_time_s >= prev_c - 1e-6 and times[2] >= prev_b - 1e-6, (cfg, floor)
            prev_c, prev_b = c.total_time_s, times[2]
    assert gaps and max(gaps) < 60.0  # the fine grid is close to the continuous optimum
    assert sorted(gaps)[len(gaps) // 2] < 10.0


def test_cfp_bounds_and_grid_convergence_with_a_floor():
    """``CFP(floor) <= BLS(floor)`` on every grid, ``CFP(floor) >= CFP(0)``, BLS on a 0.5 %
    grid close to CFP from above, monotone in the floor -- on random grids with static and
    time-dependent waits."""
    _grid_battery(tc._configs(6, size_lo=5, size_hi=7, seed=511) + tc._configs(3, static=False, seed=512))


@pytest.mark.slow
def test_cfp_bounds_and_grid_convergence_with_a_floor_slow():
    _grid_battery(tc._configs(24, seed=611) + tc._configs(12, static=False, seed=612), floors=(0.1, 0.3, 0.5, 0.7))


# =============================================================================
# 6. Pareto front and monotonicity in the floor
# =============================================================================
def _crit(p: RoutePlan) -> tuple[float, float, float]:
    return p.total_time_s, p.money, p.soh_total_pct


def _covered(p: tuple, front: list[tuple], rel: float = 1e-7) -> bool:
    return any(all(a <= b * (1 + rel) + 1e-9 for a, b in zip(q, p, strict=True)) for q in front)


@pytest.mark.parametrize("seed", range(6))
def test_pareto_front_with_a_floor(seed):
    """The front with a floor holds the BLS optima (time and cost) and every member obeys the
    rule; each member of the front with a higher floor is weakly dominated by a member of the
    front with a lower one (a higher floor only removes routes)."""
    rng = random.Random(seed)
    sc = grid_scenario(rng.randint(4, 6), rng.randint(4, 6), seed=rng.randint(0, 999), n_stations=rng.randint(3, 6),
                       initial_soc=round(rng.uniform(0.15, 0.4), 3), charge_step_soc=0.1,
                       vehicle=rng.choice(("compact50", "city27", "sedan75")),
                       time_dependent_waits=rng.random() < 0.5)
    fronts = []
    for floor in (0.0, 0.3, 0.6):
        P = sc.with_preferences(min_stop_soc=floor).prepare()
        par = _run(P, "pareto")
        if not par.feasible:
            fronts.append([])
            continue
        crit = [_crit(m) for m in par.alternatives]
        for m in par.alternatives:
            assert m.feasible and not _floor_breaches(P, m)
        for obj in ("time", "cost"):
            b = _run(sc.with_preferences(min_stop_soc=floor, objective=obj).prepare(), "bls-astar")
            assert b.feasible and not _floor_breaches(P, b)
            if obj == "time":
                assert min(c[0] for c in crit) == pytest.approx(b.total_time_s, rel=1e-9)
            else:
                assert min(m.generalized_cost for m in par.alternatives) == pytest.approx(b.generalized_cost,
                                                                                          rel=1e-7)
            assert _covered(_crit(b), crit)
        fronts.append(crit)
    for lo, hi in pairwise(fronts):
        assert all(_covered(p, lo) for p in hi)


# =============================================================================
# 7. Monte Carlo replays
# =============================================================================
def test_monte_carlo_replays_keep_the_floor():
    sc = get_scenario("corridor").with_preferences(min_stop_soc=0.5)
    P = sc.prepare()
    for name in ("bls-astar", "cfp-astar", "greedy"):
        p = _run(P, name)
        assert p.feasible and _binding(P, p)
        # zero noise reproduces the evaluator bit for bit
        mc = monte_carlo(P, p, n=5, trip_cv=0.0, edge_cv=0.0, wait_model="expected")
        assert mc.total_time_s.mean == p.total_time_s
        for policy in ("same-target", "adaptive"):
            for run in range(25):
                r = replay_run(P, p, run, seed=3, trip_cv=0.15, edge_cv=0.2, policy=policy)
                assert not _floor_breaches(P, r), (name, policy, run)
    # a hand-made decision below the floor is replayed with the target raised to it
    q = P.capacity
    d = PlanDecision(p.edges, p.levels, [ChargeDecision(c.position, c.station, 0.2 * q) for c in p.decision.charges])
    assert _floor_breaches(P, evaluate(P, d))
    r = replay_run(P, d, 0, trip_cv=0.0, edge_cv=0.0, wait_model="expected")
    assert not _floor_breaches(P, r)
    assert all(s.energy_out_kwh >= P.e_stop_min - 1e-9 for s in r.stops)
