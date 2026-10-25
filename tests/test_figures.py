"""Tests of the static figures (evroute.viz.figures), the paper style
(evroute.viz.style) and the seaborn experiment plots (evroute.experiments.plots).

Every figure function is run on a small instance and must write non-empty
PDF and PNG files that are not blank; the numbers the figures show are
cross-checked against the independent evaluator.  The whole module runs in
well under a minute on the Agg backend.
"""

from __future__ import annotations

import importlib.util
import math
from itertools import pairwise
from pathlib import Path

import pytest

from evroute.algorithms import available, plan
from evroute.evaluate import evaluate
from evroute.generators import grid_scenario
from evroute.plan import RoutePlan
from evroute.viz import figures as F
from evroute.viz import style

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")
pytest.importorskip("seaborn")
plt = pytest.importorskip("matplotlib.pyplot")


# --------------------------------------------------------------- fixtures
def _scenario(seed: int = 1):
    """6 x 6 grid, 12 km spacing, low initial charge: one stop is needed and
    the naive (battery-unaware) route strands."""
    return grid_scenario(6, 6, seed=seed, initial_soc=0.3, n_stations=8, spacing_m=12000.0, time_dependent_waits=False)


def _run_all(sc, *, trace: bool = True) -> dict[str, RoutePlan]:
    out: dict[str, RoutePlan] = {}
    for name in available():
        try:
            out[name] = plan(sc, name, trace=trace)
        except Exception:  # a planner under construction must not break the figures
            continue
    return out


@pytest.fixture(scope="module")
def case():
    sc = _scenario(1)
    plans = _run_all(sc)
    assert "bls" in plans and "bls-astar" in plans, "the exact planners are needed by these tests"
    return sc, plans


@pytest.fixture(scope="module")
def multi():
    """Three small instances, every algorithm (for distributions and heatmaps)."""
    return {f"seed {s}": list(_run_all(_scenario(s), trace=False).values()) for s in (1, 2, 3)}


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


def _assert_written(paths, min_bytes: int = 2000) -> None:
    from PIL import Image

    paths = [Path(p) for p in paths]
    assert {p.suffix for p in paths} >= {".pdf", ".png"}
    for p in paths:
        assert p.exists() and p.stat().st_size > min_bytes, p
        if p.suffix == ".png":
            with Image.open(p) as im:
                colours = im.convert("RGB").getcolors(maxcolors=1 << 20)
                assert colours is None or len(colours) > 8, f"{p} looks blank"


def _written(base: Path) -> list[Path]:
    return [base.with_suffix(".pdf"), base.with_suffix(".png")]


# ------------------------------------------------------------------ style
def test_algorithm_colours_match_web_viewer():
    try:
        from evroute.viz import export
    except Exception as exc:  # pragma: no cover - viewer module mid-edit
        pytest.skip(f"viewer module not importable: {exc}")
    for name, (light, dark) in export.ALGORITHM_COLORS.items():
        assert style.algorithm_color(name).lower() == light.lower(), name
        assert style.algorithm_color(name, dark=True).lower() == dark.lower(), name
    # unknown names: same stable fallback as the viewer
    for name in ("my-planner", "x", "contraction-hierarchies"):
        assert style.algorithm_color(name).lower() == export.algorithm_color(name)[0].lower()


def test_style_lookups_are_stable():
    for name in available():
        assert style.algorithm_color(name).startswith("#")
        assert style.algorithm_title(name)
        assert style.algorithm_marker(name)
    assert style.algorithm_color("unknown-algo") == style.algorithm_color("unknown-algo")
    assert style.algorithm_linestyle("greedy") != "-" and style.algorithm_linestyle("bls-astar") == "-"
    pal = style.algorithm_palette(["bls", "bls-astar"], titles=True)
    assert pal == {"BLS": style.algorithm_color("bls"), "BLS-A*": style.algorithm_color("bls-astar")}
    assert style.figsize("single")[0] == 3.5 and style.figsize("double")[0] == 7.16


def test_rc_params_layer_seaborn_and_paper_overrides():
    rc = style.rc_params()
    assert rc["font.size"] == 8.0
    assert rc["pdf.fonttype"] == 42 and rc["ps.fonttype"] == 42
    assert rc["axes.spines.top"] is False and rc["axes.spines.right"] is False
    assert "xtick.direction" in rc and "axes.prop_cycle" in rc  # seaborn base + our cycle
    assert style.rc_params(theme="whitegrid")["axes.grid"] is True
    with pytest.raises(ValueError):
        style.configure(theme="no-such-theme")
    with pytest.raises(ValueError):
        style.configure(font="comic")


def test_apply_installs_seaborn_theme_and_palette():
    before = dict(matplotlib.rcParams)
    try:
        style.apply()
        assert matplotlib.rcParams["font.size"] == 8.0
        first = matplotlib.rcParams["axes.prop_cycle"].by_key()["color"][0]
        assert first.lower() == style.CATEGORICAL[0].lower()
    finally:
        matplotlib.rcParams.update(before)


def test_paper_context_restores_rcparams():
    size = matplotlib.rcParams["font.size"]
    with style.paper(base_size=11.0):
        assert matplotlib.rcParams["font.size"] == 11.0
    assert matplotlib.rcParams["font.size"] == size


def test_save_writes_pdf_and_png_reproducibly(tmp_path):
    with style.paper():
        fig, ax = plt.subplots(figsize=style.figsize("single"))
        ax.plot([0, 1, 2], [1, 3, 2], **style.line_kw("bls-astar"))
    a = style.save(fig, tmp_path / "a")
    b = style.save(fig, tmp_path / "b.png")  # suffix is replaced
    assert [p.name for p in a] == ["a.pdf", "a.png"] and [p.name for p in b] == ["b.pdf", "b.png"]
    _assert_written(a, min_bytes=1000)
    # no creation date in the PDF -> identical bytes for identical figures
    assert a[0].read_bytes() == b[0].read_bytes()


def test_no_matplotlib_animation_module():
    """The animated demo is the web viewer; there is no matplotlib animation module."""
    assert importlib.util.find_spec("evroute.viz.animate") is None
    src = Path(F.__file__).read_text(encoding="utf-8")
    assert "import animate" not in src and "from .animate" not in src


# ------------------------------------------------------------ SoC series
def test_soc_series_matches_evaluator(case):
    sc, plans = case
    P = sc.prepare()
    p = plans["bls-astar"]
    assert p.feasible and p.stops
    xs, ys = F.soc_series(p, P, "distance")
    assert xs[0] == 0.0 and ys[0] == pytest.approx(100 * p.node_energy_arrive[0] / p.capacity_kwh)
    assert xs[-1] == pytest.approx(p.distance_m / 1000.0)
    assert ys[-1] == pytest.approx(100 * p.arrival_soc)
    assert all(b >= a - 1e-12 for a, b in pairwise(xs))
    # every stop is a vertical jump to soc_out
    for st in p.stops:
        d = p.node_dist_m[st.position] / 1000.0
        assert any(abs(x - d) < 1e-9 and abs(y - 100 * st.soc_out) < 1e-6 for x, y in zip(xs, ys))
    # the time series resolves the charging function exactly and ends at arrival
    ts, ys_t = F.soc_series(p, P, "time")
    assert ts[-1] == pytest.approx(p.total_time_s / 3600.0)
    assert all(b >= a - 1e-12 for a, b in pairwise(ts))
    st = p.stops[0]
    t_plug = (st.arrival_s + st.wait_s + st.setup_s) / 3600.0
    assert any(abs(t - t_plug) < 1e-9 and abs(y - 100 * st.soc_in) < 1e-6 for t, y in zip(ts, ys_t))
    assert any(abs(t - st.departure_s / 3600.0) < 1e-9 and abs(y - 100 * st.soc_out) < 1e-6 for t, y in zip(ts, ys_t))


def test_soc_series_clips_infeasible_plan_at_stranding_point(case):
    sc, plans = case
    naive = plans.get("naive")
    if naive is None or naive.feasible or naive.stranded_at_m is None:
        pytest.skip("no stranded naive plan on this instance")
    xs, ys = F.soc_series(naive, sc)
    assert ys[-1] == 0.0 and min(ys) >= 0.0
    assert xs[-1] == pytest.approx(naive.stranded_at_m / 1000.0, rel=1e-9)
    raw_xs, raw_ys = F.soc_series(naive, sc, clip_empty=False)
    assert min(raw_ys) < 0.0 and len(raw_xs) > len(xs) - 1


# ------------------------------------------------------- figure functions
def test_plot_map_variants(case, tmp_path):
    sc, plans = case
    routed = [p for p in plans.values() if p.edges]
    fig = F.plot_map(sc, routed, path=tmp_path / "map")
    _assert_written(_written(tmp_path / "map"))
    ax = fig.axes[0]
    assert ax.get_legend() is not None and len(ax.get_legend().get_texts()) >= len(routed)
    F.plot_map(sc, routed, highlight="bls-astar", legend="outside", path=tmp_path / "map_hi")
    _assert_written(_written(tmp_path / "map_hi"))
    F.plot_map(sc, plans["bls-astar"], terrain=False, labels=False, stations=False, width="single", path=tmp_path / "map1")
    _assert_written(_written(tmp_path / "map1"))
    # into an existing axes
    f2, a2 = plt.subplots(figsize=(4, 4))
    assert F.plot_map(sc, routed, ax=a2) is f2


def test_identical_routes_share_a_map_lane(case):
    _, plans = case
    exact = [plans[a] for a in ("bls", "bls-astar") if a in plans]
    same = exact[0].edges == exact[1].edges and [s.position for s in exact[0].stops] == [s.position for s in exact[1].stops]
    groups = F._lane_groups(exact, highlight=None)
    assert (len(groups) == 1) == same
    # the highlighted plan always gets its own lane
    assert F._lane_groups(exact, highlight=1) == [[0], [1]]


def test_map_extent_keeps_requested_aspect(case):
    sc, plans = case
    x0, x1, y0, y1 = F.map_extent(sc, [plans["bls-astar"]], aspect_range=(0.5, 0.8))
    assert 0.5 - 1e-9 <= (y1 - y0) / (x1 - x0) <= 0.8 + 1e-9
    net = sc.network
    for v in plans["bls-astar"].nodes:
        assert x0 <= net.x[v] <= x1 and y0 <= net.y[v] <= y1


def test_plot_soc_profile_variants(case, tmp_path):
    sc, plans = case
    routed = [p for p in plans.values() if p.edges]
    F.plot_soc_profile(routed, sc, path=tmp_path / "soc")
    _assert_written(_written(tmp_path / "soc"))
    fig = F.plot_soc_profile(routed, sc, x="time", elevation="panel", path=tmp_path / "soc_t")
    _assert_written(_written(tmp_path / "soc_t"))
    assert len(fig.axes) == 2
    F.plot_soc_profile(plans["bls-astar"], None, elevation=None)  # no scenario: no reserve, no terrain


def test_time_breakdown_adds_up(case, tmp_path):
    _, plans = case
    feas = [p for p in plans.values() if p.feasible]
    fig = F.plot_time_breakdown(feas, path=tmp_path / "time")
    _assert_written(_written(tmp_path / "time"))
    ax = fig.axes[0]
    # the stacked segments of each bar add up to the evaluator's total time
    totals: dict[float, float] = {}
    for patch in ax.patches:
        y = round(patch.get_y() + patch.get_height() / 2, 6)
        totals[y] = max(totals.get(y, 0.0), patch.get_x() + patch.get_width())
    want = sorted(p.total_time_s / 3600.0 for p in feas)
    assert sorted(totals.values()) == pytest.approx(want, rel=1e-9)


def test_comparison_table_and_latex(case, tmp_path):
    _, plans = case
    ps = list(plans.values())
    F.plot_comparison_table(ps, path=tmp_path / "table")
    _assert_written(_written(tmp_path / "table"))
    _, rows, best = F._table_data(ps, F.DEFAULT_COLUMNS)
    j = list(F.DEFAULT_COLUMNS).index("time")
    for p, row in zip(ps, rows):
        if p.feasible:
            assert float(row[j]) == pytest.approx(p.total_time_s / 60.0, abs=0.05)
        else:
            assert row[j] == "infeasible"
    # the fastest feasible plan is marked best
    fastest = min((p for p in ps if p.feasible), key=lambda p: p.total_time_s)
    assert best[ps.index(fastest)][j]
    tex = F.latex_table(ps, caption="Results", label="tab:res")
    assert "\\toprule" in tex and "\\bottomrule" in tex and "\\label{tab:res}" in tex
    assert tex.count("\\\\") == len(ps) + 1


def test_plot_pareto(case, tmp_path):
    _, plans = case
    par = plans.get("pareto")
    if par is None or not par.feasible:
        pytest.skip("pareto planner unavailable")
    others = [plans[a] for a in ("greedy", "bls-astar") if a in plans]
    F.plot_pareto(par, others=others, path=tmp_path / "pareto")
    _assert_written(_written(tmp_path / "pareto"))
    # a plain list of members works too; the selected member is on the front
    F.plot_pareto(par.alternatives or [par])
    assert any(abs(m.total_time_s - par.total_time_s) < 1e-6 and abs(m.money - par.money) < 1e-6 for m in par.alternatives)
    with pytest.raises(ValueError):
        F.plot_pareto([plans["naive"]] if "naive" in plans else [])


def test_vehicle_figures(tmp_path):
    F.plot_charging_curves(path=tmp_path / "charging")
    _assert_written(_written(tmp_path / "charging"))
    F.plot_charging_curves(["compact50", "sedan75"], station_kw=50.0)
    F.plot_consumption_vs_speed(["compact50", "van75"], (-10.0, 25.0), path=tmp_path / "cons")
    _assert_written(_written(tmp_path / "cons"))


def test_charging_curve_minutes_are_exact():
    from evroute.vehicle import get_vehicle

    v = get_vehicle("compact50")
    powers = list(v.dc_curve.power_kw)
    _, ss, t80 = F._soc_vs_minutes(v, powers, 0.1, 0.8)
    # integrate dSoC * battery / P directly
    want = 0.0
    for b0, b1, p in zip(v.dc_curve.soc_breaks, v.dc_curve.soc_breaks[1:], powers):
        lo, hi = max(b0, 0.1), min(b1, 0.8)
        if hi > lo:
            want += (hi - lo) * v.battery_kwh / p * 60.0
    assert t80 == pytest.approx(want, rel=1e-12)
    assert ss[0] == pytest.approx(10.0) and ss[-1] == pytest.approx(100.0)


def test_wait_profiles(case, tmp_path):
    sc, plans = case
    F.plot_wait_profiles(sc, plans=list(plans.values()), path=tmp_path / "wait")
    _assert_written(_written(tmp_path / "wait"))
    F.plot_wait_profiles(sc, stations=[0, 1])
    F.plot_wait_profiles(sc)


def test_search_effort_and_space(case, multi, tmp_path):
    sc, plans = case
    F.plot_search_effort(list(plans.values()), path=tmp_path / "effort")
    _assert_written(_written(tmp_path / "effort"))
    F.plot_search_effort([p for ps in multi.values() for p in ps], path=tmp_path / "effort_multi")
    _assert_written(_written(tmp_path / "effort_multi"))
    traced = [plans[a] for a in ("bls", "bls-astar", "cfp", "cfp-astar") if a in plans]
    fig = F.plot_search_space(sc, traced, path=tmp_path / "space")
    _assert_written(_written(tmp_path / "space"))
    assert len([a for a in fig.axes if a.get_title(loc="left")]) == len(traced)
    # goal direction never settles more nodes than plain Dijkstra order here
    assert len(plans["bls-astar"].trace.nodes) <= len(plans["bls"].trace.nodes)
    with pytest.raises(ValueError):
        F.plot_search_space(sc, [plan(sc, "bls-astar", trace=False)])


def test_convergence(case, tmp_path):
    _, plans = case
    gen = plans.get("genetic")
    if gen is None or not (gen.stats and gen.stats.extra.get("history")):
        pytest.skip("genetic planner without history")
    F.plot_convergence(gen, reference=plans.get("cfp") or plans["bls"], path=tmp_path / "conv")
    _assert_written(_written(tmp_path / "conv"))
    with pytest.raises(ValueError):
        F.plot_convergence(plans["bls"])


def test_gap_rows_cross_check_with_evaluator(multi):
    rows = F.gap_rows(multi, reference="bls")
    assert rows
    by = {(r["instance"], r["algorithm"]): r for r in rows}
    for (inst, algo), r in by.items():
        if algo == "bls":
            assert r["gap_pct"] == 0.0
        if algo == "bls-astar":  # same optimum on the same grid
            assert abs(r["gap_pct"]) < 1e-6
        if r["feasible"] and algo == "cfp":  # continuous charging is never worse than the grid
            assert r["gap_pct"] <= 1e-6
        if not r["feasible"]:
            assert math.isnan(r["gap_pct"])
    # the plotted numbers are the independent evaluator's: re-evaluating each
    # decision on a freshly prepared instance reproduces them exactly
    for key, plans in multi.items():
        P = _scenario(int(key.split()[1])).prepare()
        for p in plans:
            if p.feasible and p.decision is not None:
                again = evaluate(P, p.decision, algorithm=p.algorithm)
                assert again.total_time_s == pytest.approx(p.total_time_s, rel=1e-12)
                assert again.money == pytest.approx(p.money, rel=1e-12, abs=1e-12)


def test_distribution_and_heatmap(multi, tmp_path):
    F.plot_gap_distribution(multi, reference="bls", path=tmp_path / "gap_box")
    _assert_written(_written(tmp_path / "gap_box"))
    F.plot_gap_distribution(list(multi.values()), kind="violin", path=tmp_path / "gap_violin")
    _assert_written(_written(tmp_path / "gap_violin"))
    with pytest.raises(ValueError):
        F.plot_gap_distribution(multi, kind="pie")
    for metric in ("gap", "time", "runtime", "settled"):
        F.plot_metric_heatmap(multi, metric=metric, path=tmp_path / f"heat_{metric}")
        _assert_written(_written(tmp_path / f"heat_{metric}"))


# ------------------------------------------------ contract with experiments
def test_case_study_figures_contract(case, tmp_path):
    """``evroute.experiments.figures`` relies on these four signatures."""
    sc, plans = case
    routed = [p for p in plans.values() if p.edges]
    for res in (
        F.plot_map(sc, routed),
        F.plot_soc_profile(routed, sc),
        F.plot_time_breakdown([p for p in routed if p.feasible]),
        F.plot_search_space(sc, [plans["bls"], plans["bls-astar"]]),
    ):
        fig = res[0] if isinstance(res, tuple) else res
        assert hasattr(fig, "savefig")
    try:
        from evroute.experiments.figures import case_study_figures
    except Exception as exc:  # pragma: no cover - orchestrator module mid-edit
        pytest.skip(f"experiments.figures not importable: {exc}")
    written = case_study_figures(sc, tmp_path / "case", plans=list(plans.values()))
    names = {p.stem for p in written}
    assert {"fig_map", "fig_soc", "fig_time"} <= names
    for p in written:
        assert p.stat().st_size > 1000


def test_experiment_plot_helpers(tmp_path):
    from evroute.experiments import plots

    series = {"bls": ([1000, 3000, 10000], [10.0, 40.0, 150.0]), "bls-astar": ([1000, 3000, 10000], [2.0, 6.0, 20.0])}
    _assert_written(plots.line_plot(series, "nodes", "runtime [ms]", tmp_path / "line", logx=True, logy=True))
    repeated = {"preconditioned": ([0, 0, 10, 10, 20, 20], [500, 505, 490, 492, 480, float("nan")])}
    _assert_written(plots.line_plot(repeated, "°C", "min", tmp_path / "line_ci"))
    _assert_written(plots.box_plot({"greedy": [1, 2, 3, 8], "genetic": [0.1, 0.5, 0.2], "empty": []}, "gap [%]",
                                   tmp_path / "box"))
    _assert_written(plots.bar_plot({"bls": [800, 900, 1000], "bls-astar": 79.0}, "ms", tmp_path / "bar"))
    _assert_written(plots.scatter_plot([1, 2, 3, 4], [4, 3, 2, 1], "t", "money", tmp_path / "sc", c=[0.1, 0.2, 0.3, 0.4],
                                       clabel="wear", sizes=[10, 20, 30, 40]))
    _assert_written(plots.heatmap([[1.0, 2.0], [float("nan"), 4.0]], ["a", "b"], ["greedy", "station-graph"],
                                  tmp_path / "heat", clabel="gap [%]"))
    assert plots.color("bls-astar") == style.algorithm_color("bls-astar")
