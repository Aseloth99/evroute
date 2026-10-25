"""Paper style shared by every static figure and animation of EVRoute.

This module is the single place that decides how EVRoute *looks*:

* **one colour per algorithm**, taken from the colour-blind-safe palette of
  Okabe & Ito (2008, *Color Universal Design*), so that "BLS-A* is blue" holds
  in every figure, table, animation and -- ideally -- in the web viewer;
* a **secondary encoding** per algorithm (marker shape, and a dashed line for
  heuristics without an optimality guarantee) so that the figures survive
  grey-scale printing and colour-vision deficiencies;
* fonts and sizes for **IEEE single-column (3.5 in) and double-column
  (7.16 in)** figures, with TrueType (Type 42) fonts embedded in PDF/PS
  output as IEEE PDF eXpress requires;
* a :func:`save` helper that writes a vector PDF and a 300 dpi PNG at once.

Everything at module level is plain Python; matplotlib is imported lazily
inside the functions that need it, so the palette can be imported by code
that must stay dependency-free (e.g. the web exporter).

Typical use::

    from evroute.viz import style
    style.configure(font="serif")          # default for all EVRoute figures
    with style.paper():                    # or: style.apply() globally
        fig, ax = plt.subplots(figsize=style.figsize("single"))
        ax.plot(x, y, **style.line_kw("bls-astar"))
    style.save(fig, "figures/fig_soc")     # -> fig_soc.pdf + fig_soc.png
"""

from __future__ import annotations

import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

__all__ = [
    "OKABE_ITO",
    "ALGORITHM_COLORS",
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
    "line_kw",
    "categorical",
    "figsize",
    "rc_params",
    "configure",
    "apply",
    "paper",
    "save",
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

#: Stable colour per algorithm (registry name -> hex).  The pure Okabe-Ito
#: yellow (#F0E442) has a contrast of only 1.3:1 on white paper, so CFP-A*
#: uses a darkened yellow of the same hue; the naive baseline is a neutral
#: grey because it is shown only to illustrate the problem.
ALGORITHM_COLORS: dict[str, str] = {
    "naive": "#7F7F7F",
    "greedy": OKABE_ITO["orange"],
    "station-graph": OKABE_ITO["sky_blue"],
    "genetic": OKABE_ITO["reddish_purple"],
    "bls": OKABE_ITO["bluish_green"],
    "bls-astar": OKABE_ITO["blue"],
    "cfp": OKABE_ITO["vermillion"],
    "cfp-astar": "#B59F00",
    "pareto": OKABE_ITO["black"],
}

#: Lighter variants for dark backgrounds (slides, the viewer's dark theme).
ALGORITHM_COLORS_DARK: dict[str, str] = {
    "naive": "#A3A3A3",
    "greedy": "#F5B731",
    "station-graph": "#7CC8F2",
    "genetic": "#E191BE",
    "bls": "#21C495",
    "bls-astar": "#3D9EE6",
    "cfp": "#F0782E",
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

#: Short labels for legends, table rows and the animated vehicles.
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

#: Fallback colours for unknown algorithm names (chosen by a stable hash).
_FALLBACK: tuple[str, ...] = (
    OKABE_ITO["blue"],
    OKABE_ITO["vermillion"],
    OKABE_ITO["bluish_green"],
    OKABE_ITO["reddish_purple"],
    OKABE_ITO["orange"],
    OKABE_ITO["sky_blue"],
    "#B59F00",
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
    "#B59F00",
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

#: Default style settings used by :func:`paper` (change with :func:`configure`).
_CURRENT: dict[str, Any] = {"font": "sans", "base_size": 8.0}


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
    """Keyword arguments for ``ax.plot`` that draw algorithm ``name`` in style."""
    kw: dict[str, Any] = {
        "color": algorithm_color(name),
        "linestyle": algorithm_linestyle(name),
        "label": algorithm_title(name),
        "solid_capstyle": "round",
        "dash_capstyle": "butt",
    }
    kw.update(overrides)
    return kw


def categorical(i: int) -> str:
    """The ``i``-th colour of the non-algorithm categorical order (cycled
    only past 8 entities, which a single figure should never show)."""
    return CATEGORICAL[i % len(CATEGORICAL)]


def figsize(width: str | float = "single", aspect: float = 0.68, height: float | None = None) -> tuple[float, float]:
    """``(w, h)`` in inches; ``width`` is ``"single"``, ``"double"``, ... or inches."""
    w = WIDTHS[width] if isinstance(width, str) else float(width)
    return (w, height if height is not None else round(w * aspect, 3))


# ================================================================ rc settings
def rc_params(font: str | None = None, base_size: float | None = None) -> dict[str, Any]:
    """Matplotlib ``rcParams`` of the paper style.

    ``font`` is ``"sans"`` (DejaVu Sans, matplotlib's bundled font, which
    covers Turkish and the symbols used in labels) or ``"serif"`` (DejaVu
    Serif with matching math text).  ``base_size`` is the body font size in
    points (8 pt matches IEEE figure text at column width).
    """
    font = font or _CURRENT["font"]
    size = float(base_size if base_size is not None else _CURRENT["base_size"])
    serif = font == "serif"
    small = size - 1.0
    rc: dict[str, Any] = {
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
        "axes.grid": False,
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
    }
    try:  # colour cycle (needs matplotlib; skipped when it is absent)
        from cycler import cycler

        rc["axes.prop_cycle"] = cycler(color=list(CATEGORICAL))
    except ImportError:  # pragma: no cover
        pass
    return rc


def configure(*, font: str | None = None, base_size: float | None = None) -> None:
    """Change the defaults used by :func:`paper` (and thus by every figure).

    >>> configure(font="serif", base_size=9)   # doctest: +SKIP
    """
    if font is not None:
        if font not in ("sans", "serif"):
            raise ValueError("font must be 'sans' or 'serif'")
        _CURRENT["font"] = font
    if base_size is not None:
        _CURRENT["base_size"] = float(base_size)


def apply(font: str | None = None, base_size: float | None = None) -> None:
    """Install the paper style globally (``matplotlib.rcParams.update``)."""
    import matplotlib

    matplotlib.rcParams.update(rc_params(font, base_size))


@contextmanager
def paper(font: str | None = None, base_size: float | None = None, **extra: Any) -> Iterator[None]:
    """Context manager: the paper style for everything created inside it."""
    import matplotlib

    rc = rc_params(font, base_size)
    rc.update(extra)
    with matplotlib.rc_context(rc):
        yield


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
        import matplotlib.pyplot as plt

        plt.close(fig)
    return out


# ================================================================= colour maps
def terrain_cmap() -> Any:
    """Muted hypsometric tint (lowland green -> upland tan -> summit grey-brown).

    Deliberately pale, so that routes, stations and labels drawn on top keep
    their contrast; relief comes from the hill-shading, not from saturation.
    """
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list(
        "evroute_terrain",
        [
            (0.00, "#CFE0C3"),
            (0.20, "#E0E7C4"),
            (0.42, "#ECE2C2"),
            (0.66, "#DDC8A6"),
            (0.86, "#CBB59E"),
            (1.00, "#BFB2A6"),
        ],
    )


def sequential_cmap(color: str = OKABE_ITO["blue"], light: str = "#D4E6F4") -> Any:
    """Single-hue sequential ramp from ``light`` to a darkened ``color``.

    Used for magnitudes (settle order, battery wear): one hue, light to dark,
    starting light enough to stay visible on white.
    """
    from matplotlib.colors import LinearSegmentedColormap, to_rgb

    r, g, b = to_rgb(color)
    deep = (r * 0.45, g * 0.45, b * 0.45)
    return LinearSegmentedColormap.from_list("evroute_seq", [light, color, deep])


def halo(width: float = 2.0, color: str = "white", alpha: float = 0.85) -> list[Any]:
    """Path effect list that draws a halo behind text or lines."""
    import matplotlib.patheffects as pe

    return [pe.withStroke(linewidth=width, foreground=color, alpha=alpha)]
