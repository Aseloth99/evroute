"""Tests of the exact label-setting planners BLS / BLS-A* (``algorithms/labels.py``).

Correctness is established three ways:

* the independent evaluator replays every plan (feasibility + objective);
* BLS (Dijkstra order) and BLS-A* (two different potentials) must agree on
  many random instances;
* on tiny instances an exhaustive brute force over walks and charging
  targets -- written independently of the search code -- must give the same
  optimum.
"""

from __future__ import annotations

import math
import random

import pytest

from evroute.algorithms import get_planner, plan
from evroute.algorithms.labels import (
    AStarPotential,
    LabelAStarPlanner,
    LabelSettingPlanner,
    charging_targets,
    completion_energy,
    fastest_feasible_tree,
)
from evroute.evaluate import check_objective, evaluate
from evroute.generators import grid_scenario
from evroute.plan import ChargeDecision, PlanDecision
from evroute.scenario import Station

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _run(P, name, **opts):
    return get_planner(name, **opts).plan(P)


def _assert_consistent(P, p, rel=1e-9):
    """Feasible plan whose evaluated objective equals the internal one."""
    assert p.feasible, p.message
    assert check_objective(p)
    assert p.internal_objective is not None
    assert math.isclose(p.objective_value, p.internal_objective, rel_tol=rel, abs_tol=1e-9)
    if P.objective == "time":
        # same arithmetic as the evaluator -> identical floats
        assert p.objective_value == p.internal_objective


def _random_configs(n=22):
    rng = random.Random(20261024)
    out = []
    for i in range(n):
        size = rng.randint(5, 12)
        cfg = dict(
            rows=size,
            cols=rng.randint(max(5, size - 2), size),
            seed=rng.randint(0, 10_000),
            initial_soc=round(rng.uniform(0.15, 0.5), 3),
            objective="time" if i % 2 == 0 else "cost",
            time_dependent_waits=(i % 3 != 0),
            speed_factors=(1.0, 0.85) if i % 4 == 1 else (1.0,),
            n_stations=rng.randint(4, 12),
            spacing_m=rng.choice((8000.0, 12000.0)),
        )
        out.append(cfg)
    return out


RANDOM_CONFIGS = _random_configs()


def _scenario(cfg):
    cfg = dict(cfg)
    return grid_scenario(
        cfg.pop("rows"),
        cfg.pop("cols"),
        spacing_m=cfg.pop("spacing_m"),
        n_stations=cfg.pop("n_stations"),
        seed=cfg.pop("seed"),
        initial_soc=cfg.pop("initial_soc"),
        **cfg,
    )


# ----------------------------------------------------------------------------
# 1. BLS == BLS-A* on random instances, evaluator agrees
# ----------------------------------------------------------------------------


def test_bls_and_astar_agree_on_random_grids():
    feasible = with_stops = bounded = 0
    for cfg in RANDOM_CONFIGS:
        P = _scenario(cfg).prepare()
        plans = [
            _run(P, "bls"),
            _run(P, "bls-astar"),
            _run(P, "bls-astar", potential="basic"),
        ]
        flags = {p.feasible for p in plans}
        assert len(flags) == 1, (cfg, [p.message for p in plans])
        if not plans[0].feasible:
            assert all(p.message for p in plans)
            continue
        feasible += 1
        with_stops += plans[0].num_stops > 0
        bounded += plans[1].stats.extra["pruned_by_bound"]
        ref = plans[0].internal_objective
        for p in plans:
            _assert_consistent(P, p)
            assert math.isclose(p.internal_objective, ref, rel_tol=1e-9), (cfg, p.algorithm)
            # every stop target lies on the target set
            for st in p.stops:
                assert any(abs(st.energy_out_kwh - y) < 1e-9 for y in charging_targets(P, st.station))
    assert feasible >= 15
    assert with_stops >= 8
    assert bounded > 0  # the upper-bound push pruning was exercised


def test_grid_breaks_never_worse_than_grid():
    for seed in (3, 8, 13):
        P = grid_scenario(8, 8, seed=seed, initial_soc=0.25).prepare()
        a = _run(P, "bls-astar", targets="grid")
        b = _run(P, "bls-astar", targets="grid+breaks")
        assert a.feasible == b.feasible
        if a.feasible:
            assert b.internal_objective <= a.internal_objective + 1e-9
            _assert_consistent(P, a)


# ----------------------------------------------------------------------------
# 2. brute-force oracle on tiny instances
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


def brute_force(P, mode="grid+breaks", max_len=9, max_visits=2):
    """Exhaustive search over walks (each node visited at most ``max_visits``
    times, at most ``max_len`` edges) and over one optional charging stop per
    visit of a station node, to any target of the target set.  Pruning only
    by the (non-negative, monotone) partial objective."""
    net = P.net
    time_obj = P.objective == "time"
    tg = {s: _oracle_targets(P, s, mode) for s in range(len(P.stations))}
    best = [math.inf, None]
    visits = [0] * net.num_nodes
    visits[P.origin] = 1
    eps = 1e-9

    def rec(v, t, g, e, edges, levels, charges, charged_here):
        prim = t if time_obj else g
        if prim >= best[0]:
            return
        if v == P.destination and e >= P.e_dest_min - eps:
            best[0] = prim
            best[1] = PlanDecision(list(edges), list(levels), [ChargeDecision(*c) for c in charges])
            return
        if not charged_here:
            for s in P.station_at.get(v, []):
                f = P.cf[s]
                for y in tg[s]:
                    if y > e + eps:
                        w = P.wait_s(s, t)
                        ch = f.charge_time(e, y)
                        rec(v, t + (w + P.setup_s[s] + ch), g + P.charge_cost(s, e, y, ch, w), y,
                            edges, levels, charges + [(len(edges), s, y)], True)
        if e >= P.e_min - eps and len(edges) < max_len:
            for a in net.out_edges[v]:
                w = net.head[a]
                if visits[w] >= max_visits:
                    continue
                for k, tau, c, dc in P.edge_options[a]:
                    ne = min(e - c, P.capacity)
                    if ne < P.e_min - eps:
                        continue
                    visits[w] += 1
                    edges.append(a)
                    levels.append(k)
                    rec(w, t + tau, g + dc, ne, edges, levels, charges, False)
                    edges.pop()
                    levels.pop()
                    visits[w] -= 1

    rec(P.origin, 0.0, 0.0, P.e_init, [], [], [], False)
    return best[0], best[1]


def _tiny(seed, rows, cols, objective, **kw):
    kw.setdefault("charge_step_soc", 0.1)
    kw.setdefault("initial_soc", 0.25)
    return grid_scenario(rows, cols, spacing_m=25_000.0, n_stations=kw.pop("n_stations", 3), seed=seed,
                         objective=objective, **kw)


def _compare_with_oracle(sc, mode):
    P = sc.prepare()
    value, decision = brute_force(P, mode)
    for name in ("bls", "bls-astar"):
        p = _run(P, name, targets=mode)
        if value == math.inf:
            assert not p.feasible
            continue
        _assert_consistent(P, p)
        assert math.isclose(p.internal_objective, value, rel_tol=1e-9), (name, p.internal_objective, value)
    if decision is not None:
        # the oracle's own optimum replays to the same value in the evaluator
        rp = evaluate(P, decision)
        assert rp.feasible
        assert math.isclose(rp.objective_value, value, rel_tol=1e-9)
    return value


@pytest.mark.parametrize("objective", ["time", "cost"])
def test_brute_force_oracle_tiny_grids(objective):
    solved = charged = 0
    for seed in range(14):
        rows, cols = (3, 3) if seed % 2 == 0 else (3, 4)
        mode = "grid" if seed % 3 == 0 else "grid+breaks"
        sc = _tiny(seed, rows, cols, objective,
                   speed_factors=(1.0, 0.85) if seed % 4 == 3 else (1.0,),
                   time_dependent_waits=seed % 5 != 2)
        value = _compare_with_oracle(sc, mode)
        if value < math.inf:
            solved += 1
            charged += bool(get_planner("bls-astar", targets=mode).plan(sc).num_stops)
    assert solved >= 10
    assert charged >= 8  # low initial SoC: charging is really exercised


@pytest.mark.parametrize("objective", ["time", "cost"])
def test_brute_force_oracle_stacked_stations_and_origin_charging(objective):
    """Several stations on one node (two Pareto sets), a station at the origin
    with an initial energy below the reserve and a station at the destination."""
    for seed in range(8):
        sc = _tiny(seed, 3, 3, objective, n_stations=2,
                   initial_soc=(0.05, 0.12, 0.25)[seed % 3],
                   time_dependent_waits=bool(seed % 2))
        st = list(sc.stations)
        node = st[0].node
        st.append(Station(id=len(st), node=node, power_kw=22.0, kind="AC", price_per_kwh=0.2,
                          daily_sessions=3, demand_profile="urban", setup_s=60.0))
        st.append(Station(id=len(st), node=node, power_kw=50.0, kind="DC", price_per_kwh=0.3,
                          daily_sessions=20, setup_s=120.0))
        st.append(Station(id=len(st), node=sc.query.origin, power_kw=50.0, kind="DC", price_per_kwh=0.5))
        if seed % 2:
            st.append(Station(id=len(st), node=sc.query.destination, power_kw=100.0, kind="DC"))
        _compare_with_oracle(sc.with_(stations=st), "grid" if seed % 2 else "grid+breaks")


# ----------------------------------------------------------------------------
# 3. A* settles fewer labels
# ----------------------------------------------------------------------------


def test_astar_settles_no_more_labels_than_dijkstra_order():
    n_bls = n_basic = n_tight = 0
    for cfg in RANDOM_CONFIGS[:14]:
        P = _scenario(cfg).prepare()
        a = _run(P, "bls")
        b = _run(P, "bls-astar", potential="basic")
        c = _run(P, "bls-astar")
        if not a.feasible:
            continue
        n_bls += a.stats.settled
        n_basic += b.stats.settled
        n_tight += c.stats.settled
    assert n_basic <= n_bls
    assert n_tight <= n_basic


def test_potentials_are_admissible_and_consistent():
    rng = random.Random(7)
    for objective, td in (("time", True), ("cost", False), ("cost", True)):
        sc = grid_scenario(9, 9, seed=11, initial_soc=0.3, objective=objective,
                           time_dependent_waits=td, speed_factors=(1.0, 0.85))
        P = sc.prepare()
        basic, tight = AStarPotential.basic(P), AStarPotential.tight(P)
        ref = _run(P, "bls-astar")
        assert ref.feasible
        for h in (basic, tight):
            # admissible at the root
            assert h(P.origin, P.e_init) <= ref.internal_objective + 1e-6
            # zero at the destination once the arrival requirement is met
            assert h(P.destination, P.e_dest_min) == 0.0
            assert h(P.destination, P.capacity) == 0.0
        for _ in range(3000):
            u = rng.randrange(P.net.num_nodes)
            x = rng.uniform(P.e_min, P.capacity)
            hb_u, ht_u = basic(u, x), tight(u, x)
            assert ht_u >= hb_u - 1e-9  # tight is never weaker
            # non-increasing in the energy
            assert tight(u, x + rng.uniform(0, 5)) <= ht_u + 1e-9
            for a in P.net.out_edges[u]:
                w = P.net.head[a]
                for _k, tau, c, dc in P.edge_options[a]:
                    x2 = min(x - c, P.capacity)
                    cost = tau if objective == "time" else dc
                    for h in (basic, tight):
                        if h(w, x2) < math.inf:
                            assert h(u, x) <= cost + h(w, x2) + 1e-7
        # charging transitions
        for s, st in enumerate(P.stations):
            v = st.node
            for _ in range(40):
                x = rng.uniform(P.e_min, P.e_charge_cap - 1.0)
                y = rng.uniform(x, P.e_charge_cap)
                t = rng.uniform(0, 20000)
                w = P.wait_s(s, t)
                ch = P.cf[s].charge_time(x, y)
                cost = (w + P.setup_s[s] + ch) if objective == "time" else P.charge_cost(s, x, y, ch, w)
                for h in (basic, tight):
                    assert h(v, x) <= cost + h(v, y) + 1e-7


# ----------------------------------------------------------------------------
# 4. infeasibility is reported with a reason
# ----------------------------------------------------------------------------


def test_infeasible_without_stations():
    sc = grid_scenario(8, 8, seed=4, n_stations=0, initial_soc=0.13)
    for name in ("bls", "bls-astar"):
        p = plan(sc, name)
        assert not p.feasible
        assert "no battery-feasible route" in p.message
        assert "closest approach" in p.message
        assert p.stats is not None and p.stats.settled > 0


def test_infeasible_initial_energy_below_reserve_without_origin_station():
    sc = grid_scenario(5, 5, seed=1, initial_soc=0.05)
    assert not sc.prepare().is_station_node[sc.query.origin]
    p = plan(sc, "bls-astar")
    assert not p.feasible
    assert "below the reserve" in p.message


def test_unreachable_destination_and_label_limit():
    from evroute.network import NetworkBuilder
    from evroute.scenario import Query, Scenario
    from evroute.vehicle import get_vehicle

    b = NetworkBuilder()
    a0 = b.add_node(0, 0)
    a1 = b.add_node(5000, 0)
    a2 = b.add_node(10000, 0)
    b.add_road(a0, a1)
    b.add_edge(a2, a1)  # a2 -> a1 only: a2 cannot be reached
    sc = Scenario("cut", b.build(), [], get_vehicle("compact50"), Query(a0, a2, initial_soc=0.8))
    p = plan(sc, "bls")
    assert not p.feasible and "not reachable" in p.message

    sc = grid_scenario(8, 8, seed=2, initial_soc=0.3)
    p = plan(sc, "bls", max_labels=50)
    assert not p.feasible and "label limit" in p.message


def test_options_are_validated():
    sc = grid_scenario(5, 5, seed=1)
    with pytest.raises(ValueError):
        plan(sc, "bls", targets="everything")
    with pytest.raises(ValueError):
        plan(sc, "bls-astar", potential="magic")


# ----------------------------------------------------------------------------
# 5. charging at the origin when starting below the reserve
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("objective", ["time", "cost"])
def test_origin_station_with_initial_energy_below_reserve(objective):
    sc = grid_scenario(6, 6, seed=5, initial_soc=0.04, objective=objective)
    st = list(sc.stations) + [Station(id=99, node=sc.query.origin, power_kw=100.0, name="home")]
    sc = sc.with_(stations=st)
    P = sc.prepare()
    assert P.e_init < P.e_min
    plans = [plan(sc, name) for name in ("bls", "bls-astar")]
    for p in plans:
        _assert_consistent(P, p)
        first = p.stops[0]
        assert first.position == 0 and first.station == len(st) - 1
        assert first.energy_out_kwh >= P.e_min
    assert math.isclose(plans[0].internal_objective, plans[1].internal_objective, rel_tol=1e-9)


def test_origin_equals_destination():
    sc = grid_scenario(5, 5, seed=1, initial_soc=0.6).with_query(destination=0)
    p = plan(sc, "bls-astar")
    assert p.feasible and p.edges == [] and p.total_time_s == 0.0


# ----------------------------------------------------------------------------
# 6. search trace
# ----------------------------------------------------------------------------


def test_trace_records_settled_nodes_and_keys():
    sc = grid_scenario(8, 8, seed=6, initial_soc=0.3)
    for name in ("bls", "bls-astar"):
        p = plan(sc, name, trace=True)
        assert p.feasible
        tr = p.trace
        assert tr is not None and len(tr.nodes) == len(tr.keys) > 5
        assert tr.nodes[0] == sc.query.origin
        assert tr.nodes[-1] == sc.query.destination
        assert len(set(tr.nodes)) == len(tr.nodes)
        # consistent keys are popped in non-decreasing order
        assert all(b >= a - 1e-6 for a, b in zip(tr.keys, tr.keys[1:], strict=False))
        assert tr.keys[-1] == pytest.approx(p.internal_objective)
        assert p.to_dict()["trace"]["nodes"][0] == sc.query.origin
    assert plan(sc, "bls").trace is None


# ----------------------------------------------------------------------------
# 7. fastest_feasible_tree
# ----------------------------------------------------------------------------


def test_fastest_feasible_tree_matches_evaluator_and_bls():
    sc = grid_scenario(7, 7, seed=9, n_stations=0, initial_soc=0.3, min_arrival_soc=0.0,
                       speed_factors=(1.0, 0.85))
    P = sc.prepare()
    tree = fastest_feasible_tree(P, P.origin, P.e_init)
    assert tree[P.origin] == (0.0, P.e_init, [], [])
    assert 3 < len(tree) < P.net.num_nodes  # the battery limits the reach
    rng = random.Random(1)
    nodes = rng.sample(sorted(v for v in tree if v != P.origin), 6)
    for v in nodes:
        t, e, edges, levels = tree[v]
        P2 = sc.with_query(destination=v).prepare()
        rp = evaluate(P2, PlanDecision(edges, levels))
        assert rp.feasible
        assert rp.total_time_s == t
        assert rp.node_energy_arrive[-1] == e
        # minimal: BLS (no stations) finds the same arrival time
        assert plan(P2, "bls").internal_objective == pytest.approx(t, rel=1e-12)
    # nodes outside the tree are infeasible for BLS as well
    far = [v for v in range(P.net.num_nodes) if v not in tree][:2]
    for v in far:
        assert not plan(sc.with_query(destination=v), "bls-astar").feasible


def test_fastest_feasible_tree_options():
    sc = grid_scenario(7, 7, seed=9, initial_soc=0.3, min_arrival_soc=0.0)
    P = sc.prepare()
    full = fastest_feasible_tree(P, P.origin, P.e_init)
    limited = fastest_feasible_tree(P, P.origin, P.e_init, max_time=1800.0)
    assert set(limited) <= set(full)
    assert all(limited[v][0] <= 1800.0 for v in limited)
    assert all(limited[v][:2] == full[v][:2] for v in limited)
    # requirement on arrival energy: earliest arrival with enough energy
    v = max(full, key=lambda u: full[u][0])
    need = {v: full[v][1] + 0.5}
    req = fastest_feasible_tree(P, P.origin, P.e_init, min_arrival_energy=need)
    if v in req:
        assert req[v][1] >= need[v] - 1e-9 and req[v][0] >= full[v][0]
    # below the reserve nothing but the source is reachable
    low = fastest_feasible_tree(P, P.origin, P.e_min * 0.5)
    assert list(low) == [P.origin]
    # with charging every node is reachable here and the routes replay
    ch = fastest_feasible_tree(P, P.origin, P.e_init, allow_charging=True)
    assert set(full) <= set(ch)
    for v in list(ch)[-3:]:
        t, _, edges, levels, charges = ch[v]
        P2 = sc.with_query(destination=v).prepare()
        rp = evaluate(P2, PlanDecision(edges, levels, charges))
        assert rp.feasible and rp.total_time_s == pytest.approx(t, rel=1e-12)
        best = plan(P2, "bls")
        assert best.internal_objective == pytest.approx(t, rel=1e-12)


def test_completion_energy_suffices_on_the_bound_tree():
    from evroute.search import dijkstra, min_level_weights

    sc = grid_scenario(8, 8, seed=12, initial_soc=0.5)
    P = sc.prepare()
    pot = AStarPotential.tight(P)
    need = completion_energy(P, pot.base)
    assert need[P.destination] == P.e_dest_min
    _, pred = dijkstra(P.net, P.destination, min_level_weights(P, "time"), reverse=True)
    checked = 0
    for v in range(0, P.net.num_nodes, 5):
        if v == P.destination or need[v] == math.inf:
            continue
        path = []
        u = v
        while u != P.destination:
            path.append(pred[u])
            u = P.net.head[pred[u]]
        levels = [min(P.edge_options[a], key=lambda o: (o[1], o[2]))[0] for a in path]
        for e0, ok in ((need[v], True), (need[v] - 0.01, False)):
            P2 = sc.with_query(origin=v, initial_soc=e0 / P.capacity).prepare()
            rp = evaluate(P2, PlanDecision(path, levels))
            assert rp.feasible == ok
            if ok:
                assert rp.total_time_s == pytest.approx(pot.base[v], rel=1e-12)
        checked += 1
    assert checked >= 5


@pytest.mark.parametrize("only_non_dominated", [True, False])
def test_3d_pareto_set_matches_naive_scan(monkeypatch, only_non_dominated):
    """The binned/Fenwick 3-D dominance structure answers exactly like a scan."""
    import evroute.algorithms.labels as labels

    monkeypatch.setattr(labels, "_P3_BUILD", 8)  # exercise the tree early
    rng = random.Random(5 if only_non_dominated else 9)
    for _ in range(120):
        cap = rng.choice((10.0, 49.0, 80.0))
        s3 = labels._Pareto3(labels._NB3 / cap)
        Pv, Ev, Iv, pts = [], [], [], []
        targets = [round(rng.uniform(0, cap), 1) for _ in range(5)]
        for it in range(rng.randint(1, 250)):
            p = round(rng.uniform(0, 100), rng.choice((0, 1, 3)))
            q = round(rng.uniform(0, 100), rng.choice((0, 1, 3)))
            e = rng.choice(targets) if rng.random() < 0.3 else (cap if rng.random() < 0.05 else rng.uniform(0, cap))
            naive = any(p2 <= p and q2 <= q and e2 >= e for p2, q2, e2 in pts)
            got = s3.dominated(Pv, Ev, p, q, e)
            assert got == naive
            if (not got or not only_non_dominated) and rng.random() < 0.7:
                s3.add(p, q, e)
                labels._stair_add(Pv, Ev, Iv, p, e, it)
                pts.append((p, q, e))


def test_charging_targets():
    sc = grid_scenario(5, 5, seed=1, max_charge_soc=0.8, charge_step_soc=0.05)
    P = sc.prepare()
    for s in range(len(P.stations)):
        grid = charging_targets(P, s, "grid")
        both = charging_targets(P, s, "grid+breaks")
        assert grid == sorted(grid) and both == sorted(both)
        assert max(both) == pytest.approx(P.e_charge_cap) and max(both) <= P.e_charge_cap
        assert set(grid) <= set(both)
        assert all(b - a > 1e-9 for a, b in zip(both, both[1:], strict=False))
        inner = [b for b in P.cf[s].e_breaks[1:-1] if b <= P.e_charge_cap]
        assert all(any(abs(b - y) <= 1e-9 for y in both) for b in inner)
    with pytest.raises(ValueError):
        charging_targets(P, 0, "continuous")


# ----------------------------------------------------------------------------
# misc: registry metadata and performance smoke test
# ----------------------------------------------------------------------------


def test_registry_metadata():
    assert LabelSettingPlanner.info()["exact"] == "grid"
    assert LabelAStarPlanner.info()["name"] == "bls-astar"
    assert set(LabelAStarPlanner.objectives) == {"time", "cost"}
    assert LabelSettingPlanner.title == "BLS (label setting)"
    assert LabelAStarPlanner.title == "BLS-A*"


def test_astar_is_fast_on_a_30x30_grid():
    import time

    sc = grid_scenario(30, 30, seed=1, initial_soc=0.5)
    P = sc.prepare()
    t0 = time.perf_counter()
    p = plan(P, "bls-astar")
    dt = time.perf_counter() - t0
    assert p.feasible and p.num_stops >= 1
    _assert_consistent(P, p)
    assert dt < 2.0
