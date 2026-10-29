# Contributing to EVRoute

Thank you for considering a contribution. EVRoute aims to be a *reference
implementation*, so correctness and clarity come before speed and features.

## Set-up

```bash
git clone https://github.com/Aseloth99/evroute.git
cd evroute
pip install -e ".[dev]"            # pytest + seaborn
python -m pytest -m "not slow"     # fast tier, a few minutes
```

The slow cross-validation tier (`python -m pytest -m slow tests/test_crossval.py
tests/test_crossval_tariffs.py`) throws thousands of random instances, with
and without charging tariffs, at every planner; run it before you touch an
algorithm.  Before you change the web site's live planner (`site/`,
`live.js`, `server.py`), also run
`EVROUTE_BROWSER_TESTS=1 python -m pytest -m slow tests/test_site.py`: it plans
in Chromium under Pyodide and compares with `evroute plan` (needs Node.js with
Playwright and access to cdn.jsdelivr.net).

## Ground rules

- **The core stays pure standard-library Python ≥ 3.10.** Charts are drawn
  with `seaborn` only, and only in `viz/figures.py`, `viz/style.py` and
  `experiments/`, imported lazily; optional loaders such as `osmnx` are
  imported inside functions with a helpful error message.
- **Fixed units** (`src/evroute/units.py`): m, s, kWh, kW, m/s, currency, SoC
  fractions. Never mix them.
- **Determinism**: anything random takes a `seed` and uses
  `random.Random(seed)`.
- Type hints, docstrings for modules and public functions, small functions;
  match the style of the surrounding code.

## Project structure

```
evroute/
├── src/evroute/
│   ├── units.py network.py vehicle.py energy.py   # model: units, graph, vehicles, physics
│   ├── charging.py queueing.py scenario.py         # charging functions, Erlang-C queues, scenarios
│   ├── plan.py evaluate.py search.py               # plans, independent evaluator, bounds
│   ├── algorithms/      # naive, greedy, station_graph, genetic, labels (BLS), cfp, pareto
│   ├── generators/      # basic grids, turkey, osm_turkey (real data), synthetic, osm, presets
│   ├── data/            # Turkey places and roads (CSV); osm_turkey/: the real-data network (ODbL)
│   ├── robustness.py    # Monte Carlo reliability, safety-factor sweeps
│   ├── viz/             # export.py + web/ (viewer), results.py (dashboard), figures.py + style.py (seaborn)
│   ├── experiments/     # runner, plots, figures for the paper
│   ├── server.py        # live planning server (http.server)
│   └── cli.py           # `evroute` command
├── tests/               # pytest suite + fuzz_report.md
├── tools/               # build_osm_turkey.py: rebuilds data/osm_turkey from OpenStreetMap;
│                        # build_site.py: builds the web site (GitHub Pages, .github/workflows/pages.yml)
├── site/                # web site sources: landing and examples pages, worker.js (EVRoute under Pyodide)
├── examples/            # quickstart.py, custom_network.py, viewer_demo.py
└── docs/                # index, guide (EN/TR), real-data (EN/TR), model, algorithms, DEV_SPEC, PLAN.tr, demo/ (viewer + dashboard), img/
```

## Adding or changing a planner

1. Subclass `evroute.algorithms.base.Planner`, implement
   `_plan(prepared, *, trace)` and register it in `algorithms/__init__.py`.
2. Return `evaluate(P, decision, algorithm=..., internal_objective=..., stats=...)`
   - never build a `RoutePlan` yourself. The independent evaluator is the
   judge: an exact planner must report exactly the objective it measures.
3. Respect the semantics of `docs/DEV_SPEC.md` §1.2 (reserve on arrival and
   departure, regeneration clamp, waits and set-up at every listed stop).
4. If you claim a guarantee, write it down with a proof sketch in
   `docs/algorithms.md` and add it to the cross-validation invariants in
   `tests/test_crossval.py`; for a heuristic, show it is never better than
   the exact planners on the same model.

## Bugs and questions

Please open an issue with the scenario (`evroute export-scenario ... -o bug.json`
or the seed printed by a failing cross-validation test), the planner and its
options, and what you expected. A failing test that reproduces the bug is the
best possible report; the suite keeps such reproductions as
`xfail(strict=True)` until the fix lands.

## Documentation and figures

- `docs/model.md` and `docs/algorithms.md` state the model and the proofs;
  keep them in sync with the code.  `docs/guide.md` and `docs/guide.tr.md`
  explain the algorithms for newcomers; every number in them comes from the
  code or the READMEs, so update them when a quoted result changes.
- Regenerate the static figures with `evroute gallery -o docs/img` and the
  viewer screenshots and the GIF with `python examples/viewer_demo.py --screenshots docs/img`
  (Node.js + Playwright).
- `README.md` and `README.tr.md` are translations of each other, and so
  are `docs/guide.md` / `.tr.md` and `docs/real-data.md` / `.tr.md`; please
  update both, or say in the pull request that the other one needs an update.

By contributing you agree that your contribution is licensed under the
[MIT licence](LICENSE); changes to the bundled data in
`src/evroute/data/osm_turkey/` are under the ODbL 1.0
([`DATA_LICENSE.md`](src/evroute/data/osm_turkey/DATA_LICENSE.md)).
