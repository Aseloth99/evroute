# Real-data case study: Turkey on OpenStreetMap

**English** · [Türkçe](real-data.tr.md)

EVRoute ships a second Turkey case study built from real data: the
OpenStreetMap (OSM) road network of Turkey with SRTM elevations, the public
charging stations mapped in OSM, and the list prices that the charging
operators publish.  It complements the approximate case study
([`index.md`](index.md#turkey-case-study)), whose geography is
gazetteer-level and whose 290 charging stations are synthetic, and it asks
what changes on a real network 35 times larger, with the chargers that are
actually mapped.

> [!IMPORTANT]
> **Road network, places and charging stations © OpenStreetMap contributors,
> available under the Open Database License (ODbL 1.0,
> [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright)).**
> Elevations: SRTM, NASA/USGS, public domain.  The bundled tables are a
> derivative database of OpenStreetMap; the EVRoute code stays MIT
> ([licences](#licences)).  Research data, **not for navigation**: speeds
> are legal limits, OSM knows only a small part of the chargers, prices are
> a dated snapshot and the queues are assumptions.

On this page: [at a glance](#at-a-glance) · [try it](#try-it) ·
[sources](#sources) · [how the network is built](#how-the-network-is-built) ·
[validation](#validation-against-the-official-kgm-distances) ·
[coverage](#charging-coverage-osm-against-epdk) ·
[station sets](#three-station-sets-osm-synthetic-hybrid) ·
[prices and the 2026 regulation](#prices-and-the-march-2026-regulation) ·
[queues](#queues-assumptions) · [results](#results-the-real-data-experiment) ·
[limitations](#limitations) · [rebuilding](#rebuilding-the-data) ·
[licences](#licences)

## At a glance

| | where it comes from | real or assumed |
|---|---|---|
| roads | OSM extract of 2026-10-27: motorway, trunk, primary and their ramps, plus 263 km of secondary roads that close gaps | real |
| speeds | the OSM `maxspeed` tag, otherwise the legal default of the Karayolları Trafik Yönetmeliği | legal limits, free flow, no traffic |
| elevations | SRTM 1 arc-second at every node | measured (the survey is from 2000) |
| charging stations | 711 public OSM stations (618 AC, 93 DC) | real locations; 64 % of the powers and 44 % of the plug counts are defaults |
| prices | list prices on the operators' own pages, 2026-10-27 (10 operators, 237 of the 711 stations) | real, a dated snapshot; defaults for the other stations |
| queues | Erlang-C with national averages from the EPDK statistics | **assumed** |
| tariff | neutral: one price per kWh at each station | consistent with the 2026 regulation |
| vehicle | the `togg_t10x_lr` preset by default | approximate public figures |

The network has 44 673 nodes and 70 886 directed edges (strongly connected)
and covers 75 393 km of road: 6 511 km of motorway, 45 883 km of trunk
roads, 18 261 km of primary roads, 4 472 km of ramps, 263 km of secondary
roads and 3 km of their ramps.  Elevations range from −3 m to 3 076 m.
Nodes use the same map projection as the approximate network, so both share
one map frame.  The loader is
[`evroute.generators.osm_turkey`](../src/evroute/generators/osm_turkey.py);
the files, their columns and their provenance are described in
[`data/osm_turkey/README.md`](../src/evroute/data/osm_turkey/README.md).

## Try it

The bundled data needs no extra package: the network loads in about half a
second and is cached for the process.

```bash
evroute scenarios                                            # the turkey-osm-* presets are listed with the others
evroute plan turkey-osm-karaman-istanbul                     # OSM stations, BLS-A*: about 5 s
evroute plan turkey-osm-karaman-istanbul --stations hybrid   # + synthetic DC gap-fillers
evroute viz turkey-osm-karaman-istanbul --algorithms bls-astar,cfp-astar -o osm.html --lang en
```

```text
$ evroute plan turkey-osm-karaman-istanbul
[bls-astar] 9 h 14 min | 751.81 km | 2 stop(s) | arrive 10.0% | 999.92 TRY
#  station                  charger    arrive  queue  charge  SoC         cost
-  -----------------------  ---------  ------  -----  ------  ----------  ----------
1  ZES - Afyon Kolaylı DCv  DC 120 kW   12:13  0 min   5 min  14% -> 26%  170.54 TRY
2  Kütahya Supercharger     DC 250 kW   13:12  0 min  27 min  10% -> 75%  829.37 TRY
arrival 17:14 | drive 8 h 37 min | charge 31 min | queue+setup 6 min | battery wear 0.0173 %SoH
```

Station names are the OSM `name` tags as mapped.  The same trip on the
approximate network takes 8 h 07 min, 7 h 19 min of it driving
([README](../README.md#example-karaman--i̇stanbul)); on the OSM roads the
driving alone takes 8 h 37 min.

| preset | trip | stations |
|---|---|---|
| `turkey-osm-karaman-istanbul` | the flagship trip on OSM roads, leaving at 08:00 with 80 % | OSM |
| `turkey-osm-karaman-istanbul-dc85` | the same, charging capped at 85 % (operators may end DC sessions there) | OSM |
| `turkey-osm-antalya-ankara` | from the Mediterranean over the Taurus | OSM |
| `turkey-osm-istanbul-ankara` | the busiest corridor, leaving at 14:30 | OSM |
| `turkey-osm-ankara-adana` | over the Gülek pass, leaving at 15:00 | OSM |
| `turkey-osm-karaman-mersin` | the Sertavul pass or the motorway via Pozantı | OSM |
| `turkey-osm-izmir-erzurum` | the long west-east crossing | hybrid |
| `turkey-osm-karaman-konya` | Karaman → Konya (119 km direct) at 30 % charge: no DC charger on the way, so the optimum detours to an AC charger (239 km, about 5 h); small enough for every planner | OSM |

Each preset except `turkey-osm-karaman-konya` has the origin, destination,
vehicle, charge, departure and temperature of its counterpart on the
approximate network.
`--stations osm|synthetic|hybrid` chooses the
[station set](#three-station-sets-osm-synthetic-hybrid) of any of them, and
`--from` / `--to` accept the name of any of the 1 005 OSM places (case and
Turkish letters do not matter).  On a network of more than 20 000 nodes,
`--algorithms all` (`compare`, `viz`, `figures`) leaves out `bls`, `cfp`
and `pareto`, which settle millions of labels there without the A\*
potential, and `station-graph`, which takes minutes to hours there; name
them to run them anyway.  The live server ticks only BLS-A\* and CFP-A\*
at first on these presets.

In Python:

```python
from evroute import plan
from evroute.generators.osm_turkey import osm_turkey_scenario

sc = osm_turkey_scenario("Karaman", "İstanbul")                      # OSM stations (default)
print(plan(sc, "bls-astar"))
print(plan(sc, "cfp-astar"))                                         # continuous charging: about half a minute
hybrid = osm_turkey_scenario("izmir", "ERZURUM", stations="hybrid")  # names: any case, Turkish letters optional
print(plan(hybrid, "bls-astar"))                                     # 80 % at 08:00 (the preset leaves at 06:00 with 90 %)
print(sc.meta["station_info"]["0"])                                  # where the data of station 0 come from
```

```text
[bls-astar] 9 h 14 min | 751.81 km | 2 stop(s) | arrive 10.0% | 999.92 TRY
[cfp-astar] 9 h 14 min | 755.01 km | 2 stop(s) | arrive 10.0% | 1021.20 TRY
[bls-astar] 17 h 44 min | 1465.62 km | 6 stop(s) | arrive 10.5% | 3233.54 TRY
{'osm': 'node/3885275177', 'price': 'default', 'power': 'default:ac', 'plugs': 'default', 'snap_m': 35.2}
```

`get_scenario("turkey-osm-karaman-istanbul", stations="hybrid")` gives the
presets with any station set.  `osm_turkey_meta()`, `osm_turkey_prices()`
and `osm_turkey_validation()` return `meta.json`, `prices.json` and
`validation.csv`; every scenario carries the credit in
`scenario.meta["attribution"]`, and the viewer, the static map and
search-space figures, the results dashboard and the LaTeX caption of the
experiment print it.

## Sources

| source | version | used for |
|---|---|---|
| OpenStreetMap extract of Turkey, [download.openstreetmap.fr](https://download.openstreetmap.fr/extracts/europe/turkey.osm.pbf) | replication timestamp 2026-10-27T02:09:23Z (sequence 7346288), md5 `b03e7851f3312d6f5b552985c10e31db` | roads, charging stations, places |
| SRTM 1 arc-second, "skadi" tiles of the AWS Terrain Tiles | 111 tiles | elevations |
| KGM (Karayolları Genel Müdürlüğü), *İller arası mesafeler* ([`ilmesafe.xlsx`](https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Root/Uzakliklar/ilmesafe.xlsx)) | table of 2026-03-03 | validation only |
| EPDK, *Şarj Hizmeti Piyasası Aylık İstatistikleri* ([statistics page](https://www.epdk.gov.tr/Detay/Icerik/3-0-222/enerji-donusumusarj-hizmeti-piyasasi--istatistik)) | September 2026, published 16.10.2026 | coverage, queue assumptions |
| the operators' own price pages | accessed 2026-10-27 | `prices.json` |
| Resmî Gazete 23.03.2026, No. 33202: amendment of the Şarj Hizmeti Yönetmeliği ([text](https://www.resmigazete.gov.tr/eskiler/2026/03/20260323-4.htm)) | in force since March 2026 | price rules |
| Karayolları Trafik Yönetmeliği, Art. 100 | | default speeds |

## How the network is built

`tools/build_osm_turkey.py` turns the sources into the bundled tables in
these steps.

**Roads.**  Every OSM way tagged `highway=motorway`, `trunk` or `primary`,
or one of their `*_link` ramps, inside the national boundary of Turkey.
Ways closed to cars (`access=no`, `private`), areas and reversible lanes are
dropped; one-way follows the `oneway` tag (motorways and roundabouts are
one-way by OSM convention).  In OSM Turkey the state roads (D-roads) are
mapped as trunk or primary, and `secondary` mostly means provincial roads,
so secondary roads enter only where the main network needs them: 41.7 km
reconnect 10 stranded fragments of it, and 221.8 km close gaps that cut off
24 pairs of province centres or forced detours of more than 15 % between
them.  Lengths are geodesic lengths on the WGS84 ellipsoid.

**Speeds.**  The `maxspeed` tag where present.  It is missing on 5 % of
the motorway, 59 % of the trunk and 70 % of the primary kilometres; there
the build uses the legal default for cars (Karayolları Trafik Yönetmeliği,
Art. 100): 50 km/h in built-up areas, 90 km/h on rural single
carriageways, 110 km/h on dual carriageways and 120 km/h on motorways.
Ramps take the length-weighted median of the tagged ramps of their class
(50 km/h at motorway and trunk ramps, 30 km/h at primary ones), roundabouts
30 km/h.  OSM does not say where a town begins, so a road counts as
**built-up** when at least 5 junctions with minor streets (residential,
unclassified, tertiary, living street) lie within 500 m; the threshold was
calibrated on the tagged roads.  On the roads that do have a tag, the
default rule misses the tagged limit by 12-14 km/h on average; on
motorways the default is 11.6 km/h below the tagged limits on average.
All speeds are free-flow speeds: there is no traffic in the data.

**Simplification that keeps the terrain.**  Chains of OSM nodes without
junctions are contracted into single edges, but split at every local
elevation extremum with at least 20 m of relief on both sides and so that
no edge is longer than 5 km: passes and valleys stay nodes, so the energy
model sees every climb and descent.  The speed of a contracted edge is the
length-weighted harmonic mean of its pieces, which keeps the free-flow
travel time exactly.  Then the largest strongly connected component is kept
(72 457 nodes; 6 940 nodes in small fragments are dropped), the end nodes
of edges shorter than 30 m are merged (junction consolidation, 53 577
nodes), and former junctions that became plain pass-through points are
contracted again under the same terrain rules: 44 673 nodes, 70 886 edges,
from 25 m to 5 km long (median 518 m).  Every edge is at least as long as
the straight 3-D line between its end nodes, from which EVRoute derives
grades; shorter edges were lengthened to it.

**Elevations.**  A bilinear SRTM sample at every node.  Inside bridges and
tunnels the elevation is interpolated between the portals.  Grades above
15 % (SRTM artefacts at junctions on viaducts, and roads built after the
survey, such as the relocation roads of the Yusufeli dam) are clipped by
moving the deviating node: 911 nodes, median shift 5.1 m, largest 165 m.

**Charging stations.**  The extract has 773 objects tagged
`amenity=charging_station`.  26 lie outside Turkey, 14 are private
(`access=private` or `no`, `motorcar=no`, disused) and 22 are more than 3 km
from the network; the other **711** are snapped to their nearest network
node (median distance 400 m).  A station is DC if it has a DC socket type
(CCS, CHAdeMO, Tesla Supercharger, ...), a tagged station power above
22 kW or a name that says so ("Supercharger", "DC", "hızlı", ...),
otherwise AC.  Its power is the largest tagged output of its kind, else
voltage × amperage, else a **default**: 22 kW AC, 120 kW DC, 250 kW at a
Tesla Supercharger (452 stations, 64 %).  Its plug count is the
`capacity` tag, else the sum of the socket counts, else **1** (316
stations, 44 %), so plug counts are lower bounds.  Each station keeps its
OSM object id and the source of every value.

**Places.**  1 005 cities and towns of OSM, among them the 81 province
centres (their OSM admin centres), snapped to the nearest network node
(province centres within 2.24 km).  They label the map and resolve
`--from`/`--to` names; province centres come first, so `"Ereğli"` is the
larger Ereğli (Konya).

## Validation against the official KGM distances

The KGM table of 2026-03-03 gives the road distance between every two of the
81 province centres.  On the network the distance of a pair is the shortest
road distance, averaged over both directions, plus the snapping distances of
the two centres.  Over all **3 240 pairs**:

| measure | value |
|---|---:|
| mean absolute error | 2.52 % |
| median absolute error | 1.45 % |
| 90th percentile | 5.12 % |
| pairs within 5 % / within 10 % | 89.5 % / 97.2 % |
| mean signed error | −0.82 % |
| avoiding toll roads (2 932 pairs): mean absolute / signed error | 2.23 % / −0.17 % |

The KGM table follows toll-free roads: avoiding them almost removes the
bias of the network towards shorter distances.  `validation.csv` lists 20
pairs, among them the six trips of the experiment:

| pair | KGM [km] | network A → B / B → A [km] | error | without toll roads |
|---|---:|---:|---:|---:|
| Karaman - İstanbul | 789 | 731.9 / 730.0 | −7.4 % | – |
| Antalya - Ankara | 542 | 509.2 / 509.4 | −6.0 % | −6.0 % |
| İzmir - Erzurum | 1 471 | 1 451.0 / 1 451.7 | −1.3 % | −1.3 % |
| İstanbul - Ankara | 453 | 438.7 / 438.1 | −3.2 % | – |
| Ankara - Adana | 491 | 472.7 / 474.1 | −3.6 % | −3.5 % |
| Karaman - Mersin | 233 | 237.1 / 234.8 | +1.2 % | +1.2 % |
| Karaman - Konya | 118 | 120.8 / 113.5 | −0.7 % | −0.7 % |
| İstanbul - İzmir | 566 | 456.1 / 461.9 | −18.9 % | – |
| Ankara - Samsun | 412 | 403.6 / 407.3 | −1.6 % | −1.6 % |
| Trabzon - Erzurum | 294 | 284.3 / 283.7 | −3.4 % | −3.4 % |
| Gaziantep - Şanlıurfa | 139 | 145.4 / 145.2 | +4.5 % | +4.5 % |
| Diyarbakır - Van | 363 | 368.0 / 368.4 | +1.4 % | +1.4 % |
| Kayseri - Sivas | 202 | 196.7 / 196.3 | −2.7 % | −2.7 % |
| Edirne - İstanbul | 232 | 237.8 / 236.0 | +2.1 % | +2.1 % |
| Bursa - Eskişehir | 152 | 150.4 / 150.6 | −1.0 % | −1.0 % |
| Konya - Antalya | 271 | 268.2 / 273.0 | −0.2 % | −0.2 % |
| Denizli - Muğla | 150 | 149.9 / 144.9 | −1.7 % | −1.7 % |
| Erzurum - Kars | 205 | 209.7 / 215.6 | +3.7 % | +3.7 % |
| Sinop - Kastamonu | 181 | 182.8 / 182.7 | +1.0 % | +1.0 % |
| Hakkâri - Van | 195 | 177.6 / 195.0 | −4.5 % | −4.5 % |

"–": the network has no toll-free crossing of the Bosphorus, so the
İstanbul centre cannot reach Anatolia without a toll road.

**The outliers** come from the map, not from the method:

- **İstanbul - İzmir, −18.9 %.**  The shortest route crosses the Gulf of
  İzmit on the toll Osmangazi bridge, which the KGM table avoids.
- **Tunceli** (Elâzığ - Tunceli 294.9 km against 140 km, +110.7 %) is
  reached only over provincial roads, which OSM maps as secondary and the
  network mostly leaves out.
- **Yalova** (Kocaeli - Yalova +104.3 %): the road along the southern shore
  of the gulf is not a main road in OSM.
- **Hakkâri** (Hakkâri - Şırnak +81.2 %): the northbound carriageway of the
  D-975 near Başkale is missing in OSM, so routes leaving Hakkâri to the
  north take provincial roads over a 3 076 m pass, the highest node of the
  network.  Van → Hakkâri, 195.0 km, matches the KGM table.

These are gaps in OSM; the build reports them (`validation` in
`meta.json`) and does not patch them by hand.

## Charging coverage: OSM against EPDK

EPDK counted **48 971 sockets** with a commercial charging service in
September 2026 (27 664 AC, 21 307 DC; 3 875 MW).  The 711 OSM stations have
909 AC and 405 DC plugs, so **OSM holds about 3.3 % of the AC and 1.9 % of
the DC sockets**.  Both shares are rough: OSM plug counts are lower bounds,
and the EPDK count also includes sockets with restricted access, while the
OSM set keeps public stations only.  There is **no DC
station east of 38° E** - the easternmost one is at 36.4° E, and only 3 lie
east of 34° E - while 47 AC stations are mapped east of 38° E.

The most-mapped operator is Sharz.net (129 stations, mostly AC at hotels
and businesses), then ZES (91) and Trugo (84); 108 stations have no
operator tag.  EPDK publishes no station coordinates as open data (its
Şarj@TR app shows the stations, its licence register only addresses), so
OSM is the open source with locations.  The coverage is the main limit of
this case study: with OSM stations alone, long eastern trips have no fast
charger to use.

## Three station sets: osm, synthetic, hybrid

`stations=` (`--stations` on the command line) chooses the chargers:

- **`osm`** (default): the 711 public OSM stations above, priced with the
  operators' list prices ([below](#prices-and-the-march-2026-regulation)),
  with the queue assumptions of [Queues](#queues-assumptions).
- **`synthetic`**: the 290 synthetic stations of the approximate case
  study (same seed, powers, plugs, prices and calibrated queues), each moved
  to the nearest node of the OSM network whose road class matches its road
  in the approximate network, or to the nearest main-road node if that is
  more than 5 km closer (a modelled road can lie far from the OSM road of
  its class); a city station goes to the OSM place of the same name.  Same
  stations, real roads: the difference to the approximate case study is the
  network alone.
- **`hybrid`**: the OSM stations plus **synthetic DC gap-fillers** wherever
  a main road (motorway, trunk or primary) is more than 75 km of road from
  every DC station.  Farthest gap first, each filler goes to a named place
  within 20 km of the farthest uncovered node (or to that node), until every
  main-road node is covered: 373 fillers with seed 0, so 1 084 stations,
  466 of them DC.  A filler has 120 or 150 kW and 2 or 4 plugs (seeded) and
  the default DC price.  Fillers are not real stations: they are flagged by
  `operator="synthetic"` and a name starting with "Synthetic DC
  gap-filler".  The hybrid set answers "what if the DC network had no
  gaps?", not "what is there today".

`scenario.meta["station_info"]` records for every station where its data
come from: the OSM object, the source of its price (`operator` or
`default`), power and plugs, or `synthetic` / `gap-filler`.

## Prices and the March 2026 regulation

`prices.json` is a hand-curated snapshot of 2026-10-27: the public list
prices of Turkish operators in TRY/kWh, VAT included, each with the page it
was read from.  A station gets its operator's **list price** only if the
operator publishes it on its own page; where the operator gives a band, the
upper end, since under the 2026 rules lower prices are location or
time-of-day discounts:

| operator | AC [TRY/kWh] | DC [TRY/kWh] |
|---|---:|---:|
| Trugo | 9.95 | 12.00-14.98 |
| ZES | 10.99 | 12.99-16.49 |
| WAT Mobilite | 9.99-10.99 | 11.99-14.49 |
| Astor Şarj | 9.89 | 13.99 |
| En Yakıt | - (DC only) | 14.90 |
| Oncharge | 9.99 | 13.50 |
| Neva Şarj | 9.90 | 13.90 |
| Efish | 10.99 | 15.99 |
| Aksa Şarj | 9.90-10.90 | 11.99-13.99 |
| Beefull | 9.90 | 13.99 |

Every other station - no operator tag, or an operator whose page could not
be read (press or aggregator figures are recorded in `prices.json` but not
used) - gets the **default**: **9.99 TRY/kWh AC and 14.24 TRY/kWh DC**, the
medians of the confirmed list prices.  237 of the 711 OSM stations have an
operator price, 474 the default.  Idle fees, loyalty prices and campaigns
are not modelled.

The **amendment of the Şarj Hizmeti Yönetmeliği** (Resmî Gazete
23.03.2026, No. 33202) sets the rules these prices follow:

| rule | in EVRoute |
|---|---|
| prices per kWh delivered; no connection, start or equipment fee | a price per kWh; the set-up time of a stop costs time, not money |
| at public stations an operator has **one AC and one DC price**: no tiers by charger power or state of charge | one price per station; the 80 % tier of `DEMO_TARIFF` is an illustrative option only, and the `turkey-osm-*` presets keep the neutral `Tariff()` |
| discounts by location and **time of day** are allowed; the price of the hour in which the session **starts** applies | the join-time rule of a `Tariff` with `peak_hours` ([`model.md` §5](model.md#5-charging-tariffs)); EVRoute fixes the price when the car joins the queue, which is the start of the session when there is no queue |
| the public price is at most 1.25 × the lowest price for loyalty customers | not modelled |
| an operator may end a DC session at **85 % SoC** (with prior notice) | `max_charge_soc=0.85` (the `-dc85` preset; it caps AC stops too, a simplification) |

On Karaman → İstanbul the 85 % cap changes nothing: the BLS-A\* plan of
`turkey-osm-karaman-istanbul-dc85` is the one above, which charges to 75 %
at most.

## Queues (assumptions)

Every OSM station and gap-filler is an Erlang-C queue
([`model.md` §6](model.md#6-queues)) with national averages derived from the
EPDK statistics of September 2026 (`QUEUE_MODEL`, `DEMAND_PROFILE`):

| | DC | AC |
|---|---:|---:|
| sessions per plug and day | 3.7 | 1.0 |
| mean session | 36 min | 159 min |
| set-up (parking, plugging in, payment) | 180 s | 120 s |

The demand over the day follows the hourly profile of the charged energy
(peak 17:00-18:00).  These are **assumptions, not measurements**: real
queues differ from site to site, and the national averages are much lighter
than the calibrated afternoon queues of the busiest service areas in the
approximate case study (which the `synthetic` set keeps).  Queue and set-up
time of the exact optima are 0-6 min per trip with OSM stations, against
5.8-24.6 min on the approximate network.

## Results: the real-data experiment

`evroute experiments real-data` plans six trips on three data sets: the
approximate network with its synthetic stations, the OSM network with the
OSM stations, and the OSM network with the hybrid set.  The exact planners
are BLS-A\* and CFP-A\*; the heuristics greedy, station graph and genetic
run only where an exact route exists, and their gap is measured against
CFP-A\* on the same data set.  The full run (87 plans) was made on a 4-core
2.1 GHz Xeon in four worker processes in parallel, so its runtimes include
that load.

The continuous optimum (CFP-A\*) of every trip:

| trip | data set | trip time | distance | stops | charging | cost [TRY] | BLS-A\* | CFP-A\* |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Karaman → İstanbul | approximate | 8 h 07 min | 747 km | 2 | 35.3 min | 1,133.96 | 0.06 s | 0.04 s |
| | OSM | 9 h 14 min | 755 km | 2 | 32.7 min | 1,021.20 | 7.00 s | 31.22 s |
| | hybrid | 8 h 41 min | 818 km | 3 | 56.9 min | 1,634.51 | 1.72 s | 3.19 s |
| Antalya → Ankara | approximate | 5 h 52 min | 526 km | 1 | 24.8 min | 667.84 | 0.06 s | 0.03 s |
| | OSM | 6 h 18 min | 543 km | 1 | 16.3 min | 561.45 | 1.79 s | 3.07 s |
| | hybrid | 6 h 07 min | 533 km | 2 | 21.3 min | 691.33 | 0.79 s | 0.87 s |
| İstanbul → Ankara | approximate | 4 h 54 min | 457 km | 2 | 26.2 min | 837.92 | 0.06 s | 0.02 s |
| | OSM | 4 h 35 min | 449 km | 2 | 30.0 min | 1,028.02 | 2.64 s | 2.99 s |
| | hybrid | 4 h 35 min | 449 km | 2 | 30.0 min | 1,028.02 | 1.90 s | 3.03 s |
| Ankara → Adana | approximate | 5 h 07 min | 508 km | 2 | 27.1 min | 759.56 | 0.06 s | 0.02 s |
| | OSM | 5 h 58 min | 569 km | 2 | 39.4 min | 1,111.24 | 5.33 s | 16.69 s |
| | hybrid | 4 h 32 min | 493 km | 2 | 30.0 min | 1,056.19 | 0.82 s | 0.99 s |
| Karaman → Mersin | approximate | 3 h 03 min | 273 km | 1 | 2.9 min | 93.27 | 0.05 s | 0.02 s |
| | OSM | 3 h 09 min | 236 km | 0 | - | 0 | 0.81 s | 0.62 s |
| | hybrid | 2 h 58 min | 244 km | 1 | 2.5 min | 88.47 | 0.82 s | 0.78 s |
| İzmir → Erzurum | approximate | 17 h 29 min | 1 467 km | 4 | 103.8 min | 3,239.96 | 0.12 s | 0.10 s |
| | OSM | not solved | | | | | 58.66 s | 160.19 s |
| | hybrid | 17 h 50 min | 1 466 km | 6 | 103.0 min | 3,329.31 | 10.20 s | 24.83 s |

What the numbers say:

- **The exact planners find a route for 5 of 6 trips with OSM stations and
  6 of 6 with the hybrid set.**  İzmir → Erzurum with OSM stations stopped
  at the label limit (BLS-A\* after 4.59 million labels, CFP-A\* after 1.81
  million): *not solved*, not proven infeasible.  There is no DC charger on
  the eastern half of the route, and a feasible route does exist: greedy
  (`evroute plan turkey-osm-izmir-erzurum --stations osm -a greedy`, about
  3 minutes) finds one of 46 h 34 min with 15 stops, 24 h 25 min of it
  charging at AC stations.
- **With OSM stations the optimum takes 7.0 % longer on average for 0.1 %
  more distance.**  Nearly all of it is slower driving (+32 min on average;
  charging, queues and set-up −5.7 min): the OSM speeds are legal limits.
  The money is 11 % lower on average, but the spread is wide: Karaman →
  Mersin needs no stop at all on the real roads (236 km instead of 273 km),
  while Ankara → Adana costs 46 % more than on the approximate network and
  drives 76 km more than with the hybrid set to reach DC chargers - the
  gap-fillers remove that detour.
- **With the hybrid set** the optimum is 1.2 % faster than on the
  approximate network and 18 % dearer on average.
- **BLS-A\* stays within 0.4 % of the continuous optimum** on every trip
  and data set.
- **Runtimes grow, but stay in seconds.**  The median over the trips is
  4.0 s for BLS-A\* and 9.9 s for CFP-A\* with OSM stations (hybrid: 1.3 s
  and 2.0 s), against 60 ms and 24 ms on the 1 279-node approximate
  network.  Run alone on the same machine, Karaman → İstanbul takes 3.8 s
  (BLS-A\*) and 23.3 s (CFP-A\*).
- **The heuristics fall apart on real data.**  Mean gap to the optimum on
  the approximate network → with OSM stations → with the hybrid set:

  | heuristic | approximate | OSM | hybrid |
  |---|---:|---:|---:|
  | greedy | 6.4 % | 153.0 % | 103.5 % |
  | station graph | 14.9 % | 11.9 % | 14.9 % |
  | genetic | 0.2 % | 9.3 % | 0.9 % |

  Greedy, which charges to 100 % at AC stations, sits for hours at slow AC
  chargers on OSM data (Karaman → İstanbul: 15.6 h of charging, a 24 h
  18 min trip).  The station graph grows one label tree over the whole
  network (30-50 s each) for every station it expands; it ran longer than
  10 minutes in 4 of its 11 runs on OSM data: Ankara → Adana (OSM) 33 min,
  İstanbul → Ankara 54 min (OSM) and 57 min (hybrid), İzmir → Erzurum
  (hybrid) 3 h 46 min.  Greedy took at most 2.2 min, genetic 2.7 min.

**Time budget.**  The full run took 6.8 hours of planning, 6.4 of them in
the station graph.  The experiment therefore stops every heuristic run on
the OSM data sets after 10 minutes (`REAL_HEURISTIC_BUDGET_S`); a stopped
run is recorded with the status "not run: over the time budget" and its
runtime as a lower bound.  The budget came after the run above: its four
runs over 10 minutes completed and are kept, flagged by their runtime in
the dashboard.  With the budget, a full run should take about 1.5 hours on
one core (an estimate).

```bash
evroute experiments real-data -o results/ --lang en    # full; the dashboard results/results.html has a real-data section
evroute experiments real-data --quick -o results/      # Karaman → Mersin, exact planners + genetic: about 7 s
```

The experiment writes `real-data/results.csv` (one row per plan, with the
data set, the network size, the number of stations and `gap_pct`),
`summary.json`, `table.tex` and, in a full run, the viewer and figures of
Karaman → İstanbul on OSM data (`karaman-istanbul/`).

## Limitations

- **Charging coverage.**  OSM holds about 3.3 % of the AC and 1.9 % of the
  DC sockets and no DC charger east of 38° E; the optima with OSM stations
  are optima over the mapped chargers, not over the real network.  The
  hybrid gap-fillers are synthetic.
- **Station data.**  64 % of the powers and 44 % of the plug counts are
  defaults; plug counts are lower bounds.  Availability, opening hours and
  broken chargers are not modelled, and stations for one brand or for
  customers only (e.g. "Sadece Renault", hotel chargers) are kept, since
  OSM rarely tags the restriction.
- **Speeds.**  Legal limits or imputed defaults, free flow, no traffic or
  congestion.  1 633 edges are faster than 130 km/h (almost all on
  motorways, where signs may allow 140 km/h), 539 edges (285 km) faster
  than 140 km/h, and 6 very short edges (48-105 m) faster than 150 km/h, up
  to 202.6 km/h; these 6 were stretched to their straight-line length with
  their travel time kept.  Treat speeds above 140 km/h, the highest legal
  limit, with caution.
- **Roads.**  Tertiary roads and city streets are not in the network, and
  provincial roads only where they close a gap, so trips start and end at
  the network node of a place (province centres within 2.24 km), and a few
  regions are reached over detours (Tunceli, Yalova, Hakkâri).  The
  network uses toll roads; EVRoute does not price tolls.
- **Elevations.**  SRTM dates from 2000 and has a 30 m grid; roads built
  since then (dam relocations) were smoothed by the 15 % grade clip.
- **Prices** are a dated snapshot and change often; a third of the OSM
  stations has an operator-confirmed price, the rest the default.
- **Queues** are assumptions from national averages, the same at every
  station of a kind.
- **Vehicle.**  The presets use approximate public figures.

## Rebuilding the data

The files are built by
[`tools/build_osm_turkey.py`](../tools/build_osm_turkey.py) in the
repository (not part of the installed package).  It downloads the OSM
extract (about 700 MB), the SRTM tiles and the KGM table itself; a full run
takes about 10 minutes and 1 GB of disk, and later runs reuse the cached
stages of its work directory:

```bash
pip install osmium numpy scipy shapely openpyxl
python -I tools/build_osm_turkey.py --workdir /tmp/osm-work --outdir src/evroute/data/osm_turkey
```

It rewrites `nodes`, `edges`, `stations`, `places`, `validation.csv` and
`meta.json`; `prices.json`, `DATA_LICENSE.md` and the data README are
maintained by hand.  A newer extract changes the network, so the numbers on
this page (and the tests that check them against `meta.json`) follow the
new build.  `meta.json` records every parameter and statistic of the build:
the OSM timestamp and checksum, the speed rules and their check against the
tagged roads, what the simplification removed, the grade clip, the station
rules, and the validation.

## Licences

| what | licence |
|---|---|
| the EVRoute code, the loader and the build script | MIT |
| `nodes`, `edges`, `stations`, `places`, `validation.csv`, `meta.json` | **ODbL 1.0**, © OpenStreetMap contributors (a derivative database of OpenStreetMap) |
| node elevations | SRTM, NASA/USGS, public domain |
| `prices.json` | facts with source and date; no licence claimed; brand names belong to their owners |
| KGM distances, EPDK statistics | cited facts; the documents themselves are not included |

**Attribution.**  Every map, figure, route plan or table made from the
data must carry the credit "© OpenStreetMap contributors"; the viewer, the
static map and search-space figures, the results dashboard and the LaTeX
caption of the experiment do it automatically.  **Share-alike:** an adapted
version of the database (for example with corrected roads or added
stations) that you publish must be offered under the ODbL as well; a
produced work, such as a figure or a route, may be published under any
terms as long as it carries the credit.  Details:
[`DATA_LICENSE.md`](../src/evroute/data/osm_turkey/DATA_LICENSE.md) and
[openstreetmap.org/copyright](https://www.openstreetmap.org/copyright).
