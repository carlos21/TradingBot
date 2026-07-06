import { describe, it, expect, beforeEach } from 'vitest';
import { StreamHealthModel } from '../../stream_health/StreamHealthModel.js';

describe('StreamHealthModel', () => {
  let model;

  beforeEach(() => {
    model = new StreamHealthModel();
  });

  it('initializes with default values', () => {
    expect(model.currentState).toBe('DISCONNECTED');
    expect(model.previousState).toBeNull();
    expect(model.reason).toBe('Initializing');
    expect(model.readinessPercent).toBe(0);
    expect(model.phase).toBeNull();
    expect(model.phaseProgress).toBeNull();
    expect(model.lastBarTime).toBeNull();
    expect(model.barsCached).toBe(0);
    expect(model.heartbeatAgeSec).toBeNull();
    expect(model.duplicateCount).toBe(0);
    expect(model.gapCount).toBe(0);
    expect(model.ticksReceived).toBe(0);
    expect(model.historyBatches).toBe(0);
    expect(model.platformConnected).toBe(false);
    expect(model.alertLevel).toBe('ok');
    expect(model.transitionHistory).toEqual([]);
  });

  it('updates from a full health_update payload', () => {
    model.updateFromHealth({
      readiness_state: 'READY',
      readiness_reason: 'All systems go',
      readiness_percent: 100,
      phase: 'ready',
      phase_progress: { phase: 'ready', current: 1, total: 1, percent: 100 },
      last_bar_time: 1710000000,
      bars_cached: 5000,
      heartbeat_age_sec: 5,
      duplicate_count: 2,
      gap_count: 1,
      ticks_received: 12345,
      history_batches: 42,
      platform_connected: true,
    });

    expect(model.currentState).toBe('READY');
    expect(model.reason).toBe('All systems go');
    expect(model.readinessPercent).toBe(100);
    expect(model.phase).toBe('ready');
    expect(model.phaseProgress).toEqual({ phase: 'ready', current: 1, total: 1, percent: 100 });
    expect(model.lastBarTime).toBe(1710000000);
    expect(model.barsCached).toBe(5000);
    expect(model.heartbeatAgeSec).toBe(5);
    expect(model.duplicateCount).toBe(2);
    expect(model.gapCount).toBe(1);
    expect(model.ticksReceived).toBe(12345);
    expect(model.historyBatches).toBe(42);
    expect(model.platformConnected).toBe(true);
    expect(model.alertLevel).toBe('warn'); // READY but gaps/duplicates present
  });

  it('falls back to legacy state/reason keys in health_update', () => {
    model.updateFromHealth({ state: 'LIVE', reason: 'Streaming' });
    expect(model.currentState).toBe('LIVE');
    expect(model.reason).toBe('Streaming');
  });

  it('preserves existing values when health_update fields are omitted', () => {
    model.updateFromHealth({ readiness_state: 'WARMING_UP' });
    expect(model.currentState).toBe('WARMING_UP');
    expect(model.reason).toBe('');
    expect(model.readinessPercent).toBe(0);
  });

  it('sets error alert when disconnected', () => {
    model.updateFromHealth({ readiness_state: 'DISCONNECTED', platform_connected: true });
    expect(model.alertLevel).toBe('error');
  });

  it('sets error alert when platform is not connected', () => {
    model.updateFromHealth({ readiness_state: 'READY', platform_connected: false });
    expect(model.alertLevel).toBe('error');
  });

  it('sets error alert when heartbeat is stale', () => {
    model.updateFromHealth({ readiness_state: 'READY', platform_connected: true, heartbeat_age_sec: 120 });
    expect(model.alertLevel).toBe('error');
  });

  it('sets warn alert when heartbeat is moderately stale', () => {
    model.updateFromHealth({ readiness_state: 'READY', platform_connected: true, heartbeat_age_sec: 45 });
    expect(model.alertLevel).toBe('warn');
  });

  it('sets warn alert when not ready', () => {
    model.updateFromHealth({ readiness_state: 'WARMING_UP', platform_connected: true });
    expect(model.alertLevel).toBe('warn');
  });

  it('sets ok alert when ready and healthy', () => {
    model.updateFromHealth({ readiness_state: 'READY', platform_connected: true, heartbeat_age_sec: 10 });
    expect(model.alertLevel).toBe('ok');
  });

  it('records state transitions', () => {
    model.updateFromTransition({ previous_state: 'CONNECTED', state: 'WAITING_FOR_HISTORY', reason: 'History needed' });

    expect(model.currentState).toBe('WAITING_FOR_HISTORY');
    expect(model.previousState).toBe('CONNECTED');
    expect(model.reason).toBe('History needed');
    expect(model.transitionHistory).toHaveLength(1);
    expect(model.transitionHistory[0]).toMatchObject({
      previousState: 'CONNECTED',
      state: 'WAITING_FOR_HISTORY',
      reason: 'History needed',
    });
  });

  it('does not record a transition when state and reason are unchanged', () => {
    model.updateFromTransition({ previous_state: 'DISCONNECTED', state: 'DISCONNECTED', reason: 'Initializing' });
    expect(model.transitionHistory).toHaveLength(0);
  });

  it('caps transition history at 50 entries', () => {
    for (let i = 0; i < 55; i++) {
      model.updateFromTransition({ previous_state: `S${i}`, state: `S${i + 1}`, reason: 'r' });
    }
    expect(model.transitionHistory).toHaveLength(50);
    expect(model.transitionHistory[0].state).toBe('S55');
  });

  it('updates warmup progress with local boost during WARMING_UP', () => {
    model.updateFromHealth({ readiness_state: 'WARMING_UP', readiness_percent: 50 });
    model.updateWarmupProgress({ phase: 'warmup', current: 30, total: 100, percent: 50 });

    expect(model.phase).toBe('warmup');
    expect(model.phaseProgress).toEqual({ phase: 'warmup', current: 30, total: 100, percent: 50 });
    expect(model.readinessPercent).toBe(75); // 60 + round(50 * 0.30)
  });

  it('does not lower readinessPercent during warmup boost', () => {
    model.updateFromHealth({ readiness_state: 'WARMING_UP', readiness_percent: 85 });
    model.updateWarmupProgress({ phase: 'warmup', percent: 0 });
    expect(model.readinessPercent).toBe(85);
  });

  it('updates phase started', () => {
    model.updatePhaseStarted({ phase: 'refreshing' });
    expect(model.phase).toBe('refreshing');
    expect(model.phaseProgress).toEqual({ phase: 'refreshing', current: 0, total: 0, percent: 0 });
  });

  it('updates phase started with missing phase', () => {
    model.updatePhaseStarted({});
    expect(model.phase).toBeNull();
    expect(model.phaseProgress).toEqual({ phase: undefined, current: 0, total: 0, percent: 0 });
  });

  it('updates transition preserving previous reason when none provided', () => {
    model.reason = 'Existing reason';
    model.updateFromTransition({ previous_state: 'CONNECTED', state: 'READY' });
    expect(model.reason).toBe('Existing reason');
  });

  it('defaults warmup phase when not provided', () => {
    model.updateWarmupProgress({ current: 1, total: 10, percent: 10 });
    expect(model.phase).toBe('warmup');
    expect(model.phaseProgress.phase).toBe('warmup');
  });

  it('reports ready only for READY or LIVE states', () => {
    expect(model.isReady()).toBe(false);
    model.currentState = 'READY';
    expect(model.isReady()).toBe(true);
    model.currentState = 'LIVE';
    expect(model.isReady()).toBe(true);
    model.currentState = 'WARMING_UP';
    expect(model.isReady()).toBe(false);
  });
});
