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
  ``reserve_soc``, ``speed_factors``, ...).

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

from ..scenario import Scenario
from .basic import grid_scenario
from .synthetic import corridor_scenario, country_scenario, random_geometric_scenario, split_overrides
from .turkey import turkey_scenario

__all__ = ["Preset", "PRESETS", "get_scenario", "list_scenarios", "register_scenario"]


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

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "family": self.family,
            "tags": list(self.tags),
            "approx_nodes": self.approx_nodes,
            "defaults": dict(self.defaults),
        }

    def build(self, **overrides: Any) -> Scenario:
        params = _builder_params(self.builder)
        kwargs = dict(self.defaults)
        extra: dict[str, Any] = {}
        for k, v in overrides.items():
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
        meta = dict(sc.meta)
        meta["preset"] = self.name
        if overrides:
            meta["overrides"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in overrides.items()}
        return replace(sc, name=overrides.get("name") or self.name, meta=meta)


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
    replace_existing: bool = False,
) -> Preset:
    """Add a preset to the registry (``KeyError`` on duplicates unless replacing)."""
    if name in PRESETS and not replace_existing:
        raise KeyError(f"preset {name!r} already exists")
    preset = Preset(name, description, builder, dict(defaults or {}), family, tuple(tags), approx_nodes)
    PRESETS[name] = preset
    return preset


def list_scenarios(family: str | None = None) -> list[dict[str, Any]]:
    """Metadata of every registered preset (optionally of one family)."""
    return [p.info() for p in PRESETS.values() if family is None or p.family == family]


def get_scenario(name: str, **overrides: Any) -> Scenario:
    """Build the preset ``name`` (see the module docstring for overrides)."""
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
        "Karaman -> İstanbul (~750 km): Konya plateau, Afyon/Eskişehir or Ankara corridors, "
        "busy Marmara motorways. The case study's flagship trip.",
        defaults={"origin": "Karaman", "destination": "İstanbul", "initial_soc": 0.8, "temperature_c": 18.0,
                  "departure": "08:00"},
        family="turkey", tags=("case-study", "intercity", "TRY"), approx_nodes=1300,
    )
    reg(
        "turkey-antalya-ankara",
        turkey_scenario,
        "Antalya -> Ankara (~530 km): climb from the Mediterranean coast over the Taurus "
        "(Çubuk Beli or Alacabel) to the 900-1000 m Anatolian plateau.",
        defaults={"origin": "Antalya", "destination": "Ankara", "initial_soc": 0.8, "temperature_c": 22.0,
                  "departure": "09:00"},
        family="turkey", tags=("case-study", "intercity", "mountain", "TRY"), approx_nodes=1300,
    )
    reg(
        "turkey-izmir-erzurum",
        turkey_scenario,
        "İzmir -> Erzurum (~1470 km): the long west-east crossing over the plateau and the "
        "eastern mountains (Sivas-Erzincan, 2000 m summits, Erzurum at 1890 m). Several stops.",
        defaults={"origin": "İzmir", "destination": "Erzurum", "initial_soc": 0.9, "temperature_c": 12.0,
                  "departure": "06:00"},
        family="turkey", tags=("case-study", "long", "mountain", "TRY"), approx_nodes=1300,
    )
    reg(
        "turkey-istanbul-antalya-winter",
        turkey_scenario,
        "İstanbul -> Antalya (~680 km) at -5 °C: smaller usable capacity and heating load "
        "(battery pre-conditioned; pass preconditioning=False for cold-charging derating).",
        defaults={"origin": "İstanbul", "destination": "Antalya", "initial_soc": 0.9, "temperature_c": -5.0,
                  "departure": "07:00"},
        family="turkey", tags=("case-study", "winter", "TRY"), approx_nodes=1300,
    )
    reg(
        "turkey-karaman-mersin",
        turkey_scenario,
        "Karaman -> Mersin: the fastest road (Ereğli-Pozantı motorway, ~275 km) versus the shorter "
        "Sertavul pass road (1610 m, ~235 km) whose long descent to the sea regenerates energy; "
        "with 50 % charge only the pass road avoids a charging stop.",
        defaults={"origin": "Karaman", "destination": "Mersin", "initial_soc": 0.5, "temperature_c": 20.0,
                  "departure": "10:00"},
        family="turkey", tags=("case-study", "regeneration", "mountain", "TRY"), approx_nodes=1300,
    )
    reg(
        "turkey-ankara-adana-evening",
        turkey_scenario,
        "Ankara -> Adana (~510 km) leaving at 15:00 so that charging falls into the "
        "afternoon queue peak of the O-21 corridor: shows the value of queue awareness.",
        defaults={"origin": "Ankara", "destination": "Adana", "initial_soc": 0.6, "temperature_c": 25.0,
                  "departure": "15:00"},
        family="turkey", tags=("case-study", "queues", "TRY"), approx_nodes=1300,
    )
    reg(
        "corridor",
        corridor_scenario,
        "600 km motorway with eight side stations (22-350 kW, 0.35-0.79 EUR/kWh), a 1500 m pass "
        "and a valley bypass: the didactic partial-charging instance.",
        family="synthetic", tags=("didactic", "partial-charging"), approx_nodes=170,
    )
    reg(
        "corridor-winter",
        corridor_scenario,
        "The corridor at -5 °C (smaller usable battery, heating, colder charging).",
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
