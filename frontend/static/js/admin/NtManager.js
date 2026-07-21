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
      deployBtn: document.getElementById('nt-deploy-btn'),
      deployResult: document.getElementById('nt-deploy-result'),
      detectedDir: document.getElementById('nt-detected-dir'),
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
    if (this.el.deployBtn) {
      this.el.deployBtn.addEventListener('click', () => this.deploy());
    }
    if (this.el.credsTogglePass) {
      this.el.credsTogglePass.addEventListener('click', () => this.togglePassword());
    }
    if (this.el.saveCredsBtn) {
      this.el.saveCredsBtn.addEventListener('click', () => this.saveCredentials());
    }
    if (this.el.openBtn) {
      this.el.openBtn.addEventListener('click', () => this.openNt());
    }
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
      await this.api.saveCredentials(
        this.el.credsUser.value,
        this.el.credsPass.value,
      );
      this.showCredsStatus('Credentials saved', 'success');
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

  async deploy() {
    if (this.el.deployResult) {
      this.el.deployResult.textContent = 'Deploying...';
      this.el.deployResult.className = 'mt-2 text-sm text-slate-300';
    }
    try {
      const data = await this.api.deployNt();
      if (this.el.detectedDir && data.target_dir) {
        this.el.detectedDir.textContent = 'Target: ' + data.target_dir;
      }
      if (this.el.deployResult) {
        let msg = data.message;
        if (data.copied && data.copied.length) {
          msg += '\nCopied: ' + data.copied.join(', ');
        }
        if (data.errors && data.errors.length) {
          msg += '\nErrors: ' + data.errors.join('; ');
        }
        this.el.deployResult.textContent = msg;
        this.el.deployResult.className = data.success ? 'mt-2 text-sm text-emerald-400 whitespace-pre-line' : 'mt-2 text-sm text-rose-500 whitespace-pre-line';
      }
    } catch (e) {
      if (this.el.deployResult) {
        this.el.deployResult.textContent = 'Error: ' + e.message;
        this.el.deployResult.className = 'mt-2 text-sm text-rose-500';
      }
    }
  }

  showCredsStatus(message, type) {
    this.el.credsStatus.textContent = message;
    const colors = {
      success: 'text-emerald-400',
      error: 'text-rose-500',
      info: 'text-accent-400',
    };
    this.el.credsStatus.className = `text-sm ${colors[type] || 'text-slate-400'}`;
    if (type === 'success' || type === 'error') {
      setTimeout(() => { this.el.credsStatus.textContent = ''; }, 3000);
    }
  }

  showOpenResult(message, success) {
    this.el.openResult.textContent = message;
    if (success === true) {
      this.el.openResult.className = 'mt-3 text-sm text-emerald-400';
    } else if (success === false) {
      this.el.openResult.className = 'mt-3 text-sm text-rose-500';
    } else {
      this.el.openResult.className = 'mt-3 text-sm text-slate-300';
    }
  }
}
