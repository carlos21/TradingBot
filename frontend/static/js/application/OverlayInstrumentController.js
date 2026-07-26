/**
 * Owns the instrument picker on the "Ready to Trade" connection overlay.
 *
 * Each tab is pinned to one instrument via the `?pair=` URL param, so picking
 * a different instrument reloads this tab with `pair` replaced (all other
 * query params preserved). When the tab is pinned to the only catalog
 * instrument there is nothing to pick, so the static label is shown instead.
 * When the tab is pinned to nothing (no valid `?pair=`), the select is shown
 * with a placeholder and no preselection — choosing an instrument is what
 * pins the tab.
 */
export class OverlayInstrumentController {
  constructor(dom) {
    this.dom = dom;
    this.label = null;
    this.select = null;
  }

  init() {
    this.label = this.dom.getElementById('overlayInstrumentLabel');
    this.select = this.dom.getElementById('overlayInstrumentSelect');
    if (!this.select) return;
    this.dom.addEventListener(this.select, 'change', () => this._navigate(this.select.value));
  }

  setInstruments(instruments, activePair) {
    const list = Array.isArray(instruments) ? instruments : [];
    if (!this.select) return;
    const pinned = Boolean(activePair) && list.some(i => i.symbol === activePair);
    if (list.length === 0 || (pinned && list.length === 1)) {
      // Nothing to choose: keep the static label populated by main.js.
      this.select.classList.add('hidden');
      return;
    }
    this.select.innerHTML = '';
    if (!pinned) {
      const placeholder = this.dom.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Select instrument…';
      placeholder.disabled = true;
      placeholder.selected = true;
      this.select.appendChild(placeholder);
    }
    for (const inst of list) {
      const option = this.dom.createElement('option');
      option.value = inst.symbol;
      option.textContent = inst.full_name ? `${inst.symbol} — ${inst.full_name}` : inst.symbol;
      this.select.appendChild(option);
    }
    if (pinned) this.select.value = activePair;
    this.select.classList.remove('hidden');
    if (this.label) this.label.classList.add('hidden');
  }

  /** Enable/disable the picker (disabled while a start is in progress). */
  setEnabled(enabled) {
    if (this.select) this.select.disabled = !enabled;
  }

  _navigate(symbol) {
    if (!symbol) return;
    const location = this.dom.getLocation();
    const params = new URLSearchParams(location.search);
    params.set('pair', symbol);
    location.search = params.toString();
  }
}
