import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { SettingsManager } from '../../admin/SettingsManager.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
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
    <div id="settings-accounts-empty" class="hidden"></div>
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
    <svg id="eye-slash-icon" class="hidden"></svg>
    <button id="settings-save-btn"></button>
    <div id="settings-save-status"></div>
    <div id="settings-account-add-wrapper">
      <button id="settings-account-add">Add Account</button>
    </div>
  `;
}

function sampleSettings() {
  return {
    trading: {
      pair: 'MNQ',
      instrument: 'MNQ',
      session_end: '16:58',
      history_hours: '24',
    },
    network: {
      flask_port: '5001',
      zmq_host: '127.0.0.1',
      zmq_market_port: '5555',
      zmq_command_port: '5556',
      zmq_query_port: '5557',
      zmq_heartbeat_port: '5558',
    },
    accounts: [
      { name: 'acc1', risk_usd: 100, risk_pct: 1, rr_ratio: 2, live_enabled: true },
    ],
    credentials: {
      username: 'user1',
      password: 'pass1',
      stored_usernames: ['user1'],
    },
  };
}

function buildApi() {
  const http = new FakeHttpClient();
  const api = new ApiClient({ httpClient: http });
  api.setPair('MNQ');
  return { http, api };
}

function bindManager(manager) {
  manager.bindElements();
  manager.bindEvents();
}

describe('SettingsManager', () => {
  beforeEach(() => {
    setupDom();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('init loads settings into form fields', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', sampleSettings());

    const manager = new SettingsManager(api);
    manager.init();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('settings-pair').value).toBe('MNQ');
    expect(document.getElementById('settings-flask-port').value).toBe('5001');
    expect(document.getElementById('settings-nt-user').value).toBe('user1');
    expect(document.getElementById('settings-accounts-count').textContent).toBe('1 account');
  });

  it('renderAccounts shows empty state', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [];
    manager.renderAccounts();

    expect(document.getElementById('settings-accounts-empty').classList.contains('hidden')).toBe(false);
  });

  it('addAccount creates new account', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [];

    document.getElementById('settings-account-name').value = 'acc1';
    document.getElementById('settings-account-risk').value = '100';
    document.getElementById('settings-account-riskpct').value = '1';
    document.getElementById('settings-account-rr').value = '2';
    document.getElementById('settings-account-live').checked = true;

    manager.addAccount();

    expect(manager.accounts.length).toBe(1);
    expect(manager.accounts[0]).toEqual({
      name: 'acc1',
      risk_usd: 100,
      risk_pct: 1,
      rr_ratio: 2,
      live_enabled: true,
    });
  });

  it('addAccount requires name', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);

    document.getElementById('settings-account-name').value = '';
    manager.addAccount();

    expect(document.getElementById('settings-save-status').textContent).toBe('Account name is required');
  });

  it('addAccount updates existing account', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [{ name: 'acc1', risk_usd: 50, risk_pct: 0.5, rr_ratio: 1, live_enabled: false }];

    document.getElementById('settings-account-name').value = 'acc1';
    document.getElementById('settings-account-risk').value = '100';
    document.getElementById('settings-account-live').checked = true;

    manager.addAccount();

    expect(manager.accounts[0].risk_usd).toBe(100);
    expect(manager.accounts[0].live_enabled).toBe(true);
  });

  it('editAccount populates form and changes button', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [{ name: 'acc1', risk_usd: 100, risk_pct: 1, rr_ratio: 2, live_enabled: true }];
    manager.renderAccounts();

    manager.editAccount('acc1');

    expect(document.getElementById('settings-account-name').value).toBe('acc1');
    expect(document.getElementById('settings-account-add').textContent).toContain('Update Account');
    expect(document.getElementById('settings-account-cancel')).not.toBeNull();
  });

  it('cancelEditAccount clears form', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [{ name: 'acc1', risk_usd: 100, risk_pct: 1, rr_ratio: 2, live_enabled: true }];
    manager.renderAccounts();
    manager.editAccount('acc1');

    document.getElementById('settings-account-cancel').click();

    expect(document.getElementById('settings-account-name').value).toBe('');
    expect(document.getElementById('settings-account-add').textContent).toContain('Add Account');
  });

  it('removeAccount filters account', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [
      { name: 'acc1', risk_usd: 100, risk_pct: 1, rr_ratio: 2, live_enabled: true },
      { name: 'acc2', risk_usd: 50, risk_pct: 0.5, rr_ratio: 1, live_enabled: false },
    ];

    manager.removeAccount('acc1');

    expect(manager.accounts.length).toBe(1);
    expect(manager.accounts[0].name).toBe('acc2');
  });

  it('togglePassword switches input type', () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', sampleSettings());

    const manager = new SettingsManager(api);
    manager.init();

    expect(document.getElementById('settings-nt-pass').type).toBe('password');
    document.getElementById('settings-nt-toggle-pass').click();
    expect(document.getElementById('settings-nt-pass').type).toBe('text');
  });

  it('saveSettings posts payload and reloads', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', sampleSettings());
    http.setResponse('POST', '/api/settings', { saved: true });

    const manager = new SettingsManager(api);
    manager.init();
    await new Promise(r => setTimeout(r, 50));

    document.getElementById('settings-pair').value = 'ES';
    document.getElementById('settings-save-btn').click();

    await new Promise(r => setTimeout(r, 50));

    const post = http.requests.find(r => r.method === 'POST' && r.url === '/api/settings');
    expect(post.data.trading.pair).toBe('ES');
    expect(document.getElementById('settings-save-status').textContent).toBe('Settings saved successfully');
  });

  it('showStatus clears after timeout', async () => {
    vi.useFakeTimers();
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', sampleSettings());

    const manager = new SettingsManager(api);
    manager.init();

    manager.showStatus('Saved', 'success');
    expect(document.getElementById('settings-save-status').textContent).toBe('Saved');

    vi.advanceTimersByTime(4000);
    expect(document.getElementById('settings-save-status').textContent).toBe('');
  });

  it('escapeHtml returns a string representation', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    const result = manager.escapeHtml('<script>');
    expect(typeof result).toBe('string');
    expect(result.length).toBeGreaterThan(0);
  });
});
