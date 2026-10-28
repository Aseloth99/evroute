"""Tests of the real-data Turkey network (:mod:`evroute.generators.osm_turkey`).

Data integrity of the bundled OpenStreetMap extract (counts as in
``meta.json``, connectivity, geometry, elevations, station snapping), place
lookup, the three station sets, prices and queues, the ``turkey-osm-*``
presets and the CLI, and the exact planners on a short real trip.  Planning
the long presets is marked slow.
"""

from __future__ import annotations

import csv
import gzip
import io
import math
import time

import pytest

from evroute.algorithms import get_planner
from evroute.cli import main
from evroute.data import read_bytes
from evroute.generators import osm_turkey as OT
from evroute.generators.osm_turkey import (
    ATTRIBUTION,
    DEFAULT_PRICE_TRY,
    GAP_KM,
    MAIN_CLASSES,
    QUEUE_MODEL,
    find_osm_place,
    osm_turkey_meta,
    osm_turkey_network,
    osm_turkey_places,
    osm_turkey_prices,
    osm_turkey_scenario,
    osm_turkey_stations,
    osm_turkey_summary,
    station_price,
)
from evroute.generators.presets import PRESETS, get_scenario
from evroute.generators.turkey import turkey_stations
from evroute.network import ROAD_CLASSES
from evroute.units import haversine_m

OSM_PRESETS = sorted(n for n, p in PRESETS.items() if p.family == "turkey-osm")


@pytest.fixture(scope="module")
def net():
    return osm_turkey_network()


@pytest.fixture(scope="module")
def meta():
    return osm_turkey_meta()


def _raw(name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(gzip.decompress(read_bytes(f"osm_turkey/{name}")).decode("utf-8"))))


def _geodesic_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """WGS84 length of a short segment (mid-latitude radii of curvature), as in the build script."""
    a, f = 6378137.0, 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    phi = math.radians(0.5 * (lat1 + lat2))
    w = math.sqrt(1.0 - e2 * math.sin(phi) ** 2)
    m, n = a * (1.0 - e2) / w ** 3, a / w
    return math.hypot(m * math.radians(lat2 - lat1), n * math.cos(phi) * math.radians(lon2 - lon1))


# ============================================================ data integrity
def test_counts_match_the_build_metadata(net, meta):
    g = meta["graph"]
    assert (net.num_nodes, net.num_edges) == (g["nodes"], g["edges"]) == (44673, 70886)
    km = sum(net.length) / 1000.0
    assert km == pytest.approx(sum(g["directed_km_by_class"].values()), rel=1e-3)
    assert len(osm_turkey_stations("osm")) == meta["stations"]["kept"] == 711
    places = osm_turkey_places()
    assert len(places) == meta["places"]["places"]
    assert sum(p.province_centre for p in places) == meta["places"]["province_centres"] == 81
    s = osm_turkey_summary()
    assert s["nodes"] == net.num_nodes and s["stations_osm"] == 711
    assert (s["stations_osm_ac"], s["stations_osm_dc"]) == (618, 93)
    assert s["attribution"] == ATTRIBUTION == "© OpenStreetMap contributors"


def test_network_is_strongly_connected(net):
    assert net.is_strongly_connected()


def test_edges_are_plausible(net, meta):
    assert set(net.road_class) <= set(ROAD_CLASSES)
    assert set(net.road_class) >= {"motorway", "trunk", "primary", "link"}
    lo, hi = meta["graph"]["edge_length_m"]["min"], meta["graph"]["edge_length_m"]["max"]
    assert lo - 0.1 <= min(net.length) and max(net.length) <= hi + 0.1
    assert all(5.0 <= v <= 210.0 for v in net.speed_kmh)
    # above 150 km/h only on a few short edges that the build lengthened to the straight chord
    # after merging junctions (it keeps their travel time): a known artefact of the data
    fast = [e for e in net.edges() if net.speed_kmh[e] > 150.0]
    assert len(fast) <= 10 and all(net.length[e] < 200.0 for e in fast)
    assert all(net.tail[e] != net.head[e] for e in net.edges())


def test_every_edge_is_at_least_as_long_as_its_chord(net):
    """Road length >= straight 3-D line between the end nodes (WGS84; data are rounded to 0.1 m)."""
    worst = 0.0
    for e in net.edges():
        u, v = net.tail[e], net.head[e]
        chord = math.hypot(_geodesic_m(net.lat[u], net.lon[u], net.lat[v], net.lon[v]),
                           net.elevation[u] - net.elevation[v])
        worst = max(worst, chord - net.length[e])
    assert worst <= 0.25


def test_elevations_are_in_range(net, meta):
    lo, hi = min(net.elevation), max(net.elevation)
    assert lo == pytest.approx(meta["graph"]["elevation_m"]["min"]) and hi == pytest.approx(meta["graph"]["elevation_m"]["max"])
    assert -50.0 <= lo and hi <= 3500.0  # Turkish roads: sea level to the high passes of the east
    assert all(35.5 <= la <= 42.5 for la in net.lat) and all(25.5 <= lo_ <= 45.0 for lo_ in net.lon)  # type: ignore[union-attr]


def test_stations_lie_within_3_km_of_their_node(net):
    rows = _raw("stations.csv.gz")
    assert len(rows) == 711
    for r in rows:
        node = int(r["node"])
        d = haversine_m(float(r["lat"]), float(r["lon"]), net.lat[node], net.lon[node])
        assert d <= 3000.0 and d == pytest.approx(float(r["snap_m"]), abs=1.0)


def test_loader_is_fast():
    t0 = time.perf_counter()
    fresh = OT._network.__wrapped__()  # bypass the per-process cache
    dt = time.perf_counter() - t0
    assert fresh.num_nodes == 44673
    assert dt < 10.0, f"loading the network took {dt:.1f} s"
    assert osm_turkey_network() is osm_turkey_network()  # cached and shared


# ============================================================ places
def test_place_lookup_ignores_case_and_turkish_letters():
    ist = find_osm_place("İstanbul")
    assert find_osm_place("istanbul") == find_osm_place("ISTANBUL") == find_osm_place("İSTANBUL") == ist
    assert ist.province_centre and ist.population > 10_000_000
    assert find_osm_place("izmir").name == "İzmir" and find_osm_place("Sanliurfa").name == "Şanlıurfa"
    # province centres first, then the larger place: Ereğli is the Konya town, not Karadeniz Ereğli
    assert find_osm_place("Eregli").lat < 38.0 and find_osm_place("Karadeniz Ereğli").lat > 41.0
    # a province name finds its centre
    assert find_osm_place("Kocaeli").province == "Kocaeli" and find_osm_place("Kocaeli").province_centre
    with pytest.raises(KeyError, match="unknown place"):
        find_osm_place("Atlantis")
    centres = [p for p in osm_turkey_places() if p.province_centre]
    assert osm_turkey_places()[: len(centres)] == centres  # lookup priority
    assert max(p.snap_m for p in centres) < 3000.0


def test_scenario_endpoints_and_metadata():
    sc = osm_turkey_scenario("karaman", "KONYA", initial_soc=0.3)
    assert sc.query.origin == find_osm_place("Karaman").node and sc.query.destination == find_osm_place("Konya").node
    assert sc.name == "turkey-osm-karaman-konya" and sc.currency == "TRY"
    m = sc.meta
    assert m["generator"] == "osm_turkey" and m["attribution"] == ATTRIBUTION and m["stations"] == "osm"
    assert len(m["station_info"]) == len(sc.stations)
    assert {i["price"] for i in m["station_info"].values()} == {"operator", "default"}
    assert all(i["osm"].split("/")[0] in ("node", "way", "relation") for i in m["station_info"].values())
    assert sc.tariff.to_dict() == type(sc.tariff)().to_dict()  # neutral by default
    with pytest.raises(ValueError):
        osm_turkey_scenario("Konya", "Konya")
    with pytest.raises(ValueError):
        osm_turkey_scenario("Karaman", "Konya", stations="tesla")


# ============================================================ stations, prices, queues
def test_station_sets():
    osm = osm_turkey_stations("osm")
    assert len(osm) == 711 and sum(s.kind == "DC" for s in osm) == 93
    # OSM has no DC station east of 38 E (the reason for the hybrid set)
    net = osm_turkey_network()
    assert max(net.lon[s.node] for s in osm if s.kind == "DC") < 38.0  # type: ignore[index]
    synth = osm_turkey_stations("synthetic")
    approx = turkey_stations(0)
    assert len(synth) == len(approx)
    for a, b in zip(approx, synth):
        assert (a.power_kw, a.kind, a.plugs, a.price_per_kwh, a.name, a.operator) == (b.power_kw, b.kind, b.plugs, b.price_per_kwh,
                                                                                       b.name, b.operator)
    hybrid = osm_turkey_stations("hybrid")
    assert [s.to_dict() for s in hybrid[: len(osm)]] == [s.to_dict() for s in osm]
    fill = hybrid[len(osm):]
    assert fill and all(s.operator == "synthetic" and s.name.startswith("Synthetic DC") and s.kind == "DC" for s in fill)
    assert [s.id for s in hybrid] == list(range(len(hybrid)))
    assert max(net.lon[s.node] for s in fill) > 40.0  # type: ignore[index]
    assert [s.to_dict() for s in osm_turkey_stations("hybrid")] == [s.to_dict() for s in hybrid]  # deterministic
    hybrid[0].price_per_kwh = 999.0
    assert osm_turkey_stations("hybrid")[0].price_per_kwh != 999.0  # fresh copies


def test_hybrid_covers_every_main_road_node():
    net = osm_turkey_network()
    dc = sorted({s.node for s in osm_turkey_stations("hybrid") if s.kind == "DC"})
    dist = [math.inf] * net.num_nodes
    OT._reverse_multi_dijkstra(net, dc, dist, math.inf)
    main = {net.tail[e] for e in net.edges() if net.road_class[e] in MAIN_CLASSES}
    assert max(dist[v] for v in main) <= GAP_KM * 1000.0


def test_prices_follow_the_operator_list():
    defaults = osm_turkey_prices()["suggested_defaults"]
    assert DEFAULT_PRICE_TRY == {"AC": defaults["ac"], "DC": defaults["dc"]}
    p, src = station_price("Trugo", "DC")
    assert src == "operator" and p > DEFAULT_PRICE_TRY["AC"]
    assert station_price("no such operator", "AC") == (DEFAULT_PRICE_TRY["AC"], "default")
    assert station_price("no such operator", "DC") == (DEFAULT_PRICE_TRY["DC"], "default")
    for s in osm_turkey_stations("osm"):
        assert s.price_per_kwh == round(station_price(s.operator, s.kind)[0], 2)


def test_queue_assumptions():
    for s in osm_turkey_stations("hybrid"):
        q = QUEUE_MODEL[s.kind]
        assert s.daily_sessions == pytest.approx(q["sessions_per_plug_day"] * s.plugs)
        assert s.mean_service_min == q["mean_service_min"]
        assert sum(s.demand_profile or []) == pytest.approx(1.0, abs=1e-3)


# ============================================================ presets and CLI
def test_presets_are_registered():
    assert {"turkey-osm-karaman-istanbul", "turkey-osm-antalya-ankara", "turkey-osm-istanbul-ankara",
            "turkey-osm-izmir-erzurum"} <= set(OSM_PRESETS)
    assert PRESETS["turkey-osm-izmir-erzurum"].info()["defaults"]["stations"] == "hybrid"
    sc = get_scenario("turkey-osm-karaman-istanbul", stations="synthetic")
    assert sc.meta["stations"] == "synthetic" and len(sc.stations) == len(turkey_stations(0))
    assert get_scenario("turkey-osm-karaman-istanbul-dc85").preferences.max_charge_soc == 0.85


def test_cli_lists_and_plans_osm_presets(capsys):
    assert main(["scenarios"]) == 0
    out = capsys.readouterr().out
    assert "turkey-osm-karaman-istanbul" in out
    assert main(["plan", "turkey-osm-karaman-konya", "--from", "karaman", "--to", "KONYA", "--stations", "hybrid"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[bls-astar]") and "infeasible" not in out.splitlines()[0]
    assert main(["plan", "turkey-osm-karaman-konya", "--from", "Atlantis"]) == 2
    assert "unknown place" in capsys.readouterr().err
    assert main(["plan", "corridor", "--stations", "osm"]) == 2
    assert "turkey-osm presets only" in capsys.readouterr().err
    # 'all' leaves the Dijkstra-order exact planners and the Pareto search out on 44 673 nodes
    assert main(["compare", "turkey-osm-karaman-konya", "--algorithms", "all"]) == 0
    out, err = capsys.readouterr()
    assert "left out of 'all'" in err and "bls-astar" in out and "pareto" not in out
    assert not any(line.split()[0] in ("bls", "cfp") for line in out.splitlines() if line.strip())


def test_exact_planners_agree_on_a_short_real_trip():
    """Karaman -> Konya at 30 %: BLS-A* == BLS and CFP-A* == CFP (same objective and route)."""
    P = get_scenario("turkey-osm-karaman-konya").prepare()
    plans = {a: get_planner(a).plan(P) for a in ("bls", "bls-astar", "cfp", "cfp-astar")}
    assert all(p.feasible for p in plans.values())
    for slow, fast in (("bls", "bls-astar"), ("cfp", "cfp-astar")):
        a, b = plans[slow], plans[fast]
        assert a.total_time_s == pytest.approx(b.total_time_s, rel=1e-9, abs=1e-6)
        assert a.decision.edges == b.decision.edges
    assert plans["cfp"].total_time_s <= plans["bls"].total_time_s + 1e-6
    assert plans["bls-astar"].stats.settled > 0


def test_viewer_payload_carries_the_attribution():
    from evroute.viz.export import render_viewer_html, viewer_payload

    sc = get_scenario("turkey-osm-karaman-konya")
    payload = viewer_payload(sc, [], robustness_runs=0)
    assert payload["scenario"]["meta"]["attribution"] == ATTRIBUTION
    html = render_viewer_html(payload)
    assert len(html.encode("utf-8")) < 8_000_000


@pytest.mark.slow
@pytest.mark.parametrize("name", OSM_PRESETS)
def test_presets_plan_feasibly(name):
    sc = get_scenario(name)
    p = get_planner("bls-astar").plan(sc)
    assert p.feasible, p
    assert p.distance_m > 0.9 * haversine_m(*_latlon(sc, sc.query.origin), *_latlon(sc, sc.query.destination))


def _latlon(sc, v: int) -> tuple[float, float]:
    return sc.network.lat[v], sc.network.lon[v]


@pytest.mark.slow
def test_static_map_figure_carries_the_attribution():
    pytest.importorskip("seaborn")
    from evroute.viz import style
    from evroute.viz.figures import plot_map

    sc = get_scenario("turkey-osm-karaman-konya")
    p = get_planner("bls-astar").plan(sc)
    fig = plot_map(sc, [p])
    try:
        assert ATTRIBUTION in [t.get_text() for ax in fig.axes for t in ax.texts]
    finally:
        style.close(fig)
