"""Shared graph-search primitives and admissible lower bounds.

* :func:`dijkstra` -- single-source shortest paths for non-negative weights,
  forward or on the reversed graph.
* :func:`bellman_ford` -- queue-based Bellman-Ford (SPFA) for weights that may
  be negative, used for energy because regeneration makes downhill edges
  negative.  Physical networks contain no negative cycles (a closed loop has
  zero net elevation and positive losses), which is checked.
* :class:`LowerBounds` -- reverse searches from the destination that give,
  for every node ``v``:

  ``time_lb[v]``    minimum driving time to the destination,
  ``energy_lb[v]``  minimum (possibly negative) energy to the destination,
  ``cost_lb[v]``    minimum generalised *driving* cost to the destination.

  From these, :meth:`LowerBounds.h_time` and :meth:`LowerBounds.h_cost` give
  *consistent* A* heuristics on the battery-expanded state space ``(v, e)``:

  .. math::

     h(v, e) = T_{lb}(v) + \\frac{\\max(0,\\; E_{lb}(v) + e_{dest} - e)}{P_{max}}

  The first term is consistent on drive edges and constant on charging;
  the second never increases along a drive edge (energy only falls, or is
  capped) and falls by at most ``de / P_max`` seconds when ``de`` kWh are
  charged -- never faster than real charging time.  The sum is therefore
  consistent; see ``docs/algorithms.md`` for the full proof.
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from typing import Sequence

from .network import RoadNetwork
from .scenario import PreparedScenario
from .units import SECONDS_PER_HOUR

INF = math.inf


def dijkstra(
    net: RoadNetwork,
    source: int,
    weight: Sequence[float],
    *,
    reverse: bool = False,
    target: int | None = None,
) -> tuple[list[float], list[int]]:
    """Distances and predecessor edges from ``source`` (or *to* it if reverse)."""
    n = net.num_nodes
    dist = [INF] * n
    pred = [-1] * n
    dist[source] = 0.0
    adj = net.in_edges if reverse else net.out_edges
    other = net.tail if reverse else net.head
    heap = [(0.0, source)]
    heappop, heappush = heapq.heappop, heapq.heappush  # (local names: hot loop)
    while heap:
        d, u = heappop(heap)
        if d > dist[u]:
            continue
        if u == target:
            break
        for e in adj[u]:
            w = other[e]
            nd = d + weight[e]
            if nd < dist[w]:
                dist[w] = nd
                pred[w] = e
                heappush(heap, (nd, w))
    return dist, pred


def bellman_ford(
    net: RoadNetwork,
    source: int,
    weight: Sequence[float],
    *,
    reverse: bool = False,
) -> list[float]:
    """Shortest distances with possibly negative weights (SPFA + SLF)."""
    n = net.num_nodes
    dist = [INF] * n
    dist[source] = 0.0
    in_queue = [False] * n
    relax_count = [0] * n
    adj = net.in_edges if reverse else net.out_edges
    other = net.tail if reverse else net.head
    q: deque[int] = deque([source])
    in_queue[source] = True
    popleft, append, appendleft = q.popleft, q.append, q.appendleft  # (local names: hot loop)
    while q:
        u = popleft()
        in_queue[u] = False
        du = dist[u]
        for e in adj[u]:
            w = other[e]
            nd = du + weight[e]
            if nd < dist[w] - 1e-12:
                dist[w] = nd
                if not in_queue[w]:
                    relax_count[w] += 1
                    if relax_count[w] > n:
                        raise ValueError("negative cycle in energy weights")
                    if q and nd < dist[q[0]]:
                        appendleft(w)
                    else:
                        append(w)
                    in_queue[w] = True
    return dist


def path_from_pred(net: RoadNetwork, pred: list[int], source: int, target: int) -> list[int]:
    """Edge path from a forward-search predecessor array."""
    path = []
    v = target
    while v != source:
        e = pred[v]
        if e < 0:
            return []
        path.append(e)
        v = net.tail[e]
    path.reverse()
    return path


def path_from_reverse_pred(net: RoadNetwork, pred: list[int], source: int, target: int) -> list[int]:
    """Edge path ``source -> target`` from a *reverse* search rooted at ``target``."""
    path = []
    v = source
    while v != target:
        e = pred[v]
        if e < 0:
            return []
        path.append(e)
        v = net.head[e]
    return path


def min_level_weights(prepared: PreparedScenario, which: str) -> list[float]:
    """Per-edge minimum over speed levels of ``time``, ``energy`` or ``cost``."""
    idx = {"time": 1, "energy": 2, "cost": 3}[which]
    # (same values as min(o[idx] for o in opts); single-level edges skip the generator)
    return [opts[0][idx] if len(opts) == 1 else min([o[idx] for o in opts]) for opts in prepared.edge_options]


class LowerBounds:
    """Admissible, consistent bounds towards one destination (see module docs)."""

    def __init__(self, prepared: PreparedScenario, target: int | None = None) -> None:
        P = prepared
        if P.vot_per_s < 0 or P.w_money < 0 or P.w_deg < 0:  # Scenario.validate rejects them first
            raise ValueError("lower bounds require non-negative cost weights")  # Dijkstra would not terminate
        self.prepared = P
        self.target = P.destination if target is None else target
        net = P.net
        self.time_lb, self._time_pred = dijkstra(net, self.target, min_level_weights(P, "time"), reverse=True)
        self.energy_lb = bellman_ford(net, self.target, min_level_weights(P, "energy"), reverse=True)
        self.cost_lb, _ = dijkstra(net, self.target, min_level_weights(P, "cost"), reverse=True)
        self.p_max = P.p_max
        self.e_goal = P.e_dest_min
        #: seconds of charging per kWh at best
        self.sec_per_kwh = SECONDS_PER_HOUR / self.p_max if self.p_max > 0 else INF
        #: generalised cost per kWh charged at best (time + money + wear)
        self.cost_per_kwh = (
            P.vot_per_s * self.sec_per_kwh + P.w_money * P.min_money_rate + P.w_deg * P.min_soh_rate
        )

    def reachable(self, v: int) -> bool:
        return self.time_lb[v] < INF

    def deficit(self, v: int, energy: float) -> float:
        """Lower bound on energy that still has to be charged [kWh]."""
        return max(0.0, self.energy_lb[v] + self.e_goal - energy)

    def h_time(self, v: int, energy: float) -> float:
        d = self.energy_lb[v] + self.e_goal - energy
        if d > 0.0:
            return self.time_lb[v] + d * self.sec_per_kwh
        return self.time_lb[v]

    def h_cost(self, v: int, energy: float) -> float:
        d = self.energy_lb[v] + self.e_goal - energy
        if d > 0.0:
            return self.cost_lb[v] + d * self.cost_per_kwh
        return self.cost_lb[v]

    def h(self, v: int, energy: float) -> float:
        return self.h_time(v, energy) if self.prepared.objective == "time" else self.h_cost(v, energy)
