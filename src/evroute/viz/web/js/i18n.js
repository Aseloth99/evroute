/* EVRoute viewer -- i18n.js
 *
 * Complete Turkish and English UI strings plus language-aware formatting of
 * numbers, money, percentages, durations and clock times (Intl based:
 * tr-TR writes "%80" and "1.234,5", en-GB "80%" and "1,234.5").
 * Static markup is translated through data-i18n* attributes:
 *   data-i18n="key"        -> textContent
 *   data-i18n-aria="key"   -> aria-label
 *   data-i18n-title="key"  -> title
 */
(function (EVR) {
  'use strict';

  const STR = {
    tr: {
      skip_to_map: 'Haritaya geç',
      animation: 'Animasyon',
      language: 'Dil',
      theme: 'Tema',
      theme_light: 'Açık tema',
      theme_auto: 'Sistem teması',
      theme_dark: 'Koyu tema',
      controls: 'Kontroller',
      plan_trip: 'Rota planla',
      live: 'canlı',
      scenario: 'Senaryo',
      origin: 'Başlangıç',
      destination: 'Varış',
      swap: 'Başlangıç ve varışı değiştir',
      pick_hint: 'Haritaya tıklayın: başlangıç · Shift+tık: varış',
      map_click_sets: 'Harita tıklaması şunu ayarlar',
      vehicle: 'Araç',
      initial_soc: 'Başlangıç şarjı',
      temperature: 'Sıcaklık',
      reserve: 'Rezerv',
      min_arrival: 'Varış şarjı',
      objective: 'Amaç',
      objective_time: 'En kısa süre',
      objective_cost: 'En düşük maliyet',
      departure: 'Kalkış',
      value_of_time: 'Zaman değeri',
      per_hour: '/sa',
      eco_speeds: 'Eko hızlar (%100 / 90 / 80)',
      queue_aware: 'Kuyruk farkındalığı',
      algorithms: 'Algoritmalar',
      plan: 'Planla',
      routes: 'Rotalar',
      routes_hint: 'seçmek için tıklayın',
      layers: 'Katmanlar',
      layer_terrain: 'Arazi gölgelendirme',
      layer_stations: 'Şarj istasyonları',
      layer_labels: 'Yer adları',
      layer_others: 'Diğer rotalar',
      map_aria: 'Yol ağı, şarj istasyonları ve hesaplanan rotaların haritası',
      map_tools: 'Harita araçları',
      zoom_in: 'Yakınlaştır',
      zoom_out: 'Uzaklaştır',
      fit_route: 'Rotaya sığdır (F)',
      distance: 'Mesafe',
      speed: 'Hız',
      elevation: 'Rakım',
      search_replay: 'Arama tekrarı',
      compare: 'Karşılaştır',
      compare_with: 'Karşılaştırılacak algoritma',
      key_low: 'erken',
      key_high: 'geç',
      planning: 'Planlanıyor…',
      play_pause: 'Oynat / duraklat',
      play_pause_key: 'Oynat / duraklat (Boşluk)',
      restart: 'Baştan başlat',
      speed_title: 'Oynatma hızı',
      mode: 'Animasyon modu',
      mode_trip: 'Yolculuk',
      mode_trip_title: 'Seçili rotada sürüş',
      mode_race: 'Yarış',
      mode_race_title: 'Tüm görünür rotalar aynı saatte yarışır (R)',
      mode_search: 'Arama',
      mode_search_title: 'Arama algoritmasının keşfettiği düğümler (S)',
      timeline: 'Zaman çizelgesi',
      trip: 'Yolculuk',
      charging_stops: 'Şarj durakları',
      chart_soc: 'Şarj durumu ve rakım',
      chart_wait: 'Kuyruk profili (24 saat)',
      chart_time: 'Süre dağılımı',
      chart_pareto: 'Pareto cephesi: süre ve ücret',
      pareto_hint: 'nokta boyutu = batarya yıpranması · seçmek için tıklayın',
      comparison: 'Algoritma karşılaştırması',
      table_hint: 'sıralamak için başlığa, seçmek için satıra tıklayın',
      shortcuts: 'Kısayollar: Boşluk oynat, A animasyon, R yarış, S arama, ←/→ ileri-geri, F sığdır',

      st_drive: 'Sürüş',
      st_queue: 'Kuyrukta bekliyor',
      st_setup: 'Bağlanıyor',
      st_charge: 'Şarj ediliyor',
      st_done: 'Varıldı',
      st_stranded: 'Batarya bitti',
      st_start: 'Kalkışa hazır',
      min_left: '{m} kaldı',
      in_queue_left: 'sıra: {m}',
      arrived_at: 'varış {c}',
      on_road: 'yolda',

      toast_anim_on: 'Animasyon açık',
      toast_anim_off: 'Animasyon kapalı: statik görünüm',
      toast_race: 'Yarış modu: tüm görünür rotalar aynı saatte',
      toast_search: 'Arama tekrarı: {a}',
      toast_no_trace: 'Bu algoritma için arama izi yok',
      toast_origin: 'Başlangıç: {n}',
      toast_destination: 'Varış: {n}',
      toast_planned: '{n} algoritma {ms} ms içinde planladı',
      toast_select: 'Seçildi: {a}',

      total_time: 'Toplam süre',
      arrival: 'Varış',
      departs: 'Kalkış',
      stops: 'Durak',
      stops_n: '{n} durak',
      stops_1: '1 durak',
      near: '{p} yakını',
      energy: 'Enerji',
      charged: 'Şarj edilen',
      money: 'Ücret',
      soh: 'Batarya yıpranması',
      soh_short: 'SoH kaybı',
      arrival_soc: 'Varış şarjı',
      min_soc: 'En düşük şarj',
      runtime: 'Çalışma süresi',
      settled: 'Etiketler',
      drive: 'Sürüş',
      queue: 'Kuyruk',
      setup: 'Bağlanma',
      charge: 'Şarj',
      wait: 'Bekleme',
      infeasible: 'uygulanamaz',
      feasible: 'uygulanabilir',
      no_route: 'Uygulanabilir rota bulunamadı',
      stranded_at: '{km} sonra batarya bitiyor',
      no_stops: 'Şarj durağı gerekmiyor',
      vs_best: 'en iyiye göre {d}',
      best: 'en iyi',
      partial_front: 'Etiket bütçesi doldu: cephe eksik olabilir',
      alternative: 'Pareto alternatifi {i} / {n}',

      g_exact: 'kesin',
      g_grid: 'ızgara',
      g_heuristic: 'sezgisel',
      g_partial: 'kısmi',
      g_exact_title: 'Model için kanıtlanmış optimum',
      g_grid_title: 'Şarj hedefleri SoC ızgarasında olduğunda optimum',
      g_heuristic_title: 'Sezgisel: optimum garanti edilmez',

      th_algorithm: 'Algoritma',
      th_guarantee: 'Garanti',
      th_feasible: 'Uyg.',
      th_total: 'Toplam',
      th_drive: 'Sürüş',
      th_charge: 'Şarj',
      th_wait: 'Bekleme',
      th_distance: 'Mesafe',
      th_stops: 'Durak',
      th_energy: 'Enerji',
      th_money: 'Ücret',
      th_soh: 'SoH kaybı',
      th_runtime: 'Süre (ms)',
      th_settled: 'Etiket',

      stop_arrival: 'Varış',
      stop_queue: 'Kuyruk',
      stop_charge: 'Şarj',
      stop_energy: 'Enerji',
      stop_money: 'Ücret',

      tip_power: 'Güç',
      tip_plugs: 'Soket',
      tip_price: 'Fiyat',
      tip_wait: 'Beklenen kuyruk {c}',
      tip_operator: 'İşletmeci',
      tip_wait_24: '24 saatlik beklenen kuyruk',

      ax_soc: 'SoC (%)',
      ax_elev: 'Rakım (m)',
      ax_dist: 'Mesafe (km)',
      ax_time_h: 'Toplam süre (sa)',
      ax_money: 'Ücret',
      ax_wait: 'Bekleme (dk)',
      ax_hour: 'Saat',
      reserve_line: 'rezerv',
      elev_profile: 'rakım',
      now: 'şimdi',
      wait_none: 'Bir istasyonun üzerine gelin veya bir durak seçin',
      wait_zero: 'Kuyruk modeli kapalı (bekleme yok)',
      pareto_others: 'diğer algoritmalar',

      kv_vehicle: 'Araç',
      kv_battery: 'Batarya',
      kv_usable: 'Kullanılabilir',
      kv_temperature: 'Sıcaklık',
      kv_departure: 'Kalkış',
      kv_initial: 'Başlangıç şarjı',
      kv_reserve: 'Rezerv / varış',
      kv_objective: 'Amaç',
      kv_network: 'Ağ',
      kv_stations: 'İstasyon',
      kv_queues: 'Kuyruklar',
      kv_speeds: 'Hız seviyeleri',
      on: 'açık',
      off: 'kapalı',
      nodes_edges: '{n} düğüm · {m} kenar',

      sc_explored: 'keşfedilen düğüm',
      sc_labels: 'etiket',
      sc_key: 'anahtar',
      sc_done: 'tamamlandı',
      sc_of: '/',

      err_generic: 'Bir hata oluştu: {m}',
      err_same_od: 'Başlangıç ve varış aynı olamaz',
      err_no_algo: 'En az bir algoritma seçin',
      err_no_data: 'Görüntüleyici verisi bulunamadı.',
      generated_by: 'EVRoute {v} ile oluşturuldu · yaklaşık veriler, navigasyon için değildir',
      unit_km: 'km',
      unit_kmh: 'km/sa',
      unit_m: 'm',
      unit_h: 'sa',
      unit_min: 'dk',
      unit_kwh: 'kWh',
      unit_kw: 'kW',
      day_plus: '+{d}g',

      reliability: 'Güvenilirlik',
      p_strand_m: 'Yolda kalma',
      p_short_m: 'Eksik varış',
      th_p_strand: 'P(kalma)',
      th_p_short: 'P(eksik)',
      rel_title: '{n} Monte Carlo dünyası (belirsiz tüketim, rastgele kuyruklar): yolda kalma {s}, rezerv ihlali {b}, hedefin altında varış {a}',
      rel_short_title: 'Varışta istenen şarjın altında kalma olasılığı ({n} Monte Carlo dünyası)',
      rel_strand_title: 'Bataryanın yolda bitme olasılığı ({n} Monte Carlo dünyası)',
      pareto_fastest: 'en hızlı',
      pareto_cheapest: 'en ucuz',
      pareto_members: '{n} Pareto-optimal rota',
      pareto_dominated: 'domine edilen bölge',
      pareto_front: 'Pareto cephesi',
      pareto_returned: 'döndürülen plan',
      off_axis: 'eksen dışında',
      race_more: '+{n}',
      now_marker: 'şimdi',

      // charging tariffs (only shown for a scenario with a tariff / a plan with a hold)
      st_hold: 'Tarife değişimi için bekleme',
      st_hold_offpeak: 'Ucuz tarife için bekleme',
      hold_tag: 'tarife değişimine {m}',
      hold_tag_offpeak: 'ucuz tarifeye {m}',
      hold: 'Tarife beklemesi',
      th_hold: 'Tarife bekl.',
      hold_title: 'Daha ucuz bir fiyat dönemi için gönüllü bekleme (kuyruğa girmeden önce)',
      stop_hold: 'Tarife değişimi için {m} bekleme · kuyruğa giriş {c}',
      stop_hold_offpeak: 'Ucuz tarife için {m} bekleme · kuyruğa giriş {c}',
      tariff: 'Tarife',
      tariff_busy: 'yoğun saatler (doluluk ≥ {u})',
      tariff_high: '{p} üstü: {f}',
      tariff_peak: 'puant {h}: {f}',
      tariff_flat: 'sabit fiyat',
      tariff_peak_short: 'puant',
      tariff_offpeak_short: 'puant dışı',
      tariff_high_short: '{p} üstü',
      tariff_line: '{p} fiyat eşiği',
      tariff_peak_band: 'puant fiyat',
      tariff_toggle: 'Şarj tarifesi (%80 kuralı, puant saatleri)',
      tariff_illustrative: 'örnek değerler, gerçek fiyatlar değildir',
      kv_tariff: 'Tarife',
      kv_tariff_high: '{p} üstü fiyat',
      kv_tariff_peak: 'Puant saatleri',
      data_credit: 'Veri: {c}',
      data_licence: 'Lisans: {l}.',
      tip_tariff: 'Tarife ({c})',
      // in-browser planner of the web site (Pyodide in a Web Worker) and its budgets
      label_limit: 'etiket bütçesi doldu',
      g_incomplete: 'eksik olabilir',
      boot_title: 'Planlayıcı tarayıcınızda çalışır',
      boot_lead: 'İlk ziyarette bir kez yaklaşık {mb} indirilir (Pyodide {v} ile Python ve EVRoute); sonraki ziyaretlerde tarayıcı önbelleği kullanılır. Planlama tamamen tarayıcınızda yapılır, hiçbir sunucuya veri gönderilmez.',
      boot_download: 'Python indiriliyor',
      boot_install: 'EVRoute kuruluyor',
      boot_scenario: 'Senaryo yükleniyor',
      boot_failed: 'Tarayıcıdaki planlayıcı yüklenemedi: {m}',
      boot_retry: 'Tekrar dene',
      planning_algo: '{a} planlanıyor ({i}/{n})…',
      restarting: '{a} zaman sınırını aştı; planlayıcı yeniden başlatılıyor…',
      budget_time_stop: '{s} sn\'lik zaman sınırında durduruldu',
      budget_time_limit: '{s} sn\'lik zaman sınırına ulaştı',
      budget_cancelled: 'sizin isteğinizle durduruldu',
      budget_skipped: 'çalıştırılmadı',
      budget_labels: 'etiket bütçesi ({n}) doldu',
      budget_title: 'Eksik olabilir.',
      budget_hint: 'Web sürümü planlayıcıları etiket ve zaman sınırlarıyla kısıtlar. Bu sınırlar olmadan çalıştırmak için EVRoute\'u kurun ve şunu çalıştırın:',
      time_limit_hit: 'zaman sınırı doldu',
      not_planned: 'Henüz sonuç yok',
      btn_stop: 'Durdur',
      copy: 'Kopyala',
      copied: 'Kopyalandı',
      boot_stale: 'Bu sayfa açıldıktan sonra web sitesi güncellendi. Lütfen sayfayı yeniden yükleyin.',
      boot_reload: 'Sayfayı yeniden yükle',
      boot_integrity: 'İndirilen bir dosya beklenen içerikle eşleşmedi ve kullanılmadı ({m}).',
      boot_blocked: 'cdn.jsdelivr.net bir reklam engelleyici ya da ağ filtresi tarafından engelleniyor olabilir; izin verip yeniden deneyin.',
      pick_hint_touch: 'Haritaya dokunun: A ya da B düğmesi hangi noktanın seçileceğini belirler',
      plan_own: 'Kendi yolculuğunuzu planlayın',
      site_home: 'EVRoute web sitesi',
      site_repo: 'GitHub deposu',
      // scenario texts of the presets (the data carries them in English)
      meta_disclaimer_turkey: 'Yaklaşık yol ağı ve sentetik istasyonlar; araştırma amaçlı bir örnektir, seyir için değildir.',
      meta_disclaimer_osm_turkey: 'OpenStreetMap yolları ve istasyonları, operatör liste fiyatları ve varsayılan kuyruklar; araştırma verisidir, seyir için değildir.',
      meta_licence_osm_turkey: 'ODbL 1.0 (veri), MIT (kod)',
    },
    en: {
      skip_to_map: 'Skip to map',
      animation: 'Animation',
      language: 'Language',
      theme: 'Theme',
      theme_light: 'Light theme',
      theme_auto: 'System theme',
      theme_dark: 'Dark theme',
      controls: 'Controls',
      plan_trip: 'Plan a trip',
      live: 'live',
      scenario: 'Scenario',
      origin: 'Origin',
      destination: 'Destination',
      swap: 'Swap origin and destination',
      pick_hint: 'Click the map: origin · Shift+click: destination',
      map_click_sets: 'A map click sets',
      vehicle: 'Vehicle',
      initial_soc: 'Initial charge',
      temperature: 'Temperature',
      reserve: 'Reserve',
      min_arrival: 'Arrival charge',
      objective: 'Objective',
      objective_time: 'Fastest',
      objective_cost: 'Lowest cost',
      departure: 'Departure',
      value_of_time: 'Value of time',
      per_hour: '/h',
      eco_speeds: 'Eco speeds (100 / 90 / 80 %)',
      queue_aware: 'Queue-aware',
      algorithms: 'Algorithms',
      plan: 'Plan',
      routes: 'Routes',
      routes_hint: 'click to select',
      layers: 'Layers',
      layer_terrain: 'Terrain hillshade',
      layer_stations: 'Charging stations',
      layer_labels: 'Place names',
      layer_others: 'Other routes',
      map_aria: 'Map of the road network, charging stations and computed routes',
      map_tools: 'Map tools',
      zoom_in: 'Zoom in',
      zoom_out: 'Zoom out',
      fit_route: 'Fit to route (F)',
      distance: 'Distance',
      speed: 'Speed',
      elevation: 'Elevation',
      search_replay: 'Search replay',
      compare: 'Compare',
      compare_with: 'Algorithm to compare with',
      key_low: 'early',
      key_high: 'late',
      planning: 'Planning…',
      play_pause: 'Play / pause',
      play_pause_key: 'Play / pause (Space)',
      restart: 'Restart',
      speed_title: 'Playback speed',
      mode: 'Animation mode',
      mode_trip: 'Trip',
      mode_trip_title: 'Drive the selected route',
      mode_race: 'Race',
      mode_race_title: 'All visible routes race on one clock (R)',
      mode_search: 'Search',
      mode_search_title: 'Nodes explored by the search algorithm (S)',
      timeline: 'Timeline',
      trip: 'Trip',
      charging_stops: 'Charging stops',
      chart_soc: 'State of charge and elevation',
      chart_wait: 'Queue profile (24 h)',
      chart_time: 'Time breakdown',
      chart_pareto: 'Pareto front: time vs money',
      pareto_hint: 'point size = battery wear · click to select',
      comparison: 'Algorithm comparison',
      table_hint: 'click a header to sort, a row to select',
      shortcuts: 'Shortcuts: Space play, A animation, R race, S search, ←/→ scrub, F fit',

      st_drive: 'Driving',
      st_queue: 'Waiting in queue',
      st_setup: 'Plugging in',
      st_charge: 'Charging',
      st_done: 'Arrived',
      st_stranded: 'Battery empty',
      st_start: 'Ready to depart',
      min_left: '{m} left',
      in_queue_left: 'queue: {m}',
      arrived_at: 'arrived {c}',
      on_road: 'on road',

      toast_anim_on: 'Animation on',
      toast_anim_off: 'Animation off: static view',
      toast_race: 'Race mode: all visible routes on one clock',
      toast_search: 'Search replay: {a}',
      toast_no_trace: 'No search trace for this algorithm',
      toast_origin: 'Origin: {n}',
      toast_destination: 'Destination: {n}',
      toast_planned: '{n} algorithms planned in {ms} ms',
      toast_select: 'Selected: {a}',

      total_time: 'Total time',
      arrival: 'Arrival',
      departs: 'Departure',
      stops: 'Stops',
      stops_n: '{n} stops',
      stops_1: '1 stop',
      near: 'near {p}',
      energy: 'Energy',
      charged: 'Charged',
      money: 'Money',
      soh: 'Battery wear',
      soh_short: 'SoH loss',
      arrival_soc: 'Arrival charge',
      min_soc: 'Lowest charge',
      runtime: 'Runtime',
      settled: 'Settled labels',
      drive: 'Driving',
      queue: 'Queue',
      setup: 'Plug-in',
      charge: 'Charging',
      wait: 'Waiting',
      infeasible: 'infeasible',
      feasible: 'feasible',
      no_route: 'No feasible route found',
      stranded_at: 'Battery runs empty after {km}',
      no_stops: 'No charging stop needed',
      vs_best: '{d} vs best',
      best: 'best',
      partial_front: 'Label budget reached: the front may be incomplete',
      alternative: 'Pareto alternative {i} of {n}',

      g_exact: 'exact',
      g_grid: 'grid',
      g_heuristic: 'heuristic',
      g_partial: 'partial',
      g_exact_title: 'Proven optimum for the model',
      g_grid_title: 'Optimal for charging targets on the SoC grid',
      g_heuristic_title: 'Heuristic: no optimality guarantee',

      th_algorithm: 'Algorithm',
      th_guarantee: 'Guarantee',
      th_feasible: 'Feas.',
      th_total: 'Total',
      th_drive: 'Drive',
      th_charge: 'Charge',
      th_wait: 'Wait',
      th_distance: 'Distance',
      th_stops: 'Stops',
      th_energy: 'Energy',
      th_money: 'Money',
      th_soh: 'SoH loss',
      th_runtime: 'Runtime (ms)',
      th_settled: 'Labels',

      stop_arrival: 'Arrival',
      stop_queue: 'Queue',
      stop_charge: 'Charge',
      stop_energy: 'Energy',
      stop_money: 'Money',

      tip_power: 'Power',
      tip_plugs: 'Plugs',
      tip_price: 'Price',
      tip_wait: 'Expected queue {c}',
      tip_operator: 'Operator',
      tip_wait_24: 'Expected queue over 24 h',

      ax_soc: 'SoC (%)',
      ax_elev: 'Elevation (m)',
      ax_dist: 'Distance (km)',
      ax_time_h: 'Total time (h)',
      ax_money: 'Money',
      ax_wait: 'Wait (min)',
      ax_hour: 'Hour',
      reserve_line: 'reserve',
      elev_profile: 'elevation',
      now: 'now',
      wait_none: 'Hover a station or select a stop',
      wait_zero: 'Queue model off (no waiting)',
      pareto_others: 'other algorithms',

      kv_vehicle: 'Vehicle',
      kv_battery: 'Battery',
      kv_usable: 'Usable',
      kv_temperature: 'Temperature',
      kv_departure: 'Departure',
      kv_initial: 'Initial charge',
      kv_reserve: 'Reserve / arrival',
      kv_objective: 'Objective',
      kv_network: 'Network',
      kv_stations: 'Stations',
      kv_queues: 'Queues',
      kv_speeds: 'Speed levels',
      on: 'on',
      off: 'off',
      nodes_edges: '{n} nodes · {m} edges',

      sc_explored: 'nodes explored',
      sc_labels: 'labels',
      sc_key: 'key',
      sc_done: 'done',
      sc_of: '/',

      err_generic: 'Something went wrong: {m}',
      err_same_od: 'Origin and destination must differ',
      err_no_algo: 'Select at least one algorithm',
      err_no_data: 'No viewer data found.',
      generated_by: 'Generated with EVRoute {v} · approximate data, not for navigation',
      unit_km: 'km',
      unit_kmh: 'km/h',
      unit_m: 'm',
      unit_h: 'h',
      unit_min: 'min',
      unit_kwh: 'kWh',
      unit_kw: 'kW',
      day_plus: '+{d}d',

      reliability: 'Reliability',
      p_strand_m: 'P(strand)',
      p_short_m: 'P(arrive short)',
      th_p_strand: 'P(strand)',
      th_p_short: 'P(short)',
      rel_title: '{n} Monte Carlo worlds (uncertain consumption, random queues): strand {s}, reserve breach {b}, arrive below target {a}',
      rel_short_title: 'Probability of arriving below the required charge ({n} Monte Carlo worlds)',
      rel_strand_title: 'Probability that the battery runs empty on the way ({n} Monte Carlo worlds)',
      pareto_fastest: 'fastest',
      pareto_cheapest: 'cheapest',
      pareto_members: '{n} Pareto-optimal routes',
      pareto_dominated: 'dominated region',
      pareto_front: 'Pareto front',
      pareto_returned: 'returned plan',
      off_axis: 'off axis',
      race_more: '+{n}',
      now_marker: 'now',

      // charging tariffs (only shown for a scenario with a tariff / a plan with a hold)
      st_hold: 'Waiting for the tariff change',
      st_hold_offpeak: 'Waiting for the off-peak tariff',
      hold_tag: 'tariff change in {m}',
      hold_tag_offpeak: 'off-peak tariff in {m}',
      hold: 'Tariff hold',
      th_hold: 'Hold',
      hold_title: 'Voluntary wait for a cheaper price period (before joining the queue)',
      stop_hold: 'Waits {m} for the tariff change · joins the queue at {c}',
      stop_hold_offpeak: 'Waits {m} for the off-peak tariff · joins the queue at {c}',
      tariff: 'Tariff',
      tariff_busy: 'busy hours (utilisation ≥ {u})',
      tariff_high: 'above {p}: {f}',
      tariff_peak: 'peak {h}: {f}',
      tariff_flat: 'flat price',
      tariff_peak_short: 'peak',
      tariff_offpeak_short: 'off-peak',
      tariff_high_short: 'above {p}',
      tariff_line: '{p} price tier',
      tariff_peak_band: 'peak price',
      tariff_toggle: 'Charging tariff (80 % rule, peak hours)',
      tariff_illustrative: 'illustrative values, not real prices',
      kv_tariff: 'Tariff',
      kv_tariff_high: 'Price above {p}',
      kv_tariff_peak: 'Peak hours',
      data_credit: 'Data: {c}',
      data_licence: 'Licence: {l}.',
      tip_tariff: 'Tariff at {c}',
      // in-browser planner of the web site (Pyodide in a Web Worker) and its budgets
      label_limit: 'label budget reached',
      g_incomplete: 'possibly incomplete',
      boot_title: 'The planner runs in your browser',
      boot_lead: 'The first visit downloads about {mb} once (Python via Pyodide {v}, and EVRoute); later visits use the browser cache. Planning happens entirely in your browser; no data is sent to any server.',
      boot_download: 'Downloading Python',
      boot_install: 'Installing EVRoute',
      boot_scenario: 'Loading the scenario',
      boot_failed: 'The in-browser planner could not be loaded: {m}',
      boot_retry: 'Retry',
      planning_algo: 'Planning {a} ({i}/{n})…',
      restarting: '{a} ran over the time limit; restarting the planner…',
      budget_time_stop: 'stopped at the time limit of {s} s',
      budget_time_limit: 'reached the time limit of {s} s',
      budget_cancelled: 'stopped by you',
      budget_skipped: 'not run',
      budget_labels: 'label budget of {n} reached',
      budget_title: 'Possibly incomplete.',
      budget_hint: 'The web version limits the planners with label and time limits. To run them without these limits, install EVRoute and run:',
      time_limit_hit: 'time limit reached',
      not_planned: 'No results yet',
      btn_stop: 'Stop',
      copy: 'Copy',
      copied: 'Copied',
      boot_stale: 'The web site has been updated since this page was opened. Please reload the page.',
      boot_reload: 'Reload the page',
      boot_integrity: 'A downloaded file did not match its expected content and was not used ({m}).',
      boot_blocked: 'cdn.jsdelivr.net may be blocked by an ad blocker or a network filter; allow it and retry.',
      pick_hint_touch: 'Tap the map: the A or B switch sets which point you choose',
      plan_own: 'Plan your own trip',
      site_home: 'EVRoute web site',
      site_repo: 'GitHub repository',
      // scenario texts of the presets, as the data carries them (panels.js translates a known text only)
      meta_disclaimer_turkey: 'Approximate network and synthetic stations for research illustration; not for navigation.',
      meta_disclaimer_osm_turkey: 'OpenStreetMap roads and stations, operator list prices and assumed queues; research data, not for navigation.',
      meta_licence_osm_turkey: 'ODbL 1.0 (data), MIT (code)',
    },
  };

  /** Short algorithm explanations (tooltips); fall back to the payload text. */
  const ALGO_DESC = {
    tr: {
      naive: 'Bataryayı hesaba katmayan en hızlı yol: klasik navigasyonun yaptığı. Uzun yolculukta genellikle uygulanamaz.',
      greedy: 'Gerektiğinde şarj eden sürücü sezgiseli: en hızlı yolu izle, batarya yetmezse ulaşılabilir en iyi istasyona sap.',
      'station-graph': 'İstasyon meta-grafı üzerinde Dijkstra; her durakta sabit hedef şarja kadar şarj.',
      genetic: 'İstasyon ve hedef SoC dizileri üzerinde genetik algoritma (deterministik tohum).',
      bls: 'Batarya-genişletilmiş grafta kesin etiket belirleme (Dijkstra sırası); SoC ızgarası üzerinde optimum.',
      'bls-astar': 'BLS ile aynı optimum; tutarlı A* sezgiseli sayesinde çok daha az etiket.',
      cfp: 'Şarj fonksiyonu yayılımı: sürekli şarj miktarlarıyla kesin en hızlı rota (Baum ve ark.).',
      'cfp-astar': 'CFP ile aynı optimum; A* anahtarıyla çok daha az etiket.',
      pareto: 'Çok kriterli etiket belirleme: süre, ücret ve batarya yıpranması arasındaki Pareto cephesi.',
    },
  };

  /** Localised short titles of the baselines (the exact methods keep their acronyms). */
  const ALGO_TITLE = {
    tr: { naive: 'Naif', greedy: 'Açgözlü', 'station-graph': 'İstasyon grafı', genetic: 'Genetik' },
  };

  const LOCALE = { tr: 'tr-TR', en: 'en-GB' };
  const _fmtCache = new Map();

  const I = {
    lang: 'tr',
    STR,

    setLang(lang) {
      I.lang = STR[lang] ? lang : 'tr';
      document.documentElement.setAttribute('lang', I.lang);
    },

    /** Translate `key`, substituting {name} placeholders from `params`. */
    t(key, params) {
      let s = (STR[I.lang] && STR[I.lang][key]);
      if (s === undefined) s = STR.en[key];
      if (s === undefined) s = key;
      if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (params[k] !== undefined ? String(params[k]) : m));
      return s;
    },

    /** Short title of algorithm `name` in the current language (`fallback` = payload title). */
    algoTitle(name, fallback) {
      const local = ALGO_TITLE[I.lang] && ALGO_TITLE[I.lang][name];
      return local || fallback || name;
    },

    /** "1 stop" / "3 stops" (Turkish has no plural after numerals). */
    stops(n) {
      return n === 1 ? I.t('stops_1') : I.t('stops_n', { n: I.num(n, 0) });
    },

    algoDescription(info) {
      const local = ALGO_DESC[I.lang] && ALGO_DESC[I.lang][info.name];
      return local || info.description || '';
    },

    /** Translate the static markup below `root`. */
    apply(root) {
      root = root || document;
      for (const el of root.querySelectorAll('[data-i18n]')) el.textContent = I.t(el.dataset.i18n);
      for (const el of root.querySelectorAll('[data-i18n-aria]')) el.setAttribute('aria-label', I.t(el.dataset.i18nAria));
      for (const el of root.querySelectorAll('[data-i18n-title]')) el.setAttribute('title', I.t(el.dataset.i18nTitle));
    },

    _nf(opts) {
      const key = I.lang + JSON.stringify(opts);
      let f = _fmtCache.get(key);
      if (!f) {
        try { f = new Intl.NumberFormat(LOCALE[I.lang], opts); } catch (e) { f = new Intl.NumberFormat('en-GB', opts); }
        _fmtCache.set(key, f);
      }
      return f;
    },

    /** Number with exactly `digits` decimals ("–" for null/NaN). */
    num(x, digits) {
      if (x === null || x === undefined || !isFinite(x)) return '–';
      const d = digits === undefined ? 0 : digits;
      return I._nf({ minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
    },

    /** Percentage of a fraction: 0.8 -> "%80" (tr) / "80%" (en). */
    pct(frac, digits) {
      if (frac === null || frac === undefined || !isFinite(frac)) return '–';
      const d = digits || 0;
      return I._nf({ style: 'percent', minimumFractionDigits: d, maximumFractionDigits: d }).format(frac);
    },

    money(x, currency, digits) {
      if (x === null || x === undefined || !isFinite(x)) return '–';
      const d = digits === undefined ? 2 : digits;
      try {
        return I._nf({ style: 'currency', currency: currency || 'EUR', currencyDisplay: 'narrowSymbol', minimumFractionDigits: d, maximumFractionDigits: d }).format(x);
      } catch (e) {
        return `${I.num(x, d)} ${currency || ''}`.trim();
      }
    },

    /** State-of-health loss in percentage points: 0.0123 -> "%0,012" / "0.012%". */
    soh(pp) {
      if (pp === null || pp === undefined || !isFinite(pp)) return '–';
      return I.pct(pp / 100, Math.abs(pp) < 0.1 ? 3 : 2);
    },

    km(m, digits) {
      if (m === null || m === undefined || !isFinite(m)) return '–';
      const km = m / 1000;
      const d = digits === undefined ? (km < 10 ? 1 : 0) : digits;
      return `${I.num(km, d)} ${I.t('unit_km')}`;
    },

    /** 5025 s -> "1 sa 24 dk" / "1 h 24 min"; under an hour "24 dk". */
    duration(sec) {
      if (sec === null || sec === undefined || !isFinite(sec)) return '–';
      const totalMin = Math.round(sec / 60);
      const h = Math.floor(totalMin / 60), m = totalMin % 60;
      if (h > 0) return `${h} ${I.t('unit_h')} ${String(m).padStart(2, '0')} ${I.t('unit_min')}`;
      if (sec > 0 && sec < 30) return `<1 ${I.t('unit_min')}`;
      return `${m} ${I.t('unit_min')}`;
    },

    /** Short minutes: 754 s -> "13 dk"; under 10 min one decimal. */
    minutes(sec) {
      if (sec === null || sec === undefined || !isFinite(sec)) return '–';
      const m = sec / 60;
      return `${I.num(m, m < 9.95 && m > 0 ? 1 : 0)} ${I.t('unit_min')}`;
    },

    /** Trip seconds -> "8:07" (h:mm, rounded; `floor` for a running clock). */
    hm(sec, floor) {
      if (sec === null || sec === undefined || !isFinite(sec)) return '–';
      const totalMin = floor ? Math.floor(sec / 60 + 1e-9) : Math.round(sec / 60);
      return `${Math.floor(totalMin / 60)}:${String(totalMin % 60).padStart(2, '0')}`;
    },

    /** Seconds -> "8:06:41" (h:mm:ss) for precise comparisons. */
    hms(sec) {
      if (sec === null || sec === undefined || !isFinite(sec)) return '–';
      const s = Math.round(sec);
      return `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    },

    /** Seconds after midnight -> "HH:MM" (+1d when past midnight). */
    clock(sec, withDay) {
      if (!isFinite(sec)) return '–';
      const s = Math.floor(sec + 1e-6);
      const day = Math.floor(s / 86400);
      const t = ((s % 86400) + 86400) % 86400;
      const txt = `${String(Math.floor(t / 3600)).padStart(2, '0')}:${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}`;
      return withDay !== false && day > 0 ? `${txt} ${I.t('day_plus', { d: day })}` : txt;
    },
  };

  EVR.i18n = I;
})(window.EVR = window.EVR || {});
