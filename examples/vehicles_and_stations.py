"""Vehicles of 1.0 (LFP, DC power limit, connectors) and stations from operator data.

    python examples/vehicles_and_stations.py

1) the same trip with an NMC and an LFP car, at 20 °C and at -10 °C;
2) a car whose fast charging is limited to 50 kW;
3) a car without a DC inlet and a CHAdeMO car: they skip the chargers they
   cannot plug into;
4) stations from an OCPI 2.2.1 ``locations`` answer (here a tiny handmade
   one; in practice the operator's feed, fetched with your credentials).
"""

from evroute import plan
from evroute.generators.presets import get_scenario
from evroute.station_sources import stations_from_ocpi, with_stations
from evroute.vehicle import get_vehicle

trip = get_scenario("turkey-karaman-istanbul")

# 1) chemistry: the LFP pack is cheap to wear but slow to charge when it is cold
for key in ("compact50", "hatch51_lfp"):
    for temp in (20.0, -10.0):
        sc = trip.with_(vehicle=get_vehicle(key)).with_conditions(temperature_c=temp, preconditioning=False)
        r = plan(sc, "bls-astar")
        print(f"{key:12s} {temp:5.0f} °C  {r.total_time_s / 3600:5.2f} h  charging {r.charge_time_s / 60:4.0f} min  "
              f"wear {r.soh_total_pct:.4f} %SoH")

# 2) a DC power limit
fast = trip.with_(vehicle=get_vehicle("sedan60_lfp"))
slow = trip.with_(vehicle=get_vehicle("sedan60_lfp").with_(dc_max_kw=50))
print("no limit:", plan(fast, "cfp-astar"))
print("50 kW:   ", plan(slow, "cfp-astar"))

# 3) connectors: an AC-only car never stops at a DC charger
for key in ("city23_ac", "hatch40_chademo"):
    r = plan(trip.with_(vehicle=get_vehicle(key)), "bls-astar")
    print(key, r, [f"{s.station_kind} {s.station_power_kw:.0f} kW" for s in r.stops])

# 4) operator data: one OCPI location with a DC and an AC charge point near Çumra
osm = get_scenario("turkey-osm-karaman-konya")
locations = [{
    "country_code": "TR", "party_id": "XYZ", "id": "EXAMPLE-1", "name": "Example site",
    "coordinates": {"latitude": "37.4855", "longitude": "32.7040"},
    "evses": [
        {"evse_id": "TR*XYZ*E1", "status": "AVAILABLE",
         "connectors": [{"standard": "IEC_62196_T2_COMBO", "power_type": "DC", "max_electric_power": 150000}]},
        {"evse_id": "TR*XYZ*E2", "status": "CHARGING",
         "connectors": [{"standard": "IEC_62196_T2", "power_type": "AC_3_PHASE", "max_voltage": 230,
                         "max_amperage": 32}]},
    ],
}]
imported = stations_from_ocpi(locations, osm.network, default_price={"AC": 9.99, "DC": 14.24})
print(imported.summary(), imported.availability)
print(plan(with_stations(osm, imported), "bls-astar"))
