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
    this.selectedAccount = '';
    this.onTradeClick = null;
  }

  async load() {
    try {
      console.log('[TradeCalendar] Loading trades...');
      // Load all trades (no pagination for calendar view), filtered by account
      const result = await this.api.getTrades(10000, 0, this.selectedAccount);
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
      container.innerHTML = '<span class="text-gray-500 text-sm">No trades</span>';
      return;
    }
    
    container.innerHTML = this.months.map((m, idx) => {
      const shortLabel = new Date(m.year, m.month).toLocaleString('en-US', { month: 'short', year: 'numeric' });
      const active = idx === this.currentMonthIndex ? 'active' : '';
      return `<button class="month-pill ${active}" data-index="${idx}">${shortLabel}</button>`;
    }).join('');
    
    // Add click handlers
    container.querySelectorAll('.month-pill').forEach(pill => {
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
      container.innerHTML = '<div class="p-8 text-center text-gray-500">No trades found</div>';
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
    if (stats.wins) wlParts.push(`<span class="cal-positive">${stats.wins}W</span>`);
    if (stats.losses) wlParts.push(`<span class="cal-negative">${stats.losses}L</span>`);
    if (stats.be) wlParts.push(`<span class="text-yellow-500">${stats.be}BE</span>`);
    if (stats.sp) wlParts.push(`<span class="text-blue-400">${stats.sp}SP</span>`);
    const wlStr = wlParts.join(' / ') || '<span class="cal-neutral">--</span>';
    
    const pnlClass = stats.totalPnl > 0 ? 'cal-positive' : stats.totalPnl < 0 ? 'cal-negative' : 'cal-neutral';
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
      .map(d => `<th>${d}</th>`).join('');
    
    // Build weeks
    let weeksHtml = '';
    let currentWeek = [];
    
    // Adjust start day: Monday = 0, Sunday = 6 -> convert to 0-4 (Mon-Fri)
    // JavaScript getDay(): Sun=0, Mon=1, Tue=2, Wed=3, Thu=4, Fri=5, Sat=6
    const mondayBasedStart = (startDayOfWeek + 6) % 7; // Mon=0, Tue=1, ..., Sun=6
    
    // Empty cells before start of month (only for Mon-Fri)
    for (let i = 0; i < mondayBasedStart; i++) {
      currentWeek.push('<td class="cal-cell cal-empty"></td>');
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
          let badgeClass = 'badge-open';
          let badgeLabel = '?';
          
          if (t.result_type === 'SP') {
            badgeClass = 'badge-sp';
            badgeLabel = 'SP';
          } else if (result !== null && result !== undefined) {
            if (Math.abs(result) < beThreshold) {
              badgeClass = 'badge-be';
              badgeLabel = 'BE';
            } else if (result > 0) {
              badgeClass = 'badge-win';
              badgeLabel = 'W';
            } else {
              badgeClass = 'badge-loss';
              badgeLabel = 'L';
            }
          }
          return `<span class="badge ${badgeClass}">${badgeLabel}</span>`;
        }).join('');
        
        const dayPnl = dayTrades.reduce((sum, t) => sum + (t.pnl_usd || 0), 0);
        const pnlClass = dayPnl > 0 ? 'cal-positive' : dayPnl < 0 ? 'cal-negative' : 'cal-neutral';
        const mainTrade = dayTrades[0];
        
        cellContent = `
          <div class="cal-day-num">${day}</div>
          <div class="cal-trade" data-trade-id="${mainTrade.trade_id}">
            ${tradeBadges}
            <div class="cal-pnl ${pnlClass}">$${dayPnl.toFixed(0)}</div>
          </div>
        `;
        currentWeek.push(`<td class="cal-cell cal-has-trade">${cellContent}</td>`);
      } else {
        cellContent = `<div class="cal-day-num">${day}</div>`;
        currentWeek.push(`<td class="cal-cell">${cellContent}</td>`);
      }
      
      // End of week (Friday = 4 in Monday-based system)
      if (dayOfWeek === 4 || day === daysInMonth) {
        // Fill remaining cells (only 5 columns for Mon-Fri)
        while (currentWeek.length < 5) {
          currentWeek.push('<td class="cal-cell cal-empty"></td>');
        }
        weeksHtml += `<tr>${currentWeek.join('')}</tr>`;
        currentWeek = [];
      }
    }
    
    return `
      <div class="cal-header">
        <h3>${month.label}</h3>
        <div class="cal-stats">
          <div class="stat-pill">${wlStr}</div>
          <div class="stat-pill ${pnlClass}">Net: ${pnlStr}</div>
        </div>
      </div>
      <table class="cal-grid">
        <thead><tr>${dayHeaders}</tr></thead>
        <tbody>${weeksHtml}</tbody>
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
    
    if (tableView) tableView.classList.add('hidden');
    if (calendarView) calendarView.classList.remove('hidden');
    if (tableBtn) tableBtn.classList.remove('active');
    if (calendarBtn) calendarBtn.classList.add('active');
    
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
    
    if (tableView) tableView.classList.remove('hidden');
    if (calendarView) calendarView.classList.add('hidden');
    if (tableBtn) tableBtn.classList.add('active');
    if (calendarBtn) calendarBtn.classList.remove('active');
  }
}
