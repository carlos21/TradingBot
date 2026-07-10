import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { TradeHistory } from '../../admin/TradeHistory.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <select id="trades-account-filter"></select>
    <table><tbody id="trades-tbody"></tbody></table>
    <span id="trades-pagination-info"></span>
    <button id="trades-prev-btn"></button>
    <button id="trades-next-btn"></button>
    <button id="view-table-btn"></button>
    <button id="view-calendar-btn"></button>
    <div id="trades-table-view"></div>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  const api = new ApiClient({ httpClient: http });
  api.setPair('MNQ');
  return { http, api };
}

function sampleTrade(overrides = {}) {
  return {
    trade_id: 'T1',
    type: 'long',
    account: 'main',
    entry: 4500,
    exit_price: 4550,
    result: 2,
    pnl_usd: 200,
    risk_pct: 1,
    result_type: 'TP',
    status: 'closed',
    entry_time: 1700000000,
    ...overrides,
  };
}

describe('TradeHistory', () => {
  let alertSpy;
  let confirmSpy;

  beforeEach(() => {
    setupDom();
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true);
  });

  afterEach(() => {
    alertSpy.mockRestore();
    confirmSpy.mockRestore();
  });

  it('loads and renders empty state', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', { trades: [], total: 0 });
    const history = new TradeHistory(api);
    await history.load();

    const tbody = document.getElementById('trades-tbody');
    expect(tbody.textContent).toContain('No trades found');
  });

  it('loads and renders trades with pagination info', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    const tbody = document.getElementById('trades-tbody');
    expect(tbody.textContent).toContain('LONG');
    expect(document.getElementById('trades-pagination-info').textContent).toBe('Showing 1-1 of 1 trades');
  });

  it('alerts on load error', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', () => { throw new Error('boom'); });

    const history = new TradeHistory(api);
    await history.load();

    expect(alertSpy).toHaveBeenCalledWith('Failed to load trades: boom');
  });

  it('setAccount resets offset and reloads', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', { trades: [], total: 0 });
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0&account=acc2', { trades: [], total: 0 });

    const history = new TradeHistory(api);
    history.offset = 50;
    await history.setAccount('acc2');

    expect(history.selectedAccount).toBe('acc2');
    expect(history.offset).toBe(0);
  });

  it('loadAccounts populates filter and preserves selection', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trade-accounts?pair=MNQ', { accounts: ['acc1', 'acc2'] });

    const history = new TradeHistory(api);
    history.selectedAccount = 'acc2';
    await history.loadAccounts();

    const select = document.getElementById('trades-account-filter');
    expect(select.options.length).toBe(3);
    expect(select.value).toBe('acc2');
  });

  it('loadAccounts resets value if selection invalid', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trade-accounts?pair=MNQ', { accounts: ['acc1'] });

    const history = new TradeHistory(api);
    history.selectedAccount = 'acc2';
    await history.loadAccounts();

    const select = document.getElementById('trades-account-filter');
    expect(select.value).toBe('');
  });

  it('clicking a trade row invokes onTradeClick', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const history = new TradeHistory(api);
    const clickHandler = vi.fn();
    history.onTradeClick = clickHandler;
    await history.load();

    const row = document.querySelector('tr[data-trade-id="T1"]');
    row.click();

    expect(clickHandler).toHaveBeenCalledWith('T1');
  });

  it('view logs button invokes onTradeClick', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const history = new TradeHistory(api);
    const clickHandler = vi.fn();
    history.onTradeClick = clickHandler;
    await history.load();

    const btn = document.querySelector('.view-logs-btn');
    btn.click();

    expect(clickHandler).toHaveBeenCalledWith('T1');
  });

  it('delete button deletes trade after confirm', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });
    http.setResponse('DELETE', '/api/admin/trades/T1', { deleted: true });

    const history = new TradeHistory(api);
    await history.load();

    const btn = document.querySelector('.delete-trade-btn');
    btn.click();

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Delete trade T1'));
    expect(http.requests.some(r => r.method === 'DELETE' && r.url === '/api/admin/trades/T1')).toBe(true);
  });

  it('cancelled delete does not call api', async () => {
    confirmSpy.mockReturnValue(false);
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    document.querySelector('.delete-trade-btn').click();

    expect(http.requests.filter(r => r.method === 'DELETE').length).toBe(0);
  });

  it('pagination next increments offset', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 60,
    });
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=50', {
      trades: [],
      total: 60,
    });

    const history = new TradeHistory(api);
    await history.load();

    document.getElementById('trades-next-btn').click();
    expect(history.offset).toBe(50);
  });

  it('pagination prev decrements offset', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=50', {
      trades: [],
      total: 60,
    });
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 60,
    });

    const history = new TradeHistory(api);
    history.offset = 50;
    await history.load();

    document.getElementById('trades-prev-btn').click();
    expect(history.offset).toBe(0);
  });

  it('show toggles classes and loads', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', { trades: [], total: 0 });

    const history = new TradeHistory(api);
    history.show();

    expect(document.getElementById('trades-table-view').classList.contains('hidden')).toBe(false);
  });

  it('hide toggles classes', () => {
    const { api } = buildApi();
    const history = new TradeHistory(api);
    history.hide();

    expect(document.getElementById('trades-table-view').classList.contains('hidden')).toBe(true);
  });

  it('result labels and badge classes handle variants', () => {
    const { api } = buildApi();
    const history = new TradeHistory(api);

    expect(history.getResultLabel({ result_type: 'TP', status: 'closed' })).toBe('TP');
    expect(history.getResultLabel({ status: 'open' })).toBe('Open');
    expect(history.getResultBadgeClass('TP')).toContain('emerald');
    expect(history.getResultBadgeClass('SL')).toContain('rose');
    expect(history.getResultBadgeClass('BE')).toContain('amber');
    expect(history.getResultBadgeClass('SP')).toContain('accent');
    expect(history.getResultBadgeClass('CLOSE')).toContain('slate');
    expect(history.getResultBadgeClass('Open')).toContain('slate');
    expect(history.getResultBadgeClass('unknown')).toContain('slate');
  });

  it('formatTime returns formatted date or dash', () => {
    const { api } = buildApi();
    const history = new TradeHistory(api);
    expect(history.formatTime(null)).toBe('-');
    expect(history.formatTime(1700000000)).toContain('Nov');
  });

  it('renders exact percentage from pnl_usd and account_balance', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade({ result: 5, pnl_usd: 4050, account_balance: 50625, risk_pct: 1.6 })],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    const tbody = document.getElementById('trades-tbody');
    expect(tbody.textContent).toContain('+8.00%');
  });

  it('falls back to result * risk_pct when account_balance is missing', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade({ result: 5, pnl_usd: 4050, account_balance: null, risk_pct: 1.6 })],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    const tbody = document.getElementById('trades-tbody');
    expect(tbody.textContent).toContain('+8.00%');
  });

  it('renders negative exact percentage for losses', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade({ result: -1, pnl_usd: -810, account_balance: 50625, risk_pct: 1.6, result_type: 'SL' })],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    const tbody = document.getElementById('trades-tbody');
    expect(tbody.textContent).toContain('-1.60%');
  });
});
