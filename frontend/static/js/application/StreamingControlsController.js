/**
 * Owns the live-streaming lifecycle UI: Start Streaming, Reconnect, and
 * Stop Streaming buttons.
 *
 * Single responsibility: translate button clicks into streaming lifecycle
 * API calls.  Visibility of these buttons is driven separately by
 * ChartSocketController socket events (gateway_started / gateway_stopped /
 * platform_connected / stream_status), mirroring how the connection overlay
 * is managed.
 */
export class StreamingControlsController {
  constructor(dom, notification) {
    this.dom = dom;
    this.notification = notification;

    this.startStreamingBtn = null;
    this.reconnectBtn = null;
    this.stopStreamingBtn = null;
  }

  init() {
    this.startStreamingBtn = this.dom.getElementById('startStreamingBtn');
    this.reconnectBtn = this.dom.getElementById('reconnectBtn');
    this.stopStreamingBtn = this.dom.getElementById('stopStreamingBtn');
    this._bindStartEvents();
    this._bindStopEvents();
  }

  _setConnectionStatus(text) {
    const statusEl = this.dom.getElementById('connectionStatus');
    if (statusEl) statusEl.textContent = text;
  }

  _setStreamingLoading(loading) {
    if (!this.startStreamingBtn) return;
    if (loading) {
      this.startStreamingBtn.disabled = true;
      this.startStreamingBtn.classList.add('opacity-50', 'cursor-not-allowed');
      this._originalStreamingBtnHTML = this.startStreamingBtn.innerHTML;
      this.startStreamingBtn.innerHTML = `
        <svg class="animate-spin w-5 h-5 mr-2" fill="none" viewBox="0 0 24 24">
          <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
          <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
        </svg>
        <span>Starting…</span>
      `;
    } else {
      this.startStreamingBtn.disabled = false;
      this.startStreamingBtn.classList.remove('opacity-50', 'cursor-not-allowed');
      if (this._originalStreamingBtnHTML) {
        this.startStreamingBtn.innerHTML = this._originalStreamingBtnHTML;
      }
    }
  }

  _setStoppingLoading(loading) {
    if (!this.stopStreamingBtn) return;
    this.stopStreamingBtn.disabled = loading;
    this.stopStreamingBtn.classList.toggle('opacity-50', loading);
    this.stopStreamingBtn.classList.toggle('cursor-not-allowed', loading);
    if (loading) {
      this._originalStopBtnHTML = this.stopStreamingBtn.innerHTML;
      this.stopStreamingBtn.innerHTML = '<span>Stopping…</span>';
    } else if (this._originalStopBtnHTML) {
      this.stopStreamingBtn.innerHTML = this._originalStopBtnHTML;
    }
  }

  async _startStreaming() {
    this._setConnectionStatus('Starting ZeroMQ gateway…');
    this._setStreamingLoading(true);
    try {
      const resp = await fetch('/api/stream/start', { method: 'POST' });
      const data = await resp.json();
      if (!resp.ok) {
        this._setConnectionStatus('⚠️ ' + (data.message || 'Error starting stream'));
        this._setStreamingLoading(false);
      } else {
        this._setConnectionStatus(data.message || 'Starting…');
      }
    } catch (err) {
      this._setConnectionStatus('Error: ' + err.message);
      this._setStreamingLoading(false);
    }
  }

  _bindStartEvents() {
    if (this.startStreamingBtn) {
      this.dom.addEventListener(this.startStreamingBtn, 'click', () => this._startStreaming());
    }

    if (this.reconnectBtn) {
      this.dom.addEventListener(this.reconnectBtn, 'click', async () => {
        this._setConnectionStatus('Reconnecting…');
        this.reconnectBtn.classList.add('hidden');
        try {
          const resp = await fetch('/api/stream/start', { method: 'POST' });
          const data = await resp.json();
          if (!resp.ok) {
            this._setConnectionStatus('⚠️ ' + (data.message || 'Error starting stream'));
            this.reconnectBtn.classList.remove('hidden');
          } else {
            this._setConnectionStatus(data.message || 'Starting…');
          }
        } catch (err) {
          this._setConnectionStatus('Error: ' + err.message);
          this.reconnectBtn.classList.remove('hidden');
        }
      });
    }
  }

  _bindStopEvents() {
    if (!this.stopStreamingBtn) return;
    this.dom.addEventListener(this.stopStreamingBtn, 'click', async () => {
      this._setStoppingLoading(true);
      try {
        const resp = await fetch('/api/stream/stop', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
          // The overlay (and its connectionStatus element) is hidden while
          // streaming, so surface stop failures via the notification port.
          this.notification.alert('Failed to stop streaming: ' + (data.message || resp.status));
        }
        // Success path: the 'gateway_stopped' socket event drives the UI
        // (overlay shown, this button hidden) via ChartSocketController.
      } catch (err) {
        this.notification.alert('Failed to stop streaming: ' + err.message);
      } finally {
        this._setStoppingLoading(false);
      }
    });
  }
}
