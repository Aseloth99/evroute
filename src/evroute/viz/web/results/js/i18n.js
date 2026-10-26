/* EVRoute results panel -- i18n.js
 *
 * Interface strings (Turkish and English) and locale-aware number
 * formatting.  The computed findings come with the data (both languages);
 * everything else that the page writes is here.  Numbers use the real minus
 * sign (U+2212) and Turkish conventions in Turkish (%4,7 · 1.234,5).
 */
(function (EVR) {
  'use strict';

  const STR = {
    tr: {
      skip: 'İçeriğe geç',
      print: 'Yazdır',
      language: 'Dil',
      theme: 'Tema',
      theme_light: 'Açık tema',
      theme_auto: 'Sistem teması',
      theme_dark: 'Koyu tema',
      sections: 'Bölümler',
      page_title: 'Deney sonuçları',
      overview: 'Genel bakış',
      overview_kicker: 'EVRoute · elektrikli araç rota planlama',
      overview_lead: 'Her bölümdeki bulgu, bu klasördeki sonuç dosyalarından hesaplanır; grafiklerin üzerine gelin, tabloyu açın ya da ham CSV dosyasını indirin.',
      n_experiments: '{n} deney',
      total_time: 'toplam {t}',
      generated: 'oluşturuldu {d}',
      checks_title: 'Kesinlik denetimleri',
      checks_lead: 'Kesin algoritmalar için matematiksel olarak doğru olması gereken ifadeler; her biri sonuç dosyalarındaki her örnekte ayrı ayrı denetlenir.',
      checks_all: 'Tümü geçti',
      checks_some: '{n} başarısız',
      checks_none: 'Bu çalıştırmada denetlenecek veri yok.',
      run_title: 'Çalıştırma',
      status_ok: 'tamam',
      status_failed: 'başarısız',
      status_missing: 'çalıştırılmadı',
      status_empty: 'veri yok',
      status_error: 'okunamadı',
      what_it_means: 'Ne anlama geliyor?',
      finding: 'Bulgu',
      table: 'Tablo',
      hide_table: 'Tabloyu gizle',
      download_csv: 'CSV indir',
      open_viewer: 'Etkileşimli görüntüleyici',
      missing_title: 'Bu deney bu klasörde yok',
      missing_body: 'Çalıştırmak için:',
      failed_body: 'Deney hata verdi:',
      empty_body: 'Deney sonuç dosyası üretmedi.',
      unknown_body: 'Bu deney panel tarafından tanınmıyor; ham sonuçlar tablo olarak gösteriliyor.',
      rows_truncated: 'İlk {n} satır gösteriliyor ({total} satırın).',
      no_data: 'Veri yok',
      case: 'Vaka',
      all_cases: 'Tüm vakalar',
      footer: 'EVRoute sonuç paneli · tek dosya, çevrimdışı çalışır · veriler: {src}',
      // generic labels
      trip_time: 'Yolculuk süresi',
      drive: 'Sürüş',
      queue: 'Kuyruk + bağlanma',
      charge: 'Şarj',
      stops: 'Durak',
      money: 'Şarj ücreti',
      runtime: 'Hesaplama süresi',
      settled: 'Kesinleşen etiket',
      energy: 'Net enerji',
      charged: 'Şarj edilen enerji',
      wear: 'Batarya yıpranması',
      infeasible: 'yolda kalır',
      best: 'en iyi',
      exact: 'kesin',
      heuristic: 'sezgisel',
      pairs_n: '{n} çift',
      algorithm: 'Algoritma',
      // case studies
      cs_excess: 'En iyi plana göre fazladan süre',
      cs_excess_ax: 'en hızlı plana göre ek süre',
      cs_breakdown: 'Süre dağılımı',
      cs_metrics: 'Durak, ücret ve hesaplama süresi',
      cs_table_hint: 'Seçili vaka',
      // scalability
      sc_runtime: 'Hesaplama süresi ve ağ boyutu',
      sc_settled: 'Kesinleşen etiketler',
      sc_speedup: 'A* hızlanması',
      ax_nodes: 'düğüm sayısı',
      nodes_n: '{n} düğüm',
      ax_runtime: 'ortalama süre [ms]',
      ax_settled: 'kesinleşen etiket',
      ax_speedup: 'hızlanma [×]',
      speedup_vs: '{a} / {b}',
      slope: 'eğim ≈ n^{b}',
      per_size: '{n} örnek',
      // optimality gap
      og_title: 'Sürekli optimuma (CFP) göre fark',
      ax_gap_pct: 'fark [%]',
      median: 'Medyan',
      mean: 'Ortalama',
      quartiles: 'Çeyrekler',
      p95: '%95',
      max: 'En büyük',
      min_: 'En küçük',
      optimal_n: 'optimal: {k}/{n}',
      infeasible_n: '{k} uygulanamaz',
      pair: 'Çift',
      opt_time: 'Optimum',
      // grid
      gr_gap: 'CFP\'ye göre fark ve ızgara adımı',
      gr_runtime: 'Hesaplama süresi ve ızgara adımı',
      ax_step: 'şarj ızgarası adımı [% SoC] — sağa doğru incelir',
      ax_gap_s: 'fark [sn]',
      targets_grid: 'ızgara',
      targets_breaks: 'ızgara + kırılma noktaları',
      zero_gap: 'fark 0 (CFP ile aynı)',
      step: 'Adım',
      // temperature
      te_time: 'Yolculuk süresi ve sıcaklık',
      te_energy: 'Enerji ve sıcaklık',
      ax_temp: 'ortam sıcaklığı [°C]',
      ax_time_h: 'yolculuk süresi',
      ax_energy: 'net enerji [kWh]',
      preconditioned: 'Ön ısıtmalı batarya',
      cold_battery: 'Soğuk batarya',
      // queue
      qu_saving: 'Kalkış saatine göre kazanç',
      qu_wait: 'Beklenen bekleme: kuyruk-kör ve kuyruk-farkında',
      ax_hour: 'kalkış saati',
      ax_saving: 'kazanç [dk]',
      ax_wait: 'bekleme [dk]',
      blind: 'Kuyruk-kör plan',
      aware: 'Kuyruk-farkında plan',
      saving: 'Kazanç',
      departure: 'Kalkış',
      // pareto
      pa_front: 'Süre ve ücret (nokta boyutu = yıpranma)',
      ax_money: 'şarj ücreti',
      fastest: 'en hızlı',
      cheapest: 'en ucuz',
      balanced: 'dengeli',
      front_members: '{n} plan',
      other_algos: 'Diğer algoritmalar (zaman amaçlı)',
      off_axis: 'grafik dışında',
      pa_empty: 'Uygulanabilir cephe yok',
      size_legend: 'yıpranma',
      member: 'Plan',
      // robustness
      ro_risk: 'Risk ve güvenlik katsayısı',
      ro_time: 'Planlanan ve gerçekleşen süre',
      ax_factor: 'planlama güvenlik katsayısı',
      ax_prob: 'olasılık [%]',
      p_strand: 'Yolda kalma',
      p_breach: 'Rezerv aşımı',
      p_short: 'Varışta eksik şarj',
      planned: 'Planlanan süre',
      realised: 'Gerçekleşen (medyan–%95)',
      ci95: '%95 güven aralığı',
      replays: '{n} Monte Carlo tekrarı',
      // eco
      ec_dist: 'Eko hızların kazancı (her nokta bir yolculuk)',
      ax_saving_s: 'kazanç [sn]',
      fixed: 'Tam hız',
      eco: 'Eko hızlar',
      slowed: 'Yavaşlatılan yol',
      // vehicles
      ve_time: 'Yolculuk süresi',
      ve_energy: 'Enerji ve ücret',
      vehicle: 'Araç',
      battery: 'Batarya',
      // generic table / units
      unit_ms: 'ms',
      unit_s: 'sn',
      unit_min: 'dk',
      unit_h: 'sa',
      unit_kwh: 'kWh',
    },
    en: {
      skip: 'Skip to content',
      print: 'Print',
      language: 'Language',
      theme: 'Theme',
      theme_light: 'Light theme',
      theme_auto: 'System theme',
      theme_dark: 'Dark theme',
      sections: 'Sections',
      page_title: 'Experiment results',
      overview: 'Overview',
      overview_kicker: 'EVRoute · electric-vehicle route planning',
      overview_lead: 'Every finding is computed from the result files in this folder; hover the charts, open the table or download the raw CSV.',
      n_experiments: '{n} experiments',
      total_time: '{t} in total',
      generated: 'generated {d}',
      checks_title: 'Exactness checks',
      checks_lead: 'Statements that must hold mathematically for exact algorithms, checked on every single instance in the result files.',
      checks_all: 'All passed',
      checks_some: '{n} failed',
      checks_none: 'Nothing to check in this run.',
      run_title: 'Run',
      status_ok: 'ok',
      status_failed: 'failed',
      status_missing: 'not run',
      status_empty: 'no data',
      status_error: 'unreadable',
      what_it_means: 'What it means',
      finding: 'Finding',
      table: 'Table',
      hide_table: 'Hide table',
      download_csv: 'Download CSV',
      open_viewer: 'Interactive viewer',
      missing_title: 'This experiment is not in this folder',
      missing_body: 'Run it with:',
      failed_body: 'The experiment failed:',
      empty_body: 'The experiment produced no result file.',
      unknown_body: 'The panel does not know this experiment; its raw results are shown as a table.',
      rows_truncated: 'Showing the first {n} of {total} rows.',
      no_data: 'No data',
      case: 'Case',
      all_cases: 'All cases',
      footer: 'EVRoute results panel · single file, works offline · data: {src}',
      trip_time: 'Trip time',
      drive: 'Driving',
      queue: 'Queue + plug-in',
      charge: 'Charging',
      stops: 'Stops',
      money: 'Charging cost',
      runtime: 'Runtime',
      settled: 'Settled labels',
      energy: 'Net energy',
      charged: 'Energy charged',
      wear: 'Battery wear',
      infeasible: 'strands',
      best: 'best',
      exact: 'exact',
      heuristic: 'heuristic',
      pairs_n: '{n} pairs',
      algorithm: 'Algorithm',
      cs_excess: 'Extra time over the best plan',
      cs_excess_ax: 'extra time over the fastest plan',
      cs_breakdown: 'Where the time goes',
      cs_metrics: 'Stops, cost and runtime',
      cs_table_hint: 'Selected case',
      sc_runtime: 'Runtime versus network size',
      sc_settled: 'Settled labels',
      sc_speedup: 'A* speed-up',
      ax_nodes: 'nodes',
      nodes_n: '{n} nodes',
      ax_runtime: 'mean runtime [ms]',
      ax_settled: 'settled labels',
      ax_speedup: 'speed-up [×]',
      speedup_vs: '{a} vs {b}',
      slope: 'slope ≈ n^{b}',
      per_size: '{n} instances',
      og_title: 'Gap to the continuous optimum (CFP)',
      ax_gap_pct: 'gap [%]',
      median: 'Median',
      mean: 'Mean',
      quartiles: 'Quartiles',
      p95: 'p95',
      max: 'Max',
      min_: 'Min',
      optimal_n: 'optimal: {k}/{n}',
      infeasible_n: '{k} infeasible',
      pair: 'Pair',
      opt_time: 'Optimum',
      gr_gap: 'Gap to CFP versus grid step',
      gr_runtime: 'Runtime versus grid step',
      ax_step: 'charging-grid step [% SoC] — finer to the right',
      ax_gap_s: 'gap [s]',
      targets_grid: 'grid',
      targets_breaks: 'grid + breakpoints',
      zero_gap: 'gap 0 (same as CFP)',
      step: 'Step',
      te_time: 'Trip time versus temperature',
      te_energy: 'Energy versus temperature',
      ax_temp: 'ambient temperature [°C]',
      ax_time_h: 'trip time',
      ax_energy: 'net energy [kWh]',
      preconditioned: 'Preconditioned battery',
      cold_battery: 'Cold battery',
      qu_saving: 'Saving by departure hour',
      qu_wait: 'Expected waiting: queue-blind versus queue-aware',
      ax_hour: 'departure hour',
      ax_saving: 'saving [min]',
      ax_wait: 'waiting [min]',
      blind: 'Queue-blind plan',
      aware: 'Queue-aware plan',
      saving: 'Saving',
      departure: 'Departure',
      pa_front: 'Time versus money (dot size = wear)',
      ax_money: 'charging cost',
      fastest: 'fastest',
      cheapest: 'cheapest',
      balanced: 'balanced',
      front_members: '{n} plans',
      other_algos: 'Other algorithms (time objective)',
      off_axis: 'off the chart',
      pa_empty: 'No feasible front',
      size_legend: 'wear',
      member: 'Plan',
      ro_risk: 'Risk versus safety factor',
      ro_time: 'Planned and realised trip time',
      ax_factor: 'planning safety factor',
      ax_prob: 'probability [%]',
      p_strand: 'Stranding',
      p_breach: 'Reserve breach',
      p_short: 'Short on arrival',
      planned: 'Planned time',
      realised: 'Realised (median–p95)',
      ci95: '95 % confidence band',
      replays: '{n} Monte Carlo replays',
      ec_dist: 'Saving of eco speeds (one dot per trip)',
      ax_saving_s: 'saving [s]',
      fixed: 'Full speed',
      eco: 'Eco speeds',
      slowed: 'Slowed roads',
      ve_time: 'Trip time',
      ve_energy: 'Energy and cost',
      vehicle: 'Vehicle',
      battery: 'Battery',
      unit_ms: 'ms',
      unit_s: 's',
      unit_min: 'min',
      unit_h: 'h',
      unit_kwh: 'kWh',
    },
  };

  const LOCALE = { tr: 'tr-TR', en: 'en-GB' };
  const SYM = { TRY: '₺', EUR: '€', USD: '$', GBP: '£' };
  const _nf = new Map();
  const finite = (x) => typeof x === 'number' && isFinite(x);

  const I = {
    lang: 'tr',
    STR,
    payload: null,

    setLang(lang) {
      I.lang = STR[lang] ? lang : 'tr';
      document.documentElement.setAttribute('lang', I.lang);
    },

    t(key, params) {
      let s = STR[I.lang][key];
      if (s === undefined) s = STR.en[key];
      if (s === undefined) s = key;
      if (params) s = s.replace(/\{(\w+)\}/g, (m, k) => (params[k] !== undefined ? String(params[k]) : m));
      return s;
    },

    /** Pick the current language from a {tr, en} object (or return a plain string). */
    tx(obj) {
      if (obj === null || obj === undefined) return '';
      if (typeof obj === 'string') return obj;
      return obj[I.lang] !== undefined ? obj[I.lang] : (obj.en !== undefined ? obj.en : '');
    },

    _fmt(opts) {
      const key = I.lang + JSON.stringify(opts);
      if (!_nf.has(key)) _nf.set(key, new Intl.NumberFormat(LOCALE[I.lang], opts));
      return _nf.get(key);
    },

    num(x, d) {
      if (!finite(x)) return '–';
      const digits = d === undefined ? 0 : d;
      return I._fmt({ minimumFractionDigits: digits, maximumFractionDigits: digits }).format(x).replace(/^-/, '−');
    },

    /** Value in percent units: 4.7 -> "%4,7" / "4.7%". */
    pct(x, d) {
      if (!finite(x)) return '–';
      const n = I.num(Math.abs(x), d === undefined ? 1 : d);
      const neg = x < 0 && n.replace(/[0.,]/g, '') !== '';
      const s = I.lang === 'tr' ? `%${n}` : `${n}%`;
      return neg ? `−${s}` : s;
    },

    /** Compact axis label: 1 200 -> "1,2 B" / "1.2K". */
    compact(x) {
      if (!finite(x)) return '–';
      if (Math.abs(x) < 1000) return I.num(x, Math.abs(x) < 1 && x !== 0 ? (Math.abs(x) < 0.1 ? 2 : 1) : (x % 1 ? 1 : 0));
      // en-US writes 1.2K / 3M (en-GB would write "3m", easily read as metres)
      const key = `compact-${I.lang}`;
      if (!_nf.has(key)) _nf.set(key, new Intl.NumberFormat(I.lang === 'en' ? 'en-US' : LOCALE[I.lang], { notation: 'compact', maximumFractionDigits: 1 }));
      return _nf.get(key).format(x).replace(/^-/, '−');
    },

    factor(x) {
      if (!finite(x)) return '–';
      return `${I.num(x, x < 10 ? 1 : 0)}×`;
    },

    /** Duration given in minutes: 7 s, 7.8 min, 8 h 05 min. */
    minutes(m) {
      if (!finite(m)) return '–';
      const neg = m < 0;
      const a = Math.abs(m);
      let out;
      if (a > 0 && a * 60 < 0.5) out = `<1 ${I.t('unit_s')}`;
      else if (a < 1) out = `${Math.round(a * 60)} ${I.t('unit_s')}`;
      else if (a < 60) out = `${I.num(a, a < 9.95 ? 1 : 0)} ${I.t('unit_min')}`;
      else {
        const tot = Math.round(a);
        out = `${Math.floor(tot / 60)} ${I.t('unit_h')} ${String(tot % 60).padStart(2, '0')} ${I.t('unit_min')}`;
      }
      return neg && !/^0 /.test(out) ? `−${out}` : out;
    },

    /** Minutes -> "8:05" (axis ticks). */
    hm(m) {
      if (!finite(m)) return '–';
      const tot = Math.round(m);
      return `${Math.floor(tot / 60)}:${String(tot % 60).padStart(2, '0')}`;
    },

    money(x, cur, d) {
      if (!finite(x)) return '–';
      const sym = SYM[cur] || cur || '';
      const n = I.num(x, d === undefined ? (Math.abs(x) >= 100 ? 0 : 2) : d);
      if (!sym) return n;
      return I.lang === 'tr' || sym.length > 1 ? `${n} ${sym}` : `${sym}${n}`;
    },

    temp(t) {
      if (!finite(t)) return '–';
      return `${I.num(t, t % 1 ? 1 : 0)} °C`;
    },

    ms(x) {
      if (!finite(x)) return '–';
      const whole = (v) => Math.abs(v - Math.round(v)) < 1e-9;
      if (x >= 1000) return `${I.num(x / 1000, x >= 10000 || whole(x / 1000) ? 0 : 1)} ${I.t('unit_s')}`;
      return `${I.num(x, x < 10 && !whole(x) ? 1 : 0)} ${I.t('unit_ms')}`;
    },

    soh(pp) {
      if (!finite(pp)) return '–';
      return I.pct(pp, Math.abs(pp) < 0.1 ? 3 : 2);
    },

    hour(h) {
      return `${String(Math.floor(h)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
    },

    date(iso) {
      try {
        return new Date(iso).toLocaleString(LOCALE[I.lang], { dateStyle: 'medium', timeStyle: 'short' });
      } catch (e) {
        return iso || '';
      }
    },

    /** Algorithm short title in the current language. */
    algo(name) {
      const a = I.payload && I.payload.theme && I.payload.theme.algorithms[name];
      return a ? I.tx(a.title) : name;
    },

    /** Translate the static markup below `root`. */
    apply(root) {
      for (const n of (root || document).querySelectorAll('[data-i18n]')) n.textContent = I.t(n.getAttribute('data-i18n'));
      for (const n of (root || document).querySelectorAll('[data-i18n-aria]')) n.setAttribute('aria-label', I.t(n.getAttribute('data-i18n-aria')));
      for (const n of (root || document).querySelectorAll('[data-i18n-title]')) n.setAttribute('title', I.t(n.getAttribute('data-i18n-title')));
    },
  };

  EVR.RI = I;
})(window.EVR = window.EVR || {});
