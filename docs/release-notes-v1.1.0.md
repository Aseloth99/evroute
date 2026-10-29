# EVRoute v1.1.0

**English** · [Türkçe](#türkçe)

A release about real data: the trips of the Turkey case study now also run
on the OpenStreetMap road network of Turkey, with the charging stations
mapped in OSM and the prices the operators publish.

- **Real-data case study, bundled** (1.5 MB, no extra package): 44 673
  OpenStreetMap nodes and 75 393 km of motorway, trunk and primary roads
  with OSM speed limits or the legal defaults, SRTM elevations at every
  node, the 711 public OSM charging stations (618 AC, 93 DC) and the list
  prices of ten operators.  Road distances between the 81 province centres
  deviate from the official KGM table by 2.52 % on average.  Eight
  `turkey-osm-*` presets, three station sets (`osm`, `synthetic`, `hybrid`
  with flagged synthetic DC gap-fillers) and `osm_turkey_scenario()` for
  any two of 1 005 places.  Write-up in English and Turkish:
  `docs/real-data.md`, `docs/real-data.tr.md`.
- **A twelfth experiment, `real-data`**: six trips on the approximate
  network and on OSM data.  The exact planners stay exact and answer in
  seconds on 44 673 nodes; with OSM stations the optimum takes 7.0 % longer
  on average, almost all of it slower driving; the heuristics lose their
  footing.
- **Data licence**: the bundled network is ODbL 1.0, © OpenStreetMap
  contributors (the code stays MIT); every map, figure, dashboard and LaTeX
  caption made from it carries the credit.
- **Sturdier input checks**: values outside the model (a negative value of
  time or set-up time, a zero-length edge, speed factors outside (0, 1],
  temperatures below absolute zero, a charging grid finer than 0.1 %) are
  now rejected with one clear error instead of hanging or crashing a
  planner, or voiding its optimality.
- **Heuristics**: greedy and the station graph can charge at a station at
  the destination to meet the arrival requirement.  On networks of more than
  20 000 nodes the live server plans BLS-A\* and CFP-A\* by default, and
  `--algorithms all` also leaves out the station graph.
- **Requirements** section in the READMEs and a `requirements.txt` that
  states there is no runtime dependency.

The planners give the same results as in v1.0.0 on every earlier preset.

Install: `pip install git+https://github.com/Aseloth99/evroute.git`
(or clone and `pip install -e .`), then
`evroute plan turkey-osm-karaman-istanbul`.

---

## Türkçe

Gerçek veri üzerine bir sürüm: Türkiye vaka çalışmasının yolculukları artık
Türkiye'nin OpenStreetMap yol ağında, OSM'de işaretlenmiş şarj
istasyonları ve operatörlerin yayımladığı fiyatlarla da çalışıyor.

- **Paketle gelen gerçek veri vaka çalışması** (1,5 MB, ek paket
  gerekmez): 44 673 OpenStreetMap düğümü ve OSM hız sınırları ya da yasal
  varsayılanlarla 75 393 km otoyol, ana yol ve birincil yol; her düğümde
  SRTM yükseklikleri; OSM'deki 711 halka açık şarj istasyonu (618 AC, 93
  DC) ve on operatörün liste fiyatları. 81 il merkezi arasındaki yol
  mesafeleri KGM'nin resmî tablosundan ortalama %2,52 sapar. Sekiz
  `turkey-osm-*` hazır senaryosu, üç istasyon kümesi (`osm`, `synthetic`
  ve işaretli sentetik DC boşluk doldurucularıyla `hybrid`) ve 1 005
  yerleşimden herhangi ikisi için `osm_turkey_scenario()`. İngilizce ve
  Türkçe anlatım: `docs/real-data.md`, `docs/real-data.tr.md`.
- **On ikinci deney, `real-data`**: yaklaşık ağda ve OSM verisinde altı
  yolculuk. Kesin planlayıcılar 44 673 düğümde de kesin kalır ve saniyeler
  içinde yanıt verir; OSM istasyonlarıyla optimum ortalama %7,0 daha uzun
  sürer, neredeyse tamamı daha yavaş sürüşten; sezgiseller ise dengesini
  kaybeder.
- **Veri lisansı**: paketle gelen ağ ODbL 1.0 lisanslıdır, ©
  OpenStreetMap contributors (kod MIT olarak kalır); ondan üretilen her
  harita, grafik, panel ve LaTeX başlığı bu atfı taşır.
- **Daha sağlam girdi denetimi**: modelin dışındaki değerler (negatif zaman
  değeri ya da hazırlık süresi, sıfır uzunluklu kenar, (0, 1] dışında hız
  çarpanları, mutlak sıfırın altında sıcaklık, %0,1'den ince şarj ızgarası)
  artık bir planlayıcıyı kilitlemek, çökertmek ya da optimalliğini bozmak
  yerine tek ve açık bir hatayla reddedilir.
- **Sezgiseller**: greedy ve station graph, varış gereksinimini karşılamak
  için varış noktasındaki bir istasyonda şarj edebilir. 20 000'den fazla
  düğümlü ağlarda canlı sunucu varsayılan olarak BLS-A\* ve CFP-A\* ile
  planlar; `--algorithms all` station graph'ı da dışarıda bırakır.
- README'lerde **Gereksinimler** bölümü ve çalışma zamanı bağımlılığı
  olmadığını belirten bir `requirements.txt`.

Planlayıcılar daha önceki her hazır senaryoda v1.0.0 ile aynı sonuçları verir.

Kurulum: `pip install git+https://github.com/Aseloth99/evroute.git`
(ya da klonlayıp `pip install -e .`), ardından
`evroute plan turkey-osm-karaman-istanbul`.
