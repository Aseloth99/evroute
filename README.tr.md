<div align="center">

# EVRoute

**Elektrikli araçlar için bataryayı, şarj kuyruklarını ve batarya yıpranmasını hesaba katan, şarj istasyonlu kesin rota planlama.**

<img src="docs/img/demo.gif" width="880" alt="EVRoute görüntüleyicisi: optimal plan Karaman → İstanbul yolunu iki şarj molasıyla tamamlıyor, ardından tüm algoritmalar aynı saat üzerinde yarışıyor">

*Nerede ve ne kadar şarj edeceğiniz, hangi hızla gideceğiniz, ne zaman yola çıkacağınız — hepsi optimallikleri ispatlanmış algoritmalarla hesaplanır, bağımsız bir değerlendiriciyle denetlenir ve tarayıcınızda canlandırılır.*

Saf Python ≥ 3.10 · çalışma zamanında sıfır bağımlılık · MIT lisansı<br>
[English](README.md) · **Türkçe** · [**Canlı demo**](https://aseloth99.github.io/evroute/live/?lang=tr) · [Dokümantasyon](docs/index.md) · [Rehber](docs/guide.tr.md) · [Model](docs/model.md) · [Algoritmalar ve ispatlar](docs/algorithms.md)

</div>

---

## Neden EVRoute?

Elektrikli araçlar için rota planlama "çözülmüş bir problem" gibi görünür — ta
ki gerçek bir yolculuğun neleri kapsadığını tek tek sıralayana kadar.
Yazılımların çoğu bu etkilerin bir ya da ikisini ele alır, gerisini
basitleştirip geçer:

| Etki | Gerçekte | EVRoute |
|---|---|---|
| Doğrusal olmayan şarj | şarj durumu (SoC) ~%50-80'i geçince güç keskin biçimde düşer | her araç × şarj ünitesi çifti için kesin şarj eğrileri |
| Kısmi şarj | şarj %100'de değil, %54'te kesilir | sürekli şarj miktarları (CFP) ya da ince bir ızgara (BLS) |
| Kısa molalar | birkaç puanlık şarj için otoyoldan çıkan sürücü azdır | her mola için isteğe bağlı bir asgari şarj düzeyi; planlayıcılar yine kesin kalır |
| Araçlar ve soketler | LFP hücreler, 50 kW'lık bir DC sınırı, yalnızca CHAdeMO kabul eden bir araç | her araç için hücre kimyası, DC güç sınırı ve konnektörler |
| Kuyruklar | öğleden sonraları yoğun dinlenme tesislerinde 20-40 dk | saatlik talep profilli Erlang-C kuyrukları |
| Arazi ve soğuk | inişler bataryayı yeniden doldurur; −5 °C'de kapasite küçülür, şarj yavaşlar | eğim, rüzgâr ve rejeneratif frenlemeyi içeren fizik modeli; soğukta kapasite ve şarj |
| Batarya yıpranması | yüksek SoC'de hızlı şarj batarya paketini yaşlandırır | amaç fonksiyonunda fiyatlandırılır |
| Fiyatlar | %80 SoC'nin üzerinde ve puant saatlerde daha pahalı | tarifeler; beklemenin ne zaman kârlı olduğu kesin olarak planlanır |
| Belirsizlik | tüketim ±%10 sapar | Monte Carlo güvenilirlik analizi, güvenlik katsayısı |

EVRoute bunların **hepsini tek bir kesin modelde** birleştirir; modeli
optimallik ispatlarıyla birlikte gelen algoritmalarla çözer ve her yanıtı
bağımsız bir yeniden simülasyonla doğrular. Sezgisellerin, öğrenen
modellerin ve ticari planlayıcıların kendisiyle kıyaslanabileceği bir
**referans uygulama** olarak tasarlanmıştır. Etkilerin tam listesi:
[`docs/model.md`](docs/model.md#at-a-glance).

## Öne çıkanlar

- **Tek API ardında 9 planlayıcı** — bataryayı hesaba katmayan en hızlı yoldan
  ve "araç içi navigasyon" sezgiselinden; kesin etiket ayarlamaya (BLS,
  BLS-A\*), kesin sürekli şarja (CFP, CFP-A\*) ve süre, para ve batarya
  yıpranması üzerinde bir Pareto aramasına kadar.
- **Kesin ve doğrulanmış** — sürekli enerjiler, kesin şarj eğrileri,
  [optimallik ispatları](docs/algorithms.md); her plan bağımsız bir
  değerlendiricide yeniden simüle edilir ve kaba kuvvetle çapraz denetlenir.
- **Tek bir en iyi rota yok** — %80 kuralı ve puant saatlerle şarj
  tarifeleri; daha ucuz fiyatı beklemenin ne zaman kârlı olduğunun kesin
  planlanması ([aşağıda](#tek-bir-en-iyi-rota-yok)).
- **Araçlar ve sürücüler olduğu gibi** — NMC, NCA ve LFP hücreler, bir DC güç
  sınırı ve konnektör standartları (planlayıcı yalnızca aracın fişini
  takabildiği yerde durur); "+%2"lik ara şarjlar yerine her mola için bir
  asgari şarj düzeyi, üstelik kesin planlayıcılar bununla da kesin kalır
  ([rehber §13](docs/guide.tr.md#13-her-molada-en-az-bir-şarj-düzeyi-taban-kuralı)).
- **Görerek anlayın** — tarayıcınızda planlayan bir web uygulaması:
  animasyonlu bir araştırma görünümü (yolculuk, yarış ve arama tekrarı
  modları, her molanın tüm ayrıntıları) ve sürücüler için kopyalanabilen,
  yazdırılabilen ya da Google Haritalar'da açılabilen bir yol planı sunan
  **günlük kullanım modu**; aranabilir seçiciler, ipuçları, TR/EN, açık/koyu
  tema, telefondan geniş monitörlere kadar uyum. Ayrıca canlı bir planlama
  sunucusu ve etkileşimli bir deney paneli.
- **Türkiye vaka çalışmaları** — yaklaşık bir ağ (1 279 düğüm, 146 yol, 10
  dağ geçidi, 290 sentetik şarj istasyonu) ve
  [gerçek veriyle bir ağ](#gerçek-veriyle-vaka-çalışması-openstreetmap):
  SRTM yükseklikli 44 673 OpenStreetMap düğümü, OSM'deki 711 şarj
  istasyonu ve operatör liste fiyatları; ayrıca 40 000 düğüme kadar
  sentetik ağlar ve bir OpenStreetMap yükleyicisi.
- **Kendi istasyonlarınız** — şarj noktalarını bir CSV dosyasından, OCPI
  2.2.1 Location nesnelerinden ya da Open Charge Map'ten içe aktarın
  (`--stations-file`); Türkiye'de hangi operatör ve canlı verilerin
  bulunduğu ve nasıl kullanılacağı:
  [gerçek veri](docs/real-data.tr.md#canlı-ve-operatör-verisi).
- **Tekrarlanabilir** — her tablonun ve grafiğin arkasında sabit tohumlu on
  iki deney, tek komut; algoritmalar için [anlaşılır bir rehber](docs/guide.tr.md).

## Ekran görüntüleri

<table>
<tr>
<td width="50%"><img src="docs/img/viewer-light.png" alt="Görüntüleyici: rota listesi, arazi haritası ve yolculuk özeti"><br><sub><b>Yolculuk modu.</b> Her algoritmanın rotası rölyef gölgelendirmeli bir haritada; seçili plan durakları, kuyruğu, şarjı ve maliyetleriyle birlikte.</sub></td>
<td width="50%"><img src="docs/img/viewer-search.png" alt="Arama tekrarı: BLS-A* 73 düğüm, BLS 493 düğüm tarıyor"><br><sub><b>Arama tekrarı.</b> Aynı optimum, iki ayrı arama: BLS-A* (üstte) yalnızca dar bir koridoru kesinleştirirken Dijkstra sırasıyla ilerleyen BLS (altta) bütün ülkeye yayılır.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/img/viewer-charts.png" alt="Grafikler: SoC ve rakım, kuyruk profili, süre dağılımı, Pareto cephesi, karşılaştırma tablosu"><br><sub><b>Grafikler.</b> Rakım profiliyle birlikte mesafeye göre SoC, 24 saatlik kuyruk profili, süre dağılımı, süre–para Pareto cephesi ve sıralanabilir bir karşılaştırma tablosu.</sub></td>
<td width="50%"><img src="docs/img/results-light.png" alt="Sonuç paneli: her deneyin temel bulguları"><br><sub><b>Sonuç paneli.</b> Tüm deneyler tek bir çevrimdışı sayfada: hesaplanmış bulgular, kesinlik denetimleri, tablolar ve CSV indirmeleri.</sub></td>
</tr>
</table>

<details>
<summary><b>Daha fazlası: koyu tema, şarj animasyonu, seaborn grafikleri</b></summary>

<p align="center">
<img src="docs/img/viewer-dark.png" width="49%" alt="Koyu temada yarış modu">
<img src="docs/img/viewer-charging.png" width="49%" alt="Kütahya DC'de şarj animasyonu">
</p>
<p align="center">
<img src="docs/img/fig_map_turkey.png" width="49%" alt="Türkiye haritasında tüm rotalar">
<img src="docs/img/fig_soc_turkey.png" width="49%" alt="Her algoritma için rota boyunca şarj durumu">
</p>
<p align="center">
<img src="docs/img/fig_search_space_turkey.png" width="98%" alt="BLS, BLS-A*, CFP ve CFP-A*'ın kesinleştirdiği düğümler">
</p>
<p align="center">
<img src="docs/img/fig_charging_curves.png" width="98%" alt="Hazır araç profillerinin şarj eğrileri">
</p>
<p align="center">
<img src="docs/img/fig_pareto_turkey.png" width="42%" alt="Süre ve şarj maliyeti için Pareto cephesi">
<img src="docs/img/fig_gap_heatmap_turkey.png" width="56%" alt="16 Türkiye yolculuğunda her algoritmanın optimallik açığı">
</p>
</details>

## Hızlı başlangıç

Hiçbir şey kurmadan denemek için [canlı demoyu](https://aseloth99.github.io/evroute/live/?lang=tr) açın: planlama tarayıcınızda yapılır; *Günlük kullanım* anahtarı onu sade bir yolculuk planlayıcısına dönüştürür.

```bash
git clone https://github.com/Aseloth99/evroute.git
cd evroute
pip install -e .            # planlayıcı: hiçbir bağımlılığı yok
pip install -e ".[plot]"    # isteğe bağlı: statik grafikler için seaborn
pip install -e ".[dev]"     # isteğe bağlı: + pytest, testler için
```

### Komut satırı

```bash
evroute scenarios                                    # hazır senaryoları listeler
evroute plan turkey-karaman-istanbul                 # BLS-A* ile planlar (varsayılan)
evroute plan turkey-karaman-istanbul-tariff --objective cost      # %80 kuralı + puant saatler: 20:00'yi bekler
evroute plan turkey-karaman-istanbul --vehicle hatch51_lfp --dc-max-kw 80   # LFP'li bir hatchback, DC 80 kW ile sınırlı
evroute plan turkey-osm-karaman-istanbul --min-stop-soc 0.4       # gerçek veri; her mola %40'ta ya da üstünde biter
evroute viz turkey-karaman-istanbul -o karaman-istanbul.html   # animasyonlu görüntüleyici, çevrimdışı çalışır
evroute serve turkey-karaman-istanbul --open          # tarayıcıda canlı planlama
evroute experiments --quick -o results/               # on iki deneyin tamamı, hızlı boyut (~1,5 dk)
```

```text
$ evroute plan turkey-karaman-istanbul
[bls-astar] 8 h 07 min | 747.27 km | 2 stop(s) | arrive 10.4% | 1138.78 TRY
#  station     charger    arrive  queue  charge  SoC         cost
-  ----------  ---------  ------  -----  ------  ----------  ----------
1  Akşehir DC  DC 120 kW   10:20  3 min  11 min  28% -> 54%  336.73 TRY
2  Kütahya DC  DC 150 kW   12:28  3 min  25 min  10% -> 72%  802.05 TRY
arrival 16:06 | drive 7 h 19 min | charge 36 min | queue+setup 12 min | battery wear 0.0188 %SoH
```

Para Türk lirası (TRY), batarya yıpranması sağlık durumunun yüzde puanı
(%SoH) cinsindendir. Tüm komutlar ve seçenekler (başlangıç, varış, SoC,
kalkış saati, sıcaklık, araç, tarife, ...):
[dokümantasyon](docs/index.md#command-line) ya da `evroute <command> -h`.

### Python

```python
from evroute import plan
from evroute.generators.presets import get_scenario
from evroute.robustness import monte_carlo

scenario = get_scenario("turkey-karaman-istanbul")
route = plan(scenario, "bls-astar")  # şarj hedefi ızgarasında kesin
print(route)

# Ya koşullar değişirse? Senaryoyu değiştirmeyin; with_* değiştirilmiş bir kopya döndürür.
print(plan(scenario.with_conditions(temperature_c=-5), "cfp-astar"))   # kış, sürekli şarj
print(plan(scenario.with_preferences(objective="cost"), "bls-astar"))  # süre + para + batarya yıpranması

# Süre–para ödünleşimi (etiket bütçesi aramayı etkileşimli hızda tutar)
front = plan(scenario, "pareto", criteria=("time", "money"), max_labels=1_000_000)
print(len(front.alternatives), "Pareto-optimal routes; complete:", front.stats.extra["complete"])

# Tüketim ve kuyruklar rastgele olduğunda plan ne sıklıkla başarısız olur?
mc = monte_carlo(scenario.prepare(), route, n=2000, seed=0)
print(f"P(strand) = {mc.p_strand:.2%}   P(reserve breach) = {mc.p_reserve_breach:.1%}")
```

```text
[bls-astar] 8 h 07 min | 747.27 km | 2 stop(s) | arrive 10.4% | 1138.78 TRY
[cfp-astar] 8 h 23 min | 747.27 km | 3 stop(s) | arrive 10.0% | 1669.75 TRY
[bls-astar] 8 h 15 min | 747.27 km | 2 stop(s) | arrive 10.5% | 1092.91 TRY
32 Pareto-optimal routes; complete: False
P(strand) = 0.40%   P(reserve breach) = 51.9%
```

(Pareto araması etiket bütçesinde durduğu için cephenin boyutu kayan
noktalı yuvarlamaya bağlıdır ve Python 3.12, `sum()` fonksiyonunun
yuvarlamasını değiştirdi: Python 3.10 ve 3.11'de 33 yazdırılır.) Son satır,
sağlamlık modülünün neden var olduğunu özetliyor: *beklenen*
tüketime göre optimal olan bir plan hedefe %10'luk rezervin ancak biraz
üstünde (%10,4) varır; dolayısıyla rastgele gerçekleşmelerin yarısında bu
rezervin altına düşülür. Birkaç dakika karşılığında güvenilirlik satın almak
için `safety_factor` > 1 ile (`--safety`) planlayın — bkz.
[sağlamlık deneyi](#deneyler). Kendi yol ağınız, aracınız ve şarj
istasyonlarınız ya da OpenStreetMap verileri için:
[dokümantasyon](docs/index.md#your-own-network-vehicle-and-chargers).

## Gereksinimler

- Windows, macOS ya da Linux üzerinde **Python ≥ 3.10** (3.10-3.13 üzerinde
  sınanır); **çalışma zamanında hiçbir bağımlılık yok**: planlayıcı,
  görüntüleyici, canlı sunucu ve paketle gelen Türkiye gerçek veri ağı
  (1,5 MB) yalnızca standart kütüphaneyi kullanır. Gerçek veri ağındaki
  planlar birkaç yüz MB bellek ister.
- İsteğe bağlı: `.[plot]` statik grafikler için seaborn ≥ 0.13'ü
  (matplotlib, numpy ve pandas ile) ekler; o olmadan `evroute experiments`
  grafikler dışında her şeyi yazar. `.[dev]` test paketi için pytest'i
  ekler; testler kurulan pakette değil, depodadır. `osmnx` başka
  OpenStreetMap ağlarını indirir
  ([dokümantasyon](docs/index.md#your-own-network-vehicle-and-chargers)).
- Yalnızca geliştiriciler için: ekran görüntülerini ve GIF'i yeniden üretmek
  için Playwright'lı Node.js; gerçek veri ağını yeniden kurmak için
  `osmium`, `numpy`, `scipy`, `shapely`, `openpyxl`, yaklaşık 1 GB disk ve
  internet erişimi ([gerçek veri](docs/real-data.tr.md#veriyi-yeniden-oluşturmak)).

[`requirements.txt`](requirements.txt) aynı bilgiyi, böyle bir dosya arayan
araçlar için verir.

## Örnek: Karaman → İstanbul

Konya Ovası ve Afyon–Kütahya–Bursa koridoru boyunca 747 km: 85 kWh bataryalı
yerli bir C-SUV saat 08:00'de %80 şarjla yola çıkıyor; yol boyunca ve varışta
%10 rezerv korunuyor, her şarj noktasında beklenen kuyruklar hesaba katılıyor.
Çalışma süreleri, 2,1 GHz'lik bir Xeon çekirdeğinde (Python 3.13) yalnızca
planlayıcının ölçüldüğü beş koşunun medyanıdır; açık, CFP'nin sürekli
optimumuna göre ölçülmüştür; *kesinleşen etiket* ise bir planlayıcının
kesinleştirmek zorunda kaldığı kısmi rotaların sayısıdır, yani arama
çabasının bir ölçüsü:

| algoritma | yolculuk süresi | açık | durak | şarj maliyeti [TRY] | varış SoC | kesinleşen etiket | çalışma süresi |
|---|---:|---:|---:|---:|---:|---:|---:|
| `naive` | 364 km sonra yolda kalır | | 0 | | −%77,2 | 887 | 1 ms |
| `greedy` | 8:30:51 | +%4,97 | 2 | 1 659,68 | %47,1 | 2 076 | 9 ms |
| `station-graph` | 9:19:35 | +%14,98 | 2 | 1 180,77 | %17,0 | 14 | 61 ms |
| `genetic` | 8:08:28 | +%0,37 | 2 | 1 175,32 | %10,0 | 754 | 244 ms |
| `bls` | 8:06:56 | +%0,05 | 2 | 1 138,78 | %10,4 | 42 980 | 258 ms |
| **`bls-astar`** | **8:06:56** | +%0,05 | 2 | 1 138,78 | %10,4 | **1 491** | **38 ms** |
| `cfp` | 8:06:41 | 0 | 2 | 1 133,96 | %10,0 | 5 665 | 383 ms |
| **`cfp-astar`** | **8:06:41** | 0 | 2 | 1 133,96 | %10,0 | **347** | **27 ms** |

- **Bataryayı hesaba katmayan rota** 364 km sonra yolda kalır; "gerektiğinde
  %80'e kadar şarj et" diyen açgözlü (greedy) sezgisel 24 dakika daha yavaş
  ve %46 daha pahalıdır.
- **Asıl fark kısmi şarjda:** optimal plan Akşehir'de kısa (%28 → %54),
  Kütahya'da daha uzun (%10 → %72) mola verir ve hedefe tam olarak gereken
  %10'la varır.
- **A\* karşılığını veriyor:** BLS-A\*, BLS'ye göre 29 kat daha az etiket
  kesinleştirir (CFP-A\*, CFP'ye göre 16 kat daha az) ve birebir aynı
  optimumu bulur.
- %2'lik bir SoC ızgarasıyla sınırlı BLS, CFP'nin sürekli optimumunun
  yalnızca 15,6 sn gerisinde kalır.

> [!IMPORTANT]
> Türkiye verileri **yaklaşıktır, örnekleme amaçlıdır, navigasyon için uygun
> değildir**: coğrafya yer adları sözlüğü (gazetteer) düzeyindedir, yol
> uzunlukları modellenmiştir; **şarj istasyonları, fiyatlar, kuyruklar ve
> tarife sentetiktir** ve genel operatör adları kullanır. Algoritmaları
> karşılaştırmak için kullanın, gerçek bir yolculuğu planlamak için değil
> ([verilerin kaynağı](docs/index.md#turkey-case-study)).

## Gerçek veriyle vaka çalışması (OpenStreetMap)

Aynı yolculuklar, paketle birlikte gelen gerçek verilerle de çalışır:
Türkiye'nin OpenStreetMap yol ağı (44 673 düğüm; 75 393 km otoyol, ana yol
ve birincil yol; OSM hız sınırları ya da yasal varsayılanlar), her düğümde
SRTM yükseklikleri, OSM'de işaretlenmiş 711 halka açık şarj istasyonu ve
on operatörün yayımladığı liste fiyatları. **Varsayım** olanlar kuyruklar
(EPDK'nin ulusal ortalamaları) ve OSM'de bulunmayan değerlerdir: istasyon
güçlerinin %64'ü varsayılandır. 81 il merkezi arasındaki yol mesafeleri
KGM'nin resmî tablosundan ortalama %2,52 sapar.

```bash
evroute plan turkey-osm-karaman-istanbul   # 9 sa 14 dk, 751,81 km, 2 mola, 999,92 TRY (BLS-A*, yaklaşık 5 sn)
```

- **OSM az sayıda şarj noktası biliyor:** EPDK'nin saydığı (Eylül 2026) AC
  soketlerin yaklaşık %3,3'ü, DC soketlerin %1,9'u; 38° doğu boylamının
  doğusunda hiç DC istasyon yok. `hybrid` istasyon kümesi
  (`--stations hybrid`), bir ana yolun her DC şarj noktasına 75 km'den uzak
  olduğu yerlere işaretli sentetik DC boşluk doldurucular ekler.
- **Kesin planlayıcılar kesin ve hızlı kalır:** BLS-A\* ve CFP-A\*, OSM
  istasyonlarıyla 6 yolculuğun 5'inde (doğuda DC şarj noktası olmayan
  İzmir → Erzurum etiket sınırına takılır), hibrit kümeyle 6'sında da
  optimumu bulur; 44 673 düğümde OSM istasyonlarıyla altı yolculuğun medyan
  çalışma süresi 4,0 sn ve 9,9 sn'dir ve BLS-A\*, CFP-A\*'ın %0,4 yakınında
  kalır. OSM istasyonlarıyla optimum, yaklaşık ağdakinden ortalama %7,0 daha
  uzun sürer; farkın neredeyse tamamı daha yavaş sürüşten gelir.
- **Sezgiseller kalmaz:** OSM istasyonlarıyla greedy, yavaş AC şarj
  noktalarında saatlerce şarj ederek optimumdan ortalama %153 yavaş kalır
  (yaklaşık ağda %6,4); station graph tek bir yolculuk için 3 sa 46 dk'ya
  kadar uğraştı (hibrit küme; deney artık bir sezgiseli 10 dakika sonra
  durdurur).

> [!NOTE]
> Veri © OpenStreetMap contributors, ODbL 1.0; yükseklikler SRTM (NASA/USGS).
> Araştırma verisidir, navigasyon için uygun değildir. Mart 2026 şarj
> yönetmeliği ve operatör ile canlı verinin nereden gelebileceği dahil
> ayrıntılar: [`docs/real-data.tr.md`](docs/real-data.tr.md) ([English](docs/real-data.md)).

## Tek bir en iyi rota yok

Bir yolculuğun üç bedeli vardır: **zaman**, **para** ve **batarya
yıpranması**; üçü de farklı yönlere çeker. Şarj fiyatları bu ödünleşimi daha
da keskinleştirir; EVRoute yaygın iki uygulamayı bir `Tariff` ile modeller:

- **%80 kuralı.** SoC yaklaşık %80'i geçince şarj gücü keskin biçimde düşer;
  bu yüzden işletmeciler `high_soc` üzerinde depolanan her kWh için daha
  yüksek bir fiyat ister (Türkiye'de halka açık istasyonlarda değil: bu tür
  kademeler Mart 2026'dan beri yasak,
  [gerçek veri](docs/real-data.tr.md#fiyatlar-ve-mart-2026-yönetmeliği)).
- **Puant saatler.** Günün yoğun saatlerinde bir puant çarpanı uygulanır;
  ya sabit saat aralıklarında ya da istasyonun kuyruk modelinin yoğunluk
  beklediği saatlerde.

Fiyat, kuyruğa girdiğiniz anda belirlenir; bu yüzden daha ucuz fiyatı
beklemek kârlı olabilir. EVRoute bu *gönüllü beklemeleri* kesin olarak
planlar ve yalnızca kârlı olduklarında yapar. `turkey-karaman-istanbul-tariff`
hazır senaryosu, yukarıdaki yolculuğun saat 14:00'te örnekleme amaçlı
gösterim tarifesiyle (%80 SoC'nin üzerinde ×1,3; 16:00-20:00 puantında
×1,15; puantta %80'in üzerinde ×2,3) yapılanıdır:

<p align="center"><img src="docs/img/fig_tradeoff_tariff.png" width="98%" alt="Gösterim tarifesiyle saat 14:00'te yola çıkan Karaman → İstanbul yolculuğunun süre–para Pareto cephesi: 8 sa 09 dk ve 1 309,59 TRY'den 14 sa 44 dk ve 796,58 TRY'ye uzanan 268 plan; 8 sa 20 dk ve 1 111,61 TRY'lik dengeli plan, cephenin yatıklaştığı yerde"></p>

| plan | yolculuk süresi | şarj maliyeti | batarya yıpranması | ne yapıyor |
|---|---:|---:|---:|---|
| en hızlı (`bls-astar`) | 8 sa 09 dk | 1 309,59 TRY | %0,0188 SoH | Akşehir %28 → %54 ve Kütahya %10 → %72, ikisi de puantta; aynı molalar sabit fiyatla 1 138,78 TRY tutar (+%15) |
| dengeli (`--objective cost`) | 8 sa 20 dk | 1 111,61 TRY | %0,0182 SoH | Ilgın %39 → %85, kuyruğa puanttan önce, 15:52'de giriyor; İnegöl %10 → %52, 20:00'yi 2,4 dk bekledikten sonra |
| en ucuz (cephenin ucu) | 14 sa 44 dk | 796,58 TRY | %0,0162 SoH | üç yavaş 22 kW AC molası, hiçbiri %80'in üzerine çıkmıyor; ilki 15:52'de kuyruğa giriyor ve puant dışı fiyatla 3 sa 09 dk şarj ediyor |

Her bakımdan kazanan bir plan yok ve ilk dakikalar en çok kazandırır: 11
dakika fazlası 197,98 TRY (−%15,1) kazandırır, sonraki 6 sa 24 dk ise
yalnızca 315,04 TRY daha. Pareto araması cephedeki 268 planın tamamını
bulur; hangisinin doğru olduğu, bir saatin sizin için ne ettiğine bağlıdır.

```bash
evroute plan turkey-karaman-istanbul-tariff --objective cost   # dengeli plan
```

Fikri adım adım [rehber](docs/guide.tr.md#11-tarifeler-tek-bir-en-iyi-rota-yok)
anlatıyor; model [`docs/model.md` §5](docs/model.md#5-charging-tariffs)
bölümünde; örneğin tamamı (komut satırı çıktıları, Python'da Pareto araması,
bütün bir günün kalkışları) [dokümantasyonda](docs/index.md#worked-example).

## Algoritmalar

| ad | fikir | garanti |
|---|---|---|
| `naive` | en hızlı yol, batarya yok sayılır | yok |
| `greedy` | araç içi navigasyon: batarya yetmeyecekse bir şarj noktasına sap, %80'e (DC) / %100'e (AC) kadar şarj et | sezgisel |
| `station-graph` | istasyonlar üzerinde Dijkstra, sabit şarj hedefi | yalnızca kendi meta-grafı üzerinde optimal |
| `genetic` | istasyonlar ve hedef SoC'ler üzerinde sabit tohumlu bir genetik algoritma | sezgisel |
| `bls` | (düğüm, enerji) üzerinde Dijkstra sırasıyla etiket ayarlama | şarj ızgarasında **kesin** |
| `bls-astar` | BLS + tutarlı bir A\* potansiyeli (varsayılan) | `bls` ile aynı optimum, çok daha az etiket |
| `cfp` | şarj fonksiyonu yayılımı: sürekli şarj miktarları | **kesin** en hızlı rota |
| `cfp-astar` | A\* anahtarıyla CFP | `cfp` ile aynı optimum |
| `pareto` | süre, para ve yıpranma üzerinde çok ölçütlü etiket ayarlama | ızgara üzerinde **kesin** Pareto cephesi |

Kesin planlayıcılar yolculuk süresini ya da süre, para ve yıpranmadan oluşan
genelleştirilmiş bir maliyeti en küçükler (CFP: yalnızca süre); zamana bağlı
kuyrukları, eko hızları, rejeneratif frenlemeyi, tarifeleri, aracın
konnektörlerini ve her mola için bir asgari şarj düzeyini destekler.
Nasıl çalıştıkları: [rehber](docs/guide.tr.md) (adım adım; sonunda
"[hangi planlayıcıyı kullanmalıyım?](docs/guide.tr.md#12-hangi-planlayıcıyı-kullanmalıyım)")
ve [`docs/algorithms.md`](docs/algorithms.md#at-a-glance) (genel bakış ve ispatlar).

## Deneyler

Sabit tohumlu on iki deney, eşlik eden makaledeki tüm tablo ve şekilleri
yeniden üretir. Her deney CSV, JSON, grafikler (`plot` ekiyle) ve LaTeX
tabloları yazar; koşu, etkileşimli ve çevrimdışı bir panel olan
`results.html` ile tamamlanır.

```bash
evroute experiments --quick -o results/            # hızlı boyut: ~1,5 dakika
evroute experiments -o results/ --lang en          # tam boyut, İngilizce panel
evroute gallery -o docs/img                        # bu README'deki seaborn grafikleri
```

| deney | bulgu |
|---|---|
| `optimality-gap` | ≥ 300 km'lik 60 rastgele yolculukta sürekli optimuma göre açık: greedy ortalama %5,2, station graph %12,8, genetic %0,75, %2'lik ızgarasında BLS-A\* %0,11 |
| `queue-awareness` | beklenen kuyrukları hesaba katarak planlamak, kuyrukları yok sayan bir plana göre ortalama 3,4 dk, en çok 21,7 dk kazandırır |
| `temperature` | Karaman → İstanbul: 20 °C'de 8 sa 07 dk; −15 °C'de ön ısıtmalı bataryayla 8 sa 37 dk, ön ısıtma olmadan 10 sa 24 dk |
| `robustness` | İzmir → Erzurum: P(rezerv aşımı) güvenlik katsayısı 1,0'da %56, 1,1'de %9,7 (+21 dk), 1,2'de %0,4 (+36 dk) |
| `tariffs` | maliyet-optimal plan en hızlı plandan 6,3 dk uzun sürer ama 76,73 TRY (%7,6) daha az öder |
| `vehicles` | on bir hazır araç profiliyle Karaman → İstanbul: 7 sa 50 dk (75 kWh'lik sedan) ile 10 sa 57 dk (27 kWh'lik şehir aracı) arası, LFP'li araçlar 8 sa 01 dk ile 8 sa 34 dk arası; yalnızca AC ile şarj olan bir araç 20 sa 35 dk |
| `scalability` | 40 000 düğümlü sentetik ülkelerde BLS-A\* ortalama 1,7 sn'de, CFP-A\* 2,5 sn'de yanıt veriyor |
| `real-data` | yaklaşık ağda ve OpenStreetMap verisinde altı yolculuk: OSM istasyonlarıyla optimum ortalama %7,0 daha uzun sürer, neredeyse tamamı daha yavaş sürüşten; BLS-A\* 4,0 sn'de, CFP-A\* 9,9 sn'de yanıt verir (medyan) |

On iki deneyin tüm bulguları, çıktıları ve süreleri:
[dokümantasyon](docs/index.md#experiments-and-figures).

## Doğruluğun denetlenmesi

Her planlayıcının her planı **bağımsız bir değerlendiricide** yeniden simüle
edilir; sabit tohumlu bir **çapraz doğrulama paketi** on bir değişmezi kaba
kuvvete, analitik referans çözümlere, metamorfik bağıntılara ve yerel aramaya
karşı, rastgele şarj tarifeleri altında da sınar
([`tests/fuzz_report.md`](tests/fuzz_report.md)). Toplam 881 test vardır;
bunların 744'ü hızlı katmandadır ve CI'da Python 3.10-3.13 üzerinde
çalıştırılır ([ayrıntılar](docs/algorithms.md#8-how-correctness-is-verified)):

```bash
python -m pytest -m "not slow"   # hızlı katman, birkaç dakika
```

## Dokümantasyon

- [Rehber](docs/guide.tr.md) ([English](docs/guide.md)) — algoritmalar adım
  adım, konuya yeni başlayanlar için; sonunda "hangi planlayıcıyı
  kullanmalıyım?", her mola için asgari şarj düzeyi ve günlük kullanım modu
- [Model](docs/model.md) — tüm denklemler, onları gerçekleyen modüllerle
  birlikte; araçlar, DC sınırları ve konnektörler §10'da
- [Gerçek veriyle vaka çalışması](docs/real-data.tr.md) ([English](docs/real-data.md)) —
  OpenStreetMap üzerinde Türkiye: kaynaklar, verinin hazırlanışı, doğrulama,
  şarj kapsamı, fiyatlar ve 2026 yönetmeliği, sonuçlar, canlı ve operatör
  verisi (Şarj@TR, OCPI, Open Charge Map), sınırlar
- [Algoritmalar ve ispatlar](docs/algorithms.md) — doğruluk gerekçeleri ve kaynakça
- [Dokümantasyon dizini](docs/index.md) — komut satırı, Python API'si,
  görüntüleyici, canlı sunucu, deneyler, senaryolar ve veriler, tarifeler,
  SSS, yol haritası
- [Web sitesi](https://aseloth99.github.io/evroute/?lang=tr): tarayıcınızda çalışan canlı planlayıcı ve hazır
  [örnekler](https://aseloth99.github.io/evroute/examples/?lang=tr) (görüntüleyici, tarife görüntüleyicisi, gerçek veri, sonuç paneli)
- [v1.0.0 sürüm notları](docs/release-notes-v1.0.0.md#türkçe) — bu sürümün içeriği
- [Katkıda bulunma](CONTRIBUTING.md) — kurulum, proje yapısı, temel kurallar

Rehber, gerçek veri vaka çalışması ve sürüm notları dışındaki belgeler
İngilizcedir.

## Atıf

EVRoute araştırmanıza katkı sağladıysa lütfen atıf yapın
([`CITATION.cff`](CITATION.cff)):

```bibtex
@software{acik_evroute_2026,
  author  = {Açık, Mustafa},
  title   = {{EVRoute}: exact, battery-, queue- and degradation-aware route planning
             for electric vehicles with charging stations},
  version = {1.0.0},
  year    = {2026},
  url     = {https://github.com/Aseloth99/evroute},
  license = {MIT (code), ODbL-1.0 (bundled real data)}
}
```

## Lisans

[MIT](LICENSE) © 2026 Mustafa Açık, Karamanoğlu Mehmetbey Üniversitesi,
Bilgisayar Mühendisliği Bölümü.

`src/evroute/data/osm_turkey/` içindeki Türkiye gerçek veri ağı kod değil,
veridir: yol ağı, yerleşimler ve şarj istasyonları ©
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
[ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) lisansıyla;
yükseklikler SRTM (NASA/USGS, kamu malı). Ayrıntılar:
[`DATA_LICENSE.md`](src/evroute/data/osm_turkey/DATA_LICENSE.md).

## Teşekkürler

- Algoritmalar; Artmeier vd. (2010), Storandt & Funke (2012), Baum, Dibbelt,
  Gemsa, Wagner & Zündorf (2015, 2019), Baum, Dibbelt, Wagner & Zündorf
  (2020), Montoya vd. (2017), Martins
  (1984) ile Mandow & Pérez de la Cruz (2010) çalışmalarına dayanır; tam
  kaynakça [`docs/algorithms.md`](docs/algorithms.md#references) dosyasındadır.
- Yaklaşık Türkiye vaka çalışmasındaki yer koordinatları ve rakımlar,
  genel coğrafya bilgisinden yer adları sözlüğü (gazetteer) düzeyinde
  derlenmiştir; hiçbir tescilli harita ya da şarj ağı verisi
  içerilmemiştir.
- Gerçek veriyle vaka çalışması
  [OpenStreetMap](https://www.openstreetmap.org/copyright) (© OpenStreetMap
  contributors, ODbL 1.0), AWS Terrain Tiles üzerinden SRTM yükseklikleri
  (NASA/USGS), KGM mesafe tablosu, EPDK şarj istatistikleri ve
  operatörlerin yayımladığı fiyatlarla hazırlanmıştır
  ([kaynaklar](docs/real-data.tr.md#kaynaklar)). İsteğe bağlı OSM
  yükleyicisi, indirmeyi seçtiğinizde
  [OSMnx](https://github.com/gboeing/osmnx) ve OpenStreetMap verilerini
  kullanır.
