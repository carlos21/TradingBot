/**
 * Owns the header "Instruments ▾" dropdown menu.
 *
 * Single responsibility: render one entry per catalog instrument, each
 * opening `/?pair=SYMBOL` in a new tab (tabs are pinned to one instrument),
 * and handle menu open/close behavior (button toggle, outside click,
 * Escape key). The entry for the instrument this tab is pinned to gets a
 * `current` marker and `aria-current="true"`.
 */
export class InstrumentsMenuController {
  constructor(dom) {
    this.dom = dom;
    this.button = null;
    this.menu = null;
  }

  init() {
    this.button = this.dom.getElementById('instrumentsMenuBtn');
    this.menu = this.dom.getElementById('instrumentsMenu');
    if (!this.button || !this.menu) return;

    this.dom.addEventListener(this.button, 'click', (e) => {
      // Keep the document-level outside-click handler from seeing this
      // click and immediately closing the menu again.
      e.stopPropagation();
      this.toggle();
    });

    this.dom.addEventListener(this.dom.getDocument(), 'click', (e) => {
      if (this.menu.classList.contains('hidden')) return;
      if (!this.menu.contains(e.target)) this.close();
    });

    this.dom.addEventListener(this.dom.getDocument(), 'keydown', (e) => {
      if (e.key === 'Escape') this.close();
    });
  }

  setInstruments(instruments, currentSymbol) {
    if (!this.menu) return;
    this.menu.innerHTML = '';
    const list = Array.isArray(instruments) ? instruments : [];
    for (const inst of list) {
      this.menu.appendChild(this._buildEntry(inst, inst.symbol === currentSymbol));
    }
  }

  toggle() {
    if (this.menu) this.menu.classList.toggle('hidden');
  }

  close() {
    if (this.menu) this.menu.classList.add('hidden');
  }

  _buildEntry(inst, isCurrent) {
    const a = this.dom.createElement('a');
    a.href = `/?pair=${encodeURIComponent(inst.symbol)}`;
    a.target = '_blank';
    a.rel = 'noopener';
    a.className = 'block px-3 py-2 text-sm text-slate-300 hover:bg-surface-700 hover:text-slate-100 transition-colors';
    a.textContent = `Open ${inst.symbol} in new tab ↗`;
    if (isCurrent) {
      a.classList.add('current', 'text-accent-400');
      a.setAttribute('aria-current', 'true');
      a.textContent += ' (current)';
    }
    return a;
  }
}
