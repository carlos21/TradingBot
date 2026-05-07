export class ControlsView {
  constructor(chartViewer, socket) {
    this.chartViewer = chartViewer;
    this.socket = socket;
  }

  init() {
    this.toggleBtn = document.getElementById('toggleReplayBtn');
    this.stepBtn = document.getElementById('stepBarBtn');
    this.prevBtn = document.getElementById('prevDayBtn');
    this.nextBtn = document.getElementById('nextDayBtn');
    this.tfButtons = Array.from(document.querySelectorAll('[data-timeframe]'));
    this.testLongBtn = document.getElementById('testLongBtn');
    this.testShortBtn = document.getElementById('testShortBtn');
    this.closeAllBtn = document.getElementById('closeAllBtn');
    this.testTradeControls = document.getElementById('testTradeControls');
    this.testDropdownToggle = document.getElementById('testDropdownToggle');
    this.testDropdownMenu = document.getElementById('testDropdownMenu');
    this.startStreamingBtn = document.getElementById('startStreamingBtn');
    this.reconnectBtn = document.getElementById('reconnectBtn');

    this._bindReplayEvents();
    this._bindTimeframeEvents();
    this._bindTestTradeEvents();
    this._bindStreamingEvents();
    this._bindTestDropdown();

    this.socket.on('stream_status', ({ playing, live_mode }) => {
      if (!playing && this.toggleBtn) this.toggleBtn.textContent = 'Play';
      if (live_mode) this._applyLiveMode();
    });

    const defaultBtn = document.querySelector(`[data-timeframe="${this.chartViewer.currentTF}"]`);
    if (defaultBtn) this._setActiveTf(defaultBtn);
  }

  _bindReplayEvents() {
    if (!this.toggleBtn) return;
    this.toggleBtn.addEventListener('click', () => {
      this.chartViewer.toggleReplay();
      this.toggleBtn.textContent = this.chartViewer.isPlaying ? 'Pause' : 'Play';
    });

    this.stepBtn.addEventListener('click', () => {
      this.chartViewer.stepReplay();
      this.toggleBtn.textContent = 'Play';
    });

    this.prevBtn.addEventListener('click', () => this.chartViewer.jumpToDay(-1));
    this.nextBtn.addEventListener('click', () => this.chartViewer.jumpToDay(1));
  }

  _bindTimeframeEvents() {
    this.tfButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        this._setActiveTf(btn);
        this.chartViewer.changeTimeframe(btn.getAttribute('data-timeframe'));
      });
    });
  }

  _setActiveTf(button) {
    this.tfButtons.forEach(b => b.classList.remove('active'));
    button.classList.add('active');
  }

  _bindStreamingEvents() {
    if (this.startStreamingBtn) {
      this.startStreamingBtn.addEventListener('click', async () => {
        const statusEl = document.getElementById('connectionStatus');
        if (statusEl) statusEl.textContent = 'Starting ZeroMQ gateway…';
        try {
          const resp = await fetch('/api/stream/start', { method: 'POST' });
          const data = await resp.json();
          if (statusEl) statusEl.textContent = data.message || 'Starting…';
        } catch (err) {
          if (statusEl) statusEl.textContent = 'Error: ' + err.message;
        }
      });
    }

    if (this.reconnectBtn) {
      this.reconnectBtn.addEventListener('click', async () => {
        const statusEl = document.getElementById('connectionStatus');
        if (statusEl) statusEl.textContent = 'Reconnecting…';
        this.reconnectBtn.classList.add('hidden');
        try {
          const resp = await fetch('/api/stream/start', { method: 'POST' });
          const data = await resp.json();
          if (statusEl) statusEl.textContent = data.message || 'Starting…';
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

    this.testDropdownToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      this.testDropdownMenu.classList.toggle('hidden');
    });

    // Close when clicking outside
    document.addEventListener('click', (e) => {
      if (!this.testDropdownToggle.contains(e.target) && !this.testDropdownMenu.contains(e.target)) {
        closeMenu();
      }
    });

    // Close when clicking a menu item
    this.testDropdownMenu.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', closeMenu);
    });
  }

  _bindTestTradeEvents() {
    if (!this.testLongBtn || !this.testShortBtn) return;

    this.testLongBtn.addEventListener('click', async () => {
      try {
        const resp = await fetch('/api/trades/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pair: this.chartViewer.pair, direction: 'long' })
        });
        const contentType = resp.headers.get('content-type') || '';
        let data = {};
        if (contentType.includes('application/json')) {
          data = await resp.json();
        } else {
          data = { error: (await resp.text()).trim() || resp.statusText };
        }
        if (resp.ok) {
          alert('Test Long sent: ' + data.trade_id);
        } else {
          alert('Failed: ' + (data.error || resp.statusText));
        }
      } catch (err) {
        alert('Error: ' + err.message);
      }
    });

    this.testShortBtn.addEventListener('click', async () => {
      try {
        const resp = await fetch('/api/trades/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pair: this.chartViewer.pair, direction: 'short' })
        });
        const contentType = resp.headers.get('content-type') || '';
        let data = {};
        if (contentType.includes('application/json')) {
          data = await resp.json();
        } else {
          data = { error: (await resp.text()).trim() || resp.statusText };
        }
        if (resp.ok) {
          alert('Test Short sent: ' + data.trade_id);
        } else {
          alert('Failed: ' + (data.error || resp.statusText));
        }
      } catch (err) {
        alert('Error: ' + err.message);
      }
    });

    if (this.closeAllBtn) {
      this.closeAllBtn.addEventListener('click', async () => {
        try {
          const resp = await fetch('/api/trades/close-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pair: this.chartViewer.pair })
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
            alert(msg);
          } else {
            alert('Failed: ' + (data.error || resp.statusText));
          }
        } catch (err) {
          alert('Error: ' + err.message);
        }
      });
    }
  }

  _applyLiveMode() {
    document.querySelectorAll('.replay-control').forEach(el => el.style.display = 'none');
    if (this.stepBtn) this.stepBtn.style.display = 'none';
    if (this.toggleBtn) this.toggleBtn.style.display = 'none';

    const liveIndicator = document.getElementById('liveIndicator');
    if (liveIndicator) liveIndicator.classList.remove('hidden');

    if (this.testTradeControls) this.testTradeControls.classList.remove('hidden');
  }
}
