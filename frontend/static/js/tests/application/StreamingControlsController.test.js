import { describe, it, expect, beforeEach, vi } from 'vitest';
import { StreamingControlsController } from '../../application/StreamingControlsController.js';
import { StreamingLifecycleController } from '../../application/StreamingLifecycleController.js';
import { StreamingEventType } from '../../domain/streamingLifecycle.js';
import { FakeDomService } from '../fakes/FakeDomService.js';
import { FakeNotification } from '../fakes/FakeNotification.js';

function setupDocument() {
  document.body.innerHTML = `
    <div id="connectionOverlay"></div>
    <button id="startStreamingBtn">Start Streaming</button>
    <button id="reconnectBtn" class="hidden">Reconnect</button>
    <button id="stopStreamingBtn" class="hidden">Stop Streaming</button>
    <div id="connectionStatus"></div>
  `;
}

function buildControls(pair) {
  const dom = new FakeDomService(document, window);
  const notification = new FakeNotification();
  const lifecycle = new StreamingLifecycleController(dom);
  lifecycle.init();
  const controls = new StreamingControlsController(dom, notification, lifecycle, pair);
  return { dom, notification, lifecycle, controls };
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

    it('moves the machine to STREAMING when the platform was already connected', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ status: 'already_connected', message: 'Platform is already connected' }),
      });

      const { controls, lifecycle } = buildControls();
      controls.init();

      document.getElementById('startStreamingBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(lifecycle.getState()).toBe('streaming');
      expect(document.getElementById('stopStreamingBtn').classList.contains('hidden')).toBe(false);
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

  describe('beforeStart hook', () => {
    it('aborts the start when the hook returns false (no fetch, no dispatch)', async () => {
      global.fetch = vi.fn();

      const { controls, lifecycle } = buildControls();
      controls.init();
      controls.beforeStart = () => false;

      document.getElementById('startStreamingBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).not.toHaveBeenCalled();
      expect(lifecycle.getState()).toBe('idle');
    });

    it('proceeds with the normal start when the hook returns true', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stream started' }),
      });

      const { controls } = buildControls();
      controls.init();
      controls.beforeStart = () => true;

      document.getElementById('startStreamingBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/start', { method: 'POST' });
    });
  });

  describe('reconnect', () => {
    function driveToDisconnected(lifecycle) {
      lifecycle.dispatch({ type: StreamingEventType.PLATFORM_CONNECTED });
      lifecycle.dispatch({ type: StreamingEventType.PLATFORM_DISCONNECTED });
    }

    it('posts to /api/stream/start and hides button while in flight', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Reconnected' }),
      });

      const { controls, lifecycle } = buildControls();
      controls.init();
      driveToDisconnected(lifecycle);

      const reconnectBtn = document.getElementById('reconnectBtn');
      expect(reconnectBtn.classList.contains('hidden')).toBe(false);
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

      const { controls, lifecycle } = buildControls();
      controls.init();
      driveToDisconnected(lifecycle);

      const reconnectBtn = document.getElementById('reconnectBtn');
      reconnectBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(document.getElementById('connectionStatus').textContent).toBe('⚠️ Failed');
      expect(reconnectBtn.classList.contains('hidden')).toBe(false);
    });
  });

  describe('stop streaming', () => {
    function driveToStreaming(lifecycle) {
      lifecycle.dispatch({ type: StreamingEventType.PLATFORM_CONNECTED });
    }

    it('posts to /api/stream/stop without alerting on success', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stopped' }),
      });

      const { controls, notification, lifecycle } = buildControls();
      controls.init();
      driveToStreaming(lifecycle);

      const stopBtn = document.getElementById('stopStreamingBtn');
      stopBtn.click();
      expect(stopBtn.disabled).toBe(true);
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/stop', { method: 'POST' });
      expect(notification.alerts).toHaveLength(0);
      // Stop stays busy until the backend confirms via gateway_stopped.
      expect(lifecycle.getState()).toBe('stopping');

      lifecycle.dispatch({ type: StreamingEventType.GATEWAY_STOPPED });
      expect(stopBtn.disabled).toBe(false);
      expect(stopBtn.classList.contains('hidden')).toBe(true);
    });

    it('alerts and restores button on failure response', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ message: 'Gateway busy' }),
      });

      const { controls, notification, lifecycle } = buildControls();
      controls.init();
      driveToStreaming(lifecycle);

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

      const { controls, notification, lifecycle } = buildControls();
      controls.init();
      driveToStreaming(lifecycle);

      const stopBtn = document.getElementById('stopStreamingBtn');
      stopBtn.click();
      await new Promise(r => setTimeout(r, 10));

      expect(notification.alerts[0]).toBe('Failed to stop streaming: Network down');
      expect(stopBtn.disabled).toBe(false);
    });

    it('posts JSON {pair} with Content-Type header when a pair is configured', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stopped' }),
      });

      const { controls, notification, lifecycle } = buildControls('MES');
      controls.init();
      driveToStreaming(lifecycle);

      document.getElementById('stopStreamingBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pair: 'MES' }),
      });
      expect(notification.alerts).toHaveLength(0);
      expect(lifecycle.getState()).toBe('stopping');
    });

    it('posts without a body when no pair is configured (legacy global stop)', async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ message: 'Stopped' }),
      });

      const { controls, lifecycle } = buildControls();
      controls.init();
      driveToStreaming(lifecycle);

      document.getElementById('stopStreamingBtn').click();
      await new Promise(r => setTimeout(r, 10));

      expect(global.fetch).toHaveBeenCalledWith('/api/stream/stop', { method: 'POST' });
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
