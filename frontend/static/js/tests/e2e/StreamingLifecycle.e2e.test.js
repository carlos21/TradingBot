import { describe, it, expect, beforeEach, vi } from 'vitest';
import { StreamingLifecycleController } from '../../application/StreamingLifecycleController.js';
import { StreamingControlsController } from '../../application/StreamingControlsController.js';
import { ChartSocketController } from '../../application/ChartSocketController.js';
import { StreamingState } from '../../domain/streamingLifecycle.js';
import { FakeDomService } from '../fakes/FakeDomService.js';
import { FakeNotification } from '../fakes/FakeNotification.js';
import { FakeSocket } from '../fakes/FakeSocket.js';

/**
 * End-to-end test of the streaming lifecycle UI: the real state machine,
 * click controller, and socket controller wired together exactly like
 * chartApp does, driving the same overlay/button markup as index.html.
 *
 * Regression coverage for: "Start Streaming button remains disabled when
 * the stream stops."
 */

function setupDocument() {
  document.body.innerHTML = `
    <div id="connectionOverlay" data-platform-label="NinjaTrader">
      <button id="startStreamingBtn" class="flex items-center">
        <svg class="w-5 h-5"></svg>
        <span>Start Streaming</span>
      </button>
      <div id="connectionStatus"></div>
      <button id="reconnectBtn" class="hidden">Reconnect</button>
    </div>
    <button id="stopStreamingBtn" class="hidden">Stop Streaming</button>
  `;
}

function buildApp() {
  const dom = new FakeDomService(document, window);
  const socket = new FakeSocket();
  const notification = new FakeNotification();
  const chartController = {
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

  // Same wiring as composition/chartApp.js.
  const lifecycle = new StreamingLifecycleController(dom);
  lifecycle.init();
  const socketController = new ChartSocketController(socket, chartController, dom, lifecycle);
  socketController.init();
  const controls = new StreamingControlsController(dom, notification, lifecycle);
  controls.init();

  return { dom, socket, notification, lifecycle, chartController, controls };
}

const flush = () => new Promise(r => setTimeout(r, 10));

const startBtn = () => document.getElementById('startStreamingBtn');
const stopBtn = () => document.getElementById('stopStreamingBtn');
const reconnectBtn = () => document.getElementById('reconnectBtn');
const overlay = () => document.getElementById('connectionOverlay');
const isHidden = el => el.classList.contains('hidden');

describe('Streaming lifecycle e2e', () => {
  beforeEach(() => {
    setupDocument();
    vi.restoreAllMocks();
  });

  it('start → stream → stop → start again: Start is re-enabled with its original label after gateway_stopped', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'starting', message: 'ZeroMQ gateway started; waiting for platform...' }),
    });
    const { socket, lifecycle } = buildApp();

    // 1. Initial state: Start visible and enabled.
    expect(isHidden(startBtn())).toBe(false);
    expect(startBtn().disabled).toBe(false);
    const originalHTML = startBtn().innerHTML;

    // 2. Click Start → busy spinner while the request is in flight.
    startBtn().click();
    expect(startBtn().disabled).toBe(true);
    expect(startBtn().innerHTML).toContain('Starting…');
    await flush();
    expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trading_mode: 'simulation' }),
    });
    expect(lifecycle.getState()).toBe(StreamingState.STARTING);

    // 3. Backend confirms gateway (stays in-flight), then platform connects.
    socket.trigger('gateway_started');
    expect(isHidden(stopBtn())).toBe(false);
    expect(lifecycle.getState()).toBe(StreamingState.STARTING);

    socket.trigger('platform_connected');
    expect(isHidden(overlay())).toBe(true);
    expect(lifecycle.getState()).toBe(StreamingState.STREAMING);

    // 4. Click Stop → Stop shows progress; backend stops the gateway.
    stopBtn().click();
    expect(stopBtn().disabled).toBe(true);
    expect(stopBtn().innerHTML).toContain('Stopping…');
    await flush();
    expect(global.fetch).toHaveBeenCalledWith('/api/stream/stop', { method: 'POST' });

    // 5. gateway_stopped → THE regression assertion: Start must be visible,
    //    enabled, and showing its original label again.
    socket.trigger('gateway_stopped');
    expect(lifecycle.getState()).toBe(StreamingState.IDLE);
    expect(isHidden(overlay())).toBe(false);
    expect(isHidden(startBtn())).toBe(false);
    expect(startBtn().disabled).toBe(false);
    expect(startBtn().innerHTML).toBe(originalHTML);
    expect(isHidden(stopBtn())).toBe(true);
    expect(stopBtn().disabled).toBe(false);

    // 6. The button genuinely works again: a second click starts a new run.
    startBtn().click();
    await flush();
    expect(global.fetch).toHaveBeenCalledTimes(3); // start, stop, start
    expect(lifecycle.getState()).toBe(StreamingState.STARTING);
  });

  it('platform disconnect → reconnect → failed reconnect shows Reconnect again', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'starting', message: 'starting' }),
    });
    const { socket, lifecycle } = buildApp();

    // Drive to STREAMING.
    startBtn().click();
    await flush();
    socket.trigger('gateway_started');
    socket.trigger('platform_connected');
    expect(lifecycle.getState()).toBe(StreamingState.STREAMING);

    // Platform drops: overlay returns with Reconnect, Start hidden.
    socket.trigger('platform_disconnected');
    expect(lifecycle.getState()).toBe(StreamingState.DISCONNECTED);
    expect(isHidden(overlay())).toBe(false);
    expect(isHidden(reconnectBtn())).toBe(false);
    expect(isHidden(startBtn())).toBe(true);

    // Reconnect attempt fails → Reconnect reappears.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ message: 'Gateway busy' }),
    });
    reconnectBtn().click();
    expect(isHidden(reconnectBtn())).toBe(true);
    await flush();
    expect(lifecycle.getState()).toBe(StreamingState.DISCONNECTED);
    expect(isHidden(reconnectBtn())).toBe(false);
  });

  it('start failure response re-enables Start immediately', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ message: 'No accounts configured' }),
    });
    const { lifecycle } = buildApp();

    startBtn().click();
    expect(startBtn().disabled).toBe(true);
    await flush();

    expect(lifecycle.getState()).toBe(StreamingState.IDLE);
    expect(startBtn().disabled).toBe(false);
    expect(startBtn().innerHTML).toContain('Start Streaming');
    expect(document.getElementById('connectionStatus').textContent).toBe('⚠️ No accounts configured');
  });

  it('stop failure response keeps streaming and re-enables Stop', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'starting', message: 'starting' }),
    });
    const { socket, notification, lifecycle } = buildApp();

    startBtn().click();
    await flush();
    socket.trigger('gateway_started');
    socket.trigger('platform_connected');

    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ message: 'Gateway busy' }),
    });
    stopBtn().click();
    await flush();

    expect(notification.alerts[0]).toBe('Failed to stop streaming: Gateway busy');
    expect(lifecycle.getState()).toBe(StreamingState.STREAMING);
    expect(stopBtn().disabled).toBe(false);
    expect(stopBtn().innerHTML).toContain('Stop Streaming');
  });

  it('initial page-load status sync renders the backend state', () => {
    const { socket, lifecycle } = buildApp();

    // Backend already streaming when the page loads.
    socket.trigger('stream_status', { playing: true, live_mode: true, gateway_running: true, platform_connected: true });
    expect(lifecycle.getState()).toBe(StreamingState.STREAMING);
    expect(isHidden(overlay())).toBe(true);
    expect(isHidden(stopBtn())).toBe(false);

    // Backend stopped between reloads.
    socket.trigger('stream_status', { playing: true, live_mode: true, gateway_running: false, platform_connected: false });
    expect(lifecycle.getState()).toBe(StreamingState.IDLE);
    expect(isHidden(overlay())).toBe(false);
    expect(startBtn().disabled).toBe(false);
  });

  it('gateway already running at page load (auto-started at boot): Start is enabled and launches the platform', async () => {
    // Regression: with the gateway auto-started by the backend but the
    // platform not yet connected, the Start button must NOT be disabled —
    // clicking it is the only way to trigger the platform launch.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'starting', message: 'ZeroMQ gateway started; waiting for platform...' }),
    });
    const { socket, lifecycle } = buildApp();

    socket.trigger('stream_status', { playing: true, live_mode: true, gateway_running: true, platform_connected: false });
    expect(lifecycle.getState()).toBe(StreamingState.GATEWAY_UP);
    expect(isHidden(overlay())).toBe(false);
    expect(isHidden(startBtn())).toBe(false);
    expect(startBtn().disabled).toBe(false);

    startBtn().click();
    expect(startBtn().disabled).toBe(true);
    expect(startBtn().innerHTML).toContain('Starting…');
    await flush();
    expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ trading_mode: 'simulation' }),
    });

    // No gateway_started follows (the gateway was already running); the
    // machine waits for the platform while Stop stays available to abort.
    expect(lifecycle.getState()).toBe(StreamingState.STARTING);
    expect(isHidden(stopBtn())).toBe(false);

    socket.trigger('platform_connected');
    expect(lifecycle.getState()).toBe(StreamingState.STREAMING);
    expect(isHidden(overlay())).toBe(true);
  });
});
