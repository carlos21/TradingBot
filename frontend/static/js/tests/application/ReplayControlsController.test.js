import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ReplayControlsController } from '../../application/ReplayControlsController.js';
import { FakeSocket } from '../fakes/FakeSocket.js';
import { FakeDomService } from '../fakes/FakeDomService.js';
import { FakeNotification } from '../fakes/FakeNotification.js';
import { FakeTradeService } from '../fakes/FakeTradeService.js';

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
  const tradeService = new FakeTradeService();
  const controls = new ReplayControlsController(controller, socket, dom, notification, tradeService);
  return { controller, socket, dom, notification, controls, tradeService };
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
      <button id="testTradeBtn">Test</button>
      <div id="testTradeModal" class="hidden">
        <button id="testTradeModalClose">×</button>
        <button id="testLongBtn">Test Long</button>
        <button id="testShortBtn">Test Short</button>
        <button id="closeAllBtn">Close All</button>
        <select id="modifySlTradeSelect">
          <option value="">Select open trade…</option>
        </select>
        <input id="modifySlInput" type="number" step="0.01" />
        <button id="modifySlBtn">Update SL</button>
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
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.openTestTrade.mockResolvedValue({ trade_id: 't1' });
      controls.init();

      document.getElementById('testLongBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(tradeService.openTestTrade).toHaveBeenCalledWith('MNQ', 'long');
      expect(notification.alerts[0]).toContain('Test Long sent: t1');
    });

    it('sends test short trade and alerts error on failure', async () => {
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.openTestTrade.mockRejectedValue(new Error('Invalid pair'));
      controls.init();

      document.getElementById('testShortBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Failed: Invalid pair');
    });

    it('closes all trades and reports count', async () => {
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.closeAllTrades.mockResolvedValue({ count: 2, failed: [] });
      controls.init();

      document.getElementById('closeAllBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(tradeService.closeAllTrades).toHaveBeenCalledWith('MNQ');
      expect(notification.alerts[0]).toBe('Close All sent. 2 trade(s) closed.');
    });

    it('reports failed close-all trades', async () => {
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.closeAllTrades.mockResolvedValue({ count: 1, failed: [{ trade_id: 't2' }] });
      controls.init();

      document.getElementById('closeAllBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toContain('Failed: t2');
    });
  });

  describe('modal', () => {
    it('opens modal on Test button click', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      document.getElementById('testTradeBtn').click();
      expect(document.getElementById('testTradeModal').classList.contains('hidden')).toBe(false);
    });

    it('closes modal on close button click', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      document.getElementById('testTradeBtn').click();
      document.getElementById('testTradeModalClose').click();
      expect(document.getElementById('testTradeModal').classList.contains('hidden')).toBe(true);
    });

    it('closes modal when clicking backdrop', () => {
      const { controls } = buildControls(document, window);
      controls.init();

      document.getElementById('testTradeBtn').click();
      document.getElementById('testTradeModal').click();
      expect(document.getElementById('testTradeModal').classList.contains('hidden')).toBe(true);
    });
  });

  describe('modify stop loss', () => {
    it('loads open trades into select when modal opens', async () => {
      const { controls, tradeService } = buildControls(document, window);
      tradeService.listTrades.mockResolvedValue([
        { trade_id: 't1', status: 'open', type: 'long', entry: 20000, stop_loss: 19980 },
        { trade_id: 't2', status: 'closed', type: 'short', entry: 20100, stop_loss: 20120 },
      ]);
      controls.init();

      document.getElementById('testTradeBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(tradeService.listTrades).toHaveBeenCalledWith('MNQ');
      const select = document.getElementById('modifySlTradeSelect');
      expect(select.options.length).toBe(2);
      expect(select.options[1].value).toBe('t1');
    });

    it('sends modify stop-loss request', async () => {
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.listTrades.mockResolvedValue([
        { trade_id: 't1', status: 'open', type: 'long', entry: 20000, stop_loss: 19980 },
      ]);
      tradeService.modifyStopLoss.mockResolvedValue({});
      controls.init();

      document.getElementById('testTradeBtn').click();
      await new Promise(r => setTimeout(r, 10));

      const select = document.getElementById('modifySlTradeSelect');
      select.value = 't1';
      const input = document.getElementById('modifySlInput');
      input.value = '19990';

      document.getElementById('modifySlBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(tradeService.modifyStopLoss).toHaveBeenCalledWith('t1', 19990);
      expect(notification.alerts[0]).toContain('Updated SL for t1');
    });

    it('alerts when no trade selected', async () => {
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.listTrades.mockResolvedValue([]);
      tradeService.modifyStopLoss.mockResolvedValue({});
      controls.init();

      document.getElementById('testTradeBtn').click();
      await new Promise(r => setTimeout(r, 10));

      document.getElementById('modifySlInput').value = '19990';
      document.getElementById('modifySlBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Please select a trade');
      expect(tradeService.modifyStopLoss).not.toHaveBeenCalled();
    });

    it('alerts when stop loss is invalid', async () => {
      const { controls, notification, tradeService } = buildControls(document, window);
      tradeService.listTrades.mockResolvedValue([
        { trade_id: 't1', status: 'open', type: 'long', entry: 20000, stop_loss: 19980 },
      ]);
      controls.init();

      document.getElementById('testTradeBtn').click();
      await new Promise(r => setTimeout(r, 10));

      document.getElementById('modifySlTradeSelect').value = 't1';
      document.getElementById('modifySlInput').value = '-5';
      document.getElementById('modifySlBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Please enter a valid stop-loss price');
      expect(tradeService.modifyStopLoss).not.toHaveBeenCalled();
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
