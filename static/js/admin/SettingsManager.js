/**
 * Settings Manager for the admin dashboard.
 * Handles loading, editing, and saving DB-backed settings with modern UX.
 */
export class SettingsManager {
  constructor(api) {
    this.api = api;
    this.accounts = [];
  }

  init() {
    this.bindElements();
    this.bindEvents();
    this.loadSettings();
  }

  bindElements() {
    this.el = {
      pair: document.getElementById('settings-pair'),
      instrument: document.getElementById('settings-instrument'),
      // Global risk/rr removed — now per-account
      flaskPort: document.getElementById('settings-flask-port'),
      zmqHost: document.getElementById('settings-zmq-host'),
      zmqMarket: document.getElementById('settings-zmq-market'),
      zmqCmd: document.getElementById('settings-zmq-cmd'),
      zmqQuery: document.getElementById('settings-zmq-query'),
      zmqHb: document.getElementById('settings-zmq-hb'),
      accountsTbody: document.getElementById('settings-accounts-tbody'),
      accountsEmpty: document.getElementById('settings-accounts-empty'),
      accountsCount: document.getElementById('settings-accounts-count'),
      accountName: document.getElementById('settings-account-name'),
      accountRisk: document.getElementById('settings-account-risk'),
      accountRiskPct: document.getElementById('settings-account-riskpct'),
      accountRr: document.getElementById('settings-account-rr'),
      ntUser: document.getElementById('settings-nt-user'),
      ntUserList: document.getElementById('settings-nt-user-list'),
      ntPass: document.getElementById('settings-nt-pass'),
      ntTogglePass: document.getElementById('settings-nt-toggle-pass'),
      eyeIcon: document.getElementById('eye-icon'),
      eyeSlashIcon: document.getElementById('eye-slash-icon'),
      saveBtn: document.getElementById('settings-save-btn'),
      saveStatus: document.getElementById('settings-save-status'),
      addAccountBtn: document.getElementById('settings-account-add'),
    };
  }

  bindEvents() {
    this.el.saveBtn.addEventListener('click', () => this.saveSettings());
    this.el.addAccountBtn.addEventListener('click', () => this.addAccount());
    this.el.ntTogglePass.addEventListener('click', () => this.togglePassword());
  }

  async loadSettings() {
    try {
      const data = await this.api.getSettings();
      const t = data.trading || {};
      const n = data.network || {};
      this.el.pair.value = t.pair || 'MNQ';
      this.el.instrument.value = t.instrument || '';
      // Risk/RR are now per-account only
      this.el.flaskPort.value = n.flask_port || '5001';
      this.el.zmqHost.value = n.zmq_host || '127.0.0.1';
      this.el.zmqMarket.value = n.zmq_market_port || '5555';
      this.el.zmqCmd.value = n.zmq_command_port || '5556';
      this.el.zmqQuery.value = n.zmq_query_port || '5557';
      this.el.zmqHb.value = n.zmq_heartbeat_port || '5558';

      this.accounts = data.accounts || [];
      this.renderAccounts();

      const c = data.credentials || {};
      this.populateUsernameDropdown(c.stored_usernames || [], c.username || '');
      this.el.ntPass.value = c.password || '';
    } catch (e) {
      console.error('[SettingsManager] Failed to load settings:', e);
      this.showStatus('Failed to load settings', 'error');
    }
  }

  populateUsernameDropdown(usernames, current) {
    this.el.ntUserList.innerHTML = '';
    for (const u of usernames) {
      const opt = document.createElement('option');
      opt.value = u;
      this.el.ntUserList.appendChild(opt);
    }
    this.el.ntUser.value = current || '';
  }

  renderAccounts() {
    this.el.accountsTbody.innerHTML = '';
    this.el.accountsCount.textContent = `${this.accounts.length} account${this.accounts.length !== 1 ? 's' : ''}`;

    if (this.accounts.length === 0) {
      this.el.accountsEmpty.classList.remove('hidden');
      return;
    }
    this.el.accountsEmpty.classList.add('hidden');

    for (const acct of this.accounts) {
      const tr = document.createElement('tr');
      tr.className = 'hover:bg-gray-700/30 transition-colors';
      const riskUsd = acct.risk_usd ? `$${acct.risk_usd}` : '-';
      const riskPct = acct.risk_pct ? `${acct.risk_pct}%` : '-';
      const rr = acct.rr_ratio ? `${acct.rr_ratio}` : '-';
      tr.innerHTML = `
        <td class="px-4 py-3 font-medium text-white">${this.escapeHtml(acct.name)}</td>
        <td class="px-4 py-3 text-gray-300">${riskUsd}</td>
        <td class="px-4 py-3 text-gray-300">${riskPct}</td>
        <td class="px-4 py-3 text-gray-300">${rr}</td>
        <td class="px-4 py-3 text-right">
          <button class="text-red-400 hover:text-red-300 transition-colors p-1" title="Remove account">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </td>
      `;
      tr.querySelector('button').addEventListener('click', () => this.removeAccount(acct.name));
      this.el.accountsTbody.appendChild(tr);
    }
  }

  addAccount() {
    const name = this.el.accountName.value.trim();
    const risk = parseFloat(this.el.accountRisk.value) || null;
    const riskPct = parseFloat(this.el.accountRiskPct.value) || null;
    const rr = parseFloat(this.el.accountRr.value) || null;
    if (!name) {
      this.showStatus('Account name is required', 'error');
      return;
    }
    const existing = this.accounts.findIndex(a => a.name === name);
    if (existing >= 0) {
      this.accounts[existing] = { name, risk_usd: risk, risk_pct: riskPct, rr_ratio: rr };
    } else {
      this.accounts.push({ name, risk_usd: risk, risk_pct: riskPct, rr_ratio: rr });
    }
    this.renderAccounts();
    this.el.accountName.value = '';
    this.el.accountRisk.value = '';
    this.el.accountRiskPct.value = '';
    this.el.accountRr.value = '';
    this.showStatus('Account added', 'success');
  }

  removeAccount(name) {
    this.accounts = this.accounts.filter(a => a.name !== name);
    this.renderAccounts();
  }

  togglePassword() {
    const isPassword = this.el.ntPass.type === 'password';
    this.el.ntPass.type = isPassword ? 'text' : 'password';
    this.el.eyeIcon.classList.toggle('hidden', isPassword);
    this.el.eyeSlashIcon.classList.toggle('hidden', !isPassword);
  }

  async saveSettings() {
    this.showStatus('Saving...', 'info');
    try {
      const payload = {
        trading: {
          pair: this.el.pair.value,
          instrument: this.el.instrument.value,
        },
        network: {
          flask_port: this.el.flaskPort.value,
          zmq_host: this.el.zmqHost.value,
          zmq_market_port: this.el.zmqMarket.value,
          zmq_command_port: this.el.zmqCmd.value,
          zmq_query_port: this.el.zmqQuery.value,
          zmq_heartbeat_port: this.el.zmqHb.value,
        },
        accounts: this.accounts,
        credentials: {
          username: this.el.ntUser.value,
          password: this.el.ntPass.value,
        },
      };
      await this.api.saveSettings(payload);
      this.showStatus('Settings saved successfully', 'success');
      // Refresh to get updated stored_usernames
      await this.loadSettings();
    } catch (e) {
      console.error('[SettingsManager] Failed to save settings:', e);
      this.showStatus('Failed to save settings', 'error');
    }
  }

  showStatus(message, type) {
    this.el.saveStatus.textContent = message;
    const colors = {
      success: 'text-green-400',
      error: 'text-red-400',
      info: 'text-blue-400',
    };
    this.el.saveStatus.className = `text-sm ${colors[type] || 'text-gray-400'}`;
    if (type === 'success' || type === 'error') {
      setTimeout(() => { this.el.saveStatus.textContent = ''; }, 3000);
    }
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }
}
