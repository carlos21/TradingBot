import { calculateTSI, detectCrosses } from './TSICalculator.js';
import { MarkerManager } from './MarkerManager.js';
import { SocketHandler } from './SocketHandler.js';

const PRICE_FORMATS = {
  MNQ:    { precision: 2, minMove: 0.01 },
  EURUSD: { precision: 5, minMove: 0.00001 },
};

const NY_SESSION = { from: { h: 9, m: 30 }, to: { h: 16, m: 0 } };

export class ChartViewer {
  constructor(chartElement, dataService, socket, opts = {}) {
    this.chartElement = chartElement;
    this.dataService = dataService;
    this.socket = socket;

    // State
    this.lastTime = -Infinity;
    this.lastPrice = null;
    this.pair = null;
    this.currentTF = opts.timeframe || '1m';
    this.isPlaying = false;
    this.liveMode = false;
    this.activeTrade = null;

    this.historicalBars = [];
    this.allTrades = [];
    this.pinnedLines = [];
    this.allTradeLines = [];  // track all Entry/SL/TP price lines
    this._seriesBusy = false;
    this._lastShadedTime = -Infinity;
    this.historyReady = false;
    this.pendingBars = [];

    // Config
    this.keepClosedTradeLines = opts.keepClosedTradeLines || false;
    this.startTime = opts.startTime || null;
    this.keepStrategyLines = opts.keepStrategyLines || false;
    this.showTSI = opts.showTSI !== false;

    // Time formatters
    this.displayFormatter = new Intl.DateTimeFormat('en-US', {
      timeZone: 'Etc/GMT+5',
      month: 'short', day: 'numeric',
      hour12: false, hour: '2-digit', minute: '2-digit',
    });
    this.nyTimeFormatter = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour12: false, hour: '2-digit', minute: '2-digit',
    });

    const formatTime = time => this.displayFormatter.format(new Date(time * 1000));

    // Chart
    this.chart = LightweightCharts.createChart(chartElement, {
      layout: { background: { type: 'solid', color: 'white' }, textColor: 'black' },
      grid: { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { visible: true, labelVisible: true },
        horzLine: { visible: true, labelVisible: true },
      },
      localization: { locale: 'en-US', timeFormatter: formatTime },
      rightPriceScale: {
        visible: true, borderColor: '#d1d5db', minimumWidth: 75,
        scaleMargins: { top: 0.05, bottom: this.showTSI ? 0.25 : 0.05 },
      },
      timeScale: {
        visible: true, timeVisible: true,
        shiftVisibleRangeOnNewBar: true,
        tickMarkFormatter: formatTime,
      },
      width: chartElement.clientWidth || 800,
      height: chartElement.clientHeight || 600,
    });

    // Price series
    this.series = this.chart.addCandlestickSeries({
      upColor: 'white', borderUpColor: 'black', wickUpColor: 'black',
      downColor: 'black', borderDownColor: 'black', wickDownColor: 'black',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    // TSI series
    if (this.showTSI) {
      this.chart.priceScale('tsi').applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
        visible: true, borderVisible: false,
      });
      this.tsiSeries = this.chart.addLineSeries({ color: 'blue', lineWidth: 2, priceScaleId: 'tsi', title: 'TSI' });
      this.sigSeries = this.chart.addLineSeries({ color: 'red', lineWidth: 2, priceScaleId: 'tsi', title: 'Signal' });
      this.tsiSeries.createPriceLine({ price: 0, color: '#999', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: false });
    }

    // Session shading
    this.nySeries = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top: 0, bottom: 0 }, lineWidth: 0, overlay: true, color: 'rgba(255,0,0,0.1)' });

    // Markers
    this.markers = new MarkerManager(this.series);

    // Resize
    const resizer = new ResizeObserver(entries => {
      for (const entry of entries) {
        if (entry.target === this.chartElement) {
          this.chart.resize(entry.contentRect.width, entry.contentRect.height);
        }
      }
    });
    resizer.observe(this.chartElement);
    setTimeout(() => {
      if (this.chartElement) this.chart.resize(this.chartElement.clientWidth, this.chartElement.clientHeight);
    }, 100);

    // Line drawing interactions
    this.chartElement.addEventListener('contextmenu', e => e.preventDefault());
    this.chartElement.addEventListener('mousedown', this._onMouseDown.bind(this));

    this.isRPressed = false;
    window.addEventListener('keydown', e => { if (e.key.toLowerCase() === 'r') this.isRPressed = true; });
    window.addEventListener('keyup', e => { if (e.key.toLowerCase() === 'r') this.isRPressed = false; });
    this.chart.subscribeClick(param => {
      if (this.isRPressed && param && param.time) this._onChartClick(param.time);
    });

    // Init
    this._initPair()
      .then(() => {
        new SocketHandler(socket, this).init();
        if (!this.liveMode) {
          return this.initBars();
        }
        // In live mode, initBars is triggered by the history_loaded event. The
        // server emits it immediately on connect if cached bars already exist,
        // otherwise it fires once NinjaTrader finishes a history load cycle.
      })
      .then(() => this._initLines())
      .then(() => {
        if (!this.liveMode) {
          this.historyReady = true;
        }
        window.__chartReady = true;
      })
      .catch(console.error);
  }

  // --- Initialization ---

  async _initPair() {
    this.pair = await this.dataService.getPair();
    const f = PRICE_FORMATS[this.pair] || { precision: 2, minMove: 0.01 };
    this.series.applyOptions({ priceFormat: { type: 'price', precision: f.precision, minMove: f.minMove } });
  }

  async initBars() {
    // Prevent concurrent execution with changeTimeframe
    if (this._seriesBusy) {
      console.log('[ChartViewer] initBars: already busy, skipping');
      return;
    }
    this._seriesBusy = true;
    try {
      const bars = await this.dataService.fetchBars(this.pair, this.currentTF, this.startTime);
      this.historicalBars = bars;
      this._displayChart(bars);
      // Load trades before recalculating TSI so marker times are in sync
      await this._initTrades();
      this.recalculateTSI();
      this.shadeBars(bars);
    } finally {
      this._seriesBusy = false;
    }
  }

  async _initLines() {
    const lines = await this.dataService.fetchLines(this.pair);
    lines.forEach(ld => this._createLineOnChart(ld));
  }

  async _initTrades() {
    try {
      this.allTrades = await this.dataService.fetchTrades(this.pair);
      const validTimes = new Set(this.historicalBars.map(b => b.time));
      this.markers.update(this.allTrades, this.lastTime, validTimes);
    } catch (e) { console.error(e); }
  }

  // --- TSI ---

  recalculateTSI() {
    if (!this.showTSI) return;
    const bars = this.historicalBars;
    const { tsiData, signalData, tsiRaw, signalRaw } = calculateTSI(bars);

    if (!bars || bars.length < 14) {
      if (this.tsiSeries) this.tsiSeries.setData([]);
      if (this.sigSeries) this.sigSeries.setData([]);
      return;
    }

    const times = bars.map(b => b.time);
    const validTimes = new Set(times);
    this.markers.setTSIMarkers(detectCrosses(tsiRaw, signalRaw, times));

    try {
      if (this.tsiSeries) this.tsiSeries.setData(tsiData);
      if (this.sigSeries) this.sigSeries.setData(signalData);
      this.markers.update(this.allTrades, this.lastTime, validTimes);
    } catch (err) {
      console.error(err);
    }
  }

  // --- Display ---

  _displayChart(bars) {
    const valid = bars.filter(b => b && b.open != null && b.high != null && b.low != null && b.close != null);
    if (valid.length !== bars.length) {
      console.warn(`[ChartViewer] dropped ${bars.length - valid.length} bars with null OHLC`);
    }
    // Set data first, then clear markers to avoid "Value is null" error
    // This can happen when setMarkers is called on a series with no data
    this.series.setData(valid);
    this.nySeries.setData([]);
    this._lastShadedTime = -Infinity;
    if (valid.length) {
      const last = valid[valid.length - 1];
      this.lastTime = last.time;
      this.lastPrice = last.close;
    }
    // Only clear markers after data is set and we have valid bars
    // This prevents "Value is null" error from lightweight-charts
    if (valid.length > 0) {
      this.series.setMarkers([]);
    }
    this.chart.timeScale().fitContent();
  }

  // --- Session Shading ---

  shadeBars(bars) {
    /* Bulk session shading for historical initialization. Uses setData instead of update. */
    const sessionBars = [];
    for (const bar of bars) {
      if (!bar || typeof bar.time !== 'number') continue;
      const parts = this.nyTimeFormatter.formatToParts(new Date(bar.time * 1000));
      let h, m;
      for (const part of parts) {
        if (part.type === 'hour') h = parseInt(part.value, 10);
        if (part.type === 'minute') m = parseInt(part.value, 10);
      }
      const s = NY_SESSION;
      const inSession = (h > s.from.h || (h === s.from.h && m >= s.from.m))
                     && (h < s.to.h || (h === s.to.h && m <= s.to.m));
      if (inSession) sessionBars.push({ time: bar.time, value: 1 });
    }
    this.nySeries.setData(sessionBars);
    this._lastShadedTime = sessionBars.length > 0 ? sessionBars[sessionBars.length - 1].time : -Infinity;
  }

  shadeBar(bar) {
    if (!bar || typeof bar.time !== 'number') return;

    // Prevent duplicate updates which crash lightweight-charts
    if (bar.time <= this._lastShadedTime) return;

    const parts = this.nyTimeFormatter.formatToParts(new Date(bar.time * 1000));
    let h, m;
    for (const part of parts) {
      if (part.type === 'hour') h = parseInt(part.value, 10);
      if (part.type === 'minute') m = parseInt(part.value, 10);
    }
    const s = NY_SESSION;
    const inSession = (h > s.from.h || (h === s.from.h && m >= s.from.m))
                   && (h < s.to.h || (h === s.to.h && m <= s.to.m));
    if (inSession) {
      try {
        this.nySeries.update({ time: bar.time, value: 1 });
        this._lastShadedTime = bar.time;
      } catch (e) {
        if (!e.message?.includes('Cannot update oldest data')) throw e;
      }
    }
  }

  // --- Lines ---

  async _addLine(price) {
    const line = this.series.createPriceLine({ price, color: 'blue', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: 'Line' });
    try {
      const creationTime = (this.lastTime > 0) ? this.lastTime : null;
      const saved = await this.dataService.addLine(this.pair, price, creationTime);
      this.pinnedLines.push({ line, id: saved.id });
    } catch (err) {
      this.series.removePriceLine(line);
      console.error(err);
    }
  }

  async _removeNearestLine(clickedPrice) {
    if (!this.pinnedLines.length) return;
    let nearest = null, diffMin = Infinity;
    for (const o of this.pinnedLines) {
      const diff = Math.abs(o.line.options().price - clickedPrice);
      if (diff < diffMin) { diffMin = diff; nearest = o; }
    }
    if (nearest && diffMin <= clickedPrice * 0.01) {
      this.series.removePriceLine(nearest.line);
      this.pinnedLines = this.pinnedLines.filter(o => o !== nearest);
      await this.dataService.deleteLine(nearest.id);
    }
  }

  _createLineOnChart(ld) {
    if (this.pinnedLines.find(x => x.id === ld.id)) return;
    const line = this.series.createPriceLine({ price: ld.price, color: 'blue', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: 'Line' });
    this.pinnedLines.push({ line, id: ld.id });
  }

  // --- Trade Lines ---

  drawTradeLines(trade) {
    const isSameTrade = this.activeTrade && this.activeTrade.trade_id === trade.trade_id;
    if (isSameTrade || !this.keepClosedTradeLines) {
      [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && this.series.removePriceLine(h));
      this.allTradeLines = this.allTradeLines.filter(h => h !== this.tradeEntryLine && h !== this.tradeSLLine && h !== this.tradeTPLine);
    } else {
      [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && h.applyOptions({ lineStyle: LightweightCharts.LineStyle.Dashed, lineWidth: 1 }));
    }
    const n = this.allTrades.findIndex(t => t.trade_id === trade.trade_id) + 1;
    const isOppCross = trade.close_on_opposite_cross;
    // TSI cross strategy has no re-entry concept — every trade is an independent entry
    const entryLabel = (isOppCross ? 'Entry #' + n : (n > 1 ? 'Re-entry #' + n : 'Entry #' + n)) + (isOppCross ? ' → OppCross' : '');
    this.tradeEntryLine = this.series.createPriceLine({ price: trade.entry, color: 'yellow', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: entryLabel });
    this.tradeSLLine = this.series.createPriceLine({ price: trade.stop_loss || trade.stopLoss, color: 'red', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'SL #' + n });
    if (isOppCross) {
      // No fixed TP — draw a faint "floating" label line instead
      this.tradeTPLine = this.series.createPriceLine({ price: trade.entry, color: '#4CAF50', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'Exit: next opp. cross' });
    } else {
      this.tradeTPLine = this.series.createPriceLine({ price: trade.take_profit || trade.takeProfit, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'TP #' + n });
    }
    this.allTradeLines.push(this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine);
  }

  clearAllTradeLines() {
    this.allTradeLines.forEach(h => h && this.series.removePriceLine(h));
    this.allTradeLines = [];
    this.tradeEntryLine = null;
    this.tradeSLLine = null;
    this.tradeTPLine = null;
  }

  showOnlyTrade(tradeId) {
    this.clearAllTradeLines();
    const trade = this.allTrades.find(t => t.trade_id === tradeId);
    if (!trade) return;
    const n = this.allTrades.findIndex(t => t.trade_id === tradeId) + 1;
    const isOppCross = trade.close_on_opposite_cross;
    // TSI cross strategy has no re-entry concept — every trade is an independent entry
    const entryLabel = (isOppCross ? 'Entry #' + n : (n > 1 ? 'Re-entry #' + n : 'Entry #' + n)) + (isOppCross ? ' → OppCross' : '');
    this.tradeEntryLine = this.series.createPriceLine({ price: trade.entry, color: 'yellow', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: entryLabel });
    // Always show the ORIGINAL SL as the primary line
    const origSL = trade.orig_sl || trade.stop_loss || trade.stopLoss;
    this.tradeSLLine = this.series.createPriceLine({ price: origSL, color: 'red', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'SL #' + n });
    if (isOppCross) {
      this.tradeTPLine = this.series.createPriceLine({ price: trade.entry, color: '#4CAF50', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'Exit: next opp. cross' });
    } else {
      this.tradeTPLine = this.series.createPriceLine({ price: trade.take_profit || trade.takeProfit, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'TP #' + n });
    }
    this.allTradeLines.push(this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine);
    // If SL was moved, show the adjusted SL as a thin dashed line
    const currSL = trade.stop_loss || trade.stopLoss;
    if (currSL && origSL && Math.abs(currSL - origSL) > 0.01) {
      this.allTradeLines.push(this.series.createPriceLine({ price: currSL, color: '#ff5252', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'Adj SL #' + n }));
    }
    // Hide markers from other trades so the snapshot shows only this trade
    const validTimes = this.historicalBars && this.historicalBars.length > 0
      ? new Set(this.historicalBars.map(b => b.time))
      : null;
    this.markers.update([trade], this.lastTime, validTimes);
  }

  // --- Interaction ---

  _onMouseDown(e) {
    if (!e.shiftKey || (e.button !== 0 && e.button !== 2)) return;
    const rect = this.chartElement.getBoundingClientRect();
    const price = this.series.coordinateToPrice(e.clientY - rect.top);
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
      this.recalculateTSI();
      this.markers.update(this.allTrades, this.lastTime);
      this.socket.emit('seek', { fromTime: this.lastTime });
    }
  }

  // --- Replay Controls ---

  startReplay(tf = this.currentTF, fromTime = this.lastTime) {
    if (fromTime < this.lastTime) {
      const idx = this.historicalBars.findIndex(b => b.time >= fromTime);
      if (idx !== -1) {
        this.historicalBars = this.historicalBars.slice(0, idx);
        this._displayChart(this.historicalBars);
        this.recalculateTSI();
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
    this.socket.emit('step_stream', { timeframe: this.currentTF, pair: this.pair, fromTime: this.lastTime });
    this.isPlaying = false;
  }

  async changeTimeframe(tf) {
    // Prevent concurrent execution with initBars or other timeframe changes
    if (this._seriesBusy) {
      console.log('[ChartViewer] changeTimeframe: already busy, skipping');
      return;
    }
    this._seriesBusy = true;
    try {
      // Cancel any pending initBars from a history_loaded/trading_ready event so they
      // don't race with this TF change and call setData on a half-ready series.
      clearTimeout(this._historyReadyTimer);

      // Clear TSI markers (trade markers will be cleared in _displayChart after data is set)
      this.markers.setTSIMarkers([]);

      this.currentTF = tf;
      this.pauseReplay();

      if (this.showTSI) {
        this.tsiSeries.setData([]);
        this.sigSeries.setData([]);
      }
      this.nySeries.setData([]);

      const replayPos = isFinite(this.lastTime) ? this.lastTime : null;
      let bars = await this.dataService.fetchBars(this.pair, tf, this.startTime);

      if (!this.liveMode && replayPos !== null && bars.length > 0 && replayPos < bars[bars.length - 1].time) {
        bars = bars.filter(b => b.time <= replayPos);
      }

      this.historicalBars = bars;
      this._displayChart(bars);
      
      // Refresh trades first (so trade times match the new timeframe bars)
      // before recalculating TSI which calls markers.update()
      await this._initTrades();
      
      this.recalculateTSI();

      this.socket.emit('set_timeframe', { timeframe: tf, fromTime: this.lastTime });
      this.chart.timeScale().fitContent();
      this.shadeBars(bars);
    } finally {
      this._seriesBusy = false;
    }
  }

  jumpToDay(direction = 1) {
    if (!this.lastTime) return;
    const ONE_DAY = 86400;
    this.startReplay(this.currentTF, this.lastTime + direction * ONE_DAY);
  }
}
