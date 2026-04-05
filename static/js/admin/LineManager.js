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
      <tr data-line-id="${line.id}">
        <td class="px-4 py-3 font-mono text-sm">${line.id.substring(0, 8)}...</td>
        <td class="px-4 py-3 font-semibold text-blue-400">${line.price.toFixed(2)}</td>
        <td class="px-4 py-3 text-sm text-gray-400">${this.formatDate(line.creation_date)}</td>
        <td class="px-4 py-3">
          <button class="edit-line-btn text-blue-400 hover:text-blue-300 text-sm mr-3" data-line-id="${line.id}" data-price="${line.price}">
            Edit
          </button>
          <button class="delete-line-btn text-red-400 hover:text-red-300 text-sm" data-line-id="${line.id}">
            Delete
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
