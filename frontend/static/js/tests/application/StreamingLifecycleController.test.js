import { describe, it, expect, beforeEach, vi } from 'vitest';
import { StreamingLifecycleController } from '../../application/StreamingLifecycleController.js';
import { StreamingEventType, StreamingState } from '../../domain/streamingLifecycle.js';
import { FakeDomService } from '../fakes/FakeDomService.js';

function setupDocument() {
  document.body.innerHTML = `
    <div id="connectionOverlay"></div>
    <button id="startStreamingBtn">Start Streaming</button>
    <button id="reconnectBtn" class="hidden">Reconnect</button>
    <button id="stopStreamingBtn" class="hidden">Stop Streaming</button>
    <div id="connectionStatus"></div>
  `;
}

function build() {
  const dom = new FakeDomService(document, window);
  const lifecycle = new StreamingLifecycleController(dom);
  lifecycle.init();
  return { dom, lifecycle };
}

describe('StreamingLifecycleController.onStateChange', () => {
  beforeEach(() => {
    setupDocument();
    vi.restoreAllMocks();
  });

  it('fires with the new state on every transition', () => {
    const { lifecycle } = build();
    const states = [];
    lifecycle.onStateChange = s => states.push(s);

    lifecycle.dispatch({ type: StreamingEventType.START_CLICKED });
    lifecycle.dispatch({ type: StreamingEventType.PLATFORM_CONNECTED });
    lifecycle.dispatch({ type: StreamingEventType.GATEWAY_STOPPED });

    expect(states).toEqual([
      StreamingState.STARTING,
      StreamingState.STREAMING,
      StreamingState.IDLE,
    ]);
  });

  it('does not fire when the event is a no-op', () => {
    const { lifecycle } = build();
    const states = [];
    lifecycle.onStateChange = s => states.push(s);

    // STOP_CLICKED is invalid in IDLE — no transition, no notification.
    lifecycle.dispatch({ type: StreamingEventType.STOP_CLICKED });
    expect(states).toEqual([]);
  });

  it('a throwing listener does not break dispatch', () => {
    const { lifecycle } = build();
    lifecycle.onStateChange = () => { throw new Error('boom'); };
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

    const next = lifecycle.dispatch({ type: StreamingEventType.START_CLICKED });

    expect(next).toBe(StreamingState.STARTING);
    expect(lifecycle.getState()).toBe(StreamingState.STARTING);
  });
});
