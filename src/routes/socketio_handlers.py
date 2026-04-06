"""Socket.IO event handlers."""

from flask_socketio import SocketIO, emit
from src.bars_loader import BarsLoader
from src.data_sources.combined_datasource import CombinedDataSource


def register_socketio_handlers(
    socketio: SocketIO,
    loader: BarsLoader,
    data_source: CombinedDataSource,
    live_mode: bool,
):
    """Register Socket.IO event handlers.
    
    Args:
        socketio: SocketIO instance
        loader: Bars loader for stream control
        data_source: Data source for bar history
        live_mode: Whether running in live trading mode
    """
    
    @socketio.on('connect')
    def on_connect(auth):
        emit('stream_status', {'playing': loader.streaming, 'live_mode': live_mode})
        if live_mode and hasattr(data_source, '_historical_bars') and data_source._historical_bars:
            emit('history_ready', {'count': len(data_source._historical_bars)})
            # Request fresh bars from NinjaTrader (once per browser connect)
            if hasattr(data_source, 'request_history_refresh'):
                data_source.request_history_refresh(days=1)

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
