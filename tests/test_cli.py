"""Tests of the command-line interface: bad input is one ``error:`` line with
exit code 2 (never a traceback), and valid commands keep working."""

from __future__ import annotations

import json
import re
import socket

import pytest

from evroute import server
from evroute.cli import main

SC = "grid-small"


def _error_line(capsys) -> str:
    err = capsys.readouterr().err.strip()
    assert "\n" not in err and "Traceback" not in err, err
    assert err.startswith("error: "), err
    return err


@pytest.mark.parametrize(
    "argv, fragment",
    [
        (["plan", "no-such-preset"], "unknown scenario"),
        (["plan", SC, "--from", "Atlantis"], "no node named"),
        (["plan", SC, "--from", "100"], "origin 100 is not a node"),  # grid-small has nodes 0..99
        (["plan", SC, "--to", "-2"], "destination -2 is not a node"),
        (["plan", SC, "--to", "0"], "must differ"),  # the preset starts at node 0
        (["plan", SC, "--vehicle", "rocket"], "unknown vehicle"),
        (["plan", SC, "-a", "bls-astr"], "unknown algorithm 'bls-astr'"),
        (["compare", SC, "--algorithms", "greedy,nope"], "unknown algorithm 'nope'"),
        (["viz", SC, "--algorithms", "nope"], "unknown algorithm 'nope'"),
        (["plan", SC, "--depart", "8"], "--depart"),
        (["plan", SC, "--depart", "25:99"], "--depart"),
        (["plan", SC, "--soc", "1.2"], "initial_soc"),
        (["plan", SC, "--soc", "-0.3"], "initial_soc"),
        (["plan", SC, "--soc", "nan"], "initial_soc"),
        (["plan", SC, "--temp", "nan"], "temperature_c"),
        (["plan", SC, "--reserve", "-0.01"], "reserve_soc"),
        (["plan", SC, "--arrive", "1.5"], "min_arrival_soc"),
        (["plan", SC, "--safety", "0.9"], "safety_factor"),
        (["plan", SC, "--safety", "0"], "safety_factor"),
        (["plan", SC, "--step", "0"], "charge_step_soc"),
        (["plan", SC, "--step", "0.0005"], "charge_step_soc"),  # used to be raised to 0.001 silently
        (["plan", SC, "--temp", "-273.15"], "temperature_c"),  # used to raise ZeroDivisionError
        (["robustness", SC, "-n", "0"], "-n"),
        (["serve", "nope"], "unknown preset"),
        (["serve", "scenario.json"], "preset name"),
        # charging tariff
        (["plan", SC, "--tariff", "90"], "--tariff must be 80"),
        (["plan", SC, "--high-soc", "1.2"], "tariff.high_soc"),
        (["plan", SC, "--high-soc", "0"], "tariff.high_soc"),
        (["plan", SC, "--high-soc", "1"], "tariff.high_soc"),
        (["plan", SC, "--high-soc-factor", "0"], "tariff.high_soc_factor"),
        (["plan", SC, "--high-soc-factor", "nan"], "tariff.high_soc_factor"),
        (["plan", SC, "--peak-factor", "-1"], "tariff.peak_factor"),
        (["plan", SC, "--peak-high-soc-factor", "0"], "tariff.peak_high_soc_factor"),
        (["plan", SC, "--busy", "0"], "tariff.busy_utilisation"),
        (["plan", SC, "--busy", "inf"], "tariff.busy_utilisation"),
        (["plan", SC, "--peak", "16"], "--peak must be clock hours"),
        (["plan", SC, "--peak", "16-20,"], "--peak must be clock hours"),
        (["plan", SC, "--peak", "16:75-20"], "--peak must be clock hours"),
        (["plan", SC, "--peak", "16-16"], "is empty"),
        (["plan", SC, "--peak", "16-25"], "tariff.peak_hours end"),
        (["plan", SC, "--tariff", "80", "--peak", "30-2"], "tariff.peak_hours start"),
    ],
)
def test_bad_input_is_one_error_line(argv, fragment, capsys):
    assert main(argv) == 2
    assert fragment in _error_line(capsys)


def _exported(tmp_path, capsys, *argv) -> dict:
    path = tmp_path / "sc.json"
    assert main(["export-scenario", *argv, "-o", str(path)]) == 0
    capsys.readouterr()
    return json.loads(path.read_text())


def test_tariff_options_build_the_tariff(tmp_path, capsys):
    from evroute.generators.presets import DEMO_TARIFF

    assert "tariff" not in _exported(tmp_path, capsys, SC)  # neutral: no key, files unchanged
    assert _exported(tmp_path, capsys, SC, "--tariff", "80")["tariff"] == DEMO_TARIFF.to_dict()
    assert "tariff" not in _exported(tmp_path, capsys, "turkey-karaman-istanbul-tariff", "--tariff", "flat")
    t = _exported(tmp_path, capsys, SC, "--tariff", "80", "--peak", "22-6, 7:30-9", "--high-soc", "0.75",
                  "--high-soc-factor", "1.5", "--peak-factor", "1.2", "--peak-high-soc-factor", "3",
                  "--busy", "0.7")["tariff"]
    assert t == {"high_soc": 0.75, "high_soc_factor": 1.5, "peak_hours": [[22.0, 6.0], [7.5, 9.0]],
                 "peak_factor": 1.2, "peak_high_soc_factor": 3.0, "busy_utilisation": 0.7}
    t = _exported(tmp_path, capsys, "turkey-karaman-istanbul-tariff", "--peak", "none")["tariff"]
    assert t["peak_hours"] == [] and t["high_soc_factor"] == DEMO_TARIFF.high_soc_factor
    # the single options alone start from the neutral tariff
    assert _exported(tmp_path, capsys, SC, "--peak", "16-20")["tariff"]["peak_factor"] == 1.0


def test_plan_output_shows_holds_and_tariff_only_when_present(capsys):
    assert main(["plan", SC]) == 0
    header = capsys.readouterr().out.splitlines()[1].split()
    assert header == ["#", "station", "charger", "arrive", "queue", "charge", "SoC", "cost"]
    # the cost optimum of the tariff preset holds a few minutes for the 20:00 price change
    assert main(["plan", "turkey-karaman-istanbul-tariff", "--objective", "cost"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].split() == ["#", "station", "charger", "arrive", "hold", "queue", "charge", "SoC", "tariff",
                                "cost"]
    cells = [re.split(r"\s{2,}", line.strip()) for line in lines[3:-1]]
    assert all(len(c) == 10 for c in cells)
    assert any(c[4].endswith("min") for c in cells) and all(c[4] == "-" or c[4].endswith("min") for c in cells)
    assert "| hold " in lines[-1]
    # the time optimum never holds; its stops are priced in the peak
    assert main(["plan", "turkey-karaman-istanbul-tariff"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "hold" not in lines[1].split() and "tariff" in lines[1].split()
    assert all(" peak " in line for line in lines[3:-1]) and "| hold " not in lines[-1]
    # --tariff flat: the plan of the trip without a tariff, same output as a scenario without one
    assert main(["plan", "turkey-karaman-istanbul-tariff", "--tariff", "flat"]) == 0
    flat = capsys.readouterr().out
    assert main(["plan", "turkey-karaman-istanbul", "--depart", "14:00"]) == 0
    assert capsys.readouterr().out == flat


def test_scenario_files(tmp_path, capsys):
    missing = tmp_path / "missing.json"
    assert main(["plan", str(missing)]) == 2
    assert "no scenario file" in _error_line(capsys)
    for name, text in (("bad.json", "{not json"), ("empty.json", "{}"), ("list.json", "[1, 2]")):
        (tmp_path / name).write_text(text)
        assert main(["plan", str(tmp_path / name)]) == 2
        assert "cannot read scenario" in _error_line(capsys)
    # a file written by export-scenario plans like the preset with the same overrides
    path = tmp_path / "sc.json"
    assert main(["export-scenario", SC, "--soc", "0.5", "--depart", "06:30", "-o", str(path)]) == 0
    capsys.readouterr()
    assert main(["plan", str(path)]) == 0
    from_file = capsys.readouterr().out
    assert main(["plan", SC, "--soc", "0.5", "--depart", "06:30"]) == 0
    assert capsys.readouterr().out == from_file
    assert main(["export-scenario", SC, "-o", str(tmp_path / "no-dir" / "x.json")]) == 2
    assert "cannot write" in _error_line(capsys)


def test_plan_json_is_standard_json_for_infeasible_plans(tmp_path, capsys):
    """CLI-5: an infeasible plan has infinite time and cost; the file must not contain Infinity."""
    out = tmp_path / "plan.json"
    assert main(["plan", SC, "--reserve", "0.95", "--json", str(out)]) == 2  # infeasible

    def reject(token):
        raise ValueError(f"non-standard JSON constant {token}")

    d = json.loads(out.read_text(), parse_constant=reject)
    assert d["feasible"] is False and d["summary"]["total_time_s"] is None
    assert d["summary"]["generalized_cost"] is None


def test_compare_fails_when_no_planner_produced_a_plan(monkeypatch, capsys):
    import evroute.algorithms as algorithms

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(algorithms, "plan", broken)
    assert main(["compare", SC, "--algorithms", "greedy,bls-astar"]) == 1
    assert capsys.readouterr().err.count("failed (boom)") == 2


def test_serve_reports_a_busy_port(monkeypatch, capsys):
    monkeypatch.setattr(server.ViewerServer, "serve_forever", lambda self: None)  # never block the test
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        assert main(["serve", SC, "--port", str(port)]) == 2
    assert "cannot serve on" in _error_line(capsys)


@pytest.mark.parametrize("argv", [["gallery", "--quick"], ["figures", SC]])
def test_figure_commands_without_seaborn(argv, monkeypatch, tmp_path, capsys):
    import evroute.viz.style as style

    monkeypatch.setattr(style, "has_seaborn", lambda: False)
    monkeypatch.chdir(tmp_path)
    assert main(argv) == 2
    assert "pip install 'evroute[plot]'" in _error_line(capsys)
    assert not any(tmp_path.iterdir())  # nothing written


def test_experiments_without_seaborn_say_that_figures_are_skipped(monkeypatch, capsys):
    import evroute.experiments.runner as runner
    import evroute.viz.style as style

    monkeypatch.setattr(style, "has_seaborn", lambda: False)
    monkeypatch.setattr(runner, "run_experiments", lambda *a, **k: None)
    assert main(["experiments", "vehicles", "--quick", "--no-panel"]) == 0
    assert "figures skipped (pip install 'evroute[plot]')" in capsys.readouterr().err
