"""Paper style shared by every static figure of EVRoute.

This module is the single place that decides how EVRoute's static figures
*look*.  Every figure is drawn with seaborn (Waskom 2021, *J. Open Source
Softw.* 6(60), 3021):

* **one colour per algorithm**, taken from the colour-blind-safe palette of
  Okabe & Ito (2008, *Color Universal Design*) and identical to the colours
  of the interactive web viewer (:mod:`evroute.viz.export`), so that
  "BLS-A* is blue" holds in every figure, table and the animated demo;
* a **secondary encoding** per algorithm (marker shape, and a dashed line for
  heuristics without an optimality guarantee) so that the figures survive
  grey-scale printing and colour-vision deficiencies;
* a **seaborn theme**: :func:`apply` calls :func:`seaborn.set_theme` with a
  clean ``"ticks"`` style, the ``"paper"`` context and the EVRoute palette,
  and :func:`paper` installs the same settings temporarily;
* fonts and sizes for **IEEE single-column (3.5 in) and double-column
  (7.16 in)** figures, with TrueType (Type 42) fonts embedded in PDF/PS
  output as IEEE PDF eXpress requires;
* :func:`subplots` makes a figure through :class:`seaborn.FacetGrid`,
  :func:`colorbar` draws a colour scale as a one-row :func:`seaborn.heatmap`
  and :func:`save` writes a vector PDF and a 300 dpi PNG at once.

seaborn draws through matplotlib, which it installs as its own dependency;
EVRoute's figure code calls seaborn only.  The three helpers that must reach
matplotlib's settings directly -- the temporary rc settings of
:func:`paper`, :func:`close` and the text halo of :func:`halo` -- sit
together at the end of this module, and none of them draws data.

Everything at module level is plain Python; seaborn is imported lazily
inside the functions that need it, so the palette can be imported by code
that must stay dependency-free.

Typical use::

    import seaborn as sns
    from evroute.viz import style
    style.configure(font="serif")          # default for all EVRoute figures
    with style.paper():                    # or: style.apply() globally
        fig, ax = style.subplots(figsize=style.figsize("single"))
        sns.lineplot(x=x, y=y, ax=ax, **style.line_kw("bls-astar"))
        sns.barplot(data=rows, x="runtime", y="algorithm", hue="algorithm",
                    palette=style.algorithm_palette(names), ax=ax)
    style.save(fig, "figures/fig_soc", close=True)  # -> fig_soc.pdf + .png
"""

from __future__ import annotations

import math
import zlib
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path
from typing import Any

__all__ = [
    "OKABE_ITO",
    "ALGORITHM_COLORS",
    "ALGORITHM_COLORS_DARK",
    "ALGORITHM_MARKERS",
    "ALGORITHM_TITLES",
    "HEURISTICS",
    "CATEGORICAL",
    "TIME_COMPONENT_COLORS",
    "ROAD_STYLE",
    "SINGLE_COLUMN_IN",
    "DOUBLE_COLUMN_IN",
    "algorithm_color",
    "algorithm_marker",
    "algorithm_title",
    "algorithm_linestyle",
    "algorithm_palette",
    "line_kw",
    "categorical",
    "figsize",
    "rc_params",
    "configure",
    "apply",
    "paper",
    "has_seaborn",
    "require_seaborn",
    "subplots",
    "colorbar",
    "log_colorbar_ticks",
    "nice_ticks",
    "font_size",
    "rgb",
    "tint",
    "save",
    "close",
    "terrain_cmap",
    "sequential_cmap",
    "halo",
]

# ====================================================================== palette
#: The eight colours of Okabe & Ito (2008).
OKABE_ITO: dict[str, str] = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "reddish_purple": "#CC79A7",
}

#: Stable colour per algorithm (registry name -> hex), identical to the
#: light theme of the web viewer (``evroute.viz.export.ALGORITHM_COLORS``;
#: ``tests/test_figures.py`` checks that the two tables agree).  The two
#: exact label-setting methods share a hue family (BLS sky blue, BLS-A*
#: blue), the continuous CFP is bluish green.  The pure Okabe-Ito yellow
#: (#F0E442) has a contrast of only 1.3:1 on white paper, so CFP-A* uses a
#: darkened yellow of the same hue; the naive baseline is a neutral grey
#: because it is shown only to illustrate the problem.
ALGORITHM_COLORS: dict[str, str] = {
    "naive": "#7F7F7F",
    "greedy": OKABE_ITO["vermillion"],
    "station-graph": OKABE_ITO["reddish_purple"],
    "genetic": OKABE_ITO["orange"],
    "bls": OKABE_ITO["sky_blue"],
    "bls-astar": OKABE_ITO["blue"],
    "cfp": OKABE_ITO["bluish_green"],
    "cfp-astar": "#A89200",
    "pareto": "#262626",
}

#: Lighter variants for dark backgrounds (slides, the viewer's dark theme).
ALGORITHM_COLORS_DARK: dict[str, str] = {
    "naive": "#A3A3A3",
    "greedy": "#F0782E",
    "station-graph": "#E191BE",
    "genetic": "#F5B731",
    "bls": "#7CC8F2",
    "bls-astar": "#3D9EE6",
    "cfp": "#21C495",
    "cfp-astar": "#F0E442",
    "pareto": "#ECECEC",
}

#: Marker per algorithm (secondary encoding for grey-scale print).
ALGORITHM_MARKERS: dict[str, str] = {
    "naive": "X",
    "greedy": "v",
    "station-graph": "s",
    "genetic": "P",
    "bls": "o",
    "bls-astar": "D",
    "cfp": "^",
    "cfp-astar": ">",
    "pareto": "*",
}

#: Short labels for legends and table rows (same as the web viewer).
ALGORITHM_TITLES: dict[str, str] = {
    "naive": "Naive",
    "greedy": "Greedy",
    "station-graph": "Station graph",
    "genetic": "Genetic",
    "bls": "BLS",
    "bls-astar": "BLS-A*",
    "cfp": "CFP",
    "cfp-astar": "CFP-A*",
    "pareto": "Pareto",
}

#: Algorithms without an optimality guarantee; drawn with dashed lines.
HEURISTICS: frozenset[str] = frozenset({"naive", "greedy", "station-graph", "genetic"})

#: Fallback colours for unknown algorithm names (chosen by a stable hash,
#: the same table and hash as the web viewer).
_FALLBACK: tuple[str, ...] = (
    OKABE_ITO["blue"],
    OKABE_ITO["vermillion"],
    OKABE_ITO["bluish_green"],
    OKABE_ITO["reddish_purple"],
    OKABE_ITO["orange"],
    OKABE_ITO["sky_blue"],
    "#A89200",
)
_FALLBACK_MARKERS: tuple[str, ...] = ("o", "s", "D", "^", "v", "P", "X", "*")

#: Categorical order for entities that are *not* algorithms (vehicles,
#: stations, temperatures): Okabe-Ito without black and the pale yellow.
CATEGORICAL: tuple[str, ...] = (
    OKABE_ITO["blue"],
    OKABE_ITO["vermillion"],
    OKABE_ITO["bluish_green"],
    OKABE_ITO["reddish_purple"],
    OKABE_ITO["orange"],
    OKABE_ITO["sky_blue"],
    "#A89200",
    "#4D4D4D",
)

#: Colours of the trip-time components (drive / charge / queue / plug-in).
#: Checked for lightness band, chroma, normal and CVD separation (OKLab).
TIME_COMPONENT_COLORS: dict[str, str] = {
    "drive": "#2F6FA8",
    "charge": "#3FA58C",
    "wait": "#D1663C",
    "setup": "#C2A83E",
}

#: Road drawing per class: (colour, width in pt, z-order).
ROAD_STYLE: dict[str, tuple[str, float, float]] = {
    "motorway": ("#7A7A7A", 0.95, 1.40),
    "trunk": ("#8F8F8F", 0.70, 1.30),
    "primary": ("#A6A6A6", 0.50, 1.20),
    "secondary": ("#B3B3B3", 0.45, 1.10),
    "link": ("#B3B3B3", 0.40, 1.05),
    "urban": ("#BDBDBD", 0.40, 1.00),
    "residential": ("#C7C7C7", 0.30, 0.90),
}

#: Ink colours (text, axes) and surfaces.
INK = "#262626"
INK_MUTED = "#595959"
AXIS = "#4D4D4D"
GRID = "#E5E5E5"
SURFACE = "#FFFFFF"
MAP_BACKGROUND = "#F4F4F2"

# ======================================================================== sizes
#: IEEE Transactions column widths [in].
SINGLE_COLUMN_IN = 3.5
DOUBLE_COLUMN_IN = 7.16
#: Elsevier / Springer widths for completeness [in].
WIDTHS: dict[str, float] = {
    "single": SINGLE_COLUMN_IN,
    "double": DOUBLE_COLUMN_IN,
    "onehalf": 5.5,
    "slide": 10.0,
}

#: Default style settings used by :func:`paper` and :func:`apply` (change
#: them with :func:`configure`): font family, body size [pt], seaborn style
#: and seaborn context.
_CURRENT: dict[str, Any] = {"font": "sans", "base_size": 8.0, "theme": "ticks", "context": "paper"}

#: seaborn styles accepted by :func:`configure` (``seaborn.axes_style``).
THEMES: tuple[str, ...] = ("ticks", "whitegrid", "white", "darkgrid", "dark")
#: seaborn contexts accepted by :func:`configure` (``seaborn.plotting_context``).
CONTEXTS: tuple[str, ...] = ("paper", "notebook", "talk", "poster")


# ==================================================================== lookups
def algorithm_color(name: str, *, dark: bool = False) -> str:
    """Hex colour of algorithm ``name``; unknown names get a stable fallback.

    The fallback is chosen by CRC-32 of the name, so it is identical on every
    machine and every run (Python's ``hash`` is salted per process).
    """
    table = ALGORITHM_COLORS_DARK if dark else ALGORITHM_COLORS
    if name in table:
        return table[name]
    return _FALLBACK[zlib.crc32(name.encode("utf-8")) % len(_FALLBACK)]


def algorithm_marker(name: str) -> str:
    """Matplotlib marker of algorithm ``name`` (stable fallback for others)."""
    if name in ALGORITHM_MARKERS:
        return ALGORITHM_MARKERS[name]
    return _FALLBACK_MARKERS[zlib.crc32(name.encode("utf-8")) % len(_FALLBACK_MARKERS)]


def algorithm_title(name: str) -> str:
    """Short human-readable label (``"bls-astar" -> "BLS-A*"``)."""
    return ALGORITHM_TITLES.get(name, name)


def algorithm_linestyle(name: str) -> Any:
    """Solid for algorithms with a guarantee, dashed for heuristics."""
    return (0, (4.0, 1.6)) if name in HEURISTICS else "-"


def line_kw(name: str, **overrides: Any) -> dict[str, Any]:
    """Line keyword arguments that draw algorithm ``name`` in style (for
    :func:`seaborn.lineplot`, which passes them on to the line)."""
    kw: dict[str, Any] = {
        "color": algorithm_color(name),
        "linestyle": algorithm_linestyle(name),
        "label": algorithm_title(name),
        "solid_capstyle": "round",
        "dash_capstyle": "butt",
    }
    kw.update(overrides)
    return kw


def algorithm_palette(names: Iterable[str], *, titles: bool = False, dark: bool = False) -> dict[str, str]:
    """``{name: colour}`` for seaborn's ``palette=`` argument.

    With ``titles=True`` the keys are the display titles
    (:func:`algorithm_title`), for data frames whose hue column holds
    ``"BLS-A*"`` rather than ``"bls-astar"``.  Unknown names get the same
    stable fallback colour as in :func:`algorithm_color`.
    """
    out: dict[str, str] = {}
    for n in names:
        out[algorithm_title(n) if titles else n] = algorithm_color(n, dark=dark)
    return out


def categorical(i: int) -> str:
    """The ``i``-th colour of the non-algorithm categorical order (cycled
    only past 8 entities, which a single figure should never show)."""
    return CATEGORICAL[i % len(CATEGORICAL)]


def figsize(width: str | float = "single", aspect: float = 0.68, height: float | None = None) -> tuple[float, float]:
    """``(w, h)`` in inches; ``width`` is ``"single"``, ``"double"``, ... or inches."""
    w = WIDTHS[width] if isinstance(width, str) else float(width)
    return (w, height if height is not None else round(w * aspect, 3))


# ================================================================ rc settings
def has_seaborn() -> bool:
    """``True`` if seaborn is installed (it is optional; ``pip install
    evroute[plot]`` brings it, and matplotlib with it)."""
    import importlib.util

    return importlib.util.find_spec("seaborn") is not None


def require_seaborn() -> Any:
    """The :mod:`seaborn` module, or an :class:`ImportError` that says how to
    install it."""
    try:
        import seaborn as sns
    except ImportError as exc:  # pragma: no cover - depends on the installation
        raise ImportError("EVRoute's figures need seaborn: pip install 'evroute[plot]'") from exc
    return sns


def _seaborn_base(theme: str, context: str) -> dict[str, Any]:
    """seaborn's ``axes_style(theme)`` + ``plotting_context(context)`` as a
    plain dict (empty without seaborn)."""
    try:
        import seaborn as sns
    except Exception:  # pragma: no cover - depends on the installation
        return {}
    rc: dict[str, Any] = dict(sns.axes_style(theme))
    rc.update(sns.plotting_context(context))
    return rc


def rc_params(
    font: str | None = None,
    base_size: float | None = None,
    *,
    theme: str | None = None,
    context: str | None = None,
    seaborn: bool = True,
) -> dict[str, Any]:
    """Matplotlib ``rcParams`` of the paper style.

    The settings are layered: seaborn's ``axes_style(theme)`` and
    ``plotting_context(context)`` first (when seaborn is installed and
    ``seaborn`` is true), then the EVRoute overrides -- fonts, sizes, ink
    colours, thin axes, no top/right spines, Type 42 fonts in PDF.  The
    result is therefore the same clean look with or without seaborn, and
    seaborn plot functions called under it pick up the EVRoute palette.

    ``font`` is ``"sans"`` (DejaVu Sans, matplotlib's bundled font, which
    covers Turkish and the symbols used in labels) or ``"serif"`` (DejaVu
    Serif with matching math text).  ``base_size`` is the body font size in
    points (8 pt matches IEEE figure text at column width).  ``theme`` and
    ``context`` default to the values set with :func:`configure`
    (``"ticks"`` and ``"paper"``).
    """
    font = font or _CURRENT["font"]
    size = float(base_size if base_size is not None else _CURRENT["base_size"])
    theme = theme or _CURRENT["theme"]
    context = context or _CURRENT["context"]
    serif = font == "serif"
    small = size - 1.0
    rc: dict[str, Any] = _seaborn_base(theme, context) if seaborn else {}
    grid_on = theme in ("whitegrid", "darkgrid")
    rc.update({
        "font.family": "serif" if serif else "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica", "sans-serif"],
        "font.serif": ["DejaVu Serif", "STIXGeneral", "Times New Roman", "serif"],
        "mathtext.fontset": "dejavuserif" if serif else "dejavusans",
        "font.size": size,
        "axes.titlesize": size,
        "axes.labelsize": size,
        "axes.titlelocation": "left",
        "axes.titlepad": 4.0,
        "xtick.labelsize": small,
        "ytick.labelsize": small,
        "legend.fontsize": small,
        "legend.title_fontsize": small,
        "figure.titlesize": size + 1.0,
        "text.color": INK,
        "axes.labelcolor": INK,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": grid_on,
        "axes.axisbelow": True,
        "axes.facecolor": SURFACE,
        "axes.labelpad": 3.0,
        "grid.color": GRID,
        "grid.linewidth": 0.5,
        "grid.linestyle": "-",
        "xtick.color": AXIS,
        "ytick.color": AXIS,
        "xtick.labelcolor": INK_MUTED,
        "ytick.labelcolor": INK_MUTED,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.minor.width": 0.4,
        "ytick.minor.width": 0.4,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "xtick.minor.size": 1.5,
        "ytick.minor.size": 1.5,
        "xtick.major.pad": 2.0,
        "ytick.major.pad": 2.0,
        "lines.linewidth": 1.3,
        "lines.markersize": 4.0,
        "lines.solid_capstyle": "round",
        "patch.linewidth": 0.5,
        "legend.frameon": False,
        "legend.handlelength": 1.8,
        "legend.handletextpad": 0.5,
        "legend.borderaxespad": 0.3,
        "legend.columnspacing": 1.0,
        "legend.labelspacing": 0.3,
        "figure.facecolor": SURFACE,
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "savefig.facecolor": SURFACE,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.02,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "image.cmap": "cividis",
        "hatch.linewidth": 0.5,
        "patch.force_edgecolor": False,
        "xtick.direction": "out",
        "ytick.direction": "out",
    })
    if theme in ("darkgrid", "dark"):  # keep seaborn's grey canvas and white grid
        for k in ("axes.facecolor", "grid.color", "axes.spines.top", "axes.spines.right"):
            rc.pop(k, None)
        if seaborn:
            base = _seaborn_base(theme, context)
            for k in ("axes.facecolor", "grid.color"):
                if k in base:
                    rc[k] = base[k]
    try:  # colour cycle (the cycler package comes with seaborn; skipped without it)
        from cycler import cycler

        rc["axes.prop_cycle"] = cycler(color=list(CATEGORICAL))
    except ImportError:  # pragma: no cover
        pass
    return rc


def configure(
    *,
    font: str | None = None,
    base_size: float | None = None,
    theme: str | None = None,
    context: str | None = None,
) -> None:
    """Change the defaults used by :func:`paper` and :func:`apply` (and thus
    by every figure).

    >>> configure(font="serif", base_size=9, theme="whitegrid")   # doctest: +SKIP
    """
    if font is not None:
        if font not in ("sans", "serif"):
            raise ValueError("font must be 'sans' or 'serif'")
        _CURRENT["font"] = font
    if base_size is not None:
        if not float(base_size) > 0:
            raise ValueError("base_size must be positive")
        _CURRENT["base_size"] = float(base_size)
    if theme is not None:
        if theme not in THEMES:
            raise ValueError(f"theme must be one of {THEMES}")
        _CURRENT["theme"] = theme
    if context is not None:
        if context not in CONTEXTS:
            raise ValueError(f"context must be one of {CONTEXTS}")
        _CURRENT["context"] = context


def apply(
    font: str | None = None,
    base_size: float | None = None,
    *,
    theme: str | None = None,
    context: str | None = None,
) -> None:
    """Install the paper style globally.

    Calls :func:`seaborn.set_theme` with the configured style (default
    ``"ticks"``), context (default ``"paper"``), the EVRoute categorical
    palette and the EVRoute overrides of :func:`rc_params`, which seaborn
    applies last; every later plot uses them.
    """
    sns = require_seaborn()
    theme = theme or _CURRENT["theme"]
    context = context or _CURRENT["context"]
    rc = rc_params(font, base_size, theme=theme, context=context)
    sns.set_theme(context=context, style=theme, palette=list(CATEGORICAL), rc=rc)


def font_size() -> float:
    """Body font size [pt] currently in effect (inside :func:`paper`, the
    configured size), read through :func:`seaborn.plotting_context`."""
    return float(require_seaborn().plotting_context()["font.size"])


# ==================================================================== figures
#: Subplot margins and gaps of a new figure (matplotlib's defaults, which
#: seaborn's themes leave unchanged); ``bbox_inches="tight"`` in
#: :func:`save` trims the outer margins anyway.
_SUBPLOT_PARAMS: dict[str, float] = {"left": 0.125, "right": 0.9, "bottom": 0.11, "top": 0.88, "wspace": 0.2, "hspace": 0.2}


def subplots(
    nrows: int = 1,
    ncols: int = 1,
    *,
    figsize: tuple[float, float],
    sharex: bool = False,
    sharey: bool = False,
    squeeze: bool = True,
    width_ratios: Sequence[float] | None = None,
    height_ratios: Sequence[float] | None = None,
    wspace: float | None = None,
    hspace: float | None = None,
) -> tuple[Any, Any]:
    """A new figure of ``nrows x ncols`` axes, made by :class:`seaborn.FacetGrid`.

    Returns ``(figure, axes)`` like :func:`matplotlib.pyplot.subplots`: one
    axes, a 1-D array for a single row or column, else a 2-D array
    (``squeeze=False`` always gives the 2-D array).  The grid's facets are
    only the panel positions; every panel is then drawn with seaborn's
    axes-level functions.  ``figsize`` is ``(width, height)`` in inches.
    """
    sns = require_seaborn()
    import pandas as pd

    w, h = figsize
    cells = [(r, c) for r in range(nrows) for c in range(ncols)]
    panels = pd.DataFrame({"row": [r for r, _ in cells], "col": [c for _, c in cells]})
    ratios: dict[str, list[float]] = {}
    if width_ratios is not None:
        ratios["width_ratios"] = list(width_ratios)
    if height_ratios is not None:
        ratios["height_ratios"] = list(height_ratios)
    grid = sns.FacetGrid(
        panels, row="row" if nrows > 1 else None, col="col" if ncols > 1 else None, sharex=sharex, sharey=sharey,
        height=h / nrows, aspect=(w / ncols) / (h / nrows), despine=False, gridspec_kws=ratios or None,
    )
    grid.set_titles(template="")
    fig = grid.figure
    params = dict(_SUBPLOT_PARAMS)
    if wspace is not None:
        params["wspace"] = wspace
    if hspace is not None:
        params["hspace"] = hspace
    fig.subplots_adjust(**params)  # FacetGrid's own tight layout of the empty grid is replaced
    axes = grid.axes
    if squeeze:
        if axes.size == 1:
            return fig, axes[0, 0]
        if nrows == 1 or ncols == 1:
            return fig, axes.ravel()
    return fig, axes


#: Tick steps (x 10^k) of matplotlib's ``MaxNLocator`` and ``AutoLocator``.
_MAXN_STEPS: tuple[float, ...] = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)
_AUTO_STEPS: tuple[float, ...] = (1.0, 2.0, 2.5, 5.0, 10.0)


def nice_ticks(lo: float, hi: float, n: int = 5, steps: Sequence[float] = _MAXN_STEPS) -> list[float]:
    """At most ``n + 1`` round tick values inside ``[lo, hi]``: multiples of
    the smallest ``step x 10^k`` (``steps`` as in ``MaxNLocator(n)``) that
    gives at most ``n`` intervals."""
    lo, hi = float(lo), float(hi)
    if not hi > lo:
        return [lo]
    raw = (hi - lo) / max(n, 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    step = next((m * mag for m in steps if m * mag >= raw * (1 - 1e-9)), 10.0 * mag)
    first = math.ceil(lo / step - 1e-9)
    ticks = []
    k = first
    while k * step <= hi * (1 + 1e-12) + 1e-12 * step:
        ticks.append(round(k * step, 12))
        k += 1
    return ticks


def _tick_label(v: float, ticks: Sequence[float]) -> str:
    """``v`` with as many decimals as the tick spacing needs."""
    step = min((abs(b - a) for a, b in pairwise(ticks)), default=abs(v) or 1.0)
    dec = 0
    while dec < 6 and abs(round(step, dec) - step) > 1e-9 * max(step, 1.0):
        dec += 1
    return f"{v:.{dec}f}"


def colorbar(
    ax: Any,
    cmap: Any,
    vmin: float,
    vmax: float,
    *,
    label: str | None = None,
    ticks: Sequence[float] | None = None,
    ticklabels: Sequence[str] | None = None,
    orientation: str = "vertical",
    cax: Any = None,
    fraction: float = 0.06,
    pad: float = 0.02,
    aspect: float = 25.0,
    outline: float = 0.4,
) -> Any:
    """A colour scale for ``cmap`` on ``[vmin, vmax]``, drawn as a one-row
    :func:`seaborn.heatmap` of the colour ramp; returns its axes.

    Without ``cax`` the scale takes ``fraction`` of the width of ``ax``
    (``pad`` is the gap) on its right, with ``aspect`` = height / width,
    the layout of ``Figure.colorbar(ax=...)``.  ``ticks`` default to
    :func:`nice_ticks` with steps of 1, 2, 2.5 or 5 x 10^k, about one per
    three label heights.
    """
    sns = require_seaborn()
    vertical = orientation == "vertical"
    if cax is None:
        fig = ax.figure
        pos = ax.get_position()
        fw, fh = fig.get_size_inches()
        ax.set_position([pos.x0, pos.y0, pos.width * (1.0 - fraction - pad), pos.height])
        bar_w = min(pos.height * fh / aspect / fw, pos.width * fraction)
        cax = fig.add_axes((pos.x0 + pos.width * (1.0 - fraction), pos.y0, bar_w, pos.height))
    lo, hi = float(vmin), float(vmax)
    if not hi > lo:
        hi = lo + 1e-9
    n = 256
    ramp = [lo + (hi - lo) * (i + 0.5) / n for i in range(n)]
    sns.heatmap([[v] for v in reversed(ramp)] if vertical else [ramp], ax=cax, cmap=cmap, vmin=lo, vmax=hi, cbar=False,
                xticklabels=False, yticklabels=False)
    if ticks is None:
        pos = cax.get_position()
        fw, fh = cax.figure.get_size_inches()
        length_pt = 72.0 * (pos.height * fh if vertical else pos.width * fw)
        ticks = nice_ticks(lo, hi, max(2, int(length_pt / (3.0 * (font_size() - 1.0)))), steps=_AUTO_STEPS)
    else:
        ticks = [float(t) for t in ticks]
    labels = list(ticklabels) if ticklabels is not None else [_tick_label(t, ticks) for t in ticks]
    where = [((hi - t) if vertical else (t - lo)) / (hi - lo) * n for t in ticks]
    if vertical:
        cax.yaxis.tick_right()
        cax.yaxis.set_label_position("right")
        cax.set_yticks(where, labels)
        if label:
            cax.set_ylabel(label)
    else:
        cax.xaxis.tick_bottom()
        cax.set_xticks(where, labels)
        if label:
            cax.set_xlabel(label)
    cax.tick_params(length=2, width=0.5)
    for sp in cax.spines.values():
        sp.set_visible(outline > 0)
        sp.set_linewidth(outline)
    return cax


def log_colorbar_ticks(cbar: Any, lo: float, hi: float) -> None:
    """Ticks of a colour bar whose values are ``log10`` of the data (a
    logarithmic colour scale drawn by :func:`seaborn.heatmap`): powers of
    ten for every decade, or 1-2-5 steps within a single decade."""
    decades = list(range(math.ceil(lo - 1e-9), math.floor(hi + 1e-9) + 1))
    if len(decades) >= 2:
        ticks = [float(e) for e in decades]
        labels = [f"$\\mathdefault{{10^{{{e}}}}}$" for e in decades]
    else:
        vals = [m * 10.0 ** e for e in range(math.floor(lo) - 1, math.ceil(hi) + 1) for m in (1, 2, 5)]
        vals = [v for v in vals if lo - 1e-9 <= math.log10(v) <= hi + 1e-9]
        ticks = [math.log10(v) for v in vals]
        labels = [f"{v:g}" for v in vals]
    cbar.set_ticks(ticks, labels=labels)
    cbar.ax.minorticks_off()


# ===================================================================== saving
def save(
    fig: Any,
    path: str | Path,
    *,
    formats: Sequence[str] | None = None,
    dpi: int = 300,
    close: bool = False,
    transparent: bool = False,
) -> list[Path]:
    """Write ``fig`` as vector PDF and 300 dpi PNG; return the written paths.

    The suffix of ``path`` is replaced by each format, so ``"out/fig_map"``,
    ``"out/fig_map.pdf"`` and ``"out/fig_map.png"`` all write
    ``out/fig_map.pdf`` and ``out/fig_map.png``.  If ``formats`` is given it
    overrides the default ``("pdf", "png")``.  Output is reproducible: the
    PDF carries no creation date.
    """
    p = Path(path)
    if p.suffix.lower() in (".pdf", ".png", ".svg", ".eps", ".jpg", ".tif"):
        p = p.with_suffix("")
    p.parent.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for fmt in formats or ("pdf", "png"):
        fmt = fmt.lstrip(".").lower()
        target = p.with_name(p.name + "." + fmt)
        meta: dict[str, Any] | None
        if fmt == "pdf":
            meta = {"Creator": "EVRoute", "Producer": None, "CreationDate": None}
        elif fmt == "png":
            meta = {"Software": "EVRoute"}
        elif fmt == "svg":
            meta = {"Date": None}
        else:
            meta = None
        fig.savefig(
            target,
            format=fmt,
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=0.02,
            transparent=transparent,
            metadata=meta,
        )
        out.append(target)
    if close:
        _close_figure(fig)
    return out


# ================================================================= colour maps
def rgb(color: Any) -> tuple[float, float, float]:
    """``(r, g, b)`` in ``[0, 1]`` of a colour (``"#RRGGBB"`` parsed in
    plain Python, other colour specifications through seaborn)."""
    if isinstance(color, str) and color.startswith("#") and len(color) in (4, 7):
        h = color[1:]
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
    if isinstance(color, (tuple, list)) and len(color) in (3, 4):
        return (float(color[0]), float(color[1]), float(color[2]))
    r, g, b = require_seaborn().color_palette([color])[0]
    return (float(r), float(g), float(b))


def tint(color: Any, amount: float = 0.62) -> tuple[float, float, float]:
    """``color`` mixed with ``amount`` of white (pale fills behind dots)."""
    r, g, b = rgb(color)
    return (r + (1 - r) * amount, g + (1 - g) * amount, b + (1 - b) * amount)


#: Stops of the muted hypsometric tint (position, colour).
_TERRAIN_STOPS: tuple[tuple[float, str], ...] = (
    (0.00, "#CFE0C3"),
    (0.20, "#E0E7C4"),
    (0.42, "#ECE2C2"),
    (0.66, "#DDC8A6"),
    (0.86, "#CBB59E"),
    (1.00, "#BFB2A6"),
)


def terrain_cmap() -> Any:
    """Muted hypsometric tint (lowland green -> upland tan -> summit grey-brown).

    Deliberately pale, so that routes, stations and labels drawn on top keep
    their contrast; relief comes from the hill-shading, not from saturation.
    Built with :func:`seaborn.blend_palette`, which spaces its colours
    evenly: the ramp is sampled every 2 %, a grid every stop lies on, so the
    colour map is exactly the piecewise-linear ramp through the stops.
    """
    cols = []
    for i in range(51):
        t = i / 50.0
        k = max(j for j in range(len(_TERRAIN_STOPS) - 1) if _TERRAIN_STOPS[j][0] <= t + 1e-12)
        (t0, c0), (t1, c1) = _TERRAIN_STOPS[k], _TERRAIN_STOPS[min(k + 1, len(_TERRAIN_STOPS) - 1)]
        f = 0.0 if t1 <= t0 else min(max((t - t0) / (t1 - t0), 0.0), 1.0)
        a, b = rgb(c0), rgb(c1)
        cols.append(tuple(x + (y - x) * f for x, y in zip(a, b)))
    return require_seaborn().blend_palette(cols, as_cmap=True)


def sequential_cmap(color: str = OKABE_ITO["blue"], light: str = "#D4E6F4") -> Any:
    """Single-hue sequential ramp from ``light`` to a darkened ``color``.

    Used for magnitudes (settle order, battery wear): one hue, light to dark,
    starting light enough to stay visible on white
    (:func:`seaborn.blend_palette`).
    """
    r, g, b = rgb(color)
    deep = (r * 0.45, g * 0.45, b * 0.45)
    return require_seaborn().blend_palette([rgb(light), (r, g, b), deep], as_cmap=True)


# ========================================================= matplotlib bridge
# seaborn draws through matplotlib.  These three helpers are the only places
# where EVRoute reaches matplotlib directly, and none of them draws data:
# temporary rc settings (seaborn's own context managers cover only its
# style and context keys, not fonts or PDF output), closing a finished
# figure, and the white halo behind labels (a text path effect).
@contextmanager
def paper(font: str | None = None, base_size: float | None = None, **extra: Any) -> Iterator[None]:
    """Context manager: the paper style (seaborn theme + EVRoute overrides,
    see :func:`rc_params`) for everything created inside it.  ``extra``
    rcParams are applied last.  Global settings are restored on exit."""
    from matplotlib import rc_context

    rc = rc_params(font, base_size)
    rc.update(extra)
    with rc_context(rc):
        yield


def close(fig: Any = "all") -> None:
    """Release a finished figure (``"all"``: every open figure)."""
    from matplotlib import pyplot

    pyplot.close(fig)


_close_figure = close  # ``save(close=True)`` shadows the name


def halo(width: float = 2.0, color: str = "white", alpha: float = 0.85) -> list[Any]:
    """Path effect list that draws a halo behind text or lines."""
    from matplotlib.patheffects import withStroke

    return [withStroke(linewidth=width, foreground=color, alpha=alpha)]
