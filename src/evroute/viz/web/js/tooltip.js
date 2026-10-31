/* EVRoute viewer -- tooltip.js
 *
 * One small tooltip for the whole page.  Any element with a `data-tip`
 * attribute (the text, already translated: static markup sets it through
 * data-i18n-tip="key", the panels set dataset.tip) shows it
 *
 *   * on mouse hover (after a short delay),
 *   * on keyboard focus (:focus-visible, at once),
 *   * on touch after a long press (the tap that ends it does not click).
 *
 * The bubble stays inside the viewport (above the element, else below,
 * shifted sideways when needed), follows the theme's colours, has no
 * animation with prefers-reduced-motion, and is announced: the focused (or
 * hovered) element gets aria-describedby while it is shown -- the control
 * itself when the tip belongs to its surrounding field.  Escape, scrolling, a click or leaving
 * the element hides it.  Native `title` tooltips are not used next to it
 * (they would show twice).
 */
(function (EVR) {
  'use strict';

  const U = EVR.util;
  const SHOW_DELAY_MS = 380;
  const LONG_PRESS_MS = 480;

  class Tooltip {
    constructor() {
      this.el = U.el('div', { class: 'ui-tip', id: 'ui-tip', role: 'tooltip', hidden: true });
      document.body.appendChild(this.el);
      this.target = null;
      this.owner = null;
      this._timer = 0;
      this._press = null;
      this._swallowClick = false;
      this._wire();
    }

    _wire() {
      const doc = document;
      doc.addEventListener('pointerover', (ev) => {
        if (ev.pointerType === 'touch') return;
        const t = tipTarget(ev.target);
        if (!t || t === this.target) return;
        this._schedule(t, SHOW_DELAY_MS);
      });
      doc.addEventListener('pointerout', (ev) => {
        if (ev.pointerType === 'touch') return;
        const t = tipTarget(ev.target);
        if (!t) return;
        const to = ev.relatedTarget;
        if (to && t.contains(to)) return;
        this.hide();
      });
      doc.addEventListener('focusin', (ev) => {
        const t = tipTarget(ev.target);
        if (!t) return;
        let visible = true;
        try { visible = ev.target.matches(':focus-visible'); } catch (e) { /* old browsers: always */ }
        if (visible) this.show(t, ev.target);
      });
      doc.addEventListener('focusout', () => this.hide());
      doc.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && this.target) this.hide(); }, true);
      window.addEventListener('scroll', () => this.hide(), true);
      doc.addEventListener('input', () => this.hide(), true);   // typing into a field: out of the way
      window.addEventListener('resize', () => this.hide());
      // touch: a long press shows the tip; the click that ends the press is swallowed
      doc.addEventListener('pointerdown', (ev) => {
        if (ev.pointerType !== 'touch') { if (this.target) this.hide(); return; }
        const t = tipTarget(ev.target);
        this._cancelPress();
        if (!t) { this.hide(); return; }
        const x = ev.clientX, y = ev.clientY;
        this._press = {
          x, y,
          timer: setTimeout(() => { this._swallowClick = true; this.show(t); }, LONG_PRESS_MS),
        };
      }, true);
      doc.addEventListener('pointermove', (ev) => {
        const p = this._press;
        if (p && Math.abs(ev.clientX - p.x) + Math.abs(ev.clientY - p.y) > 10) this._cancelPress();
      }, true);
      const up = () => {
        this._cancelPress();
        if (this._swallowClick) setTimeout(() => this.hide(), 1800);
      };
      doc.addEventListener('pointerup', up, true);
      doc.addEventListener('pointercancel', up, true);
      doc.addEventListener('click', (ev) => {
        if (this._swallowClick) {
          this._swallowClick = false;
          ev.preventDefault();
          ev.stopPropagation();
        }
      }, true);
      doc.addEventListener('contextmenu', (ev) => { if (this._swallowClick || this._press) ev.preventDefault(); }, true);
    }

    _cancelPress() {
      if (this._press) { clearTimeout(this._press.timer); this._press = null; }
    }

    _schedule(t, ms) {
      clearTimeout(this._timer);
      this._timer = setTimeout(() => this.show(t), ms);
    }

    /** Show the tip of `t`; `owner` (default `t`) is the element it describes (the focused control). */
    show(t, owner) {
      clearTimeout(this._timer);
      const text = t.getAttribute('data-tip');
      if (!text || !t.isConnected) { this.hide(); return; }
      const o = owner && owner.nodeType === 1 ? owner : t;
      if (this.owner && this.owner !== o) this._release();
      this.target = t;
      this.owner = o;
      this.el.textContent = text;
      this.el.hidden = false;
      const prev = o.getAttribute('aria-describedby');
      if (!prev || !prev.split(' ').includes('ui-tip')) {
        o.dataset.tipPrevDesc = prev || '';
        o.setAttribute('aria-describedby', prev ? `${prev} ui-tip` : 'ui-tip');
      }
      this._place(t);
      this.el.classList.add('show');
    }

    _place(t) {
      const r = t.getBoundingClientRect();
      const tip = this.el;
      tip.style.left = '0px';
      tip.style.top = '0px';
      tip.style.maxWidth = `${Math.min(320, window.innerWidth - 16)}px`;
      const w = tip.offsetWidth, h = tip.offsetHeight;
      const vw = document.documentElement.clientWidth || window.innerWidth;
      const vh = window.innerHeight;
      let top = r.top - h - 8;
      let below = false;
      if (top < 6) { top = r.bottom + 8; below = true; }
      if (below && top + h > vh - 6) top = Math.max(6, vh - h - 6);
      const left = U.clamp(r.left + r.width / 2 - w / 2, 8, Math.max(8, vw - w - 8));
      tip.style.left = `${Math.round(left)}px`;
      tip.style.top = `${Math.round(top)}px`;
      tip.dataset.side = below ? 'below' : 'above';
      tip.style.setProperty('--arrow-x', `${Math.round(U.clamp(r.left + r.width / 2 - left, 10, w - 10))}px`);
    }

    _release() {
      const o = this.owner;
      this.owner = null;
      if (!o) return;
      const prev = o.dataset.tipPrevDesc;
      if (prev) o.setAttribute('aria-describedby', prev); else o.removeAttribute('aria-describedby');
      delete o.dataset.tipPrevDesc;
    }

    hide() {
      clearTimeout(this._timer);
      if (!this.target) return;
      this._release();
      this.target = null;
      this.el.classList.remove('show');
      this.el.hidden = true;
    }

    /** Re-read the text of the element shown now (after a language switch). */
    refresh() {
      if (this.target) {
        if (!this.target.isConnected) this.hide();
        else this.show(this.target, this.owner);
      }
    }
  }

  function tipTarget(node) {
    const el = node && node.nodeType === 1 ? node : node && node.parentElement;
    if (!el || !el.closest || el.closest('.combo-list')) return null;   // an open picker list: no tip of its field
    return el.closest('[data-tip]');
  }

  /** Set (or clear) the tooltip text of `el`; returns `el`. */
  function setTip(el, text) {
    if (!el) return el;
    if (text) el.setAttribute('data-tip', text); else el.removeAttribute('data-tip');
    return el;
  }

  let instance = null;
  EVR.tooltip = {
    Tooltip,
    setTip,
    install() { if (!instance) instance = new Tooltip(); return instance; },
    get() { return instance; },
  };
})(window.EVR = window.EVR || {});
