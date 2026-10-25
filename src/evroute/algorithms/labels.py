r"""Battery-constrained label setting: BLS and BLS-A* (the exact planners).

This module implements the flagship exact algorithms of EVRoute:

* :class:`LabelSettingPlanner` (``"bls"``) -- label setting in Dijkstra
  order on the battery-expanded state space;
* :class:`LabelAStarPlanner` (``"bls-astar"``) -- the same search guided by
  a consistent energy-deficit A* potential (:class:`AStarPotential`).

Both return the **same optimum**: the minimum trip time (objective
``"time"``) or generalised cost (objective ``"cost"``) over all routes,
speed levels and charging stops whose charging targets lie in the target
set (``P.target_grid`` plus, by default, the breakpoints of each station's
charging function).  Consumption, regeneration, time-dependent queueing and
the exact charging curves are *not* discretised -- only the choice of the
charging target is.  The continuous-target optimum is computed by
:mod:`evroute.algorithms.cfp`; the grid optimum converges to it as
``charge_step_soc`` shrinks.

Model
=====

A *label* is a partial route ending at a node ``v`` and is summarised by
``(t, g, e)``: trip time [s], generalised cost and battery energy [kWh]
(continuous, never rounded).  Two transitions exist:

*Drive* edge ``a = (v, w)`` at speed level ``k`` (every option in
``P.edge_options[a]``)::

    e' = min(e - c_ak, capacity)        # regeneration is clamped at full
    t' = t + tau_ak,   g' = g + drive_cost_ak

allowed iff ``e >= e_min`` (departure) and ``e' >= e_min`` (arrival).  The
origin's initial energy may lie below the reserve; such a label can only
charge.

*Charge* at station ``s`` located at ``v`` to a target ``y`` with
``e < y <= e_charge_cap``::

    t' = t + (wait_s(s, t) + setup_s + F_s(y) - F_s(e)),    e' = y
    g' = g + charge_cost(s, e, y, ...)

where ``F_s`` is the exact piecewise-linear "time to charge from empty"
function of :class:`~evroute.charging.ChargingFunction`.  The arithmetic is
literally the one of :func:`evroute.evaluate.evaluate`, so for the time
objective the internal and the evaluated objective agree bit for bit.  A
label that was created by charging at ``v`` does not charge again at ``v``
(at most one stop per visit of a node -- two consecutive stops would pay
the waiting and setup time twice).  Targets below the reserve are skipped
(such a label could never move again).

Dominance
=========

Label ``A`` *dominates* ``B`` at the same node if ``A`` is no worse in every
criterion that can influence the future:

* objective ``"time"``: ``t_A <= t_B`` and ``e_A >= e_B``;
* objective ``"cost"``: ``g_A <= g_B`` and ``e_A >= e_B``, plus
  ``t_A <= t_B`` when waits are time dependent (``P.time_dependent``),
  because then the cost of a later stop depends on the arrival time.

This is sound because every transition is monotone: driving maps a larger
energy to a larger (or equal) energy, ``F_s(y) - F_s(e)`` decreases in
``e``, and expected waits satisfy the FIFO property ``t + w(t)``
non-decreasing (Orda & Rom 1990; guaranteed by
:class:`~evroute.queueing.WaitProfile`), so starting a stop earlier never
makes it end later.  With static waits the cost objective is independent of
the clock, hence the 2-D rule.

*One stop per visit.*  A label ``A`` created by charging at ``v`` may
dominate a label ``B`` that is still allowed to charge at ``v``.  If ``v``
hosts a **single** station this is still exact: ``A`` was created by some
settled free label ``C`` at ``v``, and because charging costs are additive
along the charging function (``F_s``, money and wear are cumulative), ``C``
charging directly to any target ``y > e_A`` finishes no later and no more
expensively than ``B`` charging to ``y``
(``t_C + w(t_C) + setup + F(y) - F(e_C) = t_A - F(e_A) + F(y) <= t_B + w(t_B)
+ setup + F(y) - F(e_B)``, likewise for the cost), and targets ``y <= e_A``
are dominated by ``A`` itself.  At a node with **several** stations this
argument fails (``C`` might have charged at a different station), so such
nodes keep two Pareto sets: free labels are only compared with free labels,
charged labels with both.

Search order and exactness
==========================

Labels are kept in a binary heap keyed by ``primary + h(v, e)``
(``h = 0`` for BLS), ties broken by larger energy, then insertion order.
Every transition has a non-negative primary increment, and the A* potential
``h`` (see :class:`AStarPotential`) is *consistent* on the expanded graph
(drive and charge transitions), *non-increasing in the energy* and zero at
the destination once the arrival requirement is met.  Consequently

1. popped keys are non-decreasing (Hart, Nilsson & Raphael 1968);
2. a dominating label has a key no larger than the label it dominates (its
   primary is not larger and ``h`` at its larger energy is not larger), so
   it is popped first -- discarding a popped label that is dominated by an
   already *settled* label at its node is therefore exact and the settled
   sets stay Pareto-minimal up to exact ties (the generalised permanent labelling of
   Desrochers & Soumis 1988; Martins 1984 for the multi-criteria case);
3. the first popped label at the destination that meets the arrival
   requirement is optimal.

Labels dominated by a settled label are also discarded *before* they are
pushed (same argument, fewer heap operations).  Nodes from which the
destination cannot be reached at all are never entered.  BLS-A* moreover
keeps an *upper bound*: a label whose energy suffices to finish along the
shortest-driving tree of the potential without charging
(:func:`completion_energy`) has a key equal to the value of a complete
route, and labels whose key exceeds the best such value are never pushed.
They could only be popped after the optimum anyway, so this saves memory and
heap work without changing the search order or the result.

The potential matters a lot: BLS-A* defaults to the ``"tight"`` potential
of :class:`AStarPotential`, which couples time (or cost) and energy on the
same path and counts unavoidable stops; ``potential="basic"`` reproduces
:meth:`~evroute.search.LowerBounds.h` exactly.  With several speed levels
the Pareto sets grow quickly (every edge trades time for energy), and with
the cost objective eco-driving is nearly cost-neutral, so many labels have
almost equal keys; the search stays exact but needs more labels
(``max_labels`` is the safeguard).

The resource-constrained label-setting view follows Irnich & Desaulniers
(2005).  For electric vehicles the battery-expanded label-setting approach
with energy labels goes back to Artmeier et al. (2010) and Sachenbacher et
al. (2011); Storandt & Funke (2012) added charging stations and Baum et al.
(2019) gave the exact "Charging Function Propagation" with nonlinear
charging (implemented in :mod:`evroute.algorithms.cfp`).  The concave
piecewise-linear charging model follows Montoya et al. (2017).

Data layout
===========

Labels live in flat parallel lists (``primary``, ``secondary``, ``energy``,
``node``, ``parent``, ``how``) indexed by a label id; the heap holds only
``(key, -energy, label_id)``.  ``how`` packs the transition that created a
label: ``edge * n_levels + level`` for a drive, ``-2 - station`` for a
charging stop (the *charged-here* flag is ``how <= -2``) and ``-1`` for the
root.  The secondary criterion is the generalised cost for the time
objective and the trip time for the cost objective.  Per node, the settled
2-D Pareto set is a *staircase*: sorted lists (primary ascending, energy
strictly ascending, label id), queried and updated with :mod:`bisect` in
``O(log n)``.  The 3-D sets (:class:`_Pareto3`) combine projection
staircases with energy bins holding (primary, secondary) staircases, so a
query costs a few bisections instead of a scan of thousands of labels.

References
==========

* E. W. Dijkstra (1959). A note on two problems in connexion with graphs.
  *Numerische Mathematik* 1, 269-271.
* P. E. Hart, N. J. Nilsson, B. Raphael (1968). A formal basis for the
  heuristic determination of minimum cost paths. *IEEE Transactions on
  Systems Science and Cybernetics* 4(2), 100-107.
* E. Q. V. Martins (1984). On a multicriteria shortest path problem.
  *European Journal of Operational Research* 16(2), 236-245.
* M. Desrochers, F. Soumis (1988). A generalized permanent labelling
  algorithm for the shortest path problem with time windows. *INFOR* 26(3),
  191-212.
* A. Orda, R. Rom (1990). Shortest-path and minimum-delay algorithms in
  networks with time-dependent edge-length. *Journal of the ACM* 37(3),
  607-625.
* S. Irnich, G. Desaulniers (2005). Shortest path problems with resource
  constraints. In G. Desaulniers, J. Desrosiers, M. M. Solomon (eds.),
  *Column Generation*, Springer, 33-65.
* A. Artmeier, J. Haselmayr, M. Leucker, M. Sachenbacher (2010). The
  shortest path problem revisited: optimal routing for electric vehicles.
  *KI 2010: Advances in Artificial Intelligence*, Springer LNCS.
* M. Sachenbacher, M. Leucker, A. Artmeier, J. Haselmayr (2011). Efficient
  energy-optimal routing for electric vehicles. *Proc. AAAI 2011*.
* S. Storandt, S. Funke (2012). Cruising with a battery-powered vehicle and
  not getting stranded. *Proc. AAAI 2012*.
* A. Montoya, C. Guéret, J. E. Mendoza, J. G. Villegas (2017). The electric
  vehicle routing problem with nonlinear charging function.
  *Transportation Research Part B* 103, 87-110.
* M. Baum, J. Dibbelt, A. Gemsa, D. Wagner, T. Zündorf (2019). Shortest
  feasible paths with charging stops for battery electric vehicles.
  *Transportation Science* 53(6), 1627-1655.
"""

from __future__ import annotations

import heapq
import math
import time
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence

from ..evaluate import evaluate, infeasible_plan
from ..plan import ChargeDecision, PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario
from ..search import LowerBounds, bellman_ford, dijkstra, min_level_weights
from .base import Planner

INF = math.inf
#: Energy tolerance of the search [kWh] (reserve checks, "target above energy").
EPS = 1e-9
#: ``how`` code of the root label.
_ROOT = -1
#: Valid values of the ``targets`` option.
TARGET_MODES = ("grid+breaks", "grid")
#: Valid values of the ``potential`` option of BLS-A*.
POTENTIALS = ("tight", "basic")

__all__ = [
    "POTENTIALS",
    "TARGET_MODES",
    "AStarPotential",
    "LabelAStarPlanner",
    "LabelSettingPlanner",
    "best_rate_per_kwh",
    "charging_targets",
    "completion_energy",
    "fastest_feasible_tree",
    "min_stop_overhead_s",
    "reaches",
]


# ======================================================================
# helpers shared with other modules
# ======================================================================
def charging_targets(P: PreparedScenario, station: int, mode: str = "grid+breaks") -> list[float]:
    """Sorted charging targets [kWh] considered at ``station``.

    ``"grid"`` is ``P.target_grid`` (multiples of ``charge_step_soc`` up to
    ``e_charge_cap``); ``"grid+breaks"`` adds the interior breakpoints of
    the station's charging function that do not exceed ``e_charge_cap`` --
    the energies at which the charging power changes, where continuous
    optima like to stop.  Values are clamped to ``e_charge_cap`` (so that
    the evaluator charges exactly to them) and values closer than ``1e-9``
    kWh to a smaller one are merged.
    """
    if mode not in TARGET_MODES:
        raise ValueError(f"unknown targets mode {mode!r}; choose from {TARGET_MODES}")
    cap = P.e_charge_cap
    vals = [min(y, cap) for y in P.target_grid if y <= cap + EPS]
    if mode == "grid+breaks":
        vals.extend(b for b in P.cf[station].e_breaks[1:-1] if b <= cap)
    vals.sort()
    out: list[float] = []
    for y in vals:
        if y <= 0.0 or (out and y - out[-1] <= EPS):
            continue
        out.append(y)
    return out


def reaches(P: PreparedScenario, target: int) -> list[bool]:
    """``True`` for every node from which ``target`` can be reached (reverse DFS)."""
    net = P.net
    ok = [False] * net.num_nodes
    ok[target] = True
    stack = [target]
    in_edges, tail = net.in_edges, net.tail
    while stack:
        u = stack.pop()
        for a in in_edges[u]:
            w = tail[a]
            if not ok[w]:
                ok[w] = True
                stack.append(w)
    return ok


def min_stop_overhead_s(P: PreparedScenario) -> float:
    """Smallest possible ``wait + setup`` of one charging stop [s] (0 without stations)."""
    best = INF
    for s, prof in enumerate(P.wait_profiles):
        best = min(best, P.setup_s[s] + max(0.0, min(prof.waits_s)))
    return 0.0 if best == INF else best


# ======================================================================
# A* potentials
# ======================================================================
class AStarPotential:
    r"""Consistent lower bound ``h(v, e)`` on the remaining objective.

    All potentials used here have the form

    .. math::

        h(v, e) = B(v) + r\,\max(0, E^{lin}_v - e)
                  + \sigma \left\lceil \frac{\max(0, E^{stop}_v - e)}{R} \right\rceil

    with ``B`` a lower bound on the remaining *driving* time (or cost),
    ``r`` the cheapest possible price of one charged kWh (seconds at the
    highest charging power, or generalised cost), ``sigma`` the smallest
    overhead of one stop (setup + minimum wait) and ``R`` the most energy one
    stop can add.  ``B(v) = inf`` marks nodes that cannot reach the
    destination.  Construct them with :meth:`zero`, :meth:`basic` or
    :meth:`tight`.

    *Basic* (``== LowerBounds.h``): ``B`` = reverse shortest driving
    time/cost, ``E^lin = E_lb(v) + e_goal`` with the reverse minimum-energy
    distance ``E_lb`` (Bellman-Ford; regeneration makes weights negative),
    ``sigma = 0``.

    *Tight* (default of BLS-A*) strengthens all parts:

    * ``r`` = :func:`best_rate_per_kwh` -- for the cost objective time, money
      and wear of a kWh are minimised *jointly* per station and SoC bin (not
      each over all stations), and only bins the battery can actually be
      charged in count;
    * ``E^lin_v = e_goal + (W(v) - B(v)) / r`` where ``W`` is the reverse
      shortest-path distance for the coupled edge weight
      ``min_k (b_ak + r c_ak)`` -- time (or cost) and energy are measured on
      the **same** path and speed level instead of on two different ones,
      so ``W(v) >= B(v) + r E_lb(v)`` and the bound is never weaker;
    * ``sigma``/``R`` count the stops that are unavoidable for the energy
      deficit ``E_lb(v) + e_goal - e``.

    *Admissible*: if the rest of a route drives with primary ``b`` and
    consumption ``C`` and charges ``D`` kWh in ``m`` stops, the regeneration
    cap gives ``D >= C + e_goal - e``, so ``m >= ceil(D / R)`` and the rest
    costs at least ``b + r D + sigma m >= max(B, W + r (e_goal - e)) +
    sigma ceil(max(0, E_lb + e_goal - e) / R)`` because ``b + r C >= W``,
    ``b >= B`` and ``C >= E_lb``.  *Consistent*: on a drive edge ``B`` and
    ``W`` are shortest-path potentials and the energy falls by at least the
    consumption, so both linear parts are consistent and the stop term can
    only grow; a charging stop adding ``D <= R`` kWh lowers the linear part
    by at most ``r D`` and the stop term by at most ``sigma`` -- never more
    than the stop costs.  Every part is non-increasing in ``e``, and
    ``h(destination, e) = 0`` for ``e >= e_goal``.
    """

    __slots__ = ("base", "e_lin", "e_stop", "inv_r", "name", "need", "rate", "stop")

    def __init__(
        self,
        base: Sequence[float],
        e_lin: Sequence[float],
        rate: float,
        stop: float = 0.0,
        e_stop: Sequence[float] | None = None,
        inv_r: float = 0.0,
        name: str = "",
    ) -> None:
        n = len(base)
        self.base = list(base)
        self.e_lin = list(e_lin)
        self.rate = float(rate)
        self.stop = float(stop)
        self.inv_r = float(inv_r)
        if e_stop is None or self.stop <= 0.0 or self.inv_r <= 0.0:
            self.e_stop = [-INF] * n
            self.stop = 0.0
        else:
            # the engine evaluates the stop term only where the linear term is
            # active, which needs e_stop <= e_lin
            self.e_stop = [min(a, b) for a, b in zip(e_stop, self.e_lin, strict=True)]
        self.name = name
        #: ``need[v]``: energy that suffices to finish along the reverse
        #: shortest-``B`` tree without charging (``inf`` if none); a label with
        #: at least this energy has a complete route of value ``p + B(v)``,
        #: which BLS-A* uses as an upper bound to skip hopeless pushes.
        self.need: list[float] = [INF] * n

    def __call__(self, v: int, e: float) -> float:
        """``h(v, e)`` exactly as the search evaluates it."""
        hb = self.base[v]
        if hb == INF:
            return INF
        d = self.e_lin[v] - e
        if d > 0.0:
            k = hb + d * self.rate
            d = (self.e_stop[v] - e) * self.inv_r
            if d > 0.0:
                k += self.stop if d <= 1.0 else self.stop * math.ceil(d)
            return k
        return hb

    # ------------------------------------------------------------ factories
    @classmethod
    def zero(cls, P: PreparedScenario, target: int | None = None) -> AStarPotential:
        """``h = 0`` on nodes that reach ``target`` (default destination), ``inf`` elsewhere."""
        ok = reaches(P, P.destination if target is None else target)
        n = P.net.num_nodes
        return cls([0.0 if r else INF for r in ok], [-INF] * n, 0.0, name="none (Dijkstra order)")

    @classmethod
    def basic(cls, P: PreparedScenario, lb: LowerBounds | None = None) -> AStarPotential:
        """``LowerBounds.h_time`` / ``h_cost`` of :mod:`evroute.search`."""
        lb = lb or LowerBounds(P)
        base, rate = _bound_and_rate(P, lb)
        g = lb.e_goal
        e_lin = [el + g if el < INF else INF for el in lb.energy_lb]
        pot = cls(base, e_lin, rate, name="basic energy-deficit (LowerBounds.h)")
        pot.need = completion_energy(P, base)
        return pot

    @classmethod
    def tight(cls, P: PreparedScenario, lb: LowerBounds | None = None) -> AStarPotential:
        """Coupled time-energy bound plus unavoidable-stop overhead (see class docs)."""
        lb = lb or LowerBounds(P)
        base, rate = _bound_and_rate(P, lb)
        rate = max(rate, best_rate_per_kwh(P))
        g = lb.e_goal
        e_basic = [el + g if el < INF else INF for el in lb.energy_lb]
        e_lin = list(e_basic)
        if rate > 0.0 and math.isfinite(rate):
            idx = 1 if P.objective == "time" else 3
            w = [min(o[idx] + rate * o[2] for o in opts) for opts in P.edge_options]
            W = bellman_ford(P.net, lb.target, w, reverse=True)
            for v, (bv, wv) in enumerate(zip(base, W, strict=True)):
                if bv < INF and wv < INF:
                    e_lin[v] = max(e_basic[v], g + (wv - bv) / rate)
        overhead = min_stop_overhead_s(P)
        stop = overhead if P.objective == "time" else P.vot_per_s * overhead
        r = P.e_charge_cap - min(P.e_min, P.e_init) + EPS
        inv_r = 1.0 / r if r > 0.0 and P.stations else 0.0
        pot = cls(base, e_lin, rate, stop, e_basic, inv_r, name="tight coupled energy-deficit + stop count")
        pot.need = completion_energy(P, base)
        return pot


def completion_energy(P: PreparedScenario, base: Sequence[float]) -> list[float]:
    """Energy that suffices to reach the destination on a ``B``-shortest path.

    ``base`` is the reverse shortest-path distance ``B`` (driving time or
    cost, minimum over speed levels per edge).  A reverse Dijkstra with the
    same weights yields a shortest-path tree; along it,
    ``need[v] = max(e_min, need[w] + c)`` with ``need[destination] =
    e_dest_min`` (the regeneration cap never helps a requirement that is
    already ``<= capacity``).  ``inf`` where even a full battery is not
    enough.
    """
    net = P.net
    n = net.num_nodes
    idx = 1 if P.objective == "time" else 3
    weight: list[float] = []
    cons: list[float] = []
    for opts in P.edge_options:
        best = min(opts, key=lambda o: (o[idx], o[2]))
        weight.append(best[idx])
        cons.append(best[2])
    dist, pred = dijkstra(net, P.destination, weight, reverse=True)
    need = [INF] * n
    need[P.destination] = P.e_dest_min
    head = net.head
    cap, emin = P.capacity, P.e_min
    done = [False] * n
    done[P.destination] = True
    for v0 in range(n):
        if done[v0] or dist[v0] == INF:
            continue
        chain = []
        v = v0
        while not done[v] and pred[v] >= 0:
            chain.append(v)
            v = head[pred[v]]
        nw = need[v] if done[v] else INF
        for u in reversed(chain):
            if nw < INF:
                x = max(emin, nw + cons[pred[u]])
                nw = x if x <= cap else INF
            need[u] = nw
            done[u] = True
    # ``base`` must describe the same tree: only trust nodes whose distance agrees
    for v in range(n):
        if need[v] < INF and not math.isclose(dist[v], base[v], rel_tol=1e-12, abs_tol=1e-9):
            need[v] = INF
    return need


def best_rate_per_kwh(P: PreparedScenario) -> float:
    """Cheapest price of one charged kWh over all stations and usable SoC bins.

    Seconds per kWh for the time objective; generalised cost per kWh
    (``vot * seconds + w_money * money + w_deg * wear``, minimised *jointly*
    per station and bin) for the cost objective.  Only bins that intersect
    the chargeable range ``[min(e_min, e_init), e_charge_cap]`` count.
    Returns 0 without stations.
    """
    lo = min(P.e_min, P.e_init) - EPS
    hi = P.e_charge_cap
    time_obj = P.objective == "time"
    best = INF
    for f in P.cf:
        eb = f.e_breaks
        for i, pw in enumerate(f.power_kw):
            if eb[i + 1] <= lo or eb[i] >= hi:
                continue
            sec = 3600.0 / pw
            r = sec if time_obj else (
                P.vot_per_s * sec + P.w_money * f.money_per_kwh[i] + P.w_deg * f.soh_pct_per_kwh[i]
            )
            best = min(best, r)
    return 0.0 if best == INF else best


def _bound_and_rate(P: PreparedScenario, lb: LowerBounds) -> tuple[list[float], float]:
    if P.objective == "time":
        return list(lb.time_lb), lb.sec_per_kwh
    return list(lb.cost_lb), lb.cost_per_kwh


# ======================================================================
# dominance helpers (3-D case)
# ======================================================================
#: Number of energy bins of the 3-D Pareto sets (a power of two).
_NB3 = 512
#: Size from which a 3-D Pareto set switches from a scan to the binned tree.
_P3_BUILD = 1024


def _stair_add_min(Pv: list[float], Qv: list[float], p: float, q: float) -> bool:
    """Add ``(p, q)`` to a *minimum* staircase (``P`` ascending, ``Q`` strictly
    descending) unless some ``(p', q')`` with ``p' <= p, q' <= q`` exists;
    returns whether the point was added."""
    j = bisect_right(Pv, p)
    if j and Qv[j - 1] <= q:
        return False
    if j == len(Pv):
        Pv.append(p)
        Qv.append(q)
        return True
    lo = bisect_left(Pv, p)
    hi = j
    L = len(Pv)
    while hi < L and Qv[hi] >= q:
        hi += 1
    Pv[lo:hi] = [p]
    Qv[lo:hi] = [q]
    return True


class _Pareto3:
    """Settled labels ``(p, q, e)`` of one node for the 3-D dominance test.

    The query "is there a settled ``(p', q', e')`` with ``p' <= p``,
    ``q' <= q`` and ``e' >= e``?" is an orthant-emptiness query; scanning
    all labels is quadratic once a node holds thousands of labels.  Here:

    1. the node's (primary, energy) staircase (kept by the engine) and a
       (secondary, energy) staircase are checked first -- a label that is not
       dominated in one projection cannot be dominated in 3-D;
    2. small sets (fewer than ``_P3_BUILD`` labels) are then scanned, newest
       first;
    3. larger sets cut the energy axis into ``_NB3`` bins.  A Fenwick tree
       over the bins (in decreasing energy order) stores, per tree node, the
       minimum (primary, secondary) staircase of all labels in its range, so
       the bins strictly above the query's bin cost ``log2(_NB3)``
       bisections; inside the query's own (narrow) bin, labels are grouped by
       exact energy (charging targets create large groups), each group a
       (primary, secondary) staircase, and only groups with ``e' >= e`` are
       inspected.
    """

    __slots__ = ("groups", "inv_w", "qe", "qs", "rows", "tree")

    def __init__(self, inv_w: float) -> None:
        self.qs: list[float] = []
        self.qe: list[float] = []
        self.rows: list[tuple[float, float, float]] = []
        self.tree: list[list[list[float]] | None] | None = None
        # bin -> [distinct negative energies (ascending), one minimum
        #         (primary, secondary) staircase per energy]
        self.groups: dict[int, list] = {}
        self.inv_w = inv_w

    def dominated(self, Pv: list[float], Ev: list[float], p: float, q: float, e: float) -> bool:
        j = bisect_right(Pv, p)
        if not j or Ev[j - 1] < e:
            return False
        j = bisect_right(self.qs, q)
        if not j or self.qe[j - 1] < e:
            return False
        tree = self.tree
        if tree is None:
            for p2, q2, e2 in reversed(self.rows):
                if p2 <= p and q2 <= q and e2 >= e:
                    return True
            return False
        b = int(e * self.inv_w)
        if b >= _NB3:
            b = _NB3 - 1
        k = _NB3 - 1 - b  # Fenwick prefix of the bins with a higher energy
        while k > 0:
            nd = tree[k]
            if nd is not None:
                j = bisect_right(nd[0], p)
                if j and nd[1][j - 1] <= q:
                    return True
            k &= k - 1
        grp = self.groups.get(b)
        if grp is not None:
            stairs = grp[1]
            for k in range(bisect_right(grp[0], -e)):
                Ps, Qs = stairs[k]
                j = bisect_right(Ps, p)
                if j and Qs[j - 1] <= q:
                    return True
        return False

    def add(self, p: float, q: float, e: float) -> None:
        _stair_add_pe(self.qs, self.qe, q, e)
        if self.tree is None:
            rows = self.rows
            rows.append((p, q, e))
            if len(rows) >= _P3_BUILD:
                self.tree = [None] * (_NB3 + 1)
                for r in rows:
                    self._insert(*r)
                self.rows = []
            return
        self._insert(p, q, e)

    def _insert(self, p: float, q: float, e: float) -> None:
        b = int(e * self.inv_w)
        if b >= _NB3:
            b = _NB3 - 1
        grp = self.groups.get(b)
        if grp is None:
            self.groups[b] = [[-e], [([p], [q])]]
        else:
            keys = grp[0]
            k = bisect_left(keys, -e)
            if k < len(keys) and keys[k] == -e:
                Ps, Qs = grp[1][k]
                _stair_add_min(Ps, Qs, p, q)
            else:
                keys.insert(k, -e)
                grp[1].insert(k, ([p], [q]))
        tree = self.tree
        assert tree is not None
        k = _NB3 - b  # 1-based Fenwick position of bin b
        while k <= _NB3:
            nd = tree[k]
            if nd is None:
                tree[k] = [[p], [q]]
            elif not _stair_add_min(nd[0], nd[1], p, q):
                # dominated here, hence in every later node: their ranges
                # contain this node's range
                break
            k += k & -k


def _stair_add(Pv: list[float], Ev: list[float], Iv: list[int], p: float, e: float, i: int) -> None:
    """Add point ``(p, e)`` (label ``i``) to a staircase unless it is dominated.

    The staircase keeps ``Pv`` strictly increasing and ``Ev`` strictly
    increasing; points dominated by the new one are removed.
    """
    j = bisect_right(Pv, p)
    if j and Ev[j - 1] >= e:
        return
    if j == len(Pv):
        Pv.append(p)
        Ev.append(e)
        Iv.append(i)
        return
    lo = bisect_left(Pv, p)
    hi = j
    L = len(Pv)
    while hi < L and Ev[hi] <= e:
        hi += 1
    Pv[lo:hi] = [p]
    Ev[lo:hi] = [e]
    Iv[lo:hi] = [i]


def _stair_add_pe(Pv: list[float], Ev: list[float], p: float, e: float) -> None:
    """:func:`_stair_add` without label ids (for the secondary projection)."""
    j = bisect_right(Pv, p)
    if j and Ev[j - 1] >= e:
        return
    if j == len(Pv):
        Pv.append(p)
        Ev.append(e)
        return
    lo = bisect_left(Pv, p)
    hi = j
    L = len(Pv)
    while hi < L and Ev[hi] <= e:
        hi += 1
    Pv[lo:hi] = [p]
    Ev[lo:hi] = [e]


# ======================================================================
# the engine
# ======================================================================
class _LabelSearch:
    """Result of one run of :func:`_label_search` (label store + counters)."""

    __slots__ = (
        "bounded", "charged_stair", "energy", "final", "final_key", "generated", "how",
        "n_levels", "node", "parent", "peak_queue", "primary", "pruned", "pushes",
        "secondary", "settled", "stair3", "stair_e", "stair_i", "stair_p", "status",
        "time_objective",
    )
    # label store (parallel lists indexed by label id)
    primary: list[float]
    secondary: list[float]
    energy: list[float]
    node: list[int]
    parent: list[int]
    how: list[int]
    # settled Pareto sets per node (and per charged set at multi-station nodes)
    stair_p: list[list[float]]
    stair_e: list[list[float]]
    stair_i: list[list[int]]
    stair3: list[_Pareto3] | None
    charged_stair: list[int]
    # outcome and counters
    final: int
    final_key: float
    status: str
    settled: int
    generated: int
    pushes: int
    pruned: int
    bounded: int
    peak_queue: int
    n_levels: int
    time_objective: bool

    def reconstruct(self, i: int) -> tuple[list[int], list[int], list[ChargeDecision]]:
        """Edges, levels and charging stops of the partial route of label ``i``."""
        chain = []
        par = self.parent
        while i >= 0:
            chain.append(i)
            i = par[i]
        chain.reverse()
        edges: list[int] = []
        levels: list[int] = []
        charges: list[ChargeDecision] = []
        how, energy, nlev = self.how, self.energy, self.n_levels
        for j in chain:
            code = how[j]
            if code >= 0:
                a, k = divmod(code, nlev)
                edges.append(a)
                levels.append(k)
            elif code <= -2:
                charges.append(ChargeDecision(len(edges), -2 - code, energy[j]))
        return edges, levels, charges

    def settled_nodes(self) -> list[int]:
        """Nodes holding at least one settled label."""
        chst = self.charged_stair
        sets = self.stair_p
        return [v for v in range(len(chst)) if sets[v] or (chst[v] != v and sets[chst[v]])]


def _label_search(
    P: PreparedScenario,
    *,
    source: int,
    e_start: float,
    t_start: float = 0.0,
    objective: str = "time",
    goal: int = -1,
    e_goal: float = INF,
    potential: AStarPotential | None = None,
    allow_charging: bool = True,
    targets: str = "grid+breaks",
    max_labels: int = 5_000_000,
    max_key: float = INF,
    trace: SearchTrace | None = None,
    fastest_level_only: bool = False,
) -> _LabelSearch:
    """Run the battery-constrained label-setting search (see module docs).

    ``potential`` gives the A* key ``primary + h(v, e)``; ``None`` means
    ``h = 0`` everywhere (Dijkstra order, every node may be entered).
    Nodes with ``h = inf`` are never entered.  With ``goal = -1`` the search
    runs until the queue is empty or the popped key exceeds ``max_key``
    (tree mode).
    """
    net = P.net
    n = net.num_nodes
    time_obj = objective == "time"
    if not time_obj and objective != "cost":
        raise ValueError(f"unknown objective {objective!r}")
    dom3 = (not time_obj) and P.time_dependent
    nlev = len(P.speed_factors)
    cap = P.capacity
    emin_eps = P.e_min - EPS
    egoal_eps = e_goal - EPS
    if potential is None:
        hb = [0.0] * n
        el = es = [-INF] * n
        need = [INF] * n
        rate = stop = inv_r = 0.0
    else:
        hb, el, es = potential.base, potential.e_lin, potential.e_stop
        need = potential.need
        rate, stop, inv_r = potential.rate, potential.stop, potential.inv_r
    # upper bound from "saturated" labels (enough energy to finish on the
    # B-shortest path without charging): such a label's key is the value of a
    # complete route, so labels with a larger key can never be part of a
    # better solution and are not pushed at all
    ub = ub_lim = INF
    ceil = math.ceil

    # ---------------------------------------------------- adjacency table
    # one tuple (head, d_primary, d_secondary, consumption, how) per edge and
    # speed level; heads that cannot reach the goal are left out entirely
    head = net.head
    options = [opts[:1] for opts in P.edge_options] if fastest_level_only else P.edge_options
    adj: list[tuple[tuple[int, float, float, float, int], ...]] = []
    for v in range(n):
        row = []
        for a in net.out_edges[v]:
            w = head[a]
            if hb[w] == INF:
                continue
            base = a * nlev
            for lv, tau, c, dc in options[a]:
                if time_obj:
                    row.append((w, tau, dc, c, base + lv))
                else:
                    row.append((w, dc, tau, c, base + lv))
        adj.append(tuple(row))

    # ------------------------------------------------- charging tables
    station_at = P.station_at
    is_station = P.is_station_node
    TG: list[list[float]] = []
    TY: list[list[float]] = []
    MY: list[list[float]] = []
    SY: list[list[float]] = []
    JMIN: list[int] = []
    if allow_charging:
        for s in range(len(P.stations)):
            f = P.cf[s]
            tg = charging_targets(P, s, targets)
            TG.append(tg)
            TY.append([f.time_at(y) for y in tg])
            MY.append([f.money_at(y) for y in tg])
            SY.append([f.soh_at(y) for y in tg])
            # targets below the reserve lead to labels that can never move
            JMIN.append(bisect_left(tg, emin_eps))
    cf = P.cf
    setup = P.setup_s
    wait = P.wait_s
    vot, wm, wd = P.vot_per_s, P.w_money, P.w_deg

    # charged labels get their own Pareto set only at multi-station nodes
    chst = list(range(n))
    n_sets = n
    for v, lst in station_at.items():
        if len(lst) > 1:
            chst[v] = n_sets
            n_sets += 1

    # ---------------------------------------------------- label store
    PRI: list[float] = []
    SEC: list[float] = []
    EN: list[float] = []
    ND: list[int] = []
    PAR: list[int] = []
    HOW: list[int] = []
    PRI_a, SEC_a, EN_a = PRI.append, SEC.append, EN.append
    ND_a, PAR_a, HOW_a = ND.append, PAR.append, HOW.append

    # 2-D staircases (always; in the 3-D case they hold the (primary, energy)
    # projection and serve as a fast pre-filter) and the 3-D label lists
    SP: list[list[float]] = [[] for _ in range(n_sets)]
    SE: list[list[float]] = [[] for _ in range(n_sets)]
    SI: list[list[int]] = [[] for _ in range(n_sets)]
    inv_w = _NB3 / cap if cap > 0 else 0.0
    S3: list[_Pareto3] = [_Pareto3(inv_w) for _ in range(n_sets)] if dom3 else []

    heap: list[tuple[float, float, int]] = []
    heappush, heappop = heapq.heappush, heapq.heappop
    visit = trace.visit if trace is not None else None

    # root label
    p0, q0 = (t_start, 0.0) if time_obj else (0.0, t_start)
    key0 = INF
    if hb[source] < INF:
        key0 = p0 + (potential(source, e_start) if potential is not None else 0.0)
    PRI_a(p0)
    SEC_a(q0)
    EN_a(e_start)
    ND_a(source)
    PAR_a(-1)
    HOW_a(_ROOT)
    nl = 1
    pushes = 0
    if key0 < INF:
        heappush(heap, (key0, -e_start, 0))
        pushes = 1
        if e_start >= need[source]:
            ub = key0
            ub_lim = ub + 1e-10 * abs(ub) + 1e-9

    settled = generated = pruned = bounded = peak = 0
    final = -1
    final_key = INF
    status = "exhausted"

    while heap:
        lq = len(heap)
        if lq > peak:  # (an if is cheaper than max() in this loop)
            peak = lq
        if nl > max_labels:
            status = "limit"
            break
        key, _, i = heappop(heap)
        if key > max_key:
            status = "max_key"
            break
        v = ND[i]
        x = EN[i]
        p = PRI[i]
        q = SEC[i]
        if v == goal and x >= egoal_eps:
            final, final_key, status = i, key, "goal"
            settled += 1
            if visit is not None:
                visit(v, key)
            break

        # ------------------------------ dominance by settled labels at v
        charged = False
        sid = v
        if is_station[v] and HOW[i] <= -2:
            charged = True
            sid = chst[v]
        if dom3:
            if S3[sid].dominated(SP[sid], SE[sid], p, q, x) or (
                sid != v and S3[v].dominated(SP[v], SE[v], p, q, x)
            ):
                pruned += 1
                continue
            S3[sid].add(p, q, x)
            _stair_add(SP[sid], SE[sid], SI[sid], p, x, i)
        else:
            if sid != v:
                Pf = SP[v]
                j = bisect_right(Pf, p)
                if j and SE[v][j - 1] >= x:
                    pruned += 1
                    continue
            Pv = SP[sid]
            Ev = SE[sid]
            j = bisect_right(Pv, p)
            if j and Ev[j - 1] >= x:
                pruned += 1
                continue
            if j == len(Pv):
                Pv.append(p)
                Ev.append(x)
                SI[sid].append(i)
            else:
                # the new label dominates the entries with p' >= p and e' <= x
                lo = bisect_left(Pv, p)
                hi = j
                L = len(Pv)
                while hi < L and Ev[hi] <= x:
                    hi += 1
                Pv[lo:hi] = [p]
                Ev[lo:hi] = [x]
                SI[sid][lo:hi] = [i]
        settled += 1
        if visit is not None:
            visit(v, key)

        # ------------------------------------------------------- drive
        if x >= emin_eps:
            for w, dp, dq, c, code in adj[v]:
                ne = x - c
                if ne > cap:
                    ne = cap
                elif ne < emin_eps:
                    continue
                np_ = p + dp
                nq = q + dq
                generated += 1
                if dom3:
                    if S3[w].dominated(SP[w], SE[w], np_, nq, ne):
                        pruned += 1
                        continue
                else:
                    Pw = SP[w]
                    if Pw:
                        j = bisect_right(Pw, np_)
                        if j and SE[w][j - 1] >= ne:
                            pruned += 1
                            continue
                d = el[w] - ne
                if d > 0.0:
                    k = np_ + hb[w] + d * rate
                    d = (es[w] - ne) * inv_r
                    if d > 0.0:
                        k += stop if d <= 1.0 else stop * ceil(d)
                else:
                    k = np_ + hb[w]
                    if k < ub and ne >= need[w]:
                        ub = k
                        ub_lim = k + 1e-10 * abs(k) + 1e-9
                if k > ub_lim:
                    bounded += 1
                    continue
                PRI_a(np_)
                SEC_a(nq)
                EN_a(ne)
                ND_a(w)
                PAR_a(i)
                HOW_a(code)
                heappush(heap, (k, -ne, nl))
                nl += 1
                pushes += 1

        # ------------------------------------------------------ charge
        if not charged and allow_charging and is_station[v]:
            if time_obj:
                t, g = p, q
            else:
                t, g = q, p
            csid = chst[v]
            hv, elv, esv, needv = hb[v], el[v], es[v], need[v]
            for s in station_at[v]:
                tg = TG[s]
                L = len(tg)
                j0 = bisect_right(tg, x + EPS)
                if j0 < JMIN[s]:
                    j0 = JMIN[s]
                if j0 >= L:
                    continue
                f = cf[s]
                ty, my, sy = TY[s], MY[s], SY[s]
                wv = wait(s, t)
                su = setup[s]
                ws = wv + su
                fx = f.time_at(x)
                mx = f.money_at(x)
                sx = f.soh_at(x)
                for jj in range(j0, L):
                    y = tg[jj]
                    ch = ty[jj] - fx
                    # same arithmetic as evaluate(): t += w + su + ch
                    nt = t + (ws + ch)
                    ng = g + (vot * (ch + wv + su) + wm * (my[jj] - mx) + wd * (sy[jj] - sx))
                    if time_obj:
                        np_, nq = nt, ng
                    else:
                        np_, nq = ng, nt
                    generated += 1
                    if dom3:
                        if S3[csid].dominated(SP[csid], SE[csid], np_, nq, y) or (
                            csid != v and S3[v].dominated(SP[v], SE[v], np_, nq, y)
                        ):
                            pruned += 1
                            continue
                    else:
                        Pw = SP[csid]
                        if Pw:
                            j = bisect_right(Pw, np_)
                            if j and SE[csid][j - 1] >= y:
                                pruned += 1
                                continue
                        if csid != v:
                            Pw = SP[v]
                            j = bisect_right(Pw, np_)
                            if j and SE[v][j - 1] >= y:
                                pruned += 1
                                continue
                    d = elv - y
                    if d > 0.0:
                        k = np_ + hv + d * rate
                        d = (esv - y) * inv_r
                        if d > 0.0:
                            k += stop if d <= 1.0 else stop * ceil(d)
                    else:
                        k = np_ + hv
                        if k < ub and y >= needv:
                            ub = k
                            ub_lim = k + 1e-10 * abs(k) + 1e-9
                    if k > ub_lim:
                        bounded += 1
                        continue
                    PRI_a(np_)
                    SEC_a(nq)
                    EN_a(y)
                    ND_a(v)
                    PAR_a(i)
                    HOW_a(-2 - s)
                    heappush(heap, (k, -y, nl))
                    nl += 1
                    pushes += 1

    res = _LabelSearch()
    res.primary, res.secondary, res.energy = PRI, SEC, EN
    res.node, res.parent, res.how = ND, PAR, HOW
    res.stair_p, res.stair_e, res.stair_i = SP, SE, SI
    res.stair3 = S3 if dom3 else None
    res.charged_stair = chst
    res.final, res.final_key, res.status = final, final_key, status
    res.settled, res.generated, res.pushes = settled, generated, pushes
    res.pruned, res.bounded = pruned + bounded, bounded
    res.peak_queue = peak
    res.n_levels = nlev
    res.time_objective = time_obj
    return res


# ======================================================================
# reusable tree for baselines
# ======================================================================
def fastest_feasible_tree(
    P: PreparedScenario,
    source: int,
    e_start: float,
    *,
    allow_charging: bool = False,
    max_time: float | None = None,
    t_start: float = 0.0,
    min_arrival_energy: Mapping[int, float] | None = None,
    targets: str = "grid+breaks",
    max_labels: int = 5_000_000,
    fastest_level_only: bool = False,
) -> dict[int, tuple]:
    """Earliest battery-feasible arrival at every node reachable from ``source``.

    Runs the time-ordered label setting of this module from ``source`` with
    energy ``e_start`` (labels ``(t, e)``, dominance ``t <= t'`` and
    ``e >= e'``) and returns ``{node: (t, energy, edges, levels)}``: ``t`` is
    the earliest arrival trip time (``t_start`` plus travel), ``energy`` the
    largest arrival energy among the arrivals at that time and
    ``edges``/``levels`` the route (all speed levels of ``P.edge_options``
    are considered).  Every node of a returned route respects the reserve
    ``P.e_min`` on arrival and departure, so it can be fed to
    :func:`~evroute.evaluate.evaluate`.  The source is included as
    ``(t_start, e_start, [], [])`` (unless ``min_arrival_energy`` asks for
    more); if ``e_start`` is below the reserve, nothing else is reachable
    unless charging is allowed.

    Keyword options:

    ``allow_charging``
        also allow charging stops (targets as in :func:`charging_targets`,
        waits evaluated at trip time ``t``).  The values are then 5-tuples
        ``(t, energy, edges, levels, charges)`` whose
        :class:`~evroute.plan.ChargeDecision` positions refer to the
        returned route.
    ``max_time``
        only settle labels at most ``max_time`` seconds after ``t_start``.
    ``t_start``
        trip time at the source (matters for time-dependent waits).
    ``min_arrival_energy``
        ``{node: kWh}``: for these nodes report the earliest arrival with at
        least that much energy (e.g. ``{P.destination: P.e_dest_min}``) --
        the earliest arrival overall may have too little.  Nodes whose
        requirement cannot be met are omitted.
    ``targets``, ``max_labels``
        as for :class:`LabelSettingPlanner`.
    ``fastest_level_only``
        drive every edge at its fastest speed level only.  The heuristic
        baselines use this: with eco speeds the full tree can hold millions
        of labels, and a navigator-style heuristic would not slow down anyway.

    Example (reachability of every station without charging)::

        tree = fastest_feasible_tree(P, P.origin, P.e_init)
        reachable = [st for st in P.stations if st.node in tree]
    """
    max_key = INF if max_time is None else t_start + max_time
    res = _label_search(
        P,
        source=source,
        e_start=e_start,
        t_start=t_start,
        objective="time",
        goal=-1,
        allow_charging=allow_charging,
        targets=targets,
        max_labels=max_labels,
        max_key=max_key,
        fastest_level_only=fastest_level_only,
    )
    need = min_arrival_energy or {}
    out: dict[int, tuple] = {}
    SP, SE, SI, chst = res.stair_p, res.stair_e, res.stair_i, res.charged_stair
    energy, primary = res.energy, res.primary
    for v in range(P.net.num_nodes):
        best = -1
        best_t = INF
        for sid in ((v,) if chst[v] == v else (v, chst[v])):
            Ev = SE[sid]
            if not Ev:
                continue
            j = bisect_left(Ev, need[v] - EPS) if v in need else 0
            if j < len(Ev):
                tj, ij = SP[sid][j], SI[sid][j]
                if best < 0 or tj < best_t or (tj == best_t and energy[ij] > energy[best]):
                    best, best_t = ij, tj
        if best < 0:
            continue
        edges, levels, charges = res.reconstruct(best)
        if allow_charging:
            out[v] = (primary[best], energy[best], edges, levels, charges)
        else:
            out[v] = (primary[best], energy[best], edges, levels)
    return out


# ======================================================================
# planners
# ======================================================================
class LabelSettingPlanner(Planner):
    """Exact battery-constrained label setting in Dijkstra order (BLS).

    Options
    -------
    ``targets``
        ``"grid+breaks"`` (default) or ``"grid"`` -- see
        :func:`charging_targets`.
    ``max_labels``
        safeguard on the number of created labels (default 5 000 000); when
        it is hit an infeasible plan explaining that is returned.

    Guarantee: optimal for the scenario objective among all routes whose
    charging targets lie in the target set (``exact = "grid"``); see the
    module documentation for the proof.  Requires non-negative cost weights
    and FIFO waits (always true for scenarios built by EVRoute).
    """

    name = "bls"
    title = "BLS (label setting)"
    exact = "grid"
    objectives = ("time", "cost")
    description = (
        "Exact label setting on the battery-expanded graph (node, continuous energy) "
        "in Dijkstra order; charging targets on the SoC grid plus charging-curve "
        "breakpoints; Pareto dominance on (objective, energy)."
    )

    def _potential(self, P: PreparedScenario) -> AStarPotential:
        return AStarPotential.zero(P)

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        opts = self.options
        targets = opts.get("targets", "grid+breaks")
        if targets not in TARGET_MODES:
            raise ValueError(f"unknown targets mode {targets!r}; choose from {TARGET_MODES}")
        max_labels = int(opts.get("max_labels", 5_000_000))
        if P.objective not in ("time", "cost"):
            raise ValueError(f"unknown objective {P.objective!r}")
        if P.vot_per_s < 0 or P.w_money < 0 or P.w_deg < 0:
            raise ValueError("label setting requires non-negative cost weights")
        tr = SearchTrace() if trace else None

        t0 = time.perf_counter()
        pot = self._potential(P)
        t1 = time.perf_counter()
        res = _label_search(
            P,
            source=P.origin,
            e_start=P.e_init,
            objective=P.objective,
            goal=P.destination,
            e_goal=P.e_dest_min,
            potential=pot,
            allow_charging=True,
            targets=targets,
            max_labels=max_labels,
            trace=tr,
        )
        t2 = time.perf_counter()
        h0 = pot(P.origin, P.e_init)
        stats = SearchStats(
            algorithm=self.name,
            settled=res.settled,
            generated=res.generated,
            pushes=res.pushes,
            pruned=res.pruned,
            peak_queue=res.peak_queue,
            extra={
                "labels": len(res.node),
                "pruned_by_bound": res.bounded,
                "targets": targets,
                "potential": pot.name,
                "dominance": "3d (cost, time, energy)" if (P.objective == "cost" and P.time_dependent) else "2d",
                "status": res.status,
                "bounds_s": t1 - t0,
                "search_s": t2 - t1,
                "h_origin": h0 if math.isfinite(h0) else None,
            },
        )
        if res.final < 0:
            reason = _infeasibility_reason(P, res, pot, max_labels)
            return infeasible_plan(P, self.name, reason, stats, tr)
        edges, levels, charges = res.reconstruct(res.final)
        decision = PlanDecision(edges, levels, charges)
        return evaluate(
            P,
            decision,
            algorithm=self.name,
            internal_objective=res.primary[res.final],
            stats=stats,
            trace=tr,
        )


class LabelAStarPlanner(LabelSettingPlanner):
    """BLS guided by a consistent energy-deficit A* potential (flagship).

    The key of a label at ``v`` with energy ``e`` is ``primary + h(v, e)``.
    Option ``potential`` selects ``h``:

    ``"tight"`` (default)
        :meth:`AStarPotential.tight` -- coupled time/energy bound plus the
        overhead of unavoidable stops; never weaker than ``"basic"``.
    ``"basic"``
        exactly ``LowerBounds.h_time`` / ``LowerBounds.h_cost`` of
        :mod:`evroute.search`.

    Both are consistent and non-increasing in the energy, so the result is
    identical to :class:`LabelSettingPlanner` while far fewer labels are
    settled.  ``targets`` and ``max_labels`` as for BLS.
    """

    name = "bls-astar"
    title = "BLS-A*"
    exact = "grid"
    objectives = ("time", "cost")
    description = (
        "BLS with a consistent A* potential: lower-bound driving time/cost to the "
        "destination, the energy deficit charged at the best power/price and the "
        "unavoidable stop overhead. Same optimum as BLS, far fewer labels."
    )

    def _potential(self, P: PreparedScenario) -> AStarPotential:
        kind = self.options.get("potential", "tight")
        if kind == "tight":
            return AStarPotential.tight(P)
        if kind == "basic":
            return AStarPotential.basic(P)
        raise ValueError(f"unknown potential {kind!r}; choose from {POTENTIALS}")


# ======================================================================
# diagnostics
# ======================================================================
def _infeasibility_reason(P: PreparedScenario, res: _LabelSearch, pot: AStarPotential, max_labels: int) -> str:
    """Human-readable explanation of why no route was found."""
    o, d = P.origin, P.destination
    if res.status == "limit":
        return (
            f"label limit reached (max_labels={max_labels:,}, {len(res.node):,} labels created) "
            "before the destination was settled; increase max_labels or use a coarser "
            "charge_step_soc / targets='grid'"
        )
    if pot.base[o] == INF:
        return f"destination {d} is not reachable from origin {o} in the road network"
    if P.e_init < P.e_min - EPS and not P.is_station_node[o]:
        return (
            f"initial energy {P.e_init:.2f} kWh is below the reserve {P.e_min:.2f} kWh "
            "and there is no charging station at the origin"
        )
    msg = (
        f"no battery-feasible route from {o} to {d}: the charging infrastructure "
        f"({len(P.stations)} station(s)) cannot bridge the energy gaps "
        f"(start {P.e_init:.2f} kWh, reserve {P.e_min:.2f} kWh, required on arrival "
        f"{P.e_dest_min:.2f} kWh, max charge {P.e_charge_cap:.2f} kWh)"
    )
    try:
        tlb, _ = dijkstra(P.net, d, min_level_weights(P, "time"), reverse=True)
        reached = [v for v in res.settled_nodes() if tlb[v] < INF]
        if reached:
            v = min(reached, key=lambda u: tlb[u])
            name = P.net.node_name.get(v)
            where = f"node {v}" + (f" ({name})" if name else "")
            msg += f"; closest approach: {where}, still at least {tlb[v] / 60.0:.0f} min of driving away"
    except Exception:  # pragma: no cover - diagnostics must never fail
        pass
    return msg
