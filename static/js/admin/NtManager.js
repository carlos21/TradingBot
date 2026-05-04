/**
 * NinjaTrader Manager for the admin dashboard.
 * Handles credentials, auto-login, and NetMQ install.
 */
export class NtManager {
  constructor(api) {
    this.api = api;
  }

  init() {
    this.bindElements();
    this.bindEvents();
    this.loadCredentials();
  }

  bindElements() {
    this.el = {
      installNetmqBtn: document.getElementById('nt-install-netmq-btn'),
      installNetmqResult: document.getElementById('nt-install-netmq-result'),
      // Credentials
      credsUser: document.getElementById('nt-creds-user'),
      credsUserList: document.getElementById('nt-creds-user-list'),
      credsPass: document.getElementById('nt-creds-pass'),
      credsTogglePass: document.getElementById('nt-creds-toggle-pass'),
      eyeIcon: document.getElementById('nt-eye-icon'),
      eyeSlashIcon: document.getElementById('nt-eye-slash-icon'),
      saveCredsBtn: document.getElementById('nt-save-creds-btn'),
      openBtn: document.getElementById('nt-open-btn'),
      openResult: document.getElementById('nt-open-result'),
      credsStatus: document.getElementById('nt-creds-status'),
    };
  }

  bindEvents() {
    this.el.installNetmqBtn.addEventListener('click', () => this.installNetmq());
    this.el.credsTogglePass.addEventListener('click', () => this.togglePassword());
    this.el.saveCredsBtn.addEventListener('click', () => this.saveCredentials());
    this.el.openBtn.addEventListener('click', () => this.openNt());
  }

  async loadCredentials() {
    try {
      const data = await this.api.getSettings();
      const c = data.credentials || {};
      this.populateUsernameDropdown(c.stored_usernames || [], c.username || '');
      this.el.credsPass.value = c.password || '';
    } catch (e) {
      console.error('[NtManager] Failed to load credentials:', e);
    }
  }

  populateUsernameDropdown(usernames, current) {
    this.el.credsUserList.innerHTML = '';
    for (const u of usernames) {
      const opt = document.createElement('option');
      opt.value = u;
      this.el.credsUserList.appendChild(opt);
    }
    this.el.credsUser.value = current || '';
  }

  togglePassword() {
    const isPassword = this.el.credsPass.type === 'password';
    this.el.credsPass.type = isPassword ? 'text' : 'password';
    this.el.eyeIcon.classList.toggle('hidden', isPassword);
    this.el.eyeSlashIcon.classList.toggle('hidden', !isPassword);
  }

  async saveCredentials() {
    this.showCredsStatus('Saving...', 'info');
    try {
      const payload = {
        trading: {},
        network: {},
        accounts: [],
        credentials: {
          username: this.el.credsUser.value,
          password: this.el.credsPass.value,
        },
      };
      await this.api.saveSettings(payload);
      this.showCredsStatus('Credentials saved', 'success');
      await this.loadCredentials();
    } catch (e) {
      console.error('[NtManager] Failed to save credentials:', e);
      this.showCredsStatus('Failed to save credentials', 'error');
    }
  }

  async openNt() {
    const username = this.el.credsUser.value.trim();
    const password = this.el.credsPass.value;
    if (!username || !password) {
      this.showOpenResult('Please enter both username and password.', false);
      return;
    }

    this.showOpenResult('Launching NinjaTrader...', null);
    try {
      const data = await this.api.openNt(username, password);
      this.showOpenResult(data.message, data.success);
    } catch (e) {
      this.showOpenResult('Error: ' + e.message, false);
    }
  }

  async installNetmq() {
    this.el.installNetmqResult.textContent = 'Installing...';
    try {
      const data = await this.api.installNtNetmq();
      this.el.installNetmqResult.textContent = data.message;
      this.el.installNetmqResult.className = data.success ? 'mt-2 text-sm text-green-400' : 'mt-2 text-sm text-red-400';
    } catch (e) {
      this.el.installNetmqResult.textContent = 'Error: ' + e.message;
      this.el.installNetmqResult.className = 'mt-2 text-sm text-red-400';
    }
  }

  showCredsStatus(message, type) {
    this.el.credsStatus.textContent = message;
    const colors = {
      success: 'text-green-400',
      error: 'text-red-400',
      info: 'text-blue-400',
    };
    this.el.credsStatus.className = `text-sm ${colors[type] || 'text-gray-400'}`;
    if (type === 'success' || type === 'error') {
      setTimeout(() => { this.el.credsStatus.textContent = ''; }, 3000);
    }
  }

  showOpenResult(message, success) {
    this.el.openResult.textContent = message;
    if (success === true) {
      this.el.openResult.className = 'mt-3 text-sm text-green-400';
    } else if (success === false) {
      this.el.openResult.className = 'mt-3 text-sm text-red-400';
    } else {
      this.el.openResult.className = 'mt-3 text-sm text-gray-300';
    }
  }
}
