# src/app_factory.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List

from flask import Flask, jsonify, request, abort, render_template
from flask_cors import CORS
from flask_socketio import SocketIO, emit

from src.bars_loader import BarsLoader
from src.controllers.lines_controller import LinesController
from src.controllers.trades_controller import TradesController
from src.data_sources.combined_datasource import CombinedDataSource
from src.services.trade_manager import TradeManager
from src.services.trade_executor import TradeExecutor
from src.services.trade_logger import TradeLogger
from src.strategies.base_liquidity_strategy import StrategyOptions
from src.strategies.entry_context import (
    open_trades_limit_filter, max_bounce_filter
)
from src.repositories.lines_repository import LineRepository
from src.repositories.trades_repository import TradeRepository
from src.repositories.line_trigger_state_repository import LineTriggerStateRepository, InMemoryLineTriggerStateRepository
from src.strategies.liquidity_strategy_v2 import LiquidityStrategyV2, LiveLiquidityStrategyV2
from src.strategies.strategy_config import CandleConfig, StrategyNumbers
from src.strategies.triggers import three_candle_reversal_trigger, wick_near_line_trigger
from src.notifier import Notifier, NoOpNotifier
from src.analytics import AnalyticsReporter, NoOpReporter


@dataclass
class Repositories:
    lines: LineRepository
    trades: TradeRepository
    trigger_state: LineTriggerStateRepository = None

    def __post_init__(self):
        if self.trigger_state is None:
            self.trigger_state = InMemoryLineTriggerStateRepository()

@dataclass
class AppWiring:
    app: Flask
    socketio: SocketIO
    loader: BarsLoader
    strategy: LiquidityStrategyV2
    trade_manager: TradeManager
    lines_controller: LinesController
    trades_controller: TradesController
    data_source: CombinedDataSource
    pair: str
    live_mode: bool = False


def create_app(
    *,
    pair: str,
    data_source: CombinedDataSource,
    repos: Repositories,
    numbers: StrategyNumbers,
    options: Optional[StrategyOptions] = None,
    candle_config: Optional[CandleConfig] = None,
    timeframes: Optional[List[str]] = None,
    bootstrap_existing_lines: bool = True,
    broker_mode: str = 'futures',
    broker_spread: float = 0.0,
    live_mode: bool = False,
    trade_executor: TradeExecutor = None,
    notifier: Notifier = None,
    analytics: AnalyticsReporter = None,
) -> AppWiring:
    """
    Build the whole application with injected dependencies.
    No env vars; no global singletons.
    """
    app = Flask(__name__)
    CORS(app)
    socketio = SocketIO(app, cors_allowed_origins="*")

    # Suppress noisy Werkzeug access logs for high-frequency endpoints
    import logging
    class _QuietFilter(logging.Filter):
        _NOISY = ('/api/nt/tick', '/api/nt/partial')
        def filter(self, record):
            msg = record.getMessage()
            return not any(p in msg for p in self._NOISY)
    logging.getLogger('werkzeug').addFilter(_QuietFilter())
    
    if notifier is None:
        notifier = NoOpNotifier()
    if analytics is None:
        analytics = NoOpReporter()
    analytics.set_context("app", {"pair": pair, "live_mode": live_mode})

    trade_logger = TradeLogger(repos.trades)

    trade_manager = TradeManager(
        trade_repository=repos.trades,
        socketio=socketio,
        pair=pair,
        session_end_time="17:00",
        session_tz="America/New_York",
        broker_mode=broker_mode,
        broker_spread=broker_spread,
        trade_executor=trade_executor,
        trade_logger=trade_logger,
        notifier=notifier,
        analytics=analytics,
    )

    # Initialize strategy — LiveLiquidityStrategyV2 disables Python SL/TP/session-end
    # close checks (NinjaTrader is source of truth in live mode)
    StrategyClass = LiveLiquidityStrategyV2 if live_mode else LiquidityStrategyV2
    tstrategy = StrategyClass(
        min_stop_loss   = numbers.min_stop_loss,
        max_bounce      = numbers.max_bounce,
        extra_sl_space  = numbers.extra_sl_space,
        fixed_stop_loss = numbers.fixed_stop_loss,
        max_stop_loss   = numbers.max_stop_loss,
        sl_levels       = numbers.sl_levels,
        sl_level_tolerance = numbers.sl_level_tolerance,
        min_cross_depth = numbers.min_cross_depth,
        rr_ratio        = numbers.rr_ratio,
        socketio        = socketio,
        line_repository = repos.lines,
        trade_repository= repos.trades,
        trade_manager   = trade_manager,
        options         = options,
        timeframes      = timeframes,
        candle_config   = candle_config,
        trade_logger    = trade_logger,
        analytics       = analytics,
        trigger_state_repo = repos.trigger_state,
    )

    # Wire different bar processing paths based on mode
    if live_mode:
        _close_commands_sent: set = set()
        _test_sequences: dict = {}  # trade_id → {"stage", "scenario", "entry_price", ...}

        def _check_live_session_end(bar):
            """Send close commands to NinjaTrader when session ends."""
            if not trade_manager._session_end_time or not trade_manager._session_tz:
                return
            from zoneinfo import ZoneInfo
            bar_dt = datetime.fromtimestamp(bar['time'], tz=trade_manager._session_tz)
            if bar_dt.time() < trade_manager._session_end_time:
                return
            for t in list(trade_manager.open_trades):
                if t['pair'] != bar['pair'] or t['entry_time'] > bar['time']:
                    continue
                tid = t['trade_id']
                if tid not in _close_commands_sent:
                    print(f"[LiveMode] SESSION END — sending close_order to NT for {tid}")
                    trade_logger.log(tid, "SESSION_END", "Sending close_order to NinjaTrader")
                    trade_logger.log(tid, "CMD_SENT", "close_order → NinjaTrader")
                    trade_manager.trade_executor.on_trade_close(tid, bar['close'])
                    _close_commands_sent.add(tid)

        def combined_bar_callback(bar):
            # No trade_manager.handle_new_1m_bar — NinjaTrader handles SL/TP
            tstrategy.on_raw_bar(bar)
            _check_live_session_end(bar)

        def stream_end_callback(close_price: float, final_time: float):
            # Send close commands to NT, don't close locally
            for t in list(trade_manager.open_trades):
                tid = t['trade_id']
                if tid not in _close_commands_sent:
                    print(f"[LiveMode] STREAM END — sending close_order to NT for {tid}")
                    trade_logger.log(tid, "SESSION_END", "Stream end — sending close_order to NinjaTrader")
                    trade_logger.log(tid, "CMD_SENT", "close_order → NinjaTrader")
                    trade_manager.trade_executor.on_trade_close(tid, close_price)
                    _close_commands_sent.add(tid)
    else:
        def combined_bar_callback(bar):
            trade_manager.handle_new_1m_bar(bar)
            tstrategy.on_raw_bar(bar)

        def stream_end_callback(close_price: float, final_time: float):
            trade_manager.close_remaining_trades_at_stream_end(close_price, final_time)

    loader = BarsLoader(
        data_source=data_source,
        socketio=socketio,
        bar_callback=combined_bar_callback,
        stream_end_callback=stream_end_callback
    )
    loader.live_mode = live_mode

    # In live mode, wire direct callbacks on the data source so the strategy
    # processes bars as soon as NinjaTrader sends them — no browser needed.
    if live_mode:
        def _on_history_complete(bars):
            print(f"[LiveMode] Warming up strategy with {len(bars)} historical bars...")
            for bar in bars:
                tstrategy.on_raw_bar(bar)
            print("[LiveMode] Warmup complete, ready for live bars.")
            # Tell any connected browsers to reload chart data
            socketio.emit('history_ready', {'count': len(bars)})

        def _on_live_bar(bar):
            # Route through BarsLoader so bars get aggregated into
            # the current timeframe (5m, 15m, etc.) before chart emission.
            loader._handle_message(bar)

        def _on_before_refresh():
            """Reset strategy and re-add DB lines before fresh bars arrive."""
            print("[LiveMode] Refresh: resetting strategy...")
            tstrategy.reset()
            loader.reset()
            # Re-add persistent lines with their real creation timestamp so the
            # existing guards in liquidity_strategy_v2 skip historical bars that
            # predate when the line was drawn.  creation_date is always UTC-aware
            # (the repo enforces this), so .timestamp() gives correct epoch seconds.
            for l in repos.lines.list_lines(pair):
                tstrategy.add_strategy_line(l.line_id, l.price, creation_timestamp=l.creation_date.timestamp())
            print("[LiveMode] Refresh: strategy reset, ready for fresh bars.")

        data_source.on_history_complete = _on_history_complete
        data_source.on_live_bar = _on_live_bar
        data_source.on_before_refresh = _on_before_refresh

    lines_controller  = LinesController(repos.lines, loader, tstrategy)
    trades_controller = TradesController(loader, trade_manager)

    # Optionally load any preexisting lines from repo into the in-memory strategy
    if bootstrap_existing_lines:
        for l in repos.lines.list_lines(pair):
            # FIX: Force timestamp to 0 for existing DB lines so they are valid for ALL history.
            # This prevents "future" creation dates (e.g. 2025) from blocking trades on 2024 data.
            tstrategy.add_strategy_line(l.line_id, l.price, creation_timestamp=0)

    # ---------------- HTTP endpoints (capturing the injected deps) ----------------

    @app.route('/')
    def index():
        return render_template('chart.html')

    @app.route('/api/pair')
    def get_pair():
        from src.data_sources.ninjatrader_datasource import NinjaTraderDataSource
        result = {'pair': pair}
        if isinstance(data_source, NinjaTraderDataSource) and data_source._cfg.account:
            result['account'] = data_source._cfg.account
        return jsonify(result)

    @app.route('/api/bars')
    def get_bars():
        tf       = request.args.get('tf', '5m')
        start_ts = request.args.get('start_time', type=int)
        bars     = data_source.load_historical_bars(tf, start_ts)
        return jsonify(bars)

    # --- NinjaTrader ingest routes (live mode) ---
    if live_mode:
        @app.route('/api/nt/await_command', methods=['GET'])
        def nt_await_command():
            """Long-poll: NinjaTrader hangs here until Python has a command."""
            cmd = data_source.await_command(timeout=30.0)
            if cmd:
                print(f"[NT LongPoll] Sending command: {cmd}", flush=True)
                return jsonify(cmd)
            return jsonify({'command': None})

        @app.route('/api/nt/refresh_start', methods=['POST'])
        def nt_refresh_start():
            print("[NT Ingest] REFRESH_START received", flush=True)
            data_source.handle_refresh_start()
            return jsonify({'ok': True})

        @app.route('/api/nt/bars', methods=['POST'])
        def nt_ingest_bars():
            bars = request.get_json()
            if not isinstance(bars, list):
                abort(400, 'Expected JSON array of bar objects')
            data_source.ingest_bars(bars)
            print(f"[NT Ingest] Received {len(bars)} bars, total={len(data_source._historical_bars)}", flush=True)
            return jsonify({'ok': True, 'count': len(bars)})

        @app.route('/api/nt/history_end', methods=['POST'])
        def nt_history_end():
            print(f"[NT Ingest] HISTORY_END received", flush=True)
            data_source.mark_history_complete()
            # history_ready is already emitted by _on_history_complete() inside mark_history_complete()
            return jsonify({'ok': True})

        @app.route('/api/nt/tick', methods=['POST'])
        def nt_ingest_tick():
            tick = request.get_json()
            data_source.ingest_tick(tick)
            return jsonify({'ok': True})

        @app.route('/api/nt/bar', methods=['POST'])
        def nt_ingest_live_bar():
            bar = request.get_json()
            from datetime import datetime, timezone
            ts = int(bar.get('time', 0))
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%H:%M:%S')
            print(f"[NT /bar] COMPLETED bar time={dt} ({ts}) O={bar.get('open')} H={bar.get('high')} L={bar.get('low')} C={bar.get('close')}", flush=True)
            data_source.ingest_live_bar(bar)
            return jsonify({'ok': True})

        @app.route('/api/nt/partial', methods=['POST'])
        def nt_ingest_partial():
            bar = request.get_json()
            bar['partial'] = True
            data_source.ingest_live_bar(bar)
            return jsonify({'ok': True})

        @app.route('/api/nt/test_connection', methods=['POST'])
        def nt_test_connection():
            """Simple ping/pong to verify HTTP connectivity."""
            return jsonify({'ok': True, 'ts': datetime.now(tz=timezone.utc).isoformat()})

        @app.route('/api/nt/run_e2e_test', methods=['POST'])
        def nt_run_e2e_test():
            """Start an E2E test scenario. Body: {"scenario": "tp_hit"|"sl_hit"|"session_end"}"""
            data = request.get_json() or {}
            scenario = data.get('scenario', 'tp_hit')
            if scenario not in ('tp_hit', 'sl_hit', 'session_end'):
                abort(400, 'scenario must be tp_hit, sl_hit, or session_end')

            entry_price = 21000.0
            risk = 80.0
            tp_rr = 1.0  # TP at 1R for test simplicity
            sl = entry_price - risk       # 20920.0
            tp = entry_price + risk * tp_rr  # 21080.0
            now_dt = datetime.now(tz=timezone.utc)

            trade_data = repos.trades.insert_trade(
                pair=pair, trade_type="long",
                entry_price=entry_price, stop_loss=sl,
                take_profit=tp, risk=risk,
                entry_time=now_dt,
            )
            trade_id = trade_data.trade_id
            trade_manager.open_trades.append({
                'trade_id': trade_id, 'pair': pair, 'type': 'long',
                'entry': entry_price, 'stop_loss': sl, 'take_profit': tp,
                'risk': risk, 'entry_time': now_dt.timestamp(),
                'status': 'open'
            })

            trade_logger.log(trade_id, "TEST", f"E2E test started: {scenario}")

            data_source.enqueue_command({
                "command": "place_order",
                "trade_id": trade_id,
                "pair": pair,
                "direction": "long",
                "entry_price": entry_price,
                "sl_points": risk,
                "rr_ratio": tp_rr,
                "test": True,
                "scenario": scenario,
            })

            _test_sequences[trade_id] = {
                "stage": "awaiting_entry_fill",
                "scenario": scenario,
                "entry_price": entry_price,
                "sl": sl,
                "tp": tp,
            }

            print(f"[E2E Test] Started scenario={scenario} trade_id={trade_id}", flush=True)
            return jsonify({
                "ok": True, "trade_id": trade_id, "scenario": scenario,
                "entry_price": entry_price, "sl": sl, "tp": tp
            })

        @app.route('/api/nt/test_result/<string:test_trade_id>', methods=['GET'])
        def nt_test_result(test_trade_id):
            """Check the result of an E2E test by trade_id."""
            logs = repos.trades.get_trade_logs(test_trade_id)
            events = [l['event'] for l in logs]

            has_error = any("ERROR" in e for e in events)

            # Check trade is actually closed in DB (more reliable than log events
            # which can be lost to concurrent JSON column writes)
            all_trades = repos.trades.list_trades(pair)
            trade = next((t for t in all_trades if t.trade_id == test_trade_id), None)
            trade_closed = trade is not None and trade.exit_time is not None

            # Key command events that must be present
            has_key_events = all(e in events for e in
                                ["NT:ORDER", "NT_ENTRY_FILL", "NT:MODIFY"])
            passed = trade_closed and has_key_events and not has_error

            formatted = TradeLogger.format_logs(trade) if trade else "(trade not found)"

            return jsonify({
                "passed": passed,
                "trade_closed": trade_closed,
                "has_errors": has_error,
                "events_found": events,
                "trade_id": test_trade_id,
                "formatted_logs": formatted,
            })

        @app.route('/api/nt/trade_log', methods=['POST'])
        def nt_trade_log():
            """Accept log entries from NinjaTrader (NT:ORDER, NT:FILL, NT:MODIFY, etc.)."""
            data = request.get_json()
            tid = data.get('trade_id')
            event = data.get('event')
            msg = data.get('msg', '')
            if not tid or not event:
                abort(400, 'trade_id and event are required')
            trade_logger.log(tid, event, msg)

            if "ERROR" in event.upper():
                notifier.send(f"[NinjaTrader] {event}: {msg} (trade {tid})")

            # E2E test state machine: advance after NT:MODIFY acknowledgement
            if tid in _test_sequences:
                seq = _test_sequences[tid]
                if event == "NT:MODIFY" and seq["stage"] == "awaiting_modify_ack":
                    if seq["scenario"] == "session_end":
                        seq["stage"] = "awaiting_close_fill"
                        data_source.enqueue_command({"command": "close_order", "trade_id": tid, "test": True})
                        trade_logger.log(tid, "CMD_SENT", "close_order -> NinjaTrader (session end test)")
                        print(f"[E2E Test] {tid}: close_order enqueued (session_end)", flush=True)
                    else:
                        seq["stage"] = "awaiting_exit_fill"
                        print(f"[E2E Test] {tid}: awaiting exit fill from NT ({seq['scenario']})", flush=True)

            return jsonify({'ok': True})

        @app.route('/api/nt/positions', methods=['POST'])
        def nt_positions_sync():
            """Reconcile broker positions with DB after reconnect/restart."""
            positions = request.get_json()
            if not isinstance(positions, list):
                abort(400, 'Expected JSON array of position objects')

            broker_ids = {p['trade_id'] for p in positions}
            db_open = [t for t in repos.trades.list_trades(pair) if t.exit_time is None]
            db_open_ids = {t.trade_id for t in db_open}

            reconciled = []

            # DB has open trade but broker does not → closed offline
            for t in db_open:
                if t.trade_id not in broker_ids:
                    print(f"[PositionSync] Trade {t.trade_id} closed offline (not on broker)")
                    trade_logger.log(t.trade_id, "POSITION_SYNC", "Closed offline (not on broker)")
                    repos.trades.close_trade(
                        trade_id=t.trade_id,
                        exit_price=0,
                        exit_time=datetime.now(tz=timezone.utc),
                        result=0.0,
                        result_type="OFFLINE"
                    )
                    # Remove from trade_manager in-memory
                    trade_manager.open_trades = [
                        ot for ot in trade_manager.open_trades if ot['trade_id'] != t.trade_id
                    ]
                    reconciled.append({'trade_id': t.trade_id, 'action': 'closed_offline'})

            # Broker has position but DB does not → orphan
            for p in positions:
                if p['trade_id'] not in db_open_ids:
                    print(f"[PositionSync] WARNING: Orphan position on broker: {p['trade_id']}")
                    reconciled.append({'trade_id': p['trade_id'], 'action': 'orphan_warning'})

            # Matching → already resumed by TradeManager._load_open_trades_from_db
            for p in positions:
                if p['trade_id'] in db_open_ids:
                    reconciled.append({'trade_id': p['trade_id'], 'action': 'resumed'})

            print(f"[PositionSync] Reconciliation complete: {len(reconciled)} items")
            return jsonify({'ok': True, 'reconciled': reconciled})

        @app.route('/api/nt/entry_fill', methods=['POST'])
        def nt_entry_fill():
            """NinjaTrader reports the actual entry fill price and real SL/TP."""
            data = request.get_json()
            trade_id = data.get('trade_id')
            entry_price = float(data.get('entry_price', 0))
            broker_sl = float(data['stop_loss']) if data.get('stop_loss') is not None else None
            broker_tp = float(data['take_profit']) if data.get('take_profit') is not None else None

            if not trade_id:
                abort(400, 'trade_id is required')

            print(f"[NT EntryFill] trade_id={trade_id} entry={entry_price} "
                  f"SL={broker_sl} TP={broker_tp}", flush=True)
            trade_manager.handle_broker_entry_fill(trade_id, entry_price, broker_sl, broker_tp)

            # Also update entry/SL/TP in strategy's open_trades list
            for t in tstrategy.open_trades:
                if t.get('trade_id') == trade_id:
                    t['entry'] = entry_price
                    if broker_sl is not None:
                        t['stop_loss'] = broker_sl
                    if broker_tp is not None:
                        t['take_profit'] = broker_tp
                    break

            # E2E test state machine: after entry fill, move SL to breakeven
            if trade_id in _test_sequences and _test_sequences[trade_id]["stage"] == "awaiting_entry_fill":
                seq = _test_sequences[trade_id]
                seq["stage"] = "awaiting_modify_ack"
                # Update seq with broker's real values
                seq["entry_price"] = entry_price
                if broker_tp is not None:
                    seq["tp"] = broker_tp
                new_sl = entry_price  # breakeven
                for t in trade_manager.open_trades:
                    if t.get('trade_id') == trade_id:
                        t['stop_loss'] = new_sl
                        break
                repos.trades.update_stop_loss(trade_id, new_sl)
                trade_logger.log(trade_id, "SL_UPDATE", f"SL moved to breakeven {new_sl}")
                data_source.enqueue_command({
                    "command": "modify_order", "trade_id": trade_id, "stop_loss": new_sl,
                    "test": True, "scenario": seq["scenario"],
                    "entry_price": seq["entry_price"], "tp": seq["tp"],
                })
                trade_logger.log(trade_id, "CMD_SENT", "modify_order -> NinjaTrader (breakeven)")
                print(f"[E2E Test] {trade_id}: modify_order enqueued (breakeven)", flush=True)

            return jsonify({'ok': True})

        @app.route('/api/nt/fill', methods=['POST'])
        def nt_fill():
            """NinjaTrader reports a broker fill (SL, TP, or close)."""
            data = request.get_json()
            trade_id = data.get('trade_id')
            exit_price = float(data.get('exit_price', 0))
            result_type = data.get('result_type')  # "SL", "TP", or None

            if not trade_id:
                abort(400, 'trade_id is required')

            print(f"[NT Fill] trade_id={trade_id} exit_price={exit_price} type={result_type}", flush=True)
            trade_manager.handle_broker_fill(trade_id, exit_price, result_type)

            # Also remove from strategy's open_trades list
            tstrategy.open_trades = [
                t for t in tstrategy.open_trades if t.get('trade_id') != trade_id
            ]

            # Clean up E2E test state
            _test_sequences.pop(trade_id, None)

            return jsonify({'ok': True})

    @app.route('/api/lines', methods=['GET'])
    def list_lines():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required, e.g. /api/lines?pair=NQ")
        return lines_controller.list_lines(pair)

    @app.route('/api/lines', methods=['POST'])
    def add_line():
        data = request.get_json() or {}
        
        if 'pair' not in data or 'price' not in data:
            abort(400, 'Must provide {"pair":..., "price":...}')
        
        try:
            price = float(data['price'])
        except ValueError:
            abort(400, "Field 'price' must be a number")
            
        # Extract optional creation_time from request
        creation_time = data.get('creation_time')
        if creation_time is not None:
            try:
                creation_time = float(creation_time)
            except ValueError:
                abort(400, "Field 'creation_time' must be a timestamp number")

        # INJECT dependencies into the controller method
        return lines_controller.add_line(
            pair=data['pair'], 
            price=price, 
            creation_timestamp=creation_time
        )

    @app.route('/api/lines/<string:line_id>', methods=['DELETE'])
    def delete_line(line_id):
        return lines_controller.delete_line(line_id)

    @app.route('/api/trades', methods=['GET'])
    def list_trades():
        pair = request.args.get('pair')
        if not pair:
            abort(400, "Query param 'pair' is required")
        return trades_controller.list_trades(pair)

    @app.route('/api/trades', methods=['POST'])
    def open_trade():
        data = request.get_json() or {}
        pair       = data.get('pair')
        trade_type = data.get('type', '').lower()
        stop_loss  = data.get('stop_loss')
        if not isinstance(pair, str) or trade_type not in ('buy', 'sell'):
            abort(400, '"pair" must be a string and "type" must be "buy" or "sell"')
        try:
            stop_loss = float(stop_loss)
        except Exception:
            abort(400, '"stop_loss" must be a number')
        return trades_controller.open_trade(pair, stop_loss, trade_type)

    @app.route('/api/trades/<string:trade_id>/close', methods=['POST'])
    def close_trade(trade_id):
        return trades_controller.close_trade(trade_id)

    @app.route('/api/trades/<string:trade_id>/logs', methods=['GET'])
    def get_trade_logs(trade_id):
        fmt = request.args.get('format', 'json')
        if fmt == 'text':
            all_trades = repos.trades.list_trades(pair)
            trade = next((t for t in all_trades if t.trade_id == trade_id), None)
            if not trade:
                abort(404, 'Trade not found')
            return TradeLogger.format_logs(trade), 200, {'Content-Type': 'text/plain'}
        else:
            logs = repos.trades.get_trade_logs(trade_id)
            return jsonify(logs)

    # Socket.IO events
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

    # --- TEST RUNNER HELPERS ---
    @app.route('/api/debug/logs', methods=['GET'])
    def get_debug_logs():
        """Return the decision logs from the strategy."""
        return jsonify(tstrategy.decision_logs)
    
    @app.route('/__reset_all', methods=['POST'])
    def reset_all():
        """Clears all state, resets DataSource range, AND warms up strategy."""
        try:
            # 1. Clear in-memory strategy state (Deep Reset)
            tstrategy.reset()
            
            # 2. Reset Loader State (so _last_played_ts goes back to 0)
            loader.reset()

            # 3. Clear DB lines
            try:
                all_lines = repos.lines.list_lines(pair)
                for l in all_lines:
                    repos.lines.delete_line(l.line_id)
            except Exception as e:
                return jsonify({"error": str(e)}), 500

            # 4. Clear Trades (Fix for leaking trades between scenarios)
            if hasattr(repos.trades, 'clear'):
                repos.trades.clear()

            # 5. Reset DataSource history
            data = request.get_json() or {}
            start_ts = data.get('start_time')
            end_ts   = data.get('end_time')

            if hasattr(data_source, 'reset'):
                try:
                    data_source.reset(start_time=start_ts, end_time=end_ts)
                except TypeError:
                    data_source.reset()

            # 6. WARM UP STRATEGY (Without lines)
            played = getattr(data_source, '_played_bars', None) or getattr(data_source, '_historical_bars', [])
            if played:
                print(f"[Reset] Warming up strategy with {len(played)} bars (No lines)...")
                for bar in played:
                    tstrategy.on_raw_bar(bar)
                print("[Reset] Warmup complete.")

            return jsonify({"status": "OK"})
        except Exception as e:
            print(f"[Reset] Critical error: {e}")
            analytics.capture_exception(e, {"op": "reset_all"})
            notifier.send(f"[Reset] Critical error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route('/__shutdown', methods=['POST'])
    def shutdown():
        func = request.environ.get('werkzeug.server.shutdown')
        if func: func()
        return "OK"

    @app.errorhandler(500)
    def handle_500(error):
        notifier.send(f"[Flask] Unhandled server error: {error}")
        return jsonify({"error": "Internal server error"}), 500

    return AppWiring(
        app=app,
        socketio=socketio,
        loader=loader,
        strategy=tstrategy,
        trade_manager=trade_manager,
        lines_controller=lines_controller,
        trades_controller=trades_controller,
        data_source=data_source,
        pair=pair,
        live_mode=live_mode,
    )