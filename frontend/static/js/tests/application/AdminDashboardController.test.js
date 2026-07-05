import { describe, it, expect, beforeEach, vi } from 'vitest';
import { AdminDashboardController } from '../../application/AdminDashboardController.js';
import { AnalyticsCharts } from '../../admin/AnalyticsCharts.js';
import { TradeHistory } from '../../admin/TradeHistory.js';
import { TradeCalendar } from '../../admin/TradeCalendar.js';
import { TradeLogs } from '../../admin/TradeLogs.js';
import { LineManager } from '../../admin/LineManager.js';
import { DecisionLogs } from '../../admin/DecisionLogs.js';
import { SettingsManager } from '../../admin/SettingsManager.js';
import { NtManager } from '../../admin/NtManager.js';
import { MtManager } from '../../admin/MtManager.js';
import { ApiClient } from '../../admin/ApiClient.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { FakeSocket } from '../fakes/FakeSocket.js';
import { FakeDomService } from '../fakes/FakeDomService.js';
import { FakeNotification } from '../fakes/FakeNotification.js';

class FakeChart {
  constructor(ctx, config) {
    this.ctx = ctx;
    this.config = config;
  }
  destroy() {}
}

function setupAdminDocument() {
  document.body.innerHTML = `
    <div id="admin-root" data-active-tab="overview">
      <span id="js-debug"></span>
      <select id="pairSelector">
        <option value="MNQ">MNQ</option>
        <option value="MES">MES</option>
      </select>

      <aside>
        <nav>
          <a href="/">Chart</a>
          <a href="/admin/overview">Overview</a>
          <a href="/admin/trades">Trades</a>
          <a href="/admin/lines">Lines</a>
          <a href="/admin/analytics">Analytics</a>
          <a href="/admin/decisions">Decisions</a>
          <a href="/admin/settings">Settings</a>
          <a href="/admin/ninjatrader">NinjaTrader</a>
          <a href="/admin/metatrader">MetaTrader</a>
          <a href="/admin/logs">Logs</a>
        </nav>
      </aside>

      <div id="overview-tab" class="tab-content">
        <div id="stat-total-trades"></div>
        <div id="stat-open-trades"></div>
        <div id="stat-win-rate"></div>
        <div id="stat-win-loss"></div>
        <div id="stat-total-pnl-usd"></div>
        <div id="stat-total-pnl-r"></div>
        <div id="stat-avg-pnl-usd"></div>
        <div id="stat-avg-profit-monthly"></div>
        <div id="stat-avg-r"></div>
        <div id="stat-max-drawdown"></div>
        <div id="stat-max-drawdown-pct"></div>
        <div id="stat-profit-factor"></div>
        <div id="stat-avg-win-loss"></div>
        <div id="stat-expectancy"></div>
        <div id="stat-streak"></div>
        <div id="stat-streak-detail"></div>
        <canvas id="overview-equity-chart"></canvas>
        <canvas id="overview-result-chart"></canvas>
      </div>

      <div id="trades-tab" class="tab-content hidden">
        <button id="view-table-btn"></button>
        <button id="view-calendar-btn"></button>
        <select id="trades-account-filter"></select>
        <div id="trades-table-view">
          <table><tbody id="trades-tbody"></tbody></table>
        </div>
        <div id="trades-calendar-view" class="hidden">
          <div id="cal-month-pills"></div>
          <div id="calendar-container"></div>
        </div>
        <span id="trades-pagination-info"></span>
        <button id="trades-prev-btn"></button>
        <button id="trades-next-btn"></button>
      </div>

      <div id="lines-tab" class="tab-content hidden">
        <button id="add-line-btn"></button>
        <table><tbody id="lines-tbody"></tbody></table>
        <div id="line-modal" class="hidden">
          <form id="line-form">
            <input id="line-id" />
            <input id="line-price" />
            <button id="cancel-line-modal" type="button"></button>
          </form>
          <h2 id="line-modal-title"></h2>
        </div>
      </div>

      <div id="analytics-tab" class="tab-content hidden">
        <div id="account-stats-grid"></div>
        <canvas id="analytics-hour-chart"></canvas>
        <canvas id="analytics-day-chart"></canvas>
        <canvas id="analytics-monthly-chart"></canvas>
        <canvas id="analytics-pnl-dist-chart"></canvas>
      </div>

      <div id="decisions-tab" class="tab-content hidden">
        <select id="decisions-event-filter"></select>
        <input id="decisions-line-id-filter" />
        <select id="decisions-limit-filter"></select>
        <button id="refresh-decisions-btn"></button>
        <table><tbody id="decisions-tbody"></tbody></table>
        <div id="decisions-empty" class="hidden"></div>
      </div>

      <div id="settings-tab" class="tab-content hidden">
        <input id="settings-pair" />
        <input id="settings-instrument" />
        <input id="settings-session-end" />
        <input id="settings-history-hours" />
        <input id="settings-flask-port" />
        <input id="settings-zmq-host" />
        <input id="settings-zmq-market" />
        <input id="settings-zmq-cmd" />
        <input id="settings-zmq-query" />
        <input id="settings-zmq-hb" />
        <table><tbody id="settings-accounts-tbody"></tbody></table>
        <div id="settings-accounts-empty"></div>
        <span id="settings-accounts-count"></span>
        <input id="settings-account-name" />
        <input id="settings-account-risk" />
        <input id="settings-account-riskpct" />
        <input id="settings-account-rr" />
        <input id="settings-account-live" type="checkbox" />
        <input id="settings-nt-user" list="settings-nt-user-list" />
        <datalist id="settings-nt-user-list"></datalist>
        <input id="settings-nt-pass" type="password" />
        <button id="settings-nt-toggle-pass"></button>
        <svg id="eye-icon"></svg>
        <svg id="eye-slash-icon"></svg>
        <button id="settings-save-btn"></button>
        <span id="settings-save-status"></span>
        <div><button id="settings-account-add"></button></div>
      </div>

      <div id="ninjatrader-tab" class="tab-content hidden">
        <button id="nt-deploy-btn"></button>
        <div id="nt-deploy-result"></div>
        <div id="nt-detected-dir"></div>
        <input id="nt-creds-user" list="nt-creds-user-list" />
        <datalist id="nt-creds-user-list"></datalist>
        <input id="nt-creds-pass" type="password" />
        <button id="nt-creds-toggle-pass"></button>
        <svg id="nt-eye-icon"></svg>
        <svg id="nt-eye-slash-icon"></svg>
        <button id="nt-save-creds-btn"></button>
        <button id="nt-open-btn"></button>
        <div id="nt-open-result"></div>
        <div id="nt-creds-status"></div>
      </div>

      <div id="metatrader-tab" class="tab-content hidden">
        <input id="mt-path-input" />
        <button id="mt-save-path-btn"></button>
        <button id="mt-launch-btn"></button>
        <div id="mt-launch-result"></div>
        <div id="mt-path-status"></div>
        <button id="mt-deploy-btn"></button>
        <div id="mt-deploy-result"></div>
        <div id="mt-detected-dir"></div>
      </div>

      <div id="logs-tab" class="tab-content hidden">
        <div id="log-entries"></div>
        <select id="log-level-filter"></select>
        <select id="log-source-filter"></select>
        <input id="log-search-filter" />
        <button id="log-clear-btn"></button>
        <button id="log-pause-btn"></button>
        <button id="log-load-more-btn"></button>
        <span id="log-connection-status"></span>
      </div>
    </div>

    <div id="trade-modal" class="hidden">
      <h2 id="modal-trade-title"></h2>
      <div id="modal-trade-details"></div>
      <div id="modal-trade-logs"></div>
      <button id="close-trade-modal"></button>
    </div>
  `;
  return { doc: document, win: window };
}

function buildController(doc, win) {
  const http = new FakeHttpClient();
  http.setResponse('GET', '/api/pair', { pair: 'MNQ' });
  http.setResponse('GET', '/api/admin/stats', {
    total_trades: 10,
    open_trades: 2,
    win_rate: 0.6,
    winning_trades: 6,
    losing_trades: 4,
    total_pnl_usd: 1234.56,
    total_pnl: 12.3,
    avg_pnl_usd: 123.45,
    avg_profit_monthly: 500,
    avg_r_multiple: 1.2,
    max_drawdown: 5.6,
    max_drawdown_pct: 10.5,
    profit_factor: 1.8,
    avg_win: 2.5,
    avg_loss: -1.2,
    expectancy: 0.8,
    max_consecutive_wins: 3,
    max_consecutive_losses: 2,
    current_streak: 2,
    current_streak_type: 'win',
  });
  http.setResponse('GET', '/api/admin/analytics', {
    equity_curve: { labels: ['Jan', 'Feb'], data: [100, 110] },
    result_distribution: { labels: ['TP', 'SL'], data: [5, 3] },
    trades_by_hour: { labels: ['9', '10'], data: [1, 2] },
    trades_by_day: { labels: ['Mon', 'Tue'], data: [2, 3] },
    monthly_pnl: { labels: ['Jan'], data: [100] },
    pnl_distribution: { labels: ['0-10'], data: [2] },
    account_stats: [
      { account: 'A1', total_pnl_usd: 100, total_trades: 5, win_rate: 0.6, winning_trades: 3, losing_trades: 2, avg_pnl_usd: 20 },
      { account: 'A2', total_pnl_usd: -50, total_trades: 3, win_rate: 0.33, winning_trades: 1, losing_trades: 2, avg_pnl_usd: -16.67 },
    ],
  });
  http.setResponse('GET', '/api/admin/trades', { trades: [], total: 0 });
  http.setResponse('GET', '/api/admin/trade-accounts', { accounts: [] });
  http.setResponse('GET', '/api/lines', []);
  http.setResponse('GET', '/api/admin/decisions', { logs: [], events: [] });
  http.setResponse('GET', '/api/settings', {
    trading: { pair: 'MNQ', instrument: '', session_end: '16:58', history_hours: '' },
    network: { flask_port: '5001', zmq_host: '127.0.0.1', zmq_market_port: '5555', zmq_command_port: '5556', zmq_query_port: '5557', zmq_heartbeat_port: '5558' },
    accounts: [],
    credentials: { stored_usernames: [], username: '', password: '' },
  });
  http.setResponse('GET', '/api/accounts', { accounts: [] });
  http.setResponse('GET', '/api/admin/logs/recent', { logs: [], sources: [], has_more: false });
  http.setResponse('POST', '/api/settings', { success: true });
  http.setResponse('POST', '/api/nt/open', { message: 'NinjaTrader opened', success: true });
  http.setResponse('POST', '/api/mt/launch', { message: 'MetaTrader launched', success: true });
  http.setResponse('POST', '/api/nt/deploy', { success: true, message: 'Deployed', copied: [], errors: [] });
  http.setResponse('POST', '/api/mt/deploy', { success: true, message: 'Deployed', copied: [], errors: [] });

  const api = new ApiClient({ httpClient: http });
  api.setPair('MNQ');

  const socket = new FakeSocket();
  const dom = new FakeDomService(doc, win);
  const notification = new FakeNotification();

  const controller = new AdminDashboardController({
    api,
    domService: dom,
    socket,
    notification,
  });

  return { controller, http, socket, dom, notification, api };
}

function flushPromises(ms = 50) {
  return new Promise(r => setTimeout(r, ms));
}

describe('AdminDashboardController', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    window.confirm = vi.fn(() => true);
    window.alert = vi.fn();
    window.Chart = FakeChart;
    HTMLCanvasElement.prototype.getContext = function() {
      return {};
    };
  });

  it('constructor initializes properties and child managers', () => {
    const { doc, win } = setupAdminDocument();
    const { controller, api, dom, socket, notification } = buildController(doc, win);

    expect(controller.api).toBe(api);
    expect(controller.dom).toBe(dom);
    expect(controller.socket).toBe(socket);
    expect(controller.notification).toBe(notification);

    expect(controller.charts).toBeInstanceOf(AnalyticsCharts);
    expect(controller.tradeHistory).toBeInstanceOf(TradeHistory);
    expect(controller.tradeCalendar).toBeInstanceOf(TradeCalendar);
    expect(controller.tradeLogs).toBeInstanceOf(TradeLogs);
    expect(controller.lineManager).toBeInstanceOf(LineManager);
    expect(controller.decisionLogs).toBeInstanceOf(DecisionLogs);
    expect(controller.settingsManager).toBeInstanceOf(SettingsManager);
    expect(controller.ntManager).toBeInstanceOf(NtManager);
    expect(controller.mtManager).toBeInstanceOf(MtManager);
    expect(controller.logPanel).toBeNull();

    expect(controller.currentTab).toBe('overview');
    expect(controller.analyticsData).toBeNull();
    expect(controller.statsData).toBeNull();
    expect(controller.currentTradeView).toBe('table');
  });

  it('init() success path sets up dashboard and switches to initial tab', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http, socket } = buildController(doc, win);
    const pushStateSpy = vi.spyOn(win.history, 'pushState');

    await controller.init();
    await flushPromises();

    expect(doc.getElementById('js-debug').textContent).toBe('JS Loaded ✓');
    expect(doc.getElementById('pairSelector').value).toBe('MNQ');
    expect(controller.currentTab).toBe('overview');
    expect(controller.logPanel).not.toBeNull();

    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/stats'))).toBe(true);
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/analytics'))).toBe(true);
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/settings'))).toBe(true);
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/logs/recent'))).toBe(true);

    expect(pushStateSpy).not.toHaveBeenCalled();
  });

  it('init() error path notifies when api.init fails', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, notification } = buildController(doc, win);

    controller.api.init = async () => { throw new Error('pair fail'); };

    await controller.init();

    expect(notification.alerts).toContain('Failed to initialize admin dashboard. Check console for details.');
  });

  it('setupPairSelector change event updates pair and reloads tab', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    await controller.init();
    await flushPromises();
    http.requests.length = 0;

    const selector = doc.getElementById('pairSelector');
    selector.value = 'MES';
    selector.dispatchEvent(new Event('change', { bubbles: true }));
    await flushPromises();

    expect(controller.api.pair).toBe('MES');
    expect(doc.getElementById('trades-account-filter').value).toBe('');
    expect(http.requests.some(r => r.method === 'GET' && r.url.includes('/api/admin/trade-accounts'))).toBe(true);
  });

  it('setupNavigation click events switch tabs with pushState', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);
    const pushStateSpy = vi.spyOn(win.history, 'pushState');

    await controller.init();
    await flushPromises();

    const tradesLink = Array.from(doc.querySelectorAll('aside nav a')).find(a => a.getAttribute('href') === '/admin/trades');
    tradesLink.click();
    await flushPromises();

    expect(controller.currentTab).toBe('trades');
    expect(pushStateSpy).toHaveBeenCalledWith({ tab: 'trades' }, '', '/admin/trades');
  });

  it('switchTab activates overview tab and loads overview data', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('overview', false);
    await flushPromises();

    expect(controller.currentTab).toBe('overview');
    expect(doc.getElementById('overview-tab').classList.contains('hidden')).toBe(false);
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/stats'))).toBe(true);
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/analytics'))).toBe(true);
  });

  it('switchTab activates trades tab and loads trade data', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('trades', false);
    await flushPromises();

    expect(controller.currentTab).toBe('trades');
    expect(doc.getElementById('trades-tab').classList.contains('hidden')).toBe(false);
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/trades'))).toBe(true);
  });

  it('switchTab activates lines tab and loads lines', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('lines', false);
    await flushPromises();

    expect(controller.currentTab).toBe('lines');
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/lines'))).toBe(true);
  });

  it('switchTab activates analytics tab and loads analytics', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('analytics', false);
    await flushPromises();

    expect(controller.currentTab).toBe('analytics');
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/analytics'))).toBe(true);
  });

  it('switchTab activates decisions tab and loads decision logs', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('decisions', false);
    await flushPromises();

    expect(controller.currentTab).toBe('decisions');
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/decisions'))).toBe(true);
  });

  it('switchTab activates settings tab and loads settings', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    await controller.init();
    await flushPromises();
    http.requests.length = 0;

    controller.switchTab('settings', false);
    await flushPromises();

    expect(controller.currentTab).toBe('settings');
    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/settings'))).toBe(true);
  });

  it('switchTab handles ninjatrader, metatrader, and logs tabs without auto-load', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);
    const beforeCount = http.requests.length;

    controller.switchTab('ninjatrader', false);
    expect(controller.currentTab).toBe('ninjatrader');

    controller.switchTab('metatrader', false);
    expect(controller.currentTab).toBe('metatrader');

    controller.switchTab('logs', false);
    expect(controller.currentTab).toBe('logs');

    expect(http.requests.length).toBe(beforeCount);
  });

  it('setupViewToggle and switchTradeView switch between table and calendar', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);
    await controller.init();
    await flushPromises();

    const calendarBtn = doc.getElementById('view-calendar-btn');
    const tableBtn = doc.getElementById('view-table-btn');

    const historySpy = vi.spyOn(controller.tradeHistory, 'hide').mockImplementation(() => {});
    const calendarSpy = vi.spyOn(controller.tradeCalendar, 'show').mockImplementation(() => {});

    calendarBtn.click();
    expect(controller.currentTradeView).toBe('calendar');
    expect(historySpy).toHaveBeenCalled();
    expect(calendarSpy).toHaveBeenCalled();

    const historyLoadSpy = vi.spyOn(controller.tradeHistory, 'load').mockImplementation(async () => {});
    const calendarHideSpy = vi.spyOn(controller.tradeCalendar, 'hide').mockImplementation(() => {});

    tableBtn.click();
    expect(controller.currentTradeView).toBe('table');
    expect(calendarHideSpy).toHaveBeenCalled();
    expect(historyLoadSpy).toHaveBeenCalled();
  });

  it('loadTradeData loads history or calendar based on current view', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    const historyLoadSpy = vi.spyOn(controller.tradeHistory, 'load').mockImplementation(async () => {});
    const calendarLoadSpy = vi.spyOn(controller.tradeCalendar, 'load').mockImplementation(async () => {});

    controller.currentTradeView = 'table';
    controller.loadTradeData();
    expect(historyLoadSpy).toHaveBeenCalled();
    expect(calendarLoadSpy).not.toHaveBeenCalled();

    historyLoadSpy.mockClear();
    controller.currentTradeView = 'calendar';
    controller.loadTradeData();
    expect(calendarLoadSpy).toHaveBeenCalled();
    expect(historyLoadSpy).not.toHaveBeenCalled();
  });

  it('setupAccountFilter change updates history and calendar accounts', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);
    await controller.init();
    await flushPromises();

    const historySetAccountSpy = vi.spyOn(controller.tradeHistory, 'setAccount').mockImplementation(() => {});
    const calendarSetAccountSpy = vi.spyOn(controller.tradeCalendar, 'setAccount').mockImplementation(() => {});

    const select = doc.getElementById('trades-account-filter');
    select.innerHTML = '<option value="">All Accounts</option><option value="A1">A1</option>';
    select.value = 'A1';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await flushPromises();

    expect(historySetAccountSpy).toHaveBeenCalledWith('A1');
    expect(calendarSetAccountSpy).toHaveBeenCalledWith('A1');
  });

  it('resetAccountFilter clears selected account values', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    const select = doc.getElementById('trades-account-filter');
    select.innerHTML = '<option value="">All Accounts</option><option value="A1">A1</option>';
    select.value = 'A1';
    controller.tradeHistory.selectedAccount = 'A1';
    controller.tradeCalendar.selectedAccount = 'A1';

    controller.resetAccountFilter();

    expect(select.value).toBe('');
    expect(controller.tradeHistory.selectedAccount).toBe('');
    expect(controller.tradeCalendar.selectedAccount).toBe('');
  });

  it('loadTradeAccounts loads accounts through tradeHistory', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    await controller.loadTradeAccounts();

    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/trade-accounts'))).toBe(true);
  });

  it('loadOverviewData success renders stats and charts', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    await controller.loadOverviewData();
    await flushPromises();

    expect(controller.statsData).not.toBeNull();
    expect(controller.analyticsData).not.toBeNull();
    expect(http.requests.filter(r => r.method === 'GET' && r.url.startsWith('/api/admin/stats')).length).toBe(1);
    expect(http.requests.filter(r => r.method === 'GET' && r.url.startsWith('/api/admin/analytics')).length).toBe(1);
  });

  it('loadOverviewData error alerts notification', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, notification, http } = buildController(doc, win);

    http.setResponse('GET', '/api/admin/stats', () => { throw new Error('stats fail'); });

    await controller.loadOverviewData();
    await flushPromises();

    expect(notification.alerts.some(a => a.includes('Failed to load overview data'))).toBe(true);
  });

  it('loadAnalyticsData uses cached analytics when present', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.analyticsData = {
      trades_by_hour: { labels: [], data: [] },
      trades_by_day: { labels: [], data: [] },
      monthly_pnl: { labels: [], data: [] },
      pnl_distribution: { labels: [], data: [] },
      account_stats: [],
    };

    await controller.loadAnalyticsData();
    await flushPromises();

    expect(http.requests.filter(r => r.method === 'GET' && r.url.startsWith('/api/admin/analytics')).length).toBe(0);
  });

  it('renderAccountStats renders account cards', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    controller.renderAccountStats([
      { account: 'A1', total_pnl_usd: 100, total_trades: 5, win_rate: 0.6, winning_trades: 3, losing_trades: 2, avg_pnl_usd: 20 },
      { account: 'A2', total_pnl_usd: -50, total_trades: 3, win_rate: 0.33, winning_trades: 1, losing_trades: 2, avg_pnl_usd: -16.67 },
    ]);

    const container = doc.getElementById('account-stats-grid');
    expect(container.innerHTML).toContain('A1');
    expect(container.innerHTML).toContain('A2');
    expect(container.innerHTML).toContain('+');
    expect(container.innerHTML).toContain('-');
  });

  it('renderAccountStats shows empty message when no data', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    controller.renderAccountStats([]);

    const container = doc.getElementById('account-stats-grid');
    expect(container.innerHTML).toContain('No account data available');

    controller.renderAccountStats(null);
    expect(container.innerHTML).toContain('No account data available');
  });

  it('renderStats renders all stat elements with complete data', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    const stats = {
      total_trades: 10,
      open_trades: 2,
      win_rate: 0.6,
      winning_trades: 6,
      losing_trades: 4,
      total_pnl_usd: 1234.56,
      total_pnl: 12.3,
      avg_pnl_usd: 123.45,
      avg_profit_monthly: 500,
      avg_r_multiple: 1.2,
      max_drawdown: 5.6,
      max_drawdown_pct: 10.5,
      profit_factor: 1.8,
      avg_win: 2.5,
      avg_loss: -1.2,
      expectancy: 0.8,
      max_consecutive_wins: 3,
      max_consecutive_losses: 2,
      current_streak: 2,
      current_streak_type: 'win',
    };

    controller.renderStats(stats);

    expect(doc.getElementById('stat-total-trades').textContent).toBe('10');
    expect(doc.getElementById('stat-open-trades').textContent).toBe('2 open');
    expect(doc.getElementById('stat-win-rate').textContent).toBe('60.0%');
    expect(doc.getElementById('stat-win-loss').textContent).toBe('6W / 4L');
    expect(doc.getElementById('stat-total-pnl-usd').textContent).toBe('+$1234.56');
    expect(doc.getElementById('stat-total-pnl-r').textContent).toBe('R: 12.3');
    expect(doc.getElementById('stat-avg-pnl-usd').textContent).toBe('avg: +$123.45');
    expect(doc.getElementById('stat-avg-profit-monthly').textContent).toBe('+$500.00');
    expect(doc.getElementById('stat-avg-r').textContent).toBe('avg R: 1.20');
    expect(doc.getElementById('stat-max-drawdown').textContent).toBe('5.60R');
    expect(doc.getElementById('stat-max-drawdown-pct').textContent).toBe('10.5% of peak');
    expect(doc.getElementById('stat-profit-factor').textContent).toBe('1.80');
    expect(doc.getElementById('stat-avg-win-loss').textContent).toBe('2.50R / -1.20R');
    expect(doc.getElementById('stat-expectancy').textContent).toBe('+0.80R');
    expect(doc.getElementById('stat-streak').textContent).toBe('3W / 2L');
    expect(doc.getElementById('stat-streak-detail').innerHTML).toContain('Current: <span class="text-emerald-400">2 wins</span>');
  });

  it('renderStats handles null and missing fields', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    controller.renderStats(null);
    controller.renderStats({
      total_trades: null,
      open_trades: undefined,
      win_rate: null,
      winning_trades: null,
      losing_trades: null,
      total_pnl_usd: null,
      total_pnl: null,
      avg_pnl_usd: null,
      avg_profit_monthly: null,
      avg_r_multiple: null,
      max_drawdown: null,
      max_drawdown_pct: null,
      profit_factor: null,
      avg_win: undefined,
      avg_loss: undefined,
      expectancy: null,
      max_consecutive_wins: undefined,
      max_consecutive_losses: undefined,
      current_streak: undefined,
      current_streak_type: undefined,
    });

    expect(doc.getElementById('stat-total-trades').textContent).toBe('-');
    expect(doc.getElementById('stat-open-trades').textContent).toBe('- open');
    expect(doc.getElementById('stat-win-rate').textContent).toBe('-');
    expect(doc.getElementById('stat-total-pnl-usd').textContent).toBe('-');
    expect(doc.getElementById('stat-avg-profit-monthly').textContent).toBe('-');
    expect(doc.getElementById('stat-profit-factor').textContent).toBe('-');
    expect(doc.getElementById('stat-streak').textContent).toBe('-');
    expect(doc.getElementById('stat-streak-detail').textContent).toBe('-');
  });

  it('renderStats logs error when total-trades element is missing', () => {
    const { doc, win } = setupAdminDocument();
    doc.getElementById('stat-total-trades').remove();
    const { controller } = buildController(doc, win);
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    controller.renderStats({ total_trades: 5 });

    expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining('stat-total-trades element not found'));
    errorSpy.mockRestore();
  });

  it('switchTab updates active nav link and pushes state', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);
    const pushStateSpy = vi.spyOn(win.history, 'pushState');

    controller.switchTab('trades');

    const tradesLink = Array.from(doc.querySelectorAll('aside nav a')).find(a => a.getAttribute('href') === '/admin/trades');
    expect(tradesLink.classList.contains('active')).toBe(true);
    expect(pushStateSpy).toHaveBeenCalledWith({ tab: 'trades' }, '', '/admin/trades');
  });

  it('popstate handler switches tab from URL', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    await controller.init();
    await flushPromises();

    win.location.href = 'http://localhost/admin/lines';
    win.dispatchEvent(new Event('popstate'));
    await flushPromises();

    expect(controller.currentTab).toBe('lines');
  });

  it('tradeHistory and tradeCalendar click callbacks open trade logs', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);
    await controller.init();
    await flushPromises();

    const showSpy = vi.spyOn(controller.tradeLogs, 'show').mockImplementation(async () => {});

    controller.tradeHistory.onTradeClick('trade-1');
    expect(showSpy).toHaveBeenCalledWith('trade-1');

    controller.tradeCalendar.onTradeClick('trade-2');
    expect(showSpy).toHaveBeenCalledWith('trade-2');
  });

  it('loadAnalyticsData error path logs error without notification', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, notification } = buildController(doc, win);

    controller.analyticsData = null;
    controller.api.getAnalytics = async () => { throw new Error('analytics fail'); };

    await controller.loadAnalyticsData();

    expect(notification.alerts.length).toBe(0);
  });

  it('renderStats covers remaining conditional branches', () => {
    const { doc, win } = setupAdminDocument();
    const { controller } = buildController(doc, win);

    controller.renderStats({
      total_trades: 1,
      win_rate: 0.4,
      winning_trades: 2,
      losing_trades: 3,
      total_pnl_usd: -100,
      total_pnl: -1,
      avg_pnl_usd: -50,
      avg_profit_monthly: -100,
      avg_r_multiple: -0.5,
      max_drawdown: 1,
      max_drawdown_pct: 5,
      profit_factor: 1.2,
      avg_win: 1,
      avg_loss: -1,
      expectancy: -0.25,
      max_consecutive_wins: 1,
      max_consecutive_losses: 1,
      current_streak: 1,
      current_streak_type: 'loss',
    });

    expect(doc.getElementById('stat-total-pnl-usd').className).toContain('text-rose-500');
    expect(doc.getElementById('stat-avg-profit-monthly').className).toContain('text-rose-500');
    expect(doc.getElementById('stat-profit-factor').className).toContain('text-amber-400');
    expect(doc.getElementById('stat-expectancy').textContent).toBe('-0.25R');
    expect(doc.getElementById('stat-expectancy').className).toContain('text-rose-500');
    expect(doc.getElementById('stat-streak-detail').innerHTML).toContain('1 loss');
  });

  it('loads trades endpoint when switching to trades tab', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('trades');
    await flushPromises();

    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/trades'))).toBe(true);
  });

  it('loads lines endpoint when switching to lines tab', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('lines');
    await flushPromises();

    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/lines'))).toBe(true);
  });

  it('loads decisions endpoint when switching to decisions tab', async () => {
    const { doc, win } = setupAdminDocument();
    const { controller, http } = buildController(doc, win);

    controller.switchTab('decisions');
    await flushPromises();

    expect(http.requests.some(r => r.method === 'GET' && r.url.startsWith('/api/admin/decisions'))).toBe(true);
  });
});
