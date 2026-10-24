"""EVRoute -- exact, battery-, queue- and degradation-aware EV route planning.

Quick start::

    from evroute import plan
    from evroute.generators import grid_scenario

    scenario = grid_scenario(seed=1)
    route = plan(scenario, "bls-astar")
    print(route)

See ``README.md`` and ``docs/`` for the model and the algorithms.
"""

from __future__ import annotations

from .algorithms import available, describe, get_planner, plan
from .evaluate import evaluate
from .network import NetworkBuilder, RoadNetwork
from .plan import ChargeDecision, PlanDecision, RoutePlan
from .scenario import Conditions, PreparedScenario, Preferences, Query, Scenario, Station
from .vehicle import VEHICLES, ChargingCurve, Vehicle, get_vehicle

__version__ = "1.0.0"

__all__ = [
    "VEHICLES",
    "ChargeDecision",
    "ChargingCurve",
    "Conditions",
    "NetworkBuilder",
    "PlanDecision",
    "Preferences",
    "PreparedScenario",
    "Query",
    "RoadNetwork",
    "RoutePlan",
    "Scenario",
    "Station",
    "Vehicle",
    "available",
    "describe",
    "evaluate",
    "get_planner",
    "get_vehicle",
    "plan",
    "__version__",
]
