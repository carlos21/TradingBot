export class ChartViewer {
  /**
   * @param {HTMLElement} chartElement
   * @param {DataService} dataService
   */
  constructor(chartElement, dataService) {
    this.chartElement = chartElement;
    this.dataService  = dataService;
    this.lastTime     = -Infinity;
    this.lastPrice    = null;
    this.pair         = 'EURUSD';
    this.currentTF    = '5m';
    this.isPlaying    = false;

    const formats = {
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
    if (this.pnlCounter) this.pnlCounter.textContent = '0%';

    // SL/TP/Entry line handles
    this.tradeEntryLine = null;
    this.tradeSLLine    = null;
    this.tradeTPLine    = null;

    // block default context menu
    this.chartElement.addEventListener('contextmenu', e => e.preventDefault());

    // chart setup
    this.chartOptions = {
      layout: { background: { type: 'solid', color: 'white' }, textColor: 'black' },
      grid:   { vertLines: { color: '#e1e1e1' }, horzLines: { color: '#e1e1e1' } },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      timeScale: { visible: true, timeVisible: true },
      width:  chartElement.clientWidth,
      height: chartElement.clientHeight
    };
    this.chart  = LightweightCharts.createChart(chartElement, this.chartOptions);
    this.series = this.chart.addCandlestickSeries();
    this.series.setData([]);

    const fmt = formats[this.pair] || { precision: 2, minMove: 1 };
    this.series.applyOptions({ priceFormat: { type: 'price', precision: fmt.precision, minMove: fmt.minMove } });

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
      { series: this.londonSeries, from: { h:4,  m:0 },  to: { h:12, m:30 } },
      { series: this.nySeries,     from: { h:9,  m:30 }, to: { h:16, m:0  } }
    ];

    // user-drawn levels
    this.pinnedLines = [];
    this.chartElement.addEventListener('mousedown', this._onMouseDown.bind(this));

    // initialize data and socket
    this._initBars()
      .then(() => this._initLines())
      .then(() => this._setupSocket())
      .catch(console.error);
  }

  async _initBars() {
    const bars = await this.dataService.fetchBars(this.pair, this.currentTF);
    this.displayChart(bars);
    bars.forEach(bar => this._shadeBar(bar));
  }

  async _initLines() {
    const lines = await this.dataService.fetchLines();
    lines.forEach(ld => {
      const line = this.series.createPriceLine({
        price: ld.price,
        color: 'blue',
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: 'Line'
      });
      this.pinnedLines.push({ line, id: ld.id });
    });
  }

  _setupSocket() {
    this.socket = io();
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

    this.socket.on('trade_open', trade => this._drawTradeLines(trade));
    this.socket.on('trade_close', trade => this._drawResultMarker(trade));
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

  async _addLine(price) {
    const line = this.series.createPriceLine({
      price, color: 'blue', lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true,
      title: `Line ${this.pinnedLines.length + 1}`
    });
    try {
      const saved = await this.dataService.addLine(this.pair, price);
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
  }

  _drawTradeLines(trade) {
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && this.series.removePriceLine(h));
    this.tradeEntryLine = this.series.createPriceLine({ price: trade.entry,      color: 'yellow', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'Entry' });
    this.tradeSLLine    = this.series.createPriceLine({ price: trade.stop_loss || trade.stopLoss, color: 'red',   lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'SL'    });
    this.tradeTPLine    = this.series.createPriceLine({ price: trade.take_profit || trade.takeProfit, color: 'green', lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Solid, axisLabelVisible: true, title: 'TP'    });
  }

  _drawResultMarker(trade) {
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => h && this.series.removePriceLine(h));
    const exitTime = trade.exit_time || trade.exitTime || trade.entryTime;
    const marker = { time: exitTime, position: trade.type === 'long' ? 'belowBar' : 'aboveBar', shape: 'text', text: trade.result > 0 ? `+${trade.result}` : `${trade.result}` };
    this.tradeMarkers.push(marker);
    this.series.setMarkers(this.tradeMarkers);
    if (trade.result > 0) {
      this.winCount++;
      if (this.winCounter) this.winCounter.textContent = this.winCount;
    } else {
      this.lossCount++;
      if (this.lossCounter) this.lossCounter.textContent = this.lossCount;
    }
  }

  // ───── Replay & Timeframe Controls ─────
  startReplay(tf = this.currentTF) {
    console.log('[ChartViewer] ▶️ startReplay — tf:', tf, 'pair:', this.pair);
    if (this.isPlaying) return;
    this.pauseReplay();
    this.currentTF = tf;

    this.socket.emit('start_stream', { timeframe: tf, pair: this.pair, fromTime: this.lastTime });
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

    const bars = await this.dataService.fetchBars(this.pair, tf, this.lastTime);
    this.displayChart(bars);
    bars.forEach(bar => this._shadeBar(bar));
  }
}
