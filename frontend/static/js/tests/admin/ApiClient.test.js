import { describe, it, expect, beforeEach } from 'vitest';
import { ApiClient } from '../../admin/ApiClient.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';

describe('ApiClient', () => {
  let http;
  let api;

  beforeEach(() => {
    http = new FakeHttpClient();
    api = new ApiClient({ httpClient: http });
    api.setPair('MNQ');
  });

  describe('init', () => {
    it('fetches pair and stores it', async () => {
      http.setResponse('GET', '/api/pair', { pair: 'ES' });
      const pair = await api.init();
      expect(pair).toBe('ES');
      expect(api.pair).toBe('ES');
      expect(api.defaultPair).toBe('ES');
    });
  });

  describe('setPair', () => {
    it('updates the active pair', () => {
      api.setPair('NQ');
      expect(api.pair).toBe('NQ');
    });
  });

  describe('low-level HTTP', () => {
    it('performs GET requests', async () => {
      http.setResponse('GET', '/foo', { ok: true });
      const result = await api.get('/foo');
      expect(result).toEqual({ ok: true });
      expect(http.requests).toContainEqual({ method: 'GET', url: '/foo', data: undefined });
    });

    it('performs POST requests', async () => {
      http.setResponse('POST', '/foo', { id: 1 });
      const result = await api.post('/foo', { name: 'test' });
      expect(result).toEqual({ id: 1 });
      expect(http.requests).toContainEqual({ method: 'POST', url: '/foo', data: { name: 'test' } });
    });

    it('performs PUT requests', async () => {
      http.setResponse('PUT', '/foo', { updated: true });
      const result = await api.put('/foo', { name: 'test' });
      expect(result).toEqual({ updated: true });
    });

    it('performs DELETE requests', async () => {
      http.setResponse('DELETE', '/foo', { deleted: true });
      const result = await api.delete('/foo');
      expect(result).toEqual({ deleted: true });
    });
  });

  describe('settings', () => {
    it('getSettings calls /api/settings', async () => {
      http.setResponse('GET', '/api/settings', { pair: 'MNQ' });
      const result = await api.getSettings();
      expect(result).toEqual({ pair: 'MNQ' });
    });

    it('saveSettings calls /api/settings with payload', async () => {
      http.setResponse('POST', '/api/settings', { saved: true });
      const payload = { trading: {} };
      const result = await api.saveSettings(payload);
      expect(result).toEqual({ saved: true });
      expect(http.requests).toContainEqual({ method: 'POST', url: '/api/settings', data: payload });
    });
  });

  describe('accounts', () => {
    it('getAccounts calls /api/accounts', async () => {
      http.setResponse('GET', '/api/accounts', { accounts: [] });
      const result = await api.getAccounts();
      expect(result).toEqual({ accounts: [] });
    });

    it('saveAccount posts to /api/accounts', async () => {
      http.setResponse('POST', '/api/accounts', { saved: true });
      const data = { name: 'acc' };
      const result = await api.saveAccount(data);
      expect(result).toEqual({ saved: true });
      expect(http.requests).toContainEqual({ method: 'POST', url: '/api/accounts', data });
    });

    it('deleteAccount encodes the name', async () => {
      http.setResponse('DELETE', '/api/accounts/foo%20bar', { deleted: true });
      const result = await api.deleteAccount('foo bar');
      expect(result).toEqual({ deleted: true });
    });
  });

  describe('ninjatrader', () => {
    it('installNtNetmq posts empty payload', async () => {
      http.setResponse('POST', '/api/nt/install-netmq', { ok: true });
      const result = await api.installNtNetmq();
      expect(result).toEqual({ ok: true });
      expect(http.requests).toContainEqual({ method: 'POST', url: '/api/nt/install-netmq', data: {} });
    });

    it('openNt posts credentials', async () => {
      http.setResponse('POST', '/api/nt/open', { success: true });
      const result = await api.openNt('user', 'pass');
      expect(result).toEqual({ success: true });
      expect(http.requests).toContainEqual({
        method: 'POST',
        url: '/api/nt/open',
        data: { username: 'user', password: 'pass' },
      });
    });
  });

  describe('metatrader', () => {
    it('launchMt posts exe path', async () => {
      http.setResponse('POST', '/api/mt/launch', { success: true });
      const result = await api.launchMt('/path/to/terminal.exe');
      expect(result).toEqual({ success: true });
      expect(http.requests).toContainEqual({
        method: 'POST',
        url: '/api/mt/launch',
        data: { exe_path: '/path/to/terminal.exe' },
      });
    });

    it('deployMt posts empty payload', async () => {
      http.setResponse('POST', '/api/mt/deploy', { success: true });
      const result = await api.deployMt();
      expect(result).toEqual({ success: true });
    });
  });

  describe('deploy', () => {
    it('deployNt posts empty payload', async () => {
      http.setResponse('POST', '/api/nt/deploy', { success: true });
      const result = await api.deployNt();
      expect(result).toEqual({ success: true });
    });
  });

  describe('admin stats and trades', () => {
    it('getStats includes pair query', async () => {
      http.setResponse('GET', '/api/admin/stats?pair=MNQ', { stats: {} });
      const result = await api.getStats();
      expect(result).toEqual({ stats: {} });
    });

    it('getTrades builds query with defaults', async () => {
      http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=50&offset=0', { trades: [], total: 0 });
      const result = await api.getTrades();
      expect(result).toEqual({ trades: [], total: 0 });
    });

    it('getTrades builds query with account filter', async () => {
      http.setResponse('GET', '/api/admin/trades?pair=MNQ&limit=10&offset=20&account=foo%20bar', { trades: [], total: 0 });
      const result = await api.getTrades(10, 20, 'foo bar');
      expect(result).toEqual({ trades: [], total: 0 });
    });

    it('getTradeAccounts includes pair query', async () => {
      http.setResponse('GET', '/api/admin/trade-accounts?pair=MNQ', { accounts: [] });
      const result = await api.getTradeAccounts();
      expect(result).toEqual({ accounts: [] });
    });

    it('getTradeDetail uses trade id', async () => {
      http.setResponse('GET', '/api/admin/trades/123', { trade_id: '123' });
      const result = await api.getTradeDetail('123');
      expect(result).toEqual({ trade_id: '123' });
    });

    it('deleteTrade uses trade id', async () => {
      http.setResponse('DELETE', '/api/admin/trades/123', { deleted: true });
      const result = await api.deleteTrade('123');
      expect(result).toEqual({ deleted: true });
    });

    it('getAnalytics includes pair query', async () => {
      http.setResponse('GET', '/api/admin/analytics?pair=MNQ', { data: [] });
      const result = await api.getAnalytics();
      expect(result).toEqual({ data: [] });
    });
  });

  describe('lines', () => {
    it('getLines includes pair query', async () => {
      http.setResponse('GET', '/api/lines?pair=MNQ', []);
      const result = await api.getLines();
      expect(result).toEqual([]);
    });

    it('addLine posts pair and price', async () => {
      http.setResponse('POST', '/api/lines', { id: 1 });
      const result = await api.addLine(4500);
      expect(result).toEqual({ id: 1 });
      expect(http.requests).toContainEqual({
        method: 'POST',
        url: '/api/lines',
        data: { pair: 'MNQ', price: 4500 },
      });
    });

    it('updateLine puts price', async () => {
      http.setResponse('PUT', '/api/lines/1', { updated: true });
      const result = await api.updateLine('1', 4600);
      expect(result).toEqual({ updated: true });
      expect(http.requests).toContainEqual({ method: 'PUT', url: '/api/lines/1', data: { price: 4600 } });
    });

    it('deleteLine removes line', async () => {
      http.setResponse('DELETE', '/api/lines/1', { deleted: true });
      const result = await api.deleteLine('1');
      expect(result).toEqual({ deleted: true });
    });
  });

  describe('decisions', () => {
    it('getDecisionLogs builds query with filters', async () => {
      http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=100&event=ENTRY&line_id=abc', { logs: [] });
      const result = await api.getDecisionLogs('ENTRY', 'abc', 100);
      expect(result).toEqual({ logs: [] });
    });

    it('getDecisionEvents calls endpoint without pair', async () => {
      http.setResponse('GET', '/api/admin/decisions/events', { events: [] });
      const result = await api.getDecisionEvents();
      expect(result).toEqual({ events: [] });
    });
  });

  describe('logs', () => {
    it('getRecentLogs builds query', async () => {
      http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=100&offset=10', { logs: [] });
      const result = await api.getRecentLogs(100, 10);
      expect(result).toEqual({ logs: [] });
    });
  });
});
