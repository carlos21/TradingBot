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
    this.selectedPair = null;
    this.selectedAccount = '';
    this.selectedTradeIds = new Set();
    this.onTradeClick = null;
  }

  setPair(pair) {
    this.selectedPair = pair;
    this.offset = 0;
  }

  async load() {
    try {
      console.log('[TradeHistory] Loading trades...');
      const result = await this.api.getTrades(this.selectedPair, this.limit, this.offset, this.selectedAccount);
      console.log('[TradeHistory] Got result:', result);
      this.trades = result.trades || [];
      this.total = result.total || 0;
      this.render();
      this.updatePagination();
    } catch (error) {
      console.error('[TradeHistory] Failed to load trades:', error);
      alert('Failed to load trades: ' + error.message);
    }
  }

  setAccount(account) {
    this.selectedAccount = account;
    this.offset = 0;
    this.load();
  }

  async loadAccounts() {
    try {
      const result = await this.api.getTradeAccounts(this.selectedPair);
      const accounts = result.accounts || [];
      const select = document.getElementById('trades-account-filter');
      if (!select) return;

      const currentValue = select.value;
      select.innerHTML = '<option value="">All Accounts</option>' +
        accounts.map(a => `<option value="${this.escapeHtml(a)}">${this.escapeHtml(a)}</option>`).join('');

      // Preserve current selection if it's still valid, otherwise reset
      if (currentValue && accounts.includes(currentValue)) {
        select.value = currentValue;
      } else {
        select.value = this.selectedAccount || '';
      }
    } catch (error) {
      console.error('[TradeHistory] Failed to load accounts:', error);
    }
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  render() {
    console.log('[TradeHistory] Rendering trades:', this.trades);
    const tbody = document.getElementById('trades-tbody');
    if (!tbody) {
      console.error('[TradeHistory] tbody element not found!');
      return;
    }

    if (!this.trades || this.trades.length === 0) {
      console.log('[TradeHistory] No trades to display');
      tbody.innerHTML = '<tr><td colspan="10" class="px-4 py-8 text-center text-slate-500">No trades found</td></tr>';
      return;
    }
    console.log(`[TradeHistory] Rendering ${this.trades.length} trades`);

    tbody.innerHTML = this.trades.map(trade => {
      const result = trade.result;
      const pnlClass = result > 0 ? 'text-emerald-400' : result < 0 ? 'text-rose-500' : '';
      // Show actual dollar P&L (pnl_usd from backend)
      const pnlText = trade.pnl_usd !== null && trade.pnl_usd !== undefined
        ? `$${trade.pnl_usd.toFixed(2)}`
        : (result !== null && result !== undefined ? `${result.toFixed(2)}R` : '-');
      // Calculate % return on account. Prefer exact broker-reported values:
      // pnl_usd / account_balance * 100. Fall back to R * risk_pct for legacy trades.
      let pctText = '-';
      if (trade.pnl_usd !== null && trade.pnl_usd !== undefined
          && trade.account_balance !== null && trade.account_balance !== undefined
          && trade.account_balance > 0) {
        const pct = trade.pnl_usd / trade.account_balance * 100;
        pctText = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
      } else if (result !== null && result !== undefined && trade.risk_pct) {
        const pct = result * trade.risk_pct;
        pctText = `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`;
      }

      const isSelected = this.selectedTradeIds.has(trade.trade_id);
      return `
        <tr class="cursor-pointer transition-colors hover:bg-accent-500/10" data-trade-id="${trade.trade_id}">
          <td class="px-4 py-3">
            <input type="checkbox" class="trade-select-checkbox rounded border-surface-600 bg-surface-700 text-accent-500 focus:ring-accent-500" data-trade-id="${trade.trade_id}" ${isSelected ? 'checked' : ''}>
          </td>
          <td class="px-4 py-3">
            <span class="px-2 py-1 rounded text-xs font-medium ${trade.type === 'long' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-rose-500/15 text-rose-500'}">
              ${trade.type.toUpperCase()}
            </span>
          </td>
          <td class="px-4 py-3 text-sm text-slate-400">${trade.account || '-'}</td>
          <td class="px-4 py-3">${trade.entry.toFixed(2)}</td>
          <td class="px-4 py-3">${trade.exit_price ? trade.exit_price.toFixed(2) : '-'}</td>
          <td class="px-4 py-3 ${pnlClass}">${pnlText}</td>
          <td class="px-4 py-3 ${pnlClass}">${pctText}</td>
          <td class="px-4 py-3">
            <span class="px-2 py-1 rounded text-xs font-medium ${this.getResultBadgeClass(this.getResultLabel(trade))}">
              ${this.getResultLabel(trade)}
            </span>
          </td>
          <td class="px-4 py-3 text-sm text-slate-400">${this.formatTime(trade.entry_time)}</td>
          <td class="px-4 py-3">
            <div class="flex items-center gap-2">
              <button class="view-logs-btn action-btn hover:text-accent-400" data-trade-id="${trade.trade_id}" title="View logs">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
              </button>
              <button class="delete-trade-btn action-btn hover:text-rose-500" data-trade-id="${trade.trade_id}" title="Delete trade">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');

    // Add click handlers
    tbody.querySelectorAll('tr').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('.view-logs-btn') || e.target.closest('.delete-trade-btn') || e.target.closest('.trade-select-checkbox')) {
          return;
        }
        const tradeId = row.dataset.tradeId;
        if (this.onTradeClick) this.onTradeClick(tradeId);
      });
    });

    tbody.querySelectorAll('.view-logs-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const tradeId = btn.dataset.tradeId;
        if (this.onTradeClick) this.onTradeClick(tradeId);
      });
    });

    tbody.querySelectorAll('.delete-trade-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const tradeId = btn.dataset.tradeId;
        if (!confirm(`Delete trade ${tradeId} and all related data? This cannot be undone.`)) {
          return;
        }
        try {
          await this.api.deleteTrade(tradeId);
          this.selectedTradeIds.delete(tradeId);
          this.load();
        } catch (error) {
          console.error('[TradeHistory] Failed to delete trade:', error);
          alert('Failed to delete trade: ' + error.message);
        }
      });
    });

    tbody.querySelectorAll('.trade-select-checkbox').forEach(checkbox => {
      checkbox.addEventListener('click', (e) => {
        e.stopPropagation();
        const tradeId = checkbox.dataset.tradeId;
        if (checkbox.checked) {
          this.selectedTradeIds.add(tradeId);
        } else {
          this.selectedTradeIds.delete(tradeId);
        }
        this.updateBulkDeleteButton();
        this.updateSelectAllCheckbox();
      });
    });

    this.attachSelectAllHandler();
    this.attachBulkDeleteHandler();
    this.updateBulkDeleteButton();
    this.updateSelectAllCheckbox();
  }

  attachSelectAllHandler() {
    const selectAll = document.getElementById('select-all-trades');
    if (!selectAll) return;
    selectAll.replaceWith(selectAll.cloneNode(true));
    const fresh = document.getElementById('select-all-trades');
    fresh.addEventListener('click', (e) => {
      e.stopPropagation();
      const checked = fresh.checked;
      this.trades.forEach(trade => {
        if (checked) {
          this.selectedTradeIds.add(trade.trade_id);
        } else {
          this.selectedTradeIds.delete(trade.trade_id);
        }
      });
      this.render();
      this.updateBulkDeleteButton();
    });
  }

  attachBulkDeleteHandler() {
    const btn = document.getElementById('bulk-delete-trades-btn');
    if (!btn) return;
    btn.replaceWith(btn.cloneNode(true));
    const fresh = document.getElementById('bulk-delete-trades-btn');
    fresh.addEventListener('click', async () => {
      const ids = [...this.selectedTradeIds];
      if (ids.length === 0) return;
      if (!confirm(`Delete ${ids.length} trade${ids.length === 1 ? '' : 's'} and all related data? This cannot be undone.`)) {
        return;
      }
      try {
        await this.api.deleteTrades(ids);
        this.selectedTradeIds.clear();
        this.load();
      } catch (error) {
        console.error('[TradeHistory] Failed to delete trades:', error);
        alert('Failed to delete trades: ' + error.message);
      }
    });
  }

  updateBulkDeleteButton() {
    const btn = document.getElementById('bulk-delete-trades-btn');
    if (!btn) return;
    const count = this.selectedTradeIds.size;
    btn.textContent = count > 0 ? `Delete Selected (${count})` : 'Delete Selected';
    btn.disabled = count === 0;
  }

  updateSelectAllCheckbox() {
    const selectAll = document.getElementById('select-all-trades');
    if (!selectAll || this.trades.length === 0) return;
    const visibleIds = this.trades.map(t => t.trade_id);
    const allSelected = visibleIds.every(id => this.selectedTradeIds.has(id));
    selectAll.checked = allSelected;
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

  getResultLabel(trade) {
    if (trade.result_type) return trade.result_type;
    return trade.status === 'open' ? 'Open' : '-';
  }

  getResultBadgeClass(resultType) {
    switch (resultType) {
      case 'TP': return 'bg-emerald-500/20 text-emerald-400';
      case 'SL': return 'bg-rose-500/20 text-rose-500';
      case 'BE': return 'bg-amber-400/20 text-amber-400';
      case 'SP': return 'bg-accent-500/20 text-accent-400';
      case 'CLOSE': return 'bg-slate-500/20 text-slate-400';
      case 'Open': return 'bg-slate-500/20 text-slate-400';
      default: return 'bg-slate-500/20 text-slate-400';
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

  hide() {
    const tableView = document.getElementById('trades-table-view');
    const tableBtn = document.getElementById('view-table-btn');
    if (tableView) tableView.classList.add('hidden');
    if (tableBtn) {
      tableBtn.classList.remove('bg-accent-600', 'text-slate-100');
      tableBtn.classList.add('text-slate-400', 'hover:text-slate-100');
    }
  }

  show() {
    const tableView = document.getElementById('trades-table-view');
    const tableBtn = document.getElementById('view-table-btn');
    const calendarBtn = document.getElementById('view-calendar-btn');
    if (tableView) tableView.classList.remove('hidden');
    if (tableBtn) {
      tableBtn.classList.remove('text-slate-400', 'hover:text-slate-100');
      tableBtn.classList.add('bg-accent-600', 'text-slate-100');
    }
    if (calendarBtn) {
      calendarBtn.classList.remove('bg-accent-600', 'text-slate-100');
      calendarBtn.classList.add('text-slate-400', 'hover:text-slate-100');
    }
    this.load();
  }
}
