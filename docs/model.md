# The EVRoute model

This document states the optimisation problem solved by EVRoute precisely.
Code references point to the module that implements each equation.

## 1. Road network

A directed graph $G = (V, E)$.  Node $v$ has projected coordinates
$(x_v, y_v)$ [m] and elevation $h_v$ [m].  Edge $e = (u, w)$ has length
$d_e$ [m], free-flow speed $\bar s_e$ [km/h] and a road class
$\kappa_e \in \{\text{motorway},\ldots,\text{residential}\}$ that determines the
mean number of full stops per kilometre $\sigma_\kappa$
(`network.ROAD_CLASSES`).  Two-way roads are two edges.

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

## 4. Charging

The vehicle's battery-side power limit is piecewise constant on SoC bins
$[s_i, s_{i+1})$: $p_i^{veh}$ (published curves are binned with the harmonic
mean, which preserves the exact time to cross a bin; `vehicle.ChargingCurve`).
At a DC station $j$ with power $P_j$ the effective power is
$p_{ij} = \phi(T)\min(p_i^{veh}, P_j)$, where $\phi(T) = \max(\phi_{\min},
1 - \lambda_c (20 - T))$ derates a battery below 20 °C that was not
preconditioned ($\phi = 1$ otherwise).  At an AC station the on-board
charger limits the power to $\min(p^{veh}_{AC}, P_j)$ up to 95 % SoC and to
half of that above (two bins, no cold derating).  With energy breakpoints
$e_i = s_i Q$,

$$ F_j(e) = \sum_{i:\,e_{i+1}\le e} \frac{e_{i+1}-e_i}{p_{ij}}\,3600 + \frac{e - e_{i^*}}{p_{i^*j}}\,3600 $$

is the time to charge from empty to $e$ — piecewise linear, increasing,
convex.  The *charging function* $F_j^{-1}$ ("energy after $t$ seconds") is
piecewise linear, increasing and **concave** (Montoya et al., 2017).  Charging
from $x$ to $y$ takes $F_j(y) - F_j(x)$ seconds, costs
$\pi_j (y - x)/\eta_c$ money and causes wear $\int_x^y \omega_j(e)\,de$
(`charging.ChargingFunction`).

## 5. Queues

Station $j$ has $c_j$ plugs, mean session length $1/\mu_j$ and an hourly
arrival rate $\lambda_j(h)$ (daily sessions × demand profile).  The expected
wait of an arriving car is the M/M/c (Erlang C) delay

$$ W_j(h) = \frac{C(c_j, \lambda_j/\mu_j)}{c_j \mu_j - \lambda_j}, $$

capped when the queue is unstable.  Hourly values are interpolated piecewise
linearly; segments falling faster than $0.95$ s/s are raised so that
$t + W_j(t)$ is non-decreasing (FIFO), which keeps time-dependent
label-setting exact (`queueing.WaitProfile`).  Each stop also pays a set-up
time $\sigma_j$ (parking, plugging, payment).

## 6. Battery wear

SoH loss per kWh of throughput (`vehicle.AgingModel`):

$$ \omega(c, s, T) = \frac{\Delta_{EoL}}{2 N_{EFC} Q}\;
e^{k_c (c - c_0)^+}\;\Big(1 + k_s \tfrac{(s - s_0)^+}{1 - s_0}\Big)\;
\big(1 + k_{cold}(T_{c} - T)^+\big)\big(1 + k_{heat}(T - T_h)^+\big) $$

with C-rate $c = p/Q$ and SoC $s$; driving uses $c = 0.3$, $s = 0.5$.  Wear is
monetised with a cost per SoH percentage point.

## 7. Objectives

A route is a walk $v_0 = o, \ldots, v_n = d$ with speed levels and a set of
charging stops $(i, j, y)$ = (position, station, target energy).  With trip
time $\mathcal T$, money $\mathcal M$ and wear $\mathcal W$:

* **time**: minimise $\mathcal T$;
* **generalised cost**: minimise
  $\mathrm{VoT}\cdot\mathcal T + w_m \mathcal M + w_w\, C_{SoH}\,\mathcal W$;
* **multi-criteria**: the Pareto set of $(\mathcal T, \mathcal M, \mathcal W)$.

## 8. Uncertainty (evaluation only)

For robustness analysis, the *nominal* consumption of each edge is multiplied
by $\xi_{trip}\,\xi_e$ with independent log-normal factors of mean 1, and
queueing delays are drawn from an exponential distribution with the expected
wait as mean (`robustness.monte_carlo`).
