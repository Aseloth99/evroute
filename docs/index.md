# EVRoute documentation

EVRoute plans electric-vehicle trips with charging stops: exact algorithms
for a model that includes nonlinear charging, partial charging, queues,
terrain and regeneration, temperature, battery wear and uncertainty.  This
page is the map of the documentation; the [README](../README.md)
([Türkçe](../README.tr.md)) is the tour.

| document | what is in it |
|---|---|
| [`guide.md`](guide.md) ([Türkçe](guide.tr.md)) | **start here**: a gentle guide to the algorithms -- why plain A\* is not enough, labels and dominance, BLS, A\*, CFP, Pareto, the baselines, tariffs and "which planner should I use?" |
| [`model.md`](model.md) | the optimisation problem, equation by equation, with the module that implements each part |
| [`algorithms.md`](algorithms.md) | the algorithms and their correctness arguments: BLS, the consistent A\* potentials, CFP, the Pareto search, charging tariffs and voluntary holds, the baselines, verification |
| [`DEV_SPEC.md`](DEV_SPEC.md) | the developer contract: `PreparedScenario`, the semantics every planner must respect, the viewer data format |
| [`PLAN.tr.md`](PLAN.tr.md) | the original project plan (Turkish) |
| [`../tests/fuzz_report.md`](../tests/fuzz_report.md) | what the cross-validation suite checks, its coverage and the bugs it found |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | how to set up, test and extend the project |
| [`demo/index.html`](demo/index.html) | the animated viewer for Karaman → İstanbul with every planner (one offline file; `#tr` opens it in Turkish) |
| [`demo/tariff.html`](demo/tariff.html) | the same trip leaving at 14:00 under the demonstration tariff (80 % rule + peak 16:00-20:00), cost objective: the cost-optimal plan waits 2.4 min at İnegöl for the off-peak price (built with `--step 0.05` so the Pareto front fits the viewer's label budget) |
| [`demo/results.html`](demo/results.html) | the results dashboard of a full experiments run, including the `tariffs` experiment |

On this page: [install](#install) · [command line](#command-line) ·
[Python API](#python-api) · [web viewer](#web-viewer) ·
[live server](#live-planning-server) · [experiments and figures](#experiments-and-figures) ·
[scenarios and data](#scenarios-and-data) · [charging tariffs](#charging-tariffs) ·
[robustness](#robustness) · [file formats](#file-formats) ·
[FAQ](#faq-and-known-limitations)

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
| `--tariff 80`, `--tariff flat` | the illustrative demonstration tariff `DEMO_TARIFF` (×1.3 above 80 % SoC, 16:00-20:00 peak at ×1.15, ×2.3 above 80 % in the peak), or no tariff; applied before the single-value options below |
| `--high-soc 0.8`, `--high-soc-factor 1.3` | price factor for every kWh stored above that SoC |
| `--peak 16-20[,7-9]`, `--peak-factor 1.15` | daily peak hours (`22-6` wraps midnight, `none` clears them) and their price factor |
| `--peak-high-soc-factor 2` | high-SoC factor in the peak, on top of `--peak-factor` (default: `--high-soc-factor`) |
| `--busy 0.7` | a station is also in its peak while its expected charger utilisation is at least this value |

With a tariff, `evroute plan` adds a `tariff` column (price period, `>80%`
when energy was bought in the high-SoC tier) and, when a stop waits for a
cheaper price period, a `hold` column; without one its output is unchanged.

Examples:

```bash
evroute plan turkey-karaman-istanbul --from Konya --to Ankara --soc 0.35 -a cfp
evroute compare turkey-antalya-ankara --temp -5 --algorithms greedy,bls-astar,cfp-astar
evroute plan turkey-istanbul-ankara-rush --objective cost --json plan.json
evroute plan turkey-karaman-istanbul-tariff --objective cost     # waits 2 min for the 20:00 price change
evroute plan turkey-karaman-istanbul --depart 14:00 --peak 16-20 --peak-factor 1.2 --high-soc-factor 1.5
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
  charge_step_soc=..., safety_factor=...)`, `with_tariff(high_soc=...,
  high_soc_factor=..., peak_hours=..., peak_factor=..., busy_utilisation=...)`,
  `with_(vehicle=..., tariff=Tariff(...))`;
  `save(path)` / `Scenario.load(path)`; `prepare()` builds the
  `PreparedScenario` every planner works on (edge times and energies, exact
  charging functions, waits).
- **Plans** (`evroute.plan.RoutePlan`): `feasible`, `total_time_s`,
  `drive_time_s`, `charge_time_s`, `wait_time_s`, `setup_time_s`,
  `hold_time_s` (voluntary holds), `money`, `soh_total_pct`,
  `generalized_cost`, `arrival_soc`, `stops` (`ChargeStop`: station, arrival
  time, `hold_s` and `hold_until_s` of a voluntary hold, queue, charge time,
  SoC in/out, money, `peak` = priced in the peak period), per-node arrays
  (`node_arrive_s`, `node_energy_arrive`, ...),
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
  with violations listed - also for plans you construct yourself
  (`ChargeDecision(position, station, target_kwh, hold_until_s=None)`; a
  hold until a trip time waits there before joining the queue).

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
algorithms and press *Plan*; a switch turns the demonstration tariff on and
off.  The server uses only the standard library
(`http.server`) and exposes a small JSON API: `GET /api/health`,
`GET /api/scenarios`, `GET /api/scenario?name=...`, `POST /api/plan`
(documented in [`server.py`](../src/evroute/server.py)).  It binds to
`127.0.0.1` by default; it is a research tool, not a hardened web service.

## Experiments and figures

`evroute experiments` (or `python -m evroute.experiments.runner`) runs eleven
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
| `tariffs` | what do the 80 % rule and peak hours cost, and when does waiting for a cheaper price pay? | 2 trips × 9 departures (06:00-22:00) × 4 tariffs |

The `tariffs` experiment compares four tariffs built from the illustrative
`DEMO_TARIFF` (flat prices, its 80 % tier alone, its 16:00-20:00 peak alone,
both) on Karaman → İstanbul and Antalya → Ankara.  For every departure and
tariff it plans the fastest route (BLS-A\*), the cost-optimal route (BLS-A\*,
`objective="cost"`) and the (time, money) Pareto front (budget of the
viewer, 1 million labels; all searches on a 5 % charging grid so that the
fronts fit it).  It writes `tariffs/results.csv` (one row per plan, `kind` =
`time`, `cost` or `pareto`, with the highest SoC, the energy charged above
80 %, holds, peak sessions, front size and whether the front is complete),
`tariffs/fronts.csv` (every front member), `summary.json`, `table.tex`,
`fig_money_<trip>.png` (the money of the fastest plan by departure time) and
`<trip>/viewer.html` (the departure where the cost-optimal plan saves most,
with its front).  `--quick` keeps 14:00 and 18:00, flat and tier + peak,
and a budget of 200 000 labels.

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
the README (maps, SoC profiles, search spaces, Pareto fronts, the time-money
trade-off under the demonstration tariff, optimality-gap heat map, charging
curves, consumption; the tariff front alone takes about 1.5 minutes); `evroute figures SCENARIO` the
publication figures of one scenario.  Colours, fonts and the light/dark
palettes live in `evroute/viz/style.py` and are shared with the viewer.

## Scenarios and data

| family | presets | source |
|---|---|---|
| Turkey | `turkey-karaman-istanbul`, `turkey-karaman-istanbul-tariff`, `turkey-antalya-ankara`, `turkey-izmir-erzurum`, `turkey-istanbul-antalya-winter`, `turkey-karaman-mersin`, `turkey-istanbul-ankara-rush`, `turkey-ankara-adana-evening` | `generators/turkey.py` + `data/*.csv` |
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

## Charging tariffs

A `Tariff` makes the price of a kWh depend on the state of charge ("the
80 % rule") and on the clock (peak hours, or busy hours derived from each
station's queue model); the default `Tariff()` changes nothing.  The price
period of a session is fixed when the vehicle joins the queue, so a plan may
wait at a station for a cheaper period (a *voluntary hold*, `hold_s` in the
stop).  Model: [`model.md` §5](model.md#5-charging-tariffs); why holding
until a price change is enough and the time objective never holds:
[`algorithms.md` §6](algorithms.md#6-charging-tariffs-price-periods-and-voluntary-holds);
the worked example: [README, "There is no single best route"](../README.md#there-is-no-single-best-route).

```python
from evroute import plan
from evroute.generators.presets import DEMO_TARIFF, get_scenario

sc = get_scenario("turkey-karaman-istanbul", departure="14:00").with_(tariff=DEMO_TARIFF)
sc = sc.with_tariff(busy_utilisation=0.5)   # also peak while a charger is at least 50 % busy
route = plan(sc.with_preferences(objective="cost"), "bls-astar")
print(route)
for s in route.stops:
    print(f"  {s.station_name:24} {'peak' if s.peak else 'off-peak':8}  hold {s.hold_s:3.0f} s")
```

```text
[bls-astar] 8 h 18 min | 747.27 km | 2 stop(s) | arrive 10.5% | 1187.70 TRY
  D-300 service area Çay   peak      hold   0 s
  İnegöl DC                off-peak  hold  26 s
```

The time objective (BLS-A\* by default, CFP) ignores prices: its route stays
the fastest one and only its money changes.  The cost objective of BLS /
BLS-A\* and the Pareto search weigh the prices, and consider waiting.  The
values of `DEMO_TARIFF` are illustrative and synthetic, like the station
prices of the Turkey case study.

## Robustness

`evroute.robustness.monte_carlo(prepared, plan, n=1000, seed=0, ...)`
replays a fixed plan in `n` random worlds: log-normal trip and edge factors
on the consumption (`trip_cv`, `edge_cv`), exponential (`wait_model`)
queueing delays, and a driver `policy` (`"same-target"` or `"adaptive"`:
charge more after a bad leg).  The `RobustnessReport` has P(strand),
P(reserve breach), P(arrive short) with 95 % confidence intervals and the
distributions of trip time, arrival SoC and charged energy.
`safety_sweep(scenario, factors=...)` plans with several safety factors and
measures each plan.  Model: [`model.md` §9](model.md#9-uncertainty-evaluation-only).

## File formats

- **Scenario JSON** (`Scenario.save` / `evroute export-scenario`): lossless -
  a reloaded scenario prepares bit-identical data.  The `"tariff"` key
  appears only for a tariff other than `Tariff()`.
- **Plan JSON** (`evroute plan --json`, `RoutePlan.to_dict()`): route,
  stops (with `hold_s`, `hold_until_s` and `peak`), per-node times and
  energies, totals (with `hold_time_s`), statistics.
- **Viewer payload** `evroute-viewer/1`: specified in
  [`DEV_SPEC.md` §3](DEV_SPEC.md#3-viewer-data-contract-evroute-viewer1).
- **Results payload** `evroute-results/1`: produced by
  `evroute.viz.results.results_payload` from an experiments folder.

## FAQ and known limitations

**Which planner should I use?**  `bls-astar` (default) for the time or the
cost objective, with or without a tariff; `cfp-astar` for the exact fastest
route with continuous charging amounts; `pareto` for the trade-off between
time, money and wear.  The baselines are there to be beaten.  The
[guide](guide.md#12-which-planner-should-i-use) has a table.

**Why does the cost-optimal plan wait at a station?**  Under a tariff with
peak hours the price of a session is fixed when the car joins the queue; a
few minutes of waiting for the end of the peak can be worth more than they
cost at the value of time.  The time objective never waits: with FIFO queues
a hold cannot make a stop end earlier.

**Why does the Pareto front take so long with a tariff?**  Every station
expansion is also tried after a hold until each price change, which
multiplies the labels: the complete (time, money) front of Karaman →
İstanbul under the demonstration tariff needs a budget of 10 million labels
(about 1.5 minutes) on the default 2 % grid; a 5 % grid
(`charge_step_soc=0.05`) brings it under 10 s.

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
