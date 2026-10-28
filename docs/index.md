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
| [`real-data.md`](real-data.md) ([Türkçe](real-data.tr.md)) | the real-data case study: Turkey on OpenStreetMap roads with SRTM elevations, OSM charging stations and operator list prices -- sources, build, validation against the official KGM distances, charging coverage, prices and the March 2026 regulation, queue assumptions, results, limitations, licences |
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
[scenarios and data](#scenarios-and-data) ·
[Turkey case study](#turkey-case-study) ·
[real data](#real-data-case-study-openstreetmap) ·
[your own network](#your-own-network-vehicle-and-chargers) ·
[charging tariffs](#charging-tariffs) ·
[robustness](#robustness) · [file formats](#file-formats) ·
[FAQ](#faq-and-known-limitations) · [roadmap](#roadmap-and-ideas)

## Install

```bash
git clone https://github.com/Aseloth99/evroute.git
cd evroute
pip install -e .            # core planner, no dependencies (Python >= 3.10)
pip install -e ".[plot]"    # + seaborn (static figures)
pip install -e ".[dev]"     # + pytest
```

The bundled real-data network of Turkey needs nothing more; rebuilding it
from OpenStreetMap needs `osmium`, `numpy`, `scipy`, `shapely` and
`openpyxl` ([`real-data.md`](real-data.md#rebuilding-the-data)).  The
optional OpenStreetMap loader needs `osmnx`; the screenshot script of the
viewer needs Node.js with Playwright.

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
| `--stations hybrid` | charging stations of a `turkey-osm-*` preset: `osm` (the OSM stations), `synthetic` (the case-study stations moved onto the OSM roads) or `hybrid` (OSM plus synthetic DC gap-fillers); [real data](#real-data-case-study-openstreetmap) |
| `--tariff 80`, `--tariff flat` | the illustrative demonstration tariff `DEMO_TARIFF` (×1.3 above 80 % SoC, 16:00-20:00 peak at ×1.15, ×2.3 above 80 % in the peak), or no tariff; applied before the single-value options below |
| `--high-soc 0.8`, `--high-soc-factor 1.3` | price factor for every kWh stored above that SoC |
| `--peak 16-20[,7-9]`, `--peak-factor 1.15` | daily peak hours (`22-6` wraps midnight, `none` clears them) and their price factor |
| `--peak-high-soc-factor 2` | high-SoC factor in the peak, on top of `--peak-factor` (default: `--high-soc-factor`) |
| `--busy 0.7` | a station is also in its peak while its expected charger utilisation is at least this value |

With a tariff, `evroute plan` adds a `tariff` column (price period, `>80%`
when energy was bought in the high-SoC tier) and, when a stop waits for a
cheaper price period, a `hold` column; without one its output is unchanged.
On a network of more than 20 000 nodes (the OpenStreetMap presets),
`--algorithms all` of `compare`, `viz` and `figures` leaves out `bls`,
`cfp` and `pareto`, which settle millions of labels there without the A\*
potential, and says so on stderr; name them to run them anyway.

Examples:

```bash
evroute plan turkey-karaman-istanbul --from Konya --to Ankara --soc 0.35 -a cfp
evroute compare turkey-antalya-ankara --temp -5 --algorithms greedy,bls-astar,cfp-astar
evroute compare turkey-karaman-istanbul --algorithms naive,greedy,station-graph,genetic,bls,bls-astar,cfp,cfp-astar
evroute plan turkey-istanbul-ankara-rush --objective cost --json plan.json
evroute plan turkey-karaman-istanbul-tariff --objective cost     # waits 2 min for the 20:00 price change
evroute plan turkey-karaman-istanbul --depart 14:00 --peak 16-20 --peak-factor 1.2 --high-soc-factor 1.5
evroute robustness turkey-izmir-erzurum -n 2000 --safety 1.1
evroute plan turkey-osm-karaman-istanbul --stations hybrid --from Konya   # real data: OSM roads and stations
evroute compare turkey-osm-karaman-konya                                 # all planners on a short real-data trip
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

Real data: `osm_turkey_scenario("Karaman", "İstanbul", stations="hybrid")`
(`evroute.generators.osm_turkey`) builds a trip on the OpenStreetMap
network of Turkey ([`real-data.md`](real-data.md#try-it)).

Runnable examples: [`examples/quickstart.py`](../examples/quickstart.py),
[`examples/custom_network.py`](../examples/custom_network.py),
[`examples/viewer_demo.py`](../examples/viewer_demo.py).

## Web viewer

`evroute viz SCENARIO -o trip.html` (or `examples/viewer_demo.py`) writes one
self-contained HTML file - CSS, JavaScript and data inline, no network
access - that opens in any browser.  The viewer is hand-written HTML, CSS
and JavaScript with no third-party code.

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

`evroute experiments` (or `python -m evroute.experiments.runner`) runs twelve
seeded experiments that reproduce every table and figure of the accompanying
paper; `--quick` shrinks every one of them to a smoke test (about 1.5
minutes for all of them).  Each writes `<out>/<id>/results.csv` (pareto: one CSV per
case), usually `summary.json`, PDF/PNG figures (with seaborn) and, for
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
| `real-data` | what changes on real OpenStreetMap roads with the chargers mapped in OSM? ([real-data.md](real-data.md#results-the-real-data-experiment)) | 6 trips × 3 data sets (approximate, OSM, hybrid) × 5 planners |

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

Highlights of a full run (all experiments except scalability and real-data:
8 minutes on a 2.1 GHz Xeon core with Python 3.13, 5 of them for `tariffs`;
scalability alone needs 2 hours of planner time, 99 % of it in the station
graph and the Dijkstra-order baselines; real-data took 6.8 hours of planning
before it had a 10-minute budget per heuristic run, 6.4 of them in the
station graph, and should take about 1.5 hours with it):

| experiment | finding |
|---|---|
| `case-studies` | on four Turkish trips BLS-A\* settles 10-29× fewer labels than BLS and CFP-A\* 6-16× fewer than CFP, with identical optima |
| `optimality-gap` | 60 random trips ≥ 300 km, gap to the continuous optimum: greedy 5.2 % on average (max 10.5 %), station graph 12.8 % (max 20.8 %), genetic 0.75 % (max 2.8 %), BLS-A\* on its 2 % grid 0.11 % (max 0.26 %) |
| `grid-resolution` | the BLS gap to CFP shrinks with the grid: Antalya → Ankara 65.5 s at a 20 % grid, 16.7 s at 2 %, 3.3 s at 0.5 % |
| `temperature` | Karaman → İstanbul takes 8 h 07 min at 20 °C, 8 h 37 min at −15 °C with a preconditioned battery and 10 h 24 min without (cold charging) |
| `queue-awareness` | over 27 trips × departure times, planning with the expected queues saves 3.4 min on average and up to 21.7 min (leaving Karaman at 12:00) against a queue-blind plan |
| `pareto` | Antalya → Ankara: 495 Pareto-optimal routes; the cheapest saves 33.07 TRY (−5.6 %) for 34 more minutes than the fastest |
| `robustness` | İzmir → Erzurum, 2 000 Monte Carlo runs: P(strand) 1.25 % and P(reserve breach) 56 % at safety factor 1.0; 0 % and 9.7 % at 1.1 (+21 min), 0 % and 0.4 % at 1.2 (+36 min) |
| `eco-speed` | allowing 90 / 80 % speeds shortens 24 of 80 trips, by up to 2.8 min: driving a leg slower can save more charging time than it costs |
| `vehicles` | Karaman → İstanbul: 7 h 50 min (75 kWh sedan, 2 stops) to 10 h 57 min (27 kWh city car, 5 stops) |
| `tariffs` | Karaman → İstanbul and Antalya → Ankara, departures 06:00-22:00 every 2 h: under the demonstration tariff (80 % tier + 16:00-20:00 peak) the fastest plan pays 2.6 % more than at flat prices on average (up to 10.5 %, Karaman leaving at 12:00); the cost-optimal plan pays 76.73 TRY (7.6 %) less than the fastest for 6.3 min more and waits for the cheaper period in 2 of 18 trips (up to 2.4 min); the 80 % tier alone moves the cost-optimal plans below 80 % (energy charged above it: Karaman 2.34 → 0.47 kWh, Antalya 0.94 → 0 kWh on average); (time, money) fronts of 55-202 plans on a 5 % grid, 14 of 72 stopped by the 1-million-label budget |
| `scalability` | synthetic countries of 1 000-40 000 nodes, 8 trips each: BLS-A\* answers in 0.03 s → 1.7 s and CFP-A\* in 0.03 s → 2.5 s on average; Dijkstra-order BLS and CFP are already 20× and 35× slower at 10 000 nodes and run out of their label budgets on 3 / 4 of 8 trips at 20 000 nodes (CFP: 6 of 8 at 40 000); the station graph needs 8 minutes per trip at 40 000 nodes to build its meta-graph |
| `real-data` | six trips on the approximate network and on OpenStreetMap roads (44 673 nodes) with the OSM stations or the hybrid set: the exact planners solve 5 of 6 trips with OSM stations (İzmir → Erzurum runs into the label limit: no DC charger in the east) and 6 of 6 with the hybrid set, in a median of 4.0 s (BLS-A\*) and 9.9 s (CFP-A\*); with OSM stations the optimum takes 7.0 % longer on average, almost all of it slower driving; the heuristics lose their footing: mean gap greedy 6.4 % → 153.0 % (OSM) → 103.5 % (hybrid), genetic 0.2 % → 9.3 % → 0.9 %, and the station graph needs up to 3 h 46 min per trip ([details](real-data.md#results-the-real-data-experiment)) |

```bash
evroute experiments --quick -o results/            # smoke size: ~1.5 minutes
evroute experiments -o results/ --lang en          # full size, English dashboard
evroute experiments optimality-gap robustness -o results/
evroute results results/ --lang en                 # rebuild the dashboard of a results folder
```

`evroute results` (or `python -m evroute.viz.results results/ -o
results/results.html --lang en`) turns any results folder into the
**interactive results dashboard**: one offline page with a computed finding per experiment,
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
| Turkey, real data | `turkey-osm-karaman-istanbul`, `turkey-osm-karaman-istanbul-dc85`, `turkey-osm-antalya-ankara`, `turkey-osm-istanbul-ankara`, `turkey-osm-ankara-adana`, `turkey-osm-karaman-mersin`, `turkey-osm-izmir-erzurum`, `turkey-osm-karaman-konya` | `generators/osm_turkey.py` + `data/osm_turkey/` (ODbL, © OpenStreetMap contributors) |
| corridor | `corridor`, `corridor-winter` | `generators/synthetic.py` |
| synthetic countries | `country-small`, `country-medium`, `country-large` | `generators/synthetic.py` |
| other | `random-geometric`, `grid-small` | `generators/synthetic.py`, `generators/basic.py` |

`get_scenario(name, **overrides)` accepts the same overrides as the builder
functions (e.g. `get_scenario("turkey-karaman-istanbul", temperature_c=-5)`);
`register_scenario` adds your own presets.  `turkey_scenario("Karaman",
"İstanbul")` plans between any two of the 171 places (Turkish letters
optional).

### Turkey case study

`evroute.generators.turkey` builds an **approximate** Turkish intercity
network from two bundled CSV tables: 171 places (30 metropolitan provinces,
35 further provincial centres, 45 junction and 47 waypoint towns, 10 mountain
passes such as Kop 2 409 m, Alacabel 1 825 m and Sertavul 1 610 m, and 4
bridges) and 146 motorway and state-road links (O-3 ... O-53, D-100, D-300,
D-400, ...) - 1 279 nodes, 2 632 edges, ~14 100 km of road - and a
**synthetic** charging network of 290 stations (199 DC, 14 of them 300 kW
HPC, and 91 AC; 1 174 plugs) with Turkish-lira prices and calibrated
afternoon queues at the busiest service areas.

| preset | trip |
|---|---|
| `turkey-karaman-istanbul` | 750 km: Konya plateau, Afyon/Eskişehir corridors, Marmara motorways (flagship) |
| `turkey-karaman-istanbul-tariff` | the flagship trip leaving at 14:00 under the demonstration tariff (80 % rule, 16:00-20:00 peak) |
| `turkey-antalya-ankara` | 530 km from the Mediterranean over the Taurus |
| `turkey-izmir-erzurum` | 1 470 km west-east crossing, eastern mountains |
| `turkey-istanbul-antalya-winter` | 680 km at −5 °C |
| `turkey-karaman-mersin` | motorway (273 km) vs the Sertavul pass road (254 km) |
| `turkey-istanbul-ankara-rush` | 450 km leaving at 14:30 into the afternoon queues |
| `turkey-ankara-adana-evening` | 510 km leaving at 15:00 along the O-21 |

Further presets: `corridor` / `corridor-winter` (a 600 km partial-charging
instance with a 1 500 m pass), `country-small/-medium/-large` (synthetic
countries with 2 000-40 000 nodes), `random-geometric`, `grid-small`.

> [!IMPORTANT]
> **Data provenance - approximate, illustrative, not for navigation.**
> Coordinates and elevations are gazetteer-level approximations (about 1-5 km
> and 50-150 m); road lengths are modelled and typically within ±10 % of
> official distances; speeds are free-flow limits. **Charging stations,
> powers, prices, queues and the demonstration tariff are synthetic** and use
> generic operator names - they are *not* the network or the prices of any
> company. The vehicle presets use
> approximate public figures ("T10X-like" is not manufacturer data). Use the
> case study to compare algorithms and to study effects, not to plan a real
> trip; plug in measured data (`stations_from_csv`, the OSM loader, the
> [real-data presets](#real-data-case-study-openstreetmap)) for any
> operational conclusion. Details: the module docstring of
> [`generators/turkey.py`](../src/evroute/generators/turkey.py).

### Real-data case study (OpenStreetMap)

`evroute.generators.osm_turkey` loads the bundled **real-data** network of
Turkey: OpenStreetMap motorway, trunk and primary roads with their ramps
(44 673 nodes, 70 886 edges, 75 393 km; OSM speed limits or the legal
defaults), SRTM elevations at every node, the 711 public charging stations
mapped in OSM (618 AC, 93 DC) priced with the operators' list prices, and
1 005 places.  Queues are assumptions from national EPDK averages.  The
`turkey-osm-*` presets (family "Turkey, real data" above) run trips of the
approximate case study on this network; `stations=` (`--stations`) chooses
the chargers:

| `stations` | chargers |
|---|---|
| `osm` (default) | the 711 OSM stations; missing powers and plug counts are defaults (64 % and 44 % of them) |
| `synthetic` | the 290 synthetic stations of the approximate case study, moved onto the OSM roads: same stations, real network |
| `hybrid` | the OSM stations plus synthetic DC gap-fillers (operator `"synthetic"`) wherever a main road is more than 75 km from a DC station: 373 fillers with seed 0 |

OSM holds about 3.3 % of the AC and 1.9 % of the DC sockets that EPDK
counted in September 2026 and has no DC station east of 38° E, so long
eastern trips need the `hybrid` set (`turkey-osm-izmir-erzurum` uses it).
`turkey-osm-karaman-istanbul-dc85` caps charging at 85 %, where operators
may end a DC session under the 2026 regulation.  The data is ODbL 1.0, ©
OpenStreetMap contributors; every map and figure of these scenarios shows
the credit.  Sources, build, validation against the official KGM
distances (2.52 % mean absolute error over 3 240 pairs of province
centres), prices, queues, results and limitations:
[`real-data.md`](real-data.md) ([Türkçe](real-data.tr.md)).

### Your own network, vehicle and chargers

```python
from evroute import (ChargingCurve, Conditions, NetworkBuilder, Preferences, Query,
                     Scenario, Station, Vehicle, plan)

b = NetworkBuilder()
a = b.add_node(0, 0, elevation=50, name="A")                  # x, y in metres
p = b.add_node(60_000, 5_000, elevation=900, name="Pass")
s = b.add_node(120_000, 0, elevation=300, name="Service area")
z = b.add_node(260_000, 0, elevation=20, name="Z")
for u, v in [(a, p), (p, s), (s, z)]:
    b.add_road(u, v, road_class="motorway")                   # two-way by default
net = b.build()

car = Vehicle(key="my-car", name="My car", battery_kwh=40.0, mass_kg=1500,
              drag_coeff=0.28, frontal_area_m2=2.3, rolling_coeff=0.009,
              dc_curve=ChargingCurve.from_points([(0, 60), (0.5, 60), (0.8, 30), (1.0, 8)]))
scenario = Scenario(
    name="custom", network=net, vehicle=car,
    stations=[Station(id=0, node=s, power_kw=120, kind="DC", plugs=4, price_per_kwh=0.5)],
    query=Query(origin=a, destination=z, initial_soc=0.8),
    conditions=Conditions(temperature_c=10),
    preferences=Preferences(reserve_soc=0.1, min_arrival_soc=0.15),
)
for name in ["naive", "bls-astar", "cfp"]:
    print(plan(scenario, name))
```

```text
[naive] infeasible: reserve undercut at node 3 (position 3): -17.443 < 3.840 kWh; arrival energy -17.443 kWh below required 5.760 kWh; battery empty after 162.8 km
[bls-astar] 2 h 43 min | 260.43 km | 1 stop(s) | arrive 16.9% | 12.72 EUR
[cfp] 2 h 41 min | 260.43 km | 1 stop(s) | arrive 15.0% | 12.34 EUR
```

Real data: the OpenStreetMap network of Turkey is bundled
([above](#real-data-case-study-openstreetmap));
`evroute.generators.osm.load_osm("Karaman, Turkey")` downloads any
OpenStreetMap network (needs the optional `osmnx`),
`stations_from_csv(path, network)` snaps your charger list onto it and
`scenario_from_osm(...)` assembles a scenario. `Scenario.save()` /
`Scenario.load()` round-trip a scenario losslessly through JSON.  A variant
with a second, slower road is
[`examples/custom_network.py`](../examples/custom_network.py).

## Charging tariffs

A `Tariff` makes the price of a kWh depend on the state of charge ("the
80 % rule") and on the clock (peak hours, or busy hours derived from each
station's queue model); the default `Tariff()` changes nothing.  The price
period of a session is fixed when the vehicle joins the queue, so a plan may
wait at a station for a cheaper period (a *voluntary hold*, `hold_s` in the
stop).  Model: [`model.md` §5](model.md#5-charging-tariffs); why holding
until a price change is enough and the time objective never holds:
[`algorithms.md` §6](algorithms.md#6-charging-tariffs-price-periods-and-voluntary-holds);
the worked example: [below](#worked-example), and in short in the
[README](../README.md#there-is-no-single-best-route).

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

> [!NOTE]
> **Charging prices in Turkey since March 2026.**  The amendment of the
> Şarj Hizmeti Yönetmeliği (Resmî Gazete 23.03.2026, No. 33202) allows one
> AC and one DC price per operator at public stations, with no tiers by
> charger power or state of charge, so the 80 % tier of `DEMO_TARIFF` is an
> illustration, not the practice at Turkish public stations.  Discounts by
> location and time of day are allowed, at the price of the hour in which
> the session starts: that is the join-time rule above when there is no
> queue to wait in (EVRoute fixes the price when the car joins the queue).
> The real-data presets keep the neutral `Tariff()`
> ([`real-data.md`](real-data.md#prices-and-the-march-2026-regulation)).

### Worked example

The preset `turkey-karaman-istanbul-tariff` is the README trip, Karaman →
İstanbul, leaving at 14:00 under the demonstration tariff `DEMO_TARIFF`
(×1.3 above 80 % SoC, a 16:00-20:00 peak at ×1.15, and ×2.3 above 80 % in
the peak).  The fastest route makes the same two stops as at 08:00, but now
both fall into the peak.  The cost objective (an hour is worth 250 TRY in
this preset, and battery wear is priced too) charges at Ilgın before the
peak begins and waits at İnegöl for the 20:00 price change:

```text
$ evroute plan turkey-karaman-istanbul-tariff
[bls-astar] 8 h 09 min | 747.27 km | 2 stop(s) | arrive 10.4% | 1309.59 TRY
#  station     charger    arrive  queue  charge  SoC         tariff  cost
-  ----------  ---------  ------  -----  ------  ----------  ------  ----------
1  Akşehir DC  DC 120 kW   16:20  5 min  11 min  28% -> 54%    peak  387.24 TRY
2  Kütahya DC  DC 150 kW   18:30  3 min  25 min  10% -> 72%    peak  922.35 TRY
arrival 22:09 | drive 7 h 19 min | charge 36 min | queue+setup 14 min | battery wear 0.0188 %SoH
$ evroute plan turkey-karaman-istanbul-tariff --objective cost
[bls-astar] 8 h 20 min | 747.27 km | 2 stop(s) | arrive 10.5% | 1111.61 TRY
#  station    charger    arrive  hold   queue  charge  SoC         tariff         cost
-  ---------  ---------  ------  -----  -----  ------  ----------  -------------  ----------
1  Ilgın DC   DC 120 kW   15:52      -  7 min  26 min  39% -> 85%  off-peak >80%  593.50 TRY
2  İnegöl DC  DC 120 kW   19:57  2 min  2 min  18 min  10% -> 52%       off-peak  518.11 TRY
arrival 22:20 | drive 7 h 19 min | charge 43 min | queue+setup 15 min | hold 2 min | battery wear 0.0182 %SoH
```

Between these two lie many more answers.  The Pareto search finds every
plan that no other plan beats on both time and money:

```python
from evroute import plan
from evroute.generators.presets import get_scenario

sc = get_scenario("turkey-karaman-istanbul-tariff")
fastest = plan(sc, "bls-astar")                                      # minimum trip time
balanced = plan(sc.with_preferences(objective="cost"), "bls-astar")  # 250 TRY/h + money + wear
front = plan(sc, "pareto", criteria=("time", "money"), max_labels=10_000_000)  # ~1.5 min
cheapest = min(front.alternatives, key=lambda p: p.money)
for p in (fastest, balanced, cheapest):
    print(p)
print(len(front.alternatives), "Pareto-optimal routes; complete:", front.stats.extra["complete"])
```

```text
[bls-astar] 8 h 09 min | 747.27 km | 2 stop(s) | arrive 10.4% | 1309.59 TRY
[bls-astar] 8 h 20 min | 747.27 km | 2 stop(s) | arrive 10.5% | 1111.61 TRY
[pareto] 14 h 44 min | 768.22 km | 3 stop(s) | arrive 10.1% | 796.58 TRY
268 Pareto-optimal routes; complete: True
```

The README tabulates the three plans and shows the front
([`img/fig_tradeoff_tariff.png`](img/fig_tradeoff_tariff.png)).  What the
numbers say:

- **No plan wins on every count.**  The fastest plan is the dearest, the
  cheapest takes 6 h 35 min longer, and it also wears the battery least
  (slow AC charging is gentle).  None of the 268 plans of the front is
  beaten by another on both time and money.
- **The first minutes buy the most.**  The front is steep at first and then
  flat: 11 minutes more save 197.98 TRY (−15.1 %), the next 6 h 24 min only
  315.04 TRY more.  Which point is right depends on what an hour is worth to
  you - the value of time of the cost objective (`value_of_time_per_h`), or
  your own pick from the front in the viewer.
- **Waiting is a decision, too.**  28 of the 268 plans wait at a station for
  a cheaper price.  Waiting never shortens a trip (queues are first come,
  first served), so the time objective - BLS-A\*, CFP - never waits; the cost
  objective and the Pareto search consider waiting until every price change
  of a station, and that is provably enough
  ([`algorithms.md` §6](algorithms.md#6-charging-tariffs-price-periods-and-voluntary-holds)).
- **Over a whole day** of departures on two trips (experiment `tariffs`),
  the tariff makes the fastest plan 2.6 % dearer than flat prices on average
  and up to 10.5 %; the cost-optimal plan pays 76.73 TRY (7.6 %) less than
  the fastest for 6.3 minutes more.

To explore the front in the viewer, `evroute viz
turkey-karaman-istanbul-tariff --step 0.05 -o tariff.html` plans on a 5 %
charging grid, where the complete front (180 plans) fits the viewer's label
budget (as in [`demo/tariff.html`](demo/tariff.html)).

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
much larger (seconds).  On the 44 673-node OpenStreetMap network of Turkey
the median over six trips is 4.0 s for BLS-A\* and 9.9 s for CFP-A\*
([`real-data.md`](real-data.md#results-the-real-data-experiment)).
`max_labels` is the safeguard of every exact planner; when it is hit the
planner says so instead of returning a non-optimal route as optimal.

**Is the Turkey data real?**  The Turkey family of presets: approximate
geography, synthetic chargers - see [Scenarios and data](#scenarios-and-data).
The `turkey-osm-*` presets: real OpenStreetMap roads, SRTM elevations, the
chargers mapped in OSM and the operators' list prices, with assumed queues
and incomplete charger coverage - see [`real-data.md`](real-data.md).

## Roadmap and ideas

- **Learned energy and battery models.** The `EnergyModel` protocol
  (`traverse(net, edge, speed_factor) -> Traversal`) already separates the
  planner from the physics; the next step is to let `Scenario.prepare()`
  accept any implementation - e.g. an **LSTM-based SoC/SoH estimator**
  trained on driving data (the author's other research topic) - and to
  recalibrate the `AgingModel` that prices battery wear.
- Contraction hierarchies or ALT landmarks for continental networks.
- Chance-constrained planning: optimise P(arrive with reserve) directly
  instead of through a safety factor.
- Live data: real-time charger availability and prices, traffic speeds.
- Fleets and multi-day trips: overnight AC charging, driver breaks.
- A browser-only build (Pyodide) of the live planner for GitHub Pages.

Contributions are welcome - see [`CONTRIBUTING.md`](../CONTRIBUTING.md).
