"""Algorithm registry.

Planners are imported lazily so that a broken or heavy optional module never
prevents the others from loading.

>>> from evroute.algorithms import available
>>> "bls-astar" in available()
True
"""

from __future__ import annotations

import importlib
from typing import Any

from ..plan import RoutePlan
from ..scenario import PreparedScenario, Scenario
from .base import Planner

#: name -> (module, class).  Order is the order used in reports.
REGISTRY: dict[str, tuple[str, str]] = {
    "naive": ("evroute.algorithms.naive", "NaivePlanner"),
    "greedy": ("evroute.algorithms.greedy", "GreedyPlanner"),
    "station-graph": ("evroute.algorithms.station_graph", "StationGraphPlanner"),
    "genetic": ("evroute.algorithms.genetic", "GeneticPlanner"),
    "bls": ("evroute.algorithms.labels", "LabelSettingPlanner"),
    "bls-astar": ("evroute.algorithms.labels", "LabelAStarPlanner"),
    "cfp": ("evroute.algorithms.cfp", "CFPPlanner"),
    "cfp-astar": ("evroute.algorithms.cfp", "CFPAStarPlanner"),
    "pareto": ("evroute.algorithms.pareto", "ParetoPlanner"),
}

DEFAULT_ALGORITHM = "bls-astar"


def available() -> list[str]:
    return list(REGISTRY)


def planner_class(name: str) -> type[Planner]:
    try:
        module, cls = REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown algorithm {name!r}; choose from {available()}") from None
    return getattr(importlib.import_module(module), cls)


def get_planner(name: str = DEFAULT_ALGORITHM, **options: Any) -> Planner:
    return planner_class(name)(**options)


def plan(
    scenario: Scenario | PreparedScenario,
    algorithm: str = DEFAULT_ALGORITHM,
    *,
    trace: bool = False,
    **options: Any,
) -> RoutePlan:
    """Plan a route for ``scenario`` with the named algorithm."""
    return get_planner(algorithm, **options).plan(scenario, trace=trace)


def describe() -> list[dict[str, Any]]:
    """Metadata of every algorithm that imports cleanly."""
    out = []
    for name in REGISTRY:
        try:
            out.append(planner_class(name).info())
        except Exception as exc:  # pragma: no cover - diagnostics only
            out.append({"name": name, "error": repr(exc)})
    return out


__all__ = ["REGISTRY", "DEFAULT_ALGORITHM", "Planner", "available", "describe", "get_planner", "plan", "planner_class"]
