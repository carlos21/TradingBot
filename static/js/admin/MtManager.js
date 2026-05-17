/**
 * MetaTrader Manager for the admin dashboard.
 * Handles terminal path configuration and launch.
 */
export class MtManager {
  constructor(api) {
    this.api = api;
  }

  init() {
    this.bindElements();
    this.bindEvents();
    this.loadSettings();
  }

  bindElements() {
    this.el = {
      pathInput: document.getElementById('mt-path-input'),
      savePathBtn: document.getElementById('mt-save-path-btn'),
      launchBtn: document.getElementById('mt-launch-btn'),
      launchResult: document.getElementById('mt-launch-result'),
      pathStatus: document.getElementById('mt-path-status'),
    };
  }

  bindEvents() {
    if (this.el.savePathBtn) {
      this.el.savePathBtn.addEventListener('click', () => this.savePath());
    }
    if (this.el.launchBtn) {
      this.el.launchBtn.addEventListener('click', () => this.launch());
    }
  }

  async loadSettings() {
    try {
      const data = await this.api.getSettings();
      const path = data.mt_terminal_path || '';
      if (this.el.pathInput) {
        this.el.pathInput.value = path;
      }
    } catch (e) {
      console.error('[MtManager] Failed to load settings:', e);
    }
  }

  async savePath() {
    const path = this.el.pathInput?.value?.trim() || '';
    this.showPathStatus('Saving...', 'info');
    try {
      const payload = {
        trading: {},
        network: {},
        accounts: [],
        credentials: {},
        mt_terminal_path: path,
      };
      await this.api.saveSettings(payload);
      this.showPathStatus('Path saved', 'success');
    } catch (e) {
      console.error('[MtManager] Failed to save path:', e);
      this.showPathStatus('Failed to save path', 'error');
    }
  }

  async launch() {
    const path = this.el.pathInput?.value?.trim() || '';
    this.showLaunchResult('Launching MetaTrader...', null);
    try {
      const data = await this.api.launchMt(path);
      this.showLaunchResult(data.message, data.success);
    } catch (e) {
      this.showLaunchResult('Error: ' + e.message, false);
    }
  }

  showPathStatus(message, type) {
    if (!this.el.pathStatus) return;
    this.el.pathStatus.textContent = message;
    const colors = {
      success: 'text-green-400',
      error: 'text-red-400',
      info: 'text-blue-400',
    };
    this.el.pathStatus.className = `text-sm ${colors[type] || 'text-gray-400'}`;
    if (type === 'success' || type === 'error') {
      setTimeout(() => { this.el.pathStatus.textContent = ''; }, 3000);
    }
  }

  showLaunchResult(message, success) {
    if (!this.el.launchResult) return;
    this.el.launchResult.textContent = message;
    if (success === true) {
      this.el.launchResult.className = 'mt-3 text-sm text-green-400';
    } else if (success === false) {
      this.el.launchResult.className = 'mt-3 text-sm text-red-400';
    } else {
      this.el.launchResult.className = 'mt-3 text-sm text-gray-300';
    }
  }
}
