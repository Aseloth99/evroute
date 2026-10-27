"""Registry of named, reproducible benchmark scenarios.

>>> from evroute.generators.presets import get_scenario, list_scenarios
>>> "turkey-karaman-istanbul" in [p["name"] for p in list_scenarios()]
True
>>> sc = get_scenario("corridor", initial_soc=0.7, objective="cost")

Every preset is a builder plus default arguments.  ``get_scenario(name,
**overrides)`` routes each override to where it belongs:

* arguments of the builder (``initial_soc``, ``temperature_c``,
  ``departure``, ``vehicle``, ``seed``, ``origin`` ...);
* :class:`~evroute.scenario.Conditions` fields (``wind_speed_ms``,
  ``traffic_factor``, ...);
* :class:`~evroute.scenario.Preferences` fields (``objective``,
  ``reserve_soc``, ``speed_factors``, ...);
* ``name``, which renames the resulting scenario (the preset name itself is
  positional-only, so this never collides);
* ``tariff``, a :class:`~evroute.scenario.Tariff` (or a dict of its fields)
  that replaces the scenario's charging tariff; a preset may set one in its
  defaults (``turkey-karaman-istanbul-tariff`` uses :data:`DEMO_TARIFF`).

Networks are cached by the generators, so building a preset twice costs
almost nothing; stations are fresh copies every time.  Register your own
presets with :func:`register_scenario`; they then appear in the CLI,
experiments and viewer like the built-in ones.
"""

from __future__ import annotations

import difflib
import inspect
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from ..scenario import Scenario, Tariff
from .basic import grid_scenario
from .synthetic import corridor_scenario, country_scenario, random_geometric_scenario, split_overrides
from .turkey import turkey_scenario

__all__ = ["DEMO_TARIFF", "Preset", "PRESETS", "get_scenario", "list_scenarios", "register_scenario"]

#: Demonstration charging tariff: "the 80 % rule" and an evening peak.  The
#: values are **illustrative, synthetic** choices (like the station prices of
#: the case study), not the prices of any operator:
#:
#: * every kWh stored above 80 % SoC costs 1.3x the station price;
#: * 16:00-20:00 local time is the peak period, with 1.15x the price below
#:   80 % and 2x (1.15 x 2 = 2.3x the station price) above it.
#:
#: Used by ``--tariff 80`` on the command line, the
#: ``turkey-karaman-istanbul-tariff`` preset, the live viewer's tariff
#: switch and the ``tariffs`` experiment.
DEMO_TARIFF = Tariff(
    high_soc=0.8, high_soc_factor=1.3, peak_hours=((16.0, 20.0),), peak_factor=1.15, peak_high_soc_factor=2.0
)


def _as_tariff(value: Any) -> Tariff:
    """A :class:`Tariff` from a ``Tariff`` or a dict of its fields."""
    if isinstance(value, Tariff):
        return value
    if isinstance(value, dict):
        return Tariff.from_dict(value)
    raise TypeError(f"tariff must be a Tariff or a dict of its fields, got {type(value).__name__}")


def _json_value(value: Any) -> Any:
    """``value`` as stored in listings and ``meta``: tuples -> lists, a tariff -> its dict."""
    if isinstance(value, Tariff):
        return value.to_dict()
    return list(value) if isinstance(value, tuple) else value


@dataclass(frozen=True)
class Preset:
    """A named scenario recipe."""

    name: str
    description: str
    builder: Callable[..., Scenario]
    defaults: dict[str, Any] = field(default_factory=dict)
    family: str = "synthetic"
    tags: tuple[str, ...] = ()
    #: Rough network size, for listings and experiment planning.
    approx_nodes: int | None = None
    #: Longer explanation (what the instance is meant to show); ``description``
    #: stays a one-line summary so that listings remain readable.
    notes: str = ""

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "notes": self.notes,
            "family": self.family,
            "tags": list(self.tags),
            "approx_nodes": self.approx_nodes,
            "defaults": {k: _json_value(v) for k, v in self.defaults.items()},
        }

    def build(self, **overrides: Any) -> Scenario:
        """Instantiate the preset; ``name=`` renames the scenario (every builder, not only some)
        and ``tariff=`` replaces its charging tariff."""
        params = _builder_params(self.builder)
        kwargs = dict(self.defaults)
        tariff = kwargs.pop("tariff", None)
        scenario_name = overrides.get("name")
        extra: dict[str, Any] = {}
        for k, v in overrides.items():
            if k == "name":
                continue  # handled below: not every builder takes a name
            if k == "tariff":
                tariff = v  # applied below: no builder takes a tariff
                continue
            if k in params:
                kwargs[k] = v
            else:
                extra[k] = v
        cond, pref = split_overrides(extra)
        sc = self.builder(**kwargs)
        if cond:
            sc = sc.with_conditions(**cond)
        if pref:
            sc = sc.with_preferences(**pref)
        if tariff is not None:
            sc = sc.with_(tariff=_as_tariff(tariff))
        meta = dict(sc.meta)
        meta["preset"] = self.name
        if overrides:
            meta["overrides"] = {k: _json_value(v) for k, v in overrides.items()}
        return replace(sc, name=scenario_name or self.name, meta=meta)


def _builder_params(fn: Callable[..., Any]) -> set[str]:
    sig = inspect.signature(fn)
    return {
        n
        for n, p in sig.parameters.items()
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }


PRESETS: dict[str, Preset] = {}


def register_scenario(
    name: str,
    builder: Callable[..., Scenario],
    description: str,
    *,
    defaults: dict[str, Any] | None = None,
    family: str = "custom",
    tags: tuple[str, ...] = (),
    approx_nodes: int | None = None,
    notes: str = "",
    replace_existing: bool = False,
) -> Preset:
    """Add a preset to the registry (``KeyError`` on duplicates unless replacing)."""
    if name in PRESETS and not replace_existing:
        raise KeyError(f"preset {name!r} already exists")
    preset = Preset(name, description, builder, dict(defaults or {}), family, tuple(tags), approx_nodes, notes)
    PRESETS[name] = preset
    return preset


def list_scenarios(family: str | None = None) -> list[dict[str, Any]]:
    """Metadata of every registered preset (optionally of one family)."""
    return [p.info() for p in PRESETS.values() if family is None or p.family == family]


def get_scenario(name: str, /, **overrides: Any) -> Scenario:
    """Build the preset ``name`` (see the module docstring for overrides).

    ``name`` is positional-only so that ``name="my-run"`` can be passed as an
    override to give the resulting scenario its own name.
    """
    try:
        preset = PRESETS[name]
    except KeyError:
        close = difflib.get_close_matches(name, list(PRESETS), n=3)
        hint = f"; did you mean {close}?" if close else ""
        raise KeyError(f"unknown scenario {name!r}{hint} (available: {sorted(PRESETS)})") from None
    return preset.build(**overrides)


# ------------------------------------------------------------------ built-ins
def _register_builtins() -> None:
    reg = register_scenario
    reg(
        "turkey-karaman-istanbul",
        turkey_scenario,
        "Karaman -> İstanbul (~750 km), the flagship case study: Konya plateau, Afyon/Eskişehir corridors, Marmara motorways.",
        defaults={"origin": "Karaman", "destination": "İstanbul", "initial_soc": 0.8, "temperature_c": 18.0,
                  "departure": "08:00"},
        family="turkey", tags=("case-study", "intercity", "TRY"), approx_nodes=1300,
        notes="Starts in Karaman (1030 m), crosses the Konya plateau and the Afyon/Kütahya uplands and ends at "
              "the Marmara coast; with the default C-SUV two charging stops are needed.",
    )
    reg(
        "turkey-karaman-istanbul-tariff",
        turkey_scenario,
        "Karaman -> İstanbul leaving at 14:00 under the demonstration tariff (80 % rule, 16:00-20:00 peak).",
        defaults={"origin": "Karaman", "destination": "İstanbul", "initial_soc": 0.8, "temperature_c": 18.0,
                  "departure": "14:00", "tariff": DEMO_TARIFF},
        family="turkey", tags=("case-study", "tariff", "TRY"), approx_nodes=1300,
        notes="The flagship trip with illustrative charging prices (DEMO_TARIFF): kWh above 80 % SoC cost "
              "1.3x, and 16:00-20:00 is a peak period (1.15x, 2.3x above 80 %). Leaving at 14:00, the fastest "
              "plan charges twice in the peak; the cheapest plan (objective='cost') takes other chargers and "
              "waits a few minutes at the last one for the 20:00 price change. There is no single best "
              "route: the time-money Pareto front shows the trade-off.",
    )
    reg(
        "turkey-antalya-ankara",
        turkey_scenario,
        "Antalya -> Ankara (~530 km): from the Mediterranean over the Taurus to the Anatolian plateau.",
        defaults={"origin": "Antalya", "destination": "Ankara", "initial_soc": 0.8, "temperature_c": 22.0,
                  "departure": "09:00"},
        family="turkey", tags=("case-study", "intercity", "mountain", "TRY"), approx_nodes=1300,
        notes="Climbs from sea level over Çubuk Beli (1000 m) or Alacabel (1825 m) to the 900-1000 m plateau; "
              "the climb is expensive in energy, the return trip regenerates.",
    )
    reg(
        "turkey-izmir-erzurum",
        turkey_scenario,
        "İzmir -> Erzurum (~1470 km): the long west-east crossing of the plateau and the eastern mountains.",
        defaults={"origin": "İzmir", "destination": "Erzurum", "initial_soc": 0.9, "temperature_c": 12.0,
                  "departure": "06:00"},
        family="turkey", tags=("case-study", "long", "mountain", "TRY"), approx_nodes=1300,
        notes="Long and mountainous (Sivas-Erzincan section with 2000 m summits, Erzurum at 1890 m); "
              "four to five charging stops and a trip time that spans more than half a day.",
    )
    reg(
        "turkey-istanbul-antalya-winter",
        turkey_scenario,
        "İstanbul -> Antalya (~680 km) at -5 °C: smaller usable capacity, heating load, cold charging.",
        defaults={"origin": "İstanbul", "destination": "Antalya", "initial_soc": 0.9, "temperature_c": -5.0,
                  "departure": "07:00"},
        family="turkey", tags=("case-study", "winter", "TRY"), approx_nodes=1300,
        notes="The battery is pre-conditioned before fast charging; pass preconditioning=False to see the "
              "cold-charging derating as well.",
    )
    reg(
        "turkey-karaman-mersin",
        turkey_scenario,
        "Karaman -> Mersin: motorway (273 km) versus the Sertavul pass road (254 km); the best route depends on the objective.",
        defaults={"origin": "Karaman", "destination": "Mersin", "initial_soc": 0.5, "temperature_c": 20.0,
                  "departure": "10:00"},
        family="turkey", tags=("case-study", "regeneration", "mountain", "TRY"), approx_nodes=1300,
        notes="The fastest road is the Ereğli-Pozantı motorway (273 km, about 3 h). The Sertavul pass road via "
              "Mut and Silifke (1610 m, 254 km) is slower but its long descent to the sea regenerates energy. "
              "At 50 % charge the time-optimal plan takes the motorway with one charging stop; with "
              "objective='cost' the planner takes the pass road and hardly charges.",
    )
    reg(
        "turkey-istanbul-ankara-rush",
        turkey_scenario,
        "İstanbul -> Ankara (~450 km) leaving at 14:30 into the afternoon queues of the O-4 service areas.",
        defaults={"origin": "İstanbul", "destination": "Ankara", "initial_soc": 0.55, "temperature_c": 20.0,
                  "departure": "14:30"},
        family="turkey", tags=("case-study", "queues", "TRY"), approx_nodes=1300,
        notes="The busiest corridor: its motorway service areas reach 30+ minute queues around 17:30. A "
              "planner that ignores queues walks into about 50 minutes of waiting; the queue-aware plan "
              "waits less than 10 minutes by choosing other sites and charge levels (about 40 minutes saved).",
    )
    reg(
        "turkey-ankara-adana-evening",
        turkey_scenario,
        "Ankara -> Adana (~510 km) leaving at 15:00 along the O-21 corridor.",
        defaults={"origin": "Ankara", "destination": "Adana", "initial_soc": 0.6, "temperature_c": 25.0,
                  "departure": "15:00"},
        family="turkey", tags=("case-study", "intercity", "TRY"), approx_nodes=1300,
        notes="Central Anatolia to the Cilician plain over the Taurus (Gülek); queues are moderate on this "
              "corridor, so queue awareness changes little here (compare turkey-istanbul-ankara-rush).",
    )
    reg(
        "corridor",
        corridor_scenario,
        "600 km motorway, eight side stations (22-350 kW), a 1500 m pass and a valley bypass: the partial-charging instance.",
        family="synthetic", tags=("didactic", "partial-charging"), approx_nodes=170,
        notes="Stations differ in power (22-350 kW), price (0.35-0.79 EUR/kWh) and afternoon queues, and the "
              "pass makes the energy needed before the climb larger than after it: the optimal plan charges "
              "different amounts at different stops, so how much to charge matters, not only where.",
    )
    reg(
        "corridor-winter",
        corridor_scenario,
        "The corridor at -5 °C: smaller usable battery, heating, colder charging.",
        defaults={"temperature_c": -5.0, "initial_soc": 1.0},
        family="synthetic", tags=("didactic", "winter"), approx_nodes=170,
    )
    reg(
        "country-small",
        country_scenario,
        "Synthetic country, 25 cities in 500 km, ~2 000 nodes.",
        defaults={"n_cities": 25, "region_km": 500.0, "nodes_target": 2000, "seed": 1},
        family="synthetic", tags=("benchmark", "scalability"), approx_nodes=2000,
    )
    reg(
        "country-medium",
        country_scenario,
        "Synthetic country, 40 cities in 700 km, ~10 000 nodes.",
        defaults={"n_cities": 40, "region_km": 700.0, "nodes_target": 10000, "seed": 2},
        family="synthetic", tags=("benchmark", "scalability"), approx_nodes=10000,
    )
    reg(
        "country-large",
        country_scenario,
        "Synthetic country, 60 cities in 900 km, ~40 000 nodes.",
        defaults={"n_cities": 60, "region_km": 900.0, "nodes_target": 40000, "seed": 3},
        family="synthetic", tags=("benchmark", "scalability"), approx_nodes=40000,
    )
    reg(
        "random-geometric",
        random_geometric_scenario,
        "Gabriel-filtered 5-NN random geometric graph, ~3 000 nodes in 250 km.",
        defaults={"n_nodes": 3000, "seed": 0},
        family="synthetic", tags=("benchmark",), approx_nodes=3000,
    )
    reg(
        "grid-small",
        grid_scenario,
        "10 x 10 jittered grid with hills (basic test instance), low initial charge.",
        defaults={"rows": 10, "cols": 10, "seed": 1, "initial_soc": 0.3},
        family="grid", tags=("test",), approx_nodes=100,
    )


_register_builtins()
