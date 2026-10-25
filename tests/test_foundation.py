"""Tests of the model layer: units, network, physics, charging, queues, evaluator."""

import json
import math

import pytest

from evroute import NetworkBuilder, PlanDecision, ChargeDecision, Scenario, evaluate
from evroute.charging import ChargingFunction
from evroute.evaluate import TOL_KWH
from evroute.network import RoadNetwork
from evroute.scenario import Conditions, Preferences, Query, Station
from evroute.generators import grid_scenario
from evroute.queueing import WaitProfile, erlang_c, expected_wait_s
from evroute.search import LowerBounds, bellman_ford, dijkstra, min_level_weights, path_from_pred
from evroute.vehicle import VEHICLES, ChargingCurve


def test_erlang_c_known_value():
    # c = 2 servers, offered load a = 1 Erlang -> C = 1/3
    assert erlang_c(2, 1.0) == pytest.approx(1 / 3)
    assert erlang_c(1, 0.5) == pytest.approx(0.5)  # M/M/1: P(wait) = rho
    assert expected_wait_s(10.0, 0.5, 4, 1e9) > 0
    assert expected_wait_s(100.0, 0.5, 4, 7200.0) == 7200.0  # unstable -> cap


def test_wait_profile_is_fifo():
    hourly = [0.0] * 24
    hourly[10] = 3 * 3600.0  # a huge spike that would violate FIFO
    prof = WaitProfile.from_hourly(hourly)
    t = 0.0
    prev_leave = -math.inf
    while t < 86400.0:
        leave = t + prof(t)
        assert leave >= prev_leave - 1e-6
        prev_leave = leave
        t += 30.0


def test_charging_curve_harmonic_binning_preserves_time():
    pts = [(0.0, 100.0), (0.5, 100.0), (1.0, 20.0)]
    curve = ChargingCurve.from_points(pts, bins=10)
    # exact time over [0.5, 1.0] for 1 kWh capacity: int ds / p(s)
    slope = (20.0 - 100.0) / 0.5
    exact = math.log(20.0 / 100.0) / slope
    binned = sum((b - a) / p for a, b, p in zip(curve.soc_breaks, curve.soc_breaks[1:], curve.power_kw) if a >= 0.5)
    assert binned == pytest.approx(exact, rel=1e-9)


def test_charging_function_inverse_and_monotone():
    f = ChargingFunction([0, 10, 20, 30], [100.0, 50.0, 10.0], [0.5, 0.5, 0.5], [1e-4, 2e-4, 3e-4])
    assert f.time_at(10) == pytest.approx(360.0)
    assert f.time_at(30) == pytest.approx(360 + 720 + 3600)
    for e in [0.0, 3.3, 10.0, 17.2, 29.9, 30.0]:
        assert f.energy_at(f.time_at(e)) == pytest.approx(e)
    assert f.charge_time(5, 25) == pytest.approx(f.time_at(25) - f.time_at(5))
    assert f.energy_after(5, f.charge_time(5, 25)) == pytest.approx(25)
    assert f.money(0, 30) == pytest.approx(15.0)
    assert f.breakpoints_between(5, 25) == [10, 20]


def _line(elev_b: float, length: float = 10000.0, road_class: str = "motorway"):
    b = NetworkBuilder()
    a = b.add_node(0, 0, 0)
    z = b.add_node(length, 0, elev_b)
    b.add_road(a, z, road_class=road_class)
    return b.build()


def test_physics_uphill_downhill_regen():
    from evroute.energy import PhysicsEnergyModel
    from evroute.scenario import Conditions

    veh = VEHICLES["sedan75"]
    m = PhysicsEnergyModel(veh, Conditions(temperature_c=20.0))
    flat = m.traverse(_line(0.0), 0)
    up = m.traverse(_line(400.0), 0)
    down = m.traverse(_line(400.0), 1)
    assert up.energy_kwh > flat.energy_kwh > 0
    assert down.energy_kwh < flat.energy_kwh
    # potential energy of 400 m is ~2.2 kWh for 2 t: uphill minus flat must be close to it / eta
    pot = (veh.mass_kg + 75) * 9.80665 * 400 / 3.6e6
    assert up.energy_kwh - flat.energy_kwh == pytest.approx(pot / veh.drivetrain_eff, rel=0.05)
    # a steep descent regenerates (negative consumption)
    steep = m.traverse(_line(800.0, 8000.0), 1)
    assert steep.energy_kwh < 0
    # cold weather costs energy (heater)
    cold = PhysicsEnergyModel(veh, Conditions(temperature_c=-10.0)).traverse(_line(0.0), 0)
    assert cold.energy_kwh > flat.energy_kwh


def test_network_roundtrip_and_scenario_roundtrip(tmp_path):
    sc = grid_scenario(rows=5, cols=5, seed=3)
    d = sc.to_dict()
    sc2 = Scenario.from_dict(json.loads(json.dumps(d)))
    assert sc2.network.num_edges == sc.network.num_edges
    assert sc2.vehicle == sc.vehicle
    p = tmp_path / "s.json"
    sc.save(p)
    sc3 = Scenario.load(p)
    assert sc3.prepare().capacity == pytest.approx(sc.prepare().capacity)
    assert sc3.network == sc.network  # save/load is lossless too


def _geo_scenario() -> Scenario:
    b = NetworkBuilder()
    pts = [(0.0, 0.0, 912.37, 39.9334, 32.8597), (51234.567891, 1234.5, 1001.1, 39.9449123, 33.4567891),
           (98765.4321, -2222.2222, 777.777, 39.91234567, 34.0123456789)]
    ids = [b.add_node(x, y, h, name=f"N{i}", lat=la, lon=lo) for i, (x, y, h, la, lo) in enumerate(pts)]
    b.add_road(ids[0], ids[1], road_class="motorway", name="A1")
    b.add_road(ids[1], ids[2], road_class="primary")
    st = [Station(0, ids[1], 150.0, "DC", demand_profile=tuple(float(h % 7) for h in range(24))),
          Station(1, ids[1], 22, "AC", plugs=2, demand_profile="urban")]
    return Scenario("geo", b.build(), st, VEHICLES["sedan75"], Query(ids[0], ids[2], initial_soc=0.31),
                    Conditions(temperature_c=-3.3, wind_speed_ms=4.4, class_speed_factor={"urban": 0.7}),
                    Preferences(objective="cost", speed_factors=(1.0, 0.9), reserve_soc=0.0), meta={"k": (1, 2)})


@pytest.mark.parametrize("make", [lambda: grid_scenario(rows=5, cols=5, seed=3, initial_soc=0.2), _geo_scenario])
def test_scenario_json_round_trip_is_lossless(make):
    """BUG-2: ``to_dict`` -> JSON -> ``from_dict`` rebuilds an instance that plans bit-identically."""
    sc = make()
    d = sc.to_dict()
    text = json.dumps(d, allow_nan=False)
    assert json.loads(text) == d  # JSON-normal (tuples written as lists, string keys)
    back = Scenario.from_dict(json.loads(text))
    assert back.to_dict() == d  # fixed point
    assert back.network == sc.network  # every float bit-identical, lat/lon included
    assert back.vehicle == sc.vehicle and back.query == sc.query and back.conditions == sc.conditions
    assert back.preferences == sc.preferences and isinstance(back.preferences.speed_factors, tuple)
    assert [s.to_dict() for s in back.stations] == [s.to_dict() for s in sc.stations]
    P, Pb = sc.prepare(), back.prepare()
    assert Pb.edge_time == P.edge_time and Pb.edge_energy == P.edge_energy and Pb.edge_options == P.edge_options
    assert Pb.cf == P.cf and Pb.target_grid == P.target_grid
    assert [w.to_dict() for w in Pb.wait_profiles] == [w.to_dict() for w in P.wait_profiles]
    for a in ("capacity", "e_min", "e_charge_cap", "e_init", "e_dest_min", "setup_s"):
        assert getattr(Pb, a) == getattr(P, a), a


def test_network_compact_dict_is_rounded_and_smaller():
    net = _geo_scenario().network
    full, compact = net.to_dict(), net.to_dict(compact=True)
    assert compact["x"][1] == 51234.57 and compact["length"] == [round(v, 3) for v in net.length]
    assert compact["lat"][2] == 39.912346 and full["lat"][2] == net.lat[2]
    assert len(json.dumps(compact)) < len(json.dumps(full))
    again = RoadNetwork.from_dict(json.loads(json.dumps(compact)))
    assert again.num_edges == net.num_edges and max(abs(a - b) for a, b in zip(again.x, net.x)) <= 0.005


def _single_edge(initial_soc: float) -> Scenario:
    b = NetworkBuilder()
    o, d = b.add_node(0.0, 0.0, 100.0), b.add_node(30e3, 0.0, 100.0)
    b.add_road(o, d, road_class="motorway")
    return Scenario("single edge", b.build(), [], VEHICLES["compact50"], Query(o, d, initial_soc=initial_soc),
                    Conditions(), Preferences(reserve_soc=0.0, min_arrival_soc=0.0, queue_aware=False))


def test_evaluator_empty_battery_uses_the_energy_tolerance():
    """BUG-4: with a zero reserve, an energy in ``[-TOL_KWH, 0)`` is float noise; below
    ``-TOL_KWH`` the battery is empty (and the stranding point is where it hit zero)."""
    P = _single_edge(0.4).prepare()
    dec = PlanDecision([0], [0])
    assert evaluate(P, dec).feasible
    for short, ok in ((0.0, True), (1e-15, True), (0.5 * TOL_KWH, True), (0.99 * TOL_KWH, True),
                      (2.0 * TOL_KWH, False), (1.0, False)):
        c = P.e_init + short  # consumption of the only edge: arrive with -short kWh
        plan = evaluate(P, dec, energy_override=[c])
        assert plan.feasible == ok, (short, plan.violations)
        assert (plan.stranded_at_m is None) == ok, short
        if not ok:
            assert plan.stranded_at_m == pytest.approx(P.e_init / c * P.net.length[0])
            assert any("battery empty" in v for v in plan.violations)
    # a positive reserve is unaffected: undercutting it by more than TOL_KWH is a violation
    Pr = _single_edge(0.4).with_preferences(reserve_soc=0.05, min_arrival_soc=0.05).prepare()
    c = Pr.e_init - Pr.e_min
    assert evaluate(Pr, dec, energy_override=[c + 0.5 * TOL_KWH]).feasible
    bad = evaluate(Pr, dec, energy_override=[c + 2.0 * TOL_KWH])
    assert not bad.feasible and bad.stranded_at_m is None


def test_lower_bounds_are_admissible():
    sc = grid_scenario(rows=8, cols=8, seed=5)
    P = sc.prepare()
    lb = LowerBounds(P)
    w = min_level_weights(P, "time")
    for s in [0, 7, 20, 41]:
        d, _ = dijkstra(P.net, s, w)
        assert lb.time_lb[s] == pytest.approx(d[P.destination])
    ew = min_level_weights(P, "energy")
    de = bellman_ford(P.net, 0, ew)
    assert lb.energy_lb[0] == pytest.approx(de[P.destination])


def test_evaluator_feasible_with_charging():
    sc = grid_scenario(rows=8, cols=8, seed=2, initial_soc=0.3)
    P = sc.prepare()
    st = P.stations[0]
    # drive origin -> station -> destination along time-shortest paths
    w = min_level_weights(P, "time")
    _, p1 = dijkstra(P.net, P.origin, w)
    _, p2 = dijkstra(P.net, st.node, w)
    a = path_from_pred(P.net, p1, P.origin, st.node)
    b = path_from_pred(P.net, p2, st.node, P.destination)
    dec = PlanDecision(a + b, [0] * (len(a) + len(b)), [ChargeDecision(len(a), 0, P.e_charge_cap)])
    plan = evaluate(P, dec, algorithm="manual")
    assert plan.num_stops == 1
    stop = plan.stops[0]
    assert stop.energy_out_kwh == pytest.approx(P.e_charge_cap)
    assert plan.total_time_s == pytest.approx(plan.drive_time_s + plan.charge_time_s + plan.wait_time_s + plan.setup_time_s)
    # SoC profile is consistent with arrival/departure energies
    assert plan.node_energy_depart[len(a)] == pytest.approx(P.e_charge_cap)
    # wrong station position is reported
    bad = PlanDecision(a + b, [0] * (len(a) + len(b)), [ChargeDecision(0, 0, P.e_charge_cap)])
    assert not evaluate(P, bad).feasible


def test_evaluator_detects_broken_path():
    sc = grid_scenario(rows=4, cols=4, seed=1)
    P = sc.prepare()
    e0 = P.net.out_edges[P.origin][0]
    other = next(e for e in range(P.net.num_edges) if P.net.tail[e] != P.net.head[e0])
    plan = evaluate(P, PlanDecision([e0, other], [0, 0]))
    assert not plan.feasible
