"""Tests of the multi-criteria planner ``pareto`` (``algorithms/pareto.py``).

Correctness is established four ways:

* the independent evaluator replays every front member (feasibility and
  internal objective) and the members are mutually non-dominated;
* the extremes of the front equal the exact single-objective optima of BLS
  with the same charging targets (fastest member = BLS ``time``, cheapest
  generalised cost = BLS ``cost``), also with time-dependent waits;
* on tiny instances an exhaustive enumeration of walks and charging targets,
  written independently of the search code, gives exactly the same front;
* the completion bounds used for target pruning are checked to be
  admissible along every front route and consistent on random transitions.
"""

from __future__ import annotations

import functools
import json
import math
import random

import pytest

from evroute.algorithms import get_planner, plan
from evroute.algorithms.labels import charging_targets
from evroute.algorithms.pareto import (
    FRONT_RTOL,
    CompletionBounds,
    ParetoPlanner,
    _Archive3,
    _pareto_search,
    criteria_vector,
    dominates,
    pareto_front,
)
from evroute.evaluate import check_objective
from evroute.generators import grid_scenario
from evroute.network import NetworkBuilder
from evroute.scenario import Conditions, Preferences, Query, Scenario, Station
from evroute.vehicle import get_vehicle

EPS = 1e-9

# ----------------------------------------------------------------------------
# instances and helpers
# ----------------------------------------------------------------------------

#: (rows, cols, seed, initial_soc, time_dependent_waits, speed_factors, n_stations, step)
CONFIGS = [
    (6, 6, 1, 0.25, True, (1.0,), 8, 0.1),
    (7, 6, 4, 0.2, False, (1.0,), 9, 0.1),
    (7, 7, 11, 0.3, True, (1.0,), 10, 0.05),
    (6, 7, 21, 0.18, True, (1.0, 0.85), 7, 0.1),
    (8, 6, 33, 0.22, False, (1.0,), 12, 0.1),
    (7, 7, 47, 0.15, True, (1.0,), 11, 0.1),
]


def _scenario(cfg, objective="cost"):
    rows, cols, seed, soc, td, sf, ns, step = cfg
    return grid_scenario(rows, cols, n_stations=ns, seed=seed, initial_soc=soc, objective=objective,
                         time_dependent_waits=td, speed_factors=sf, charge_step_soc=step)


@functools.lru_cache(maxsize=None)
def _solved(cfg, objective="cost", **opts):
    """(prepared scenario, pareto plan) -- cached, the planner is deterministic."""
    P = _scenario(cfg, objective).prepare()
    return P, get_planner("pareto", **opts).plan(P)


def _internal_front(p):
    return [tuple(x[:3]) for x in p.stats.extra["front"]]


def _covers(y, x, tol):
    """``y`` within the relative tolerances ``tol`` of ``x`` in every criterion."""
    return all(a <= b * (1.0 + r) + 1e-12 for a, b, r in zip(y, x, tol))


def _assert_member_consistent(P, m):
    assert m.feasible, m.message
    assert check_objective(m)
    assert m.internal_objective is not None
    if P.objective == "time":
        # time uses the evaluator's arithmetic: identical floats
        assert m.objective_value == m.internal_objective
    else:
        assert math.isclose(m.objective_value, m.internal_objective, rel_tol=1e-12, abs_tol=1e-12)


# ----------------------------------------------------------------------------
# 1. front members are feasible, consistent and mutually non-dominated
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("criteria", [("time", "money", "wear"), ("time", "money")])
def test_front_feasible_consistent_and_nondominated(criteria):
    sizes = []
    for cfg in CONFIGS:
        for objective in ("time", "cost"):
            P, p = _solved(cfg, objective, criteria=criteria)
            assert p.feasible, p.message
            members = p.alternatives
            assert p.stats.extra["front_size"] == len(members) >= 1
            assert p.stats.extra["complete"] and p.stats.extra["criteria"] == list(criteria)
            sizes.append(len(members))
            for m in members:
                _assert_member_consistent(P, m)
                assert m.algorithm == "pareto"
                for st in m.stops:  # every stop target lies on the target set
                    assert any(abs(st.energy_out_kwh - y) < 1e-9 for y in charging_targets(P, st.station))
            # sorted by time, mutually non-dominated (evaluated and internal vectors)
            times = [m.total_time_s for m in members]
            assert times == sorted(times)
            vecs = [criteria_vector(m, criteria) for m in members]
            internal = [v[: len(criteria)] for v in _internal_front(p)]
            for group in (vecs, internal):
                for i, a in enumerate(group):
                    for j, b in enumerate(group):
                        assert i == j or not dominates(a, b), (cfg, a, b)
            # the returned plan is the best member for the scenario objective,
            # a distinct object (it is not its own alternative)
            best = min(members, key=lambda m: m.objective_value)
            assert p.objective_value == best.objective_value
            assert p.decision == members[p.stats.extra["selected"]].decision
            assert all(m is not p for m in members)
            assert p.internal_objective == members[p.stats.extra["selected"]].internal_objective
            _assert_member_consistent(P, p)
    assert max(sizes) >= 8  # real trade-offs, not single-point fronts


# ----------------------------------------------------------------------------
# 2./3. the extremes of the front equal the BLS optima
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("cfg", CONFIGS)
def test_fastest_member_equals_bls_time(cfg):
    P, p = _solved(cfg, "time")
    ref = get_planner("bls", targets="grid+breaks").plan(P)
    assert ref.feasible and p.feasible
    fastest = min(m.total_time_s for m in p.alternatives)
    assert math.isclose(fastest, ref.total_time_s, rel_tol=1e-9)
    # objective "time": the returned plan is the fastest member
    assert math.isclose(p.objective_value, ref.objective_value, rel_tol=1e-9)
    assert p.alternatives[0].total_time_s == fastest


@pytest.mark.parametrize("cfg", CONFIGS)
def test_min_generalised_cost_member_equals_bls_cost(cfg):
    P, p = _solved(cfg, "cost")
    ref = get_planner("bls", targets="grid+breaks").plan(P)
    assert ref.feasible and p.feasible
    cheapest = min(m.generalized_cost for m in p.alternatives)
    assert math.isclose(cheapest, ref.generalized_cost, rel_tol=1e-9)
    assert math.isclose(p.objective_value, ref.objective_value, rel_tol=1e-9)
    assert math.isclose(p.internal_objective, ref.internal_objective, rel_tol=1e-9)


def _random_configs(n=40):
    rng = random.Random(20261024)
    out = []
    for i in range(n):
        two_speeds = i % 4 == 1
        size = rng.randint(5, 7 if two_speeds else 9)
        out.append((size, rng.randint(max(5, size - 2), size), rng.randint(0, 10_000),
                    round(rng.uniform(0.15, 0.4), 3), i % 3 != 0, (1.0, 0.85) if two_speeds else (1.0,),
                    rng.randint(4, 12), 0.1 if two_speeds else rng.choice((0.05, 0.1))))
    return out


def test_front_extremes_match_bls_on_random_instances():
    """Cross-validation on random grids: fastest member = BLS (time) and
    cheapest generalised cost = BLS (cost); every member replays exactly."""
    feasible = multi = 0
    for cfg in _random_configs():
        P_time = _scenario(cfg, "time").prepare()
        P_cost = _scenario(cfg, "cost").prepare()
        p = get_planner("pareto").plan(P_cost)
        bt = get_planner("bls").plan(P_time)
        bc = get_planner("bls").plan(P_cost)
        assert p.feasible == bt.feasible == bc.feasible, (cfg, p.message)
        if not p.feasible:
            continue
        feasible += 1
        multi += len(p.alternatives) >= 3
        assert math.isclose(p.alternatives[0].total_time_s, bt.total_time_s, rel_tol=1e-9), cfg
        assert math.isclose(p.generalized_cost, bc.generalized_cost, rel_tol=1e-9), cfg
        for m in p.alternatives:
            _assert_member_consistent(P_cost, m)
    assert feasible >= 30
    assert multi >= 20


def test_two_criteria_front_is_projection_of_three_criteria_front():
    """The (time, money) front equals the non-dominated projection of the
    3-criteria front, and its extremes still match BLS for time."""
    for cfg in CONFIGS[:4]:
        P, p3 = _solved(cfg, "cost")
        _, p2 = _solved(cfg, "cost", criteria=("time", "money"))
        proj = [v[:2] for v in _internal_front(p3)]
        nd = [a for a in proj if not any(dominates(b, a) for b in proj)]
        f2 = [v[:2] for v in _internal_front(p2)]
        assert len(f2) <= len(p3.alternatives)
        tol = (1e-12, FRONT_RTOL)
        for a in nd:
            assert any(_covers(b, a, tol) for b in f2)
        for b in f2:
            assert any(_covers(a, b, tol) for a in nd)
        ref = get_planner("bls").plan(_scenario(cfg, "time").prepare())
        assert math.isclose(p2.alternatives[0].total_time_s, ref.total_time_s, rel_tol=1e-9)


# ----------------------------------------------------------------------------
# 4. epsilon dominance
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("eps", [0.01, 0.05, {"money": 0.02, "wear": 0.1}])
def test_epsilon_front_is_smaller_and_covers_the_exact_front(eps):
    shrunk = 0
    for cfg in CONFIGS:
        P, exact = _solved(cfg, "cost")
        approx = get_planner("pareto", epsilon=eps).plan(P)
        assert approx.feasible
        assert len(approx.alternatives) <= len(exact.alternatives)
        shrunk += len(approx.alternatives) < len(exact.alternatives)
        e = eps if isinstance(eps, dict) else {"money": eps, "wear": eps}
        tol = (0.0, e["money"] + 2 * FRONT_RTOL, e["wear"] + 2 * FRONT_RTOL)
        approx_vecs = [criteria_vector(m) for m in approx.alternatives]
        for x in (criteria_vector(m) for m in exact.alternatives):
            assert any(_covers(y, x, tol) for y in approx_vecs), (cfg, eps, x)
        for m in approx.alternatives:
            _assert_member_consistent(P, m)
        # time is the lexicographic key: the fastest member is exact
        assert approx.alternatives[0].total_time_s == exact.alternatives[0].total_time_s
        assert approx.stats.extra["epsilon"]["money"] == e["money"]
    assert shrunk >= 3


def test_epsilon_on_labels_is_a_fast_feasible_approximation():
    for cfg in CONFIGS[:4]:
        P, exact = _solved(cfg, "cost")
        approx = get_planner("pareto", epsilon=0.05, epsilon_scope="labels").plan(P)
        assert approx.feasible
        assert approx.stats.extra["labels"] <= exact.stats.extra["labels"]
        assert len(approx.alternatives) <= len(exact.alternatives)
        vecs = [criteria_vector(m) for m in approx.alternatives]
        for i, a in enumerate(vecs):
            _assert_member_consistent(P, approx.alternatives[i])
            assert not any(dominates(b, a) for b in vecs)
        # node pruning never trades time: the fastest member stays exact
        assert approx.alternatives[0].total_time_s == exact.alternatives[0].total_time_s
        # the cost optimum is approximated within the (compounded) tolerance
        assert approx.generalized_cost <= exact.generalized_cost * 1.05**4


# ----------------------------------------------------------------------------
# 5. time-dependent waits
# ----------------------------------------------------------------------------


def test_time_dependent_waits():
    """Busy stations around the morning peak: waits change by many minutes
    within the trip.  The front still matches BLS, every member replays
    exactly, and the result differs from the static-wait snapshot."""
    differs = 0
    for seed in (3, 8, 15):
        base = grid_scenario(7, 7, seed=seed, initial_soc=0.2, n_stations=9, charge_step_soc=0.1)
        busy = [Station(**{**st.to_dict(), "daily_sessions": 40.0 if st.kind == "DC" else 9.0,
                           "plugs": 2})
                for st in base.stations]
        sc = base.with_(stations=busy).with_query(departure_s=6.5 * 3600.0)
        fronts = {}
        for td in (True, False):
            for objective in ("time", "cost"):
                s2 = sc.with_preferences(objective=objective, time_dependent_waits=td, charge_step_soc=0.1)
                P = s2.prepare()
                assert P.time_dependent == td
                p = get_planner("pareto").plan(P)
                ref = get_planner("bls").plan(P)
                assert p.feasible and ref.feasible
                assert math.isclose(p.objective_value, ref.objective_value, rel_tol=1e-9)
                for m in p.alternatives:
                    _assert_member_consistent(P, m)
                    for st in m.stops:
                        assert st.wait_s == P.wait_s(st.station, st.arrival_s)
                fronts[td] = _internal_front(p)
        differs += fronts[True] != fronts[False]
    assert differs >= 1  # the waits really depend on the arrival time


# ----------------------------------------------------------------------------
# 6. cheap slow AC versus expensive fast DC
# ----------------------------------------------------------------------------


def _ac_dc_line(same_node: bool):
    """Origin -- 60 km -- charging site(s) -- 60 km -- destination; the car
    cannot finish without charging.  A cheap 11 kW AC and an expensive
    150 kW DC charger, either at the same node or on two parallel branches."""
    b = NetworkBuilder()
    o = b.add_node(0, 0, name="O")
    d = b.add_node(120_000, 0, name="D")
    if same_node:
        s = b.add_node(60_000, 0, name="S")
        b.add_road(o, s, road_class="trunk")
        b.add_road(s, d, road_class="trunk")
        nodes = (s, s)
    else:
        a = b.add_node(60_000, 3_000, name="A")
        c = b.add_node(60_000, -3_000, name="C")
        for x in (a, c):
            b.add_road(o, x, road_class="trunk")
            b.add_road(x, d, road_class="trunk")
        nodes = (a, c)
    stations = [
        Station(id=0, node=nodes[0], power_kw=11.0, kind="AC", price_per_kwh=0.20, name="AC cheap",
                daily_sessions=0.0, setup_s=120.0),
        Station(id=1, node=nodes[1], power_kw=150.0, kind="DC", price_per_kwh=0.79, name="DC fast",
                daily_sessions=0.0, setup_s=120.0),
    ]
    return Scenario("ac-vs-dc", b.build(), stations, get_vehicle("compact50"),
                    Query(o, d, initial_soc=0.32), Conditions(temperature_c=20.0),
                    Preferences(objective="cost", charge_step_soc=0.05))


@pytest.mark.parametrize("same_node", [True, False])
def test_cheap_slow_ac_and_expensive_fast_dc_give_a_trade_off(same_node):
    sc = _ac_dc_line(same_node)
    P = sc.prepare()
    p = plan(sc, "pareto")
    assert p.feasible
    front = p.alternatives
    assert len(front) >= 2
    fastest, cheapest = front[0], min(front, key=lambda m: m.money)
    assert fastest.stops and {st.station_kind for st in fastest.stops} == {"DC"}
    assert cheapest.stops and {st.station_kind for st in cheapest.stops} == {"AC"}
    assert cheapest.total_time_s > fastest.total_time_s + 1800.0
    assert cheapest.money < fastest.money
    # fast charging at a high C-rate costs more battery health per kWh
    assert (fastest.soh_charge_pct / fastest.energy_charged_kwh
            > cheapest.soh_charge_pct / cheapest.energy_charged_kwh)
    for m in front:
        _assert_member_consistent(P, m)
    # extremes agree with BLS on this hand-built instance too
    assert math.isclose(fastest.total_time_s, plan(sc.with_preferences(objective="time", charge_step_soc=0.05),
                                                     "bls").total_time_s, rel_tol=1e-9)
    assert math.isclose(min(m.generalized_cost for m in front), plan(sc, "bls").generalized_cost, rel_tol=1e-9)
    # without the wear criterion the front cannot be larger
    assert len(pareto_front(P, criteria="time,money")) <= len(front)


# ----------------------------------------------------------------------------
# 7. brute-force oracle on tiny instances
# ----------------------------------------------------------------------------


def _oracle_targets(P, s, mode):
    """Target set re-derived from the scenario (independent of the planner code)."""
    cap = P.e_charge_cap
    vals = [min(y, cap) for y in P.target_grid if y <= cap + 1e-9]
    if mode == "grid+breaks":
        vals += [b for b in P.cf[s].e_breaks[1:-1] if b <= cap]
    out = []
    for y in sorted(vals):
        if y > 0 and not (out and y - out[-1] <= 1e-9):
            out.append(y)
    return out


def brute_force_front(P, mode, max_len=8, max_visits=2, ncrit=3):
    """Exhaustive enumeration of walks (each node at most ``max_visits`` times,
    at most ``max_len`` edges, every speed level) and of one optional charging
    stop per visit of a station node to any target.  A partial route is cut
    only if a complete route found so far is no worse in every criterion
    (then every completion is dominated, since time strictly increases)."""
    net = P.net
    tg = {s: _oracle_targets(P, s, mode) for s in range(len(P.stations))}
    front: list[tuple[float, ...]] = []
    visits = [0] * net.num_nodes
    visits[P.origin] = 1

    def weakly_dominated(vec):
        return any(all(a <= b for a, b in zip(f, vec)) for f in front)

    def rec(v, t, m, w, e, n_edges, charged_here):
        vec = (t, m, w)[:ncrit]
        if weakly_dominated(vec):
            return
        if v == P.destination and e >= P.e_dest_min - EPS:
            front[:] = [f for f in front if not all(a <= b for a, b in zip(vec, f))]
            front.append(vec)
            return
        if not charged_here:
            for s in P.station_at.get(v, []):
                f = P.cf[s]
                for y in tg[s]:
                    if y > e + EPS and y >= P.e_min - EPS:
                        wq = P.wait_s(s, t)
                        rec(v, t + (wq + P.setup_s[s] + f.charge_time(e, y)), m + f.money(e, y),
                            w + f.soh(e, y), y, n_edges, True)
        if e >= P.e_min - EPS and n_edges < max_len:
            for a in net.out_edges[v]:
                u = net.head[a]
                if visits[u] >= max_visits:
                    continue
                for _k, tau, c, _dc in P.edge_options[a]:
                    ne = min(e - c, P.capacity)
                    if ne < P.e_min - EPS:
                        continue
                    visits[u] += 1
                    rec(u, t + tau, m, w + P.drive_soh_per_kwh * max(c, 0.0), ne, n_edges + 1, False)
                    visits[u] -= 1

    rec(P.origin, 0.0, 0.0, 0.0, P.e_init, 0, False)
    return sorted(front)


def _tiny(seed):
    rows, cols = (3, 3) if seed % 2 == 0 else (3, 4)
    sc = grid_scenario(rows, cols, spacing_m=25_000.0, n_stations=3, seed=seed, initial_soc=0.25,
                       charge_step_soc=0.2, speed_factors=(1.0, 0.85) if seed % 4 == 3 else (1.0,),
                       time_dependent_waits=seed % 5 != 2)
    st = list(sc.stations)
    if seed % 3 == 1:  # a cheap AC charger next to a DC one: several stations on one node
        st.append(Station(id=len(st), node=st[0].node, power_kw=11.0, kind="AC", price_per_kwh=0.15,
                          daily_sessions=2.0, demand_profile="urban", setup_s=60.0))
    if seed % 3 == 2:  # a station at the origin
        st.append(Station(id=len(st), node=sc.query.origin, power_kw=50.0, kind="DC", price_per_kwh=0.6))
    return sc.with_(stations=st)


@pytest.mark.parametrize("ncrit", [3, 2])
def test_front_equals_brute_force_on_tiny_instances(ncrit):
    criteria = ("time", "money", "wear")[:ncrit]
    nontrivial = 0
    for seed in range(10):
        mode = "grid" if seed % 3 == 0 or seed % 4 == 3 else "grid+breaks"
        P = _tiny(seed).prepare()
        oracle = brute_force_front(P, mode, ncrit=ncrit)
        p = get_planner("pareto", targets=mode, criteria=criteria).plan(P)
        if not oracle:
            assert not p.feasible
            continue
        assert p.feasible, p.message
        # the planner's routes lie inside the enumerated space ...
        for m in p.alternatives:
            assert len(m.edges) <= 8
            assert max(m.nodes.count(v) for v in set(m.nodes)) <= 2
        # ... so both fronts must coincide
        got = sorted(v[:ncrit] for v in _internal_front(p))
        assert len(got) == len(oracle), (seed, got, oracle)
        for a, b in zip(got, oracle):
            assert all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-15) for x, y in zip(a, b)), (seed, a, b)
        nontrivial += len(oracle) >= 3
    assert nontrivial >= 3


# ----------------------------------------------------------------------------
# 8. completion bounds: admissible and consistent
# ----------------------------------------------------------------------------


def test_completion_bounds_are_admissible_along_front_routes():
    for cfg in CONFIGS[:4]:
        P, p = _solved(cfg, "cost")
        B = CompletionBounds(P)
        assert B(P.destination, P.e_dest_min) == (0.0, 0.0, 0.0)
        for m in p.alternatives:
            final = criteria_vector(m)
            consumed = 0.0
            for i, v in enumerate(m.nodes):
                if i:
                    c = P.edge_energy[m.levels[i - 1]][m.edges[i - 1]]
                    consumed += max(c, 0.0)
                before = [st for st in m.stops if st.position < i]
                upto = [st for st in m.stops if st.position <= i]
                for t, e, stops in ((m.node_arrive_s[i], m.node_energy_arrive[i], before),
                                    (m.node_depart_s[i], m.node_energy_depart[i], upto)):
                    money = sum(st.money for st in stops)
                    wear = P.drive_soh_per_kwh * consumed + sum(st.soh_pct for st in stops)
                    h = B(v, e)
                    opt = (t + h[0], money + h[1], wear + h[2])
                    for o, f in zip(opt, final):
                        assert o <= f * (1 + 1e-9) + 1e-12, (cfg, i, opt, final)


def test_completion_bounds_are_consistent_and_monotone():
    rng = random.Random(5)
    sc = grid_scenario(8, 8, seed=9, initial_soc=0.3, speed_factors=(1.0, 0.85), time_dependent_waits=True)
    P = sc.prepare()
    B = CompletionBounds(P)
    for _ in range(2000):
        v = rng.randrange(P.net.num_nodes)
        x = rng.uniform(P.e_min, P.capacity)
        h = B(v, x)
        assert all(a <= b + 1e-12 for a, b in zip(B(v, x + rng.uniform(0, 5)), h))  # non-increasing in e
        for a in P.net.out_edges[v]:
            u = P.net.head[a]
            for _k, tau, c, _dc in P.edge_options[a]:
                x2 = min(x - c, P.capacity)
                hu = B(u, x2)
                cost = (tau, 0.0, P.drive_soh_per_kwh * max(c, 0.0))
                for k in range(3):
                    assert h[k] <= cost[k] + hu[k] + 1e-9
    for s, st in enumerate(P.stations):
        f = P.cf[s]
        for _ in range(50):
            x = rng.uniform(P.e_min, P.e_charge_cap - 1.0)
            y = rng.uniform(x, P.e_charge_cap)
            t = rng.uniform(0, 30000)
            cost = (P.wait_s(s, t) + P.setup_s[s] + f.charge_time(x, y), f.money(x, y), f.soh(x, y))
            hx, hy = B(st.node, x), B(st.node, y)
            for k in range(3):
                assert hx[k] <= cost[k] + hy[k] + 1e-9


# ----------------------------------------------------------------------------
# 9. search internals
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("n_points", [40, 700])
def test_archive3_matches_naive_scan(n_points):
    """Orthant query of the 3-D archive (scan mode and binned Fenwick mode)."""
    rng = random.Random(n_points)
    cap = 50.0
    arch = _Archive3(256 / cap)
    pts = []
    shared = [rng.uniform(5, cap) for _ in range(12)]  # charging targets: exact energy ties
    for _ in range(n_points):
        e = rng.choice(shared) if rng.random() < 0.5 else rng.uniform(0.0, cap)
        p = (round(rng.uniform(0, 20), 1), round(rng.uniform(0, 0.02), 4), e)
        arch.add(*p)
        pts.append(p)
    queries = [(rng.uniform(0, 22), rng.uniform(0, 0.022), rng.choice(shared) if rng.random() < 0.3
                else rng.uniform(0, cap)) for _ in range(3000)]
    queries += [p for p in pts[:200]]  # exact hits
    hits = 0
    for m, w, e in queries:
        naive = any(m2 <= m and w2 <= w and e2 >= e for m2, w2, e2 in pts)
        assert arch.dominated(m, w, e) == naive, (m, w, e)
        hits += naive
    assert 0 < hits < len(queries)


def test_goal_directed_and_martins_order_give_the_same_front():
    for cfg in (CONFIGS[0], CONFIGS[1], CONFIGS[2], CONFIGS[4], CONFIGS[5]):
        P, _ = _solved(cfg, "cost")
        B = CompletionBounds(P)
        fronts, labels = [], []
        for gd in (True, False):
            res = _pareto_search(P, B, goal_directed=gd)
            assert res.status == "exhausted"
            fronts.append(sorted((res.time[i], res.money[i], res.wear[i]) for i in res.front))
            labels.append(len(res.node))
        assert fronts[0] == fronts[1]
        assert labels[0] <= labels[1]
    P, _ = _solved(CONFIGS[0], "cost")
    p = get_planner("pareto", goal_directed=False).plan(P)
    assert p.feasible and p.stats.extra["goal_directed"] is False


# ----------------------------------------------------------------------------
# 10. API, limits, infeasibility, options
# ----------------------------------------------------------------------------


def test_registry_helper_and_serialisation():
    cfg = CONFIGS[0]
    P, p = _solved(cfg, "cost")
    info = ParetoPlanner.info()
    assert info["name"] == "pareto" and info["exact"] == "grid" and info["objectives"] == ["time", "cost"]
    assert ParetoPlanner.title == "Pareto label setting"
    front = pareto_front(P)
    assert [m.decision for m in front] == [m.decision for m in p.alternatives]
    sc = _scenario(cfg)
    q = plan(sc, "pareto", trace=True)
    assert q.algorithm == "pareto" and q.stats.algorithm == "pareto"
    assert q.trace is not None and len(q.trace.nodes) == len(q.trace.keys) > 5
    d = q.to_dict()
    assert len(d["alternatives"]) == len(q.alternatives)
    json.dumps(d)  # the viewer contract is plain JSON


def test_origin_station_below_reserve_and_origin_equals_destination():
    sc = grid_scenario(6, 6, seed=5, initial_soc=0.04, charge_step_soc=0.1)
    sc = sc.with_(stations=list(sc.stations) + [Station(id=99, node=sc.query.origin, power_kw=100.0)])
    P = sc.prepare()
    assert P.e_init < P.e_min
    p = get_planner("pareto").plan(P)
    assert p.feasible
    for m in p.alternatives:
        _assert_member_consistent(P, m)
        assert m.stops[0].position == 0 and m.stops[0].energy_out_kwh >= P.e_min
    same = grid_scenario(5, 5, seed=1, initial_soc=0.6).with_query(destination=0)
    q = plan(same, "pareto")
    assert q.feasible and q.edges == [] and len(q.alternatives) == 1 and q.total_time_s == 0.0


def test_infeasible_instances_and_label_limit():
    p = plan(grid_scenario(8, 8, seed=4, n_stations=0, initial_soc=0.13), "pareto")
    assert not p.feasible and "no battery-feasible route" in p.message and p.alternatives == []
    p = plan(grid_scenario(5, 5, seed=1, initial_soc=0.05), "pareto")
    assert not p.feasible and "below the reserve" in p.message
    b = NetworkBuilder()
    a0, a1, a2 = b.add_node(0, 0), b.add_node(5000, 0), b.add_node(10000, 0)
    b.add_road(a0, a1)
    b.add_edge(a2, a1)
    cut = Scenario("cut", b.build(), [], get_vehicle("compact50"), Query(a0, a2, initial_soc=0.8))
    p = plan(cut, "pareto")
    assert not p.feasible and "not reachable" in p.message
    assert pareto_front(cut) == []

    sc = _scenario(CONFIGS[2])
    p = plan(sc, "pareto", max_labels=30)
    assert not p.feasible and "label limit" in p.message
    # a limit hit after the first member: the fastest part of the front is returned
    full = plan(sc, "pareto")
    first = full.alternatives[0]
    for frac in (0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65):
        part = plan(sc, "pareto", max_labels=int(frac * full.stats.extra["labels"]))
        if part.feasible:
            assert part.stats.extra["complete"] is False and part.stats.extra["status"] == "limit"
            assert part.alternatives[0].total_time_s == first.total_time_s
            assert len(part.alternatives) <= len(full.alternatives)
            break
    else:  # pragma: no cover - depends on the instance
        pytest.fail("no partial front found")


@pytest.mark.parametrize("opts", [
    {"criteria": ("time", "wear")},
    {"criteria": "money"},
    {"targets": "everything"},
    {"epsilon": -0.1},
    {"epsilon": (0.1, 0.2)},
    {"epsilon": {"comfort": 0.1}},
    {"epsilon_scope": "nodes"},
    {"potential": "tight"},
])
def test_options_are_validated(opts):
    with pytest.raises(ValueError):
        plan(grid_scenario(5, 5, seed=1), "pareto", **opts)


def test_epsilon_option_forms():
    P, _ = _solved(CONFIGS[0], "cost")
    a = get_planner("pareto", epsilon=0.02).plan(P)
    b = get_planner("pareto", epsilon=(0.0, 0.02, 0.02)).plan(P)
    c = get_planner("pareto", epsilon={"money": 0.02, "wear": 0.02}).plan(P)
    assert _internal_front(a) == _internal_front(b) == _internal_front(c)
    assert a.stats.extra["epsilon"] == {"time": 0.02, "money": 0.02, "wear": 0.02}
