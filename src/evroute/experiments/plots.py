"""Experiment plots (seaborn + matplotlib, imported lazily).

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
:func:`scatter_plot`      :func:`seaborn.scatterplot` (+ colour bar)
:func:`heatmap`           :func:`seaborn.heatmap`
========================  ===================================================

Each helper returns the list of written paths and closes its figure.
Without seaborn the same figures are drawn with plain matplotlib.
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


def _plt() -> Any:
    """``matplotlib.pyplot`` on the Agg backend with the paper style applied."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    try:
        from ..viz import style

        style.apply()
    except Exception:  # pragma: no cover - style module unavailable
        plt.rcParams.update({"font.size": 8, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _sns() -> Any:
    try:
        import seaborn as sns
    except ImportError:  # pragma: no cover - optional
        return None
    return sns


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
    try:
        from ..viz import style

        return style.save(fig, Path(path), close=True)
    except ImportError:  # pragma: no cover
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        out = []
        for ext in (".pdf", ".png"):
            p = path.with_suffix(ext)
            fig.savefig(p, dpi=300 if ext == ".png" else None, bbox_inches="tight")
            out.append(p)
        import matplotlib.pyplot as plt

        plt.close(fig)
        return out


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
    plt = _plt()
    sns = _sns()
    fig, ax = plt.subplots(figsize=FIGSIZE)
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
        if sns is not None:
            sns.lineplot(x=X, y=Y, hue=H, style=H, palette=pal, markers=mk if markers else False, dashes=False,
                         errorbar=("ci", 95), n_boot=500, seed=0, markersize=4.5, markeredgecolor="white",
                         markeredgewidth=0.5, lw=1.3, ax=ax, legend=len(names) > 1, sort=True)
        else:  # pragma: no cover
            for n in names:
                lab = _label(n)
                pts = sorted((x, y) for x, y, h in zip(X, Y, H) if h == lab)
                ax.plot([p[0] for p in pts], [p[1] for p in pts], marker=mk[lab] if markers else None, ms=4, lw=1.3,
                        color=pal[lab], label=lab)
            if len(names) > 1:
                ax.legend()
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
    plt = _plt()
    sns = _sns()
    names = [n for n, v in groups.items() if any(_finite(x) for x in v)]
    fig, ax = plt.subplots(figsize=FIGSIZE)
    if names:
        pal = _palette(names)
        X = [_label(n) for n in names for v in groups[n] if _finite(v)]
        Y = [float(v) for n in names for v in groups[n] if _finite(v)]
        order = [_label(n) for n in names]
        if sns is not None:
            from matplotlib.colors import to_rgb

            pale = {k: tuple(c + (1 - c) * 0.68 for c in to_rgb(v)) for k, v in pal.items()}
            with warnings.catch_warnings():  # seaborn 0.13 passes matplotlib's deprecated ``vert``
                warnings.filterwarnings("ignore", message=".*vert: bool was deprecated.*")
                sns.boxplot(x=X, y=Y, hue=X, order=order, hue_order=order, palette=pale, width=0.6, showfliers=False,
                            saturation=1.0, linewidth=0.7, legend=False, ax=ax,
                            medianprops={"color": "#262626", "linewidth": 1.1}, boxprops={"edgecolor": "#595959"},
                            whiskerprops={"color": "#595959"}, capprops={"color": "#595959"})
            sns.stripplot(x=X, y=Y, hue=X, order=order, hue_order=order, palette=pal, size=2.6, alpha=0.75,
                          jitter=0.2, linewidth=0.3, edgecolor="white", legend=False, ax=ax)
        else:  # pragma: no cover
            bp = ax.boxplot([groups[n] for n in names], patch_artist=True, showfliers=True, widths=0.6)
            for patch, n in zip(bp["boxes"], names):
                patch.set_facecolor(color(n))
                patch.set_alpha(0.6)
            ax.set_xticks(range(1, len(names) + 1))
            ax.set_xticklabels(order)
        if sum(len(_label(n)) for n in names) > 40:
            plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
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
    plt = _plt()
    sns = _sns()
    fig, ax = plt.subplots(figsize=FIGSIZE)
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
    if sns is not None and X:
        sns.barplot(x=X, y=Y, hue=X, order=order, hue_order=order, palette=pal, legend=False, ax=ax, saturation=1.0,
                    errorbar=("ci", 95) if repeated else None, n_boot=1000, seed=0, err_kws={"linewidth": 1.0},
                    capsize=0.15, width=0.65, edgecolor="white", linewidth=0.6)
    elif X:  # pragma: no cover
        means = [sum(y for x, y in zip(X, Y) if x == o) / max(1, sum(1 for x in X if x == o)) for o in order]
        ax.bar(order, means, color=[pal[o] for o in order])
    if annotate and X and not repeated:
        for i, o in enumerate(order):
            ys = [y for x, y in zip(X, Y) if x == o]
            if ys:
                ax.annotate(f"{ys[0]:.3g}", (i, ys[0]), xytext=(0, 2), textcoords="offset points", ha="center",
                            va="bottom", fontsize=6.5, color="#262626")
    if sum(len(o) for o in order) > 40:
        plt.setp(ax.get_xticklabels(), rotation=20, ha="right")
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
    plt = _plt()
    sns = _sns()
    from ..viz import style

    fig, ax = plt.subplots(figsize=FIGSIZE)
    big = len(xs) > 150
    edge = {"edgecolor": "none", "linewidth": 0.0} if big else {"edgecolor": "white", "linewidth": 0.4}
    cmap = style.sequential_cmap()
    norm = None
    if c is not None and len(c):
        from matplotlib.colors import Normalize

        lo, hi = min(c), max(c)
        norm = Normalize(vmin=lo, vmax=hi if hi > lo else lo + 1e-9)
    if sns is not None:
        kw: dict[str, Any] = dict(x=list(xs), y=list(ys), ax=ax, legend=False, **edge)
        if c is not None:
            kw.update(hue=list(c), hue_norm=norm, palette=cmap)
        else:
            kw.update(color=style.OKABE_ITO["blue"])
        if sizes is not None:
            kw.update(size=list(sizes), sizes=(min(sizes), max(sizes)) if max(sizes) > min(sizes) else None)
        else:
            kw.update(s=8 if big else 18)
        sns.scatterplot(**kw)
    else:  # pragma: no cover
        ax.scatter(xs, ys, s=sizes or 18, c=c if c is not None else style.OKABE_ITO["blue"], cmap=cmap, norm=norm)
    if c is not None and norm is not None:
        from matplotlib.cm import ScalarMappable

        cb = fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=ax, pad=0.02, fraction=0.06, aspect=25)
        if clabel:
            cb.set_label(clabel)
        cb.outline.set_linewidth(0.4)
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
    runtimes and label counts).
    """
    plt = _plt()
    sns = _sns()
    import numpy as np

    from ..viz import style

    data = np.array([[float(v) if _finite(v) else np.nan for v in row] for row in matrix], dtype=float)
    finite = data[np.isfinite(data)]
    n_rows, n_cols = data.shape if data.size else (0, 0)
    w = min(7.16, max(FIGSIZE[0], 0.9 + 0.62 * n_cols))
    fig, ax = plt.subplots(figsize=(w, 0.7 + 0.21 * max(n_rows, 1)))
    cmap = style.sequential_cmap(style.OKABE_ITO["blue"], light="#EEF5FB")
    cmap = cmap.with_extremes(bad="white")
    norm = None
    if log and finite.size and finite.min() > 0:
        from matplotlib.colors import LogNorm

        norm = LogNorm(vmin=float(finite.min()), vmax=float(finite.max()))
    cols = [_wrap(_label(c)) for c in col_labels]
    if sns is not None and finite.size:
        sns.heatmap(data, ax=ax, cmap=cmap, norm=norm, annot=annot, fmt=fmt, annot_kws={"fontsize": 6.5},
                    linewidths=0.6, linecolor="white", mask=~np.isfinite(data), xticklabels=cols,
                    yticklabels=list(row_labels), cbar_kws={"label": clabel or "", "pad": 0.015, "aspect": 30})
        cbar = ax.collections[0].colorbar
        if cbar is not None:
            cbar.outline.set_linewidth(0)
    elif finite.size:  # pragma: no cover
        im = ax.imshow(np.ma.masked_invalid(data), cmap=cmap, norm=norm, aspect="auto")
        fig.colorbar(im, ax=ax, label=clabel or "")
        ax.set_xticks(range(n_cols), cols)
        ax.set_yticks(range(n_rows), list(row_labels))
    ax.tick_params(axis="both", length=0)
    ax.xaxis.tick_top()
    plt.setp(ax.get_xticklabels(), rotation=0)
    plt.setp(ax.get_yticklabels(), rotation=0)
    if title:
        ax.set_title(title, loc="left", pad=16)
    return save(fig, path)
