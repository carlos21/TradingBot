import { describe, it, expect } from 'vitest';
import {
  StreamingState as S,
  StreamingEventType as E,
  reduceStreamingState,
  stateFromStatus,
  streamingViewModel,
} from '../../domain/streamingLifecycle.js';

describe('reduceStreamingState', () => {
  it('follows the happy path: IDLE → STARTING → STREAMING → STOPPING → IDLE', () => {
    let state = S.IDLE;
    state = reduceStreamingState(state, { type: E.START_CLICKED });
    expect(state).toBe(S.STARTING);
    // gateway_started does not end the in-flight start (gateway may have
    // already been running); only the platform connecting does.
    state = reduceStreamingState(state, { type: E.GATEWAY_STARTED });
    expect(state).toBe(S.STARTING);
    state = reduceStreamingState(state, { type: E.PLATFORM_CONNECTED });
    expect(state).toBe(S.STREAMING);
    state = reduceStreamingState(state, { type: E.STOP_CLICKED });
    expect(state).toBe(S.STOPPING);
    state = reduceStreamingState(state, { type: E.GATEWAY_STOPPED });
    expect(state).toBe(S.IDLE);
  });

  it('returns to IDLE when starting fails', () => {
    expect(reduceStreamingState(S.STARTING, { type: E.START_FAILED })).toBe(S.IDLE);
  });

  it('returns to IDLE when the gateway stops mid-start', () => {
    expect(reduceStreamingState(S.STARTING, { type: E.GATEWAY_STOPPED })).toBe(S.IDLE);
    expect(reduceStreamingState(S.GATEWAY_UP, { type: E.GATEWAY_STOPPED })).toBe(S.IDLE);
    expect(reduceStreamingState(S.DISCONNECTED, { type: E.GATEWAY_STOPPED })).toBe(S.IDLE);
  });

  it('GATEWAY_UP (gateway already running, e.g. auto-started at boot) lets the user start the platform', () => {
    // Page load sync puts us here; Start must be clickable.
    let state = reduceStreamingState(S.IDLE, {
      type: E.STATUS_SYNC, live_mode: true, gateway_running: true, platform_connected: false,
    });
    expect(state).toBe(S.GATEWAY_UP);
    state = reduceStreamingState(state, { type: E.START_CLICKED });
    expect(state).toBe(S.STARTING);
    state = reduceStreamingState(state, { type: E.PLATFORM_CONNECTED });
    expect(state).toBe(S.STREAMING);
  });

  it('moves to DISCONNECTED when the platform drops while streaming', () => {
    expect(reduceStreamingState(S.STREAMING, { type: E.PLATFORM_DISCONNECTED })).toBe(S.DISCONNECTED);
    expect(reduceStreamingState(S.GATEWAY_UP, { type: E.PLATFORM_DISCONNECTED })).toBe(S.DISCONNECTED);
  });

  it('reconnect click goes to STARTING and reconnect failure returns to DISCONNECTED', () => {
    let state = reduceStreamingState(S.DISCONNECTED, { type: E.RECONNECT_CLICKED });
    expect(state).toBe(S.STARTING);
    state = reduceStreamingState(state, { type: E.RECONNECT_FAILED });
    expect(state).toBe(S.DISCONNECTED);
  });

  it('recovers to STREAMING when the platform reconnects', () => {
    expect(reduceStreamingState(S.DISCONNECTED, { type: E.PLATFORM_CONNECTED })).toBe(S.STREAMING);
  });

  it('allows stopping while waiting for the platform', () => {
    for (const from of [S.STARTING, S.GATEWAY_UP, S.DISCONNECTED]) {
      let state = reduceStreamingState(from, { type: E.STOP_CLICKED });
      expect(state).toBe(S.STOPPING);
      state = reduceStreamingState(state, { type: E.GATEWAY_STOPPED });
      expect(state).toBe(S.IDLE);
    }
  });

  it('returns to STREAMING when stopping fails', () => {
    expect(reduceStreamingState(S.STOPPING, { type: E.STOP_FAILED })).toBe(S.STREAMING);
  });

  it('ignores events that are invalid in the current state', () => {
    expect(reduceStreamingState(S.IDLE, { type: E.STOP_CLICKED })).toBe(S.IDLE);
    expect(reduceStreamingState(S.STREAMING, { type: E.GATEWAY_STARTED })).toBe(S.STREAMING);
    expect(reduceStreamingState(S.IDLE, { type: E.GATEWAY_STOPPED })).toBe(S.IDLE);
    expect(reduceStreamingState(S.IDLE, {})).toBe(S.IDLE);
    expect(reduceStreamingState(S.IDLE, null)).toBe(S.IDLE);
  });

  it('STATUS_SYNC maps the backend status onto the machine from any state', () => {
    const connected = { type: E.STATUS_SYNC, live_mode: true, gateway_running: true, platform_connected: true };
    expect(reduceStreamingState(S.IDLE, connected)).toBe(S.STREAMING);
    expect(reduceStreamingState(S.DISCONNECTED, connected)).toBe(S.STREAMING);

    const stopped = { type: E.STATUS_SYNC, live_mode: true, gateway_running: false, platform_connected: false };
    expect(reduceStreamingState(S.STREAMING, stopped)).toBe(S.IDLE);

    const waiting = { type: E.STATUS_SYNC, live_mode: true, gateway_running: true, platform_connected: false };
    expect(reduceStreamingState(S.IDLE, waiting)).toBe(S.GATEWAY_UP);

    const notLive = { type: E.STATUS_SYNC, live_mode: false, gateway_running: false, platform_connected: false };
    expect(reduceStreamingState(S.STREAMING, notLive)).toBe(S.INACTIVE);
  });

  it('ignores STATUS_SYNC payloads that only carry playing (no live_mode)', () => {
    // Playback-only stream_status events must not reset the lifecycle state.
    expect(reduceStreamingState(S.INACTIVE, { type: E.STATUS_SYNC, playing: true })).toBe(S.INACTIVE);
    expect(reduceStreamingState(S.STREAMING, { type: E.STATUS_SYNC, playing: false })).toBe(S.STREAMING);
  });
});

describe('stateFromStatus', () => {
  it('maps payloads to states', () => {
    expect(stateFromStatus({ live_mode: false })).toBe(S.INACTIVE);
    expect(stateFromStatus({ live_mode: true, platform_connected: true })).toBe(S.STREAMING);
    expect(stateFromStatus({ live_mode: true, gateway_running: true })).toBe(S.GATEWAY_UP);
    expect(stateFromStatus({ live_mode: true })).toBe(S.IDLE);
    expect(stateFromStatus()).toBe(S.IDLE);
  });
});

describe('streamingViewModel', () => {
  it('IDLE: overlay with an enabled Start button, no Stop/Reconnect', () => {
    const vm = streamingViewModel(S.IDLE);
    expect(vm.overlayVisible).toBe(true);
    expect(vm.startBtn).toEqual({ visible: true, enabled: true, busy: false });
    expect(vm.reconnectVisible).toBe(false);
    expect(vm.stopBtn.visible).toBe(false);
  });

  it('STARTING: Start busy/disabled, Stop available to abort a hung launch', () => {
    const vm = streamingViewModel(S.STARTING);
    expect(vm.startBtn).toEqual({ visible: true, enabled: false, busy: true });
    expect(vm.stopBtn).toEqual({ visible: true, enabled: true, busy: false });
  });

  it('GATEWAY_UP: Start enabled so the platform launch can be triggered', () => {
    const vm = streamingViewModel(S.GATEWAY_UP);
    expect(vm.overlayVisible).toBe(true);
    expect(vm.startBtn).toEqual({ visible: true, enabled: true, busy: false });
    expect(vm.stopBtn).toEqual({ visible: true, enabled: true, busy: false });
    expect(vm.reconnectVisible).toBe(false);
  });

  it('STREAMING: overlay hidden, Stop enabled', () => {
    const vm = streamingViewModel(S.STREAMING);
    expect(vm.overlayVisible).toBe(false);
    expect(vm.stopBtn).toEqual({ visible: true, enabled: true, busy: false });
    expect(vm.reconnectVisible).toBe(false);
  });

  it('DISCONNECTED: overlay with Reconnect, Start hidden', () => {
    const vm = streamingViewModel(S.DISCONNECTED);
    expect(vm.overlayVisible).toBe(true);
    expect(vm.reconnectVisible).toBe(true);
    expect(vm.startBtn.visible).toBe(false);
  });

  it('STOPPING: Stop busy/disabled', () => {
    const vm = streamingViewModel(S.STOPPING);
    expect(vm.stopBtn).toEqual({ visible: true, enabled: false, busy: true });
  });

  it('INACTIVE: overlay and Stop hidden', () => {
    const vm = streamingViewModel(S.INACTIVE);
    expect(vm.overlayVisible).toBe(false);
    expect(vm.stopBtn.visible).toBe(false);
  });

  it('falls back to the IDLE view model for unknown states', () => {
    expect(streamingViewModel('bogus')).toEqual(streamingViewModel(S.IDLE));
  });
});
