/**
 * Switches the tab's active instrument in place — no page reload.
 *
 * Single responsibility: given a new (pair, instrument), move every
 * instrument-scoped concern to it atomically:
 *   - the Socket.IO room (leave the old symbol's room first, so the tab is
 *     never in two rooms at once; rooms are per-symbol and per-sid, so other
 *     tabs are unaffected),
 *   - the chart (bars/trades/lines cleared and reloaded by ChartController),
 *   - the Start button symbol, stream-health panel, and accounts display
 *     (all filter per instrument),
 *   - the header/overlay labels,
 *   - the `?pair=` URL param (history.replaceState — a later manual refresh
 *     still pins the tab to the switched instrument).
 *
 * Depends only on injected ports/controllers; contains no DOM or framework
 * details of its own.
 */
export class InstrumentSwitchController {
  constructor({ socket, chartController, lifecycle, healthPanel, accountsDisplay, dom }) {
    this.socket = socket;
    this.chartController = chartController;
    this.lifecycle = lifecycle;
    this.healthPanel = healthPanel;
    this.accountsDisplay = accountsDisplay;
    this.dom = dom;
  }

  switchTo(pair, instrument, previousPair) {
    if (!pair || pair === previousPair) return;

    if (previousPair) this.socket.emit('leave_instrument', { pair: previousPair });
    this.socket.emit('join_instrument', { pair });

    this.chartController.setActiveInstrument(pair, instrument);
    this.lifecycle.setStartSymbol(pair);
    if (this.healthPanel) this.healthPanel.setPair(pair);
    if (this.accountsDisplay) this.accountsDisplay.setActiveSymbol(pair);

    this._updateLabels(pair, instrument);
    this._updateUrl(pair);
  }

  _updateLabels(pair, instrument) {
    const headerLabel = this.dom.getElementById('currentInstrumentLabel');
    if (headerLabel) headerLabel.textContent = instrument?.full_name || pair;
    const overlayLabel = this.dom.getElementById('overlayInstrumentLabel');
    if (overlayLabel) {
      overlayLabel.textContent = instrument?.full_name
        ? `${pair} — ${instrument.full_name}`
        : pair;
    }
  }

  _updateUrl(pair) {
    const win = this.dom.getWindow();
    if (!win?.history?.replaceState) return;
    const params = new URLSearchParams(win.location.search);
    params.set('pair', pair);
    const qs = params.toString();
    win.history.replaceState(null, '', win.location.pathname + (qs ? `?${qs}` : ''));
  }
}
