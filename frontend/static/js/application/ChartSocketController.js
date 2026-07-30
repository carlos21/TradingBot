import { StreamingEventType } from '../domain/streamingLifecycle.js';

/**
 * Maps Socket.IO events to ChartController commands.
 * Depends only on the controller's public API and socket ports.
 *
 * Streaming-lifecycle socket events (gateway_started / platform_connected /
 * platform_disconnected / gateway_stopped / stream_stopped / stream_status)
 * are dispatched
 * into the shared StreamingLifecycleController state machine, which owns the
 * Start/Reconnect/Stop buttons and the connection overlay.
 */
export class ChartSocketController {
  constructor(socket, controller, domService, lifecycle) {
    this.socket = socket;
    this.controller = controller;
    this.dom = domService;
    this.lifecycle = lifecycle;
  }

  init() {
    this.socket.on('connect', () => console.log('[ChartSocketController] socket connected'));

    this.socket.on('bar', bar => {
      if (!this._matchesCurrentPair(bar)) return;
      if (!this.controller.historyReady) {
        this.controller.queueBar(bar);
        return;
      }
      this.controller.processBar(bar);
    });

    this.socket.on('indicator_update', data => {
      if (!this._matchesCurrentPair(data)) return;
      this.controller.handleIndicatorUpdate(data);
    });
    this.socket.on('trade_open', trade => {
      if (!this._matchesCurrentPair(trade)) return;
      this.controller.handleTradeOpen(trade);
    });
    this.socket.on('trade_close', trade => {
      if (!this._matchesCurrentPair(trade)) return;
      this.controller.handleTradeClose(trade);
    });
    this.socket.on('trade_update', update => {
      if (!this._matchesCurrentPair(update)) return;
      this.controller.handleTradeUpdate(update);
    });
    this.socket.on('trade_entry_update', update => {
      if (!this._matchesCurrentPair(update)) return;
      this.controller.handleTradeEntryUpdate(update);
    });

    this.socket.on('line_removed', data => {
      if (!this._matchesCurrentPair(data)) return;
      this.controller.handleLineRemoved(data.id);
    });

    this.socket.on('stream_end', () => {
      const win = this.dom.getWindow();
      if (win) win.__done = true;
    });

    this.socket.on('history_loaded', data => {
      console.log('[ChartSocketController] history_loaded received!', data);
      this.controller.setLiveMode(true);
      this.controller.handleHistoryReady(data);
    });

    this.socket.on('trading_ready', data => {
      console.log('[ChartSocketController] trading_ready received!', data);
      this.controller.setLiveMode(true);
      this.controller.handleHistoryReady(data);
    });

    this.socket.on('stream_status', data => {
      console.log('[ChartSocketController] stream_status received:', data);
      if (!data.playing) this.controller.setPlaying(false);
      if (data.live_mode) {
        this.controller.setLiveMode(true);
        if (!data.platform_connected) {
          this._setConnectionStatus(`Waiting for ${this._getPlatformLabel()} connection...`);
        }
      }
      this.lifecycle.dispatch({ type: StreamingEventType.STATUS_SYNC, ...data });
    });

    this.socket.on('gateway_started', () => {
      this._setConnectionStatus(`ZeroMQ gateway started. Launching ${this._getPlatformLabel()}...`);
      this.lifecycle.dispatch({ type: StreamingEventType.GATEWAY_STARTED });
    });

    this.socket.on('platform_connected', () => {
      this._setConnectionStatus('Connected! Loading chart...');
      this.lifecycle.dispatch({ type: StreamingEventType.PLATFORM_CONNECTED });
    });

    this.socket.on('platform_disconnected', () => {
      this.controller.setHistoryReady(false);
      this.controller.clearPendingBars();
      this._setConnectionStatus(`Lost connection — ${this._getPlatformLabel()} disconnected`);
      this.lifecycle.dispatch({ type: StreamingEventType.PLATFORM_DISCONNECTED });
    });

    this.socket.on('gateway_stopped', () => {
      this.controller.setHistoryReady(false);
      this.controller.clearPendingBars();
      this._setConnectionStatus('Streaming stopped');
      this.lifecycle.dispatch({ type: StreamingEventType.GATEWAY_STOPPED });
    });

    // Pair-scoped stop: only this instrument's session ended. Same UI reset
    // as gateway_stopped, but ignored when the event is for another pair
    // (another tab may still be streaming it).
    this.socket.on('stream_stopped', data => {
      if (!this._matchesCurrentPair(data)) return;
      this.controller.setHistoryReady(false);
      this.controller.clearPendingBars();
      this._setConnectionStatus('Streaming stopped');
      this.lifecycle.dispatch({ type: StreamingEventType.GATEWAY_STOPPED });
    });
  }

  _matchesCurrentPair(data) {
    if (!data || !data.pair) return true;
    return data.pair === this.controller.pair;
  }

  _setConnectionStatus(text) {
    const el = this.dom.getElementById('connectionStatus');
    if (el) el.textContent = text;
  }

  _getPlatformLabel() {
    const overlay = this.dom.getElementById('connectionOverlay');
    return overlay?.dataset.platformLabel || 'NinjaTrader';
  }
}
