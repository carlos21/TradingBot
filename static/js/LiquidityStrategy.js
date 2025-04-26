export class LiquidityStrategy {
    /**
     * @param {Object} config
     * @param {number} config.minStopLoss    – minimum stop loss in points
     * @param {number} config.maxBounce      – maximum allowed bounce depth in points (default 50)
     * @param {HTMLElement} config.pnlCounter– element to update with total PnL
     * @param {Function} config.onTradeOpen  – callback(trade) when a trade opens
     * @param {Function} config.onTradeClose – callback(trade) when a trade closes
     */
    constructor({ minStopLoss, maxBounce = 50, pnlCounter, onTradeOpen, onTradeClose, onLineRemoved }) {
      this.minStopLoss   = minStopLoss;
      this.maxBounce     = maxBounce;
      this.pnlCounter    = pnlCounter   || null;
      this.onTradeOpen   = onTradeOpen  || (() => {});
      this.onTradeClose  = onTradeClose || (() => {});
      this.onLineRemoved  = onLineRemoved || (() => {});
      this.strategyLines = [];  // holds { line, id, level, direction, hasCrossed, extreme }
      this.openTrades    = [];  // only one at a time
      this.totalPnL      = 0;   // in risk units (1=1%, 4=4%)
    }

    addStrategyLine({ line, id, level, direction }) {
      this.strategyLines.push({
        line,
        id,
        level,
        direction,
        hasCrossed: false,
        extreme: direction === 'long' ? Infinity : -Infinity
      });
    }
  
    removeStrategyLine(id) {
      this.strategyLines = this.strategyLines.filter(s => s.id !== id);
      this.onLineRemoved(id);
    }
  
    onNewBar(bar) {
      // 1) Exits
      this._checkOpenTrades(bar);
  
      // 2) No new entries if a trade is open
      if (this.openTrades.some(t => t.status === 'open')) return;
  
      // 3) Scan levels for entry
      for (const s of [...this.strategyLines]) {
        console.log(
          `[Strategy] processing line id=${s.id}` +
          ` dir=${s.direction}` +
          ` level=${s.level}` +
          ` bar.close=${bar.close}`
        );

        const { level, direction } = s;
  
        if (direction === 'long') {
          // LONG: price dips below, then closes back above
          if (bar.close < level) {
            s.hasCrossed = true;
            s.extreme = Math.min(s.extreme, bar.low);
          }
          if (s.hasCrossed && bar.close >= level) {
            const depth = s.direction === 'long'
              ? s.level - s.extreme
              : s.extreme - s.level;
            console.log(`[Strategy] Computed depth=${depth.toFixed(5)}, maxBounce=${this.maxBounce}`);

            if (depth <= this.maxBounce) {
              const entry      = bar.close;
              let   risk       = entry - s.extreme;
              if (risk < this.minStopLoss) risk = this.minStopLoss;
              const stopLoss   = entry - risk;
              const takeProfit = entry + 4 * risk;
              const trade = {
                type:       'long',
                entry,
                stopLoss,
                takeProfit,
                risk,
                status:     'open',
                entryTime:  bar.time
              };
              this.openTrades.push(trade);
              console.log(
                `Long opened @${entry}: SL=${stopLoss} (${risk.toFixed(2)}pts), ` +
                `TP=${takeProfit} (${(4*risk).toFixed(2)}pts)`
              );
              this.onTradeOpen(trade);
            } else {
              console.log(
                `Skipped LONG @${level}: depth ${depth.toFixed(2)}pts > maxBounce ${this.maxBounce}`
              );
            }
            this.removeStrategyLine(s.id);
            break;
          }
        } else {
          // SHORT: price spikes above, then closes back below
          if (bar.close > level) {
            s.hasCrossed = true;
            s.extreme = Math.max(s.extreme, bar.high);
          }
          if (s.hasCrossed && bar.close <= level) {
            const depth = s.extreme - level;
            if (depth <= this.maxBounce) {
              const entry      = bar.close;
              let   risk       = s.extreme - entry;
              if (risk < this.minStopLoss) risk = this.minStopLoss;
              const stopLoss   = entry + risk;
              const takeProfit = entry - 4 * risk;
              const trade = {
                type:       'short',
                entry,
                stopLoss,
                takeProfit,
                risk,
                status:     'open',
                entryTime:  bar.time
              };
              this.openTrades.push(trade);
              console.log(
                `Short opened @${entry}: SL=${stopLoss} (${risk.toFixed(2)}pts), ` +
                `TP=${takeProfit} (${(4*risk).toFixed(2)}pts)`
              );
              this.onTradeOpen(trade);
            } else {
              console.log(
                `Skipped SHORT @${level}: depth ${depth.toFixed(2)}pts > maxBounce ${this.maxBounce}`
              );
            }
            this.removeStrategyLine(s.id);
            break;
          }
        }
      }
    }
  
    _checkOpenTrades(bar) {
      const remaining = [];
      this.openTrades.forEach(trade => {
        if (trade.status !== 'open') return;
  
        if (trade.type === 'long') {
          if (bar.low <= trade.stopLoss) {
            trade.status    = 'closed';
            trade.result    = -1;
            trade.exitTime  = bar.time;
            trade.exitPrice = bar.low;
            console.log(`Long closed loss @${trade.exitPrice}`);
            this.totalPnL += trade.result;
            this.onTradeClose(trade);
          } else if (bar.high >= trade.takeProfit) {
            trade.status    = 'closed';
            trade.result    = 4;
            trade.exitTime  = bar.time;
            trade.exitPrice = bar.high;
            console.log(`Long closed win @${trade.exitPrice}`);
            this.totalPnL += trade.result;
            this.onTradeClose(trade);
          } else {
            remaining.push(trade);
          }
        } else { // short
          if (bar.high >= trade.stopLoss) {
            trade.status    = 'closed';
            trade.result    = -1;
            trade.exitTime  = bar.time;
            trade.exitPrice = bar.high;
            console.log(`Short closed loss @${trade.exitPrice}`);
            this.totalPnL += trade.result;
            this.onTradeClose(trade);
          } else if (bar.low <= trade.takeProfit) {
            trade.status    = 'closed';
            trade.result    = 4;
            trade.exitTime  = bar.time;
            trade.exitPrice = bar.low;
            console.log(`Short closed win @${trade.exitPrice}`);
            this.totalPnL += trade.result;
            this.onTradeClose(trade);
          } else {
            remaining.push(trade);
          }
        }
      });
      this.openTrades = remaining;
      this._updatePnLCounter();
    }
  
    _updatePnLCounter() {
      if (this.pnlCounter) {
        this.pnlCounter.textContent = `Total PnL: ${this.totalPnL}%`;
      }
    }
  }