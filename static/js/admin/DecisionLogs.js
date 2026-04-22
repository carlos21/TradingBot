/**
 * Decision Logs Component
 * Manages the decision logs table and filters
 */
export class DecisionLogs {
  constructor(apiClient) {
    this.api = apiClient;
    this.logs = [];
    this.events = [];
  }

  async load() {
    try {
      console.log('[DecisionLogs] Loading...');
      const eventFilter = document.getElementById('decisions-event-filter')?.value || '';
      const lineIdFilter = document.getElementById('decisions-line-id-filter')?.value || '';
      const limitFilter = document.getElementById('decisions-limit-filter')?.value || 500;

      const result = await this.api.getDecisionLogs(eventFilter, lineIdFilter, limitFilter);
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
        <tr class="hover:bg-gray-700/50 transition-colors">
          <td class="px-4 py-3 text-sm text-gray-400 whitespace-nowrap">${timeStr}</td>
          <td class="px-4 py-3">
            <span class="px-2 py-1 rounded text-xs font-medium ${badgeClass}">
              ${log.event || '-'}
            </span>
          </td>
          <td class="px-4 py-3 text-sm font-mono text-gray-300">${log.line_id || '-'}</td>
          <td class="px-4 py-3 text-sm">
            ${log.direction
              ? `<span class="px-2 py-0.5 rounded text-xs ${log.direction === 'long' ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'}">${log.direction.toUpperCase()}</span>`
              : '-'
            }
          </td>
          <td class="px-4 py-3 text-sm text-gray-300">${log.trigger_name || '-'}</td>
          <td class="px-4 py-3 text-sm text-gray-300">${log.filter_name || '-'}</td>
          <td class="px-4 py-3 text-sm text-gray-400 max-w-xs truncate" title="${this.escapeHtml(log.reason || '')}">${log.reason || '-'}</td>
          <td class="px-4 py-3 text-sm text-gray-400 max-w-xs truncate" title="${this.escapeHtml(log.details || '')}">${log.details || '-'}</td>
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
        return 'bg-green-900 text-green-300';
      case 'FILTER_BLOCK':
      case 'TSI_INVALID':
      case 'VAT_CROSS1_TOO_FAR':
        return 'bg-red-900 text-red-300';
      case 'TRIGGER_SKIP':
      case 'LATCH_PENDING':
      case 'TSI_FAST':
        return 'bg-yellow-900 text-yellow-300';
      case 'REMOVE':
        return 'bg-gray-700 text-gray-300';
      case 'VAT_REGIME':
      case 'TSI_RESET':
      case 'VAT_RESET':
      case 'TSI_SWEEP':
        return 'bg-blue-900 text-blue-300';
      default:
        return 'bg-gray-700 text-gray-300';
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
