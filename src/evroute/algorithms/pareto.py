r"""Multi-criteria label setting: the Pareto front of time, money and battery wear.

:class:`ParetoPlanner` (``"pareto"``) computes the trade-off between three
criteria of an EV route that no single-objective planner can show at once:

=========  ============================================================
``time``   total trip time ``T`` [s] (driving, queueing, set-up, charging)
``money``  charging money ``M`` [currency] paid at the stations
``wear``   battery wear ``W`` [percentage points of state of health]:
           driving wear plus charging wear (C-rate, SoC and temperature
           stress of :class:`~evroute.vehicle.AgingModel`)
=========  ============================================================

A route ``R`` is *Pareto optimal* (efficient) if no other feasible route is
at least as good in every criterion and strictly better in one.  The planner
returns one route for every Pareto-optimal criteria vector -- a *minimal
complete set* in the terminology of Hansen (1980) and Martins (1984) -- among
all routes whose charging targets lie in the target set of BLS
(``exact = "grid"``; see :mod:`evroute.algorithms.labels`).  Consumption,
regeneration, time-dependent queueing and the exact charging curves are not
discretised.  ``criteria=("time", "money")`` drops the wear criterion.

Why wear?  Fast DC charging saves time but costs more money *and* more state
of health (high C-rates and high SoC accelerate capacity fade; Pelletier et
al. 2017), slow AC charging is cheap and gentle but slow.  The front makes
this explicit, so that battery-health research (SoC/SoH estimation, ageing
models) can be connected to routing decisions: a better SoH estimate or a
recalibrated :class:`~evroute.vehicle.AgingModel` changes the wear axis of
the front, not the algorithm.

Labels and transitions
======================

A label is a partial route ending at node ``v`` with the vector
``(t, m, w, e)``: trip time, money, wear and battery energy (continuous,
never rounded).  The transitions are exactly those of BLS, with the same
floating-point arithmetic as :func:`evroute.evaluate.evaluate` for time and
money:

*Drive* edge ``a = (v, u)`` at every speed level of ``P.edge_options[a]``::

    e' = min(e - c, capacity),  t' = t + tau,  m' = m,
    w' = w + drive_soh_per_kwh * max(c, 0)

allowed iff ``e >= e_min`` and ``e' >= e_min`` (the origin's initial energy
may be below the reserve; such a label can only charge).

*Charge* at station ``s`` of node ``v`` to a target ``y`` of
:func:`~evroute.algorithms.labels.charging_targets` with ``e < y`` (and
``y >= e_min`` at a node with a single station)::

    t' = t + (wait_s(s, t) + setup_s + F_s(y) - F_s(e))
    m' = m + M_s(y) - M_s(e),   w' = w + S_s(y) - S_s(e),   e' = y

with the cumulative time, money and wear functions ``F_s, M_s, S_s`` of
:class:`~evroute.charging.ChargingFunction`; the wait is evaluated at the
arrival time.  The stop rule is the one of BLS: a label created by a stop at
``s`` may stop again at any *other* station of ``v`` (e.g. fast DC, then
cheap AC), never at ``s`` itself right away (the first stop could have
charged further for less); a stop may end below the reserve at a node with
several stations, since only another stop can follow it there.

Dominance
=========

At a node, label ``A`` dominates ``B`` if ``t_A <= t_B``, ``m_A <= m_B``,
``w_A <= w_B`` and ``e_A >= e_B`` (a label equal in all four is a duplicate
and is dropped as well).  This is sound because every transition is monotone:
more energy never hurts later (driving keeps the order of energies,
``F_s, M_s, S_s`` are non-decreasing so charging from a higher energy is no
more expensive), and the expected waits are FIFO, ``t + wait(t)``
non-decreasing (Orda & Rom 1990; guaranteed by
:class:`~evroute.queueing.WaitProfile`), so arriving earlier never makes a
stop end later -- also with time-dependent waits.  One Pareto set per node
is exact although a label ``A`` created by a stop at ``s`` may not stop at
``s`` next: a stop of a dominated label ``B`` at ``s`` to a target
``y > e_A`` is matched by ``A``'s parent charging directly to ``y`` (no
larger time, money or wear: ``F_s, M_s, S_s`` are additive along the
charging curve and the wait and setup are not paid twice), and one to
``y <= e_A`` by ``A`` itself (see the module docs of
:mod:`evroute.algorithms.labels`).

Search order (Martins 1984, goal-directed)
==========================================

Labels are kept in a binary heap ordered *lexicographically* by
``(t + T_lb(v), m, w)``, ties broken by larger energy, where ``T_lb(v)`` is
the reverse shortest driving time to the destination
(:attr:`~evroute.search.LowerBounds.time_lb`).  ``T_lb(v)`` is the same
constant for every label of a node, so **at every node the labels are
settled in lexicographic order of** ``(t, m, w)`` exactly as in Martins'
algorithm; across nodes the shift makes the search goal-directed -- it is
the consistent vector heuristic ``(T_lb(v), 0, 0)`` of NAMOA* (Mandow &
Pérez de la Cruz 2010): every edge takes at least its lower-bound time and
a charging stop does not move.  ``goal_directed=False`` gives the plain
Martins order (``T_lb = 0``); both return the same front, but the potential
saves one to two orders of magnitude of labels, because far-away labels are
popped only after the front members that prune them are known.

**Lemma 1.**  Every transition increases ``t`` strictly (edges take time; a
stop charges a positive amount) and never decreases ``m`` or ``w``; with the
consistent potential the popped keys are lexicographically non-decreasing.
If ``A`` dominates ``B`` at a node, ``A``'s key is no larger than ``B``'s and
so are its ancestors' keys, so ``A`` -- or a settled label dominating ``A``
-- is popped before ``B`` (exact key ties aside).  Discarding a popped label
that is dominated by a *settled* label is exact (dominance is sound and
transitive), and the settled sets are Pareto-minimal up to exact ties and
only grow.  This is Martins' label-setting argument (Martins
1984) extended by the maximised energy resource (generalised permanent
labelling, Desrochers & Soumis 1988).

**Lemma 2 (time is implicit).**  Whenever a label ``L`` at node ``v`` is
tested -- when it is generated or popped -- every settled label of ``v``
was popped earlier and has a time ``<= t_L``, and every front member ``F``
has ``t_F <= t_L + T_lb(v)``.  The dominance tests therefore only compare
``(m, w, e)`` at a node and ``(m, w)`` against the destination front.  For
the 2-criteria version a node test is one :mod:`bisect` on a
(money, energy) staircase; for 3 criteria a staircase pre-filter is
followed by an orthant query on an energy-binned Fenwick tree of
(money, wear) staircases (:class:`_Archive3`).

Destination and front
=====================

A popped destination label with ``e >= e_dest_min`` is a complete route.  It
is never expanded (any extension is strictly slower and no cheaper) and
enters the front unless a member dominates it in criteria space
``(t, m, w)`` (by Lemma 2 a ``(m, w)`` staircase query).

**Lemma 3.**  In exact arithmetic a new member never dominates an existing
one (that would need equal time and, by the lexicographic order, equal money
and wear -- a duplicate, which is rejected), so the front only grows, in
order of increasing time, and the members are mutually non-dominated.  When
the queue is empty the front is a minimal complete set of the Pareto-optimal
criteria vectors.

*Floating point (front maintenance).*  Routes with mathematically equal trip
times (e.g. the same energy charged at two stations with the same power, or
the same edges summed in a different order) reach the destination with times
that differ in the last bits, and the goal-directed keys ``t + T_lb(v)`` are
monotone only up to a few units in the last place, so a destination label
can be popped a few ulps *before* an equally fast but cheaper one.  The front
is therefore maintained robustly: a new route ``c`` is rejected if a member
``f`` *covers* it (``m_f <= m_c (1 + R)``, ``w_f <= w_c (1 + R)`` with
``R = FRONT_RTOL`` and the epsilon tolerance, and -- implicitly, by the pop
order -- ``t_f <= t_c`` up to a few ulps); otherwise it enters, and every
member ``f`` it covers in turn -- ``t_c <= t_f (1 + TIE_RTOL)``,
``m_c <= m_f (1 + R)``, ``w_c <= w_f (1 + R)`` with ``TIE_RTOL = 1e-12``,
i.e. an equally fast member that is dearer or wears more -- leaves the
front.  Vectors that are equal within these tolerances are resolved
deterministically in favour of the member that was popped first (a later
one is covered and rejected).  A member is only ever removed by a route that
is as fast up to ``TIE_RTOL`` and no worse up to ``R`` in money and wear, so
the coverage statements below hold up to these factors.

Target pruning (NAMOA*-style)
=============================

As in NAMOA* (Mandow & Pérez de la Cruz 2010), a label is discarded -- when
generated and again when popped, because the front grows -- if a front
member dominates its *optimistic completion vector*
``(t + h_t, m + h_m, w + h_w)`` computed by :class:`CompletionBounds` from
:class:`~evroute.search.LowerBounds`.  With the energy deficit
``d = max(0, E_lb(v) + e_dest_min - e)`` (``E_lb`` the reverse minimum-energy
distance; regeneration may make it negative)::

    h_t = T_lb(v) + d * 3600 / P_max + sigma * ceil(d / R)
    h_m = d * (cheapest money per kWh)
    h_w = drive_soh_per_kwh * C_lb(v) + d * (lowest wear per kWh)

where ``C_lb`` is the reverse shortest distance for the *positive*
consumption ``min_k max(c, 0)`` (driving wear ignores regeneration), the
rates are taken over the station bins the battery can actually be charged
in, ``sigma`` is the smallest set-up plus wait of a stop and ``R`` the most
energy one stop can add.  Every completion of the label has to charge at
least ``d`` kWh (the regeneration cap only loses energy), in at least
``ceil(d / R)`` stops, so the vector bounds the criteria of every complete
route through the label from below and pruning loses nothing (only routes
weakly dominated by a known member).  All three components are consistent
(never decrease by more than a transition costs) and non-increasing in the
energy.  By Lemma 2 the time component never decides (every known member is
already faster than ``t + T_lb(v) <= t + h_t``), so the test reduces to money
and wear; ``h_t`` is kept in :class:`CompletionBounds` for completeness and
for the admissibility tests.

Epsilon dominance
=================

Option ``epsilon`` (default ``0.0``: exact) gives relative tolerances
``(eps_money, eps_wear)``: ``F`` *epsilon-dominates* ``x`` if
``m_F <= (1 + eps_money) m_x`` and ``w_F <= (1 + eps_wear) w_x`` (time stays
exact: it is the lexicographic key, Lemma 2).

``epsilon_scope="front"`` (default)
    the tolerance is applied to the two destination tests (front entry and
    target pruning) only; node dominance stays exact.  Every member ``X`` of
    the exact front is then *covered*: the returned front contains ``Y``
    with ``t_Y <= t_X``, ``m_Y <= (1 + eps_money) m_X`` and
    ``w_Y <= (1 + eps_wear) w_X`` -- if a label on ``X``'s route is pruned
    by a member ``F``, then ``F`` is within the tolerance of the label's
    optimistic vector, which lies below ``X``; exact node dominance only
    replaces ``X`` by a route that is no worse.  The front becomes much
    smaller; the search effort, which sits in the node label sets, much
    less so.
``epsilon_scope="labels"``
    the tolerance is also applied to the dominance tests at the nodes.
    This shrinks the label sets as well and makes country-scale instances
    tractable, but the tolerance can compound along a route: ``X`` is
    covered within ``(1 + eps)^(k+1)`` where ``k`` is the number of
    tolerant node prunings on its way (cf. the scaling argument of the
    FPTAS of Warburton 1987).  Use it as a fast approximation.

The epsilon-dominance concept for archives of trade-offs follows Laumanns
et al. (2002).

Floating point.  The destination tests always include a relative tolerance
of ``FRONT_RTOL = 1e-9``, so that routes which differ from a member only by
rounding noise (the same charging decision reached along two almost equally
long paths) do not become spurious members; coverage is therefore exact up
to that factor.  With the potential, heap keys ``t + T_lb(v)`` are rounded
sums, so the order is exact up to one unit in the last place of the key
(``goal_directed=False`` has exact keys).

Output
======

Every front member is replayed by the independent evaluator.  The returned
plan is the member with the smallest generalised cost (scenario objective
``"cost"``) or the smallest trip time (objective ``"time"``); with all three
criteria these equal the optima of BLS with the same targets (the
generalised cost ``VoT*T + w_money*M + w_deg*W`` is non-decreasing in every
criterion, so it is minimised by a front member).  With
``criteria=("time", "money")`` the minimum-cost member is only the best
*front* member, since wear is then ignored.  ``plan.alternatives`` holds all
evaluated members sorted by time, each with its own ``internal_objective``;
``plan.stats.extra`` reports ``front_size`` and the internal criteria
vectors (``front``).  :func:`pareto_front` returns the members directly.

Effort.  Exact multi-criteria label sets grow quickly: speed levels trade
time for energy on every edge and every charging target is a different
(time, money, wear, energy) trade-off.  Typical test grids take well under
a second; a 90-node grid with 14 stations, two speed levels and a 2 % SoC
target grid has a front of ~2 500 members and needs ~2 million labels.  For
large instances use ``epsilon`` with ``epsilon_scope="labels"``, a coarser
``charge_step_soc``, ``targets="grid"`` or ``criteria=("time", "money")``;
``max_labels`` is the safeguard.

References
==========

* P. Hansen (1980). Bicriterion path problems.  In G. Fandel, T. Gal (eds.),
  *Multiple Criteria Decision Making: Theory and Application*, Lecture
  Notes in Economics and Mathematical Systems 177, Springer, 109-127.
* E. Q. V. Martins (1984). On a multicriteria shortest path problem.
  *European Journal of Operational Research* 16(2), 236-245.
* J. P. Warburton (1987). Approximation of Pareto optima in
  multiple-objective, shortest-path problems.  *Operations Research* 35(1),
  70-79.
* M. Desrochers, F. Soumis (1988). A generalized permanent labelling
  algorithm for the shortest path problem with time windows.  *INFOR* 26(3),
  191-212.
* A. Orda, R. Rom (1990). Shortest-path and minimum-delay algorithms in
  networks with time-dependent edge-length.  *Journal of the ACM* 37(3),
  607-625.
* M. Laumanns, L. Thiele, K. Deb, E. Zitzler (2002). Combining convergence
  and diversity in evolutionary multiobjective optimization.  *Evolutionary
  Computation* 10(3), 263-282.
* M. Ehrgott (2005). *Multicriteria Optimization*, 2nd ed., Springer.
* L. Mandow, J. L. Pérez de la Cruz (2010). Multiobjective A* search with
  consistent heuristics.  *Journal of the ACM* 57(5), article 27.
* S. Pelletier, O. Jabali, G. Laporte, M. Veneroni (2017). Battery
  degradation and behaviour for electric vehicles: review and numerical
  analyses of several models.  *Transportation Research Part B* 103,
  158-187.
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
from typing import Any

from ..evaluate import evaluate, infeasible_plan
from ..plan import ChargeDecision, PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario, Scenario
from ..search import LowerBounds, dijkstra
from .base import Planner
from .labels import TARGET_MODES, charging_targets, min_stop_overhead_s

INF = math.inf
#: Energy tolerance of the search [kWh] (same as BLS).
EPS = 1e-9
#: Relative tolerance of the destination tests (front entry, target pruning):
#: routes that differ from a member only by floating-point noise in money or
#: wear (e.g. the same charging decision reached along two almost equally
#: long paths) do not become spurious members.
FRONT_RTOL = 1e-9
#: Relative tolerance under which two trip times count as equal in the front
#: maintenance (module docs, "Floating point"): goal-directed keys are monotone
#: only up to a few ulps, so an equally fast but cheaper route can be popped
#: right after a member it dominates.
TIE_RTOL = 1e-12
#: Names of the criteria, in lexicographic order.
CRITERIA = ("time", "money", "wear")
#: Accepted values of the ``criteria`` option.
CRITERIA_SETS = (("time", "money", "wear"), ("time", "money"))
#: Accepted values of the ``epsilon_scope`` option.
EPSILON_SCOPES = ("front", "labels")
#: Options understood by :class:`ParetoPlanner`.
OPTIONS = ("criteria", "targets", "epsilon", "epsilon_scope", "max_labels", "goal_directed")
#: ``how`` code of the root label.
_ROOT = -1

__all__ = [
    "CRITERIA",
    "CRITERIA_SETS",
    "EPSILON_SCOPES",
    "FRONT_RTOL",
    "OPTIONS",
    "TIE_RTOL",
    "CompletionBounds",
    "ParetoPlanner",
    "chargeable_rates",
    "criteria_vector",
    "dominates",
    "pareto_front",
]


# ======================================================================
# small public helpers
# ======================================================================
def criteria_vector(plan: RoutePlan, criteria: Sequence[str] = CRITERIA) -> tuple[float, ...]:
    """Evaluated criteria of ``plan``: ``(total_time_s, money, soh_total_pct)``
    restricted to ``criteria``."""
    values = {"time": plan.total_time_s, "money": plan.money, "wear": plan.soh_total_pct}
    return tuple(values[c] for c in criteria)


def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """Pareto dominance for minimisation: ``a <= b`` everywhere, ``<`` somewhere."""
    strict = False
    for x, y in zip(a, b, strict=True):
        if x > y:
            return False
        if x < y:
            strict = True
    return strict


# ======================================================================
# lower bounds on the completion of a label
# ======================================================================
class CompletionBounds:
    r"""Optimistic completion vector ``h(v, e) = (h_t, h_m, h_w)``.

    For a label at node ``v`` with energy ``e``, every feasible completion
    to the destination needs at least ``h_t`` more seconds, ``h_m`` more
    money and ``h_w`` more SoH percentage points (see the module docs for
    the formulas and the argument).  All three components are
    non-increasing in ``e`` and ``(inf, inf, inf)`` where the destination
    cannot be reached.

    Attributes are plain lists indexed by node so that the search loop can
    read them directly:

    ``time_lb``, ``energy_lb``
        from :class:`~evroute.search.LowerBounds`;
    ``e_need``
        ``energy_lb[v] + e_dest_min`` -- the deficit is ``max(0, e_need - e)``;
    ``wear_drive_lb``
        ``drive_soh_per_kwh`` times the reverse shortest positive consumption;
    ``sec_per_kwh``, ``money_per_kwh``, ``soh_per_kwh``
        best rates over the station bins intersecting the chargeable range
        ``[min(e_min, e_init), e_charge_cap]`` (0 without stations);
    ``stop_s``, ``stop_kwh``
        smallest overhead of one stop and most energy one stop can add.
    """

    def __init__(self, prepared: PreparedScenario, lower_bounds: LowerBounds | None = None) -> None:
        P = prepared
        lb = lower_bounds if lower_bounds is not None else LowerBounds(P)
        self.lower_bounds = lb
        self.time_lb: list[float] = lb.time_lb
        self.energy_lb: list[float] = lb.energy_lb
        goal = P.e_dest_min
        self.e_goal = goal
        self.e_need = [el + goal if el < INF else INF for el in lb.energy_lb]
        positive = [min(o[2] if o[2] > 0.0 else 0.0 for o in opts) for opts in P.edge_options]
        dist, _ = dijkstra(P.net, P.destination, positive, reverse=True)
        k = P.drive_soh_per_kwh
        self.wear_drive_lb = [k * d if d < INF else INF for d in dist]
        self.sec_per_kwh, self.money_per_kwh, self.soh_per_kwh = chargeable_rates(P)
        self.stop_s = min_stop_overhead_s(P)
        self.stop_kwh = max(P.e_charge_cap - min(P.e_min, P.e_init) + EPS, 0.0)

    def deficit(self, v: int, energy: float) -> float:
        """Energy that still has to be charged at least [kWh]."""
        d = self.e_need[v] - energy
        return d if d > 0.0 else 0.0

    def __call__(self, v: int, energy: float) -> tuple[float, float, float]:
        """``(h_t, h_m, h_w)`` at node ``v`` with ``energy`` kWh."""
        tl = self.time_lb[v]
        if tl == INF:
            return INF, INF, INF
        d = self.e_need[v] - energy
        if d <= 0.0:
            return tl, 0.0, self.wear_drive_lb[v]
        stops = math.ceil(d / self.stop_kwh) if self.stop_kwh > 0.0 else 1
        return (
            tl + d * self.sec_per_kwh + self.stop_s * stops,
            d * self.money_per_kwh,
            self.wear_drive_lb[v] + d * self.soh_per_kwh,
        )


def chargeable_rates(P: PreparedScenario) -> tuple[float, float, float]:
    """``(seconds, money, wear)`` per kWh at best over all stations and the SoC
    bins that intersect the chargeable range ``[min(e_min, e_init), e_charge_cap]``.

    Each rate is minimised separately (a lower bound for every criterion on
    its own).  ``(0, 0, 0)`` without stations.
    """
    lo = min(P.e_min, P.e_init) - EPS
    hi = P.e_charge_cap
    sec = money = soh = INF
    for f in P.cf:
        eb = f.e_breaks
        for i, pw in enumerate(f.power_kw):
            if eb[i + 1] <= lo or eb[i] >= hi:
                continue
            sec = min(sec, 3600.0 / pw)
            money = min(money, f.money_per_kwh[i])
            soh = min(soh, f.soh_pct_per_kwh[i])
    if sec == INF:
        return 0.0, 0.0, 0.0
    return sec, max(money, 0.0), max(soh, 0.0)


# ======================================================================
# dominance structures
# ======================================================================
def _stair_add_max(K: list[float], E: list[float], k: float, e: float) -> bool:
    """Add ``(k, e)`` to a staircase answering "is there ``k' <= k`` with
    ``e' >= e``?" (``K`` and ``E`` strictly increasing) unless it is
    dominated; remove the points it dominates.  Returns whether it was added."""
    j = bisect_right(K, k)
    if j and E[j - 1] >= e:
        return False
    if j == len(K):
        K.append(k)
        E.append(e)
        return True
    lo = bisect_left(K, k)
    hi = j
    n = len(K)
    while hi < n and E[hi] <= e:
        hi += 1
    K[lo:hi] = [k]
    E[lo:hi] = [e]
    return True


def _stair_add_min(M: list[float], W: list[float], m: float, w: float) -> bool:
    """Add ``(m, w)`` to a *minimum* staircase answering "is there ``m' <= m``
    with ``w' <= w``?" (``M`` strictly increasing, ``W`` strictly decreasing) unless it
    is dominated; remove the points it dominates.  Returns whether it was added."""
    j = bisect_right(M, m)
    if j and W[j - 1] <= w:
        return False
    if j == len(M):
        M.append(m)
        W.append(w)
        return True
    lo = bisect_left(M, m)
    hi = j
    n = len(M)
    while hi < n and W[hi] >= w:
        hi += 1
    M[lo:hi] = [m]
    W[lo:hi] = [w]
    return True


#: Energy bins of :class:`_Archive3` (a power of two).
_NB = 256
#: Size from which an archive switches from a scan to the binned Fenwick tree.
_BUILD = 64


class _Archive3:
    """Settled ``(money, wear, energy)`` points of one node: orthant queries.

    ``dominated(m, w, e)`` answers "is there a point with ``m' <= m``,
    ``w' <= w`` and ``e' >= e``?" (time is implicit, Lemma 2).  The engine
    first checks its (money, energy) staircase; the archive adds

    1. a (wear, energy) staircase -- a point not dominated in one projection
       is not dominated at all;
    2. a scan of all points (newest first) while the archive is small;
    3. beyond ``_BUILD`` points: the energy axis ``[0, capacity]`` is cut into
       ``_NB`` bins.  A Fenwick tree over the bins in *decreasing* energy
       order stores, per tree node, the minimum (money, wear) staircase of
       all points in its range, so the bins strictly above the query's bin
       cost ``log2(_NB)`` bisections.  Inside the query's own bin the points
       are grouped by exact energy (charging targets create large groups),
       each group a (money, wear) staircase, and only groups with
       ``e' >= e`` are inspected.

    Bin indices are a non-decreasing function of the energy, so a point in a
    higher bin has a strictly higher energy than the query; the result is
    exact.  Points are never removed (the settled sets only grow, Lemma 1);
    dominated points simply do not enter the staircases.
    """

    __slots__ = ("groups", "inv_w", "rows", "tree", "we", "ww")

    def __init__(self, inv_w: float) -> None:
        self.ww: list[float] = []
        self.we: list[float] = []
        self.rows: list[tuple[float, float, float]] = []
        self.tree: list[tuple[list[float], list[float]] | None] | None = None
        self.groups: dict[int, tuple[list[float], list[tuple[list[float], list[float]]]]] = {}
        self.inv_w = inv_w

    def _bin(self, e: float) -> int:
        b = int(e * self.inv_w)
        if b < 0:
            return 0
        return b if b < _NB else _NB - 1

    def dominated(self, m: float, w: float, e: float) -> bool:
        j = bisect_right(self.ww, w)
        if not j or self.we[j - 1] < e:
            return False
        tree = self.tree
        if tree is None:
            for m2, w2, e2 in reversed(self.rows):
                if m2 <= m and w2 <= w and e2 >= e:
                    return True
            return False
        b = self._bin(e)
        k = _NB - 1 - b  # Fenwick prefix = the bins with a higher energy
        while k > 0:
            nd = tree[k]
            if nd is not None:
                M, W = nd
                j = bisect_right(M, m)
                if j and W[j - 1] <= w:
                    return True
            k &= k - 1
        grp = self.groups.get(b)
        if grp is not None:
            keys, stairs = grp
            for k in range(bisect_right(keys, -e)):
                M, W = stairs[k]
                j = bisect_right(M, m)
                if j and W[j - 1] <= w:
                    return True
        return False

    def add(self, m: float, w: float, e: float) -> None:
        _stair_add_max(self.ww, self.we, w, e)
        if self.tree is None:
            rows = self.rows
            rows.append((m, w, e))
            if len(rows) >= _BUILD:
                self.tree = [None] * (_NB + 1)
                for r in rows:
                    self._insert(*r)
                self.rows = []
            return
        self._insert(m, w, e)

    def _insert(self, m: float, w: float, e: float) -> None:
        b = self._bin(e)
        grp = self.groups.get(b)
        if grp is None:
            self.groups[b] = ([-e], [([m], [w])])
        else:
            keys, stairs = grp
            k = bisect_left(keys, -e)
            if k < len(keys) and keys[k] == -e:
                M, W = stairs[k]
                _stair_add_min(M, W, m, w)
            else:
                keys.insert(k, -e)
                stairs.insert(k, ([m], [w]))
        tree = self.tree
        assert tree is not None
        k = _NB - b  # 1-based Fenwick position of bin b
        while k <= _NB:
            nd = tree[k]
            if nd is None:
                tree[k] = ([m], [w])
            elif not _stair_add_min(nd[0], nd[1], m, w):
                # dominated here, hence in every later node (their ranges
                # contain this node's range)
                break
            k += k & -k


# ======================================================================
# the engine
# ======================================================================
class _ParetoSearch:
    """Label store, destination front and counters of one search."""

    __slots__ = (
        "energy", "front", "generated", "how", "money", "n_levels", "node", "parent",
        "peak_queue", "pruned", "pruned_front", "pushes", "replaced", "settled", "status", "time", "wear",
    )
    time: list[float]
    money: list[float]
    wear: list[float]
    energy: list[float]
    node: list[int]
    parent: list[int]
    how: list[int]
    front: list[int]
    status: str
    settled: int
    generated: int
    pushes: int
    pruned: int
    pruned_front: int
    #: members that left the front for an equally fast, cheaper route (float ties)
    replaced: int
    peak_queue: int
    n_levels: int

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


def _pareto_search(
    P: PreparedScenario,
    bounds: CompletionBounds,
    *,
    use_wear: bool = True,
    targets: str = "grid+breaks",
    eps_money: float = 0.0,
    eps_wear: float = 0.0,
    eps_labels: bool = False,
    max_labels: int = 2_000_000,
    goal_directed: bool = True,
    trace: SearchTrace | None = None,
) -> _ParetoSearch:
    """Run the multi-criteria label-setting search (see the module docs).

    The heap key is ``(t + pot[v], m, w, -e)`` with ``pot = time_lb``
    (``goal_directed``) or ``pot = 0`` (pure Martins order).  The relative
    tolerances ``eps_money`` / ``eps_wear`` always apply to the destination
    tests and, with ``eps_labels``, also to the dominance tests at the nodes.
    """
    net = P.net
    n = net.num_nodes
    source, dest = P.origin, P.destination
    nlev = len(P.speed_factors)
    cap = P.capacity
    emin_eps = P.e_min - EPS
    egoal_eps = P.e_dest_min - EPS
    # destination tests: tolerance plus floating-point guard
    fm = (1.0 + eps_money) * (1.0 + FRONT_RTOL)
    fw = (1.0 + eps_wear) * (1.0 + FRONT_RTOL)
    # front maintenance: "c covers f" in money and wear up to rounding
    rf = 1.0 + FRONT_RTOL
    # node tests: exact unless eps_labels (multiplying by 1.0 is exact)
    nfm = 1.0 + eps_money if eps_labels else 1.0
    nfw = 1.0 + eps_wear if eps_labels else 1.0

    # optimistic completion: money and wear parts (time is implicit, Lemma 2)
    tlb = bounds.time_lb
    eneed = bounds.e_need
    hwd = bounds.wear_drive_lb if use_wear else [0.0] * n
    mrate = bounds.money_per_kwh
    srate = bounds.soh_per_kwh if use_wear else 0.0
    # node potential of the heap key: the same constant for every label of a
    # node, so the order *at* a node stays lexicographic in (t, m, w)
    pot = tlb if goal_directed else [0.0 if x < INF else INF for x in tlb]

    # ---------------------------------------------------- adjacency table
    # (head, tau, consumption, drive wear, how) per edge and speed level;
    # heads that cannot reach the destination are left out entirely
    head = net.head
    dsoh = P.drive_soh_per_kwh
    options = P.edge_options
    adj: list[tuple[tuple[int, float, float, float, int], ...]] = []
    for v in range(n):
        row = []
        for a in net.out_edges[v]:
            u = head[a]
            if tlb[u] == INF:
                continue
            base = a * nlev
            for lv, tau, c, _dc in options[a]:
                row.append((u, tau, c, dsoh * c if c > 0.0 else 0.0, base + lv))
        adj.append(tuple(row))

    # ------------------------------------------------- charging tables
    station_at = P.station_at
    is_station = P.is_station_node
    TG: list[list[float]] = []
    TY: list[list[float]] = []
    MY: list[list[float]] = []
    SY: list[list[float]] = []
    JMIN: list[int] = []
    for s in range(len(P.stations)):
        f = P.cf[s]
        tg = charging_targets(P, s, targets)
        TG.append(tg)
        TY.append([f.time_at(y) for y in tg])
        MY.append([f.money_at(y) for y in tg])
        SY.append([f.soh_at(y) for y in tg])
        # targets below the reserve lead to labels that can never move -- unless
        # another station of the node can raise the energy further
        alone = len(station_at[P.stations[s].node]) <= 1
        JMIN.append(bisect_left(tg, emin_eps) if alone else 0)
    cf = P.cf
    setup = P.setup_s
    wait = P.wait_s

    # ---------------------------------------------------- label store
    TT: list[float] = []
    MM: list[float] = []
    WW: list[float] = []
    EN: list[float] = []
    ND: list[int] = []
    PAR: list[int] = []
    HOW: list[int] = []
    TT_a, MM_a, WW_a, EN_a = TT.append, MM.append, WW.append, EN.append
    ND_a, PAR_a, HOW_a = ND.append, PAR.append, HOW.append

    # settled sets (one per node): (money, energy) staircase plus a 3-D archive
    SM: list[list[float]] = [[] for _ in range(n)]
    SE: list[list[float]] = [[] for _ in range(n)]
    inv_w = _NB / cap if cap > 0 else 0.0
    S3: list[Any] | None = [None] * n if use_wear else None

    # destination front: member label ids (in time order) and the minimum
    # (money, wear-key) staircase used by the front tests (Lemma 2)
    FR: list[int] = []
    FM: list[float] = []
    FW: list[float] = []

    heap: list[tuple[float, float, float, float, int]] = []
    heappush, heappop = heapq.heappush, heapq.heappop
    visit = trace.visit if trace is not None else None

    e0 = P.e_init
    TT_a(0.0)
    MM_a(0.0)
    WW_a(0.0)
    EN_a(e0)
    ND_a(source)
    PAR_a(-1)
    HOW_a(_ROOT)
    nl = 1
    pushes = 0
    if tlb[source] < INF:
        heappush(heap, (pot[source], 0.0, 0.0, -e0, 0))
        pushes = 1

    settled = generated = pruned = pruned_front = peak = replaced = 0
    status = "exhausted"

    while heap:
        lq = len(heap)
        if lq > peak:
            peak = lq
        if nl > max_labels:
            status = "limit"
            break
        key, m, k, _, i = heappop(heap)
        t = TT[i]
        v = ND[i]
        x = EN[i]

        # ----------------------------------------------- complete route
        if v == dest and x >= egoal_eps:
            j = bisect_right(FM, m * fm)
            if j and FW[j - 1] <= k * fw:
                pruned_front += 1  # covered by a member (popped no later)
                continue
            # members it covers (equally fast up to TIE_RTOL, no dearer and no more
            # wear up to FRONT_RTOL) leave the front -- only possible on float ties
            # (module docs, "Floating point"); members are in pop order, which is
            # time order up to a few ulps, so only the tail needs a look
            tie = TIE_RTOL * abs(t)
            t_lo = t - 4.0 * tie
            mc = m / rf
            kc = k / rf
            q = len(FR) - 1
            drop = []
            while q >= 0:
                f = FR[q]
                tf = TT[f]
                if tf < t_lo:
                    break
                if t <= tf + tie and mc <= MM[f] and kc <= (WW[f] if use_wear else 0.0):
                    drop.append(q)
                q -= 1
            if drop:
                for q in drop:  # (descending positions)
                    del FR[q]
                FM, FW = [], []
                for f in FR:
                    _stair_add_min(FM, FW, MM[f], WW[f] if use_wear else 0.0)
                replaced += len(drop)
            FR.append(i)
            _stair_add_min(FM, FW, m, k)
            settled += 1
            if visit is not None:
                visit(v, key)
            continue

        # ---------------------------- target pruning (the front grows)
        if FM:
            d = eneed[v] - x
            if d > 0.0:
                j = bisect_right(FM, (m + d * mrate) * fm)
                if j and FW[j - 1] <= (k + hwd[v] + d * srate) * fw:
                    pruned_front += 1
                    continue
            else:
                j = bisect_right(FM, m * fm)
                if j and FW[j - 1] <= (k + hwd[v]) * fw:
                    pruned_front += 1
                    continue

        # ------------------------------ dominance by settled labels at v
        mq = m * nfm  # also the money query of every drive successor
        Ms = SM[v]
        if Ms:
            j = bisect_right(Ms, mq)
            if j and SE[v][j - 1] >= x and (S3 is None or S3[v].dominated(mq, k * nfw, x)):
                pruned += 1
                continue
        _stair_add_max(Ms, SE[v], m, x)
        if S3 is not None:
            a3 = S3[v]
            if a3 is None:
                a3 = S3[v] = _Archive3(inv_w)
            a3.add(m, k, x)
        settled += 1
        if visit is not None:
            visit(v, key)
        w = WW[i]

        # ------------------------------------------------------- drive
        if x >= emin_eps:
            for u, tau, c, dw, code in adj[v]:
                ne = x - c
                if ne > cap:
                    ne = cap
                elif ne < emin_eps:
                    continue
                generated += 1
                nw = w + dw
                nk = nw if use_wear else 0.0
                Ms = SM[u]
                if Ms:
                    j = bisect_right(Ms, mq)
                    if j and SE[u][j - 1] >= ne and (S3 is None or S3[u].dominated(mq, nk * nfw, ne)):
                        pruned += 1
                        continue
                if FM:
                    d = eneed[u] - ne
                    if d > 0.0:
                        j = bisect_right(FM, (m + d * mrate) * fm)
                        if j and FW[j - 1] <= (nk + hwd[u] + d * srate) * fw:
                            pruned_front += 1
                            continue
                    else:
                        j = bisect_right(FM, m * fm)
                        if j and FW[j - 1] <= (nk + hwd[u]) * fw:
                            pruned_front += 1
                            continue
                nt = t + tau
                TT_a(nt)
                MM_a(m)
                WW_a(nw)
                EN_a(ne)
                ND_a(u)
                PAR_a(i)
                HOW_a(code)
                heappush(heap, (nt + pot[u], m, nk, -ne, nl))
                nl += 1
                pushes += 1

        # ------------------------------------------------------ charge
        if is_station[v]:
            last = -2 - HOW[i]  # station of the stop that created this label (< 0: none)
            hv = hwd[v]
            env = eneed[v]
            pv = pot[v]
            Ms, Es = SM[v], SE[v]
            A3 = S3[v] if S3 is not None else None
            for s in station_at[v]:
                if s == last:
                    continue  # never the same station twice in a row (module docs)
                tg = TG[s]
                L = len(tg)
                j0 = bisect_right(tg, x + EPS)
                if j0 < JMIN[s]:
                    j0 = JMIN[s]
                if j0 >= L:
                    continue
                f = cf[s]
                ty, my, sy = TY[s], MY[s], SY[s]
                ws = wait(s, t) + setup[s]
                fx = f.time_at(x)
                mx = f.money_at(x)
                sx = f.soh_at(x)
                for jj in range(j0, L):
                    y = tg[jj]
                    # same arithmetic as evaluate(): money += M(y) - M(x)
                    nm = m + (my[jj] - mx)
                    nw = w + (sy[jj] - sx)
                    nk = nw if use_wear else 0.0
                    generated += 1
                    nmq = nm * nfm
                    j = bisect_right(Ms, nmq)
                    if j and Es[j - 1] >= y and (A3 is None or A3.dominated(nmq, nk * nfw, y)):
                        pruned += 1
                        continue
                    if FM:
                        d = env - y
                        if d > 0.0:
                            j = bisect_right(FM, (nm + d * mrate) * fm)
                            if j and FW[j - 1] <= (nk + hv + d * srate) * fw:
                                pruned_front += 1
                                continue
                        else:
                            j = bisect_right(FM, nm * fm)
                            if j and FW[j - 1] <= (nk + hv) * fw:
                                pruned_front += 1
                                continue
                    # same arithmetic as evaluate(): t += wait + setup + charge
                    nt = t + (ws + (ty[jj] - fx))
                    TT_a(nt)
                    MM_a(nm)
                    WW_a(nw)
                    EN_a(y)
                    ND_a(v)
                    PAR_a(i)
                    HOW_a(-2 - s)
                    heappush(heap, (nt + pv, nm, nk, -y, nl))
                    nl += 1
                    pushes += 1

    res = _ParetoSearch()
    res.time, res.money, res.wear, res.energy = TT, MM, WW, EN
    res.node, res.parent, res.how = ND, PAR, HOW
    res.front = FR
    res.status = status
    res.settled, res.generated, res.pushes = settled, generated, pushes
    res.pruned, res.pruned_front = pruned, pruned_front
    res.replaced = replaced
    res.peak_queue = peak
    res.n_levels = nlev
    return res


# ======================================================================
# options
# ======================================================================
def _parse_criteria(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [c.strip() for c in value.replace("+", ",").split(",") if c.strip()]
    crit = tuple(value)
    if crit not in CRITERIA_SETS:
        raise ValueError(f"unknown criteria {crit!r}; choose from {CRITERIA_SETS}")
    return crit


def _parse_epsilon(value: Any, criteria: tuple[str, ...]) -> dict[str, float]:
    """Relative tolerance per criterion from a number, a sequence (one value
    per criterion, in order) or a mapping ``{criterion: value}``."""
    if value is None:
        value = 0.0
    if isinstance(value, Mapping):
        unknown = set(value) - set(criteria)
        if unknown:
            raise ValueError(f"epsilon given for unknown criteria {sorted(unknown)}")
        eps = {c: float(value.get(c, 0.0)) for c in criteria}
    elif isinstance(value, Sequence) and not isinstance(value, str):
        if len(value) != len(criteria):
            raise ValueError(f"epsilon needs one value per criterion {criteria}")
        eps = {c: float(x) for c, x in zip(criteria, value, strict=True)}
    else:
        eps = {c: float(value) for c in criteria}
    for c, x in eps.items():
        if not (x >= 0.0 and math.isfinite(x)):
            raise ValueError(f"epsilon for {c!r} must be a finite number >= 0, got {x!r}")
    return eps


def _validate_model(P: PreparedScenario) -> None:
    """The lexicographic search needs non-negative increments in every criterion."""
    for s, f in enumerate(P.cf):
        if min(f.money_per_kwh) < 0.0 or min(f.soh_pct_per_kwh) < 0.0:
            raise ValueError(f"station {s}: negative price or wear rate is not supported")
        if min(P.wait_profiles[s].waits_s) + P.setup_s[s] < 0.0:
            raise ValueError(f"station {s}: negative wait + setup time is not supported")
    if P.drive_soh_per_kwh < 0.0:
        raise ValueError("negative driving wear is not supported")


# ======================================================================
# planner
# ======================================================================
class ParetoPlanner(Planner):
    """Multi-criteria label setting: Pareto front of (time, money[, wear]).

    Options
    -------
    ``criteria``
        ``("time", "money", "wear")`` (default) or ``("time", "money")``
        (also accepted as a comma-separated string).
    ``targets``
        ``"grid+breaks"`` (default) or ``"grid"`` -- charging targets as in
        :func:`~evroute.algorithms.labels.charging_targets`.
    ``epsilon``
        relative tolerance (default ``0.0``: exact): a number for all
        criteria, one value per criterion or a mapping such as
        ``{"money": 0.01, "wear": 0.05}``.  Time is the lexicographic key and
        always exact.
    ``epsilon_scope``
        ``"front"`` (default): the tolerance applies to the destination tests
        only and every member of the exact front stays covered within it;
        ``"labels"``: also to the node dominance tests -- much faster on
        large instances, coverage may compound along a route (module docs).
    ``goal_directed``
        ``True`` (default): heap key ``t + T_lb(v)``; ``False``: plain
        Martins order.  Same front, the potential needs far fewer labels.
    ``max_labels``
        safeguard on the number of created labels (default 2 000 000, a few
        hundred bytes each).  When it is hit, the members found so far --
        the fastest part of the front -- are returned and
        ``stats.extra["complete"]`` is ``False``.

    The returned plan is the member with the smallest generalised cost
    (scenario objective ``"cost"``) or trip time (``"time"``);
    ``plan.alternatives`` holds every evaluated front member sorted by time.
    """

    name = "pareto"
    OPTIONS = OPTIONS
    title = "Pareto label setting"
    exact = "grid"
    objectives = ("time", "cost")
    description = (
        "Martins-style multi-criteria label setting on the battery-expanded graph: "
        "the Pareto front of trip time, charging money and battery wear (SoH loss), "
        "with NAMOA*-style target pruning and optional epsilon-dominance; charging "
        "targets on the SoC grid plus charging-curve breakpoints."
    )

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        opts = self.options
        unknown = set(opts) - set(OPTIONS)
        if unknown:
            raise ValueError(f"unknown option(s) {sorted(unknown)} for {self.name!r}; choose from {OPTIONS}")
        criteria = _parse_criteria(opts.get("criteria", CRITERIA))
        targets = opts.get("targets", "grid+breaks")
        if targets not in TARGET_MODES:
            raise ValueError(f"unknown targets mode {targets!r}; choose from {TARGET_MODES}")
        eps = _parse_epsilon(opts.get("epsilon", 0.0), criteria)
        scope = opts.get("epsilon_scope", "front")
        if scope not in EPSILON_SCOPES:
            raise ValueError(f"unknown epsilon_scope {scope!r}; choose from {EPSILON_SCOPES}")
        max_labels = int(opts.get("max_labels", 2_000_000))
        goal_directed = bool(opts.get("goal_directed", True))
        if P.objective not in ("time", "cost"):
            raise ValueError(f"unknown objective {P.objective!r}")
        _validate_model(P)
        use_wear = "wear" in criteria
        tr = SearchTrace() if trace else None

        t0 = time.perf_counter()
        bounds = CompletionBounds(P)
        t1 = time.perf_counter()
        res = _pareto_search(
            P,
            bounds,
            use_wear=use_wear,
            targets=targets,
            eps_money=eps["money"],
            eps_wear=eps.get("wear", 0.0),
            eps_labels=scope == "labels",
            max_labels=max_labels,
            goal_directed=goal_directed,
            trace=tr,
        )
        t2 = time.perf_counter()
        TT, MM, WW, EN = res.time, res.money, res.wear, res.energy
        stats = SearchStats(
            algorithm=self.name,
            settled=res.settled,
            generated=res.generated,
            pushes=res.pushes,
            pruned=res.pruned + res.pruned_front,
            peak_queue=res.peak_queue,
            extra={
                "front_size": len(res.front),
                "criteria": list(criteria),
                "epsilon": eps,
                "epsilon_scope": scope,
                "goal_directed": goal_directed,
                "targets": targets,
                "status": res.status,
                "complete": res.status == "exhausted",
                "labels": len(res.node),
                "pruned_by_dominance": res.pruned,
                "pruned_by_front": res.pruned_front,
                "front_replaced": res.replaced,
                "front": [[TT[i], MM[i], WW[i], EN[i]] for i in res.front],
                "bounds_s": t1 - t0,
                "search_s": t2 - t1,
            },
        )
        if not res.front:
            return infeasible_plan(P, self.name, _infeasibility_reason(P, res, bounds, max_labels), stats, tr)

        time_obj = P.objective == "time"
        vot, wm, wd = P.vot_per_s, P.w_money, P.w_deg
        members: list[RoutePlan] = []
        for i in res.front:
            edges, levels, charges = res.reconstruct(i)
            internal = TT[i] if time_obj else vot * TT[i] + wm * MM[i] + wd * WW[i]
            members.append(
                evaluate(P, PlanDecision(edges, levels, charges), algorithm=self.name, internal_objective=internal)
            )
        members.sort(key=lambda p: (p.total_time_s, p.money, p.soh_total_pct))
        sel = min(
            range(len(members)),
            key=lambda j: (members[j].objective_value, members[j].total_time_s, members[j].money),
        )
        best = members[sel]
        stats.extra["selected"] = sel
        # a distinct object, so that the returned plan is not its own alternative
        plan = evaluate(
            P,
            best.decision,  # type: ignore[arg-type]
            algorithm=self.name,
            internal_objective=best.internal_objective,
            stats=stats,
            trace=tr,
        )
        plan.alternatives = members
        return plan


def pareto_front(prepared: PreparedScenario | Scenario, **options: Any) -> list[RoutePlan]:
    """Evaluated Pareto-optimal routes sorted by trip time (``[]`` if none).

    Convenience wrapper around :class:`ParetoPlanner`; ``options`` as there.
    """
    plan = ParetoPlanner(**options).plan(prepared)
    return list(plan.alternatives) if plan.feasible else []


# ======================================================================
# diagnostics
# ======================================================================
def _infeasibility_reason(
    P: PreparedScenario, res: _ParetoSearch, bounds: CompletionBounds, max_labels: int
) -> str:
    o, d = P.origin, P.destination
    if res.status == "limit":
        return (
            f"label limit reached (max_labels={max_labels:,}, {len(res.node):,} labels created) "
            "before the first Pareto-optimal route was found; increase max_labels, use "
            "epsilon > 0 with epsilon_scope='labels', criteria=('time', 'money'), a coarser "
            "charge_step_soc or targets='grid'"
        )
    if bounds.time_lb[o] == INF:
        return f"destination {d} is not reachable from origin {o} in the road network"
    if P.e_init < P.e_min - EPS and not P.is_station_node[o]:
        return (
            f"initial energy {P.e_init:.2f} kWh is below the reserve {P.e_min:.2f} kWh "
            "and there is no charging station at the origin"
        )
    return (
        f"no battery-feasible route from {o} to {d}: the charging infrastructure "
        f"({len(P.stations)} station(s)) cannot bridge the energy gaps "
        f"(start {P.e_init:.2f} kWh, reserve {P.e_min:.2f} kWh, required on arrival "
        f"{P.e_dest_min:.2f} kWh, max charge {P.e_charge_cap:.2f} kWh)"
    )
