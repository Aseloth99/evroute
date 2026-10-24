r"""Greedy "charge when needed" planner (``"greedy"``): the in-car navigator.

:class:`GreedyPlanner` imitates what a typical in-car navigation system
does and what many drivers do by hand.  It never plans ahead more than one
charging stop:

1. From the current node ``v`` with the current battery energy ``x`` look at
   the **fastest path to the destination**.  If the battery suffices (the
   reserve ``P.e_min`` holds at every node and the arrival requirement
   ``P.e_dest_min`` is met), drive it and stop.
2. Otherwise consider every **not yet used** charging station that can be
   reached from ``(v, x)`` without violating the reserve (the set comes from
   :func:`~evroute.algorithms.labels.fastest_feasible_tree`, which respects
   hills, regeneration and the speed levels) and pick the one that minimises

   .. math::  \tau(v \to s) + T_{lb}(s \to d),

   the fastest feasible driving time to the station plus the lower bound on
   the driving time from the station to the destination
   (:attr:`evroute.search.LowerBounds.time_lb`).  Every station on a fastest
   path to the destination has the same value (the detour is zero), so ties
   are the rule, not the exception; they are broken like a driver would:
   **the station closest to the destination wins** (drive as far as the
   battery allows), then the shorter expected stop (wait + setup + charge to
   the target).
3. Drive to the station along the fastest feasible path and charge to a
   fixed target SoC -- by default 80 % at DC chargers (the usual advice,
   because charging power falls steeply above it) and 100 % at AC
   chargers -- capped by ``P.e_charge_cap``.  Go back to step 1.

The target is raised to full (``P.e_charge_cap``) when the nominal one would
not allow to **make progress**, i.e. neither to finish nor to reach another
unused station; a station that cannot make progress even with a full charge
is skipped and the next best one is tried.  Because every iteration consumes
one station the loop always terminates; it fails with an infeasible plan if
no station is reachable, if no reachable station makes progress, or after
``max_stops`` stops.

Guarantees and limitations
==========================

* **Feasible by construction** whenever a plan is returned: every leg comes
  from a battery-feasible label search, the evaluator replays exactly the
  same arithmetic, and charging only ever raises the energy.  The evaluator
  nevertheless re-checks the result.
* **No optimality guarantee** (``exact = "heuristic"``).  The decision at
  each stop uses only the distance to the station and a lower bound for the
  rest, never the charging speed of the station or its queue, the SoC-
  dependent charging power or the energy the remaining trip really needs.
  Charging to 80 %/100 % regardless of the remaining distance wastes time on
  the flat part of the charging curve; picking the "closest to the goal"
  station systematically favours slow AC chargers on the way.  These are the
  typical behaviours of the heuristic being imitated and the reason why the
  exact planners beat it.
* May fail (infeasible plan) on instances for which a feasible route exists,
  e.g. when the station nearest to the goal is out of reach of the best
  stop-over.
* ``internal_objective`` is ``None``: the heuristic has no model of the
  objective.  With the cost objective the plan is still the time-greedy one.

Options
=======

``charge_to_soc``
    ``None`` (default: 0.8 for ``"DC"``, 1.0 for ``"AC"`` stations), a float
    used for every station, or a mapping ``{"DC": 0.8, "AC": 1.0}``;
    fractions of the usable capacity in ``(0, 1]``.
``max_stops``
    maximum number of charging stops (default 20).
``final_leg``
    ``"fastest"`` (default) -- step 1 uses the battery-unaware fastest path,
    exactly like a navigator that only checks whether its route is drivable;
    ``"feasible"`` -- step 1 uses the fastest path the battery can drive
    (found by the label search), which may be a slower detour but avoids
    needless stops.

References
==========

The problem setting (a battery-constrained route with charging stops) is
the one of S. Storandt, S. Funke (2012), *Cruising with a battery-powered
vehicle and not getting stranded*, Proc. AAAI 2012, and of M. Baum, J. Dibbelt,
A. Gemsa, D. Wagner, T. Zuendorf (2019), *Shortest feasible paths with
charging stops for battery electric vehicles*, Transportation Science 53(6),
1627-1655, where the exact methods are described.  The heuristic itself is
folklore (the strategy of a driver or a simple navigator) and is *not* a
published algorithm; it is included as the "what people do today" baseline.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..evaluate import evaluate, infeasible_plan
from ..plan import ChargeDecision, PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario
from ..search import dijkstra, min_level_weights
from .base import Planner
from .labels import fastest_feasible_tree
from .naive import (
    EPS,
    dijkstra_traced,
    drive_path,
    fastest_levels,
    time_weights,
    trace_label_tree,
)

INF = math.inf

#: Default charging targets by station kind [fraction of usable capacity].
DEFAULT_CHARGE_TO_SOC: dict[str, float] = {"DC": 0.8, "AC": 1.0}
#: Valid values of the ``final_leg`` option.
FINAL_LEGS = ("fastest", "feasible")

__all__ = ["DEFAULT_CHARGE_TO_SOC", "FINAL_LEGS", "GreedyPlanner", "GreedyRun", "run_greedy", "target_energy"]


@dataclass
class GreedyRun:
    """Outcome of :func:`run_greedy` (a decision or the reason for failure)."""

    ok: bool
    message: str = ""
    edges: list[int] = field(default_factory=list)
    levels: list[int] = field(default_factory=list)
    charges: list[ChargeDecision] = field(default_factory=list)
    #: ``(station, target_kwh)`` of the stops in driving order.
    stops: list[tuple[int, float]] = field(default_factory=list)
    #: Nodes settled by the underlying searches (tree sizes + Dijkstra).
    settled: int = 0
    #: Label-setting trees computed.
    trees: int = 0
    #: Candidate stations examined for progress.
    candidates: int = 0


def target_energy(P: PreparedScenario, station: int, charge_to_soc: float | Mapping[str, float] | None) -> float:
    """Nominal charging target [kWh] at ``station`` for the ``charge_to_soc`` option."""
    kind = P.stations[station].kind
    if charge_to_soc is None:
        frac = DEFAULT_CHARGE_TO_SOC[kind]
    elif isinstance(charge_to_soc, Mapping):
        frac = charge_to_soc.get(kind, DEFAULT_CHARGE_TO_SOC[kind])
    else:
        frac = charge_to_soc
    if not 0.0 < frac <= 1.0:
        raise ValueError(f"charge_to_soc must lie in (0, 1], got {frac!r}")
    return min(frac * P.capacity, P.e_charge_cap)


def run_greedy(
    P: PreparedScenario,
    *,
    charge_to_soc: float | Mapping[str, float] | None = None,
    max_stops: int = 20,
    final_leg: str = "fastest",
    time_lb: list[float] | None = None,
    trace: SearchTrace | None = None,
) -> GreedyRun:
    """Run the greedy heuristic (see the module documentation).

    ``time_lb`` is the lower bound on the driving time to the destination
    for every node (default: reverse Dijkstra on the fastest speed levels,
    i.e. :attr:`evroute.search.LowerBounds.time_lb`).  The genetic planner
    calls this function to seed its population.
    """
    if final_leg not in FINAL_LEGS:
        raise ValueError(f"unknown final_leg {final_leg!r}; choose from {FINAL_LEGS}")
    if max_stops < 0:
        raise ValueError("max_stops must be non-negative")
    net = P.net
    dest = P.destination
    cap_e = P.e_charge_cap
    if time_lb is None:
        time_lb, _ = dijkstra(net, dest, min_level_weights(P, "time"), reverse=True)
    w_fast = time_weights(P)
    lv_fast = fastest_levels(P)
    need = {dest: P.e_dest_min}
    fast_cache: dict[int, tuple[list[int], list[int]] | None] = {}
    run = GreedyRun(ok=False)

    def fail(msg: str) -> GreedyRun:
        run.ok = False
        run.message = msg
        return run

    def make_tree(v: int, x: float, t: float) -> dict[int, tuple]:
        tree = fastest_feasible_tree(P, v, x, t_start=t, min_arrival_energy=need)
        run.trees += 1
        run.settled += len(tree)
        trace_label_tree(trace, tree)
        return tree

    def fastest_from(v: int, t: float) -> tuple[list[int], list[int]] | None:
        """Battery-unaware fastest path ``v -> destination`` (cached per node)."""
        if v in fast_cache:
            return fast_cache[v]
        dist, pred, st = dijkstra_traced(net, v, w_fast, target=dest, trace=trace, key_offset=t)
        run.settled += st
        res: tuple[list[int], list[int]] | None = None
        if dist[dest] < INF:
            path: list[int] = []
            u = dest
            while u != v:
                e = pred[u]
                path.append(e)
                u = net.tail[e]
            path.reverse()
            res = (path, [lv_fast[e] for e in path])
        fast_cache[v] = res
        return res

    def finishing_leg(
        v: int, x: float, tree: dict[int, tuple] | None, t: float
    ) -> tuple[list[int], list[int]] | None:
        """The leg to the destination if the battery allows it, else ``None``.

        ``tree`` (the feasible tree from ``(v, x)``) is only consulted for
        ``final_leg="feasible"``; the ``"fastest"`` rule needs no label search.
        """
        if final_leg == "feasible":
            assert tree is not None
            rec = tree.get(dest)
            return None if rec is None else (list(rec[2]), list(rec[3]))
        path = fastest_from(v, t)
        if path is None or not drive_path(P, path[0], path[1], x)[0]:
            return None
        return path

    def makes_progress(
        v: int, y: float, s: int, t: float, used: set[int]
    ) -> tuple[bool, dict[int, tuple] | None]:
        """After charging to ``y`` at ``s`` (node ``v``): finish or reach another station?

        Returns the verdict and the feasible tree from ``(v, y)`` (``None`` if
        it was not needed to decide).
        """
        if final_leg == "fastest":
            if finishing_leg(v, y, None, t) is not None:
                return True, None
            tree2 = make_tree(v, y, t)
        else:
            tree2 = make_tree(v, y, t)
            if finishing_leg(v, y, tree2, t) is not None:
                return True, tree2
        for s2, st2 in enumerate(P.stations):
            if s2 == s or s2 in used or st2.node == v:
                continue
            if st2.node in tree2 and time_lb[st2.node] < INF:
                return True, tree2
        return False, tree2

    node, energy, t_now = P.origin, P.e_init, 0.0
    edges: list[int] = []
    levels: list[int] = []
    used: set[int] = set()
    exclude_node = -1
    # the feasible tree of the current state; computed lazily ("fastest" needs it
    # only when the fastest path is not drivable)
    tree: dict[int, tuple] | None = make_tree(node, energy, t_now) if final_leg == "feasible" else None

    while True:
        fin = finishing_leg(node, energy, tree, t_now)
        if fin is not None:
            edges.extend(fin[0])
            levels.extend(fin[1])
            run.ok = True
            run.edges, run.levels = edges, levels
            return run
        if len(run.stops) >= max_stops:
            return fail(f"destination not reached after the maximum of {max_stops} charging stop(s)")
        if tree is None:
            tree = make_tree(node, energy, t_now)

        # ------------------------------------------------ candidate stations
        cands: list[tuple[float, float, float, int, float, float, list[float], tuple]] = []
        for s, st in enumerate(P.stations):
            if s in used or st.node == exclude_node:
                continue
            rec = tree.get(st.node)
            lbv = time_lb[st.node]
            if rec is None or lbv == INF:
                continue
            t_arr, e_arr = rec[0], rec[1]
            y0 = target_energy(P, s, charge_to_soc)
            ladder = [y for y in (y0, cap_e) if y > e_arr + EPS]
            if len(ladder) == 2 and ladder[1] - ladder[0] <= EPS:
                ladder.pop()
            if not ladder:  # the battery cannot take more: the stop is useless
                continue
            # keys are rounded to 1 microsecond: stations on a common fastest path
            # tie up to float noise and are then ordered by the tie-break rules
            key = round((t_arr - t_now) + lbv, 6)
            stop_est = P.wait_s(s, t_arr) + P.setup_s[s] + P.cf[s].charge_time(e_arr, ladder[0])
            cands.append((key, lbv, stop_est, s, t_arr, e_arr, ladder, rec))
        if not cands:
            return fail(
                f"no unused charging station can be reached from node {node} with {energy:.1f} kWh "
                f"(reserve {P.e_min:.1f} kWh) after {len(run.stops)} stop(s)"
            )
        cands.sort(key=lambda c: c[:4])

        # ------------------------------------- best station that makes progress
        accepted = False
        for _key, _lb, _est, s, t_arr, e_arr, ladder, rec in cands:
            run.candidates += 1
            v = P.stations[s].node
            f = P.cf[s]
            for y in ladder:
                # same arithmetic as the evaluator: t += w + su + ch
                t_dep = t_arr + (P.wait_s(s, t_arr) + P.setup_s[s] + f.charge_time(e_arr, y))
                ok, tree2 = makes_progress(v, y, s, t_dep, used)
                if ok:
                    edges.extend(rec[2])
                    levels.extend(rec[3])
                    run.charges.append(ChargeDecision(len(edges), s, y))
                    run.stops.append((s, y))
                    used.add(s)
                    node, energy, t_now, exclude_node, tree = v, y, t_dep, v, tree2
                    accepted = True
                    break
            if accepted:
                break
        if not accepted:
            return fail(
                f"none of the {len(cands)} reachable charging station(s) leads closer to the destination "
                f"even with a full charge (stuck at node {node} after {len(run.stops)} stop(s))"
            )


class GreedyPlanner(Planner):
    """Greedy "charge when needed" heuristic; see the module documentation.

    Options: ``charge_to_soc`` (``None`` | float | ``{"DC": f, "AC": f}``),
    ``max_stops`` (20) and ``final_leg`` (``"fastest"`` | ``"feasible"``).
    ``stats.settled`` counts the nodes of all label-setting trees and
    Dijkstra searches, ``stats.generated`` the candidate stations examined and
    ``stats.extra`` the number of trees and stops.
    """

    name = "greedy"
    title = "Greedy (charge when needed)"
    exact = "heuristic"
    description = (
        "Drive the fastest path; when the battery does not suffice, detour to the "
        "reachable station minimising time-to-station + lower bound to the goal, "
        "charge to a fixed SoC (80 % DC / 100 % AC), repeat. Mimics an in-car navigator."
    )

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        opts = self.options
        charge_to_soc = opts.get("charge_to_soc")
        max_stops = int(opts.get("max_stops", 20))
        final_leg = opts.get("final_leg", "fastest")
        tr = SearchTrace() if trace else None
        run = run_greedy(P, charge_to_soc=charge_to_soc, max_stops=max_stops, final_leg=final_leg, trace=tr)
        stats = SearchStats(
            algorithm=self.name,
            settled=run.settled,
            generated=run.candidates,
            extra={
                "trees": run.trees,
                "stops": len(run.stops),
                "final_leg": final_leg,
                "max_stops": max_stops,
            },
        )
        if not run.ok:
            return infeasible_plan(P, self.name, run.message, stats, tr)
        decision = PlanDecision(run.edges, run.levels, run.charges)
        return evaluate(P, decision, algorithm=self.name, internal_objective=None, stats=stats, trace=tr)
