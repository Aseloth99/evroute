"""Tests of the command-line interface: bad input is one ``error:`` line with
exit code 2 (never a traceback), and valid commands keep working."""

from __future__ import annotations

import json
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
        (["robustness", SC, "-n", "0"], "-n"),
        (["serve", "nope"], "unknown preset"),
        (["serve", "scenario.json"], "preset name"),
    ],
)
def test_bad_input_is_one_error_line(argv, fragment, capsys):
    assert main(argv) == 2
    assert fragment in _error_line(capsys)


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
