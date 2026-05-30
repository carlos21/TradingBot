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
                socketio.emit('health_update', data_source.get_health())

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
        if live_mode and isinstance(data_source, ZMQDataSource):
            # Request a refresh on browser connect. The state machine inside
            # ZMQDataSource guards against duplicates and disconnected state.
            data_source.request_refresh()

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
