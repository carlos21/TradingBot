/**
 * Computes stepper metadata and display labels from a StreamHealthModel.
 * Pure policy class with no DOM dependencies.
 */
export class ReadinessProgressCalculator {
  constructor() {
    // Ordered list of readiness states shown in the stepper.
    // Some backend states are collapsed into user-friendly labels.
    this.steps = [
      { key: 'DISCONNECTED', label: 'Disconnected' },
      { key: 'CONNECTED', label: 'Connected' },
      { key: 'WAITING_FOR_HISTORY', label: 'History' },
      { key: 'REFRESHING', label: 'Refreshing' },
      { key: 'WARMING_UP', label: 'Warmup' },
      { key: 'READY', label: 'Ready' },
      { key: 'LIVE', label: 'Live' },
    ];

    this.stepIndex = new Map(this.steps.map((s, i) => [s.key, i]));
  }

  /**
   * Return stepper items with status: pending | active | completed | error.
   */
  computeStepper(model) {
    const currentIdx = this.stepIndex.get(model.currentState) ?? -1;

    return this.steps.map((step, idx) => {
      let status = 'pending';
      if (step.key === model.currentState) {
        status = model.alertLevel === 'error' ? 'error' : 'active';
      } else if (idx < currentIdx) {
        status = 'completed';
      }
      return { ...step, status };
    });
  }

  /**
   * Return a user-facing phase label and progress info for the active phase.
   */
  computePhaseDisplay(model) {
    const state = model.currentState;
    const phase = model.phase;
    const progress = model.phaseProgress;
    const reason = model.reason || '';
    const isStaleReason = this._isStaleReason(reason);

    if (state === 'REFRESHING' || phase === 'refreshing') {
      return {
        title: 'Loading historical bars...',
        determinate: false,
        current: 0,
        total: 0,
        percent: model.readinessPercent,
        reason,
      };
    }

    if (state === 'WARMING_UP' || phase === 'warmup') {
      if (isStaleReason) {
        return {
          title: 'Waiting for fresh market data',
          determinate: false,
          current: 0,
          total: 0,
          percent: model.readinessPercent,
          reason,
        };
      }
      const current = progress?.current ?? 0;
      const total = progress?.total ?? 0;
      const percent = progress?.percent ?? 0;
      return {
        title: 'Warming up indicators...',
        determinate: total > 0,
        current,
        total,
        percent,
        reason,
      };
    }

    if (state === 'DEGRADED') {
      return {
        title: 'Degraded — waiting for data quality to recover',
        determinate: false,
        current: 0,
        total: 0,
        percent: model.readinessPercent,
        reason,
      };
    }

    if (state === 'READY') {
      return {
        title: 'Ready for live trading',
        determinate: true,
        current: 100,
        total: 100,
        percent: 100,
        reason,
      };
    }

    if (state === 'LIVE') {
      return {
        title: 'Live trading active',
        determinate: true,
        current: 100,
        total: 100,
        percent: 100,
        reason,
      };
    }

    if (state === 'WAITING_FOR_HISTORY') {
      const title = isStaleReason
        ? 'Waiting for fresh market data'
        : 'Waiting for historical data...';
      return {
        title,
        determinate: false,
        current: 0,
        total: 0,
        percent: model.readinessPercent,
        reason,
      };
    }

    return {
      title: reason || 'Initializing...',
      determinate: false,
      current: 0,
      total: 0,
      percent: model.readinessPercent,
      reason,
    };
  }

  _isStaleReason(reason) {
    if (!reason) return false;
    const lower = reason.toLowerCase();
    return (
      lower.includes('old') ||
      lower.includes('stale') ||
      lower.includes('closed') ||
      lower.includes('gap') ||
      /last bar is \d+m old/.test(lower)
    );
  }
}
