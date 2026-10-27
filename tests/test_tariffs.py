"""Tests of charging tariffs: the high-SoC tier ("80 % rule"), peak hours and voluntary holds.

A :class:`~evroute.scenario.Tariff` makes the price of a kWh depend on the
energy (above ``high_soc``) and on the price period of the station at the time
the vehicle joins its queue.  The tests check

* the model: money on both sides of ``high_soc`` in both periods, period
  boundaries (wrap-around, busy hours from the queue model), serialisation;
* the evaluator: a voluntary hold costs time, the queue is evaluated at the
  end of the hold and the money is that of the period at that time;
* exactness: the neutral tariff changes nothing (values recorded before the
  feature existed), BLS == BLS-A* and Pareto extremes == BLS under
  time-dependent tariffs, an exhaustive enumeration of walks, stops, targets
  and holds (also to times that are not price changes) agrees on tiny
  instances, and hand-made instances where the cost optimum must hold until
  the peak ends, charge to exactly 80 % and stop again, or restart at the same
  station when the off-peak period begins;
* the time objective never holds.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import random
import sys

import pytest

from evroute import Tariff
from evroute.algorithms import get_planner
from evroute.evaluate import check_objective, evaluate
from evroute.generators import grid_scenario
from evroute.generators.presets import get_scenario
from evroute.network import NetworkBuilder
from evroute.plan import ChargeDecision, PlanDecision
from evroute.queueing import HIGHWAY_PROFILE, URBAN_PROFILE
from evroute.robustness import monte_carlo, replay_run
from evroute.scenario import Conditions, Preferences, Query, Scenario, Station
from evroute.vehicle import ChargingCurve, get_vehicle

H = 3600.0
DAY = 86400.0
EPS = 1e-9
INF = math.inf


# ----------------------------------------------------------------------------
# instances
# ----------------------------------------------------------------------------


def _peak_line(tariff: Tariff | None = None, *, vot: float = 15.0, depart_h: float = 19.0, queue: bool = False,
               objective: str = "cost") -> Scenario:
    """O --60 km--> S --150 km--> D, one 50 kW DC charger (0.50 / kWh) at S.

    The car reaches S after exactly 30 min (19:30 with the default departure)
    with too little energy to finish."""
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, 100.0, name="O")
    s = b.add_node(60e3, 0.0, 100.0, name="S")
    d = b.add_node(210e3, 0.0, 100.0, name="D")
    b.add_road(o, s, road_class="motorway")
    b.add_road(s, d, road_class="motorway")
    st = [Station(0, s, 50.0, "DC", price_per_kwh=0.5, setup_s=180.0, plugs=2,
                  daily_sessions=40.0 if queue else 0.0, name="S")]
    return Scenario("peak line", b.build(), st, get_vehicle("compact50"),
                    Query(o, d, initial_soc=0.4, departure_s=depart_h * H), Conditions(temperature_c=20.0),
                    Preferences(objective=objective, value_of_time_per_h=vot, queue_aware=queue,
                                charge_step_soc=0.05),
                    tariff=tariff or Tariff())


def _flat_vehicle():
    """compact50 with a flat 50 kW DC curve: 80 % is not a breakpoint of the curve."""
    return dataclasses.replace(get_vehicle("compact50"), dc_curve=ChargingCurve((0.0, 1.0), (50.0,)))


def _two_station_line(tariff: Tariff | None = None, objective: str = "cost") -> Scenario:
    """O --60 km--> S1 (0.40 / kWh) --150 km--> S2 (0.60 / kWh, 10 min set-up) --60 km--> D."""
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, 100.0, name="O")
    s1 = b.add_node(60e3, 0.0, 100.0, name="S1")
    s2 = b.add_node(210e3, 0.0, 100.0, name="S2")
    d = b.add_node(270e3, 0.0, 100.0, name="D")
    for u, v in ((o, s1), (s1, s2), (s2, d)):
        b.add_road(u, v, road_class="motorway")
    st = [Station(0, s1, 50.0, "DC", price_per_kwh=0.4, setup_s=180.0, daily_sessions=0.0, name="S1"),
          Station(1, s2, 50.0, "DC", price_per_kwh=0.6, setup_s=600.0, daily_sessions=0.0, name="S2")]
    return Scenario("80 % rule", b.build(), st, _flat_vehicle(),
                    Query(o, d, initial_soc=0.5, departure_s=9 * H), Conditions(temperature_c=20.0),
                    Preferences(objective=objective, queue_aware=False, charge_step_soc=0.05),
                    tariff=tariff or Tariff())


def _one_station(tariff: Tariff, **station) -> Scenario:
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0)
    d = b.add_node(50e3, 0.0)
    b.add_road(o, d)
    st = Station(0, o, **{"power_kw": 50.0, "kind": "DC", "price_per_kwh": 0.4, **station})
    return Scenario("one station", b.build(), [st], _flat_vehicle(), Query(o, d, initial_soc=0.3),
                    tariff=tariff)


def _run(P, name, **opts):
    return get_planner(name, **opts).plan(P)


def _close(a, b, rel=1e-9, abs_=1e-9):
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def _assert_consistent(p):
    assert p.feasible, p.message
    assert check_objective(p)
    assert _close(p.objective_value, p.internal_objective, 1e-9, 1e-9), (p.objective_value, p.internal_objective)


# ----------------------------------------------------------------------------
# 1. model: money, periods, serialisation
# ----------------------------------------------------------------------------


def test_money_on_both_sides_of_high_soc_in_both_periods():
    tariff = Tariff(high_soc_factor=2.0, peak_hours=((16, 20),), peak_factor=1.5, peak_high_soc_factor=3.0)
    sc = _one_station(tariff)
    P, P0 = sc.prepare(), sc.with_(tariff=Tariff()).prepare()
    q = P.capacity
    f, fp, f0 = P.cf[0], P.cf_peak[0], P0.cf[0]
    eb = 0.8 * q
    assert f0.e_breaks == [0.0, q] and eb in f.e_breaks  # the bin is split at high_soc x capacity ...
    assert f.e_breaks == fp.e_breaks and f.power_kw == fp.power_kw == [50.0, 50.0]  # ... with the same power
    assert f.soh_pct_per_kwh == fp.soh_pct_per_kwh == f0.soh_pct_per_kwh * 2
    unit = 0.4 / P.vehicle.charge_eff_dc  # station price per stored kWh
    cases = [  # (function, e1, e2, expected money)
        (f, 0.5 * q, 0.7 * q, 0.2 * q * unit),  # off-peak, below
        (f, 0.85 * q, 0.95 * q, 0.1 * q * unit * 2.0),  # off-peak, above
        (fp, 0.5 * q, 0.7 * q, 0.2 * q * unit * 1.5),  # peak, below
        (fp, 0.85 * q, 0.95 * q, 0.1 * q * unit * 1.5 * 3.0),  # peak, above
        (f, 0.7 * q, 0.9 * q, 0.1 * q * unit + 0.1 * q * unit * 2.0),  # across the breakpoint
        (fp, 0.7 * q, 0.9 * q, 0.1 * q * unit * 1.5 + 0.1 * q * unit * 4.5),
    ]
    for fn, a, b, want in cases:
        assert _close(fn.money(a, b), want, 1e-12, 1e-12), (a / q, b / q, fn.money(a, b), want)
    for a, b in ((0.0, q), (0.1 * q, 0.8 * q), (0.75 * q, 0.85 * q), (0.8 * q, q)):
        # charging time and wear are those of the neutral tariff (up to rounding of the tables)
        assert _close(f.charge_time(a, b), f0.charge_time(a, b), 1e-12, 1e-9)
        assert _close(f.soh(a, b), f0.soh(a, b), 1e-12, 1e-15)
    # the period at the time of joining the queue selects the money (departure 08:00)
    assert P.money_cf(0, 7.5 * H) is f and P.money_cf(0, 8 * H) is fp and P.money_cf(0, 12 * H - 1e-6) is fp
    assert P.money_cf(0, 12 * H) is f
    # peak_high_soc_factor=None: the off-peak high-SoC factor applies in the peak as well
    Pn = sc.with_tariff(peak_high_soc_factor=None).prepare()
    assert _close(Pn.cf_peak[0].money(0.85 * q, 0.95 * q), 0.1 * q * unit * 1.5 * 2.0, 1e-12, 1e-12)
    # bounds use the cheapest money of all periods
    cheap = sc.with_tariff(peak_factor=0.5).prepare()
    assert cheap.min_money_rate == pytest.approx(unit * 0.5)
    assert cheap.cf_peak[0] in cheap.cf_variants


def test_neutral_and_no_op_tariffs_add_nothing():
    sc = _one_station(Tariff())
    P = sc.prepare()
    assert P.cf_peak is P.cf and P.cf_variants is P.cf
    assert not P.tariff_time_dependent and P.price_changes == [()] and P.peak_intervals == [()]
    assert P.cf[0].e_breaks == [0.0, P.capacity]
    assert P.hold_candidates(0, 0.0) == [] and not P.is_peak(0, 0.0)
    # peak hours with factor 1 change no price: no breakpoint, no hold, but the period is known
    Q = sc.with_tariff(peak_hours=((7, 12),), busy_utilisation=0.5).prepare()
    assert Q.cf_peak is Q.cf and Q.cf[0].e_breaks == P.cf[0].e_breaks and not Q.tariff_time_dependent
    assert Q.is_peak(0, 0.0) and Q.hold_candidates(0, 0.0) == []
    # a high-SoC factor alone is time-independent: split bin, no peak functions
    R = sc.with_tariff(high_soc_factor=1.25).prepare()
    assert R.cf_peak is R.cf and not R.tariff_time_dependent and len(R.cf[0].e_breaks) == 3


def test_peak_periods_boundaries_and_wrap_around():
    t = Tariff(peak_hours=((22, 6), (16, 20)))
    assert t.peak_intervals() == ((0.0, 6 * H), (16 * H, 20 * H), (22 * H, DAY))
    assert Tariff(peak_hours=((8, 10), (9, 12), (12, 13))).peak_intervals() == ((8 * H, 13 * H),)
    assert Tariff(peak_hours=((0, 24),)).peak_intervals() == ((0.0, DAY),)
    assert Tariff(peak_hours=((6, 6),)).peak_intervals() == ()
    assert Tariff(peak_hours=((24, 6),)).peak_intervals() == ((0.0, 6 * H),)
    assert Tariff(peak_hours=((20.5, 24),)).peak_intervals() == ((20.5 * H, DAY),)

    sc = _one_station(dataclasses.replace(t, peak_factor=2.0))
    sc = sc.with_query(departure_s=21 * H)
    P = sc.prepare()
    assert P.tariff_time_dependent and P.price_changes[0] == (6 * H, 16 * H, 20 * H, 22 * H)

    def peak_at(clock_h, days=0):
        return P.is_peak(0, (clock_h - 21.0) * H + days * DAY)

    # [start, end): the start belongs to the peak, the end does not; midnight is no boundary
    assert not peak_at(21.999) and peak_at(22.0) and peak_at(23.99) and peak_at(24.0) and peak_at(29.99)
    assert not peak_at(30.0) and not peak_at(39.99) and peak_at(40.0) and not peak_at(44.0)
    assert peak_at(22.0, days=3) and not peak_at(30.0, days=3)  # every day
    # hold candidates: the price changes in (t, t + 24 h], already in the new period
    for t0 in (0.0, 0.5 * H, 1.0 * H, 1.0 * H - 1e-7, 7.3 * H, 9.0 * H, 30.0 * H + 0.1):
        hc = P.hold_candidates(0, t0)
        assert hc == sorted(hc) and len(hc) == 4
        assert all(t0 < h <= t0 + DAY for h in hc)
        for h in hc:
            assert P.is_peak(0, h) != P.is_peak(0, h - 1e-3)  # a real change
            clock = (P.departure_s + h) % DAY
            assert min(abs(clock - c) for c in (*P.price_changes[0], DAY)) < 1e-6
    # standing exactly at a change: the next change of that kind is a day later
    assert P.hold_candidates(0, 1.0 * H)[-1] == pytest.approx(1.0 * H + DAY)
    # all-day peak: one price, the peak one, also through cf
    A = _one_station(Tariff(peak_hours=((0, 24),), peak_factor=2.0)).prepare()
    assert not A.tariff_time_dependent and A.cf[0] is A.cf_peak[0] and A.hold_candidates(0, 5.0) == []
    assert A.cf[0].money_per_kwh[0] == pytest.approx(2 * 0.4 / A.vehicle.charge_eff_dc)


def test_busy_hours_follow_the_queue_model():
    hw = dict(daily_sessions=150.0, plugs=4, mean_service_min=35.0, demand_profile="highway")
    ur = dict(daily_sessions=150.0, plugs=4, mean_service_min=35.0, demand_profile="urban")
    st = Station(0, 0, 50.0, **hw)
    rho = st.utilisation()
    total = sum(HIGHWAY_PROFILE)
    for h in range(24):  # rho = lambda / (c mu) with lambda = sessions x share, 1 / mu = 35 min
        lam = 150.0 * HIGHWAY_PROFILE[h] / total
        assert rho[h] == pytest.approx(lam * (35.0 / 60.0) / 4)
    thr = sorted(rho)[16]  # the 8 busiest hours (ties aside)
    tariff = Tariff(busy_utilisation=thr, peak_factor=1.8)
    want = []
    for h in range(24):
        if rho[h] >= thr:
            if want and want[-1][1] == h * H:
                want[-1] = (want[-1][0], (h + 1) * H)
            else:
                want.append((h * H, (h + 1) * H))
    assert tariff.peak_intervals(rho) == tuple(want) and len(want) >= 1
    assert tariff.peak_intervals() == ()  # nothing busy without a demand profile
    # per station: a highway and an urban charger get different periods; peak hours are added
    b = NetworkBuilder()
    o, d = b.add_node(0.0, 0.0), b.add_node(40e3, 0.0)
    b.add_road(o, d)
    stations = [Station(0, o, 50.0, **hw), Station(1, o, 50.0, **ur)]
    sc = Scenario("busy", b.build(), stations, _flat_vehicle(), Query(o, d, initial_soc=0.2),
                  tariff=dataclasses.replace(tariff, peak_hours=((2, 3),)))
    P = sc.prepare()
    assert P.peak_intervals[0] != P.peak_intervals[1]
    urho = stations[1].utilisation()
    assert urho == pytest.approx([150.0 * w / sum(URBAN_PROFILE) * (35 / 60) / 4 for w in URBAN_PROFILE])
    for h in range(24):
        for s, r in ((0, rho), (1, urho)):
            assert P._peak_at_clock(s, h * H + 1.0) == (r[h] >= thr or h == 2)
    # waits are not changed by the tariff
    plain = sc.with_(tariff=Tariff()).prepare()
    assert [w.to_dict() for w in P.wait_profiles] == [w.to_dict() for w in plain.wait_profiles]


def test_serialisation_validation_and_helpers():
    base = _peak_line()
    assert "tariff" not in base.to_dict()  # neutral: the JSON form of earlier versions
    assert list(base.to_dict()) == ["format", "name", "description", "currency", "network", "stations", "vehicle",
                                    "query", "conditions", "preferences", "meta"]
    sc = base.with_tariff(high_soc=0.75, high_soc_factor=1.4, peak_hours=((16, 20), (22.5, 6)), peak_factor=1.6,
                          peak_high_soc_factor=2.5, busy_utilisation=0.7)
    assert base.tariff == Tariff() and sc.tariff.peak_factor == 1.6  # with_* copies
    d = sc.to_dict()
    assert list(d)[-2:] == ["tariff", "meta"]
    assert d["tariff"]["peak_hours"] == [[16, 20], [22.5, 6]]
    back = Scenario.from_dict(json.loads(json.dumps(d)))
    assert back.tariff == sc.tariff and back.to_dict() == d  # lossless, fixed point
    assert back.tariff.peak_hours == ((16, 20), (22.5, 6))
    # lists are normalised to tuples
    assert Tariff(peak_hours=[[16, 20]]) == Tariff(peak_hours=((16, 20),))
    # validation
    bad = [dict(high_soc=1.0), dict(high_soc=0.0), dict(high_soc_factor=0.0), dict(peak_factor=-1.0),
           dict(peak_high_soc_factor=0.0), dict(busy_utilisation=0.0), dict(peak_hours=((16, 25),)),
           dict(peak_hours=((-1, 5),)), dict(peak_hours=((16, 20, 1),)), dict(peak_hours=((16, float("nan")),)),
           dict(high_soc_factor=float("inf"))]
    for kw in bad:
        with pytest.raises(ValueError, match="tariff"):
            base.with_tariff(**kw).prepare()
    # the prepared cache notices a tariff changed in place
    sc2 = base.with_tariff()
    P1 = sc2.prepare()
    sc2.tariff.peak_factor = 2.0
    sc2.tariff.peak_hours = ((16, 20),)
    assert sc2.prepare() is not P1 and sc2.prepare().tariff_time_dependent


def test_scenario_json_of_presets_is_unchanged():
    """Neutral tariff: the exact JSON of earlier versions (hashes recorded before the tariff feature)."""
    ref = {
        "grid-small": "5673289db14663b039bf7944c744ff9e46987a057bcc723a8f5a76daa5f17d6c",
        "corridor": "1d63e29a4b84f6bab1b1dc51e08ff7d4ce57ac4bd694776b1eb089cb844376f1",
    }
    for name, digest in ref.items():
        blob = json.dumps(get_scenario(name).to_dict(), ensure_ascii=False).encode()
        assert hashlib.sha256(blob).hexdigest() == digest, name


# ----------------------------------------------------------------------------
# 2. the evaluator: holds, queue at the end of the hold, money of the period
# ----------------------------------------------------------------------------


def test_evaluator_hold_time_queue_and_period_money():
    sc = _peak_line(Tariff(peak_hours=((16, 20),), peak_factor=2.0, high_soc_factor=1.5), queue=True)
    P = sc.prepare()
    assert P.time_dependent and P.tariff_time_dependent
    e1, e2 = 0, 2  # O -> S, S -> D
    assert P.net.head[e1] == 1 and P.net.tail[e2] == 1 and P.net.head[e2] == 2
    t_arr = P.edge_time[0][e1]
    assert P.time_of_day(t_arr) == pytest.approx(19.5 * H)
    y = 0.85 * P.capacity  # crosses the high-SoC breakpoint
    hold_until = t_arr + 1800.0  # 20:00, the end of the peak
    held = evaluate(P, PlanDecision([e1, e2], [0, 0], [ChargeDecision(1, 0, y, hold_until)]))
    at_once = evaluate(P, PlanDecision([e1, e2], [0, 0], [ChargeDecision(1, 0, y)]))
    for p in (held, at_once):
        assert p.feasible, p.message
    st, s0 = held.stops[0], at_once.stops[0]
    e_in = P.e_init - P.edge_energy[0][e1]
    assert st.arrival_s == s0.arrival_s == t_arr and st.energy_in_kwh == e_in
    assert st.hold_s == hold_until - t_arr and st.join_s == pytest.approx(hold_until) and s0.hold_s == 0.0
    # the queue is evaluated when the vehicle joins it
    assert st.wait_s == P.wait_s(0, hold_until) and s0.wait_s == P.wait_s(0, t_arr) and st.wait_s != s0.wait_s
    # money of the period at the time of joining: off-peak after the hold, peak at once
    assert not st.peak and s0.peak
    assert st.money == P.cf[0].money(e_in, y) and s0.money == P.cf_peak[0].money(e_in, y)
    assert s0.money == pytest.approx(2.0 * st.money)
    assert st.charge_s == s0.charge_s == P.cf[0].charge_time(e_in, y)
    # time: t = end of hold; t += wait + setup + charge; then drive
    t = hold_until
    t += st.wait_s + st.setup_s + st.charge_s
    t += P.edge_time[0][e2]
    assert held.total_time_s == t
    assert held.hold_time_s == st.hold_s and at_once.hold_time_s == 0.0
    assert held.total_time_s == pytest.approx(held.drive_time_s + held.hold_time_s + held.wait_time_s
                                              + held.setup_time_s + held.charge_time_s)
    assert st.departure_s == pytest.approx(held.node_depart_s[1])
    assert st.dwell_s == pytest.approx(st.hold_s + st.wait_s + st.setup_s + st.charge_s)
    # the generalised cost pays the hold at the value of time
    want = (P.drive_cost(P.edge_time[0][e1], P.edge_energy[0][e1])
            + P.drive_cost(P.edge_time[0][e2], P.edge_energy[0][e2])
            + P.charge_cost(0, e_in, y, st.charge_s, st.wait_s, join_s=hold_until, hold_s=st.hold_s))
    assert held.generalized_cost == pytest.approx(want, rel=1e-12)
    # a hold that is not after the arrival is no hold
    for h in (t_arr, t_arr - 100.0):
        p = evaluate(P, PlanDecision([e1, e2], [0, 0], [ChargeDecision(1, 0, y, h)]))
        assert p.total_time_s == at_once.total_time_s and p.money == at_once.money and p.hold_time_s == 0.0
    # holding into the peak makes the stop dearer (16:00 from a 15:30 arrival)
    early = _peak_line(sc.tariff, depart_h=15.0, queue=True).prepare()
    p = evaluate(early, PlanDecision([e1, e2], [0, 0], [ChargeDecision(1, 0, y, t_arr + 1800.0)]))
    assert p.stops[0].peak and p.stops[0].money == early.cf_peak[0].money(e_in, y)
    # a non-finite hold is a violation
    p = evaluate(P, PlanDecision([e1, e2], [0, 0], [ChargeDecision(1, 0, y, math.inf)]))
    assert not p.feasible and "hold" in p.message
    # serialised views
    dd = held.to_dict()
    assert dd["hold_time_s"] == held.hold_time_s and dd["summary"]["hold_time_s"] == round(held.hold_time_s, 1)
    assert dd["stops"][0]["hold_s"] == st.hold_s and dd["stops"][0]["peak"] is False
    json.dumps(dd)


def test_monte_carlo_replays_keep_the_holds():
    sc = _peak_line(Tariff(peak_hours=((16, 20),), peak_factor=3.0), queue=True)
    P = sc.prepare()
    p = _run(P, "bls-astar")
    assert p.feasible and p.hold_time_s > 0.0
    hold = p.decision.charges[0].hold_until_s
    assert hold is not None
    rep = monte_carlo(P, p, n=40, seed=3, verify=True)  # raises if a replay disagrees with evaluate()
    assert rep.n == 40
    # no noise, expected waits: the replay is the plan itself
    z = monte_carlo(P, p, n=1, trip_cv=0.0, edge_cv=0.0, wait_model="expected", verify=True)
    assert z.total_time_s.mean == p.total_time_s
    r = replay_run(P, p, 5, seed=3)
    assert r.decision.charges[0].hold_until_s == hold
    assert r.stops[0].hold_s > 0.0 or r.stops[0].arrival_s >= hold
    # a plan rebuilt from its measured stops keeps the hold as well
    q = dataclasses.replace(p, decision=None)
    z2 = monte_carlo(P, q, n=1, trip_cv=0.0, edge_cv=0.0, wait_model="expected")
    assert z2.total_time_s.mean == pytest.approx(p.total_time_s, rel=1e-12)


# ----------------------------------------------------------------------------
# 3. the neutral tariff changes nothing (values recorded before the feature)
# ----------------------------------------------------------------------------

#: (objective value, total time, money, settled, generated, pushes, stops) measured with the
#: code before charging tariffs existed
NEUTRAL_REFERENCE = {
    ("grid-small", "time", "bls"): (6732.458585850733, 6732.458585850733, 10.437117941939277, 4177, 22297, 10911,
                                    [(4, 20.58)]),
    ("grid-small", "time", "bls-astar"): (6732.458585850733, 6732.458585850733, 10.437117941939277, 43, 351, 108,
                                          [(4, 20.58)]),
    ("grid-small", "time", "cfp"): (6728.203427626171, 6728.203427626171, 10.35769449808965, 202, 668, 383,
                                    [(4, 20.471406491318326)]),
    ("grid-small", "time", "cfp-astar"): (6728.203427626171, 6728.203427626171, 10.35769449808965, 40, 109, 68,
                                          [(4, 20.471406491318326)]),
    ("grid-small", "time", "pareto"): (6732.458585850733, 6732.458585850733, 10.437117941939277, 9818, 56154, 15592,
                                       [(4, 20.58)]),
    ("grid-small", "time", "greedy"): (7970.157563280863, 7970.157563280863, 17.187990115256106, 248, 1, 0,
                                       [(10, 39.2)]),
    ("grid-small", "time", "station-graph"): (9388.268400104387, 9388.268400104387, 31.22302219725843, 3, 18, 17,
                                              [(4, 49.0)]),
    ("grid-small", "time", "genetic"): (6728.203427626171, 6728.203427626171, 10.357694498089643, 233, 640, 0,
                                        [(4, 20.471406491318316)]),
    ("grid-small", "cost", "bls"): (40.934035905653666, 7436.36306086741, 7.511793306745465, 26082, 136187, 56949,
                                    [(10, 24.5)]),
    ("grid-small", "cost", "bls-astar"): (40.934035905653666, 7436.36306086741, 7.511793306745465, 248, 1374, 293,
                                          [(10, 24.5)]),
    ("grid-small", "cost", "pareto"): (40.934035905653666, 7436.36306086741, 7.511793306745465, 9818, 56154, 15592,
                                       [(10, 24.5)]),
    ("corridor", "time", "bls-astar"): (24718.7353878479, 24718.7353878479, 55.64007455759229, 3976, 12459, 4250,
                                        [(0, 35.28), (3, 43.12), (5, 40.18)]),
    ("corridor", "time", "cfp-astar"): (24577.687776549443, 24577.687776549443, 55.3831198256206, 280, 578, 530,
                                        [(0, 34.90190472940891), (3, 42.15044831216785), (5, 39.694573432669316)]),
    ("turkey-karaman-istanbul", "time", "bls-astar"): (29216.431270815057, 29216.431270815057, 1138.7764718755511,
                                                       1491, 16775, 2323, [(175, 45.5328), (80, 60.7104)]),
    ("turkey-karaman-istanbul", "time", "cfp-astar"): (29200.801793980936, 29200.801793980936, 1133.959191693709,
                                                       347, 1166, 1125,
                                                       [(175, 45.24909939975695), (80, 60.398107353728854)]),
}


@pytest.mark.parametrize("preset", ["grid-small", "corridor", "turkey-karaman-istanbul"])
def test_neutral_tariff_reproduces_earlier_plans_and_statistics(preset):
    base = get_scenario(preset)
    for objective in ("time", "cost"):
        names = [n for (s, o, n) in NEUTRAL_REFERENCE if s == preset and o == objective]
        if not names:
            continue
        P = base.with_preferences(objective=objective).prepare()
        assert P.cf_peak is P.cf and not P.tariff_time_dependent
        for name in names:
            p = _run(P, name)
            want = NEUTRAL_REFERENCE[(preset, objective, name)]
            got = (p.objective_value, p.total_time_s, p.money, p.stats.settled, p.stats.generated, p.stats.pushes,
                   [(s.station, s.energy_out_kwh) for s in p.stops])
            if sys.version_info >= (3, 12):
                assert got == want, (preset, objective, name)
            else:
                # The reference was recorded on Python >= 3.12, whose sum() of floats is compensated
                # (bpo-100425): older versions differ in the last bits, and float ties can then settle
                # a handful of labels more or fewer.  Same plan, same values up to rounding.
                assert got[:3] == pytest.approx(want[:3], rel=1e-12), (preset, objective, name)
                assert got[3:6] == pytest.approx(want[3:6], rel=1e-3), (preset, objective, name)
                assert [s for s, _ in got[6]] == [s for s, _ in want[6]], (preset, objective, name)
                assert [e for _, e in got[6]] == pytest.approx([e for _, e in want[6]], rel=1e-12)
            assert p.hold_time_s == 0.0 and all(s.hold_s == 0.0 and not s.peak for s in p.stops)
            assert all(c.hold_until_s is None for c in p.decision.charges)


def test_no_op_tariff_gives_the_neutral_plans():
    """Peak hours with factor 1 (and busy hours) do not change any price: identical plans."""
    base = grid_scenario(6, 6, seed=11, initial_soc=0.2, n_stations=8, objective="cost", charge_step_soc=0.1)
    noop = base.with_tariff(peak_hours=((9, 10),), busy_utilisation=0.01)
    for name in ("bls", "bls-astar", "pareto", "cfp"):
        a, b = _run(base.prepare(), name), _run(noop.prepare(), name)
        assert a.decision == b.decision and a.objective_value == b.objective_value
        assert a.stats.settled == b.stats.settled


# ----------------------------------------------------------------------------
# 4. exactness under time-dependent tariffs
# ----------------------------------------------------------------------------


def _random_tariff(rng: random.Random) -> Tariff:
    peaks = rng.choice((((9.3, 9.8),), ((9.4, 10.2), (10.6, 23.0)), ((22, 9.5),), ((9.0, 9.9), (21, 7))))
    return Tariff(
        high_soc=rng.choice((0.7, 0.8)),
        high_soc_factor=rng.choice((1.0, 1.5, 2.5)),
        peak_hours=peaks,
        peak_factor=rng.choice((0.8, 1.6, 2.5)),
        peak_high_soc_factor=rng.choice((None, 1.0, 3.0)),
        busy_utilisation=rng.choice((None, None, 0.05)),
    )


def _random_grid(i: int) -> Scenario:
    rng = random.Random(f"tariff-grid-{i}")
    size = rng.randint(5, 8)
    sc = grid_scenario(size, size, spacing_m=15_000.0, seed=rng.randint(0, 10_000), n_stations=rng.randint(5, 12),
                       initial_soc=round(rng.uniform(0.12, 0.3), 3), charge_step_soc=0.05,
                       time_dependent_waits=i % 3 != 0, value_of_time_per_h=rng.choice((6.0, 15.0, 40.0)))
    st = list(sc.stations)
    if i % 4 == 1:  # a cheap slow AC charger next to a DC one
        st.append(Station(id=len(st), node=st[0].node, power_kw=11.0, kind="AC", price_per_kwh=0.2,
                          daily_sessions=4.0, demand_profile="urban", setup_s=60.0))
    return sc.with_(stations=st, tariff=_random_tariff(rng))


def test_bls_astar_and_pareto_extremes_match_bls_under_time_dependent_tariffs():
    held = nontrivial = 0
    for i in range(10):
        sc = _random_grid(i)
        Pt = sc.with_preferences(objective="time").prepare()
        Pc = sc.with_preferences(objective="cost").prepare()
        assert Pc.tariff_time_dependent
        bt, bc = _run(Pt, "bls"), _run(Pc, "bls")
        at, ac = _run(Pt, "bls-astar"), _run(Pc, "bls-astar")
        par = _run(Pc, "pareto", criteria=("time", "money", "wear"), max_labels=600_000)
        assert bt.feasible == bc.feasible == at.feasible == ac.feasible == par.feasible, i
        if not bt.feasible:
            continue
        for p in (bt, bc, at, ac, par):
            _assert_consistent(p)
        assert bc.stats.extra["dominance"].startswith("3d")
        assert at.objective_value == bt.objective_value
        assert _close(ac.objective_value, bc.objective_value), (i, ac.objective_value, bc.objective_value)
        assert par.stats.extra["complete"], i
        assert _close(par.alternatives[0].total_time_s, bt.total_time_s), i
        assert _close(min(m.generalized_cost for m in par.alternatives), bc.objective_value), i
        assert bt.hold_time_s == at.hold_time_s == 0.0 and par.alternatives[0].hold_time_s == 0.0
        for m in par.alternatives:
            assert m.feasible and check_objective(m)
        held += any(m.hold_time_s > 0.0 for m in par.alternatives) + (bc.hold_time_s > 0.0)
        nontrivial += len(par.alternatives) >= 3
    assert held >= 6 and nontrivial >= 6


def _oracle_targets(P, s):
    """Target set re-derived from the scenario (``grid+breaks``), not from the planner code."""
    cap = P.e_charge_cap
    vals = [min(y, cap) for y in P.target_grid if y <= cap + 1e-9]
    vals += [b for b in P.cf[s].e_breaks[1:-1] if b <= cap]
    out = []
    for y in sorted(vals):
        if y > 0 and not (out and y - out[-1] <= 1e-9):
            out.append(y)
    return out


def brute_force(P, *, stops_per_visit=2, max_visits=1, extra_holds_s=(1800.0, 5400.0)):
    """Exhaustive enumeration with the evaluator's rules.

    Walks visit every node at most ``max_visits`` times; at a station node up
    to ``stops_per_visit`` stops at any station (the same one again included),
    each to any target of the target set, joining the queue at once or after
    a hold until any price change of the station in ``(t, t + 24 h]`` *or*
    ``extra_holds_s`` seconds after the arrival -- holds to times that are
    not price changes must never be better.  A partial route is cut only if
    a complete route is no worse in time, money and wear (every transition
    increases the time and none decreases money or wear).  Returns ``(best
    objective, best decision, non-dominated (t, m, w))`` with the objective of
    ``P`` (``None`` values if no route exists).
    """
    net = P.net
    time_obj = P.objective == "time"
    tg = {s: _oracle_targets(P, s) for s in range(len(P.stations))}
    visits = [0] * net.num_nodes
    visits[P.origin] = 1
    pts = []
    best = [INF, None]
    dsoh = P.drive_soh_per_kwh

    def holds(s, t):
        out = {t}
        out.update(P.hold_candidates(s, t))
        out.update(t + h for h in extra_holds_s)
        return sorted(out)

    def rec(v, t, m, w, e, edges, levels, charges, stops_here):
        for q in pts:
            if q[0] <= t and q[1] <= m and q[2] <= w:
                return
        if v == P.destination and e >= P.e_dest_min - EPS:
            pts.append((t, m, w))
            value = t if time_obj else P.vot_per_s * t + P.w_money * m + P.w_deg * w
            if value < best[0]:
                best[0] = value
                best[1] = PlanDecision(list(edges), list(levels), [ChargeDecision(*c) for c in charges])
            return
        if stops_here < stops_per_visit:
            for s in P.station_at.get(v, []):
                f = P.cf[s]
                for tj in holds(s, t):
                    fm = P.money_cf(s, tj)
                    wq = P.wait_s(s, tj)
                    for y in tg[s]:
                        if y > e + EPS:
                            ch = f.time_at(y) - f.time_at(e)
                            rec(v, tj + (wq + P.setup_s[s] + ch), m + fm.money(e, y), w + f.soh(e, y), y,
                                edges, levels, charges + [(len(edges), s, y, tj if tj > t else None)],
                                stops_here + 1)
        if e >= P.e_min - EPS:
            for a in net.out_edges[v]:
                u = net.head[a]
                if visits[u] >= max_visits:
                    continue
                for k, tau, c, _dc in P.edge_options[a]:
                    ne = min(e - c, P.capacity)
                    if ne < P.e_min - EPS:
                        continue
                    visits[u] += 1
                    edges.append(a)
                    levels.append(k)
                    rec(u, t + tau, m, w + (dsoh * c if c > 0 else 0.0), ne, edges, levels, charges, 0)
                    edges.pop()
                    levels.pop()
                    visits[u] -= 1

    rec(P.origin, 0.0, 0.0, 0.0, P.e_init, [], [], [], 0)
    return (best[0] if best[1] is not None else None), best[1], _front_of(pts)


def _front_of(points):
    out = []
    for p in sorted(set(points)):
        if any(all(a <= b * (1 + 1e-9) + 1e-12 for a, b in zip(q, p)) for q in out):
            continue
        out = [q for q in out if not all(a <= b for a, b in zip(p, q))]
        out.append(p)
    return out


def _tiny(seed: int) -> Scenario:
    """O -> A -> D and O -> B -> D with chargers at A (one or two), B and sometimes O;
    random prices, peak hours around the trip, a simple non-concave charging curve."""
    rng = random.Random(f"tariff-tiny-{seed}")
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, 100.0)
    a = b.add_node(rng.uniform(40e3, 80e3), 10e3, rng.choice((100.0, 700.0)))
    m = b.add_node(rng.uniform(40e3, 80e3), -10e3, rng.choice((100.0, 400.0)))
    d = b.add_node(rng.uniform(150e3, 200e3), 0.0, 100.0)
    for u, v in ((o, a), (a, d), (o, m), (m, d)):
        b.add_road(u, v, road_class=rng.choice(("motorway", "trunk")))
    at = [a, a] if seed % 4 == 1 else [a]
    at += [m] + ([o] if seed % 4 == 3 else [])
    st = []
    for v in at:
        kind = rng.choice(("DC", "DC", "AC"))
        st.append(Station(len(st), v, rng.choice((50.0, 150.0)) if kind == "DC" else 22.0, kind,
                          plugs=rng.choice((1, 2, 4)), price_per_kwh=round(rng.uniform(0.3, 0.7), 2),
                          daily_sessions=rng.choice((0.0, 20.0, 40.0)), setup_s=rng.choice((60.0, 180.0))))
    veh = dataclasses.replace(get_vehicle("compact50"),
                              dc_curve=ChargingCurve((0.0, 0.1, 0.75, 1.0), (40.0, 60.0, 25.0)))
    nodes = [o, d]
    depart = rng.choice((14.0, 15.5, 18.5, 19.25, 21.0))
    tariff = Tariff(
        high_soc=rng.choice((0.7, 0.8)),
        high_soc_factor=rng.choice((1.0, 1.5, 3.0)),
        peak_hours=rng.choice((((16, 20),), ((17, 19.75), (22, 6)), ((15, 16.5), (19.5, 21)))),
        peak_factor=rng.choice((1.5, 2.0, 3.0)),
        peak_high_soc_factor=rng.choice((None, 1.0)),
        busy_utilisation=rng.choice((None, None, 0.4)),
    )
    prefs = Preferences(
        objective="cost", value_of_time_per_h=rng.choice((8.0, 20.0, 60.0)), charge_step_soc=0.1,
        queue_aware=rng.random() < 0.6, time_dependent_waits=rng.random() < 0.6,
        speed_factors=(1.0, 0.85) if seed % 5 == 4 else (1.0,),
    )
    soc = round(rng.uniform(0.3, 0.55), 2) if seed % 4 != 3 else 0.08  # almost empty: charge at the origin
    return Scenario(f"tiny-{seed}", b.build(), st, veh,
                    Query(nodes[0], nodes[-1], initial_soc=soc, departure_s=depart * H),
                    Conditions(temperature_c=rng.choice((20.0, 5.0))), prefs, tariff=tariff)


def _stops_per_visit(p):
    visits = {}
    for c in p.decision.charges:
        visits[c.position] = visits.get(c.position, 0) + 1
    return max(visits.values(), default=0)


def _check_against_brute_force(seed: int) -> tuple[int, int]:
    """BLS, BLS-A* and the Pareto front against :func:`brute_force` on ``_tiny(seed)``;
    returns (front size, front members that hold)."""
    P = _tiny(seed).prepare()
    assert P.tariff_time_dependent
    value, decision, front = brute_force(P)
    plans = {name: _run(P, name) for name in ("bls", "bls-astar")}
    par = _run(P, "pareto")
    if value is None:
        assert not any(p.feasible for p in plans.values()) and not par.feasible
        return 0, 0
    for name, p in plans.items():
        _assert_consistent(p)
        # exact over a superset of the enumerated plans; equal when its plan is enumerated
        assert p.objective_value <= value * (1 + 1e-9) + 1e-9, (seed, name, p.objective_value, value)
        if _stops_per_visit(p) <= 2:
            assert _close(p.objective_value, value), (seed, name, p.objective_value, value)
    bf = evaluate(P, decision)
    assert bf.feasible and _close(bf.objective_value, value)

    def crit(m):
        return m.total_time_s, m.money, m.soh_total_pct

    def same(a, b):
        return all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) for x, y in zip(a, b))

    def covers(a, b):
        return all(x <= y + 1e-9 * abs(y) + 1e-12 for x, y in zip(a, b))

    assert par.stats.extra["complete"]
    got = [crit(m) for m in par.alternatives]
    for o in front:  # every enumerated Pareto point is reached (or beaten) by the planner
        assert any(covers(g, o) for g in got), (seed, o, got)
    for m, g in zip(par.alternatives, got):  # and every member inside the enumerated space is enumerated
        if _stops_per_visit(m) <= 2:
            assert any(same(g, o) or covers(o, g) for o in front), (seed, g, front)
    return len(front), sum(m.hold_time_s > 0.0 for m in par.alternatives)


def test_planners_match_brute_force_with_holds_on_tiny_instances():
    sizes = held = 0
    for seed in (s for s in range(16) if s % 4 != 3):
        n, h = _check_against_brute_force(seed)
        sizes += n >= 3
        held += h > 0
    assert sizes >= 5 and held >= 4


@pytest.mark.slow
@pytest.mark.parametrize("seed", [3, 7, 11, 15, 19, 23])
def test_planners_match_brute_force_with_an_origin_charger_slow(seed):
    """Origin charger with an almost empty battery: up to two stops at the origin and two at the next charger."""
    _check_against_brute_force(seed)


def test_cost_optimum_holds_until_the_peak_ends():
    sc = _peak_line(Tariff(peak_hours=((16, 20),), peak_factor=3.0))
    P = sc.prepare()
    plans = [_run(P, name) for name in ("bls", "bls-astar")]
    par = _run(P, "pareto")
    for p in plans:
        _assert_consistent(p)
        assert p.num_stops == 1
        st = p.stops[0]
        assert P.time_of_day(st.arrival_s) == pytest.approx(19.5 * H)
        assert st.hold_s == pytest.approx(1800.0) and P.time_of_day(st.join_s) == pytest.approx(20 * H)
        assert not st.peak and st.money == pytest.approx(P.cf[0].money(st.energy_in_kwh, st.energy_out_kwh))
    assert plans[0].objective_value == pytest.approx(plans[1].objective_value, rel=1e-12)
    assert min(m.generalized_cost for m in par.alternatives) == pytest.approx(plans[0].objective_value, rel=1e-9)
    value, _, _ = brute_force(P, stops_per_visit=1, extra_holds_s=(600.0, 1200.0, 2400.0, 7200.0))
    assert value == pytest.approx(plans[0].objective_value, rel=1e-9)
    # the fastest route charges at once, in the peak, and it is on the front as well
    fast = _run(sc.with_preferences(objective="time").prepare(), "bls")
    assert fast.hold_time_s == 0.0 and fast.stops[0].peak
    assert par.alternatives[0].total_time_s == pytest.approx(fast.total_time_s)
    assert par.alternatives[0].money > 2.5 * plans[0].money
    # without the peak price the optimum does not wait
    plain = _run(sc.with_(tariff=Tariff()).prepare(), "bls")
    assert plain.hold_time_s == 0.0 and plain.objective_value < plans[0].objective_value


def test_cost_optimum_charges_to_exactly_80_percent_and_stops_again():
    neutral = _two_station_line()
    p0 = _run(neutral.prepare(), "bls")
    assert p0.num_stops == 1 and p0.stops[0].soc_out > 0.8 + 1e-9  # one stop above 80 % without the tier
    sc = neutral.with_tariff(high_soc_factor=3.0)
    P = sc.prepare()
    assert 0.8 * P.capacity in P.cf[0].e_breaks  # the tier is a breakpoint, hence a target
    plans = [_run(P, name) for name in ("bls", "bls-astar")]
    for p in plans:
        _assert_consistent(p)
        assert [s.station for s in p.stops] == [0, 1]
        assert p.stops[0].energy_out_kwh == 0.8 * P.capacity  # exactly 80 %
        assert p.stops[1].soc_out <= 0.8 + 1e-12  # the second stop stays below the tier as well
    assert plans[0].objective_value == pytest.approx(plans[1].objective_value, rel=1e-12)
    par = _run(P, "pareto")
    assert min(m.generalized_cost for m in par.alternatives) == pytest.approx(plans[0].objective_value, rel=1e-9)
    value, _, _ = brute_force(P, stops_per_visit=1)
    assert value == pytest.approx(plans[0].objective_value, rel=1e-9)
    # paying the tier instead (the neutral plan) costs more under the tariff
    assert evaluate(P, p0.decision).generalized_cost > plans[0].objective_value


def test_cost_optimum_restarts_at_the_same_station_when_the_peak_ends():
    """Charge in the peak until it ends, then join the queue again for the off-peak price.

    A second stop at the same station pays the set-up again but is priced in the new
    period; ``evaluate()`` accepts it, so the exact planners must consider it."""
    sc = _peak_line(Tariff(peak_hours=((16, 20),), peak_factor=3.0), vot=60.0)
    P = sc.prepare()
    plans = [_run(P, name) for name in ("bls", "bls-astar")]
    for p in plans:
        _assert_consistent(p)
        assert [s.station for s in p.stops] == [0, 0]
        first, second = p.stops
        assert first.peak and not second.peak and first.hold_s == 0.0
        assert P.time_of_day(second.join_s) == pytest.approx(20 * H)
    assert plans[0].objective_value == pytest.approx(plans[1].objective_value, rel=1e-12)
    par = _run(P, "pareto")
    assert min(m.generalized_cost for m in par.alternatives) == pytest.approx(plans[0].objective_value, rel=1e-9)
    value, _, _ = brute_force(P, extra_holds_s=(900.0, 2400.0))
    assert value == pytest.approx(plans[0].objective_value, rel=1e-9)
    # better than both single-stop alternatives: charging all in the peak or waiting for 20:00
    y = plans[0].stops[-1].energy_out_kwh
    path = list(plans[0].edges)
    at_once = evaluate(P, PlanDecision(path, [0] * len(path), [ChargeDecision(1, 0, y)]))
    waiting = evaluate(P, PlanDecision(path, [0] * len(path), [ChargeDecision(1, 0, y, plans[0].stops[1].join_s)]))
    assert plans[0].objective_value < min(at_once.generalized_cost, waiting.generalized_cost)


def test_time_objective_never_holds():
    for i in range(4):
        sc = _random_grid(i).with_preferences(objective="time")
        P = sc.prepare()
        # the same charging functions (breakpoints, powers) with clock-independent prices
        neutral = sc.with_(tariff=Tariff()).prepare()
        tiered = any(len(a.e_breaks) != len(b.e_breaks) for a, b in zip(P.cf, neutral.cf))
        P0 = sc.with_(tariff=Tariff(high_soc=sc.tariff.high_soc, high_soc_factor=1.5 if tiered else 1.0)).prepare()
        assert P.tariff_time_dependent and not P0.tariff_time_dependent
        assert [f.e_breaks for f in P.cf] == [f.e_breaks for f in P0.cf]
        for name in ("bls", "bls-astar", "cfp", "cfp-astar", "pareto"):
            p = _run(P, name)
            if not p.feasible:
                continue
            assert p.hold_time_s == 0.0 and all(c.hold_until_s is None for c in p.decision.charges), (i, name)
            assert check_objective(p)
            # prices do not enter the time objective: the same optimum as with fixed prices
            q = _run(P0, name)
            assert _close(p.total_time_s, q.total_time_s, 1e-12, 1e-9), (i, name)
            # its money is measured in the right period
            for s in p.stops:
                assert s.money == pytest.approx(P.money_cf(s.station, s.join_s).money(s.energy_in_kwh,
                                                                                     s.energy_out_kwh))


def test_heuristics_report_the_money_of_the_right_period():
    sc = _random_grid(1).with_preferences(objective="cost")
    P = sc.prepare()
    for name in ("greedy", "station-graph", "genetic"):
        p = _run(P, name)
        if not p.feasible:
            continue
        assert p.hold_time_s == 0.0 and check_objective(p)
        for s in p.stops:
            fm = P.cf_peak[s.station] if P.is_peak(s.station, s.arrival_s) else P.cf[s.station]
            assert s.money == fm.money(s.energy_in_kwh, s.energy_out_kwh)
            assert s.peak == P.is_peak(s.station, s.arrival_s)


def test_viewer_payload_carries_the_tariff_only_when_it_is_not_neutral():
    from evroute.viz.export import viewer_payload

    base = _peak_line()
    d = viewer_payload(base, [], robustness_runs=0)
    assert "tariff" not in d["scenario"] and "peak_s" not in d["stations"][0]
    sc = base.with_tariff(peak_hours=((16, 20),), peak_factor=2.0, high_soc_factor=1.5)
    p = _run(sc.prepare(), "bls-astar")
    d = viewer_payload(sc, [p], robustness_runs=0)
    assert d["scenario"]["tariff"] == sc.tariff.to_dict()
    assert d["stations"][0]["peak_s"] == [[16 * H, 20 * H]]
    assert d["stations"][0]["price_factors"] == {"offpeak": [1.0, 1.5], "peak": [2.0, 3.0]}
    st = d["results"][0]["stops"][0]
    assert "hold_s" in st and "peak" in st and "hold_time_s" in d["results"][0]
    json.dumps(d)
