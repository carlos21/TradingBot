import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { LineManager } from '../../admin/LineManager.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <button id="add-line-btn"></button>
    <div id="line-modal" class="hidden">
      <h2 id="line-modal-title"></h2>
      <form id="line-form">
        <input id="line-id" />
        <input id="line-price" />
        <button type="submit">Save</button>
      </form>
    </div>
    <button id="cancel-line-modal"></button>
    <table><tbody id="lines-tbody"></tbody></table>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  const api = new ApiClient({ httpClient: http });
  api.setPair('MNQ');
  return { http, api };
}

describe('LineManager', () => {
  let alertSpy;
  let confirmSpy;

  beforeEach(() => {
    setupDom();
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
    confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => true);
  });

  afterEach(() => {
    alertSpy.mockRestore();
    confirmSpy.mockRestore();
  });

  it('loads and renders lines', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', [
      { id: 'L1', price: 4500, creation_date: '2024-01-01T00:00:00Z' },
    ]);

    const manager = new LineManager(api);
    await manager.load();

    const tbody = document.getElementById('lines-tbody');
    expect(tbody.textContent).toContain('4500.00');
  });

  it('renders empty tbody when no lines', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', []);

    const manager = new LineManager(api);
    await manager.load();

    expect(document.getElementById('lines-tbody').children.length).toBe(0);
  });

  it('add line button opens modal', () => {
    const { api } = buildApi();
    const manager = new LineManager(api);

    document.getElementById('add-line-btn').click();

    expect(manager.modal.classList.contains('hidden')).toBe(false);
  });

  it('cancel button closes modal', () => {
    const { api } = buildApi();
    const manager = new LineManager(api);
    manager.openModal();

    document.getElementById('cancel-line-modal').click();

    expect(manager.modal.classList.contains('hidden')).toBe(true);
  });

  it('backdrop click closes modal', () => {
    const { api } = buildApi();
    const manager = new LineManager(api);
    manager.openModal();

    manager.modal.dispatchEvent(new MouseEvent('click', { bubbles: true }));

    expect(manager.modal.classList.contains('hidden')).toBe(true);
  });

  it('form submit creates new line', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', []);
    http.setResponse('POST', '/api/lines', { id: 'L1' });

    const manager = new LineManager(api);
    manager.openModal();
    document.getElementById('line-price').value = '4500';

    const form = document.getElementById('line-form');
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests).toContainEqual({
      method: 'POST',
      url: '/api/lines',
      data: { pair: 'MNQ', price: 4500 },
    });
    expect(manager.modal.classList.contains('hidden')).toBe(true);
  });

  it('form submit updates existing line', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', []);
    http.setResponse('PUT', '/api/lines/L1', { updated: true });

    const manager = new LineManager(api);
    manager.openModal('L1', '4500');

    document.getElementById('line-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await new Promise(r => setTimeout(r, 50));

    expect(http.requests).toContainEqual({ method: 'PUT', url: '/api/lines/L1', data: { price: 4500 } });
  });

  it('saveLine alerts on invalid price', async () => {
    const { api } = buildApi();
    const manager = new LineManager(api);
    manager.openModal();
    document.getElementById('line-price').value = 'abc';

    await manager.saveLine();

    expect(alertSpy).toHaveBeenCalledWith('Please enter a valid price');
  });

  it('deleteLine removes line after confirm', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', [
      { id: 'L1', price: 4500, creation_date: '2024-01-01T00:00:00Z' },
    ]);
    http.setResponse('DELETE', '/api/lines/L1', { deleted: true });

    const manager = new LineManager(api);
    await manager.load();

    document.querySelector('.delete-line-btn').click();

    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('delete this line'));
    expect(http.requests.some(r => r.method === 'DELETE' && r.url === '/api/lines/L1')).toBe(true);
  });

  it('cancelled delete does not call api', async () => {
    confirmSpy.mockReturnValue(false);
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', [
      { id: 'L1', price: 4500, creation_date: '2024-01-01T00:00:00Z' },
    ]);

    const manager = new LineManager(api);
    await manager.load();

    document.querySelector('.delete-line-btn').click();

    expect(http.requests.filter(r => r.method === 'DELETE').length).toBe(0);
  });

  it('edit button opens modal with values', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/lines?pair=MNQ', [
      { id: 'L1', price: 4500, creation_date: '2024-01-01T00:00:00Z' },
    ]);

    const manager = new LineManager(api);
    await manager.load();

    document.querySelector('.edit-line-btn').click();

    expect(document.getElementById('line-id').value).toBe('L1');
    expect(document.getElementById('line-price').value).toBe('4500');
    expect(document.getElementById('line-modal-title').textContent).toBe('Edit Line');
  });

  it('formatDate returns formatted date or dash', () => {
    const { api } = buildApi();
    const manager = new LineManager(api);
    expect(manager.formatDate(null)).toBe('-');
    expect(manager.formatDate('2024-06-15T12:30:00Z')).toContain('Jun');
  });
});
