"""Socket.IO event handlers."""

import contextlib
import re
import threading
import time

from flask_socketio import SocketIO, emit

from src.bars_loader import BarsLoader
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.utils.app_logger import ILogger
from src.utils.bar_aggregator import BarAggregator


class _TokenBucket:
    """Simple thread-safe token bucket for rate limiting."""

    def __init__(self, rate: float, capacity: float):
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_update = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            # A non-positive rate means "do not rate-limit".
            if self._rate <= 0:
                return True
            now = time.monotonic()
            elapsed = now - self._last_update
            self._last_update = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


# Sentinel attribute used to avoid double-wrapping the same logger instance.
_LOGGER_WRAPPED_ATTR = "_socketio_log_wrapped"

# Heuristic source tag at the start of a log message, e.g. "[LiveMode] ...".
_LOG_SOURCE_RE = re.compile(r"^\[([^\]]+)\]\s*")


def _extract_log_source(message: str) -> str:
    """Infer a source tag from a log message prefix."""
    match = _LOG_SOURCE_RE.match(message)
    if match:
        return match.group(1)
    return "server"


def register_socketio_handlers(
    socketio: SocketIO,
    loader: BarsLoader,
    data_source: CombinedDataSource,
    live_mode: bool,
    _logger: ILogger,
    parity_service=None,
    readiness_monitor=None,
    history_loaded_deduper=None,
):
    """Register Socket.IO event handlers.

    Args:
        socketio: SocketIO instance
        loader: Bars loader for stream control
        data_source: Data source for bar history
        live_mode: Whether running in live trading mode

    Returns:
        A cleanup function that stops the background health thread and
        unwraps the logger. Callers should invoke it on shutdown/reload.
    """

    def _emit_error(message: str) -> None:
        """Emit an error event to the client and log it locally."""
        _logger.error(f"[SocketIO] {message}")
        with contextlib.suppress(Exception):
            emit("error", {"message": message})

    def _safe_handler(fn):
        """Wrap a handler so unexpected exceptions emit an error event."""
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                _logger.error(f"[SocketIO] handler {fn.__name__} failed: {exc}")
                _emit_error(f"Handler {fn.__name__} failed: {exc}")
        wrapper.__name__ = fn.__name__
        return wrapper

    def _validate_timeframe(tf) -> str:
        """Return a normalized timeframe string or raise ValueError."""
        if not isinstance(tf, str) or not tf.strip():
            raise ValueError("timeframe must be a non-empty string")
        tf = tf.strip()
        # Let BarAggregator validate the numeric/unit format.
        BarAggregator.parse_timeframe(tf)
        unit = tf[-1].lower()
        if unit not in ("m", "h"):
            raise ValueError(f"Unsupported timeframe '{tf}' for streaming")
        return tf

    def _as_int(value, name: str) -> int:
        """Coerce a payload value to int or raise ValueError."""
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer") from exc

    # Track the last history-loaded signature emitted on connect so reconnects
    # do not spam the frontend with duplicate events.
    _last_history_loaded_signature: dict | None = None

    def _history_loaded_signature(cached: list[dict]) -> dict:
        readiness_state = "UNKNOWN"
        readiness_reason = "Cached bars available on connect"
        if readiness_monitor is not None:
            health = readiness_monitor.get_health()
            readiness_state = health.get("readiness_state", readiness_state)
            readiness_reason = health.get("readiness_reason", readiness_reason)
        return {
            "readiness_state": readiness_state,
            "readiness_reason": readiness_reason,
            "bar_count": len(cached),
            "last_bar_time": cached[-1]["time"] if cached else None,
        }

    def _emit_health():
        """Emit current health snapshot if ZMQDataSource is available."""
        if isinstance(data_source, ZMQDataSource):
            with contextlib.suppress(Exception):
                health = data_source.get_health()
                if readiness_monitor is not None:
                    health.update(readiness_monitor.get_health())
                socketio.emit("health_update", health)

    @socketio.on("connect")
    @_safe_handler
    def on_connect(_auth):
        gateway_running = False
        platform_connected = False
        if isinstance(data_source, ZMQDataSource) and data_source.gateway:
            gateway_running = data_source.gateway.is_running
            platform_connected = data_source.gateway.is_connected

        emit("stream_status", {
            "playing": loader.streaming,
            "live_mode": live_mode,
            "gateway_running": gateway_running,
            "platform_connected": platform_connected,
        })
        _emit_health()

        # If the platform is already connected, immediately clear the reconnect
        # overlay for a reconnecting browser.
        if live_mode and platform_connected:
            emit("platform_connected")

        # If historical bars are already cached (e.g. server has been running),
        # tell the frontend to draw them immediately. This avoids an empty
        # chart while waiting for a fresh history load cycle.
        # Reconnects are deduplicated by signature so the same cached batch is
        # not emitted repeatedly.
        if live_mode and isinstance(data_source, ZMQDataSource):
            try:
                cached = data_source.load_historical_bars("1m")
                if cached:
                    payload = _history_loaded_signature(cached)
                    if history_loaded_deduper is not None:
                        history_loaded_deduper.emit(socketio, payload)
                    else:
                        signature = _history_loaded_signature(cached)
                        nonlocal _last_history_loaded_signature
                        if signature != _last_history_loaded_signature:
                            _last_history_loaded_signature = signature
                            emit("history_loaded", {
                                "readiness_state": signature["readiness_state"],
                                "readiness_reason": signature["readiness_reason"],
                                "bar_count": signature["bar_count"],
                            })
            except Exception as exc:
                # Don't break the connect handshake, but let the client know.
                _logger.error(f"[SocketIO] failed to load cached bars on connect: {exc}")
                with contextlib.suppress(Exception):
                    emit("history_load_failed", {"error": str(exc)})

        # Note: we do NOT auto-request a refresh here. Refreshing on every
        # browser connect duplicates the refresh already scheduled when the
        # platform connected and causes unnecessary load on NinjaTrader.
        # Use the explicit 'request_refresh' event to force a refresh.

    @socketio.on("start_stream")
    @_safe_handler
    def on_start_stream(payload):
        tf = _validate_timeframe(payload.get("timeframe", "1m"))
        from_time = _as_int(payload.get("fromTime", 0), "fromTime")
        stop_at = payload.get("stopAt")
        if stop_at is not None:
            stop_at = _as_int(stop_at, "stopAt")

        # Set base time first so set_timeframe uses the right window
        loader.seek(from_time)
        loader.set_timeframe(tf)
        loader.start(from_time, stop_at)
        emit("stream_status", {"playing": True})

    @socketio.on("pause_stream")
    @_safe_handler
    def on_pause_stream():
        if live_mode:
            return
        loader.pause()
        emit("stream_status", {"playing": False})

    @socketio.on("step_stream")
    @_safe_handler
    def on_step_stream(payload):
        if live_mode:
            return
        tf = _validate_timeframe(payload.get("timeframe", "1m"))
        from_time = _as_int(payload.get("fromTime", 0), "fromTime")
        # Advance from_time by one full timeframe window so the step lands on
        # the *next* bar, not the current one (which is already displayed).
        window_secs = BarAggregator.parse_timeframe(tf)
        loader.seek(from_time + window_secs)
        loader.set_timeframe(tf)
        loader.step()
        emit("stream_status", {"playing": True})

    @socketio.on("seek")
    @_safe_handler
    def on_seek(payload):
        if live_mode:
            return
        loader.seek(_as_int(payload.get("fromTime", 0), "fromTime"))

    @socketio.on("set_timeframe")
    @_safe_handler
    def on_set_timeframe(payload):
        tf = _validate_timeframe(payload.get("timeframe", "1m"))
        from_time = _as_int(payload.get("fromTime", 0), "fromTime")
        loader.seek(from_time)
        loader.set_timeframe(tf)

    @socketio.on("jump_day")
    @_safe_handler
    def on_jump_day(payload):
        if live_mode:
            return
        # payload: {direction: 1|-1, fast: true|false}
        direction_raw = payload.get("direction", 1)
        direction = _as_int(direction_raw, "direction")
        if direction not in (-1, 1):
            raise ValueError("direction must be 1 or -1")
        fast = bool(payload.get("fast", True))
        ts = loader.jump_day(direction=direction, fast=fast)
        emit("jump_result", {"to": ts})

    @socketio.on("request_health")
    @_safe_handler
    def on_request_health():
        _emit_health()

    @socketio.on("request_refresh")
    @_safe_handler
    def on_request_refresh(payload=None):
        if not isinstance(data_source, ZMQDataSource):
            emit("refresh_result", {"ok": False, "error": "Not a ZMQ data source"})
            return
        days = (payload or {}).get("days")
        if days is not None:
            days = _as_int(days, "days")
        try:
            data_source.request_refresh(days=days)
            emit("refresh_result", {"ok": True, "days": days})
        except Exception as e:
            _logger.error(f"request_refresh failed: {e}")
            emit("refresh_result", {"ok": False, "error": str(e)})

    @socketio.on("check_parity")
    @_safe_handler
    def on_check_parity():
        if parity_service is None:
            emit("parity_result", {
                "ok": False,
                "error": "Parity service not available",
            })
            return
        try:
            result = parity_service.check_parity(hours_back=5)
            emit("parity_result", result.to_dict())
        except Exception as e:
            _logger.error(f"check_parity failed: {e}")
            emit("parity_result", {
                "ok": False,
                "error": str(e),
            })

    # Wire up platform connection state changes
    if isinstance(data_source, ZMQDataSource) and data_source.gateway:
        def _on_conn_change(connected: bool):
            event_name = "platform_connected" if connected else "platform_disconnected"
            socketio.emit(event_name)
            _emit_health()
        data_source.gateway.on_connection_change(_on_conn_change)

    # Start periodic health emission (every 2 seconds)
    _health_stop_event = threading.Event()

    def _health_loop():
        while not _health_stop_event.is_set():
            _health_stop_event.wait(2.0)
            if not _health_stop_event.is_set():
                _emit_health()

    _health_thread = threading.Thread(target=_health_loop, name="SocketIO-Health", daemon=True)
    _health_thread.start()

    # Wire up log forwarding to connected browsers
    _original_logger_methods = {}
    _system_log_bucket = _TokenBucket(rate=20.0, capacity=40.0)

    def _forward_log(level: str, message: str):
        """Forward log entries to browsers via Socket.IO.

        Rate-limited so a log storm (e.g. TSI during warm-up) cannot saturate
        the Socket.IO connection and disconnect the browser.
        Error-level logs always pass through to avoid dropping critical alerts.
        """
        if level != "ERROR" and not _system_log_bucket.allow():
            return
        try:
            socketio.emit("system_log", {
                "time": time.time(),
                "level": level,
                "source": _extract_log_source(message),
                "message": message,
            })
        except Exception as exc:
            _logger.error(f"[SocketIO] failed to forward log: {exc}")

    def _wrap_logger():
        """Wrap the logger's methods to also emit via Socket.IO.

        Idempotent: if the logger is already wrapped by a previous
        registration call, do nothing.
        """
        if getattr(_logger, _LOGGER_WRAPPED_ATTR, False):
            return
        for level in ("debug", "info", "warning", "error"):
            orig = getattr(_logger, level, None)
            if orig is None:
                continue
            _original_logger_methods[level] = orig

            def make_wrapper(lvl, original):
                def wrapper(msg: str):
                    original(msg)
                    if lvl in ("info", "warning", "error"):
                        _forward_log(lvl.upper(), msg)
                return wrapper

            setattr(_logger, level, make_wrapper(level, orig))
        setattr(_logger, _LOGGER_WRAPPED_ATTR, True)

    def _unwrap_logger():
        """Restore the original logger methods."""
        if not getattr(_logger, _LOGGER_WRAPPED_ATTR, False):
            return
        for level, orig in _original_logger_methods.items():
            setattr(_logger, level, orig)
        _original_logger_methods.clear()
        delattr(_logger, _LOGGER_WRAPPED_ATTR)

    _wrap_logger()

    def cleanup() -> None:
        """Stop background threads and unwrap the logger."""
        _health_stop_event.set()
        _health_thread.join(timeout=2.0)
        _unwrap_logger()

    return cleanup
