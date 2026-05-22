export class StreamHealthPanel {
  constructor(socket) {
    this.socket = socket;
    this.lastHealth = null;
    this.alertLevel = 'ok';
    this.expanded = false;
    this.userCollapsed = false;

    this.bar = document.getElementById('streamHealthBar');
    this.panel = document.getElementById('streamHealthPanel');
    if (!this.bar || !this.panel) return;

    // Elements
    this.dot = document.getElementById('healthDot');
    this.stateEl = document.getElementById('healthState');
    this.lastBarEl = document.getElementById('healthLastBar');
    this.cachedEl = document.getElementById('healthCached');
    this.alertIcon = document.getElementById('healthAlertIcon');
    this.chevron = document.getElementById('healthChevron');
    this.hint = document.getElementById('healthExpandHint');

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

    this.resyncBtn = document.getElementById('healthResyncBtn');
    this.deepResyncBtn = document.getElementById('healthDeepResyncBtn');
    this.refreshStatus = document.getElementById('healthRefreshStatus');

    this._bindEvents();
    this._loadPreference();
  }

  _bindEvents() {
    this.bar.addEventListener('click', () => this.toggle());

    this.resyncBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      this._doRefresh();
    });

    this.deepResyncBtn?.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!confirm('Force deep re-sync? This will request up to 7 days of history from NinjaTrader.')) return;
      this._doRefresh(7);
    });

    this.socket.on('health_update', (data) => this.update(data));
    this.socket.on('refresh_result', (data) => this._onRefreshResult(data));
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

  update(data) {
    if (!this.bar) return;
    this.lastHealth = data;

    // Show bar once we have data
    this.bar.classList.remove('hidden');

    const state = data.state || 'UNKNOWN';
    const lastBarTime = data.last_bar_time;
    const barsCached = data.bars_cached ?? 0;
    const hbAge = data.heartbeat_age_sec;
    const duplicates = data.duplicate_count ?? 0;
    const gaps = data.gap_count ?? 0;
    const platformConnected = data.platform_connected;

    // Determine alert level
    let newLevel = 'ok';
    if (state !== 'LIVE' || !platformConnected || (hbAge !== null && hbAge > 90)) {
      newLevel = 'error';
    } else if (gaps > 0 || duplicates > 0 || (hbAge !== null && hbAge > 30)) {
      newLevel = 'warn';
    }

    // Compact bar
    this.stateEl.textContent = state;
    this.lastBarEl.textContent = `Last bar: ${lastBarTime ? this._fmtTime(lastBarTime) : '--'}`;
    this.cachedEl.textContent = `Cached: ${barsCached.toLocaleString()}`;

    // Alert icon
    this.alertIcon.classList.toggle('hidden', newLevel === 'ok');

    // Dot color
    this.dot.className = 'w-2.5 h-2.5 rounded-full';
    if (newLevel === 'error') this.dot.classList.add('bg-red-500');
    else if (newLevel === 'warn') this.dot.classList.add('bg-yellow-400');
    else this.dot.classList.add('bg-green-500');

    // State text color
    this.stateEl.className = 'font-semibold';
    if (newLevel === 'error') this.stateEl.classList.add('text-red-400');
    else if (newLevel === 'warn') this.stateEl.classList.add('text-yellow-400');
    else this.stateEl.classList.add('text-green-400');

    // Detailed panel
    if (this.detailState) this.detailState.textContent = state;
    if (this.detailLastBar) this.detailLastBar.textContent = lastBarTime ? this._fmtTimeFull(lastBarTime) : '--';
    if (this.detailCached) this.detailCached.textContent = barsCached.toLocaleString();
    if (this.detailHeartbeat) this.detailHeartbeat.textContent = hbAge !== null ? `${hbAge.toFixed(1)}s` : '--';
    if (this.detailDuplicates) this.detailDuplicates.textContent = duplicates.toLocaleString();
    if (this.detailGaps) this.detailGaps.textContent = gaps.toLocaleString();
    if (this.detailTicks) this.detailTicks.textContent = (data.ticks_received ?? 0).toLocaleString();
    if (this.detailBatches) this.detailBatches.textContent = (data.history_batches ?? 0).toLocaleString();
    if (this.detailPlatform) this.detailPlatform.textContent = platformConnected ? 'Connected' : 'Disconnected';

    // Refresh buttons state
    const canRefresh = platformConnected && state !== 'REFRESHING';
    if (this.resyncBtn) this.resyncBtn.disabled = !canRefresh;
    if (this.deepResyncBtn) this.deepResyncBtn.disabled = !canRefresh;

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
    if (this.hint) this.hint.textContent = 'Click to collapse';
    this.userCollapsed = false;
    this._savePreference();
  }

  collapse() {
    if (!this.panel) return;
    this.expanded = false;
    this.panel.classList.add('hidden');
    if (this.chevron) this.chevron.classList.remove('rotate-180');
    if (this.hint) this.hint.textContent = 'Click for details';
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
}
