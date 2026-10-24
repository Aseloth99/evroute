# EVRoute — Proje Planı (Türkçe)

> **Konu:** Şarj istasyonlu elektrikli araçlar için yol planlama
> (*Path Planning for Electric Vehicles with Charging Stations*)

## 1. Problem neden hâlâ ilginç? ("Hep yapılmış" itirazına cevap)

Literatürde EV rota planlama çalışılmış bir konu, ama çalışmaların çoğu
aşağıdaki gerçekçi unsurların **yalnızca bir ya da ikisini** birlikte ele alır:

| Unsur | Gerçek hayatta | Çoğu çalışmada |
|---|---|---|
| Doğrusal olmayan şarj eğrisi (CC-CV) | %80'den sonra güç düşer | sabit güç varsayımı |
| Kısmi şarj (ne kadar şarj?) | sürücü %43'e kadar da şarj edebilir | hep %100'e şarj |
| İstasyon kuyruğu | akşam saatlerinde 20-40 dk bekleme | bekleme yok |
| Rejeneratif frenleme / eğim | Toroslar'da iniş batarya doldurur | düz yol |
| Sıcaklık | -5 °C'de menzil %20-30 düşer | 20 °C |
| Batarya yaşlanması (SoH) | hızlı şarj bataryayı yıpratır | yok sayılır |
| Belirsizlik | tüketim ±%10 sapar | deterministik |
| Eko-hız | yavaş sürmek bir şarj durağını kaldırabilir | sabit hız |

**Bizim katkımız:** Bunların **hepsini tek, kesin (optimal) ve doğrulanabilir**
bir çerçevede birleştirmek; açık kaynak bir **referans uygulama**, tekrar
üretilebilir bir **benchmark seti** ve bir **Türkiye şehirlerarası vaka
çalışması** sunmak. Ayrıca batarya yıpranmasını (SoH) amaç fonksiyonuna
katarak listendeki 1. konu (LSTM ile SoC/SoH tahmini) ile köprü kurmak.

## 2. Dil ve teknoloji seçimi

| Katman | Dil | Neden |
|---|---|---|
| Çekirdek model + algoritmalar | **Python 3.10+ (saf, bağımlılıksız)** | akademide ortak dil; okunabilir; her yerde çalışır; hakemler kodu kolayca doğrular |
| Etkileşimli görselleştirme + animasyon | **HTML + CSS + saf JavaScript (Canvas)** | tarayıcıda çalışır, internet/kütüphane gerektirmez, GitHub Pages'te yayınlanabilir |
| Statik grafikler (README, deney raporları) | **Python + seaborn** (isteğe bağlı) | şık, tutarlı tema; çekirdek kod bağımlılıksız kalır |
| Canlı mod sunucusu | Python standart kütüphanesi (`http.server`) | ek bağımlılık yok |

## 3. Matematiksel model (özet)

* **Yol ağı** $G=(V,E)$: düğümlerin rakımı, kenarların uzunluğu, hız sınırı ve yol sınıfı.
* **Enerji** (fizik tabanlı boyuna dinamik): yuvarlanma + aerodinamik (rüzgâr dahil) + eğim
  + dur-kalk kaybı + klima/ısıtıcı; rejeneratif frenleme verimi ve güç sınırı; batarya
  doluysa geri kazanım kesilir.
* **Şarj**: araç şarj eğrisi SoC aralıklarında sabit güçlü (Montoya vd. 2017),
  istasyon gücüyle sınırlandırılır → şarj fonksiyonu *parçalı doğrusal, iç bükey*.
  Soğukta (ön ısıtma yoksa) güç düşer.
* **Kuyruk**: her istasyon bir M/M/c kuyruğu; saatlik talep profili; Erlang-C ile
  beklenen bekleme süresi; FIFO özelliği korunarak zamana bağlı.
* **Batarya yıpranması**: C-oranı, yüksek SoC, soğuk/sıcak stres çarpanlarıyla
  kWh başına SoH kaybı → para karşılığı.
* **Amaç**: (a) toplam süre, (b) genelleştirilmiş maliyet = zamanın değeri × süre +
  enerji parası + yıpranma maliyeti, (c) Pareto cephesi (süre, para, yıpranma).
* **Kısıtlar**: her düğümde rezerv SoC (örn. %10), hedefte minimum varış SoC'si,
  şarj üst sınırı (örn. %80/100).

## 4. Algoritmalar (karşılaştırmalı)

| Ad | Tür | Garanti |
|---|---|---|
| `naive` | bataryayı yok sayan en hızlı yol | çoğu zaman *uygulanamaz* (yolda kalır) — problemin gösterimi |
| `greedy` | "gerekince en uygun istasyona git, %80 şarj et" | sezgisel (araç içi navigasyon benzeri) |
| `station-graph` | istasyon meta-grafı, sabit şarj hedefi | sezgisel (Storandt & Funke 2012 tarzı) |
| `genetic` | genetik algoritma | sezgisel (literatürdeki metasezgisellerin temsilcisi) |
| **`bls`** | Batarya durumlu etiket belirleme (label-setting) | şarj hedefi ızgarasında **kesin** |
| **`bls-astar`** | BLS + tutarlı (consistent) "enerji açığı" A* sezgiseli | aynı optimum, çok daha az etiket — **ana yöntem** |
| **`cfp`** / `cfp-astar` | Şarj Fonksiyonu Yayılımı (Baum vd.) | **sürekli şarj miktarları için kesin** |
| `pareto` | çok amaçlı etiket belirleme (Martins + NAMOA* budama) | Pareto cephesi |

Bağımsız bir **değerlendirici (evaluator)** her planı sıfırdan yeniden
simüle eder; testler her algoritmanın iddia ettiği amaç değerinin bununla
birebir aynı olduğunu doğrular. Küçük örneklerde kaba kuvvet (brute force)
ile optimallik kontrol edilir.

## 5. Görselleştirme ve animasyon

* **Etkileşimli web görüntüleyici** (tek HTML dosyası, çevrimdışı çalışır):
  arazi gölgelendirmeli harita, istasyonlar, her algoritmanın rotası,
  **Animasyon AÇIK/KAPALI anahtarı**, oynat/duraklat, hız, zaman çizelgesi,
  şarj animasyonu (dolan batarya, kW, kalan dakika), **yarış modu** (tüm
  algoritmalar aynı saatte), **arama tekrarı** (algoritmanın grafı nasıl
  taradığı; BLS ↔ BLS-A* karşılaştırması), SoC–mesafe grafiği + rakım profili,
  süre dağılımı, Pareto grafiği, karşılaştırma tablosu, TR/EN dil, açık/koyu tema,
  mobil uyum.
* **Canlı mod**: `evroute serve` → haritaya tıklayıp başlangıç/varış seç,
  parametreleri değiştir, "Planla"ya bas.
* **Etkileşimli sonuç paneli**: tüm deneylerin grafikleri tarayıcıda
  (üzerine gelince değer, filtre, algoritma seçimi).
* **seaborn** (isteğe bağlı): README ve raporlar için statik grafikler.

## 6. Deneyler

1. Vaka çalışması: Karaman → İstanbul, Antalya → Ankara, İzmir → Erzurum (dağlık).
2. Ölçeklenebilirlik: düğüm sayısına göre çalışma süresi / yerleşen etiket.
3. Sezgisellerin optimallik açığı (100+ rastgele başlangıç–varış çifti).
4. Şarj ızgarası çözünürlüğünün etkisi (BLS → CFP yakınsaması).
5. Sıcaklık duyarlılığı (-10 … +35 °C).
6. Kuyruk farkındalığının değeri.
7. Süre–maliyet–yıpranma Pareto analizi.
8. Monte Carlo güvenilirlik: güvenlik katsayısına göre yolda kalma olasılığı.
9. Eko-hız: daha yavaş sürmenin toplam süreyi kısalttığı durumlar.

## 7. Klasör yapısı

```
evroute/
├── src/evroute/          # çekirdek paket (saf Python)
│   ├── algorithms/       # naive, greedy, station_graph, genetic, labels, cfp, pareto
│   ├── generators/       # Türkiye ağı, sentetik ağlar, OSM yükleyici, hazır senaryolar
│   ├── viz/              # web görüntüleyici, matplotlib figür/animasyon
│   ├── experiments/      # tekrar üretilebilir deneyler
│   ├── robustness.py     # Monte Carlo
│   ├── server.py         # canlı mod
│   └── cli.py            # komut satırı
├── tests/                # pytest
├── docs/                 # model, algoritmalar, ispatlar, ekran görüntüleri
└── examples/
```

## 8. Yol haritası

1. ✅ Çekirdek model katmanı + bağımsız değerlendirici + testler
2. ⏳ Algoritmalar (BLS, BLS-A*, CFP, Pareto, sezgiseller) + çapraz doğrulama
3. ⏳ Türkiye veri seti + sentetik benchmark üreteçleri
4. ⏳ Web görüntüleyici + animasyonlar + matplotlib figürleri
5. ⏳ Deneyler, etkileşimli sonuç paneli, seaborn grafikleri, dokümantasyon
6. ⏳ Bağımsız inceleme turu (doğruluk, performans, tasarım), düzeltmeler
7. 🔔 **En son: GitHub'da paylaşım** (ayrı depo önerisi, README, lisans, DOI)
