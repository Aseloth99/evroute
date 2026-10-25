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

import pytest

from evroute.algorithms import get_planner
from evroute.algorithms.cfp import _dominated_in, _SettledSet, dominates, label_function
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


@pytest.mark.parametrize("objective", ["time", "cost"])
def test_upper_bound_from_one_speed_level_leaves_the_search_unchanged(objective):
    """Same settled labels, route and value with and without the single-level bound."""
    sc = grid_scenario(9, 9, seed=21, initial_soc=0.3, speed_factors=ECO, objective=objective)
    P = sc.prepare()
    for pot in (AStarPotential.tight(P), AStarPotential.zero(P)):
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


def test_cfp_settled_set_answers_like_the_reference_scan():
    """``_SettledSet`` (staircases + sorted scan) == the plain pre-filtered scan."""
    P = grid_scenario(6, 6, seed=4, initial_soc=0.3).prepare()
    cf = P.cf[0]
    cap = P.e_charge_cap
    rng = random.Random(11)
    for _ in range(60):
        ref: list = []
        fast = _SettledSet()
        for _k in range(rng.randint(1, 60)):
            if rng.random() < 0.4:  # a constant label
                ts, bs = [round(rng.uniform(0, 500), rng.choice((0, 1, 3)))], [rng.uniform(5, 40)]
            else:
                b = rng.uniform(2, 30)
                ts, bs = label_function(cf, cap, rng.uniform(0, 400), cf.time_at(b), b,
                                        rng.uniform(2, 35), rng.uniform(-3, 15), rng.uniform(10, 60))
            bmax = bs[-1]
            got, _ = fast.dominated(ts, bs, bmax, 1e-9)
            want, _ = _dominated_in(ref, ts, bs, bmax, 1e-9)
            assert got == want
            if not want or rng.random() < 0.3:
                ref.append((ts[0], bmax, ts, bs))
                fast.add(ts, bs, bmax)
                assert fast.dominated(ts, bs, bmax, 1e-9)[0] and dominates(ts, bs, ts, bs)


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
