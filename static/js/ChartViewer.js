import { LiquidityStrategy }   from './LiquidityStrategy.js';

export class ChartViewer {
  /**
   * @param {HTMLElement} chartElement
   * @param {DataService} dataService
   */
  constructor(chartElement, dataService) {
    this.chartElement = chartElement;
    this.dataService  = dataService;
    this.lastTime = -Infinity;
    this.pair = "EURUSD";

    const minStopLossConfig = {
      NQ:     10,        // 10 points
      EURUSD: 0.0004,    // 4 pips
    };
    const maxBounceConfig = {
      NQ:     50,        // 50 points
      EURUSD: 0.0020,     // 20 pips
    };
    const formats = {
      NQ:     { precision: 2,    minMove: 0.01    },
      EURUSD: { precision: 5,    minMove: 0.00001 }
    };

    // minimum stop loss
    this.minStopLoss = minStopLossConfig[this.pair];
    this.maxBounce = maxBounceConfig[this.pair];

    // PnL display
    this.tradeMarkers = [];
    this.pnlCounter = document.getElementById('pnlCounter');
    if (this.pnlCounter) this.pnlCounter.textContent = "Total PnL: 0%";

    // SL/TP/Entry line handles
    this.tradeSLLine    = null;
    this.tradeTPLine    = null;
    this.tradeEntryLine = null;


    this.winCounter   = document.getElementById('winCount');
    this.lossCounter  = document.getElementById('lossCount');
    this.pnlCounter   = document.getElementById('pnlCounter');
    this.winCount     = 0;
    this.lossCount    = 0;
    if (this.pnlCounter) this.pnlCounter.textContent = '0%';

    // instantiate strategy (maxBounce defaults to 50 inside)
    this.strategy = new LiquidityStrategy({
      minStopLoss: this.minStopLoss,
      maxBounce:   this.maxBounce,
      pnlCounter:  this.pnlCounter,
      onTradeOpen:  this._drawTradeLines.bind(this),
      onTradeClose: this._drawResultMarker.bind(this),
      onLineRemoved: this._handleSkippedLine.bind(this)
    });

    // block default context menu
    this.chartElement.addEventListener('contextmenu', e => e.preventDefault());

    // chart options
    this.chartOptions = {
      layout: {
        background: { type: 'solid', color: 'white' },
        textColor: 'black'
      },
      grid: {
        vertLines: { color: '#e1e1e1' },
        horzLines: { color: '#e1e1e1' }
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      timeScale: { 
        visible: true,
        timeVisible: true
      },
      width:  chartElement.clientWidth,
      height: chartElement.clientHeight
    };
    this.chart  = LightweightCharts.createChart(chartElement, this.chartOptions);
    this.series = this.chart.addCandlestickSeries();
    this.series.setData([]);

    const fmt = formats[this.pair] || { precision: 2, minMove: 1 };
    this.series.applyOptions({
      priceFormat: {
        type: 'price',
        precision: fmt.precision,
        minMove: fmt.minMove,
      }
    });

    const ro = new ResizeObserver(() => {
      this.chart.resize(
        this.chartElement.clientWidth,
        this.chartElement.clientHeight
      );
    });
    ro.observe(this.chartElement);

    this.londonSeries = this.chart.addHistogramSeries({
      priceScaleId:  '',
      scaleMargins:  { top: 0, bottom: 0 },
      lineWidth:     0,
      overlay:       true,
      color:         'rgba(0, 255, 0, 0.1)', // green
    });
    this.nySeries = this.chart.addHistogramSeries({
      priceScaleId:  '',
      scaleMargins:  { top: 0, bottom: 0 },
      lineWidth:     0,
      overlay:       true,
      color:         'rgba(255, 0, 0, 0.1)', // red
    });

    // session time config in UTC
    this.sessions = [
      { series: this.londonSeries, from: { h: 4,  m: 0 },  to: { h: 12, m: 30 } },
      { series: this.nySeries,     from: { h: 9,  m: 30 }, to: { h: 16, m: 0  } },
    ];

    // user‑drawn levels
    this.pinnedLines = [];

    // SHIFT+click to add/remove levels
    this.chartElement.addEventListener('mousedown', this._onMouseDown.bind(this));

    // load persisted levels and bars
    this._initBars()
      .then(() => this._initLines())
      .catch(console.error);

    // replay via WebSocket
    this.socket    = null;
    this.isPlaying = false;
  }

  async _initBars() {
    try {
      const bars = await this.dataService.fetchBars(this.pair, '5m');
      this.displayChart(bars);
      bars.forEach(bar => this._shadeNewBar(bar));
    } catch (err) {
      console.error('Error fetching bars:', err);
    }
  }

  async _initLines() {
    try {
      const lines = await this.dataService.fetchLines();
      lines.forEach(ld => {
        const line = this.series.createPriceLine({
          price:            ld.price,
          color:            'blue',
          lineWidth:        1,
          lineStyle:        LightweightCharts.LineStyle.Dotted,
          axisLabelVisible: true,
          title:            'Line'
        });
        this.pinnedLines.push({ line, id: ld.id });
  
        // ← NEW: derive direction from current lastPrice
        const direction = (this.lastPrice != null && this.lastPrice > ld.price)
          ? 'long'
          : 'short';
        
        console.log(
          `[ChartViewer] initLine id=${ld.id}` +
          ` level=${ld.price}` +
          ` lastPrice=${this.lastPrice}` +
          ` → direction=${direction}`
        );

        // ← PASS direction into your strategy
        this.strategy.addStrategyLine({
          line,
          id:    ld.id,
          level: ld.price,
          direction
        });
      });
    } catch (err) {
      console.error('Error fetching lines:', err);
    }
  }

  async _handleSkippedLine(id) {
    // find and remove the visual line
    const obj = this.pinnedLines.find(o => o.id === id);
    if (!obj) return;
    this.series.removePriceLine(obj.line);
    this.pinnedLines = this.pinnedLines.filter(o => o.id !== id);
  
    // call the backend to delete it
    try {
      await this.dataService.deleteLine(id);
    } catch (err) {
      console.error('Error deleting skipped line:', err);
    }
  }

  _shadeNewBar(bar) {
    const d = new Date(bar.time * 1000);
    const h = d.getUTCHours(), m = d.getUTCMinutes();
  
    this.sessions.forEach(s => {
      const inSession =
        (h > s.from.h  || (h === s.from.h  && m >= s.from.m)) &&
        (h < s.to.h    || (h === s.to.h    && m <= s.to.m));
      if (inSession) {
        s.series.update({ time: bar.time, value: 1 });
      }
    });
  }

  _onMouseDown(e) {
    if (!e.shiftKey) return;
    if (e.button !== 0 && e.button !== 2) return;
    const rect  = this.chartElement.getBoundingClientRect();
    const price = this.series.coordinateToPrice(e.clientY - rect.top);
    if (e.button === 0) this._addLine(price);
    else               this._removeNearestLine(price);
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
  
      // ← NEW: direction for user-drawn line
      const direction = this.lastPrice > price ? 'long' : 'short';
      
      console.log(
        `[ChartViewer] addLine id=${saved.id}` +
        ` level=${price}` +
        ` lastPrice=${this.lastPrice}` +
        ` → direction=${direction}`
      );

      // ← PASS direction as well
      this.strategy.addStrategyLine({
        line,
        id:    saved.id,
        level: price,
        direction
      });
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
      this.strategy.removeStrategyLine(nearest.id);
      try {
        await this.dataService.deleteLine(nearest.id);
      } catch (err) {
        console.error('Error deleting line:', err);
      }
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
    // clear old
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => {
      if (h) this.series.removePriceLine(h);
    });

    this.tradeEntryLine = this.series.createPriceLine({
      price:            trade.entry,
      color:            'yellow',
      lineWidth:        2,
      lineStyle:        LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title:            'Entry'
    });
    this.tradeSLLine = this.series.createPriceLine({
      price:            trade.stopLoss,
      color:            'red',
      lineWidth:        2,
      lineStyle:        LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title:            'SL'
    });
    this.tradeTPLine = this.series.createPriceLine({
      price:            trade.takeProfit,
      color:            'green',
      lineWidth:        2,
      lineStyle:        LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
      title:            'TP'
    });
  }

  _removeTradeLines() {
    [this.tradeEntryLine, this.tradeSLLine, this.tradeTPLine].forEach(h => {
      if (h) this.series.removePriceLine(h);
    });
    this.tradeEntryLine = this.tradeSLLine = this.tradeTPLine = null;
  }

  _drawResultMarker(trade) {
    // first remove SL/TP/Entry lines
    this._removeTradeLines();
  
    // then draw result text at exitTime
    const marker = {
      time:     trade.entryTime,
      position: trade.type === 'long' ? 'belowBar' : 'aboveBar',
      color:    'black',
      shape:    'text',
      text:     trade.result > 0 ? `+${trade.result}` : `${trade.result}`,
    };
  
    // preserve old markers if you want history:
    this.tradeMarkers = this.tradeMarkers || [];
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

  startReplay(tf = '5m') {
    if (!this.socket) {
      this.socket = io();
      this.socket.on('bar', bar => {
        // only update if truly newer than lastTime
        if (bar.time > this.lastTime) {
          this.series.update(bar);
          this.lastTime = bar.time;
          this.lastPrice = bar.close;
          this.strategy.onNewBar(bar);
          this._shadeNewBar(bar);
        }
      });
    }
    this.socket.emit('start_stream', {
      timeframe: tf,
      pair: this.pair
    });
    this.isPlaying = true;
  }

  pauseReplay() {
    if (!this.isPlaying) return;
    if (this.socket) this.socket.emit('pause_stream');
    this.isPlaying = false;
  }

  toggleReplay() {
    this.isPlaying ? this.pauseReplay() : this.startReplay();
  }

  async changeTimeframe(tf) {
    // 1) Pause the stream
    this.pauseReplay();
  
    // 2) Clear all series and markers
    this.series.setData([]);
    this.londonSeries.setData([]);
    this.nySeries.setData([]);
    this.tradeMarkers = [];
    this.series.setMarkers([]);
    // this.lastTime = -Infinity;
  
    // 3) Fetch & draw the new TF bars
    const bars = await this.dataService.fetchBars(this.pair, tf, this.lastTime);
    this.displayChart(bars);
  
    // 4) Re‑shade sessions
    bars.forEach(bar => this._shadeNewBar(bar));
  
    // You remain paused until the user hits Play again
  }

}