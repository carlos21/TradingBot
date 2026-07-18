import { AnalyticsCharts } from '../admin/AnalyticsCharts.js';
import { TradeHistory } from '../admin/TradeHistory.js';
import { TradeCalendar } from '../admin/TradeCalendar.js';
import { TradeLogs } from '../admin/TradeLogs.js';
import { LineManager } from '../admin/LineManager.js';
import { DecisionLogs } from '../admin/DecisionLogs.js';
import { SettingsManager } from '../admin/SettingsManager.js';
import { NtManager } from '../admin/NtManager.js';
import { MtManager } from '../admin/MtManager.js';
import { LogPanel } from '../admin/LogPanel.js';
import { PairSelector } from '../admin/PairSelector.js';

const CARD = 'bg-surface-800 border border-surface-700 rounded-xl p-5 shadow-lg';
const TEXT_MUTED = 'text-slate-400';
const TEXT_WIN = 'text-emerald-400';
const TEXT_LOSS = 'text-rose-500';
const TEXT_WARN = 'text-amber-400';

/**
 * AdminDashboardController orchestrates the admin dashboard tabs.
 *
 * Dependencies are injected via the constructor so the controller can be
 * unit-tested with fake implementations of the ports.
 */
export class AdminDashboardController {
  constructor({ api, domService, socket, notification }) {
    this.api = api;
    this.dom = domService;
    this.socket = socket;
    this.notification = notification;

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

    this.currentTab = 'overview';
    this.pairSelectors = {};
    this.overviewAnalytics = null;
    this.analyticsCache = null; // { pair, data } for the analytics tab
    this.statsData = null;
    this.currentTradeView = 'table'; // 'table' or 'calendar'
  }

  async init() {
    try {
      // Mark JS as loaded
      const debugEl = this.dom.getElementById('js-debug');
      if (debugEl) debugEl.textContent = 'JS Loaded ✓';

      // Initialize API and get the server default pair
      console.log('[AdminDashboardController] Starting init...');
      const defaultPair = await this.api.init();
      console.log(`[AdminDashboardController] Got pair: ${defaultPair}`);

      // Set up per-tab pair selectors and propagate the default pair
      this.setupPairSelectors(defaultPair);
      this.tradeHistory.setPair(defaultPair);
      this.tradeCalendar.setPair(defaultPair);
      this.lineManager.setPair(defaultPair);
      this.decisionLogs.setPair(defaultPair);

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
      if (this.dom.getElementById('ninjatrader-tab')) {
        await this.ntManager.init();
      }
      if (this.dom.getElementById('metatrader-tab')) {
        await this.mtManager.init();
      }

      // Initialize log panel (needs socket and api)
      if (this.dom.getElementById('logs-tab') && this.socket) {
        this.logPanel = new LogPanel(this.socket, this.api, this.getTabPair('logs'));
      }

      // Determine initial tab from URL path or legacy server-rendered attribute
      const win = this.dom.getWindow();
      const path = win.location.pathname;
      const match = path.match(/^\/admin\/(\w+)$/);
      const rootEl = this.dom.getElementById('admin-root');
      const initialTab = match?.[1] || rootEl?.dataset.activeTab || 'overview';
      console.log(`[AdminDashboardController] Initial tab: ${initialTab}`);
      this.switchTab(initialTab, false);

      // Set up browser back/forward handling
      this.dom.addEventListener(win, 'popstate', (e) => {
        const path = win.location.pathname;
        const match = path.match(/^\/admin\/(\w+)$/);
        if (match) {
          this.switchTab(match[1], false);
        }
      });

      console.log('[AdminDashboardController] Initialized successfully');
    } catch (error) {
      console.error('[AdminDashboardController] Initialization failed:', error);
      this.notification.alert('Failed to initialize admin dashboard. Check console for details.');
    }
  }

  setupPairSelectors(defaultPair) {
    // Each pair-consuming tab gets its own selector; changing one only
    // affects its own tab. Options come from the configured instruments.
    const pairs = (this.api.instruments || []).map(i => i.symbol);
    const definitions = {
      overview: {
        elementId: 'overview-pair-selector',
        onChange: () => this.loadOverviewData(),
      },
      trades: {
        elementId: 'trades-pair-selector',
        onChange: (pair) => {
          this.tradeHistory.setPair(pair);
          this.tradeCalendar.setPair(pair);
          this.resetAccountFilter();
          this.loadTradeAccounts();
          this.loadTradeData();
        },
      },
      lines: {
        elementId: 'lines-pair-selector',
        onChange: (pair) => {
          this.lineManager.setPair(pair);
          this.lineManager.load();
        },
      },
      analytics: {
        elementId: 'analytics-pair-selector',
        onChange: () => this.loadAnalyticsData(true),
      },
      decisions: {
        elementId: 'decisions-pair-selector',
        onChange: (pair) => {
          this.decisionLogs.setPair(pair);
          this.decisionLogs.load();
        },
      },
      logs: {
        elementId: 'logs-pair-selector',
        onChange: (pair) => {
          if (this.logPanel) this.logPanel.setPair(pair);
        },
      },
    };

    Object.entries(definitions).forEach(([tab, { elementId, onChange }]) => {
      const element = this.dom.getElementById(elementId);
      if (!element) return;
      this.pairSelectors[tab] = new PairSelector({ element, pairs, selected: defaultPair, onChange });
    });
  }

  getTabPair(tab) {
    return this.pairSelectors[tab]?.value || this.api.defaultPair;
  }

  setupNavigation() {
    // Intercept sidebar nav links that point to /admin/* for client-side switching
    const navLinks = this.dom.querySelectorAll('aside nav a[href^="/admin/"]');
    console.log(`[AdminDashboardController] Found ${navLinks.length} admin nav links`);

    navLinks.forEach(link => {
      this.dom.addEventListener(link, 'click', (e) => {
        e.preventDefault();
        const href = link.getAttribute('href');
        const tab = href.replace('/admin/', '');
        console.log(`[AdminDashboardController] Navigating to tab: ${tab}`);
        this.switchTab(tab, true);
      });
    });
  }

  switchTab(tab, pushState = true) {
    // Update active state on sidebar nav links
    this.dom.querySelectorAll('aside nav a').forEach(link => {
      const href = link.getAttribute('href') || '';
      const linkTab = href === '/' ? 'chart' : href.replace('/admin/', '');
      if (linkTab === tab) {
        link.classList.add('active');
      } else {
        link.classList.remove('active');
      }
    });

    // Hide all tabs
    this.dom.querySelectorAll('.tab-content').forEach(content => {
      content.classList.add('hidden');
    });

    // Show selected tab
    const selectedTab = this.dom.getElementById(`${tab}-tab`);
    if (selectedTab) {
      selectedTab.classList.remove('hidden');
    }

    this.currentTab = tab;

    // Update browser URL
    if (pushState) {
      const win = this.dom.getWindow();
      win.history.pushState({ tab }, '', '/admin/' + tab);
    }

    // Load tab-specific data
    console.log(`[AdminDashboardController] Loading data for tab: ${tab}`);
    switch (tab) {
      case 'overview':
        this.loadOverviewData();
        break;
      case 'trades':
        console.log('[AdminDashboardController] Loading trades...');
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
    const tableBtn = this.dom.getElementById('view-table-btn');
    const calendarBtn = this.dom.getElementById('view-calendar-btn');

    if (tableBtn) {
      this.dom.addEventListener(tableBtn, 'click', () => this.switchTradeView('table'));
    }

    if (calendarBtn) {
      this.dom.addEventListener(calendarBtn, 'click', () => this.switchTradeView('calendar'));
    }
  }

  switchTradeView(view) {
    this.currentTradeView = view;

    if (view === 'table') {
      this.tradeCalendar.hide();
      this.tradeHistory.load();
    } else {
      this.tradeHistory.hide?.() || this.dom.getElementById('trades-table-view')?.classList.add('hidden');
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
    const select = this.dom.getElementById('trades-account-filter');
    if (!select) return;

    this.dom.addEventListener(select, 'change', (e) => {
      const account = e.target.value;
      console.log(`[AdminDashboardController] Account filter changed to: ${account || 'All Accounts'}`);
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
    const select = this.dom.getElementById('trades-account-filter');
    if (select) select.value = '';
    this.tradeHistory.selectedAccount = '';
    this.tradeCalendar.selectedAccount = '';
  }

  async loadOverviewData() {
    try {
      const pair = this.getTabPair('overview');
      console.log('[AdminDashboardController] Loading overview data...');
      // Load stats
      const stats = await this.api.getStats(pair);
      console.log('[AdminDashboardController] Got stats:', stats);
      this.statsData = stats;
      this.renderStats(stats);

      // Load analytics for charts
      const analytics = await this.api.getAnalytics(pair);
      console.log('[AdminDashboardController] Got analytics:', analytics);
      this.overviewAnalytics = analytics;

      // Render overview charts
      this.charts.renderEquityCurve('overview-equity-chart', analytics.equity_curve);
      this.charts.renderResultDistribution('overview-result-chart', analytics.result_distribution);
    } catch (error) {
      console.error('[AdminDashboardController] Failed to load overview data:', error);
      this.notification.alert('Failed to load overview data: ' + error.message);
    }
  }

  async loadAnalyticsData(force = false) {
    try {
      const pair = this.getTabPair('analytics');
      if (force || !this.analyticsCache || this.analyticsCache.pair !== pair) {
        this.analyticsCache = { pair, data: await this.api.getAnalytics(pair) };
      }

      const data = this.analyticsCache.data;

      this.charts.renderTradesByHour('analytics-hour-chart', data.trades_by_hour);
      this.charts.renderTradesByDay('analytics-day-chart', data.trades_by_day);
      this.charts.renderMonthlyPnl('analytics-monthly-chart', data.monthly_pnl);
      this.charts.renderPnlDistribution('analytics-pnl-dist-chart', data.pnl_distribution);

      // Render per-account stats
      this.renderAccountStats(data.account_stats);
    } catch (error) {
      console.error('[AdminDashboardController] Failed to load analytics data:', error);
    }
  }

  renderAccountStats(accountStats) {
    const container = this.dom.getElementById('account-stats-grid');
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
    console.log('[AdminDashboardController] Rendering stats:', stats);
    if (!stats) {
      console.warn('[AdminDashboardController] No stats to render');
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
    const totalEl = this.dom.getElementById('stat-total-trades');
    const openEl = this.dom.getElementById('stat-open-trades');
    console.log(`[AdminDashboardController] Setting total_trades: ${stats.total_trades}`);
    if (totalEl) {
      totalEl.textContent = formatNumber(stats.total_trades);
    } else {
      console.error('[AdminDashboardController] stat-total-trades element not found');
    }
    if (openEl) openEl.textContent = `${formatNumber(stats.open_trades)} open`;

    // Win rate
    const winRateEl = this.dom.getElementById('stat-win-rate');
    const winLossEl = this.dom.getElementById('stat-win-loss');
    if (winRateEl) winRateEl.textContent = formatPercent(stats.win_rate);
    if (winLossEl) winLossEl.textContent = `${stats.winning_trades}W / ${stats.losing_trades}L`;

    // P&L (USD)
    const totalPnlUsdEl = this.dom.getElementById('stat-total-pnl-usd');
    const totalPnlREl = this.dom.getElementById('stat-total-pnl-r');
    const avgPnlUsdEl = this.dom.getElementById('stat-avg-pnl-usd');
    if (totalPnlUsdEl) {
      totalPnlUsdEl.textContent = formatCurrency(stats.total_pnl_usd);
      totalPnlUsdEl.className = `text-3xl font-bold ${stats.total_pnl_usd >= 0 ? TEXT_WIN : TEXT_LOSS}`;
    }
    if (totalPnlREl) totalPnlREl.textContent = `R: ${stats.total_pnl?.toFixed(1) || '-'}`;
    if (avgPnlUsdEl) avgPnlUsdEl.textContent = `avg: ${formatCurrency(stats.avg_pnl_usd)}`;

    // Avg Profit Monthly
    const avgProfitMonthlyEl = this.dom.getElementById('stat-avg-profit-monthly');
    const avgREl = this.dom.getElementById('stat-avg-r');
    if (avgProfitMonthlyEl) {
      const avgMonthly = stats.avg_profit_monthly;
      avgProfitMonthlyEl.textContent = avgMonthly !== undefined && avgMonthly !== null ?
        `${avgMonthly >= 0 ? '+' : '-'}$${Math.abs(avgMonthly).toFixed(2)}` : '-';
      avgProfitMonthlyEl.className = `text-3xl font-bold ${avgMonthly >= 0 ? TEXT_WIN : TEXT_LOSS}`;
    }
    if (avgREl) avgREl.textContent = `avg R: ${stats.avg_r_multiple?.toFixed(2) || '-'}`;

    // Enhanced KPIs
    // Max Drawdown
    const maxDdEl = this.dom.getElementById('stat-max-drawdown');
    const maxDdPctEl = this.dom.getElementById('stat-max-drawdown-pct');
    if (maxDdEl) {
      const dd = stats.max_drawdown;
      maxDdEl.textContent = dd !== undefined && dd !== null ? `${dd.toFixed(2)}R` : '-';
    }
    if (maxDdPctEl) {
      const ddPct = stats.max_drawdown_pct;
      maxDdPctEl.textContent = ddPct !== undefined && ddPct !== null ? `${ddPct.toFixed(1)}% of peak` : '-';
    }

    // Profit Factor
    const pfEl = this.dom.getElementById('stat-profit-factor');
    const avgWinLossEl = this.dom.getElementById('stat-avg-win-loss');
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
    const expEl = this.dom.getElementById('stat-expectancy');
    if (expEl) {
      const exp = stats.expectancy;
      expEl.textContent = exp !== undefined && exp !== null ? `${exp >= 0 ? '+' : ''}${exp.toFixed(2)}R` : '-';
      expEl.className = `text-3xl font-bold ${exp >= 0 ? TEXT_WIN : TEXT_LOSS}`;
    }

    // Streak
    const streakEl = this.dom.getElementById('stat-streak');
    const streakDetailEl = this.dom.getElementById('stat-streak-detail');
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
