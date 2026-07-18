import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { DecisionLogs } from '../../admin/DecisionLogs.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <select id="decisions-event-filter">
      <option value="">All Events</option>
      <option value="ENTRY">ENTRY</option>
    </select>
    <input id="decisions-line-id-filter" />
    <select id="decisions-limit-filter">
      <option value="500">500</option>
      <option value="100">100</option>
    </select>
    <button id="refresh-decisions-btn"></button>
    <table><tbody id="decisions-tbody"></tbody></table>
    <div id="decisions-empty" class="hidden"></div>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  const api = new ApiClient({ httpClient: http });
  return { http, api };
}

describe('DecisionLogs', () => {
  beforeEach(() => {
    setupDom();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads and renders logs', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500', {
      logs: [
        {
          event: 'ENTRY',
          bar_time: 1700000000,
          line_id: 'L1',
          direction: 'long',
          trigger_name: 't1',
          filter_name: 'f1',
          reason: 'r1',
          details: 'd1',
        },
      ],
      events: ['ENTRY'],
    });

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    await logs.load();

    const tbody = document.getElementById('decisions-tbody');
    expect(tbody.textContent).toContain('ENTRY');
    expect(tbody.textContent).toContain('L1');
  });

  it('renders empty message when no logs', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500', { logs: [], events: [] });

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    await logs.load();

    expect(document.getElementById('decisions-empty').classList.contains('hidden')).toBe(false);
  });

  it('uses filter values in request', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=100&event=ENTRY&line_id=abc', {
      logs: [],
      events: [],
    });

    document.getElementById('decisions-event-filter').value = 'ENTRY';
    document.getElementById('decisions-line-id-filter').value = 'abc';
    document.getElementById('decisions-limit-filter').value = '100';

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    await logs.load();

    expect(http.requests.some(r =>
      r.method === 'GET' && r.url === '/api/admin/decisions?pair=MNQ&limit=100&event=ENTRY&line_id=abc'
    )).toBe(true);
  });

  it('renderFilters rebuilds event options and restores value', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500&event=ENTRY', {
      logs: [],
      events: ['ENTRY', 'FILTER_BLOCK'],
    });

    document.getElementById('decisions-event-filter').value = 'ENTRY';

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    await logs.load();

    const select = document.getElementById('decisions-event-filter');
    expect(select.options.length).toBe(3);
    expect(select.value).toBe('ENTRY');
  });

  it('bindFilters reloads on event change', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500', { logs: [], events: [] });
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500&event=ENTRY', { logs: [], events: [] });

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    logs.bindFilters();

    document.getElementById('decisions-event-filter').value = 'ENTRY';
    document.getElementById('decisions-event-filter').dispatchEvent(new Event('change', { bubbles: true }));

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests.some(r => r.url === '/api/admin/decisions?pair=MNQ&limit=500&event=ENTRY')).toBe(true);
  });

  it('bindFilters reloads on line id input after debounce', async () => {
    vi.useFakeTimers();
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500', { logs: [], events: [] });
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500&line_id=L1', { logs: [], events: [] });

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    logs.bindFilters();

    const input = document.getElementById('decisions-line-id-filter');
    input.value = 'L1';
    input.dispatchEvent(new Event('input', { bubbles: true }));

    vi.advanceTimersByTime(350);

    expect(http.requests.some(r => r.url === '/api/admin/decisions?pair=MNQ&limit=500&line_id=L1')).toBe(true);
  });

  it('bindFilters reloads on refresh click', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/decisions?pair=MNQ&limit=500', { logs: [], events: [] });

    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    logs.bindFilters();

    document.getElementById('refresh-decisions-btn').click();

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests.filter(r => r.method === 'GET' && r.url.startsWith('/api/admin/decisions')).length).toBeGreaterThanOrEqual(1);
  });

  it('getEventBadgeClass returns expected classes', () => {
    const { api } = buildApi();
    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');

    expect(logs.getEventBadgeClass('ENTRY')).toContain('emerald');
    expect(logs.getEventBadgeClass('FILTER_BLOCK')).toContain('rose');
    expect(logs.getEventBadgeClass('TRIGGER_SKIP')).toContain('amber');
    expect(logs.getEventBadgeClass('REMOVE')).toContain('surface');
    expect(logs.getEventBadgeClass('VAT_REGIME')).toContain('accent');
    expect(logs.getEventBadgeClass('UNKNOWN')).toContain('surface');
  });

  it('escapeHtml returns a string representation', () => {
    const { api } = buildApi();
    const logs = new DecisionLogs(api);
    logs.setPair('MNQ');
    const result = logs.escapeHtml('<script>');
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });
});
