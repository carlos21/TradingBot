/**
 * Displays configured NT accounts in the chart header.
 * Shows warning banner when no accounts are configured.
 */
export class NtAccountsDisplay {
  constructor(socket) {
    this.socket = socket;
    this.accounts = [];
    this.el = {
      display: document.getElementById('ntAccountsDisplay'),
      warning: document.getElementById('ntAccountsWarning'),
      liveIndicator: document.getElementById('liveIndicator'),
      testTradeControls: document.getElementById('testTradeControls'),
    };
  }

  init() {
    this.loadAccounts();
    this.socket.on('stream_status', data => {
      if (data.nt_accounts !== undefined) {
        this.render(data.nt_accounts, data.streaming_disabled_reason);
      }
    });
  }

  async loadAccounts() {
    try {
      const res = await fetch('/api/accounts');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const accounts = await res.json();
      this.render(accounts, accounts.length === 0 ? 'No NT accounts configured' : null);
    } catch (e) {
      console.error('[NtAccountsDisplay] Failed to load accounts:', e);
      this.render([], 'Failed to load accounts');
    }
  }

  render(accounts, disabledReason) {
    this.accounts = accounts || [];

    // Render account badges
    if (this.el.display) {
      this.el.display.innerHTML = '';
      if (this.accounts.length > 0) {
        const label = document.createElement('span');
        label.className = 'text-gray-400 mr-1';
        label.textContent = 'NT:';
        this.el.display.appendChild(label);

        for (const acct of this.accounts) {
          const badge = document.createElement('span');
          badge.className = 'px-2 py-0.5 bg-green-700 text-white rounded font-medium';
          badge.textContent = acct.name || acct;
          badge.title = this._formatTooltip(acct);
          this.el.display.appendChild(badge);
        }
      } else {
        const none = document.createElement('span');
        none.className = 'text-gray-500 italic';
        none.textContent = 'No accounts';
        this.el.display.appendChild(none);
      }
    }

    // Toggle warning banner
    if (this.el.warning) {
      if (disabledReason) {
        this.el.warning.classList.remove('hidden');
        this.el.warning.querySelector('a')?.setAttribute('href', '/admin');
      } else {
        this.el.warning.classList.add('hidden');
      }
    }

    // If streaming is disabled, hide live indicator and test controls
    if (disabledReason) {
      if (this.el.liveIndicator) this.el.liveIndicator.classList.add('hidden');
      if (this.el.testTradeControls) this.el.testTradeControls.classList.add('hidden');
    }
  }

  _formatTooltip(acct) {
    if (typeof acct === 'string') return acct;
    const parts = [];
    if (acct.risk_usd != null) parts.push(`$${acct.risk_usd} risk`);
    if (acct.risk_pct != null) parts.push(`${acct.risk_pct}% risk`);
    if (acct.rr_ratio != null) parts.push(`${acct.rr_ratio}:1 RR`);
    return parts.length > 0 ? parts.join(' | ') : acct.name;
  }
}
