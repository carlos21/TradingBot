import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { TradeHistory } from '../../admin/TradeHistory.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <select id="trades-account-filter"></select>
    <table><thead><tr><th><input type="checkbox" id="select-all-trades"></th></tr></thead><tbody id="trades-tbody"></tbody></table>
    <span id="trades-pagination-info"></span>
    <button id="trades-prev-btn"></button>
    <button id="trades-next-btn"></button>
    <button id="view-table-btn"></button>
    <button id="view-calendar-btn"></button>
    <div id="trades-table-view"></div>
    <button id="bulk-delete-trades-btn"></button>
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

  it('renders a checkbox for each trade row', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    expect(document.querySelectorAll('.trade-select-checkbox').length).toBe(1);
  });

  it('selecting a trade checkbox adds it to selection and updates button', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const history = new TradeHistory(api);
    await history.load();

    const checkbox = document.querySelector('.trade-select-checkbox');
    checkbox.click();

    expect(history.selectedTradeIds.has('T1')).toBe(true);
    const btn = document.getElementById('bulk-delete-trades-btn');
    expect(btn.textContent).toBe('Delete Selected (1)');
    expect(btn.disabled).toBe(false);
  });

  it('select all checkbox selects and deselects visible trades', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade({ trade_id: 'T1' }), sampleTrade({ trade_id: 'T2' })],
      total: 2,
    });

    const history = new TradeHistory(api);
    await history.load();

    document.getElementById('select-all-trades').click();

    expect(history.selectedTradeIds.has('T1')).toBe(true);
    expect(history.selectedTradeIds.has('T2')).toBe(true);

    document.getElementById('select-all-trades').click();

    expect(history.selectedTradeIds.has('T1')).toBe(false);
    expect(history.selectedTradeIds.has('T2')).toBe(false);
  });

  it('bulk delete button calls api with selected ids after confirm', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade({ trade_id: 'T1' }), sampleTrade({ trade_id: 'T2' })],
      total: 2,
    });

    const history = new TradeHistory(api);
    await history.load();

    document.querySelectorAll('.trade-select-checkbox').forEach(cb => cb.click());

    http.setResponse('POST', '/api/admin/trades/bulk-delete', { deleted: true });
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [],
      total: 0,
    });

    document.getElementById('bulk-delete-trades-btn').click();

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('Delete 2 trades'));
    const bulkRequest = http.requests.find(r => r.method === 'POST' && r.url === '/api/admin/trades/bulk-delete');
    expect(bulkRequest).toBeDefined();
    expect(bulkRequest.data).toEqual({ trade_ids: ['T1', 'T2'] });
  });

  it('cancelled bulk delete does not call api', async () => {
    confirmSpy.mockReturnValue(false);
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', {
      trades: [sampleTrade({ trade_id: 'T1' }), sampleTrade({ trade_id: 'T2' })],
      total: 2,
    });

    const history = new TradeHistory(api);
    await history.load();

    document.querySelectorAll('.trade-select-checkbox').forEach(cb => cb.click());
    document.getElementById('bulk-delete-trades-btn').click();

    expect(http.requests.filter(r => r.method === 'POST').length).toBe(0);
  });
});
