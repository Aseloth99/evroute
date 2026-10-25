# EVRoute — developer specification (contract for all contributors)

EVRoute is meant to become a **reference implementation** for electric-vehicle
route planning with charging stations: exact algorithms, honest baselines,
an independent verifier, reproducible experiments for a journal paper, and a
beautiful animated viewer.  Read this file completely before writing code.

## 0. Ground rules

* **Core is pure Python ≥ 3.10, zero dependencies.** `matplotlib` and `seaborn`
  are allowed only inside `evroute/viz/figures.py`, `evroute/viz/style.py`
  and `evroute/experiments/`, imported lazily.  `pytest` for tests.  Optional
  third-party loaders (e.g. `osmnx`) must be imported lazily inside functions
  with a helpful error message.
* Type hints, module and public-function docstrings, small functions.  Match
  the style of the existing modules (`network.py`, `scenario.py`, ...).
* **Units are fixed** (see `src/evroute/units.py`): m, s, kWh, kW, m/s,
  currency, SoC fractions.  Never mix.
* **Determinism**: anything random takes a `seed` and uses `random.Random(seed)`.
* Never edit a file you do not own (see your task).  If the foundation needs a
  change, describe it in your final report instead of editing it.
* Do **not** run any `git` command.  Run only your own test files
  (`python3 -m pytest tests/test_<yours>.py -q`), plus
  `tests/test_foundation.py` if you want — other agents are writing other
  modules at the same time and their tests may be mid-edit.
* The package is installed in editable mode (`pip install -e .` already done),
  so `import evroute` works from anywhere.

## 1. Model summary (implemented in the foundation)

| module | what it gives you |
|---|---|
| `network.py` | `RoadNetwork` (flat lists: `x, y, elevation, tail, head, length, speed_kmh, road_class, twin, out_edges, in_edges, node_name`), `NetworkBuilder`, `ROAD_CLASSES`, `largest_strongly_connected_component` |
| `vehicle.py` | `Vehicle`, `ChargingCurve` (piecewise-constant power over SoC bins), `AgingModel`, presets `VEHICLES` / `get_vehicle` |
| `energy.py` | `PhysicsEnergyModel.traverse(net, e, speed_factor) -> Traversal(time_s, energy_kwh, ...)` |
| `charging.py` | `ChargingFunction`: exact piecewise-linear `time_at(e)`=F(e), `energy_at(t)`=F⁻¹(t), `charge_time(e1,e2)`, `energy_after(e, sec)`, `money(e1,e2)`, `soh(e1,e2)`, `e_breaks`, `t_breaks`, `breakpoints_between(e1,e2)`, `power_at(e)` |
| `queueing.py` | Erlang-C M/M/c waits, FIFO-safe `WaitProfile` |
| `scenario.py` | `Scenario` (serialisable instance), `Station`, `Conditions`, `Preferences`, `Query`, `PreparedScenario` |
| `plan.py` | `PlanDecision`, `ChargeDecision`, `RoutePlan`, `ChargeStop`, `SearchStats`, `SearchTrace` |
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
P.speed_factors            # e.g. (1.0,) or (1.0, 0.9, 0.8)
P.edge_options[e]          # tuple of (k, time_s, energy_kwh, drive_cost) non-dominated speed levels
P.edge_time[k][e], P.edge_energy[k][e]
P.stations                 # list[Station];  station index s = position in this list
P.station_at[node]         # -> list of station indices at that node
P.is_station_node[node]    # bool
P.cf[s]                    # ChargingFunction of station s (energy units, exact)
P.wait_s(s, trip_s)        # expected queueing delay when arriving trip_s seconds after departure
P.setup_s[s]               # plug-in/out overhead, paid at every stop
P.time_dependent           # True if waits depend on arrival time (FIFO guaranteed)
P.target_grid              # sorted charging targets [kWh] on the charge_step_soc grid (<= e_charge_cap)
P.objective                # "time" or "cost"
P.drive_cost(t, c)         # generalised cost of a drive segment
P.charge_cost(s, e1, e2, charge_s, wait_s)  # generalised cost of a stop (incl. setup)
P.vot_per_s, P.w_money, P.w_deg, P.drive_soh_per_kwh, P.p_max, P.min_money_rate, P.min_soh_rate
```

### 1.2 Semantics every algorithm must respect (the evaluator enforces them)

1. Driving edge `e` at level `k` from energy `x`: new energy `min(x - c, capacity)`
   (regeneration is clamped at full battery).
2. Energy at every node of the path — on arrival and on departure — must be
   `>= P.e_min` (tolerance 1e-6 kWh).  The only exception is the origin's
   *arrival* state `P.e_init`, which may be below the reserve if the vehicle
   charges at the origin before leaving.
3. Arrival at the destination needs `>= P.e_dest_min`.
4. A charging stop at station `s` reached at trip time `t` with energy `x`
   to target `y` costs `P.wait_s(s, t) + P.setup_s[s] + P.cf[s].charge_time(x, y)`
   seconds, money `P.cf[s].money(x, y)`, wear `P.cf[s].soh(x, y)`.  A listed stop
   always pays wait + setup, even if `y <= x`.  Targets must be `<= P.e_charge_cap`.
5. Objective `"time"` = total trip time; `"cost"` = generalised cost
   `vot*T + w_money*money + w_deg*(soh_drive + soh_charge)`, which equals the sum
   of `P.drive_cost(...)` over driven edges plus `P.charge_cost(...)` over stops.
6. Output a `PlanDecision(edges, levels, charges)` and return
   `evaluate(P, decision, algorithm=self.name, internal_objective=<your value>,
   stats=SearchStats(...), trace=trace_or_None)`.  `ChargeDecision.position` is
   the index of the stop in the node sequence (`0` = origin, `len(edges)` =
   destination).  If nothing is found return `infeasible_plan(P, self.name, reason, stats, trace)`.
7. `check_objective(plan)` must hold for every exact/grid algorithm: the
   evaluator's objective may not exceed the internal one (1e-7 relative).
   For exact algorithms they should be equal up to float rounding.
8. When `trace=True`, record `trace.visit(node, key)` every time a label is
   settled (`SearchTrace` keeps only the first visit per node).

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
Labels: `(key, t, g, e, node, parent, how)`.  Time objective: dominance at a
node `t' <= t and e' >= e`.  Cost objective: `g' <= g and e' >= e`, and also
`t' <= t` when `P.time_dependent` (because waits depend on time).  Settled
Pareto sets per node; for 2-D use a staircase with `bisect`.
Driving: every `P.edge_options[e]`.  Charging: when a label at a station node
is settled and it was not produced by charging at that node, for every
station `s` at the node and every target `y` in
`targets = P.target_grid ∪ {interior breakpoints of P.cf[s] ≤ e_charge_cap}`
with `y > e`, push a charged label.  A* key = `primary + LowerBounds.h(v, e)`.
Stop when the first label at the destination with `e >= P.e_dest_min` is
popped.  Options: `targets="grid+breaks"|"grid"`, `max_labels` safeguard.

### 2.2 CFP (cfp.py) — continuous charging (time objective)

Label at node `v`: last station `s` (or origin), `t0` (trip time if no extra
charging at `s`), `b_s` (energy when charging started at `s`), and the path
consumption profile `(in, cost, out)` from `s` to `v`
(`f(x) = min(x - cost, out)` for `x >= in`).  The label's function
`B(t) = f(x(t))`, `x(t) = min(F⁻¹(F(b_s) + t - t0), cap)` is piecewise linear,
concave, non-decreasing.  Edge extension with `(τ, c)`:
`in' = max(in, e_min + cost + c)`, `cost' = cost + c`, `out' = min(out - c, capacity)`,
infeasible if `out' < e_min` or the needed `in'` exceeds the max reachable `x`.
At a station node, a settled label spawns one new label per breakpoint
`t'_j` of `B` (switch from charging at `s` to charging at the new station):
`t0 = t'_j + wait(t'_j) + setup`, `b = B(t'_j)`, identity profile.  Queue key =
earliest feasible time `t_feas` (+ heuristic at `B(t_feas)` for A*).
Dominance: single-label pointwise `B' >= B` (checked on the union of
breakpoints and at infinity, with `t_feas' <= t_feas`).  At the destination,
push a final entry with key = min `t` with `B(t) >= e_dest_min`; the first
final entry popped is optimal.  Exact when waits are static
(`P.time_dependent == False`), a sound heuristic otherwise.

### 2.3 Pareto (pareto.py)

Martins-style multi-criteria label setting over `(t, money, wear, -e)` with the
same transitions as BLS (charging targets on the grid).  Optional
target-pruning with lower bounds against the destination front and optional
ε-dominance.  Return the plan with the minimum generalised cost and put all
evaluated front members (sorted by time) into `plan.alternatives`.

## 3. Viewer data contract (`evroute-viewer/1`)

```json
{
  "format": "evroute-viewer/1",
  "title": "string",
  "lang": "tr|en",
  "animate": true,
  "live": false,
  "scenario": {"name": "", "description": "", "currency": "TRY",
               "vehicle": {"key": "", "name": "", "battery_kwh": 0, "capacity_kwh": 0, "dc_peak_kw": 0},
               "query": {"origin": 0, "destination": 0, "initial_soc": 0.8, "departure_s": 28800},
               "conditions": {...}, "preferences": {...}},
  "network": {"x": [], "y": [], "elevation": [], "tail": [], "head": [], "road_class": [],
              "speed_kmh": [], "twin": [], "node_name": {"12": "Ankara"}, "lat": [], "lon": []},
  "stations": [{"id": 0, "node": 3, "name": "", "power_kw": 150, "kind": "DC", "plugs": 4,
                "price_per_kwh": 0.5, "operator": "", "wait_profile": {"knots_s": [], "waits_s": []}}],
  "results": [RoutePlan.to_dict() + {"color": "#hex", "title": "BLS-A*", "exact": "grid"}],
  "algorithms": [Planner.info()...]
}
```

`RoutePlan.to_dict()` contains `nodes`, `edges`, `levels`, `stops` (list of
`ChargeStop` dicts), per-node `node_arrive_s`, `node_depart_s`,
`node_energy_arrive`, `node_energy_depart`, `node_dist_m`, totals,
`summary`, `stats`, `trace` (`{"nodes": [...], "keys": [...]}`) and
`alternatives` (Pareto front members).

## 4. Testing expectations

* Every module gets `tests/test_<module>.py`.
* Algorithms: feasibility via the evaluator, `check_objective`, and
  cross-validation on small random instances (`grid_scenario(rows, cols, seed=...)`
  with low `initial_soc` so that charging is needed):
  * `bls` and `bls-astar` return identical objective values;
  * `cfp` time ≤ `bls` time (+1e-6) on static-wait instances, and the gap
    shrinks as `charge_step_soc` shrinks;
  * heuristics (`greedy`, `station-graph`, `genetic`) are never better than
    `bls` on the same target grid... *unless* they use targets off the grid
    — compare them against `cfp` instead;
  * on tiny instances, a brute-force enumeration over simple paths and grid
    targets agrees with `bls`.
* Keep the default test-suite under ~60 s; mark long tests `@pytest.mark.slow`.
