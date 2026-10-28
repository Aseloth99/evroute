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
spreadsheet program.

``osm_turkey/``
    The real-data Turkey network: OpenStreetMap roads (motorway, trunk,
    primary and their ramps) with SRTM elevations, the public OSM charging
    stations, places and charging list prices, read by
    :mod:`evroute.generators.osm_turkey`.  These files are **ODbL 1.0**,
    © OpenStreetMap contributors (``osm_turkey/README.md`` and
    ``osm_turkey/DATA_LICENSE.md``), unlike the MIT-licensed code.

Use :func:`read_text` and :func:`read_bytes` to load a file independently
of how the package was installed; a name may contain ``/`` to reach into a
subfolder (``"osm_turkey/meta.json"``).
"""

from __future__ import annotations

from importlib import resources
from typing import Any


def _resource(name: str) -> Any:
    """The :class:`importlib.resources` traversable of ``name`` (``/`` separates subfolders)."""
    res = resources.files(__name__)
    for part in name.split("/"):
        res = res.joinpath(part)
    return res


def read_text(name: str) -> str:
    """Return the UTF-8 content of the bundled data file ``name``."""
    return _resource(name).read_text(encoding="utf-8")


def read_bytes(name: str) -> bytes:
    """Return the raw content of the bundled data file ``name`` (e.g. a ``.csv.gz``)."""
    return _resource(name).read_bytes()


__all__ = ["read_bytes", "read_text"]
