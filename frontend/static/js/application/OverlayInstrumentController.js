/**
 * Owns the instrument picker on the "Ready to Trade" connection overlay.
 *
 * Each tab is pinned to one instrument via the `?pair=` URL param. Picking a
 * different instrument only records the pending selection and notifies
 * `onSelectionChange` — the page is NEVER reloaded. When the user clicks
 * Start Streaming, main.js hands the selection to InstrumentSwitchController,
 * which switches the tab to the new instrument in place (room, chart data,
 * labels, URL) while the streaming state machine shows its busy state.
 * When the tab is pinned to the only catalog instrument there is nothing to
 * pick, so the static label is shown instead.
 * When the tab is pinned to nothing (no valid `?pair=`), the select is shown
 * with a placeholder and no preselection — choosing an instrument enables the
 * Start button.
 */
export class OverlayInstrumentController {
  constructor(dom) {
    this.dom = dom;
    this.label = null;
    this.select = null;
    this._selectedSymbol = null;

    /** Optional listener called with the symbol each time the user picks one. */
    this.onSelectionChange = null;
  }

  init() {
    this.label = this.dom.getElementById('overlayInstrumentLabel');
    this.select = this.dom.getElementById('overlayInstrumentSelect');
    if (!this.select) return;
    this.dom.addEventListener(this.select, 'change', () => this._onChange(this.select.value));
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
    if (pinned) {
      this.select.value = activePair;
      this._selectedSymbol = activePair;
    }
    this.select.classList.remove('hidden');
    if (this.label) this.label.classList.add('hidden');
  }

  /** Enable/disable the picker (disabled while a start is in progress). */
  setEnabled(enabled) {
    if (this.select) this.select.disabled = !enabled;
  }

  /** The symbol currently selected in the picker (pinned symbol by default). */
  getSelectedSymbol() {
    return this._selectedSymbol;
  }

  _onChange(symbol) {
    if (!symbol) return;
    this._selectedSymbol = symbol;
    if (typeof this.onSelectionChange === 'function') {
      this.onSelectionChange(symbol);
    }
  }
}
