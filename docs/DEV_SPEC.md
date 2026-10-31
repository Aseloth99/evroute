# EVRoute — developer specification (contract for all contributors)

EVRoute is meant to become a **reference implementation** for electric-vehicle
route planning with charging stations: exact algorithms, honest baselines,
an independent verifier, reproducible experiments for a journal paper, and a
beautiful animated viewer.  Read this file completely before writing code.

## 0. Ground rules

* **Core is pure Python ≥ 3.10, zero dependencies.** Charts use `seaborn`
  only, and only inside `evroute/viz/figures.py`, `evroute/viz/style.py`
  and `evroute/experiments/`, imported lazily.  `pytest` for tests.  Optional
  third-party loaders (e.g. `osmnx`) must be imported lazily inside functions
  with a helpful error message.
* Type hints, module and public-function docstrings, small functions.  Match
  the style of the existing modules (`network.py`, `scenario.py`, ...).
* **Units are fixed** (see `src/evroute/units.py`): m, s, kWh, kW, m/s,
  currency, SoC fractions.  Never mix.
* **Determinism**: anything random takes a `seed` and uses `random.Random(seed)`.
* Install in editable mode (`pip install -e ".[dev]"`, see
  [`CONTRIBUTING.md`](../CONTRIBUTING.md)).  Run the tests of the modules you
  touch (`python -m pytest tests/test_<module>.py -q`) and, before you change
  an algorithm, the cross-validation suites (`tests/test_crossval.py` and
  `tests/test_crossval_tariffs.py`, fast and slow tier).

## 1. Model summary (implemented in the foundation)

| module | what it gives you |
|---|---|
| `network.py` | `RoadNetwork` (flat lists: `x, y, elevation, tail, head, length, speed_kmh, road_class, twin, out_edges, in_edges, node_name`), `NetworkBuilder`, `ROAD_CLASSES`, `largest_strongly_connected_component` |
| `vehicle.py` | `Vehicle`, `ChargingCurve` (piecewise-constant power over SoC bins), `AgingModel`, presets `VEHICLES` / `get_vehicle` |
| `energy.py` | `PhysicsEnergyModel.traverse(net, e, speed_factor) -> Traversal(time_s, energy_kwh, ...)` |
| `charging.py` | `ChargingFunction`: exact piecewise-linear `time_at(e)`=F(e), `energy_at(t)`=F⁻¹(t), `charge_time(e1,e2)`, `energy_after(e, sec)`, `money(e1,e2)`, `soh(e1,e2)`, `e_breaks`, `t_breaks`, `breakpoints_between(e1,e2)`, `power_at(e)` |
| `queueing.py` | Erlang-C M/M/c waits, FIFO-safe `WaitProfile` |
| `scenario.py` | `Scenario` (serialisable instance), `Station`, `Conditions`, `Preferences`, `Query`, `Tariff` (high-SoC and peak-hour prices), `PreparedScenario` |
| `plan.py` | `PlanDecision`, `ChargeDecision` (with an optional voluntary hold `hold_until_s`), `RoutePlan`, `ChargeStop`, `SearchStats`, `SearchTrace` |
| `evaluate.py` | `evaluate(prepared, decision, ...) -> RoutePlan`, `infeasible_plan(...)`, `check_objective(plan)` |
| `search.py` | `dijkstra`, `bellman_ford`, `path_from_pred`, `path_from_reverse_pred`, `min_level_weights`, `LowerBounds` (`time_lb`, `energy_lb`, `cost_lb`, `h_time(v,e)`, `h_cost(v,e)`, `h(v,e)`, `deficit(v,e)`) |
| `algorithms/` | `Planner` base class (`_plan(prepared, *, trace) -> RoutePlan`), lazy `REGISTRY`, `plan(scenario, name)` |
| `generators/basic.py` | `grid_scenario(rows, cols, ..., seed)` deterministic test instances |

### 1.1 `PreparedScenario` — everything an algorithm needs

```python
P = scenario.prepare()
P.net                      # RoadNetwork
P.origin, P.destination    # node ids
P.capacity                 # usable kWh at trip temperature
P.e_min                    # reserve: energy at every node (arrival AND departure) must be >= e_min
P.e_charge_cap             # charging targets must be <= this
P.e_init                   # energy at departure
P.e_dest_min               # required energy at the destination (already >= e_min)
P.e_stop_min               # the floor: min(min_stop_soc, max_charge_soc) * capacity (0: off); §1.2 item 9
P.speed_factors            # e.g. (1.0,) or (1.0, 0.9, 0.8)
P.edge_options[e]          # tuple of (k, time_s, energy_kwh, drive_cost) non-dominated speed levels
P.edge_time[k][e], P.edge_energy[k][e]
P.stations                 # list[Station];  station index s = position in this list (every station)
P.station_usable[s]        # can the vehicle charge at s? (Station.usable_by: a socket of the station's
                           #   kind that the vehicle has)
P.station_at[node]         # -> list of the indices of the USABLE stations at that node
P.is_station_node[node]    # bool: the node has a usable station
P.cf[s]                    # ChargingFunction of station s (energy units, exact); money: off-peak price
                           #   (the only price of a station in its peak period all day)
P.wait_s(s, trip_s)        # expected queueing delay when arriving trip_s seconds after departure
P.setup_s[s]               # plug-in/out overhead, paid at every stop
P.time_dependent           # True if waits depend on arrival time (FIFO guaranteed)
P.target_grid              # sorted charging targets [kWh] on the charge_step_soc grid (<= e_charge_cap)
P.objective                # "time" or "cost"
P.drive_cost(t, c)         # generalised cost of a drive segment
P.charge_cost(s, e1, e2, charge_s, wait_s, *, join_s=None, hold_s=0.0)  # generalised cost of a stop (incl. setup)
P.vot_per_s, P.w_money, P.w_deg, P.drive_soh_per_kwh, P.p_max, P.min_money_rate, P.min_soh_rate
# charging tariff (scenario.tariff: Tariff; Tariff() is neutral and changes nothing)
P.tariff                   # the Tariff
P.cf_peak[s]               # same breakpoints/powers/wear as P.cf[s], peak money (`P.cf_peak is P.cf` if no peak price)
P.cf_variants              # every ChargingFunction whose money can apply (bounds: minimise over these)
P.peak_intervals[s]        # peak period of s: clock intervals [a, b) in seconds of the day
P.price_changes[s]         # clock times [s of the day] at which the price of s changes (() if constant)
P.tariff_time_dependent    # True if some price depends on the clock
P.is_peak(s, trip_s)       # is s in its peak period at that trip time?
P.money_cf(s, trip_s)      # P.cf[s] or P.cf_peak[s]: the money of a session joining the queue at trip_s
P.hold_candidates(s, t)    # sorted trip times in (t, t + 24 h] at which the price of s changes
                           #   (nudged by a few ulps so that P.is_peak already reports the new period)
```

`P.stations`, `P.cf`, `P.cf_peak`, `P.setup_s`, ... keep every station of the
scenario, so station indices are the same for every vehicle; only
`station_at` and `is_station_node` leave out the stations the vehicle cannot
plug into.  Find stations through `station_at`, never by scanning
`P.stations` or `P.cf` directly.

With an active high-SoC tier every charging function has a breakpoint at
`high_soc x capacity` (same power on both sides, so charging times are
unchanged), which makes it a charging target of BLS/Pareto as well.  The
model (price factors, periods, busy hours, the join-time rule, holds) is
[`model.md` §5](model.md#5-charging-tariffs); which holds a planner must
consider is [`algorithms.md` §6](algorithms.md#6-charging-tariffs-price-periods-and-voluntary-holds).
`Scenario.to_dict()` writes the `"tariff"` key only for a tariff other than
`Tariff()`, so instances without a tariff keep their earlier JSON form.

### 1.2 Semantics every algorithm must respect (the evaluator enforces them)

1. Driving edge `e` at level `k` from energy `x`: new energy `min(x - c, capacity)`
   (regeneration is clamped at full battery).
2. Energy at every node of the path — on arrival and on departure — must be
   `>= P.e_min` (tolerance 1e-6 kWh).  The only exception is the origin's
   *arrival* state `P.e_init`, which may be below the reserve if the vehicle
   charges at the origin before leaving (nothing is checked between
   consecutive stops, so a stop there may end below the reserve when another
   stop at the origin follows).
3. Arrival at the destination needs `>= P.e_dest_min`.
4. A charging stop at station `s` reached at trip time `t` with energy `x`
   to target `y` joins the queue at `j = t`, or at `j = hold_until_s` if its
   `ChargeDecision` holds voluntarily until a later time (the hold is
   `j - t`), and costs `(j - t) + P.wait_s(s, j) + P.setup_s[s] +
   P.cf[s].charge_time(x, y)` seconds, money `P.money_cf(s, j).money(x, y)`
   (the price period is fixed when the vehicle joins the queue), wear
   `P.cf[s].soh(x, y)`.  A listed stop always pays wait + setup, even if
   `y <= x`.  Targets must be `<= P.e_charge_cap`.  The evaluator computes
   the end of a stop as `t = j; t += wait + setup + charge`; planners use
   the same arithmetic.
5. Objective `"time"` = total trip time; `"cost"` = generalised cost
   `vot*T + w_money*money + w_deg*(soh_drive + soh_charge)`, which equals the sum
   of `P.drive_cost(...)` over driven edges plus `P.charge_cost(..., join_s=j,
   hold_s=j - t)` over stops (`T` includes the holds).
6. Output a `PlanDecision(edges, levels, charges)` (`ChargeDecision(position,
   station, target_kwh, hold_until_s=None)`) and return
   `evaluate(P, decision, algorithm=self.name, internal_objective=<your value>,
   stats=SearchStats(...), trace=trace_or_None)`.  `ChargeDecision.position` is
   the index of the stop in the node sequence (`0` = origin, `len(edges)` =
   destination).  If nothing is found return `infeasible_plan(P, self.name, reason, stats, trace)`.
7. `check_objective(plan)` must hold for every exact/grid algorithm: the
   evaluator's objective may not exceed the internal one (1e-7 relative).
   For exact algorithms they should be equal up to float rounding.
8. When `trace=True`, record `trace.visit(node, key)` every time a label is
   settled (`SearchTrace` keeps only the first visit per node).
9. **Connectors.**  A stop at a station `s` with `P.station_usable[s]` false
   is a violation (the vehicle has no socket of that station's kind).
10. **The floor** (`Preferences.min_stop_soc`, `P.e_stop_min > 0`): a stop
   that starts below `P.e_stop_min` must end at or above it (tolerance
   `TOL_KWH`); a stop that starts at or above it is not constrained.  BLS
   and Pareto add `P.e_stop_min` to their charging targets
   (`labels.charging_targets`) and skip targets below it for a stop that
   starts below it, CFP restricts the stop to end at or above it, and the
   heuristics raise their targets to it.  Model: `model.md` §3; proofs:
   `algorithms.md` §1.

## 2. Algorithms (registry names)

| name | module | class | guarantee |
|---|---|---|---|
| `naive` | `naive.py` | `NaivePlanner` | battery-unaware fastest path; usually infeasible (shows the problem) |
| `greedy` | `greedy.py` | `GreedyPlanner` | "charge when needed" driver heuristic |
| `station-graph` | `station_graph.py` | `StationGraphPlanner` | meta-graph of stations, fixed charge target (Storandt & Funke 2012 style) |
| `genetic` | `genetic.py` | `GeneticPlanner` | metaheuristic over station sequences + targets |
| `bls` | `labels.py` | `LabelSettingPlanner` | **exact on the charging-target grid**, Dijkstra order |
| `bls-astar` | `labels.py` | `LabelAStarPlanner` | same optimum, A* with consistent energy-deficit heuristic (flagship) |
| `cfp` | `cfp.py` | `CFPPlanner` | **exact with continuous charging amounts** (Charging Function Propagation, Baum et al.) — fastest path |
| `cfp-astar` | `cfp.py` | `CFPAStarPlanner` | CFP with the A* key |
| `pareto` | `pareto.py` | `ParetoPlanner` | multi-criteria label setting → Pareto front (time, money[, wear]) |

### 2.1 BLS / BLS-A* (labels.py)

State = (node, energy) with *continuous* energy (no rounding of consumption).
Labels: `(key, t, g, e, node, parent, how)`.  Dominance at a node: time
objective `t' <= t and e' >= e`; cost objective with static waits
`g' <= g and e' >= e`; cost objective with time-dependent waits or a time-dependent tariff
(`P.time_dependent or P.tariff_time_dependent`) `m' <= m, t' <= t and e' >= e` with
`m = g - P.vot_per_s * t`, the money and wear spent so far (comparing
`(g, t, e)` is unsound: a falling FIFO queue can absorb the head start of an
earlier label while its money disadvantage persists, `docs/algorithms.md`
§1).  One settled Pareto set per node; 2-D sets are staircases with
`bisect`, 3-D sets `_Pareto3`.
Driving: every `P.edge_options[e]`.  Charging: when a label at a station node
is settled, for every station `s` at the node except the one whose stop
created the label (consecutive stops at *other* stations of a node are
allowed, any number of times) and every target `y` in
`targets = P.target_grid ∪ {interior breakpoints of P.cf[s] ≤ e_charge_cap}`
(∪ `{P.e_stop_min}` when the floor is on) with `y > e` (and `y >= P.e_stop_min`
when `e < P.e_stop_min`), push a charged label (targets below the reserve only at a node
with several stations, i.e. at the origin with `e_init < e_min`).  With the
cost objective and `P.tariff_time_dependent` the same targets are also
pushed after a voluntary hold until each `P.hold_candidates(s, t)` (money of
the period at the join time, hold paid at `vot`), and the station that
created the label may be used again in the *other* price period
(`docs/algorithms.md` and the module docs of `labels.py`, "Charging
tariffs"); the time objective never holds.  A* key =
`primary + h(v, e)` with the default potential `AStarPotential.tight`
(`potential="basic"`: `LowerBounds.h(v, e)`).  Stop when the first label at
the destination with `e >= P.e_dest_min` is popped.  Options:
`targets="grid+breaks"|"grid"`, `max_labels` safeguard, `potential`
(BLS-A\*), `time_limit_s` (optional safeguard in seconds, status
`"time_limit"`; CFP, CFP-A\* and Pareto take it too; never set by the CLI or
the experiments, so their results stay machine-independent).  Details and proofs: module docs of `labels.py`,
`docs/algorithms.md` §1-3.

### 2.2 CFP (cfp.py) — continuous charging (time objective)

Label at node `v`: last station `s` (or `-1`, the origin), `t0` (trip time at
`v` if nothing beyond `b_s` is charged at `s`; wait and setup included),
`b_s` (energy when charging started at `s`), and the path consumption profile
`(in, cost, out)` from `s` to `v` (`f(x) = min(x - cost, out)` for
`x >= in`).  The label's function `B(t) = f(x(t))`,
`x(t) = min(F⁻¹(F(b_s) + t - t0), max(cap, b_s))`, is piecewise linear and
non-decreasing; it is concave only if the power of `s` does not increase with
the energy (the DC curves of the presets ramp up at low SoC).  Edge extension
with `(τ, c)`: `t0' = t0 + τ`, `in' = max(in, e_min + cost + c)`,
`cost' = cost + c`, `out' = min(out - c, capacity)`, infeasible if
`out' < e_min` or the needed `in'` exceeds the max reachable `x`.
At a station node, a settled label spawns, for every station `s2` of the node
other than its own `s` (consecutive stops at other stations are allowed), one
stop label per *switch candidate* `tj`: `t0 = tj + wait(tj) + setup`,
`b = B(tj)`, identity profile.  The candidates (option `exact_switching=True`,
the default) are the breakpoints of `B`, the times at which `B` crosses an
energy where the power of `s2` increases, and the knots of the wait profile of
`s2`; `exact_switching=False` uses the breakpoints only (the textbook set,
exact for concave charging functions and static waits).  A label created by a
stop skips the candidate at its own `t0`, and sibling candidates that an
earlier one dominates are not created.  Queue key: the earliest feasible time
`t_feas` (CFP), or the minimum of `t + h(v, B(t))` over the label (CFP-A\*,
default potential `"tight"`).  Dominance: set dominance -- a popped label is
discarded if the upper envelope of the settled labels of its node lies above
its function (`_Envelope`, exact on the union of breakpoints; constant labels
in a staircase), with energies capped at the completion energy `need[v]`.
Destination: the arrival time of every label generated there (least
`t >= t_feas` with `B(t) >= e_dest_min`, closed form) updates an incumbent
kept outside the heap; the search stops when the smallest key reaches it.
Exact when waits are static, or FIFO time-dependent with
`exact_switching=True`; `stats.extra["optimality"]` is then `"proven"`
(unless `max_labels` was hit).  A charging tariff changes nothing in CFP:
the time objective never holds (`docs/algorithms.md` §6, Lemma 4), and the
money of its plans is what `evaluate()` measures.
Details and proofs: module docs of `cfp.py`, `docs/algorithms.md` §4.

### 2.3 Pareto (pareto.py)

Martins-style multi-criteria label setting over `(t, money, wear, -e)` with the
same transitions as BLS (charging targets on the target set, consecutive
stops at different stations of a node included; with a time-dependent
tariff also the voluntary holds and same-station restarts of BLS's cost
objective, since money is always a criterion).  Goal-directed order
(`t + T_lb(v)`; `goal_directed=False` restores plain Martins), pruning
against the destination front with consistent completion bounds, optional
ε-dominance (`epsilon`, `epsilon_scope`) and two criteria
(`criteria=("time", "money")`).  Return the front member with the best
scenario objective (minimum trip time for `"time"`, minimum generalised cost
for `"cost"`) and put all evaluated front members (sorted by time) into
`plan.alternatives`.

### 2.4 Heuristics under a tariff

`greedy`, `station-graph`, `genetic` and `naive` never hold.  Their plans go
through `evaluate()` like every plan, so the money they report is priced in
the period at each arrival; their internal objectives must stay consistent
with it (`check_objective` is only required of the exact planners).

## 3. Viewer data contract (`evroute-viewer/1`)

```json
{
  "format": "evroute-viewer/1",
  "title": "string",
  "lang": "tr|en",
  "animate": true,
  "live": false,
  "scenario": {"name": "", "description": "", "currency": "TRY",
               "vehicle": {"key": "", "name": "", "battery_kwh": 0, "capacity_kwh": 0, "dc_peak_kw": 0,
                           "ac_max_kw": 11, "mass_kg": 0, "chemistry": "NMC|NCA|LFP",
                           "connectors": ["Type2", "CCS2"], "dc_max_kw": null,
                           "dc_curve": {"soc_breaks": [], "power_kw": []}, "aux_kw": 0.35,
                           "dc_temp_factor": 1.0},
               "query": {"origin": 0, "destination": 0, "initial_soc": 0.8, "departure_s": 28800},
               "conditions": {...}, "preferences": {...}},
  "network": {"x": [], "y": [], "elevation": [], "tail": [], "head": [], "road_class": [],
              "speed_kmh": [], "twin": [], "node_name": {"12": "Ankara"}, "lat": [], "lon": []},
  "stations": [{"id": 0, "node": 3, "name": "", "power_kw": 150, "kind": "DC", "plugs": 4,
                "price_per_kwh": 0.5, "operator": "", "wait_profile": {"knots_s": [], "waits_s": []},
                "connectors": ["CCS2"], "usable": true}],
  "vehicles": [{"key": "", "name": "", "battery_kwh": 0, "dc_peak_kw": 0, "ac_max_kw": 0,
                "chemistry": "LFP", "connectors": ["Type2", "CCS2"], "notes": ""}],
  "results": [RoutePlan.to_dict() + {"color": "#hex", "title": "BLS-A*", "exact": "grid"}],
  "algorithms": [Planner.info()...]
}
```

`dc_peak_kw` is the *effective* DC peak (capped at `dc_max_kw`; 0 for a
vehicle without a DC inlet); `dc_curve` is the vehicle's DC charging curve
before that cap, `aux_kw` the auxiliary (HVAC) power and `dc_temp_factor`
the cold derating of DC power at the trip's conditions (the viewer's
vehicle panel and energy breakdown; older payloads lack them).  A station's `connectors` are its connector
standards (`Station.sockets`: the kind's default when none are given) and
`usable` tells whether the scenario's vehicle can plug in there
(`PreparedScenario.station_usable`; the planners never stop where it is
`false`).

`RoutePlan.to_dict()` contains `nodes`, `edges`, `levels`, `stops` (list of
`ChargeStop` dicts), per-node `node_arrive_s`, `node_depart_s`,
`node_energy_arrive`, `node_energy_depart`, `node_dist_m`, totals (among
them `hold_time_s`, the voluntary holds, part of `total_time_s`),
`summary` (with `hold_time_s`), `stats`, `trace` (`{"nodes": [...], "keys": [...]}`) and
`alternatives` (Pareto front members).

A stop dict has `arrival_s`, `hold_s` (voluntary hold before joining the
queue, usually 0), `wait_s`, `setup_s`, `charge_s` -- in this order on the
clock, so the stop ends at `arrival_s + hold_s + wait_s + setup_s +
charge_s` -- the energies and SoCs, `money` (priced in the period at the
time of joining the queue), `peak` (`true` if that was the station's peak
period), `hold_until_s` (after a hold the exact trip time of joining the
queue, else `null`; rebuild a decision's hold from it, not from `arrival_s
+ hold_s`, which can round to the other side of a price change),
`soh_pct`, the station data and, in the viewer payload, `curve` (`t` counts
from the start of charging, after hold, wait and set-up).

Only for a scenario whose tariff is not `Tariff()`: `scenario.tariff`
(`Tariff.to_dict()`: `high_soc`, `high_soc_factor`, `peak_hours` as
`[[start_h, end_h], ...]`, `peak_factor`, `peak_high_soc_factor`,
`busy_utilisation`), and per station `peak_s` (`[[a, b], ...]`, clock
seconds) and `price_factors` (`{"offpeak": [below, above], "peak": [below,
above]}`, multiplying `price_per_kwh` below / above `high_soc`).

**Live server** (`server.py`).  The live page's payload (`"live": true`)
carries `demo_tariff`, the `Tariff.to_dict()` of the illustrative
`DEMO_TARIFF`, for the page's tariff switch, and `default_algorithms`, the
planners ticked before the first plan (those of a request without
`"algorithms"`: without greedy and the station graph on a network of more
than 20 000 nodes).  `POST /api/plan` accepts
`"tariff": "80"` (the demonstration tariff) or `"flat"` (no tariff; absent:
the preset's own tariff), `"min_stop_soc"` (0..0.95), `"dc_max_kw"`
(3..500, or `null` for the vehicle's own limit) and `"charge_step_soc"`
(0.01..0.25, the charging-target grid step; everyday use re-plans with 0.05
when BLS-A* stops at its label budget on the preset's grid) and answers with
`results_payload(...)`: `{"scenario", "results", "wait_profiles",
"station_usable"}` (`station_usable` in station order, for the request's
vehicle) plus, for a tariff other than
`Tariff()`, `station_tariffs` (per station, in station order, the `peak_s`
and `price_factors` above).

The logic behind the four endpoints is HTTP-free: `PlanningService` (presets
on offer, scenario cache, planner `options`) and `api_call(service, endpoint,
request) -> (status, answer)` / `api_call_json` (the same with JSON text).
`live.js` reaches it through a *transport*: `HttpTransport` (`evroute
serve`) or, on the web site, `WorkerTransport`, which talks to a Web Worker
(`site/worker.js`) running EVRoute under Pyodide.  The web site's live page
carries a `"worker"` block in its payload (`tools/build_site.py`): `script`,
`pyodide` (pinned CDN index URL) and `pyodide_version`, `integrity` (SHA-256
of every file the worker may take from the CDN; it refuses any other file
and any mismatch), `wheel` (site-relative path), `service` (the
`PlanningService` keyword arguments, with the browser's label and time limits
as `options`), `time_budget_s`, `time_limit_s`, `download_bytes`, `sizes`
(decoded bytes per loading step, as the worker counts them), `transfer`
(bytes transferred per step, as shown) and `install`.  Messages: page →
worker `{type: "init", config, prepare}` (`prepare`: the preset to get ready
first), `{type: "call", id, endpoint, args}`; worker → page `{type:
"progress", phase, loaded, total}`, `{type: "ready", ...}`, `{type:
"failed", message, reason}` (`reason` `"stale"`: the page names a wheel the
site no longer has -- reload; `"integrity"`: a CDN file did not match), `{type:
"reply", id, status, body, fatal}` (`args` and `body` are JSON text).  The
page plans one algorithm per request, the fast exact planners first, shows
the results so far during a long run, and terminates and restarts the worker
when a planner runs over `time_budget_s` or the visitor presses *Stop*.  A
result whose `stats.extra.status` is `"limit"` or `"time_limit"` or whose
`complete` is `false` is shown as possibly incomplete; planners that did not
answer are listed in `errors` with `budget` `"time"`, `"cancelled"` or
`"skipped"`, and a run without any answer keeps the trip in the controls and
shows no results.  The site's viewer pages also carry `"site": {"home",
"repo"}` (the logo leads home, a GitHub link) and `"lang_auto": true`.  The
viewer starts in the language of `?lang=` / `#lang`, else the one saved by a
language switch (`localStorage["evroute.lang"]`, shared with the results
dashboard and the site), else -- with `lang_auto` -- the browser's, else the
payload's `lang`.

## 4. Testing expectations

* Every module gets `tests/test_<module>.py`.
* Algorithms: feasibility via the evaluator, `check_objective`, and
  cross-validation on small random instances (`grid_scenario(rows, cols, seed=...)`
  with low `initial_soc` so that charging is needed):
  * `bls` and `bls-astar` return identical objective values;
  * `cfp` time ≤ `bls` time (+1e-6), and the gap shrinks as
    `charge_step_soc` shrinks;
  * heuristics (`greedy`, `station-graph`, `genetic`) are never better than
    `bls` on the same target grid... *unless* they use targets off the grid
    — compare them against `cfp` instead;
  * on tiny instances, a brute-force enumeration of walks (each node at most
    twice), up to two stops per node visit (consecutive stops at different
    stations) and targets on the target set agrees with `bls`, `bls-astar` and
    the Pareto front, and `cfp` is no slower.
* `tests/test_crossval.py` runs all of these, and more, on seeded random
  instances (summary in `tests/fuzz_report.md`).
* Charging tariffs: `tests/test_tariffs.py` (model, evaluator, hand-made
  instances) and `tests/test_crossval_tariffs.py` (random tariffs against an
  independent price oracle, brute force with holds; `fuzz_report.md` §7).
  With the neutral `Tariff()` every plan, objective and search statistic
  must stay identical to the code without tariffs.
* Keep the default test-suite under ~60 s; mark long tests `@pytest.mark.slow`.
