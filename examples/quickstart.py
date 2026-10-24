"""EVRoute quick start: plan Karaman -> Istanbul and compare algorithms.

    python examples/quickstart.py
"""

from evroute import plan
from evroute.generators.presets import get_scenario

scenario = get_scenario("turkey-karaman-istanbul")

# 1) the flagship exact planner
route = plan(scenario, "bls-astar")
print(route)
for i, stop in enumerate(route.stops, 1):
    print(
        f"  stop {i}: {stop.station_name:<28} {stop.station_kind} {stop.station_power_kw:>4.0f} kW  "
        f"queue {stop.wait_s / 60:4.1f} min  charge {stop.charge_s / 60:4.1f} min  "
        f"SoC {100 * stop.soc_in:3.0f}% -> {100 * stop.soc_out:3.0f}%"
    )

# 2) the same trip in winter, minimising generalised cost instead of time
winter = scenario.with_conditions(temperature_c=-5).with_preferences(objective="cost")
print(plan(winter, "bls-astar"))

# 3) every algorithm side by side
for name in ["naive", "greedy", "station-graph", "genetic", "bls", "bls-astar", "cfp", "cfp-astar"]:
    print(plan(scenario, name))
