"""Live planning server for the interactive web viewer (standard library only).

Start it with one of::

    python -m evroute.server --scenario turkey-karaman-istanbul --open
    evroute serve turkey-karaman-istanbul --open

and open ``http://127.0.0.1:8765/``.  The page is the same single-file viewer
that :func:`evroute.viz.export.build_viewer_html` writes, switched to *live
mode*: click the map (or use the place pickers) to set origin and
destination, change the vehicle and the trip parameters, tick algorithms and
press *Plan*; the browser posts the request to this server, which plans the
trip with the selected algorithms and returns the evaluated routes.

Endpoints
=========

``GET /``
    The viewer in live mode (CSS, JavaScript and the scenario payload
    inlined; no results yet -- the page requests them on load).
``GET /css/<file>``, ``GET /js/<file>``
    The viewer's own stylesheets and scripts, for development.  Only the
    files listed in :data:`evroute.viz.export.CSS_FILES` and
    :data:`~evroute.viz.export.JS_FILES` are served; nothing else on disk is
    reachable, so there is no path traversal.
``GET /api/health``
    ``{"ok": true, "version": ...}``.
``GET /api/scenarios``
    ``{"default": name, "scenarios": [preset info, ...]}`` -- the presets of
    :mod:`evroute.generators.presets` that are small enough to plan
    interactively (``max_nodes``).
``GET /api/scenario?name=NAME``
    The ``evroute-viewer/1`` payload of preset ``NAME`` without results.
``POST /api/plan``
    Plan a trip.  The JSON body may contain (all optional except where the
    scenario has no default)::

        {"scenario": "turkey-karaman-istanbul", "origin": 12, "destination": 345,
         "algorithms": ["greedy", "bls-astar"], "vehicle": "sedan75",
         "initial_soc": 0.8, "temperature_c": 18, "reserve_soc": 0.1,
         "min_arrival_soc": 0.1, "objective": "time", "value_of_time_per_h": 250,
         "speed_factors": [1.0, 0.9, 0.8], "queue_aware": true, "departure_s": 28800,
         "tariff": "80"}

    ``origin``/``destination`` are node ids or place names, ``departure_s``
    seconds after midnight or ``"HH:MM"``, ``tariff`` ``"80"`` (the
    illustrative :data:`~evroute.generators.presets.DEMO_TARIFF`) or
    ``"flat"`` (no tariff; absent: the preset's own tariff).  The answer is
    :func:`evroute.viz.export.results_payload` plus ``"errors"`` (algorithms
    that failed) and ``"elapsed_ms"``.  Invalid requests get status 400 and
    ``{"error": message}``; bodies above :data:`MAX_BODY_BYTES` get 413.

Planning runs in the request thread of a
:class:`http.server.ThreadingHTTPServer`; the CPU-heavy algorithms hold the
GIL, so concurrent requests are served one after another in practice, which
is what an interactive single-user tool needs.  The server binds to the
loopback interface by default and has no authentication: do not expose it
to an untrusted network.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .scenario import Scenario

__all__ = [
    "MAX_BODY_BYTES",
    "PlanRequestError",
    "ViewerServer",
    "build_plan_scenario",
    "make_server",
    "serve",
]

log = logging.getLogger(__name__)

#: Largest accepted request body [bytes]; a plan request is a few hundred bytes.
MAX_BODY_BYTES = 64 * 1024
#: Presets with more nodes than this are not offered for live planning.
DEFAULT_MAX_NODES = 15000
#: Algorithms planned when a request does not name any.
DEFAULT_ALGORITHMS: tuple[str, ...] = ("greedy", "station-graph", "bls-astar", "cfp-astar")

_PLAN_FIELDS = frozenset(
    {
        "scenario", "origin", "destination", "algorithms", "vehicle", "initial_soc", "temperature_c",
        "reserve_soc", "min_arrival_soc", "objective", "value_of_time_per_h", "speed_factors",
        "queue_aware", "departure_s", "lang", "tariff",
    }
)
_ASSET_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}
_CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


class PlanRequestError(ValueError):
    """A plan request that cannot be honoured (reported as HTTP 400)."""


# ====================================================================== requests
def _number(body: Mapping[str, Any], key: str, lo: float, hi: float) -> float | None:
    """``body[key]`` as a finite float in ``[lo, hi]`` (``None`` when absent)."""
    if key not in body or body[key] is None:
        return None
    v = body[key]
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise PlanRequestError(f"{key} must be a number")
    try:
        x = float(v)
    except ValueError:
        raise PlanRequestError(f"{key} must be a number") from None
    if not math.isfinite(x) or not lo <= x <= hi:
        raise PlanRequestError(f"{key} must lie in [{lo:g}, {hi:g}], got {v!r}")
    return x


def _node(scenario: Scenario, value: Any, key: str) -> int:
    """A node id from an int, a numeric string or a place name."""
    net = scenario.network
    if isinstance(value, bool):
        raise PlanRequestError(f"{key} must be a node id or a place name")
    if isinstance(value, int) or (isinstance(value, str) and value.strip().lstrip("-").isdigit()):
        v = int(value)
        if not 0 <= v < net.num_nodes:
            raise PlanRequestError(f"{key} {v} is not a node of the network (0..{net.num_nodes - 1})")
        return v
    if isinstance(value, str) and value.strip():
        try:
            return net.node_by_name(value)
        except KeyError:
            raise PlanRequestError(f"{key}: no place named {value!r}") from None
    raise PlanRequestError(f"{key} must be a node id or a place name")


def _departure(value: Any) -> float:
    if isinstance(value, str):
        m = _CLOCK.match(value)
        if m:
            h, mi = int(m.group(1)), int(m.group(2))
            if h < 24 and mi < 60:
                return h * 3600.0 + mi * 60.0
            raise PlanRequestError(f"departure_s: invalid clock time {value!r}")
    x = _number({"departure_s": value}, "departure_s", 0.0, 86400.0)
    assert x is not None
    return x % 86400.0


def build_plan_scenario(base: Scenario, body: Mapping[str, Any]) -> tuple[Scenario, list[str]]:
    """Apply a ``POST /api/plan`` body to ``base``; return ``(scenario, algorithms)``.

    Every field is validated (type, range, membership) and unknown fields are
    rejected, so a typo never silently plans a different trip.  The base
    scenario is not modified.  Raises :class:`PlanRequestError`.
    """
    from .algorithms import REGISTRY
    from .vehicle import VEHICLES

    if not isinstance(body, Mapping):
        raise PlanRequestError("the request body must be a JSON object")
    unknown = sorted(set(body) - _PLAN_FIELDS)
    if unknown:
        raise PlanRequestError(f"unknown field(s): {', '.join(unknown)}")
    sc = base

    # ---------------------------------------------------------------- query
    q: dict[str, Any] = {}
    if body.get("origin") is not None:
        q["origin"] = _node(sc, body["origin"], "origin")
    if body.get("destination") is not None:
        q["destination"] = _node(sc, body["destination"], "destination")
    soc = _number(body, "initial_soc", 0.01, 1.0)
    if soc is not None:
        q["initial_soc"] = soc
    if body.get("departure_s") is not None:
        q["departure_s"] = _departure(body["departure_s"])
    if q:
        sc = sc.with_query(**q)
    if sc.query.origin == sc.query.destination:
        raise PlanRequestError("origin and destination must differ")

    # -------------------------------------------------------------- vehicle
    key = body.get("vehicle")
    if key is not None:
        if not isinstance(key, str) or key not in VEHICLES:
            raise PlanRequestError(f"unknown vehicle {key!r}; choose from {sorted(VEHICLES)}")
        sc = sc.with_(vehicle=VEHICLES[key])

    # ----------------------------------------------------------- conditions
    temp = _number(body, "temperature_c", -40.0, 50.0)
    if temp is not None:
        sc = sc.with_conditions(temperature_c=temp)

    # ---------------------------------------------------------- preferences
    p: dict[str, Any] = {}
    for name, lo, hi in (("reserve_soc", 0.0, 0.5), ("min_arrival_soc", 0.0, 0.95), ("value_of_time_per_h", 0.0, 1e6)):
        v = _number(body, name, lo, hi)
        if v is not None:
            p[name] = v
    obj = body.get("objective")
    if obj is not None:
        if obj not in ("time", "cost"):
            raise PlanRequestError("objective must be 'time' or 'cost'")
        p["objective"] = obj
    sf = body.get("speed_factors")
    if sf is not None:
        if not isinstance(sf, list) or not 1 <= len(sf) <= 5:
            raise PlanRequestError("speed_factors must be a list of 1 to 5 numbers")
        vals = [_number({"speed_factors": x}, "speed_factors", 0.5, 1.0) for x in sf]
        p["speed_factors"] = tuple(sorted({float(x) for x in vals if x is not None}, reverse=True))
    qa = body.get("queue_aware")
    if qa is not None:
        if not isinstance(qa, bool):
            raise PlanRequestError("queue_aware must be true or false")
        p["queue_aware"] = qa
    if p:
        sc = sc.with_preferences(**p)

    # --------------------------------------------------------------- tariff
    tariff = body.get("tariff")
    if tariff is not None:
        from .generators.presets import DEMO_TARIFF
        from .scenario import Tariff

        if tariff == "80":
            sc = sc.with_(tariff=DEMO_TARIFF)
        elif tariff == "flat":
            sc = sc.with_(tariff=Tariff())
        else:
            raise PlanRequestError("tariff must be '80' (the demonstration tariff) or 'flat' (no tariff)")

    # ------------------------------------------------------------ algorithms
    algos = body.get("algorithms")
    if algos is None:
        names = [a for a in DEFAULT_ALGORITHMS if a in REGISTRY]
    else:
        if not isinstance(algos, list) or not all(isinstance(a, str) for a in algos):
            raise PlanRequestError("algorithms must be a list of names")
        bad = [a for a in algos if a not in REGISTRY]
        if bad:
            raise PlanRequestError(f"unknown algorithm(s) {bad}; choose from {list(REGISTRY)}")
        names = list(dict.fromkeys(algos))
        if not names:
            raise PlanRequestError("select at least one algorithm")
    return sc, names


# ====================================================================== server
class ViewerServer(ThreadingHTTPServer):
    """HTTP server holding the scenario cache and the viewer page cache."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        scenario_name: str,
        *,
        lang: str = "tr",
        max_nodes: int = DEFAULT_MAX_NODES,
        robustness_runs: int = 200,
    ) -> None:
        from .generators.presets import PRESETS

        if scenario_name not in PRESETS:
            raise KeyError(f"unknown scenario {scenario_name!r}; choose from {sorted(PRESETS)}")
        self.default_scenario = scenario_name
        self.lang = lang if lang in ("tr", "en") else "tr"
        self.max_nodes = max_nodes
        self.robustness_runs = robustness_runs
        self._lock = threading.Lock()
        self._scenarios: dict[str, Scenario] = {}
        self._pages: dict[str, bytes] = {}
        super().__init__(address, ViewerRequestHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        return f"http://{host}:{port}/"

    # ------------------------------------------------------------ caches
    def preset_names(self) -> list[dict[str, Any]]:
        from .generators.presets import list_scenarios

        out = []
        for info in list_scenarios():
            n = info.get("approx_nodes")
            if n is not None and n > self.max_nodes and info["name"] != self.default_scenario:
                continue
            out.append({k: info[k] for k in ("name", "description", "family", "tags", "approx_nodes")})
        return out

    def base_scenario(self, name: str | None) -> Scenario:
        """The preset ``name`` (default: the server's scenario), built once."""
        from .generators.presets import get_scenario

        name = name or self.default_scenario
        if not isinstance(name, str) or name not in {p["name"] for p in self.preset_names()}:
            raise PlanRequestError(f"unknown or unavailable scenario {name!r}")
        with self._lock:
            sc = self._scenarios.get(name)
            if sc is None:
                sc = get_scenario(name)
                self._scenarios[name] = sc
        return sc

    def scenario_payload(self, name: str | None) -> dict[str, Any]:
        """Live-mode viewer payload without results."""
        from .viz.export import viewer_payload

        from .generators.presets import DEMO_TARIFF

        sc = self.base_scenario(name)
        return viewer_payload(
            sc, [], lang=self.lang, animate=True, live=True,
            extra={"scenario_preset": name or self.default_scenario, "demo_tariff": DEMO_TARIFF.to_dict()},
        )

    def page(self) -> bytes:
        """The live viewer page (built once)."""
        from .viz.export import render_viewer_html

        with self._lock:
            cached = self._pages.get(self.default_scenario)
        if cached is None:
            cached = render_viewer_html(self.scenario_payload(None)).encode("utf-8")
            with self._lock:
                self._pages[self.default_scenario] = cached
        return cached

    def plan(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Handle a ``POST /api/plan`` body (see the module docs)."""
        from .viz.export import results_payload, run_all

        if not isinstance(body, Mapping):
            raise PlanRequestError("the request body must be a JSON object")
        name = body.get("scenario") or self.default_scenario
        sc, algos = build_plan_scenario(self.base_scenario(name), body)
        errors: list[dict[str, str]] = []

        def report(algo: str, exc: BaseException) -> None:
            log.warning("algorithm %s failed: %r", algo, exc)
            errors.append({"algorithm": algo, "message": str(exc) or type(exc).__name__})

        t0 = time.perf_counter()
        plans = run_all(sc, algos, trace=True, on_error=report)
        data = results_payload(sc, plans, robustness_runs=self.robustness_runs)
        data["errors"] = errors
        data["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        data["scenario_preset"] = name
        return data


class ViewerRequestHandler(BaseHTTPRequestHandler):
    """Routes of :class:`ViewerServer` (see the module docs)."""

    server: ViewerServer
    server_version = "EVRouteViewer/1"
    protocol_version = "HTTP/1.1"

    # ----------------------------------------------------------- helpers
    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover - quiet by default
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str, *, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "max-age=60" if cache else "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # ------------------------------------------------------------ routes
    def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        try:
            url = urlsplit(self.path)
            path = url.path
            if path in ("/", "/index.html"):
                self._send(HTTPStatus.OK, self.server.page(), "text/html; charset=utf-8")
            elif path == "/api/health":
                from . import __version__

                self._json(HTTPStatus.OK, {"ok": True, "version": __version__})
            elif path == "/api/scenarios":
                self._json(HTTPStatus.OK, {"default": self.server.default_scenario, "scenarios": self.server.preset_names()})
            elif path == "/api/scenario":
                name = parse_qs(url.query).get("name", [None])[0]
                self._json(HTTPStatus.OK, self.server.scenario_payload(name))
            elif path.startswith(("/css/", "/js/")):
                self._asset(path)
            elif path == "/favicon.ico":
                self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except PlanRequestError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            log.exception("GET %s failed", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def _asset(self, path: str) -> None:
        """Serve a whitelisted viewer asset (no file-system paths from the URL)."""
        from .viz.export import CSS_FILES, JS_FILES, WEB_DIR

        folder, _, name = path.lstrip("/").partition("/")
        allowed = CSS_FILES if folder == "css" else JS_FILES
        if name not in allowed:
            self._error(HTTPStatus.NOT_FOUND, "not found")
            return
        body = (WEB_DIR / folder / name).read_bytes()
        self._send(HTTPStatus.OK, body, _ASSET_TYPES[name[name.rfind("."):]], cache=True)

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        try:
            if urlsplit(self.path).path != "/api/plan":
                self._error(HTTPStatus.NOT_FOUND, "not found")
                return
            length_hdr = self.headers.get("Content-Length")
            if length_hdr is None:
                self._error(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
                return
            try:
                length = int(length_hdr)
            except ValueError:
                length = -1
            if length < 0:  # the body cannot be delimited: drop the connection
                self.close_connection = True
                self._error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
                return
            if length > MAX_BODY_BYTES:
                self.close_connection = True
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request body larger than {MAX_BODY_BYTES} bytes")
                return
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:  # RecursionError: deep nesting
                self._error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {exc}")
                return
            self._json(HTTPStatus.OK, self.server.plan(body))
        except PlanRequestError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except (KeyError, ValueError) as exc:
            # scenario construction rejected the parameters
            self._error(HTTPStatus.BAD_REQUEST, str(exc).strip("'\""))
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            log.exception("POST %s failed", self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error")

    def _method_not_allowed(self) -> None:
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = _method_not_allowed  # noqa: N815


# ====================================================================== entry points
def make_server(
    scenario_name: str = "turkey-karaman-istanbul",
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    lang: str = "tr",
    max_nodes: int = DEFAULT_MAX_NODES,
    robustness_runs: int = 200,
) -> ViewerServer:
    """Create (but do not start) the server; ``port=0`` picks a free port.

    Run it with ``server.serve_forever()`` (e.g. in a thread) and stop it
    with ``server.shutdown(); server.server_close()``.
    """
    return ViewerServer(
        (host, port), scenario_name, lang=lang, max_nodes=max_nodes, robustness_runs=robustness_runs
    )


def serve(
    scenario_name: str = "turkey-karaman-istanbul",
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    *,
    lang: str = "tr",
) -> None:
    """Serve the live viewer until interrupted (Ctrl+C)."""
    server = make_server(scenario_name, host, port, lang=lang)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: listening on {host}; the server has no authentication", file=sys.stderr)
    print(f"EVRoute viewer: {server.url}  (scenario {scenario_name}; Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive
        print("\nstopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evroute.server", description="Live EV route planning in the browser.")
    ap.add_argument("--scenario", default="turkey-karaman-istanbul", help="preset name (see evroute scenarios)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--lang", default="tr", choices=["tr", "en"])
    ap.add_argument("--open", action="store_true", help="open a browser tab")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    serve(args.scenario, args.host, args.port, args.open, lang=args.lang)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
