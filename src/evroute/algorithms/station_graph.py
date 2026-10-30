r"""Station graph: two-level planning with a fixed charging target (``"station-graph"``).

The classic way to keep battery-constrained routing simple is to reduce the
road network to a small *meta graph* whose nodes are the interesting places --
here the origin, every charging station and the destination -- and whose
edges are battery-feasible stretches of road between them.  A shortest path on
the meta graph is then a sequence of stations, each of which is left with a
*fixed* battery level.  This is the "station graph" family of approaches in the
spirit of S. Storandt, S. Funke (2012), *Cruising with a battery-powered
vehicle and not getting stranded*, Proc. AAAI 2012; it is cheap and simple, but
the fixed target is exactly what the exact planners improve upon.

Model
=====

Let ``T`` be the **charging target** (option ``charge_to_soc``, default 1.0,
capped at ``P.e_charge_cap``).  *Every* stop charges to ``T``, so the vehicle
always departs from a station with exactly ``T`` kWh; it leaves the origin with
``P.e_init``.

*Meta nodes*: the origin ``o``, the destination ``d`` and every station ``j``
the vehicle can plug into (``P.station_usable``) inside the detour ellipse
(below).  *Meta edges* ``a -> b``: the **fastest
battery-feasible path** without charging from the node of ``a`` (departing with
``P.e_init`` or ``T``) to the node of ``b`` -- computed by one run of
:func:`~evroute.algorithms.labels.fastest_feasible_tree` per meta node, which
respects hills, regeneration, speed levels and the reserve at every node.  The
path also fixes the arrival energy ``x_ab`` at ``b``.  An edge into the
destination needs ``x_ab >= P.e_dest_min``; an edge into a station at the
destination does not (charging there is how the requirement is met).

*Weights are not static.*  The cost of arriving at station ``b`` depends on the
arrival time (queueing delay ``W_b(t)``) and on the arrival energy
(``F_b(T) - F_b(x_ab)`` seconds of charging).  The meta search therefore runs
Dijkstra with **time labels**: the label of a station is the *departure time*
after its stop, relaxation along ``a -> b`` gives

.. math::

    t_b^{dep} = t_a^{dep} + \tau_{ab} + \big(W_b(t_a^{dep} + \tau_{ab}) +
                \sigma_b + F_b(T) - F_b(x_{ab})\big),

(``F`` is the exact piecewise-linear charging function, ``sigma`` the setup
time) and the label of ``d`` is the arrival time.  Since a vehicle always
leaves a station with the same energy ``T``, the departure time is a complete
state, and because expected waits satisfy the FIFO property ``t + W(t)``
non-decreasing (guaranteed by :class:`~evroute.queueing.WaitProfile`), the
departure time is non-decreasing in the arrival time -- the precondition for
Dijkstra on time-dependent graphs (Orda & Rom 1990).  The wait is
therefore evaluated at the **real arrival time**, also with ``P.time_dependent``.
Trees are computed lazily, only for the meta nodes Dijkstra actually settles.

Guarantees and limitations
==========================

* The meta-Dijkstra value is the **exact trip time of the plan it returns**:
  the evaluator replays the same paths, energies and stops, so
  ``internal_objective`` equals the evaluated time up to floating-point
  rounding (``< 1e-9`` s) for the time objective.  For the cost objective the
  planner still minimises time, so ``internal_objective`` is ``None`` (the
  evaluator then measures the generalised cost of the time-optimal meta path).
* The plan is the **optimum of the meta graph**, i.e. optimal among the plans
  that (1) stop only at stations in the ellipse, (2) charge every time to
  ``T``, and (3) drive each stretch on its single *fastest feasible* path.  It
  can be arbitrarily worse than the true optimum:

  - a fixed target wastes time charging in the slow top range of the
    charging curve (or too little for a long next stretch);
  - a slower stretch that arrives with more energy (and so charges less) is
    ignored: only the earliest-arrival label of each node becomes an edge;
  - stations outside the ellipse are never considered.

* ``exact = "heuristic"``.  A plan may be missing although a feasible route
  exists (e.g. when ``T`` is too low to bridge a gap); with ``widen=True`` the
  ellipse is enlarged (factor ``x1.5``, ``x2.25`` and finally all stations)
  before giving up, which cures the infeasibility caused by the ellipse.

Options
=======

``charge_to_soc``
    charging target as a fraction of the usable capacity (default 1.0).
``detour_factor``
    ellipse: only stations ``s`` with ``|os| + |sd| <= detour_factor * |od|``
    (Euclidean distances) are meta nodes (default 1.6).  Stations at the
    origin or destination are always inside.
``widen``
    retry with larger ellipses when no path exists (default ``True``).

References
==========

* S. Storandt, S. Funke (2012). Cruising with a battery-powered vehicle and
  not getting stranded. *Proc. AAAI 2012*.
* A. Orda, R. Rom (1990). Shortest-path and minimum-delay algorithms in
  networks with time-dependent edge-length. *Journal of the ACM* 37(3),
  607-625.
"""

from __future__ import annotations

import heapq
import math

from ..evaluate import evaluate, infeasible_plan
from ..plan import ChargeDecision, PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario
from .base import Planner
from .labels import fastest_feasible_tree
from .naive import dijkstra_traced, time_weights, trace_label_tree

INF = math.inf
#: Meta node ids: stations use their (non-negative) index.
_ORIGIN = -1
_DEST = -2
#: Growth of the detour factor when the meta graph has no path.
_WIDEN = 1.5

__all__ = ["StationGraphPlanner", "detour_candidates"]


def detour_candidates(P: PreparedScenario, factor: float) -> list[int]:
    """Usable stations inside the detour ellipse of the origin-destination pair.

    ``s`` qualifies iff the vehicle can plug in there (``P.station_usable``)
    and ``|o s| + |s d| <= factor * |o d|`` (Euclidean distances in projected
    metres).  With ``factor = inf``, or when origin and destination (almost)
    coincide and the ellipse degenerates, every usable station qualifies.
    """
    net = P.net
    xs, ys = net.x, net.y
    o, d = P.origin, P.destination
    usable = P.station_usable
    d_od = math.hypot(xs[d] - xs[o], ys[d] - ys[o])
    if factor == INF or d_od < 1.0:
        return [s for s in range(len(P.stations)) if usable[s]]
    limit = factor * d_od + 1e-6
    out = []
    for s, st in enumerate(P.stations):
        v = st.node
        if not usable[s]:
            continue
        if math.hypot(xs[v] - xs[o], ys[v] - ys[o]) + math.hypot(xs[v] - xs[d], ys[v] - ys[d]) <= limit:
            out.append(s)
    return out


class _MetaSearch:
    """Lazy meta graph (feasible-path trees cached per ``(node, energy)``) + Dijkstra."""

    def __init__(self, P: PreparedScenario, target_kwh: float, trace: SearchTrace | None) -> None:
        self.P = P
        self.T = target_kwh
        self.trace = trace
        self.need = {P.destination: P.e_dest_min}
        #: driving-time lower bound to the destination (the A* potential)
        self.h, _, _ = dijkstra_traced(P.net, P.destination, time_weights(P), reverse=True)
        self.trees: dict[tuple[int, float], dict[int, tuple]] = {}
        # A tree holds the destination only if it is reached with the arrival
        # requirement; a station there may be reached with less (that is why it
        # is used), so its arrival comes from a tree without the requirement.
        # (Driving keeps the reserve, so with a requirement equal to the reserve
        # only the start of a round trip can fall short.)
        self.dest_station = P.is_station_node[P.destination] and (
            P.e_dest_min > P.e_min or P.origin == P.destination
        )
        self.free_dest: dict[tuple[int, float], tuple | None] = {}
        self.tree_nodes = 0
        # counters of the Dijkstra runs
        self.settled = 0
        self.generated = 0
        self.pushes = 0
        self.peak_queue = 0

    def tree(self, node: int, energy: float, t_offset: float) -> dict[int, tuple]:
        """Fastest feasible tree from ``node`` with ``energy`` (trip time relative to the start)."""
        key = (node, energy)
        tree = self.trees.get(key)
        if tree is None:
            tree = fastest_feasible_tree(
                self.P, node, energy, t_start=0.0, min_arrival_energy=self.need, fastest_level_only=True
            )
            self.trees[key] = tree
            self.tree_nodes += len(tree)
            trace_label_tree(self.trace, tree, offset=t_offset)
        return tree

    def station_record(
        self, tree: dict[int, tuple], v: int, node: int, energy: float, t_offset: float
    ) -> tuple | None:
        """Arrival record of the station node ``v`` in ``tree``, the tree from ``(node, energy)``."""
        rec = tree.get(v)
        if rec is None and v == self.P.destination and self.dest_station:
            key = (node, energy)
            if key not in self.free_dest:
                free = fastest_feasible_tree(self.P, node, energy, t_start=0.0, fastest_level_only=True)
                self.tree_nodes += len(free)
                trace_label_tree(self.trace, free, offset=t_offset)
                self.free_dest[key] = free.get(v)
            rec = self.free_dest[key]
        return rec

    def shortest(self, cand: list[int]):
        """A* (Dijkstra with a consistent potential) on the meta graph over ``cand``.

        Returns ``(arrival_time, edges, levels, charges)`` of the best plan or
        ``None`` if the destination is not reachable on the meta graph.
        """
        P = self.P
        stations = P.stations
        T = self.T
        dest = P.destination
        wait, setup, cf = P.wait_s, P.setup_s, P.cf
        h = self.h
        dist: dict[int, float] = {_ORIGIN: 0.0}
        parent: dict[int, tuple[int, tuple]] = {}
        done: set[int] = set()
        heap: list[tuple[float, float, int]] = [(h[P.origin], 0.0, _ORIGIN)]
        heappush, heappop = heapq.heappush, heapq.heappop
        self.pushes += 1
        while heap:
            if len(heap) > self.peak_queue:
                self.peak_queue = len(heap)
            _, d, u = heappop(heap)
            if u in done or d > dist[u]:
                continue
            done.add(u)
            self.settled += 1
            if u == _DEST:
                break
            if u == _ORIGIN:
                node_u, e_u = P.origin, P.e_init
            else:
                node_u, e_u = stations[u].node, T
            tree = self.tree(node_u, e_u, d)

            rec = tree.get(dest)
            if rec is not None:
                arr = d + rec[0]
                self.generated += 1
                if arr < dist.get(_DEST, INF):
                    dist[_DEST] = arr
                    parent[_DEST] = (u, rec)
                    heappush(heap, (arr, arr, _DEST))
                    self.pushes += 1
            for s in cand:
                if s == u:
                    continue
                v = stations[s].node
                if u != _ORIGIN and v == node_u:  # a second stop at the same node is never useful
                    continue
                rec = self.station_record(tree, v, node_u, e_u, d)
                if rec is None or h[v] == INF:
                    continue
                arr = d + rec[0]
                # same arithmetic as the evaluator: t += w + su + ch
                dep = arr + (wait(s, arr) + setup[s] + cf[s].charge_time(rec[1], T))
                self.generated += 1
                if dep < dist.get(s, INF):
                    dist[s] = dep
                    parent[s] = (u, rec)
                    heappush(heap, (dep + h[v], dep, s))
                    self.pushes += 1
        if _DEST not in done:
            return None
        chain: list[tuple[int, tuple]] = []
        u = _DEST
        while u != _ORIGIN:
            prev, rec = parent[u]
            chain.append((u, rec))
            u = prev
        chain.reverse()
        edges: list[int] = []
        levels: list[int] = []
        charges: list[ChargeDecision] = []
        for u, rec in chain:
            edges.extend(rec[2])
            levels.extend(rec[3])
            if u >= 0:
                charges.append(ChargeDecision(len(edges), u, T))
        return dist[_DEST], edges, levels, charges


class StationGraphPlanner(Planner):
    """Dijkstra on the origin-stations-destination meta graph with a fixed charging target.

    See the module documentation for the model, the exactness of the
    internal objective and the limitations; options are ``charge_to_soc``
    (1.0), ``detour_factor`` (1.6) and ``widen`` (``True``).

    ``stats.settled`` counts the expanded meta nodes, ``stats.generated`` the
    meta-edge relaxations; ``stats.extra`` reports the number of
    feasible-path trees (one label-setting run each), the nodes they
    contain, the target and the detour factor that finally produced the plan.
    """

    name = "station-graph"
    OPTIONS = ("charge_to_soc", "detour_factor", "widen")
    title = "Station graph (fixed charge target)"
    exact = "heuristic"
    description = (
        "Two-level search: Dijkstra on a meta graph of origin, stations and destination "
        "whose edges are fastest battery-feasible paths; every stop charges to a fixed "
        "target SoC. Waits are evaluated at the real arrival time."
    )

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        opts = self.options
        frac = opts.get("charge_to_soc")
        frac = 1.0 if frac is None else float(frac)
        if not 0.0 < frac <= 1.0:
            raise ValueError(f"charge_to_soc must lie in (0, 1], got {frac!r}")
        detour = float(opts.get("detour_factor", 1.6))
        if detour < 1.0:
            raise ValueError("detour_factor must be >= 1")
        widen = bool(opts.get("widen", True))
        target = min(frac * P.capacity, P.e_charge_cap)
        tr = SearchTrace() if trace else None

        meta = _MetaSearch(P, target, tr)
        factors = [detour]
        if widen:
            f = detour
            for _ in range(2):
                f *= _WIDEN
                factors.append(f)
            factors.append(INF)
        result = None
        used_factor = detour
        attempts = 0
        n_cand = 0
        for used_factor in factors:
            attempts += 1
            cand = detour_candidates(P, used_factor)
            n_cand = len(cand)
            result = meta.shortest(cand)
            if result is not None:
                break
            if n_cand == sum(P.station_usable):  # nothing left to widen
                break
        stats = SearchStats(
            algorithm=self.name,
            settled=meta.settled,
            generated=meta.generated,
            pushes=meta.pushes,
            peak_queue=meta.peak_queue,
            extra={
                "trees": len(meta.trees),
                "tree_nodes": meta.tree_nodes,
                "stations_in_ellipse": n_cand,
                "detour_factor": None if used_factor == INF else used_factor,
                "attempts": attempts,
                "target_kwh": target,
                "target_soc": target / P.capacity,
            },
        )
        if result is None:
            msg = (
                f"the meta graph over {n_cand} station(s) (of {P.stations_note()}) has no battery-feasible "
                f"origin-destination path when every stop charges to {100.0 * target / P.capacity:.0f} % "
                f"(start {P.e_init:.1f} kWh, reserve {P.e_min:.1f} kWh, arrival requirement {P.e_dest_min:.1f} kWh)"
            )
            return infeasible_plan(P, self.name, msg, stats, tr)
        t_total, edges, levels, charges = result
        return evaluate(
            P,
            PlanDecision(edges, levels, charges),
            algorithm=self.name,
            internal_objective=t_total if P.objective == "time" else None,
            stats=stats,
            trace=tr,
        )
