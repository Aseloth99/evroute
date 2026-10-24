"""Small deterministic synthetic instances (tests, examples, development).

``grid_scenario`` builds a jittered grid with rolling hills, a few fast
arterial corridors and randomly placed charging stations.  Everything is
driven by a seed, so tests are reproducible across machines.
"""

from __future__ import annotations

import math
import random

from ..network import NetworkBuilder, RoadNetwork
from ..scenario import Conditions, Preferences, Query, Scenario, Station
from ..vehicle import get_vehicle


def hills(rng: random.Random, width: float, height: float, count: int, relief_m: float):
    """Return ``f(x, y)`` = smooth terrain made of Gaussian hills/valleys."""
    bumps = []
    for _ in range(count):
        bumps.append(
            (
                rng.uniform(0, width),
                rng.uniform(0, height),
                rng.uniform(0.15, 0.45) * max(width, height),
                rng.uniform(-0.5, 1.0) * relief_m,
            )
        )

    def f(x: float, y: float) -> float:
        z = 0.0
        for cx, cy, r, a in bumps:
            z += a * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * r * r))
        return z

    return f


def grid_network(
    rows: int = 10,
    cols: int = 10,
    spacing_m: float = 8000.0,
    seed: int = 0,
    relief_m: float = 400.0,
    base_elevation_m: float = 200.0,
    jitter: float = 0.25,
    arterial_every: int = 3,
    drop_fraction: float = 0.08,
) -> RoadNetwork:
    """Jittered grid road network with terrain.

    Every ``arterial_every``-th row/column is a ``trunk`` road, the rest are
    ``secondary``.  A fraction of non-arterial links is removed (keeping the
    grid connected) so that routes are not trivially Manhattan.
    """
    rng = random.Random(seed)
    w, h = (cols - 1) * spacing_m, (rows - 1) * spacing_m
    terrain = hills(rng, w, h, 6, relief_m)
    b = NetworkBuilder()
    ids = {}
    for r in range(rows):
        for c in range(cols):
            x = c * spacing_m + rng.uniform(-jitter, jitter) * spacing_m
            y = r * spacing_m + rng.uniform(-jitter, jitter) * spacing_m
            z = max(0.0, base_elevation_m + terrain(x, y))
            ids[r, c] = b.add_node(x, y, z)

    def cls(r1: int, c1: int, r2: int, c2: int) -> str:
        if r1 == r2 and r1 % arterial_every == 0:
            return "trunk"
        if c1 == c2 and c1 % arterial_every == 0:
            return "trunk"
        return "secondary"

    links = []
    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                links.append((r, c, r, c + 1))
            if r + 1 < rows:
                links.append((r, c, r + 1, c))
    rng.shuffle(links)
    # keep a spanning structure: drop only links whose endpoints stay connected
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    keep, maybe = [], []
    for link in links:
        a, z = ids[link[0], link[1]], ids[link[2], link[3]]
        ra, rz = find(a), find(z)
        if ra != rz:
            parent[ra] = rz
            keep.append(link)
        else:
            maybe.append(link)
    n_drop = int(drop_fraction * len(links))
    droppable = [l for l in maybe if cls(*l) != "trunk"]
    dropped = set(map(tuple, droppable[:n_drop]))
    for link in keep + [l for l in maybe if tuple(l) not in dropped]:
        r1, c1, r2, c2 = link
        u, v = ids[r1, c1], ids[r2, c2]
        b.add_road(u, v, road_class=cls(r1, c1, r2, c2), length=None)
    # make lengths a bit longer than straight lines (curvy roads)
    for e in range(len(b.length)):
        b.length[e] *= 1.08
    return b.build()


def place_stations(
    net: RoadNetwork,
    count: int,
    seed: int = 0,
    *,
    exclude: set[int] | None = None,
    power_choices: tuple[float, ...] = (50.0, 100.0, 150.0, 22.0),
    price_dc: float = 0.55,
    price_ac: float = 0.35,
) -> list[Station]:
    """Scatter ``count`` stations over distinct random nodes."""
    rng = random.Random(seed + 7919)
    candidates = [v for v in net.nodes() if not exclude or v not in exclude]
    rng.shuffle(candidates)
    stations = []
    for i, v in enumerate(candidates[:count]):
        p = rng.choice(power_choices)
        kind = "AC" if p <= 22.0 else "DC"
        stations.append(
            Station(
                id=i,
                node=v,
                power_kw=p,
                kind=kind,
                plugs=rng.choice((2, 4, 6)),
                price_per_kwh=price_ac if kind == "AC" else price_dc * (1.0 + (p - 50.0) / 400.0),
                name=f"S{i}",
                daily_sessions=rng.uniform(8, 40) if kind == "DC" else rng.uniform(2, 6),
                demand_profile="highway" if kind == "DC" else "urban",
                mean_service_min=35.0 if kind == "DC" else 120.0,
            )
        )
    return stations


def grid_scenario(
    rows: int = 10,
    cols: int = 10,
    spacing_m: float = 8000.0,
    n_stations: int = 12,
    vehicle: str = "compact50",
    seed: int = 0,
    initial_soc: float = 0.5,
    temperature_c: float = 15.0,
    **pref_overrides,
) -> Scenario:
    """A ready-to-plan grid instance from the bottom-left to the top-right."""
    net = grid_network(rows, cols, spacing_m, seed=seed)
    origin, destination = 0, net.num_nodes - 1
    stations = place_stations(net, n_stations, seed, exclude={origin, destination})
    return Scenario(
        name=f"grid-{rows}x{cols}-s{seed}",
        description="Synthetic jittered grid with rolling terrain",
        network=net,
        stations=stations,
        vehicle=get_vehicle(vehicle),
        query=Query(origin=origin, destination=destination, initial_soc=initial_soc, departure_s=9 * 3600.0),
        conditions=Conditions(temperature_c=temperature_c),
        preferences=Preferences(**pref_overrides),
        currency="EUR",
        meta={"generator": "grid", "seed": seed},
    )
