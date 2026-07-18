import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { LogPanel } from '../../admin/LogPanel.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';
import { FakeSocket } from '../fakes/FakeSocket.js';

function setupDom() {
  document.body.innerHTML = `
    <div id="log-entries"></div>
    <select id="log-level-filter">
      <option value="">All</option>
      <option value="INFO">INFO</option>
      <option value="ERROR">ERROR</option>
    </select>
    <select id="log-source-filter"></select>
    <input id="log-search-filter" />
    <button id="log-clear-btn"></button>
    <button id="log-pause-btn">Pause</button>
    <button id="log-load-more-btn">Load more</button>
    <span id="log-connection-status"></span>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', {
    logs: [],
    sources: [],
    has_more: false,
  });
  const api = new ApiClient({ httpClient: http });
  return { http, api };
}

describe('LogPanel', () => {
  beforeEach(() => {
    setupDom();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('loads initial logs on construction', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', {
      logs: [{ time: 1700000000, level: 'INFO', source: 'app', message: 'hello' }],
      sources: ['app'],
      has_more: false,
    });

    const socket = new FakeSocket();
    new LogPanel(socket, api, 'MNQ');

    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('log-entries').textContent).toContain('hello');
    expect(document.getElementById('log-load-more-btn').classList.contains('hidden')).toBe(true);
  });

  it('socket connect/disconnect updates status', () => {
    const { api } = buildApi();
    const socket = new FakeSocket();
    new LogPanel(socket, api, 'MNQ');

    socket.trigger('connect');
    expect(document.getElementById('log-connection-status').textContent).toBe('Connected');

    socket.trigger('disconnect');
    expect(document.getElementById('log-connection-status').textContent).toBe('Disconnected');
  });

  it('clear button empties entries', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', {
      logs: [{ time: 1700000000, level: 'INFO', source: 'app', message: 'hello' }],
      sources: [],
      has_more: false,
    });

    const socket = new FakeSocket();
    const panel = new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    panel.clear();
    expect(document.getElementById('log-entries').textContent).toContain('No logs available');
  });

  it('togglePause toggles state and button text', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', { logs: [], sources: [], has_more: false });

    const socket = new FakeSocket();
    const panel = new LogPanel(socket, api, 'MNQ');
    const btn = document.getElementById('log-pause-btn');

    expect(panel.paused).toBe(false);
    btn.click();
    expect(panel.paused).toBe(true);
    expect(btn.textContent).toBe('Resume');
    btn.click();
    expect(panel.paused).toBe(false);
    expect(btn.textContent).toBe('Pause');
  });

  it('loadMore fetches next page', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', {
      logs: [{ time: 1700000000, level: 'INFO', source: 'app', message: 'first' }],
      sources: [],
      has_more: true,
    });
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=1', {
      logs: [{ time: 1700000001, level: 'INFO', source: 'app', message: 'second' }],
      sources: [],
      has_more: false,
    });

    const socket = new FakeSocket();
    const panel = new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    document.getElementById('log-load-more-btn').click();
    await new Promise(r => setTimeout(r, 50));

    expect(panel.entries.length).toBe(2);
  });

  it('filters rerender on input', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', {
      logs: [
        { time: 1700000000, level: 'INFO', source: 'app', message: 'hello' },
        { time: 1700000001, level: 'ERROR', source: 'app', message: 'boom' },
      ],
      sources: [],
      has_more: false,
    });

    const socket = new FakeSocket();
    new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    const search = document.getElementById('log-search-filter');
    search.value = 'boom';
    search.dispatchEvent(new Event('input', { bubbles: true }));

    expect(document.getElementById('log-entries').textContent).toContain('boom');
    expect(document.getElementById('log-entries').textContent).not.toContain('hello');
  });

  it('onLogEntry prepends entry when no filters', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', { logs: [], sources: [], has_more: false });

    const socket = new FakeSocket();
    new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    socket.trigger('system_log', { message: 'live' });

    expect(document.getElementById('log-entries').textContent).toContain('live');
  });

  it('onLogEntry does nothing when paused', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', { logs: [], sources: [], has_more: false });

    const socket = new FakeSocket();
    const panel = new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    panel.togglePause();
    socket.trigger('system_log', { message: 'live' });

    expect(document.getElementById('log-entries').textContent).not.toContain('live');
  });

  it('onLogEntry falls back to render when filters active', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', { logs: [], sources: [], has_more: false });

    const socket = new FakeSocket();
    new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    document.getElementById('log-level-filter').value = 'INFO';
    socket.trigger('system_log', { level: 'INFO', message: 'filtered' });

    expect(document.getElementById('log-entries').textContent).toContain('filtered');
  });

  it('source dropdown updates with new sources', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/admin/logs/recent?pair=MNQ&limit=200&offset=0', {
      logs: [],
      sources: ['alpha', 'beta'],
      has_more: false,
    });

    const socket = new FakeSocket();
    new LogPanel(socket, api, 'MNQ');
    await new Promise(r => setTimeout(r, 50));

    const options = Array.from(document.getElementById('log-source-filter').options).map(o => o.value);
    expect(options).toEqual(['', 'alpha', 'beta']);
  });

  it('level color helpers return expected classes', () => {
    const { api } = buildApi();
    const panel = new LogPanel(new FakeSocket(), api, 'MNQ');
    expect(panel._levelColor('ERROR')).toContain('rose');
    expect(panel._levelColor('WARN')).toContain('amber');
    expect(panel._levelColor('INFO')).toContain('accent');
    expect(panel._levelColor('DEBUG')).toContain('slate');
    expect(panel._levelBg('ERROR')).toContain('rose');
    expect(panel._levelBg('DEBUG')).toBe('');
  });
});
