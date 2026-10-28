# Real-data network of Turkey (OpenStreetMap)

**Road network, places and charging stations © OpenStreetMap contributors,
available under the Open Database License (ODbL 1.0,
https://www.openstreetmap.org/copyright).** Elevations: SRTM 1 arc-second,
NASA/USGS, public domain.

These files are *data*, not code: they are licensed under the ODbL 1.0 (the
tables derived from OpenStreetMap) or are cited facts (prices, official
statistics), while the EVRoute code around them is MIT. Every map, figure,
route plan or table made from them must carry the credit
"© OpenStreetMap contributors" (the EVRoute viewer and figures do it
automatically). An adapted version of this database that you publish must
be offered under the ODbL as well. Details: [`DATA_LICENSE.md`](DATA_LICENSE.md).

Research data, **not for navigation**.

## Files

| file | content |
|---|---|
| `nodes.csv.gz` | 44 673 nodes: `id, lat, lon, elevation_m, osm_id` (`osm_id` 0 = a point inserted by the build) |
| `edges.csv.gz` | 70 886 directed edges: `u, v, length_m, speed_kmh, road_class, ref, toll` (a two-way road is two rows) |
| `stations.csv.gz` | 711 public OSM charging stations snapped to the network (kind, power, plugs, operator, and where each value came from) |
| `places.csv.gz` | 1 005 cities and towns, among them the 81 province centres, snapped to the network |
| `prices.json` | operator list prices in TRY/kWh (VAT included), with source URL, access date and verification level; the March 2026 charging regulation |
| `meta.json` | sources, OSM timestamp and PBF checksum, build parameters, statistics, EPDK charging statistics, licences |
| `validation.csv` | road distances on the network against the official KGM table for 20 city pairs |
| `DATA_LICENSE.md` | licences and attribution of every file |

## Provenance

| source | used for |
|---|---|
| OpenStreetMap extract of Turkey, `https://download.openstreetmap.fr/extracts/europe/turkey.osm.pbf`, replication timestamp 2026-10-27T02:09:23Z (sequence 7346288, md5 `b03e7851f3312d6f5b552985c10e31db`) | roads (motorway, trunk, primary and their ramps; 263 km of secondary roads that close gaps), charging stations, places |
| SRTM 1" (skadi tiles of the AWS Terrain Tiles, `https://s3.amazonaws.com/elevation-tiles-prod/skadi/`) | node elevations (bridges and tunnels interpolated, grades above 15 % clipped) |
| KGM "İller arası mesafeler" (`ilmesafe.xlsx`, table of 2026-03-03) | validation only: over 3 240 pairs of province centres the network distance deviates by 2.43 % on average, 89.8 % of the pairs within 5 % |
| EPDK, Şarj Hizmeti Piyasası Aylık İstatistikleri, September 2026 | coverage statement (48 971 public sockets, 27 664 AC and 21 307 DC: OSM holds about 3.3 % of the AC and 1.9 % of the DC sockets) and the queue assumptions of the loader (sessions per plug, session length, hourly profile) |
| operator price pages (accessed 2026-10-27) and Resmî Gazete 23.03.2026, No. 33202 | `prices.json` |

Speeds are the OSM `maxspeed` where tagged, otherwise the legal defaults of
Karayolları Trafik Yönetmeliği Art. 100 (50 km/h built up, 90 rural, 110
dual carriageway, 120 motorway) with a built-up-area detector; missing
station powers default to 22 kW AC, 120 kW DC and 250 kW at a Tesla
Supercharger, missing plug counts to 1. The full list of rules and their
statistics is in `meta.json`; the loader
[`evroute.generators.osm_turkey`](../../generators/osm_turkey.py) documents
how stations, prices and queues become a scenario.

## Rebuilding

The files are built by `tools/build_osm_turkey.py` in the EVRoute
repository (not part of the installed package). It downloads the OSM extract
(~700 MB), the SRTM tiles and the KGM table itself; a full run takes about
10 minutes and 1 GB of disk, later runs reuse the cached stages of its work
directory:

```bash
pip install osmium numpy scipy shapely openpyxl
python -I tools/build_osm_turkey.py --workdir /tmp/osm-work --outdir src/evroute/data/osm_turkey
```

It rewrites `nodes`, `edges`, `stations`, `places`, `validation.csv` and
`meta.json`; `prices.json`, `DATA_LICENSE.md` and this README are maintained
by hand. A newer extract changes the network, so the numbers above (and the
tests that check them against `meta.json`) follow the new build.
