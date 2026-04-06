/**
 * Admin Dashboard Application
 * Main entry point that orchestrates all components
 */
import { ApiClient } from './ApiClient.js';
import { AnalyticsCharts } from './AnalyticsCharts.js';
import { TradeHistory } from './TradeHistory.js';
import { TradeLogs } from './TradeLogs.js';
import { LineManager } from './LineManager.js';

class AdminApp {
  constructor() {
    this.api = new ApiClient();
    this.charts = new AnalyticsCharts();
    this.tradeHistory = new TradeHistory(this.api);
    this.tradeLogs = new TradeLogs(this.api);
    this.lineManager = new LineManager(this.api);
    
    this.currentTab = 'overview';
    this.analyticsData = null;
    this.statsData = null;
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
      
      // Set up export button
      document.getElementById('export-trades-btn')?.addEventListener('click', () => {
        this.tradeHistory.exportToCSV();
      });
      
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
      
      // Load initial data for overview
      await this.loadOverviewData();
      
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
    const navButtons = document.querySelectorAll('.nav-btn');
    console.log(`[AdminApp] Found ${navButtons.length} nav buttons`);
    
    navButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        console.log(`[AdminApp] Navigating to tab: ${tab}`);
        this.switchTab(tab);
      });
    });
  }

  switchTab(tab) {
    // Update active state on buttons
    document.querySelectorAll('.nav-btn').forEach(btn => {
      if (btn.dataset.tab === tab) {
        btn.classList.remove('text-gray-300', 'hover:bg-gray-700');
        btn.classList.add('bg-blue-600', 'text-white');
      } else {
        btn.classList.remove('bg-blue-600', 'text-white');
        btn.classList.add('text-gray-300', 'hover:bg-gray-700');
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

    // Load tab-specific data
    console.log(`[AdminApp] Loading data for tab: ${tab}`);
    switch (tab) {
      case 'overview':
        this.loadOverviewData();
        break;
      case 'trades':
        console.log('[AdminApp] Loading trades...');
        this.tradeHistory.load();
        break;
      case 'lines':
        this.lineManager.load();
        break;
      case 'analytics':
        this.loadAnalyticsData();
        break;
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
