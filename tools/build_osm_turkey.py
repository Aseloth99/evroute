#!/usr/bin/env python3
"""Build EVRoute's real-data Turkey case study from OpenStreetMap and SRTM.

Pipeline (each stage is cached as a file in ``--workdir``; delete it, or pass
``--force STAGE``, to recompute)::

    download -> extract -> raw graph (secondary bridges, speeds, urban proxy)
             -> SRTM elevations -> contract degree-2 chains (terrain-preserving)
             -> largest SCC -> junction consolidation -> re-contraction
             -> grade sanity clip -> charging stations -> places
             -> validation (KGM) -> write

Inputs (all downloaded by the script, nothing else is needed):

* OpenStreetMap extract of Turkey (PBF), default
  https://download.openstreetmap.fr/extracts/europe/turkey.osm.pbf
  (© OpenStreetMap contributors, ODbL 1.0).
* SRTM 1 arc-second elevation tiles (HGT, "skadi" layout of the AWS terrain
  tiles), https://s3.amazonaws.com/elevation-tiles-prod/skadi/N37/N37E033.hgt.gz
  (NASA/USGS, public domain).  Only the 1°x1° cells that contain a road node
  are fetched; each tile is deleted right after sampling.
* KGM "İller arası mesafe" table (official road distances between the 81
  province centres), used for validation only:
  https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Root/Uzakliklar/ilmesafe.xlsx

Outputs in ``--outdir`` (gzip-compressed CSV, UTF-8):

* ``nodes.csv.gz``    id, lat, lon, elevation_m, osm_id (0 = point inserted by the build)
* ``edges.csv.gz``    u, v, length_m, speed_kmh, road_class, ref, toll  (directed; a two-way road is two rows)
* ``stations.csv.gz`` one row per public OSM amenity=charging_station within ``--snap-max-m`` of the network
* ``places.csv.gz``   place=city/town nodes + the 81 province centres, snapped to the network
* ``validation.csv``  road distances on the graph vs the official KGM table
* ``meta.json``       sources, OSM timestamp, parameters, statistics, licences
  (``prices.json`` and ``DATA_LICENSE.md`` are maintained by hand next to it.)

Requirements: Python >= 3.10, ``pip install osmium numpy scipy shapely openpyxl``
(pyosmium >= 4).  Run with ``python -I build_osm_turkey.py --workdir W --outdir O``;
a full run takes about 10 minutes (the extract pass ~7 min) and ~1 GB of disk
(the PBF is ~700 MB); later runs reuse the cached stages and take seconds.

This script is a repository tool of EVRoute (``tools/``), not part of the
installed package.  To refresh the bundled data, run it from the repository
root with the package folder as the output::

    python -I tools/build_osm_turkey.py --workdir /tmp/osm-work --outdir src/evroute/data/osm_turkey

It rewrites the generated files (CSV tables, ``validation.csv``,
``meta.json``) and leaves ``prices.json``, ``DATA_LICENSE.md`` and the
folder's ``README.md`` alone; the loader is
:mod:`evroute.generators.osm_turkey`.

Modelling choices (details in the docstrings below and in meta.json):

* Roads: highway = motorway, trunk, primary and their *_link ramps.  All of
  ``secondary`` only with ``--with-secondary``; otherwise only the secondary
  segments needed to reconnect stranded main-road fragments (>= 2 km, via
  <= 30 km of secondary) and to close short gaps that force detours between
  province centres (see :func:`bridge_with_secondary`, :func:`detour_bridges`).
  Ways with access=no/private (unless
  motor vehicles are explicitly allowed), area=yes or reversible/alternating
  oneway are dropped; only the part inside Turkey's admin_level=2 boundary
  (OSM relation 174737) is kept.
* Direction: oneway=yes/true/1 forward, -1 backward, no both ways; without a
  tag, motorway, motorway_link and roundabouts are one-way (OSM convention),
  everything else two-way.
* Free-flow speed: a valid ``maxspeed`` (or ``maxspeed:forward/backward``,
  or a ``TR:*`` zone) when present; otherwise the legal default for cars of
  Karayolları Trafik Yönetmeliği Art. 100: 50 km/h in built-up areas, 90 on
  single-carriageway rural roads, 110 on dual carriageways, 120 on motorways;
  ramps (``*_link``) the length-weighted median of the tagged ramps of the
  same class and roundabouts 30 km/h (design assumptions).  "Built
  up" is detected from the density of junctions with minor streets
  (residential/unclassified/tertiary/living_street) within 500 m, with a
  threshold calibrated on the tagged roads.
* Lengths: sum of the WGS84 geodesic lengths of the OSM segments.
* Simplification: degree-2 chains are contracted, but split at every local
  elevation extremum with at least ``--prominence-m`` (20 m) of relief on
  both sides and so that no edge is longer than ``--max-edge-m`` (5 km).
  Road class = the class covering most of the length, speed = length-weighted
  harmonic mean (preserves the free-flow travel time exactly).  After the
  SCC, the end nodes of edges shorter than ``--consolidate-m`` (30 m) are
  merged (junction consolidation) and former junctions that became plain
  pass-through points are contracted again under the same terrain/length
  rules.
* Elevation: bilinear SRTM sample at every node; inside bridges and tunnels
  the road elevation is interpolated linearly between the portals; grades
  above 15 % (SRTM artefacts at junctions on viaducts, dam relocations)
  are clipped by moving the deviating node.
* Stations: every amenity=charging_station node/way/relation inside Turkey;
  public ones (not access=private/no) within ``--snap-max-m`` of a network
  node.  Kind/power/plug rules: see :func:`describe_station` and meta.json.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import gzip
import hashlib
import io
import json
import math
import os
import pickle
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------- configuration
PBF_URL = "https://download.openstreetmap.fr/extracts/europe/turkey.osm.pbf"
SRTM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{lat_dir}/{name}.hgt.gz"
KGM_URL = "https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Root/Uzakliklar/ilmesafe.xlsx"
TURKEY_RELATION = 174737  # OSM relation "Türkiye", boundary=administrative, admin_level=2

MAIN_CLASSES = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link")
SECONDARY_CLASSES = ("secondary", "secondary_link")
#: Minor streets whose junctions with the main roads indicate a built-up area.
MINOR_STREETS = ("residential", "unclassified", "tertiary", "tertiary_link", "living_street")
#: OSM highway -> EVRoute road class (evroute.network.ROAD_CLASSES).
EVROUTE_CLASS = {
    "motorway": "motorway", "trunk": "trunk", "primary": "primary", "secondary": "secondary",
    "motorway_link": "link", "trunk_link": "link", "primary_link": "link", "secondary_link": "link",
}
OSM_CLASSES = MAIN_CLASSES + SECONDARY_CLASSES
CLASS_CODE = {c: i for i, c in enumerate(OSM_CLASSES)}

#: Legal default speeds for cars, Karayolları Trafik Yönetmeliği Art. 100 [km/h].
LEGAL_URBAN, LEGAL_RURAL, LEGAL_DUAL, LEGAL_MOTORWAY = 50.0, 90.0, 110.0, 120.0
LINK_DEFAULT, ROUNDABOUT_DEFAULT = 50.0, 30.0
#: Implicit speed zones (maxspeed=TR:urban etc., also used in maxspeed:type).
ZONES = {"urban": 50.0, "rural": 90.0, "trunk": 110.0, "motorway": 120.0, "living_street": 20.0}
NO_ACCESS = {"no", "private", "agricultural", "forestry", "delivery", "emergency"}
YES_ACCESS = {"yes", "designated", "permissive", "destination"}

URBAN_RADIUS_M = 500.0
DENSIFY_M = 200.0
#: Why a node of the simplified graph exists.
KIND_JUNCTION, KIND_EXTREMUM, KIND_LENGTH, KIND_LOOP = 0, 1, 2, 3
#: Grades steeper than this are treated as SRTM artefacts (junctions on bridges, cliffs) and clipped.
MAX_GRADE = 0.15

#: Charging: socket types (OSM key:socket) by current.
DC_SOCKETS = {"type2_combo", "type1_combo", "chademo", "gb_dc", "tesla_supercharger",
              "tesla_supercharger_ccs", "ccs", "ccs2", "ccs1", "mcs", "chaoji"}
AC_SOCKETS = {"type2", "type2_cable", "type1", "type3", "type3a", "type3c", "schuko", "cee_blue",
              "cee_red_16a", "cee_red_32a", "cee_red_63a", "cee_red_125a", "tesla_destination",
              "gb_ac", "as3112", "bs1363", "domestic"}
AC_DEFAULT_KW = 22.0     # nearly all public AC points in Turkey are 22 kW Type 2 (EPDK: 12-22 kW is the largest class)
DC_DEFAULT_KW = 120.0    # conservative: EPDK Sept 2026 DC sockets are ~49 % 51-150 kW and ~48 % >150 kW
TESLA_DEFAULT_KW = 250.0  # Tesla Supercharger V3/V4 stalls
AC_DC_THRESHOLD_KW = 22.0
#: Canonical operator names (regex on brand / operator / network / name, first match wins).
OPERATORS = [
    (r"\btrugo\b|\btogg\b", "Trugo"),
    (r"\bzes\b|zorlu", "ZES"),
    (r"e[şs]arj|enerjisa", "Eşarj"),
    (r"\bwat\b", "WAT Mobilite"),
    (r"astor", "Astor Şarj"),
    (r"tesla", "Tesla"),
    (r"en\s*yak[ıi]t", "En Yakıt"),
    (r"voltrun", "Voltrun"),
    (r"zeplin", "Zeplin"),
    (r"otopriz", "Otopriz"),
    (r"shell", "Shell Recharge"),
    (r"on\s*charge", "Oncharge"),
    (r"otojet", "Otojet"),
    (r"\b5\s*[şs]arj", "5Şarj"),
    (r"neva", "Neva Şarj"),
    (r"efish", "Efish"),
    (r"aksa", "Aksa Şarj"),
    (r"bee\s*full", "Beefull"),
    (r"\bk-?\s*[şs]arj", "K-Şarj"),
    (r"lumicle", "Lumicle"),
    (r"sharz", "Sharz.net"),
    (r"ovolt", "Ovolt"),
    (r"d-?\s*charge", "D-Charge"),
    (r"petrol ofisi|\bpo\b", "Petrol Ofisi"),
    (r"opet", "Opet"),
    (r"aytemiz", "Aytemiz"),
    (r"total", "TotalEnergies"),
    (r"bp pulse|\bbp\b", "bp pulse"),
    (r"renault", "Renault (dealer)"),
    (r"tun[çc]matik", "Tunçmatik"),
    (r"porsche", "Porsche (dealer)"),
    (r"mercedes", "Mercedes-Benz (dealer)"),
    (r"bmw", "BMW (dealer)"),
]

#: Official charging statistics used for the coverage statement (EPDK monthly report, latest at build time).
EPDK_STATS = {
    "source": "EPDK, Şarj Hizmeti Piyasası Aylık İstatistikleri - Eylül 2026 (published 16.10.2026)",
    "url": "https://www.epdk.gov.tr/Detay/Icerik/3-0-222/enerji-donusumusarj-hizmeti-piyasasi--istatistik",
    "month": "2026-09",
    "scope": "sockets with commercial charging service under the Şarj Hizmeti Yönetmeliği (public and non-public)",
    "sockets_total": 48971, "sockets_ac": 27664, "sockets_dc": 21307,
    "sockets_by_power_2026Q3": {"1-11kW": 6717, "12-22kW": 20781, "23-50kW": 920, "51-150kW": 10320, "151kW+": 10277},
    "installed_mw": 3875, "evs_registered": 491492, "energy_mwh_month": 83253, "sessions_month": 3152733,
    "dc_share_of_energy_pct": 82.44,
    "hourly_energy_mwh_00_23": [1890, 1305, 955, 789, 725, 788, 1045, 1575, 2633, 3855, 4314, 4565, 5118, 5355, 5384,
                                5490, 5590, 5730, 5607, 5286, 4919, 4263, 3413, 2657],
    "note": "EPDK publishes socket counts, not site counts; no station coordinates are published as open data "
            "(the Şarj@TR app and the licence register lisans.epdk.gov.tr show stations, the register with addresses only)",
}

UA = "evroute-data-build/1.0 (research; contact via github.com/Aseloth99/evroute)"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------------- helpers
def curl(url: str, dest: Path, *, resume: bool = False) -> int:
    """Download with curl (honours the proxy and CA settings of the environment); returns the HTTP code."""
    cmd = ["curl", "-sS", "-L", "--retry", "5", "-A", UA, "-o", str(dest), "-w", "%{http_code}", url]
    if resume:
        cmd[1:1] = ["-C", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return int(out.stdout.strip()[-3:])
    except ValueError:
        return 0


def md5sum(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


_A, _F = 6378137.0, 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)


def geodesic_m(lat1, lon1, lat2, lon2):
    """WGS84 length of short segments (vectorised).

    Uses the meridional (M) and prime-vertical (N) radii of curvature at the
    mid-latitude; the relative error is below 1e-6 for segments under 5 km,
    i.e. well below the precision of the OSM coordinates.
    """
    phi = np.radians((np.asarray(lat1) + np.asarray(lat2)) * 0.5)
    s = np.sin(phi)
    w = np.sqrt(1.0 - _E2 * s * s)
    m = _A * (1.0 - _E2) / w ** 3
    n = _A / w
    dphi = np.radians(np.asarray(lat2) - np.asarray(lat1))
    dlam = np.radians(np.asarray(lon2) - np.asarray(lon1))
    return np.hypot(m * dphi, n * np.cos(phi) * dlam)


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371008.8
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(np.minimum(a, 1.0)))


def local_xy(lat, lon, lat0=39.0, lon0=35.0):
    """Equirectangular metres around (lat0, lon0) for nearest-neighbour searches."""
    r = 6371008.8
    return np.c_[np.radians(np.asarray(lon) - lon0) * r * math.cos(math.radians(lat0)),
                 np.radians(np.asarray(lat) - lat0) * r]


def parse_speed(value: str | None) -> float | None:
    """OSM maxspeed -> km/h, ``None`` if missing or invalid (accepts 'TR:urban', '70 km/h', '50;70', 'x mph')."""
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if ";" in s or "|" in s:
        vals = [parse_speed(p) for p in re.split(r"[;|]", s)]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None
    if ":" in s:
        return ZONES.get(s.split(":", 1)[1])
    if s in ZONES:
        return ZONES[s]
    factor = 1.0
    if s.endswith("mph"):
        factor, s = 1.609344, s[:-3]
    s = s.replace("km/h", "").replace("kmh", "").replace("kph", "").strip()
    try:
        v = float(s) * factor
    except ValueError:
        return None
    return v if 5.0 <= v <= 150.0 else None


def norm_ref(ref: str | None) -> str:
    if not ref:
        return ""
    r = ref.split(";")[0].strip().upper()
    m = re.fullmatch(r"([A-Z]{1,2})[\s\-]*(\d+[A-Z]?)", r)
    return f"{m.group(1)}-{m.group(2)}" if m else r


# ============================================================================= stage 1: download
def stage_download(work: Path, args) -> dict:
    raw = work / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    pbf = raw / "turkey.osm.pbf"
    info_path = work / "download.json"
    if info_path.exists() and pbf.exists() and "download" not in args.force:
        return json.loads(info_path.read_text())
    log(f"downloading {args.pbf_url}")
    code = curl(args.pbf_url, pbf, resume=True)
    if code not in (200, 206, 416):
        raise SystemExit(f"PBF download failed: HTTP {code}")
    md5_remote = None
    if curl(args.pbf_url + ".md5", raw / "turkey.osm.pbf.md5") == 200:
        md5_remote = (raw / "turkey.osm.pbf.md5").read_text().split()[0]
    md5_local = md5sum(pbf)
    if md5_remote and md5_remote != md5_local:
        raise SystemExit(f"md5 mismatch: {md5_local} != {md5_remote}")
    import osmium

    reader = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING)
    header = reader.header()
    ts = header.get("osmosis_replication_timestamp")
    seq = header.get("osmosis_replication_sequence_number")
    reader.close()
    kgm = raw / "kgm_ilmesafe.xlsx"
    if curl(KGM_URL, kgm) != 200:
        log("WARNING: KGM distance table not downloaded; validation will be skipped")
    info = {"pbf_url": args.pbf_url, "pbf_bytes": pbf.stat().st_size, "pbf_md5": md5_local,
            "md5_checked": bool(md5_remote), "osm_timestamp": ts, "osm_sequence": seq,
            "downloaded_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kgm_url": KGM_URL}
    info_path.write_text(json.dumps(info, indent=1))
    return info


# ============================================================================= stage 2: extract
def stage_extract(work: Path, args) -> dict:
    """Read roads, minor-street junctions, charging stations, places and province centres."""
    path = work / "extract.pkl"
    if path.exists() and "extract" not in args.force:
        with open(path, "rb") as f:
            return pickle.load(f)
    import osmium
    import shapely
    from osmium.filter import IdFilter, KeyFilter

    pbf = str(work / "raw" / "turkey.osm.pbf")
    keys = ("highway", "oneway", "junction", "maxspeed", "maxspeed:forward", "maxspeed:backward",
            "maxspeed:type", "ref", "name", "bridge", "tunnel", "access", "motor_vehicle", "motorcar",
            "vehicle", "toll", "area")
    t0 = time.time()
    # ---- relations: charging stations, provinces (admin_level 4) with their admin_centre node
    cs_rel, admin4 = {}, {}
    for r in osmium.FileProcessor(pbf, osmium.osm.RELATION):
        t = r.tags
        if t.get("amenity") == "charging_station":
            cs_rel[r.id] = (dict(t), [(m.type, m.ref, m.role) for m in r.members])
        elif t.get("boundary") == "administrative" and t.get("admin_level") == "4" and \
                str(t.get("ISO3166-2", "")).startswith("TR-"):
            admin4[r.id] = ({k: t.get(k) for k in ("name", "name:en", "ISO3166-2", "population") if k in t},
                            [(m.type, m.ref, m.role) for m in r.members if m.type == "n"])
    rel_way_ids = {ref for _, ms in cs_rel.values() for ty, ref, _ in ms if ty == "w"}
    log(f"relations: {len(cs_rel)} charging stations, {len(admin4)} provinces ({time.time() - t0:.0f} s)")

    # ---- ways: roads (with node refs), minor streets (only node refs), station ways
    road_set = set(OSM_CLASSES)
    minor_set = set(MINOR_STREETS)
    roads, cs_way, rel_ways, minor_chunks, buf = [], {}, {}, [], []
    for w in osmium.FileProcessor(pbf, osmium.osm.WAY).with_filter(KeyFilter("highway", "amenity")):
        t = w.tags
        hw = t.get("highway")
        if hw in road_set:
            roads.append((w.id, {k: t.get(k) for k in keys if k in t},
                          np.fromiter((n.ref for n in w.nodes), dtype=np.int64)))
        elif hw in minor_set:
            buf.append(np.fromiter((n.ref for n in w.nodes), dtype=np.int64))
            if len(buf) >= 200_000:
                minor_chunks.append(np.unique(np.concatenate(buf)))
                buf = []
        if t.get("amenity") == "charging_station":
            cs_way[w.id] = (dict(t), np.fromiter((n.ref for n in w.nodes), dtype=np.int64))
        if w.id in rel_way_ids:
            rel_ways[w.id] = np.fromiter((n.ref for n in w.nodes), dtype=np.int64)
    if buf:
        minor_chunks.append(np.unique(np.concatenate(buf)))
    minor_ids = np.unique(np.concatenate(minor_chunks)) if minor_chunks else np.zeros(0, np.int64)
    log(f"ways: {len(roads)} roads, {len(minor_ids)} minor-street nodes, {len(cs_way)} station ways "
        f"({time.time() - t0:.0f} s)")

    # ---- node locations of everything referenced
    admin_ids = {ref for _, ms in admin4.values() for ty, ref, ro in ms if ro == "admin_centre"}
    rel_node_ids = {ref for _, ms in cs_rel.values() for ty, ref, _ in ms if ty == "n"}
    parts = [refs for _, _, refs in roads] + [r for _, r in cs_way.values()] + list(rel_ways.values())
    parts.append(np.array(sorted(admin_ids | rel_node_ids), dtype=np.int64))
    ids = np.unique(np.concatenate(parts))
    lat = np.full(len(ids), np.nan)
    lon = np.full(len(ids), np.nan)
    admin_nodes = {}
    for n in osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(IdFilter(ids.tolist())):
        i = np.searchsorted(ids, n.id)
        lat[i], lon[i] = n.location.lat, n.location.lon
        if n.id in admin_ids:
            admin_nodes[n.id] = (n.location.lat, n.location.lon, dict(n.tags))
    minor_flag = np.isin(ids, minor_ids)
    log(f"locations: {np.isfinite(lat).sum()} / {len(ids)} ({time.time() - t0:.0f} s)")

    # ---- POI nodes
    cs_node, charge_points, places = {}, 0, []
    for n in osmium.FileProcessor(pbf, osmium.osm.NODE).with_filter(KeyFilter("amenity", "man_made", "place")):
        t = n.tags
        if t.get("amenity") == "charging_station":
            cs_node[n.id] = (n.location.lat, n.location.lon, dict(t))
        elif t.get("man_made") == "charge_point":
            charge_points += 1
        if t.get("place") in ("city", "town"):
            places.append((n.id, n.location.lat, n.location.lon, dict(t)))
    log(f"POIs: {len(cs_node)} station nodes, {charge_points} charge_point nodes, {len(places)} city/town "
        f"({time.time() - t0:.0f} s)")

    # ---- Turkey boundary
    fab = osmium.geom.WKBFactory()
    boundary = None
    for obj in osmium.FileProcessor(pbf).with_areas(IdFilter([TURKEY_RELATION])):
        if isinstance(obj, osmium.osm.Area) and not obj.from_way() and obj.orig_id() == TURKEY_RELATION:
            boundary = fab.create_multipolygon(obj)
            break
    if boundary is None:
        raise SystemExit("could not assemble the Turkey boundary")
    geom = shapely.from_wkb(bytes.fromhex(boundary))
    shapely.prepare(geom)
    ok = np.isfinite(lat)
    inside = np.zeros(len(ids), bool)
    inside[ok] = shapely.contains_xy(geom, lon[ok], lat[ok])
    log(f"boundary assembled; {inside.sum()} of {ok.sum()} located nodes inside Turkey ({time.time() - t0:.0f} s)")

    data = dict(roads=roads, cs_way=cs_way, cs_rel=cs_rel, rel_ways=rel_ways, cs_node=cs_node,
                charge_points=charge_points, places=places, admin4=admin4, admin_nodes=admin_nodes,
                ids=ids, lat=lat, lon=lon, inside=inside, minor_flag=minor_flag, boundary_wkb=boundary)
    with open(path, "wb") as f:
        pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    return data


def inside_turkey(data: dict, lat, lon) -> np.ndarray:
    import shapely

    geom = data.get("_geom")
    if geom is None:
        geom = shapely.from_wkb(bytes.fromhex(data["boundary_wkb"]))
        shapely.prepare(geom)
        data["_geom"] = geom
    return shapely.contains_xy(geom, np.asarray(lon, float), np.asarray(lat, float))


# ============================================================================= stage 3: raw graph
def way_direction(t: dict) -> tuple[bool, bool] | None:
    ow = (t.get("oneway") or "").strip().lower()
    if ow in ("yes", "true", "1"):
        return True, False
    if ow in ("-1", "reverse"):
        return False, True
    if ow in ("no", "false", "0"):
        return True, True
    if ow in ("reversible", "alternating"):
        return None
    if t.get("junction") in ("roundabout", "circular") or t.get("highway") in ("motorway", "motorway_link"):
        return True, False
    return True, True


def way_usable(t: dict) -> bool:
    if t.get("area") == "yes":
        return False
    explicit = any(t.get(k) in YES_ACCESS for k in ("motorcar", "motor_vehicle"))
    if t.get("motorcar") in NO_ACCESS or t.get("motor_vehicle") in NO_ACCESS:
        return False
    if (t.get("access") in NO_ACCESS or t.get("vehicle") in NO_ACCESS) and not explicit:
        return False
    return True


def bridge_with_secondary(seg: dict, lat: np.ndarray, lon: np.ndarray, *, min_fragment_m: float,
                          max_bridge_m: float, rounds: int = 3) -> tuple[np.ndarray, dict]:
    """Choose the few secondary segments needed to reconnect stranded parts of the main network.

    The main roads (motorway/trunk/primary + links) alone fall apart into a
    large strongly connected component and fragments, mostly where a short
    piece of a national road is tagged ``secondary`` (e.g. D-975 near Başkale,
    which cuts off Hakkâri).  For every fragment with at least
    ``min_fragment_m`` of main road, two multi-source Dijkstra searches (from
    the main component, on the graph and on its reverse) find the paths
    main -> fragment and fragment -> main that use the least *secondary*
    length (main edges cost almost nothing); their secondary segments are kept
    when that length is at most ``max_bridge_m``.  Repeated ``rounds`` times
    because a bridge may run through another fragment.
    """
    from scipy.sparse import coo_matrix, csr_matrix
    from scipy.sparse.csgraph import connected_components, dijkstra

    a, b = seg["a"], seg["b"]
    is_sec = np.isin(seg["cls"], [CLASS_CODE[c] for c in SECONDARY_CLASSES])
    L = geodesic_m(lat[a], lon[a], lat[b], lon[b])
    used = np.unique(np.concatenate([a, b]))
    m = len(used)
    ia, ib = np.searchsorted(used, a), np.searchsorted(used, b)
    keep_sec = np.zeros(len(a), bool)

    def directed(mask):
        f, r = mask & seg["fwd"], mask & seg["bwd"]
        return (np.concatenate([ia[f], ib[r]]), np.concatenate([ib[f], ia[r]]),
                np.concatenate([np.nonzero(f)[0], np.nonzero(r)[0]]))

    report = {"rounds": []}
    for _ in range(rounds):
        active = ~is_sec | keep_sec
        s, d, _ = directed(active)
        A = coo_matrix((np.ones(len(s)), (s, d)), shape=(m, m)).tocsr()
        nc, lab = connected_components(A, directed=True, connection="strong")
        same = active & (lab[ia] == lab[ib])
        comp_len = np.bincount(lab[ia][same], weights=L[same], minlength=nc)
        big = int(np.argmax(comp_len))
        frags = [int(c) for c in np.nonzero(comp_len >= min_fragment_m)[0] if c != big]
        if not frags:
            break
        s, d, sidx = directed(np.ones(len(a), bool))
        w = np.where(is_sec[sidx] & ~keep_sec[sidx], L[sidx], 1e-3 * L[sidx]) + 1e-6
        key = s.astype(np.int64) * m + d
        order = np.lexsort((w, key))
        first = np.r_[True, key[order][1:] != key[order][:-1]]
        sel = order[first]  # parallel entries would be summed by scipy: keep the cheapest
        s, d, w, sidx, key = s[sel], d[sel], w[sel], sidx[sel], key[sel]
        G = csr_matrix((w, (s, d)), shape=(m, m))
        edge_of = dict(zip(key.tolist(), sidx.tolist()))
        sources = np.nonzero(lab == big)[0]
        # with min_only=True scipy returns (distances, predecessors, nearest source)
        searches = [(False,) + dijkstra(G, indices=sources, min_only=True, return_predecessors=True)[:2],
                    (True,) + dijkstra(G.T.tocsr(), indices=sources, min_only=True, return_predecessors=True)[:2]]
        ok = failed = 0
        added = 0.0
        for F in frags:
            nodes_f = np.nonzero(lab == F)[0]
            good = True
            for transposed, dist, pred in searches:
                j = int(nodes_f[np.argmin(dist[nodes_f])])
                if not np.isfinite(dist[j]) or dist[j] > max_bridge_m:
                    good = False
                    continue
                x = j
                while pred[x] >= 0:
                    p = int(pred[x])
                    si = edge_of[(x * m + p) if transposed else (p * m + x)]
                    if is_sec[si] and not keep_sec[si]:
                        keep_sec[si] = True
                        added += L[si]
                    x = p
            ok += good
            failed += not good
        report["rounds"].append({"fragments": len(frags), "bridged": ok, "not_bridged": failed,
                                 "secondary_km_added": round(added / 1000, 1)})
        log(f"  secondary bridging: {len(frags)} fragments >= {min_fragment_m / 1000:g} km, {ok} bridged, "
            f"{failed} not (> {max_bridge_m / 1000:g} km of secondary), +{added / 1000:.1f} km secondary")
    report["secondary_km_total"] = round(float(L[keep_sec].sum() / 1000), 1)
    report["secondary_segments"] = int(keep_sec.sum())
    return ~is_sec | keep_sec, report


def detour_bridges(seg: dict, lat: np.ndarray, lon: np.ndarray, keep: np.ndarray, centres: list[tuple[str, float, float]],
                   *, max_crow_m: float = 300e3, ratio: float = 1.15, min_gain_m: float = 15e3,
                   secondary_penalty: float = 4.0) -> tuple[np.ndarray, dict]:
    """Close short gaps of the main network that force long detours between province centres.

    A national road is sometimes interrupted by a few kilometres tagged
    ``secondary`` (e.g. D-975 near Başkale cuts Hakkâri off; D-915 towards
    Bayburt).  For every ordered pair of the 81 province centres at most
    ``max_crow_m`` apart, the shortest main-road path (A, roads kept so far)
    is compared with path B that may use secondary roads at
    ``secondary_penalty`` times their length - so B follows the main network
    and borrows secondary roads only to close gaps, not to take il-yolu
    shortcuts.  If A is unreachable, or longer than ``ratio`` x the true
    length of B and by more than ``min_gain_m``, B's secondary segments are kept.
    """
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    a, b = seg["a"], seg["b"]
    is_sec = np.isin(seg["cls"], [CLASS_CODE[c] for c in SECONDARY_CLASSES])
    L = geodesic_m(lat[a], lon[a], lat[b], lon[b])
    used = np.unique(np.concatenate([a, b]))
    m = len(used)
    ia, ib = np.searchsorted(used, a), np.searchsorted(used, b)

    def graph(mask, weight):
        f, r = mask & seg["fwd"], mask & seg["bwd"]
        s = np.concatenate([ia[f], ib[r]])
        d = np.concatenate([ib[f], ia[r]])
        sidx = np.concatenate([np.nonzero(f)[0], np.nonzero(r)[0]])
        key = s.astype(np.int64) * m + d
        order = np.lexsort((weight[sidx], key))
        first = order[np.r_[True, key[order][1:] != key[order][:-1]]]
        G = csr_matrix((weight[sidx][first] + 1e-6, (s[first], d[first])), shape=(m, m))
        return G, dict(zip(key[first].tolist(), sidx[first].tolist()))

    from scipy.sparse.csgraph import connected_components

    GA, _ = graph(keep, L)
    GB, edge_of = graph(np.ones(len(a), bool), np.where(is_sec & ~keep, secondary_penalty * L, L))
    _, labB = connected_components(GB, directed=True, connection="strong")
    big = np.bincount(labB).argmax()
    main_nodes = np.unique(np.concatenate([ia[~is_sec], ib[~is_sec]]))
    main_nodes = main_nodes[labB[main_nodes] == big]
    tree = cKDTree(local_xy(lat[used][main_nodes], lon[used][main_nodes]))
    _, k = tree.query(local_xy([c[1] for c in centres], [c[2] for c in centres]))
    src = main_nodes[k]
    crow = haversine_m(np.array([c[1] for c in centres])[:, None], np.array([c[2] for c in centres])[:, None],
                       np.array([c[1] for c in centres])[None, :], np.array([c[2] for c in centres])[None, :])
    add = np.zeros(len(a), bool)
    fixed = []
    for i, c in enumerate(centres):
        targets = [j for j in range(len(centres)) if j != i and crow[i, j] <= max_crow_m]
        if not targets:
            continue
        lim = 3.0 * max_crow_m
        dA = dijkstra(GA, indices=int(src[i]), limit=lim)
        dB, pB = dijkstra(GB, indices=int(src[i]), limit=lim, return_predecessors=True)
        for j in targets:
            t = int(src[j])
            if not np.isfinite(dB[t]):
                continue
            path, x = [], t
            while pB[x] >= 0:
                path.append(edge_of[int(pB[x]) * m + x])
                x = int(pB[x])
            true_b = float(L[path].sum())
            if np.isfinite(dA[t]) and not (dA[t] > ratio * true_b and dA[t] - true_b > min_gain_m):
                continue
            gained = 0.0
            for si in path:
                if is_sec[si] and not keep[si] and not add[si]:
                    add[si] = True
                    gained += L[si]
            if gained > 0:
                fixed.append((c[0], centres[j][0], round(float(dA[t]) / 1000, 1) if np.isfinite(dA[t]) else None,
                              round(true_b / 1000, 1), round(gained / 1000, 1)))
    rep = {"pairs_fixed": len(fixed), "secondary_km_added": round(float(L[add].sum() / 1000), 1),
           "pairs_from_to_kmBefore_kmWithSecondary_kmSecondaryAdded": fixed,
           "rule": f"crow <= {max_crow_m / 1000:g} km; secondary weighted x{secondary_penalty:g}; main-only path unreachable or "
                   f"> {ratio} x and > {min_gain_m / 1000:g} km longer than the gap-closed path"}
    log(f"  detour bridging: {len(fixed)} province-centre pairs, +{rep['secondary_km_added']} km secondary")
    return keep | add, rep


def stage_raw(work: Path, data: dict, args) -> dict:
    """Directed segment list over OSM nodes (+ densification points), with speeds."""
    tag = "sec" if args.with_secondary else "main"
    path = work / f"raw2_{tag}.pkl"
    if path.exists() and "raw" not in args.force:
        with open(path, "rb") as f:
            return pickle.load(f)
    from scipy.spatial import cKDTree

    classes = set(OSM_CLASSES)  # secondary is filtered below (all of it with --with-secondary, else bridges only)
    ids, lat, lon, inside = data["ids"], data["lat"], data["lon"], data["inside"]
    located = np.isfinite(lat)
    refs_tab: dict[str, int] = {"": 0}
    cols = defaultdict(list)
    dropped = Counter()
    for wid, t, refs in data["roads"]:
        hw = t["highway"]
        if hw not in classes:
            continue
        if not way_usable(t):
            dropped["access/area"] += 1
            continue
        d = way_direction(t)
        if d is None:
            dropped["reversible"] += 1
            continue
        idx = np.searchsorted(ids, refs)
        a, b = idx[:-1], idx[1:]
        keep = (a != b) & located[a] & located[b] & inside[a] & inside[b]
        dropped["segments outside Turkey/unlocated"] += int((~keep & (a != b)).sum())
        if not keep.any():
            continue
        a, b = a[keep], b[keep]
        k = len(a)
        vf = parse_speed(t.get("maxspeed:forward")) or parse_speed(t.get("maxspeed"))
        vb = parse_speed(t.get("maxspeed:backward")) or parse_speed(t.get("maxspeed"))
        if vf is None and vb is None and t.get("maxspeed:type"):
            vf = vb = parse_speed(t.get("maxspeed:type"))
        bridge = (t.get("bridge") or "no") not in ("no",)
        tunnel = (t.get("tunnel") or "no") not in ("no", "culvert")
        ref = norm_ref(t.get("ref"))
        cols["a"].append(a)
        cols["b"].append(b)
        cols["cls"].append(np.full(k, CLASS_CODE[hw], np.int8))
        cols["fwd"].append(np.full(k, d[0]))
        cols["bwd"].append(np.full(k, d[1]))
        cols["vf_tag"].append(np.full(k, np.nan if vf is None else vf, np.float32))
        cols["vb_tag"].append(np.full(k, np.nan if vb is None else vb, np.float32))
        cols["bt"].append(np.full(k, bridge or tunnel))
        cols["rbt"].append(np.full(k, t.get("junction") in ("roundabout", "circular")))
        cols["oneway_way"].append(np.full(k, d[0] != d[1]))
        cols["toll"].append(np.full(k, t.get("toll") == "yes"))
        cols["ref"].append(np.full(k, refs_tab.setdefault(ref, len(refs_tab)), np.int32))
        cols["wid"].append(np.full(k, wid, np.int64))
    seg = {k: np.concatenate(v) for k, v in cols.items()}
    # drop exact duplicate segments (overlapping ways)
    key = seg["a"].astype(np.int64) * (len(ids) + 1) + seg["b"]
    _, first = np.unique(key, return_index=True)
    dup = len(key) - len(first)
    first.sort()
    seg = {k: v[first] for k, v in seg.items()}
    log(f"raw segments incl. secondary: {len(seg['a'])} (dropped {dict(dropped)}, {dup} duplicates)")
    bridge_report = {"mode": "all secondary roads"}
    if not args.with_secondary:
        keep, bridge_report = bridge_with_secondary(seg, lat, lon, min_fragment_m=args.bridge_min_fragment_m,
                                                    max_bridge_m=args.bridge_max_m)
        centres = []
        for _, (t, ms) in data["admin4"].items():
            for ty, ref, ro in ms:
                if ro == "admin_centre" and ref in data["admin_nodes"]:
                    la_, lo_, _ = data["admin_nodes"][ref]
                    centres.append((t.get("name", ""), la_, lo_))
        keep, detour_report = detour_bridges(seg, lat, lon, keep, centres)
        bridge_report["detours"] = detour_report
        bridge_report["secondary_km_total"] = round(float(geodesic_m(
            lat[seg["a"]], lon[seg["a"]], lat[seg["b"]], lon[seg["b"]])[keep & np.isin(
                seg["cls"], [CLASS_CODE[c] for c in SECONDARY_CLASSES])].sum() / 1000), 1)
        seg = {k: v[keep] for k, v in seg.items()}
        log(f"raw segments: {len(seg['a'])} (main roads + {bridge_report['secondary_km_total']} km bridging secondary)")

    # ---- compact node numbering: used OSM nodes first, then densification points
    used = np.unique(np.concatenate([seg["a"], seg["b"]]))
    remap = np.full(len(ids), -1, np.int64)
    remap[used] = np.arange(len(used))
    n_lat, n_lon = list(lat[used]), list(lon[used])
    n_osm = list(ids[used])
    n_minor = list(data["minor_flag"][used])
    a, b = remap[seg["a"]], remap[seg["b"]]
    la, lo = np.asarray(n_lat), np.asarray(n_lon)
    length = geodesic_m(la[a], lo[a], la[b], lo[b])

    # ---- densify segments longer than DENSIFY_M (elevation profile only; extra points are not kept unless needed)
    long_idx = np.nonzero(length > DENSIFY_M)[0]
    new_rows = {k: [] for k in seg}
    new_a, new_b, keep_mask = [], [], np.ones(len(a), bool)
    next_id = len(n_lat)
    for s in long_idx:
        k = int(math.ceil(length[s] / DENSIFY_M))
        u, v = int(a[s]), int(b[s])
        chain = [u]
        for j in range(1, k):
            f = j / k
            n_lat.append(la[u] + f * (la[v] - la[u]))
            n_lon.append(lo[u] + f * (lo[v] - lo[u]))
            n_osm.append(0)
            n_minor.append(False)
            chain.append(next_id)
            next_id += 1
        chain.append(v)
        keep_mask[s] = False
        for p, q in zip(chain[:-1], chain[1:]):
            new_a.append(p)
            new_b.append(q)
            for kk in seg:
                if kk not in ("a", "b"):
                    new_rows[kk].append(seg[kk][s])
    out = {k: np.concatenate([v[keep_mask], np.asarray(new_rows[k], dtype=v.dtype)]) for k, v in seg.items()
           if k not in ("a", "b")}
    out["a"] = np.concatenate([a[keep_mask], np.asarray(new_a, np.int64)])
    out["b"] = np.concatenate([b[keep_mask], np.asarray(new_b, np.int64)])
    nodes = {"lat": np.asarray(n_lat), "lon": np.asarray(n_lon), "osm": np.asarray(n_osm, np.int64),
             "minor": np.asarray(n_minor, bool)}
    out["len"] = geodesic_m(nodes["lat"][out["a"]], nodes["lon"][out["a"]], nodes["lat"][out["b"]],
                            nodes["lon"][out["b"]])
    log(f"densified {len(long_idx)} long segments: {len(out['a'])} segments, {len(nodes['lat'])} points")

    # ---- urban proxy: junctions with minor streets within URBAN_RADIUS_M of the segment midpoint
    mxy = local_xy(nodes["lat"][nodes["minor"]], nodes["lon"][nodes["minor"]])
    tree = cKDTree(mxy)
    mid = local_xy((nodes["lat"][out["a"]] + nodes["lat"][out["b"]]) / 2,
                   (nodes["lon"][out["a"]] + nodes["lon"][out["b"]]) / 2)
    density = tree.query_ball_point(mid, URBAN_RADIUS_M, return_length=True).astype(np.int32)
    out["minor_density"] = density
    # calibrate: threshold that best separates tagged <= 60 km/h from > 60 km/h on non-link, non-motorway roads
    cls = out["cls"]
    is_main = np.isin(cls, [CLASS_CODE[c] for c in ("trunk", "primary", "secondary")]) & ~out["rbt"]
    vt = np.where(np.isnan(out["vf_tag"]), out["vb_tag"], out["vf_tag"])
    tagged = is_main & np.isfinite(vt)
    y = vt[tagged] <= 60.0
    w = out["len"][tagged]
    x = density[tagged]
    best = (0.0, 0)
    calib = []
    for thr in range(1, 31):
        pred = x >= thr
        tpr = w[pred & y].sum() / max(w[y].sum(), 1)
        tnr = w[~pred & ~y].sum() / max(w[~y].sum(), 1)
        bal = 0.5 * (tpr + tnr)
        calib.append((thr, round(float(tpr), 3), round(float(tnr), 3), round(float(bal), 3)))
        if bal > best[0]:
            best = (bal, thr)
    thr = args.urban_threshold or best[1]
    urban = density >= thr
    log(f"urban proxy: threshold {thr} minor junctions within {URBAN_RADIUS_M:.0f} m "
        f"(balanced accuracy {best[0]:.3f} on {w.sum() / 1000:.0f} km of tagged roads)")

    # ---- ramp defaults: ramps have no legal limit of their own -> length-weighted median of the tagged ramps
    link_default = {}
    for name in OSM_CLASSES:
        if not name.endswith("_link"):
            continue
        m = (cls == CLASS_CODE[name]) & np.isfinite(vt) & ~out["rbt"]
        if m.sum() < 50:
            link_default[name] = LINK_DEFAULT
            continue
        o = np.argsort(vt[m])
        cw = np.cumsum(out["len"][m][o])
        med = float(vt[m][o][np.searchsorted(cw, cw[-1] / 2)])
        link_default[name] = float(min(max(round(med / 10) * 10, 30), 90))
    log(f"ramp defaults (km-weighted median of tagged ramps): {link_default}")

    # ---- final speeds
    def default_speed(code: np.ndarray, rbt, oneway, urb) -> np.ndarray:
        name = np.array(OSM_CLASSES)[code]
        v = np.where(oneway, LEGAL_DUAL, LEGAL_RURAL)
        v = np.where(urb, LEGAL_URBAN, v)
        v = np.where(name == "motorway", LEGAL_MOTORWAY, v)
        for ln, lv in link_default.items():
            v = np.where(name == ln, lv, v)
        v = np.where(rbt, ROUNDABOUT_DEFAULT, v)
        return v.astype(np.float32)

    dflt = default_speed(cls, out["rbt"], out["oneway_way"], urban)
    out["vf"] = np.where(np.isnan(out["vf_tag"]), dflt, out["vf_tag"]).astype(np.float32)
    out["vb"] = np.where(np.isnan(out["vb_tag"]), dflt, out["vb_tag"]).astype(np.float32)
    out["urban"] = urban
    # how well would the default rule have predicted the tagged speeds?
    err = {}
    for name in OSM_CLASSES:
        m = (cls == CLASS_CODE[name]) & np.isfinite(vt)
        if m.any():
            err[name] = {"tagged_km": round(float(out["len"][m].sum() / 1000), 1),
                         "untagged_km": round(float(out["len"][(cls == CLASS_CODE[name]) & ~np.isfinite(vt)].sum() / 1000), 1),
                         "default_mae_kmh": round(float(np.average(np.abs(dflt[m] - vt[m]), weights=out["len"][m])), 1),
                         "default_bias_kmh": round(float(np.average(dflt[m] - vt[m], weights=out["len"][m])), 1)}
    res = {"seg": out, "nodes": nodes, "refs": list(refs_tab.keys()),
           "stats": {"dropped_ways": dict(dropped), "duplicate_segments": int(dup), "secondary_bridging": bridge_report,
                     "urban_threshold": int(thr), "urban_calibration_thr_tpr_tnr_balacc": calib,
                     "ramp_default_kmh": link_default, "default_rule_check": err}}
    with open(path, "wb") as f:
        pickle.dump(res, f, protocol=pickle.HIGHEST_PROTOCOL)
    return res


# ============================================================================= stage 4: elevations
def stage_elevation(work: Path, raw: dict, args) -> np.ndarray:
    """Bilinear SRTM-1 sample at every raw point; tiles are downloaded one at a time and deleted."""
    lat, lon = raw["nodes"]["lat"], raw["nodes"]["lon"]
    h = hashlib.md5(np.round(np.c_[lat, lon], 7).tobytes()).hexdigest()[:12]
    path = work / f"elev_{h}.npz"
    if path.exists() and "elevation" not in args.force:
        return np.load(path)["z"]
    tile_dir = work / "raw" / "srtm"
    tile_dir.mkdir(parents=True, exist_ok=True)
    z = np.full(len(lat), np.nan)
    ty, tx = np.floor(lat).astype(int), np.floor(lon).astype(int)
    tiles = sorted(set(zip(ty.tolist(), tx.tolist())))
    stats = Counter()
    for k, (ly, lx) in enumerate(tiles):
        name = f"{'N' if ly >= 0 else 'S'}{abs(ly):02d}{'E' if lx >= 0 else 'W'}{abs(lx):03d}"
        url = SRTM_URL.format(lat_dir=name[:3], name=name)
        gz = tile_dir / f"{name}.hgt.gz"
        for attempt in range(6):  # transient proxy/network errors (HTTP 0, 5xx) must not become 0 m elevations
            code = curl(url, gz)
            if code in (200, 404):
                break
            log(f"  tile {name}: HTTP {code}, retrying ({attempt + 1}/6)")
            time.sleep(5 * (attempt + 1))
        sel = np.nonzero((ty == ly) & (tx == lx))[0]
        if code == 404:  # no SRTM tile: open sea only
            log(f"  tile {name}: not available (404), {len(sel)} points set to 0 m (sea level)")
            z[sel] = 0.0
            stats["missing_tile_points"] += len(sel)
            gz.unlink(missing_ok=True)
            continue
        if code != 200:
            raise SystemExit(f"SRTM tile {name} could not be downloaded (HTTP {code}); rerun later")
        arr = np.frombuffer(gzip.decompress(gz.read_bytes()), dtype=">i2")
        gz.unlink()
        size = int(round(math.sqrt(arr.size)))
        grid = arr.reshape(size, size).astype(np.float64)
        void = grid <= -1000
        res = size - 1
        r = (ly + 1 - lat[sel]) * res
        c = (lon[sel] - lx) * res
        r0 = np.clip(np.floor(r).astype(int), 0, res - 1)
        c0 = np.clip(np.floor(c).astype(int), 0, res - 1)
        fr, fc = r - r0, c - c0
        corners = [(r0, c0, (1 - fr) * (1 - fc)), (r0, c0 + 1, (1 - fr) * fc),
                   (r0 + 1, c0, fr * (1 - fc)), (r0 + 1, c0 + 1, fr * fc)]
        num = np.zeros(len(sel))
        den = np.zeros(len(sel))
        for rr, cc, ww in corners:
            ok = ~void[rr, cc]
            num += np.where(ok, grid[rr, cc] * ww, 0.0)
            den += np.where(ok, ww, 0.0)
        partial = (den > 0) & (den < 0.999999)
        stats["partial_void_points"] += int(partial.sum())
        zz = np.where(den > 0, num / np.maximum(den, 1e-12), np.nan)
        for j in np.nonzero(np.isnan(zz))[0]:  # all four corners void: mean of valid pixels in a growing window
            for rad in (2, 4, 8, 16, 32, 64):
                win = grid[max(r0[j] - rad, 0):r0[j] + rad + 2, max(c0[j] - rad, 0):c0[j] + rad + 2]
                good = win[win > -1000]
                if good.size:
                    zz[j] = float(good.mean())
                    stats["void_filled_points"] += 1
                    break
            else:
                zz[j] = 0.0
                stats["void_unfilled_points"] += 1
        stats["void_pixels_in_tiles"] += int(void.sum())
        z[sel] = zz
        if (k + 1) % 10 == 0 or k + 1 == len(tiles):
            log(f"  SRTM {k + 1}/{len(tiles)} tiles")
    stats["tiles"] = len(tiles)
    np.savez_compressed(path, z=z)
    (work / "elev_stats.json").write_text(json.dumps(dict(stats), indent=1))
    return z


# ============================================================================= stage 5: contraction
def zigzag_extrema(z: np.ndarray, thr: float) -> list[int]:
    """Indices of interior turning points whose rise and fall both exceed ``thr`` (ZigZag filter)."""
    ext: list[int] = []
    n = len(z)
    if n < 3:
        return ext
    piv, cand, trend = 0, 0, 0
    lo_i = hi_i = 0
    for i in range(1, n):
        if trend == 0:
            if z[i] > z[hi_i]:
                hi_i = i
            if z[i] < z[lo_i]:
                lo_i = i
            if z[hi_i] - z[lo_i] >= thr:
                # the first swing: the earlier of the two is a pivot if it is interior and prominent vs the start
                if lo_i < hi_i:
                    if lo_i > 0 and z[piv] - z[lo_i] >= thr:
                        ext.append(lo_i)
                    trend, cand = 1, hi_i
                else:
                    if hi_i > 0 and z[hi_i] - z[piv] >= thr:
                        ext.append(hi_i)
                    trend, cand = -1, lo_i
        elif trend == 1:
            if z[i] > z[cand]:
                cand = i
            elif z[cand] - z[i] >= thr:
                ext.append(cand)
                trend, cand = -1, i
        else:
            if z[i] < z[cand]:
                cand = i
            elif z[i] - z[cand] >= thr:
                ext.append(cand)
                trend, cand = 1, i
    return [i for i in ext if 0 < i < n - 1]


def stage_contract(work: Path, raw: dict, zraw: np.ndarray, args) -> dict:
    """Contract degree-2 chains into edges, keeping terrain extrema and a maximum edge length."""
    seg, nodes = raw["seg"], raw["nodes"]
    a, b = seg["a"], seg["b"]
    n_nodes, n_seg = len(nodes["lat"]), len(a)
    fwd, bwd = seg["fwd"], seg["bwd"]
    # incidence lists (CSR)
    ends = np.concatenate([a, b])
    order = np.argsort(ends, kind="stable")
    start = np.searchsorted(ends[order], np.arange(n_nodes + 1))
    inc_seg = (order % n_seg).astype(np.int64)
    deg = np.diff(start)
    interior = np.zeros(n_nodes, bool)
    cand = np.nonzero(deg == 2)[0]
    s1 = inc_seg[start[cand]]
    s2 = inc_seg[start[cand] + 1]
    o1 = np.where(a[s1] == cand, b[s1], a[s1])
    o2 = np.where(a[s2] == cand, b[s2], a[s2])
    in1 = np.where(b[s1] == cand, fwd[s1], bwd[s1])    # o1 -> v allowed
    out1 = np.where(b[s1] == cand, bwd[s1], fwd[s1])   # v -> o1 allowed
    out2 = np.where(a[s2] == cand, fwd[s2], bwd[s2])   # v -> o2 allowed
    in2 = np.where(a[s2] == cand, bwd[s2], fwd[s2])    # o2 -> v allowed
    ok = (s1 != s2) & (o1 != o2) & (o1 != cand) & (o2 != cand) & (in1 == out2) & (out1 == in2)
    interior[cand[ok]] = True

    # node-level elevations; points strictly inside bridges/tunnels are re-interpolated per chain
    z_node = zraw.astype(np.float64).copy()
    bt_all = np.zeros(n_nodes, bool)  # every incident segment is a bridge/tunnel
    bt_cnt = np.bincount(np.concatenate([a[seg["bt"]], b[seg["bt"]]]), minlength=n_nodes)
    bt_all[(bt_cnt == deg) & (deg > 0)] = True

    used = np.zeros(n_seg, bool)
    keep_node = {}            # raw node -> new id
    new_lat, new_lon, new_z, new_osm = [], [], [], []

    new_kind = []  # why the node exists: KIND_JUNCTION, KIND_EXTREMUM, KIND_LENGTH, KIND_LOOP

    def nid(v: int, zval: float, kind: int) -> int:
        i = keep_node.get(v)
        if i is None:
            i = len(new_lat)
            keep_node[v] = i
            new_lat.append(float(nodes["lat"][v]))
            new_lon.append(float(nodes["lon"][v]))
            new_z.append(float(zval))
            new_osm.append(int(nodes["osm"][v]))
            new_kind.append(kind)
        return i

    E = defaultdict(list)
    n_split_ext = n_split_len = n_chains = 0
    n_len_fix = [0]
    max_edge = args.max_edge_m
    seglen = seg["len"]

    def walk(v0: int, s0: int) -> None:
        nonlocal n_split_ext, n_split_len, n_chains
        chain_nodes = [v0]
        chain_segs = []
        orient = []
        v, s = v0, s0
        while True:
            used[s] = True
            forward = a[s] == v
            w = int(b[s] if forward else a[s])
            chain_segs.append(s)
            orient.append(forward)
            chain_nodes.append(w)
            if not interior[w] or w == v0:
                break
            i0 = start[w]
            nxt = inc_seg[i0] if inc_seg[i0] != s else inc_seg[i0 + 1]
            if used[nxt]:
                break
            v, s = w, int(nxt)
        n_chains += 1
        cs = np.asarray(chain_segs)
        ori = np.asarray(orient)
        cf = np.where(ori, fwd[cs], bwd[cs])
        cb = np.where(ori, bwd[cs], fwd[cs])
        if not (cf.all() or cb.all()):
            return  # inconsistent directions cannot happen for interior nodes; be safe
        go_f, go_b = bool(cf.all()), bool(cb.all())
        L = seglen[cs]
        d = np.concatenate([[0.0], np.cumsum(L)])
        cn = np.asarray(chain_nodes)
        zc = z_node[cn].copy()
        bt = seg["bt"][cs]
        if bt.any():  # interpolate interior points of every bridge/tunnel run
            i = 0
            m = len(bt)
            while i < m:
                if not bt[i]:
                    i += 1
                    continue
                j = i
                while j < m and bt[j]:
                    j += 1
                # run covers segments i..j-1, nodes i..j
                if j - i >= 1 and d[j] > d[i]:
                    f = (d[i + 1:j] - d[i]) / (d[j] - d[i])
                    zc[i + 1:j] = zc[i] + f * (zc[j] - zc[i])
                i = j
        # split points: terrain extrema, loops, maximum length
        kind_of = {0: KIND_JUNCTION, len(cn) - 1: KIND_JUNCTION}
        ext = zigzag_extrema(zc, args.prominence_m)
        n_split_ext += len(ext)
        kind_of.update({i: KIND_EXTREMUM for i in ext})
        if cn[0] == cn[-1] and len(cn) > 2 and len(kind_of) == 2:
            kind_of[int(np.argmin(np.abs(d - d[-1] / 2))) or 1] = KIND_LOOP
        cuts = sorted(kind_of)
        final = [cuts[0]]
        for p, q in zip(cuts[:-1], cuts[1:]):
            span = d[q] - d[p]
            if span > max_edge:
                k = int(math.ceil(span / (max_edge - DENSIFY_M)))
                for j in range(1, k):
                    target = d[p] + span * j / k
                    idx = int(np.argmin(np.abs(d[p:q + 1] - target))) + p
                    if final[-1] < idx < q:
                        final.append(idx)
                        kind_of[idx] = KIND_LENGTH
                        n_split_len += 1
            final.append(q)
        for p, q in zip(final[:-1], final[1:]):
            # chain end points are junctions: zc equals their SRTM value (interpolation only moves interior points)
            u = nid(int(cn[p]), zc[p], kind_of[p])
            v2 = nid(int(cn[q]), zc[q], kind_of[q])
            if u == v2:
                continue
            ss = cs[p:q]
            ll = L[p:q]
            length = float(ll.sum())
            # never shorter than the straight 3-D line between the end nodes (EVRoute derives grades from it)
            chord = float(geodesic_m(nodes["lat"][cn[p]], nodes["lon"][cn[p]], nodes["lat"][cn[q]], nodes["lon"][cn[q]]))
            min_len = math.sqrt(chord * chord + (zc[q] - zc[p]) ** 2)
            if length < min_len:
                length = min_len * 1.0001
                n_len_fix[0] += 1
            cls_len = np.bincount(seg["cls"][ss], weights=ll, minlength=len(OSM_CLASSES))
            cls = OSM_CLASSES[int(np.argmax(cls_len))]
            ref_len = defaultdict(float)
            for r_, l_ in zip(seg["ref"][ss], ll):
                if r_:
                    ref_len[int(r_)] += l_
            ref = raw["refs"][max(ref_len, key=ref_len.get)] if ref_len else ""
            toll = bool((ll * seg["toll"][ss]).sum() > 0.5 * length)
            urban = float((ll * seg["urban"][ss]).sum() / max(length, 1e-9))
            oo = ori[p:q]
            vf = np.where(oo, seg["vf"][ss], seg["vb"][ss]).astype(np.float64)
            vb = np.where(oo, seg["vb"][ss], seg["vf"][ss]).astype(np.float64)
            for flag, uu, vv, sp in ((go_f, u, v2, vf), (go_b, v2, u, vb)):
                if flag:
                    speed = length / float((ll / sp).sum())
                    E["u"].append(uu)
                    E["v"].append(vv)
                    E["len"].append(length)
                    E["speed"].append(speed)
                    E["cls"].append(cls)
                    E["ref"].append(ref)
                    E["toll"].append(toll)
                    E["urban"].append(urban)
                    E["twoway"].append(go_f and go_b)

    junctions = np.nonzero(~interior & (deg > 0))[0]
    for v in junctions:
        for i in range(start[v], start[v + 1]):
            s = int(inc_seg[i])
            if not used[s]:
                walk(int(v), s)
    rest = np.nonzero(~used)[0]  # pure cycles (every node interior)
    n_cycles = 0
    for s in rest:
        if not used[s]:
            n_cycles += 1
            walk(int(a[s]), int(s))
    log(f"contracted: {n_chains} chains ({n_cycles} isolated cycles), {len(new_lat)} nodes, {len(E['u'])} edges; "
        f"splits: {n_split_ext} at terrain extrema, {n_split_len} for length")
    g = {"lat": np.asarray(new_lat), "lon": np.asarray(new_lon), "z": np.asarray(new_z),
         "osm": np.asarray(new_osm, np.int64), "kind": np.asarray(new_kind, np.int8)}
    g.update({k: np.asarray(v) for k, v in E.items()})
    g["stats"] = {"chains": n_chains, "isolated_cycles": n_cycles, "splits_extrema": n_split_ext,
                  "splits_length": n_split_len, "edges_lengthened_to_3d_chord": n_len_fix[0],
                  "junctions_on_bridge_or_tunnel": int((bt_all & ~interior & (deg > 0)).sum())}
    return g


def merge_parallel(g: dict) -> dict:
    """Drop exact duplicate directed edges (same u, v and length within 1 m)."""
    key = {}
    keep = np.ones(len(g["u"]), bool)
    for i, (u, v, L) in enumerate(zip(g["u"], g["v"], g["len"])):
        k = (int(u), int(v), int(round(L)))
        if k in key:
            keep[i] = False
        else:
            key[k] = i
    for k in ("u", "v", "len", "speed", "cls", "ref", "toll", "urban", "twoway"):
        g[k] = g[k][keep]
    return g


def stage_scc(g: dict) -> tuple[dict, dict]:
    """Keep the largest strongly connected component; report what is dropped."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = len(g["lat"])
    A = coo_matrix((np.ones(len(g["u"])), (g["u"], g["v"])), shape=(n, n)).tocsr()
    ncomp, lab = connected_components(A, directed=True, connection="strong")
    sizes = np.bincount(lab)
    big = int(np.argmax(sizes))
    keep_n = lab == big
    keep_e = keep_n[g["u"]] & keep_n[g["v"]]
    drop_km = Counter()
    for c, L, ke in zip(g["cls"], g["len"], keep_e):
        if not ke:
            drop_km[str(c)] += L / 1000.0
    # describe the largest dropped components
    comps = []
    for c in np.argsort(-sizes)[1:11]:
        m = lab == c
        comps.append({"nodes": int(sizes[c]), "lat": round(float(g["lat"][m].mean()), 4),
                      "lon": round(float(g["lon"][m].mean()), 4)})
    remap = np.full(n, -1, np.int64)
    remap[keep_n] = np.arange(keep_n.sum())
    out = {k: g[k][keep_n] for k in ("lat", "lon", "z", "osm", "kind")}
    for k in ("u", "v"):
        out[k] = remap[g[k][keep_e]]
    for k in ("len", "speed", "cls", "ref", "toll", "urban", "twoway"):
        out[k] = g[k][keep_e]
    info = {"components": int(ncomp), "kept_nodes": int(keep_n.sum()), "dropped_nodes": int(n - keep_n.sum()),
            "kept_edges": int(keep_e.sum()), "dropped_edges": int((~keep_e).sum()),
            "dropped_km_by_class_directed": {k: round(v, 1) for k, v in sorted(drop_km.items())},
            "largest_dropped_components": comps}
    log(f"SCC: kept {info['kept_nodes']} nodes / {info['kept_edges']} edges; dropped {info['dropped_nodes']} nodes, "
        f"{info['dropped_edges']} edges in {ncomp - 1} components")
    return out, info


# ============================================================================= junction consolidation
EDGE_KEYS = ("len", "speed", "cls", "ref", "toll", "urban", "twoway")


def consolidate_junctions(g: dict, radius_m: float) -> tuple[dict, dict]:
    """Merge the end nodes of edges shorter than ``radius_m`` (junction consolidation).

    A dual-carriageway crossing, a small roundabout or an interchange core
    consists of several OSM junctions a few metres apart; for intercity EV
    routing they are one place.  Clusters grow from the shortest edge upwards
    and every member must stay within ``radius_m`` of the cluster centroid, so
    a street with many short edges is never collapsed.  Only nodes joined by
    an edge are merged (no new connections), so strong connectivity is
    preserved; turn restrictions are not modelled anyway.  The merged node
    takes the mean position and elevation.  Internal edges disappear (their
    length - at most ~2 x radius per junction passed - is the price, see the
    validation); parallel edges dominated in (length, time) are dropped.
    """
    n = len(g["lat"])
    lat, lon = g["lat"], g["lon"]
    parent = np.arange(n)

    def find(x: int) -> int:
        r = x
        while parent[r] != r:
            r = parent[r]
        while parent[x] != r:
            parent[x], x = r, parent[x]
        return r

    members: dict[int, list[int]] = {}
    short = np.nonzero(g["len"] < radius_m)[0]
    short = short[np.argsort(g["len"][short], kind="stable")]
    refused = 0
    for e in short:
        ru, rv = find(int(g["u"][e])), find(int(g["v"][e]))
        if ru == rv:
            continue
        mu, mv = members.get(ru, [ru]), members.get(rv, [rv])
        allm = mu + mv
        if haversine_m(lat[allm], lon[allm], lat[allm].mean(), lon[allm].mean()).max() > radius_m:
            refused += 1
            continue
        if len(mu) < len(mv):
            ru, rv = rv, ru
        parent[rv] = ru
        members[ru] = allm
        members.pop(rv, None)
    root = np.array([find(i) for i in range(n)])
    _, newid = np.unique(root, return_inverse=True)
    m = int(newid.max()) + 1
    cnt = np.bincount(newid, minlength=m)
    deg = np.bincount(g["u"], minlength=n) + np.bincount(g["v"], minlength=n)
    order = np.lexsort((-deg, newid))
    head = order[np.r_[True, newid[order][1:] != newid[order][:-1]]]
    kind = np.full(m, 99, np.int16)
    np.minimum.at(kind, newid, g["kind"].astype(np.int16))
    out = {"lat": np.bincount(newid, weights=lat, minlength=m) / cnt,
           "lon": np.bincount(newid, weights=lon, minlength=m) / cnt,
           "z": np.bincount(newid, weights=g["z"], minlength=m) / cnt,
           "osm": g["osm"][head], "kind": kind.astype(np.int8)}
    u2, v2 = newid[g["u"]], newid[g["v"]]
    t = g["len"] / (g["speed"] / 3.6)
    groups = defaultdict(list)
    for e in np.nonzero(u2 != v2)[0]:
        groups[(int(u2[e]), int(v2[e]))].append(int(e))
    final = []
    for es in groups.values():
        es.sort(key=lambda e: (t[e], g["len"][e]))
        best = math.inf
        for e in es:  # keep the (time, length) Pareto set
            if g["len"][e] < best - 0.5:
                final.append(e)
                best = g["len"][e]
    final = np.array(sorted(final))
    out["u"], out["v"] = u2[final], v2[final]
    for k in EDGE_KEYS:
        out[k] = g[k][final]
    stats = {"radius_m": radius_m, "nodes_before": n, "nodes_after": m, "edges_before": int(len(g["u"])),
             "edges_after": int(len(final)), "internal_edges_removed": int((u2 == v2).sum()),
             "internal_km_removed_directed": round(float(g["len"][u2 == v2].sum() / 1000), 1),
             "merges_refused_by_radius": refused, "largest_cluster": int(cnt.max())}
    log(f"consolidation (< {radius_m:g} m): {n} -> {m} nodes, {len(g['u'])} -> {len(final)} edges")
    return out, stats


def recontract(g: dict, max_edge_m: float, prominence_m: float) -> tuple[dict, dict]:
    """Contract nodes that became plain pass-through points (degree 2) after SCC and consolidation.

    Only former junctions are candidates (terrain-extremum and length-split
    nodes stay); a node is removed when its two neighbours carry the same
    traffic directions through it, the merged edge stays within
    ``max_edge_m`` and the node is not a local elevation extremum of more than
    ``prominence_m`` relative to its two neighbours.
    """
    n = len(g["lat"])
    cols = {k: list(g[k]) for k in ("u", "v") + EDGE_KEYS}
    alive = [True] * len(cols["u"])
    out_e = [set() for _ in range(n)]
    in_e = [set() for _ in range(n)]
    for e, (u, v) in enumerate(zip(cols["u"], cols["v"])):
        out_e[u].add(e)
        in_e[v].add(e)
    z, kind = g["z"], g["kind"]
    removed = np.zeros(n, bool)

    def add_merged(a: int, b: int) -> None:
        la, lb = cols["len"][a], cols["len"][b]
        length = la + lb
        time = la / cols["speed"][a] + lb / cols["speed"][b]
        e = len(cols["u"])
        cols["u"].append(cols["u"][a])
        cols["v"].append(cols["v"][b])
        cols["len"].append(length)
        cols["speed"].append(length / time)
        cols["cls"].append(cols["cls"][a] if la >= lb else cols["cls"][b])
        ra, rb = cols["ref"][a], cols["ref"][b]
        cols["ref"].append((ra if la >= lb else rb) or ra or rb)
        cols["toll"].append(bool((la * cols["toll"][a] + lb * cols["toll"][b]) > 0.5 * length))
        cols["urban"].append((la * cols["urban"][a] + lb * cols["urban"][b]) / length)
        cols["twoway"].append(bool(cols["twoway"][a] and cols["twoway"][b]))
        alive.append(True)
        out_e[cols["u"][e]].add(e)
        in_e[cols["v"][e]].add(e)
        for old in (a, b):
            alive[old] = False
            out_e[cols["u"][old]].discard(old)
            in_e[cols["v"][old]].discard(old)

    changed = True
    while changed:
        changed = False
        for w in np.nonzero(kind == KIND_JUNCTION)[0]:
            w = int(w)
            if removed[w]:
                continue
            ins, outs = in_e[w], out_e[w]
            nb = {cols["u"][e] for e in ins} | {cols["v"][e] for e in outs}
            if len(nb) != 2 or w in nb:
                continue
            x, y = nb
            if len(ins) == 1 and len(outs) == 1:
                ei, eo = next(iter(ins)), next(iter(outs))
                if cols["u"][ei] == cols["v"][eo]:
                    continue
                pairs = [(ei, eo)]
            elif len(ins) == 2 and len(outs) == 2:
                pi = {cols["u"][e]: e for e in ins}
                po = {cols["v"][e]: e for e in outs}
                if set(pi) != {x, y} or set(po) != {x, y}:
                    continue
                pairs = [(pi[x], po[y]), (pi[y], po[x])]
            else:
                continue
            if any(cols["len"][a] + cols["len"][b] > max_edge_m for a, b in pairs):
                continue
            if not (min(z[x], z[y]) - prominence_m < z[w] < max(z[x], z[y]) + prominence_m):
                continue
            for a, b in pairs:
                add_merged(a, b)
            removed[w] = True
            changed = True
    keep_n = ~removed
    remap = np.full(n, -1, np.int64)
    remap[keep_n] = np.arange(keep_n.sum())
    idx = np.nonzero(alive)[0]
    out = {k: g[k][keep_n] for k in ("lat", "lon", "z", "osm", "kind")}
    out["u"] = remap[np.asarray(cols["u"])[idx]]
    out["v"] = remap[np.asarray(cols["v"])[idx]]
    for k in EDGE_KEYS:
        out[k] = np.asarray(cols[k], dtype=g[k].dtype)[idx] if k != "ref" else np.asarray(cols[k], dtype=object)[idx].astype(str)
    stats = {"nodes_removed": int(removed.sum()), "nodes_after": int(keep_n.sum()), "edges_after": int(len(idx))}
    log(f"re-contraction of pass-through junctions: -{removed.sum()} nodes -> {keep_n.sum()} nodes, {len(idx)} edges")
    return out, stats


def clip_grades(g: dict, max_grade: float, iters: int = 10) -> dict:
    """Remove SRTM artefacts: where an edge is steeper than ``max_grade``, move the end node that
    deviates more from its neighbours into the range its neighbours allow (or to their mean)."""
    u, v, L = g["u"], g["v"], g["len"]
    z = g["z"].astype(np.float64).copy()
    n = len(z)
    before = int((np.abs(z[v] - z[u]) > max_grade * L).sum())
    w = 1.0 / np.maximum(L, 1.0)
    touched = set()
    for _ in range(iters):
        bad = np.abs(z[v] - z[u]) > max_grade * L + 1e-9
        if not bad.any():
            break
        mean = (np.bincount(u, weights=w * z[v], minlength=n) + np.bincount(v, weights=w * z[u], minlength=n)) / \
               np.maximum(np.bincount(u, weights=w, minlength=n) + np.bincount(v, weights=w, minlength=n), 1e-12)
        dev = np.abs(z - mean)
        pick = np.unique(np.where(dev[u[bad]] >= dev[v[bad]], u[bad], v[bad]))
        lo = np.full(n, -np.inf)
        hi = np.full(n, np.inf)
        np.maximum.at(lo, u, z[v] - max_grade * L)
        np.minimum.at(hi, u, z[v] + max_grade * L)
        np.maximum.at(lo, v, z[u] - max_grade * L)
        np.minimum.at(hi, v, z[u] + max_grade * L)
        for x in pick:
            z[x] = min(max(z[x], lo[x]), hi[x]) if lo[x] <= hi[x] else mean[x]
            touched.add(int(x))
    shift = np.abs(z - g["z"])
    after = int((np.abs(z[v] - z[u]) > max_grade * L + 1e-6).sum())
    g["z"] = z
    top = np.argsort(-shift)[:5]
    st = {"max_grade": max_grade, "steep_edges_before": before, "steep_edges_after": after,
          "nodes_adjusted": len(touched), "max_shift_m": round(float(shift.max()), 1),
          "largest_shifts_m_lat_lon_osm": [(round(float(shift[i]), 1), round(float(g["lat"][i]), 5),
                                            round(float(g["lon"][i]), 5), int(g["osm"][i])) for i in top],
          "nodes_shifted_over_50m": int((shift > 50).sum()),
          "median_shift_m_of_adjusted": round(float(np.median(shift[list(touched)])), 1) if touched else 0.0}
    log(f"grade clip {max_grade:.0%}: {before} steep edges -> {after}; {len(touched)} node elevations adjusted "
        f"(max {st['max_shift_m']} m)")
    return st


def enforce_chord(g: dict) -> dict:
    """Every edge at least as long as the straight 3-D line between its end nodes (EVRoute derives grades from it)."""
    chord = geodesic_m(g["lat"][g["u"]], g["lon"][g["u"]], g["lat"][g["v"]], g["lon"][g["v"]])
    c3 = np.sqrt(chord ** 2 + (g["z"][g["v"]] - g["z"][g["u"]]) ** 2) * 1.0001
    fix = g["len"] < c3
    added = float((c3 - g["len"])[fix].sum())
    rel = float(((c3 - g["len"]) / g["len"])[fix].max()) if fix.any() else 0.0
    g["len"] = np.where(fix, c3, g["len"])
    return {"edges": int(fix.sum()), "km_added": round(added / 1000, 2), "max_relative_increase": round(rel, 4)}


# ============================================================================= stations
_NUM = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kw|kva|w)?", re.I)


def parse_power_kw(value: str | None) -> float | None:
    """'180 kW', '22kW', '50 kW;150 kW', '7400 W', '120' -> maximum power in kW."""
    if not value:
        return None
    best = None
    for num, unit in _NUM.findall(str(value)):
        v = float(num.replace(",", "."))
        unit = (unit or "").lower()
        if unit == "w" or (not unit and v > 1000):
            v /= 1000.0
        if 1.0 <= v <= 1000.0:
            best = v if best is None else max(best, v)
    return best


def parse_count(value: str | None) -> int | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("yes",):
        return 1
    if s in ("no", "0"):
        return 0
    nums = re.findall(r"\d+", s)
    return sum(int(x) for x in nums) if nums and re.fullmatch(r"[\d\s;,+]+", s) else None


def canonical_operator(t: dict) -> str:
    for key in ("brand", "operator", "network", "name"):
        v = t.get(key)
        if not v:
            continue
        for pat, name in OPERATORS:
            if re.search(pat, v, re.I):
                return name
    for key in ("brand", "operator", "network"):
        if t.get(key):
            return t[key].strip()
    return "unknown"


def describe_station(t: dict) -> dict:
    """Kind, power, plugs from the tags (rules documented in DATA_LICENSE.md / meta.json)."""
    sockets = {}
    for k, v in t.items():
        m = re.fullmatch(r"socket:([a-z0-9_]+)", k)
        if m:
            sockets.setdefault(m.group(1), {})["count"] = parse_count(v)
        m = re.fullmatch(r"socket:([a-z0-9_]+):output", k)
        if m:
            sockets.setdefault(m.group(1), {})["kw"] = parse_power_kw(v)
    dc = {s: d for s, d in sockets.items() if s in DC_SOCKETS and (d.get("count") is None or d["count"] > 0)}
    ac = {s: d for s, d in sockets.items() if s in AC_SOCKETS and (d.get("count") is None or d["count"] > 0)}
    station_kw = parse_power_kw(t.get("charging_station:output")) or parse_power_kw(t.get("maxpower"))
    cap_raw = t.get("capacity")
    plugs_cap = None
    cap_kw = None
    if cap_raw:
        c = parse_count(cap_raw)
        if c is not None and 0 < c <= 20 and not re.search(r"kw", cap_raw, re.I):
            plugs_cap = c
        else:
            cap_kw = parse_power_kw(cap_raw)  # capacity misused for the power ("120kW", "180")
    dc_kw = max([d["kw"] for d in dc.values() if d.get("kw")], default=None)
    ac_kw = max([d["kw"] for d in ac.values() if d.get("kw")], default=None)
    text = " ".join(str(t.get(k, "")) for k in ("name", "brand", "operator", "network")).lower()
    # kind
    other_kw = station_kw or cap_kw
    if dc:
        kind = "DC"
    elif ac:
        kind = "DC" if (other_kw or 0) > AC_DC_THRESHOLD_KW or (ac_kw or 0) > 43.0 else "AC"
    elif other_kw:
        kind = "DC" if other_kw > AC_DC_THRESHOLD_KW else "AC"
    elif re.search(r"supercharger|\bdc\b|h[ıi]zl[ıi]|\bhpc\b|fast", text):
        kind = "DC"
    else:
        kind = "AC"
    # power
    src = "tag"
    if kind == "DC":
        power = dc_kw or (other_kw if (other_kw or 0) > AC_DC_THRESHOLD_KW else None)
    else:
        power = ac_kw or (other_kw if other_kw and other_kw <= 43.0 else None)
    if power is None:  # derive from voltage x amperage (three-phase AC when >= 380 V)
        nums_v = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t.get("voltage", ""))]
        nums_a = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", t.get("amperage", ""))]
        volt, amp = (max(nums_v) if nums_v else None), (max(nums_a) if nums_a else None)
        if volt and amp:
            # AC: three-phase when >= 380 V (400 V x 32 A = 22.2 kW -> nominal 22 kW); DC: V x I.  A station-level
            # voltage/amperage of 400 V/32 A on a DC site describes its AC points, so DC needs >= 25 kW to count.
            p = (math.sqrt(3) * volt * amp if (kind == "AC" and volt >= 380) else volt * amp) / 1000.0
            if kind == "AC" and 1.0 <= p <= 43.5:
                power, src = float(round(p)), "voltage*amperage"
            elif kind == "DC" and 25.0 <= p <= 400.0:
                power, src = float(round(p)), "voltage*amperage"
    if power is None:
        if kind == "DC" and re.search(r"tesla|supercharger", text):
            power, src = TESLA_DEFAULT_KW, "default:tesla"
        else:
            power, src = (DC_DEFAULT_KW, "default:dc") if kind == "DC" else (AC_DEFAULT_KW, "default:ac")
    # plugs
    if plugs_cap:
        plugs, psrc = plugs_cap, "capacity"
    else:
        counts = [d.get("count") for d in sockets.values() if d.get("count")]
        plugs, psrc = (sum(counts), "sockets") if counts else (1, "default")
    summary = ";".join(f"{s}:{d.get('count') if d.get('count') is not None else '?'}"
                       + (f"@{d['kw']:g}" if d.get("kw") else "") for s, d in sorted(sockets.items()))
    return {"kind": kind, "power_kw": float(power), "power_source": src, "plugs": int(plugs),
            "plugs_source": psrc, "sockets": summary}


def stage_stations(data: dict, g: dict, args) -> tuple[list[dict], dict]:
    from scipy.spatial import cKDTree

    rows = []
    for nid_, (la, lo, t) in data["cs_node"].items():
        rows.append(("node", nid_, la, lo, t))
    ids, lat, lon = data["ids"], data["lat"], data["lon"]

    def centroid(refs):
        """Mean of the distinct vertices of a way (closed rings repeat the first node)."""
        i = np.minimum(np.searchsorted(ids, refs), len(ids) - 1)
        i = i[(ids[i] == refs) & np.isfinite(lat[i])]
        if not len(i):
            return None
        pts = np.unique(np.c_[lat[i], lon[i]], axis=0)
        return float(pts[:, 0].mean()), float(pts[:, 1].mean())

    for wid, (t, refs) in data["cs_way"].items():
        c = centroid(refs)
        if c:
            rows.append(("way", wid, c[0], c[1], t))
    for rid, (t, ms) in data["cs_rel"].items():
        pts = []
        for ty, ref, _ in ms:
            if ty == "n":
                i = np.searchsorted(ids, ref)
                if i < len(ids) and ids[i] == ref and np.isfinite(lat[i]):
                    pts.append((lat[i], lon[i]))
            elif ty == "w" and ref in data["rel_ways"]:
                c = centroid(data["rel_ways"][ref])
                if c:
                    pts.append(c)
        if pts:
            rows.append(("relation", rid, float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts])), t))
    stats = Counter()
    stats["osm_objects"] = len(rows)
    stats["osm_nodes"] = sum(r[0] == "node" for r in rows)
    stats["osm_ways"] = sum(r[0] == "way" for r in rows)
    stats["osm_relations"] = sum(r[0] == "relation" for r in rows)
    ins = inside_turkey(data, [r[2] for r in rows], [r[3] for r in rows])
    stats["outside_turkey"] = int((~ins).sum())
    rows = [r for r, i in zip(rows, ins) if i]
    access = Counter((r[4].get("access") or "(none)") for r in rows)
    stats["access_tags"] = dict(access)
    public = [r for r in rows if (r[4].get("access") or "") not in ("private", "no")
              and r[4].get("motorcar") not in ("no",) and r[4].get("disused") != "yes"]
    stats["dropped_private"] = len(rows) - len(public)
    # snapping
    tree = cKDTree(local_xy(g["lat"], g["lon"]))
    q = local_xy([r[2] for r in public], [r[3] for r in public])
    _, idx = tree.query(q)
    dist = haversine_m(np.array([r[2] for r in public]), np.array([r[3] for r in public]), g["lat"][idx], g["lon"][idx])
    stats["snap_distance_m_percentiles"] = {p: round(float(np.percentile(dist, p)), 1) for p in (10, 25, 50, 75, 90, 95, 99, 100)}
    stats["snap_bins"] = {lab: int(((dist >= lo_) & (dist < hi_)).sum()) for lab, lo_, hi_ in
                          (("<100m", 0, 100), ("100-500m", 100, 500), ("0.5-1km", 500, 1000),
                           ("1-2km", 1000, 2000), ("2-3km", 2000, 3000), (">3km", 3000, 1e12))}
    out = []
    for r, v, dd in zip(public, idx, dist):
        if dd > args.snap_max_m:
            continue
        t = r[4]
        info = describe_station(t)
        out.append({"osm_type": r[0], "osm_id": r[1], "lat": round(r[2], 7), "lon": round(r[3], 7), "node": int(v),
                    "snap_m": round(float(dd), 1), **info, "operator": canonical_operator(t),
                    "operator_raw": t.get("operator", ""), "brand": t.get("brand", ""),
                    "network": t.get("network", ""), "name": t.get("name", ""),
                    "access": t.get("access", ""), "fee": t.get("fee", ""),
                    "opening_hours": t.get("opening_hours", "")})
    stats["dropped_far"] = len(public) - len(out)
    stats["kept"] = len(out)
    log(f"stations: {stats['osm_objects']} OSM objects, {len(rows)} in Turkey, {len(public)} public, "
        f"{len(out)} within {args.snap_max_m:.0f} m of the network")
    return out, dict(stats)


# ============================================================================= places
def stage_places(data: dict, g: dict) -> tuple[list[dict], dict]:
    from scipy.spatial import cKDTree

    centres = {}  # node id -> (province name, plate)
    for rid, (t, ms) in data["admin4"].items():
        iso = t.get("ISO3166-2", "")
        for ty, ref, ro in ms:
            if ro == "admin_centre":
                centres[ref] = (t.get("name", ""), iso.split("-")[1] if "-" in iso else "")
    rows = {}
    for nid_, la, lo, t in data["places"]:
        rows[nid_] = (la, lo, t)
    for nid_, (la, lo, t) in data["admin_nodes"].items():
        if nid_ in centres and nid_ not in rows:
            rows[nid_] = (la, lo, t)
    keys = list(rows)
    lat = np.array([rows[k][0] for k in keys])
    lon = np.array([rows[k][1] for k in keys])
    ins = inside_turkey(data, lat, lon)
    keys = [k for k, i in zip(keys, ins) if i]
    lat, lon = lat[ins], lon[ins]
    tree = cKDTree(local_xy(g["lat"], g["lon"]))
    _, idx = tree.query(local_xy(lat, lon))
    dist = haversine_m(lat, lon, g["lat"][idx], g["lon"][idx])
    out = []
    for k, la, lo, v, dd in zip(keys, lat, lon, idx, dist):
        t = rows[k][2]
        prov = centres.get(k)
        pop = t.get("population", "")
        try:
            pop = int(float(pop.replace(".", "").replace(",", ""))) if pop else ""
        except ValueError:
            pop = ""
        out.append({"osm_id": k, "name": t.get("name", ""), "name_en": t.get("name:en", ""),
                    "place": t.get("place", ""), "lat": round(float(la), 7), "lon": round(float(lo), 7),
                    "node": int(v), "snap_m": round(float(dd), 1), "population": pop,
                    "province_centre": 1 if prov else 0, "province": prov[0] if prov else "",
                    "plate": prov[1] if prov else ""})
    out.sort(key=lambda r: (-r["province_centre"], r["plate"], r["name"]))
    pc = [r for r in out if r["province_centre"]]
    stats = {"places": len(out), "province_centres": len(pc), "city": sum(r["place"] == "city" for r in out),
             "town": sum(r["place"] == "town" for r in out),
             "snap_m_percentiles": {p: round(float(np.percentile(dist, p)), 1) for p in (50, 90, 99, 100)},
             "province_centre_snap_m_max": round(max(r["snap_m"] for r in pc), 1) if pc else None,
             "province_centres_farthest": sorted(((r["snap_m"], r["name"]) for r in pc), reverse=True)[:5]}
    log(f"places: {len(out)} ({len(pc)} province centres), median snap {stats['snap_m_percentiles'][50]} m")
    return out, stats


# ============================================================================= validation
def stage_validate(work: Path, g: dict, places: list[dict]) -> tuple[list[dict], dict]:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra

    xlsx = work / "raw" / "kgm_ilmesafe.xlsx"
    if not xlsx.exists():
        return [], {"skipped": "no KGM table"}
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    rows = [r for r in wb.worksheets[0].iter_rows(values_only=True)]
    table_date = rows[0][0]
    header = rows[1]
    plates, names, mat = [], [], []
    for r in rows[2:]:
        if r[0] is None or r[1] is None:
            continue
        plates.append(f"{int(r[0]):02d}")
        names.append(str(r[1]))
        mat.append([float(x) if isinstance(x, (int, float)) else np.nan for x in r[2:2 + 81]])
    mat = np.array(mat)
    centre = {r["plate"]: r for r in places if r["province_centre"]}
    n = len(g["lat"])
    W = coo_matrix((g["len"], (g["u"], g["v"])), shape=(n, n)).tocsr()
    T = coo_matrix((g["len"] / (g["speed"] / 3.6), (g["u"], g["v"])), shape=(n, n)).tocsr()
    have = [p for p in plates if p in centre]
    src = [centre[p]["node"] for p in have]
    D = dijkstra(W, directed=True, indices=src)
    TT = dijkstra(T, directed=True, indices=src)
    pos = {p: i for i, p in enumerate(have)}
    free_all = ~np.asarray(g["toll"], bool)
    DF = dijkstra(coo_matrix((g["len"][free_all], (g["u"][free_all], g["v"][free_all])), shape=(n, n)).tocsr(),
                  directed=True, indices=src)
    errs, errs_nt, worst = [], [], []
    for i, p in enumerate(plates):
        for j, q in enumerate(plates):
            if i >= j or p not in pos or q not in pos or not np.isfinite(mat[i, j]):
                continue
            off = (centre[p]["snap_m"] + centre[q]["snap_m"]) / 1000.0
            d = 0.5 * (D[pos[p], centre[q]["node"]] + D[pos[q], centre[p]["node"]]) / 1000.0 + off
            dn = 0.5 * (DF[pos[p], centre[q]["node"]] + DF[pos[q], centre[p]["node"]]) / 1000.0 + off
            errs.append((d - mat[i, j]) / mat[i, j])
            errs_nt.append((dn - mat[i, j]) / mat[i, j])
            worst.append((round(100 * errs[-1], 1), centre[p]["name"], centre[q]["name"], mat[i, j], round(d, 1)))
    errs, errs_nt = np.array(errs), np.array(errs_nt)
    pairs = [("KARAMAN", "İSTANBUL"), ("ANTALYA", "ANKARA"), ("İZMİR", "ERZURUM"), ("İSTANBUL", "ANKARA"),
             ("ANKARA", "ADANA"), ("KARAMAN", "MERSİN"), ("KARAMAN", "KONYA"), ("İSTANBUL", "İZMİR"),
             ("ANKARA", "SAMSUN"), ("TRABZON", "ERZURUM"), ("GAZİANTEP", "ŞANLIURFA"), ("DİYARBAKIR", "VAN"),
             ("KAYSERİ", "SİVAS"), ("EDİRNE", "İSTANBUL"), ("BURSA", "ESKİŞEHİR"), ("KONYA", "ANTALYA"),
             ("DENİZLİ", "MUĞLA"), ("ERZURUM", "KARS"), ("SİNOP", "KASTAMONU"), ("HAKKARİ", "VAN")]
    idx = {nm: i for i, nm in enumerate(names)}
    # second metric: the same distance avoiding toll roads (KGM's table follows the state-road network)
    free = ~np.asarray(g["toll"], bool)
    Wf = coo_matrix((g["len"][free], (g["u"][free], g["v"][free])), shape=(n, n)).tocsr()
    pair_plates = sorted({plates[idx[x]] for pq in pairs for x in pq if x in idx and plates[idx[x]] in pos})
    Df = dijkstra(Wf, directed=True, indices=[centre[p]["node"] for p in pair_plates])
    fpos = {p: i for i, p in enumerate(pair_plates)}
    table = []
    for p_, q_ in pairs:
        i, j = idx.get(p_), idx.get(q_)
        if i is None or j is None:
            continue
        p, q = plates[i], plates[j]
        if p not in pos or q not in pos:
            continue
        d_pq = D[pos[p], centre[q]["node"]] / 1000.0
        d_qp = D[pos[q], centre[p]["node"]] / 1000.0
        t_pq = TT[pos[p], centre[q]["node"]] / 3600.0
        off = (centre[p]["snap_m"] + centre[q]["snap_m"]) / 1000.0
        nt = 0.5 * (Df[fpos[p], centre[q]["node"]] + Df[fpos[q], centre[p]["node"]]) / 1000.0 + off
        table.append({"from": centre[p]["name"], "to": centre[q]["name"], "kgm_km": mat[i, j],
                      "graph_km_ab": round(d_pq + off, 1), "graph_km_ba": round(d_qp + off, 1),
                      "rel_error_pct": round(100 * ((d_pq + d_qp) / 2 + off - mat[i, j]) / mat[i, j], 1),
                      "graph_km_no_toll": round(float(nt), 1) if np.isfinite(nt) else "",
                      "rel_error_no_toll_pct": round(100 * (nt - mat[i, j]) / mat[i, j], 1) if np.isfinite(nt) else "",
                      "free_flow_h_ab": round(t_pq, 2)})
    stats = {"kgm_table_date": str(table_date), "pairs": int(len(errs)),
             "mean_rel_error_pct": round(float(100 * errs.mean()), 2),
             "mean_abs_rel_error_pct": round(float(100 * np.abs(errs).mean()), 2),
             "median_abs_rel_error_pct": round(float(100 * np.median(np.abs(errs))), 2),
             "p90_abs_rel_error_pct": round(float(100 * np.percentile(np.abs(errs), 90)), 2),
             "max_abs_rel_error_pct": round(float(100 * np.abs(errs).max()), 2),
             "share_within_5pct": round(float(np.mean(np.abs(errs) <= 0.05)), 3),
             "share_within_10pct": round(float(np.mean(np.abs(errs) <= 0.10)), 3),
             "no_toll_pairs_finite": int(np.isfinite(errs_nt).sum()),
             "no_toll_mean_rel_error_pct": round(float(100 * np.mean(errs_nt[np.isfinite(errs_nt)])), 2),
             "no_toll_mean_abs_rel_error_pct": round(float(100 * np.mean(np.abs(errs_nt[np.isfinite(errs_nt)]))), 2),
             "worst_pairs_pct_name_name_kgm_graph": sorted(worst, key=lambda r: -abs(r[0]))[:10]}
    log(f"validation vs KGM ({len(errs)} pairs): mean abs rel error {stats['mean_abs_rel_error_pct']} %")
    return table, stats


# ============================================================================= write
def write_csv_gz(path: Path, header: list[str], rows) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as f:
        f.write(buf.getvalue().encode("utf-8"))


def fmt(x: float, nd: int) -> str:
    s = f"{x:.{nd}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def stage_write(out: Path, g: dict, stations: list[dict], places: list[dict], meta: dict, validation: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    write_csv_gz(out / "nodes.csv.gz", ["id", "lat", "lon", "elevation_m", "osm_id"],
                 ((i, fmt(la, 6), fmt(lo, 6), fmt(z, 1), int(o))
                  for i, (la, lo, z, o) in enumerate(zip(g["lat"], g["lon"], g["z"], g["osm"]))))
    write_csv_gz(out / "edges.csv.gz", ["u", "v", "length_m", "speed_kmh", "road_class", "ref", "toll"],
                 ((int(u), int(v), fmt(L, 1), fmt(s, 1), EVROUTE_CLASS[str(c)], r, int(t))
                  for u, v, L, s, c, r, t in zip(g["u"], g["v"], g["len"], g["speed"], g["cls"], g["ref"], g["toll"])))
    st_cols = ["id", "osm_type", "osm_id", "lat", "lon", "node", "snap_m", "kind", "power_kw", "plugs",
               "power_source", "plugs_source", "sockets", "operator", "operator_raw", "brand", "network", "name",
               "access", "fee", "opening_hours"]
    write_csv_gz(out / "stations.csv.gz", st_cols,
                 ([i] + [s[c] if not isinstance(s[c], float) else fmt(s[c], 7 if c in ("lat", "lon") else 1)
                         for c in st_cols[1:]] for i, s in enumerate(stations)))
    pl_cols = ["id", "name", "name_en", "place", "lat", "lon", "node", "snap_m", "population", "province_centre",
               "province", "plate", "osm_id"]
    write_csv_gz(out / "places.csv.gz", pl_cols, ([i] + [p[c] for c in pl_cols[1:]] for i, p in enumerate(places)))
    if validation:
        with open(out / "validation.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(validation[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(validation)
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


# ============================================================================= main
def graph_stats(g: dict) -> dict:
    """Length by class: every directed edge, and 'road km' (two-way roads once, each carriageway of a
    dual carriageway once, as it is mapped in OSM)."""
    directed = Counter()
    road = Counter()
    for L, c, tw in zip(g["len"], g["cls"], g["twoway"]):
        c = str(c)
        directed[c] += L / 1000.0
        road[c] += L / (2000.0 if tw else 1000.0)
    lens = np.asarray(g["len"])
    return {"nodes": int(len(g["lat"])), "edges": int(len(g["u"])),
            "directed_km_by_class": {k: round(v, 1) for k, v in sorted(directed.items())},
            "road_km_by_class": {k: round(v, 1) for k, v in sorted(road.items())},
            "road_km_total": round(sum(road.values()), 1),
            "edge_length_m": {"min": round(float(lens.min()), 1), "median": round(float(np.median(lens)), 1),
                              "mean": round(float(lens.mean()), 1), "max": round(float(lens.max()), 1)},
            "elevation_m": {"min": round(float(g["z"].min()), 1), "max": round(float(g["z"].max()), 1)},
            "speed_kmh_length_weighted": round(float(np.average(g["speed"], weights=g["len"])), 1),
            "share_km_tagged_note": "see raw_stats.default_rule_check"}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    ap.add_argument("--pbf-url", default=PBF_URL)
    ap.add_argument("--with-secondary", action="store_true", help="also use highway=secondary(_link)")
    ap.add_argument("--max-edge-m", type=float, default=5000.0)
    ap.add_argument("--prominence-m", type=float, default=20.0)
    ap.add_argument("--snap-max-m", type=float, default=3000.0)
    ap.add_argument("--urban-threshold", type=int, default=0, help="0 = calibrate on tagged roads")
    ap.add_argument("--consolidate-m", type=float, default=30.0,
                    help="merge the end nodes of edges shorter than this (junction consolidation; 0 = off)")
    ap.add_argument("--bridge-min-fragment-m", type=float, default=2000.0,
                    help="reconnect main-road fragments with at least this much road via secondary roads")
    ap.add_argument("--bridge-max-m", type=float, default=30000.0,
                    help="longest secondary bridge accepted (each direction)")
    ap.add_argument("--force", nargs="*", default=[],
                    help="stages to recompute: download extract raw elevation (stage caches are not keyed by the "
                         "options: pass --force raw after changing --with-secondary/--bridge-*/--urban-threshold; "
                         "delete WORKDIR/raw/turkey.osm.pbf to fetch a newer extract)")
    args = ap.parse_args(argv)
    args.force = set(args.force)
    work = args.workdir
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    dl = stage_download(work, args)
    data = stage_extract(work, args)
    raw = stage_raw(work, data, args)
    z = stage_elevation(work, raw, args)
    g = stage_contract(work, raw, z, args)
    contract_stats = g.pop("stats")
    g = merge_parallel(g)
    g, scc = stage_scc(g)
    g, cons_stats = consolidate_junctions(g, args.consolidate_m)
    g, rec_stats = recontract(g, args.max_edge_m, args.prominence_m)
    grade_stats = clip_grades(g, MAX_GRADE)
    grade_stats["lengthened_to_3d_chord"] = enforce_chord(g)
    stats = graph_stats(g)
    log(f"graph: {stats['nodes']} nodes, {stats['edges']} edges, {stats['road_km_total']} road km")
    stations, st_stats = stage_stations(data, g, args)
    places, pl_stats = stage_places(data, g)
    validation, val_stats = stage_validate(work, g, places)
    plugs = Counter()
    for st_ in stations:
        plugs[st_["kind"]] += st_["plugs"]
    coverage = {"osm_stations_kept": len(stations), "osm_plugs_ac": plugs["AC"], "osm_plugs_dc": plugs["DC"],
                "share_of_epdk_ac_sockets_pct": round(100 * plugs["AC"] / EPDK_STATS["sockets_ac"], 1),
                "share_of_epdk_dc_sockets_pct": round(100 * plugs["DC"] / EPDK_STATS["sockets_dc"], 1),
                "note": "OSM plug counts default to 1 where untagged, so they are lower bounds; EPDK counts sockets of "
                        "all commercial stations"}
    elev_stats = json.loads((work / "elev_stats.json").read_text()) if (work / "elev_stats.json").exists() else {}
    meta = {
        "name": "EVRoute Turkey real-data network",
        "build_utc": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "build_script": "build_osm_turkey.py",
        "sources": {
            "osm": {**dl, "licence": "ODbL 1.0, © OpenStreetMap contributors",
                    "licence_url": "https://www.openstreetmap.org/copyright"},
            "srtm": {"url_template": SRTM_URL, "product": "SRTM 1 arc-second (skadi HGT tiles, AWS terrain tiles)",
                     "licence": "public domain (NASA/USGS)", **elev_stats},
            "kgm_distances": {"url": KGM_URL, "use": "validation only", "table_date": val_stats.get("kgm_table_date")},
        },
        "parameters": {"classes": list(MAIN_CLASSES) + (list(SECONDARY_CLASSES) if args.with_secondary else []),
                       "max_edge_m": args.max_edge_m, "prominence_m": args.prominence_m,
                       "densify_m": DENSIFY_M, "snap_max_m": args.snap_max_m,
                       "speed_defaults_kmh": {"urban": LEGAL_URBAN, "rural_single": LEGAL_RURAL,
                                              "rural_dual_carriageway": LEGAL_DUAL, "motorway": LEGAL_MOTORWAY,
                                              "link": LINK_DEFAULT, "roundabout": ROUNDABOUT_DEFAULT},
                       "urban_proxy": f"junctions with {MINOR_STREETS} within {URBAN_RADIUS_M:.0f} m >= threshold",
                       "station_defaults_kw": {"AC": AC_DEFAULT_KW, "DC": DC_DEFAULT_KW, "Tesla": TESLA_DEFAULT_KW},
                       "ac_dc_threshold_kw": AC_DC_THRESHOLD_KW},
        "graph": stats, "contraction": contract_stats, "scc": scc, "consolidation": cons_stats,
        "recontraction": rec_stats, "grade_clip": grade_stats, "raw_stats": raw["stats"],
        "stations": st_stats, "official_charging_statistics": EPDK_STATS, "osm_coverage_vs_epdk": coverage,
        "places": pl_stats, "validation": val_stats,
        "licences": {
            "database": "ODbL 1.0 (https://opendatacommons.org/licenses/odbl/1-0/) - derived from OpenStreetMap",
            "attribution": "© OpenStreetMap contributors, available under the Open Database License (https://www.openstreetmap.org/copyright)",
            "elevation": "SRTM 1 arc-second, NASA/USGS, public domain (via AWS Terrain Tiles)",
            "prices": "facts with source URL and date (prices.json); no licence claimed",
            "code": "build_osm_turkey.py: MIT (EVRoute)",
            "details": "DATA_LICENSE.md",
        },
        "rules": {
            "speed_sources": [
                "Karayolları Trafik Yönetmeliği Art. 100 (cars: 50 built-up / 90 two-lane rural / 110 dual carriageway / 120 motorway; signs may raise by up to 20 km/h)",
                "OSM wiki Default speed limits, TR row (urban 50, rural 90, dual carriageway 110, motorway 120): https://wiki.openstreetmap.org/wiki/Default_speed_limits",
            ],
            "station_kind": "DC if any DC socket type (type2_combo, type1_combo, chademo, gb_dc, tesla_supercharger*, ccs*); "
                            "else AC if any AC socket type unless a station-level power > 22 kW (or an AC socket > 43 kW) is tagged; "
                            "without socket tags: DC if a tagged power > 22 kW or name/brand says supercharger/DC/hızlı/HPC/fast, else AC",
            "station_power": "max socket:*:output of the station's kind; else charging_station:output/maxpower/capacity misused as kW; "
                             "else voltage x amperage (sqrt(3) for >= 380 V AC; DC only if >= 25 kW); else defaults AC 22, DC 120, Tesla 250 kW",
            "station_plugs": "capacity (if a count <= 20), else the sum of socket:* counts, else 1",
            "station_access": "dropped: access=private/no, motorcar=no, disused=yes; kept and recorded: yes/customers/permissive/permit/none",
        },
        "files": {"nodes.csv.gz": "id,lat,lon,elevation_m,osm_id",
                  "edges.csv.gz": "u,v,length_m,speed_kmh,road_class,ref,toll (directed)",
                  "stations.csv.gz": "see header", "places.csv.gz": "see header",
                  "validation.csv": "graph vs KGM distances", "prices.json": "operator price list (hand-curated)",
                  "DATA_LICENSE.md": "licences and attribution"},
    }
    stage_write(args.outdir, g, stations, places, meta, validation)
    with open(work / "graph_final.pkl", "wb") as f:
        pickle.dump({"g": g, "stations": stations, "places": places}, f, protocol=pickle.HIGHEST_PROTOCOL)
    log(f"done in {time.time() - t0:.0f} s -> {args.outdir}")


if __name__ == "__main__":
    main()
