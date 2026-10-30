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
import math
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from .charging import ChargingFunction, build_charging_function
from .energy import PhysicsEnergyModel
from .network import RoadNetwork
from .queueing import (
    HIGHWAY_PROFILE,
    URBAN_PROFILE,
    WaitProfile,
    hourly_utilisation,
    station_wait_profile,
)
from .units import SECONDS_PER_DAY, SECONDS_PER_HOUR
from .vehicle import CONNECTORS, Vehicle, check_connectors, get_vehicle

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
    #: Connector standards offered (``"Type2"``, ``"CCS2"``, ``"CHAdeMO"``, ``"Type1"``,
    #: ``"CCS1"``, ``"NACS"``; :data:`evroute.vehicle.CONNECTORS`); empty: the default of
    #: the kind (AC ``Type2``, DC ``CCS2``).  Sockets of the other kind at the same site
    #: (a Type 2 point next to a DC charger) may be listed; :meth:`usable_by` ignores them.
    connectors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.connectors = tuple(self.connectors)

    @property
    def sockets(self) -> tuple[str, ...]:
        """Connector standards of the station (the kind's default if none are given)."""
        return self.connectors or (("Type2",) if self.kind == "AC" else ("CCS2",))

    def usable_by(self, connectors: Iterable[str]) -> bool:
        """Can a vehicle with these connector standards charge here?

        The station is modelled as one charger of its :attr:`kind`, so the
        vehicle needs a socket of that kind it can plug into: a standard that
        is in :attr:`sockets`, in ``connectors`` and carries the station's kind
        (:data:`evroute.vehicle.CONNECTORS`).  A Type 2 socket listed at a DC
        station does not make it usable for a car without a DC inlet: the
        charging function of the station is the DC one.
        """
        mine = set(connectors)
        return any(c in mine and self.kind in CONNECTORS.get(c, ()) for c in self.sockets)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not isinstance(self.demand_profile, str):  # a tuple of weights is written (and read) as a list
            d["demand_profile"] = [float(w) for w in self.demand_profile]
        if self.connectors:
            d["connectors"] = list(self.connectors)
        else:  # the default is not written, so earlier instances keep their exact JSON form
            del d["connectors"]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Station":
        return cls(**d)

    def wait_profile(self) -> WaitProfile:
        prof = self.demand_profile
        if isinstance(prof, str):
            prof = list(URBAN_PROFILE if prof == "urban" else HIGHWAY_PROFILE)
        return station_wait_profile(self.daily_sessions, prof, self.mean_service_min, self.plugs)

    def utilisation(self) -> list[float]:
        """Expected utilisation ``rho(h) = lambda(h) / (plugs * mu)`` of each clock hour
        (the demand of :meth:`wait_profile`; drives ``Tariff.busy_utilisation``)."""
        prof = self.demand_profile
        if isinstance(prof, str):
            prof = list(URBAN_PROFILE if prof == "urban" else HIGHWAY_PROFILE)
        return hourly_utilisation(self.daily_sessions, prof, self.mean_service_min, self.plugs)


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
    #: Every charging stop ends at this SoC or above (capped at ``max_charge_soc``),
    #: unless the vehicle arrives above it: no "+2 %" top-ups.  ``0`` disables the rule.
    min_stop_soc: float = 0.0
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
class Tariff:
    """Charging prices that depend on the state of charge and on the clock.

    Two practices make some charging sessions dearer:

    * *the 80 % rule*: charging above ``high_soc`` is slow and blocks a plug,
      so operators (and the other drivers) penalise it -- every kWh stored
      above ``high_soc x capacity`` is charged at ``high_soc_factor`` times
      the price;
    * *peak hours*: ``peak_hours`` are local clock intervals ``[start, end)``
      in hours, e.g. ``((16, 20),)``; an interval may wrap midnight
      (``(22, 6)``), and they repeat every day.  With ``busy_utilisation`` a
      station is also in its peak period during every clock hour in which the
      expected utilisation ``rho(h) = lambda(h) / (plugs x mu)`` of its queue
      model (:meth:`Station.utilisation`) is at least that value, so
      congestion pricing follows the Erlang-C demand profiles and the periods
      differ per station.

    A kWh stored at station ``s`` costs ``s.price_per_kwh`` times::

                    below high_soc     above high_soc
        off-peak    1                  high_soc_factor
        peak        peak_factor        peak_factor x (peak_high_soc_factor
                                                      if not None else high_soc_factor)

    (divided by the charging efficiency, as every price).  The period of a
    charging session is fixed when the vehicle joins the station's queue: on
    arrival, or at the end of a voluntary hold
    (:attr:`~evroute.plan.ChargeDecision.hold_until_s`).  The clock is the
    departure time of day plus the trip time, modulo 24 h -- the clock of the
    queue model.  ``Tariff()`` is neutral: every kWh costs the station price.
    """

    #: SoC above which the high-SoC tier applies.
    high_soc: float = 0.8
    #: Price factor above ``high_soc`` (off-peak).
    high_soc_factor: float = 1.0
    #: Daily peak intervals ``(start, end)`` in clock hours, ``[start, end)``.
    peak_hours: tuple[tuple[float, float], ...] = ()
    #: Price factor in the peak period (below ``high_soc``).
    peak_factor: float = 1.0
    #: High-SoC factor in the peak period (``None``: ``high_soc_factor``).
    peak_high_soc_factor: float | None = None
    #: Utilisation from which a clock hour of a station is a peak hour (``None``: off).
    busy_utilisation: float | None = None

    def __post_init__(self) -> None:
        try:  # lists (e.g. from JSON) -> tuples; anything else is reported by validate()
            self.peak_hours = tuple(tuple(iv) for iv in self.peak_hours)
        except TypeError:
            pass

    def factors(self, peak: bool) -> tuple[float, float]:
        """Price factors ``(below high_soc, above high_soc)`` of the off-peak or the peak period."""
        if not peak:
            return 1.0, self.high_soc_factor
        hi = self.high_soc_factor if self.peak_high_soc_factor is None else self.peak_high_soc_factor
        return self.peak_factor, self.peak_factor * hi

    def peak_intervals(self, utilisation: Sequence[float] | None = None) -> tuple[tuple[float, float], ...]:
        """Peak period of one station: sorted, disjoint clock intervals ``[a, b)``
        in seconds of the day (``((0, 86400),)`` all day, ``()`` never).

        ``utilisation`` holds the station's 24 hourly utilisations
        (:meth:`Station.utilisation`); it is only read with ``busy_utilisation``.
        """
        hour, day = SECONDS_PER_HOUR, SECONDS_PER_DAY
        raw: list[tuple[float, float]] = []
        for a, b in self.peak_hours:
            a, b = float(a) * hour, float(b) * hour
            if a < b:
                raw.append((a, b))
            elif a > b:  # wraps midnight
                raw.append((a, day))
                raw.append((0.0, b))
        if self.busy_utilisation is not None and utilisation is not None:
            for h, rho in enumerate(list(utilisation)[:24]):
                if rho >= self.busy_utilisation:
                    raw.append((h * hour, (h + 1) * hour))
        raw = sorted((a, b) for a, b in raw if b > a)
        merged: list[tuple[float, float]] = []
        for a, b in raw:
            if merged and a <= merged[-1][1]:
                if b > merged[-1][1]:
                    merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        return tuple(merged)

    def validate(self) -> None:
        """Raise :class:`ValueError` for a value outside the model's domain:
        ``0 < high_soc < 1``, factors ``> 0``, peak hours in ``[0, 24]``,
        ``busy_utilisation > 0``."""
        _check_number("tariff.high_soc", self.high_soc, 0.0, 1.0, open_lo=True, open_hi=True)
        _check_number("tariff.high_soc_factor", self.high_soc_factor, 0.0, open_lo=True)
        _check_number("tariff.peak_factor", self.peak_factor, 0.0, open_lo=True)
        if self.peak_high_soc_factor is not None:
            _check_number("tariff.peak_high_soc_factor", self.peak_high_soc_factor, 0.0, open_lo=True)
        if self.busy_utilisation is not None:
            _check_number("tariff.busy_utilisation", self.busy_utilisation, 0.0, open_lo=True)
        hours = self.peak_hours
        if not isinstance(hours, tuple) or not all(isinstance(iv, tuple) and len(iv) == 2 for iv in hours):
            raise ValueError(f"tariff.peak_hours must be a sequence of (start, end) pairs, got {hours!r}")
        for a, b in hours:
            _check_number("tariff.peak_hours start", a, 0.0, 24.0)
            _check_number("tariff.peak_hours end", b, 0.0, 24.0)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["peak_hours"] = [list(iv) for iv in self.peak_hours]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tariff":
        return cls(**d)


def _price_changes(intervals: tuple[tuple[float, float], ...]) -> tuple[float, ...]:
    """Clock times in ``[0, 86400)`` at which a station enters or leaves its peak period."""
    day = SECONDS_PER_DAY
    if not intervals or intervals == ((0.0, day),):
        return ()
    wrap = intervals[0][0] == 0.0 and intervals[-1][1] == day  # contiguous across midnight
    out = set()
    for a, b in intervals:
        if not (a == 0.0 and wrap):
            out.add(a)
        if b < day:
            out.add(b)
        elif not wrap:
            out.add(0.0)
    return tuple(sorted(out))


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
    #: Charging prices by state of charge and time of day (neutral by default).
    tariff: Tariff = field(default_factory=Tariff)

    def __post_init__(self) -> None:
        self._prepared: PreparedScenario | None = None
        self._prepared_key: str | None = None

    # ------------------------------------------------------------- derived
    def prepare(self) -> "PreparedScenario":
        """Return (and cache) the derived data used by all algorithms.

        Vary a scenario through the ``with_*`` copies; a field changed in
        place (``sc.query.initial_soc = 1.0``) is noticed here and the cache
        is rebuilt, so a plan never uses stale derived data.  The network is
        treated as immutable.
        """
        key = repr((self.query, self.conditions, self.preferences, self.stations, self.vehicle, self.currency,
                    self.tariff))
        if self._prepared is None or key != self._prepared_key:
            self._prepared = PreparedScenario(self)
            self._prepared_key = key
        return self._prepared

    def validate(self) -> None:
        """Raise :class:`ValueError` for a value outside the model's domain.

        Origin, destination and station nodes must be nodes of the network;
        every edge has a finite length and speed ``> 0``; SoC fractions lie
        in ``[0, 1]`` (``max_charge_soc`` in ``(0, 1]``), speed factors in
        ``(0, 1]``, ``safety_factor >= 1``, ``0.001 <= charge_step_soc <=
        1``, the departure time is finite, the temperature above absolute
        zero, the traffic factors ``> 0``, the cost weights ``>= 0``; a
        station has a power ``> 0``, at least one plug, a price, set-up time
        and daily sessions ``>= 0``, a mean session ``> 0``, the kind ``"AC"``
        or ``"DC"`` and known connector names; and the
        tariff is valid (:meth:`Tariff.validate`).  Called by
        :meth:`prepare`, so an out-of-range value is never clamped or
        wrapped silently (it used to crash or hang a planner, or to void its
        optimality).
        """
        n = self.network.num_nodes

        def is_node(v: Any) -> bool:
            return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n

        for key in ("origin", "destination"):
            v = getattr(self.query, key)
            if not is_node(v):
                raise ValueError(f"{key} {v!r} is not a node of the network (0..{n - 1})")
        net = self.network
        for e, (d, s) in enumerate(zip(net.length, net.speed_kmh)):
            if not (0.0 < d < math.inf and 0.0 < s < math.inf):  # also catches NaN
                raise ValueError(f"edge {e}: length {d!r} m and speed {s!r} km/h must be finite and > 0")
        for st in self.stations:
            if not is_node(st.node):
                raise ValueError(f"station {st.id} references unknown node {st.node!r}")
            _check_number(f"station {st.id}: power_kw", st.power_kw, 0.0, open_lo=True)
            if isinstance(st.plugs, bool) or not isinstance(st.plugs, int) or st.plugs < 1:
                raise ValueError(f"station {st.id}: plugs must be an integer >= 1, got {st.plugs!r}")
            _check_number(f"station {st.id}: price_per_kwh", st.price_per_kwh, 0.0)
            _check_number(f"station {st.id}: setup_s", st.setup_s, 0.0)
            _check_number(f"station {st.id}: daily_sessions", st.daily_sessions, 0.0)
            _check_number(f"station {st.id}: mean_service_min", st.mean_service_min, 0.0, open_lo=True)
            if st.kind not in ("AC", "DC"):
                raise ValueError(f"station {st.id}: kind must be 'AC' or 'DC', got {st.kind!r}")
            check_connectors(f"station {st.id}: connectors", st.connectors)
        q, pref, cond = self.query, self.preferences, self.conditions
        _check_number("initial_soc", q.initial_soc, 0.0, 1.0)
        _check_number("departure_s", q.departure_s)
        _check_number("temperature_c", cond.temperature_c, -273.15, open_lo=True)
        _check_number("traffic_factor", cond.traffic_factor, 0.0, open_lo=True)
        for cls, f in cond.class_speed_factor.items():
            _check_number(f"class_speed_factor[{cls!r}]", f, 0.0, open_lo=True)
        _check_number("reserve_soc", pref.reserve_soc, 0.0, 1.0)
        _check_number("min_arrival_soc", pref.min_arrival_soc, 0.0, 1.0)
        _check_number("max_charge_soc", pref.max_charge_soc, 0.0, 1.0, open_lo=True)
        _check_number("min_stop_soc", pref.min_stop_soc, 0.0, 1.0)
        _check_number("safety_factor", pref.safety_factor, 1.0)
        _check_number("charge_step_soc", pref.charge_step_soc, 1e-3, 1.0)
        for f in pref.speed_factors:
            _check_number("speed_factors", f, 0.0, 1.0, open_lo=True)
        for key in ("value_of_time_per_h", "weight_money", "weight_degradation", "soh_cost_per_pct"):
            _check_number(key, getattr(pref, key), 0.0)
        if not isinstance(self.tariff, Tariff):
            raise ValueError(f"tariff must be a Tariff, got {type(self.tariff).__name__}")
        self.tariff.validate()

    def with_(self, **changes: Any) -> "Scenario":
        """Copy with replaced top-level fields (the cache is not shared)."""
        return replace(self, **changes)

    def with_query(self, **changes: Any) -> "Scenario":
        return self.with_(query=replace(self.query, **changes))

    def with_preferences(self, **changes: Any) -> "Scenario":
        return self.with_(preferences=replace(self.preferences, **changes))

    def with_conditions(self, **changes: Any) -> "Scenario":
        return self.with_(conditions=replace(self.conditions, **changes))

    def with_tariff(self, **changes: Any) -> "Scenario":
        return self.with_(tariff=replace(self.tariff, **changes))

    # ------------------------------------------------------ serialisation
    def to_dict(self) -> dict[str, Any]:
        """JSON-ready, lossless description of the instance.

        ``Scenario.from_dict(json.loads(json.dumps(sc.to_dict())))`` rebuilds
        an instance that plans bit-identically: the network is stored
        unrounded (see :meth:`RoadNetwork.to_dict`), floats survive JSON
        exactly (``repr``), and the only normalisations are tuple -> list
        (``speed_factors``, the tariff's ``peak_hours`` and the vehicle's
        charging curve become tuples again on loading) and non-string
        ``meta`` keys -> strings.  The result is a fixed point: ``to_dict`` of
        the reloaded scenario is equal to the dict it was loaded from.  The
        ``"tariff"`` key is only written for a tariff other than ``Tariff()``
        (a missing key loads as the neutral tariff), so instances without a
        tariff keep their exact earlier JSON form.
        """
        prefs = asdict(self.preferences)
        prefs["speed_factors"] = [float(f) for f in self.preferences.speed_factors]
        if prefs["min_stop_soc"] == 0.0:  # not written when off: earlier instances keep their JSON form
            del prefs["min_stop_soc"]
        d = {
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
        }
        if self.tariff != Tariff():
            d["tariff"] = self.tariff.to_dict()
        d["meta"] = _json_ready(self.meta)
        return d

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
            tariff=Tariff.from_dict(d["tariff"]) if "tariff" in d else Tariff(),
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

    Stations: ``stations``, ``cf``, ``setup_s``, ``wait_profiles`` ... hold
    every station of the scenario (index = position in ``scenario.stations``),
    but ``station_at`` and ``is_station_node`` only know the stations the
    vehicle can plug into (``station_usable[s]``, :meth:`Station.usable_by`):
    the planners find stations through them, so they never stop at a station
    without a connector of the vehicle, and the bounds (``p_max``,
    ``cf_variants`` ...) only cover the usable stations.

    Charging prices (:class:`Tariff`): ``cf[s]`` carries the money of
    station ``s`` outside its peak period (of its only period if it is in
    its peak period all day), so code that is not tariff-aware is right
    whenever prices do not depend on the clock.  With an active high-SoC
    tier every charging function has a breakpoint at ``high_soc x capacity``
    (same power on both sides).  ``cf_peak[s]`` has the same breakpoints,
    powers and wear and the peak money (``cf_peak is cf`` when no station has
    a peak price).  ``money_cf(s, t)`` is the function whose money applies to
    a session joining the queue at trip time ``t``; ``tariff_time_dependent``
    tells whether any price depends on the clock, and ``hold_candidates(s,
    t)`` lists the price changes a vehicle could wait for.
    """

    def __init__(self, scenario: Scenario) -> None:
        scenario.validate()
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
        self.e_charge_cap = pref.max_charge_soc * q
        #: lowest energy a charging stop may end at (``0``: no minimum); see Preferences.min_stop_soc
        self.e_stop_min = min(pref.min_stop_soc, pref.max_charge_soc) * q
        self.e_init = sc.query.initial_soc * q
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
        #: can the vehicle charge at station ``i``?  (Station.usable_by; the indices of
        #: ``stations``, ``cf`` ... keep counting every station, usable or not)
        self.station_usable: list[bool] = [st.usable_by(veh.connectors) for st in sc.stations]
        #: usable stations per node: the only way the planners find stations, so a
        #: station without a connector of the vehicle is never a stop
        self.station_at: dict[int, list[int]] = {}
        for i, st in enumerate(sc.stations):  # nodes checked by Scenario.validate
            if self.station_usable[i]:
                self.station_at.setdefault(st.node, []).append(i)
        self.is_station_node = [False] * net.num_nodes
        for v in self.station_at:
            self.is_station_node[v] = True

        # ------------------------------------------------- charging prices
        tariff = sc.tariff
        self.tariff = tariff
        busy = tariff.busy_utilisation is not None
        #: peak period of every station: clock intervals [a, b) in seconds of the day
        self.peak_intervals: list[tuple[tuple[float, float], ...]] = [
            tariff.peak_intervals(st.utilisation() if busy else None) for st in sc.stations
        ]
        self._peak_starts = [[a for a, _ in ivs] for ivs in self.peak_intervals]
        off_lo, off_hi = tariff.factors(False)
        pk_lo, pk_hi = tariff.factors(True)
        any_peak = any(self.peak_intervals)
        peak_money = any_peak and (pk_lo != off_lo or pk_hi != off_hi)
        # the high-SoC tier needs a breakpoint where the price per kWh changes
        tiered = off_hi != off_lo or (any_peak and pk_hi != pk_lo)
        e_high = tariff.high_soc * q

        dc_factor = veh.dc_temperature_factor(cond.temperature_c, cond.preconditioning)
        dc_curve = veh.dc_curve_effective  # capped at veh.dc_max_kw (the curve itself without a cap)
        aging = veh.aging
        ac_curve = (0.0, 0.95, 1.0)
        cache: dict[tuple, tuple[ChargingFunction, ChargingFunction]] = {}
        self.cf: list[ChargingFunction] = []
        cf_peak: list[ChargingFunction] = []
        for i, st in enumerate(sc.stations):
            key = (st.kind, round(st.power_kw, 6), round(st.price_per_kwh, 6))
            if key not in cache:
                if st.kind == "AC":
                    p_ac = min(veh.ac_max_kw, st.power_kw)
                    soc_breaks, powers, eff, tf = ac_curve, (p_ac, p_ac * 0.5), veh.charge_eff_ac, 1.0
                else:
                    soc_breaks, powers = dc_curve.soc_breaks, dc_curve.power_kw
                    eff, tf = veh.charge_eff_dc, dc_factor
                temp = cond.temperature_c

                def soh_fn(c_rate: float, soc: float, _t: float = temp) -> float:
                    return aging.fade_pct_per_kwh(q, c_rate, soc, _t)

                f_off = f_pk = build_charging_function(
                    q, soc_breaks, powers, st.power_kw, tf, st.price_per_kwh, eff, soh_fn
                )
                if tiered or peak_money:
                    # split the bin at the high-SoC energy (same power and wear) and
                    # price every bin: price x factor / charging efficiency
                    base = f_off.split(e_high) if tiered else f_off
                    price = st.price_per_kwh

                    def money(lo: float, hi: float, _f: ChargingFunction = base, _p: float = price,
                              _eff: float = eff) -> list[float]:
                        return [_p * (hi if e >= e_high else lo) / _eff for e in _f.e_breaks[:-1]]

                    f_off = base.with_money(money(off_lo, off_hi)) if tiered else base
                    f_pk = base.with_money(money(pk_lo, pk_hi)) if peak_money else f_off
                cache[key] = (f_off, f_pk)
            f_off, f_pk = cache[key]
            # a station that is in its peak period all day has no other price
            self.cf.append(f_pk if self.peak_intervals[i] == ((0.0, SECONDS_PER_DAY),) else f_off)
            cf_peak.append(f_pk)
        #: charging functions with the peak money (``is self.cf`` when no peak price exists)
        self.cf_peak: list[ChargingFunction] = cf_peak if peak_money else self.cf
        # the bounds below only need the stations the vehicle can use (a minimum or maximum
        # over fewer functions is still a valid bound, and a tighter one)
        usable = self.station_usable
        usable_cf = self.cf if all(usable) else [f for f, u in zip(self.cf, usable) if u]
        #: every charging function whose money can apply at a usable station (bounds take
        #: the minimum over them)
        self.cf_variants: list[ChargingFunction] = usable_cf if self.cf_peak is self.cf else usable_cf + [
            f for f, ivs, u in zip(cf_peak, self.peak_intervals, usable, strict=True) if ivs and u
        ]
        #: clock times [s of the day] at which the price of each station changes
        self.price_changes: list[tuple[float, ...]] = [
            _price_changes(ivs) if peak_money else () for ivs in self.peak_intervals
        ]
        #: ``True`` if the price of some station depends on the time of day
        self.tariff_time_dependent = any(self.price_changes)
        self.p_max = max((f.max_power_kw for f in usable_cf), default=1.0)
        self.min_money_rate = min((min(f.money_per_kwh) for f in self.cf_variants), default=0.0)
        self.min_soh_rate = min((min(f.soh_pct_per_kwh) for f in usable_cf), default=0.0)
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
        step = pref.charge_step_soc  # at least 0.001 (Scenario.validate)
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

    def is_peak(self, station: int, trip_s: float) -> bool:
        """Is ``station`` in its peak period ``trip_s`` seconds after departure?"""
        if not self.peak_intervals[station]:
            return False
        return self._peak_at_clock(station, (self.departure_s + trip_s) % SECONDS_PER_DAY)

    def _peak_at_clock(self, station: int, clock_s: float) -> bool:
        i = bisect_right(self._peak_starts[station], clock_s) - 1
        return i >= 0 and clock_s < self.peak_intervals[station][i][1]

    def money_cf(self, station: int, trip_s: float) -> ChargingFunction:
        """Charging function whose money applies to a session at ``station`` that
        joins the queue ``trip_s`` seconds after departure (``cf`` or ``cf_peak``)."""
        if self.cf_peak is self.cf or not self.price_changes[station]:
            return self.cf[station]
        return self.cf_peak[station] if self.is_peak(station, trip_s) else self.cf[station]

    def hold_candidates(self, station: int, trip_s: float) -> list[float]:
        """Sorted trip times in ``(trip_s, trip_s + 24 h]`` at which the price of
        ``station`` changes (``[]`` if it does not depend on the clock).

        A vehicle that waits until one of them joins the queue in the new price
        period: float rounding of the clock is corrected so that
        :meth:`is_peak` already reports the new period at the returned time.
        """
        changes = self.price_changes[station]
        if not changes:
            return []
        clock = (self.departure_s + trip_s) % SECONDS_PER_DAY
        out = []
        for x in changes:
            d = x - clock
            if d <= 0.0:
                d += SECONDS_PER_DAY
            h = trip_s + d
            new = self._peak_at_clock(station, x)
            for _ in range(10_000):  # a few ulps at most
                if h > trip_s and self.is_peak(station, h) == new:
                    break
                h = math.nextafter(h, math.inf)
            out.append(h)
        out.sort()
        return out

    def drive_cost(self, time_s: float, energy_kwh: float) -> float:
        """Generalised cost of a drive segment (time + driving wear)."""
        return self.vot_per_s * time_s + self.w_deg * self.drive_soh_per_kwh * max(energy_kwh, 0.0)

    def charge_cost(
        self,
        station: int,
        e_from: float,
        e_to: float,
        charge_s: float,
        wait_s: float,
        *,
        join_s: float | None = None,
        hold_s: float = 0.0,
    ) -> float:
        """Generalised cost of one charging stop including waiting/setup.

        ``join_s`` is the trip time at which the vehicle joins the queue (it
        selects the price period, default: the off-peak money of ``cf``) and
        ``hold_s`` a voluntary hold before that, paid at the value of time.
        """
        f = self.cf[station]
        fm = f if join_s is None else self.money_cf(station, join_s)
        return (
            self.vot_per_s * (hold_s + charge_s + wait_s + self.setup_s[station])
            + self.w_money * fm.money(e_from, e_to)
            + self.w_deg * f.soh(e_from, e_to)
        )

    def edge_traverse(self, e: int, k: int, energy: float) -> float:
        """Battery energy after driving edge ``e`` at level ``k`` (regen capped)."""
        return min(energy - self.edge_energy[k][e], self.capacity)

    def soc(self, energy_kwh: float) -> float:
        return energy_kwh / self.capacity

    def stations_note(self) -> str:
        """``"12 station(s)"``, or ``"12 station(s), 5 usable with the vehicle's
        connectors"`` when the vehicle cannot plug in everywhere (for messages)."""
        n, k = len(self.stations), sum(self.station_usable)
        return f"{n} station(s)" if k == n else f"{n} station(s), {k} usable with the vehicle's connectors"

    def summary(self) -> dict[str, Any]:
        return {
            "nodes": self.net.num_nodes,
            "edges": self.net.num_edges,
            "stations": len(self.stations),
            "stations_usable": sum(self.station_usable),
            "capacity_kwh": round(self.capacity, 3),
            "speed_levels": list(self.speed_factors),
            "p_max_kw": round(self.p_max, 1),
        }


def _check_number(
    name: str, value: Any, lo: float = -math.inf, hi: float = math.inf, *, open_lo: bool = False, open_hi: bool = False
) -> None:
    """Raise :class:`ValueError` unless ``value`` is a finite number in ``[lo, hi]``
    (``(lo, hi]`` with ``open_lo``, ``[lo, hi)`` with ``open_hi``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if value < lo or (open_lo and value == lo) or value > hi or (open_hi and value == hi):
        if hi == math.inf:
            raise ValueError(f"{name} must be {'>' if open_lo else '>='} {lo:g}, got {value!r}")
        raise ValueError(
            f"{name} must lie in {'(' if open_lo else '['}{lo:g}, {hi:g}{')' if open_hi else ']'}, got {value!r}"
        )


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
