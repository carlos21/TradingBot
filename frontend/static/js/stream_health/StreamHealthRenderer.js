/**
 * DOM renderer for the Stream Health panel.
 * No socket or business logic — it only translates a model into markup.
 */
export class StreamHealthRenderer {
  constructor() {
    this.wrapper = document.getElementById('streamHealthWrapper');
    this.bar = document.getElementById('streamHealthBar');
    this.panel = document.getElementById('streamHealthPanel');
    if (!this.bar || !this.panel || !this.wrapper) return;

    this.dot = document.getElementById('healthDot');
    this.stateEl = document.getElementById('healthState');
    this.lastBarEl = document.getElementById('healthLastBar');
    this.cachedEl = document.getElementById('healthCached');
    this.alertIcon = document.getElementById('healthAlertIcon');
    this.readyBadge = document.getElementById('healthReadyBadge');
    this.chevron = document.getElementById('healthChevron');
    this.compactProgress = document.getElementById('healthCompactProgress');

    this.detailState = document.getElementById('detailState');
    this.detailLastBar = document.getElementById('detailLastBar');
    this.detailCached = document.getElementById('detailCached');
    this.detailHeartbeat = document.getElementById('detailHeartbeat');
    this.detailDuplicates = document.getElementById('detailDuplicates');
    this.detailGaps = document.getElementById('detailGaps');
    this.detailTicks = document.getElementById('detailTicks');
    this.detailBatches = document.getElementById('detailBatches');
    this.detailPlatform = document.getElementById('detailPlatform');
    this.detailHistory = document.getElementById('detailHistory');

    this.stepperEl = document.getElementById('readinessStepper');
    this.phaseCardEl = document.getElementById('readinessPhaseCard');
    this.transitionLogEl = document.getElementById('readinessTransitionLog');

    this.resyncBtn = document.getElementById('healthResyncBtn');
    this.checkParityBtn = document.getElementById('healthCheckParityBtn');
    this.refreshStatus = document.getElementById('healthRefreshStatus');

    // Parity modal elements
    this.parityModal = document.getElementById('parityModal');
    this.parityModalIcon = document.getElementById('parityModalIcon');
    this.parityModalSummary = document.getElementById('parityModalSummary');
    this.parityModalAllGood = document.getElementById('parityModalAllGood');
    this.parityModalGaps = document.getElementById('parityModalGaps');
    this.parityModalGapsBody = document.getElementById('parityModalGapsBody');
    this.parityModalCheckedAt = document.getElementById('parityModalCheckedAt');
    this.parityModalClose = document.getElementById('parityModalClose');
    this.parityModalCloseBtn = document.getElementById('parityModalCloseBtn');

    // Style constants
    this.TEXT_DANGER = 'text-rose-500';
    this.TEXT_WARNING = 'text-amber-400';
    this.TEXT_SUCCESS = 'text-emerald-400';
    this.BG_DANGER = 'bg-rose-500';
    this.BG_WARNING = 'bg-amber-400';
    this.BG_SUCCESS = 'bg-emerald-400';
    this.BADGE_SUCCESS = 'bg-emerald-500 text-slate-100';
    this.BADGE_DANGER = 'bg-rose-500 text-slate-100';
  }

  render(model, stepper, phaseDisplay) {
    if (!this.bar) return;

    const isDisconnected = model.currentState === 'DISCONNECTED';
    if (isDisconnected) {
      this.wrapper.classList.add('hidden');
      return;
    }
    this.wrapper.classList.remove('hidden');

    const level = model.alertLevel;

    // Compact bar
    this.stateEl.textContent = model.currentState;
    this.lastBarEl.textContent = model.lastBarTime ? this._fmtTime(model.lastBarTime) : '--';
    this.cachedEl.textContent = model.barsCached.toLocaleString();

    this.alertIcon.classList.toggle('hidden', level === 'ok');

    this.dot.className = 'w-2.5 h-2.5 rounded-full flex-shrink-0';
    this.dot.classList.add(
      level === 'error' ? this.BG_DANGER : level === 'warn' ? this.BG_WARNING : this.BG_SUCCESS
    );

    this.stateEl.className = 'font-semibold';
    this.stateEl.classList.add(
      level === 'error' ? this.TEXT_DANGER : level === 'warn' ? this.TEXT_WARNING : this.TEXT_SUCCESS
    );

    // Mini progress bar in compact bar
    if (this.compactProgress) {
      const pct = Math.min(model.readinessPercent, 100);
      this.compactProgress.style.width = `${pct}%`;
      this.compactProgress.className = `h-1 rounded-full transition-all duration-500 ${
        level === 'error' ? this.BG_DANGER : level === 'warn' ? this.BG_WARNING : this.BG_SUCCESS
      }`;
    }

    // Ready badge
    if (this.readyBadge) {
      this.readyBadge.className = 'hidden px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide';
      if (model.isReady()) {
        this.readyBadge.textContent = 'READY';
        this.readyBadge.classList.add(...this.BADGE_SUCCESS.split(' '));
        this.readyBadge.classList.remove('hidden');
      } else {
        this.readyBadge.textContent = 'NOT READY';
        this.readyBadge.classList.add(...this.BADGE_DANGER.split(' '));
        this.readyBadge.classList.remove('hidden');
      }
    }

    // Detail metrics
    if (this.detailState) this.detailState.textContent = model.currentState;
    if (this.detailLastBar) this.detailLastBar.textContent = model.lastBarTime ? this._fmtTimeFull(model.lastBarTime) : '--';
    if (this.detailCached) this.detailCached.textContent = model.barsCached.toLocaleString();
    if (this.detailHeartbeat) this.detailHeartbeat.textContent = model.heartbeatAgeSec !== null ? `${model.heartbeatAgeSec.toFixed(1)}s` : '--';
    if (this.detailDuplicates) this.detailDuplicates.textContent = model.duplicateCount.toLocaleString();
    if (this.detailGaps) this.detailGaps.textContent = model.gapCount.toLocaleString();
    if (this.detailTicks) this.detailTicks.textContent = model.ticksReceived.toLocaleString();
    if (this.detailBatches) this.detailBatches.textContent = model.historyBatches.toLocaleString();
    if (this.detailPlatform) this.detailPlatform.textContent = model.platformConnected ? 'Connected' : 'Disconnected';

    if (this.detailHistory) {
      if (model.isReady()) {
        this.detailHistory.textContent = 'Complete ✅';
        this.detailHistory.className = `font-mono ${this.TEXT_SUCCESS}`;
      } else {
        const reason = model.reason || 'Checking...';
        this.detailHistory.textContent = `Incomplete — ${reason}`;
        this.detailHistory.className = `font-mono ${this.TEXT_DANGER}`;
      }
    }

    // Stepper
    this._renderStepper(stepper);

    // Phase card
    this._renderPhaseCard(phaseDisplay, model.readinessPercent);

    // Transition log
    this._renderTransitionLog(model.transitionHistory);

    // Button states
    const canRefresh = model.platformConnected && model.currentState !== 'REFRESHING';
    if (this.resyncBtn) this.resyncBtn.disabled = !canRefresh;
    if (this.checkParityBtn) this.checkParityBtn.disabled = !canRefresh;
  }

  _renderStepper(stepper) {
    if (!this.stepperEl) return;
    this.stepperEl.innerHTML = '';

    stepper.forEach((step, idx) => {
      const item = document.createElement('div');
      item.className = 'flex flex-col items-center flex-1 min-w-0';

      const node = document.createElement('div');
      node.className = `w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-bold mb-1 border-2 ${this._stepNodeClasses(step.status)}`;
      node.textContent = this._stepIcon(step.status);

      const label = document.createElement('div');
      label.className = `text-[10px] text-center leading-tight ${this._stepLabelClasses(step.status)}`;
      label.textContent = step.label;

      item.appendChild(node);
      item.appendChild(label);
      this.stepperEl.appendChild(item);

      // Connector line between nodes
      if (idx < stepper.length - 1) {
        const line = document.createElement('div');
        const isCompleted = step.status === 'completed';
        line.className = `flex-1 h-0.5 mx-1 mt-3 ${isCompleted ? 'bg-emerald-500' : 'bg-surface-600'}`;
        this.stepperEl.appendChild(line);
      }
    });
  }

  _stepNodeClasses(status) {
    switch (status) {
      case 'completed':
        return 'bg-emerald-500 border-emerald-500 text-slate-100';
      case 'active':
        return 'bg-surface-800 border-accent-500 text-accent-400 animate-pulse';
      case 'error':
        return 'bg-surface-800 border-rose-500 text-rose-500';
      default:
        return 'bg-surface-800 border-surface-600 text-slate-500';
    }
  }

  _stepLabelClasses(status) {
    switch (status) {
      case 'completed':
        return 'text-emerald-400';
      case 'active':
        return 'text-accent-400 font-semibold';
      case 'error':
        return 'text-rose-500';
      default:
        return 'text-slate-500';
    }
  }

  _stepIcon(status) {
    switch (status) {
      case 'completed':
        return '✓';
      case 'error':
        return '!';
      default:
        return '•';
    }
  }

  _renderPhaseCard(phase, overallPercent) {
    if (!this.phaseCardEl) return;
    this.phaseCardEl.innerHTML = '';

    const title = document.createElement('div');
    title.className = 'text-xs font-semibold text-slate-200 mb-1';
    title.textContent = phase.title;

    const reason = document.createElement('div');
    reason.className = 'text-[10px] text-slate-500 mb-2';
    reason.textContent = phase.reason || '';

    const progressContainer = document.createElement('div');
    progressContainer.className = 'w-full bg-surface-700 rounded-full h-2 overflow-hidden mb-1';

    const progressBar = document.createElement('div');
    const pct = phase.determinate ? phase.percent : overallPercent;
    progressBar.className = `h-2 rounded-full transition-all duration-500 ${
      phase.determinate ? 'bg-accent-500' : 'bg-accent-500 animate-pulse'
    }`;
    progressBar.style.width = `${Math.min(pct, 100)}%`;
    progressContainer.appendChild(progressBar);

    const meta = document.createElement('div');
    meta.className = 'text-[10px] text-slate-400 text-right';
    if (phase.determinate && phase.total > 0) {
      meta.textContent = `${phase.current.toLocaleString()} / ${phase.total.toLocaleString()} (${phase.percent}%)`;
    } else {
      meta.textContent = `${Math.min(overallPercent, 100)}%`;
    }

    this.phaseCardEl.appendChild(title);
    if (phase.reason) this.phaseCardEl.appendChild(reason);
    this.phaseCardEl.appendChild(progressContainer);
    this.phaseCardEl.appendChild(meta);
  }

  _renderTransitionLog(history) {
    if (!this.transitionLogEl) return;
    this.transitionLogEl.innerHTML = '';

    if (history.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'text-slate-500 text-center py-2 text-[10px]';
      empty.textContent = 'No state changes yet';
      this.transitionLogEl.appendChild(empty);
      return;
    }

    history.forEach((entry) => {
      const row = document.createElement('div');
      row.className = 'flex items-start space-x-2 text-[10px] py-1 border-b border-surface-700/50 last:border-0';

      const time = document.createElement('span');
      time.className = 'text-slate-500 flex-shrink-0';
      time.textContent = this._fmtTime(entry.timestamp / 1000);

      const change = document.createElement('span');
      change.className = 'font-mono text-slate-300';
      change.textContent = `${entry.previousState || '—'} → ${entry.state}`;

      const reason = document.createElement('span');
      reason.className = 'text-slate-500 truncate';
      reason.title = entry.reason;
      reason.textContent = entry.reason ? `— ${entry.reason}` : '';

      row.appendChild(time);
      row.appendChild(change);
      row.appendChild(reason);
      this.transitionLogEl.appendChild(row);
    });
  }

  setRefreshStatus(text, isError = false) {
    if (!this.refreshStatus) return;
    this.refreshStatus.classList.remove('hidden');
    this.refreshStatus.textContent = text;
    this.refreshStatus.className = isError ? this.TEXT_DANGER : this.TEXT_SUCCESS;
  }

  hideRefreshStatus() {
    if (this.refreshStatus) this.refreshStatus.classList.add('hidden');
  }

  expand() {
    if (!this.panel) return;
    this.panel.classList.remove('hidden');
    if (this.chevron) this.chevron.classList.add('rotate-180');
  }

  collapse() {
    if (!this.panel) return;
    this.panel.classList.add('hidden');
    if (this.chevron) this.chevron.classList.remove('rotate-180');
  }

  isExpanded() {
    return this.panel && !this.panel.classList.contains('hidden');
  }

  _fmtTime(ts) {
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        timeZone: 'America/New_York',
      });
    } catch {
      return String(ts);
    }
  }

  _fmtTimeFull(ts) {
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        month: 'short',
        day: 'numeric',
        timeZone: 'America/New_York',
      });
    } catch {
      return String(ts);
    }
  }
}
