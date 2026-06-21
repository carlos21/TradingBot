/**
 * Displays configured NT accounts in the chart header.
 * Renders the first few accounts as inline pills, with a +N overflow
 * indicator that expands to show all accounts on click.
 */
const TEXT_MUTED = 'text-slate-400';
const TEXT_SUBTLE = 'text-slate-500';
const TEXT_HEADING = 'text-slate-100';
const TEXT_BODY = 'text-slate-300';
const BG_SURFACE_700 = 'bg-surface-700';
const BG_SURFACE_800 = 'bg-surface-800';
const HOVER_SURFACE_600 = 'hover:bg-surface-600';
const HOVER_SURFACE_700 = 'hover:bg-surface-700';
const BG_SUCCESS = 'bg-emerald-600';
const BORDER_SURFACE_600 = 'border-surface-600';
const BORDER_SURFACE_700 = 'border-surface-700';

export class NtAccountsDisplay {
  constructor(socket) {
    this.socket = socket;
    this.accounts = [];
    this.el = {
      display: document.getElementById('ntAccountsDisplay'),
      warning: document.getElementById('ntAccountsWarning'),
      testTradeControls: document.getElementById('testTradeControls'),
    };
    this._popover = null;
    this._outsideClickHandler = null;
  }

  init() {
    this.loadAccounts();
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

    if (this.el.display) {
      this.el.display.innerHTML = '';
      this._closePopover();

      if (this.accounts.length > 0) {
        // Label
        const label = document.createElement('span');
        label.className = `${TEXT_MUTED} mr-0.5 text-[10px] uppercase tracking-wider flex-shrink-0`;
        label.textContent = 'NT';
        this.el.display.appendChild(label);

        const maxVisible = 2;
        const visible = this.accounts.slice(0, maxVisible);
        const hidden = this.accounts.slice(maxVisible);

        // Visible account pills
        for (const acct of visible) {
          const badge = document.createElement('span');
          badge.className =
            `px-1.5 py-0.5 ${BG_SUCCESS} ${TEXT_HEADING} rounded text-[11px] font-medium whitespace-nowrap flex-shrink-0`;
          badge.textContent = acct.name || acct;
          badge.title = this._formatTooltip(acct);
          this.el.display.appendChild(badge);
        }

        // Overflow indicator
        if (hidden.length > 0) {
          const overflowBtn = document.createElement('button');
          overflowBtn.className =
            `px-1.5 py-0.5 ${BG_SURFACE_700} ${HOVER_SURFACE_600} ${TEXT_HEADING} rounded text-[11px] font-medium transition-colors flex-shrink-0`;
          overflowBtn.textContent = `+${hidden.length}`;
          overflowBtn.title = `${hidden.length} more account${hidden.length !== 1 ? 's' : ''}`;

          overflowBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            this._togglePopover(overflowBtn);
          });

          this.el.display.appendChild(overflowBtn);
        }
      } else {
        const none = document.createElement('span');
        none.className = `${TEXT_SUBTLE} italic text-xs`;
        none.textContent = 'No accounts';
        this.el.display.appendChild(none);
      }
    }

    // Toggle warning banner
    if (this.el.warning) {
      if (disabledReason) {
        this.el.warning.classList.remove('hidden');
        this.el.warning.querySelector('a')?.setAttribute('href', '/admin/settings');
      } else {
        this.el.warning.classList.add('hidden');
      }
    }

    // If streaming is disabled, hide test controls
    if (disabledReason) {
      if (this.el.testTradeControls) this.el.testTradeControls.classList.add('hidden');
    }
  }

  _togglePopover(anchorBtn) {
    if (this._popover) {
      this._closePopover();
      return;
    }

    const popover = document.createElement('div');
    popover.className =
      `absolute mt-1 w-56 ${BG_SURFACE_800} ${BORDER_SURFACE_600} border rounded shadow-lg z-50 overflow-hidden`;
    // Position it near the anchor
    const rect = anchorBtn.getBoundingClientRect();
    popover.style.position = 'fixed';
    popover.style.left = `${rect.left}px`;
    popover.style.top = `${rect.bottom + 4}px`;

    const header = document.createElement('div');
    header.className =
      `px-3 py-1.5 ${BG_SURFACE_700} ${TEXT_BODY} text-[10px] uppercase tracking-wider font-semibold border-b ${BORDER_SURFACE_600}`;
    header.textContent = `All Accounts (${this.accounts.length})`;
    popover.appendChild(header);

    const list = document.createElement('ul');
    list.className = 'max-h-48 overflow-y-auto';

    for (const acct of this.accounts) {
      const li = document.createElement('li');
      li.className =
        `px-3 py-2 ${HOVER_SURFACE_700} border-b ${BORDER_SURFACE_700} last:border-0`;

      const nameRow = document.createElement('div');
      nameRow.className = `text-xs font-semibold ${TEXT_HEADING}`;
      nameRow.textContent = acct.name || acct;

      const metaRow = document.createElement('div');
      metaRow.className = `text-[10px] ${TEXT_MUTED} mt-0.5`;
      metaRow.textContent = this._formatTooltip(acct);

      li.appendChild(nameRow);
      li.appendChild(metaRow);
      list.appendChild(li);
    }

    popover.appendChild(list);
    document.body.appendChild(popover);
    this._popover = popover;

    this._outsideClickHandler = (e) => {
      if (!popover.contains(e.target) && e.target !== anchorBtn) {
        this._closePopover();
      }
    };
    document.addEventListener('click', this._outsideClickHandler);
  }

  _closePopover() {
    if (this._popover) {
      this._popover.remove();
      this._popover = null;
    }
    if (this._outsideClickHandler) {
      document.removeEventListener('click', this._outsideClickHandler);
      this._outsideClickHandler = null;
    }
  }

  _formatTooltip(acct) {
    if (typeof acct === 'string') return acct;
    const parts = [];
    if (acct.risk_usd != null) parts.push(`$${acct.risk_usd} risk`);
    if (acct.risk_pct != null) parts.push(`${acct.risk_pct}% risk`);
    if (acct.rr_ratio != null) parts.push(`${acct.rr_ratio}:1 RR`);
    return parts.length > 0 ? parts.join(' · ') : acct.name;
  }
}
