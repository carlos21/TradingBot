import { describe, it, expect, beforeEach, vi } from 'vitest';
import { StreamingControlsController } from '../../application/StreamingControlsController.js';
import { FakeDomService } from '../fakes/FakeDomService.js';
import { FakeNotification } from '../fakes/FakeNotification.js';

function setupDocument() {
  document.body.innerHTML = `
    <button id="startStreamingBtn">Start Streaming</button>
    <button id="reconnectBtn" class="hidden">Reconnect</button>
    <button id="stopStreamingBtn" class="hidden">Stop Streaming</button>
    <div id="connectionStatus"></div>
  `;
}

function buildControls() {
  const dom = new FakeDomService(document, window);
  const notification = new FakeNotification();
  const controls = new StreamingControlsController(dom, notification);
  return { dom, notification, controls };
}

describe('StreamingControlsController', () => {
  beforeEach(() => {
    setupDocument();
    vi.restoreAllMocks();
  });

  describe('start streaming', () => {
    it('posts to /api/stream/start and shows server message on success', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stream started' }),
      });

      const { controls } = buildControls();
      controls.init();

      const btn = document.getElementById('startStreamingBtn');
      btn.click();
      expect(btn.disabled).toBe(true);
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', { method: 'POST' });
      expect(document.getElementById('connectionStatus').textContent).toBe('Stream started');
    });

    it('shows warning in status and restores button on error response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ message: 'Already running' }),
      });

      const { controls } = buildControls();
      controls.init();

      const btn = document.getElementById('startStreamingBtn');
      const originalHTML = btn.innerHTML;
      btn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(document.getElementById('connectionStatus').textContent).toBe('⚠️ Already running');
      expect(btn.disabled).toBe(false);
      expect(btn.innerHTML).toBe(originalHTML);
    });

    it('restores button on network error', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network failed'));

      const { controls } = buildControls();
      controls.init();

      const btn = document.getElementById('startStreamingBtn');
      btn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(document.getElementById('connectionStatus').textContent).toBe('Error: Network failed');
      expect(btn.disabled).toBe(false);
    });
  });

  describe('reconnect', () => {
    it('posts to /api/stream/start and hides button while in flight', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Reconnected' }),
      });

      const { controls } = buildControls();
      controls.init();

      const reconnectBtn = document.getElementById('reconnectBtn');
      reconnectBtn.classList.remove('hidden');
      reconnectBtn.click();
      expect(reconnectBtn.classList.contains('hidden')).toBe(true);
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', { method: 'POST' });
      expect(document.getElementById('connectionStatus').textContent).toBe('Reconnected');
      expect(reconnectBtn.classList.contains('hidden')).toBe(true);
    });

    it('shows reconnect button again on error response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ message: 'Failed' }),
      });

      const { controls } = buildControls();
      controls.init();

      const reconnectBtn = document.getElementById('reconnectBtn');
      reconnectBtn.classList.remove('hidden');
      reconnectBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(document.getElementById('connectionStatus').textContent).toBe('⚠️ Failed');
      expect(reconnectBtn.classList.contains('hidden')).toBe(false);
    });
  });

  describe('stop streaming', () => {
    it('posts to /api/stream/stop without alerting on success', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stopped' }),
      });

      const { controls, notification } = buildControls();
      controls.init();

      const stopBtn = document.getElementById('stopStreamingBtn');
      stopBtn.click();
      expect(stopBtn.disabled).toBe(true);
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/stop', { method: 'POST' });
      expect(notification.alerts).toHaveLength(0);
      expect(stopBtn.disabled).toBe(false);
    });

    it('alerts and restores button on failure response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ message: 'Gateway busy' }),
      });

      const { controls, notification } = buildControls();
      controls.init();

      const stopBtn = document.getElementById('stopStreamingBtn');
      const originalHTML = stopBtn.innerHTML;
      stopBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Failed to stop streaming: Gateway busy');
      expect(stopBtn.disabled).toBe(false);
      expect(stopBtn.innerHTML).toBe(originalHTML);
    });

    it('alerts on network error', async () => {
      global.fetch = vi.fn().mockRejectedValue(new Error('Network down'));

      const { controls, notification } = buildControls();
      controls.init();

      const stopBtn = document.getElementById('stopStreamingBtn');
      stopBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Failed to stop streaming: Network down');
      expect(stopBtn.disabled).toBe(false);
    });
  });

  describe('init with missing elements', () => {
    it('does not crash when buttons are absent from the dom', () => {
      document.body.innerHTML = '';
      const { controls } = buildControls();
      expect(() => controls.init()).not.toThrow();
    });
  });
});
