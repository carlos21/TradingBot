import { calculateTSI, detectCrosses } from '../TSICalculator.js';
import { isInNewYorkSession, formatChartTime } from '../domain/time.js';
import {
  buildTradeLineDescriptors,
  buildSingleTradeLineDescriptors,
  upsertTrade,
} from '../domain/trade.js';
import { composeMarkers } from '../domain/marker.js';

const PRICE_FORMATS = {
  MNQ: { precision: 2, minMove: 0.01 },
  EURUSD: { precision: 5, minMove: 0.00001 },
};

const DEFAULT_OPTIONS = {
  timeframe: '1m',
  keepStrategyLines: false,
  keepClosedTradeLines: false,
  startTime: null,
  showTSI: true,
};

/**
 * ChartController orchestrates the chart view.
 *
 * Dependencies (ports) are injected via the constructor so the controller
 * can be unit-tested with fake implementations.
 */
export class ChartController {
  constructor(deps) {
    this.dom = deps.domService;
    this.http = deps.httpClient;
    this.socket = deps.socket;
    this.chartApi = deps.chartApi;
    this.storage = deps.storage;
    this.options = { ...DEFAULT_OPTIONS, ...(deps.options || {}) };

    this.chartElement = this.dom.getElementById('chartContainer');

    // State
    this.lastTime = -Infinity;
    this.lastPrice = null;
    this.pair = null;
    this.currentTF = this.options.timeframe;
    this.isPlaying = false;
    this.liveMode = false;
    this.activeTrade = null;
    this.historyReady = false;

    this.historicalBars = [];
    this.allTrades = [];
    this.validTimes = new Set();
    this.pendingBars = [];

    this.pinnedLines = []; // strategy lines
    this.allTradeLines = []; // handles for every entry/sl/tp line drawn
    this.tradeEntryLine = null;
    this.tradeSLLine = null;
    this.tradeTPLine = null;

    this._seriesBusy = false;
    this._lastShadedTime = -Infinity;
    this._historyReadyTimer = null;
    this._tsiMarkers = [];
    this._resizeObserver = null;
    this._isRPressed = false;

    this._init();
  }

  async _init() {
    try {
      await this._initPair();
      this._initChart();
      this._applyPriceFormat();
      this._bindInteractions();
      this._bindResize();
      this._emitReady();

      if (!this.liveMode) {
        await this.initBars();
        this.historyReady = true;
      }
    } catch (err) {
      console.error('[ChartController] init failed:', err);
    }
  }

  _emitReady() {
    const win = this.dom.getWindow();
    if (win) win.__chartReady = true;
  }

  async _initPair() {
    const res = await this.http.get('/api/pair');
    this.pair = res.pair;
    return this.pair;
  }

  _initChart() {
    const formatTime = time => formatChartTime(time);

    this.chart = this.chartApi.createChart(this.chartElement, {
      layout: { background: { type: 'solid', color: 'white' }, textColor: 'black' },
      grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
      crosshair: {
        mode: 0, // LightweightCharts.CrosshairMode.Normal
        vertLine: { visible: true, labelVisible: true },
        horzLine: { visible: true, labelVisible: true },
      },
      localization: { locale: 'en-US', timeFormatter: formatTime },
      rightPriceScale: {
        visible: true,
        borderColor: '#d1d5db',
        minimumWidth: 75,
        scaleMargins: { top: 0.05, bottom: this.options.showTSI ? 0.25 : 0.05 },
      },
      timeScale: {
        visible: true,
        timeVisible: true,
        shiftVisibleRangeOnNewBar: true,
        tickMarkFormatter: formatTime,
      },
      width: this.chartElement?.clientWidth || 800,
      height: this.chartElement?.clientHeight || 600,
    });

    this.priceSeries = this.chartApi.addCandlestickSeries(this.chart, {
      upColor: 'white',
      borderUpColor: 'black',
      wickUpColor: 'black',
      downColor: 'black',
      borderDownColor: 'black',
      wickDownColor: 'black',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    if (this.options.showTSI) {
      this.chartApi.applyPriceScaleOptions(this.chart, 'tsi', {
        scaleMargins: { top: 0.8, bottom: 0 },
        visible: true,
        borderVisible: false,
      });
      this.tsiSeries = this.chartApi.addLineSeries(this.chart, {
        color: 'blue',
        lineWidth: 2,
        priceScaleId: 'tsi',
        title: 'TSI',
      });
      this.sigSeries = this.chartApi.addLineSeries(this.chart, {
        color: 'red',
        lineWidth: 2,
        priceScaleId: 'tsi',
        title: 'Signal',
      });
      this.chartApi.createPriceLine(this.tsiSeries, {
        price: 0,
        color: '#999',
        lineWidth: 1,
        lineStyle: 2, // Dotted
        axisLabelVisible: false,
      });
    }

    this.nySeries = this.chartApi.addHistogramSeries(this.chart, {
      priceScaleId: '',
      scaleMargins: { top: 0, bottom: 0 },
      lineWidth: 0,
      overlay: true,
      color: 'rgba(255,0,0,0.1)',
    });
  }

  _applyPriceFormat() {
    const f = PRICE_FORMATS[this.pair] || { precision: 2, minMove: 0.01 };
    this.chartApi.applyOptions(this.priceSeries, {
      priceFormat: { type: 'price', precision: f.precision, minMove: f.minMove },
    });
  }

  _bindInteractions() {
    this.dom.addEventListener(this.chartElement, 'contextmenu', e => e.preventDefault());
    this.dom.addEventListener(this.chartElement, 'mousedown', this._onMouseDown.bind(this));

    const win = this.dom.getWindow();
    this.dom.addEventListener(win, 'keydown', e => {
      if (e.key.toLowerCase() === 'r') this._isRPressed = true;
    });
    this.dom.addEventListener(win, 'keyup', e => {
      if (e.key.toLowerCase() === 'r') this._isRPressed = false;
    });

    this.chartApi.subscribeClick(this.chart, param => {
      if (this._isRPressed && param?.time) this._onChartClick(param.time);
    });
  }

  _bindResize() {
    const ResizeObserver = this.dom.getWindow()?.ResizeObserver;
    if (!ResizeObserver || !this.chartElement) return;

    this._resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        if (entry.target === this.chartElement) {
          this.chartApi.resize(this.chart, entry.contentRect.width, entry.contentRect.height);
        }
      }
    });
    this._resizeObserver.observe(this.chartElement);

    setTimeout(() => {
      if (this.chartElement) {
        this.chartApi.resize(this.chart, this.chartElement.clientWidth, this.chartElement.clientHeight);
      }
    }, 100);
  }

  // --- Public API used by socket controller / controls ---

  async initBars() {
    if (this._seriesBusy) {
      console.log('[ChartController] initBars: already busy, skipping');
      return;
    }
    this._seriesBusy = true;
    try {
      const bars = await this._fetchBars(this.currentTF, this.options.startTime);
      this.historicalBars = bars;
      this._displayChart(bars);
      await this._initTrades();
      this._recalculateTSI();
      this._shadeBars(bars);
      try {
        await this.loadLines();
      } catch (err) {
        console.error('[ChartController] loadLines failed during initBars:', err);
      }
    } finally {
      this._seriesBusy = false;
      this._flushPendingBars();
    }
  }

  async _fetchBars(tf, startTime) {
    const url = `/api/bars?pair=${encodeURIComponent(this.pair)}&tf=${encodeURIComponent(tf)}&start_time=${encodeURIComponent(startTime)}`;
    return this.http.get(url);
  }

  _displayChart(bars) {
    const valid = bars.filter(b => b && b.open != null && b.high != null && b.low != null && b.close != null);
    if (valid.length !== bars.length) {
      console.warn(`[ChartController] dropped ${bars.length - valid.length} bars with null OHLC`);
    }

    this.validTimes = new Set(valid.map(b => b.time));
    this.chartApi.setData(this.priceSeries, valid);
    this.chartApi.setData(this.nySeries, []);
    this._lastShadedTime = -Infinity;

    if (valid.length) {
      const last = valid[valid.length - 1];
      this.lastTime = last.time;
      this.lastPrice = last.close;
    }

    if (valid.length > 0) {
      this.chartApi.setMarkers(this.priceSeries, []);
    }
    this.chartApi.fitContent(this.chart);
  }

  async _initTrades() {
    try {
      this.allTrades = await this._fetchTrades();
      this._updateMarkers();
    } catch (e) {
      console.error('[ChartController] failed to load trades:', e);
    }
  }

  async _fetchTrades() {
    return this.http.get(`/api/trades?pair=${encodeURIComponent(this.pair)}`);
  }

  _recalculateTSI() {
    if (!this.options.showTSI) return;
    const bars = this.historicalBars;
    if (!bars || bars.length < 14) {
      this.chartApi.setData(this.tsiSeries, []);
      this.chartApi.setData(this.sigSeries, []);
      return;
    }

    const { tsiData, signalData, tsiRaw, signalRaw } = calculateTSI(bars);
    const times = bars.map(b => b.time);
    this._tsiMarkers = detectCrosses(tsiRaw, signalRaw, times);
    this.chartApi.setData(this.tsiSeries, tsiData);
    this.chartApi.setData(this.sigSeries, signalData);
    this._updateMarkers();
  }

  _updateMarkers() {
    const markers = composeMarkers(this._tsiMarkers, this.allTrades, this.lastTime, this.validTimes);
    this.chartApi.setMarkers(this.priceSeries, markers);
  }

  _shadeBars(bars) {
    const sessionBars = [];
    for (const bar of bars) {
      if (!bar || typeof bar.time !== 'number') continue;
      if (isInNewYorkSession(bar.time)) {
        sessionBars.push({ time: bar.time, value: 1 });
      }
    }
    this.chartApi.setData(this.nySeries, sessionBars);
    this._lastShadedTime = sessionBars.length > 0 ? sessionBars[sessionBars.length - 1].time : -Infinity;
  }

  _shadeBar(bar) {
    if (!bar || typeof bar.time !== 'number') return;
    if (bar.time <= this._lastShadedTime) return;
    if (isInNewYorkSession(bar.time)) {
      try {
        this.chartApi.update(this.nySeries, { time: bar.time, value: 1 });
        this._lastShadedTime = bar.time;
      } catch (e) {
        if (!e.message?.includes('Cannot update oldest data')) throw e;
      }
    }
  }

  _flushPendingBars() {
    if (this.pendingBars.length === 0) return;
    const bars = this.pendingBars;
    this.pendingBars = [];
    for (const bar of bars) {
      if (bar.time >= this.lastTime) {
        this.chartApi.update(this.priceSeries, bar);
        const lastIdx = this.historicalBars.length - 1;
        if (lastIdx >= 0 && this.historicalBars[lastIdx].time === bar.time) {
          this.historicalBars[lastIdx] = bar;
        } else {
          this.historicalBars.push(bar);
        }
        this.validTimes.add(bar.time);
        this.lastTime = bar.time;
        this.lastPrice = bar.close;
        this._shadeBar(bar);
      }
    }
    this._recalculateTSI();
  }

  // --- Lines ---

  async loadLines() {
    for (const pinned of this.pinnedLines) {
      if (pinned.line) this.chartApi.removePriceLine(this.priceSeries, pinned.line);
    }
    this.pinnedLines = [];

    const lines = await this.http.get(`/api/lines?pair=${encodeURIComponent(this.pair)}`);
    for (const ld of lines) {
      this._createLineOnChart(ld);
    }
  }

  async _addLine(price) {
    const line = this.chartApi.createPriceLine(this.priceSeries, {
      price,
      color: 'blue',
      lineWidth: 1,
      lineStyle: 2, // Dotted
      axisLabelVisible: true,
      title: 'Line',
    });
    try {
      const creationTime = this.lastTime > 0 ? this.lastTime : null;
      const saved = await this.http.post('/api/lines', {
        pair: this.pair,
        price,
        creation_time: creationTime,
      });
      this.pinnedLines.push({ line, id: saved.id });
    } catch (err) {
      this.chartApi.removePriceLine(this.priceSeries, line);
      console.error(err);
    }
  }

  async _removeNearestLine(clickedPrice) {
    if (!this.pinnedLines.length) return;
    let nearest = null;
    let diffMin = Infinity;
    for (const o of this.pinnedLines) {
      const diff = Math.abs(this.chartApi.priceLineOptions(o.line).price - clickedPrice);
      if (diff < diffMin) {
        diffMin = diff;
        nearest = o;
      }
    }
    if (nearest && diffMin <= clickedPrice * 0.01) {
      this.chartApi.removePriceLine(this.priceSeries, nearest.line);
      this.pinnedLines = this.pinnedLines.filter(o => o !== nearest);
      await this.http.delete(`/api/lines/${encodeURIComponent(nearest.id)}`);
    }
  }

  _createLineOnChart(ld) {
    if (this.pinnedLines.find(x => x.id === ld.id)) return;
    const line = this.chartApi.createPriceLine(this.priceSeries, {
      price: ld.price,
      color: 'blue',
      lineWidth: 1,
      lineStyle: 2, // Dotted
      axisLabelVisible: true,
      title: 'Line',
    });
    this.pinnedLines.push({ line, id: ld.id });
  }

  // --- Trade lines ---

  _createTradeLines(trade, slPrice) {
    const idx = this.allTrades.findIndex(t => t.trade_id === trade.trade_id) + 1;
    const descriptors = buildTradeLineDescriptors(trade, { index: idx, slPrice });
    const handles = [];
    for (const d of descriptors) {
      const line = this.chartApi.createPriceLine(this.priceSeries, {
        price: d.price,
        color: d.color,
        lineWidth: d.lineWidth,
        lineStyle: d.lineStyle === 'dashed' ? 1 : d.lineStyle === 'dotted' ? 2 : 0,
        axisLabelVisible: true,
        title: d.title,
      });
      handles.push(line);
    }
    this.tradeEntryLine = handles[0];
    this.tradeSLLine = handles[1];
    this.tradeTPLine = handles[2];
    this.allTradeLines.push(...handles);
  }

  drawTradeLines(trade) {
    const isSameTrade = this.activeTrade && this.activeTrade.trade_id === trade.trade_id;
    if (isSameTrade || !this.options.keepClosedTradeLines) {
      [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => {
        if (h) this.chartApi.removePriceLine(this.priceSeries, h);
      });
      this.allTradeLines = this.allTradeLines.filter(
        h => h !== this.tradeEntryLine && h !== this.tradeSLLine && h !== this.tradeTPLine
      );
    } else {
      [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => {
        if (h) this.chartApi.applyLineOptions(h, { lineStyle: 1, lineWidth: 1 }); // dashed
      });
    }
    this._createTradeLines(trade);
  }

  clearAllTradeLines() {
    this.allTradeLines.forEach(h => {
      if (h) this.chartApi.removePriceLine(this.priceSeries, h);
    });
    this.allTradeLines = [];
    this.tradeEntryLine = null;
    this.tradeSLLine = null;
    this.tradeTPLine = null;
  }

  showOnlyTrade(tradeId) {
    this.clearAllTradeLines();
    const trade = this.allTrades.find(t => t.trade_id === tradeId);
    if (!trade) return;
    const idx = this.allTrades.findIndex(t => t.trade_id === tradeId) + 1;
    const origSL = trade.orig_sl ?? trade.stop_loss ?? trade.stopLoss;
    const descriptors = buildSingleTradeLineDescriptors(trade, idx);
    const handles = [];
    for (const d of descriptors) {
      const line = this.chartApi.createPriceLine(this.priceSeries, {
        price: d.price,
        color: d.color,
        lineWidth: d.lineWidth,
        lineStyle: d.lineStyle === 'dashed' ? 1 : d.lineStyle === 'dotted' ? 2 : 0,
        axisLabelVisible: true,
        title: d.title,
      });
      handles.push(line);
    }
    this.tradeEntryLine = handles[0];
    this.tradeSLLine = handles[1];
    this.tradeTPLine = handles[2];
    this.allTradeLines.push(...handles);

    this._updateMarkersForSingleTrade(trade);
  }

  _updateMarkersForSingleTrade(trade) {
    const markers = composeMarkers(
      this._tsiMarkers,
      [trade],
      this.lastTime,
      this.validTimes.size > 0 ? this.validTimes : null
    );
    this.chartApi.setMarkers(this.priceSeries, markers);
  }

  // --- Interaction ---

  _onMouseDown(e) {
    if (!e.shiftKey || (e.button !== 0 && e.button !== 2)) return;
    const rect = this.chartElement.getBoundingClientRect();
    const price = this.chartApi.coordinateToPrice(this.priceSeries, e.clientY - rect.top);
    if (e.button === 0) this._addLine(price);
    else this._removeNearestLine(price);
  }

  _onChartClick(clickedTime) {
    this.pauseReplay();
    let idx = this.historicalBars.findIndex(b => b.time === clickedTime);
    if (idx === -1) {
      idx = this.historicalBars.findIndex(b => b.time > clickedTime);
      if (idx > 0) idx--;
    }
    if (idx !== -1) {
      const slice = this.historicalBars.slice(0, idx + 1);
      this._displayChart(slice);
      this.historicalBars = slice;
      this._recalculateTSI();
      this._updateMarkers();
      this.socket.emit('seek', { fromTime: this.lastTime });
    }
  }

  // --- Replay controls ---

  startReplay(tf = this.currentTF, fromTime = this.lastTime) {
    if (fromTime < this.lastTime) {
      const idx = this.historicalBars.findIndex(b => b.time >= fromTime);
      if (idx !== -1) {
        this.historicalBars = this.historicalBars.slice(0, idx);
        this._displayChart(this.historicalBars);
        this._recalculateTSI();
      }
    }
    this.lastTime = fromTime - 1;
    this.socket.emit('start_stream', { timeframe: tf, pair: this.pair, fromTime });
    this.isPlaying = true;
  }

  pauseReplay() {
    this.socket.emit('pause_stream');
    this.isPlaying = false;
  }

  toggleReplay() {
    if (this.isPlaying) this.pauseReplay();
    else this.startReplay();
  }

  stepReplay() {
    if (this.isPlaying) this.pauseReplay();
    this.socket.emit('step_stream', {
      timeframe: this.currentTF,
      pair: this.pair,
      fromTime: this.lastTime,
    });
    this.isPlaying = false;
  }

  async changeTimeframe(tf) {
    if (this._seriesBusy) {
      console.log('[ChartController] changeTimeframe: already busy, skipping');
      return;
    }
    this._seriesBusy = true;
    try {
      clearTimeout(this._historyReadyTimer);
      this._tsiMarkers = [];
      this.currentTF = tf;
      this.pauseReplay();

      if (this.options.showTSI) {
        this.chartApi.setData(this.tsiSeries, []);
        this.chartApi.setData(this.sigSeries, []);
      }
      this.chartApi.setData(this.nySeries, []);

      const replayPos = isFinite(this.lastTime) ? this.lastTime : null;
      let bars = await this._fetchBars(tf, this.options.startTime);

      if (
        !this.liveMode &&
        replayPos !== null &&
        bars.length > 0 &&
        replayPos < bars[bars.length - 1].time
      ) {
        bars = bars.filter(b => b.time <= replayPos);
      }

      this.historicalBars = bars;
      this._displayChart(bars);
      await this._initTrades();
      this._recalculateTSI();
      this.socket.emit('set_timeframe', { timeframe: tf, fromTime: this.lastTime });
      this.chartApi.fitContent(this.chart);
      this._shadeBars(bars);
    } finally {
      this._seriesBusy = false;
      this._flushPendingBars();
    }
  }

  jumpToDay(direction = 1) {
    if (!this.lastTime) return;
    const ONE_DAY = 86400;
    this.startReplay(this.currentTF, this.lastTime + direction * ONE_DAY);
  }

  // --- Socket event handlers ---

  setHistoryReady(ready) {
    this.historyReady = ready;
  }

  setLiveMode(live) {
    this.liveMode = live;
  }

  setPlaying(playing) {
    this.isPlaying = playing;
  }

  async handleHistoryReady() {
    clearTimeout(this._historyReadyTimer);
    this._historyReadyTimer = setTimeout(async () => {
      try {
        await this.initBars();
      } catch (err) {
        console.error('[ChartController] initBars failed during history ready:', err);
      } finally {
        this.historyReady = true;
        this._flushPendingBars();
      }
    }, 150);
  }

  processBar(bar) {
    if (this._seriesBusy) {
      this.pendingBars.push(bar);
      return;
    }
    if (bar.time >= this.lastTime) {
      this.chartApi.update(this.priceSeries, bar);
      const lastIdx = this.historicalBars.length - 1;
      if (lastIdx >= 0 && this.historicalBars[lastIdx].time === bar.time) {
        this.historicalBars[lastIdx] = bar;
      } else {
        this.historicalBars.push(bar);
      }
      this.validTimes.add(bar.time);
      this.lastTime = bar.time;
      this.lastPrice = bar.close;
      this._shadeBar(bar);
      this._recalculateTSI();
    }
  }

  queueBar(bar) {
    this.pendingBars.push(bar);
  }

  handleIndicatorUpdate(data) {
    if (data.tf && data.tf !== this.currentTF) return;
    if (data.time >= this.lastTime) {
      if (this.tsiSeries) this.chartApi.update(this.tsiSeries, { time: data.time, value: data.tsi });
      if (this.sigSeries) this.chartApi.update(this.sigSeries, { time: data.time, value: data.signal });
      if (data.cross_type) {
        const marker = {
          time: data.time,
          position: data.cross_type === 'bullish' ? 'belowBar' : 'aboveBar',
          color: data.cross_type === 'bullish' ? '#00E676' : '#FF1744',
          shape: data.cross_type === 'bullish' ? 'arrowUp' : 'arrowDown',
          size: 1,
        };
        this._tsiMarkers.push(marker);
        this._updateMarkers();
      }
    }
  }

  handleTradeOpen(trade) {
    this.activeTrade = trade;
    this.allTrades = upsertTrade(this.allTrades, trade);
    this.drawTradeLines(trade);
    if (!this._seriesBusy) this._updateMarkers();
  }

  handleTradeClose(trade) {
    this.activeTrade = null;
    this.allTrades = upsertTrade(this.allTrades, { ...trade, status: 'closed' });
    if (!this.options.keepClosedTradeLines) {
      [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => {
        if (h) this.chartApi.removePriceLine(this.priceSeries, h);
      });
      this.allTradeLines = this.allTradeLines.filter(
        h => h !== this.tradeEntryLine && h !== this.tradeSLLine && h !== this.tradeTPLine
      );
    }
    if (!this._seriesBusy) this._updateMarkers();
  }

  handleTradeUpdate(update) {
    if (this.activeTrade && this.activeTrade.trade_id === update.trade_id) {
      this.activeTrade.stop_loss = update.stop_loss;
      this.drawTradeLines(this.activeTrade);
      if (!this._seriesBusy) this._updateMarkers();
    }
  }

  handleTradeEntryUpdate(update) {
    const idx = this.allTrades.findIndex(t => t.trade_id === update.trade_id);
    if (idx !== -1) {
      this.allTrades[idx] = {
        ...this.allTrades[idx],
        entry: update.entry_price,
        stop_loss: update.stop_loss,
        take_profit: update.take_profit,
        risk: update.risk,
      };
    }
    if (this.activeTrade && this.activeTrade.trade_id === update.trade_id) {
      this.activeTrade.entry = update.entry_price;
      this.activeTrade.stop_loss = update.stop_loss;
      this.activeTrade.take_profit = update.take_profit;
      this.activeTrade.risk = update.risk;
      this.drawTradeLines(this.activeTrade);
    }
    if (!this._seriesBusy) this._updateMarkers();
  }

  handleLineRemoved(id) {
    if (this.options.keepStrategyLines) return;
    const found = this.pinnedLines.find(o => o.id === id);
    if (!found) return;
    this.chartApi.removePriceLine(this.priceSeries, found.line);
    this.pinnedLines = this.pinnedLines.filter(o => o.id !== id);
  }

  clearPendingBars() {
    this.pendingBars = [];
  }

  destroy() {
    if (this._resizeObserver && this.chartElement) {
      this._resizeObserver.unobserve(this.chartElement);
    }
    if (this.chart) {
      this.chartApi.destroy(this.chart);
    }
  }
}
