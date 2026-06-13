export class StreamHealthPanel {
  constructor(socket) {
    this.socket = socket;
    this.lastHealth = null;
    this.alertLevel = 'ok';
    this.expanded = false;
    this.userCollapsed = false;

    this.wrapper = document.getElementById('streamHealthWrapper');
    this.bar = document.getElementById('streamHealthBar');
    this.panel = document.getElementById('streamHealthPanel');
    if (!this.bar || !this.panel || !this.wrapper) return;

    // Elements
    this.dot = document.getElementById('healthDot');
    this.stateEl = document.getElementById('healthState');
    this.lastBarEl = document.getElementById('healthLastBar');
    this.cachedEl = document.getElementById('healthCached');
    this.alertIcon = document.getElementById('healthAlertIcon');
    this.readyBadge = document.getElementById('healthReadyBadge');
    this.chevron = document.getElementById('healthChevron');

    // Detail elements
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

    this._bindEvents();
    this._loadPreference();
    this._restorePosition();
  }

  _bindEvents() {
    this.bar.addEventListener('click', () => this.toggle());

    this.resyncBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._doRefresh();
    });

    this.checkParityBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._doCheckParity();
    });

    this.socket.on('health_update', (data) => this.update(data));
    this.socket.on('refresh_result', (data) => this._onRefreshResult(data));
    this.socket.on('parity_result', (data) => this._onParityResult(data));

    // Parity modal close handlers
    this.parityModalClose?.addEventListener('click', () => this._closeParityModal());
    this.parityModalCloseBtn?.addEventListener('click', () => this._closeParityModal());
    this.parityModal?.addEventListener('click', (e) => {
      if (e.target === this.parityModal) this._closeParityModal();
    });

    // Drag support
    this._initDrag();
  }

  _loadPreference() {
    try {
      this.userCollapsed = sessionStorage.getItem('healthPanelCollapsed') === 'true';
    } catch {}
  }

  _savePreference() {
    try {
      sessionStorage.setItem('healthPanelCollapsed', String(this.expanded ? false : true));
    } catch {}
  }

  _restorePosition() {
    try {
      const pos = sessionStorage.getItem('healthPanelPos');
      if (pos) {
        const { right, bottom } = JSON.parse(pos);
        this.wrapper.style.right = right;
        this.wrapper.style.bottom = bottom;
        this.wrapper.style.left = 'auto';
        this.wrapper.style.top = 'auto';
      }
    } catch {}
  }

  _savePosition() {
    try {
      sessionStorage.setItem('healthPanelPos', JSON.stringify({
        right: this.wrapper.style.right,
        bottom: this.wrapper.style.bottom,
      }));
    } catch {}
  }

  _initDrag() {
    let isDragging = false;
    let startX, startY, startRight, startBottom;

    const onMouseDown = (e) => {
      // Don't drag if clicking a button inside the bar
      if (e.target.tagName === 'BUTTON' || e.target.closest('button')) return;
      isDragging = true;
      startX = e.clientX;
      startY = e.clientY;

      const rect = this.wrapper.getBoundingClientRect();
      const parentRect = this.wrapper.offsetParent.getBoundingClientRect();
      startRight = parentRect.right - rect.right;
      startBottom = parentRect.bottom - rect.bottom;

      this.wrapper.classList.add('dragging');
      e.preventDefault();
    };

    const onMouseMove = (e) => {
      if (!isDragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;

      const newRight = Math.max(0, startRight - dx);
      const newBottom = Math.max(0, startBottom - dy);

      this.wrapper.style.right = `${newRight}px`;
      this.wrapper.style.bottom = `${newBottom}px`;
      this.wrapper.style.left = 'auto';
      this.wrapper.style.top = 'auto';
    };

    const onMouseUp = () => {
      if (!isDragging) return;
      isDragging = false;
      this.wrapper.classList.remove('dragging');
      this._savePosition();
    };

    this.bar.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
  }

  update(data) {
    if (!this.bar) return;
    this.lastHealth = data;

    const dsState = data.state || 'UNKNOWN';
    const readinessState = data.readiness_state || dsState;
    const readinessReason = data.readiness_reason || '';

    // Only show panel once the platform is connected.
    if (readinessState === 'DISCONNECTED') {
      this.wrapper.classList.add('hidden');
      return;
    }
    this.wrapper.classList.remove('hidden');

    const lastBarTime = data.last_bar_time;
    const barsCached = data.bars_cached ?? 0;
    const hbAge = data.heartbeat_age_sec;
    const duplicates = data.duplicate_count ?? 0;
    const gaps = data.gap_count ?? 0;
    const platformConnected = data.platform_connected;
    const isReady = readinessState === 'READY' || readinessState === 'LIVE';

    // Determine alert level
    let newLevel = 'ok';
    if (readinessState === 'DISCONNECTED' || !platformConnected || (hbAge !== null && hbAge > 90)) {
      newLevel = 'error';
    } else if (!isReady || gaps > 0 || duplicates > 0 || (hbAge !== null && hbAge > 30)) {
      newLevel = 'warn';
    }

    // Compact bar shows the readiness state
    this.stateEl.textContent = readinessState;
    this.lastBarEl.textContent = lastBarTime ? this._fmtTime(lastBarTime) : '--';
    this.cachedEl.textContent = `${barsCached.toLocaleString()}`;

    // Alert icon
    this.alertIcon.classList.toggle('hidden', newLevel === 'ok');

    // Dot color
    this.dot.className = 'w-2.5 h-2.5 rounded-full flex-shrink-0';
    if (newLevel === 'error') this.dot.classList.add('bg-red-500');
    else if (newLevel === 'warn') this.dot.classList.add('bg-yellow-400');
    else this.dot.classList.add('bg-green-500');

    // State text color
    this.stateEl.className = 'font-semibold';
    if (newLevel === 'error') this.stateEl.classList.add('text-red-400');
    else if (newLevel === 'warn') this.stateEl.classList.add('text-yellow-400');
    else this.stateEl.classList.add('text-green-400');

    // Detailed panel
    if (this.detailState) this.detailState.textContent = readinessState;
    if (this.detailLastBar) this.detailLastBar.textContent = lastBarTime ? this._fmtTimeFull(lastBarTime) : '--';
    if (this.detailCached) this.detailCached.textContent = barsCached.toLocaleString();
    if (this.detailHeartbeat) this.detailHeartbeat.textContent = hbAge !== null ? `${hbAge.toFixed(1)}s` : '--';
    if (this.detailDuplicates) this.detailDuplicates.textContent = duplicates.toLocaleString();
    if (this.detailGaps) this.detailGaps.textContent = gaps.toLocaleString();
    if (this.detailTicks) this.detailTicks.textContent = (data.ticks_received ?? 0).toLocaleString();
    if (this.detailBatches) this.detailBatches.textContent = (data.history_batches ?? 0).toLocaleString();
    if (this.detailPlatform) this.detailPlatform.textContent = platformConnected ? 'Connected' : 'Disconnected';

    // Ready badge: green only when the readiness state machine is READY/LIVE.
    if (this.readyBadge) {
      this.readyBadge.classList.remove('hidden', 'bg-green-600', 'text-white', 'bg-red-600');
      if (isReady) {
        this.readyBadge.textContent = 'READY';
        this.readyBadge.classList.add('bg-green-600', 'text-white');
      } else {
        this.readyBadge.textContent = 'NOT READY';
        this.readyBadge.classList.add('bg-red-600', 'text-white');
      }
    }

    // Detail history shows the readiness reason.
    if (this.detailHistory) {
      if (isReady) {
        this.detailHistory.textContent = 'Complete ✅';
        this.detailHistory.className = 'font-mono text-green-400';
      } else {
        const reason = readinessReason || 'Checking...';
        this.detailHistory.textContent = `Incomplete — ${reason}`;
        this.detailHistory.className = 'font-mono text-red-400';
      }
    }

    // Refresh button state is driven by the data-source state.
    const canRefresh = platformConnected && dsState !== 'REFRESHING';
    if (this.resyncBtn) this.resyncBtn.disabled = !canRefresh;
    if (this.checkParityBtn) this.checkParityBtn.disabled = !canRefresh;

    // Auto-expand on new alert (unless user manually collapsed)
    if (newLevel !== 'ok' && newLevel !== this.alertLevel && !this.userCollapsed) {
      this.expand();
    }
    this.alertLevel = newLevel;
  }

  toggle() {
    if (this.expanded) {
      this.collapse();
    } else {
      this.expand();
    }
  }

  expand() {
    if (!this.panel) return;
    this.expanded = true;
    this.panel.classList.remove('hidden');
    if (this.chevron) this.chevron.classList.add('rotate-180');
    this.userCollapsed = false;
    this._savePreference();
  }

  collapse() {
    if (!this.panel) return;
    this.expanded = false;
    this.panel.classList.add('hidden');
    if (this.chevron) this.chevron.classList.remove('rotate-180');
    this.userCollapsed = true;
    this._savePreference();
  }

  _doRefresh(days = null) {
    this.socket.emit('request_refresh', days ? { days } : {});
    if (this.refreshStatus) {
      this.refreshStatus.classList.remove('hidden');
      this.refreshStatus.textContent = 'Requesting refresh...';
    }
  }

  _onRefreshResult(data) {
    if (!this.refreshStatus) return;
    this.refreshStatus.classList.remove('hidden');
    if (data.ok) {
      this.refreshStatus.textContent = 'Refresh requested. Waiting for data...';
      this.refreshStatus.className = 'text-green-400';
    } else {
      this.refreshStatus.textContent = `Refresh failed: ${data.error || 'unknown'}`;
      this.refreshStatus.className = 'text-red-400';
    }
    setTimeout(() => {
      if (this.refreshStatus) this.refreshStatus.classList.add('hidden');
    }, 5000);
  }

  _fmtTime(ts) {
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'America/New_York' });
    } catch {
      return String(ts);
    }
  }

  _fmtTimeFull(ts) {
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', month: 'short', day: 'numeric', timeZone: 'America/New_York' });
    } catch {
      return String(ts);
    }
  }

  _doCheckParity() {
    if (!this.checkParityBtn) return;
    this.checkParityBtn.disabled = true;
    this.checkParityBtn.textContent = 'Checking...';
    this.socket.emit('check_parity');
  }

  _onParityResult(data) {
    // Re-enable button
    if (this.checkParityBtn) {
      this.checkParityBtn.disabled = false;
      this.checkParityBtn.textContent = 'Check Parity';
    }

    // Handle error from service
    if (data.error) {
      this.parityModalSummary.textContent = `Error: ${data.error}`;
      this.parityModalIcon.textContent = '❌';
      this.parityModalAllGood?.classList.add('hidden');
      this.parityModalGaps?.classList.add('hidden');
      this.parityModalCheckedAt.textContent = '';
      this._openParityModal();
      return;
    }

    // Populate header
    const allGood = data.all_good;
    this.parityModalIcon.textContent = allGood ? '✅' : '⚠️';
    this.parityModalSummary.textContent = data.summary || '--';

    // Checked-at timestamp
    if (data.checked_at) {
      const d = new Date(data.checked_at * 1000);
      this.parityModalCheckedAt.textContent = `Checked at ${d.toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/New_York' })} ET`;
    } else {
      this.parityModalCheckedAt.textContent = '';
    }

    if (allGood) {
      this.parityModalAllGood?.classList.remove('hidden');
      this.parityModalGaps?.classList.add('hidden');
    } else {
      this.parityModalAllGood?.classList.add('hidden');
      this.parityModalGaps?.classList.remove('hidden');
      this._renderGaps(data.gaps || []);
    }

    this._openParityModal();
  }

  _renderGaps(gaps) {
    if (!this.parityModalGapsBody) return;
    this.parityModalGapsBody.innerHTML = '';

    for (const g of gaps) {
      const row = document.createElement('tr');
      row.className = g.is_market_closed ? 'text-gray-500' : 'text-gray-300';

      // Time (CDT)
      const timeCell = document.createElement('td');
      timeCell.className = 'px-3 py-2 font-mono text-xs';
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

      // Duration
      const durCell = document.createElement('td');
      durCell.className = 'px-3 py-2 font-mono text-xs';
      durCell.textContent = g.duration_seconds ? `${g.duration_seconds}s` : '—';
      row.appendChild(durCell);

      // Type
      const typeCell = document.createElement('td');
      typeCell.className = 'px-3 py-2';
      const typeBadge = document.createElement('span');
      typeBadge.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold uppercase';
      if (g.gap_type === 'missing') {
        typeBadge.classList.add('bg-red-900/50', 'text-red-400');
        typeBadge.textContent = 'Missing';
      } else if (g.gap_type === 'extra') {
        typeBadge.classList.add('bg-yellow-900/50', 'text-yellow-400');
        typeBadge.textContent = 'Extra';
      } else {
        typeBadge.classList.add('bg-orange-900/50', 'text-orange-400');
        typeBadge.textContent = 'Mismatch';
      }
      typeCell.appendChild(typeBadge);
      row.appendChild(typeCell);

      // Details
      const detailCell = document.createElement('td');
      detailCell.className = 'px-3 py-2 text-xs max-w-[200px] break-words';
      detailCell.textContent = g.details || '—';
      row.appendChild(detailCell);

      // Market status
      const marketCell = document.createElement('td');
      marketCell.className = 'px-3 py-2';
      if (g.is_market_closed) {
        const badge = document.createElement('span');
        badge.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-gray-700 text-gray-400';
        badge.textContent = '🌙 Closed';
        marketCell.appendChild(badge);
      } else {
        const badge = document.createElement('span');
        badge.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-green-900/50 text-green-400';
        badge.textContent = 'Open';
        marketCell.appendChild(badge);
      }
      row.appendChild(marketCell);

      this.parityModalGapsBody.appendChild(row);
    }
  }

  _openParityModal() {
    if (this.parityModal) {
      this.parityModal.classList.remove('hidden');
    }
  }

  _closeParityModal() {
    if (this.parityModal) {
      this.parityModal.classList.add('hidden');
    }
  }
}
