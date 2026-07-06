import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReplayControlsController } from '../../application/ReplayControlsController.js';
import { FakeSocket } from '../fakes/FakeSocket.js';
import { FakeDomService } from '../fakes/FakeDomService.js';
import { FakeNotification } from '../fakes/FakeNotification.js';

function buildControls(doc, win) {
  const controller = {
    currentTF: '1m',
    isPlaying: false,
    pair: 'MNQ',
    toggleReplay: () => { controller.isPlaying = !controller.isPlaying; },
    stepReplay: () => { controller.isPlaying = false; },
    jumpToDay: (dir) => {},
    changeTimeframe: (tf) => { controller.currentTF = tf; },
  };
  const socket = new FakeSocket();
  const dom = new FakeDomService(doc, win);
  const notification = new FakeNotification();
  const controls = new ReplayControlsController(controller, socket, dom, notification);
  return { controller, socket, dom, notification, controls };
}

function setupDocument() {
  document.body.innerHTML = `
    <button id="toggleReplayBtn">Play</button>
    <button id="stepBarBtn">Step</button>
    <button id="prevDayBtn">Prev</button>
    <button id="nextDayBtn">Next</button>
    <button data-timeframe="1m">1m</button>
    <button data-timeframe="5m">5m</button>
    <div id="testTradeControls" class="hidden">
      <button id="testLongBtn">Test Long</button>
      <button id="testShortBtn">Test Short</button>
      <button id="closeAllBtn">Close All</button>
    </div>
    <div id="testDropdown">
      <button id="testDropdownToggle">Test</button>
      <div id="testDropdownMenu" class="hidden">
        <button>Option 1</button>
      </div>
    </div>
    <button id="startStreamingBtn">Start Streaming</button>
    <button id="reconnectBtn" class="hidden">Reconnect</button>
    <div id="connectionStatus"></div>
    <div class="replay-control">Control 1</div>
    <div class="replay-control">Control 2</div>
  `;
}

describe('ReplayControlsController', () => {
  beforeEach(() => {
    setupDocument();
    vi.restoreAllMocks();
  });

  describe('replay controls', () => {
    it('toggles replay on button click', () => {
      const { controls, controller } = buildControls(document, window);
      controls.init();

      const btn = document.getElementById('toggleReplayBtn');
      btn.click();
      expect(controller.isPlaying).toBe(true);
      expect(btn.textContent).toBe('Pause');

      btn.click();
      expect(controller.isPlaying).toBe(false);
      expect(btn.textContent).toBe('Play');
    });

    it('steps replay and resets toggle text', () => {
      const { controls, controller } = buildControls(document, window);
      controls.init();

      const stepBtn = document.getElementById('stepBarBtn');
      const toggleBtn = document.getElementById('toggleReplayBtn');
      controller.isPlaying = true;
      toggleBtn.textContent = 'Pause';

      stepBtn.click();
      expect(controller.isPlaying).toBe(false);
      expect(toggleBtn.textContent).toBe('Play');
    });

    it('jumps days on prev/next buttons', () => {
      const { controls, controller } = buildControls(document, window);
      const jumpSpy = vi.spyOn(controller, 'jumpToDay');
      controls.init();

      document.getElementById('prevDayBtn').click();
      expect(jumpSpy).toHaveBeenCalledWith(-1);

      document.getElementById('nextDayBtn').click();
      expect(jumpSpy).toHaveBeenCalledWith(1);
    });

    it('changes timeframe on button click', () => {
      const { controls, controller } = buildControls(document, window);
      controls.init();

      const btn = document.querySelector('[data-timeframe="5m"]');
      btn.click();
      expect(controller.currentTF).toBe('5m');
      expect(btn.classList.contains('active')).toBe(true);
    });

    it('marks current timeframe active on init', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      const btn = document.querySelector('[data-timeframe="1m"]');
      expect(btn.classList.contains('active')).toBe(true);
    });
  });

  describe('streaming buttons', () => {
    it('starts streaming and updates status on success', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stream started' }),
      });

      const { controls } = buildControls(document, window);
      controls.init();

      const btn = document.getElementById('startStreamingBtn');
      btn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', { method: 'POST' });
      expect(document.getElementById('connectionStatus').textContent).toBe('Stream started');
      expect(btn.disabled).toBe(true);
    });

    it('restores start button on error response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ message: 'Already running' }),
      });

      const { controls } = buildControls(document, window);
      controls.init();

      const btn = document.getElementById('startStreamingBtn');
      const originalHTML = btn.innerHTML;
      btn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(document.getElementById('connectionStatus').textContent).toBe('⚠️ Already running');
      expect(btn.disabled).toBe(false);
      expect(btn.innerHTML).toBe(originalHTML);
    });

    it('restores start button on network error', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network failed'));

      const { controls } = buildControls(document, window);
      controls.init();

      const btn = document.getElementById('startStreamingBtn');
      btn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(document.getElementById('connectionStatus').textContent).toBe('Error: Network failed');
      expect(btn.disabled).toBe(false);
    });

    it('reconnects and hides reconnect button on success', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Reconnected' }),
      });

      const { controls } = buildControls(document, window);
      controls.init();

      const reconnectBtn = document.getElementById('reconnectBtn');
      reconnectBtn.classList.remove('hidden');
      reconnectBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', { method: 'POST' });
      expect(document.getElementById('connectionStatus').textContent).toBe('Reconnected');
      expect(reconnectBtn.classList.contains('hidden')).toBe(true);
    });

    it('shows reconnect button again on reconnect error', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ message: 'Failed' }),
      });

      const { controls } = buildControls(document, window);
      controls.init();

      const reconnectBtn = document.getElementById('reconnectBtn');
      reconnectBtn.classList.remove('hidden');
      reconnectBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(reconnectBtn.classList.contains('hidden')).toBe(false);
    });
  });

  describe('test trades', () => {
    it('sends test long trade and alerts trade id', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => 'application/json' },
        json: async () => ({ trade_id: 't1' }),
      });

      const { controls, notification } = buildControls(document, window);
      controls.init();

      document.getElementById('testLongBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith(
        '/api/trades/test',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ pair: 'MNQ', direction: 'long' }),
        })
      );
      expect(notification.alerts[0]).toContain('Test Long sent: t1');
    });

    it('sends test short trade and alerts error on failure', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        statusText: 'Bad Request',
        headers: { get: () => 'application/json' },
        json: async () => ({ error: 'Invalid pair' }),
      });

      const { controls, notification } = buildControls(document, window);
      controls.init();

      document.getElementById('testShortBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Failed: Invalid pair');
    });

    it('handles non-JSON error response for test trades', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        statusText: 'Server Error',
        headers: { get: () => 'text/plain' },
        text: async () => 'boom',
      });

      const { controls, notification } = buildControls(document, window);
      controls.init();

      document.getElementById('testLongBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Failed: boom');
    });

    it('alerts network error for test trades', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Offline'));

      const { controls, notification } = buildControls(document, window);
      controls.init();

      document.getElementById('testShortBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Error: Offline');
    });

    it('closes all trades and reports count', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => 'application/json' },
        json: async () => ({ count: 2, failed: [] }),
      });

      const { controls, notification } = buildControls(document, window);
      controls.init();

      document.getElementById('closeAllBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith(
        '/api/trades/close-all',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ pair: 'MNQ' }),
        })
      );
      expect(notification.alerts[0]).toBe('Close All sent. 2 trade(s) closed.');
    });

    it('reports failed close-all trades', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        headers: { get: () => 'application/json' },
        json: async () => ({ count: 1, failed: [{ trade_id: 't2' }] }),
      });

      const { controls, notification } = buildControls(document, window);
      controls.init();

      document.getElementById('closeAllBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toContain('Failed: t2');
    });
  });

  describe('dropdown', () => {
    it('toggles dropdown menu', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      const toggle = document.getElementById('testDropdownToggle');
      const menu = document.getElementById('testDropdownMenu');

      toggle.click();
      expect(menu.classList.contains('hidden')).toBe(false);

      toggle.click();
      expect(menu.classList.contains('hidden')).toBe(true);
    });

    it('closes dropdown when clicking outside', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      const toggle = document.getElementById('testDropdownToggle');
      const menu = document.getElementById('testDropdownMenu');

      toggle.click();
      document.body.click();
      expect(menu.classList.contains('hidden')).toBe(true);
    });

    it('closes dropdown when selecting an option', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      const toggle = document.getElementById('testDropdownToggle');
      const menu = document.getElementById('testDropdownMenu');

      toggle.click();
      menu.querySelector('button').click();
      expect(menu.classList.contains('hidden')).toBe(true);
    });
  });

  describe('live mode', () => {
    it('applies live mode UI changes on stream_status', () => {
      const { controls, socket } = buildControls(document, window);
      controls.init();

      socket.trigger('stream_status', { playing: false, live_mode: true });

      expect(document.getElementById('testTradeControls').classList.contains('hidden')).toBe(false);
      document.querySelectorAll('.replay-control').forEach(el => {
        expect(el.style.display).toBe('none');
      });
      expect(document.getElementById('stepBarBtn').style.display).toBe('none');
      expect(document.getElementById('toggleReplayBtn').style.display).toBe('none');
    });

    it('updates toggle button text when stream stops', () => {
      const { controls, socket } = buildControls(document, window);
      controls.init();

      const toggleBtn = document.getElementById('toggleReplayBtn');
      toggleBtn.textContent = 'Pause';
      socket.trigger('stream_status', { playing: false, live_mode: false });

      expect(toggleBtn.textContent).toBe('Play');
    });
  });
});
