"""Performance regression guards for the exact planners (BLS / BLS-A*, CFP / CFP-A*).

The speed-ups of ``algorithms/labels.py`` and ``algorithms/cfp.py`` are meant
to be *result-neutral*: they only avoid work on labels that the plain
algorithms would discard anyway.  These tests check that claim directly where
it is cheap to do so (same settled labels and route with and without the
devices) and keep the label counts of a few fixed instances under budgets
with a wide margin, so that they stay robust on slow machines while still
catching a lost pruning rule (which typically multiplies the counts).  Time
limits are generous (several times the expected runtime).
"""

from __future__ import annotations

import math
import random
import time
from itertools import pairwise

import pytest

from evroute.algorithms import get_planner
from evroute.algorithms.cfp import _dominated_in, _Envelope, evaluate_function, label_function
from evroute.algorithms.labels import (
    AStarPotential,
    _label_search,
    _single_level_bound,
    single_level_options,
)
from evroute.generators import grid_scenario
from evroute.network import NetworkBuilder
from evroute.queueing import WaitProfile
from evroute.scenario import Conditions, Preferences, Query, Scenario, Station
from evroute.vehicle import get_vehicle

ECO = (1.0, 0.9, 0.8)


def _run(P, name, **opts):
    return get_planner(name, **opts).plan(P)


def _search(P, pot, **kw):
    return _label_search(
        P,
        source=P.origin,
        e_start=P.e_init,
        objective=P.objective,
        goal=P.destination,
        e_goal=P.e_dest_min,
        potential=pot,
        **kw,
    )


# ----------------------------------------------------------------------------
# 1. the devices do not change the settled labels or the result
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("objective,td", [("time", True), ("cost", False), ("cost", True)])
def test_upper_bound_from_one_speed_level_leaves_the_search_unchanged(objective, td):
    """Same settled labels, route and value with and without the single-level bound.

    (Cost objective with time-dependent waits -- 3-D dominance on money + wear, time and
    energy: only with the tight potential.  In Dijkstra order the exact 3-D label sets of
    this eco-speed grid exceed the 5 million label budget, with or without the bound.)"""
    sc = grid_scenario(9, 9, seed=21, initial_soc=0.3, speed_factors=ECO, objective=objective,
                       time_dependent_waits=td)
    P = sc.prepare()
    pots = [AStarPotential.tight(P)] + ([AStarPotential.zero(P)] if objective == "time" or not td else [])
    for pot in pots:
        ub, _ = _single_level_bound(P, pot, targets="grid+breaks", max_labels=1_000_000)
        assert math.isfinite(ub)
        plain = _search(P, pot)
        bounded = _search(P, pot, upper_bound=ub)
        assert plain.status == bounded.status == "goal"
        assert bounded.settled == plain.settled
        assert bounded.primary[bounded.final] == plain.primary[plain.final]
        assert bounded.reconstruct(bounded.final) == plain.reconstruct(plain.final)
        assert bounded.pushes <= plain.pushes
        # the bound is the value of a real route: never below the optimum
        assert ub >= plain.primary[plain.final] * (1 - 1e-12)


def test_single_level_options_pick_one_level_per_edge():
    P = grid_scenario(6, 6, seed=2, speed_factors=ECO).prepare()
    opts = single_level_options(P, 0.0)
    assert len(opts) == P.net.num_edges
    for one, full in zip(opts, P.edge_options, strict=True):
        assert len(one) == 1 and one[0] in full
        assert one[0][1] == min(o[1] for o in full)  # rate 0: the fastest level


def _set_dominated_reference(labels, ts, bs, eps=1e-9):
    """CFP set dominance from its definition: a settled *constant* label dominates the new
    label, or the upper envelope of the settled non-constant labels is ``>= B - eps`` on
    ``[t_feas, inf)`` -- probed at every breakpoint, at every crossing of a settled label
    with ``B``, densely in between and far beyond (a tolerance of 1e-7 on the probes)."""
    for t1, b1 in labels:
        if len(t1) == 1 and t1[0] <= ts[0] + eps and b1[0] >= bs[-1] - eps:
            return True
    fun = [(t1, b1) for t1, b1 in labels if len(t1) > 1]
    if not fun or min(t1[0] for t1, _ in fun) > ts[0] + eps:
        return False
    start = min(t1[0] for t1, _ in fun)

    def env(t):
        t = max(t, start)
        return max(evaluate_function(t1, b1, t) for t1, b1 in fun if t1[0] <= t)

    pts = sorted({t for t1, _ in fun for t in t1} | set(ts))
    pts = [t for t in pts if t >= ts[0]]
    probe = set(pts) | {pts[-1] + 1e4}
    for a, b in pairwise(pts):
        probe.update((a + (b - a) * f for f in (1e-7, 0.25, 0.5, 0.75, 1 - 1e-7)))
    for t1, b1 in fun:
        knots = sorted(set(t1) | set(ts))
        for a, b in pairwise(knots):
            d1 = evaluate_function(t1, b1, a) - evaluate_function(ts, bs, a)
            d2 = evaluate_function(t1, b1, b) - evaluate_function(ts, bs, b)
            if d1 * d2 < 0.0:
                probe.add(a + (b - a) * d1 / (d1 - d2))
    return all(env(t) >= evaluate_function(ts, bs, t) - 1e-7 for t in probe if t >= ts[0])


def test_cfp_envelope_answers_like_the_definition():
    """``_Envelope`` (constant staircase + upper envelope of the other labels) gives the
    answer of the set-dominance definition, is implied by single-label dominance (the rule
    of earlier versions) and stores exactly the upper envelope."""
    P = grid_scenario(6, 6, seed=4, initial_soc=0.3).prepare()
    cf = P.cf[0]
    cap = P.e_charge_cap
    rng = random.Random(11)
    n_dom = n_set_only = 0
    for _ in range(60):
        labels: list = []
        ref: list = []
        env = _Envelope()
        for _k in range(rng.randint(1, 40)):
            if rng.random() < 0.4:  # a constant label
                ts, bs = [round(rng.uniform(0, 500), rng.choice((0, 1, 3)))], [rng.uniform(5, 40)]
            else:
                b = rng.uniform(2, 30)
                ts, bs = label_function(cf, cap, rng.uniform(0, 400), cf.time_at(b), b,
                                        rng.uniform(2, 35), rng.uniform(-3, 15), rng.uniform(10, 60))
            got, _ = env.dominated(ts, bs, 1e-9)
            assert got == _set_dominated_reference(labels, ts, bs)
            single, _ = _dominated_in(ref, ts, bs, bs[-1], 1e-9)
            assert got or not single  # single-label dominance implies set dominance
            n_dom += got
            n_set_only += got and not single
            if not got or rng.random() < 0.3:
                labels.append((ts, bs))
                ref.append((ts[0], bs[-1], ts, bs))
                env.add(ts, bs)
                assert env.dominated(ts, bs, 1e-9)[0]
                T, V = env.T, env.V
                assert all(a <= b for a, b in pairwise(T)) and all(a <= b for a, b in pairwise(V))
                fun = [(t1, b1) for t1, b1 in labels if len(t1) > 1]
                for t in (rng.uniform(T[0], T[-1] + 50.0) for _ in range(10) if T):
                    want = max(evaluate_function(t1, b1, t) for t1, b1 in fun if t1[0] <= t)
                    assert env.right(t) == pytest.approx(want, abs=1e-9)
    assert n_dom > 300 and n_set_only > 5  # the set rule prunes labels no single label dominates


def test_cfp_with_the_cost_objective_plans_exactly_like_with_time():
    """CFP always minimises time; its potential and energy cap must not depend on the objective."""
    sc = grid_scenario(9, 9, seed=13, initial_soc=0.25)
    for name in ("cfp", "cfp-astar"):
        a = _run(sc.prepare(), name)
        b = _run(sc.with_preferences(objective="cost").prepare(), name)
        assert a.feasible and b.feasible
        assert b.total_time_s == a.total_time_s
        assert b.stats.settled == a.stats.settled


# ----------------------------------------------------------------------------
# 2. soundness guard: cost objective with a shrinking queue (3-D dominance)
# ----------------------------------------------------------------------------


def test_cost_dominance_is_sound_when_a_queue_shrinks():
    """An earlier label that spent more money must not prune a later, cheaper one.

    Origin with a fast expensive and a slow cheap charger, then a station whose
    queue falls steeply in the morning: arriving later (after charging slowly
    and cheaply) loses nothing in the queue, so the cheap charger wins the
    generalised cost.  Comparing (cost, time, energy) would discard that label;
    the sound rule compares (money + wear, time, energy).  The Pareto planner,
    which keeps time and money apart, is the oracle.
    """
    b = NetworkBuilder()
    o, y, d = b.add_node(0, 0), b.add_node(97276.8, 0), b.add_node(267801.8, 0)
    b.add_road(o, y, road_class="motorway", length=97276.8)
    b.add_road(y, d, road_class="motorway", length=170525.0)
    stations = [
        Station(id=0, node=o, power_kw=250.0, price_per_kwh=1.1333, setup_s=60),
        Station(id=1, node=o, power_kw=22.0, kind="DC", price_per_kwh=0.1632, setup_s=60),
        Station(id=2, node=y, power_kw=50.0, price_per_kwh=0.5, setup_s=60),
    ]
    sc = Scenario(
        name="falling-queue", network=b.build(), stations=stations, vehicle=get_vehicle("compact50"),
        query=Query(o, d, 0.1553, 9 * 3600.0), conditions=Conditions(temperature_c=20.0),
        preferences=Preferences(objective="cost", value_of_time_per_h=40.0, time_dependent_waits=True,
                                charge_step_soc=0.1),
    )
    P = sc.prepare()
    P.wait_profiles[0] = WaitProfile.constant(0.0)
    P.wait_profiles[1] = WaitProfile.constant(0.0)
    peak_h, peak = 9.5884, 2120.7
    P.wait_profiles[2] = WaitProfile.from_hourly(
        [max(0.0, peak - 3420.0 * max(0.0, h + 0.5 - peak_h)) if h + 0.5 >= peak_h - 1 else 0.0 for h in range(24)]
    )
    front = _run(P, "pareto")
    assert front.feasible
    for name in ("bls", "bls-astar"):
        p = _run(P, name)
        assert p.feasible
        assert p.objective_value == pytest.approx(front.objective_value, rel=1e-9)
        assert [s.station for s in p.stops][0] == 1  # the slow, cheap charger at the origin


# ----------------------------------------------------------------------------
# 3. label budgets and time limits (generous)
# ----------------------------------------------------------------------------

# (scenario kwargs, planner, settled budget, push budget): about twice the measured
# counts.  Settled labels depend only on the potential and the dominance rules;
# pushes also on the generated sets and the upper bound (2-3 times more without).
BUDGETS = [
    (dict(rows=12, cols=12, seed=3, initial_soc=0.3, speed_factors=ECO), "bls-astar", 1_500, 1_800),
    (dict(rows=12, cols=12, seed=3, initial_soc=0.3, speed_factors=ECO), "cfp-astar", 2_200, 3_400),
    (dict(rows=12, cols=12, seed=3, initial_soc=0.3, speed_factors=ECO, objective="cost",
          time_dependent_waits=False), "bls-astar", 12_000, 14_000),
    (dict(rows=10, cols=10, seed=8, initial_soc=0.25, speed_factors=(1.0, 0.85), objective="cost"),
     "bls-astar", 11_000, 16_500),
    (dict(rows=9, cols=9, seed=21, initial_soc=0.3, speed_factors=ECO), "cfp", 15_000, 35_000),
    (dict(rows=14, cols=14, seed=5, initial_soc=0.35), "bls-astar", 300, 350),
]


@pytest.mark.parametrize("kwargs,name,settled,pushes", BUDGETS)
def test_settled_label_budgets(kwargs, name, settled, pushes):
    kw = dict(kwargs)
    sc = grid_scenario(kw.pop("rows"), kw.pop("cols"), **kw)
    p = _run(sc.prepare(), name)
    assert p.feasible
    assert p.stats.settled <= settled, (name, p.stats.settled)
    assert p.stats.pushes <= pushes, (name, p.stats.pushes)


def test_eco_speeds_on_the_turkey_preset_stay_fast():
    """The motivating hot spot: Karaman -> Istanbul with three speed levels."""
    from evroute.generators.presets import get_scenario

    one = get_scenario("turkey-karaman-istanbul").prepare()
    eco = get_scenario("turkey-karaman-istanbul", speed_factors=ECO).prepare()
    base = _run(one, "bls-astar")
    t0 = time.perf_counter()
    p = _run(eco, "bls-astar")
    dt = time.perf_counter() - t0
    assert base.feasible and p.feasible
    # more speed levels can only help, and here they do not
    assert p.objective_value <= base.objective_value + 1e-6
    assert p.stats.extra["upper_bound"] is not None
    assert p.stats.extra["upper_bound"] >= p.objective_value * (1 - 1e-9)
    assert p.stats.settled <= 400_000, p.stats.settled
    assert p.stats.pushes <= 1_000_000, p.stats.pushes  # ~2 million before the bound and the generated sets
    assert dt < 60.0, dt  # about 3 s on a laptop


# ----------------------------------------------------------------------------
# 4. CFP with several speed levels (set dominance, bound for the Dijkstra order)
# ----------------------------------------------------------------------------


def test_cfp_with_three_speed_levels_is_exact_and_fast():
    """Regression: with several speed levels CFP compared labels one by one and its label
    sets exploded (Karaman -> Mersin: 43 000 settled labels and 13 s for ``"cfp"``; on
    Karaman -> Istanbul it ran out of labels and reported no route).  With the set
    dominance of :class:`~evroute.algorithms.cfp._Envelope` both variants are exact and
    small: the optimum is never above the grid optimum of BLS-A*."""
    from evroute.generators.presets import get_scenario

    P = get_scenario("turkey-karaman-mersin", speed_factors=ECO).prepare()
    bls = _run(P, "bls-astar")
    assert bls.feasible
    for name, budget in (("cfp", 8_000), ("cfp-astar", 8_000)):
        t0 = time.perf_counter()
        p = _run(P, name)
        dt = time.perf_counter() - t0
        assert p.feasible and p.stats.extra["status"] == "optimal", (name, p.message)
        assert p.stats.extra["optimality"] == "proven"
        assert p.total_time_s <= bls.total_time_s + 1e-6
        assert p.internal_objective == pytest.approx(p.total_time_s, abs=1e-6)
        assert p.stats.settled <= budget, (name, p.stats.settled)
        assert dt < 10.0, (name, dt)  # about 0.1 s on a laptop


@pytest.mark.slow
def test_cfp_with_three_speed_levels_on_karaman_istanbul():
    """The motivating instance of the regression above (about 15 / 20 s on a laptop)."""
    from evroute.generators.presets import get_scenario

    P = get_scenario("turkey-karaman-istanbul", speed_factors=ECO).prepare()
    bls = _run(P, "bls-astar")
    one = _run(get_scenario("turkey-karaman-istanbul").prepare(), "cfp-astar")
    for name in ("cfp-astar", "cfp"):
        t0 = time.perf_counter()
        p = _run(P, name)
        dt = time.perf_counter() - t0
        assert p.feasible and p.stats.extra["status"] == "optimal", (name, p.message)
        assert p.total_time_s <= bls.total_time_s + 1e-6
        assert p.total_time_s <= one.total_time_s + 1e-6  # more speed levels never hurt
        assert p.stats.settled <= 500_000, (name, p.stats.settled)
        assert dt < 120.0, (name, dt)
