r"""Monte Carlo robustness of route plans: *how likely is the driver to strand?*

A route plan is computed on a deterministic model -- expected consumption,
expected queueing delays.  Reality deviates: weather and driving style change
the consumption of the whole trip, every road segment has its own surprises,
and the queue at a charger is random.  This module replays a fixed plan in
many sampled "worlds" and estimates how often it fails.

Uncertainty model
=================

For run (world) :math:`r` the realised consumption of edge :math:`a` driven
at speed level :math:`k` is derived from the *nominal* physics energy
:math:`c^0_{ak}` (``P.edge_energy_nominal[k][a]``, i.e. without the planning
``safety_factor``):

.. math::

   c_{ak} = \begin{cases}
       c^0_{ak}\,\xi_r\,\xi_{ra}   & c^0_{ak} > 0 \quad\text{(consumption)}\\
       c^0_{ak} / (\xi_r\,\xi_{ra}) & c^0_{ak} \le 0 \quad\text{(regeneration)}
   \end{cases}

with independent log-normal factors of mean one: a *trip factor*
:math:`\xi_r` (coefficient of variation ``trip_cv``; weather, payload,
driving style -- it is shared by all edges of the run) and an *edge factor*
:math:`\xi_{ra}` (``edge_cv``; local traffic, wind gusts, road surface).  A
log-normal variable with mean 1 and coefficient of variation :math:`v` has
:math:`\ln \xi \sim \mathcal N(\mu, \sigma^2)` with
:math:`\sigma^2 = \ln(1 + v^2)` and :math:`\mu = -\sigma^2/2`
(:func:`lognormal_params`).  Dividing regeneration by the factor follows the
convention of ``Preferences.safety_factor``: a bad day consumes more *and*
recovers less.  (Since ``E[1/xi] = 1 + v^2``, the *mean* recovered energy is
slightly above nominal, by 1 % at ``v = 0.1``.)

Queueing delays (``wait_model``):

* ``"exponential"`` -- exponential with mean equal to the expected wait
  ``P.wait_s(s, t)`` at the *realised* arrival time :math:`t` (earlier
  surprises shift the arrival into a different hour of the demand profile);
* ``"expected"`` -- exactly ``P.wait_s(s, t)`` at the realised arrival time;
* ``"none"`` -- no queueing at all.

The exponential law is a deliberately simple dispersion model that keeps the
mean.  In a stationary M/M/c queue the delay is zero with probability
``1 - C(c, a)`` and exponential with rate ``c mu - lambda`` otherwise (Gross
et al., *Fundamentals of Queueing Theory*), i.e. its coefficient of variation
``sqrt(2/C - 1)`` is at least the exponential's 1.  Travel times are not
perturbed: uncertainty enters through energy and queues only.

Driver policies (``policy``)
============================

The route and the stations are fixed; only the charging *targets* react.

* ``"same-target"`` -- charge to the planned target ``y`` (as
  :func:`evroute.evaluate.evaluate` does: nothing is added when the battery
  already holds ``y``).
* ``"adaptive"`` -- charge to ``min(y + max(D, 0), e_charge_cap)`` where
  ``D = sum(c_real - c_planned)`` over the edges driven so far is the excess
  consumption accumulated since departure relative to the planner's own
  prediction ``P.edge_energy`` (which includes the safety factor): a driver
  who sees the state of charge fall behind the plan buys that energy back at
  the next planned stop, but never charges *less* than planned.

Neither policy re-routes or adds unplanned stops; the analysis measures the
robustness of the *plan*, not of an online re-planning strategy.

**Guarantee (pathwise dominance).**  Energy evolution does not depend on
time, driving maps ``e -> min(e - c, capacity)`` and charging
``e -> max(e, target)``, both non-decreasing, and the adaptive target is never
below the planned one.  By induction along the path, in *every* sampled world
the adaptive driver holds at least as much energy at every node as the
same-target driver.  With common random numbers (below) the adaptive
estimates of ``p_strand``, ``p_reserve_breach`` and ``p_arrival_short`` are
therefore never larger than the same-target estimates -- for every sample,
not only in expectation.

Outcomes
========

Every run replays the plan with the arithmetic of
:func:`evroute.evaluate.evaluate` (regeneration clamped at full battery,
every listed stop pays wait + setup, targets clamped at ``e_charge_cap``).
Let ``m`` be the lowest energy the battery holds *while driving*: the
departure energy at the origin and the arrival energy at every later node
(exactly the states the evaluator checks against the reserve).

* **strand**: ``m < 0`` -- the battery empties before the destination;
* **reserve breach**: ``m < reserve - 1e-6`` (default reserve ``P.e_min``);
  every stranding is also a breach;
* **arrival short**: stranded, or the destination is reached with less than
  the required energy (default ``P.e_dest_min``) minus 1e-6.

A stranded run is continued on a *virtual* battery (energy may become
negative) so that its records stay defined; ``min_soc`` then measures the
depth of the deficit.  Trip outcomes that only make sense for a completed
trip (``total_time_s``, ``arrival_soc``, ``charged_kwh``, ``wait_s``) are
summarised over the runs that did **not** strand; ``min_soc`` over all runs.
Probabilities come with 95 % Wilson score intervals (Wilson 1927), which
behave well for probabilities near 0 -- the interesting regime here.

Common random numbers
=====================

Every random quantity of run ``r`` is a fixed function of
``(seed, r, what)``: ``random.Random(seed)`` draws one 64-bit key per run,
and the variate of item ``j`` (``j = 0``: trip factor, ``1 + a``: edge ``a``,
then one slot per (station, visit) for queues) is the ``j``-th output of a
SplitMix64 stream started at that key (Steele, Lea & Flood 2014), mapped to a
normal variate by the inverse CDF (Wichura's AS 241, as implemented by
:meth:`statistics.NormalDist.inv_cdf`) or to an exponential one by
``-ln U``.  Consequently

* two plans on the same network see **the same worlds** (an edge used by both
  gets the same factor in run ``r``) -- comparisons between plans, safety
  factors, policies or wait models are paired, which removes most of the
  Monte Carlo noise from their *differences* (the classic variance-reduction
  technique of common random numbers, see e.g. Law, *Simulation Modeling and
  Analysis*);
* the first ``n`` runs of a larger experiment are exactly the ``n`` runs of
  a smaller one;
* an edge driven twice in one run gets the same factor both times (it is the
  same road on the same day).

Exactness and cost
==================

With ``trip_cv = edge_cv = 0`` and ``wait_model="expected"`` the replay is
the deterministic evaluation of the plan on the nominal model; for a plan made
with ``safety_factor = 1`` it reproduces :func:`~evroute.evaluate.evaluate`
bit for bit.  ``monte_carlo(..., verify=k)`` re-evaluates the first ``k``
runs through :func:`~evroute.evaluate.evaluate` (with ``energy_override`` and
``wait_override``) and raises if any quantity differs, and
:func:`replay_run` returns any single sampled trip as an ordinary, fully
evaluated :class:`~evroute.plan.RoutePlan` (e.g. to animate a stranding).

A run costs ``O(|path| + |stops|)`` -- about one microsecond per edge in
CPython (2 000 runs of a 1 470 km, 132-edge plan take well under a second) --
and memory is ``O(n)``.

References
==========

* E. B. Wilson (1927). Probable inference, the law of succession, and
  statistical inference. *JASA* 22(158), 209-212.
* R. J. Hyndman, Y. Fan (1996). Sample quantiles in statistical packages.
  *The American Statistician* 50(4), 361-365 (definition 7 is used).
* M. J. Wichura (1988). Algorithm AS 241: The percentage points of the
  normal distribution. *Applied Statistics* 37(3), 477-484.
* G. L. Steele Jr., D. Lea, C. H. Flood (2014). Fast splittable pseudorandom
  number generators. *Proc. OOPSLA 2014*, ACM.
* A. M. Law (2015). *Simulation Modeling and Analysis*, 5th ed. McGraw-Hill.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Any, Iterable, Sequence

from .evaluate import TOL_KWH, evaluate
from .plan import ChargeDecision, PlanDecision, RoutePlan
from .scenario import PreparedScenario, Scenario

__all__ = [
    "POLICIES",
    "WAIT_MODELS",
    "NotARouteError",
    "RobustnessReport",
    "Summary",
    "lognormal_params",
    "monte_carlo",
    "quantile",
    "replay_run",
    "reserve_sweep",
    "safety_sweep",
    "wilson_interval",
]

#: Driver reactions understood by :func:`monte_carlo`.
POLICIES: tuple[str, ...] = ("same-target", "adaptive")
#: Queueing-delay models understood by :func:`monte_carlo`.
WAIT_MODELS: tuple[str, ...] = ("exponential", "expected", "none")

_WAIT_CODE = {"exponential": 2, "expected": 1, "none": 0}

# ------------------------------------------------------------ SplitMix64
_MASK64 = (1 << 64) - 1
_GAMMA = 0x9E3779B97F4A7C15  # golden-ratio increment of SplitMix64
_MIX1 = 0xBF58476D1CE4E5B9
_MIX2 = 0x94D049BB133111EB
_INV_2_52 = 1.0 / 4503599627370496.0  # 2**-52
#: 97.5 % quantile of the standard normal distribution (95 % two-sided).
_Z95 = 1.959963984540054
_STD_NORMAL = NormalDist()


def _stream_offset(index: int) -> int:
    """SplitMix64 state increment of stream position ``index`` (0-based)."""
    return ((index + 1) * _GAMMA) & _MASK64


def _uniform(key: int, offset: int) -> float:
    """Uniform variate in the *open* interval (0, 1) at a stream position.

    ``key + offset`` is the SplitMix64 state, the three lines below are its
    output function.  The top 52 bits are centred in their cell,
    ``u = (k + 1/2) / 2^52``; every such value is exactly representable, so
    ``u`` lies in ``[2^-53, 1 - 2^-53]`` and is never 0 or 1 (safe for
    ``inv_cdf`` and ``log``).  (With 53 bits, ``2^53 - 1/2`` would round up
    to ``2^53`` and yield ``u = 1``.)  The replay kernel inlines exactly
    these operations.
    """
    z = (key + offset) & _MASK64
    z = ((z ^ (z >> 30)) * _MIX1) & _MASK64
    z = ((z ^ (z >> 27)) * _MIX2) & _MASK64
    z ^= z >> 31
    return ((z >> 12) + 0.5) * _INV_2_52


def _world_keys(seed: int, n: int) -> list[int]:
    """One 64-bit key per run from ``random.Random(seed)`` (prefix-stable)."""
    rng = random.Random(seed)
    getrandbits = rng.getrandbits
    return [getrandbits(64) for _ in range(n)]


# --------------------------------------------------------------- statistics
def lognormal_params(cv: float) -> tuple[float, float]:
    """``(mu, sigma)`` of ``ln X`` for a log-normal ``X`` with mean 1 and CV ``cv``.

    ``sigma^2 = ln(1 + cv^2)`` and ``mu = -sigma^2 / 2`` give
    ``E[X] = exp(mu + sigma^2/2) = 1`` and ``Var[X] = exp(sigma^2) - 1 = cv^2``.

    >>> lognormal_params(0.0)
    (0.0, 0.0)
    """
    if not cv >= 0.0 or not math.isfinite(cv):
        raise ValueError(f"coefficient of variation must be finite and >= 0, got {cv!r}")
    s2 = math.log1p(cv * cv)
    return (-0.5 * s2 if s2 > 0.0 else 0.0), math.sqrt(s2)


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Sample quantile by linear interpolation (Hyndman & Fan definition 7).

    This is the default of R, NumPy and ``statistics.quantiles(...,
    method="inclusive")``.  ``sorted_values`` must be sorted and non-empty.

    >>> quantile([1.0, 2.0, 3.0, 4.0], 0.5)
    2.5
    """
    n = len(sorted_values)
    if n == 0:
        raise ValueError("quantile of an empty sample")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must lie in [0, 1]")
    h = (n - 1) * q
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (h - lo) * (sorted_values[hi] - sorted_values[lo])


def wilson_interval(successes: int, n: int, z: float = _Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (Wilson 1927).

    Unlike the normal-approximation interval it never leaves ``[0, 1]`` and
    stays informative when no event was observed: for ``0`` events in ``n``
    runs the upper end is ``z^2 / (n + z^2)`` (about ``3.8 / n``).
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    # the bounds are exactly 0 (no event) and 1 (only events); avoid round-off
    lo = 0.0 if successes <= 0 else max(0.0, centre - half)
    hi = 1.0 if successes >= n else min(1.0, centre + half)
    return (lo, hi)


@dataclass(frozen=True)
class Summary:
    """Distribution summary of one Monte Carlo output (``count`` samples)."""

    count: int
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    p5: float | None = None
    p50: float | None = None
    p95: float | None = None
    max: float | None = None

    @classmethod
    def of(cls, values: Iterable[float]) -> "Summary":
        """Summarise ``values`` (sample standard deviation, type-7 quantiles)."""
        xs = sorted(values)
        n = len(xs)
        if n == 0:
            return cls(0)
        mean = math.fsum(xs) / n
        std = math.sqrt(math.fsum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
        return cls(
            count=n,
            mean=mean,
            std=std,
            min=xs[0],
            p5=quantile(xs, 0.05),
            p50=quantile(xs, 0.50),
            p95=quantile(xs, 0.95),
            max=xs[-1],
        )

    def to_dict(self) -> dict[str, Any]:
        return {k: _finite(getattr(self, k)) for k in ("count", "mean", "std", "min", "p5", "p50", "p95", "max")}


def _finite(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


# ------------------------------------------------------------ compiled plan
class NotARouteError(ValueError):
    """The plan is not an origin -> destination route of the scenario."""


class _Route:
    """A validated plan decision flattened into per-position arrays.

    ``stops_at[i]`` lists the stops at node position ``i`` in the order in
    which :func:`~evroute.evaluate.evaluate` processes them, each as
    ``(station, clamped_target, charging_function, setup_s, wait_offset)``.
    """

    __slots__ = (
        "P", "decision", "nodes", "n_edges", "nominal", "planned", "time",
        "edge_offsets", "stops_at", "charges",
    )

    def __init__(self, P: PreparedScenario, decision: PlanDecision) -> None:
        net = P.net
        edges, levels = decision.edges, decision.levels
        if not edges and P.origin != P.destination:
            raise NotARouteError("plan has no route to replay (was it infeasible?)")
        nodes = [P.origin] if not edges else [net.tail[edges[0]]]
        n_levels = len(P.speed_factors)
        for i, (a, k) in enumerate(zip(edges, levels)):
            if not 0 <= a < net.num_edges:
                raise NotARouteError(f"plan is not a route: edge index {a} out of range")
            if not 0 <= k < n_levels:
                raise NotARouteError(f"plan is not a route: invalid speed level {k} on edge {a}")
            if net.tail[a] != nodes[-1]:
                raise NotARouteError(f"plan is not a route: edge {a} at position {i} does not continue the path")
            nodes.append(net.head[a])
        if nodes[0] != P.origin or nodes[-1] != P.destination:
            raise NotARouteError("plan is not a route from the origin to the destination of this scenario")

        self.P = P
        self.decision = decision
        self.nodes = nodes
        self.n_edges = len(edges)
        self.nominal = [P.edge_energy_nominal[k][a] for a, k in zip(edges, levels)]
        self.planned = [P.edge_energy[k][a] for a, k in zip(edges, levels)]
        self.time = [P.edge_time[k][a] for a, k in zip(edges, levels)]
        # stream positions: 0 = trip factor, 1 + a = edge a, then queues
        self.edge_offsets = [_stream_offset(1 + a) for a in edges]
        wait_base = 1 + net.num_edges
        n_st = max(len(P.stations), 1)

        stops_at: list[list[tuple[int, float, Any, float, int]]] = [[] for _ in nodes]
        visits: dict[int, int] = {}
        ordered: list[ChargeDecision] = []
        for c in decision.charges:  # sorted by position (stable) like the evaluator
            if not 0 <= c.position < len(nodes):
                raise NotARouteError(f"plan is not a route: charge position {c.position} outside the path")
            s = c.station
            if not 0 <= s < len(P.stations) or P.stations[s].node != nodes[c.position]:
                raise NotARouteError(f"plan is not a route: station {s} is not at path position {c.position}")
            occ = visits.get(s, 0)
            visits[s] = occ + 1
            target = min(c.target_kwh, P.e_charge_cap)
            offset = _stream_offset(wait_base + occ * n_st + s)
            stops_at[c.position].append((s, target, P.cf[s], P.setup_s[s], offset))
            ordered.append(c)
        self.stops_at = [tuple(x) for x in stops_at]
        self.charges = ordered

    def decision_with_targets(self, targets: Sequence[float]) -> PlanDecision:
        """Same route and stops, charging targets replaced (in stop order)."""
        charges = [ChargeDecision(c.position, c.station, y) for c, y in zip(self.charges, targets)]
        return PlanDecision(list(self.decision.edges), list(self.decision.levels), charges)


@dataclass
class _Outcomes:
    """Raw per-run results of the replay kernel (energies in kWh)."""

    min_kwh: list[float] = field(default_factory=list)
    arrival_kwh: list[float] = field(default_factory=list)
    time_s: list[float] = field(default_factory=list)
    charged_kwh: list[float] = field(default_factory=list)
    wait_s: list[float] = field(default_factory=list)
    trip_factor: list[float] = field(default_factory=list)
    #: per run ``(energies per edge, waits per stop, effective targets per stop)``
    records: list[tuple[list[float], list[float], list[float]]] = field(default_factory=list)
    #: ``False`` for an edge-less plan (origin == destination): nothing is driven
    drives: bool = True

    def events(self, reserve_kwh: float, arrival_kwh: float) -> tuple[list[bool], list[bool], list[bool]]:
        """Per-run ``(strand, reserve breach, arrival short)`` flags.

        ``strand``: lowest driving energy below zero; ``breach``: stranded or
        below ``reserve_kwh - TOL_KWH``; ``short``: stranded or final energy
        below ``arrival_kwh - TOL_KWH`` (the evaluator's tolerance).
        """
        drives = self.drives
        lim_r, lim_a = reserve_kwh - TOL_KWH, arrival_kwh - TOL_KWH
        strand = [drives and m < 0.0 for m in self.min_kwh]
        breach = [s or (drives and m < lim_r) for s, m in zip(strand, self.min_kwh)]
        short = [s or a < lim_a for s, a in zip(strand, self.arrival_kwh)]
        return strand, breach, short


def _simulate(
    route: _Route,
    keys: Sequence[int],
    trip_cv: float,
    edge_cv: float,
    wait_model: str,
    adaptive: bool,
    record: bool = False,
) -> _Outcomes:
    """Replay ``route`` once per world key (the hot loop of this module).

    The arithmetic mirrors :func:`evroute.evaluate.evaluate` operation by
    operation (same expressions, same order) so that a zero-noise replay is
    bit-identical to the evaluator.  Comparisons are written as ``if``
    statements instead of ``min``/``max`` calls, and all attributes are
    aliased to locals, because this loop runs ``n x |path|`` times.
    """
    P = route.P
    mu_t, sig_t = lognormal_params(trip_cv)
    mu_e, sig_e = lognormal_params(edge_cv)
    wcode = _WAIT_CODE[wait_model]
    inv = _STD_NORMAL.inv_cdf
    exp, log = math.exp, math.log
    M, M1, M2, SC = _MASK64, _MIX1, _MIX2, _INV_2_52
    trip_off = _stream_offset(0)
    nominal, planned, times, offs = route.nominal, route.planned, route.time, route.edge_offsets
    stops_at = route.stops_at
    n_edges = route.n_edges
    n_nodes = n_edges + 1
    e_init, cap, e_cap = P.e_init, P.capacity, P.e_charge_cap
    wait_s = P.wait_s
    inf = math.inf

    out = _Outcomes(drives=n_edges > 0)
    o_min, o_arr, o_t = out.min_kwh.append, out.arrival_kwh.append, out.time_s.append
    o_ch, o_w, o_xi = out.charged_kwh.append, out.wait_s.append, out.trip_factor.append

    for key in keys:
        if sig_t > 0.0:
            z = (key + trip_off) & M
            z = ((z ^ (z >> 30)) * M1) & M
            z = ((z ^ (z >> 27)) * M2) & M
            z ^= z >> 31
            ln_trip = mu_t + sig_t * inv(((z >> 12) + 0.5) * SC)
            xi_trip = exp(ln_trip)
        else:
            ln_trip, xi_trip = 0.0, 1.0
        ln_base = ln_trip + mu_e  # ln(xi_trip * xi_edge) = ln_base + sig_e * z_edge
        if record:
            rec_c: list[float] = []
            rec_w: list[float] = []
            rec_y: list[float] = []
        e = e_init
        t = 0.0
        excess = 0.0  # accumulated (real - planned) consumption, adaptive policy
        charged = waited = 0.0
        e_low = inf
        for i in range(n_nodes):
            stops = stops_at[i]
            if stops:
                for s, y, f, su, w_off in stops:
                    if wcode == 2:
                        z = (key + w_off) & M
                        z = ((z ^ (z >> 30)) * M1) & M
                        z = ((z ^ (z >> 27)) * M2) & M
                        z ^= z >> 31
                        w = wait_s(s, t) * -log(((z >> 12) + 0.5) * SC)
                    elif wcode == 1:
                        w = wait_s(s, t)
                    else:
                        w = 0.0
                    if adaptive and excess > 0.0:
                        y = y + excess
                        if y > e_cap:
                            y = e_cap
                    e_out = y if y > e else e  # == max(e, y)
                    ch = f.charge_time(e, e_out)
                    t += w + su + ch
                    charged += e_out - e
                    waited += w
                    e = e_out
                    if record:
                        rec_w.append(w)
                        rec_y.append(y)
            if i < n_edges:
                if e < e_low:  # departure state (only the origin's can be lowest)
                    e_low = e
                c = nominal[i]
                if sig_e > 0.0:
                    z = (key + offs[i]) & M
                    z = ((z ^ (z >> 30)) * M1) & M
                    z = ((z ^ (z >> 27)) * M2) & M
                    z ^= z >> 31
                    xi = exp(ln_base + sig_e * inv(((z >> 12) + 0.5) * SC))
                else:
                    xi = xi_trip
                c = c * xi if c > 0.0 else c / xi
                if adaptive:
                    excess += c - planned[i]
                e = e - c
                if e > cap:  # == min(e - c, capacity)
                    e = cap
                if e < e_low:  # arrival state at node i + 1
                    e_low = e
                t += times[i]
                if record:
                    rec_c.append(c)
        o_min(e_low if n_edges else e)
        o_arr(e)
        o_t(t)
        o_ch(charged)
        o_w(waited)
        o_xi(xi_trip)
        if record:
            out.records.append((rec_c, rec_w, rec_y))
    return out


# ------------------------------------------------------------------ report
@dataclass
class RobustnessReport:
    """Result of :func:`monte_carlo` (see the module docs for definitions).

    Probabilities are relative frequencies over ``n`` runs; ``ci95`` holds
    95 % Wilson intervals.  ``total_time_s``, ``arrival_soc``,
    ``charged_kwh`` and ``wait_s`` summarise the ``n_completed`` runs that did
    not strand; ``min_soc`` (lowest SoC while driving, negative = deficit on a
    virtual battery) summarises all runs.  ``planned_*`` describe the plan as
    the planner saw it (``evaluate`` on the planning model; note that the
    evaluator's ``min_soc`` also counts the initial state, which may lie
    below the reserve when the plan charges at the origin).

    ``samples`` (only with ``keep_samples=True``) maps ``trip_factor``,
    ``min_soc``, ``arrival_soc``, ``total_time_s``, ``charged_kwh``,
    ``wait_s``, ``stranded``, ``reserve_breach`` and ``arrival_short`` to
    per-run lists in run order; values of stranded runs refer to the virtual
    battery.
    """

    n: int
    seed: int
    policy: str
    wait_model: str
    trip_cv: float
    edge_cv: float
    algorithm: str
    #: Thresholds that define the events [SoC fraction of usable capacity].
    reserve_soc: float
    arrival_soc_required: float
    #: The plan as planned (deterministic evaluation on the planning model).
    planned_feasible: bool
    planned_time_s: float
    planned_arrival_soc: float
    planned_min_soc: float
    planned_stops: int
    n_strand: int
    n_reserve_breach: int
    n_arrival_short: int
    p_strand: float
    p_reserve_breach: float
    p_arrival_short: float
    ci95: dict[str, tuple[float, float]]
    n_completed: int
    total_time_s: Summary
    arrival_soc: Summary
    min_soc: Summary
    charged_kwh: Summary
    wait_s: Summary
    runtime_s: float = 0.0
    samples: dict[str, list[Any]] | None = None

    @property
    def combined_cv(self) -> float:
        """Coefficient of variation of the product ``trip x edge`` factor."""
        return math.sqrt((1.0 + self.trip_cv ** 2) * (1.0 + self.edge_cv ** 2) - 1.0)

    @property
    def p_success(self) -> float:
        """Probability of reaching the destination with the required energy."""
        return 1.0 - self.p_arrival_short

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dictionary (non-finite floats become ``None``)."""
        d: dict[str, Any] = {
            "n": self.n,
            "seed": self.seed,
            "policy": self.policy,
            "wait_model": self.wait_model,
            "trip_cv": self.trip_cv,
            "edge_cv": self.edge_cv,
            "combined_cv": self.combined_cv,
            "algorithm": self.algorithm,
            "thresholds": {"reserve_soc": self.reserve_soc, "arrival_soc": self.arrival_soc_required},
            "planned": {
                "feasible": self.planned_feasible,
                "total_time_s": _finite(self.planned_time_s),
                "arrival_soc": self.planned_arrival_soc,
                "min_soc": self.planned_min_soc,
                "stops": self.planned_stops,
            },
            "p_strand": self.p_strand,
            "p_reserve_breach": self.p_reserve_breach,
            "p_arrival_short": self.p_arrival_short,
            "n_strand": self.n_strand,
            "n_reserve_breach": self.n_reserve_breach,
            "n_arrival_short": self.n_arrival_short,
            "ci95": {k: list(v) for k, v in self.ci95.items()},
            "n_completed": self.n_completed,
            "total_time_s": self.total_time_s.to_dict(),
            "arrival_soc": self.arrival_soc.to_dict(),
            "min_soc": self.min_soc.to_dict(),
            "charged_kwh": self.charged_kwh.to_dict(),
            "wait_s": self.wait_s.to_dict(),
            "runtime_s": self.runtime_s,
        }
        if self.samples is not None:
            d["samples"] = {k: [_finite(x) for x in v] for k, v in self.samples.items()}
        return d

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        def pct(name: str, p: float) -> str:
            lo, hi = self.ci95[name]
            return f"{100 * p:.2f}% [{100 * lo:.2f}, {100 * hi:.2f}]"

        mean_t = self.total_time_s.mean
        return (
            f"Monte Carlo ({self.n} runs, policy={self.policy}, waits={self.wait_model}, "
            f"cv trip/edge={self.trip_cv:g}/{self.edge_cv:g}): "
            f"P(strand)={pct('p_strand', self.p_strand)}, "
            f"P(reserve breach)={pct('p_reserve_breach', self.p_reserve_breach)}, "
            f"P(arrival short)={pct('p_arrival_short', self.p_arrival_short)}, "
            f"mean time {mean_t / 60.0 if mean_t is not None else float('nan'):.1f} min "
            f"(planned {self.planned_time_s / 60.0:.1f} min)"
        )


# --------------------------------------------------------------- public API
def _prepared(prepared: PreparedScenario | Scenario) -> PreparedScenario:
    return prepared.prepare() if isinstance(prepared, Scenario) else prepared


def _decision(plan: RoutePlan | PlanDecision) -> tuple[PlanDecision, str]:
    """The decision to replay and a label for it."""
    if isinstance(plan, PlanDecision):
        return plan, ""
    if plan.decision is not None:
        return plan.decision, plan.algorithm
    # Older/foreign plans: rebuild from the measured stops.  The measured
    # departure energy is used as target, which equals the decided target
    # unless the decided stop added nothing.
    charges = [ChargeDecision(st.position, st.station, st.energy_out_kwh) for st in plan.stops]
    return PlanDecision(list(plan.edges), list(plan.levels), charges), plan.algorithm


def _check_options(n: int, wait_model: str, policy: str) -> None:
    if int(n) != n or n < 1:
        raise ValueError(f"n must be a positive integer, got {n!r}")
    if wait_model not in _WAIT_CODE:
        raise ValueError(f"unknown wait_model {wait_model!r}; choose from {WAIT_MODELS}")
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}; choose from {POLICIES}")


def _verify(
    route: _Route,
    keys: Sequence[int],
    out: _Outcomes,
    runs: int,
    trip_cv: float,
    edge_cv: float,
    wait_model: str,
    adaptive: bool,
) -> None:
    """Cross-check the first ``runs`` runs against the independent evaluator."""
    P = route.P
    rec = _simulate(route, keys[:runs], trip_cv, edge_cv, wait_model, adaptive, record=True)

    def same(a: float, b: float) -> bool:
        return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-9)

    for r in range(min(runs, len(keys))):
        energies, waits, targets = rec.records[r]
        ev = evaluate(P, route.decision_with_targets(targets), energy_override=energies, wait_override=waits)
        if route.n_edges:
            low = min(min(ev.node_energy_depart[:-1]), min(ev.node_energy_arrive[1:]))
        else:
            low = ev.node_energy_depart[-1]
        stranded = out.min_kwh[r] < 0.0
        checks = {
            "kernel determinism": rec.time_s[r] == out.time_s[r] and rec.arrival_kwh[r] == out.arrival_kwh[r],
            "total time": same(ev.total_time_s, out.time_s[r]),
            "arrival energy": same(ev.arrival_soc * P.capacity, out.arrival_kwh[r]),
            "lowest driving energy": same(low, out.min_kwh[r]),
            "charged energy": same(ev.energy_charged_kwh, out.charged_kwh[r]),
            "waiting time": same(ev.wait_time_s, out.wait_s[r]),
            "stranding": (ev.stranded_at_m is not None) == stranded,
            "feasibility": ev.feasible
            == (
                out.min_kwh[r] >= P.e_min - TOL_KWH
                and not stranded
                and out.arrival_kwh[r] >= P.e_dest_min - TOL_KWH
            ),
        }
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            raise RuntimeError(
                f"Monte Carlo replay disagrees with evaluate() in run {r}: {', '.join(bad)} "
                f"(kernel: time {out.time_s[r]!r}, arrival {out.arrival_kwh[r]!r}, low {out.min_kwh[r]!r}; "
                f"evaluator: time {ev.total_time_s!r}, arrival {ev.arrival_soc * P.capacity!r}, low {low!r})"
            )


def _run(
    P: PreparedScenario,
    plan: RoutePlan | PlanDecision,
    n: int,
    seed: int,
    trip_cv: float,
    edge_cv: float,
    wait_model: str,
    policy: str,
    keep_samples: bool,
    reserve_kwh: float | None,
    arrival_kwh: float | None,
    verify: int | bool,
) -> tuple[RobustnessReport, _Outcomes]:
    _check_options(n, wait_model, policy)
    lognormal_params(trip_cv)
    lognormal_params(edge_cv)
    n = int(n)
    t0 = time.perf_counter()
    decision, label = _decision(plan)
    route = _Route(P, decision)
    planned = evaluate(P, decision, algorithm=label)
    adaptive = policy == "adaptive"
    keys = _world_keys(seed, n)
    out = _simulate(route, keys, trip_cv, edge_cv, wait_model, adaptive)
    if verify:
        runs = n if verify is True else min(int(verify), n)
        _verify(route, keys, out, runs, trip_cv, edge_cv, wait_model, adaptive)

    cap = P.capacity
    reserve = P.e_min if reserve_kwh is None else float(reserve_kwh)
    need = P.e_dest_min if arrival_kwh is None else float(arrival_kwh)
    strand, breach, short = out.events(reserve, need)
    ok = [i for i in range(n) if not strand[i]]
    n_s, n_b, n_a = sum(strand), sum(breach), sum(short)

    samples = None
    if keep_samples:
        samples = {
            "trip_factor": list(out.trip_factor),
            "min_soc": [m / cap for m in out.min_kwh],
            "arrival_soc": [a / cap for a in out.arrival_kwh],
            "total_time_s": list(out.time_s),
            "charged_kwh": list(out.charged_kwh),
            "wait_s": list(out.wait_s),
            "stranded": strand,
            "reserve_breach": breach,
            "arrival_short": short,
        }
    report = RobustnessReport(
        n=n,
        seed=seed,
        policy=policy,
        wait_model=wait_model,
        trip_cv=float(trip_cv),
        edge_cv=float(edge_cv),
        algorithm=label,
        reserve_soc=reserve / cap,
        arrival_soc_required=need / cap,
        planned_feasible=planned.feasible,
        planned_time_s=planned.total_time_s,
        planned_arrival_soc=planned.arrival_soc,
        planned_min_soc=planned.min_soc,
        planned_stops=planned.num_stops,
        n_strand=n_s,
        n_reserve_breach=n_b,
        n_arrival_short=n_a,
        p_strand=n_s / n,
        p_reserve_breach=n_b / n,
        p_arrival_short=n_a / n,
        ci95={
            "p_strand": wilson_interval(n_s, n),
            "p_reserve_breach": wilson_interval(n_b, n),
            "p_arrival_short": wilson_interval(n_a, n),
        },
        n_completed=len(ok),
        total_time_s=Summary.of(out.time_s[i] for i in ok),
        arrival_soc=Summary.of(out.arrival_kwh[i] / cap for i in ok),
        min_soc=Summary.of(m / cap for m in out.min_kwh),
        charged_kwh=Summary.of(out.charged_kwh[i] for i in ok),
        wait_s=Summary.of(out.wait_s[i] for i in ok),
        samples=samples,
    )
    report.runtime_s = time.perf_counter() - t0
    return report, out


def monte_carlo(
    prepared: PreparedScenario | Scenario,
    plan: RoutePlan | PlanDecision,
    n: int = 1000,
    seed: int = 0,
    trip_cv: float = 0.06,
    edge_cv: float = 0.10,
    wait_model: str = "exponential",
    policy: str = "same-target",
    *,
    keep_samples: bool = False,
    reserve_kwh: float | None = None,
    arrival_kwh: float | None = None,
    verify: int | bool = 0,
) -> RobustnessReport:
    """Estimate how often ``plan`` fails under uncertain consumption and queues.

    Parameters
    ----------
    prepared:
        The prepared scenario the plan was made with (a :class:`Scenario` is
        prepared on the fly).  Realised consumption is always derived from the
        *nominal* energies ``P.edge_energy_nominal``; ``P.edge_energy`` (which
        includes the planning safety factor) is only the reference of the
        adaptive policy.
    plan:
        A :class:`RoutePlan` (its ``decision`` is replayed) or a
        :class:`PlanDecision`.  The route must lead from the scenario's origin
        to its destination; a plan that is infeasible on the planning model
        may still be analysed (e.g. the battery-unaware ``naive`` baseline).
    n, seed:
        Number of runs and seed.  Run ``r`` is a fixed function of
        ``(seed, r)`` -- see "Common random numbers" in the module docs.
    trip_cv, edge_cv:
        Coefficients of variation of the per-run trip factor and of the
        per-edge factors (log-normal, mean 1).
    wait_model:
        ``"exponential"`` (default), ``"expected"`` or ``"none"``.
    policy:
        ``"same-target"`` (default) or ``"adaptive"``.
    keep_samples:
        Store per-run arrays in ``report.samples`` (see
        :class:`RobustnessReport`).
    reserve_kwh, arrival_kwh:
        Thresholds of the reserve-breach and arrival-short events (default
        ``P.e_min`` and ``P.e_dest_min``); used to compare plans made with
        different reserves against a common requirement.
    verify:
        Re-evaluate the first ``verify`` runs (``True``: all runs) with
        :func:`~evroute.evaluate.evaluate` and raise ``RuntimeError`` on any
        disagreement (self-check; costs one full evaluation per run).

    Returns
    -------
    RobustnessReport
        Probabilities of stranding, breaching the reserve and arriving short,
        with Wilson intervals and distribution summaries.
    """
    report, _ = _run(
        _prepared(prepared), plan, n, seed, trip_cv, edge_cv, wait_model, policy,
        keep_samples, reserve_kwh, arrival_kwh, verify,
    )
    return report


def replay_run(
    prepared: PreparedScenario | Scenario,
    plan: RoutePlan | PlanDecision,
    run: int,
    seed: int = 0,
    trip_cv: float = 0.06,
    edge_cv: float = 0.10,
    wait_model: str = "exponential",
    policy: str = "same-target",
) -> RoutePlan:
    """Run ``run`` of :func:`monte_carlo` as a fully evaluated :class:`RoutePlan`.

    The sampled world (same arguments, same ``seed``) is passed to
    :func:`~evroute.evaluate.evaluate` through ``energy_override`` and
    ``wait_override`` -- with adaptive targets substituted into the
    decision -- so every per-node quantity of that trip is available, e.g.
    for plotting or animating a stranding.  ``plan.feasible`` is ``False``
    exactly when the run breaches the reserve, strands or arrives short with
    respect to the scenario's own requirements.
    """
    P = _prepared(prepared)
    _check_options(1, wait_model, policy)
    if int(run) != run or run < 0:
        raise ValueError(f"run must be a non-negative integer, got {run!r}")
    decision, label = _decision(plan)
    route = _Route(P, decision)
    key = _world_keys(seed, int(run) + 1)[-1:]
    rec = _simulate(route, key, trip_cv, edge_cv, wait_model, policy == "adaptive", record=True)
    energies, waits, targets = rec.records[0]
    name = f"{label or 'plan'} | MC run {int(run)}"
    return evaluate(
        P, route.decision_with_targets(targets), algorithm=name, energy_override=energies, wait_override=waits
    )


# ------------------------------------------------------------------ sweeps
def _sweep(
    scenario: Scenario,
    field_name: str,
    values: Iterable[float],
    algorithm: str,
    n: int,
    seed: int,
    trip_cv: float,
    edge_cv: float,
    wait_model: str,
    policy: str,
    planner_options: dict[str, Any] | None,
    own_thresholds: bool,
) -> list[dict[str, Any]]:
    """Re-plan for every value of a preference field, then run Monte Carlo.

    The events are measured against the thresholds of the *input* scenario
    (its reserve and arrival requirement), so that every row of the sweep
    estimates the same events; all rows share the same sampled worlds.
    """
    from .algorithms import plan as run_planner

    _check_options(n, wait_model, policy)
    lognormal_params(trip_cv)
    lognormal_params(edge_cv)
    ref = scenario.prepare()
    rows: list[dict[str, Any]] = []
    for value in values:
        sc = scenario.with_preferences(**{field_name: value})
        P = sc.prepare()
        t0 = time.perf_counter()
        route = run_planner(P, algorithm, **(planner_options or {}))
        t_plan = time.perf_counter() - t0
        try:  # heuristics may return an infeasible plan that is still a route
            rep, out = _run(
                P, route, n, seed, trip_cv, edge_cv, wait_model, policy,
                False, ref.e_min, ref.e_dest_min, 0,
            )
        except NotARouteError:  # no origin -> destination route to replay
            rep = out = None
        known = rep is not None
        row: dict[str, Any] = {
            field_name: value,
            "algorithm": algorithm,
            "policy": policy,
            "wait_model": wait_model,
            "feasible": route.feasible,
            "planned_time_s": route.total_time_s if known else None,
            "planned_time_min": route.total_time_s / 60.0 if known else None,
            "stops": route.num_stops if known else None,
            "distance_km": route.distance_m / 1000.0 if known else None,
            "planned_charged_kwh": route.energy_charged_kwh if known else None,
            "planned_arrival_soc": route.arrival_soc if known else None,
            "planned_min_soc": route.min_soc if known else None,
            "plan_runtime_s": t_plan,
            "n": n,
        }
        if rep is not None and out is not None:
            row.update(_risk_columns(rep))
            if own_thresholds:
                _, own_b, own_a = out.events(P.e_min, P.e_dest_min)
                row["p_own_reserve_breach"] = sum(own_b) / n
                row["p_own_arrival_short"] = sum(own_a) / n
        else:
            row.update(_risk_columns(None))
            if own_thresholds:
                row["p_own_reserve_breach"] = None
                row["p_own_arrival_short"] = None
        rows.append(row)
    return rows


def _risk_columns(rep: RobustnessReport | None) -> dict[str, Any]:
    """Flat (CSV-friendly) risk metrics of one sweep row."""
    names = ("p_strand", "p_reserve_breach", "p_arrival_short")
    if rep is None:
        cols: dict[str, Any] = {}
        for k in names:
            cols[k] = cols[k + "_lo"] = cols[k + "_hi"] = None
        for k in ("n_completed", "time_mean_s", "time_p50_s", "time_p95_s", "arrival_soc_mean",
                  "arrival_soc_p5", "min_soc_p5", "min_soc_p50", "charged_mean_kwh", "mc_runtime_s"):
            cols[k] = None
        return cols
    cols = {}
    for k in names:
        cols[k] = getattr(rep, k)
        cols[k + "_lo"], cols[k + "_hi"] = rep.ci95[k]
    cols.update(
        n_completed=rep.n_completed,
        time_mean_s=rep.total_time_s.mean,
        time_p50_s=rep.total_time_s.p50,
        time_p95_s=rep.total_time_s.p95,
        arrival_soc_mean=rep.arrival_soc.mean,
        arrival_soc_p5=rep.arrival_soc.p5,
        min_soc_p5=rep.min_soc.p5,
        min_soc_p50=rep.min_soc.p50,
        charged_mean_kwh=rep.charged_kwh.mean,
        mc_runtime_s=rep.runtime_s,
    )
    return cols


def safety_sweep(
    scenario: Scenario,
    factors: Iterable[float] = (1.0, 1.05, 1.1, 1.15, 1.2, 1.3),
    algorithm: str = "bls-astar",
    n: int = 500,
    seed: int = 0,
    trip_cv: float = 0.06,
    edge_cv: float = 0.10,
    wait_model: str = "exponential",
    policy: str = "same-target",
    *,
    planner_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reliability-versus-time trade-off of the planning safety factor.

    For every factor ``f`` the scenario is re-planned with
    ``Preferences.safety_factor = f`` (consumption inflated, regeneration
    deflated by ``f``), and the resulting plan is replayed by
    :func:`monte_carlo` on the *nominal* model.  All factors are evaluated in
    the same sampled worlds (common random numbers), so differences between
    rows reflect the plans, not sampling noise.

    For an exact planner the planned time is non-decreasing in ``f`` (a larger
    factor only shrinks the feasible set); the risk is *usually* but not
    provably non-increasing (a different route may meet different edges).

    Returns one flat dictionary per factor (``safety_factor``, planned time,
    stops, distance, ``p_strand``, ``p_reserve_breach``, ``p_arrival_short``
    with ``_lo``/``_hi`` Wilson bounds, realised time quantiles, arrival and
    minimum SoC quantiles); ready for :func:`evroute.experiments.common.write_csv`.
    Rows of plans without a route carry ``None`` risk values.
    """
    return _sweep(
        scenario, "safety_factor", factors, algorithm, n, seed, trip_cv, edge_cv,
        wait_model, policy, planner_options, own_thresholds=False,
    )


def reserve_sweep(
    scenario: Scenario,
    reserves: Iterable[float] = (0.05, 0.1, 0.15, 0.2),
    algorithm: str = "bls-astar",
    n: int = 500,
    seed: int = 0,
    trip_cv: float = 0.06,
    edge_cv: float = 0.10,
    wait_model: str = "exponential",
    policy: str = "same-target",
    *,
    planner_options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Like :func:`safety_sweep`, varying ``Preferences.reserve_soc`` instead.

    ``p_reserve_breach`` and ``p_arrival_short`` are measured against the
    reserve and arrival requirement of the *input* scenario, so that all rows
    estimate the same events; ``p_own_reserve_breach`` and
    ``p_own_arrival_short`` use each plan's own (swept) requirements.
    ``p_strand`` does not depend on any threshold.  Note that the arrival
    requirement of a plan is ``max(min_arrival_soc, reserve_soc)``.
    """
    return _sweep(
        scenario, "reserve_soc", reserves, algorithm, n, seed, trip_cv, edge_cv,
        wait_model, policy, planner_options, own_thresholds=True,
    )
