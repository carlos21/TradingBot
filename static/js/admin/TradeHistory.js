/**
 * Trade History Component
 * Manages the trades table and pagination
 */
export class TradeHistory {
  constructor(apiClient) {
    this.api = apiClient;
    this.trades = [];
    this.total = 0;
    this.limit = 50;
    this.offset = 0;
    this.onTradeClick = null;
  }

  async load() {
    const result = await this.api.getTrades(this.limit, this.offset);
    this.trades = result.trades;
    this.total = result.total;
    this.render();
    this.updatePagination();
  }

  render() {
    const tbody = document.getElementById('trades-tbody');
    if (!tbody) return;

    tbody.innerHTML = this.trades.map(trade => {
      const pnlClass = trade.result > 0 ? 'pnl-positive' : trade.result < 0 ? 'pnl-negative' : '';
      const pnlText = trade.result !== null ? `$${trade.result.toFixed(2)}` : '-';
      
      return `
        <tr class="cursor-pointer transition-colors" data-trade-id="${trade.trade_id}">
          <td class="px-4 py-3 font-mono text-sm">${trade.trade_id.substring(0, 8)}...</td>
          <td class="px-4 py-3">
            <span class="px-2 py-1 rounded text-xs font-medium ${trade.type === 'long' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}">
              ${trade.type.toUpperCase()}
            </span>
          </td>
          <td class="px-4 py-3">${trade.entry.toFixed(2)}</td>
          <td class="px-4 py-3">${trade.exit_price ? trade.exit_price.toFixed(2) : '-'}</td>
          <td class="px-4 py-3 ${pnlClass}">${pnlText}</td>
          <td class="px-4 py-3">
            <span class="px-2 py-1 rounded text-xs font-medium ${this.getResultBadgeClass(trade.result_type)}">
              ${trade.result_type || 'OPEN'}
            </span>
          </td>
          <td class="px-4 py-3 text-sm text-gray-400">${this.formatTime(trade.entry_time)}</td>
          <td class="px-4 py-3">
            <button class="view-logs-btn text-blue-400 hover:text-blue-300 text-sm" data-trade-id="${trade.trade_id}">
              View Logs
            </button>
          </td>
        </tr>
      `;
    }).join('');

    // Add click handlers
    tbody.querySelectorAll('tr').forEach(row => {
      row.addEventListener('click', (e) => {
        if (!e.target.classList.contains('view-logs-btn')) {
          const tradeId = row.dataset.tradeId;
          if (this.onTradeClick) this.onTradeClick(tradeId);
        }
      });
    });

    tbody.querySelectorAll('.view-logs-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const tradeId = btn.dataset.tradeId;
        if (this.onTradeClick) this.onTradeClick(tradeId);
      });
    });
  }

  updatePagination() {
    const info = document.getElementById('trades-pagination-info');
    const prevBtn = document.getElementById('trades-prev-btn');
    const nextBtn = document.getElementById('trades-next-btn');

    if (info) {
      const start = this.total > 0 ? this.offset + 1 : 0;
      const end = Math.min(this.offset + this.limit, this.total);
      info.textContent = `Showing ${start}-${end} of ${this.total} trades`;
    }

    if (prevBtn) {
      prevBtn.disabled = this.offset === 0;
      prevBtn.onclick = () => {
        this.offset = Math.max(0, this.offset - this.limit);
        this.load();
      };
    }

    if (nextBtn) {
      nextBtn.disabled = this.offset + this.limit >= this.total;
      nextBtn.onclick = () => {
        this.offset += this.limit;
        this.load();
      };
    }
  }

  getResultBadgeClass(resultType) {
    switch (resultType) {
      case 'TP': return 'bg-green-900 text-green-300';
      case 'SL': return 'bg-red-900 text-red-300';
      case 'SP': return 'bg-yellow-900 text-yellow-300';
      default: return 'bg-blue-900 text-blue-300';
    }
  }

  formatTime(timestamp) {
    if (!timestamp) return '-';
    const date = new Date(timestamp * 1000);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  exportToCSV() {
    const headers = ['Trade ID', 'Type', 'Entry Price', 'Stop Loss', 'Take Profit', 'Exit Price', 'P&L', 'Result', 'Entry Time', 'Exit Time'];
    const rows = this.trades.map(t => [
      t.trade_id,
      t.type,
      t.entry,
      t.stop_loss,
      t.take_profit,
      t.exit_price || '',
      t.result !== null ? t.result : '',
      t.result_type || 'OPEN',
      new Date(t.entry_time * 1000).toISOString(),
      t.exit_time ? new Date(t.exit_time * 1000).toISOString() : '',
    ]);

    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `trades_${this.api.pair}_${new Date().toISOString().split('T')[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }
}
