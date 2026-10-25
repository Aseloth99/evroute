"""Adversarial cross-validation of every planner -- a permanent regression suite.

Thousands of seeded random instances are thrown at all algorithms and every
answer is checked against the others, against the independent evaluator,
against brute force and against *metamorphic* relations (an equivalent
instance must give the same optimum, a strictly easier one a no worse optimum).
Each instance draws its own generator (jittered grid 3x3..14x14, random
geometric graph, mountain corridor, tiny random digraph), vehicle preset,
temperature (-15..40 degC), initial SoC (0.05..0.95, also below the reserve
at a station origin), reserve / arrival requirement (0..0.3), charging cap
(0.6..1), speed levels, objective, queue model (off / static / time
dependent), departure time (often near the demand peaks or midnight), safety
factor (1..1.3) and charging-target step (0.01..0.1); stacked stations
(several at one node) and stations at the origin or destination are added on
purpose.

Invariants (ids as in ``tests/fuzz_report.md``)

* **I1 evaluator** -- every plan replays identically through
  :func:`~evroute.evaluate.evaluate` and through the tiny first-principles
  replay of this file; ``check_objective`` holds; exact planners report the
  evaluated objective (1e-6 relative).
* **I2 A* = Dijkstra order** -- ``bls == bls-astar`` (tight and basic
  potential) and ``cfp == cfp-astar`` (tight and basic) on every instance
  (both families claim exactness for every option combination: BLS for FIFO
  waits, CFP with its default ``exact_switching=True``).
* **I3 grid order** -- CFP (continuous charging) is never slower than BLS
  (``+1e-6`` s), with static waits (required) and with time-dependent waits
  (claimed by CFP); BLS on a finer target grid that contains a coarser one is
  never worse, in both ``targets`` modes; BLS targets lie on the target set.
* **I4 Pareto** -- fastest member = BLS(time), cheapest generalised cost =
  BLS(cost), members feasible and mutually non-dominated.
* **I5 lower bound** -- no feasible plan of *any* planner (heuristics, BLS
  under the cost objective, Pareto members) is faster than CFP.
* **I6 feasibility agreement** -- if any planner finds a feasible plan, every
  exact planner does (modulo ``max_labels``).
* **I7 robustness** -- Monte Carlo with zero noise reproduces ``evaluate``;
  adaptive targets never strand more often; a plan made with a safety factor
  stays feasible (and no slower) under nominal consumption, and a larger
  safety factor never yields a faster optimum.
* **I8 serialisation** -- scenario and plan round trips through JSON are lossless:
  the reloaded scenario prepares bit-identical data and every plan (also one
  rebuilt from ``RoutePlan.to_dict`` alone) replays to the identical objective.
* **I9 brute force** -- on tiny digraphs (<= 7 nodes, <= 2 stations) an
  exhaustive enumeration of walks (each node at most twice) x up to two stops
  per node visit x targets equals BLS, its Pareto front equals the planner's
  front, and CFP is no slower than the best one-stop-per-visit route.
* **I10 metamorphic** -- relabelling nodes/edges/stations, duplicating a
  station, adding a slower parallel road, moving the departure without
  queues: same optimum; more initial energy, an extra station, an extra speed
  level, a lower reserve / arrival requirement, no queues, a higher charging
  cap, safety factor 1: no worse optimum.
* **I11 local search** -- random feasible neighbours of the BLS-A* / CFP-A*
  optima and of Pareto members (other targets, dropped / added / moved stops,
  other speed levels, random detours) are never better (dominating).

Besides, every plan's per-node bookkeeping is checked (I1), the A* potentials
are checked for consistency on random states (I2), option relations are
checked (``targets``, ``exact_switching``, ``trace``, determinism), and a
matrix of odd inputs (cap below the reserve, empty battery, origin =
destination, unreachable destination, departure after 24 h, ...) must not
crash or disagree.

Tiers: the default (fast) tier checks ~180 instances in about 20 s (budget: 90 s);
``@pytest.mark.slow`` checks thousands but runs only when asked for (``pytest
-m slow tests/test_crossval.py`` or ``EVROUTE_SLOW=1``).  Bugs found by this
suite are kept as ``xfail(strict=True)`` reproductions at the end of the file
(and become plain regression tests once fixed); ``tests/fuzz_report.md``
summarises the suite, its coverage and the bugs.
"""

from __future__ import annotations

import json
import math
import os
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Any, Callable

import pytest

from evroute.algorithms import get_planner
from evroute.algorithms.labels import charging_targets
from evroute.evaluate import TOL_KWH, check_objective, evaluate
from evroute.generators import grid_scenario
from evroute.generators.synthetic import corridor_scenario, random_geometric_scenario
from evroute.network import ROAD_CLASSES, NetworkBuilder, RoadNetwork
from evroute.plan import ChargeDecision, PlanDecision, RoutePlan
from evroute.robustness import monte_carlo, replay_run
from evroute.scenario import Conditions, Preferences, PreparedScenario, Query, Scenario, Station
from evroute.vehicle import VEHICLES, get_vehicle

INF = math.inf
VEHICLE_KEYS = sorted(VEHICLES)
SPEED_SETS = ((1.0,), (1.0, 0.85), (1.0, 0.9, 0.8))
STEPS = (0.01, 0.02, 0.025, 0.03, 0.04, 0.05, 0.07, 0.1)
#: departure times near the demand peaks of the queue model and around midnight
PEAKS_H = (7.5, 8.5, 12.0, 16.5, 17.5, 18.0, 23.6, 0.2)
HEURISTICS = ("naive", "greedy", "station-graph", "genetic")
#: absolute tolerance for "never slower than" comparisons [s]
TOL_S = 1e-6
#: relative trip-time difference below which two Pareto routes are equally fast
#: (``pareto.TIE_RTOL``: goal-directed keys are monotone only up to a few ulps)
TIE_RTOL = 1e-12


def _close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-9) -> bool:
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


# =============================================================================
# instance sampler
# =============================================================================
@dataclass(frozen=True)
class Instance:
    """Everything needed to rebuild one random instance (``describe()`` prints it)."""

    seed: int
    generator: str  # grid | rgg | corridor | tiny
    size: tuple
    vehicle: str
    temperature_c: float
    initial_soc: float
    departure_s: float
    prefs: tuple  # sorted (name, value) pairs of Preferences overrides
    origin_station: bool = False
    stacked: int = 0  # extra stations stacked on an existing station node
    dest_station: bool = False
    conds: tuple = ()  # sorted (name, value) pairs of extra Conditions overrides

    def preferences(self) -> dict[str, Any]:
        return dict(self.prefs)

    def describe(self) -> str:
        p = ", ".join(f"{k}={v!r}" for k, v in self.prefs)
        extra = []
        if self.origin_station:
            extra.append("origin_station")
        if self.stacked:
            extra.append(f"stacked={self.stacked}")
        if self.dest_station:
            extra.append("dest_station")
        extra.extend(f"{k}={v!r}" for k, v in self.conds)
        return (
            f"[seed={self.seed} {self.generator}{self.size} {self.vehicle} T={self.temperature_c:.1f}C "
            f"soc0={self.initial_soc:.3f} dep={self.departure_s / 3600:.2f}h {p} {' '.join(extra)}]"
        )


def _departure(rng: random.Random) -> float:
    if rng.random() < 0.6:
        h = rng.choice(PEAKS_H) + rng.uniform(-0.75, 0.75)
    else:
        h = rng.uniform(0.0, 24.0)
    return round((h % 24.0) * 3600.0, 1)


def sample_instance(seed: int, profile: str = "fast") -> Instance:
    """Deterministic random instance number ``seed`` (``profile``: fast | slow)."""
    rng = random.Random(f"evroute-crossval-{profile}-{seed}")
    slow = profile == "slow"
    u = rng.random()
    if u < 0.62:
        gen = "grid"
        hi = 14 if slow else 7
        rows, cols = rng.randint(3, hi), rng.randint(3, hi)
        if slow and rows * cols > 100 and rng.random() < 0.5:  # keep the big ones rarer
            rows, cols = rng.randint(3, 9), rng.randint(3, 9)
        size = (rows, cols, round(rng.uniform(7000.0, 26000.0), 1), rng.randint(1, 10))
    elif u < 0.84:
        gen = "rgg"
        size = (rng.randint(18, 110 if slow else 50), round(rng.uniform(70.0, 220.0), 1), rng.choice((3, 4, 5)))
    else:
        gen = "corridor"
        size = (round(rng.uniform(150.0, 520.0 if slow else 350.0), 1), rng.choice((15.0, 20.0, 30.0)),
                rng.random() < 0.75)
    speed = rng.choice(SPEED_SETS) if (slow or rng.random() < 0.6) else (1.0,)
    if not slow and len(speed) == 3:
        speed = (1.0, 0.85)
    big = (gen == "grid" and size[0] * size[1] > 64) or (gen == "rgg" and size[0] > 60)
    if slow and big and len(speed) == 3:  # three levels on big graphs: millions of labels
        speed = (1.0, 0.9)
    reserve = rng.choice((0.0, 0.05, 0.1, 0.15, 0.2, 0.3, round(rng.uniform(0.0, 0.3), 3)))
    prefs = {
        "objective": rng.choice(("time", "time", "cost")),
        "reserve_soc": reserve,
        "min_arrival_soc": rng.choice((0.0, reserve, 0.1, 0.2, 0.3, round(rng.uniform(0.0, 0.3), 3))),
        "max_charge_soc": rng.choice((1.0, 1.0, 0.9, 0.8, 0.6, round(rng.uniform(0.6, 1.0), 3))),
        "speed_factors": speed,
        "queue_aware": rng.random() < 0.75,
        "time_dependent_waits": rng.random() < 0.5,
        "charge_step_soc": rng.choice(STEPS + (round(rng.uniform(0.01, 0.1), 3),)),
        "safety_factor": rng.choice((1.0, 1.0, 1.1, 1.3, round(rng.uniform(1.0, 1.3), 3))),
    }
    conds = {}
    if rng.random() < 0.3:
        conds["wind_speed_ms"] = round(rng.uniform(2.0, 15.0), 1)
        conds["wind_from_deg"] = round(rng.uniform(0.0, 360.0), 1)
    if rng.random() < 0.25:
        conds["preconditioning"] = False  # cold DC derating
    if rng.random() < 0.2:
        conds["payload_kg"] = round(rng.uniform(0.0, 600.0), 1)
    if rng.random() < 0.2:
        conds["traffic_factor"] = round(rng.uniform(0.6, 1.1), 3)
    origin_station = rng.random() < 0.25
    soc0 = round(rng.uniform(0.05, 0.95), 3)
    if origin_station and rng.random() < 0.5:
        soc0 = round(rng.uniform(0.0, max(reserve, 0.05)), 3)  # below (or at) the reserve
    elif rng.random() < 0.5:
        soc0 = round(rng.uniform(0.05, 0.45), 3)  # charging is needed most of the time
    return Instance(
        seed=seed,
        generator=gen,
        size=size,
        vehicle=rng.choice(VEHICLE_KEYS),
        temperature_c=round(rng.uniform(-15.0, 40.0), 1),
        initial_soc=soc0,
        departure_s=_departure(rng),
        prefs=tuple(sorted(prefs.items())),
        origin_station=origin_station,
        stacked=rng.choice((0, 0, 0, 1, 2)),
        dest_station=rng.random() < 0.15,
        conds=tuple(sorted(conds.items())),
    )


def _extra_stations(sc: Scenario, inst: Instance) -> Scenario:
    rng = random.Random(f"evroute-crossval-stations-{inst.seed}")
    st = [replace(s) for s in sc.stations]
    o, d = sc.query.origin, sc.query.destination

    def add(node: int, **kw: Any) -> None:
        power = kw.pop("power_kw", rng.choice((22.0, 50.0, 150.0, 350.0)))
        kind = "AC" if power <= 22.0 else "DC"
        st.append(Station(id=len(st), node=node, power_kw=power, kind=kind, plugs=rng.choice((1, 2, 4)),
                          price_per_kwh=round(rng.uniform(0.2, 0.8), 3), name=f"X{len(st)}",
                          daily_sessions=round(rng.uniform(1.0, 60.0), 2),
                          demand_profile=rng.choice(("highway", "urban")),
                          mean_service_min=rng.choice((25.0, 35.0, 120.0)), setup_s=rng.choice((0.0, 60.0, 180.0)),
                          **kw))

    if inst.origin_station:
        add(o)
    if inst.dest_station:
        add(d)
    if inst.stacked and st:
        node = rng.choice([s.node for s in st])
        for _ in range(inst.stacked):
            add(node)
    return sc.with_(stations=st)


def build_scenario(inst: Instance) -> Scenario:
    """The :class:`Scenario` of ``inst`` (deterministic)."""
    prefs = inst.preferences()
    if inst.generator == "grid":
        rows, cols, spacing, n_st = inst.size
        sc = grid_scenario(rows, cols, spacing_m=spacing, n_stations=n_st, vehicle=inst.vehicle,
                           seed=inst.seed, initial_soc=inst.initial_soc, temperature_c=inst.temperature_c, **prefs)
    elif inst.generator == "rgg":
        n, region, k = inst.size
        sc = None
        for attempt in range(6):
            try:
                sc = random_geometric_scenario(n + 7 * attempt, seed=inst.seed, region_km=region, k=k,
                                               n_stations=max(2, (n + 7 * attempt) // 9),
                                               vehicle=inst.vehicle, initial_soc=inst.initial_soc,
                                               temperature_c=inst.temperature_c, **prefs)
                break
            except ValueError:
                continue
        assert sc is not None, inst.describe()
    elif inst.generator == "corridor":
        length, spacing, mountain = inst.size
        sc = corridor_scenario(length, spacing_km=spacing, mountain=mountain, vehicle=inst.vehicle,
                               initial_soc=inst.initial_soc, temperature_c=inst.temperature_c, seed=inst.seed,
                               **prefs)
    elif inst.generator == "tiny":
        sc = tiny_scenario(inst)
    else:  # pragma: no cover
        raise ValueError(inst.generator)
    sc = sc.with_query(departure_s=inst.departure_s)
    if inst.conds:
        sc = sc.with_conditions(**dict(inst.conds))
    return _extra_stations(sc, inst) if inst.generator != "tiny" else sc


# ----------------------------------------------------------------- tiny graphs
def sample_tiny(seed: int) -> Instance:
    """A tiny random digraph instance (<= 7 nodes, <= 2 stations) for brute force."""
    rng = random.Random(f"evroute-crossval-tiny-{seed}")
    reserve = rng.choice((0.0, 0.05, 0.1, 0.2, 0.3))
    prefs = {
        "objective": rng.choice(("time", "cost")),
        "reserve_soc": reserve,
        "min_arrival_soc": rng.choice((0.0, reserve, 0.15, 0.3)),
        "max_charge_soc": rng.choice((1.0, 0.9, 0.8, 0.6, round(rng.uniform(0.6, 1.0), 3))),
        "speed_factors": rng.choice(((1.0,), (1.0,), (1.0, 0.85), (1.0, 0.9, 0.8))),
        "queue_aware": rng.random() < 0.75,
        "time_dependent_waits": rng.random() < 0.5,
        "charge_step_soc": rng.choice((0.05, 0.1, 0.1, 0.125, round(rng.uniform(0.04, 0.1), 3))),
        "safety_factor": rng.choice((1.0, 1.15, round(rng.uniform(1.0, 1.3), 3))),
    }
    n = rng.randint(3, 7)
    conds = {}
    if rng.random() < 0.3:
        conds["wind_speed_ms"] = round(rng.uniform(2.0, 15.0), 1)
        conds["wind_from_deg"] = round(rng.uniform(0.0, 360.0), 1)
    if rng.random() < 0.3:
        conds["preconditioning"] = False
    return Instance(seed=seed, generator="tiny", size=(n, round(rng.uniform(25e3, 140e3), 1), rng.randint(0, 2)),
                    vehicle=rng.choice(VEHICLE_KEYS), temperature_c=round(rng.uniform(-15.0, 40.0), 1),
                    initial_soc=round(rng.uniform(0.0, 0.7), 3), departure_s=_departure(rng),
                    prefs=tuple(sorted(prefs.items())), conds=tuple(sorted(conds.items())))


def tiny_scenario(inst: Instance) -> Scenario:
    """Random digraph: arbitrary topology, one-way roads, steep hills (regeneration)."""
    rng = random.Random(f"evroute-crossval-tinynet-{inst.seed}")
    n, span, n_st = inst.size
    b = NetworkBuilder()
    for i in range(n):
        x = span * i / max(n - 1, 1) + rng.uniform(-0.25, 0.25) * span / n
        b.add_node(x, rng.uniform(-0.3, 0.3) * span, rng.choice((0.0, rng.uniform(0.0, 1800.0))))
    classes = list(ROAD_CLASSES)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    rng.shuffle(pairs)
    used = set()
    for i, j in pairs[: rng.randint(n - 1, min(len(pairs), 2 * n))]:
        cls = rng.choice(classes)
        L = math.hypot(b.x[j] - b.x[i], b.y[j] - b.y[i]) * rng.uniform(1.0, 1.4)
        r = rng.random()
        if r < 0.6:
            b.add_road(i, j, length=L, road_class=cls)
        elif r < 0.8:
            b.add_edge(i, j, length=L, road_class=cls)
        else:
            b.add_edge(j, i, length=L, road_class=cls)
        used.add((i, j))
    for i in range(n - 1):  # a forward chain keeps the destination reachable
        if (i, i + 1) not in used:
            b.add_road(i, i + 1, road_class=rng.choice(classes))
    net = b.build()
    stations = []
    for k in range(n_st):
        node = rng.randrange(n)
        power = rng.choice((22.0, 50.0, 150.0, 350.0))
        stations.append(Station(id=k, node=node, power_kw=power, kind="AC" if power <= 22.0 else "DC",
                                plugs=rng.choice((1, 2, 4)), price_per_kwh=round(rng.uniform(0.2, 0.8), 3),
                                daily_sessions=round(rng.uniform(1.0, 60.0), 2),
                                demand_profile=rng.choice(("highway", "urban")),
                                setup_s=rng.choice((0.0, 60.0, 180.0))))
    return Scenario(
        name=f"tiny-{inst.seed}",
        network=net,
        stations=stations,
        vehicle=get_vehicle(inst.vehicle),
        query=Query(origin=0, destination=n - 1, initial_soc=inst.initial_soc, departure_s=inst.departure_s),
        conditions=Conditions(temperature_c=inst.temperature_c, **dict(inst.conds)),
        preferences=Preferences(**inst.preferences()),
    )


# =============================================================================
# bookkeeping
# =============================================================================
INVARIANTS = {
    "I1": "evaluator replay, check_objective, internal = evaluated",
    "I2": "A* = Dijkstra order (bls, cfp; tight and basic potentials)",
    "I3": "cfp <= bls; finer nested grid never worse; targets on the grid",
    "I4": "pareto extremes = bls(time)/bls(cost), members non-dominated",
    "I5": "no feasible plan of any planner beats cfp on time",
    "I6": "feasibility agreement of the exact planners",
    "I7": "robustness: zero-noise MC = evaluate, adaptive <= same-target, safety factor",
    "I8": "lossless serialisation round trips",
    "I9": "brute force on tiny digraphs (BLS value and Pareto front)",
    "I10": "metamorphic relations (relabel, duplicate, monotone easier instances)",
    "I11": "no feasible neighbour of an exact optimum is better (mutation search)",
}


@dataclass
class Tally:
    """Counts of checks and failure messages per invariant."""

    checked: Counter = field(default_factory=Counter)
    failed: dict = field(default_factory=lambda: defaultdict(list))
    skipped: Counter = field(default_factory=Counter)
    worst: dict = field(default_factory=dict)

    def check(self, inv: str, ok: bool, msg: Callable[[], str] | str) -> bool:
        self.checked[inv] += 1
        if not ok:
            self.failed[inv].append(msg() if callable(msg) else msg)
        return ok

    def skip(self, what: str) -> None:
        self.skipped[what] += 1

    def note(self, what: str, value: float) -> None:
        """Keep the largest ``value`` seen for ``what`` (diagnostic statistics)."""
        if value > self.worst.get(what, -INF):
            self.worst[what] = value

    def merge(self, other: "Tally") -> None:
        self.checked.update(other.checked)
        self.skipped.update(other.skipped)
        for k, v in other.worst.items():
            self.note(k, v)
        for k, v in other.failed.items():
            self.failed[k].extend(v)

    @property
    def n_failed(self) -> int:
        return sum(len(v) for v in self.failed.values())

    def table(self) -> str:
        rows = ["| invariant | checks | failures |", "|---|---:|---:|"]
        for k in INVARIANTS:
            rows.append(f"| {k} {INVARIANTS[k]} | {self.checked[k]} | {len(self.failed.get(k, []))} |")
        return "\n".join(rows)

    def report(self, limit: int = 12) -> str:
        lines = [self.table()]
        for k, msgs in self.failed.items():
            for m in msgs[:limit]:
                lines.append(f"{k}: {m}")
        return "\n".join(lines)


def _run(P: PreparedScenario, name: str, **opts: Any) -> RoutePlan:
    return get_planner(name, **opts).plan(P)


def _status(plan: RoutePlan) -> str:
    return (plan.stats.extra.get("status", "") if plan.stats is not None else "") or ""


def _hit_limit(plan: RoutePlan) -> bool:
    return _status(plan) == "limit"


# =============================================================================
# I1: first-principles replay (independent of evaluate())
# =============================================================================
def replay(P: PreparedScenario, d: PlanDecision) -> tuple[bool, float, float]:
    """``(feasible, total time, generalised cost)`` replayed from the model text
    (``docs/DEV_SPEC.md`` section 1.2) -- a second oracle next to ``evaluate``."""
    net = P.net
    node = P.origin
    nodes = [node]
    ok = True
    for i, a in enumerate(d.edges):
        if net.tail[a] != nodes[-1] or not 0 <= d.levels[i] < len(P.speed_factors):
            return False, INF, INF
        nodes.append(net.head[a])
    if nodes[-1] != P.destination:
        ok = False
    by_pos: dict[int, list[ChargeDecision]] = defaultdict(list)
    for c in d.charges:
        if not 0 <= c.position < len(nodes) or P.stations[c.station].node != nodes[c.position]:
            return False, INF, INF
        by_pos[c.position].append(c)
    t = cost = 0.0
    e = P.e_init
    for i, v in enumerate(nodes):
        if i > 0 and e < P.e_min - TOL_KWH:
            ok = False
        for c in by_pos.get(i, ()):
            f = P.cf[c.station]
            if c.target_kwh > P.e_charge_cap + TOL_KWH:
                ok = False
            y = max(e, min(c.target_kwh, P.e_charge_cap))
            w = P.wait_s(c.station, t)
            ch = f.time_at(y) - f.time_at(e) if y > e else 0.0
            cost += P.charge_cost(c.station, e, y, ch, w)
            t += w + P.setup_s[c.station] + ch
            e = y
        if i < len(nodes) - 1:
            if e < P.e_min - TOL_KWH:
                ok = False
            a, k = d.edges[i], d.levels[i]
            c = P.edge_energy[k][a]
            if e - c < -TOL_KWH:  # empty battery, same float tolerance as the reserve (BUG-4)
                ok = False
            e = min(e - c, P.capacity)
            t += P.edge_time[k][a]
            cost += P.drive_cost(P.edge_time[k][a], c)
    if e < P.e_dest_min - TOL_KWH:
        ok = False
    return ok, t, cost


def check_plan(tally: Tally, P: PreparedScenario, plan: RoutePlan, tag: str, exact: bool) -> None:
    """I1 for one plan: evaluator replay, first-principles replay, internal objective."""
    if plan.decision is None:
        tally.check("I1", not plan.feasible, lambda: f"{tag}: feasible plan without a decision")
        return
    again = evaluate(P, plan.decision)
    tally.check("I1", again.feasible == plan.feasible,
                lambda: f"{tag}: re-evaluation feasible={again.feasible} vs plan {plan.feasible}: {again.message}")
    if not plan.feasible:
        tally.check("I1", bool(plan.violations), lambda: f"{tag}: infeasible plan without violations")
        return
    tally.check("I1", not plan.violations, lambda: f"{tag}: feasible plan with violations {plan.violations}")
    tally.check("I1", again.objective_value == plan.objective_value,
                lambda: f"{tag}: re-evaluation {again.objective_value!r} != {plan.objective_value!r}")
    tally.check("I1", check_objective(plan),
                lambda: f"{tag}: check_objective fails: evaluated {plan.objective_value!r} "
                        f"> internal {plan.internal_objective!r}")
    if exact:
        tally.check("I1", plan.internal_objective is not None and _close(plan.internal_objective,
                                                                         plan.objective_value, 1e-6, 1e-6),
                    lambda: f"{tag}: internal {plan.internal_objective!r} != evaluated {plan.objective_value!r}")
    ok, t, g = replay(P, plan.decision)
    tally.check("I1", ok and _close(t, plan.total_time_s, 1e-9, 1e-6) and _close(g, plan.generalized_cost, 1e-9, 1e-9),
                lambda: f"{tag}: first-principles replay ok={ok} t={t!r} g={g!r} vs evaluate "
                        f"t={plan.total_time_s!r} g={plan.generalized_cost!r}")
    parts = plan.drive_time_s + plan.wait_time_s + plan.setup_time_s + plan.charge_time_s
    tally.check("I1", _close(parts, plan.total_time_s, 1e-9, 1e-6),
                lambda: f"{tag}: time components {parts!r} != total {plan.total_time_s!r}")
    tally.check("I1", not check_bookkeeping(P, plan), lambda: f"{tag}: {check_bookkeeping(P, plan)}")


def check_bookkeeping(P: PreparedScenario, plan: RoutePlan) -> list[str]:
    """Structural sanity of an evaluated feasible plan (per-node arrays, stops, totals)."""
    bad: list[str] = []
    n = len(plan.nodes)
    if not (len(plan.edges) == n - 1 == len(plan.levels) and len(plan.node_arrive_s) == n == len(plan.node_energy_depart)):
        return ["array lengths"]
    if plan.nodes[0] != P.origin or plan.nodes[-1] != P.destination:
        bad.append("endpoints")
    tol = TOL_KWH
    for i in range(n):
        if plan.node_depart_s[i] < plan.node_arrive_s[i] or plan.node_energy_depart[i] < plan.node_energy_arrive[i] - 1e-12:
            bad.append(f"node {i}: departs before arriving / loses energy while standing")
        if i and (plan.node_arrive_s[i] <= plan.node_depart_s[i - 1] or plan.node_dist_m[i] <= plan.node_dist_m[i - 1]):
            bad.append(f"node {i}: time or distance does not increase along an edge")
        if (i and plan.node_energy_arrive[i] < P.e_min - tol) or (i < n - 1 and plan.node_energy_depart[i] < P.e_min - tol):
            bad.append(f"node {i}: below the reserve in a feasible plan")
        if plan.node_energy_arrive[i] > P.capacity + 1e-9 or plan.node_energy_depart[i] > P.capacity + 1e-9:
            bad.append(f"node {i}: above capacity")
    for st in plan.stops:
        if st.energy_out_kwh > max(P.e_charge_cap, st.energy_in_kwh) + 1e-9 or st.energy_out_kwh < st.energy_in_kwh:
            bad.append(f"stop at {st.node}: charges to {st.energy_out_kwh} (cap {P.e_charge_cap}, in {st.energy_in_kwh})")
        if st.wait_s < 0 or st.setup_s < 0 or st.charge_s < 0 or P.stations[st.station].node != st.node:
            bad.append(f"stop at {st.node}: negative duration or wrong node")
    if not _close(plan.distance_m, sum(P.net.length[a] for a in plan.edges), 1e-9, 1e-6):
        bad.append("distance")
    if plan.energy_charged_kwh < -1e-12 or plan.money < -1e-12 or plan.soh_total_pct < -1e-15:
        bad.append("negative totals")
    if not (plan.arrival_soc * P.capacity >= P.e_dest_min - tol and plan.stranded_at_m is None):
        bad.append("arrival")
    plan.summary()
    prof = plan.soc_profile()
    if any(b[1] < a[1] for a, b in zip(prof, prof[1:])):
        bad.append("soc_profile time not monotone")
    return bad


def _one_stop_per_visit(plan: RoutePlan) -> bool:
    pos = [s.position for s in plan.stops]
    return len(pos) == len(set(pos))


def _all_plans(plans: dict[str, RoutePlan]) -> list[tuple[str, RoutePlan]]:
    out = []
    for name, p in plans.items():
        out.append((name, p))
        for j, m in enumerate(p.alternatives):
            out.append((f"{name}[{j}]", m))
    return out


# =============================================================================
# the per-instance check
# =============================================================================
@dataclass
class Budget:
    """How much work one instance may cost."""

    #: label limits of the A* runs (also used by the derived checks) and of the
    #: Dijkstra-order runs (CFP in Dijkstra order is quadratic in the label sets)
    bls_labels: int = 400_000
    cfp_labels: int = 400_000
    bls_dijkstra_labels: int = 400_000
    cfp_dijkstra_labels: int = 100_000
    pareto_labels: int = 150_000
    pareto_max_nodes: int = 30
    genetic: dict = field(default_factory=lambda: {"population": 16, "generations": 12, "patience": 5})
    nesting: bool = True
    robustness: bool = True
    serialise: bool = True
    metamorphic: bool = True
    basic_potentials: bool = True
    #: metamorphic relations checked per instance (randomly chosen)
    metamorphic_relations: int = 3
    #: random neighbours of each exact optimum (I11)
    mutations: int = 40


FAST = Budget()
SLOW = Budget(bls_labels=250_000, cfp_labels=60_000, bls_dijkstra_labels=150_000, cfp_dijkstra_labels=20_000,
              pareto_labels=150_000, pareto_max_nodes=50, metamorphic_relations=4)


def check_instance(inst: Instance, budget: Budget = FAST, tally: Tally | None = None,
                   scenario: Scenario | None = None) -> Tally:
    """Run every planner on ``inst`` (or on ``scenario``, labelled by ``inst``) and
    check invariants I1-I8 and I10."""
    tally = tally if tally is not None else Tally()
    sc = scenario if scenario is not None else build_scenario(inst)
    P = sc.prepare()
    tag = inst.describe()
    time_obj = P.objective == "time"

    plans: dict[str, RoutePlan] = {}
    plans["bls"] = _run(P, "bls", max_labels=budget.bls_dijkstra_labels)
    plans["bls-astar"] = _run(P, "bls-astar", max_labels=budget.bls_labels)
    plans["cfp"] = _run(P, "cfp", max_labels=budget.cfp_dijkstra_labels)
    plans["cfp-astar"] = _run(P, "cfp-astar", max_labels=budget.cfp_labels)
    if budget.basic_potentials:
        plans["bls-astar/basic"] = _run(P, "bls-astar", potential="basic", max_labels=budget.bls_labels)
        plans["cfp-astar/basic"] = _run(P, "cfp-astar", potential="basic", max_labels=budget.cfp_labels)
    small = P.net.num_nodes <= budget.pareto_max_nodes and len(P.speed_factors) <= 2
    if small and P.preferences.charge_step_soc >= 0.04:
        plans["pareto"] = _run(P, "pareto", max_labels=budget.pareto_labels)
    else:
        tally.skip("pareto (size)")
    hopt = random.Random(f"evroute-crossval-heur-{inst.seed}")
    heur_opts = {
        "naive": {"metric": hopt.choice(("time", "time", "distance"))},
        "greedy": {"charge_to_soc": hopt.choice((None, None, 0.6, 0.9, 1.0))},
        "station-graph": {"charge_to_soc": hopt.choice((None, None, 0.7, 0.9)),
                          "detour_factor": hopt.choice((1.6, 1.6, 1.2, 3.0))},
        "genetic": dict(budget.genetic, seed=hopt.randrange(4)),
    }
    for h in HEURISTICS:
        plans[h] = _run(P, h, **heur_opts[h])

    exact_names = [n for n in plans if n.startswith(("bls", "cfp", "pareto"))]
    limited = {n for n in exact_names if _hit_limit(plans[n])}
    for n in limited:
        tally.skip(f"{n.split('/')[0]} (max_labels)")

    # ---------------------------------------------------------------- I1
    for name, p in _all_plans(plans):
        exact = name.startswith(("bls", "cfp", "pareto"))
        check_plan(tally, P, p, f"{name} {tag}", exact)

    # ---------------------------------------------------------------- I2
    def same(a: str, b: str, rel: float, abs_: float) -> None:
        if a in limited or b in limited or a not in plans or b not in plans:
            return
        pa, pb = plans[a], plans[b]
        if not tally.check("I2", pa.feasible == pb.feasible,
                           lambda: f"{a} feasible={pa.feasible} vs {b} feasible={pb.feasible} {tag}"):
            return
        if pa.feasible:
            tally.check("I2", _close(pa.internal_objective, pb.internal_objective, rel, abs_),
                        lambda: f"{a}={pa.internal_objective!r} vs {b}={pb.internal_objective!r} {tag}")

    same("bls", "bls-astar", 1e-9, 1e-9)
    same("bls", "bls-astar/basic", 1e-9, 1e-9)
    same("cfp", "cfp-astar", 1e-9, TOL_S)
    same("cfp", "cfp-astar/basic", 1e-9, TOL_S)
    check_potentials(tally, P, inst.seed)
    check_options(tally, P, plans, limited, inst, budget)

    # reference values
    cfp = next((plans[n] for n in ("cfp", "cfp-astar") if n not in limited), None)
    cfp_time = cfp.total_time_s if (cfp is not None and cfp.feasible) else None
    bls = next((plans[n] for n in ("bls-astar", "bls") if n not in limited), None)

    # ---------------------------------------------------------------- I3
    if time_obj and bls is not None and cfp is not None and bls.feasible and cfp.feasible:
        tally.check("I3", cfp.total_time_s <= bls.total_time_s + TOL_S,
                    lambda: f"cfp {cfp.total_time_s!r} > bls {bls.total_time_s!r} "
                            f"(waits {'td' if P.time_dependent else 'static'}) {tag}")
    for name in ("bls", "bls-astar", "pareto"):
        p = plans.get(name)
        if p is None or not p.feasible:
            continue
        for c in p.decision.charges:
            tg = charging_targets(P, c.station, "grid+breaks")
            tally.check("I3", any(c.target_kwh == y for y in tg),
                        lambda: f"{name}: target {c.target_kwh!r} not on the target set {tag}")
    if budget.nesting:
        if "bls-astar" not in limited:
            check_nesting(tally, sc, inst, budget)

    # ---------------------------------------------------------------- I4
    if "pareto" in plans and "pareto" not in limited:
        check_pareto(tally, sc, P, plans["pareto"], plans, tag, budget)

    # ---------------------------------------------------------------- I5
    if cfp_time is not None:
        for name, p in _all_plans(plans):
            if p.feasible and _one_stop_per_visit(p):
                tally.check("I5", p.total_time_s >= cfp_time - TOL_S - 1e-9 * cfp_time,
                            lambda: f"{name} time {p.total_time_s!r} < cfp {cfp_time!r} "
                                    f"(waits {'td' if P.time_dependent else 'static'}) {tag}")
            elif p.feasible:
                tally.skip("I5 (two stops at one node visit)")

    # ---------------------------------------------------------------- I6
    any_feasible = [n for n, p in plans.items() if p.feasible]
    if any_feasible:
        for n in exact_names:
            if n not in limited:
                tally.check("I6", plans[n].feasible,
                            lambda: f"{n} infeasible ({plans[n].message[:160]}) but {any_feasible} feasible {tag}")

    # ---------------------------------------------------------------- I7
    if budget.robustness:
        for name in ("bls-astar", "cfp-astar", "greedy"):
            p = plans.get(name)
            if p is not None and p.feasible:
                check_robustness(tally, sc, P, p, f"{name} {tag}")
                break
        if "bls-astar" not in limited:
            check_safety_factor(tally, sc, inst, tag, budget)

    # ---------------------------------------------------------------- I8
    if budget.serialise:
        check_serialisation(tally, sc, P, plans, tag)

    # --------------------------------------------------------------- I10
    if budget.metamorphic:
        check_metamorphic(tally, sc, inst, plans, tag, budget)

    # --------------------------------------------------------------- I11
    for name in ("bls-astar", "cfp-astar"):
        p = plans.get(name)
        if p is not None and p.feasible and name not in limited:
            check_local_optimality(tally, P, p, name, inst.seed, budget.mutations)
    return tally


# =============================================================================
# I2: the A* potentials themselves
# =============================================================================
def check_potentials(tally: Tally, P: PreparedScenario, seed: int, samples: int = 60) -> None:
    """Consistency of every potential on random states of the battery-expanded graph.

    BLS-A* (tight / basic): ``h(v, e) <= cost(transition) + h(w, e')`` for every
    drive and charge transition; ``h(d, e) = 0`` for ``e >= e_goal``; ``h`` non-
    increasing in ``e``.  CFP-A*: ``rate * p / 3600 <= 1`` for every charging
    power in the usable range (Lemma 2 of cfp.py) and consistency on drive edges.
    Pareto: every component of :class:`CompletionBounds` is consistent."""
    from evroute.algorithms.cfp import CFPAStarPlanner
    from evroute.algorithms.labels import AStarPotential
    from evroute.algorithms.pareto import CompletionBounds

    rng = random.Random(f"evroute-crossval-pot-{seed}")
    net = P.net
    time_obj = P.objective == "time"
    lo = min(P.e_min, P.e_init)
    hi = P.capacity
    pots = {"tight": AStarPotential.tight(P), "basic": AStarPotential.basic(P)}
    for kind in ("tight", "basic"):
        hb, el, rate, _need, _ = CFPAStarPlanner(potential=kind)._potential(P)

        def hc(v: int, e: float, hb=hb, el=el, rate=rate) -> float:
            if hb[v] == INF:
                return INF
            d = el[v] - e
            return hb[v] + d * rate if d > 0.0 else hb[v]

        pots[f"cfp-{kind}"] = hc
        bad = [(s, i) for s, f in enumerate(P.cf) for i, pw in enumerate(f.power_kw)
               if f.e_breaks[i + 1] > lo + 1e-9 and f.e_breaks[i] < P.e_charge_cap and rate * pw / 3600.0 > 1 + 1e-12]
        tally.check("I2", not bad, lambda: f"cfp-astar/{kind}: rate {rate} faster than charging at {bad[:2]} "
                                           f"(Lemma 2 of cfp.py) [seed={seed}]")
    cb = CompletionBounds(P)
    fails: list[str] = []
    tol = 1e-7
    for _ in range(samples):
        v = rng.randrange(net.num_nodes)
        e = rng.uniform(max(lo, P.e_min), hi)
        for name, h in pots.items():
            hv = h(v, e)
            if hv == INF:
                continue
            if h(v, min(hi, e + rng.uniform(0.0, 5.0))) > hv + tol:
                fails.append(f"{name}: h increases with the energy at node {v}")
            for a in net.out_edges[v]:
                w = net.head[a]
                for _k, tau, c, dc in P.edge_options[a]:
                    e2 = min(e - c, P.capacity)
                    if e2 < P.e_min - 1e-9:
                        continue
                    step = tau if (time_obj or name.startswith("cfp")) else dc
                    if hv > step + h(w, e2) + tol * max(1.0, hv):
                        fails.append(f"{name}: drive {v}->{w} h={hv!r} > {step!r} + {h(w, e2)!r}")
            if name.startswith("cfp"):
                continue
            t = rng.uniform(0.0, 30000.0)
            for st in P.station_at.get(v, ()):
                f = P.cf[st]
                for y in charging_targets(P, st):
                    if y <= e + 1e-9:
                        continue
                    ch = f.time_at(y) - f.time_at(e)
                    wt = P.wait_s(st, t)
                    step = wt + P.setup_s[st] + ch if time_obj else P.charge_cost(st, e, y, ch, wt)
                    if hv > step + h(v, y) + tol * max(1.0, hv):
                        fails.append(f"{name}: charge at {st} {e:.3f}->{y:.3f} h={hv!r} > {step!r} + {h(v, y)!r}")
        # Pareto completion bounds (time, money, wear)
        b = cb(v, e)
        if b[0] < INF:
            for a in net.out_edges[v]:
                w = net.head[a]
                for _k, tau, c, _dc in P.edge_options[a]:
                    e2 = min(e - c, P.capacity)
                    if e2 < P.e_min - 1e-9:
                        continue
                    b2 = cb(w, e2)
                    dw = P.drive_soh_per_kwh * c if c > 0 else 0.0
                    for x, d, y2, lab in ((b[0], tau, b2[0], "t"), (b[1], 0.0, b2[1], "m"), (b[2], dw, b2[2], "w")):
                        if x > d + y2 + tol * max(1.0, abs(x)):
                            fails.append(f"pareto bound {lab}: drive {v}->{w} {x!r} > {d!r} + {y2!r}")
    if P.destination is not None:
        for name, h in pots.items():
            if h(P.destination, max(P.e_dest_min, hi)) != 0.0:
                fails.append(f"{name}: h(destination, full) != 0")
    tally.check("I2", not fails, lambda: f"potential inconsistent: {fails[:3]} [seed={seed}]")


def check_options(tally: Tally, P: PreparedScenario, plans: dict, limited: set, inst: Instance, budget: Budget) -> None:
    """Option relations: ``targets`` superset, ``exact_switching``, ``trace`` and determinism."""
    from evroute.algorithms.cfp import _textbook_is_exact

    tag = inst.describe()
    rng = random.Random(f"evroute-crossval-opts-{inst.seed}")
    if "bls-astar" not in limited:
        g = _run(P, "bls-astar", targets="grid", max_labels=budget.bls_labels)
        b = plans["bls-astar"]
        if not _hit_limit(g) and g.feasible:
            tally.check("I3", b.feasible and b.internal_objective <= g.internal_objective * (1 + 1e-9) + 1e-9,
                        lambda: f"targets='grid+breaks' {b.internal_objective!r} worse than 'grid' "
                                f"{g.internal_objective!r} {tag}")
        t = get_planner("bls-astar", max_labels=budget.bls_labels).plan(P, trace=True)
        tally.check("I2", t.feasible == b.feasible and (not b.feasible or t.internal_objective == b.internal_objective)
                    and (t.trace is not None and (not b.feasible or P.destination in t.trace.nodes)),
                    lambda: f"bls-astar with trace=True differs: {t.internal_objective!r} vs {b.internal_objective!r} {tag}")
        again = _run(P.scenario.with_().prepare(), "bls-astar", max_labels=budget.bls_labels)
        tally.check("I1", again.feasible == b.feasible and (not b.feasible or again.internal_objective == b.internal_objective),
                    lambda: f"bls-astar not deterministic on a re-prepared copy {tag}")
    if "cfp-astar" not in limited and rng.random() < 0.5:
        c = plans["cfp-astar"]
        tb = _run(P, "cfp-astar", exact_switching=False, max_labels=budget.cfp_labels)
        if not _hit_limit(tb) and c.feasible:
            tally.check("I2", tb.feasible == c.feasible, lambda: f"cfp exact_switching=False feasibility differs {tag}")
            if tb.feasible:
                if _textbook_is_exact(P):
                    tally.check("I2", _close(tb.total_time_s, c.total_time_s, 1e-9, TOL_S),
                                lambda: f"textbook cfp {tb.total_time_s!r} != cfp {c.total_time_s!r} although "
                                        f"the textbook candidate set is exact here {tag}")
                tally.check("I2", tb.total_time_s >= c.total_time_s - TOL_S,
                            lambda: f"cfp with fewer switch candidates is faster: {tb.total_time_s!r} < "
                                    f"{c.total_time_s!r} {tag}")
            tally.check("I2", tb.stats.extra["optimality"] == ("proven" if _textbook_is_exact(P) else "not proven"),
                        lambda: f"cfp exact_switching=False claims {tb.stats.extra['optimality']!r} {tag}")
    g1 = plans.get("genetic")
    if g1 is not None:
        seed = g1.stats.extra.get("seed", 0) if g1.stats is not None else 0
        g2 = _run(P, "genetic", **dict(budget.genetic, seed=seed))
        tally.check("I1", g1.feasible == g2.feasible and g1.objective_value == g2.objective_value,
                    lambda: f"genetic is not deterministic: {g1.objective_value!r} vs {g2.objective_value!r} {tag}")


# =============================================================================
# I3: nested target grids
# =============================================================================
def check_nesting(tally: Tally, sc: Scenario, inst: Instance, budget: Budget) -> None:
    rng = random.Random(f"evroute-crossval-nest-{inst.seed}")
    fine = rng.choice((0.01, 0.02, 0.025, 0.05))
    coarse = fine * rng.choice((2, 4, 5))
    if coarse > 0.2:
        coarse = fine * 2
    mode = rng.choice(("grid", "grid+breaks"))
    Pf = sc.with_preferences(charge_step_soc=fine).prepare()
    Pc = sc.with_preferences(charge_step_soc=coarse).prepare()
    # (the cap is appended unrounded while grid points are rounded to 1e-9 kWh,
    # so "contained" holds up to the 1e-9 kWh merge tolerance of the planners)
    fine_set = sorted(Pf.target_grid)
    missing = [y for y in Pc.target_grid if not any(abs(y - z) <= 1e-9 for z in fine_set)]
    tally.check("I3", not missing, lambda: f"target grid {coarse} not contained in grid {fine}: "
                                           f"{missing[:4]} [seed={inst.seed}]")
    if Pf.net.num_nodes > 60 and len(Pf.speed_factors) > 1:
        return
    pf = _run(Pf, "bls-astar", targets=mode, max_labels=budget.bls_labels)
    pc = _run(Pc, "bls-astar", targets=mode, max_labels=budget.bls_labels)
    if _hit_limit(pf) or _hit_limit(pc):
        tally.skip("nesting (max_labels)")
        return
    if pc.feasible:
        tally.check("I3", pf.feasible and pf.internal_objective <= pc.internal_objective * (1 + 1e-9) + 1e-9,
                    lambda: f"bls step {fine} ({mode}) = {pf.internal_objective!r} worse than step {coarse} = "
                            f"{pc.internal_objective!r} {inst.describe()}")


# =============================================================================
# I4: Pareto front
# =============================================================================
def _crit(p: RoutePlan) -> tuple[float, float, float]:
    return p.total_time_s, p.money, p.soh_total_pct


def _dominates(a, b) -> bool:
    """Plain Pareto dominance (minimisation) in floating point."""
    return all(x <= y for x, y in zip(a, b, strict=True)) and any(x < y for x, y in zip(a, b, strict=True))


def _dominates_tol(a, b, rel: float = 1e-7) -> bool:
    """``a`` dominates ``b`` by more than float noise."""
    weak = all(x <= y + 1e-12 + 1e-9 * abs(y) for x, y in zip(a, b, strict=True))
    strict = any(x < y - 1e-12 - rel * abs(y) for x, y in zip(a, b, strict=True))
    return weak and strict


def check_pareto(tally: Tally, sc: Scenario, P: PreparedScenario, par: RoutePlan, plans: dict, tag: str,
                 budget: Budget) -> None:
    members = list(par.alternatives)
    if not par.feasible:
        return
    tally.check("I4", bool(members) and all(m.feasible for m in members),
                lambda: f"pareto: infeasible or missing members {tag}")
    crit = [_crit(m) for m in members]
    # a member dominated by one that is faster or equally fast up to float noise in the time
    # (pareto.TIE_RTOL = 1e-12; BUG-1, fixed) is a bug.  A "dominator" that is slower by more
    # than that (but within the 1e-9 slack of _dominates_tol) is a distinct route with a real,
    # if tiny, time difference: both belong on the front.
    pairs = [(i, j) for i in range(len(crit)) for j in range(len(crit)) if i != j and _dominates_tol(crit[i], crit[j])]
    bad = [(i, j) for i, j in pairs if crit[i][0] <= crit[j][0] * (1.0 + TIE_RTOL)]
    tally.check("I4", not bad, lambda: f"pareto: member {bad[0][0]} {crit[bad[0][0]]} dominates "
                                       f"{bad[0][1]} {crit[bad[0][1]]} {tag}")
    if len(pairs) > len(bad):
        tally.skip("I4 member next to a member up to 1e-9 slower but cheaper (distinct times)")
    other = "cost" if P.objective == "time" else "time"
    Po = sc.with_preferences(objective=other).prepare()
    ref = {P.objective: plans.get("bls-astar"), other: _run(Po, "bls-astar", max_labels=budget.bls_labels)}
    for obj, p in ref.items():
        if p is None or _hit_limit(p) or not p.feasible:
            continue
        if obj == "time":
            best = min(m.total_time_s for m in members)
            tally.check("I4", _close(best, p.total_time_s, 1e-9, TOL_S),
                        lambda: f"pareto fastest {best!r} != bls(time) {p.total_time_s!r} {tag}")
        else:
            best = min(m.generalized_cost for m in members)
            tally.check("I4", _close(best, p.generalized_cost, 1e-7, 1e-7),
                        lambda: f"pareto cheapest {best!r} != bls(cost) {p.generalized_cost!r} {tag}")
    # I11 for the front: no feasible neighbour of a member dominates it
    rng = random.Random(f"evroute-crossval-pmut-{tag}")
    for m in rng.sample(members, min(4, len(members))):
        x = _crit(m)
        for _ in range(budget.mutations // 4):
            nd = neighbours(P, m.decision, rng, False)
            if nd is None:
                break
            q = evaluate(P, nd)
            if q.feasible and _one_stop_per_visit(q):
                y = _crit(q)
                # (equally fast up to float noise counts since BUG-1 is fixed)
                tally.check("I11", not (_dominates_tol(y, x, 1e-7) and y[0] <= x[0] * (1.0 + TIE_RTOL)),
                            lambda: f"pareto member {x} dominated by a feasible neighbour {y} {tag}")
    # one documented option variant per instance
    variant = random.Random(f"evroute-crossval-pareto-{tag}").choice(("martins", "epsilon", "two-criteria"))
    lim = budget.pareto_labels
    if variant == "martins":
        alt = _run(P, "pareto", goal_directed=False, max_labels=lim)
        if not _hit_limit(alt):
            a = sorted(_crit(m) for m in alt.alternatives)
            ok = len(a) == len(crit) and all(all(_close(x, y, 1e-9, 1e-9) for x, y in zip(u, v, strict=True))
                                             for u, v in zip(a, sorted(crit), strict=True))
            # (members 1e-12..1e-9 apart in time may be resolved differently)
            tally.check("I4", ok or len(pairs) > len(bad),
                        lambda: f"pareto goal_directed=False front ({len(a)}) != goal-directed front ({len(crit)}) {tag}")
    elif variant == "epsilon":
        eps = 0.05
        alt = _run(P, "pareto", epsilon=eps, max_labels=lim)
        if not _hit_limit(alt):
            a = [_crit(m) for m in alt.alternatives]
            f = (1 + eps) * (1 + 1e-9)
            miss = [x for x in crit if not any(y[0] <= x[0] + 1e-6 and y[1] <= f * x[1] + 1e-12 and y[2] <= f * x[2] + 1e-15
                                               for y in a)]
            tally.check("I4", not miss and len(a) <= len(crit),
                        lambda: f"pareto epsilon={eps}: {len(miss)} exact members not covered, e.g. {miss[:1]} {tag}")
    else:
        alt = _run(P, "pareto", criteria=("time", "money"), max_labels=lim)
        if not _hit_limit(alt) and alt.feasible:
            a = [_crit(m)[:2] for m in alt.alternatives]
            tally.check("I4", _close(min(x[0] for x in a), min(x[0] for x in crit), 1e-9, TOL_S),
                        lambda: f"pareto (time, money): fastest {min(x[0] for x in a)!r} != {min(x[0] for x in crit)!r} {tag}")
            proj = [x[:2] for x in crit]
            # every 2-criteria member is non-dominated among the projected 3-criteria members
            dom = [x for x in a if any(_dominates_tol(y, x, 1e-7) and y[0] < x[0] - 1e-9 * x[0] for y in proj)]
            tally.check("I4", not dom, lambda: f"pareto (time, money) member dominated by a projected 3-criteria "
                                               f"member: {dom[:1]} {tag}")


# =============================================================================
# I7: robustness
# =============================================================================
def check_robustness(tally: Tally, sc: Scenario, P: PreparedScenario, plan: RoutePlan, tag: str) -> None:
    rep = monte_carlo(P, plan, n=3, seed=1, trip_cv=0.0, edge_cv=0.0, wait_model="expected", verify=True)
    tally.check("I7", rep.planned_feasible == plan.feasible and rep.n_strand == 0 and rep.n_reserve_breach == 0
                and rep.n_arrival_short == 0,
                lambda: f"zero-noise MC fails: strand={rep.n_strand} breach={rep.n_reserve_breach} "
                        f"short={rep.n_arrival_short} planned={rep.planned_feasible} {tag}")
    sf = P.preferences.safety_factor
    if sf == 1.0:
        tally.check("I7", rep.total_time_s.min == rep.total_time_s.max == plan.total_time_s,
                    lambda: f"zero-noise MC time {rep.total_time_s.min!r} != evaluate {plan.total_time_s!r} {tag}")
        rp = replay_run(P, plan, 0, seed=1, trip_cv=0.0, edge_cv=0.0, wait_model="expected")
        tally.check("I7", rp.feasible and rp.objective_value == plan.objective_value,
                    lambda: f"replay_run {rp.objective_value!r} feasible={rp.feasible} != {plan.objective_value!r} {tag}")
    else:
        tally.check("I7", rep.total_time_s.max <= plan.total_time_s + TOL_S,
                    lambda: f"nominal replay slower than planned with safety factor {sf}: "
                            f"{rep.total_time_s.max!r} > {plan.total_time_s!r} {tag}")
    same = monte_carlo(P, plan, n=40, seed=7, trip_cv=0.15, edge_cv=0.2, keep_samples=True)
    adap = monte_carlo(P, plan, n=40, seed=7, trip_cv=0.15, edge_cv=0.2, policy="adaptive", keep_samples=True)
    ok = all(a <= s for a, s in zip(adap.samples["stranded"], same.samples["stranded"], strict=True)) and all(
        a >= s - 1e-12 for a, s in zip(adap.samples["min_soc"], same.samples["min_soc"], strict=True))
    tally.check("I7", ok and adap.p_strand <= same.p_strand and adap.p_arrival_short <= same.p_arrival_short,
                lambda: f"adaptive policy worse than same-target: {adap.p_strand} > {same.p_strand} {tag}")
    for r in range(3):  # a single sampled world replayed through evaluate() tells the same story
        rp = replay_run(P, plan, r, seed=7, trip_cv=0.15, edge_cv=0.2)
        st = same.samples["stranded"][r]
        tally.check("I7", (rp.stranded_at_m is not None) == st and (
            st or _close(rp.total_time_s, same.samples["total_time_s"][r], 1e-12, 1e-9)),
                    lambda: f"replay_run({r}) disagrees with monte_carlo sample: stranded {rp.stranded_at_m} vs {st} {tag}")


def check_safety_factor(tally: Tally, sc: Scenario, inst: Instance, tag: str, budget: Budget) -> None:
    """A plan made with gamma > 1 is feasible and no slower under gamma = 1, and
    the optimum with gamma = 1 is never worse."""
    sf = sc.preferences.safety_factor
    lo, hi = (1.0, sf) if sf > 1.0 else (1.0, 1.2)
    P_lo = sc.with_preferences(safety_factor=lo).prepare()
    P_hi = sc.with_preferences(safety_factor=hi).prepare()
    if P_lo.net.num_nodes > 80 and len(P_lo.speed_factors) > 1:
        return
    a = _run(P_lo, "bls-astar", max_labels=budget.bls_labels)
    b = _run(P_hi, "bls-astar", max_labels=budget.bls_labels)
    if _hit_limit(a) or _hit_limit(b):
        return
    if b.feasible:
        nominal = evaluate(P_lo, b.decision)
        tally.check("I7", nominal.feasible and nominal.objective_value <= b.objective_value * (1 + 1e-12) + 1e-9,
                    lambda: f"plan with safety factor {hi} infeasible/slower at {lo}: {nominal.message} {tag}")
        tally.check("I7", a.feasible and a.internal_objective <= b.internal_objective * (1 + 1e-9) + 1e-9,
                    lambda: f"optimum with safety factor {lo} ({a.internal_objective!r}) worse than with {hi} "
                            f"({b.internal_objective!r}) {tag}")


# =============================================================================
# I8: serialisation
# =============================================================================
def check_serialisation(tally: Tally, sc: Scenario, P: PreparedScenario, plans: dict, tag: str) -> None:
    """I8: ``Scenario`` JSON is lossless (BUG-2) and ``RoutePlan.to_dict`` keeps the objective.

    The reloaded scenario must prepare bit-identical edge data, every plan's decision must
    replay to the *same* objective on it, and the decision rebuilt from the plan's JSON
    (edges, levels, stops) must replay identically too -- including knife-edge plans."""
    d1 = json.loads(json.dumps(sc.to_dict(), allow_nan=False))
    sc1 = Scenario.from_dict(d1)
    d2 = json.loads(json.dumps(sc1.to_dict(), allow_nan=False))
    tally.check("I8", d1 == d2, lambda: f"Scenario.to_dict is not a fixed point after one round trip {tag}")
    tally.check("I8", d1 == sc.to_dict(), lambda: f"Scenario.to_dict is not JSON-normal (tuples, keys) {tag}")
    P1 = sc1.prepare()
    tally.check("I8", sc1.network == sc.network and P1.edge_time == P.edge_time and P1.edge_energy == P.edge_energy
                and P1.target_grid == P.target_grid and P1.cf == P.cf
                and all(getattr(P1, a) == getattr(P, a) for a in ("capacity", "e_min", "e_charge_cap", "e_init",
                                                                    "e_dest_min", "setup_s", "speed_factors")),
                lambda: f"the reloaded scenario prepares different data {tag}")
    for name in ("bls-astar", "cfp-astar", "greedy"):
        p = plans.get(name)
        if p is None or not p.feasible:
            continue
        d = p.to_dict()
        txt = json.dumps(d, allow_nan=False)
        back = json.loads(txt)
        tally.check("I8", back["objective_value"] == p.objective_value and back["total_time_s"] == p.total_time_s
                    and back["feasible"] and back["generalized_cost"] == p.generalized_cost
                    and back["internal_objective"] == p.internal_objective,
                    lambda: f"{name}: RoutePlan.to_dict changes the objective {tag}")
        # the same decision on the round-tripped scenario is bit-identical (no BUG-2 drift)
        r1 = evaluate(P1, p.decision)
        tally.check("I8", r1.feasible == p.feasible and r1.objective_value == p.objective_value
                    and r1.total_time_s == p.total_time_s and r1.generalized_cost == p.generalized_cost,
                    lambda: f"{name}: objective after round trip {r1.objective_value!r} vs {p.objective_value!r} "
                            f"feasible={r1.feasible} ({r1.message}) {tag}")
        # ... and so is the decision rebuilt from the plan's JSON alone (a stop's target is its
        # departure energy; a no-op stop above the cap is re-targeted at the cap, also a no-op)
        rebuilt = PlanDecision(back["edges"], back["levels"],
                               [ChargeDecision(s["position"], s["station"], min(s["energy_out_kwh"], P1.e_charge_cap))
                                for s in back["stops"]])
        r2 = evaluate(P1, rebuilt)
        tally.check("I8", r2.feasible and r2.objective_value == p.objective_value,
                    lambda: f"{name}: decision rebuilt from RoutePlan JSON replays to {r2.objective_value!r} "
                            f"vs {p.objective_value!r} ({r2.message}) {tag}")


# =============================================================================
# I10: metamorphic relations
# =============================================================================
def relabel(sc: Scenario, seed: int) -> Scenario:
    """Random permutation of node ids, edge order and station order (same instance)."""
    rng = random.Random(f"evroute-crossval-perm-{seed}")
    net = sc.network
    n, m = net.num_nodes, net.num_edges
    perm = list(range(n))
    rng.shuffle(perm)  # old -> new
    inv = [0] * n
    for old, new in enumerate(perm):
        inv[new] = old
    eorder = list(range(m))
    rng.shuffle(eorder)  # new edge j is old edge eorder[j]
    epos = [0] * m
    for j, old in enumerate(eorder):
        epos[old] = j
    new = RoadNetwork(
        x=[net.x[inv[i]] for i in range(n)],
        y=[net.y[inv[i]] for i in range(n)],
        elevation=[net.elevation[inv[i]] for i in range(n)],
        tail=[perm[net.tail[o]] for o in eorder],
        head=[perm[net.head[o]] for o in eorder],
        length=[net.length[o] for o in eorder],
        speed_kmh=[net.speed_kmh[o] for o in eorder],
        road_class=[net.road_class[o] for o in eorder],
        twin=[epos[net.twin[o]] if net.twin[o] >= 0 else -1 for o in eorder],
        node_name={perm[k]: v for k, v in net.node_name.items()},
    )
    st = [replace(s, node=perm[s.node]) for s in sc.stations]
    rng.shuffle(st)
    q = replace(sc.query, origin=perm[sc.query.origin], destination=perm[sc.query.destination])
    return sc.with_(network=new, stations=st, query=q)


def _value(p: RoutePlan, name: str) -> float:
    """What planner ``name`` optimises: the objective, or the trip time for CFP."""
    if not p.feasible:
        return INF
    return p.total_time_s if name.startswith("cfp") else p.internal_objective


def _opt(P: PreparedScenario, name: str, max_labels: int = 400_000, **opts: Any) -> float | None:
    """Optimum (``inf`` if infeasible, ``None`` if the label limit was hit)."""
    p = _run(P, name, max_labels=max_labels, **opts)
    if _hit_limit(p):
        return None
    return _value(p, name)


def _parallel_edge(sc: Scenario, a: int) -> Scenario:
    """``sc`` plus a slower parallel copy of edge ``a`` (it can never be used profitably)."""
    net = sc.network
    b = NetworkBuilder()
    for i in range(net.num_nodes):
        b.add_node(net.x[i], net.y[i], net.elevation[i])
    for e in range(net.num_edges):
        b.add_edge(net.tail[e], net.head[e], length=net.length[e], speed_kmh=net.speed_kmh[e],
                   road_class=net.road_class[e])
    b.add_edge(net.tail[a], net.head[a], length=net.length[a] * 1.5, speed_kmh=net.speed_kmh[a],
               road_class=net.road_class[a])
    return sc.with_(network=b.build())


def check_metamorphic(tally: Tally, sc: Scenario, inst: Instance, plans: dict, tag: str, budget: Budget) -> None:
    """I10: equivalent instances give the same optimum, easier ones a no worse one."""
    P = sc.prepare()
    if P.net.num_nodes > 80 and len(P.speed_factors) > 1:
        return
    rng = random.Random(f"evroute-crossval-meta-{inst.seed}")
    base = {}
    for name in ("bls-astar", "cfp-astar"):
        p = plans.get(name)
        if p is not None and not _hit_limit(p):
            base[name] = _value(p, name)
    if not base:
        return
    name = rng.choice(sorted(base))
    b = base[name]
    q, pref, net = sc.query, P.preferences, sc.network

    # (relation, description, scenario): "=" same optimum, "<=" no worse optimum
    rel: list[tuple[str, str, Callable[[], Scenario]]] = [
        ("=", "relabelled nodes/edges/stations", lambda: relabel(sc, inst.seed)),
    ]
    a = rng.randrange(net.num_edges)
    rel.append(("=", f"parallel slower copy of edge {a}", lambda: _parallel_edge(sc, a)))
    if sc.stations:
        s = rng.choice(sc.stations)
        rel.append(("=", f"duplicated station {s.id} at node {s.node}",
                    lambda: sc.with_(stations=list(sc.stations) + [replace(s, id=len(sc.stations))])))
    if not pref.queue_aware:
        dep = round(rng.uniform(0.0, 86400.0), 1)
        rel.append(("=", f"departure {dep / 3600:.2f}h without queues", lambda: sc.with_query(departure_s=dep)))
    soc = min(1.0, q.initial_soc + rng.uniform(0.02, 0.3))
    rel.append(("<=", f"initial SoC {soc:.3f}", lambda: sc.with_query(initial_soc=soc)))
    if pref.reserve_soc > 0:
        r = pref.reserve_soc * rng.uniform(0.0, 0.9)
        rel.append(("<=", f"lower reserve {r:.3f}", lambda: sc.with_preferences(reserve_soc=r)))
    if pref.min_arrival_soc > 0:
        r2 = pref.min_arrival_soc * rng.uniform(0.0, 0.9)
        rel.append(("<=", f"lower arrival requirement {r2:.3f}", lambda: sc.with_preferences(min_arrival_soc=r2)))
    if len(P.speed_factors) == 1 or (budget is SLOW and len(P.speed_factors) < 3):
        more = tuple(P.speed_factors) + (rng.choice((0.95, 0.9, 0.8, 0.7)),)
        if len(set(more)) == len(more):
            rel.append(("<=", f"speed levels {more}", lambda: sc.with_preferences(speed_factors=more)))
    if (P.objective == "time" or name.startswith("cfp")) and pref.queue_aware:
        rel.append(("<=", "no queues", lambda: sc.with_preferences(queue_aware=False)))
    node = rng.randrange(net.num_nodes)
    extra = Station(id=len(sc.stations), node=node, power_kw=rng.choice((50.0, 150.0)), kind="DC",
                    price_per_kwh=0.5, daily_sessions=10.0)
    rel.append(("<=", f"extra station at node {node}", lambda: sc.with_(stations=list(sc.stations) + [extra])))
    if name.startswith("cfp") and pref.max_charge_soc < 1.0:
        cap = min(1.0, pref.max_charge_soc + rng.uniform(0.01, 0.1))
        rel.append(("<=", f"higher charging cap {cap:.3f}", lambda: sc.with_preferences(max_charge_soc=cap)))
    if pref.safety_factor > 1.0:
        rel.append(("<=", "safety factor 1", lambda: sc.with_preferences(safety_factor=1.0)))

    if budget.metamorphic_relations < len(rel):
        rel = rng.sample(rel, budget.metamorphic_relations)
    for kind, what, make in rel:
        v = _opt(make().prepare(), name, budget.cfp_labels if name.startswith("cfp") else budget.bls_labels)
        if v is None:
            tally.skip("I10 (max_labels)")
            continue
        if kind == "=":
            tally.check("I10", (v == INF and b == INF) or (v < INF and b < INF and _close(v, b, 1e-9, TOL_S)),
                        lambda: f"{what}: {name} {v!r} != {b!r} {tag}")
        else:
            tally.check("I10", v <= b + TOL_S + 1e-9 * abs(v), lambda: f"{what}: {name} {v!r} worse than {b!r} {tag}")


# =============================================================================
# I11: local search around the optima
# =============================================================================
def _detour(P: PreparedScenario, u: int, v: int, avoid: int, rng: random.Random) -> list[int] | None:
    """A random alternative path ``u -> v`` (Dijkstra on randomly perturbed times, edge ``avoid`` removed)."""
    import heapq

    net = P.net
    w = [P.edge_options[a][0][1] * rng.uniform(1.0, 2.0) for a in range(net.num_edges)]
    w[avoid] = INF
    dist = {u: 0.0}
    pred: dict[int, int] = {}
    heap = [(0.0, u)]
    while heap:
        d, x = heapq.heappop(heap)
        if x == v:
            break
        if d > dist.get(x, INF):
            continue
        for a in net.out_edges[x]:
            y = net.head[a]
            nd = d + w[a]
            if nd < dist.get(y, INF):
                dist[y] = nd
                pred[y] = a
                heapq.heappush(heap, (nd, y))
    if v not in pred:
        return None
    path = []
    x = v
    while x != u:
        a = pred[x]
        path.append(a)
        x = P.net.tail[a]
    return path[::-1]


#: attempts to draw an applicable modification before :func:`neighbours` gives up
NEIGHBOUR_ATTEMPTS = 64


def neighbours(P: PreparedScenario, d: PlanDecision, rng: random.Random, continuous: bool) -> PlanDecision | None:
    """Random small modifications of a decision (targets, stops, speed levels, detours).

    ``None`` if no modification applies (e.g. an edge-less plan without an origin station,
    or one road, no station and one speed level) -- the draw used to loop forever there."""
    net = P.net
    nodes = [P.origin] + [net.head[a] for a in d.edges]
    charges = list(d.charges)
    nlev = len(P.speed_factors)
    for _ in range(NEIGHBOUR_ATTEMPTS):
        op = rng.choice(("target", "target", "drop", "add", "move", "level", "detour"))
        edges, levels, ch = list(d.edges), list(d.levels), list(charges)
        if op == "target" and ch:
            k = rng.randrange(len(ch))
            c = ch[k]
            if continuous:
                y = c.target_kwh + rng.choice((-1, 1)) * rng.choice((1e-3, 1e-2, 0.1, 1.0))
            else:
                tg = charging_targets(P, c.station)
                j = min(range(len(tg)), key=lambda i: abs(tg[i] - c.target_kwh))
                j = max(0, min(len(tg) - 1, j + rng.choice((-2, -1, 1, 2))))
                y = tg[j]
            ch[k] = ChargeDecision(c.position, c.station, min(y, P.e_charge_cap))
        elif op == "drop" and ch:
            ch.pop(rng.randrange(len(ch)))
        elif op in ("add", "move"):
            spots = [(i, s) for i, v in enumerate(nodes) for s in P.station_at.get(v, ())
                     if all(c.position != i for c in ch)]
            if not spots:
                continue
            i, s = rng.choice(spots)
            if op == "move" and ch:
                k = rng.randrange(len(ch))
                y = ch.pop(k).target_kwh
            else:
                tg = charging_targets(P, s)
                y = rng.choice(tg) if not continuous else rng.uniform(P.e_min, P.e_charge_cap)
            ch.append(ChargeDecision(i, s, min(y, P.e_charge_cap)))
        elif op == "level" and edges and nlev > 1:
            k = rng.randrange(len(edges))
            levels[k] = rng.choice([o[0] for o in P.edge_options[edges[k]]])
        elif op == "detour" and edges:
            i = rng.randrange(len(edges))
            j = min(len(edges), i + rng.randint(1, 4))
            if any(i < c.position < j for c in ch):
                continue
            alt = _detour(P, nodes[i], nodes[j], edges[i], rng)
            if alt is None:
                continue
            shift = len(alt) - (j - i)
            edges[i:j] = alt
            levels[i:j] = [P.edge_options[a][0][0] for a in alt]
            ch = [c if c.position <= i else ChargeDecision(c.position + shift, c.station, c.target_kwh) for c in ch]
        else:
            continue
        return PlanDecision(edges, levels, ch)
    return None


def check_local_optimality(tally: Tally, P: PreparedScenario, plan: RoutePlan, name: str, seed: int, n: int) -> None:
    """I11: no feasible neighbour (one stop per node visit; grid targets for BLS) beats the optimum."""
    rng = random.Random(f"evroute-crossval-mut-{seed}-{name}")
    cont = name.startswith("cfp")
    best = plan.total_time_s if cont else plan.objective_value
    better = []
    for _ in range(n):
        nd = neighbours(P, plan.decision, rng, cont)
        if nd is None:
            tally.skip("I11 (no applicable modification)")
            break
        q = evaluate(P, nd)
        if not q.feasible or not _one_stop_per_visit(q):
            continue
        v = q.total_time_s if cont else q.objective_value
        tally.checked["I11"] += 1
        if v < best - TOL_S - 1e-9 * abs(best):
            better.append((v, nd))
    if better:
        v, nd = min(better, key=lambda x: x[0])
        tally.failed["I11"].append(f"{name} {best!r} beaten by {len(better)} feasible neighbour(s), best {v!r}: "
                                   f"{nd} [seed={seed}]")


# =============================================================================
# I9: brute force on tiny digraphs
# =============================================================================
def _oracle_targets(P: PreparedScenario, s: int) -> list[float]:
    """Target set re-derived from the scenario (``grid+breaks``), not from the planner code."""
    cap = P.e_charge_cap
    vals = [min(y, cap) for y in P.target_grid if y <= cap + 1e-9]
    vals += [b for b in P.cf[s].e_breaks[1:-1] if b <= cap]
    out: list[float] = []
    for y in sorted(vals):
        if y > 0 and not (out and y - out[-1] <= 1e-9):
            out.append(y)
    return out


def brute_force(P: PreparedScenario, max_visits: int = 2, max_steps: int = 400_000, stops_per_visit: int = 1):
    """Exhaustive enumeration of walks (every node at most ``max_visits`` times)
    with at most ``stops_per_visit`` stops (at distinct stations) per node visit
    to any target of the target set.  ``stops_per_visit=1`` is the model of CFP;
    BLS, BLS-A* and Pareto allow consecutive stops at different stations (BUG-3).

    Returns ``(best objective, best decision, (t, money, wear) of the
    non-dominated complete routes)``; ``None`` if the enumeration needs more
    than ``max_steps`` steps.  The only pruning: a partial route whose
    ``(t, money, wear)`` is weakly dominated by a complete route is dropped
    (every transition increases ``t`` strictly and never decreases money or
    wear, and both objectives are monotone in the three criteria)."""
    net = P.net
    time_obj = P.objective == "time"
    tg = {s: _oracle_targets(P, s) for s in range(len(P.stations))}
    visits = [0] * net.num_nodes
    visits[P.origin] = 1
    eps = 1e-9
    best = [INF, None]
    pts: list[tuple[float, float, float]] = []
    count = [0]
    dsoh = P.drive_soh_per_kwh

    def rec(v, t, g, m, w, e, edges, levels, charges, used_here):
        count[0] += 1
        if count[0] > max_steps:
            raise OverflowError
        for q in pts:
            if q[0] <= t and q[1] <= m and q[2] <= w:
                return
        if v == P.destination and e >= P.e_dest_min - eps:
            prim = t if time_obj else g
            pts.append((t, m, w))
            if prim < best[0]:
                best[0] = prim
                best[1] = PlanDecision(list(edges), list(levels), [ChargeDecision(*c) for c in charges])
            return  # every continuation is dominated by this complete route
        if len(used_here) < stops_per_visit:
            here = P.station_at.get(v, [])
            # a stop may end below the reserve only if another stop can follow at this node
            # (the evaluator checks the reserve on arrival and departure, not between stops)
            low_ok = len(used_here) + 1 < min(stops_per_visit, len(here))
            for s in here:
                if s in used_here:
                    continue
                f = P.cf[s]
                for y in tg[s]:
                    if y > e + eps and (low_ok or y >= P.e_min - eps):
                        wt = P.wait_s(s, t)
                        ch = f.time_at(y) - f.time_at(e)
                        rec(v, t + (wt + P.setup_s[s] + ch), g + P.charge_cost(s, e, y, ch, wt),
                            m + (f.money_at(y) - f.money_at(e)), w + (f.soh_at(y) - f.soh_at(e)), y,
                            edges, levels, charges + [(len(edges), s, y)], used_here + (s,))
        if e >= P.e_min - eps:
            for a in net.out_edges[v]:
                u = net.head[a]
                if visits[u] >= max_visits:
                    continue
                for k, tau, c, dc in P.edge_options[a]:
                    ne = min(e - c, P.capacity)
                    if ne < P.e_min - eps:
                        continue
                    visits[u] += 1
                    edges.append(a)
                    levels.append(k)
                    rec(u, t + tau, g + dc, m, w + (dsoh * c if c > 0 else 0.0), ne, edges, levels, charges, ())
                    edges.pop()
                    levels.pop()
                    visits[u] -= 1

    try:
        rec(P.origin, 0.0, 0.0, 0.0, 0.0, P.e_init, [], [], [], ())
    except OverflowError:
        return None
    return best[0], best[1], _front_of(pts)


def _max_node_visits(P: PreparedScenario, plan: RoutePlan) -> int:
    return max(Counter(plan.nodes).values()) if plan.nodes else 0


def _front_of(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    pts = sorted(set(points))
    out: list[tuple[float, float, float]] = []
    for p in pts:
        if not any(_dominates_tol(q, p, 1e-9) or all(_close(x, y, 1e-9, 1e-12) for x, y in zip(q, p, strict=True))
                   for q in out):
            out = [q for q in out if not _dominates_tol(p, q, 1e-9)]
            out.append(p)
    return out


def check_brute_force(inst: Instance, tally: Tally | None = None) -> Tally:
    """I9 (and I1/I2 on the tiny instance).

    BLS, BLS-A* and Pareto model consecutive stops at different stations of one node
    (BUG-3, fixed for them), so their oracle enumerates up to two stops per node visit (a
    tiny digraph has at most two stations); CFP is compared with the one-stop enumeration
    (BUG-3 is open for CFP)."""
    tally = tally if tally is not None else Tally()
    sc = build_scenario(inst)
    P = sc.prepare()
    tag = inst.describe()
    res = brute_force(P, stops_per_visit=2)
    if res is None:
        tally.skip("brute force (too many walks)")
        return tally
    value, decision, pts = res
    for name in ("bls", "bls-astar"):
        p = _run(P, name)
        check_plan(tally, P, p, f"{name} {tag}", True)
        if value == INF:
            tally.check("I9", not p.feasible, lambda: f"{name} feasible ({p.internal_objective!r}) but brute force "
                                                     f"found no route {tag}")
            continue
        if not tally.check("I9", p.feasible, lambda: f"{name} infeasible, brute force {value!r} {tag}"):
            continue
        if _close(p.internal_objective, value, 1e-9, 1e-9):
            tally.check("I9", True, "")
        elif p.internal_objective < value:
            # BLS may use a walk that visits a node more often than the enumeration allows
            tally.check("I9", _max_node_visits(P, p) > 2,
                        lambda: f"{name} {p.internal_objective!r} < brute force {value!r} on a walk the "
                                f"enumeration covers {tag}")
        else:
            rp = evaluate(P, decision)
            tally.check("I9", False, lambda: f"{name} {p.internal_objective!r} > brute force {value!r} "
                                             f"(oracle route replays to {rp.objective_value!r}, "
                                             f"feasible={rp.feasible}; decision {decision}) {tag}")
    if decision is not None:
        rp = evaluate(P, decision)
        tally.check("I9", rp.feasible and _close(rp.objective_value, value, 1e-9, 1e-9),
                    lambda: f"oracle decision replays to {rp.objective_value!r} != {value!r} {tag}")
    # continuous charging can only help (against routes with one stop per node visit)
    c = _run(P, "cfp")
    check_plan(tally, P, c, f"cfp {tag}", True)
    if value < INF:
        tally.check("I9", c.feasible, lambda: f"cfp infeasible, brute force {value!r} {tag}")
        if P.objective == "time" and c.feasible:
            one = value
            if len({ch.position for ch in decision.charges}) < len(decision.charges):
                r1 = brute_force(P)  # the optimum needs two stops at one node: enumerate one-stop routes
                one = r1[0] if r1 is not None else None
            if one is None:
                tally.skip("I9 cfp (one-stop enumeration too large)")
            else:
                tally.check("I9", c.total_time_s <= one + TOL_S,
                            lambda: f"cfp {c.total_time_s!r} > one-stop brute force {one!r} {tag}")
    # the Pareto front
    par = _run(P, "pareto")
    if value == INF:
        tally.check("I9", not par.feasible, lambda: f"pareto feasible, brute force found nothing {tag}")
        return tally
    if not tally.check("I9", par.feasible, lambda: f"pareto infeasible, brute force {value!r} {tag}"):
        return tally
    want = _front_of(pts)
    got = sorted(_crit(m) for m in par.alternatives)
    # every brute-force front point is matched by a member and vice versa
    miss = [p for p in want if not any(all(_close(x, y, 1e-7, 1e-9) for x, y in zip(g, p, strict=True))
                                       or _dominates_tol(g, p, 1e-7) for g in got)]
    extra = [g for g in got if any(_dominates_tol(p, g, 1e-7) for p in want)]
    if miss or extra:
        visits = max((_max_node_visits(P, m) for m in par.alternatives), default=0)
        tally.check("I9", visits > 2 and not miss,
                    lambda: f"pareto front differs from brute force: {len(miss)} missing (e.g. {miss[:1]}), "
                            f"{len(extra)} dominated members (e.g. {extra[:1]}) {tag}")
    else:
        tally.check("I9", len(got) >= 1, "")
    return tally


# =============================================================================
# the tests: fast tier
# =============================================================================
FAST_BLOCKS = 6
FAST_PER_BLOCK = 16  # 96 random instances
TINY_FAST = 48


def _assert_clean(tally: Tally) -> None:
    assert tally.n_failed == 0, "\n" + tally.report()


@pytest.mark.parametrize("block", range(FAST_BLOCKS))
def test_random_instances_fast(block):
    """I1-I8 and I10 on 16 random instances per block (generators, vehicles, options)."""
    tally = Tally()
    for seed in range(block * FAST_PER_BLOCK, (block + 1) * FAST_PER_BLOCK):
        check_instance(sample_instance(seed, "fast"), FAST, tally)
    _assert_clean(tally)
    assert tally.checked["I1"] > 50 * FAST_PER_BLOCK


@pytest.mark.parametrize("block", range(4))
def test_brute_force_tiny_digraphs_fast(block):
    """I9: BLS = exhaustive enumeration, Pareto front = enumerated front."""
    tally = Tally()
    n = TINY_FAST // 4
    for seed in range(block * n, (block + 1) * n):
        check_brute_force(sample_tiny(seed), tally)
    _assert_clean(tally)
    assert tally.checked["I9"] >= n


def test_sampler_is_deterministic_and_diverse():
    a = [sample_instance(s, "slow") for s in range(300)]
    b = [sample_instance(s, "slow") for s in range(300)]
    assert a == b
    assert {i.generator for i in a} == {"grid", "rgg", "corridor"}
    assert {i.vehicle for i in a} == set(VEHICLE_KEYS)
    prefs = [i.preferences() for i in a]
    assert {p["objective"] for p in prefs} == {"time", "cost"}
    assert set(SPEED_SETS) <= {p["speed_factors"] for p in prefs}
    assert any(p["reserve_soc"] == 0.0 for p in prefs) and any(p["reserve_soc"] == 0.3 for p in prefs)
    assert any(i.origin_station and i.initial_soc < p["reserve_soc"] for i, p in zip(a, prefs, strict=True))
    assert any(i.stacked for i in a) and any(i.dest_station for i in a)
    assert {(p["queue_aware"], p["time_dependent_waits"]) for p in prefs} == {
        (True, True), (True, False), (False, True), (False, False)}


# =============================================================================
# slow tier: thousands of instances
# =============================================================================
SLOW_BLOCKS = 40
SLOW_PER_BLOCK = 50  # 2000 random instances
TINY_SLOW = 1200


def _require_slow(request: pytest.FixtureRequest) -> None:
    """The slow tier runs only when asked for (``-m slow`` or ``EVROUTE_SLOW=1``):
    the repository's default ``pytest`` invocation does not deselect slow tests."""
    expr = request.config.getoption("markexpr", "") or ""
    if os.environ.get("EVROUTE_SLOW") != "1" and ("slow" not in expr or "not slow" in expr):
        pytest.skip("slow tier: run with `pytest -m slow tests/test_crossval.py` or EVROUTE_SLOW=1")


@pytest.mark.slow
@pytest.mark.parametrize("block", range(SLOW_BLOCKS))
def test_random_instances_slow(block, request):
    _require_slow(request)
    tally = Tally()
    for seed in range(block * SLOW_PER_BLOCK, (block + 1) * SLOW_PER_BLOCK):
        check_instance(sample_instance(seed, "slow"), SLOW, tally)
    _assert_clean(tally)


@pytest.mark.slow
@pytest.mark.parametrize("block", range(12))
def test_brute_force_tiny_digraphs_slow(block, request):
    _require_slow(request)
    tally = Tally()
    n = TINY_SLOW // 12
    for seed in range(10_000 + block * n, 10_000 + (block + 1) * n):
        check_brute_force(sample_tiny(seed), tally)
    _assert_clean(tally)


# =============================================================================
# edge cases: odd but legal inputs must not crash and must stay consistent
# =============================================================================
def _edge_variants(sc: Scenario) -> list[tuple[str, Scenario]]:
    o, d = sc.query.origin, sc.query.destination
    st_o = Station(id=len(sc.stations), node=o, power_kw=50.0, kind="DC", price_per_kwh=0.5, setup_s=0.0)
    with_o = sc.with_(stations=list(sc.stations) + [st_o])
    b = NetworkBuilder()
    net = sc.network
    for i in range(net.num_nodes):
        b.add_node(net.x[i], net.y[i], net.elevation[i])
    for e in range(net.num_edges):  # drop every edge into the destination
        if net.head[e] != d:
            b.add_edge(net.tail[e], net.head[e], length=net.length[e], speed_kmh=net.speed_kmh[e],
                       road_class=net.road_class[e])
    return [
        ("charging cap below the reserve", sc.with_preferences(reserve_soc=0.3, max_charge_soc=0.25)),
        ("arrival requirement above the charging cap", sc.with_preferences(min_arrival_soc=0.9, max_charge_soc=0.7)),
        ("empty battery at an origin station", with_o.with_query(initial_soc=0.0)),
        ("initial SoC below reserve at an origin station, zero setup", with_o.with_query(initial_soc=0.04)),
        ("full battery above the charging cap", sc.with_query(initial_soc=1.0).with_preferences(max_charge_soc=0.6)),
        ("zero reserve and arrival requirement", sc.with_preferences(reserve_soc=0.0, min_arrival_soc=0.0)),
        ("coarsest grid", sc.with_preferences(charge_step_soc=0.5)),
        ("finest grid", sc.with_preferences(charge_step_soc=0.001)),
        ("departure one second before midnight", sc.with_query(departure_s=86399.0)),
        ("departure after 24 h", sc.with_query(departure_s=86400.0 + 8 * 3600.0)),
        ("origin = destination", sc.with_query(destination=o)),
        ("origin = destination, must charge there", with_o.with_query(destination=o, initial_soc=0.05)),
        ("no stations", sc.with_(stations=[])),
        ("unreachable destination", sc.with_(network=b.build())),
        ("all stations on one node", sc.with_(stations=[replace(x, node=sc.stations[0].node) for x in sc.stations]
                                              if sc.stations else [])),
        ("safety factor 1.3 and no queues", sc.with_preferences(safety_factor=1.3, queue_aware=False)),
    ]


@pytest.mark.parametrize("base", ["grid", "tiny"])
def test_edge_cases_are_consistent(base):
    """Odd inputs: every planner runs, plans replay, exact planners agree."""
    if base == "grid":
        inst = sample_instance(3, "fast")
        sc = grid_scenario(4, 4, spacing_m=20000.0, n_stations=5, seed=11, initial_soc=0.3, charge_step_soc=0.05)
    else:
        inst = sample_tiny(5)
        sc = build_scenario(inst)
    tally = Tally()
    lean = replace(FAST, nesting=False, metamorphic=False, robustness=False)
    for i, (what, variant) in enumerate(_edge_variants(sc)):
        tag = replace(inst, seed=1000 + i, generator=f"{base}: {what}")
        check_instance(tag, lean, tally, scenario=variant)
    _assert_clean(tally)
    assert tally.checked["I1"] > 100


def test_neighbour_search_terminates_without_applicable_moves():
    """Regression: :func:`neighbours` drew operations in a ``while True`` loop and hung
    forever when none applies -- an edge-less plan (origin = destination) without an origin
    station, or one road without a detour, station or second speed level.  The fast tier
    never finished (``test_edge_cases_are_consistent``)."""
    sc = _single_edge(0.5)
    P = sc.prepare()
    assert P.net.num_edges == 2 and not P.stations and len(P.speed_factors) == 1
    for cont in (False, True):
        assert neighbours(P, PlanDecision([0], [0]), random.Random(1), cont) is None
        P0 = sc.with_query(destination=sc.query.origin).prepare()
        assert neighbours(P0, PlanDecision([], []), random.Random(1), cont) is None
    # with a choice of speed levels a neighbour exists again
    P2 = sc.with_preferences(speed_factors=(1.0, 0.8)).prepare()
    nd = neighbours(P2, PlanDecision([0], [0]), random.Random(1), False)
    assert nd is not None and nd.edges == [0] and nd.levels in ([0], [1])


# =============================================================================
# bugs found by this suite: strict xfail reproductions (remove the marker when fixed,
# keep the test as a regression test; see tests/fuzz_report.md)
# =============================================================================
#: BUG-1 instances (fast-profile sampler of an earlier revision, frozen here)
BUG1_TIE = Instance(seed=322, generator="corridor", size=(189.3, 20.0, False), vehicle="van75", temperature_c=-13.3,
                    initial_soc=0.577, departure_s=4436.2,
                    prefs=(("charge_step_soc", 0.073), ("max_charge_soc", 0.6), ("min_arrival_soc", 0.0),
                           ("objective", "time"), ("queue_aware", True), ("reserve_soc", 0.0), ("safety_factor", 1.0),
                           ("speed_factors", (1.0,)), ("time_dependent_waits", False)),
                    stacked=2, dest_station=True)
BUG1_ULP = Instance(seed=6, generator="grid", size=(3, 7, 14146.7, 8), vehicle="sedan75", temperature_c=34.0,
                    initial_soc=0.069, departure_s=71891.4,
                    prefs=(("charge_step_soc", 0.05), ("max_charge_soc", 0.889), ("min_arrival_soc", 0.15),
                           ("objective", "time"), ("queue_aware", False), ("reserve_soc", 0.15), ("safety_factor", 1.0),
                           ("speed_factors", (1.0,)), ("time_dependent_waits", True)),
                    origin_station=True, stacked=2)


def test_bug1_pareto_front_keeps_a_dominated_member():
    """Regression for BUG-1 (fixed in pareto.py): the front kept members that a later member
    dominates.  Lemma 3 (a new member never dominates an existing one) assumes exact key order,
    but routes with mathematically equal trip times (e.g. the same energy charged at two
    stations with the same charging power) reach the destination via different float sums,
    and the goal-directed keys t + T_lb(v) are not monotone in floating point; the dearer
    route was popped first and stayed in the front."""
    P = build_scenario(BUG1_TIE).prepare()
    crit = [_crit(m) for m in _run(P, "pareto").alternatives]
    dominated = [(i, j) for i in range(len(crit)) for j in range(len(crit)) if i != j and _dominates(crit[i], crit[j])]
    assert not dominated, [(crit[i], crit[j]) for i, j in dominated]


def test_bug1_pareto_front_differs_from_brute_force_on_a_tie():
    """Regression for BUG-1 on a 3-node brute-force instance (slow tier, tiny seed 10850): two
    AC stations with the same 22 kW power; charging more at the origin and less at the second
    station gives the same trip time in exact arithmetic, but 19897.815888469413 s vs ...417 s
    in floating point -- the member that is 1 ulp faster but 6.26 EUR dearer stayed in the
    front, which the exhaustive enumeration (I9) reports as dominated."""
    P = build_scenario(sample_tiny(10850)).prepare()
    res = brute_force(P)
    assert res is not None
    want = _front_of(res[2])
    got = [_crit(m) for m in _run(P, "pareto").alternatives]
    extra = [g for g in got if any(_dominates_tol(p, g, 1e-7) for p in want)]
    assert not extra, (extra, want)


def test_bug1_pareto_front_keeps_a_member_dominated_up_to_rounding():
    """Regression for BUG-1 (rounding variant): a member 1 ulp faster but 0.77 EUR dearer than
    another member with the same wear survived, although both routes have the same trip time
    in exact arithmetic."""
    P = build_scenario(BUG1_ULP).prepare()
    crit = [_crit(m) for m in _run(P, "pareto").alternatives]
    dominated = [(i, j) for i in range(len(crit)) for j in range(len(crit)) if i != j and _dominates_tol(crit[i], crit[j])]
    assert not dominated, [(crit[i], crit[j]) for i, j in dominated]


def test_bug2_scenario_json_round_trip_reproduces_the_plan():
    """Regression for BUG-2 (fixed): ``RoadNetwork.to_dict`` used to round x/y/elevation to
    1 cm and lengths to 1 mm, so a saved and reloaded scenario had different edge energies --
    objective values moved by ~1e-6 relative and exact (just-enough) CFP plans replayed as
    infeasible (reserve undercut by ~1e-5 kWh).  The dict is lossless now."""
    sc = grid_scenario(5, 5, seed=2, initial_soc=0.2, n_stations=6)
    p = _run(sc.prepare(), "cfp")
    assert p.feasible
    back = Scenario.from_dict(json.loads(json.dumps(sc.to_dict())))
    again = evaluate(back.prepare(), p.decision)
    assert again.feasible, again.violations
    assert again.objective_value == p.objective_value
    assert back.network == sc.network
    Pb, P = back.prepare(), sc.prepare()
    assert Pb.edge_time == P.edge_time and Pb.edge_energy == P.edge_energy and Pb.target_grid == P.target_grid
    assert _run(Pb, "cfp").decision == p.decision


def _two_station_line(objective: str, setup_s: float) -> Scenario:
    """O --30 km--> S --150 km--> D; S hosts a 50 kW DC (0.75 EUR/kWh) and a 22 kW AC
    (0.30 EUR/kWh) charger; -10 degC without preconditioning (DC derated to 17.5 kW,
    falling to 4 kW near full; AC keeps 11 kW)."""
    b = NetworkBuilder()
    o, s, d = b.add_node(0.0, 0.0, 100.0), b.add_node(30e3, 0.0, 100.0), b.add_node(180e3, 0.0, 100.0)
    b.add_road(o, s, road_class="motorway")
    b.add_road(s, d, road_class="motorway")
    stations = [Station(0, s, 50.0, "DC", price_per_kwh=0.75, setup_s=setup_s, daily_sessions=1.0),
                Station(1, s, 22.0, "AC", price_per_kwh=0.30, setup_s=setup_s, daily_sessions=1.0)]
    return Scenario("two-station line", b.build(), stations, get_vehicle("compact50"),
                    Query(o, d, initial_soc=0.3, departure_s=9 * 3600.0),
                    Conditions(temperature_c=-10.0, preconditioning=False),
                    Preferences(objective=objective, queue_aware=False, charge_step_soc=0.05))


def _queue_absorbs_line() -> Scenario:
    """O (fast dear DC + slow cheap AC) --60 km--> S (one-plug DC, evening queue) --200 km--> D.

    Leaving O after a slow, cheap AC charge reaches S ~2 h later than after a fast DC
    charge -- exactly while the queue at S drains at the FIFO limit (-0.95 s/s), so
    almost all of the DC route's head start is spent waiting."""
    b = NetworkBuilder()
    o, s, d = b.add_node(0.0, 0.0, 100.0), b.add_node(60e3, 0.0, 100.0), b.add_node(260e3, 0.0, 100.0)
    b.add_road(o, s, road_class="motorway")
    b.add_road(s, d, road_class="motorway")
    st = [Station(0, o, 50.0, "DC", plugs=4, price_per_kwh=0.79, setup_s=60.0, daily_sessions=0.0),
          Station(1, o, 22.0, "AC", plugs=4, price_per_kwh=0.30, setup_s=60.0, daily_sessions=0.0),
          Station(2, s, 150.0, "DC", plugs=1, price_per_kwh=0.5, setup_s=60.0, daily_sessions=30.0)]
    return Scenario("queue absorbs the head start", b.build(), st, get_vehicle("compact50"),
                    Query(o, d, initial_soc=0.05, departure_s=19 * 3600.0), Conditions(temperature_c=20.0),
                    Preferences(objective="cost", charge_step_soc=0.05, reserve_soc=0.1, time_dependent_waits=True))


def test_cost_dominance_with_time_dependent_waits_regression():
    """Regression for the 3-D cost dominance ``(g, t, e)`` that BLS used with
    time-dependent waits: a label that is earlier *and* cheaper in g is not better when
    a FIFO queue absorbs its head start (the waiting is paid at the value of time while
    the other label's money advantage persists).  Here the old rule discards the AC
    label that leads to the optimum; the planners must still match brute force."""
    P = _queue_absorbs_line().prepare()
    value, decision, _ = brute_force(P, max_visits=1)
    first = decision.charges[0]
    assert P.stations[first.station].kind == "AC"  # the optimum starts with the slow cheap charge
    lab = {}
    for s in (0, 1):  # the two labels at S after charging at O to the optimal first target
        f = P.cf[s]
        ch = f.time_at(first.target_kwh) - f.time_at(P.e_init)
        w = P.wait_s(s, 0.0)
        t = w + P.setup_s[s] + ch + P.edge_time[0][0]
        g = P.charge_cost(s, P.e_init, first.target_kwh, ch, w) + P.drive_cost(P.edge_time[0][0], P.edge_energy[0][0])
        lab[s] = (t, g)
    assert lab[0][0] <= lab[1][0] and lab[0][1] <= lab[1][1]  # (g, t, e) would discard the AC label
    for name in ("bls", "bls-astar"):
        p = _run(P, name)
        assert p.feasible and _close(p.objective_value, value, 1e-9, 1e-9), (name, p.objective_value, value)
    par = _run(P, "pareto")
    assert _close(min(m.generalized_cost for m in par.alternatives), value, 1e-9, 1e-9)


def test_two_station_line_reference_values():
    """The plans used by the BUG-3 reproductions are feasible and as described."""
    P = _two_station_line("cost", 60.0).prepare()
    assert P.capacity == 44.0 and P.cf[0].power_kw[-1] < P.cf[1].power_kw[0]  # derated DC slower than AC near full
    assert P.net.tail[0] == P.origin and P.net.head[0] == P.net.tail[2] and P.net.head[2] == P.destination
    double = evaluate(P, PlanDecision([0, 2], [0, 0], [ChargeDecision(1, 0, 35.2), ChargeDecision(1, 1, 39.6)]))
    assert double.feasible and len(double.stops) == 2
    assert {round(y, 9) for y in (35.2, 39.6)} <= {round(y, 9) for y in P.target_grid}


def test_bug3_consecutive_stops_at_two_stations_beat_the_grid_optimum():
    """Regression for BUG-3 (fixed for BLS/BLS-A*/Pareto): they allowed at most one charging
    stop per node visit, justified by 'two consecutive stops would pay the waiting and setup
    time twice'.  With two different stations at one node that is false: fast DC to 80 % then
    cheap AC to 90 % (both grid targets, accepted by evaluate()) costs 90.72 vs the claimed
    grid optimum 91.75."""
    P = _two_station_line("cost", 60.0).prepare()
    double = evaluate(P, PlanDecision([0, 2], [0, 0], [ChargeDecision(1, 0, 35.2), ChargeDecision(1, 1, 39.6)]))
    assert double.feasible
    for name in ("bls", "bls-astar", "pareto"):
        p = _run(P, name)
        assert p.objective_value <= double.objective_value + 1e-9, (name, p.objective_value, double.objective_value)


@pytest.mark.xfail(strict=True, reason=(
    "BUG-3 (CFP): CFP's guarantee covers routes with at most one stop per node visit only (module docs of "
    "cfp.py when found: a label 'charged here ... spawns no further stop'); with zero setup, DC to 37.4 kWh then AC to the same "
    "just-enough energy is 16 s faster than the 'exact' CFP optimum, and evaluate() accepts it"))
def test_bug3_consecutive_stops_beat_the_continuous_optimum():
    P = _two_station_line("time", 0.0).prepare()
    c = _run(P, "cfp")
    assert c.feasible and len(c.stops) == 1
    need = c.stops[0].energy_out_kwh
    double = evaluate(P, PlanDecision(c.edges, c.levels, [ChargeDecision(1, 0, 37.4), ChargeDecision(1, 1, need)]))
    assert double.feasible
    for name in ("cfp", "cfp-astar"):
        assert _run(P, name).total_time_s <= double.total_time_s + TOL_S


def _single_edge(initial_soc: float) -> Scenario:
    b = NetworkBuilder()
    o, d = b.add_node(0.0, 0.0, 100.0), b.add_node(30e3, 0.0, 100.0)
    b.add_road(o, d, road_class="motorway")
    return Scenario("single edge", b.build(), [], get_vehicle("compact50"),
                    Query(o, d, initial_soc=initial_soc, departure_s=9 * 3600.0), Conditions(),
                    Preferences(reserve_soc=0.0, min_arrival_soc=0.0, queue_aware=False))


def _zero_reserve_knife_edge() -> Scenario:
    """Initial energy that undershoots the consumption of the only edge by ~5e-14 kWh."""
    c = _single_edge(0.5).prepare().edge_energy[0][0]
    soc = c / 50.0  # compact50: 50 kWh usable at 20 degC
    for k in range(80):
        sc = _single_edge(soc - k * 2.0 ** -55)
        P = sc.prepare()
        if -1e-9 < P.e_init - P.edge_energy[0][0] < 0.0:
            return sc
    raise AssertionError("no knife-edge initial SoC found")


def test_bug4_zero_reserve_float_noise_is_not_stranding():
    """Regression for BUG-4 (fixed in evaluate.py): with reserve_soc = min_arrival_soc = 0,
    evaluate() used to flag 'battery empty' as soon as the energy was below 0.0 by any amount
    (strict ``< 0.0``), contradicting the 1e-6 kWh tolerance of the reserve and arrival checks
    (DEV_SPEC 1.2): a route arriving with -9e-16 kWh was returned by BLS/BLS-A*/Pareto/greedy/
    station-graph as infeasible ('battery empty after 30.0 km').  Now only an energy below
    ``-TOL_KWH`` strands."""
    P = _zero_reserve_knife_edge().prepare()
    assert -1e-9 < P.e_init - P.edge_energy[0][0] < 0.0
    direct = evaluate(P, PlanDecision([0], [0]))
    assert direct.feasible and direct.stranded_at_m is None, direct.violations
    tally = Tally()
    for name in ("bls", "bls-astar", "pareto", "greedy", "station-graph", "naive", "genetic"):
        p = _run(P, name)
        assert p.feasible, (name, p.message)
        check_plan(tally, P, p, name, name in ("bls", "bls-astar", "pareto"))
    _assert_clean(tally)


@pytest.mark.xfail(strict=True, reason=(
    "BUG-4 (CFP): cfp.py raises the reserve and the goal energy to a private floor of 10*eps = 1e-8 kWh "
    "when they are zero ('floor = 10.0 * eps', 'emin = max(P.e_min, floor)', 'egoal = max(P.e_dest_min, "
    "floor)' at the top of _search, cfp.py:903-907 at the time of writing), a workaround for the old "
    "strict evaluator; the route that ends at -9e-16 kWh (feasible for evaluate() and every other planner) "
    "is rejected and CFP reports that no route exists. Remove the floor now that evaluate() tolerates float "
    "noise"))
def test_bug4_cfp_zero_reserve_float_noise_is_not_stranding():
    P = _zero_reserve_knife_edge().prepare()
    assert evaluate(P, PlanDecision([0], [0])).feasible
    for name in ("cfp", "cfp-astar"):
        p = _run(P, name)
        assert p.feasible, (name, p.message)


@pytest.mark.xfail(strict=True, reason=(
    "BUG-4 (robustness): the Monte Carlo kernel still strands at any negative energy "
    "(robustness.py:427 'strand = [drives and m < 0.0 ...]' in _Outcomes.events, and robustness.py:720 "
    "'stranded = out.min_kwh[r] < 0.0' in the _verify cross-check) while evaluate() strands below -TOL_KWH: "
    "a zero-noise replay of the knife-edge plan counts 3/3 strandings, and verify=True raises "
    "'Monte Carlo replay disagrees with evaluate(): stranding, feasibility'. Both comparisons should be "
    "'< -TOL_KWH'"))
def test_bug4_monte_carlo_zero_reserve_float_noise_is_not_stranding():
    P = _zero_reserve_knife_edge().prepare()
    p = _run(P, "bls")
    assert p.feasible
    rep = monte_carlo(P, p, n=3, seed=1, trip_cv=0.0, edge_cv=0.0, wait_model="expected", verify=True)
    assert rep.planned_feasible and rep.n_strand == rep.n_reserve_breach == rep.n_arrival_short == 0
