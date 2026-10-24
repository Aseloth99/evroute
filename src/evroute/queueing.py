"""Expected waiting time at charging stations (M/M/c, Erlang C).

A station with ``c`` charge points, arrival rate ``lambda`` (vehicles/h) and
mean session length ``1/mu`` (h) is modelled as an M/M/c queue.  The expected
time a newly arriving car waits before a plug frees up is

.. math::  W_q = \\frac{C(c, a)}{c\\mu - \\lambda},\\qquad a = \\lambda / \\mu,

where ``C(c, a)`` is the Erlang-C probability of having to wait
(Gross et al., *Fundamentals of Queueing Theory*).  Arrival rates follow a
24-hour profile, so the expected wait depends on the time of day.

Routing with time-dependent waits stays exact under Dijkstra-like search only
if the *FIFO property* holds: arriving later never means leaving earlier,
i.e. ``dW/dt >= -1``.  :class:`WaitProfile` interpolates the hourly values
piecewise linearly and repairs any segment that would violate FIFO.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Sequence

from .units import SECONDS_PER_DAY

#: Relative hourly demand on an intercity highway (sums to 1).  Morning and
#: evening peaks with a strong midday travel plateau.
HIGHWAY_PROFILE: tuple[float, ...] = tuple(
    v / 100.0
    for v in (
        1.0, 0.7, 0.5, 0.4, 0.5, 1.0, 2.2, 3.8, 5.2, 5.8, 6.0, 6.2,
        6.3, 6.3, 6.4, 6.6, 6.9, 7.1, 6.4, 5.1, 3.9, 2.9, 2.0, 1.5,
    )
)
#: Relative hourly demand at an urban charger: commute and evening peaks.
URBAN_PROFILE: tuple[float, ...] = tuple(
    v / 100.0
    for v in (
        0.8, 0.5, 0.4, 0.3, 0.4, 1.2, 3.0, 6.0, 7.2, 6.0, 5.2, 5.0,
        5.4, 5.3, 5.1, 5.4, 6.4, 7.6, 7.8, 6.6, 5.0, 3.4, 2.2, 1.4,
    )
)


def erlang_c(c: int, offered_load: float) -> float:
    """Probability that an arrival must wait in an M/M/c queue.

    Uses the numerically stable Erlang-B recursion
    ``B(k) = a B(k-1) / (k + a B(k-1))`` and ``C = B / (1 - rho (1 - B))``.
    """
    if offered_load <= 0.0:
        return 0.0
    if offered_load >= c:
        return 1.0
    b = 1.0
    for k in range(1, c + 1):
        b = offered_load * b / (k + offered_load * b)
    rho = offered_load / c
    return b / (1.0 - rho * (1.0 - b))


def expected_wait_s(arrivals_per_h: float, mean_service_h: float, plugs: int, cap_s: float) -> float:
    """Expected queueing delay [s] (``cap_s`` if the queue is unstable)."""
    if arrivals_per_h <= 0.0 or plugs <= 0:
        return 0.0
    mu = 1.0 / mean_service_h
    a = arrivals_per_h / mu
    if a >= plugs * 0.999:
        return cap_s
    wq_h = erlang_c(plugs, a) / (plugs * mu - arrivals_per_h)
    return min(wq_h * 3600.0, cap_s)


@dataclass
class WaitProfile:
    """Piecewise-linear, FIFO-safe expected wait as a function of time of day."""

    #: Knot times [s after midnight], strictly increasing, first knot >= 0.
    knots_s: list[float]
    #: Expected waiting time at each knot [s].
    waits_s: list[float]

    @classmethod
    def from_hourly(cls, hourly_wait_s: Sequence[float], max_drop_rate: float = 0.95) -> "WaitProfile":
        """Interpolate 24 hourly means placed at the middle of each hour.

        Any decrease steeper than ``max_drop_rate`` seconds per second is
        flattened by raising the later knot, which restores FIFO.
        """
        if len(hourly_wait_s) != 24:
            raise ValueError("need 24 hourly values")
        knots = [(h + 0.5) * 3600.0 for h in range(24)]
        waits = [float(w) for w in hourly_wait_s]
        # repair FIFO on the cyclic sequence (a few passes always suffice)
        for _ in range(4):
            changed = False
            for i in range(24):
                j = (i + 1) % 24
                floor = waits[i] - max_drop_rate * 3600.0
                if waits[j] < floor:
                    waits[j] = floor
                    changed = True
            if not changed:
                break
        return cls(knots, waits)

    @classmethod
    def constant(cls, wait_s: float) -> "WaitProfile":
        return cls([0.0], [float(wait_s)])

    def __call__(self, time_of_day_s: float) -> float:
        if len(self.knots_s) == 1:
            return self.waits_s[0]
        t = time_of_day_s % SECONDS_PER_DAY
        k, w = self.knots_s, self.waits_s
        n = len(k)
        if t < k[0] or t >= k[-1]:
            # wrap-around segment between the last and the first knot
            span = k[0] + SECONDS_PER_DAY - k[-1]
            dt = t - k[-1] if t >= k[-1] else t + SECONDS_PER_DAY - k[-1]
            return w[-1] + (w[0] - w[-1]) * dt / span
        i = bisect_right(k, t) - 1
        i = min(i, n - 2)
        return w[i] + (w[i + 1] - w[i]) * (t - k[i]) / (k[i + 1] - k[i])

    def mean(self) -> float:
        return sum(self(h * 900.0) for h in range(96)) / 96.0

    def to_dict(self) -> dict[str, list[float]]:
        return {"knots_s": self.knots_s, "waits_s": [round(x, 2) for x in self.waits_s]}


def station_wait_profile(
    daily_sessions: float,
    profile: Sequence[float],
    mean_service_min: float,
    plugs: int,
    cap_s: float = 3 * 3600.0,
) -> WaitProfile:
    """Erlang-C waits for a station serving ``daily_sessions`` cars a day."""
    total = sum(profile) or 1.0
    hourly = []
    for share in profile:
        lam = daily_sessions * share / total
        hourly.append(expected_wait_s(lam, mean_service_min / 60.0, plugs, cap_s))
    return WaitProfile.from_hourly(hourly)
