import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ChartController } from '../../application/ChartController.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { FakeSocket } from '../fakes/FakeSocket.js';
import { FakeChartApi } from '../fakes/FakeChartApi.js';
import { FakeStorage } from '../fakes/FakeStorage.js';
import { FakeDomService } from '../fakes/FakeDomService.js';

function setupDocument() {
  const container = document.createElement('div');
  container.id = 'chartContainer';
  container.style.width = '800px';
  container.style.height = '600px';
  document.body.appendChild(container);
  return { doc: document, win: window };
}

function makeBars(count = 20) {
  const bars = [];
  for (let i = 0; i < count; i++) {
    bars.push({
      time: 1000 + i,
      open: 100 + i,
      high: 101 + i,
      low: 99 + i,
      close: 100.5 + i,
    });
  }
  return bars;
}

function buildController(doc, win, options = {}) {
  const http = new FakeHttpClient();
  http.setResponse('GET', '/api/pair', { pair: 'MNQ' });
  http.setResponse('GET', '/api/bars', makeBars());
  http.setResponse('GET', '/api/trades', []);
  http.setResponse('GET', '/api/lines', []);

  const socket = new FakeSocket();
  const chartApi = new FakeChartApi();
  const storage = new FakeStorage();
  const dom = new FakeDomService(doc, win);

  const controller = new ChartController({
    httpClient: http,
    socket,
    chartApi,
    storage,
    domService: dom,
    options: { showTSI: false, ...options },
  });

  return { controller, http, socket, chartApi, storage, dom };
}

async function flushPromises(ms = 50) {
  await new Promise(r => setTimeout(r, ms));
}

describe('ChartController', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  describe('initialization', () => {
    it('initializes pair, chart, and loads bars', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);

      await flushPromises();

      expect(controller.pair).toBe('MNQ');
      expect(chartApi.calls.some(c => c.method === 'createChart')).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'addCandlestickSeries')).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'setData')).toBe(true);
      expect(controller.historyReady).toBe(true);
    });

    it('logs init error when pair fetch fails', async () => {
      const { doc, win } = setupDocument();
      const http = new FakeHttpClient();
      http.setResponse('GET', '/api/pair', () => {
        throw new Error('pair error');
      });
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

      new ChartController({
        httpClient: http,
        socket: new FakeSocket(),
        chartApi: new FakeChartApi(),
        storage: new FakeStorage(),
        domService: new FakeDomService(doc, win),
        options: { showTSI: false },
      });

      await flushPromises();
      expect(consoleError).toHaveBeenCalledWith(
        '[ChartController] init failed:',
        expect.any(Error)
      );
      consoleError.mockRestore();
    });
  });

  describe('initBars', () => {
    it('loads and displays bars with trades and lines', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi, http } = buildController(doc, win);
      await flushPromises();

      chartApi.calls.length = 0;
      await controller.initBars();

      expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/bars'))).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'setData')).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'fitContent')).toBe(true);
    });

    it('skips when series is busy', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller._seriesBusy = true;
      const consoleLog = vi.spyOn(console, 'log').mockImplementation(() => {});
      await controller.initBars();
      expect(consoleLog).toHaveBeenCalledWith(
        '[ChartController] initBars: already busy, skipping'
      );
      consoleLog.mockRestore();
    });

    it('logs loadLines error without failing initBars', async () => {
      const { doc, win } = setupDocument();
      const { controller, http } = buildController(doc, win);
      await flushPromises();

      http.setResponse('GET', '/api/lines', () => {
        throw new Error('lines failed');
      });
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

      await controller.initBars();

      expect(consoleError).toHaveBeenCalledWith(
        '[ChartController] loadLines failed during initBars:',
        expect.any(Error)
      );
      consoleError.mockRestore();
    });

    it('drops bars with null OHLC and warns', async () => {
      const { doc, win } = setupDocument();
      const { controller, http } = buildController(doc, win);
      await flushPromises();

      const consoleWarn = vi.spyOn(console, 'warn').mockImplementation(() => {});
      http.setResponse('GET', '/api/bars', [
        { time: 1000, open: 100, high: 101, low: 99, close: 100.5 },
        { time: 1001, open: null, high: null, low: null, close: null },
      ]);

      await controller.initBars();

      expect(consoleWarn).toHaveBeenCalledWith('[ChartController] dropped 1 bars with null OHLC');
      consoleWarn.mockRestore();
    });
  });

  describe('changeTimeframe', () => {
    it('switches timeframe and emits set_timeframe', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket } = buildController(doc, win);
      await flushPromises();

      await controller.changeTimeframe('5m');
      expect(controller.currentTF).toBe('5m');
      expect(socket.emissions).toContainEqual({
        event: 'set_timeframe',
        payload: { timeframe: '5m', fromTime: expect.any(Number) },
      });
    });

    it('skips when series is busy', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller._seriesBusy = true;
      const consoleLog = vi.spyOn(console, 'log').mockImplementation(() => {});
      await controller.changeTimeframe('5m');
      expect(consoleLog).toHaveBeenCalledWith(
        '[ChartController] changeTimeframe: already busy, skipping'
      );
      consoleLog.mockRestore();
    });

    it('filters bars to replay position in non-live mode', async () => {
      const { doc, win } = setupDocument();
      const { controller, http } = buildController(doc, win);
      await flushPromises();

      controller.lastTime = 1005;
      http.setResponse('GET', '/api/bars', makeBars(20).map(b => ({ ...b, time: b.time + 500 })));

      await controller.changeTimeframe('5m');
      expect(controller.historicalBars.every(b => b.time <= 1005)).toBe(true);
    });

    it('clears TSI series when TSI is enabled', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win, { showTSI: true });
      await flushPromises();

      chartApi.calls.length = 0;
      await controller.changeTimeframe('5m');

      expect(chartApi.calls.some(c => c.method === 'setData' && c.args[1].length === 0)).toBe(true);
    });
  });

  describe('replay controls', () => {
    it('starts replay and emits start_stream', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket } = buildController(doc, win);
      await flushPromises();

      controller.startReplay('5m', 2000);
      expect(controller.isPlaying).toBe(true);
      expect(socket.emissions).toContainEqual({
        event: 'start_stream',
        payload: { timeframe: '5m', pair: 'MNQ', fromTime: 2000 },
      });
    });

    it('slices historical bars when starting replay before lastTime', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      const originalLength = controller.historicalBars.length;
      controller.startReplay('1m', 1005);
      expect(controller.historicalBars.length).toBeLessThan(originalLength);
      expect(controller.historicalBars[controller.historicalBars.length - 1].time).toBeLessThan(1005);
    });

    it('pauses replay and emits pause_stream', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket } = buildController(doc, win);
      await flushPromises();

      controller.startReplay();
      controller.pauseReplay();
      expect(controller.isPlaying).toBe(false);
      expect(socket.emissions).toContainEqual({ event: 'pause_stream', payload: undefined });
    });

    it('toggles replay', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller.toggleReplay();
      expect(controller.isPlaying).toBe(true);
      controller.toggleReplay();
      expect(controller.isPlaying).toBe(false);
    });

    it('steps replay and emits step_stream', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket } = buildController(doc, win);
      await flushPromises();

      controller.isPlaying = true;
      controller.stepReplay();
      expect(controller.isPlaying).toBe(false);
      expect(socket.emissions).toContainEqual({
        event: 'step_stream',
        payload: { timeframe: '1m', pair: 'MNQ', fromTime: expect.any(Number) },
      });
    });

    it('jumps to next/prev day', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket } = buildController(doc, win);
      await flushPromises();

      const before = controller.lastTime;
      controller.jumpToDay(1);
      expect(socket.emissions.at(-1).payload.fromTime).toBe(before + 86400);

      const before2 = controller.lastTime;
      controller.jumpToDay(-1);
      expect(socket.emissions.at(-1).payload.fromTime).toBe(before2 - 86400);
    });
  });

  describe('bar processing', () => {
    it('processes incoming bars', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.setHistoryReady(true);
      controller.processBar({ time: 1020, open: 101, high: 103, low: 100, close: 102 });

      expect(controller.lastTime).toBe(1020);
      expect(chartApi.calls.some(c => c.method === 'update' && c.args[1]?.time === 1020)).toBe(true);
    });

    it('queues bars while series is busy', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller._seriesBusy = true;
      controller.processBar({ time: 1020, open: 101, high: 103, low: 100, close: 102 });
      expect(controller.pendingBars).toHaveLength(1);
    });

    it('flushes queued bars when series becomes free', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller._seriesBusy = true;
      controller.processBar({ time: 1020, open: 101, high: 103, low: 100, close: 102 });
      controller._seriesBusy = false;
      controller._flushPendingBars();

      expect(controller.pendingBars).toHaveLength(0);
      expect(controller.lastTime).toBe(1020);
      expect(chartApi.calls.some(c => c.method === 'update' && c.args[1]?.time === 1020)).toBe(true);
    });

    it('updates last bar in place when time matches', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      const lastTime = controller.historicalBars.at(-1).time;
      controller.processBar({ time: lastTime, open: 1, high: 2, low: 0, close: 1.5 });
      expect(controller.historicalBars.at(-1).close).toBe(1.5);
      expect(chartApi.calls.some(c => c.method === 'update')).toBe(true);
    });

    it('shades New York session bars', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      const nyTime = Math.floor(new Date('2024-01-10T14:30:00-05:00').getTime() / 1000);
      controller.setHistoryReady(true);
      controller.processBar({ time: nyTime, open: 100, high: 101, low: 99, close: 100.5 });

      expect(chartApi.calls.some(c => c.method === 'update' && c.args[0] === controller.nySeries)).toBe(true);
    });
  });

  describe('TSI', () => {
    it('calculates and displays TSI when enabled and enough bars exist', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win, { showTSI: true });
      await flushPromises();

      expect(chartApi.calls.some(c => c.method === 'addLineSeries')).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'applyPriceScaleOptions')).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'setData' && c.args[0] === controller.tsiSeries)).toBe(true);
    });

    it('skips TSI when disabled', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win, { showTSI: false });
      await flushPromises();

      expect(controller.tsiSeries).toBeUndefined();
      expect(chartApi.calls.some(c => c.method === 'addLineSeries')).toBe(false);
    });

    it('handles indicator updates and crosses', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win, { showTSI: true });
      await flushPromises();

      controller.setHistoryReady(true);
      controller.handleIndicatorUpdate({ time: controller.lastTime + 1, tsi: 10, signal: 5, tf: '1m', cross_type: 'bullish' });

      expect(chartApi.calls.some(c => c.method === 'update' && c.args[0] === controller.tsiSeries)).toBe(true);
      expect(controller._tsiMarkers.length).toBeGreaterThan(0);
    });
  });

  describe('trade handling', () => {
    it('opens a trade and draws trade lines', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.handleTradeOpen({
        trade_id: 't1',
        type: 'long',
        entry: 100,
        stop_loss: 99,
        take_profit: 102,
      });

      expect(controller.activeTrade.trade_id).toBe('t1');
      expect(chartApi.calls.filter(c => c.method === 'createPriceLine').length).toBeGreaterThanOrEqual(3);
    });

    it('clears all trade lines including null handles', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.allTradeLines = [null, { id: 'line-1' }];
      controller.clearAllTradeLines();
      expect(controller.allTradeLines).toHaveLength(0);
      expect(chartApi.calls.filter(c => c.method === 'removePriceLine').length).toBe(1);
    });

    it('closes a trade and removes trade lines', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.handleTradeOpen({ trade_id: 't1', type: 'long', entry: 100, stop_loss: 99, take_profit: 102 });
      const linesBefore = chartApi.calls.filter(c => c.method === 'createPriceLine').length;

      controller.handleTradeClose({ trade_id: 't1', status: 'closed' });
      expect(controller.activeTrade).toBeNull();
      expect(chartApi.calls.filter(c => c.method === 'removePriceLine').length).toBeGreaterThanOrEqual(3);
    });

    it('updates active trade SL', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.handleTradeOpen({ trade_id: 't1', type: 'long', entry: 100, stop_loss: 99, take_profit: 102 });
      chartApi.calls.length = 0;
      controller.handleTradeUpdate({ trade_id: 't1', stop_loss: 98.5 });

      expect(controller.activeTrade.stop_loss).toBe(98.5);
      expect(chartApi.calls.some(c => c.method === 'createPriceLine')).toBe(true);
    });

    it('updates trade entry and redraws lines', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.handleTradeOpen({ trade_id: 't1', type: 'long', entry: 100, stop_loss: 99, take_profit: 102 });
      chartApi.calls.length = 0;
      controller.handleTradeEntryUpdate({
        trade_id: 't1',
        entry_price: 101,
        stop_loss: 98,
        take_profit: 103,
        risk: 1.5,
      });

      expect(controller.activeTrade.entry).toBe(101);
      expect(controller.activeTrade.risk).toBe(1.5);
      expect(chartApi.calls.some(c => c.method === 'createPriceLine')).toBe(true);
    });

    it('keeps closed trade lines when option is set', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win, { keepClosedTradeLines: true });
      await flushPromises();

      controller.handleTradeOpen({ trade_id: 't1', type: 'long', entry: 100, stop_loss: 99, take_profit: 102 });
      const linesAfterOpen = chartApi.calls.filter(c => c.method === 'createPriceLine').length;
      chartApi.calls.length = 0;
      controller.handleTradeClose({ trade_id: 't1' });

      expect(controller.activeTrade).toBeNull();
      expect(chartApi.calls.filter(c => c.method === 'removePriceLine').length).toBe(0);
      expect(controller.allTradeLines.length).toBe(linesAfterOpen);
    });

    it('keeps closed trade lines when option is set', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win, { keepClosedTradeLines: true });
      await flushPromises();

      controller.handleTradeOpen({ trade_id: 't1', type: 'long', entry: 100, stop_loss: 99, take_profit: 102 });
      const linesAfterOpen = chartApi.calls.filter(c => c.method === 'createPriceLine').length;
      chartApi.calls.length = 0;
      controller.handleTradeClose({ trade_id: 't1' });

      expect(controller.activeTrade).toBeNull();
      expect(chartApi.calls.filter(c => c.method === 'removePriceLine').length).toBe(0);
      expect(controller.allTradeLines.length).toBe(linesAfterOpen);
    });

    it('shows only a single trade snapshot', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.allTrades = [
        { trade_id: 't1', type: 'long', entry: 100, stop_loss: 99, take_profit: 102 },
      ];
      controller.showOnlyTrade('t1');

      expect(chartApi.calls.filter(c => c.method === 'createPriceLine').length).toBeGreaterThanOrEqual(3);
    });
  });

  describe('lines', () => {
    it('adds a line via shift+left click', async () => {
      const { doc, win } = setupDocument();
      const { controller, http, chartApi } = buildController(doc, win);
      await flushPromises();

      http.setResponse('POST', '/api/lines', { id: 42 });
      const event = new MouseEvent('mousedown', {
        button: 0,
        shiftKey: true,
        clientX: 10,
        clientY: 200,
      });
      controller.chartElement.dispatchEvent(event);
      await flushPromises();

      expect(http.requests.some(r => r.method === 'POST' && r.url === '/api/lines')).toBe(true);
      expect(controller.pinnedLines.some(l => l.id === 42)).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'createPriceLine')).toBe(true);
    });

    it('removes a line via shift+right click', async () => {
      const { doc, win } = setupDocument();
      const { controller, http, chartApi } = buildController(doc, win);
      await flushPromises();

      http.setResponse('POST', '/api/lines', { id: 42 });
      http.setResponse('DELETE', '/api/lines/42', {});
      await controller._addLine(100);
      await flushPromises();

      const event = new MouseEvent('mousedown', {
        button: 2,
        shiftKey: true,
        clientX: 10,
        clientY: 100,
      });
      controller.chartElement.dispatchEvent(event);
      await flushPromises();

      expect(controller.pinnedLines).toHaveLength(0);
      expect(http.requests.some(r => r.method === 'DELETE' && r.url === '/api/lines/42')).toBe(true);
    });

    it('rolls back line on save error', async () => {
      const { doc, win } = setupDocument();
      const { controller, http, chartApi } = buildController(doc, win);
      await flushPromises();

      http.setResponse('POST', '/api/lines', () => {
        throw new Error('save failed');
      });
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

      await controller._addLine(100);

      expect(controller.pinnedLines).toHaveLength(0);
      expect(chartApi.calls.some(c => c.method === 'removePriceLine')).toBe(true);
      consoleError.mockRestore();
    });

    it('loads existing lines on init', async () => {
      const { doc, win } = setupDocument();
      const { controller, http, chartApi } = buildController(doc, win);
      http.setResponse('GET', '/api/lines', [{ id: 7, price: 105 }]);
      await flushPromises();

      expect(controller.pinnedLines.some(l => l.id === 7)).toBe(true);
      expect(chartApi.calls.some(c => c.method === 'createPriceLine' && c.args[1].price === 105)).toBe(true);
    });

    it('ignores duplicate lines when loading', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      controller.pinnedLines = [{ id: 7, line: {} }];
      controller._createLineOnChart({ id: 7, price: 105 });
      expect(controller.pinnedLines).toHaveLength(1);
    });

    it('removes line by id from socket event', async () => {
      const { doc, win } = setupDocument();
      const { controller, http, chartApi } = buildController(doc, win);
      http.setResponse('GET', '/api/lines', [{ id: 7, price: 105 }]);
      await flushPromises();

      controller.handleLineRemoved(7);
      expect(controller.pinnedLines).toHaveLength(0);
      expect(chartApi.calls.some(c => c.method === 'removePriceLine')).toBe(true);
    });

    it('keeps strategy lines when option is set', async () => {
      const { doc, win } = setupDocument();
      const { controller, http, chartApi } = buildController(doc, win, { keepStrategyLines: true });
      http.setResponse('GET', '/api/lines', [{ id: 7, price: 105 }]);
      await flushPromises();

      chartApi.calls.length = 0;
      controller.handleLineRemoved(7);
      expect(controller.pinnedLines).toHaveLength(1);
      expect(chartApi.calls.some(c => c.method === 'removePriceLine')).toBe(false);
    });
  });

  describe('chart click seek', () => {
    it('seeks to clicked bar time', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket, chartApi } = buildController(doc, win);
      await flushPromises();

      const clickHandler = chartApi.calls.find(c => c.method === 'subscribeClick').args[1];
      controller._isRPressed = true;
      clickHandler({ time: 1005 });

      expect(socket.emissions).toContainEqual({
        event: 'seek',
        payload: { fromTime: expect.any(Number) },
      });
      expect(controller.historicalBars.at(-1).time).toBeLessThanOrEqual(1005);
    });

    it('seeks to nearest bar when exact time is missing', async () => {
      const { doc, win } = setupDocument();
      const { controller, socket, chartApi } = buildController(doc, win);
      await flushPromises();

      const clickHandler = chartApi.calls.find(c => c.method === 'subscribeClick').args[1];
      controller._isRPressed = true;
      clickHandler({ time: 1001.5 });

      expect(socket.emissions).toContainEqual({
        event: 'seek',
        payload: { fromTime: expect.any(Number) },
      });
      expect(controller.historicalBars.at(-1).time).toBeLessThanOrEqual(1001.5);
    });
  });

  describe('destroy', () => {
    it('destroys cleanly', async () => {
      const { doc, win } = setupDocument();
      const { controller, chartApi } = buildController(doc, win);
      await flushPromises();

      controller.destroy();
      expect(chartApi.calls.some(c => c.method === 'destroy')).toBe(true);
    });
  });

  describe('helpers', () => {
    it('queues a bar', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller.queueBar({ time: 1 });
      expect(controller.pendingBars).toContainEqual({ time: 1 });
    });

    it('clears pending bars', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller.pendingBars = [{ time: 1 }];
      controller.clearPendingBars();
      expect(controller.pendingBars).toHaveLength(0);
    });

    it('sets live mode and playing state', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller.setLiveMode(true);
      expect(controller.liveMode).toBe(true);

      controller.setPlaying(true);
      expect(controller.isPlaying).toBe(true);
    });

    it('handles history ready after timeout', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      controller.handleHistoryReady();
      await new Promise(r => setTimeout(r, 200));

      expect(controller.historyReady).toBe(true);
    });

    it('logs error when initBars fails during history ready', async () => {
      const { doc, win } = setupDocument();
      const { controller } = buildController(doc, win);
      await flushPromises();

      vi.spyOn(controller, 'initBars').mockRejectedValue(new Error('bars failed'));
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

      controller.handleHistoryReady();
      await new Promise(r => setTimeout(r, 200));

      expect(consoleError).toHaveBeenCalledWith(
        '[ChartController] initBars failed during history ready:',
        expect.any(Error)
      );
      consoleError.mockRestore();
    });
  });
});
