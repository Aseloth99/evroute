"""Scenario generators (synthetic networks, case studies, external data).

* :mod:`~evroute.generators.basic` -- small jittered grids for tests;
* :mod:`~evroute.generators.turkey` -- the Turkey intercity case study;
* :mod:`~evroute.generators.synthetic` -- scalable benchmark families
  (synthetic country, random geometric graphs, the didactic corridor);
* :mod:`~evroute.generators.osm` -- optional OpenStreetMap/CSV adapters;
* :mod:`~evroute.generators.presets` -- the registry of named scenarios.
"""

from .basic import grid_network, grid_scenario, place_stations
from .osm import from_networkx, load_osm, scenario_from_osm, stations_from_csv
from .presets import get_scenario, list_scenarios, register_scenario
from .synthetic import (
    corridor_network,
    corridor_scenario,
    country_network,
    country_scenario,
    random_geometric_network,
    random_geometric_scenario,
)
from .turkey import (
    find_place,
    road_distance_km,
    turkey_network,
    turkey_places,
    turkey_scenario,
    turkey_stations,
    turkey_summary,
)

__all__ = [
    "corridor_network",
    "corridor_scenario",
    "country_network",
    "country_scenario",
    "find_place",
    "from_networkx",
    "get_scenario",
    "grid_network",
    "grid_scenario",
    "list_scenarios",
    "load_osm",
    "place_stations",
    "random_geometric_network",
    "random_geometric_scenario",
    "register_scenario",
    "road_distance_km",
    "scenario_from_osm",
    "stations_from_csv",
    "turkey_network",
    "turkey_places",
    "turkey_scenario",
    "turkey_stations",
    "turkey_summary",
]
