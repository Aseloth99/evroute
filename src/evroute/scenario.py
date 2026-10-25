"""Problem instances: network + stations + vehicle + conditions + query.

:class:`Scenario` is the user-facing, serialisable description of a routing
problem.  :meth:`Scenario.prepare` turns it into a :class:`PreparedScenario`
holding every derived quantity the algorithms need (per-edge time/energy for
each speed level, one exact :class:`~evroute.charging.ChargingFunction` per
station, waiting-time profiles, battery bounds in kWh).  All algorithms *and*
the independent evaluator read the same prepared data, so an algorithm's
internal objective can be checked against the evaluator to floating-point
precision.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from .charging import ChargingFunction, build_charging_function
from .energy import PhysicsEnergyModel
from .network import RoadNetwork
from .queueing import HIGHWAY_PROFILE, URBAN_PROFILE, WaitProfile, station_wait_profile
from .units import SECONDS_PER_DAY, SECONDS_PER_HOUR
from .vehicle import Vehicle, get_vehicle

Objective = Literal["time", "cost"]


@dataclass
class Station:
    """A charging site attached to a network node."""

    id: int
    node: int
    power_kw: float
    kind: Literal["AC", "DC"] = "DC"
    plugs: int = 4
    price_per_kwh: float = 0.45
    name: str = ""
    operator: str = "generic"
    #: Average number of charging sessions per day (drives the queue model).
    daily_sessions: float = 30.0
    #: ``"highway"`` or ``"urban"`` demand shape, or 24 explicit weights.
    demand_profile: str | list[float] = "highway"
    #: Mean session length of other users [min].
    mean_service_min: float = 35.0
    #: Parking, plugging in, authenticating and unplugging [s].
    setup_s: float = 180.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not isinstance(self.demand_profile, str):  # a tuple of weights is written (and read) as a list
            d["demand_profile"] = [float(w) for w in self.demand_profile]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Station":
        return cls(**d)

    def wait_profile(self) -> WaitProfile:
        prof = self.demand_profile
        if isinstance(prof, str):
            prof = list(URBAN_PROFILE if prof == "urban" else HIGHWAY_PROFILE)
        return station_wait_profile(self.daily_sessions, prof, self.mean_service_min, self.plugs)


@dataclass
class Conditions:
    """Environment of the trip."""

    temperature_c: float = 20.0
    wind_speed_ms: float = 0.0
    #: Compass direction the wind blows *from* (0 = north wind).
    wind_from_deg: float = 0.0
    payload_kg: float = 75.0
    #: Global multiplier on free-flow speeds (congestion < 1).
    traffic_factor: float = 1.0
    #: Optional per-road-class speed multipliers, e.g. ``{"urban": 0.7}``.
    class_speed_factor: dict[str, float] = field(default_factory=dict)
    #: Battery pre-conditioned before fast charging (removes cold derating).
    preconditioning: bool = True


@dataclass
class Preferences:
    """What the driver wants and how the planner may achieve it."""

    objective: Objective = "time"
    #: Value of time used by the generalised cost [currency / h].
    value_of_time_per_h: float = 15.0
    #: Weight of energy money in the generalised cost.
    weight_money: float = 1.0
    #: Weight of battery-wear money in the generalised cost.
    weight_degradation: float = 1.0
    #: Money value of one percentage point of state of health.
    soh_cost_per_pct: float = 450.0
    #: SoC that must never be undercut while driving.
    reserve_soc: float = 0.10
    #: SoC required on arrival at the destination.
    min_arrival_soc: float = 0.10
    #: Highest SoC the planner may charge to.
    max_charge_soc: float = 1.0
    #: Allowed speed multipliers per edge (``(1.0, 0.9, 0.8)`` = eco driving).
    speed_factors: tuple[float, ...] = (1.0,)
    #: Include expected queueing delay at stations.
    queue_aware: bool = True
    #: Waits depend on arrival time (``False``: snapshot at departure time).
    time_dependent_waits: bool = True
    #: Grid of charging targets for label algorithms [SoC fraction].
    charge_step_soc: float = 0.02
    #: Planning consumption multiplier (risk-averse > 1).
    safety_factor: float = 1.0


@dataclass
class Query:
    origin: int
    destination: int
    initial_soc: float = 0.9
    #: Departure time of day [s after midnight].
    departure_s: float = 8 * 3600.0


@dataclass
class Scenario:
    """A complete, serialisable EV routing instance."""

    name: str
    network: RoadNetwork
    stations: list[Station]
    vehicle: Vehicle
    query: Query
    conditions: Conditions = field(default_factory=Conditions)
    preferences: Preferences = field(default_factory=Preferences)
    currency: str = "EUR"
    description: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._prepared: PreparedScenario | None = None

    # ------------------------------------------------------------- derived
    def prepare(self) -> "PreparedScenario":
        """Return (and cache) the derived data used by all algorithms."""
        if self._prepared is None:
            self._prepared = PreparedScenario(self)
        return self._prepared

    def with_(self, **changes: Any) -> "Scenario":
        """Copy with replaced top-level fields (the cache is not shared)."""
        return replace(self, **changes)

    def with_query(self, **changes: Any) -> "Scenario":
        return self.with_(query=replace(self.query, **changes))

    def with_preferences(self, **changes: Any) -> "Scenario":
        return self.with_(preferences=replace(self.preferences, **changes))

    def with_conditions(self, **changes: Any) -> "Scenario":
        return self.with_(conditions=replace(self.conditions, **changes))

    # ------------------------------------------------------ serialisation
    def to_dict(self) -> dict[str, Any]:
        """JSON-ready, lossless description of the instance.

        ``Scenario.from_dict(json.loads(json.dumps(sc.to_dict())))`` rebuilds
        an instance that plans bit-identically: the network is stored
        unrounded (see :meth:`RoadNetwork.to_dict`), floats survive JSON
        exactly (``repr``), and the only normalisations are tuple -> list
        (``speed_factors`` and the vehicle's charging curve become tuples
        again on loading) and non-string ``meta`` keys -> strings.  The
        result is a fixed point: ``to_dict`` of the reloaded scenario is equal
        to the dict it was loaded from.
        """
        prefs = asdict(self.preferences)
        prefs["speed_factors"] = [float(f) for f in self.preferences.speed_factors]
        return {
            "format": "evroute-scenario/1",
            "name": self.name,
            "description": self.description,
            "currency": self.currency,
            "network": self.network.to_dict(),
            "stations": [s.to_dict() for s in self.stations],
            "vehicle": self.vehicle.to_dict(),
            "query": asdict(self.query),
            "conditions": asdict(self.conditions),
            "preferences": prefs,
            "meta": _json_ready(self.meta),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scenario":
        veh = d["vehicle"]
        vehicle = get_vehicle(veh) if isinstance(veh, str) else Vehicle.from_dict(veh)
        prefs = dict(d.get("preferences", {}))
        if "speed_factors" in prefs:
            prefs["speed_factors"] = tuple(prefs["speed_factors"])
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            currency=d.get("currency", "EUR"),
            network=RoadNetwork.from_dict(d["network"]),
            stations=[Station.from_dict(s) for s in d["stations"]],
            vehicle=vehicle,
            query=Query(**d["query"]),
            conditions=Conditions(**d.get("conditions", {})),
            preferences=Preferences(**prefs),
            meta=d.get("meta", {}),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


class PreparedScenario:
    """Derived, read-only data shared by every algorithm.

    Battery quantities are in kWh of *usable* capacity at the trip
    temperature:

    ``capacity``      full battery
    ``e_min``         driving reserve (never undercut at any node)
    ``e_charge_cap``  highest energy the planner may charge to
    ``e_init``        energy at departure
    ``e_dest_min``    energy required at the destination

    Edges offer one or more *speed levels*.  ``edge_time[k][e]`` and
    ``edge_energy[k][e]`` give time [s] and planning energy [kWh] at level
    ``k``; ``edge_options[e]`` lists the non-dominated ``(k, time, energy,
    drive_cost)`` tuples algorithms should iterate over.
    """

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        sc = scenario
        net = sc.network
        veh = sc.vehicle
        cond = sc.conditions
        pref = sc.preferences
        self.net = net
        self.vehicle = veh
        self.preferences = pref
        self.conditions = cond
        self.objective: Objective = pref.objective
        self.currency = sc.currency

        # ---------------------------------------------------- battery bounds
        q = veh.usable_capacity_kwh(cond.temperature_c)
        self.capacity = q
        self.e_min = pref.reserve_soc * q
        self.e_charge_cap = min(1.0, pref.max_charge_soc) * q
        self.e_init = min(max(sc.query.initial_soc, 0.0), 1.0) * q
        # arriving at the destination is still driving: the reserve applies too
        self.e_dest_min = max(pref.min_arrival_soc * q, self.e_min)
        self.origin = sc.query.origin
        self.destination = sc.query.destination
        self.departure_s = sc.query.departure_s

        # ------------------------------------------------------ cost weights
        self.vot_per_s = pref.value_of_time_per_h / SECONDS_PER_HOUR
        self.w_money = pref.weight_money
        self.w_deg = pref.weight_degradation * pref.soh_cost_per_pct  # per SoH %

        # ------------------------------------------------- per-edge physics
        model = PhysicsEnergyModel(veh, cond)
        self.energy_model = model
        self.speed_factors = tuple(pref.speed_factors) or (1.0,)
        sf = pref.safety_factor
        drive_soh = veh.aging.fade_pct_per_kwh(q, 0.3, 0.5, cond.temperature_c)
        self.drive_soh_per_kwh = drive_soh
        m = net.num_edges
        self.edge_time: list[list[float]] = []
        self.edge_energy: list[list[float]] = []
        self.edge_energy_nominal: list[list[float]] = []
        self.edge_speed: list[list[float]] = []
        for f in self.speed_factors:
            times, energies, nominal, speeds = [], [], [], []
            for e in range(m):
                tr = model.traverse(net, e, f)
                times.append(tr.time_s)
                nominal.append(tr.energy_kwh)
                energies.append(tr.energy_kwh * sf if tr.energy_kwh > 0 else tr.energy_kwh / sf)
                speeds.append(tr.speed_ms)
            self.edge_time.append(times)
            self.edge_energy.append(energies)
            self.edge_energy_nominal.append(nominal)
            self.edge_speed.append(speeds)
        self.edge_options: list[tuple[tuple[int, float, float, float], ...]] = []
        for e in range(m):
            opts = []
            for k in range(len(self.speed_factors)):
                t, c = self.edge_time[k][e], self.edge_energy[k][e]
                opts.append((k, t, c, self.drive_cost(t, c)))
            self.edge_options.append(_pareto_options(opts))

        # ------------------------------------------------------- stations
        self.stations = sc.stations
        self.station_at: dict[int, list[int]] = {}
        for i, st in enumerate(sc.stations):
            if not 0 <= st.node < net.num_nodes:
                raise ValueError(f"station {st.id} references unknown node {st.node}")
            self.station_at.setdefault(st.node, []).append(i)
        self.is_station_node = [False] * net.num_nodes
        for v in self.station_at:
            self.is_station_node[v] = True

        dc_factor = veh.dc_temperature_factor(cond.temperature_c, cond.preconditioning)
        aging = veh.aging
        ac_curve = (0.0, 0.95, 1.0)
        cache: dict[tuple, ChargingFunction] = {}
        self.cf: list[ChargingFunction] = []
        for st in sc.stations:
            key = (st.kind, round(st.power_kw, 6), round(st.price_per_kwh, 6))
            if key not in cache:
                if st.kind == "AC":
                    p_ac = min(veh.ac_max_kw, st.power_kw)
                    soc_breaks, powers, eff, tf = ac_curve, (p_ac, p_ac * 0.5), veh.charge_eff_ac, 1.0
                else:
                    soc_breaks, powers = veh.dc_curve.soc_breaks, veh.dc_curve.power_kw
                    eff, tf = veh.charge_eff_dc, dc_factor
                temp = cond.temperature_c

                def soh_fn(c_rate: float, soc: float, _t: float = temp) -> float:
                    return aging.fade_pct_per_kwh(q, c_rate, soc, _t)

                cache[key] = build_charging_function(
                    q, soc_breaks, powers, st.power_kw, tf, st.price_per_kwh, eff, soh_fn
                )
            self.cf.append(cache[key])
        self.p_max = max((f.max_power_kw for f in self.cf), default=1.0)
        self.min_money_rate = min((min(f.money_per_kwh) for f in self.cf), default=0.0)
        self.min_soh_rate = min((min(f.soh_pct_per_kwh) for f in self.cf), default=0.0)
        self.setup_s = [float(st.setup_s) for st in sc.stations]

        # ---------------------------------------------------------- waits
        self.wait_profiles: list[WaitProfile] = []
        for st in sc.stations:
            if not pref.queue_aware:
                prof = WaitProfile.constant(0.0)
            else:
                prof = st.wait_profile()
                if not pref.time_dependent_waits:
                    prof = WaitProfile.constant(prof(self.departure_s))
            self.wait_profiles.append(prof)
        self.time_dependent = pref.queue_aware and pref.time_dependent_waits

        # -------------------------------------------- charging target grid
        step = max(pref.charge_step_soc, 1e-3)
        grid = set()
        s = step
        while s < 1.0 + 1e-9:
            grid.add(round(min(s, 1.0) * q, 9))
            s += step
        self.target_grid = sorted(g for g in grid if g <= self.e_charge_cap + 1e-9)
        if not self.target_grid or self.target_grid[-1] < self.e_charge_cap - 1e-9:
            self.target_grid.append(self.e_charge_cap)

    # ------------------------------------------------------------ helpers
    def time_of_day(self, trip_s: float) -> float:
        return (self.departure_s + trip_s) % SECONDS_PER_DAY

    def wait_s(self, station: int, trip_s: float) -> float:
        """Expected queueing delay when arriving ``trip_s`` after departure."""
        return self.wait_profiles[station](self.departure_s + trip_s)

    def drive_cost(self, time_s: float, energy_kwh: float) -> float:
        """Generalised cost of a drive segment (time + driving wear)."""
        return self.vot_per_s * time_s + self.w_deg * self.drive_soh_per_kwh * max(energy_kwh, 0.0)

    def charge_cost(self, station: int, e_from: float, e_to: float, charge_s: float, wait_s: float) -> float:
        """Generalised cost of one charging stop including waiting/setup."""
        f = self.cf[station]
        return (
            self.vot_per_s * (charge_s + wait_s + self.setup_s[station])
            + self.w_money * f.money(e_from, e_to)
            + self.w_deg * f.soh(e_from, e_to)
        )

    def edge_traverse(self, e: int, k: int, energy: float) -> float:
        """Battery energy after driving edge ``e`` at level ``k`` (regen capped)."""
        return min(energy - self.edge_energy[k][e], self.capacity)

    def soc(self, energy_kwh: float) -> float:
        return energy_kwh / self.capacity

    def summary(self) -> dict[str, Any]:
        return {
            "nodes": self.net.num_nodes,
            "edges": self.net.num_edges,
            "stations": len(self.stations),
            "capacity_kwh": round(self.capacity, 3),
            "speed_levels": list(self.speed_factors),
            "p_max_kw": round(self.p_max, 1),
        }


def _json_ready(obj: Any) -> Any:
    """Copy of ``obj`` in the form :func:`json.loads` returns it (tuples -> lists,
    dictionary keys -> strings), so that ``to_dict`` output is a JSON fixed point."""
    if isinstance(obj, dict):
        # json writes int/float/bool/None keys as 1, 1.5, true, null -- exactly json.dumps(k)
        return {k if isinstance(k, str) else json.dumps(k): _json_ready(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_ready(v) for v in obj]
    return obj


def _pareto_options(
    opts: list[tuple[int, float, float, float]]
) -> tuple[tuple[int, float, float, float], ...]:
    """Drop speed levels that are both slower and hungrier than another."""
    keep = []
    for o in opts:
        dominated = any(
            (p[1] <= o[1] and p[2] <= o[2] and p[3] <= o[3]) and (p[1] < o[1] or p[2] < o[2] or p[3] < o[3])
            for p in opts
        )
        if not dominated:
            keep.append(o)
    keep.sort(key=lambda o: o[1])
    return tuple(keep)
