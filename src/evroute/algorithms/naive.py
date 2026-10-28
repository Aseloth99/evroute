r"""Naive fastest path: the battery-unaware baseline (``"naive"``).

:class:`NaivePlanner` is what a conventional, non-electric navigator does:
it computes the time-fastest route from the origin to the destination, at
the fastest speed level of every edge, and plans **no charging stop at all**.
The battery plays no role in the search.

Why include it?  It is the reference point that shows *why* electric-vehicle
routing is a different problem.  On any trip that is longer than the range
the plan is infeasible, and the independent evaluator
(:func:`evroute.evaluate.evaluate`) replays it from the origin and reports
where the battery runs out (``RoutePlan.stranded_at_m``) and which reserve
constraints are violated.  The plan is deliberately returned as measured
instead of being turned into :func:`~evroute.evaluate.infeasible_plan`, so
the viewer can draw the route and mark the point where the car would be
stranded.  When the battery does suffice, the plan is feasible and simply the
fastest route (then it is optimal for the time objective).

Algorithm
=========

Dijkstra (1959) with a binary heap on the road network, weight

``metric="time"`` (default)
    the travel time of the *fastest* non-dominated speed level of the edge,
    ``P.edge_options[e][0]`` (the options are sorted by time);
``metric="distance"``
    the edge length in metres -- the "shortest path" navigators offer next to
    the "fastest path" -- driven at the fastest speed level.

Guarantees: the returned path minimises the chosen metric among *all*
origin-destination paths (no battery constraint).  There is no guarantee
whatsoever about the energy: ``exact = "heuristic"`` states that honestly.
``internal_objective`` is ``None`` because the planner has no model of the
objective the evaluator measures (the plan carries no charging stops and the
cost objective also contains drive wear).

Shared helpers
==============

The module also hosts the small primitives the other baselines
(:mod:`~evroute.algorithms.greedy`, :mod:`~evroute.algorithms.station_graph`,
:mod:`~evroute.algorithms.genetic`) need:

* :func:`dijkstra_traced` -- Dijkstra that can feed a
  :class:`~evroute.plan.SearchTrace` and stop early;
* :func:`fastest_levels`, :func:`time_weights` -- the battery-unaware
  fastest speed level and travel time per edge;
* :func:`drive_path` -- replay of a fixed path on the battery (the same
  arithmetic as the evaluator);
* :func:`trace_label_tree` -- feed the result of
  :func:`~evroute.algorithms.labels.fastest_feasible_tree` to a trace.

References
==========

* E. W. Dijkstra (1959). A note on two problems in connexion with graphs.
  *Numerische Mathematik* 1, 269-271.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Collection, Mapping, Sequence

from ..evaluate import evaluate, infeasible_plan
from ..plan import PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario
from .base import Planner

INF = math.inf
#: Energy tolerance of the baselines [kWh]; stricter than the evaluator's 1e-6.
EPS = 1e-9

#: Valid values of the ``metric`` option.
METRICS = ("time", "distance")

__all__ = [
    "METRICS",
    "NaivePlanner",
    "dijkstra_traced",
    "drive_path",
    "fastest_levels",
    "time_weights",
    "trace_label_tree",
]


# ======================================================================
# shared helpers
# ======================================================================
def fastest_levels(P: PreparedScenario) -> list[int]:
    """Speed level with the smallest travel time on every edge.

    ``P.edge_options[e]`` is sorted by travel time, so this is the level of
    its first entry (``0`` when the scenario has a single speed level).
    """
    return [opts[0][0] for opts in P.edge_options]


def time_weights(P: PreparedScenario) -> list[float]:
    """Smallest travel time [s] of every edge over its speed levels."""
    return [opts[0][1] for opts in P.edge_options]


def dijkstra_traced(
    net,
    source: int,
    weight: Sequence[float],
    *,
    target: int | None = None,
    targets: Collection[int] | None = None,
    reverse: bool = False,
    trace: SearchTrace | None = None,
    key_offset: float = 0.0,
) -> tuple[list[float], list[int], int]:
    """Dijkstra with early stopping and optional search trace.

    Returns ``(dist, pred, settled)``: distances and predecessor edges from
    ``source`` (to it, with ``reverse=True``) and the number of settled
    nodes.  The search stops as soon as ``target`` is settled, or as soon as
    every node in ``targets`` is settled; entries of nodes that were not
    settled yet are tentative upper bounds then.  With ``trace`` each settled
    node is recorded with the key ``key_offset + distance``.
    """
    n = net.num_nodes
    dist = [INF] * n
    pred = [-1] * n
    dist[source] = 0.0
    adj = net.in_edges if reverse else net.out_edges
    other = net.tail if reverse else net.head
    remaining = set(targets) if targets is not None else None
    heap = [(0.0, source)]
    heappush, heappop = heapq.heappush, heapq.heappop
    visit = trace.visit if trace is not None else None
    settled = 0
    while heap:
        d, u = heappop(heap)
        if d > dist[u]:
            continue
        settled += 1
        if visit is not None:
            visit(u, key_offset + d)
        if u == target:
            break
        if remaining is not None:
            remaining.discard(u)
            if not remaining:
                break
        for e in adj[u]:
            w = other[e]
            nd = d + weight[e]
            if nd < dist[w]:
                dist[w] = nd
                pred[w] = e
                heappush(heap, (nd, w))
    return dist, pred, settled


def drive_path(
    P: PreparedScenario,
    edges: Sequence[int],
    levels: Sequence[int],
    e_start: float,
    *,
    e_final_min: float | None = None,
) -> tuple[bool, float]:
    """Replay a fixed path on the battery; returns ``(feasible, final_energy)``.

    Uses exactly the arithmetic of :func:`evroute.evaluate.evaluate` (energy
    ``min(x - c, capacity)`` per edge).  The path is feasible if the energy is
    at least the reserve ``P.e_min`` when leaving every node and on arrival at
    every node (up to ``1e-9`` kWh) and, at the end, at least ``e_final_min``
    (default: the destination requirement ``P.e_dest_min``).  An empty path is
    feasible iff ``e_start >= e_final_min``.
    """
    need = P.e_dest_min if e_final_min is None else e_final_min
    e_min = P.e_min - EPS
    cap = P.capacity
    energy = P.edge_energy
    x = e_start
    for e, k in zip(edges, levels):
        if x < e_min:
            return False, x
        x = x - energy[k][e]
        if x > cap:
            x = cap
        if x < e_min:
            return False, x
    return x >= need - EPS, x


def trace_label_tree(trace: SearchTrace | None, tree: Mapping[int, tuple], offset: float = 0.0) -> None:
    """Record the nodes of a :func:`fastest_feasible_tree` result in a trace.

    Nodes are visited in order of earliest arrival (the order in which the
    underlying label setting settled them for the first time); the key is
    ``offset + arrival time`` (the tree already contains its ``t_start``).
    """
    if trace is None:
        return
    arrival = getattr(tree, "arrival", None)  # a FeasibleTree: times without building the routes
    if arrival is not None:
        times = [(v, arrival(v)[0]) for v in tree]
    else:
        times = [(v, rec[0]) for v, rec in tree.items()]
    for v, t in sorted(times, key=lambda kv: (kv[1], kv[0])):
        trace.visit(v, offset + t)


# ======================================================================
# planner
# ======================================================================
class NaivePlanner(Planner):
    """Battery-unaware fastest (or shortest) path with no charging stop.

    Options
    -------
    ``metric``
        ``"time"`` (default) -- minimise travel time at the fastest speed
        level; ``"distance"`` -- minimise length, driven at the fastest
        level.

    The returned plan is *not* repaired: if the battery does not last, the
    evaluated plan is infeasible and ``stranded_at_m`` tells where the car
    stops (see the module documentation).  An infeasible plan object with an
    empty route is returned only if the destination cannot be reached in the
    road network at all.
    """

    name = "naive"
    OPTIONS = ("metric",)
    title = "Naive fastest path"
    exact = "heuristic"
    description = (
        "Battery-unaware fastest path without any charging stop (what a conventional "
        "navigator computes). Usually infeasible on long trips; the evaluator reports "
        "where the battery runs out."
    )

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        metric = self.options.get("metric", "time")
        if metric not in METRICS:
            raise ValueError(f"unknown metric {metric!r}; choose from {METRICS}")
        tr = SearchTrace() if trace else None
        net = P.net
        weight = time_weights(P) if metric == "time" else net.length
        dist, pred, settled = dijkstra_traced(
            net, P.origin, weight, target=P.destination, trace=tr
        )
        stats = SearchStats(
            algorithm=self.name,
            settled=settled,
            extra={"metric": metric, "path_weight": dist[P.destination] if dist[P.destination] < INF else None},
        )
        if dist[P.destination] == INF:
            return infeasible_plan(
                P, self.name, f"destination {P.destination} is not reachable from origin {P.origin}", stats, tr
            )
        edges: list[int] = []
        v = P.destination
        while v != P.origin:
            e = pred[v]
            edges.append(e)
            v = net.tail[e]
        edges.reverse()
        lv = fastest_levels(P)
        decision = PlanDecision(edges, [lv[e] for e in edges], [])
        return evaluate(
            P,
            decision,
            algorithm=self.name,
            internal_objective=None,
            stats=stats,
            trace=tr,
        )
