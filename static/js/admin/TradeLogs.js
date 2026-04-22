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
    const resultLabel = trade.result_type || (trade.status === 'open' ? 'OPEN' : 'CLOSED');
    const resultBadgeClass = this.getResultBadgeBackgroundClass(trade.result_type);
    this.titleEl.innerHTML = `
      Trade ${trade.trade_id} 
      <span class="px-2 py-1 rounded text-xs font-medium ${resultBadgeClass}">${resultLabel}</span>
    `;

    // Details Grid
    const pnlClass = trade.result > 0 ? 'pnl-positive' : trade.result < 0 ? 'pnl-negative' : '';
    this.detailsEl.innerHTML = `
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Type</div>
        <div class="font-semibold ${trade.type === 'long' ? 'text-green-400' : 'text-red-400'}">
          ${trade.type.toUpperCase()}
        </div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Entry Price</div>
        <div class="font-semibold">${trade.entry.toFixed(2)}</div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Stop Loss</div>
        <div class="font-semibold text-red-400">${trade.stop_loss.toFixed(2)}</div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Take Profit</div>
        <div class="font-semibold text-green-400">${trade.take_profit.toFixed(2)}</div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Risk $</div>
        <div class="font-semibold">${trade.risk_dollars ? '$' + trade.risk_dollars.toFixed(2) : '-'}</div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">P&L</div>
        <div class="font-semibold ${pnlClass}">
          ${trade.pnl_usd !== null && trade.pnl_usd !== undefined 
            ? `$${trade.pnl_usd.toFixed(2)}` 
            : (trade.result !== null ? `${trade.result.toFixed(2)}R` : '-')}
        </div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Entry Time</div>
        <div class="font-semibold text-sm">${this.formatDateTime(trade.entry_time)}</div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Exit Time</div>
        <div class="font-semibold text-sm">${trade.exit_time ? this.formatDateTime(trade.exit_time) : '-'}</div>
      </div>
      <div class="bg-gray-700 rounded-lg p-3">
        <div class="text-xs text-gray-400 mb-1">Contracts</div>
        <div class="font-semibold">${trade.contracts || '-'}</div>
      </div>
    `;

    // Logs
    if (trade.logs && trade.logs.length > 0) {
      this.logsEl.innerHTML = trade.logs.map(log => {
        const eventClass = this.getEventClass(log.event);
        const time = log.ts ? new Date(log.ts).toLocaleTimeString('en-US', { hour12: false }) : '';
        return `
          <div class="log-entry ${eventClass}">
            <div class="flex justify-between items-start mb-1">
              <span class="font-semibold text-sm">${log.event}</span>
              <span class="text-xs text-gray-500">${time}</span>
            </div>
            <div class="text-sm text-gray-300">${log.msg}</div>
          </div>
        `;
      }).join('');
    } else {
      this.logsEl.innerHTML = '<div class="text-gray-500 text-center py-4">No logs available</div>';
    }
  }

  getEventClass(event) {
    if (!event) return '';
    const eventUpper = event.toUpperCase();
    if (eventUpper.includes('ERROR') || eventUpper.includes('FAIL')) return 'error';
    if (eventUpper.includes('FILL') || eventUpper.includes('COMPLETE') || eventUpper.includes('TP')) return 'success';
    if (eventUpper.includes('WARNING') || eventUpper.includes('SL')) return 'warning';
    return '';
  }

  getResultBadgeClass(resultType) {
    switch (resultType) {
      case 'TP': return 'text-green-400';
      case 'SL': return 'text-red-400';
      case 'BE': return 'text-yellow-400';
      case 'SP': return 'text-blue-400';
      default: return 'text-gray-400';
    }
  }

  getResultBadgeBackgroundClass(resultType) {
    switch (resultType) {
      case 'TP': return 'bg-green-600 text-white';
      case 'SL': return 'bg-red-600 text-white';
      case 'BE': return 'bg-yellow-500 text-black';
      case 'SP': return 'bg-blue-500 text-white';
      default: return 'bg-gray-600 text-gray-300';
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
