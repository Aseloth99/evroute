"""Turkey on real data: OpenStreetMap roads, SRTM elevations, OSM charging stations.

This module loads the bundled real-data network of Turkey
(``evroute/data/osm_turkey``) and turns it into scenarios.  It is the
real-data counterpart of the approximate case study of
:mod:`evroute.generators.turkey`:

==========================  ==================================  ==========================================
                            ``generators.turkey``               ``generators.osm_turkey``
==========================  ==================================  ==========================================
roads                       146 modelled intercity links        OpenStreetMap motorway, trunk, primary and
                                                                their ramps (+ 263 km of gap-closing
                                                                secondary), 44 673 nodes, 70 886 edges,
                                                                75 393 km
elevation                   gazetteer passes + undulation       SRTM 1" at every node
charging stations           290 synthetic                       711 public OSM stations (``"osm"``), the
                                                                synthetic set moved onto this network
                                                                (``"synthetic"``), or OSM plus flagged
                                                                synthetic DC gap-fillers (``"hybrid"``)
prices                      synthetic                           operator list prices (``prices.json``)
==========================  ==================================  ==========================================

Quick start::

    from evroute.generators.osm_turkey import osm_turkey_scenario
    sc = osm_turkey_scenario("Karaman", "İstanbul")                 # OSM stations
    sc = osm_turkey_scenario("izmir", "ERZURUM", stations="hybrid")  # + synthetic DC gap-fillers

**Licence and attribution.**  The road, station and place tables are a
derivative database of OpenStreetMap under the **ODbL 1.0**, © OpenStreetMap
contributors (:data:`ATTRIBUTION`); everything shown or published from them
(maps, figures, route plans, tables) must carry that credit -- the viewer and
the static map figures do it automatically for scenarios of this module
(``scenario.meta["attribution"]``).  Elevations are SRTM (NASA/USGS, public
domain).  The data README (``evroute/data/osm_turkey/README.md``) and
``DATA_LICENSE.md`` give the details; the build script is
``tools/build_osm_turkey.py`` in the repository.

Network
-------
OSM extract of 2026-10-27 (``meta.json`` has the replication timestamp and
the md5 of the PBF).  Degree-2 chains are contracted (split at every
elevation extremum with 20 m of relief and every 5 km), junctions closer
than 30 m are consolidated, and the largest strongly connected component is
kept.  Free-flow speeds are the ``maxspeed`` tag where present, otherwise
the legal defaults of Karayolları Trafik Yönetmeliği Art. 100 (50 km/h in
built-up areas, detected from the density of junctions with minor streets;
90 rural; 110 dual carriageway; 120 motorway; ramps the median of the
tagged ramps).  Over the 3 240 pairs of province centres the shortest road
distance deviates from the official KGM table by 2.52 % on average (89.5 %
of the pairs within 5 %); İstanbul-İzmir is 18.9 % shorter because the graph
may use the toll Osmangazi bridge and the KGM table does not, and Tunceli,
Yalova and Hakkâri are reached over detours where the national road is
missing or mapped as a minor road in OSM (``validation.csv``,
``meta.json``).  Nodes are projected around 39 N, 35 E like the approximate
network, so both share one map frame.

Charging stations (``stations=``)
---------------------------------
``"osm"``
    The 711 public ``amenity=charging_station`` objects of OSM within 3 km
    of the network (618 AC, 93 DC).  Kind, power and plugs come from the
    tags; where a tag is missing the build uses **defaults**: 22 kW AC,
    120 kW DC, 250 kW at a Tesla Supercharger, and 1 plug (64 % of the
    powers and 44 % of the plug counts are defaults, so plug counts are
    lower bounds).  OSM is far from complete: EPDK counted 48 971 sockets
    with a commercial charging service (public or with restricted access) in
    September 2026 (27 664 AC, 21 307 DC), so the OSM stations
    hold about 3.3 % of the AC and 1.9 % of the DC sockets, and there is no
    DC station east of 38 E.  Long eastern trips then depend on slow AC
    stops (İzmir → Erzurum with OSM stations alone: a greedy plan of
    46 h 34 min), and the exact planners may run out of their label budgets.
``"synthetic"``
    The 290 synthetic stations of :mod:`evroute.generators.turkey` (same
    seed, same powers, plugs, prices and calibrated queues), each moved to
    the nearest node of this network whose road class matches the station's
    road in the approximate network -- or to the nearest main-road node if
    that is more than :data:`SYNTHETIC_SNAP_SLACK_M` (5 km) closer, since a
    modelled road may lie far from the OSM road of its class -- and a city
    station to the node of the OSM place of the same name.  Same stations,
    real roads: the difference to the approximate case study is the network
    alone.
``"hybrid"``
    The OSM stations plus **synthetic DC gap-fillers** wherever a main road
    (motorway, trunk or primary) is more than ``gap_km`` (75 km) of road
    from every DC station: farthest-first, each filler goes to the nearest
    named place within 20 km of the farthest uncovered node (or to that
    node) until every main-road node is covered.  Fillers are flagged by
    ``operator="synthetic"`` and a name starting with ``"Synthetic DC"``;
    they have 120 or 150 kW and 2 or 4 plugs (seeded) and the default DC
    price.  No :class:`~evroute.scenario.Station` field is added, so the
    JSON of every other scenario stays unchanged.

``scenario.meta["station_info"]`` records, per station id, where its data
come from: the OSM object (``"osm": "node/123"``), the price source
(``"operator"`` or ``"default"``), the power and plug sources of the build
(``"tag"``, ``"voltage*amperage"``, ``"default:ac"`` ...), or
``"synthetic"`` / ``"gap-filler"``.

Prices
------
``prices.json`` is a dated snapshot (2026-10-27) of the public list prices
of Turkish operators in TRY/kWh, VAT included.  A station whose operator
published its price on its own page gets that **list price** -- the upper
end where the operator gives a band, since under the 2026 rules lower
prices are location or time-of-day discounts -- for its kind (AC or DC):
Trugo, ZES, WAT Mobilite, Astor Şarj, En Yakıt (DC only), Oncharge, Neva
Şarj, Efish, Aksa Şarj and Beefull.  Every other station (operator unknown,
or only press or aggregator figures) gets the **defaults**
:data:`DEFAULT_PRICE_TRY`: AC 9.99 and DC 14.24 TRY/kWh, the medians of the
operator-confirmed list prices.  Idle fees and loyalty prices are not
modelled.

**Tariff.**  The default :class:`~evroute.scenario.Tariff` stays neutral.
The amendment of the Şarj Hizmeti Yönetmeliği (Resmî Gazete 23.03.2026,
No. 33202) allows one AC and one DC price per operator at public stations
-- no tiers by charger power or state of charge -- so the "80 % rule" of
:data:`~evroute.generators.presets.DEMO_TARIFF` is an illustrative option
only, not the practice at Turkish public stations since March 2026.
Discounts by location and time of day are allowed, priced at the hour in
which the session starts: EVRoute's join-time rule (``model.md`` §5).
Operators may end a DC session at 85 % SoC; plan with
``max_charge_soc=0.85`` to respect that (the ``-dc85`` preset).

Queues (assumptions)
--------------------
Every station is the Erlang-C queue of :mod:`evroute.queueing` with
national averages derived from the EPDK statistics of September 2026
(:data:`QUEUE_MODEL`): 3.7 sessions per DC plug and 1.0 per AC plug and
day, mean sessions of 36 min (DC) and 159 min (AC), and the hourly profile
of the charged energy (:data:`DEMAND_PROFILE`, peak 17:00-18:00).  Real
queues differ from site to site; these are *assumptions*, not
measurements.  (The synthetic stations keep the calibrated queues of the
approximate case study.)

**Not for navigation.**  Research data: speeds are legal limits or imputed
defaults, OSM charging coverage is incomplete, and prices change often.
"""

from __future__ import annotations

import csv
import functools
import gzip
import heapq
import io
import json
import math
import random
from dataclasses import dataclass
from typing import Any, Iterable

from ..data import read_bytes, read_text
from ..network import RoadNetwork
from ..scenario import Conditions, Preferences, Query, Scenario, Station
from ..units import haversine_m, project_equirectangular
from ..vehicle import get_vehicle
from .osm import OSM_ATTRIBUTION
from .synthetic import GridIndex, copy_stations, parse_clock, split_overrides
from .turkey import DEFAULT_PREFERENCES, REF_LAT, REF_LON, _norm, turkey_network, turkey_stations
from .turkey import find_place as find_turkey_place

__all__ = [
    "ATTRIBUTION",
    "DATA_VERSION",
    "DEFAULT_PRICE_TRY",
    "DEMAND_PROFILE",
    "OSMPlace",
    "QUEUE_MODEL",
    "STATION_SOURCES",
    "find_osm_place",
    "osm_turkey_meta",
    "osm_turkey_network",
    "osm_turkey_places",
    "osm_turkey_prices",
    "osm_turkey_scenario",
    "osm_turkey_stations",
    "osm_turkey_summary",
    "osm_turkey_validation",
    "osm_place_node",
    "station_price",
]

#: Folder of the bundled data inside :mod:`evroute.data`.
DATA_DIR = "osm_turkey"
#: The credit every map, figure or table made from this data must carry (ODbL 1.0).
ATTRIBUTION = OSM_ATTRIBUTION
#: Longer credit for figure captions and data sections.
ATTRIBUTION_LONG = (
    "Road network, places and charging stations © OpenStreetMap contributors, ODbL 1.0 "
    "(openstreetmap.org/copyright); elevations SRTM (NASA/USGS)"
)
#: The station sets of :func:`osm_turkey_stations`.
STATION_SOURCES = ("osm", "synthetic", "hybrid")
#: Prices of stations without an operator-confirmed list price [TRY/kWh]: the
#: medians of the operator-confirmed list prices (``prices.json``,
#: ``suggested_defaults``).
DEFAULT_PRICE_TRY: dict[str, float] = {"AC": 9.99, "DC": 14.24}
#: Queue model of the OSM stations and the gap-fillers (assumptions derived
#: from the EPDK statistics of September 2026): sessions per plug and day
#: and mean session length [min].
QUEUE_MODEL: dict[str, dict[str, float]] = {
    "DC": {"sessions_per_plug_day": 3.7, "mean_service_min": 36.0},
    "AC": {"sessions_per_plug_day": 1.0, "mean_service_min": 159.0},
}
#: Set-up time of a stop (parking, plugging in, payment) [s], as in the
#: synthetic case studies.
SETUP_S: dict[str, float] = {"AC": 120.0, "DC": 180.0}
#: Gap-fillers of the hybrid station set: a main-road node farther than this
#: from every DC station (road distance) is not covered [km].
GAP_KM = 75.0
#: A gap-filler moves to the nearest named place within this radius [m].
GAP_PLACE_RADIUS_M = 20_000.0
#: Road classes whose nodes the hybrid gap-fillers must cover.
MAIN_CLASSES = ("motorway", "trunk", "primary")
#: A synthetic station moves to the nearest node of its road class, unless that
#: node is more than this much farther away than the nearest main-road node [m].
SYNTHETIC_SNAP_SLACK_M = 5_000.0


# ===================================================================== raw files
def _csv_rows(name: str) -> list[dict[str, str]]:
    raw = gzip.decompress(read_bytes(f"{DATA_DIR}/{name}")).decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


@functools.lru_cache(maxsize=1)
def _meta() -> dict[str, Any]:
    return json.loads(read_text(f"{DATA_DIR}/meta.json"))


@functools.lru_cache(maxsize=1)
def _prices() -> dict[str, Any]:
    return json.loads(read_text(f"{DATA_DIR}/prices.json"))


def osm_turkey_meta() -> dict[str, Any]:
    """Provenance, parameters and statistics of the build (``meta.json``; a fresh copy)."""
    return json.loads(json.dumps(_meta()))


def osm_turkey_prices() -> dict[str, Any]:
    """The operator price snapshot (``prices.json``; a fresh copy)."""
    return json.loads(json.dumps(_prices()))


def osm_turkey_validation() -> list[dict[str, Any]]:
    """Road distances on the network against the official KGM table (``validation.csv``)."""
    out = []
    for row in csv.DictReader(io.StringIO(read_text(f"{DATA_DIR}/validation.csv"))):
        out.append({k: (float(v) if v and k not in ("from", "to") else (v or None)) for k, v in row.items()})
    return out


def _osm_timestamp() -> str:
    return str(_meta()["sources"]["osm"]["osm_timestamp"])


#: Version of the bundled data: the OSM replication timestamp of the extract.
DATA_VERSION = "osm-2026-10-27"


def _demand_profile() -> tuple[float, ...]:
    hourly = _meta()["official_charging_statistics"]["hourly_energy_mwh_00_23"]
    total = float(sum(hourly))
    return tuple(round(float(h) / total, 5) for h in hourly)


#: Hourly demand profile of every OSM station and gap-filler: the share of
#: the charged energy in each clock hour, EPDK September 2026.
DEMAND_PROFILE: tuple[float, ...] = _demand_profile()


# ======================================================================= places
@dataclass(frozen=True)
class OSMPlace:
    """A city or town of the OSM extract, snapped to the network."""

    name: str
    name_en: str
    #: ``"city"`` or ``"town"`` (the OSM ``place`` tag).
    kind: str
    province_centre: bool
    province: str
    population: int
    lat: float
    lon: float
    #: Network node the place is snapped to, and the snapping distance [m].
    node: int
    snap_m: float
    osm_id: int

    @property
    def tier(self) -> int:
        """Label priority as in the approximate case study: 1 = metropoles of a
        million or more, 2 = other province centres, 3 = towns of 50 000 or
        more, 4 = the rest."""
        if self.population >= 1_000_000:
            return 1
        if self.province_centre:
            return 2
        return 3 if self.population >= 50_000 else 4


@functools.lru_cache(maxsize=1)
def _places() -> tuple[OSMPlace, ...]:
    out = []
    for r in _csv_rows("places.csv.gz"):
        out.append(
            OSMPlace(
                name=r["name"],
                name_en=r["name_en"] or r["name"],
                kind=r["place"],
                province_centre=r["province_centre"] == "1",
                province=r["province"],
                population=int(r["population"] or 0),
                lat=float(r["lat"]),
                lon=float(r["lon"]),
                node=int(r["node"]),
                snap_m=float(r["snap_m"]),
                osm_id=int(r["osm_id"] or 0),
            )
        )
    # priority: province centres, then cities, then towns, by population
    out.sort(key=lambda p: (not p.province_centre, p.kind != "city", -p.population, p.name))
    return tuple(out)


def osm_turkey_places() -> list[OSMPlace]:
    """All 1 005 places in lookup priority: the 81 province centres first,
    then cities and towns, larger first."""
    return list(_places())


@functools.lru_cache(maxsize=1)
def _lookup() -> dict[str, OSMPlace]:
    table: dict[str, OSMPlace] = {}
    for p in _places():  # in priority order: the first place with a key keeps it
        table.setdefault(_norm(p.name), p)
    for p in _places():
        table.setdefault(_norm(p.name_en), p)
    for p in _places():
        if p.province_centre:  # the province name finds its centre
            table.setdefault(_norm(p.province), p)
    return table


def find_osm_place(name: str) -> OSMPlace:
    """Look a place up by name, ignoring case, Turkish letters and punctuation.

    Province centres come first, then cities and towns by population, so
    ``"Ereğli"`` is the larger Ereğli (Konya) and ``"istanbul"``,
    ``"İSTANBUL"`` and ``"Istanbul"`` are the same place.  Names unknown to
    OSM but known to the approximate case study (aliases such as
    ``"Izmit"``, ``"Antakya"``) are resolved through
    :func:`evroute.generators.turkey.find_place`.
    """
    table = _lookup()
    hit = table.get(_norm(name))
    if hit is not None:
        return hit
    try:
        alias = find_turkey_place(name)
    except KeyError:
        alias = None
    if alias is not None:
        for key in (alias.key, alias.name, alias.name.split("(")[0], *alias.aliases):
            hit = table.get(_norm(key))
            if hit is not None:
                return hit
    import difflib

    options = sorted({p.name for p in _places() if p.province_centre or p.population >= 50_000})
    close = difflib.get_close_matches(name, options, n=4)
    hint = f"; did you mean {close}?" if close else ""
    raise KeyError(f"unknown place {name!r}{hint}")


def osm_place_node(name: str | int) -> int:
    """Node id of a place (name as in :func:`find_osm_place`); integers are returned as is."""
    if isinstance(name, int):
        n = osm_turkey_network().num_nodes
        if not 0 <= name < n:
            raise ValueError(f"node {name} outside the network")
        return name
    return find_osm_place(name).node


# ====================================================================== network
@functools.lru_cache(maxsize=1)
def _network() -> RoadNetwork:
    nodes = gzip.decompress(read_bytes(f"{DATA_DIR}/nodes.csv.gz")).decode("utf-8").splitlines()
    if nodes[0].split(",")[:4] != ["id", "lat", "lon", "elevation_m"]:
        raise ValueError("unexpected header of nodes.csv.gz")
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    lats: list[float] = []
    lons: list[float] = []
    for i, line in enumerate(nodes[1:]):
        nid, la, lo, z = line.split(",", 4)[:4]
        if int(nid) != i:
            raise ValueError("node ids must be 0..n-1 in order")
        lat, lon = float(la), float(lo)
        x, y = project_equirectangular(lat, lon, REF_LAT, REF_LON)
        xs.append(x)
        ys.append(y)
        zs.append(float(z))
        lats.append(lat)
        lons.append(lon)
    tail: list[int] = []
    head: list[int] = []
    length: list[float] = []
    speed: list[float] = []
    cls: list[str] = []
    edge_name: dict[int, str] = {}
    reader = csv.reader(io.StringIO(gzip.decompress(read_bytes(f"{DATA_DIR}/edges.csv.gz")).decode("utf-8")))
    header = next(reader)
    if header[:5] != ["u", "v", "length_m", "speed_kmh", "road_class"]:
        raise ValueError("unexpected header of edges.csv.gz")
    for e, row in enumerate(reader):
        tail.append(int(row[0]))
        head.append(int(row[1]))
        length.append(float(row[2]))
        speed.append(float(row[3]))
        cls.append(row[4])
        if row[5]:
            edge_name[e] = row[5]
    # twins (drawing only): pair (u, v) with an unpaired reverse edge, same class first
    m = len(tail)
    twin = [-1] * m
    open_pairs: dict[tuple[int, int], list[int]] = {}
    for e in range(m):
        rev = open_pairs.get((head[e], tail[e]))
        if rev:
            j = next((k for k, c in enumerate(rev) if cls[c] == cls[e]), 0)
            f = rev.pop(j)
            twin[e], twin[f] = f, e
        else:
            open_pairs.setdefault((tail[e], head[e]), []).append(e)
    node_name: dict[int, str] = {}
    for p in _places():  # priority order: one label per node
        node_name.setdefault(p.node, p.name)
    return RoadNetwork(
        x=xs, y=ys, elevation=zs, tail=tail, head=head, length=length, speed_kmh=speed, road_class=cls,
        twin=twin, lat=lats, lon=lons, node_name=node_name, edge_name=edge_name,
    )


def osm_turkey_network() -> RoadNetwork:
    """The OpenStreetMap road network of Turkey (strongly connected, with
    lat/lon, SRTM elevations, road numbers and place names).

    Loaded once per process (about half a second) and shared by every
    scenario; treat it as immutable.  ``node_name`` labels the node of every
    place (one name per node, in the priority of :func:`osm_turkey_places`).
    """
    return _network()


# ====================================================================== stations
def station_price(operator: str, kind: str) -> tuple[float, str]:
    """``(price per kWh [TRY], source)`` of a station of ``operator`` and ``kind``.

    ``source`` is ``"operator"`` for a list price read on the operator's
    own page (the upper end of a published band) and ``"default"`` for
    :data:`DEFAULT_PRICE_TRY` (unknown operator, unverified or no price for
    that kind).
    """
    entry = _prices()["operators"].get(operator)
    if entry and entry.get("verified") == "operator":
        v = entry.get("ac" if kind == "AC" else "dc")
        if isinstance(v, list) and v:
            v = max(v)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return float(v), "operator"
    return DEFAULT_PRICE_TRY["AC" if kind == "AC" else "DC"], "default"


def _queue_station(
    sid: int, node: int, *, kind: str, power_kw: float, plugs: int, price: float, name: str, operator: str
) -> Station:
    q = QUEUE_MODEL[kind]
    return Station(
        id=sid,
        node=node,
        power_kw=float(power_kw),
        kind=kind,  # type: ignore[arg-type]
        plugs=int(plugs),
        price_per_kwh=round(float(price), 2),
        name=name,
        operator=operator,
        daily_sessions=round(q["sessions_per_plug_day"] * int(plugs), 2),
        demand_profile=list(DEMAND_PROFILE),
        mean_service_min=q["mean_service_min"],
        setup_s=SETUP_S[kind],
    )


@functools.lru_cache(maxsize=1)
def _place_index() -> tuple[GridIndex, tuple[OSMPlace, ...]]:
    places = _places()
    net = _network()
    return GridIndex([net.x[p.node] for p in places], [net.y[p.node] for p in places]), places


def _nearest_place(net: RoadNetwork, node: int, max_m: float = 25_000.0) -> str | None:
    index, places = _place_index()
    hits = index.k_nearest(net.x[node], net.y[node], 1)
    if hits and hits[0][0] <= max_m * max_m:
        return places[hits[0][1]].name
    return None


@functools.lru_cache(maxsize=1)
def _osm_stations() -> tuple[tuple[Station, ...], tuple[dict[str, Any], ...]]:
    net = _network()
    stations: list[Station] = []
    info: list[dict[str, Any]] = []
    for r in _csv_rows("stations.csv.gz"):
        kind = r["kind"]
        op = r["operator"] or "unknown"
        price, price_src = station_price(op, kind)
        node = int(r["node"])
        name = r["name"].strip()
        if not name:
            label = f"{op} {kind}" if op != "unknown" else f"{kind} charger"
            where = _nearest_place(net, node)
            name = f"{label}, {where}" if where else label
        sid = len(stations)
        stations.append(
            _queue_station(sid, node, kind=kind, power_kw=float(r["power_kw"]), plugs=int(r["plugs"]), price=price,
                           name=name, operator=op)
        )
        info.append({"osm": f"{r['osm_type']}/{r['osm_id']}", "price": price_src, "power": r["power_source"],
                     "plugs": r["plugs_source"], "snap_m": float(r["snap_m"])})
    return tuple(stations), tuple(info)


def _node_classes(net: RoadNetwork) -> list[set[str]]:
    out: list[set[str]] = [set() for _ in range(net.num_nodes)]
    for e in range(net.num_edges):
        c = net.road_class[e]
        out[net.tail[e]].add(c)
        out[net.head[e]].add(c)
    return out


@functools.lru_cache(maxsize=4)
def _synthetic_stations(seed: int) -> tuple[tuple[Station, ...], tuple[dict[str, Any], ...]]:
    """The synthetic stations of the approximate case study, moved onto this network."""
    approx = turkey_network(seed)
    net = _network()
    classes = _node_classes(net)
    approx_classes = _node_classes(approx)
    # candidate nodes per road class of the approximate road (motorway > trunk > primary)
    rank = {"motorway": 0, "trunk": 1, "primary": 2}
    pools: dict[str, GridIndex] = {}
    pool_nodes: dict[str, list[int]] = {}
    for c in rank:
        ids = [v for v in range(net.num_nodes) if c in classes[v]]
        pool_nodes[c] = ids
        pools[c] = GridIndex([net.x[v] for v in ids], [net.y[v] for v in ids])
    main_nodes = [v for v in range(net.num_nodes) if classes[v] & set(MAIN_CLASSES)]
    main_pool = GridIndex([net.x[v] for v in main_nodes], [net.y[v] for v in main_nodes])
    everything = GridIndex(net.x, net.y)
    out: list[Station] = []
    info: list[dict[str, Any]] = []
    for st in turkey_stations(seed):
        lat, lon = approx.lat[st.node], approx.lon[st.node]  # type: ignore[index]
        x, y = project_equirectangular(lat, lon, REF_LAT, REF_LON)
        label = approx.node_name.get(st.node)
        node, how = -1, ""
        if label is not None:  # a city or town of the case study: the OSM place of that name
            try:
                place = find_osm_place(label)
            except KeyError:
                place = None
            if place is not None and haversine_m(lat, lon, place.lat, place.lon) < 30_000.0:
                node, how = place.node, "place"
        if node < 0:
            best = min((rank[c] for c in approx_classes[st.node] if c in rank), default=None)
            if best is not None:
                c = next(k for k, r in rank.items() if r == best)
                node, how = pool_nodes[c][pools[c].nearest(x, y)], f"nearest {c}"
                # the modelled road may lie far from the OSM road of its class: then the
                # nearest main road (7 stations of seed 0: 1.4-6.5 km instead of 24-58 km away)
                main = main_nodes[main_pool.nearest(x, y)]
                if math.hypot(net.x[node] - x, net.y[node] - y) > (
                    math.hypot(net.x[main] - x, net.y[main] - y) + SYNTHETIC_SNAP_SLACK_M
                ):
                    node, how = main, "nearest main road"
            else:
                node, how = everything.nearest(x, y), "nearest node"
        d = st.to_dict()
        d.update(id=len(out), node=node)
        out.append(Station.from_dict(d))
        info.append({"source": "synthetic", "price": "synthetic", "snap": how,
                     "snap_m": round(haversine_m(lat, lon, net.lat[node], net.lon[node]), 1)})  # type: ignore[index]
    return tuple(out), tuple(info)


def _reverse_multi_dijkstra(net: RoadNetwork, sources: Iterable[int], dist: list[float], limit: float) -> None:
    """Lower ``dist[v]`` to the road distance from ``v`` to the nearest source (in place),
    exploring only distances below ``limit``."""
    heap = [(0.0, s) for s in sources]
    for _, s in heap:
        dist[s] = 0.0
    heapq.heapify(heap)
    in_edges, tail, length = net.in_edges, net.tail, net.length
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for e in in_edges[u]:
            w = tail[e]
            nd = d + length[e]
            if nd < dist[w] and nd < limit:
                dist[w] = nd
                heapq.heappush(heap, (nd, w))


@functools.lru_cache(maxsize=8)
def _gap_fillers(seed: int, gap_km: float) -> tuple[tuple[Station, ...], tuple[dict[str, Any], ...]]:
    """Synthetic DC stations that close the gaps of the OSM DC network (see module docs)."""
    net = _network()
    osm, _ = _osm_stations()
    classes = _node_classes(net)
    main = [v for v in range(net.num_nodes) if classes[v] & set(MAIN_CLASSES)]
    limit = 4.0 * gap_km * 1000.0
    gap_m = gap_km * 1000.0
    dist = [math.inf] * net.num_nodes
    _reverse_multi_dijkstra(net, sorted({s.node for s in osm if s.kind == "DC"}), dist, limit)
    index, places = _place_index()
    main_set = set(main)
    rng = random.Random(f"evroute-osm-turkey-{seed}-gap-fillers")
    out: list[Station] = []
    info: list[dict[str, Any]] = []
    while True:
        far = max(main, key=lambda v: (dist[v], -v))  # farthest uncovered main-road node (ties: lowest id)
        if dist[far] <= gap_m:
            break
        node = far
        for _, i in index.k_nearest(net.x[far], net.y[far], 8):
            p = places[i]
            if p.node in main_set and math.hypot(net.x[p.node] - net.x[far], net.y[p.node] - net.y[far]) <= GAP_PLACE_RADIUS_M:
                trial = [math.inf] * net.num_nodes
                _reverse_multi_dijkstra(net, [p.node], trial, gap_m + 1.0)
                if trial[far] <= gap_m:  # the place still covers the gap's farthest node
                    node = p.node
                    break
        _reverse_multi_dijkstra(net, [node], dist, limit)
        where = _nearest_place(net, node) or f"node {node}"
        sid = len(out)
        power = rng.choice((120.0, 150.0))
        plugs = rng.choice((2, 4))
        price, _ = station_price("synthetic", "DC")
        out.append(
            _queue_station(sid, node, kind="DC", power_kw=power, plugs=plugs, price=price,
                           name=f"Synthetic DC gap-filler {sid + 1}, {where}", operator="synthetic")
        )
        info.append({"source": "gap-filler", "price": "default", "gap_km": round(gap_km, 1)})
    return tuple(out), tuple(info)


def _station_set(source: str, seed: int, gap_km: float) -> tuple[list[Station], list[dict[str, Any]]]:
    if source == "osm":
        st, info = _osm_stations()
        return copy_stations(st), [dict(i) for i in info]
    if source == "synthetic":
        st, info = _synthetic_stations(seed)
        return copy_stations(st), [dict(i) for i in info]
    if source == "hybrid":
        if isinstance(gap_km, bool) or not isinstance(gap_km, (int, float)) or not 0.0 < gap_km < math.inf:
            raise ValueError(f"gap_km must be a finite number > 0, got {gap_km!r}")  # else no gap ever closes
        st, info = _osm_stations()
        fill, finfo = _gap_fillers(seed, float(gap_km))
        out = copy_stations(st)
        for s in copy_stations(fill):
            s.id = len(out)
            out.append(s)
        return out, [dict(i) for i in (*info, *finfo)]
    raise ValueError(f"stations must be one of {STATION_SOURCES}, got {source!r}")


def osm_turkey_stations(source: str = "osm", seed: int = 0, *, gap_km: float = GAP_KM) -> list[Station]:
    """Fresh copies of a station set (``"osm"``, ``"synthetic"`` or ``"hybrid"``; module docs).

    ``seed`` selects the synthetic case-study stations (``"synthetic"``) and
    the powers and plugs of the gap-fillers (``"hybrid"``); ``gap_km`` is the
    coverage radius of the gap-fillers.
    """
    return _station_set(source, seed, gap_km)[0]


# ===================================================================== summary
def osm_turkey_summary() -> dict[str, Any]:
    """Size statistics of the real-data network and its station sets."""
    net = _network()
    km: dict[str, float] = {}
    for e in net.edges():
        km[net.road_class[e]] = km.get(net.road_class[e], 0.0) + net.length[e] / 1000.0
    osm, info = _osm_stations()
    fill, _ = _gap_fillers(0, GAP_KM)
    synth, _ = _synthetic_stations(0)
    return {
        "data_version": DATA_VERSION,
        "osm_timestamp": _osm_timestamp(),
        "nodes": net.num_nodes,
        "edges": net.num_edges,
        "directed_km": {k: round(v, 1) for k, v in sorted(km.items())},
        "road_km": _meta()["graph"]["road_km_total"],
        "places": len(_places()),
        "province_centres": sum(p.province_centre for p in _places()),
        "stations_osm": len(osm),
        "stations_osm_dc": sum(s.kind == "DC" for s in osm),
        "stations_osm_ac": sum(s.kind == "AC" for s in osm),
        "plugs_osm": sum(s.plugs for s in osm),
        "price_from_operator": sum(i["price"] == "operator" for i in info),
        "gap_fillers": len(fill),
        "stations_synthetic": len(synth),
        "min_elevation_m": min(net.elevation),
        "max_elevation_m": max(net.elevation),
        "attribution": ATTRIBUTION,
    }


# ===================================================================== scenario
def osm_turkey_scenario(
    origin: str | int = "Karaman",
    destination: str | int = "İstanbul",
    vehicle: str = "togg_t10x_lr",
    initial_soc: float = 0.8,
    temperature_c: float = 18.0,
    departure: str | float = "08:00",
    *,
    stations: str = "osm",
    seed: int = 0,
    gap_km: float = GAP_KM,
    name: str | None = None,
    **overrides: Any,
) -> Scenario:
    """An intercity trip on the real-data network of Turkey.

    ``origin``/``destination`` are place names (any case, with or without
    Turkish letters; :func:`find_osm_place`) or node ids; ``stations`` is
    ``"osm"`` (default), ``"synthetic"`` or ``"hybrid"`` (module docs).
    Further keyword arguments are :class:`Preferences` or :class:`Conditions`
    fields (``objective="cost"``, ``max_charge_soc=0.85`` ...); the
    preferences default to those of the approximate case study (250 TRY/h,
    25 000 TRY per SoH percentage point).  The default tariff is neutral.
    """
    if stations not in STATION_SOURCES:
        raise ValueError(f"stations must be one of {STATION_SOURCES}, got {stations!r}")
    net = _network()
    o, d = osm_place_node(origin), osm_place_node(destination)
    if o == d:
        raise ValueError("origin and destination are the same place")
    st, info = _station_set(stations, seed, gap_km)
    cond, pref = split_overrides(overrides)
    o_name = net.node_name.get(o, find_osm_place(origin).name if isinstance(origin, str) else str(o))
    d_name = net.node_name.get(d, find_osm_place(destination).name if isinstance(destination, str) else str(d))
    seen: set[int] = set()
    places = []
    for p in _places():
        if p.node not in seen:  # one label per node, as in node_name
            seen.add(p.node)
            places.append({"key": p.name, "name": p.name, "node": p.node, "kind": "city" if p.province_centre else "town",
                           "tier": p.tier})
    queue = {k: dict(v) for k, v in QUEUE_MODEL.items()}
    return Scenario(
        name=name or f"turkey-osm-{_norm(o_name)}-{_norm(d_name)}",
        description=(f"{o_name} → {d_name}: Turkey on OpenStreetMap roads with {stations} stations "
                     f"({ATTRIBUTION}; research data, not for navigation)"),
        network=net,
        stations=st,
        vehicle=get_vehicle(vehicle),
        query=Query(origin=o, destination=d, initial_soc=initial_soc, departure_s=parse_clock(departure)),
        conditions=Conditions(**{"temperature_c": temperature_c, **cond}),
        preferences=Preferences(**{**DEFAULT_PREFERENCES, **pref}),
        currency="TRY",
        meta={
            "generator": "osm_turkey",
            "data_version": DATA_VERSION,
            "osm_timestamp": _osm_timestamp(),
            "stations": stations,
            "seed": seed,
            **({"gap_km": gap_km} if stations == "hybrid" else {}),
            "origin_name": o_name,
            "destination_name": d_name,
            "attribution": ATTRIBUTION,
            "licence": "ODbL 1.0 (data), MIT (code)",
            "places": places,
            "prices": {"snapshot": _prices()["snapshot_accessed"], "defaults_try": dict(DEFAULT_PRICE_TRY),
                       "rule": "operator list price (upper end of a band) if confirmed on the operator's page, "
                               "else the default"},
            "queue_model": {"source": "EPDK monthly statistics, September 2026 (assumption)", **queue,
                            "profile": "hourly charged energy"},
            "station_info": {str(i): v for i, v in enumerate(info)},
            "disclaimer": "OpenStreetMap roads and stations, operator list prices and assumed queues; "
                          "research data, not for navigation.",
        },
    )
