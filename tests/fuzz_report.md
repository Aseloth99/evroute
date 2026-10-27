# Cross-validation (fuzz) report — `tests/test_crossval.py`

`tests/test_crossval.py` is a permanent, seeded, adversarial regression suite:
it throws random instances at **every** planner and checks each answer against
the other planners, against the independent evaluator (`evaluate.py`), against
a second first-principles replay written from `docs/DEV_SPEC.md` §1.2, against
brute force, and against metamorphic relations.  This file summarises what it
checks, how much of the instance space each invariant sees, and the bugs it
found.

```
python3 -m pytest tests/test_crossval.py -q                  # fast tier (default), ~20 s
python3 -m pytest -m slow tests/test_crossval.py -q          # slow tier (or EVROUTE_SLOW=1)
```

## 1. Instance space

Every instance is rebuilt deterministically from its seed
(`sample_instance(seed, "fast" | "slow")`, `sample_tiny(seed)`), and every
failure message carries `Instance.describe()`, so any report can be replayed.

| dimension | values |
|---|---|
| generator | jittered grid 3x3..7x7 (slow 14x14), random geometric graph 18..50 nodes (slow 110), mountain corridor 150..350 km (slow 520), tiny random digraph <= 7 nodes with one-way roads and steep hills (brute force) |
| vehicle | every preset in `VEHICLES` |
| conditions | -15..40 degC, wind, cold DC derating without preconditioning, payload, traffic factor |
| battery | initial SoC 0.05..0.95, also *below the reserve* at an origin station; reserve and arrival requirement 0..0.3 (0 on purpose: zero-reserve knife edges); charging cap 0.6..1 |
| options | 1-3 speed levels, objective time / cost, queues off / static / time dependent, departure near the demand peaks or midnight, safety factor 1..1.3, target step 0.01..0.1 |
| stations | stacked stations (several at one node, AC + DC), stations at the origin and at the destination |
| edge cases | 16 odd-but-legal variants on a grid and on a tiny digraph: cap below the reserve, arrival requirement above the cap, empty battery at an origin station, full battery above the cap, zero reserve, coarsest / finest grid (0.5 / 0.001), departure 1 s before midnight and after 24 h, origin = destination (also with a forced charge), no stations, unreachable destination, all stations on one node, safety factor 1.3 without queues |

## 2. Invariants

| id | what is checked |
|---|---|
| I1 | every plan of every planner (and every Pareto member) replays identically through `evaluate()` *and* through the independent replay of the test file; `check_objective` holds; exact planners report the evaluated objective (1e-6 rel.); per-node bookkeeping (times/distances increase, reserve, capacity, stops, totals, SoC profile); determinism of BLS-A* on a re-prepared copy and of the GA |
| I2 | A* = Dijkstra order: `bls == bls-astar` (tight and basic potential), `cfp == cfp-astar` (tight and basic); every potential is consistent on random states of the battery-expanded graph (drive and charge transitions, monotone in the energy, 0 at the goal), CFP's rate bound (Lemma 2 of `cfp.py`), the Pareto completion bounds; `trace=True` and `exact_switching=False` relations |
| I3 | CFP (continuous charging) is never slower than BLS; a finer target grid that contains a coarser one is never worse (both `targets` modes); `targets="grid+breaks"` never worse than `"grid"`; BLS/Pareto targets lie on the target set |
| I4 | Pareto: fastest member = BLS(time), cheapest generalised cost = BLS(cost), members feasible and mutually non-dominated (an equally fast member up to 1e-12 relative counts as a dominator); option variants (`goal_directed=False`, `epsilon`, two criteria) agree with the exact front |
| I5 | no feasible plan of any planner (heuristics, BLS under the cost objective, Pareto members) is faster than CFP, plans with consecutive stops at one node included |
| I6 | if any planner finds a feasible plan, every exact planner does (modulo `max_labels`) |
| I7 | zero-noise Monte Carlo reproduces `evaluate()`; the adaptive policy never strands more often than same-target; a plan made with safety factor > 1 is feasible and no slower at factor 1, and the factor-1 optimum is never worse |
| I8 | lossless serialisation: `Scenario.to_dict` is JSON-normal and a fixed point, the reloaded scenario prepares bit-identical data (network, edge times/energies, charging functions, bounds), every plan replays to the *identical* objective on it, and so does the decision rebuilt from `RoutePlan.to_dict` alone |
| I9 | brute force on tiny digraphs: an exhaustive enumeration of walks (each node at most twice) x up to two stops per node visit (consecutive stops at different stations) x targets equals BLS / BLS-A*, CFP is no slower than the enumerated optimum (time objective), and the enumerated Pareto front equals the planner's front |
| I10 | metamorphic relations: relabelled nodes/edges/stations, a duplicated station, a slower parallel road, another departure without queues give the same optimum; more initial energy, an extra station, an extra speed level, a lower reserve / arrival requirement, no queues, a higher charging cap, safety factor 1 give a no worse optimum |
| I11 | local search: random feasible neighbours (other targets, dropped / added / moved stops, other speed levels, random detours) of the BLS-A* / CFP-A* optima and of Pareto members are never better (dominating) |

## 3. Coverage

"instances" = instances on which the invariant was checked at least once;
"checks" = individual assertions.  Collected with the suite's own `Tally`
over exactly the instances of each tier.

The tallies of this section were collected at revision `bcfb4b7`, *before*
the CFP fixes for BUG-3 and BUG-4 (section 5): at that time I5 skipped plans
with two stops at one node visit, I9 compared CFP with the one-stop
enumeration and I11 ignored neighbours with two stops at one node visit.  The
suite no longer has these restrictions; the tallies have not been collected
again since, so the I5 / I9 / I11 rows and the skip counts below describe the
earlier revision.

### Fast tier (default `pytest`): 176 instances, 0 failures (revision `bcfb4b7`)

96 random instances (61 grid, 21 rgg, 14 corridor), 48 tiny digraphs (brute
force) and 32 edge-case variants.

| invariant | instances | checks |
|---|---:|---:|
| I1 evaluator / replay / bookkeeping | 175 | 23 523 |
| I2 A* = Dijkstra, potentials | 128 | 1 567 |
| I3 CFP <= BLS, grid nesting, targets | 114 | 531 |
| I4 Pareto extremes / non-dominance | 45 | 243 |
| I5 nothing beats CFP | 86 | 2 642 |
| I6 feasibility agreement | 86 | 561 |
| I7 robustness / safety factor | 68 | 572 |
| I8 lossless serialisation | 128 | 1 143 |
| I9 brute force (tiny) | 47 | 287 |
| I10 metamorphic | 96 | 288 |
| I11 local search | 82 | 4 255 |

Skips: Pareto on instances above 30 nodes / 3 speed levels / step < 0.04 (55)
or at its label limit (3); I5 for plans with two stops at one node visit
(135; this skip was removed with the BUG-3 fix for CFP); I11 where no
neighbour exists (9); brute force above 400 000 enumeration steps (1).

### Slow tier (`-m slow`, revision `bcfb4b7`)

2000 random instances (`SLOW_PER_BLOCK` x `SLOW_BLOCKS`) and 1200 tiny
digraphs.  Samples run for this report (the per-invariant table is the first row):

| sample | instances | failures | time |
|---|---:|---:|---:|
| random seeds 0..649 (`check_instance(..., SLOW)`) | 650 (402 grid, 144 rgg, 104 corridor) | 0 | 699 s |
| tiny digraphs 10000..11199 (`check_brute_force`) = the whole slow I9 tier | 1200 | 0 (1 before the BUG-1 fix, see below) | 128 s |
| random seeds 0..199 again, after the BUG-1 fix with the stricter I4 / I11 tie rule | 200 | 0 | 483 s |

| invariant | instances | checks |
|---|---:|---:|
| I1 | 650 + 1176 tiny | 123 540 + 15 918 |
| I2 | 650 | 7 114 |
| I3 | 644 | 2 892 |
| I4 | 55 | 276 |
| I5 | 395 | 14 203 |
| I6 | 399 | 2 353 |
| I7 | 399 | 3 235 |
| I8 | 650 | 5 457 |
| I9 | 1176 tiny | 6 780 |
| I10 | 573 | 2 292 |
| I11 | 399 | 16 456 |

Skips in the random sample: Pareto above 50 nodes / step < 0.04 (533) or at
its label limit (14); label limits of BLS (32), BLS-A* (18), CFP (43), CFP-A*
(11) -- the derived checks of that planner are skipped on that instance; I5
for plans with two stops at one node visit (737; removed with the BUG-3 fix
for CFP); a Pareto member dominated by
an equally fast member, i.e. BUG-1 (6, before its fix).  24 tiny digraphs
exceed the enumeration budget (up to two stops per node visit).  Before the
BUG-1 fix the tiny tier had one failure, seed 10850: a 3-node instance on
which the Pareto front kept a member that is 1 ulp faster and 6.26 EUR dearer
than another; it is frozen as
`test_bug1_pareto_front_differs_from_brute_force_on_a_tie`.  (Run while the
algorithm files were being edited concurrently; the numbers describe the
suite, not a release.)

## 4. Run time of the fast tier

| | wall time |
|---|---|
| before | never finished: the edge-case test hung (killed after > 15 min) |
| after | 21.7 s: `pytest -m "not slow" tests/test_crossval.py` -> 22 passed, 3 xfailed (revision `bcfb4b7`, before the CFP / Monte Carlo fixes of BUG-3 and BUG-4; 4 cores shared with other jobs) |
| now | 18.9 s: the same command -> 25 passed (the three former strict xfails are plain regression tests; 4 cores) |

The cause was not the instance mix but a test-harness bug: `neighbours()`
(the I11 mutation generator) drew random operations in a `while True` loop and
never returned when *no* operation applies -- an edge-less plan (origin =
destination) without an origin station, or a one-road plan without a detour,
station or second speed level.  `test_edge_cases_are_consistent[grid]`
("origin = destination") and `[tiny]` ("full battery above the charging cap",
via the Pareto front's I11) spun forever.  It now gives up after 64 draws and
returns `None` (regression test
`test_neighbour_search_terminates_without_applicable_moves`).  The performance
pass on `labels.py`, `cfp.py` and `search.py` did the rest: one random block of
16 instances takes 1-4 s.

## 5. Bugs found

Each bug is reproduced by a test at the end of `tests/test_crossval.py`:
`xfail(strict=True)` while open, a plain regression test once fixed.

| id | component | bug | status |
|---|---|---|---|
| BUG-1 | `pareto.py` | The front keeps members that a later member dominates.  Lemma 3 (a new member never dominates an existing one) assumes exact key order, but routes with equal trip times in exact arithmetic (the same energy charged at two stations with the same power) reach the destination through different float sums, and the goal-directed keys `t + T_lb(v)` are not monotone in floating point; the dearer route is popped first and stays.  Variant: a member 1 ulp faster but 0.77 EUR dearer survives. | **fixed** in `pareto.py` (robust front maintenance: a new route that a member covers is rejected, and members that an equally fast -- `TIE_RTOL = 1e-12` -- but cheaper route covers leave the front).  The three reproductions (`test_bug1_*`, incl. the 3-node instance found by the slow tier) are plain regression tests now, and I4 / I11 count an equally fast dominator (up to `1e-12` relative) as a failure instead of skipping it |
| BUG-2 | `network.py` | `Scenario.to_dict` was lossy: `RoadNetwork.to_dict` rounded x/y/elevation to 1 cm and lengths to 1 mm, so a saved and reloaded scenario had different edge energies -- objective values moved by ~1e-6 relative and exact just-enough CFP plans replayed as infeasible (reserve undercut by ~1e-5 kWh). | **fixed**: the dict is lossless (`json` writes `repr(float)`, which reads back bit-identically); `to_dict(compact=True)` keeps the old rounding for display payloads.  `Scenario.to_dict` is JSON-normal (tuple demand profiles, `speed_factors`, `meta`).  Regression tests `test_bug2_scenario_json_round_trip_reproduces_the_plan`, I8 (now exact), `test_foundation.py::test_scenario_json_round_trip_is_lossless` |
| BUG-3 | `labels.py`, `pareto.py`, `cfp.py` | At most one charging stop per node visit, justified by "two consecutive stops would pay waiting and setup twice" -- false with two *different* stations at one node: fast DC to 80 % then cheap AC to 90 % costs 90.72 vs the claimed grid optimum 91.75 (cost objective); with zero setup, DC to 37.4 kWh then AC to the just-enough energy is 16 s faster than the "exact" CFP optimum.  `evaluate()` accepts both. | **fixed** in BLS, BLS-A* and Pareto (consecutive stops at different stations of one node; `test_bug3_consecutive_stops_at_two_stations_beat_the_grid_optimum`) and in CFP / CFP-A* (a label created by a stop spawns stops at every *other* station of its node, at the switch candidates of its own function; one settled set per node stays exact, `docs/algorithms.md` §4): `test_bug3_consecutive_stops_beat_the_continuous_optimum` and `test_cfp.py::test_consecutive_stops_match_an_exact_dynamic_programme` (an exact dynamic programme over breakpoint energies and the charger in use) are plain regression tests.  I5, I9 and I11 no longer restrict CFP's competitors to one stop per node visit |
| BUG-4 | `evaluate.py` (+ `cfp.py`, `robustness.py`) | With `reserve_soc = min_arrival_soc = 0`, `evaluate()` flagged "battery empty" as soon as the energy was below 0.0 by any amount, contradicting the 1e-6 kWh tolerance of the reserve and arrival checks: a route arriving with -9e-16 kWh was returned by BLS/BLS-A*/Pareto/greedy/station-graph as infeasible, while CFP, which adds a private 1e-8 kWh floor for exactly this reason, reported that no route exists. | **fixed**: the evaluator's battery is empty below `-TOL_KWH` only (`test_bug4_zero_reserve_float_noise_is_not_stranding`, `test_foundation.py::test_evaluator_empty_battery_uses_the_energy_tolerance`); `cfp.py` no longer raises a zero reserve / goal energy to a private 1e-8 kWh floor in `_search` (`test_bug4_cfp_zero_reserve_float_noise_is_not_stranding`); the Monte Carlo kernel of `robustness.py` strands below `-TOL_KWH` like `evaluate()`, in `_Outcomes.events` and in the `_verify` cross-check (`test_bug4_monte_carlo_zero_reserve_float_noise_is_not_stranding`).  All three are plain regression tests |
| TD-waits dominance | `labels.py` | The 3-D cost dominance `(g, t, e)` used with time-dependent waits is unsound: a label that is earlier *and* cheaper in `g` is not better when a FIFO queue absorbs its head start (the waiting is paid at the value of time while the other label's money advantage persists).  On a hand-built line the old rule discarded the slow, cheap AC charge that leads to the optimum. | **fixed** in `labels.py` (dominance on `m = g - vot*t`, `t`, `e`); BLS, BLS-A* and Pareto match brute force (`test_cost_dominance_with_time_dependent_waits_regression`) |
| harness | `test_crossval.py` | `neighbours()` looped forever when no modification applies (section 4). | **fixed** (`test_neighbour_search_terminates_without_applicable_moves`) |

## 6. Open items

None.  The items listed here in earlier versions of this report are
resolved: BUG-3 for CFP and BUG-4 in `cfp.py` and `robustness.py` (section 5),
and the viewer payload size -- `viz/export.py::_network_block` builds the
network block with `RoadNetwork.to_dict(compact=True)` again (rounded to
1 cm / 1 mm; the viewer only draws the network and the live server plans on
its own `Scenario`, never on the payload).

## 7. Charging tariffs — `tests/test_crossval_tariffs.py`

```
python3 -m pytest tests/test_crossval_tariffs.py -q          # fast tier, 48 instances, ~40 s
python3 -m pytest -m slow tests/test_crossval_tariffs.py -q  # slow tier, 460 instances
```

The instances of this suite (`sample_instance`, `sample_tiny`) get a random
`Tariff`: a high-SoC tier on or off, zero to two peak intervals (wrapping
midnight, all day, `(24, x)`), peak factors 0.3..4 with or without their own
high-SoC factor, and busy hours from the queue model.  The peak is usually
aimed at a stop of the fastest route without a tariff (dear at the arrival and
ending soon after it, or a cheap period that begins soon after it), and most
instances use the cost objective with a value of time of 2..40 per hour, so
that holding pays.  `tiniest_instance` builds O -> A -> D lines (sometimes a
second path, stacked stations, an origin station) with the peak aimed at the
arrival at A.  The oracle (`TariffOracle`) never reads the price tables of
`PreparedScenario`: it re-derives periods, price changes, money and targets
from the `Tariff` and the station data, and takes charging times, wear and
waits from the scenario prepared with the neutral tariff.

| id | what is checked |
|---|---|
| T1 | every plan of every planner (Pareto members included) replays identically through `evaluate()`, through the oracle's first-principles replay (hold, wait at the join time, money of the period at the join time) and from its JSON (`RoutePlan.to_dict`, holds rebuilt from `hold_until_s`); `check_objective`; exact planners report the evaluated objective; `peak` flags and holds of the stops; time components add up |
| T2 | periods, hold candidates, money and targets equal the oracle's; charging times and wear equal the neutral tariff's; the bounds' cheapest money covers both periods; FIFO queues; a tariff that changes no price gives identical plans, fronts and search statistics (BLS-A* and two random planners) |
| T3 | `bls == bls-astar` (tight, basic), `cfp == cfp-astar` |
| T4 | Pareto: fastest member = BLS(time), cheapest = BLS(cost), members non-dominated |
| T5 | the time objective never holds (BLS-A*, CFP, CFP-A*, fastest Pareto member); its optimum does not depend on prices; CFP's does not change with the tier's breakpoint; nothing beats CFP |
| T6 | raising a price factor, or adding peak / busy hours when the peak is dearer, never lowers the optimal cost, and the dearer tariff's Pareto front is covered by the cheaper one |
| T7 | relabelling, a duplicated station, departure + 24 h, departure and peak hours shifted by whole hours (no queues; busy hours through rotated demand profiles) |
| T8 | tiny digraphs: an exhaustive enumeration (each node at most twice, up to two stops per visit, the same station again included, every target, holds until every price change in `(t, t + 24 h]`) never beats BLS, equals it whenever BLS's plan is enumerated, and its front is covered by the Pareto front; tiniest instances: a branch and bound that may also hold until every 5-minute step of the next 24 h never beats BLS, at the instance's value of time and at a second one (where the Pareto front's cheapest member equals BLS) |
| T9 | random neighbours of the optimum (holds moved to arbitrary times, dropped, added) are never better; zero-noise and noisy Monte Carlo with `verify=True` keep the holds; lossless scenario JSON |

Runs at the revision of the tariff feature (with the BUG-T1 fix), 4 cores
shared with other jobs, `nice -n 10`, all with **0 failures**:

| instances | count | notes |
|---|---:|---|
| cross-validation instances, fast profile (`tariff_instance`, seeds 0..239) | 240 | 115 342 T1, 5 547 T2, 1 941 T3, 306 T4, 15 307 T5, 217 T6, 476 T7, 6 166 T9 checks; Pareto members that hold: 6 477, that restart at a station: 4 093; run again after T6 gained the Pareto check |
| cross-validation instances, slow profile (seeds 1000..1044) | 45 | ~8 s per instance |
| tiny digraphs, brute force (seeds 0..399) | 400 | 2 328 T8 checks; 24 skipped (enumeration budget) |
| tiniest lines, 5-minute hold grid (seeds 0..139) | 140 | BLS holds on 108 of them; 7 of the 280 bound checks skipped (budget) |

**Neutral tariff = previous release.**  Outside the test suite, every planner
(BLS, BLS-A*, CFP, CFP-A*, Pareto, greedy, station-graph, naive, genetic) was
run on the same 150 fast-profile and 120 tiny instances, both objectives,
with the source of `cf328d1` (before the tariffs) and with the new source:
all 4 726 plan dicts (decisions, objective values, per-node arrays, stops,
settled / generated / pushed counts, Pareto alternatives) are identical,
apart from the run times and the new keys `hold_time_s`, `hold_s`, `peak`,
`hold_until_s`.

| id | component | bug | status |
|---|---|---|---|
| BUG-T1 | `plan.py`, `evaluate.py`, `robustness.py` | A stop recorded only `arrival_s` and `hold_s = hold_until - arrival`, and a plan rebuilt from its stops (`robustness` for a plan without its decision; any reader of `RoutePlan.to_dict`) held until `arrival_s + hold_s`.  For 102 of 29 950 hold candidates in a probe that sum differs from the decided hold in the last bit, and 60 of them land on the other side of the price change: on a 3-node line (peak 16:00-20:00 at three times the price) the replay of the plan joined 1 ulp before 16:00 and was priced 10.74 instead of 32.22. | **fixed**: `ChargeStop.hold_until_s` keeps the exact join time (`None` without a hold; `join_s` and `departure_s` use it), `evaluate()` sets it and `robustness._decision` rebuilds holds from it; documented in `docs/DEV_SPEC.md` (stop dict).  Regression test `test_bug_t1_a_plan_rebuilt_from_its_stops_joins_at_the_exact_hold_time` |
