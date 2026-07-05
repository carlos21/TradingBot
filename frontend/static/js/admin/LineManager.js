/**
 * Line Manager Component
 * CRUD operations for strategy lines
 */
export class LineManager {
  constructor(apiClient) {
    this.api = apiClient;
    this.lines = [];
    this.modal = document.getElementById('line-modal');
    this.form = document.getElementById('line-form');
    this.modalTitle = document.getElementById('line-modal-title');
    this.lineIdInput = document.getElementById('line-id');
    this.priceInput = document.getElementById('line-price');

    this.setupEventListeners();
  }

  setupEventListeners() {
    // Add line button
    document.getElementById('add-line-btn')?.addEventListener('click', () => {
      this.openModal();
    });

    // Cancel button
    document.getElementById('cancel-line-modal')?.addEventListener('click', () => {
      this.closeModal();
    });

    // Form submit
    this.form?.addEventListener('submit', (e) => {
      e.preventDefault();
      this.saveLine();
    });

    // Close on backdrop click
    this.modal?.addEventListener('click', (e) => {
      if (e.target === this.modal) this.closeModal();
    });
  }

  async load() {
    try {
      this.lines = await this.api.getLines();
      this.render();
    } catch (error) {
      console.error('Failed to load lines:', error);
    }
  }

  render() {
    const tbody = document.getElementById('lines-tbody');
    if (!tbody) return;

    tbody.innerHTML = this.lines.map(line => `
      <tr data-line-id="${line.id}" class="hover:bg-accent-500/10 transition-colors">
        <td class="px-4 py-3 font-semibold text-accent-400">${line.price.toFixed(2)}</td>
        <td class="px-4 py-3 text-sm text-slate-400">${this.formatDate(line.creation_date)}</td>
        <td class="px-4 py-3">
          <button class="edit-line-btn action-btn hover:text-accent-400 mr-1" data-line-id="${line.id}" data-price="${line.price}" title="Edit line">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
          </button>
          <button class="delete-line-btn action-btn hover:text-rose-500" data-line-id="${line.id}" title="Delete line">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
          </button>
        </td>
      </tr>
    `).join('');

    // Add event listeners
    tbody.querySelectorAll('.edit-line-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const lineId = btn.dataset.lineId;
        const price = parseFloat(btn.dataset.price);
        this.openModal(lineId, price);
      });
    });

    tbody.querySelectorAll('.delete-line-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const lineId = btn.dataset.lineId;
        this.deleteLine(lineId);
      });
    });
  }

  openModal(lineId = null, price = '') {
    this.lineIdInput.value = lineId || '';
    this.priceInput.value = price || '';
    this.modalTitle.textContent = lineId ? 'Edit Line' : 'Add Line';
    this.modal.classList.remove('hidden');
    this.priceInput.focus();
  }

  closeModal() {
    this.modal?.classList.add('hidden');
    this.form?.reset();
  }

  async saveLine() {
    const lineId = this.lineIdInput.value;
    const price = parseFloat(this.priceInput.value);

    if (isNaN(price)) {
      alert('Please enter a valid price');
      return;
    }

    try {
      if (lineId) {
        // Update existing
        await this.api.updateLine(lineId, price);
      } else {
        // Create new
        await this.api.addLine(price);
      }
      this.closeModal();
      await this.load();
    } catch (error) {
      console.error('Failed to save line:', error);
      alert('Failed to save line: ' + error.message);
    }
  }

  async deleteLine(lineId) {
    if (!confirm('Are you sure you want to delete this line?')) {
      return;
    }

    try {
      await this.api.deleteLine(lineId);
      await this.load();
    } catch (error) {
      console.error('Failed to delete line:', error);
      alert('Failed to delete line: ' + error.message);
    }
  }

  formatDate(isoDate) {
    if (!isoDate) return '-';
    const date = new Date(isoDate);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}
