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
label created this way at ``v`` may itself spawn stops at ``v`` again, at every
station ``s3 != s2`` of the node (consecutive stops, exactly as in BLS: e.g. a
fast DC charger up to the energy where its power drops below that of an AC
charger next to it, then the AC charger); it never spawns a stop at ``s2``
itself -- continuing to charge at ``s2`` is the label's own function and is
never worse than paying the wait and setup again -- nor a switch at its own
``t0`` (nothing charged at ``s2`` yet: the parent's direct switch to ``s3`` is
no worse).  Targets are limited by ``cap``: switch times with
``B_L(tj) >= cap`` are useless and skipped.

*Stops below the reserve.*  The evaluator checks the reserve on arrival and
departure only, so at the origin with ``e_init < e_min`` a stop may end below
the reserve if another stop at the same node follows.  At a node with several
stations a station label with ``b < e_min`` is therefore *extended*: its
function, its dominance tests and its switch candidates start at ``(t0, b)``
instead of at ``t_feas`` (it can switch from ``t0`` on, it can only drive from
``t_feas`` on), and its key is the minimum of ``t + h(v, B(t))`` from ``t0``
on (Lemma 2 below makes it a lower bound for every route through the label,
including switches before ``t_feas``).  At a node with a single station nothing can follow such a stop,
and the function and key start at ``t_feas`` as for every other label.

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

*Key.*  The key of a label is ``t_feas`` (``"cfp"``) or the minimum of
``t + h(v, B(t))`` over the label (``"cfp-astar"``) with a potential
``h(v, e) = base(v) + r * max(0, E(v) - e) [+ sigma * ceil(max(0, E_s(v) - e) / R)]``,
non-increasing in ``e`` and consistent on drive edges and stops.  Two are
available (option ``potential``): ``"basic"`` is
:meth:`~evroute.search.LowerBounds.h_time` -- ``base`` the fastest driving time
``T_lb(v)``, ``E(v) = E_lb(v) + e_goal`` the least energy needed,
``r = 3600 / P_max``, no stop term -- and ``"tight"`` (default) is
:meth:`~evroute.algorithms.labels.AStarPotential.tight` *with* its stop-count
term: ``E(v)`` is measured on the *same* path and speed level as the driving
time, ``r`` is the best charging rate in the usable energy range, so
``r * p(x) / 3600 <= 1`` for every power ``p(x)`` that occurs, and ``sigma``
(the least wait + setup of a stop) is counted once for every ``R`` kWh of the
deficit ``E_s(v) - e`` (``R`` the most one stop can add).

**Lemma 2.**  Between the energies ``E_s(v) - j R`` at which the stop count
drops, ``t + h(v, B(t))`` is non-decreasing in ``t``.  Hence its minimum over
the label is attained at ``t_feas`` or at the first time ``B`` reaches one of
those energies (computed in closed form from ``F_s``; one or two candidates,
since ``R`` is almost the whole battery), and this minimum -- the key -- is a
lower bound for every route the label stands for; keys never decrease along
edge extensions or stops.

*Proof.*  ``dB/dt <= dx/dt = p(x) / 3600`` and ``r * p(x) / 3600 <= 1`` (see
above; for the basic potential because power never exceeds ``P.p_max``), while
the linear part of ``h(v, .)`` is non-increasing with slope at least ``-r``.
Hence ``d/dt (t + h(v, B(t))) >= 1 - 1 = 0`` where the energy term is active
and ``= 1`` elsewhere, as long as the stop count is constant.  (Within one
label the vehicle keeps charging in the stop it has already paid for, so the
count can drop without a new overhead: that is why the key is the minimum
over the label, not ``t_feas + h(v, B(t_feas))``.)  A completion that leaves
the label at time ``t`` costs at least ``t + h(v, B(t))`` (admissibility), so
the key is a lower bound.  For an edge extension,
``key' = \min_{t'} [t' + tau + h(w, \min(B(t') - c, Q))]
>= \min_{t'} [t' + h(v, B(t'))]`` by consistency of ``h`` on drive edges
(Theorem 2 in ``docs/algorithms.md``; the docs of
:class:`~evroute.algorithms.labels.AStarPotential` for the coupled bound and
the stop term); a stop at ``tj`` moves from ``(tj, B(tj))`` to states that
pay at least ``sigma`` and add at most ``R`` kWh, so by consistency on charging
transitions its labels' keys are at least ``tj + h(v, B(tj)) >= key``.
:math:`\square`

*Dominance (set dominance).*  A popped label ``L`` is discarded if the
*upper envelope* of the settled labels of its node lies above it:
``E(t) = max { B_L'(t) : L' settled, t_feas(L') <= t } >= B_L(t)`` for every
``t >= t_feas(L)`` (:class:`_Envelope`; constant labels are compared one by one,
the others through their envelope).  This is exact: a route of ``L`` that
leaves the node at time ``t`` with ``y = B_L(t)`` -- along an edge, or by a stop
at another station of the node -- is matched by the settled label ``L'`` that
attains ``E(t)``: ``L'`` can leave at ``t`` with at least ``y`` (monotonicity),
and for a stop, ``L'``'s own stop labels at the candidate times have, by
Lemma 1, an upper envelope at least as high as switching at ``t`` (if ``L'``
is charging at that very station, continuing to charge is better still, see
below).  ``L'`` was settled, so its extensions and stops were generated.  Single
dominance by one label (``t_feas(L1) <= t_feas(L2)``, ``B1 >= B2``), the rule of
earlier versions, is the special case of a one-label envelope; with several
speed levels it is far too weak -- the labels of one charging event that drove
different speed levels are each best somewhere (fast ones early, frugal ones
once the charger saturates) -- while most of them lie below the envelope of a
few.  ``E - B_L`` is linear between the union of their breakpoints and both are
constant after the last one, so the test is exact on finitely many points.  A
tolerance of ``eps`` (1e-9 kWh / s) makes ties dominate, so duplicates and
zero-gain cycles die.  Settled labels are never removed, so a label pruned
once would be pruned at any later time as well.

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

*One set per node.*  A label ``A`` created by a stop at ``s`` cannot stop at
``s`` again, so it can do less than a label ``B`` that may.  Comparing them is
nevertheless exact, and every node keeps a single settled set: if ``A``
attains the envelope at time ``tj``, a stop of ``B`` at ``s`` at ``tj`` yields at
every later time no more energy than ``A`` continuing to charge at ``s``
(``A``'s own function: ``B_A(tj) >= B_B(tj)``, and the stop pays wait and
setup), and all other continuations of ``B`` are open to ``A`` as well.  (Earlier versions
allowed one stop per node visit only and kept a second set for charged labels
at multi-station nodes; they missed routes such as DC-then-AC at one node.)

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
routes the evaluator accepts with arbitrary charging amounts ``<= cap`` (any
number of consecutive stops at different stations of a node).

*Sketch.*  (i) The label function describes exactly the set of
``(time, energy)`` pairs of its route family, extension (section 2) and stops
(section 3) map families to families, and by Lemma 1 the finitely many labels
created at the candidate times have the same upper envelope as the continuum of
all switch times, for every final time.  (ii) Everything is monotone (arriving
earlier with more energy is never worse -- FIFO for the waits), so a label
below the envelope of the settled labels of its node can be dropped together
with all its descendants (section 4, with energies above ``need[v]``
identified).  If ``L2`` is dropped, any route through it that leaves the node
at time ``t`` is replaced by one through the settled label ``L1`` attaining the
envelope at ``t``, which is no slower; ``L1``'s own extensions and stops were
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
  parent chain, and per node an :class:`_Envelope` for the dominance test --
  constant labels in a staircase (one bisection), the upper envelope of the
  others as a point list (a test bisects to the pieces of the new label and
  gallops over the envelope where it might be below).
* Drive labels that the settled labels dominate as constants, or that a
  constant label pushed earlier dominates, are dropped before they are pushed.
  With several speed levels a search restricted to one level per edge first
  provides an upper bound on the optimum: no label whose lower bound (the A*
  key; for ``"cfp"``, which keeps the Dijkstra order ``t_feas``, the key of the
  tight potential of ``"cfp-astar"``) exceeds it is pushed.  None of this
  changes the optimum (see :func:`_search`).
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
* Cost: with several speed levels the labels that have not charged yet form
  long ``(time, energy)`` staircases (every edge trades time for energy, as in
  BLS); the set dominance keeps the charged labels in check.  On the
  ``turkey-karaman-istanbul`` preset with three levels ``"cfp-astar"`` settles
  about 220 000 labels (earlier versions: millions, and ran out of labels);
  ``max_labels`` is the safeguard.
* The pure functions :func:`label_function`, :func:`arrival_time`,
  :func:`dominates`, :func:`switch_candidates` and :func:`evaluate_function`
  are public so that the mathematics can be tested in isolation.
* Options: ``max_labels`` (default 3 000 000; safeguard on the number of
  pushed labels -- if it is hit the best plan found so far, if any (also the
  one of the single-level search), is returned with ``stats.extra["status"]
  == "limit"`` and a message saying so; otherwise an infeasible plan whose
  message says that the limit was hit), ``eps`` (1e-9),
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
    (Reference for single-label dominance; the search uses the stronger, still
    exact, set dominance of :class:`_Envelope`.)
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


class _Envelope:
    """Settled label functions of one node, for the dominance test.

    * Constant labels (a single breakpoint: every label that has not charged
      yet, and labels whose stop cannot add energy) are kept in a maximum
      staircase over ``(t_feas, B)``: one bisection tells whether one of them
      dominates a new label (``t_feas' <= t_feas + eps``, ``B' >= B_max - eps``).
    * The other labels are merged into their *upper envelope*
      ``E(t) = max { B_L(t) : L settled, non-constant, t_feas(L) <= t }`` (each
      function capped at ``need[v]``), piecewise linear and non-decreasing, with
      upward jumps where a label with a higher value starts.  It is stored as a
      list of points ``(T[i], V[i])`` with ``T`` and ``V`` non-decreasing: linear
      between points with different times, a jump between two points with the
      same time (the value *at* that time is the later, higher one -- ``E`` is
      right-continuous), constant after the last point, undefined before the
      first.

    A new label is dominated if a constant label dominates it or if
    ``E(t) >= B(t) - eps`` for every ``t >= t_feas`` (``E`` defined from
    ``t_feas + eps`` on).  The second test -- *set dominance* -- is weaker than
    being dominated by one settled label (:func:`dominates`) and exact (module
    docs, section 4): a route of the new label that leaves the node at time
    ``t`` -- by an edge or by a stop at another station -- is matched by the
    settled label that attains ``E(t)``.  It is what keeps the label sets small
    with several speed levels: the labels of one charging event that drove
    different speed levels are each best somewhere (fast ones early, frugal ones
    once the charger saturates), so none dominates another, but most are below
    the envelope of a few.  (Constant labels are left out of ``E``: with several
    speed levels they form long staircases, and walking them in every merge
    costs more than the few labels that only a mixed envelope would prune.)

    ``E - B`` is linear between the union of both breakpoint sets, so the test
    evaluates ``E`` at the right end of ``t_feas``, at the left limits of the
    breakpoints of ``B`` and at the points of ``E`` inside the pieces of ``B``
    where ``E`` starts below the end value of the piece (``E`` is
    non-decreasing); after ``B``'s last breakpoint ``B`` is constant.  ``add``
    merges a function into ``E`` from its ``t_feas`` up to the time from which
    ``E`` is above it for good.
    """

    __slots__ = ("T", "V", "cb", "ct")

    def __init__(self) -> None:
        self.T: list[float] = []
        self.V: list[float] = []
        self.ct: list[float] = []  # constant labels: max staircase (t_feas, value)
        self.cb: list[float] = []

    # ------------------------------------------------------------ evaluation
    def right(self, t: float) -> float:
        """``E(t)`` (the right limit at a jump); ``V[0]`` before the first point."""
        T, V = self.T, self.V
        j = bisect_right(T, t) - 1
        if j < 0:
            return V[0]
        if j == len(T) - 1:
            return V[j]
        t1 = T[j]
        return V[j] + (V[j + 1] - V[j]) * (t - t1) / (T[j + 1] - t1)

    def left(self, t: float) -> float:
        """``lim E(s)`` for ``s -> t`` from below (``t > T[0]``); ``V[0]`` before the first point."""
        T, V = self.T, self.V
        i = bisect_left(T, t)
        if i == 0:
            return V[0]
        if i == len(T):
            return V[-1]
        t2 = T[i]
        if t2 == t:
            return V[i]
        t1 = T[i - 1]
        return V[i - 1] + (V[i] - V[i - 1]) * (t - t1) / (t2 - t1)

    def dominated(self, ts: Sequence[float], bs: Sequence[float], eps: float) -> tuple[bool, int]:
        """Is the label ``B`` (breakpoints ``ts``, ``bs``) dominated?  ``(answer, number of
        points of E inspected one by one)``."""
        t0 = ts[0]
        n = len(ts)
        ct = self.ct
        if ct:
            j = bisect_right(ct, t0 + eps)
            if j and self.cb[j - 1] >= bs[n - 1] - eps:
                return True, 0
        T = self.T
        if not T or T[0] > t0 + eps:
            return False, 0
        ea = self.right(t0)
        if ea < bs[0] - eps:
            return False, 0
        if n == 1:
            return True, 0  # E is non-decreasing, B constant
        left = self.left
        if left(ts[n - 1]) < bs[n - 1] - eps:
            return False, 0
        V = self.V
        m = len(T)
        scanned = 0
        for k in range(n - 1):
            a = ts[k]
            if k:
                ea = left(a)
                if ea < bs[k] - eps:
                    return False, scanned
            vb = bs[k + 1] - eps
            if ea >= vb:
                continue  # E is non-decreasing: above B on the whole piece
            b_ = ts[k + 1]
            dt = b_ - a
            if dt <= 0.0:
                continue
            slope = (bs[k + 1] - bs[k]) / dt
            base = bs[k] - eps
            idx = bisect_right(T, a)
            while idx < m:
                p = T[idx]
                if p >= b_:
                    break
                v = V[idx]
                if v >= vb:
                    break  # ... and from here on
                scanned += 1
                if v < base + slope * (p - a):
                    return False, scanned
                # E >= v from p on and B < v until it catches up: skip those points
                if slope > 0.0:
                    idx = bisect_left(T, a + (v - base) / slope, idx + 1)
                else:
                    break  # B is flat on this piece: E >= v >= B from here on
        return True, scanned

    def const_dominated(self, t: float, b: float, eps: float) -> bool:
        """:meth:`dominated` for a constant function ``b`` from ``t`` on."""
        ct = self.ct
        if ct:
            j = bisect_right(ct, t + eps)
            if j and self.cb[j - 1] >= b - eps:
                return True
        T = self.T
        return bool(T) and T[0] <= t + eps and self.right(t) >= b - eps

    # ------------------------------------------------------------ insertion
    def add(self, ts: Sequence[float], bs: Sequence[float]) -> None:
        """Add a settled label: to the staircase if it is constant, else ``E = max(E, B)``
        (``B`` defined from ``ts[0]`` on).

        Points of ``E`` that end up on a linear piece of ``B`` (where ``B`` is the
        maximum) are dropped, so that the envelope does not accumulate redundant
        points: its size stays proportional to the number of its kinks.
        """
        n = len(ts)
        if n == 1:
            _stair_max_add(self.ct, self.cb, ts[0], bs[0])
            return
        T, V = self.T, self.V
        t0 = ts[0]
        te = ts[n - 1]
        bend = bs[n - 1]
        m = len(T)
        i0 = bisect_left(T, t0)
        ot: list[float] = []
        ov: list[float] = []
        oc: list[int] = []  # piece of B a point lies inside (B the maximum there), else -1
        if m and T[0] <= t0:
            if T[0] < t0:
                ot.append(t0)
                ov.append(self.left(t0))
                oc.append(-1)
            er = self.right(t0)
        else:
            er = -INF
        b0 = bs[0]
        top = er if er > b0 else b0
        if not ov or top > ov[-1]:
            ot.append(t0)
            ov.append(top)
            oc.append(-1)
        # walk the union of E's points after t0 and B's breakpoints
        i = bisect_right(T, t0)  # next point of E
        k = 1  # next breakpoint of B
        pt, pe, pq = t0, er, b0  # previous union time, E there (right value), B there
        while True:
            if i < m and (k >= n or T[i] <= ts[k]):
                p = T[i]
            elif k < n:
                p = ts[k]
            else:
                break
            # E at p: left limit and right value (-inf where E is undefined); all
            # points of E up to the previous union time pt have been consumed
            if i < m and T[i] == p:
                el = V[i] if i else -INF  # (undefined just before T[0])
                i += 1
                while i < m and T[i] == p:
                    i += 1
                er_p = V[i - 1]
            elif i == 0:
                el = er_p = -INF
            elif i == m:
                el = er_p = V[m - 1]
            else:
                t1 = T[i - 1]
                el = er_p = V[i - 1] + (V[i] - V[i - 1]) * (p - t1) / (T[i] - t1)
            # B at p (continuous; constant after its last breakpoint)
            on_break = k < n and ts[k] <= p
            while k < n and ts[k] <= p:
                k += 1
            if k >= n:
                bq = bend
            else:
                t1 = ts[k - 1]
                dt = ts[k] - t1
                bq = bs[k] if dt <= 0.0 else bs[k - 1] + (bs[k] - bs[k - 1]) * (p - t1) / dt
            # crossing strictly inside (pt, p)?
            if pe > -INF and el > -INF and p > pt:
                d1 = pe - pq
                d2 = el - bq
                if (d1 < 0.0 < d2) or (d1 > 0.0 > d2):
                    # the envelope has a kink here: it must be a point (whatever its value)
                    tc = pt + (p - pt) * d1 / (d1 - d2)
                    if pt < tc < p:
                        vc = pq + (bq - pq) * (tc - pt) / (p - pt)
                        ot.append(tc)
                        ov.append(vc if vc > ov[-1] else ov[-1])
                        oc.append(-1)
            lo = el if el > bq else bq
            if lo < ov[-1]:
                lo = ov[-1]
            hi = er_p if er_p > bq else bq
            if lo > ov[-1] or ot[-1] != p:
                # a point strictly inside a piece of B, with B the maximum on both sides
                src = k if (not on_break and el <= bq and er_p <= bq) else -1
                if src >= 0 and len(oc) >= 2 and oc[-1] == src and oc[-2] == src:
                    ot[-1] = p  # the previous point lies on the same segment
                    ov[-1] = lo
                else:
                    ot.append(p)
                    ov.append(lo)
                    oc.append(src)
            if hi > ov[-1]:
                ot.append(p)
                ov.append(hi)
                oc.append(-1)
            pt, pe, pq = p, er_p, bq
            if p >= te and er_p >= bend:
                # E is above B for good: keep the rest of E unchanged
                break
            if k < n and i < m and er_p >= bq:
                # E stays above B at least until B reaches E(p) (both are
                # non-decreasing): copy E's points up to there in bulk
                if er_p >= bs[k]:
                    tstar = ts[k]
                else:
                    t1 = ts[k - 1]
                    tstar = t1 + (ts[k] - t1) * (er_p - bs[k - 1]) / (bs[k] - bs[k - 1])
                if T[i] >= tstar:
                    continue
                j2 = bisect_left(T, tstar, i)
                ot.extend(T[i:j2])
                ov.extend(V[i:j2])
                oc.extend([-1] * (j2 - i))
                i = j2
                pt = T[j2 - 1]
                pe = V[j2 - 1]
                t1 = ts[k - 1]
                dt = ts[k] - t1
                pq = bs[k] if dt <= 0.0 else bs[k - 1] + (bs[k] - bs[k - 1]) * (pt - t1) / dt
        T[i0:i] = ot
        V[i0:i] = ov


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
    es: Sequence[float] | None = None,
    stop: float = 0.0,
    inv_r: float = 0.0,
    order_by_potential: bool = True,
) -> _Outcome:
    """The CFP label-setting search (see the module docs).

    The potential is ``h(v, e) = hb[v] + rate * max(0, el[v] - e)`` plus, if
    ``es`` is given, the stop-count term ``stop * ceil(max(0, es[v] - e) *
    inv_r)`` where the linear part is active (exactly as
    :class:`~evroute.algorithms.labels.AStarPotential`); ``hb[v]`` is ``inf``
    for nodes that cannot reach the destination (they are never entered).
    ``el is None`` means ``h = 0`` (Dijkstra order).  The key of a label is
    ``min_t (t + h(v, B(t)))`` (section 4 of the module docs).  With
    ``order_by_potential=False`` the key is ``t_feas`` (Dijkstra order) and the
    potential only serves as a lower bound in the tests against
    ``upper_bound`` and the incumbent.  ``options`` replaces
    ``P.edge_options``; ``upper_bound`` is the trip time of some feasible route.

    *Performance devices that do not change the result:*

    1. the settled labels of a node live in an :class:`_Envelope` (set
       dominance, exact);
    2. a drive label that is not at the destination is dropped when it is
       generated if the plain algorithm would discard it when popped: if the
       *settled* labels dominate it as a constant (settled sets only grow), or if
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
    bounded = el is not None  # a potential (lower bound) is available
    astar = bounded and order_by_potential  # ... and orders the queue
    elv: Sequence[float] = el if el is not None else hb  # (only read when bounded)
    use_stop = bounded and es is not None and stop > 0.0 and inv_r > 0.0
    esv: Sequence[float] = es if (use_stop and es is not None) else elv
    R = 1.0 / inv_r if use_stop else INF
    ceil = math.ceil

    def hfull(v: int, e: float) -> float:
        """``h(v, e)`` with the stop-count term (A* only)."""
        d = elv[v] - e
        if d <= 0.0:
            return hb[v]
        k = hb[v] + d * rate
        if use_stop:
            d = (esv[v] - e) * inv_r
            if d > 0.0:
                k += stop if d <= 1.0 else stop * ceil(d)
        return k

    def key_min(v: int, k: float, bf: float, bmax: float, t0_: float, F, Fb_: float, cost_: float) -> float:
        """Key ``min_t (t + h(v, B(t)))`` of a non-constant label whose key at ``t_feas`` is ``k``
        (``B(t_feas) = bf``, ``B`` rising to ``bmax``, ``B(t) = x(t) - cost_`` with
        ``x(t) = F^-1(Fb_ + t - t0_)`` there).  Between the energies at which the stop
        count drops ``t + h`` is non-decreasing (Lemma 2), so only those energies
        ``es[v] - j R`` matter."""
        e_s = esv[v]
        if bmax <= bf or e_s <= bf or elv[v] <= bf:
            return k
        j = ceil((e_s - bmax) * inv_r)
        if j < 0:
            j = 0
        hv, ev = hb[v], elv[v]
        while True:
            e_j = e_s - j * R
            if e_j <= bf:
                return k
            t_j = t0_ + (F(e_j + cost_) - Fb_)
            d = ev - e_j
            kj = t_j + hv + (d * rate if d > 0.0 else 0.0) + j * stop
            if kj < k:
                k = kj
            j += 1
    nlev = len(P.speed_factors)
    Q = P.capacity
    # (no private floor for a zero reserve: "just enough" plans may arrive with -1e-15 kWh of
    # float noise, which the evaluator tolerates like every other energy check, TOL_KWH)
    emin = P.e_min
    cap = P.e_charge_cap
    e0 = P.e_init
    egoal = P.e_dest_min
    origin, dest = P.origin, P.destination
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

    # settled-label sets, one per node (also for labels that may not stop at the
    # station they are charging at: see "One set per node" in the module docs)
    sets: list[_Envelope] = [_Envelope() for _ in range(n)]
    # nodes with several stations: a stop there may be followed by a stop at
    # another station of the node, also one that starts below the reserve
    multi = [False] * n
    for v, lst in station_at.items():
        if len(lst) > 1:
            multi[v] = True
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
        hb0 = hfull(origin, e0) if bounded else 0.0
        h0 = hb0 if astar else 0.0
        if origin == dest:
            ta = arrival_time(None, cap, 0.0, 0.0, e0, emin, 0.0, Q, egoal, eps)
            if ta < best:
                best = ta
                best_rec = (0.0, 0.0, -1, -1, _ROOT, 0.0)
        if hb0 < best and hb0 <= ub_lim:
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
        charged = how <= -2  # created by a stop at station s of v
        if s < 0:
            bv = b - cost
            if bv > oc:
                bv = oc
            ts = [t0]
            bs = [bv]
        elif charged and b < inn - eps and multi[v]:
            # stop that starts below the reserve (only at the origin) at a node with
            # several stations: the vehicle cannot leave before t_feas, but it may
            # switch to another station of the node at any time from t0 on, so the
            # label function starts at (t0, b) ("extended" label, module docs)
            ts, bs = label_function(cfs[s], cap, t0, Fb, b, b, cost, oc, eps)
        else:
            ts, bs = label_function(cfs[s], cap, t0, Fb, b, inn, cost, oc, eps)
        bp_total += len(ts)

        # ------------------------------------ dominance by settled labels
        dom, nchk = sets[v].dominated(ts, bs, eps)
        checks_total += nchk
        if dom:
            pruned += 1
            continue
        i_new = len(records)
        records.append((t0, Fb, s, par, how, sw))
        sets[v].add(ts, bs)
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
                if bounded:
                    d = elv[w] - bf
                    if d > 0.0:
                        k2 = tf2 + hb[w] + d * rate
                        if use_stop:
                            d = (esv[w] - bf) * inv_r
                            if d > 0.0:
                                k2 += stop if d <= 1.0 else stop * ceil(d)
                                if s >= 0:  # the stop count may drop while charging at s
                                    xt2 = cap if cap >= b else b
                                    bmu = xt2 - cost2
                                    if bmu > out2:
                                        bmu = out2
                                    k2 = key_min(w, k2, bf, bmu, t02, time_at[s], Fb, cost2)
                    else:
                        k2 = tf2 + hb[w]
                    kb = k2  # lower bound on every route through the label
                    if not astar:
                        k2 = tf2
                else:
                    kb = k2 = tf2
                if kb >= best or kb > ub_lim:
                    pruned_bound += 1
                    continue
                if w == dest:
                    ta = arrival_time(cfs[s] if s >= 0 else None, cap, t02, Fb, b, inn2, cost2, out2, egoal, eps)
                    if ta < best:
                        best = ta
                        best_rec = (t02, Fb, s, i_new, code, 0.0)
                        if kb >= best:
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
                    if sets[w].const_dominated(tf2, bm2, eps):
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
        if is_station[v]:
            last = s if charged else -1  # never the same station twice in a row
            mv = multi[v]
            for s2 in station_at[v]:
                if s2 == last:
                    continue
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
                    if charged and tj <= t0:
                        # nothing charged at s yet: switching to s2 directly is no worse
                        continue
                    below = bj < emin_eps  # (only at the origin)
                    if below and emin > cap_lim:
                        continue  # the reserve can never be reached
                    t0n = tj + ((wc if static else wait_s(s2, tj)) + su)
                    Fbn = F2(bj)
                    kap = t0n - Fbn
                    if kap >= kmin - eps:
                        pruned_kappa += 1
                        continue  # dominated by an earlier sibling
                    kmin = kap
                    if not below or mv:
                        # (an extended label below the reserve may switch again from t0n on)
                        tfn = t0n
                        bf = bj
                    else:  # below the reserve, no other station: charge up to it first
                        tfn = t0n + (F2(emin) - Fbn)
                        bf = emin
                    generated += 1
                    if bounded:
                        kb = tfn + hfull(v, bf)
                        if use_stop:
                            bmu = cap if cap >= bj else bj
                            if bmu > Q:
                                bmu = Q
                            kb = key_min(v, kb, bf, bmu, t0n, F2, Fbn, 0.0)
                        kn = kb if astar else tfn
                    else:
                        kb = kn = tfn
                    if kb >= best or kb > ub_lim:
                        pruned_bound += 1
                        continue
                    if v == dest:
                        ta = arrival_time(cf2, cap, t0n, Fbn, bj, emin, 0.0, Q, egoal, eps)
                        if ta < best:
                            best = ta
                            best_rec = (t0n, Fbn, s2, i_new, -2 - s2, tj)
                            if kb >= best:
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
        is hit, the best plan found so far (if any; with several speed levels
        also the one of the single-level search) is returned with
        ``stats.extra["status"] == "limit"`` and a note in ``plan.message``;
        optimality is then not proven.  If no route is known, the plan is
        infeasible and its message says that the label limit was hit.
    ``eps``
        tolerance of the dominance test and of feasibility comparisons
        (default 1e-9 kWh / s).

    Labels are settled in Dijkstra order (key ``t_feas``).  With several
    speed levels a first search restricted to one level per edge provides an
    upper bound, and labels whose lower bound -- the key of
    :class:`CFPAStarPlanner` -- exceeds it are not pushed; the order and the
    optimum are unaffected.
    ``exact_switching``
        ``True`` (default): besides the breakpoints of a label function also
        try the crossings of upward power breaks and wait-profile knots as
        stop times (see the module docs); this makes the algorithm exact for
        arbitrary piecewise-linear charging functions and time-dependent
        waits.  ``False`` gives the textbook algorithm, exact only for
        concave charging functions and static waits.

    Guarantee (``exact = "exact"``): the least trip time over all routes with
    any real charging amounts up to ``e_charge_cap`` (consecutive stops at
    different stations of a node included) -- see the module documentation.
    The objective is always the trip time, also if ``P.objective == "cost"``;
    then the plan is the
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
        """``(hb, el, rate, need, name)``: the linear part ``h = hb + rate * max(0, el - e)`` of
        the queue potential and ``need[v]``, the energy that suffices to finish along the
        fastest tree (see :meth:`_potential_full` for the stop-count term).

        Dijkstra order: ``h = 0`` on nodes that can reach the destination
        (``hb = inf`` elsewhere), no energy term.
        """
        return self._potential_full(P)[:5]

    def _potential_full(self, P: PreparedScenario) -> tuple:
        """``(hb, el, rate, need, name, es, stop, inv_r)``: :meth:`_potential` plus the
        stop-count term ``stop * ceil(max(0, es - e) * inv_r)`` (``es = None``: none)."""
        tl, _ = dijkstra(P.net, P.destination, min_level_weights(P, "time"), reverse=True)
        need = completion_energy(P, tl, "time")
        return [0.0 if d < INF else INF for d in tl], None, 0.0, need, "none (Dijkstra order)", None, 0.0, 0.0

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
        hb, el, rate, need, pot_name, es, stop, inv_r = self._potential_full(P)
        t1 = time.perf_counter()
        ub = INF
        pre: _Outcome | None = None
        bound = (hb, el, rate, es, stop, inv_r)  # potential of the main search
        order = True
        if any(len(o) > 1 for o in P.edge_options):
            # Several speed levels: first solve the problem restricted to one level per
            # edge (the one minimising time + rate * energy).  Its route is a route of the
            # full problem, so its trip time bounds the optimum and the full search never
            # pushes labels whose lower bound is larger -- the optimum is unchanged (see _search).
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
                es=es,
                stop=stop,
                inv_r=inv_r,
            )
            ub = pre.best
            if el is None and ub < INF:
                # Dijkstra order: the main search still settles labels by t_feas, but tests
                # them against the bound with the (admissible) potential of CFP-A*
                tight = AStarPotential.tight(P, objective="time")
                bound = (list(tight.base), list(tight.e_lin), tight.rate, list(tight.e_stop), tight.stop,
                         tight.inv_r)
                order = False
        t_ub = time.perf_counter()
        res = _search(
            P,
            hb=bound[0],
            el=bound[1],
            rate=bound[2],
            need=need,
            max_labels=max_labels,
            eps=eps,
            exact_switching=exact_switching,
            trace=tr,
            upper_bound=ub,
            es=bound[3],
            stop=bound[4],
            inv_r=bound[5],
            order_by_potential=order,
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
                "upper_bound_status": pre.status if pre is not None else None,
                "bound_potential": None if order else "tight coupled energy-deficit + stop count",
            },
        )
        best, records, rec = res.best, res.records, res.best_rec
        from_pre = False
        if rec is None and res.status == "limit" and pre is not None and pre.best_rec is not None:
            # the full search ran out of labels: the single-level route is the best one known
            best, records, rec = pre.best, pre.records, pre.best_rec
            from_pre = True
        if rec is None:
            return infeasible_plan(P, self.name, _infeasibility_reason(P, res, hb, max_labels), stats, tr)
        edges, levels, charges = _reconstruct(P, records, rec, best)
        plan = evaluate(
            P,
            PlanDecision(edges, levels, charges),
            algorithm=self.name,
            internal_objective=best,
            stats=stats,
            trace=tr,
        )
        stats.extra["time_s"] = best
        if P.objective != "time":
            # the plan is time-optimal, not cost-optimal: claim only what was measured
            plan.internal_objective = plan.objective_value
        if res.status == "limit":
            plan.message = (
                f"label limit reached (max_labels={max_labels:,}, {res.pushes:,} labels pushed) before the "
                "search was complete; best route found so far"
                + (" (by the search restricted to one speed level per edge)" if from_pre else "")
                + ", optimality not proven; increase max_labels"
            )
        return plan


class CFPAStarPlanner(CFPPlanner):
    """CFP guided by a consistent energy-deficit potential (A*).

    The key of a label is the minimum of ``t + h(v, B(t))`` over the label --
    attained at ``t_feas`` or where the stop count of ``h`` drops (Lemma 2 of
    the module docs) -- a lower bound for the whole label, so the result is
    identical to :class:`CFPPlanner` while far fewer labels are settled.
    Option ``potential`` selects ``h``:

    ``"tight"`` (default)
        :meth:`AStarPotential.tight`: ``h = B(v) + r max(0, E_v - e)`` plus the
        overhead of the unavoidable stops, where the required energy ``E_v``
        is measured on the *same* path and speed level as the driving time
        (``B``) and ``r`` is the best charging rate in the usable energy
        range.  Never weaker than ``"basic"``; with several speed levels the
        difference is large.
    ``"basic"``
        ``LowerBounds.h_time`` of :mod:`evroute.search`:
        ``T_lb(v) + 3600 / P_max * max(0, E_lb(v) + e_goal - e)``.

    Other options as for :class:`CFPPlanner`.
    """

    name = "cfp-astar"
    title = "CFP-A*"
    description = (
        "CFP with an A* key: earliest feasible time plus a consistent lower bound "
        "(coupled driving time + energy deficit at the best usable charging rate). "
        "Same optimum as CFP, far fewer labels."
    )

    def _potential_full(self, P: PreparedScenario) -> tuple:
        kind = self.options.get("potential", "tight")
        lb = LowerBounds(P)
        if kind == "basic":
            g = lb.e_goal
            el = [e + g if e < INF else INF for e in lb.energy_lb]
            need = completion_energy(P, lb.time_lb, "time")
            return (list(lb.time_lb), el, lb.sec_per_kwh, need, "basic energy-deficit (LowerBounds.h_time)",
                    None, 0.0, 0.0)
        if kind == "tight":
            pot = AStarPotential.tight(P, lb, "time")
            return (list(pot.base), list(pot.e_lin), pot.rate, list(pot.need),
                    "tight coupled energy-deficit + stop count", list(pot.e_stop), pot.stop, pot.inv_r)
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
            "any route to the destination was found -- this does not mean that no route exists; "
            "increase max_labels"
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
