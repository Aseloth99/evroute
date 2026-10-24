"""Scalable synthetic benchmark families (deterministic by seed).

Three families complement the small grids of :mod:`evroute.generators.basic`
and the real-geography case study of :mod:`evroute.generators.turkey`:

:func:`country_scenario`
    A *synthetic country*.  Cities are placed by a Poisson-disk process
    (Bridson 2007) with a preference for lowlands; the intercity highway
    network is the Gabriel graph (Gabriel & Sokal 1969) of the cities, whose
    relative-neighbourhood subgraph (Toussaint 1980) carries the motorways and
    trunk roads; every highway is subdivided into 5-10 km segments; large
    cities receive a local street grid (``secondary`` avenues, ``urban``
    arterials, partly one-way ``residential`` streets); elevation comes from a
    smooth :class:`Terrain` with mountain ranges; charging stations sit in
    cities, in the street grids and at highway service areas.  The size is
    steered by ``nodes_target`` and scales to ~50 000 nodes in a few seconds.
:func:`random_geometric_scenario`
    The classic random geometric graph benchmark (Penrose 2003): uniform
    points joined to their ``k`` nearest neighbours, Gabriel-filtered so the
    drawing is planar, a fraction of one-way links, restricted to the largest
    strongly connected component.
:func:`corridor_scenario`
    One long motorway with side stations of different power and price, a
    mountain pass and an optional valley bypass: the didactic instance in
    which *how much* to charge at each stop (partial charging) matters.

Every builder returns a ready-to-plan :class:`~evroute.scenario.Scenario`
whose origin and destination are far apart and whose initial state of charge
requires one to three charging stops with the default vehicle.  All
randomness flows from ``random.Random`` instances seeded with strings derived
from the ``seed`` argument, so instances are identical on every machine and
Python version (string seeding hashes with SHA-512, independent of
``PYTHONHASHSEED``).

The module also provides the building blocks shared by all generators:
:class:`GridIndex` (bucket-grid nearest-neighbour index), :class:`Terrain`,
:func:`subnetwork`, :func:`calibrated_station` /
:func:`calibrate_daily_sessions` (queue demand calibrated to a target peak
wait with the Erlang-C model of :mod:`evroute.queueing`),
:func:`parse_clock` and :func:`split_overrides`.

References
----------
* R. Bridson (2007). Fast Poisson disk sampling in arbitrary dimensions.
  ACM SIGGRAPH 2007 Sketches.
* K. R. Gabriel, R. R. Sokal (1969). A new statistical approach to
  geographic variation analysis. Systematic Zoology 18(3), 259-278.
* G. T. Toussaint (1980). The relative neighbourhood graph of a finite
  planar set. Pattern Recognition 12(4), 261-268.
* M. Penrose (2003). Random Geometric Graphs. Oxford University Press.
"""

from __future__ import annotations

import functools
import math
import random
from dataclasses import dataclass, fields
from typing import Any, Iterable, Sequence

from ..network import ROAD_CLASSES, NetworkBuilder, RoadNetwork, largest_strongly_connected_component
from ..queueing import HIGHWAY_PROFILE, URBAN_PROFILE, expected_wait_s
from ..scenario import Conditions, Preferences, Query, Scenario, Station
from ..vehicle import get_vehicle

__all__ = [
    "City",
    "GridIndex",
    "Terrain",
    "calibrate_daily_sessions",
    "calibrated_station",
    "corridor_network",
    "corridor_scenario",
    "country_network",
    "country_scenario",
    "parse_clock",
    "random_geometric_network",
    "random_geometric_scenario",
    "split_overrides",
    "subnetwork",
]

#: Steepest sustained grade a generated road may have; steeper terrain is
#: "solved" by lengthening the road (switchbacks), as real road design does.
MAX_GRADE = 0.07

_COND_FIELDS = frozenset(f.name for f in fields(Conditions))
_PREF_FIELDS = frozenset(f.name for f in fields(Preferences))


# =====================================================================
# shared helpers
# =====================================================================
def parse_clock(value: str | float | int) -> float:
    """Time of day -> seconds after midnight.

    Accepts ``"HH:MM"`` or ``"HH:MM:SS"`` strings, or a number that is
    already in seconds (the unit of :attr:`Query.departure_s`).

    >>> parse_clock("08:30")
    30600.0
    """
    if isinstance(value, (int, float)):
        return float(value)
    parts = str(value).strip().split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"time of day must look like 'HH:MM', got {value!r}")
    h, m = int(parts[0]), int(parts[1])
    s = float(parts[2]) if len(parts) == 3 else 0.0
    if not (0 <= h <= 24 and 0 <= m < 60 and 0 <= s < 60):
        raise ValueError(f"invalid time of day {value!r}")
    return h * 3600.0 + m * 60.0 + s


def split_overrides(overrides: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split keyword overrides into :class:`Conditions` and :class:`Preferences` fields.

    Raises ``TypeError`` for keys that belong to neither, so typos never pass
    silently.
    """
    cond: dict[str, Any] = {}
    pref: dict[str, Any] = {}
    for k, v in overrides.items():
        if k in _PREF_FIELDS:
            pref[k] = tuple(v) if k == "speed_factors" else v
        elif k in _COND_FIELDS:
            cond[k] = dict(v) if k == "class_speed_factor" else v
        else:
            raise TypeError(
                f"unknown override {k!r}; expected a Conditions field ({sorted(_COND_FIELDS)}) "
                f"or a Preferences field ({sorted(_PREF_FIELDS)})"
            )
    return cond, pref


def _profile(profile: str | Sequence[float]) -> list[float]:
    if isinstance(profile, str):
        return list(URBAN_PROFILE if profile == "urban" else HIGHWAY_PROFILE)
    return [float(p) for p in profile]


def calibrate_daily_sessions(
    target_peak_wait_s: float,
    plugs: int,
    mean_service_min: float,
    profile: str | Sequence[float] = "highway",
    cap_s: float = 3 * 3600.0,
) -> float:
    """Daily sessions that make the *peak-hour* expected wait equal the target.

    The station is the M/M/c queue of :mod:`evroute.queueing`; the hourly
    arrival rate is ``daily_sessions * share_h / sum(shares)``.  The expected
    wait is increasing in the arrival rate, so the peak-hour rate that yields
    ``target_peak_wait_s`` is found by bisection and scaled back to a daily
    volume.  This calibrates synthetic demand to an *observable* quantity
    (how long drivers queue at the busiest hour) rather than to unobservable
    session counts.
    """
    if target_peak_wait_s <= 0.0 or plugs <= 0:
        return 0.0
    if target_peak_wait_s >= cap_s:
        raise ValueError("target wait must be below the queue cap")
    shares = _profile(profile)
    total = sum(shares)
    peak_share = max(shares) / total
    mean_h = mean_service_min / 60.0
    lo, hi = 0.0, plugs / mean_h  # hi saturates the queue
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if expected_wait_s(mid, mean_h, plugs, cap_s) < target_peak_wait_s:
            lo = mid
        else:
            hi = mid
    return lo / peak_share


def calibrated_station(
    sid: int,
    node: int,
    *,
    power_kw: float,
    kind: str,
    plugs: int,
    price_per_kwh: float,
    name: str,
    operator: str,
    peak_wait_min: float,
    profile: str = "highway",
    mean_service_min: float | None = None,
    setup_s: float | None = None,
) -> Station:
    """A :class:`Station` whose demand is calibrated to a peak-hour wait.

    ``mean_service_min`` defaults to 120 min for AC and, for DC, to a session
    length that shrinks with power (25-40 min).  ``daily_sessions`` is rounded
    to 0.1 so that serialised instances stay compact and stable.
    """
    if mean_service_min is None:
        mean_service_min = 120.0 if kind == "AC" else max(25.0, min(40.0, 40.0 - (power_kw - 50.0) * 0.05))
    if setup_s is None:
        setup_s = 120.0 if kind == "AC" else 180.0
    sessions = calibrate_daily_sessions(peak_wait_min * 60.0, plugs, mean_service_min, profile)
    return Station(
        id=sid,
        node=node,
        power_kw=float(power_kw),
        kind=kind,  # type: ignore[arg-type]
        plugs=int(plugs),
        price_per_kwh=round(float(price_per_kwh), 2),
        name=name,
        operator=operator,
        daily_sessions=round(sessions, 1),
        demand_profile=profile,
        mean_service_min=round(float(mean_service_min), 1),
        setup_s=float(setup_s),
    )


def copy_stations(stations: Iterable[Station]) -> list[Station]:
    """Fresh :class:`Station` objects (cached templates stay untouched)."""
    out = []
    for s in stations:
        d = s.to_dict()
        if isinstance(d.get("demand_profile"), list):
            d["demand_profile"] = list(d["demand_profile"])
        out.append(Station.from_dict(d))
    return out


class GridIndex:
    """Bucket-grid spatial index for nearest-neighbour queries in the plane.

    Points are hashed into square cells of side ``cell``; a query scans
    Chebyshev rings of cells around the query cell and stops as soon as no
    unseen cell can hold a closer point (every point in ring ``r + 1`` is at
    least ``r * cell`` away).  With roughly one point per cell, ``nearest``
    and ``k_nearest`` run in expected constant time.
    """

    def __init__(self, xs: Sequence[float], ys: Sequence[float], cell: float | None = None) -> None:
        n = len(xs)
        self.xs = xs
        self.ys = ys
        if n == 0:
            raise ValueError("GridIndex needs at least one point")
        if cell is None:
            w = (max(xs) - min(xs)) or 1.0
            h = (max(ys) - min(ys)) or 1.0
            cell = max(math.sqrt(w * h / n), 1e-6)
        self.cell = float(cell)
        inv = 1.0 / self.cell
        buckets: dict[tuple[int, int], list[int]] = {}
        for i in range(n):
            key = (int(math.floor(xs[i] * inv)), int(math.floor(ys[i] * inv)))
            b = buckets.get(key)
            if b is None:
                buckets[key] = [i]
            else:
                b.append(i)
        self.buckets = buckets
        cxs = [k[0] for k in buckets]
        cys = [k[1] for k in buckets]
        self._bounds = (min(cxs), max(cxs), min(cys), max(cys))

    def _cell_of(self, x: float, y: float) -> tuple[int, int]:
        inv = 1.0 / self.cell
        return int(math.floor(x * inv)), int(math.floor(y * inv))

    def _max_ring(self, cx: int, cy: int) -> int:
        x0, x1, y0, y1 = self._bounds
        return max(abs(cx - x0), abs(x1 - cx), abs(cy - y0), abs(y1 - cy))

    @staticmethod
    def _ring(cx: int, cy: int, r: int):
        if r == 0:
            yield cx, cy
            return
        for dx in range(-r, r + 1):
            yield cx + dx, cy - r
            yield cx + dx, cy + r
        for dy in range(-r + 1, r):
            yield cx - r, cy + dy
            yield cx + r, cy + dy

    def k_nearest(self, x: float, y: float, k: int, exclude: int = -1) -> list[tuple[float, int]]:
        """Up to ``k`` nearest points as ``(squared distance, index)``, closest first."""
        xs, ys, buckets = self.xs, self.ys, self.buckets
        cx, cy = self._cell_of(x, y)
        r_max = self._max_ring(cx, cy)
        found: list[tuple[float, int]] = []
        r = 0
        while r <= r_max:
            for key in self._ring(cx, cy, r):
                b = buckets.get(key)
                if b is None:
                    continue
                for i in b:
                    if i == exclude:
                        continue
                    dx = xs[i] - x
                    dy = ys[i] - y
                    found.append((dx * dx + dy * dy, i))
            if len(found) >= k:
                found.sort()
                del found[k:]
                lim = r * self.cell
                if found[-1][0] <= lim * lim:
                    return found
            r += 1
        found.sort()
        return found[:k]

    def nearest(self, x: float, y: float, exclude: int = -1) -> int:
        """Index of the nearest point (``-1`` only if every point is excluded)."""
        res = self.k_nearest(x, y, 1, exclude)
        return res[0][1] if res else -1

    def within(self, x: float, y: float, radius: float) -> list[int]:
        """Indices of all points with distance ``< radius`` (unordered)."""
        xs, ys, buckets = self.xs, self.ys, self.buckets
        inv = 1.0 / self.cell
        r2 = radius * radius
        out = []
        for gx in range(int(math.floor((x - radius) * inv)), int(math.floor((x + radius) * inv)) + 1):
            for gy in range(int(math.floor((y - radius) * inv)), int(math.floor((y + radius) * inv)) + 1):
                b = buckets.get((gx, gy))
                if b is None:
                    continue
                for i in b:
                    dx = xs[i] - x
                    dy = ys[i] - y
                    if dx * dx + dy * dy < r2:
                        out.append(i)
        return out


class Terrain:
    """Smooth synthetic relief ``z(x, y)`` in metres.

    Three layers are summed:

    * a coastal plain on one side of the region rising (smooth-step) to an
      inland plateau of ``plateau_m`` -- the typical Anatolian/Iberian profile;
    * ``n_ranges`` mountain ranges: Gaussian ridges along random line
      segments with heights drawn from ``range_m`` and a flat-topped taper
      towards the ends;
    * ``n_hills`` Gaussian hills and basins of up to ``hill_m``.

    The result is clamped at sea level.  It is cheap (a few ``exp`` calls per
    point) and infinitely differentiable, so road grades derived from it are
    realistic at every sampling distance.
    """

    def __init__(
        self,
        width: float,
        height: float,
        seed: int | str = 0,
        *,
        plateau_m: float = 800.0,
        n_ranges: int | None = None,
        range_m: tuple[float, float] = (900.0, 2000.0),
        n_hills: int = 10,
        hill_m: float = 150.0,
    ) -> None:
        rng = random.Random(f"evroute-terrain-{seed}")
        self.width, self.height = float(width), float(height)
        self.plateau_m = float(plateau_m)
        size = max(width, height)
        self.coast = rng.choice(("south", "west", "north", "east"))
        self.coast_band = 0.35
        if n_ranges is None:
            n_ranges = 2 + int(size / 300_000.0)
        self.ranges = []
        for _ in range(n_ranges):
            ax, ay = rng.uniform(0.1, 0.9) * width, rng.uniform(0.1, 0.9) * height
            ang = rng.uniform(0.0, math.pi)
            length = rng.uniform(0.25, 0.6) * size
            bx, by = ax + math.cos(ang) * length, ay + math.sin(ang) * length
            h = rng.uniform(*range_m)
            w = rng.uniform(0.02, 0.05) * size + 6000.0
            vx, vy = bx - ax, by - ay
            self.ranges.append((ax, ay, vx, vy, 1.0 / (vx * vx + vy * vy), h, 1.0 / (2.0 * w * w)))
        self.hills = []
        for _ in range(n_hills):
            cx, cy = rng.uniform(0, width), rng.uniform(0, height)
            r = rng.uniform(0.04, 0.12) * size
            a = rng.uniform(-0.6, 1.0) * hill_m
            self.hills.append((cx, cy, 1.0 / (2.0 * r * r), a))

    def _coast_factor(self, x: float, y: float) -> float:
        c = self.coast
        if c == "south":
            u = y / self.height
        elif c == "north":
            u = 1.0 - y / self.height
        elif c == "west":
            u = x / self.width
        else:
            u = 1.0 - x / self.width
        u = min(max(u / self.coast_band, 0.0), 1.0)
        return u * u * (3.0 - 2.0 * u)

    def __call__(self, x: float, y: float) -> float:
        exp = math.exp
        z = 20.0 + self.plateau_m * self._coast_factor(x, y)
        for ax, ay, vx, vy, inv_l2, h, inv_w in self.ranges:
            t = ((x - ax) * vx + (y - ay) * vy) * inv_l2
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            dx = x - ax - t * vx
            dy = y - ay - t * vy
            s = 2.0 * t - 1.0
            s2 = s * s
            z += h * (1.0 - s2 * s2) * exp(-(dx * dx + dy * dy) * inv_w)
        for cx, cy, inv_r, a in self.hills:
            dx = x - cx
            dy = y - cy
            z += a * exp(-(dx * dx + dy * dy) * inv_r)
        return z if z > 0.0 else 0.0


def subnetwork(net: RoadNetwork, keep: Iterable[int]) -> tuple[RoadNetwork, list[int]]:
    """Induced subnetwork on ``keep`` (node order preserved).

    Returns the new network and ``old_to_new`` (``-1`` for dropped nodes).
    Edges need both endpoints kept; a twin whose partner was dropped becomes
    ``-1``; node and edge names are carried over.
    """
    old_to_new = [-1] * net.num_nodes
    nodes = sorted(set(keep))
    for i, v in enumerate(nodes):
        old_to_new[v] = i
    e_map = [-1] * net.num_edges
    tail, head = net.tail, net.head
    kept_edges = []
    for e in range(net.num_edges):
        if old_to_new[tail[e]] >= 0 and old_to_new[head[e]] >= 0:
            e_map[e] = len(kept_edges)
            kept_edges.append(e)
    geo = net.lat is not None and net.lon is not None
    return (
        RoadNetwork(
            x=[net.x[v] for v in nodes],
            y=[net.y[v] for v in nodes],
            elevation=[net.elevation[v] for v in nodes],
            tail=[old_to_new[tail[e]] for e in kept_edges],
            head=[old_to_new[head[e]] for e in kept_edges],
            length=[net.length[e] for e in kept_edges],
            speed_kmh=[net.speed_kmh[e] for e in kept_edges],
            road_class=[net.road_class[e] for e in kept_edges],
            twin=[e_map[net.twin[e]] if net.twin[e] >= 0 else -1 for e in kept_edges],
            lat=[net.lat[v] for v in nodes] if geo else None,  # type: ignore[index]
            lon=[net.lon[v] for v in nodes] if geo else None,  # type: ignore[index]
            node_name={old_to_new[v]: s for v, s in net.node_name.items() if old_to_new[v] >= 0},
            edge_name={e_map[e]: s for e, s in net.edge_name.items() if e_map[e] >= 0},
        ),
        old_to_new,
    )


def _restrict_to_lscc(net: RoadNetwork) -> tuple[RoadNetwork, list[int]]:
    comp = largest_strongly_connected_component(net)
    if len(comp) == net.num_nodes:
        return net, list(range(net.num_nodes))
    return subnetwork(net, comp)


def _segment_length(chord: float, dh: float, winding: float) -> float:
    """Road length of a segment: chord times winding, lengthened to cap the grade."""
    flat = chord * winding
    need = abs(dh) / MAX_GRADE
    if flat < need:
        flat = need
    straight = math.sqrt(chord * chord + dh * dh)
    return max(flat, straight * 1.000001, 1.0)


def _graded_speed(road_class: str, length: float, dh: float) -> float:
    """Class speed, reduced on steep sections (mountain roads are slower)."""
    v = ROAD_CLASSES[road_class].speed_kmh
    g = abs(dh) / length if length > 0 else 0.0
    if g > 0.05:
        return min(v, {"motorway": 90.0, "trunk": 80.0}.get(road_class, 60.0))
    if g > 0.035:
        return min(v, {"motorway": 100.0, "trunk": 90.0}.get(road_class, 70.0))
    return v


_OPERATORS = (("Operator A", 1.00), ("Operator B", 0.93), ("Operator C", 1.07), ("Operator D", 0.97), ("Operator E", 1.04))
_SYLL_A = ("ba", "ka", "lo", "mi", "ra", "te", "vo", "sa", "de", "no", "ri", "za", "pe", "lu", "ma", "ko", "ta", "ne", "ve", "ga")
_SYLL_B = ("n", "r", "l", "s", "", "", "", "k", "m", "")


def _city_name(rng: random.Random, used: set[str]) -> str:
    for _ in range(100):
        s = "".join(rng.choice(_SYLL_A) for _ in range(rng.choice((2, 2, 3)))) + rng.choice(_SYLL_B)
        s = s.capitalize()
        if s not in used:
            used.add(s)
            return s
    s = f"City{len(used)}"
    used.add(s)
    return s


def _wiggle(rng: random.Random, amplitude: float):
    """Smooth lateral offset ``w(t)`` with ``w(0) = w(1) = 0``."""
    f1, f2 = rng.uniform(0.6, 1.6), rng.uniform(2.0, 3.5)
    p1, p2 = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)
    a1, a2 = amplitude * rng.uniform(0.5, 1.0), amplitude * rng.uniform(0.1, 0.35)
    sin, pi = math.sin, math.pi

    def w(t: float) -> float:
        return sin(pi * t) * (a1 * sin(2 * pi * f1 * t + p1) + a2 * sin(2 * pi * f2 * t + p2))

    return w


# =====================================================================
# synthetic country
# =====================================================================
@dataclass
class City:
    """A city of a synthetic country."""

    index: int
    name: str
    x: float
    y: float
    elevation: float
    population: float
    tier: int
    #: Node of the city centre in the final network.
    node: int = -1
    #: Number of nodes of the local street grid (0 = single node).
    grid_nodes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "node": self.node,
            "tier": self.tier,
            "population": round(self.population),
            "grid_nodes": self.grid_nodes,
        }


def _poisson_disk(width: float, height: float, r: float, rng: random.Random, k: int = 30) -> list[tuple[float, float]]:
    """Bridson's (2007) maximal Poisson-disk sampling of a rectangle."""
    cell = r / math.sqrt(2.0)
    gw, gh = int(width / cell) + 1, int(height / cell) + 1
    grid = [-1] * (gw * gh)
    pts: list[tuple[float, float]] = []
    r2 = r * r

    def ok(x: float, y: float) -> bool:
        gx, gy = int(x / cell), int(y / cell)
        for ix in range(max(gx - 2, 0), min(gx + 3, gw)):
            for iy in range(max(gy - 2, 0), min(gy + 3, gh)):
                j = grid[iy * gw + ix]
                if j >= 0:
                    dx, dy = pts[j][0] - x, pts[j][1] - y
                    if dx * dx + dy * dy < r2:
                        return False
        return True

    def add(x: float, y: float) -> None:
        grid[int(y / cell) * gw + int(x / cell)] = len(pts)
        pts.append((x, y))

    add(rng.uniform(0, width), rng.uniform(0, height))
    active = [0]
    while active:
        a = rng.randrange(len(active))
        px, py = pts[active[a]]
        for _ in range(k):
            ang = rng.uniform(0.0, 2.0 * math.pi)
            rad = r * math.sqrt(rng.uniform(1.0, 4.0))
            x, y = px + rad * math.cos(ang), py + rad * math.sin(ang)
            if 0.0 <= x < width and 0.0 <= y < height and ok(x, y):
                add(x, y)
                active.append(len(pts) - 1)
                break
        else:
            active[a] = active[-1]
            active.pop()
    return pts


def _proximity_graph(xs: list[float], ys: list[float]) -> tuple[list[tuple[int, int]], set[tuple[int, int]], set[tuple[int, int]]]:
    """Gabriel edges, their relative-neighbourhood subset and the MST.

    Candidates are the 12 nearest neighbours of every point (Gabriel edges of
    well-spread point sets are short); the Euclidean minimum spanning tree is
    always added so that the result is connected (MST is a subgraph of the
    RNG, which is a subgraph of the Gabriel graph).
    """
    n = len(xs)
    index = GridIndex(xs, ys)
    cand: set[tuple[int, int]] = set()
    for i in range(n):
        for _, j in index.k_nearest(xs[i], ys[i], min(12, n - 1), exclude=i):
            cand.add((min(i, j), max(i, j)))
    # Prim's MST, O(n^2) -- n is the number of cities
    in_tree = [False] * n
    best = [math.inf] * n
    parent = [-1] * n
    best[0] = 0.0
    mst: set[tuple[int, int]] = set()
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=best.__getitem__)
        in_tree[u] = True
        if parent[u] >= 0:
            mst.add((min(u, parent[u]), max(u, parent[u])))
        for v in range(n):
            if not in_tree[v]:
                d = (xs[u] - xs[v]) ** 2 + (ys[u] - ys[v]) ** 2
                if d < best[v]:
                    best[v], parent[v] = d, u
    cand |= mst
    gabriel: list[tuple[int, int]] = []
    rng_edges: set[tuple[int, int]] = set()
    for i, j in sorted(cand):
        d2 = (xs[i] - xs[j]) ** 2 + (ys[i] - ys[j]) ** 2
        mx, my = 0.5 * (xs[i] + xs[j]), 0.5 * (ys[i] + ys[j])
        if any(k != i and k != j for k in index.within(mx, my, math.sqrt(d2) * 0.5 * (1 - 1e-9))):
            if (i, j) not in mst:
                continue
        gabriel.append((i, j))
        d = math.sqrt(d2)
        lune = False
        for k in index.within(xs[i], ys[i], d):
            if k == i or k == j:
                continue
            if (xs[j] - xs[k]) ** 2 + (ys[j] - ys[k]) ** 2 < d2:
                lune = True
                break
        if not lune or (i, j) in mst:
            rng_edges.add((i, j))
    return gabriel, rng_edges, mst


@functools.lru_cache(maxsize=8)
def _country_cached(
    n_cities: int,
    region_km: float,
    nodes_target: int,
    seed: int,
    grid_spacing_m: float,
    oneway_fraction: float,
) -> tuple[RoadNetwork, tuple[City, ...], tuple[Station, ...]]:
    return _build_country(n_cities, region_km, nodes_target, seed, grid_spacing_m, oneway_fraction)


def _build_country(
    n_cities: int,
    region_km: float,
    nodes_target: int,
    seed: int,
    grid_spacing_m: float,
    oneway_fraction: float,
) -> tuple[RoadNetwork, tuple[City, ...], tuple[Station, ...]]:
    if n_cities < 2:
        raise ValueError("a country needs at least two cities")
    W = region_km * 1000.0
    terrain = Terrain(W, W, f"country-{seed}", plateau_m=750.0)
    rng = random.Random(f"evroute-country-{seed}-cities")

    # ---------------------------------------------------------- cities
    r = W * math.sqrt(0.70 / (1.3 * n_cities))
    pts: list[tuple[float, float]] = []
    for _ in range(12):
        margin = 0.04 * W
        raw = _poisson_disk(W - 2 * margin, W - 2 * margin, r, rng)
        pts = [(x + margin, y + margin) for x, y in raw]
        if len(pts) >= n_cities:
            break
        r *= 0.85
    if len(pts) < n_cities:
        raise ValueError("could not place that many cities; enlarge region_km")
    scored = sorted(
        ((rng.random() * (1.0 + terrain(x, y) / 700.0), x, y) for x, y in pts),
    )[:n_cities]
    order = list(range(n_cities))
    rng.shuffle(order)
    used: set[str] = set()
    cities: list[City] = []
    n1 = max(1, round(0.08 * n_cities))
    n2 = max(n1 + 1, round(0.25 * n_cities))
    for idx, (_, x, y) in enumerate(scored):
        rank = order[idx] + 1
        pop = 2.5e6 / rank**1.07 * math.exp(rng.gauss(0.0, 0.15))
        tier = 1 if rank <= n1 else (2 if rank <= n2 else 3)
        cities.append(City(idx, _city_name(rng, used), x, y, terrain(x, y), pop, tier))
    cxs, cys = [c.x for c in cities], [c.y for c in cities]

    # -------------------------------------------------------- highways
    gabriel, rng_set, mst = _proximity_graph(cxs, cys)
    lengths = [math.hypot(cxs[i] - cxs[j], cys[i] - cys[j]) for i, j in gabriel]
    rng_lengths = sorted(L for (i, j), L in zip(gabriel, lengths) if (i, j) in rng_set)
    median = rng_lengths[len(rng_lengths) // 2] if rng_lengths else max(lengths)
    highways: list[tuple[int, int, str]] = []
    for (i, j), L in zip(gabriel, lengths):
        if (i, j) in rng_set:
            cls = "motorway" if cities[i].tier <= 2 and cities[j].tier <= 2 else "trunk"
        else:
            if L > 2.2 * median and (i, j) not in mst:
                continue
            cls = "primary"
        highways.append((i, j, cls))
    total_len = sum(math.hypot(cxs[i] - cxs[j], cys[i] - cys[j]) for i, j, _ in highways) * 1.1
    seg = min(max(total_len / max(0.5 * nodes_target, 1.0), 5000.0), 10000.0)
    hw_nodes = sum(max(1, round(math.hypot(cxs[i] - cxs[j], cys[i] - cys[j]) / seg)) - 1 for i, j, _ in highways)

    # ------------------------------------------------- grid allocation
    remaining = nodes_target - hw_nodes - n_cities
    grid_cities = [c for c in cities if c.tier <= 2]
    weights = [c.population**0.9 for c in grid_cities]
    wsum = sum(weights) or 1.0
    grid_side: dict[int, int] = {}
    if remaining > 0:
        for c, w in zip(grid_cities, weights):
            m = int(round(math.sqrt(remaining * w / wsum + 1)))
            if m >= 4:
                grid_side[c.index] = m

    # ---------------------------------------------------------- build
    b = NetworkBuilder()
    add_node, add_road = b.add_node, b.add_road
    grng = random.Random(f"evroute-country-{seed}-grids")
    boundary: dict[int, list[int]] = {}
    grid_members: dict[int, list[int]] = {}
    for c in cities:
        m = grid_side.get(c.index, 0)
        if m == 0:
            c.node = add_node(c.x, c.y, c.elevation, name=c.name)
            continue
        spacing = grid_spacing_m * (1.0 + 0.1 * (c.tier - 1))
        ids = _city_grid(b, terrain, c.x, c.y, m, spacing, grng, oneway_fraction)
        mid = m // 2
        c.node = ids[mid][mid]
        b.node_name[c.node] = c.name
        c.grid_nodes = m * m
        bnd = sorted({*ids[0], *ids[-1], *(row[0] for row in ids), *(row[-1] for row in ids)})
        boundary[c.index] = bnd
        grid_members[c.index] = [v for row in ids for v in row]

    hrng = random.Random(f"evroute-country-{seed}-highways")
    hw_polylines: list[tuple[str, str, list[int], list[float]]] = []
    counters = {"motorway": 0, "trunk": 0, "primary": 0}
    for i, j, cls in highways:
        a = _attach(b, cities[i], cities[j], boundary.get(i))
        z = _attach(b, cities[j], cities[i], boundary.get(j))
        ax, ay, zx, zy = b.x[a], b.y[a], b.x[z], b.y[z]
        dist = math.hypot(zx - ax, zy - ay)
        nseg = max(1, round(dist / seg))
        counters[cls] += 1
        label = {"motorway": "M", "trunk": "T", "primary": "P"}[cls] + f"-{counters[cls]}"
        w = _wiggle(hrng, min(0.035 * dist, 4000.0))
        ux, uy = (zx - ax) / dist, (zy - ay) / dist
        nodes = [a]
        for s in range(1, nseg):
            t = s / nseg
            off = w(t)
            x = ax + t * (zx - ax) - uy * off
            y = ay + t * (zy - ay) + ux * off
            nodes.append(add_node(x, y, terrain(x, y)))
        nodes.append(z)
        cum = [0.0]
        for u, v in zip(nodes, nodes[1:]):
            chord = math.hypot(b.x[v] - b.x[u], b.y[v] - b.y[u])
            dh = b.elevation[v] - b.elevation[u]
            winding = 1.03 + min(0.2, 2.5 * abs(dh) / max(chord, 1.0)) + hrng.uniform(0.0, 0.03)
            L = _segment_length(chord, dh, winding)
            add_road(u, v, length=L, speed_kmh=_graded_speed(cls, L, dh), road_class=cls, name=label)
            cum.append(cum[-1] + L)
        hw_polylines.append((cls, label, nodes, cum))

    net, old_to_new = _restrict_to_lscc(b.build())
    for c in cities:
        c.node = old_to_new[c.node]
        if c.node < 0:  # pragma: no cover - highways always attach to the LSCC
            raise RuntimeError("city centre fell outside the strongly connected core")
    stations = _country_stations(net, cities, hw_polylines, grid_members, old_to_new, seed)
    return net, tuple(cities), tuple(stations)


def _city_grid(
    b: NetworkBuilder,
    terrain: Terrain,
    cx: float,
    cy: float,
    m: int,
    spacing: float,
    rng: random.Random,
    oneway_fraction: float,
) -> list[list[int]]:
    """``m x m`` jittered street grid centred on ``(cx, cy)``.

    The middle row and column are ``secondary`` avenues, every fourth line an
    ``urban`` arterial, the rest ``residential``; a fraction of residential
    lines are one-way with alternating directions (Manhattan style) and ~2 %
    of residential links are missing.  Returns node ids as ``ids[row][col]``.
    """
    half = (m - 1) / 2.0
    add_node, add_edge, add_road = b.add_node, b.add_edge, b.add_road
    xs, ys, zs = b.x, b.y, b.elevation
    ids: list[list[int]] = []
    for r in range(m):
        row = []
        for c in range(m):
            x = cx + (c - half) * spacing + (rng.random() - 0.5) * 0.3 * spacing
            y = cy + (r - half) * spacing + (rng.random() - 0.5) * 0.3 * spacing
            row.append(add_node(x, y, terrain(x, y)))
        ids.append(row)
    mid = m // 2

    def line_class(k: int) -> str:
        if k == mid:
            return "secondary"
        if (k - mid) % 4 == 0:
            return "urban"
        return "residential"

    oneway_rows = {k: rng.random() < oneway_fraction for k in range(m)}
    oneway_cols = {k: rng.random() < oneway_fraction for k in range(m)}

    def link(u: int, v: int, cls: str, oneway: bool, forward: bool) -> None:
        dx, dy, dz = xs[v] - xs[u], ys[v] - ys[u], zs[v] - zs[u]
        L = math.sqrt(dx * dx + dy * dy + dz * dz) * 1.02
        if oneway:
            if forward:
                add_edge(u, v, length=L, road_class=cls)
            else:
                add_edge(v, u, length=L, road_class=cls)
        else:
            add_road(u, v, length=L, road_class=cls)

    for r in range(m):
        cls = line_class(r)
        res = cls == "residential"
        for c in range(m - 1):
            if res and rng.random() < 0.02:
                continue
            link(ids[r][c], ids[r][c + 1], cls, res and oneway_rows[r], r % 2 == 0)
    for c in range(m):
        cls = line_class(c)
        res = cls == "residential"
        for r in range(m - 1):
            if res and rng.random() < 0.02:
                continue
            link(ids[r][c], ids[r + 1][c], cls, res and oneway_cols[c], c % 2 == 1)
    return ids


def _attach(b: NetworkBuilder, city: City, other: City, boundary: list[int] | None) -> int:
    """Node where a highway from ``city`` towards ``other`` leaves the city."""
    if not boundary:
        return city.node
    cx, cy = b.x[city.node], b.y[city.node]
    dx, dy = other.x - city.x, other.y - city.y
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    best, best_s = boundary[0], -math.inf
    for v in boundary:
        vx, vy = b.x[v] - cx, b.y[v] - cy
        proj = vx * ux + vy * uy
        perp = abs(-vx * uy + vy * ux)
        s = proj - 0.6 * perp
        if s > best_s:
            best, best_s = v, s
    return best


def _price(rng: random.Random, base: float) -> tuple[str, float]:
    op, f = rng.choice(_OPERATORS)
    return op, base * f


def _country_stations(
    net: RoadNetwork,
    cities: list[City],
    hw_polylines: list[tuple[str, str, list[int], list[float]]],
    grid_members: dict[int, list[int]],
    old_to_new: list[int],
    seed: int,
) -> list[Station]:
    rng = random.Random(f"evroute-country-{seed}-stations")
    out: list[Station] = []

    def add(node: int, **kw: Any) -> None:
        out.append(calibrated_station(len(out), node, **kw))

    for c in cities:
        v = c.node
        if c.tier == 1:
            op, p = _price(rng, 0.59)
            add(v, power_kw=150, kind="DC", plugs=6, price_per_kwh=p, name=f"{c.name} DC hub", operator=op,
                peak_wait_min=rng.uniform(10, 20), profile="urban")
            op, p = _price(rng, 0.79)
            add(v, power_kw=350, kind="DC", plugs=4, price_per_kwh=p, name=f"{c.name} HPC", operator=op,
                peak_wait_min=rng.uniform(8, 18), profile="highway")
            op, p = _price(rng, 0.39)
            add(v, power_kw=22, kind="AC", plugs=6, price_per_kwh=p, name=f"{c.name} AC", operator=op,
                peak_wait_min=rng.uniform(2, 8), profile="urban")
        elif c.tier == 2:
            op, p = _price(rng, 0.59)
            add(v, power_kw=150, kind="DC", plugs=4, price_per_kwh=p, name=f"{c.name} DC", operator=op,
                peak_wait_min=rng.uniform(6, 14), profile="urban")
            op, p = _price(rng, 0.39)
            add(v, power_kw=22, kind="AC", plugs=4, price_per_kwh=p, name=f"{c.name} AC", operator=op,
                peak_wait_min=rng.uniform(1, 6), profile="urban")
        else:
            if rng.random() < 0.85:
                pw = rng.choice((50, 75, 100))
                op, p = _price(rng, 0.49 if pw < 100 else 0.55)
                add(v, power_kw=pw, kind="DC", plugs=rng.choice((2, 2, 4)), price_per_kwh=p, name=f"{c.name} DC",
                    operator=op, peak_wait_min=rng.uniform(2, 10), profile="highway")
            if rng.random() < 0.6:
                op, p = _price(rng, 0.39)
                add(v, power_kw=22, kind="AC", plugs=2, price_per_kwh=p, name=f"{c.name} AC", operator=op,
                    peak_wait_min=rng.uniform(0.5, 5), profile="urban")
        members = [old_to_new[u] for u in grid_members.get(c.index, []) if old_to_new[u] >= 0 and old_to_new[u] != v]
        for _ in range(len(members) // 350):
            u = rng.choice(members)
            if rng.random() < 0.7:
                op, p = _price(rng, 0.39)
                add(u, power_kw=22, kind="AC", plugs=rng.choice((2, 4)), price_per_kwh=p,
                    name=f"{c.name} AC (street)", operator=op, peak_wait_min=rng.uniform(0.5, 6), profile="urban")
            else:
                op, p = _price(rng, 0.49)
                add(u, power_kw=50, kind="DC", plugs=2, price_per_kwh=p, name=f"{c.name} DC (street)",
                    operator=op, peak_wait_min=rng.uniform(2, 10), profile="urban")

    for cls, label, nodes, cum in hw_polylines:
        total = cum[-1]
        spacing = rng.uniform(60e3, 100e3) if cls != "primary" else rng.uniform(100e3, 140e3)
        k = max(0, math.ceil(total / spacing) - 1)
        if cls == "primary" and rng.random() > 0.6:
            k = 0
        for q in range(1, k + 1):
            target = total * q / (k + 1)
            pos = min(range(1, len(nodes) - 1), key=lambda i: abs(cum[i] - target), default=None)
            if pos is None:
                continue
            v = old_to_new[nodes[pos]]
            if v < 0:
                continue
            km = round(cum[pos] / 1000.0)
            if cls == "motorway":
                hpc = rng.random() < 0.4
                pw = 350 if hpc else 150
                op, p = _price(rng, 0.79 if hpc else 0.59)
                add(v, power_kw=pw, kind="DC", plugs=rng.choice((4, 6, 8)), price_per_kwh=p,
                    name=f"Service area {label} km {km}", operator=op,
                    peak_wait_min=rng.uniform(12, 32), profile="highway")
            elif cls == "trunk":
                pw = rng.choice((100, 150, 150))
                op, p = _price(rng, 0.59 if pw >= 150 else 0.55)
                add(v, power_kw=pw, kind="DC", plugs=rng.choice((2, 4, 6)), price_per_kwh=p,
                    name=f"Service area {label} km {km}", operator=op,
                    peak_wait_min=rng.uniform(5, 18), profile="highway")
            else:
                op, p = _price(rng, 0.49)
                add(v, power_kw=50, kind="DC", plugs=2, price_per_kwh=p,
                    name=f"Roadside DC {label} km {km}", operator=op,
                    peak_wait_min=rng.uniform(1, 8), profile="highway")
    return out


def country_network(
    n_cities: int = 40,
    region_km: float = 600.0,
    nodes_target: int = 5000,
    seed: int = 0,
    *,
    grid_spacing_m: float = 300.0,
    oneway_fraction: float = 0.35,
) -> tuple[RoadNetwork, list[City]]:
    """The road network and cities of a synthetic country (cached).

    The returned network is shared between calls with the same arguments;
    treat it as immutable (every algorithm does).
    """
    net, cities, _ = _country_cached(n_cities, float(region_km), int(nodes_target), int(seed),
                                     float(grid_spacing_m), float(oneway_fraction))
    return net, [City(**{f.name: getattr(c, f.name) for f in fields(City)}) for c in cities]


def _pick_far_pair(cities: Sequence[City], max_m: float) -> tuple[City, City]:
    big = [c for c in cities if c.tier <= 2]
    if len(big) < 2:
        big = list(cities)
    best: tuple[float, int, int] | None = None
    fallback: tuple[float, int, int] | None = None
    for a in range(len(big)):
        for z in range(a + 1, len(big)):
            d = math.hypot(big[a].x - big[z].x, big[a].y - big[z].y)
            if fallback is None or d > fallback[0]:
                fallback = (d, a, z)
            if d <= max_m and (best is None or d > best[0]):
                best = (d, a, z)
    _, a, z = best or fallback  # type: ignore[misc]
    first, second = big[a], big[z]
    # deterministic orientation: start in the west
    return (first, second) if first.x <= second.x else (second, first)


def country_scenario(
    n_cities: int = 40,
    region_km: float = 600.0,
    nodes_target: int = 5000,
    seed: int = 0,
    *,
    vehicle: str = "sedan75",
    initial_soc: float = 0.8,
    temperature_c: float = 15.0,
    departure: str | float = "09:00",
    origin: int | str | None = None,
    destination: int | str | None = None,
    trip_km_max: float = 750.0,
    grid_spacing_m: float = 300.0,
    oneway_fraction: float = 0.35,
    name: str | None = None,
    **overrides: Any,
) -> Scenario:
    """A ready-to-plan synthetic-country instance.

    ``origin``/``destination`` may be node ids or city names; by default the
    two large cities farthest apart within ``trip_km_max`` (straight line)
    are used, which with the default vehicle needs one to three stops.
    Extra keyword arguments are :class:`Conditions` or :class:`Preferences`
    fields.
    """
    net, cities, stations = _country_cached(n_cities, float(region_km), int(nodes_target), int(seed),
                                            float(grid_spacing_m), float(oneway_fraction))
    a, z = _pick_far_pair(cities, trip_km_max * 1000.0)
    by_name = {c.name.casefold(): c.node for c in cities}

    def resolve(v: int | str | None, default: int) -> int:
        if v is None:
            return default
        if isinstance(v, int):
            if not 0 <= v < net.num_nodes:
                raise ValueError(f"node {v} outside the network")
            return v
        try:
            return by_name[v.casefold()]
        except KeyError:
            raise KeyError(f"no city named {v!r}; cities: {sorted(c.name for c in cities)}") from None

    o, d = resolve(origin, a.node), resolve(destination, z.node)
    cond, pref = split_overrides(overrides)
    return Scenario(
        name=name or f"country-{n_cities}c-{nodes_target}n-s{seed}",
        description=(
            f"Synthetic country ({n_cities} cities, {region_km:.0f} km, ~{net.num_nodes} nodes): "
            f"{net.node_name.get(o, o)} -> {net.node_name.get(d, d)}"
        ),
        network=net,
        stations=copy_stations(stations),
        vehicle=get_vehicle(vehicle),
        query=Query(origin=o, destination=d, initial_soc=initial_soc, departure_s=parse_clock(departure)),
        conditions=Conditions(**{"temperature_c": temperature_c, **cond}),
        preferences=Preferences(**pref),
        currency="EUR",
        meta={
            "generator": "country",
            "seed": seed,
            "params": {"n_cities": n_cities, "region_km": region_km, "nodes_target": nodes_target,
                       "grid_spacing_m": grid_spacing_m, "oneway_fraction": oneway_fraction},
            "cities": [c.to_dict() for c in cities],
        },
    )


# =====================================================================
# random geometric graphs
# =====================================================================
def random_geometric_network(
    n_nodes: int = 3000,
    seed: int = 0,
    *,
    region_km: float = 250.0,
    k: int = 5,
    oneway_fraction: float = 0.08,
    relief_m: float = 600.0,
) -> RoadNetwork:
    """Gabriel-filtered k-nearest-neighbour graph restricted to its largest SCC.

    Points are uniform in a ``region_km`` square.  Each point is joined to its
    ``k`` nearest neighbours; a link survives only if the circle with the
    link as diameter contains no other point (Gabriel condition), which makes
    the drawing planar and road-like.  A fraction ``oneway_fraction`` of the
    links is one-way in a random direction; the result is restricted to the
    largest strongly connected component.  The longest 20 % of the links
    (rural connections) are ``primary``, the rest ``secondary``.
    """
    if n_nodes < 4:
        raise ValueError("need at least 4 nodes")
    rng = random.Random(f"evroute-rgg-{seed}")
    W = region_km * 1000.0
    terrain = Terrain(W, W, f"rgg-{seed}", plateau_m=relief_m, n_hills=12, hill_m=relief_m * 0.3,
                      range_m=(relief_m * 0.8, relief_m * 1.8))
    xs = [rng.uniform(0.0, W) for _ in range(n_nodes)]
    ys = [rng.uniform(0.0, W) for _ in range(n_nodes)]
    index = GridIndex(xs, ys)
    pairs: set[tuple[int, int]] = set()
    for i in range(n_nodes):
        for _, j in index.k_nearest(xs[i], ys[i], k, exclude=i):
            pairs.add((i, j) if i < j else (j, i))
    links = []
    for i, j in sorted(pairs):
        mx, my = 0.5 * (xs[i] + xs[j]), 0.5 * (ys[i] + ys[j])
        d = math.hypot(xs[i] - xs[j], ys[i] - ys[j])
        inside = index.within(mx, my, 0.5 * d * (1.0 - 1e-9))
        if any(q != i and q != j for q in inside):
            continue
        links.append((i, j, d))
    cut = sorted(d for _, _, d in links)[int(0.8 * len(links))] if links else 0.0
    b = NetworkBuilder()
    for i in range(n_nodes):
        b.add_node(xs[i], ys[i], terrain(xs[i], ys[i]))
    zs = b.elevation
    for i, j, d in links:
        cls = "primary" if d >= cut else "secondary"
        dh = zs[j] - zs[i]
        L = _segment_length(d, dh, 1.05 + rng.uniform(0.0, 0.1))
        v = _graded_speed(cls, L, dh)
        if rng.random() < oneway_fraction:
            if rng.random() < 0.5:
                b.add_edge(i, j, length=L, speed_kmh=v, road_class=cls)
            else:
                b.add_edge(j, i, length=L, speed_kmh=v, road_class=cls)
        else:
            b.add_road(i, j, length=L, speed_kmh=v, road_class=cls)
    net, _ = _restrict_to_lscc(b.build())
    return net


@functools.lru_cache(maxsize=8)
def _rgg_cached(n_nodes: int, seed: int, region_km: float, k: int, oneway_fraction: float) -> RoadNetwork:
    return random_geometric_network(n_nodes, seed, region_km=region_km, k=k, oneway_fraction=oneway_fraction)


def _scatter_stations(net: RoadNetwork, count: int, seed: str, exclude: set[int]) -> list[Station]:
    rng = random.Random(seed)
    cand = [v for v in range(net.num_nodes) if v not in exclude]
    rng.shuffle(cand)
    out = []
    for v in cand[:count]:
        u = rng.random()
        if u < 0.25:
            op, p = _price(rng, 0.39)
            out.append(calibrated_station(len(out), v, power_kw=22, kind="AC", plugs=rng.choice((2, 4)),
                                          price_per_kwh=p, name=f"AC {len(out)}", operator=op,
                                          peak_wait_min=rng.uniform(0.5, 6), profile="urban"))
        else:
            pw = 50 if u < 0.55 else (150 if u < 0.92 else 350)
            op, p = _price(rng, {50: 0.49, 150: 0.59, 350: 0.79}[pw])
            out.append(calibrated_station(len(out), v, power_kw=pw, kind="DC", plugs=rng.choice((2, 4, 6)),
                                          price_per_kwh=p, name=f"DC{pw} {len(out)}", operator=op,
                                          peak_wait_min=rng.uniform(2, 20), profile="highway"))
    return out


def random_geometric_scenario(
    n_nodes: int = 3000,
    seed: int = 0,
    *,
    region_km: float = 250.0,
    k: int = 5,
    oneway_fraction: float = 0.08,
    n_stations: int | None = None,
    vehicle: str = "compact50",
    initial_soc: float = 0.5,
    temperature_c: float = 15.0,
    departure: str | float = "09:00",
    name: str | None = None,
    **overrides: Any,
) -> Scenario:
    """Random geometric graph instance from the south-west to the north-east corner.

    ``n_stations`` defaults to one station per 60 nodes (at least 8).
    Extra keyword arguments are :class:`Conditions` or :class:`Preferences`
    fields.
    """
    net = _rgg_cached(int(n_nodes), int(seed), float(region_km), int(k), float(oneway_fraction))
    W = region_km * 1000.0
    index = GridIndex(net.x, net.y)
    o = index.nearest(0.04 * W, 0.04 * W)
    d = index.nearest(0.96 * W, 0.96 * W)
    count = n_stations if n_stations is not None else max(8, net.num_nodes // 60)
    stations = _scatter_stations(net, count, f"evroute-rgg-{seed}-stations", {o, d})
    cond, pref = split_overrides(overrides)
    return Scenario(
        name=name or f"rgg-{n_nodes}-s{seed}",
        description=f"Random geometric graph (Gabriel-filtered {k}-NN, {net.num_nodes} nodes in the LSCC)",
        network=net,
        stations=stations,
        vehicle=get_vehicle(vehicle),
        query=Query(origin=o, destination=d, initial_soc=initial_soc, departure_s=parse_clock(departure)),
        conditions=Conditions(**{"temperature_c": temperature_c, **cond}),
        preferences=Preferences(**pref),
        currency="EUR",
        meta={"generator": "random_geometric", "seed": seed,
              "params": {"n_nodes": n_nodes, "region_km": region_km, "k": k, "oneway_fraction": oneway_fraction}},
    )


# =====================================================================
# corridor
# =====================================================================
#: Side stations of the corridor: (position as fraction of the length,
#: label, power kW, kind, plugs, price EUR/kWh, peak wait min).  The layout is
#: deliberate: a fast but pricey charger before a slow cheap one, a busy site
#: in the afternoon, a charger at the foot of the climb and one just after
#: the pass (where regeneration has refilled the battery).
CORRIDOR_STATIONS: tuple[tuple[float, str, float, str, int, float, float], ...] = (
    (0.12, "HPC", 350.0, "DC", 4, 0.79, 8.0),
    (0.22, "Budget", 50.0, "DC", 2, 0.39, 4.0),
    (0.33, "Hub (busy)", 150.0, "DC", 6, 0.59, 28.0),
    (0.37, "Foot of the pass", 120.0, "DC", 4, 0.55, 6.0),
    (0.58, "After the pass", 50.0, "DC", 2, 0.45, 3.0),
    (0.70, "HPC", 350.0, "DC", 6, 0.79, 12.0),
    (0.82, "Standard", 150.0, "DC", 4, 0.59, 8.0),
    (0.92, "Village AC", 22.0, "AC", 2, 0.35, 2.0),
)


def corridor_network(
    length_km: float = 600.0,
    *,
    spacing_km: float = 5.0,
    mountain: bool = True,
    pass_elevation_m: float = 1500.0,
    bypass: bool = True,
    seed: int = 0,
) -> tuple[RoadNetwork, list[int], dict[str, int]]:
    """Motorway corridor with side-station nodes.

    Returns the network, the node of every entry of
    :data:`CORRIDOR_STATIONS` (plus the bypass village) and a dict of named
    nodes (``"start"``, ``"end"``, ``"pass"``).
    """
    rng = random.Random(f"evroute-corridor-{seed}")
    L = length_km * 1000.0
    n = max(2, round(length_km / spacing_km))
    w = _wiggle(rng, 0.01 * L)
    p1, p2 = rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi)

    def elev(s: float) -> float:
        """Elevation profile along the corridor (``s`` in [0, 1]): a gentle
        inland rise, small undulations and the mountain pass."""
        z = 120.0 + 260.0 * math.sin(math.pi * s) * (1.0 - 0.6 * s)
        z += 25.0 * math.sin(2 * math.pi * 7 * s + p1) + 12.0 * math.sin(2 * math.pi * 17 * s + p2)
        if mountain and 0.38 < s < 0.64:
            u = (s - 0.38) / 0.26  # 0..1 over the mountain section, summit at u = 0.45
            shape = (u / 0.45) if u <= 0.45 else (1.0 - u) / 0.55
            shape = 0.5 - 0.5 * math.cos(math.pi * shape)
            z = z + (pass_elevation_m - z) * shape
        return max(z, 5.0)

    b = NetworkBuilder()
    main = []
    summit_i = None
    for i in range(n + 1):
        s = i / n
        x, y = s * L, w(s)
        main.append(b.add_node(x, y, elev(s)))
        if mountain and summit_i is None and s >= 0.38 + 0.45 * 0.26 - 1e-9:
            summit_i = i
    b.node_name[main[0]] = "Start"
    b.node_name[main[-1]] = "End"
    named = {"start": main[0], "end": main[-1]}
    if mountain and summit_i is not None:
        b.node_name[main[summit_i]] = f"Pass ({pass_elevation_m:.0f} m)"
        named["pass"] = main[summit_i]
    for u, v in zip(main, main[1:]):
        chord = math.hypot(b.x[v] - b.x[u], b.y[v] - b.y[u])
        dh = b.elevation[v] - b.elevation[u]
        seg = _segment_length(chord, dh, 1.04)
        b.add_road(u, v, length=seg, speed_kmh=_graded_speed("motorway", seg, dh), road_class="motorway", name="A1")

    station_nodes = []
    for k, (frac, label, *_rest) in enumerate(CORRIDOR_STATIONS):
        i = min(range(1, n), key=lambda j: abs(j / n - frac))
        side = 1.0 if k % 2 == 0 else -1.0
        off = side * (600.0 + 300.0 * (k % 3))
        x, y = b.x[main[i]] + 150.0, b.y[main[i]] + off
        v = b.add_node(x, y, b.elevation[main[i]] + rng.uniform(-5.0, 5.0), name=f"S{k + 1} {label}")
        b.add_road(main[i], v, length=abs(off) * 1.3 + 150.0, road_class="link", name="exit")
        station_nodes.append(v)

    if bypass and mountain:
        i0 = min(range(n), key=lambda j: abs(j / n - 0.36))
        i1 = min(range(n), key=lambda j: abs(j / n - 0.66))
        a, z = main[i0], main[i1]
        span = b.x[z] - b.x[a]
        m = max(3, round(span * 1.15 / (spacing_km * 1000.0)))
        prev = a
        village = None
        for q in range(1, m):
            t = q / m
            x = b.x[a] + t * span
            y = b.y[a] - 0.06 * L * math.sin(math.pi * t)
            z_ = 250.0 + 350.0 * math.sin(math.pi * t) + 20.0 * math.sin(2 * math.pi * 5 * t + p1)
            v = b.add_node(x, y, z_)
            if q == m // 2:
                village = v
                b.node_name[v] = "Valley village"
            chord = math.hypot(b.x[v] - b.x[prev], b.y[v] - b.y[prev])
            dh = b.elevation[v] - b.elevation[prev]
            seg = _segment_length(chord, dh, 1.12)
            b.add_road(prev, v, length=seg, speed_kmh=_graded_speed("primary", seg, dh), road_class="primary",
                       name="Old valley road")
            prev = v
        chord = math.hypot(b.x[z] - b.x[prev], b.y[z] - b.y[prev])
        dh = b.elevation[z] - b.elevation[prev]
        seg = _segment_length(chord, dh, 1.12)
        b.add_road(prev, z, length=seg, speed_kmh=_graded_speed("primary", seg, dh), road_class="primary",
                   name="Old valley road")
        if village is not None:
            station_nodes.append(village)
    return b.build(), station_nodes, named


def corridor_scenario(
    length_km: float = 600.0,
    *,
    spacing_km: float = 5.0,
    mountain: bool = True,
    pass_elevation_m: float = 1500.0,
    bypass: bool = True,
    vehicle: str = "compact50",
    initial_soc: float = 0.9,
    temperature_c: float = 15.0,
    departure: str | float = "12:00",
    seed: int = 0,
    name: str | None = None,
    **overrides: Any,
) -> Scenario:
    """The didactic corridor: where to stop *and how much to charge*.

    Stations differ in power (22-350 kW), price (0.35-0.79 EUR/kWh) and
    afternoon queues; a mountain pass in the middle makes the energy needed
    before the climb larger than after it, and the descent regenerates.  The
    optional valley bypass is longer and slower but stays below 650 m.
    """
    net, snodes, named = corridor_network(length_km, spacing_km=spacing_km, mountain=mountain,
                                          pass_elevation_m=pass_elevation_m, bypass=bypass, seed=seed)
    stations = []
    for k, (_, label, pw, kind, plugs, price, wait) in enumerate(CORRIDOR_STATIONS):
        stations.append(calibrated_station(k, snodes[k], power_kw=pw, kind=kind, plugs=plugs, price_per_kwh=price,
                                           name=f"S{k + 1} {label}", operator=_OPERATORS[k % len(_OPERATORS)][0],
                                           peak_wait_min=wait, profile="highway"))
    if len(snodes) > len(CORRIDOR_STATIONS):
        stations.append(calibrated_station(len(stations), snodes[-1], power_kw=50, kind="DC", plugs=2,
                                           price_per_kwh=0.45, name="Valley village DC", operator="Operator D",
                                           peak_wait_min=2.0, profile="highway"))
    cond, pref = split_overrides(overrides)
    return Scenario(
        name=name or f"corridor-{length_km:.0f}km",
        description=(f"{length_km:.0f} km motorway corridor with a {pass_elevation_m:.0f} m pass"
                     if mountain else f"{length_km:.0f} km flat motorway corridor"),
        network=net,
        stations=stations,
        vehicle=get_vehicle(vehicle),
        query=Query(origin=named["start"], destination=named["end"], initial_soc=initial_soc,
                    departure_s=parse_clock(departure)),
        conditions=Conditions(**{"temperature_c": temperature_c, **cond}),
        preferences=Preferences(**pref),
        currency="EUR",
        meta={"generator": "corridor", "seed": seed,
              "params": {"length_km": length_km, "spacing_km": spacing_km, "mountain": mountain,
                         "pass_elevation_m": pass_elevation_m, "bypass": bypass}},
    )
