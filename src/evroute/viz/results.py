"""Interactive results panel: every experiment of a run in one offline HTML page.

The experiments runner (:mod:`evroute.experiments.runner`) writes one folder
per experiment (``results.csv``, ``summary.json``, sometimes other CSV files)
and a ``report.json`` at the top.  This module reads such a folder, analyses
it in pure Python (key numbers, one computed finding per experiment and a set
of *exactness checks* that must hold for exact algorithms) and renders a
single self-contained dashboard: hand-written SVG charts with tooltips, a
table view and a CSV download for every section, Turkish and English, light
and dark theme.  Nothing is loaded from the network.

Usage::

    python -m evroute.viz.results results/ -o results/results.html --lang en

    from evroute.viz.results import build_results_html
    build_results_html("results", "results/results.html", lang="tr")

Missing or failed experiments are shown as such; unknown experiment folders
get a generic table section, so the panel works for quick runs, full runs and
anything in between.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .export import ALGORITHM_COLORS, SHORT_TITLES, algorithm_color, dumps_for_html

__all__ = [
    "FORMAT",
    "SECTION_IDS",
    "analyze_results",
    "build_results_html",
    "load_results",
    "render_results_html",
    "results_payload",
]

#: Contract identifier written into every payload.
FORMAT = "evroute-results/1"

#: ``web/`` (viewer assets) and ``web/results/`` (this dashboard).
WEB_DIR = Path(__file__).resolve().parent / "web"
PANEL_DIR = WEB_DIR / "results"

#: Stylesheets inlined in this order, relative to :data:`PANEL_DIR`.  The
#: design tokens are the viewer's own ``theme.css`` (one source of truth).
CSS_FILES: tuple[str, ...] = ("../css/theme.css", "results.css")

#: Scripts inlined in this order; ``util.js`` is the viewer's helper module.
JS_FILES: tuple[str, ...] = ("../js/util.js", "js/i18n.js", "js/charts.js", "js/sections.js", "js/app.js")

#: Known experiments in presentation order (the runner's order).
SECTION_IDS: tuple[str, ...] = (
    "case-studies",
    "scalability",
    "optimality-gap",
    "grid-resolution",
    "temperature",
    "queue-awareness",
    "pareto",
    "robustness",
    "eco-speed",
    "vehicles",
    "tariffs",
)

#: Algorithms in a fixed order (legends, tables); colours follow the entity.
ALGO_ORDER: tuple[str, ...] = (
    "naive", "greedy", "station-graph", "genetic", "bls", "bls-astar", "cfp", "cfp-astar", "pareto",
)
EXACT_ALGOS = frozenset({"bls", "bls-astar", "cfp", "cfp-astar"})
HEURISTIC_ALGOS = ("naive", "greedy", "station-graph", "genetic")

#: Turkish titles of the baselines (the exact methods keep their acronyms);
#: identical to the viewer's ``ALGO_TITLE``.
ALGO_TITLES_TR: dict[str, str] = {
    "naive": "Naif", "greedy": "Açgözlü", "station-graph": "İstasyon grafı", "genetic": "Genetik",
}

#: Case colours ``(light, dark)``: a categorical palette that does not reuse
#: the algorithm hues (validated for colour-vision deficiency on both themes).
CASE_COLORS: tuple[tuple[str, str], ...] = (
    ("#4F46E5", "#7579F0"),
    ("#D97706", "#C97812"),
    ("#0891B2", "#14A0BD"),
    ("#BE185D", "#DB4F88"),
)
#: Warm (pre-conditioned battery / mild weather) and cold series.
WARM_COLOR = ("#E8590C", "#E2691F")
COLD_COLOR = ("#1C7ED6", "#3D97E8")

#: Charging tariffs of the ``tariffs`` experiment in presentation order: key,
#: title and ``(light, dark)`` colour.  Flat prices are the neutral reference
#: (``None``: the muted text colour, drawn dashed); the three priced tariffs
#: are a categorical set validated all-pairs for colour-vision deficiency on
#: both panel surfaces and kept clear of the algorithm hues.
TARIFF_STYLES: tuple[tuple[str, LangText, tuple[str, str] | None], ...] = (
    ("flat", {"tr": "Sabit fiyat", "en": "Flat price"}, None),
    ("tier", {"tr": "%80 kademesi", "en": "80 % tier"}, ("#6250D6", "#9085E9")),
    ("peak", {"tr": "Puant saatleri", "en": "Peak hours"}, ("#E0602A", "#DD6A2C")),
    ("tier+peak", {"tr": "%80 kademesi + puant", "en": "80 % tier + peak"}, ("#C2185B", "#D43D84")),
)

_CURRENCY_SYMBOL = {"TRY": "₺", "EUR": "€", "USD": "$", "GBP": "£"}
_TABLE_MAX_ROWS = 600
_REL = 1e-6
_ABS_MIN = 1e-5  # CSV values are rounded to 6 decimals

_TEMPLATE_DATA_TAG = '<script id="evroute-results" type="application/json">null</script>'
_STYLES_BLOCK = re.compile(r"<!-- evroute:styles -->.*?<!-- /evroute:styles -->", re.S)
_SCRIPTS_BLOCK = re.compile(r"<!-- evroute:scripts -->.*?<!-- /evroute:scripts -->", re.S)

LangText = dict[str, str]


# ============================================================== formatting
def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def fmt_num(x: float | None, digits: int = 1, lang: str = "en") -> str:
    """Locale-aware number: ``1,234.5`` (en) / ``1.234,5`` (tr); true minus sign."""
    if not _finite(x):
        return "–"
    s = f"{abs(x):,.{digits}f}"
    if lang == "tr":
        s = s.translate(str.maketrans({",": ".", ".": ","}))
    neg = x < 0 and round(abs(x), digits) != 0
    return ("−" if neg else "") + s


def fmt_pct(x: float | None, digits: int = 1, lang: str = "en") -> str:
    """``x`` in percent units: ``4.7%`` (en) / ``%4,7`` (tr)."""
    if not _finite(x):
        return "–"
    n = fmt_num(x, digits, lang)
    if lang == "tr":
        return ("−%" + n[1:]) if n.startswith("−") else "%" + n
    return n + "%"


def fmt_factor(x: float | None, lang: str = "en") -> str:
    """Speed-up factor: ``7.0×``, ``12×``."""
    if not _finite(x):
        return "–"
    return fmt_num(x, 1 if x < 10 else 0, lang) + "×"


def fmt_minutes(m: float | None, lang: str = "en") -> str:
    """Duration given in minutes: ``7 s``, ``7.8 min``, ``8 h 05 min`` (Turkish: sn, dk, sa)."""
    if not _finite(m):
        return "–"
    neg = m < 0
    m = abs(m)
    u_s, u_min, u_h = ("sn", "dk", "sa") if lang == "tr" else ("s", "min", "h")
    if 0 < m * 60 < 0.5:
        out = f"<1 {u_s}"
    elif m < 1.0:
        out = f"{round(m * 60)} {u_s}"
    elif m < 60.0:
        out = f"{fmt_num(m, 1 if m < 9.95 else 0, lang)} {u_min}"
    else:
        total = int(round(m))
        out = f"{total // 60} {u_h} {total % 60:02d} {u_min}"
    return ("−" + out) if neg and out[0] != "0" else out


def fmt_money(x: float | None, currency: str | None, lang: str = "en") -> str:
    if not _finite(x):
        return "–"
    sym = _CURRENCY_SYMBOL.get(currency or "", currency or "")
    n = fmt_num(x, 0 if abs(x) >= 100 else 2, lang)
    if not sym:
        return n
    if lang == "tr" or len(sym) > 1:
        return f"{n} {sym}"
    return f"{sym}{n}"


def fmt_temp(t: float | None, lang: str = "en") -> str:
    if not _finite(t):
        return "–"
    return f"{fmt_num(t, 0 if float(t).is_integer() else 1, lang)} °C"


def _both(fn: Callable[[str], str]) -> LangText:
    return {"tr": fn("tr"), "en": fn("en")}


def algo_title(name: str, lang: str = "en") -> str:
    if lang == "tr" and name in ALGO_TITLES_TR:
        return ALGO_TITLES_TR[name]
    return SHORT_TITLES.get(name, name)


# ================================================================= loading
def _parse_value(text: str) -> Any:
    t = text.strip()
    if t == "":
        return None
    if t in ("True", "true"):
        return True
    if t in ("False", "false"):
        return False
    try:
        return int(t)
    except ValueError:
        pass
    try:
        v = float(t)
    except ValueError:
        return t
    return v if math.isfinite(v) else None


def read_csv_text(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Columns and typed rows of a CSV text (numbers, booleans, ``None`` for empty)."""
    if not text.strip():
        return [], []
    reader = csv.DictReader(io.StringIO(text))
    cols = list(reader.fieldnames or [])
    rows = [{k: _parse_value(v if isinstance(v, str) else "") for k, v in r.items() if k is not None} for r in reader]
    return cols, rows


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def load_results(results_dir: str | Path) -> dict[str, Any]:
    """Read an experiments output folder.

    Returns ``{"root", "report", "experiments": {id: {"dir", "files":
    {name: {"text", "columns", "rows"}}, "summary"}}}``; experiments are the
    known ones (in presentation order) followed by any other sub-folder that
    holds a CSV or ``summary.json``.  Nothing is required to exist.
    """
    root = Path(results_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"results folder not found: {root}")
    report = _read_json(root / "report.json")
    if not isinstance(report, dict):
        report = {}
    names: list[str] = [n for n in SECTION_IDS if (root / n).is_dir() or n in report]
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name not in names and not p.name.startswith((".", "_")):
            if any(p.glob("*.csv")) or (p / "summary.json").exists():
                names.append(p.name)
    for n in report:
        if n not in names:
            names.append(n)
    experiments: dict[str, Any] = {}
    for n in names:
        d = root / n
        files: dict[str, Any] = {}
        if d.is_dir():
            for f in sorted(d.glob("*.csv")):
                try:
                    text = f.read_text(encoding="utf-8-sig")  # a spreadsheet may have re-saved it with a BOM
                except OSError:
                    continue
                try:
                    cols, rows = read_csv_text(text)
                except csv.Error:
                    cols, rows = [], []
                files[f.name] = {"text": text, "columns": cols, "rows": rows}
        summary = _read_json(d / "summary.json") if d.is_dir() else None
        experiments[n] = {"dir": d, "files": files, "summary": summary}
    return {"root": root, "report": report, "experiments": experiments}


# ============================================================ small maths
def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _REL * max(abs(a), abs(b)) + _ABS_MIN


def _leq(a: float, b: float) -> bool:
    return a <= b + _REL * abs(b) + _ABS_MIN


def _mean(xs: Iterable[float]) -> float | None:
    v = [x for x in xs if _finite(x)]
    return statistics.fmean(v) if v else None


def _geomean(xs: Iterable[float]) -> float | None:
    v = [x for x in xs if _finite(x) and x > 0]
    return math.exp(statistics.fmean(math.log(x) for x in v)) if v else None


def _quantile(sorted_v: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy's default) of a sorted list."""
    if len(sorted_v) == 1:
        return sorted_v[0]
    pos = q * (len(sorted_v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (pos - lo)


def box_stats(values: Iterable[float]) -> dict[str, Any]:
    """Tukey box statistics: quartiles, whiskers at 1.5 IQR (inside the data), mean."""
    v = sorted(x for x in values if _finite(x))
    if not v:
        return {"n": 0}
    q1, med, q3 = _quantile(v, 0.25), _quantile(v, 0.5), _quantile(v, 0.75)
    iqr = q3 - q1
    lo = min(x for x in v if x >= q1 - 1.5 * iqr)
    hi = max(x for x in v if x <= q3 + 1.5 * iqr)
    return {"n": len(v), "min": v[0], "q1": q1, "median": med, "q3": q3, "max": v[-1],
            "mean": statistics.fmean(v), "whisker_lo": lo, "whisker_hi": hi,
            "p95": v[min(len(v) - 1, int(0.95 * len(v)))]}


def _loglog_slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if _finite(x) and _finite(y) and x > 0 and y > 0]
    if len(pts) < 2:
        return None
    mx = statistics.fmean(p[0] for p in pts)
    my = statistics.fmean(p[1] for p in pts)
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    if sxx <= 0:
        return None
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx


def _feasible(row: Mapping[str, Any]) -> bool:
    v = row.get("feasible")
    if v is None:
        return _finite(row.get("time_min"))
    return bool(v) and v not in ("0", "False")


def _limit(row: Mapping[str, Any]) -> bool:
    """The search ran out of its label budget: "not solved", not "no route exists"."""
    return str(row.get("status") or "").strip().lower() == "limit"


def _num(row: Mapping[str, Any], key: str) -> float | None:
    v = row.get(key)
    return float(v) if _finite(v) else None


def _rows(exp: Mapping[str, Any] | None, name: str = "results.csv") -> list[dict[str, Any]]:
    if not exp:
        return []
    f = exp["files"].get(name)
    return list(f["rows"]) if f else []


def _ordered_algos(names: Iterable[str]) -> list[str]:
    seen = list(dict.fromkeys(names))
    return [a for a in ALGO_ORDER if a in seen] + [a for a in seen if a not in ALGO_ORDER]


# =============================================================== metadata
def _preset_info(name: str) -> dict[str, Any] | None:
    try:
        from ..generators.presets import PRESETS
    except Exception:  # noqa: BLE001 - metadata is optional
        return None
    p = PRESETS.get(name)
    return p.info() if p else None


def case_meta(name: str) -> dict[str, Any]:
    """Title (``Karaman → İstanbul``), short facts and currency of a preset case."""
    info = _preset_info(name) or {}
    d = info.get("defaults") or {}
    if d.get("origin") and d.get("destination"):
        title = f"{d['origin']} → {d['destination']}"
    else:
        parts = [p for p in name.replace("turkey-", "").split("-") if p]
        title = " → ".join(p.capitalize() for p in parts[:2]) if len(parts) >= 2 else name
    sub: dict[str, str] = {}
    for lang in ("tr", "en"):
        bits = []
        if _finite(d.get("temperature_c")):
            bits.append(fmt_temp(d["temperature_c"], lang))
        if d.get("departure"):
            bits.append(str(d["departure"]))
        if _finite(d.get("initial_soc")):
            bits.append(fmt_pct(100 * d["initial_soc"], 0, lang))
        sub[lang] = " · ".join(bits)
    tags = info.get("tags") or []
    currency = next((t for t in tags if isinstance(t, str) and len(t) == 3 and t.isupper()), None)
    if currency is None and name.startswith("turkey-"):
        currency = "TRY"
    winter = "winter" in tags or name.endswith("-winter")
    return {"title": title, "subtitle": sub, "currency": currency, "winter": winter,
            "description": info.get("description") or ""}


def vehicle_meta(key: str) -> dict[str, Any]:
    try:
        from ..vehicle import VEHICLES
    except Exception:  # noqa: BLE001
        VEHICLES = {}
    v = VEHICLES.get(key) if isinstance(VEHICLES, dict) else None
    if v is None:
        return {"name": key}
    out: dict[str, Any] = {"name": getattr(v, "name", key)}

    def value(obj: Any, attr: str) -> float | None:
        val = getattr(obj, attr, None)
        try:
            val = val() if callable(val) else val
        except Exception:  # noqa: BLE001 - metadata only
            return None
        return float(val) if _finite(val) else None

    for name, obj, attr in (("battery_kwh", v, "battery_kwh"), ("usable_kwh", v, "usable_capacity_kwh"),
                            ("dc_peak_kw", getattr(v, "dc_curve", None), "peak_kw")):
        val = value(obj, attr) if obj is not None else None
        if val is not None:
            out[name] = val
    return out


# ================================================================== checks
class _Checks:
    """Exactness checks: statements that must hold for exact algorithms."""

    TITLES: dict[str, LangText] = {
        "bls-astar-equals-bls": {"tr": "BLS-A* ile BLS aynı optimumu buluyor",
                                 "en": "BLS-A* finds the same optimum as BLS"},
        "cfp-astar-equals-cfp": {"tr": "CFP-A* ile CFP aynı optimumu buluyor (sabit bekleme)",
                                 "en": "CFP-A* finds the same optimum as CFP (static waits)"},
        "cfp-lower-bound": {"tr": "Hiçbir plan sürekli şarj optimumunu (CFP) geçmiyor",
                            "en": "No plan beats the continuous-charging optimum (CFP)"},
        "breakpoints-help": {"tr": "Şarj eğrisi kırılma noktalarını eklemek planı kötüleştirmiyor",
                             "en": "Adding charging-curve breakpoints never makes the plan worse"},
        "queue-aware-optimal": {"tr": "Kuyruk-farkında plan, kuyruk-kör plandan kötü değil",
                                "en": "The queue-aware plan is never worse than the queue-blind one"},
        "eco-not-worse": {"tr": "Eko hız seçenekleri yolculuğu uzatmıyor",
                          "en": "Eco speed options never lengthen the trip"},
        "safety-monotone": {"tr": "Planlanan süre güvenlik katsayısıyla azalmıyor",
                            "en": "Planned time never decreases with the safety factor"},
        "pareto-nondominated": {"tr": "Hiçbir Pareto planı bir diğerinden her ölçütte açıkça daha iyi değil",
                                "en": "No Pareto front member is clearly better than another in every criterion"},
        "tariff-time-optimal": {"tr": "Tarifeden bağımsız: hiçbir plan zaman-optimal plandan hızlı değil ve zaman-optimal plan hiç beklemiyor",
                                "en": "Under every tariff no plan is faster than the time-optimal one, which never holds"},
        "tariff-cost-gcost": {"tr": "Maliyet-optimal planın genelleştirilmiş maliyeti zaman-optimal planınkinden yüksek değil",
                              "en": "The cost-optimal plan's generalised cost is no higher than the time-optimal plan's"},
        "tariff-cost-money": {"tr": "Maliyet-optimal plan şarj ve batarya yıpranması için zaman-optimal plandan fazla ödemiyor",
                              "en": "The cost-optimal plan pays no more for charging and battery wear than the time-optimal plan"},
        "tariff-front-extremes": {"tr": "Cephenin en hızlı planı zaman optimumu; tam bir cephede maliyet-optimal plandan yavaş ve pahalı olmayan bir plan var",
                                  "en": "The front's fastest plan is the time optimum; a complete front has a plan no slower and no dearer than the cost optimum"},
    }

    def __init__(self) -> None:
        self.groups: dict[str, dict[str, Any]] = {}

    def add(self, cid: str, ok: bool, section: str, detail: str) -> None:
        g = self.groups.setdefault(cid, {"id": cid, "title": self.TITLES.get(cid, {"tr": cid, "en": cid}),
                                         "passed": 0, "total": 0, "sections": [], "failures": []})
        g["total"] += 1
        if section not in g["sections"]:
            g["sections"].append(section)
        if ok:
            g["passed"] += 1
        elif len(g["failures"]) < 25:
            g["failures"].append(detail)

    def summary(self) -> dict[str, Any]:
        groups = [self.groups[k] for k in self.TITLES if k in self.groups]
        groups += [g for k, g in self.groups.items() if k not in self.TITLES]
        return {"passed": sum(g["passed"] for g in groups), "total": sum(g["total"] for g in groups), "groups": groups}


# ================================================================ sections
def _sec_case_studies(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    case_ids = list(dict.fromkeys(str(r.get("case")) for r in rows if r.get("case") is not None))
    cases = []
    excess: dict[str, list[float]] = {}
    strands: dict[str, int] = {}
    exact_best = 0
    for cid in case_ids:
        sel = [r for r in rows if str(r.get("case")) == cid]
        algos = _ordered_algos(str(r.get("algorithm")) for r in sel)
        by_algo = {str(r.get("algorithm")): r for r in sel}
        feas_times = [_num(r, "time_min") for r in sel if _feasible(r) and _num(r, "time_min") is not None]
        best = min(feas_times) if feas_times else None
        out_rows = []
        for a in algos:
            r = by_algo[a]
            t = _num(r, "time_min") if _feasible(r) else None
            ex = (t - best) / best * 100.0 if t is not None and best else None
            out_rows.append({
                "algorithm": a, "feasible": _feasible(r), "time_min": t,
                "drive_min": _num(r, "drive_min"), "queue_min": _num(r, "queue_min"),
                "charge_min": _num(r, "charge_min"), "stops": _num(r, "stops"),
                "money": _num(r, "money"), "runtime_ms": _num(r, "runtime_ms"),
                "settled": _num(r, "settled"), "energy_kwh": _num(r, "energy_kwh"),
                "charged_kwh": _num(r, "charged_kwh"), "distance_km": _num(r, "distance_km"),
                "arrival_soc": _num(r, "arrival_soc"), "soh_pct": _num(r, "soh_pct"),
                "excess_pct": ex, "best": ex is not None and ex <= 1e-6 * 100,
            })
            if a in HEURISTIC_ALGOS:
                if ex is not None:
                    excess.setdefault(a, []).append(ex)
                elif not _feasible(r):
                    strands[a] = strands.get(a, 0) + 1
        if best is not None and any(o["best"] and o["algorithm"] in EXACT_ALGOS for o in out_rows):
            exact_best += 1
        if "bls" in by_algo and "bls-astar" in by_algo:
            b, s = by_algo["bls"], by_algo["bls-astar"]
            tb, ts = _num(b, "time_min"), _num(s, "time_min")
            ok = (_feasible(b) == _feasible(s)) and (not _feasible(b) or (tb is not None and ts is not None and _close(tb, ts)))
            checks.add("bls-astar-equals-bls", ok, "case-studies", f"{cid}: BLS {tb} vs BLS-A* {ts} min")
        meta = case_meta(cid)
        viewer = None
        vp = Path(exp["dir"]) / cid / "viewer.html"
        if vp.exists() and ctx.get("out_dir") is not None:
            viewer = os.path.relpath(vp, ctx["out_dir"]).replace(os.sep, "/")
        cases.append({"id": cid, "title": meta["title"], "subtitle": meta["subtitle"], "currency": meta["currency"],
                      "best_min": best, "rows": out_rows, "viewer": viewer,
                      "distance_km": next((o["distance_km"] for o in out_rows if o["feasible"]), None)})
    n = len(cases)
    heur = sorted(excess, key=lambda a: _mean(excess[a]) or 0.0)

    def finding(lang: str) -> str:
        if lang == "tr":
            s = f"En hızlı planı kesin bir algoritma buluyor: {exact_best}/{n} vaka"
        else:
            s = f"An exact algorithm finds the fastest plan in {exact_best} of {n} case{'s' if n != 1 else ''}"
        parts = []
        for a in reversed(heur):
            m = _mean(excess[a]) if a != "naive" else None
            if m is not None:
                parts.append(f"{algo_title(a, lang)} +{fmt_pct(m, 1, lang)}")
        if parts:
            s += ("; sezgiseller ortalama daha uzun sürüyor: " if lang == "tr" else "; heuristics take longer on average: ") + ", ".join(parts)
        k = strands.get("naive", 0)
        if "naive" in {o["algorithm"] for c in cases for o in c["rows"]}:
            if lang == "tr":
                s += f"; bataryayı hesaba katmayan naif rota {k}/{n} vakada yolda kalıyor"
            else:
                s += f"; the battery-unaware naive route strands in {k} of {n}"
        return s + "."

    return {"data": {"cases": cases}, "finding": _both(finding),
            "facts": {"cases": n, "exact_best": exact_best, "mean_excess_pct": {a: _mean(v) for a, v in excess.items()},
                      "strands": strands}}


def _sec_scalability(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    algos = _ordered_algos(str(r.get("algorithm")) for r in rows)
    sizes = sorted({int(r["nodes"]) for r in rows if _finite(r.get("nodes"))})
    series = []
    budget: list[dict[str, Any]] = []  # searches stopped by their label budget, per algorithm and size
    for a in algos:
        pts = []
        for n in sizes:
            at = [r for r in rows if r.get("algorithm") == a and r.get("nodes") == n]
            sel = [r for r in at if _feasible(r)]
            unsolved = sum(_limit(r) for r in at)
            if unsolved:
                budget.append({"algorithm": a, "nodes": n, "unsolved": unsolved, "total": len(at)})
            rt = [x for x in (_num(r, "runtime_ms") for r in sel) if x is not None]
            st = [x for x in (_num(r, "settled") for r in sel) if x is not None]
            if not rt:
                continue
            pts.append({"nodes": n, "n": len(rt), "unsolved": unsolved, "runtime_ms": statistics.fmean(rt), "runtime_min": min(rt),
                        "runtime_max": max(rt), "settled": statistics.fmean(st) if st else None,
                        "settled_min": min(st) if st else None, "settled_max": max(st) if st else None})
        if pts:
            series.append({"algorithm": a, "points": pts,
                           "slope": _loglog_slope([p["nodes"] for p in pts], [p["runtime_ms"] for p in pts])})
    # per instance comparisons
    inst: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for r in rows:
        inst.setdefault((r.get("nodes"), r.get("pair")), {})[str(r.get("algorithm"))] = r
    # A speed-up pairs every instance both algorithms solved, also when their optima
    # differ: such a mismatch is stated next to the speed-up, never hidden by dropping it.
    speed: dict[str, dict[int, list[tuple[float, float, float, float]]]] = {"bls-astar": {}, "cfp-astar": {}}
    mismatches: dict[str, list[int]] = {"bls-astar": [0, 0], "cfp-astar": [0, 0]}  # [failed, compared]
    for (n, pair), by in sorted(inst.items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
        label = f"n={n} pair {pair}"
        for fast, slow, cid in (("bls-astar", "bls", "bls-astar-equals-bls"), ("cfp-astar", "cfp", "cfp-astar-equals-cfp")):
            if fast in by and slow in by and not (_limit(by[fast]) or _limit(by[slow])):
                a, b = by[fast], by[slow]
                ta, tb = _num(a, "time_min"), _num(b, "time_min")
                ok = (_feasible(a) == _feasible(b)) and (not _feasible(a) or (ta is not None and tb is not None and _close(ta, tb)))
                checks.add(cid, ok, "scalability", f"{label}: {slow} {tb} vs {fast} {ta} min")
                mismatches[fast][0] += not ok
                mismatches[fast][1] += 1
                ra, rb = _num(a, "runtime_ms"), _num(b, "runtime_ms")
                sa, sb = _num(a, "settled"), _num(b, "settled")
                if _feasible(a) and _feasible(b) and ra and rb and ra > 0 and isinstance(n, int):
                    speed[fast].setdefault(n, []).append((rb / ra, (sb / sa) if sa and sb and sa > 0 else float("nan"), ra, rb))
        tc = _num(by["cfp"], "time_min") if "cfp" in by and _feasible(by["cfp"]) else None
        for other in ("bls", "bls-astar", "station-graph"):
            if tc is not None and other in by and _feasible(by[other]):
                to = _num(by[other], "time_min")
                if to is not None:
                    checks.add("cfp-lower-bound", _leq(tc, to), "scalability", f"{label}: CFP {tc} > {other} {to} min")
    speedup = []
    for fast, by_n in speed.items():
        # geometric means; the mean runtimes are geometric too, so their ratio is the speed-up
        pts = [{"nodes": n, "speedup": _geomean(x[0] for x in v), "settled_ratio": _geomean(x[1] for x in v), "n": len(v),
                "runtime_ms": _geomean(x[2] for x in v), "baseline_runtime_ms": _geomean(x[3] for x in v)}
               for n, v in sorted(by_n.items())]
        if pts:
            speedup.append({"algorithm": fast, "baseline": fast.replace("-astar", ""), "points": pts})
    # The headline compares both pairs at one size: the largest that has both (a full
    # run skips BLS on the largest networks); without a common size each keeps its own.
    common = set.intersection(*({p["nodes"] for p in s["points"]} for s in speedup)) if speedup else set()
    ref_n = max(common) if common else None
    sp = {s["algorithm"]: next((p for p in s["points"] if p["nodes"] == ref_n), s["points"][-1]) for s in speedup}
    bad = {k: v for k, v in mismatches.items() if v[0]}
    nmax = max(sizes) if sizes else None

    def finding(lang: str) -> str:
        s = finding_core(lang)
        by_alg: dict[str, list[dict[str, Any]]] = {}
        for x in budget:
            by_alg.setdefault(x["algorithm"], []).append(x)
        for a, xs in by_alg.items():
            if lang == "tr":
                at = ", ".join(f"{fmt_num(x['nodes'], 0, lang)} düğümde {x['total']} örneğin {x['unsolved']} tanesinde" for x in xs)
                s += f" {algo_title(a, lang)} etiket bütçesini {at} aştı (bunlar çözümsüz sayılmaz; hızlanmalar iki algoritmanın da çözdüğü örneklerden hesaplanır)."
            else:
                at = ", ".join(f"{x['unsolved']} of {x['total']} instances at {fmt_num(x['nodes'], 0, lang)} nodes" for x in xs)
                s += f" {algo_title(a, lang)} ran out of its label budget on {at} (not counted as infeasible; speed-ups use the instances both solved)."
        return s

    def finding_core(lang: str) -> str:
        b, c = sp.get("bls-astar"), sp.get("cfp-astar")
        if not b and not c:
            fastest = min(series, key=lambda s: s["points"][-1]["runtime_ms"]) if series else None
            if not fastest:
                return ""
            p = fastest["points"][-1]
            if lang == "tr":
                return f"{fmt_num(p['nodes'], 0, lang)} düğümde en hızlı algoritma {algo_title(fastest['algorithm'], lang)} ({fmt_num(p['runtime_ms'], 0, lang)} ms)."
            return f"At {fmt_num(p['nodes'], 0, lang)} nodes the fastest algorithm is {algo_title(fastest['algorithm'], lang)} ({fmt_num(p['runtime_ms'], 0, lang)} ms)."
        parts = []
        ref = b or c
        c_at = fmt_num(c["nodes"], 0, lang) if b and c and c["nodes"] != b["nodes"] else None
        if lang == "tr":
            head = f"{fmt_num(ref['nodes'], 0, lang)} düğümlü ağda "
            if b:
                parts.append((f"BLS-A* BLS ile karşılaştırıldığında {fmt_factor(b['speedup'], lang)} daha hızlı" if "bls-astar" in bad
                              else f"BLS-A* BLS ile aynı optimumu {fmt_factor(b['speedup'], lang)} daha hızlı buluyor")
                             + (f" ve {fmt_factor(b['settled_ratio'], lang)} daha az etiket kesinleştiriyor" if _finite(b.get("settled_ratio")) else ""))
            if c:
                parts.append(f"CFP-A* ise {c_at + ' düğümde ' if c_at else ''}CFP ile karşılaştırıldığında {fmt_factor(c['speedup'], lang)} daha hızlı")
            s = head + "; ".join(parts) + " (örnek başına hızlanmaların geometrik ortalaması)"
            for fast, (k, m) in bad.items():
                s += f"; ancak {algo_title(fast, lang)} ile {algo_title(fast.replace('-astar', ''), lang)} {m} örneğin {k} tanesinde farklı sonuç buluyor"
            return s + (" (kesinlik denetimlerine bakın)." if bad else ".")
        head = f"At {fmt_num(ref['nodes'], 0, lang)} nodes "
        if b:
            parts.append(f"BLS-A* is {fmt_factor(b['speedup'], lang)} faster than BLS"
                         + (f" and settles {fmt_factor(b['settled_ratio'], lang)} fewer labels" if _finite(b.get("settled_ratio")) else ""))
        if c:
            parts.append(f"CFP-A* is {fmt_factor(c['speedup'], lang)} faster than CFP" + (f" at {c_at} nodes" if c_at else ""))
        s = head + "; ".join(parts) + " (geometric means of per-instance speed-ups)"
        if not bad:
            return s + " — with identical optima."
        return s + "; but " + " and ".join(f"{algo_title(fast, lang)} and {algo_title(fast.replace('-astar', ''), lang)} disagree on {k} of {m} instances"
                                           for fast, (k, m) in bad.items()) + " (see the exactness checks)."

    return {"data": {"series": series, "speedup": speedup, "sizes": sizes, "max_nodes": nmax, "budget": budget,
                     "headline_nodes": (sp.get("bls-astar") or sp.get("cfp-astar") or {}).get("nodes")},
            "finding": _both(finding),
            "facts": {"speedup": {k: v["speedup"] for k, v in sp.items()},
                      "settled_ratio": {k: v["settled_ratio"] for k, v in sp.items()},
                      "nodes": {k: v["nodes"] for k, v in sp.items()},
                      "mismatches": {k: v for k, v in mismatches.items() if v[1]},
                      "slopes": {s["algorithm"]: s["slope"] for s in series}}}


def _sec_optimality_gap(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    algos = _ordered_algos(str(r.get("algorithm")) for r in rows)
    groups = []
    for a in algos:
        sel = [r for r in rows if r.get("algorithm") == a]
        pts = []
        for r in sel:
            g = _num(r, "gap_pct")
            feas = _feasible(r) and g is not None
            pts.append({"pair": r.get("pair"), "gap_pct": g if feas else None, "time_min": _num(r, "time_min"),
                        "opt_min": _num(r, "opt_min"), "stops": _num(r, "stops"), "feasible": feas,
                        "runtime_ms": _num(r, "runtime_ms")})
            if feas:
                checks.add("cfp-lower-bound", g >= -1e-6, "optimality-gap", f"pair {r.get('pair')}: {a} gap {g}%")
        vals = [p["gap_pct"] for p in pts if p["gap_pct"] is not None]
        groups.append({"algorithm": a, "points": pts, "stats": box_stats(vals),
                       "feasible_rate": (sum(p["feasible"] for p in pts) / len(pts)) if pts else None,
                       "infeasible": sum(not p["feasible"] for p in pts),
                       "optimal": sum(1 for v in vals if v <= 1e-6)})
    heur = [g for g in groups if g["stats"].get("n") and g["algorithm"] not in EXACT_ALGOS]
    pairs = len({r.get("pair") for r in rows})

    def finding(lang: str) -> str:
        if not heur:
            g = next((g for g in groups if g["stats"].get("n")), None)
            if not g:
                return ""
            if lang == "tr":
                return f"{algo_title(g['algorithm'], lang)} ortalama {fmt_pct(g['stats']['mean'], 2, lang)} uzakta."
            return f"{algo_title(g['algorithm'], lang)} is {fmt_pct(g['stats']['mean'], 2, lang)} from the optimum on average."
        best = min(heur, key=lambda g: g["stats"]["mean"])
        worst = max(heur, key=lambda g: g["stats"]["mean"])
        wmax = max(heur, key=lambda g: g["stats"]["max"])
        grid = next((g for g in groups if g["algorithm"] == "bls-astar" and g["stats"].get("n")), None)
        if lang == "tr":
            s = (f"{pairs} başlangıç–varış çiftinde sürekli optimuma en yakın sezgisel {algo_title(best['algorithm'], lang)} "
                 f"(ortalama {fmt_pct(best['stats']['mean'], 1, lang)}); {algo_title(worst['algorithm'], lang)} ortalama "
                 f"{fmt_pct(worst['stats']['mean'], 1, lang)}, en kötü durumda {algo_title(wmax['algorithm'], lang)} "
                 f"{fmt_pct(wmax['stats']['max'], 1, lang)} daha uzun sürüyor")
            if grid:
                s += (f"; ızgaralı BLS-A* ortalama yalnızca {fmt_pct(grid['stats']['mean'], 2, lang)}, "
                      f"en çok {fmt_pct(grid['stats']['max'], 2, lang)} uzakta")
            return s + "."
        s = (f"Over {pairs} origin–destination pairs the closest heuristic is {algo_title(best['algorithm'], lang)} "
             f"(mean gap {fmt_pct(best['stats']['mean'], 1, lang)}); {algo_title(worst['algorithm'], lang)} averages "
             f"{fmt_pct(worst['stats']['mean'], 1, lang)} and the worst case is {fmt_pct(wmax['stats']['max'], 1, lang)} "
             f"({algo_title(wmax['algorithm'], lang)})")
        if grid:
            s += (f"; grid-based BLS-A* is {fmt_pct(grid['stats']['mean'], 2, lang)} from the optimum on average "
                  f"(at most {fmt_pct(grid['stats']['max'], 2, lang)})")
        return s + "."

    return {"data": {"groups": groups, "pairs": pairs}, "finding": _both(finding),
            "facts": {"mean_gap_pct": {g["algorithm"]: g["stats"].get("mean") for g in groups},
                      "heuristic_mean_gap_pct": _mean(p["gap_pct"] for g in heur for p in g["points"] if p["gap_pct"] is not None)}}


def _sec_grid_resolution(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    case_ids = list(dict.fromkeys(str(r.get("case")) for r in rows))
    targets = list(dict.fromkeys(str(r.get("targets")) for r in rows))
    series = []
    for ci, cid in enumerate(case_ids):
        for tg in targets:
            sel = sorted((r for r in rows if str(r.get("case")) == cid and str(r.get("targets")) == tg and _finite(r.get("step"))),
                         key=lambda r: -r["step"])
            pts = []
            for r in sel:
                gap = _num(r, "gap_s")
                if gap is not None:
                    tmin = _num(r, "time_min")
                    opt = (tmin * 60.0 - gap) if tmin is not None else None
                    checks.add("cfp-lower-bound", gap >= -(_REL * (opt or 0.0) + 1e-3), "grid-resolution",
                               f"{cid} step {r['step']} {tg}: gap {gap} s")
                pts.append({"step_pct": float(r["step"]) * 100.0, "gap_s": gap, "time_min": _num(r, "time_min"),
                            "runtime_ms": _num(r, "runtime_ms"), "settled": _num(r, "settled"), "stops": _num(r, "stops")})
            if pts:
                series.append({"case": cid, "case_index": ci, "targets": tg, "points": pts})
    for cid in case_ids:
        for r in rows:
            if str(r.get("case")) != cid or r.get("targets") != "grid":
                continue
            twin = next((q for q in rows if str(q.get("case")) == cid and q.get("targets") == "grid+breaks"
                         and q.get("step") == r.get("step")), None)
            if twin and _num(twin, "time_min") is not None and _num(r, "time_min") is not None:
                checks.add("breakpoints-help", _leq(twin["time_min"], r["time_min"]), "grid-resolution",
                           f"{cid} step {r['step']}: grid+breaks {twin['time_min']} > grid {r['time_min']} min")
    cases = [{"id": c, "title": case_meta(c)["title"]} for c in case_ids]
    main_tg = "grid+breaks" if "grid+breaks" in targets else (targets[0] if targets else None)
    main = [s for s in series if s["targets"] == main_tg and len(s["points"]) >= 2]
    coarse = [s["points"][0] for s in main]
    fine = [s["points"][-1] for s in main]

    def finding(lang: str) -> str:
        if not main:
            return ""
        g0 = _mean(p["gap_s"] for p in coarse)
        g1 = _mean(p["gap_s"] for p in fine)
        r0 = _mean(p["runtime_ms"] for p in coarse)
        r1 = _mean(p["runtime_ms"] for p in fine)
        s0, s1 = coarse[0]["step_pct"], fine[0]["step_pct"]
        ratio = (r1 / r0) if r0 and r1 and r0 > 0 else None
        where = (case_meta(main[0]["case"])["title"] if len(main) == 1 else None)
        if lang == "tr":
            s = (f"Şarj ızgarasını {fmt_pct(s0, 1 if s0 < 1 else 0, lang)} adımdan {fmt_pct(s1, 1 if s1 < 1 else 0, lang)} adıma inceltmek "
                 f"sürekli optimuma (CFP) farkı ortalama {fmt_num(g0, 0 if (g0 or 0) >= 10 else 1, lang)} sn'den "
                 f"{fmt_num(g1, 0 if (g1 or 0) >= 10 else 1, lang)} sn'ye indiriyor")
            if ratio:
                s += f"; bedeli {fmt_factor(ratio, lang)} hesaplama süresi"
            return s + (f" ({where})." if where else ".")
        s = (f"Refining the charging grid from {fmt_pct(s0, 1 if s0 < 1 else 0, lang)} to {fmt_pct(s1, 1 if s1 < 1 else 0, lang)} SoC "
             f"cuts the mean gap to the continuous optimum (CFP) from {fmt_num(g0, 0 if (g0 or 0) >= 10 else 1, lang)} s to "
             f"{fmt_num(g1, 0 if (g1 or 0) >= 10 else 1, lang)} s")
        if ratio:
            s += f" at {fmt_factor(ratio, lang)} the runtime"
        return s + (f" ({where})." if where else ".")

    return {"data": {"series": series, "cases": cases, "targets": targets}, "finding": _both(finding),
            "facts": {"coarse_gap_s": _mean(p["gap_s"] for p in coarse), "fine_gap_s": _mean(p["gap_s"] for p in fine)}}


def _sec_temperature(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    series = []
    for pre in (1, 0):
        sel = sorted((r for r in rows if int(bool(r.get("preconditioning", 1))) == pre and _finite(r.get("temperature_c"))),
                     key=lambda r: r["temperature_c"])
        pts = [{"temperature_c": float(r["temperature_c"]), "feasible": _feasible(r),
                "time_min": _num(r, "time_min") if _feasible(r) else None, "energy_kwh": _num(r, "energy_kwh"),
                "stops": _num(r, "stops"), "charge_min": _num(r, "charge_min"), "queue_min": _num(r, "queue_min"),
                "charged_kwh": _num(r, "charged_kwh"), "money": _num(r, "money")} for r in sel]
        if pts:
            series.append({"key": "preconditioned" if pre else "cold", "points": pts})
    pre_s = next((s for s in series if s["key"] == "preconditioned"), series[0] if series else None)
    cold_s = next((s for s in series if s["key"] == "cold"), None)
    facts: dict[str, Any] = {}
    if pre_s:
        ok = [p for p in pre_s["points"] if p["time_min"] is not None]
        if ok:
            coldest = ok[0]
            best = min(ok, key=lambda p: p["time_min"])
            facts = {"coldest_c": coldest["temperature_c"], "best_c": best["temperature_c"],
                     "delta_time_min": coldest["time_min"] - best["time_min"],
                     "energy_increase_pct": ((coldest["energy_kwh"] / best["energy_kwh"] - 1) * 100.0)
                     if coldest.get("energy_kwh") and best.get("energy_kwh") else None,
                     "extra_stops": (coldest["stops"] or 0) - (best["stops"] or 0)}
            if cold_s:
                twin = next((p for p in cold_s["points"] if p["temperature_c"] == coldest["temperature_c"] and p["time_min"] is not None), None)
                if twin:
                    facts["cold_battery_penalty_min"] = twin["time_min"] - coldest["time_min"]

    def finding(lang: str) -> str:
        if not facts:
            return ""
        tc, tb = fmt_temp(facts["coldest_c"], lang), fmt_temp(facts["best_c"], lang)
        if lang == "tr":
            s = f"{tc} sıcaklıkta yolculuk {tb} sıcaklığa göre {fmt_minutes(facts['delta_time_min'], lang)} uzun sürüyor"
            if _finite(facts.get("energy_increase_pct")):
                s += f" ve {fmt_pct(facts['energy_increase_pct'], 0, lang)} daha fazla enerji harcıyor"
            if _finite(facts.get("cold_battery_penalty_min")):
                s += f"; batarya ön ısıtılmazsa {fmt_minutes(facts['cold_battery_penalty_min'], lang)} daha kayboluyor"
            return s + "."
        s = f"At {tc} the trip takes {fmt_minutes(facts['delta_time_min'], lang)} longer than at {tb}"
        if _finite(facts.get("energy_increase_pct")):
            s += f" and needs {fmt_pct(facts['energy_increase_pct'], 0, lang)} more energy"
        if _finite(facts.get("cold_battery_penalty_min")):
            s += f"; without battery preconditioning another {fmt_minutes(facts['cold_battery_penalty_min'], lang)} are lost"
        return s + "."

    return {"data": {"series": series}, "finding": _both(finding), "facts": facts}


def _sec_queue(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    case_ids = list(dict.fromkeys(str(r.get("case")) for r in rows))
    hours = sorted({r["departure_h"] for r in rows if _finite(r.get("departure_h"))})
    series = []
    for ci, cid in enumerate(case_ids):
        sel = sorted((r for r in rows if str(r.get("case")) == cid and _finite(r.get("departure_h"))), key=lambda r: r["departure_h"])
        pts = []
        for r in sel:
            s = _num(r, "saving_min")
            if s is not None:
                checks.add("queue-aware-optimal", s >= -(_REL * (_num(r, "aware_min") or 0.0) + _ABS_MIN), "queue-awareness",
                           f"{cid} {r['departure_h']}:00: saving {s} min")
            pts.append({"hour": r["departure_h"], "saving_min": s, "aware_min": _num(r, "aware_min"),
                        "blind_min": _num(r, "blind_min"), "aware_wait_min": _num(r, "aware_wait_min"),
                        "blind_wait_min": _num(r, "blind_wait_min")})
        meta = case_meta(cid)
        series.append({"case": cid, "case_index": ci, "title": meta["title"], "points": pts})
    allp = [(s, p) for s in series for p in s["points"] if p["saving_min"] is not None]
    mean = _mean(p["saving_min"] for _, p in allp)
    top = max(allp, key=lambda sp: sp[1]["saving_min"]) if allp else None

    def finding(lang: str) -> str:
        if not allp:
            return ""
        s_top, p_top = top
        hh = f"{int(p_top['hour']):02d}:00"
        if lang == "tr":
            s = (f"Beklenen kuyruklarla planlamak {len(allp)} kalkışta ortalama {fmt_minutes(mean, lang)} kazandırıyor; "
                 f"en büyük kazanç {fmt_minutes(p_top['saving_min'], lang)} ({s_top['title']}, {hh} kalkış)")
            return s + "."
        return (f"Planning with expected queues saves {fmt_minutes(mean, lang)} on average over {len(allp)} departures; "
                f"the largest saving is {fmt_minutes(p_top['saving_min'], lang)} ({s_top['title']}, leaving at {hh}).")

    return {"data": {"series": series, "hours": hours}, "finding": _both(finding),
            "facts": {"mean_saving_min": mean, "max_saving_min": top[1]["saving_min"] if top else None, "n": len(allp)}}


def _dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    """``a`` is better than ``b`` in *every* criterion by more than the CSV rounding.

    The runner rounds values to six decimals, so a tie in the CSV (typical
    for the wear, ~0.01 %SoH) says nothing about which value is smaller; only
    a clear improvement in all criteria proves that ``b`` should not be on
    the front.
    """
    tol = [_REL * max(abs(x), abs(y)) + 2e-6 for x, y in zip(a, b)]
    return all(x < y - t for x, y, t in zip(a, b, tol))


def _sec_pareto(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    files = {k: v for k, v in exp["files"].items() if k != "results.csv"} or dict(exp["files"])
    if not files:
        return None
    fronts = []
    empty = []
    cs = ctx.get("case_studies") or {}
    for fname, f in files.items():
        rows = [r for r in f["rows"] if _feasible(r) and _finite(r.get("time_min")) and _finite(r.get("money"))]
        if not rows:
            if f["rows"]:
                r0 = f["rows"][0]
                cid = str(r0.get("case") or fname[:-4])
                empty.append({"case": cid, "title": case_meta(cid)["title"], "runtime_ms": _num(r0, "runtime_ms"),
                              "settled": _num(r0, "settled"), "generated": _num(r0, "generated")})
            continue
        cid = str(rows[0].get("case") or fname[:-4])
        pts = sorted(({"time_min": float(r["time_min"]), "money": float(r["money"]), "soh_pct": _num(r, "soh_pct"),
                       "stops": _num(r, "stops"), "charge_min": _num(r, "charge_min"), "queue_min": _num(r, "queue_min"),
                       "charged_kwh": _num(r, "charged_kwh"), "gcost": _num(r, "gcost")} for r in rows),
                     key=lambda p: (p["time_min"], p["money"]))
        vec = [(p["time_min"], p["money"], p["soh_pct"] if p["soh_pct"] is not None else 0.0) for p in pts]
        for i, a in enumerate(vec):
            dominated_by = next((j for j, b in enumerate(vec) if j != i and _dominates(b, a)), None)
            checks.add("pareto-nondominated", dominated_by is None, "pareto",
                       f"{cid}: member {i} {a} dominated by {dominated_by}")
        t0, t1 = pts[0]["time_min"], max(p["time_min"] for p in pts)
        m0, m1 = min(p["money"] for p in pts), max(p["money"] for p in pts)
        knee = min(range(len(pts)), key=lambda i: math.hypot((pts[i]["time_min"] - t0) / max(t1 - t0, 1e-9),
                                                             (pts[i]["money"] - m0) / max(m1 - m0, 1e-9)))
        cheapest = min(range(len(pts)), key=lambda i: (pts[i]["money"], pts[i]["time_min"]))
        refs = []
        for case in cs.get("cases", []):
            if case["id"] == cid:
                refs = [{"algorithm": r["algorithm"], "time_min": r["time_min"], "money": r["money"], "soh_pct": r["soh_pct"],
                         "stops": r["stops"]} for r in case["rows"] if r["feasible"] and r["time_min"] is not None and r["money"] is not None]
        # The front is exact on its charging-target grid only (ε-approximate in quick runs): a
        # plan that charges between the grid's targets (CFP, or BLS on a finer grid) can beat
        # members in every criterion.  That is flagged here, not counted as an exactness failure.
        beaten: set[int] = set()
        for ref in refs:
            rv = (ref["time_min"], ref["money"], ref["soh_pct"] if ref["soh_pct"] is not None else 0.0)
            hit = [i for i, b in enumerate(vec) if _dominates(rv, b)]
            ref["dominates"] = len(hit)
            beaten.update(hit)
        meta = case_meta(cid)
        fronts.append({"case": cid, "title": meta["title"], "currency": meta["currency"], "points": pts,
                       "fastest": 0, "cheapest": cheapest, "knee": knee, "references": refs,
                       "beaten_by_references": sorted(beaten)})
    if not fronts and not empty:
        return None

    def finding(lang: str) -> str:
        if not fronts:
            names = ", ".join(e["title"] for e in empty)
            if lang == "tr":
                return f"Pareto araması uygulanabilir bir cephe döndürmedi ({names}); arama bütçesi aşılmış olabilir."
            return f"The Pareto search returned no feasible front ({names}); its label budget was probably exhausted."
        f = fronts[0]
        p0, pc = f["points"][f["fastest"]], f["points"][f["cheapest"]]
        dm, dt = p0["money"] - pc["money"], pc["time_min"] - p0["time_min"]
        pct = dm / p0["money"] * 100.0 if p0["money"] else None
        cur = f["currency"]
        if lang == "tr":
            s = (f"{f['title']} için cephede {len(f['points'])} plan var: en hızlıdan en ucuza geçmek "
                 f"{fmt_money(dm, cur, lang)} ({fmt_pct(pct, 0, lang)}) tasarruf ettiriyor, bedeli {fmt_minutes(dt, lang)} daha uzun yolculuk")
        else:
            s = (f"The front for {f['title']} has {len(f['points'])} plans: going from the fastest to the cheapest saves "
                 f"{fmt_money(dm, cur, lang)} ({fmt_pct(pct, 0, lang)}) for {fmt_minutes(dt, lang)} more travel time")
        return s + "."

    return {"data": {"fronts": fronts, "empty": empty}, "finding": _both(finding),
            "facts": {"front_sizes": {f["case"]: len(f["points"]) for f in fronts},
                      "beaten_by_references": {f["case"]: len(f["beaten_by_references"]) for f in fronts}}}


def _sec_robustness(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    key = "safety_factor" if rows and "safety_factor" in rows[0] else ("factor" if rows and "factor" in rows[0] else None)
    if not rows or key is None:
        return None
    rows = sorted((r for r in rows if _finite(r.get(key))), key=lambda r: r[key])
    pts = []
    for r in rows:
        pts.append({"factor": float(r[key]), "feasible": _feasible(r), "planned_min": _num(r, "planned_time_min"),
                    "stops": _num(r, "stops"), "n": _num(r, "n"),
                    "p_strand": _num(r, "p_strand"), "p_strand_lo": _num(r, "p_strand_lo"), "p_strand_hi": _num(r, "p_strand_hi"),
                    "p_breach": _num(r, "p_reserve_breach"), "p_breach_lo": _num(r, "p_reserve_breach_lo"),
                    "p_breach_hi": _num(r, "p_reserve_breach_hi"),
                    "p_short": _num(r, "p_arrival_short"), "p_short_lo": _num(r, "p_arrival_short_lo"),
                    "p_short_hi": _num(r, "p_arrival_short_hi"),
                    "time_p50_min": (_num(r, "time_p50_s") or 0) / 60.0 if _num(r, "time_p50_s") is not None else None,
                    "time_p95_min": (_num(r, "time_p95_s") or 0) / 60.0 if _num(r, "time_p95_s") is not None else None,
                    "time_mean_min": (_num(r, "time_mean_s") or 0) / 60.0 if _num(r, "time_mean_s") is not None else None,
                    "charged_kwh": _num(r, "planned_charged_kwh"), "distance_km": _num(r, "distance_km")})
    planned = [p for p in pts if p["planned_min"] is not None]
    for a, b in zip(planned, planned[1:]):
        checks.add("safety-monotone", _leq(a["planned_min"], b["planned_min"]), "robustness",
                   f"factor {a['factor']} -> {b['factor']}: {a['planned_min']} > {b['planned_min']} min")
    base = planned[0] if planned else None
    target = next((p for p in planned if p["p_breach"] is not None and p["p_breach"] <= 0.05), None)
    algorithm = str(rows[0].get("algorithm") or "bls-astar") if rows else "bls-astar"

    def finding(lang: str) -> str:
        if not base or base["p_breach"] is None:
            return ""
        n = int(base["n"]) if base.get("n") else None
        f0 = fmt_num(base["factor"], 2, lang)
        pb, ps = fmt_pct(100 * base["p_breach"], 1, lang), fmt_pct(100 * (base["p_strand"] or 0.0), 1, lang)
        if lang == "tr":
            s = f"{f0} katsayısıyla planlanan rotada rezerv aşımı olasılığı {pb} ({n or ''} tekrar, yolda kalma {ps})"
            s = s.replace("( tekrar, ", "(")
            if target is base:
                s = f"{f0} katsayısında bile rezerv aşımı olasılığı yalnızca {pb} (yolda kalma {ps})"
            elif target:
                s += (f"; {fmt_num(target['factor'], 2, lang)} katsayısı bunu {fmt_pct(100 * target['p_breach'], 1, lang)} düzeyine "
                      f"indiriyor, bedeli {fmt_minutes(target['planned_min'] - base['planned_min'], lang)} daha uzun plan")
            return s + "."
        s = f"Planned with factor {f0}, the route breaches the reserve in {pb} of {n or 'the'} replays (strands in {ps})"
        if target is base:
            s = f"Even with factor {f0} the reserve is breached in only {pb} of replays (strands in {ps})"
        elif target:
            s += (f"; factor {fmt_num(target['factor'], 2, lang)} brings it down to {fmt_pct(100 * target['p_breach'], 1, lang)} "
                  f"for {fmt_minutes(target['planned_min'] - base['planned_min'], lang)} more planned time")
        return s + "."

    return {"data": {"points": pts, "algorithm": algorithm,
                     "distance_km": base.get("distance_km") if base else None},
            "finding": _both(finding),
            "facts": {"base_breach": base["p_breach"] if base else None, "target_factor": target["factor"] if target else None}}


def _sec_eco(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    pts = []
    for r in rows:
        s = _num(r, "saving_min")
        f, e = _num(r, "fixed_min"), _num(r, "eco_min")
        if f is not None and e is not None:
            checks.add("eco-not-worse", _leq(e, f), "eco-speed", f"pair {r.get('pair')} {r.get('temperature_c')} °C: eco {e} > fixed {f} min")
        pts.append({"pair": r.get("pair"), "temperature_c": _num(r, "temperature_c"), "saving_min": s,
                    "fixed_min": f, "eco_min": e, "fixed_stops": _num(r, "fixed_stops"), "eco_stops": _num(r, "eco_stops"),
                    "slowed_edges": _num(r, "slowed_edges")})
    vals = [p for p in pts if p["saving_min"] is not None]
    improved = [p for p in vals if p["saving_min"] > 0.05]
    fewer = [p for p in vals if p["eco_stops"] is not None and p["fixed_stops"] is not None and p["eco_stops"] < p["fixed_stops"]]
    top = max(vals, key=lambda p: p["saving_min"]) if vals else None
    temps = sorted({p["temperature_c"] for p in pts if p["temperature_c"] is not None})

    def finding(lang: str) -> str:
        if not vals:
            return ""
        n, k = len(vals), len(improved)
        if lang == "tr":
            if not k:
                return f"{n} yolculuğun hiçbirinde yavaşlamak kazandırmıyor: tam hız zaten en iyisi."
            s = f"Eko hızlar {k}/{n} yolculuğu 3 saniyeden fazla kısaltıyor; en büyük kazanç {fmt_minutes(top['saving_min'], lang)}"
            if fewer:
                s += f", {len(fewer)} yolculukta bir şarj durağı daha az"
            return s + "; diğerlerinde tam hız zaten en iyisi."
        if not k:
            return f"Slowing down helps on none of the {n} trips: full speed is already optimal."
        s = f"Eco speeds shorten {k} of {n} trips by more than 3 s; the largest saving is {fmt_minutes(top['saving_min'], lang)}"
        if fewer:
            s += f", and {len(fewer)} trip{'s' if len(fewer) != 1 else ''} need one stop fewer"
        return s + "; on the others full speed is already optimal."

    return {"data": {"points": pts, "temperatures": temps}, "finding": _both(finding),
            "facts": {"improved": len(improved), "n": len(vals), "max_saving_min": top["saving_min"] if top else None,
                      "fewer_stops": len(fewer)}}


def _sec_vehicles(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    out = []
    for r in rows:
        key = str(r.get("vehicle"))
        meta = vehicle_meta(key)
        out.append({"vehicle": key, **meta, "algorithm": r.get("algorithm"), "feasible": _feasible(r),
                    "time_min": _num(r, "time_min") if _feasible(r) else None, "drive_min": _num(r, "drive_min"),
                    "queue_min": _num(r, "queue_min"), "charge_min": _num(r, "charge_min"), "stops": _num(r, "stops"),
                    "energy_kwh": _num(r, "energy_kwh"), "charged_kwh": _num(r, "charged_kwh"), "money": _num(r, "money"),
                    "soh_pct": _num(r, "soh_pct"), "distance_km": _num(r, "distance_km"), "arrival_soc": _num(r, "arrival_soc")})
    ok = [v for v in out if v["time_min"] is not None]
    fast = min(ok, key=lambda v: v["time_min"]) if ok else None
    slow = max(ok, key=lambda v: v["time_min"]) if ok else None
    cur = case_meta("turkey-karaman-istanbul")["currency"]

    def finding(lang: str) -> str:
        if not fast or not slow:
            return ""
        if fast is slow:
            if lang == "tr":
                return f"{fast['name']} yolculuğu {fmt_minutes(fast['time_min'], lang)} sürede tamamlıyor."
            return f"{fast['name']} completes the trip in {fmt_minutes(fast['time_min'], lang)}."
        dt = slow["time_min"] - fast["time_min"]
        if lang == "tr":
            return (f"Aynı yolculukta en hızlı araç {fast['name']} ({fmt_minutes(fast['time_min'], lang)}, {int(fast['stops'] or 0)} durak); "
                    f"{slow['name']} {fmt_minutes(dt, lang)} daha uzun sürüyor ve {int(slow['stops'] or 0)} kez şarj ediyor.")
        return (f"On the same trip {fast['name']} is fastest ({fmt_minutes(fast['time_min'], lang)}, {int(fast['stops'] or 0)} "
                f"stop{'s' if (fast['stops'] or 0) != 1 else ''}); {slow['name']} needs {fmt_minutes(dt, lang)} more and "
                f"{int(slow['stops'] or 0)} stops.")

    return {"data": {"vehicles": out, "currency": cur}, "finding": _both(finding),
            "facts": {"fastest": fast["vehicle"] if fast else None, "slowest": slow["vehicle"] if slow else None}}


_PLAN_FIELDS = ("time_min", "money", "gcost", "money_wear", "soh_pct", "stops", "holds", "hold_min", "max_soc",
                "above80_kwh", "peak_stops", "drive_min", "queue_min", "charge_min", "charged_kwh", "runtime_ms")


def _dominated_2d(pts: Sequence[tuple[float, float]]) -> int | None:
    """Index of a point that another point beats clearly in both criteria
    (:func:`_dominates` on ``(time, money)``), or ``None``; ``O(n log n)``."""
    order = sorted(range(len(pts)), key=lambda i: pts[i][0])
    ts = [pts[i][0] for i in order]
    pre: list[float] = []  # prefix minimum of the money in time order
    for i in order:
        pre.append(min(pts[i][1], pre[-1]) if pre else pts[i][1])
    for i, (t, m) in enumerate(pts):
        # b beats a clearly iff b.t < a.t - tol(a.t) and b.m < a.m - tol(a.m) (b smaller in both)
        k = bisect.bisect_left(ts, t - (_REL * abs(t) + 2e-6))
        if k and pre[k - 1] < m - (_REL * abs(m) + 2e-6):
            return i
    return None


def _sec_tariffs(exp: Mapping[str, Any], checks: _Checks, ctx: Mapping[str, Any]) -> dict[str, Any] | None:
    rows = _rows(exp)
    if not rows:
        return None
    members = _rows(exp, "fronts.csv")
    summary = exp.get("summary") if isinstance(exp.get("summary"), dict) else {}
    specs = summary.get("tariffs") if isinstance(summary.get("tariffs"), dict) else {}
    known = [k for k, _, _ in TARIFF_STYLES]
    present = list(dict.fromkeys(str(r.get("tariff")) for r in rows if r.get("tariff") is not None))
    tkeys = [k for k in known if k in present] + [k for k in present if k not in known]
    style = {k: (title, color) for k, title, color in TARIFF_STYLES}
    tariffs = [{"key": k, "title": style.get(k, ({"tr": k, "en": k}, None))[0], "color": style.get(k, (None, None))[1],
                "spec": specs.get(k)} for k in tkeys]
    case_ids = list(dict.fromkeys(str(r.get("case")) for r in rows if r.get("case") is not None))
    hours = sorted({int(r["departure_h"]) for r in rows if _finite(r.get("departure_h"))})
    by: dict[tuple[str, int, str, str], dict[str, Any]] = {}
    for r in rows:
        if _finite(r.get("departure_h")):
            by[(str(r.get("case")), int(r["departure_h"]), str(r.get("tariff")), str(r.get("kind")))] = r
    fronts: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for r in members:
        if _finite(r.get("departure_h")) and _finite(r.get("time_min")) and _finite(r.get("money")):
            fronts.setdefault((str(r.get("case")), int(r["departure_h"]), str(r.get("tariff"))), []).append(r)
    for v in fronts.values():
        v.sort(key=lambda r: (r["time_min"], r["money"]))

    def plan_dict(r: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if r is None:
            return None
        out = {k: _num(r, k) for k in _PLAN_FIELDS}
        out["feasible"] = _feasible(r)
        out["status"] = str(r.get("status") or "")
        if not out["feasible"]:
            out["time_min"] = None
        return out

    cases = []
    out_fronts: dict[str, list[list[float | None]]] = {}
    for ci, cid in enumerate(case_ids):
        meta = case_meta(cid)
        series: dict[str, list[dict[str, Any]]] = {}
        for tk in tkeys:
            pts = []
            for h in hours:
                t_row, c_row, f_row = (by.get((cid, h, tk, k)) for k in ("time", "cost", "pareto"))
                if t_row is None and c_row is None and f_row is None:
                    continue
                t, c = plan_dict(t_row), plan_dict(c_row)
                fr = fronts.get((cid, h, tk), [])
                front = None
                if f_row is not None:
                    front = {"size": int(_num(f_row, "front_size") or len(fr)), "complete": bool(_num(f_row, "complete")),
                             "status": str(f_row.get("status") or ""), "runtime_ms": _num(f_row, "runtime_ms"),
                             "settled": _num(f_row, "settled")}
                    out_fronts[f"{cid}|{h}|{tk}"] = [
                        [round(m["time_min"], 3), round(m["money"], 2), _round(_num(m, "hold_min"), 2),
                         _round(_num(m, "max_soc"), 3), _round(_num(m, "above80_kwh"), 2), _num(m, "stops"),
                         _round(_num(m, "soh_pct"), 5)] for m in fr]
                label = f"{cid} {h:02d}:00 {tk}"
                _tariff_checks(checks, label, t, c, fr, front)
                pts.append({"hour": h, "time": t, "cost": c, "front": front})
            series[tk] = pts
        viewer = None
        vp = Path(exp["dir"]) / cid / "viewer.html"
        if vp.exists() and ctx.get("out_dir") is not None:
            viewer = os.path.relpath(vp, ctx["out_dir"]).replace(os.sep, "/")
        vh = ((summary.get("cases") or {}).get(cid) or {}).get("viewer_departure_h") if isinstance(summary.get("cases"), dict) else None
        cases.append({"id": cid, "title": meta["title"], "currency": meta["currency"], "case_index": ci,
                      "series": series, "viewer": viewer, "viewer_hour": vh if _finite(vh) else None})

    # ---- facts: the strongest priced tariff against flat prices
    main = next((k for k in reversed(tkeys) if k != "flat"), None)
    prem: list[tuple[float, str, int]] = []
    save: list[tuple[float, float, float, str, int]] = []
    holds: list[tuple[float, str, int, str]] = []
    above_t: list[float] = []
    above_c: list[float] = []
    dearer = 0
    for c in cases:
        flat = {p["hour"]: p for p in c["series"].get("flat", [])}
        for tk, pts in c["series"].items():
            for p in pts:
                t, k = p["time"], p["cost"]
                if k and k["feasible"] and (k["holds"] or 0) > 0:
                    holds.append((k["hold_min"] or 0.0, c["title"], p["hour"], tk))
                if tk != main or not (t and k and t["feasible"] and k["feasible"]):
                    continue
                save.append((t["money"] - k["money"], (t["money"] - k["money"]) / t["money"] * 100.0 if t["money"] else 0.0,
                             k["time_min"] - t["time_min"], c["title"], p["hour"]))
                dearer += k["money"] > t["money"] + 1e-6
                above_t.append(t["above80_kwh"] or 0.0)
                above_c.append(k["above80_kwh"] or 0.0)
                f = flat.get(p["hour"])
                if f and f["time"] and f["time"]["feasible"] and f["time"]["money"]:
                    prem.append(((t["money"] / f["time"]["money"] - 1.0) * 100.0, c["title"], p["hour"]))
    sizes = [p["front"]["size"] for c in cases for pts in c["series"].values() for p in pts if p["front"]]
    incomplete = sum(1 for c in cases for pts in c["series"].values() for p in pts if p["front"] and not p["front"]["complete"])
    n_main = len(save)
    top_prem = max(prem, key=lambda x: x[0]) if prem else None
    top_hold = max(holds, key=lambda x: x[0]) if holds else None
    holds_main = [x for x in holds if x[3] == main]
    facts = {
        "main_tariff": main, "n": n_main,
        "premium_mean_pct": _mean(x[0] for x in prem), "premium_max_pct": top_prem[0] if top_prem else None,
        "saving_mean": _mean(x[0] for x in save), "saving_mean_pct": _mean(x[1] for x in save),
        "extra_time_mean_min": _mean(x[2] for x in save),
        "holds": len(holds_main), "holds_all": len(holds), "max_hold_min": top_hold[0] if top_hold else None,
        "front_min": min(sizes) if sizes else None, "front_max": max(sizes) if sizes else None,
        "fronts": len(sizes), "incomplete_fronts": incomplete,
        "above80_time_mean_kwh": _mean(above_t), "above80_cost_mean_kwh": _mean(above_c),
        # not an exactness failure: the generalised cost may trade money for less battery wear
        "cost_dearer_than_time": dearer,
    }
    title = {k: v[0] for k, v in style.items()}
    cur = cases[0]["currency"] if cases else None

    def finding(lang: str) -> str:
        if main is None or not n_main:
            return ""
        tn = _tariff_title(title, main, lang)
        if lang == "tr":
            parts = []
            if top_prem is not None:
                parts.append(f"{tn} tarifesinde en hızlı plan şarja sabit fiyata göre ortalama {fmt_pct(facts['premium_mean_pct'], 0, lang)} "
                             f"fazla ödüyor (en çok {fmt_pct(top_prem[0], 0, lang)}, {top_prem[1]}, {top_prem[2]:02d}:00 kalkış)")
            parts.append(f"maliyet-optimal plan en hızlıdan ortalama {fmt_money(facts['saving_mean'], cur, lang)} "
                         f"({fmt_pct(facts['saving_mean_pct'], 0, lang)}) ucuz, bedeli {fmt_minutes(facts['extra_time_mean_min'], lang)} daha uzun yolculuk")
            s = "; ".join(parts)
            s = s[0].upper() + s[1:]
            if holds_main:
                th = max(holds_main, key=lambda x: x[0])
                s += (f"; {n_main} yolculuğun {len(holds_main)} tanesinde ucuz fiyat dönemi için istasyonda bekliyor "
                      f"(en çok {fmt_minutes(th[0], lang)}, {th[1]}, {th[2]:02d}:00)")
            if facts["front_min"] is not None:
                s += (f". Tek bir en iyi rota yok: (süre, ücret) cephelerinde {facts['front_min']}–{facts['front_max']} plan var"
                      + (f" ({facts['fronts']} cepheden {incomplete} tanesi etiket bütçesinde kesildi)" if incomplete else ""))
            return s + "."
        parts = []
        if top_prem is not None:
            parts.append(f"under the {tn} tariff the fastest plan pays on average {fmt_pct(facts['premium_mean_pct'], 0, lang)} more "
                         f"for charging than with flat prices (up to {fmt_pct(top_prem[0], 0, lang)}, {top_prem[1]} leaving at {top_prem[2]:02d}:00)")
        parts.append(f"the cost-optimal plan pays on average {fmt_money(facts['saving_mean'], cur, lang)} "
                     f"({fmt_pct(facts['saving_mean_pct'], 0, lang)}) less than the fastest for {fmt_minutes(facts['extra_time_mean_min'], lang)} more travel time")
        s = "; ".join(parts)
        s = s[0].upper() + s[1:]
        if holds_main:
            th = max(holds_main, key=lambda x: x[0])
            s += (f", and in {len(holds_main)} of {n_main} trips it waits at a station for the cheaper period "
                  f"(up to {fmt_minutes(th[0], lang)}, {th[1]} at {th[2]:02d}:00)")
        if facts["front_min"] is not None:
            s += (f". There is no single best route: the (time, money) fronts hold {facts['front_min']}–{facts['front_max']} plans"
                  + (f" ({incomplete} of {facts['fronts']} fronts stopped at the label budget)" if incomplete else ""))
        return s + "."

    return {"data": {"cases": cases, "hours": hours, "tariffs": tariffs, "fronts": out_fronts, "main": main,
                     "step_pct": 100.0 * float(summary["charge_step_soc"]) if _finite(summary.get("charge_step_soc")) else None,
                     "budget": summary.get("pareto_max_labels") if _finite(summary.get("pareto_max_labels")) else None,
                     "high_soc": summary.get("high_soc") if _finite(summary.get("high_soc")) else 0.8},
            "finding": _both(finding), "facts": facts}


def _tariff_title(titles: Mapping[str, LangText], key: str, lang: str) -> str:
    """Title of tariff ``key`` in ``lang`` (``80 % tier + peak``); unknown keys stay as they are."""
    t = titles.get(key)
    return t.get(lang, t.get("en", key)) if t else key


def _round(x: float | None, nd: int) -> float | None:
    return round(x, nd) if x is not None else None


def _tariff_checks(checks: _Checks, label: str, t: Mapping[str, Any] | None, c: Mapping[str, Any] | None,
               front: Sequence[Mapping[str, Any]], info: Mapping[str, Any] | None) -> None:
    """The exactness checks of one (case, departure, tariff) of the ``tariffs`` experiment."""
    tf = bool(t and t["feasible"] and t["time_min"] is not None)
    cf = bool(c and c["feasible"] and c["time_min"] is not None)
    if tf:
        tt = t["time_min"]
        ok = (t["hold_min"] or 0.0) <= 0.0
        others = ([c["time_min"]] if cf else []) + [m["time_min"] for m in front]
        slower = [x for x in others if not _leq(tt, x)]
        checks.add("tariff-time-optimal", ok and not slower, "tariffs",
                   f"{label}: time-optimal {tt} min, hold {t['hold_min']} min, faster plans {slower[:3]}")
    if tf and cf:
        for cid, key in (("tariff-cost-gcost", "gcost"), ("tariff-cost-money", "money_wear")):
            a, b = c.get(key), t.get(key)
            if a is not None and b is not None:
                checks.add(cid, _leq(a, b), "tariffs", f"{label}: cost-optimal {key} {a} > time-optimal {b}")
    if front:
        ok, why = True, ""
        if tf and not _close(front[0]["time_min"], t["time_min"]):
            ok, why = False, f"fastest member {front[0]['time_min']} min vs time optimum {t['time_min']} min"
        covered = any(_leq(m["time_min"], c["time_min"]) and _leq(m["money"], c["money"]) for m in front) if cf else True
        if ok and info and info.get("complete") and not covered:
            ok, why = False, f"no member covers the cost optimum ({c['time_min']} min, {c['money']})"
        checks.add("tariff-front-extremes", ok, "tariffs", f"{label}: {why}")
        bad = _dominated_2d([(m["time_min"], m["money"]) for m in front])
        checks.add("pareto-nondominated", bad is None, "tariffs",
                   f"{label}: member {bad} {None if bad is None else (front[bad]['time_min'], front[bad]['money'])} is dominated")


_ANALYZERS: dict[str, Callable[[Mapping[str, Any], _Checks, Mapping[str, Any]], dict[str, Any] | None]] = {
    "case-studies": _sec_case_studies,
    "scalability": _sec_scalability,
    "optimality-gap": _sec_optimality_gap,
    "grid-resolution": _sec_grid_resolution,
    "temperature": _sec_temperature,
    "queue-awareness": _sec_queue,
    "pareto": _sec_pareto,
    "robustness": _sec_robustness,
    "eco-speed": _sec_eco,
    "vehicles": _sec_vehicles,
    "tariffs": _sec_tariffs,
}

SECTION_TEXT: dict[str, dict[str, LangText]] = {
    "case-studies": {
        "title": {"tr": "Vaka çalışmaları: Türkiye şehirlerarası", "en": "Case studies: Turkish intercity trips"},
        "meaning": {"tr": "Sürüş süresi rotaya bağlı; algoritmalar arasındaki fark esas olarak nerede, ne kadar ve hangi saatte şarj edildiğinden, yani kuyruk ve şarj süresinden doğuyor.",
                    "en": "Driving time depends on the road; the differences between algorithms come mostly from where, how much and when they charge, i.e. from queueing and charging time."},
    },
    "scalability": {
        "title": {"tr": "Ölçeklenebilirlik", "en": "Scalability"},
        "meaning": {"tr": "Tutarlı enerji-açığı sezgiseliyle A* optimumu değiştirmeden aramanın büyük kısmını budar; kesin planlama ülke ölçeğindeki ağlarda da etkileşimli hızda kalır.",
                    "en": "A* with the consistent energy-deficit heuristic prunes most of the search without changing the optimum, so exact planning stays interactive on country-sized networks."},
    },
    "optimality-gap": {
        "title": {"tr": "Optimallik farkı", "en": "Optimality gap"},
        "meaning": {"tr": "Fark, bir planın sürekli şarj optimumuna (CFP) göre fazladan harcattığı yolculuk süresidir; %0 optimal demektir. Kutular çeyrekleri, noktalar tek tek yolculukları gösterir.",
                    "en": "The gap is the extra trip time a plan costs compared with the continuous-charging optimum (CFP); 0 % means optimal. Boxes show quartiles, dots individual trips."},
    },
    "grid-resolution": {
        "title": {"tr": "Şarj ızgarası çözünürlüğü", "en": "Charging-grid resolution"},
        "meaning": {"tr": "BLS yalnızca şarj hedefi ızgarasında kesindir; daha ince ızgara ya da şarj eğrisinin kırılma noktaları sürekli optimuma olan farkı kapatır, karşılığında arama büyür.",
                    "en": "BLS is exact only on its charging-target grid; a finer grid or the charging curve's breakpoints close the gap to the continuous optimum, at the price of a larger search."},
    },
    "temperature": {
        "title": {"tr": "Sıcaklığın etkisi", "en": "Effect of temperature"},
        "meaning": {"tr": "Soğuk hava kullanılabilir kapasiteyi düşürür, ısıtma yükü ekler ve şarjı yavaşlatır; hızlı şarj öncesi batarya ön ısıtması kaybın bir kısmını geri kazandırır.",
                    "en": "Cold weather shrinks the usable capacity, adds heating load and slows charging; preconditioning the battery before fast charging recovers part of the loss."},
    },
    "queue-awareness": {
        "title": {"tr": "Kuyruk farkındalığı", "en": "Queue awareness"},
        "meaning": {"tr": "Kuyruğu yok sayan plan, görmezden geldiği beklenen beklemelerle yeniden değerlendirilir; kazanç, o kalkış saati için kuyruk tahmininin değeridir.",
                    "en": "The queue-blind plan is re-evaluated with the expected waits it ignored; the saving is what queue forecasts are worth at that departure time."},
    },
    "pareto": {
        "title": {"tr": "Pareto cephesi: süre, ücret, yıpranma", "en": "Pareto front: time, money, wear"},
        "meaning": {"tr": "Her nokta, aynı şarj hedefi ızgarasındaki hiçbir planın süre, ücret ve batarya yıpranmasında aynı anda geçemediği bir plandır (hızlı koşularda ε payıyla); seçimi sürücü yapar. Cephe yalnızca bu ızgarada kesindir: ızgara hedeflerinin arasında şarj eden bir plan, örneğin CFP'nin sürekli şarjı, bir üyeyi yine de geçebilir.",
                    "en": "Every point is a plan that no other plan on the same charging-target grid beats on time, money and battery wear at once (within ε in quick runs); the driver picks the trade-off. The front is exact on that grid only: a plan that charges between the grid's targets, such as CFP's continuous charging, can still beat a member."},
    },
    "robustness": {
        "title": {"tr": "Sağlamlık: güvenlik katsayısı", "en": "Robustness: safety factor"},
        "meaning": {"tr": "Güvenlik katsayısı planlanan tüketimi büyütür; belirsiz tüketim ve kuyruklarla yapılan Monte Carlo tekrarları her fazladan dakikanın ne kadar güvenilirlik kazandırdığını gösterir.",
                    "en": "The safety factor inflates planned consumption; Monte Carlo replays with uncertain consumption and queues show how much reliability each extra minute buys."},
    },
    "eco-speed": {
        "title": {"tr": "Eko hızlar", "en": "Eco speeds"},
        "meaning": {"tr": "Bazı yollarda %90 ya da %80 hız serbest bırakılınca planlayıcı sürüş süresini daha kısa ya da daha az şarj durağıyla takas edebilir; sıfır kazanç tam hızın zaten en iyi olduğu demektir.",
                    "en": "Allowing 90 % or 80 % speed on some roads lets the planner trade driving time for shorter or fewer charging stops; a zero saving means full speed was already optimal."},
    },
    "vehicles": {
        "title": {"tr": "Araçlar", "en": "Vehicles"},
        "meaning": {"tr": "Aynı yolculuk farklı araçlarla: batarya boyutu, tüketim ve şarj eğrisi durak sayısını ve süresini belirler.",
                    "en": "The same trip with different vehicles: battery size, consumption and the charging curve decide how many stops are needed and how long they take."},
    },
    "tariffs": {
        "title": {"tr": "Şarj tarifeleri: %80 kuralı ve puant saatleri", "en": "Charging tariffs: the 80 % rule and peak hours"},
        "meaning": {"tr": "Örnek (sentetik) tarifelerle aynı yolculuk farklı kalkış saatlerinde: %80 doluluğun üzerindeki kWh daha pahalı, akşam puantında her kWh daha pahalı. Zaman-optimal plan fiyata bakmaz ve hiç beklemez; maliyet-optimal plan zaman değeri, ücret ve yıpranmanın toplamını en aza indirir ve gerekirse ucuz fiyat dönemine kadar istasyonda bekler; Pareto cephesi süre ile ücret arasındaki bütün kesin takasları gösterir. Tüm aramalar %5 SoC şarj ızgarasında kesindir; etiket bütçesinde kesilen bir cephe eksik olarak işaretlenir.",
                    "en": "The same trip at different departure times under illustrative (synthetic) tariffs: kWh above 80 % state of charge cost more, and every kWh costs more in the evening peak. The time-optimal plan ignores prices and never waits; the cost-optimal plan minimises the value of time plus money plus wear and may wait at a station for the cheaper period; the Pareto front shows every exact trade-off between time and money. All searches are exact on a 5 % SoC charging grid; a front stopped by its label budget is marked incomplete."},
    },
}


# ================================================================ overview
def _kpis(sections: Mapping[str, Mapping[str, Any]], checks: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def kpi(kid: str, section: str, label: LangText, value: LangText, sub: LangText, tone: str = "neutral", hero: bool = False) -> None:
        out.append({"id": kid, "section": section, "label": label, "value": value, "sub": sub, "tone": tone, "hero": hero})

    sc = sections.get("scalability", {}).get("facts") or {}
    at = sc.get("nodes") or {}
    mism = sc.get("mismatches") or {}

    def optimum(fast: str, L: str) -> str:
        """'same optimum' only when every equality check of the pair passed."""
        k, m = mism.get(fast, (0, 0))
        if k:
            return f"{m} örneğin {k} tanesinde farklı sonuç" if L == "tr" else f"differs on {k} of {m} instances"
        return "aynı optimum" if L == "tr" else "same optimum"

    sp = (sc.get("speedup") or {}).get("bls-astar")
    if _finite(sp):
        sr = (sc.get("settled_ratio") or {}).get("bls-astar")
        kpi("speedup-bls", "scalability", {"tr": "Hızlanma: BLS-A* / BLS", "en": "Speed-up: BLS-A* vs BLS"},
            _both(lambda L: fmt_factor(sp, L) + (" daha hızlı" if L == "tr" else " faster")),
            _both(lambda L: " · ".join(x for x in (
                f"{fmt_num(at.get('bls-astar'), 0, L)} {'düğüm' if L == 'tr' else 'nodes'}",
                ((f"{fmt_factor(sr, L)} daha az etiket" if L == "tr" else f"{fmt_factor(sr, L)} fewer labels") if _finite(sr) else ""),
                optimum("bls-astar", L)) if x)),
            "bad" if mism.get("bls-astar", (0, 0))[0] else "ok", hero=True)
    cp = (sc.get("speedup") or {}).get("cfp-astar")
    if _finite(cp):
        kpi("speedup-cfp", "scalability", {"tr": "Hızlanma: CFP-A* / CFP", "en": "Speed-up: CFP-A* vs CFP"},
            _both(lambda L: fmt_factor(cp, L)),
            _both(lambda L: (f"{fmt_num(at.get('cfp-astar'), 0, L)} düğüm · sürekli şarj · {optimum('cfp-astar', L)}" if L == "tr"
                             else f"{fmt_num(at.get('cfp-astar'), 0, L)} nodes · continuous charging · {optimum('cfp-astar', L)}")),
            "bad" if mism.get("cfp-astar", (0, 0))[0] else "neutral")
    og = sections.get("optimality-gap", {})
    hg = (og.get("facts") or {}).get("heuristic_mean_gap_pct")
    if _finite(hg):
        means = {k: v for k, v in (og["facts"].get("mean_gap_pct") or {}).items() if _finite(v) and k not in EXACT_ALGOS}
        lo = min(means, key=means.get) if means else None
        hi = max(means, key=means.get) if means else None
        kpi("heuristic-gap", "optimality-gap", {"tr": "Sezgisellerin ortalama farkı", "en": "Mean heuristic gap"},
            _both(lambda L: fmt_pct(hg, 1, L)),
            _both(lambda L: (f"{algo_title(lo, L)} {fmt_pct(means[lo], 1, L)} · {algo_title(hi, L)} {fmt_pct(means[hi], 1, L)}")
                  if lo and hi and lo != hi else ""), "warn" if hg > 1 else "ok")
    if checks.get("total"):
        p, t = checks["passed"], checks["total"]
        kpi("checks", "checks", {"tr": "Kesinlik denetimleri", "en": "Exactness checks"},
            {"tr": f"{p}/{t}", "en": f"{p}/{t}"},
            {"tr": "hepsi geçti" if p == t else f"{t - p} başarısız", "en": "all passed" if p == t else f"{t - p} failed"},
            "ok" if p == t else "bad")
    q = sections.get("queue-awareness", {}).get("facts") or {}
    if _finite(q.get("mean_saving_min")):
        kpi("queue", "queue-awareness", {"tr": "Kuyruk farkındalığı kazancı", "en": "Queue-aware saving"},
            _both(lambda L: fmt_minutes(q["mean_saving_min"], L)),
            _both(lambda L: (f"ortalama · en çok {fmt_minutes(q['max_saving_min'], L)}" if L == "tr"
                             else f"mean · up to {fmt_minutes(q['max_saving_min'], L)}")))
    t = sections.get("temperature", {}).get("facts") or {}
    if _finite(t.get("delta_time_min")):
        kpi("cold", "temperature", {"tr": "Soğuğun bedeli", "en": "Cost of cold"},
            _both(lambda L: "+" + fmt_minutes(t["delta_time_min"], L)),
            _both(lambda L: f"{fmt_temp(t['coldest_c'], L)} ↔ {fmt_temp(t['best_c'], L)}"))
    pr = sections.get("pareto", {})
    if pr.get("data") and pr["data"]["fronts"]:
        f = pr["data"]["fronts"][0]
        p0, pc = f["points"][f["fastest"]], f["points"][f["cheapest"]]
        kpi("pareto", "pareto", {"tr": "Pareto cephesi", "en": "Pareto front"},
            _both(lambda L: f"{len(f['points'])} plan" + ("" if L == "tr" else "s")),
            _both(lambda L: (f"{fmt_money(p0['money'] - pc['money'], f['currency'], L)} tasarruf ↔ +{fmt_minutes(pc['time_min'] - p0['time_min'], L)}"
                             if L == "tr" else f"save {fmt_money(p0['money'] - pc['money'], f['currency'], L)} ↔ +{fmt_minutes(pc['time_min'] - p0['time_min'], L)}")))
    rb = sections.get("robustness", {})
    if rb.get("data") and rb["data"]["points"]:
        pts = [p for p in rb["data"]["points"] if p["p_breach"] is not None]
        if pts:
            a, b = pts[0], pts[-1]
            kpi("robust", "robustness", {"tr": "Rezerv aşımı riski", "en": "Reserve-breach risk"},
                _both(lambda L: f"{fmt_pct(100 * a['p_breach'], 0, L)} → {fmt_pct(100 * b['p_breach'], 0, L)}"),
                _both(lambda L: (f"katsayı {fmt_num(a['factor'], 2, L)} → {fmt_num(b['factor'], 2, L)}" if L == "tr"
                                 else f"safety factor {fmt_num(a['factor'], 2, L)} → {fmt_num(b['factor'], 2, L)}")),
                "warn" if a["p_breach"] > 0.05 else "ok")
    tf = sections.get("tariffs", {}).get("facts") or {}
    if _finite(tf.get("saving_mean")) and tf.get("n"):
        cur = next((c.get("currency") for c in (sections["tariffs"].get("data") or {}).get("cases", [])), None)
        kpi("tariffs", "tariffs", {"tr": "Tarifeye göre planlama", "en": "Planning for the tariff"},
            _both(lambda L: ("−" if tf["saving_mean"] > 0 else "") + fmt_money(abs(tf["saving_mean"]), cur, L)),
            _both(lambda L: (f"en hızlı plana göre ortalama · +{fmt_minutes(tf['extra_time_mean_min'], L)} · {tf['holds']}/{tf['n']} beklemeli" if L == "tr"
                             else f"vs the fastest plan, mean · +{fmt_minutes(tf['extra_time_mean_min'], L)} · {tf['holds']}/{tf['n']} with a hold")))
    eco = sections.get("eco-speed", {}).get("facts") or {}
    if eco.get("n"):
        kpi("eco", "eco-speed", {"tr": "Eko hızlar", "en": "Eco speeds"},
            {"tr": f"{eco['improved']}/{eco['n']}", "en": f"{eco['improved']}/{eco['n']}"},
            _both(lambda L: (f"yolculuk kısalıyor · en çok {fmt_minutes(eco['max_saving_min'], L)}" if L == "tr"
                             else f"trips shorter · up to {fmt_minutes(eco['max_saving_min'], L)}")))
    return out


# ================================================================ payload
def _table_from_file(f: Mapping[str, Any]) -> dict[str, Any]:
    cols = list(f["columns"])
    rows = [[r.get(c) for c in cols] for r in f["rows"][:_TABLE_MAX_ROWS]]
    return {"columns": cols, "rows": rows, "total": len(f["rows"]), "truncated": len(f["rows"]) > _TABLE_MAX_ROWS}


def analyze_results(loaded: Mapping[str, Any], *, out_dir: str | Path | None = None) -> dict[str, Any]:
    """Sections, key numbers and exactness checks of a :func:`load_results` result."""
    checks = _Checks()
    report = loaded.get("report") or {}
    experiments = loaded.get("experiments") or {}
    ctx: dict[str, Any] = {"out_dir": Path(out_dir) if out_dir is not None else None}
    analysed: dict[str, dict[str, Any]] = {}
    order = [n for n in SECTION_IDS] + [n for n in experiments if n not in SECTION_IDS]
    for name in order:
        exp = experiments.get(name)
        if exp is None or name not in _ANALYZERS:
            continue
        try:
            res = _ANALYZERS[name](exp, checks, ctx)
        except Exception as exc:  # noqa: BLE001 - one bad file must not take the panel down
            res = {"error": f"{type(exc).__name__}: {exc}"}
        if res is not None:
            analysed[name] = res
            if name == "case-studies" and "data" in res:
                ctx["case_studies"] = res["data"]
    # Pareto references need the case studies, which come first in the order.
    sections = []
    index = 0
    for name in order:
        if name not in experiments and name not in report and name in SECTION_IDS:
            sections.append({"id": name, "status": "missing", "title": SECTION_TEXT[name]["title"],
                             "meaning": SECTION_TEXT[name]["meaning"]})
            continue
        if name not in experiments and name not in report:
            continue
        index += 1
        exp = experiments.get(name) or {"files": {}, "summary": None}
        rep = report.get(name) if isinstance(report.get(name), dict) else {}
        res = analysed.get(name) or {}
        # the runner writes an empty results.csv when no run produced a row
        has_rows = any(f["rows"] for f in exp["files"].values())
        status = rep.get("status") or ("ok" if has_rows else "empty")
        if status == "ok" and not has_rows:
            status = "empty"
        if "error" in res:
            status, err = "error", res["error"]
        else:
            err = (rep.get("result") or {}).get("error") if isinstance(rep.get("result"), dict) else None
        text = SECTION_TEXT.get(name, {"title": {"tr": name, "en": name}, "meaning": {"tr": "", "en": ""}})
        csvs = [{"name": fname, "text": f["text"]} for fname, f in exp["files"].items()]
        tables = [{"name": fname, **_table_from_file(f)} for fname, f in exp["files"].items() if f["columns"]]
        sections.append({
            "id": name, "index": index, "known": name in _ANALYZERS, "status": status, "error": err,
            "seconds": rep.get("seconds"), "title": text["title"], "meaning": text["meaning"],
            "finding": res.get("finding") if res.get("finding") and res["finding"].get("en") else None,
            "data": res.get("data"), "facts": res.get("facts") or {}, "csv": csvs, "tables": tables,
            "summary": exp.get("summary"),
        })
    for s in sections:
        if s["status"] == "missing":
            s["index"] = None
    check_summary = checks.summary()
    by_id = {s["id"]: s for s in sections if s.get("data") is not None}
    return {"sections": sections, "checks": check_summary, "kpis": _kpis(by_id, check_summary)}


def _theme_colors() -> dict[str, Any]:
    algos = {a: {"color": list(algorithm_color(a)), "title": {"tr": algo_title(a, "tr"), "en": algo_title(a, "en")},
                 "exact": a in EXACT_ALGOS} for a in list(ALGORITHM_COLORS)}
    tariffs = {k: {"title": title, "color": list(color) if color else None} for k, title, color in TARIFF_STYLES}
    return {"algorithms": algos, "cases": [list(c) for c in CASE_COLORS], "warm": list(WARM_COLOR), "cold": list(COLD_COLOR),
            "tariffs": tariffs}


def results_payload(
    results_dir: str | Path,
    *,
    lang: str = "tr",
    title: str | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """The JSON payload (format :data:`FORMAT`) of the dashboard for ``results_dir``."""
    loaded = load_results(results_dir)
    analysis = analyze_results(loaded, out_dir=out_dir)
    report = loaded["report"]
    total = sum(float(v.get("seconds") or 0) for v in report.values() if isinstance(v, dict))
    run = [{"id": k, "status": v.get("status"), "seconds": v.get("seconds")} for k, v in report.items() if isinstance(v, dict)]
    root = Path(results_dir).resolve()
    return {
        "format": FORMAT,
        "lang": "en" if lang == "en" else "tr",
        "title": title,
        "source": root.name,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run": {"experiments": run, "total_seconds": round(total, 2) if run else None},
        "theme": _theme_colors(),
        **analysis,
    }


# ================================================================== render
def _read_asset(rel: str) -> str:
    return (PANEL_DIR / rel).resolve().read_text(encoding="utf-8")


def _color_css() -> str:
    """Algorithm / case colour custom properties for both themes (from ALGORITHM_COLORS)."""
    light, dark = [], []
    for a, (lc, dc) in ALGORITHM_COLORS.items():
        light.append(f"--a-{a}: {lc};")
        dark.append(f"--a-{a}: {dc};")
    for i, (lc, dc) in enumerate(CASE_COLORS):
        light.append(f"--case-{i}: {lc};")
        dark.append(f"--case-{i}: {dc};")
    light += [f"--warm: {WARM_COLOR[0]};", f"--cold: {COLD_COLOR[0]};"]
    dark += [f"--warm: {WARM_COLOR[1]};", f"--cold: {COLD_COLOR[1]};"]
    for i, (_, _, color) in enumerate(TARIFF_STYLES):
        light.append(f"--tariff-{i}: {color[0] if color else 'var(--text-3)'};")
        dark.append(f"--tariff-{i}: {color[1] if color else 'var(--text-3)'};")
    return (":root {\n  " + "\n  ".join(light) + "\n}\n:root[data-theme=\"dark\"] {\n  " + "\n  ".join(dark) + "\n}\n")


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_results_html(payload: Mapping[str, Any]) -> str:
    """The self-contained dashboard (CSS, JS and data inlined) as a string."""
    template = _read_asset("index.html")
    css = "\n".join(f"/* ---- {name} ---- */\n{_read_asset(name)}" for name in CSS_FILES)
    css += "\n/* ---- colours (evroute.viz.export.ALGORITHM_COLORS) ---- */\n" + _color_css()
    js = "\n".join(f"// ---- {name} ----\n{_read_asset(name)}" for name in JS_FILES)
    if re.search(r"</style", css, re.I):
        raise ValueError("a stylesheet contains '</style'; it cannot be inlined")
    if re.search(r"</script|<!--", js, re.I):
        raise ValueError("a script contains '</script' or '<!--'; it cannot be inlined")
    if _TEMPLATE_DATA_TAG not in template or not _STYLES_BLOCK.search(template) or not _SCRIPTS_BLOCK.search(template):
        raise ValueError("results template is missing an evroute marker")
    lang = "en" if payload.get("lang") == "en" else "tr"
    page = _STYLES_BLOCK.sub(lambda _m: f"<style>\n{css}\n</style>", template, count=1)
    page = _SCRIPTS_BLOCK.sub(lambda _m: f"<script>\n{js}\n</script>", page, count=1)
    data_tag = f'<script id="evroute-results" type="application/json">{dumps_for_html(_clean(payload))}</script>'
    page = page.replace(_TEMPLATE_DATA_TAG, data_tag, 1)
    page = page.replace('<html lang="tr">', f'<html lang="{lang}">', 1)
    title = str(payload.get("title") or ("Deney sonuçları" if lang == "tr" else "Experiment results"))
    page = re.sub(r"<title>.*?</title>", lambda _m: f"<title>{_html_escape(title)} · EVRoute</title>", page, count=1)
    return page


def _clean(obj: Any) -> Any:
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    return obj


def build_results_html(results_dir: str | Path, out_path: str | Path, lang: str = "tr", *, title: str | None = None) -> Path:
    """Write the results dashboard of ``results_dir`` to ``out_path``; return the path.

    The page is a single file without any network reference (fonts, scripts
    and data are inlined), so it can be archived with the results or sent by
    e-mail.  ``lang`` (``"tr"`` or ``"en"``) is the initial language; the
    page can switch at any time.
    """
    out = Path(out_path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    payload = results_payload(results_dir, lang=lang, title=title, out_dir=out.parent.resolve())
    out.write_text(render_results_html(payload), encoding="utf-8")
    return out


# ===================================================================== CLI
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m evroute.viz.results",
        description="Build the interactive, offline results dashboard of an experiments output folder.",
    )
    ap.add_argument("results_dir", help="folder written by 'evroute experiments' (contains report.json)")
    ap.add_argument("-o", "--out", default=None, help="output HTML file (default: <results_dir>/results.html)")
    ap.add_argument("--lang", default="tr", choices=["tr", "en"], help="initial language (default: tr)")
    ap.add_argument("--title", default=None, help="page title")
    args = ap.parse_args(argv)
    src = Path(args.results_dir)
    if not src.is_dir():
        print(f"error: {src} is not a folder", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else src / "results.html"
    path = build_results_html(src, out, lang=args.lang, title=args.title)
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KiB; open it in any browser, works offline)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
