"""Figure bundle for a single scenario (map, SoC profile, time breakdown)."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..plan import RoutePlan
from ..scenario import Scenario


def case_study_figures(
    scenario: Scenario,
    outdir: Path,
    *,
    plans: Sequence[RoutePlan] | None = None,
    algorithms: Sequence[str] | None = None,
) -> list[Path]:
    """Write the standard figure set for ``scenario`` into ``outdir``."""
    from ..algorithms import available, plan as run_plan
    from ..viz import figures as F

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    if plans is None:
        plans = []
        for a in algorithms or available():
            try:
                plans.append(run_plan(scenario, a, trace=True))
            except Exception as exc:  # keep going
                print(f"  ! {a}: {exc}")
    routed = [p for p in plans if p.edges]
    written: list[Path] = []
    jobs = [
        ("fig_map", lambda: F.plot_map(scenario, routed)),
        ("fig_soc", lambda: F.plot_soc_profile(routed, scenario)),
        ("fig_time", lambda: F.plot_time_breakdown([p for p in routed if p.feasible])),
    ]
    if hasattr(F, "plot_search_space"):
        traced = [p for p in routed if p.trace is not None and p.algorithm in ("bls", "bls-astar", "cfp", "cfp-astar")]
        if len(traced) >= 2:
            jobs.append(("fig_search", lambda: F.plot_search_space(scenario, traced[:4])))
    from ..viz import style

    for name, job in jobs:
        try:
            res = job()
            fig = res[0] if isinstance(res, tuple) else res
            written += style.save(fig, outdir / name, close=True)
        except Exception as exc:
            print(f"  ! {name}: {exc}")
    return written
