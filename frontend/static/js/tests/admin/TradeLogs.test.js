import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { TradeLogs } from '../../admin/TradeLogs.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <div id="trade-modal" class="hidden">
      <div id="modal-trade-title"></div>
      <div id="modal-trade-details"></div>
      <div id="modal-trade-logs"></div>
      <button id="close-trade-modal"></button>
    </div>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  const api = new ApiClient({ httpClient: http });
  return { http, api };
}

function sampleTrade(overrides = {}) {
  return {
    trade_id: 'T1',
    result_type: 'TP',
    status: 'closed',
    type: 'long',
    entry: 4500,
    stop_loss: 4490,
    take_profit: 4600,
    risk_dollars: 100,
    pnl_usd: 200,
    result: 2,
    entry_time: 1700000000,
    exit_time: 1700000100,
    contracts: 2,
    logs: [
      { event: 'FILL', ts: 1700000000000, msg: 'filled' },
      { event: 'ERROR', ts: 1700000100000, msg: 'error' },
    ],
    ...overrides,
  };
}

describe('TradeLogs', () => {
  let alertSpy;

  beforeEach(() => {
    setupDom();
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
  });

  afterEach(() => {
    alertSpy.mockRestore();
  });

  it('show loads trade and renders modal', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades/T1', sampleTrade());

    const logs = new TradeLogs(api);
    await logs.show('T1');

    expect(document.getElementById('trade-modal').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('modal-trade-title').textContent).toContain('T1');
    expect(document.getElementById('modal-trade-details').textContent).toContain('4500.00');
  });

  it('show alerts on error', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades/T1', () => { throw new Error('boom'); });

    const logs = new TradeLogs(api);
    await logs.show('T1');

    expect(alertSpy).toHaveBeenCalledWith('Failed to load trade details');
  });

  it('close button hides modal', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades/T1', sampleTrade());

    const logs = new TradeLogs(api);
    await logs.show('T1');

    document.getElementById('close-trade-modal').click();
    expect(document.getElementById('trade-modal').classList.contains('hidden')).toBe(true);
  });

  it('backdrop click hides modal', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/trades/T1', sampleTrade());

    const logs = new TradeLogs(api);
    await logs.show('T1');

    document.getElementById('trade-modal').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(document.getElementById('trade-modal').classList.contains('hidden')).toBe(true);
  });

  it('render handles open trade', () => {
    const { api } = buildApi();
    const logs = new TradeLogs(api);
    logs.render(sampleTrade({ result_type: null, status: 'open', pnl_usd: null, result: null }));

    expect(document.getElementById('modal-trade-title').textContent).toContain('Open');
  });

  it('render handles missing pnl_usd', () => {
    const { api } = buildApi();
    const logs = new TradeLogs(api);
    logs.render(sampleTrade({ pnl_usd: null, result: 1.5 }));

    expect(document.getElementById('modal-trade-details').textContent).toContain('1.50R');
  });

  it('render shows no logs message', () => {
    const { api } = buildApi();
    const logs = new TradeLogs(api);
    logs.render(sampleTrade({ logs: [] }));

    expect(document.getElementById('modal-trade-logs').textContent).toContain('No logs available');
  });

  it('getEventBorderClass classifies events', () => {
    const { api } = buildApi();
    const logs = new TradeLogs(api);
    expect(logs.getEventBorderClass('ERROR')).toContain('rose');
    expect(logs.getEventBorderClass('FILL')).toContain('emerald');
    expect(logs.getEventBorderClass('WARNING')).toContain('amber');
    expect(logs.getEventBorderClass('OTHER')).toContain('surface');
  });

  it('getResultBadgeBackgroundClass returns colors', () => {
    const { api } = buildApi();
    const logs = new TradeLogs(api);
    expect(logs.getResultBadgeBackgroundClass('TP')).toContain('emerald');
    expect(logs.getResultBadgeBackgroundClass('SL')).toContain('rose');
    expect(logs.getResultBadgeBackgroundClass('BE')).toContain('amber');
    expect(logs.getResultBadgeBackgroundClass('SP')).toContain('accent');
    expect(logs.getResultBadgeBackgroundClass('CLOSE')).toContain('slate');
    expect(logs.getResultBadgeBackgroundClass('unknown')).toContain('slate');
  });

  it('formatDateTime handles null timestamp', () => {
    const { api } = buildApi();
    const logs = new TradeLogs(api);
    expect(logs.formatDateTime(null)).toBe('-');
  });
});
