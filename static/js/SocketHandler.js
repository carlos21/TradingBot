export class SocketHandler {
  constructor(socket, chart) {
    this.socket = socket;
    this.chart = chart;
  }

  init() {
    const c = this.chart;

    this.socket.on('connect', () => console.log('[ChartViewer] socket connected'));

    this.socket.on('bar', bar => {
      if (c._seriesBusy) return;
      if (bar.time >= c.lastTime) {
        c.series.update(bar);

        const lastIdx = c.historicalBars.length - 1;
        if (lastIdx >= 0 && c.historicalBars[lastIdx].time === bar.time) {
          c.historicalBars[lastIdx] = bar;
        } else {
          c.historicalBars.push(bar);
        }

        c.lastTime = bar.time;
        c.lastPrice = bar.close;
        c.shadeBar(bar);
        c.recalculateTSI();  // passes validTimes built from historicalBars internally
      }
    });

    this.socket.on('indicator_update', data => {
      if (data.tf && data.tf !== c.currentTF) return;

      if (data.time >= c.lastTime) {
        if (c.tsiSeries) c.tsiSeries.update({ time: data.time, value: data.tsi });
        if (c.sigSeries) c.sigSeries.update({ time: data.time, value: data.signal });

        if (data.cross_type) {
          c.markers.appendTSIMarker(data.cross_type, data.time);
          c.markers.update(c.allTrades, c.lastTime, new Set(c.historicalBars.map(b => b.time)));
        }
      }
    });

    this.socket.on('trade_open', trade => {
      c.activeTrade = trade;
      const idx = c.allTrades.findIndex(t => t.trade_id === trade.trade_id);
      if (idx !== -1) c.allTrades[idx] = trade;
      else c.allTrades.push(trade);
      c.drawTradeLines(trade);
      if (!c._seriesBusy) c.markers.update(c.allTrades, c.lastTime, new Set(c.historicalBars.map(b => b.time)));
    });

    this.socket.on('trade_close', trade => {
      c.activeTrade = null;
      const idx = c.allTrades.findIndex(t => t.trade_id === trade.trade_id);
      if (idx !== -1) c.allTrades[idx] = { ...c.allTrades[idx], ...trade, status: 'closed' };
      else c.allTrades.push(trade);

      if (!c.keepClosedTradeLines) {
        [c.tradeEntryLine, c.tradeSLLine, c.tradeTPLine].forEach(h => h && c.series.removePriceLine(h));
      }
      if (!c._seriesBusy) c.markers.update(c.allTrades, c.lastTime, new Set(c.historicalBars.map(b => b.time)));
    });

    this.socket.on('trade_update', update => {
      if (c.activeTrade && c.activeTrade.trade_id === update.trade_id) {
        c.activeTrade.stop_loss = update.stop_loss;
        c.drawTradeLines(c.activeTrade);
      }
    });

    this.socket.on('trade_entry_update', update => {
      const idx = c.allTrades.findIndex(t => t.trade_id === update.trade_id);
      if (idx !== -1) {
        c.allTrades[idx].entry = update.entry_price;
        c.allTrades[idx].stop_loss = update.stop_loss;
        c.allTrades[idx].take_profit = update.take_profit;
        c.allTrades[idx].risk = update.risk;
      }
      if (c.activeTrade && c.activeTrade.trade_id === update.trade_id) {
        c.activeTrade.entry = update.entry_price;
        c.activeTrade.stop_loss = update.stop_loss;
        c.activeTrade.take_profit = update.take_profit;
        c.activeTrade.risk = update.risk;
        c.drawTradeLines(c.activeTrade);
      }
      if (!c._seriesBusy) c.markers.update(c.allTrades, c.lastTime, new Set(c.historicalBars.map(b => b.time)));
    });

    this.socket.on('line_removed', ({ id }) => {
      if (c.keepStrategyLines) return;
      const found = c.pinnedLines.find(o => o.id === id);
      if (!found) return;
      c.series.removePriceLine(found.line);
      c.pinnedLines = c.pinnedLines.filter(o => o.id !== id);
    });

    this.socket.on('stream_end', () => { window.__done = true; });

    this.socket.on('history_ready', async data => {
      console.log('[ChartViewer] history_ready received!', data);
      c.liveMode = true;
      // Debounce: coalesce rapid history_ready events (e.g. duplicate emissions)
      clearTimeout(c._historyReadyTimer);
      c._historyReadyTimer = setTimeout(async () => {
        await c.initBars();
      }, 150);
    });

    this.socket.on('stream_status', data => {
      console.log('[ChartViewer] stream_status received:', data);
      if (!data.playing) c.isPlaying = false;
      if (data.live_mode) c.liveMode = true;
    });
  }
}
