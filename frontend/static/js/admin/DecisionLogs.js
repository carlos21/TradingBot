/**
 * Decision Logs Component
 * Manages the decision logs table and filters
 */
const TABLE_ROW = 'hover:bg-accent-500/10 transition-colors';
const BADGE_ENTRY = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-emerald-500/10 text-emerald-400';
const BADGE_BLOCK = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-rose-500/10 text-rose-500';
const BADGE_PENDING = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-amber-400/10 text-amber-400';
const BADGE_REMOVE = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-surface-700 text-slate-300';
const BADGE_INFO = 'inline-flex items-center px-2 py-1 rounded text-xs font-medium bg-accent-500/10 text-accent-400';
const BADGE_LONG = 'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-emerald-500/10 text-emerald-400';
const BADGE_SHORT = 'inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-rose-500/10 text-rose-500';

export class DecisionLogs {
  constructor(apiClient) {
    this.api = apiClient;
    this.logs = [];
    this.events = [];
    this.selectedPair = null;
  }

  setPair(pair) {
    this.selectedPair = pair;
  }

  async load() {
    try {
      console.log('[DecisionLogs] Loading...');
      const eventFilter = document.getElementById('decisions-event-filter')?.value || '';
      const lineIdFilter = document.getElementById('decisions-line-id-filter')?.value || '';
      const limitFilter = document.getElementById('decisions-limit-filter')?.value || 500;

      const result = await this.api.getDecisionLogs(this.selectedPair, eventFilter, lineIdFilter, limitFilter);
      console.log('[DecisionLogs] Got result:', result);
      this.logs = result.logs || [];
      this.events = result.events || [];
      this.render();
      this.renderFilters();
    } catch (error) {
      console.error('[DecisionLogs] Failed to load:', error);
    }
  }

  render() {
    const tbody = document.getElementById('decisions-tbody');
    const emptyMsg = document.getElementById('decisions-empty');
    if (!tbody) return;

    if (!this.logs || this.logs.length === 0) {
      tbody.innerHTML = '';
      if (emptyMsg) emptyMsg.classList.remove('hidden');
      return;
    }
    if (emptyMsg) emptyMsg.classList.add('hidden');

    tbody.innerHTML = this.logs.map(log => {
      const badgeClass = this.getEventBadgeClass(log.event);
      const timeStr = log.bar_time
        ? new Date(log.bar_time * 1000).toLocaleString()
        : (log.created_at || '-');
      return `
        <tr class="${TABLE_ROW}">
          <td class="px-4 py-3 text-sm text-slate-400 whitespace-nowrap">${timeStr}</td>
          <td class="px-4 py-3">
            <span class="${badgeClass}">
              ${log.event || '-'}
            </span>
          </td>
          <td class="px-4 py-3 text-sm font-mono text-slate-300">${log.line_id || '-'}</td>
          <td class="px-4 py-3 text-sm">
            ${log.direction
              ? `<span class="${log.direction === 'long' ? BADGE_LONG : BADGE_SHORT}">${log.direction.toUpperCase()}</span>`
              : '-'
            }
          </td>
          <td class="px-4 py-3 text-sm text-slate-300">${log.trigger_name || '-'}</td>
          <td class="px-4 py-3 text-sm text-slate-300">${log.filter_name || '-'}</td>
          <td class="px-4 py-3 text-sm text-slate-400 max-w-xs truncate" title="${this.escapeHtml(log.reason || '')}">${log.reason || '-'}</td>
          <td class="px-4 py-3 text-sm text-slate-400 max-w-xs truncate" title="${this.escapeHtml(log.details || '')}">${log.details || '-'}</td>
        </tr>
      `;
    }).join('');
  }

  renderFilters() {
    const select = document.getElementById('decisions-event-filter');
    if (!select || this.events.length === 0) return;

    const currentVal = select.value;
    // Keep the "All Events" option and rebuild the rest
    select.innerHTML = '<option value="">All Events</option>' +
      this.events.map(ev => `<option value="${ev}">${ev}</option>`).join('');
    select.value = currentVal;
  }

  bindFilters() {
    const eventSelect = document.getElementById('decisions-event-filter');
    const lineIdInput = document.getElementById('decisions-line-id-filter');
    const limitSelect = document.getElementById('decisions-limit-filter');
    const refreshBtn = document.getElementById('refresh-decisions-btn');

    const reload = () => this.load();

    if (eventSelect) eventSelect.addEventListener('change', reload);
    if (lineIdInput) lineIdInput.addEventListener('input', this.debounce(reload, 300));
    if (limitSelect) limitSelect.addEventListener('change', reload);
    if (refreshBtn) refreshBtn.addEventListener('click', reload);
  }

  getEventBadgeClass(event) {
    switch (event) {
      case 'ENTRY':
      case 'LATCH':
      case 'VAT_CROSS_2':
      case 'TSI_CROSS':
        return BADGE_ENTRY;
      case 'FILTER_BLOCK':
      case 'TSI_INVALID':
      case 'VAT_CROSS1_TOO_FAR':
        return BADGE_BLOCK;
      case 'TRIGGER_SKIP':
      case 'LATCH_PENDING':
      case 'TSI_FAST':
        return BADGE_PENDING;
      case 'REMOVE':
        return BADGE_REMOVE;
      case 'VAT_REGIME':
      case 'TSI_RESET':
      case 'VAT_RESET':
      case 'TSI_SWEEP':
        return BADGE_INFO;
      default:
        return BADGE_REMOVE;
    }
  }

  escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  debounce(fn, ms) {
    let timeout;
    return () => {
      clearTimeout(timeout);
      timeout = setTimeout(() => fn(), ms);
    };
  }
}
