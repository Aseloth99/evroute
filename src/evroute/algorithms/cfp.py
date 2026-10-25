r"""Charging Function Propagation (CFP): exact fastest paths with continuous charging.

This module implements

* :class:`CFPPlanner` (``"cfp"``) -- label setting on *charging functions*,
* :class:`CFPAStarPlanner` (``"cfp-astar"``) -- the same search guided by a
  consistent energy-deficit A* potential.

Both return a **fastest feasible route when the amount of energy charged at
every stop may be any real number** (up to ``P.e_charge_cap``), for the exact
piecewise-linear charging functions of the scenario.  The BLS family
(:mod:`evroute.algorithms.labels`) restricts the charging target to a finite
set; CFP removes that restriction without discretising anything, so its
optimum is a lower bound for the BLS optimum and the latter converges to it as
``charge_step_soc`` shrinks.  The approach follows Baum, Dibbelt, Gemsa,
Wagner and Zuendorf (2015, 2019) and Baum, Dibbelt, Wagner and Zuendorf (2017).
The formulation, the proofs below and three additions are this
implementation's own: the exact candidate set for stop times (section 3, which
also covers non-concave charging functions and time-dependent waits), the
energy cap (section 4) and the A* keys (section 4).

CFP is a **time-objective** algorithm: with ``P.objective == "cost"`` it still
minimises the trip time (the evaluator reports the cost of the resulting
plan), and its potential and energy cap are always built for the trip time
(``objective="time"`` of :meth:`~evroute.algorithms.labels.AStarPotential.tight`
and :func:`~evroute.algorithms.labels.completion_energy`).  Use BLS / BLS-A*
or the Pareto planner for the generalised cost.

Notation
========

Energies are in kWh, times in seconds of trip time.  ``Q`` is the battery
capacity, ``e_min`` the reserve, ``cap = P.e_charge_cap`` the highest charging
target, ``e_goal = P.e_dest_min``.  For station ``s``, ``F_s`` is the time to
charge from empty to a given energy (``cf.time_at``) and ``F_s^{-1}`` its inverse
(``cf.energy_at``); both are exact, increasing and piecewise linear.  A stop
at ``s`` that starts at trip time ``t`` costs ``w_s(t) + u_s`` seconds of
waiting and setup before charging begins.

1. Labels are functions
=======================

A CFP label lives at a node ``v`` and stands for a *whole family* of partial
routes -- all those that differ only in how long the vehicle charged at its
**last station** ``s`` -- not for a single route.  It consists of

``s``     last station used (``-1``: no stop since the origin),
``t0``    trip time at ``v`` if no energy beyond ``b`` is charged at ``s`` (the
          wait and setup of the stop are already included),
``b``     energy when charging at ``s`` starts (``e_init`` for ``s = -1``),
``(inn, cost, out)``  the *consumption profile* of the path from ``s`` to ``v``.

The profile says: departing ``s`` with energy ``x >= inn`` yields energy
``f(x) = min(x - cost, out)`` at ``v`` (``-inf`` if ``x < inn``).  After
charging until trip time ``t`` the departure energy is

.. math:: x(t) = \min\bigl(F_s^{-1}(F_s(b) + t - t_0),\ \max(cap, b)\bigr)
   \quad (t \ge t_0), \qquad x(t) \equiv e_{init} \text{ for } s = -1,

and the label's *function* ``B(t) = f(x(t))`` is the energy with which the
route arrives at (and can leave) ``v`` at trip time ``t``.  ``B`` is piecewise
linear and non-decreasing (it is also concave when the charging power does not
increase with the state of charge, but nothing below needs that).  It is
defined from the *feasible time* ``t_feas``, the earliest ``t`` with
``x(t) >= inn`` (``t0`` if ``b >= inn``, else ``t0 + F_s(inn) - F_s(b)``, never
if ``inn`` exceeds ``max(cap, b)``).  The origin label is ``s = -1``,
``t0 = 0``, ``b = e_init`` with the identity profile ``(e_min, 0, Q)``.

Only ``O(#bins)`` numbers ever have to be stored: the *breakpoints* of ``B``
are ``t_feas``; the times at which ``x(t)`` crosses an energy breakpoint of
``F_s`` strictly between ``max(b, inn)`` and the top of the range; and the time
at which ``B`` stops increasing (``x`` reaches ``min(cap, out + cost)``).
Between breakpoints ``B`` is linear and after the last one it is constant.
Because the breakpoints are recomputed from the *exact* tables of ``F_s`` and
from ``(t0, b, inn, cost, out)``, no interpolation error accumulates along a
path.

2. Edge extension (propagation along an edge)
=============================================

Driving an edge with time ``tau`` and energy ``c`` (possibly negative:
regeneration, clamped at ``Q``) maps ``y`` to ``min(y - c, Q)`` and requires
the energy ``y`` at the tail to satisfy ``y >= e_min + c`` (reserve at the
head).  Composing with ``f`` gives, for ``x >= inn`` with ``f(x) >= e_min + c``,

.. math:: \min(\min(x - cost, out) - c,\ Q) = \min(x - (cost + c),\ \min(out - c, Q)),

i.e. the label of the head has ``t0' = t0 + tau``, the same ``s`` and ``b`` and

    ``inn' = max(inn, e_min + cost + c)``, ``cost' = cost + c``,
    ``out' = min(out - c, Q)``,

and the extension is infeasible iff ``out' < e_min`` or ``inn'`` exceeds the
largest reachable ``x`` (``max(cap, b)``).  The reserve is therefore
respected at *every* node of the path for *every* admissible charging amount,
and ``B'(t) = min(B(t - tau) - c, Q)`` -- the new function is a shifted,
clipped copy of the old one, so dominance between labels is preserved
(everything below is monotone and commutes with pointwise maxima).

3. Stops: switching at breakpoints suffices
===========================================

A label ``L`` at a station node ``v`` spawns, for every station ``s2`` at
``v`` and every *switch time* ``tj`` from a finite candidate set, the label

    ``(v, s2, t0 = tj + w_{s2}(tj) + u_{s2}, b = B_L(tj), identity profile)``,

"stop charging at ``s`` at time ``tj``, drive on, and charge at ``s2``".  A
label created this way at ``v`` is *charged here* and spawns no further stop at
``v`` (one stop per visit of a node, exactly as in BLS: two consecutive stops
would pay waiting and setup twice).  Targets are limited by ``cap``: switch times
with ``B_L(tj) >= cap`` are useless and skipped.

**Lemma 1 (candidate switch times).**  Fix a final trip time ``tau`` (the time
at which the vehicle leaves ``v``).  Switching at ``t`` yields the energy
``Phi(t) = min(F_2^{-1}(F_2(B(t)) + tau - t - w(t) - u), cap)``
(``F_2 = F_{s2}``, ``w = w_{s2}``), for ``t_feas <= t <= tau - w(t) - u``.
Suppose the waits are static (``w`` constant) and the power of ``s2`` does not
increase with the energy.  Then the maximum of ``Phi`` over all admissible
``t`` is attained at a breakpoint of ``B`` -- or by not stopping at ``s2`` at
all.

*Proof.*  Consider one linear piece of ``B``, ``B(t) = B_0 + beta (t - t_a)``,
``beta >= 0`` in kWh/s.  Put ``u(t) = F_2(B(t)) + tau - t - w - u``.  Where ``F_2`` is
differentiable, ``u'(t) = beta / q(B(t)) - 1`` with ``q = p_{s2} / 3600`` the
charging rate [kWh/s] of ``s2`` at the energy ``B(t)``.  Along the piece ``B``
does not decrease, ``q`` does not increase by assumption, so ``u'`` is
non-decreasing: the sign of ``u'`` changes at most once, from negative to
positive.  ``F_2^{-1}`` is increasing, so ``Phi = min(F_2^{-1} \circ u, cap)``
is decreasing, then increasing (then flat) -- it is *quasi-convex* on the piece
and attains its maximum at an endpoint of the piece.  The pieces' endpoints are
breakpoints, except possibly the largest admissible time ``t = tau - w - u``,
where no energy is added at ``s2``; that option is dominated by ``L`` itself,
which leaves ``v`` at ``tau - w - u <= tau`` with the same energy (``B`` is
non-decreasing).  Beyond the last breakpoint ``B`` is constant, ``u' = -1``,
and the left endpoint (a breakpoint) is best.  :math:`\square`

The proof also shows what is needed in general.  Without the assumptions
``u'`` is ``beta / q(B(t)) - 1 - w'(t)``, and it changes sign more often only

* where ``B(t)`` crosses an energy at which the power of ``s2`` **increases**
  (``q`` jumps up, ``u'`` jumps down -- a local maximum of ``Phi``), e.g. the
  ramp-up of the charging curve of many vehicles at low state of charge
  (the ``sedan75`` preset ramps up until 15 % SoC), or
* at the knots of a time-dependent wait profile (``w'`` jumps).

Inside the pieces obtained by cutting at these points the argument above
applies again (``q`` is non-increasing, ``w'`` constant; for the wait
profile FIFO, ``w' >= -1``, gives ``u' <= 0`` where ``B`` is constant), so

    **candidate switch times = breakpoints of ``B`` + crossings of the upward
    power breaks of ``s2`` + wait-profile knots inside the active range.**

With the default option ``exact_switching=True`` all three are generated; the
last two sets are empty in the textbook case (concave charging functions,
static waits), where the algorithm coincides with the one of Baum et al.  The
test-suite checks the lemma, and the necessity of both additions, against a
dense scan of all switch times.

*Sibling pruning.*  Candidates of one parent for the same ``s2`` create
labels whose energy is ``F_2^{-1}(t - kappa)`` with
``kappa = t0 - F_2(b)``; a candidate with an earlier or equal ``t0`` and a
smaller or equal ``kappa`` dominates the later one outright.  Only candidates
whose ``kappa`` strictly improves on all earlier ones are created.

4. Queue keys and dominance
===========================

*Key.*  The key of a label is ``t_feas`` (``"cfp"``) or
``t_feas + h(v, B(t_feas))`` (``"cfp-astar"``) with a potential
``h(v, e) = base(v) + r * max(0, E(v) - e)``, non-increasing in ``e``, that is
consistent on drive edges.  Two are available (option ``potential``):
``"basic"`` is :meth:`~evroute.search.LowerBounds.h_time` -- ``base`` the
fastest driving time ``T_lb(v)``, ``E(v) = E_lb(v) + e_goal`` the least energy
needed, ``r = 3600 / P_max`` -- and ``"tight"`` (default) is the coupled bound
of :meth:`~evroute.algorithms.labels.AStarPotential.tight` without its
stop-count term: ``E(v)`` is measured on the *same* path and speed level as
the driving time and ``r`` is the best charging rate in the usable energy
range, so ``r * p(x) / 3600 <= 1`` for every power ``p(x)`` that occurs.

**Lemma 2.**  ``t + h(v, B(t))`` is non-decreasing in ``t``, so the key is the
minimum of ``t + h`` over the whole label, hence a lower bound for every route
the label stands for, and keys never decrease along edge extensions or stops.

*Proof.*  ``dB/dt <= dx/dt = p(x) / 3600`` and ``r * p(x) / 3600 <= 1`` (see
above; for the basic potential because power never exceeds ``P.p_max``), while
``h(v, .)`` is non-increasing with slope at least ``-r``.  Hence
``d/dt (t + h(v, B(t))) >= 1 - 1 = 0`` where the energy term is active and
``= 1`` elsewhere.  (The stop-count term of the BLS potential is a step
function of the energy; it would make ``t + h`` jump down inside a label, so
it is not used here.)  For an edge extension,
``t_feas' + h(w, B'(t_feas')) = \min_{t'} [t' + tau + h(w, \min(B(t') - c, Q))]
>= \min_{t'} [t' + h(v, B(t'))]`` by consistency of ``h`` on drive edges (Theorem 2
in ``docs/algorithms.md``; the docs of :class:`~evroute.algorithms.labels.AStarPotential`
for the coupled bound); for a stop the key grows by the wait and setup time
``>= 0``.  :math:`\square`

*Dominance.*  ``L1`` dominates ``L2`` at the same node iff
``t_feas(L1) <= t_feas(L2)`` and ``B1(t) >= B2(t)`` for all ``t >= t_feas(L2)``.
Both functions are piecewise linear, so ``B1 - B2`` is linear between the
union of their breakpoints and constant after the last one: it suffices to
compare at the breakpoints of both labels in ``[t_feas(L2), inf)`` (the limit
at infinity is covered by the last breakpoint).  A tolerance of ``eps``
(1e-9 kWh / s) makes ties dominate, so duplicates and zero-gain cycles die.
By Lemma 2 a dominating label has a key no larger than the dominated one, so
it is settled first; a popped label that is dominated by an already settled
label at its node can be discarded.

*Energy cap.*  Let ``need[v]`` be the energy that suffices to drive from ``v``
to the destination along a fastest path without charging
(:func:`~evroute.algorithms.labels.completion_energy`; ``inf`` if no battery
level suffices).  With ``need[v]`` kWh or more at trip time ``t`` the vehicle
reaches the destination at ``t + T_lb(v)``, and no route from ``v`` is faster,
so for the *time* objective every energy above ``need[v]`` is as good as
``need[v]``.  Labels are therefore compared, stored and expanded into stops as
``min(B, need[v])``: ``L1`` dominates ``L2`` iff ``t_feas(L1) <= t_feas(L2)``
and ``min(B1, need) >= min(B2, need)`` on ``[t_feas(L2), inf)``, and no stop
candidates with ``B >= need[v]`` are generated.  *Soundness:* take a
completion of ``L2`` that leaves ``v`` at time ``t`` with ``y = B2(t)``.  If
``y <= need`` then ``B1(t) >= y`` and ``L1`` imitates it (monotonicity).  If
``y > need`` the completion needs at least ``T_lb(v)`` more seconds, while
``L1``, holding at least ``need`` at ``t``, finishes after exactly
``T_lb(v)``.  (The keys are computed from the uncapped energies; they stay
valid lower bounds.)  The cap collapses all labels that already carry "enough"
energy to the earliest one per node; with several speed levels, where the
Pareto sets of incomparable labels grow quickly, it reduces the number of
labels by an order of magnitude.

*One stop per visit.*  At a node with a **single** station a label is compared
with all settled labels (free or charged): if a charged label ``A`` dominates a
free label ``B``, every stop ``B`` could still make is dominated by continuing
to charge at the same station from ``A`` (the switch time and the wait and
setup overhead can only hurt).  At a node with **several** stations free
labels are compared with free labels only and charged labels with both, as in
BLS (``A`` cannot stop at *another* station of the node).

5. Destination and optimality
=============================

When a label at the destination is generated, ``t_arr`` -- the least
``t >= t_feas`` with ``B(t) >= e_goal`` -- is computed in closed form (via
``F_s`` and ``F_s^{-1}``; ``inf`` if impossible) and the best such value is kept
as the incumbent.  This is the "final entry" of the textbook formulation, kept
outside of the heap: a label whose key is not smaller than the incumbent is
neither pushed nor expanded, and the search stops when the smallest key in the
heap reaches the incumbent.  Labels at the destination are expanded like any
other (the destination may host a faster station, or a detour may pay off).

**Theorem.**  Let the waits be static, or let ``exact_switching`` be on and the
waits FIFO.  Then the incumbent at termination is the least trip time over all
feasible routes with at most one stop per node visit and arbitrary charging
amounts ``<= cap``.

*Sketch.*  (i) The label function describes exactly the set of
``(time, energy)`` pairs of its route family, extension (section 2) and stops
(section 3) map families to families, and by Lemma 1 the finitely many labels
created at the candidate times have the same upper envelope as the continuum of
all switch times, for every final time.  (ii) Everything is monotone (arriving
earlier with more energy is never worse -- FIFO for the waits), so a label
dominated by a settled label can be dropped together with all its descendants
(section 4, with energies above ``need[v]`` identified).  If ``L2`` is
dropped, any route through it is replaced by one through the dominating
settled label ``L1`` that is no slower; ``L1``'s own extensions and stops were
generated when it was settled, so by induction along the route the
replacement reaches the destination, where the closed-form ``t_arr`` of the
last label yields a trip time no larger.  (iii) By Lemma 2 keys are lower
bounds that never decrease along extensions, so when the heap minimum reaches
the incumbent no unexpanded label can lead to a better route.  Hence the
incumbent is optimal.  Termination: energies are bounded, every cycle takes
positive time and does not raise the energy, so cycle labels are dominated.
:math:`\square`

The dominance and feasibility tests use a tolerance ``eps`` (1e-9 kWh / s), so
the returned time may exceed the exact optimum by a few times ``eps`` times the
seconds per kWh of charging (far below a microsecond); the objective of the
returned plan is nevertheless exactly what the evaluator measures.

With time-dependent waits and ``exact_switching=False`` plans are still
feasible and the objective still equals the evaluator's (waits are evaluated at
the concrete switch time), but optimality is not guaranteed.

Implementation notes
====================

* A heap entry *is* the label: ``(key, seq, v, s, t0, Fb, b, inn, cost, out,
  parent, how, switch_time)`` with ``Fb = F_s(b)``; dominated labels vanish
  when popped.  Only settled labels are stored: a record per label for the
  parent chain, and per node a :class:`_SettledSet` for the dominance test --
  constant labels in a staircase (one bisection), the others sorted by
  ``B_max`` behind two scalar pre-filters and a first-breakpoint test before
  the full breakpoint comparison.
* Drive labels that a settled constant label, or a constant label pushed
  earlier, dominates are dropped before they are pushed, and with several
  speed levels a search restricted to one level per edge first provides an
  upper bound on the optimum (labels with a larger key are never pushed).
  Neither changes the settled labels or the result (see :func:`_search`).
* ``how`` is ``edge * n_levels + level`` for a drive, ``-2 - station`` for a
  stop and ``-1`` for the root.
* Reconstruction walks the parent chain from the incumbent with its time
  ``t*``: the departure energy at the last station is ``x(t*)``; the previous
  station's target is ``x`` at the stored switch time, and so on.  A stop that
  adds nothing is kept (the evaluator charges its wait and setup, and so does
  the label), so the internal objective equals the evaluated trip time up to
  float rounding.
* The potentials and ``need`` come from :mod:`evroute.search` and
  :mod:`evroute.algorithms.labels` (:class:`~evroute.algorithms.labels.AStarPotential`,
  :func:`~evroute.algorithms.labels.completion_energy`); nodes that cannot
  reach the destination are never entered.
* Cost: a popped non-constant label may still be compared with many settled
  labels of its node (those passing the scalar pre-filters), so the run time
  can grow quadratically with the number of such labels per node.  The energy
  cap and the A* key keep those sets small; on large networks or with several
  speed levels use ``"cfp-astar"`` (identical optimum).  With several speed
  levels the number of labels can still grow very large (every edge trades
  time for energy); ``max_labels`` is the safeguard.
* The pure functions :func:`label_function`, :func:`arrival_time`,
  :func:`dominates`, :func:`switch_candidates` and :func:`evaluate_function`
  are public so that the mathematics can be tested in isolation.
* Options: ``max_labels`` (default 3 000 000; safeguard on the number of
  pushed labels -- if it is hit the best plan found so far, if any, is
  returned and ``stats.extra["status"]`` says so), ``eps`` (1e-9),
  ``exact_switching`` (default ``True``), and for ``"cfp-astar"`` ``potential``
  (``"tight"`` default, or ``"basic"``).

References
==========

* M. Baum, J. Dibbelt, A. Gemsa, D. Wagner, T. Zuendorf (2015).  Shortest
  feasible paths with charging stops for battery electric vehicles.  *Proc.
  23rd ACM SIGSPATIAL International Conference on Advances in Geographic
  Information Systems*; extended version (2019) in *Transportation Science*
  53(6), 1627-1655.
* M. Baum, J. Dibbelt, D. Wagner, T. Zuendorf (2017).  Modeling and engineering
  constrained shortest path algorithms for battery electric vehicles.  *Proc.
  25th European Symposium on Algorithms (ESA 2017)*, LIPIcs 87, 11:1-11:16.
* P. E. Hart, N. J. Nilsson, B. Raphael (1968).  A formal basis for the
  heuristic determination of minimum cost paths.  *IEEE Transactions on Systems
  Science and Cybernetics* 4(2), 100-107.
* A. Orda, R. Rom (1990).  Shortest-path and minimum-delay algorithms in
  networks with time-dependent edge-length.  *Journal of the ACM* 37(3),
  607-625.
* A. Montoya, C. Gueret, J. E. Mendoza, J. G. Villegas (2017).  The electric
  vehicle routing problem with nonlinear charging function.  *Transportation
  Research Part B* 103, 87-110.
* S. Storandt, S. Funke (2012).  Cruising with a battery-powered vehicle and
  not getting stranded.  *Proc. AAAI 2012*.
"""

from __future__ import annotations

import heapq
import math
import time
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from ..charging import ChargingFunction
from ..evaluate import evaluate, infeasible_plan
from ..plan import ChargeDecision, PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario
from ..search import LowerBounds, dijkstra, min_level_weights
from ..units import SECONDS_PER_DAY
from .base import Planner
from .labels import AStarPotential, completion_energy

INF = math.inf
#: Default tolerance for energies [kWh] and times [s].
EPS = 1e-9
#: ``how`` code of the root label.
_ROOT = -1

__all__ = [
    "CFPAStarPlanner",
    "CFPPlanner",
    "arrival_time",
    "dominates",
    "evaluate_function",
    "label_function",
    "switch_candidates",
]


# ======================================================================
# label functions
# ======================================================================
def label_function(
    cf: ChargingFunction,
    cap: float,
    t0: float,
    Fb: float,
    b: float,
    inn: float,
    cost: float,
    out: float,
    eps: float = EPS,
) -> tuple[list[float], list[float]]:
    """Breakpoints ``(ts, bs)`` of the label function ``B(t)`` (``s >= 0``).

    ``B(t) = min(x(t) - cost, out)`` with ``x(t) = min(F^{-1}(Fb + t - t0),
    max(cap, b))``, defined for ``t >= ts[0] = t_feas``.  ``ts`` is
    non-decreasing, ``bs`` strictly increasing; ``B`` is linear between
    consecutive breakpoints and constant (``bs[-1]``) after the last one.
    ``Fb`` must be ``cf.time_at(b)``.
    """
    xtop = max(cap, b)
    if b >= inn - eps:
        xlo = b
        tf = t0
    else:
        xlo = min(inn, xtop)
        tf = t0 + (cf.time_at(xlo) - Fb)
    xsat = min(out + cost, xtop)  # x at which B stops increasing, or the battery stops charging
    blo = min(xlo - cost, out)
    if xsat - xlo <= eps:
        return [tf], [blo]
    kap = t0 - Fb
    eb = cf.e_breaks
    tb = cf.t_breaks
    ts = [tf]
    bs = [blo]
    i = bisect_right(eb, xlo)
    n = len(eb)
    while i < n:
        e = eb[i]
        if e >= xsat:
            break
        ts.append(kap + tb[i])
        bs.append(e - cost)
        i += 1
    ts.append(kap + cf.time_at(xsat))
    bs.append(xsat - cost)
    return ts, bs


def evaluate_function(ts: Sequence[float], bs: Sequence[float], t: float) -> float:
    """``B(t)`` from its breakpoints (constant before ``ts[0]`` and after ``ts[-1]``)."""
    j = bisect_right(ts, t) - 1
    if j < 0:
        return bs[0]
    if j >= len(ts) - 1:
        return bs[-1]
    dt = ts[j + 1] - ts[j]
    if dt <= 0.0:
        return bs[j + 1]
    return bs[j] + (bs[j + 1] - bs[j]) * (t - ts[j]) / dt


def arrival_time(
    cf: ChargingFunction | None,
    cap: float,
    t0: float,
    Fb: float,
    b: float,
    inn: float,
    cost: float,
    out: float,
    e_goal: float,
    eps: float = EPS,
) -> float:
    """Least ``t >= t_feas`` with ``B(t) >= e_goal`` (``inf`` if there is none).

    Closed form: ``B(t) >= e_goal`` iff ``out >= e_goal`` and ``x(t) >= need``
    with ``need = max(inn, e_goal + cost)``; then ``t = t0 + F(need) - F(b)``.
    ``cf=None`` stands for labels that never charge (``s = -1``).
    """
    if out < e_goal - eps:
        return INF
    need = max(e_goal + cost, inn)
    if b >= need - eps:
        return t0
    if cf is None:
        return INF
    xtop = max(cap, b)
    if need > xtop + eps:
        return INF
    return t0 + (cf.time_at(min(need, xtop)) - Fb)


def dominates(
    ts1: Sequence[float],
    bs1: Sequence[float],
    ts2: Sequence[float],
    bs2: Sequence[float],
    eps: float = EPS,
) -> bool:
    """True iff label 1 dominates label 2: ``t_feas1 <= t_feas2`` and ``B1 >= B2``.

    The comparison is exact up to ``eps``: ``B1 - B2`` is piecewise linear with
    breakpoints in the union of both breakpoint sets and constant after the
    last, so it is enough to compare at the breakpoints of either function in
    ``[t_feas2, inf)``.  Beyond the last breakpoint of label 2 its function is
    constant while ``B1`` cannot decrease, so only breakpoints of label 1
    before ``ts2[-1]`` need a look.
    """
    if ts1[0] > ts2[0] + eps:
        return False
    n1 = len(ts1)
    n2 = len(ts2)
    if bs1[n1 - 1] < bs2[n2 - 1] - eps:
        return False
    last1 = n1 - 1
    # (a) B1 at the breakpoints of label 2
    j = 0
    for k in range(n2):
        t = ts2[k]
        while j < last1 and ts1[j + 1] <= t:
            j += 1
        if j == last1 or t <= ts1[0]:
            v1 = bs1[j] if t > ts1[0] else bs1[0]
        else:
            dt = ts1[j + 1] - ts1[j]
            v1 = bs1[j + 1] if dt <= 0.0 else bs1[j] + (bs1[j + 1] - bs1[j]) * (t - ts1[j]) / dt
        if v1 < bs2[k] - eps:
            return False
    # (b) B2 at the breakpoints of label 1 inside (ts2[0], ts2[-1])
    last2 = n2 - 1
    t_lo = ts2[0]
    t_hi = ts2[last2]
    j = 0
    for k in range(n1):
        t = ts1[k]
        if t <= t_lo:
            continue
        if t >= t_hi:
            break
        while j < last2 and ts2[j + 1] <= t:
            j += 1
        dt = ts2[j + 1] - ts2[j]
        v2 = bs2[j + 1] if dt <= 0.0 else bs2[j] + (bs2[j + 1] - bs2[j]) * (t - ts2[j]) / dt
        if bs1[k] < v2 - eps:
            return False
    return True


def _knot_times(knots: Sequence[float], departure_s: float, t_lo: float, t_hi: float) -> list[float]:
    """Trip times in ``(t_lo, t_hi)`` at which a day-periodic profile has a knot."""
    out: list[float] = []
    if t_hi <= t_lo:
        return out
    base = departure_s + t_lo
    for k in knots:
        m = math.floor((base - k) / SECONDS_PER_DAY) + 1
        t = k + SECONDS_PER_DAY * m - departure_s
        while t < t_hi:
            out.append(t)
            t += SECONDS_PER_DAY
    out.sort()
    return out


def switch_candidates(
    ts: Sequence[float],
    bs: Sequence[float],
    up_energies: Sequence[float] = (),
    knot_times: Sequence[float] = (),
    eps: float = EPS,
) -> tuple[list[float], list[float]]:
    """Candidate switch times ``(times, energies)`` of a label function, sorted by time.

    The breakpoints ``(ts, bs)`` themselves (Lemma 1 of the module docs), plus
    the times at which ``B`` crosses an energy of ``up_energies`` (the energies
    at which the charging power of the *next* station increases) and the given
    ``knot_times`` of its wait profile, both restricted to the interior of the
    active range of ``B``.
    """
    n = len(ts)
    et: list[float] = []
    eb: list[float] = []
    if n > 1:
        lo, hi = bs[0] + eps, bs[n - 1] - eps
        for e in up_energies:
            if e <= lo:
                continue
            if e >= hi:
                break
            j = bisect_right(bs, e) - 1
            dt = ts[j + 1] - ts[j]
            et.append(ts[j] + (e - bs[j]) / (bs[j + 1] - bs[j]) * dt)
            eb.append(e)
        for t in knot_times:
            if ts[0] < t < ts[n - 1]:
                et.append(t)
                eb.append(evaluate_function(ts, bs, t))
    if not et:
        return list(ts), list(bs)
    pairs = sorted(list(zip(ts, bs)) + list(zip(et, eb)))
    return [p[0] for p in pairs], [p[1] for p in pairs]


# ======================================================================
# the engine
# ======================================================================
@dataclass(slots=True)
class _Outcome:
    """Result of one CFP run: incumbent, settled-label records and counters."""

    best: float  #: trip time of the incumbent (``inf`` if none)
    best_rec: tuple | None  #: its label record ``(t0, Fb, s, parent, how, switch_time)``
    records: list[tuple]  #: settled labels, indexed by label id
    status: str  #: ``"optimal"``, ``"limit"`` or ``"exhausted"``
    settled: int
    generated: int
    pushes: int
    pruned: int
    pruned_bound: int
    pruned_kappa: int
    peak_queue: int
    dominance_checks: int
    breakpoints: int
    station_labels: int


def _dominated_in(labels: list, ts: list[float], bs: list[float], bmax: float, eps: float) -> tuple[bool, int]:
    """Is the label dominated by one of the settled ``(t_feas, B_max, ts, bs)`` in ``labels``?

    Two scalar necessary conditions (earlier feasible time, at least the same
    final energy) filter the candidates before the breakpoint comparison of
    :func:`dominates`.  Returns ``(dominated, number of full comparisons)``.
    (Reference implementation; the search uses :class:`_SettledSet`, which
    gives the same answers in logarithmic time for most queries.)
    """
    tfe = ts[0] + eps
    bme = bmax - eps
    checks = 0
    for tf1, bm1, ts1, bs1 in labels:
        if tf1 <= tfe and bm1 >= bme:
            checks += 1
            if dominates(ts1, bs1, ts, bs, eps):
                return True, checks
    return False, checks


def _stair_max_add(T: list[float], V: list[float], t: float, v: float) -> None:
    """Add ``(t, v)`` to a *maximum* staircase (``T`` and ``V`` strictly increasing).

    The staircase answers "largest ``v'`` with ``t' <= t``" by one bisection;
    points with ``t' >= t`` and ``v' <= v`` are removed (they can never be
    that answer again), a point dominated by an existing one is not added.
    """
    j = bisect_right(T, t)
    if j and V[j - 1] >= v:
        return
    if j == len(T):
        T.append(t)
        V.append(v)
        return
    lo = bisect_left(T, t, 0, j)
    hi = j
    n = len(T)
    while hi < n and V[hi] <= v:
        hi += 1
    T[lo:hi] = [t]
    V[lo:hi] = [v]


class _SettledSet:
    """Settled labels of one node (or one charged set) for the dominance test.

    Gives exactly the answer of :func:`_dominated_in` over all settled labels,
    but in ``O(log n)`` for the common cases:

    * *constant* labels (a single breakpoint -- every label that has not
      charged yet, and labels whose stop cannot add energy) are dominated
      precisely when ``t_feas' <= t_feas + eps`` and ``B' >= B_max - eps`` (all
      comparisons of :func:`dominates` reduce to these two), so they are kept
      in a maximum staircase over ``(t_feas, B)``;
    * the other labels pass through the two scalar pre-filters of
      :func:`_dominated_in` first: a staircase over ``(t_feas, B_max)`` tells in
      one bisection whether *any* of them passes, and the labels sorted by
      decreasing ``B_max`` limit the scan to those with ``B_max' >= B_max - eps``
      (the order of the scan does not change the answer).
    """

    __slots__ = ("cb", "ct", "fb", "fl", "qb", "qt")

    def __init__(self) -> None:
        self.ct: list[float] = []  # constant labels: max staircase (t_feas, value)
        self.cb: list[float] = []
        self.fb: list[float] = []  # other labels sorted by -B_max ...
        self.fl: list[tuple] = []  # ... as (t_feas, B_max, ts, bs)
        self.qt: list[float] = []  # max staircase (t_feas, B_max) of the other labels
        self.qb: list[float] = []

    def dominated(self, ts: list[float], bs: list[float], bmax: float, eps: float) -> tuple[bool, int]:
        tfe = ts[0] + eps
        bme = bmax - eps
        ct = self.ct
        if ct:
            j = bisect_right(ct, tfe)
            if j and self.cb[j - 1] >= bme:
                return True, 0
        qt = self.qt
        if not qt:
            return False, 0
        j = bisect_right(qt, tfe)
        if not j or self.qb[j - 1] < bme:
            return False, 0
        checks = 0
        fl = self.fl
        t = ts[0]
        b0e = bs[0] - eps
        for k in range(bisect_right(self.fb, -bme)):
            tf1, bm1, ts1, bs1 = fl[k]
            if tf1 <= tfe:
                # the first comparison of dominates(): B1 at t_feas of the new
                # label, evaluated exactly as there (but found by bisection)
                last1 = len(ts1) - 1
                j = bisect_right(ts1, t) - 1
                if j < 0:
                    j = 0
                if j == last1 or t <= tf1:
                    v1 = bs1[j] if t > tf1 else bs1[0]
                else:
                    dt = ts1[j + 1] - ts1[j]
                    v1 = bs1[j + 1] if dt <= 0.0 else bs1[j] + (bs1[j + 1] - bs1[j]) * (t - ts1[j]) / dt
                if v1 < b0e:
                    continue
                checks += 1
                if dominates(ts1, bs1, ts, bs, eps):
                    return True, checks
        return False, checks

    def constant_dominated(self, tf: float, b: float, eps: float) -> bool:
        """The constant-label part of :meth:`dominated` for ``t_feas = tf``, ``B_max = b``."""
        ct = self.ct
        if not ct:
            return False
        j = bisect_right(ct, tf + eps)
        return bool(j) and self.cb[j - 1] >= b - eps

    def add(self, ts: list[float], bs: list[float], bmax: float) -> None:
        tf = ts[0]
        if len(ts) == 1:
            _stair_max_add(self.ct, self.cb, tf, bmax)
            return
        k = bisect_right(self.fb, -bmax)
        self.fb.insert(k, -bmax)
        self.fl.insert(k, (tf, bmax, ts, bs))
        _stair_max_add(self.qt, self.qb, tf, bmax)


def _search(
    P: PreparedScenario,
    *,
    hb: Sequence[float],
    el: Sequence[float] | None,
    rate: float,
    need: Sequence[float],
    max_labels: int,
    eps: float,
    exact_switching: bool,
    trace: SearchTrace | None,
    options: Sequence[Sequence[tuple[int, float, float, float]]] | None = None,
    upper_bound: float = INF,
) -> _Outcome:
    """The CFP label-setting search (see the module docs).

    The potential is ``h(v, e) = hb[v] + rate * max(0, el[v] - e)``; ``hb[v]``
    is ``inf`` for nodes that cannot reach the destination (they are never
    entered).  ``el is None`` means ``h = 0`` (Dijkstra order).  ``options``
    replaces ``P.edge_options``; ``upper_bound`` is the trip time of some
    feasible route.

    *Performance devices that do not change the result* (the settled labels,
    hence the incumbent and the route, are those of the plain algorithm):

    1. the settled labels of a node live in a :class:`_SettledSet`, which
       answers the dominance test exactly like a scan over all of them;
    2. a drive label that is not at the destination is dropped when it is
       generated if the plain algorithm would discard it when popped: if a
       *settled* constant label dominates it (settled sets only grow), or if
       a constant label pushed earlier dominates it *exactly*
       (``t_feas' <= t_feas``, ``B' >= B_max``) -- that label has a key no
       larger (Lemma 2) and a smaller sequence number, so it is popped first
       and is then settled, or discarded in favour of a settled label that
       dominates both;
    3. labels with a key above ``upper_bound`` (relative slack ``1e-9``) are
       never pushed: they cannot lead to a route of at most that time, while
       the optimum is at most that time.
    """
    net = P.net
    n = net.num_nodes
    astar = el is not None
    elv: Sequence[float] = el if el is not None else hb  # (only read when astar)
    nlev = len(P.speed_factors)
    Q = P.capacity
    # A zero reserve would let exact "just enough" plans arrive with exactly 0 kWh, where float
    # rounding (-1e-15) trips the evaluator's strict "battery empty" test: keep a hair of margin.
    floor = 10.0 * eps
    emin = max(P.e_min, floor)
    cap = P.e_charge_cap
    e0 = P.e_init
    egoal = max(P.e_dest_min, floor)
    origin, dest = P.origin, P.destination
    shift = max(emin - P.e_min, egoal - P.e_dest_min)
    if shift > 0.0:  # raising every reserve by at most `shift` raises the energy needed by at most that
        need = [x + shift if x < INF else INF for x in need]
    cap_lim = cap - eps
    emin_eps = emin - eps
    ub_lim = upper_bound + 1e-9 * abs(upper_bound) + 1e-6 if upper_bound < INF else INF

    # ---------------------------------------------------- adjacency table
    head = net.head
    if options is None:
        options = P.edge_options
    adj: list[tuple[tuple[int, float, float, int], ...]] = []
    for v in range(n):
        row = []
        if hb[v] < INF:
            for a in net.out_edges[v]:
                w = head[a]
                if hb[w] == INF:
                    continue  # w cannot reach the destination
                base = a * nlev
                for k, tau, c, _dc in options[a]:
                    row.append((w, tau, c, base + k))
        adj.append(tuple(row))

    # ---------------------------------------------------- station tables
    ns = len(P.stations)
    cfs = P.cf
    time_at = [f.time_at for f in cfs]
    setup = P.setup_s
    station_at = P.station_at
    is_station = P.is_station_node
    static = not P.time_dependent
    wait_s = P.wait_s
    wconst = [wait_s(s, 0.0) for s in range(ns)] if static else [0.0] * ns
    dep = P.departure_s
    up_energies: list[list[float]] = []
    knots: list[tuple[float, ...]] = []
    for s in range(ns):
        pw, eb = cfs[s].power_kw, cfs[s].e_breaks
        ups = [eb[i] for i in range(1, len(pw)) if pw[i] > pw[i - 1] * (1.0 + 1e-12)]
        up_energies.append(ups if exact_switching else [])
        prof = P.wait_profiles[s]
        knots.append(tuple(prof.knots_s) if (exact_switching and not static and len(prof.knots_s) > 1) else ())

    # settled-label sets: set v holds the free labels of node v (and, at nodes
    # with a single station or none, the charged ones); nodes with several
    # stations get a second set for their charged labels
    csid = list(range(n))
    n_sets = n
    for v, lst in station_at.items():
        if len(lst) > 1:
            csid[v] = n_sets
            n_sets += 1
    sets: list[_SettledSet] = [_SettledSet() for _ in range(n_sets)]
    # constant drive labels pushed so far, per node: max staircase (t_feas, B)
    gen_t: list[list[float]] = [[] for _ in range(n)]
    gen_b: list[list[float]] = [[] for _ in range(n)]
    records: list[tuple] = []  # settled labels: (t0, Fb, s, parent, how, switch_time)

    heap: list[tuple] = []
    push, pop = heapq.heappush, heapq.heappop
    visit = trace.visit if trace is not None else None
    seq = 0
    pushes = generated = pruned = pruned_bound = pruned_kappa = 0
    settled = peak = checks_total = bp_total = station_labels = 0
    best = INF
    best_rec: tuple | None = None
    status = "exhausted"

    # ---------------------------------------------------------- root label
    if hb[origin] < INF:
        h0 = 0.0
        if astar:
            d = elv[origin] - e0
            h0 = hb[origin] + d * rate if d > 0.0 else hb[origin]
        if origin == dest:
            ta = arrival_time(None, cap, 0.0, 0.0, e0, emin, 0.0, Q, egoal, eps)
            if ta < best:
                best = ta
                best_rec = (0.0, 0.0, -1, -1, _ROOT, 0.0)
        if h0 < best and h0 <= ub_lim:
            push(heap, (h0, 0, origin, -1, 0.0, 0.0, e0, emin, 0.0, Q, -1, _ROOT, 0.0))
            seq = 1
            pushes = 1

    # ------------------------------------------------------------ main loop
    while heap:
        lq = len(heap)
        if lq > peak:  # (an `if` is cheaper than max() in this loop)
            peak = lq
        if pushes > max_labels:
            status = "limit"
            break
        key, _, v, s, t0, Fb, b, inn, cost, out, par, how, sw = pop(heap)
        if key >= best:
            status = "optimal"  # every other key is at least as large
            break

        # ------------------------------------------- the label function
        # (compared and stored as min(B, need[v]): more energy than `need[v]`
        # cannot make the rest of the trip faster than the driving-time bound)
        oc = need[v]
        if out < oc:
            oc = out
        if s < 0:
            bv = b - cost
            if bv > oc:
                bv = oc
            ts = [t0]
            bs = [bv]
            bmax = bv
        else:
            ts, bs = label_function(cfs[s], cap, t0, Fb, b, inn, cost, oc, eps)
            bmax = bs[-1]
        bp_total += len(ts)

        # ------------------------------------ dominance by settled labels
        charged = how <= -2
        sid = csid[v] if charged else v
        dom, nchk = sets[v].dominated(ts, bs, bmax, eps)
        checks_total += nchk
        if not dom and sid != v:
            dom, nchk = sets[sid].dominated(ts, bs, bmax, eps)
            checks_total += nchk
        if dom:
            pruned += 1
            continue
        i_new = len(records)
        records.append((t0, Fb, s, par, how, sw))
        sets[sid].add(ts, bs, bmax)
        settled += 1
        if visit is not None:
            visit(v, key)

        # ------------------------------------------------------- drive
        if s >= 0 or b >= emin_eps:  # (the origin label below the reserve cannot move)
            xtop = cap if s >= 0 else b
            xtop_e = xtop + eps
            emc = emin + cost
            for w, tau, c, code in adj[v]:
                out2 = out - c
                if out2 > Q:
                    out2 = Q
                if out2 < emin_eps:
                    continue
                inn2 = emc + c
                if inn2 < inn:
                    inn2 = inn
                if inn2 > xtop_e:
                    continue
                cost2 = cost + c
                t02 = t0 + tau
                if b >= inn2 - eps:
                    tf2 = t02
                    bf = b - cost2
                elif s < 0:
                    continue
                else:
                    tf2 = t02 + (time_at[s](inn2 if inn2 < xtop else xtop) - Fb)
                    bf = inn2 - cost2
                if bf > out2:
                    bf = out2
                generated += 1
                if astar:
                    d = elv[w] - bf
                    k2 = tf2 + (hb[w] + d * rate if d > 0.0 else hb[w])
                else:
                    k2 = tf2
                if k2 >= best or k2 > ub_lim:
                    pruned_bound += 1
                    continue
                if w == dest:
                    ta = arrival_time(cfs[s] if s >= 0 else None, cap, t02, Fb, b, inn2, cost2, out2, egoal, eps)
                    if ta < best:
                        best = ta
                        best_rec = (t02, Fb, s, i_new, code, 0.0)
                        if k2 >= best:
                            continue
                    const2 = False
                else:
                    # (t_feas, B_max) of the new label exactly as label_function()
                    # will compute them when it is popped (capped at need[w])
                    oc2 = need[w]
                    if out2 < oc2:
                        oc2 = out2
                    if s < 0:
                        bm2 = b - cost2
                        if bm2 > oc2:
                            bm2 = oc2
                        const2 = True
                    else:
                        xt2 = cap if cap >= b else b
                        if b >= inn2 - eps:
                            xlo2 = b
                        else:
                            xlo2 = inn2 if inn2 < xt2 else xt2
                        xs2 = oc2 + cost2
                        if xs2 > xt2:
                            xs2 = xt2
                        if xs2 - xlo2 <= eps:
                            bm2 = xlo2 - cost2
                            if bm2 > oc2:
                                bm2 = oc2
                            const2 = True
                        else:
                            bm2 = xs2 - cost2
                            const2 = False
                    if sets[w].constant_dominated(tf2, bm2, eps):
                        pruned += 1
                        continue
                    gt = gen_t[w]
                    if gt:
                        j = bisect_right(gt, tf2)
                        if j and gen_b[w][j - 1] >= bm2:
                            pruned += 1
                            continue
                push(heap, (k2, seq, w, s, t02, Fb, b, inn2, cost2, out2, i_new, code, 0.0))
                seq += 1
                pushes += 1
                if const2:
                    _stair_max_add(gen_t[w], gen_b[w], tf2, bm2)

        # ------------------------------------------------------- stops
        if not charged and is_station[v]:
            for s2 in station_at[v]:
                if len(ts) > 1 and (up_energies[s2] or knots[s2]):
                    kt = _knot_times(knots[s2], dep, ts[0], ts[-1]) if knots[s2] else ()
                    cts, cbs = switch_candidates(ts, bs, up_energies[s2], kt, eps)
                else:
                    cts, cbs = ts, bs
                su = setup[s2]
                F2 = time_at[s2]
                wc = wconst[s2]
                cf2 = cfs[s2]
                kmin = INF
                blim = need[v] - eps
                if blim > cap_lim:
                    blim = cap_lim
                for j in range(len(cts)):
                    bj = cbs[j]
                    if bj >= blim:
                        break  # no energy may be added / none is needed any more
                    tj = cts[j]
                    t0n = tj + ((wc if static else wait_s(s2, tj)) + su)
                    Fbn = F2(bj)
                    if bj >= emin_eps:
                        kap = t0n - Fbn
                        if kap >= kmin - eps:
                            pruned_kappa += 1
                            continue  # dominated by an earlier sibling
                        kmin = kap
                        tfn = t0n
                        bf = bj
                    else:  # only the origin label below the reserve: charge up to it first
                        if emin > cap_lim:
                            continue
                        tfn = t0n + (F2(emin) - Fbn)
                        bf = emin
                    generated += 1
                    if astar:
                        d = elv[v] - bf
                        kn = tfn + (hb[v] + d * rate if d > 0.0 else hb[v])
                    else:
                        kn = tfn
                    if kn >= best or kn > ub_lim:
                        pruned_bound += 1
                        continue
                    if v == dest:
                        ta = arrival_time(cf2, cap, t0n, Fbn, bj, emin, 0.0, Q, egoal, eps)
                        if ta < best:
                            best = ta
                            best_rec = (t0n, Fbn, s2, i_new, -2 - s2, tj)
                            if kn >= best:
                                continue
                    push(heap, (kn, seq, v, s2, t0n, Fbn, bj, emin, 0.0, Q, i_new, -2 - s2, tj))
                    seq += 1
                    pushes += 1
                    station_labels += 1

    if status == "exhausted" and best < INF:
        status = "optimal"
    return _Outcome(
        best=best,
        best_rec=best_rec,
        records=records,
        status=status,
        settled=settled,
        generated=generated,
        pushes=pushes,
        pruned=pruned + pruned_bound + pruned_kappa,
        pruned_bound=pruned_bound,
        pruned_kappa=pruned_kappa,
        peak_queue=peak,
        dominance_checks=checks_total,
        breakpoints=bp_total,
        station_labels=station_labels,
    )


def _reconstruct(
    P: PreparedScenario, records: list[tuple], rec: tuple, t_star: float
) -> tuple[list[int], list[int], list[ChargeDecision]]:
    """Edges, speed levels and charging stops of the incumbent label ``rec`` at time ``t_star``."""
    nlev = len(P.speed_factors)
    cfs = P.cf
    cap = P.e_charge_cap
    t0, Fb, _s, par, how, sw = rec
    edges: list[int] = []
    levels: list[int] = []
    stops: list[tuple[int, int, float]] = []  # (edges after the stop, station, target)
    t = t_star
    while how != _ROOT:
        if how >= 0:  # drive: same station, shift the time to the tail's frame
            a, k = divmod(how, nlev)
            edges.append(a)
            levels.append(k)
            t0p, Fbp, _sp, parp, howp, swp = records[par]
            t -= t0 - t0p
            t0, Fb, par, how, sw = t0p, Fbp, parp, howp, swp
        else:  # stop: departure energy at time t, then continue at the switch time
            st = -2 - how
            x = min(cfs[st].energy_at(Fb + (t - t0)), cap)
            stops.append((len(edges), st, x))
            t = sw
            t0, Fb, _sp, par, how, sw = records[par]
    edges.reverse()
    levels.reverse()
    total = len(edges)
    charges = [ChargeDecision(total - k, st, x) for k, st, x in reversed(stops)]
    return edges, levels, charges


# ======================================================================
# planners
# ======================================================================
class CFPPlanner(Planner):
    """Charging Function Propagation: fastest route, continuous charging amounts.

    Options
    -------
    ``max_labels``
        safeguard on the number of pushed labels (default 3 000 000).  If it
        is hit, the best plan found so far (if any) is returned with
        ``stats.extra["status"] == "limit"`` and a note in ``plan.message``;
        optimality is then not proven.
    ``eps``
        tolerance of the dominance test and of feasibility comparisons
        (default 1e-9 kWh / s).
    ``exact_switching``
        ``True`` (default): besides the breakpoints of a label function also
        try the crossings of upward power breaks and wait-profile knots as
        stop times (see the module docs); this makes the algorithm exact for
        arbitrary piecewise-linear charging functions and time-dependent
        waits.  ``False`` gives the textbook algorithm, exact only for
        concave charging functions and static waits.

    Guarantee (``exact = "exact"``): the least trip time over all routes with
    at most one stop per node visit and any real charging amounts up to
    ``e_charge_cap`` -- see the module documentation.  The objective is always
    the trip time, also if ``P.objective == "cost"``; then the plan is the
    fastest one, its generalised cost is reported by the evaluator and
    ``plan.internal_objective`` is that evaluated cost (the trip time itself is
    ``stats.extra["time_s"]``).
    """

    name = "cfp"
    title = "CFP (charging function propagation)"
    exact = "exact"
    objectives = ("time",)
    description = (
        "Exact fastest route with continuous charging amounts: label setting on "
        "piecewise-linear charging functions (Baum et al.), one function-label per "
        "last station, pointwise dominance, optimal stop times at breakpoints."
    )

    def _potential(self, P: PreparedScenario) -> tuple[list[float], list[float] | None, float, list[float], str]:
        """``(hb, el, rate, need, name)``: the queue potential ``h = hb + rate * max(0, el - e)``
        and ``need[v]``, the energy that suffices to finish along the fastest tree.

        Dijkstra order: ``h = 0`` on nodes that can reach the destination
        (``hb = inf`` elsewhere), no energy term.
        """
        tl, _ = dijkstra(P.net, P.destination, min_level_weights(P, "time"), reverse=True)
        need = completion_energy(P, tl, "time")
        return [0.0 if d < INF else INF for d in tl], None, 0.0, need, "none (Dijkstra order)"

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        opts = self.options
        max_labels = int(opts.get("max_labels", 3_000_000))
        eps = float(opts.get("eps", EPS))
        exact_switching = bool(opts.get("exact_switching", True))
        if eps <= 0.0:
            raise ValueError("eps must be positive")
        tr = SearchTrace() if trace else None

        t0 = time.perf_counter()
        hb, el, rate, need, pot_name = self._potential(P)
        t1 = time.perf_counter()
        ub = INF
        if any(len(o) > 1 for o in P.edge_options):
            # Several speed levels: first solve the problem restricted to one level per
            # edge (the one minimising time + rate * energy).  Its route is a route of the
            # full problem, so its trip time bounds the optimum and the full search never
            # pushes labels with a larger key -- the result is unchanged (see _search).
            pre = _search(
                P,
                hb=hb,
                el=el,
                rate=rate,
                need=need,
                max_labels=max(1, max_labels // 4),
                eps=eps,
                exact_switching=exact_switching,
                trace=None,
                options=[(min(o, key=lambda x: (x[1] + rate * x[2], x[1])),) for o in P.edge_options],
            )
            ub = pre.best
        t_ub = time.perf_counter()
        res = _search(
            P,
            hb=hb,
            el=el,
            rate=rate,
            need=need,
            max_labels=max_labels,
            eps=eps,
            exact_switching=exact_switching,
            trace=tr,
            upper_bound=ub,
        )
        t2 = time.perf_counter()
        proven = res.status == "optimal" and (exact_switching or _textbook_is_exact(P))
        stats = SearchStats(
            algorithm=self.name,
            settled=res.settled,
            generated=res.generated,
            pushes=res.pushes,
            pruned=res.pruned,
            peak_queue=res.peak_queue,
            extra={
                "labels": len(res.records),
                "breakpoints": res.breakpoints,
                "station_labels": res.station_labels,
                "dominance_checks": res.dominance_checks,
                "pruned_by_bound": res.pruned_bound,
                "pruned_by_sibling": res.pruned_kappa,
                "status": res.status,
                "optimality": "proven" if proven else "not proven",
                "exact_switching": exact_switching,
                "potential": pot_name,
                "waits": "time-dependent" if P.time_dependent else "static",
                "planned_objective": "time",
                "bounds_s": t1 - t0,
                "search_s": t2 - t1,
                "upper_bound": ub if math.isfinite(ub) else None,
                "upper_bound_s": t_ub - t1,
            },
        )
        if res.best_rec is None:
            return infeasible_plan(P, self.name, _infeasibility_reason(P, res, hb, max_labels), stats, tr)
        edges, levels, charges = _reconstruct(P, res.records, res.best_rec, res.best)
        plan = evaluate(
            P,
            PlanDecision(edges, levels, charges),
            algorithm=self.name,
            internal_objective=res.best,
            stats=stats,
            trace=tr,
        )
        stats.extra["time_s"] = res.best
        if P.objective != "time":
            # the plan is time-optimal, not cost-optimal: claim only what was measured
            plan.internal_objective = plan.objective_value
        if res.status == "limit":
            plan.message = (
                f"label limit reached (max_labels={max_labels:,}); best route found so far, "
                "optimality not proven"
            )
        return plan


class CFPAStarPlanner(CFPPlanner):
    """CFP guided by a consistent energy-deficit potential (A*).

    The key of a label is ``t_feas + h(v, B(t_feas))``; ``t + h(v, B(t))`` is
    non-decreasing in ``t`` (Lemma 2 of the module docs), so the key is a lower
    bound for the whole label and the result is identical to
    :class:`CFPPlanner` while far fewer labels are settled.  Option
    ``potential`` selects ``h``:

    ``"tight"`` (default)
        the coupled time/energy bound of :meth:`AStarPotential.tight`
        without its stop-count term: ``h = B(v) + r max(0, E_v - e)`` where the
        required energy ``E_v`` is measured on the *same* path and speed level
        as the driving time (``B``) and ``r`` is the best charging rate in the
        usable energy range.  Never weaker than ``"basic"``; with several speed
        levels the difference is large.
    ``"basic"``
        ``LowerBounds.h_time`` of :mod:`evroute.search`:
        ``T_lb(v) + 3600 / P_max * max(0, E_lb(v) + e_goal - e)``.

    (The stop-count term of the BLS potential is a step function of the energy;
    it would make ``t + h(v, B(t))`` non-monotone inside one label, so it is
    not used here.)  Other options as for :class:`CFPPlanner`.
    """

    name = "cfp-astar"
    title = "CFP-A*"
    description = (
        "CFP with an A* key: earliest feasible time plus a consistent lower bound "
        "(coupled driving time + energy deficit at the best usable charging rate). "
        "Same optimum as CFP, far fewer labels."
    )

    def _potential(self, P: PreparedScenario) -> tuple[list[float], list[float] | None, float, list[float], str]:
        kind = self.options.get("potential", "tight")
        lb = LowerBounds(P)
        if kind == "basic":
            g = lb.e_goal
            el = [e + g if e < INF else INF for e in lb.energy_lb]
            need = completion_energy(P, lb.time_lb, "time")
            return list(lb.time_lb), el, lb.sec_per_kwh, need, "basic energy-deficit (LowerBounds.h_time)"
        if kind == "tight":
            pot = AStarPotential.tight(P, lb, "time")
            return list(pot.base), list(pot.e_lin), pot.rate, list(pot.need), "tight coupled energy-deficit"
        raise ValueError(f"unknown potential {kind!r}; choose from ('tight', 'basic')")


# ======================================================================
# diagnostics
# ======================================================================
def _textbook_is_exact(P: PreparedScenario) -> bool:
    """Is the breakpoints-only candidate set provably exact for this scenario?

    Needs static waits and charging power that never increases with the energy
    in the range where stops can start (``[min(e_min, e_init), cap)``); a power
    increase exactly at the lower end cannot occur strictly inside a label.
    """
    if P.time_dependent:
        return False
    lo = min(P.e_min, P.e_init)
    for cf in P.cf:
        pw, eb = cf.power_kw, cf.e_breaks
        for i in range(1, len(pw)):
            if pw[i] > pw[i - 1] * (1.0 + 1e-12) and lo < eb[i] < P.e_charge_cap:
                return False
    return True


def _infeasibility_reason(P: PreparedScenario, res: _Outcome, hb: Sequence[float], max_labels: int) -> str:
    o, d = P.origin, P.destination
    if res.status == "limit":
        return (
            f"label limit reached (max_labels={max_labels:,}, {res.pushes:,} labels pushed) before "
            "any route to the destination was found; increase max_labels"
        )
    if hb[o] == INF:
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
