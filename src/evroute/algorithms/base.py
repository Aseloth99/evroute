"""Common interface for all route planners."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ..plan import RoutePlan
from ..scenario import PreparedScenario, Scenario


class Planner(ABC):
    """Base class: subclasses implement :meth:`_plan`.

    Class attributes describe the method for reports, the CLI and the web
    viewer.  ``exact`` states the optimality guarantee honestly: ``"exact"``
    (optimal for the model), ``"grid"`` (optimal for charging targets on the
    ``charge_step_soc`` grid), or ``"heuristic"``.
    """

    name: ClassVar[str] = ""
    title: ClassVar[str] = ""
    exact: ClassVar[str] = "heuristic"
    objectives: ClassVar[tuple[str, ...]] = ("time",)
    description: ClassVar[str] = ""
    #: Names of the keyword options the planner reads.  Any other name raises
    #: :class:`ValueError`, so a misspelt option never silently runs default
    #: settings; ``None`` skips the check.
    OPTIONS: ClassVar[tuple[str, ...] | None] = None

    def __init__(self, **options: Any) -> None:
        if self.OPTIONS is not None:
            unknown = sorted(set(options) - set(self.OPTIONS))
            if unknown:
                raise ValueError(f"unknown option(s) {unknown} for {self.name!r}; choose from {self.OPTIONS}")
        self.options = options

    def plan(self, scenario: Scenario | PreparedScenario, *, trace: bool = False) -> RoutePlan:
        prepared = scenario.prepare() if isinstance(scenario, Scenario) else scenario
        t0 = time.perf_counter()
        result = self._plan(prepared, trace=trace)
        elapsed = time.perf_counter() - t0
        if result.stats is not None:
            result.stats.algorithm = self.name
            if result.stats.runtime_s == 0.0:
                result.stats.runtime_s = elapsed
        result.algorithm = self.name
        return result

    @abstractmethod
    def _plan(self, prepared: PreparedScenario, *, trace: bool) -> RoutePlan:
        """Compute a plan; must return an evaluated :class:`RoutePlan`."""

    @classmethod
    def info(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "title": cls.title,
            "exact": cls.exact,
            "objectives": list(cls.objectives),
            "description": cls.description,
        }
