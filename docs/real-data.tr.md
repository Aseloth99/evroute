# Gerçek veriyle vaka çalışması: OpenStreetMap üzerinde Türkiye

[English](real-data.md) · **Türkçe**

EVRoute, gerçek verilerle hazırlanmış ikinci bir Türkiye vaka çalışmasıyla
gelir: SRTM yükseklikleriyle Türkiye'nin OpenStreetMap (OSM) yol ağı,
OSM'de işaretlenmiş halka açık şarj istasyonları ve şarj operatörlerinin
yayımladığı liste fiyatları. Coğrafyası yer adları sözlüğü (gazetteer)
düzeyinde olan ve 290 şarj istasyonu sentetik olan yaklaşık vaka
çalışmasını ([`index.md`](index.md#turkey-case-study)) tamamlar ve şu
soruyu sorar: 35 kat daha büyük gerçek bir ağda, gerçekten haritalanmış şarj
noktalarıyla ne değişir?

> [!IMPORTANT]
> **Yol ağı, yerleşimler ve şarj istasyonları © OpenStreetMap contributors,
> Open Database License (ODbL 1.0,
> [openstreetmap.org/copyright](https://www.openstreetmap.org/copyright))
> lisansıyla.** Yükseklikler: SRTM, NASA/USGS, kamu malı. Paketteki
> tablolar OpenStreetMap'ten türetilmiş bir veritabanıdır; EVRoute kodu MIT
> lisanslı kalır ([lisanslar](#lisanslar)). Araştırma verisidir,
> **navigasyon için uygun değildir**: hızlar yasal sınırlardır, OSM şarj
> noktalarının yalnızca küçük bir kısmını bilir, fiyatlar belirli bir tarihe
> aittir ve kuyruklar varsayımdır.

Bu sayfada: [bir bakışta](#bir-bakışta) · [deneyin](#deneyin) ·
[kaynaklar](#kaynaklar) · [ağ nasıl oluşturuldu](#ağ-nasıl-oluşturuldu) ·
[doğrulama](#kgmnin-resmî-mesafeleriyle-doğrulama) ·
[kapsam](#şarj-kapsamı-osm-ve-epdk) ·
[istasyon kümeleri](#üç-istasyon-kümesi-osm-synthetic-hybrid) ·
[fiyatlar ve 2026 yönetmeliği](#fiyatlar-ve-mart-2026-yönetmeliği) ·
[kuyruklar](#kuyruklar-varsayımlar) · [sonuçlar](#sonuçlar-real-data-deneyi) ·
[canlı ve operatör verisi](#canlı-ve-operatör-verisi) ·
[sınırlar](#sınırlar) · [yeniden oluşturma](#veriyi-yeniden-oluşturmak) ·
[lisanslar](#lisanslar)

## Bir bakışta

| | kaynağı | gerçek mi, varsayım mı |
|---|---|---|
| yollar | 2026-10-27 tarihli OSM özütü: otoyol, ana yol (trunk), birincil yol (primary) ve bunların bağlantı yolları; ayrıca boşlukları kapatan 263 km ikincil yol | gerçek |
| hızlar | OSM `maxspeed` etiketi, yoksa Karayolları Trafik Yönetmeliği'ndeki yasal varsayılan | yasal sınırlar, serbest akış, trafik yok |
| yükseklikler | her düğümde SRTM 1 açı saniyesi | ölçülmüş (ölçüm 2000 yılından) |
| şarj istasyonları | OSM'deki 711 halka açık istasyon (618 AC, 93 DC) | konumlar gerçek; güçlerin %64'ü ve soket sayılarının %44'ü varsayılan |
| fiyatlar | operatörlerin kendi sayfalarındaki liste fiyatları, 2026-10-27 (10 operatör, 711 istasyonun 237'si) | gerçek, belirli bir tarihe ait; diğer istasyonlar için varsayılanlar |
| kuyruklar | EPDK istatistiklerinden ulusal ortalamalarla Erlang-C | **varsayım** |
| tarife | nötr: her istasyonda kWh başına tek fiyat | 2026 yönetmeliğiyle uyumlu |
| araç | varsayılan olarak `togg_t10x_lr` hazır profili | kamuya açık yaklaşık değerler |

Ağda 44 673 düğüm ve 70 886 yönlü kenar vardır (güçlü bağlantılı); 75 393
km yolu kapsar: 6 511 km otoyol, 45 883 km ana yol, 18 261 km birincil
yol, 4 472 km bağlantı yolu, 263 km ikincil yol ve 3 km ikincil bağlantı
yolu. Yükseklikler −3 m ile 3 076 m arasındadır. Düğümler yaklaşık ağla
aynı harita izdüşümünü kullanır; ikisi aynı harita çerçevesini paylaşır. Yükleyici
[`evroute.generators.osm_turkey`](../src/evroute/generators/osm_turkey.py)
modülüdür; dosyalar, sütunları ve kaynakları
[`data/osm_turkey/README.md`](../src/evroute/data/osm_turkey/README.md)
dosyasında anlatılır.

## Deneyin

Paketteki veri için ek bir paket gerekmez: ağ yaklaşık yarım saniyede
yüklenir ve süreç boyunca önbellekte kalır.

```bash
evroute scenarios                                            # turkey-osm-* hazır senaryoları diğerleriyle birlikte listelenir
evroute plan turkey-osm-karaman-istanbul                     # OSM istasyonları, BLS-A*: yaklaşık 5 sn
evroute plan turkey-osm-karaman-istanbul --stations hybrid   # + sentetik DC boşluk doldurucular
evroute viz turkey-osm-karaman-istanbul --algorithms bls-astar,cfp-astar -o osm.html
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

İstasyon adları, OSM'de girildiği hâliyle `name` etiketleridir. Aynı
yolculuk yaklaşık ağda 8 sa 07 dk sürer, bunun 7 sa 19 dk'sı sürüştür
([README](../README.tr.md#örnek-karaman--i̇stanbul)); OSM yollarında
yalnızca sürüş 8 sa 37 dk tutar.

| hazır senaryo | yolculuk | istasyonlar |
|---|---|---|
| `turkey-osm-karaman-istanbul` | OSM yollarında amiral yolculuk, 08:00'de %80 şarjla | OSM |
| `turkey-osm-karaman-istanbul-dc85` | aynısı, şarj %85 ile sınırlı (operatörler DC oturumunu orada bitirebilir) | OSM |
| `turkey-osm-antalya-ankara` | Akdeniz'den Toroslar üzerinden | OSM |
| `turkey-osm-istanbul-ankara` | en yoğun koridor, 14:30'da kalkış | OSM |
| `turkey-osm-ankara-adana` | Gülek Boğazı üzerinden, 15:00'te kalkış | OSM |
| `turkey-osm-karaman-mersin` | Sertavul geçidi ya da Pozantı üzerinden otoyol | OSM |
| `turkey-osm-izmir-erzurum` | uzun batı-doğu geçişi | hibrit |
| `turkey-osm-karaman-konya` | %30 şarjla Karaman → Konya (doğrudan 119 km): yolda DC şarj noktası yok, bu yüzden optimum bir AC şarj noktasına sapar (239 km, yaklaşık 5 sa); her planlayıcı için yeterince küçük | OSM |

`turkey-osm-karaman-konya` dışındaki her hazır senaryonun başlangıcı,
varışı, aracı, şarjı, kalkış saati ve sıcaklığı, yaklaşık ağdaki
karşılığıyla aynıdır.
`--stations osm|synthetic|hybrid` herhangi birinin
[istasyon kümesini](#üç-istasyon-kümesi-osm-synthetic-hybrid) seçer;
`--from` / `--to`, 1 005 OSM yerleşiminden herhangi birinin adını kabul
eder (büyük/küçük harf ve Türkçe karakterler fark etmez). 20 000'den fazla
düğümlü bir ağda `--algorithms all` (`compare`, `viz`, `figures`),
A\* potansiyeli olmadan orada milyonlarca etiket kesinleştiren `bls`, `cfp`
ve `pareto`'yu ve orada dakikalar, hatta saatler süren `station-graph`'ı
dışarıda bırakır; yine de çalıştırmak için adlarını yazın. Canlı sunucu bu
hazır senaryolarda başta yalnızca BLS-A\* ve CFP-A\*'ı işaretler.

Python'da:

```python
from evroute import plan
from evroute.generators.osm_turkey import osm_turkey_scenario

sc = osm_turkey_scenario("Karaman", "İstanbul")                      # OSM istasyonları (varsayılan)
print(plan(sc, "bls-astar"))
print(plan(sc, "cfp-astar"))                                         # sürekli şarj: yaklaşık yarım dakika
hybrid = osm_turkey_scenario("izmir", "ERZURUM", stations="hybrid")  # adlar: büyük/küçük harf ve Türkçe karakter serbest
print(plan(hybrid, "bls-astar"))                                     # %80 şarj, 08:00 (hazır senaryo 06:00'da %90 ile çıkar)
print(sc.meta["station_info"]["0"])                                  # 0 numaralı istasyonun verileri nereden geliyor
```

```text
[bls-astar] 9 h 14 min | 751.81 km | 2 stop(s) | arrive 10.0% | 999.92 TRY
[cfp-astar] 9 h 14 min | 755.01 km | 2 stop(s) | arrive 10.0% | 1021.20 TRY
[bls-astar] 17 h 44 min | 1465.62 km | 6 stop(s) | arrive 10.5% | 3233.54 TRY
{'osm': 'node/3885275177', 'price': 'default', 'power': 'default:ac', 'plugs': 'default', 'snap_m': 35.2}
```

`get_scenario("turkey-osm-karaman-istanbul", stations="hybrid")` hazır
senaryoları istenen istasyon kümesiyle verir. `osm_turkey_meta()`,
`osm_turkey_prices()` ve `osm_turkey_validation()` sırasıyla `meta.json`,
`prices.json` ve `validation.csv` dosyalarını döndürür; her senaryo atıf
metnini `scenario.meta["attribution"]` içinde taşır; görüntüleyici, statik
harita ve arama uzayı grafikleri, sonuç paneli ve deneyin LaTeX tablo
başlığı bu atfı yazar.

## Kaynaklar

| kaynak | sürüm | kullanıldığı yer |
|---|---|---|
| Türkiye OpenStreetMap özütü, [download.openstreetmap.fr](https://download.openstreetmap.fr/extracts/europe/turkey.osm.pbf) | çoğaltma zaman damgası 2026-10-27T02:09:23Z (sıra 7346288), md5 `b03e7851f3312d6f5b552985c10e31db` | yollar, şarj istasyonları, yerleşimler |
| SRTM 1 açı saniyesi, AWS Terrain Tiles'ın "skadi" karoları | 111 karo | yükseklikler |
| KGM (Karayolları Genel Müdürlüğü), *İller arası mesafeler* ([`ilmesafe.xlsx`](https://www.kgm.gov.tr/SiteCollectionDocuments/KGMdocuments/Root/Uzakliklar/ilmesafe.xlsx)) | 2026-03-03 tarihli tablo | yalnızca doğrulama |
| EPDK, *Şarj Hizmeti Piyasası Aylık İstatistikleri* ([istatistik sayfası](https://www.epdk.gov.tr/Detay/Icerik/3-0-222/enerji-donusumusarj-hizmeti-piyasasi--istatistik)) | Eylül 2026, yayım 16.10.2026 | kapsam, kuyruk varsayımları |
| operatörlerin kendi fiyat sayfaları | erişim 2026-10-27 | `prices.json` |
| Resmî Gazete 23.03.2026, Sayı 33202: Şarj Hizmeti Yönetmeliği'nde değişiklik ([metin](https://www.resmigazete.gov.tr/eskiler/2026/03/20260323-4.htm)) | Mart 2026'dan beri yürürlükte | fiyat kuralları; işletmecilerin veri yükümlülükleri ([canlı veri](#canlı-ve-operatör-verisi)) |
| Karayolları Trafik Yönetmeliği, Md. 100 | | varsayılan hızlar |

## Ağ nasıl oluşturuldu

`tools/build_osm_turkey.py`, kaynakları şu adımlarla paketteki tablolara
dönüştürür.

**Yollar.** Türkiye'nin ulusal sınırı içinde `highway=motorway`, `trunk`
ya da `primary` etiketli her OSM yolu ve bunların `*_link` bağlantı
yolları. Otomobile kapalı yollar (`access=no`, `private`), alanlar ve
yönü değişen şeritler atılır; tek yönlülük `oneway` etiketine uyar
(otoyollar ve dönel kavşaklar OSM kuralı gereği tek yönlüdür). OSM
Türkiye'de devlet yolları (D yolları) trunk ya da primary olarak
işaretlidir; `secondary` çoğunlukla il yolları demektir. Bu yüzden ikincil
yollar yalnızca ana ağın ihtiyaç duyduğu yerde girer: 41,7 km ana ağın
kopuk kalmış 10 parçasını yeniden bağlar, 221,8 km ise 24 il merkezi
çiftini birbirinden koparan ya da aralarında %15'ten uzun dolambaçlara yol
açan boşlukları kapatır. Uzunluklar WGS84 elipsoidi üzerindeki jeodezik
uzunluklardır.

**Hızlar.** Varsa `maxspeed` etiketi. Etiket otoyol kilometrelerinin
%5'inde, ana yolların %59'unda ve birincil yolların %70'inde yoktur; orada
otomobiller için yasal varsayılan kullanılır (Karayolları Trafik
Yönetmeliği, Md. 100): yerleşim yerlerinde 50 km/sa, bölünmemiş şehirlerarası
yollarda 90 km/sa, bölünmüş yollarda 110 km/sa ve otoyollarda 120 km/sa.
Bağlantı yolları kendi sınıflarındaki etiketli bağlantı yollarının
uzunluk ağırlıklı medyanını alır (otoyol ve ana yol bağlantılarında
50 km/sa, birincil yol bağlantılarında 30 km/sa), dönel kavşaklar 30 km/sa.
OSM bir yerleşimin nerede başladığını söylemez; bu yüzden 500 m içinde
yerel sokaklarla (residential, unclassified, tertiary, living street) en
az 5 kavşağı olan bir yol **yerleşim yeri** sayılır; eşik, etiketli
yollar üzerinde ayarlanmıştır. Etiketi olan yollarda varsayılan kural
etiketli sınırı ortalama 12-14 km/sa ıskalar; otoyollarda varsayılan,
etiketli sınırların ortalama 11,6 km/sa altındadır. Tüm hızlar serbest akış
hızlarıdır: veride trafik yoktur.

**Araziyi koruyan sadeleştirme.** Kavşaksız OSM düğüm zincirleri tek
kenarlara indirgenir; ama her iki yanında en az 20 m yükselti farkı olan
her yerel yükselti uç noktasında ve hiçbir kenar 5 km'den uzun olmayacak
biçimde bölünür: geçitler ve vadiler düğüm olarak kalır, enerji modeli
her tırmanışı ve inişi görür. İndirgenmiş bir kenarın hızı, parçalarının
uzunluk ağırlıklı harmonik ortalamasıdır; bu, serbest akış yolculuk
süresini birebir korur. Ardından en büyük güçlü bağlantılı bileşen
tutulur (72 457 düğüm; küçük parçalardaki 6 940 düğüm atılır), 30 m'den
kısa kenarların uç düğümleri birleştirilir (kavşak birleştirme, 53 577
düğüm) ve düz geçiş noktasına dönüşen eski kavşaklar aynı arazi
kurallarıyla yeniden indirgenir: 44 673 düğüm, 70 886 kenar; kenarlar 25 m
ile 5 km arası (medyan 518 m). Her kenar, uç düğümleri arasındaki düz 3
boyutlu çizgi kadar uzundur (EVRoute eğimi bundan türetir); daha kısa
kenarlar bu uzunluğa getirilmiştir.

**Yükseklikler.** Her düğümde çift doğrusal bir SRTM örneği. Köprü ve
tünellerin içinde yükseklik, iki ağız arasında doğrusal olarak
ara değerlenir. %15'ten dik eğimler (viyadüklerdeki kavşaklarda SRTM
hataları ve Yusufeli Barajı'nın yer değiştirme yolları gibi ölçümden sonra
yapılmış yollar) sapan düğüm kaydırılarak kırpılır: 911 düğüm, medyan
kayma 5,1 m, en büyüğü 165 m.

**Şarj istasyonları.** Özütte `amenity=charging_station` etiketli 773
nesne vardır. 26'sı Türkiye dışında, 14'ü özel (`access=private` ya da
`no`, `motorcar=no`, kullanım dışı), 22'si ağa 3 km'den uzaktır; kalan
**711** istasyon en yakın ağ düğümüne bağlanır (medyan uzaklık 400 m). Bir
istasyon DC soket türü (CCS, CHAdeMO, Tesla Supercharger, ...), 22 kW'ın
üzerinde etiketli istasyon gücü ya da bunu söyleyen bir adı
("Supercharger", "DC", "hızlı", ...) varsa DC, yoksa AC'dir. Gücü kendi
türündeki en büyük etiketli çıkıştır; yoksa gerilim × akım; o da yoksa
bir **varsayılan**: AC 22 kW, DC 120 kW, Tesla Supercharger'da 250 kW (452
istasyon, %64). Soket sayısı `capacity` etiketidir; yoksa soket
sayılarının toplamı, o da yoksa **1** (316 istasyon, %44); bu yüzden soket
sayıları alt sınırdır. Her istasyon OSM nesne numarasını ve her değerin
kaynağını saklar.

**Yerleşimler.** OSM'deki 1 005 şehir ve kasaba; aralarında 81 il merkezi
(OSM'deki idari merkezleri) var; hepsi en yakın ağ düğümüne bağlanır (il
merkezleri 2,24 km içinde). Haritayı etiketler ve `--from`/`--to`
adlarını çözerler; önce il merkezleri gelir, bu yüzden `"Ereğli"` daha
büyük olan Ereğli'dir (Konya).

## KGM'nin resmî mesafeleriyle doğrulama

KGM'nin 2026-03-03 tarihli tablosu 81 il merkezinin her birinden
diğerlerine olan yol mesafesini verir. Ağda bir çiftin mesafesi, iki yöndeki en kısa yol
mesafesinin ortalaması artı iki merkezin ağa bağlanma uzaklıklarıdır.
**3 240 çiftin** tamamında:

| ölçü | değer |
|---|---:|
| ortalama mutlak hata | %2,52 |
| medyan mutlak hata | %1,45 |
| 90. yüzdelik | %5,12 |
| %5 içindeki / %10 içindeki çiftler | %89,5 / %97,2 |
| ortalama işaretli hata | −%0,82 |
| ücretli yollar olmadan (2 932 çift): ortalama mutlak / işaretli hata | %2,23 / −%0,17 |

KGM tablosu ücretsiz yolları izler: ücretli yollardan kaçınınca ağın
daha kısa mesafelere doğru sapması neredeyse kaybolur. `validation.csv`
aralarında deneyin altı yolculuğunun da bulunduğu 20 çifti listeler:

| çift | KGM [km] | ağ A → B / B → A [km] | hata | ücretli yollar olmadan |
|---|---:|---:|---:|---:|
| Karaman - İstanbul | 789 | 731,9 / 730,0 | −%7,4 | – |
| Antalya - Ankara | 542 | 509,2 / 509,4 | −%6,0 | −%6,0 |
| İzmir - Erzurum | 1 471 | 1 451,0 / 1 451,7 | −%1,3 | −%1,3 |
| İstanbul - Ankara | 453 | 438,7 / 438,1 | −%3,2 | – |
| Ankara - Adana | 491 | 472,7 / 474,1 | −%3,6 | −%3,5 |
| Karaman - Mersin | 233 | 237,1 / 234,8 | +%1,2 | +%1,2 |
| Karaman - Konya | 118 | 120,8 / 113,5 | −%0,7 | −%0,7 |
| İstanbul - İzmir | 566 | 456,1 / 461,9 | −%18,9 | – |
| Ankara - Samsun | 412 | 403,6 / 407,3 | −%1,6 | −%1,6 |
| Trabzon - Erzurum | 294 | 284,3 / 283,7 | −%3,4 | −%3,4 |
| Gaziantep - Şanlıurfa | 139 | 145,4 / 145,2 | +%4,5 | +%4,5 |
| Diyarbakır - Van | 363 | 368,0 / 368,4 | +%1,4 | +%1,4 |
| Kayseri - Sivas | 202 | 196,7 / 196,3 | −%2,7 | −%2,7 |
| Edirne - İstanbul | 232 | 237,8 / 236,0 | +%2,1 | +%2,1 |
| Bursa - Eskişehir | 152 | 150,4 / 150,6 | −%1,0 | −%1,0 |
| Konya - Antalya | 271 | 268,2 / 273,0 | −%0,2 | −%0,2 |
| Denizli - Muğla | 150 | 149,9 / 144,9 | −%1,7 | −%1,7 |
| Erzurum - Kars | 205 | 209,7 / 215,6 | +%3,7 | +%3,7 |
| Sinop - Kastamonu | 181 | 182,8 / 182,7 | +%1,0 | +%1,0 |
| Hakkâri - Van | 195 | 177,6 / 195,0 | −%4,5 | −%4,5 |

"–": ağda Boğaz'ı ücretsiz geçen bir yol yoktur; İstanbul merkezi
Anadolu'ya ücretli bir yol kullanmadan ulaşamaz.

**Aykırı değerler** yöntemden değil, haritadan gelir:

- **İstanbul - İzmir, −%18,9.** En kısa rota İzmit Körfezi'ni ücretli
  Osmangazi Köprüsü'nden geçer; KGM tablosu bu köprüyü kullanmaz.
- **Tunceli'ye** (Elâzığ - Tunceli 140 km yerine 294,9 km, +%110,7)
  yalnızca il yollarından ulaşılır; OSM bunları ikincil yol olarak işaretler
  ve ağ çoğunu dışarıda bırakır.
- **Yalova** (Kocaeli - Yalova +%104,3): körfezin güney kıyısındaki yol OSM'de
  ana yol değildir.
- **Hakkâri** (Hakkâri - Şırnak +%81,2): Başkale yakınında D-975'in
  kuzeye giden şeridi OSM'de eksiktir; bu yüzden Hakkâri'den kuzeye çıkan
  rotalar ağın en yüksek düğümü olan 3 076 m'lik bir geçitten il yollarını
  kullanır. Van → Hakkâri, 195,0 km, KGM tablosuyla aynıdır.

Bunlar OSM'deki boşluklardır; veri hazırlama betiği bunları raporlar
(`meta.json` içindeki `validation`) ve elle yamamaz.

## Şarj kapsamı: OSM ve EPDK

EPDK, Eylül 2026'da ticari şarj hizmeti verilen **48 971 soket** saydı
(27 664 AC, 21 307 DC; 3 875 MW). 711 OSM istasyonunda 909 AC ve 405 DC
soket var; yani **OSM, AC soketlerin yaklaşık %3,3'ünü, DC soketlerin
%1,9'unu biliyor**. İki oran da kabadır: OSM soket sayıları alt sınırdır;
EPDK sayısı erişimi kısıtlı soketleri de içerir, OSM kümesi ise yalnızca
halka açık istasyonları tutar. **38° doğu boylamının doğusunda hiç DC
istasyon yok**: en doğudaki 36,4° D'dedir ve 34° D'nin doğusunda yalnızca
3 tane vardır; buna karşılık 38° D'nin doğusunda 47 AC istasyon
işaretlidir.

En çok işaretlenen operatör Sharz.net'tir (129 istasyon, çoğu otel ve
işletmelerde AC); ardından ZES (91) ve Trugo (84) gelir; 108 istasyonun
operatör etiketi yoktur. EPDK istasyon koordinatlarını açık veri olarak
yayımlamaz (Şarj@TR uygulaması istasyonları gösterir, lisans sicili
yalnızca adres verir); konumları veren açık kaynak OSM'dir
([canlı ve operatör verisi](#canlı-ve-operatör-verisi)). Kapsam bu vaka
çalışmasının asıl sınırıdır: yalnızca OSM istasyonlarıyla uzun doğu
yolculuklarında kullanılacak hızlı şarj noktası yoktur.

## Üç istasyon kümesi: osm, synthetic, hybrid

`stations=` (komut satırında `--stations`) şarj noktalarını seçer:

- **`osm`** (varsayılan): yukarıdaki 711 halka açık OSM istasyonu,
  operatörlerin liste fiyatlarıyla
  ([aşağıda](#fiyatlar-ve-mart-2026-yönetmeliği)) ve
  [Kuyruklar](#kuyruklar-varsayımlar) bölümündeki varsayımlarla.
- **`synthetic`**: yaklaşık vaka çalışmasının 290 sentetik istasyonu (aynı
  tohum, güç, soket, fiyat ve ayarlanmış kuyruklar); her biri, yaklaşık
  ağdaki yolunun sınıfıyla eşleşen en yakın OSM ağ düğümüne, en yakın ana
  yol düğümü 5 km'den fazla daha yakınsa ona taşınır (modellenmiş bir yol,
  OSM'de aynı sınıftaki yoldan uzakta kalabilir); bir şehir istasyonu aynı
  adlı OSM yerleşimine gider. Aynı istasyonlar, gerçek yollar: yaklaşık vaka
  çalışmasından tek fark ağdır.
- **`hybrid`**: OSM istasyonları ve bir ana yolun (otoyol, ana yol ya da
  birincil yol) her DC istasyona yol üzerinden 75 km'den uzak olduğu her
  yerde **sentetik DC boşluk doldurucular**. En uzak boşluktan başlayarak
  her doldurucu, en uzak kapsanmamış düğümün 20 km yakınındaki adlı bir
  yerleşime (ya da o düğüme) konur; bu, her ana yol düğümü kapsanana kadar
  sürer: tohum 0 ile 373 doldurucu, yani 466'sı DC olmak üzere 1 084
  istasyon. Bir doldurucunun gücü 120 ya da 150 kW, soket sayısı 2 ya da
  4'tür (tohumlu) ve varsayılan DC fiyatını alır. Doldurucular gerçek
  istasyon değildir: `operator="synthetic"` ve "Synthetic DC gap-filler"
  ile başlayan bir adla işaretlidirler. Hibrit küme "bugün ne var?"
  sorusunu değil, "DC ağında boşluk olmasaydı ne olurdu?" sorusunu
  yanıtlar.

`scenario.meta["station_info"]` her istasyon için verilerinin nereden
geldiğini kaydeder: OSM nesnesi, fiyatın kaynağı (`operator` ya da
`default`), güç ve soket kaynağı ya da `synthetic` / `gap-filler`.

**Konnektörler.** OSM'deki `socket:*` etiketleri istasyonlara konnektör
standartlarını verir (`type2` → Type 2, `type2_combo` → CCS2, `chademo` →
CHAdeMO, `type1_combo` → CCS1, `type1` → Type 1). 711 istasyondan yalnızca
294'ünde soket etiketi vardır ve etiketler her zaman doğru değildir (Kuzey
Amerika standardı CCS1'i belirten `type1_combo`, Türkiye'de DC şarj Avrupa
standardı CCS2'yi kullandığı hâlde 7 çok standartlı şarj cihazında
etiketlidir). Bu yüzden etiketin olmaması soketin olmadığı anlamına gelmez:
her OSM istasyonu türünün varsayılan konnektörünü (AC'de Type 2, DC'de
CCS2) ve etiketli soketlerini sunar. Type 2/CCS2'li bir araç - önceki
sürümlerin bütün hazır araç profilleri - 711 istasyonun hepsini
kullanabilir, dolayısıyla sonuçları değişmez; CHAdeMO'lu bir araç
(`hatch40_chademo`) DC gücü yalnızca `chademo` etiketli 12 istasyonda, DC
girişi olmayan bir araç (`city23_ac`) ise yalnızca 618 AC istasyonda bulur
([model §10](model.md#10-vehicles-cell-chemistry-dc-power-limit-connectors)).

## Fiyatlar ve Mart 2026 yönetmeliği

`prices.json`, 2026-10-27 tarihli, elle derlenmiş bir anlık görüntüdür:
Türkiye'deki operatörlerin TRY/kWh cinsinden KDV dahil liste fiyatları,
her biri okunduğu sayfayla birlikte. Bir istasyon, operatörünün **liste
fiyatını** ancak operatör bunu kendi sayfasında yayımlıyorsa alır;
operatör bir aralık veriyorsa üst ucu, çünkü 2026 kurallarına göre daha
düşük fiyatlar konum ya da saat indirimidir:

| operatör | AC [TRY/kWh] | DC [TRY/kWh] |
|---|---:|---:|
| Trugo | 9,95 | 12,00-14,98 |
| ZES | 10,99 | 12,99-16,49 |
| WAT Mobilite | 9,99-10,99 | 11,99-14,49 |
| Astor Şarj | 9,89 | 13,99 |
| En Yakıt | - (yalnızca DC) | 14,90 |
| Oncharge | 9,99 | 13,50 |
| Neva Şarj | 9,90 | 13,90 |
| Efish | 10,99 | 15,99 |
| Aksa Şarj | 9,90-10,90 | 11,99-13,99 |
| Beefull | 9,90 | 13,99 |

Diğer her istasyon (operatör etiketi olmayanlar ya da sayfası okunamayan
bir operatörün istasyonları; basın ve fiyat derleyici sitelerdeki rakamlar
`prices.json` içinde kayıtlıdır ama kullanılmaz) **varsayılanı** alır:
**AC 9,99 TRY/kWh, DC 14,24 TRY/kWh**, yani doğrulanmış liste fiyatlarının
medyanları. 711 OSM istasyonunun 237'si operatör fiyatı, 474'ü varsayılanı
alır. İşgal (bekleme) ücretleri, üyelik fiyatları ve kampanyalar
modellenmez.

**Şarj Hizmeti Yönetmeliği'ndeki değişiklik** (Resmî Gazete 23.03.2026,
Sayı 33202) bu fiyatların uyduğu kuralları koyar:

| kural | EVRoute'ta |
|---|---|
| fiyat verilen kWh başına; bağlantı, başlatma ya da ekipman ücreti yok | kWh başına bir fiyat; molanın hazırlık süresi para değil zaman tutar |
| halka açık istasyonlarda bir operatörün **bir AC ve bir DC fiyatı** olur: şarj gücüne ya da doluluk oranına göre kademe yok | istasyon başına tek fiyat; `DEMO_TARIFF`'in %80 kademesi yalnızca örnekleme amaçlı bir seçenektir ve `turkey-osm-*` hazır senaryoları nötr `Tariff()`'i korur |
| konuma ve **günün saatine** göre indirim serbest; oturumun **başladığı** saatin fiyatı uygulanır | `peak_hours` içeren bir `Tariff`'in kuyruğa giriş kuralı ([`model.md` §5](model.md#5-charging-tariffs)); EVRoute fiyatı aracın kuyruğa girdiği anda sabitler, kuyruk yoksa bu an oturumun başlangıcıdır |
| halka açık fiyat, üyelik sözleşmeli kullanıcılara uygulanan en düşük fiyatın en çok 1,25 katı | modellenmez |
| operatör, önceden bildirerek bir DC oturumunu **%85 doluluk oranında** bitirebilir | `max_charge_soc=0.85` (`-dc85` hazır senaryosu; basitleştirme olarak AC molalarını da sınırlar) |

Karaman → İstanbul'da %85 sınırı hiçbir şeyi değiştirmez:
`turkey-osm-karaman-istanbul-dc85` için BLS-A\* planı yukarıdakiyle
aynıdır ve en çok %75'e kadar şarj eder.

## Kuyruklar (varsayımlar)

Her OSM istasyonu ve boşluk doldurucu, EPDK'nin Eylül 2026
istatistiklerinden türetilmiş ulusal ortalamalarla bir Erlang-C kuyruğudur
([`model.md` §6](model.md#6-queues); `QUEUE_MODEL`, `DEMAND_PROFILE`):

| | DC | AC |
|---|---:|---:|
| soket başına günlük oturum | 3,7 | 1,0 |
| ortalama oturum | 36 dk | 159 dk |
| hazırlık (park etme, fişi takma, ödeme) | 180 sn | 120 sn |

Gün içindeki talep, şarj edilen enerjinin saatlik profilini izler (tepe
17:00-18:00). Bunlar **ölçüm değil, varsayımdır**: gerçek kuyruklar
istasyondan istasyona değişir; ulusal ortalamalar da yaklaşık vaka
çalışmasındaki en yoğun dinlenme tesislerinin ayarlanmış öğleden sonra
kuyruklarından çok daha hafiftir (`synthetic` kümesi onları korur). Kesin
optimumların kuyruk ve hazırlık süresi OSM istasyonlarıyla yolculuk başına
0-6 dk, yaklaşık ağda 5,8-24,6 dk'dır.

## Sonuçlar: real-data deneyi

`evroute experiments real-data` altı yolculuğu üç veri kümesinde planlar:
sentetik istasyonlarıyla yaklaşık ağ, OSM istasyonlarıyla OSM ağı ve hibrit
kümeyle OSM ağı. Kesin planlayıcılar BLS-A\* ve CFP-A\*'dır; greedy,
station graph ve genetic sezgiselleri yalnızca kesin bir rotanın
bulunduğu yerde çalışır ve açıkları aynı veri kümesindeki CFP-A\*'a göre
ölçülür. Tam koşu (87 plan), 4 çekirdekli 2,1 GHz'lik bir Xeon'da paralel
dört işçi süreçle yapıldı; çalışma süreleri bu yükü içerir.

Her yolculuğun sürekli optimumu (CFP-A\*):

| yolculuk | veri kümesi | yolculuk süresi | mesafe | mola | şarj | maliyet [TRY] | BLS-A\* | CFP-A\* |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Karaman → İstanbul | yaklaşık | 8 sa 07 dk | 747 km | 2 | 35,3 dk | 1 133,96 | 0,06 sn | 0,04 sn |
| | OSM | 9 sa 14 dk | 755 km | 2 | 32,7 dk | 1 021,20 | 7,00 sn | 31,22 sn |
| | hibrit | 8 sa 41 dk | 818 km | 3 | 56,9 dk | 1 634,51 | 1,72 sn | 3,19 sn |
| Antalya → Ankara | yaklaşık | 5 sa 52 dk | 526 km | 1 | 24,8 dk | 667,84 | 0,06 sn | 0,03 sn |
| | OSM | 6 sa 18 dk | 543 km | 1 | 16,3 dk | 561,45 | 1,79 sn | 3,07 sn |
| | hibrit | 6 sa 07 dk | 533 km | 2 | 21,3 dk | 691,33 | 0,79 sn | 0,87 sn |
| İstanbul → Ankara | yaklaşık | 4 sa 54 dk | 457 km | 2 | 26,2 dk | 837,92 | 0,06 sn | 0,02 sn |
| | OSM | 4 sa 35 dk | 449 km | 2 | 30,0 dk | 1 028,02 | 2,64 sn | 2,99 sn |
| | hibrit | 4 sa 35 dk | 449 km | 2 | 30,0 dk | 1 028,02 | 1,90 sn | 3,03 sn |
| Ankara → Adana | yaklaşık | 5 sa 07 dk | 508 km | 2 | 27,1 dk | 759,56 | 0,06 sn | 0,02 sn |
| | OSM | 5 sa 58 dk | 569 km | 2 | 39,4 dk | 1 111,24 | 5,33 sn | 16,69 sn |
| | hibrit | 4 sa 32 dk | 493 km | 2 | 30,0 dk | 1 056,19 | 0,82 sn | 0,99 sn |
| Karaman → Mersin | yaklaşık | 3 sa 03 dk | 273 km | 1 | 2,9 dk | 93,27 | 0,05 sn | 0,02 sn |
| | OSM | 3 sa 09 dk | 236 km | 0 | - | 0 | 0,81 sn | 0,62 sn |
| | hibrit | 2 sa 58 dk | 244 km | 1 | 2,5 dk | 88,47 | 0,82 sn | 0,78 sn |
| İzmir → Erzurum | yaklaşık | 17 sa 29 dk | 1 467 km | 4 | 103,8 dk | 3 239,96 | 0,12 sn | 0,10 sn |
| | OSM | çözülemedi | | | | | 58,66 sn | 160,19 sn |
| | hibrit | 17 sa 50 dk | 1 466 km | 6 | 103,0 dk | 3 329,31 | 10,20 sn | 24,83 sn |

Sayıların söyledikleri:

- **Kesin planlayıcılar OSM istasyonlarıyla 6 yolculuğun 5'inde, hibrit
  kümeyle 6'sında da rota bulur.** OSM istasyonlarıyla İzmir → Erzurum
  etiket sınırında durdu (BLS-A\* 4,59 milyon, CFP-A\* 1,81 milyon
  etiketten sonra): *çözülemedi*, ama olanaksız olduğu ispatlanmadı.
  Rotanın doğu yarısında hiç DC şarj noktası yok, yine de uygun bir rota
  var: greedy
  (`evroute plan turkey-osm-izmir-erzurum --stations osm -a greedy`,
  yaklaşık 3 dakika) 15 molalı, 46 sa 34 dk'lık bir rota bulur; bunun
  24 sa 25 dk'sı AC istasyonlarda şarjdır.
- **OSM istasyonlarıyla optimum, ortalama %0,1 daha uzun mesafe için
  ortalama %7,0 daha uzun sürer.** Bunun neredeyse tamamı daha yavaş
  sürüştür (ortalama +32 dk; şarj, kuyruk ve hazırlık −5,7 dk): OSM hızları
  yasal sınırlardır. Para ortalama %11 daha azdır, ama dağılım geniştir:
  Karaman → Mersin gerçek yollarda hiç mola gerektirmez (273 km yerine
  236 km), Ankara → Adana ise yaklaşık ağdakinden %46 daha pahalıdır ve DC
  şarj noktalarına ulaşmak için hibrit kümeyle olduğundan 76 km fazla yol
  gider; boşluk doldurucular bu dolambacı kaldırır.
- **Hibrit kümeyle** optimum, yaklaşık ağa göre ortalama %1,2 daha hızlı
  ve %18 daha pahalıdır.
- **BLS-A\* her yolculukta ve her veri kümesinde sürekli optimumun %0,4
  yakınında kalır.**
- **Çalışma süreleri artar ama saniyeler düzeyinde kalır.** Yolculuklar
  üzerinden medyan, OSM istasyonlarıyla BLS-A\* için 4,0 sn, CFP-A\* için
  9,9 sn (hibrit: 1,3 sn ve 2,0 sn); 1 279 düğümlü yaklaşık ağda ise 60 ms
  ve 24 ms. Aynı makinede tek başına çalıştırıldığında Karaman → İstanbul
  3,8 sn (BLS-A\*) ve 23,3 sn (CFP-A\*) sürer.
- **Sezgiseller gerçek veride dağılır.** Optimuma göre ortalama açık;
  yaklaşık ağda → OSM istasyonlarıyla → hibrit kümeyle:

  | sezgisel | yaklaşık | OSM | hibrit |
  |---|---:|---:|---:|
  | greedy | %6,4 | %153,0 | %103,5 |
  | station graph | %14,9 | %11,9 | %14,9 |
  | genetic | %0,2 | %9,3 | %0,9 |

  AC istasyonlarda %100'e kadar şarj eden greedy, OSM verisinde yavaş AC
  şarj noktalarında saatlerce bekler (Karaman → İstanbul: 15,6 sa şarj,
  24 sa 18 dk'lık bir yolculuk). Station graph genişlettiği her istasyon
  için bütün ağ üzerinde bir etiket ağacı kurar (her biri 30-50 sn); OSM
  verisindeki 11 koşusunun 4'ünde 10 dakikadan uzun sürdü: Ankara → Adana
  (OSM) 33 dk, İstanbul → Ankara 54 dk (OSM) ve 57 dk (hibrit), İzmir →
  Erzurum (hibrit) 3 sa 46 dk. Greedy en çok 2,2 dk, genetic 2,7 dk sürdü.

**Süre bütçesi.** Tam koşu 6,8 saatlik planlama sürdü, bunun 6,4 saati
station graph'ta geçti. Bu yüzden deney, OSM veri kümelerindeki her
sezgisel koşuyu 10 dakika sonra durdurur (`REAL_HEURISTIC_BUDGET_S`);
durdurulan koşu "not run: over the time budget" durumuyla ve çalışma
süresi bir alt sınır olarak kaydedilir. Bütçe yukarıdaki koşudan sonra
geldi: o koşunun 10 dakikayı aşan dört çalışması tamamlandı ve korundu;
panelde çalışma süreleriyle işaretlenir. Bütçeyle tam bir koşunun tek
çekirdekte yaklaşık 1,5 saat sürmesi beklenir (bir tahmin).

```bash
evroute experiments real-data -o results/              # tam koşu; results/results.html panelinde real-data bölümü var
evroute experiments real-data --quick -o results/      # Karaman → Mersin, kesin planlayıcılar + genetic: yaklaşık 7 sn
```

Deney `real-data/results.csv` (plan başına bir satır; veri kümesi, ağ
büyüklüğü, istasyon sayısı ve `gap_pct` ile), `summary.json`, `table.tex`
ve tam koşuda OSM verisinde Karaman → İstanbul'un görüntüleyicisini ve
grafiklerini (`karaman-istanbul/`) yazar.

## Canlı ve operatör verisi

Bu vaka çalışmasının zayıf noktası yollar değil, şarj noktalarıdır. OSM,
EPDK'nin saydığı soketlerin yaklaşık %3'ünü bilir, istasyon güçlerinin
%64'ü varsayılandır ve bir şarj noktasının boş mu, dolu mu, arızalı mı
olduğunu hiçbir veri söylemez ([kapsam](#şarj-kapsamı-osm-ve-epdk)).
İşletmeciler ise (Eşarj, Trugo, ZES, Tesla ve çok sayıdaki küçük lisans
sahibi) kendi ağlarını eksiksiz bilir: her tesisi, her soketi ve gücünü,
fiyatı ve her şarj noktasının o anki durumunu. Onların verisiyle
planlayıcılar ağın haritalanmış kısmı yerine gerçek şarj ağı üzerinde
optimize eder; anlık müsaitlik de varsayılan kuyrukların
([Kuyruklar](#kuyruklar-varsayımlar)) yerine gözlenenleri koyabilir.
Planlayıcılar her istasyon listesini kabul ettiği için bu bir algoritma
değil, veri sorunudur. Bu bölüm 30 Ekim 2026 itibarıyla Türkiye'de neyin
bulunduğunu, neyin yapılmaması gerektiğini ve EVRoute'un bu veriyi nasıl
aldığını anlatır.

### Neler var (Ekim 2026)

**EPDK'nin Şarj@TR platformu.** Şarj Hizmeti Yönetmeliği'ndeki değişiklik
(Resmî Gazete 23.03.2026, Sayı 33202,
[metin](https://www.resmigazete.gov.tr/eskiler/2026/03/20260323-4.htm);
burada anılan maddeler yayımı tarihinde, iki başka hüküm 1 Temmuz 2026'da
yürürlüğe girdi) 30. maddeyi yeniden yazdı: EPDK, bütün şarj ağlarındaki
halka açık her şarj istasyonunun konumunu, şarj ünitelerini, soket
türlerini, sayılarını ve güçlerini, müsaitliğini, ödeme yöntemlerini ve
fiyatlarını yayımlayan, ücretsiz erişimli bir platform kurar ve işletir;
her şarj ağı işletmecisi de konum, ünite ve soketler, istasyon numarası ve
adı, ödeme yöntemleri ve çalışma saatleri, ünite ve soket başına fiyatlar,
planlı bakım ve arızalar, müsaitlik ve verilen enerji bilgilerini EPDK'nin
belirlediği biçimde ve aralıklarla Kuruma sunmakla yükümlüdür (değişiklik
yönetmeliğinin 19. maddesi). Yeni 28. maddenin birinci fıkrası ayrıca her
işletmeciye, ağındaki tüm istasyon ve soketlerin kullanım durumlarını,
müsaitliklerini ve fiyatlarını internet sitesinde ya da mobil
uygulamasında anlık olarak gösterme yükümlülüğü getirir (18. madde).
Platform bugün EPDK'nin iOS ve Android için Şarj@TR mobil uygulamasıdır:
bütün halka açık istasyonlar bir haritada, bir istasyonun soketleri
müsaitlik ve rezervasyon bilgileriyle, ödeme seçenekleri, güncel fiyat ve
soket gücü
([EPDK sayfası](https://www.epdk.gov.tr/Detay/Icerik/1-3428/enerji-donusumuserbest-erisim-platformu--sarj@tr)).
Sayfa, üçüncü taraflar için bir API, indirme ya da web servisi anlatmaz.
EPDK makinece okunabilir bir erişim sunduğunda Şarj@TR ideal tek kaynak
olur (her lisanslı işletmeci ona bildirmek zorunda olduğu için eksiksiz,
tek biçimli ve resmî, üstelik müsaitlik bilgisiyle) ve bu vaka
çalışmasındaki OSM istasyonlarının da fiyat anlık görüntüsünün de yerini
alır. O zamana kadar bir programın girdisi değil, insanlar için bir
başvuru kaynağıdır.

**OCPI.** Open Charge Point Interface, şarj noktası işletmecilerinin
(CPO) ve e-mobilite hizmet sağlayıcılarının dolaşım (roaming) için konum,
tarife, oturum ve şarj kaydı alışverişi yaptığı açık protokoldür. EVRoaming
Foundation tarafından sürdürülür ve ücretsiz kullanılır; güncel sürüm
2.3.0'dır, 2.2.1 hâlâ listelenir, 3.0 taslağının hedefi 2027'nin ortasıdır
([evroaming.org/ocpi](https://evroaming.org/ocpi/)). Locations modülü bir
tesisi Location → EVSE → Connector olarak tanımlar: her konnektörün
standardı, akım türü ve `max_electric_power` değeri (W cinsinden), her
EVSE'nin durumu (`AVAILABLE`, `CHARGING`, `OUTOFORDER`, ...). CPO durum
değişikliklerini ortaklarına iletir, ortaklar listenin tamamını da
çekebilir
([Locations modülü, 2.2.1](https://github.com/ocpi/ocpi/blob/release-2.2.1-bugfixes/mod_locations.asciidoc));
fiyatları Tariffs modülü taşır. Verisini bir araştırma projesiyle
paylaşmayı kabul eden bir işletmeci, bunu dolaşım ortaklarıyla zaten
kullandığı biçimde yapabilir: Locations ve Tariffs için okuma erişimi ya da
kaydedilmiş bir yanıt. Türkiye'deki hangi işletmecilerin kime OCPI arayüzü
sunduğu kendi anlaşmalarına bağlıdır; bu sayfa bunu bildiğini iddia etmez.

**Open Charge Map.** Herkese açık bir API'si olan, dünya çapında açık bir
şarj noktası kaydı; API, anahtar (API key) içermeyen istekleri reddeder.
Kullanıcılarının katkıda bulunduğu veriler 1 Nisan 2022'den beri CC BY 4.0
lisanslıdır; o tarihte önceki "aynı lisansla paylaş" (share-alike) koşulu
kaldırıldı
([duyuru](https://community.openchargemap.org/t/announcing-our-new-simpler-data-license-for-ocm-data-cc-by-4-0-international/565)).
Başka veri sağlayıcılarından aktarılan POI'ler ise o sağlayıcıların verisi
olarak kalır ve onları kullanan bir uygulama, veri sağlayıcının atfını
lisans koşullarıyla birlikte son kullanıcılarına göstermelidir
([koşullar](https://beta.openchargemap.org/about/terms)). Türkiye'deki
ağın ne kadarını içerdiği bu sayfa için ölçülmedi.

**Araştırma için veri anlaşmaları.** Bir işletmecinin eksiksiz verisine
giden doğrudan yol, istemektir: amacı, alanları (tesisler, soketler,
güçler, fiyatlar, durum geçmişi), dönemi, verinin nasıl saklanacağını ve
ham verinin değil yalnızca sonuçların yayımlanacağını belirten, üniversiteden
gelen bir talep. İstenecek şey tarihli bir anlık görüntü ya da işletmecinin
zaten sahip olduğu bir arayüze okuma erişimidir (OCPI Locations ve Tariffs
ya da aşağıdaki şemada bir CSV dışa aktarımı). Öteki yol, platformun
topladığı veriye araştırma amaçlı erişim için EPDK'ye başvurmaktır.

**OpenStreetMap** açık temel olarak kalır: ODbL lisanslı, pakette hazır ve
herkesçe geliştirilebilir; saha gözlemiyle ya da lisansı izin veren bir
kaynaktan işaretlenen istasyonlar ağın bir sonraki yapımına girer.

### Neler yapılmamalı

- İşletmecilerin uygulamalarını ya da internet sitelerini kullanım
  koşullarına aykırı biçimde **kazımayın** (scraping), uygulamalarının
  kapalı arayüzlerini çağırmayın. 28. maddenin birinci fıkrası veriyi
  sürücülere görünür kılar, toplanmaya açmaz: koşullar bunu genellikle
  yasaklar, işletmecinin sunucularına yük bindirir ve bunun üzerine kurulan
  bir sonuç ne yayımlanabilir ne de yeniden üretilebilir.
- Uygulamalardan ya da tescilli haritalardan istasyon listelerini OSM'ye
  **kopyalamayın**: OpenStreetMap yalnızca kaynağı buna izin veren veriyi
  kabul eder.
- İçe aktarılan bir veri kümesini lisansının izin vermediği koşullarla
  **yayımlamayın**: Open Charge Map verisi atıf ister, bir OCPI akışı
  anlaşmasına, OSM'den türetilen veri ODbL'ye tabidir.

### İstasyon verisini içe aktarmak: `evroute.station_sources`

[`evroute.station_sources`](../src/evroute/station_sources.py) modülündeki
bağdaştırıcılar, operatörlerin ve açık veri tabanlarının istasyonlarını
yayımladığı biçimleri, paketle gelen ağın (ya da başka herhangi bir ağın)
istasyonlarına dönüştürür; `evroute plan SENARYO --stations-file DOSYA` bir
senaryonun istasyonlarını bunlarla değiştirir. Kütüphane hiçbir zaman
internete bağlanmaz: veriler sağlayıcının API'si ve kullanıcının kendi
erişim bilgileriyle indirilir, kaydedilir ve içe aktarılır.

| bağdaştırıcı | biçim | okuduğu alanlar |
|---|---|---|
| `stations_from_csv` | belgelenmiş genel bir tablo (`CSV_COLUMNS`) | konum (`lat`/`lon`, `x`/`y` ya da `node`), `kind`, `power_kw`, `plugs`, `connectors`, `operator`, `name`, `price_per_kwh`, kuyruk alanları |
| `stations_from_ocpi` | OCPI 2.2.1 `Location` nesneleri, isteğe bağlı `Tariff` nesneleri | `coordinates`, `operator`, `publish`; her EVSE için `status` ve konnektörleri (`standard`, `power_type`, `max_electric_power` ya da gerilim x akım, `tariff_ids`) |
| `stations_from_ocm` | Open Charge Map POI'leri (API'sinin JSON çıktısı) | `AddressInfo`, `OperatorInfo`, `StatusType`; her bağlantı için `ConnectionTypeID`, `CurrentTypeID`, `PowerKW`, `Quantity`, `StatusType` |

Kurallar üçü için de aynıdır:

- bir kayıt 3 km içindeki (`max_snap_m`) en yakın düğüme bağlanır; OSM
  istasyonlarının yarıçapı da budur. Daha uzaktaki kayıtlar, koordinatı
  olmayan, modelin bildiği bir konnektör standardı bulunmayan ya da (OCPI'de)
  hizmette EVSE'si olmayan kayıtlar gibi, nedenleriyle birlikte `skipped`
  listesine yazılır;
- DC ve AC şarj noktaları olan bir tesis aynı düğümde **tür başına bir
  istasyon** olur; böylece DC girişi olmayan bir araç AC noktalarını yine
  kullanabilir. Güç o türün en yüksek gücüdür, soket sayısı da aynı anda
  araç şarj edebilen nokta sayısıdır (OCPI'de hizmetteki EVSE'ler);
- hizmet dışı bir şarj noktası (OCPI'de `INOPERATIVE` ya da `OUTOFORDER`,
  Open Charge Map'te çalışmayan bir bağlantı) istasyona soket, konnektör
  standardı, güç ya da tarife katmaz: tek CHAdeMO noktası arızalı olan bir
  tesise CHAdeMO'lu bir araç yönlendirilmez;
- fiyat dosyanın kendi fiyatıdır (CSV'de `price_per_kwh` ya da KDV
  eklenmiş OCPI tarifesinin temel enerji fiyatı); yoksa `prices.json`'daki
  doğrulanmış operatör liste fiyatı (operatör adı onu içerebilir, ör. "ZES
  (Zorlu Energy Solutions)"), o da yoksa varsayılan fiyat (AC 9,99 / DC
  14,24 TRY);
- kuyruklar, OSM istasyonlarının EPDK verisine dayanan varsayımlarını alır
  ([Kuyruklar](#kuyruklar-varsayımlar)).

**Canlı müsaitlik.** OCPI verisinde `imported.availability[i]`, `i`
istasyonunun anlık görüntüsüdür: `available` EVSE'ler (durumu
`AVAILABLE`), hizmetteki `total`, `out_of_service` (`INOPERATIVE`,
`OUTOFORDER`; bu EVSE'ler yalnızca anlık görüntüde yer alır), duruma göre
sayılar ve konumun `last_updated` zamanı;
`with_stations` bunu `scenario.meta["station_info"]` içinde saklar.
Planlayıcılar bunu henüz kullanmaz: kuyruk modeli beklemeleri hâlâ
Erlang-C varsayımlarından alır.

**Lisanslar.** İçe aktarılan veriler kaynaklarının lisansını korur. Open
Charge Map verileri CC BY 4.0'dır ([yukarıda](#neler-var-ekim-2026)):
`with_stations`, görüntüleyicinin gösterdiği atıf satırına bir "Open Charge
Map" atfı ekler; başka bir veri sağlayıcısından gelen bir POI ise o
sağlayıcının koşullarını ve atfını taşır (POI'deki `DataProvider`), bunları
da göstermeniz gerekir. OCPI akışları, erişimi sağlayan anlaşmaya tabidir.

### Anlık görüntülerden canlı planlamaya (gelecek çalışma)

Henüz yapılmamış iki sonraki adım:

- **Varsayılan kuyruklar yerine gözlenen doluluk.** Bugün her istasyonun
  beklenen bekleme süresi, ulusal ortalamalarla Erlang-C modelinden gelir.
  OCPI EVSE durumlarının (ya da Şarj@TR'deki müsaitliğin) bir geçmişi her
  tesisin günün saatlerine göre doluluğunu verir; bundan soket başına
  oturum sayısı, oturum süresi ve saatlik talep profili kestirilebilir:
  CSV şemasının zaten kabul ettiği istasyon başına kuyruk alanları
  (`daily_sessions`, `mean_service_min`, `demand_profile`). Parametreler ne
  olursa olsun, bekleme profilinin FIFO onarımı
  ([`model.md` §6](model.md#6-queues)) planlayıcıları kesin tutar. Erlang-C
  formülünün kendisini gözlenen beklemelerle değiştirmek ve hemen yola
  çıkan bir yolculuğun ilk istasyonları için anlık bir görüntü kullanmak
  bundan sonraki adımlardır.
- **Web sitesi için gecelik bir anlık görüntü.** Canlı demo tarayıcıda
  planlar ve Pyodide dışında hiçbir şey indirmez. Zamanlanmış bir GitHub
  Actions iş akışı her gece bir istasyon görüntüsü indirebilir (API
  anahtarı depo sırrı olarak saklanan Open Charge Map ya da anlaşmasıyla
  bir işletmeci akışı), onu `evroute.station_sources` ile içe aktarıp
  tarihli ve atıflı olarak siteyle birlikte yayımlayabilir: ziyaretçiler
  canlı istasyonlar üzerinde değil, bir önceki günün istasyonları üzerinde
  planlar.

## Sınırlar

- **Şarj kapsamı.** OSM, AC soketlerin yaklaşık %3,3'ünü ve DC soketlerin
  %1,9'unu bilir; 38° D'nin doğusunda DC şarj noktası yoktur. OSM
  istasyonlarıyla bulunan optimumlar gerçek ağın değil, haritalanmış şarj
  noktalarının optimumlarıdır. Hibrit kümenin boşluk doldurucuları
  sentetiktir.
- **İstasyon verisi.** Güçlerin %64'ü ve soket sayılarının %44'ü
  varsayılandır; soket sayıları alt sınırdır. Müsaitlik, çalışma saatleri
  ve arızalı şarj noktaları modellenmez (OCPI'den içe aktarılan veri
  müsaitliği bildirir, ama planlayıcılar onu henüz kullanmaz); tek bir markaya ya da yalnızca
  müşterilere açık istasyonlar (ör. "Sadece Renault", otel şarj noktaları)
  kümede kalır, çünkü OSM bu kısıtı nadiren işaretler.
- **Hızlar.** Yasal sınırlar ya da atanmış varsayılanlar; serbest akış,
  trafik ya da sıkışıklık yok. 1 633 kenarın hızı 130 km/sa üzerindedir
  (neredeyse tamamı otoyollarda; levhalar orada 140 km/sa'e kadar izin
  verebilir), 539 kenarınki (285 km) 140 km/sa üzerinde, çok kısa 6
  kenarınki (48-105 m) 150 km/sa üzerinde, en çok 202,6 km/sa: bu 6 kenar
  yolculuk süreleri korunarak düz çizgi uzunluklarına uzatıldı. En yüksek
  yasal sınır olan 140 km/sa üzerindeki hızlara ihtiyatla yaklaşın.
- **Yollar.** Üçüncül yollar ve şehir içi sokaklar ağda yoktur, il yolları
  da yalnızca bir boşluğu kapattıkları yerde vardır; bu yüzden yolculuklar
  bir yerleşimin ağ düğümünde başlar ve biter (il merkezleri 2,24 km
  içinde) ve birkaç bölgeye dolambaçla ulaşılır (Tunceli, Yalova, Hakkâri).
  Ağ ücretli yolları kullanır; EVRoute geçiş ücretlerini fiyatlandırmaz.
- **Yükseklikler.** SRTM 2000 yılına ait ve 30 m ızgaralıdır; o tarihten
  sonra yapılmış yollar (baraj yer değiştirmeleri) %15 eğim kırpmasıyla
  yumuşatıldı.
- **Fiyatlar** belirli bir tarihe ait ve sık değişir; OSM istasyonlarının
  üçte birinin fiyatı operatörce doğrulanmıştır, gerisi varsayılandır.
- **Kuyruklar** ulusal ortalamalardan varsayımlardır ve bir türün her
  istasyonunda aynıdır.
- **Araç.** Hazır profiller kamuya açık yaklaşık değerler kullanır.

## Veriyi yeniden oluşturmak

Dosyalar depodaki
[`tools/build_osm_turkey.py`](../tools/build_osm_turkey.py) betiğiyle
oluşturulur (kurulan paketin parçası değildir). Betik OSM özütünü
(yaklaşık 700 MB), SRTM karolarını ve KGM tablosunu kendisi indirir; tam bir
çalışma yaklaşık 10 dakika ve 1 GB disk alanı ister, sonraki çalışmalar
çalışma dizinindeki önbelleğe alınmış aşamaları yeniden kullanır:

```bash
pip install osmium numpy scipy shapely openpyxl
python -I tools/build_osm_turkey.py --workdir /tmp/osm-work --outdir src/evroute/data/osm_turkey
```

Betik `nodes`, `edges`, `stations`, `places`, `validation.csv` ve
`meta.json` dosyalarını yeniden yazar; `prices.json`, `DATA_LICENSE.md` ve
veri README'si elle tutulur. Daha yeni bir özüt ağı değiştirir; bu sayfadaki
sayılar (ve onları `meta.json` ile karşılaştıran testler) yeni yapıyı izler.
`meta.json` yapının her parametresini ve istatistiğini kaydeder: OSM zaman
damgası ve sağlama toplamı, hız kuralları ve etiketli yollar üzerindeki
denetimleri, sadeleştirmenin attıkları, eğim kırpma, istasyon kuralları ve
doğrulama.

## Lisanslar

| ne | lisans |
|---|---|
| EVRoute kodu, yükleyici ve veri hazırlama betiği | MIT |
| `nodes`, `edges`, `stations`, `places`, `validation.csv`, `meta.json` | **ODbL 1.0**, © OpenStreetMap contributors (OpenStreetMap'ten türetilmiş bir veritabanı) |
| düğüm yükseklikleri | SRTM, NASA/USGS, kamu malı |
| `prices.json` | kaynağı ve tarihiyle olgular; lisans iddiası yok; markalar sahiplerine aittir |
| KGM mesafeleri, EPDK istatistikleri | alıntılanan olgular; belgelerin kendisi pakette yok |

**Atıf.** Bu veriden üretilen her harita, grafik, rota planı ya da tablo
"© OpenStreetMap contributors" atfını taşımalıdır; görüntüleyici, statik
harita ve arama uzayı grafikleri, sonuç paneli ve deneyin LaTeX tablo
başlığı bunu kendiliğinden yapar. **Benzer paylaşım:** veritabanının
uyarlanmış bir sürümünü (örneğin düzeltilmiş yollar ya da eklenmiş
istasyonlarla) yayımlarsanız onu da ODbL ile sunmanız gerekir; grafik ya da
rota gibi üretilmiş bir çalışma ise atfı taşıdığı sürece her koşulla
yayımlanabilir. Ayrıntılar:
[`DATA_LICENSE.md`](../src/evroute/data/osm_turkey/DATA_LICENSE.md) ve
[openstreetmap.org/copyright](https://www.openstreetmap.org/copyright).
