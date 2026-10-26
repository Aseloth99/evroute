"""Route plans: what algorithms decide and what the evaluator measures.

An algorithm outputs a :class:`PlanDecision` -- the edge sequence, a speed
level per edge and the charging stops with their *target energies*.  The
independent evaluator (:mod:`evroute.evaluate`) turns a decision into a fully
measured :class:`RoutePlan`.  Keeping these apart means every number shown
in a figure, table or animation was produced by the same code, whichever
algorithm made the decision.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .units import format_duration


@dataclass(frozen=True)
class ChargeDecision:
    """Charge at ``station`` when standing at node index ``position``.

    ``position`` indexes the plan's node sequence: ``0`` is the origin and
    ``len(edges)`` the destination.  The battery is charged up to
    ``target_kwh`` (no-op if it already holds that much).
    """

    position: int
    station: int
    target_kwh: float


@dataclass
class PlanDecision:
    edges: list[int]
    levels: list[int]
    charges: list[ChargeDecision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.levels) != len(self.edges):
            raise ValueError("one speed level per edge required")
        self.charges = sorted(self.charges, key=lambda c: c.position)


@dataclass
class ChargeStop:
    """A measured charging stop."""

    station: int
    node: int
    position: int
    arrival_s: float
    wait_s: float
    setup_s: float
    charge_s: float
    energy_in_kwh: float
    energy_out_kwh: float
    soc_in: float
    soc_out: float
    grid_energy_kwh: float
    money: float
    soh_pct: float
    power_kw: float
    station_power_kw: float
    station_kind: str
    station_name: str = ""

    @property
    def departure_s(self) -> float:
        return self.arrival_s + self.wait_s + self.setup_s + self.charge_s

    @property
    def dwell_s(self) -> float:
        return self.wait_s + self.setup_s + self.charge_s


@dataclass
class SearchStats:
    """Effort counters reported by an algorithm."""

    algorithm: str = ""
    runtime_s: float = 0.0
    settled: int = 0
    generated: int = 0
    pushes: int = 0
    pruned: int = 0
    peak_queue: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SearchTrace:
    """Order in which nodes were first settled (for search animations)."""

    nodes: list[int] = field(default_factory=list)
    keys: list[float] = field(default_factory=list)
    _seen: set[int] = field(default_factory=set, repr=False)

    def visit(self, node: int, key: float) -> None:
        if node not in self._seen:
            self._seen.add(node)
            self.nodes.append(node)
            self.keys.append(key)

    def to_dict(self, max_events: int | None = None) -> dict[str, Any]:
        nodes, keys = self.nodes, self.keys
        if max_events is not None and len(nodes) > max_events:
            stride = len(nodes) / max_events
            idx = [int(i * stride) for i in range(max_events)]
            nodes = [nodes[i] for i in idx]
            keys = [keys[i] for i in idx]
        return {"nodes": nodes, "keys": [round(k, 2) for k in keys]}


@dataclass
class RoutePlan:
    """A fully evaluated route (feasible or not)."""

    algorithm: str
    feasible: bool
    edges: list[int]
    levels: list[int]
    nodes: list[int]
    stops: list[ChargeStop]
    node_arrive_s: list[float]
    node_depart_s: list[float]
    node_energy_arrive: list[float]
    node_energy_depart: list[float]
    node_dist_m: list[float]
    capacity_kwh: float
    currency: str = "EUR"
    violations: list[str] = field(default_factory=list)
    message: str = ""
    # totals
    total_time_s: float = math.inf
    drive_time_s: float = 0.0
    wait_time_s: float = 0.0
    setup_time_s: float = 0.0
    charge_time_s: float = 0.0
    distance_m: float = 0.0
    energy_consumed_kwh: float = 0.0
    energy_regen_kwh: float = 0.0
    energy_charged_kwh: float = 0.0
    grid_energy_kwh: float = 0.0
    money: float = 0.0
    soh_drive_pct: float = 0.0
    soh_charge_pct: float = 0.0
    degradation_money: float = 0.0
    generalized_cost: float = math.inf
    arrival_soc: float = 0.0
    min_soc: float = 0.0
    stranded_at_m: float | None = None
    objective: str = "time"
    objective_value: float = math.inf
    internal_objective: float | None = None
    stats: SearchStats | None = None
    trace: SearchTrace | None = None
    alternatives: list["RoutePlan"] = field(default_factory=list)
    #: The decision this plan was evaluated from (re-evaluation, Monte Carlo).
    decision: PlanDecision | None = None

    # -------------------------------------------------------------- views
    @property
    def num_stops(self) -> int:
        return len(self.stops)

    @property
    def soh_total_pct(self) -> float:
        return self.soh_drive_pct + self.soh_charge_pct

    def soc_profile(self) -> list[tuple[float, float, float]]:
        """``(distance_m, trip_time_s, soc)`` samples including charging jumps."""
        out = []
        q = self.capacity_kwh
        for i in range(len(self.nodes)):
            out.append((self.node_dist_m[i], self.node_arrive_s[i], self.node_energy_arrive[i] / q))
            if self.node_depart_s[i] > self.node_arrive_s[i] + 1e-9 or self.node_energy_depart[i] != self.node_energy_arrive[i]:
                out.append((self.node_dist_m[i], self.node_depart_s[i], self.node_energy_depart[i] / q))
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "feasible": self.feasible,
            "total_time": format_duration(self.total_time_s),
            "total_time_s": round(self.total_time_s, 1),
            "drive_time_s": round(self.drive_time_s, 1),
            "charge_time_s": round(self.charge_time_s, 1),
            "wait_time_s": round(self.wait_time_s + self.setup_time_s, 1),
            "distance_km": round(self.distance_m / 1000.0, 2),
            "stops": self.num_stops,
            "energy_kwh": round(self.energy_consumed_kwh - self.energy_regen_kwh, 2),
            "charged_kwh": round(self.energy_charged_kwh, 2),
            "money": round(self.money, 2),
            "soh_pct": round(self.soh_total_pct, 5),
            "generalized_cost": round(self.generalized_cost, 2),
            "arrival_soc": round(self.arrival_soc, 4),
            "min_soc": round(self.min_soc, 4),
            "runtime_ms": round(self.stats.runtime_s * 1000.0, 2) if self.stats else None,
            "settled": self.stats.settled if self.stats else None,
        }

    def to_dict(self, *, trace_events: int | None = 20000) -> dict[str, Any]:
        d: dict[str, Any] = {
            k: v
            for k, v in self.__dict__.items()
            if k not in ("stops", "stats", "trace", "alternatives", "decision")
        }
        for k in ("node_arrive_s", "node_depart_s", "node_dist_m"):
            d[k] = [round(x, 2) for x in d[k]]
        for k in ("node_energy_arrive", "node_energy_depart"):
            d[k] = [round(x, 5) for x in d[k]]
        d = _finite_or_none(d)
        d["stops"] = [asdict(s) for s in self.stops]
        d["stats"] = self.stats.to_dict() if self.stats else None
        d["trace"] = self.trace.to_dict(trace_events) if self.trace else None
        d["alternatives"] = [a.to_dict(trace_events=0) for a in self.alternatives]
        d["summary"] = _finite_or_none(self.summary())  # an infeasible plan has infinite time and cost
        return d

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if not self.feasible:
            return f"[{self.algorithm}] infeasible: {self.message or '; '.join(self.violations)}"
        s = self.summary()
        return (
            f"[{self.algorithm}] {s['total_time']} | {s['distance_km']} km | {s['stops']} stop(s) | "
            f"arrive {100 * self.arrival_soc:.1f}% | {self.money:.2f} {self.currency}"
        )


def _finite_or_none(d: dict[str, Any]) -> dict[str, Any]:
    """``d`` with non-finite float values replaced by ``None`` (standard JSON has no Infinity)."""
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()}
