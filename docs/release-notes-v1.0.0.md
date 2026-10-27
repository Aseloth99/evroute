# EVRoute v1.0.0

**English** · [Türkçe](#türkçe)

The first public release of EVRoute: exact, battery-, queue- and
degradation-aware route planning for electric vehicles with charging
stations, in pure Python with zero runtime dependencies.

- **9 planners behind one API**: a battery-blind fastest path, a greedy
  "in-car navigator", a station graph and a genetic algorithm as baselines;
  exact label setting (BLS, BLS-A\*), exact continuous charging (CFP, CFP-A\*)
  and a Pareto search over time, money and battery wear.
- **One exact model** for nonlinear and partial charging, time-dependent
  Erlang-C queues, terrain and regeneration, temperature, battery wear, eco
  speeds and, new in this release, **charging tariffs**: a higher price above
  80 % SoC and peak hours (fixed or derived from how busy a station is).
  Cost-optimal plans may wait for the cheaper period, and dominance stays
  exact (proof in `docs/algorithms.md` §6).
- **There is no single best route**: the Pareto search shows the trade-off.
  Karaman → İstanbul leaving at 14:00 under the demonstration tariff: the
  fastest plan takes 8 h 09 min for 1,309.59 TRY, a balanced plan 8 h 20 min
  for 1,111.61 TRY, and the front holds 268 plans.
- **Verified, not just tested**: an independent evaluator replays every plan;
  a seeded cross-validation suite checks the planners against brute force,
  metamorphic relations and local search, now including tariffs.
- **A real application**: an animated single-file web viewer (trip, race and
  search-replay modes, TR/EN, light/dark), a live planning server, an
  interactive experiments dashboard and seaborn figures.
- **Turkey case study** (approximate network, synthetic charging stations)
  and eleven reproducible experiments, up to 40 000-node networks.
- **Gentle guides** to the algorithms in English and Turkish:
  `docs/guide.md`, `docs/guide.tr.md`.

Install: `pip install git+https://github.com/Aseloth99/evroute.git`
(or clone and `pip install -e .`), then `evroute plan turkey-karaman-istanbul`.

---

## Türkçe

EVRoute'un ilk herkese açık sürümü: elektrikli araçlar için bataryayı,
şarj kuyruklarını ve batarya yıpranmasını hesaba katan, şarj istasyonlu kesin
rota planlama. Saf Python, çalışma zamanı bağımlılığı yok.

- **Tek bir arayüzde 9 planlayıcı**: karşılaştırma için bataryayı yok sayan
  en hızlı yol, açgözlü "araç içi navigasyon", istasyon grafı ve genetik
  algoritma; kesin etiket ayarlama (BLS, BLS-A\*), kesin sürekli şarj (CFP,
  CFP-A\*) ve süre, para ve batarya yıpranması üzerinde Pareto araması.
- Doğrusal olmayan ve kısmi şarj, saate bağlı Erlang-C kuyrukları, arazi ve
  rejeneratif frenleme, sıcaklık, batarya yıpranması ve eko hızlar için
  **tek bir kesin model**. Bu sürümle gelen yenilik **şarj tarifeleri**:
  %80 doluluk üstünde daha yüksek fiyat ve yoğun saatler (sabit ya da
  istasyonun doluluğundan türetilen). Maliyet odaklı planlar ucuz dönemi
  bekleyebilir; baskınlık yine kesin kalır (ispat: `docs/algorithms.md` §6).
- **Tek bir en iyi rota yok**: Pareto araması ödünleşimi gösterir. Gösterim
  tarifesiyle 14:00'te Karaman → İstanbul: en hızlı plan 8 sa 09 dk ve
  1.309,59 TL, dengeli bir plan 8 sa 20 dk ve 1.111,61 TL; cephede 268 plan
  var.
- **Yalnızca test edilmedi, doğrulandı**: bağımsız bir değerlendirici her
  planı yeniden sürer; sabit tohumlu çapraz doğrulama paketi planlayıcıları
  kaba kuvvet, metamorfik ilişkiler ve yerel arama ile, artık tarifelerle de
  sınar.
- **Gerçek bir uygulama**: animasyonlu tek dosyalık web görüntüleyici
  (yolculuk, yarış ve arama tekrarı; TR/EN; açık/koyu tema), canlı planlama
  sunucusu, etkileşimli deney paneli ve seaborn grafikleri.
- **Türkiye vaka çalışması** (yaklaşık ağ, sentetik şarj istasyonları) ve
  40 000 düğüme kadar on bir tekrarlanabilir deney.
- Algoritmalara İngilizce ve Türkçe **sade rehberler**: `docs/guide.md`,
  `docs/guide.tr.md`.

Kurulum: `pip install git+https://github.com/Aseloth99/evroute.git`
(ya da klonlayıp `pip install -e .`), ardından
`evroute plan turkey-karaman-istanbul`.
