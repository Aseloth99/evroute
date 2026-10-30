"""Tests of the 1.0 vehicle model: cell chemistry, DC power limit, connectors.

* chemistry: validation, the wear model per chemistry and the LFP presets;
* ``dc_max_kw``: the capped curve, the exact charging functions it yields,
  "never more power", plans that cannot get faster, an inactive cap that
  changes nothing;
* connectors: :meth:`Station.usable_by`, the station tables of
  :class:`PreparedScenario`, every one of the nine planners on instances
  where an AC-only car and a CHAdeMO car must leave the other stations aside
  (and still find a route), the evaluator's report, the bounds;
* the exact planners agree with each other on the new presets;
* JSON round trips, the API body fields and the viewer payload.
"""

from __future__ import annotations

import dataclasses
import json
import math
import random

import pytest

from evroute import Scenario, Station
from evroute.algorithms import available, plan
from evroute.charging import build_charging_function
from evroute.evaluate import check_objective, evaluate
from evroute.generators import grid_scenario
from evroute.generators.osm_turkey import osm_connectors, osm_turkey_stations
from evroute.plan import ChargeDecision, PlanDecision
from evroute.server import PlanRequestError, build_plan_scenario
from evroute.vehicle import CHEMISTRIES, CONNECTORS, VEHICLES, AgingModel, ChargingCurve, Vehicle, get_vehicle
from evroute.viz.export import results_payload, viewer_payload

NEW_PRESETS = ("hatch51_lfp", "sedan60_lfp", "suv60_lfp", "city23_ac", "hatch40_chademo")
OLD_PRESETS = ("city27", "compact50", "sedan75", "suv84_800v", "togg_t10x_lr", "van75")


# ====================================================================== chemistry
def test_presets_state_their_chemistry():
    assert set(VEHICLES) == set(OLD_PRESETS) | set(NEW_PRESETS)
    assert {VEHICLES[k].chemistry for k in OLD_PRESETS} == {"NMC"}
    assert {k for k, v in VEHICLES.items() if v.chemistry == "LFP"} == {"hatch51_lfp", "sedan60_lfp", "suv60_lfp"}
    for v in VEHICLES.values():
        assert v.chemistry in CHEMISTRIES
        assert v.notes or v.key in OLD_PRESETS


def test_chemistry_is_validated():
    v = get_vehicle("compact50")
    for bad in ("LiPo", "lfp", "", None):
        with pytest.raises(ValueError, match="chemistry"):
            v.with_(chemistry=bad)
    assert v.with_(chemistry="NCA").chemistry == "NCA"
    with pytest.raises(ValueError, match="chemistry"):
        AgingModel.for_chemistry("NiMH")


def test_aging_per_chemistry():
    assert AgingModel.for_chemistry("NMC") == AgingModel() == AgingModel.for_chemistry("NCA")
    lfp, nmc = AgingModel.for_chemistry("LFP"), AgingModel()
    assert AgingModel.for_chemistry("LFP", k_heat=0.1).k_heat == 0.1
    assert 3000 <= lfp.cycle_life_efc <= 4000 and lfp.cycle_life_efc > 2 * nmc.cycle_life_efc

    def fade(m, **kw):
        args = {"capacity_kwh": 60.0, "c_rate": 0.5, "soc": 0.5, "temp_c": 20.0, **kw}
        return m.fade_pct_per_kwh(**args)

    # longer cycle life: less wear per kWh at benign conditions
    assert fade(lfp) < fade(nmc)
    # a full battery stresses LFP much less, the cold stresses it more
    extra_lfp, extra_nmc = fade(lfp, soc=0.98) / fade(lfp) - 1.0, fade(nmc, soc=0.98) / fade(nmc) - 1.0
    assert 0.0 < extra_lfp == pytest.approx(0.2 * extra_nmc)
    assert fade(lfp, temp_c=-10.0) / fade(lfp) > fade(nmc, temp_c=-10.0) / fade(nmc)


def test_lfp_presets_derate_harder_in_the_cold():
    base = Vehicle.__dataclass_fields__
    for key in ("hatch51_lfp", "sedan60_lfp", "suv60_lfp"):
        v = get_vehicle(key)
        assert v.aging == AgingModel.for_chemistry("LFP")
        assert v.cold_capacity_loss_per_deg > base["cold_capacity_loss_per_deg"].default
        assert v.cold_charge_loss_per_deg > base["cold_charge_loss_per_deg"].default
        assert v.min_cold_charge_factor < base["min_cold_charge_factor"].default
        ref = get_vehicle("compact50")
        assert v.usable_capacity_kwh(-10.0) / v.battery_kwh < ref.usable_capacity_kwh(-10.0) / ref.battery_kwh
        assert v.dc_temperature_factor(-10.0, False) < ref.dc_temperature_factor(-10.0, False)
        assert v.dc_temperature_factor(-10.0, True) == 1.0


def test_new_presets_match_their_description():
    peaks = {k: get_vehicle(k).dc_peak_kw for k in NEW_PRESETS}
    assert 110 <= peaks["hatch51_lfp"] <= 120 and get_vehicle("hatch51_lfp").ac_max_kw == 11.0
    assert 160 <= peaks["sedan60_lfp"] <= 175
    assert 85 <= peaks["suv60_lfp"] <= 90
    assert peaks["city23_ac"] == 0.0 and get_vehicle("city23_ac").ac_max_kw == 7.4
    assert 40 <= peaks["hatch40_chademo"] <= 50
    assert get_vehicle("city23_ac").connectors == ("Type2",) and not get_vehicle("city23_ac").dc_capable
    assert get_vehicle("hatch40_chademo").connectors == ("Type2", "CHAdeMO")
    assert get_vehicle("hatch40_chademo").aging.k_heat > AgingModel().k_heat
    # the LFP sedan tapers early: below half its peak at 70 % SoC, the long plateau of the SUV holds to 50 %
    sedan = get_vehicle("sedan60_lfp").dc_curve
    assert sedan.power_at(0.72) < 0.5 * sedan.peak_kw
    suv = get_vehicle("suv60_lfp").dc_curve
    assert min(suv.power_at(s / 100) for s in range(20, 50)) >= 0.97 * suv.peak_kw


# ====================================================================== DC limit
def test_dc_max_kw_is_validated():
    v = get_vehicle("sedan75")
    for bad in (0.0, -50.0, math.nan, math.inf, True, "50"):
        with pytest.raises(ValueError, match="dc_max_kw"):
            v.with_(dc_max_kw=bad)
    assert v.with_(dc_max_kw=50).dc_max_kw == 50


def test_capped_curve_never_raises_power():
    rng = random.Random(7)
    for v in VEHICLES.values():
        curve = v.dc_curve
        assert curve.capped(None) is curve and curve.capped(curve.peak_kw) is curve
        assert curve.capped(curve.peak_kw + 1.0) is curve
        for _ in range(5):
            cap = rng.uniform(5.0, curve.peak_kw * 1.2)
            c = curve.capped(cap)
            assert c.soc_breaks == curve.soc_breaks
            assert all(p2 == min(p, cap) for p, p2 in zip(curve.power_kw, c.power_kw))
            assert all(p2 <= p for p, p2 in zip(curve.power_kw, c.power_kw))
            w = v.with_(dc_max_kw=cap)
            assert w.dc_curve_effective == c
            assert w.dc_peak_kw == (min(cap, curve.peak_kw) if v.dc_capable else 0.0)


def _line_scenario(vehicle: Vehicle, *, temperature_c: float = 20.0, preconditioning: bool = True) -> Scenario:
    sc = grid_scenario(rows=4, cols=4, spacing_m=10000.0, n_stations=6, vehicle="compact50", seed=3, initial_soc=0.3,
                       temperature_c=temperature_c)
    st = []
    for i, s in enumerate(sc.stations):
        s = dataclasses.replace(s, kind="AC" if i % 2 else "DC", power_kw=22.0 if i % 2 else (50.0, 150.0, 350.0)[i % 3])
        st.append(s)
    return sc.with_(stations=st, vehicle=vehicle).with_conditions(preconditioning=preconditioning)


@pytest.mark.parametrize("cold", [False, True])
def test_dc_cap_builds_exactly_the_capped_charging_functions(cold):
    veh = get_vehicle("sedan60_lfp")
    cap = 60.0
    kw = {"temperature_c": -5.0, "preconditioning": False} if cold else {}
    P0 = _line_scenario(veh, **kw).prepare()
    P1 = _line_scenario(veh.with_(dc_max_kw=cap), **kw).prepare()
    tf = veh.dc_temperature_factor(P1.conditions.temperature_c, P1.conditions.preconditioning)
    assert (tf < 1.0) == cold
    q = P1.capacity
    for s, st in enumerate(P1.stations):
        f0, f1 = P0.cf[s], P1.cf[s]
        if st.kind == "AC":
            assert f1.power_kw == f0.power_kw and f1.e_breaks == f0.e_breaks
            continue
        curve = veh.dc_curve
        expect = [max(min(min(p, cap), st.power_kw) * tf, 0.5) for p in curve.power_kw]
        assert f1.power_kw == expect
        ref = build_charging_function(q, curve.soc_breaks, [min(p, cap) for p in curve.power_kw], st.power_kw, tf,
                                      st.price_per_kwh, veh.charge_eff_dc,
                                      lambda c, soc: veh.aging.fade_pct_per_kwh(q, c, soc, P1.conditions.temperature_c))
        assert (f1.e_breaks, f1.power_kw, f1.money_per_kwh, f1.soh_pct_per_kwh) == (
            ref.e_breaks, ref.power_kw, ref.money_per_kwh, ref.soh_pct_per_kwh)
        assert all(a <= b for a, b in zip(f1.power_kw, f0.power_kw))
        for lo, hi in ((0.1 * q, 0.8 * q), (0.05 * q, 0.5 * q), (0.5 * q, q)):
            assert f1.charge_time(lo, hi) >= f0.charge_time(lo, hi)
    assert P1.p_max <= P0.p_max


def test_inactive_cap_changes_nothing():
    veh = get_vehicle("compact50")
    sc0 = _line_scenario(veh)
    sc1 = _line_scenario(veh.with_(dc_max_kw=veh.dc_curve.peak_kw))
    assert sc1.vehicle.dc_curve_effective is sc1.vehicle.dc_curve
    for algo in ("bls-astar", "cfp-astar", "greedy"):
        a, b = plan(sc0, algo), plan(sc1, algo)
        assert (a.edges, a.levels, a.total_time_s, a.money) == (b.edges, b.levels, b.total_time_s, b.money)
        assert [(s.station, s.energy_out_kwh) for s in a.stops] == [(s.station, s.energy_out_kwh) for s in b.stops]


@pytest.mark.parametrize("cap", [120.0, 60.0, 25.0])
def test_a_cap_never_makes_the_optimum_faster(cap):
    for key in ("sedan60_lfp", "suv84_800v"):
        veh = get_vehicle(key)
        free = plan(_line_scenario(veh), "cfp-astar")
        capped = plan(_line_scenario(veh.with_(dc_max_kw=cap)), "cfp-astar")
        assert free.feasible and capped.feasible
        assert capped.total_time_s >= free.total_time_s - 1e-6
        for s in capped.stops:
            if s.station_kind == "DC" and s.charge_s > 0:
                assert s.power_kw <= cap + 1e-9


# ====================================================================== connectors
def test_connector_vocabulary_and_validation():
    assert set(CONNECTORS) == {"Type2", "Type1", "CCS2", "CCS1", "CHAdeMO", "NACS"}
    v = get_vehicle("compact50")
    for bad in (("Type2", "ccs2"), ("Schuko",), "CCS2", ()):
        with pytest.raises(ValueError):
            v.with_(connectors=bad)
    assert v.with_(connectors=["Type2", "NACS"]).connectors == ("Type2", "NACS")
    sc = _line_scenario(v)
    sc.stations[0].connectors = ("CCS3",)
    with pytest.raises(ValueError, match="connector"):
        sc.validate()
    sc.stations[0].connectors = ()
    sc.stations[0].kind = "HPC"
    with pytest.raises(ValueError, match="kind"):
        sc.validate()


def test_station_usable_by():
    ac = Station(0, 0, 22.0, kind="AC")
    dc = Station(1, 0, 150.0, kind="DC")
    mixed = Station(2, 0, 120.0, kind="DC", connectors=("Type2", "CCS2", "CHAdeMO"))
    chademo = Station(3, 0, 50.0, kind="DC", connectors=("CHAdeMO",))
    nacs = Station(4, 0, 250.0, kind="DC", connectors=("NACS",))
    assert ac.sockets == ("Type2",) and dc.sockets == ("CCS2",)
    default, ac_only, leaf = ("Type2", "CCS2"), ("Type2",), ("Type2", "CHAdeMO")
    assert [s.usable_by(default) for s in (ac, dc, mixed, chademo, nacs)] == [True, True, True, False, False]
    # a Type 2 socket at a DC station does not make it an AC station
    assert [s.usable_by(ac_only) for s in (ac, dc, mixed, chademo, nacs)] == [True, False, False, False, False]
    assert [s.usable_by(leaf) for s in (ac, dc, mixed, chademo, nacs)] == [True, False, True, True, False]
    assert nacs.usable_by(("NACS",)) and Station(5, 0, 11.0, kind="AC", connectors=("NACS",)).usable_by(("NACS",))
    assert not Station(6, 0, 11.0, kind="AC", connectors=("Type1",)).usable_by(default)


def _mixed_instance(seed: int, vehicle: str) -> Scenario:
    """A grid with three kinds of stations: AC 22 kW, DC 150 kW CCS2 only, DC 50 kW multi-standard
    (Type 2 + CCS2 + CHAdeMO), and one node with a DC and an AC station."""
    sc = grid_scenario(rows=8, cols=8, spacing_m=6000.0, n_stations=18, vehicle=vehicle, seed=seed, initial_soc=0.35)
    st = []
    for i, s in enumerate(sc.stations):
        if i % 3 == 0:
            s = dataclasses.replace(s, kind="AC", power_kw=22.0, connectors=())
        elif i % 3 == 1:
            s = dataclasses.replace(s, kind="DC", power_kw=150.0, connectors=())
        else:
            s = dataclasses.replace(s, kind="DC", power_kw=50.0, connectors=("Type2", "CCS2", "CHAdeMO"))
        st.append(s)
    st.append(dataclasses.replace(st[1], id=len(st), kind="AC", power_kw=22.0, connectors=()))
    return sc.with_(stations=st)


def test_prepared_station_tables_keep_indices_and_drop_unusable_stations():
    for key in ("compact50", "city23_ac", "hatch40_chademo"):
        sc = _mixed_instance(0, key)
        P = sc.prepare()
        assert len(P.cf) == len(P.stations) == len(P.setup_s) == len(P.wait_profiles) == len(P.station_usable) == 19
        expect = [s.usable_by(sc.vehicle.connectors) for s in sc.stations]
        assert P.station_usable == expect
        listed = sorted(i for lst in P.station_at.values() for i in lst)
        assert listed == [i for i, u in enumerate(expect) if u]
        for v, lst in P.station_at.items():
            assert all(P.stations[i].node == v for i in lst) and P.is_station_node[v]
        assert sum(P.is_station_node) == len(P.station_at)
        shared = sc.stations[1].node  # the node with a DC and an AC station
        assert P.station_at[shared] == ([1, 18] if key == "compact50" else [18])
    assert sum(_mixed_instance(0, "compact50").prepare().station_usable) == 19
    assert sum(_mixed_instance(0, "city23_ac").prepare().station_usable) == 7
    assert sum(_mixed_instance(0, "hatch40_chademo").prepare().station_usable) == 13


@pytest.mark.parametrize("seed", [0, 1, 3])
def test_every_planner_respects_the_connectors(seed):
    """An AC-only car never stops at a DC station, a CHAdeMO car never at a CCS-only one,
    with each of the nine planners -- and both still reach the destination."""
    allowed = {"city23_ac": {("AC", 22.0)}, "hatch40_chademo": {("AC", 22.0), ("DC", 50.0)}}
    default_dc = False
    for key in ("compact50", "city23_ac", "hatch40_chademo"):
        sc = _mixed_instance(seed, key)
        P = sc.prepare()
        for algo in available():
            p = plan(sc, algo)
            assert p.feasible or algo == "naive", (key, algo, p.message)
            for s in p.stops:
                assert P.station_usable[s.station], (key, algo, s.station)
                if key in allowed:
                    assert (s.station_kind, s.station_power_kw) in allowed[key], (key, algo, s.station_kind)
                else:
                    default_dc |= s.station_kind == "DC"
            if p.feasible:
                assert not evaluate(P, p.decision).violations
                assert check_objective(p)
        assert any(plan(sc, a).stops for a in ("bls-astar", "cfp-astar")), key  # charging is needed
    assert default_dc  # the default car prefers DC: the filter changes something


def test_the_evaluator_reports_a_stop_at_an_unusable_station():
    sc = _mixed_instance(0, "compact50")
    p = plan(sc, "bls-astar")
    dc = [s for s in p.stops if s.station_kind == "DC"]
    assert p.feasible and dc
    ac_only = sc.with_(vehicle=get_vehicle("city23_ac")).prepare()
    replay = evaluate(ac_only, p.decision)
    assert not replay.feasible
    assert any("no DC connector of the vehicle" in m for m in replay.violations)
    # the stop is still replayed, so the numbers show what the forced plan would do
    assert len(replay.stops) == len(p.stops)


def test_bounds_only_cover_usable_stations():
    P = _mixed_instance(0, "city23_ac").prepare()
    assert P.p_max == pytest.approx(7.4)
    assert all(f in [P.cf[i] for i, u in enumerate(P.station_usable) if u] for f in P.cf_variants)
    assert P.summary()["stations_usable"] == 7
    assert "7 usable" in P.stations_note()
    Pd = _mixed_instance(0, "compact50").prepare()
    assert Pd.cf_variants is Pd.cf and Pd.stations_note() == "19 station(s)"


def test_default_connectors_keep_every_plan():
    """Listing more connectors than the stations offer changes nothing."""
    sc = _mixed_instance(1, "compact50")
    wide = sc.with_(vehicle=sc.vehicle.with_(connectors=("Type2", "CCS2", "CHAdeMO", "Type1", "CCS1")))
    assert wide.prepare().station_at == sc.prepare().station_at
    for algo in ("greedy", "station-graph", "bls-astar", "cfp-astar", "pareto"):
        a, b = plan(sc, algo), plan(wide, algo)
        assert (a.edges, a.total_time_s, [s.station for s in a.stops]) == (b.edges, b.total_time_s,
                                                                           [s.station for s in b.stops])


def test_no_usable_station_at_all_is_reported_as_such():
    sc = _mixed_instance(0, "city23_ac")
    sc = sc.with_(stations=[dataclasses.replace(s, kind="DC", power_kw=150.0, connectors=()) for s in sc.stations])
    for algo in ("bls-astar", "cfp-astar", "pareto", "greedy", "station-graph", "genetic"):
        p = plan(sc, algo)
        assert not p.feasible and not p.stops
    assert "0 usable" in plan(sc, "bls-astar").message


def test_osm_stations_stay_usable_for_the_default_vehicles():
    st = osm_turkey_stations("osm")
    for key in OLD_PRESETS:
        assert all(s.usable_by(get_vehicle(key).connectors) for s in st), key
    assert sum(s.kind == "DC" and s.usable_by(("Type2", "CHAdeMO")) for s in st) == 12
    assert not any(s.kind == "DC" and s.usable_by(("Type2",)) for s in st)
    assert osm_connectors("AC", "") == () and osm_connectors("DC", "type2_combo:8@250") == ()
    assert osm_connectors("DC", "chademo:1;type1_combo:1;type2:3") == ("Type2", "CCS2", "CCS1", "CHAdeMO")
    assert osm_connectors("AC", "type1:1") == ("Type2", "Type1")
    assert osm_connectors("DC", "chademo:0;type2:1") == ("Type2", "CCS2")
    assert osm_connectors("AC", "schuko:2;type2:?@11") == ()


# ====================================================================== exactness on the new presets
@pytest.mark.parametrize("seed", range(6))
def test_exact_planners_agree_on_the_new_presets(seed):
    rng = random.Random(seed)
    key = NEW_PRESETS[seed % len(NEW_PRESETS)]
    veh = get_vehicle(key)
    if rng.random() < 0.6 and veh.dc_capable:
        veh = veh.with_(dc_max_kw=round(rng.uniform(25.0, veh.dc_peak_kw), 1))
    sc = _mixed_instance(seed, "compact50").with_(vehicle=veh)
    sc = sc.with_preferences(objective=rng.choice(["time", "cost"]), charge_step_soc=0.05,
                             time_dependent_waits=rng.random() < 0.5)
    sc = sc.with_conditions(temperature_c=rng.choice([-8.0, 5.0, 20.0]), preconditioning=rng.random() < 0.5)
    plans = {a: plan(sc, a) for a in ("bls", "bls-astar", "cfp", "cfp-astar", "pareto")}
    for a, p in plans.items():
        assert p.feasible, (a, p.message)
        assert check_objective(p), a
    assert plans["bls-astar"].objective_value == pytest.approx(plans["bls"].objective_value, rel=1e-9, abs=1e-6)
    if sc.preferences.objective == "time":
        assert plans["cfp-astar"].total_time_s == pytest.approx(plans["cfp"].total_time_s, rel=1e-9, abs=1e-6)
        assert plans["cfp"].total_time_s <= plans["bls"].total_time_s + 1e-6


# ====================================================================== JSON
def test_vehicle_json_round_trip():
    for v in VEHICLES.values():
        d = json.loads(json.dumps(v.to_dict()))
        assert Vehicle.from_dict(d) == v
        assert ("chemistry" in d) == (v.chemistry != "NMC")
        assert ("connectors" in d) == (v.connectors != ("Type2", "CCS2"))
        assert "dc_max_kw" not in d
    v = get_vehicle("sedan60_lfp").with_(dc_max_kw=80.0, connectors=("Type2", "CCS2", "CHAdeMO"))
    d = json.loads(json.dumps(v.to_dict()))
    assert d["dc_max_kw"] == 80.0 and d["connectors"] == ["Type2", "CCS2", "CHAdeMO"] and d["chemistry"] == "LFP"
    assert Vehicle.from_dict(d) == v
    for key in OLD_PRESETS:  # the JSON of the 0.x presets is unchanged
        assert not {"chemistry", "dc_max_kw", "connectors"} & set(VEHICLES[key].to_dict())


def test_scenario_json_round_trip_with_connectors():
    sc = _mixed_instance(2, "hatch40_chademo").with_(vehicle=get_vehicle("hatch40_chademo").with_(dc_max_kw=40.0))
    d = json.loads(json.dumps(sc.to_dict()))
    assert [s.get("connectors") for s in d["stations"][:3]] == [None, None, ["Type2", "CCS2", "CHAdeMO"]]
    back = Scenario.from_dict(d)
    assert back.to_dict() == d
    assert back.prepare().station_usable == sc.prepare().station_usable
    a, b = plan(sc, "bls-astar"), plan(back, "bls-astar")
    assert (a.edges, a.total_time_s) == (b.edges, b.total_time_s)


# ====================================================================== API and payload
def test_api_body_fields():
    base = _mixed_instance(0, "compact50")
    sc, _ = build_plan_scenario(base, {"min_stop_soc": 0.4, "dc_max_kw": 50, "vehicle": "sedan60_lfp"})
    assert sc.preferences.min_stop_soc == 0.4
    assert sc.vehicle.key == "sedan60_lfp" and sc.vehicle.dc_max_kw == 50.0 and sc.vehicle.dc_peak_kw == 50.0
    sc, _ = build_plan_scenario(base, {"dc_max_kw": None, "min_stop_soc": 0})
    assert sc.vehicle.dc_max_kw is None and sc.preferences.min_stop_soc == 0.0
    for body, msg in (({"dc_max_kw": 2}, "dc_max_kw"), ({"dc_max_kw": 501}, "dc_max_kw"),
                      ({"dc_max_kw": "fast"}, "dc_max_kw"), ({"min_stop_soc": 0.96}, "min_stop_soc"),
                      ({"min_stop_soc": -0.1}, "min_stop_soc"), ({"min_stop_soc": True}, "min_stop_soc")):
        with pytest.raises(PlanRequestError, match=msg):
            build_plan_scenario(base, body)


def test_payload_vehicle_and_station_fields():
    sc = _mixed_instance(0, "hatch40_chademo").with_(vehicle=get_vehicle("hatch40_chademo").with_(dc_max_kw=30.0))
    p = plan(sc, "bls-astar")
    data = json.loads(json.dumps(viewer_payload(sc, [p], robustness_runs=0)))
    veh = data["scenario"]["vehicle"]
    assert veh["chemistry"] == "NMC" and veh["connectors"] == ["Type2", "CHAdeMO"]
    assert veh["dc_max_kw"] == 30.0 and veh["dc_peak_kw"] == 30.0
    keys = {"key", "name", "battery_kwh", "dc_peak_kw", "ac_max_kw", "chemistry", "connectors", "notes"}
    assert [v["key"] for v in data["vehicles"]] == list(VEHICLES)
    for v in data["vehicles"]:
        assert set(v) == keys
        assert v["dc_peak_kw"] == round(VEHICLES[v["key"]].dc_peak_kw, 1)
    ac = next(v for v in data["vehicles"] if v["key"] == "city23_ac")
    assert ac["dc_peak_kw"] == 0.0 and ac["connectors"] == ["Type2"]
    P = sc.prepare()
    for i, st in enumerate(data["stations"]):
        assert st["connectors"] == list(sc.stations[i].sockets)
        assert st["usable"] is P.station_usable[i]
        assert st["operator"] == sc.stations[i].operator
    assert data["scenario"]["preferences"]["min_stop_soc"] == 0.0
    res = results_payload(sc.with_(vehicle=get_vehicle("city23_ac")), [], robustness_runs=0)
    assert res["station_usable"] == [s.kind == "AC" for s in sc.stations]
