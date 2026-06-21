/**
 * Admin Dashboard Application
 * Main entry point that orchestrates all components
 */
import { ApiClient } from './ApiClient.js';
import { AnalyticsCharts } from './AnalyticsCharts.js';
import { TradeHistory } from './TradeHistory.js';
import { TradeCalendar } from './TradeCalendar.js';
import { TradeLogs } from './TradeLogs.js';
import { LineManager } from './LineManager.js';
import { DecisionLogs } from './DecisionLogs.js';
import { SettingsManager } from './SettingsManager.js';
import { NtManager } from './NtManager.js';
import { MtManager } from './MtManager.js';
import { LogPanel } from './LogPanel.js';

const CARD = 'bg-surface-800 border border-surface-700 rounded-xl p-5 shadow-lg';
const TEXT_MUTED = 'text-slate-400';
const TEXT_WIN = 'text-emerald-400';
const TEXT_LOSS = 'text-rose-500';
const TEXT_WARN = 'text-amber-400';

class AdminApp {
  constructor() {
    this.api = new ApiClient();
    this.charts = new AnalyticsCharts();
    this.tradeHistory = new TradeHistory(this.api);
    this.tradeCalendar = new TradeCalendar(this.api);
    this.tradeLogs = new TradeLogs(this.api);
    this.lineManager = new LineManager(this.api);
    this.decisionLogs = new DecisionLogs(this.api);
    this.settingsManager = new SettingsManager(this.api);
    this.ntManager = new NtManager(this.api);
    this.mtManager = new MtManager(this.api);
    this.logPanel = null;
    this.socket = null;

    this.currentTab = 'overview';
    this.analyticsData = null;
    this.statsData = null;
    this.currentTradeView = 'table'; // 'table' or 'calendar'
  }

  async init() {
    try {
      // Mark JS as loaded
      const debugEl = document.getElementById('js-debug');
      if (debugEl) debugEl.textContent = 'JS Loaded ✓';

      // Initialize API and get pair
      console.log('[AdminApp] Starting init...');
      const pair = await this.api.init();
      console.log(`[AdminApp] Got pair: ${pair}`);

      // Set up pair selector
      this.setupPairSelector();

      // Set up navigation
      this.setupNavigation();

      // Set up trade logs modal callback
      this.tradeHistory.onTradeClick = (tradeId) => {
        this.tradeLogs.show(tradeId);
      };

      this.tradeCalendar.onTradeClick = (tradeId) => {
        this.tradeLogs.show(tradeId);
      };

      // Set up view toggle buttons
      this.setupViewToggle();

      // Set up account filter
      this.setupAccountFilter();

      // Set up decision logs filters
      this.decisionLogs.bindFilters();

      // Initialize new manager panels
      await this.settingsManager.init();
      if (document.getElementById('ninjatrader-tab')) {
        await this.ntManager.init();
      }
      if (document.getElementById('metatrader-tab')) {
        await this.mtManager.init();
      }

      // Initialize Socket.IO connection for admin
      if (typeof io !== 'undefined') {
        this.socket = io();
      }

      // Initialize log panel (needs socket)
      if (document.getElementById('logs-tab') && this.socket) {
        this.logPanel = new LogPanel(this.socket);
      }

      // Determine initial tab from server-rendered attribute or URL
      const rootEl = document.getElementById('admin-root');
      const initialTab = rootEl?.dataset.activeTab || 'overview';
      console.log(`[AdminApp] Initial tab: ${initialTab}`);
      this.switchTab(initialTab, false);

      // Set up browser back/forward handling
      window.addEventListener('popstate', (e) => {
        const path = window.location.pathname;
        const match = path.match(/^\/admin\/(\w+)$/);
        if (match) {
          this.switchTab(match[1], false);
        }
      });

      console.log('[AdminApp] Initialized successfully');
    } catch (error) {
      console.error('[AdminApp] Initialization failed:', error);
      alert('Failed to initialize admin dashboard. Check console for details.');
    }
  }

  setupPairSelector() {
    const selector = document.getElementById('pairSelector');
    if (!selector) return;
    
    // Set current pair
    selector.value = this.api.pair;
    
    selector.addEventListener('change', (e) => {
      const newPair = e.target.value;
      console.log(`[AdminApp] Pair changed to: ${newPair}`);
      this.api.setPair(newPair);
      // Reset account filter for the new pair
      this.resetAccountFilter();
      this.loadTradeAccounts();
      // Reload current tab data
      this.switchTab(this.currentTab);
    });
  }

  setupNavigation() {
    // Intercept sidebar nav links that point to /admin/* for client-side switching
    const navLinks = document.querySelectorAll('aside nav a[href^="/admin/"]');
    console.log(`[AdminApp] Found ${navLinks.length} admin nav links`);

    navLinks.forEach(link => {
      link.addEventListener('click', (e) => {
        e.preventDefault();
        const href = link.getAttribute('href');
        const tab = href.replace('/admin/', '');
        console.log(`[AdminApp] Navigating to tab: ${tab}`);
        this.switchTab(tab, true);
      });
    });
  }

  switchTab(tab, pushState = true) {
    // Update active state on sidebar nav links
    document.querySelectorAll('aside nav a').forEach(link => {
      const href = link.getAttribute('href') || '';
      const linkTab = href === '/' ? 'chart' : href.replace('/admin/', '');
      if (linkTab === tab) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });

    // Hide all tabs
    document.querySelectorAll('.tab-content').forEach(content => {
      content.classList.add('hidden');
    });

    // Show selected tab
    const selectedTab = document.getElementById(`${tab}-tab`);
    if (selectedTab) {
      selectedTab.classList.remove('hidden');
    }

    this.currentTab = tab;

    // Update browser URL
    if (pushState) {
      history.pushState({ tab }, '', '/admin/' + tab);
    }

    // Load tab-specific data
    console.log(`[AdminApp] Loading data for tab: ${tab}`);
    switch (tab) {
      case 'overview':
        this.loadOverviewData();
        break;
      case 'trades':
        console.log('[AdminApp] Loading trades...');
        this.loadTradeData();
        break;
      case 'lines':
        this.lineManager.load();
        break;
      case 'analytics':
        this.loadAnalyticsData();
        break;
      case 'decisions':
        this.decisionLogs.load();
        break;
      case 'settings':
        this.settingsManager.loadSettings();
        break;
      case 'ninjatrader':
        // NT panel is event-driven; no auto-load needed
        break;
      case 'metatrader':
        // MT panel is event-driven; no auto-load needed
        break;
      case 'logs':
        // Log panel is event-driven; no auto-load needed
        break;
    }
  }

  setupViewToggle() {
    const tableBtn = document.getElementById('view-table-btn');
    const calendarBtn = document.getElementById('view-calendar-btn');
    
    if (tableBtn) {
      tableBtn.addEventListener('click', () => this.switchTradeView('table'));
    }
    
    if (calendarBtn) {
      calendarBtn.addEventListener('click', () => this.switchTradeView('calendar'));
    }
  }

  switchTradeView(view) {
    this.currentTradeView = view;
    
    if (view === 'table') {
      this.tradeCalendar.hide();
      this.tradeHistory.load();
    } else {
      this.tradeHistory.hide?.() || document.getElementById('trades-table-view')?.classList.add('hidden');
      this.tradeCalendar.show();
    }
  }

  loadTradeData() {
    if (this.currentTradeView === 'table') {
      this.tradeHistory.load();
    } else {
      this.tradeCalendar.load();
    }
  }

  setupAccountFilter() {
    const select = document.getElementById('trades-account-filter');
    if (!select) return;

    select.addEventListener('change', (e) => {
      const account = e.target.value;
      console.log(`[AdminApp] Account filter changed to: ${account || 'All Accounts'}`);
      this.tradeHistory.setAccount(account);
      this.tradeCalendar.setAccount(account);
    });

    // Initial population
    this.loadTradeAccounts();
  }

  async loadTradeAccounts() {
    await this.tradeHistory.loadAccounts();
  }

  resetAccountFilter() {
    const select = document.getElementById('trades-account-filter');
    if (select) select.value = '';
    this.tradeHistory.selectedAccount = '';
    this.tradeCalendar.selectedAccount = '';
  }

  async loadOverviewData() {
    try {
      console.log('[AdminApp] Loading overview data...');
      // Load stats
      const stats = await this.api.getStats();
      console.log('[AdminApp] Got stats:', stats);
      this.statsData = stats;
      this.renderStats(stats);

      // Load analytics for charts
      const analytics = await this.api.getAnalytics();
      console.log('[AdminApp] Got analytics:', analytics);
      this.analyticsData = analytics;
      
      // Render overview charts
      this.charts.renderEquityCurve('overview-equity-chart', analytics.equity_curve);
      this.charts.renderResultDistribution('overview-result-chart', analytics.result_distribution);
    } catch (error) {
      console.error('[AdminApp] Failed to load overview data:', error);
      alert('Failed to load overview data: ' + error.message);
    }
  }

  async loadAnalyticsData() {
    try {
      if (!this.analyticsData) {
        this.analyticsData = await this.api.getAnalytics();
      }

      const data = this.analyticsData;

      this.charts.renderTradesByHour('analytics-hour-chart', data.trades_by_hour);
      this.charts.renderTradesByDay('analytics-day-chart', data.trades_by_day);
      this.charts.renderMonthlyPnl('analytics-monthly-chart', data.monthly_pnl);
      this.charts.renderPnlDistribution('analytics-pnl-dist-chart', data.pnl_distribution);

      // Render per-account stats
      this.renderAccountStats(data.account_stats);
    } catch (error) {
      console.error('[AdminApp] Failed to load analytics data:', error);
    }
  }

  renderAccountStats(accountStats) {
    const container = document.getElementById('account-stats-grid');
    if (!container) return;

    if (!accountStats || accountStats.length === 0) {
      container.innerHTML = `<div class="col-span-full ${TEXT_MUTED} text-center py-4">No account data available</div>`;
      return;
    }

    container.innerHTML = accountStats.map(acct => {
      const pnlClass = acct.total_pnl_usd >= 0 ? TEXT_WIN : TEXT_LOSS;
      const pnlSign = acct.total_pnl_usd >= 0 ? '+' : '-';
      const pnlAbs = Math.abs(acct.total_pnl_usd).toFixed(2);
      const winRatePct = (acct.win_rate * 100).toFixed(1);

      return `
        <div class="${CARD}">
          <div class="flex items-center justify-between mb-3">
            <span class="text-sm font-semibold text-slate-300">${acct.account}</span>
            <span class="text-xs text-slate-500">${acct.total_trades} trades</span>
          </div>
          <div class="text-2xl font-bold ${pnlClass}">${pnlSign}$${pnlAbs}</div>
          <div class="flex items-center gap-3 mt-2 text-xs ${TEXT_MUTED}">
            <span class="${winRatePct >= 50 ? TEXT_WIN : TEXT_LOSS}">${winRatePct}% WR</span>
            <span>|</span>
            <span>${acct.winning_trades}W / ${acct.losing_trades}L</span>
            <span>|</span>
            <span>avg ${acct.avg_pnl_usd >= 0 ? '+' : '-'}$${Math.abs(acct.avg_pnl_usd).toFixed(2)}</span>
          </div>
        </div>
      `;
    }).join('');
  }

  renderStats(stats) {
    console.log('[AdminApp] Rendering stats:', stats);
    if (!stats) {
      console.warn('[AdminApp] No stats to render');
      return;
    }
    
    // Format helpers
    const formatNumber = (n) => n !== undefined && n !== null ? n.toLocaleString() : '-';
    const formatPercent = (n) => n !== undefined && n !== null ? `${(n * 100).toFixed(1)}%` : '-';
    const formatCurrency = (n) => {
      if (n === undefined || n === null) return '-';
      const formatted = Math.abs(n).toFixed(2);
      const sign = n >= 0 ? '+' : '-';
      return `${sign}$${formatted}`;
    };

    // Total trades
    const totalEl = document.getElementById('stat-total-trades');
    const openEl = document.getElementById('stat-open-trades');
    console.log(`[AdminApp] Setting total_trades: ${stats.total_trades}`);
    if (totalEl) {
      totalEl.textContent = formatNumber(stats.total_trades);
    } else {
      console.error('[AdminApp] stat-total-trades element not found');
    }
    if (openEl) openEl.textContent = `${formatNumber(stats.open_trades)} open`;

    // Win rate
    const winRateEl = document.getElementById('stat-win-rate');
    const winLossEl = document.getElementById('stat-win-loss');
    if (winRateEl) winRateEl.textContent = formatPercent(stats.win_rate);
    if (winLossEl) winLossEl.textContent = `${stats.winning_trades}W / ${stats.losing_trades}L`;

    // P&L (USD)
    const totalPnlUsdEl = document.getElementById('stat-total-pnl-usd');
    const totalPnlREl = document.getElementById('stat-total-pnl-r');
    const avgPnlUsdEl = document.getElementById('stat-avg-pnl-usd');
    if (totalPnlUsdEl) {
      totalPnlUsdEl.textContent = formatCurrency(stats.total_pnl_usd);
      totalPnlUsdEl.className = `text-3xl font-bold ${stats.total_pnl_usd >= 0 ? TEXT_WIN : TEXT_LOSS}`;
    }
    if (totalPnlREl) totalPnlREl.textContent = `R: ${stats.total_pnl?.toFixed(1) || '-'}`;
    if (avgPnlUsdEl) avgPnlUsdEl.textContent = `avg: ${formatCurrency(stats.avg_pnl_usd)}`;

    // Avg Profit Monthly
    const avgProfitMonthlyEl = document.getElementById('stat-avg-profit-monthly');
    const avgREl = document.getElementById('stat-avg-r');
    if (avgProfitMonthlyEl) {
      const avgMonthly = stats.avg_profit_monthly;
      avgProfitMonthlyEl.textContent = avgMonthly !== undefined && avgMonthly !== null ? 
        `${avgMonthly >= 0 ? '+' : '-'}$${Math.abs(avgMonthly).toFixed(2)}` : '-';
      avgProfitMonthlyEl.className = `text-3xl font-bold ${avgMonthly >= 0 ? TEXT_WIN : TEXT_LOSS}`;
    }
    if (avgREl) avgREl.textContent = `avg R: ${stats.avg_r_multiple?.toFixed(2) || '-'}`;

    // Enhanced KPIs
    // Max Drawdown
    const maxDdEl = document.getElementById('stat-max-drawdown');
    const maxDdPctEl = document.getElementById('stat-max-drawdown-pct');
    if (maxDdEl) {
      const dd = stats.max_drawdown;
      maxDdEl.textContent = dd !== undefined && dd !== null ? `${dd.toFixed(2)}R` : '-';
    }
    if (maxDdPctEl) {
      const ddPct = stats.max_drawdown_pct;
      maxDdPctEl.textContent = ddPct !== undefined && ddPct !== null ? `${ddPct.toFixed(1)}% of peak` : '-';
    }

    // Profit Factor
    const pfEl = document.getElementById('stat-profit-factor');
    const avgWinLossEl = document.getElementById('stat-avg-win-loss');
    if (pfEl) {
      const pf = stats.profit_factor;
      pfEl.textContent = pf !== undefined && pf !== null ? pf.toFixed(2) : '-';
      pfEl.className = `text-3xl font-bold ${pf >= 1.5 ? TEXT_WIN : pf >= 1.0 ? TEXT_WARN : TEXT_LOSS}`;
    }
    if (avgWinLossEl) {
      const aw = stats.avg_win;
      const al = stats.avg_loss;
      avgWinLossEl.textContent = (aw !== undefined && al !== undefined) ? `${aw.toFixed(2)}R / ${al.toFixed(2)}R` : '-';
    }

    // Expectancy
    const expEl = document.getElementById('stat-expectancy');
    if (expEl) {
      const exp = stats.expectancy;
      expEl.textContent = exp !== undefined && exp !== null ? `${exp >= 0 ? '+' : ''}${exp.toFixed(2)}R` : '-';
      expEl.className = `text-3xl font-bold ${exp >= 0 ? TEXT_WIN : TEXT_LOSS}`;
    }

    // Streak
    const streakEl = document.getElementById('stat-streak');
    const streakDetailEl = document.getElementById('stat-streak-detail');
    if (streakEl) {
      const cw = stats.max_consecutive_wins;
      const cl = stats.max_consecutive_losses;
      const cur = stats.current_streak;
      const curType = stats.current_streak_type;
      streakEl.textContent = (cw !== undefined && cl !== undefined) ? `${cw}W / ${cl}L` : '-';
    }
    if (streakDetailEl) {
      const cur = stats.current_streak;
      const curType = stats.current_streak_type;
      if (cur !== undefined && curType) {
        const color = curType === 'win' ? TEXT_WIN : TEXT_LOSS;
        streakDetailEl.innerHTML = `Current: <span class="${color}">${cur} ${curType}${cur > 1 ? 's' : ''}</span>`;
      } else {
        streakDetailEl.textContent = '-';
      }
    }
  }
}

// Global error handling
window.addEventListener('error', (e) => {
  console.error('[Global Error]', e.message, e.filename, e.lineno);
});

window.addEventListener('unhandledrejection', (e) => {
  console.error('[Unhandled Promise Rejection]', e.reason);
});

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  console.log('[AdminApp] DOM ready, initializing app...');
  const app = new AdminApp();
  app.init().catch(err => {
    console.error('[AdminApp] Init failed:', err);
    alert('Failed to initialize: ' + err.message);
  });
});
