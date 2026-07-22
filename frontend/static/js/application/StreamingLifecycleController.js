import { StreamingState, reduceStreamingState, streamingViewModel } from '../domain/streamingLifecycle.js';

const START_BUSY_HTML = `
  <svg class="animate-spin w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24">
    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
  </svg>
  <span>Starting…</span>
`;

const STOP_BUSY_HTML = '<span>Stopping…</span>';

/**
 * Owns the streaming-lifecycle state and renders its view model to the DOM.
 *
 * Single responsibility: given the current StreamingState, keep the Start /
 * Reconnect / Stop buttons and the connection overlay consistent with it.
 * Callers (click and socket controllers) only dispatch semantic events; they
 * never touch button visibility, disabled flags, or labels themselves.
 */
export class StreamingLifecycleController {
  constructor(dom) {
    this.dom = dom;
    this.state = StreamingState.IDLE;

    this.overlay = null;
    this.startBtn = null;
    this.reconnectBtn = null;
    this.stopBtn = null;

    this._normalStartHTML = null;
    this._normalStopHTML = null;
    this._startBusy = null;
    this._stopBusy = null;
    this._startSymbol = null;
  }

  init() {
    this.overlay = this.dom.getElementById('connectionOverlay');
    this.startBtn = this.dom.getElementById('startStreamingBtn');
    this.reconnectBtn = this.dom.getElementById('reconnectBtn');
    this.stopBtn = this.dom.getElementById('stopStreamingBtn');
    if (this.startBtn) this._normalStartHTML = this.startBtn.innerHTML;
    if (this.stopBtn) this._normalStopHTML = this.stopBtn.innerHTML;
  }

  getState() {
    return this.state;
  }

  /**
   * Dispatch a lifecycle event ({type: StreamingEventType, ...payload}),
   * transition the machine, and render the resulting view model.
   * Returns the new state.
   */
  dispatch(event) {
    this.state = reduceStreamingState(this.state, event);
    this._render();
    return this.state;
  }

  /** Update the instrument symbol shown on the Start button label. */
  setStartSymbol(symbol) {
    this._startSymbol = symbol;
    if (this._startBusy !== true) this._applyStartSymbol();
  }

  _render() {
    const vm = streamingViewModel(this.state);
    if (this.overlay) this.overlay.classList.toggle('hidden', !vm.overlayVisible);
    this._renderButton(this.startBtn, vm.startBtn, true);
    if (this.reconnectBtn) this.reconnectBtn.classList.toggle('hidden', !vm.reconnectVisible);
    this._renderButton(this.stopBtn, vm.stopBtn, false);
  }

  _renderButton(btn, vm, isStart) {
    if (!btn) return;
    btn.classList.toggle('hidden', !vm.visible);
    this._applyBusy(btn, vm.busy, isStart);
    btn.disabled = !vm.enabled;
    btn.classList.toggle('opacity-50', !vm.enabled);
    btn.classList.toggle('cursor-not-allowed', !vm.enabled);
  }

  _applyBusy(btn, busy, isStart) {
    const key = isStart ? '_startBusy' : '_stopBusy';
    if (this[key] === busy) return;
    this[key] = busy;
    if (busy) {
      btn.innerHTML = isStart ? START_BUSY_HTML : STOP_BUSY_HTML;
    } else {
      const normal = isStart ? this._normalStartHTML : this._normalStopHTML;
      if (normal != null) btn.innerHTML = normal;
      if (isStart) this._applyStartSymbol();
    }
  }

  _applyStartSymbol() {
    if (!this.startBtn || !this._startSymbol) return;
    const span = this.startBtn.querySelector('span');
    if (span) span.textContent = `Start Streaming ${this._startSymbol}`;
  }
}
