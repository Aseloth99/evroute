"""Experiment plots (matplotlib, imported lazily)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence

FALLBACK_COLORS = {
    "naive": "#999999",
    "greedy": "#E69F00",
    "station-graph": "#56B4E9",
    "genetic": "#CC79A7",
    "bls": "#009E73",
    "bls-astar": "#0072B2",
    "cfp": "#D55E00",
    "cfp-astar": "#F0E442",
    "pareto": "#000000",
}


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    try:  # use the paper style when available
        from ..viz import style

        if hasattr(style, "apply"):
            style.apply()
    except Exception:
        plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False})
    return plt


def color(name: str) -> str:
    try:
        from ..viz.style import algorithm_color

        return algorithm_color(name)
    except Exception:
        return FALLBACK_COLORS.get(name, "#333333")


def save(fig: Any, path: Path) -> list[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for ext in (".pdf", ".png"):
        p = path.with_suffix(ext)
        fig.savefig(p, dpi=300 if ext == ".png" else None, bbox_inches="tight")
        out.append(p)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return out


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
    plt = _plt()
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for name, (xs, ys) in series.items():
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None and math.isfinite(y)]
        if not pts:
            continue
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o" if markers else None, ms=3, lw=1.4, label=name, color=color(name))
    if logx:
        ax.set_xscale("log")
    if logy:
        ax.set_yscale("log")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(frameon=False, fontsize=7)
    return save(fig, path)


def box_plot(groups: dict[str, list[float]], ylabel: str, path: Path, title: str | None = None) -> list[Path]:
    plt = _plt()
    names = [n for n, v in groups.items() if v]
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    if names:
        bp = ax.boxplot([groups[n] for n in names], patch_artist=True, showfliers=True, widths=0.6)
        for patch, n in zip(bp["boxes"], names):
            patch.set_facecolor(color(n))
            patch.set_alpha(0.6)
        ax.set_xticks(range(1, len(names) + 1))
        ax.set_xticklabels(names, rotation=20)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    return save(fig, path)


def bar_plot(values: dict[str, float], ylabel: str, path: Path, title: str | None = None) -> list[Path]:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    names = list(values)
    ax.bar(names, [values[n] for n in names], color=[color(n) for n in names])
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", rotation=20)
    if title:
        ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
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
    plt = _plt()
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    sc = ax.scatter(xs, ys, s=sizes or 18, c=c if c is not None else "#0072B2", cmap="viridis", edgecolors="k", linewidths=0.3)
    if c is not None:
        cb = fig.colorbar(sc, ax=ax)
        if clabel:
            cb.set_label(clabel)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.grid(alpha=0.3)
    return save(fig, path)
