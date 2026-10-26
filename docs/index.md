# EVRoute documentation

EVRoute plans electric-vehicle trips with charging stops: exact algorithms
for a model that includes nonlinear charging, partial charging, queues,
terrain and regeneration, temperature, battery wear and uncertainty.  This
page is the map of the documentation; the [README](../README.md)
([Türkçe](../README.tr.md)) is the tour.

| document | what is in it |
|---|---|
| [`model.md`](model.md) | the optimisation problem, equation by equation, with the module that implements each part |
| [`algorithms.md`](algorithms.md) | the algorithms and their correctness arguments: BLS, the consistent A\* potentials, CFP, the Pareto search, the baselines, verification |
| [`DEV_SPEC.md`](DEV_SPEC.md) | the developer contract: `PreparedScenario`, the semantics every planner must respect, the viewer data format |
| [`PLAN.tr.md`](PLAN.tr.md) | the original project plan (Turkish) |
| [`../tests/fuzz_report.md`](../tests/fuzz_report.md) | what the cross-validation suite checks, its coverage and the bugs it found |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | how to set up, test and extend the project |
| [`demo/index.html`](demo/index.html) | the animated viewer for Karaman → İstanbul with every planner (one offline file; `#tr` opens it in Turkish) |
| [`demo/results.html`](demo/results.html) | the results dashboard of a full experiments run |

On this page: [install](#install) · [command line](#command-line) ·
[Python API](#python-api) · [web viewer](#web-viewer) ·
[live server](#live-planning-server) · [experiments and figures](#experiments-and-figures) ·
[scenarios and data](#scenarios-and-data) · [robustness](#robustness) ·
[file formats](#file-formats) · [FAQ](#faq-and-known-limitations)

## Install

```bash
git clone https://github.com/Aseloth99/evroute.git
cd evroute
pip install -e .            # core planner, no dependencies (Python >= 3.10)
pip install -e ".[plot]"    # + matplotlib, seaborn (static figures)
pip install -e ".[dev]"     # + pytest
```

The optional OpenStreetMap loader needs `osmnx`; the screenshot script of
the viewer needs Node.js with Playwright.

## Command line

`evroute <command> -h` prints the options of every command.

| command | purpose |
|---|---|
| `evroute scenarios` | list the preset scenarios |
| `evroute algorithms` | list the planners and their guarantees (`exact`, `grid`, `heuristic`) |
| `evroute plan [SCENARIO] [-a ALGO] [--json FILE]` | plan one trip and print the stops (default `bls-astar`) |
| `evroute compare [SCENARIO] [--algorithms a,b,...]` | run several planners side by side |
| `evroute viz [SCENARIO] -o FILE.html [--lang en] [--no-animate] [--algorithms ...]` | export the animated viewer as one offline HTML file |
| `evroute serve [SCENARIO] [--port 8765] [--open]` | live planning server with the viewer |
| `evroute robustness [SCENARIO] [-a ALGO] [-n 1000] [--seed 0]` | Monte Carlo reliability of a plan (JSON report) |
| `evroute experiments [NAMES ...] [-o results] [--quick] [--lang en] [--no-panel]` | run the paper experiments; ends with the dashboard `<out>/results.html` |
| `evroute results [DIR] [-o FILE.html] [--lang en]` | rebuild the interactive results dashboard of an experiments folder |
| `evroute figures [SCENARIO] [--outdir figures]` | publication figures for one scenario (map, SoC, time, search) |
| `evroute gallery [-o docs/img] [--quick]` | the seaborn figure gallery of the README |
| `evroute export-scenario [SCENARIO] -o FILE.json` | save a (modified) scenario as JSON |

`SCENARIO` is a preset name (default `turkey-karaman-istanbul`) or, except
for `serve`, the path of a scenario `.json` file.  `plan`, `compare`, `viz`,
`robustness`, `figures` and `export-scenario` accept these overrides:

| option | meaning |
|---|---|
| `--from X`, `--to Y` | origin / destination: node id or place name (`--from Konya`) |
| `--soc 0.35` | initial state of charge |
| `--depart 14:30` | departure time |
| `--temp -5` | ambient temperature [°C] |
| `--vehicle sedan75` | vehicle preset (`city27`, `compact50`, `sedan75`, `suv84_800v`, `togg_t10x_lr`, `van75`) |
| `--objective cost` | minimise generalised cost instead of time |
| `--reserve 0.15`, `--arrive 0.2` | reserve SoC on the way, minimum SoC on arrival |
| `--eco` | allow 100 / 90 / 80 % speeds |
| `--no-queue` | ignore charging queues |
| `--step 0.01` | charging-target grid of BLS / Pareto (SoC fraction) |
| `--safety 1.1` | plan with a consumption safety factor |

Examples:

```bash
evroute plan turkey-karaman-istanbul --from Konya --to Ankara --soc 0.35 -a cfp
evroute compare turkey-antalya-ankara --temp -5 --algorithms greedy,bls-astar,cfp-astar
evroute plan turkey-istanbul-ankara-rush --objective cost --json plan.json
evroute robustness turkey-izmir-erzurum -n 2000 --safety 1.1
```

## Python API

```python
from evroute import plan, available, get_planner, evaluate
from evroute.generators.presets import get_scenario, list_scenarios

sc = get_scenario("turkey-karaman-istanbul")
route = plan(sc, "bls-astar")                       # -> RoutePlan
route = plan(sc, "pareto", criteria=("time", "money"), max_labels=1_000_000)
route = get_planner("bls-astar", potential="basic").plan(sc, trace=True)
```

- **Scenarios** (`evroute.scenario.Scenario`) are meant to be used as
  immutable values (`prepare()` notices in-place changes, but the `with_*`
  helpers are the intended way) and are serialisable: `with_query(origin=..., initial_soc=..., departure_s=...)`,
  `with_conditions(temperature_c=..., wind_speed_ms=..., preconditioning=...)`,
  `with_preferences(objective=..., reserve_soc=..., speed_factors=...,
  charge_step_soc=..., safety_factor=...)`, `with_(vehicle=...)`;
  `save(path)` / `Scenario.load(path)`; `prepare()` builds the
  `PreparedScenario` every planner works on (edge times and energies, exact
  charging functions, waits).
- **Plans** (`evroute.plan.RoutePlan`): `feasible`, `total_time_s`,
  `drive_time_s`, `charge_time_s`, `wait_time_s`, `setup_time_s`, `money`,
  `soh_total_pct`, `generalized_cost`, `arrival_soc`, `stops`
  (`ChargeStop`: station, arrival time, queue, charge time, SoC in/out,
  money), per-node arrays (`node_arrive_s`, `node_energy_arrive`, ...),
  `stats` (`SearchStats`: runtime, settled labels, ...), `alternatives`
  (Pareto members), `summary()`, `to_dict()`.
- **Planner options** (keyword arguments of `plan()` / `get_planner()`):
  BLS / BLS-A\*: `targets="grid+breaks" | "grid"`, `max_labels`,
  `potential="tight" | "basic"` (A\* only); CFP / CFP-A\*: `exact_switching`,
  `max_labels`, `potential`; Pareto: `criteria`, `targets`, `epsilon`,
  `epsilon_scope`, `goal_directed`, `max_labels`; greedy: `charge_to_soc`,
  `max_stops`, `final_leg`; station graph: `charge_to_soc`, `detour_factor`;
  genetic: `seed`, `population`, `generations`, `patience`, `max_stops`,
  `detour_factor`, `seed_greedy`.  The module docstrings of
  `evroute/algorithms/*.py` document every option.
- **Evaluation**: `evaluate(prepared, PlanDecision(edges, levels, charges))`
  replays any decision from first principles and returns a `RoutePlan`
  with violations listed - also for plans you construct yourself.

Runnable examples: [`examples/quickstart.py`](../examples/quickstart.py),
[`examples/custom_network.py`](../examples/custom_network.py),
[`examples/viewer_demo.py`](../examples/viewer_demo.py).

## Web viewer

`evroute viz SCENARIO -o trip.html` (or `examples/viewer_demo.py`) writes one
self-contained HTML file - CSS, JavaScript and data inline, no network
access - that opens in any browser.

<p align="center"><img src="img/viewer-light.png" width="80%" alt="The viewer in trip mode"></p>

- **Map**: hill-shaded terrain, roads by class, charging stations (AC, DC,
  HPC ≥ 250 kW), place names, the route of every algorithm; zoom, pan, fit.
  Hover a station for its 24-hour queue profile; click a route to select it.
- **Animation switch** (key `A`): with animation on, the car drives the
  selected route on a simulated clock - queueing, plugging in and charging
  with the exact charging curve - while the HUD (clock, SoC, speed,
  elevation), the timeline and the charts follow it.  With animation off the
  page is a static report.  `Space` plays / pauses, `←` / `→` scrub (`Shift`
  for larger steps).
- **Race mode** (`R`): every visible algorithm drives at once on the same
  clock, with a live leaderboard.
- **Search replay** (`S`): the order in which a planner settled nodes;
  *Compare* shows BLS and BLS-A\* (or any two planners) side by side.
- **Panels**: route list with guarantees (`HEURISTIC`, `GRID`, `EXACT`,
  `PARTIAL` for an incomplete Pareto front), trip summary (time breakdown,
  energy, money, SoH loss, Monte Carlo P(strand) and P(arrive short),
  settled labels), charging stops, layers, scenario.
- **Charts**: SoC over distance with the elevation profile, time breakdown,
  queue profile, time-vs-money Pareto front (click a point to show that
  route) and a sortable comparison table.
- Turkish / English, light / dark / system theme, keyboard accessible,
  mobile layout.  `F` fits the map, `+` / `-` zoom.

## Live planning server

```bash
evroute serve turkey-karaman-istanbul --open      # http://127.0.0.1:8765/
```

The same viewer in *live mode*: click the map or use the place pickers to set
origin and destination, change vehicle, initial SoC, temperature, reserve,
objective, value of time, eco speeds, queue awareness and departure, tick
algorithms and press *Plan*.  The server uses only the standard library
(`http.server`) and exposes a small JSON API: `GET /api/health`,
`GET /api/scenarios`, `GET /api/scenario?name=...`, `POST /api/plan`
(documented in [`server.py`](../src/evroute/server.py)).  It binds to
`127.0.0.1` by default; it is a research tool, not a hardened web service.

## Experiments and figures

`evroute experiments` (or `python -m evroute.experiments.runner`) runs ten
seeded experiments; `--quick` shrinks every one of them to a smoke test of
about a minute.  Each writes `<out>/<id>/results.csv` (pareto: one CSV per
case), usually `summary.json`, PDF/PNG figures (with matplotlib) and, for
case-studies, optimality-gap and vehicles, a booktabs `table.tex`; the run
adds `<out>/report.json` and the interactive dashboard `<out>/results.html`
(`--no-panel` skips it, `evroute results <out>` rebuilds it).

| id | question | full size |
|---|---|---|
| `case-studies` | all planners on four Turkish trips (also writes a viewer per case) | 4 cases × 8 planners |
| `scalability` | runtime and settled labels versus network size | synthetic countries 1 000-40 000 nodes, 8 trips each |
| `optimality-gap` | how far are the heuristics from the continuous optimum? | 60 random trips ≥ 300 km |
| `grid-resolution` | how does the BLS optimum converge to CFP as the target grid is refined? | 3 cases × 6 steps × 2 target modes |
| `temperature` | trip time, stops and energy from −15 to 35 °C, with and without preconditioning | 11 temperatures |
| `queue-awareness` | what does planning with expected queues save against a queue-blind plan? | 3 cases × 9 departure hours |
| `pareto` | time / money / wear trade-off | 2 cases, exact fronts |
| `robustness` | P(strand) and P(reserve breach) versus the planning safety factor | 6 factors × 2 000 Monte Carlo runs |
| `eco-speed` | when does driving slower shorten the trip? | 80 trips |
| `vehicles` | the flagship trip with every vehicle preset | 6 vehicles |

```bash
evroute experiments --quick -o results/
python -m evroute.viz.results results/ -o results/results.html --lang en
```

The second command turns any results folder into the **interactive results
dashboard**: one offline page with a computed finding per experiment,
exactness checks (e.g. "BLS-A\* equals BLS on every case"), SVG charts with
tooltips, table views and CSV downloads, in Turkish and English.

<p align="center"><img src="img/results-light.png" width="80%" alt="The results dashboard"></p>

Static figures: `evroute gallery -o docs/img` renders the seaborn gallery of
the README (maps, SoC profiles, search spaces, Pareto fronts, optimality-gap
heat map, charging curves, consumption); `evroute figures SCENARIO` the
publication figures of one scenario.  Colours, fonts and the light/dark
palettes live in `evroute/viz/style.py` and are shared with the viewer.

## Scenarios and data

| family | presets | source |
|---|---|---|
| Turkey | `turkey-karaman-istanbul`, `turkey-antalya-ankara`, `turkey-izmir-erzurum`, `turkey-istanbul-antalya-winter`, `turkey-karaman-mersin`, `turkey-istanbul-ankara-rush`, `turkey-ankara-adana-evening` | `generators/turkey.py` + `data/*.csv` |
| corridor | `corridor`, `corridor-winter` | `generators/synthetic.py` |
| synthetic countries | `country-small`, `country-medium`, `country-large` | `generators/synthetic.py` |
| other | `random-geometric`, `grid-small` | `generators/synthetic.py`, `generators/basic.py` |

`get_scenario(name, **overrides)` accepts the same overrides as the builder
functions (e.g. `get_scenario("turkey-karaman-istanbul", temperature_c=-5)`);
`register_scenario` adds your own presets.  `turkey_scenario("Karaman",
"İstanbul")` plans between any two of the 171 places (Turkish letters
optional).

**The Turkey data is approximate and illustrative - not for navigation.**
Coordinates and elevations are gazetteer-level, road lengths are modelled
(typically within ±10 % of official distances), and the charging stations,
prices and queues are synthetic with generic operator names.  The full
provenance statement is the docstring of `generators/turkey.py`.  For real
studies load OpenStreetMap roads (`generators.osm.load_osm`, needs `osmnx`)
and your own charger list (`stations_from_csv`), then
`scenario_from_osm(...)`.

## Robustness

`evroute.robustness.monte_carlo(prepared, plan, n=1000, seed=0, ...)`
replays a fixed plan in `n` random worlds: log-normal trip and edge factors
on the consumption (`trip_cv`, `edge_cv`), exponential (`wait_model`)
queueing delays, and a driver `policy` (`"same-target"` or `"adaptive"`:
charge more after a bad leg).  The `RobustnessReport` has P(strand),
P(reserve breach), P(arrive short) with 95 % confidence intervals and the
distributions of trip time, arrival SoC and charged energy.
`safety_sweep(scenario, factors=...)` plans with several safety factors and
measures each plan.  Model: [`model.md` §8](model.md#8-uncertainty-evaluation-only).

## File formats

- **Scenario JSON** (`Scenario.save` / `evroute export-scenario`): lossless -
  a reloaded scenario prepares bit-identical data.
- **Plan JSON** (`evroute plan --json`, `RoutePlan.to_dict()`): route,
  stops, per-node times and energies, totals, statistics.
- **Viewer payload** `evroute-viewer/1`: specified in
  [`DEV_SPEC.md` §3](DEV_SPEC.md#3-viewer-data-contract-evroute-viewer1).
- **Results payload** `evroute-results/1`: produced by
  `evroute.viz.results.results_payload` from an experiments folder.

## FAQ and known limitations

**Which planner should I use?**  `bls-astar` (default) for the time or the
cost objective; `cfp-astar` for the exact fastest route with continuous
charging amounts; `pareto` for the trade-off between time, money and wear.
The baselines are there to be beaten.

**Why does `evroute compare` report `pareto` as infeasible on Karaman →
İstanbul?**  The exact three-criteria front of a 750 km trip exceeds the
default budget of 2 million labels before the first route is found.  Use
`criteria=("time", "money")`, `targets="grid"`, a coarser
`charge_step_soc` or `epsilon`; the viewer does this automatically and marks
the front as *partial* when the budget is hit.

**Why is BLS a few seconds slower than CFP?**  BLS charges to targets on a
grid (2 % SoC plus the breakpoints of the charging curves by default); CFP
chooses any amount.  The gap shrinks as `charge_step_soc` shrinks
(experiment `grid-resolution`).

**Can CFP minimise cost?**  No - CFP is a time-objective algorithm; with
`objective="cost"` it still returns the fastest route (and the evaluator
reports its cost).  Use BLS-A\* or the Pareto planner for costs.

**How fast is it?**  Pure Python: on the 1 279-node Turkey network BLS-A\*
and CFP-A\* answer intercity queries in tens of milliseconds (about 0.1 s
for the 1 470 km İzmir → Erzurum trip); three speed
levels or the cost objective with time-dependent queues make the label sets
much larger (seconds).  `max_labels` is the safeguard of every exact
planner; when it is hit the planner says so instead of returning a
non-optimal route as optimal.

**Is the Turkey data real?**  Approximate geography, synthetic chargers -
see [Scenarios and data](#scenarios-and-data).
