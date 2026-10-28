"""Experiment plots, drawn with seaborn (imported lazily).

Small, generic helpers used by :mod:`evroute.experiments.runner`: every
function takes plain numbers, draws one IEEE single-column figure in the
EVRoute paper style (:mod:`evroute.viz.style`: seaborn ``"ticks"`` theme,
``"paper"`` context, colour-blind-safe Okabe-Ito colours) and writes it as a
vector PDF plus a 300 dpi PNG.  Series or groups named after an algorithm
(``"bls-astar"``) get that algorithm's colour, marker and title; any other
names get the categorical palette in order of appearance.

========================  ===================================================
helper                    seaborn plot
========================  ===================================================
:func:`line_plot`         :func:`seaborn.lineplot` (mean and 95 % CI when a
                          series repeats an x value)
:func:`box_plot`          :func:`seaborn.boxplot` + :func:`seaborn.stripplot`
:func:`bar_plot`          :func:`seaborn.barplot` (95 % CI for lists)
:func:`scatter_plot`      :func:`seaborn.scatterplot` (+ colour bar, itself
                          a one-row :func:`seaborn.heatmap`)
:func:`heatmap`           :func:`seaborn.heatmap`
========================  ===================================================

Each helper returns the list of written paths and closes its figure.  The
figure itself comes from :func:`evroute.viz.style.subplots` (a
:class:`seaborn.FacetGrid`).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

#: Kept for backwards compatibility; the colours come from
#: :data:`evroute.viz.style.ALGORITHM_COLORS` (same as the web viewer).
FALLBACK_COLORS: dict[str, str] = {
    "naive": "#7F7F7F",
    "greedy": "#D55E00",
    "station-graph": "#CC79A7",
    "genetic": "#E69F00",
    "bls": "#56B4E9",
    "bls-astar": "#0072B2",
    "cfp": "#009E73",
    "cfp-astar": "#A89200",
    "pareto": "#262626",
}

#: Figure size of every helper: IEEE single column, golden-ish aspect.
FIGSIZE: tuple[float, float] = (3.5, 2.5)


def _figure(figsize: tuple[float, float] = FIGSIZE) -> tuple[Any, Any]:
    """A new figure and axes (:func:`evroute.viz.style.subplots`) with the
    paper style installed (:func:`evroute.viz.style.apply`)."""
    from ..viz import style

    style.apply()
    return style.subplots(figsize=figsize)


def _sns() -> Any:
    from ..viz import style

    return style.require_seaborn()


def _is_algorithm(name: str) -> bool:
    from ..viz import style

    return name in style.ALGORITHM_COLORS


def color(name: str) -> str:
    """Colour of an algorithm (or a stable fallback for any other name)."""
    try:
        from ..viz.style import algorithm_color

        return algorithm_color(name)
    except Exception:  # pragma: no cover
        return FALLBACK_COLORS.get(name, "#333333")


def _label(name: str) -> str:
    from ..viz import style

    return style.algorithm_title(name) if _is_algorithm(name) else name


def _palette(names: Sequence[str]) -> dict[str, str]:
    """Display label -> colour: algorithm colours, else categorical order."""
    from ..viz import style

    out: dict[str, str] = {}
    k = 0
    for n in names:
        if _is_algorithm(n):
            out[_label(n)] = style.algorithm_color(n)
        else:
            out[_label(n)] = style.categorical(k)
            k += 1
    return out


def _markers(names: Sequence[str]) -> dict[str, str]:
    from ..viz import style

    generic = ("o", "s", "D", "^", "v", "P", "X", "*")
    out: dict[str, str] = {}
    k = 0
    for n in names:
        if _is_algorithm(n):
            out[_label(n)] = style.algorithm_marker(n)
        else:
            out[_label(n)] = generic[k % len(generic)]
            k += 1
    return out


def save(fig: Any, path: Path) -> list[Path]:
    """Write ``path.pdf`` and ``path.png`` (300 dpi), close ``fig``."""
    from ..viz import style

    return style.save(fig, Path(path), close=True)


def _slant(ax: Any) -> None:
    """Slant long category labels on the x axis."""
    ax.tick_params(axis="x", labelrotation=20)
    for t in ax.get_xticklabels():
        t.set_horizontalalignment("right")


def _wrap(label: str, width: int = 9) -> str:
    """Break a long tick label at its spaces (``"Station graph"`` -> two lines)."""
    if len(label) <= width or " " not in label:
        return label
    words, lines, cur = label.split(" "), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "\n".join(lines)


def _finish(ax: Any, xlabel: str | None, ylabel: str | None, title: str | None) -> None:
    if xlabel is not None:
        ax.set_xlabel(xlabel)
    if ylabel is not None:
        ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title, loc="left")


def _finite(v: Any) -> bool:
    """A real, finite number (numpy scalars included; ``None``/NaN/inf/bool not)."""
    if v is None or isinstance(v, (bool, str)):
        return False
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def line_plot(
    series: dict[str, tuple[Sequence[float], Sequence[float]]],
    xlabel: str,
    ylabel: str,
    path: Path,
    *,
    logx: bool = False,
    logy: bool = False,
    title: str | None = None,
    markers: bool = True,
) -> list[Path]:
    """One line per series ``name -> (xs, ys)``; non-finite points are skipped.

    When a series has several y values at the same x (repeated runs or
    instances) seaborn draws their mean with a 95 % bootstrap confidence
    band; otherwise the points are joined as given.
    """
    sns = _sns()
    fig, ax = _figure()
    names = [n for n, (xs, ys) in series.items() if any(_finite(y) for y in ys)]
    pal = _palette(names)
    mk = _markers(names)
    X: list[float] = []
    Y: list[float] = []
    H: list[str] = []
    for n in names:
        xs, ys = series[n]
        for x, y in zip(xs, ys):
            if _finite(y) and _finite(x):
                X.append(float(x))
                Y.append(float(y))
                H.append(_label(n))
    if X:
        sns.lineplot(x=X, y=Y, hue=H, style=H, palette=pal, markers=mk if markers else False, dashes=False,
                     errorbar=("ci", 95), n_boot=500, seed=0, markersize=4.5, markeredgecolor="white",
                     markeredgewidth=0.5, lw=1.3, ax=ax, legend=len(names) > 1, sort=True)
    if logx:
        ax.set_xscale("log")
    if logy:
        ax.set_yscale("log")
    _finish(ax, xlabel, ylabel, title)
    ax.grid(color="#E5E5E5", lw=0.5)
    leg = ax.get_legend()
    if leg is not None:
        leg.set_frame_on(False)
        leg.set_title(None)
    return save(fig, path)


def box_plot(groups: dict[str, list[float]], ylabel: str, path: Path, title: str | None = None) -> list[Path]:
    """Distribution per group: box (median, quartiles, 1.5 IQR whiskers) in a
    pale tint of the group colour with every value as a jittered dot."""
    from ..viz import style

    sns = _sns()
    names = [n for n, v in groups.items() if any(_finite(x) for x in v)]
    fig, ax = _figure()
    if names:
        pal = _palette(names)
        X = [_label(n) for n in names for v in groups[n] if _finite(v)]
        Y = [float(v) for n in names for v in groups[n] if _finite(v)]
        order = [_label(n) for n in names]
        pale = {k: style.tint(v, 0.68) for k, v in pal.items()}
        with warnings.catch_warnings():  # seaborn 0.13 passes matplotlib's deprecated ``vert``
            warnings.filterwarnings("ignore", message=".*vert: bool was deprecated.*")
            sns.boxplot(x=X, y=Y, hue=X, order=order, hue_order=order, palette=pale, width=0.6, showfliers=False,
                        saturation=1.0, linewidth=0.7, legend=False, ax=ax,
                        medianprops={"color": "#262626", "linewidth": 1.1}, boxprops={"edgecolor": "#595959"},
                        whiskerprops={"color": "#595959"}, capprops={"color": "#595959"})
        sns.stripplot(x=X, y=Y, hue=X, order=order, hue_order=order, palette=pal, size=2.6, alpha=0.75,
                      jitter=0.2, linewidth=0.3, edgecolor="white", legend=False, ax=ax)
        if sum(len(_label(n)) for n in names) > 40:
            _slant(ax)
    _finish(ax, None, ylabel, title)
    ax.grid(axis="y", color="#E5E5E5", lw=0.5)
    return save(fig, path)


def bar_plot(
    values: Mapping[str, float | Sequence[float]],
    ylabel: str,
    path: Path,
    title: str | None = None,
    *,
    annotate: bool = True,
) -> list[Path]:
    """One bar per name.  A list of values gives the mean with a 95 %
    bootstrap confidence interval (seaborn); a number is drawn as is."""
    sns = _sns()
    fig, ax = _figure()
    names = list(values)
    X: list[str] = []
    Y: list[float] = []
    repeated = False
    for n in names:
        v = values[n]
        if isinstance(v, (int, float)):
            vals = [float(v)] if _finite(v) else []
        else:
            vals = [float(x) for x in v if _finite(x)]
            repeated = repeated or len(vals) > 1
        X += [_label(n)] * len(vals)
        Y += vals
    order = [_label(n) for n in names]
    pal = _palette(names)
    if X:
        sns.barplot(x=X, y=Y, hue=X, order=order, hue_order=order, palette=pal, legend=False, ax=ax, saturation=1.0,
                    errorbar=("ci", 95) if repeated else None, n_boot=1000, seed=0, err_kws={"linewidth": 1.0},
                    capsize=0.15, width=0.65, edgecolor="white", linewidth=0.6)
    if annotate and X and not repeated:
        for i, o in enumerate(order):
            ys = [y for x, y in zip(X, Y) if x == o]
            if ys:
                ax.annotate(f"{ys[0]:.3g}", (i, ys[0]), xytext=(0, 2), textcoords="offset points", ha="center",
                            va="bottom", fontsize=6.5, color="#262626")
    if sum(len(o) for o in order) > 40:
        _slant(ax)
    _finish(ax, None, ylabel, title)
    ax.grid(axis="y", color="#E5E5E5", lw=0.5)
    ax.tick_params(axis="x", length=0)
    return save(fig, path)


def scatter_plot(
    xs: Sequence[float],
    ys: Sequence[float],
    xlabel: str,
    ylabel: str,
    path: Path,
    *,
    sizes: Sequence[float] | None = None,
    c: Sequence[float] | None = None,
    clabel: str | None = None,
    title: str | None = None,
) -> list[Path]:
    """Scatter of ``(xs, ys)``; optional per-point ``sizes`` (marker area)
    and a continuous colour ``c`` with a colour bar labelled ``clabel``."""
    from ..viz import style

    sns = _sns()
    fig, ax = _figure()
    big = len(xs) > 150
    edge = {"edgecolor": "none", "linewidth": 0.0} if big else {"edgecolor": "white", "linewidth": 0.4}
    cmap = style.sequential_cmap()
    lim = None
    if c is not None and len(c):
        lo, hi = min(c), max(c)
        lim = (lo, hi if hi > lo else lo + 1e-9)
    kw: dict[str, Any] = dict(x=list(xs), y=list(ys), ax=ax, legend=False, **edge)
    if c is not None:
        kw.update(hue=list(c), hue_norm=lim, palette=cmap)
    else:
        kw.update(color=style.OKABE_ITO["blue"])
    if sizes is not None:
        kw.update(size=list(sizes), sizes=(min(sizes), max(sizes)) if max(sizes) > min(sizes) else None)
    else:
        kw.update(s=8 if big else 18)
    sns.scatterplot(**kw)
    if lim is not None:
        style.colorbar(ax, cmap, lim[0], lim[1], label=clabel)
    _finish(ax, xlabel, ylabel, title)
    ax.grid(color="#E5E5E5", lw=0.5)
    return save(fig, path)


def heatmap(
    matrix: Sequence[Sequence[float]],
    row_labels: Sequence[str],
    col_labels: Sequence[str],
    path: Path,
    *,
    clabel: str | None = None,
    fmt: str = ".1f",
    log: bool = False,
    title: str | None = None,
    annot: bool = True,
) -> list[Path]:
    """Annotated matrix (rows x columns), one sequential hue, light = small.

    Non-finite entries are blank.  Column labels that name algorithms are
    shown by their titles.  ``log`` uses a logarithmic colour scale (for
    runtimes and label counts; the heatmap colours ``log10`` of the values
    and the colour bar is labelled in powers of ten).
    """
    sns = _sns()
    import numpy as np

    from ..viz import style

    data = np.array([[float(v) if _finite(v) else np.nan for v in row] for row in matrix], dtype=float)
    finite = data[np.isfinite(data)]
    n_rows, n_cols = data.shape if data.size else (0, 0)
    w = min(7.16, max(FIGSIZE[0], 0.9 + 0.62 * n_cols))
    fig, ax = _figure((w, 0.7 + 0.21 * max(n_rows, 1)))
    cmap = style.sequential_cmap(style.OKABE_ITO["blue"], light="#EEF5FB")
    cmap = cmap.with_extremes(bad="white")
    cols = [_wrap(_label(c)) for c in col_labels]
    log = bool(log and finite.size and finite.min() > 0)
    if finite.size:
        shown = np.log10(data) if log else data
        lo, hi = (float(np.log10(finite.min())), float(np.log10(finite.max()))) if log else (None, None)
        sns.heatmap(shown, ax=ax, cmap=cmap, vmin=lo, vmax=hi, annot=data if annot else False, fmt=fmt,
                    annot_kws={"fontsize": 6.5}, linewidths=0.6, linecolor="white", mask=~np.isfinite(data),
                    xticklabels=cols, yticklabels=list(row_labels),
                    cbar_kws={"label": clabel or "", "pad": 0.015, "aspect": 30})
        cbar = ax.collections[0].colorbar
        if cbar is not None:
            cbar.outline.set_linewidth(0)
            if log and lo is not None and hi is not None and hi > lo:
                style.log_colorbar_ticks(cbar, lo, hi)
    ax.tick_params(axis="both", length=0)
    ax.xaxis.tick_top()
    ax.tick_params(axis="x", labelrotation=0)
    ax.tick_params(axis="y", labelrotation=0)
    if title:
        ax.set_title(title, loc="left", pad=16)
    return save(fig, path)
