"""Adapters for real road and charging data (optional, lazily imported).

EVRoute's core has no dependencies; this module bridges to the usual open
data tool chain without importing it at package import time:

:func:`from_networkx`
    Convert any networkx-style graph -- typically an OSMnx ``MultiDiGraph``
    (Boeing 2017) -- into a :class:`~evroute.network.RoadNetwork`.  Only the
    duck-typed API ``G.nodes(data=True)``, ``G.edges(keys=True, data=True)``
    (or ``G.edges(data=True)``), ``G.is_directed()`` and ``G.is_multigraph()``
    is used, so networkx itself is not required.
:func:`load_osm`
    Download a drivable OpenStreetMap network for a place name or a bounding
    box with OSMnx (if installed), impute speeds, optionally add raster
    elevations, and convert it.
:func:`stations_from_csv`
    Read charging stations (``lat, lon, power_kw, kind, plugs, price, name``)
    from a CSV file and snap each to the nearest network node.  (The station
    data adapters of :mod:`evroute.station_sources` also read connectors,
    operator prices, OCPI and Open Charge Map data.)
:func:`scenario_from_osm`
    Glue: network + stations + origin/destination coordinates -> Scenario.

Attribute mapping (OSMnx conventions)
-------------------------------------
* node ``x``/``y`` are longitude/latitude for an unprojected graph; if
  ``lat``/``lon`` attributes exist (projected OSMnx graphs keep them), the
  node is projected locally and ``x``/``y`` are ignored; projected graphs
  without ``lat``/``lon`` are used in their own metric coordinates.  The
  coordinate system is read from ``G.graph["crs"]`` when present (OSMnx sets
  it) and otherwise guessed from the numeric range of ``x``/``y``;
* node ``elevation`` [m] (``ox.elevation.add_node_elevations_*``), default 0;
* edge ``length`` [m] (else the straight 3-D distance);
* edge speed: numeric ``speed_kph`` (``ox.add_edge_speeds``) if present,
  else a parsed ``maxspeed`` (``"50"``, ``"30 mph"``, ``"TR:urban"``,
  ``"none"``, lists -> mean), else the road-class default of
  :data:`~evroute.network.ROAD_CLASSES`;
* edge ``highway`` -> road class via :data:`HIGHWAY_CLASS` (lists -> the
  most important class);
* edge ``ref`` or ``name`` -> :attr:`RoadNetwork.edge_name`.

Reference: G. Boeing (2017). OSMnx: New methods for acquiring, constructing,
analyzing, and visualizing complex street networks. Computers, Environment
and Urban Systems 65, 126-139.
"""

from __future__ import annotations

import csv
import math
import warnings
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..network import ROAD_CLASSES, NetworkBuilder, RoadNetwork
from ..scenario import Conditions, Preferences, Query, Scenario, Station
from ..units import haversine_m, project_equirectangular
from ..vehicle import get_vehicle
from .synthetic import GridIndex, _restrict_to_lscc, parse_clock, split_overrides

__all__ = [
    "HIGHWAY_CLASS",
    "OSM_ATTRIBUTION",
    "from_networkx",
    "load_osm",
    "nearest_node",
    "parse_maxspeed",
    "scenario_from_osm",
    "stations_from_csv",
]

#: The credit every map made from OpenStreetMap data must carry (ODbL 1.0).
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

#: OSM ``highway`` tag -> EVRoute road class.
HIGHWAY_CLASS: dict[str, str] = {
    "motorway": "motorway",
    "motorway_link": "link",
    "trunk": "trunk",
    "trunk_link": "link",
    "primary": "primary",
    "primary_link": "link",
    "secondary": "secondary",
    "secondary_link": "link",
    "tertiary": "secondary",
    "tertiary_link": "link",
    "unclassified": "urban",
    "road": "urban",
    "busway": "urban",
    "residential": "residential",
    "living_street": "residential",
    "service": "residential",
    "track": "residential",
}

#: Importance order used when an edge carries several ``highway`` tags.
_CLASS_RANK = {c: i for i, c in enumerate(("motorway", "trunk", "primary", "secondary", "urban", "link", "residential"))}

#: Implicit speed limits (``maxspeed="XX:zone"``) [km/h].
_ZONE_SPEED = {
    "urban": 50.0,
    "rural": 90.0,
    "trunk": 110.0,
    "motorway": 120.0,
    "living_street": 20.0,
    "walk": 7.0,
    "zone30": 30.0,
    "zone:30": 30.0,
}


def _first_class(highway: Any, class_map: dict[str, str], default: str) -> str:
    tags = highway if isinstance(highway, (list, tuple)) else [highway]
    classes = [class_map[t] for t in tags if isinstance(t, str) and t in class_map]
    if not classes:
        return default
    return min(classes, key=lambda c: _CLASS_RANK.get(c, 99))


def parse_maxspeed(value: Any) -> float | None:
    """Parse an OSM ``maxspeed`` value into km/h (``None`` if unknown).

    >>> parse_maxspeed("50"), parse_maxspeed("30 mph"), parse_maxspeed(["50", "70"])
    (50.0, 48.28032, 60.0)
    >>> parse_maxspeed("TR:urban")
    50.0
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 and math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        vals = [v for v in (parse_maxspeed(x) for x in value) if v is not None]
        return sum(vals) / len(vals) if vals else None
    s = str(value).strip().lower()
    if not s:
        return None
    if ";" in s or "|" in s:
        return parse_maxspeed(s.replace("|", ";").split(";"))
    if s in ("none", "unlimited"):
        return 130.0
    if s == "signals" or s == "variable":
        return None
    if ":" in s:
        zone = s.split(":", 1)[1]
        return _ZONE_SPEED.get(zone)
    if s in _ZONE_SPEED:
        return _ZONE_SPEED[s]
    factor = 1.0
    if s.endswith("mph"):
        factor, s = 1.609344, s[:-3]
    elif s.endswith("knots"):
        factor, s = 1.852, s[:-5]
    elif s.endswith("km/h") or s.endswith("kmh"):
        s = s.replace("km/h", "").replace("kmh", "")
    try:
        v = float(s.strip()) * factor
    except ValueError:
        return None
    return v if v > 0 else None


def _speed(data: dict[str, Any], speed_attr: str, road_class: str) -> float:
    v = data.get(speed_attr)
    if isinstance(v, (int, float)) and v > 0 and math.isfinite(v):
        return float(v)
    if isinstance(v, (list, tuple)):  # some exports keep lists
        p = parse_maxspeed(v)
        if p:
            return p
    p = parse_maxspeed(data.get("maxspeed"))
    if p:
        return p
    return ROAD_CLASSES[road_class].speed_kmh


def _label(data: dict[str, Any]) -> str | None:
    for key in ("ref", "name"):
        v = data.get(key)
        if isinstance(v, (list, tuple)):
            v = "/".join(str(x) for x in v if x)
        if v:
            return str(v)
    return None


def _edges(G: Any) -> Iterable[tuple[Any, Any, Any, dict[str, Any]]]:
    multi = bool(getattr(G, "is_multigraph", lambda: False)())
    if multi:
        for u, v, k, d in G.edges(keys=True, data=True):
            yield u, v, k, d
    else:
        for u, v, d in G.edges(data=True):
            yield u, v, 0, d


def _crs_kind(G: Any) -> str | None:
    """``"geographic"`` or ``"projected"`` from OSMnx's ``G.graph["crs"]``; ``None`` if unknown."""
    attrs = getattr(G, "graph", None)
    crs = attrs.get("crs") if isinstance(attrs, dict) else None
    text = str(crs).strip().lower() if crs is not None else ""
    if not text:
        return None
    if "4326" in text or text in ("wgs84", "wgs 84") or "longlat" in text or "latlong" in text:
        return "geographic"
    return "projected"


def from_networkx(
    G: Any,
    *,
    largest_scc: bool = True,
    speed_attr: str = "speed_kph",
    elevation_attr: str = "elevation",
    default_elevation: float = 0.0,
    class_map: dict[str, str] | None = None,
    default_class: str = "urban",
    edge_names: bool = True,
    return_mapping: bool = False,
) -> RoadNetwork | tuple[RoadNetwork, dict[Any, int]]:
    """Convert a networkx-style (OSMnx) graph into a :class:`RoadNetwork`.

    Directed graphs keep their edge directions (one-way streets survive);
    undirected graphs become two-way roads.  Self-loops are dropped, parallel
    edges are kept, and an edge ``(u, v)`` is paired as twin with an unpaired
    reverse edge ``(v, u)`` of equal length (+-1 %).  With ``largest_scc`` the
    result is restricted to its largest strongly connected component, which
    every router needs.  ``return_mapping`` additionally returns a dict from
    the graph's node keys to network node ids (dropped nodes are absent).
    """
    cmap = HIGHWAY_CLASS if class_map is None else class_map
    if default_class not in ROAD_CLASSES:
        raise ValueError(f"unknown default_class {default_class!r}")
    directed = bool(getattr(G, "is_directed", lambda: True)())
    nodes = list(G.nodes(data=True))
    if not nodes:
        raise ValueError("graph has no nodes")

    # ------------------------------------------------- coordinates
    crs = _crs_kind(G)

    def latlon(d: dict[str, Any]) -> tuple[float, float] | None:
        if "lat" in d and "lon" in d:
            return float(d["lat"]), float(d["lon"])
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            raise ValueError("every node needs x/y (or lat/lon) attributes")
        in_range = -180.0 <= float(x) <= 180.0 and -90.0 <= float(y) <= 90.0
        if crs == "geographic" and not in_range:
            raise ValueError("the graph declares a geographic CRS but node x/y are outside lon/lat range")
        if crs == "projected" or not in_range:
            return None  # metric coordinates are used as they are
        return float(y), float(x)

    geo = [latlon(d) for _, d in nodes]
    known = [g for g in geo if g is not None]
    use_geo = len(known) == len(geo)
    lat0 = sum(g[0] for g in known) / len(known) if use_geo else 0.0
    lon0 = sum(g[1] for g in known) / len(known) if use_geo else 0.0
    b = NetworkBuilder()
    index: dict[Any, int] = {}
    for (key, d), g in zip(nodes, geo):
        z = d.get(elevation_attr, default_elevation)
        try:
            z = float(z)
            if not math.isfinite(z):
                z = default_elevation
        except (TypeError, ValueError):
            z = default_elevation
        label = d.get("name") if isinstance(d.get("name"), str) else None
        if use_geo:
            lat, lon = g  # type: ignore[misc]
            x, y = project_equirectangular(lat, lon, lat0, lon0)
            index[key] = b.add_node(x, y, z, name=label, lat=lat, lon=lon)
        else:
            index[key] = b.add_node(float(d["x"]), float(d["y"]), z, name=label)

    # -------------------------------------------------------- edges
    pending: dict[tuple[int, int], list[int]] = {}
    for u_key, v_key, _k, d in _edges(G):
        if u_key not in index or v_key not in index:
            continue
        u, v = index[u_key], index[v_key]
        if u == v:
            continue
        cls = _first_class(d.get("highway"), cmap, default_class)
        length = d.get("length")
        if not isinstance(length, (int, float)) or not length > 0:
            length = None
        else:
            # never shorter than the straight 3-D line between the end nodes
            flat = (
                haversine_m(b.lat[u], b.lon[u], b.lat[v], b.lon[v])
                if use_geo
                else math.hypot(b.x[v] - b.x[u], b.y[v] - b.y[u])
            )
            dz = b.elevation[v] - b.elevation[u]
            length = max(float(length), math.sqrt(flat * flat + dz * dz))
        speed = _speed(d, speed_attr, cls)
        name = _label(d) if edge_names else None
        if directed:
            e = b.add_edge(u, v, length=length, speed_kmh=speed, road_class=cls, name=name)
            rev = pending.get((v, u))
            twin = -1
            if rev:
                for j, cand in enumerate(rev):
                    if abs(b.length[cand] - b.length[e]) <= 0.01 * max(b.length[e], 1.0):
                        twin = rev.pop(j)
                        break
            if twin >= 0:
                b.twin[e], b.twin[twin] = twin, e
            else:
                pending.setdefault((u, v), []).append(e)
        else:
            b.add_road(u, v, length=length, speed_kmh=speed, road_class=cls, name=name)
    net = b.build()
    if net.num_edges == 0:
        raise ValueError("graph has no usable edges")
    mapping = dict(index)
    if largest_scc:
        net, old_to_new = _restrict_to_lscc(net)
        mapping = {k: old_to_new[i] for k, i in index.items() if old_to_new[i] >= 0}
    return (net, mapping) if return_mapping else net


def _import_osmnx():
    try:
        import osmnx as ox  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(
            "load_osm() needs the optional package 'osmnx' (pip install osmnx). "
            "Without it, build a graph elsewhere and pass it to from_networkx(), "
            "or use the built-in generators (turkey_scenario, country_scenario)."
        ) from exc
    return ox


def load_osm(
    place_or_bbox: str | Sequence[float],
    *,
    network_type: str = "drive",
    simplify: bool = True,
    elevation_raster: str | Path | None = None,
    largest_scc: bool = True,
    custom_filter: str | None = None,
    return_graph: bool = False,
) -> RoadNetwork | tuple[RoadNetwork, Any]:
    """Download an OpenStreetMap road network with OSMnx and convert it.

    ``place_or_bbox`` is a geocodable place name (``"Karaman, Turkey"``) or a
    bounding box ``(west, south, east, north)`` in degrees.  Speeds are
    imputed with OSMnx (``add_edge_speeds``); elevations are added from a
    local GeoTIFF if ``elevation_raster`` is given (e.g. SRTM tiles).  Works
    with OSMnx 1.x and 2.x.  ``return_graph`` also returns the OSMnx graph.
    """
    ox = _import_osmnx()
    kw: dict[str, Any] = {"network_type": network_type, "simplify": simplify}
    if custom_filter:
        kw["custom_filter"] = custom_filter
    if isinstance(place_or_bbox, str):
        G = ox.graph_from_place(place_or_bbox, **kw)
    else:
        west, south, east, north = (float(v) for v in place_or_bbox)
        if not (west < east and south < north):
            raise ValueError("bbox must be (west, south, east, north)")
        try:  # OSMnx >= 2.0: bbox=(left, bottom, right, top)
            G = ox.graph_from_bbox(bbox=(west, south, east, north), **kw)
        except TypeError:  # OSMnx 1.x: north, south, east, west
            G = ox.graph_from_bbox(north, south, east, west, **kw)
    add_speeds = getattr(getattr(ox, "routing", None), "add_edge_speeds", None) or getattr(ox, "add_edge_speeds", None)
    if add_speeds is not None:
        G = add_speeds(G)
    if elevation_raster is not None:
        G = ox.elevation.add_node_elevations_raster(G, str(elevation_raster))
    net = from_networkx(G, largest_scc=largest_scc)
    return (net, G) if return_graph else net  # type: ignore[return-value]


class _Snapper:
    """Nearest-node lookup for lat/lon (or x/y) queries on a network."""

    def __init__(self, net: RoadNetwork) -> None:
        self.net = net
        self.geo = net.lat is not None and net.lon is not None
        if self.geo:
            self.lat0 = sum(net.lat) / net.num_nodes  # type: ignore[arg-type]
            self.lon0 = sum(net.lon) / net.num_nodes  # type: ignore[arg-type]
            pts = [project_equirectangular(a, o, self.lat0, self.lon0) for a, o in zip(net.lat, net.lon)]  # type: ignore[arg-type]
            self.xs = [p[0] for p in pts]
            self.ys = [p[1] for p in pts]
        else:
            self.xs, self.ys = net.x, net.y
        self.index = GridIndex(self.xs, self.ys)

    def snap_latlon(self, lat: float, lon: float) -> tuple[int, float]:
        if not self.geo:
            raise ValueError("network has no lat/lon; give x/y coordinates instead")
        x, y = project_equirectangular(lat, lon, self.lat0, self.lon0)
        v = self.index.nearest(x, y)
        return v, haversine_m(lat, lon, self.net.lat[v], self.net.lon[v])  # type: ignore[index]

    def snap_xy(self, x: float, y: float) -> tuple[int, float]:
        if self.geo:
            raise ValueError("network is geo-referenced; give lat/lon coordinates")
        v = self.index.nearest(x, y)
        return v, math.hypot(self.xs[v] - x, self.ys[v] - y)


def nearest_node(net: RoadNetwork, lat: float, lon: float) -> int:
    """Network node closest to a WGS84 point (network needs lat/lon)."""
    return _Snapper(net).snap_latlon(lat, lon)[0]


def _num(row: dict[str, str], key: str, default: float | None) -> float | None:
    v = (row.get(key) or "").strip()
    if not v:
        return default
    return float(v.replace(",", ".")) if v.count(",") == 1 and "." not in v else float(v)


def stations_from_csv(
    path: str | Path,
    network: RoadNetwork,
    *,
    max_snap_m: float | None = 2000.0,
    first_id: int = 0,
    default_price: float = 0.45,
    default_plugs: int = 2,
    default_daily_sessions: float = 20.0,
    delimiter: str | None = None,
) -> list[Station]:
    """Read charging stations from a CSV file and snap them to the network.

    Required columns: ``lat`` and ``lon`` (or ``x`` and ``y`` for networks
    without geo-reference) and ``power_kw``.  Optional: ``kind`` (``AC``/``DC``;
    default ``AC`` up to 22 kW), ``plugs``, ``price`` (or ``price_per_kwh``),
    ``name``, ``operator``, ``daily_sessions``, ``mean_service_min``,
    ``demand_profile`` (``highway``/``urban``) and ``setup_s``.  Header names
    are case-insensitive; ``;`` and tab delimiters are detected.  Stations
    farther than ``max_snap_m`` from every node are skipped with a warning.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    if delimiter is None:
        head = text.splitlines()[0] if text else ""
        delimiter = max((";", "\t", ","), key=head.count)
    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
    snap = _Snapper(network)
    out: list[Station] = []
    skipped = 0
    for lineno, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        if not any(v.strip() for v in row.values()):
            continue
        try:
            power = _num(row, "power_kw", None)
            if power is None:
                raise ValueError("missing power_kw")
            if row.get("lat", "").strip() and row.get("lon", "").strip():
                node, dist = snap.snap_latlon(float(_num(row, "lat", 0.0)), float(_num(row, "lon", 0.0)))  # type: ignore[arg-type]
            else:
                node, dist = snap.snap_xy(float(_num(row, "x", None)), float(_num(row, "y", None)))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from None
        if max_snap_m is not None and dist > max_snap_m:
            skipped += 1
            continue
        kind = (row.get("kind") or "").strip().upper() or ("AC" if power <= 22.0 else "DC")
        if kind not in ("AC", "DC"):
            raise ValueError(f"{path}:{lineno}: kind must be AC or DC, got {kind!r}")
        price = _num(row, "price", None)
        if price is None:
            price = _num(row, "price_per_kwh", default_price)
        profile = (row.get("demand_profile") or "").strip() or ("urban" if kind == "AC" else "highway")
        sid = first_id + len(out)
        out.append(
            Station(
                id=sid,
                node=node,
                power_kw=float(power),
                kind=kind,  # type: ignore[arg-type]
                plugs=int(_num(row, "plugs", default_plugs) or default_plugs),
                price_per_kwh=float(price),  # type: ignore[arg-type]
                name=(row.get("name") or "").strip() or f"Station {sid}",
                operator=(row.get("operator") or "").strip() or "generic",
                daily_sessions=float(_num(row, "daily_sessions", default_daily_sessions)),  # type: ignore[arg-type]
                demand_profile=profile,
                mean_service_min=float(_num(row, "mean_service_min", 120.0 if kind == "AC" else 35.0)),  # type: ignore[arg-type]
                setup_s=float(_num(row, "setup_s", 180.0)),  # type: ignore[arg-type]
            )
        )
    if skipped:
        warnings.warn(f"{skipped} station(s) farther than {max_snap_m} m from the network were skipped", stacklevel=2)
    return out


def scenario_from_osm(
    network: RoadNetwork,
    stations: list[Station],
    origin: tuple[float, float] | int,
    destination: tuple[float, float] | int,
    *,
    vehicle: str = "compact50",
    initial_soc: float = 0.8,
    temperature_c: float = 20.0,
    departure: str | float = "09:00",
    currency: str = "EUR",
    name: str = "osm",
    attribution: str | None = OSM_ATTRIBUTION,
    **overrides: Any,
) -> Scenario:
    """Assemble a :class:`Scenario` from real data.

    ``origin``/``destination`` are ``(lat, lon)`` pairs (snapped to the
    nearest node) or node ids.  Further keyword arguments are
    :class:`Conditions` or :class:`Preferences` fields.  ``attribution`` is
    the data credit that maps of the scenario show (``meta["attribution"]``;
    the ODbL requires "© OpenStreetMap contributors" on maps made from
    OpenStreetMap data); pass ``None`` for a network that is not from OSM.
    """
    snap = None

    def node(p: tuple[float, float] | int) -> int:
        nonlocal snap
        if isinstance(p, int):
            return p
        if snap is None:
            snap = _Snapper(network)
        return snap.snap_latlon(float(p[0]), float(p[1]))[0]

    cond, pref = split_overrides(overrides)
    return Scenario(
        name=name,
        description="Scenario built from external road/charging data",
        network=network,
        stations=stations,
        vehicle=get_vehicle(vehicle),
        query=Query(origin=node(origin), destination=node(destination), initial_soc=initial_soc,
                    departure_s=parse_clock(departure)),
        conditions=Conditions(**{"temperature_c": temperature_c, **cond}),
        preferences=Preferences(**pref),
        currency=currency,
        meta={"generator": "osm", **({"attribution": attribution} if attribution else {})},
    )
