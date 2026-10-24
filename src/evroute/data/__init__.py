"""Bundled data files of EVRoute.

``turkey_places.csv``
    Cities, towns, mountain passes and landmarks of the Turkey intercity case
    study (approximate WGS84 coordinates and elevations).
``turkey_roads.csv``
    The main intercity road skeleton between those places (road class,
    indicative road number, terrain, traffic level, an optional ``detour``
    winding factor and waypoints).

Both files are read by :mod:`evroute.generators.turkey`, whose module
docstring documents provenance, accuracy and every modelling assumption.
They are plain UTF-8 CSV so that they can be reviewed and edited in any
spreadsheet program.  Use :func:`read_text` to load a file independently of
how the package was installed.
"""

from __future__ import annotations

from importlib import resources


def read_text(name: str) -> str:
    """Return the UTF-8 content of the bundled data file ``name``."""
    return resources.files(__name__).joinpath(name).read_text(encoding="utf-8")


__all__ = ["read_text"]
