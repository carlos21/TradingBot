import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { NtManager } from '../../admin/NtManager.js';
import { FakeHttpClient } from '../fakes/FakeHttpClient.js';
import { ApiClient } from '../../admin/ApiClient.js';

function setupDom() {
  document.body.innerHTML = `
    <button id="nt-deploy-btn"></button>
    <div id="nt-deploy-result"></div>
    <div id="nt-detected-dir"></div>
    <input id="nt-creds-user" list="nt-creds-user-list" />
    <datalist id="nt-creds-user-list"></datalist>
    <input id="nt-creds-pass" type="password" />
    <button id="nt-creds-toggle-pass"></button>
    <svg id="nt-eye-icon"></svg>
    <svg id="nt-eye-slash-icon" class="hidden"></svg>
    <button id="nt-save-creds-btn"></button>
    <button id="nt-open-btn"></button>
    <div id="nt-open-result"></div>
    <div id="nt-creds-status"></div>
  `;
}

function buildApi() {
  const http = new FakeHttpClient();
  http.setResponse('GET', '/api/settings', {
    credentials: { username: '', password: '', stored_usernames: [] },
  });
  const api = new ApiClient({ httpClient: http });
  return { http, api };
}

describe('NtManager', () => {
  beforeEach(() => {
    setupDom();
  });

  it('init loads credentials', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', {
      credentials: {
        username: 'user1',
        password: 'pass1',
        stored_usernames: ['user1', 'user2'],
      },
    });

    const manager = new NtManager(api);
    manager.init();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('nt-creds-user').value).toBe('user1');
    expect(document.getElementById('nt-creds-pass').value).toBe('pass1');
  });

  it('togglePassword switches input type and icons', () => {
    const { api } = buildApi();
    const manager = new NtManager(api);
    manager.init();

    document.getElementById('nt-creds-toggle-pass').click();
    expect(document.getElementById('nt-creds-pass').type).toBe('text');

    document.getElementById('nt-creds-toggle-pass').click();
    expect(document.getElementById('nt-creds-pass').type).toBe('password');
  });

  it('saveCredentials posts settings and reloads', async () => {
    const { http, api } = buildApi();
    http.setResponse('GET', '/api/settings', {
      credentials: { username: '', password: '', stored_usernames: [] },
    });
    http.setResponse('POST', '/api/settings', { saved: true });

    const manager = new NtManager(api);
    manager.init();
    await new Promise(r => setTimeout(r, 50));

    document.getElementById('nt-creds-user').value = 'user1';
    document.getElementById('nt-creds-pass').value = 'pass1';
    document.getElementById('nt-save-creds-btn').click();

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests).toContainEqual({
      method: 'POST',
      url: '/api/settings',
      data: expect.objectContaining({
        credentials: { username: 'user1', password: 'pass1' },
      }),
    });
    expect(document.getElementById('nt-creds-status').textContent).toBe('Credentials saved');
  });

  it('openNt validates missing credentials', async () => {
    const { api } = buildApi();
    const manager = new NtManager(api);
    manager.init();

    document.getElementById('nt-open-btn').click();

    expect(document.getElementById('nt-open-result').textContent).toBe('Please enter both username and password.');
  });

  it('openNt posts credentials and shows result', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/nt/open', { success: true, message: 'Opened' });

    const manager = new NtManager(api);
    manager.init();

    document.getElementById('nt-creds-user').value = 'user1';
    document.getElementById('nt-creds-pass').value = 'pass1';
    document.getElementById('nt-open-btn').click();

    await new Promise(r => setTimeout(r, 50));

    expect(http.requests).toContainEqual({
      method: 'POST',
      url: '/api/nt/open',
      data: { username: 'user1', password: 'pass1' },
    });
    expect(document.getElementById('nt-open-result').textContent).toBe('Opened');
  });

  it('deploy shows success result with copied files', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/nt/deploy', {
      success: true,
      message: 'Deployed',
      target_dir: '/nt',
      copied: ['a.cs'],
      errors: [],
    });

    const manager = new NtManager(api);
    manager.init();

    document.getElementById('nt-deploy-btn').click();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('nt-deploy-result').textContent).toContain('Deployed');
    expect(document.getElementById('nt-deploy-result').textContent).toContain('Copied: a.cs');
    expect(document.getElementById('nt-detected-dir').textContent).toBe('Target: /nt');
  });

  it('deploy shows error result', async () => {
    const { http, api } = buildApi();
    http.setResponse('POST', '/api/nt/deploy', () => { throw new Error('deploy failed'); });

    const manager = new NtManager(api);
    manager.init();

    document.getElementById('nt-deploy-btn').click();
    await new Promise(r => setTimeout(r, 50));

    expect(document.getElementById('nt-deploy-result').textContent).toContain('deploy failed');
  });

  it('showCredsStatus clears after timeout', async () => {
    vi.useFakeTimers();
    const { api } = buildApi();
    const manager = new NtManager(api);
    manager.init();

    manager.showCredsStatus('Saved', 'success');
    expect(document.getElementById('nt-creds-status').textContent).toBe('Saved');

    vi.advanceTimersByTime(4000);
    expect(document.getElementById('nt-creds-status').textContent).toBe('');
  });
});
