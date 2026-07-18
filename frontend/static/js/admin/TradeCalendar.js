/**
 * Trade Calendar Component
 * Manages the calendar view of trades
 */
export class TradeCalendar {
  constructor(apiClient) {
    this.api = apiClient;
    this.trades = [];
    this.months = [];
    this.currentMonthIndex = 0;
    this.selectedPair = null;
    this.selectedAccount = '';
    this.onTradeClick = null;
  }

  setPair(pair) {
    this.selectedPair = pair;
  }

  async load() {
    try {
      console.log('[TradeCalendar] Loading trades...');
      // Load all trades (no pagination for calendar view), filtered by account
      const result = await this.api.getTrades(this.selectedPair, 10000, 0, this.selectedAccount);
      this.trades = result.trades || [];
      console.log(`[TradeCalendar] Loaded ${this.trades.length} trades`);

      this.processMonths();
      this.render();
    } catch (error) {
      console.error('[TradeCalendar] Failed to load trades:', error);
    }
  }

  setAccount(account) {
    this.selectedAccount = account;
    this.load();
  }

  processMonths() {
    // Group trades by month
    const monthMap = new Map();

    this.trades.forEach(trade => {
      const date = new Date(trade.entry_time * 1000);
      const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
      const monthLabel = date.toLocaleString('en-US', { month: 'long', year: 'numeric' });

      if (!monthMap.has(monthKey)) {
        monthMap.set(monthKey, {
          key: monthKey,
          label: monthLabel,
          year: date.getFullYear(),
          month: date.getMonth(),
          trades: [],
          stats: { wins: 0, losses: 0, be: 0, sp: 0, totalPnl: 0 }
        });
      }

      const month = monthMap.get(monthKey);
      month.trades.push(trade);

      // Update stats
      const result = trade.result;
      const beThreshold = 0.5; // Consider trades between -0.5R and +0.5R as breakeven
      if (trade.result_type === 'SP') {
        month.stats.sp++;
      } else if (result !== null && result !== undefined) {
        if (Math.abs(result) < beThreshold) {
          month.stats.be++;
        } else if (result > 0) {
          month.stats.wins++;
        } else {
          month.stats.losses++;
        }
      }

      if (trade.pnl_usd !== null && trade.pnl_usd !== undefined) {
        month.stats.totalPnl += trade.pnl_usd;
      }
    });

    // Sort months chronologically
    this.months = Array.from(monthMap.values()).sort((a, b) => {
      if (a.year !== b.year) return a.year - b.year;
      return a.month - b.month;
    });

    // Set current month to the most recent
    this.currentMonthIndex = Math.max(0, this.months.length - 1);
  }

  render() {
    this.renderMonthPills();
    this.renderCalendar();
    this.updateNavigation();
  }

  renderMonthPills() {
    const container = document.getElementById('cal-month-pills');
    if (!container) return;

    if (this.months.length === 0) {
      container.innerHTML = '<span class="text-slate-500 text-sm">No trades</span>';
      return;
    }

    container.innerHTML = this.months.map((m, idx) => {
      const shortLabel = new Date(m.year, m.month).toLocaleString('en-US', { month: 'short', year: 'numeric' });
      const isActive = idx === this.currentMonthIndex;
      const baseClass = 'px-3.5 py-1.5 rounded-full border text-sm whitespace-nowrap transition-colors';
      const activeClass = isActive
        ? 'bg-accent-600 border-accent-600 text-slate-100 font-semibold'
        : 'bg-transparent border-surface-700 text-slate-400 hover:border-accent-500 hover:text-slate-100';
      return `<button class="${baseClass} ${activeClass}" data-index="${idx}">${shortLabel}</button>`;
    }).join('');

    // Add click handlers
    container.querySelectorAll('button[data-index]').forEach(pill => {
      pill.addEventListener('click', () => {
        this.currentMonthIndex = parseInt(pill.dataset.index);
        this.render();
      });
    });
  }

  renderCalendar() {
    const container = document.getElementById('calendar-container');
    if (!container) return;

    if (this.months.length === 0) {
      container.innerHTML = '<div class="p-8 text-center text-slate-500">No trades found</div>';
      return;
    }

    const month = this.months[this.currentMonthIndex];
    const calendarHtml = this.buildCalendarHtml(month);
    container.innerHTML = calendarHtml;

    // Add click handlers for trade cells
    container.querySelectorAll('.cal-trade').forEach(cell => {
      cell.addEventListener('click', () => {
        const tradeId = cell.dataset.tradeId;
        if (this.onTradeClick && tradeId) {
          this.onTradeClick(tradeId);
        }
      });
    });
  }

  buildCalendarHtml(month) {
    // Build stats display
    const stats = month.stats;
    const wlParts = [];
    if (stats.wins) wlParts.push(`<span class="text-emerald-400">${stats.wins}W</span>`);
    if (stats.losses) wlParts.push(`<span class="text-rose-500">${stats.losses}L</span>`);
    if (stats.be) wlParts.push(`<span class="text-amber-400">${stats.be}BE</span>`);
    if (stats.sp) wlParts.push(`<span class="text-accent-400">${stats.sp}SP</span>`);
    const wlStr = wlParts.join(' / ') || '<span class="text-slate-500">--</span>';

    const pnlClass = stats.totalPnl > 0 ? 'text-emerald-400' : stats.totalPnl < 0 ? 'text-rose-500' : 'text-slate-500';
    const pnlStr = `$${stats.totalPnl.toFixed(0)}`;

    // Group trades by day
    const dayMap = new Map();
    month.trades.forEach(trade => {
      const date = new Date(trade.entry_time * 1000);
      const day = date.getDate();
      if (!dayMap.has(day)) {
        dayMap.set(day, []);
      }
      dayMap.get(day).push(trade);
    });

    // Build calendar grid
    const firstDay = new Date(month.year, month.month, 1);
    const lastDay = new Date(month.year, month.month + 1, 0);
    const startDayOfWeek = firstDay.getDay(); // 0 = Sunday
    const daysInMonth = lastDay.getDate();

    // Day headers
    const dayHeaders = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
      .map(d => `<th class="py-2.5 text-xs uppercase tracking-wider text-slate-400 text-center border-b border-surface-700 bg-slate-900/10">${d}</th>`).join('');

    // Build weeks
    let weeksHtml = '';
    let currentWeek = [];

    // Adjust start day: Monday = 0, Sunday = 6 -> convert to 0-4 (Mon-Fri)
    // JavaScript getDay(): Sun=0, Mon=1, Tue=2, Wed=3, Thu=4, Fri=5, Sat=6
    const mondayBasedStart = (startDayOfWeek + 6) % 7; // Mon=0, Tue=1, ..., Sun=6

    // Empty cells before start of month (only for Mon-Fri)
    for (let i = 0; i < mondayBasedStart; i++) {
      currentWeek.push('<td class="align-top p-2 h-[70px] md:h-[100px] border border-surface-700 bg-slate-900/10"></td>');
    }

    // Days of month
    for (let day = 1; day <= daysInMonth; day++) {
      // Calculate day of week (Monday=0, Friday=4, Saturday=5, Sunday=6)
      const dayOfWeek = (mondayBasedStart + day - 1) % 7;

      // Skip Saturday (5) and Sunday (6)
      if (dayOfWeek === 5 || dayOfWeek === 6) {
        continue;
      }

      const dayTrades = dayMap.get(day) || [];
      let cellContent = '';

      if (dayTrades.length > 0) {
        const tradeBadges = dayTrades.map(t => {
          const result = t.result;
          const beThreshold = 0.5;
          let badgeClass = 'bg-slate-500/15 text-slate-400';
          let badgeLabel = '?';

          if (t.result_type === 'SP') {
            badgeClass = 'bg-accent-500/15 text-accent-400';
            badgeLabel = 'SP';
          } else if (result !== null && result !== undefined) {
            if (Math.abs(result) < beThreshold) {
              badgeClass = 'bg-amber-400/15 text-amber-400';
              badgeLabel = 'BE';
            } else if (result > 0) {
              badgeClass = 'bg-emerald-400/15 text-emerald-400';
              badgeLabel = 'W';
            } else {
              badgeClass = 'bg-rose-500/15 text-rose-500';
              badgeLabel = 'L';
            }
          }
          return `<span class="inline-flex px-2 py-0.5 rounded text-xs font-bold tracking-wide ${badgeClass}">${badgeLabel}</span>`;
        }).join('');

        const dayPnl = dayTrades.reduce((sum, t) => sum + (t.pnl_usd || 0), 0);
        const dayPnlClass = dayPnl > 0 ? 'text-emerald-400' : dayPnl < 0 ? 'text-rose-500' : 'text-slate-500';
        const mainTrade = dayTrades[0];

        cellContent = `
          <div class="text-xs font-medium text-slate-100 mb-1">${day}</div>
          <div class="cal-trade mt-1 p-1 bg-slate-900/20 rounded cursor-pointer hover:bg-accent-500/20 transition-colors" data-trade-id="${mainTrade.trade_id}">
            ${tradeBadges}
            <div class="text-xs font-bold tabular-nums mt-1 ${dayPnlClass}">$${dayPnl.toFixed(0)}</div>
          </div>
        `;
        currentWeek.push(`<td class="align-top p-2 h-[70px] md:h-[100px] border border-surface-700 bg-accent-500/5">${cellContent}</td>`);
      } else {
        cellContent = `<div class="text-xs font-medium text-slate-500 mb-1">${day}</div>`;
        currentWeek.push(`<td class="align-top p-2 h-[70px] md:h-[100px] border border-surface-700">${cellContent}</td>`);
      }

      // End of week (Friday = 4 in Monday-based system)
      if (dayOfWeek === 4 || day === daysInMonth) {
        // Fill remaining cells (only 5 columns for Mon-Fri)
        while (currentWeek.length < 5) {
          currentWeek.push('<td class="align-top p-2 h-[70px] md:h-[100px] border border-surface-700 bg-slate-900/10"></td>');
        }
        weeksHtml += `<tr class="divide-x divide-surface-700">${currentWeek.join('')}</tr>`;
        currentWeek = [];
      }
    }

    return `
      <div class="px-5 py-4 border-b border-surface-700 flex justify-between items-center flex-wrap gap-3">
        <h3 class="text-lg font-bold text-slate-100">${month.label}</h3>
        <div class="flex gap-3 flex-wrap">
          <div class="text-sm px-3 py-1 rounded-md bg-slate-400/5 whitespace-nowrap">${wlStr}</div>
          <div class="text-sm px-3 py-1 rounded-md bg-slate-400/5 whitespace-nowrap ${pnlClass}">Net: ${pnlStr}</div>
        </div>
      </div>
      <table class="w-full border-collapse table-fixed">
        <thead><tr>${dayHeaders}</tr></thead>
        <tbody class="divide-y divide-surface-700">${weeksHtml}</tbody>
      </table>
    `;
  }

  updateNavigation() {
    const prevBtn = document.getElementById('cal-prev-btn');
    const nextBtn = document.getElementById('cal-next-btn');

    if (prevBtn) {
      prevBtn.disabled = this.currentMonthIndex <= 0;
      prevBtn.onclick = () => {
        if (this.currentMonthIndex > 0) {
          this.currentMonthIndex--;
          this.render();
        }
      };
    }

    if (nextBtn) {
      nextBtn.disabled = this.currentMonthIndex >= this.months.length - 1;
      nextBtn.onclick = () => {
        if (this.currentMonthIndex < this.months.length - 1) {
          this.currentMonthIndex++;
          this.render();
        }
      };
    }
  }

  show() {
    const tableView = document.getElementById('trades-table-view');
    const calendarView = document.getElementById('trades-calendar-view');
    const tableBtn = document.getElementById('view-table-btn');
    const calendarBtn = document.getElementById('view-calendar-btn');

    const activeBtnClass = 'bg-accent-600 text-slate-100';
    const inactiveBtnClass = 'text-slate-400 hover:text-slate-100';

    if (tableView) tableView.classList.add('hidden');
    if (calendarView) calendarView.classList.remove('hidden');
    if (tableBtn) {
      tableBtn.classList.remove(...activeBtnClass.split(' '));
      tableBtn.classList.add(...inactiveBtnClass.split(' '));
    }
    if (calendarBtn) {
      calendarBtn.classList.remove(...inactiveBtnClass.split(' '));
      calendarBtn.classList.add(...activeBtnClass.split(' '));
    }

    // Reload data if needed
    if (this.trades.length === 0) {
      this.load();
    }
  }

  hide() {
    const tableView = document.getElementById('trades-table-view');
    const calendarView = document.getElementById('trades-calendar-view');
    const tableBtn = document.getElementById('view-table-btn');
    const calendarBtn = document.getElementById('view-calendar-btn');

    const activeBtnClass = 'bg-accent-600 text-slate-100';
    const inactiveBtnClass = 'text-slate-400 hover:text-slate-100';

    if (tableView) tableView.classList.remove('hidden');
    if (calendarView) calendarView.classList.add('hidden');
    if (tableBtn) {
      tableBtn.classList.remove(...inactiveBtnClass.split(' '));
      tableBtn.classList.add(...activeBtnClass.split(' '));
    }
    if (calendarBtn) {
      calendarBtn.classList.remove(...activeBtnClass.split(' '));
      calendarBtn.classList.add(...inactiveBtnClass.split(' '));
    }
  }
}
