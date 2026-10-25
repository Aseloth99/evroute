"""Publication-quality static figures (matplotlib, imported lazily).

Every function takes evaluated :class:`~evroute.plan.RoutePlan` objects --
i.e. numbers produced by the independent evaluator
(:func:`evroute.evaluate.evaluate`), never by the algorithm that made the
decision -- and returns a :class:`matplotlib.figure.Figure`.  Pass ``path=``
to also write a vector PDF and a 300 dpi PNG (see :func:`evroute.viz.style.save`).

==============================  =============================================
function                        what it shows
==============================  =============================================
:func:`plot_map`                terrain (hill-shaded), roads by class, stations,
                                routes per algorithm with stops, cities
:func:`plot_soc_profile`        state of charge vs distance (or time), reserve,
                                elevation along the route
:func:`plot_time_breakdown`     trip time split into drive / charge / queue /
                                plug-in, one bar per algorithm
:func:`plot_comparison_table`   the key numbers as a figure table;
                                :func:`latex_table` gives a booktabs table
:func:`plot_pareto`             a Pareto front: time vs money, wear as colour
:func:`plot_charging_curves`    vehicle charging power vs SoC and SoC vs minutes
:func:`plot_consumption_vs_speed` kWh/100 km vs speed at several temperatures
:func:`plot_wait_profiles`      expected queueing delay vs time of day
:func:`plot_search_effort`      settled labels and runtime per algorithm
:func:`plot_search_space`       settled nodes coloured by settle order
                                (small multiples, e.g. BLS vs BLS-A*)
:func:`plot_convergence`        convergence history of the genetic algorithm
==============================  =============================================

Terrain without SciPy
---------------------
The hill-shaded background is interpolated from the node elevations only:
the node coordinates are triangulated (Delaunay, via ``matplotlib.tri``),
the elevation is interpolated linearly on a regular grid
(``matplotlib.tri.LinearTriInterpolator``) and smoothed, areas far from
every node and not enclosed by the network (open sea, unmapped land) fade to
transparent, and the result is shaded with
:class:`matplotlib.colors.LightSource` (soft-light blend of a muted
hypsometric tint).  Network nodes are the only elevation samples the
model knows, so the background shows exactly the relief the energy model
"sees" -- not more detail than the data carries.

Overlapping routes
------------------
Algorithms often agree on (parts of) the route.  On the map, routes are
drawn as parallel *lanes* (as on transit maps): each plan is offset sideways
by a fixed number of points, so every colour stays visible where routes
coincide.  In the SoC profile, plans with identical profiles are drawn with
interleaved dashes, one colour after the other.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..plan import RoutePlan
from ..scenario import PreparedScenario, Scenario
from ..units import format_duration
from . import style

__all__ = [
    "plot_map",
    "plot_soc_profile",
    "plot_time_breakdown",
    "plot_comparison_table",
    "latex_table",
    "plot_pareto",
    "plot_charging_curves",
    "plot_consumption_vs_speed",
    "plot_wait_profiles",
    "plot_search_effort",
    "plot_search_space",
    "plot_convergence",
    "map_extent",
    "soc_series",
]

PathLike = "str | Path | None"


# =============================================================== small helpers
def _plt() -> Any:
    import matplotlib.pyplot as plt

    return plt


def _as_plans(plans: Any) -> list[RoutePlan]:
    """Accept one plan, a sequence of plans or a ``{name: plan}`` mapping."""
    if plans is None:
        return []
    if isinstance(plans, RoutePlan):
        return [plans]
    if isinstance(plans, Mapping):
        return [p for p in plans.values() if p is not None]
    return [p for p in plans if p is not None]


def _routed(plans: Iterable[RoutePlan]) -> list[RoutePlan]:
    """Plans that have a path to draw (feasible or not)."""
    return [p for p in plans if p.edges]


def _title(plan: RoutePlan) -> str:
    return style.algorithm_title(plan.algorithm)


def _finish(fig: Any, path: Any) -> Any:
    if path is not None:
        style.save(fig, path)
    return fig


def _new_fig(width: str | float, aspect: float, height: float | None = None, **kw: Any) -> tuple[Any, Any]:
    plt = _plt()
    return plt.subplots(figsize=style.figsize(width, aspect, height), **kw)


def _prepared(scenario: Scenario | PreparedScenario | None) -> PreparedScenario | None:
    if scenario is None:
        return None
    return scenario.prepare() if isinstance(scenario, Scenario) else scenario


def _scenario(scenario: Scenario | PreparedScenario) -> Scenario:
    return scenario.scenario if isinstance(scenario, PreparedScenario) else scenario


def _short_name(name: str) -> str:
    """``"Kocaeli (İzmit)" -> "Kocaeli"`` (map labels stay compact)."""
    return name.split(" (")[0].strip()


def _hm(seconds: float) -> str:
    """``29234 -> "8:07"`` (hours:minutes, for tables)."""
    if not math.isfinite(seconds):
        return "--"
    m = int(round(seconds / 60.0))
    return f"{m // 60}:{m % 60:02d}"


def _guarantee(name: str) -> str:
    try:
        from ..algorithms import planner_class

        return str(planner_class(name).exact)
    except Exception:  # unknown or broken planner module
        return ""


def _nice_step(span: float, target_ticks: int = 5) -> float:
    raw = span / max(target_ticks, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def _plan_label(plan: RoutePlan, with_time: bool = True) -> str:
    t = _title(plan)
    if not plan.feasible:
        return f"{t} (infeasible)"
    if with_time:
        return f"{t} · {format_duration(plan.total_time_s)}"
    return t


# ============================================================ SoC time series
def soc_series(
    plan: RoutePlan,
    prepared: Scenario | PreparedScenario | None = None,
    x: str = "distance",
) -> tuple[list[float], list[float]]:
    """State of charge [%] along a plan, as plotted by :func:`plot_soc_profile`.

    ``x="distance"`` returns kilometres (charging stops are vertical jumps);
    ``x="time"`` returns trip hours.  With a scenario, every stop in the time
    series is resolved exactly: flat while queueing and plugging in, then the
    breakpoints of the station's piecewise-linear charging function
    (:meth:`evroute.charging.ChargingFunction.breakpoints_between`).  Between
    nodes the energy is linear, which is exact for the model's constant-rate
    edges.  All values come from the evaluated plan.
    """
    if x not in ("distance", "time"):
        raise ValueError("x must be 'distance' or 'time'")
    P = _prepared(prepared)
    q = plan.capacity_kwh
    by_pos: dict[int, list[Any]] = {}
    for st in plan.stops:
        by_pos.setdefault(st.position, []).append(st)
    xs: list[float] = []
    ys: list[float] = []
    for i in range(len(plan.nodes)):
        d = plan.node_dist_m[i] / 1000.0
        xs.append(d if x == "distance" else plan.node_arrive_s[i] / 3600.0)
        ys.append(100.0 * plan.node_energy_arrive[i] / q)
        for st in by_pos.get(i, []):
            if x == "distance":
                xs.append(d)
                ys.append(100.0 * st.energy_out_kwh / q)
                continue
            t_plug = st.arrival_s + st.wait_s + st.setup_s
            xs.append(t_plug / 3600.0)
            ys.append(100.0 * st.energy_in_kwh / q)
            if P is not None and st.energy_out_kwh > st.energy_in_kwh:
                f = P.cf[st.station]
                base = f.time_at(st.energy_in_kwh)
                for b in f.breakpoints_between(st.energy_in_kwh, st.energy_out_kwh):
                    xs.append((t_plug + f.time_at(b) - base) / 3600.0)
                    ys.append(100.0 * b / q)
            xs.append(st.departure_s / 3600.0)
            ys.append(100.0 * st.energy_out_kwh / q)
    return xs, ys


# ====================================================================== map
def map_extent(
    scenario: Scenario,
    plans: Sequence[RoutePlan] = (),
    mode: str | tuple[float, float, float, float] = "auto",
    margin: float = 0.10,
    aspect_range: tuple[float, float] = (0.55, 1.25),
) -> tuple[float, float, float, float]:
    """Map window ``(x0, x1, y0, y1)`` in projected metres.

    ``mode="auto"`` fits the routes (plus origin and destination) with a
    ``margin`` and widens the shorter side so that the height/width ratio
    stays inside ``aspect_range``; ``"network"`` shows the whole network; a
    4-tuple is used as is.
    """
    net = scenario.network
    if isinstance(mode, tuple):
        return tuple(float(v) for v in mode)  # type: ignore[return-value]
    nodes: list[int] = []
    if mode == "auto":
        for p in _routed(plans):
            nodes.extend(p.nodes)
        if nodes:
            nodes += [scenario.query.origin, scenario.query.destination]
    if not nodes:
        x0, y0, x1, y1 = net.bbox()
    else:
        xs = [net.x[v] for v in nodes]
        ys = [net.y[v] for v in nodes]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    pad = margin * max(w, h)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    w, h = x1 - x0, y1 - y0
    lo, hi = aspect_range
    if h / w < lo:
        grow = (lo * w - h) / 2.0
        y0, y1 = y0 - grow, y1 + grow
    elif h / w > hi:
        grow = (h / hi - w) / 2.0
        x0, x1 = x0 - grow, x1 + grow
    return (x0, x1, y0, y1)


def _support_distance(
    xs: Any, ys: Any, extent: tuple[float, float, float, float], radius: float, max_cells: int = 400
) -> tuple[Any, float]:
    """Distance [m] from each cell of a coarse grid to the nearest node.

    Exact Euclidean distance up to ``radius`` (larger values are reported
    as ``radius``), computed at cell resolution by a min-plus "dilation":
    every cell that contains a node is 0, and the array is shifted by every
    integer offset ``(dy, dx)`` inside the disk of ``radius`` with the
    offset length added.  ``O(cells * radius_cells**2)`` numpy work, no
    SciPy.  Returns ``(dist, cell_size)``; cell ``(i, j)`` is centred at
    ``(x0 + (j + 0.5) c, y0 + (i + 0.5) c)``.
    """
    import numpy as np

    x0, x1, y0, y1 = extent
    c = max(radius / 8.0, max(x1 - x0, y1 - y0) / max_cells)
    nx = int(math.ceil((x1 - x0) / c)) + 1
    ny = int(math.ceil((y1 - y0) / c)) + 1
    r = int(math.ceil(radius / c))
    pad = r
    big = radius
    occ = np.full((ny + 2 * pad, nx + 2 * pad), big)
    ix = np.floor((np.asarray(xs) - x0) / c).astype(int) + pad
    iy = np.floor((np.asarray(ys) - y0) / c).astype(int) + pad
    ok = (ix >= 0) & (ix < nx + 2 * pad) & (iy >= 0) & (iy < ny + 2 * pad)
    occ[iy[ok], ix[ok]] = 0.0
    dist = np.full((ny, nx), big)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d = math.hypot(dx, dy) * c
            if d > radius:
                continue
            sub = occ[pad + dy: pad + dy + ny, pad + dx: pad + dx + nx]
            np.minimum(dist, sub + d, out=dist)
    return np.minimum(dist, big), c


def _land_mask(x: Any, y: Any, support: float, max_cells: int = 420) -> tuple[Any, tuple[float, float, float, float], float]:
    """Soft "land" mask on a coarse grid over the whole network.

    A cell is land if it lies within ``support`` of a node (alpha fades
    linearly over the outer 35 %) or if it is a *hole* of the network --
    an area enclosed by roads, i.e. not connected to the border of the grid
    through cells far from every node (flood fill by repeated 4-neighbour
    dilation).  Computing this on the full network, not on the map window,
    keeps the mask independent of the zoom.
    """
    import numpy as np

    pad = 1.5 * support
    ext = (float(x.min()) - pad, float(x.max()) + pad, float(y.min()) - pad, float(y.max()) + pad)
    dist, c = _support_distance(x, y, ext, support, max_cells=max_cells)
    soft = np.clip((support - dist) / (0.35 * support), 0.0, 1.0)
    far = dist >= support * 0.999
    outside = np.zeros_like(far)
    outside[0, :] = far[0, :]
    outside[-1, :] = far[-1, :]
    outside[:, 0] = far[:, 0]
    outside[:, -1] = far[:, -1]
    while True:
        grown = outside.copy()
        grown[1:, :] |= outside[:-1, :]
        grown[:-1, :] |= outside[1:, :]
        grown[:, 1:] |= outside[:, :-1]
        grown[:, :-1] |= outside[:, 1:]
        grown &= far
        if (grown == outside).all():
            break
        outside = grown
    # everything not connected to the border through far cells is land;
    # feather the coast-like boundary with a small blur
    land = (~outside).astype(float)
    land = np.clip(_blur(land, max(0.12 * support / c, 0.6)) * 1.15, 0.0, 1.0)
    del soft
    return land, ext, c


def _blur(A: Any, sigma: float) -> Any:
    """Separable Gaussian blur (zero padding) of a 2-D array, numpy only."""
    import numpy as np

    r = max(1, int(math.ceil(3.0 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, A)
    return np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, out)


def _sample_bilinear(A: Any, fy: Any, fx: Any) -> Any:
    """Bilinear sample of ``A`` at fractional indices (clamped)."""
    import numpy as np

    fy = np.clip(fy, 0, A.shape[0] - 1)
    fx = np.clip(fx, 0, A.shape[1] - 1)
    i0 = np.floor(fy).astype(int)
    j0 = np.floor(fx).astype(int)
    i1 = np.minimum(i0 + 1, A.shape[0] - 1)
    j1 = np.minimum(j0 + 1, A.shape[1] - 1)
    wy = fy - i0
    wx = fx - j0
    return (A[i0][:, j0] * (1 - wy)[:, None] * (1 - wx)[None, :] + A[i0][:, j1] * (1 - wy)[:, None] * wx[None, :]
            + A[i1][:, j0] * wy[:, None] * (1 - wx)[None, :] + A[i1][:, j1] * wy[:, None] * wx[None, :])


def _terrain_rgba(
    scenario: Scenario,
    extent: tuple[float, float, float, float],
    resolution: int = 420,
    *,
    azdeg: float = 315.0,
    altdeg: float = 40.0,
    support: float | None = None,
) -> Any:
    """Hill-shaded RGBA image of the node elevations over ``extent``.

    1. The distinct node coordinates are triangulated (Delaunay, through
       ``matplotlib.tri``) and the elevation is interpolated linearly on a
       regular grid of ``resolution`` cells along the longer side
       (``LinearTriInterpolator``), then smoothed with a Gaussian of
       ``0.35 * support`` (normalised convolution) so that the facets of
       the sparse triangulation do not show up as artificial ridges.
    2. Pixels farther than ``support`` metres from every node carry no
       information (open sea, unmapped land) and fade to transparent; the
       default ``support`` is half the 75th percentile of the longest edge
       of the Delaunay triangles, i.e. it adapts to the sampling density.
    3. A muted hypsometric tint is shaded with
       :class:`matplotlib.colors.LightSource` (soft-light blend, light from
       the north-west); the vertical exaggeration is chosen so that the
       95th-percentile slope gets a clearly visible but not harsh shading.

    Returns ``None`` when the nodes cannot be triangulated.
    """
    import numpy as np
    import matplotlib.tri as mtri
    from matplotlib.colors import LightSource, Normalize

    net = scenario.network
    seen: set[tuple[float, float]] = set()
    xs, ys, zs = [], [], []
    for i in range(net.num_nodes):
        key = (round(net.x[i], 1), round(net.y[i], 1))
        if key in seen:
            continue
        seen.add(key)
        xs.append(net.x[i])
        ys.append(net.y[i])
        zs.append(net.elevation[i])
    if len(xs) < 3:
        return None
    x = np.asarray(xs)
    y = np.asarray(ys)
    z = np.asarray(zs, dtype=float)
    try:
        tri = mtri.Triangulation(x, y)
        t = tri.triangles
        if len(t) == 0:
            return None
    except Exception:  # degenerate (collinear) input
        return None
    if support is None:
        ex = x[t] - x[np.roll(t, 1, axis=1)]
        ey = y[t] - y[np.roll(t, 1, axis=1)]
        longest = np.sqrt(ex * ex + ey * ey).max(axis=1)
        support = 0.5 * float(np.percentile(longest, 75))
    support = max(float(support), 1.0)
    interp = mtri.LinearTriInterpolator(tri, z)
    x0, x1, y0, y1 = extent
    if (y1 - y0) > (x1 - x0):
        ny = int(resolution)
        nx = max(2, int(round(ny * (x1 - x0) / (y1 - y0))))
    else:
        nx = int(resolution)
        ny = max(2, int(round(nx * (y1 - y0) / (x1 - x0))))
    gx = np.linspace(x0, x1, nx)
    gy = np.linspace(y0, y1, ny)
    X, Y = np.meshgrid(gx, gy)
    Z = interp(X, Y)
    inside = ~np.ma.getmaskarray(Z)
    if not inside.any():
        return None
    zmin, zmax = float(z.min()), float(z.max())
    Zf = np.clip(np.ma.filled(Z.astype(float), 0.0), zmin, zmax)
    dx = (x1 - x0) / max(nx - 1, 1)
    dy = (y1 - y0) / max(ny - 1, 1)
    # Smooth away the facets of the sparse triangulation (normalised
    # convolution, so that pixels outside the hull do not leak in).
    sigma_px = 0.35 * support / max(dx, 1e-9)
    if sigma_px > 0.75:
        w = inside.astype(float)
        num = _blur(Zf * w, sigma_px)
        den = _blur(w, sigma_px)
        Zf = np.where(den > 1e-6, num / np.maximum(den, 1e-6), Zf)
    Zf = np.where(inside, Zf, float(Zf[inside].mean()))
    # land mask: near a node (soft edge) or enclosed by the network
    land, lext, c = _land_mask(x, y, support)
    fy = (gy - lext[2]) / c - 0.5
    fx = (gx - lext[0]) / c - 0.5
    alpha = np.clip(_sample_bilinear(land, fy, fx), 0.0, 1.0) * inside
    if not (alpha > 0).any():
        return None
    gyy, gxx = np.gradient(Zf, dy, dx)
    slope = np.hypot(gxx, gyy)[alpha > 0.5]
    p95 = float(np.percentile(slope, 95)) if slope.size else 0.0
    vert = min(max(1.2 / p95, 1.0), 500.0) if p95 > 0 else 1.0
    ls = LightSource(azdeg=azdeg, altdeg=altdeg)
    norm = Normalize(vmin=zmin, vmax=max(zmax, zmin + 1.0))
    rgba = ls.shade(Zf, cmap=style.terrain_cmap(), norm=norm, blend_mode="soft", vert_exag=vert, dx=dx, dy=dy)
    rgba[..., 3] = alpha
    return rgba


def _offset_polyline(xs: Sequence[float], ys: Sequence[float], off: float) -> tuple[list[float], list[float]]:
    """Polyline shifted ``off`` metres to the left (mitred joins, clamped)."""
    n = len(xs)
    if n < 2 or off == 0.0:
        return list(xs), list(ys)
    # unit left normals per segment (zero-length segments inherit)
    normals: list[tuple[float, float]] = []
    last = (0.0, 1.0)
    for i in range(n - 1):
        dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
        L = math.hypot(dx, dy)
        if L > 1e-9:
            last = (-dy / L, dx / L)
        normals.append(last)
    ox, oy = [], []
    for i in range(n):
        if i == 0:
            nx_, ny_ = normals[0]
            scale = 1.0
        elif i == n - 1:
            nx_, ny_ = normals[-1]
            scale = 1.0
        else:
            ax_, ay_ = normals[i - 1]
            bx_, by_ = normals[i]
            mx, my = ax_ + bx_, ay_ + by_
            L = math.hypot(mx, my)
            if L < 1e-9:  # U-turn: no sensible miter
                nx_, ny_, scale = bx_, by_, 1.0
            else:
                nx_, ny_ = mx / L, my / L
                cos_half = nx_ * bx_ + ny_ * by_
                scale = min(1.0 / max(cos_half, 1e-9), 2.5)
        ox.append(xs[i] + nx_ * off * scale)
        oy.append(ys[i] + ny_ * off * scale)
    return ox, oy


@dataclass
class MapContext:
    """Geometry of a drawn map, shared with :mod:`evroute.viz.animate`."""

    ax: Any
    extent: tuple[float, float, float, float]
    #: metres per typographic point at the current axes size
    m_per_pt: float
    #: plan index -> lane-offset polyline (metres) through ``plan.nodes``
    lanes: dict[int, tuple[list[float], list[float]]] = field(default_factory=dict)
    #: display-space boxes already occupied (labels, legend, markers)
    occupied: "_Occupancy" = field(default_factory=lambda: _Occupancy())
    route_artists: dict[int, list[Any]] = field(default_factory=dict)


def _fix_axes(ax: Any, extent: tuple[float, float, float, float]) -> float:
    """Set limits/aspect, hide ticks; return metres per point."""
    x0, x1, y0, y1 = extent
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(True)
        sp.set_linewidth(0.5)
        sp.set_color("#9A9A9A")
    ax.set_facecolor(style.MAP_BACKGROUND)
    ax.apply_aspect()
    bb = ax.get_window_extent()
    width_pt = bb.width * 72.0 / ax.figure.dpi
    return (ax.get_xlim()[1] - ax.get_xlim()[0]) / max(width_pt, 1e-9)


def _draw_roads(ax: Any, scenario: Scenario, extent: tuple[float, float, float, float], *, scale: float = 1.0, alpha: float = 1.0, color: str | None = None) -> None:
    from matplotlib.collections import LineCollection

    net = scenario.network
    x0, x1, y0, y1 = extent
    px, py = 0.1 * (x1 - x0), 0.1 * (y1 - y0)
    groups: dict[str, list[Any]] = {}
    X, Y, T, H, TW = net.x, net.y, net.tail, net.head, net.twin
    for e in range(net.num_edges):
        tw = TW[e]
        if 0 <= tw < e:
            continue
        u, v = T[e], H[e]
        xu, yu, xv, yv = X[u], Y[u], X[v], Y[v]
        if (max(xu, xv) < x0 - px or min(xu, xv) > x1 + px or max(yu, yv) < y0 - py or min(yu, yv) > y1 + py):
            continue
        groups.setdefault(net.road_class[e], []).append(((xu, yu), (xv, yv)))
    for cls, segs in groups.items():
        col, lw, z = style.ROAD_STYLE.get(cls, ("#B3B3B3", 0.4, 1.0))
        ax.add_collection(
            LineCollection(segs, colors=color or col, linewidths=lw * scale, alpha=alpha, zorder=z, capstyle="round")
        )


def _station_sites(scenario: Scenario) -> dict[int, tuple[str, float]]:
    """node -> (kind, max power): one marker per charging site."""
    kind: dict[int, str] = {}
    for st in scenario.stations:
        if st.kind == "DC" or st.node not in kind:
            kind[st.node] = st.kind
    power: dict[int, float] = {}
    for st in scenario.stations:
        if st.kind == kind[st.node]:
            power[st.node] = max(power.get(st.node, 0.0), st.power_kw)
    return {v: (kind[v], power[v]) for v in kind}


def _station_size(power_kw: float, scale: float = 1.0) -> float:
    d = (2.0 + 1.5 * math.sqrt(max(power_kw, 1.0) / 50.0)) * scale
    return d * d


def _draw_stations(ax: Any, scenario: Scenario, extent: tuple[float, float, float, float], *, scale: float = 1.0) -> None:
    net = scenario.network
    x0, x1, y0, y1 = extent
    ac_x, ac_y, ac_s, dc_x, dc_y, dc_s = [], [], [], [], [], []
    for v, (kind, p) in _station_sites(scenario).items():
        x, y = net.x[v], net.y[v]
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            continue
        if kind == "DC":
            dc_x.append(x), dc_y.append(y), dc_s.append(_station_size(p, scale))
        else:
            ac_x.append(x), ac_y.append(y), ac_s.append(_station_size(p, scale))
    if ac_x:
        ax.scatter(ac_x, ac_y, s=ac_s, marker="o", facecolors="white", edgecolors="#5E5E5E", linewidths=0.55, zorder=2.2)
    if dc_x:
        ax.scatter(dc_x, dc_y, s=dc_s, marker="s", facecolors="#5E5E5E", edgecolors="white", linewidths=0.4, zorder=2.3)


def _lane_offsets(n: int, gap_pt: float) -> list[float]:
    return [(i - (n - 1) / 2.0) * gap_pt for i in range(n)]


def _route_polyline(scenario: Scenario, plan: RoutePlan) -> tuple[list[float], list[float]]:
    net = scenario.network
    return [net.x[v] for v in plan.nodes], [net.y[v] for v in plan.nodes]


def _resolve_highlight(plans: Sequence[RoutePlan], highlight: Any) -> int | None:
    if highlight is None:
        return None
    if isinstance(highlight, int) and not isinstance(highlight, bool):
        return highlight if 0 <= highlight < len(plans) else None
    for i, p in enumerate(plans):
        if p is highlight or p.algorithm == highlight:
            return i
    return None


def _draw_routes(
    ctx: MapContext,
    scenario: Scenario,
    plans: Sequence[RoutePlan],
    *,
    highlight: int | None,
    lw: float = 1.5,
    gap_pt: float | None = None,
    stops: bool = True,
    faded: bool = False,
) -> None:
    ax = ctx.ax
    n = len(plans)
    gap = (lw + 0.35) if gap_pt is None else gap_pt
    offsets = _lane_offsets(n, gap)
    order = list(range(n))
    if highlight is not None:
        order.remove(highlight)
        order.append(highlight)
    for i in order:
        p = plans[i]
        xs, ys = _route_polyline(scenario, p)
        ox, oy = _offset_polyline(xs, ys, offsets[i] * ctx.m_per_pt)
        ctx.lanes[i] = (ox, oy)
        col = style.algorithm_color(p.algorithm)
        is_hi = highlight is not None and i == highlight
        width = lw * (1.45 if is_hi else 1.0)
        alpha = 0.35 if faded else (1.0 if (highlight is None or is_hi) else 0.85)
        z = 4.0 + (1.0 if is_hi else 0.0) + 0.01 * i
        ls = style.algorithm_linestyle(p.algorithm)
        arts = []
        (casing,) = ax.plot(ox, oy, color="white", lw=width + 1.3, alpha=0.9 * alpha, zorder=z - 0.5, solid_capstyle="round", solid_joinstyle="round")
        (line,) = ax.plot(ox, oy, color=col, lw=width, ls=ls, alpha=alpha, zorder=z, solid_capstyle="round", solid_joinstyle="round", dash_capstyle="butt")
        arts += [casing, line]
        if stops and p.stops:
            sx = [ox[s.position] for s in p.stops]
            sy = [oy[s.position] for s in p.stops]
            arts.append(
                ax.scatter(sx, sy, s=(3.3 + width) ** 2, marker="o", facecolors=col, edgecolors="white", linewidths=0.9, zorder=z + 0.3, alpha=alpha)
            )
        if not p.feasible and p.stranded_at_m is not None:
            # where the battery would be empty
            k = 0
            while k + 1 < len(p.node_dist_m) and p.node_dist_m[k + 1] < p.stranded_at_m:
                k += 1
            if k + 1 < len(ox):
                seg = p.node_dist_m[k + 1] - p.node_dist_m[k]
                f = (p.stranded_at_m - p.node_dist_m[k]) / seg if seg > 0 else 0.0
                xx = ox[k] + f * (ox[k + 1] - ox[k])
                yy = oy[k] + f * (oy[k + 1] - oy[k])
                arts.append(ax.scatter([xx], [yy], s=42, marker="X", facecolors=col, edgecolors="white", linewidths=0.8, zorder=z + 0.4))
        ctx.route_artists[i] = arts


class _Occupancy:
    """Display-space boxes ``(x0, y0, x1, y1)`` with a uniform-grid index.

    Label placement asks "does this box hit anything?" thousands of times;
    bucketing the boxes into ``cell``-pixel squares keeps every query to the
    handful of boxes nearby.
    """

    def __init__(self, cell: float = 24.0) -> None:
        self.cell = cell
        self.boxes: list[tuple[float, float, float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = {}

    def _keys(self, b: tuple[float, float, float, float]) -> Iterable[tuple[int, int]]:
        c = self.cell
        for gx in range(int(math.floor(b[0] / c)), int(math.floor(b[2] / c)) + 1):
            for gy in range(int(math.floor(b[1] / c)), int(math.floor(b[3] / c)) + 1):
                yield (gx, gy)

    def add(self, b: tuple[float, float, float, float]) -> None:
        i = len(self.boxes)
        self.boxes.append(b)
        grid = self.grid
        for k in self._keys(b):
            grid.setdefault(k, []).append(i)

    def hits(self, b: tuple[float, float, float, float]) -> bool:
        x0, y0, x1, y1 = b
        boxes = self.boxes
        get = self.grid.get
        for k in self._keys(b):
            for i in get(k, ()):
                c0, d0, c1, d1 = boxes[i]
                if x0 < c1 and c0 < x1 and y0 < d1 and d0 < y1:
                    return True
        return False


#: Label positions around a point: (dx, dy) [pt], ha, va -- right first,
#: as cartographers do.
_CANDIDATES: tuple[tuple[float, float, str, str], ...] = (
    (4, 0, "left", "center"),
    (-4, 0, "right", "center"),
    (0, 3.5, "center", "bottom"),
    (0, -3.5, "center", "top"),
    (3, 3, "left", "bottom"),
    (3, -3, "left", "top"),
    (-3, 3, "right", "bottom"),
    (-3, -3, "right", "top"),
)
_WIDE_CANDIDATES: tuple[tuple[float, float, str, str], ...] = (
    (7, 0, "left", "center"),
    (-7, 0, "right", "center"),
    (5, 5, "left", "bottom"),
    (-5, 5, "right", "bottom"),
    (5, -5, "left", "top"),
    (-5, -5, "right", "top"),
    (0, 7, "center", "bottom"),
    (0, -7, "center", "top"),
)


def _place_label(
    ctx: MapContext,
    renderer: Any,
    x: float,
    y: float,
    text: str,
    *,
    fontsize: float,
    weight: str = "normal",
    fontstyle: str = "normal",
    color: str = style.INK,
    force: bool = False,
    candidates: Sequence[tuple[float, float, str, str]] = _CANDIDATES,
    pad_px: float = 1.5,
    zorder: float = 7.0,
    linespacing: float = 1.1,
) -> Any | None:
    """Greedy collision-free label placement.

    The text is measured once; every candidate position is then tested
    arithmetically against the occupied boxes, and only the winning
    position becomes an artist.  ``force`` places the label at the first
    candidate inside the axes even if it collides (used for origin and
    destination).
    """
    ax = ctx.ax
    probe = ax.text(0, 0, text, fontsize=fontsize, fontweight=weight, fontstyle=fontstyle, linespacing=linespacing)
    bb = probe.get_window_extent(renderer)
    probe.remove()
    w, h = bb.width + 2.0, bb.height + 1.0  # + halo
    axbb = ax.get_window_extent()
    px, py = ax.transData.transform((x, y))
    k = ax.figure.dpi / 72.0
    chosen = None
    fallback = None
    for dx, dy, ha, va in candidates:
        ax_ = px + dx * k
        ay_ = py + dy * k
        bx0 = ax_ if ha == "left" else (ax_ - w if ha == "right" else ax_ - w / 2)
        by0 = ay_ if va == "bottom" else (ay_ - h if va == "top" else ay_ - h / 2)
        box = (bx0 - pad_px, by0 - pad_px, bx0 + w + pad_px, by0 + h + pad_px)
        inside = box[0] >= axbb.x0 + 1 and box[2] <= axbb.x1 - 1 and box[1] >= axbb.y0 + 1 and box[3] <= axbb.y1 - 1
        if not inside:
            continue
        if not ctx.occupied.hits(box):
            chosen = (dx, dy, ha, va, box)
            break
        if fallback is None:
            fallback = (dx, dy, ha, va, box)
    if chosen is None:
        if not (force and fallback is not None):
            return None
        chosen = fallback
    dx, dy, ha, va, box = chosen
    ctx.occupied.add(box)
    return ax.annotate(
        text, xy=(x, y), xytext=(dx, dy), textcoords="offset points", ha=ha, va=va, fontsize=fontsize,
        fontweight=weight, fontstyle=fontstyle, color=color, zorder=zorder, linespacing=linespacing,
        path_effects=style.halo(2.0, "white", 0.9), annotation_clip=False,
    )


def _point_obstacles(ctx: MapContext, xs: Sequence[float], ys: Sequence[float], r_px: float, step_px: float = 6.0) -> None:
    """Mark a polyline (densified in display space) as occupied."""
    if not len(xs):
        return
    pts = ctx.ax.transData.transform(list(zip(xs, ys)))
    add = ctx.occupied.add
    for i in range(len(pts)):
        x, y = pts[i]
        add((x - r_px, y - r_px, x + r_px, y + r_px))
        if i + 1 < len(pts):
            x2, y2 = pts[i + 1]
            n = int(math.hypot(x2 - x, y2 - y) / step_px)
            for j in range(1, n):
                f = j / n
                xx, yy = x + f * (x2 - x), y + f * (y2 - y)
                add((xx - r_px, yy - r_px, xx + r_px, yy + r_px))


_CORNERS = ("upper left", "upper right", "lower left", "lower right")


def _corner_scores(ctx: MapContext, weighted: Iterable[tuple[float, float, float]], frac: float = 0.34) -> dict[str, float]:
    """Amount of map content (weighted points) in each corner region."""
    x0, x1, y0, y1 = ctx.extent
    w, h = x1 - x0, y1 - y0
    score = dict.fromkeys(_CORNERS, 0.0)
    for x, y, wt in weighted:
        fx, fy = (x - x0) / w, (y - y0) / h
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            continue
        left, right, upper, lower = fx < frac, fx > 1 - frac, fy > 1 - frac, fy < frac
        if upper and left:
            score["upper left"] += wt
        if upper and right:
            score["upper right"] += wt
        if lower and left:
            score["lower left"] += wt
        if lower and right:
            score["lower right"] += wt
    return score


def _scalebar(ctx: MapContext, corner: str, fontsize: float) -> None:
    """A labelled scale bar (1-2-5 rounded length) in ``corner``."""
    ax = ctx.ax
    x0, x1, y0, y1 = ctx.extent
    w, h = x1 - x0, y1 - y0
    target = 0.2 * w
    mag = 10 ** math.floor(math.log10(target))
    length = mag
    for m in (1, 2, 5, 10):
        if m * mag <= target:
            length = m * mag
    left = "left" in corner
    upper = "upper" in corner
    bx = x0 + 0.04 * w if left else x1 - 0.04 * w - length
    by = (y1 - 0.08 * h) if upper else (y0 + 0.045 * h)
    tick = 0.012 * h
    kw = dict(color=style.INK, lw=0.9, zorder=8, solid_capstyle="butt", path_effects=style.halo(2.2, "white", 0.85))
    ax.plot([bx, bx + length], [by, by], **kw)
    ax.plot([bx, bx], [by - tick, by + tick], **kw)
    ax.plot([bx + length, bx + length], [by - tick, by + tick], **kw)
    km = length / 1000.0
    label = f"{km:g} km" if km >= 1 else f"{length:g} m"
    t = ax.text(bx + length / 2, by + 1.6 * tick, label, ha="center", va="bottom", fontsize=fontsize, color=style.INK,
                zorder=8, path_effects=style.halo(2.0, "white", 0.9))
    bb = t.get_window_extent(ax.figure.canvas.get_renderer())
    p0 = ax.transData.transform((bx, by - tick))
    p1 = ax.transData.transform((bx + length, by - tick))
    ctx.occupied.add((min(p0[0], bb.x0) - 3, p0[1] - 3, max(p1[0], bb.x1) + 3, bb.y1 + 3))


_PLACE_RANK = {"city": 0, "town": 1, "pass": 2, "landmark": 3}


def _places(scenario: Scenario) -> list[tuple[int, str, str]]:
    """Named nodes in labelling priority: ``(node, name, kind)``.

    Uses ``scenario.meta["places"]`` (kind and tier per place, as written
    by the Turkey generator) when present -- cities by tier, then towns,
    passes and landmarks; waypoints are never labelled.  Otherwise the
    insertion order of ``network.node_name`` is the priority.
    """
    net = scenario.network
    places = scenario.meta.get("places") if isinstance(scenario.meta, dict) else None
    out: list[tuple[int, str, str]] = []
    if isinstance(places, list) and places and isinstance(places[0], dict) and "node" in places[0]:
        ranked = []
        for i, pl in enumerate(places):
            kind = str(pl.get("kind", "city"))
            if kind == "waypoint":
                continue
            v = int(pl["node"])
            if not 0 <= v < net.num_nodes:
                continue
            ranked.append((_PLACE_RANK.get(kind, 4), int(pl.get("tier") or 9), i, v, str(pl.get("name") or net.node_name.get(v, "")), kind))
        ranked.sort()
        out = [(v, name, kind) for _, _, _, v, name, kind in ranked if name]
    else:
        out = [(v, name, "city") for v, name in net.node_name.items()]
    return out


def draw_map(
    ax: Any,
    scenario: Scenario,
    plans: Sequence[RoutePlan],
    *,
    extent: tuple[float, float, float, float],
    terrain: bool = True,
    roads: bool = True,
    stations: bool = True,
    labels: bool = True,
    highlight: int | None = None,
    route_lw: float = 1.5,
    stops: bool = True,
    annotate_stops: bool | None = None,
    max_labels: int = 24,
    label_size: float | None = None,
    scalebar: bool = True,
    legend: bool | str = True,
    legend_time: bool = True,
    faded_routes: bool = False,
    resolution: int | None = None,
    avoid: Sequence[tuple[float, float, float, float]] = (),
) -> MapContext:
    """Draw every map layer into ``ax`` (used by :func:`plot_map` and the animations).

    ``avoid`` lists extra display-space boxes (e.g. inset axes) that labels
    and the legend must not cover.  Returns the :class:`MapContext` with
    the lane polylines of the routes.
    """
    from matplotlib.lines import Line2D

    m_per_pt = _fix_axes(ax, extent)
    ctx = MapContext(ax=ax, extent=extent, m_per_pt=m_per_pt)
    for b in avoid:
        ctx.occupied.add(tuple(b))  # type: ignore[arg-type]
    fig = ax.figure
    fs = label_size if label_size is not None else max(5.5, float(_rc("font.size")) - 1.5)
    if terrain:
        img = _terrain_rgba(scenario, extent, resolution or 420)
        if img is not None:
            ax.imshow(img, extent=extent, origin="lower", interpolation="bilinear", zorder=0.2)
            ax.set_xlim(extent[0], extent[1])
            ax.set_ylim(extent[2], extent[3])
    if roads:
        _draw_roads(ax, scenario, extent)
    if stations:
        _draw_stations(ax, scenario, extent)
    plans = list(plans)
    _draw_routes(ctx, scenario, plans, highlight=highlight, lw=route_lw, stops=stops, faded=faded_routes)

    net = scenario.network
    o, d = scenario.query.origin, scenario.query.destination
    ax.scatter([net.x[o]], [net.y[o]], s=46, marker="o", facecolors="white", edgecolors=style.INK, linewidths=1.5, zorder=9)
    ax.scatter([net.x[d]], [net.y[d]], s=46, marker="o", facecolors=style.INK, edgecolors="white", linewidths=1.2, zorder=9)

    renderer = fig.canvas.get_renderer()
    for xy in ((net.x[o], net.y[o]), (net.x[d], net.y[d])):
        p = ax.transData.transform(xy)
        ctx.occupied.add((p[0] - 6, p[1] - 6, p[0] + 6, p[1] + 6))
    k_px = fig.dpi / 72.0
    for ox, oy in ctx.lanes.values():
        _point_obstacles(ctx, ox, oy, r_px=(1.2 + route_lw) * k_px)

    # ---------------------------------------------------- legend placement
    x0, x1, y0, y1 = extent
    places = [(v, nm, kd) for v, nm, kd in _places(scenario) if x0 <= net.x[v] <= x1 and y0 <= net.y[v] <= y1]
    weighted: list[tuple[float, float, float]] = []
    for ox, oy in ctx.lanes.values():
        weighted += [(a, b, 4.0) for a, b in zip(ox, oy)]
    weighted += [(net.x[v], net.y[v], 1.0) for v, _, _ in places[: max_labels + 6]]
    weighted += [(net.x[o], net.y[o], 40.0), (net.x[d], net.y[d], 40.0)]
    scores = _corner_scores(ctx, weighted)
    corners = sorted(_CORNERS, key=lambda c: (scores[c], ("lower left", "upper left", "lower right", "upper right").index(c)))
    handles: list[Any] = []
    if legend:
        for i, p in enumerate(plans):
            handles.append(
                Line2D([], [], color=style.algorithm_color(p.algorithm), lw=route_lw * (1.45 if i == highlight else 1.0),
                       ls=style.algorithm_linestyle(p.algorithm), label=_plan_label(p, with_time=legend_time))
            )
        in_view = [st for st in scenario.stations if x0 <= net.x[st.node] <= x1 and y0 <= net.y[st.node] <= y1]
        if stations and in_view:
            if any(st.kind == "DC" for st in in_view):
                handles.append(Line2D([], [], ls="none", marker="s", ms=4.2, mfc="#5E5E5E", mec="white", mew=0.4, label="DC charging site"))
            if any(st.kind == "AC" for st in in_view):
                handles.append(Line2D([], [], ls="none", marker="o", ms=3.4, mfc="white", mec="#5E5E5E", mew=0.55, label="AC charging site"))
        if stops and any(p.stops for p in plans):
            handles.append(Line2D([], [], ls="none", marker="o", ms=4.6, mfc="#8C8C8C", mec="white", mew=0.9, label="charging stop"))
        handles.append(Line2D([], [], ls="none", marker="o", ms=6.2, mfc="white", mec=style.INK, mew=1.5, label="origin"))
        handles.append(Line2D([], [], ls="none", marker="o", ms=6.2, mfc=style.INK, mec="white", mew=1.2, label="destination"))
    legend_corner = None
    if handles:
        if legend == "outside":
            leg = ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.01), ncol=min(4, len(handles)),
                            frameon=False, fontsize=fs + 0.5, handlelength=2.0)
        else:
            legend_corner = corners[0]
            ncol = 2 if len(handles) > 9 else 1
            leg = ax.legend(handles=handles, loc=legend_corner, frameon=True, fancybox=False, framealpha=0.9,
                            edgecolor="#BFBFBF", fontsize=fs + 0.5, handlelength=2.0, borderpad=0.5, ncol=ncol,
                            columnspacing=1.0)
            leg.get_frame().set_linewidth(0.5)
            bb = leg.get_window_extent(renderer)
            ctx.occupied.add((bb.x0 - 3, bb.y0 - 3, bb.x1 + 3, bb.y1 + 3))
        leg.set_zorder(20)
    if scalebar:
        free = [c for c in corners if c != legend_corner]
        _scalebar(ctx, free[0], fs)

    # ----------------------------------------------------------------- labels
    if labels:
        names = net.node_name
        o_name = _short_name(names.get(o, "")) or "origin"
        d_name = _short_name(names.get(d, "")) or "destination"
        for v, nm in ((o, o_name), (d, d_name)):
            _place_label(ctx, renderer, net.x[v], net.y[v], nm, fontsize=fs + 1.0, weight="bold", force=True,
                         candidates=_WIDE_CANDIDATES)
        ann = annotate_stops if annotate_stops is not None else (highlight is not None or len(plans) == 1)
        if ann and plans:
            k = highlight if highlight is not None else 0
            p = plans[k]
            ox, oy = ctx.lanes[k]
            for s in p.stops:
                text = f"{s.station_name or 'stop'}\n{100 * s.soc_in:.0f}→{100 * s.soc_out:.0f} % · {format_duration(s.charge_s)}"
                _place_label(ctx, renderer, ox[s.position], oy[s.position], text, fontsize=fs, color=style.INK, force=True,
                             candidates=_WIDE_CANDIDATES)
        placed = 0
        dots: dict[str, tuple[list[float], list[float]]] = {}
        for v, name, kind in places:
            if placed >= max_labels:
                break
            if v in (o, d):
                continue
            t = _place_label(ctx, renderer, net.x[v], net.y[v], _short_name(name), fontsize=fs if kind == "city" else fs - 0.5,
                             color="#3A3A3A" if kind in ("city", "town") else style.INK_MUTED,
                             fontstyle="italic" if kind in ("pass", "landmark") else "normal")
            if t is not None:
                placed += 1
                dx_, dy_ = dots.setdefault("pass" if kind == "pass" else "city", ([], []))
                dx_.append(net.x[v])
                dy_.append(net.y[v])
        if "city" in dots:
            ax.scatter(*dots["city"], s=5, c="#3A3A3A", linewidths=0, zorder=3.5)
        if "pass" in dots:
            ax.scatter(*dots["pass"], s=12, marker="^", c="#6B5B4B", linewidths=0, zorder=3.5)
    return ctx


def _rc(key: str) -> Any:
    import matplotlib

    return matplotlib.rcParams[key]


def plot_map(
    scenario: Scenario,
    plans: Any = (),
    ax: Any = None,
    terrain: bool = True,
    stations: bool = True,
    labels: bool = True,
    highlight: Any = None,
    *,
    extent: str | tuple[float, float, float, float] = "auto",
    roads: bool = True,
    legend: bool | str = True,
    scalebar: bool = True,
    annotate_stops: bool | None = None,
    max_labels: int = 24,
    width: str | float = "double",
    title: str | None = None,
    path: Any = None,
) -> Any:
    """Map of the scenario with one route per plan.

    Layers, bottom to top: hill-shaded terrain interpolated from the node
    elevations (``terrain``), roads by class, charging sites (squares = DC,
    circles = AC, area grows with the strongest charger), routes as
    parallel lanes in the algorithm colour (dashed = heuristic), charging
    stops (dots on the route), origin (open circle) and destination (filled
    circle), place names placed greedily without overlaps (passes in
    italics), a legend with each plan's trip time, and a scale bar.

    ``highlight`` (algorithm name, plan index or plan) draws one plan wider
    and on top and annotates its stops (station, SoC in -> out, charging
    time); with a single plan the stops are annotated too.  ``extent`` is
    ``"auto"`` (fit the routes; a double-column map is kept landscape),
    ``"network"`` or ``(x0, x1, y0, y1)`` in metres.  ``legend="outside"``
    puts the legend below the map.
    """
    plans = _routed(_as_plans(plans))
    with style.paper():
        w_in = style.figsize(width)[0]
        aspect_range = (0.5, 0.8) if w_in > 5.0 else (0.6, 1.25)
        ext = map_extent(scenario, plans, extent, aspect_range=aspect_range)
        if ax is None:
            aspect = (ext[3] - ext[2]) / (ext[1] - ext[0])
            fig = _plt().figure(figsize=(w_in, w_in * aspect))
            ax = fig.add_axes((0.005, 0.005, 0.99, 0.99))
        fig = ax.figure
        hi = _resolve_highlight(plans, highlight)
        draw_map(
            ax, scenario, plans, extent=ext, terrain=terrain, roads=roads, stations=stations, labels=labels,
            highlight=hi, annotate_stops=annotate_stops, max_labels=max_labels, scalebar=scalebar, legend=legend,
        )
        if title:
            ax.set_title(title, loc="left")
        return _finish(fig, path)


# ============================================================== SoC profile
def _interleave_groups(series: list[tuple[list[float], list[float]]], tol: float = 1e-6) -> list[list[int]]:
    groups: list[list[int]] = []
    for i, (xs, ys) in enumerate(series):
        for g in groups:
            gx, gy = series[g[0]]
            if len(gx) == len(xs) and all(abs(a - b) <= tol for a, b in zip(gx, xs)) and all(abs(a - b) <= tol for a, b in zip(gy, ys)):
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def plot_soc_profile(
    plans: Any,
    scenario: Scenario | PreparedScenario | None = None,
    *,
    x: str = "distance",
    elevation: str | None = "twin",
    reference: Any = None,
    ax: Any = None,
    width: str | float = "single",
    aspect: float = 0.62,
    legend: bool = True,
    path: Any = None,
) -> Any:
    """State of charge along each route.

    SoC [%] against driven distance (``x="distance"``; charging stops are
    vertical jumps) or trip time (``x="time"``; queueing and plugging in are
    flat, charging follows the exact charging function).  With a scenario the
    figure also shows the driving reserve (hatched band) and the elevation
    profile of the ``reference`` plan's route (default: the first feasible
    plan) as a grey silhouette -- on a secondary axis (``elevation="twin"``),
    in a small panel below (``"panel"``) or not at all (``None``).

    Plans whose profiles coincide are drawn with interleaved dashes so that
    every colour remains visible; infeasible plans are dashed and their
    stranding point is marked with a cross.
    """
    plans = _routed(_as_plans(plans))
    P = _prepared(scenario)
    with style.paper():
        ax_elev = None
        if ax is None:
            if elevation == "panel" and P is not None:
                plt = _plt()
                w, h = style.figsize(width, aspect)
                fig, (ax, ax_elev) = plt.subplots(2, 1, figsize=(w, h * 1.25), sharex=True,
                                                  gridspec_kw={"height_ratios": [3.2, 1.0], "hspace": 0.08})
            else:
                fig, ax = _new_fig(width, aspect)
        fig = ax.figure
        series = [soc_series(p, P, x) for p in plans]
        groups = _interleave_groups(series)
        handles: dict[int, Any] = {}
        lw = 1.3
        for g in groups:
            xs, ys = series[g[0]]
            k = len(g)
            for j, i in enumerate(g):
                p = plans[i]
                col = style.algorithm_color(p.algorithm)
                if k == 1:
                    ls: Any = style.algorithm_linestyle(p.algorithm) if p.feasible else (0, (2.0, 1.5))
                else:
                    L = 3.2
                    ls = (((k - j) % k) * L, (L, (k - 1) * L))
                (h,) = ax.plot(xs, ys, color=col, lw=lw, ls=ls, zorder=3 + 0.01 * i, solid_capstyle="butt",
                               alpha=1.0 if p.feasible else 0.85)
                handles[i] = h
        # charging stops: algorithm marker at the arrival point of each stop
        for i, p in enumerate(plans):
            if not p.stops:
                continue
            col = style.algorithm_color(p.algorithm)
            q = p.capacity_kwh
            if x == "distance":
                sx = [p.node_dist_m[s.position] / 1000.0 for s in p.stops]
            else:
                sx = [s.arrival_s / 3600.0 for s in p.stops]
            sy = [100.0 * s.energy_in_kwh / q for s in p.stops]
            ax.scatter(sx, sy, s=14, marker=style.algorithm_marker(p.algorithm), facecolors=col, edgecolors="white",
                       linewidths=0.5, zorder=5)
        for i, p in enumerate(plans):
            if not p.feasible and p.stranded_at_m is not None and x == "distance":
                ax.scatter([p.stranded_at_m / 1000.0], [0.0], s=30, marker="X", facecolors=style.algorithm_color(p.algorithm),
                           edgecolors="white", linewidths=0.6, zorder=6)
                ax.annotate("battery empty", (p.stranded_at_m / 1000.0, 0.0), xytext=(0, 4), textcoords="offset points",
                            ha="center", va="bottom", fontsize=_rc("font.size") - 1.5, color=style.INK_MUTED)
        y_min = min((min(ys) for _, ys in series), default=0.0)
        ax.set_ylim(min(0.0, y_min - 3.0), 102.0)
        x_max = max((max(xs) for xs, _ in series), default=1.0)
        ax.set_xlim(0.0, x_max * 1.005)
        if P is not None:
            pref = _scenario(scenario).preferences  # type: ignore[arg-type]
            res = 100.0 * pref.reserve_soc
            ax.axhspan(ax.get_ylim()[0], res, facecolor="#F3E3DD", edgecolor="none", zorder=0.5)
            ax.axhline(res, color="#B5654A", lw=0.7, ls=(0, (3, 2)), zorder=1)
            ax.annotate(f"reserve {res:.0f} %", (1.0, res), xycoords=("axes fraction", "data"), xytext=(-2, 2),
                        textcoords="offset points", ha="right", va="bottom", fontsize=_rc("font.size") - 1.5, color="#9A4F37")
        ax.set_ylabel("State of charge [%]")
        if ax_elev is None:
            ax.set_xlabel("Distance [km]" if x == "distance" else "Trip time [h]")
        ax.yaxis.set_major_locator(_plt().MultipleLocator(20))
        ax.grid(axis="y", color=style.GRID, lw=0.5)

        # ---------------------------------------------------- elevation profile
        if P is not None and elevation and plans:
            ref_i = _resolve_highlight(plans, reference)
            if ref_i is None:
                ref_i = next((i for i, p in enumerate(plans) if p.feasible), 0)
            rp = plans[ref_i]
            net = P.net
            ex: list[float] = []
            ez: list[float] = []
            for i, v in enumerate(rp.nodes):
                if x == "distance":
                    ex.append(rp.node_dist_m[i] / 1000.0)
                    ez.append(net.elevation[v])
                else:
                    ex += [rp.node_arrive_s[i] / 3600.0, rp.node_depart_s[i] / 3600.0]
                    ez += [net.elevation[v], net.elevation[v]]
            distinct_routes = len({tuple(p.edges) for p in plans}) > 1
            elabel = "Elevation [m]" if not distinct_routes else f"Elevation, {_title(rp)} route [m]"
            if elevation == "panel" and ax_elev is not None:
                ax_elev.fill_between(ex, ez, color="#D6D6D6", lw=0, zorder=1)
                ax_elev.plot(ex, ez, color="#9E9E9E", lw=0.6, zorder=2)
                ax_elev.set_ylim(0, max(ez) * 1.15 + 1)
                ax_elev.set_ylabel("Elev. [m]")
                ax_elev.set_xlabel("Distance [km]" if x == "distance" else "Trip time [h]")
                ax_elev.grid(axis="y", color=style.GRID, lw=0.5)
            elif elevation == "twin":
                ax2 = ax.twinx()
                ax2.fill_between(ex, ez, color="#DCDCDC", alpha=0.75, lw=0, zorder=1)
                ax2.plot(ex, ez, color="#B3B3B3", lw=0.6, zorder=1.5)
                ax2.set_ylim(0, max(max(ez) * 2.3, 1.0))
                ax2.set_ylabel(elabel, color="#7A7A7A")
                ax2.tick_params(axis="y", colors="#9A9A9A", labelcolor="#7A7A7A")
                ax2.spines["right"].set_visible(True)
                ax2.spines["right"].set_color("#B3B3B3")
                ax2.spines["top"].set_visible(False)
                ax.set_zorder(ax2.get_zorder() + 1)
                ax.patch.set_visible(False)
        if legend and plans:
            hs = [handles[i] for i in range(len(plans))]
            labels = [_plan_label(p, with_time=False) for p in plans]
            from matplotlib.lines import Line2D

            proxies = [Line2D([], [], color=style.algorithm_color(p.algorithm), lw=lw,
                              ls=style.algorithm_linestyle(p.algorithm) if p.feasible else (0, (2.0, 1.5)),
                              marker=style.algorithm_marker(p.algorithm), ms=3.6, mec="white", mew=0.4)
                       for p, h in zip(plans, hs)]
            ncol = 1 if len(plans) <= 4 else 2
            ax.legend(proxies, labels, loc="upper right", ncol=ncol, fontsize=_rc("legend.fontsize"), handlelength=2.2,
                      frameon=True, framealpha=0.9, edgecolor="none", borderpad=0.3)
        return _finish(fig, path)


# =========================================================== time breakdown
def plot_time_breakdown(
    plans: Any,
    *,
    ax: Any = None,
    width: str | float = "single",
    include_infeasible: bool = False,
    path: Any = None,
) -> Any:
    """Trip time of each plan split into drive / charge / queue / plug-in.

    One horizontal stacked bar per plan (hours), separated by thin white
    gaps, with the total trip time written at the end of each bar.  The
    segment lengths are the evaluator's ``drive_time_s``, ``charge_time_s``,
    ``wait_time_s`` and ``setup_time_s``; they add up to ``total_time_s``.
    """
    plans = _as_plans(plans)
    if not include_infeasible:
        plans = [p for p in plans if p.feasible]
    with style.paper():
        n = max(len(plans), 1)
        if ax is None:
            fig, ax = _new_fig(width, 0.0, height=0.55 + 0.24 * n)
        fig = ax.figure
        comps = (
            ("drive", "driving", "drive_time_s"),
            ("charge", "charging", "charge_time_s"),
            ("wait", "queueing", "wait_time_s"),
            ("setup", "plug-in", "setup_time_s"),
        )
        ys = list(range(n))[::-1]
        tmax = max((p.total_time_s for p in plans if p.feasible), default=1.0) / 3600.0
        for y, p in zip(ys, plans):
            if not p.feasible:
                ax.text(0.0, y, "  infeasible", va="center", ha="left", fontsize=_rc("font.size") - 1, color=style.INK_MUTED)
                continue
            left = 0.0
            for key, label, attr in comps:
                v = getattr(p, attr) / 3600.0
                if v <= 0:
                    continue
                ax.barh(y, v, left=left, height=0.62, color=style.TIME_COMPONENT_COLORS[key], edgecolor="white",
                        linewidth=0.7, label=label if y == ys[0] else None, zorder=2)
                left += v
            ax.text(left + 0.012 * tmax, y, format_duration(p.total_time_s), va="center", ha="left",
                    fontsize=_rc("font.size") - 1, color=style.INK)
        ax.set_yticks(ys)
        ax.set_yticklabels([_title(p) for p in plans])
        ax.tick_params(axis="y", length=0)
        ax.set_xlim(0, tmax * 1.2)
        ax.set_ylim(-0.6, n - 0.4)
        ax.set_xlabel("Trip time [h]")
        ax.spines["left"].set_visible(False)
        ax.grid(axis="x", color=style.GRID, lw=0.5)
        from matplotlib.patches import Patch

        handles = [Patch(facecolor=style.TIME_COMPONENT_COLORS[k], edgecolor="none", label=lab) for k, lab, _ in comps]
        ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=4, frameon=False,
                  handlelength=1.0, handleheight=0.8, columnspacing=0.9, borderaxespad=0.2)
        return _finish(fig, path)


# ======================================================== comparison table
_COLUMNS: dict[str, tuple[str, str]] = {
    # key: (header, better)
    "algorithm": ("Algorithm", ""),
    "guarantee": ("Guarantee", ""),
    "time": ("Trip time [h:mm]", "min"),
    "drive": ("Driving [h:mm]", ""),
    "charge": ("Charging [min]", ""),
    "wait": ("Queue+plug [min]", ""),
    "stops": ("Stops", ""),
    "distance": ("Distance [km]", ""),
    "charged": ("Charged [kWh]", ""),
    "money": ("Cost [{cur}]", "min"),
    "gcost": ("Gen. cost [{cur}]", "min"),
    "arrival": ("Arrival SoC [%]", ""),
    "runtime": ("Runtime [ms]", "min"),
    "settled": ("Settled", "min"),
}
DEFAULT_COLUMNS: tuple[str, ...] = (
    "algorithm", "guarantee", "time", "charge", "wait", "stops", "distance", "charged", "money", "arrival", "runtime", "settled",
)


def _cell(p: RoutePlan, key: str) -> tuple[str, float | None]:
    """(formatted text, numeric value for 'best' detection) of one cell."""
    feas = p.feasible
    stats = p.stats
    if key == "algorithm":
        return _title(p), None
    if key == "guarantee":
        return _guarantee(p.algorithm) or "--", None
    if not feas and key not in ("runtime", "settled"):
        return ("infeasible", None) if key == "time" else ("--", None)
    if key == "time":
        return _hm(p.total_time_s), p.total_time_s
    if key == "drive":
        return _hm(p.drive_time_s), p.drive_time_s
    if key == "charge":
        return f"{p.charge_time_s / 60:.1f}", p.charge_time_s
    if key == "wait":
        return f"{(p.wait_time_s + p.setup_time_s) / 60:.1f}", p.wait_time_s + p.setup_time_s
    if key == "stops":
        return str(p.num_stops), float(p.num_stops)
    if key == "distance":
        return f"{p.distance_m / 1000:.1f}", p.distance_m
    if key == "charged":
        return f"{p.energy_charged_kwh:.1f}", p.energy_charged_kwh
    if key == "money":
        return f"{p.money:.2f}", p.money
    if key == "gcost":
        return f"{p.generalized_cost:.2f}", p.generalized_cost
    if key == "arrival":
        return f"{100 * p.arrival_soc:.1f}", p.arrival_soc
    if key == "runtime":
        if stats is None:
            return "--", None
        return f"{1000 * stats.runtime_s:.1f}", stats.runtime_s
    if key == "settled":
        if stats is None:
            return "--", None
        return f"{stats.settled:,}", float(stats.settled)
    raise KeyError(f"unknown column {key!r}; choose from {sorted(_COLUMNS)}")


def _table_data(plans: Sequence[RoutePlan], columns: Sequence[str]) -> tuple[list[str], list[list[str]], list[list[bool]]]:
    cur = plans[0].currency if plans else ""
    headers = [_COLUMNS[c][0].format(cur=cur) for c in columns]
    cells = [[_cell(p, c) for c in columns] for p in plans]
    best = [[False] * len(columns) for _ in plans]
    for j, c in enumerate(columns):
        if _COLUMNS[c][1] != "min":
            continue
        vals = [(cells[i][j][1], i) for i, p in enumerate(plans) if p.feasible and cells[i][j][1] is not None]
        if len(vals) < 2:
            continue
        m = min(v for v, _ in vals)
        for v, i in vals:
            if v <= m * (1 + 1e-9) + 1e-12:
                best[i][j] = True
    return headers, [[t for t, _ in row] for row in cells], best


def latex_table(
    plans: Any,
    *,
    columns: Sequence[str] = DEFAULT_COLUMNS,
    caption: str | None = None,
    label: str | None = None,
    bold_best: bool = True,
    environment: str = "table*",
) -> str:
    """A booktabs LaTeX table of the plans (``\\usepackage{booktabs}``).

    Columns are chosen from ``algorithm, guarantee, time, drive, charge,
    wait, stops, distance, charged, money, gcost, arrival, runtime,
    settled``.  The best (smallest) trip time, cost, runtime and settled
    count among feasible plans are set in bold.  Pass ``environment=""`` to
    get the bare ``tabular``.
    """
    plans = _as_plans(plans)
    headers, rows, best = _table_data(plans, columns)
    align = "".join("l" if c in ("algorithm", "guarantee") else "r" for c in columns)
    lines: list[str] = []
    if environment:
        lines += [f"\\begin{{{environment}}}[t]", "\\centering"]
        if caption:
            lines.append(f"\\caption{{{_tex(caption)}}}")
        if label:
            lines.append(f"\\label{{{label}}}")
    lines += [f"\\begin{{tabular}}{{{align}}}", "\\toprule", " & ".join(_tex(h) for h in headers) + " \\\\", "\\midrule"]
    for row, b in zip(rows, best):
        cells = [(f"\\textbf{{{_tex(t)}}}" if (bold_best and bb) else _tex(t)) for t, bb in zip(row, b)]
        lines.append(" & ".join(cells) + " \\\\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    if environment:
        lines.append(f"\\end{{{environment}}}")
    return "\n".join(lines) + "\n"


def _tex(s: str) -> str:
    rep = {
        "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_",
        "{": "\\{", "}": "\\}", "~": "\\textasciitilde{}", "^": "\\textasciicircum{}",
    }
    out = "".join(rep.get(ch, ch) for ch in s)
    return out.replace("*", "$^{*}$")


def plot_comparison_table(
    plans: Any,
    *,
    columns: Sequence[str] = DEFAULT_COLUMNS,
    width: str | float = "double",
    bold_best: bool = True,
    path: Any = None,
) -> Any:
    """The key numbers of every plan as a booktabs-style figure table.

    A coloured swatch identifies the algorithm; the best trip time, cost,
    runtime and settled count are bold (see :func:`latex_table` for the
    LaTeX version of the same numbers).
    """
    plans = _as_plans(plans)
    headers, rows, best = _table_data(plans, columns)
    with style.paper():
        n = len(rows)
        fig, ax = _new_fig(width, 0.0, height=0.32 + 0.205 * (n + 1))
        ax.axis("off")
        cell_text = [["■"] + r for r in rows]
        tab = ax.table(cellText=cell_text, colLabels=[""] + headers, loc="upper center", cellLoc="right", colLoc="right",
                       bbox=(0.0, 0.0, 1.0, 1.0))
        tab.auto_set_font_size(False)
        fs = _rc("font.size") - 1.0
        ncols = len(headers) + 1
        for (r, c), cell in tab.get_celld().items():
            cell.set_fontsize(fs)
            cell.set_linewidth(0.0)
            cell.PAD = 0.04
            txt = cell.get_text()
            if c <= 1 or (c - 1 < len(columns) and columns[c - 1] in ("algorithm", "guarantee")):
                cell.set_text_props(ha="left")
                cell._loc = "left"
            if r == 0:
                txt.set_fontweight("bold")
                txt.set_color(style.INK)
                cell.visible_edges = "B"
                cell.set_linewidth(0.6)
                txt.set_wrap(True)
            else:
                p = plans[r - 1]
                if c == 0:
                    txt.set_color(style.algorithm_color(p.algorithm))
                    txt.set_fontsize(fs + 2)
                elif best[r - 1][c - 1]:
                    txt.set_fontweight("bold")
                if not p.feasible and c > 0:
                    txt.set_color(style.INK_MUTED)
                if r == n:
                    cell.visible_edges = "B"
                    cell.set_linewidth(0.6)
                else:
                    cell.visible_edges = ""
        # narrow swatch column, wider algorithm column
        widths = [0.025] + [0.11 if columns[j] == "algorithm" else (0.085 if columns[j] == "guarantee" else 0.0) for j in range(len(columns))]
        rest = [j for j in range(len(columns)) if widths[j + 1] == 0.0]
        free = max(1.0 - sum(widths), 0.1)
        for j in rest:
            widths[j + 1] = free / max(len(rest), 1)
        for (r, c), cell in tab.get_celld().items():
            if c < ncols:
                cell.set_width(widths[c])
        top = ax.plot([0, 1], [1, 1], transform=ax.transAxes, color=style.INK, lw=0.9, clip_on=False)
        del top
        return _finish(fig, path)


# =================================================================== Pareto
def plot_pareto(
    plan: RoutePlan | Sequence[RoutePlan],
    scenario: Scenario | None = None,
    *,
    others: Any = (),
    ax: Any = None,
    width: str | float = "single",
    aspect: float = 0.72,
    path: Any = None,
) -> Any:
    """A Pareto front in the (trip time, money) plane, wear as colour.

    ``plan`` is the result of the ``pareto`` planner (its ``alternatives``
    are the evaluated front members) or a list of member plans.  Marker
    colour encodes the battery wear (state-of-health loss, one-hue ramp),
    marker size the number of charging stops; the grey staircase is the
    time-money trade-off curve.  The member the planner selected (minimum
    generalised cost) is ringed.  ``others`` adds plans of other algorithms
    as reference points in their own colour and marker.
    """
    if isinstance(plan, RoutePlan):
        members = list(plan.alternatives) or [plan]
        selected = plan
    else:
        members = list(plan)
        selected = None
    members = [m for m in members if m.feasible]
    if not members:
        raise ValueError("no feasible front members to plot")
    with style.paper():
        if ax is None:
            fig, ax = _new_fig(width, aspect)
        fig = ax.figure
        T = [m.total_time_s / 3600.0 for m in members]
        M = [m.money for m in members]
        W = [m.soh_total_pct for m in members]
        S = [m.num_stops for m in members]
        wmax = max(W) if max(W) > 0 else 1.0
        exp = math.floor(math.log10(wmax)) if wmax > 0 else 0
        scale = 10.0 ** (-exp + (0 if wmax / 10 ** exp >= 2 else 1)) if wmax > 0 else 1.0
        Wd = [w * scale for w in W]
        order = sorted(range(len(members)), key=lambda i: (T[i], M[i]))
        # staircase of the non-dominated (time, money) envelope
        st_x, st_y, best_m = [], [], math.inf
        for i in order:
            if M[i] < best_m - 1e-12:
                if st_x:
                    st_x.append(T[i])
                    st_y.append(best_m)
                st_x.append(T[i])
                st_y.append(M[i])
                best_m = M[i]
        ax.plot(st_x, st_y, color="#A6A6A6", lw=0.8, zorder=1)
        sizes = [10 + 9 * s for s in S]
        cmap = style.sequential_cmap()
        sc = ax.scatter(T, M, c=Wd, s=sizes, cmap=cmap, edgecolors="white", linewidths=0.5, zorder=3)
        cb = fig.colorbar(sc, ax=ax, pad=0.02, fraction=0.06, aspect=25)
        unit = "" if scale == 1.0 else f" [×10$^{{{-int(round(math.log10(scale)))}}}$ %]"
        cb.set_label(f"Battery wear (SoH loss){unit or ' [%]'}")
        cb.outline.set_linewidth(0.4)
        cb.ax.tick_params(width=0.5, length=2)
        if selected is not None:
            ax.scatter([selected.total_time_s / 3600.0], [selected.money], s=95, facecolors="none", edgecolors=style.INK,
                       linewidths=0.9, zorder=4)
            ax.annotate("selected", (selected.total_time_s / 3600.0, selected.money), xytext=(7, 7), textcoords="offset points",
                        fontsize=_rc("font.size") - 1, color=style.INK, arrowprops=dict(arrowstyle="-", lw=0.5, color=style.INK_MUTED))
        handles = []
        from matplotlib.lines import Line2D

        for o in _as_plans(others):
            if not o.feasible:
                continue
            ax.scatter([o.total_time_s / 3600.0], [o.money], s=34, marker=style.algorithm_marker(o.algorithm),
                       facecolors=style.algorithm_color(o.algorithm), edgecolors="white", linewidths=0.6, zorder=5)
            handles.append(Line2D([], [], ls="none", marker=style.algorithm_marker(o.algorithm), ms=5,
                                  mfc=style.algorithm_color(o.algorithm), mec="white", mew=0.5, label=_title(o)))
        for s in sorted(set(S)):
            handles.append(Line2D([], [], ls="none", marker="o", ms=math.sqrt(10 + 9 * s), mfc="#9E9E9E", mec="white",
                                  mew=0.5, label=f"{s} stop{'s' if s != 1 else ''}"))
        ax.legend(handles=handles, loc="upper right", frameon=False, ncol=1, handletextpad=0.3)
        cur = members[0].currency
        ax.set_xlabel("Trip time [h]")
        ax.set_ylabel(f"Charging cost [{cur}]")
        ax.grid(color=style.GRID, lw=0.5)
        return _finish(fig, path)


# ========================================================= charging curves
def _vehicles(vehicles: Any) -> list[Any]:
    from ..vehicle import VEHICLES, Vehicle, get_vehicle

    if vehicles is None:
        return list(VEHICLES.values())
    if isinstance(vehicles, (Vehicle, str)):
        vehicles = [vehicles]
    return [get_vehicle(v) if isinstance(v, str) else v for v in vehicles]


def plot_charging_curves(
    vehicle_or_keys: Any = None,
    *,
    station_kw: float | None = None,
    soc_from: float = 0.10,
    soc_to: float = 0.80,
    width: str | float = "double",
    path: Any = None,
) -> Any:
    """Charging behaviour of vehicle presets.

    Left: maximum battery-side DC power vs SoC (the piecewise-constant bins
    the exact algorithms use).  Right: SoC vs minutes at the charger,
    starting from ``soc_from``, at a station that does not limit the power
    (or at ``station_kw``).  The legend reports the classic ``soc_from`` ->
    ``soc_to`` charging time.  Times are exact for the binned curve:
    ``sum(dSoC * battery / P)``.
    """
    vehicles = _vehicles(vehicle_or_keys)
    with style.paper():
        plt = _plt()
        w, h = style.figsize(width, 0.36)
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(w, max(h, 2.2)), gridspec_kw={"wspace": 0.28})
        for k, v in enumerate(vehicles):
            col = style.categorical(k)
            breaks = list(v.dc_curve.soc_breaks)
            powers = [min(p, station_kw) if station_kw else p for p in v.dc_curve.power_kw]
            a1.stairs(powers, [100 * b for b in breaks], color=col, lw=1.2, baseline=None, label=v.name)
            # SoC vs minutes from soc_from
            ts, ss = [0.0], [100 * soc_from]
            t = 0.0
            t_target = None
            for b0, b1, p in zip(breaks, breaks[1:], powers):
                lo, hi = max(b0, soc_from), b1
                if hi <= lo:
                    continue
                if t_target is None and soc_to <= hi and soc_to >= lo:
                    t_target = t + (soc_to - lo) * v.battery_kwh / p * 60.0
                t += (hi - lo) * v.battery_kwh / p * 60.0
                ts.append(t)
                ss.append(100 * hi)
            lab = f"{v.name}" + (f" — {100 * soc_from:.0f}→{100 * soc_to:.0f} %: {t_target:.0f} min" if t_target is not None else "")
            a2.plot(ts, ss, color=col, lw=1.2, label=lab)
            if t_target is not None:
                a2.scatter([t_target], [100 * soc_to], s=12, color=col, edgecolors="white", linewidths=0.5, zorder=4)
        a1.set_xlim(0, 100)
        a1.set_ylim(0, None)
        a1.set_xlabel("State of charge [%]")
        a1.set_ylabel("Max. charging power [kW]")
        a1.grid(color=style.GRID, lw=0.5)
        a2.axhline(100 * soc_to, color="#B3B3B3", lw=0.6, zorder=0)
        a2.set_ylim(100 * soc_from - 2, 101)
        a2.set_xlim(0, None)
        a2.set_xlabel("Minutes at the charger")
        a2.set_ylabel("State of charge [%]")
        a2.grid(color=style.GRID, lw=0.5)
        a2.legend(loc="lower right", fontsize=_rc("legend.fontsize") - 0.5, frameon=True, framealpha=0.9, edgecolor="none")
        return _finish(fig, path)


def plot_consumption_vs_speed(
    vehicles: Any = None,
    temperatures: Sequence[float] = (-10.0, 20.0, 35.0),
    *,
    speeds_kmh: Sequence[float] | None = None,
    grade: float = 0.0,
    width: str | float = "single",
    aspect: float = 0.8,
    path: Any = None,
) -> Any:
    """Steady-state consumption [kWh/100 km] vs speed for each vehicle.

    Computed with the same physics model the planners use
    (:func:`evroute.energy.consumption_curve`, a straight motorway edge at
    ``grade``).  Colour = vehicle, line style = temperature (cold weather
    adds heating load, heat adds cooling load; denser cold air adds drag).
    Speeds above a vehicle's top speed are not drawn.
    """
    from ..energy import consumption_curve
    from ..scenario import Conditions

    vehicles = _vehicles(vehicles)
    speeds = list(speeds_kmh or [float(s) for s in range(30, 165, 5)])
    styles = ["-", (0, (4.0, 1.6)), (0, (1.2, 1.4)), (0, (5, 1.5, 1.2, 1.5))]
    with style.paper():
        fig, ax = _new_fig(width, aspect)
        from matplotlib.lines import Line2D

        for k, v in enumerate(vehicles):
            sp = [s for s in speeds if s <= v.max_speed_kmh + 1e-9]
            for j, T in enumerate(temperatures):
                c = consumption_curve(v, Conditions(temperature_c=float(T)), sp, grade=grade)
                ax.plot(sp, c, color=style.categorical(k), lw=1.1, ls=styles[j % len(styles)])
        h1 = [Line2D([], [], color=style.categorical(k), lw=1.2, label=v.name) for k, v in enumerate(vehicles)]
        h2 = [Line2D([], [], color=style.INK_MUTED, lw=1.0, ls=styles[j % len(styles)], label=f"{T:g} °C")
              for j, T in enumerate(temperatures)]
        leg1 = ax.legend(handles=h1, loc="upper left", fontsize=_rc("legend.fontsize") - 0.5)
        ax.add_artist(leg1)
        ax.legend(handles=h2, loc="lower right", fontsize=_rc("legend.fontsize") - 0.5)
        ax.set_xlabel("Speed [km/h]")
        ax.set_ylabel("Consumption [kWh/100 km]")
        ax.set_xlim(min(speeds), max(speeds))
        ax.grid(color=style.GRID, lw=0.5)
        return _finish(fig, path)


# ============================================================ wait profiles
def plot_wait_profiles(
    scenario: Scenario,
    stations: Sequence[int] | None = None,
    *,
    plans: Any = (),
    top: int = 6,
    width: str | float = "single",
    aspect: float = 0.66,
    path: Any = None,
) -> Any:
    """Expected queueing delay vs time of day at selected stations.

    The curves are the FIFO-safe Erlang-C profiles the planners use
    (``PreparedScenario.wait_profiles``).  ``stations`` are station indices;
    by default the stations used by ``plans`` are shown, otherwise the
    ``top`` stations with the highest peak wait.  The departure time is
    marked, and with ``plans`` each planned arrival is a dot on its curve.
    """
    P = scenario.prepare()
    plans = _routed(_as_plans(plans))
    if stations is None:
        used: list[int] = []
        for p in plans:
            for s in p.stops:
                if s.station not in used:
                    used.append(s.station)
        if used:
            stations = used[:8]
        else:
            peak = sorted(range(len(P.stations)), key=lambda s: -max(P.wait_profiles[s](h * 900.0) for h in range(96)))
            stations = peak[:top]
    stations = list(stations)
    with style.paper():
        fig, ax = _new_fig(width, aspect)
        hours = [i / 12.0 for i in range(24 * 12 + 1)]
        for k, s in enumerate(stations):
            st = P.stations[s]
            prof = P.wait_profiles[s]
            ys = [prof(h * 3600.0) / 60.0 for h in hours]
            name = st.name or f"station {st.id}"
            ax.plot(hours, ys, color=style.categorical(k), lw=1.2, label=f"{name} ({st.power_kw:g} kW, {st.plugs} plugs)")
            for p in plans:
                for stop in p.stops:
                    if stop.station == s:
                        tod = P.time_of_day(stop.arrival_s) / 3600.0
                        ax.scatter([tod], [stop.wait_s / 60.0], s=22, marker=style.algorithm_marker(p.algorithm),
                                   facecolors=style.categorical(k), edgecolors="white", linewidths=0.6, zorder=4)
        dep = (P.departure_s % 86400.0) / 3600.0
        ax.axvline(dep, color=style.INK_MUTED, lw=0.7, ls=(0, (3, 2)), zorder=1)
        ax.annotate("departure", (dep, 1.0), xycoords=("data", "axes fraction"), xytext=(2, -2), textcoords="offset points",
                    ha="left", va="top", fontsize=_rc("font.size") - 1.5, color=style.INK_MUTED)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3))
        ax.set_ylim(0, None)
        ax.set_xlabel("Time of day [h]")
        ax.set_ylabel("Expected wait [min]")
        ax.grid(color=style.GRID, lw=0.5)
        ax.legend(loc="upper left", fontsize=_rc("legend.fontsize") - 0.5, frameon=True, framealpha=0.9, edgecolor="none",
                  bbox_to_anchor=(0.0, 0.93))
        return _finish(fig, path)


# ============================================================ search effort
def plot_search_effort(
    plans: Any,
    *,
    width: str | float = "single",
    path: Any = None,
) -> Any:
    """Search effort per algorithm: settled labels and runtime (log scales).

    ``settled`` is what each planner reports in ``stats.settled`` (labels
    for the label-setting methods, evaluator calls for the genetic
    algorithm, ...); runtime is wall-clock time of ``Planner.plan``.
    """
    plans = [p for p in _as_plans(plans) if p.stats is not None]
    with style.paper():
        plt = _plt()
        n = max(len(plans), 1)
        w = style.figsize(width)[0]
        fig, (a1, a2) = plt.subplots(1, 2, figsize=(w, 0.5 + 0.22 * n), sharey=True, gridspec_kw={"wspace": 0.08})
        ys = list(range(n))[::-1]
        for ax, key, label in ((a1, "settled", "Settled labels"), (a2, "runtime", "Runtime [ms]")):
            vals = []
            for y, p in zip(ys, plans):
                v = float(p.stats.settled) if key == "settled" else 1000.0 * p.stats.runtime_s  # type: ignore[union-attr]
                v = max(v, 1e-3)
                vals.append(v)
                ax.barh(y, v, height=0.62, color=style.algorithm_color(p.algorithm), edgecolor="white", linewidth=0.6, zorder=2)
                txt = f"{int(v):,}" if key == "settled" else (f"{v:.1f}" if v < 100 else f"{v:,.0f}")
                ax.text(v * 1.12, y, txt, va="center", ha="left", fontsize=_rc("font.size") - 1.5, color=style.INK)
            ax.set_xscale("log")
            lo = min(vals) if vals else 1.0
            hi = max(vals) if vals else 10.0
            ax.set_xlim(10 ** math.floor(math.log10(lo)) if key == "settled" else max(lo / 3, 1e-3), hi * 12)
            ax.set_xlabel(label)
            ax.grid(axis="x", color=style.GRID, lw=0.5)
            ax.spines["left"].set_visible(False)
            ax.tick_params(axis="y", length=0)
        a1.set_yticks(ys)
        a1.set_yticklabels([_title(p) for p in plans])
        a1.set_ylim(-0.6, n - 0.4)
        return _finish(fig, path)


# ============================================================ search space
def plot_search_space(
    scenario: Scenario,
    plans: Any,
    *,
    cols: int | None = None,
    width: str | float = "double",
    extent: str | tuple[float, float, float, float] = "trace",
    path: Any = None,
) -> Any:
    """Small multiples of the search space of each plan.

    Every node a planner settled (``plan.trace``, recorded with
    ``trace=True``) is a dot coloured by *when* it was first settled (light
    = early, dark = late); unsettled nodes are omitted, the route is drawn
    on top.  All panels share the same window (``extent="trace"`` fits the
    union of the traces) so the size of the explored region is directly
    comparable -- e.g. Dijkstra-order BLS against goal-directed BLS-A*.
    """
    plans = [p for p in _as_plans(plans) if p.trace is not None and p.trace.nodes]
    if not plans:
        raise ValueError("no plan carries a search trace (plan with trace=True)")
    net = scenario.network
    if extent == "trace":
        nodes = [v for p in plans for v in p.trace.nodes]  # type: ignore[union-attr]
        nodes += [v for p in plans for v in p.nodes]
        xs = [net.x[v] for v in nodes]
        ys = [net.y[v] for v in nodes]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = 0.05 * max(x1 - x0, y1 - y0, 1.0)
        ext = (x0 - pad, x1 + pad, y0 - pad, y1 + pad)
    else:
        ext = map_extent(scenario, plans, extent)
    n = len(plans)
    cols = cols or min(n, 3)
    rows = math.ceil(n / cols)
    with style.paper():
        plt = _plt()
        w = style.figsize(width)[0]
        aspect = (ext[3] - ext[2]) / (ext[1] - ext[0])
        panel_w = (w - 0.1) / cols
        fig = plt.figure(figsize=(w, rows * panel_w * aspect + 0.62))
        top_h = 0.25 / (rows * panel_w * aspect + 0.62)
        bot_h = 0.37 / (rows * panel_w * aspect + 0.62)
        cmap = style.sequential_cmap(style.OKABE_ITO["blue"], light="#C9E0F2")
        sc = None
        for k, p in enumerate(plans):
            r, c = divmod(k, cols)
            hgt = (1.0 - top_h - bot_h) / rows
            ax = fig.add_axes((c / cols + 0.004, 1.0 - top_h - (r + 1) * hgt + 0.004, 1.0 / cols - 0.008, hgt - 0.008 - top_h * 0.2))
            m_per_pt = _fix_axes(ax, ext)
            ax.set_facecolor("white")
            _draw_roads(ax, scenario, ext, alpha=0.55, scale=0.8)
            tr = p.trace
            assert tr is not None
            m = len(tr.nodes)
            order = [i / max(m - 1, 1) for i in range(m)]
            sc = ax.scatter([net.x[v] for v in tr.nodes], [net.y[v] for v in tr.nodes], c=order, cmap=cmap, vmin=0, vmax=1,
                            s=7, linewidths=0, zorder=3)
            ctx = MapContext(ax=ax, extent=ext, m_per_pt=m_per_pt)
            if p.edges:
                _draw_routes(ctx, scenario, [p], highlight=None, lw=1.4, stops=True)
            o, d = scenario.query.origin, scenario.query.destination
            ax.scatter([net.x[o]], [net.y[o]], s=30, marker="o", facecolors="white", edgecolors=style.INK, linewidths=1.2, zorder=9)
            ax.scatter([net.x[d]], [net.y[d]], s=30, marker="o", facecolors=style.INK, edgecolors="white", linewidths=1.0, zorder=9)
            settled = p.stats.settled if p.stats else m
            frac = 100.0 * m / max(net.num_nodes, 1)
            ax.set_title(f"{_title(p)}", loc="left", fontsize=_rc("font.size"), fontweight="bold", pad=3)
            ax.text(1.0, 1.0, f"{m:,} nodes ({frac:.0f} %) · {settled:,} labels", transform=ax.transAxes, ha="right",
                    va="bottom", fontsize=_rc("font.size") - 1.5, color=style.INK_MUTED)
        cax = fig.add_axes((0.3, 0.35 * bot_h, 0.4, 0.18 * bot_h))
        cb = fig.colorbar(sc, cax=cax, orientation="horizontal")
        cb.set_ticks([0, 0.5, 1])
        cb.set_ticklabels(["first", "settle order", "last"])
        cb.outline.set_linewidth(0.4)
        cb.ax.tick_params(length=0, labelsize=_rc("font.size") - 1)
        return _finish(fig, path)


# ============================================================== convergence
def plot_convergence(
    genetic_plan: RoutePlan,
    *,
    reference: Any = None,
    width: str | float = "single",
    aspect: float = 0.75,
    path: Any = None,
) -> Any:
    """Convergence of the genetic algorithm (``stats.extra["history"]``).

    Top: best feasible objective found so far after each generation (trip
    time in hours, or generalised cost), with the optimum of an exact
    ``reference`` plan (or a number) as a horizontal line.  Bottom: share
    of feasible individuals in the population.
    """
    st = genetic_plan.stats
    hist = (st.extra or {}).get("history") if st else None
    if not hist:
        raise ValueError("plan has no convergence history (stats.extra['history'])")
    feas = list((st.extra or {}).get("history_feasible") or [])  # type: ignore[union-attr]
    time_obj = genetic_plan.objective == "time"
    conv = (lambda v: v / 3600.0) if time_obj else (lambda v: v)
    with style.paper():
        plt = _plt()
        w, h = style.figsize(width, aspect)
        if feas:
            fig, (a1, a2) = plt.subplots(2, 1, figsize=(w, h), sharex=True, gridspec_kw={"height_ratios": [2.4, 1.0], "hspace": 0.1})
        else:
            fig, a1 = plt.subplots(figsize=(w, h))
            a2 = None
        gens = list(range(len(hist)))
        gx = [g for g, v in zip(gens, hist) if v is not None]
        gy = [conv(v) for v in hist if v is not None]
        col = style.algorithm_color(genetic_plan.algorithm or "genetic")
        a1.step(gx, gy, where="post", color=col, lw=1.3, label="best feasible")
        if gx:
            a1.scatter([gx[-1]], [gy[-1]], s=14, color=col, zorder=4)
        ref_val = None
        ref_label = "optimum"
        if isinstance(reference, RoutePlan):
            if reference.feasible:
                ref_val = conv(reference.objective_value)
                ref_label = f"{_title(reference)} optimum"
        elif reference is not None:
            ref_val = conv(float(reference))
        if ref_val is not None:
            a1.axhline(ref_val, color=style.INK, lw=0.8, ls=(0, (3, 2)))
            a1.annotate(ref_label, (1.0, ref_val), xycoords=("axes fraction", "data"), xytext=(-2, 2), textcoords="offset points",
                        ha="right", va="bottom", fontsize=_rc("font.size") - 1.5, color=style.INK)
        a1.set_ylabel("Trip time [h]" if time_obj else f"Gen. cost [{genetic_plan.currency}]")
        a1.grid(color=style.GRID, lw=0.5)
        a1.legend(loc="upper right")
        if a2 is not None:
            a2.fill_between(range(len(feas)), [100 * f for f in feas], step="post", color=col, alpha=0.25, lw=0)
            a2.step(range(len(feas)), [100 * f for f in feas], where="post", color=col, lw=0.9)
            a2.set_ylim(0, 105)
            a2.set_ylabel("Feasible [%]")
            a2.set_xlabel("Generation")
            a2.grid(color=style.GRID, lw=0.5)
        else:
            a1.set_xlabel("Generation")
        a1.set_xlim(0, max(len(hist) - 1, 1))
        return _finish(fig, path)
