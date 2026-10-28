# Data licence: EVRoute real-data Turkey network

The files in this directory are data, not code. They come under different
terms from the MIT-licensed EVRoute source code.

| File | Content | Licence |
|---|---|---|
| `nodes.csv.gz`, `edges.csv.gz` | road network derived from OpenStreetMap; node elevations from SRTM | **ODbL 1.0**, © OpenStreetMap contributors (elevations: public domain, see below) |
| `stations.csv.gz` | public charging stations derived from OpenStreetMap | **ODbL 1.0**, © OpenStreetMap contributors |
| `places.csv.gz` | cities, towns and the 81 province centres derived from OpenStreetMap | **ODbL 1.0**, © OpenStreetMap contributors |
| `validation.csv` | road distances computed on the network, compared with the official KGM table | ODbL 1.0 (derived from the database); the KGM distances are facts cited from the source below |
| `prices.json` | public charging list prices of Turkish operators | facts with source URL and date; no licence is claimed |
| `meta.json` | provenance, parameters and statistics | ODbL 1.0 as part of the database |
| `tools/build_osm_turkey.py` (in the EVRoute repository, not in the package) | the build script | MIT, like the rest of EVRoute |

## OpenStreetMap: ODbL 1.0

The road, station and place tables are a **derivative database** of
OpenStreetMap. OpenStreetMap is open data licensed under the
[Open Data Commons Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
by the OpenStreetMap Foundation.

**Attribution.** Any use must credit OpenStreetMap, for example:

> Road network and charging stations © OpenStreetMap contributors, available under the Open Database License
> (https://www.openstreetmap.org/copyright).

Put this credit wherever the data, or anything produced from it (maps,
figures, route plans, tables in papers, the EVRoute viewer), is shown. See
https://www.openstreetmap.org/copyright and the
[OSMF attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines).

**Share-alike.** If you publicly use an *adapted* version of this database
(for example with corrected roads or added stations), or a work produced
from an adapted database, you must also offer that adapted database under
the ODbL 1.0, or make the changes available. A *produced work*, such as a
figure, a route or a statistic, may be published under any terms as long
as it carries the attribution above. Shipping these files inside the
EVRoute package is allowed: the code stays MIT, and these data files stay
under the ODbL. Package metadata and the README should say so.

**Snapshot.** The files are built from the OpenStreetMap France extract
`https://download.openstreetmap.fr/extracts/europe/turkey.osm.pbf` with OSM
replication timestamp 2026-10-27T02:09:23Z (sequence 7346288). Every
station row keeps its `osm_type`/`osm_id`, and every node keeps its
`osm_id` (`0` = a point inserted by the build), so the source objects can
be traced and corrected upstream.

## SRTM elevations: public domain

Node elevations are bilinear samples of SRTM 1 arc-second data (NASA/USGS
Shuttle Radar Topography Mission), read from the "skadi" HGT tiles of the
AWS Terrain Tiles open dataset
(`https://s3.amazonaws.com/elevation-tiles-prod/skadi/`). SRTM is a work
of the U.S. Government and is in the **public domain**. A courtesy credit
is appreciated: "SRTM elevation data courtesy of NASA/USGS; tiles via the AWS
Terrain Tiles open dataset (Mapzen/Linux Foundation)". See the source
attribution list at https://github.com/tilezen/joerd/blob/master/docs/attribution.md.
The build changed some elevations: bridges and tunnels are interpolated
between their portals, and implausible grades above 15 % were clipped
(see `meta.json` → `grade_clip`).

## Charging prices: facts with sources

`prices.json` lists publicly advertised list prices (TRY/kWh, VAT
included) of charging operators. For each operator it gives the page it was
read from and the date. Prices are facts and are not licensed here. Brand
names belong to their owners. Prices change often, and under the Şarj Hizmeti
Yönetmeliği (Resmî Gazete 23.03.2026, No. 33202) they may also vary by
location and time of day. Treat them as a dated snapshot. Entries
marked `"unknown"` or `"press"` were not confirmed on the operator's own
page.

## Official statistics and distances (cited, not redistributed)

* EPDK, *Şarj Hizmeti Piyasası Aylık İstatistikleri – Eylül 2026*
  (https://www.epdk.gov.tr/Detay/Icerik/3-0-222/enerji-donusumusarj-hizmeti-piyasasi--istatistik).
  `meta.json` cites figures from it; the report itself is not included.
* KGM, *İller arası mesafeler*
  (https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Root/Uzakliklar/ilmesafe.xlsx,
  table dated 2026-03-03). This table is used for validation only. `validation.csv`
  quotes 20 of its distances.

## Disclaimer

This is research data, **not for navigation**. OSM charging coverage in
Turkey is far from complete: 711 OSM stations (909 AC + 405 DC plugs), against 48,971 commercial
sockets in the official EPDK statistics for September 2026. Free-flow speeds
are legal limits or imputed defaults, not observed traffic. The data is
provided "as is", without warranty of any kind.
