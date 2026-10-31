"""Tests of :mod:`evroute.station_sources` on the handcrafted fixtures of ``tests/data``.

No network access: the fixtures imitate a CSV export, an OCPI 2.2.1
``locations`` response (plus a ``tariffs`` response) and an Open Charge Map
POI list around the Karaman -> Konya road of the bundled OSM network.  The
records are invented (names start with "Fixture"); operator names are real so
that the price lookup in ``prices.json`` is exercised.
"""

from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

import pytest

from evroute.algorithms import plan
from evroute.cli import main
from evroute.generators import grid_scenario
from evroute.generators.osm_turkey import DEFAULT_PRICE_TRY, osm_turkey_network, station_price
from evroute.generators.presets import get_scenario
from evroute.scenario import Scenario
from evroute.station_sources import (
    OCM_ATTRIBUTION,
    detect_format,
    load_stations,
    normalise_connector,
    stations_from_csv,
    stations_from_ocm,
    stations_from_ocpi,
    with_stations,
)
from evroute.units import haversine_m
from evroute.vehicle import get_vehicle

DATA = Path(__file__).parent / "data"
CUMRA_NODE = 23350  # the network node next to the "Fixture ... Çumra" records


@pytest.fixture(scope="module")
def net():
    return osm_turkey_network()


@pytest.fixture(scope="module")
def tariffs():
    return json.loads((DATA / "ocpi_tariffs.json").read_text(encoding="utf-8"))


def _check_snapping(net, imported, positions):
    for st, inf, (lat, lon) in zip(imported.stations, imported.info, positions):
        d = haversine_m(lat, lon, net.lat[st.node], net.lon[st.node])
        assert inf["snap_m"] == pytest.approx(d, abs=0.06)
        # no other node is closer (brute force over the nodes within 0.05 degrees)
        near = [v for v in range(net.num_nodes) if abs(net.lat[v] - lat) < 0.05 and abs(net.lon[v] - lon) < 0.05]
        assert min(haversine_m(lat, lon, net.lat[v], net.lon[v]) for v in near) == pytest.approx(d, abs=1e-6)


# ====================================================================== connectors
def test_connector_spellings():
    for name, std in (("Type2", "Type2"), ("type 2", "Type2"), ("TYPE_2", "Type2"), ("Mennekes", "Type2"),
                      ("ccs", "CCS2"), ("CCS (Type 2)", "CCS2"), ("type2_combo", "CCS2"), ("Combo 2", "CCS2"),
                      ("ccs1", "CCS1"), ("type1_combo", "CCS1"), ("J1772", "Type1"), ("CHAdeMO", "CHAdeMO"),
                      ("chademo", "CHAdeMO"), ("NACS", "NACS"), ("SAE J3400", "NACS")):
        assert normalise_connector(name) == std, name
    assert normalise_connector("Schuko") is None and normalise_connector("") is None


# ====================================================================== CSV
def test_csv_fixture(net):
    r = stations_from_csv(DATA / "stations.csv", net)
    assert r.source == "csv" and len(r) == 3
    assert [s.id for s in r.stations] == [0, 1, 2]
    hub, ac, chademo = r.stations
    assert (hub.node, hub.kind, hub.power_kw, hub.plugs, hub.connectors) == (CUMRA_NODE, "DC", 180.0, 4, ("Type2", "CCS2"))
    assert hub.price_per_kwh == station_price("ZES", "DC")[0] == 16.49 and r.info[0]["price"] == "operator"
    assert (ac.kind, ac.power_kw, ac.plugs, ac.connectors, ac.sockets) == ("AC", 22.0, 2, (), ("Type2",))
    assert ac.price_per_kwh == 8.5 and r.info[1]["price"] == "file" and ac.operator == "Unknown Operator Ltd"
    assert (chademo.node, chademo.connectors, chademo.price_per_kwh) == (7673, ("CHAdeMO",), 14.98)
    assert r.info[2]["snap_m"] == 0.0 and r.info[0]["record"] == "csv-1"
    assert r.skipped == [{"record": "csv-3", "reason": "too far from the network", "max_snap_m": 3000.0}]
    assert r.availability == [None, None, None]
    _check_snapping(net, r, [(37.4862, 32.7055), (37.2600, 32.9231)])
    # queue assumptions of the OSM stations
    assert hub.daily_sessions == pytest.approx(3.7 * 4) and hub.mean_service_min == 36.0 and hub.setup_s == 180.0
    assert len(hub.demand_profile) == 24


def test_csv_rows_in_the_network_frame():
    sc = grid_scenario(seed=1)
    net = sc.network
    rows = [
        {"x": net.x[5] + 30.0, "y": net.y[5] - 40.0, "power_kw": "50", "connectors": "CCS2|CHAdeMO"},
        {"node": "7", "power_kw": "11", "plugs": "3", "operator": "Somebody", "setup_s": "60",
         "demand_profile": "urban", "mean_service_min": "90"},
        {"x": net.x[0] + 1e6, "y": net.y[0], "power_kw": "50"},
    ]
    r = stations_from_csv(rows, net, default_price={"AC": 0.3, "DC": 0.5}, operator_prices=False)
    a, b = r.stations
    assert (a.node, a.kind, a.connectors, a.price_per_kwh) == (5, "DC", ("CCS2", "CHAdeMO"), 0.5)
    assert r.info[0]["snap_m"] == pytest.approx(50.0)
    assert (b.node, b.kind, b.plugs, b.price_per_kwh, b.setup_s, b.demand_profile, b.mean_service_min) == (
        7, "AC", 3, 0.3, 60.0, "urban", 90.0)
    assert r.skipped[0]["reason"] == "too far from the network"
    planned = plan(sc.with_(stations=r.stations), "bls-astar")
    assert planned.feasible or "no battery-feasible" in planned.message


@pytest.mark.parametrize("row, msg", [
    ({"lat": "37.4", "lon": "32.7"}, "power_kw is required"),
    ({"lat": "37.4", "lon": "32.7", "power_kw": "x"}, "power_kw must be a number"),
    ({"lat": "37.4", "lon": "32.7", "power_kw": "0"}, "power_kw must be > 0"),
    ({"lat": "137.4", "lon": "32.7", "power_kw": "50"}, "out of range"),
    ({"power_kw": "50"}, "needs lat/lon, x/y or node"),
    ({"node": "1e9", "power_kw": "50"}, "not a node"),
    ({"node": "1", "power_kw": "50", "connectors": "CCS3"}, "unknown connector"),
    ({"node": "1", "power_kw": "50", "kind": "HPC"}, "kind must be AC or DC"),
    ({"node": "1", "power_kw": "50", "kind": "AC", "connectors": "CHAdeMO"}, "carries AC"),
    ({"node": "1", "power_kw": "50", "plugs": "1.5"}, "whole number"),
    ({"node": "1", "power_kw": "50", "price_per_kwh": "-1"}, "price_per_kwh"),
    ({"node": "1", "power_kw": "50", "demand_profile": "rush"}, "demand_profile"),
])
def test_csv_errors_name_the_row(net, row, msg):
    with pytest.raises(ValueError, match=msg) as exc:
        stations_from_csv([row], net)
    assert "row 1" in str(exc.value)


def test_csv_semicolons_and_decimal_commas(net, tmp_path):
    f = tmp_path / "stations.csv"
    f.write_text("Lat;Lon;Power_kW;Connectors;Price\n37,4862;32,7055;50;CCS2|CHAdeMO;12,5\n;;;;\n", encoding="utf-8")
    r = stations_from_csv(f, net)
    assert len(r) == 1 and r.stations[0].node == CUMRA_NODE
    assert (r.stations[0].price_per_kwh, r.stations[0].connectors) == (12.5, ("CCS2", "CHAdeMO"))


def test_csv_row_with_more_fields_than_the_header(net, tmp_path):
    """Regression: the fields beyond the header were dropped, so a decimal comma in a comma-separated
    file shifted the values silently (``9,99`` became price 9 and 99 plugs)."""
    f = tmp_path / "stations.csv"
    f.write_text("node,power_kw,price_per_kwh,plugs\n5,50,9.99,2\n5,50,9,99,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"row 2: 5 fields, but the header has 4 \(a decimal comma"):
        stations_from_csv(f, net)
    # the same from rows of csv.DictReader; a quoted decimal comma and a row of empty fields are fine
    with pytest.raises(ValueError, match="row 1: 6 fields"):
        stations_from_csv(csv.DictReader(io.StringIO("node,power_kw,price_per_kwh,plugs\n5,50,9,99,2,\n")), net)
    f.write_text('node,power_kw,price_per_kwh,plugs\n5,50,"9,99",2\n,,,,,\n', encoding="utf-8")
    (st,) = stations_from_csv(f, net).stations
    assert (st.price_per_kwh, st.plugs) == (9.99, 2)


def test_csv_kind_inference(net):
    rows = [{"node": "1", "power_kw": "22"}, {"node": "1", "power_kw": "50"},
            {"node": "1", "power_kw": "11", "connectors": "CHAdeMO"}, {"node": "1", "power_kw": "7", "connectors": "Type2"}]
    assert [s.kind for s in stations_from_csv(rows, net).stations] == ["AC", "DC", "DC", "AC"]


def test_lat_lon_needs_a_network_with_coordinates():
    net = grid_scenario(seed=0).network
    assert net.lat is None
    with pytest.raises(ValueError, match="no node coordinates"):
        stations_from_csv([{"lat": "37.4", "lon": "32.7", "power_kw": "50"}], net)


def test_snapping_radius(net):
    row = {"lat": "37.4862", "lon": "32.7055", "power_kw": "50"}
    assert not stations_from_csv([row], net, max_snap_m=50.0).stations
    assert stations_from_csv([row], net, max_snap_m=100.0).stations
    with pytest.raises(ValueError, match="max_snap_m"):
        stations_from_csv([row], net, max_snap_m=0.0)


# ====================================================================== OCPI
def test_ocpi_fixture(net, tariffs):
    resp = json.loads((DATA / "ocpi_locations.json").read_text(encoding="utf-8"))
    r = stations_from_ocpi(resp, net, tariffs=tariffs["data"])
    assert r.source == "ocpi" and len(r) == 3
    dc, ac, trugo = r.stations
    # one station per kind at the site: the AC points stay usable for a car without a DC inlet
    assert dc.node == ac.node == CUMRA_NODE and (dc.kind, ac.kind) == ("DC", "AC")
    assert (dc.power_kw, dc.plugs, dc.connectors) == (180.0, 2, ("CCS2", "CHAdeMO"))
    assert dc.price_per_kwh == pytest.approx(12.5 * 1.2) and r.info[0]["price"] == "ocpi-tariff"
    assert r.availability[0] == {"available": 1, "total": 2, "out_of_service": 1,
                                 "status": {"AVAILABLE": 1, "CHARGING": 1, "OUTOFORDER": 1},
                                 "last_updated": "2026-10-30T08:59:00Z"}
    assert r.info[0]["evses"] == ["TR*ZES*E1", "TR*ZES*E2", "TR*ZES*E3"]
    # 3 x 230 V x 32 A; the restricted night price is not the base price; the removed EVSE does not count
    assert (ac.power_kw, ac.plugs, ac.connectors) == (22.08, 1, ())
    assert ac.price_per_kwh == pytest.approx(8.5 * 1.2)
    assert r.availability[1]["total"] == 1 and r.availability[1]["available"] == 1
    # no power given: the default; no tariff: the operator's verified list price
    assert (trugo.power_kw, trugo.price_per_kwh, trugo.operator) == (120.0, 14.98, "Trugo")
    assert r.info[2]["power"] == "default" and r.info[2]["price"] == "operator"
    assert r.availability[2]["status"] == {"UNKNOWN": 1} and r.availability[2]["available"] == 0
    assert [(s["record"], s["reason"]) for s in r.skipped] == [
        ("TR*ZES*LOC-2", "not published"), ("TR*ZES*LOC-3", "too far from the network"),
        ("TR*XYZ*LOC-4", "no usable connector")]
    assert r.skipped[2]["types"] == ["DOMESTIC_F"]
    # a list of locations, a response and a tariff response are all accepted
    r2 = stations_from_ocpi(resp["data"], net, tariffs=tariffs)
    assert [s.to_dict() for s in r2.stations] == [s.to_dict() for s in r.stations]


def test_ocpi_without_tariffs_and_with_another_currency(net, tariffs):
    resp = json.loads((DATA / "ocpi_locations.json").read_text(encoding="utf-8"))
    plain = stations_from_ocpi(resp, net)
    assert [s.price_per_kwh for s in plain.stations] == [16.49, 10.99, 14.98]  # ZES DC/AC, Trugo DC list prices
    eur = stations_from_ocpi(resp, net, tariffs=tariffs, currency="EUR")
    assert [i["price"] for i in eur.info] == ["operator", "operator", "operator"]
    flat = stations_from_ocpi(resp, net, operator_prices=False, default_price=11.0)
    assert [s.price_per_kwh for s in flat.stations] == [11.0, 11.0, 11.0]


def test_ocpi_evse_states():
    net = osm_turkey_network()
    loc = {"id": "L", "coordinates": {"latitude": "37.4855", "longitude": "32.704"},
           "evses": [{"status": st, "connectors": [{"standard": "IEC_62196_T2_COMBO", "power_type": "DC",
                                                    "max_voltage": 400, "max_amperage": 125}]}
                     for st in ("BLOCKED", "RESERVED", "INOPERATIVE", "PLANNED")]}
    r = stations_from_ocpi([loc], net)
    assert r.stations[0].plugs == 2 and r.stations[0].power_kw == 50.0  # 400 V x 125 A
    assert r.availability[0]["out_of_service"] == 1 and "PLANNED" not in r.availability[0]["status"]
    down = {**loc, "evses": [{"status": "OUTOFORDER", "connectors": loc["evses"][0]["connectors"]}]}
    r = stations_from_ocpi([down], net)
    assert not r.stations and r.skipped[0]["reason"] == "no EVSE in service"
    with pytest.raises(ValueError, match="data"):
        stations_from_ocpi({"status_code": 2001}, net)


def test_ocpi_evses_out_of_service_lend_the_station_nothing(net):
    """Regression: an EVSE out of order added its connectors, power and tariffs to the station,
    so a site with one working CCS2 point imported as a 300 kW station a CHAdeMO car could use."""
    def conn(standard, kw, tariff):
        return {"standard": standard, "power_type": "DC", "max_electric_power": kw * 1000, "tariff_ids": [tariff]}

    tariffs = [{"id": tid, "currency": "TRY", "elements": [{"price_components": [{"type": "ENERGY", "price": p}]}]}
               for tid, p in (("T-OK", 10.0), ("T-DOWN", 30.0))]
    loc = {"id": "L", "coordinates": {"latitude": "37.4855", "longitude": "32.704"},
           "evses": [{"uid": "E1", "status": "AVAILABLE", "connectors": [conn("IEC_62196_T2_COMBO", 50, "T-OK")]},
                     {"uid": "E2", "status": "OUTOFORDER",
                      "connectors": [conn("CHADEMO", 50, "T-DOWN"), conn("IEC_62196_T2_COMBO", 300, "T-DOWN")]}]}
    for down in ("OUTOFORDER", "INOPERATIVE"):
        loc["evses"][1]["status"] = down
        r = stations_from_ocpi([loc], net, tariffs=tariffs)
        (st,) = r.stations
        assert (st.power_kw, st.plugs, st.sockets, st.price_per_kwh) == (50.0, 1, ("CCS2",), 10.0)
        assert not st.usable_by(get_vehicle("hatch40_chademo").connectors)
        # the broken EVSE stays in the live snapshot
        assert r.availability[0]["out_of_service"] == 1 and r.availability[0]["status"] == {"AVAILABLE": 1, down: 1}
        assert r.info[0]["evses"] == ["E1", "E2"]


# ====================================================================== Open Charge Map
def test_ocm_fixture(net):
    pois = json.loads((DATA / "stations_ocm.json").read_text(encoding="utf-8"))
    r = stations_from_ocm(pois, net)
    assert r.source == "ocm" and len(r) == 3 and r.availability == [None, None, None]
    dc, ac, zes = r.stations
    assert dc.node == ac.node == CUMRA_NODE
    assert (dc.kind, dc.power_kw, dc.plugs, dc.connectors) == ("DC", 120.0, 3, ("CCS2", "CHAdeMO"))
    assert (ac.kind, ac.plugs, ac.connectors) == ("AC", 2, ())
    assert ac.power_kw == round(32 * 400 * math.sqrt(3) / 1000, 2)  # three-phase from amps and volts
    # Eşarj has no verified list price: the defaults
    assert (dc.price_per_kwh, ac.price_per_kwh) == (DEFAULT_PRICE_TRY["DC"], DEFAULT_PRICE_TRY["AC"])
    # an unknown ConnectionTypeID is matched by its title; the operator by a word of its name
    assert (zes.kind, zes.power_kw, zes.connectors, zes.price_per_kwh) == ("DC", 60.0, (), 16.49)
    assert [(s["record"], s["reason"]) for s in r.skipped] == [
        (900002, "not operational"), (900004, "no usable connector"), (900005, "too far from the network")]


def test_ocm_compact_output(net):
    """Regression: with ``compact=true`` the API gives ``StatusTypeID`` instead of ``StatusType``,
    and removed or non-operational POIs and connections were imported; ``OperatorID`` alone names
    no operator."""
    def poi(ident, status, *conns):
        return {"ID": ident, "StatusTypeID": status, "OperatorID": 3,
                "AddressInfo": {"Title": "Fixture compact", "Latitude": 37.4858, "Longitude": 32.7048},
                "Connections": [{"ConnectionTypeID": t, "CurrentTypeID": 30, "PowerKW": kw, "Quantity": 1,
                                 "StatusTypeID": s} for t, kw, s in conns]}

    pois = [poi(1, 50, (33, 150.0, 50), (2, 50.0, 100), (33, 300.0, 200)),  # CCS2 150 kW; CHAdeMO down; 300 kW removed
            poi(2, 100, (33, 50.0, 50)), poi(3, 150, (33, 50.0, 0)), poi(4, 200, (33, 50.0, 50)),
            poi(5, 210, (33, 50.0, 50)), poi(6, 0, (33, 50.0, 210))]
    r = stations_from_ocm(pois, net)
    (st,) = r.stations
    assert (st.power_kw, st.plugs, st.sockets, st.operator) == (150.0, 1, ("CCS2",), "unknown")
    assert st.price_per_kwh == DEFAULT_PRICE_TRY["DC"]
    assert [(s["record"], s["reason"]) for s in r.skipped] == [
        (2, "not operational"), (3, "not operational"), (4, "not operational"), (5, "not operational"),
        (6, "no usable connector")]
    # IsOperational of the full output wins over the ID
    full = {**pois[1], "StatusType": {"ID": 100, "IsOperational": True}}
    assert len(stations_from_ocm([full], net)) == 1


# ====================================================================== files and scenarios
def test_load_stations_detects_the_format(net, tmp_path):
    assert detect_format(DATA / "stations.csv") == "csv"
    assert detect_format(DATA / "ocpi_locations.json") == "ocpi"
    assert detect_format(DATA / "stations_ocm.json") == "ocm"
    for name, src in (("stations.csv", "csv"), ("ocpi_locations.json", "ocpi"), ("stations_ocm.json", "ocm")):
        assert load_stations(DATA / name, net).source == src
    bundle = {"locations": json.loads((DATA / "ocpi_locations.json").read_text(encoding="utf-8"))["data"],
              "tariffs": json.loads((DATA / "ocpi_tariffs.json").read_text(encoding="utf-8"))["data"]}
    f = tmp_path / "bundle.json"
    f.write_text(json.dumps(bundle), encoding="utf-8")
    r = load_stations(f, net)
    assert r.source == "ocpi" and r.info[0]["price"] == "ocpi-tariff"
    odd = tmp_path / "odd.json"
    odd.write_text("[{\"foo\": 1}]", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot tell the format"):
        load_stations(odd, net)
    with pytest.raises(ValueError, match="format"):
        load_stations(DATA / "stations.csv", net, "kml")


def test_with_stations_and_planning(net):
    base = get_scenario("turkey-osm-karaman-konya")
    r = load_stations(DATA / "ocpi_locations.json", net)
    sc = with_stations(base, r, label="fixture")
    assert len(sc.stations) == 3 and sc.network is base.network and base.stations is not sc.stations
    assert sc.meta["stations"] == "fixture"
    assert sc.meta["station_info"]["0"]["availability"]["available"] == 1
    assert sc.meta["attribution"] == base.meta["attribution"]
    p = plan(sc, "bls-astar")
    assert p.feasible and p.stops and all(sc.stations[s.station].node == CUMRA_NODE for s in p.stops)
    # an AC-only car uses the AC points of the same site
    p_ac = plan(sc.with_(vehicle=get_vehicle("city23_ac")).with_query(initial_soc=0.9), "bls-astar")
    assert p_ac.feasible and all(s.station_kind == "AC" for s in p_ac.stops)
    # the scenario survives JSON with its connectors
    back = Scenario.from_dict(json.loads(json.dumps(sc.to_dict())))
    assert [s.connectors for s in back.stations] == [s.connectors for s in sc.stations]
    ocm = with_stations(base, load_stations(DATA / "stations_ocm.json", net))
    assert ocm.meta["attribution"].endswith(OCM_ATTRIBUTION) and ocm.meta["attribution"].startswith("©")


def test_cli_stations_file(capsys):
    assert main(["plan", "turkey-osm-karaman-konya", "--stations-file", str(DATA / "ocpi_locations.json")]) == 0
    out, err = capsys.readouterr()
    assert "Fixture site near Çumra" in out
    assert "stations: 3 from" in err and "1 not published" in err
    assert main(["plan", "turkey-osm-karaman-konya", "--stations-file", str(DATA / "missing.json")]) == 2
    assert "no stations file" in capsys.readouterr().err
    assert main(["plan", "turkey-osm-karaman-konya", "--stations", "hybrid",
                 "--stations-file", str(DATA / "stations.csv")]) == 2
    assert "exclude each other" in capsys.readouterr().err
    assert main(["plan", "corridor", "--stations-file", str(DATA / "stations.csv")]) == 2
    assert "no node coordinates" in capsys.readouterr().err


def test_cli_dc_limit(capsys, tmp_path):
    out = tmp_path / "plan.json"
    assert main(["plan", "turkey-karaman-mersin", "--vehicle", "sedan60_lfp", "--soc", "0.3", "--dc-max-kw", "40",
                 "--json", str(out)]) == 0
    stops = json.loads(out.read_text(encoding="utf-8"))["stops"]
    dc = [s for s in stops if s["station_kind"] == "DC"]
    assert dc and all(s["power_kw"] <= 40.0 + 1e-9 for s in dc)
    capsys.readouterr()
    assert main(["plan", "turkey-karaman-mersin", "--dc-max-kw", "0"]) == 2
    assert "dc_max_kw" in capsys.readouterr().err
