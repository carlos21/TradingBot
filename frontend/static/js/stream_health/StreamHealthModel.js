/**
 * Pure data model for the stream health / readiness panel.
 * No DOM or socket dependencies.
 */
export class StreamHealthModel {
  constructor() {
    this.currentState = 'DISCONNECTED';
    this.previousState = null;
    this.reason = 'Initializing';
    this.readinessPercent = 0;
    this.phase = null;
    this.phaseProgress = null;
    this.lastBarTime = null;
    this.barsCached = 0;
    this.heartbeatAgeSec = null;
    this.duplicateCount = 0;
    this.gapCount = 0;
    this.ticksReceived = 0;
    this.historyBatches = 0;
    this.platformConnected = false;
    this.alertLevel = 'ok';
    this.transitionHistory = [];
  }

  /**
   * Update from a periodic health_update payload.
   */
  updateFromHealth(data) {
    this.currentState = data.readiness_state || data.state || 'UNKNOWN';
    this.reason = data.readiness_reason || data.reason || '';
    this.readinessPercent = data.readiness_percent ?? this.readinessPercent;
    this.phase = data.phase ?? this.phase;
    this.phaseProgress = data.phase_progress ?? this.phaseProgress;
    this.lastBarTime = data.last_bar_time ?? this.lastBarTime;
    this.barsCached = data.bars_cached ?? this.barsCached;
    this.heartbeatAgeSec = data.heartbeat_age_sec ?? this.heartbeatAgeSec;
    this.duplicateCount = data.duplicate_count ?? this.duplicateCount;
    this.gapCount = data.gap_count ?? this.gapCount;
    this.ticksReceived = data.ticks_received ?? this.ticksReceived;
    this.historyBatches = data.history_batches ?? this.historyBatches;
    this.platformConnected = data.platform_connected ?? this.platformConnected;
    this.alertLevel = this._computeAlertLevel();
  }

  /**
   * Update from a readiness_changed event and record the transition.
   */
  updateFromTransition(data) {
    const prev = data.previous_state || this.currentState;
    const next = data.state || this.currentState;
    if (prev !== next || data.reason !== this.reason) {
      this.transitionHistory.unshift({
        timestamp: Date.now(),
        previousState: prev,
        state: next,
        reason: data.reason || '',
      });
      // Keep a reasonable cap to avoid unbounded growth.
      if (this.transitionHistory.length > 50) {
        this.transitionHistory.pop();
      }
    }
    this.previousState = prev;
    this.currentState = next;
    this.reason = data.reason || this.reason;
    this.alertLevel = this._computeAlertLevel();
  }

  /**
   * Update from a warmup_progress event.
   */
  updateWarmupProgress(data) {
    this.phase = data.phase || 'warmup';
    this.phaseProgress = {
      phase: data.phase || 'warmup',
      current: data.current ?? 0,
      total: data.total ?? 0,
      percent: data.percent ?? 0,
    };
    // During warmup the backend snapshot may lag; boost % locally.
    if (this.currentState === 'WARMING_UP' && this.readinessPercent < 90) {
      this.readinessPercent = Math.max(
        this.readinessPercent,
        60 + Math.round((this.phaseProgress.percent || 0) * 0.30)
      );
    }
  }

  /**
   * Update from a phase_started event.
   */
  updatePhaseStarted(data) {
    this.phase = data.phase || null;
    this.phaseProgress = { phase: data.phase, current: 0, total: 0, percent: 0 };
  }

  isReady() {
    return this.currentState === 'READY' || this.currentState === 'LIVE';
  }

  _computeAlertLevel() {
    if (
      this.currentState === 'DISCONNECTED' ||
      !this.platformConnected ||
      (this.heartbeatAgeSec !== null && this.heartbeatAgeSec > 90)
    ) {
      return 'error';
    }
    if (
      !this.isReady() ||
      this.gapCount > 0 ||
      this.duplicateCount > 0 ||
      (this.heartbeatAgeSec !== null && this.heartbeatAgeSec > 30)
    ) {
      return 'warn';
    }
    return 'ok';
  }
}
