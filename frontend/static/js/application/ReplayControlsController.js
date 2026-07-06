/**
 * Binds replay UI controls to the ChartController.
 */
export class ReplayControlsController {
  constructor(controller, socket, domService, notification) {
    this.controller = controller;
    this.socket = socket;
    this.dom = domService;
    this.notification = notification;

    this.toggleBtn = null;
    this.stepBtn = null;
    this.prevBtn = null;
    this.nextBtn = null;
    this.tfButtons = [];
    this.testLongBtn = null;
    this.testShortBtn = null;
    this.closeAllBtn = null;
    this.testTradeControls = null;
    this.testDropdownToggle = null;
    this.testDropdownMenu = null;
    this.startStreamingBtn = null;
    this.reconnectBtn = null;
  }

  init() {
    this._bindElements();
    this._bindReplayEvents();
    this._bindTimeframeEvents();
    this._bindTestTradeEvents();
    this._bindStreamingEvents();
    this._bindTestDropdown();

    this.socket.on('stream_status', ({ playing, live_mode }) => {
      if (!playing && this.toggleBtn) this.toggleBtn.textContent = 'Play';
      if (live_mode) this._applyLiveMode();
    });

    const defaultBtn = this.tfButtons.find(
      b => b.getAttribute('data-timeframe') === this.controller.currentTF
    );
    if (defaultBtn) this._setActiveTf(defaultBtn);
  }

  _bindElements() {
    this.toggleBtn = this.dom.getElementById('toggleReplayBtn');
    this.stepBtn = this.dom.getElementById('stepBarBtn');
    this.prevBtn = this.dom.getElementById('prevDayBtn');
    this.nextBtn = this.dom.getElementById('nextDayBtn');
    this.tfButtons = this.dom.querySelectorAll('[data-timeframe]');
    this.testLongBtn = this.dom.getElementById('testLongBtn');
    this.testShortBtn = this.dom.getElementById('testShortBtn');
    this.closeAllBtn = this.dom.getElementById('closeAllBtn');
    this.testTradeControls = this.dom.getElementById('testTradeControls');
    this.testDropdownToggle = this.dom.getElementById('testDropdownToggle');
    this.testDropdownMenu = this.dom.getElementById('testDropdownMenu');
    this.startStreamingBtn = this.dom.getElementById('startStreamingBtn');
    this.reconnectBtn = this.dom.getElementById('reconnectBtn');
  }

  _bindReplayEvents() {
    if (!this.toggleBtn) return;
    this.dom.addEventListener(this.toggleBtn, 'click', () => {
      this.controller.toggleReplay();
      this.toggleBtn.textContent = this.controller.isPlaying ? 'Pause' : 'Play';
    });

    this.dom.addEventListener(this.stepBtn, 'click', () => {
      this.controller.stepReplay();
      this.toggleBtn.textContent = 'Play';
    });

    this.dom.addEventListener(this.prevBtn, 'click', () => this.controller.jumpToDay(-1));
    this.dom.addEventListener(this.nextBtn, 'click', () => this.controller.jumpToDay(1));
  }

  _bindTimeframeEvents() {
    for (const btn of this.tfButtons) {
      this.dom.addEventListener(btn, 'click', () => {
        this._setActiveTf(btn);
        this.controller.changeTimeframe(btn.getAttribute('data-timeframe'));
      });
    }
  }

  _setActiveTf(button) {
    for (const b of this.tfButtons) b.classList.remove('active');
    button.classList.add('active');
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

  _bindStreamingEvents() {
    if (this.startStreamingBtn) {
      this.dom.addEventListener(this.startStreamingBtn, 'click', async () => {
        const statusEl = this.dom.getElementById('connectionStatus');
        if (statusEl) statusEl.textContent = 'Starting ZeroMQ gateway…';
        this._setStreamingLoading(true);
        try {
          const resp = await fetch('/api/stream/start', { method: 'POST' });
          const data = await resp.json();
          if (!resp.ok) {
            if (statusEl) statusEl.textContent = '⚠️ ' + (data.message || 'Error starting stream');
            this._setStreamingLoading(false);
          } else {
            if (statusEl) statusEl.textContent = data.message || 'Starting…';
          }
        } catch (err) {
          if (statusEl) statusEl.textContent = 'Error: ' + err.message;
          this._setStreamingLoading(false);
        }
      });
    }

    if (this.reconnectBtn) {
      this.dom.addEventListener(this.reconnectBtn, 'click', async () => {
        const statusEl = this.dom.getElementById('connectionStatus');
        if (statusEl) statusEl.textContent = 'Reconnecting…';
        this.reconnectBtn.classList.add('hidden');
        try {
          const resp = await fetch('/api/stream/start', { method: 'POST' });
          const data = await resp.json();
          if (!resp.ok) {
            if (statusEl) statusEl.textContent = '⚠️ ' + (data.message || 'Error starting stream');
            this.reconnectBtn.classList.remove('hidden');
          } else {
            if (statusEl) statusEl.textContent = data.message || 'Starting…';
          }
        } catch (err) {
          if (statusEl) statusEl.textContent = 'Error: ' + err.message;
          this.reconnectBtn.classList.remove('hidden');
        }
      });
    }
  }

  _bindTestDropdown() {
    if (!this.testDropdownToggle || !this.testDropdownMenu) return;

    const closeMenu = () => this.testDropdownMenu.classList.add('hidden');

    this.dom.addEventListener(this.testDropdownToggle, 'click', e => {
      e.stopPropagation();
      this.testDropdownMenu.classList.toggle('hidden');
    });

    const doc = this.dom.getDocument();
    this.dom.addEventListener(doc, 'click', e => {
      if (!this.testDropdownToggle.contains(e.target) && !this.testDropdownMenu.contains(e.target)) {
        closeMenu();
      }
    });

    for (const btn of this.testDropdownMenu.querySelectorAll('button')) {
      this.dom.addEventListener(btn, 'click', closeMenu);
    }
  }

  async _sendTestTrade(direction) {
    try {
      const resp = await fetch('/api/trades/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pair: this.controller.pair, direction }),
      });
      const contentType = resp.headers.get('content-type') || '';
      let data = {};
      if (contentType.includes('application/json')) {
        data = await resp.json();
      } else {
        data = { error: (await resp.text()).trim() || resp.statusText };
      }
      if (resp.ok) {
        this.notification.alert(
          `Test ${direction.charAt(0).toUpperCase() + direction.slice(1)} sent: ` + data.trade_id
        );
      } else {
        this.notification.alert('Failed: ' + (data.error || resp.statusText));
      }
    } catch (err) {
      this.notification.alert('Error: ' + err.message);
    }
  }

  _bindTestTradeEvents() {
    if (!this.testLongBtn || !this.testShortBtn) return;

    this.dom.addEventListener(this.testLongBtn, 'click', () => this._sendTestTrade('long'));
    this.dom.addEventListener(this.testShortBtn, 'click', () => this._sendTestTrade('short'));

    if (this.closeAllBtn) {
      this.dom.addEventListener(this.closeAllBtn, 'click', async () => {
        try {
          const resp = await fetch('/api/trades/close-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pair: this.controller.pair }),
          });
          const contentType = resp.headers.get('content-type') || '';
          let data = {};
          if (contentType.includes('application/json')) {
            data = await resp.json();
          } else {
            data = { error: (await resp.text()).trim() || resp.statusText };
          }
          if (resp.ok) {
            const count = data.count || 0;
            const failed = data.failed || [];
            let msg = `Close All sent. ${count} trade(s) closed.`;
            if (failed.length > 0) {
              msg += `\nFailed: ${failed.map(f => f.trade_id).join(', ')}`;
            }
            this.notification.alert(msg);
          } else {
            this.notification.alert('Failed: ' + (data.error || resp.statusText));
          }
        } catch (err) {
          this.notification.alert('Error: ' + err.message);
        }
      });
    }
  }

  _applyLiveMode() {
    for (const el of this.dom.querySelectorAll('.replay-control')) {
      el.style.display = 'none';
    }
    if (this.stepBtn) this.stepBtn.style.display = 'none';
    if (this.toggleBtn) this.toggleBtn.style.display = 'none';
    if (this.testTradeControls) this.testTradeControls.classList.remove('hidden');
  }
}
