"""Charging stations from operator and open data: CSV, OCPI 2.2.1, Open Charge Map.

The bundled real-data network of Turkey takes its stations from
OpenStreetMap (:mod:`evroute.generators.osm_turkey`), which holds only a few
per cent of the public charge points.  Operators keep the complete and live
picture, and the adapters of this module turn the formats they publish into
:class:`~evroute.scenario.Station` lists for any network:

:func:`stations_from_csv`
    a documented generic table (:data:`CSV_COLUMNS`: your own survey, an export);
:func:`stations_from_ocpi`
    OCPI 2.2.1 ``Location`` objects -- the roaming standard operators speak --
    with a **live availability snapshot** of the EVSEs, optionally priced by
    OCPI ``Tariff`` objects;
:func:`stations_from_ocm`
    Open Charge Map POIs (the JSON of its public API);
:func:`load_stations`
    one of the three from a file (format detected);
:func:`with_stations`
    a scenario with the imported stations instead of its own.

The library never goes online: fetch the data with the provider's API and
your own credentials, save it, and hand the file or the parsed JSON to these
functions (``evroute plan ... --stations-file FILE`` on the command line).

Common rules
============

*Snapping.*  A station is attached to the nearest network node within
``max_snap_m`` (default 3000 m, as for the bundled OSM stations); farther
records are reported in :attr:`ImportedStations.skipped`.  WGS84 positions
(``lat``/``lon``) need a network with node coordinates
(:attr:`~evroute.network.RoadNetwork.lat`): the nearest node is the
great-circle nearest of the 8 nearest nodes in a local equirectangular
projection.  A synthetic network without coordinates takes positions in its
own frame (CSV columns ``x``/``y`` in metres) or node ids (column ``node``).

*One station per kind.*  The model charges at a station with one charging
function (:attr:`~evroute.scenario.Station.kind` AC or DC), so an OCPI
location or an OCM POI with DC and AC charge points becomes two stations at
the same node: a car without a DC inlet can still use the site's AC points.
``power_kw`` is the largest power of the kind at the site, ``plugs`` the
number of charge points that can serve a vehicle at the same time (OCPI:
EVSEs in service; OCM: the ``Quantity`` of the connections) and
``connectors`` the standards of that kind
(:data:`evroute.vehicle.CONNECTORS`).  A CSV row is one station.

*Prices.*  An explicit price wins (CSV ``price_per_kwh``, an OCPI tariff);
otherwise the operator's verified AC/DC list price of the Turkish price
snapshot ``prices.json`` (:func:`evroute.generators.osm_turkey.station_price`;
the operator name must equal a known operator or contain it as a word
sequence, ignoring case and Turkish letters, e.g. ``"ZES (Zorlu Energy
Solutions)"``), otherwise ``default_price`` (default
:data:`~evroute.generators.osm_turkey.DEFAULT_PRICE_TRY`).  These prices are
in TRY; pass ``operator_prices=False`` and your own ``default_price`` for
another currency.

*Queues.*  Every imported station gets the queue assumptions of the OSM
stations (:data:`~evroute.generators.osm_turkey.QUEUE_MODEL`, national
averages derived from the EPDK statistics: sessions per plug and day, mean
session length, hourly profile) unless the CSV gives its own.  Live
occupancy (OCPI EVSE status) is reported, not yet used by the queue model.

Licences
========

Data keep the licence of their source; the adapters do not change it.
**Open Charge Map** data are published under CC BY-SA 4.0: credit "Open
Charge Map" (openchargemap.org) and share adapted data under the same
licence; a POI imported from another data provider may carry that provider's
terms (``DataProvider.License`` in the POI).  **OCPI** feeds are governed by
the agreement with the operator or roaming hub that gives you access.
Scraping apps or web sites against their terms is not a data source.
:func:`with_stations` appends the credit of the source to
``scenario.meta["attribution"]``, which the viewer shows.
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .network import RoadNetwork
from .scenario import Scenario, Station
from .units import haversine_m, project_equirectangular
from .vehicle import CONNECTORS

__all__ = [
    "CSV_COLUMNS",
    "DEFAULT_MAX_SNAP_M",
    "FORMATS",
    "OCM_ATTRIBUTION",
    "OCM_CONNECTION_TYPES",
    "OCPI_STANDARDS",
    "ImportedStations",
    "detect_format",
    "load_stations",
    "normalise_connector",
    "stations_from_csv",
    "stations_from_ocm",
    "stations_from_ocpi",
    "with_stations",
]

#: Formats of :func:`load_stations`.
FORMATS = ("csv", "ocpi", "ocm")
#: Default snapping radius [m] (the bundled OSM stations use the same).
DEFAULT_MAX_SNAP_M = 3000.0
#: Credit line of Open Charge Map data.
OCM_ATTRIBUTION = "Charging stations: Open Charge Map (openchargemap.org), CC BY-SA 4.0"
#: Default power of a charge point whose power is not given [kW] (the defaults of the OSM build).
DEFAULT_POWER_KW: dict[str, float] = {"AC": 22.0, "DC": 120.0}

#: Columns of the generic CSV schema (header names are case-insensitive).
CSV_COLUMNS: dict[str, str] = {
    "lat, lon": "WGS84 position (needs a network with coordinates), or",
    "x, y": "position in the network's frame [m], or",
    "node": "the network node id itself",
    "kind": "AC or DC (default: DC if a connector is DC-only or power_kw > 22, else AC)",
    "power_kw": "largest charging power [kW] (required)",
    "plugs": "vehicles served at the same time (default 1)",
    "connectors": "standards separated by ';' or '|' ('|' in a ';'-delimited file), e.g. 'CCS2;CHAdeMO' "
                  "(default: the kind's)",
    "operator": "operator name (prices.json lookup; default 'unknown')",
    "name": "station name (default: operator and kind)",
    "price_per_kwh": "price per kWh, or 'price' (default: operator price, else default_price)",
    "id": "your identifier, kept in the import info",
    "setup_s, daily_sessions, mean_service_min, demand_profile": "optional Station fields",
}

#: OCPI 2.2.1 ``ConnectorType`` -> connector standard (other types are not vehicle
#: connectors of the model: household and industrial sockets, pantographs ...).
#: ``TESLA_S`` is the Tesla "Model S" connector that SAE J3400 standardised as NACS.
OCPI_STANDARDS: dict[str, str] = {
    "IEC_62196_T2": "Type2",
    "IEC_62196_T2_COMBO": "CCS2",
    "CHADEMO": "CHAdeMO",
    "IEC_62196_T1": "Type1",
    "IEC_62196_T1_COMBO": "CCS1",
    "TESLA_S": "NACS",
}
#: OCPI EVSE states that do not count as charge points at all.
_OCPI_GONE = frozenset({"REMOVED", "PLANNED"})
#: OCPI EVSE states of charge points that exist but cannot charge now.
_OCPI_DOWN = frozenset({"INOPERATIVE", "OUTOFORDER"})

#: Open Charge Map ``ConnectionTypeID`` -> connector standard (the common types; a
#: connection of another type is matched by its ``ConnectionType.Title`` if present).
OCM_CONNECTION_TYPES: dict[int, str] = {
    1: "Type1",  # Type 1 (J1772)
    2: "CHAdeMO",
    25: "Type2",  # Type 2 (Socket Only)
    1036: "Type2",  # Type 2 (Tethered Connector)
    32: "CCS1",  # CCS (Type 1)
    33: "CCS2",  # CCS (Type 2)
}
#: Open Charge Map ``CurrentTypeID``: 10 AC single phase, 20 AC three phase, 30 DC.
_OCM_CURRENT = {10: "AC", 20: "AC", 30: "DC"}

#: Spellings accepted for the connector standards (lower case, without spaces, ``-`` and ``_``).
_CONNECTOR_ALIASES: dict[str, str] = {
    "type2": "Type2", "t2": "Type2", "mennekes": "Type2", "iec62196t2": "Type2", "type2cable": "Type2",
    "type1": "Type1", "t1": "Type1", "j1772": "Type1", "iec62196t1": "Type1",
    "ccs2": "CCS2", "ccs": "CCS2", "combo2": "CCS2", "type2combo": "CCS2", "ccstype2": "CCS2",
    "iec62196t2combo": "CCS2",
    "ccs1": "CCS1", "combo1": "CCS1", "type1combo": "CCS1", "ccstype1": "CCS1", "iec62196t1combo": "CCS1",
    "chademo": "CHAdeMO",
    "nacs": "NACS", "j3400": "NACS", "saej3400": "NACS",
}


def normalise_connector(name: str) -> str | None:
    """The standard (:data:`~evroute.vehicle.CONNECTORS` key) of a connector name in
    any usual spelling (``"ccs"``, ``"Type 2"``, ``"type2_combo"``, ``"CHAdeMO"`` ...),
    ``None`` if it is not one."""
    if name in CONNECTORS:
        return name
    return _CONNECTOR_ALIASES.get("".join(ch for ch in str(name).lower() if ch.isalnum()))


# ====================================================================== result
@dataclass
class ImportedStations:
    """Stations imported by one adapter.

    ``stations[i]`` has ``id == i``; ``info[i]`` tells where it came from
    (``source``, the record id, ``snap_m``, the sources of the price and the
    power); ``availability[i]`` is the live snapshot of an OCPI station
    (``{"available", "total", "out_of_service", "status": {...}, "last_updated"}``,
    counted in EVSEs) and ``None`` elsewhere; ``skipped`` lists the records
    that were not imported, each with its ``reason``: ``"too far from the
    network"``, ``"no coordinates"``, ``"no usable connector"`` (no connector
    standard of the model, or none operational), ``"no EVSE in service"``,
    ``"not published"`` (OCPI ``publish: false``), ``"not operational"``
    (OCM) or ``"invalid record"``.
    """

    stations: list[Station] = field(default_factory=list)
    info: list[dict[str, Any]] = field(default_factory=list)
    availability: list[dict[str, Any] | None] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    #: ``"csv"``, ``"ocpi"`` or ``"ocm"``.
    source: str = ""

    def __len__(self) -> int:
        return len(self.stations)

    def summary(self) -> dict[str, Any]:
        """Counts for a log line: stations (AC/DC), skipped records by reason."""
        reasons: dict[str, int] = {}
        for s in self.skipped:
            reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
        return {
            "source": self.source,
            "stations": len(self.stations),
            "ac": sum(s.kind == "AC" for s in self.stations),
            "dc": sum(s.kind == "DC" for s in self.stations),
            "skipped": len(self.skipped),
            "skipped_by_reason": reasons,
        }

    def _add(self, station: Station, info: dict[str, Any], availability: dict[str, Any] | None = None) -> None:
        station.id = len(self.stations)
        self.stations.append(station)
        self.info.append(info)
        self.availability.append(availability)


# ====================================================================== snapping
class _Snapper:
    """Nearest network node of a WGS84 position or of a point in the network frame."""

    def __init__(self, network: RoadNetwork, max_snap_m: float) -> None:
        from .generators.synthetic import GridIndex

        if not (max_snap_m > 0.0 and math.isfinite(max_snap_m)):
            raise ValueError(f"max_snap_m must be a finite number > 0, got {max_snap_m!r}")
        if network.num_nodes == 0:
            raise ValueError("the network has no nodes")
        self.net = network
        self.max_snap_m = float(max_snap_m)
        self._grid_xy = None
        self._grid_geo = None
        self._geo_ref: tuple[float, float] = (0.0, 0.0)
        self._GridIndex = GridIndex

    @property
    def has_geo(self) -> bool:
        return self.net.lat is not None and self.net.lon is not None

    def geo(self, lat: float, lon: float) -> tuple[int, float] | None:
        """``(node, distance [m])`` of the nearest node within ``max_snap_m``, else ``None``."""
        net = self.net
        if not self.has_geo:
            raise ValueError("the network has no node coordinates (lat/lon): give x/y or node positions instead")
        if self._grid_geo is None:
            lat0 = sum(net.lat) / len(net.lat)  # type: ignore[arg-type]
            lon0 = sum(net.lon) / len(net.lon)  # type: ignore[arg-type]
            self._geo_ref = (lat0, lon0)
            pts = [project_equirectangular(a, b, lat0, lon0)
                   for a, b in zip(net.lat, net.lon)]  # type: ignore[arg-type]
            self._grid_geo = self._GridIndex([p[0] for p in pts], [p[1] for p in pts])
        x, y = project_equirectangular(lat, lon, *self._geo_ref)
        best: tuple[int, float] | None = None
        for _, i in self._grid_geo.k_nearest(x, y, 8):
            d = haversine_m(lat, lon, net.lat[i], net.lon[i])  # type: ignore[index]
            if best is None or d < best[1]:
                best = (i, d)
        return best if best is not None and best[1] <= self.max_snap_m else None

    def xy(self, x: float, y: float) -> tuple[int, float] | None:
        """The same for a point in the network frame [m]."""
        if self._grid_xy is None:
            self._grid_xy = self._GridIndex(self.net.x, self.net.y)
        i = self._grid_xy.nearest(x, y)
        d = math.hypot(self.net.x[i] - x, self.net.y[i] - y)
        return (i, d) if d <= self.max_snap_m else None


# ====================================================================== prices
class _Prices:
    """Price of a station: explicit > operator list price (prices.json) > default."""

    def __init__(self, operator_prices: bool, default_price: float | Mapping[str, float] | None) -> None:
        from .generators.osm_turkey import DEFAULT_PRICE_TRY, osm_turkey_prices

        self.operator_prices = operator_prices
        if default_price is None:
            default_price = DEFAULT_PRICE_TRY
        if isinstance(default_price, Mapping):
            self.default = {"AC": float(default_price["AC"]), "DC": float(default_price["DC"])}
        else:
            self.default = {"AC": float(default_price), "DC": float(default_price)}
        for k, v in self.default.items():
            if not (v >= 0.0 and math.isfinite(v)):
                raise ValueError(f"default_price[{k!r}] must be a finite number >= 0, got {v!r}")
        self._known: list[tuple[list[str], str]] = []
        if operator_prices:
            for name in osm_turkey_prices()["operators"]:
                self._known.append((_words(name), name))
            self._known.sort(key=lambda kv: -len(kv[0]))  # the longest name first: it wins over a word of it
        self._cache: dict[str, str | None] = {}

    def operator_key(self, operator: str) -> str | None:
        """The operator of ``prices.json`` that ``operator`` names, if any."""
        if operator in self._cache:
            return self._cache[operator]
        words = _words(operator)
        hit = None
        for key_words, name in self._known:
            n = len(key_words)
            if n and any(words[i:i + n] == key_words for i in range(len(words) - n + 1)):
                hit = name
                break
        if hit is None and words:
            joined = "".join(words)
            hit = next((name for kw, name in self._known if "".join(kw) == joined), None)
        self._cache[operator] = hit
        return hit

    def price(self, operator: str, kind: str) -> tuple[float, str]:
        """``(price per kWh, source)``: ``"operator"`` (prices.json) or ``"default"``."""
        from .generators.osm_turkey import station_price

        if self.operator_prices:
            key = self.operator_key(operator)
            if key is not None:
                p, src = station_price(key, kind)
                if src == "operator":
                    return p, "operator"
        return self.default[kind], "default"


def _words(text: str) -> list[str]:
    """Lower-case words of ``text`` with Turkish letters folded (``"Eşarj A.Ş."`` -> ``['esarj', 'a', 's']``)."""
    from .generators.turkey import _norm

    out, cur = [], []
    for ch in str(text):
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append(_norm("".join(cur)))
            cur = []
    if cur:
        out.append(_norm("".join(cur)))
    return [w for w in out if w]


# ====================================================================== station factory
def _station(
    node: int, kind: str, power_kw: float, plugs: int, price: float, name: str, operator: str,
    connectors: Sequence[str], **fields: Any,
) -> Station:
    """A station with the queue assumptions of the OSM stations (fields override them)."""
    from .generators.osm_turkey import DEMAND_PROFILE, QUEUE_MODEL, SETUP_S

    q = QUEUE_MODEL[kind]
    default_kind = ("Type2",) if kind == "AC" else ("CCS2",)
    conns = tuple(c for c in CONNECTORS if c in set(connectors))
    st = Station(
        id=0,
        node=int(node),
        power_kw=round(float(power_kw), 2),
        kind=kind,  # type: ignore[arg-type]
        plugs=int(plugs),
        price_per_kwh=round(float(price), 4),
        name=name,
        operator=operator,
        daily_sessions=round(q["sessions_per_plug_day"] * int(plugs), 2),
        demand_profile=list(DEMAND_PROFILE),
        mean_service_min=q["mean_service_min"],
        setup_s=SETUP_S[kind],
        connectors=() if conns == default_kind else conns,
    )
    for k, v in fields.items():
        setattr(st, k, v)
    return st


def _site_name(name: str, operator: str, kind: str) -> str:
    base = name.strip() if name else ""
    if not base:
        base = f"{operator} {kind}" if operator and operator != "unknown" else f"{kind} charger"
    return base


# ====================================================================== CSV
def stations_from_csv(
    source: str | Path | Iterable[Mapping[str, Any]],
    network: RoadNetwork,
    *,
    max_snap_m: float = DEFAULT_MAX_SNAP_M,
    operator_prices: bool = True,
    default_price: float | Mapping[str, float] | None = None,
) -> ImportedStations:
    """Stations from a table in the generic schema :data:`CSV_COLUMNS`.

    ``source`` is a CSV file (UTF-8, header row; the delimiter ``,``, ``;``
    or tab is taken from the header, and ``1,5`` reads as 1.5 when the
    delimiter is not a comma) or rows as mappings (e.g. from
    :class:`csv.DictReader`).  Every row is one station; empty rows are
    ignored.  A row needs a position (``lat``/``lon``, ``x``/``y`` or
    ``node``) and ``power_kw``; ``price`` is accepted for ``price_per_kwh``.
    Values are validated, and a bad value raises :class:`ValueError` naming
    the row (a typo never passes silently).  Rows farther than
    ``max_snap_m`` from every node are skipped.

    (:func:`evroute.generators.osm.stations_from_csv`, the reader of 0.x for
    networks loaded with ``load_osm``, stays as it was: it returns a plain
    list with its own defaults and knows no connectors.)
    """
    if isinstance(source, (str, Path)):
        text = Path(source).read_text(encoding="utf-8-sig")
        head = text.splitlines()[0] if text else ""
        delimiter = max((",", ";", "\t"), key=head.count)
        rows: list[Mapping[str, Any]] = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    else:
        rows = list(source)
    snap = _Snapper(network, max_snap_m)
    prices = _Prices(operator_prices, default_price)
    out = ImportedStations(source="csv")
    for n, raw in enumerate(rows, start=1):
        row = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v)
               for k, v in raw.items() if k is not None}
        if not any(v not in (None, "") for v in row.values()):
            continue
        if row.get("price_per_kwh") in (None, "") and row.get("price") not in (None, ""):
            row["price_per_kwh"] = row["price"]
        where = f"row {n}"

        def num(key: str, *, required: bool = False, lo: float = -math.inf) -> float | None:
            v = row.get(key)
            if v is None or v == "":
                if required:
                    raise ValueError(f"{where}: {key} is required")
                return None
            if isinstance(v, str) and v.count(",") == 1 and "." not in v:
                v = v.replace(",", ".")  # decimal comma
            try:
                x = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{where}: {key} must be a number, got {v!r}") from None
            if not math.isfinite(x) or x < lo:
                raise ValueError(f"{where}: {key} must be a finite number >= {lo:g}, got {v!r}")
            return x

        # ------------------------------------------------ position
        lat, lon, x, y, node = num("lat"), num("lon"), num("x"), num("y"), num("node")
        if lat is not None and lon is not None:
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                raise ValueError(f"{where}: lat/lon {lat}, {lon} out of range")
            hit = snap.geo(lat, lon)
        elif x is not None and y is not None:
            hit = snap.xy(x, y)
        elif node is not None:
            if node != int(node) or not 0 <= node < network.num_nodes:
                raise ValueError(f"{where}: node {row.get('node')!r} is not a node of the network")
            hit = (int(node), 0.0)
        else:
            raise ValueError(f"{where}: needs lat/lon, x/y or node")
        # ------------------------------------------------ charger
        power = num("power_kw", required=True, lo=0.0)
        assert power is not None
        if power <= 0.0:
            raise ValueError(f"{where}: power_kw must be > 0")
        conns: list[str] = []
        for c in str(row.get("connectors") or "").replace("|", ";").split(";"):
            if c.strip():
                std = normalise_connector(c.strip())
                if std is None:
                    raise ValueError(f"{where}: unknown connector {c.strip()!r}; use one of {list(CONNECTORS)}")
                conns.append(std)
        kind = str(row.get("kind") or "").upper()
        if not kind:
            dc_only = any(CONNECTORS[c] == ("DC",) for c in conns)
            kind = "DC" if dc_only or power > 22.0 else "AC"
        if kind not in ("AC", "DC"):
            raise ValueError(f"{where}: kind must be AC or DC, got {row.get('kind')!r}")
        if conns and not any(kind in CONNECTORS[c] for c in conns):
            raise ValueError(f"{where}: none of the connectors {conns} carries {kind}")
        plugs = num("plugs", lo=1.0)
        if plugs is not None and plugs != int(plugs):
            raise ValueError(f"{where}: plugs must be a whole number")
        operator = str(row.get("operator") or "unknown")
        explicit = num("price_per_kwh", lo=0.0)
        price, price_src = (explicit, "file") if explicit is not None else prices.price(operator, kind)
        extra: dict[str, Any] = {}
        for key in ("setup_s", "daily_sessions"):
            v = num(key, lo=0.0)
            if v is not None:
                extra[key] = v
        v = num("mean_service_min", lo=0.0)
        if v is not None:
            if v <= 0.0:
                raise ValueError(f"{where}: mean_service_min must be > 0")
            extra["mean_service_min"] = v
        prof = row.get("demand_profile")
        if prof:
            if prof not in ("highway", "urban"):
                raise ValueError(f"{where}: demand_profile must be 'highway' or 'urban', got {prof!r}")
            extra["demand_profile"] = prof
        # every value is checked before the row may be skipped: a typo never hides behind the distance
        ident = row.get("id") or str(n)
        if hit is None:
            out.skipped.append({"record": ident, "reason": "too far from the network", "max_snap_m": max_snap_m})
            continue
        name = _site_name(str(row.get("name") or ""), operator, kind)
        st = _station(hit[0], kind, power, int(plugs or 1), price, name, operator, conns, **extra)
        out._add(st, {"source": "csv", "record": ident, "snap_m": round(hit[1], 1), "price": price_src})
    return out


# ====================================================================== OCPI
def _ocpi_power_kw(conn: Mapping[str, Any]) -> float | None:
    """Power of an OCPI connector [kW]: ``max_electric_power`` [W], else voltage x current
    (x 3 for three-phase AC, whose ``max_voltage`` is line to neutral)."""
    p = conn.get("max_electric_power")
    if isinstance(p, (int, float)) and not isinstance(p, bool) and p > 0:
        return float(p) / 1000.0
    v, a = conn.get("max_voltage"), conn.get("max_amperage")
    if isinstance(v, (int, float)) and isinstance(a, (int, float)) and v > 0 and a > 0:
        phases = 3 if conn.get("power_type") == "AC_3_PHASE" else 2 if conn.get("power_type") == "AC_2_PHASE" else 1
        return float(v) * float(a) * phases / 1000.0
    return None


def _ocpi_tariff_prices(
    tariffs: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None, currency: str | None
) -> dict[str, float]:
    """``tariff id -> energy price per kWh incl. VAT`` of OCPI 2.2.1 ``Tariff`` objects.

    The price is the ``ENERGY`` component of the first tariff element
    without ``restrictions`` (the base price; time-of-day and other restricted
    elements are not modelled), times ``1 + vat / 100`` when ``vat`` is given
    (OCPI prices exclude VAT).  Tariffs in another currency than ``currency``
    are ignored.  ``tariffs`` may also be an OCPI response (``{"data": [...]}``).
    """
    if isinstance(tariffs, Mapping):
        tariffs = tariffs.get("data") or ()
    out: dict[str, float] = {}
    for t in tariffs or ():
        if not isinstance(t, Mapping) or "id" not in t:
            continue
        if currency is not None and t.get("currency") not in (None, currency):
            continue
        for el in t.get("elements") or ():
            if el.get("restrictions"):
                continue
            comp = next((c for c in el.get("price_components") or () if c.get("type") == "ENERGY"), None)
            if comp is None:
                continue
            price = float(comp.get("price", 0.0))
            vat = comp.get("vat")
            if isinstance(vat, (int, float)) and not isinstance(vat, bool):
                price *= 1.0 + float(vat) / 100.0
            out[str(t["id"])] = price
            break
    return out


def _ocpi_kind(conn: Mapping[str, Any]) -> str | None:
    pt = conn.get("power_type")
    if pt == "DC":
        return "DC"
    if isinstance(pt, str) and pt.startswith("AC"):
        return "AC"
    std = OCPI_STANDARDS.get(str(conn.get("standard")))
    if std is not None and len(CONNECTORS[std]) == 1:
        return CONNECTORS[std][0]
    return None


def stations_from_ocpi(
    locations: Iterable[Mapping[str, Any]] | Mapping[str, Any],
    network: RoadNetwork,
    *,
    tariffs: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
    max_snap_m: float = DEFAULT_MAX_SNAP_M,
    operator_prices: bool = True,
    default_price: float | Mapping[str, float] | None = None,
    currency: str | None = "TRY",
) -> ImportedStations:
    """Stations from OCPI 2.2.1 ``Location`` objects (module ``locations``).

    ``locations`` is a list of Location objects or an OCPI response
    (``{"data": [...], "status_code": 1000, ...}``).  Read per location:
    ``coordinates`` (decimal strings), ``name``/``address``, ``operator.name``
    (else ``party_id``), ``publish`` (``false``: skipped) and every EVSE with
    its ``status`` and ``connectors[]`` (``standard``, ``power_type``,
    ``max_electric_power`` [W] or voltage and amperage, ``tariff_ids``).

    Each location gives one station per kind (DC, AC) that has an EVSE in
    service: ``REMOVED`` and ``PLANNED`` EVSEs are ignored, ``INOPERATIVE`` and
    ``OUTOFORDER`` ones count as out of service, all others (``AVAILABLE``,
    ``CHARGING``, ``BLOCKED``, ``RESERVED``, ``UNKNOWN``) as plugs.  An EVSE
    whose connectors carry both kinds counts for each kind.
    :attr:`ImportedStations.availability` keeps the snapshot of the station:
    ``available`` EVSEs (status ``AVAILABLE``), ``total`` EVSEs in service,
    ``out_of_service``, the count per ``status`` and the location's
    ``last_updated`` -- the live data the caller can show next to the plan.

    ``tariffs`` (OCPI ``Tariff`` objects in ``currency``) price a station by
    the highest base energy price among its connectors' ``tariff_ids`` (the
    list price, as for the OSM stations); without a usable tariff the
    operator or default price applies (module docs).
    """
    if isinstance(locations, Mapping):
        if "data" not in locations:
            raise ValueError("an OCPI response needs a 'data' list of Location objects")
        locations = locations["data"]  # type: ignore[assignment]
    snap = _Snapper(network, max_snap_m)
    prices = _Prices(operator_prices, default_price)
    tariff_price = _ocpi_tariff_prices(tariffs, currency)
    out = ImportedStations(source="ocpi")
    for n, loc in enumerate(locations):  # type: ignore[union-attr]
        if not isinstance(loc, Mapping):
            out.skipped.append({"record": n, "reason": "invalid record"})
            continue
        ident = f"{loc.get('country_code', '')}*{loc.get('party_id', '')}*{loc.get('id', n)}".strip("*")
        if loc.get("publish") is False:
            out.skipped.append({"record": ident, "reason": "not published"})
            continue
        coords = loc.get("coordinates") or {}
        try:
            lat, lon = float(coords["latitude"]), float(coords["longitude"])
        except (KeyError, TypeError, ValueError):
            out.skipped.append({"record": ident, "reason": "no coordinates"})
            continue
        hit = snap.geo(lat, lon)
        if hit is None:
            out.skipped.append({"record": ident, "reason": "too far from the network", "max_snap_m": max_snap_m})
            continue
        op = loc.get("operator") or {}
        operator = str((op.get("name") if isinstance(op, Mapping) else None) or loc.get("party_id") or "unknown")
        name = str(loc.get("name") or loc.get("address") or "")
        per_kind: dict[str, dict[str, Any]] = {}
        unknown: set[str] = set()
        for evse in loc.get("evses") or ():
            status = str(evse.get("status") or "UNKNOWN").upper()
            if status in _OCPI_GONE:
                continue
            kinds_here: dict[str, list[tuple[str, float | None, list[str]]]] = {}
            for conn in evse.get("connectors") or ():
                kind = _ocpi_kind(conn)
                std = OCPI_STANDARDS.get(str(conn.get("standard")))
                if std is None:  # a household or industrial socket: not a vehicle connector of the model
                    unknown.add(str(conn.get("standard")))
                    continue
                if kind is None or kind not in CONNECTORS[std]:
                    continue
                kinds_here.setdefault(kind, []).append(
                    (std, _ocpi_power_kw(conn), [str(t) for t in conn.get("tariff_ids") or ()]))
            for kind, conns in kinds_here.items():
                agg = per_kind.setdefault(kind, {"power": None, "plugs": 0, "down": 0, "available": 0, "status": {},
                                                 "connectors": set(), "tariffs": set(), "evses": []})
                agg["status"][status] = agg["status"].get(status, 0) + 1
                agg["evses"].append(str(evse.get("evse_id") or evse.get("uid") or ""))
                if status in _OCPI_DOWN:
                    agg["down"] += 1
                else:
                    agg["plugs"] += 1
                    agg["available"] += status == "AVAILABLE"
                for std, kw, tids in conns:
                    agg["connectors"].add(std)
                    if kw is not None and (agg["power"] is None or kw > agg["power"]):
                        agg["power"] = kw
                    agg["tariffs"].update(tids)
        if not per_kind:
            out.skipped.append({"record": ident, "reason": "no usable connector", "types": sorted(unknown)})
            continue
        for kind in ("DC", "AC"):
            agg = per_kind.get(kind)
            if agg is None:
                continue
            if agg["plugs"] == 0:
                out.skipped.append({"record": f"{ident} {kind}", "reason": "no EVSE in service",
                                    "status": dict(agg["status"])})
                continue
            power, power_src = (agg["power"], "ocpi") if agg["power"] else (DEFAULT_POWER_KW[kind], "default")
            tp = [tariff_price[t] for t in agg["tariffs"] if t in tariff_price]
            price, price_src = (max(tp), "ocpi-tariff") if tp else prices.price(operator, kind)
            st = _station(hit[0], kind, power, agg["plugs"], price, _site_name(name, operator, kind), operator,
                          agg["connectors"])
            avail = {"available": agg["available"], "total": agg["plugs"], "out_of_service": agg["down"],
                     "status": dict(sorted(agg["status"].items())), "last_updated": loc.get("last_updated")}
            out._add(st, {"source": "ocpi", "record": ident, "evses": agg["evses"], "snap_m": round(hit[1], 1),
                          "price": price_src, "power": power_src}, avail)
    return out


# ====================================================================== Open Charge Map
def _ocm_standard(conn: Mapping[str, Any]) -> str | None:
    tid = conn.get("ConnectionTypeID")
    if isinstance(tid, int) and tid in OCM_CONNECTION_TYPES:
        return OCM_CONNECTION_TYPES[tid]
    ctype = conn.get("ConnectionType")
    title = ctype.get("Title") if isinstance(ctype, Mapping) else None
    if isinstance(title, str):
        t = title.lower()
        if "ccs" in t:
            return "CCS1" if "type 1" in t else "CCS2"
        if "chademo" in t:
            return "CHAdeMO"
        if "nacs" in t or "j3400" in t:
            return "NACS"
        if "type 2" in t or "mennekes" in t:
            return "Type2"
        if "type 1" in t or "j1772" in t:
            return "Type1"
    return None


def _ocm_operational(obj: Mapping[str, Any]) -> bool | None:
    st = obj.get("StatusType")
    if isinstance(st, Mapping) and isinstance(st.get("IsOperational"), bool):
        return st["IsOperational"]
    return None


def stations_from_ocm(
    pois: Iterable[Mapping[str, Any]],
    network: RoadNetwork,
    *,
    max_snap_m: float = DEFAULT_MAX_SNAP_M,
    operator_prices: bool = True,
    default_price: float | Mapping[str, float] | None = None,
) -> ImportedStations:
    """Stations from Open Charge Map POIs (the JSON list of ``/v3/poi``).

    Read per POI: ``AddressInfo.Latitude/Longitude`` and ``Title``,
    ``OperatorInfo.Title``, ``StatusType.IsOperational`` (``false``: skipped)
    and every connection: ``ConnectionTypeID`` (:data:`OCM_CONNECTION_TYPES`,
    else ``ConnectionType.Title``), ``CurrentTypeID`` (10/20 AC, 30 DC; else
    the standard decides), ``PowerKW`` (else amps x volts, x sqrt 3 for three
    phases), ``Quantity`` (default 1) and its own ``StatusType``
    (non-operational connections are left out).  One station per kind, as
    for OCPI.  OCM has no live status: ``availability`` stays ``None``.
    Licence: CC BY-SA 4.0 (module docs, :data:`OCM_ATTRIBUTION`).
    """
    snap = _Snapper(network, max_snap_m)
    prices = _Prices(operator_prices, default_price)
    out = ImportedStations(source="ocm")
    for n, poi in enumerate(pois):
        if not isinstance(poi, Mapping):
            out.skipped.append({"record": n, "reason": "invalid record"})
            continue
        ident = poi.get("ID", n)
        if _ocm_operational(poi) is False:
            out.skipped.append({"record": ident, "reason": "not operational"})
            continue
        addr = poi.get("AddressInfo") or {}
        try:
            lat, lon = float(addr["Latitude"]), float(addr["Longitude"])
        except (KeyError, TypeError, ValueError):
            out.skipped.append({"record": ident, "reason": "no coordinates"})
            continue
        hit = snap.geo(lat, lon)
        if hit is None:
            out.skipped.append({"record": ident, "reason": "too far from the network", "max_snap_m": max_snap_m})
            continue
        op = poi.get("OperatorInfo") or {}
        operator = str((op.get("Title") if isinstance(op, Mapping) else None) or "unknown")
        per_kind: dict[str, dict[str, Any]] = {}
        for conn in poi.get("Connections") or ():
            if _ocm_operational(conn) is False:
                continue
            std = _ocm_standard(conn)
            if std is None:
                continue
            kind = _OCM_CURRENT.get(conn.get("CurrentTypeID"))  # type: ignore[arg-type]
            kw = conn.get("PowerKW")
            kw = float(kw) if isinstance(kw, (int, float)) and not isinstance(kw, bool) and kw > 0 else None
            amps, volts = conn.get("Amps"), conn.get("Voltage")
            if kw is None and isinstance(amps, (int, float)) and isinstance(volts, (int, float)):
                kw = amps * volts * (math.sqrt(3) if conn.get("CurrentTypeID") == 20 else 1) / 1000
                kw = kw if kw > 0 else None
            if kind is None:
                kind = CONNECTORS[std][0] if len(CONNECTORS[std]) == 1 else ("DC" if (kw or 0) > 22.0 else "AC")
            if kind not in CONNECTORS[std]:
                continue
            qty = conn.get("Quantity")
            qty = int(qty) if isinstance(qty, int) and not isinstance(qty, bool) and qty > 0 else 1
            agg = per_kind.setdefault(kind, {"power": None, "plugs": 0, "connectors": set()})
            agg["plugs"] += qty
            agg["connectors"].add(std)
            if kw is not None and (agg["power"] is None or kw > agg["power"]):
                agg["power"] = kw
        if not per_kind:
            out.skipped.append({"record": ident, "reason": "no usable connector"})
            continue
        title = str(addr.get("Title") or "")
        for kind in ("DC", "AC"):
            agg = per_kind.get(kind)
            if agg is None:
                continue
            power, power_src = (agg["power"], "ocm") if agg["power"] else (DEFAULT_POWER_KW[kind], "default")
            price, price_src = prices.price(operator, kind)
            st = _station(hit[0], kind, power, agg["plugs"], price, _site_name(title, operator, kind), operator,
                          agg["connectors"])
            out._add(st, {"source": "ocm", "record": ident, "snap_m": round(hit[1], 1), "price": price_src,
                          "power": power_src})
    return out


# ====================================================================== files
def detect_format(path: str | Path, data: Any = None) -> str:
    """``"csv"``, ``"ocpi"`` or ``"ocm"`` from the file name and (for JSON) its content."""
    p = Path(path)
    if p.suffix.lower() in (".csv", ".tsv", ".txt"):
        return "csv"
    if data is None:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    items = data.get("data") if isinstance(data, Mapping) else data
    if isinstance(items, list) and items and isinstance(items[0], Mapping):
        first = items[0]
        if "AddressInfo" in first or "Connections" in first:
            return "ocm"
        if "evses" in first or "coordinates" in first:
            return "ocpi"
    raise ValueError(f"cannot tell the format of {p.name}: give it explicitly (one of {FORMATS})")


def load_stations(path: str | Path, network: RoadNetwork, fmt: str = "auto", **options: Any) -> ImportedStations:
    """Read a station file and import it with the adapter of its format.

    ``fmt`` is ``"auto"`` (:func:`detect_format`), ``"csv"``, ``"ocpi"`` (a
    JSON list of Location objects or an OCPI response; an object with
    ``"locations"`` and ``"tariffs"`` lists also works) or ``"ocm"`` (the
    JSON list of POIs).  ``options`` go to the adapter.
    """
    p = Path(path)
    if fmt not in ("auto", *FORMATS):
        raise ValueError(f"format must be 'auto' or one of {FORMATS}, got {fmt!r}")
    if fmt == "csv" or (fmt == "auto" and p.suffix.lower() in (".csv", ".tsv", ".txt")):
        return stations_from_csv(p, network, **options)
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    if isinstance(data, Mapping) and "locations" in data:  # a bundle of locations and tariffs
        options.setdefault("tariffs", data.get("tariffs"))
        data = data["locations"]
        fmt = "ocpi" if fmt == "auto" else fmt
    if fmt == "auto":
        fmt = detect_format(p, data)
    if fmt == "ocpi":
        return stations_from_ocpi(data, network, **options)
    if fmt == "ocm":
        if not isinstance(data, list):
            raise ValueError("an Open Charge Map file must hold a JSON list of POIs")
        return stations_from_ocm(data, network, **options)
    raise ValueError(f"{p.name}: format {fmt!r} needs a CSV file")


# ====================================================================== scenario
_CREDITS = {"ocm": OCM_ATTRIBUTION}


def with_stations(scenario: Scenario, imported: ImportedStations, *, label: str | None = None) -> Scenario:
    """A copy of ``scenario`` whose stations are ``imported.stations``.

    ``meta["station_info"]`` becomes the import info per station (with the
    availability snapshot under ``"availability"`` where there is one),
    ``meta["stations"]`` names the source (``label`` or the format) and the
    credit of the source is appended to ``meta["attribution"]`` (Open Charge
    Map: :data:`OCM_ATTRIBUTION`).  The network, vehicle and query are kept.
    """
    meta = dict(scenario.meta) if isinstance(scenario.meta, dict) else {}
    info = []
    for i, (inf, av) in enumerate(zip(imported.info, imported.availability, strict=True)):
        d = dict(inf)
        if av is not None:
            d["availability"] = dict(av)
        info.append(d)
        assert imported.stations[i].id == i
    meta["station_info"] = {str(i): d for i, d in enumerate(info)}
    meta["stations"] = label or imported.source or "file"
    credit = _CREDITS.get(imported.source)
    if credit:
        meta["attribution"] = f"{meta['attribution']}; {credit}" if meta.get("attribution") else credit
    stations = [Station.from_dict(s.to_dict()) for s in imported.stations]
    return scenario.with_(stations=stations, meta=meta)
