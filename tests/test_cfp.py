"""Tests of Charging Function Propagation (``algorithms/cfp.py``).

Correctness is established in layers:

* the mathematics of the module (label functions, closed-form arrival times,
  dominance, the switching lemma including its two extensions) is tested in
  isolation against direct evaluation and dense scans;
* every plan is replayed by the independent evaluator (feasibility and
  objective equal the internal one);
* CFP and CFP-A* (two potentials) must agree on many random instances;
* CFP may never be slower than BLS on its target grid, and BLS must converge to
  it as the grid is refined (the gap is reported);
* a hand-built line network where the optimal continuous split between a slow
  and a fast station is found by a dense scan through the evaluator;
* regression instances on which the textbook candidate set provably loses
  (non-monotone charging curve, time-dependent waits), verified against a
  fine-grid BLS.
"""

from __future__ import annotations

import dataclasses
import math
import random
from itertools import pairwise, product

import pytest

from evroute.algorithms import get_planner, plan
from evroute.algorithms.cfp import (
    CFPAStarPlanner,
    CFPPlanner,
    arrival_time,
    dominates,
    evaluate_function,
    label_function,
    switch_candidates,
)
from evroute.charging import ChargingFunction
from evroute.evaluate import check_objective, evaluate
from evroute.generators import grid_scenario
from evroute.network import NetworkBuilder
from evroute.plan import ChargeDecision, PlanDecision
from evroute.scenario import Conditions, Preferences, Query, Scenario, Station
from evroute.vehicle import ChargingCurve, get_vehicle

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _run(P, name, **opts):
    return get_planner(name, **opts).plan(P)


def _assert_consistent(p, tol=1e-6):
    """Feasible plan whose evaluated time equals the internal objective."""
    assert p.feasible, p.message
    assert p.internal_objective is not None
    assert abs(p.total_time_s - p.internal_objective) <= tol, (p.total_time_s, p.internal_objective)
    assert check_objective(p)


def _configs(n, *, size_lo=5, size_hi=8, static=True, seed=20261024):
    """Random grid configurations (static waits unless ``static=False``)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        size = rng.randint(size_lo, size_hi)
        out.append(
            dict(
                rows=size,
                cols=rng.randint(max(size_lo, size - 2), size),
                seed=rng.randint(0, 10_000),
                initial_soc=round(rng.uniform(0.15, 0.5), 3),
                n_stations=rng.randint(4, 12),
                spacing_m=rng.choice((8000.0, 12000.0)),
                vehicle=rng.choice(("compact50", "sedan75", "city27", "togg_t10x_lr")),
                queue_aware=(i % 4 != 0),
                time_dependent_waits=not static,
                speed_factors=(1.0, 0.85) if i % 5 == 1 else (1.0,),
            )
        )
    return out


def _scenario(cfg, **extra):
    cfg = dict(cfg, **extra)
    rows, cols = cfg.pop("rows"), cfg.pop("cols")
    return grid_scenario(rows, cols, **cfg)


STATIC_CONFIGS = _configs(18)

# ----------------------------------------------------------------------------
# 1. the mathematics in isolation
# ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def station_cf():
    P = grid_scenario(6, 6, seed=1, n_stations=6).prepare()
    cf = next(f for f, st in zip(P.cf, P.stations) if st.kind == "DC")
    return P, cf


def _direct_B(cf, cap, t0, Fb, b, inn, cost, out, t):
    """B(t) from the definition (no breakpoints)."""
    xtop = max(cap, b)
    x = min(cf.energy_at(Fb + (t - t0)), xtop)
    return min(x - cost, out) if x >= inn - 1e-9 else -math.inf


def _random_label(rng, P, cf):
    """Random label parameters of a feasible label (``inn`` reachable)."""
    return _random_label_for(rng, P.e_min, P.e_charge_cap, P.capacity, cf)


def _random_label_for(rng, e_min, cap, capacity, cf):
    b = rng.uniform(e_min, 0.8 * cap)
    t0 = rng.uniform(0.0, 5000.0)
    cost = rng.uniform(-1.0, 20.0)
    inn = e_min + max(cost, 0.0) + rng.uniform(0.0, 12.0)
    inn = min(inn, cap - 0.1)
    out = min(capacity, e_min + rng.uniform(0.0, 30.0) + max(-cost, 0.0))
    return cap, t0, cf.time_at(b), b, inn, cost, out


def test_label_function_matches_definition(station_cf):
    P, cf = station_cf
    rng = random.Random(7)
    checked = 0
    for _ in range(300):
        cap, t0, Fb, b, inn, cost, out = _random_label(rng, P, cf)
        ts, bs = label_function(cf, cap, t0, Fb, b, inn, cost, out)
        # feasible time: first t with x(t) >= inn
        tf = t0 if b >= inn - 1e-9 else t0 + (cf.time_at(inn) - Fb)
        assert ts[0] == pytest.approx(tf, abs=1e-9)
        assert all(t2 >= t1 for t1, t2 in pairwise(ts))
        assert all(b2 > b1 for b1, b2 in pairwise(bs))
        horizon = ts[-1] + 500.0
        for _k in range(40):
            t = rng.uniform(ts[0], horizon)
            assert evaluate_function(ts, bs, t) == pytest.approx(_direct_B(cf, cap, t0, Fb, b, inn, cost, out, t), abs=1e-7)
        for t in ts:  # exact at the breakpoints themselves
            assert evaluate_function(ts, bs, t) == pytest.approx(_direct_B(cf, cap, t0, Fb, b, inn, cost, out, t), abs=1e-7)
        checked += 1
    assert checked == 300


def test_arrival_time_closed_form(station_cf):
    P, cf = station_cf
    rng = random.Random(8)
    finite = infinite = 0
    for _ in range(300):
        cap, t0, Fb, b, inn, cost, out = _random_label(rng, P, cf)
        goal = rng.uniform(P.e_min, 0.95 * P.capacity)
        got = arrival_time(cf, cap, t0, Fb, b, inn, cost, out, goal)
        ts, bs = label_function(cf, cap, t0, Fb, b, inn, cost, out)
        if bs[-1] < goal - 1e-9:
            assert got == math.inf
            infinite += 1
            continue
        finite += 1
        # B(t) >= goal exactly from `got` on, and not before (up to the label start)
        assert _direct_B(cf, cap, t0, Fb, b, inn, cost, out, got) >= goal - 1e-7
        if got > ts[0] + 1e-6:
            assert _direct_B(cf, cap, t0, Fb, b, inn, cost, out, got - 1e-3) < goal
    assert finite > 50 and infinite > 20
    # labels that never charge
    assert arrival_time(None, 40.0, 12.0, 0.0, 30.0, 5.0, 3.0, 49.0, 20.0) == 12.0
    assert arrival_time(None, 40.0, 12.0, 0.0, 10.0, 5.0, 3.0, 49.0, 20.0) == math.inf


def test_dominates_matches_exact_definition_and_dense_sampling(station_cf):
    """Breakpoint comparison == pointwise comparison for piecewise-linear functions.

    Includes non-concave charging functions (where label functions have convex
    kinks, so the breakpoints of *both* labels must be compared) and near ties.
    """
    P, cf_real = station_cf
    rng = random.Random(9)
    n_true = n_false = n_near = 0
    for k in range(900):
        if k % 3 == 2:
            cf = _rand_cf(rng, concave=False)
            e_min, cap, capacity = 0.1 * cf.capacity, 0.95 * cf.capacity, cf.capacity
        else:
            cf, e_min, cap, capacity = cf_real, P.e_min, P.e_charge_cap, P.capacity
        cap, t0, Fb, b, inn, cost, out = _random_label_for(rng, e_min, cap, capacity, cf)
        f1 = label_function(cf, cap, t0, Fb, b, inn, cost, out)
        if k % 2:
            # an independent label: the two functions usually cross
            cap, t02, Fb2, b2, inn2, cost2, out2 = _random_label_for(rng, e_min, cap, capacity, cf)
            t02 = t0 + rng.uniform(-300.0, 600.0)
            f2 = label_function(cf, cap, t02, Fb2, b2, inn2, cost2, out2)
        else:
            # a perturbed copy: later / costlier / lower ceiling, slightly better, or a near tie
            t02 = t0 + rng.choice((-30.0, 0.0, 5.0, 60.0, 400.0, 0.0, 0.0))
            cost2 = cost + rng.choice((0.0, 0.5, 3.0, -0.2, 0.01, -0.01, -1e-7, 1e-12))
            out2 = out + rng.choice((-4.0, -0.5, 0.0, 0.5))
            inn2 = max(inn, e_min + max(cost2, 0.0))
            if inn2 > cap - 0.1:
                continue
            f2 = label_function(cf, cap, t02, Fb, b, inn2, cost2, out2)
        got = dominates(f1[0], f1[1], f2[0], f2[1])
        samples = {t for t in f1[0] + f2[0] if t >= f2[0][0]}
        samples.add(max(f1[0][-1], f2[0][-1]) + 5000.0)
        lo, hi = f2[0][0], max(f1[0][-1], f2[0][-1]) + 100.0
        samples.update(lo + (hi - lo) * i / 300 for i in range(301))
        worst = min(evaluate_function(*f1, t) - evaluate_function(*f2, t) for t in samples)
        starts_ok = f1[0][0] <= f2[0][0] + 1e-9
        assert got == (starts_ok and worst >= -1e-9), (got, starts_ok, worst)
        n_true += got
        n_false += not got
        n_near += (not got) and starts_ok and -0.05 < worst < -1e-6
    assert n_true > 80 and n_false > 80 and n_near > 5


def test_dominates_deterministic_cases():
    # a convex kink of label 1 *between* the breakpoints of label 2: B1 >= B2 at every
    # breakpoint of label 2 and at the end, but not in between
    assert not dominates([0.0, 100.0, 200.0], [10.0, 11.0, 20.0], [0.0, 200.0], [10.0, 20.0])
    # the same function is dominated by (and dominates) itself; a lower copy is dominated
    f = ([0.0, 100.0, 200.0], [10.0, 15.0, 20.0])
    assert dominates(*f, *f)
    assert dominates(*f, [0.0, 100.0, 200.0], [9.0, 14.0, 19.5])
    assert not dominates([0.0, 100.0, 200.0], [9.0, 14.0, 19.5], *f)
    # starting later never dominates; starting earlier with the same shape does
    assert not dominates([10.0, 110.0, 210.0], [10.0, 15.0, 20.0], *f)
    assert dominates([0.0, 90.0, 190.0], [10.0, 15.0, 20.0], *f)
    # a constant (never charging) label vs a charging one
    assert dominates([0.0], [30.0], [5.0, 50.0], [10.0, 25.0])
    assert not dominates([0.0], [20.0], [5.0, 50.0], [10.0, 25.0])
    assert not dominates([0.0, 50.0], [10.0, 30.0], [0.0], [30.0])  # rises later than the constant label
    assert dominates([0.0, 50.0], [10.0, 30.0], [0.0], [10.0])


def _rand_cf(rng, concave):
    k = rng.randint(3, 8)
    eb = [0.0]
    for _ in range(k):
        eb.append(eb[-1] + rng.uniform(2.0, 12.0))
    p = [rng.uniform(5.0, 150.0) for _ in range(k)]
    if concave:
        p.sort(reverse=True)  # non-increasing power == concave charging function
    return ChargingFunction(eb, p, [0.5] * k, [1e-4] * k)


def _rand_B(rng, cap):
    n = rng.randint(2, 6)
    ts = sorted(rng.uniform(0.0, 3000.0) for _ in range(n))
    bs = sorted(rng.uniform(1.0, 0.9 * cap) for _ in range(n))
    return ts, bs


def _lemma_trial(rng, concave, time_dep, extended):
    """One random instance of the switching lemma; returns the number of final times
    ``tau`` for which the candidate set misses the best post-stop energy."""
    f2 = _rand_cf(rng, concave)
    cap = 0.95 * f2.capacity
    ts, bs = _rand_B(rng, cap)
    su = rng.uniform(0.0, 300.0)
    knots = sorted(rng.uniform(ts[0] - 200.0, ts[-1] + 200.0) for _ in range(3))
    wv = [rng.uniform(0.0, 400.0) for _ in knots]
    for i in range(1, 3):  # FIFO: slope >= -0.9
        wv[i] = max(wv[i], wv[i - 1] - 0.9 * (knots[i] - knots[i - 1]))

    def wait(t):
        return evaluate_function(knots, wv, t) if time_dep else su

    def post(t, tau):
        """Energy when leaving at ``tau`` after switching to ``f2`` at time ``t``."""
        b = evaluate_function(ts, bs, t)
        if b >= cap:
            return b
        return min(f2.energy_at(f2.time_at(b) + tau - t - wait(t)), cap)

    ups = [f2.e_breaks[i] for i in range(1, len(f2.power_kw)) if f2.power_kw[i] > f2.power_kw[i - 1]]
    kt = [k for k in knots if ts[0] < k < ts[-1]] if (time_dep and extended) else []
    cand_times, _ = switch_candidates(ts, bs, ups if extended else [], kt)
    bad = 0
    for tau in (rng.uniform(ts[0], ts[-1] + 2000.0) for _ in range(6)):
        hi = min(tau, ts[-1] + 500.0)
        dense = max(
            (post(t, tau) for t in (ts[0] + (hi - ts[0]) * i / 400 for i in range(401)) if tau >= t + wait(t)),
            default=-1.0,
        )
        cand = max((post(t, tau) for t in cand_times if tau >= t + wait(t)), default=-1.0)
        cand = max(cand, evaluate_function(ts, bs, tau))  # not stopping at all
        bad += dense > cand + 1e-6
    return bad


def _lemma_violations(concave, time_dep, extended, trials, seed):
    rng = random.Random(seed)
    return sum(_lemma_trial(rng, concave, time_dep, extended) for _ in range(trials))


def test_switching_lemma_and_necessity_of_both_extensions():
    """Lemma 1 (breakpoints suffice) and the two additions that make it exact in general."""
    # concave charging function, static waits: the textbook candidate set is exact
    assert _lemma_violations(True, False, False, 250, 1) == 0
    # the extended candidate set is exact in every regime ...
    for concave, td in ((True, False), (True, True), (False, False), (False, True)):
        assert _lemma_violations(concave, td, True, 250, 2) == 0, (concave, td)
    # ... and each addition is needed: the textbook set loses with non-concave
    # charging functions and with time-dependent waits
    assert _lemma_violations(False, False, False, 250, 3) > 0
    assert _lemma_violations(True, True, False, 250, 4) > 0


# ----------------------------------------------------------------------------
# 2. planners on random instances
# ----------------------------------------------------------------------------


def test_cfp_and_astar_agree_and_match_the_evaluator():
    """CFP == CFP-A* (both potentials) on >= 15 random static-wait grids; evaluator agrees."""
    feasible = with_stops = 0
    for cfg in STATIC_CONFIGS:
        P = _scenario(cfg).prepare()
        plans = [_run(P, "cfp"), _run(P, "cfp-astar"), _run(P, "cfp-astar", potential="basic")]
        assert len({p.feasible for p in plans}) == 1, (cfg, [p.message for p in plans])
        if not plans[0].feasible:
            assert all(p.message for p in plans)
            continue
        feasible += 1
        with_stops += plans[0].num_stops > 0
        ref = plans[0].internal_objective
        for p in plans:
            _assert_consistent(p)
            assert p.internal_objective == pytest.approx(ref, abs=1e-6), (cfg, p.algorithm)
            assert p.stats.extra["optimality"] == "proven"
            assert p.stats.settled > 0
    assert len(STATIC_CONFIGS) >= 15
    assert feasible >= 14
    assert with_stops >= 8


def test_optimality_claim_matches_the_assumptions():
    sc = grid_scenario(7, 7, seed=3, initial_soc=0.25, time_dependent_waits=False)  # compact50, reserve 10 %
    P = sc.prepare()
    assert _run(P, "cfp-astar").stats.extra["optimality"] == "proven"
    # textbook candidates: exact for concave charging (power ramp-up lies below the reserve) and static waits
    assert _run(P, "cfp-astar", exact_switching=False).stats.extra["optimality"] == "proven"
    # ... but not with time-dependent waits, and not with a charging ramp-up above the reserve
    P = grid_scenario(7, 7, seed=3, initial_soc=0.25, time_dependent_waits=True).prepare()
    assert _run(P, "cfp-astar").stats.extra["optimality"] == "proven"
    assert _run(P, "cfp-astar", exact_switching=False).stats.extra["optimality"] == "not proven"
    sc = grid_scenario(7, 7, seed=3, initial_soc=0.25, time_dependent_waits=False, vehicle="sedan75")
    assert _run(sc.prepare(), "cfp-astar", exact_switching=False).stats.extra["optimality"] == "proven"  # stations <= 150 kW
    for st in sc.stations:  # a 350 kW charger follows the vehicle's ramp-up (power rises until 15 % SoC)
        if st.kind == "DC":
            st.power_kw = 350.0
    sc._prepared = None
    P = sc.prepare()
    assert _run(P, "cfp-astar", exact_switching=False).stats.extra["optimality"] == "not proven"
    assert _run(P, "cfp-astar").stats.extra["optimality"] == "proven"


def test_grid_optimum_converges_to_cfp_from_above():
    """BLS on nested target grids 0.02 > 0.005 > 0.001 > 0.0002 (override of ``P.target_grid``)."""
    cfgs = [
        dict(rows=5, cols=5, seed=s, initial_soc=soc, n_stations=5, vehicle=veh, queue_aware=False,
             time_dependent_waits=False)
        for s, soc, veh in ((11, 0.22, "compact50"), (12, 0.30, "sedan75"), (13, 0.25, "togg_t10x_lr"), (14, 0.2, "compact50"))
    ]
    first = last = 0.0
    for cfg in cfgs:
        P = _scenario(cfg).prepare()
        c = _run(P, "cfp-astar")
        assert c.feasible
        gaps = []
        for step in (0.02, 0.005, 0.001, 0.0002):
            q = P.capacity
            n_steps = round(1 / step)
            P.target_grid = sorted(
                {round(k * step * q, 9) for k in range(1, n_steps + 1) if k * step * q <= P.e_charge_cap + 1e-9}
                | {P.e_charge_cap}
            )
            b = _run(P, "bls-astar")
            assert b.feasible
            gaps.append(b.total_time_s - c.total_time_s)
        assert all(g >= -1e-6 for g in gaps), gaps  # CFP is never beaten
        assert all(g2 <= g1 + 1e-6 for g1, g2 in pairwise(gaps)), gaps  # nested grids: monotone
        first += gaps[0]
        last += gaps[-1]
    assert first > 5.0 and last < 0.2 * first and last / len(cfgs) < 2.0, (first, last)


def test_cfp_never_slower_than_bls_and_gap_shrinks_with_the_grid(capsys):
    """CFP <= BLS(grid+breaks) for every grid; BLS converges to CFP as the step shrinks."""
    gaps_coarse, gaps_fine = [], []
    for cfg in STATIC_CONFIGS:
        sc = _scenario(cfg)
        P = sc.prepare()
        c = _run(P, "cfp-astar")
        if not c.feasible:
            continue
        coarse = _run(P, "bls-astar")  # charge_step_soc = 0.02
        fine = _run(sc.with_preferences(charge_step_soc=0.005).prepare(), "bls-astar")
        assert coarse.feasible and fine.feasible
        t_c, t_coarse, t_fine = c.total_time_s, coarse.total_time_s, fine.total_time_s
        assert t_c <= t_fine + 1e-6, (cfg, t_c, t_fine)
        assert t_fine <= t_coarse + 1e-6, (cfg, t_fine, t_coarse)
        gaps_coarse.append(t_coarse - t_c)
        gaps_fine.append(t_fine - t_c)
    assert len(gaps_fine) >= 12
    with capsys.disabled():
        print(
            f"\n[cfp] BLS - CFP gap over {len(gaps_fine)} instances: step 0.02: max {max(gaps_coarse):.2f} s, "
            f"mean {sum(gaps_coarse) / len(gaps_coarse):.2f} s; step 0.005: max {max(gaps_fine):.2f} s, "
            f"mean {sum(gaps_fine) / len(gaps_fine):.2f} s"
        )
    assert max(gaps_fine) <= max(gaps_coarse) + 1e-6
    assert sum(gaps_fine) < sum(gaps_coarse)  # the grid optimum approaches the continuous one
    assert max(gaps_coarse) > 1.0  # the continuous optimum is genuinely better somewhere


def test_charging_amounts_are_continuous_not_on_a_grid():
    """CFP plans stop at energies that no charging grid contains (e.g. just-enough charging)."""
    off_grid = 0
    for cfg in STATIC_CONFIGS:
        P = _scenario(cfg).prepare()
        p = _run(P, "cfp-astar")
        if not p.feasible:
            continue
        for st in p.stops:
            grid = set(P.target_grid) | set(P.cf[st.station].e_breaks)
            if all(abs(st.energy_out_kwh - y) > 1e-6 for y in grid):
                off_grid += 1
    assert off_grid >= 3


def test_local_optimality_of_the_charging_targets():
    """No small change of any target makes the CFP route faster (evaluator replay)."""
    tested = 0
    for cfg in STATIC_CONFIGS[:10]:
        P = _scenario(cfg).prepare()
        p = _run(P, "cfp-astar")
        if not p.feasible or not p.stops:
            continue
        base = p.total_time_s
        dec = p.decision
        tested += 1
        for i, ch in enumerate(dec.charges):
            for delta in (-1.0, -0.1, -0.01, 0.01, 0.1, 1.0):
                target = min(ch.target_kwh + delta, P.e_charge_cap)
                charges = list(dec.charges)
                charges[i] = ChargeDecision(ch.position, ch.station, target)
                q = evaluate(P, PlanDecision(list(dec.edges), list(dec.levels), charges))
                if q.feasible:
                    assert q.total_time_s >= base - 1e-6, (cfg, i, delta, q.total_time_s, base)
    assert tested >= 5


# ----------------------------------------------------------------------------
# 3. analytic oracle: slow and fast station on a line
# ----------------------------------------------------------------------------


def _line_scenario(
    lengths, stations, soc, *, vehicle="compact50", reserve=0.10, td=False, elevations=None, road="trunk", **prefs
):
    """origin - 1 - 2 - ... - destination on a straight road (flat unless ``elevations`` is given)."""
    b = NetworkBuilder()
    ids, x = [], 0.0
    for i in range(len(lengths) + 1):
        ids.append(b.add_node(x, 0.0, 0.0 if elevations is None else elevations[i]))
        if i < len(lengths):
            x += lengths[i]
    for i, length in enumerate(lengths):
        b.add_road(ids[i], ids[i + 1], road_class=road, length=length)
    sts = [
        Station(id=i, node=nd, power_kw=pw, kind=kind, plugs=2, price_per_kwh=0.5, name=f"S{i}", setup_s=su)
        for i, (nd, pw, kind, su) in enumerate(stations)
    ]
    veh = get_vehicle(vehicle) if isinstance(vehicle, str) else vehicle
    return Scenario(
        name="line",
        network=b.build(),
        stations=sts,
        vehicle=veh,
        query=Query(0, len(lengths), soc, 9 * 3600.0),
        conditions=Conditions(temperature_c=20.0),
        preferences=Preferences(
            reserve_soc=reserve, min_arrival_soc=reserve, queue_aware=td, time_dependent_waits=td, **prefs
        ),
    )


def _scan_best(P, stops, step=0.5):
    """Dense scan of the departure energies at ``stops`` (list of (position, station)).

    Every candidate is a :class:`PlanDecision` replayed by the evaluator; each
    stop may also be skipped.  Coarse grid first, then two refinements around
    the best candidate.  Returns ``(best time, targets)``.
    """
    net = P.net
    n = P.destination - P.origin  # nodes are numbered along the line
    edges = [net.edge_between(i, i + 1) for i in range(n)]
    levels = [0] * n

    def run(value_lists):
        best = (math.inf, None)
        for combo in product(*value_lists):
            ch = [ChargeDecision(pos, st, y) for (pos, st), y in zip(stops, combo) if y is not None]
            q = evaluate(P, PlanDecision(edges, levels, ch))
            if q.feasible and q.total_time_s < best[0]:
                best = (q.total_time_s, combo)
        return best

    def grid(lo, hi, st):
        k = int((hi - lo) / st)
        return [lo + i * st for i in range(k + 1)] + [hi]

    lo, hi = P.e_min, P.e_charge_cap
    best = run([[None] + grid(lo, hi, step)] * len(stops))
    for width, st in ((0.75, 0.025), (0.05, 0.002)):
        lists = []
        for y in best[1]:
            lists.append([None] if y is None else [None] + grid(max(lo, y - width), min(hi, y + width), st))
        best = min(best, run(lists), key=lambda r: r[0])
    return best


ORACLE_CASES = {
    # slow A, fast B: charge at A only what is needed to reach B, the rest at B
    "slow-A-fast-B": ([40e3, 80e3, 150e3], [(1, 50.0, "DC", 120.0), (2, 150.0, "DC", 120.0)], 0.30),
    # fast A, slow AC B: charging more at A and skipping B altogether wins
    "fast-A-slow-B": ([40e3, 80e3, 150e3], [(1, 150.0, "DC", 120.0), (2, 22.0, "AC", 120.0)], 0.30),
    # AC at A (cheap setup), DC at B (expensive setup)
    "AC-A-DC-B": ([30e3, 90e3, 120e3], [(1, 22.0, "AC", 60.0), (2, 150.0, "DC", 180.0)], 0.22),
}


@pytest.mark.parametrize("case", sorted(ORACLE_CASES))
def test_line_oracle_matches_dense_scan(case):
    lengths, stations, soc = ORACLE_CASES[case]
    sc = _line_scenario(lengths, stations, soc)
    P = sc.prepare()
    scan_t, scan_targets = _scan_best(P, [(1, 0), (2, 1)])
    assert math.isfinite(scan_t)
    for name in ("cfp", "cfp-astar"):
        p = _run(P, name)
        _assert_consistent(p)
        assert p.total_time_s <= scan_t + 1e-6, (name, p.total_time_s, scan_t)  # never worse than any scanned plan
        assert scan_t - p.total_time_s <= 1.0, (name, p.total_time_s, scan_t, scan_targets)  # ... and within 1 s
    # BLS on a 2 % grid is strictly worse where charging targets matter
    bls = _run(P, "bls-astar")
    assert bls.total_time_s >= p.total_time_s - 1e-6


def test_line_oracle_optimum_uses_the_expected_stations():
    p = _run(_line_scenario(*ORACLE_CASES["slow-A-fast-B"][:2], ORACLE_CASES["slow-A-fast-B"][2]).prepare(), "cfp")
    assert [s.station for s in p.stops] == [0, 1]
    # just-enough charging: the vehicle reaches B exactly at the reserve
    assert p.stops[1].energy_in_kwh == pytest.approx(p.stops[1].soc_in * p.capacity_kwh)
    assert p.stops[1].energy_in_kwh == pytest.approx(0.10 * p.capacity_kwh, abs=1e-6)
    p = _run(_line_scenario(*ORACLE_CASES["fast-A-slow-B"][:2], ORACLE_CASES["fast-A-slow-B"][2]).prepare(), "cfp")
    assert [s.station for s in p.stops] == [0]


# ----------------------------------------------------------------------------
# 4. regression instances: the textbook candidate set provably loses
# ----------------------------------------------------------------------------


def _nonconcave_vehicle():
    curve = ChargingCurve((0.0, 0.35, 0.75, 1.0), (58.0, 150.0, 28.0))  # ramps up, then falls
    return dataclasses.replace(get_vehicle("compact50"), dc_curve=curve)


def test_nonconcave_charging_curve_needs_upward_break_candidates():
    sc = _line_scenario(
        [45600.0, 40600.0, 125000.0],
        [(1, 80.0, "DC", 30.0), (2, 150.0, "DC", 30.0)],
        0.26,
        vehicle=_nonconcave_vehicle(),
        charge_step_soc=0.002,
    )
    P = sc.prepare()
    full = _run(P, "cfp")
    textbook = _run(P, "cfp", exact_switching=False)
    fine = _run(P, "bls-astar")  # 0.2 % grid + breakpoints
    for p in (full, textbook, fine):
        assert p.feasible
    _assert_consistent(full)
    _assert_consistent(textbook)  # still a valid plan, just not the fastest
    assert textbook.stats.extra["optimality"] == "not proven"  # non-concave curve: the textbook set is not exact
    assert full.stats.extra["optimality"] == "proven"
    assert full.total_time_s <= fine.total_time_s + 1e-6
    assert fine.total_time_s - full.total_time_s < 8.0  # the grid optimum is just above the continuous one
    assert textbook.total_time_s > full.total_time_s + 10.0  # the textbook candidates miss the optimum
    assert textbook.total_time_s > fine.total_time_s  # ... even a 0.2 % grid beats them
    # A* agrees with plain CFP
    assert _run(P, "cfp-astar").total_time_s == pytest.approx(full.total_time_s, abs=1e-6)


def _wait_knot_instance():
    """Five-leg line, small battery, four stations with peaked demand (steep wait profiles)."""
    lens = [59325.8, 51081.5, 36892.9, 53484.2, 46936.0]
    stations = [(1, 22.0, "DC", 120.0), (2, 50.0, "DC", 20.0), (3, 100.0, "DC", 20.0), (4, 50.0, "DC", 20.0)]
    sc = _line_scenario(lens, stations, 0.509, vehicle="city27", td=True, charge_step_soc=0.002)
    params = [
        (147.438, 3, 40.0, {16: 8.157, 22: 8.113, 23: 4.307}),
        (254.991, 2, 40.0, {4: 5.023, 5: 4.927, 13: 7.574}),
        (140.305, 2, 25.0, {4: 8.974, 11: 7.14, 16: 7.188}),
        (286.814, 2, 40.0, {3: 7.439, 18: 4.812, 22: 8.256}),
    ]
    for st, (sessions, plugs, service, peaks) in zip(sc.stations, params):
        st.daily_sessions, st.plugs, st.mean_service_min = sessions, plugs, service
        st.demand_profile = [peaks.get(h, 0.2) for h in range(24)]
    return dataclasses.replace(sc, query=Query(0, len(lens), 0.509, 42342.2))


def test_time_dependent_waits_need_wait_knot_candidates():
    sc = _wait_knot_instance()
    P = sc.prepare()
    assert P.time_dependent
    full = _run(P, "cfp-astar")
    plain = _run(P, "cfp")
    textbook = _run(P, "cfp-astar", exact_switching=False)
    fine = _run(P, "bls-astar")
    for p in (full, plain, textbook):
        _assert_consistent(p)  # waits are evaluated at the concrete switch time: always consistent
    assert plain.total_time_s == pytest.approx(full.total_time_s, abs=1e-6)
    assert full.stats.extra["optimality"] == "proven"
    assert textbook.stats.extra["optimality"] == "not proven"
    assert full.total_time_s <= fine.total_time_s + 1e-6
    assert textbook.total_time_s > full.total_time_s + 10.0


# ----------------------------------------------------------------------------
# 5. time-dependent waits, infeasible instances, edge cases
# ----------------------------------------------------------------------------


def test_time_dependent_waits_feasible_consistent_and_never_worse_than_bls():
    cfgs = _configs(10, static=False, seed=424242, size_hi=7)
    feasible = 0
    for cfg in cfgs:
        cfg = dict(cfg, queue_aware=True)
        sc = _scenario(cfg)
        P = sc.prepare()
        assert P.time_dependent
        a = _run(P, "cfp-astar")
        b = _run(P, "cfp")
        t = _run(P, "cfp-astar", exact_switching=False)
        bls = _run(P, "bls-astar")
        assert a.feasible == b.feasible == t.feasible == bls.feasible
        if not a.feasible:
            continue
        feasible += 1
        for p in (a, b, t):
            _assert_consistent(p)
        assert a.total_time_s == pytest.approx(b.total_time_s, abs=1e-6)
        assert a.total_time_s <= bls.total_time_s + 1e-6  # exact also with time-dependent waits
        assert a.total_time_s <= t.total_time_s + 1e-6
    assert feasible >= 7


def test_infeasible_instances_are_reported():
    # 1. battery cannot bridge the distance and there are no stations
    sc = grid_scenario(9, 9, seed=2, initial_soc=0.3, n_stations=0)
    for name in ("cfp", "cfp-astar"):
        p = _run(sc.prepare(), name)
        assert not p.feasible and p.message
        assert p.stats is not None and p.stats.algorithm == name
        assert p.total_time_s == math.inf
    # 2. initial energy below the reserve, no station at the origin
    sc = grid_scenario(7, 7, seed=4, n_stations=8, initial_soc=0.06)
    for name in ("cfp", "cfp-astar"):
        p = _run(sc.prepare(), name)
        assert not p.feasible and "reserve" in p.message
    # 3. unreachable destination
    b = NetworkBuilder()
    a0 = b.add_node(0, 0, 0)
    a1 = b.add_node(10000, 0, 0)
    b.add_node(20000, 0, 0)
    b.add_edge(a0, a1, road_class="trunk")
    sc = Scenario(
        name="iso",
        network=b.build(),
        stations=[],
        vehicle=get_vehicle("compact50"),
        query=Query(origin=0, destination=2, initial_soc=0.9),
    )
    for name in ("cfp", "cfp-astar"):
        p = _run(sc.prepare(), name)
        assert not p.feasible and "not reachable" in p.message
    # 4. the infeasible verdicts agree with BLS on random instances
    for cfg in _configs(12, seed=99, size_lo=6, size_hi=9):
        cfg = dict(cfg, initial_soc=0.12)
        P = _scenario(cfg).prepare()
        assert _run(P, "cfp-astar").feasible == _run(P, "bls-astar").feasible


def test_edge_cases():
    # origin == destination: nothing to do
    sc = grid_scenario(6, 6, seed=2, initial_soc=0.5)
    sc = dataclasses.replace(sc, query=Query(origin=3, destination=3, initial_soc=0.5))
    for name in ("cfp", "cfp-astar"):
        p = _run(sc.prepare(), name)
        assert p.feasible and p.total_time_s == 0.0 and p.edges == [] and p.internal_objective == 0.0
    # no stations, enough energy: the fastest path
    sc = grid_scenario(6, 6, seed=2, initial_soc=0.9, n_stations=0)
    P = sc.prepare()
    cfp_p, bls_p = _run(P, "cfp"), _run(P, "bls")
    _assert_consistent(cfp_p)
    assert cfp_p.num_stops == 0 and cfp_p.total_time_s == pytest.approx(bls_p.total_time_s, abs=1e-6)
    # initial energy below the reserve with a station at the origin: charge there first
    sc = grid_scenario(7, 7, seed=4, n_stations=8, initial_soc=0.06)
    sc.stations.append(
        Station(id=len(sc.stations), node=sc.query.origin, power_kw=100.0, kind="DC", plugs=4, price_per_kwh=0.5, name="O")
    )
    P = sc.prepare()
    assert P.e_init < P.e_min
    for name in ("cfp", "cfp-astar"):
        p = _run(P, name)
        _assert_consistent(p)
        assert p.stops[0].position == 0 and p.stops[0].energy_out_kwh >= P.e_min - 1e-6
    assert _run(P, "cfp").total_time_s <= _run(P, "bls").total_time_s + 1e-6
    # several stations at one node: consecutive stops only at different stations
    sc = grid_scenario(8, 8, seed=7, n_stations=8, initial_soc=0.25)
    for i, s in enumerate(list(sc.stations[:4])):
        sc.stations.append(
            Station(
                id=len(sc.stations),
                node=s.node,
                power_kw=150.0 if s.kind == "AC" else 22.0,
                kind="DC" if s.kind == "AC" else "AC",
                plugs=2,
                price_per_kwh=0.4,
                name=f"X{i}",
            )
        )
    P = sc.prepare()
    c, a, bls = _run(P, "cfp"), _run(P, "cfp-astar"), _run(P, "bls-astar")
    _assert_consistent(c)
    assert c.total_time_s == pytest.approx(a.total_time_s, abs=1e-6)
    assert c.total_time_s <= bls.total_time_s + 1e-6
    for s1, s2 in zip(c.stops, c.stops[1:]):  # never the same station twice in a row
        assert s1.position != s2.position or s1.station != s2.station


def test_best_station_at_a_multi_station_node_is_chosen():
    lengths = [40e3, 90e3, 100e3]
    slow, fast = (1, 22.0, "AC", 60.0), (1, 150.0, "DC", 60.0)
    for stations, expected in (([slow, fast], 1), ([fast, slow], 0)):
        sc = _line_scenario(lengths, stations, 0.30)
        for name in ("cfp", "cfp-astar"):
            p = _run(sc.prepare(), name)
            _assert_consistent(p)
            assert [s.station for s in p.stops] == [expected], name
        only_fast = _run(_line_scenario(lengths, [fast], 0.30).prepare(), "cfp")
        assert p.total_time_s == pytest.approx(only_fast.total_time_s, abs=1e-6)
        assert p.total_time_s <= _run(sc.prepare(), "bls-astar").total_time_s + 1e-6


def _two_station_node(*, at_origin, setup_s, soc, reserve, temperature_c, vehicle):
    """O --30 km--> S --150 km--> D (``at_origin``: O = S, O --200 km--> D); the station node
    hosts a DC charger (50 kW, or 150 kW at the origin) and a 22 kW AC charger."""
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, 100.0)
    s = o if at_origin else b.add_node(30e3, 0.0, 100.0)
    d = b.add_node(200e3 if at_origin else 180e3, 0.0, 100.0)
    if not at_origin:
        b.add_road(o, s, road_class="motorway")
    b.add_road(s, d, road_class="motorway")
    st = [Station(0, s, 150.0 if at_origin else 50.0, "DC", price_per_kwh=0.75, setup_s=setup_s, daily_sessions=1.0),
          Station(1, s, 22.0, "AC", price_per_kwh=0.30, setup_s=setup_s, daily_sessions=1.0)]
    return Scenario("two stations at one node", b.build(), st, vehicle, Query(o, d, soc, 9 * 3600.0),
                    Conditions(temperature_c=temperature_c, preconditioning=False),
                    Preferences(queue_aware=False, charge_step_soc=0.05, reserve_soc=reserve, min_arrival_soc=0.1))


@pytest.mark.parametrize("case", ["derated DC then AC", "AC below the reserve then DC"])
def test_consecutive_stops_at_one_node(case):
    """Regression (BUG-3 of the cross-validation suite): a stop may be followed by a stop at
    *another* station of the same node.  (a) Cold compact50, no preconditioning: the derated
    DC charger is faster than the AC charger up to 37.4 kWh and slower above, so with zero
    setup the fastest route charges DC to 37.4 kWh and then AC to the just-enough energy.
    (b) A DC curve that is slow (15 kW) below 20 % SoC, 5 % initial charge, 25 % reserve and
    both chargers at the origin: AC to 20 % -- below the reserve, which the evaluator checks on
    arrival and departure only -- then DC (an *extended* label, module docs)."""
    if case.startswith("derated"):
        sc = _two_station_node(at_origin=False, setup_s=0.0, soc=0.3, reserve=0.1, temperature_c=-10.0,
                               vehicle=get_vehicle("compact50"))
        first = 37.4
    else:
        veh = dataclasses.replace(get_vehicle("compact50"), dc_curve=ChargingCurve((0.0, 0.2, 1.0), (15.0, 150.0)),
                                  ac_max_kw=22.0)
        sc = _two_station_node(at_origin=True, setup_s=30.0, soc=0.05, reserve=0.25, temperature_c=20.0, vehicle=veh)
        first = 10.0
    P = sc.prepare()
    one = min(_run(sc.with_(stations=[s]).prepare(), "cfp").total_time_s for s in sc.stations)
    fine = _run(sc.with_preferences(charge_step_soc=0.005).prepare(), "bls-astar")
    for name in ("cfp", "cfp-astar"):
        p = _run(P, name)
        _assert_consistent(p)
        assert [(s.position, s.station) for s in p.stops] == [(p.stops[0].position, 0 if first > 20 else 1),
                                                              (p.stops[0].position, 1 if first > 20 else 0)]
        assert p.stops[0].energy_out_kwh == pytest.approx(first, abs=1e-6)
        assert p.total_time_s < one - 10.0  # the best single stop is clearly slower
        assert p.total_time_s <= fine.total_time_s + 1e-6  # and no grid route beats the continuous optimum
        assert fine.total_time_s - p.total_time_s < 5.0
    if not case.startswith("derated"):
        assert p.stops[0].energy_out_kwh < P.e_min < p.stops[1].energy_out_kwh


def _regen_line(length_last, soc=1.0):
    """Steep descent (regeneration) from a full battery, then a long climb to the destination."""
    return _line_scenario(
        [20000.0, length_last], [], soc, elevations=[1500.0, 300.0, 1500.0], road="secondary"
    )


def test_regeneration_is_clamped_at_full_battery():
    # the descent regenerates, but a full battery cannot take more: after it the vehicle holds
    # exactly Q, not Q + |c|.  With a last leg that needs between Q - reserve and Q + |c| - reserve
    # the trip is infeasible, and an algorithm that forgot the clamp would claim otherwise.
    probe = _regen_line(1000.0).prepare()
    c0 = probe.edge_energy[0][probe.net.edge_between(0, 1)]
    assert c0 < -1.0, c0  # net regeneration on the first leg
    per_m = probe.edge_energy[0][probe.net.edge_between(1, 2)] / 1000.0

    def length_for(consumption):
        lo, hi = 1000.0, 1_000_000.0
        for _ in range(60):  # consumption is increasing in the length
            mid = 0.5 * (lo + hi)
            P = _regen_line(mid).prepare()
            if P.edge_energy[0][P.net.edge_between(1, 2)] < consumption:
                lo = mid
            else:
                hi = mid
        return hi

    assert per_m > 0
    Q = probe.capacity
    need_max = Q - probe.e_dest_min  # most the last leg may consume after a clamped descent
    too_much = length_for(need_max + 0.5 * (-c0))  # feasible only if the regenerated energy were kept
    ok = length_for(need_max - 0.5)
    for name in ("cfp", "cfp-astar", "bls"):
        p = _run(_regen_line(too_much).prepare(), name)
        assert not p.feasible, name
        assert p.internal_objective is None and "no battery-feasible route" in p.message, (name, p.message)
        q = _run(_regen_line(ok).prepare(), name)
        assert q.feasible, (name, q.message)
        assert q.arrival_soc * q.capacity_kwh == pytest.approx(probe.e_dest_min + 0.5, abs=1e-3)
    _assert_consistent(_run(_regen_line(ok).prepare(), "cfp"))


def test_cost_objective_is_planned_as_time():
    sc = grid_scenario(7, 7, seed=3, initial_soc=0.25)
    p_time = _run(sc.prepare(), "cfp-astar")
    sc_cost = sc.with_preferences(objective="cost")
    p_cost = _run(sc_cost.prepare(), "cfp-astar")
    assert p_cost.feasible and p_time.feasible
    assert p_cost.total_time_s == pytest.approx(p_time.total_time_s, abs=1e-9)  # still the fastest route
    assert p_cost.stats.extra["planned_objective"] == "time"
    assert p_cost.stats.extra["time_s"] == pytest.approx(p_time.total_time_s, abs=1e-6)
    assert p_cost.objective == "cost" and p_cost.generalized_cost == pytest.approx(p_time.generalized_cost)
    assert check_objective(p_cost)  # internal_objective is the evaluated generalised cost
    assert get_planner("cfp").objectives == ("time",)


def test_label_limit_is_reported():
    sc = grid_scenario(8, 8, seed=7, n_stations=8, initial_soc=0.25)
    P = sc.prepare()
    p = _run(P, "cfp", max_labels=20)
    assert p.stats.extra["status"] == "limit"
    assert not p.feasible and "label limit" in p.message
    full = _run(P, "cfp")
    assert full.stats.extra["status"] == "optimal"
    # a limit hit after the first incumbent still returns that (feasible) plan
    q = _run(P, "cfp-astar", max_labels=60)
    if q.feasible:
        _assert_consistent(q)
        assert "not proven" in q.message and q.total_time_s >= full.total_time_s - 1e-6


def test_trace_stats_and_registry():
    sc = grid_scenario(7, 7, seed=3, initial_soc=0.25)
    for name, cls in (("cfp", CFPPlanner), ("cfp-astar", CFPAStarPlanner)):
        pl = get_planner(name)
        assert isinstance(pl, cls)
        info = pl.info()
        assert info["name"] == name and info["exact"] == "exact" and info["objectives"] == ["time"]
        assert info["title"] == {"cfp": "CFP (charging function propagation)", "cfp-astar": "CFP-A*"}[name]
        p = plan(sc, name, trace=True)
        assert p.trace is not None and p.trace.nodes[0] == p.nodes[0]
        keys = p.trace.keys
        assert all(k2 >= k1 - 1e-9 for k1, k2 in pairwise(keys))  # settled in key order
        s = p.stats
        assert s.settled > 0 and s.pushes >= s.settled and s.generated >= s.pushes and s.peak_queue > 0
        assert s.algorithm == name and s.runtime_s > 0
        assert p.algorithm == name
    with pytest.raises(ValueError):
        _run(sc.prepare(), "cfp-astar", potential="nonsense")


def test_energy_cap_keeps_the_label_sets_small():
    """Regression guard: with several speed levels the Pareto sets explode unless labels
    are compared as ``min(B, need[v])`` (about 30 000 settled labels without the cap)."""
    sc = grid_scenario(
        10, 9, seed=38806, spacing_m=12000.0, n_stations=4, vehicle="togg_t10x_lr", initial_soc=0.312,
        queue_aware=False, time_dependent_waits=False, speed_factors=(1.0, 0.85),
    )
    P = sc.prepare()
    plain, astar = _run(P, "cfp"), _run(P, "cfp-astar")
    _assert_consistent(plain)
    assert plain.total_time_s == pytest.approx(astar.total_time_s, abs=1e-6)
    assert plain.stats.settled < 6000
    assert astar.stats.settled < 2000
    assert _run(P, "bls-astar").total_time_s >= plain.total_time_s - 1e-6


def test_astar_expands_far_fewer_labels_than_dijkstra_order():
    P = grid_scenario(8, 8, seed=3, initial_soc=0.25).prepare()
    plain, astar = _run(P, "cfp"), _run(P, "cfp-astar")
    assert astar.stats.settled < plain.stats.settled
    assert astar.total_time_s == pytest.approx(plain.total_time_s, abs=1e-6)


# ----------------------------------------------------------------------------
# 6. larger cross-validation (slow)
# ----------------------------------------------------------------------------


@pytest.mark.slow
def test_large_random_cross_validation():
    """cfp == cfp-astar (both potentials) <= bls, with the evaluator agreeing, on many mixed instances."""
    rng = random.Random(777)
    n_feasible = 0
    for i in range(80):
        size = rng.randint(5, 11)
        cfg = dict(
            rows=size,
            cols=rng.randint(max(5, size - 2), size),
            seed=rng.randint(0, 100000),
            initial_soc=round(rng.uniform(0.12, 0.5), 3),
            n_stations=rng.randint(3, 14),
            spacing_m=rng.choice((8000.0, 12000.0)),
            vehicle=rng.choice(("compact50", "sedan75", "city27", "suv84_800v", "togg_t10x_lr", "van75")),
            time_dependent_waits=rng.random() < 0.5,
            queue_aware=rng.random() < 0.8,
            speed_factors=(1.0, 0.85) if rng.random() < 0.2 else (1.0,),
            reserve_soc=rng.choice((0.10, 0.10, 0.05, 0.15)),
            max_charge_soc=rng.choice((1.0, 1.0, 0.9)),
        )
        P = _scenario(cfg).prepare()
        a = _run(P, "cfp-astar")
        b = _run(P, "cfp-astar", potential="basic")
        bls = _run(P, "bls-astar")
        assert a.feasible == b.feasible == bls.feasible, cfg
        if not a.feasible:
            continue
        n_feasible += 1
        for p in (a, b):
            _assert_consistent(p)
        assert a.total_time_s == pytest.approx(b.total_time_s, abs=1e-6), cfg
        assert a.total_time_s <= bls.total_time_s + 1e-6, cfg
        if a.stats.settled < 2500:
            c = _run(P, "cfp")
            _assert_consistent(c)
            assert c.total_time_s == pytest.approx(a.total_time_s, abs=1e-6), cfg
    assert n_feasible >= 50
