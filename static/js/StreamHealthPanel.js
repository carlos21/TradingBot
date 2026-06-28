import { ReadinessProgressCalculator } from './stream_health/ReadinessProgressCalculator.js';
import { StreamHealthModel } from './stream_health/StreamHealthModel.js';
import { StreamHealthRenderer } from './stream_health/StreamHealthRenderer.js';

const CELL = 'px-3 py-2 font-mono text-xs';
const CELL_WRAP = 'px-3 py-2 text-xs max-w-[200px] break-words';
const BADGE_BASE = 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase';
const BADGE_DANGER_SUBTLE = 'bg-rose-900/50 text-rose-500';
const BADGE_WARNING_SUBTLE = 'bg-amber-900/50 text-amber-400';
const MARKET_CLOSED = `${BADGE_BASE} bg-surface-700 text-slate-400`;
const MARKET_OPEN = `${BADGE_BASE} bg-emerald-900/50 text-emerald-400`;
const ROW_CLOSED = 'text-slate-500';
const ROW_OPEN = 'text-slate-300';

/**
 * Orchestrates the Stream Health floating panel.
 *
 * Responsibilities:
 *   - subscribe to Socket.IO events
 *   - keep the readiness model up to date
 *   - delegate rendering to StreamHealthRenderer
 *   - handle user interactions (toggle, drag, resync, parity check)
 *
 * The model/calculator/renderer split follows SOLID: this class is the
 * composition root and does not contain DOM or policy details.
 */
export class StreamHealthPanel {
  constructor(socket) {
    this.socket = socket;
    this.model = new StreamHealthModel();
    this.calculator = new ReadinessProgressCalculator();
    this.renderer = new StreamHealthRenderer();

    this.userCollapsed = false;
    this.lastAlertLevel = 'ok';

    if (!this.renderer.wrapper) return;

    this._bindEvents();
    this._loadPreference();
    this._restorePosition();
  }

  _bindEvents() {
    this.renderer.bar?.addEventListener('click', () => this.toggle());

    this.renderer.resyncBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._doRefresh();
    });

    this.renderer.checkParityBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._doCheckParity();
    });

    this.socket.on('health_update', (data) => this._onHealthUpdate(data));
    this.socket.on('readiness_changed', (data) => this._onReadinessChanged(data));
    this.socket.on('warmup_progress', (data) => this._onWarmupProgress(data));
    this.socket.on('phase_started', (data) => this._onPhaseStarted(data));
    this.socket.on('refresh_result', (data) => this._onRefreshResult(data));
    this.socket.on('parity_result', (data) => this._onParityResult(data));

    // Parity modal close handlers
    this.renderer.parityModalClose?.addEventListener('click', () => this._closeParityModal());
    this.renderer.parityModalCloseBtn?.addEventListener('click', () => this._closeParityModal());
    this.renderer.parityModal?.addEventListener('click', (e) => {
      if (e.target === this.renderer.parityModal) this._closeParityModal();
    });

    this._initDrag();
  }

  _loadPreference() {
    try {
      this.userCollapsed = sessionStorage.getItem('healthPanelCollapsed') === 'true';
      if (this.userCollapsed) this.renderer.collapse();
    } catch {}
  }

  _savePreference() {
    try {
      sessionStorage.setItem('healthPanelCollapsed', String(!this.renderer.isExpanded()));
    } catch {}
  }

  _restorePosition() {
    try {
      const pos = sessionStorage.getItem('healthPanelPos');
      if (pos) {
        const { right, bottom } = JSON.parse(pos);
        this.renderer.wrapper.style.right = right;
        this.renderer.wrapper.style.bottom = bottom;
        this.renderer.wrapper.style.left = 'auto';
        this.renderer.wrapper.style.top = 'auto';
      }
    } catch {}
  }

  _savePosition() {
    try {
      sessionStorage.setItem('healthPanelPos', JSON.stringify({
        right: this.renderer.wrapper.style.right,
        bottom: this.renderer.wrapper.style.bottom,
      }));
    } catch {}
  }

  _initDrag() {
    let isDragging = false;
    let startX, startY, startRight, startBottom;
    const wrapper = this.renderer.wrapper;

    const onMouseDown = (e) => {
      if (e.target.tagName === 'BUTTON' || e.target.closest('button')) return;
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;

      const rect = wrapper.getBoundingClientRect();
      const parentRect = wrapper.offsetParent.getBoundingClientRect();
      startRight = parentRect.right - rect.right;
      startBottom = parentRect.bottom - rect.bottom;

      wrapper.classList.add('dragging');
      e.preventDefault();
    };

    const onMouseMove = (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const newRight = Math.max(0, startRight - dx);
      const newBottom = Math.max(0, startBottom - dy);

      wrapper.style.right = `${newRight}px`;
      wrapper.style.bottom = `${newBottom}px`;
      wrapper.style.left = 'auto';
      wrapper.style.top = 'auto';
    };

    const onMouseUp = () => {
      if (!isDragging) return;
      isDragging = false;
      wrapper.classList.remove('dragging');
      this._savePosition();
    };

    this.renderer.bar?.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  _onHealthUpdate(data) {
    this.model.updateFromHealth(data);
    this._render();
  }

  _onReadinessChanged(data) {
    this.model.updateFromTransition(data);
    this._render();
  }

  _onWarmupProgress(data) {
    this.model.updateWarmupProgress(data);
    this._render();
  }

  _onPhaseStarted(data) {
    this.model.updatePhaseStarted(data);
    this._render();
  }

  _render() {
    const stepper = this.calculator.computeStepper(this.model);
    const phase = this.calculator.computePhaseDisplay(this.model);
    this.renderer.render(this.model, stepper, phase);

    // Auto-expand on new alert unless user manually collapsed.
    const level = this.model.alertLevel;
    if (level !== 'ok' && level !== this.lastAlertLevel && !this.userCollapsed) {
      this.renderer.expand();
    }
    this.lastAlertLevel = level;
  }

  toggle() {
    if (this.renderer.isExpanded()) {
      this.renderer.collapse();
      this.userCollapsed = true;
    } else {
      this.renderer.expand();
      this.userCollapsed = false;
    }
    this._savePreference();
  }

  _doRefresh(days = null) {
    this.socket.emit('request_refresh', days ? { days } : {});
    this.renderer.setRefreshStatus('Requesting refresh...');
    setTimeout(() => this.renderer.hideRefreshStatus(), 5000);
  }

  _onRefreshResult(data) {
    if (data.ok) {
      this.renderer.setRefreshStatus('Refresh requested. Waiting for data...');
    } else {
      this.renderer.setRefreshStatus(`Refresh failed: ${data.error || 'unknown'}`, true);
    }
    setTimeout(() => this.renderer.hideRefreshStatus(), 5000);
  }

  _doCheckParity() {
    if (!this.renderer.checkParityBtn) return;
    this.renderer.checkParityBtn.disabled = true;
    this.renderer.checkParityBtn.textContent = 'Checking...';
    this.socket.emit('check_parity');
  }

  _onParityResult(data) {
    if (this.renderer.checkParityBtn) {
      this.renderer.checkParityBtn.disabled = false;
      this.renderer.checkParityBtn.textContent = 'Check Parity';
    }

    if (data.error) {
      this.renderer.parityModalIcon.textContent = '❌';
      this.renderer.parityModalSummary.textContent = `Error: ${data.error}`;
      this.renderer.parityModalAllGood?.classList.add('hidden');
      this.renderer.parityModalGaps?.classList.add('hidden');
      this.renderer.parityModalCheckedAt.textContent = '';
      this._openParityModal();
      return;
    }

    const allGood = data.all_good;
    this.renderer.parityModalIcon.textContent = allGood ? '✅' : '⚠️';
    this.renderer.parityModalSummary.textContent = data.summary || '--';

    if (data.checked_at) {
      const d = new Date(data.checked_at * 1000);
      this.renderer.parityModalCheckedAt.textContent = `Checked at ${d.toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/New_York' })} ET`;
    } else {
      this.renderer.parityModalCheckedAt.textContent = '';
    }

    if (allGood) {
      this.renderer.parityModalAllGood?.classList.remove('hidden');
      this.renderer.parityModalGaps?.classList.add('hidden');
    } else {
      this.renderer.parityModalAllGood?.classList.add('hidden');
      this.renderer.parityModalGaps?.classList.remove('hidden');
      this._renderGaps(data.gaps || []);
    }

    this._openParityModal();
  }

  _renderGaps(gaps) {
    const body = this.renderer.parityModalGapsBody;
    if (!body) return;
    body.innerHTML = '';

    for (const g of gaps) {
      const row = document.createElement('tr');
      row.className = g.is_market_closed ? ROW_CLOSED : ROW_OPEN;

      const timeCell = document.createElement('td');
      timeCell.className = CELL;
      const dt = new Date(g.start_time * 1000);
      timeCell.textContent = dt.toLocaleString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        month: 'short',
        day: 'numeric',
        timeZone: 'America/Chicago',
      });
      row.appendChild(timeCell);

      const durCell = document.createElement('td');
      durCell.className = CELL;
      durCell.textContent = g.duration_seconds ? `${g.duration_seconds}s` : '—';
      row.appendChild(durCell);

      const typeCell = document.createElement('td');
      typeCell.className = 'px-3 py-2';
      const typeBadge = document.createElement('span');
      typeBadge.className = BADGE_BASE;
      if (g.gap_type === 'missing') {
        typeBadge.classList.add(...BADGE_DANGER_SUBTLE.split(' '));
        typeBadge.textContent = 'Missing';
      } else if (g.gap_type === 'extra') {
        typeBadge.classList.add(...BADGE_WARNING_SUBTLE.split(' '));
        typeBadge.textContent = 'Extra';
      } else {
        typeBadge.classList.add(...BADGE_WARNING_SUBTLE.split(' '));
        typeBadge.textContent = 'Mismatch';
      }
      typeCell.appendChild(typeBadge);
      row.appendChild(typeCell);

      const detailCell = document.createElement('td');
      detailCell.className = CELL_WRAP;
      detailCell.textContent = g.details || '—';
      row.appendChild(detailCell);

      const marketCell = document.createElement('td');
      marketCell.className = 'px-3 py-2';
      if (g.is_market_closed) {
        const badge = document.createElement('span');
        badge.className = MARKET_CLOSED;
        badge.textContent = '🌙 Closed';
        marketCell.appendChild(badge);
      } else {
        const badge = document.createElement('span');
        badge.className = MARKET_OPEN;
        badge.textContent = 'Open';
        marketCell.appendChild(badge);
      }
      row.appendChild(marketCell);

      body.appendChild(row);
    }
  }

  _openParityModal() {
    if (this.renderer.parityModal) {
      this.renderer.parityModal.classList.remove('hidden');
    }
  }

  _closeParityModal() {
    if (this.renderer.parityModal) {
      this.renderer.parityModal.classList.add('hidden');
    }
  }
}
