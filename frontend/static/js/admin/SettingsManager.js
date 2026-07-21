/**
 * Settings Manager for the admin dashboard.
 * Handles loading, editing, and saving DB-backed settings with modern UX.
 */
export class SettingsManager {
  constructor(api) {
    this.api = api;
    this.accounts = [];
    this.instruments = [];
    this.editingAccountName = null;
  }

  init() {
    this.bindElements();
    this.bindEvents();
    this.loadSettings();
  }

  bindElements() {
    this.el = {
      instrumentsTbody: document.getElementById('settings-instruments-tbody'),
      instrumentsEmpty: document.getElementById('settings-instruments-empty'),
      sessionEnd: document.getElementById('settings-session-end'),
      historyHours: document.getElementById('settings-history-hours'),
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
      accountLive: document.getElementById('settings-account-live'),
      accountInstruments: document.getElementById('settings-account-instruments'),
      saveBtn: document.getElementById('settings-save-btn'),
      saveStatus: document.getElementById('settings-save-status'),
      addAccountBtn: document.getElementById('settings-account-add'),
      addAccountBtnWrapper: document.getElementById('settings-account-add').parentElement,
    };
  }

  bindEvents() {
    this.el.saveBtn.addEventListener('click', () => this.saveSettings());
    this.el.addAccountBtn.addEventListener('click', () => this.addAccount());
  }

  async loadSettings() {
    try {
      const data = await this.api.getSettings();
      const t = data.trading || {};
      const n = data.network || {};
      this.instruments = Array.isArray(t.instruments) ? t.instruments : [];
      this.renderInstruments();
      this.renderAccountInstrumentChips();
      this.el.sessionEnd.value = t.session_end || '16:58';
      this.el.historyHours.value = t.history_hours || '';
      // Risk/RR are now per-account only
      this.el.flaskPort.value = n.flask_port || '5001';
      this.el.zmqHost.value = n.zmq_host || '127.0.0.1';
      this.el.zmqMarket.value = n.zmq_market_port || '5555';
      this.el.zmqCmd.value = n.zmq_command_port || '5556';
      this.el.zmqQuery.value = n.zmq_query_port || '5557';
      this.el.zmqHb.value = n.zmq_heartbeat_port || '5558';

      this.accounts = data.accounts || [];
      this.renderAccounts();
    } catch (e) {
      console.error('[SettingsManager] Failed to load settings:', e);
      this.showStatus('Failed to load settings', 'error');
    }
  }

  renderInstruments() {
    this.el.instrumentsTbody.innerHTML = '';

    if (this.instruments.length === 0) {
      this.el.instrumentsEmpty.classList.remove('hidden');
      return;
    }
    this.el.instrumentsEmpty.classList.add('hidden');

    for (const inst of this.instruments) {
      const tr = document.createElement('tr');
      tr.className = 'hover:bg-accent-500/10 transition-colors';
      tr.innerHTML = `
        <td class="px-3 py-2 font-medium text-slate-100">${this.escapeHtml(inst.symbol)}</td>
        <td class="px-3 py-2">
          <input type="text" class="instrument-full-name-input w-full bg-surface-700 border border-surface-600 rounded-lg px-2 py-1 text-slate-100 focus:ring-2 focus:ring-accent-500 focus:border-transparent outline-none transition-colors" data-symbol="${this.escapeHtml(inst.symbol)}" />
        </td>
        <td class="px-3 py-2 text-slate-300">${inst.point_value ?? '-'}</td>
      `;
      const input = tr.querySelector('.instrument-full-name-input');
      input.value = inst.full_name || '';
      input.addEventListener('input', () => {
        inst.full_name = input.value;
      });
      this.el.instrumentsTbody.appendChild(tr);
    }
  }

  renderAccountInstrumentChips(selectedSymbols = []) {
    const container = this.el.accountInstruments;
    if (!container) return;
    container.innerHTML = '';
    const selectedSet = new Set(selectedSymbols);

    for (const inst of this.instruments) {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.dataset.symbol = inst.symbol;
      const isSelected = selectedSet.has(inst.symbol);
      chip.className = this._instrumentChipClass(isSelected);
      chip.innerHTML = `
        <span>${this.escapeHtml(inst.symbol)}</span>
        ${isSelected ? '<svg class="h-3.5 w-3.5 ml-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>' : ''}
      `;
      chip.addEventListener('click', () => this._toggleInstrumentChip(chip));
      container.appendChild(chip);
    }

    if (this.instruments.length === 0) {
      container.innerHTML = '<span class="text-xs text-slate-500">No instruments configured. Add instruments below.</span>';
    }
  }

  _instrumentChipClass(selected) {
    const base = 'inline-flex items-center px-3 py-1.5 rounded-full text-sm font-medium transition-colors cursor-pointer select-none border';
    return selected
      ? `${base} bg-accent-600/20 border-accent-500 text-accent-300 ring-1 ring-accent-500/50`
      : `${base} bg-surface-700 border-surface-600 text-slate-300 hover:bg-surface-600 hover:border-surface-500`;
  }

  _toggleInstrumentChip(chip) {
    const selected = chip.classList.contains('bg-accent-600/20');
    chip.className = this._instrumentChipClass(!selected);
    chip.innerHTML = `
      <span>${chip.querySelector('span').textContent}</span>
      ${!selected ? '<svg class="h-3.5 w-3.5 ml-1.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>' : ''}
    `;
  }

  getSelectedInstrumentSymbols() {
    const container = this.el.accountInstruments;
    if (!container) return [];
    const chips = container.querySelectorAll('[data-symbol]');
    return Array.from(chips)
      .filter(chip => chip.classList.contains('bg-accent-600/20'))
      .map(chip => chip.dataset.symbol);
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
      tr.className = 'hover:bg-accent-500/10 transition-colors';
      const riskUsd = acct.risk_usd ? `$${acct.risk_usd}` : '-';
      const riskPct = acct.risk_pct ? `${acct.risk_pct}%` : '-';
      const rr = acct.rr_ratio ? `${acct.rr_ratio}` : '-';
      const symbols = Array.isArray(acct.instrument_symbols) ? acct.instrument_symbols : [];
      const instrumentsHtml = symbols.length
        ? symbols.map(s => `<span class="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-accent-500/20 text-accent-300 mr-1">${this.escapeHtml(s)}</span>`).join('')
        : '<span class="text-xs text-rose-400">None</span>';
      const liveBadge = acct.live_enabled
        ? '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/20 text-emerald-400">Live</span>'
        : '<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-600/30 text-slate-400">Off</span>';
      tr.innerHTML = `
        <td class="px-4 py-3 font-medium text-slate-100">${this.escapeHtml(acct.name)}</td>
        <td class="px-4 py-3 text-slate-300">${riskUsd}</td>
        <td class="px-4 py-3 text-slate-300">${riskPct}</td>
        <td class="px-4 py-3 text-slate-300">${rr}</td>
        <td class="px-4 py-3">${instrumentsHtml}</td>
        <td class="px-4 py-3">${liveBadge}</td>
        <td class="px-4 py-3 text-right">
          <button class="action-btn hover:text-accent-400 mr-1" title="Edit account">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
          </button>
          <button class="action-btn hover:text-rose-500" title="Remove account">
            <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </td>
      `;
      const buttons = tr.querySelectorAll('button');
      buttons[0].addEventListener('click', () => this.editAccount(acct.name));
      buttons[1].addEventListener('click', () => this.removeAccount(acct.name));
      this.el.accountsTbody.appendChild(tr);
    }
  }

  addAccount() {
    const name = this.el.accountName.value.trim();
    const risk = parseFloat(this.el.accountRisk.value) || null;
    const riskPct = parseFloat(this.el.accountRiskPct.value) || null;
    const rr = parseFloat(this.el.accountRr.value) || null;
    const liveEnabled = this.el.accountLive.checked;
    const instrumentSymbols = this.getSelectedInstrumentSymbols();
    if (!name) {
      this.showStatus('Account name is required', 'error');
      return;
    }
    const existing = this.accounts.findIndex(a => a.name === name);
    const account = { name, risk_usd: risk, risk_pct: riskPct, rr_ratio: rr, live_enabled: liveEnabled, instrument_symbols: instrumentSymbols };
    if (existing >= 0) {
      this.accounts[existing] = account;
      this.showStatus('Account updated', 'success');
    } else {
      this.accounts.push(account);
      this.showStatus('Account added', 'success');
    }
    this.renderAccounts();
    this.clearAccountForm();
  }

  clearAccountForm() {
    this.el.accountName.value = '';
    this.el.accountRisk.value = '';
    this.el.accountRiskPct.value = '';
    this.el.accountRr.value = '';
    this.el.accountLive.checked = false;
    this.renderAccountInstrumentChips([]);
    this.editingAccountName = null;
    this.resetAddAccountBtn();
  }

  resetAddAccountBtn() {
    this.el.addAccountBtn.innerHTML = `
      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
      Add Account
    `;
    this.el.addAccountBtn.className = 'px-4 py-2 bg-accent-600 hover:bg-accent-500 rounded-lg font-medium text-slate-100 transition-colors h-fit flex items-center justify-center gap-2';
    const cancelBtn = this.el.addAccountBtnWrapper.querySelector('#settings-account-cancel');
    if (cancelBtn) cancelBtn.remove();
  }

  setEditAccountBtn() {
    this.el.addAccountBtn.innerHTML = `
      <svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/></svg>
      Update Account
    `;
    this.el.addAccountBtn.className = 'px-4 py-2 bg-amber-600 hover:bg-amber-500 rounded-lg font-medium text-slate-100 transition-colors h-fit flex items-center justify-center gap-2';
    if (!this.el.addAccountBtnWrapper.querySelector('#settings-account-cancel')) {
      const cancelBtn = document.createElement('button');
      cancelBtn.id = 'settings-account-cancel';
      cancelBtn.type = 'button';
      cancelBtn.className = 'mt-2 w-full px-3 py-1.5 text-xs text-slate-400 hover:text-slate-100 hover:bg-surface-600 rounded transition-colors';
      cancelBtn.textContent = 'Cancel';
      cancelBtn.addEventListener('click', () => this.cancelEditAccount());
      this.el.addAccountBtnWrapper.appendChild(cancelBtn);
    }
  }

  editAccount(name) {
    const acct = this.accounts.find(a => a.name === name);
    if (!acct) return;
    this.editingAccountName = name;
    this.el.accountName.value = acct.name;
    this.el.accountRisk.value = acct.risk_usd ?? '';
    this.el.accountRiskPct.value = acct.risk_pct ?? '';
    this.el.accountRr.value = acct.rr_ratio ?? '';
    this.el.accountLive.checked = acct.live_enabled === true;
    const selected = Array.isArray(acct.instrument_symbols) ? acct.instrument_symbols : [];
    this.renderAccountInstrumentChips(selected);
    this.setEditAccountBtn();
  }

  cancelEditAccount() {
    this.clearAccountForm();
  }

  removeAccount(name) {
    this.accounts = this.accounts.filter(a => a.name !== name);
    this.renderAccounts();
  }

  async saveSettings() {
    this.showStatus('Saving...', 'info');
    try {
      const primaryInstrument = this.instruments[0] || {};
      const payload = {
        trading: {
          instruments: this.instruments.map(i => ({ symbol: i.symbol, full_name: i.full_name })),
          pair: primaryInstrument.symbol || '',
          instrument: primaryInstrument.full_name || '',
          session_end: this.el.sessionEnd.value,
          history_hours: this.el.historyHours.value,
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
      };
      await this.api.saveSettings(payload);
      this.showStatus('Settings saved successfully', 'success');
      await this.loadSettings();
    } catch (e) {
      console.error('[SettingsManager] Failed to save settings:', e);
      this.showStatus('Failed to save settings', 'error');
    }
  }

  showStatus(message, type) {
    this.el.saveStatus.textContent = message;
    const colors = {
      success: 'text-emerald-400',
      error: 'text-rose-500',
      info: 'text-accent-400',
    };
    this.el.saveStatus.className = `text-sm ${colors[type] || 'text-slate-400'}`;
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
