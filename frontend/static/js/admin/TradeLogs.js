/**
 * Trade Logs Component
 * Displays trade details and logs in a modal
 */
export class TradeLogs {
  constructor(apiClient) {
    this.api = apiClient;
    this.modal = document.getElementById('trade-modal');
    this.titleEl = document.getElementById('modal-trade-title');
    this.detailsEl = document.getElementById('modal-trade-details');
    this.logsEl = document.getElementById('modal-trade-logs');
    this.closeBtn = document.getElementById('close-trade-modal');

    this.closeBtn?.addEventListener('click', () => this.hide());
    this.modal?.addEventListener('click', (e) => {
      if (e.target === this.modal) this.hide();
    });
  }

  async show(tradeId) {
    try {
      const trade = await this.api.getTradeDetail(tradeId);
      this.render(trade);
      this.modal.classList.remove('hidden');
    } catch (error) {
      console.error('Failed to load trade details:', error);
      alert('Failed to load trade details');
    }
  }

  hide() {
    this.modal?.classList.add('hidden');
  }

  render(trade) {
    // Title - show full trade ID and result badge with colored background
    const resultLabel = trade.result_type || (trade.status === 'open' ? 'Open' : 'Close');
    const resultBadgeClass = this.getResultBadgeBackgroundClass(resultLabel);
    this.titleEl.innerHTML = `
      Trade ${trade.trade_id}
      <span class="px-2 py-1 rounded text-xs font-medium ${resultBadgeClass}">${resultLabel}</span>
    `;

    // Details Grid
    const pnlClass = trade.result > 0 ? 'text-emerald-400' : trade.result < 0 ? 'text-rose-500' : '';
    this.detailsEl.innerHTML = `
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Type</div>
        <div class="font-semibold ${trade.type === 'long' ? 'text-emerald-400' : 'text-rose-500'}">
          ${trade.type.toUpperCase()}
        </div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Entry Price</div>
        <div class="font-semibold">${trade.entry.toFixed(2)}</div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Stop Loss</div>
        <div class="font-semibold text-rose-500">${trade.stop_loss.toFixed(2)}</div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Take Profit</div>
        <div class="font-semibold text-emerald-400">${trade.take_profit.toFixed(2)}</div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Risk $</div>
        <div class="font-semibold">${trade.risk_dollars ? '$' + trade.risk_dollars.toFixed(2) : '-'}</div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">P&L</div>
        <div class="font-semibold ${pnlClass}">
          ${trade.pnl_usd !== null && trade.pnl_usd !== undefined
            ? `$${trade.pnl_usd.toFixed(2)}`
            : (trade.result !== null ? `${trade.result.toFixed(2)}R` : '-')}
        </div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Entry Time</div>
        <div class="font-semibold text-sm">${this.formatDateTime(trade.entry_time)}</div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Exit Time</div>
        <div class="font-semibold text-sm">${trade.exit_time ? this.formatDateTime(trade.exit_time) : '-'}</div>
      </div>
      <div class="bg-surface-700 rounded-lg p-3">
        <div class="text-xs text-slate-400 mb-1">Contracts</div>
        <div class="font-semibold">${trade.contracts || '-'}</div>
      </div>
    `;

    // Logs
    if (trade.logs && trade.logs.length > 0) {
      this.logsEl.innerHTML = trade.logs.map(log => {
        const borderClass = this.getEventBorderClass(log.event);
        const time = log.ts ? new Date(log.ts).toLocaleTimeString('en-US', { hour12: false }) : '';
        return `
          <div class="p-3 bg-surface-700 rounded-lg border-l-4 ${borderClass}">
            <div class="flex justify-between items-start mb-1">
              <span class="font-semibold text-sm text-slate-100">${log.event}</span>
              <span class="text-xs text-slate-500">${time}</span>
            </div>
            <div class="text-sm text-slate-300">${log.msg}</div>
          </div>
        `;
      }).join('');
    } else {
      this.logsEl.innerHTML = '<div class="text-slate-500 text-center py-4">No logs available</div>';
    }
  }

  getEventBorderClass(event) {
    if (!event) return 'border-surface-600';
    const eventUpper = event.toUpperCase();
    if (eventUpper.includes('ERROR') || eventUpper.includes('FAIL')) return 'border-rose-500';
    if (eventUpper.includes('FILL') || eventUpper.includes('COMPLETE') || eventUpper.includes('TP')) return 'border-emerald-400';
    if (eventUpper.includes('WARNING') || eventUpper.includes('SL')) return 'border-amber-400';
    return 'border-surface-600';
  }

  getResultBadgeClass(resultType) {
    switch (resultType) {
      case 'TP': return 'text-emerald-400';
      case 'SL': return 'text-rose-500';
      case 'BE': return 'text-amber-400';
      case 'SP': return 'text-accent-400';
      case 'CLOSE': return 'text-slate-400';
      default: return 'text-slate-400';
    }
  }

  getResultBadgeBackgroundClass(resultType) {
    switch (resultType) {
      case 'TP': return 'bg-emerald-500/20 text-emerald-400';
      case 'SL': return 'bg-rose-500/20 text-rose-500';
      case 'BE': return 'bg-amber-400/20 text-amber-400';
      case 'SP': return 'bg-accent-500/20 text-accent-400';
      case 'CLOSE': return 'bg-slate-500/20 text-slate-400';
      default: return 'bg-slate-500/20 text-slate-400';
    }
  }

  formatDateTime(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  }
}
