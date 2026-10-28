"""Publication-quality static figures, drawn with seaborn (imported lazily).

Every function takes evaluated :class:`~evroute.plan.RoutePlan` objects --
i.e. numbers produced by the independent evaluator
(:func:`evroute.evaluate.evaluate`), never by the algorithm that made the
decision -- and returns the figure (the one seaborn draws into; it has
``savefig``).  Pass ``path=`` to also write a vector PDF and a 300 dpi PNG
(see :func:`evroute.viz.style.save`).  All figures are drawn inside
:func:`evroute.viz.style.paper` (seaborn ``"ticks"`` theme, ``"paper"``
context, Okabe-Ito algorithm colours shared with the web viewer, IEEE
column widths), which leaves global settings untouched.

Every mark is drawn by a seaborn function: lines, routes, roads and step
curves by :func:`seaborn.lineplot` (``estimator=None, sort=False``, with
``units=`` for many line pieces), points by :func:`seaborn.scatterplot`,
bars by :func:`seaborn.barplot`, step areas by :func:`seaborn.histplot`,
distributions by :func:`seaborn.boxplot` / :func:`seaborn.violinplot` /
:func:`seaborn.stripplot` / :func:`seaborn.pointplot`, and matrices, colour
scales and the terrain by :func:`seaborn.heatmap`.  Figures come from
:func:`evroute.viz.style.subplots` (a :class:`seaborn.FacetGrid`); legends
are built from the labelled artists seaborn drew.

================================  ===========================================
function                          what it shows
================================  ===========================================
:func:`plot_map`                  terrain (hill-shaded), roads by class,
                                  stations, routes per algorithm with stops,
                                  cities
:func:`plot_soc_profile`          state of charge vs distance (or time),
                                  reserve, elevation along the route
:func:`plot_time_breakdown`       trip time split into drive / charge /
                                  queue / plug-in, one bar per algorithm
:func:`plot_comparison_table`     the key numbers as a figure table;
                                  :func:`latex_table` gives a booktabs table
:func:`plot_pareto`               a Pareto front: time vs money, wear as
                                  colour (seaborn scatter)
:func:`plot_tradeoff`             a time-money front with chosen plans
                                  (fastest / balanced / cheapest) marked
:func:`plot_charging_curves`      charging power vs SoC and SoC vs minutes
:func:`plot_consumption_vs_speed` kWh/100 km vs speed, one panel per
                                  temperature (seaborn lines)
:func:`plot_wait_profiles`        expected queueing delay vs time of day
:func:`plot_search_effort`        settled labels and runtime per algorithm
                                  (dot plot; strip + mean/CI over runs)
:func:`plot_search_space`         settled nodes coloured by settle order
                                  (small multiples, e.g. BLS vs BLS-A*)
:func:`plot_convergence`          convergence of the genetic algorithm
:func:`plot_gap_distribution`     optimality gap over many instances
                                  (seaborn box/violin + strip)
:func:`plot_metric_heatmap`       instance x algorithm matrix (seaborn)
:func:`make_gallery`              regenerates ``docs/img/fig_*.png``
================================  ===========================================

Terrain without SciPy
---------------------
The hill-shaded background is interpolated from the node elevations only
(numpy, no SciPy): the elevations are spread onto a regular grid by
normalised Gaussian convolution, which also continues the surface smoothly
past the outermost roads; areas far from every node and not enclosed by
the network (open sea, unmapped land) fade to transparent.  The grid is
drawn as two :func:`seaborn.heatmap` layers in an axes under the map: a
muted hypsometric tint, and a black/white hill-shading layer whose opacity
reproduces a soft-light blend (light from the north-west).  Network nodes
are the only elevation samples the model knows, so the background shows
exactly the relief the energy model "sees" -- not more detail than the
data carries.

Overlapping routes
------------------
Algorithms often agree on (parts of) the route.  On the map, routes are
drawn as parallel *lanes* (as on transit maps): each lane is offset sideways
by a fixed number of points, so every colour stays visible where routes
coincide; plans that drive the same edges and stop at the same nodes share
one lane whose colours alternate along the route.  In the SoC profile,
plans whose profiles coincide are drawn with interleaved dashes, one colour
after the other.  Labels are placed greedily without overlaps (a uniform
grid index of occupied display boxes); places under the route bundle may be
labelled a little further away, clear of the lanes.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    "plot_tradeoff",
    "plot_charging_curves",
    "plot_consumption_vs_speed",
    "plot_wait_profiles",
    "plot_search_effort",
    "plot_search_space",
    "plot_convergence",
    "plot_gap_distribution",
    "plot_metric_heatmap",
    "gap_rows",
    "make_gallery",
    "draw_map",
    "map_extent",
    "soc_series",
]

# =============================================================== small helpers
def _sns() -> Any:
    return style.require_seaborn()


def _fs() -> float:
    """Body font size [pt] in effect (inside :func:`style.paper`)."""
    return style.font_size()


def _line(ax: Any, xs: Sequence[float], ys: Sequence[float], **kw: Any) -> Any:
    """One polyline, drawn as given (:func:`seaborn.lineplot` without
    aggregation or sorting); returns the line artist."""
    _sns().lineplot(x=list(xs), y=list(ys), estimator=None, sort=False, legend=False, ax=ax, **kw)
    return ax.get_lines()[-1]


def _segments(ax: Any, pieces: Sequence[tuple[Sequence[float], Sequence[float]]], **kw: Any) -> None:
    """Many polylines in one :func:`seaborn.lineplot` call (``units=``)."""
    xs: list[float] = []
    ys: list[float] = []
    units: list[int] = []
    for u, (px, py) in enumerate(pieces):
        xs += list(px)
        ys += list(py)
        units += [u] * len(px)
    if xs:
        _sns().lineplot(x=xs, y=ys, units=units, estimator=None, sort=False, legend=False, ax=ax, **kw)


def _points(ax: Any, xs: Sequence[float], ys: Sequence[float], **kw: Any) -> Any:
    """Markers (:func:`seaborn.scatterplot`); returns the collection, or
    ``None`` when there is nothing to draw."""
    if not len(xs):
        return None
    n = len(ax.collections)
    _sns().scatterplot(x=list(xs), y=list(ys), legend=False, ax=ax, **kw)
    return ax.collections[-1] if len(ax.collections) > n else None


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
    return style.subplots(figsize=style.figsize(width, aspect, height), **kw)


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
    m = round(seconds / 60.0)
    return f"{m // 60}:{m % 60:02d}"


def _guarantee(name: str) -> str:
    try:
        from ..algorithms import planner_class

        return str(planner_class(name).exact)
    except Exception:  # unknown or broken planner module
        return ""


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
    *,
    clip_empty: bool = True,
) -> tuple[list[float], list[float]]:
    """State of charge [%] along a plan, as plotted by :func:`plot_soc_profile`.

    ``x="distance"`` returns kilometres (charging stops are vertical jumps);
    ``x="time"`` returns trip hours.  With a scenario, every stop in the time
    series is resolved exactly: flat during a voluntary hold
    (:attr:`~evroute.plan.ChargeStop.hold_s`), while queueing and plugging in, then the
    breakpoints of the station's piecewise-linear charging function
    (:meth:`evroute.charging.ChargingFunction.breakpoints_between`).  Between
    nodes the energy is linear, which is exact for the model's constant-rate
    edges.  All values come from the evaluated plan.

    The evaluator keeps integrating an infeasible plan past an empty
    battery; with ``clip_empty`` the series ends where the SoC first reaches
    0 % (linear interpolation, the same rule the evaluator uses for
    ``RoutePlan.stranded_at_m``).
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
            t_plug = st.join_s + st.wait_s + st.setup_s  # after a voluntary hold, if any
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
    if clip_empty:
        for i in range(1, len(ys)):
            if ys[i] < 0.0 <= ys[i - 1]:
                f = ys[i - 1] / (ys[i - 1] - ys[i])
                xe = xs[i - 1] + f * (xs[i] - xs[i - 1])
                del xs[i:], ys[i:]
                xs.append(xe)
                ys.append(0.0)
                break
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
    nx = math.ceil((x1 - x0) / c) + 1
    ny = math.ceil((y1 - y0) / c) + 1
    r = math.ceil(radius / c)
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

    A cell is land if it lies within ``support`` of a node or if it is a
    *hole* of the network -- an area enclosed by roads, i.e. not connected
    to the border of the grid through cells far from every node (flood fill
    by repeated 4-neighbour dilation).  The binary mask is feathered with a
    small Gaussian blur.  Computing this on the full network, not on the
    map window, keeps the mask independent of the zoom.
    """
    import numpy as np

    pad = 1.5 * support
    ext = (float(x.min()) - pad, float(x.max()) + pad, float(y.min()) - pad, float(y.max()) + pad)
    dist, c = _support_distance(x, y, ext, support, max_cells=max_cells)
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
    return land, ext, c


def _blur(A: Any, sigma: float, truncate: float = 3.0) -> Any:
    """Separable Gaussian blur (zero padding) of a 2-D array, numpy only;
    the kernel is cut at ``truncate`` standard deviations."""
    import numpy as np

    r = max(1, math.ceil(truncate * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()

    def conv(v: Any) -> Any:  # "same" size even when the kernel is longer than v
        return np.convolve(v, k, mode="full")[r: r + len(v)]

    out = np.apply_along_axis(conv, 1, A)
    return np.apply_along_axis(conv, 0, out)


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


def _convex_hull(x: Any, y: Any) -> list[tuple[float, float]]:
    """Convex hull of the points, counter-clockwise (Andrew's monotone chain)."""
    pts = sorted(set(zip((float(v) for v in x), (float(v) for v in y))))
    if len(pts) < 3:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    upper: list[tuple[float, float]] = []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def _terrain_support(x: Any, y: Any, hull: Sequence[tuple[float, float]], samples: int = 1200) -> float:
    """Typical half-gap between roads [m]: 1.7 x the mean distance from a
    regular sample of the network's convex hull to the nearest node.

    Over most of a road network this matches half the area-weighted 40th
    percentile of the longest Delaunay edge (the measure of earlier
    versions) within about 15 %, without a triangulation.
    """
    import numpy as np

    x0, x1, y0, y1 = float(x.min()), float(x.max()), float(y.min()), float(y.max())
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    nx = max(2, round(math.sqrt(samples * w / h)))
    ny = max(2, round(samples / nx))
    X, Y = np.meshgrid(np.linspace(x0, x1, nx), np.linspace(y0, y1, ny))
    inside = np.ones(X.shape, dtype=bool)
    for i in range(len(hull)):
        (ax_, ay_), (bx_, by_) = hull[i], hull[(i + 1) % len(hull)]
        inside &= (bx_ - ax_) * (Y - ay_) - (by_ - ay_) * (X - ax_) >= 0
    px, py = X[inside], Y[inside]
    if not len(px):
        return 0.05 * max(w, h)
    d = np.empty(len(px))
    chunk = max(1, int(4_000_000 // max(len(x), 1)))
    for s in range(0, len(px), chunk):
        dx = px[s: s + chunk, None] - x[None, :]
        dy = py[s: s + chunk, None] - y[None, :]
        d[s: s + chunk] = np.sqrt((dx * dx + dy * dy).min(axis=1))
    return 1.7 * float(d.mean())


def _spread(x: Any, y: Any, v: Any, gx: Any, gy: Any, sigma: float) -> tuple[Any, Any]:
    """Gaussian-weighted sums of ``v`` and of the weights at the grid points
    ``gx`` (columns) x ``gy`` (rows), for normalised convolution.

    The samples are binned onto a working grid of the target cell size
    (coarser only for very wide kernels, at most ``sigma / 24``, so that the
    bilinear read-back leaves no facets for the hill-shading to show),
    blurred, and read back bilinearly; nodes up to ``5 sigma`` outside the
    target window contribute too.
    """
    import numpy as np

    step = float(min(abs(gx[1] - gx[0]) if len(gx) > 1 else sigma, abs(gy[1] - gy[0]) if len(gy) > 1 else sigma))
    c = max(sigma / 24.0, step)
    pad = 5.0 * sigma
    X0, X1 = float(gx.min()) - pad, float(gx.max()) + pad
    Y0, Y1 = float(gy.min()) - pad, float(gy.max()) + pad
    nxc = math.ceil((X1 - X0) / c) + 1
    nyc = math.ceil((Y1 - Y0) / c) + 1
    ix = np.floor((x - X0) / c).astype(int)
    iy = np.floor((y - Y0) / c).astype(int)
    ok = (ix >= 0) & (ix < nxc) & (iy >= 0) & (iy < nyc)
    num = np.zeros((nyc, nxc))
    den = np.zeros((nyc, nxc))
    np.add.at(num, (iy[ok], ix[ok]), v[ok])
    np.add.at(den, (iy[ok], ix[ok]), 1.0)
    # a long kernel: where a single node dominates, the edge of a 3-sigma
    # cut-off would show as a straight seam in the hill-shading
    num = _blur(num, sigma / c, truncate=5.0)
    den = _blur(den, sigma / c, truncate=5.0)
    fy = (gy - Y0) / c - 0.5
    fx = (gx - X0) / c - 0.5
    return _sample_bilinear(num, fy, fx), _sample_bilinear(den, fy, fx)


@dataclass
class _Terrain:
    """Terrain layers on a grid over the map window (row 0 = north)."""

    elevation: Any
    zlim: tuple[float, float]
    #: opacity of the tint (0 over open sea and unmapped land)
    land: Any
    #: 1 = lit slope (drawn white), 0 = shaded slope (drawn black)
    light: Any
    #: opacity of the black/white shading layer
    shade: Any


def _terrain_grids(
    scenario: Scenario,
    extent: tuple[float, float, float, float],
    resolution: int = 640,
    *,
    azdeg: float = 315.0,
    altdeg: float = 40.0,
    support: float | None = None,
) -> _Terrain | None:
    """Hill-shaded terrain of the node elevations over ``extent``.

    1. The elevations of the distinct node positions are spread onto a
       regular grid of ``resolution`` cells along the longer side by
       normalised Gaussian convolution (``sigma = support / 2``); where
       nodes are sparse the estimate leans on a wider pass (``2 support``),
       which also continues the surface smoothly past the outermost roads.
    2. Cells farther than ``support`` metres from every node and not
       enclosed by roads carry no information (open sea, unmapped land)
       and fade to transparent (:func:`_land_mask`); the default
       ``support`` adapts to the gaps between roads
       (:func:`_terrain_support`).
    3. The hill-shading intensity ``I`` (light from azimuth ``azdeg``,
       altitude ``altdeg``; vertical exaggeration chosen so that the
       95th-percentile slope gets a clearly visible but not harsh shading)
       is applied to the hypsometric tint ``t`` as a soft-light blend,
       ``t + (2I - 1) t (1 - t)``: a white layer of opacity ``(2I - 1) t``
       on lit slopes, a black one of opacity ``(1 - 2I)(1 - t)`` in the
       shade.

    Returns ``None`` when the nodes span no area.
    """
    import numpy as np

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
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    z = np.asarray(zs, dtype=float)
    hull = _convex_hull(x, y)
    if len(hull) < 3:  # collinear nodes
        return None
    if support is None:
        support = _terrain_support(x, y, hull)
    support = max(float(support), 1.0)
    x0, x1, y0, y1 = extent
    if (y1 - y0) > (x1 - x0):
        ny = int(resolution)
        nx = max(2, round(ny * (x1 - x0) / (y1 - y0)))
    else:
        nx = int(resolution)
        ny = max(2, round(nx * (y1 - y0) / (x1 - x0)))
    dx = (x1 - x0) / nx
    dy = (y1 - y0) / ny
    gx = x0 + (np.arange(nx) + 0.5) * dx
    gy = y1 - (np.arange(ny) + 0.5) * dy  # row 0 = north, as a heatmap draws it
    num, den = _spread(x, y, z, gx, gy, 0.5 * support)
    num_w, den_w = _spread(x, y, z, gx, gy, 2.0 * support)
    wide = np.where(den_w > 1e-9 * max(float(den_w.max()), 1e-300), num_w / np.maximum(den_w, 1e-300), float(z.mean()))
    # trust the fine pass as much as a single node at distance ``support`` would
    k = math.exp(-2.0) / (2.0 * math.pi * (0.5 * support / max(0.5 * support / 24.0, min(dx, dy))) ** 2)
    zmin, zmax = float(z.min()), float(z.max())
    Z = np.clip((num + k * wide) / (den + k), zmin, zmax)
    # land mask: near a node (soft edge) or enclosed by the network
    land, lext, c = _land_mask(x, y, support)
    alpha = np.clip(_sample_bilinear(land, (gy - lext[2]) / c - 0.5, (gx - lext[0]) / c - 0.5), 0.0, 1.0)
    if not (alpha > 0).any():
        return None
    # hill-shading (the normal-vector formulation of matplotlib's LightSource)
    gyy, gxx = np.gradient(Z, dy, dx)
    slope = np.hypot(gxx, gyy)[alpha > 0.5]
    p95 = float(np.percentile(slope, 95)) if slope.size else 0.0
    vert = min(max(1.2 / p95, 1.0), 500.0) if p95 > 0 else 1.0
    e_dy, e_dx = np.gradient(vert * Z, -dy, dx)  # rows run southwards
    norm = np.sqrt(e_dx * e_dx + e_dy * e_dy + 1.0)
    az = math.radians(90.0 - azdeg)
    alt = math.radians(altdeg)
    light = (-e_dx * math.cos(az) * math.cos(alt) - e_dy * math.sin(az) * math.cos(alt) + math.sin(alt)) / norm
    lo, hi = float(light.min()), float(light.max())
    if hi - lo > 1e-6:
        light = (light - lo) / (hi - lo)
    light = np.clip(light, 0.0, 1.0)
    zlim = (zmin, max(zmax, zmin + 1.0))
    tone = style.terrain_cmap()((Z - zlim[0]) / (zlim[1] - zlim[0]))[..., :3].mean(axis=-1)
    lit = light >= 0.5
    shade = np.where(lit, (2.0 * light - 1.0) * tone, (1.0 - 2.0 * light) * (1.0 - tone))
    return _Terrain(elevation=Z, zlim=zlim, land=alpha, light=lit.astype(float), shade=np.clip(shade, 0.0, 1.0) * alpha)


def _heatmap_layer(ax: Any, values: Any, opacity: Any, **kw: Any) -> None:
    """One :func:`seaborn.heatmap` layer with a per-cell opacity.

    The opacity is set on the mesh seaborn returns, after giving it an
    explicit face colour array and no edge colour: matplotlib then applies
    it vectorised instead of converting the default colours cell by cell
    (seconds for a map-sized grid).  The mesh is rasterised in PDF output.
    """
    import numpy as np

    _sns().heatmap(values, ax=ax, cbar=False, xticklabels=False, yticklabels=False, rasterized=True, **kw)
    mesh = ax.collections[-1]
    mesh.set_edgecolor("none")
    mesh.set_facecolor(np.zeros((values.size, 4)))
    mesh.set_alpha(opacity)


def _draw_terrain(ax: Any, t: _Terrain) -> Any:
    """Draw the terrain as two :func:`seaborn.heatmap` layers (tint, then
    the black/white shading) in a new axes right under ``ax``; returns it.
    The new axes follows the box of ``ax`` at every draw, so the terrain
    stays aligned when the layout changes later (``tight_layout``, a new
    figure size)."""
    fig = ax.figure
    under = fig.add_axes(ax.get_position(), zorder=ax.get_zorder() - 1)

    def follow(_under: Any, renderer: Any) -> Any:
        locator = ax.get_axes_locator()
        ax.apply_aspect(locator(ax, renderer) if locator else None)
        return ax.get_position()

    under.set_axes_locator(follow)
    under.set_facecolor(style.MAP_BACKGROUND)
    _heatmap_layer(under, t.elevation, t.land, cmap=style.terrain_cmap(), vmin=t.zlim[0], vmax=t.zlim[1])
    _heatmap_layer(under, t.light, t.shade, cmap=_sns().blend_palette(["black", "white"], as_cmap=True), vmin=0.0,
                   vmax=1.0)
    under.set_xlabel("")
    under.set_ylabel("")
    ax.set_facecolor("none")
    return under


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
    """Geometry of a drawn map (axes, window, scale and route lanes)."""

    ax: Any
    extent: tuple[float, float, float, float]
    #: metres per typographic point at the current axes size
    m_per_pt: float
    #: plan index -> lane-offset polyline (metres) through ``plan.nodes``
    lanes: dict[int, tuple[list[float], list[float]]] = field(default_factory=dict)
    #: display-space boxes already occupied (labels, legend, markers)
    occupied: _Occupancy = field(default_factory=lambda: _Occupancy())
    #: display-space boxes covered by the route lanes only
    lane_index: _Occupancy = field(default_factory=lambda: _Occupancy())
    route_artists: dict[int, list[Any]] = field(default_factory=dict)
    #: plan index -> legend key line (colour, width, dash pattern)
    legend_keys: dict[int, Any] = field(default_factory=dict)
    #: interleaved dash lines of each shared lane
    shared_lines: list[list[Any]] = field(default_factory=list)
    #: charging-stop markers per lane (``(neutral, collection)``)
    stop_marks: list[tuple[bool, Any]] = field(default_factory=list)
    #: legend artists of the base layers ("DC charging site", ...)
    keys: dict[str, Any] = field(default_factory=dict)


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
    """Roads by class: one :func:`seaborn.lineplot` per class, one unit per
    road segment (twin edges once; segments far outside ``extent`` skipped)."""
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
        groups.setdefault(net.road_class[e], []).append(((xu, xv), (yu, yv)))
    for cls, segs in groups.items():
        col, lw, z = style.ROAD_STYLE.get(cls, ("#B3B3B3", 0.4, 1.0))
        _segments(ax, segs, color=color or col, lw=lw * scale, alpha=alpha, zorder=z, solid_capstyle="round")


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


def _draw_stations(ax: Any, scenario: Scenario, extent: tuple[float, float, float, float], *, scale: float = 1.0) -> dict[str, Any]:
    """Charging sites in ``extent`` (:func:`seaborn.scatterplot`); returns
    the drawn collections by legend label."""
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
    keys: dict[str, Any] = {}
    dc = _points(ax, dc_x, dc_y, s=dc_s, marker="s", color="#5E5E5E", edgecolor="white", linewidth=0.45, zorder=2.3)
    ac = _points(ax, ac_x, ac_y, s=ac_s, marker="o", color="white", edgecolor="#5E5E5E", linewidth=0.55, zorder=2.2)
    if dc is not None:
        keys["DC charging site"] = dc
    if ac is not None:
        keys["AC charging site"] = ac
    return keys


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


def _lane_groups(plans: Sequence[RoutePlan], highlight: int | None) -> list[list[int]]:
    """Plans that would draw the *same picture* share one map lane.

    Two plans share a lane if they drive the same edge sequence, stop at the
    same positions and strand at the same point (if at all); exact methods
    very often agree, and drawing them as separate parallel lanes would
    only widen the bundle.  The highlighted plan always gets its own lane.
    """
    groups: list[list[int]] = []
    index: dict[tuple[Any, ...], int] = {}
    for i, p in enumerate(plans):
        if i == highlight:
            groups.append([i])
            continue
        key = (
            tuple(p.edges),
            tuple(s.position for s in p.stops),
            None if p.stranded_at_m is None else round(p.stranded_at_m, 0),
        )
        if key in index:
            groups[index[key]].append(i)
        else:
            index[key] = len(groups)
            groups.append([i])
    return groups


def _pale(color: str, amount: float = 0.62) -> tuple[float, float, float]:
    """``color`` mixed with ``amount`` of white."""
    return style.tint(color, amount)


def _interleaved_dashes(k: int, j: int, seg_pt: float, lw: float) -> tuple[float, tuple[float, float]]:
    """Dash pattern so that ``k`` lines drawn on top of each other show
    consecutive ``seg_pt``-long segments of each colour (line ``j`` first
    at offset ``j * seg_pt``).  Matplotlib scales dashes by the line width,
    so the pattern is given in units of ``lw``."""
    u = seg_pt / max(lw, 1e-9)
    return (((k - j) % k) * u, (u, (k - 1) * u))


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
    share_lanes: bool = True,
) -> list[list[int]]:
    """Draw the routes as parallel lanes; return the lane groups.

    Each lane is offset sideways by a fixed number of points (transit-map
    style) so that coinciding routes stay distinguishable.  All casings
    (white outlines) are drawn below all lanes, so neighbouring lanes touch
    without white seams.  Plans that draw the same picture share a lane
    (:func:`_lane_groups`) whose colours alternate along the route; a
    heuristic drawn alone gets a dashed line over a pale tint of its
    colour, which keeps the lane continuous.  Every line and marker is a
    :func:`seaborn.lineplot` / :func:`seaborn.scatterplot`; each plan also
    gets a one-point line (it draws nothing) that carries its legend key:
    colour, width and dash pattern.
    """
    ax = ctx.ax
    n = len(plans)
    groups = _lane_groups(plans, highlight) if share_lanes else [[i] for i in range(n)]
    gap = (lw + 0.3) if gap_pt is None else gap_pt
    offsets = _lane_offsets(len(groups), gap)
    order = list(range(len(groups)))
    hi_group = next((g for g, members in enumerate(groups) if highlight in members), None)
    if hi_group is not None:
        order.remove(hi_group)
        order.append(hi_group)
    for g in order:
        members = groups[g]
        lead = plans[members[0]]
        xs, ys = _route_polyline(scenario, lead)
        ox, oy = _offset_polyline(xs, ys, offsets[g] * ctx.m_per_pt)
        is_hi = g == hi_group
        width = lw * (1.45 if is_hi else 1.0)
        alpha = 0.35 if faded else (1.0 if (highlight is None or is_hi) else 0.9)
        z = 4.0 + (1.0 if is_hi else 0.0) + 0.01 * g
        arts: list[Any] = [
            _line(ax, ox, oy, color="white", lw=width + 1.6, alpha=0.95 * alpha, zorder=(z - 0.5) if is_hi else 3.9,
                  solid_capstyle="round", solid_joinstyle="round")
        ]
        k = len(members)
        interleaved: list[Any] = []
        for j, i in enumerate(members):
            ctx.lanes[i] = (ox, oy)
            p = plans[i]
            col = style.algorithm_color(p.algorithm)
            own = style.algorithm_linestyle(p.algorithm)
            ctx.legend_keys[i] = _line(ax, ox[:1], oy[:1], color=col, lw=width, linestyle=own, zorder=z,
                                       solid_capstyle="butt", dash_capstyle="butt")
            if k > 1:
                ls: Any = _interleaved_dashes(k, j, 5.0, width)
            else:
                ls = own
                if ls != "-":
                    arts.append(_line(ax, ox, oy, color=_pale(col), lw=width, alpha=alpha, zorder=z - 0.01,
                                      solid_capstyle="round", solid_joinstyle="round"))
            line = _line(ax, ox, oy, color=col, lw=width, linestyle=ls, alpha=alpha, zorder=z, solid_capstyle="round",
                         solid_joinstyle="round", dash_capstyle="butt")
            arts.append(line)
            if k > 1:
                interleaved.append(line)
        if interleaved:
            ctx.shared_lines.append(interleaved)
        if stops and lead.stops:
            col = style.algorithm_color(lead.algorithm) if k == 1 else style.INK
            marks = _points(ax, [ox[s.position] for s in lead.stops], [oy[s.position] for s in lead.stops],
                            s=(3.0 + width) ** 2, marker="o", color=col, edgecolor="white", linewidth=0.9,
                            zorder=z + 0.3, alpha=alpha)
            if marks is not None:
                arts.append(marks)
                ctx.stop_marks.append((k > 1, marks))
        if not lead.feasible and lead.stranded_at_m is not None:
            # where the battery would be empty
            dist = lead.node_dist_m
            q = 0
            while q + 1 < len(dist) and dist[q + 1] < lead.stranded_at_m:
                q += 1
            if q + 1 < len(ox):
                seg = dist[q + 1] - dist[q]
                f = (lead.stranded_at_m - dist[q]) / seg if seg > 0 else 0.0
                xx = ox[q] + f * (ox[q + 1] - ox[q])
                yy = oy[q] + f * (oy[q + 1] - oy[q])
                col = style.algorithm_color(lead.algorithm) if k == 1 else style.INK
                arts.append(_points(ax, [xx], [yy], s=46, marker="X", color=col, edgecolor="white", linewidth=0.8,
                                    zorder=z + 0.4))
        for i in members:
            ctx.route_artists[i] = arts
    return groups


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
        for gx in range(math.floor(b[0] / c), math.floor(b[2] / c) + 1):
            for gy in range(math.floor(b[1] / c), math.floor(b[3] / c) + 1):
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
#: Last resort for places next to a route bundle: clear of the lanes.
_FAR_CANDIDATES: tuple[tuple[float, float, str, str], ...] = (
    (0, 10, "center", "bottom"),
    (0, -10, "center", "top"),
    (8, 8, "left", "bottom"),
    (-8, 8, "right", "bottom"),
    (8, -8, "left", "top"),
    (-8, -8, "right", "top"),
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
    occ_add = ctx.occupied.add
    lane_add = ctx.lane_index.add

    def add(b: tuple[float, float, float, float]) -> None:
        occ_add(b)
        lane_add(b)

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
    _segments(ax, [((bx, bx + length), (by, by)), ((bx, bx), (by - tick, by + tick)),
                   ((bx + length, bx + length), (by - tick, by + tick))],
              color=style.INK, lw=0.9, zorder=8, solid_capstyle="butt", path_effects=style.halo(2.2, "white", 0.85))
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
    share_lanes: bool = True,
) -> MapContext:
    """Draw every map layer into ``ax`` (used by :func:`plot_map`; reusable for
    custom map panels).

    ``avoid`` lists extra display-space boxes (e.g. inset axes) that labels
    and the legend must not cover.  Returns the :class:`MapContext` with
    the lane polylines of the routes.  The legend is made of the drawn
    artists (stations, stops, origin, destination), the plans' legend keys
    and, for a shared lane, its interleaved lines.
    """
    m_per_pt = _fix_axes(ax, extent)
    ctx = MapContext(ax=ax, extent=extent, m_per_pt=m_per_pt)
    for b in avoid:
        ctx.occupied.add(tuple(b))  # type: ignore[arg-type]
    fig = ax.figure
    fs = label_size if label_size is not None else max(5.5, float(_fs()) - 1.5)
    if terrain:
        grids = _terrain_grids(scenario, extent, resolution or 640)
        if grids is not None:
            _draw_terrain(ax, grids)
    if roads:
        _draw_roads(ax, scenario, extent)
    if stations:
        ctx.keys.update(_draw_stations(ax, scenario, extent))
    plans = list(plans)
    _draw_routes(ctx, scenario, plans, highlight=highlight, lw=route_lw, stops=stops, faded=faded_routes,
                 share_lanes=share_lanes)

    net = scenario.network
    o, d = scenario.query.origin, scenario.query.destination
    ctx.keys["origin"] = _points(ax, [net.x[o]], [net.y[o]], s=46, marker="o", color="white", edgecolor=style.INK,
                                 linewidth=1.5, zorder=9)
    ctx.keys["destination"] = _points(ax, [net.x[d]], [net.y[d]], s=46, marker="o", color=style.INK, edgecolor="white",
                                      linewidth=1.2, zorder=9)

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
    texts: list[str] = []

    def entry(handle: Any, text: str) -> None:
        if handle is not None:
            handles.append(handle)
            texts.append(text)

    if legend:
        for i, p in enumerate(plans):
            entry(ctx.legend_keys.get(i), _plan_label(p, with_time=legend_time))
        for key in ("DC charging site", "AC charging site"):
            entry(ctx.keys.get(key), key)
        if ctx.stop_marks:  # a neutral (shared-lane) stop marker if there is one
            entry(next((m for neutral, m in ctx.stop_marks if neutral), ctx.stop_marks[0][1]), "charging stop")
        if ctx.shared_lines:
            entry(tuple(ctx.shared_lines[0]), "shared route (colours alternate)")
        entry(ctx.keys.get("origin"), "origin")
        entry(ctx.keys.get("destination"), "destination")
    legend_corner = None
    if handles:
        axbb = ax.get_window_extent()
        elongated = axbb.height < 0.42 * axbb.width
        leg = None
        if legend not in ("outside",) and not elongated:
            legend_corner = corners[0]
            ncol = 2 if len(handles) > 9 else 1
            leg = ax.legend(handles, texts, loc=legend_corner, frameon=True, fancybox=False, framealpha=0.92,
                            edgecolor="#BFBFBF", fontsize=fs + 0.5, handlelength=2.0, borderpad=0.5, ncol=ncol,
                            columnspacing=1.0, scatterpoints=1)
            leg.get_frame().set_linewidth(0.5)
            bb = leg.get_window_extent(renderer)
            if legend == "auto" and (len(handles) > 10 or bb.width * bb.height > 0.12 * axbb.width * axbb.height):
                leg.remove()  # too large for an inset: put it below the map
                leg = None
                legend_corner = None
            else:
                ctx.occupied.add((bb.x0 - 3, bb.y0 - 3, bb.x1 + 3, bb.y1 + 3))
        if leg is None:
            ncol = max(1, min(len(handles), int(axbb.width / (115.0 * fig.dpi / 72.0)) or 1))
            leg = ax.legend(handles, texts, loc="upper center", bbox_to_anchor=(0.5, -0.012), ncol=ncol,
                            frameon=False, fontsize=fs + 0.5, handlelength=2.0, columnspacing=1.4, scatterpoints=1)
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
        annotated: set[int] = set()
        if ann and plans:
            k = highlight if highlight is not None else 0
            p = plans[k]
            ox, oy = ctx.lanes[k]
            for s in p.stops:
                text = f"{s.station_name or 'stop'}\n{100 * s.soc_in:.0f}→{100 * s.soc_out:.0f} % · {format_duration(s.charge_s)}"
                _place_label(ctx, renderer, ox[s.position], oy[s.position], text, fontsize=fs, color=style.INK, force=True,
                             candidates=_WIDE_CANDIDATES)
                annotated.add(s.node)
        placed = 0
        dots: dict[str, tuple[list[float], list[float]]] = {}
        for v, name, kind in places:
            if placed >= max_labels:
                break
            if v in (o, d) or v in annotated:  # named already (stop annotation)
                continue
            # a place under the route bundle may be labelled clear of it
            px_, py_ = ax.transData.transform((net.x[v], net.y[v]))
            on_route = ctx.lane_index.hits((px_ - 1.0, py_ - 1.0, px_ + 1.0, py_ + 1.0))
            t = _place_label(ctx, renderer, net.x[v], net.y[v], _short_name(name), fontsize=fs if kind == "city" else fs - 0.5,
                             color="#3A3A3A" if kind in ("city", "town") else style.INK_MUTED,
                             fontstyle="italic" if kind in ("pass", "landmark") else "normal",
                             candidates=(_CANDIDATES + _FAR_CANDIDATES) if on_route else _CANDIDATES)
            if t is not None:
                placed += 1
                dx_, dy_ = dots.setdefault("pass" if kind == "pass" else "city", ([], []))
                dx_.append(net.x[v])
                dy_.append(net.y[v])
        if "city" in dots:
            _points(ax, *dots["city"], s=5, color="#3A3A3A", linewidth=0, zorder=3.5)
        if "pass" in dots:
            _points(ax, *dots["pass"], s=12, marker="^", color="#6B5B4B", linewidth=0, zorder=3.5)
    return ctx


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
    legend: bool | str = "auto",
    scalebar: bool = True,
    annotate_stops: bool | None = None,
    max_labels: int = 24,
    share_lanes: bool = True,
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

    Plans that drive the same edges *and* stop at the same nodes share one
    lane whose colours alternate along the route (``share_lanes=False``
    gives every plan its own lane).  ``highlight`` (algorithm name, plan
    index or plan) draws one plan in its own, wider lane on top and
    annotates its stops (station, SoC in -> out, charging time); with a
    single plan the stops are annotated too.  ``extent`` is ``"auto"``
    (fit the routes; a double-column map is kept landscape unless the
    instance itself is elongated), ``"network"`` or ``(x0, x1, y0, y1)``
    in metres.  ``legend`` is ``"auto"`` (inside the emptiest corner if it
    is small -- at most 10 entries covering at most 12 % of the map --,
    otherwise below the map), ``"outside"`` (below), ``True`` (inside,
    except on a thin strip map such as a corridor) or ``False``.
    """
    plans = _routed(_as_plans(plans))
    with style.paper():
        w_in = style.figsize(width)[0]
        # landscape for a double-column map, but elongated instances (a
        # corridor) keep their shape instead of being padded with empty land
        aspect_range = (0.2, 0.8) if w_in > 5.0 else (0.45, 1.25)
        ext = map_extent(scenario, plans, extent, aspect_range=aspect_range)
        if ax is None:
            aspect = (ext[3] - ext[2]) / (ext[1] - ext[0])
            fig, ax = style.subplots(figsize=(w_in, w_in * aspect))
            ax.set_position((0.005, 0.005, 0.99, 0.99))
        fig = ax.figure
        hi = _resolve_highlight(plans, highlight)
        draw_map(
            ax, scenario, plans, extent=ext, terrain=terrain, roads=roads, stations=stations, labels=labels,
            highlight=hi, annotate_stops=annotate_stops, max_labels=max_labels, scalebar=scalebar, legend=legend,
            share_lanes=share_lanes,
        )
        if title:
            ax.set_title(title, loc="left")
        return _finish(fig, path)


# ============================================================== SoC profile
def _interleave_groups(
    series: list[tuple[list[float], list[float]]], xtol: float = 1e-6, ytol: float = 1e-6
) -> list[list[int]]:
    """Group series that coincide within ``(xtol, ytol)`` point by point
    (same number of points required); such series are drawn with
    interleaved dashes so that every colour stays visible."""
    groups: list[list[int]] = []
    for i, (xs, ys) in enumerate(series):
        for g in groups:
            gx, gy = series[g[0]]
            if (
                len(gx) == len(xs)
                and all(abs(a - b) <= xtol for a, b in zip(gx, xs))
                and all(abs(a - b) <= ytol for a, b in zip(gy, ys))
            ):
                g.append(i)
                break
        else:
            groups.append([i])
    return groups


def _common_route(plans: Sequence[RoutePlan]) -> int:
    """Index of the representative plan for "the route": among feasible
    plans, the edge sequence driven by most plans; within it the first plan
    with an optimality guarantee (so the label reads e.g. "BLS route")."""
    cand = [i for i, p in enumerate(plans) if p.feasible] or list(range(len(plans)))
    count: dict[tuple[int, ...], int] = {}
    for i in cand:
        key = tuple(plans[i].edges)
        count[key] = count.get(key, 0) + 1
    best = max(count.values())
    members = [i for i in cand if count[tuple(plans[i].edges)] == best]
    return next((i for i in members if plans[i].algorithm not in style.HEURISTICS), members[0])


def _elevation_series(plan: RoutePlan, net: Any, x: str) -> tuple[list[float], list[float]]:
    ex: list[float] = []
    ez: list[float] = []
    for i, v in enumerate(plan.nodes):
        if x == "distance":
            ex.append(plan.node_dist_m[i] / 1000.0)
            ez.append(net.elevation[v])
        else:
            ex += [plan.node_arrive_s[i] / 3600.0, plan.node_depart_s[i] / 3600.0]
            ez += [net.elevation[v], net.elevation[v]]
    return ex, ez


def _ground(ax: Any, xs: Sequence[float], ys: Sequence[float], *, fill: str, edge: str, lw: float = 0.6,
            zorder: float = 1.0) -> None:
    """Elevation silhouette with :func:`seaborn.lineplot`: the line is the
    maximum of the two values at each x (the elevation and 0) and the band
    their full range (``errorbar=("pi", 100)``), i.e. from the ground up."""
    n = len(xs)
    if not n:
        return
    _sns().lineplot(x=list(xs) * 2, y=list(ys) + [0.0] * n, estimator="max", errorbar=("pi", 100), sort=True,
                    color=edge, lw=lw, zorder=zorder + 0.5, legend=False, ax=ax,
                    err_kws={"facecolor": fill, "alpha": 1.0, "linewidth": 0.0, "zorder": zorder})


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
    flat, charging follows the exact charging function, see
    :func:`soc_series`).  With a scenario the figure also shows the driving
    reserve (tinted band below the dashed line) and the elevation profile
    of the ``reference`` plan's route (default: the first feasible plan) as
    a grey silhouette -- on a secondary axis (``elevation="twin"``), in a
    small panel below (``"panel"``) or not at all (``None``).

    Plans whose profiles coincide visually (within 0.4 % of the x range
    and 0.8 % SoC at every point) are drawn with interleaved dashes, so
    every colour stays visible; heuristics are dashed when drawn alone.  An
    infeasible plan is drawn until its battery would be empty, where a
    cross marks the stranding point.  The legend sits above the axes.
    """
    plans = _routed(_as_plans(plans))
    P = _prepared(scenario)
    with style.paper():
        sns = _sns()
        ax_elev = None
        if ax is None:
            w, h = style.figsize(width, aspect)
            if elevation == "panel" and P is not None:
                fig, (ax, ax_elev) = style.subplots(2, 1, figsize=(w, h * 1.3), sharex=True, height_ratios=[3.0, 1.0],
                                                    hspace=0.08)
            else:
                fig, ax = style.subplots(figsize=(w, h))
        fig = ax.figure
        fs_small = _fs() - 1.5
        series = [soc_series(p, P, x) for p in plans]
        x_max = max((max(xs) for xs, _ in series if xs), default=1.0)
        groups = _interleave_groups(series, xtol=0.004 * x_max, ytol=0.8)
        lw = 1.3
        # every plan in its colour, dashed if it is a heuristic: one seaborn
        # call, whose legend keys become the legend above the axes
        labels = [_plan_label(p, with_time=False) for p in plans]
        palette = {lab: style.algorithm_color(p.algorithm) for lab, p in zip(labels, plans)}
        dashes = {lab: ("" if style.algorithm_linestyle(p.algorithm) == "-" else style.algorithm_linestyle(p.algorithm)[1])
                  for lab, p in zip(labels, plans)}
        X: list[float] = []
        Y: list[float] = []
        H: list[str] = []
        U: list[int] = []
        for i, (lab, (xs, ys)) in enumerate(zip(labels, series)):
            X += xs
            Y += ys
            H += [lab] * len(xs)
            U += [i] * len(xs)
        order = list(dict.fromkeys(labels))
        if X:
            sns.lineplot(x=X, y=Y, hue=H, style=H, units=U, hue_order=order, style_order=order, palette=palette,
                         dashes=dashes, estimator=None, sort=False, lw=lw, legend="full" if legend else False, ax=ax,
                         zorder=3, solid_capstyle="butt", dash_capstyle="butt", solid_joinstyle="miter")
        # profiles that coincide: interleaved dashes on top, one colour after the other
        for g in groups:
            k = len(g)
            if k < 2:
                continue
            for j, i in enumerate(g):
                xs, ys = series[i]
                _line(ax, xs, ys, color=style.algorithm_color(plans[i].algorithm), lw=lw,
                      linestyle=_interleaved_dashes(k, j, 3.5, lw), zorder=3.1 + 0.01 * i, solid_capstyle="butt",
                      dash_capstyle="butt", solid_joinstyle="miter")
        # where infeasible plans run out of energy
        noted = False
        for i, p in enumerate(plans):
            xs, ys = series[i]
            if p.feasible or p.stranded_at_m is None or not xs:
                continue
            _points(ax, [xs[-1]], [ys[-1]], s=30, marker="X", color=style.algorithm_color(p.algorithm),
                    edgecolor="white", linewidth=0.6, zorder=6, clip_on=False)
            if not noted:
                ax.annotate(f"{_title(p)}: battery empty", (xs[-1], ys[-1]), xytext=(5, 3), textcoords="offset points",
                            ha="left", va="bottom", fontsize=fs_small, color=style.INK_MUTED,
                            path_effects=style.halo(2.0, "white", 0.9), zorder=7)
                noted = True
        y_min = min((min(ys) for _, ys in series if ys), default=0.0)
        ax.set_ylim(min(-2.0, y_min - 2.0), 103.0)
        ax.set_xlim(0.0, x_max * 1.005)
        if P is not None:
            pref = _scenario(scenario).preferences  # type: ignore[arg-type]
            res = 100.0 * pref.reserve_soc
            ax.axhspan(ax.get_ylim()[0], res, facecolor="#F1DDD4", alpha=0.65, edgecolor="none", zorder=0.6)
            ax.axhline(res, color="#B5654A", lw=0.7, ls=(0, (3, 2)), zorder=1)
            ax.annotate(f"reserve {res:.0f} %", (0.0, res), xycoords=("axes fraction", "data"), xytext=(3, -1.5),
                        textcoords="offset points", ha="left", va="top", fontsize=fs_small, color="#9A4F37", zorder=2)
        ax.set_ylabel("State of charge [%]")
        ax.set_xlabel("" if ax_elev is not None else ("Distance [km]" if x == "distance" else "Trip time [h]"))
        ax.set_yticks(range(0, 101, 20))
        ax.grid(axis="y", color=style.GRID, lw=0.5, zorder=0)

        # ---------------------------------------------------- elevation profile
        if P is not None and elevation and plans:
            ref_i = _resolve_highlight(plans, reference)
            if ref_i is None:
                ref_i = _common_route(plans)
            rp = plans[ref_i]
            ex, ez = _elevation_series(rp, P.net, x)
            distinct_routes = len({tuple(p.edges) for p in plans}) > 1
            if elevation == "panel" and ax_elev is not None:
                _ground(ax_elev, ex, ez, fill="#E0E0E0", edge="#8C8C8C")
                top = max(ez) * 1.15 + 1
                ax_elev.set_ylim(0, top)
                ax_elev.set_ylabel("Elev. [m]")
                ax_elev.set_xlabel("Distance [km]" if x == "distance" else "Trip time [h]", visible=True)
                ax_elev.grid(axis="y", color=style.GRID, lw=0.5)
                ax_elev.set_yticks(style.nice_ticks(0, top, 3))
                if distinct_routes:
                    ax_elev.annotate(f"{_title(rp)} route", (0.0, 1.0), xycoords="axes fraction", xytext=(3, -2),
                                     textcoords="offset points", ha="left", va="top", fontsize=fs_small,
                                     color=style.INK_MUTED)
            elif elevation == "twin":
                ax2 = ax.twinx()
                _ground(ax2, ex, ez, fill="#E4E4E4", edge="#BDBDBD")
                top = max(max(ez) * 2.6, 1.0)
                ax2.set_ylim(0, top)
                ax2.set_ylabel("Elevation [m]" if not distinct_routes else f"Elevation, {_title(rp)} route [m]",
                               color="#8C8C8C", visible=True)
                ax2.tick_params(axis="y", colors="#B3B3B3", labelcolor="#8C8C8C")
                ax2.set_yticks(style.nice_ticks(0, top, 4))
                ax2.spines["right"].set_visible(True)
                ax2.spines["right"].set_color("#BFBFBF")
                ax2.spines["top"].set_visible(False)
                ax2.grid(False)
                ax.set_zorder(ax2.get_zorder() + 1)
                ax.patch.set_visible(False)
        if legend and ax.get_legend() is not None:
            w_in = fig.get_size_inches()[0]
            ncol = max(1, min(len(order), int(w_in / 1.15)))
            sns.move_legend(ax, "lower left", bbox_to_anchor=(0.0, 1.01, 1.0, 0.1), mode="expand", ncol=ncol,
                            borderaxespad=0.0, frameon=False, handlelength=2.0, columnspacing=0.8, title=None)
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
    The stack is drawn with :func:`seaborn.barplot`, cumulative sums from
    the longest down, so each shorter bar covers the start of the longer.
    """
    plans = _as_plans(plans)
    if not include_infeasible:
        plans = [p for p in plans if p.feasible]
    with style.paper():
        sns = _sns()
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
        rows = [str(i) for i in range(len(plans))]  # one category per plan, top to bottom
        feas = [(r, p) for r, p in zip(rows, plans) if p.feasible]
        tmax = max((p.total_time_s for p in plans if p.feasible), default=1.0) / 3600.0
        ends: list[list[float]] = []
        for _, p in feas:
            acc, cum = 0.0, []
            for _, _, attr in comps:
                acc += getattr(p, attr) / 3600.0
                cum.append(acc)
            ends.append(cum)
        for c in reversed(range(len(comps))):
            key, label, _ = comps[c]
            if feas:
                sns.barplot(x=[e[c] for e in ends], y=[r for r, _ in feas], order=rows, orient="y", errorbar=None,
                            color=style.TIME_COMPONENT_COLORS[key], saturation=1.0, width=0.62, edgecolor="white",
                            linewidth=0.7, label=label, zorder=2, ax=ax)
        for y, p in enumerate(plans):
            if not p.feasible:
                ax.text(0.0, y, "  infeasible", va="center", ha="left", fontsize=_fs() - 1, color=style.INK_MUTED)
                continue
            ax.text(p.total_time_s / 3600.0 + 0.012 * tmax, y, format_duration(p.total_time_s), va="center", ha="left",
                    fontsize=_fs() - 1, color=style.INK)
        ax.set_yticks(range(len(plans)), [_title(p) for p in plans])
        ax.tick_params(axis="y", length=0)
        ax.set_xlim(0, tmax * 1.2)
        ax.set_ylim(n - 0.4, -0.6)  # first plan on top
        ax.set_xlabel("Trip time [h]")
        ax.set_ylabel("")
        ax.spines["left"].set_visible(False)
        ax.grid(axis="x", color=style.GRID, lw=0.5)
        bars = dict(zip(*reversed(ax.get_legend_handles_labels())))
        handles = [bars[lab] for _, lab, _ in comps if lab in bars]
        if handles:
            ax.legend(handles, [lab for _, lab, _ in comps if lab in bars], loc="lower left", bbox_to_anchor=(0.0, 1.0),
                      ncol=4, frameon=False, handlelength=1.0, handleheight=0.8, columnspacing=0.9, borderaxespad=0.2)
        return _finish(fig, path)


# ======================================================== comparison table
_COLUMNS: dict[str, tuple[str, str]] = {
    # key: (header, better)
    "algorithm": ("Algorithm", ""),
    "guarantee": ("Guarantee", ""),
    "time": ("Trip time [min]", "min"),
    "drive": ("Driving [h:mm]", ""),
    "charge": ("Charging [min]", ""),
    "wait": ("Queue + plug-in [min]", ""),
    "stops": ("Stops", ""),
    "distance": ("Distance [km]", ""),
    "charged": ("Charged [kWh]", ""),
    "money": ("Cost [{cur}]", "min"),
    "gcost": ("Gen. cost [{cur}]", "min"),
    "arrival": ("Arrival SoC [%]", ""),
    "runtime": ("Runtime [ms]", "min"),
    "settled": ("Settled [labels]", "min"),
}
DEFAULT_COLUMNS: tuple[str, ...] = (
    "algorithm", "guarantee", "time", "charge", "wait", "stops", "distance", "charged", "money", "arrival", "runtime", "settled",
)


def _cell(p: RoutePlan, key: str) -> tuple[str, float | None]:
    """(formatted text, numeric value for 'best' detection) of one cell.

    The value is rounded like the text, so that plans that *look* equal in
    the table are marked best together."""
    feas = p.feasible
    stats = p.stats
    if key == "algorithm":
        return _title(p), None
    if key == "guarantee":
        return _guarantee(p.algorithm) or "--", None
    if not feas and key not in ("runtime", "settled"):
        return ("infeasible", None) if key == "time" else ("--", None)
    if key == "time":
        return f"{p.total_time_s / 60:.1f}", round(p.total_time_s / 60, 1)
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
        return f"{p.money:.2f}", round(p.money, 2)
    if key == "gcost":
        return f"{p.generalized_cost:.2f}", round(p.generalized_cost, 2)
    if key == "arrival":
        return f"{100 * p.arrival_soc:.1f}", p.arrival_soc
    if key == "runtime":
        if stats is None:
            return "--", None
        return f"{1000 * stats.runtime_s:.1f}", round(1000 * stats.runtime_s, 1)
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
    zebra: bool = True,
    path: Any = None,
) -> Any:
    """The key numbers of every plan as a booktabs-style figure table.

    Columns are chosen from ``algorithm, guarantee, time, drive, charge,
    wait, stops, distance, charged, money, gcost, arrival, runtime,
    settled``.  A colour swatch identifies the algorithm; the best trip
    time, cost, runtime and settled count among feasible plans are bold;
    infeasible rows are muted.  The table is laid out from measured text
    extents (two-line headers: name over unit) and spans the requested
    width; if it does not fit, the font shrinks (down to 5.5 pt).  See
    :func:`latex_table` for the LaTeX version of the same numbers.  The
    axes measure typographic points, so the rules, the zebra rows (lines
    one row high) and the swatches are seaborn lines and square markers.
    """
    plans = _as_plans(plans)
    headers, rows, best = _table_data(plans, columns)
    heads = [h.replace(" [", "\n[") for h in headers]
    left = [c in ("algorithm", "guarantee") for c in columns]
    with style.paper():
        w_in = style.figsize(width)[0]
        fig, ax = style.subplots(figsize=(w_in, 1.0))
        renderer = fig.canvas.get_renderer()
        k_pt = 72.0 / fig.dpi

        def text_w(s: str, size: float, weight: str = "normal") -> float:
            t = fig.text(0, 0, s, fontsize=size, fontweight=weight, linespacing=1.15)
            w = t.get_window_extent(renderer).width * k_pt
            t.remove()
            return w

        fs = float(_fs()) - 0.5
        avail = w_in * 72.0 - 4.0
        swatch = 1.0  # in units of fs
        for _ in range(6):
            pad = 0.9 * fs
            col_w = []
            for j in range(len(columns)):
                cw = text_w(heads[j], fs, "bold")
                for r, row in enumerate(rows):
                    cw = max(cw, text_w(row[j], fs, "bold" if best[r][j] else "normal"))
                col_w.append(cw)
            natural = swatch * fs + 0.6 * fs + sum(col_w) + pad * (len(columns) - 1)
            if natural <= avail or fs <= 5.5:
                break
            fs = max(5.5, fs * avail / natural * 0.98)
        extra = max(avail - natural, 0.0) / max(len(columns) - 1, 1)
        gap = pad + extra
        row_h = 1.55 * fs
        head_h = 2 * 1.2 * fs + 0.6 * fs
        total_h = head_h + row_h * len(rows) + 0.5 * fs
        fig.set_size_inches(w_in, (total_h + 4.0) / 72.0)
        ax.set_position((0.0, 0.0, 1.0, 1.0))
        ax.set_xlim(-2.0, w_in * 72.0 - 2.0)
        ax.set_ylim(total_h + 2.0, -2.0)
        ax.axis("off")
        # column anchors (left edge for text columns, right edge for numbers)
        x = swatch * fs + 0.6 * fs
        anchors = []
        for j in range(len(columns)):
            anchors.append(x if left[j] else x + col_w[j])
            x += col_w[j] + gap
        x_end = x - gap
        rule = {"color": style.INK, "solid_capstyle": "butt"}
        _segments(ax, [((0, x_end), (0, 0)), ((0, x_end), (total_h, total_h))], lw=0.9, **rule)
        _line(ax, [0, x_end], [head_h, head_h], lw=0.5, **rule)
        for j, h in enumerate(heads):
            ax.text(anchors[j], head_h - 0.35 * fs, h, ha="left" if left[j] else "right", va="bottom", fontsize=fs,
                    fontweight="bold", color=style.INK, linespacing=1.15)
        centres = [head_h + r * row_h + 0.25 * fs + row_h / 2.0 for r in range(len(rows))]
        if zebra:  # every other row shaded: a line exactly one row high
            _segments(ax, [((0, x_end), (centres[r], centres[r])) for r in range(1, len(rows), 2)], lw=row_h,
                      color="#F4F4F4", solid_capstyle="butt", zorder=0)
        side = 0.8 * fs
        _points(ax, [0.1 * fs + side / 2.0] * len(plans), centres, hue=[str(r) for r in range(len(plans))],
                palette={str(r): style.algorithm_color(p.algorithm) for r, p in enumerate(plans)}, s=side * side,
                marker="s", linewidth=0, edgecolor="none", zorder=2)
        for r, (p, row) in enumerate(zip(plans, rows)):
            yc = centres[r]
            muted = not p.feasible
            for j, t in enumerate(row):
                ax.text(anchors[j], yc, t, ha="left" if left[j] else "right", va="center_baseline", fontsize=fs,
                        fontweight="bold" if (bold_best and best[r][j]) else "normal",
                        color=style.INK_MUTED if (muted and columns[j] != "algorithm") else style.INK, zorder=3)
        return _finish(fig, path)


# =================================================================== Pareto
def _wear_scale(wmax: float) -> tuple[float, str]:
    """Factor and unit label that show wear values with 1-3 integer digits."""
    if wmax <= 0:
        return 1.0, "[%]"
    exp = math.floor(math.log10(wmax))
    if -1 <= exp <= 1:
        return 1.0, "[%]"
    return 10.0 ** (-exp), f"[$10^{{{exp}}}$ %]"


def plot_pareto(
    plan: RoutePlan | Sequence[RoutePlan],
    scenario: Scenario | None = None,
    *,
    others: Any = (),
    ax: Any = None,
    width: str | float = "single",
    aspect: float = 0.78,
    path: Any = None,
) -> Any:
    """A Pareto front in the (trip time, money) plane, wear as colour.

    ``plan`` is the result of the ``pareto`` planner (its ``alternatives``
    are the evaluated front members) or a list of member plans.  Marker
    colour encodes the battery wear (state-of-health loss, one-hue ramp),
    marker area the number of charging stops; the thin dark staircase is
    the two-criteria (time, money) envelope -- members above it are
    non-dominated only thanks to lower wear.  The member the planner
    selected (minimum generalised cost) is ringed.  ``others`` adds plans
    of other algorithms as reference points in their own colour and marker.
    The scatter is drawn with :func:`seaborn.scatterplot` (its size legend
    gives the stop keys), the staircase with :func:`seaborn.lineplot` and
    the colour scale with :func:`evroute.viz.style.colorbar`.  A front cut
    short by the planner's label budget (``stats.extra["complete"] is
    False``) is flagged in the corner.
    """
    if isinstance(plan, RoutePlan):
        members = list(plan.alternatives) or [plan]
        selected: RoutePlan | None = plan
        st = plan.stats
        complete = (st.extra or {}).get("complete", True) if st else True
    else:
        members = list(plan)
        selected = None
        complete = True
    members = [m for m in members if m.feasible]
    if not members:
        raise ValueError("no feasible front members to plot")
    with style.paper():
        sns = _sns()
        import pandas as pd

        if ax is None:
            fig, ax = _new_fig(width, aspect)
        fig = ax.figure
        T = [m.total_time_s / 3600.0 for m in members]
        M = [m.money for m in members]
        W = [m.soh_total_pct for m in members]
        S = [m.num_stops for m in members]
        scale, unit = _wear_scale(max(W))
        Wd = [w * scale for w in W]
        big = len(members) > 150
        # staircase of the non-dominated (time, money) envelope
        st_x, st_y = _staircase(list(zip(T, M)))
        _line(ax, st_x, st_y, color=style.INK, lw=0.6, alpha=0.7, zorder=2, solid_joinstyle="miter")
        cmap = style.sequential_cmap()
        w_lo = min(Wd)
        w_hi = max(Wd) if max(Wd) > min(Wd) else min(Wd) + 1e-9
        s_lo, s_hi = (5.0, 26.0) if big else (14.0, 60.0)
        stops_sorted = sorted(set(S))
        smin, smax = stops_sorted[0], stops_sorted[-1]

        def area(s: int) -> float:
            return s_lo if smax == smin else s_lo + (s_hi - s_lo) * (s - smin) / (smax - smin)

        edge = {"edgecolor": "none", "linewidth": 0.0} if big else {"edgecolor": "white", "linewidth": 0.4}
        front = pd.DataFrame({"time": T, "money": M, "wear": Wd, "stops": S})
        n_keys = len(ax.get_legend_handles_labels()[0])
        sns.scatterplot(data=front, x="time", y="money", hue="wear", hue_norm=(w_lo, w_hi), palette=cmap, size="stops",
                        sizes=(area(smin), area(smax)), size_norm=(smin, smax) if smax > smin else None, legend="auto",
                        ax=ax, zorder=3, alpha=0.9 if big else 1.0, **edge)
        # seaborn's legend keys of the size mapping (after its "stops" subtitle) become the stop keys
        keys, texts = ax.get_legend_handles_labels()
        keys, texts = keys[n_keys:], texts[n_keys:]
        first = texts.index("stops") + 1 if "stops" in texts else len(texts)
        size_keys = {int(float(t)): k for k, t in zip(keys[first:], texts[first:])}
        if ax.get_legend() is not None:
            ax.get_legend().remove()
        style.colorbar(ax, cmap, w_lo, w_hi, label=f"Battery wear (SoH loss) {unit}")
        if selected is not None:
            _points(ax, [selected.total_time_s / 3600.0], [selected.money], s=95, color=style.INK, facecolor="none",
                    edgecolor=style.INK, linewidth=0.9, zorder=5)
            ax.annotate("selected", (selected.total_time_s / 3600.0, selected.money), xytext=(8, 8),
                        textcoords="offset points", fontsize=_fs() - 1, color=style.INK,
                        arrowprops={"arrowstyle": "-", "lw": 0.5, "color": style.INK_MUTED, "shrinkA": 0, "shrinkB": 4})
        handles: list[Any] = []
        texts = []
        for o in _as_plans(others):
            if not o.feasible:
                continue
            mark = _points(ax, [o.total_time_s / 3600.0], [o.money], s=36, marker=style.algorithm_marker(o.algorithm),
                           color=style.algorithm_color(o.algorithm), edgecolor="white", linewidth=0.6, zorder=6)
            handles.append(mark)
            texts.append(_title(o))
        # seaborn lists every stop count up to 7 levels and a brief, evenly
        # spaced selection beyond; the keys shown are taken from that list
        listed = sorted(size_keys)
        shown = listed if len(listed) <= 4 else [listed[0], listed[len(listed) // 2], listed[-1]]
        for s in shown:
            key = size_keys[s]
            key.set_markerfacecolor("#A6A6A6")
            key.set_markeredgecolor("white")
            key.set_markeredgewidth(0.4)
            handles.append(key)
            texts.append(f"{s} stop{'s' if s != 1 else ''}")
        ax.legend(handles, texts, loc="lower left", bbox_to_anchor=(0.0, 1.01, 1.0, 0.1), mode="expand",
                  ncol=min(len(handles), 3), frameon=False, handletextpad=0.3, borderaxespad=0.0, columnspacing=0.6)
        if not complete:
            ax.annotate("front may be incomplete (label budget)", (1.0, 0.0), xycoords="axes fraction", xytext=(-3, 3),
                        textcoords="offset points", ha="right", va="bottom", fontsize=_fs() - 1.5,
                        color=style.INK_MUTED)
        ax.margins(x=0.06, y=0.08)
        cur = members[0].currency
        ax.set_xlabel("Trip time [h]")
        ax.set_ylabel(f"Charging cost [{cur}]")
        ax.grid(color=style.GRID, lw=0.5, zorder=0)
        return _finish(fig, path)


def _staircase(points: Sequence[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Step line through the non-dominated points of ``(x, y)`` (both minimised)."""
    xs: list[float] = []
    ys: list[float] = []
    best = math.inf
    for x, y in sorted(points):
        if y < best - 1e-12:
            if xs:
                xs.append(x)
                ys.append(best)
            xs.append(x)
            ys.append(y)
            best = y
    return xs, ys


def plot_tradeoff(
    front: RoutePlan | Sequence[RoutePlan],
    marked: Mapping[str, RoutePlan] | None = None,
    *,
    zoom_min: float | None = 60.0,
    width: str | float = "double",
    aspect: float = 0.4,
    path: Any = None,
) -> Any:
    """The time-money trade-off of one trip: a Pareto front with chosen plans marked.

    ``front`` is the result of the ``pareto`` planner (its ``alternatives``)
    or a list of member plans; ``marked`` maps a label to a plan, e.g.
    ``{"Fastest": ..., "Balanced": ..., "Cheapest": ...}``.  The left panel
    shows every member (trip time vs charging cost) and the non-dominated
    staircase; members with a voluntary hold (waiting for a cheaper price
    period) are hollow.  The marked plans get the categorical colours in
    order, their own marker and a direct label (trip time and money).  With
    ``zoom_min`` a second panel enlarges the members at most that many
    minutes slower than the fastest one, with the extra minutes as x -- the
    steep start of the front, where little time buys much money.  A front
    cut short by the planner's label budget is flagged.
    """
    if isinstance(front, RoutePlan):
        members = list(front.alternatives) or [front]
        st = front.stats
        complete = (st.extra or {}).get("complete", True) if st else True
    else:
        members = list(front)
        complete = True
    members = [m for m in members if m.feasible]
    if not members:
        raise ValueError("no feasible front members to plot")
    marks = [(k, p) for k, p in (marked or {}).items() if p is not None and p.feasible]
    cur = members[0].currency
    t_fast = min(m.total_time_s for m in members + [p for _, p in marks])
    shapes = ("D", "o", "^", "s", "v", "P")
    grey = "#9A9A9A"
    with style.paper():
        if zoom_min:
            fig, axes = style.subplots(1, 2, figsize=style.figsize(width, aspect), width_ratios=[1.25, 1.0])
        else:
            fig, ax0 = _new_fig(width, aspect)
            axes = [ax0]
        fs = _fs()
        mark_keys: dict[str, Any] = {}
        member_keys: dict[str, Any] = {}

        def draw(ax: Any, x_of: Any, sel: list[RoutePlan], x_max: float) -> None:
            sx, sy = _staircase([(x_of(m), m.money) for m in sel])
            _line(ax, sx, sy, color=style.INK, lw=0.6, alpha=0.7, zorder=2, solid_joinstyle="miter")
            size = 9.0 if len(sel) > 120 else 16.0
            plain = [m for m in sel if m.hold_time_s <= 0.0]
            held = [m for m in sel if m.hold_time_s > 0.0]
            dots = _points(ax, [x_of(m) for m in plain], [m.money for m in plain], s=size, color=grey,
                           edgecolor="none", linewidth=0.0, zorder=3)
            rings = _points(ax, [x_of(m) for m in held], [m.money for m in held], s=size + 4, color=style.SURFACE,
                            edgecolor=grey, linewidth=0.8, zorder=3)
            for key, art in (("front member", dots), ("waits for a cheaper price period", rings)):
                if art is not None:
                    member_keys.setdefault(key, art)
            for i, (name, p) in enumerate(marks):
                if x_of(p) <= x_max + 1e-9:
                    art = _points(ax, [x_of(p)], [p.money], s=46, marker=shapes[i % len(shapes)],
                                  color=style.categorical(i), edgecolor=style.SURFACE, linewidth=0.8, zorder=5)
                    mark_keys.setdefault(name, art)
            ax.grid(color=style.GRID, lw=0.5, zorder=0)
            ax.set_ylabel(f"Charging cost [{cur}]")

        def label(ax: Any, x_of: Any, x_max: float) -> None:
            lo, hi = ax.get_xlim()
            shown = [x_of(p) for _, p in marks if x_of(p) <= x_max + 1e-9]
            y0, y1 = ax.get_ylim()  # room above the plot, and below it for labels in the right half
            below = any(x > 0.5 * (lo + hi) for x in shown)
            ax.set_ylim(y0 - (0.24 if below else 0.04) * (y1 - y0), y1 + 0.12 * (y1 - y0))
            ref = marks[0][1]
            for name, p in marks:
                x = x_of(p)
                if x > x_max + 1e-9:
                    continue
                text = f"{name}\n{format_duration(p.total_time_s)} · {p.money:,.2f} {cur}"
                if p is not ref:  # the price of the trade against the first marked plan
                    dm = p.money - ref.money
                    text += (f"\n+{format_duration(p.total_time_s - ref.total_time_s)},"
                             f" {'−' if dm < 0 else '+'}{abs(dm):,.2f} {cur}")
                right = x <= 0.5 * (lo + hi)  # left half: label to the right and above, else left and below
                ax.annotate(text, (x, p.money), xytext=(9, 4) if right else (-9, -10), textcoords="offset points",
                            ha="left" if right else "right", va="bottom" if right else "top", fontsize=fs - 0.5,
                            color=style.INK, linespacing=1.15, zorder=6, path_effects=style.halo(2.5))

        def hours(m: RoutePlan) -> float:
            return m.total_time_s / 3600.0

        def extra(m: RoutePlan) -> float:
            return (m.total_time_s - t_fast) / 60.0

        ax = axes[0]
        draw(ax, hours, members, math.inf)
        ax.margins(x=0.08, y=0.12)
        label(ax, hours, math.inf)
        ax.set_xlabel("Trip time [h]")
        ax.set_title(f"{len(members)} Pareto-optimal plans", fontsize=fs)
        if not complete:
            ax.annotate("front may be incomplete (label budget)", (1.0, 1.0), xycoords="axes fraction",
                        xytext=(-3, -3), textcoords="offset points", ha="right", va="top", fontsize=fs - 1.5,
                        color=style.INK_MUTED)
        if zoom_min:
            span = 60.0 * zoom_min
            near = [m for m in members if m.total_time_s <= t_fast + span + 1e-6]
            lo = min(m.money for m in near + [p for _, p in marks if p.total_time_s <= t_fast + span + 1e-6])
            hi = max(m.money for m in near + [p for _, p in marks if p.total_time_s <= t_fast + span + 1e-6])
            pad = 0.05 * (hi - lo or 1.0)
            # the zoomed window, outlined in the full view
            bx0, bx1 = t_fast / 3600.0, (t_fast + span) / 3600.0
            by0, by1 = lo - pad, hi + pad
            _line(ax, [bx0, bx1, bx1, bx0, bx0], [by0, by0, by1, by1, by0], lw=0.6, linestyle=(0, (2, 1.5)),
                  color=style.INK_MUTED, zorder=1, solid_joinstyle="miter")
            az = axes[1]
            draw(az, extra, near, zoom_min)
            az.margins(x=0.06, y=0.1)
            label(az, extra, zoom_min)
            az.set_xlabel("Extra time over the fastest plan [min]")
            az.set_title(f"Zoom: at most {zoom_min:g} min slower ({len(near)} plans)", fontsize=fs)
        names = [name for name, _ in marks if name in mark_keys]
        handles = [mark_keys[n] for n in names] + list(member_keys.values())
        fig.tight_layout(w_pad=1.5)
        fig.legend(handles, names + list(member_keys), loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=len(handles),
                   frameon=False, handletextpad=0.3, columnspacing=1.2, borderaxespad=0.0, scatterpoints=1,
                   markerscale=0.85)
        return _finish(fig, path)


# ========================================================= charging curves
def _vehicles(vehicles: Any) -> list[Any]:
    from ..vehicle import VEHICLES, Vehicle, get_vehicle

    if vehicles is None:
        return list(VEHICLES.values())
    if isinstance(vehicles, (Vehicle, str)):
        vehicles = [vehicles]
    return [get_vehicle(v) if isinstance(v, str) else v for v in vehicles]


def _soc_vs_minutes(vehicle: Any, powers: Sequence[float], soc_from: float, soc_to: float) -> tuple[list[float], list[float], float | None]:
    """SoC [%] vs minutes for piecewise-constant battery power (exact:
    each bin is crossed in ``dSoC * battery / P`` hours); also the minutes
    from ``soc_from`` to ``soc_to``."""
    breaks = list(vehicle.dc_curve.soc_breaks)
    ts, ss = [0.0], [100.0 * soc_from]
    t = 0.0
    t_target = None
    for b0, b1, p in zip(breaks, breaks[1:], powers):
        lo, hi = max(b0, soc_from), b1
        if hi <= lo:
            continue
        if t_target is None and lo <= soc_to <= hi:
            t_target = t + (soc_to - lo) * vehicle.battery_kwh / p * 60.0
        t += (hi - lo) * vehicle.battery_kwh / p * 60.0
        ts.append(t)
        ss.append(100.0 * hi)
    return ts, ss, t_target


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

    Left: maximum battery-side DC power vs SoC -- the piecewise-constant
    bins the exact algorithms use; the resulting charging function (SoC vs
    time) is piecewise linear and concave, as in Montoya et al. (2017,
    Transp. Res. B) and Baum et al. (2019, Transp. Sci.).  Right: SoC vs minutes at
    the charger, starting from ``soc_from``, at a station that does not
    limit the power (or at ``station_kw``); the dot marks ``soc_to``.  Times
    are exact for the binned curve, ``sum(dSoC * battery / P)``; the shared
    legend below the panels reports the classic ``soc_from`` -> ``soc_to``
    charging time of each vehicle.  Both panels are
    :func:`seaborn.lineplot` (colour = vehicle).
    """
    vehicles = _vehicles(vehicle_or_keys)
    with style.paper():
        sns = _sns()
        w, h = style.figsize(width, 0.34)
        fig, (a1, a2) = style.subplots(1, 2, figsize=(w, max(h, 2.1)), wspace=0.24)
        names: list[str] = []
        palette: dict[str, str] = {}
        steps: dict[str, list[Any]] = {"x": [], "y": [], "hue": []}
        ramps: dict[str, list[Any]] = {"x": [], "y": [], "hue": []}
        dots: dict[str, list[Any]] = {"x": [], "y": [], "hue": []}
        for k, v in enumerate(vehicles):
            breaks = list(v.dc_curve.soc_breaks)
            powers = [min(p, station_kw) if station_kw else p for p in v.dc_curve.power_kw]
            ts, ss, t_target = _soc_vs_minutes(v, powers, soc_from, soc_to)
            name = v.name + (f"  ({t_target:.0f} min)" if t_target is not None else "")
            names.append(name)
            palette[name] = style.categorical(k)
            for b0, b1, p in zip(breaks, breaks[1:], powers):  # the piecewise-constant bins as stairs
                steps["x"] += [100 * b0, 100 * b1]
                steps["y"] += [p, p]
                steps["hue"] += [name, name]
            ramps["x"] += ts
            ramps["y"] += ss
            ramps["hue"] += [name] * len(ts)
            if t_target is not None:
                dots["x"].append(t_target)
                dots["y"].append(100 * soc_to)
                dots["hue"].append(name)
        common = {"hue_order": names, "palette": palette, "estimator": None, "sort": False, "lw": 1.2}
        sns.lineplot(**steps, **common, legend=False, ax=a1)
        sns.lineplot(**ramps, **common, legend=True, ax=a2)
        if dots["x"]:
            sns.scatterplot(**dots, hue_order=names, palette=palette, s=13, edgecolor="white", linewidth=0.5, zorder=4,
                            legend=False, ax=a2)
        handles, labels = a2.get_legend_handles_labels()
        if a2.get_legend() is not None:
            a2.get_legend().remove()
        a1.set_xlim(0, 100)
        a1.set_ylim(0, None)
        a1.set_xlabel("State of charge [%]")
        a1.set_ylabel("Max. charging power [kW]")
        a1.grid(color=style.GRID, lw=0.5)
        a2.axhline(100 * soc_to, color="#B3B3B3", lw=0.6, ls=(0, (3, 2)), zorder=0)
        a2.annotate(f"{100 * soc_to:.0f} %", (1.0, 100 * soc_to), xycoords=("axes fraction", "data"), xytext=(-2, -2),
                    textcoords="offset points", ha="right", va="top", fontsize=_fs() - 1.5,
                    color=style.INK_MUTED)
        a2.set_ylim(100 * soc_from - 2, 102)
        a2.set_xlim(0, None)
        a2.set_xlabel(f"Minutes at the charger (from {100 * soc_from:.0f} % SoC)")
        a2.set_ylabel("State of charge [%]")
        a2.grid(color=style.GRID, lw=0.5)
        ncol = 3 if len(handles) > 4 else max(len(handles), 1)
        fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.03), ncol=ncol, frameon=False,
                   title=f"vehicle (minutes from {100 * soc_from:.0f} to {100 * soc_to:.0f} % SoC)",
                   columnspacing=1.2, handlelength=1.8)
        return _finish(fig, path)


def plot_consumption_vs_speed(
    vehicles: Any = None,
    temperatures: Sequence[float] = (-10.0, 20.0, 35.0),
    *,
    speeds_kmh: Sequence[float] | None = None,
    grade: float = 0.0,
    width: str | float = "double",
    aspect: float = 0.36,
    path: Any = None,
) -> Any:
    """Steady-state consumption [kWh/100 km] vs speed, one panel per temperature.

    Computed with the same physics model the planners use
    (:func:`evroute.energy.consumption_curve`: a straight motorway edge at
    ``grade``).  Colour = vehicle; the panels share the y axis, so the
    effect of temperature reads across (cold weather adds heating load and
    denser air, heat adds cooling load -- most visible at low speed, where
    the auxiliary load is spread over few kilometres).  Speeds above a
    vehicle's top speed are not drawn.  The figure is one
    :func:`seaborn.relplot` (``kind="line"``, one column per temperature).
    """
    from ..energy import consumption_curve
    from ..scenario import Conditions

    vehicles = _vehicles(vehicles)
    temps = list(temperatures) or [20.0]
    speeds = list(speeds_kmh or [float(s) for s in range(30, 165, 5)])
    rows: list[tuple[str, float, float, float]] = []
    for v in vehicles:
        sp = [s for s in speeds if s <= v.max_speed_kmh + 1e-9]
        for T in temps:
            c = consumption_curve(v, Conditions(temperature_c=float(T)), sp, grade=grade)
            rows += [(v.name, float(T), s, e) for s, e in zip(sp, c)]
    palette = {v.name: style.categorical(k) for k, v in enumerate(vehicles)}
    panels = [f"{float(T):g} °C" for T in temps]
    data = {
        "vehicle": [r[0] for r in rows],
        "panel": [f"{r[1]:g} °C" for r in rows],
        "speed": [r[2] for r in rows],
        "consumption": [r[3] for r in rows],
    }
    with style.paper():
        sns = _sns()
        w, h = style.figsize(width, aspect)
        h = max(h, 1.9)
        g = sns.relplot(data=data, x="speed", y="consumption", hue="vehicle", col="panel", kind="line", palette=palette,
                        hue_order=list(palette), col_order=panels, lw=1.2, errorbar=None, sort=True,
                        height=h, aspect=w / len(temps) / h, facet_kws={"sharey": True, "sharex": True, "legend_out": False})
        fig = g.figure
        g.set_titles("{col_name}", loc="left", fontweight="bold")
        g.set_axis_labels("Speed [km/h]", "Consumption [kWh/100 km]")
        for ax in g.axes.flat:
            ax.set_xlim(min(speeds), max(speeds))
            ax.grid(color=style.GRID, lw=0.5)
        g.axes.flat[0].set_ylim(0, None)
        g.tight_layout()
        fig.subplots_adjust(wspace=0.08)
        if g.legend is not None:
            sns.move_legend(g, "upper center", bbox_to_anchor=(0.5, -0.03), ncol=3 if len(palette) > 4 else len(palette),
                            frameon=False, columnspacing=1.2, handlelength=1.8, title=None)
        return _finish(fig, path)


# ============================================================ wait profiles
_ARRIVAL_MARKERS: tuple[str, ...] = ("o", "s", "D", "^", "v", "P", "X", "*", "h", "<", ">")


def plot_wait_profiles(
    scenario: Scenario,
    stations: Sequence[int] | None = None,
    *,
    plans: Any = (),
    top: int = 6,
    width: str | float = "single",
    aspect: float = 0.62,
    path: Any = None,
) -> Any:
    """Expected queueing delay vs time of day at selected stations.

    The curves are the FIFO-safe Erlang-C profiles the planners use
    (``PreparedScenario.wait_profiles``; M/M/c waits per hour, see
    :mod:`evroute.queueing`).  ``stations`` are station indices; by default
    the stations used by ``plans`` are shown, otherwise the ``top`` stations
    with the highest peak wait.  The departure time is marked, and with
    ``plans`` each planned arrival is a marker (the algorithm's shape) on
    its station's curve -- after a voluntary hold, at the time the vehicle
    joins the queue, where its wait is evaluated.  Curves are a
    :func:`seaborn.lineplot`, arrivals a :func:`seaborn.scatterplot`
    (colour = station, shape = algorithms).
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
        sns = _sns()
        fig, ax = _new_fig(width, aspect)
        hours = [i / 12.0 for i in range(24 * 12 + 1)]
        names: list[str] = []
        curves: dict[str, list[Any]] = {"x": [], "y": [], "hue": [], "units": []}
        arrivals_tbl: dict[str, list[Any]] = {"time": [], "wait": [], "station": [], "arrival": []}
        marker_sets: dict[str, str] = {}  # arrival label -> marker
        for k, s in enumerate(stations):
            st = P.stations[s]
            prof = P.wait_profiles[s]
            name = f"{st.name or f'station {st.id}'} ({st.power_kw:g} kW, {st.plugs} plugs)"
            if name in names:
                name += f" #{k + 1}"
            names.append(name)
            curves["x"] += hours
            curves["y"] += [prof(h * 3600.0) / 60.0 for h in hours]
            curves["hue"] += [name] * len(hours)
            curves["units"] += [k] * len(hours)
            # arrivals: plans arriving within the same minute share a marker
            arrivals: dict[int, tuple[float, float, list[str]]] = {}
            for p in plans:
                for stop in p.stops:
                    if stop.station == s:
                        tod = P.time_of_day(stop.join_s) / 3600.0  # the wait applies from here
                        key = round(tod * 60.0)
                        if key not in arrivals:
                            arrivals[key] = (tod, stop.wait_s / 60.0, [])
                        if p.algorithm not in arrivals[key][2]:
                            arrivals[key][2].append(p.algorithm)
            for tod, wait_min, algos in arrivals.values():
                label = ", ".join(style.algorithm_title(a) for a in algos)
                if label not in marker_sets:  # one distinct shape per set of algorithms
                    shape = style.algorithm_marker(algos[0]) if len(algos) == 1 else None
                    if shape is None or shape in marker_sets.values():
                        shape = next((m for m in _ARRIVAL_MARKERS if m not in marker_sets.values()), "o")
                    marker_sets[label] = shape
                arrivals_tbl["time"].append(tod)
                arrivals_tbl["wait"].append(wait_min)
                arrivals_tbl["station"].append(name)
                arrivals_tbl["arrival"].append(label)
        palette = {name: style.categorical(k) for k, name in enumerate(names)}
        handles: list[Any] = []
        texts: list[str] = []
        if names:
            sns.lineplot(**curves, hue_order=names, palette=palette, estimator=None, sort=False, lw=1.2, legend=True,
                         ax=ax)
            handles, texts = ax.get_legend_handles_labels()
        if arrivals_tbl["time"]:
            # colour = station, shape = algorithms; seaborn's style keys become the arrival keys
            sns.scatterplot(data=arrivals_tbl, x="time", y="wait", hue="station", style="arrival", hue_order=names,
                            palette=palette, style_order=list(marker_sets), markers=marker_sets, s=22,
                            edgecolor=style.INK, linewidth=0.5, zorder=4, legend="full", ax=ax)
            keys, labels = ax.get_legend_handles_labels()
            first = labels.index("arrival", len(texts)) + 1 if "arrival" in labels[len(texts):] else len(labels)
            for key, label in zip(keys[first:], labels[first:]):
                key.set_markerfacecolor("white")
                key.set_markeredgecolor(style.INK)
                key.set_markeredgewidth(0.6)
                key.set_markersize(4.2)
                handles.append(key)
                texts.append(f"arrival: {label}")
        dep = (P.departure_s % 86400.0) / 3600.0
        ax.axvline(dep, color=style.INK_MUTED, lw=0.7, ls=(0, (3, 2)), zorder=1)
        ax.annotate("departure", (dep, 1.0), xycoords=("data", "axes fraction"), xytext=(2, -2), textcoords="offset points",
                    ha="left", va="top", fontsize=_fs() - 1.5, color=style.INK_MUTED)
        ax.set_xlim(0, 24)
        ax.set_xticks(range(0, 25, 3))
        ax.set_ylim(0, None)
        ax.set_xlabel("Time of day [h]")
        ax.set_ylabel("Expected wait [min]")
        ax.grid(color=style.GRID, lw=0.5)
        ax.legend(handles, texts, loc="upper left", bbox_to_anchor=(0.0, -0.2), ncol=1, frameon=False,
                  handlelength=1.6, borderaxespad=0.0)
        return _finish(fig, path)


# ============================================================ search effort
def _geomean(values: Iterable[float]) -> float:
    vals = [max(float(v), 1e-12) for v in values]
    return math.exp(sum(math.log(v) for v in vals) / len(vals)) if vals else math.nan


def plot_search_effort(
    plans: Any,
    *,
    width: str | float = "single",
    path: Any = None,
) -> Any:
    """Search effort per algorithm: settled labels and runtime (log scales).

    A Cleveland dot plot (bars would be misleading on a log axis, where
    their length depends on an arbitrary baseline).  ``settled`` is what
    each planner reports in ``stats.settled`` (labels for the label-setting
    methods, decoded individuals for the genetic algorithm, ...); runtime
    is the wall-clock time of ``Planner.plan``.  ``plans`` may contain
    several plans per algorithm (e.g. one per instance): each run is then
    a small jittered dot (:func:`seaborn.stripplot`) and the big marker is
    the geometric mean with its 95 % bootstrap confidence interval
    (:func:`seaborn.pointplot`).
    """
    plans = [p for p in _as_plans(plans) if p.stats is not None]
    if not plans:
        raise ValueError("no plan carries search statistics")
    order: list[str] = []
    for p in plans:
        if p.algorithm not in order:
            order.append(p.algorithm)
    titles = [style.algorithm_title(a) for a in order]
    palette = style.algorithm_palette(order, titles=True)
    data = {
        "algorithm": [_title(p) for p in plans],
        "settled": [max(float(p.stats.settled), 1.0) for p in plans],  # type: ignore[union-attr]
        "runtime": [max(1000.0 * p.stats.runtime_s, 1e-3) for p in plans],  # type: ignore[union-attr]
    }
    repeated = len(plans) > len(order)
    with style.paper():
        sns = _sns()
        n = len(order)
        w = style.figsize(width)[0]
        fig, axes = style.subplots(1, 2, figsize=(w, 0.62 + 0.2 * n), sharey=True, wspace=0.16)
        for ax, key, label in ((axes[0], "settled", "Settled labels"), (axes[1], "runtime", "Runtime [ms]")):
            ax.set_xscale("log")
            vals = data[key]
            centre = {t: _geomean(v for a, v in zip(data["algorithm"], vals) if a == t) for t in titles}
            # guide lines from the axis to the dot
            _segments(ax, [((1e-12, centre[t]), (y, y)) for y, t in enumerate(titles)], color=style.GRID, lw=0.8,
                      zorder=1, solid_capstyle="butt")
            if repeated:
                sns.stripplot(x=vals, y=data["algorithm"], hue=data["algorithm"], order=titles, hue_order=titles,
                              palette=palette, size=2.0, alpha=0.4, jitter=0.2, legend=False, ax=ax, zorder=2,
                              native_scale=True)
                sns.pointplot(x=vals, y=data["algorithm"], hue=data["algorithm"], order=titles, hue_order=titles,
                              palette=palette, estimator=_geomean, errorbar=("ci", 95), seed=0, linestyle="none",
                              markers=[style.algorithm_marker(a) for a in order], markersize=4.0,
                              markeredgecolor="white", markeredgewidth=0.5, err_kws={"linewidth": 1.2},
                              capsize=0.0, legend=False, ax=ax, native_scale=True)
            else:
                for y, (a, t) in enumerate(zip(order, titles)):
                    mk = style.algorithm_marker(a)
                    _points(ax, [centre[t]], [y], s=48 if mk == "*" else 26, marker=mk, color=palette[t],
                            edgecolor="white", linewidth=0.5, zorder=3)
            lo = min(vals)
            hi = max(vals)
            lo_dec = math.floor(math.log10(lo))
            hi_dec = math.log10(hi)
            span = max(hi_dec - lo_dec, 1.0)
            top = hi_dec + 0.62 * span
            ax.set_xlim(10 ** lo_dec, 10 ** top)
            stride = 1 if top - lo_dec <= 4.5 else 2
            decades = list(range(lo_dec, math.floor(top) + 1, stride))
            ax.set_xticks([10.0 ** e for e in decades], [f"$\\mathdefault{{10^{{{e}}}}}$" for e in decades])
            ax.set_xticks([], minor=True)
            for y, t in enumerate(titles):  # value column, right-aligned at the axes edge
                v = centre[t]
                txt = f"{v:,.0f}" if key == "settled" else (f"{v:.1f}" if v < 100 else f"{v:,.0f}")
                ax.annotate(txt, (1.0, y), xycoords=("axes fraction", "data"), xytext=(-1, 0),
                            textcoords="offset points", va="center", ha="right", fontsize=_fs() - 1.5,
                            color=style.INK)
            ax.set_xlabel(label)
            ax.grid(axis="x", color=style.GRID, lw=0.5, which="major")
            ax.spines["left"].set_visible(False)
            ax.tick_params(axis="y", length=0)
            ax.set_ylabel("")
        axes[0].set_yticks(range(n))
        axes[0].set_yticklabels(titles)
        axes[0].set_ylim(n - 0.5, -0.5)
        if repeated:
            runs = len(plans) / max(n, 1)
            axes[1].annotate(f"dots: {runs:.0f} runs per algorithm · marker: geometric mean, 95 % CI", (1.0, 1.0),
                             xycoords="axes fraction", xytext=(0, 4), textcoords="offset points", ha="right",
                             va="bottom", fontsize=_fs() - 1.5, color=style.INK_MUTED, annotation_clip=False)
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
    comparable -- e.g. Dijkstra-order BLS against goal-directed BLS-A*
    (Hart, Nilsson & Raphael 1968).  Each panel reports the settled nodes
    (share of the network) and the settled labels (``stats.settled``).
    Four panels are laid out 2 x 2, otherwise up to three per row.  Nodes
    are a :func:`seaborn.scatterplot` with the settle order as hue.
    """
    plans = [p for p in _as_plans(plans) if p.trace is not None and p.trace.nodes]
    if not plans:
        raise ValueError("no plan carries a search trace (plan with trace=True)")
    net = scenario.network
    if extent == "trace":
        nodes = [v for p in plans for v in p.trace.nodes]  # type: ignore[union-attr]
        nodes += [v for p in plans for v in p.nodes]
        nodes += [scenario.query.origin, scenario.query.destination]
        xs = [net.x[v] for v in nodes]
        ys = [net.y[v] for v in nodes]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        pad = 0.05 * max(x1 - x0, y1 - y0, 1.0)
        ext = (x0 - pad, x1 + pad, y0 - pad, y1 + pad)
    else:
        ext = map_extent(scenario, plans, extent)
    n = len(plans)
    cols = cols or (2 if n == 4 else min(n, 3))
    rows = math.ceil(n / cols)
    with style.paper():
        fs = float(_fs())
        w = style.figsize(width)[0]
        aspect = (ext[3] - ext[2]) / max(ext[1] - ext[0], 1e-9)
        gap_w, title_h, cbar_h, margin = 0.12, (fs * 1.9) / 72.0, 0.42, 0.03
        panel_w = (w - 2 * margin - (cols - 1) * gap_w) / cols
        panel_h = panel_w * aspect
        if panel_h > 3.2:  # very tall instances: limit the height, keep the aspect
            panel_h = 3.2
            panel_w = panel_h / aspect
        fig_h = rows * (panel_h + title_h) + cbar_h + margin
        fig, axes = style.subplots(rows, cols, figsize=(w, fig_h), squeeze=False)
        for extra_ax in axes.flat[n:]:
            extra_ax.remove()
        cmap = style.sequential_cmap(style.OKABE_ITO["blue"], light="#C9E0F2")
        o, d = scenario.query.origin, scenario.query.destination
        x_off = (w - (cols * panel_w + (cols - 1) * gap_w)) / 2.0
        for k, p in enumerate(plans):
            r, c = divmod(k, cols)
            left = x_off + c * (panel_w + gap_w)
            top = fig_h - margin - r * (panel_h + title_h) - title_h
            ax = axes.flat[k]
            ax.set_position((left / w, (top - panel_h) / fig_h, panel_w / w, panel_h / fig_h))
            m_per_pt = _fix_axes(ax, ext)
            ax.set_facecolor("white")
            _draw_roads(ax, scenario, ext, alpha=0.5, scale=0.8)
            tr = p.trace
            assert tr is not None
            m = len(tr.nodes)
            order = [i / max(m - 1, 1) for i in range(m)]
            _points(ax, [net.x[v] for v in tr.nodes], [net.y[v] for v in tr.nodes], hue=order, hue_norm=(0.0, 1.0),
                    palette=cmap, s=6, linewidth=0, edgecolor="none", zorder=3)
            ctx = MapContext(ax=ax, extent=ext, m_per_pt=m_per_pt)
            if p.edges:
                _draw_routes(ctx, scenario, [p], highlight=None, lw=1.4, stops=True)
            _points(ax, [net.x[o]], [net.y[o]], s=28, marker="o", color="white", edgecolor=style.INK, linewidth=1.2,
                    zorder=9)
            _points(ax, [net.x[d]], [net.y[d]], s=28, marker="o", color=style.INK, edgecolor="white", linewidth=1.0,
                    zorder=9)
            settled = p.stats.settled if p.stats else m
            frac = 100.0 * m / max(net.num_nodes, 1)
            ax.set_title(_title(p), loc="left", fontsize=fs, fontweight="bold", pad=3,
                         color=style.algorithm_color(p.algorithm) if p.algorithm != "pareto" else style.INK)
            ax.text(1.0, 1.0, f"{m:,} nodes ({frac:.0f} %) · {settled:,} labels", transform=ax.transAxes, ha="right",
                    va="bottom", fontsize=fs - 1.5, color=style.INK_MUTED)
        cw = min(2.6, w * 0.45)
        cax = fig.add_axes(((w - cw) / 2 / w, (0.22) / fig_h, cw / w, 0.07 / fig_h))
        style.colorbar(None, cmap, 0.0, 1.0, cax=cax, orientation="horizontal", ticks=[0.0, 1.0],
                       ticklabels=["settled first", "settled last"], outline=0.4)
        cax.tick_params(length=0, labelsize=fs - 1)
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
    ``reference`` plan (or a number) as a dashed line; the final gap to
    it is written next to the last point.  Bottom: share of feasible
    individuals in the population (``stats.extra["history_feasible"]``).
    Steps are :func:`seaborn.lineplot` (``drawstyle="steps-post"``), the
    feasible share is filled by a weighted :func:`seaborn.histplot`.
    """
    st = genetic_plan.stats
    hist = (st.extra or {}).get("history") if st else None
    if not hist:
        raise ValueError("plan has no convergence history (stats.extra['history'])")
    feas = list((st.extra or {}).get("history_feasible") or [])  # type: ignore[union-attr]
    time_obj = genetic_plan.objective == "time"
    conv = (lambda v: v / 3600.0) if time_obj else (lambda v: v)
    with style.paper():
        sns = _sns()
        w, h = style.figsize(width, aspect)
        if feas:
            fig, (a1, a2) = style.subplots(2, 1, figsize=(w, h), sharex=True, height_ratios=[2.4, 1.0], hspace=0.1)
        else:
            fig, a1 = style.subplots(figsize=(w, h))
            a2 = None
        fs_small = _fs() - 1.5
        gens = list(range(len(hist)))
        gx = [g for g, v in zip(gens, hist) if v is not None]
        gy = [conv(v) for v in hist if v is not None]
        col = style.algorithm_color(genetic_plan.algorithm or "genetic")
        best = _line(a1, gx, gy, drawstyle="steps-post", color=col, lw=1.3) if gx else None
        if gx:
            _points(a1, [gx[-1]], [gy[-1]], s=14, color=col, edgecolor="none", linewidth=0, zorder=4, clip_on=False)
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
            a1.annotate(ref_label, (0.0, ref_val), xycoords=("axes fraction", "data"), xytext=(3, 2),
                        textcoords="offset points", ha="left", va="bottom", fontsize=fs_small, color=style.INK)
            if gy and ref_val > 0:
                gap = 100.0 * (gy[-1] - ref_val) / ref_val
                a1.annotate(f"gap {gap:.2f} %", (gx[-1], gy[-1]), xytext=(-2, 4), textcoords="offset points",
                            ha="right", va="bottom", fontsize=fs_small, color=col)
            lo = min(min(gy, default=ref_val), ref_val)
            hi = max(gy, default=ref_val)
            a1.set_ylim(lo - 0.08 * (hi - lo + 1e-9), hi + 0.08 * (hi - lo + 1e-9))
        a1.set_ylabel("Trip time [h]" if time_obj else f"Gen. cost [{genetic_plan.currency}]")
        a1.grid(color=style.GRID, lw=0.5)
        if best is not None:
            a1.legend([best], [f"{_title(genetic_plan)}: best feasible"], loc="upper right")
        if a2 is not None:
            # feasible share per generation: a step histogram over the generations weighted by the share
            gens_f = list(range(len(feas)))
            sns.histplot(x=gens_f, weights=[100 * f for f in feas], bins=[float(g) for g in range(len(feas) + 1)],
                         element="step", fill=True, color=col, alpha=0.25, linewidth=0, ax=a2)
            _line(a2, gens_f, [100 * f for f in feas], drawstyle="steps-post", color=col, lw=0.9)
            a2.set_ylim(0, 105)
            a2.set_ylabel("Feasible [%]")
            a2.set_xlabel("Generation", visible=True)
            a2.grid(color=style.GRID, lw=0.5)
        else:
            a1.set_xlabel("Generation")
        a1.set_xlim(0, max(len(hist) - 1, 1))
        return _finish(fig, path)


# ================================================ several instances (seaborn)
def _results_table(results: Any) -> tuple[list[str], list[str], dict[tuple[str, str], RoutePlan]]:
    """Normalise multi-instance results.

    Accepts ``{instance: [plans]}``, ``{instance: {algorithm: plan}}`` or a
    sequence of plan lists (instances are then numbered from 1).  Returns
    ``(instances, algorithms, {(instance, algorithm): plan})`` with the
    algorithms in order of first appearance.
    """
    if isinstance(results, Mapping):
        items = [(str(k), v) for k, v in results.items()]
    else:
        items = [(str(i + 1), v) for i, v in enumerate(results)]
    instances: list[str] = []
    algos: list[str] = []
    table: dict[tuple[str, str], RoutePlan] = {}
    for name, plans in items:
        instances.append(name)
        for p in _as_plans(plans):
            if p.algorithm not in algos:
                algos.append(p.algorithm)
            table[(name, p.algorithm)] = p
    return instances, algos, table


def _objective_of(p: RoutePlan, metric: str) -> float:
    if metric == "time":
        return p.total_time_s
    if metric == "cost":
        return p.generalized_cost
    if metric == "money":
        return p.money
    if metric == "objective":
        return p.objective_value
    raise ValueError("metric must be 'time', 'cost', 'money' or 'objective'")


def gap_rows(results: Any, *, reference: str | None = None, metric: str = "time") -> list[dict[str, Any]]:
    """Long-form rows ``{instance, algorithm, title, feasible, value, gap_pct}``.

    The gap of a feasible plan is ``100 * (value - ref) / ref`` where
    ``ref`` is the ``reference`` algorithm's value on the same instance (an
    exact method such as ``"cfp"``) or, without a reference, the best value
    of any feasible plan on that instance.  Instances whose reference plan
    is missing or infeasible are skipped.  ``gap_pct`` is ``nan`` for
    infeasible plans.
    """
    instances, algos, table = _results_table(results)
    rows: list[dict[str, Any]] = []
    for inst in instances:
        plans = [table[(inst, a)] for a in algos if (inst, a) in table]
        if reference is not None:
            ref_plan = table.get((inst, reference))
            if ref_plan is None or not ref_plan.feasible:
                continue
            ref = _objective_of(ref_plan, metric)
        else:
            vals = [_objective_of(p, metric) for p in plans if p.feasible]
            if not vals:
                continue
            ref = min(vals)
        for p in plans:
            v = _objective_of(p, metric) if p.feasible else math.nan
            gap = 100.0 * (v - ref) / ref if (p.feasible and ref > 0) else math.nan
            rows.append({"instance": inst, "algorithm": p.algorithm, "title": _title(p), "feasible": p.feasible,
                         "value": v, "gap_pct": gap})
    return rows


def plot_gap_distribution(
    results: Any,
    *,
    reference: str | None = None,
    metric: str = "time",
    kind: str = "box",
    exclude: Sequence[str] = ("naive",),
    width: str | float = "single",
    path: Any = None,
) -> Any:
    """Distribution of the optimality gap of each algorithm over many instances.

    ``results`` holds one set of plans per instance (see :func:`gap_rows`
    for the accepted shapes and the gap definition).  One row per
    algorithm: a box plot (``kind="box"``, median, quartiles, whiskers at
    1.5 IQR) or a violin (``kind="violin"``, cut at the data range) in a
    pale tint of the algorithm colour, with every instance as a jittered
    dot on top (:func:`seaborn.boxplot` / :func:`seaborn.violinplot` +
    :func:`seaborn.stripplot`).  The right margin reports how many
    instances each algorithm solved; infeasible runs have no gap and are
    only counted there.  The reference algorithm itself is omitted.
    """
    sns = _sns()

    rows = [r for r in gap_rows(results, reference=reference, metric=metric)
            if r["algorithm"] not in exclude and r["algorithm"] != reference]
    if not rows:
        raise ValueError("no instance with a feasible reference")
    order: list[str] = []
    for r in rows:
        if r["algorithm"] not in order:
            order.append(r["algorithm"])
    titles = [style.algorithm_title(a) for a in order]
    pal = style.algorithm_palette(order, titles=True)
    pale = {t: _pale(c, 0.68) for t, c in pal.items()}
    feas_rows = [r for r in rows if r["feasible"] and math.isfinite(r["gap_pct"])]
    x = [r["gap_pct"] for r in feas_rows]
    y = [r["title"] for r in feas_rows]
    with style.paper():
        n = len(order)
        fig, ax = _new_fig(width, 0.0, height=0.62 + 0.27 * n)
        common = {"x": x, "y": y, "hue": y, "order": titles, "hue_order": titles, "legend": False, "ax": ax}
        if kind == "violin":
            sns.violinplot(**common, palette=pale, cut=0, inner=None, linewidth=0.6, density_norm="width", width=0.75,
                           saturation=1.0)
            for coll in ax.collections:
                coll.set_edgecolor(style.INK_MUTED)
        elif kind == "box":
            with warnings.catch_warnings():  # seaborn 0.13 passes matplotlib's deprecated ``vert``
                warnings.filterwarnings("ignore", message=".*vert: bool was deprecated.*")
                sns.boxplot(**common, palette=pale, width=0.6, showfliers=False, linewidth=0.7, saturation=1.0,
                            medianprops={"color": style.INK, "linewidth": 1.1}, whiskerprops={"color": style.INK_MUTED},
                            capprops={"color": style.INK_MUTED}, boxprops={"edgecolor": style.INK_MUTED})
        else:
            raise ValueError("kind must be 'box' or 'violin'")
        sns.stripplot(**common, palette=pal, size=2.6, alpha=0.75, jitter=0.2, linewidth=0.3, edgecolor="white")
        n_inst = len({r["instance"] for r in rows})
        for i, a in enumerate(order):
            mine = [r for r in rows if r["algorithm"] == a]
            ok = sum(r["feasible"] for r in mine)
            ax.annotate(f"{ok}/{len(mine)}", (1.0, i), xycoords=("axes fraction", "data"), xytext=(4, 0),
                        textcoords="offset points", ha="left", va="center", fontsize=_fs() - 1.5,
                        color=style.INK if ok == len(mine) else "#B5654A", annotation_clip=False)
        ax.annotate("solved", (1.0, 1.0), xycoords="axes fraction", xytext=(4, 2), textcoords="offset points",
                    ha="left", va="bottom", fontsize=_fs() - 1.5, color=style.INK_MUTED,
                    annotation_clip=False)
        ax.axvline(0.0, color=style.INK, lw=0.6, zorder=0)
        ref_name = style.algorithm_title(reference) if reference else "best plan"
        what = {"time": "trip time", "cost": "generalised cost", "money": "charging cost", "objective": "objective"}[metric]
        ax.set_xlabel(f"Gap in {what} to {ref_name} [%]  ({n_inst} instances)")
        ax.set_ylabel("")
        ax.set_xlim(left=min(-0.5, min(x, default=0.0) - 0.5))
        ax.grid(axis="x", color=style.GRID, lw=0.5)
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
        return _finish(fig, path)


def plot_metric_heatmap(
    results: Any,
    *,
    metric: str = "gap",
    reference: str | None = None,
    annot: bool = True,
    exclude: Sequence[str] = (),
    width: str | float = "double",
    path: Any = None,
) -> Any:
    """Instance x algorithm matrix of one metric (:func:`seaborn.heatmap`).

    ``metric`` is ``"gap"`` (optimality gap in trip time [%], see
    :func:`gap_rows`), ``"time"`` (trip time [min]), ``"runtime"`` [ms]
    or ``"settled"`` (labels); the last two use a logarithmic colour
    scale (the heatmap of ``log10`` of the values, with the colour bar
    labelled in powers of ten).  Infeasible runs are blank cells marked
    with a cross.  Light = good, dark = bad, one sequential hue, so the
    matrix also reads in grey-scale print.
    """
    sns = _sns()

    instances, algos, table = _results_table(results)
    algos = [a for a in algos if a not in exclude]
    gaps: dict[tuple[str, str], float] = {}
    if metric == "gap":
        for r in gap_rows(results, reference=reference, metric="time"):
            gaps[(r["instance"], r["algorithm"])] = r["gap_pct"]
    labels = {"gap": "gap [%]", "time": "trip time [min]", "runtime": "runtime [ms]", "settled": "settled labels"}
    if metric not in labels:
        raise ValueError(f"metric must be one of {sorted(labels)}")
    M: list[list[float]] = []
    for inst in instances:
        row = []
        for a in algos:
            p = table.get((inst, a))
            if p is None:
                row.append(math.nan)
            elif metric == "gap":
                row.append(gaps.get((inst, a), math.nan))
            elif metric == "time":
                row.append(p.total_time_s / 60.0 if p.feasible else math.nan)
            elif metric == "runtime":
                row.append(1000.0 * p.stats.runtime_s if p.stats else math.nan)
            else:
                row.append(float(p.stats.settled) if p.stats else math.nan)
        M.append(row)
    finite = [v for row in M for v in row if math.isfinite(v)]
    if not finite:
        raise ValueError("nothing to plot")
    log = metric in ("runtime", "settled")

    def fmt(v: float) -> str:
        if not math.isfinite(v):
            return ""
        if metric == "gap":
            return f"{0.0 if abs(v) < 0.05 else v:.1f}"  # no "-0.0" from float noise
        if metric == "time":
            return f"{v:.0f}"
        if metric == "runtime":
            return f"{v:.0f}" if v >= 10 else f"{v:.1f}"
        return f"{v / 1000:.0f}k" if v >= 10000 else f"{v:.0f}"

    import numpy as np

    data = np.array(M, dtype=float)
    with style.paper():
        w = style.figsize(width)[0]
        cell_h = 0.2
        fig, ax = _new_fig(w, 0.0, height=0.75 + cell_h * len(instances))
        cmap = style.sequential_cmap(style.OKABE_ITO["blue"], light="#EEF5FB")
        cmap = cmap.with_extremes(bad="white")
        shown = data
        vmin: float | None = 0.0 if metric == "gap" else min(finite)
        vmax: float | None = None
        if log:  # colour by log10 of the value; the colour bar is labelled in powers of ten
            lo_v = max(min(finite), 1e-3)
            shown = np.log10(np.clip(data, lo_v, None))
            vmin, vmax = math.log10(lo_v), math.log10(max(max(finite), lo_v * (1 + 1e-9)))
        sns.heatmap(shown, ax=ax, cmap=cmap, vmin=vmin, vmax=vmax,
                    annot=[[fmt(v) for v in row] for row in M] if annot else False,
                    fmt="", annot_kws={"fontsize": _fs() - 1.5}, linewidths=0.6, linecolor="white",
                    cbar_kws={"label": labels[metric], "shrink": 0.9, "pad": 0.015, "aspect": 30},
                    xticklabels=[style.algorithm_title(a) for a in algos], yticklabels=instances,
                    mask=~np.isfinite(data))
        for i, row in enumerate(M):
            for j, v in enumerate(row):
                if not math.isfinite(v) and table.get((instances[i], algos[j])) is not None:
                    ax.text(j + 0.5, i + 0.5, "×", ha="center", va="center", color="#B5654A",
                            fontsize=_fs())
        ax.tick_params(axis="both", length=0)
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.xaxis.tick_top()
        ax.tick_params(axis="x", labelrotation=0)
        ax.tick_params(axis="y", labelrotation=0)
        cbar = ax.collections[0].colorbar
        if cbar is not None:
            cbar.outline.set_linewidth(0)
            cbar.ax.tick_params(length=2, width=0.5)
            if log:
                style.log_colorbar_ticks(cbar, vmin, vmax)
        return _finish(fig, path)


# ================================================================== gallery
def _plan_all(scenario: Scenario, options: Mapping[str, Mapping[str, Any]] | None = None, *, trace: bool = True,
              algorithms: Sequence[str] | None = None) -> list[RoutePlan]:
    """Run every registered planner (failures are skipped with a warning)."""
    from ..algorithms import available
    from ..algorithms import plan as run_plan

    out: list[RoutePlan] = []
    for name in algorithms or available():
        try:
            out.append(run_plan(scenario, name, trace=trace, **dict((options or {}).get(name, {}))))
        except Exception as exc:  # a broken planner must not stop the gallery
            warnings.warn(f"{name} failed on {scenario.name}: {exc!r}", stacklevel=2)
    return out


def make_gallery(
    outdir: str | Path = "docs/img",
    *,
    quick: bool = False,
    dpi: int = 200,
    formats: Sequence[str] = ("png",),
    instances: int = 16,
) -> list[Path]:
    """Regenerate the README / paper figure gallery (``fig_*.png``).

    Uses the Turkey flagship preset (``turkey-karaman-istanbul``; falls back
    to a grid instance if the presets are unavailable), the ``corridor``
    preset, ``instances`` random origin/destination pairs on the Turkey
    network (static waits, for the gap distribution) and the vehicle
    presets.  Every registered algorithm is run; the Pareto planner uses the
    web viewer's (time, money) settings on the large network.  The tariff
    figure (``turkey-karaman-istanbul-tariff``) needs the complete (time,
    money) front of a 750 km trip under a time-dependent tariff: about 1.5
    minutes and 10 million labels.  ``quick`` shrinks the multi-instance
    study, the corridor front and the tariff front (5 % charging grid, for
    smoke tests).  Run as ``python -m evroute.viz.figures [outdir] [--quick]``.
    """
    import random

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def emit(name: str, fig: Any) -> None:
        fig = fig[0] if isinstance(fig, tuple) else fig
        written.extend(style.save(fig, outdir / name, formats=formats, dpi=dpi, close=True))

    budget = {"pareto": {"criteria": ("time", "money"), "max_labels": 1_000_000}}
    try:
        from ..generators.presets import get_scenario

        turkey = get_scenario("turkey-karaman-istanbul")
        corridor: Scenario | None = get_scenario("corridor")
        tariff: Scenario | None = get_scenario("turkey-karaman-istanbul-tariff")
    except Exception:  # presets unavailable: a grid instance still shows every figure
        from ..generators import grid_scenario

        turkey = grid_scenario(12, 12, seed=3, initial_soc=0.35, spacing_m=15000.0)
        corridor = tariff = None
    plans = _plan_all(turkey, budget)
    by = {p.algorithm: p for p in plans}
    routed = [p for p in plans if p.edges]
    emit("fig_map_turkey", plot_map(turkey, routed))
    if "bls-astar" in by:
        emit("fig_map_turkey_bls_astar", plot_map(turkey, routed, highlight="bls-astar"))
    emit("fig_soc_turkey", plot_soc_profile(routed, turkey))
    emit("fig_time_turkey", plot_time_breakdown(routed))
    emit("fig_table_turkey", plot_comparison_table(plans))
    exact = [by[a] for a in ("bls", "bls-astar", "cfp", "cfp-astar") if a in by]
    if exact:
        emit("fig_search_space_turkey", plot_search_space(turkey, exact))
    if "pareto" in by and by["pareto"].feasible:
        emit("fig_pareto_turkey", plot_pareto(by["pareto"], others=[by[a] for a in ("genetic", "bls-astar", "cfp") if a in by]))
    shown = [by[a] for a in ("greedy", "station-graph", "genetic", "bls-astar") if a in by]
    emit("fig_wait_turkey", plot_wait_profiles(turkey, plans=shown))
    if "genetic" in by:
        ref = by.get("cfp") or by.get("bls")
        try:
            emit("fig_convergence_turkey", plot_convergence(by["genetic"], reference=ref))
        except ValueError:
            pass

    # several origin/destination pairs on the same network
    base = turkey.with_preferences(time_dependent_waits=False)
    net = base.network
    rng = random.Random(42)
    named = sorted(net.node_name) or list(range(net.num_nodes))
    n_inst = 4 if quick else instances
    results: dict[str, list[RoutePlan]] = {}
    algos = [a for a in by if a != "pareto"]
    tries = 0
    while len(results) < n_inst and tries < 200 * n_inst:
        tries += 1
        o, d = rng.sample(named, 2)
        if net.euclidean(o, d) < 300_000.0:
            continue
        name = f"{_short_name(net.node_name.get(o, str(o)))} → {_short_name(net.node_name.get(d, str(d)))}"
        if name in results:
            continue
        sc = base.with_query(origin=o, destination=d, initial_soc=round(rng.uniform(0.3, 0.9), 2))
        results[name] = _plan_all(sc, trace=False, algorithms=algos)
    if results:
        ref = "cfp" if "cfp" in algos else None
        emit("fig_gap_turkey", plot_gap_distribution(results, reference=ref))
        emit("fig_gap_heatmap_turkey", plot_metric_heatmap(results, metric="gap", reference=ref))
        emit("fig_search_effort_turkey", plot_search_effort([p for ps in results.values() for p in ps]))

    if corridor is not None:
        cplans = _plan_all(corridor, budget if quick else None)
        cby = {p.algorithm: p for p in cplans}
        crouted = [p for p in cplans if p.edges]
        emit("fig_map_corridor", plot_map(corridor, crouted))
        emit("fig_soc_corridor", plot_soc_profile(crouted, corridor, x="time", elevation="panel"))
        if "pareto" in cby and cby["pareto"].feasible:
            emit("fig_pareto_corridor", plot_pareto(cby["pareto"], others=[cby[a] for a in ("greedy", "cfp") if a in cby]))
    if tariff is not None:
        # no single best route: the (time, money) front under the demonstration tariff
        from ..algorithms import plan as run_plan

        tsc = tariff.with_preferences(charge_step_soc=0.05) if quick else tariff
        fastest = run_plan(tsc, "bls-astar")
        balanced = run_plan(tsc.with_preferences(objective="cost"), "bls-astar")
        front = run_plan(tsc, "pareto", criteria=("time", "money"), max_labels=1_000_000 if quick else 10_000_000)
        if front.alternatives:
            cheapest = min(front.alternatives, key=lambda p: (p.money, p.total_time_s))
            emit("fig_tradeoff_tariff",
                 plot_tradeoff(front, {"Fastest": fastest, "Balanced": balanced, "Cheapest": cheapest}))
    emit("fig_charging_curves", plot_charging_curves())
    emit("fig_consumption", plot_consumption_vs_speed())
    style.close("all")
    return written


if __name__ == "__main__":  # pragma: no cover - manual gallery rebuild
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    paths = make_gallery(args[0] if args else "docs/img", quick="--quick" in sys.argv)
    for p in paths:
        print(p)
