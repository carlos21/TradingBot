import { StreamingEventType } from '../domain/streamingLifecycle.js';
import {
  TradingMode,
  TRADING_MODE_STORAGE_KEY,
  normalizeTradingMode,
  tradingModeLabel,
} from '../domain/tradingMode.js';

/**
 * Owns the live-streaming lifecycle UI: Start Streaming, Reconnect, and
 * Stop Streaming buttons, plus the trading-mode (Live Trading / Simulation)
 * selector and the always-visible mode badge.
 *
 * Single responsibility: translate button clicks into streaming lifecycle
 * API calls and dispatch the resulting lifecycle events into the shared
 * StreamingLifecycleController state machine. Button visibility, disabled
 * flags, and labels are rendered exclusively by that state machine (also
 * driven by ChartSocketController socket events), so the controls can never
 * get stuck in an inconsistent state.
 */
export class StreamingControlsController {
  constructor(dom, notification, lifecycle, pair, storage = null) {
    this.dom = dom;
    this.notification = notification;
    this.lifecycle = lifecycle;
    // When known, Stop Streaming is scoped to this instrument so other tabs
    // streaming other pairs keep running. When undefined, Stop keeps the
    // legacy global-stop behavior (bare POST, no body).
    this.pair = pair || null;
    // Optional IStorage port used to persist the trading mode across reloads.
    this.storage = storage;

    this.startStreamingBtn = null;
    this.reconnectBtn = null;
    this.stopStreamingBtn = null;
    this.tradingModeSelect = null;
    this.tradingModeBadge = null;
    this._tradingMode = TradingMode.SIMULATION;

    /**
     * Optional hook called at the top of _startStreaming(). Return false to
     * abort the start (e.g. to navigate to another instrument instead).
     */
    this.beforeStart = null;
  }

  init() {
    this.startStreamingBtn = this.dom.getElementById('startStreamingBtn');
    this.reconnectBtn = this.dom.getElementById('reconnectBtn');
    this.stopStreamingBtn = this.dom.getElementById('stopStreamingBtn');
    this._initTradingMode();
    this._bindStartEvents();
    this._bindStopEvents();
  }

  _initTradingMode() {
    this.tradingModeSelect = this.dom.getElementById('tradingModeSelect');
    this.tradingModeBadge = this.dom.getElementById('tradingModeBadge');

    const stored = this.storage ? this.storage.getItem(TRADING_MODE_STORAGE_KEY) : null;
    this._tradingMode = normalizeTradingMode(stored);

    if (this.tradingModeSelect) {
      this.tradingModeSelect.value = this._tradingMode;
      this.dom.addEventListener(this.tradingModeSelect, 'change', () => {
        this._tradingMode = normalizeTradingMode(this.tradingModeSelect.value);
        if (this.storage) this.storage.setItem(TRADING_MODE_STORAGE_KEY, this._tradingMode);
        this._renderTradingModeBadge();
      });
    }
    this._renderTradingModeBadge();
  }

  _renderTradingModeBadge() {
    if (!this.tradingModeBadge) return;
    const isLive = this._tradingMode === TradingMode.LIVE;
    this.tradingModeBadge.textContent = tradingModeLabel(this._tradingMode);
    this.tradingModeBadge.className =
      'px-2 py-0.5 text-xs font-semibold rounded-full border ' +
      (isLive
        ? 'bg-rose-500/20 text-rose-400 border-rose-500/40'
        : 'bg-amber-500/20 text-amber-400 border-amber-500/40');
  }

  _postStreamStart() {
    return fetch('/api/stream/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trading_mode: this._tradingMode }),
    });
  }

  _setConnectionStatus(text) {
    const statusEl = this.dom.getElementById('connectionStatus');
    if (statusEl) statusEl.textContent = text;
  }

  async _startStreaming() {
    if (typeof this.beforeStart === 'function' && this.beforeStart() === false) return;
    this._setConnectionStatus('Starting ZeroMQ gateway… (' + tradingModeLabel(this._tradingMode) + ')');
    this.lifecycle.dispatch({ type: StreamingEventType.START_CLICKED });
    try {
      const resp = await this._postStreamStart();
      const data = await resp.json();
      if (!resp.ok) {
        this._setConnectionStatus('⚠️ ' + (data.message || 'Error starting stream'));
        this.lifecycle.dispatch({ type: StreamingEventType.START_FAILED });
      } else {
        this._setConnectionStatus(data.message || 'Starting…');
        if (data.status === 'already_connected') {
          // The platform is already up; no gateway_started event will follow,
          // so move the machine forward ourselves instead of leaving Start
          // disabled forever.
          this.lifecycle.dispatch({ type: StreamingEventType.PLATFORM_CONNECTED });
        }
        // Otherwise the 'gateway_started' socket event drives the next state.
      }
    } catch (err) {
      this._setConnectionStatus('Error: ' + err.message);
      this.lifecycle.dispatch({ type: StreamingEventType.START_FAILED });
    }
  }

  async _reconnect() {
    this._setConnectionStatus('Reconnecting… (' + tradingModeLabel(this._tradingMode) + ')');
    this.lifecycle.dispatch({ type: StreamingEventType.RECONNECT_CLICKED });
    try {
      const resp = await this._postStreamStart();
      const data = await resp.json();
      if (!resp.ok) {
        this._setConnectionStatus('⚠️ ' + (data.message || 'Error starting stream'));
        this.lifecycle.dispatch({ type: StreamingEventType.RECONNECT_FAILED });
      } else {
        this._setConnectionStatus(data.message || 'Starting…');
      }
    } catch (err) {
      this._setConnectionStatus('Error: ' + err.message);
      this.lifecycle.dispatch({ type: StreamingEventType.RECONNECT_FAILED });
    }
  }

  _bindStartEvents() {
    if (this.startStreamingBtn) {
      this.dom.addEventListener(this.startStreamingBtn, 'click', () => this._startStreaming());
    }

    if (this.reconnectBtn) {
      this.dom.addEventListener(this.reconnectBtn, 'click', () => this._reconnect());
    }
  }

  _bindStopEvents() {
    if (!this.stopStreamingBtn) return;
    this.dom.addEventListener(this.stopStreamingBtn, 'click', async () => {
      this.lifecycle.dispatch({ type: StreamingEventType.STOP_CLICKED });
      try {
        const options = { method: 'POST' };
        if (this.pair) {
          options.headers = { 'Content-Type': 'application/json' };
          options.body = JSON.stringify({ pair: this.pair });
        }
        const resp = await fetch('/api/stream/stop', options);
        const data = await resp.json();
        if (!resp.ok) {
          // The overlay (and its connectionStatus element) is hidden while
          // streaming, so surface stop failures via the notification port.
          this.notification.alert('Failed to stop streaming: ' + (data.message || resp.status));
          this.lifecycle.dispatch({ type: StreamingEventType.STOP_FAILED });
        }
        // Success path: the pair-scoped 'stream_stopped' socket event (or
        // the global 'gateway_stopped' when the last session stopped) drives
        // the UI back to IDLE via ChartSocketController.
      } catch (err) {
        this.notification.alert('Failed to stop streaming: ' + err.message);
        this.lifecycle.dispatch({ type: StreamingEventType.STOP_FAILED });
      }
    });
  }
}
