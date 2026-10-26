r"""Genetic algorithm over charging-station sequences (``"genetic"``).

Metaheuristics -- genetic algorithms, simulated annealing, variable
neighbourhood search, ant colonies -- are the most common tools in applied
electric-vehicle routing papers.  :class:`GeneticPlanner` is a compact,
**deterministic** representative of that family, included so that the exact
planners can be compared against what a practitioner would write first.

Representation
==============

A **chromosome** is an ordered list of *genes* ``(station, target_soc)`` of
length ``0 .. max_stops``: the stations at which the vehicle charges, in
driving order, and the SoC it wants to charge to.  Every station and every
station *node* occurs at most once.

Decoding
========

The decoder turns a chromosome into a :class:`~evroute.plan.PlanDecision`:

* the route is the concatenation of the legs ``origin -> s_1 -> ... -> s_k ->
  destination``; each leg follows the **time-fastest path** (fastest speed
  levels, battery-unaware).  Legs are computed lazily by Dijkstra and cached
  per ``(u, v)`` node pair;
* at station ``s_i`` the vehicle charges to
  ``max(target_i, just_enough_i)`` capped at ``P.e_charge_cap``, where
  ``just_enough_i`` is the smallest departure energy for which the next leg
  keeps the reserve at every node (and, on the last leg, meets the arrival
  requirement).  It is obtained exactly from the leg's *energy profile*
  ``f(x) = min(x - cost, out)`` (cf. the CFP formulation of Baum et al., 2019:
  consumption ``cost``, saturation ``out``, minimal input ``in``) by a single
  pass over the leg, so the chromosome only has to say *where* to stop and
  *how much more* than necessary to take;
* a stop that would add no energy (the vehicle arrives with at least the
  required target) is **not** listed -- the car just drives through;
* the decision is replayed by the independent evaluator
  (:func:`evroute.evaluate.evaluate`), which provides time, money, wear and
  the feasibility verdict.

Fitness (minimised) is the value of the scenario objective -- trip time or
generalised cost -- of the evaluated plan.  Infeasible decodes (the battery
does not suffice on some leg even after charging to the cap) are penalised by
the static penalty

.. math:: f = \text{objective} + S\,(1 + w\,v/Q),

where ``v`` is the total reserve violation in kWh measured on the replay (the
largest shortfall below the reserve at any node plus the shortfall of the
arrival energy), ``Q`` the usable capacity, ``w`` the option
``penalty_weight`` and ``S`` ten times the objective of the plain fastest path:
every infeasible individual is worse than any realistic feasible one and the
penalty grows with the violation, so selection pressure points towards
feasibility (static penalty functions, see Coello Coello 2002).  Chromosomes
that cannot be decoded at all (a leg without a path) get ``100 S``.  A cache
keyed by the chromosome avoids repeated decodes.

Algorithm
=========

Generational GA with elitism (De Jong 1975):

1. **Initial population** (default 40): the empty chromosome (no charging);
   the station sequence and targets of the :mod:`greedy <evroute.algorithms.greedy>`
   planner when it succeeds (option ``seed_greedy``); the remainder are
   *random range-aware sequences*: starting at the origin, repeatedly pick a
   random station that lies further along the route (travel time from the
   origin) but within a random fraction of the estimated range, with random
   targets.  Stations are restricted to a **detour ellipse** around the
   origin-destination pair (``detour_factor``, as in the station graph; the
   greedy stations are always allowed).
2. **Selection**: tournament of size 3 (Goldberg & Deb 1991).
3. **Crossover** (probability 0.85), *order-preserving one-point*: a random
   point ``theta`` along the route (travel time from the origin) splits both
   parents; child 1 takes the genes of parent A up to ``theta`` followed by the
   genes of parent B beyond it, child 2 the opposite; every parent keeps the
   relative order of its genes, later duplicates are dropped and the length
   is truncated to ``max_stops``.  The operator is a variable-length
   analogue of the order crossover of Davis (1985).
4. **Mutation** (probability 0.35 per child; always when no crossover took
   place): *insert* a station (at its progress-ordered position with
   probability 0.7), *delete* a gene, *replace* a station (by a near one with
   probability 0.7) or *perturb* a target (Gaussian, sometimes snapped to the
   cap or to "just enough").
5. **Elitism**: the best 2 individuals survive unchanged; ``max(1, N/10)``
   random immigrants replace the worst offspring to keep diversity.
6. **Stopping**: ``generations`` (60) or ``patience`` (15) generations
   without improvement of the best fitness.

Everything random draws from ``random.Random(seed)`` in a fixed order, so
results are reproducible (same ``seed`` -> same plan, same history).

Guarantees and limitations
==========================

* **Feasible plans are verified**, never assumed: the result is the best
  individual re-evaluated by the independent evaluator; if no feasible
  individual was found an infeasible plan is returned.
* **No optimality guarantee** (``exact = "heuristic"``).  The search space is
  restricted: legs follow the battery-*unaware* time-fastest path (a leg that
  only a slower, flatter detour could drive is infeasible for the GA), stops
  are limited to ``max_stops`` and the ellipse, and a stop adds energy only
  up to ``max(target, just_enough)``.  Being stochastic, a different seed can
  give a different plan; the quality typically improves with more
  ``population``/``generations`` at a rapidly growing cost.
* The convergence history is reported in ``stats.extra`` (see below).
* ``internal_objective`` is ``None``: the fitness *is* the evaluator's value,
  so there is no independent internal estimate to compare with.

Options
=======

``seed`` (0), ``population`` (40), ``generations`` (60), ``patience`` (15),
``max_stops`` (10), ``detour_factor`` (1.6), ``elite`` (2), ``tournament``
(3), ``crossover_rate`` (0.85), ``mutation_rate`` (0.35),
``penalty_weight`` (20), ``seed_greedy`` (``True``).

``stats.settled`` is the number of decodes (evaluator calls), ``stats.generated``
the number of fitness look-ups including cache hits.  ``stats.extra``:

``history``
    best *feasible* objective found up to the end of each generation
    (index 0 = initial population; ``None`` while no feasible individual
    exists) -- the convergence curve;
``history_fitness`` / ``history_mean``
    best and mean penalised fitness of each generation;
``history_feasible``
    fraction of feasible individuals in each generation;
``generations_run``, ``stop_reason`` (``"generations"`` or ``"stagnation"``),
``decodes``, ``cache_hits``, ``candidates``, ``seed``, ``population``,
``greedy_seed`` (stops of the greedy seed or ``None``), ``best_genes``.

References
==========

* J. H. Holland (1975). *Adaptation in Natural and Artificial Systems*.
  University of Michigan Press.
* K. A. De Jong (1975). *An analysis of the behavior of a class of genetic
  adaptive systems*. PhD thesis, University of Michigan.
* L. Davis (1985). Applying adaptive algorithms to epistatic domains.
  *Proc. IJCAI 1985*, 162-164.
* D. E. Goldberg, K. Deb (1991). A comparative analysis of selection schemes
  used in genetic algorithms. In G. Rawlins (ed.), *Foundations of Genetic
  Algorithms*, Morgan Kaufmann, 69-93.
* C. A. Coello Coello (2002). Theoretical and numerical constraint-handling
  techniques used with evolutionary algorithms: a survey of the state of the
  art. *Computer Methods in Applied Mechanics and Engineering* 191(11-12),
  1245-1287.
* M. Baum, J. Dibbelt, A. Gemsa, D. Wagner, T. Zuendorf (2019). Shortest
  feasible paths with charging stops for battery electric vehicles.
  *Transportation Science* 53(6), 1627-1655.
"""

from __future__ import annotations

import heapq
import math
import random
from dataclasses import dataclass

from ..evaluate import evaluate, infeasible_plan
from ..plan import ChargeDecision, PlanDecision, RoutePlan, SearchStats, SearchTrace
from ..scenario import PreparedScenario
from .base import Planner
from .greedy import run_greedy
from .naive import EPS, dijkstra_traced, fastest_levels, time_weights
from .station_graph import detour_candidates

INF = math.inf
#: Fitness of a chromosome that cannot be decoded, in units of the penalty scale.
_UNDECODABLE = 100.0
#: A stop is only listed if it adds at least this much energy [kWh].
_MIN_ADD_KWH = 1e-9
#: Number of nearest stations considered by the "replace" mutation.
_NEIGHBOURS = 8

Gene = tuple[int, float]
Chromosome = tuple[Gene, ...]

__all__ = ["GeneticPlanner"]


@dataclass
class _Leg:
    """Time-fastest path between two nodes and its energy profile."""

    edges: list[int]
    levels: list[int]
    #: Smallest departure energy keeping the reserve at every node of the leg.
    need: float
    #: Same, additionally requiring ``P.e_dest_min`` at the end (last leg).
    need_last: float
    #: Net consumption along the leg, ``f(x) = min(x - cost, out)``.
    cost: float
    #: Largest possible energy at the end of the leg (capacity saturation).
    out: float
    #: ``False`` if the reserve is violated at some node even when the leg is
    #: started with a full battery (regeneration is clamped at full capacity,
    #: so a climb after a descent can be undrivable at any departure energy).
    ok: bool = True


class _Problem:
    """Decoder, fitness function and caches of one GA run."""

    def __init__(self, P: PreparedScenario, trace: SearchTrace | None, penalty_weight: float) -> None:
        self.P = P
        self.trace = trace
        self.penalty_weight = penalty_weight
        self.w_fast = time_weights(P)
        self.lv_fast = fastest_levels(P)
        self.target_nodes: set[int] = {P.destination}
        self.trees: dict[int, tuple[list[float], list[int]]] = {}
        self.legs: dict[tuple[int, int], _Leg | None] = {}
        self.cache: dict[Chromosome, tuple[float, bool, float]] = {}
        self.scale = 1.0
        self.decodes = 0
        self.lookups = 0
        self.settled_nodes = 0

    # ------------------------------------------------------------- legs
    def tree(self, source: int) -> tuple[list[float], list[int]]:
        """Fastest-path tree from ``source`` up to all candidate nodes (cached)."""
        tree = self.trees.get(source)
        if tree is None:
            dist, pred, st = dijkstra_traced(
                self.P.net, source, self.w_fast, targets=self.target_nodes, trace=self.trace
            )
            self.settled_nodes += st
            tree = (dist, pred)
            self.trees[source] = tree
        return tree

    def leg(self, u: int, v: int) -> _Leg | None:
        """Fastest path ``u -> v`` with its energy profile; ``None`` if unreachable."""
        key = (u, v)
        if key in self.legs:
            return self.legs[key]
        P = self.P
        dist, pred = self.tree(u)
        lg: _Leg | None = None
        if dist[v] < INF:
            net = P.net
            path: list[int] = []
            x = v
            while x != u:
                e = pred[x]
                path.append(e)
                x = net.tail[e]
            path.reverse()
            levels = [self.lv_fast[e] for e in path]
            e_min, cap = P.e_min, P.capacity
            need, cost, out, ok = e_min, 0.0, cap, True
            for e, k in zip(path, levels):
                c = P.edge_energy[k][e]
                t = e_min + cost + c
                if t > need:
                    need = t
                cost += c
                out -= c
                if out > cap:
                    out = cap
                if out < e_min - EPS:
                    ok = False
            lg = _Leg(path, levels, need, max(need, P.e_dest_min + cost), cost, out, ok)
        self.legs[key] = lg
        return lg

    # ----------------------------------------------------------- decoding
    def decode(self, genes: Chromosome) -> tuple[list[int], list[int], list[ChargeDecision]] | None:
        """Plan decision of a chromosome (see the module documentation)."""
        P = self.P
        stations = P.stations
        nodes = [P.origin] + [stations[g[0]].node for g in genes] + [P.destination]
        k = len(genes)
        legs: list[_Leg] = []
        for i in range(k + 1):
            lg = self.leg(nodes[i], nodes[i + 1])
            if lg is None:
                return None
            legs.append(lg)
        cap, cap_e = P.capacity, P.e_charge_cap
        x = P.e_init
        edges: list[int] = []
        levels: list[int] = []
        charges: list[ChargeDecision] = []
        for i, lg in enumerate(legs):
            if i > 0:
                s, soc = genes[i - 1]
                need = lg.need_last if i == k else lg.need
                y = min(cap_e, max(soc * cap, need))
                if y > x + _MIN_ADD_KWH:
                    charges.append(ChargeDecision(len(edges), s, y))
                    x = y
            edges.extend(lg.edges)
            levels.extend(lg.levels)
            x = min(x - lg.cost, lg.out)
        return edges, levels, charges

    # ------------------------------------------------------------ fitness
    def calibrate(self) -> float:
        """Set the penalty scale from the plain fastest path; returns its objective.

        The scale is ten times the objective value of the plan without any
        charging stop (at least 10).  This evaluation is not counted as a
        decode.
        """
        dec = self.decode(())
        if dec is None:
            return INF
        plan = evaluate(self.P, PlanDecision(dec[0], dec[1], dec[2]), algorithm="genetic")
        obj = plan.objective_value
        self.scale = 10.0 * max(obj, 1.0) if math.isfinite(obj) else 10.0
        return obj

    def peek(self, genes: Chromosome) -> tuple[float, bool, float]:
        """Cached ``fitness`` of an already scored chromosome (not counted as a look-up)."""
        return self.cache[genes]

    def shortfall_kwh(self, plan: RoutePlan) -> float:
        """Total reserve violation of a replayed plan [kWh]."""
        P = self.P
        arrive, depart = plan.node_energy_arrive, plan.node_energy_depart
        worst = INF
        if len(arrive) > 1:
            worst = min(min(arrive[1:]), min(depart[:-1]))
        v = max(0.0, P.e_min - worst) if worst < INF else 0.0
        return v + max(0.0, P.e_dest_min - arrive[-1])

    def fitness(self, genes: Chromosome) -> tuple[float, bool, float]:
        """``(fitness, feasible, objective_value)`` of a chromosome (cached)."""
        self.lookups += 1
        hit = self.cache.get(genes)
        if hit is not None:
            return hit
        dec = self.decode(genes)
        if dec is None:
            res = (_UNDECODABLE * self.scale, False, INF)
        else:
            plan = evaluate(self.P, PlanDecision(dec[0], dec[1], dec[2]), algorithm="genetic")
            self.decodes += 1
            if plan.feasible:
                res = (plan.objective_value, True, plan.objective_value)
            else:
                v = self.shortfall_kwh(plan)
                res = (
                    plan.objective_value + self.scale * (1.0 + self.penalty_weight * v / self.P.capacity),
                    False,
                    plan.objective_value,
                )
        self.cache[genes] = res
        return res


class _Evolution:
    """Variation operators and the generational loop."""

    def __init__(
        self,
        problem: _Problem,
        rng: random.Random,
        cand: list[int],
        rank: dict[int, float],
        *,
        max_stops: int,
        range_s: float,
        route_s: float,
    ) -> None:
        P = problem.P
        self.problem = problem
        self.rng = rng
        self.cand = cand
        self.rank = rank
        self.max_stops = max_stops
        self.range_s = range_s
        self.route_s = route_s
        self.soc_hi = P.e_charge_cap / P.capacity
        self.soc_lo = min(self.soc_hi, P.e_min / P.capacity)
        self.node_of = [st.node for st in P.stations]
        self.by_rank = sorted(cand, key=lambda c: (rank[c], c))
        self._nbrs: dict[int, list[int]] = {}

    # ----------------------------------------------------------- helpers
    def random_soc(self) -> float:
        return round(self.rng.uniform(self.soc_lo, self.soc_hi), 4)

    def normalise(self, genes: list[Gene]) -> Chromosome:
        """Drop repeated stations/nodes (first wins), truncate to ``max_stops``."""
        seen_nodes: set[int] = set()
        out: list[Gene] = []
        for s, soc in genes:
            v = self.node_of[s]
            if v in seen_nodes:
                continue
            seen_nodes.add(v)
            out.append((s, round(min(max(soc, self.soc_lo), self.soc_hi), 4)))
            if len(out) >= self.max_stops:
                break
        return tuple(out)

    def neighbours(self, s: int) -> list[int]:
        """The few candidate stations nearest to ``s`` (cached)."""
        res = self._nbrs.get(s)
        if res is None:
            net = self.problem.P.net
            v = self.node_of[s]
            x, y = net.x[v], net.y[v]
            others = [c for c in self.cand if self.node_of[c] != v]
            res = heapq.nsmallest(
                _NEIGHBOURS,
                others,
                key=lambda c: ((net.x[self.node_of[c]] - x) ** 2 + (net.y[self.node_of[c]] - y) ** 2, c),
            )
            self._nbrs[s] = res
        return res

    # ------------------------------------------------------- construction
    def random_chromosome(self) -> Chromosome:
        """Range-aware random sequence of stations sorted along the route."""
        rng, rank, cand = self.rng, self.rank, self.cand
        if not cand or self.max_stops == 0:
            return ()
        genes: list[Gene] = []
        cur = -1.0  # stations at the origin (rank 0) may be the first stop
        remaining = self.by_rank
        while len(genes) < self.max_stops:
            reach = cur + self.range_s * rng.uniform(0.4, 1.0)
            if reach >= self.route_s and rng.random() < 0.7:
                break  # the destination is within range: stop planning
            window = [c for c in remaining if cur < rank[c] <= reach]
            if not window:
                window = [c for c in remaining if rank[c] > cur][:1]
                if not window:
                    break
            s = window[-1] if rng.random() < 0.5 else rng.choice(window)
            genes.append((s, self.random_soc()))
            cur = rank[s]
        return self.normalise(genes)

    # ---------------------------------------------------------- operators
    def crossover(self, a: Chromosome, b: Chromosome) -> tuple[Chromosome, Chromosome]:
        """Order-preserving one-point crossover at a random route position."""
        rank = self.rank
        theta = self.rng.uniform(0.0, self.route_s)
        a_head = [g for g in a if rank[g[0]] <= theta]
        a_tail = [g for g in a if rank[g[0]] > theta]
        b_head = [g for g in b if rank[g[0]] <= theta]
        b_tail = [g for g in b if rank[g[0]] > theta]
        return self.normalise(a_head + b_tail), self.normalise(b_head + a_tail)

    def mutate(self, genes: Chromosome) -> Chromosome:
        rng = self.rng
        g = list(genes)
        rounds = 1 + (rng.random() < 0.3)
        for _ in range(rounds):
            r = rng.random()
            if not g or r < 0.30:
                self._insert(g)
            elif r < 0.50:
                del g[rng.randrange(len(g))]
            elif r < 0.75:
                self._replace(g)
            else:
                self._perturb(g)
        return self.normalise(g)

    def _insert(self, g: list[Gene]) -> None:
        rng, rank = self.rng, self.rank
        if len(g) >= self.max_stops or not self.cand:
            return
        used = {s for s, _ in g}
        for _ in range(10):
            c = rng.choice(self.cand)
            if c not in used:
                break
        else:
            return
        soc = self.soc_hi if rng.random() < 0.3 else self.random_soc()
        if rng.random() < 0.7:
            pos = sum(1 for s, _ in g if rank[s] <= rank[c])
        else:
            pos = rng.randint(0, len(g))
        g.insert(pos, (c, soc))

    def _replace(self, g: list[Gene]) -> None:
        rng = self.rng
        j = rng.randrange(len(g))
        used = {s for s, _ in g}
        pool = self.neighbours(g[j][0]) if rng.random() < 0.7 else self.cand
        pool = [c for c in pool if c not in used]
        if not pool:
            return
        soc = g[j][1] if rng.random() < 0.7 else self.random_soc()
        g[j] = (rng.choice(pool), soc)

    def _perturb(self, g: list[Gene]) -> None:
        rng = self.rng
        j = rng.randrange(len(g))
        s, soc = g[j]
        r = rng.random()
        if r < 0.15:
            soc = self.soc_hi
        elif r < 0.25:
            soc = self.soc_lo
        else:
            soc = min(max(soc + rng.gauss(0.0, 0.08), self.soc_lo), self.soc_hi)
        g[j] = (s, round(soc, 4))

    def select(self, pop: list[Chromosome], fits: list[float], k: int) -> Chromosome:
        """Tournament selection of size ``k``."""
        best = self.rng.randrange(len(pop))
        for _ in range(k - 1):
            j = self.rng.randrange(len(pop))
            if fits[j] < fits[best]:
                best = j
        return pop[best]


class GeneticPlanner(Planner):
    """Deterministic genetic algorithm over ordered ``(station, target SoC)`` genes.

    See the module documentation for the representation, the decoder, the
    operators, the options and the convergence history in ``stats.extra``.
    Supports both the ``"time"`` and the ``"cost"`` objective (the fitness is
    the evaluator's value of the scenario objective).  With ``trace=True`` the
    visited nodes of the leg searches (Dijkstra) and of the greedy seed are
    recorded.
    """

    name = "genetic"
    OPTIONS = (
        "crossover_rate", "detour_factor", "elite", "generations", "max_stops", "mutation_rate",
        "patience", "penalty_weight", "population", "seed", "seed_greedy", "tournament",
    )
    title = "Genetic algorithm"
    exact = "heuristic"
    objectives = ("time", "cost")
    description = (
        "Genetic algorithm over ordered (station, target SoC) genes: legs along time-fastest "
        "paths, charge to max(gene target, just enough for the next leg), tournament selection, "
        "order-preserving one-point crossover, insert/delete/replace/perturb mutation, elitism. "
        "Deterministic for a fixed seed; seeded with the greedy solution."
    )

    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        P = prepared
        opts = self.options
        seed = int(opts.get("seed", 0))
        n_pop = int(opts.get("population", 40))
        n_gen = int(opts.get("generations", 60))
        patience = int(opts.get("patience", 15))
        max_stops = int(opts.get("max_stops", 10))
        detour = float(opts.get("detour_factor", 1.6))
        n_elite = int(opts.get("elite", 2))
        k_tour = int(opts.get("tournament", 3))
        p_cross = float(opts.get("crossover_rate", 0.85))
        p_mut = float(opts.get("mutation_rate", 0.35))
        pen_w = float(opts.get("penalty_weight", 20.0))
        seed_greedy = bool(opts.get("seed_greedy", True))
        if n_pop < 2 or n_gen < 0 or max_stops < 0 or n_elite < 0 or n_elite >= n_pop or k_tour < 1:
            raise ValueError("invalid GA options (population >= 2, generations >= 0, 0 <= elite < population, ...)")
        if detour < 1.0:
            raise ValueError("detour_factor must be >= 1")

        tr = SearchTrace() if trace else None
        rng = random.Random(seed)
        problem = _Problem(P, tr, pen_w)
        net = P.net
        dest = P.destination

        # ---------------------------------------------------- candidates
        rev_dist, _, _ = dijkstra_traced(net, dest, problem.w_fast, reverse=True)
        cand = [s for s in detour_candidates(P, detour) if rev_dist[P.stations[s].node] < INF]
        greedy_stops: list[tuple[int, float]] | None = None
        greedy_info: dict[str, int] = {}
        if seed_greedy and P.stations:
            run = run_greedy(P, time_lb=rev_dist, trace=tr)
            greedy_info = {"trees": run.trees, "settled": run.settled}
            if run.ok and len(run.stops) <= max_stops:
                greedy_stops = run.stops
                for s, _ in run.stops:
                    if s not in cand:
                        cand.append(s)
        cand.sort()
        problem.target_nodes = {P.stations[s].node for s in cand} | {dest}
        dist_o, _ = problem.tree(P.origin)
        cand = [s for s in cand if dist_o[P.stations[s].node] < INF]
        rank = {s: dist_o[P.stations[s].node] for s in cand}

        stats = SearchStats(algorithm=self.name)
        stats.extra = {"seed": seed, "population": n_pop, "candidates": len(cand), "greedy_seed": None}

        # ------------------------------------------ the plain fastest path
        base = problem.leg(P.origin, dest)
        if base is None:
            stats.settled = problem.settled_nodes
            return infeasible_plan(P, self.name, f"destination {dest} is not reachable from origin {P.origin}", stats, tr)
        problem.calibrate()

        route_s = max(dist_o[dest], 1.0)
        usable = max(P.e_charge_cap - P.e_min, 1e-6)
        range_s = max(usable / max(base.cost, 1e-6) * route_s, 60.0)
        evo = _Evolution(problem, rng, cand, rank, max_stops=max_stops, range_s=range_s, route_s=route_s)

        # --------------------------------------------- initial population
        pop: list[Chromosome] = [()]
        if greedy_stops is not None:
            pop.append(evo.normalise([(s, round(y / P.capacity, 4)) for s, y in greedy_stops]))
            stats.extra["greedy_seed"] = [s for s, _ in greedy_stops]
        seen = set(pop)
        attempts = 0
        while len(pop) < n_pop:
            attempts += 1
            c = evo.random_chromosome()
            if c in seen and attempts < 20 * n_pop:  # prefer distinct individuals
                continue
            seen.add(c)
            pop.append(c)
        pop = pop[:n_pop]
        fits = [problem.fitness(c)[0] for c in pop]

        history: list[float | None] = []
        history_fit: list[float] = []
        history_mean: list[float] = []
        history_feas: list[float] = []
        best_obj: float | None = None
        best_idx = min(range(len(pop)), key=lambda i: (fits[i], i))
        best, best_fit = pop[best_idx], fits[best_idx]

        def record() -> None:
            """Append the statistics of the current population to the histories."""
            nonlocal best_obj
            n_feas = 0
            for c in pop:
                _, ok, obj = problem.peek(c)
                if ok:
                    n_feas += 1
                    if best_obj is None or obj < best_obj:
                        best_obj = obj
            history.append(best_obj)
            history_fit.append(min(fits))
            history_mean.append(sum(fits) / len(fits))
            history_feas.append(n_feas / len(pop))

        record()

        # ------------------------------------------------------ evolution
        stagnant = 0
        stop_reason = "generations"
        gens_run = 0
        n_imm = max(1, n_pop // 10)
        if not cand or max_stops == 0:
            n_gen = 0  # only the empty chromosome exists
        for gen in range(1, n_gen + 1):
            gens_run = gen
            order = sorted(range(len(pop)), key=lambda i: (fits[i], i))
            new_pop: list[Chromosome] = [pop[i] for i in order[:n_elite]]
            n_off = n_pop - n_imm
            while len(new_pop) < n_off:
                a = evo.select(pop, fits, k_tour)
                b = evo.select(pop, fits, k_tour)
                crossed = rng.random() < p_cross
                children = evo.crossover(a, b) if crossed else (a, b)
                for c in children:
                    if len(new_pop) >= n_off:
                        break
                    if not crossed or rng.random() < p_mut:
                        c = evo.mutate(c)
                    new_pop.append(c)
            while len(new_pop) < n_pop:
                new_pop.append(evo.random_chromosome())
            pop = new_pop
            fits = [problem.fitness(c)[0] for c in pop]
            record()
            cur = min(range(len(pop)), key=lambda i: (fits[i], i))
            if fits[cur] < best_fit - 1e-9 * max(1.0, abs(best_fit)):
                best, best_fit = pop[cur], fits[cur]
                stagnant = 0
            else:
                stagnant += 1
                if stagnant >= patience:
                    stop_reason = "stagnation"
                    break

        # --------------------------------------------------------- result
        stats.settled = problem.decodes
        stats.generated = problem.lookups
        stats.extra.update(
            {
                "history": history,
                "history_fitness": history_fit,
                "history_mean": history_mean,
                "history_feasible": history_feas,
                "generations_run": gens_run,
                "stop_reason": stop_reason,
                "decodes": problem.decodes,
                "cache_hits": problem.lookups - problem.decodes,
                "leg_nodes_settled": problem.settled_nodes,
                "greedy_seed_effort": greedy_info,
                "best_genes": [list(g) for g in best],
            }
        )
        dec = problem.decode(best)
        if dec is None:  # pragma: no cover - the best individual was decodable
            return infeasible_plan(P, self.name, "no decodable chromosome found", stats, tr)
        plan = evaluate(
            P,
            PlanDecision(dec[0], dec[1], dec[2]),
            algorithm=self.name,
            internal_objective=None,
            stats=stats,
            trace=tr,
        )
        if not plan.feasible:
            msg = (
                f"no feasible station sequence found in {gens_run} generation(s) "
                f"(best individual: {plan.message or 'infeasible'})"
            )
            return infeasible_plan(P, self.name, msg, stats, tr)
        return plan
