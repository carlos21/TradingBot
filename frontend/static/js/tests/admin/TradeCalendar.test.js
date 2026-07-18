import { describe, it, expect, beforeEach, vi } from 'vitest';
import { TradeCalendar } from '../../admin/TradeCalendar.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <div id="trades-table-view"></div>
    <div id="trades-calendar-view">
      <div id="cal-month-pills"></div>
      <div id="calendar-container"></div>
    </div>
    <button id="view-table-btn"></button>
    <button id="view-calendar-btn"></button>
    <button id="cal-prev-btn"></button>
    <button id="cal-next-btn"></button>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  const api = new ApiClient({ httpClient: http });
  return { http, api };
}

// Tuesday Jan 16 2024 12:00 UTC (weekday in any common US timezone)
const JAN_WEEKDAY = 1705404000;
// Thursday Feb 15 2024 12:00 UTC
const FEB_WEEKDAY = 1707998400;

function sampleTrade(overrides = {}) {
  return {
    trade_id: 'T1',
    entry_time: JAN_WEEKDAY,
    result: 1.2,
    result_type: 'TP',
    pnl_usd: 120,
    ...overrides,
  };
}

describe('TradeCalendar', () => {
  beforeEach(() => {
    setupDom();
  });

  it('loads and renders empty calendar', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', { trades: [], total: 0 });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    await calendar.load();

    expect(document.getElementById('calendar-container').textContent).toContain('No trades found');
    expect(document.getElementById('cal-month-pills').textContent).toContain('No trades');
  });

  it('groups trades by month and renders pills', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    await calendar.load();

    expect(document.getElementById('cal-month-pills').querySelectorAll('button').length).toBe(1);
    expect(document.getElementById('calendar-container').textContent).toContain('January 2024');
  });

  it('clicking a month pill switches month', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', {
      trades: [
        sampleTrade({ trade_id: 'T1', entry_time: JAN_WEEKDAY }),
        sampleTrade({ trade_id: 'T2', entry_time: FEB_WEEKDAY }),
      ],
      total: 2,
    });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    await calendar.load();

    const pills = document.getElementById('cal-month-pills').querySelectorAll('button');
    expect(calendar.currentMonthIndex).toBe(1);
    pills[0].click();
    expect(calendar.currentMonthIndex).toBe(0);
  });

  it('setAccount reloads with account filter', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', { trades: [], total: 0 });
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0&account=acc2', { trades: [], total: 0 });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    await calendar.setAccount('acc2');

    expect(calendar.selectedAccount).toBe('acc2');
  });

  it('navigation buttons update month index', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', {
      trades: [
        sampleTrade({ entry_time: JAN_WEEKDAY }),
        sampleTrade({ entry_time: FEB_WEEKDAY }),
      ],
      total: 2,
    });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    await calendar.load();

    document.getElementById('cal-prev-btn').click();
    expect(calendar.currentMonthIndex).toBe(0);

    document.getElementById('cal-next-btn').click();
    expect(calendar.currentMonthIndex).toBe(1);
  });

  it('clicking a trade cell invokes onTradeClick', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', {
      trades: [sampleTrade()],
      total: 1,
    });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    const handler = vi.fn();
    calendar.onTradeClick = handler;
    await calendar.load();

    document.querySelector('.cal-trade').click();
    expect(handler).toHaveBeenCalledWith('T1');
  });

  it('show toggles views and loads data', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', { trades: [], total: 0 });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    calendar.show();

    expect(document.getElementById('trades-calendar-view').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('trades-table-view').classList.contains('hidden')).toBe(true);
  });

  it('hide toggles views', () => {
    const { api } = buildApi();
    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    calendar.hide();

    expect(document.getElementById('trades-calendar-view').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('trades-table-view').classList.contains('hidden')).toBe(false);
  });

  it('stats categorize results', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10000&offset=0', {
      trades: [
        sampleTrade({ result: 2, result_type: 'TP', pnl_usd: 200 }),
        sampleTrade({ result: -1.5, result_type: 'SL', pnl_usd: -150 }),
        sampleTrade({ result: 0.1, result_type: 'BE', pnl_usd: 10 }),
        sampleTrade({ result: 0.8, result_type: 'SP', pnl_usd: 80 }),
      ],
      total: 4,
    });

    const calendar = new TradeCalendar(api);
    calendar.setPair('MNQ');
    await calendar.load();

    const month = calendar.months[0];
    expect(month.stats.wins).toBe(1);
    expect(month.stats.losses).toBe(1);
    expect(month.stats.be).toBe(1);
    expect(month.stats.sp).toBe(1);
    expect(month.stats.totalPnl).toBe(140);
  });
});
