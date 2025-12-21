export class ChartViewer {
  /**
   * @param {HTMLElement} chartElement
   * @param {DataService} dataService
   */
  constructor(chartElement, dataService, socket, opts = {}) {
    this.chartElement = chartElement;
    this.dataService  = dataService;
    this.socket = socket;
    this.onDisplay = opts.onDisplay || (() => {});
    this.lastTime     = -Infinity;
    this.lastPrice    = null;
    this.pair         = null;
    this.currentTF    = opts.timeframe || '5m';
    this.isPlaying    = false;
    this.activeTrade = null;
    this.historicalBars = [];
    this.tradeMarkers   = [];
    this.lastBarTs = null;
    this.keepClosedTradeLines = opts.keepClosedTradeLines || false;
    
    // Use the passed startTime to limit history fetching (for test scenarios)
    this.startTime = opts.startTime || null;
    
    // New option to keep blue lines even after they trigger
    this.keepStrategyLines = opts.keepStrategyLines || false;

    this.formats = {
      NQ:     { precision: 2,    minMove: 0.01    },
      EURUSD: { precision: 5,    minMove: 0.00001 }
    };

    // PnL display
    this.tradeMarkers = [];
    this.winCount     = 0;
    this.lossCount    = 0;
    this.winCounter   = document.getElementById('winCount');
    this.lossCounter  = document.getElementById('lossCount');
    this.pnlCounter   = document.getElementById('pnlCounter');
    this.totalPnL    = 0;
    if (this.pnlCounter) this.pnlCounter.textContent = '0%';

    // SL/TP/Entry line handles
    this.tradeEntryLine = null;
    this.tradeSLLine    = null;
    this.tradeTPLine    = null;

    // storage for preview lines
    this.previewEntryLine = null;
    this.previewSLLine    = null;
    this.previewTPLine    = null;

    // block default context menu
    this.chartElement.addEventListener('contextmenu', e => e.preventDefault());

    // chart setup
    this.chartOptions = {
      layout: { background: { type: 'solid', color: 'white' }, textColor: 'black' },
      grid:   { vertLines: { color: '#e1e1e1' }, horzLines: { color: '#e1e1e1' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      timeScale: {
        visible: true,
        timeVisible: true,
        shiftVisibleRangeOnNewBar: true,
      },
      width: chartElement.clientWidth,
      height: chartElement.clientHeight
    };
    this.chart  = LightweightCharts.createChart(chartElement, this.chartOptions);
    this.series = this.chart.addCandlestickSeries();
    this.series.applyOptions({
        upColor:         'white',
        borderUpColor:   'black',
        wickUpColor:     'black',
        downColor:       'black',
        borderDownColor: 'black',
        wickDownColor:   'black',
      });
    this.series.setData([]);

    this.isRPressed = false;
    window.addEventListener('keydown', (e) => {
      if (e.key.toLowerCase() === 'r') this.isRPressed = true;
    });
    window.addEventListener('keyup', (e) => {
      if (e.key.toLowerCase() === 'r') this.isRPressed = false;
    });

    this.chart.subscribeClick(param => {
      if (!this.isRPressed) {
        return;
      }
      if (param && param.time) {
        this._onChartClick(param.time);
      }
    });

    // temporary neutral price format until pair is known
    this.series.applyOptions({
      priceFormat: { type: 'price', precision: 2, minMove: 1 },
      lastValueVisible: false,
      priceLineVisible: false
    });

    // handle resize
    new ResizeObserver(() => {
      this.chart.resize(
        this.chartElement.clientWidth,
        this.chartElement.clientHeight
      );
    }).observe(this.chartElement);

    // session shading
    this.londonSeries = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top:0, bottom:0 }, lineWidth:0, overlay:true, color:'rgba(0,255,0,0.1)' });
    this.nySeries     = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top:0, bottom:0 }, lineWidth:0, overlay:true, color:'rgba(255,0,0,0.1)' });
    this.sessions = [
      //{ series: this.londonSeries, from: { h:4,  m:0 },  to: { h:12, m:30 } },
       { series: this.nySeries,     from: { h:8,  m:30 }, to: { h:16, m:0  } }
    ];

    // user-drawn levels
    this.pinnedLines = [];
    this.chartElement.addEventListener('mousedown', this._onMouseDown.bind(this));

    // initialize: fetch server PAIR first
    this._initPair()
      .then(() => this._initBars())
      .then(() => this._initLines())
      .then(() => {
          window.__chartReady = true; // Signal to Playwright
          return this._setupSocket();
      })
      .catch(console.error);
  }

  async _initPair() {
    this.pair = await this.dataService.getPair();
    this._applyPriceFormat();
  }

  _applyPriceFormat() {
    const f = this.formats[this.pair] || { precision: 2, minMove: 1 };
    this.series.applyOptions({
      priceFormat: { type: 'price', precision: f.precision, minMove: f.minMove }
    });
  }

  async _initBars() {
    // Pass this.startTime to fetchBars to limit history
    const bars = await this.dataService.fetchBars(this.pair, this.currentTF, this.startTime);
    this.historicalBars = bars;
    this.displayChart(bars);
    bars.forEach(bar => this._shadeBar(bar));
  }

  async _initLines() {
    const lines = await this.dataService.fetchLines(this.pair);
    lines.forEach(ld => {
      this._createLineOnChart(ld);
    });
  }

  _createLineOnChart(ld) {
    // Avoid duplicates
    if (this.pinnedLines.find(x => x.id === ld.id)) return;

    const line = this.series.createPriceLine({
      price: ld.price,
      color: 'blue',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true,
      title: 'Line'
    });
    this.pinnedLines.push({ line, id: ld.id });
  }

  _setupSocket() {
    this.socket.on('connect', () => console.log('[ChartViewer] socket connected, id=', this.socket.id));
    this.socket.on('disconnect', () => console.log('[ChartViewer] socket disconnected'));
    this.socket.on('connect_error', err => console.error('[ChartViewer] socket error', err));

    this.socket.on('bar', bar => {
      console.log(`[ChartViewer] bar event: time=${bar.time}, close=${bar.close}`);
      if (bar.time > this.lastTime) {
        this.series.update(bar);
        this.lastTime  = bar.time;
        this.lastPrice = bar.close;
        this._shadeBar(bar);
      }
    });

    // when server opens a trade, freeze our SL/TP
    this.socket.on('trade_open', trade => {
      this.activeTrade = trade;
      // immediately draw the locked-in levels
      this._drawTradeLines(trade);
    });

    this.socket.on('trade_close', (trade) => {
      this.activeTrade = null;
      this._drawResultMarker(trade)
    });

    this.socket.on('trade_update', (update) => {
      console.log('[ChartViewer] trade_update received:', update);
      
      // Check if this update belongs to the currently displayed active trade
      if (this.activeTrade && this.activeTrade.trade_id === update.trade_id) {
        this.activeTrade.stop_loss = update.stop_loss;
        this._drawTradeLines(this.activeTrade);
      }
    });

    this.socket.on('line_removed', ({ id }) => {
      console.log('[ChartViewer] line_removed for id=', id);
      
      // FIX: If we are in test mode, keep the line visible for the screenshot
      if (this.keepStrategyLines) {
          console.log('[ChartViewer] Ignoring line removal (keepStrategyLines=true)');
          return;
      }

      const found = this.pinnedLines.find(o => o.id === id);
      if (!found) return;
      this.series.removePriceLine(found.line);
      this.pinnedLines = this.pinnedLines.filter(o => o.id !== id);
    });

    this.socket.on('stream_end', () => { window.__done = true; });

    this.socket.on('jump_result', (payload) => {
      if (payload && payload.to) {
        label.textContent = 'Current day: ' + tsToIso(payload.to).slice(0, 10) + ' ... ' + tsToIso(payload.to).slice(11, 16);
      }
    });
  }

  _shadeBar(bar) {
    const d = new Date(bar.time * 1000), h = d.getUTCHours(), m = d.getUTCMinutes();
    this.sessions.forEach(s => {
      const inSession =
        (h > s.from.h || (h === s.from.h && m >= s.from.m)) &&
        (h < s.to.h   || (h === s.to.h   && m <= s.to.m));
      if (inSession) s.series.update({ time: bar.time, value: 1 });
    });
  }

  _onMouseDown(e) {
    if (!e.shiftKey || (e.button !== 0 && e.button !== 2)) return;
    const rect = this.chartElement.getBoundingClientRect();
    const price = this.series.coordinateToPrice(e.clientY - rect.top);
    if (e.button === 0) this._addLine(price);
    else                this._removeNearestLine(price);
  }

  _onChartClick(clickedTime) {
    this.pauseReplay();

    // 1) find the *exact* bar under the click
    let idx = this.historicalBars.findIndex(b => b.time === clickedTime);
    if (idx === -1) {
      console.warn('No exact match for clickedTime, falling back to nearest earlier bar');
      idx = this.historicalBars
        .map((b, i) => ({ b, i }))
        .filter(x => x.b.time < clickedTime)
        .sort((a, b) => b.b.time - a.b.time)[0]?.i;
      if (idx === undefined) return;
    }

    // 2) redraw everything up to and including that bar
    const slice = this.historicalBars.slice(0, idx + 1);
    this.displayChart(slice);
    this.series.setMarkers([]);
    this.londonSeries.setData([]);
    this.nySeries.setData([]);

    // 3) use the bar’s own timestamp
    const bar = slice[slice.length - 1];
    this.lastTime  = bar.time;
    this.lastPrice = bar.close;

    // 4) tell the server to seek here
    this.socket.emit('seek', { fromTime: this.lastTime });
  }

  async _addLine(price) {
    const line = this.series.createPriceLine({
      price, color: 'blue', lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true,
      title: `Line ${this.pinnedLines.length + 1}`
    });
    try {
      // Use this.lastTime (current replay time) as creation time
      // If replay hasn't started, this.lastTime might be -Infinity or null, 
      // in which case the backend defaults to Now.
      const creationTime = (this.lastTime > 0) ? this.lastTime : null;

      const saved = await this.dataService.addLine(this.pair, price, creationTime);
      this.pinnedLines.push({ line, id: saved.id });
    } catch (err) {
      this.series.removePriceLine(line);
      console.error('Error adding line:', err);
    }
  }

  async _removeNearestLine(clickedPrice) {
    if (!this.pinnedLines.length) return;
    let nearest = null, diffMin = Infinity;
    this.pinnedLines.forEach(o => {
      const diff = Math.abs(o.line.options().price - clickedPrice);
      if (diff < diffMin) { diffMin = diff; nearest = o; }
    });
    if (nearest && diffMin <= clickedPrice * 0.01) {
      this.series.removePriceLine(nearest.line);
      this.pinnedLines = this.pinnedLines.filter(o => o !== nearest);
      await this.dataService.deleteLine(nearest.id);
    }
  }

  displayChart(bars) {
    this.series.setData(bars);
    if (bars.length) {
      const last = bars[bars.length - 1];
      this.lastTime  = last.time;
      this.lastPrice = last.close;
    }
    this.onDisplay();
  }

  _drawPreviews({ entry, stop_loss, take_profit }) {
    // remove old previews
    [ this.previewEntryLine, this.previewSLLine, this.previewTPLine ].forEach(l => l && this.series.removePriceLine(l));

    this.previewEntryLine = this.series.createPriceLine({
      price: entry,
      color: 'rgba(0, 128, 255, 0.8)',     // bright blue
      lineWidth: 2,                         // thicker
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'Entry (preview)'
    });
    this.previewSLLine = this.series.createPriceLine({
      price: stop_loss,
      color: 'rgba(255, 64, 64, 0.8)',      // strong red
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'SL (preview)'
    });
    this.previewTPLine = this.series.createPriceLine({
      price: take_profit,
      color: 'rgba(64, 255, 64, 0.8)',      // bright green
      lineWidth: 2,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: 'TP (preview)'
    });
  }

  // helper to clear previews once a trade is open
  clearPreviews() {
    [ this.previewEntryLine, this.previewSLLine, this.previewTPLine ]
      .forEach(l => l && this.series.removePriceLine(l));
    this.previewEntryLine =
    this.previewSLLine    =
    this.previewTPLine    = null;
  }

  _drawTradeLines(trade) {
    this.clearPreviews();

    // now draw the confirmed trade lines as before—
    // but you can make them a bit bolder if you like:
    [ this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine ]
      .forEach(l => l && this.series.removePriceLine(l));

    
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && this.series.removePriceLine(h));
    this.tradeEntryLine = this.series.createPriceLine({ price: trade.entry,      color: 'yellow', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'Entry' });
    this.tradeSLLine    = this.series.createPriceLine({ price: trade.stop_loss || trade.stopLoss, color: 'red',   lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'SL'    });
    this.tradeTPLine    = this.series.createPriceLine({ price: trade.take_profit || trade.takeProfit, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'TP'    });
  }

  _drawResultMarker(trade) {
    // remove entry/SL/TP lines
    if (!this.keepClosedTradeLines) {
      [ this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine ]
        .forEach(h => h && this.series.removePriceLine(h));
    }

    // draw the exit‐marker
    const exitTime = trade.exit_time || trade.exitTime || trade.entryTime;
    const isWin = trade.result > 0;
    const isLong = trade.type === 'long' || trade.type === 'buy';
    
    // Logic: Place marker near the exit price (High for Long Win/Short Loss, Low for Long Loss/Short Win)
    // Long Win (High) -> Above
    // Long Loss (Low) -> Below
    // Short Win (Low) -> Below
    // Short Loss (High) -> Above
    
    let position = 'aboveBar';
    let shape = 'arrowDown';

    if (isLong) {
        if (isWin) { position = 'aboveBar'; shape = 'arrowDown'; }
        else       { position = 'belowBar'; shape = 'arrowUp';   }
    } else {
        if (isWin) { position = 'belowBar'; shape = 'arrowUp';   }
        else       { position = 'aboveBar'; shape = 'arrowDown'; }
    }

    const marker   = {
      time:     exitTime,
      position: position,
      shape:    shape,
      color:    isWin ? '#00E676' : '#FF1744', // Bright Green / Red
      text:     trade.result > 0 ? `+${trade.result}` : `${trade.result}`,
      size:     2, // Make it bigger (default is 1)
    };
    this.tradeMarkers.push(marker);
    this.series.setMarkers(this.tradeMarkers);

    // update win/loss counters
    if (trade.result > 0) {
      this.winCount++;
      if (this.winCounter) this.winCounter.textContent = this.winCount;
    } else {
      this.lossCount++;
      if (this.lossCounter) this.lossCounter.textContent = this.lossCount;
    }

    // **update the total PnL display**
    this.totalPnL += trade.result;
    if (this.pnlCounter) {
      this.pnlCounter.textContent = `Total PnL: ${this.totalPnL}%`;
    }
  }

  // ───── Replay & Timeframe Controls ─────
  startReplay(tf = this.currentTF, fromTime = this.lastTime) {
    console.log('[ChartViewer] ▶️ startReplay — tf:', tf, 'pair:', this.pair);
    this.lastTime = fromTime - 1;
    this.socket.emit('start_stream', { timeframe: tf, pair: this.pair, fromTime: fromTime });
    this.isPlaying = true;
  }

  pauseReplay() {
    console.log('[ChartViewer] ⏸ pauseReplay');
    this.socket.emit('pause_stream');
    this.isPlaying = false;
  }

  toggleReplay() {
    console.log('[ChartViewer] toggleReplay called — isPlaying before:', this.isPlaying);
    if (this.isPlaying) this.pauseReplay();
    else                this.startReplay();
    console.log('[ChartViewer] toggleReplay after — isPlaying now:', this.isPlaying);
  }

  async changeTimeframe(tf) {
    this.currentTF = tf;
    this.pauseReplay();
    this.series.setData([]);
    this.tradeMarkers = [];
    this.series.setMarkers([]);
    this.londonSeries.setData([]);
    this.nySeries.setData([]);

    // FIX: Only use this.startTime if it is explicitly set (test mode).
    // Otherwise pass null to fetch full history.
    const bars = await this.dataService.fetchBars(this.pair, tf, this.startTime);
    this.historicalBars = bars;
    this.displayChart(bars);
    bars.forEach(bar => this._shadeBar(bar));
  }

  jumpToDay(direction = 1) {
    if (!this.lastTime) return;
    const ONE_DAY = 86400;

    // Start where we are, stop one day away
    const fromTime = this.lastTime;
    const stopAt = direction === 1
      ? this.lastTime + ONE_DAY       // forward a day
      : Math.max(0, this.lastTime - ONE_DAY); // backward a day

    this.socket.emit('start_stream', {
      timeframe: this.currentTF || '5m',
      fromTime,
      stopAt
    });
  }

  startDayReplay(fromTime) {
    const ONE_DAY = 86400;
    const stopAt = fromTime + ONE_DAY;

    this.socket.emit('start_stream', {
      timeframe: this.currentTF || '5m',
      fromTime,
      stopAt
    });
  }
}