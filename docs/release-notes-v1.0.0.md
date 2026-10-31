# EVRoute v1.0.0

**English** · [Türkçe](#türkçe)

The first public release of EVRoute: exact, battery-, queue- and
degradation-aware route planning for electric vehicles with charging
stations, in pure Python with zero runtime dependencies, with a web app that
plans in the browser.

- **9 planners behind one API**: a battery-blind fastest path, a greedy
  "in-car navigator", a station graph and a genetic algorithm as baselines;
  exact label setting (BLS, BLS-A\*), exact continuous charging (CFP, CFP-A\*)
  and a Pareto search over time, money and battery wear.  BLS-A\* is the
  default and the everyday choice: exact on its charging grid, 0.11 % from
  the continuous optimum on average, about 4 s on the 44 673-node
  OpenStreetMap network of Turkey (`docs/guide.md` §12).
- **One exact model** for nonlinear and partial charging, time-dependent
  Erlang-C queues, terrain and regeneration, temperature, battery wear, eco
  speeds and **charging tariffs** (a higher price above 80 % SoC, peak hours
  fixed or derived from how busy a station is; cost-optimal plans may wait
  for the cheaper period, and dominance stays exact).
- **No "+2 %" top-ups**: an optional minimum stop SoC (`min_stop_soc`,
  `--min-stop-soc`) makes every stop that starts below a floor end at it or
  above.  A floor, not "add at least Δ", because a floor keeps "more energy,
  no later" dominant: every exact planner stays exact (Lemma 1F, Lemma 3F in
  `docs/algorithms.md`), the heuristics raise their targets to it, and the
  evaluator checks it.
- **Vehicles as they are**: NMC, NCA and LFP cells with their own wear and
  cold behaviour, a DC power limit (`dc_max_kw`, `--dc-max-kw`) and connector
  standards (Type 2, CCS2, CHAdeMO, ...); a planner only stops where the car
  can plug in.  The presets include three LFP cars, an AC-only city car and
  an older CHAdeMO hatchback (`docs/model.md` §10).
- **There is no single best route**: Karaman → İstanbul leaving at 14:00
  under the demonstration tariff takes 8 h 09 min for 1,309.59 TRY at the
  fastest, 8 h 20 min for 1,111.61 TRY in a balanced plan, and the Pareto
  front holds 268 plans.
- **Real data, bundled** (1.5 MB): the OpenStreetMap road network of Turkey
  (44 673 nodes, 75 393 km) with SRTM elevations, the 711 public OSM
  charging stations with their sockets and the list prices of ten
  operators; road distances deviate from the official KGM table by 2.52 %
  on average.  ODbL 1.0, © OpenStreetMap contributors.
- **Station data from operators and open data**: adapters for a generic CSV
  schema, OCPI 2.2.1 Locations (with a live availability snapshot and
  Tariffs) and Open Charge Map POIs (`evroute.station_sources`,
  `evroute plan --stations-file`).  The library never goes online;
  `docs/real-data.md` explains where such data comes from in Turkey (EPDK's
  Şarj@TR platform, OCPI, Open Charge Map, research agreements).
- **Verified, not just tested**: an independent evaluator replays every
  plan; a seeded cross-validation suite checks the planners against brute
  force, dynamic programmes, metamorphic relations and local search, with
  random tariffs and random floors.
- **A web app**: the website https://aseloth99.github.io/evroute/ plans in
  the browser (Pyodide, about 6 MB on the first visit) and has prebuilt
  examples.  The viewer animates trips, races the planners and replays
  their searches (TR/EN, light/dark, phones to 3440 px wide monitors), with
  searchable place and vehicle pickers that ignore case and Turkish letters,
  tooltips on every control and term, and two levels of detail: the
  research view (per stop: energy, losses, power, C-rate, price period,
  wear) and an **everyday mode** for drivers - from, to, car, charge and
  charging style in, a trip plan out, to copy, print or open in Google Maps.
  `evroute serve` runs the same app locally.
- **Twelve reproducible experiments** up to 40 000-node networks and on the
  real network, an interactive results dashboard and seaborn figures.
- **Gentle guides** in English and Turkish (`docs/guide.md`,
  `docs/guide.tr.md`), from "why plain A\* is not enough" to "which planner
  should I use?".

Install: `pip install git+https://github.com/Aseloth99/evroute.git`
(or clone and `pip install -e .`), then `evroute plan turkey-karaman-istanbul`
or `evroute serve --open`.

---

## Türkçe

EVRoute'un ilk herkese açık sürümü: elektrikli araçlar için bataryayı,
şarj kuyruklarını ve batarya yıpranmasını hesaba katan, şarj istasyonlu kesin
rota planlama. Saf Python, çalışma zamanı bağımlılığı yok; tarayıcıda planlayan
bir web uygulamasıyla birlikte.

- **Tek bir arayüzde 9 planlayıcı**: karşılaştırma için bataryayı yok sayan
  en hızlı yol, açgözlü "araç içi navigasyon", istasyon grafı ve genetik
  algoritma; kesin etiket ayarlama (BLS, BLS-A\*), kesin sürekli şarj (CFP,
  CFP-A\*) ve süre, para ve batarya yıpranması üzerinde Pareto araması.
  Varsayılan ve günlük kullanım için önerilen BLS-A\*: şarj ızgarasında
  kesin, sürekli optimumdan ortalama %0,11 uzakta, Türkiye'nin 44 673
  düğümlü OpenStreetMap ağında yaklaşık 4 sn (`docs/guide.tr.md` §12).
- Doğrusal olmayan ve kısmi şarj, saate bağlı Erlang-C kuyrukları, arazi ve
  rejeneratif frenleme, sıcaklık, batarya yıpranması, eko hızlar ve **şarj
  tarifeleri** için **tek bir kesin model** (%80 SoC üstünde daha yüksek
  fiyat, sabit ya da istasyonun doluluğundan türetilen yoğun saatler;
  maliyet odaklı planlar ucuz dönemi bekleyebilir, baskınlık yine kesin
  kalır).
- **"+%2"lik ara şarjlara son**: isteğe bağlı asgari mola şarjı
  (`min_stop_soc`, `--min-stop-soc`), bir eşiğin altında başlayan her
  molanın o eşikte ya da üstünde bitmesini sağlar. "En az Δ ekle" yerine bir
  eşik, çünkü eşik "daha fazla enerji, daha geç değil" baskınlığını korur:
  bütün kesin planlayıcılar kesin kalır (`docs/algorithms.md` içinde Lemma
  1F, Lemma 3F), sezgiseller hedeflerini eşiğe yükseltir, değerlendirici de
  kuralı denetler.
- **Araçlar olduğu gibi**: kendi yıpranma ve soğuk davranışlarıyla NMC, NCA
  ve LFP hücreler, bir DC güç sınırı (`dc_max_kw`, `--dc-max-kw`) ve
  konnektör standartları (Type 2, CCS2, CHAdeMO, ...); planlayıcı yalnızca
  aracın fişini takabildiği yerde durur. Hazır profiller arasında üç LFP'li
  araç, yalnızca AC ile şarj olan bir şehir aracı ve CHAdeMO'lu eski bir
  hatchback de var (`docs/model.md` §10).
- **Tek bir en iyi rota yok**: Gösterim tarifesiyle 14:00'te Karaman →
  İstanbul en hızlı planla 8 sa 09 dk ve 1 309,59 TRY, dengeli bir
  planla 8 sa 20 dk ve 1 111,61 TRY; Pareto cephesinde 268 plan var.
- **Paketle gelen gerçek veri** (1,5 MB): SRTM yükseklikleriyle Türkiye'nin
  OpenStreetMap yol ağı (44 673 düğüm, 75 393 km), soketleriyle birlikte
  OSM'deki 711 halka açık şarj istasyonu ve on operatörün liste fiyatları;
  yol mesafeleri KGM'nin resmî tablosundan ortalama %2,52 sapar. ODbL 1.0,
  © OpenStreetMap contributors.
- **Operatörlerden ve açık veriden istasyon verisi**: genel bir CSV şeması,
  OCPI 2.2.1 Location nesneleri (anlık müsaitlik görüntüsü ve tarifelerle)
  ve Open Charge Map POI'leri için bağdaştırıcılar
  (`evroute.station_sources`, `evroute plan --stations-file`). Kütüphane
  internete bağlanmaz; bu verinin Türkiye'de nereden geldiğini (EPDK'nin
  Şarj@TR platformu, OCPI, Open Charge Map, araştırma anlaşmaları)
  `docs/real-data.tr.md` anlatır.
- **Yalnızca test edilmedi, doğrulandı**: bağımsız bir değerlendirici her
  planı yeniden simüle eder; sabit tohumlu çapraz doğrulama paketi
  planlayıcıları kaba kuvvet, dinamik programlar, metamorfik ilişkiler ve
  yerel aramayla, rastgele tarifeler ve rastgele eşiklerle de sınar.
- **Bir web uygulaması**: https://aseloth99.github.io/evroute/ web sitesi
  tarayıcıda planlar (Pyodide; ilk ziyarette yaklaşık 6 MB) ve hazır
  örnekler sunar. Görüntüleyici yolculukları canlandırır, planlayıcıları
  yarıştırır ve aramalarını yeniden oynatır (TR/EN, açık/koyu tema,
  telefondan 3440 piksel genişliğindeki monitörlere kadar). Büyük-küçük harf
  ve Türkçe karakter farkını gözetmeyen aranabilir yer ve araç seçicileri,
  her denetim ve terim için ipuçları ve iki ayrıntı düzeyi vardır: araştırma
  görünümü (her molada enerji, kayıplar, güç, C-oranı, fiyat dönemi,
  yıpranma) ve sürücüler için **günlük kullanım modu**: nereden, nereye,
  araç, şarj ve şarj tarzı girilir; kopyalanabilen, yazdırılabilen ya da
  Google Haritalar'da açılabilen bir yol planı çıkar. `evroute serve` aynı
  uygulamayı yerelde çalıştırır.
- 40 000 düğüme kadar ağlarda ve gerçek ağda **on iki tekrarlanabilir
  deney**, etkileşimli bir sonuç paneli ve seaborn grafikleri.
- Algoritmalara İngilizce ve Türkçe **sade rehberler** (`docs/guide.md`,
  `docs/guide.tr.md`): "düz A\* neden yetmez?" sorusundan "hangi
  planlayıcıyı kullanmalıyım?" sorusuna kadar.

Kurulum: `pip install git+https://github.com/Aseloth99/evroute.git`
(ya da klonlayıp `pip install -e .`), ardından
`evroute plan turkey-karaman-istanbul` ya da `evroute serve --open`.
