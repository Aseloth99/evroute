"""Physics-based energy consumption model.

For an edge of length ``d`` with elevation change ``dh`` driven at constant
speed ``v`` the tractive force is the classic longitudinal-dynamics sum

.. math::

    F = \\underbrace{c_{rr}\\, m g \\cos\\theta}_{\\text{rolling}}
      + \\underbrace{\\tfrac12 \\rho\\, c_d A\\, v_{air}|v_{air}|}_{\\text{aerodynamic}}
      + \\underbrace{m g \\sin\\theta}_{\\text{grade}}

with ``v_air = v + headwind``.  Battery power is ``F v / eta_drive`` when
traction is positive and ``max(F v * eta_regen, -P_regen_max)`` when the car
recuperates (anything beyond the regeneration limit is lost in the friction
brakes).  Two corrections make the model useful on real road networks:

* **Stop-and-go**: urban road classes have ``stops_per_km`` full stops.  Each
  stop costs the kinetic energy ``0.5 m v^2`` minus what regeneration wins
  back, plus a time penalty for decelerating, idling and accelerating.
* **Auxiliaries**: a constant base load plus temperature-dependent HVAC is
  drawn for the whole edge duration.

This is the same family of model as Fiori, Ahn & Rakha (2016, Applied
Energy 168) without the instantaneous drive-cycle terms, which constant-speed
edges cannot resolve.  Any other model can be plugged in by implementing
:class:`EnergyModel`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .network import ROAD_CLASSES, RoadNetwork
from .units import G, J_PER_KWH, air_density, kmh_to_ms

if TYPE_CHECKING:  # pragma: no cover
    from .scenario import Conditions
    from .vehicle import Vehicle


@dataclass(frozen=True)
class Traversal:
    """Cost of driving one edge at one speed."""

    time_s: float
    energy_kwh: float
    speed_ms: float
    #: Breakdown in kWh: rolling, aero, grade, stop-and-go, auxiliary,
    #: drivetrain loss and energy lost because regeneration was saturated.
    rolling_kwh: float = 0.0
    aero_kwh: float = 0.0
    grade_kwh: float = 0.0
    stops_kwh: float = 0.0
    aux_kwh: float = 0.0


class EnergyModel(Protocol):
    """Anything that can price an edge at a speed (e.g. a learned model)."""

    def traverse(self, net: RoadNetwork, e: int, speed_factor: float) -> Traversal:  # pragma: no cover
        ...


class PhysicsEnergyModel:
    """Longitudinal-dynamics consumption model (see module docs)."""

    #: Time lost per full stop in addition to the braking/accelerating
    #: phases: queueing at the light, junction negotiation [s].
    idle_per_stop_s = 8.0
    #: Comfortable acceleration used to derive the stop time penalty [m/s^2].
    comfort_accel = 1.5

    def __init__(self, vehicle: "Vehicle", conditions: "Conditions") -> None:
        self.vehicle = vehicle
        self.conditions = conditions
        self.aux_kw = vehicle.aux_power_kw(conditions.temperature_c)

    def speed_ms(self, net: RoadNetwork, e: int, speed_factor: float) -> float:
        c = self.conditions
        cls_factor = c.class_speed_factor.get(net.road_class[e], 1.0)
        v_kmh = min(net.speed_kmh[e] * c.traffic_factor * cls_factor, self.vehicle.max_speed_kmh)
        return max(kmh_to_ms(v_kmh * speed_factor), 1.0)

    def headwind_ms(self, net: RoadNetwork, e: int) -> float:
        c = self.conditions
        if c.wind_speed_ms == 0.0:
            return 0.0
        rel = math.radians(net.bearing(e) - c.wind_from_deg)
        return c.wind_speed_ms * math.cos(rel)

    def traverse(self, net: RoadNetwork, e: int, speed_factor: float = 1.0) -> Traversal:
        veh, cond = self.vehicle, self.conditions
        d = net.length[e]
        dh = net.elevation[net.head[e]] - net.elevation[net.tail[e]]
        dh = max(-d * 0.999, min(d * 0.999, dh))
        sin_t = dh / d
        cos_t = math.sqrt(1.0 - sin_t * sin_t)
        v = self.speed_ms(net, e, speed_factor)
        mid_elev = 0.5 * (net.elevation[net.head[e]] + net.elevation[net.tail[e]])
        rho = air_density(cond.temperature_c, mid_elev)
        m = veh.mass_kg + cond.payload_kg
        v_air = v + self.headwind_ms(net, e)

        f_roll = veh.rolling_coeff * m * G * cos_t
        f_aero = 0.5 * rho * veh.drag_coeff * veh.frontal_area_m2 * v_air * abs(v_air)
        f_grade = m * G * sin_t
        f_total = f_roll + f_aero + f_grade
        t_move = d / v
        p_wheel = f_total * v
        if p_wheel >= 0.0:
            p_batt = p_wheel / veh.drivetrain_eff
        else:
            p_batt = max(p_wheel * veh.regen_eff, -veh.max_regen_kw * 1000.0)
        e_move = p_batt * t_move

        stops = ROAD_CLASSES[net.road_class[e]].stops_per_km * d / 1000.0
        e_stop_each = 0.5 * m * v * v * (1.0 / veh.drivetrain_eff - veh.regen_eff)
        t_stop_each = v / self.comfort_accel + self.idle_per_stop_s
        e_stops = stops * e_stop_each
        t = t_move + stops * t_stop_each
        e_aux = self.aux_kw * 1000.0 * t

        total_j = e_move + e_stops + e_aux
        share = 1.0 / veh.drivetrain_eff if p_wheel >= 0.0 else 0.0
        return Traversal(
            time_s=t,
            energy_kwh=total_j / J_PER_KWH,
            speed_ms=v,
            rolling_kwh=f_roll * d * share / J_PER_KWH,
            aero_kwh=f_aero * d * share / J_PER_KWH,
            grade_kwh=(f_grade * d * share if p_wheel >= 0 else e_move) / J_PER_KWH,
            stops_kwh=e_stops / J_PER_KWH,
            aux_kwh=e_aux / J_PER_KWH,
        )


def consumption_curve(
    vehicle: "Vehicle",
    conditions: "Conditions",
    speeds_kmh: list[float],
    grade: float = 0.0,
    length_m: float = 1000.0,
) -> list[float]:
    """kWh/100 km on a synthetic straight road for each speed (for plots)."""
    from .network import NetworkBuilder

    out = []
    for s in speeds_kmh:
        b = NetworkBuilder()
        a = b.add_node(0.0, 0.0, 0.0)
        z = b.add_node(length_m, 0.0, grade * length_m)
        b.add_edge(a, z, length=length_m * math.sqrt(1 + grade * grade), speed_kmh=s, road_class="motorway")
        net = b.build()
        model = PhysicsEnergyModel(vehicle, conditions)
        tr = model.traverse(net, 0, 1.0)
        out.append(tr.energy_kwh / (net.length[0] / 1000.0) * 100.0)
    return out
