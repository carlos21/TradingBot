/**
 * Binds replay UI controls to the ChartController.
 */
export class ReplayControlsController {
  constructor(controller, socket, domService, notification, tradeService) {
    this.controller = controller;
    this.socket = socket;
    this.dom = domService;
    this.notification = notification;
    this.tradeService = tradeService;

    this.toggleBtn = null;
    this.stepBtn = null;
    this.prevBtn = null;
    this.nextBtn = null;
    this.tfButtons = [];
    this.testLongBtn = null;
    this.testShortBtn = null;
    this.closeAllBtn = null;
    this.testTradeControls = null;
    this.testTradeBtn = null;
    this.testTradeModal = null;
    this.testTradeModalClose = null;
    this.modifySlTradeSelect = null;
    this.modifySlInput = null;
    this.modifySlBtn = null;
    this.startStreamingBtn = null;
    this.reconnectBtn = null;
  }

  init() {
    this._bindElements();
    this._bindReplayEvents();
    this._bindTimeframeEvents();
    this._bindTestTradeEvents();
    this._bindStreamingEvents();
    this._bindTestModal();

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
    this.testTradeBtn = this.dom.getElementById('testTradeBtn');
    this.testTradeModal = this.dom.getElementById('testTradeModal');
    this.testTradeModalClose = this.dom.getElementById('testTradeModalClose');
    this.modifySlTradeSelect = this.dom.getElementById('modifySlTradeSelect');
    this.modifySlInput = this.dom.getElementById('modifySlInput');
    this.modifySlBtn = this.dom.getElementById('modifySlBtn');
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

  _openTestModal() {
    if (!this.testTradeModal) return;
    this.testTradeModal.classList.remove('hidden');
    this._loadOpenTradesForModify();
  }

  _closeTestModal() {
    if (!this.testTradeModal) return;
    this.testTradeModal.classList.add('hidden');
  }

  _bindTestModal() {
    if (!this.testTradeBtn || !this.testTradeModal) return;

    this.dom.addEventListener(this.testTradeBtn, 'click', e => {
      e.stopPropagation();
      this._openTestModal();
    });

    if (this.testTradeModalClose) {
      this.dom.addEventListener(this.testTradeModalClose, 'click', () => this._closeTestModal());
    }

    const doc = this.dom.getDocument();
    this.dom.addEventListener(doc, 'click', e => {
      if (e.target === this.testTradeModal) {
        this._closeTestModal();
      }
    });
  }

  async _loadOpenTradesForModify() {
    if (!this.modifySlTradeSelect) return;
    this.modifySlTradeSelect.innerHTML = '<option value="">Select open trade…</option>';

    try {
      const trades = await this.tradeService.listTrades(this.controller.pair);
      const openTrades = (trades || []).filter(t => t.status === 'open');
      for (const trade of openTrades) {
        const option = this.dom.createElement('option');
        option.value = trade.trade_id;
        option.textContent = `${trade.trade_id} — ${trade.type} @ ${trade.entry} (SL ${trade.stop_loss})`;
        this.modifySlTradeSelect.appendChild(option);
      }
    } catch (err) {
      this.notification.alert('Failed to load open trades: ' + err.message);
    }
  }

  async _sendTestTrade(direction) {
    try {
      const data = await this.tradeService.openTestTrade(this.controller.pair, direction);
      this.notification.alert(
        `Test ${direction.charAt(0).toUpperCase() + direction.slice(1)} sent: ` + data.trade_id
      );
      this._closeTestModal();
    } catch (err) {
      this.notification.alert('Failed: ' + err.message);
    }
  }

  _bindTestTradeEvents() {
    if (!this.testLongBtn || !this.testShortBtn) return;

    this.dom.addEventListener(this.testLongBtn, 'click', () => this._sendTestTrade('long'));
    this.dom.addEventListener(this.testShortBtn, 'click', () => this._sendTestTrade('short'));

    if (this.closeAllBtn) {
      this.dom.addEventListener(this.closeAllBtn, 'click', async () => {
        try {
          const data = await this.tradeService.closeAllTrades(this.controller.pair);
          const count = data.count || 0;
          const failed = data.failed || [];
          let msg = `Close All sent. ${count} trade(s) closed.`;
          if (failed.length > 0) {
            msg += `\nFailed: ${failed.map(f => f.trade_id).join(', ')}`;
          }
          this.notification.alert(msg);
          this._closeTestModal();
        } catch (err) {
          this.notification.alert('Failed: ' + err.message);
        }
      });
    }

    if (this.modifySlBtn) {
      this.dom.addEventListener(this.modifySlBtn, 'click', async () => {
        const tradeId = this.modifySlTradeSelect?.value;
        const newSl = parseFloat(this.modifySlInput?.value);
        if (!tradeId) {
          this.notification.alert('Please select a trade');
          return;
        }
        if (!Number.isFinite(newSl) || newSl <= 0) {
          this.notification.alert('Please enter a valid stop-loss price');
          return;
        }
        try {
          await this.tradeService.modifyStopLoss(tradeId, newSl);
          this.notification.alert(`Updated SL for ${tradeId} to ${newSl}`);
          this.modifySlInput.value = '';
          this._loadOpenTradesForModify();
        } catch (err) {
          this.notification.alert('Failed to update SL: ' + err.message);
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
