# The EVRoute model

This document states the optimisation problem solved by EVRoute precisely.
Code references point to the module that implements each equation.

## At a glance

Electric-vehicle route planning is "a solved problem" - until you list what a
real trip involves. Most studies and tools handle one or two of these effects
and simplify the rest away:

| Effect | In reality | Common simplification | EVRoute |
|---|---|---|---|
| Nonlinear charging | power drops sharply above ~50-80 % SoC | constant power | exact piecewise-linear charging functions per vehicle × charger |
| Partial charging | stop at 54 %, not at 100 % | always charge to full / fixed target | continuous amounts (CFP) or a fine target grid (BLS) |
| Worthwhile stops | nobody parks and plugs in for +2 % | any amount, however small | optional minimum stop SoC (a floor every stop reaches), exact in every planner |
| Queues | 20-40 min at busy service areas in the afternoon | no waiting | Erlang-C M/M/c queues with hourly demand, time-dependent and FIFO-safe |
| Terrain and regeneration | descents refill the battery | flat roads | physics model with grade, wind, stop-and-go, regeneration limit, full-battery clamp |
| Temperature | −5 °C: smaller battery, heating, slower charging | 20 °C | usable capacity, HVAC load, cold-charging derating, preconditioning |
| Battery wear | fast charging at high SoC ages the pack | ignored | SoH loss from C-rate, SoC and temperature, priced in the objective |
| Prices | dearer above 80 % SoC and at peak hours | one price per kWh | tariffs by SoC and time of day (fixed or from the queue load), exact planning of when to wait for a cheaper price |
| Uncertainty | consumption deviates ±10 % | deterministic | Monte Carlo reliability, safety factor, adaptive driver policy |
| Eco-driving | driving slower can save a stop | one speed | several speed levels per road |
| Vehicle and plug | LFP lasts longer but charges slowly in the cold; some cars take 50 kW at most, have CHAdeMO or no DC inlet | one generic car, every charger fits | chemistry-specific wear and cold derating, DC power limit, connector standards of vehicles and stations |

The model in one screen; the sections below state each part exactly.

- **Road network** ([§1](#1-road-network)) - directed graph with
  coordinates, elevation, length, speed limit and road class (stop-and-go
  intensity).
- **Driving** ([§2](#2-driving-time-and-energy)) - longitudinal dynamics:
  rolling resistance, aerodynamic drag with head-/tailwind and air density,
  grade, stop-and-go losses, auxiliary and temperature-dependent HVAC power,
  regeneration with efficiency and power limit; regeneration into a full
  battery is lost. Optional speed levels (e.g. 100/90/80 %).
- **Battery** ([§3](#3-battery)) - usable capacity shrinks in the cold;
  reserve SoC at every node, minimum arrival SoC, maximum charging SoC,
  optional minimum stop SoC (no "+2 %" top-ups).
- **Charging** ([§4](#4-charging)) - vehicle charging curves
  (piecewise-constant power over SoC bins) capped by the charger power, DC
  power derated for a cold battery without preconditioning → exact,
  piecewise-linear charging functions (concave above the low-SoC ramp-up;
  CFP handles both shapes exactly); prices per kWh with charging losses;
  set-up time per stop.
- **Charging tariffs** ([§5](#5-charging-tariffs)) - price factors above a
  SoC threshold ("the 80 % rule") and in peak hours (fixed, or wherever a
  station's queue model expects it to be busy); the price is fixed when the
  car joins the queue, so waiting for a cheaper period can pay.
- **Queues** ([§6](#6-queues)) - each station is an M/M/c queue with an
  hourly demand profile; the Erlang-C expected wait is made FIFO-safe so that
  time-dependent planning stays exact.
- **Battery wear** ([§7](#7-battery-wear)) - SoH loss per kWh as a function
  of C-rate, SoC and temperature, priced per SoH percentage point.
- **Objectives** ([§8](#8-objectives)) - trip time, generalised cost
  `VoT·T + w_money·money + w_wear·wear`, or the Pareto front of the three.
- **Uncertainty** ([§9](#9-uncertainty-evaluation-only), evaluation only) -
  log-normal trip and edge factors on the consumption, exponential queueing
  delays.
- **Vehicles** ([§10](#10-vehicles-cell-chemistry-dc-power-limit-connectors)) -
  NMC/NCA/LFP wear and cold behaviour, a DC power limit, connector
  standards: a station is used only through a socket of its kind that the
  vehicle has.

## 1. Road network

A directed graph $G = (V, E)$.  Node $v$ has projected coordinates
$(x_v, y_v)$ [m] and elevation $h_v$ [m].  Edge $e = (u, w)$ has length
$d_e$ [m], free-flow speed $\bar s_e$ [km/h] and a road class
$\kappa_e \in \{\text{motorway},\ldots,\text{residential}\}$ that determines the
mean number of full stops per kilometre $\sigma_\kappa$
(`network.ROAD_CLASSES`).  Two-way roads are two edges.

The bundled networks are the approximate network of Turkey
(`generators/turkey.py`), the real-data network of Turkey
(`generators/osm_turkey.py`: OpenStreetMap roads with $\bar s_e$ from the
`maxspeed` tag or the legal default and SRTM elevations $h_v$;
[`real-data.md`](real-data.md)) and synthetic countries
(`generators/synthetic.py`).

## 2. Driving: time and energy

The planner may drive an edge at a *speed level* $k$ with multiplier
$f_k \in (0, 1]$ (e.g. $\{1, 0.9, 0.8\}$ for eco driving).  The cruise speed is

$$ v_{e,k} = f_k \cdot \min(\bar s_e \cdot \tau \cdot \tau_{\kappa_e},\; v_{\max}) $$

with traffic factors $\tau, \tau_\kappa$ (`energy.PhysicsEnergyModel.speed_ms`).

**Tractive force** on grade $\theta_e = \arcsin(\Delta h_e / d_e)$ with head
wind $w_e = W \cos(\beta_e - \beta_W)$ (wind speed $W$ blowing from
$\beta_W$, edge bearing $\beta_e$):

$$ F = c_{rr}\, m g \cos\theta_e + \tfrac12 \rho(T, \bar h_e)\, c_d A\,
(v + w_e)\,|v + w_e| + m g \sin\theta_e . $$

**Battery power** with drivetrain efficiency $\eta_d$, regeneration efficiency
$\eta_r$ and regeneration limit $P_r$:

$$ P_b = \begin{cases} F v / \eta_d & F \ge 0 \\ \max(F v \eta_r, -P_r) & F < 0. \end{cases} $$

**Stop-and-go**: $n_e = \sigma_{\kappa_e} d_e / 1000$ stops, each costing
$\tfrac12 m v^2 (1/\eta_d - \eta_r)$ of energy and $v/a + t_{idle}$ seconds.
**Auxiliaries**: $P_{aux}(T) = P_0 + \min(P_{hvac}, \alpha_h (T_{lo} - T)^+ + \alpha_c (T - T_{hi})^+)$.

Edge time and energy:

$$ t_{e,k} = \frac{d_e}{v} + n_e\left(\frac{v}{a} + t_{idle}\right),\qquad
c_{e,k} = \frac{P_b d_e / v + n_e \tfrac12 m v^2 (1/\eta_d - \eta_r) + P_{aux}\, t_{e,k}}{3.6\cdot 10^6}\ \text{kWh}. $$

$c_{e,k}$ can be negative on descents.  A planning safety factor $\gamma \ge 1$
uses $\gamma c$ for $c > 0$ and $c/\gamma$ for $c < 0$.
Speed levels that are dominated in (time, energy, cost) are discarded.

## 3. Battery

Usable capacity at temperature $T$: $Q(T) = Q_{nom} \max(0.6,\, 1 - \lambda_T (20 - T)^+)$.
Energy after driving edge $e$ at level $k$ from energy $x$:

$$ \delta_{e,k}(x) = \min(x - c_{e,k},\; Q) $$

(regeneration into a full battery is lost).  Constraints on every node $v$ of
the route, both on arrival and on departure:

$$ x_v \ge \underline{e} = r\,Q \quad\text{(reserve)},\qquad x_{dest} \ge \max(\underline{e}, a\,Q),\qquad x \le \bar e = \mu Q \text{ after charging.} $$

The only exception is the origin's initial state $x_0$, which may lie below
the reserve if the vehicle charges there before leaving.  No constraint
applies between consecutive stops at one node, so with several stations at
the origin a stop may also end below the reserve when another stop follows
(at every other node the arrival state already satisfies the reserve).  The
departure state at the origin must satisfy it again.

**Minimum stop SoC (the floor rule).**  Stopping costs parking, plugging in,
the queue and the set-up time whatever the amount, so drivers do not stop for
"+2 %": they charge at least to some comfortable level, or to well above the
reserve when they are in a hurry.  `Preferences.min_stop_soc` $= \psi$ (default
0: off) sets the floor $\varphi = \min(\psi, \mu)\,Q$ (`P.e_stop_min`).  A
charging stop that starts at energy $x$ and ends at $y$ must satisfy

$$ x \ge \varphi \quad\text{or}\quad y \ge \varphi : $$

a stop that starts below the floor ends at it or above, and one that starts at
or above it may add any amount (as without the rule).  A stop that starts below
the floor and adds nothing is a violation too.  For consecutive stops at one
node $x$ is the energy after the previous stop, so the rule binds only the
first one.  Vehicles usually reach a stop near the reserve, so a floor of
reserve + $\Delta$ means "charge at least about $\Delta$" (e.g. reserve + 10 %
in a hurry, + 30-40 % otherwise).  Why a floor and not "add at least $\Delta$
per stop": charging is slower at high SoC, so under a "$+\Delta$" rule a
vehicle with *more* energy could be forced into a *longer* stop than one with
less, and "more energy and no later" would no longer be better -- the property
every exact planner relies on.  The floor keeps it (Lemma 1F in
[`algorithms.md` §1](algorithms.md#1-the-battery-expanded-state-space)): BLS,
Pareto and CFP stay exact with the rule, the heuristics raise every target to
the floor, and `evaluate()` reports a stop that starts and ends below $\varphi$
(up to `TOL_KWH`).  CLI: `--min-stop-soc`; API: `min_stop_soc`.

## 4. Charging

The vehicle's battery-side power limit is piecewise constant on SoC bins
$[s_i, s_{i+1})$: $p_i^{veh}$ (published curves are binned with the harmonic
mean, which preserves the exact time to cross a bin; `vehicle.ChargingCurve`).
At a DC station $j$ with power $P_j$ the effective power is
$p_{ij} = \phi(T)\min(p_i^{veh}, \bar P_{DC}, P_j)$, where $\bar P_{DC}$ is
the vehicle's DC power limit (`dc_max_kw`, $+\infty$ by default; §10) and $\phi(T) = \max(\phi_{\min},
1 - \lambda_c (20 - T))$ derates a battery below 20 °C that was not
preconditioned ($\phi = 1$ otherwise).  At an AC station the on-board
charger limits the power to $\min(p^{veh}_{AC}, P_j)$ up to 95 % SoC and to
half of that above (two bins, no cold derating).  Every bin's power is
floored at 0.5 kW (`charging.build_charging_function`), which matters only
for chargers below about 1.5 kW.  With energy breakpoints
$e_i = s_i Q$,

$$ F_j(e) = \sum_{i:\,e_{i+1}\le e} \frac{e_{i+1}-e_i}{p_{ij}}\,3600 + \frac{e - e_{i^*}}{p_{i^*j}}\,3600 $$

is the time to charge from empty to $e$ — piecewise linear and increasing.
The *charging function* $F_j^{-1}$ ("energy after $t$ seconds") is piecewise
linear and increasing as well.  $F_j$ is convex and $F_j^{-1}$ **concave**
(the textbook model of Montoya et al., 2017) only where the power $p_{ij}$
does not increase with the energy.  Real curves ramp up at low SoC: the DC
curves of all vehicle presets rise up to 5-15 % SoC (e.g. `sedan75` at a
350 kW charger and 20 °C: 139 → 162.5 → 167.5 → 169 kW on its first four bins), so
their charging functions are not concave there.  The exact planners do not
need concavity (CFP's default candidate set covers rising power,
[`algorithms.md` §4](algorithms.md#4-cfp--charging-function-propagation-continuous-charging)).  Charging
from $x$ to $y$ takes $F_j(y) - F_j(x)$ seconds, costs
$\pi_j (y - x)/\eta_c$ money (with a charging tariff: §5) and causes wear
$\int_x^y \omega_j(e)\,de$ (`charging.ChargingFunction`).

## 5. Charging tariffs

A single price $\pi_j$ per kWh hides two practices that make some charging
sessions dearer than others.  *The 80 % rule*: above about 80 % SoC the
charging power falls (§4), so a car that keeps charging there occupies a
fast charger for little energy; operators discourage it with a higher
price, and the drivers waiting behind it do not like it either.  *Peak
hours*: chargers and the grid are busiest at certain times of day, and
energy costs more then.  A `scenario.Tariff` describes both; the default
`Tariff()` is neutral and changes nothing.

**Price factors.**  Let $e_h = s_h Q$ be the energy at the high-SoC
threshold $s_h$ (`high_soc`, default 0.8).  A kWh stored at station $j$ in
price period $p \in \{\text{off-peak}, \text{peak}\}$ costs
$\pi_j\,\varphi_p(e)/\eta_c$ with

| period | $e < e_h$ | $e \ge e_h$ |
|---|---|---|
| off-peak | $1$ | $f_h$ (`high_soc_factor`) |
| peak | $f_p$ (`peak_factor`) | $f_p \cdot f_{ph}$ (`peak_high_soc_factor`, $= f_h$ if not given) |

so the money of a session from $x$ to $y$ is
$M_j^p(x, y) = \int_x^y \pi_j \varphi_p(e)/\eta_c \, de$, piecewise linear
in both energies.  When the factor changes at $e_h$ (in either period),
every charging function gets an extra energy breakpoint at $e_h$: the SoC
bin that contains it is split into two bins with the same power and wear,
so $F_j$ and the wear are unchanged, and each bin carries its own money per
kWh.  $e_h$ thereby becomes a charging target of BLS and the Pareto search
(their default target set contains the breakpoints of every charging
function), and "charge to exactly 80 %" is a plan they can find.

**Price periods.**  `peak_hours` are daily clock intervals $[a, b)$ in hours,
e.g. `((16, 20),)`; an interval with $a > b$ wraps midnight
(`(22, 6)` $= [22, 24) \cup [0, 6)$), one with $a = b$ is empty, and every
interval repeats every day.
With `busy_utilisation` $= u^*$ a station is also in its peak period during
every clock hour $h$ in which the expected utilisation of its queue model
(§6, `Station.utilisation`)

$$ \rho_j(h) = \frac{\lambda_j(h)}{c_j\,\mu_j} $$

is at least $u^*$, so congestion pricing follows the hourly demand profiles
of the Erlang-C model and the peak periods differ from station to station.
The union of these intervals is the peak period $\mathcal P_j \subseteq
[0, 24\,\text{h})$ of station $j$; all other times are off-peak.  The
*price changes* of $j$ are the clock times at which $\mathcal P_j$ begins or
ends (none if it is empty or the whole day).  The clock of trip time $t$ is
$c(t) = (t_{dep} + t) \bmod 24\,\text{h}$, the clock of the queue model.

**The join-time rule.**  The price period of a charging session is fixed
when the vehicle joins the station's queue, at trip time $\tau$:
$p_j(\tau) = \text{peak}$ iff $c(\tau) \in \mathcal P_j$, and the whole
session is priced with $\varphi_{p_j(\tau)}$, however long it lasts.
A car that joins at 15:52 and charges until 19:03 pays the off-peak price
for all of it, although a 16:00-20:00 peak began meanwhile (the cheapest
plan of the README example).

**Voluntary holds.**  A stop may wait before it joins the queue: a hold
until $\tau \ge t$, the arrival time (`ChargeDecision.hold_until_s`; `None`
or a time $\le t$ means no hold).  A stop charging from $x$ to $y$ then
takes

$$ (\tau - t) + W_j(\tau) + \sigma_j + F_j(y) - F_j(x) \ \text{seconds}, $$

with the expected wait at the join time $\tau$, costs $M_j^{p_j(\tau)}(x, y)$
and causes the usual wear.  The hold is part of the trip time, so the
generalised cost pays $\mathrm{VoT}\cdot(\tau - t)$ for it.  With FIFO queues
($t + W_j(t)$ non-decreasing) a hold never makes a stop end earlier: it only
pays when the vehicle joins in a cheaper price period.  Which holds the
exact planners consider, and why that is enough, is the subject of
[`algorithms.md` §6](algorithms.md#6-charging-tariffs-price-periods-and-voluntary-holds).

`Tariff.validate()` (called by `Scenario.validate()`) requires
$0 < s_h < 1$, all factors $> 0$, peak hours in $[0, 24]$ and $u^* > 0$.
The scenario JSON has a `"tariff"` key only for a tariff other than
`Tariff()`.  The demonstration tariff `generators.presets.DEMO_TARIFF`
($f_h = 1.3$, peak 16:00-20:00 with $f_p = 1.15$ and $f_{ph} = 2$, i.e.
2.3 times the price above 80 % in the peak) uses illustrative, synthetic
values, like the station prices of the Turkey case study.

**Turkish public stations since March 2026.**  The amendment of the Şarj
Hizmeti Yönetmeliği (Resmî Gazete 23.03.2026, No. 33202) allows one AC and
one DC price per operator at public stations and forbids tiers by charger
power or state of charge, so the high-SoC factor $f_h$ is an illustrative
option there, not the practice.  Discounts by location and time of day are
allowed, priced at the hour in which the session starts; that is the
join-time rule above with `peak_hours` (and $f_p < 1$ for a discount),
except that EVRoute fixes the period when the car joins the queue, which
is the start of the session only when there is no queue to wait in.  The
same regulation lets an operator end a DC session at 85 % SoC, which
$\mu = 0.85$ (`max_charge_soc`, §3) respects.  The real-data presets
(`turkey-osm-*`) keep the neutral `Tariff()`
([`real-data.md`](real-data.md#prices-and-the-march-2026-regulation)).

## 6. Queues

Station $j$ has $c_j$ plugs, mean session length $1/\mu_j$ and an hourly
arrival rate $\lambda_j(h)$ (daily sessions × demand profile).  The expected
wait of an arriving car is the M/M/c (Erlang C) delay

$$ W_j(h) = \frac{C(c_j, \lambda_j/\mu_j)}{c_j \mu_j - \lambda_j}, $$

capped when the queue is unstable.  Hourly values are interpolated piecewise
linearly; segments falling faster than $0.95$ s/s are raised so that
$t + W_j(t)$ is non-decreasing (FIFO), which keeps time-dependent
label-setting exact (`queueing.WaitProfile`).  Each stop also pays a set-up
time $\sigma_j$ (parking, plugging, payment).

The parameters come with the stations: the synthetic stations of the Turkey
case study have calibrated daily sessions and profiles; the OpenStreetMap
stations of the real-data case study use national averages derived from
the EPDK statistics of September 2026 (DC: 3.7 sessions per plug and day,
$1/\mu_j$ = 36 min; AC: 1.0 and 159 min; the hourly profile of the charged
energy) - assumptions, not measurements
([`real-data.md`](real-data.md#queues-assumptions)).

## 7. Battery wear

SoH loss per kWh of throughput (`vehicle.AgingModel`):

$$ \omega(c, s, T) = \frac{\Delta_{EoL}}{2 N_{EFC} Q}\;
e^{k_c (c - c_0)^+}\;\Big(1 + k_s \tfrac{(s - s_0)^+}{1 - s_0}\Big)\;
\big(1 + k_{cold}(T_{c} - T)^+\big)\big(1 + k_{heat}(T - T_h)^+\big) $$

with C-rate $c = p/Q$ and SoC $s$; driving uses $c = 0.3$, $s = 0.5$.  Wear is
monetised with a cost per SoH percentage point.

## 8. Objectives

A route is a walk $v_0 = o, \ldots, v_n = d$ with speed levels and a set of
charging stops $(i, j, y)$ = (position, station, target energy), each with an
optional hold (§5).  With trip time $\mathcal T$ (holds included), money
$\mathcal M$ (priced with the tariff, §5) and wear $\mathcal W$:

* **time**: minimise $\mathcal T$;
* **generalised cost**: minimise
  $\mathrm{VoT}\cdot\mathcal T + w_m \mathcal M + w_w\, C_{SoH}\,\mathcal W$;
* **multi-criteria**: the Pareto set of $(\mathcal T, \mathcal M, \mathcal W)$.

## 9. Uncertainty (evaluation only)

For robustness analysis, the *nominal* consumption $c > 0$ of each edge is
multiplied by $\xi_{trip}\,\xi_e$ and regeneration ($c \le 0$) is divided by
it, with independent log-normal factors of mean 1, and
queueing delays are drawn from an exponential distribution with the expected
wait as mean (`robustness.monte_carlo`).

## 10. Vehicles: cell chemistry, DC power limit, connectors

**Cell chemistry** (`Vehicle.chemistry`: `NMC`, `NCA` or `LFP`).  Lithium
iron phosphate (LFP) cells differ from the nickel-rich NMC/NCA cells in ways
that matter for trip planning:

- they tolerate a high state of charge and last longer: in the
  multi-chemistry cycling study of Preger et al. (2020, *J. Electrochem.
  Soc.* 167, 120532) the LFP cells outlasted the NMC and NCA cells several
  times and were the least sensitive to the SoC range, so makers of LFP
  cars allow and recommend regular charges to 100 %;
- their voltage curve is flat, which makes the SoC hard to estimate from the
  voltage (another reason for the regular full charge; not modelled);
- they are weaker in the cold: lower ionic conductivity and a higher
  impedance reduce the usable capacity and above all the charging power;
- they store less energy per kilogram, so an LFP pack of the same capacity
  is heavier (the presets carry it in their mass).

The model expresses these facts with illustrative coefficients, not with a
fit to any cell (`vehicle.AgingModel.for_chemistry`, the LFP presets):

| parameter (§3, §4, §7) | NMC / NCA (default) | LFP |
|---|---|---|
| equivalent full cycles to end of life $N_{EFC}$ | 1500 | 3500 |
| high-SoC stress $k_s$ | 1.0 | 0.2 |
| cold stress $k_{cold}$ [1/K] | 0.06 | 0.10 |
| usable-capacity loss in the cold $\lambda_T$ [1/K] | 0.004 | 0.006 |
| cold charging derating $\lambda_c$ [1/K], floor $\phi_{\min}$ | 0.025, 0.35 | 0.035, 0.25 |

`Vehicle.chemistry` itself is a label: the wear model (`Vehicle.aging`) and
the three cold-derating fields are parameters of the vehicle, which the
presets set to the values of their chemistry.  A custom vehicle gets them
with `vehicle.with_chemistry("LFP")` (or with
`aging=AgingModel.for_chemistry("LFP")` and the cold fields of the table);
`with_(chemistry="LFP")` changes the label alone.  A vehicle read from JSON
(`Vehicle.from_dict`, a scenario file) takes the chemistry's values for the
wear and cold fields it leaves out, so `{"chemistry": "LFP", ...}` without
them wears and derates like the LFP presets.

The shape of the DC curve (the early taper of one LFP pack, the long flat
plateau of another) belongs to each vehicle's curve, not to the chemistry.

**DC power limit** (`Vehicle.dc_max_kw`, $\bar P_{DC}$).  The bin powers of
the DC curve become $\min(p_i^{veh}, \bar P_{DC})$ before the charger limit
and the cold derating apply (§4), wherever DC charging functions are built;
`Vehicle.dc_peak_kw` reports the effective peak.  A limit only lowers powers,
so every charging time grows or stays and the optimum can only get slower;
the charging functions stay piecewise linear, so every planner and every
guarantee is unchanged.  A limit at or above the curve's peak leaves the
curve object itself, so the plans are bit-identical.

**Connectors.**  A vehicle lists the standards it can plug into
(`Vehicle.connectors`, default Type 2 + CCS2), a station its sockets
(`Station.connectors`; empty means the default of its kind: Type 2 at AC,
CCS2 at DC):

| standard | carries | | standard | carries |
|---|---|---|---|---|
| `Type2` (IEC 62196-2) | AC | | `CCS2` (Combo 2) | DC |
| `Type1` (SAE J1772) | AC | | `CCS1` (Combo 1) | DC |
| `CHAdeMO` | DC | | `NACS` (SAE J3400) | AC and DC |

A station is modelled as one charger of its kind, so the vehicle can use
station $j$ iff some standard is a socket of $j$, a connector of the vehicle
and carries the kind of $j$ (`Station.usable_by`).  A Type 2 socket listed
at a DC station therefore does not make it usable for a car without a DC
inlet: its charging function is the DC one (data sources split a site with
DC and AC points into two stations, so the AC points stay usable).  The
planners see only the usable stations (`PreparedScenario.station_at`; the
station indices are kept), which is the same as planning on the scenario
without the other stations: every guarantee holds unchanged.  The bounds
of the exact planners (best charging rate, highest power, smallest stop
overhead) cover the usable stations only, which keeps them valid and makes
them tighter.  The evaluator reports a stop at an unusable station as a
violation.

**Vehicle presets** (approximate public figures of each class, generic
names; `vehicle.VEHICLES`; DC peak = effective peak, 10→80 % = minutes at a
charger that does not limit the power, 20 °C):

| key | chemistry | battery [kWh] | DC peak [kW] | AC [kW] | connectors | 10→80 % [min] |
|---|---|---|---|---|---|---|
| `city27` | NMC | 26.8 | 30 | 7 | Type2 + CCS2 | 40 |
| `compact50` | NMC | 50 | 99 | 11 | Type2 + CCS2 | 26 |
| `sedan75` | NMC | 75 | 169 | 11 | Type2 + CCS2 | 26 |
| `suv84_800v` | NMC | 80 | 225 | 11 | Type2 + CCS2 | 18 |
| `togg_t10x_lr` | NMC | 85 | 149 | 11 | Type2 + CCS2 | 31 |
| `van75` | NMC | 72 | 109 | 11 | Type2 + CCS2 | 34 |
| `hatch51_lfp` | LFP | 50.8 | 116 | 11 | Type2 + CCS2 | 27 |
| `sedan60_lfp` | LFP | 57.5 | 168 | 11 | Type2 + CCS2 | 25 |
| `suv60_lfp` | LFP | 60.5 | 88 | 11 | Type2 + CCS2 | 32 |
| `city23_ac` | NMC | 23 | 0 (no DC inlet) | 7.4 | Type2 | - |
| `hatch40_chademo` | NMC | 39 | 46 | 6.6 | Type2 + CHAdeMO | 41 |

`hatch40_chademo` stands for an older car with a passively cooled pack: its
wear model has a stronger heat stress ($k_{heat}$ 0.06 from 25 °C instead
of 0.03 from 30 °C); the power drop after repeated fast charges on a hot
pack is not modelled.
