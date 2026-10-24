"""Tests of the instance generators: Turkey case study, synthetic families, OSM adapters, presets."""

from __future__ import annotations

import json
import math
import random
import statistics
import time

import pytest

from evroute import Scenario
from evroute.generators import synthetic, turkey
from evroute.generators.osm import from_networkx, load_osm, parse_maxspeed, scenario_from_osm, stations_from_csv
from evroute.generators.presets import PRESETS, get_scenario, list_scenarios, register_scenario
from evroute.generators.synthetic import (
    GridIndex,
    Terrain,
    calibrate_daily_sessions,
    corridor_scenario,
    country_scenario,
    parse_clock,
    random_geometric_scenario,
    split_overrides,
    subnetwork,
)
from evroute.generators.turkey import find_place, road_distance_km, turkey_network, turkey_scenario, turkey_stations
from evroute.data import read_text
from evroute.queueing import station_wait_profile
from evroute.search import dijkstra, min_level_weights, path_from_pred
from evroute.units import haversine_m

METROPOLITAN = [
    "Adana", "Ankara", "Antalya", "Aydın", "Balıkesir", "Bursa", "Denizli", "Diyarbakır", "Erzurum",
    "Eskişehir", "Gaziantep", "Hatay", "İstanbul", "İzmir", "Kahramanmaraş", "Kayseri", "Kocaeli", "Konya",
    "Malatya", "Manisa", "Mardin", "Mersin", "Muğla", "Ordu", "Sakarya", "Samsun", "Şanlıurfa", "Tekirdağ",
    "Trabzon", "Van",
]
JUNCTIONS = ["Pozantı", "Afyonkarahisar", "Bolu", "Gerede", "Aksaray", "Ereğli (Konya)", "Akseki",
             "Seydişehir", "Tarsus", "Silifke", "Mut", "Karaman"]
#: Real charging brands that must never appear (generic operators only).
BRANDS = ["zes", "eşarj", "esarj", "trugo", "voltrun", "sharz", "tesla", "beefull", "astor", "otowatt", "enyakıt"]


def _fastest_path_stops(sc: Scenario) -> tuple[int, float]:
    """Lower bound on the number of stops along the fastest path, and its energy."""
    P = sc.prepare()
    w = min_level_weights(P, "time")
    _, pred = dijkstra(P.net, P.origin, w, target=P.destination)
    path = path_from_pred(P.net, pred, P.origin, P.destination)
    assert path, "destination unreachable"
    energy = sum(P.edge_energy[0][e] for e in path)
    need = energy - (P.e_init - P.e_dest_min)
    return max(0, math.ceil(need / (P.e_charge_cap - P.e_min))), energy


def _roundtrip(sc: Scenario) -> None:
    d = sc.to_dict()
    sc2 = Scenario.from_dict(json.loads(json.dumps(d, ensure_ascii=False)))
    assert sc2.to_dict() == d
    assert sc2.prepare().capacity == pytest.approx(sc.prepare().capacity)


# =============================================================== helpers
def test_parse_clock_and_overrides():
    assert parse_clock("08:00") == 8 * 3600
    assert parse_clock("23:59:30") == 23 * 3600 + 59 * 60 + 30
    assert parse_clock(1234.5) == 1234.5
    with pytest.raises(ValueError):
        parse_clock("8h")
    cond, pref = split_overrides({"objective": "cost", "wind_speed_ms": 4.0, "speed_factors": [1.0, 0.9]})
    assert cond == {"wind_speed_ms": 4.0}
    assert pref == {"objective": "cost", "speed_factors": (1.0, 0.9)}
    with pytest.raises(TypeError):
        split_overrides({"no_such_field": 1})


def test_grid_index_matches_brute_force():
    rng = random.Random(7)
    xs = [rng.uniform(0, 1000) for _ in range(800)]
    ys = [rng.uniform(0, 600) for _ in range(800)]
    idx = GridIndex(xs, ys)
    for _ in range(60):
        x, y = rng.uniform(-200, 1200), rng.uniform(-200, 800)
        brute = sorted(((xs[i] - x) ** 2 + (ys[i] - y) ** 2, i) for i in range(len(xs)))
        got = idx.k_nearest(x, y, 6)
        assert [round(d, 6) for d, _ in got] == [round(d, 6) for d, _ in brute[:6]]
        assert idx.nearest(x, y) == brute[0][1]
        r = rng.uniform(10, 150)
        assert sorted(idx.within(x, y, r)) == sorted(i for d, i in brute if d < r * r)


def test_grid_index_degenerate_inputs_do_not_crawl():
    """One point, coincident points and collinear points once made far queries scan ~10^5 empty rings."""
    t0 = time.perf_counter()
    one = GridIndex([231_656.0], [87_461.0])
    got = one.k_nearest(10_000.0, 10_000.0, 3)  # 220 km from the only point
    assert len(got) == 1 and got[0][1] == 0
    assert one.nearest(231_656.0, 87_461.0) == 0
    same = GridIndex([5.0] * 10, [7.0] * 10)
    assert len(same.k_nearest(0.0, 0.0, 3)) == 3 and same.nearest(5.0, 7.0) in range(10)
    xs = [i * 1000.0 for i in range(500)]
    line = GridIndex(xs, [0.0] * 500)  # collinear: the area-based cell size would be zero
    rng = random.Random(3)
    for _ in range(40):
        x, y = rng.uniform(-5e4, 5.5e5), rng.choice((0.0, 1e3, 1e6, -2e5))
        brute = sorted(((xs[i] - x) ** 2 + y * y, i) for i in range(500))[:4]
        assert [round(d, 3) for d, _ in line.k_nearest(x, y, 4)] == [round(d, 3) for d, _ in brute]
    assert time.perf_counter() - t0 < 2.0


def test_terrain_is_smooth_and_non_negative():
    t = Terrain(300e3, 300e3, seed=3)
    vals = [t(x * 3e3, y * 3e3) for x in range(101) for y in range(0, 101, 10)]
    assert min(vals) >= 0.0
    assert max(vals) > 800.0  # mountain ranges exist
    # 100 m apart the surface never jumps by more than ~10 % grade
    for x in range(0, 300_000, 7_000):
        assert abs(t(x + 100.0, 150e3) - t(x, 150e3)) < 10.0


def test_calibrated_daily_sessions_hits_target_peak_wait():
    for target_min, plugs, service, prof in [(30, 6, 30, "highway"), (8, 2, 35, "highway"), (4, 4, 120, "urban")]:
        n = calibrate_daily_sessions(target_min * 60.0, plugs, service, prof)
        wp = station_wait_profile(n, list(synthetic._profile(prof)), service, plugs)
        assert max(wp.waits_s) == pytest.approx(target_min * 60.0, rel=1e-6)
    assert calibrate_daily_sessions(0.0, 4, 30) == 0.0


def test_subnetwork_remaps_twins_and_names():
    sc = corridor_scenario()
    net = sc.network
    keep = [v for v in net.nodes() if v % 3 != 1]
    sub, old_to_new = subnetwork(net, keep)
    assert sub.num_nodes == len(keep)
    for e in sub.edges():
        t = sub.twin[e]
        if t >= 0:
            assert sub.tail[t] == sub.head[e] and sub.head[t] == sub.tail[e]
    for v, name in net.node_name.items():
        if old_to_new[v] >= 0:
            assert sub.node_name[old_to_new[v]] == name


# =============================================================== Turkey
def test_turkey_places_and_connectivity():
    net = turkey_network()
    assert net.is_strongly_connected()
    assert net.lat is not None and net.lon is not None
    places = turkey.turkey_places()
    settlements = [p for p in places if p.is_settlement]
    assert len(settlements) >= 65
    assert sum(p.metro for p in places) == 30
    names = set(net.node_name.values())
    for city in METROPOLITAN + JUNCTIONS:
        assert find_place(city).name in names, city
    for p in places:  # stored coordinates are the table coordinates
        v = turkey.place_node(p.key)
        assert net.lat[v] == pytest.approx(p.lat) and net.lon[v] == pytest.approx(p.lon)
        assert 35.5 < p.lat < 42.5 and 25.5 < p.lon < 45.0  # inside Turkey's bounding box
        assert net.out_edges[v], p.key


def test_turkey_spellings_resolve():
    ist = turkey.place_node("İstanbul")
    for spelling in ("istanbul", "ISTANBUL", "Istanbul", "İSTANBUL", " istanbul "):
        assert turkey.place_node(spelling) == ist
    assert find_place("izmit").key == "kocaeli"
    assert find_place("Adapazari").key == "sakarya"
    assert find_place("ANTAKYA").key == "hatay"
    assert find_place("Eregli").key == "eregli_konya"
    assert find_place("ereğli (konya)").key == "eregli_konya"
    assert find_place("Konya").key == "konya"
    assert find_place("afyon").key == "afyonkarahisar"
    with pytest.raises(KeyError, match="did you mean"):
        find_place("Karamann")


def test_turkey_road_distances_are_realistic():
    # shortest road distance (Dijkstra over length) vs. official tables
    assert 420.0 <= road_distance_km("Ankara", "İstanbul") <= 480.0
    assert 100.0 <= road_distance_km("Karaman", "Konya") <= 130.0
    assert 540.0 <= road_distance_km("İzmir", "Ankara") <= 620.0
    net = turkey_network()
    d, _ = dijkstra(net, turkey.place_node("Ankara"), net.length)
    assert d[turkey.place_node("Konya")] / 1000 == pytest.approx(258, rel=0.08)
    assert d[turkey.place_node("Erzurum")] / 1000 == pytest.approx(880, rel=0.08)
    assert road_distance_km("İstanbul", "Bursa") == pytest.approx(155, rel=0.1)
    assert road_distance_km("Mersin", "Adana") == pytest.approx(70, rel=0.12)


def test_turkey_geometry_lengths_grades_and_passes():
    net = turkey_network()
    assert net.lat is not None and net.lon is not None
    for e in net.edges():
        u, v = net.tail[e], net.head[e]
        flat = haversine_m(net.lat[u], net.lon[u], net.lat[v], net.lon[v])
        chord = math.hypot(flat, net.elevation[v] - net.elevation[u])
        assert net.length[e] >= chord - 1e-6
        # also never shorter than the projected straight line (Euclidean A* bounds stay admissible)
        assert net.length[e] >= math.hypot(net.euclidean(u, v), net.elevation[v] - net.elevation[u])
        assert net.length[e] <= 20_000.0
        assert abs(net.grade(e)) < 0.08
        assert net.twin[e] >= 0 and net.twin[net.twin[e]] == e
        assert net.road_class[e] in ("motorway", "trunk", "primary")
    seg_km = sorted(net.length[e] / 1000 for e in net.edges())
    assert 8.0 <= statistics.median(seg_km) <= 12.0
    expected = {"Sertavul Geçidi": 1610, "Alacabel Geçidi": 1825, "Kop Geçidi": 2409, "Bolu Dağı": 900,
                "Gülek Boğazı": 1050, "Çubuk Beli": 1000}
    for name, z in expected.items():
        v = net.node_by_name(name)
        assert net.elevation[v] == pytest.approx(z)
        # the pass is the high point of its road: both neighbours are lower
        nbrs = {net.head[e] for e in net.out_edges[v]}
        assert all(net.elevation[w] < z for w in nbrs), name
    assert max(net.elevation) == pytest.approx(2409)
    assert min(net.elevation) >= 0.0
    classes = {net.edge_name[e] for e in net.edges() if net.road_class[e] == "motorway"}
    for ref in ("O-3", "O-4", "O-5", "O-7", "O-21", "O-31", "O-32", "O-52"):
        assert ref in classes


def test_turkey_stations_are_generic_and_calibrated():
    net = turkey_network()
    stations = turkey_stations()
    assert [s.id for s in stations] == list(range(len(stations)))
    # every city and town has a DC charger
    dc_nodes = {s.node for s in stations if s.kind == "DC"}
    for p in turkey.turkey_places():
        if p.is_settlement:
            assert turkey.place_node(p.key) in dc_nodes, p.name
    service_areas = [s for s in stations if "service area" in s.name]
    assert len(service_areas) >= 40
    assert any(s.power_kw >= 300 for s in service_areas)
    for s in stations:
        assert 0 <= s.node < net.num_nodes
        assert 2 <= s.plugs <= 8
        assert s.operator.startswith("Operator ")
        assert not any(b in (s.name + s.operator).casefold() for b in BRANDS)
        if s.kind == "AC":
            assert s.power_kw == 22 and 9.0 <= s.price_per_kwh <= 11.0
        else:
            assert 60 <= s.power_kw <= 300 and 13.5 <= s.price_per_kwh <= 18.5
        assert s.demand_profile in ("highway", "urban")
    peaks, means, peak_hours = [], [], []
    for s in stations:
        wp = s.wait_profile()
        pk = max(wp.waits_s)
        peaks.append(pk / 60)
        means.append(wp.mean() / 60)
        peak_hours.append(wp.knots_s[wp.waits_s.index(pk)] / 3600)
    assert statistics.median(means) < 15.0  # typical waits 0-15 min
    assert sorted(peaks)[int(0.8 * len(peaks))] <= 15.0
    assert 20.0 <= max(peaks) <= 40.0  # peaks of 20-40 min at the busiest sites ...
    busiest = [h for p, h in zip(peaks, peak_hours) if p >= 20.0]
    assert busiest and all(12.0 <= h <= 20.0 for h in busiest)  # ... in the afternoon


def test_turkey_scenario_defaults_and_overrides():
    sc = turkey_scenario()
    assert sc.currency == "TRY"
    assert sc.preferences.value_of_time_per_h == 250.0
    assert sc.preferences.soh_cost_per_pct == 25_000.0
    assert sc.query.departure_s == 8 * 3600
    assert sc.query.initial_soc == 0.8
    assert sc.conditions.temperature_c == 18.0
    assert sc.vehicle.key == "togg_t10x_lr"
    assert sc.network.node_name[sc.query.origin] == "Karaman"
    assert sc.network.node_name[sc.query.destination] == "İstanbul"
    sc2 = turkey_scenario("izmir", "ERZURUM", temperature_c=-5, departure="06:30", objective="cost",
                          wind_speed_ms=6.0, reserve_soc=0.15)
    assert sc2.preferences.objective == "cost" and sc2.preferences.reserve_soc == 0.15
    assert sc2.preferences.value_of_time_per_h == 250.0
    assert sc2.conditions.wind_speed_ms == 6.0 and sc2.conditions.temperature_c == -5
    assert sc2.query.departure_s == 6.5 * 3600
    assert sc2.network is sc.network  # cached network
    assert sc2.stations is not sc.stations and sc2.stations[0] is not sc.stations[0]
    with pytest.raises(TypeError):
        turkey_scenario(colour="red")
    with pytest.raises(ValueError):
        turkey_scenario("Konya", "konya")


def test_turkey_summary():
    s = turkey.turkey_summary()
    assert s["metropolitan"] == 30 and s["places"]["city"] + s["places"]["town"] >= 65
    assert s["places"]["pass"] >= 8 and s["max_elevation_m"] == pytest.approx(2409)
    assert s["stations"] == s["stations_dc"] + s["stations_ac"] and s["stations_hpc"] >= 8
    assert 9000 < sum(s["road_km"].values()) < 25000
    json.dumps(s)


def test_turkey_determinism_and_roundtrip():
    a = turkey_network(seed=0, cached=False).to_dict()
    b = turkey_network(seed=0, cached=False).to_dict()
    assert a == b
    c = turkey_network(seed=1, cached=False).to_dict()
    assert c["tail"] == a["tail"] and c["elevation"] != a["elevation"]  # same topology, other undulation
    _roundtrip(turkey_scenario("Antalya", "Ankara"))


def test_turkey_trips_need_charging():
    for o, d in [("Karaman", "İstanbul"), ("Antalya", "Ankara"), ("İzmir", "Erzurum")]:
        stops, energy = _fastest_path_stops(turkey_scenario(o, d))
        assert stops >= 1, (o, d)
        assert 12.0 < energy / road_distance_km(o, d) * 100 < 30.0  # kWh/100 km of a C-SUV


def test_turkey_road_table_detour_column():
    links = turkey.turkey_links()
    assert all(1.0 <= lk.detour <= 2.0 for lk in links)
    winding = {(lk.a, lk.b): lk.detour for lk in links if lk.detour != 1.0}
    assert winding == {("mut", "silifke"): 1.3}
    # the Goksu canyon road is ~85 km (a plain 'hilly' primary road would be only ~64 km)
    assert 75.0 <= road_distance_km("Mut", "Silifke") <= 95.0
    assert 235.0 <= road_distance_km("Karaman", "Mersin") <= 290.0
    header = read_text("turkey_roads.csv").splitlines()[0]
    assert header == "id,from,to,class,ref,terrain,traffic,detour,via"


def test_turkey_stations_cover_the_whole_network():
    """No point of the network is farther than 80 km (by road) from a charging site."""
    import heapq

    net = turkey_network()
    dist = [math.inf] * net.num_nodes
    heap = []
    for s in turkey_stations():
        if dist[s.node] > 0.0:
            dist[s.node] = 0.0
            heap.append((0.0, s.node))
    heapq.heapify(heap)
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for e in net.out_edges[u]:
            w, nd = net.head[e], d + net.length[e]
            if nd < dist[w]:
                dist[w] = nd
                heapq.heappush(heap, (nd, w))
    assert max(dist) < 80_000.0


def _bls_astar_or_skip():
    try:
        from evroute.algorithms import planner_class

        planner_class("bls-astar")
    except ImportError:
        pytest.skip("bls-astar not available")


def test_turkey_random_city_pairs_are_plannable():
    _bls_astar_or_skip()
    from evroute import plan
    from evroute.evaluate import check_objective

    cities = [p for p in turkey.turkey_places() if p.is_settlement]
    rng = random.Random(2024)
    for _ in range(8):
        a, b = rng.sample(cities, 2)
        sc = turkey_scenario(a.key, b.key)
        route = plan(sc, "bls-astar")
        assert route.feasible, (a.name, b.name, route.message)
        assert check_objective(route)
        assert route.distance_m >= 0.95 * haversine_m(*_latlon(a), *_latlon(b))  # never shorter than the crow flies


def _latlon(p):
    return p.lat, p.lon


def test_karaman_mersin_route_depends_on_the_objective():
    """The preset's story: the time-optimal plan takes the motorway, the cost-optimal one the Sertavul pass road."""
    _bls_astar_or_skip()
    from evroute import plan

    def uses_pass_road(sc, route):
        return any(sc.network.edge_name.get(e) == "D-715" for e in route.edges)

    fast = get_scenario("turkey-karaman-mersin")
    cheap = get_scenario("turkey-karaman-mersin", objective="cost")
    r_fast, r_cheap = plan(fast, "bls-astar"), plan(cheap, "bls-astar")
    assert r_fast.feasible and r_cheap.feasible
    assert not uses_pass_road(fast, r_fast) and uses_pass_road(cheap, r_cheap)
    assert r_cheap.distance_m < r_fast.distance_m and r_cheap.total_time_s > r_fast.total_time_s
    assert r_cheap.generalized_cost < r_fast.generalized_cost  # and it really is cheaper in the model


def test_istanbul_ankara_rush_shows_the_value_of_queue_awareness():
    """A plan made without queues, evaluated with the real afternoon queues, loses a lot of time."""
    _bls_astar_or_skip()
    from evroute import evaluate, plan

    sc = get_scenario("turkey-istanbul-ankara-rush")
    aware = plan(sc, "bls-astar")
    blind = plan(sc.with_preferences(queue_aware=False), "bls-astar")
    real = evaluate(sc.prepare(), blind.decision, algorithm="queue-blind")
    assert aware.feasible and blind.feasible and real.feasible
    assert real.wait_time_s >= aware.wait_time_s + 15 * 60  # measured: ~50 min versus ~9 min
    assert real.total_time_s >= aware.total_time_s + 15 * 60


def test_plan_turkey_karaman_istanbul_with_bls_astar():
    try:
        from evroute.algorithms import planner_class

        planner_class("bls-astar")
    except ImportError:
        pytest.skip("bls-astar not available")
    from evroute import plan
    from evroute.evaluate import check_objective

    sc = get_scenario("turkey-karaman-istanbul")
    route = plan(sc, "bls-astar")
    assert route.feasible, route.message
    assert check_objective(route)
    assert 1 <= route.num_stops <= 3
    assert 700e3 <= route.distance_m <= 820e3
    assert route.currency == "TRY" and route.money > 0


# =============================================================== synthetic
def test_country_determinism_sizes_and_connectivity():
    synthetic._country_cached.cache_clear()
    a = country_scenario(n_cities=20, region_km=400, nodes_target=3000, seed=5).to_dict()
    synthetic._country_cached.cache_clear()
    b = country_scenario(n_cities=20, region_km=400, nodes_target=3000, seed=5).to_dict()
    assert a == b
    c = country_scenario(n_cities=20, region_km=400, nodes_target=3000, seed=6).to_dict()
    assert c["network"]["x"] != a["network"]["x"]
    for target in (2000, 5000, 12000):
        sc = country_scenario(n_cities=30, region_km=600, nodes_target=target, seed=1)
        net = sc.network
        assert abs(net.num_nodes - target) <= 0.1 * target
        assert net.is_strongly_connected()
        classes = set(net.road_class)
        assert {"motorway", "trunk", "secondary", "urban", "residential"} <= classes
        assert any(net.twin[e] < 0 for e in net.edges())  # one-way streets exist
        assert sc.query.origin != sc.query.destination
        for s in sc.stations:
            assert 0 <= s.node < net.num_nodes and 1 <= s.plugs <= 8
        cities = sc.meta["cities"]
        assert len(cities) == 30 and all(net.node_name[c["node"]] == c["name"] for c in cities)
        for e in net.edges():
            assert abs(net.grade(e)) <= synthetic.MAX_GRADE + 1e-9
            u, v = net.tail[e], net.head[e]
            assert net.length[e] >= math.hypot(net.euclidean(u, v), net.elevation[v] - net.elevation[u])


def test_country_scales_to_50k_nodes_quickly():
    t0 = time.perf_counter()
    sc = country_scenario(n_cities=50, region_km=800, nodes_target=50_000, seed=11)
    elapsed = time.perf_counter() - t0
    assert abs(sc.network.num_nodes - 50_000) <= 5_000
    assert elapsed < 20.0  # ~1 s on a laptop
    synthetic._country_cached.cache_clear()


def test_country_default_needs_one_to_three_stops():
    for kw in ({}, {"n_cities": 25, "region_km": 500, "nodes_target": 2000, "seed": 1}):
        sc = country_scenario(**kw)
        stops, _ = _fastest_path_stops(sc)
        assert 1 <= stops <= 3
    # cities can be addressed by name (any case) or node id
    dest_name = sc.network.node_name[sc.query.destination]
    rev = country_scenario(**kw, origin=dest_name.upper(), destination=sc.query.origin)
    assert rev.query.origin == sc.query.destination and rev.query.destination == sc.query.origin
    with pytest.raises(KeyError):
        country_scenario(**kw, origin="Atlantis")


def _reach(net, source: int, forward: bool) -> set[int]:
    adj, other = (net.out_edges, net.head) if forward else (net.in_edges, net.tail)
    seen, stack = {source}, [source]
    while stack:
        u = stack.pop()
        for e in adj[u]:
            if other[e] not in seen:
                seen.add(other[e])
                stack.append(other[e])
    return seen


def test_city_grid_gates_are_strongly_connected_to_the_centre():
    """Highways attach at gates; even when every residential street is one-way a gate must stay in the core."""
    from evroute.network import NetworkBuilder

    for seed in range(6):
        for oneway in (0.0, 0.5, 1.0):
            b = NetworkBuilder()
            ids, gates = synthetic._city_grid(b, Terrain(60e3, 60e3, seed), 30e3, 30e3, 13, 300.0,
                                              random.Random(seed), oneway)
            net = b.build()
            centre = ids[6][6]
            assert len(gates) >= 4
            out, back = _reach(net, centre, True), _reach(net, centre, False)
            for g in gates:
                assert g in out and g in back, (seed, oneway, g)


def test_country_with_two_cities_is_connected():
    """Regression: with few cities the single highway used to attach at a one-way boundary node."""
    for kw in ({"region_km": 200, "nodes_target": 3000, "seed": 0}, {"region_km": 60, "nodes_target": 3000, "seed": 0},
               {"region_km": 60, "nodes_target": 50, "seed": 2}):
        sc = country_scenario(n_cities=2, **kw)
        assert sc.network.is_strongly_connected()
        assert len({c["node"] for c in sc.meta["cities"]}) == 2
        assert sc.query.origin != sc.query.destination
    synthetic._country_cached.cache_clear()


def test_random_geometric_collapse_is_reported():
    """Too sparse/one-way a graph used to leave a single node (and a hung nearest-neighbour query)."""
    with pytest.raises(ValueError, match="strongly connected component"):
        random_geometric_scenario(4, seed=0, k=2, oneway_fraction=0.3)
    with pytest.raises(ValueError, match="at least 4"):
        random_geometric_scenario(3)


def test_random_geometric_scenario():
    a = random_geometric_scenario(1500, seed=4)
    synthetic._rgg_cached.cache_clear()
    b = random_geometric_scenario(1500, seed=4)
    assert a.to_dict() == b.to_dict()
    net = a.network
    assert net.is_strongly_connected()
    assert 0.8 * 1500 <= net.num_nodes <= 1500
    assert any(net.twin[e] < 0 for e in net.edges())
    # Gabriel filter: the graph is planar-ish, average out-degree stays small
    assert 2.0 <= net.num_edges / net.num_nodes <= 4.0
    stops, _ = _fastest_path_stops(random_geometric_scenario(3000))
    assert 1 <= stops <= 3
    _roundtrip(a)


def test_corridor_scenario():
    sc = corridor_scenario()
    net = sc.network
    assert net.is_strongly_connected()
    assert len(sc.stations) == len(synthetic.CORRIDOR_STATIONS) + 1
    powers = {s.power_kw for s in sc.stations}
    prices = {s.price_per_kwh for s in sc.stations}
    assert min(powers) <= 22 and max(powers) >= 350 and len(prices) >= 4
    assert max(net.elevation) == pytest.approx(1500.0, abs=30.0)
    assert "Old valley road" in net.edge_name.values()
    stops, _ = _fastest_path_stops(sc)
    assert 1 <= stops <= 3
    flat = corridor_scenario(400, mountain=False)
    assert max(flat.network.elevation) < 600.0
    _roundtrip(sc)


def test_corridor_partial_charging_matters():
    try:
        from evroute.algorithms import planner_class

        planner_class("bls-astar")
    except ImportError:
        pytest.skip("bls-astar not available")
    from evroute import plan
    from evroute.evaluate import check_objective

    sc = corridor_scenario()
    route = plan(sc, "bls-astar")
    assert route.feasible and check_objective(route)
    assert route.num_stops >= 1
    cap = sc.prepare().e_charge_cap
    assert any(s.energy_out_kwh < cap - 1e-6 for s in route.stops)  # some stop is a partial charge


# =============================================================== OSM adapters
class FakeGraph:
    """Minimal stand-in for a networkx (Multi)DiGraph."""

    def __init__(self, directed: bool = True, multi: bool = True, crs: str | None = "epsg:4326") -> None:
        self._nodes: dict = {}
        self._edges: list = []
        self._directed, self._multi = directed, multi
        self.graph = {"crs": crs} if crs else {}

    def add_node(self, n, **d):
        self._nodes[n] = d

    def add_edge(self, u, v, key=0, **d):
        self._edges.append((u, v, key, d))

    def nodes(self, data=False):
        return list(self._nodes.items()) if data else list(self._nodes)

    def edges(self, keys=False, data=False):
        out = []
        for u, v, k, d in self._edges:
            t = (u, v)
            if keys:
                t += (k,)
            if data:
                t += (d,)
            out.append(t)
        return out

    def is_directed(self):
        return self._directed

    def is_multigraph(self):
        return self._multi


def _fake_osm() -> FakeGraph:
    G = FakeGraph()
    coords = {101: (37.180, 33.210, 1030.0), 102: (37.190, 33.230, 1035.0), 103: (37.200, 33.215, 1040.0),
              104: (37.185, 33.250, None), 105: (37.300, 33.300, 1100.0)}
    for n, (lat, lon, z) in coords.items():
        d = {"x": lon, "y": lat}
        if z is not None:
            d["elevation"] = z
        G.add_node(n, **d)

    def two_way(u, v, **d):
        G.add_edge(u, v, 0, **d)
        G.add_edge(v, u, 0, **d)

    two_way(101, 102, length=2100.0, highway="motorway", speed_kph=118.0, ref="D-715")
    two_way(102, 103, length=1900.0, highway=["secondary", "primary"], maxspeed="30 mph", name="Main St")
    two_way(103, 101, length=2300.0, highway="tertiary", maxspeed=["50", "70"])
    G.add_edge(102, 104, 0, length=2000.0, highway="residential", maxspeed="TR:urban", oneway=True)
    G.add_edge(104, 101, 0, length=3600.0, highway="unclassified")
    G.add_edge(101, 105, 0, length=15000.0, highway="trunk")  # dead end: dropped by the SCC filter
    G.add_edge(103, 103, 0, length=10.0, highway="service")  # self-loop: dropped
    G.add_edge(101, 102, 1, length=2500.0, highway="primary")  # parallel edge
    return G


def test_parse_maxspeed():
    assert parse_maxspeed("50") == 50.0
    assert parse_maxspeed("30 mph") == pytest.approx(48.28032)
    assert parse_maxspeed(["50", "70"]) == 60.0
    assert parse_maxspeed("50;70") == 60.0
    assert parse_maxspeed("TR:urban") == 50.0 and parse_maxspeed("DE:motorway") == 120.0
    assert parse_maxspeed("none") == 130.0
    assert parse_maxspeed("signals") is None and parse_maxspeed(None) is None and parse_maxspeed("abc") is None


def test_from_networkx_fake_multidigraph():
    net, mapping = from_networkx(_fake_osm(), return_mapping=True)
    assert set(mapping) == {101, 102, 103, 104}  # 105 is not strongly connected
    assert net.num_nodes == 4 and net.is_strongly_connected()
    assert net.lat is not None and net.lat[mapping[101]] == pytest.approx(37.18)
    assert net.elevation[mapping[104]] == 0.0  # missing elevation -> default

    def edge(u, v, cls=None):
        es = [e for e in net.out_edges[mapping[u]] if net.head[e] == mapping[v]]
        if cls:
            es = [e for e in es if net.road_class[e] == cls]
        assert es
        return es[0]

    e = edge(101, 102, "motorway")
    assert net.speed_kmh[e] == 118.0 and net.length[e] == 2100.0 and net.edge_name[e] == "D-715"
    assert net.road_class[net.twin[e]] == "motorway" and net.head[net.twin[e]] == mapping[101]
    assert net.road_class[edge(101, 102, "primary")] == "primary"  # parallel edge kept
    e = edge(102, 103)
    assert net.road_class[e] == "primary" and net.speed_kmh[e] == pytest.approx(48.28032)
    assert net.edge_name[e] == "Main St"
    e = edge(103, 101)
    assert net.road_class[e] == "secondary" and net.speed_kmh[e] == 60.0
    e = edge(102, 104)
    assert net.road_class[e] == "residential" and net.speed_kmh[e] == 50.0 and net.twin[e] == -1
    e = edge(104, 101)
    assert net.road_class[e] == "urban" and net.speed_kmh[e] == 50.0
    for e in net.edges():
        assert net.tail[e] != net.head[e]


def test_from_networkx_undirected_projected():
    G = FakeGraph(directed=False, multi=False, crs="EPSG:32636")
    for n, (x, y) in enumerate([(0.0, 0.0), (5000.0, 0.0), (5000.0, 4000.0)]):
        G.add_node(n, x=x, y=y)
    G.add_edge(0, 1, length=5200.0, highway="primary")
    G.add_edge(1, 2, highway="secondary")  # no length -> straight line
    net = from_networkx(G)
    assert net.num_edges == 4 and net.lat is None
    e = next(e for e in net.edges() if net.tail[e] == 1 and net.head[e] == 2)
    assert net.length[e] == pytest.approx(4000.0)
    with pytest.raises(ValueError):
        from_networkx(FakeGraph())


def _tiny_metric_graph(crs: str | None) -> FakeGraph:
    """Three nodes whose metric x/y happen to lie inside the lon/lat range."""
    G = FakeGraph(directed=False, multi=False, crs=crs)
    for n, (x, y) in enumerate([(10.0, 10.0), (60.0, 10.0), (60.0, 50.0)]):
        G.add_node(n, x=x, y=y)
    G.add_edge(0, 1, highway="secondary")
    G.add_edge(1, 2, highway="secondary")
    return G


def test_from_networkx_uses_the_declared_crs():
    # a projected CRS wins over the numeric-range guess ...
    net = from_networkx(_tiny_metric_graph("EPSG:32636"))
    assert net.lat is None
    e = next(e for e in net.edges() if net.tail[e] == 1 and net.head[e] == 2)
    assert net.length[e] == pytest.approx(40.0)  # metres, not degrees
    # ... a geographic CRS (OSMnx style, also the old dict form) means lon/lat ...
    for crs in ("epsg:4326", "EPSG:4326", "{'init': 'epsg:4326'}"):
        geo = from_networkx(_tiny_metric_graph(crs))
        assert geo.lat is not None and geo.num_nodes == 3
        assert geo.length[next(iter(geo.edges()))] > 1000.0  # degrees converted to metres
    # ... without CRS information the numeric range decides (legacy behaviour)
    assert from_networkx(_tiny_metric_graph(None)).lat is not None
    # a geographic CRS with impossible coordinates is an error, not a silent misreading
    bad = FakeGraph(directed=False, multi=False, crs="epsg:4326")
    bad.add_node(0, x=500000.0, y=4000000.0)
    bad.add_node(1, x=500100.0, y=4000000.0)
    bad.add_edge(0, 1, highway="primary")
    with pytest.raises(ValueError, match="geographic CRS"):
        from_networkx(bad)
    # lat/lon node attributes (projected OSMnx graphs keep them) always win
    G = FakeGraph(directed=False, multi=False, crs="EPSG:32636")
    for n, (x, y, la, lo) in enumerate([(5e5, 4.1e6, 37.0, 33.0), (5.01e5, 4.1e6, 37.0, 33.01)]):
        G.add_node(n, x=x, y=y, lat=la, lon=lo)
    G.add_edge(0, 1, highway="primary")
    net = from_networkx(G)
    assert net.lat is not None and net.lat[0] == pytest.approx(37.0)


def test_load_osm_without_osmnx_is_helpful():
    try:
        import osmnx  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="osmnx"):
            load_osm("Karaman, Turkey")
    else:  # pragma: no cover
        pytest.skip("osmnx installed; not downloading in unit tests")


def test_stations_from_csv(tmp_path):
    net = turkey_network()
    p = tmp_path / "stations.csv"
    p.write_text(
        "lat,lon,power_kw,kind,plugs,price,name\n"
        "37.875,32.49,150,DC,4,14.5,Konya test DC\n"
        "37.181,33.215,22,,2,9.9,Karaman AC\n"
        "0.0,0.0,50,DC,2,10,Gulf of Guinea\n",
        encoding="utf-8",
    )
    with pytest.warns(UserWarning, match="skipped"):
        st = stations_from_csv(p, net, first_id=10)
    assert [s.id for s in st] == [10, 11]
    assert st[0].node == turkey.place_node("Konya") and st[0].kind == "DC" and st[0].plugs == 4
    assert st[1].node == turkey.place_node("Karaman") and st[1].kind == "AC" and st[1].price_per_kwh == 9.9
    # semicolon CSV with decimal commas
    q = tmp_path / "semicolon.csv"
    q.write_text("LAT;LON;POWER_KW;PLUGS;PRICE;NAME\n39,9334;32,8597;180;6;15,2;Ankara\n", encoding="utf-8")
    st2 = stations_from_csv(q, net)
    assert st2[0].node == turkey.place_node("Ankara") and st2[0].price_per_kwh == pytest.approx(15.2)
    sc = scenario_from_osm(net, st + st2, (37.18, 33.21), turkey.place_node("Ankara"), currency="TRY",
                           objective="cost")
    assert sc.query.origin == turkey.place_node("Karaman") and sc.preferences.objective == "cost"
    sc.prepare()


# =============================================================== presets
REQUIRED_PRESETS = [
    "turkey-karaman-istanbul", "turkey-antalya-ankara", "turkey-izmir-erzurum",
    "turkey-istanbul-antalya-winter", "corridor", "country-small", "country-medium", "country-large",
    "grid-small",
]


def test_preset_registry():
    infos = list_scenarios()
    names = [p["name"] for p in infos]
    for n in REQUIRED_PRESETS:
        assert n in names
    assert len(set(names)) == len(names)
    for p in infos:
        assert p["description"] and p["family"]
        assert len(p["description"]) <= 140 and "\n" not in p["description"]  # one readable line in listings
        assert isinstance(p["notes"], str)
        json.dumps(p)
    assert "turkey-istanbul-ankara-rush" in names and PRESETS["corridor"].notes
    assert all(p["family"] == "turkey" for p in list_scenarios("turkey"))
    with pytest.raises(KeyError, match="did you mean"):
        get_scenario("turkey-karaman-istanbull")
    with pytest.raises(TypeError):
        get_scenario("corridor", warp_drive=True)
    with pytest.raises(KeyError):
        register_scenario("corridor", corridor_scenario, "duplicate")


def test_preset_overrides_are_routed():
    sc = get_scenario("corridor", initial_soc=0.7, objective="cost", wind_speed_ms=3.0, length_km=300)
    assert sc.name == "corridor" and sc.meta["preset"] == "corridor"
    assert sc.query.initial_soc == 0.7
    assert sc.preferences.objective == "cost"
    assert sc.conditions.wind_speed_ms == 3.0
    assert sc.network.bbox()[2] < 320e3
    g = get_scenario("grid-small", temperature_c=-3.0, reserve_soc=0.2)
    assert g.conditions.temperature_c == -3.0 and g.preferences.reserve_soc == 0.2
    w = get_scenario("turkey-istanbul-antalya-winter")
    assert w.conditions.temperature_c == -5.0
    assert w.preferences.value_of_time_per_h == 250.0


@pytest.mark.parametrize("preset", ["grid-small", "corridor", "turkey-karaman-istanbul", "country-small",
                                    "random-geometric"])
def test_preset_name_override_works_for_every_builder(preset):
    """Regression: ``name=`` collided with the positional ``name`` and was forwarded to builders without one."""
    sc = get_scenario(preset, name="my-run")
    assert sc.name == "my-run" and sc.meta["preset"] == preset and sc.meta["overrides"]["name"] == "my-run"
    assert get_scenario(preset).name == preset


def test_presets_do_not_share_mutable_state():
    a = get_scenario("turkey-karaman-istanbul")
    n = len(a.stations)
    a.stations[0].price_per_kwh = 999.0
    a.stations.pop()
    a.conditions.temperature_c = -40.0
    b = get_scenario("turkey-karaman-istanbul")
    assert len(b.stations) == n and b.stations[0].price_per_kwh != 999.0 and b.conditions.temperature_c == 18.0
    assert b.network is a.network  # the (immutable) network is cached and shared
    c1, c2 = get_scenario("country-small"), get_scenario("country-small")
    assert c1.stations[0] is not c2.stations[0] and c1.network is c2.network


@pytest.mark.parametrize("name", ["corridor", "corridor-winter", "grid-small", "turkey-izmir-erzurum",
                                  "turkey-karaman-mersin"])
def test_presets_are_deterministic(name):
    assert get_scenario(name).to_dict() == get_scenario(name).to_dict()


@pytest.mark.parametrize("name", sorted(PRESETS))
def test_every_preset_prepares(name):
    sc = get_scenario(name)
    P = sc.prepare()
    assert P.net.num_nodes > 0 and P.stations
    assert sc.network.is_strongly_connected()
    assert P.origin != P.destination
    approx = PRESETS[name].approx_nodes
    if approx:
        assert 0.7 * approx <= sc.network.num_nodes <= 1.3 * approx
    if name.startswith("country") or name in ("random-geometric",):
        stops, _ = _fastest_path_stops(sc)
        assert 1 <= stops <= 3
