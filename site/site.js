/* EVRoute web site -- language (TR/EN) and theme switches of the landing and
 * examples pages.  The <head> of every page resolves both before the first
 * paint (inline snippet: ?lang=, then the saved choice, then the browser's
 * language); this file wires the buttons.  The choices are kept in
 * localStorage under the keys the viewer and the results dashboard use
 * ("evroute.lang", "evroute.theme").  Links into a viewer carry ?lang=; a
 * link to the results dashboard (data-keep-lang), which reads only the saved
 * choice, saves the language shown when it is followed.
 * No tracking, no network access.
 */
(function () {
  'use strict';

  const root = document.documentElement;
  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* storage blocked */ } },
  };
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function setLang(lang, save) {
    root.setAttribute('data-lang', lang);
    root.setAttribute('lang', lang);
    if (save) store.set('evroute.lang', lang);
    for (const b of document.querySelectorAll('[data-set-lang]')) b.setAttribute('aria-pressed', String(b.dataset.setLang === lang));
    for (const a of document.querySelectorAll('a[data-viewer]')) {
      const [path, hash] = a.dataset.viewer.split('#');   // e.g. "live/#everyday": the query goes before the hash
      a.setAttribute('href', `${path}?lang=${lang}${hash ? '#' + hash : ''}`);
    }
    const title = root.getAttribute(`data-title-${lang}`);
    if (title) document.title = title;
  }

  function applyTheme() {
    const pref = store.get('evroute.theme') || 'auto';
    const dark = pref === 'dark' || (pref !== 'light' && !!mq && mq.matches);
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    const b = document.getElementById('theme-toggle');
    if (b) b.setAttribute('aria-pressed', String(dark));
  }

  function toggleTheme() {
    const dark = root.getAttribute('data-theme') !== 'dark';
    // back to the system's choice: follow the system again
    store.set('evroute.theme', mq && dark === mq.matches ? 'auto' : dark ? 'dark' : 'light');
    applyTheme();
  }

  function boot() {
    setLang(root.getAttribute('data-lang') === 'tr' ? 'tr' : 'en', false);
    applyTheme();
    for (const b of document.querySelectorAll('[data-set-lang]')) b.addEventListener('click', () => setLang(b.dataset.setLang, true));
    for (const a of document.querySelectorAll('a[data-keep-lang]')) {
      a.addEventListener('click', () => store.set('evroute.lang', root.getAttribute('data-lang') === 'tr' ? 'tr' : 'en'));
    }
    const t = document.getElementById('theme-toggle');
    if (t) t.addEventListener('click', toggleTheme);
    if (mq && mq.addEventListener) mq.addEventListener('change', applyTheme);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
