"""Independent plan evaluator and feasibility verifier.

:func:`evaluate` replays a :class:`~evroute.plan.PlanDecision` from first
principles -- drive each edge, apply regeneration capping, queue, plug in,
charge along the exact charging function -- and records every quantity in a
:class:`~evroute.plan.RoutePlan`.  It shares no search code with any
algorithm, so it doubles as the correctness oracle in the test-suite:

* a plan is *feasible* iff it is a connected origin->destination path, every
  charging stop is at a station on that path, the energy never drops below
  the reserve at any node, never exceeds the charging cap when charging, and
  the arrival energy meets the destination requirement -- each up to the
  float tolerance :data:`TOL_KWH`, which also applies to the empty battery
  (the battery is empty when the energy falls below ``-TOL_KWH``);
* the objective it reports must match the algorithm's internal objective.

Rules (shared with all algorithms):

* the battery is clamped at full capacity when regenerating;
* every listed stop pays its expected queueing delay (evaluated at the
  arrival time) and the setup time, even if no energy is added;
* charging targets below the current energy add nothing.
"""

from __future__ import annotations

import math

from .plan import ChargeDecision, ChargeStop, PlanDecision, RoutePlan, SearchStats, SearchTrace
from .scenario import PreparedScenario

#: Energy tolerance for every feasibility check [kWh]: the reserve, the
#: arrival requirement, the charging cap and the empty battery.  Energies are
#: sums of float edge consumptions, so a route that is feasible in exact
#: arithmetic can come out ~1e-14 kWh short; only a shortfall of more than
#: ``TOL_KWH`` is a violation (and with ``reserve_soc = 0`` an energy in
#: ``[-TOL_KWH, 0)`` is float noise, not an empty battery).  The exact planners
#: do not use this slack (their own tolerance is 1e-9 kWh): their optimum is
#: over the plans feasible in exact arithmetic, so on a knife-edge instance a
#: plan up to ``TOL_KWH`` short that is accepted here can be faster than it.
TOL_KWH = 1e-6


def evaluate(
    prepared: PreparedScenario,
    decision: PlanDecision,
    *,
    algorithm: str = "",
    internal_objective: float | None = None,
    stats: SearchStats | None = None,
    trace: SearchTrace | None = None,
    energy_override: list[float] | None = None,
    wait_override: list[float] | None = None,
) -> RoutePlan:
    """Measure ``decision`` on ``prepared``.

    ``energy_override`` replaces the per-edge energy of the chosen speed level
    (indexed like ``decision.edges``) and ``wait_override`` the queueing delay
    of each stop (in stop order); the Monte Carlo robustness analysis uses
    them to replay a plan under perturbed consumption and random queues.
    """
    P = prepared
    net = P.net
    edges, levels = decision.edges, decision.levels
    violations: list[str] = []

    # ------------------------------------------------------------ path
    nodes: list[int] = [P.origin] if not edges else [net.tail[edges[0]]]
    for i, e in enumerate(edges):
        if not 0 <= e < net.num_edges:
            violations.append(f"edge index {e} out of range")
            break
        if net.tail[e] != nodes[-1]:
            violations.append(f"edge {e} at position {i} does not continue the path")
            break
        if not 0 <= levels[i] < len(P.speed_factors):
            violations.append(f"invalid speed level {levels[i]} on edge {e}")
            break
        nodes.append(net.head[e])
    if violations:
        return infeasible_plan(P, algorithm, "; ".join(violations), stats, trace, decision=decision)
    if nodes[0] != P.origin:
        violations.append("path does not start at the origin")
    if nodes[-1] != P.destination:
        violations.append("path does not end at the destination")

    by_pos: dict[int, list[ChargeDecision]] = {}
    for c in decision.charges:
        if not 0 <= c.position < len(nodes):
            violations.append(f"charge position {c.position} outside the path")
            continue
        if not 0 <= c.station < len(P.stations) or P.stations[c.station].node != nodes[c.position]:
            violations.append(f"station {c.station} is not at path position {c.position}")
            continue
        by_pos.setdefault(c.position, []).append(c)

    # -------------------------------------------------------- replay
    t = 0.0
    e = P.e_init
    dist = 0.0
    n = len(nodes)
    arrive_t, depart_t = [0.0] * n, [0.0] * n
    arrive_e, depart_e = [0.0] * n, [0.0] * n
    node_dist = [0.0] * n
    stops: list[ChargeStop] = []
    drive_t = wait_t = setup_t = charge_t = 0.0
    consumed = regen = charged = grid = money = soh_charge = 0.0
    drive_cost = 0.0
    stranded_at: float | None = None

    for i in range(n):
        v = nodes[i]
        arrive_t[i], arrive_e[i], node_dist[i] = t, e, dist
        if i > 0 and e < P.e_min - TOL_KWH:
            violations.append(f"reserve undercut at node {v} (position {i}): {e:.3f} < {P.e_min:.3f} kWh")
        for c in by_pos.get(i, []):
            s = c.station
            f = P.cf[s]
            st = P.stations[s]
            if c.target_kwh > P.e_charge_cap + TOL_KWH:
                violations.append(f"charging target {c.target_kwh:.3f} kWh above cap {P.e_charge_cap:.3f}")
            target = min(c.target_kwh, P.e_charge_cap)
            w = P.wait_s(s, t) if wait_override is None else wait_override[len(stops)]
            su = P.setup_s[s]
            e_in = e
            e_out = max(e, target)
            eff = P.vehicle.charge_eff_ac if st.kind == "AC" else P.vehicle.charge_eff_dc
            ch = f.charge_time(e_in, e_out)
            mo = f.money(e_in, e_out)
            so = f.soh(e_in, e_out)
            stops.append(
                ChargeStop(
                    station=s,
                    node=v,
                    position=i,
                    arrival_s=t,
                    wait_s=w,
                    setup_s=su,
                    charge_s=ch,
                    energy_in_kwh=e_in,
                    energy_out_kwh=e_out,
                    soc_in=e_in / P.capacity,
                    soc_out=e_out / P.capacity,
                    grid_energy_kwh=(e_out - e_in) / eff,
                    money=mo,
                    soh_pct=so,
                    power_kw=(e_out - e_in) / ch * 3600.0 if ch > 0 else 0.0,
                    station_power_kw=st.power_kw,
                    station_kind=st.kind,
                    station_name=st.name,
                )
            )
            t += w + su + ch
            wait_t += w
            setup_t += su
            charge_t += ch
            charged += e_out - e_in
            grid += stops[-1].grid_energy_kwh
            money += mo
            soh_charge += so
            e = e_out
        depart_t[i], depart_e[i] = t, e
        if i < n - 1:
            if e < P.e_min - TOL_KWH:
                violations.append(f"departing node {v} (position {i}) below reserve: {e:.3f} kWh")
            ed = edges[i]
            k = levels[i]
            c_e = P.edge_energy[k][ed] if energy_override is None else energy_override[i]
            dt = P.edge_time[k][ed]
            if c_e > 0:
                consumed += c_e
                # same tolerance as the reserve check: with a zero reserve, ending an
                # edge at -1e-14 kWh is rounding noise, not an empty battery (BUG-4)
                if stranded_at is None and e - c_e < -TOL_KWH:
                    frac = max(e, 0.0) / c_e  # where the battery reaches zero
                    stranded_at = dist + frac * net.length[ed]
            new_e = min(e - c_e, P.capacity)
            if new_e > e:
                regen += new_e - e
            drive_cost += P.drive_cost(dt, c_e)
            e = new_e
            t += dt
            drive_t += dt
            dist += net.length[ed]
    min_e = min(min(arrive_e), min(depart_e))

    if e < P.e_dest_min - TOL_KWH:
        violations.append(f"arrival energy {e:.3f} kWh below required {P.e_dest_min:.3f} kWh")
    if stranded_at is not None:
        violations.append(f"battery empty after {stranded_at / 1000.0:.1f} km")

    soh_drive = P.drive_soh_per_kwh * consumed
    deg_money = P.preferences.soh_cost_per_pct * (soh_drive + soh_charge)
    gen = P.vot_per_s * t + P.w_money * money + P.w_deg * (soh_drive + soh_charge)
    plan = RoutePlan(
        algorithm=algorithm,
        feasible=not violations,
        edges=list(edges),
        levels=list(levels),
        nodes=nodes,
        stops=stops,
        node_arrive_s=arrive_t,
        node_depart_s=depart_t,
        node_energy_arrive=arrive_e,
        node_energy_depart=depart_e,
        node_dist_m=node_dist,
        capacity_kwh=P.capacity,
        currency=P.currency,
        violations=violations,
        message="; ".join(violations[:3]),
        total_time_s=t,
        drive_time_s=drive_t,
        wait_time_s=wait_t,
        setup_time_s=setup_t,
        charge_time_s=charge_t,
        distance_m=dist,
        energy_consumed_kwh=consumed,
        energy_regen_kwh=regen,
        energy_charged_kwh=charged,
        grid_energy_kwh=grid,
        money=money,
        soh_drive_pct=soh_drive,
        soh_charge_pct=soh_charge,
        degradation_money=deg_money,
        generalized_cost=gen,
        arrival_soc=e / P.capacity,
        min_soc=min_e / P.capacity,
        stranded_at_m=stranded_at,
        objective=P.objective,
        objective_value=t if P.objective == "time" else gen,
        internal_objective=internal_objective,
        stats=stats,
        trace=trace,
        decision=decision,
    )
    return plan


def infeasible_plan(
    prepared: PreparedScenario,
    algorithm: str,
    message: str,
    stats: SearchStats | None = None,
    trace: SearchTrace | None = None,
    decision: PlanDecision | None = None,
) -> RoutePlan:
    """A plan object representing "no feasible route found"."""
    P = prepared
    return RoutePlan(
        algorithm=algorithm,
        feasible=False,
        edges=[],
        levels=[],
        nodes=[P.origin],
        stops=[],
        node_arrive_s=[0.0],
        node_depart_s=[0.0],
        node_energy_arrive=[P.e_init],
        node_energy_depart=[P.e_init],
        node_dist_m=[0.0],
        capacity_kwh=P.capacity,
        currency=P.currency,
        violations=[message],
        message=message,
        arrival_soc=P.e_init / P.capacity,
        min_soc=P.e_init / P.capacity,
        objective=P.objective,
        stats=stats,
        trace=trace,
        decision=decision,
    )


def check_objective(plan: RoutePlan, rel_tol: float = 1e-7, abs_tol: float = 1e-6) -> bool:
    """True iff the evaluator agrees with the algorithm's internal objective.

    The evaluated objective may be *lower* (an algorithm may conservatively
    over-estimate, e.g. when it pays a stop that adds no energy) but never
    higher than what the algorithm claimed.
    """
    if plan.internal_objective is None or not plan.feasible:
        return True
    lim = plan.internal_objective + max(abs_tol, rel_tol * abs(plan.internal_objective))
    return plan.objective_value <= lim and math.isfinite(plan.objective_value)
