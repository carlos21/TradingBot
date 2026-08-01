import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ChartSocketController } from '../../application/ChartSocketController.js';
import { StreamingLifecycleController } from '../../application/StreamingLifecycleController.js';
import { StreamingState as S } from '../../domain/streamingLifecycle.js';
import { FakeSocket } from '../fakes/FakeSocket.js';
import { FakeDomService } from '../fakes/FakeDomService.js';

function setupDocument() {
  document.body.innerHTML = `
    <div id="connectionOverlay" class="hidden" data-platform-label="MetaTrader">Overlay</div>
    <div id="connectionStatus">Idle</div>
    <button id="reconnectBtn" class="hidden">Reconnect</button>
    <button id="startStreamingBtn">Start Streaming</button>
    <button id="stopStreamingBtn" class="hidden">Stop Streaming</button>
  `;
  return { doc: document, win: window };
}

function buildController(doc, win) {
  const socket = new FakeSocket();
  const dom = new FakeDomService(doc, win);
  const controller = {
    pair: 'MNQ',
    historyReady: false,
    queueBar: vi.fn(),
    processBar: vi.fn(),
    handleIndicatorUpdate: vi.fn(),
    handleTradeOpen: vi.fn(),
    handleTradeClose: vi.fn(),
    handleTradeUpdate: vi.fn(),
    handleTradeEntryUpdate: vi.fn(),
    handleLineRemoved: vi.fn(),
    setLiveMode: vi.fn(),
    setPlaying: vi.fn(),
    setHistoryReady: vi.fn(),
    handleHistoryReady: vi.fn(),
    clearPendingBars: vi.fn(),
  };
  const lifecycle = new StreamingLifecycleController(dom);
  lifecycle.init();
  const socketController = new ChartSocketController(socket, controller, dom, lifecycle);
  socketController.init();
  return { socket, controller, dom, lifecycle, socketController };
}

describe('ChartSocketController', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    vi.restoreAllMocks();
  });

  it('logs socket connect', () => {
    const { socket } = buildController(...Object.values(setupDocument()));
    const consoleLog = vi.spyOn(console, 'log').mockImplementation(() => {});
    socket.trigger('connect');
    expect(consoleLog).toHaveBeenCalledWith('[ChartSocketController] socket connected');
    consoleLog.mockRestore();
  });

  it('queues bar when history is not ready', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    const bar = { time: 1 };
    socket.trigger('bar', bar);
    expect(controller.queueBar).toHaveBeenCalledWith(bar);
    expect(controller.processBar).not.toHaveBeenCalled();
  });

  it('processes bar when history is ready', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    controller.historyReady = true;
    const bar = { time: 1 };
    socket.trigger('bar', bar);
    expect(controller.processBar).toHaveBeenCalledWith(bar);
    expect(controller.queueBar).not.toHaveBeenCalled();
  });

  it('routes indicator_update to controller', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    const data = { tf: '1m', time: 1, tsi: 10, signal: 5 };
    socket.trigger('indicator_update', data);
    expect(controller.handleIndicatorUpdate).toHaveBeenCalledWith(data);
  });

  it('routes trade events to controller', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    socket.trigger('trade_open', { trade_id: 't1' });
    expect(controller.handleTradeOpen).toHaveBeenCalledWith({ trade_id: 't1' });

    socket.trigger('trade_close', { trade_id: 't1' });
    expect(controller.handleTradeClose).toHaveBeenCalledWith({ trade_id: 't1' });

    socket.trigger('trade_update', { trade_id: 't1', stop_loss: 99 });
    expect(controller.handleTradeUpdate).toHaveBeenCalledWith({ trade_id: 't1', stop_loss: 99 });

    socket.trigger('trade_entry_update', { trade_id: 't1', entry_price: 100 });
    expect(controller.handleTradeEntryUpdate).toHaveBeenCalledWith({ trade_id: 't1', entry_price: 100 });
  });

  it('routes line_removed to controller', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    socket.trigger('line_removed', { id: 7 });
    expect(controller.handleLineRemoved).toHaveBeenCalledWith(7);
  });

  describe('instrument filtering', () => {
    it('ignores bar for a different instrument', () => {
      const { socket, controller } = buildController(...Object.values(setupDocument()));
      controller.historyReady = true;
      socket.trigger('bar', { pair: 'ES', time: 1 });
      expect(controller.processBar).not.toHaveBeenCalled();
      expect(controller.queueBar).not.toHaveBeenCalled();
    });

    it('processes bar without a pair for backward compatibility', () => {
      const { socket, controller } = buildController(...Object.values(setupDocument()));
      controller.historyReady = true;
      const bar = { time: 1 };
      socket.trigger('bar', bar);
      expect(controller.processBar).toHaveBeenCalledWith(bar);
    });

    it('ignores indicator_update for a different instrument', () => {
      const { socket, controller } = buildController(...Object.values(setupDocument()));
      socket.trigger('indicator_update', { pair: 'ES', time: 1, tsi: 10, signal: 5 });
      expect(controller.handleIndicatorUpdate).not.toHaveBeenCalled();
    });

    it('ignores trade events for a different instrument', () => {
      const { socket, controller } = buildController(...Object.values(setupDocument()));
      socket.trigger('trade_open', { pair: 'ES', trade_id: 't1' });
      socket.trigger('trade_close', { pair: 'ES', trade_id: 't1' });
      socket.trigger('trade_update', { pair: 'ES', trade_id: 't1', stop_loss: 99 });
      socket.trigger('trade_entry_update', { pair: 'ES', trade_id: 't1', entry_price: 100 });
      expect(controller.handleTradeOpen).not.toHaveBeenCalled();
      expect(controller.handleTradeClose).not.toHaveBeenCalled();
      expect(controller.handleTradeUpdate).not.toHaveBeenCalled();
      expect(controller.handleTradeEntryUpdate).not.toHaveBeenCalled();
    });

    it('ignores line_removed for a different instrument', () => {
      const { socket, controller } = buildController(...Object.values(setupDocument()));
      socket.trigger('line_removed', { pair: 'ES', id: 7 });
      expect(controller.handleLineRemoved).not.toHaveBeenCalled();
    });

    it('processes events for the current instrument', () => {
      const { socket, controller } = buildController(...Object.values(setupDocument()));
      controller.historyReady = true;
      socket.trigger('bar', { pair: 'MNQ', time: 1 });
      socket.trigger('indicator_update', { pair: 'MNQ', time: 1, tsi: 10, signal: 5 });
      socket.trigger('trade_open', { pair: 'MNQ', trade_id: 't1' });
      socket.trigger('line_removed', { pair: 'MNQ', id: 7 });
      expect(controller.processBar).toHaveBeenCalled();
      expect(controller.handleIndicatorUpdate).toHaveBeenCalled();
      expect(controller.handleTradeOpen).toHaveBeenCalled();
      expect(controller.handleLineRemoved).toHaveBeenCalledWith(7);
    });
  });

  it('sets window __done on stream_end', () => {
    const { doc, win } = setupDocument();
    const { socket } = buildController(doc, win);
    socket.trigger('stream_end');
    expect(win.__done).toBe(true);
  });

  it('sets live mode and triggers history ready on history_loaded', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    const data = { pair: 'MNQ' };
    socket.trigger('history_loaded', data);
    expect(controller.setLiveMode).toHaveBeenCalledWith(true);
    expect(controller.handleHistoryReady).toHaveBeenCalledWith(data);
  });

  it('sets live mode and triggers history ready on trading_ready', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    const data = { pair: 'MNQ' };
    socket.trigger('trading_ready', data);
    expect(controller.setLiveMode).toHaveBeenCalledWith(true);
    expect(controller.handleHistoryReady).toHaveBeenCalledWith(data);
  });

  it('updates overlay on stream_status', () => {
    const { doc } = setupDocument();
    const { socket } = buildController(doc, window);

    socket.trigger('stream_status', { playing: false, live_mode: true, platform_connected: true });

    const overlay = doc.getElementById('connectionOverlay');
    expect(overlay.classList.contains('hidden')).toBe(true);
  });

  it('shows waiting overlay when live but platform not connected', () => {
    const { doc } = setupDocument();
    const { socket } = buildController(doc, window);

    socket.trigger('stream_status', { playing: true, live_mode: true, platform_connected: false });

    const overlay = doc.getElementById('connectionOverlay');
    const status = doc.getElementById('connectionStatus');
    expect(overlay.classList.contains('hidden')).toBe(false);
    expect(status.textContent).toContain('Waiting for MetaTrader');
  });

  it('hides overlay when not live', () => {
    const { doc } = setupDocument();
    const { socket } = buildController(doc, window);

    socket.trigger('stream_status', { playing: true, live_mode: false });

    expect(doc.getElementById('connectionOverlay').classList.contains('hidden')).toBe(true);
  });

  it('pauses controller when stream_status reports not playing', () => {
    const { socket, controller } = buildController(...Object.values(setupDocument()));
    socket.trigger('stream_status', { playing: false, live_mode: false });
    expect(controller.setPlaying).toHaveBeenCalledWith(false);
  });

  it('does not show overlay for playback-only stream_status without live_mode', () => {
    const { doc, win } = setupDocument();
    const { socket, lifecycle } = buildController(doc, win);
    // Simulate CSV replay page load: status sync hides the overlay.
    socket.trigger('stream_status', { playing: false, live_mode: false });
    expect(lifecycle.getState()).toBe(S.INACTIVE);

    // Runner emits start_stream, which broadcasts { playing: true } only.
    socket.trigger('stream_status', { playing: true });

    expect(lifecycle.getState()).toBe(S.INACTIVE);
    expect(doc.getElementById('connectionOverlay').classList.contains('hidden')).toBe(true);
  });

  it('shows gateway started status and hides reconnect', () => {
    const { doc } = setupDocument();
    const { socket } = buildController(doc, window);

    socket.trigger('gateway_started');

    expect(doc.getElementById('connectionStatus').textContent).toContain('ZeroMQ gateway started');
    expect(doc.getElementById('reconnectBtn').classList.contains('hidden')).toBe(true);
  });

  it('shows connected status and hides overlay on platform_connected', () => {
    const { doc } = setupDocument();
    const { socket } = buildController(doc, window);

    socket.trigger('platform_connected');

    expect(doc.getElementById('connectionOverlay').classList.contains('hidden')).toBe(true);
    expect(doc.getElementById('connectionStatus').textContent).toBe('Connected! Loading chart...');
    expect(doc.getElementById('reconnectBtn').classList.contains('hidden')).toBe(true);
    expect(doc.getElementById('startStreamingBtn').classList.contains('hidden')).toBe(false);
  });

  it('shows disconnected status and overlay with reconnect button', () => {
    const { doc } = setupDocument();
    const { socket, controller } = buildController(doc, window);

    // A disconnect can only happen while streaming.
    socket.trigger('platform_connected');
    socket.trigger('platform_disconnected');

    expect(controller.setHistoryReady).toHaveBeenCalledWith(false);
    expect(controller.clearPendingBars).toHaveBeenCalled();
    expect(doc.getElementById('connectionOverlay').classList.contains('hidden')).toBe(false);
    expect(doc.getElementById('connectionStatus').textContent).toContain('Lost connection');
    expect(doc.getElementById('reconnectBtn').classList.contains('hidden')).toBe(false);
    expect(doc.getElementById('startStreamingBtn').classList.contains('hidden')).toBe(true);
  });

  it('shows stopped status and hides reconnect', () => {
    const { doc } = setupDocument();
    const { socket, controller } = buildController(doc, window);

    socket.trigger('gateway_stopped');

    expect(controller.setHistoryReady).toHaveBeenCalledWith(false);
    expect(controller.clearPendingBars).toHaveBeenCalled();
    expect(doc.getElementById('connectionOverlay').classList.contains('hidden')).toBe(false);
    expect(doc.getElementById('connectionStatus').textContent).toBe('Streaming stopped');
    expect(doc.getElementById('reconnectBtn').classList.contains('hidden')).toBe(true);
  });

  describe('stream_stopped (pair-scoped stop)', () => {
    it('resets the UI like gateway_stopped when the event is for the current pair', () => {
      const { doc } = setupDocument();
      const { socket, controller, lifecycle } = buildController(doc, window);

      socket.trigger('platform_connected');
      expect(lifecycle.getState()).toBe('streaming');

      socket.trigger('stream_stopped', { pair: 'MNQ' });

      expect(controller.setHistoryReady).toHaveBeenCalledWith(false);
      expect(controller.clearPendingBars).toHaveBeenCalled();
      expect(doc.getElementById('connectionStatus').textContent).toBe('Streaming stopped');
      expect(lifecycle.getState()).toBe('idle');
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(true);
    });

    it('ignores the event when it is for a different pair', () => {
      const { doc } = setupDocument();
      const { socket, controller, lifecycle } = buildController(doc, window);

      socket.trigger('platform_connected');
      expect(lifecycle.getState()).toBe('streaming');

      socket.trigger('stream_stopped', { pair: 'MES' });

      expect(controller.setHistoryReady).not.toHaveBeenCalled();
      expect(controller.clearPendingBars).not.toHaveBeenCalled();
      expect(lifecycle.getState()).toBe('streaming');
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);
    });
  });

  describe('stop streaming button visibility', () => {
    it('shows stop button on stream_status when live and gateway running', () => {
      const { doc } = setupDocument();
      const { socket } = buildController(doc, window);

      socket.trigger('stream_status', { playing: true, live_mode: true, gateway_running: true, platform_connected: true });

      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);
    });

    it('hides stop button on stream_status when live but gateway not running', () => {
      const { doc } = setupDocument();
      const { socket } = buildController(doc, window);

      socket.trigger('gateway_started');
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);

      socket.trigger('stream_status', { playing: true, live_mode: true, gateway_running: false, platform_connected: false });
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(true);
    });

    it('hides stop button on stream_status when not live', () => {
      const { doc } = setupDocument();
      const { socket } = buildController(doc, window);

      socket.trigger('gateway_started');
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);

      socket.trigger('stream_status', { playing: false, live_mode: false, gateway_running: true });
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(true);
    });

    it('shows stop button on gateway_started', () => {
      const { doc } = setupDocument();
      const { socket } = buildController(doc, window);

      socket.trigger('gateway_started');

      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);
    });

    it('shows stop button on platform_connected', () => {
      const { doc } = setupDocument();
      const { socket } = buildController(doc, window);

      socket.trigger('platform_connected');

      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);
    });

    it('hides stop button on gateway_stopped', () => {
      const { doc } = setupDocument();
      const { socket } = buildController(doc, window);

      socket.trigger('gateway_started');
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);

      socket.trigger('gateway_stopped');
      expect(doc.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(true);
    });
  });

  it('falls back to NinjaTrader platform label', () => {
    document.body.innerHTML = `
      <div id="connectionOverlay" class="hidden">Overlay</div>
      <div id="connectionStatus"></div>
      <button id="reconnectBtn" class="hidden"></button>
      <button id="startStreamingBtn"></button>
    `;
    const { socket } = buildController(document, window);
    socket.trigger('gateway_started');
    expect(document.getElementById('connectionStatus').textContent).toContain('NinjaTrader');
  });
});
