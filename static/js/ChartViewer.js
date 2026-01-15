export class ChartViewer {
  /**
   * @param {HTMLElement} chartElement
   * @param {DataService} dataService
   * @param {Socket} socket
   * @param {Object} opts
   */
  constructor(chartElement, dataService, socket, opts = {}) {
    this.chartElement = chartElement;
    this.dataService  = dataService;
    this.socket = socket;
    this.onDisplay = opts.onDisplay || (() => {});
    
    // State
    this.lastTime     = -Infinity;
    this.lastPrice    = null;
    this.pair         = null;
    this.currentTF    = opts.timeframe || '5m';
    this.isPlaying    = false;
    this.activeTrade  = null;
    
    // Data Caches
    this.historicalBars = []; 
    this.allTrades      = []; 
    this.tradeMarkers   = []; 
    
    // Config
    this.keepClosedTradeLines = opts.keepClosedTradeLines || false;
    this.startTime = opts.startTime || null;
    this.keepStrategyLines = opts.keepStrategyLines || false;

    this.formats = {
      NQ:     { precision: 2,    minMove: 0.01    },
      EURUSD: { precision: 5,    minMove: 0.00001 }
    };

    // PnL / UI Elements
    this.winCount     = 0;
    this.lossCount    = 0;
    this.totalPnL     = 0;
    this.winCounter   = document.getElementById('winCount');
    this.lossCounter  = document.getElementById('lossCount');
    this.pnlCounter   = document.getElementById('pnlCounter');
    
    if (this.pnlCounter) this.pnlCounter.textContent = 'Total PnL: 0.00R';

    // Line Handles
    this.tradeEntryLine = null;
    this.tradeSLLine    = null;
    this.tradeTPLine    = null;
    this.previewEntryLine = null;
    this.previewSLLine    = null;
    this.previewTPLine    = null;
    this.pinnedLines      = [];

    // --- Chart Initialization ---
    this.chartElement.addEventListener('contextmenu', e => e.preventDefault());

    // --- Time Formatting Helpers (NY Time) ---
    
    // 1. Time Only (e.g. "09:30")
    const formatTimeNY = (timestamp) => {
      const date = new Date(timestamp * 1000);
      return date.toLocaleTimeString('en-US', {
        timeZone: 'America/New_York',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      });
    };

    // 2. Date Only (e.g. "Jan 14")
    const formatDateNY = (timestamp) => {
      const date = new Date(timestamp * 1000);
      return date.toLocaleDateString('en-US', {
        timeZone: 'America/New_York',
        month: 'short',
        day: 'numeric',
      });
    };

    // 3. Full Date + Time (e.g. "Jan 14, 09:30")
    const formatDateTimeNY = (timestamp) => {
      const date = new Date(timestamp * 1000);
      return date.toLocaleString('en-US', {
        timeZone: 'America/New_York',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
      });
    };

    // FIX: Create a reusable formatter for session logic (performance optimization)
    this.nyTimeFormatter = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: 'numeric',
      hour12: false
    });

    this.chartOptions = {
      layout: { background: { type: 'solid', color: 'white' }, textColor: 'black' },
      grid:   { vertLines: { color: '#e1e1e1' }, horzLines: { color: '#e1e1e1' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      
      // Force TimeScale to use NY Time for axis labels
      timeScale: {
        visible: true,
        timeVisible: true,
        shiftVisibleRangeOnNewBar: true,
        tickMarkFormatter: (time, tickMarkType, locale) => {
          // tickMarkType: 0=Year, 1=Month, 2=DayOfMonth, 3=Time, 4=TimeWithSeconds
          if (tickMarkType < 3) {
            return formatDateNY(time);
          }
          return formatTimeNY(time);
        },
      },
      
      // Force Crosshair to use NY Time (Date + Time)
      localization: {
        timeFormatter: (timestamp) => {
          return formatDateTimeNY(timestamp);
        }
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
        priceFormat:     { type: 'price', precision: 2, minMove: 1 },
    });
    this.series.setData([]);

    // Keyboard listeners for 'R' key
    this.isRPressed = false;
    window.addEventListener('keydown', (e) => {
      if (e.key.toLowerCase() === 'r') this.isRPressed = true;
    });
    window.addEventListener('keyup', (e) => {
      if (e.key.toLowerCase() === 'r') this.isRPressed = false;
    });

    this.chart.subscribeClick(param => {
      if (!this.isRPressed) return;
      if (param && param.time) {
        this._onChartClick(param.time);
      }
    });

    new ResizeObserver(() => {
      this.chart.resize(
        this.chartElement.clientWidth,
        this.chartElement.clientHeight
      );
    }).observe(this.chartElement);

    this.londonSeries = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top:0, bottom:0 }, lineWidth:0, overlay:true, color:'rgba(0,255,0,0.1)' });
    this.nySeries     = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top:0, bottom:0 }, lineWidth:0, overlay:true, color:'rgba(255,0,0,0.1)' });
    
    // Session definition (NY Time)
    this.sessions = [
       { series: this.nySeries,     from: { h:8,  m:30 }, to: { h:16, m:0  } }
    ];

    this.chartElement.addEventListener('mousedown', this._onMouseDown.bind(this));

    // --- Start Initialization Chain ---
    this._initPair()
      .then(() => this._initBars())
      .then(() => this._initLines())
      .then(() => this._initTrades())
      .then(() => {
          window.__chartReady = true; 
          return this._setupSocket();
      })
      .catch(console.error);
  }

  // --------------------------------------------------------------------------
  // Initialization Methods
  // --------------------------------------------------------------------------

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

  async _initTrades() {
    try {
      this.allTrades = await this.dataService.fetchTrades(this.pair);
      console.log(`[ChartViewer] Loaded ${this.allTrades.length} trades from API.`);
      this._updateMarkers();
    } catch (e) {
      console.error('[ChartViewer] Failed to load trades:', e);
    }
  }

  // --------------------------------------------------------------------------
  // Core Logic: Markers & PnL
  // --------------------------------------------------------------------------

  _getBarTimeForTimestamp(timestamp) {
    if (!this.historicalBars || this.historicalBars.length === 0) return null;
    const bar = this.historicalBars.find(b => b.time >= timestamp);
    return bar ? bar.time : null;
  }

  _updateMarkers() {
    if (!this.historicalBars.length) return;

    const markers = [];
    let win = 0, loss = 0, pnl = 0;

    console.groupCollapsed(`[ChartViewer] _updateMarkers (Trades: ${this.allTrades.length}, Bars: ${this.historicalBars.length}, LastTime: ${this.lastTime})`);

    this.allTrades.forEach(t => {
      const entryTime = t.entry_time || t.entryTime;
      const exitTime  = t.exit_time  || t.exitTime;
      const isLong    = t.type === 'long' || t.type === 'buy';
      const isClosed  = t.status === 'closed' || (!!exitTime);

      if (entryTime) {
        const barTime = this._getBarTimeForTimestamp(entryTime);
        if (barTime && barTime <= this.lastTime) {
          markers.push({
            time:     barTime,
            position: isLong ? 'belowBar' : 'aboveBar',
            shape:    isLong ? 'arrowUp' : 'arrowDown',
            color:    '#2962FF',
            text:     'Entry',
            size:     1,
            id:       `entry_${t.trade_id}`
          });
        }
      }

      if (isClosed && exitTime) {
        if (t.result !== null && t.result !== undefined) {
          if (t.result > 0) win++; else loss++;
          pnl += t.result;
        }

        const barTime = this._getBarTimeForTimestamp(exitTime);
        if (barTime && barTime <= this.lastTime) {
          const isWin = t.result > 0;
          let text = 'Exit';
          if (t.result !== null && t.result !== undefined) {
             const sign = t.result > 0 ? '+' : '';
             text = `${sign}${t.result.toFixed(2)}R`;
          }

          markers.push({
            time:     barTime,
            position: isLong ? 'aboveBar' : 'belowBar',
            shape:    isLong ? 'arrowDown' : 'arrowUp',
            color:    isWin ? '#00E676' : '#FF1744',
            text:     text,
            size:     2, 
            id:       `exit_${t.trade_id}` 
          });
        }
      }
    });
    
    console.groupEnd();

    markers.sort((a, b) => {
        if (a.time !== b.time) return a.time - b.time;
        if (a.text === 'Entry' && b.text !== 'Entry') return -1;
        return 0; 
    });

    this.series.setMarkers(markers);

    if (this.winCounter) this.winCounter.textContent = win;
    if (this.lossCounter) this.lossCounter.textContent = loss;
    if (this.pnlCounter) this.pnlCounter.textContent = `Total PnL: ${pnl.toFixed(2)}R`;
  }

  // --------------------------------------------------------------------------
  // Socket Handling
  // --------------------------------------------------------------------------

  _setupSocket() {
    this.socket.on('connect', () => console.log('[ChartViewer] socket connected'));
    
    this.socket.on('bar', bar => {
      if (bar.time >= this.lastTime) {
        this.series.update(bar);
        
        const lastIdx = this.historicalBars.length - 1;
        if (lastIdx >= 0 && this.historicalBars[lastIdx].time === bar.time) {
            this.historicalBars[lastIdx] = bar; 
        } else {
            this.historicalBars.push(bar); 
        }

        this.lastTime  = bar.time;
        this.lastPrice = bar.close;
        this._shadeBar(bar);
        
        this._updateMarkers();
      }
    });

    this.socket.on('trade_open', trade => {
      this.activeTrade = trade;
      this._drawTradeLines(trade);
      
      const existingIdx = this.allTrades.findIndex(t => t.trade_id === trade.trade_id);
      if (existingIdx !== -1) {
          this.allTrades[existingIdx] = trade;
      } else {
          this.allTrades.push(trade);
      }
      
      this._updateMarkers(); 
    });

    this.socket.on('trade_close', (trade) => {
      this.activeTrade = null;
      
      const idx = this.allTrades.findIndex(t => t.trade_id === trade.trade_id);
      if (idx !== -1) {
          this.allTrades[idx] = { ...this.allTrades[idx], ...trade, status: 'closed' };
      } else {
          this.allTrades.push(trade);
      }

      if (!this.keepClosedTradeLines) {
        [ this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine ]
          .forEach(h => h && this.series.removePriceLine(h));
      }
      
      this._updateMarkers();
    });

    this.socket.on('trade_update', (update) => {
      if (this.activeTrade && this.activeTrade.trade_id === update.trade_id) {
        this.activeTrade.stop_loss = update.stop_loss;
        this._drawTradeLines(this.activeTrade);
      }
    });

    this.socket.on('line_removed', ({ id }) => {
      if (this.keepStrategyLines) return;
      const found = this.pinnedLines.find(o => o.id === id);
      if (!found) return;
      this.series.removePriceLine(found.line);
      this.pinnedLines = this.pinnedLines.filter(o => o.id !== id);
    });

    this.socket.on('stream_end', () => { window.__done = true; });
    this.socket.on('jump_result', (payload) => { /* ... */ });
  }

  // --------------------------------------------------------------------------
  // Chart Interaction & Drawing
  // --------------------------------------------------------------------------

  _shadeBar(bar) {
    // Convert UTC timestamp to NY time components using the formatter created in constructor
    const parts = this.nyTimeFormatter.formatToParts(new Date(bar.time * 1000));
    let h, m;
    
    for (const part of parts) {
      if (part.type === 'hour') h = parseInt(part.value, 10);
      if (part.type === 'minute') m = parseInt(part.value, 10);
    }

    // Check if the NY time falls within the session limits (08:30 - 16:00)
    this.sessions.forEach(s => {
      // Handle 24h wrap-around if necessary, though standard session is usually within one day
      const inSession =
        (h > s.from.h || (h === s.from.h && m >= s.from.m)) &&
        (h < s.to.h   || (h === s.to.h   && m <= s.to.m));
      
      if (inSession) {
        s.series.update({ time: bar.time, value: 1 });
      }
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
    
    let idx = this.historicalBars.findIndex(b => b.time === clickedTime);
    if (idx === -1) {
      idx = this.historicalBars
        .map((b, i) => ({ b, i }))
        .filter(x => x.b.time < clickedTime)
        .sort((a, b) => b.b.time - a.b.time)[0]?.i;
      if (idx === undefined) return;
    }

    const slice = this.historicalBars.slice(0, idx + 1);
    this.displayChart(slice);
    
    this.historicalBars = slice; 
    
    const bar = slice[slice.length - 1];
    this.lastTime  = bar.time;
    this.lastPrice = bar.close;
    
    this._updateMarkers();
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
    // FIX: Ensure chart fits content when data is loaded
    this.chart.timeScale().fitContent();
    this.onDisplay();
  }

  _createLineOnChart(ld) {
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

  _drawTradeLines(trade) {
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && this.series.removePriceLine(h));
    this.tradeEntryLine = this.series.createPriceLine({ price: trade.entry,      color: 'yellow', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'Entry' });
    this.tradeSLLine    = this.series.createPriceLine({ price: trade.stop_loss || trade.stopLoss, color: 'red',   lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'SL'    });
    this.tradeTPLine    = this.series.createPriceLine({ price: trade.take_profit || trade.takeProfit, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'TP'    });
  }

  _drawPreviews({ entry, stop_loss, take_profit }) {
    this.clearPreviews();
    this.previewEntryLine = this.series.createPriceLine({
      price: entry, color: 'rgba(0, 128, 255, 0.8)', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'Entry (preview)'
    });
    this.previewSLLine = this.series.createPriceLine({
      price: stop_loss, color: 'rgba(255, 64, 64, 0.8)', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'SL (preview)'
    });
    this.previewTPLine = this.series.createPriceLine({
      price: take_profit, color: 'rgba(64, 255, 64, 0.8)', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: 'TP (preview)'
    });
  }

  clearPreviews() {
    [ this.previewEntryLine, this.previewSLLine, this.previewTPLine ]
      .forEach(l => l && this.series.removePriceLine(l));
    this.previewEntryLine = this.previewSLLine = this.previewTPLine = null;
  }

  // --------------------------------------------------------------------------
  // Controls
  // --------------------------------------------------------------------------

  startReplay(tf = this.currentTF, fromTime = this.lastTime) {
    if (fromTime < this.lastTime) {
       const idx = this.historicalBars.findIndex(b => b.time >= fromTime);
       if (idx !== -1) {
           this.historicalBars = this.historicalBars.slice(0, idx);
           this.displayChart(this.historicalBars);
       } else if (this.historicalBars.length > 0 && fromTime < this.historicalBars[0].time) {
           this.historicalBars = [];
           this.displayChart([]);
       }
    }

    this.lastTime = fromTime - 1;
    this.socket.emit('start_stream', { timeframe: tf, pair: this.pair, fromTime: fromTime });
    this.isPlaying = true;
  }

  pauseReplay() {
    this.socket.emit('pause_stream');
    this.isPlaying = false;
  }

  toggleReplay() {
    if (this.isPlaying) this.pauseReplay();
    else                this.startReplay();
  }

  async changeTimeframe(tf) {
    this.currentTF = tf;
    this.pauseReplay();
    this.series.setData([]);
    this.londonSeries.setData([]);
    this.nySeries.setData([]);

    const bars = await this.dataService.fetchBars(this.pair, tf, this.startTime);
    this.historicalBars = bars;
    this.displayChart(bars);
    bars.forEach(bar => this._shadeBar(bar));
    await this._initTrades();
  }

  jumpToDay(direction = 1) {
    if (!this.lastTime) return;
    const ONE_DAY = 86400;
    const targetTime = this.lastTime + (direction * ONE_DAY);
    this.startReplay(this.currentTF, targetTime);
  }
}