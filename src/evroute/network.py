"""Directed road network.

The network is stored as flat parallel lists ("structure of arrays") rather
than as objects per node/edge.  Search loops in pure Python then index plain
lists, which is several times faster than attribute access on objects and
keeps the whole graph trivially serialisable to JSON.

Build networks with :class:`NetworkBuilder`; a finished :class:`RoadNetwork`
is treated as immutable by every algorithm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from .units import haversine_m


@dataclass(frozen=True)
class RoadClass:
    """Default attributes of a road category."""

    name: str
    speed_kmh: float
    #: Average full stops (lights, junctions, congestion) per km; drives the
    #: stop-and-go energy and time penalty in :mod:`evroute.energy`.
    stops_per_km: float
    #: Drawing width hint for the visualisers.
    width: float


ROAD_CLASSES: dict[str, RoadClass] = {
    "motorway": RoadClass("motorway", 120.0, 0.0, 3.0),
    "trunk": RoadClass("trunk", 100.0, 0.05, 2.5),
    "primary": RoadClass("primary", 90.0, 0.2, 2.0),
    "secondary": RoadClass("secondary", 70.0, 0.4, 1.5),
    "urban": RoadClass("urban", 50.0, 1.5, 1.2),
    "residential": RoadClass("residential", 30.0, 2.5, 0.8),
    "link": RoadClass("link", 60.0, 0.5, 1.0),
}


@dataclass
class RoadNetwork:
    """A directed graph with geometry, elevation and road attributes.

    Node ``i`` has coordinates ``(x[i], y[i])`` in projected metres and an
    elevation in metres.  Edge ``e`` goes from ``tail[e]`` to ``head[e]``.
    Two-way roads are two edges; ``twin[e]`` is the reverse edge or ``-1``.
    """

    x: list[float]
    y: list[float]
    elevation: list[float]
    tail: list[int]
    head: list[int]
    length: list[float]
    speed_kmh: list[float]
    road_class: list[str]
    twin: list[int]
    lat: list[float] | None = None
    lon: list[float] | None = None
    node_name: dict[int, str] = field(default_factory=dict)
    edge_name: dict[int, str] = field(default_factory=dict)
    out_edges: list[list[int]] = field(default_factory=list, repr=False)
    in_edges: list[list[int]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        n, m = len(self.x), len(self.tail)
        for name in ("y", "elevation"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"node attribute {name!r} has wrong length")
        for name in ("head", "length", "speed_kmh", "road_class", "twin"):
            if len(getattr(self, name)) != m:
                raise ValueError(f"edge attribute {name!r} has wrong length")
        if not self.out_edges:
            self.out_edges = [[] for _ in range(n)]
            self.in_edges = [[] for _ in range(n)]
            for e in range(m):
                self.out_edges[self.tail[e]].append(e)
                self.in_edges[self.head[e]].append(e)

    # ------------------------------------------------------------------ size
    @property
    def num_nodes(self) -> int:
        return len(self.x)

    @property
    def num_edges(self) -> int:
        return len(self.tail)

    def nodes(self) -> range:
        return range(len(self.x))

    def edges(self) -> range:
        return range(len(self.tail))

    # -------------------------------------------------------------- geometry
    def grade(self, e: int) -> float:
        """Average road grade of edge ``e`` (rise over horizontal run)."""
        d = self.length[e]
        if d <= 0:
            return 0.0
        dh = self.elevation[self.head[e]] - self.elevation[self.tail[e]]
        run = math.sqrt(max(d * d - dh * dh, 1e-9))
        return dh / run

    def bearing(self, e: int) -> float:
        """Compass bearing of edge ``e`` in degrees (0 = north, 90 = east)."""
        u, v = self.tail[e], self.head[e]
        dx, dy = self.x[v] - self.x[u], self.y[v] - self.y[u]
        return math.degrees(math.atan2(dx, dy)) % 360.0

    def euclidean(self, u: int, v: int) -> float:
        return math.hypot(self.x[v] - self.x[u], self.y[v] - self.y[u])

    def bbox(self) -> tuple[float, float, float, float]:
        return min(self.x), min(self.y), max(self.x), max(self.y)

    def nearest_node(self, x: float, y: float) -> int:
        best, best_d = -1, math.inf
        for i in range(self.num_nodes):
            d = (self.x[i] - x) ** 2 + (self.y[i] - y) ** 2
            if d < best_d:
                best, best_d = i, d
        return best

    def node_by_name(self, name: str) -> int:
        """Look a node up by its label (case- and accent-insensitive)."""
        key = _fold(name)
        for i, label in self.node_name.items():
            if _fold(label) == key:
                return i
        raise KeyError(f"no node named {name!r}")

    def edge_between(self, u: int, v: int) -> int:
        """Fastest-class edge from ``u`` to ``v``; ``-1`` if none."""
        best = -1
        for e in self.out_edges[u]:
            if self.head[e] == v and (best < 0 or self.length[e] < self.length[best]):
                best = e
        return best

    def path_nodes(self, edge_path: Iterable[int]) -> list[int]:
        """Node sequence of an edge path (empty path -> empty list)."""
        nodes: list[int] = []
        for e in edge_path:
            if not nodes:
                nodes.append(self.tail[e])
            elif nodes[-1] != self.tail[e]:
                raise ValueError(f"edge {e} does not continue the path")
            nodes.append(self.head[e])
        return nodes

    def path_length(self, edge_path: Iterable[int]) -> float:
        return sum(self.length[e] for e in edge_path)

    # -------------------------------------------------------- serialisation
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "x": [round(v, 2) for v in self.x],
            "y": [round(v, 2) for v in self.y],
            "elevation": [round(v, 2) for v in self.elevation],
            "tail": list(self.tail),
            "head": list(self.head),
            "length": [round(v, 3) for v in self.length],
            "speed_kmh": list(self.speed_kmh),
            "road_class": list(self.road_class),
            "twin": list(self.twin),
            "node_name": {str(k): v for k, v in self.node_name.items()},
            "edge_name": {str(k): v for k, v in self.edge_name.items()},
        }
        if self.lat is not None and self.lon is not None:
            d["lat"] = [round(v, 6) for v in self.lat]
            d["lon"] = [round(v, 6) for v in self.lon]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RoadNetwork":
        return cls(
            x=[float(v) for v in d["x"]],
            y=[float(v) for v in d["y"]],
            elevation=[float(v) for v in d["elevation"]],
            tail=[int(v) for v in d["tail"]],
            head=[int(v) for v in d["head"]],
            length=[float(v) for v in d["length"]],
            speed_kmh=[float(v) for v in d["speed_kmh"]],
            road_class=list(d["road_class"]),
            twin=[int(v) for v in d.get("twin", [-1] * len(d["tail"]))],
            lat=[float(v) for v in d["lat"]] if "lat" in d else None,
            lon=[float(v) for v in d["lon"]] if "lon" in d else None,
            node_name={int(k): v for k, v in d.get("node_name", {}).items()},
            edge_name={int(k): v for k, v in d.get("edge_name", {}).items()},
        )

    def stats(self) -> dict[str, float]:
        total = sum(self.length) / 2.0 if self.num_edges else 0.0
        return {
            "nodes": self.num_nodes,
            "edges": self.num_edges,
            "road_km": total / 1000.0,
            "min_elevation_m": min(self.elevation) if self.elevation else 0.0,
            "max_elevation_m": max(self.elevation) if self.elevation else 0.0,
        }

    def is_strongly_connected(self) -> bool:
        if self.num_nodes == 0:
            return True
        return (
            len(_reach(self, 0, forward=True)) == self.num_nodes
            and len(_reach(self, 0, forward=False)) == self.num_nodes
        )


def _reach(net: RoadNetwork, s: int, forward: bool) -> set[int]:
    seen = {s}
    stack = [s]
    adj = net.out_edges if forward else net.in_edges
    other = net.head if forward else net.tail
    while stack:
        u = stack.pop()
        for e in adj[u]:
            w = other[e]
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return seen


def largest_strongly_connected_component(net: RoadNetwork) -> list[int]:
    """Nodes of the largest SCC (iterative Kosaraju)."""
    n = net.num_nodes
    order: list[int] = []
    visited = [False] * n
    for s in range(n):
        if visited[s]:
            continue
        visited[s] = True
        stack: list[tuple[int, Iterator[int]]] = [(s, iter(net.out_edges[s]))]
        while stack:
            u, it = stack[-1]
            advanced = False
            for e in it:
                w = net.head[e]
                if not visited[w]:
                    visited[w] = True
                    stack.append((w, iter(net.out_edges[w])))
                    advanced = True
                    break
            if not advanced:
                stack.pop()
                order.append(u)
    comp = [-1] * n
    best: list[int] = []
    c = 0
    for s in reversed(order):
        if comp[s] >= 0:
            continue
        members = [s]
        comp[s] = c
        stack2 = [s]
        while stack2:
            u = stack2.pop()
            for e in net.in_edges[u]:
                w = net.tail[e]
                if comp[w] < 0:
                    comp[w] = c
                    members.append(w)
                    stack2.append(w)
        if len(members) > len(best):
            best = members
        c += 1
    return sorted(best)


def _fold(s: str) -> str:
    table = str.maketrans("çğıöşüâîûÇĞİÖŞÜÂÎÛ", "cgiosuaiuCGIOSUAIU")
    return s.translate(table).casefold().strip()


class NetworkBuilder:
    """Incrementally assemble a :class:`RoadNetwork`.

    >>> b = NetworkBuilder()
    >>> a = b.add_node(0, 0, elevation=100, name="A")
    >>> c = b.add_node(1000, 0, elevation=120, name="B")
    >>> _ = b.add_road(a, c, road_class="primary")
    >>> net = b.build()
    >>> net.num_edges
    2
    """

    def __init__(self) -> None:
        self.x: list[float] = []
        self.y: list[float] = []
        self.elevation: list[float] = []
        self.lat: list[float] = []
        self.lon: list[float] = []
        self.node_name: dict[int, str] = {}
        self.tail: list[int] = []
        self.head: list[int] = []
        self.length: list[float] = []
        self.speed_kmh: list[float] = []
        self.road_class: list[str] = []
        self.twin: list[int] = []
        self.edge_name: dict[int, str] = {}
        self._has_geo = True

    @property
    def num_nodes(self) -> int:
        return len(self.x)

    def add_node(
        self,
        x: float,
        y: float,
        elevation: float = 0.0,
        name: str | None = None,
        lat: float | None = None,
        lon: float | None = None,
    ) -> int:
        i = len(self.x)
        self.x.append(float(x))
        self.y.append(float(y))
        self.elevation.append(float(elevation))
        if lat is None or lon is None:
            self._has_geo = False
            self.lat.append(0.0)
            self.lon.append(0.0)
        else:
            self.lat.append(float(lat))
            self.lon.append(float(lon))
        if name:
            self.node_name[i] = name
        return i

    def _straight_length(self, u: int, v: int) -> float:
        flat = math.hypot(self.x[v] - self.x[u], self.y[v] - self.y[u])
        if self._has_geo and (self.lat[u] or self.lon[u]):
            flat = haversine_m(self.lat[u], self.lon[u], self.lat[v], self.lon[v])
        dh = self.elevation[v] - self.elevation[u]
        return math.sqrt(flat * flat + dh * dh)

    def add_edge(
        self,
        u: int,
        v: int,
        *,
        length: float | None = None,
        speed_kmh: float | None = None,
        road_class: str = "primary",
        name: str | None = None,
    ) -> int:
        if u == v:
            raise ValueError("self loops are not allowed")
        rc = ROAD_CLASSES[road_class]
        e = len(self.tail)
        self.tail.append(u)
        self.head.append(v)
        d = self._straight_length(u, v) if length is None else float(length)
        self.length.append(max(d, 1.0))
        self.speed_kmh.append(float(speed_kmh if speed_kmh is not None else rc.speed_kmh))
        self.road_class.append(road_class)
        self.twin.append(-1)
        if name:
            self.edge_name[e] = name
        return e

    def add_road(
        self,
        u: int,
        v: int,
        *,
        length: float | None = None,
        speed_kmh: float | None = None,
        road_class: str = "primary",
        name: str | None = None,
    ) -> tuple[int, int]:
        """Add a two-way road (two twin edges)."""
        a = self.add_edge(u, v, length=length, speed_kmh=speed_kmh, road_class=road_class, name=name)
        b = self.add_edge(v, u, length=self.length[a], speed_kmh=speed_kmh, road_class=road_class, name=name)
        self.twin[a], self.twin[b] = b, a
        return a, b

    def add_polyline_road(
        self,
        points: list[tuple[float, float, float]] | list[int],
        *,
        road_class: str = "primary",
        speed_kmh: float | None = None,
        name: str | None = None,
        length_factor: float = 1.0,
    ) -> list[int]:
        """Add a two-way road through existing node ids; returns the nodes."""
        nodes = [int(p) for p in points]  # type: ignore[arg-type]
        for u, v in zip(nodes, nodes[1:]):
            d = self._straight_length(u, v) * length_factor
            self.add_road(u, v, length=d, speed_kmh=speed_kmh, road_class=road_class, name=name)
        return nodes

    def build(self) -> RoadNetwork:
        geo = self._has_geo and len(self.x) > 0
        return RoadNetwork(
            x=list(self.x),
            y=list(self.y),
            elevation=list(self.elevation),
            tail=list(self.tail),
            head=list(self.head),
            length=list(self.length),
            speed_kmh=list(self.speed_kmh),
            road_class=list(self.road_class),
            twin=list(self.twin),
            lat=list(self.lat) if geo else None,
            lon=list(self.lon) if geo else None,
            node_name=dict(self.node_name),
            edge_name=dict(self.edge_name),
        )
