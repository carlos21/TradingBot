"""Socket.IO event handlers."""

import threading

from flask_socketio import SocketIO, emit

from src.bars_loader import BarsLoader
from src.infrastructure.data_sources.combined_datasource import CombinedDataSource
from src.infrastructure.gateway.datasource import ZMQDataSource
from src.utils.app_logger import ILogger
import contextlib


def register_socketio_handlers(
    socketio: SocketIO,
    loader: BarsLoader,
    data_source: CombinedDataSource,
    live_mode: bool,
    _logger: ILogger,
    parity_service=None,
    readiness_monitor=None,
):
    """Register Socket.IO event handlers.

    Args:
        socketio: SocketIO instance
        loader: Bars loader for stream control
        data_source: Data source for bar history
        live_mode: Whether running in live trading mode
    """


    def _emit_health():
        """Emit current health snapshot if ZMQDataSource is available."""
        if isinstance(data_source, ZMQDataSource):
            with contextlib.suppress(Exception):
                health = data_source.get_health()
                if readiness_monitor is not None:
                    health.update(readiness_monitor.get_health())
                socketio.emit('health_update', health)

    @socketio.on('connect')
    def on_connect(_auth):
        gateway_running = False
        platform_connected = False
        if isinstance(data_source, ZMQDataSource) and data_source.gateway:
            gateway_running = data_source.gateway.is_running
            platform_connected = data_source.gateway.is_connected

        emit('stream_status', {
            'playing': loader.streaming,
            'live_mode': live_mode,
            'gateway_running': gateway_running,
            'platform_connected': platform_connected,
        })
        _emit_health()

        # If historical bars are already cached (e.g. server has been running),
        # tell the frontend to draw them immediately. This avoids an empty
        # chart while waiting for a fresh history load cycle.
        if live_mode and isinstance(data_source, ZMQDataSource):
            try:
                cached = data_source.load_historical_bars("1m")
                if cached:
                    readiness_state = 'UNKNOWN'
                    readiness_reason = 'Cached bars available on connect'
                    if readiness_monitor is not None:
                        health = readiness_monitor.get_health()
                        readiness_state = health.get('readiness_state', readiness_state)
                        readiness_reason = health.get('readiness_reason', readiness_reason)
                    emit('history_loaded', {
                        'readiness_state': readiness_state,
                        'readiness_reason': readiness_reason,
                        'bar_count': len(cached),
                    })
            except Exception:
                pass  # Don't break connect if cached-bar lookup fails

        # Note: we do NOT auto-request a refresh here. Refreshing on every
        # browser connect duplicates the refresh already scheduled when the
        # platform connected and causes unnecessary load on NinjaTrader.
        # Use the explicit 'request_refresh' event to force a refresh.

    @socketio.on('start_stream')
    def on_start_stream(payload):
        tf = payload.get('timeframe', '1m')
        from_time = payload.get('fromTime', 0)
        stop_at   = payload.get('stopAt')

        # Set base time first so set_timeframe uses the right window
        loader.seek(from_time)
        loader.set_timeframe(tf)
        loader.start(from_time, stop_at)
        emit('stream_status', {'playing': True})

    @socketio.on('pause_stream')
    def on_pause_stream():
        if live_mode:
            return
        loader.pause()
        emit('stream_status', {'playing': False})

    @socketio.on('step_stream')
    def on_step_stream(payload):
        if live_mode:
            return
        tf = payload.get('timeframe', '1m')
        from_time = payload.get('fromTime', 0)
        # Advance from_time by one full timeframe window so the step lands on
        # the *next* bar, not the current one (which is already displayed).
        unit = tf[-1]
        num = int(tf[:-1])
        group_size = num if unit == 'm' else num * 60
        from_time = from_time + group_size * 60
        loader.seek(from_time)
        loader.set_timeframe(tf)
        loader.step()
        emit('stream_status', {'playing': True})

    @socketio.on('seek')
    def on_seek(payload):
        if live_mode:
            return
        loader.seek(payload.get('fromTime', 0))

    @socketio.on('set_timeframe')
    def on_set_timeframe(payload):
        tf = payload.get('timeframe', '1m')
        from_time = payload.get('fromTime', 0)
        loader.seek(from_time)
        loader.set_timeframe(tf)

    @socketio.on('jump_day')
    def on_jump_day(payload):
        if live_mode:
            return
        # payload: {direction: 1|-1, fast: true|false}
        direction = int(payload.get('direction', 1))
        fast      = bool(payload.get('fast', True))
        ts = loader.jump_day(direction=direction, fast=fast)
        emit('jump_result', {'to': ts})

    @socketio.on('request_health')
    def on_request_health():
        _emit_health()

    @socketio.on('request_refresh')
    def on_request_refresh(payload=None):
        if not isinstance(data_source, ZMQDataSource):
            emit('refresh_result', {'ok': False, 'error': 'Not a ZMQ data source'})
            return
        days = (payload or {}).get('days')
        try:
            data_source.request_refresh(days=days)
            emit('refresh_result', {'ok': True, 'days': days})
        except Exception as e:
            _logger.error(f"request_refresh failed: {e}")
            emit('refresh_result', {'ok': False, 'error': str(e)})

    @socketio.on('check_parity')
    def on_check_parity():
        if parity_service is None:
            emit('parity_result', {
                'ok': False,
                'error': 'Parity service not available',
            })
            return
        try:
            result = parity_service.check_parity(hours_back=5)
            emit('parity_result', result.to_dict())
        except Exception as e:
            _logger.error(f"check_parity failed: {e}")
            emit('parity_result', {
                'ok': False,
                'error': str(e),
            })

    # Wire up platform connection state changes
    if isinstance(data_source, ZMQDataSource) and data_source.gateway:
        def _on_conn_change(connected: bool):
            event_name = 'platform_connected' if connected else 'platform_disconnected'
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

    def _forward_log(level: str, message: str):
        """Forward log entries to browsers via Socket.IO."""
        try:
            socketio.emit('system_log', {
                'time': __import__('time').time(),
                'level': level,
                'source': 'server',
                'message': message,
            })
        except Exception:
            pass  # Don't let log forwarding break anything

    def _wrap_logger():
        """Wrap the logger's methods to also emit via Socket.IO."""
        for level in ('debug', 'info', 'warning', 'error'):
            orig = getattr(_logger, level, None)
            if orig is None:
                continue
            _original_logger_methods[level] = orig

            def make_wrapper(lvl, original):
                def wrapper(msg: str):
                    original(msg)
                    if lvl in ('info', 'warning', 'error'):
                        _forward_log(lvl.upper(), msg)
                return wrapper

            setattr(_logger, level, make_wrapper(level, orig))

    _wrap_logger()
