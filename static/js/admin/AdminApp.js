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

      // Set up export button
      document.getElementById('export-trades-btn')?.addEventListener('click', () => {
        this.tradeHistory.exportToCSV();
      });

      // Set up view toggle buttons
      this.setupViewToggle();

      // Set up decision logs filters
      this.decisionLogs.bindFilters();

      // Set up test button
      document.getElementById('test-load-btn')?.addEventListener('click', async () => {
        console.log('[Test] Manual trade load triggered');
        try {
          const result = await this.api.getTrades(5, 0);
          console.log('[Test] Direct API result:', result);
          alert(`Loaded ${result.total} trades total. First 5: ${JSON.stringify(result.trades.slice(0, 2), null, 2)}`);
        } catch (e) {
          console.error('[Test] Error:', e);
          alert('Error: ' + e.message);
        }
      });

      // Initialize new manager panels
      this.settingsManager.init();
      if (document.getElementById('ninjatrader-tab')) {
        this.ntManager.init();
      }
      if (document.getElementById('metatrader-tab')) {
        this.mtManager.init();
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
    } catch (error) {
      console.error('[AdminApp] Failed to load analytics data:', error);
    }
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
      totalPnlUsdEl.className = `text-3xl font-bold ${stats.total_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}`;
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
      avgProfitMonthlyEl.className = `text-3xl font-bold ${avgMonthly >= 0 ? 'text-green-400' : 'text-red-400'}`;
    }
    if (avgREl) avgREl.textContent = `avg R: ${stats.avg_r_multiple?.toFixed(2) || '-'}`;
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
