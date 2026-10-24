"""Exact piecewise-linear charging functions.

A :class:`ChargingFunction` describes charging *one vehicle at one charger
under fixed conditions*.  It works in battery-energy units (kWh stored in the
battery), not SoC fractions, so search code never multiplies by the capacity.

Power is constant on each energy bin ``[e_i, e_{i+1})``, hence

* ``F(e)``  -- seconds needed to charge from 0 to ``e`` -- is piecewise linear,
  increasing and convex;
* ``F^{-1}(t)`` -- the classic *charging function* "energy after ``t`` seconds"
  -- is piecewise linear, increasing and concave;
* ``M(e)`` / ``S(e)`` -- cumulative money and state-of-health cost of charging
  from 0 to ``e`` -- are piecewise linear as well.

All quantities between two energies are differences of these cumulative
functions, evaluated exactly (no numeric integration).
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Sequence

from .units import SECONDS_PER_HOUR


@dataclass
class ChargingFunction:
    """Charging behaviour of one vehicle at one station.

    Parameters
    ----------
    e_breaks:
        Energy breakpoints ``0 = e_0 < e_1 < ... < e_K = capacity`` [kWh].
    power_kw:
        Battery-side power on each bin (``K`` values, all positive).
    money_per_kwh:
        Money per kWh *stored* on each bin (price divided by charging
        efficiency, so grid losses are paid for).
    soh_pct_per_kwh:
        Battery wear on each bin [SoH percentage points per kWh stored].
    """

    e_breaks: list[float]
    power_kw: list[float]
    money_per_kwh: list[float]
    soh_pct_per_kwh: list[float]

    def __post_init__(self) -> None:
        k = len(self.power_kw)
        if len(self.e_breaks) != k + 1 or len(self.money_per_kwh) != k or len(self.soh_pct_per_kwh) != k:
            raise ValueError("inconsistent bin arrays")
        # cumulative tables at the breakpoints
        t = [0.0]
        mo = [0.0]
        so = [0.0]
        for i in range(k):
            de = self.e_breaks[i + 1] - self.e_breaks[i]
            t.append(t[-1] + de / self.power_kw[i] * SECONDS_PER_HOUR)
            mo.append(mo[-1] + de * self.money_per_kwh[i])
            so.append(so[-1] + de * self.soh_pct_per_kwh[i])
        self.t_breaks = t
        self.m_breaks = mo
        self.s_breaks = so
        self.capacity = self.e_breaks[-1]
        self.max_power_kw = max(self.power_kw)

    # ----------------------------------------------------------- core maps
    def _bin(self, e: float) -> int:
        i = bisect_right(self.e_breaks, e) - 1
        return min(max(i, 0), len(self.power_kw) - 1)

    def time_at(self, e: float) -> float:
        """``F(e)``: seconds to charge from empty to ``e``."""
        e = min(max(e, 0.0), self.capacity)
        i = self._bin(e)
        return self.t_breaks[i] + (e - self.e_breaks[i]) / self.power_kw[i] * SECONDS_PER_HOUR

    def energy_at(self, t: float) -> float:
        """``F^{-1}(t)``: energy after charging ``t`` seconds from empty."""
        if t <= 0.0:
            return 0.0
        if t >= self.t_breaks[-1]:
            return self.capacity
        i = bisect_right(self.t_breaks, t) - 1
        i = min(max(i, 0), len(self.power_kw) - 1)
        return self.e_breaks[i] + (t - self.t_breaks[i]) * self.power_kw[i] / SECONDS_PER_HOUR

    def money_at(self, e: float) -> float:
        e = min(max(e, 0.0), self.capacity)
        i = self._bin(e)
        return self.m_breaks[i] + (e - self.e_breaks[i]) * self.money_per_kwh[i]

    def soh_at(self, e: float) -> float:
        e = min(max(e, 0.0), self.capacity)
        i = self._bin(e)
        return self.s_breaks[i] + (e - self.e_breaks[i]) * self.soh_pct_per_kwh[i]

    # --------------------------------------------------------- convenience
    def charge_time(self, e_from: float, e_to: float) -> float:
        """Seconds to charge from ``e_from`` to ``e_to`` (0 if not above)."""
        if e_to <= e_from:
            return 0.0
        return self.time_at(e_to) - self.time_at(e_from)

    def energy_after(self, e_from: float, seconds: float, cap: float | None = None) -> float:
        """Energy after charging ``seconds`` starting at ``e_from``."""
        e = self.energy_at(self.time_at(e_from) + max(seconds, 0.0))
        return min(e, cap) if cap is not None else e

    def money(self, e_from: float, e_to: float) -> float:
        return self.money_at(e_to) - self.money_at(e_from) if e_to > e_from else 0.0

    def soh(self, e_from: float, e_to: float) -> float:
        return self.soh_at(e_to) - self.soh_at(e_from) if e_to > e_from else 0.0

    def power_at(self, e: float) -> float:
        return self.power_kw[self._bin(min(max(e, 0.0), self.capacity * (1 - 1e-12)))]

    def breakpoints_between(self, e_from: float, e_to: float) -> list[float]:
        """Interior energy breakpoints strictly inside ``(e_from, e_to)``."""
        lo = bisect_right(self.e_breaks, e_from)
        out = []
        for j in range(lo, len(self.e_breaks)):
            b = self.e_breaks[j]
            if b >= e_to:
                break
            if b > e_from:
                out.append(b)
        return out

    def min_rate(self) -> tuple[float, float]:
        """Smallest money and SoH cost per kWh over all bins (for bounds)."""
        return min(self.money_per_kwh), min(self.soh_pct_per_kwh)


def build_charging_function(
    capacity_kwh: float,
    soc_breaks: Sequence[float],
    vehicle_power_kw: Sequence[float],
    station_power_kw: float,
    temperature_factor: float,
    price_per_kwh: float,
    efficiency: float,
    soh_rate_fn,
) -> ChargingFunction:
    """Combine vehicle curve and station limit into a :class:`ChargingFunction`.

    ``soh_rate_fn(c_rate, soc)`` returns SoH loss per kWh for a bin.
    """
    e_breaks = [s * capacity_kwh for s in soc_breaks]
    powers, money, soh = [], [], []
    for i, p_veh in enumerate(vehicle_power_kw):
        p = max(min(p_veh, station_power_kw) * temperature_factor, 0.5)
        powers.append(p)
        money.append(price_per_kwh / efficiency)
        mid = 0.5 * (soc_breaks[i] + soc_breaks[i + 1])
        soh.append(soh_rate_fn(p / capacity_kwh, mid))
    return ChargingFunction(e_breaks, powers, money, soh)
