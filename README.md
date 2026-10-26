<div align="center">

# EVRoute

**Exact, battery-, queue- and degradation-aware route planning for electric vehicles with charging stations.**

<img src="docs/img/demo.gif" width="880" alt="The EVRoute viewer: the optimal plan drives Karaman → İstanbul with two charging stops, then every algorithm races on the same clock">

*Where to charge, how much, at what speed and when to leave - computed with provably optimal algorithms, checked by an independent evaluator, and animated in your browser.*

Pure Python ≥ 3.10 · zero runtime dependencies · MIT licence<br>
**English** · [Türkçe](README.tr.md) · [Documentation](docs/index.md) · [Model](docs/model.md) · [Algorithms & proofs](docs/algorithms.md)

</div>

---

## Why EVRoute?

Electric-vehicle route planning is "a solved problem" - until you list what a
real trip involves. Most studies and tools handle one or two of these effects
and simplify the rest away:

| Effect | In reality | Common simplification | EVRoute |
|---|---|---|---|
| Nonlinear charging | power drops sharply above ~50-80 % SoC | constant power | exact piecewise-linear charging functions per vehicle × charger |
| Partial charging | stop at 54 %, not at 100 % | always charge to full / fixed target | continuous amounts (CFP) or a fine target grid (BLS) |
| Queues | 20-40 min at busy service areas in the afternoon | no waiting | Erlang-C M/M/c queues with hourly demand, time-dependent and FIFO-safe |
| Terrain and regeneration | descents refill the battery | flat roads | physics model with grade, wind, stop-and-go, regeneration limit, full-battery clamp |
| Temperature | −5 °C: smaller battery, heating, slower charging | 20 °C | usable capacity, HVAC load, cold-charging derating, preconditioning |
| Battery wear | fast charging at high SoC ages the pack | ignored | SoH loss from C-rate, SoC and temperature, priced in the objective |
| Uncertainty | consumption deviates ±10 % | deterministic | Monte Carlo reliability, safety factor, adaptive driver policy |
| Eco-driving | driving slower can save a stop | one speed | several speed levels per road |

EVRoute puts **all of them into one exact model**, solves it with algorithms
that come with optimality proofs, verifies every answer with an independent
replay, and makes the result tangible in an animated, offline web viewer. It
is meant as a **reference implementation**: a baseline that other methods
(heuristics, learned models, commercial planners) can be measured against.

## Highlights

- **9 planners behind one API** - from a battery-blind fastest path and an
  "in-car navigator" heuristic to exact label-setting (BLS, BLS-A\*), exact
  continuous charging (CFP, CFP-A\*) and a multi-criteria Pareto search over
  time, money and battery wear.
- **Exact means exact** - continuous energies (consumption is never rounded),
  exact charging curves, consecutive stops at different chargers of one site,
  optimality proofs in [`docs/algorithms.md`](docs/algorithms.md).
- **Verified, not just tested** - every plan of every planner is replayed by an
  independent evaluator; a seeded cross-validation suite checks 11 invariants
  against brute force, metamorphic relations and local search
  ([`tests/fuzz_report.md`](tests/fuzz_report.md)).
- **A real application** - an animated single-file web viewer (trip, race and
  search-replay modes, TR/EN, light/dark, mobile), a live planning server
  (click the map, press *Plan*), an interactive experiment dashboard and
  publication-quality seaborn figures.
- **Turkey case study** - an approximate intercity network of 1 279 nodes, 146
  roads, 10 mountain passes and 290 synthetic charging stations; plus
  synthetic generators (up to 40 000 nodes) and an OpenStreetMap loader.
- **Reproducible experiments** - ten experiments behind every table and
  figure, one command, seeded, CSV + JSON + LaTeX + figures.
- **Zero dependencies** - the core is standard-library Python;
  `matplotlib`/`seaborn` are optional and only used for static figures.

## Screenshots

<table>
<tr>
<td width="50%"><img src="docs/img/viewer-light.png" alt="Viewer: route list, terrain map and trip summary"><br><sub><b>Trip mode.</b> Every algorithm's route on a hill-shaded map; the selected plan with its stops, queue, charging and costs.</sub></td>
<td width="50%"><img src="docs/img/viewer-search.png" alt="Search replay: BLS-A* explores 73 nodes, BLS 493"><br><sub><b>Search replay.</b> The same optimum, two searches: BLS-A* (top) settles a narrow corridor, Dijkstra-order BLS (bottom) floods the country.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/img/viewer-charts.png" alt="Charts: SoC and elevation, queue profile, time breakdown, Pareto front, comparison table"><br><sub><b>Charts.</b> SoC over distance with elevation, 24 h queue profile, time breakdown, time-vs-money Pareto front and a sortable comparison table.</sub></td>
<td width="50%"><img src="docs/img/results-light.png" alt="Results dashboard: key findings of every experiment"><br><sub><b>Results dashboard.</b> All experiments in one offline page with computed findings, exactness checks, tables and CSV downloads.</sub></td>
</tr>
</table>

<details>
<summary><b>More: dark theme, charging animation, mobile, seaborn figures</b></summary>

<p align="center">
<img src="docs/img/viewer-dark.png" width="49%" alt="Race mode in the dark theme">
<img src="docs/img/viewer-charging.png" width="49%" alt="Charging animation at Kütahya DC">
</p>
<p align="center">
<img src="docs/img/fig_map_turkey.png" width="49%" alt="All routes on the Turkey map">
<img src="docs/img/fig_soc_turkey.png" width="49%" alt="State of charge along the route for every algorithm">
</p>
<p align="center">
<img src="docs/img/fig_search_space_turkey.png" width="98%" alt="Settled nodes of BLS, BLS-A*, CFP and CFP-A*">
</p>
<p align="center">
<img src="docs/img/fig_charging_curves.png" width="98%" alt="Charging curves of the vehicle presets">
</p>
<p align="center">
<img src="docs/img/fig_pareto_turkey.png" width="42%" alt="Pareto front time vs charging cost">
<img src="docs/img/fig_gap_heatmap_turkey.png" width="56%" alt="Optimality gap of every algorithm on 16 Turkish trips">
</p>

All static figures are regenerated with `evroute gallery -o docs/img`; the
screenshots and the GIF with `python examples/viewer_demo.py --screenshots docs/img`
(needs Node.js + Playwright).
</details>

## Quick start

```bash
git clone https://github.com/Aseloth99/evroute.git
cd evroute
pip install -e .            # the planner: no dependencies at all
pip install -e ".[plot]"    # optional: matplotlib + seaborn for static figures
pip install -e ".[dev]"     # optional: + pytest
```

### Command line

```bash
evroute scenarios                                    # list the preset scenarios
evroute algorithms                                   # list the planners and their guarantees
evroute plan turkey-karaman-istanbul                 # plan with BLS-A* (default)
evroute plan turkey-karaman-istanbul --from Konya --to Ankara --soc 0.35 -a cfp
evroute compare turkey-karaman-istanbul --algorithms naive,greedy,station-graph,genetic,bls,bls-astar,cfp,cfp-astar
evroute viz turkey-karaman-istanbul -o karaman-istanbul.html --lang en   # animated viewer, works offline
evroute serve turkey-karaman-istanbul --open          # live planning in the browser
evroute robustness turkey-izmir-erzurum -n 2000 --safety 1.1             # Monte Carlo reliability
evroute experiments --quick -o results/               # all ten experiments, smoke size (~1 min)
```

```text
$ evroute plan turkey-karaman-istanbul
[bls-astar] 8 h 07 min | 747.27 km | 2 stop(s) | arrive 10.4% | 1138.78 TRY
#  station     charger    arrive  queue  charge  SoC         cost
-  ----------  ---------  ------  -----  ------  ----------  ----------
1  Akşehir DC  DC 120 kW   10:20  3 min  11 min  28% -> 54%  336.73 TRY
2  Kütahya DC  DC 150 kW   12:28  3 min  25 min  10% -> 72%  802.05 TRY
arrival 16:06 | drive 7 h 19 min | charge 36 min | queue+setup 12 min | battery wear 0.0188 %SoH
```

Every command takes a preset name or a scenario `.json` file (`evroute
export-scenario ... -o my.json`) and the overrides `--from --to --soc --depart
--temp --vehicle --objective --reserve --arrive --eco --no-queue --step
--safety`. `evroute <command> -h` lists them.

### Python

```python
from evroute import plan
from evroute.generators.presets import get_scenario
from evroute.robustness import monte_carlo

scenario = get_scenario("turkey-karaman-istanbul")
route = plan(scenario, "bls-astar")  # exact on the charging-target grid
print(route)
for s in route.stops:
    print(f"  {s.station_name:12} {s.soc_in:4.0%} -> {s.soc_out:4.0%}"
          f"  queue {s.wait_s / 60:2.0f} min  charge {s.charge_s / 60:2.0f} min")

# What if?  Every scenario is immutable; with_* returns a modified copy.
print(plan(scenario.with_conditions(temperature_c=-5), "cfp-astar"))   # winter, continuous charging
print(plan(scenario.with_preferences(objective="cost"), "bls-astar"))  # time + money + battery wear

# The time-money trade-off (a label budget keeps it interactive)
front = plan(scenario, "pareto", criteria=("time", "money"), max_labels=1_000_000)
print(len(front.alternatives), "Pareto-optimal routes; complete:", front.stats.extra["complete"])

# How often does the plan fail when consumption and queues are random?
mc = monte_carlo(scenario.prepare(), route, n=2000, seed=0)
print(f"P(strand) = {mc.p_strand:.2%}   P(reserve breach) = {mc.p_reserve_breach:.1%}")
```

```text
[bls-astar] 8 h 07 min | 747.27 km | 2 stop(s) | arrive 10.4% | 1138.78 TRY
  Akşehir DC    28% ->  54%  queue  3 min  charge 11 min
  Kütahya DC    10% ->  72%  queue  3 min  charge 25 min
[cfp-astar] 8 h 23 min | 747.27 km | 3 stop(s) | arrive 10.0% | 1669.75 TRY
[bls-astar] 8 h 15 min | 747.27 km | 2 stop(s) | arrive 10.5% | 1092.91 TRY
32 Pareto-optimal routes; complete: False
P(strand) = 0.40%   P(reserve breach) = 51.9%
```

The last line is the point of the robustness module: a plan that is optimal
for the *expected* consumption arrives with exactly the 10 % reserve, so half
of all random worlds dip below it. Plan with `safety_factor` > 1 (or
`--safety` on the command line) to buy reliability with minutes - see
[the robustness experiment](#experiments).

<details>
<summary><b>Bring your own road network, vehicle and chargers</b></summary>

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

Real data: `evroute.generators.osm.load_osm("Karaman, Turkey")` downloads an
OpenStreetMap network (needs the optional `osmnx`),
`stations_from_csv(path, network)` snaps your charger list onto it and
`scenario_from_osm(...)` assembles a scenario. `Scenario.save()` /
`Scenario.load()` round-trip a scenario losslessly through JSON.
</details>

## Example: Karaman → İstanbul

747 km across the Konya plateau and the Afyon-Kütahya-Bursa corridor, a
Turkish C-SUV (85 kWh battery) leaving at 08:00 with 80 %, 10 % reserve on
the way and on arrival, expected queues at every charger. The columns are
those of `evroute compare`; runtimes are the median of five runs of the
planner alone on a 2.1 GHz Xeon core (Python 3.13), and the gap is measured
against the continuous optimum of CFP:

| algorithm | trip time | gap | stops | charging cost [TRY] | arrival SoC | labels settled | runtime |
|---|---:|---:|---:|---:|---:|---:|---:|
| `naive` | stranded at 364 km | | 0 | | −77.2 % | 887 | 1 ms |
| `greedy` | 8:30:51 | +4.97 % | 2 | 1,659.68 | 47.1 % | 2,076 | 9 ms |
| `station-graph` | 9:19:35 | +14.98 % | 2 | 1,180.77 | 17.0 % | 14 | 61 ms |
| `genetic` | 8:08:28 | +0.37 % | 2 | 1,175.32 | 10.0 % | 754 | 244 ms |
| `bls` | 8:06:56 | +0.05 % | 2 | 1,138.78 | 10.4 % | 42,980 | 258 ms |
| **`bls-astar`** | **8:06:56** | +0.05 % | 2 | 1,138.78 | 10.4 % | **1,491** | **38 ms** |
| `cfp` | 8:06:41 | 0 | 2 | 1,133.96 | 10.0 % | 5,665 | 383 ms |
| **`cfp-astar`** | **8:06:41** | 0 | 2 | 1,133.96 | 10.0 % | **347** | **27 ms** |

What the table shows:

- **The battery-blind route strands** after 364 km. The greedy "charge to
  80 % when needed" heuristic is feasible but 24 minutes slower and 46 %
  more expensive - it fills the battery on the slow part of the charging
  curve and arrives with 47 % it did not need.
- **Partial charging is the whole game.** The optimum stops briefly at
  Akşehir (28 → 54 %) and longer at Kütahya (10 → 72 %), arriving with
  exactly the required 10 %. BLS, restricted to a 2 % SoC grid, is 15.6 s
  behind the continuous optimum of CFP.
- **A\* pays off.** BLS-A\* settles 29× fewer labels than BLS (CFP-A\*: 16×
  fewer than CFP) and returns the identical optimum - the search-replay
  screenshot above shows the difference.
- The three-criteria Pareto search needs more than its default 2 million
  labels on this 750 km instance (`evroute compare` then reports `pareto` as
  infeasible); with `criteria=("time", "money")` it returns 32 non-dominated
  routes within a 1-million-label budget, marked as possibly incomplete.

## Algorithms

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
regeneration and consecutive stops at different chargers of one site.

<details>
<summary><b>How BLS-A* works</b></summary>

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
same path and counts unavoidable stops. Proofs: [`docs/algorithms.md` §2-3](docs/algorithms.md#2-bls--battery-constrained-label-setting).
</details>

<details>
<summary><b>How CFP works (continuous charging)</b></summary>

BLS fixes *how much* to charge to a grid. CFP defers that decision: a label
stores the last station `j`, the time and energy at which charging there
began and the consumption profile `(in, cost, out)` of the road since then.
Its *function* `B(t)` - the energy available at the current node if the car
leaves `j` so that it arrives at time `t` - is piecewise linear and
non-decreasing. Along an edge only the profile changes (closed form); at a
new station the label spawns stop labels at the breakpoints of `B`, which is
where an optimal switch from charging at `j` to charging at the new station
lies (Lemma 3). Dominance compares a label against the upper envelope of the
settled labels of its node. The first destination entry popped is the
fastest route over **all** charging amounts. Proofs and the extensions
(non-concave curves, time-dependent queues, consecutive stops):
[`docs/algorithms.md` §4](docs/algorithms.md#4-cfp--charging-function-propagation-continuous-charging).
</details>

## The model in one screen

- **Road network** - directed graph with coordinates, elevation, length,
  speed limit and road class (stop-and-go intensity).
- **Driving** - longitudinal dynamics: rolling resistance, aerodynamic drag
  with head-/tailwind and air density, grade, stop-and-go losses, auxiliary
  and temperature-dependent HVAC power, regeneration with efficiency and
  power limit; regeneration into a full battery is lost. Optional speed
  levels (e.g. 100/90/80 %).
- **Battery** - usable capacity shrinks in the cold; reserve SoC at every
  node, minimum arrival SoC, maximum charging SoC.
- **Charging** - vehicle charging curves (piecewise-constant power over SoC
  bins) capped by the charger power, DC power derated for a cold battery
  without preconditioning → exact, piecewise-linear charging functions
  (concave for the usual curves); prices per kWh with charging losses;
  set-up time per stop.
- **Queues** - each station is an M/M/c queue with an hourly demand profile;
  the Erlang-C expected wait is made FIFO-safe so that time-dependent
  planning stays exact.
- **Battery wear** - SoH loss per kWh as a function of C-rate, SoC and
  temperature, priced per SoH percentage point.
- **Objectives** - trip time, generalised cost
  `VoT·T + w_money·money + w_wear·wear`, or the Pareto front of the three.
- **Uncertainty** (evaluation) - log-normal trip and edge factors on the
  consumption, exponential queueing delays.

Every equation, with the module that implements it: [`docs/model.md`](docs/model.md).

## Turkey case study

`evroute.generators.turkey` builds an **approximate** Turkish intercity
network from two bundled CSV tables: 171 places (30 metropolitan provinces,
35 further provincial centres, 45 junction and 47 waypoint towns, 10 mountain
passes (Kop 2 409 m, Alacabel 1 825 m, Sertavul 1 610 m, ...), 146 motorway
and state-road links (O-3 ... O-53, D-100, D-300, D-400, ...) - 1 279 nodes,
2 632 edges, ~14 100 km of road - and a **synthetic** charging network of
290 stations (199 DC, 14 HPC, 91 AC; 1 174 plugs) with Turkish-lira prices
and calibrated afternoon queues at the busiest service areas.

| preset | trip |
|---|---|
| `turkey-karaman-istanbul` | 750 km: Konya plateau, Afyon/Eskişehir corridors, Marmara motorways (flagship) |
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
> powers, prices and queues are synthetic** and use generic operator names -
> they are *not* the network of any company. The vehicle presets use
> approximate public figures ("T10X-like" is not manufacturer data). Use the
> case study to compare algorithms and to study effects, not to plan a real
> trip; plug in measured data (`stations_from_csv`, the OSM loader) for any
> operational conclusion. Details: the module docstring of
> [`generators/turkey.py`](src/evroute/generators/turkey.py).

## Experiments

Ten seeded experiments reproduce every table and figure of the accompanying
paper. Each writes `results.csv`, `summary.json`, a booktabs `table.tex` and
PDF/PNG figures into `<out>/<experiment>/`; the run ends with
`<out>/results.html`, an interactive offline dashboard of everything it
measured (computed findings, exactness checks, charts, tables, CSV downloads).

```bash
evroute experiments --quick -o results/            # smoke size: ~1 minute
evroute experiments -o results/ --lang en          # full size, English dashboard
evroute experiments optimality-gap robustness -o results/
evroute results results/ --lang en                 # rebuild the dashboard of a results folder
evroute gallery -o docs/img                        # the seaborn figures of this README
```

Highlights of a full run (all experiments except scalability: 3 minutes on
the machine above):

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
| `scalability` | runtime and labels vs network size on synthetic countries of 1 000-40 000 nodes |

## How correctness is verified

An optimality claim is only as good as its checks:

1. **Independent evaluator.** `evaluate.py` replays every plan from first
   principles - energies, reserves, queues, charging curves, money, wear. An
   exact planner must report exactly the objective the evaluator measures;
   every heuristic's plan goes through the same replay.
2. **Cross-validation suite** (`tests/test_crossval.py`, report in
   [`tests/fuzz_report.md`](tests/fuzz_report.md)): seeded random grids,
   random geometric graphs, mountain corridors and tiny digraphs with every
   vehicle, −15 … 40 °C, 1-3 speed levels, both objectives, static and
   time-dependent queues, stacked AC + DC chargers and 16 odd-but-legal edge
   cases. Eleven invariants: BLS = BLS-A\*, CFP = CFP-A\*, CFP ≤ BLS, Pareto
   extremes = BLS (time/cost), nothing beats CFP, finer grids are never worse,
   lossless JSON round trips, metamorphic relations (relabelling, extra
   stations, more energy ...), local search around every optimum.
3. **Brute force.** On tiny digraphs an exhaustive enumeration of walks,
   stops and targets reproduces the BLS optimum and the Pareto front.
4. **Analytic oracles.** For consecutive stops at a multi-charger site an
   exact dynamic programme over breakpoint energies; CFP matches it.

The suite found and fixed real bugs - a Pareto front that kept a member 1 ulp
faster but 6 EUR dearer, a lossy JSON round trip, a missed DC-then-AC double
stop, an unsound 3-D dominance rule with time-dependent queues. 471 tests,
416 in the fast tier (`pytest -m "not slow"`), run on Python 3.10-3.13 in CI.

```bash
python -m pytest -m "not slow"                    # fast tier, a few minutes
python -m pytest -m slow tests/test_crossval.py   # 3 200 random instances, ~40 min
```

## Project structure

```
evroute/
├── src/evroute/
│   ├── units.py network.py vehicle.py energy.py   # model: units, graph, vehicles, physics
│   ├── charging.py queueing.py scenario.py         # charging functions, Erlang-C queues, scenarios
│   ├── plan.py evaluate.py search.py               # plans, independent evaluator, bounds
│   ├── algorithms/      # naive, greedy, station_graph, genetic, labels (BLS), cfp, pareto
│   ├── generators/      # basic grids, turkey, synthetic, osm, presets
│   ├── data/            # Turkey places and roads (CSV)
│   ├── robustness.py    # Monte Carlo reliability, safety-factor sweeps
│   ├── viz/             # export.py + web/ (viewer), results.py (dashboard), figures.py + style.py (seaborn)
│   ├── experiments/     # runner, plots, figures for the paper
│   ├── server.py        # live planning server (http.server)
│   └── cli.py           # `evroute` command
├── tests/               # pytest suite + fuzz_report.md
├── examples/            # quickstart.py, custom_network.py, viewer_demo.py
└── docs/                # index, model, algorithms, images
```

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

Contributions are welcome - see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Citation

If EVRoute helps your research, please cite it ([`CITATION.cff`](CITATION.cff)):

```bibtex
@software{acik_evroute_2026,
  author  = {Açık, Mustafa},
  title   = {{EVRoute}: exact, battery-, queue- and degradation-aware route planning
             for electric vehicles with charging stations},
  version = {1.0.0},
  year    = {2026},
  url     = {https://github.com/Aseloth99/evroute},
  license = {MIT}
}
```

## Licence

[MIT](LICENSE) © 2026 Mustafa Açık, Karamanoğlu Mehmetbey University,
Department of Computer Engineering.

## Acknowledgements

- The algorithms build on the work of Artmeier et al. (2010), Storandt &
  Funke (2012), Baum, Dibbelt, Gemsa, Wagner & Zündorf (2015, 2019, 2020),
  Montoya et al. (2017), Martins (1984) and Mandow & Pérez de la Cruz
  (2010) - full references in [`docs/algorithms.md`](docs/algorithms.md).
- Place coordinates and elevations of the Turkey case study were compiled
  at gazetteer level from general geographic knowledge; no proprietary map
  or charging-network data is included. The optional OSM loader uses
  [OSMnx](https://github.com/gboeing/osmnx) and
  [OpenStreetMap](https://www.openstreetmap.org/copyright) data (© OpenStreetMap
  contributors, ODbL) when you choose to download it.
- Static figures use [matplotlib](https://matplotlib.org/) and
  [seaborn](https://seaborn.pydata.org/); screenshots are taken with
  [Playwright](https://playwright.dev/). The viewer itself is hand-written
  HTML, CSS and JavaScript with no third-party code.
