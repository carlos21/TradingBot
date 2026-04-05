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
      // Initialize API and get pair
      const pair = await this.api.init();
      document.getElementById('pairDisplay').textContent = `Pair: ${pair}`;
      
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
      
      // Load initial data for overview
      await this.loadOverviewData();
      
      console.log('[AdminApp] Initialized successfully');
    } catch (error) {
      console.error('[AdminApp] Initialization failed:', error);
      alert('Failed to initialize admin dashboard. Check console for details.');
    }
  }

  setupNavigation() {
    const navButtons = document.querySelectorAll('.nav-btn');
    
    navButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
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
    switch (tab) {
      case 'overview':
        this.loadOverviewData();
        break;
      case 'trades':
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
      // Load stats
      const stats = await this.api.getStats();
      this.statsData = stats;
      this.renderStats(stats);

      // Load analytics for charts
      const analytics = await this.api.getAnalytics();
      this.analyticsData = analytics;
      
      // Render overview charts
      this.charts.renderEquityCurve('overview-equity-chart', analytics.equity_curve);
      this.charts.renderResultDistribution('overview-result-chart', analytics.result_distribution);
    } catch (error) {
      console.error('[AdminApp] Failed to load overview data:', error);
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
    if (totalEl) totalEl.textContent = formatNumber(stats.total_trades);
    if (openEl) openEl.textContent = `${formatNumber(stats.open_trades)} open`;

    // Win rate
    const winRateEl = document.getElementById('stat-win-rate');
    const winLossEl = document.getElementById('stat-win-loss');
    if (winRateEl) winRateEl.textContent = formatPercent(stats.win_rate);
    if (winLossEl) winLossEl.textContent = `${stats.winning_trades}W / ${stats.losing_trades}L`;

    // P&L
    const totalPnlEl = document.getElementById('stat-total-pnl');
    const avgPnlEl = document.getElementById('stat-avg-pnl');
    if (totalPnlEl) {
      totalPnlEl.textContent = formatCurrency(stats.total_pnl);
      totalPnlEl.className = `text-3xl font-bold ${stats.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`;
    }
    if (avgPnlEl) avgPnlEl.textContent = `avg: ${formatCurrency(stats.avg_pnl)}`;

    // Profit factor
    const profitFactorEl = document.getElementById('stat-profit-factor');
    const avgREl = document.getElementById('stat-avg-r');
    if (profitFactorEl) profitFactorEl.textContent = stats.profit_factor?.toFixed(2) || '-';
    if (avgREl) avgREl.textContent = `avg R: ${stats.avg_r_multiple?.toFixed(2) || '-'}`;
  }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  const app = new AdminApp();
  app.init();
});
