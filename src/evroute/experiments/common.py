"""Shared helpers for experiments: OD sampling, timing, CSV/LaTeX output."""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..algorithms import plan as run_plan
from ..plan import RoutePlan
from ..scenario import Scenario


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
    if not rows:
        path.write_text("")
        return path
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k)) for k in keys})
    return path


def _fmt(v: Any) -> Any:
    if isinstance(v, float):
        if not math.isfinite(v):
            return ""
        return round(v, 6)
    return v


def write_json(path: Path, obj: Any) -> Path:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=_json_default), encoding="utf-8")
    return path


def _json_default(o: Any) -> Any:
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, Path):
        return str(o)
    return str(o)


def latex_table(
    rows: Sequence[Sequence[Any]],
    header: Sequence[str],
    caption: str,
    label: str,
    align: str | None = None,
) -> str:
    """A ``booktabs`` table ready for ``\\input{}``."""
    align = align or "l" + "r" * (len(header) - 1)
    out = [
        "\\begin{table}[t]",
        "\\centering",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}}",
        "\\small",
        f"\\begin{{tabular}}{{{align}}}",
        "\\toprule",
        " & ".join(header) + " \\\\",
        "\\midrule",
    ]
    for r in rows:
        out.append(" & ".join(_tex(x) for x in r) + " \\\\")
    out += ["\\bottomrule", "\\end{tabular}", "\\end{table}", ""]
    return "\n".join(out)


def _tex(x: Any) -> str:
    s = f"{x:.2f}" if isinstance(x, float) else str(x)
    return s.replace("_", "\\_").replace("%", "\\%").replace("&", "\\&")


def plan_row(plan: RoutePlan, **extra: Any) -> dict[str, Any]:
    """Flat record of a plan for CSV output."""
    st = plan.stats
    row = {
        **extra,
        "algorithm": plan.algorithm,
        "feasible": int(plan.feasible),
        "time_min": plan.total_time_s / 60.0 if plan.feasible else math.nan,
        "drive_min": plan.drive_time_s / 60.0,
        "charge_min": plan.charge_time_s / 60.0,
        "queue_min": (plan.wait_time_s + plan.setup_time_s) / 60.0,
        "distance_km": plan.distance_m / 1000.0,
        "stops": plan.num_stops,
        "energy_kwh": plan.energy_consumed_kwh - plan.energy_regen_kwh,
        "charged_kwh": plan.energy_charged_kwh,
        "money": plan.money,
        "soh_pct": plan.soh_total_pct,
        "gcost": plan.generalized_cost,
        "arrival_soc": plan.arrival_soc,
        "runtime_ms": (st.runtime_s * 1000.0) if st else math.nan,
        "settled": st.settled if st else 0,
        "generated": st.generated if st else 0,
        # "limit": the search hit its label budget, so "infeasible" means "not solved", not "no route"
        "status": str((st.extra or {}).get("status", "")) if st else "",
    }
    return row


def timed_plan(scenario: Scenario, algorithm: str, **options: Any) -> RoutePlan:
    return run_plan(scenario, algorithm, **options)


def sample_od_pairs(
    scenario: Scenario,
    count: int,
    seed: int,
    *,
    min_km: float = 250.0,
    named_only: bool = True,
) -> list[tuple[int, int]]:
    """Random origin/destination pairs that are far enough apart."""
    net = scenario.network
    rng = random.Random(seed)
    nodes = sorted(net.node_name) if named_only and net.node_name else list(net.nodes())
    pairs: list[tuple[int, int]] = []
    tries = 0
    while len(pairs) < count and tries < count * 200:
        tries += 1
        a, b = rng.sample(nodes, 2)
        if net.euclidean(a, b) >= min_km * 1000.0 and (a, b) not in pairs:
            pairs.append((a, b))
    return pairs


def describe(values: Iterable[float]) -> dict[str, float]:
    v = [x for x in values if x is not None and math.isfinite(x)]
    if not v:
        return {"n": 0}
    v.sort()
    return {
        "n": len(v),
        "mean": statistics.fmean(v),
        "median": statistics.median(v),
        "p95": v[min(len(v) - 1, int(0.95 * len(v)))],
        "max": v[-1],
        "min": v[0],
    }


def safe_plan(scenario: Scenario, algorithm: str, **options: Any) -> RoutePlan | None:
    try:
        return run_plan(scenario, algorithm, **options)
    except Exception as exc:  # pragma: no cover - reported, experiment continues
        print(f"  ! {algorithm} failed: {exc}")
        return None
