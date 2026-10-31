# Algorithms and correctness arguments

Notation follows [`model.md`](model.md).  Energies are in kWh, times in
seconds.  $\underline e$ is the reserve, $\bar e$ the charging cap, $Q$ the
capacity, $e_d$ the destination requirement and $P_{\max}$ the highest
effective charging power of any station.

## At a glance

| name | idea | guarantee |
|---|---|---|
| `naive` | fastest path, battery ignored | none - shows *where* the battery runs out |
| `greedy` | in-car navigator: drive the fastest path; when the battery is short, detour to the reachable station with the least time-to-station + lower bound to the goal, charge to 80 % (DC) / 100 % (AC), repeat | heuristic |
| `station-graph` | Dijkstra on a meta-graph of stations; every stop charges to a fixed target (default 100 %), stretches use the fastest speed level (Storandt & Funke 2012 style) | optimal only on its meta-graph |
| `genetic` | deterministic, seeded genetic algorithm over station sequences and target SoCs, decoded with exact "just enough" energies | heuristic |
| `bls` | label setting on the battery-expanded state space (node, energy) in Dijkstra order | **exact** for charging targets on the grid (2 % SoC + charging-curve breakpoints) |
| `bls-astar` | BLS + a consistent energy-deficit A\* potential (default planner) | same optimum as `bls`, far fewer labels |
| `cfp` | charging-function propagation (Baum et al.): labels are functions of time, charging amounts stay continuous | **exact** fastest route with arbitrary charging amounts |
| `cfp-astar` | CFP with the A\* key | same optimum as `cfp` |
| `pareto` | multi-criteria label setting (Martins, NAMOA\*-style pruning) over time, money, wear | **exact** Pareto front on the target grid |

Exact planners minimise the trip time or the generalised cost
`VoT·T + money + w·wear` (BLS, BLS-A\*, Pareto; CFP is a time-objective
algorithm). All of them handle time-dependent queues, several speed levels,
regeneration and consecutive stops at different chargers of one site.  With
a charging tariff whose prices depend on the clock, BLS and BLS-A\* (cost)
and Pareto also consider waiting at a station until a price change, which
is provably enough; the time objective never waits.  Not sure which planner
to use? See the [guide](guide.md#12-which-planner-should-i-use).

### How BLS-A\* works

A *label* is a partial route summarised by `(t, e)` - trip time and battery
energy at a node, with the energy kept **continuous**. Driving an edge moves
`(v, e) → (w, min(e − c, Q))`; charging at station `j` to a target `y` costs
`wait_j(t) + setup_j + F_j(y) − F_j(e)` seconds, with the exact charging
function `F_j`. A label dominates another at the same node if it is no later
and has no less energy (the cost objective adds money and wear). Every
transition is monotone and the queues are FIFO, so pruning dominated labels
is exact.

The A\* key is `t + h(v, e)` with

```
h(v, e) = T_lb(v) + (3600 / P_max) · max(0, E_lb(v) + e_goal − e)   [+ unavoidable-stop overhead]
```

that is, the fastest remaining driving time plus the time needed to charge
the energy deficit at the best possible power. `h` is consistent on the battery state
graph (the deficit cannot shrink along a road, and charging `ΔE` takes at
least `3600 ΔE / P_max` seconds) and non-increasing in `e`, so a dominating
label is always popped first and the first label popped at the destination
is optimal. The default *tight* potential measures time and energy on the
same path and counts unavoidable stops. Proofs: Sections
[2](#2-bls--battery-constrained-label-setting) and
[3](#3-a-consistent-heuristic-for-the-battery-state-space).

### How CFP works (continuous charging)

BLS fixes *how much* to charge to a grid. CFP defers that decision: a label
stores the last station `j`, the time and energy at which charging there
began and the consumption profile `(in, cost, out)` of the road since then.
Its *function* `B(t)` - the energy available at the current node if the car
leaves `j` so that it arrives at time `t` - is piecewise linear and
non-decreasing. Along an edge only the profile changes (closed form); at a
new station the label spawns stop labels at the breakpoints of `B`, which is
where an optimal switch from charging at `j` to charging at the new station
lies (Lemma 3; plus the upward power breaks of non-concave curves and the
knots of time-dependent waits). Dominance compares a label against the upper envelope of the
settled labels of its node. The first destination entry popped is the
fastest route over **all** charging amounts. Proofs and the extensions
(non-concave curves, time-dependent queues, consecutive stops):
[Section 4](#4-cfp--charging-function-propagation-continuous-charging).

## 1. The battery-expanded state space

A *state* is a pair $(v, x)$ of a node and the energy in the battery.  Driving
edge $e = (v, w)$ at speed level $k$ (speed adaptation as in Baum et al.
2020) moves $(v, x) \to (w, \delta_{e,k}(x))$
in $t_{e,k}$ seconds, provided $x \ge \underline e$ and
$\delta_{e,k}(x) \ge \underline e$.  Charging at station $j$ located at $v$
moves $(v, x) \to (v, y)$, $y > x$, in $W_j(t) + \sigma_j + F_j(y) - F_j(x)$
seconds where $t$ is the arrival time.  A route is a path in this (infinite)
state graph from $(o, x_0)$ to some $(d, x)$ with $x \ge e_d$.

*Consecutive stops.*  Several stops may follow each other at one node as
long as consecutive stops use different stations: after a stop at $j$ the
next stop at the same node uses a station $j' \ne j$ (e.g. a fast DC charger up to the
energy where its power falls below that of an AC charger next to it, then the
AC charger -- cheaper, or even faster near full in the cold).  A second stop
at $j$ itself is never useful: it pays $W_j + \sigma_j$ again for energy the
first stop could have charged, and $F_j$, the money and the wear are additive
along the charging curve.  (The one exception is a clock-dependent tariff,
where a second session at $j$ in the *other* price period can pay off,
Section 6.)  The evaluator checks the reserve on arrival and on
departure only, so at the origin with $x_0 < \underline e$ a stop may end
below the reserve when another stop at the same node follows.  This is
exactly the set of plans `evaluate()` accepts (up to its float tolerance, see
below); earlier versions allowed one stop per node visit and missed such
routes (BUG-3 of the cross-validation suite).

*Minimum stop SoC (the floor rule).*  Drivers do not stop for "+2 %":
parking, plugging in and paying cost the same for a small top-up as for a real
charge.  `Preferences.min_stop_soc` sets a floor
$\varphi = \min(\texttt{min\_stop\_soc}, \mu)\,Q$ (`P.e_stop_min`; $\varphi = 0$
switches the rule off) and restricts the charging transition: a stop at
$(v, x)$ may end at $y$ only if

$$ x \ge \varphi \quad\text{or}\quad y \ge \varphi, $$

i.e. a stop that starts below the floor ends at it or above, one that starts at
or above it may add any amount.  Consecutive stops at a node are tested one by
one; since every stop ends at $\varphi$ or above, the rule binds only the first
stop of a node visit.  `evaluate()` reports a stop that starts *and* ends below
$\varphi - $ `TOL_KWH`.  Vehicles usually reach a stop near the reserve, so a floor
of "reserve + $\Delta$" behaves like "charge at least $\Delta$" in practice.

*Feasibility tolerance.*  The guarantees below are stated for exact
arithmetic.  `evaluate()` accepts an energy shortfall of up to
`TOL_KWH` $= 10^{-6}$ kWh at every reserve, arrival, cap and empty-battery
check, to absorb the rounding of float sums of edge consumptions.  The
exact planners (BLS, CFP, Pareto) do not use this slack: they treat a state
as feasible up to their own `eps` $= 10^{-9}$ kWh only.  On a knife-edge
instance `evaluate()` can therefore accept a plan that is up to $10^{-6}$ kWh
short: one faster than the exact optimum (e.g. without a stop that would add
a few joules), or one on an instance where the exact planners report that no
route exists.  Such plans are infeasible in exact arithmetic; the slack is
float-noise tolerance, not part of the model.

**Lemma 1 (monotonicity).** Let two partial routes reach the same node $v$
with $(t_1, x_1)$ and $(t_2, x_2)$, $t_1 \le t_2$ and $x_1 \ge x_2$.  Any
completion of the second is feasible for the first and arrives no later.

*Proof.* $\delta_{e,k}$ is non-decreasing in $x$, so energies stay ordered
along any common continuation and the reserve constraints hold for the first
whenever they hold for the second.  Charging to a target $y$ from $x_1 \ge
x_2$ takes no longer ($F_j$ is increasing).  Driving times do not depend on
the clock, and waiting satisfies FIFO ($t + W_j(t)$ non-decreasing), so the
first route is never behind at any later event.  $\square$

For the generalised-cost objective $g = \mathrm{VoT}\cdot T + m$ (with $m$
the weighted money and wear spent so far) the same argument holds for
$(g, x)$ when waits are static.  With time-dependent waits FIFO only says that
the earlier label leaves a queue *no later*: a falling queue can absorb its
whole head start, so comparing $(g, t, x)$ is **not** sound (an earlier label
that paid more money can lose its time advantage while its money disadvantage
persists).  The exact rule compares $(m, t, x)$: $m_1 \le m_2$, $t_1 \le t_2$,
$x_1 \ge x_2$ -- every completion of label 2 is then matched by label 1 with
no larger final $T$ (FIFO) and no larger final $m$ (money and wear increments
are no larger from a higher energy), hence no larger $g$; and the dominating
label has $g_1 \le g_2$, i.e. a key no larger.  BLS/BLS-A\* are therefore exact
for the cost objective with time-dependent waits as well (regression
`test_cost_dominance_with_time_dependent_waits_regression`, where the rule
$(g, t, x)$ discards the slow cheap first charge of the optimum); the Pareto
planner keeps $(t, M, W, x)$ apart and agrees.  The price is effort: the 3-D
label sets are much larger than the 2-D ones (with three speed levels, or on
the Turkey presets with their time-dependent queues, the cost objective can
exceed the default label budget), so `max_labels` and the A\* potential matter
more there.

**Lemma 1F (the floor keeps monotonicity).**  Lemma 1 and its cost versions
hold under the floor rule: if $t_1 \le t_2$ and $x_1 \ge x_2$ (and $m_1 \le m_2$
or $g_1 \le g_2$), every completion of the second partial route that obeys the
rule is matched by one of the first that obeys it, arrives no later and costs no
more.

*Proof.* Follow the second route's decisions.  Energies stay ordered
($x_1 \ge x_2$ at every later event, by Lemma 1).  At a stop of the second route
to a target $y$: if $y \le x_1$ the first route skips it.  Otherwise, if
$x_1 \ge \varphi$ every target above $x_1$ is allowed; if $x_1 < \varphi$ then
$x_2 \le x_1 < \varphi$, so the second route's stop was bound by the floor and
$y \ge \varphi$ -- allowed for the first route too.  Charging from the higher
energy to the same $y$ takes no longer and costs no more money and wear (the
cumulative functions telescope), and with FIFO queues it starts no later.
$\square$

The floor is the strongest rule of this kind that keeps the lemma.  A rule
"add at least $\Delta$ per stop" breaks it: a label with 70 % that needs 75 % for
the rest of the trip must then charge 70 → 90 %, which in the slow top range of
the charging curve can take longer than 20 → 75 % in the fast range for a label
with 20 % -- more energy at the same time would no longer dominate, and every
label-setting argument below would fail.  With the floor, every exact planner
keeps its guarantee: the target sets of BLS and Pareto contain $\varphi$
(Section 2), CFP folds the mandatory part of a stop into its label (Section 4),
and all lower bounds and A\* potentials stay valid: they are computed without
the floor, and their consistency proofs hold for a charging stop to *any*
target (they never use the target set), so they are admissible for every route
that obeys the rule and consistent on every transition it allows, the stop to
the extra target $\varphi$ included.  With $\varphi = 0$ every planner runs its
earlier code path and the results are bit-identical.

*Monotonicity in the floor.*  For CFP, which charges continuously, the floor
is a pure restriction: the routes it allows are routes without it, so a higher
floor never gives a faster optimum.  For BLS and Pareto it is not: they are
exact over their target set, and the floor adds $\varphi$ to it.  A floor that
is not a target anyway can therefore *improve* their optimum -- the stop to
$\varphi$ may beat every stop to a grid target, and that plan is feasible
without the floor as well (`test_an_off_grid_floor_can_improve_bls_and_pareto`:
24 272 s without a floor, 23 256 s with a floor of 39.5 % on a 12.5 % grid).  A
higher floor never gives a better BLS or Pareto optimum when it lies on the
`charge_step_soc` grid, where it adds no target.

## 2. BLS — battery-constrained label setting

BLS restricts charging targets to a finite set $\mathcal Y_j$ (the
`charge_step_soc` grid united with the breakpoints of $F_j$ below $\bar e$) and
runs a multi-criteria Dijkstra over labels $(t, x)$ per node, pruning by
Lemma 1.  A label $A$ created by a stop at $j$ may stop again at every other
station of its node.  One Pareto set per node is exact although $A$ cannot
stop at $j$ next: if $A$ dominates a label $B$, a stop of $B$ at $j$ to a
target $y > x_A$ is matched by $A$'s parent $C$ (which just stopped at $j$)
charging directly to $y$ ($t_C + W_j(t_C) + \sigma_j + F_j(y) - F_j(x_C) = t_A - F_j(x_A) +
F_j(y) \le t_B + W_j(t_B) + \sigma_j + F_j(y) - F_j(x_B)$, likewise for money
and wear), and one to $y \le x_A$ by $A$ itself.  With a floor $\varphi > 0$ the
target set also contains $\varphi$ itself (unless a target lies within
$10^{-9}$ kWh of it), a stop from $x < \varphi$ only goes to targets
$y \ge \varphi$, and the matching still works: if $x_C < \varphi$ then $A$'s own
stop was bound by the floor, $x_A \ge \varphi$ and $y > x_A$ is allowed for $C$
(Lemma 1F).  Energies are kept *continuous* — consumption is never rounded, so
there is no accumulated discretisation error along long paths (a classic
problem of SoC-bucket formulations).

**Theorem 1.** BLS returns a route of minimum trip time among all routes whose
charging targets lie in $\mathcal Y$ (consecutive stops at different stations
of a node included; with a minimum stop SoC, among those that obey the floor
rule).

*Proof sketch.* Every transition has non-negative duration, so labels are
settled in non-decreasing time.  By Lemma 1 a label dominated by a settled
label at the same node can be discarded without losing any optimal
completion.  The first settled label at $d$ with $x \ge e_d$ therefore has
minimum time.  Termination: the number of non-dominated labels per node is
finite because energies reachable with finitely many targets on simple
segments form a finite set; dominated cycles are pruned.  $\square$

As the grid is refined, the BLS optimum converges to the continuous optimum
computed by CFP (experiment `grid-resolution`).

## 3. A consistent heuristic for the battery state space

Let $T_{lb}(v)$ be the minimum driving time from $v$ to $d$ (reverse Dijkstra
on $\min_k t_{e,k}$) and $E_{lb}(v)$ the minimum energy (reverse Bellman-Ford
on $\min_k c_{e,k}$; negative weights occur, negative cycles cannot).  Define

$$ h(v, x) = T_{lb}(v) + \frac{3600}{P_{\max}}\,\big(E_{lb}(v) + e_d - x\big)^+ . $$

**Theorem 2.** $h$ is admissible and consistent on the battery state graph,
and non-increasing in $x$.

*Proof.* *Drive transition* $(v, x)\to(w, x')$ with $x' = \delta_{e,k}(x) \le x - c_{e,k}$:
the first term satisfies $T_{lb}(v) \le t_{e,k} + T_{lb}(w)$.  For the second,
$E_{lb}(v) \le c_{e,k} + E_{lb}(w)$, hence
$E_{lb}(v) + e_d - x \le E_{lb}(w) + e_d - (x - c_{e,k}) \le E_{lb}(w) + e_d - x'$;
the deficit cannot shrink along a drive edge.  Summing,
$h(v, x) \le t_{e,k} + h(w, x')$.
*Charge transition* $(v, x)\to(v, y)$ of duration $\tau \ge 3600 (y - x)/P_{\max}$:
the first term is unchanged and the second decreases by at most
$3600 (y - x) / P_{\max} \le \tau$.
*Goal:* at $(d, x)$ with $x \ge e_d$, $h = 0$.  Consistency with $h = 0$ at goal
states implies admissibility.  Monotonicity in $x$ is immediate.  $\square$

The same construction with $C_{lb}(v)$ (generalised driving cost) and the
cheapest possible cost per charged kWh,
$\mathrm{VoT}\cdot 3600/P_{\max} + w_m \min \pi/\eta_c + w_w \min\omega$,
gives a consistent heuristic for the generalised cost.  This *basic*
potential is `potential="basic"` of BLS-A\* (`LowerBounds.h` in `search.py`).

**The default ("tight") potential.**  BLS-A\* and CFP-A\* use a stronger
bound of the same shape (`AStarPotential.tight` in `algorithms/labels.py`):

$$ h(v, x) = T_{lb}(v) + r\,\big(E^{lin}_v - x\big)^+
   + \sigma \left\lceil \frac{\big(E_{lb}(v) + e_d - x\big)^+}{R} \right\rceil . $$

* $r \ge 3600/P_{\max}$ is the cheapest time (or generalised cost) per
  charged kWh over the stations and SoC bins the battery can actually be
  charged in (for the cost objective time, money and wear are minimised
  *jointly* per station and bin).
* $E^{lin}_v = \max\big(E_{lb}(v) + e_d,\ e_d + (W(v) - T_{lb}(v))/r\big)$,
  where $W$ is the reverse shortest-path distance for the coupled edge weight
  $\min_k (t_{e,k} + r\,c_{e,k})$: time and energy are measured on the *same*
  path and speed level, so the bound is never weaker than the basic one.
* $\sigma$ is the smallest overhead of one stop (set-up plus minimum wait)
  and $R$ the most energy one stop can add, so the last term counts the stops
  that the energy deficit makes unavoidable.

For the cost objective $T_{lb}$, $W$ and $\sigma$ are replaced by their
generalised-cost counterparts ($C_{lb}$, the coupled cost weight
$\min_k(\mathrm{cost}_{e,k} + r\,c_{e,k})$ and $\mathrm{VoT}\cdot\sigma$).

*Admissible:* a completion that drives with time $b$ and consumption $C$ and
charges $D$ kWh in $m$ stops has $D \ge C + e_d - x$ (regeneration is clamped),
$m \ge \lceil D/R \rceil$, $b + rC \ge W(v)$, $b \ge T_{lb}(v)$ and
$C \ge E_{lb}(v)$, hence it costs at least $h(v, x)$.  *Consistent:* along a
drive edge both linear parts are shortest-path potentials and the stop term
can only grow; a stop adding $D \le R$ kWh lowers the linear part by at most
$rD$ and the stop term by at most $\sigma$ -- never more than the stop costs.
Every part is non-increasing in $x$, so Lemma 2 below applies unchanged.

**Lemma 2 (A\* with dominance).** If $h$ is consistent and non-increasing in
$x$, a label that dominates another at the same node has a key that is not
larger.  Hence, when labels are expanded in key order, a dominated label is
never expanded before its dominator, and pruning popped labels against the
settled set is exact.  *Proof:* $t_1 \le t_2$, $x_1 \ge x_2$ ⇒
$t_1 + h(v, x_1) \le t_2 + h(v, x_2)$.  $\square$

**Corollary.** BLS-A\* returns the same objective value as BLS (verified on
random instances by `tests/test_labels.py`) while settling fewer labels.

## 4. CFP — charging function propagation (continuous charging)

BLS fixes charging amounts to a grid.  CFP (Baum et al. 2015, 2019) instead
*defers* the decision of how long to charge at the last station until it is
known what the rest of the route needs.

**Consumption profiles.** For a path segment starting at a station with
departure energy $x$, the arrival energy is
$f(x) = \min(x - \mathrm{cost},\ \mathrm{out})$ for $x \ge \mathrm{in}$
(infeasible otherwise).  Profiles compose in closed form:
appending an edge with consumption $c$ gives
$\mathrm{in}' = \max(\mathrm{in}, \underline e + \mathrm{cost} + c)$,
$\mathrm{cost}' = \mathrm{cost} + c$,
$\mathrm{out}' = \min(\mathrm{out} - c, Q)$.

**Labels as functions.** A label at node $v$ stores the last station $j$,
the base time $t_0$ (arrival at $v$ without extra charging at $j$), the energy
$b$ at which charging at $j$ began and the profile from $j$ to $v$.  Spending
$t - t_0$ extra seconds at $j$ yields

$$ B(t) = f\big(\min(F_j^{-1}(F_j(b) + t - t_0),\ \bar e)\big), $$

a piecewise-linear, non-decreasing function of the arrival time (concave when
the charging power does not increase with the energy).

**Lemma 3 (switching at breakpoints).** Let the waits be static and let the
charging power of the new station $j'$ not increase with the energy
($F_{j'}^{-1}$ concave).  Then, when arriving at $j'$, the best moment to
stop charging at $j$ and start charging at $j'$ lies at a breakpoint of $B$.
This includes a station $j' \ne j$ at the node of $j$ itself (consecutive
stops): then $B$ is the label's own charging curve at $j$, and its
breakpoints are the energies where the power of $j$ changes -- e.g. where a
derated DC charger falls below the AC charger next to it.

*Proof.* For a fixed final time $t$, switching at $t'$ gives
$\Phi(t') = F_{j'}^{-1}\big(F_{j'}(B(t')) + t - t' - W_{j'} - \sigma_{j'}\big)$.
On a linear piece of $B$ with slope $\beta$,
$\mathrm{sign}\,\Phi'(t') = \mathrm{sign}\big(\beta - p_{j'}(B(t'))\big)$
where $p_{j'}(B(t'))$ is the charging rate at $j'$ at the current energy.
Concavity of $F_{j'}^{-1}$ makes $p_{j'}$ non-increasing in energy while
$B(t')$ increases, so $\Phi'$ changes sign at most once, from negative to
positive: $\Phi$ is quasi-convex on the piece and attains its maximum at an
endpoint.  $\square$

Both conditions matter.  Where $B(t')$ crosses an energy at which the power
of $j'$ *increases* -- the ramp-up at low SoC of every vehicle preset
(`model.md` §4) -- $p_{j'}$ jumps up, $\Phi'$ can turn from positive to
negative and $\Phi$ has a local maximum inside the piece; a time-dependent
wait adds a $-W_{j'}'$ term that jumps at the knots of the wait profile.  Cutting the pieces of $B$ at these points restores the
argument, so the *extended candidate set* -- breakpoints of $B$, crossings of
the upward power breaks of $j'$ and wait-profile knots -- is exact in general
(Lemma 1 of `cfp.py`, option `exact_switching=True`, the default).  With the
breakpoints alone (`exact_switching=False`) CFP misses the optimum by more
than 10 s on a line with a curve that ramps up
(`test_nonconcave_charging_curve_needs_upward_break_candidates`).

**Key.** Let $t_{feas}$ be the earliest time at which $B$ is defined.  The
queue key is $t_{feas}$ (CFP) or $\min_t\, t + h(v, B(t))$ (CFP-A\*).
While the label charges at $j$ with energy $x$, $B'(t) \le p_j(x)/3600$, and
the linear part of $h$ falls by at most $r$ per kWh, the rate of the
potential: $3600/P_{\max}$ for the basic one, and for the tight one the best
rate over the energies the battery can be charged in (Section 3).  Every
energy $x$ of a stop lies in that range, so $r\,p_j(x)/3600 \le 1$ ($r$ can
exceed $3600/P_{\max}$, e.g. when a high reserve excludes the bins of peak
power, so $B' \le P_{\max}/3600$ alone would not suffice).  Hence
$t + h(v, B(t))$ is non-decreasing in $t$ as long as
the stop-count term of the tight potential (Section 3) is constant; it can
drop inside a label (the vehicle keeps charging in a stop it has already paid
for), so the minimum is taken over $t_{feas}$ and the times at which $B$ reaches
the energies where the count drops (closed form via $F_j$).  The key is a
lower bound for every route of the label and never decreases along edge
extensions and stops (consistency of $h$).  With several speed levels CFP
first solves the problem restricted to one level per edge; its trip time is an
upper bound, and no label whose lower bound exceeds it is pushed (plain CFP
keeps the Dijkstra order and uses the CFP-A\* key only for this test).

**Dominance (set dominance).** A label is discarded when the *upper envelope*
of the settled labels of its node lies above it: $\max_{L'} B_{L'}(t) \ge
B(t)$ for all $t \ge t_{feas}$, the maximum over the settled labels with
$t_{feas}(L') \le t$ -- checked exactly on the union of the breakpoints
(everything is piecewise linear).  It is exact: a route of the label that
leaves the node at time $t$ is matched by the settled label attaining the
envelope at $t$, whose extensions and (by Lemma 3) stop labels were generated.
Single-label dominance ($B_1 \ge B_2$ everywhere) is the special case of a
one-label envelope and was used by earlier versions; with several speed
levels it is far too weak (the labels of one charging event that drove
different speed levels are each best somewhere: fast ones early, frugal ones
once the charger saturates), and CFP ran out of labels on the Turkey presets.
Constant labels (not charged yet) are compared one by one through a
staircase, the others through their envelope.

*Consecutive stops.*  A label created by a stop at $j$ spawns stop labels for
every *other* station $j' \ne j$ of its node, at the switch candidates of its
own function (Lemma 3) -- the model of Section 1 -- and never one for $j$
itself: continuing to charge at $j$ is the label's own function and never
worse than paying $W_j + \sigma_j$ again.  It also skips the candidate at its
own $t_0$ (nothing charged at $j$ yet), which the parent's direct switch to
$j'$ covers: that one starts earlier with the same energy.  One settled set
per node stays exact although a label created at $j$ may not stop at $j$ next:
if it attains the envelope at time $t$, a stop at $j$ at $t$ of the label it
prunes yields at every later time no more energy than continuing to charge.
At the origin with $x_0 < \underline e$ and several stations, a stop label
below the reserve is *extended*: its function, dominance tests and switch
candidates start where charging starts (it may switch below the reserve, but
drive only from $t_{feas}$ on).  Earlier versions spawned no stop from a label
created at the same node and missed, e.g., DC to 37.4 kWh and then AC on the
cold two-charger line of BUG-3 (16 s faster than the best single stop).

**Theorem 3.** With static waits, CFP returns a fastest feasible route with
arbitrary (continuous) charging amounts and any number of consecutive stops
at different stations of a node (with a minimum stop SoC: every stop obeying
the floor rule, by Lemma 1F and Lemma 3F below), i.e. over all plans that are feasible in
exact arithmetic -- the plans `evaluate()` accepts, up to its float
tolerance (Section 1); a second stop at the same station right after the
first is never useful (Section 1).  *Sketch:* every label operation is monotone
and distributes over pointwise maxima, so pruning labels below the envelope
of settled labels is safe; by Lemma 3 and its extension, creating station
labels at the candidate times loses nothing, also for a stop that follows a
stop at the same node; keys are lower bounds that never decrease along label
extensions; the first final entry popped at $d$ is therefore optimal.
$\square$ (With the breakpoints alone this holds for concave charging
functions; the default extended candidate set makes the result exact for
non-concave charging curves and FIFO time-dependent waits as well.)

The consecutive-stop part is checked against an independent oracle: on a line
with two or three chargers at one node (also at the origin, where stops may
end below the reserve), the trip time is separable and
piecewise linear in the energies at which the stops end, the constraints form
an interval chain, so an optimal plan ends every stop at a breakpoint energy
and a shortest path over (breakpoint energy, charger in use) is exact for any
number of stops.  CFP and CFP-A\* match it on every random instance
(`test_consecutive_stops_match_an_exact_dynamic_programme`; in a 6000-instance
run 236 optima had two or more stops at the node, up to five alternating
stops with a non-monotone charging curve).

**The floor rule in CFP.**  A stop label at $j'$ created at the switch time
$t_j$ with $b = B(t_j) < \varphi$ stands only for the stops that end at
$\varphi$ or above, so it is *folded*: it starts at

$$ b' = \varphi,\qquad t_0' = t_0 + F_{j'}(\varphi) - F_{j'}(b), $$

and its function for $t \ge t_0'$ is unchanged
($F_{j'}^{-1}(F_{j'}(b) + t - t_0) = F_{j'}^{-1}(F_{j'}(b') + t - t_0')$).  Keys,
dominance, sibling pruning (the offset $t_0 - F_{j'}(b)$ is the same, and
$t_0' = \max(t_0, t_0 - F_{j'}(b) + F_{j'}(\varphi))$ is non-decreasing in
$t_j$) and the reconstruction of the plan work on the folded parameters
unchanged.  A folded label has charged at $j'$ at its own $t_0'$, so its switch
candidate there is kept (the rule "skip the candidate at $t_0$" refers to the
start of charging).

**Lemma 3F (the candidates stay complete).**  For every final time $t$, the
best energy over all switch times that obey the floor is attained at a
candidate of Lemma 3 (or by not stopping at $j'$), and if no switch time obeys
it, none does.  *Proof.*  With $B(t') \ge \varphi$ a switch at $t'$ is always
allowed and then $\Phi(t') \ge B(t') \ge \varphi$; with $B(t') < \varphi$ it is
allowed iff the stop ends at the floor, $\Phi(t') \ge \varphi$.  The allowed
switch times are therefore exactly the upper level set
$\{t' : \Phi(t') \ge \varphi\}$ of the function Lemma 3 maximises: if its
maximum is at least $\varphi$, a maximiser at a candidate is allowed; if not, no
switch time is.  $\square$  In particular the earliest time at which the
vehicle can leave $j'$ with exactly $\varphi$ -- the route that dominates all
others leaving with $\varphi$ -- is reached by a candidate label (apply the
lemma at that time), so the crossing of $B$ with $\varphi$ needs no candidate of
its own.  The test-suite checks the lemma against a dense scan of switch times
and CFP against the dynamic programme above extended by the floor (the
constraint $y_1 \ge \varphi$ on the first stop is one more interval bound of the
chain, so with $\varphi$ among the energies a vertex solution is still optimal),
against a dense scan of departure energies on line networks, and against BLS
(never slower on any grid, converging from above as the grid is refined).

With time-dependent waits CFP evaluates $W_{j'}$ at each concrete switch time.
With the breakpoints alone (option `exact_switching=False`) plans remain
consistent with the evaluator but optimality is no longer guaranteed
(Lemma 3's derivative gains a $-W'$ term); the default candidate set adds the
knots of the wait profiles, between which $W'$ is constant, and is exact.

## 5. Multi-criteria search

`pareto` runs Martins' label-setting algorithm (Martins, 1984) over
$(t, \mathcal M, \mathcal W)$ with energy as an additional maximised resource,
and prunes labels whose optimistic completion vector (current values plus
consistent lower bounds) is dominated by the destination front, as in NAMOA\*
(Mandow & Pérez de la Cruz, 2010).  The transitions (including consecutive
stops) are those of BLS.  The fastest front member equals BLS with objective
time and the cheapest equals BLS with objective cost — both equalities are
asserted in the test-suite.  With a clock-dependent charging tariff the
transitions include the voluntary holds of Section 6 (money is always a
criterion here), and the dominance rule is unchanged.

*Goal-directed order.*  Labels are popped in lexicographic order of the key
$(t + T_{lb}(v),\ \mathcal M,\ \mathcal W)$, ties broken by larger energy.
$T_{lb}(v)$ is the same constant for every label of a node, so at each node
labels are settled in Martins' lexicographic order of $(t, \mathcal M,
\mathcal W)$; across nodes the shift is the consistent vector heuristic
$(T_{lb}(v), 0, 0)$ of NAMOA\*, which pops far-away labels only after the
front members that prune them are known (one to two orders of magnitude
fewer labels; `goal_directed=False` restores the plain Martins order and
returns the same front).  Because time leads the key, every settled label of
a node and every front member is no slower than a label being tested, so
the node tests compare $(\mathcal M, \mathcal W, x)$ and the front tests
$(\mathcal M, \mathcal W)$ only.

*Output and effort.*  The returned plan is the front member with the best
scenario objective (trip time or generalised cost); all evaluated members,
sorted by time, are in `plan.alternatives`.  Exact three-criteria fronts can
need millions of labels (on the 750 km Karaman → İstanbul preset the default
budget `max_labels = 2 000 000` is exhausted before the first route is
found); `criteria=("time", "money")`, `targets="grid"`, a coarser
`charge_step_soc` or $\varepsilon$-dominance (`epsilon`, `epsilon_scope`) make
such instances tractable.  When the budget is hit the front may be
incomplete and the planner says so (`plan.stats.extra["complete"] = False`).

*Front maintenance in floating point.*  In exact arithmetic a new front
member never dominates an existing one (members arrive in order of time).
Routes with mathematically equal trip times reach the destination with times
that differ in the last bits, and the goal-directed keys are monotone only up
to a few ulps, so an equally fast but cheaper route can arrive right after a
member it dominates.  A new route is therefore rejected if a member covers it
(money and wear within a relative $10^{-9}$, time implicit), and otherwise
removes every member it covers (time within a relative $10^{-12}$, money and
wear within $10^{-9}$); vectors equal within these tolerances keep the member
that arrived first.  The front is thus mutually non-dominated up to rounding
(BUG-1 of the cross-validation suite).

## 6. Charging tariffs: price periods and voluntary holds

A tariff (`model.md` §5) changes the *money* of a charging session, never its
time or wear.  The money depends on the energy (the high-SoC factor; its
threshold $e_h$ is a breakpoint of every charging function, hence a charging
target of BLS and Pareto) and, with peak hours or busy hours, on the price
period at the moment $\tau$ the vehicle joins the queue: $p_j(\tau) \in
\{\text{off-peak}, \text{peak}\}$, money $M^{p_j(\tau)}_j(x, y)$.  Prices that
depend on the clock (`P.tariff_time_dependent`) create a new decision: a
vehicle may *hold* before it joins, until a cheaper period begins.  Holds
cost time, and the question is which holds an exact planner must consider.
Write $H_j(t)$ for the price changes of station $j$ in $(t, t + 24\,\text{h}]$,
as trip times (`P.hold_candidates(j, t)`), and $J_j(t) = \{t\} \cup H_j(t)$
for the *generated join times* of a vehicle that reaches $j$ at $t$.

**Lemma 4 (holds never save time).**  Removing every hold from a plan --
joining each queue on arrival, same walk, stations and targets -- gives a
feasible plan that reaches every node no later and with the same energy.

*Proof.*  Induction along the plan.  Energies do not depend on the clock, so
they and the reserve checks are unchanged.  Let a stop be reached at
$t' \le t$ (induction hypothesis; $t$ is the arrival in the original plan,
which joined at $\tau \ge t$).  The new stop leaves the queue at
$t' + W_j(t') \le t + W_j(t) \le \tau + W_j(\tau)$, by FIFO applied twice,
and then takes the same set-up and charging time; drive times do not depend
on the clock.  $\square$

Prices do not enter the trip time, so the **time objective never holds**:
BLS and BLS-A\* with objective time, CFP and CFP-A\* generate no holds, and
their optimum does not depend on the prices at all -- except that the
high-SoC breakpoint $e_h$ joins the target set of BLS (a finer target set is
never worse; on the default 2 % grid $e_h = 0.8\,Q$ is already a target).
CFP needs no change: the money of its plan is whatever `evaluate()`
measures, in the period at the arrival time.  (BLS does not look at the
clock-dependent money when it chooses among *equally fast* routes, so the
fastest route it returns need not be the cheapest of them; its trip time is
optimal regardless.)

For the **cost objective** of BLS / BLS-A\* and for the **Pareto search**
(money is always one of its criteria) a hold can pay: the balanced plan of
the README example waits 2.4 minutes at İnegöl for the 20:00 price change
and saves more than it loses.  When `P.tariff_time_dependent`, every station
expansion of a label at trip time $t$ generates the usual charging targets
for every join time $\tau \in J_j(t)$: wait $W_j(\tau)$, money of the period
$p_j(\tau)$, and for the cost objective $\mathrm{VoT}\cdot(\tau - t)$ for the
hold.  Lemma 5 shows that these join times are enough.

**Lemma 5 (the earliest join in a period).**  Let a vehicle reach station
$j$ at $t$ and let $\tau \ge t$ be any join time.  The earliest time
$\kappa \ge t$ with $p_j(\kappa) = p_j(\tau)$ exists, lies in $J_j(t)$ and
satisfies $\kappa \le \tau$.

*Proof.*  The peak period is a finite union of half-open intervals
$[a, b)$ on the daily clock, so both periods are unions of half-open
intervals and the earliest time of a period at or after $t$ is attained.
If $p_j(t) = p_j(\tau)$ then $\kappa = t$.  Otherwise $\kappa > t$ is the
first instant of a maximal interval of period $p_j(\tau)$, i.e. a price
change.  The pattern repeats every 24 h and $p_j(\tau)$ occurs (at $\tau$),
so it occurs in $[t, t + 24\,\text{h})$: $\kappa \in H_j(t)$.  $\tau$ itself
is a time $\ge t$ in that period, hence $\kappa \le \tau$.  $\square$

In words: waiting is only worth it until a price change, and never past the
first one that starts the price period one would have paid anyway.  A hold
that ends anywhere else can be shortened to the start of its price period
(the money stays, the queue is left no later by FIFO), or dropped.

**Theorem 4 (exactness with holds).**  Let labels $A$ and $B$ at the same node
satisfy $t_A \le t_B$, $m_A \le m_B$ (for Pareto: $\mathcal M_A \le \mathcal
M_B$ and $\mathcal W_A \le \mathcal W_B$) and $x_A \ge x_B$, where $m$ is the
weighted money and wear spent so far.  Then every completion of $B$ -- with
holds until *arbitrary* times -- is matched by a completion of $A$ (or, where
$A$ may not stop, of a label generated together with $A$) that only uses
generated join times and reaches the destination no later, with no less
energy, no more money and no more wear.

*Proof.*  Follow $B$ event by event and keep the invariant (no later, no
less energy, no more money and wear).  A *drive* keeps it as in Lemma 1.  A
*stop* of $B$ at station $j$, reached at $t_B$, joining at $\tau_B \ge t_B$ in
period $p = p_j(\tau_B)$ and charging from $x_B$ to $y$: if $y \le x_A$, $A$
skips it.  Otherwise $A$ (reaching $j$ at $t_A \le t_B$) joins at the
$\kappa \in J_j(t_A)$ of Lemma 5 for $\tau = \tau_B$: $\kappa \le \tau_B$ and
$p_j(\kappa) = p$.  By FIFO, $A$ leaves the queue at $\kappa + W_j(\kappa)
\le \tau_B + W_j(\tau_B)$; it charges from $x_A \ge x_B$ to the same $y$ in
no more time ($F_j$ increasing), and pays the same price function $\varphi_p$
on the sub-interval $[x_A, y] \subseteq [x_B, y]$, likewise for the wear.  Its
hold is part of its trip time, which stays no later, so
$\mathrm{VoT}\cdot T$ is ordered as well.  The remaining case is a stop that
$A$ may not make: $A$ was created by a session at the same station $j$ in the
same period $p$ (a label never restarts at its station in the same period,
below).  Then $A$'s parent $C$ joined $j$ in period $p$ and also generated
the target $y$ with the same join time; that label ends charging at
$t_A - F_j(x_A) + F_j(y) \le \tau_B + W_j(\tau_B) + \sigma_j + F_j(y) -
F_j(x_B)$ (as $t_A \le t_B \le \tau_B$ and $F_j(x_A) \ge F_j(x_B)$) with
money $m_A + M^p_j(x_A, y) \le m_B + M^p_j(x_B, y)$, and continues in place
of $A$.  $\square$

Applied to the start label, Theorem 4 says that some optimal plan uses only
generated join times; applied to a settled label and a popped one, that
discarding dominated labels is exact.  So BLS and BLS-A\* with the cost
objective return the minimum generalised cost over all plans with targets in
$\mathcal Y$ and holds until any time, and the Pareto search returns the
complete front over these plans.  The A\* potentials remain admissible and
consistent: a hold is part of a stop transition, costs
$\mathrm{VoT}\cdot\text{hold} \ge 0$ and does not change the energy, so the
remaining lower bound $h(v, x)$ is unchanged; the charging rates in the
potentials and in the Pareto completion bounds are the cheapest over both
price periods (`P.cf_variants`).  The cross-validation suite checks the
theorem against an exhaustive enumeration that holds until every price
change, and on the smallest instances against one that may hold until every
5-minute step of the next 24 hours (`tests/fuzz_report.md` §7).

Two rules of BLS change with a clock-dependent tariff; both are needed for
Theorem 4.

* *Dominance of the cost objective is 3-D, also with static waits.*  With
  constant prices and static waits BLS compares $(g, x)$ with $g =
  \mathrm{VoT}\cdot t + m$: a later label is at a disadvantage only through
  $\mathrm{VoT}\cdot t$, which $g$ already contains.  Under a clock-dependent
  tariff it is also at a disadvantage through the prices it can still get:
  a cheaper but later label $A$ ($g_A \le g_B$, $t_A > t_B$) may arrive after
  the off-peak period has ended, which $B$ still catches, and could reach the
  next one only by holding for hours.  Pruning $B$ would lose the optimum, so
  BLS uses the rule $(m, t, x)$ of Section 1, as for time-dependent waits
  (`P.time_dependent or P.tariff_time_dependent`).  The Pareto search keeps
  $(t, \mathcal M, \mathcal W, x)$ apart anyway.
* *A second session at the same station in the other period.*  The price of
  a session is fixed when it joins the queue, so splitting a session at a
  price change can pay: charge in the peak only what is needed until the peak
  ends, then join the queue again at the off-peak price (and pay $W_j +
  \sigma_j$ once more) -- the cost optimum of
  `test_cost_optimum_restarts_at_the_same_station_when_the_peak_ends`.  A
  label created by a stop at $j$ may therefore stop at $j$ again if the new
  session joins in the other price period; in the same period the first
  session could have charged on, so the argument of Section 1 still excludes
  it.  `evaluate()` accepts such plans; at a single-station origin the first
  session may then end below the reserve, as between consecutive stops at
  different stations (Section 1).

*Effort.*  Holds multiply the transitions of a station by up to
$1 + |H_j(t)|$ (two price changes a day for one peak interval), and the 3-D
dominance keeps more labels.  BLS-A\* answers the cost objective of the
README example in under a second, but the complete (time, money) front of
that 750 km trip creates about 6 million labels (85 s of search) on the
default 2 % charging grid -- more than the viewer's budget of 1 million,
hence `max_labels=10_000_000` in the [worked example](index.md#worked-example) -- against fewer than 1 million
(7 s) on the 5 % grid of the `tariffs` experiment.  A possible pruning, not
implemented: skip a hold into a period that is nowhere cheaper than the
current one (its price is no lower in every energy band); joining at once
then finishes no later and pays no more, so Theorem 4 would still hold.  With the neutral `Tariff()`
none of this applies: no breakpoint, no hold, the same labels as before.

## 7. Baselines

The baselines drive every edge at its fastest speed level, as a navigation
system would (`fastest_level_only` in the label search they call); only the
exact planners trade speed for energy.

* `naive` — battery-unaware fastest path, no charging; shows *where* the
  battery runs out.
* `greedy` — if the battery-unaware fastest path to the destination is
  drivable, drive it; otherwise detour to the reachable, not yet used station
  minimising time-to-station + $T_{lb}$(station) (ties: the station closest to
  the destination, then the shorter stop), charge to 80 % (DC) / 100 % (AC)
  -- to the cap if that is needed to make progress -- and repeat.  Typical of
  in-car navigation.
* `station-graph` — time-dependent Dijkstra on a meta-graph of the origin,
  the stations inside a detour ellipse and the destination; a meta-edge is
  the fastest battery-feasible stretch between two of them, and every stop
  charges to a fixed target (default 100 %, capped by $\bar e$) (Storandt &
  Funke, 2012).  Optimal on its meta-graph only.
* `genetic` — a deterministic (seeded) GA over ordered station sequences and
  target SoCs; legs follow the fastest paths and a stop charges to
  $\max(\text{target}, \text{just enough for the next leg})$; fitness is the
  evaluated objective plus a static penalty for infeasibility.  A
  representative of the metaheuristics that dominate applied EV-routing
  papers.

## 8. How correctness is verified

1. An **independent evaluator** (`evaluate.py`) replays every plan from first
   principles - energies, reserves, queues, charging curves, money, wear;
   the objective an exact algorithm claims must match it, and every
   heuristic's plan goes through the same replay.
2. **Cross-validation** on thousands of seeded random instances
   (`tests/test_crossval.py`, summarised in `tests/fuzz_report.md`): BLS =
   BLS-A\*; CFP = CFP-A\*; CFP ≤ BLS; Pareto extremes = BLS (time / cost);
   no plan of any planner beats CFP; finer target grids are never worse;
   metamorphic relations and local search around every optimum.
3. **Brute force** on tiny instances: enumeration of walks, stops (including
   consecutive stops at different stations) and targets reproduces the BLS
   optimum and the Pareto front.
4. **Analytic oracles**: hand-built lines where the optimal continuous split
   between a slow and a fast charger can be found by dense scanning, and the
   exact dynamic programme over breakpoint energies for consecutive stops
   (Section 4).
5. **Charging tariffs** (`tests/test_crossval_tariffs.py`,
   `tests/fuzz_report.md` §7): random tariffs (high-SoC tier, peak
   intervals that wrap midnight, busy hours) on the instances above, checked
   against an oracle that re-derives periods, price changes and money from
   the `Tariff` alone; the invariants of item 2 with holds, the time
   objective never holding, monotonicity in the prices, and the brute force
   of item 3 with holds until every price change (on the smallest instances
   until every 5-minute step of the next 24 hours, Section 6).
6. **Minimum stop SoC** (`tests/test_min_stop.py`): every planner's plans obey
   the floor rule (the evaluator and an independent check); the brute force of
   items 3 and 5 with the floor (holds included) equals BLS and the Pareto
   front; the whole battery of item 2 runs with random floors; CFP with the
   floor matches the dynamic programme of item 4 extended by the floor and a
   dense scan of departure energies, Lemma 3F is checked against a dense scan of
   switch times; a higher floor never gives a better CFP optimum, nor a better
   BLS optimum or Pareto front when it lies on the target grid (an off-grid
   floor adds a target and can improve them, Section 1).

The random instances are seeded grids, random geometric graphs, mountain
corridors and tiny digraphs with every vehicle, −15 … 40 °C, 1-3 speed
levels, both objectives, static and time-dependent queues, stacked AC + DC
chargers and 16 odd-but-legal edge cases.  Eleven invariants are checked:
those of item 2 plus lossless JSON round trips and metamorphic relations
such as relabelling, extra stations and more energy; the tariff suite checks
its prices against an oracle written from the specification alone, and that
dearer prices never lower the optimal cost.

The suites found and fixed real bugs (`tests/fuzz_report.md` §5) - a Pareto
front that kept a member 1 ulp faster but 6 EUR dearer, a lossy JSON round
trip, a missed DC-then-AC double stop, an unsound 3-D dominance rule with
time-dependent queues, a plan rebuilt from its stops that joined a queue
1 ulp before a price change.  897 tests, 759 in the fast tier
(`pytest -m "not slow"`), run on Python 3.10-3.13 in CI:

```bash
python -m pytest -m "not slow"                    # fast tier, a few minutes
python -m pytest -m slow tests/test_crossval.py   # 3 200 random instances, ~40 min
python -m pytest -m slow tests/test_crossval_tariffs.py   # 460 random tariff instances
```

## References

* Artmeier, Haselmayr, Leucker, Sachenbacher (2010). *The shortest path
  problem revisited: optimal routing for electric vehicles.* KI 2010, LNCS 6359.
* Baum, Dibbelt, Gemsa, Wagner, Zündorf (2015). *Shortest feasible paths with
  charging stops.* ACM SIGSPATIAL 2015.
* Baum, Dibbelt, Gemsa, Wagner, Zündorf (2019). *Shortest feasible paths
  with charging stops for battery electric vehicles.* Transportation Science
  53(6), 1627-1655.
* Baum, Dibbelt, Wagner, Zündorf (2020). *Modeling and engineering
  constrained shortest path algorithms for battery electric vehicles.*
  Transportation Science 54(6) (speed adaptation, without charging stops).
* Hart, Nilsson, Raphael (1968). *A formal basis for the heuristic
  determination of minimum cost paths.* IEEE Trans. SSC 4(2).
* Mandow, Pérez de la Cruz (2010). *Multiobjective A\* search with consistent
  heuristics.* Journal of the ACM 57(5).
* Martins (1984). *On a multicriteria shortest path problem.* EJOR 16(2).
* Montoya, Guéret, Mendoza, Villegas (2017). *The electric vehicle routing
  problem with nonlinear charging function.* Transportation Research Part B 103.
* Storandt, Funke (2012). *Cruising with a battery-powered vehicle and not
  getting stranded.* AAAI 2012.
