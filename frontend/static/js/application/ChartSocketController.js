/**
 * Maps Socket.IO events to ChartController commands.
 * Depends only on the controller's public API and socket ports.
 */
export class ChartSocketController {
  constructor(socket, controller, domService) {
    this.socket = socket;
    this.controller = controller;
    this.dom = domService;
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
      if (data.live_mode) this.controller.setLiveMode(true);
      this._updateOverlay(data);
    });

    this.socket.on('gateway_started', () => {
      this._setConnectionStatus(`ZeroMQ gateway started. Launching ${this._getPlatformLabel()}...`);
    });

    this.socket.on('platform_connected', () => {
      this._setOverlayVisible(false);
      this._showReconnectButton(false);
      this._setConnectionStatus('Connected! Loading chart...');
    });

    this.socket.on('platform_disconnected', () => {
      this.controller.setHistoryReady(false);
      this.controller.clearPendingBars();
      this._setOverlayVisible(true);
      this._setConnectionStatus(`Lost connection — ${this._getPlatformLabel()} disconnected`);
      this._showReconnectButton(true);
    });

    this.socket.on('gateway_stopped', () => {
      this.controller.setHistoryReady(false);
      this.controller.clearPendingBars();
      this._setOverlayVisible(true);
      this._setConnectionStatus('Streaming stopped');
      this._showReconnectButton(false);
    });
  }

  _updateOverlay(data) {
    if (data.live_mode) {
      if (data.platform_connected) {
        this._setOverlayVisible(false);
        this._showReconnectButton(false);
      } else {
        this._setOverlayVisible(true);
        this._setConnectionStatus(`Waiting for ${this._getPlatformLabel()} connection...`);
      }
    } else {
      this._setOverlayVisible(false);
    }
  }

  _setOverlayVisible(visible) {
    const overlay = this.dom.getElementById('connectionOverlay');
    if (!overlay) return;
    if (visible) overlay.classList.remove('hidden');
    else overlay.classList.add('hidden');
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

  _showReconnectButton(visible) {
    const btn = this.dom.getElementById('reconnectBtn');
    const startBtn = this.dom.getElementById('startStreamingBtn');
    if (btn) btn.classList.toggle('hidden', !visible);
    if (startBtn) startBtn.classList.toggle('hidden', visible);
  }
}
