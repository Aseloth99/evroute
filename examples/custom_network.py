"""Build your own road network, stations and vehicle from scratch.

    python examples/custom_network.py
"""

from evroute import (
    ChargingCurve, Conditions, NetworkBuilder, Preferences, Query, Scenario, Station, Vehicle, plan,
)

b = NetworkBuilder()
a = b.add_node(0, 0, elevation=50, name="A")
m1 = b.add_node(60_000, 5_000, elevation=900, name="Pass")
s1 = b.add_node(120_000, 0, elevation=300, name="Service area")
m2 = b.add_node(180_000, -10_000, elevation=600)
z = b.add_node(260_000, 0, elevation=20, name="Z")
for u, v in [(a, m1), (m1, s1), (s1, m2), (m2, z)]:
    b.add_road(u, v, road_class="motorway")
b.add_road(a, z, road_class="primary", length=290_000)  # slower coastal road
net = b.build()

car = Vehicle(
    key="my-car", name="My car", battery_kwh=40.0, mass_kg=1500, drag_coeff=0.28,
    frontal_area_m2=2.3, rolling_coeff=0.009,
    dc_curve=ChargingCurve.from_points([(0, 60), (0.5, 60), (0.8, 30), (1.0, 8)]),
)
scenario = Scenario(
    name="custom",
    network=net,
    stations=[Station(id=0, node=s1, power_kw=120, kind="DC", plugs=4, price_per_kwh=0.5, name="Fast charger")],
    vehicle=car,
    query=Query(origin=a, destination=z, initial_soc=0.8),
    conditions=Conditions(temperature_c=10),
    preferences=Preferences(reserve_soc=0.1, min_arrival_soc=0.15),
)
for name in ["naive", "bls-astar", "cfp"]:
    print(plan(scenario, name))
