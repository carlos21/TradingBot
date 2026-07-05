import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { MtManager } from '../../admin/MtManager.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <input id="mt-path-input" />
    <button id="mt-save-path-btn"></button>
    <button id="mt-launch-btn"></button>
    <div id="mt-launch-result"></div>
    <div id="mt-path-status"></div>
    <button id="mt-deploy-btn"></button>
    <div id="mt-deploy-result"></div>
    <div id="mt-detected-dir"></div>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  http.setResponse('GET', '/api/settings', { mt_terminal_path: '' });
  const api = new ApiClient({ httpClient: http });
  api.setPair('MNQ');
  return { http, api };
}

describe('MtManager', () => {
  beforeEach(() => {
    setupDom();
  });

  it('init loads settings', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', { mt_terminal_path: '/mt/terminal.exe' });

    const manager = new MtManager(api);
    manager.init();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('mt-path-input').value).toBe('/mt/terminal.exe');
  });

  it('savePath posts terminal path', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/settings', { saved: true });

    const manager = new MtManager(api);
    manager.init();

    document.getElementById('mt-path-input').value = '/new/terminal.exe';
    document.getElementById('mt-save-path-btn').click();

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests).toContainEqual({
      method: 'POST',
      url: '/api/settings',
      data: expect.objectContaining({ mt_terminal_path: '/new/terminal.exe' }),
    });
  });

  it('launch posts exe path and shows result', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/mt/launch', { success: true, message: 'Launched' });

    const manager = new MtManager(api);
    manager.init();

    document.getElementById('mt-path-input').value = '/mt/terminal.exe';
    document.getElementById('mt-launch-btn').click();

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests).toContainEqual({
      method: 'POST',
      url: '/api/mt/launch',
      data: { exe_path: '/mt/terminal.exe' },
    });
    expect(document.getElementById('mt-launch-result').textContent).toBe('Launched');
  });

  it('launch handles error', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/mt/launch', () => { throw new Error('launch failed'); });

    const manager = new MtManager(api);
    manager.init();

    document.getElementById('mt-launch-btn').click();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('mt-launch-result').textContent).toContain('launch failed');
  });

  it('deploy shows success result', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/mt/deploy', {
      success: true,
      message: 'Deployed',
      target_dir: '/mt',
      copied: ['a.ex5'],
      errors: [],
    });

    const manager = new MtManager(api);
    manager.init();

    document.getElementById('mt-deploy-btn').click();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('mt-deploy-result').textContent).toContain('Deployed');
    expect(document.getElementById('mt-detected-dir').textContent).toBe('Target: /mt');
  });

  it('showPathStatus clears after timeout', async () => {
    vi.useFakeTimers();
    const { api } = buildApi();
    const manager = new MtManager(api);
    manager.init();

    manager.showPathStatus('Saved', 'success');
    expect(document.getElementById('mt-path-status').textContent).toBe('Saved');

    vi.advanceTimersByTime(4000);
    expect(document.getElementById('mt-path-status').textContent).toBe('');
  });
});
