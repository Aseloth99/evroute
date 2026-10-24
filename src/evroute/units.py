"""Physical constants and unit conventions.

Every quantity inside ``evroute`` uses one fixed set of units so that no
function ever has to guess:

==============  =========  ===================================================
quantity        unit       notes
==============  =========  ===================================================
distance        m          node coordinates are projected metres
elevation       m          above sea level
time            s          trip time is measured from departure
time of day     s          seconds after local midnight, wrapped mod 86 400
speed           m/s        road speed limits are *stored* in km/h for humans
energy          kWh        battery energy, consumption, charged energy
power           kW         charging power, auxiliary loads
mass            kg
money           currency   whatever ``Scenario.currency`` says (EUR, TRY, ...)
SoC             fraction   0.0 ... 1.0 of the *usable* capacity
==============  =========  ===================================================

``kW * s -> kWh`` therefore needs a division by 3600, which is what
:data:`SECONDS_PER_HOUR` is for.
"""

from __future__ import annotations

import math

#: Standard gravity [m/s^2].
G = 9.80665
#: Joules in one kilowatt-hour.
J_PER_KWH = 3.6e6
#: Seconds in one hour.
SECONDS_PER_HOUR = 3600.0
#: Seconds in one day.
SECONDS_PER_DAY = 86400.0
#: Air density at 15 degC, sea level [kg/m^3].
AIR_DENSITY_SEA_LEVEL = 1.225
#: Mean Earth radius [m] (IUGG).
EARTH_RADIUS_M = 6_371_008.8

#: Tolerance used when comparing floating point energies [kWh].
ENERGY_EPS = 1e-9
#: Tolerance used when comparing floating point times [s].
TIME_EPS = 1e-6


def kmh_to_ms(v_kmh: float) -> float:
    return v_kmh / 3.6


def ms_to_kmh(v_ms: float) -> float:
    return v_ms * 3.6


def joule_to_kwh(j: float) -> float:
    return j / J_PER_KWH


def kwh_to_joule(kwh: float) -> float:
    return kwh * J_PER_KWH


def charge_seconds(energy_kwh: float, power_kw: float) -> float:
    """Seconds needed to move ``energy_kwh`` at a constant ``power_kw``."""
    return energy_kwh / power_kw * SECONDS_PER_HOUR


def air_density(temperature_c: float, elevation_m: float = 0.0) -> float:
    """Dry-air density from the ideal gas law and the barometric formula.

    Accurate to a few percent between -30 and +45 degC and 0..3000 m, which
    is far below the uncertainty of the drag coefficient itself.
    """
    t_k = 273.15 + temperature_c
    pressure = 101_325.0 * math.exp(-elevation_m / 8434.5)
    return pressure / (287.058 * t_k)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points [m]."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def project_equirectangular(
    lat: float, lon: float, lat0: float, lon0: float
) -> tuple[float, float]:
    """Project WGS84 to local metres around a reference point.

    Good to well under 1 % over a country-sized area, which is all the
    routing code needs (true edge lengths are stored separately).
    """
    x = math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def format_duration(seconds: float) -> str:
    """``5025.3 -> '1 h 23 min'``; used in reports and the CLI."""
    if seconds != seconds or seconds == math.inf:
        return "inf"
    total_min = int(round(seconds / 60.0))
    h, m = divmod(total_min, 60)
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min"


def format_clock(time_of_day_s: float) -> str:
    """Seconds after midnight -> ``'HH:MM'`` (wraps past midnight)."""
    t = int(round(time_of_day_s)) % int(SECONDS_PER_DAY)
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}"
