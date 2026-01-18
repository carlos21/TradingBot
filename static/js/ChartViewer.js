export class ChartViewer {
  constructor(chartElement, dataService, socket, opts = {}) {
    this.chartElement = chartElement;
    this.dataService  = dataService;
    this.socket = socket;
    this.onDisplay = opts.onDisplay || (() => {});
    
    // --- State ---
    this.lastTime     = -Infinity;
    this.lastPrice    = null;
    this.pair         = null;
    this.currentTF    = opts.timeframe || '5m';
    this.isPlaying    = false;
    this.activeTrade  = null;
    
    this.historicalBars = []; 
    this.allTrades      = []; 
    this.pinnedLines    = []; 

    // --- Config ---
    this.keepClosedTradeLines = opts.keepClosedTradeLines || false;
    this.startTime = opts.startTime || null;
    this.keepStrategyLines = opts.keepStrategyLines || false;

    this.formats = {
      NQ:     { precision: 2,    minMove: 0.01    },
      EURUSD: { precision: 5,    minMove: 0.00001 }
    };

    // --- UI DOM ---
    this.winCounter   = document.getElementById('winCount');
    this.lossCounter  = document.getElementById('lossCount');
    this.pnlCounter   = document.getElementById('pnlCounter');
    if (this.pnlCounter) this.pnlCounter.textContent = 'Total PnL: 0.00R';

    // ========================================================================
    // 1. SHARED CONFIGURATION
    // ========================================================================
    
    const PRICE_SCALE_WIDTH = 75; // Fixed width for alignment

    const commonOptions = {
      layout: { background: { type: 'solid', color: 'white' }, textColor: 'black' },
      grid:   { vertLines: { color: '#f0f0f0' }, horzLines: { color: '#f0f0f0' } },
      crosshair: { 
          mode: LightweightCharts.CrosshairMode.Normal,
          // We handle crosshair sync manually, but this keeps the native feel
          vertLine: { visible: true, labelVisible: true },
          horzLine: { visible: true, labelVisible: true }
      },
      rightPriceScale: {
        visible: true,
        borderColor: '#d1d5db',
        minimumWidth: PRICE_SCALE_WIDTH, 
      },
      timeScale: {
        visible: true,
        timeVisible: true,
        shiftVisibleRangeOnNewBar: true,
        tickMarkFormatter: (time) => {
             const date = new Date(time * 1000);
             return date.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour12: false, hour: '2-digit', minute: '2-digit'});
        }
      }
    };

    // ========================================================================
    // 2. MAIN CHART (PRICE)
    // ========================================================================
    this.chart = LightweightCharts.createChart(chartElement, {
      ...commonOptions,
      width: chartElement.clientWidth || 800,
      height: chartElement.clientHeight || 400,
      timeScale: { 
          ...commonOptions.timeScale, 
          visible: false // Hide dates on top chart
      } 
    });

    this.series = this.chart.addCandlestickSeries({
        upColor: 'white', borderUpColor: 'black', wickUpColor: 'black',
        downColor: 'black', borderDownColor: 'black', wickDownColor: 'black',
        priceFormat: { type: 'price', precision: 2, minMove: 1 },
    });

    // ========================================================================
    // 3. INDICATOR CHART (TSI)
    // ========================================================================
    this.indicatorElement = document.getElementById('indicatorContainer');
    if (!this.indicatorElement) console.error("CRITICAL: #indicatorContainer not found.");
    
    this.indicatorChart = LightweightCharts.createChart(this.indicatorElement, {
      ...commonOptions,
      width: this.indicatorElement?.clientWidth || 800,
      height: this.indicatorElement?.clientHeight || 200,
      timeScale: { 
          ...commonOptions.timeScale, 
          visible: true // Show dates on bottom chart
      }
    });

    this.tsiSeries = this.indicatorChart.addLineSeries({ color: 'blue', lineWidth: 2, title: 'TSI' });
    this.sigSeries = this.indicatorChart.addLineSeries({ color: 'red',  lineWidth: 2, title: 'Signal' });
    
    const zeroLine = { price: 0, color: '#999', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: false };
    this.tsiSeries.createPriceLine(zeroLine);


    // ========================================================================
    // 4. SYNCHRONIZATION (Zoom & Crosshair)
    // ========================================================================
    
    // --- Zoom/Scroll Sync ---
    let isSyncingRange = false;
    const syncRange = (source, target) => {
        const range = source.timeScale().getVisibleLogicalRange();
        if (range && !isSyncingRange) {
            isSyncingRange = true;
            // Protect against null range errors
            if (range.from !== null && range.to !== null) {
                try { target.timeScale().setVisibleLogicalRange(range); } catch(e) {}
            }
            isSyncingRange = false;
        }
    };

    this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => syncRange(this.chart, this.indicatorChart));
    this.indicatorChart.timeScale().subscribeVisibleLogicalRangeChange(() => syncRange(this.indicatorChart, this.chart));

    // --- Crosshair Sync (The Vertical Line) ---
    // We update the other chart's crosshair position based on the time of the hover.
    
    const syncCrosshair = (sourceChart, targetChart, targetSeries) => {
        sourceChart.subscribeCrosshairMove(param => {
            if (!param.point || !param.time) {
                targetChart.clearCrosshairPosition();
                return;
            }
            // Pass NaN for price to hide the horizontal line on the target chart
            // (or pass a valid price if you wanted to sync price levels, but that doesn't make sense for TSI)
            targetChart.setCrosshairPosition(NaN, param.time, targetSeries);
        });
    };

    // Sync Price -> Indicator
    syncCrosshair(this.chart, this.indicatorChart, this.tsiSeries);
    
    // Sync Indicator -> Price
    syncCrosshair(this.indicatorChart, this.chart, this.series);


    // ========================================================================
    // 5. RESIZE & EVENTS
    // ========================================================================
    
    const resizer = new ResizeObserver(entries => {
        for (let entry of entries) {
            if (entry.target === this.chartElement) {
                this.chart.resize(entry.contentRect.width, entry.contentRect.height);
            } else if (entry.target === this.indicatorElement) {
                this.indicatorChart.resize(entry.contentRect.width, entry.contentRect.height);
            }
        }
    });
    resizer.observe(this.chartElement);
    if(this.indicatorElement) resizer.observe(this.indicatorElement);

    setTimeout(() => {
        if(this.chartElement) this.chart.resize(this.chartElement.clientWidth, this.chartElement.clientHeight);
        if(this.indicatorElement) this.indicatorChart.resize(this.indicatorElement.clientWidth, this.indicatorElement.clientHeight);
    }, 100);

    // Event Listeners
    this.chartElement.addEventListener('contextmenu', e => e.preventDefault());
    this.chartElement.addEventListener('mousedown', this._onMouseDown.bind(this));
    
    this.isRPressed = false;
    window.addEventListener('keydown', e => { if (e.key.toLowerCase() === 'r') this.isRPressed = true; });
    window.addEventListener('keyup', e => { if (e.key.toLowerCase() === 'r') this.isRPressed = false; });
    
    this.chart.subscribeClick(param => {
      if (this.isRPressed && param && param.time) this._onChartClick(param.time);
    });

    // Session shading
    this.londonSeries = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top:0, bottom:0 }, lineWidth:0, overlay:true, color:'rgba(0,255,0,0.1)' });
    this.nySeries     = this.chart.addHistogramSeries({ priceScaleId: '', scaleMargins: { top:0, bottom:0 }, lineWidth:0, overlay:true, color:'rgba(255,0,0,0.1)' });
    this.sessions = [{ series: this.nySeries, from: { h:8, m:30 }, to: { h:16, m:0 } }];
    this.nyTimeFormatter = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: 'numeric', hour12: false });

    // Start Init
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
  // Initialization
  // --------------------------------------------------------------------------

  async _initPair() {
    this.pair = await this.dataService.getPair();
    const f = this.formats[this.pair] || { precision: 2, minMove: 1 };
    this.series.applyOptions({ priceFormat: { type: 'price', precision: f.precision, minMove: f.minMove } });
  }

  async _initBars() {
    console.log(`[ChartViewer] Fetching bars for ${this.currentTF}...`);
    const bars = await this.dataService.fetchBars(this.pair, this.currentTF, this.startTime);
    this.historicalBars = bars;
    
    // 1. Calculate Indicators FIRST
    this._calculateAndDrawTSI(bars);

    // 2. Draw Price Chart
    this.displayChart(bars);
    
    // 3. Shading
    bars.forEach(bar => this._shadeBar(bar));
  }

  async _initLines() {
    const lines = await this.dataService.fetchLines(this.pair);
    lines.forEach(ld => this._createLineOnChart(ld));
  }

  async _initTrades() {
    try {
      this.allTrades = await this.dataService.fetchTrades(this.pair);
      this._updateMarkers();
    } catch (e) { console.error(e); }
  }

  // --------------------------------------------------------------------------
  // TSI Calculation
  // --------------------------------------------------------------------------
  _calculateAndDrawTSI(bars) {
      // Relaxed constraint: Need at least 14 bars for minimal calc (Long length=6 + Short=13 + Signal=4)
      if (!bars || bars.length < 14) {
          console.warn(`[TSI] Not enough bars (${bars ? bars.length : 0}) for calculation.`);
          this.tsiSeries.setData([]);
          this.sigSeries.setData([]);
          return;
      }

      const closes = bars.map(b => b.close);
      const times  = bars.map(b => b.time);

      const TSI_LONG = 6, TSI_SHORT = 13, TSI_SIGNAL = 4;

      const ema = (vals, len) => {
          const k = 2 / (len + 1);
          const res = new Array(vals.length).fill(0);
          res[0] = vals[0];
          for(let i=1; i<vals.length; i++) res[i] = (vals[i] * k) + (res[i-1] * (1 - k));
          return res;
      };

      const pc = [0], abs_pc = [0];
      for(let i=1; i<closes.length; i++) {
          const diff = closes[i] - closes[i-1];
          pc.push(diff);
          abs_pc.push(Math.abs(diff));
      }

      const ema_pc_2  = ema(ema(pc, TSI_LONG), TSI_SHORT);
      const ema_apc_2 = ema(ema(abs_pc, TSI_LONG), TSI_SHORT);

      const tsiData = [], tsiRawValues = [];

      for(let i=0; i<ema_pc_2.length; i++) {
          const val = ema_pc_2[i];
          const absVal = ema_apc_2[i];
          let tsi = 0;
          if (absVal !== 0) tsi = 100 * (val / absVal);
          if (!isFinite(tsi)) tsi = 0;
          tsiRawValues.push(tsi);
          tsiData.push({ time: times[i], value: tsi });
      }

      const signalValues = ema(tsiRawValues, TSI_SIGNAL);
      const signalData = signalValues.map((val, i) => ({ time: times[i], value: val }));

      try {
          this.tsiSeries.setData(tsiData);
          this.sigSeries.setData(signalData);
          this.indicatorChart.timeScale().fitContent(); 
      } catch (err) {
          console.error(err);
      }
  }

  // --------------------------------------------------------------------------
  // Core Display
  // --------------------------------------------------------------------------

  displayChart(bars) {
    this.series.setData(bars);
    if (bars.length) {
      const last = bars[bars.length - 1];
      this.lastTime  = last.time;
      this.lastPrice = last.close;
    }
    this.chart.timeScale().fitContent();
    this.onDisplay();
  }

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

    this.socket.on('indicator_update', (data) => {
        if (data.time >= this.lastTime) {
            this.tsiSeries.update({ time: data.time, value: data.tsi });
            this.sigSeries.update({ time: data.time, value: data.signal });
        }
    });

    this.socket.on('trade_open', trade => {
      this.activeTrade = trade;
      this._drawTradeLines(trade);
      const existingIdx = this.allTrades.findIndex(t => t.trade_id === trade.trade_id);
      if (existingIdx !== -1) this.allTrades[existingIdx] = trade;
      else this.allTrades.push(trade);
      this._updateMarkers(); 
    });

    this.socket.on('trade_close', (trade) => {
      this.activeTrade = null;
      const idx = this.allTrades.findIndex(t => t.trade_id === trade.trade_id);
      if (idx !== -1) this.allTrades[idx] = { ...this.allTrades[idx], ...trade, status: 'closed' };
      else this.allTrades.push(trade);

      if (!this.keepClosedTradeLines) {
        [ this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine ].forEach(h => h && this.series.removePriceLine(h));
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
  }

  _updateMarkers() {
    if (!this.historicalBars.length) return;
    const markers = [];
    let win = 0, loss = 0, pnl = 0;

    this.allTrades.forEach(t => {
      const entryTime = t.entry_time || t.entryTime;
      const exitTime  = t.exit_time  || t.exitTime;
      const isLong    = t.type === 'long' || t.type === 'buy';

      if (entryTime && entryTime <= this.lastTime) {
          markers.push({ time: entryTime, position: isLong ? 'belowBar' : 'aboveBar', shape: isLong ? 'arrowUp' : 'arrowDown', color: '#2962FF', text: 'Entry', size: 1 });
      }

      if (t.status === 'closed' && exitTime && exitTime <= this.lastTime) {
          const res = t.result || 0;
          if (res > 0) win++; else loss++;
          pnl += res;
          markers.push({ 
              time: exitTime, 
              position: isLong ? 'aboveBar' : 'belowBar', 
              shape: isLong ? 'arrowDown' : 'arrowUp', 
              color: res > 0 ? '#00E676' : '#FF1744', 
              text: (res > 0 ? '+' : '') + res.toFixed(2) + 'R',
              size: 2 
          });
      }
    });

    const uniqueMarkers = [];
    const seen = new Set();
    markers.sort((a,b) => a.time - b.time).forEach(m => {
        const k = `${m.time}_${m.text}`;
        if(!seen.has(k)) { seen.add(k); uniqueMarkers.push(m); }
    });

    this.series.setMarkers(uniqueMarkers);
    if (this.winCounter) this.winCounter.textContent = win;
    if (this.lossCounter) this.lossCounter.textContent = loss;
    if (this.pnlCounter) this.pnlCounter.textContent = `Total PnL: ${pnl.toFixed(2)}R`;
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
      idx = this.historicalBars.findIndex(b => b.time > clickedTime);
      if(idx > 0) idx--; 
    }
    if (idx !== -1) {
        const slice = this.historicalBars.slice(0, idx + 1);
        this.displayChart(slice);
        this.historicalBars = slice;
        this._calculateAndDrawTSI(slice); 
        this._updateMarkers();
        this.socket.emit('seek', { fromTime: this.lastTime });
    }
  }

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

  _createLineOnChart(ld) {
    if (this.pinnedLines.find(x => x.id === ld.id)) return;
    const line = this.series.createPriceLine({ price: ld.price, color: 'blue', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true, title: 'Line' });
    this.pinnedLines.push({ line, id: ld.id });
  }

  _drawTradeLines(trade) {
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && this.series.removePriceLine(h));
    this.tradeEntryLine = this.series.createPriceLine({ price: trade.entry,      color: 'yellow', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'Entry' });
    this.tradeSLLine    = this.series.createPriceLine({ price: trade.stop_loss || trade.stopLoss, color: 'red',   lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'SL'    });
    this.tradeTPLine    = this.series.createPriceLine({ price: trade.take_profit || trade.takeProfit, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'TP'    });
  }
  
  clearPreviews() {
    [ this.previewEntryLine, this.previewSLLine, this.previewTPLine ].forEach(l => l && this.series.removePriceLine(l));
    this.previewEntryLine = this.previewSLLine = this.previewTPLine = null;
  }

  _shadeBar(bar) {
    const parts = this.nyTimeFormatter.formatToParts(new Date(bar.time * 1000));
    let h, m;
    for (const part of parts) {
      if (part.type === 'hour') h = parseInt(part.value, 10);
      if (part.type === 'minute') m = parseInt(part.value, 10);
    }
    this.sessions.forEach(s => {
      const inSession = (h > s.from.h || (h === s.from.h && m >= s.from.m)) && (h < s.to.h || (h === s.to.h && m <= s.to.m));
      if (inSession) s.series.update({ time: bar.time, value: 1 });
    });
  }

  // --- Controls ---
  startReplay(tf = this.currentTF, fromTime = this.lastTime) {
    if (fromTime < this.lastTime) {
       const idx = this.historicalBars.findIndex(b => b.time >= fromTime);
       if (idx !== -1) {
           this.historicalBars = this.historicalBars.slice(0, idx);
           this.displayChart(this.historicalBars);
           this._calculateAndDrawTSI(this.historicalBars); 
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
    
    // Explicitly Clear Data to prevent "Gap/Space" artifacts
    this.series.setData([]);
    this.tsiSeries.setData([]);
    this.sigSeries.setData([]);
    this.londonSeries.setData([]);
    this.nySeries.setData([]);

    const bars = await this.dataService.fetchBars(this.pair, tf, this.startTime);
    this.historicalBars = bars;
    
    // Recalculate and Draw
    this._calculateAndDrawTSI(bars);
    this.displayChart(bars);
    
    // Reset Zoom to fit new data
    this.chart.timeScale().fitContent();
    this.indicatorChart.timeScale().fitContent();

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