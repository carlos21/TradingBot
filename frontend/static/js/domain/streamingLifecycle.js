/**
 * Streaming lifecycle state machine (pure domain logic, no DOM).
 *
 * Single source of truth for the streaming-control UI state: the Start /
 * Reconnect / Stop buttons and the connection overlay. Both the click-driven
 * controller (StreamingControlsController) and the socket-driven controller
 * (ChartSocketController) dispatch events into this machine instead of
 * mutating button state directly, so the UI can never get stuck in an
 * inconsistent state (e.g. Start left disabled after the stream stops).
 */

export const StreamingState = Object.freeze({
  /** Nothing running; overlay offers Start Streaming. */
  IDLE: 'idle',
  /** Start/reconnect request in flight; waiting for the platform to connect. */
  STARTING: 'starting',
  /** Gateway running but platform not connected; Start can launch the platform. */
  GATEWAY_UP: 'gateway_up',
  /** Platform connected; live data flowing. */
  STREAMING: 'streaming',
  /** Platform lost while the gateway is up; overlay offers Reconnect. */
  DISCONNECTED: 'disconnected',
  /** Stop request in flight. */
  STOPPING: 'stopping',
  /** Page is not in live mode; streaming controls stay out of the way. */
  INACTIVE: 'inactive',
});

export const StreamingEventType = Object.freeze({
  START_CLICKED: 'start_clicked',
  START_FAILED: 'start_failed',
  RECONNECT_CLICKED: 'reconnect_clicked',
  RECONNECT_FAILED: 'reconnect_failed',
  GATEWAY_STARTED: 'gateway_started',
  PLATFORM_CONNECTED: 'platform_connected',
  PLATFORM_DISCONNECTED: 'platform_disconnected',
  STOP_CLICKED: 'stop_clicked',
  STOP_FAILED: 'stop_failed',
  GATEWAY_STOPPED: 'gateway_stopped',
  /** Payload: { live_mode, gateway_running, platform_connected } */
  STATUS_SYNC: 'status_sync',
});

const S = StreamingState;
const E = StreamingEventType;

const TRANSITIONS = Object.freeze({
  [S.IDLE]: Object.freeze({
    [E.START_CLICKED]: S.STARTING,
    [E.RECONNECT_CLICKED]: S.STARTING,
    [E.GATEWAY_STARTED]: S.GATEWAY_UP,
    [E.PLATFORM_CONNECTED]: S.STREAMING,
  }),
  // STARTING covers the whole in-flight period until the platform connects.
  // The gateway may already be running (it auto-starts with the backend), so
  // GATEWAY_STARTED does not end the in-flight state — only the platform
  // connecting (or a failure/stop) does.
  [S.STARTING]: Object.freeze({
    [E.GATEWAY_STARTED]: S.STARTING,
    [E.PLATFORM_CONNECTED]: S.STREAMING,
    [E.START_FAILED]: S.IDLE,
    [E.RECONNECT_FAILED]: S.DISCONNECTED,
    [E.PLATFORM_DISCONNECTED]: S.DISCONNECTED,
    [E.STOP_CLICKED]: S.STOPPING,
    [E.GATEWAY_STOPPED]: S.IDLE,
  }),
  // GATEWAY_UP: gateway running, platform not connected, nothing in flight.
  // Start stays enabled here: clicking it is what launches the platform.
  [S.GATEWAY_UP]: Object.freeze({
    [E.START_CLICKED]: S.STARTING,
    [E.PLATFORM_CONNECTED]: S.STREAMING,
    [E.PLATFORM_DISCONNECTED]: S.DISCONNECTED,
    [E.STOP_CLICKED]: S.STOPPING,
    [E.GATEWAY_STOPPED]: S.IDLE,
  }),
  [S.STREAMING]: Object.freeze({
    [E.PLATFORM_DISCONNECTED]: S.DISCONNECTED,
    [E.STOP_CLICKED]: S.STOPPING,
    [E.GATEWAY_STOPPED]: S.IDLE,
  }),
  [S.DISCONNECTED]: Object.freeze({
    [E.RECONNECT_CLICKED]: S.STARTING,
    [E.PLATFORM_CONNECTED]: S.STREAMING,
    [E.STOP_CLICKED]: S.STOPPING,
    [E.GATEWAY_STOPPED]: S.IDLE,
  }),
  [S.STOPPING]: Object.freeze({
    [E.GATEWAY_STOPPED]: S.IDLE,
    [E.STOP_FAILED]: S.STREAMING,
    [E.PLATFORM_DISCONNECTED]: S.DISCONNECTED,
  }),
  [S.INACTIVE]: Object.freeze({
    // Live-mode events can still arrive (e.g. mode flipped at runtime).
    [E.GATEWAY_STARTED]: S.GATEWAY_UP,
    [E.PLATFORM_CONNECTED]: S.STREAMING,
  }),
});

/**
 * Map a backend stream-status payload to the matching lifecycle state.
 * Payload: { live_mode, gateway_running, platform_connected }.
 */
export function stateFromStatus(payload = {}) {
  if (payload.live_mode === false) return S.INACTIVE;
  if (payload.platform_connected) return S.STREAMING;
  if (payload.gateway_running) return S.GATEWAY_UP;
  return S.IDLE;
}

/**
 * Reduce a (state, event) pair to the next state.
 * Unknown events are a no-op and return the current state.
 *
 * @param {string} state one of StreamingState
 * @param {{type: string}} event event with a StreamingEventType `type`
 * @returns {string} the next StreamingState
 */
export function reduceStreamingState(state, event) {
  if (!event || !event.type) return state;
  if (event.type === E.STATUS_SYNC) {
    // Ignore partial stream_status payloads that only carry `playing`.
    // A full status sync must include an explicit `live_mode` flag.
    if (event.live_mode === undefined) return state;
    return stateFromStatus(event);
  }
  const transitions = TRANSITIONS[state];
  if (!transitions) return state;
  return transitions[event.type] || state;
}

const BUTTON = Object.freeze({
  VISIBLE_ENABLED: Object.freeze({ visible: true, enabled: true, busy: false }),
  VISIBLE_BUSY: Object.freeze({ visible: true, enabled: false, busy: true }),
  // Hidden buttons are kept enabled so they never resurface in a stale
  // disabled state; hidden already means unclickable.
  HIDDEN: Object.freeze({ visible: false, enabled: true, busy: false }),
});

const VIEW_MODELS = Object.freeze({
  [S.IDLE]: Object.freeze({
    overlayVisible: true,
    startBtn: BUTTON.VISIBLE_ENABLED,
    reconnectVisible: false,
    stopBtn: BUTTON.HIDDEN,
  }),
  [S.STARTING]: Object.freeze({
    overlayVisible: true,
    startBtn: BUTTON.VISIBLE_BUSY,
    reconnectVisible: false,
    // Stop stays available so a hung platform launch can be aborted.
    stopBtn: BUTTON.VISIBLE_ENABLED,
  }),
  [S.GATEWAY_UP]: Object.freeze({
    overlayVisible: true,
    // Start is enabled: clicking it is how the platform launch is triggered.
    startBtn: BUTTON.VISIBLE_ENABLED,
    reconnectVisible: false,
    stopBtn: BUTTON.VISIBLE_ENABLED,
  }),
  [S.STREAMING]: Object.freeze({
    overlayVisible: false,
    startBtn: BUTTON.VISIBLE_BUSY,
    reconnectVisible: false,
    stopBtn: BUTTON.VISIBLE_ENABLED,
  }),
  [S.DISCONNECTED]: Object.freeze({
    overlayVisible: true,
    startBtn: BUTTON.HIDDEN,
    reconnectVisible: true,
    stopBtn: BUTTON.VISIBLE_ENABLED,
  }),
  [S.STOPPING]: Object.freeze({
    overlayVisible: false,
    startBtn: BUTTON.VISIBLE_BUSY,
    reconnectVisible: false,
    stopBtn: Object.freeze({ visible: true, enabled: false, busy: true }),
  }),
  [S.INACTIVE]: Object.freeze({
    overlayVisible: false,
    startBtn: BUTTON.VISIBLE_ENABLED,
    reconnectVisible: false,
    stopBtn: BUTTON.HIDDEN,
  }),
});

/**
 * View model describing the streaming-control UI for a given state:
 * { overlayVisible, startBtn:{visible,enabled,busy}, reconnectVisible,
 *   stopBtn:{visible,enabled,busy} }.
 */
export function streamingViewModel(state) {
  return VIEW_MODELS[state] || VIEW_MODELS[S.IDLE];
}
