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

    this._bindReplayEvents();
    this._bindTimeframeEvents();
    this._bindTestTradeEvents();

    this.socket.on('stream_status', ({ playing, live_mode }) => {
      if (!playing) this.toggleBtn.textContent = 'Play';
      if (live_mode) this._applyLiveMode();
    });

    const defaultBtn = document.querySelector(`[data-timeframe="${this.chartViewer.currentTF}"]`);
    if (defaultBtn) this._setActiveTf(defaultBtn);
  }

  _bindReplayEvents() {
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

    const liveIndicator = document.getElementById('liveIndicator');
    if (liveIndicator) liveIndicator.classList.remove('hidden');

    if (this.testTradeControls) this.testTradeControls.classList.remove('hidden');

    this.toggleBtn.textContent = 'Start';
  }
}
