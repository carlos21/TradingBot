"""Socket.IO event handlers."""

import contextlib
import re
import threading
import time
from collections.abc import Callable

from flask import request
from flask_socketio import SocketIO, emit, join_room, leave_room

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


# Heuristic source tag at the start of a log message, e.g. "[LiveMode] ...".
_LOG_SOURCE_RE = re.compile(r"^\[([^\]]+)\]\s*")


def _extract_log_source(message: str) -> str:
    """Infer a source tag from a log message prefix."""
    match = _LOG_SOURCE_RE.match(message)
    if match:
        return match.group(1)
    return "server"


def make_system_log_forwarder(socketio: SocketIO) -> Callable[[str, str], None]:
    """Build the callback that forwards log entries to browsers via Socket.IO.

    Rate-limited so a log storm (e.g. TSI during warm-up) cannot saturate
    the Socket.IO connection and disconnect the browser.
    Error-level logs always pass through to avoid dropping critical alerts.
    """
    bucket = _TokenBucket(rate=20.0, capacity=40.0)

    def _forward_log(level: str, message: str) -> None:
        if level != "ERROR" and not bucket.allow():
            return
        with contextlib.suppress(Exception):
            socketio.emit("system_log", {
                "time": time.time(),
                "level": level,
                "source": _extract_log_source(message),
                "message": message,
            })

    return _forward_log


class SocketIOLogForwarder(ILogger):
    """ILogger decorator that also forwards info/warning/error logs to the browser via a callback."""

    def __init__(self, delegate: ILogger, forward: Callable[[str, str], None]):
        self.delegate = delegate
        self._forward = forward

    @staticmethod
    def _format(message: str, args: tuple) -> str:
        # Best-effort formatting so browser logs stay readable when callers
        # pass printf-style arguments.
        try:
            return message % args if args else message
        except Exception:
            return message

    def debug(self, message: str, *args, **kwargs) -> None:
        self.delegate.debug(message, *args, **kwargs)

    def info(self, message: str, *args, **kwargs) -> None:
        self.delegate.info(message, *args, **kwargs)
        self._forward("INFO", self._format(message, args))

    def warning(self, message: str, *args, **kwargs) -> None:
        self.delegate.warning(message, *args, **kwargs)
        self._forward("WARNING", self._format(message, args))

    def error(self, message: str, *args, **kwargs) -> None:
        self.delegate.error(message, *args, **kwargs)
        self._forward("ERROR", self._format(message, args))

    def close(self) -> None:
        self.delegate.close()


def _current_sid() -> str | None:
    """Return the Socket.IO session id for the current request.

    flask-socketio injects ``request.sid`` at runtime; it is not declared on
    Flask's Request type, so this helper is the single dynamic-access point.
    """
    return getattr(request, "sid", None)


def register_socketio_handlers(
    socketio: SocketIO,
    loader: BarsLoader,
    data_source: CombinedDataSource,
    live_mode: bool,
    _logger: ILogger,
    parity_service=None,
    readiness_monitor=None,
    history_loaded_deduper=None,
    coordinator=None,
):
    """Register Socket.IO event handlers.

    Args:
        socketio: SocketIO instance
        loader: Bars loader for stream control
        data_source: Data source for bar history
        live_mode: Whether running in live trading mode
        coordinator: Optional StreamCoordinator for per-instrument streaming

    Returns:
        A ``(cleanup, logger)`` tuple. ``cleanup`` stops the background health
        thread and unwraps the logger; callers should invoke it on
        shutdown/reload. ``logger`` is the effective logger the handlers use
        (a ``SocketIOLogForwarder`` around the given one).
    """

    def _resolve_session(symbol: str | None = None):
        """Return the bars loader for the requested symbol."""
        if coordinator is not None:
            return coordinator.require_session(symbol).bars_loader
        if loader is None:
            raise ValueError("No streaming session available")
        return loader

    def _resolve_pair(payload: dict | None) -> str | None:
        """Extract the pair from the payload.

        There is no default instrument: with a coordinator a missing pair is
        an error surfaced to the client; without one (legacy single-session
        wiring) ``None`` simply means "the only session".
        """
        if payload and isinstance(payload, dict) and payload.get("pair"):
            return payload["pair"]
        return None

    def _emit_to_pair(event: str, payload: dict, pair: str | None = None) -> None:
        """Emit to the room for the requested pair, or broadcast when no coordinator."""
        if coordinator is not None and pair:
            try:
                emit(event, payload, room=pair)
                return
            except Exception as exc:
                _logger.error(f"[SocketIO] room emit failed: {exc}")
        emit(event, payload)

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

    def _history_loaded_signature(cached: list[dict], symbol: str | None = None) -> dict:
        readiness_state = "UNKNOWN"
        readiness_reason = "Cached bars available on connect"
        monitor = readiness_monitor
        if coordinator is not None and symbol:
            session = coordinator.get_session(symbol)
            if session is not None:
                monitor = session.readiness_monitor
        if monitor is not None:
            health = monitor.get_health()
            readiness_state = health.get("readiness_state", readiness_state)
            readiness_reason = health.get("readiness_reason", readiness_reason)
        return {
            "readiness_state": readiness_state,
            "readiness_reason": readiness_reason,
            "bar_count": len(cached),
            "last_bar_time": cached[-1]["time"] if cached else None,
            "pair": symbol,
        }

    def _emit_health():
        """Emit current health snapshot if ZMQDataSource is available.

        Emits per-room snapshots for each active streaming session so the
        stream health panel reflects the readiness state of the instrument
        the user is actually viewing. The legacy global snapshot is only
        sent when no room snapshot was emitted: it carries the datasource
        state ('STREAMING') but no readiness_state, and a panel pinned to an
        instrument would briefly render it as NOT READY before the room
        snapshot arrived.
        """
        if not isinstance(data_source, ZMQDataSource):
            return
        with contextlib.suppress(Exception):
            base_health = data_source.get_health()
            if readiness_monitor is not None:
                base_health.update(readiness_monitor.get_health())

            # Per-instrument health: clients in a room see their session's
            # readiness state instead of the legacy default session state.
            room_emitted = False
            if coordinator is not None:
                for session in coordinator.get_active_sessions():
                    symbol = session.instrument.symbol
                    monitor = getattr(session, "readiness_monitor", None)
                    if monitor is None:
                        continue
                    session_health = dict(base_health)
                    session_health.update(monitor.get_health())
                    session_health["pair"] = symbol
                    socketio.emit("health_update", session_health, room=symbol)
                    room_emitted = True

            if not room_emitted:
                socketio.emit("health_update", base_health)

    @socketio.on("connect")
    @_safe_handler
    def on_connect(_auth):
        gateway_running = False
        platform_connected = False
        if isinstance(data_source, ZMQDataSource) and data_source.gateway:
            gateway_running = data_source.gateway.is_running
            platform_connected = data_source.gateway.is_connected

        emit("stream_status", {
            "playing": loader.streaming if loader is not None else False,
            "live_mode": live_mode,
            "gateway_running": gateway_running,
            "platform_connected": platform_connected,
        })
        _emit_health()

        # If the platform is already connected, immediately clear the reconnect
        # overlay for a reconnecting browser.
        if live_mode and platform_connected:
            emit("platform_connected")

        # Note: we do NOT auto-request a refresh here. Refreshing on every
        # browser connect duplicates the refresh already scheduled when the
        # platform connected and causes unnecessary load on NinjaTrader.
        # Use the explicit 'request_refresh' event to force a refresh.
        # Cached bars are emitted per instrument on join_instrument — there is
        # no default instrument to load on bare connect.

    @socketio.on("join_instrument")
    @_safe_handler
    def on_join_instrument(payload):
        if not isinstance(payload, dict) or not payload.get("pair"):
            _emit_error("join_instrument requires {'pair': ...}")
            return
        symbol = payload["pair"]
        sid = _current_sid()
        if sid is None:
            _emit_error("join_instrument: no socket sid available")
            return
        join_room(symbol)
        if coordinator is not None:
            session = coordinator.join_instrument(symbol, sid)
            # Emit cached history to the joining room if available.
            try:
                cached = data_source.load_historical_bars("1m", pair=symbol)
                if cached:
                    sig = _history_loaded_signature(cached, symbol)
                    if history_loaded_deduper is not None:
                        history_loaded_deduper.emit(socketio, sig)
                    else:
                        socketio.emit("history_loaded", sig, room=symbol)
                    # In live mode, seed the session's readiness monitor with
                    # the cached bars so the Stream Health panel does not stay
                    # stuck on CONNECTED when history is already available.
                    # Skip the seed once the monitor is warming or warm —
                    # otherwise every browser join (page refresh, reconnect)
                    # would cancel and restart the full warmup replay.
                    if (
                        live_mode
                        and session is not None
                        and getattr(session, "readiness_monitor", None) is not None
                    ):
                        with contextlib.suppress(Exception):
                            monitor = session.readiness_monitor
                            needs_seed = getattr(monitor, "needs_history_seed", None)
                            if needs_seed is None or needs_seed():
                                session.on_history_loaded(list(cached))
            except Exception as exc:
                _logger.error(f"[SocketIO] failed to load cached bars on join: {exc}")
                with contextlib.suppress(Exception):
                    emit("history_load_failed", {"error": str(exc)}, room=symbol)
        emit("joined_instrument", {"pair": symbol})

    @socketio.on("leave_instrument")
    @_safe_handler
    def on_leave_instrument(payload):
        if not isinstance(payload, dict) or not payload.get("pair"):
            _emit_error("leave_instrument requires {'pair': ...}")
            return
        symbol = payload["pair"]
        sid = _current_sid()
        if sid is not None:
            leave_room(symbol)
            if coordinator is not None:
                coordinator.leave_instrument(symbol, sid)
        emit("left_instrument", {"pair": symbol})

    @socketio.on("start_stream")
    @_safe_handler
    def on_start_stream(payload):
        tf = _validate_timeframe(payload.get("timeframe", "1m"))
        from_time = _as_int(payload.get("fromTime", 0), "fromTime")
        stop_at = payload.get("stopAt")
        if stop_at is not None:
            stop_at = _as_int(stop_at, "stopAt")
        pace_bps = payload.get("paceBps")
        if pace_bps is not None:
            pace_bps = float(pace_bps)

        symbol = _resolve_pair(payload)
        if coordinator is not None and symbol is None:
            _emit_error("start_stream requires {'pair': ...}")
            return
        session_loader = _resolve_session(symbol)

        # Set base time first so set_timeframe uses the right window
        session_loader.seek(from_time)
        session_loader.set_timeframe(tf)
        session_loader.start(from_time, stop_at, pace_bps=pace_bps)
        _emit_to_pair("stream_status", {"playing": True}, symbol)

    @socketio.on("pause_stream")
    @_safe_handler
    def on_pause_stream(payload=None):
        if live_mode:
            return
        symbol = _resolve_pair(payload)
        if coordinator is not None and symbol is None:
            _emit_error("pause_stream requires {'pair': ...}")
            return
        session_loader = _resolve_session(symbol)
        session_loader.pause()
        _emit_to_pair("stream_status", {"playing": False}, symbol)

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

        symbol = _resolve_pair(payload)
        if coordinator is not None and symbol is None:
            _emit_error("step_stream requires {'pair': ...}")
            return
        session_loader = _resolve_session(symbol)

        session_loader.seek(from_time + window_secs)
        session_loader.set_timeframe(tf)
        session_loader.step()
        _emit_to_pair("stream_status", {"playing": True}, symbol)

    @socketio.on("seek")
    @_safe_handler
    def on_seek(payload):
        if live_mode:
            return
        symbol = _resolve_pair(payload)
        if coordinator is not None and symbol is None:
            _emit_error("seek requires {'pair': ...}")
            return
        session_loader = _resolve_session(symbol)
        session_loader.seek(_as_int(payload.get("fromTime", 0), "fromTime"))

    @socketio.on("set_timeframe")
    @_safe_handler
    def on_set_timeframe(payload):
        tf = _validate_timeframe(payload.get("timeframe", "1m"))
        from_time = _as_int(payload.get("fromTime", 0), "fromTime")

        symbol = _resolve_pair(payload)
        if coordinator is not None and symbol is None:
            _emit_error("set_timeframe requires {'pair': ...}")
            return
        session_loader = _resolve_session(symbol)

        session_loader.seek(from_time)
        session_loader.set_timeframe(tf)

    @socketio.on("jump_day")
    @_safe_handler
    def on_jump_day(payload):
        if live_mode:
            return
        # payload: {pair, direction: 1|-1, fast: true|false}
        symbol = _resolve_pair(payload)
        if coordinator is not None and symbol is None:
            _emit_error("jump_day requires {'pair': ...}")
            return
        session_loader = _resolve_session(symbol)

        direction_raw = payload.get("direction", 1)
        direction = _as_int(direction_raw, "direction")
        if direction not in (-1, 1):
            raise ValueError("direction must be 1 or -1")
        fast = bool(payload.get("fast", True))
        ts = session_loader.jump_day(direction=direction, fast=fast)
        _emit_to_pair("jump_result", {"to": ts}, symbol)

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
    def on_check_parity(payload=None):
        service = parity_service
        if coordinator is not None:
            symbol = _resolve_pair(payload)
            if symbol is None:
                _emit_error("check_parity requires {'pair': ...}")
                return
            session = coordinator.get_session(symbol)
            service = session.parity_service if session is not None else None
        if service is None:
            emit("parity_result", {
                "ok": False,
                "error": "Parity service not available",
            })
            return
        try:
            result = service.check_parity(hours_back=5)
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

    # Wire up log forwarding to connected browsers. In app wiring the logger
    # already arrives wrapped (app_factory decorates it eagerly so every
    # component shares the forwarder); _wrap_logger keeps direct registration
    # working and is idempotent either way.
    _forward_log = make_system_log_forwarder(socketio)

    def _wrap_logger() -> ILogger:
        """Decorate the logger so logs also reach browsers (idempotent)."""
        if isinstance(_logger, SocketIOLogForwarder):
            return _logger
        return SocketIOLogForwarder(_logger, _forward_log)

    def _unwrap_logger(logger: ILogger) -> ILogger:
        """Restore the delegate logger."""
        if isinstance(logger, SocketIOLogForwarder):
            return logger.delegate
        return logger

    _logger = _wrap_logger()

    def cleanup() -> ILogger:
        """Stop background threads and unwrap the logger.

        Returns the restored (unwrapped) logger.
        """
        nonlocal _logger
        _health_stop_event.set()
        _health_thread.join(timeout=2.0)
        _logger = _unwrap_logger(_logger)
        return _logger

    return cleanup, _logger
