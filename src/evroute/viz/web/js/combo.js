/* EVRoute viewer -- combo.js
 *
 * Searchable pickers: a <select> becomes an accessible combobox (ARIA 1.2
 * "combobox with listbox popup" pattern).  The <select> stays in the page
 * as the source of truth -- its value, its options and its `change` event
 * are what the rest of the viewer reads and writes -- and is only hidden
 * from view and from assistive technology; the combobox mirrors it.
 *
 *   typing          filters the options (Turkish-aware, accent- and
 *                   case-insensitive: EVR.util.fold), best matches first
 *                   (the whole text, the start of the text, the start of a
 *                   word, anywhere, then the extra search text), the match
 *                   highlighted; a "no match" row when nothing fits
 *   ArrowDown/Up    open the list / move the active option
 *   Home/End        first / last option (while the list is open)
 *   Enter           choose the active option
 *   Escape          close the list and restore the chosen text
 *   mouse, touch    click (tap) an option; the chevron opens the list
 *
 * Choosing an option sets the <select>'s value and dispatches `change` on
 * it.  When code changes the <select> without an event, call sync().  The
 * list is positioned in the viewport (position: fixed), below the field or
 * above it when there is more room there, so a scrolling side panel never
 * clips it.
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const I = EVR.i18n;
  const MAX_ROWS = 200;
  let uid = 0;

  class Combo {
    /**
     * `select`: the <select> to enhance.  opts.describe(value, option) may
     * return {sub, badge, badgeClass} shown under / beside an option (the
     * vehicle list: battery, DC power, chemistry); opts.extra(value) more
     * text that searching matches (not shown).
     */
    constructor(select, opts) {
      this.select = select;
      this.opts = opts || {};
      this.id = select.id || `combo${++uid}`;
      this.items = [];
      this.shown = [];
      this.active = -1;
      this.open = false;
      const label = select.id ? document.querySelector(`label[for="${select.id}"]`) : null;
      this.input = U.el('input', {
        type: 'text', id: `${this.id}-combo`, class: 'combo-input', role: 'combobox',
        autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
        'aria-autocomplete': 'list', 'aria-expanded': 'false', 'aria-controls': `${this.id}-list`,
      });
      this.toggle = U.el('button', { type: 'button', class: 'combo-toggle', tabindex: '-1', 'aria-hidden': 'true' },
        U.svg('M6 9l6 6 6-6'));
      this.list = U.el('ul', { class: 'combo-list', id: `${this.id}-list`, role: 'listbox', hidden: true });
      this.wrap = U.el('div', { class: 'combo' }, this.input, this.toggle, this.list);
      select.classList.add('combo-native');
      select.setAttribute('tabindex', '-1');
      select.setAttribute('aria-hidden', 'true');
      select.after(this.wrap);
      if (label) {
        label.setAttribute('for', this.input.id);
        if (!label.id) label.id = `${this.id}-label`;
        this.list.setAttribute('aria-labelledby', label.id);
      }
      this._wire();
      this.sync();
    }

    _wire() {
      const inp = this.input;
      // select the text, so that typing replaces it (backwards: the start of a long name stays in view)
      inp.addEventListener('focus', () => { try { inp.setSelectionRange(0, inp.value.length, 'backward'); } catch (e) { inp.select(); } });
      inp.addEventListener('click', () => { if (!this.open) this.openList(''); });
      inp.addEventListener('input', () => this.openList(inp.value));
      inp.addEventListener('keydown', (ev) => this._key(ev));
      inp.addEventListener('blur', () => {
        // a click into the list keeps the focus (mousedown prevented): this is a real blur
        setTimeout(() => { if (document.activeElement !== inp) this.close(true); }, 0);
      });
      this.toggle.addEventListener('mousedown', (ev) => ev.preventDefault());
      this.toggle.addEventListener('click', () => {
        if (this.open) this.close(true);
        else { inp.focus(); this.openList(''); }
      });
      this.list.addEventListener('mousedown', (ev) => ev.preventDefault());
      this.list.addEventListener('click', (ev) => {
        const li = ev.target.closest('li[data-k]');
        if (li) this.choose(+li.dataset.k);
      });
      this.list.addEventListener('pointermove', (ev) => {
        const li = ev.target.closest('li[data-k]');
        if (li) this._setActive(this.shown.indexOf(+li.dataset.k), false);
      });
      this.select.addEventListener('change', () => this.sync());
      // the list follows its field when a panel scrolls or the window resizes
      const follow = (ev) => { if (this.open && !(ev && ev.target === this.list)) this._place(); };
      window.addEventListener('scroll', follow, true);
      window.addEventListener('resize', follow);
    }

    /** Put the open list under the field (or above it when there is more room above). */
    _place() {
      const r = this.input.getBoundingClientRect();
      const vh = window.innerHeight;
      const below = vh - r.bottom - 8, above = r.top - 8;
      const up = below < 220 && above > below;
      const room = Math.max(120, Math.min(320, up ? above : below));
      const st = this.list.style;
      st.left = `${Math.round(r.left)}px`;
      st.width = `${Math.round(Math.max(r.width, Math.min(320, window.innerWidth - r.left - 8)))}px`;
      st.maxHeight = `${Math.round(room)}px`;
      if (up) { st.top = 'auto'; st.bottom = `${Math.round(vh - r.top + 4)}px`; } else { st.bottom = 'auto'; st.top = `${Math.round(r.bottom + 4)}px`; }
      this.list.dataset.side = up ? 'above' : 'below';
    }

    _key(ev) {
      const k = ev.key;
      if (k === 'ArrowDown' || k === 'ArrowUp') {
        ev.preventDefault();
        if (!this.open) { this.openList(''); this._setActive(this._selectedRow(), true); return; }
        const n = this.shown.length;
        if (!n) return;
        const cur = this.active < 0 ? (k === 'ArrowDown' ? -1 : n) : this.active;
        this._setActive(U.clamp(cur + (k === 'ArrowDown' ? 1 : -1), 0, n - 1), true);
      } else if ((k === 'Home' || k === 'End') && this.open && this.shown.length) {
        ev.preventDefault();
        this._setActive(k === 'Home' ? 0 : this.shown.length - 1, true);
      } else if (k === 'Enter') {
        if (this.open) {
          ev.preventDefault();
          ev.stopPropagation();   // not the form's "Enter plans"
          if (this.active >= 0) this.choose(this.shown[this.active]);
          else if (this.shown.length === 1) this.choose(this.shown[0]);
        }
      } else if (k === 'Escape') {
        if (this.open) { ev.preventDefault(); ev.stopPropagation(); this.close(true); }
      } else if (k === 'Tab') {
        if (this.open) this.close(true);
      }
    }

    /** Rebuild from the <select>'s options and show the chosen one. */
    sync() {
      const d = this.opts.describe;
      const x = this.opts.extra;
      this.items = Array.from(this.select.options).map((o, k) => {
        const info = d ? d(o.value, o) || {} : {};
        const text = o.textContent;
        return { k, value: o.value, text, info, fm: U.foldMap(text), extra: U.fold(`${x ? x(o.value, o) || '' : ''} ${info.sub || ''} ${info.badge || ''}`) };
      });
      const sel = this.select.selectedIndex;
      this.input.value = sel >= 0 && this.items[sel] ? this.items[sel].text : '';
      this.input.disabled = this.select.disabled;
      if (this.open) this.openList(this.input.value === this._chosenText() ? '' : this.input.value);
    }

    _chosenText() {
      const i = this.select.selectedIndex;
      return i >= 0 && this.items[i] ? this.items[i].text : '';
    }

    _selectedRow() {
      const i = this.shown.indexOf(this.select.selectedIndex);
      return i >= 0 ? i : 0;
    }

    /** Filter by `query` and show the list. */
    openList(query) {
      const q = U.fold(query || '').trim();
      let rows;
      if (!q) {
        rows = this.items.map((it) => ({ it, rank: 0, at: -1 }));
      } else {
        rows = [];
        for (const it of this.items) {
          const at = it.fm.f.indexOf(q);
          if (at === 0) rows.push({ it, rank: it.fm.f.length === q.length ? -1 : 0, at });   // the whole text first
          else if (at > 0) {
            // a word start ("bul" does not rank "İstanbul" first, "ist" does)
            let ws = -1;
            for (let p = at; p >= 0; p = it.fm.f.indexOf(q, p + 1)) {
              if (p === 0 || /[^a-z0-9]/.test(it.fm.f[p - 1])) { ws = p; break; }
              if (p + 1 >= it.fm.f.length) break;
            }
            rows.push(ws >= 0 ? { it, rank: 1, at: ws } : { it, rank: 2, at });
          } else if (it.extra.includes(q)) rows.push({ it, rank: 3, at: -1 });
        }
        rows.sort((a, b) => a.rank - b.rank || a.it.k - b.it.k);
      }
      this.shown = rows.map((r) => r.it.k);
      const lis = rows.slice(0, MAX_ROWS).map((r) => this._row(r, q));
      if (rows.length > MAX_ROWS) {
        lis.push(U.el('li', { class: 'combo-more', role: 'presentation', text: I.t('combo_more', { n: I.num(rows.length - MAX_ROWS, 0) }) }));
        this.shown.length = MAX_ROWS;
      }
      if (!rows.length) lis.push(U.el('li', { class: 'combo-empty', role: 'option', 'aria-disabled': 'true', 'aria-selected': 'false', text: I.t('combo_no_match') }));
      U.setChildren(this.list, lis);
      this.list.hidden = false;
      this.open = true;
      this._place();
      this.wrap.classList.add('open');
      this.input.setAttribute('aria-expanded', 'true');
      this._setActive(q ? (this.shown.length ? 0 : -1) : -1, false);
      if (!q) {
        const li = this.list.querySelector('[aria-selected="true"]');
        if (li) li.scrollIntoView({ block: 'nearest' });
      }
    }

    _row(r, q) {
      const it = r.it;
      const li = U.el('li', {
        role: 'option', id: `${this.id}-opt-${it.k}`, 'data-k': String(it.k),
        'aria-selected': String(it.k === this.select.selectedIndex),
      });
      const main = U.el('span', { class: 'combo-main' });
      if (q && r.at >= 0) {
        const a = it.fm.map[r.at], b = it.fm.map[r.at + q.length];
        main.append(it.text.slice(0, a), U.el('mark', { text: it.text.slice(a, b) }), it.text.slice(b));
      } else {
        main.textContent = it.text;
      }
      const kids = [main];
      if (it.info.badge) kids.push(U.el('span', { class: `chem-badge ${it.info.badgeClass || ''}`, text: it.info.badge }));
      if (it.info.sub) kids.push(U.el('span', { class: 'combo-sub', text: it.info.sub }));
      li.append(...kids);
      return li;
    }

    _setActive(j, scroll) {
      const prev = this.list.querySelector('.active');
      if (prev) prev.classList.remove('active');
      this.active = j;
      if (j < 0 || j >= this.shown.length) {
        this.active = -1;
        this.input.removeAttribute('aria-activedescendant');
        return;
      }
      const li = this.list.querySelector(`li[data-k="${this.shown[j]}"]`);
      if (!li) return;
      li.classList.add('active');
      this.input.setAttribute('aria-activedescendant', li.id);
      if (scroll) li.scrollIntoView({ block: 'nearest' });
    }

    choose(k) {
      const it = this.items[k];
      if (!it) return;
      const changed = this.select.value !== it.value;
      this.select.value = it.value;
      this.close(true);
      if (changed) this.select.dispatchEvent(new Event('change', { bubbles: true }));
    }

    close(restore) {
      this.open = false;
      this.list.hidden = true;
      this.wrap.classList.remove('open');
      this.input.setAttribute('aria-expanded', 'false');
      this.input.removeAttribute('aria-activedescendant');
      this.active = -1;
      if (restore) this.input.value = this._chosenText();
    }
  }

  EVR.combo = { Combo };
})(window.EVR = window.EVR || {});
