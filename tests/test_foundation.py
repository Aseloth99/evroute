"""Tests of the model layer: units, network, physics, charging, queues, evaluator."""

import json
import math

import pytest

from evroute import NetworkBuilder, PlanDecision, ChargeDecision, Scenario, evaluate
from evroute.charging import ChargingFunction
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
