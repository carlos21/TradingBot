import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { SettingsManager } from '../../admin/SettingsManager.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <table><tbody id="settings-instruments-tbody"></tbody></table>
    <div id="settings-instruments-empty" class="hidden"></div>
    <input id="settings-instrument-symbol" />
    <input id="settings-instrument-full-name" />
    <input id="settings-instrument-point-value" />
    <div id="settings-instrument-add-wrapper">
      <button id="settings-instrument-add">Add Instrument</button>
    </div>
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
    <select id="settings-account-instruments" multiple></select>
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

function sampleInstruments() {
  return [
    { symbol: 'MNQ', full_name: 'MNQ 09-26', point_value: 2.0 },
    { symbol: 'ES', full_name: 'ES 09-26', point_value: 50.0 },
  ];
}

function sampleSettings() {
  return {
    trading: {
      instruments: sampleInstruments(),
      pair: 'MNQ',
      instrument: 'MNQ 09-26',
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
      { name: 'acc1', risk_usd: 100, risk_pct: 1, rr_ratio: 2, live_enabled: true, instrument_symbols: ['MNQ'] },
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

    expect(manager.instruments).toEqual(sampleInstruments());
    expect(document.getElementById('settings-session-end').value).toBe('16:58');
    expect(document.getElementById('settings-flask-port').value).toBe('5001');
    expect(document.getElementById('settings-nt-user').value).toBe('user1');
    expect(document.getElementById('settings-accounts-count').textContent).toBe('1 account');
  });

  it('renderInstruments shows empty state', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = [];
    manager.renderInstruments();

    expect(document.getElementById('settings-instruments-empty').classList.contains('hidden')).toBe(false);
  });

  it('renderInstruments renders rows with remove buttons', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();
    manager.renderInstruments();

    const rows = document.getElementById('settings-instruments-tbody').querySelectorAll('tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('MNQ');
    expect(rows[0].textContent).toContain('MNQ 09-26');
    expect(document.getElementById('settings-instruments-empty').classList.contains('hidden')).toBe(true);
  });

  it('addInstrument creates new instrument', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = [];

    document.getElementById('settings-instrument-symbol').value = 'MNQ';
    document.getElementById('settings-instrument-full-name').value = 'MNQ 09-26';
    document.getElementById('settings-instrument-point-value').value = '2.0';

    manager.addInstrument();

    expect(manager.instruments.length).toBe(1);
    expect(manager.instruments[0]).toEqual({
      symbol: 'MNQ',
      full_name: 'MNQ 09-26',
      point_value: 2.0,
    });
  });

  it('addInstrument requires symbol and full name', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);

    document.getElementById('settings-instrument-symbol').value = '';
    document.getElementById('settings-instrument-full-name').value = 'MNQ 09-26';
    manager.addInstrument();
    expect(document.getElementById('settings-save-status').textContent).toBe('Instrument symbol is required');

    document.getElementById('settings-instrument-symbol').value = 'MNQ';
    document.getElementById('settings-instrument-full-name').value = '';
    manager.addInstrument();
    expect(document.getElementById('settings-save-status').textContent).toBe('Instrument full name is required');
  });

  it('editInstrument updates existing instrument', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = [{ symbol: 'MNQ', full_name: 'Old', point_value: 1 }];

    manager.editInstrument('MNQ');
    document.getElementById('settings-instrument-full-name').value = 'MNQ 09-26';
    document.getElementById('settings-instrument-point-value').value = '2.5';

    manager.addInstrument();

    expect(manager.instruments[0].full_name).toBe('MNQ 09-26');
    expect(manager.instruments[0].point_value).toBe(2.5);
  });

  it('removeInstrument filters instrument', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();

    manager.removeInstrument('MNQ');

    expect(manager.instruments.length).toBe(1);
    expect(manager.instruments[0].symbol).toBe('ES');
  });

  it('editInstrument populates form and changes button', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();
    manager.renderInstruments();

    manager.editInstrument('MNQ');

    expect(document.getElementById('settings-instrument-symbol').value).toBe('MNQ');
    expect(document.getElementById('settings-instrument-add').textContent).toContain('Update Instrument');
    expect(document.getElementById('settings-instrument-cancel')).not.toBeNull();
  });

  it('cancelEditInstrument clears form', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();
    manager.renderInstruments();
    manager.editInstrument('MNQ');

    document.getElementById('settings-instrument-cancel').click();

    expect(document.getElementById('settings-instrument-symbol').value).toBe('');
    expect(document.getElementById('settings-instrument-add').textContent).toContain('Add Instrument');
  });

  it('addInstrument blocks duplicate symbols', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();

    document.getElementById('settings-instrument-symbol').value = 'MNQ';
    document.getElementById('settings-instrument-full-name').value = 'Duplicate';
    document.getElementById('settings-instrument-point-value').value = '1.0';

    manager.addInstrument();

    expect(manager.instruments.length).toBe(2);
    expect(document.getElementById('settings-save-status').textContent).toBe('Instrument symbol already exists');
  });

  it('renderAccounts shows empty state', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.accounts = [];
    manager.renderAccounts();

    expect(document.getElementById('settings-accounts-empty').classList.contains('hidden')).toBe(false);
  });

  it('addAccount creates new account with selected instruments', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();
    manager.populateAccountInstrumentOptions();
    manager.accounts = [];

    document.getElementById('settings-account-name').value = 'acc1';
    document.getElementById('settings-account-risk').value = '100';
    document.getElementById('settings-account-riskpct').value = '1';
    document.getElementById('settings-account-rr').value = '2';
    document.getElementById('settings-account-live').checked = true;

    const select = document.getElementById('settings-account-instruments');
    Array.from(select.options).forEach(o => { o.selected = o.value === 'MNQ' || o.value === 'ES'; });

    manager.addAccount();

    expect(manager.accounts.length).toBe(1);
    expect(manager.accounts[0]).toEqual({
      name: 'acc1',
      risk_usd: 100,
      risk_pct: 1,
      rr_ratio: 2,
      live_enabled: true,
      instrument_symbols: ['MNQ', 'ES'],
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

  it('addAccount updates existing account and instruments', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();
    manager.populateAccountInstrumentOptions();
    manager.accounts = [{ name: 'acc1', risk_usd: 50, risk_pct: 0.5, rr_ratio: 1, live_enabled: false, instrument_symbols: [] }];

    document.getElementById('settings-account-name').value = 'acc1';
    document.getElementById('settings-account-risk').value = '100';
    document.getElementById('settings-account-live').checked = true;

    const select = document.getElementById('settings-account-instruments');
    Array.from(select.options).forEach(o => { o.selected = o.value === 'ES'; });

    manager.addAccount();

    expect(manager.accounts[0].risk_usd).toBe(100);
    expect(manager.accounts[0].live_enabled).toBe(true);
    expect(manager.accounts[0].instrument_symbols).toEqual(['ES']);
  });

  it('editAccount populates form and changes button', () => {
    const { api } = buildApi();
    const manager = new SettingsManager(api);
    bindManager(manager);
    manager.instruments = sampleInstruments();
    manager.accounts = [{ name: 'acc1', risk_usd: 100, risk_pct: 1, rr_ratio: 2, live_enabled: true, instrument_symbols: ['MNQ'] }];
    manager.renderAccounts();

    manager.editAccount('acc1');

    expect(document.getElementById('settings-account-name').value).toBe('acc1');
    const select = document.getElementById('settings-account-instruments');
    const selected = Array.from(select.selectedOptions).map(o => o.value);
    expect(selected).toEqual(['MNQ']);
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

  it('saveSettings posts payload with instruments and reloads', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', sampleSettings());
    http.setResponse('POST', '/api/settings', { saved: true });

    const manager = new SettingsManager(api);
    manager.init();
    await new Promise(r => setTimeout(r, 50));

    const newInstruments = [{ symbol: 'ES', full_name: 'ES 09-26', point_value: 50 }];
    manager.instruments = newInstruments;
    document.getElementById('settings-save-btn').click();

    await new Promise(r => setTimeout(r, 50));

    const post = http.requests.find(r => r.method === 'POST' && r.url === '/api/settings');
    expect(post.data.trading.instruments).toEqual(newInstruments);
    expect(post.data.trading.pair).toBe('ES');
    expect(post.data.trading.instrument).toBe('ES 09-26');
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
