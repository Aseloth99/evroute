"""Cross-validation of charging tariffs: the random instances of ``test_crossval.py`` with
prices that depend on the state of charge and on the clock.

Every instance of the cross-validation suite (generators, vehicles, temperatures, queue
models, speed levels, stacked and origin stations, ...) gets a random
:class:`~evroute.scenario.Tariff`: a high-SoC tier on or off (``high_soc`` 0.2..0.95,
factors 0.8..3), zero to two peak intervals (wrapping midnight, all day, ``(24, x)``),
peak factors 0.3..4 with or without a high-SoC factor of their own, and busy hours from the
queue model (``busy_utilisation`` between two hourly utilisations of the stations).  The
peak is usually aimed at a stop of the fastest route without a tariff: dear at the arrival
and ending soon after it, or a cheap period that begins soon after it.  Most instances use
the cost objective, where prices matter, with a value of time of 2..40 per hour, so that
holding for a cheaper period is often worth it.

The oracle never reads the price tables of :class:`~evroute.scenario.PreparedScenario`
(``cf``, ``cf_peak``, ``money_cf``, ``is_peak``, ``hold_candidates``):
:class:`TariffOracle` re-derives the price periods, the price changes, the money and the
charging targets from the ``Tariff`` and the station data (``Tariff`` docstring,
``docs/DEV_SPEC.md`` 1.2), and charging times, wear and waits come from the same scenario
prepared with the neutral tariff (charging functions without the high-SoC breakpoint).

Invariants (ids in the failure messages)

* **T1 replay** -- every plan of every planner (Pareto members included) replays
  identically through ``evaluate()``, through the oracle's first-principles replay (hold,
  then the wait at the join time, money of the period at the join time) and from its JSON
  (``RoutePlan.to_dict``, holds included); ``check_objective`` holds; exact planners report
  the evaluated objective; the stops' ``peak`` flags and holds are the oracle's; the time
  components add up.
* **T2 model** -- the oracle's periods, hold candidates, money and targets equal the
  prepared scenario's; charging times and wear equal those of the neutral tariff; the
  bounds' cheapest money per kWh covers both periods; the queues are FIFO (the premise of
  the completeness argument); the neutral tariff and tariffs that change no price give
  identical plans, Pareto fronts and search statistics (BLS-A* and two random planners).
* **T3 A* = Dijkstra order** -- ``bls == bls-astar`` (tight and basic potential),
  ``cfp == cfp-astar``.
* **T4 Pareto** -- fastest member = BLS(time), cheapest generalised cost = BLS(cost),
  members feasible and mutually non-dominated.
* **T5 time** -- the time objective never holds (BLS-A*, CFP, CFP-A*, the fastest Pareto
  member); its optimum does not depend on prices (same breakpoints, other factors), CFP's
  does not change with the tariff's breakpoint, and no plan of any planner beats CFP.
* **T6 monotone prices** -- raising a price factor, or adding peak or busy hours when the
  peak is the dearer period, never lowers the optimal generalised cost (same targets).
* **T7 metamorphic** -- relabelled nodes / edges / stations, a duplicated station, the
  departure 24 h later, departure and peak hours shifted by whole hours (queues off; busy
  hours through rotated demand profiles): the same optimum.
* **T8 brute force** -- on tiny digraphs an exhaustive enumeration of walks (each node at
  most twice), up to two stops per node visit (the same station again included), every
  target, joining the queue at once or after a hold until each price change in
  ``(t, t + 24 h]`` never beats BLS (cost and time), equals it whenever BLS's plan is
  enumerated, and its Pareto front is covered by the planner's front, none of whose members
  it dominates.  On the tiniest instances a branch-and-bound enumeration that may also hold
  until every 5-minute step of the next 24 h never beats BLS either, at the instance's
  value of time and at a second one (where the Pareto front's cheapest member equals BLS):
  holds until price changes are complete (``docs/algorithms.md``, "Charging tariffs").
* **T9 local search, robustness, serialisation** -- random feasible neighbours of the
  optimum (holds moved to arbitrary times, dropped or added, other targets, stops dropped /
  added / moved) are never better; zero-noise Monte Carlo reproduces the plan with its
  holds; the scenario JSON keeps the tariff losslessly and the plans replay identically on
  the reloaded scenario.

Tiers: the fast tier checks 48 instances in about 40 s; ``@pytest.mark.slow`` checks 460
(``pytest -m slow tests/test_crossval_tariffs.py`` or ``EVROUTE_SLOW=1``).
"""

from __future__ import annotations

import heapq
import json
import math
import random
from dataclasses import dataclass, field, replace
from itertools import pairwise
from typing import Any

import pytest
import test_crossval as cv

from evroute import Tariff
from evroute.algorithms.labels import charging_targets
from evroute.evaluate import TOL_KWH, check_objective, evaluate
from evroute.network import NetworkBuilder
from evroute.plan import ChargeDecision, PlanDecision, RoutePlan
from evroute.queueing import HIGHWAY_PROFILE, URBAN_PROFILE
from evroute.robustness import monte_carlo, replay_run
from evroute.scenario import (
    Conditions,
    Preferences,
    PreparedScenario,
    Query,
    Scenario,
    Station,
)
from evroute.vehicle import get_vehicle

H = 3600.0
DAY = 86400.0
INF = math.inf
EPS = 1e-9
TOL_S = cv.TOL_S
_close = cv._close

INVARIANTS = {
    "T1": "replay: evaluate, oracle replay, JSON; check_objective; internal = evaluated",
    "T2": "model: periods, hold candidates, money, targets; neutral = no-op tariff",
    "T3": "A* = Dijkstra order (bls tight/basic, cfp)",
    "T4": "pareto extremes = bls(time)/bls(cost), members non-dominated",
    "T5": "time objective: no holds, price-independent, nothing beats cfp",
    "T6": "raising prices never lowers the optimal cost",
    "T7": "metamorphic: relabel, duplicate station, +24 h, shifted clock",
    "T8": "brute force with holds (price changes; 5-minute grid on the tiniest)",
    "T9": "local search with holds, zero-noise Monte Carlo, serialisation",
}


@dataclass
class Tally(cv.Tally):
    """:class:`test_crossval.Tally` with the tariff invariants in its table."""

    def table(self) -> str:
        rows = ["| invariant | checks | failures |", "|---|---:|---:|"]
        for k, what in INVARIANTS.items():
            rows.append(f"| {k} {what} | {self.checked[k]} | {len(self.failed.get(k, []))} |")
        return "\n".join(rows)


# =============================================================================
# the oracle: the tariff re-derived from its definition
# =============================================================================
def utilisation(st: Station) -> list[float]:
    """``rho(h) = lambda(h) / (plugs x mu)`` from the station data (``model.md`` section 5)."""
    prof = st.demand_profile
    if isinstance(prof, str):
        prof = URBAN_PROFILE if prof == "urban" else HIGHWAY_PROFILE
    total = sum(prof) or 1.0
    mu = 60.0 / st.mean_service_min  # services per hour and plug
    out = []
    for w in prof:
        lam = st.daily_sessions * w / total
        out.append(lam / (st.plugs * mu) if st.plugs > 0 else (INF if lam > 0 else 0.0))
    return out


class TariffOracle:
    """Price periods, price changes, money and charging targets of ``sc.tariff``.

    Written from the specification only: a kWh stored at station ``s`` costs
    ``price / efficiency`` times the factor of the period (off-peak: 1 below ``high_soc``,
    ``high_soc_factor`` above; peak: ``peak_factor`` below, ``peak_factor x
    (peak_high_soc_factor or high_soc_factor)`` above); the period is that of the clock
    ``(departure + t) mod 24 h`` at the time ``t`` the vehicle joins the queue; peak hours are
    ``[start, end)`` intervals that may wrap midnight, busy hours the clock hours with
    ``rho(h) >= busy_utilisation``.  ``P0`` is the scenario prepared with the neutral tariff.
    """

    def __init__(self, sc: Scenario, P0: PreparedScenario) -> None:
        tf = sc.tariff
        self.P0 = P0
        self.dep = float(sc.query.departure_s)
        self.e_high = tf.high_soc * P0.capacity
        hi_pk = tf.high_soc_factor if tf.peak_high_soc_factor is None else tf.peak_high_soc_factor
        self.factors = {False: (1.0, tf.high_soc_factor), True: (tf.peak_factor, tf.peak_factor * hi_pk)}
        self.hours = [(float(a) * H, float(b) * H) for a, b in tf.peak_hours]
        veh = sc.vehicle
        self.unit = [st.price_per_kwh / (veh.charge_eff_ac if st.kind == "AC" else veh.charge_eff_dc)
                     for st in sc.stations]
        thr = tf.busy_utilisation
        self.busy = [[thr is not None and r >= thr for r in utilisation(st)] for st in sc.stations]
        nonempty = any(a < b or (a > b and (a < DAY or b > 0.0)) for a, b in self.hours)
        self.has_peak = [nonempty or any(b) for b in self.busy]
        (lo0, hi0), (lo1, hi1) = self.factors[False], self.factors[True]
        #: the high-SoC breakpoint exists where some price changes at high_soc x capacity
        self.tiered = hi0 != lo0 or (any(self.has_peak) and hi1 != lo1)
        #: the two periods have different prices (otherwise a hold is never worth it)
        self.priced = any(self.has_peak) and self.factors[True] != self.factors[False]
        self.changes = [self._changes(s) if self.priced else () for s in range(len(sc.stations))]

    # ------------------------------------------------------------ periods
    def peak_clock(self, s: int, clock: float) -> bool:
        for a, b in self.hours:
            if (a <= clock < b) if a < b else (a > b and (clock >= a or clock < b)):
                return True
        return self.busy[s][int(clock // H)]

    def peak(self, s: int, t: float) -> bool:
        return self.peak_clock(s, (self.dep + t) % DAY)

    def _changes(self, s: int) -> tuple[float, ...]:
        cand = {a % DAY for a, _ in self.hours} | {b % DAY for _, b in self.hours} | {h * H for h in range(24)}
        return tuple(sorted(x for x in cand if self.peak_clock(s, x) != self.peak_clock(s, (x - 0.5) % DAY)))

    def hold_times(self, s: int, t: float) -> list[float]:
        """Earliest trip times in ``(t, t + 24 h]`` at which ``s`` is in a new price period."""
        clock = (self.dep + t) % DAY
        out = []
        for x in self.changes[s]:
            d = x - clock
            if d <= 0.0:
                d += DAY
            h = t + d
            new = self.peak_clock(s, x)
            for _ in range(100_000):
                if h > t and self.peak(s, h) == new:
                    break
                h = math.nextafter(h, INF)
            else:  # pragma: no cover
                raise AssertionError("no float time in the new period")
            out.append(h)
        return sorted(out)

    # -------------------------------------------------------------- money
    def money(self, s: int, peak: bool, x: float, y: float) -> float:
        """Money of charging from ``x`` to ``y > x`` kWh in the given period."""
        lo, hi = self.factors[peak]
        eh = self.e_high
        return self.unit[s] * (lo * (min(y, eh) - min(x, eh)) + hi * (max(y, eh) - max(x, eh)))

    def targets(self, s: int) -> list[float]:
        """Target set ``grid+breaks``: the grid, the curve's breakpoints and the high-SoC energy."""
        P0 = self.P0
        cap = P0.e_charge_cap
        vals = [min(y, cap) for y in P0.target_grid if y <= cap + EPS]
        vals += [b for b in P0.cf[s].e_breaks[1:-1] if b <= cap]
        if self.tiered and self.e_high <= cap:
            vals.append(self.e_high)
        out: list[float] = []
        for y in sorted(vals):
            if y > 0 and not (out and y - out[-1] <= EPS):
                out.append(y)
        floor = P0.e_stop_min  # minimum stop SoC: the floor is a target too
        if floor > 0.0 and not any(abs(y - floor) <= EPS for y in out):
            out = sorted(out + [floor])
        return out

    # ------------------------------------------------------------- replay
    def replay(self, d: PlanDecision) -> tuple[bool, float, float, float, list[tuple[float, bool, float]]]:
        """``(feasible, time, money, generalised cost, [(hold, peak, money) per stop])`` of a decision,
        replayed from the model text with this oracle's prices."""
        P0 = self.P0
        net = P0.net
        nodes = [P0.origin]
        for i, a in enumerate(d.edges):
            if net.tail[a] != nodes[-1] or not 0 <= d.levels[i] < len(P0.speed_factors):
                return False, INF, INF, INF, []
            nodes.append(net.head[a])
        ok = nodes[-1] == P0.destination
        by_pos: dict[int, list[ChargeDecision]] = {}
        for c in d.charges:
            if not 0 <= c.position < len(nodes) or P0.stations[c.station].node != nodes[c.position]:
                return False, INF, INF, INF, []
            by_pos.setdefault(c.position, []).append(c)
        t = money = wear = drive = 0.0
        e = P0.e_init
        stops = []
        for i in range(len(nodes)):
            if i > 0 and e < P0.e_min - TOL_KWH:
                ok = False
            for c in by_pos.get(i, ()):
                s = c.station
                f = P0.cf[s]
                if c.target_kwh > P0.e_charge_cap + TOL_KWH:
                    ok = False
                y = max(e, min(c.target_kwh, P0.e_charge_cap))
                if P0.e_stop_min > 0.0 and max(e, y) < P0.e_stop_min - TOL_KWH:
                    ok = False  # the floor rule: a stop that starts below the floor ends at it
                hu = c.hold_until_s
                if hu is not None and not math.isfinite(hu):
                    return False, INF, INF, INF, []
                tj = hu if (hu is not None and hu > t) else t
                w = P0.wait_s(s, tj)
                ch = f.time_at(y) - f.time_at(e) if y > e else 0.0
                pk = self.peak(s, tj)
                mo = self.money(s, pk, e, y) if y > e else 0.0
                stops.append((tj - t, pk, mo))
                money += mo
                wear += f.soh_at(y) - f.soh_at(e)
                t = tj
                t += w + P0.setup_s[s] + ch
                e = y
            if i < len(nodes) - 1:
                if e < P0.e_min - TOL_KWH:
                    ok = False
                a, k = d.edges[i], d.levels[i]
                c = P0.edge_energy[k][a]
                if e - c < -TOL_KWH:
                    ok = False
                e = min(e - c, P0.capacity)
                t += P0.edge_time[k][a]
                drive += P0.drive_cost(P0.edge_time[k][a], c) - P0.vot_per_s * P0.edge_time[k][a]
        if e < P0.e_dest_min - TOL_KWH:
            ok = False
        g = P0.vot_per_s * t + P0.w_money * money + P0.w_deg * wear + drive
        return ok, t, money, g, stops


def _time_lb(P: PreparedScenario) -> list[float]:
    """Least driving time from every node to the destination (reverse Dijkstra)."""
    net = P.net
    dist = [INF] * net.num_nodes
    dist[P.destination] = 0.0
    heap = [(0.0, P.destination)]
    while heap:
        d, v = heapq.heappop(heap)
        if d > dist[v]:
            continue
        for a in net.in_edges[v]:
            u = net.tail[a]
            nd = d + min((o[1] for o in P.edge_options[a]), default=INF)
            if nd < dist[u]:
                dist[u] = nd
                heapq.heappush(heap, (nd, u))
    return dist


# =============================================================================
# instances
# =============================================================================
def stop_clocks(sc: Scenario) -> list[float]:
    """Clock hours at which the fastest route of ``sc`` without a tariff reaches its stations."""
    P = sc.with_(tariff=Tariff()).with_preferences(objective="time").prepare()
    p = _run(P, "bls-astar", max_labels=50_000)
    return [P.time_of_day(st.arrival_s) / H for st in p.stops] if p.feasible else []


def sample_tariff(rng: random.Random, sc: Scenario, aim: list[float] | None = None) -> Tariff:
    """A random tariff whose peak periods matter for the trip of ``sc``.

    With ``aim`` (clock hours of likely stops, :func:`stop_clocks`) the peak is usually placed
    around one of them: dear at the arrival and ending soon after it, or a cheap period that
    begins soon after it -- the situations in which holding pays."""
    dep_h = (sc.query.departure_s % DAY) / H
    tier = rng.random() < 0.6
    high_soc = rng.choice((0.5, 0.7, 0.8, 0.8, 0.9, round(rng.uniform(0.2, 0.95), 3)))
    hsf = rng.choice((1.25, 1.5, 2.0, 3.0, 0.8, round(rng.uniform(1.05, 3.0), 3))) if tier else 1.0
    peaks: list[tuple[float, float]] = []
    pf = rng.choice((0.6, 1.0, 1.3, 1.6, 2.5, 4.0, round(rng.uniform(1.05, 3.0), 3)))
    r = rng.random()
    if aim and r < 0.6:
        a = rng.choice(aim)
        if rng.random() < 0.65:  # dear at the arrival, cheaper soon after
            start = a - rng.uniform(0.0, 1.5)
            end = a + rng.choice((rng.uniform(0.05, 0.5), rng.uniform(0.5, 2.5)))
            pf = rng.choice((2.0, 3.0, 4.0, round(rng.uniform(1.2, 4.0), 3)))
        else:  # a cheap period begins soon after the arrival
            start = a + rng.uniform(0.05, 2.0)
            end = start + rng.uniform(0.5, 6.0)
            pf = rng.choice((0.3, 0.5, 0.7))
        peaks.append((round(start % 24.0, 2), round(end % 24.0, 2)))
    elif r < 0.75:
        for _ in range(rng.choice((1, 1, 2))):
            start = (dep_h + rng.uniform(-2.0, 6.0)) % 24.0
            length = rng.choice((0.25, 0.5, 1.0, 2.0, 3.0, 6.0, 12.0, 20.0, round(rng.uniform(0.1, 23.0), 2)))
            peaks.append((round(start, 2), round((start + length) % 24.0, 2)))  # end < start: wraps midnight
    elif r < 0.8:
        peaks = [(0, 24)]
    elif r < 0.85:
        x = round(rng.uniform(0.0, 24.0), 1)
        peaks = [rng.choice(((x, 24), (24, x)))]
    busy = None
    if rng.random() < 0.3:
        rho = sorted({u for st in sc.stations for u in utilisation(st) if math.isfinite(u)})
        if len(rho) >= 2:
            k = rng.randrange(len(rho) // 3, len(rho) - 1)
            busy = (rho[k] + rho[k + 1]) / 2.0  # between two values: no tie with any hour
        else:
            busy = 0.5
    return Tariff(
        high_soc=high_soc,
        high_soc_factor=hsf,
        peak_hours=tuple(peaks),
        peak_factor=pf,
        peak_high_soc_factor=rng.choice((None, None, 1.0, 2.0, 4.0)),
        busy_utilisation=busy,
    )


def tariff_instance(seed: int, profile: str = "fast") -> tuple[cv.Instance, Scenario]:
    """Instance ``seed`` of the cross-validation sampler with a random tariff (mostly aimed at
    a stop, :func:`sample_tariff`), mostly the cost objective and a random value of time."""
    rng = random.Random(f"evroute-crossval-tariff-{profile}-{seed}")
    base = cv.sample_instance(70_000 + seed, profile)
    prefs = dict(base.prefs)
    prefs["objective"] = rng.choice(("time", "cost", "cost", "cost"))
    prefs["value_of_time_per_h"] = rng.choice((2.0, 4.0, 6.0, 15.0, 40.0))
    if profile == "fast":  # 3-D cost dominance with holds: keep the label sets of the fast tier small
        prefs["charge_step_soc"] = max(prefs["charge_step_soc"], 0.04)
    inst = replace(base, prefs=tuple(sorted(prefs.items())))
    sc = cv.build_scenario(inst)
    return inst, sc.with_(tariff=sample_tariff(rng, sc, stop_clocks(sc)))


def tiny_tariff_instance(seed: int) -> tuple[cv.Instance, Scenario]:
    """A tiny digraph of the cross-validation suite (<= 7 nodes, <= 2 stations) with a tariff."""
    rng = random.Random(f"evroute-crossval-tariff-tiny-{seed}")
    base = cv.sample_tiny(70_000 + seed)
    prefs = dict(base.prefs)
    prefs["objective"] = rng.choice(("time", "cost", "cost", "cost"))
    prefs["value_of_time_per_h"] = rng.choice((2.0, 4.0, 6.0, 15.0, 40.0))
    prefs["speed_factors"] = prefs["speed_factors"][:2]  # holds multiply the walks: at most two levels
    n, span, n_st = base.size
    inst = replace(base, prefs=tuple(sorted(prefs.items())), size=(n, span, max(1, n_st)))
    sc = cv.build_scenario(inst)
    return inst, sc.with_(tariff=sample_tariff(rng, sc, stop_clocks(sc)))


def _describe(inst: cv.Instance, sc: Scenario) -> str:
    return f"{inst.describe()} {sc.tariff!r}"


# =============================================================================
# checks of one plan
# =============================================================================
def _holds(p: RoutePlan) -> bool:
    return p.hold_time_s > 0.0 or any(c.hold_until_s is not None for c in (p.decision.charges if p.decision else ()))


def _rebuilt_from_json(d: dict[str, Any], cap: float) -> PlanDecision:
    """The decision of a plan rebuilt from ``RoutePlan.to_dict`` alone (holds included)."""
    return PlanDecision(d["edges"], d["levels"],
                        [ChargeDecision(s["position"], s["station"], min(s["energy_out_kwh"], cap), s["hold_until_s"])
                         for s in d["stops"]])


def check_plan(tally: Tally, P: PreparedScenario, orc: TariffOracle, plan: RoutePlan, tag: str, exact: bool) -> None:
    """T1 for one plan."""
    if plan.decision is None:
        tally.check("T1", not plan.feasible, lambda: f"{tag}: feasible plan without a decision")
        return
    again = evaluate(P, plan.decision)
    tally.check("T1", again.feasible == plan.feasible and (not plan.feasible or again.objective_value
                                                          == plan.objective_value),
                lambda: f"{tag}: re-evaluation {again.objective_value!r} != {plan.objective_value!r}")
    if not plan.feasible:
        return
    tally.check("T1", check_objective(plan), lambda: f"{tag}: check_objective fails: evaluated "
                                                     f"{plan.objective_value!r} > internal {plan.internal_objective!r}")
    if exact:
        tally.check("T1", plan.internal_objective is not None and _close(plan.internal_objective,
                                                                         plan.objective_value, 1e-6, 1e-6),
                    lambda: f"{tag}: internal {plan.internal_objective!r} != evaluated {plan.objective_value!r}")
    ok, t, m, g, stops = orc.replay(plan.decision)
    tally.check("T1", ok and _close(t, plan.total_time_s, 1e-9, 1e-6) and _close(m, plan.money, 1e-9, 1e-9)
                and _close(g, plan.generalized_cost, 1e-9, 1e-9),
                lambda: f"{tag}: oracle replay ok={ok} t={t!r} money={m!r} g={g!r} vs evaluate "
                        f"t={plan.total_time_s!r} money={plan.money!r} g={plan.generalized_cost!r}")
    bad = [(i, st.peak, pk, st.hold_s, hd, st.money, mo) for i, (st, (hd, pk, mo)) in enumerate(zip(plan.stops, stops))
           if st.peak != pk or not _close(st.hold_s, hd, 1e-9, 1e-6) or not _close(st.money, mo, 1e-9, 1e-9)]
    tally.check("T1", len(stops) == len(plan.stops) and not bad,
                lambda: f"{tag}: stops (i, peak, oracle peak, hold, oracle hold, money, oracle money) {bad[:2]}")
    parts = plan.drive_time_s + plan.hold_time_s + plan.wait_time_s + plan.setup_time_s + plan.charge_time_s
    tally.check("T1", _close(parts, plan.total_time_s, 1e-9, 1e-6),
                lambda: f"{tag}: time components {parts!r} != total {plan.total_time_s!r}")
    tally.check("T1", not cv.check_bookkeeping(P, plan), lambda: f"{tag}: {cv.check_bookkeeping(P, plan)}")
    # the plan's JSON is enough to rebuild it, holds included
    back = json.loads(json.dumps(plan.to_dict(trace_events=0), allow_nan=False))
    r = evaluate(P, _rebuilt_from_json(back, P.e_charge_cap))
    tally.check("T1", r.feasible and r.objective_value == plan.objective_value and r.money == plan.money,
                lambda: f"{tag}: decision rebuilt from RoutePlan JSON replays to {r.objective_value!r} "
                        f"(money {r.money!r}) vs {plan.objective_value!r} (money {plan.money!r}) {r.message}")


# =============================================================================
# the per-instance check
# =============================================================================
@dataclass
class Budget:
    bls_labels: int = 300_000
    bls_dijkstra_labels: int = 200_000
    cfp_labels: int = 200_000
    cfp_dijkstra_labels: int = 60_000
    pareto_labels: int = 120_000
    pareto_max_nodes: int = 30
    genetic: dict = field(default_factory=lambda: {"population": 12, "generations": 8, "patience": 4})
    metamorphic_relations: int = 2
    mutations: int = 40


FAST = Budget()
SLOW = Budget(pareto_max_nodes=50, metamorphic_relations=3, mutations=60)


def _run(P: PreparedScenario, name: str, **opts: Any) -> RoutePlan:
    return cv._run(P, name, **opts)


def _value(p: RoutePlan) -> float:
    return p.internal_objective if p.feasible else INF


def check_model(tally: Tally, P: PreparedScenario, orc: TariffOracle, rng: random.Random, tag: str) -> None:
    """T2: the prepared price data against the oracle."""
    P0 = orc.P0
    tally.check("T2", P.tariff_time_dependent == (orc.priced and any(orc.changes)),
                lambda: f"tariff_time_dependent={P.tariff_time_dependent} vs oracle {tag}")
    for s in range(len(P.stations)):
        tg = charging_targets(P, s, "grid+breaks")
        want = orc.targets(s)
        tally.check("T2", len(tg) == len(want) and all(abs(a - b) <= EPS for a, b in zip(tg, want)),
                    lambda: f"station {s}: targets {tg} != oracle {want} {tag}")
        f, f0 = P.cf[s], P0.cf[s]
        bad = []
        for _ in range(6):
            t = rng.choice((rng.uniform(0.0, 2 * DAY), rng.uniform(0.0, 3 * H)))
            hc = P.hold_candidates(s, t)
            ho = orc.hold_times(s, t)
            if P.is_peak(s, t) != orc.peak(s, t):
                bad.append(f"is_peak({t!r}) = {P.is_peak(s, t)}")
            if len(hc) != len(ho) or any(abs(a - b) > 1e-6 or P.is_peak(s, a) != orc.peak(s, a)
                                         for a, b in zip(hc, ho)):
                bad.append(f"hold_candidates({t!r}) = {hc} vs oracle {ho}")
            x, y = sorted(rng.uniform(0.0, P.capacity) for _ in range(2))
            if y - x < 1e-6:
                continue
            mo = P.money_cf(s, t).money(x, y)
            if not _close(mo, orc.money(s, orc.peak(s, t), x, y), 1e-12, 1e-12):
                bad.append(f"money {x}->{y} at {t!r}: {mo!r} vs oracle {orc.money(s, orc.peak(s, t), x, y)!r}")
            if not (_close(f.charge_time(x, y), f0.charge_time(x, y), 1e-12, 1e-9)
                    and _close(f.soh(x, y), f0.soh(x, y), 1e-12, 1e-15)
                    and P.cf_peak[s].power_kw == f.power_kw and P.cf_peak[s].e_breaks == f.e_breaks):
                bad.append(f"charging time / wear / breakpoints of the split function {x}->{y}")
        # FIFO queues (t + W(t) never decreases), the premise of "holds until price changes
        # are complete" (the 5-minute grid of T8 does not rely on it)
        ts = sorted(rng.uniform(0.0, 2 * DAY) for _ in range(40))
        ends = [t + P.wait_s(s, t) for t in ts]
        bad = [(a, b) for a, b in pairwise(ends) if b < a - 1e-9]
        tally.check("T2", not bad, lambda: f"station {s}: the queue is not FIFO: t + W(t) drops {bad[:1]} {tag}")
        # the bounds' cheapest money per kWh covers both periods
        lo = min(min(f.money_per_kwh) for f in P.cf_variants)
        clocks = {0.0, *(h * H for h in range(24)), *(x % DAY for iv in orc.hours for x in iv)}
        least = min(orc.unit[s] * x for pk in {orc.peak_clock(s, c) for c in clocks} for x in orc.factors[pk])
        tally.check("T2", lo <= least * (1 + 1e-12),
                    lambda: f"station {s}: cf_variants' least money {lo!r} > the oracle's {least!r} {tag}")


def check_tariff_instance(inst: cv.Instance, sc: Scenario, budget: Budget = FAST, tally: Tally | None = None) -> Tally:
    """Run every planner on ``sc`` and check T1-T7 and T9."""
    tally = tally if tally is not None else Tally()
    tag = _describe(inst, sc)
    P = sc.prepare()
    P0 = sc.with_(tariff=Tariff()).prepare()
    orc = TariffOracle(sc, P0)
    rng = random.Random(f"evroute-crossval-tariff-check-{inst.seed}")
    check_model(tally, P, orc, rng, tag)
    time_obj = P.objective == "time"
    other = "cost" if time_obj else "time"
    Po = sc.with_preferences(objective=other).prepare()
    Pt = P if time_obj else Po

    plans: dict[str, RoutePlan] = {
        "bls": _run(P, "bls", max_labels=budget.bls_dijkstra_labels),
        "bls-astar": _run(P, "bls-astar", max_labels=budget.bls_labels),
        "bls-astar/basic": _run(P, "bls-astar", potential="basic", max_labels=budget.bls_labels),
        f"bls-astar/{other}": _run(Po, "bls-astar", max_labels=budget.bls_labels),
        "cfp": _run(P, "cfp", max_labels=budget.cfp_dijkstra_labels),
        "cfp-astar": _run(P, "cfp-astar", max_labels=budget.cfp_labels),
    }
    small = P.net.num_nodes <= budget.pareto_max_nodes and len(P.speed_factors) <= 2
    if small and P.preferences.charge_step_soc >= 0.04:
        plans["pareto"] = _run(P, "pareto", max_labels=budget.pareto_labels)
    hopt = random.Random(f"evroute-crossval-tariff-heur-{inst.seed}")
    plans["naive"] = _run(P, "naive")
    plans["greedy"] = _run(P, "greedy", charge_to_soc=hopt.choice((None, 0.6, 0.9)))
    plans["station-graph"] = _run(P, "station-graph", charge_to_soc=hopt.choice((None, 0.7, 0.9)))
    plans["genetic"] = _run(P, "genetic", **dict(budget.genetic, seed=hopt.randrange(4)))
    limited = {n for n, p in plans.items() if cv._hit_limit(p)}
    for n in limited:
        tally.skip(f"{n} (max_labels)")
    # coverage statistics (reported with the skips)
    if P.tariff_time_dependent:
        tally.skip("info: time-dependent tariff")
    for n, p in cv._all_plans({k: plans[k] for k in ("bls-astar", "pareto") if k in plans}):
        if p.feasible and _holds(p):
            tally.skip(f"info: {n.split('[')[0]} plans that hold")
        if p.feasible and any(a.station == b.station and a.position == b.position
                              for a, b in zip(p.decision.charges, p.decision.charges[1:])):
            tally.skip(f"info: {n.split('[')[0]} plans that restart at a station")

    # ------------------------------------------------------------------ T1
    for name, p in cv._all_plans(plans):
        exact = name.startswith(("bls", "cfp", "pareto"))
        check_plan(tally, Po if name == f"bls-astar/{other}" else P, orc, p, f"{name} {tag}", exact)

    # ------------------------------------------------------------------ T3
    def same(a: str, b: str, value) -> None:
        if a in limited or b in limited:
            return
        pa, pb = plans[a], plans[b]
        if tally.check("T3", pa.feasible == pb.feasible, lambda: f"{a} feasible={pa.feasible} vs {b} {tag}") \
                and pa.feasible:
            tally.check("T3", _close(value(pa), value(pb), 1e-9, 1e-9),
                        lambda: f"{a}={value(pa)!r} vs {b}={value(pb)!r} {tag}")

    same("bls", "bls-astar", _value)
    same("bls", "bls-astar/basic", _value)
    same("cfp", "cfp-astar", lambda p: p.total_time_s)

    # reference optima of both objectives
    ref = {P.objective: plans["bls-astar"], other: plans[f"bls-astar/{other}"]}
    ref = {k: v for k, v in ref.items() if not cv._hit_limit(v)}
    cfp = next((plans[n] for n in ("cfp-astar", "cfp") if n not in limited), None)

    # ------------------------------------------------------------------ T4
    par = plans.get("pareto")
    if par is not None and "pareto" not in limited and par.feasible:
        members = par.alternatives
        crit = [cv._crit(m) for m in members]
        dom = [(i, j) for i in range(len(crit)) for j in range(len(crit))
               if i != j and cv._dominates_tol(crit[i], crit[j]) and crit[i][0] <= crit[j][0] * (1 + cv.TIE_RTOL)]
        tally.check("T4", members and all(m.feasible for m in members) and not dom,
                    lambda: f"pareto: infeasible or dominated members {dom[:1]} {tag}")
        if "time" in ref and ref["time"].feasible:
            best = min(c[0] for c in crit)
            tally.check("T4", _close(best, ref["time"].total_time_s, 1e-9, TOL_S),
                        lambda: f"pareto fastest {best!r} != bls(time) {ref['time'].total_time_s!r} {tag}")
            fastest = min(members, key=lambda m: m.total_time_s)
            tally.check("T5", not _holds(fastest), lambda: f"the fastest pareto member holds {tag}")
        if "cost" in ref and ref["cost"].feasible:
            best = min(m.generalized_cost for m in members)
            tally.check("T4", _close(best, ref["cost"].generalized_cost, 1e-7, 1e-7),
                        lambda: f"pareto cheapest {best!r} != bls(cost) {ref['cost'].generalized_cost!r} {tag}")
    if any(p.feasible for n, p in plans.items() if n not in limited):
        for n in ("bls", "bls-astar", "cfp", "cfp-astar", "pareto"):
            if n in plans and n not in limited:
                tally.check("T3", plans[n].feasible, lambda: f"{n} infeasible although another planner found "
                                                             f"a route {tag}")

    # ------------------------------------------------------------------ T5
    tp = ref.get("time")
    for name, p in (("bls-astar(time)", tp), ("cfp", plans["cfp"]), ("cfp-astar", plans["cfp-astar"])):
        if p is not None and p.feasible:
            tally.check("T5", not _holds(p), lambda: f"{name} holds under the time objective {tag}")
    if tp is not None and tp.feasible:
        alt = Tariff(high_soc=sc.tariff.high_soc, high_soc_factor=1.7 if orc.tiered else 1.0)
        Pa = sc.with_(tariff=alt).with_preferences(objective="time").prepare()
        if [f.e_breaks for f in Pa.cf] == [f.e_breaks for f in Pt.cf]:
            q = _run(Pa, "bls-astar", max_labels=budget.bls_labels)
            if not cv._hit_limit(q):
                tally.check("T5", q.feasible and _close(q.total_time_s, tp.total_time_s, 1e-12, 1e-9),
                            lambda: f"time optimum depends on prices: {tp.total_time_s!r} vs {q.total_time_s!r} "
                                    f"with {alt!r} {tag}")
        else:
            tally.skip("T5 price independence (breakpoints differ)")
    if cfp is not None and cfp.feasible:
        c0 = _run(P0, "cfp-astar", max_labels=budget.cfp_labels)
        if not cv._hit_limit(c0):
            tally.check("T5", c0.feasible and _close(c0.total_time_s, cfp.total_time_s, 1e-9, TOL_S),
                        lambda: f"cfp {cfp.total_time_s!r} != cfp without the tariff {c0.total_time_s!r} {tag}")
        for name, p in cv._all_plans(plans):
            if p.feasible:
                tally.check("T5", p.total_time_s >= cfp.total_time_s - TOL_S - 1e-9 * cfp.total_time_s,
                            lambda: f"{name} time {p.total_time_s!r} < cfp {cfp.total_time_s!r} {tag}")

    # ------------------------------------------------------------------ T6
    if "cost" in ref:
        check_monotone(tally, sc, orc, ref["cost"], rng, tag, budget,
                       par if par is not None and "pareto" not in limited and par.stats.extra.get("complete") else None)

    # ------------------------------------------------------------------ T7
    if "bls-astar" not in limited:
        check_metamorphic(tally, sc, inst, plans["bls-astar"], rng, tag, budget)

    # ------------------------------------------------------------------ T9
    best = plans["bls-astar"]
    if best.feasible and "bls-astar" not in limited:
        check_local_search(tally, P, orc, best, rng, tag, budget.mutations)
        try:
            rep = monte_carlo(P, best, n=2, seed=1, trip_cv=0.0, edge_cv=0.0, wait_model="expected", verify=True)
            tally.check("T9", rep.n_strand == 0 and rep.n_reserve_breach == 0 and rep.n_arrival_short == 0
                        and (P.preferences.safety_factor != 1.0 or rep.total_time_s.max == best.total_time_s),
                        lambda: f"zero-noise Monte Carlo of the optimum: {rep.total_time_s.max!r} vs "
                                f"{best.total_time_s!r} {tag}")
            # random worlds (arrivals before and after the planned holds) agree with evaluate()
            monte_carlo(P, best, n=6, seed=inst.seed, verify=True)
            tally.check("T9", True, "")
        except RuntimeError as exc:
            tally.check("T9", False, f"Monte Carlo with holds: {exc} {tag}")
    check_serialisation(tally, sc, P, plans, tag)
    return tally


def check_monotone(tally: Tally, sc: Scenario, orc: TariffOracle, base: RoutePlan, rng: random.Random, tag: str,
                   budget: Budget, par: RoutePlan | None = None) -> None:
    """T6: a dearer tariff (same charging targets) never has a cheaper optimum, and every
    member of its Pareto front is weakly dominated by a member of ``par`` (the front of the
    cheaper tariff: every route costs no more there)."""
    tf = sc.tariff
    r = rng.uniform(1.05, 2.0)
    (lo0, hi0), (lo1, hi1) = orc.factors[False], orc.factors[True]
    dearer_peak = lo1 >= lo0 and hi1 >= hi0
    options = [("high_soc_factor", {"high_soc_factor": tf.high_soc_factor * r}),
               ("peak_factor", {"peak_factor": tf.peak_factor * r}),
               ("peak_high_soc_factor", {"peak_high_soc_factor": hi1 / lo1 * r})]
    if dearer_peak:
        a = round(rng.uniform(0.0, 24.0), 2)
        options.append(("extra peak hours", {"peak_hours": tf.peak_hours + ((a, round((a + rng.uniform(0.2, 8.0))
                                                                                     % 24.0, 2)),)}))
        rho = sorted({u for st in sc.stations for u in utilisation(st) if 0.0 < u < INF})
        if rho:
            lower = rng.uniform(0.0, min(rho)) if tf.busy_utilisation is None else tf.busy_utilisation / r
            options.append(("busy hours", {"busy_utilisation": max(lower, 1e-6)}))
    what, kw = rng.choice(options)
    hi = sc.with_tariff(**kw)
    Ph = hi.with_preferences(objective="cost").prepare()
    Pl = sc.with_preferences(objective="cost").prepare()
    if [f.e_breaks for f in Ph.cf] != [f.e_breaks for f in Pl.cf]:
        tally.skip("T6 (the dearer tariff has other breakpoints)")
        return
    p = _run(Ph, "bls-astar", max_labels=budget.bls_labels)
    if cv._hit_limit(p):
        tally.skip("T6 (max_labels)")
        return
    v0, v1 = _value(base), _value(p)
    tally.check("T6", v1 == INF if v0 == INF else v1 >= v0 - 1e-9 * abs(v0) - 1e-9,
                lambda: f"dearer tariff ({what}: {hi.tariff!r}) has a cheaper optimum {v1!r} < {v0!r} {tag}")
    if par is not None:
        ph = _run(Ph, "pareto", max_labels=budget.pareto_labels)
        if not ph.stats.extra.get("complete"):
            tally.skip("T6 pareto (max_labels)")
            return
        low = [cv._crit(m) for m in par.alternatives]
        bad = [c for c in (cv._crit(m) for m in ph.alternatives)
               if not any(all(x <= y + 1e-9 * abs(y) + 1e-9 for x, y in zip(g, c)) for g in low)]
        tally.check("T6", not bad, lambda: f"dearer tariff ({what}: {hi.tariff!r}): pareto members {bad[:2]} not "
                                           f"covered by the cheaper front {tag}")


def _shift_hours(peaks: tuple, k: int) -> tuple:
    out = []
    for a, b in peaks:
        a, b = float(a), float(b)
        if a % 24.0 == b % 24.0:
            out.append((0.0, 24.0) if (a, b) == (0.0, 24.0) else (a, b))  # all day / never
        else:
            out.append(((a + k) % 24.0, (b + k) % 24.0))
    return tuple(out)


def check_metamorphic(tally: Tally, sc: Scenario, inst: cv.Instance, best: RoutePlan, rng: random.Random, tag: str,
                      budget: Budget) -> None:
    """T7 (and the neutral half of T2): equivalent instances have the same optimum."""
    P = sc.prepare()
    if P.net.num_nodes > 80 and len(P.speed_factors) > 1:
        return
    b = _value(best)
    tf = sc.tariff
    rel: list[tuple[str, Any]] = [
        ("relabelled nodes/edges/stations", lambda: cv.relabel(sc, inst.seed)),
        ("departure + 24 h", lambda: sc.with_query(departure_s=sc.query.departure_s + DAY)),
    ]
    if sc.stations:
        s = rng.choice(sc.stations)
        rel.append((f"duplicated station {s.id}",
                    lambda: sc.with_(stations=list(sc.stations) + [replace(s, id=len(sc.stations))])))
    k = rng.randint(1, 23)

    def shifted() -> tuple[Scenario, Scenario]:
        base = sc.with_preferences(queue_aware=False)
        st = [replace(x, demand_profile=[(HIGHWAY_PROFILE if x.demand_profile == "highway" else URBAN_PROFILE
                                           if x.demand_profile == "urban" else x.demand_profile)[(h - k) % 24]
                                          for h in range(24)]) for x in base.stations]
        moved = base.with_(stations=st).with_query(departure_s=(sc.query.departure_s + k * H) % DAY)
        return base, moved.with_tariff(peak_hours=_shift_hours(tf.peak_hours, k))

    rel.append((f"departure, peak and busy hours shifted by {k} h (no queues)", shifted))
    for what, make in rng.sample(rel, min(budget.metamorphic_relations, len(rel))):
        made = make()
        if isinstance(made, tuple):
            base_sc, made = made
            pb = _run(base_sc.prepare(), "bls-astar", max_labels=budget.bls_labels)
            if cv._hit_limit(pb):
                tally.skip("T7 (max_labels)")
                continue
            ref = _value(pb)
        else:
            ref = b
        p = _run(made.prepare(), "bls-astar", max_labels=budget.bls_labels)
        if cv._hit_limit(p):
            tally.skip("T7 (max_labels)")
            continue
        v = _value(p)
        tally.check("T7", (v == INF and ref == INF) or (v < INF and ref < INF and _close(v, ref, 1e-9, TOL_S)),
                    lambda: f"{what}: bls-astar {v!r} != {ref!r} {tag}")
    # a tariff that changes no price is the neutral tariff: identical search for every planner
    noop = Tariff(high_soc=rng.choice((0.3, 0.8, tf.high_soc)), peak_hours=tf.peak_hours or ((7, 9),),
                  peak_high_soc_factor=rng.choice((None, 1.0)), busy_utilisation=tf.busy_utilisation)
    P0, Pn = sc.with_(tariff=Tariff()).prepare(), sc.with_(tariff=noop).prepare()
    names = [("bls-astar", {"max_labels": budget.bls_labels})] + rng.sample(
        [("bls", {"max_labels": budget.bls_dijkstra_labels}), ("cfp-astar", {"max_labels": budget.cfp_labels}),
         ("greedy", {}), ("station-graph", {}), ("naive", {}), ("genetic", dict(budget.genetic, seed=1))]
        + ([("pareto", {"max_labels": budget.pareto_labels})]
           if P.net.num_nodes <= budget.pareto_max_nodes and len(P.speed_factors) <= 2 else []), 2)
    for name, opts in names:
        a, c = _run(P0, name, **opts), _run(Pn, name, **opts)

        def key(p: RoutePlan) -> tuple:
            st = p.stats
            return (p.feasible, p.decision, p.objective_value, p.internal_objective, p.nodes,
                    [x.decision for x in p.alternatives], [x.objective_value for x in p.alternatives],
                    None if st is None else (st.settled, st.generated, st.pushes))

        tally.check("T2", key(a) == key(c), lambda: f"{name}: no-op tariff {noop!r} changes the plan: "
                                                    f"{c.objective_value!r} vs {a.objective_value!r} {tag}")


def neighbours_with_holds(P: PreparedScenario, orc: TariffOracle, d: PlanDecision, rng: random.Random) -> PlanDecision | None:
    """:func:`test_crossval.neighbours` plus moves of the voluntary holds: a hold until a random
    time of the next 24 h, just before / after a price change, or none; a new stop with a hold."""
    if rng.random() < 0.4 or not d.charges:
        return cv.neighbours(P, d, rng, False)
    ch = list(d.charges)
    ev = evaluate(P, d)
    arrivals = [st.arrival_s for st in ev.stops] if ev.stops and len(ev.stops) == len(ch) else None
    k = rng.randrange(len(ch))
    c = ch[k]
    t = arrivals[k] if arrivals is not None else 0.0
    op = rng.choice(("random", "random", "near change", "none", "other station"))
    if op == "random":
        hold = t + rng.uniform(0.0, DAY)
    elif op == "near change":
        hs = orc.hold_times(c.station, t) or [t + rng.uniform(0.0, DAY)]
        hold = rng.choice(hs) + rng.choice((-60.0, -1.0, -1e-6, 1e-6, 1.0, 60.0, 600.0))
    elif op == "none":
        hold = None
    else:
        others = [s for s in P.station_at.get(P.stations[c.station].node, ()) if s != c.station]
        if not others:
            return cv.neighbours(P, d, rng, False)
        s2 = rng.choice(others)
        tg = charging_targets(P, s2)
        ch.insert(k + 1, ChargeDecision(c.position, s2, rng.choice(tg),
                                        rng.choice((None, t + rng.uniform(0.0, DAY)))))
        return PlanDecision(list(d.edges), list(d.levels), ch)
    ch[k] = ChargeDecision(c.position, c.station, c.target_kwh, hold)
    return PlanDecision(list(d.edges), list(d.levels), ch)


def check_local_search(tally: Tally, P: PreparedScenario, orc: TariffOracle, plan: RoutePlan, rng: random.Random,
                       tag: str, n: int) -> None:
    """T9: no feasible neighbour (targets on the grid, holds anywhere) beats the BLS-A* optimum."""
    best = plan.objective_value
    better = []
    for _ in range(n):
        nd = neighbours_with_holds(P, orc, plan.decision, rng)
        if nd is None:
            break
        q = evaluate(P, nd)
        if not q.feasible:
            continue
        tally.checked["T9"] += 1
        if q.objective_value < best - TOL_S - 1e-9 * abs(best):
            better.append((q.objective_value, nd))
    if better:
        v, nd = min(better, key=lambda x: x[0])
        tally.failed["T9"].append(f"bls-astar {best!r} beaten by {len(better)} neighbour(s), best {v!r}: {nd} {tag}")


def check_serialisation(tally: Tally, sc: Scenario, P: PreparedScenario, plans: dict, tag: str) -> None:
    """T9: the scenario JSON keeps the tariff; the plans replay identically on the reloaded scenario."""
    d1 = json.loads(json.dumps(sc.to_dict(), allow_nan=False))
    sc1 = Scenario.from_dict(d1)
    tally.check("T9", sc1.tariff == sc.tariff and json.loads(json.dumps(sc1.to_dict())) == d1
                and ("tariff" in d1) == (sc.tariff != Tariff()),
                lambda: f"scenario JSON round trip changes the tariff or is no fixed point {tag}")
    P1 = sc1.prepare()
    tally.check("T9", P1.cf == P.cf and P1.cf_peak == P.cf_peak and P1.peak_intervals == P.peak_intervals
                and P1.price_changes == P.price_changes and P1.tariff_time_dependent == P.tariff_time_dependent,
                lambda: f"the reloaded scenario prepares other price data {tag}")
    for name in ("bls-astar", "pareto", "greedy"):
        p = plans.get(name)
        if p is None or not p.feasible:
            continue
        r = evaluate(P1, p.decision)
        tally.check("T9", r.feasible and r.objective_value == p.objective_value and r.money == p.money,
                    lambda: f"{name}: objective after the round trip {r.objective_value!r} vs "
                            f"{p.objective_value!r} {tag}")


# =============================================================================
# T8: brute force on tiny digraphs
# =============================================================================
def _energy_lb(P: PreparedScenario) -> list[float]:
    """Least energy from every node to the destination (reverse Bellman-Ford, regeneration
    included; ``inf`` where the destination cannot be reached)."""
    net = P.net
    dist = [INF] * net.num_nodes
    dist[P.destination] = 0.0
    for _ in range(net.num_nodes):
        changed = False
        for a in range(net.num_edges):
            d = dist[net.head[a]]
            if d < INF:
                nd = d + min((o[2] for o in P.edge_options[a]), default=INF)
                if nd < dist[net.tail[a]]:
                    dist[net.tail[a]] = nd
                    changed = True
        if not changed:
            break
    return dist


def brute_force(P: PreparedScenario, orc: TariffOracle, *, max_visits: int = 2, stops_per_visit: int = 2,
                grid_s: float | None = None, bound: float | None = None, max_steps: int = 2_000_000):
    """Exhaustive enumeration with the evaluator's rules and the oracle's prices.

    Walks visit every node at most ``max_visits`` times; at a node visit up to
    ``stops_per_visit`` stops at any of its stations (the same one again included), each to
    any target of the oracle's target set, joining the queue at once or after a hold until
    each price change in ``(t, t + 24 h]`` (:meth:`TariffOracle.hold_times`) and, with
    ``grid_s``, until every multiple of ``grid_s`` seconds after the arrival up to 24 h.
    With a minimum stop SoC a stop that starts below the floor ends at it or above.

    A partial route is only cut by lower bounds of its completions: at least the least
    remaining driving time, and the energy it still lacks (least remaining consumption plus
    the arrival requirement minus its energy) charged at the least time, money and wear per
    kWh of any station in any price period.  Without ``bound`` it returns ``(best value of
    P's objective, its decision, non-dominated (t, money, wear))`` and cuts a partial route
    whose bounds are weakly dominated by a complete route.  With ``bound`` it is a
    branch-and-bound search for a route whose value is *below* ``bound`` and returns
    ``(value, decision, [])`` of the best one found (``(bound, None, [])`` if there is
    none).  ``None`` if the enumeration needs more than ``max_steps`` steps.
    """
    P0 = orc.P0
    net = P0.net
    time_obj = P.objective == "time"
    vot, wm, wd = P0.vot_per_s, P0.w_money, P0.w_deg
    dsoh = P0.drive_soh_per_kwh
    nst = len(P0.stations)
    tg = [orc.targets(s) for s in range(nst)]
    TY = [[P0.cf[s].time_at(y) for y in tg[s]] for s in range(nst)]
    SY = [[P0.cf[s].soh_at(y) for y in tg[s]] for s in range(nst)]
    tlb = _time_lb(P0)
    elb = [x + P0.e_dest_min for x in _energy_lb(P0)]
    # least time, money and wear (and generalised cost) per charged kWh
    factor = min(min(f) for f in orc.factors.values())
    per = [(3600.0 / max(P0.cf[s].power_kw), orc.unit[s] * factor, min(P0.cf[s].soh_pct_per_kwh)) for s in range(nst)]
    rt = min((x[0] for x in per), default=INF)
    rm = min((x[1] for x in per), default=INF)
    rw = min((x[2] for x in per), default=INF)
    rg = min((vot * x[0] + wm * x[1] + wd * x[2] for x in per), default=INF)
    grid = [k * grid_s for k in range(1, int(DAY / grid_s) + 1)] if grid_s else []
    floor = P0.e_stop_min - EPS if P0.e_stop_min > 0.0 else -INF  # minimum stop SoC (floor rule)
    visits = [0] * net.num_nodes
    visits[P0.origin] = 1
    best: list[Any] = [INF if bound is None else bound, None]
    pts: list[tuple[float, float, float]] = []
    count = [0]

    def cut(v: int, t: float, m: float, w: float, e: float) -> bool:
        """Can no completion of this partial route be (part of) a better result?"""
        lt = t + tlb[v]
        if lt == INF or elb[v] == INF:
            return True
        d = elb[v] - e
        if bound is not None:
            if time_obj:
                return (lt + d * rt if d > 0.0 else lt) >= best[0]
            return (vot * lt + wm * m + wd * w + (d * rg if d > 0.0 else 0.0)) >= best[0]
        if d > 0.0:
            lt, m, w = lt + d * rt, m + d * rm, w + d * rw
        return any(q[0] <= lt and q[1] <= m and q[2] <= w for q in pts)

    def rec(v, t, m, w, e, edges, levels, charges, here):
        count[0] += 1
        if count[0] > max_steps:
            raise OverflowError
        if cut(v, t, m, w, e):
            return
        if v == P0.destination and e >= P0.e_dest_min - EPS:
            value = t if time_obj else vot * t + wm * m + wd * w
            if bound is None:
                pts.append((t, m, w))
            if value < best[0]:
                best[0] = value
                best[1] = PlanDecision(list(edges), list(levels), [ChargeDecision(*c) for c in charges])
            return
        if e >= P0.e_min - EPS:  # drive first: complete routes found early cut more partial routes
            for a in net.out_edges[v]:
                u = net.head[a]
                if visits[u] >= max_visits:
                    continue
                for k, tau, c, _dc in P0.edge_options[a]:
                    ne = min(e - c, P0.capacity)
                    if ne < P0.e_min - EPS:
                        continue
                    visits[u] += 1
                    edges.append(a)
                    levels.append(k)
                    rec(u, t + tau, m, w + (dsoh * c if c > 0 else 0.0), ne, edges, levels, charges, 0)
                    edges.pop()
                    levels.pop()
                    visits[u] -= 1
        if here < stops_per_visit:
            for s in P0.station_at.get(v, ()):
                f = P0.cf[s]
                fx, sx = f.time_at(e), f.soh_at(e)
                su = P0.setup_s[s]
                for tj in (t, *orc.hold_times(s, t), *(t + x for x in grid)):
                    ws = P0.wait_s(s, tj) + su
                    if cut(v, tj + ws, m, w, e):  # every stop that joins at tj ends later than this
                        continue
                    pk = orc.peak(s, tj)
                    for j, y in enumerate(tg[s]):
                        # a stop may end below the reserve only if another stop can follow
                        if y > e + EPS and (y >= P0.e_min - EPS or here + 1 < stops_per_visit) and (
                                e >= floor or y >= floor):
                            rec(v, tj + (ws + (TY[s][j] - fx)), m + orc.money(s, pk, e, y), w + (SY[s][j] - sx), y,
                                edges, levels, charges + [(len(edges), s, y, tj if tj > t else None)], here + 1)

    try:
        rec(P0.origin, 0.0, 0.0, 0.0, P0.e_init, [], [], [], 0)
    except OverflowError:
        return None
    return best[0], best[1], cv._front_of(pts)


def _shape(d: PlanDecision, nodes: list[int]) -> tuple[int, int]:
    """(most visits of a node, most stops at one node visit) of a decision."""
    stops: dict[int, int] = {}
    for c in d.charges:
        stops[c.position] = stops.get(c.position, 0) + 1
    visits: dict[int, int] = {}
    for v in nodes:
        visits[v] = visits.get(v, 0) + 1
    return max(visits.values(), default=0), max(stops.values(), default=0)


def _grid_bound_check(tally: Tally, P: PreparedScenario, orc: TariffOracle, bls: RoutePlan, grid_s: float,
                      max_steps: int, tag: str) -> None:
    """Branch and bound: is there any route (holds on the ``grid_s`` grid included) below BLS?"""
    v = bls.objective_value
    res = brute_force(P, orc, max_visits=1, grid_s=grid_s, bound=v - 1e-9 * abs(v) - 1e-9, max_steps=max_steps)
    if res is None:
        tally.skip("T8 grid (too many walks)")
        return
    if res[1] is not None:
        rp = evaluate(P, res[1])
        tally.check("T8", False, lambda: f"grid oracle beats bls: {res[0]!r} < {v!r} (evaluate: "
                                         f"{rp.objective_value!r} feasible={rp.feasible}): {res[1]} {tag}")
        return
    tally.checked["T8"] += 1
    # the search is not vacuous: with a slightly larger bound it finds BLS's value
    if max(_shape(bls.decision, bls.nodes)) <= 1 or _shape(bls.decision, bls.nodes) == (1, 2):
        res = brute_force(P, orc, max_visits=1, grid_s=grid_s, bound=v + 1e-7 * abs(v) + 1e-7, max_steps=max_steps)
        if res is None:
            tally.skip("T8 grid (too many walks)")
        else:
            tally.check("T8", res[1] is not None and _close(res[0], v, 1e-9, 1e-9),
                        lambda: f"grid oracle does not reach bls {v!r}: {res[0]!r} {tag}")


def check_brute_force(inst: cv.Instance, sc: Scenario, tally: Tally | None = None, *, grid_s: float | None = None,
                      max_steps: int = 2_000_000) -> Tally:
    """T8 on one tiny instance (and T1 on its plans)."""
    tally = tally if tally is not None else Tally()
    tag = _describe(inst, sc)
    P = sc.prepare()
    P0 = sc.with_(tariff=Tariff()).prepare()
    orc = TariffOracle(sc, P0)
    plans = {n: _run(P, n) for n in ("bls", "bls-astar")}
    for n, p in plans.items():
        check_plan(tally, P, orc, p, f"{n} {tag}", True)
    bls = plans["bls"]
    if P.tariff_time_dependent:
        tally.skip("info: time-dependent tariff")
    if bls.feasible:
        tally.skip("info: feasible")
        if _holds(bls):
            tally.skip("info: bls plans that hold")
    if grid_s is not None:
        if not bls.feasible:  # a hold never changes the energy: no grid needed for feasibility
            res = brute_force(P, orc, max_visits=1, max_steps=max_steps)
            if res is None:
                tally.skip("T8 grid (too many walks)")
            else:
                tally.check("T8", res[1] is None, lambda: f"grid oracle finds {res[0]!r}, bls infeasible {tag}")
            return tally
        _grid_bound_check(tally, P, orc, bls, grid_s, max_steps, tag)
        # another value of time: another supported point of the (time, money, wear) front
        vot = random.Random(f"evroute-crossval-tariff-vot-{inst.seed}").choice((1.0, 30.0, 120.0))
        sv = sc.with_preferences(value_of_time_per_h=vot)
        Pv = sv.prepare()
        ov = TariffOracle(sv, sv.with_(tariff=Tariff()).prepare())
        bv = _run(Pv, "bls")
        check_plan(tally, Pv, ov, bv, f"bls vot={vot} {tag}", True)
        if bv.feasible:
            _grid_bound_check(tally, Pv, ov, bv, grid_s, max_steps, f"vot={vot} {tag}")
            par = _run(Pv, "pareto")
            best = min((m.generalized_cost for m in par.alternatives), default=INF)
            tally.check("T8", _close(best, bv.generalized_cost, 1e-7, 1e-7),
                        lambda: f"pareto cheapest at vot={vot} {best!r} != bls {bv.generalized_cost!r} {tag}")
        return tally
    res = brute_force(P, orc, max_steps=max_steps)
    if res is None:
        tally.skip("T8 (too many walks)")
        return tally
    value, decision, front = res
    for n, p in plans.items():
        if value == INF:
            tally.check("T8", not p.feasible, lambda: f"{n} feasible ({p.objective_value!r}), brute force none {tag}")
            continue
        if not tally.check("T8", p.feasible, lambda: f"{n} infeasible, brute force {value!r} {tag}"):
            continue
        tally.check("T8", p.objective_value <= value + 1e-9 * abs(value) + 1e-9,
                    lambda: f"brute force {value!r} beats {n} {p.objective_value!r}: {decision} {tag}")
        if max(_shape(p.decision, p.nodes)) <= 2:  # BLS's plan is one of the enumerated plans
            tally.check("T8", _close(p.objective_value, value, 1e-9, 1e-9),
                        lambda: f"{n} {p.objective_value!r} != brute force {value!r} on a plan it enumerates {tag}")
    if decision is not None:
        rp = evaluate(P, decision)
        tally.check("T8", rp.feasible and _close(rp.objective_value, value, 1e-9, 1e-9),
                    lambda: f"brute-force decision replays to {rp.objective_value!r} != {value!r} {tag}")
    # the Pareto front (money is always a criterion: holds and restarts whenever prices change)
    par = _run(P, "pareto")
    for m in par.alternatives:
        check_plan(tally, P, orc, m, f"pareto member {tag}", True)
    if value == INF:
        tally.check("T8", not par.feasible, lambda: f"pareto feasible, brute force none {tag}")
        return tally
    if not tally.check("T8", par.feasible and par.stats.extra["complete"], lambda: f"pareto infeasible {tag}"):
        return tally
    got = [cv._crit(m) for m in par.alternatives]
    miss = [o for o in front if not any(all(x <= y + 1e-9 * abs(y) + 1e-12 for x, y in zip(g, o)) for g in got)]
    extra = [g for g in got if any(cv._dominates_tol(o, g, 1e-7) for o in front)]
    tally.check("T8", not miss and not extra,
                lambda: f"pareto front vs brute force: {len(miss)} enumerated points not covered (e.g. {miss[:1]}), "
                        f"{len(extra)} members dominated by enumerated routes (e.g. {extra[:1]}) {tag}")
    held = [m for m in par.alternatives if _holds(m)]
    if held:
        tally.skip("info: pareto fronts with members that hold")
    tally.note("pareto members that hold", len(held))
    return tally


def tiniest_instance(seed: int) -> tuple[cv.Instance, Scenario]:
    """O -> A -> D (and sometimes O -> B -> D): chargers at A (one or two), sometimes at B
    and at the origin (then with an almost empty battery), a trip that must charge, a peak
    period that begins or ends around the arrival at A and a low value of time -- the
    instances on which holding pays, small enough for the 5-minute hold grid."""
    rng = random.Random(f"evroute-crossval-tariff-tiniest-{seed}")
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, rng.choice((100.0, 600.0)))
    a = b.add_node(rng.uniform(30e3, 80e3), rng.uniform(-10e3, 10e3), rng.choice((100.0, 900.0)))
    d = b.add_node(rng.uniform(150e3, 240e3), 0.0, rng.choice((50.0, 400.0)))
    roads = [(o, a), (a, d)]
    at = [a] * rng.choice((1, 1, 2))
    if rng.random() < 0.35:
        m = b.add_node(rng.uniform(40e3, 90e3), rng.uniform(-30e3, -10e3), 300.0)
        roads += [(o, m), (m, d)]
        at.append(m)
    origin = rng.random() < 0.25
    if origin:
        at.append(o)
    for u, v in roads:
        b.add_road(u, v, road_class=rng.choice(("motorway", "trunk", "primary")))
    stations = []
    for v in at:
        kind = rng.choice(("DC", "DC", "AC"))
        stations.append(Station(len(stations), v, rng.choice((50.0, 150.0)) if kind == "DC" else 22.0, kind,
                                plugs=rng.choice((1, 2, 4)), price_per_kwh=round(rng.uniform(0.3, 0.7), 3),
                                daily_sessions=rng.choice((0.0, 10.0, 40.0)), setup_s=rng.choice((60.0, 180.0)),
                                demand_profile=rng.choice(("highway", "urban"))))
    dep_h = round(rng.uniform(0.0, 24.0), 2)
    prefs = {"objective": "cost", "value_of_time_per_h": rng.choice((2.0, 4.0, 6.0, 15.0)),
             "charge_step_soc": rng.choice((0.1, 0.125, 0.2)), "queue_aware": rng.random() < 0.6,
             "time_dependent_waits": rng.random() < 0.6, "reserve_soc": rng.choice((0.0, 0.1)),
             "min_arrival_soc": rng.choice((0.0, 0.1))}
    net = b.build()
    fits = []  # vehicles that can drive the last leg on one charge
    for veh in ("city27", "compact50", "compact50", "sedan75"):
        sc = Scenario(f"tiniest-{seed}", net, stations, get_vehicle(veh), Query(o, d, departure_s=dep_h * H),
                      Conditions(), Preferences(**prefs))
        P = sc.prepare()
        last = max(P.edge_energy[0][e] for e in range(net.num_edges) if net.head[e] == d)
        if last + P.e_dest_min + P.e_min <= 0.9 * P.e_charge_cap:
            fits.append((veh, sc))
    veh, sc = rng.choice(fits or [(veh, sc)])
    # enough energy to reach A (a stop at the origin: almost empty); the peak around the arrival at A
    P = sc.prepare()
    oa = next(e for e in P.net.out_edges[o] if P.net.head[e] == a)
    need = (P.edge_energy[0][oa] + P.e_min) / P.capacity
    soc0 = 0.05 if origin else round(min(need + rng.uniform(0.02, 0.15), 0.95), 3)
    arr = dep_h + P.edge_time[0][oa] / H
    if rng.random() < 0.65:  # dear at the arrival, cheaper soon after
        start, end = arr - rng.uniform(0.0, 1.0), arr + rng.choice((0.1, 0.3, 0.6, 1.0, rng.uniform(0.05, 2.0)))
        pf = rng.choice((2.0, 2.5, 4.0))
    else:  # a cheap period begins soon after the arrival
        start = arr + rng.choice((0.1, 0.3, 0.6, rng.uniform(0.05, 1.5)))
        end = start + rng.choice((1.0, 4.0, 10.0))
        pf = rng.choice((0.3, 0.5))
    peaks = [(round(start % 24.0, 2), round(end % 24.0, 2))]
    if rng.random() < 0.3:
        peaks.append((round((start + 6.0) % 24.0, 2), round((start + 7.5) % 24.0, 2)))
    tariff = Tariff(high_soc=rng.choice((0.6, 0.7, 0.8)), high_soc_factor=rng.choice((1.0, 1.0, 1.5, 3.0)),
                    peak_hours=tuple(peaks), peak_factor=pf, peak_high_soc_factor=rng.choice((None, None, 1.0, 2.0)),
                    busy_utilisation=rng.choice((None, None, 0.3)))
    inst = cv.Instance(seed=seed, generator="tiniest", size=(len(b.x), len(stations)), vehicle=veh,
                       temperature_c=20.0, initial_soc=soc0, departure_s=dep_h * H,
                       prefs=tuple(sorted(prefs.items())), origin_station=origin)
    return inst, sc.with_query(initial_soc=soc0).with_(tariff=tariff)


# =============================================================================
# the tests: fast tier
# =============================================================================
FAST_INSTANCES = 24
TINY_FAST = 16
GRID_FAST = range(2, 10)  # about 0.1 s each; the slow tier runs seeds 0..59 (a few take ~20 s)


def _assert_clean(tally: Tally) -> None:
    assert tally.n_failed == 0, "\n" + tally.report()


@pytest.mark.parametrize("block", range(3))
def test_tariff_instances_fast(block):
    """T1-T7, T9 on random instances of the cross-validation generators with random tariffs."""
    tally = Tally()
    n = FAST_INSTANCES // 3
    for seed in range(block * n, (block + 1) * n):
        inst, sc = tariff_instance(seed)
        check_tariff_instance(inst, sc, FAST, tally)
    _assert_clean(tally)
    assert tally.checked["T1"] > 20 * n


@pytest.mark.parametrize("block", range(2))
def test_tariff_brute_force_tiny_digraphs_fast(block):
    """T8: BLS and the Pareto front against the enumeration with holds until price changes."""
    tally = Tally()
    n = TINY_FAST // 2
    for seed in range(block * n, (block + 1) * n):
        check_brute_force(*tiny_tariff_instance(seed), tally)
    _assert_clean(tally)
    assert tally.checked["T8"] >= n


def test_holds_on_a_five_minute_grid_never_beat_bls_fast():
    """T8 (completeness): holding until any 5-minute step of the next 24 h is never better than
    BLS, which holds until price changes only."""
    tally = Tally()
    for seed in GRID_FAST:
        check_brute_force(*tiniest_instance(seed), tally, grid_s=300.0)
    _assert_clean(tally)
    assert tally.checked["T8"] >= len(GRID_FAST) and tally.skipped["info: bls plans that hold"] >= 3


def test_tariff_sampler_is_diverse():
    tariffs = [tariff_instance(s)[1].tariff for s in range(80)]
    assert any(t.high_soc_factor != 1.0 for t in tariffs) and any(t.high_soc_factor == 1.0 for t in tariffs)
    assert any(any(a > b for a, b in t.peak_hours) for t in tariffs)  # wraps midnight
    assert any(t.peak_hours == ((0, 24),) for t in tariffs) and any(not t.peak_hours for t in tariffs)
    assert any(t.busy_utilisation is not None for t in tariffs)
    assert any(t.peak_high_soc_factor is not None for t in tariffs)
    assert {t.peak_factor < 1.0 for t in tariffs} == {True, False}
    objectives = [tariff_instance(s)[1].preferences.objective for s in range(40)]
    assert objectives.count("cost") > objectives.count("time") > 0


# =============================================================================
# slow tier
# =============================================================================
SLOW_BLOCKS = 20
SLOW_PER_BLOCK = 8  # 160 instances (about 8 s each)
TINY_SLOW = 240
GRID_SLOW = 60


@pytest.mark.slow
@pytest.mark.parametrize("block", range(SLOW_BLOCKS))
def test_tariff_instances_slow(block, request):
    cv._require_slow(request)
    tally = Tally()
    for seed in range(1_000 + block * SLOW_PER_BLOCK, 1_000 + (block + 1) * SLOW_PER_BLOCK):
        check_tariff_instance(*tariff_instance(seed, "slow"), SLOW, tally)
    _assert_clean(tally)


@pytest.mark.slow
@pytest.mark.parametrize("block", range(8))
def test_tariff_brute_force_tiny_digraphs_slow(block, request):
    cv._require_slow(request)
    tally = Tally()
    n = TINY_SLOW // 8
    for seed in range(1_000 + block * n, 1_000 + (block + 1) * n):
        check_brute_force(*tiny_tariff_instance(seed), tally)
    _assert_clean(tally)


@pytest.mark.slow
@pytest.mark.parametrize("block", range(4))
def test_holds_on_a_five_minute_grid_never_beat_bls_slow(block, request):
    cv._require_slow(request)
    tally = Tally()
    n = GRID_SLOW // 4
    for seed in range(block * n, (block + 1) * n):
        check_brute_force(*tiniest_instance(seed), tally, grid_s=300.0)
    _assert_clean(tally)


# =============================================================================
# bugs found by this suite (regression tests; see tests/fuzz_report.md)
# =============================================================================
def _join_line() -> Scenario:
    """O --60 km residential (9233.3 s)--> S (50 kW DC) --50 km--> D, peak 16:00-20:00 at three
    times the price, leaving at 04:09:43.581."""
    b = NetworkBuilder()
    o = b.add_node(0.0, 0.0, 100.0, name="O")
    s = b.add_node(60e3, 0.0, 100.0, name="S")
    d = b.add_node(110e3, 0.0, 100.0, name="D")
    b.add_road(o, s, road_class="residential")
    b.add_road(s, d, road_class="motorway")
    st = [Station(0, s, 50.0, "DC", price_per_kwh=0.5, setup_s=180.0, daily_sessions=0.0, name="S")]
    return Scenario("join line", b.build(), st, get_vehicle("compact50"),
                    Query(o, d, initial_soc=0.6, departure_s=14983.581), Conditions(),
                    Preferences(objective="cost", queue_aware=False, charge_step_soc=0.05),
                    tariff=Tariff(peak_hours=((16, 20),), peak_factor=3.0))


def test_bug_t1_a_plan_rebuilt_from_its_stops_joins_at_the_exact_hold_time():
    """Regression for BUG-T1 (fixed in plan.py / evaluate.py / robustness.py): a stop recorded
    only ``arrival_s`` and ``hold_s = hold_until - arrival``, and a plan rebuilt from its stops
    (``robustness`` for a plan without its decision; any reader of ``RoutePlan.to_dict``) held
    until ``arrival_s + hold_s``.  That sum can differ from the decided hold in the last bit --
    here 1 ulp before 16:00, the start of the peak -- and the replay was priced in the other
    period: 10.74 instead of 32.22.  The stop keeps the exact join time now (``hold_until_s``)."""
    P = _join_line().prepare()
    t = P.edge_time[0][0]
    hold = P.hold_candidates(0, t)[0]
    assert P.time_of_day(hold) == pytest.approx(16 * H)
    p = evaluate(P, PlanDecision([0, 2], [0, 0], [ChargeDecision(1, 0, 0.9 * P.capacity, hold)]))
    st = p.stops[0]
    assert p.feasible and st.peak and st.money == pytest.approx(3 * P.cf[0].money(st.energy_in_kwh, st.energy_out_kwh))
    # the float trap: arrival + (hold - arrival) is not the hold, and lies before the peak
    assert st.arrival_s + st.hold_s != hold and not P.is_peak(0, st.arrival_s + st.hold_s)
    assert st.hold_until_s == hold and st.join_s == hold
    # a plan without its decision is replayed from its stops ...
    r = replay_run(P, replace(p, decision=None), 0, trip_cv=0.0, edge_cv=0.0, wait_model="expected")
    assert r.stops[0].peak and r.money == p.money and r.objective_value == p.objective_value
    # ... and so is a decision rebuilt from the plan's JSON
    back = json.loads(json.dumps(p.to_dict()))
    assert back["stops"][0]["hold_until_s"] == hold
    q = evaluate(P, _rebuilt_from_json(back, P.e_charge_cap))
    assert q.money == p.money and q.objective_value == p.objective_value
    # without a hold the field is empty and the join time is the arrival
    a = evaluate(P, PlanDecision([0, 2], [0, 0], [ChargeDecision(1, 0, 0.9 * P.capacity)])).stops[0]
    assert a.hold_until_s is None and a.join_s == a.arrival_s and a.hold_s == 0.0
