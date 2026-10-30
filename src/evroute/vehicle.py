"""Vehicle model: longitudinal-dynamics parameters, charging curve, ageing.

The charging behaviour is modelled the way the EV routing literature does it
(Montoya et al. 2017; Baum et al. 2019): the vehicle's maximum charging power
is *piecewise constant over SoC bins*.  The resulting charging function
"SoC as a function of time at the charger" is then piecewise linear, and
concave wherever the power does not rise with the SoC (every preset ramps up
over the first 5-15 % SoC); the exact algorithms in :mod:`evroute.algorithms`
handle both shapes.

Published curves are usually given as a polyline (power vs SoC).
:meth:`ChargingCurve.from_points` converts such a polyline into bins using the
*harmonic* mean of the power inside each bin, which preserves the exact time
needed to cross the bin.

The presets below use approximate, publicly reported figures.  They are
meant to be realistic, not authoritative: calibrate them against your own
measurements before drawing vehicle-specific conclusions.

Cell chemistry
--------------
``Vehicle.chemistry`` is ``"NMC"``, ``"NCA"`` (nickel-rich layered oxides,
the classic long-range cells) or ``"LFP"`` (lithium iron phosphate).  The
qualitative differences that matter for trip planning are well established:

* **LFP tolerates a high state of charge and lasts longer.**  In the
  multi-chemistry cycling study of Preger et al. (2020, J. Electrochem. Soc.
  167, 120532) the LFP cells kept their capacity for far more cycles than
  the NMC and NCA cells and were the least sensitive to the SoC range;
  NMC/NCA age faster when they are held or cycled near 100 %.  Makers of LFP
  cars therefore allow (and recommend) regular charges to 100 %.
* **LFP has a flat voltage curve**: the SoC is harder to estimate from the
  voltage, which is another reason for regular full charges (a balancing
  and calibration need, not modelled here).
* **LFP is weaker in the cold**: lower ionic conductivity and higher
  impedance reduce the usable capacity and, above all, the charging power at
  low cell temperatures.
* **LFP is heavier per kWh** (lower specific energy), so an LFP pack of the
  same capacity weighs more -- the presets carry that in ``mass_kg``.

:meth:`AgingModel.for_chemistry` returns illustrative wear coefficients for
these facts (``"LFP"``: about 3500 equivalent full cycles to 80 % SoH
instead of 1500, a much weaker high-SoC stress, a stronger cold stress);
NMC and NCA share the default model.  The LFP presets also derate harder in
the cold (``cold_capacity_loss_per_deg``, ``cold_charge_loss_per_deg``,
``min_cold_charge_factor``).  None of these numbers is a fit to a specific
cell: they encode the direction and the rough size of the effects only.

Charging limits and connectors
------------------------------
``Vehicle.dc_max_kw`` caps the DC charging power (``min(curve power,
dc_max_kw)`` in every bin, :attr:`Vehicle.dc_curve_effective`), e.g. for a
car whose maker limits fast charging, or to plan with a charger the driver
knows to be slow; ``None`` keeps the curve.  ``Vehicle.connectors`` lists the
connector standards the vehicle can plug into (:data:`CONNECTORS`); a
station is usable only through a socket of its own kind (AC or DC) that the
vehicle has (:meth:`evroute.scenario.Station.usable_by`), so a car without
a DC inlet never fast-charges and a CHAdeMO car skips CCS-only chargers.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Sequence

#: Cell chemistries of :attr:`Vehicle.chemistry` (see the module documentation).
CHEMISTRIES: tuple[str, ...] = ("NMC", "NCA", "LFP")

#: Connector standards known to the model and the kinds of charging (``"AC"``,
#: ``"DC"``) each one carries: ``Type2`` (IEC 62196-2 "Mennekes", the European
#: AC standard), ``Type1`` (SAE J1772 AC), ``CCS2`` / ``CCS1`` (Combined
#: Charging System, DC on a Type 2 / Type 1 inlet), ``CHAdeMO`` (DC) and
#: ``NACS`` (SAE J3400, AC and DC).  A vehicle lists its inlets
#: (:attr:`Vehicle.connectors`), a station its sockets
#: (:attr:`evroute.scenario.Station.connectors`).
CONNECTORS: dict[str, tuple[str, ...]] = {
    "Type2": ("AC",),
    "Type1": ("AC",),
    "CCS2": ("DC",),
    "CCS1": ("DC",),
    "CHAdeMO": ("DC",),
    "NACS": ("AC", "DC"),
}


def check_connectors(name: str, connectors: Sequence[str]) -> None:
    """Raise :class:`ValueError` unless every entry of ``connectors`` is a key of :data:`CONNECTORS`."""
    if isinstance(connectors, str):
        raise ValueError(f"{name} must be a sequence of connector names, not the string {connectors!r}")
    bad = [c for c in connectors if c not in CONNECTORS]
    if bad:
        raise ValueError(f"{name}: unknown connector(s) {bad}; choose from {list(CONNECTORS)}")


@dataclass(frozen=True)
class ChargingCurve:
    """Maximum battery-side charging power, piecewise constant over SoC.

    ``power_kw[i]`` is valid on ``[soc_breaks[i], soc_breaks[i + 1])``.
    """

    soc_breaks: tuple[float, ...]
    power_kw: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.soc_breaks) != len(self.power_kw) + 1:
            raise ValueError("need exactly one more breakpoint than power values")
        if abs(self.soc_breaks[0]) > 1e-12 or abs(self.soc_breaks[-1] - 1.0) > 1e-12:
            raise ValueError("SoC breakpoints must span [0, 1]")
        if any(b <= a for a, b in zip(self.soc_breaks, self.soc_breaks[1:])):
            raise ValueError("SoC breakpoints must be strictly increasing")
        if any(p <= 0 for p in self.power_kw):
            raise ValueError("charging power must be positive in every bin")

    @classmethod
    def from_points(cls, points: Sequence[tuple[float, float]], bins: int = 20) -> "ChargingCurve":
        """Bin a power-vs-SoC polyline into ``bins`` equal-width SoC bins."""
        pts = sorted((float(s), float(p)) for s, p in points)
        if pts[0][0] > 0.0:
            pts.insert(0, (0.0, pts[0][1]))
        if pts[-1][0] < 1.0:
            pts.append((1.0, pts[-1][1]))
        breaks = tuple(i / bins for i in range(bins + 1))
        powers = []
        for a, b in zip(breaks, breaks[1:]):
            # harmonic mean over [a, b] of the piecewise-linear polyline
            inv = _integrate_inverse_polyline(pts, a, b)
            powers.append((b - a) / inv)
        return cls(breaks, tuple(powers))

    @classmethod
    def constant(cls, power_kw: float, taper_from: float = 0.95, taper_factor: float = 0.5) -> "ChargingCurve":
        """Flat curve with a short taper near full (typical AC behaviour)."""
        if taper_from >= 1.0:
            return cls((0.0, 1.0), (power_kw,))
        return cls((0.0, taper_from, 1.0), (power_kw, power_kw * taper_factor))

    def power_at(self, soc: float) -> float:
        soc = min(max(soc, 0.0), 1.0)
        for i in range(len(self.power_kw)):
            if soc < self.soc_breaks[i + 1]:
                return self.power_kw[i]
        return self.power_kw[-1]

    @property
    def peak_kw(self) -> float:
        return max(self.power_kw)

    def capped(self, limit_kw: float | None) -> "ChargingCurve":
        """The curve with every power capped at ``limit_kw`` (same breakpoints).

        Returns ``self`` when ``limit_kw`` is ``None`` or no bin exceeds it, so
        an inactive limit changes nothing, not even the float values.
        """
        if limit_kw is None or self.peak_kw <= limit_kw:
            return self
        return ChargingCurve(self.soc_breaks, tuple(min(p, limit_kw) for p in self.power_kw))

    def to_dict(self) -> dict[str, Any]:
        return {"soc_breaks": list(self.soc_breaks), "power_kw": list(self.power_kw)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChargingCurve":
        return cls(tuple(d["soc_breaks"]), tuple(d["power_kw"]))


def _integrate_inverse_polyline(pts: list[tuple[float, float]], a: float, b: float) -> float:
    """Integral of 1 / p(s) ds over [a, b] for a positive polyline ``p``."""
    total = 0.0
    for (s0, p0), (s1, p1) in zip(pts, pts[1:]):
        lo, hi = max(a, s0), min(b, s1)
        if hi <= lo:
            continue
        if s1 == s0:
            continue
        slope = (p1 - p0) / (s1 - s0)
        q0 = p0 + slope * (lo - s0)
        q1 = p0 + slope * (hi - s0)
        if abs(slope) < 1e-12:
            total += (hi - lo) / q0
        else:
            total += math.log(q1 / q0) / slope
    return total


@dataclass(frozen=True)
class AgingModel:
    """Semi-empirical battery wear model (capacity fade per kWh throughput).

    The base fade spreads the end-of-life capacity drop evenly over the rated
    cycle life; stress multipliers raise it for high C-rates, high SoC, cold
    charging (lithium plating) and heat.  The functional form follows the
    stress-factor approach reviewed by Pelletier et al. (2017, TR-B 103) with
    a C-rate exponent in the spirit of Wang et al. (2011, J. Power Sources).
    Coefficients are illustrative defaults, not cell-specific fits;
    :meth:`for_chemistry` gives the variant of a cell chemistry.
    """

    cycle_life_efc: float = 1500.0
    eol_fade_pct: float = 20.0
    k_crate: float = 0.35
    c_ref: float = 0.5
    k_high_soc: float = 1.0
    high_soc_start: float = 0.8
    k_cold: float = 0.06
    cold_ref_c: float = 15.0
    k_heat: float = 0.03
    heat_ref_c: float = 30.0

    def fade_pct_per_kwh(self, capacity_kwh: float, c_rate: float, soc: float, temp_c: float) -> float:
        """State-of-health loss [percentage points] per kWh of throughput."""
        base = self.eol_fade_pct / (self.cycle_life_efc * 2.0 * capacity_kwh)
        stress = math.exp(self.k_crate * max(0.0, c_rate - self.c_ref))
        if soc > self.high_soc_start:
            stress *= 1.0 + self.k_high_soc * (soc - self.high_soc_start) / (1.0 - self.high_soc_start)
        stress *= 1.0 + self.k_cold * max(0.0, self.cold_ref_c - temp_c)
        stress *= 1.0 + self.k_heat * max(0.0, temp_c - self.heat_ref_c)
        return base * stress

    @classmethod
    def for_chemistry(cls, chemistry: str, **changes: Any) -> "AgingModel":
        """Illustrative wear model of a cell chemistry (:data:`CHEMISTRIES`).

        ``"NMC"`` and ``"NCA"``: the defaults.  ``"LFP"``: 3500 equivalent full
        cycles to the end of life instead of 1500 (LFP cells outlast NMC and
        NCA cells several times in cycling studies such as Preger et al.
        2020), a high-SoC stress of a fifth of the default (LFP tolerates a
        full battery) and a cold stress about 1.7 times the default
        (``k_cold`` 0.10 instead of 0.06: lithium plating sets in earlier).
        ``changes`` override single fields.  The values are directions and
        rough sizes, not a cell fit.
        """
        if chemistry not in CHEMISTRIES:
            raise ValueError(f"unknown chemistry {chemistry!r}; choose from {list(CHEMISTRIES)}")
        base = cls(cycle_life_efc=3500.0, k_high_soc=0.2, k_cold=0.10) if chemistry == "LFP" else cls()
        return replace(base, **changes) if changes else base


@dataclass(frozen=True)
class Vehicle:
    """Battery electric vehicle parameters (SI units unless noted)."""

    key: str
    name: str
    battery_kwh: float
    mass_kg: float
    drag_coeff: float
    frontal_area_m2: float
    rolling_coeff: float
    dc_curve: ChargingCurve
    ac_max_kw: float = 11.0
    drivetrain_eff: float = 0.90
    regen_eff: float = 0.68
    max_regen_kw: float = 70.0
    max_speed_kmh: float = 160.0
    aux_base_kw: float = 0.35
    heat_kw_per_deg: float = 0.11
    cool_kw_per_deg: float = 0.09
    hvac_max_kw: float = 3.5
    comfort_low_c: float = 18.0
    comfort_high_c: float = 24.0
    charge_eff_dc: float = 0.94
    charge_eff_ac: float = 0.89
    cold_capacity_loss_per_deg: float = 0.004
    cold_charge_loss_per_deg: float = 0.025
    min_cold_charge_factor: float = 0.35
    aging: AgingModel = field(default_factory=AgingModel)
    notes: str = ""
    #: Cell chemistry: ``"NMC"``, ``"NCA"`` or ``"LFP"``.
    chemistry: str = "NMC"
    #: Highest DC charging power the vehicle accepts [kW] (``None``: the curve's peak).
    dc_max_kw: float | None = None
    #: Connector standards the vehicle can use (see :attr:`Station.connectors`).
    connectors: tuple[str, ...] = ("Type2", "CCS2")

    def __post_init__(self) -> None:
        object.__setattr__(self, "connectors", tuple(self.connectors))
        if self.chemistry not in CHEMISTRIES:
            raise ValueError(
                f"vehicle {self.key!r}: unknown chemistry {self.chemistry!r}; choose from {list(CHEMISTRIES)}"
            )
        lim = self.dc_max_kw
        if lim is not None and (isinstance(lim, bool) or not isinstance(lim, (int, float)) or not 0.0 < lim < math.inf):
            raise ValueError(f"vehicle {self.key!r}: dc_max_kw must be a finite number > 0 or None, got {lim!r}")
        check_connectors(f"vehicle {self.key!r}: connectors", self.connectors)
        if not self.connectors:
            raise ValueError(f"vehicle {self.key!r}: needs at least one connector")

    # ------------------------------------------------------------ derived
    @property
    def dc_capable(self) -> bool:
        """Can the vehicle charge with DC at all (a connector that carries DC)?"""
        return any("DC" in CONNECTORS[c] for c in self.connectors)

    @property
    def dc_curve_effective(self) -> ChargingCurve:
        """The DC curve with every power capped at :attr:`dc_max_kw` (``dc_curve`` itself without a cap)."""
        return self.dc_curve.capped(self.dc_max_kw)

    @property
    def dc_peak_kw(self) -> float:
        """Highest DC power the vehicle draws [kW]: the curve's peak capped at
        :attr:`dc_max_kw`, ``0`` for a vehicle without a DC connector."""
        return self.dc_curve_effective.peak_kw if self.dc_capable else 0.0

    def usable_capacity_kwh(self, temperature_c: float) -> float:
        loss = self.cold_capacity_loss_per_deg * max(0.0, 20.0 - temperature_c)
        return self.battery_kwh * max(0.6, 1.0 - loss)

    def aux_power_kw(self, temperature_c: float) -> float:
        p = self.aux_base_kw
        if temperature_c < self.comfort_low_c:
            p += min(self.hvac_max_kw, self.heat_kw_per_deg * (self.comfort_low_c - temperature_c))
        elif temperature_c > self.comfort_high_c:
            p += min(self.hvac_max_kw, self.cool_kw_per_deg * (temperature_c - self.comfort_high_c))
        return p

    def dc_temperature_factor(self, temperature_c: float, preconditioned: bool) -> float:
        if preconditioned or temperature_c >= 20.0:
            return 1.0
        f = 1.0 - self.cold_charge_loss_per_deg * (20.0 - temperature_c)
        return max(self.min_cold_charge_factor, f)

    def with_(self, **changes: Any) -> "Vehicle":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dc_curve"] = self.dc_curve.to_dict()
        d["connectors"] = list(self.connectors)
        # defaults of the fields added in 1.0 are not written: earlier instances keep their JSON form
        for key, default in _V1_DEFAULTS.items():
            if d[key] == default:
                del d[key]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Vehicle":
        d = dict(d)
        d["dc_curve"] = ChargingCurve.from_dict(d["dc_curve"])
        d["aging"] = AgingModel(**d.get("aging", {}))
        if "connectors" in d:
            d["connectors"] = tuple(d["connectors"])
        return cls(**d)


_V1_DEFAULTS: dict[str, Any] = {"chemistry": "NMC", "dc_max_kw": None, "connectors": ["Type2", "CCS2"]}


def _v(key: str, name: str, curve: list[tuple[float, float]], **kw: Any) -> Vehicle:
    return Vehicle(key=key, name=name, dc_curve=ChargingCurve.from_points(curve, bins=20), **kw)


#: Cold derating of the LFP presets (illustrative: stronger than the defaults
#: 0.004 / 0.025 / 0.35, see the module documentation).
_LFP_COLD: dict[str, float] = {
    "cold_capacity_loss_per_deg": 0.006,
    "cold_charge_loss_per_deg": 0.035,
    "min_cold_charge_factor": 0.25,
}


#: Built-in vehicle presets (approximate public figures; see module docs).
VEHICLES: dict[str, Vehicle] = {
    v.key: v
    for v in [
        _v(
            "city27",
            "City EV 27 kWh",
            [(0.0, 25), (0.1, 30), (0.6, 30), (0.8, 20), (0.9, 12), (1.0, 4)],
            battery_kwh=26.8,
            mass_kg=1050,
            drag_coeff=0.31,
            frontal_area_m2=2.2,
            rolling_coeff=0.0095,
            ac_max_kw=7.0,
            max_regen_kw=30,
            max_speed_kmh=125,
            heat_kw_per_deg=0.13,
            notes="Small low-cost city car; exercises routes with many stops.",
            chemistry="NMC",
        ),
        _v(
            "compact50",
            "Compact EV 50 kWh",
            [(0.0, 70), (0.1, 100), (0.5, 90), (0.65, 70), (0.8, 45), (0.9, 25), (1.0, 8)],
            battery_kwh=50.0,
            mass_kg=1600,
            drag_coeff=0.29,
            frontal_area_m2=2.3,
            rolling_coeff=0.009,
            max_regen_kw=60,
            chemistry="NMC",
        ),
        _v(
            "sedan75",
            "Sedan EV 75 kWh (400 V)",
            [(0.0, 120), (0.05, 160), (0.15, 170), (0.4, 160), (0.55, 120), (0.65, 95), (0.75, 75), (0.85, 50), (0.95, 25), (1.0, 10)],
            battery_kwh=75.0,
            mass_kg=1950,
            drag_coeff=0.23,
            frontal_area_m2=2.22,
            rolling_coeff=0.0085,
            max_regen_kw=90,
            heat_kw_per_deg=0.08,
            notes="Heat-pump equipped aerodynamic sedan.",
            chemistry="NMC",
        ),
        _v(
            "suv84_800v",
            "Crossover EV 84 kWh (800 V)",
            [(0.0, 180), (0.05, 225), (0.5, 225), (0.6, 180), (0.7, 140), (0.8, 90), (0.9, 40), (1.0, 12)],
            battery_kwh=80.0,
            mass_kg=2200,
            drag_coeff=0.29,
            frontal_area_m2=2.6,
            rolling_coeff=0.009,
            max_regen_kw=120,
            ac_max_kw=11.0,
            notes="800 V architecture with a long high-power plateau.",
            chemistry="NMC",
        ),
        _v(
            "togg_t10x_lr",
            "Turkish C-SUV 85 kWh usable (T10X-like, approx.)",
            [(0.0, 110), (0.1, 150), (0.45, 140), (0.6, 110), (0.75, 75), (0.85, 45), (0.95, 20), (1.0, 8)],
            battery_kwh=85.0,
            mass_kg=2150,
            drag_coeff=0.29,
            frontal_area_m2=2.65,
            rolling_coeff=0.009,
            max_regen_kw=100,
            ac_max_kw=11.0,
            notes="Approximation built from publicly reported specs of a Turkish C-SUV; not manufacturer data.",
            chemistry="NMC",
        ),
        _v(
            "van75",
            "Electric Van 75 kWh",
            [(0.0, 80), (0.1, 110), (0.5, 100), (0.7, 70), (0.85, 40), (1.0, 8)],
            battery_kwh=72.0,
            mass_kg=2800,
            drag_coeff=0.33,
            frontal_area_m2=3.9,
            rolling_coeff=0.0105,
            max_regen_kw=80,
            max_speed_kmh=130,
            hvac_max_kw=4.5,
            chemistry="NMC",
        ),
        # ------------------------------------------------------------------ added in 1.0
        _v(
            "hatch51_lfp",
            "Hatchback EV 51 kWh LFP",
            [(0.0, 70), (0.1, 100), (0.2, 117), (0.3, 112), (0.45, 90), (0.6, 70), (0.7, 58), (0.8, 42), (0.9, 25),
             (1.0, 8)],
            battery_kwh=50.8,
            mass_kg=1680,
            drag_coeff=0.29,
            frontal_area_m2=2.35,
            rolling_coeff=0.009,
            ac_max_kw=11.0,
            max_regen_kw=70,
            chemistry="LFP",
            aging=AgingModel.for_chemistry("LFP"),
            **_LFP_COLD,
            notes="Hatchback with a 51 kWh LFP pack, about 117 kW DC peak and 11 kW AC "
                  "(approximate public figures of the class, generic name).",
        ),
        _v(
            "sedan60_lfp",
            "Sedan EV 60 kWh LFP",
            [(0.0, 120), (0.05, 165), (0.12, 170), (0.25, 150), (0.4, 115), (0.55, 90), (0.7, 65), (0.8, 50),
             (0.9, 30), (1.0, 12)],
            battery_kwh=57.5,
            mass_kg=1760,
            drag_coeff=0.23,
            frontal_area_m2=2.22,
            rolling_coeff=0.0085,
            ac_max_kw=11.0,
            max_regen_kw=80,
            heat_kw_per_deg=0.08,
            chemistry="LFP",
            aging=AgingModel.for_chemistry("LFP"),
            **_LFP_COLD,
            notes="Aerodynamic sedan with a 60 kWh LFP pack (about 57.5 kWh usable): about 170 kW DC peak with the "
                  "early taper typical of LFP (approximate public figures of the class, generic name).",
        ),
        _v(
            "suv60_lfp",
            "Compact SUV EV 60 kWh LFP",
            [(0.0, 50), (0.1, 85), (0.2, 88), (0.5, 88), (0.6, 78), (0.7, 65), (0.8, 50), (0.9, 30), (1.0, 8)],
            battery_kwh=60.5,
            mass_kg=1750,
            drag_coeff=0.29,
            frontal_area_m2=2.6,
            rolling_coeff=0.0095,
            ac_max_kw=11.0,
            max_regen_kw=70,
            chemistry="LFP",
            aging=AgingModel.for_chemistry("LFP"),
            **_LFP_COLD,
            notes="Compact SUV with a 60 kWh LFP pack: about 88 kW DC on a long flat plateau "
                  "(approximate public figures of the class, generic name).",
        ),
        _v(
            "city23_ac",
            "City EV 23 kWh (AC only)",
            [(0.0, 7.4), (1.0, 7.4)],
            battery_kwh=23.0,
            mass_kg=1150,
            drag_coeff=0.33,
            frontal_area_m2=2.15,
            rolling_coeff=0.0095,
            ac_max_kw=7.4,
            max_regen_kw=25,
            max_speed_kmh=135,
            heat_kw_per_deg=0.13,
            chemistry="NMC",
            connectors=("Type2",),
            notes="Small city car without a DC inlet (Type 2 only, 7.4 kW on-board charger): it never fast-charges; "
                  "its DC curve is a placeholder at the AC power and is never used.",
        ),
        _v(
            "hatch40_chademo",
            "Hatchback EV 40 kWh (CHAdeMO)",
            [(0.0, 38), (0.1, 46), (0.5, 44), (0.7, 35), (0.8, 25), (0.9, 15), (1.0, 6)],
            battery_kwh=39.0,
            mass_kg=1580,
            drag_coeff=0.28,
            frontal_area_m2=2.28,
            rolling_coeff=0.0095,
            ac_max_kw=6.6,
            max_regen_kw=50,
            max_speed_kmh=144,
            chemistry="NMC",
            connectors=("Type2", "CHAdeMO"),
            # passive air cooling: the pack runs warm, so heat ages it faster than an actively cooled one
            aging=AgingModel.for_chemistry("NMC", k_heat=0.06, heat_ref_c=25.0),
            notes="Older hatchback with a CHAdeMO inlet (about 46 kW DC, 6.6 kW AC) and a passively cooled 40 kWh "
                  "pack: stronger heat stress in the wear model; the power drop after repeated fast charges on a hot "
                  "pack is not modelled (approximate public figures of the class, generic name).",
        ),
    ]
}


def get_vehicle(key: str) -> Vehicle:
    try:
        return VEHICLES[key]
    except KeyError:
        raise KeyError(f"unknown vehicle {key!r}; choose from {sorted(VEHICLES)}") from None
